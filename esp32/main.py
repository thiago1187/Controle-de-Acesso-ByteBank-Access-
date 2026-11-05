# main.py - ByteBank Access (ESP32 + MicroPython)
#
# O boot.py já conectou no Wi-Fi. Aqui o ESP32:
#   1. lê o cartão no leitor RFID (RC522);
#   2. procura o cartão na Tabela Hash e descobre a categoria da pessoa;
#   3. acende a luz da categoria (amarelo = VIP, verde = estágio, vermelho = visita);
#   4. guarda o acesso no Ring Buffer (últimos 20) e envia um JSON para o servidor.

import time
import network
import urequests
from machine import Pin
from mfrc522 import MFRC522
from estruturas import RingBuffer, TabelaCartoes

# Pinos do ESP32 (os mesmos do README)
PINO_SCK, PINO_MOSI, PINO_MISO, PINO_RST, PINO_SDA = 18, 23, 19, 22, 5
PINO_PIR = 27

# Cada categoria tem uma luz: amarelo = VIP, verde = estágio, vermelho = visita
LEDS = {
    "vip": Pin(25, Pin.OUT),      # amarelo
    "estagio": Pin(26, Pin.OUT),  # verde
    "visita": Pin(33, Pin.OUT),   # vermelho
}

# Servidor de teste da Entrega 2. Na Entrega 3 troca pela nossa API.
URL_API = "http://httpbin.org/post"

SEGUNDOS_LUZ = 3          # tempo que a luz fica acesa
INTERVALO_MESMO_CARTAO_MS = 2000  # ignora o mesmo cartão lido de novo logo em seguida

# Cartões cadastrados. Os UIDs abaixo são exemplos: troque pelos dos nossos cartões.
# Cartão desconhecido mostra o UID no terminal, é só copiar para cá.
cartoes = TabelaCartoes()
cartoes.cadastrar("11111111", "Pessoa VIP", "vip")
cartoes.cadastrar("22222222", "Estagiario", "estagio")
cartoes.cadastrar("33333333", "Visitante", "visita")

ultimos_acessos = RingBuffer(20)


def apagar_luzes():
    for led in LEDS.values():
        led.value(0)


def piscar_todas(vezes=3):
    """Cartão não cadastrado: as três luzes piscam juntas."""
    for _ in range(vezes):
        for led in LEDS.values():
            led.value(1)
        time.sleep_ms(200)
        apagar_luzes()
        time.sleep_ms(200)


def ler_uid(leitor):
    """Devolve o UID do cartão em texto hexadecimal, ou None se não tem cartão."""
    status, _ = leitor.request(leitor.REQIDL)
    if status != leitor.OK:
        return None
    status, bruto = leitor.anticoll()
    if status != leitor.OK:
        return None
    return "%02x%02x%02x%02x" % (bruto[0], bruto[1], bruto[2], bruto[3])


def enviar(payload):
    """Manda o JSON para o servidor. Devolve True se deu certo."""
    if not network.WLAN(network.STA_IF).isconnected():
        print("Sem Wi-Fi: o acesso fica só no Ring Buffer.")
        return False
    try:
        resposta = urequests.post(URL_API, json=payload)
        ok = 200 <= resposta.status_code < 300
        print("Servidor respondeu:", resposta.status_code)
        resposta.close()
        return ok
    except (OSError, ValueError) as erro:
        print("Falha ao enviar:", erro)
        return False


def tratar_cartao(uid, presenca):
    """Decide o acesso, acende a luz, guarda no Ring Buffer e envia."""
    achado = cartoes.buscar(uid)

    if achado is None:
        nome, categoria, resultado = None, None, "negado"
        print("Cartão não cadastrado, UID:", uid)
        piscar_todas()
    else:
        nome, categoria = achado
        resultado = "liberado"
        print("Acesso liberado:", nome, "| categoria:", categoria)
        LEDS[categoria].value(1)
        time.sleep(SEGUNDOS_LUZ)
        apagar_luzes()

    payload = {
        "uid": uid,
        "nome": nome,
        "categoria": categoria,
        "resultado": resultado,
        "presenca": presenca,
        "uptime_ms": time.ticks_ms(),
    }
    registro = dict(payload)
    registro["enviado"] = enviar(payload)
    ultimos_acessos.adicionar(registro)
    print("Ring Buffer:", len(ultimos_acessos), "de 20 acessos guardados")


def rodar():
    apagar_luzes()
    pir = Pin(PINO_PIR, Pin.IN)

    try:
        leitor = MFRC522(PINO_SCK, PINO_MOSI, PINO_MISO, PINO_RST, PINO_SDA)
    except (RuntimeError, OSError) as erro:
        print("Leitor RFID não respondeu:", erro)
        return

    print("ByteBank Access pronto. Aproxime um cartão.")
    ultimo_uid = None
    ultimo_tempo = 0
    havia_presenca = False

    while True:
        presenca = pir.value() == 1
        if presenca and not havia_presenca:
            print("Presença detectada perto da porta.")
        havia_presenca = presenca

        try:
            uid = ler_uid(leitor)
        except OSError as erro:
            print("Erro de leitura no RFID:", erro)
            uid = None

        if uid is not None:
            agora = time.ticks_ms()
            repetido = uid == ultimo_uid and time.ticks_diff(agora, ultimo_tempo) < INTERVALO_MESMO_CARTAO_MS
            if not repetido:
                tratar_cartao(uid, presenca)
            ultimo_uid = uid
            ultimo_tempo = time.ticks_ms()

        time.sleep_ms(100)


try:
    rodar()
except KeyboardInterrupt:
    apagar_luzes()
    print("Encerrado.")
