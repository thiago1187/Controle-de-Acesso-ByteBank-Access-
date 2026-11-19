"""Regressões do driver com temporização e respostas ISO 14443A simuladas.

Não acessam USB. Cobrem falhas que os mocks de test_main não reproduzem.
"""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ARQUIVO = Path(__file__).resolve().parent.parent / "esp32" / "mfrc522.py"


class Relogio:
    def __init__(self):
        self.agora = 0

    def sleep_ms(self, ms):
        self.agora += ms

    def ticks_ms(self):
        return self.agora

    @staticmethod
    def ticks_diff(a, b):
        return a - b


def carregar_driver():
    relogio = Relogio()
    pinos = []

    class Pin:
        OUT = 1

        def __init__(self, numero, modo=None):
            self.numero = numero
            self.transicoes = []
            pinos.append(self)

        def value(self, valor):
            self.transicoes.append((relogio.agora, valor))

    class SPI:
        def __init__(self, *args, **kwargs):
            self.configuracao = kwargs

    maquina = types.SimpleNamespace(Pin=Pin, SPI=SPI)
    spec = importlib.util.spec_from_file_location("driver_em_teste", ARQUIVO)
    modulo = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"machine": maquina, "time": relogio}), \
            mock.patch("os.uname", return_value=("esp32",)):
        spec.loader.exec_module(modulo)
    return modulo.MFRC522, relogio, pinos


def crc_a(dados):
    """CRC_A de referência em software, independente do coprocessador."""
    crc = 0x6363
    for valor in dados:
        valor ^= crc & 0xFF
        valor ^= (valor << 4) & 0xFF
        crc = ((crc >> 8) ^ (valor << 8) ^ (valor << 3) ^ (valor >> 4)) & 0xFFFF
    return [crc & 0xFF, crc >> 8]


def chip_inicializavel(base, relogio, versao=0x92, atraso_reset=80, travado=False):
    class Chip(base):
        def __init__(self):
            self.regs = {0x37: versao}
            self.pronto_em = None
            self.escritas = []
            super().__init__(18, 23, 19, 22, 5, baudrate=100000)

        def _wreg(self, reg, valor):
            if reg == 0x01 and valor == 0x0F:
                self.pronto_em = relogio.agora + atraso_reset
            elif self.pronto_em is not None and (travado or relogio.agora < self.pronto_em):
                raise AssertionError("configuração escrita antes do fim do reset")
            self.regs[reg] = valor
            self.escritas.append((relogio.agora, reg, valor))

        def _rreg(self, reg):
            if reg == 0x01 and self.pronto_em is not None:
                if travado or relogio.agora < self.pronto_em:
                    return 0x10
                self.pronto_em = None
                self.regs[reg] = 0
            return self.regs.get(reg, 0)

    return Chip()


class TagSimulada:
    def __init__(self, base, uid, falha=None):
        self.uid = list(bytes.fromhex(uid))
        self.falha = falha
        self.nivel = 0
        self.selecionados = []
        self.halts = 0
        resto = self.uid[:]
        self.blocos = []
        while len(resto) > 4:
            bloco = [0x88] + resto[:3]
            resto = resto[3:]
            self.blocos.append(bloco)
        self.blocos.append(resto)
        for bloco in self.blocos:
            bloco.append(bloco[0] ^ bloco[1] ^ bloco[2] ^ bloco[3])
        self.leitor = object.__new__(base)
        self.leitor._rreg = lambda reg: 0
        self.leitor._wreg = lambda reg, valor: None
        self.leitor._crc = crc_a
        self.leitor.request = self.request
        self.leitor._tocard = self.trocar

    def request(self, modo):
        if modo != self.leitor.REQALL:
            raise AssertionError("tag em HALT exige WUPA")
        self.nivel = 0
        return self.leitor.OK, 16

    def trocar(self, comando, dados):
        if comando != 0x0C:
            raise AssertionError("comando inesperado")
        if dados[:2] == [0x50, 0]:
            if self.nivel != len(self.blocos) or dados[2:] != crc_a([0x50, 0]):
                raise AssertionError("HLTA antes da seleção completa ou CRC incorreto")
            self.halts += 1
            return self.leitor.NOTAGERR, [], 0
        seletor = (0x93, 0x95, 0x97)[self.nivel]
        if dados[:2] == [seletor, 0x20]:
            bloco = self.blocos[self.nivel][:]
            if self.falha == "bcc":
                bloco[-1] ^= 1
            return self.leitor.OK, bloco, 40
        if dados[:2] != [seletor, 0x70]:
            raise AssertionError("nível de cascata ou comando incorreto")
        if dados[2:7] != self.blocos[self.nivel] or dados[7:] != crc_a(dados[:7]):
            raise AssertionError("SELECT com BCC/CRC incorreto")
        self.selecionados.append(seletor)
        mais = self.nivel < len(self.blocos) - 1
        sak = 0x04 if mais else 0x08
        if self.falha == "cascata":
            sak ^= 0x04
        resposta = [sak] + crc_a([sak])
        if self.falha == "crc_sak":
            resposta[-1] ^= 1
        self.nivel += 1
        return self.leitor.OK, resposta, 24


class TestMFRC522(unittest.TestCase):
    def setUp(self):
        self.base, self.relogio, self.pinos = carregar_driver()

    def test_chip_ausente_falha_antes_de_anunciar_pronto(self):
        for versao in (0x00, 0xFF):
            with self.subTest(versao=versao), self.assertRaisesRegex(OSError, "sem resposta"):
                chip_inicializavel(self.base, self.relogio, versao=versao)

    def test_recriar_leitor_preserva_mux_do_spi_com_configuracao_identica(self):
        # No ESP32, Pin.OUT retoma o sinal como GPIO. SPI com configuração
        # idêntica reutiliza o barramento, sem reconectar os sinais ao periférico.
        mux_spi = set()

        class Pin:
            OUT = 1

            def __init__(self, numero, modo=None):
                self.numero = numero
                if modo is not None:
                    mux_spi.discard(numero)

            def value(self, valor):
                pass

        class SPI:
            configuracao_anterior = None
            reconexoes = 0

            def __init__(self, bus, *, sck, mosi, miso, **configuracao):
                pinos = (sck.numero, mosi.numero, miso.numero)
                chave = (bus, pinos, tuple(sorted(configuracao.items())))
                if chave != SPI.configuracao_anterior:
                    mux_spi.update(pinos)
                    SPI.reconexoes += 1
                    SPI.configuracao_anterior = chave

        class Chip(self.base):
            def _wreg(self, reg, valor):
                pass

            def _rreg(self, reg):
                if not {18, 23, 19}.issubset(mux_spi):
                    return 0
                return 0x92 if reg == 0x37 else 0

        with mock.patch.dict(self.base.__init__.__globals__, {"Pin": Pin, "SPI": SPI}):
            primeiro = Chip(18, 23, 19, 22, 5, baudrate=100000)
            self.assertEqual(primeiro._rreg(0x37), 0x92)
            segundo = Chip(18, 23, 19, 22, 5, baudrate=100000)
            self.assertEqual(segundo._rreg(0x37), 0x92)
        self.assertEqual(SPI.reconexoes, 1)

    def test_espera_oscilador_e_reset_antes_de_configurar(self):
        chip = chip_inicializavel(self.base, self.relogio)
        rst = next(pin for pin in self.pinos if pin.numero == 22)
        soltou_reset = next(t for t, valor in rst.transicoes if valor == 1)
        reset_software = next(t for t, reg, valor in chip.escritas if reg == 1 and valor == 15)
        configura = next(t for t, reg, _ in chip.escritas if reg == 0x2A)
        self.assertGreaterEqual(reset_software - soltou_reset, 50)
        self.assertGreaterEqual(configura - reset_software, 80)
        self.assertEqual(chip.spi.configuracao["baudrate"], 100000)

    def test_configura_rf_106k_timer_25ms_e_aguarda_campo(self):
        chip = chip_inicializavel(self.base, self.relogio)
        self.assertEqual(chip.regs[0x12], 0)  # TxMode
        self.assertEqual(chip.regs[0x13], 0)  # RxMode
        self.assertEqual(chip.regs[0x24], 0x26)  # ModWidth
        self.assertEqual(chip.regs[0x15], 0x40)  # 100% ASK
        self.assertEqual(chip.regs[0x11], 0x3D)  # CRC_A
        prescaler = ((chip.regs[0x2A] & 0x0F) << 8) | chip.regs[0x2B]
        recarga = (chip.regs[0x2C] << 8) | chip.regs[0x2D]
        timeout_ms = 1000 * recarga * (2 * prescaler + 1) / 13560000
        self.assertEqual(timeout_ms, 25)
        self.assertTrue(chip.regs[0x2A] & 0x80)  # TAuto
        ligou_antena = next(t for t, reg, valor in chip.escritas
                            if reg == 0x14 and valor & 3 == 3)
        self.assertGreaterEqual(self.relogio.agora - ligou_antena, 50)

    def test_reset_travado_tem_timeout(self):
        with self.assertRaisesRegex(OSError, "timeout ao sair do reset"):
            chip_inicializavel(self.base, self.relogio, travado=True)
        self.assertLessEqual(self.relogio.agora, 205)

    def test_crc_travado_nao_devolve_resultado_antigo(self):
        chip = object.__new__(self.base)
        chip._rreg = lambda reg: 0
        chip._wreg = lambda reg, valor: None
        with self.assertRaisesRegex(OSError, "timeout no calculo do CRC"):
            chip._crc([0x50, 0])
        self.assertEqual(self.relogio.agora, 100)

    def test_crc_limpa_irq_anterior_e_aguarda_resultado_novo(self):
        chip = object.__new__(self.base)
        regs = {0x05: 0x04, 0x22: 0xAA, 0x21: 0xBB}
        estado = {"pronto_em": None, "fifo": []}

        def escrever(reg, valor):
            if reg == 0x05:
                # DivIrqReg: Set2=0 limpa somente os bits escritos como 1.
                if valor & 0x80:
                    regs[reg] |= valor & 0x7F
                else:
                    regs[reg] &= ~(valor & 0x7F)
            elif reg == 0x0A and valor & 0x80:
                estado["fifo"] = []
            elif reg == 0x09:
                estado["fifo"].append(valor)
            elif reg == 0x01 and valor == 3:
                estado["pronto_em"] = self.relogio.agora + 2
            else:
                regs[reg] = valor

        def ler(reg):
            pronto = estado["pronto_em"]
            if pronto is not None and self.relogio.agora >= pronto:
                crc = crc_a(estado["fifo"])
                regs.update({0x05: 0x04, 0x22: crc[0], 0x21: crc[1]})
            return regs.get(reg, 0)

        chip._wreg = escrever
        chip._rreg = ler
        self.assertEqual(chip._crc([0x50, 0]), [0x57, 0xCD])
        self.assertGreaterEqual(self.relogio.agora, 2)

    def test_crc_referencia_hlta(self):
        self.assertEqual(crc_a([0x50, 0]), [0x57, 0xCD])

    def test_idle_do_comando_anterior_nao_antecipa_resposta(self):
        chip = object.__new__(self.base)
        estado = {"irq": 0, "inicio": False, "polls": 0, "framing": 0}
        resposta = [1, 2, 3, 4, 4]

        def escrever(reg, valor):
            if reg == 0x01 and valor == 0:
                # Interromper CalcCRC/Transceive anterior pode gerar IdleIRq.
                estado["irq"] |= 0x10
            elif reg == 0x04:
                estado["irq"] &= ~(valor & 0x7F)
            elif reg == 0x0D:
                estado["framing"] = valor
                if valor & 0x80:
                    estado["inicio"] = True

        def ler(reg):
            if reg == 0x04:
                if estado["inicio"]:
                    estado["polls"] += 1
                    if estado["polls"] >= 2:
                        estado["irq"] |= 0x30
                return estado["irq"]
            if reg == 0x0A:
                return len(resposta) if estado["polls"] >= 2 else 0
            if reg == 0x0D:
                return estado["framing"]
            return 0

        def ler_spi(enviado, recebido):
            recebido[:] = bytes([0] + resposta[:len(recebido) - 1])

        chip._rreg = ler
        chip._wreg = escrever
        chip.cs = types.SimpleNamespace(value=lambda v: None)
        chip.spi = types.SimpleNamespace(write_readinto=ler_spi)
        self.assertEqual(chip._tocard(0x0C, [0x93, 0x20]), (chip.OK, resposta, 40))
        self.assertGreaterEqual(estado["polls"], 2)

    def test_ler_uid_4_7_e_10_bytes_completo(self):
        for uid, niveis in [("deadbeef", [0x93]),
                            ("04112233445566", [0x93, 0x95]),
                            ("04112233445566778899", [0x93, 0x95, 0x97])]:
            with self.subTest(uid=uid):
                tag = TagSimulada(self.base, uid)
                self.assertEqual(bytes(tag.leitor.read_uid()).hex(), uid)
                self.assertEqual(tag.selecionados, niveis)
                self.assertEqual(tag.halts, 1)

    def test_uids_com_mesmo_cl1_permanecem_distintos(self):
        uids = ["04112233445566", "041122aabbccdd"]
        tags = [TagSimulada(self.base, uid) for uid in uids]
        self.assertEqual(tags[0].blocos[0], tags[1].blocos[0])
        self.assertEqual([bytes(t.leitor.read_uid()).hex() for t in tags], uids)

    def test_mesma_tag_pode_ser_lida_depois_do_halt(self):
        tag = TagSimulada(self.base, "deadbeef")
        self.assertEqual(tag.leitor.read_uid(), tag.uid)
        self.assertEqual(tag.leitor.read_uid(), tag.uid)
        self.assertEqual(tag.halts, 2)

    def test_rejeita_bcc_crc_e_cascata_inconsistente(self):
        for falha in ("bcc", "crc_sak", "cascata"):
            with self.subTest(falha=falha):
                tag = TagSimulada(self.base, "04112233445566", falha=falha)
                self.assertIsNone(tag.leitor.read_uid())
                self.assertEqual(tag.halts, 0)

    def test_sem_atqa_nao_inventa_uid(self):
        tag = TagSimulada(self.base, "deadbeef")
        tag.leitor.request = lambda modo: (self.base.NOTAGERR, 0)
        self.assertIsNone(tag.leitor.read_uid())
        self.assertFalse(tag.selecionados)


if __name__ == "__main__":
    unittest.main()
