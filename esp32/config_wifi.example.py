# Modelo de configuração do Wi-Fi.
#
# Como usar:
#   1. Copie este arquivo e chame a cópia de config_wifi.py
#   2. Troque os valores abaixo pelos da sua rede (2,4 GHz)
#   3. Envie o config_wifi.py para a placa junto com o boot.py
#
# O config_wifi.py está no .gitignore. Nunca envie a senha real para o GitHub.

WIFI_SSID = "SEU_WIFI_2G"
WIFI_SENHA = "SUA_SENHA"
# IP do computador que roda a API, na mesma rede da placa (ver o README,
# "Configurar o endereço da API"). Exemplo: "http://192.168.0.20:5000"
URL_API = "http://IP_DO_COMPUTADOR:5000"
