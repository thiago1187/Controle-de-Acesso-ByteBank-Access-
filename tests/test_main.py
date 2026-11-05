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
    IN, OUT = 0, 1

    def __init__(self, numero, modo=None):
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
    def __init__(self, status):
        self.status_code = status

    def close(self):
        pass


def rodar_main(roteiro, wifi=True, status_post=200, erro_post=None, pir=0):
    """Executa o main.py com tudo simulado. Devolve (módulo, saída, chamadas de post)."""
    LeitorFalso.roteiro = roteiro
    chamadas = []

    def post(url, json=None):
        chamadas.append((url, json))
        if erro_post:
            raise erro_post
        return RespostaFalsa(status_post)

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
                if novo is None and self.numero == 27:
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
        casos = [("11111111", "vip", 25), ("22222222", "estagio", 26), ("33333333", "visita", 33)]
        for uid, categoria, pino in casos:
            with self.subTest(categoria=categoria):
                modulo, texto, _ = rodar_main([uid])
                self.assertEqual(modulo.LEDS[categoria].numero, pino)
                # acendeu (1) e depois apagou (0)
                self.assertIn(1, modulo.LEDS[categoria].historico)
                self.assertEqual(modulo.LEDS[categoria].valor, 0)
                # as outras duas nunca acenderam
                for outra, led in modulo.LEDS.items():
                    if outra != categoria:
                        self.assertNotIn(1, led.historico)
                self.assertIn("Acesso liberado", texto)

    def test_cartao_desconhecido_pisca_as_tres_e_avisa_o_uid(self):
        modulo, texto, chamadas = rodar_main(["deadbeef"])
        for led in modulo.LEDS.values():
            self.assertEqual(led.historico.count(1), 3)
            self.assertEqual(led.valor, 0)
        self.assertIn("deadbeef", texto)
        self.assertEqual(chamadas[0][1]["resultado"], "negado")

    def test_json_enviado_tem_os_campos_combinados(self):
        _, _, chamadas = rodar_main(["11111111"])
        url, json = chamadas[0]
        self.assertEqual(url, "http://httpbin.org/post")
        self.assertEqual(set(json), {"uid", "nome", "categoria", "resultado", "presenca", "uptime_ms"})
        self.assertEqual(json["categoria"], "vip")
        self.assertEqual(json["resultado"], "liberado")
        self.assertFalse(json["presenca"])

    def test_presenca_do_pir_vai_no_json(self):
        _, texto, chamadas = rodar_main(["22222222"], pir=1)
        self.assertTrue(chamadas[0][1]["presenca"])
        self.assertIn("Presença detectada", texto)

    def test_sem_wifi_nao_envia_mas_guarda_no_ring_buffer(self):
        modulo, texto, chamadas = rodar_main(["11111111"], wifi=False)
        self.assertEqual(chamadas, [])
        self.assertIn("Sem Wi-Fi", texto)
        acessos = modulo.ultimos_acessos.listar()
        self.assertEqual(len(acessos), 1)
        self.assertFalse(acessos[0]["enviado"])

    def test_servidor_com_erro_nao_derruba_o_programa(self):
        modulo, texto, _ = rodar_main(["11111111", "22222222"], status_post=503)
        acessos = modulo.ultimos_acessos.listar()
        self.assertEqual(len(acessos), 2)
        self.assertFalse(any(a["enviado"] for a in acessos))

    def test_falha_de_rede_no_post_e_tratada(self):
        modulo, texto, _ = rodar_main(["11111111"], erro_post=OSError("timeout"))
        self.assertIn("Falha ao enviar", texto)
        self.assertEqual(len(modulo.ultimos_acessos.listar()), 1)

    def test_post_com_sucesso_marca_como_enviado(self):
        modulo, _, _ = rodar_main(["11111111"])
        self.assertTrue(modulo.ultimos_acessos.listar()[0]["enviado"])

    def test_mesmo_cartao_seguido_conta_uma_vez_so(self):
        # 3 leituras seguidas do mesmo cartão em menos de 2 s viram 1 acesso
        modulo, _, chamadas = rodar_main(["11111111", "11111111", "11111111"])
        self.assertEqual(len(chamadas), 1)

    def test_erro_de_spi_no_leitor_nao_derruba(self):
        modulo, texto, chamadas = rodar_main(["erro", "11111111"])
        self.assertIn("Erro de leitura no RFID", texto)
        self.assertEqual(len(chamadas), 1)

    def test_ring_buffer_guarda_so_os_ultimos_20(self):
        # 25 acessos de cartões diferentes (todos desconhecidos): sobram 20
        uids = ["%08x" % (0x100 + n) for n in range(25)]
        modulo, _, _ = rodar_main(uids)
        acessos = modulo.ultimos_acessos.listar()
        self.assertEqual(len(acessos), 20)
        self.assertEqual(acessos[0]["uid"], uids[5])
        self.assertEqual(acessos[-1]["uid"], uids[24])


if __name__ == "__main__":
    unittest.main()
