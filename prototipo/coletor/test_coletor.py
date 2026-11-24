"""Testes offline do coletor MVP. Rodar: python3 -m unittest discover -s prototipo/coletor -v

Usa os exemplos reais já salvos em schemas/testes/ (não há rede). Não envia nada à SEFAZ.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import coletor as c

REPO = Path(__file__).resolve().parents[2]
TESTES = REPO / "schemas" / "testes"
CNPJ = "11222333000181"
T0 = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)

RESP_398 = (TESTES / "ret_ultNSU398.xml").read_bytes()  # 138, ultNSU 448, max 483, 50 docs
RESP_448 = (TESTES / "ret_ultNSU448.xml").read_bytes()  # 138, ultNSU 483, max 483, 35 docs
RESP_137 = (TESTES / "retDistDFeInt_ok.xml").read_bytes()  # 137 nenhum documento
RESP_656 = b"""<?xml version="1.0" encoding="utf-8"?>
<retDistDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
  <tpAmb>2</tpAmb><verAplic>X</verAplic><cStat>656</cStat>
  <xMotivo>Rejeicao: Consumo indevido</xMotivo><dhResp>2026-11-24T12:00:00-03:00</dhResp>
  <ultNSU>000000000000448</ultNSU><maxNSU>000000000000483</maxNSU>
</retDistDFeInt>"""


def fake(resposta: bytes):
    chamadas = []

    def transporte(envelope: bytes) -> bytes:
        chamadas.append(envelope)
        return resposta

    transporte.chamadas = chamadas
    return transporte


class ColetorTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.raiz = Path(self.dir.name) / "dados"
        con = sqlite3.connect(":memory:")
        self.repo = c.Repositorio(con, "qmark")
        self.repo.criar_schema()

    def tearDown(self):
        self.dir.cleanup()

    def test_parcial_grava_bruto_e_avanca_nsu(self):
        rel = c.executa_ciclo(self.repo, CNPJ, fake(RESP_398), self.raiz, "2", T0)
        self.assertEqual(rel.status, "parcial")
        self.assertEqual(rel.novos, 50)
        ctrl = self.repo.controle(CNPJ)
        self.assertEqual(ctrl["ultimo_nsu"], "000000000000448")
        self.assertIsNone(ctrl["proxima"])  # parcial: sem pausa
        arquivos = list((self.raiz / CNPJ / "nsu").glob("*.xml"))
        self.assertEqual(len(arquivos), 50)

    def test_repetir_mesmo_lote_nao_duplica(self):
        c.executa_ciclo(self.repo, CNPJ, fake(RESP_398), self.raiz, "2", T0)
        rel = c.executa_ciclo(self.repo, CNPJ, fake(RESP_398), self.raiz, "2", T0 + timedelta(hours=2))
        self.assertEqual(rel.novos, 0)
        total = self.repo.executa("SELECT COUNT(*) FROM dfe_documento WHERE cnpj = ?", (CNPJ,)).fetchone()[0]
        self.assertEqual(total, 50)

    def test_sincronizado_entra_em_pausa_de_1_hora(self):
        c.executa_ciclo(self.repo, CNPJ, fake(RESP_398), self.raiz, "2", T0)
        rel = c.executa_ciclo(self.repo, CNPJ, fake(RESP_448), self.raiz, "2", T0 + timedelta(hours=3))
        self.assertEqual(rel.status, "sincronizado")
        ctrl = self.repo.controle(CNPJ)
        self.assertEqual(ctrl["ultimo_nsu"], ctrl["max_nsu"])
        self.assertEqual(ctrl["proxima"], (T0 + timedelta(hours=4)).isoformat())

    def test_nao_consulta_durante_a_pausa(self):
        c.executa_ciclo(self.repo, CNPJ, fake(RESP_448), self.raiz, "2", T0)
        transporte = fake(RESP_448)
        rel = c.executa_ciclo(self.repo, CNPJ, transporte, self.raiz, "2", T0 + timedelta(minutes=30))
        self.assertEqual(rel.status, "aguardando")
        self.assertEqual(transporte.chamadas, [])  # nenhuma requisição enviada

    def test_137_pausa_e_nao_grava(self):
        rel = c.executa_ciclo(self.repo, CNPJ, fake(RESP_137), self.raiz, "2", T0)
        self.assertEqual(rel.status, "sem_documentos")
        self.assertEqual(rel.cStat, "137")
        self.assertEqual(self.repo.controle(CNPJ)["proxima"], (T0 + timedelta(hours=1)).isoformat())
        self.assertFalse((self.raiz / CNPJ / "nsu").exists() and any((self.raiz / CNPJ / "nsu").iterdir()))

    def test_656_nao_avanca_nsu_e_pausa(self):
        c.executa_ciclo(self.repo, CNPJ, fake(RESP_398), self.raiz, "2", T0)
        antes = self.repo.controle(CNPJ)["ultimo_nsu"]
        rel = c.executa_ciclo(self.repo, CNPJ, fake(RESP_656), self.raiz, "2", T0 + timedelta(hours=2))
        self.assertEqual(rel.status, "bloqueado_656")
        self.assertEqual(self.repo.controle(CNPJ)["ultimo_nsu"], antes)
        self.assertEqual(self.repo.controle(CNPJ)["proxima"], (T0 + timedelta(hours=3)).isoformat())

    def test_conflito_de_bruto_nunca_sobrescreve(self):
        c.executa_ciclo(self.repo, CNPJ, fake(RESP_398), self.raiz, "2", T0)
        arquivo = next((self.raiz / CNPJ / "nsu").glob("000000000000399_*.xml"))
        arquivo.write_bytes(b"<alterado/>")  # simula alteração indevida
        doc = c.interpreta_resposta(RESP_398).docs[0]
        with self.assertRaises(c.ConflitoBruto):
            c.grava_bruto(self.raiz, CNPJ, doc)
        self.assertEqual(arquivo.read_bytes(), b"<alterado/>")

    def test_pendentes_ciencia_bate_com_os_dados(self):
        c.executa_ciclo(self.repo, CNPJ, fake(RESP_398), self.raiz, "2", T0)
        c.executa_ciclo(self.repo, CNPJ, fake(RESP_448), self.raiz, "2", T0 + timedelta(hours=3))
        docs = c.interpreta_resposta(RESP_398).docs + c.interpreta_resposta(RESP_448).docs
        resnfe = {d.chave for d in docs if d.schema.startswith("resNFe")}
        proc = {d.chave for d in docs if d.schema.startswith("procNFe")}
        esperado = sorted(resnfe - proc)
        self.assertEqual(self.repo.pendentes_ciencia(CNPJ), esperado)

    def test_pendencia_some_quando_chega_procnfe(self):
        # Os exemplos reais têm 1 resNFe e 1 procNFe da mesma chave, então testamos com dados sintéticos
        chave = "29" + "0" * 41 + "1"
        res = c.Documento("000000000000001", "resNFe_v1.01.xsd", b"<resNFe/>", chave, None)
        proc = c.Documento("000000000000002", "procNFe_v4.00.xsd", b"<procNFe/>", chave, None)
        self.repo.insere_documento(CNPJ, res, "x", "s", 1, T0.isoformat())
        self.repo.con.commit()
        self.assertEqual(self.repo.pendentes_ciencia(CNPJ), [chave])
        self.repo.insere_documento(CNPJ, proc, "y", "s", 1, T0.isoformat())
        self.repo.con.commit()
        self.assertEqual(self.repo.pendentes_ciencia(CNPJ), [])

    def test_envelope_respeita_o_xsd_oficial(self):
        import subprocess

        xsd = REPO / "schemas" / "PL_NFeDistDFe_104" / "distDFeInt_v1.01.xsd"
        env = c.monta_envelope(CNPJ, "000000000000448", "2")
        corpo = env.decode().split("<distDFeInt")[1]
        xml = "<distDFeInt" + corpo.split("</distDFeInt>")[0] + "</distDFeInt>"
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
            f.write(xml)
        res = subprocess.run(["xmllint", "--noout", "--schema", str(xsd), f.name], capture_output=True)
        self.assertEqual(res.returncode, 0, res.stderr.decode())


if __name__ == "__main__":
    unittest.main()
