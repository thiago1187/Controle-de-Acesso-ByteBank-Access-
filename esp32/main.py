# main.py - ByteBank Access (ESP32 + MicroPython)
#
# O boot.py já conectou no Wi-Fi. Aqui o ESP32:
#   1. lê o cartão no leitor RFID (RC522) e manda o UID para a API;
#   2. a API responde se o acesso foi liberado e qual a categoria da pessoa;
#   3. acende verde para qualquer acesso liberado;
#   4. guarda o acesso no Ring Buffer (últimos 20) e na Tabela Hash local (cache);
#   5. de 2 em 2 segundos pergunta à API se o dashboard mandou algum comando.

import time
import network
import urequests
from machine import Pin
from mfrc522 import MFRC522
from estruturas import RingBuffer, TabelaCartoes

# Pinos do ESP32 (os mesmos do README)
PINO_SCK, PINO_MOSI, PINO_MISO, PINO_RST, PINO_SDA = 18, 23, 19, 22, 5
PINO_PIR = 33

# Verde indica acesso liberado; as três luzes piscam em caso de negação ou alarme.
LEDS = {
    "vermelho": Pin(27, Pin.OUT),
    "amarelo": Pin(26, Pin.OUT),
    "verde": Pin(25, Pin.OUT),
}

# Endereço da API (api/app.py), definido em config_wifi.py para esta rede.
try:
    from config_wifi import URL_API
except ImportError:
    URL_API = "http://192.168.0.20:5000"

SEGUNDOS_LUZ = 3                  # tempo que a luz fica acesa
INTERVALO_MESMO_CARTAO_MS = 2000  # ignora o mesmo cartão lido de novo logo em seguida
INTERVALO_COMANDOS_MS = 2000      # de quanto em quanto tempo pergunta por comandos

# Tabela Hash local: guarda os cartões que a API já liberou.
# Se o Wi-Fi cair, a placa ainda reconhece esses cartões (autenticação local).
cartoes = TabelaCartoes()

# Ring Buffer: os 20 últimos acessos ficam na memória da placa.
ultimos_acessos = RingBuffer(20)


def apagar_luzes():
    for led in LEDS.values():
        led.value(0)


def piscar_todas(vezes=3):
    """Cartão negado ou alarme: as três luzes piscam juntas."""
    for _ in range(vezes):
        for led in LEDS.values():
            led.value(1)
        time.sleep_ms(200)
        apagar_luzes()
        time.sleep_ms(200)


def acender_verde():
    LEDS["verde"].value(1)
    time.sleep(SEGUNDOS_LUZ)
    apagar_luzes()


def ler_uid(leitor):
    """Devolve o UID do cartão em texto hexadecimal, ou None se não tem cartão."""
    uid = leitor.read_uid()
    return bytes(uid).hex() if uid is not None else None


def tem_wifi():
    return network.WLAN(network.STA_IF).isconnected()


def perguntar_api(uid, presenca):
    """Manda o UID para a API. Devolve o JSON da resposta, ou None se falhou."""
    if not tem_wifi():
        print("Sem Wi-Fi.")
        return None
    try:
        resposta = urequests.post(URL_API + "/acessos", json={"uid": uid, "presenca": presenca})
        print("Servidor respondeu:", resposta.status_code)
        dados = resposta.json() if resposta.status_code == 201 else None
        resposta.close()
        return dados
    except (OSError, ValueError) as erro:
        print("Falha ao enviar:", erro)
        return None


def tratar_cartao(uid, presenca):
    """Pergunta à API, acende a luz e guarda o acesso no Ring Buffer."""
    resposta = perguntar_api(uid, presenca)

    if resposta is not None:
        # A API decidiu. Se liberou, guarda na Tabela Hash local.
        categoria = resposta.get("categoria")
        resultado = resposta.get("resultado")
        if resultado == "liberado":
            cartoes.cadastrar(uid, resposta.get("nome"), categoria)
        origem = "api"
    else:
        # Sem API: usa a Tabela Hash local (só conhece cartões já liberados antes).
        achado = cartoes.buscar(uid)
        categoria = achado[1] if achado else None
        resultado = "liberado" if achado else "negado"
        origem = "local"

    if resultado == "liberado":
        print("Acesso liberado | categoria:", categoria, "| decidido:", origem)
        acender_verde()
    else:
        print("Acesso negado, UID:", uid, "| decidido:", origem)
        piscar_todas()

    ultimos_acessos.adicionar({
        "uid": uid,
        "categoria": categoria,
        "resultado": resultado,
        "presenca": presenca,
        "origem": origem,
        "uptime_ms": time.ticks_ms(),
    })
    print("Ring Buffer:", len(ultimos_acessos), "de 20 acessos guardados")


def executar_comando(comando):
    """Comandos que vêm do dashboard."""
    print("Comando recebido:", comando)
    if comando == "liberar":
        acender_verde()
    elif comando == "alarme":
        piscar_todas(vezes=6)
    elif comando == "resetar":
        ultimos_acessos.limpar()
        print("Ring Buffer zerado.")


def buscar_comando():
    """Pergunta à API se tem comando pendente. Devolve o nome do comando ou None."""
    if not tem_wifi():
        return None
    try:
        resposta = urequests.get(URL_API + "/comandos/proximo")
        dados = resposta.json() if resposta.status_code == 200 else {}
        resposta.close()
        return dados.get("comando")
    except (OSError, ValueError):
        return None


def rodar():
    apagar_luzes()
    # PULL_DOWN: sem o PIR ligado, o pino fica em 0 e não inventa presença
    pir = Pin(PINO_PIR, Pin.IN, Pin.PULL_DOWN)

    try:
        leitor = MFRC522(PINO_SCK, PINO_MOSI, PINO_MISO, PINO_RST, PINO_SDA)
    except (RuntimeError, OSError) as erro:
        print("Leitor RFID não respondeu:", erro)
        return

    print("ByteBank Access pronto. Aproxime um cartão.")
    ultimo_uid = None
    ultimo_tempo = 0
    ultimo_comando = time.ticks_ms() - INTERVALO_COMANDOS_MS  # já pergunta na primeira volta
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

        agora = time.ticks_ms()
        if time.ticks_diff(agora, ultimo_comando) >= INTERVALO_COMANDOS_MS:
            comando = buscar_comando()
            if comando:
                executar_comando(comando)
            ultimo_comando = agora

        time.sleep_ms(100)


try:
    rodar()
except KeyboardInterrupt:
    apagar_luzes()
    print("Encerrado.")
