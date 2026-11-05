"""Testes do esp32/estruturas.py (Ring Buffer e Tabela Hash).

Para rodar, na raiz do projeto:
    python -m unittest discover tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "esp32"))

from estruturas import RingBuffer, TabelaCartoes  # noqa: E402


class TestRingBuffer(unittest.TestCase):
    def test_comeca_vazio(self):
        fila = RingBuffer(20)
        self.assertEqual(len(fila), 0)
        self.assertEqual(fila.listar(), [])

    def test_guarda_na_ordem_de_chegada(self):
        fila = RingBuffer(20)
        for numero in range(5):
            fila.adicionar(numero)
        self.assertEqual(fila.listar(), [0, 1, 2, 3, 4])
        self.assertEqual(len(fila), 5)

    def test_exatamente_cheio_nao_perde_nada(self):
        fila = RingBuffer(20)
        for numero in range(20):
            fila.adicionar(numero)
        self.assertEqual(fila.listar(), list(range(20)))

    def test_item_21_sobrescreve_o_mais_antigo(self):
        fila = RingBuffer(20)
        for numero in range(21):
            fila.adicionar(numero)
        self.assertEqual(len(fila), 20)
        self.assertEqual(fila.listar(), list(range(1, 21)))

    def test_da_varias_voltas_e_mantem_os_20_ultimos(self):
        fila = RingBuffer(20)
        for numero in range(75):
            fila.adicionar(numero)
        self.assertEqual(fila.listar(), list(range(55, 75)))

    def test_capacidade_pequena(self):
        fila = RingBuffer(3)
        for letra in "abcde":
            fila.adicionar(letra)
        self.assertEqual(fila.listar(), ["c", "d", "e"])


class TestTabelaCartoes(unittest.TestCase):
    def test_acha_cartao_cadastrado(self):
        tabela = TabelaCartoes()
        tabela.cadastrar("aabbccdd", "Ana", "vip")
        self.assertEqual(tabela.buscar("aabbccdd"), ("Ana", "vip"))

    def test_cartao_desconhecido_devolve_none(self):
        tabela = TabelaCartoes()
        self.assertIsNone(tabela.buscar("00000000"))

    def test_cadastrar_de_novo_atualiza(self):
        tabela = TabelaCartoes()
        tabela.cadastrar("aabbccdd", "Ana", "visita")
        tabela.cadastrar("aabbccdd", "Ana", "vip")
        self.assertEqual(tabela.buscar("aabbccdd"), ("Ana", "vip"))


if __name__ == "__main__":
    unittest.main()
