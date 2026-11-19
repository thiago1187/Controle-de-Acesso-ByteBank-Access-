"""Leitura simples de tags no ESP32, sem Wi-Fi, API, LEDs ou PIR.

RC522 -> ESP32: SDA=5, SCK=18, MOSI=23, MISO=19, RST=22,
3.3V=3V3 e GND=GND. Requer mfrc522.py na placa.
"""

import time
from mfrc522 import MFRC522


leitor = MFRC522(sck=18, mosi=23, miso=19, rst=22, cs=5, baudrate=100000)
versao = leitor._rreg(0x37)
print("RC522: versao", hex(versao))
if versao in (0x00, 0xFF):
    raise RuntimeError("RC522 sem resposta. Confira os fios e a alimentacao 3,3 V.")

print("Aproxime uma tag. Ctrl+C encerra.")
ultimo_uid = None
ultima_leitura = time.ticks_ms()

try:
    while True:
        dados = leitor.read_uid()
        if dados is not None:
            uid = bytes(dados).hex()
            if uid != ultimo_uid or time.ticks_diff(time.ticks_ms(), ultima_leitura) > 2000:
                print("TAG RECONHECIDA! UID:", uid)
                ultimo_uid = uid
                ultima_leitura = time.ticks_ms()
        time.sleep_ms(100)
except KeyboardInterrupt:
    print("Leitura encerrada.")
