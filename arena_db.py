"""arena_db.py — banco ISOLADO do módulo ARENA (jogos casuais valendo prêmio, PWA /arena).

Padrão copiado de somaja_db.py (auth isolado) + drzap_db.py (créditos pré-pagos).
L0: fundação. As tabelas de compra/partida/pagamento já nascem prontas pros próximos lotes
(comprar crédito no PIX, sala valendo, saque) — migrations idempotentes, não quebram nada.
"""
import os
import sqlite3
from datetime import datetime

_base = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_base, 'arena.db')


def get_arena_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass
    return conn


def init_arena_db():
    conn = get_arena_db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS arena_users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        email TEXT UNIQUE,
        tel TEXT,
        cpf TEXT,
        password_hash TEXT NOT NULL,
        creditos INTEGER DEFAULT 0,
        saldo REAL DEFAULT 0,
        pix_chave TEXT,
        pix_tipo TEXT,
        asaas_customer_id TEXT,
        criado_em TEXT,
        ultimo_acesso TEXT
    );
    CREATE TABLE IF NOT EXISTS arena_compras(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        pacote TEXT,
        creditos INTEGER,
        valor REAL,
        status TEXT DEFAULT 'pendente',
        charge_id TEXT,
        ext_ref TEXT,
        criado_em TEXT
    );
    CREATE TABLE IF NOT EXISTS arena_partidas(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        jogo TEXT,
        seed INTEGER,
        user_id INTEGER,
        pontos INTEGER DEFAULT 0,
        resultado TEXT,
        creditos_usados INTEGER DEFAULT 0,
        premio REAL DEFAULT 0,
        criado_em TEXT
    );
    CREATE TABLE IF NOT EXISTS arena_pagamentos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        partida_id INTEGER,
        user_id INTEGER,
        pix_chave TEXT,
        pix_tipo TEXT,
        valor REAL,
        ext_ref TEXT UNIQUE,
        status TEXT DEFAULT 'pendente',
        transfer_id TEXT,
        erro TEXT,
        tentativas INTEGER DEFAULT 0,
        criado_em TEXT
    );
    CREATE TABLE IF NOT EXISTS arena_salas(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE,
        jogo TEXT DEFAULT 'blocos',
        seed INTEGER,
        aposta INTEGER DEFAULT 1,
        criador_id INTEGER,
        criador_pontos INTEGER,
        criador_moves TEXT,
        oponente_id INTEGER,
        oponente_pontos INTEGER,
        oponente_moves TEXT,
        oponente_em TEXT,
        vencedor_id INTEGER,
        premio REAL DEFAULT 0,
        rake REAL DEFAULT 0,
        status TEXT DEFAULT 'criada',
        criado_em TEXT,
        apurado_em TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_arena_compras_user ON arena_compras(user_id);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_arena_pag_ext ON arena_pagamentos(ext_ref);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_arena_salas_token ON arena_salas(token);
    ''')
    conn.commit()
    for m in [
        'ALTER TABLE arena_users ADD COLUMN saldo REAL DEFAULT 0',
        'ALTER TABLE arena_users ADD COLUMN pix_chave TEXT',
        'ALTER TABLE arena_users ADD COLUMN pix_tipo TEXT',
        'ALTER TABLE arena_users ADD COLUMN asaas_customer_id TEXT',
        'ALTER TABLE arena_salas ADD COLUMN oponente_em TEXT',
    ]:
        try:
            conn.execute(m); conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    conn.close()


# ---- usuários (molde SomaJá) ----
def get_user(uid):
    conn = get_arena_db()
    u = conn.execute('SELECT * FROM arena_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return dict(u) if u else None


def get_user_by_email(email):
    conn = get_arena_db()
    u = conn.execute('SELECT * FROM arena_users WHERE email=?', ((email or '').strip().lower(),)).fetchone()
    conn.close()
    return dict(u) if u else None


def criar_user(nome, email, tel, password_hash):
    conn = get_arena_db()
    try:
        cur = conn.execute(
            'INSERT INTO arena_users(nome,email,tel,password_hash,creditos,saldo,criado_em) '
            'VALUES(?,?,?,?,?,?,?)',
            (nome, (email or '').strip().lower(), tel, password_hash, 0, 0, datetime.now().isoformat()))
        conn.commit()
        uid = cur.lastrowid
    except Exception:
        uid = None
    conn.close()
    return uid


# ---- créditos (molde DRZAP — atômico) ----
def get_creditos(uid):
    conn = get_arena_db()
    r = conn.execute('SELECT creditos FROM arena_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return (r['creditos'] or 0) if r else 0


def add_creditos(uid, qtd):
    conn = get_arena_db()
    conn.execute('UPDATE arena_users SET creditos=COALESCE(creditos,0)+? WHERE id=?', (int(qtd), uid))
    conn.commit()
    conn.close()


def debita_creditos(uid, qtd):
    """Debita SÓ se houver saldo (atômico) — retorna True/False. Blinda corrida."""
    qtd = int(qtd)
    if qtd < 1:
        return True
    conn = get_arena_db()
    cur = conn.execute('UPDATE arena_users SET creditos=creditos-? WHERE id=? AND creditos>=?',
                       (qtd, uid, qtd))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# ---- saldo em R$ (prêmio ganho; é o que vira saque no Lote 4) ----
def get_saldo(uid):
    conn = get_arena_db()
    r = conn.execute('SELECT saldo FROM arena_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return round((r['saldo'] or 0.0), 2) if r else 0.0


def add_saldo(uid, valor):
    conn = get_arena_db()
    # ROUND no VALOR ARMAZENADO (não só na leitura) — evita drift de float IEEE754 no saldo.
    conn.execute('UPDATE arena_users SET saldo=ROUND(COALESCE(saldo,0)+?,2) WHERE id=?', (round(float(valor), 2), uid))
    conn.commit()
    conn.close()


def debita_saldo(uid, valor):
    """Debita R$ do saldo SÓ se houver (atômico) — retorna True/False. Blinda saque a descoberto.
    ROUND no armazenado E na comparação (WHERE) pra não travar por 6.7999999 vs 6.80."""
    valor = round(float(valor), 2)
    if valor <= 0:
        return False
    conn = get_arena_db()
    cur = conn.execute('UPDATE arena_users SET saldo=ROUND(saldo-?,2) WHERE id=? AND ROUND(saldo,2)>=?',
                       (valor, uid, valor))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok
