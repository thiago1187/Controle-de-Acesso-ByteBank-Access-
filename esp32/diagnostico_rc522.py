"""Diagnóstico de bancada, independente de mfrc522.py, Wi-Fi, PIR e LEDs.

Executar pela USB, sem substituir os arquivos da placa:
    mpremote connect PORTA resume run esp32/diagnostico_rc522.py

Só identifica tags; não lê nem escreve a memória dos cartões.
Referências: datasheet NXP MFRC522, seções 8.1.2 e 16.1.1;
inicialização e transceive de github.com/miguelbalboa/rfid (domínio público).
"""

from machine import Pin, SPI
import time


class DiagnosticoRC522:
    def __init__(self):
        self.cs = Pin(5, Pin.OUT, value=1)
        self.rst = Pin(22, Pin.OUT, value=1)
        self.spi = SPI(2, baudrate=100000, polarity=0, phase=0,
                       sck=Pin(18), mosi=Pin(23), miso=Pin(19))
        self.reset_fisico()

    def ler(self, reg):
        recebido = bytearray(2)
        self.cs.value(0)
        try:
            self.spi.write_readinto(bytes([0x80 | ((reg << 1) & 0x7E), 0]), recebido)
        finally:
            self.cs.value(1)
        return recebido[1]

    def escrever(self, reg, valor):
        self.cs.value(0)
        try:
            self.spi.write(bytes([(reg << 1) & 0x7E, valor & 0xFF]))
        finally:
            self.cs.value(1)

    def reset_fisico(self):
        self.rst.value(0)
        time.sleep_ms(20)
        self.rst.value(1)
        time.sleep_ms(50)

    def configurar(self):
        self.reset_fisico()
        # Modulação a 106 kbit/s, temporizador de 25 ms e CRC_A.
        valores = ((0x12, 0), (0x13, 0), (0x24, 0x26),
                   (0x2A, 0x80), (0x2B, 0xA9), (0x2C, 0x03),
                   (0x2D, 0xE8), (0x15, 0x40), (0x11, 0x3D))
        for reg, valor in valores:
            self.escrever(reg, valor)
        divergencias = [(hex(reg), hex(valor), hex(self.ler(reg)))
                        for reg, valor in valores if self.ler(reg) != valor]
        if divergencias:
            raise RuntimeError("Registradores nao mantiveram a configuracao: " + str(divergencias))
        self.escrever(0x14, self.ler(0x14) | 3)
        time.sleep_ms(50)

    def testar_spi(self):
        dados = bytes((i * 17 + 0x55) & 0xFF for i in range(64))
        for frequencia in (100000, 1000000):
            self.spi.init(baudrate=frequencia, polarity=0, phase=0)
            self.escrever(0x01, 0)
            self.escrever(0x0A, 0x80)
            for valor in dados:
                self.escrever(0x09, valor)
            quantidade = self.ler(0x0A)
            recebido = bytes(self.ler(0x09) for _ in range(min(quantidade, 64)))
            print("SPI", frequencia, "Hz / FIFO:",
                  "PASSOU" if recebido == dados else "FALHOU",
                  "bytes:", quantidade)
            if recebido != dados:
                raise RuntimeError("Falha de integridade na comunicacao SPI")
        self.spi.init(baudrate=100000, polarity=0, phase=0)

    def autoteste(self):
        esperado = bytes.fromhex(
            "00eb66ba57bf2395d0e30d3d27895cde9d3ba700215b8982"
            "513aeb020ca500497c844db3ccd21b815d4876d5716121a9"
            "86968338cf9d5b6ddc15ba3e7d953b2f")
        self.reset_fisico()
        if self.ler(0x37) != 0x92:
            print("Autoteste omitido: referencia disponivel apenas para versao 0x92.")
            return
        try:
            # A sequência de autoteste da NXP começa com SoftReset.
            self.escrever(0x01, 0x0F)
            time.sleep_ms(50)
            inicio_reset = time.ticks_ms()
            while self.ler(0x01) & 0x10:
                if time.ticks_diff(time.ticks_ms(), inicio_reset) >= 150:
                    raise RuntimeError("SoftReset nao concluiu")
                time.sleep_ms(5)
            self.escrever(0x0A, 0x80)
            for _ in range(25):
                self.escrever(0x09, 0)
            self.escrever(0x01, 1)
            self.escrever(0x36, 9)
            self.escrever(0x09, 0)
            self.escrever(0x01, 3)
            inicio = time.ticks_ms()
            while self.ler(0x0A) < 64 and time.ticks_diff(time.ticks_ms(), inicio) < 1000:
                time.sleep_ms(5)
            quantidade = self.ler(0x0A)
            self.escrever(0x01, 0)
            recebido = bytes(self.ler(0x09) for _ in range(min(quantidade, 64)))
            print("AUTOTESTE DIGITAL:", "PASSOU" if recebido == esperado else "FALHOU",
                  "quantidade:", quantidade,
                  "bytes corretos:", sum(a == b for a, b in zip(recebido, esperado)))
            if recebido != esperado:
                print("AUTOTESTE / BYTES:", recebido.hex())
        finally:
            self.escrever(0x36, 0)
            self.configurar()

    def trocar(self, dados, bits_finais=0):
        self.escrever(0x01, 0)       # Idle antes de limpar as interrupções.
        self.escrever(0x04, 0x7F)    # Limpa todos os eventos anteriores.
        self.escrever(0x0A, 0x80)    # Esvazia a FIFO.
        self.escrever(0x0E, self.ler(0x0E) & 0x7F)
        for valor in dados:
            self.escrever(0x09, valor)
        self.escrever(0x0D, bits_finais)
        self.escrever(0x01, 0x0C)    # Transceive.
        self.escrever(0x0D, bits_finais | 0x80)
        inicio = time.ticks_ms()
        while True:
            irq = self.ler(0x04)
            if irq & 0x30 or irq & 1 or time.ticks_diff(time.ticks_ms(), inicio) > 50:
                break
            time.sleep_us(100)
        self.escrever(0x0D, bits_finais)
        erro = self.ler(0x06)
        n = self.ler(0x0A)
        resposta = bytes(self.ler(0x09) for _ in range(min(n, 64)))
        return irq, erro, resposta, self.ler(0x0C) & 7


def diagnosticar():
    leitor = DiagnosticoRC522()
    versoes = [leitor.ler(0x37) for _ in range(10)]
    print("VERSAO:", [hex(v) for v in versoes])
    if len(set(versoes)) != 1 or versoes[0] in (0, 255):
        raise RuntimeError("Leitor sem identificacao SPI estavel")

    leitor.escrever(0x11, 0x3D)
    antes = leitor.ler(0x11)
    leitor.reset_fisico()
    depois = leitor.ler(0x11)
    print("RST GPIO22:", "PASSOU" if (antes, depois) == (0x3D, 0x3F) else "FALHOU",
          hex(antes), "->", hex(depois))
    if (antes, depois) != (0x3D, 0x3F):
        raise RuntimeError("Conferir ligacao do RST ao GPIO22")
    leitor.testar_spi()
    leitor.autoteste()
    leitor.configurar()
    print("CONFIGURACAO: leitura de retorno conferida; antena:", hex(leitor.ler(0x14)))
    print("MANTENHA UMA TAG SOBRE A ANTENA. Teste independente de 40 segundos.")

    inicio = time.ticks_ms()
    estatisticas = {}
    ganho_alto = False
    while time.ticks_diff(time.ticks_ms(), inicio) < 40000:
        if not ganho_alto and time.ticks_diff(time.ticks_ms(), inicio) >= 20000:
            leitor.escrever(0x26, (leitor.ler(0x26) & 0x8F) | 0x70)
            ganho_alto = True
            print("GANHO: 48 dB")
        irq, erro, atqa, bits = leitor.trocar(b"\x52", 7)
        chave = (hex(irq), hex(erro), len(atqa), bits)
        estatisticas[chave] = estatisticas.get(chave, 0) + 1
        if irq & 0x30 and not erro and len(atqa) == 2 and bits == 0:
            print("TAG RESPONDEU / ATQA:", atqa.hex())
            irq2, erro2, bruto, bits2 = leitor.trocar(b"\x93\x20")
            if irq2 & 0x30 and not erro2 and len(bruto) == 5 and bits2 == 0:
                bcc = bruto[0] ^ bruto[1] ^ bruto[2] ^ bruto[3]
                if bcc == bruto[4]:
                    if bruto[0] == 0x88:
                        print("TAG DETECTADA COM UID LONGO. Cascata inicial:", bruto.hex())
                    else:
                        print("UID RECONHECIDO:", bruto[:4].hex())
                    return
            print("ANTICOLISAO:", hex(irq2), hex(erro2), bruto.hex(), bits2)
        time.sleep_ms(100)
    print("SEM UID. Estatisticas (IRQ, erro, bytes recebidos, bits):", estatisticas)


diagnosticar()
