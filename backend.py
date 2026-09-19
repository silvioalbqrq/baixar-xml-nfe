# backend.py — SITE 100% LOCAL — Baixar XML de NF-e / CT-e via MeuDanfe
# Uso: duplo-clique em iniciar_xml.bat (ou: python backend.py)
# Abre em: http://127.0.0.1:8002/  (frontend servido pelo proprio backend, mesma origem)
# Fluxo automatizado (igual ao manual):
#   1. Acessar https://meudanfe.com.br/
#   2. Colar chave de 44 digitos em #searchTxt e clicar #searchBtn (BUSCAR)
#   3. Aguardar Cloudflare/Turnstile liberar (resolver "Sou humano" manual 1x)
#   4. Clicar #downloadXmlBtn (Baixar XML)
#   5. Clicar #newSearchBtn (Nova Consulta) e repetir p/ proxima chave
import io
import os
import re
import time
import traceback
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

HOST_LOCAL = "127.0.0.1"
PORTA_LOCAL = 8002

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"
XML_DIR = BASE_DIR / "xml_baixados"
XML_DIR.mkdir(exist_ok=True)
PERFIL_DIR = BASE_DIR / "chrome-perfil-xml"
PERFIL_DIR.mkdir(exist_ok=True)

MEUDANFE_URL = "https://meudanfe.com.br/"

app = FastAPI(title="Baixar XML NFe — local", docs_url="/docs", redoc_url=None)
# Sem CORS aberto: frontend e API estao na mesma origem (http://127.0.0.1:8002).


# ---------- validacao ----------
def so_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def dv_mod11_ok(chave: str) -> bool:
    if not re.fullmatch(r"\d{44}", chave or ""):
        return False
    corpo, dv = chave[:43], int(chave[43])
    soma, peso = 0, 2
    for d in reversed(corpo):
        soma += int(d) * peso
        peso = peso + 1 if peso < 9 else 2
    resto = soma % 11
    calc = 0 if resto in (0, 1) else 11 - resto
    return calc == dv


def valida_chave(chave: str) -> Optional[str]:
    if not re.fullmatch(r"\d{44}", chave or ""):
        return "chave deve ter 44 digitos numericos"
    modelo = chave[20:22]
    if modelo not in ("55", "57"):
        return f"modelo {modelo} invalido (esperado 55 p/ NF-e ou 57 p/ CT-e, posicoes 21-22)"
    if not dv_mod11_ok(chave):
        return "digito verificador (DV) invalido — confira a chave digitada"
    return None


def arquivo_cache(chave: str) -> Path:
    return XML_DIR / f"nfe_{chave}.xml"


# ---------- selenium (import tardio p/ nao quebrar /api/status sem chrome) ----------
SELECTORS = {
    "search": [("css", "#searchTxt")],
    "go": [("css", "#searchBtn")],
    "dl": [("css", "#downloadXmlBtn")],
    "new": [("css", "#newSearchBtn")],
    "chave_ok": [("css", "#chaveDsd")],
    "alert": [("css", "#alertTxt")],
}


def make_driver(destino: Path, headless: bool = False):
    destino.mkdir(parents=True, exist_ok=True)
    prefs = {
        "download.default_directory": str(destino.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    # 1) undetected-chromedriver: passa bem melhor pelo Turnstile/Cloudflare.
    try:
        import undetected_chromedriver as uc
        opts = uc.ChromeOptions()
        opts.add_argument("--user-data-dir=" + str(PERFIL_DIR.resolve()))
        opts.add_argument("--start-maximized")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_experimental_option("prefs", prefs)
        driver = uc.Chrome(options=opts, headless=headless, use_subprocess=False)
        print("[INFO] Chrome via undetected-chromedriver.")
        return driver
    except Exception as e:
        print(f"[AVISO] undetected indisponivel ({str(e)[:200]}); usando selenium padrao.")
    # 2) fallback: selenium puro + webdriver-manager.
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    # Evita erro "user-data-dir ja em uso":
    # tenta o perfil persistente; se o Chrome ja estiver aberto, cai p/ perfil temp.
    perfil = PERFIL_DIR.resolve()
    opts.add_argument("--user-data-dir=" + str(perfil))
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--remote-debugging-port=0")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_experimental_option("prefs", prefs)
    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts
        )
    except Exception as e:
        msg = str(e)
        # Perfil travado -> tenta de novo com perfil temporario (sem persistir cookie Cloudflare)
        if "user-data-dir" in msg.lower() or "devtoolsactiveport" in msg.lower() or "session not created" in msg.lower():
            import tempfile
            tmp = Path(tempfile.mkdtemp(prefix="chrome-xml-"))
            opts2 = Options()
            opts2.add_argument("--user-data-dir=" + str(tmp))
            if headless:
                opts2.add_argument("--headless=new")
            opts2.add_argument("--start-maximized")
            opts2.add_argument("--no-sandbox")
            opts2.add_argument("--disable-dev-shm-usage")
            opts2.add_argument("--disable-gpu")
            opts2.add_argument("--remote-debugging-port=0")
            opts2.add_experimental_option("prefs", prefs)
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()), options=opts2
            )
        else:
            raise
    try:
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
    except Exception:
        pass
    return driver


def erro_detalhado(prefixo: str, e: Exception) -> dict:
    tb = traceback.format_exc(limit=5)
    print(f"[ERRO] {prefixo}: {e}\n{tb}")
    return {"ok": False, "erro": f"{prefixo}: {e}"[:800], "tipo": type(e).__name__,
            "dica": "Se for 'session not created'/'DevToolsActivePort': feche o Chrome aberto e repita. "
                    "Se for 'chrome not found': instale o Google Chrome. "
                    "Veja a janela preta do backend p/ o log completo."}


def find(driver, name: str, timeout: int = 20):
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By

    last = None
    for kind, sel in SELECTORS[name]:
        by = By.CSS_SELECTOR if kind == "css" else By.XPATH
        try:
            return WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, sel))
            )
        except Exception as e:
            last = e
    raise last


def fechar_banners(driver):
    from selenium.webdriver.common.by import By

    xpaths = [
        "//button[contains(translate(.,'ACEITARCONCORDOKFECHARENTENDI','aceitarconcordokfecharentendi'),'aceitar')]",
        "//button[contains(translate(.,'ACEITARCONCORDOKFECHARENTENDI','aceitarconcordokfecharentendi'),'concordo')]",
        "//button[contains(translate(.,'ACEITARCONCORDOKFECHARENTENDI','aceitarconcordokfecharentendi'),'entendi')]",
    ]
    for xp in xpaths:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    time.sleep(0.4)
        except Exception:
            pass
    dispensar_novidade(driver)


def dispensar_novidade(driver) -> bool:
    """Fecha o modal 'TEM NOVIDADE!' do MeuDanfe (botao 'depois'). Sem isso ele
    cobre a pagina e bloqueia o Buscar. Retorna True se dispensou algo."""
    from selenium.webdriver.common.by import By

    xps = [
        "//button[contains(translate(normalize-space(.),'DEPOIS','depois'),'depois')]",
        "//div[contains(@class,'swal')]//button",
        "//div[contains(@class,'modal')]//button[contains(translate(.,'FECHAR','fechar'),'fechar')]",
    ]
    dispensou = False
    for xp in xps:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                try:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        time.sleep(0.8)
                        dispensou = True
                except Exception:
                    pass
        except Exception:
            pass
    return dispensou


def tem_falha_cloudflare(driver) -> bool:
    """Detecta o estado 'Falha na verificação' do Turnstile (print 2 do usuario)."""
    try:
        html = (driver.page_source or "").lower()
        return "falha na verifica" in html
    except Exception:
        return False


def turnstile_visivel(driver) -> bool:
    """Detecta se o widget do Cloudflare/Turnstile esta pedindo verificação."""
    from selenium.webdriver.common.by import By

    try:
        for fr in driver.find_elements(By.CSS_SELECTOR, "iframe"):
            try:
                src = (fr.get_attribute("src") or "").lower()
                if "turnstile" in src or "cloudflare" in src or "captcha" in src:
                    if fr.is_displayed():
                        return True
            except Exception:
                pass
        html = (driver.page_source or "").lower()
        return ("confirme que" in html and "humano" in html) or "verificando" in html
    except Exception:
        return False


def resolver_turnstile(driver, timeout: int = 20) -> bool:
    """Best-effort: clica no checkbox do Turnstile dentro do iframe (eventos
    confiaveis via WebDriver). Retorna True se conseguiu clicar."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    fim = time.time() + timeout
    while time.time() < fim:
        try:
            iframes = driver.find_elements(By.CSS_SELECTOR, "iframe")
            for fr in iframes:
                try:
                    src = (fr.get_attribute("src") or "").lower()
                except Exception:
                    src = ""
                if not ("turnstile" in src or "cloudflare" in src or "captcha" in src):
                    continue
                try:
                    driver.switch_to.frame(fr)
                    for sel in ("input[type='checkbox']", "label", "#challenge-stage", "body"):
                        try:
                            alvo = WebDriverWait(driver, 3).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                            alvo.click()
                            time.sleep(1)
                            break
                        except Exception:
                            continue
                    driver.switch_to.default_content()
                    return True
                except Exception:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(2)
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return False


def safe_click(driver, element):
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", element
        )
        time.sleep(0.4)
    except Exception:
        pass
    fechar_banners(driver)
    try:
        element.click()
        return
    except Exception as e:
        msg = str(e)
        if "click intercepted" not in msg and "not clickable" not in msg:
            raise
    driver.execute_script("arguments[0].click();", element)


def esperar_cloudflare(driver, timeout: int = 90) -> bool:
    """Retorna True quando #searchTxt esta visivel e clicavel (site liberado).
    No caminho dispensa o modal 'TEM NOVIDADE!' e, se o Turnstile falhar
    ('Falha na verificação'), recarrega a pagina e tenta de novo (ate 2x)."""
    from selenium.webdriver.common.by import By

    ini = time.time()
    refreshs = 0
    while time.time() - ini < timeout:
        try:
            dispensar_novidade(driver)
            if tem_falha_cloudflare(driver):
                if refreshs >= 2:
                    return False
                refreshs += 1
                print(f"[AVISO] Cloudflare falhou; recarregando pagina ({refreshs}/2)...")
                try:
                    driver.refresh()
                except Exception:
                    pass
                time.sleep(5)
                continue
            els = driver.find_elements(By.CSS_SELECTOR, "#searchTxt")
            if els and els[0].is_displayed():
                if not turnstile_visivel(driver):
                    return True
                # widget visivel: tenta o clique automatico e segue aguardando
                resolver_turnstile(driver, timeout=10)
                time.sleep(2)
                continue
            html = (driver.page_source or "").lower()
            if any(
                k in html
                for k in (
                    "verifying",
                    "checking your browser",
                    "sou humano",
                    "turnstile",
                    "cloudflare",
                    "captcha",
                )
            ):
                time.sleep(3)
                continue
        except Exception:
            pass
        time.sleep(2)
    try:
        from selenium.webdriver.common.by import By as _By

        dispensar_novidade(driver)
        els = driver.find_elements(_By.CSS_SELECTOR, "#searchTxt")
        return bool(els and els[0].is_displayed() and not turnstile_visivel(driver))
    except Exception:
        return False


def ler_alerta(driver) -> str:
    try:
        from selenium.webdriver.common.by import By

        el = driver.find_element(By.CSS_SELECTOR, "#alertTxt")
        txt = (el.text or "").strip()
        if txt and el.is_displayed():
            return txt
    except Exception:
        pass
    return ""


def esperar_novo_xml(destino: Path, antes: set, timeout: int = 60) -> Optional[Path]:
    ini = time.time()
    destino.mkdir(parents=True, exist_ok=True)
    while time.time() - ini < timeout:
        try:
            agora = set(os.listdir(destino))
            novos = agora - antes
            # ignora prints de erro e temporarios
            candidatos = [
                f
                for f in novos
                if f.lower().endswith(".xml") and not f.startswith("erro_")
            ]
            if candidatos:
                arq = destino / candidatos[0]
                # espera estabilizar (fim do .crdownload)
                t1 = arq.stat().st_size
                time.sleep(1)
                if arq.stat().st_size == t1 and t1 > 0:
                    return arq
            # tambem aceita qualquer xml novo mesmo sem diff (fallback por chave no nome)
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None


def baixar_uma_chave(driver, chave: str, destino: Path, espera: int = 20,
                     timeout_dl: int = 60) -> dict:
    """Executa Buscar -> Baixar XML -> Nova Consulta. Retorna dict resultado."""
    from selenium.webdriver.common.by import By

    antes = set(os.listdir(destino)) if destino.exists() else set()
    # 1. campo + buscar (dispensa o modal 'TEM NOVIDADE!' antes, senao ele cobre tudo)
    dispensar_novidade(driver)
    campo = find(driver, "search", espera)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", campo)
    campo.clear()
    campo.send_keys(chave)
    safe_click(driver, find(driver, "go", espera))
    # 2. aguarda tela de botoes OU alerta de erro. O Turnstile pode aparecer
    # DEPOIS do Buscar: tenta o clique automatico e da margem extra de espera.
    ini = time.time()
    liberou_dl = False
    tentou_turnstile = False
    limite = espera + 60
    while time.time() - ini < limite:
        dispensar_novidade(driver)
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "#downloadXmlBtn")
            if btn.is_displayed():
                liberou_dl = True
                break
        except Exception:
            pass
        if tem_falha_cloudflare(driver):
            raise RuntimeError(
                "Cloudflare recusou a verificacao ('Falha na verificação'). "
                "Feche o Chrome do robo, aguarde uns minutos (bloqueio temporario) e repita. "
                "Na janela do robo, resolver manualmente raramente adianta quando o navegador ja foi sinalizado.")
        if turnstile_visivel(driver) and not tentou_turnstile:
            tentou_turnstile = True
            print("[INFO] Turnstile apos o Buscar; tentando clique automatico...")
            resolver_turnstile(driver, timeout=15)
            time.sleep(2)
            continue
        alerta = ler_alerta(driver)
        if alerta:
            raise RuntimeError(f"MeuDanfe recusou a chave: {alerta}")
        time.sleep(1)
    if not liberou_dl:
        alerta = ler_alerta(driver)
        shot = str(destino / f"erro_{chave}_busca.png")
        try:
            driver.save_screenshot(shot)
        except Exception:
            shot = None
        raise RuntimeError(
            (f"botao Baixar XML nao apareceu. {alerta}" if alerta
             else "botao Baixar XML nao apareceu (chave pode ser invalida/nao encontrada).")
            + (f" Print: {shot}" if shot else "")
        )
    # 3. clicar Baixar XML (abre popup/download — popups devem estar liberados)
    safe_click(driver, find(driver, "dl", espera))
    arq = esperar_novo_xml(destino, antes, timeout_dl)
    if not arq:
        # tenta fallback: arquivo com a chave no nome ja existente
        cands = [f for f in destino.glob("*.xml") if chave in f.name]
        if cands:
            arq = cands[0]
        else:
            shot = str(destino / f"erro_{chave}_download.png")
            try:
                driver.save_screenshot(shot)
            except Exception:
                shot = None
            raise RuntimeError(
                "download do XML nao iniciou (libere pop-ups no navegador/Chrome do robo). "
                + (f"Print: {shot}" if shot else "")
            )
    # renomeia p/ padrao nfe_{chave}.xml
    final = arquivo_cache(chave)
    try:
        if arq.resolve() != final.resolve():
            if final.exists():
                final.unlink()
            arq.rename(final)
        else:
            final = arq
    except Exception:
        final = arq
    # 4. Nova Consulta p/ proxima chave
    try:
        safe_click(driver, find(driver, "new", 8))
        time.sleep(1)
    except Exception:
        driver.get(MEUDANFE_URL)
        esperar_cloudflare(driver, 60)
    return {"chave": chave, "ok": True, "arquivo": str(final.name)}


# ---------- rotas ----------
@app.get("/", include_in_schema=False)
def site():
    if HTML_FILE.exists():
        return FileResponse(str(HTML_FILE), media_type="text/html")
    return {"status": "backend XML no ar (index.html nao encontrado)", "docs": "/docs"}


@app.get("/api/status")
def status():
    return {"status": "backend XML local no ar",
            "modo": "local", "site": "/", "meudanfe": MEUDANFE_URL}


@app.get("/api/diag")
def diag():
    """Diagnostico rapido: mostra se selenium/chrome/driver estao OK (sem abrir pagina)."""
    info = {"ok": True, "python": os.sys.version.split()[0]}
    try:
        import selenium
        info["selenium"] = getattr(selenium, "__version__", "?")
    except Exception as e:
        info["ok"] = False
        info["selenium_erro"] = str(e)[:300]
        return info
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        info["chromedriver"] = ChromeDriverManager().install()[-80:]
    except Exception as e:
        info["ok"] = False
        info["driver_erro"] = str(e)[:500]
    try:
        import shutil
        info["chrome_path"] = shutil.which("chrome") or shutil.which("chrome.exe") or "nao encontrado no PATH"
    except Exception:
        pass
    info["xml_dir"] = str(XML_DIR.resolve())
    info["perfil_dir"] = str(PERFIL_DIR.resolve())
    return info


class LoteIn(BaseModel):
    chaves: List[str]
    espera_explicita: int = 20
    timeout_download: int = 60
    intervalo: float = 2.0
    tentativas: int = 2
    headless: bool = False


def _normaliza_lista(chaves: List[str]):
    vistas, fila, invalidas = set(), [], []
    for bruta in chaves or []:
        c = so_digitos(bruta)
        if not c or c in vistas:
            continue
        vistas.add(c)
        err = valida_chave(c)
        if err:
            invalidas.append({"chave": c or str(bruta), "ok": False, "erro": err})
        else:
            fila.append(c)
    return fila, invalidas


@app.get("/api/nfe/xml")
def baixar_xml_get(chave: str = Query(..., description="Chave de 44 digitos"),
                   espera: int = 20, timeout_dl: int = 60,
                   headless: bool = False):
    c = so_digitos(chave)
    err = valida_chave(c)
    if err:
        return JSONResponse({"chave": c, "ok": False, "erro": err}, status_code=400)
    cached = arquivo_cache(c)
    if cached.exists() and cached.stat().st_size > 0:
        return FileResponse(str(cached), media_type="application/xml",
                            filename=f"nfe_{c}.xml")
    driver = None
    try:
        try:
            driver = make_driver(XML_DIR, headless)
        except Exception as e:
            d = erro_detalhado("Falha ao abrir o Chrome (make_driver)", e)
            d["chave"] = c
            return JSONResponse(d, status_code=502)
        driver.get(MEUDANFE_URL)
        if not esperar_cloudflare(driver, 90):
            shot = str(XML_DIR / "cloudflare_bloqueio.png")
            try:
                driver.save_screenshot(shot)
            except Exception:
                pass
            return JSONResponse(
                {"chave": c, "ok": False,
                 "erro": "Cloudflare nao liberou. No Chrome que o robo abriu, marque 'Sou humano' 1x e repita. "
                         "Na 1a execucao isso e normal (perfil persistente guarda o cookie depois).",
                 "print": shot}, status_code=503)
        res = baixar_uma_chave(driver, c, XML_DIR, espera, timeout_dl)
        final = arquivo_cache(c)
        if final.exists():
            return FileResponse(str(final), media_type="application/xml",
                                filename=f"nfe_{c}.xml")
        return JSONResponse({"chave": c, "ok": False,
                             "erro": "XML baixado mas arquivo nao localizado: " + str(res)},
                            status_code=500)
    except Exception as e:
        d = erro_detalhado("Falha na automacao do MeuDanfe", e)
        d["chave"] = c
        return JSONResponse(d, status_code=502)
    finally:
        try:
            if driver is not None:
                driver.quit()
        except Exception:
            pass


@app.post("/api/nfe/lote")
def baixar_lote(lote: LoteIn):
    fila, invalidas = _normaliza_lista(lote.chaves)
    if not fila:
        return {"total": len(invalidas), "ok": 0, "resultados": invalidas}
    driver = None
    resultados = list(invalidas)
    try:
        try:
            driver = make_driver(XML_DIR, lote.headless)
        except Exception as e:
            d = erro_detalhado("Falha ao abrir o Chrome (make_driver)", e)
            return {"total": len(fila) + len(invalidas), "ok": 0,
                    "resultados": list(invalidas) + [
                        {"chave": c, "ok": False, "erro": d["erro"]} for c in fila]}
        driver.get(MEUDANFE_URL)
        if not esperar_cloudflare(driver, 90):
            shot = str(XML_DIR / "cloudflare_bloqueio.png")
            try:
                driver.save_screenshot(shot)
            except Exception:
                pass
            for c in fila:
                resultados.append(
                    {"chave": c, "ok": False,
                     "erro": "Cloudflare nao liberou (marque 'Sou humano' no Chrome do robo e repita). Print: " + shot})
            return {"total": len(resultados), "ok": 0, "resultados": resultados}
        for c in fila:
            cached = arquivo_cache(c)
            if cached.exists() and cached.stat().st_size > 0:
                resultados.append({"chave": c, "ok": True, "arquivo": cached.name,
                                   "cache": True})
                continue
            ok, erro, arq = False, None, None
            for t in range(1, max(1, lote.tentativas) + 1):
                try:
                    r = baixar_uma_chave(driver, c, XML_DIR,
                                         lote.espera_explicita,
                                         lote.timeout_download)
                    ok, arq = True, r.get("arquivo")
                    erro = None
                    break
                except Exception as e:
                    erro = str(e)[:400]
                    time.sleep(2 * t)
            resultados.append({"chave": c, "ok": ok, "arquivo": arq, "erro": erro})
            time.sleep(max(0, lote.intervalo))
    except Exception as e:
        d = erro_detalhado("Falha no lote", e)
        resultados.append({"chave": "lote", "ok": False, "erro": d["erro"]})
    finally:
        try:
            if driver is not None:
                driver.quit()
        except Exception:
            pass
    return {"total": len(resultados), "ok": sum(1 for r in resultados if r.get("ok")),
            "resultados": resultados}


@app.get("/api/nfe/arquivo/{chave}")
def servir_arquivo(chave: str):
    c = so_digitos(chave)
    f = arquivo_cache(c)
    if not f.exists():
        return JSONResponse({"ok": False, "erro": "XML ainda nao baixado p/ esta chave"}, status_code=404)
    return FileResponse(str(f), media_type="application/xml", filename=f"nfe_{c}.xml")


@app.post("/api/nfe/zip")
def baixar_zip(lote: LoteIn):
    """Retorna .zip com os XMLs ja baixados (baixa os faltantes antes)."""
    fila, _ = _normaliza_lista(lote.chaves)
    resp = baixar_lote(lote)  # garante downloads
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in resp.get("resultados", []):
            if r.get("ok"):
                f = XML_DIR / (r.get("arquivo") or f"nfe_{r.get('chave')}.xml")
                if f.exists():
                    z.write(str(f), arcname=f.name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": "attachment; filename=xml_nfe_lote.zip"})


if __name__ == "__main__":
    import uvicorn
    print(f"Site local em http://{HOST_LOCAL}:{PORTA_LOCAL}/")
    uvicorn.run(app, host=HOST_LOCAL, port=PORTA_LOCAL)
