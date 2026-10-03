"""Backend Central XML multi-cliente — FastAPI local porta 8004."""
import json
import re
import shutil
import uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from nfe_ce_lib import so_digitos, valida_chave, montar_dist_dfe, decodificar_doczip, AN_URLS

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"
CLIENTES_FILE = BASE_DIR / "clientes.json"
CERTS_DIR = BASE_DIR / "certs"
DOCS_DIR = BASE_DIR / "docs_xml"
CERTS_DIR.mkdir(exist_ok=True)
DOCS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Central XML multi-cliente — local")


def _load():
    if not CLIENTES_FILE.exists():
        return []
    try:
        return json.loads(CLIENTES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(lst):
    CLIENTES_FILE.write_text(json.dumps(lst, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/", include_in_schema=False)
def site():
    return FileResponse(str(HTML_FILE), media_type="text/html")


@app.get("/api/status")
def status():
    return {"status": "central local no ar", "porta": 8004, "modo": "local"}


@app.get("/api/clientes")
def listar():
    return _load()


@app.post("/api/clientes")
def criar(payload: dict):
    nome = (payload.get("nome") or "").strip()
    cnpj = so_digitos(payload.get("cnpj", ""))
    uf = so_digitos(payload.get("uf", "23"))[:2] or "23"
    if not nome:
        return JSONResponse({"ok": False, "erro": "Nome obrigatório"}, status_code=400)
    if len(cnpj) != 14:
        return JSONResponse({"ok": False, "erro": "CNPJ deve ter 14 dígitos"}, status_code=400)
    lst = _load()
    cid = uuid.uuid4().hex[:8]
    reg = {"id": cid, "nome": nome, "cnpj": cnpj, "uf": uf, "pfx": f"{cid}.pfx"}
    lst.append(reg)
    _save(lst)
    return reg


@app.post("/api/clientes/{cid}/pfx")
async def enviar_pfx(cid: str, pfx: UploadFile = File(...)):
    lst = _load()
    reg = next((c for c in lst if c["id"] == cid), None)
    if not reg:
        return JSONResponse({"ok": False, "erro": "Cliente não encontrado"}, status_code=404)
    data = await pfx.read()
    if len(data) > 5 * 1024 * 1024:
        return JSONResponse({"ok": False, "erro": "Arquivo .pfx muito grande (limite 5 MB)"}, status_code=413)
    if not data:
        return JSONResponse({"ok": False, "erro": "Arquivo .pfx vazio"}, status_code=400)
    (CERTS_DIR / f"{cid}.pfx").write_bytes(data)
    return {"ok": True}


@app.delete("/api/clientes/{cid}")
def excluir(cid: str):
    lst = [c for c in _load() if c["id"] != cid]
    _save(lst)
    try:
        (CERTS_DIR / f"{cid}.pfx").unlink(missing_ok=True)
    except Exception:
        pass
    shutil.rmtree(DOCS_DIR / cid, ignore_errors=True)
    return {"ok": True}


SOAP_ACTION = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse"
AN_EVENTO = {
    "prod": "https://www.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx",
    "homolog": "https://hom.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx",
}
EVENTO_ACTION = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4/nfeRecepcaoEvento"


def _montar_evento_ciencia(cnpj: str, chave: str, tp_amb: int = 1) -> bytes:
    from datetime import datetime, timezone
    cnpj_d = so_digitos(cnpj)
    if len(cnpj_d) != 14:
        raise ValueError("CNPJ deve ter 14 digitos")
    err = valida_chave(chave)
    if err:
        raise ValueError(err)
    dh = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    xml = (
        f'<envEvento versao="1.00" xmlns="http://www.portalfiscal.inf.br/nfe">'
        f"<idLote>1</idLote><evento versao=\"1.00\">"
        f"<infEvento Id=\"ID210200{chave}1\">"
        f"<cOrgao>91</cOrgao><tpAmb>{tp_amb}</tpAmb><CNPJ>{cnpj_d}</CNPJ>"
        f"<chNFe>{chave}</chNFe><dhEvento>{dh}</dhEvento>"
        f"<tpEvento>210200</tpEvento><nSeqEvento>1</nSeqEvento><verEvento>1.00</verEvento>"
        f"<detEvento versao=\"1.00\"><descEvento>Ciencia da Operacao</descEvento></detEvento>"
        f"</infEvento></evento></envEvento>"
    )
    return xml.encode("utf-8")


def _assinar_evento(env_xml: bytes, pfx_bytes: bytes, senha: str) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
    from lxml import etree
    from signxml import XMLSigner
    priv, cert, _ = pkcs12.load_key_and_certificates(pfx_bytes, (senha or "").encode())
    key_pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    class _SHA1Signer(XMLSigner):
        def check_deprecated_methods(self):
            return None

    signer = _SHA1Signer(signature_algorithm="rsa-sha1", digest_algorithm="sha1",
                        c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
    signed = signer.sign(etree.fromstring(env_xml), key=key_pem, cert=cert_pem)
    return etree.tostring(signed, encoding="utf-8")


@app.post("/api/evento/ciencia")
def evento_ciencia(cliente_id: str = Form(...), chave: str = Form(...),
                   senha: str = Form(""), ambiente: str = Form("prod")):
    import os
    import requests
    from lxml import etree
    found, erro = _cliente_ou_erro(cliente_id)
    if erro:
        return erro
    reg, pfx_path = found
    c = so_digitos(chave)
    pfx_bytes = pfx_path.read_bytes()
    try:
        _validar_pfx(pfx_bytes, senha)
        dist = _montar_evento_ciencia(reg["cnpj"], c, 1 if ambiente == "prod" else 2)
        assinado = _assinar_evento(dist, pfx_bytes, senha)
        env = (_soap_envelope_evento(assinado))
        url = AN_EVENTO["prod"] if ambiente == "prod" else AN_EVENTO["homolog"]
        cert_path, key_path = _pfx_para_pem_temp(pfx_bytes, senha)
        try:
            r = requests.post(url, data=env, headers={"Content-Type": "text/xml; charset=utf-8",
                              "SOAPAction": EVENTO_ACTION}, timeout=30, cert=(cert_path, key_path))
        finally:
            for p in (cert_path, key_path):
                try:
                    os.unlink(p)
                except Exception:
                    pass
        root = etree.fromstring(r.content)
        ns = {"n": "http://www.portalfiscal.inf.br/nfe"}
        cstat = (root.find(".//n:cStat", ns).text or "").strip() if root.find(".//n:cStat", ns) is not None else ""
        xmot = (root.find(".//n:xMotivo", ns).text or "").strip() if root.find(".//n:xMotivo", ns) is not None else ""
    except ValueError as e:
        return JSONResponse({"ok": False, "erro": str(e)[:400]}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "erro": f"Falha SEFAZ/rede: {str(e)[:400]}"}, status_code=502)
    if cstat in ("135", "136"):
        return {"ok": True, "cstat": cstat, "xmotivo": xmot}
    return JSONResponse({"ok": False, "erro": f"SEFAZ {cstat}: {xmot}"[:400]}, status_code=502)


def _soap_envelope_evento(assinado: bytes) -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        b"<soap:Body><nfeRecepcaoEvento xmlns=\"http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4\">"
        b"<nfeDadosMsg>" + assinado + b"</nfeDadosMsg>"
        b"</nfeRecepcaoEvento></soap:Body></soap:Envelope>"
    )


def _soap_envelope(dist_xml: bytes) -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        b'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        b"<soap:Body><nfeDistDFeInteresse xmlns=\"http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe\">"
        b"<nfeDadosMsg>" + dist_xml + b"</nfeDadosMsg>"
        b"</nfeDistDFeInteresse></soap:Body></soap:Envelope>"
    )


def _validar_pfx(pfx_bytes: bytes, senha: str) -> None:
    from cryptography.hazmat.primitives.serialization import pkcs12
    from datetime import datetime, timezone
    try:
        priv, cert, _ = pkcs12.load_key_and_certificates(pfx_bytes, (senha or "").encode())
    except ValueError as e:
        msg = str(e).lower()
        if "invalid password" in msg or "mac verify" in msg or "bad decrypt" in msg or "could not deserialize" in msg:
            raise ValueError("Senha do PFX incorreta ou arquivo .pfx invalido")
        raise ValueError(f"Arquivo .pfx invalido: {e}")
    if priv is None or cert is None:
        raise ValueError("Arquivo .pfx sem chave/certificado valido")
    try:
        exp = getattr(cert, "not_valid_after_utc", None) or getattr(cert, "not_valid_after", None)
        if exp is not None:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                raise ValueError("Certificado A1 expirado")
    except ValueError:
        raise
    except Exception:
        pass


def _pfx_para_pem_temp(pfx_bytes: bytes, senha: str):
    import tempfile
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
    priv, cert, chain = pkcs12.load_key_and_certificates(pfx_bytes, (senha or "").encode())
    if priv is None or cert is None:
        raise ValueError("Arquivo .pfx sem chave/certificado valido")
    kf = tempfile.NamedTemporaryFile(delete=False, suffix=".key.pem")
    cf = tempfile.NamedTemporaryFile(delete=False, suffix=".cert.pem")
    kf.write(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    kf.close()
    cf.write(cert.public_bytes(serialization.Encoding.PEM))
    if chain:
        for c in chain:
            cf.write(c.public_bytes(serialization.Encoding.PEM))
    cf.close()
    return cf.name, kf.name


def _consultar(chave: str, cnpj: str, ambiente: str, pfx_bytes: bytes, senha: str, uf_autor: str = "23", debug_dir=None):
    import os
    import requests
    from lxml import etree
    tp = 1 if ambiente == "prod" else 2
    dist = montar_dist_dfe(cnpj, chave, tp, uf_autor)
    _validar_pfx(pfx_bytes, senha)
    env = _soap_envelope(dist)
    url = AN_URLS["prod"] if ambiente == "prod" else AN_URLS["homolog"]
    cert_path, key_path = _pfx_para_pem_temp(pfx_bytes, senha)
    try:
        r = requests.post(url, data=env, headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": SOAP_ACTION,
        }, timeout=30, cert=(cert_path, key_path))
    finally:
        try:
            os.unlink(cert_path)
        except Exception:
            pass
        try:
            os.unlink(key_path)
        except Exception:
            pass
    if r.status_code != 200:
        raise RuntimeError(f"SEFAZ HTTP {r.status_code}: {(r.text or '')[:300]}")
    try:
        root = etree.fromstring(r.content)
    except Exception:
        raise RuntimeError(f"Resposta SEFAZ nao-XML (HTTP 200, {len(r.content)} bytes)")
    ns = {"n": "http://www.portalfiscal.inf.br/nfe"}
    def txt(tag):
        el = root.find(f".//n:{tag}", ns)
        return (el.text or "").strip() if el is not None else ""
    fault = root.find(".//{http://www.w3.org/2003/05/soap-envelope}Fault")
    if fault is not None:
        razao = "".join(fault.itertext()).strip()[:300]
        raise RuntimeError(f"SEFAZ Fault: {razao}")
    cstat, xmot = txt("cStat"), txt("xMotivo")
    docs = root.findall(".//n:docZip", ns)
    if not cstat and not docs:
        trecho = (r.text or "")[:300].replace("\n", " ")
        raise RuntimeError(f"SEFAZ sem cStat (resposta inesperada): {trecho}")
    if cstat != "138" and debug_dir is not None:
        try:
            Path(debug_dir).mkdir(parents=True, exist_ok=True)
            (Path(debug_dir) / f"debug_{chave}_req.xml").write_bytes(env)
            (Path(debug_dir) / f"debug_{chave}_res.xml").write_bytes(r.content)
        except Exception:
            pass
    return cstat, xmot, docs


def _cid_ok(cid: str) -> bool:
    import re as _re
    return bool(_re.fullmatch(r"[0-9a-f]{8}", cid or ""))


def _cliente_existe(cid: str):
    if not _cid_ok(cid):
        return None, JSONResponse({"ok": False, "erro": "Cliente inválido"}, status_code=400)
    reg = next((c for c in _load() if c["id"] == cid), None)
    if not reg:
        return None, JSONResponse({"ok": False, "erro": "Cliente não encontrado"}, status_code=404)
    return reg, None


def _cliente_ou_erro(cid: str):
    reg, erro = _cliente_existe(cid)
    if erro:
        return None, erro
    pfx_path = CERTS_DIR / f"{cid}.pfx"
    if not pfx_path.exists():
        return None, JSONResponse({"ok": False, "erro": "Cliente sem certificado (.pfx não enviado)"}, status_code=400)
    return (reg, pfx_path), None


def _baixar_para_cliente(reg, pfx_bytes: bytes, chave: str, senha: str, ambiente: str):
    import time as _time
    import json as _json
    c = so_digitos(chave)
    err = valida_chave(c)
    if err:
        return {"chave": c, "ok": False, "erro": err}
    pasta = DOCS_DIR / reg["id"]
    pasta.mkdir(parents=True, exist_ok=True)
    dest = pasta / f"nfe_{c}.xml"
    meta = pasta / f"nfe_{c}.meta.json"
    if dest.exists() and dest.stat().st_size > 0:
        try:
            m = _json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
            if m.get("cnpj") == reg["cnpj"] and m.get("ambiente") == ambiente:
                return {"chave": c, "ok": True, "duplicado": True, "arquivo": dest.name}
        except Exception:
            pass
    try:
        cstat, xmot, docs = _consultar(c, reg["cnpj"], ambiente, pfx_bytes, senha, reg.get("uf", "23"), pasta)
    except ValueError as e:
        return {"chave": c, "ok": False, "erro": str(e)[:400]}
    except Exception as e:
        return {"chave": c, "ok": False, "erro": f"Falha SEFAZ/rede: {str(e)[:400]}"}
    if cstat == "138" and docs:
        try:
            xml_bytes = decodificar_doczip(docs[0].text or "")
            dest.write_bytes(xml_bytes)
            meta.write_text(_json.dumps({"cnpj": reg["cnpj"], "ambiente": ambiente}), encoding="utf-8")
            return {"chave": c, "ok": True, "arquivo": dest.name}
        except Exception as e:
            return {"chave": c, "ok": False, "erro": f"Falha ao decodificar XML: {e}"}
    if cstat == "137":
        return {"chave": c, "ok": False, "erro": "SEFAZ sem documento (137): CNPJ sem autorizacao/manifestacao. Manifeste no portal e tente de novo."}
    if cstat == "656":
        return {"chave": c, "ok": False, "erro": "Consumo indevido (656): aguarde 1h."}
    if cstat in ("108", "109"):
        return {"chave": c, "ok": False, "erro": f"SEFAZ parada ({cstat}): {xmot or 'tente mais tarde.'}"[:400]}
    if cstat == "213":
        return {"chave": c, "ok": False, "erro": "CNPJ nao autorizado (213)."}
    return {"chave": c, "ok": False, "erro": f"SEFAZ {cstat}: {xmot}"[:500]}


@app.post("/api/sefaz/xml")
def sefaz_xml(cliente_id: str = Form(...), chave: str = Form(...),
              senha: str = Form(""), ambiente: str = Form("prod")):
    found, erro = _cliente_ou_erro(cliente_id)
    if erro:
        return erro
    reg, pfx_path = found
    res = _baixar_para_cliente(reg, pfx_path.read_bytes(), chave, senha, ambiente)
    if res.get("ok"):
        f = DOCS_DIR / reg["id"] / f"nfe_{res['chave']}.xml"
        return FileResponse(str(f), media_type="application/xml", filename=f.name)
    status = 400 if "44 digitos" in res.get("erro", "") or "DV" in res.get("erro", "") or "Senha" in res.get("erro", "") else 502
    return JSONResponse(res, status_code=status)


@app.post("/api/sefaz/lote")
def sefaz_lote(payload: dict):
    import time as _time
    chaves = payload.get("chaves", [])
    if len(chaves) > 50:
        return JSONResponse({"ok": False, "erro": "Lote limitado a 50 chaves por vez"}, status_code=400)
    found, erro = _cliente_ou_erro(payload.get("cliente_id", ""))
    if erro:
        return erro
    reg, pfx_path = found
    pfx_bytes = pfx_path.read_bytes()
    senha = payload.get("senha", "")
    ambiente = payload.get("ambiente", "prod")
    vistos, fila = set(), []
    for bruta in chaves:
        c = so_digitos(bruta)
        if c and c not in vistos:
            vistos.add(c)
            fila.append(c)
    resultados = []
    for c in fila:
        resultados.append(_baixar_para_cliente(reg, pfx_bytes, c, senha, ambiente))
        _time.sleep(2)
    return {"total": len(resultados), "ok": sum(1 for r in resultados if r.get("ok")), "resultados": resultados}


def _texto_celulas_planilha(nome: str, data: bytes) -> str:
    ext = Path(nome or "").suffix.lower()
    if ext in (".csv", ".txt"):
        import csv as _csv
        txt = data.decode("utf-8-sig", errors="replace")
        if ext == ".txt":
            return txt
        try:
            dial = _csv.Sniffer().sniff(txt[:4096], delimiters=";,|\t")
        except Exception:
            dial = None
        partes = []
        for row in _csv.reader(txt.splitlines(), dialect=dial) if dial else _csv.reader(txt.splitlines()):
            partes.extend(str(v or "") for v in row)
        return "\n".join(partes)
    if ext in (".xlsx", ".xlsm"):
        import openpyxl as _oxl
        import tempfile as _tf
        with _tf.NamedTemporaryFile(delete=False, suffix=ext) as tf:
            tf.write(data)
            tmp = tf.name
        try:
            wb = _oxl.load_workbook(tmp, read_only=True, data_only=True)
            partes = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    for v in row:
                        if v is None:
                            continue
                        if isinstance(v, float) and v.is_integer():
                            partes.append(str(int(v)))
                        else:
                            partes.append(str(v))
            return "\n".join(partes)
        finally:
            try:
                import os as _os
                _os.unlink(tmp)
            except Exception:
                pass
    return ""


@app.post("/api/planilha/chaves")
async def planilha_chaves(arquivo: UploadFile = File(...)):
    import re as _re
    data = await arquivo.read()
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse({"ok": False, "erro": "Planilha muito grande (limite 10 MB)"}, status_code=413)
    ext = Path(arquivo.filename or "").suffix.lower()
    if ext == ".xls":
        return JSONResponse({"ok": False, "erro": "Formato .xls antigo não suportado: salve como .xlsx ou .csv e envie de novo"}, status_code=400)
    if ext not in (".xlsx", ".xlsm", ".csv", ".txt"):
        return JSONResponse({"ok": False, "erro": "Envie .xlsx, .csv ou .txt"}, status_code=400)
    try:
        texto = _texto_celulas_planilha(arquivo.filename, data)
    except Exception as e:
        return JSONResponse({"ok": False, "erro": f"Falha ao ler planilha: {e}"[:300]}, status_code=400)
    achadas = _re.findall(r"\d{44}", texto)
    validas, vistos, invalidas = [], set(), 0
    for c in achadas:
        if c in vistos:
            continue
        vistos.add(c)
        if valida_chave(c):
            invalidas += 1
        else:
            validas.append(c)
    if not validas:
        return JSONResponse({"ok": False, "erro": "Nenhuma chave de 44 dígitos válida na planilha", "invalidas": invalidas}, status_code=400)
    return {"ok": True, "validas": validas, "invalidas": invalidas}


def _parse_nfe(data: bytes):
    from lxml import etree
    ns = {"n": "http://www.portalfiscal.inf.br/nfe"}
    try:
        root = etree.fromstring(data)
    except Exception:
        return None
    inf = root.find(".//n:infNFe", ns)
    if inf is None:
        return None
    def txt(path):
        el = root.find(path, ns)
        return (el.text or "").strip() if el is not None else ""
    chave = (inf.get("Id") or "")[3:]
    prot = txt(".//n:infProt/n:cStat")
    return {"chave": chave, "emit": txt(".//n:emit/n:xNome") or txt(".//n:emit/n:CNPJ"),
            "emit_cnpj": txt(".//n:emit/n:CNPJ"), "dest": txt(".//n:dest/n:xNome") or txt(".//n:dest/n:CNPJ"),
            "dest_cnpj": txt(".//n:dest/n:CNPJ"), "vnf": txt(".//n:ICMSTot/n:vNF"),
            "dhemi": txt(".//n:ide/n:dhEmi"), "nnf": txt(".//n:ide/n:nNF"),
            "status": "autorizada" if prot == "100" else ("sem protocolo" if not prot else f"cStat {prot}")}


def _ignorados_path(pasta: Path) -> Path:
    return pasta / ".ignorado.json"


def _ler_ignorados(pasta: Path):
    import json as _json
    try:
        return _json.loads(_ignorados_path(pasta).read_text(encoding="utf-8"))
    except Exception:
        return []


def _anotar_ignorado(pasta: Path, nome: str):
    import json as _json
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        lst = _ler_ignorados(pasta)
        if nome not in lst:
            lst.append(nome)
        _ignorados_path(pasta).write_text(_json.dumps(lst, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _guardar_doc(pasta: Path, nome: str, data: bytes):
    try:
        info = _parse_nfe(data)
    except Exception:
        info = None
    if not info or not info["chave"]:
        _anotar_ignorado(pasta, nome)
        return {"ok": False, "ignorado": nome}
    dest = pasta / f"nfe_{info['chave']}.xml"
    if dest.exists():
        return {"ok": False, "duplicado": info["chave"]}
    dest.write_bytes(data)
    return {"ok": True, "chave": info["chave"]}


@app.post("/api/docs/upload")
async def docs_upload(cliente_id: str, xmls: list[UploadFile] = File(...)):
    reg, erro = _cliente_existe(cliente_id)
    if erro:
        return erro
    pasta = DOCS_DIR / reg["id"]
    pasta.mkdir(parents=True, exist_ok=True)
    ok, ignorados, dup = 0, [], 0
    total = 0
    for up in xmls:
        data = await up.read()
        total += len(data)
        if total > 50 * 1024 * 1024:
            break
        if not (up.filename or "").lower().endswith(".xml"):
            ignorados.append(up.filename)
            continue
        r = _guardar_doc(pasta, up.filename, data)
        if r.get("ok"):
            ok += 1
        elif "duplicado" in r:
            dup += 1
        else:
            ignorados.append(r.get("ignorado"))
    return {"ok": ok, "ignorados": ignorados, "duplicados": dup}


@app.post("/api/docs/zip")
async def docs_zip(cliente_id: str = Form(...), pacote: UploadFile = File(...)):
    import zipfile as _zf
    reg, erro = _cliente_existe(cliente_id)
    if erro:
        return erro
    data = await pacote.read()
    if len(data) > 50 * 1024 * 1024:
        return JSONResponse({"ok": False, "erro": "Arquivo muito grande (limite 50 MB)"}, status_code=413)
    import io as _io
    pasta = DOCS_DIR / reg["id"]
    pasta.mkdir(parents=True, exist_ok=True)
    ok, ignorados, dup = 0, [], 0
    try:
        zf = _zf.ZipFile(_io.BytesIO(data))
    except Exception:
        return JSONResponse({"ok": False, "erro": "Arquivo zip inválido"}, status_code=400)
    for info in zf.infolist():
        if info.is_dir() or not info.filename.lower().endswith(".xml"):
            continue
        if info.file_size > 10 * 1024 * 1024:
            continue
        r = _guardar_doc(pasta, Path(info.filename).name, zf.read(info.filename))
        if r.get("ok"):
            ok += 1
        elif "duplicado" in r:
            dup += 1
        else:
            ignorados.append(r.get("ignorado"))
    return {"ok": ok, "ignorados": ignorados, "duplicados": dup}


@app.get("/api/docs/{cid}")
def docs_listar(cid: str):
    reg, erro = _cliente_existe(cid)
    if erro:
        return erro
    pasta = DOCS_DIR / reg["id"]
    itens = []
    if pasta.exists():
        for f in sorted(pasta.glob("nfe_*.xml")):
            try:
                info = _parse_nfe(f.read_bytes())
                if info:
                    itens.append(info)
            except Exception:
                continue
        for nome in _ler_ignorados(pasta):
            itens.append({"chave": nome, "emit": "—", "vnf": "—", "status": "ignorado"})
    return itens


@app.get("/api/docs/arquivo/{cid}/{chave}")
def docs_arquivo(cid: str, chave: str):
    reg, erro = _cliente_existe(cid)
    if erro:
        return erro
    f = DOCS_DIR / reg["id"] / f"nfe_{so_digitos(chave)}.xml"
    if not f.exists():
        return JSONResponse({"ok": False, "erro": "XML não encontrado"}, status_code=404)
    return FileResponse(str(f), media_type="application/xml", filename=f.name)


@app.post("/api/docs/pacote")
def docs_pacote(payload: dict):
    import io as _io
    import zipfile as _zf
    reg, erro = _cliente_existe(payload.get("cliente_id", ""))
    if erro:
        return erro
    pasta = DOCS_DIR / reg["id"]
    from fastapi.responses import StreamingResponse
    buf = _io.BytesIO()
    with _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as z:
        if pasta.exists():
            for f in sorted(pasta.glob("nfe_*.xml")):
                z.write(str(f), arcname=f.name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": "attachment; filename=xml_docs.zip"})


def _parse_itens(data: bytes):
    from lxml import etree
    ns = {"n": "http://www.portalfiscal.inf.br/nfe"}
    try:
        root = etree.fromstring(data)
    except Exception:
        return []
    itens = []
    for det in root.findall(".//n:det", ns):
        def txt(p):
            el = det.find(p, ns)
            return (el.text or "").strip() if el is not None else ""
        itens.append({"n": det.get("nItem", ""), "xprod": txt("./n:prod/n:xProd"),
                      "ncm": txt("./n:prod/n:NCM"), "cfop": txt("./n:prod/n:CFOP"),
                      "qcom": txt("./n:prod/n:qCom"), "vun": txt("./n:prod/n:vUnCom"),
                      "vprod": txt("./n:prod/n:vProd")})
    return itens


@app.get("/api/danfe/{cid}/{chave}", include_in_schema=False)
def danfe(cid: str, chave: str):
    from fastapi.responses import HTMLResponse
    import html as _html
    reg, erro = _cliente_existe(cid)
    if erro:
        return erro
    f = DOCS_DIR / reg["id"] / f"nfe_{so_digitos(chave)}.xml"
    if not f.exists():
        return JSONResponse({"ok": False, "erro": "XML não encontrado"}, status_code=404)
    data = f.read_bytes()
    info = _parse_nfe(data) or {}
    itens = _parse_itens(data)
    e = lambda v: _html.escape(v or "")
    linhas = "".join(f"<tr><td>{e(i['n'])}</td><td>{e(i['xprod'])}</td><td>{e(i['ncm'])}</td>"
                     f"<td>{e(i['cfop'])}</td><td>{e(i['qcom'])}</td><td>{e(i['vun'])}</td><td>{e(i['vprod'])}</td></tr>" for i in itens)
    return HTMLResponse(f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<title>DANFE {e(info.get('chave'))}</title>
<style>body{{font-family:Arial;font-size:12px;max-width:800px;margin:auto}}table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #000;padding:4px}}@media print{{button{{display:none}}}}</style>
</head><body><button onclick="window.print()">Imprimir / salvar PDF</button>
<h2>DANFE — Documento Auxiliar (sem valor fiscal)</h2>
<p><b>Chave:</b> {e(info.get('chave'))}</p>
<p><b>Emitente:</b> {e(info.get('emit'))} — {e(info.get('emit_cnpj'))}</p>
<p><b>Destinatário:</b> {e(info.get('dest'))} — {e(info.get('dest_cnpj'))}</p>
<table><tr><th>#</th><th>Produto</th><th>NCM</th><th>CFOP</th><th>Qtd</th><th>V.Unit</th><th>V.Total</th></tr>{linhas}</table>
<p><b>Valor total da nota: R$ {e(info.get('vnf'))}</b> — Emissão {e(info.get('dhemi'))} — Status {e(info.get('status'))}</p>
</body></html>""")


@app.post("/api/docs/excel")
def docs_excel(payload: dict):
    import io as _io
    reg, erro = _cliente_existe(payload.get("cliente_id", ""))
    if erro:
        return erro
    import openpyxl as _oxl
    wb = _oxl.Workbook()
    ws = wb.active
    ws.title = "Documentos"
    ws.append(["Chave", "Emitente", "Emit CNPJ", "Destinatario", "Valor", "Emissao", "Numero", "Status"])
    pasta = DOCS_DIR / reg["id"]
    if pasta.exists():
        for f in sorted(pasta.glob("nfe_*.xml")):
            try:
                info = _parse_nfe(f.read_bytes())
                if info:
                    ws.append([info.get("chave"), info.get("emit"), info.get("emit_cnpj"),
                               info.get("dest"), info.get("vnf"), info.get("dhemi"),
                               info.get("nnf"), info.get("status")])
            except Exception:
                continue
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=documentos.xlsx"})
