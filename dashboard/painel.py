"""Dashboard do ByteBank Access (Entrega 4).

Lê os acessos direto do banco SQLite da API e mostra na tela, atualizando a
cada 2 segundos. Os botões mandam comandos para o ESP32 pela API.

Rodar (com a API já rodando):  streamlit run painel.py

Modo demonstração (BYTEBANK_DEMO=1, usado pelo painel_nuvem.py): o painel roda
sozinho, sem a API em outro processo e sem a placa. A API é carregada aqui
dentro e uma placa simulada faz o papel do ESP32.
"""

import os
import sqlite3
import sys
import tempfile
import time

import pandas as pd
import requests
import streamlit as st

PASTA = os.path.dirname(os.path.abspath(__file__))
MODO_DEMO = os.environ.get("BYTEBANK_DEMO") == "1"
if MODO_DEMO:
    # Na nuvem não existe api/bytebank.db: o banco da demonstração fica em uma pasta temporária.
    os.environ.setdefault("BYTEBANK_DB", os.path.join(tempfile.gettempdir(), "bytebank-demo.db"))
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


@st.cache_resource
def api_interna():
    """Modo demonstração: carrega a API dentro do dashboard e cria o banco."""
    sys.path.insert(0, os.path.join(PASTA, "..", "api"))
    import app as api
    api.iniciar_banco()
    return api


def cliente_interno():
    """Cliente que chama as rotas da API sem passar pela rede (modo demonstração)."""
    return api_interna().app.test_client()


def simular_cartao(uid):
    """Faz o papel do ESP32: manda o UID para POST /acessos e devolve a resposta."""
    return cliente_interno().post("/acessos", json={"uid": uid, "presenca": True}).get_json()


def placa_simulada_busca_comando():
    """Faz o papel do ESP32: busca o próximo comando da fila."""
    return cliente_interno().get("/comandos/proximo").get_json()["comando"]


def enviar_comando(comando):
    """Coloca o comando na fila da API. O ESP32 busca em até 2 segundos."""
    if MODO_DEMO:
        return cliente_interno().post("/comandos", json={"comando": comando}).status_code == 201
    try:
        resposta = requests.post(URL_API + "/comandos", json={"comando": comando}, timeout=3)
        return resposta.status_code == 201
    except requests.RequestException:
        return False


st.set_page_config(page_title="ByteBank Access", page_icon="🔐")
st.title("ByteBank Access")
st.caption("Controle de acesso à área VIP e ao cofre")

# ---------- placa simulada (só no modo demonstração) ----------
CARTOES_DEMO = [
    ("Cartão VIP", "01020304"),
    ("Cartão de estágio", "11223344"),
    ("Cartão de visita", "55667788"),
    ("Cartão sem cadastro", "deadbeef"),
]
ACOES_DA_PLACA = {
    "liberar": "trava liberada, luz verde no painel",
    "alarme": "alarme acionado, as 3 luzes no painel",
    "resetar": "zera o Ring Buffer",
}

# ---------- semáforo na tela (no lugar dos LEDs da bancada) ----------
LUZES = {"vip": "amarela", "estagio": "verde", "visita": "vermelha"}
ORDEM_DAS_LUZES = ("vermelha", "amarela", "verde")
CORES = {"vermelha": "#C8321F", "amarela": "#F2B705", "verde": "#2DB56A"}
COR_APAGADA = "#D5D8DC"
SEGUNDOS_DO_EFEITO = 4   # quanto tempo "liberar" e "alarme" ficam no semáforo


def luzes_do_acesso(resultado, categoria):
    """Luzes que acendem para um acesso: a da categoria, ou as 3 se foi negado."""
    if resultado == "negado":
        return list(ORDEM_DAS_LUZES)
    return [LUZES[categoria]]


def desenhar_semaforo(acesas, legenda):
    """Desenha as 3 luzes (acesas ou apagadas) e escreve embaixo o que elas dizem."""
    bolas = "".join(
        '<span style="display:inline-block;width:56px;height:56px;border-radius:50%;'
        'margin-right:12px;background:' + (CORES[cor] if cor in acesas else COR_APAGADA) + '"></span>'
        for cor in ORDEM_DAS_LUZES
    )
    st.markdown(bolas, unsafe_allow_html=True)
    st.caption(legenda)

if MODO_DEMO:
    api_interna()
    st.info(
        "Modo demonstração: este painel roda na nuvem com uma placa simulada. "
        "Os acessos são gerados pelos botões abaixo, não pelo hardware da bancada."
    )
    st.subheader("Placa simulada: encoste um cartão")
    for coluna, (texto, uid) in zip(st.columns(len(CARTOES_DEMO)), CARTOES_DEMO):
        if coluna.button(texto, use_container_width=True):
            acesso = simular_cartao(uid)
            if acesso["resultado"] == "liberado":
                st.success(
                    "Acesso liberado para " + acesso["nome"] + " (" + acesso["categoria"]
                    + "). Acende a luz " + LUZES[acesso["categoria"]] + " no semáforo."
                )
            else:
                st.error("Acesso negado: cartão " + acesso["uid"] + " sem cadastro. Acendem as 3 luzes.")

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
        enviado = enviar_comando(comando)
        if enviado and comando in ("liberar", "alarme"):
            # O semáforo da tela mostra o comando por alguns segundos
            st.session_state["efeito"] = (comando, time.time() + SEGUNDOS_DO_EFEITO)
        if not enviado:
            st.error("Não consegui falar com a API em " + URL_API)
        elif MODO_DEMO:
            recebido = placa_simulada_busca_comando()
            if recebido:
                st.success("A placa simulada recebeu '" + recebido + "': " + ACOES_DA_PLACA[recebido] + ".")
        else:
            st.success("Comando '" + comando + "' enviado. A placa executa em até 2 s.")


# ---------- telemetria (atualiza sozinha) ----------
@st.fragment(run_every=2)
def telemetria():
    st.subheader("Acessos em tempo real")
    if not os.path.exists(CAMINHO_BANCO):
        st.warning("Banco não encontrado. Rode a API primeiro (python api/app.py).")
        return

    acessos = ler_acessos()

    # Semáforo: mostra o comando recém-enviado ou, sem comando, o último acesso
    efeito = st.session_state.get("efeito")
    if efeito and time.time() < efeito[1] and efeito[0] == "liberar":
        desenhar_semaforo(["verde"], "Trava liberada pelo painel. Luz verde.")
    elif efeito and time.time() < efeito[1] and efeito[0] == "alarme":
        desenhar_semaforo(list(ORDEM_DAS_LUZES), "Alarme acionado pelo painel. As 3 luzes.")
    elif acessos.empty:
        desenhar_semaforo([], "Nenhuma luz acesa: ainda não houve acesso.")
    else:
        ultimo = acessos.iloc[0]
        acesas = luzes_do_acesso(ultimo["resultado"], ultimo["categoria"])
        if ultimo["resultado"] == "liberado":
            legenda = ("Último acesso: " + ultimo["nome"] + " (" + ultimo["categoria"]
                       + "), liberado. Luz " + acesas[0] + ".")
        else:
            legenda = "Último acesso: cartão " + ultimo["uid"] + " sem cadastro, negado. As 3 luzes."
        desenhar_semaforo(acesas, legenda)

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
