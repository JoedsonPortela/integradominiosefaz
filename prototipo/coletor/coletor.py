"""MVP do coletor DF-e (NFeDistribuicaoDFe, nfeDistDFeInteresse).

Faz: consulta por NSU, grava XML bruto (atômico, sem sobrescrever), registra no banco,
respeita 137/656 e a pausa de 1 hora, e lista resNFe ainda sem procNFe.
Não faz: envio ao Domínio, manifestação de ciência, consulta por chave.

Uso:
  python3 -m coletor ciclo --cnpj 11222333000181 --ambiente 2 --raiz ./dados --dsn sqlite:///./dfe.db
  python3 -m coletor pendentes --cnpj 11222333000181 --dsn sqlite:///./dfe.db

Senha do certificado: pedida no terminal (ou lida de DFE_SENHA_ARQUIVO, ex.: Docker secret).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import gzip
import hashlib
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional

NFE_NS = "http://www.portalfiscal.inf.br/nfe"
URL = {
    "2": "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
    "1": "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
}
PAUSA = timedelta(hours=1)
NSU_ZERO = "000000000000000"
SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def _tag(nome: str) -> str:
    return f"{{{NFE_NS}}}{nome}"


def _agora() -> datetime:
    return datetime.now(timezone.utc)


class ConflitoBruto(Exception):
    """Mesmo NSU já gravado com conteúdo diferente: nunca sobrescrever."""


@dataclass
class Documento:
    nsu: str
    schema: str
    conteudo: bytes
    chave: Optional[str]
    tipo_evento: Optional[str]


@dataclass
class Resposta:
    tpAmb: Optional[str]
    cStat: str
    xMotivo: Optional[str]
    ultNSU: str
    maxNSU: str
    docs: List[Documento]


@dataclass
class Relatorio:
    status: str
    cStat: Optional[str]
    novos: int = 0
    motivo: str = ""


# ---------- XML ----------

def monta_envelope(cnpj: str, ultimo_nsu: str, tp_amb: str, cuf: str = "29") -> bytes:
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">
      <nfeDadosMsg>
<distDFeInt xmlns="{NFE_NS}" versao="1.01">
  <tpAmb>{tp_amb}</tpAmb>
  <cUFAutor>{cuf}</cUFAutor>
  <CNPJ>{cnpj}</CNPJ>
  <distNSU>
    <ultNSU>{ultimo_nsu}</ultNSU>
  </distNSU>
</distDFeInt>
      </nfeDadosMsg>
    </nfeDistDFeInteresse>
  </soap12:Body>
</soap12:Envelope>
"""
    return xml.encode("utf-8")


def _extrai_chave_e_tipo(conteudo: bytes):
    """Retorna (chave_nfe, tipo_evento) conforme o tipo do documento."""
    raiz = ET.fromstring(conteudo)
    nome = raiz.tag.split("}")[-1]
    if nome == "procNFe":
        inf = raiz.find(f".//{_tag('infNFe')}")
        chave = inf.get("Id", "")[3:] if inf is not None else None
        return chave, None
    chave_el = raiz.find(f".//{_tag('chNFe')}")
    chave = chave_el.text.strip() if chave_el is not None and chave_el.text else None
    tp_el = raiz.find(f".//{_tag('tpEvento')}")
    tipo = tp_el.text.strip() if tp_el is not None and tp_el.text else None
    return chave, tipo


def interpreta_resposta(xml_bytes: bytes) -> Resposta:
    raiz = ET.fromstring(xml_bytes)
    ret = next((e for e in raiz.iter(_tag("retDistDFeInt"))), None)
    if ret is None:
        raise ValueError("retDistDFeInt não encontrado na resposta")

    def texto(pai, nome):
        el = pai.find(_tag(nome))
        return el.text.strip() if el is not None and el.text else None

    docs: List[Documento] = []
    lote = ret.find(_tag("loteDistDFeInt"))
    if lote is not None:
        for d in lote.findall(_tag("docZip")):
            conteudo = gzip.decompress(base64.b64decode(d.text.strip()))
            chave, tipo = _extrai_chave_e_tipo(conteudo)
            docs.append(Documento(d.get("NSU"), d.get("schema", ""), conteudo, chave, tipo))

    return Resposta(
        tpAmb=texto(ret, "tpAmb"),
        cStat=texto(ret, "cStat") or "",
        xMotivo=texto(ret, "xMotivo"),
        ultNSU=texto(ret, "ultNSU") or NSU_ZERO,
        maxNSU=texto(ret, "maxNSU") or NSU_ZERO,
        docs=docs,
    )


# ---------- arquivos brutos ----------

def _sha256(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def grava_bruto(raiz: Path, cnpj: str, doc: Documento):
    """Grava o XML por NSU de forma atômica. Retorna (caminho, sha256, tamanho)."""
    pasta = Path(raiz) / cnpj / "nsu"
    pasta.mkdir(parents=True, exist_ok=True)
    os.chmod(pasta.parent, 0o700)
    os.chmod(pasta, 0o700)

    destino = pasta / f"{doc.nsu}_{doc.schema.replace('.xsd', '')}.xml"
    sha = _sha256(doc.conteudo)
    if destino.exists():
        if _sha256(destino.read_bytes()) != sha:
            raise ConflitoBruto(f"{destino} já existe com conteúdo diferente")
        return destino, sha, len(doc.conteudo)

    tmp = destino.with_suffix(".tmp")
    tmp.write_bytes(doc.conteudo)
    os.chmod(tmp, 0o600)
    os.replace(tmp, destino)
    return destino, sha, len(doc.conteudo)


# ---------- banco ----------

class Repositorio:
    """Acesso mínimo ao banco. Usa '?' nos SQLs e converte para '%s' em psycopg."""

    def __init__(self, conexao, estilo: str = "qmark"):
        self.con = conexao
        self.estilo = estilo

    def _q(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.estilo == "format" else sql

    def executa(self, sql: str, params=()):
        cur = self.con.cursor()
        cur.execute(self._q(sql), params)
        return cur

    def criar_schema(self):
        for comando in SCHEMA_SQL.read_text(encoding="utf-8").split(";"):
            if comando.strip():
                self.executa(comando)
        self.con.commit()

    def controle(self, cnpj: str) -> dict:
        self.executa(
            "INSERT INTO dfe_controle (cnpj) VALUES (?) ON CONFLICT (cnpj) DO NOTHING", (cnpj,)
        )
        cur = self.executa(
            "SELECT ultimo_nsu, max_nsu, proxima_consulta_permitida_em FROM dfe_controle WHERE cnpj = ?",
            (cnpj,),
        )
        ultimo, maximo, proxima = cur.fetchone()
        return {"ultimo_nsu": ultimo, "max_nsu": maximo, "proxima": proxima}

    def atualiza_controle(self, cnpj: str, **campos):
        colunas = ", ".join(f"{c} = ?" for c in campos)
        self.executa(
            f"UPDATE dfe_controle SET {colunas} WHERE cnpj = ?", (*campos.values(), cnpj)
        )
        self.con.commit()

    def insere_documento(self, cnpj: str, doc: Documento, caminho, sha, tamanho, agora: str) -> bool:
        cur = self.executa(
            """INSERT INTO dfe_documento
               (cnpj, nsu, schema_doc, chave_nfe, tipo_evento, caminho_bruto, sha256, tamanho_bytes, recebido_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (cnpj, nsu) DO NOTHING""",
            (cnpj, doc.nsu, doc.schema, doc.chave, doc.tipo_evento, str(caminho), sha, tamanho, agora),
        )
        return cur.rowcount == 1

    def pendentes_ciencia(self, cnpj: str) -> List[str]:
        """Chaves com resNFe e sem procNFe recebido (aguardam Ciência da Operação)."""
        cur = self.executa(
            """SELECT DISTINCT r.chave_nfe FROM dfe_documento r
               WHERE r.cnpj = ? AND r.schema_doc LIKE 'resNFe%' AND r.chave_nfe IS NOT NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM dfe_documento p
                   WHERE p.cnpj = r.cnpj AND p.chave_nfe = r.chave_nfe AND p.schema_doc LIKE 'procNFe%')
               ORDER BY r.chave_nfe""",
            (cnpj,),
        )
        return [linha[0] for linha in cur.fetchall()]


# ---------- ciclo ----------

Transporte = Callable[[bytes], bytes]


def executa_ciclo(
    repo: Repositorio,
    cnpj: str,
    transporte: Transporte,
    raiz: Path,
    tp_amb: str,
    agora: Optional[datetime] = None,
) -> Relatorio:
    """Uma consulta por execução. Aplica as regras da NT 2014.002 (NSU, 137, 656, 1 hora)."""
    agora = agora or _agora()
    ctrl = repo.controle(cnpj)

    if ctrl["proxima"] and agora < datetime.fromisoformat(ctrl["proxima"]):
        return Relatorio("aguardando", None, motivo=f"pausa até {ctrl['proxima']}")

    resposta = interpreta_resposta(transporte(monta_envelope(cnpj, ctrl["ultimo_nsu"], tp_amb)))
    agora_txt = agora.isoformat()
    repo.atualiza_controle(
        cnpj, ultima_consulta_em=agora_txt, ultimo_cstat=resposta.cStat
    )

    if resposta.cStat == "656":
        # Consumo indevido: não avança NSU; aguarda 1 hora
        repo.atualiza_controle(cnpj, proxima_consulta_permitida_em=(agora + PAUSA).isoformat())
        return Relatorio("bloqueado_656", "656", motivo="aguardar 1 hora")

    if resposta.cStat == "137":
        # Nenhum documento: NT pede 1 hora antes da próxima consulta
        repo.atualiza_controle(
            cnpj,
            max_nsu=resposta.maxNSU,
            proxima_consulta_permitida_em=(agora + PAUSA).isoformat(),
        )
        return Relatorio("sem_documentos", "137", motivo="aguardar 1 hora")

    if resposta.cStat != "138":
        # Outro status: não avança estado; o operador decide
        return Relatorio("erro_cstat", resposta.cStat, motivo=resposta.xMotivo or "")

    novos = 0
    for doc in resposta.docs:
        caminho, sha, tamanho = grava_bruto(raiz, cnpj, doc)
        if repo.insere_documento(cnpj, doc, caminho, sha, tamanho, agora_txt):
            novos += 1
    repo.con.commit()

    campos = {"ultimo_nsu": resposta.ultNSU, "max_nsu": resposta.maxNSU}
    if resposta.ultNSU == resposta.maxNSU:
        campos["proxima_consulta_permitida_em"] = (agora + PAUSA).isoformat()
        status = "sincronizado"
    else:
        campos["proxima_consulta_permitida_em"] = None
        status = "parcial"
    repo.atualiza_controle(cnpj, **campos)
    return Relatorio(status, "138", novos=novos)


# ---------- transporte real (curl + certificado do cliente) ----------

def transporte_curl(pfx: Path, senha: str, tp_amb: str) -> Transporte:
    def enviar(envelope: bytes) -> bytes:
        with tempfile.NamedTemporaryFile(delete=False) as corpo:
            caminho_corpo = corpo.name
        try:
            cmd = [
                "curl", "-sS", "--max-time", "60",
                "--cert-type", "P12", "--cert", str(pfx), "--pass", senha,
                "-H", "Content-Type: application/soap+xml; charset=utf-8",
                "--data-binary", "@-", "-o", caminho_corpo, "-w", "%{http_code}",
                URL[tp_amb],
            ]
            res = subprocess.run(cmd, input=envelope, capture_output=True, check=False)
            http = res.stdout.decode().strip()
            if res.returncode != 0 or http != "200":
                raise RuntimeError(f"HTTP {http or '000'} (curl {res.returncode}): {res.stderr.decode()[:200]}")
            return Path(caminho_corpo).read_bytes()
        finally:
            os.unlink(caminho_corpo)

    return enviar


# ---------- CLI ----------

def _conecta(dsn: str):
    if dsn.startswith("sqlite:///"):
        import sqlite3

        return Repositorio(sqlite3.connect(dsn[len("sqlite:///"):]), "qmark")
    import psycopg  # PostgreSQL em produção (container)

    return Repositorio(psycopg.connect(dsn), "format")


def _senha() -> str:
    arquivo = os.environ.get("DFE_SENHA_ARQUIVO")
    if arquivo:
        return Path(arquivo).read_text(encoding="utf-8").strip()
    return getpass.getpass("Senha do certificado: ")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="coletor")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("ciclo")
    c.add_argument("--cnpj", required=True)
    c.add_argument("--ambiente", choices=["1", "2"], default="2")
    c.add_argument("--raiz", type=Path, required=True)
    c.add_argument("--pfx", type=Path, default=Path(os.environ.get("DFE_PFX", "")))
    c.add_argument("--dsn", required=True)
    q = sub.add_parser("pendentes")
    q.add_argument("--cnpj", required=True)
    q.add_argument("--dsn", required=True)
    args = p.parse_args(argv)

    repo = _conecta(args.dsn)
    repo.criar_schema()
    if args.cmd == "pendentes":
        for chave in repo.pendentes_ciencia(args.cnpj):
            print(chave)
        return 0

    transporte = transporte_curl(args.pfx, _senha(), args.ambiente)
    rel = executa_ciclo(repo, args.cnpj, transporte, args.raiz, args.ambiente)
    print(f"status={rel.status} cStat={rel.cStat} novos={rel.novos} {rel.motivo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
