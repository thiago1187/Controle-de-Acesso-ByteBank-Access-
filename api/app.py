"""API do ByteBank Access (Entrega 3).

Recebe o JSON que o ESP32 manda em cada tentativa de acesso, confere o cartão
em uma Tabela Hash em memória (busca O(1)) e grava o log de auditoria no banco.

Rodar:  python app.py        (sobe em http://0.0.0.0:5000)
Testar: python -m unittest discover tests
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from flask import Flask, jsonify, request

PASTA = os.path.dirname(os.path.abspath(__file__))
CAMINHO_BANCO = os.environ.get("BYTEBANK_DB", os.path.join(PASTA, "bytebank.db"))
CAMINHO_SCHEMA = os.path.join(PASTA, "schema.sql")

# Cartões de exemplo. Na bancada, troque pelos UIDs dos nossos cartões.
USUARIOS_INICIAIS = [
    ("01020304", "Pessoa VIP", "vip"),
    ("11223344", "Estagiario", "estagio"),
    ("55667788", "Visitante", "visita"),
]

app = Flask(__name__)

# Tabela Hash em memória: uid -> {"id", "nome", "categoria"}.
# O dict do Python é uma tabela hash, então a busca por uid é O(1).
cartoes = {}

# Comandos que o dashboard mandou e o ESP32 ainda não buscou (fila simples).
COMANDOS_VALIDOS = ("liberar", "alarme", "resetar")
comandos_pendentes = []


def conectar():
    con = sqlite3.connect(CAMINHO_BANCO)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def banco():
    """Abre uma transação. No fim, grava tudo (commit) ou desfaz tudo (rollback) e fecha."""
    con = conectar()
    try:
        with con:
            yield con
    finally:
        con.close()


def iniciar_banco():
    """Cria as tabelas (se faltarem), cadastra os usuários de exemplo e carrega a hash."""
    with open(CAMINHO_SCHEMA, encoding="utf-8") as f:
        schema = f.read()
    with banco() as con:
        con.executescript(schema)
        for uid, nome, categoria in USUARIOS_INICIAIS:
            con.execute(
                "INSERT OR IGNORE INTO usuarios (uid, nome, categoria) VALUES (?, ?, ?)",
                (uid, nome, categoria),
            )
        con.execute(
            "INSERT OR IGNORE INTO saldos (usuario_id, saldo) "
            "SELECT id, 0 FROM usuarios"
        )
    carregar_hash()


def carregar_hash():
    """Lê a tabela usuarios e monta a Tabela Hash em memória."""
    cartoes.clear()
    with banco() as con:
        for linha in con.execute("SELECT id, uid, nome, categoria FROM usuarios"):
            cartoes[linha["uid"]] = {
                "id": linha["id"],
                "nome": linha["nome"],
                "categoria": linha["categoria"],
            }


# ---------- rotas ----------

@app.get("/")
def raiz():
    return jsonify({"api": "ByteBank Access", "cartoes_na_hash": len(cartoes)})


@app.post("/acessos")
def registrar_acesso():
    """Recebe o JSON do ESP32, valida e grava o log de auditoria."""
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "Envie um objeto JSON"}), 400
    if not dados or not isinstance(dados.get("uid"), str) or not dados["uid"]:
        return jsonify({"erro": "JSON precisa ter o campo 'uid' (texto)"}), 400

    uid = dados["uid"].lower()
    presenca = 1 if dados.get("presenca") else 0

    usuario = cartoes.get(uid)          # busca O(1) na Tabela Hash
    resultado = "liberado" if usuario else "negado"
    usuario_id = usuario["id"] if usuario else None

    # O "with" abre uma transação: ou grava tudo, ou não grava nada.
    with banco() as con:
        cursor = con.execute(
            "INSERT INTO audit_access_logs (uid, usuario_id, resultado, presenca) "
            "VALUES (?, ?, ?, ?)",
            (uid, usuario_id, resultado, presenca),
        )
        log_id = cursor.lastrowid

    return jsonify({
        "log_id": log_id,
        "uid": uid,
        "resultado": resultado,
        "nome": usuario["nome"] if usuario else None,
        "categoria": usuario["categoria"] if usuario else None,
    }), 201


@app.get("/acessos")
def listar_acessos():
    """Últimos acessos gravados (o dashboard vai usar isso)."""
    limite = request.args.get("limite", default=20, type=int)
    with banco() as con:
        linhas = con.execute(
            "SELECT l.id, l.timestamp, l.uid, u.nome, u.categoria, l.resultado, l.presenca "
            "FROM audit_access_logs l LEFT JOIN usuarios u ON u.id = l.usuario_id "
            "ORDER BY l.id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return jsonify([dict(linha) for linha in linhas])


@app.get("/usuarios")
def listar_usuarios():
    return jsonify([{"uid": uid, **info} for uid, info in cartoes.items()])


@app.post("/usuarios")
def cadastrar_usuario():
    """Cadastra um cartão novo no banco e na Tabela Hash."""
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "Envie um objeto JSON"}), 400
    uid, nome, categoria = dados.get("uid"), dados.get("nome"), dados.get("categoria")
    if (not isinstance(uid, str) or not uid or not isinstance(nome, str) or not nome
            or categoria not in ("vip", "estagio", "visita")):
        return jsonify({"erro": "Informe uid, nome e categoria (vip, estagio ou visita)"}), 400
    uid = uid.lower()
    if uid in cartoes:
        return jsonify({"erro": "Cartão já cadastrado"}), 409

    with banco() as con:
        cursor = con.execute(
            "INSERT INTO usuarios (uid, nome, categoria) VALUES (?, ?, ?)",
            (uid, nome, categoria),
        )
        con.execute("INSERT INTO saldos (usuario_id, saldo) VALUES (?, 0)", (cursor.lastrowid,))
    cartoes[uid] = {"id": cursor.lastrowid, "nome": nome, "categoria": categoria}
    return jsonify({"uid": uid, **cartoes[uid]}), 201


@app.post("/transacoes")
def registrar_transacao():
    """Depósito ou saque. Atualiza o saldo e grava a transação de forma atômica."""
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "Envie um objeto JSON"}), 400
    uid = str(dados.get("uid", "")).lower()
    tipo = dados.get("tipo")
    valor = dados.get("valor")

    usuario = cartoes.get(uid)
    if not usuario:
        return jsonify({"erro": "Cartão não cadastrado"}), 404
    if tipo not in ("deposito", "saque") or not isinstance(valor, (int, float)) or valor <= 0:
        return jsonify({"erro": "Informe tipo (deposito ou saque) e valor maior que zero"}), 400

    delta = valor if tipo == "deposito" else -valor
    try:
        # Tudo dentro de uma transação SQL: se o saldo ficar negativo,
        # o CHECK do schema falha e nada é gravado (nem o saldo, nem a transação).
        with banco() as con:
            con.execute(
                "UPDATE saldos SET saldo = saldo + ? WHERE usuario_id = ?",
                (delta, usuario["id"]),
            )
            con.execute(
                "INSERT INTO transacoes (usuario_id, tipo, valor) VALUES (?, ?, ?)",
                (usuario["id"], tipo, valor),
            )
            saldo = con.execute(
                "SELECT saldo FROM saldos WHERE usuario_id = ?", (usuario["id"],)
            ).fetchone()["saldo"]
    except sqlite3.IntegrityError:
        return jsonify({"erro": "Saldo insuficiente"}), 422

    return jsonify({"uid": uid, "tipo": tipo, "valor": valor, "saldo": saldo}), 201


@app.post("/comandos")
def enviar_comando():
    """O dashboard chama aqui. O comando fica na fila até o ESP32 buscar."""
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "Envie um objeto JSON"}), 400
    comando = dados.get("comando")
    if comando not in COMANDOS_VALIDOS:
        return jsonify({"erro": "Comando precisa ser um destes: " + ", ".join(COMANDOS_VALIDOS)}), 400
    comandos_pendentes.append(comando)
    return jsonify({"comando": comando, "na_fila": len(comandos_pendentes)}), 201


@app.get("/comandos/proximo")
def proximo_comando():
    """O ESP32 chama a cada 2 s. Devolve o comando mais antigo e tira da fila."""
    comando = comandos_pendentes.pop(0) if comandos_pendentes else None
    return jsonify({"comando": comando})


@app.get("/saldos/<uid>")
def consultar_saldo(uid):
    usuario = cartoes.get(uid.lower())
    if not usuario:
        return jsonify({"erro": "Cartão não cadastrado"}), 404
    with banco() as con:
        saldo = con.execute(
            "SELECT saldo FROM saldos WHERE usuario_id = ?", (usuario["id"],)
        ).fetchone()["saldo"]
    return jsonify({"uid": uid.lower(), "nome": usuario["nome"], "saldo": saldo})


if __name__ == "__main__":
    iniciar_banco()
    print("Banco:", CAMINHO_BANCO, "| cartões na hash:", len(cartoes))
    print("Hora do servidor:", datetime.now().strftime("%d/%m/%Y %H:%M"))
    app.run(host="0.0.0.0", port=5000)
