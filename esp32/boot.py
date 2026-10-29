# boot.py - ByteBank Access (ESP32 com MicroPython)
#
# O MicroPython executa este arquivo sozinho quando a placa liga ou reinicia,
# antes do main.py. A função aqui é uma só: conectar o ESP32 ao Wi-Fi para que
# o main.py consiga enviar os acessos para a API.
#
# O nome e a senha da rede ficam em config_wifi.py, que NÃO vai para o GitHub.
# Use o config_wifi.example.py como modelo.

import network
import time

try:
    from config_wifi import WIFI_SSID, WIFI_SENHA
except ImportError:
    WIFI_SSID = None
    WIFI_SENHA = None

TENTATIVAS = 3        # quantas vezes tenta conectar antes de desistir
TEMPO_LIMITE_S = 15   # segundos de espera em cada tentativa

wlan = network.WLAN(network.STA_IF)  # STA_IF: a placa entra em uma rede existente


def conectar_wifi():
    """Conecta ao Wi-Fi. Devolve True se conectou e False se não conseguiu.

    Não levanta exceção: se o Wi-Fi falhar, a placa continua ligada e o
    main.py decide o que fazer (por exemplo, guardar os acessos na fila local).
    """
    if WIFI_SSID is None:
        print("Erro: config_wifi.py não encontrado na placa.")
        print("Copie config_wifi.example.py para config_wifi.py e preencha.")
        return False

    wlan.active(True)

    if wlan.isconnected():
        print("Wi-Fi já estava conectado. IP:", wlan.ifconfig()[0])
        return True

    for tentativa in range(1, TENTATIVAS + 1):
        print("Conectando à rede '{}' (tentativa {} de {})...".format(
            WIFI_SSID, tentativa, TENTATIVAS))

        try:
            wlan.connect(WIFI_SSID, WIFI_SENHA)
        except OSError as erro:
            # Falha do próprio rádio Wi-Fi da placa
            print("Erro de hardware no Wi-Fi:", erro)
            time.sleep(2)
            continue

        # Espera até 1 segundo por vez, até conectar ou estourar o tempo
        for _ in range(TEMPO_LIMITE_S):
            if wlan.isconnected():
                print("Wi-Fi conectado! IP:", wlan.ifconfig()[0])
                return True
            time.sleep(1)

        print("Não conectou a tempo. Código de status:", wlan.status())
        wlan.disconnect()

    print("Não foi possível conectar ao Wi-Fi.")
    print("Confira nome, senha e se a rede é 2,4 GHz (o ESP32 não usa 5 GHz).")
    return False


conectar_wifi()
