"""Teste do painel em modo demonstração (o que roda no Streamlit Community Cloud).

Abre o painel_nuvem.py sem API em outro processo e sem placa, clica nos botões
da placa simulada e confere que os acessos aparecem na tabela e que o comando
chega na placa simulada.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import streamlit as st

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "api"))

import app as api  # noqa: E402


class TestPainelDemo(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ambiente = {nome: os.environ.get(nome) for nome in ("BYTEBANK_DB", "BYTEBANK_DEMO")}
        cls.banco_api = api.CAMINHO_BANCO
        banco = os.path.join(tempfile.mkdtemp(), "demo.db")
        os.environ["BYTEBANK_DB"] = banco
        api.CAMINHO_BANCO = banco
        api.comandos_pendentes.clear()
        st.cache_resource.clear()

    @classmethod
    def tearDownClass(cls):
        for nome, valor in cls.ambiente.items():
            if valor is None:
                os.environ.pop(nome, None)
            else:
                os.environ[nome] = valor
        api.CAMINHO_BANCO = cls.banco_api
        st.cache_resource.clear()

    def abrir_painel(self):
        from streamlit.testing.v1 import AppTest
        tela = AppTest.from_file(str(RAIZ / "dashboard" / "painel_nuvem.py"), default_timeout=20)
        return tela.run()

    def clicar(self, tela, texto):
        botao = [b for b in tela.button if b.label == texto][0]
        return botao.click().run()

    def test_placa_simulada_gera_acessos_e_recebe_comando(self):
        tela = self.abrir_painel()
        self.assertFalse(tela.exception, tela.exception)
        self.assertIn("Modo demonstração", tela.info[0].value)

        # Cartão cadastrado: liberado, com a luz da categoria
        tela = self.clicar(tela, "Cartão VIP")
        self.assertFalse(tela.exception, tela.exception)
        self.assertIn("luz amarela", tela.success[0].value)

        # Cartão sem cadastro: negado
        tela = self.clicar(tela, "Cartão sem cadastro")
        self.assertIn("Acesso negado", tela.error[0].value)

        # Os dois acessos aparecem na tabela, o mais novo primeiro
        tabela = tela.dataframe[0].value
        self.assertEqual(list(tabela["uid"]), ["deadbeef", "01020304"])
        metricas = {m.label: m.value for m in tela.metric}
        self.assertEqual(metricas["Liberados"], "1")
        self.assertEqual(metricas["Negados"], "1")

        # Comando do painel chega na placa simulada e sai da fila
        tela = self.clicar(tela, "Liberar trava")
        self.assertIn("recebeu 'liberar'", tela.success[0].value)
        self.assertEqual(api.comandos_pendentes, [])


if __name__ == "__main__":
    unittest.main()
