"""Testes da API (Flask + SQLite). Usam um banco temporário, apagado no fim."""

import os
import sys
import tempfile
import unittest

PASTA_API = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api")
sys.path.insert(0, PASTA_API)

TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
TMP.close()
os.environ["BYTEBANK_DB"] = TMP.name

import app as api  # noqa: E402


class TestAPI(unittest.TestCase):

    def setUp(self):
        if os.path.exists(TMP.name):
            os.remove(TMP.name)
        api.iniciar_banco()
        self.cliente = api.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TMP.name):
            os.remove(TMP.name)

    def test_hash_carregada_com_usuarios_iniciais(self):
        self.assertEqual(len(api.cartoes), 3)
        self.assertEqual(api.cartoes["01020304"]["categoria"], "vip")

    def test_acesso_liberado_grava_log(self):
        r = self.cliente.post("/acessos", json={"uid": "01020304", "presenca": True})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["resultado"], "liberado")
        self.assertEqual(r.get_json()["categoria"], "vip")

        logs = self.cliente.get("/acessos").get_json()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["uid"], "01020304")
        self.assertEqual(logs[0]["presenca"], 1)
        self.assertTrue(logs[0]["timestamp"])

    def test_cartao_desconhecido_e_negado_mas_fica_no_log(self):
        r = self.cliente.post("/acessos", json={"uid": "deadbeef"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["resultado"], "negado")
        logs = self.cliente.get("/acessos").get_json()
        self.assertEqual(logs[0]["resultado"], "negado")
        self.assertIsNone(logs[0]["nome"])

    def test_uid_em_maiusculo_e_normalizado(self):
        r = self.cliente.post("/acessos", json={"uid": "01020304".upper()})
        self.assertEqual(r.get_json()["resultado"], "liberado")

    def test_json_sem_uid_da_400(self):
        self.assertEqual(self.cliente.post("/acessos", json={"nome": "x"}).status_code, 400)
        self.assertEqual(self.cliente.post("/acessos", data="nao e json").status_code, 400)

    def test_cadastrar_cartao_entra_na_hash(self):
        r = self.cliente.post("/usuarios", json={"uid": "AABBCCDD", "nome": "Novo", "categoria": "estagio"})
        self.assertEqual(r.status_code, 201)
        self.assertIn("aabbccdd", api.cartoes)
        r = self.cliente.post("/acessos", json={"uid": "aabbccdd"})
        self.assertEqual(r.get_json()["resultado"], "liberado")

    def test_cadastro_repetido_da_409(self):
        r = self.cliente.post("/usuarios", json={"uid": "01020304", "nome": "x", "categoria": "vip"})
        self.assertEqual(r.status_code, 409)

    def test_cadastro_com_categoria_invalida_da_400(self):
        r = self.cliente.post("/usuarios", json={"uid": "1", "nome": "x", "categoria": "chefe"})
        self.assertEqual(r.status_code, 400)

    def test_deposito_e_saque_atualizam_saldo(self):
        self.cliente.post("/transacoes", json={"uid": "01020304", "tipo": "deposito", "valor": 100})
        r = self.cliente.post("/transacoes", json={"uid": "01020304", "tipo": "saque", "valor": 30})
        self.assertEqual(r.get_json()["saldo"], 70)
        self.assertEqual(self.cliente.get("/saldos/01020304").get_json()["saldo"], 70)

    def test_saque_maior_que_saldo_nao_grava_nada(self):
        r = self.cliente.post("/transacoes", json={"uid": "11223344", "tipo": "saque", "valor": 50})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.cliente.get("/saldos/11223344").get_json()["saldo"], 0)
        with api.banco() as con:
            n = con.execute("SELECT COUNT(*) FROM transacoes").fetchone()[0]
        self.assertEqual(n, 0)

    def test_transacao_de_cartao_desconhecido_da_404(self):
        r = self.cliente.post("/transacoes", json={"uid": "zzz", "tipo": "deposito", "valor": 1})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
