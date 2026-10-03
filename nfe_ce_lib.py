"""Lib CE NF-e — validacao chave 44 digitos + URLs AN/SVRS."""
import re
import base64
import gzip

AN_URLS = {
    "prod": "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
    "homolog": "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
}

SVRS_URLS = {
    "svrs_nfe_autorizacao": "https://nfe.svrs.rs.gov.br/ws/NfeAutorizacao/NFeAutorizacao4.asmx?WSDL",
    "svrs_nfe_ret": "https://nfe.svrs.rs.gov.br/ws/NfeRetAutorizacao/NFeRetAutorizacao4.asmx?WSDL",
    "svrs_nfe_inutilizacao": "https://nfe.svrs.rs.gov.br/ws/nfeinutilizacao/nfeinutilizacao4.asmx?WSDL",
    "svrs_nfe_consulta": "https://nfe.svrs.rs.gov.br/ws/NfeConsulta/NfeConsulta4.asmx?WSDL",
    "svrs_nfe_status": "https://nfe.svrs.rs.gov.br/ws/NfeStatusServico/NfeStatusServico4.asmx?WSDL",
    "svrs_nfe_cadastro": "https://cad.svrs.rs.gov.br/ws/cadconsultacadastro/cadconsultacadastro4.asmx?WSDL",
    "svrs_recepcao_evento": "https://nfe.svrs.rs.gov.br/ws/recepcaoevento/recepcaoevento4.asmx?WSDL",
}


def so_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def dv_ok(chave: str) -> bool:
    if not re.fullmatch(r"\d{44}", chave or ""):
        return False
    corpo = chave[:43]
    soma, peso = 0, 2
    for d in reversed(corpo):
        soma += int(d) * peso
        peso = peso + 1 if peso < 9 else 2
    resto = soma % 11
    calc = 0 if resto in (0, 1) else 11 - resto
    return calc == int(chave[43])


UFS_VALIDAS = {"11","12","13","14","15","16","17","21","22","23","24","25","26","27","28","29","31","32","33","35","41","42","43","50","51","52","53","91","92"}

def valida_chave(chave: str):
    c = so_digitos(chave)
    if not re.fullmatch(r"\d{44}", c):
        return "chave deve ter 44 digitos numericos"
    if c[0:2] not in UFS_VALIDAS:
        return "UF do emitente invalida (posicoes 1-2)"
    if c[20:22] != "55":
        return "modelo invalido (posicoes 21-22 devem ser 55 p/ NF-e)"
    if not dv_ok(c):
        return "DV invalido — confira a digitacao"
    return None


def montar_dist_dfe(cnpj: str, chave: str, tp_amb: int = 1, uf_autor: str = "23") -> bytes:
    cnpj_d = so_digitos(cnpj)
    if len(cnpj_d) != 14:
        raise ValueError("CNPJ deve ter 14 digitos")
    err = valida_chave(chave)
    if err:
        raise ValueError(err)
    uf_a = so_digitos(uf_autor or "23")[:2] or "23"
    if uf_a not in UFS_VALIDAS:
        raise ValueError("UF do autor invalida")
    xml = (
        f'<distDFeInt versao="1.01" xmlns="http://www.portalfiscal.inf.br/nfe">'
        f"<tpAmb>{tp_amb}</tpAmb><cUFAutor>{uf_a}</cUFAutor><CNPJ>{cnpj_d}</CNPJ>"
        f"<consChNFe><chNFe>{chave}</chNFe></consChNFe></distDFeInt>"
    )
    return xml.encode("utf-8")


def decodificar_doczip(b64: str) -> bytes:
    raw = base64.b64decode(b64)
    try:
        return gzip.decompress(raw)
    except OSError:
        return raw
