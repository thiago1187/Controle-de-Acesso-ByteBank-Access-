"""Testes do fluxo do esp32/main.py com placa, Wi-Fi e servidor simulados.

Os módulos machine, network, urequests, mfrc522 e time do MicroPython são
trocados por versões falsas. Isso testa a lógica (qual luz acende, o que vai
no JSON, o que acontece sem Wi-Fi), mas NÃO substitui o teste na placa.

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

RAIZ = Path(__file__).resolve().parent.parent / "esp32"


class PinFalso:
    IN, OUT, PULL_DOWN = 0, 1, 2

    def __init__(self, numero, modo=None, pull=None):
        self.numero = numero
        self.valor = 0
        self.historico = []  # tudo que foi escrito, para conferir depois

    def value(self, novo=None):
        if novo is None:
            return self.valor
        self.valor = novo
        self.historico.append(novo)


class LeitorFalso:
    """Devolve um UID por ciclo. Quando a lista acaba, simula Ctrl+C."""

    OK, NOTAGERR, ERR = 0, 1, 2
    REQIDL = 0x26
    roteiro = []

    def __init__(self, *pinos):
        self.fila = list(self.roteiro)
        self.atual = None

    def request(self, modo):
        if not self.fila:
            raise KeyboardInterrupt
        self.atual = self.fila.pop(0)
        if self.atual is None:
            return self.NOTAGERR, 0
        if self.atual == "erro":
            raise OSError("SPI falhou")
        return self.OK, 0x10

    def anticoll(self):
        uid = bytes.fromhex(self.atual)
        return self.OK, list(uid) + [0]


class RespostaFalsa:
    def __init__(self, status, dados=None):
        self.status_code = status
        self.dados = dados or {}

    def json(self):
        return self.dados

    def close(self):
        pass


# Cartões que a API "conhece" nos testes
CATEGORIAS_API = {"11111111": "vip", "22222222": "estagio", "33333333": "visita"}


def rodar_main(roteiro, wifi=True, status_post=201, erro_post=None, pir=0, comandos=None):
    """Executa o main.py com tudo simulado. Devolve (módulo, saída, chamadas de post)."""
    LeitorFalso.roteiro = roteiro
    chamadas = []
    fila_comandos = list(comandos or [])

    def post(url, json=None):
        chamadas.append((url, json))
        if erro_post:
            raise erro_post
        categoria = CATEGORIAS_API.get(json["uid"])
        resposta = {
            "uid": json["uid"],
            "resultado": "liberado" if categoria else "negado",
            "categoria": categoria,
            "nome": "Pessoa " + categoria if categoria else None,
        }
        return RespostaFalsa(status_post, resposta)

    def get(url):
        comando = fila_comandos.pop(0) if fila_comandos else None
        return RespostaFalsa(200, {"comando": comando})

    relogio = {"t": 0}
    tempo = types.ModuleType("time")
    tempo.sleep = lambda s: relogio.__setitem__("t", relogio["t"] + int(s * 1000))
    tempo.sleep_ms = lambda ms: relogio.__setitem__("t", relogio["t"] + ms)
    tempo.ticks_ms = lambda: relogio["t"]
    tempo.ticks_diff = lambda a, b: a - b

    maquina = types.ModuleType("machine")
    maquina.Pin = PinFalso

    rede = types.ModuleType("network")
    rede.STA_IF = 0
    rede.WLAN = lambda i: types.SimpleNamespace(isconnected=lambda: wifi)

    requisicoes = types.ModuleType("urequests")
    requisicoes.post = post
    requisicoes.get = get

    rfid = types.ModuleType("mfrc522")
    rfid.MFRC522 = LeitorFalso

    modulos = {"time": tempo, "machine": maquina, "network": rede,
               "urequests": requisicoes, "mfrc522": rfid}

    saida = io.StringIO()
    with mock.patch.dict(sys.modules, modulos):
        sys.path.insert(0, str(RAIZ))
        try:
            sys.modules.pop("estruturas", None)
            spec = importlib.util.spec_from_file_location("main_sob_teste", RAIZ / "main.py")
            modulo = importlib.util.module_from_spec(spec)
            # o PIR é criado dentro de rodar(); fixamos o valor dele por Pin
            original = PinFalso.value

            def value_com_pir(self, novo=None):
                if novo is None and self.numero == 33:
                    return pir
                return original(self, novo)

            with mock.patch.object(PinFalso, "value", value_com_pir):
                with redirect_stdout(saida):
                    spec.loader.exec_module(modulo)
        finally:
            sys.path.remove(str(RAIZ))
            sys.modules.pop("estruturas", None)
    return modulo, saida.getvalue(), chamadas


class TestMain(unittest.TestCase):
    def test_cada_categoria_acende_a_luz_certa(self):
        casos = [("11111111", "vip", 26), ("22222222", "estagio", 25), ("33333333", "visita", 27)]
        for uid, categoria, pino in casos:
            with self.subTest(categoria=categoria):
                modulo, texto, _ = rodar_main([uid])
                self.assertEqual(modulo.LEDS[categoria].numero, pino)
                self.assertIn(1, modulo.LEDS[categoria].historico)
                self.assertEqual(modulo.LEDS[categoria].valor, 0)
                for outra, led in modulo.LEDS.items():
                    if outra != categoria:
                        self.assertNotIn(1, led.historico)
                self.assertIn("Acesso liberado", texto)

    def test_cartao_negado_pela_api_pisca_as_tres(self):
        modulo, texto, _ = rodar_main(["deadbeef"])
        for led in modulo.LEDS.values():
            self.assertEqual(led.historico.count(1), 3)
            self.assertEqual(led.valor, 0)
        self.assertIn("deadbeef", texto)

    def test_json_enviado_tem_uid_e_presenca(self):
        _, _, chamadas = rodar_main(["11111111"])
        url, json = chamadas[0]
        self.assertTrue(url.endswith("/acessos"))
        self.assertEqual(json, {"uid": "11111111", "presenca": False})

    def test_presenca_do_pir_vai_no_json(self):
        _, texto, chamadas = rodar_main(["22222222"], pir=1)
        self.assertTrue(chamadas[0][1]["presenca"])
        self.assertIn("Presença detectada", texto)

    def test_cartao_liberado_entra_na_tabela_hash_local(self):
        modulo, _, _ = rodar_main(["11111111"])
        self.assertEqual(modulo.cartoes.buscar("11111111")[1], "vip")
        self.assertIsNone(modulo.cartoes.buscar("deadbeef"))

    def test_sem_wifi_usa_hash_local_e_nega_desconhecido(self):
        modulo, texto, chamadas = rodar_main(["11111111"], wifi=False)
        self.assertEqual(chamadas, [])
        self.assertIn("Sem Wi-Fi", texto)
        acessos = modulo.ultimos_acessos.listar()
        self.assertEqual(acessos[0]["resultado"], "negado")
        self.assertEqual(acessos[0]["origem"], "local")

    def test_api_fora_do_ar_usa_cartao_ja_conhecido(self):
        # Primeiro com API (cartão entra na hash). Depois a API cai e o cartão
        # continua liberado pela hash local.
        modulo, _, _ = rodar_main(["11111111"])
        cache = dict(modulo.cartoes.cartoes)
        modulo2, texto, _ = rodar_main(["11111111"], erro_post=OSError("timeout"))
        modulo2.cartoes.cartoes.update(cache)
        modulo2.tratar_cartao("11111111", False)
        self.assertEqual(modulo2.ultimos_acessos.listar()[-1]["resultado"], "liberado")
        self.assertIn("Falha ao enviar", texto)

    def test_servidor_com_erro_nao_derruba_o_programa(self):
        modulo, _, _ = rodar_main(["11111111", "22222222"], status_post=503)
        self.assertEqual(len(modulo.ultimos_acessos.listar()), 2)

    def test_mesmo_cartao_seguido_conta_uma_vez_so(self):
        _, _, chamadas = rodar_main(["11111111", "11111111", "11111111"])
        self.assertEqual(len(chamadas), 1)

    def test_erro_de_spi_no_leitor_nao_derruba(self):
        _, texto, chamadas = rodar_main(["erro", "11111111"])
        self.assertIn("Erro de leitura no RFID", texto)
        self.assertEqual(len(chamadas), 1)

    def test_ring_buffer_guarda_so_os_ultimos_20(self):
        uids = ["%08x" % (0x100 + n) for n in range(25)]
        modulo, _, _ = rodar_main(uids)
        acessos = modulo.ultimos_acessos.listar()
        self.assertEqual(len(acessos), 20)
        self.assertEqual(acessos[0]["uid"], uids[5])
        self.assertEqual(acessos[-1]["uid"], uids[24])

    def test_comando_liberar_acende_verde(self):
        modulo, texto, _ = rodar_main([None], comandos=["liberar"])
        self.assertIn("Comando recebido: liberar", texto)
        self.assertIn(1, modulo.LEDS["estagio"].historico)

    def test_comando_alarme_pisca_as_tres(self):
        modulo, _, _ = rodar_main([None], comandos=["alarme"])
        for led in modulo.LEDS.values():
            self.assertEqual(led.historico.count(1), 6)

    def test_comando_resetar_zera_o_ring_buffer(self):
        # 1ª volta: busca comando (nenhum) e lê o cartão. 20 voltas depois (2 s) chega o "resetar".
        roteiro = ["11111111"] + [None] * 25
        modulo, texto, _ = rodar_main(roteiro, comandos=[None, "resetar"])
        self.assertIn("Ring Buffer zerado", texto)
        self.assertEqual(len(modulo.ultimos_acessos), 0)

if __name__ == "__main__":
    unittest.main()
