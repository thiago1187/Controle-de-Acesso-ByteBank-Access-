"""Teste do painel em modo demonstração (o que roda no Streamlit Community Cloud).

Abre o painel_nuvem.py sem API em outro processo e sem placa, clica nos botões
da placa simulada e confere que os acessos aparecem na tabela, que um cartão novo
pode ser cadastrado pelo painel e que o comando chega na placa simulada.
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

    def test_placa_simulada_acessos_cadastro_e_comando(self):
        tela = self.abrir_painel()
        self.assertFalse(tela.exception, tela.exception)
        self.assertIn("Modo demonstração", tela.info[0].value)

        # Cartão cadastrado: liberado, com a luz da categoria
        tela = self.clicar(tela, "Cartão VIP")
        self.assertFalse(tela.exception, tela.exception)
        self.assertIn("luz amarela", tela.success[0].value)
        self.assertTrue(any("Luz amarela" in c.value for c in tela.caption), "semáforo deveria mostrar a luz amarela")

        # Cartão novo: negado, e o painel oferece o cadastro
        tela = self.clicar(tela, "Cartão novo")
        self.assertIn("Acesso negado", tela.error[0].value)
        self.assertTrue(any("negado. As 3 luzes" in c.value for c in tela.caption), "semáforo deveria acender as 3 luzes")
        tabela = tela.dataframe[0].value
        uid_novo = tabela["UID"][0]
        self.assertEqual(list(tabela["UID"]), [uid_novo, "01020304"])
        metricas = {m.label: m.value for m in tela.metric}
        self.assertEqual(metricas["Liberados"], "1")
        self.assertEqual(metricas["Negados"], "1")
        self.assertIn(uid_novo, tela.warning[0].value)

        # A pessoa escolhe o que o cartão será e cadastra
        tela.selectbox[0].select("Estágio")
        tela.text_input[0].input("Maria")
        tela = self.clicar(tela, "Cadastrar cartão")
        self.assertFalse(tela.exception, tela.exception)
        self.assertEqual(api.cartoes[uid_novo]["categoria"], "estagio")
        self.assertEqual(api.cartoes[uid_novo]["nome"], "Maria")

        # Na próxima leitura, o cartão cadastrado é liberado e aparece no painel
        acesso = api.app.test_client().post(
            "/acessos", json={"uid": uid_novo, "presenca": True}
        )
        self.assertEqual(acesso.get_json()["resultado"], "liberado")
        tela = self.abrir_painel()
        self.assertFalse(tela.exception, tela.exception)
        tabela = tela.dataframe[0].value
        self.assertEqual(tabela.iloc[0]["UID"], uid_novo)
        self.assertEqual(tabela.iloc[0]["Nome"], "Maria")
        self.assertTrue(tabela.iloc[0]["Data e hora"])
        self.assertTrue(any("Maria (estagio), liberado. Luz verde" in c.value for c in tela.caption))

        # Comando do painel chega na placa simulada e sai da fila
        tela = self.clicar(tela, "Liberar trava")
        self.assertTrue(any("recebeu 'liberar'" in a.value for a in tela.success))
        self.assertEqual(api.comandos_pendentes, [])
        self.assertTrue(any("Trava liberada" in c.value for c in tela.caption), "semáforo deveria mostrar a trava liberada")


if __name__ == "__main__":
    unittest.main()
