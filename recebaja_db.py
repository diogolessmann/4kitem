"""
recebaja_db.py — Banco de dados do RecebaJá
Cobrança de boleto no WhatsApp (a régua de lembrete). O banco do LOJISTA emite o
boleto; o RecebaJá só cobra — NÃO processa pagamento (baixa manual no MVP).
Mesmo padrão dos outros SaaS: SQLite em DATA_DIR + WAL + migrações seguras.
"""
import os
import sqlite3

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'recebaja.db')


def get_recebaja_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_recebaja_db():
    conn = get_recebaja_db()
    conn.executescript('''
        -- ── Lojistas (o dono do negócio; é ELE o cliente do RecebaJá) ──────────
        CREATE TABLE IF NOT EXISTS recebaja_users (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            nome           TEXT NOT NULL,
            email          TEXT NOT NULL UNIQUE,
            telefone       TEXT,
            negocio        TEXT,               -- nome da loja (assina a cobrança)
            password_hash  TEXT NOT NULL,
            cnpj           TEXT,               -- só p/ o tier Pro (dente); NULL no grátis
            razao_social   TEXT,
            cnpj_ok        INTEGER DEFAULT 0,  -- CNPJ verificado na Receita?
            termo_aceito   INTEGER DEFAULT 0,
            reset_token    TEXT,
            reset_expires  TEXT,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso  TEXT
        );

        -- ── Clientes do lojista (quem deve) ────────────────────────────────────
        CREATE TABLE IF NOT EXISTS recebaja_clientes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            nome        TEXT NOT NULL,
            whatsapp    TEXT NOT NULL,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES recebaja_users(id)
        );

        -- ── Cobranças (1 boleto = 1 cobrança) ──────────────────────────────────
        CREATE TABLE IF NOT EXISTS recebaja_cobrancas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            cliente_id      INTEGER NOT NULL,
            valor_centavos  INTEGER NOT NULL,
            vencimento      TEXT NOT NULL,            -- YYYY-MM-DD
            linha_digitavel TEXT,
            status          TEXT DEFAULT 'a_vencer',  -- a_vencer|atrasado|pago|cancelado
            opt_in          INTEGER DEFAULT 0,        -- avisou o cliente? (libera régua auto)
            criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
            pago_em         TEXT,
            FOREIGN KEY (user_id)    REFERENCES recebaja_users(id),
            FOREIGN KEY (cliente_id) REFERENCES recebaja_clientes(id)
        );

        -- ── Log de mensagens enviadas (idempotência da régua) ──────────────────
        CREATE TABLE IF NOT EXISTS recebaja_msg_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cobranca_id INTEGER NOT NULL,
            tipo        TEXT NOT NULL,          -- D-3|D0|D+3|D+7|manual_<ts>
            enviado_em  TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(cobranca_id, tipo),
            FOREIGN KEY (cobranca_id) REFERENCES recebaja_cobrancas(id)
        );

        CREATE INDEX IF NOT EXISTS idx_rj_cob_user   ON recebaja_cobrancas(user_id);
        CREATE INDEX IF NOT EXISTS idx_rj_cob_status ON recebaja_cobrancas(status);
        CREATE INDEX IF NOT EXISTS idx_rj_cli_user   ON recebaja_clientes(user_id);
        CREATE INDEX IF NOT EXISTS idx_rj_msg_cob    ON recebaja_msg_log(cobranca_id);
    ''')
    conn.commit()

    # ── Migrações seguras (ADD COLUMN se não existir) ──────────────────────────
    for migration in [
        'ALTER TABLE recebaja_users ADD COLUMN negocio TEXT',
        'ALTER TABLE recebaja_users ADD COLUMN cnpj TEXT',
        'ALTER TABLE recebaja_users ADD COLUMN razao_social TEXT',
        'ALTER TABLE recebaja_users ADD COLUMN cnpj_ok INTEGER DEFAULT 0',
        'ALTER TABLE recebaja_users ADD COLUMN termo_aceito INTEGER DEFAULT 0',
        'ALTER TABLE recebaja_users ADD COLUMN reset_token TEXT',
        'ALTER TABLE recebaja_users ADD COLUMN reset_expires TEXT',
        'ALTER TABLE recebaja_users ADD COLUMN ultimo_acesso TEXT',
    ]:
        try:
            conn.execute(migration); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    conn.close()
