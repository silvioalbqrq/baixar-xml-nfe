# Baixar XML de NF-e / CT-e pela Chave — Site Local

Site **100% local** que automatiza o passo a passo do [meudanfe.com.br](https://meudanfe.com.br/): colar a **chave de acesso de 44 dígitos** → **Buscar** → passar pelo **Cloudflare** → **Baixar XML** → **Nova Consulta**. Suporta chave única e **lote via CSV/TXT**.

> ⚠️ O MeuDanfe usa proteção Cloudflare/Turnstile — por isso não dá para fazer tudo só no navegador (CORS + captcha). Este projeto usa um **backend local com Chrome automatizado (Selenium + undetected-chromedriver)**.

## Como usar

1. Instale o [Python 3.12+](https://www.python.org/) (marque **Add to PATH**) e o [Google Chrome](https://www.google.com/chrome/).
2. Dê **duplo-clique** em **`Abrir Site Local`** (atalho) ou em **`iniciar_xml.bat`**.
3. O navegador abre sozinho em `http://127.0.0.1:8002/`.
4. Digite a chave de 44 dígitos e clique em **BUSCAR** — o XML baixa automaticamente.
5. Para várias chaves: arraste um **.csv/.txt** (ou cole as chaves) → **Carregar texto** → **Baixar lote** → **Baixar tudo (.zip)**.

Não feche as janelas pretas enquanto usar. Na 1ª vez, marque **“Sou humano”** no Chrome que o robô abrir (se pedir) e **libere pop-ups**.

## Passo a passo automatizado (igual ao manual)

| # | Manual no MeuDanfe | O que o robô faz |
|---|---|---|
| 1 | Acessar `meudanfe.com.br` e colar a chave | Abre o site e preenche `#searchTxt` |
| 2 | Clicar em **BUSCAR** | Clica em `#searchBtn` (dispensa o modal “TEM NOVIDADE!”) |
| 3 | Passar pelo “Verificando… Cloudflare” | Aguarda o Turnstile, tenta o clique automático e recarrega se falhar |
| 4 | Clicar em **Baixar XML** | Clica em `#downloadXmlBtn` e salva em `xml_baixados/nfe_<chave>.xml` |
| 5 | Clicar em **NOVA CONSULTA** | Clica em `#newSearchBtn` e parte para a próxima chave |

## Estrutura

```
baixar-xml-nfe/
├── backend.py           # FastAPI + Selenium (serve o site e automatiza o MeuDanfe)
├── index.html           # Frontend (busca única, lote CSV/TXT, progresso, log, ZIP)
├── iniciar_xml.bat      # Lançador: instala deps, liga o backend e abre o navegador
├── Abrir Site Local.lnk # Atalho para o lançador
├── requirements.txt     # fastapi, uvicorn, selenium, webdriver-manager, undetected-chromedriver
├── xml_baixados/        # XMLs baixados (nfe_<chave>.xml) + prints de erro
└── chrome-perfil-xml/   # Perfil persistente do Chrome do robô (guarda o cookie do Cloudflare)
```

## API local

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | O site |
| GET | `/api/status` | Saúde do backend |
| GET | `/api/diag` | Diagnóstico (versões, pastas) |
| GET | `/api/nfe/xml?chave=<44 dígitos>` | Baixa o XML de 1 chave |
| POST | `/api/nfe/lote` | `{"chaves": [...]}` — baixa o lote |
| GET | `/api/nfe/arquivo/<chave>` | Serve um XML já baixado |
| POST | `/api/nfe/zip` | Retorna `.zip` com os XMLs do lote |

## Validação das chaves

Antes de abrir o navegador, cada chave é validada: 44 dígitos numéricos, modelo `55` (NF-e) ou `57` (CT-e) nas posições 21–22 e **dígito verificador (módulo 11)**.

## Problemas comuns

| Sintoma | Causa | Solução |
|---|---|---|
| Janela preta abre e fecha | `.bat` com quebra de linha errada ou `)` em `echo` dentro de bloco | Use o `iniciar_xml.bat` atual (CRLF, sem parênteses em blocos) |
| `127.0.0.1:8002` recusa conexão | Backend não ligou | Veja a janela “XML backend local” ou `backend.log` |
| `Falha na verificação` (Cloudflare) | Navegador sinalizado / bloqueio temporário | Feche tudo, aguarde ~15 min e repita; não insista em rajada |
| `botão Baixar XML não apareceu` | Chave inválida/não encontrada ou Cloudflare bloqueou | Confira a chave e o print `erro_<chave>_busca.png` em `xml_baixados/` |
| CSV/TXT com 0 chaves | Arquivo sem sequência de 44 dígitos | Confira o arquivo; no Excel, salve como CSV com a coluna em TEXTO |

## Aviso legal

Projeto educacional de automação sobre serviço de terceiros. Os XMLs pertencem aos seus emitentes/destinatários. Confira documentos críticos no portal oficial. Respeite os termos de uso do MeuDanfe e da SEFAZ.
