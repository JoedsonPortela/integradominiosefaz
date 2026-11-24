#!/usr/bin/env python3
"""Lê uma resposta retDistDFeInt (direta ou dentro de SOAP) e extrai os documentos.

Uso: python3 prototipo/parse_retdistdfe.py <resposta.xml> <pasta_saida>

- Mostra cStat, xMotivo, ultNSU, maxNSU e a contagem de docZip por schema.
- Cada docZip é base64 de um gzip; grava o XML descompactado como
  <NSU>_<schema>.xml na pasta de saída (nunca dentro do repositório).
- Não faz nova consulta à SEFAZ. Só lê o arquivo.
"""

import base64
import collections
import gzip
import pathlib
import sys
import xml.etree.ElementTree as ET

NFE_NS = "http://www.portalfiscal.inf.br/nfe"


def tag(local: str) -> str:
    return f"{{{NFE_NS}}}{local}"


def texto(elem, local: str):
    filho = elem.find(tag(local))
    return filho.text.strip() if filho is not None and filho.text else None


def parse(caminho: pathlib.Path, saida: pathlib.Path) -> dict:
    raiz = ET.parse(caminho).getroot()

    # Aceita o retDistDFeInt direto ou embrulhado em SOAP (qualquer profundidade)
    ret = next((e for e in raiz.iter(tag("retDistDFeInt"))), None)
    if ret is None:
        raise SystemExit("retDistDFeInt não encontrado no arquivo")

    resumo = {
        "tpAmb": texto(ret, "tpAmb"),
        "cStat": texto(ret, "cStat"),
        "xMotivo": texto(ret, "xMotivo"),
        "ultNSU": texto(ret, "ultNSU"),
        "maxNSU": texto(ret, "maxNSU"),
        "documentos": 0,
        "por_schema": collections.Counter(),
    }

    lote = ret.find(tag("loteDistDFeInt"))
    docs = lote.findall(tag("docZip")) if lote is not None else []

    saida.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        nsu = doc.get("NSU")
        schema = doc.get("schema", "desconhecido")
        xml_bytes = gzip.decompress(base64.b64decode(doc.text.strip()))
        (saida / f"{nsu}_{schema.replace('.xsd', '')}.xml").write_bytes(xml_bytes)
        resumo["documentos"] += 1
        resumo["por_schema"][schema] += 1

    return resumo


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    resumo = parse(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
    print(f"tpAmb={resumo['tpAmb']} cStat={resumo['cStat']} xMotivo={resumo['xMotivo']}")
    print(f"ultNSU={resumo['ultNSU']} maxNSU={resumo['maxNSU']}")
    print(f"documentos={resumo['documentos']}")
    for schema, n in resumo["por_schema"].items():
        print(f"  {schema}: {n}")


if __name__ == "__main__":
    main()
