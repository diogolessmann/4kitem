"""
somaja_db.py — Banco de dados SomaJá
Coach financeiro por IA no WhatsApp ("Porquim anabolizado"). Modelo: ASSINATURA (trial 7 dias).
Guarda os lançamentos (entradas/saídas) do usuário para gerar saldo, resumo e conselhos.
"""
import os
import sqlite3
import random
import string
from datetime import datetime, timedelta

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'somaja.db')

# Categorias que a IA pode atribuir (fixas, p/ relatório consistente)
CATEGORIAS = [
    'mercado', 'alimentação', 'delivery', 'transporte', 'moradia', 'contas',
    'assinaturas', 'saúde', 'educação', 'lazer', 'compras', 'pets',
    'salário', 'renda extra', 'investimento', 'outros',
]


def get_somaja_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_somaja_db():
    conn = get_somaja_db()
    conn.executescript('''
        -- ── Usuários (B2C — assinatura mensal/anual) ──────────────────────────
        CREATE TABLE IF NOT EXISTS somaja_users (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            nome                  TEXT NOT NULL,
            email                 TEXT NOT NULL UNIQUE,
            telefone              TEXT,
            cpf                   TEXT,
            password_hash         TEXT NOT NULL,
            renda                 REAL DEFAULT 0,     -- renda mensal informada (ajuda o coach)
            plano                 TEXT,               -- chave do plano escolhido
            plan_active           INTEGER DEFAULT 0,  -- 1 = assinatura paga ativa
            trial_until           TEXT,               -- ISO date: acesso grátis até aqui
            asaas_customer_id     TEXT,
            asaas_subscription_id TEXT,
            reset_token           TEXT,
            reset_expires         TEXT,
            carteira_id           INTEGER,            -- carteira (casal/família) que ele divide
            wa_day                TEXT,
            wa_count              INTEGER DEFAULT 0,
            created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso         TEXT
        );

        -- ── Lançamentos (entradas e saídas) ───────────────────────────────────
        CREATE TABLE IF NOT EXISTS somaja_tx (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            tipo        TEXT NOT NULL,          -- 'entrada' | 'saida'
            valor       REAL NOT NULL,
            categoria   TEXT,
            descricao   TEXT,
            data        TEXT,                   -- ISO date (YYYY-MM-DD) do lançamento
            fonte       TEXT,                   -- 'texto' | 'audio' | 'foto'
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES somaja_users(id)
        );

        -- ── Log dos conselhos do coach (p/ não repetir / histórico) ───────────
        CREATE TABLE IF NOT EXISTS somaja_coach_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            texto       TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES somaja_users(id)
        );

        -- ── Carteiras (Lote 3: casal/família — vários membros no mesmo bolso) ──
        CREATE TABLE IF NOT EXISTS somaja_carteiras (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nome        TEXT,
            owner_id    INTEGER,
            codigo      TEXT UNIQUE,        -- código de convite p/ entrar na carteira
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_somaja_tx_user  ON somaja_tx(user_id);
        CREATE INDEX IF NOT EXISTS idx_somaja_tx_data  ON somaja_tx(data);
    ''')
    conn.commit()

    # ── Migrações seguras (ADD COLUMN se não existir) ──────────────────────────
    for migration in [
        'ALTER TABLE somaja_users ADD COLUMN renda REAL DEFAULT 0',
        'ALTER TABLE somaja_users ADD COLUMN plano TEXT',
        'ALTER TABLE somaja_users ADD COLUMN plan_active INTEGER DEFAULT 0',
        'ALTER TABLE somaja_users ADD COLUMN trial_until TEXT',
        'ALTER TABLE somaja_users ADD COLUMN asaas_customer_id TEXT',
        'ALTER TABLE somaja_users ADD COLUMN asaas_subscription_id TEXT',
        'ALTER TABLE somaja_users ADD COLUMN reset_token TEXT',
        'ALTER TABLE somaja_users ADD COLUMN reset_expires TEXT',
        'ALTER TABLE somaja_users ADD COLUMN ultimo_acesso TEXT',
        'ALTER TABLE somaja_users ADD COLUMN wa_day TEXT',           # WhatsApp: dia do último cap
        'ALTER TABLE somaja_users ADD COLUMN wa_count INTEGER DEFAULT 0',   # msgs no dia (anti-abuso)
        'ALTER TABLE somaja_users ADD COLUMN carteira_id INTEGER',   # carteira família (Lote 3)
        'ALTER TABLE somaja_users ADD COLUMN afiliado_ref TEXT',     # código do afiliado que trouxe (Lote afiliados)
    ]:
        try:
            conn.execute(migration); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    # Índices que dependem de colunas adicionadas por migração: criar SÓ DEPOIS
    # de garantir que as colunas existem (senão quebra em bancos antigos — era
    # o bug que derrubava o blueprint do SomaJá com "no such column: carteira_id").
    for idx in [
        'CREATE INDEX IF NOT EXISTS idx_somaja_users_cust ON somaja_users(asaas_customer_id)',
        'CREATE INDEX IF NOT EXISTS idx_somaja_users_cart ON somaja_users(carteira_id)',
    ]:
        try:
            conn.execute(idx); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    conn.close()


# ── Acesso (assinatura ativa OU trial válido) ──────────────────────────────────
def tem_acesso(u) -> bool:
    """True se o usuário pode usar o app: assinatura paga, OU trial válido, OU alguém
    da MESMA carteira (família) tem assinatura ativa — 1 plano cobre a família inteira."""
    if not u:
        return False
    try:
        if u['plan_active']:
            return True
    except (KeyError, IndexError):
        pass
    tu = None
    try:
        tu = u['trial_until']
    except (KeyError, IndexError):
        tu = None
    if tu and tu >= datetime.now().strftime('%Y-%m-%d'):
        return True
    # Família: 1 assinatura cobre todos os membros da carteira
    try:
        cid = u['carteira_id']
    except (KeyError, IndexError):
        cid = None
    if cid:
        conn = get_somaja_db()
        r = conn.execute('SELECT 1 FROM somaja_users WHERE carteira_id=? AND plan_active=1 LIMIT 1',
                         (cid,)).fetchone()
        conn.close()
        if r:
            return True
    return False


def dias_de_trial_restantes(u) -> int:
    try:
        tu = u['trial_until']
    except (KeyError, IndexError):
        tu = None
    if not tu:
        return 0
    try:
        fim = datetime.strptime(tu, '%Y-%m-%d').date()
    except ValueError:
        return 0
    return max(0, (fim - datetime.now().date()).days)


# ── Lançamentos ────────────────────────────────────────────────────────────────
def add_tx(user_id, tipo, valor, categoria, descricao, data=None, fonte='texto'):
    conn = get_somaja_db()
    conn.execute(
        'INSERT INTO somaja_tx (user_id,tipo,valor,categoria,descricao,data,fonte,created_at) '
        'VALUES (?,?,?,?,?,?,?,?)',
        (user_id, tipo, float(valor), categoria, descricao,
         data or datetime.now().strftime('%Y-%m-%d'), fonte, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def ultimos_tx(user_id, limite=10):
    conn = get_somaja_db()
    rows = conn.execute(
        'SELECT * FROM somaja_tx WHERE user_id=? ORDER BY id DESC LIMIT ?',
        (user_id, int(limite))).fetchall()
    conn.close()
    return rows


def _ym(ano_mes=None):
    return ano_mes or datetime.now().strftime('%Y-%m')


def _membros_ids(conn, user_id):
    """IDs dos usuários que dividem a MESMA carteira do user (inclui ele).
    Sem carteira (solo) = só o próprio. É o que faz a família somar junto."""
    u = conn.execute('SELECT carteira_id FROM somaja_users WHERE id=?', (user_id,)).fetchone()
    cid = u['carteira_id'] if u else None
    if not cid:
        return [user_id]
    rows = conn.execute('SELECT id FROM somaja_users WHERE carteira_id=?', (cid,)).fetchall()
    return [r['id'] for r in rows] or [user_id]


def saldo_mes(user_id, ano_mes=None):
    """Entradas, saídas e saldo do mês (YYYY-MM) — somando TODA a carteira do user."""
    ym = _ym(ano_mes)
    conn = get_somaja_db()
    ids = _membros_ids(conn, user_id)
    ph = ','.join('?' * len(ids))
    row = conn.execute(
        "SELECT "
        "COALESCE(SUM(CASE WHEN tipo='entrada' THEN valor ELSE 0 END),0) AS entradas, "
        "COALESCE(SUM(CASE WHEN tipo='saida'   THEN valor ELSE 0 END),0) AS saidas "
        f"FROM somaja_tx WHERE user_id IN ({ph}) AND substr(data,1,7)=?",
        (*ids, ym)).fetchone()
    conn.close()
    ent = row['entradas'] or 0.0
    sai = row['saidas'] or 0.0
    return {'entradas': ent, 'saidas': sai, 'saldo': ent - sai, 'ano_mes': ym}


def resumo_categorias(user_id, ano_mes=None, tipo='saida'):
    """[(categoria, total)] do mês p/ a carteira inteira, do maior gasto pro menor."""
    ym = _ym(ano_mes)
    conn = get_somaja_db()
    ids = _membros_ids(conn, user_id)
    ph = ','.join('?' * len(ids))
    rows = conn.execute(
        "SELECT categoria, COALESCE(SUM(valor),0) AS total "
        f"FROM somaja_tx WHERE user_id IN ({ph}) AND tipo=? AND substr(data,1,7)=? "
        "GROUP BY categoria ORDER BY total DESC",
        (*ids, tipo, ym)).fetchall()
    conn.close()
    return [(r['categoria'] or 'outros', r['total'] or 0.0) for r in rows]


def tx_do_mes(user_id, ano_mes=None):
    """Todos os lançamentos do mês (carteira inteira), mais recentes primeiro. P/ painel e relatório."""
    ym = _ym(ano_mes)
    conn = get_somaja_db()
    ids = _membros_ids(conn, user_id)
    ph = ','.join('?' * len(ids))
    rows = conn.execute(
        f"SELECT * FROM somaja_tx WHERE user_id IN ({ph}) AND substr(data,1,7)=? "
        "ORDER BY data DESC, id DESC", (*ids, ym)).fetchall()
    conn.close()
    return rows


# ── Carteira família (Lote 3) ───────────────────────────────────────────────────
def _gen_codigo(conn):
    while True:
        c = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not conn.execute('SELECT 1 FROM somaja_carteiras WHERE codigo=?', (c,)).fetchone():
            return c


def garantir_carteira(user_id, nome_user=''):
    """Garante que o user tenha uma carteira (cria a pessoal se não tiver).
    Retorna (carteira_id, codigo)."""
    conn = get_somaja_db()
    u = conn.execute('SELECT carteira_id FROM somaja_users WHERE id=?', (user_id,)).fetchone()
    if u and u['carteira_id']:
        c = conn.execute('SELECT id, codigo FROM somaja_carteiras WHERE id=?', (u['carteira_id'],)).fetchone()
        if c:
            conn.close()
            return c['id'], c['codigo']
    codigo = _gen_codigo(conn)
    nome = ('Carteira de ' + (nome_user or 'você')).strip()[:60]
    cur = conn.execute('INSERT INTO somaja_carteiras (nome,owner_id,codigo,created_at) VALUES (?,?,?,?)',
                       (nome, user_id, codigo, datetime.now().isoformat()))
    cid = cur.lastrowid
    conn.execute('UPDATE somaja_users SET carteira_id=? WHERE id=?', (cid, user_id))
    conn.commit(); conn.close()
    return cid, codigo


FAMILIA_MAX = 5   # 1 assinatura cobre a família até este nº de pessoas (anti-abuso)


def entrar_carteira(user_id, codigo):
    """Entra na carteira de outra pessoa pelo código.
    Retorna: nome da carteira (ok), None (código não existe) ou 'LIMITE' (família cheia)."""
    codigo = (codigo or '').strip().upper()
    if not codigo:
        return None
    conn = get_somaja_db()
    c = conn.execute('SELECT * FROM somaja_carteiras WHERE codigo=?', (codigo,)).fetchone()
    if not c:
        conn.close()
        return None
    n = conn.execute('SELECT COUNT(*) AS n FROM somaja_users WHERE carteira_id=?', (c['id'],)).fetchone()['n']
    ja = conn.execute('SELECT carteira_id FROM somaja_users WHERE id=?', (user_id,)).fetchone()
    ja_dentro = ja and ja['carteira_id'] == c['id']
    if not ja_dentro and n >= FAMILIA_MAX:
        conn.close()
        return 'LIMITE'
    conn.execute('UPDATE somaja_users SET carteira_id=? WHERE id=?', (c['id'], user_id))
    conn.commit(); conn.close()
    return c['nome']


def sair_carteira(user_id):
    """Sai da carteira compartilhada — volta a ser solo (carteira própria sob demanda)."""
    conn = get_somaja_db()
    conn.execute('UPDATE somaja_users SET carteira_id=NULL WHERE id=?', (user_id,))
    conn.commit(); conn.close()


def membros_carteira(user_id):
    """Nomes dos membros que dividem a carteira do user (inclui ele)."""
    conn = get_somaja_db()
    u = conn.execute('SELECT carteira_id FROM somaja_users WHERE id=?', (user_id,)).fetchone()
    cid = u['carteira_id'] if u else None
    if not cid:
        row = conn.execute('SELECT nome FROM somaja_users WHERE id=?', (user_id,)).fetchone()
        conn.close()
        return [row['nome']] if row else []
    rows = conn.execute('SELECT nome FROM somaja_users WHERE carteira_id=? ORDER BY id', (cid,)).fetchall()
    conn.close()
    return [r['nome'] for r in rows]


def registrar_conselho(user_id, texto):
    conn = get_somaja_db()
    conn.execute('INSERT INTO somaja_coach_log (user_id,texto,created_at) VALUES (?,?,?)',
                 (user_id, texto, datetime.now().isoformat()))
    conn.commit()
    conn.close()
