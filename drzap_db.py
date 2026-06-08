"""
drzap_db.py — Banco de dados DRZAP
Assistente jurídico por IA (orientação ao consumidor/trabalhista) — créditos pré-pagos.
Privacidade: NÃO guarda o conteúdo das consultas (só log de uso p/ controle/custo).
"""
import os
import sqlite3

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'drzap.db')


def get_drzap_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_drzap_db():
    conn = get_drzap_db()
    conn.executescript('''
        -- ── Usuários (B2C, leigos) ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS drzap_users (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nome              TEXT NOT NULL,
            email             TEXT NOT NULL UNIQUE,
            telefone          TEXT,
            cpf               TEXT,
            password_hash     TEXT NOT NULL,
            creditos          INTEGER DEFAULT 0,
            termo_aceito      INTEGER DEFAULT 0,   -- aceite do termo de ciência (não substitui advogado)
            reset_token       TEXT,
            reset_expires     TEXT,
            asaas_customer_id TEXT,
            created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso     TEXT
        );

        -- ── Compras de crédito (PIX via Asaas) ─────────────────────────────────
        CREATE TABLE IF NOT EXISTS drzap_compras (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL,
            pacote           TEXT,
            creditos         INTEGER NOT NULL,
            valor            REAL,
            status           TEXT DEFAULT "pendente",
            asaas_payment_id TEXT,
            billing_type     TEXT,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES drzap_users(id)
        );

        -- ── Log de uso (SEM o conteúdo da consulta) — controle/antifraude/custo ─
        CREATE TABLE IF NOT EXISTS drzap_uso_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            tipo        TEXT,                 -- 'pergunta' | 'documento' | 'ocr'
            creditos    INTEGER DEFAULT 0,
            categoria   TEXT,                 -- 'consumidor' | 'trabalhista' | ...
            tokens_in   INTEGER DEFAULT 0,    -- p/ medir custo real da IA
            tokens_out  INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES drzap_users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_drzap_compras_user ON drzap_compras(user_id);
        CREATE INDEX IF NOT EXISTS idx_drzap_compras_pay  ON drzap_compras(asaas_payment_id);
        CREATE INDEX IF NOT EXISTS idx_drzap_uso_user     ON drzap_uso_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_drzap_uso_data     ON drzap_uso_log(created_at);
    ''')
    conn.commit()

    # ── Migrações seguras (ADD COLUMN se não existir) ──────────────────────────
    for migration in [
        'ALTER TABLE drzap_users ADD COLUMN creditos INTEGER DEFAULT 0',
        'ALTER TABLE drzap_users ADD COLUMN termo_aceito INTEGER DEFAULT 0',
        'ALTER TABLE drzap_users ADD COLUMN asaas_customer_id TEXT',
        'ALTER TABLE drzap_users ADD COLUMN reset_token TEXT',
        'ALTER TABLE drzap_users ADD COLUMN reset_expires TEXT',
        'ALTER TABLE drzap_users ADD COLUMN ultimo_acesso TEXT',
        'ALTER TABLE drzap_users ADD COLUMN cpf TEXT',
        'ALTER TABLE drzap_users ADD COLUMN telefone TEXT',
    ]:
        try:
            conn.execute(migration); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    conn.close()


# ── Helpers de crédito (débito ATÔMICO — anti-malandro) ────────────────────────
def get_creditos(user_id):
    conn = get_drzap_db()
    row = conn.execute('SELECT creditos FROM drzap_users WHERE id=?', (user_id,)).fetchone()
    conn.close()
    return (row['creditos'] or 0) if row else 0


def add_creditos(user_id, qtd):
    conn = get_drzap_db()
    conn.execute('UPDATE drzap_users SET creditos = COALESCE(creditos,0) + ? WHERE id=?',
                 (int(qtd), user_id))
    conn.commit()
    conn.close()


def debita_creditos(user_id, qtd):
    """Debita N créditos de forma ATÔMICA. Retorna True se debitou, False se sem saldo.
    Só debita se houver saldo suficiente (creditos >= qtd) — impossível ficar negativo."""
    qtd = int(qtd)
    if qtd < 1:
        return True
    conn = get_drzap_db()
    cur = conn.execute(
        'UPDATE drzap_users SET creditos = creditos - ? WHERE id=? AND creditos >= ?',
        (qtd, user_id, qtd))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok
