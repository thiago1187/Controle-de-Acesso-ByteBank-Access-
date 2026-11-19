"""Leitura RFID com telemetria pela USB para dashboard/monitor_nfc.py."""

import time
import json
from mfrc522 import MFRC522


def emitir(tipo, **dados):
    dados["type"] = tipo
    print("@NFC " + json.dumps(dados))


def monitorar():
    leitor = None
    tentativas = 0
    ultimo_uid = None
    ultima_tag = time.ticks_ms()
    ultimo_status = time.ticks_ms() - 1000
    ultimo_erro = None
    while True:
        versao = None
        try:
            if leitor is None:
                leitor = MFRC522(18, 23, 19, 22, 5, baudrate=100000)
                emitir("ready", version=hex(leitor._rreg(0x37)), reader_ok=True)
                ultimo_uid = None
            versao = leitor._rreg(0x37)
            if versao not in (0x91, 0x92):
                raise OSError("RC522 sem identificacao valida: " + hex(versao))
            dados = leitor.read_uid()
            tentativas += 1
            ultimo_erro = None
        except OSError as erro:
            mensagem = str(erro)
            if mensagem != ultimo_erro:
                emitir("error", message=mensagem)
            ultimo_erro = mensagem
            emitir("heartbeat", attempts=tentativas, version=hex(versao) if versao is not None else None,
                   reader_ok=False, uptime_ms=time.ticks_ms())
            leitor = None
            time.sleep_ms(1000)
            continue
        agora = time.ticks_ms()
        if dados is not None:
            uid = bytes(dados).hex()
            if uid != ultimo_uid:
                emitir("tag", uid=uid)
            ultimo_uid = uid
            ultima_tag = agora
        if time.ticks_diff(agora, ultima_tag) > 1500:
            ultimo_uid = None
        if time.ticks_diff(agora, ultimo_status) >= 1000:
            versao = leitor._rreg(0x37)
            emitir("heartbeat", attempts=tentativas, version=hex(versao),
                   reader_ok=versao in (0x91, 0x92), uptime_ms=agora)
            ultimo_status = agora
        time.sleep_ms(100)


try:
    monitorar()
except KeyboardInterrupt:
    emitir("info", message="Leitura encerrada.")
except Exception as erro:
    emitir("error", message=str(erro))
