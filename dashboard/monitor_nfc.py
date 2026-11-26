"""Monitor local da leitura real do ESP32 pela USB.

Na raiz: .venv/bin/python dashboard/monitor_nfc.py
Abra http://127.0.0.1:8502. Não precisa de Wi-Fi na placa.
"""

import atexit
from collections import deque
from pathlib import Path
import json
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time

from flask import Flask, jsonify, send_file
from serial.tools import list_ports

RAIZ = Path(__file__).resolve().parent.parent
SERIAL_ID = os.environ.get("BYTEBANK_ESP32_SERIAL", "56AB001645")
PORTA_FIXA = os.environ.get("BYTEBANK_ESP32_PORT")
PREFIXO = "@NFC "
PRAZO_INICIAL_S = 10
PRAZO_HEARTBEAT_S = 6
INTERVALO_RECONEXAO_S = 3
app = Flask(__name__)


class Estado:
    def __init__(self):
        self.lock = threading.Lock()
        self.eventos = deque(maxlen=80)
        self.dados = dict(connected=False, reader_ok=False, phase="connecting",
                          port="", version=None, attempts=0, detections=0,
                          last_uid=None, last_tag_at=None, last_heartbeat_at=None,
                          started_at=time.time(), error=None)
        self.base_tentativas = 0

    def evento(self, texto, tipo="info"):
        self.eventos.append(dict(time=time.time(), text=texto, kind=tipo))

    def atualizar_linha(self, linha):
        pos = linha.find(PREFIXO)
        if pos < 0:
            if linha.strip():
                with self.lock:
                    self.evento(linha.strip()[:500])
            return False
        try:
            dados = json.loads(linha[pos + len(PREFIXO):])
            if not isinstance(dados, dict):
                return False
        except ValueError:
            return False
        tipo = dados.get("type")
        agora = time.time()
        with self.lock:
            if tipo == "ready":
                if not re.fullmatch(r"0x[0-9a-fA-F]{1,2}", str(dados.get("version", ""))):
                    return False
                ok = dados.get("version") in ("0x91", "0x92") and dados.get("reader_ok", True) is True
                self.dados.update(connected=True, reader_ok=ok, phase="reading" if ok else "error",
                                  version=dados.get("version"), error=None if ok else "RC522 sem resposta",
                                  last_heartbeat_at=agora)
                self.evento("ESP32 conectado. RC522 respondeu " + str(dados.get("version")) + "."
                            if ok else "ESP32 conectado pela USB. RC522 sem resposta.", "info" if ok else "error")
                return True
            elif tipo == "heartbeat":
                tentativas = dados.get("attempts")
                versao = dados.get("version")
                versao_valida = (versao is None and dados.get("reader_ok") is False) or (
                    isinstance(versao, str) and re.fullmatch(r"0x[0-9a-fA-F]{1,2}", versao))
                if (type(tentativas) is not int or tentativas < 0
                        or type(dados.get("reader_ok")) is not bool
                        or not versao_valida):
                    return False
                ok = dados["reader_ok"] and versao in ("0x91", "0x92")
                if self.dados["reader_ok"] and not ok:
                    self.evento("O RC522 deixou de responder. Confira as conexões.", "error")
                elif ok and not self.dados["reader_ok"]:
                    self.evento("O RC522 voltou a responder. Leitura retomada.")
                self.dados.update(connected=True, reader_ok=ok,
                                  phase="reading" if ok else "error",
                                  attempts=self.base_tentativas + tentativas,
                                  version=dados.get("version"), last_heartbeat_at=agora,
                                  error=None if ok else "RC522 sem resposta")
                return True
            elif tipo == "tag":
                uid = dados.get("uid", "")
                longo = uid is None and dados.get("uid_long") is True
                if not longo and (not isinstance(uid, str) or not re.fullmatch(
                        r"(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{14}|[0-9a-fA-F]{20})", uid)):
                    return False
                self.dados["detections"] += 1
                self.dados.update(last_uid=None if longo else uid.lower(), last_tag_at=agora)
                self.evento("Tag detectada com UID longo; identificação completa pendente."
                            if longo else "Tag reconhecida. UID: " + uid.lower(), "tag")
            elif tipo == "error":
                mensagem = str(dados.get("message", "Erro na placa"))[:500]
                self.dados.update(phase="error", reader_ok=False, error=mensagem)
                self.evento(mensagem, "error")
            elif tipo == "info":
                self.evento(str(dados.get("message", ""))[:500])
        # Só ready/heartbeat renovam o prazo: logs antigos e tags não o fazem.
        return False

    def snapshot(self):
        with self.lock:
            retorno = dict(self.dados)
            retorno["events"] = list(self.eventos)
        ultima = retorno["last_heartbeat_at"]
        if retorno["connected"] and (ultima is None or time.time() - ultima > 4):
            retorno.update(connected=False, reader_ok=False, phase="disconnected",
                           error="Sem atualização da placa há mais de 4 segundos")
        return retorno


estado = Estado()
parar = threading.Event()
processo = None
processo_lock = threading.Lock()
encerramento_lock = threading.Lock()


def encerrar_processo(atual):
    """Libera a serial antes de permitir outro mpremote, inclusive no shutdown."""
    with encerramento_lock:
        if atual.poll() is not None:
            return True
        acoes = ((atual.terminate, atual.kill) if os.name == "nt"
                  else (lambda: atual.send_signal(signal.SIGINT), atual.terminate, atual.kill))
        for acao in acoes:
            try:
                acao()
            except ProcessLookupError:
                pass
            try:
                atual.wait(timeout=2)
                return True
            except subprocess.TimeoutExpired:
                continue
        return False


def ler_stdout(saida, linhas, parar_leitura):
    """A leitura bloqueante fica separada do watchdog do processo."""
    def enviar(item):
        while not parar_leitura.is_set():
            try:
                linhas.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    try:
        for linha in saida:
            if parar_leitura.is_set():
                break
            enviar(linha)
    except (OSError, ValueError) as erro:
        enviar("Erro ao ler a saída USB: " + str(erro))
    finally:
        enviar(None)


def supervisionar(atual):
    """Devolve o motivo de saída e garante o término do filho antes de retornar."""
    linhas = queue.Queue(maxsize=256)
    parar_leitura = threading.Event()
    leitor = threading.Thread(target=ler_stdout,
                              args=(atual.stdout, linhas, parar_leitura), daemon=True)
    leitor.start()
    prazo = time.monotonic() + PRAZO_INICIAL_S
    recebeu_status = False
    motivo = "Comunicação interrompida."
    try:
        while not parar.is_set():
            restante = prazo - time.monotonic()
            if restante <= 0:
                motivo = ("ESP32 parou de enviar telemetria. Reiniciando a leitura USB."
                          if recebeu_status else
                          "ESP32 não iniciou a telemetria. Reiniciando a leitura USB.")
                break
            try:
                linha = linhas.get(timeout=min(restante, 0.2))
            except queue.Empty:
                if atual.poll() is not None and not leitor.is_alive():
                    break
                continue
            if linha is None:
                break
            if estado.atualizar_linha(linha):
                recebeu_status = True
                prazo = time.monotonic() + PRAZO_HEARTBEAT_S
    finally:
        parar_leitura.set()
        terminou = encerrar_processo(atual)
        leitor.join(timeout=1)
        if not leitor.is_alive():
            atual.stdout.close()
    return motivo, terminou


def porta_esp32():
    for porta in list_ports.comports():
        if (PORTA_FIXA and porta.device == PORTA_FIXA) or (
                not PORTA_FIXA and porta.serial_number == SERIAL_ID):
            return porta.device
    return None


def acompanhar():
    global processo
    while not parar.is_set():
        porta = porta_esp32()
        if not porta:
            with estado.lock:
                if estado.dados["phase"] != "disconnected":
                    estado.evento("ESP32 não encontrado. Reconecte o cabo USB.", "error")
                estado.dados.update(connected=False, reader_ok=False, phase="disconnected",
                                    error="ESP32 não encontrado na USB")
            parar.wait(2)
            continue
        with estado.lock:
            estado.base_tentativas = estado.dados["attempts"]
            estado.dados.update(port=porta, connected=False, reader_ok=False,
                                phase="connecting", error=None, last_heartbeat_at=None)
            estado.evento("Abrindo comunicação USB com o ESP32.")
        comando = [sys.executable, "-u", "-m", "mpremote", "connect", porta,
                   "resume", "run", str(RAIZ / "esp32" / "monitor_nfc.py")]
        atual = None
        terminou = True
        try:
            with processo_lock:
                if parar.is_set():
                    break
                atual = subprocess.Popen(comando, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1)
                processo = atual
            motivo, terminou = supervisionar(atual)
            with estado.lock:
                estado.dados.update(connected=False, reader_ok=False, phase="disconnected")
                if not parar.is_set():
                    estado.evento(motivo, "error")
                    estado.dados["error"] = motivo
        except OSError as erro:
            with estado.lock:
                estado.dados.update(connected=False, reader_ok=False, phase="error", error=str(erro))
                estado.evento(str(erro), "error")
        finally:
            if atual is not None:
                terminou = encerrar_processo(atual)
            with processo_lock:
                if terminou and processo is atual:
                    processo = None
        if not terminou:
            with estado.lock:
                mensagem = "Não foi possível encerrar o processo USB anterior. Reinicie o monitor."
                estado.dados.update(connected=False, reader_ok=False, phase="error", error=mensagem)
                estado.evento(mensagem, "error")
            return  # Nunca abre outro processo enquanto o anterior estiver vivo.
        parar.wait(INTERVALO_RECONEXAO_S)


def encerrar():
    parar.set()
    with processo_lock:
        atual = processo
    if atual is not None:
        encerrar_processo(atual)


def receber_sinal(numero, quadro):
    encerrar()
    raise SystemExit(128 + numero)


@app.get("/")
def painel():
    return send_file(Path(__file__).with_suffix(".html"))


@app.get("/estado")
def dados_atuais():
    resposta = jsonify(estado.snapshot())
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


if __name__ == "__main__":
    atexit.register(encerrar)
    signal.signal(signal.SIGINT, receber_sinal)
    signal.signal(signal.SIGTERM, receber_sinal)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    threading.Thread(target=acompanhar, daemon=True).start()
    app.run(host="127.0.0.1", port=8502, debug=False, use_reloader=False)
