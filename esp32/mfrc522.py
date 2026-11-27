# Biblioteca MFRC522 (leitor RFID RC522) para MicroPython.
# Autor original: Stefan Wendler, 2016. Licença MIT (texto em LICENSE_mfrc522.txt).
# Origem: https://github.com/wendlers/micropython-mfrc522
# Alterações do grupo ByteBank Access:
# 1. ramo 'esp32' no __init__ (a original só aceita WiPy e ESP8266);
# 2. condição de espera do _tocard sem '~' (a original saía do laço cedo demais);
# 3. leitura da FIFO e dos registradores em uma transação SPI só.
# 4. espera de reset, detecção de chip ausente e timeout de CRC;
# 5. UIDs completos com cascata CL1/CL2/CL3, BCC, SAK e CRC_A;
# 6. encerra o comando anterior antes de limpar as interrupções.
# 7. preserva a função SPI dos pinos ao recriar o leitor.
# 8. limpa CRCIRq escrevendo 1 antes de iniciar um novo cálculo.
# 9. configura RF explicitamente e aguarda o campo antes da primeira leitura.

from machine import Pin, SPI
from os import uname
import time


class MFRC522:

	OK = 0
	NOTAGERR = 1
	ERR = 2

	REQIDL = 0x26
	REQALL = 0x52
	AUTHENT1A = 0x60
	AUTHENT1B = 0x61

	def __init__(self, sck, mosi, miso, rst, cs, baudrate=1000000):

		# O SPI configura estes sinais. Pin.OUT desfaz o mux se o barramento
		# já existe, e o ESP32 não o refaz ao receber configuração idêntica.
		self.sck = Pin(sck)
		self.mosi = Pin(mosi)
		self.miso = Pin(miso)
		self.rst = Pin(rst, Pin.OUT)
		self.cs = Pin(cs, Pin.OUT)

		self.rst.value(0)
		self.cs.value(1)
		
		board = uname()[0]

		if board == 'WiPy' or board == 'LoPy' or board == 'FiPy':
			self.spi = SPI(0)
			self.spi.init(SPI.MASTER, baudrate=baudrate, pins=(self.sck, self.mosi, self.miso))
		elif board == 'esp8266':
			self.spi = SPI(baudrate=100000, polarity=0, phase=0, sck=self.sck, mosi=self.mosi, miso=self.miso)
			self.spi.init()
		elif board == 'esp32':
			self.spi = SPI(2, baudrate=baudrate, polarity=0, phase=0, sck=self.sck, mosi=self.mosi, miso=self.miso)
		else:
			raise RuntimeError("Unsupported platform")

		self.rst.value(1)
		time.sleep_ms(50)  # aguarda o oscilador depois do reset físico
		if self._rreg(0x37) in (0x00, 0xFF):
			raise OSError("RC522 sem resposta: confira SPI e alimentacao 3,3 V")
		self.init()

	def _wreg(self, reg, val):

		# bytes([...]) e não b'%c': no MicroPython, b'%c' com valor acima de 0x7F
		# vira dois bytes (UTF-8), e o leitor recebe lixo na FIFO. Era o que
		# impedia a anticolisão (0x93) e deixava o UID sem ler na bancada.
		self.cs.value(0)
		self.spi.write(bytes([(reg << 1) & 0x7e, val & 0xff]))
		self.cs.value(1)

	def _rreg(self, reg):

		self.cs.value(0)
		resp = bytearray(2)
		self.spi.write_readinto(bytes([0xff & (((reg << 1) & 0x7e) | 0x80), 0]), resp)
		val = resp[1:]
		self.cs.value(1)

		return val[0]

	def _sflags(self, reg, mask):
		self._wreg(reg, self._rreg(reg) | mask)

	def _cflags(self, reg, mask):
		self._wreg(reg, self._rreg(reg) & (~mask))

	def _tocard(self, cmd, send):

		recv = []
		bits = irq_en = wait_irq = n = 0
		stat = self.ERR

		if cmd == 0x0E:
			irq_en = 0x12
			wait_irq = 0x10
		elif cmd == 0x0C:
			irq_en = 0x77
			wait_irq = 0x30

		self._wreg(0x01, 0x00)
		self._wreg(0x02, irq_en | 0x80)
		self._wreg(0x04, 0x7F)
		self._sflags(0x0A, 0x80)

		for c in send:
			self._wreg(0x09, c)
		self._wreg(0x01, cmd)

		if cmd == 0x0C:
			self._sflags(0x0D, 0x80)

		i = 2000
		while True:
			n = self._rreg(0x04)
			i -= 1
			if not ((i != 0) and not (n & 0x01) and not (n & wait_irq)):
				break

		self._cflags(0x0D, 0x80)

		if i:
			if (self._rreg(0x06) & 0x1B) == 0x00:
				stat = self.OK

				if n & irq_en & 0x01:
					stat = self.NOTAGERR
				elif cmd == 0x0C:
					n = self._rreg(0x0A)
					lbits = self._rreg(0x0C) & 0x07
					if lbits != 0:
						bits = (n - 1) * 8 + lbits
					else:
						bits = n * 8

					if n == 0:
						n = 1
					elif n > 16:
						n = 16

					self.cs.value(0)
					buf = bytearray(n + 1)
					self.spi.write_readinto(bytes([0x92] * n + [0]), buf)
					self.cs.value(1)
					recv = list(buf[1:])
			else:
				stat = self.ERR

		return stat, recv, bits

	def _crc(self, data):

		self._wreg(0x01, 0x00)
		self._wreg(0x05, 0x04)  # CRCIRq é limpo escrevendo 1, não 0.
		self._sflags(0x0A, 0x80)

		for c in data:
			self._wreg(0x09, c)

		self._wreg(0x01, 0x03)

		inicio = time.ticks_ms()
		while True:
			n = self._rreg(0x05)
			if n & 0x04:
				break
			if time.ticks_diff(time.ticks_ms(), inicio) >= 100:
				raise OSError("RC522: timeout no calculo do CRC")
			time.sleep_ms(1)

		return [self._rreg(0x22), self._rreg(0x21)]

	def init(self):

		self.reset()
		self._wreg(0x12, 0x00)  # TxMode: 106 kbit/s, CRC automático desligado
		self._wreg(0x13, 0x00)  # RxMode: 106 kbit/s, CRC automático desligado
		self._wreg(0x24, 0x26)  # largura da modulação padrão
		# Timer automático: 13,56 MHz / (2 * 169 + 1) = 40 kHz;
		# recarga 1000 resulta em timeout de 25 ms após a transmissão.
		self._wreg(0x2A, 0x80)
		self._wreg(0x2B, 0xA9)
		self._wreg(0x2C, 0x03)
		self._wreg(0x2D, 0xE8)
		self._wreg(0x15, 0x40)
		self._wreg(0x11, 0x3D)
		self.antenna_on()
		time.sleep_ms(50)  # aguarda o campo antes de enviar REQA/WUPA

	def reset(self):
		self._wreg(0x01, 0x0F)
		inicio = time.ticks_ms()
		time.sleep_ms(50)
		while self._rreg(0x01) & 0x10:
			if time.ticks_diff(time.ticks_ms(), inicio) >= 150:
				raise OSError("RC522: timeout ao sair do reset")
			time.sleep_ms(5)

	def antenna_on(self, on=True):

		if on:
			if self._rreg(0x14) & 0x03 != 0x03:
				self._sflags(0x14, 0x03)
		else:
			self._cflags(0x14, 0x03)

	def request(self, mode):

		self._wreg(0x0D, 0x07)
		(stat, recv, bits) = self._tocard(0x0C, [mode])

		if (stat != self.OK) | (bits != 0x10):
			stat = self.ERR

		return stat, bits

	def anticoll(self):

		ser_chk = 0
		ser = [0x93, 0x20]

		self._wreg(0x0D, 0x00)
		(stat, recv, bits) = self._tocard(0x0C, ser)

		if stat == self.OK:
			if len(recv) == 5:
				for i in range(4):
					ser_chk = ser_chk ^ recv[i]
				if ser_chk != recv[4]:
					stat = self.ERR
			else:
				stat = self.ERR

		return stat, recv

	def read_uid(self):
		"""Lê o UID inteiro (4, 7 ou 10 bytes) de uma única tag, ou None.

		Implementa os níveis CL1/CL2/CL3 da NXP AN10927, verificando BCC,
		SAK e CRC_A antes de devolver a identificação. Colisões são rejeitadas.
		"""
		status, _ = self.request(self.REQALL)
		if status != self.OK:
			return None
		uid = []
		for nivel in (0x93, 0x95, 0x97):
			self._cflags(0x0E, 0x80)
			self._wreg(0x0D, 0x00)
			status, bloco, bits = self._tocard(0x0C, [nivel, 0x20])
			if status != self.OK or bits != 40 or len(bloco) != 5:
				return None
			if bloco[0] ^ bloco[1] ^ bloco[2] ^ bloco[3] != bloco[4]:
				return None
			cascata = bloco[0] == 0x88
			if cascata and nivel == 0x97:
				return None
			selecao = [nivel, 0x70] + bloco
			selecao += self._crc(selecao)
			status, sak, bits = self._tocard(0x0C, selecao)
			if status != self.OK or bits != 24 or len(sak) != 3:
				return None
			if sak[1:] != self._crc(sak[:1]) or bool(sak[0] & 0x04) != cascata:
				return None
			uid.extend(bloco[1:4] if cascata else bloco[:4])
			if not cascata:
				# Encerra a seleção; a próxima WUPA pode reconhecer a mesma tag.
				halt = [0x50, 0x00]
				halt += self._crc(halt)
				self._tocard(0x0C, halt)
				return uid
		return None

	def select_tag(self, ser):

		buf = [0x93, 0x70] + ser[:5]
		buf += self._crc(buf)
		(stat, recv, bits) = self._tocard(0x0C, buf)
		return self.OK if (stat == self.OK) and (bits == 0x18) else self.ERR

	def auth(self, mode, addr, sect, ser):
		return self._tocard(0x0E, [mode, addr] + sect + ser[:4])[0]

	def stop_crypto1(self):
		self._cflags(0x08, 0x08)

	def read(self, addr):

		data = [0x30, addr]
		data += self._crc(data)
		(stat, recv, _) = self._tocard(0x0C, data)
		return recv if stat == self.OK else None

	def write(self, addr, data):

		buf = [0xA0, addr]
		buf += self._crc(buf)
		(stat, recv, bits) = self._tocard(0x0C, buf)

		if not (stat == self.OK) or not (bits == 4) or not ((recv[0] & 0x0F) == 0x0A):
			stat = self.ERR
		else:
			buf = []
			for i in range(16):
				buf.append(data[i])
			buf += self._crc(buf)
			(stat, recv, bits) = self._tocard(0x0C, buf)
			if not (stat == self.OK) or not (bits == 4) or not ((recv[0] & 0x0F) == 0x0A):
				stat = self.ERR

		return stat
