-- Modelo relacional do ByteBank Access (SQLite)
-- Para criar o banco: sqlite3 bytebank.db < schema.sql
-- (a API também cria as tabelas sozinha na primeira execução)

-- Pessoas com cartão cadastrado. O uid do cartão é a chave da Tabela Hash em memória.
CREATE TABLE IF NOT EXISTS usuarios (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    uid       TEXT    NOT NULL UNIQUE,              -- UID do cartão RFID, ex: "a1b2c3d4"
    nome      TEXT    NOT NULL,
    categoria TEXT    NOT NULL CHECK (categoria IN ('vip', 'estagio', 'visita'))
);

-- Logs de auditoria: uma linha por tentativa de acesso, gravada sempre (liberado ou negado).
CREATE TABLE IF NOT EXISTS audit_access_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    uid        TEXT    NOT NULL,                     -- cartão lido, mesmo se não cadastrado
    usuario_id INTEGER,                              -- NULL quando o cartão não é cadastrado
    resultado  TEXT    NOT NULL CHECK (resultado IN ('liberado', 'negado')),
    presenca   INTEGER NOT NULL DEFAULT 0,           -- 1 se o PIR viu alguém
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);

-- Saldo de cada usuário (parte do ByteBank). Uma linha por usuário.
CREATE TABLE IF NOT EXISTS saldos (
    usuario_id INTEGER PRIMARY KEY,
    saldo      REAL    NOT NULL DEFAULT 0 CHECK (saldo >= 0),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);

-- Transações: cada movimento de saldo. Gravada na mesma transação SQL que atualiza o saldo.
CREATE TABLE IF NOT EXISTS transacoes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    usuario_id INTEGER NOT NULL,
    tipo       TEXT    NOT NULL CHECK (tipo IN ('deposito', 'saque')),
    valor      REAL    NOT NULL CHECK (valor > 0),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);

CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON audit_access_logs(timestamp);
