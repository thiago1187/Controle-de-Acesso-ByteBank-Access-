"""Ponte USB: leva as leituras do RC522 para a API sem depender de Wi-Fi.

O ESP32 fica ligado no computador pelo cabo USB. Este programa interrompe o
main.py da placa, roda na memória dela um laço que só lê o cartão e escreve o
UID na serial, e manda cada UID para POST /acessos. O dashboard mostra o
resultado como em qualquer outro acesso. Nada é gravado na placa.

Rodar (com a API já rodando):  python dashboard/ponte_usb.py
Porta e API: variáveis BYTEBANK_ESP32_PORT e BYTEBANK_API.
"""

import os
import sys
import time

import requests
import serial

PORTA = os.environ.get("BYTEBANK_ESP32_PORT", "/dev/cu.usbserial-0001")
URL_API = os.environ.get("BYTEBANK_API", "http://localhost:5000")

# Roda na memória da placa: lê o cartão e escreve "UID:<hex>" a cada aproximação.
LACO_NA_PLACA = b"""
import time
from mfrc522 import MFRC522
leitor = MFRC522(18, 23, 19, 22, 5)
print("PONTE PRONTA")
ultimo = None
quando = 0
while True:
    try:
        uid = leitor.read_uid()
    except OSError:
        uid = None
    if uid is not None:
        texto = bytes(uid).hex()
        agora = time.ticks_ms()
        if texto != ultimo or time.ticks_diff(agora, quando) > 2000:
            print("UID:" + texto)
        ultimo = texto
        quando = agora
    time.sleep_ms(100)
"""


def ler_ate(porta, marca, segundos):
    """Lê a serial até aparecer a marca ou o tempo acabar."""
    recebido = b""
    fim = time.time() + segundos
    while time.time() < fim:
        recebido += porta.read(4096)
        if marca in recebido:
            return True
    return False


def iniciar_laco(porta):
    """Interrompe o programa da placa e põe o laço de leitura para rodar."""
    # Abrir a serial reinicia a placa: espera o boot (Wi-Fi) terminar antes de interromper
    ler_ate(porta, "Aproxime um cart".encode(), 45)
    for _ in range(3):
        porta.write(b"\r\x03")
        time.sleep(0.3)
    porta.read(8192)
    porta.write(b"\r\x01")                      # REPL bruto
    if not ler_ate(porta, b"raw REPL", 5):
        return False
    ler_ate(porta, b">", 1)
    for i in range(0, len(LACO_NA_PLACA), 128):
        porta.write(LACO_NA_PLACA[i:i + 128])
        time.sleep(0.02)
    porta.write(b"\x04")                        # executa
    return ler_ate(porta, b"PONTE PRONTA", 10)


def enviar(uid):
    """Manda o UID para a API e devolve um texto com o resultado."""
    try:
        resposta = requests.post(URL_API + "/acessos", json={"uid": uid, "presenca": False}, timeout=3)
        dados = resposta.json()
    except (requests.RequestException, ValueError) as erro:
        return "API não respondeu: " + str(erro)
    if dados.get("resultado") == "liberado":
        return "liberado | " + str(dados.get("nome")) + " (" + str(dados.get("categoria")) + ")"
    return "negado | cartão sem cadastro, cadastre pelo dashboard"


def main():
    porta = serial.Serial(PORTA, 115200, timeout=0.2)
    porta.dtr = False
    porta.rts = False
    print("Preparando a placa em", PORTA, "(pode levar até 1 minuto)...", flush=True)
    if not iniciar_laco(porta):
        print("Não consegui iniciar a leitura na placa.")
        return 1
    print("Ponte pronta. Aproxime um cartão.", flush=True)
    resto = b""
    while True:
        resto += porta.read(4096)
        while b"\n" in resto:
            linha, resto = resto.split(b"\n", 1)
            texto = linha.decode("utf-8", "replace").strip()
            if texto.startswith("UID:"):
                uid = texto[4:]
                print(time.strftime("%H:%M:%S"), uid, "->", enviar(uid), flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Ponte encerrada.")
