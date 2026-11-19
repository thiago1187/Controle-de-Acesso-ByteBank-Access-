"""Dashboard do ByteBank Access (Entrega 4).

Lê os acessos direto do banco SQLite da API e mostra na tela, atualizando a
cada 2 segundos. Os botões mandam comandos para o ESP32 pela API.

Rodar (com a API já rodando):  streamlit run painel.py
"""

import os
import sqlite3

import pandas as pd
import requests
import streamlit as st

PASTA = os.path.dirname(os.path.abspath(__file__))
CAMINHO_BANCO = os.environ.get("BYTEBANK_DB", os.path.join(PASTA, "..", "api", "bytebank.db"))
URL_API = os.environ.get("BYTEBANK_API", "http://localhost:5000")


def ler_acessos(limite=20):
    """Últimos acessos gravados em audit_access_logs."""
    con = sqlite3.connect(CAMINHO_BANCO)
    try:
        return pd.read_sql_query(
            "SELECT l.timestamp AS horario, l.uid, u.nome, u.categoria, "
            "l.resultado, l.presenca "
            "FROM audit_access_logs l LEFT JOIN usuarios u ON u.id = l.usuario_id "
            "ORDER BY l.id DESC LIMIT ?",
            con,
            params=(limite,),
        )
    finally:
        con.close()


def enviar_comando(comando):
    """Coloca o comando na fila da API. O ESP32 busca em até 2 segundos."""
    try:
        resposta = requests.post(URL_API + "/comandos", json={"comando": comando}, timeout=3)
        return resposta.status_code == 201
    except requests.RequestException:
        return False


st.set_page_config(page_title="ByteBank Access", page_icon="🔐")
st.title("ByteBank Access")
st.caption("Controle de acesso à área VIP e ao cofre")

# ---------- comandos para o ESP32 ----------
st.subheader("Comandos para a placa")
col1, col2, col3 = st.columns(3)
botoes = [
    (col1, "Liberar trava", "liberar"),
    (col2, "Acionar alarme", "alarme"),
    (col3, "Resetar contador", "resetar"),
]
for coluna, texto, comando in botoes:
    if coluna.button(texto, use_container_width=True):
        if enviar_comando(comando):
            st.success("Comando '" + comando + "' enviado. A placa executa em até 2 s.")
        else:
            st.error("Não consegui falar com a API em " + URL_API)


# ---------- telemetria (atualiza sozinha) ----------
@st.fragment(run_every=2)
def telemetria():
    st.subheader("Acessos em tempo real")
    if not os.path.exists(CAMINHO_BANCO):
        st.warning("Banco não encontrado. Rode a API primeiro (python api/app.py).")
        return

    acessos = ler_acessos()
    liberados = int((acessos["resultado"] == "liberado").sum())
    negados = int((acessos["resultado"] == "negado").sum())

    a, b, c = st.columns(3)
    a.metric("Últimos acessos", len(acessos))
    b.metric("Liberados", liberados)
    c.metric("Negados", negados)

    if acessos.empty:
        st.info("Nenhum acesso ainda. Passe um cartão no leitor.")
    else:
        acessos["presenca"] = acessos["presenca"].map({1: "sim", 0: "não"})
        st.dataframe(acessos, hide_index=True, use_container_width=True)


telemetria()
