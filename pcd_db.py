"""
pcd_db.py — Banco de dados do PCD Fácil (módulo do 4kitem)
Guia passo a passo a isenção de impostos PCD (carro 0km / IPVA). Créditos pré-pagos.
Privacidade: não guarda conteúdo de consulta (só log de uso p/ custo/antifraude).
"""
import os
import sqlite3

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'pcd.db')


def get_pcd_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_pcd_db():
    conn = get_pcd_db()
    conn.executescript('''
        -- ── Usuários (cliente final OU operador: despachante/escritório/concessionária) ──
        CREATE TABLE IF NOT EXISTS pcd_users (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nome              TEXT NOT NULL,
            email             TEXT NOT NULL UNIQUE,
            telefone          TEXT,
            cpf               TEXT,
            password_hash     TEXT NOT NULL,
            perfil            TEXT DEFAULT 'cliente',   -- 'cliente' | 'operador'
            creditos          INTEGER DEFAULT 0,
            reset_token       TEXT,
            reset_expires     TEXT,
            asaas_customer_id TEXT,
            created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso     TEXT
        );

        -- ── Casos (jornada de isenção: trilha A=0km / B=IPVA, com fase) ──────────
        CREATE TABLE IF NOT EXISTS pcd_casos (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL,
            trilha           TEXT NOT NULL,            -- 'A' | 'B'
            uf               TEXT DEFAULT 'SC',
            nome_cliente     TEXT,
            tipo_deficiencia TEXT,
            condutor         TEXT,                     -- 'sim' | 'nao'
            fase_atual       INTEGER DEFAULT 1,
            status           TEXT DEFAULT 'em_andamento',  -- em_andamento | montado | arquivado
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at       TEXT,
            FOREIGN KEY (user_id) REFERENCES pcd_users(id)
        );

        -- ── Documentos anexados a um caso (com veredito da IA) ───────────────────
        CREATE TABLE IF NOT EXISTS pcd_documentos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            caso_id     INTEGER NOT NULL,
            fase        INTEGER,
            tipo_doc    TEXT,
            arquivo     TEXT,
            status      TEXT,                          -- ok | atencao | falta
            analise     TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (caso_id) REFERENCES pcd_casos(id)
        );

        -- ── Compras de crédito (PIX via Asaas) ───────────────────────────────────
        CREATE TABLE IF NOT EXISTS pcd_compras (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL,
            pacote           TEXT,
            creditos         INTEGER NOT NULL,
            valor            REAL,
            status           TEXT DEFAULT "pendente",
            asaas_payment_id TEXT,
            billing_type     TEXT,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES pcd_users(id)
        );

        -- ── Log de uso (SEM conteúdo) — custo/antifraude ─────────────────────────
        CREATE TABLE IF NOT EXISTS pcd_uso_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            tipo        TEXT,                          -- 'abrir_caso' | 'reanalise_doc'
            creditos    INTEGER DEFAULT 0,
            tokens_in   INTEGER DEFAULT 0,
            tokens_out  INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES pcd_users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_pcd_casos_user   ON pcd_casos(user_id);
        CREATE INDEX IF NOT EXISTS idx_pcd_docs_caso    ON pcd_documentos(caso_id);
        CREATE INDEX IF NOT EXISTS idx_pcd_compras_user ON pcd_compras(user_id);
        CREATE INDEX IF NOT EXISTS idx_pcd_compras_pay  ON pcd_compras(asaas_payment_id);
    ''')
    conn.commit()

    # ── Migrações seguras (ADD COLUMN se não existir) ──────────────────────────
    for migration in [
        'ALTER TABLE pcd_users ADD COLUMN perfil TEXT DEFAULT "cliente"',
        'ALTER TABLE pcd_users ADD COLUMN asaas_customer_id TEXT',
        'ALTER TABLE pcd_users ADD COLUMN reset_token TEXT',
        'ALTER TABLE pcd_users ADD COLUMN reset_expires TEXT',
        'ALTER TABLE pcd_users ADD COLUMN ultimo_acesso TEXT',
        'ALTER TABLE pcd_users ADD COLUMN cpf TEXT',
        'ALTER TABLE pcd_users ADD COLUMN afiliado_ref TEXT',
    ]:
        try:
            conn.execute(migration); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    conn.close()


# ── Helpers de crédito (débito ATÔMICO — anti-malandro) ────────────────────────
def get_creditos(user_id):
    conn = get_pcd_db()
    row = conn.execute('SELECT creditos FROM pcd_users WHERE id=?', (user_id,)).fetchone()
    conn.close()
    return (row['creditos'] or 0) if row else 0


def add_creditos(user_id, qtd):
    conn = get_pcd_db()
    conn.execute('UPDATE pcd_users SET creditos = COALESCE(creditos,0) + ? WHERE id=?',
                 (int(qtd), user_id))
    conn.commit()
    conn.close()


def debita_creditos(user_id, qtd):
    """Debita N créditos de forma ATÔMICA. Só desce se houver saldo (>= qtd).
    Retorna True se debitou, False se sem saldo. Impossível ficar negativo."""
    qtd = int(qtd)
    if qtd < 1:
        return True
    conn = get_pcd_db()
    cur = conn.execute(
        'UPDATE pcd_users SET creditos = creditos - ? WHERE id=? AND creditos >= ?',
        (qtd, user_id, qtd))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok
