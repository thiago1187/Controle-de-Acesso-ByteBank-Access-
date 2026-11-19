"""Teste de ponta a ponta (End-to-End): ESP32 -> API -> banco -> dashboard -> ESP32.

Sobe a API de verdade em uma porta local, faz o papel do ESP32 com requests
e abre o dashboard com o AppTest do Streamlit (sem navegador).
Precisa de: flask, streamlit, pandas, requests.
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import requests
from werkzeug.serving import make_server

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "api"))

BANCO = os.path.join(tempfile.mkdtemp(), "e2e.db")
os.environ["BYTEBANK_DB"] = BANCO

import app as api  # noqa: E402

PORTA = 5055
URL = "http://127.0.0.1:%d" % PORTA


class TestPontaAPonta(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        api.CAMINHO_BANCO = BANCO
        api.iniciar_banco()
        api.comandos_pendentes.clear()
        cls.servidor = make_server("127.0.0.1", PORTA, api.app)
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        os.environ["BYTEBANK_API"] = URL

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()

    def abrir_dashboard(self):
        from streamlit.testing.v1 import AppTest
        tela = AppTest.from_file(str(RAIZ / "dashboard" / "painel.py"), default_timeout=20)
        return tela.run()

    def test_fluxo_completo(self):
        # 1. ESP32 manda dois cartões: um cadastrado e um desconhecido
        r1 = requests.post(URL + "/acessos", json={"uid": "01020304", "presenca": True}, timeout=3)
        r2 = requests.post(URL + "/acessos", json={"uid": "deadbeef", "presenca": False}, timeout=3)
        self.assertEqual(r1.json()["resultado"], "liberado")
        self.assertEqual(r1.json()["categoria"], "vip")
        self.assertEqual(r2.json()["resultado"], "negado")

        # 2. Dashboard lê do banco e mostra os dois acessos
        tela = self.abrir_dashboard()
        self.assertFalse(tela.exception, tela.exception)
        tabela = tela.dataframe[0].value
        self.assertEqual(list(tabela["uid"]), ["deadbeef", "01020304"])
        metricas = {m.label: m.value for m in tela.metric}
        self.assertEqual(metricas["Liberados"], "1")
        self.assertEqual(metricas["Negados"], "1")

        # 3. Clique em "Liberar trava" no dashboard
        botao = [b for b in tela.button if b.label == "Liberar trava"][0]
        tela = botao.click().run()
        self.assertTrue(tela.success, "o dashboard deveria confirmar o envio")

        # 4. ESP32 busca o comando, e ele sai da fila
        proximo = requests.get(URL + "/comandos/proximo", timeout=3).json()
        self.assertEqual(proximo["comando"], "liberar")
        vazio = requests.get(URL + "/comandos/proximo", timeout=3).json()
        self.assertIsNone(vazio["comando"])

    def test_varios_acessos_seguidos_sem_travar(self):
        for n in range(30):
            r = requests.post(URL + "/acessos", json={"uid": "%08x" % n}, timeout=3)
            self.assertEqual(r.status_code, 201)
        ultimos = requests.get(URL + "/acessos?limite=5", timeout=3).json()
        self.assertEqual(len(ultimos), 5)
        self.assertEqual(ultimos[0]["uid"], "%08x" % 29)


if __name__ == "__main__":
    unittest.main()
