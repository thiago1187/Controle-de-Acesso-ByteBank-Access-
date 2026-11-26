"""Protocolo e recuperação USB com subprocessos locais, sem acessar hardware."""

import importlib.util
import json
from pathlib import Path
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch


ARQUIVO = Path(__file__).resolve().parent.parent / "dashboard" / "monitor_nfc.py"


def carregar():
    spec = importlib.util.spec_from_file_location("monitor_nfc_sob_teste", ARQUIVO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def protocolo(**dados):
    return "@NFC " + json.dumps(dados)


class TestEstadoMonitor(unittest.TestCase):
    def setUp(self):
        self.modulo = carregar()
        self.estado = self.modulo.Estado()

    def test_uid_de_quatro_sete_e_dez_bytes(self):
        for uid in ("A1B2C3D4", "04112233445566", "04112233445566778899"):
            with self.subTest(uid=uid):
                self.estado.atualizar_linha(protocolo(type="tag", uid=uid))
                self.assertEqual(self.estado.snapshot()["last_uid"], uid.lower())
        self.assertEqual(self.estado.snapshot()["detections"], 3)

    def test_uid_com_tamanho_invalido_nao_conta_deteccao(self):
        for uid in ("", "abcdef", "a" * 10, "a" * 12, "a" * 16, "a" * 18,
                    "a" * 22, "z" * 8, None, 1234):
            self.estado.atualizar_linha(protocolo(type="tag", uid=uid))
        self.assertEqual(self.estado.snapshot()["detections"], 0)

    def test_compatibilidade_com_tag_longa_sem_uid_completo(self):
        self.estado.atualizar_linha(protocolo(type="tag", uid=None, uid_long=True))
        resultado = self.estado.snapshot()
        self.assertEqual(resultado["detections"], 1)
        self.assertIsNone(resultado["last_uid"])
        self.assertIsNotNone(resultado["last_tag_at"])

    def test_so_protocolo_de_status_valido_renova_watchdog(self):
        self.assertTrue(self.estado.atualizar_linha(protocolo(type="ready", version="0x92")))
        self.assertTrue(self.estado.atualizar_linha(protocolo(
            type="heartbeat", attempts=1, reader_ok=True, version="0x92")))
        ultima = self.estado.snapshot()["last_heartbeat_at"]
        for linha in ("ByteBank Access pronto. Aproxime um cartão.", "@NFC {",
                      "@NFC []", protocolo(type="heartbeat", attempts=True,
                                           reader_ok=True, version="0x92"),
                      protocolo(type="heartbeat", attempts=2, reader_ok="sim", version="0x92"),
                      protocolo(type="heartbeat", attempts=2, reader_ok=True, version="invalida"),
                      protocolo(type="heartbeat", attempts=2, reader_ok=True, version=None),
                      protocolo(type="tag", uid="01020304")):
            self.assertFalse(self.estado.atualizar_linha(linha), linha)
        self.assertEqual(self.estado.snapshot()["last_heartbeat_at"], ultima)

    def test_falha_do_leitor_nao_confunde_com_desconexao_usb(self):
        for versao in ("0x0", None):
            with self.subTest(versao=versao):
                self.assertTrue(self.estado.atualizar_linha(protocolo(
                    type="heartbeat", attempts=2, reader_ok=False, version=versao)))
                atual = self.estado.snapshot()
                self.assertTrue(atual["connected"])
                self.assertFalse(atual["reader_ok"])
                self.assertEqual(atual["phase"], "error")
                self.assertEqual(atual["version"], versao)
        self.estado.dados["last_heartbeat_at"] = time.time() - 10
        self.assertEqual(self.estado.snapshot()["phase"], "disconnected")


class TestSupervisaoMonitor(unittest.TestCase):
    def setUp(self):
        self.modulo = carregar()
        self.modulo.PRAZO_INICIAL_S = 1.0
        self.modulo.PRAZO_HEARTBEAT_S = 0.3
        self.modulo.INTERVALO_RECONEXAO_S = 0.01
        self.filhos = []
        self.worker = None
        self.popen_real = subprocess.Popen

    def tearDown(self):
        self.modulo.encerrar()
        if self.worker:
            self.worker.join(timeout=4)
            self.assertFalse(self.worker.is_alive(), "Worker não encerrou")
        for filho in self.filhos:
            if filho.poll() is None:
                filho.kill()
            filho.wait(timeout=3)
            if filho.stdout and not filho.stdout.closed:
                filho.stdout.close()

    def filho(self, codigo):
        processo = self.popen_real([sys.executable, "-u", "-c", codigo],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
        self.filhos.append(processo)
        return processo

    def esperar(self, condicao, prazo=3):
        limite = time.monotonic() + prazo
        while time.monotonic() < limite:
            if condicao():
                return
            time.sleep(0.01)
        self.fail("Condição não ocorreu dentro do prazo")

    def test_reboot_sem_eof_reinicia_e_preserva_tentativas(self):
        ready = protocolo(type="ready", version="0x92")
        primeiro = protocolo(type="heartbeat", attempts=5, reader_ok=True, version="0x92")
        segundo = protocolo(type="heartbeat", attempts=3, reader_ok=True, version="0x92")
        sobreposicoes = []

        def iniciar(*args, **kwargs):
            sobreposicoes.append(any(p.poll() is None for p in self.filhos))
            if not self.filhos:
                return self.filho(
                    "import time\nprint(%r, flush=True)\nprint(%r, flush=True)\n"
                    "while True:\n print('ByteBank Access pronto. Aproxime um cartão.', flush=True)\n"
                    " time.sleep(.02)\n" % (ready, primeiro))
            return self.filho("import time\nprint(%r, flush=True)\n"
                              "while True:\n print(%r, flush=True)\n time.sleep(.05)\n" % (ready, segundo))

        with patch.object(self.modulo, "porta_esp32", return_value="/dev/SEM_HARDWARE"), \
                patch.object(self.modulo.subprocess, "Popen", side_effect=iniciar):
            self.worker = threading.Thread(target=self.modulo.acompanhar)
            self.worker.start()
            self.esperar(lambda: len(self.filhos) >= 2 and self.modulo.estado.snapshot()["attempts"] == 8)
            self.modulo.encerrar()
            self.worker.join(timeout=3)

        self.assertFalse(any(sobreposicoes), "Dois processos USB existiram ao mesmo tempo")
        self.assertEqual(len(self.filhos), 2)
        self.assertTrue(all(p.poll() is not None for p in self.filhos))
        mensagens = [e["text"] for e in self.modulo.estado.snapshot()["events"]]
        self.assertTrue(any("parou de enviar telemetria" in texto for texto in mensagens))

    def test_logs_de_boot_nao_substituem_handshake(self):
        self.modulo.PRAZO_INICIAL_S = 0.15
        processo = self.filho("import time\nwhile True:\n print('entry 0x400805a8', flush=True)\n time.sleep(.02)\n")
        motivo, terminou = self.modulo.supervisionar(processo)
        self.assertIn("não iniciou a telemetria", motivo)
        self.assertTrue(terminou)
        self.assertIsNotNone(processo.poll())
        self.assertFalse(self.modulo.estado.snapshot()["connected"])

    def test_shutdown_interrompe_leitura_bloqueada_e_espera_filho(self):
        processo = self.filho("import time; time.sleep(60)")
        self.modulo.processo = processo
        saida = []
        supervisor = threading.Thread(target=lambda: saida.append(self.modulo.supervisionar(processo)))
        supervisor.start()
        self.modulo.encerrar()
        supervisor.join(timeout=3)
        self.assertFalse(supervisor.is_alive())
        self.assertTrue(saida[0][1])
        self.assertIsNotNone(processo.poll())

    def test_processo_que_ignora_sigint_e_encerrado_por_terminate(self):
        processo = self.filho("import signal,time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                              "print('pronto',flush=True)\ntime.sleep(60)")
        self.assertEqual(processo.stdout.readline().strip(), "pronto")
        self.assertTrue(self.modulo.encerrar_processo(processo))
        codigo_esperado = 1 if os.name == "nt" else -signal.SIGTERM
        self.assertEqual(processo.returncode, codigo_esperado)


if __name__ == "__main__":
    unittest.main()
