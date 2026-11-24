# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Estado do projeto

Fase de MVP do coletor DF-e em `prototipo/coletor/` (Python 3 stdlib; PostgreSQL via `psycopg` só em produção). Sem gerenciador de pacotes nem lint.

Comandos reais:
```bash
cd "/Users/joedsonportela/Projetos/integracao sefaz-ba" && python3 -m unittest discover -s prototipo/coletor -v
zsh prototipo/consulta_dfe_homologacao.sh schemas/testes/envelope_distDFeInt_hom_ultNSU0.xml prototipo/saida_hom_ultNSU0.xml
python3 prototipo/coletor/coletor.py ciclo --cnpj <CNPJ> --ambiente 2 --raiz <pasta> --pfx <pfx> --dsn sqlite:///<arquivo.db>
```
Os testes rodam sem rede, usando os exemplos de `schemas/testes/`. Docker e PostgreSQL ainda não foram testados.

Documento de referência: `ARQUITETURA.md` (lacunas marcadas **[VALIDAR]**).

## Comandos úteis hoje

Validar XML contra os XSD oficiais (pacote DF-e 1.04):

```bash
cd schemas/PL_NFeDistDFe_104
xmllint --noout --schema retDistDFeInt_v1.01.xsd ../testes/ret_ultNSU448.xml
xmllint --noout --schema distDFeInt_v1.01.xsd ../testes/distDFeInt_cliente.xml
```

Extrair texto de PDF da NT (requer `brew install poppler`): `pdftotext -layout arquivo.pdf saida.txt`.

## Mapa do repositório

- `ARQUITETURA.md`: desenho da integração (fontes de dados, componentes, fluxo, LGPD, tabela de lacunas). Mantenha a tabela de lacunas atualizada ao validar um ponto.
- `wsdl/`: WSDL reais baixados da SEFAZ-BA e do ambiente nacional. Não editar; são referência de contrato.
- `schemas/PL_NFeDistDFe_104/`: XSD oficiais da distribuição DF-e (`distDFeInt`, `retDistDFeInt`, `tiposDistDFe`, `resNFe`, `resEvento`).
- `schemas/testes/`: mensagens de exemplo (válidas e inválidas) e respostas reais de consultas. Os arquivos `resposta_*` e `ret_*` contêm dados fiscais de cliente real: não versionar nem compartilhar.

## Arquitetura pretendida (resumo)

1. **Coleta SEFAZ/DF-e:** consulta `nfeDistDFeInteresse` no host nacional `www1.nfe.fazenda.gov.br` com o certificado e-CNPJ do cliente (mTLS). A consulta usa `distNSU/ultNSU`, que é o caminho principal para download em lote. A consulta por chave e por NSU é pontual.
2. **Armazenamento bruto:** XML por NSU e chave de acesso, sem alteração.
3. **Envio ao Domínio:** API Domínio com token OAuth (`client_credentials`) e Integration Key. Detalhes do endpoint de envio ainda **não confirmados**.

Serviços de NF-e no host `nfe.sefaz.ba.gov.br` respondem com certificado de cliente (sem ele, `403`). Os demais WSDL da SEFAZ-BA estão em `wsdl/`.

## Regras que não podem ser quebradas

- **NSU sequencial:** cada consulta `distNSU` parte do `ultNSU` retornado pela anterior. Consulta fora de sequência gera bloqueio (656).
- **Pausa de 1 hora:** com `cStat=137` ou com `ultNSU == maxNSU`, aguardar 1 hora. Reconsulta antes disso bloqueia o CNPJ por 1 hora.
- **Limite de consultas por chave/NSU:** 20 por hora (NT 2014.002, seção 3.11.4).
- **Consultas em produção (`tpAmb=1`) são reais:** conferir o CNPJ e o ambiente antes de rodar qualquer envio. Testes de estrutura usam só validação local com `xmllint`.
- **Certificado e senha:** o `.pfx` do cliente fica fora do repositório (em `~/Downloads`). Nunca colar a senha em chat, arquivo ou comando versionado. Usar `read -s` e `unset`.
- **TLS:** nunca desativar a verificação de certificado (`-k`, `verify=False`). Se a cadeia não for confiável, instalar a cadeia ICP-Brasil e da SEFAZ-BA.

## Convenções

- Documentos em português do Brasil.
- Nomes de pastas com espaço (`integracao sefaz-ba`) exigem aspas em todos os comandos de shell.
