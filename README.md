# Central DFe — Site Local multi-cliente

> Esta página no GitHub é **vitrine/demonstração**. O site só funciona de
> verdade rodando local no Windows (o GitHub Pages não executa o backend).

Site 100% local: várias empresas (cada uma com seu certificado A1),
download de XML da SEFAZ (`NFeDistribuicaoDFe`), importação de planilha
(xlsx/csv/txt → chaves de 44 dígitos) e central de documentos
(upload avulso, pasta inteira, zip → tabela + downloads).

## Como usar de verdade

1. Baixe este repositório e instale o Python 3.12+.
2. Dê duplo-clique em `iniciar_central.bat` (abre `http://127.0.0.1:8004/`).
3. Aba Empresas: cadastre (nome, CNPJ, UF) e envie o `.pfx`.
4. Selecione a empresa no topo, digite a senha do PFX (vale na sessão).
5. Baixar SEFAZ: 1 chave, várias ou via planilha.
6. Documentos: envie XMLs/pasta/zip, veja a tabela, baixe individual ou tudo em zip.

## Segurança

`certs/`, `docs_xml/` e `clientes.json` nunca vão para o git. A senha do
PFX nunca é salva em disco.
