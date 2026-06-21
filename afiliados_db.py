"""
afiliados_db.py — Programa de Afiliados COMPARTILHADO do 4kitem.
Um afiliado se cadastra UMA vez e pode vender QUALQUER app. A comissão é por app
(valor fixo, recorrente) e é creditada quando o CLIENTE paga (via webhook do Asaas).
Pagamento ao afiliado: PIX via Asaas /transfers (grátis), igual o SlotZap já faz.
"""
import os
import sqlite3
import random
import string
from datetime import datetime

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'afiliados.db')


def get_afil_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn


def init_afil_db():
    conn = get_afil_db()
    conn.executescript('''
        -- ── Afiliados (cadastro único, vende qualquer app) ────────────────────
        CREATE TABLE IF NOT EXISTS afiliados (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            nome           TEXT NOT NULL,
            email          TEXT NOT NULL UNIQUE,
            telefone       TEXT,
            cpf            TEXT,
            codigo         TEXT NOT NULL UNIQUE,   -- código de indicação (vai no ?ref=)
            password_hash  TEXT NOT NULL,
            pix_chave      TEXT,
            pix_tipo       TEXT,                   -- CPF | EMAIL | PHONE | EVP
            reset_token    TEXT,
            reset_expires  TEXT,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso  TEXT
        );

        -- ── Conversões / comissões (1 linha por mensalidade paga do cliente) ──
        -- Idempotência: UNIQUE(app, payment_id) → cada fatura do Asaas credita 1×.
        CREATE TABLE IF NOT EXISTS afiliado_conversoes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            afiliado_id       INTEGER NOT NULL,
            app               TEXT NOT NULL,        -- 'somaja', etc.
            payment_id        TEXT NOT NULL,        -- id do pagamento Asaas (idempotência)
            cliente_nome      TEXT,
            valor             REAL NOT NULL,        -- comissão daquele mês
            status            TEXT DEFAULT 'pendente',  -- pendente | pago | erro
            asaas_transfer_id TEXT,
            erro              TEXT DEFAULT '',
            tentativas        INTEGER DEFAULT 0,
            created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            pago_em           TEXT,
            FOREIGN KEY (afiliado_id) REFERENCES afiliados(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_afil_conv_pay ON afiliado_conversoes(app, payment_id);
        CREATE INDEX IF NOT EXISTS idx_afil_conv_afil ON afiliado_conversoes(afiliado_id);
        CREATE INDEX IF NOT EXISTS idx_afil_codigo ON afiliados(codigo);
    ''')
    conn.commit()
    for migration in [
        'ALTER TABLE afiliados ADD COLUMN pix_chave TEXT',
        'ALTER TABLE afiliados ADD COLUMN pix_tipo TEXT',
        'ALTER TABLE afiliados ADD COLUMN ultimo_acesso TEXT',
    ]:
        try:
            conn.execute(migration); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass
    conn.close()


def _gen_codigo(conn):
    while True:
        c = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not conn.execute('SELECT 1 FROM afiliados WHERE codigo=?', (c,)).fetchone():
            return c


def criar_afiliado(nome, email, telefone, password_hash, pix_chave='', pix_tipo='', cpf=''):
    """Cria o afiliado. Retorna (id, codigo) ou (None, None) se o e-mail já existe."""
    conn = get_afil_db()
    if conn.execute('SELECT 1 FROM afiliados WHERE email=?', (email,)).fetchone():
        conn.close()
        return None, None
    codigo = _gen_codigo(conn)
    cur = conn.execute(
        'INSERT INTO afiliados (nome,email,telefone,cpf,codigo,password_hash,pix_chave,pix_tipo,created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (nome, email, telefone, cpf, codigo, password_hash, pix_chave, pix_tipo, datetime.now().isoformat()))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid, codigo


def get_por_codigo(codigo):
    conn = get_afil_db()
    r = conn.execute('SELECT * FROM afiliados WHERE codigo=?', ((codigo or '').strip().upper(),)).fetchone()
    conn.close()
    return r


def get_por_email(email):
    conn = get_afil_db()
    r = conn.execute('SELECT * FROM afiliados WHERE email=?', ((email or '').strip().lower(),)).fetchone()
    conn.close()
    return r


def get_por_id(aid):
    conn = get_afil_db()
    r = conn.execute('SELECT * FROM afiliados WHERE id=?', (aid,)).fetchone()
    conn.close()
    return r


def registrar_conversao(afiliado_id, app, payment_id, cliente_nome, valor):
    """Cria a comissão de UMA mensalidade paga (idempotente por app+payment_id).
    Retorna o id da conversão nova, ou None se já existia (reenvio de webhook)."""
    conn = get_afil_db()
    cur = conn.execute(
        'INSERT OR IGNORE INTO afiliado_conversoes '
        '(afiliado_id,app,payment_id,cliente_nome,valor,status,created_at) '
        'VALUES (?,?,?,?,?,?,?)',
        (afiliado_id, app, payment_id, cliente_nome, float(valor), 'pendente', datetime.now().isoformat()))
    conn.commit()
    novo = cur.lastrowid if cur.rowcount > 0 else None
    conn.close()
    return novo


def marcar_conversao(conv_id, status, transfer_id='', erro=''):
    conn = get_afil_db()
    conn.execute(
        'UPDATE afiliado_conversoes SET status=?, asaas_transfer_id=?, erro=?, '
        'tentativas=IFNULL(tentativas,0)+1, pago_em=? WHERE id=?',
        (status, transfer_id, erro[:200], (datetime.now().isoformat() if status == 'pago' else None), conv_id))
    conn.commit()
    conn.close()


def conversoes_do_afiliado(afiliado_id, limite=50):
    conn = get_afil_db()
    rows = conn.execute(
        'SELECT * FROM afiliado_conversoes WHERE afiliado_id=? ORDER BY id DESC LIMIT ?',
        (afiliado_id, int(limite))).fetchall()
    conn.close()
    return rows


def stats_afiliado(afiliado_id):
    conn = get_afil_db()
    row = conn.execute(
        "SELECT "
        "COALESCE(SUM(valor),0) AS total, "
        "COALESCE(SUM(CASE WHEN status='pago' THEN valor ELSE 0 END),0) AS pago, "
        "COALESCE(SUM(CASE WHEN status!='pago' THEN valor ELSE 0 END),0) AS pendente, "
        "COUNT(*) AS n_comissoes, "
        "COUNT(DISTINCT cliente_nome) AS n_clientes "
        "FROM afiliado_conversoes WHERE afiliado_id=?", (afiliado_id,)).fetchone()
    conn.close()
    return {'total': row['total'] or 0, 'pago': row['pago'] or 0,
            'pendente': row['pendente'] or 0, 'n_comissoes': row['n_comissoes'] or 0,
            'n_clientes': row['n_clientes'] or 0}


def conversoes_pendentes(limite=100):
    """Comissões pendentes/erro p/ o reconciliador re-tentar o PIX."""
    conn = get_afil_db()
    rows = conn.execute(
        "SELECT * FROM afiliado_conversoes WHERE status IN ('pendente','erro') "
        "AND IFNULL(tentativas,0) < 8 ORDER BY id ASC LIMIT ?", (int(limite),)).fetchall()
    conn.close()
    return rows
