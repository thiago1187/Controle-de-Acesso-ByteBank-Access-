"""Testes do esp32/boot.py rodando no computador (Python 3 comum).

A placa não existe aqui, então o módulo `network` do MicroPython é trocado por
uma versão falsa. Isso testa a lógica do boot.py (tentativas, tempo limite,
erros), mas NÃO substitui o teste na placa de verdade.

Para rodar, na raiz do projeto:
    python -m unittest discover tests -v
"""

import importlib.util
import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

CAMINHO_BOOT = Path(__file__).resolve().parent.parent / "esp32" / "boot.py"
SENHA = "senha-secreta-123"


class WLANFalso:
    """Imita network.WLAN. `conecta_apos` é o número de consultas a
    isconnected() até a rede "conectar". None significa que nunca conecta."""

    def __init__(self, conecta_apos=None, ja_conectado=False, erro_no_connect=False):
        self.conecta_apos = conecta_apos
        self.ja_conectado = ja_conectado
        self.erro_no_connect = erro_no_connect
        self.chamadas_connect = 0
        self.chamadas_disconnect = 0
        self.consultas = 0
        self.tentando = False

    def active(self, estado=None):
        return True

    def isconnected(self):
        if self.ja_conectado:
            return True
        if not self.tentando:
            return False
        self.consultas += 1
        return self.conecta_apos is not None and self.consultas >= self.conecta_apos

    def connect(self, ssid, senha):
        self.chamadas_connect += 1
        if self.erro_no_connect:
            raise OSError("Wifi Internal Error")
        self.tentando = True
        self.consultas = 0

    def disconnect(self):
        self.chamadas_disconnect += 1
        self.tentando = False

    def ifconfig(self):
        return ("192.168.0.50", "255.255.255.0", "192.168.0.1", "8.8.8.8")

    def status(self):
        return 201  # código qualquer; só para aparecer na mensagem


def carregar_boot(wlan, com_config=True):
    """Executa o boot.py com `network`, `config_wifi` e `time.sleep` falsos.
    Devolve (módulo, texto impresso, lista de esperas pedidas ao sleep)."""
    rede = types.ModuleType("network")
    rede.STA_IF = 0
    rede.WLAN = lambda interface: wlan

    modulos = {"network": rede}
    if com_config:
        config = types.ModuleType("config_wifi")
        config.WIFI_SSID = "RedeTeste"
        config.WIFI_SENHA = SENHA
        modulos["config_wifi"] = config

    esperas = []
    saida = io.StringIO()
    spec = importlib.util.spec_from_file_location("boot_sob_teste", CAMINHO_BOOT)
    modulo = importlib.util.module_from_spec(spec)

    with mock.patch.dict(sys.modules, modulos):
        if not com_config:
            sys.modules.pop("config_wifi", None)
        with mock.patch("time.sleep", side_effect=esperas.append):
            with redirect_stdout(saida):
                spec.loader.exec_module(modulo)
    return modulo, saida.getvalue(), esperas


class TestBoot(unittest.TestCase):
    def test_conecta_na_primeira_tentativa(self):
        wlan = WLANFalso(conecta_apos=3)
        _, texto, _ = carregar_boot(wlan)
        self.assertEqual(wlan.chamadas_connect, 1)
        self.assertEqual(wlan.chamadas_disconnect, 0)
        self.assertIn("Wi-Fi conectado! IP: 192.168.0.50", texto)

    def test_ja_conectado_nao_chama_connect_de_novo(self):
        wlan = WLANFalso(ja_conectado=True)
        _, texto, _ = carregar_boot(wlan)
        self.assertEqual(wlan.chamadas_connect, 0)
        self.assertIn("já estava conectado", texto)

    def test_rede_ou_senha_errada_tenta_3_vezes_e_desiste(self):
        wlan = WLANFalso(conecta_apos=None)
        modulo, texto, esperas = carregar_boot(wlan)
        self.assertEqual(wlan.chamadas_connect, 3)
        self.assertEqual(wlan.chamadas_disconnect, 3)
        # 3 tentativas x 15 esperas de 1 segundo
        self.assertEqual(sum(esperas), 45)
        self.assertIn("Não foi possível conectar ao Wi-Fi", texto)
        self.assertIn("2,4 GHz", texto)
        # Chamando de novo o resultado também é False (a função não quebra)
        wlan2 = WLANFalso(conecta_apos=None)
        modulo.wlan = wlan2
        with mock.patch("time.sleep"), redirect_stdout(io.StringIO()):
            self.assertFalse(modulo.conectar_wifi())

    def test_falha_de_hardware_nao_derruba_o_boot(self):
        wlan = WLANFalso(erro_no_connect=True)
        _, texto, _ = carregar_boot(wlan)  # não pode levantar exceção
        self.assertEqual(wlan.chamadas_connect, 3)
        self.assertIn("Erro de hardware no Wi-Fi", texto)

    def test_sem_config_wifi_avisa_e_nao_tenta_conectar(self):
        wlan = WLANFalso(conecta_apos=1)
        _, texto, _ = carregar_boot(wlan, com_config=False)
        self.assertEqual(wlan.chamadas_connect, 0)
        self.assertIn("config_wifi.py não encontrado", texto)

    def test_senha_nunca_aparece_na_saida(self):
        for wlan in (WLANFalso(conecta_apos=1), WLANFalso(conecta_apos=None),
                     WLANFalso(erro_no_connect=True)):
            _, texto, _ = carregar_boot(wlan)
            self.assertNotIn(SENHA, texto)

    def test_conecta_so_na_segunda_tentativa(self):
        # Primeira tentativa estoura o tempo; na segunda a rede responde
        class WLANSegunda(WLANFalso):
            def connect(self, ssid, senha):
                super().connect(ssid, senha)
                if self.chamadas_connect == 2:
                    self.conecta_apos = 2
                else:
                    self.conecta_apos = None

        wlan = WLANSegunda()
        _, texto, _ = carregar_boot(wlan)
        self.assertEqual(wlan.chamadas_connect, 2)
        self.assertEqual(wlan.chamadas_disconnect, 1)
        self.assertIn("Wi-Fi conectado!", texto)


if __name__ == "__main__":
    unittest.main()
