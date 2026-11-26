"""Entrada do painel no Streamlit Community Cloud.

Na nuvem não tem a API rodando nem a placa. Este arquivo liga o modo
demonstração e abre o mesmo painel.py: a API é carregada dentro do painel e
uma placa simulada faz o papel do ESP32.

Rodar no computador:  streamlit run painel_nuvem.py
"""

import os
import runpy

os.environ["BYTEBANK_DEMO"] = "1"

PASTA = os.path.dirname(os.path.abspath(__file__))
runpy.run_path(os.path.join(PASTA, "painel.py"), run_name="__main__")
