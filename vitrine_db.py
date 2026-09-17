"""
vitrine_db.py — banco do VITRINE (site padrão de loja que posta sozinho, módulo do 4kitem) — 17/set/2026
Uma loja = 1 slug (4kitem.com.br/v/<slug>) e, depois, 1 domínio próprio.
Loja 1: Romano (pelúcia/grua, nacional). Loja 2: Ledoux (elétrica/iluminação, Schroeder) — aprovada 17/set.
"""
import os
import sqlite3

_base = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'vitrine.db')


def get_vit_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def _col(conn, tabela, coluna, ddl):
    cols = [r['name'] for r in conn.execute(f'PRAGMA table_info({tabela})')]
    if coluna not in cols:
        conn.execute(f'ALTER TABLE {tabela} ADD COLUMN {coluna} {ddl}')


def init_vit_db():
    conn = get_vit_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS vit_lojas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            slug          TEXT NOT NULL UNIQUE,
            nome          TEXT NOT NULL,
            senha_hash    TEXT NOT NULL,
            whatsapp      TEXT DEFAULT '',
            telefone      TEXT DEFAULT '',
            endereco      TEXT DEFAULT '',
            cidade        TEXT DEFAULT '',
            horario       TEXT DEFAULT '',
            instagram     TEXT DEFAULT '',
            ml_url        TEXT DEFAULT '',
            cnpj          TEXT DEFAULT '',
            frase         TEXT DEFAULT '',
            descricao     TEXT DEFAULT '',
            google_nota   TEXT DEFAULT '',
            google_n      TEXT DEFAULT '',
            tema          TEXT DEFAULT 'ledoux',
            logo          TEXT DEFAULT '',
            dominio       TEXT DEFAULT '',
            ativo         INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS vit_produtos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            loja_id       INTEGER NOT NULL,
            codigo        TEXT DEFAULT '',
            titulo        TEXT NOT NULL,
            marca         TEXT DEFAULT '',
            categoria     TEXT DEFAULT 'ilum',      -- ilum | elet | smart
            tipo          TEXT DEFAULT '',          -- spot, lampada, fita, plafon, pendente, arandela, tomada, disjuntor, quadro, cabo...
            k             TEXT DEFAULT '',          -- "3000 4000 6500"
            specs         TEXT DEFAULT '',          -- "525 lm · IRC 95 · bivolt" (separado por ·)
            preco         REAL,
            preco_pro     REAL,
            unidade       TEXT DEFAULT '',          -- "o kit · R$ 7,90 a lâmpada"
            estoque       INTEGER DEFAULT 0,
            tag           TEXT DEFAULT '',          -- "Mais vendido"
            descricao     TEXT DEFAULT '',
            legenda_ig    TEXT DEFAULT '',
            foto          TEXT DEFAULT '',
            video_url     TEXT DEFAULT '',
            ml_url        TEXT DEFAULT '',
            ativo         INTEGER DEFAULT 1,
            destaque      INTEGER DEFAULT 0,
            ordem         INTEGER DEFAULT 0,
            vendas        INTEGER DEFAULT 0,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (loja_id) REFERENCES vit_lojas(id)
        );
        CREATE TABLE IF NOT EXISTS vit_eventos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            loja_id       INTEGER NOT NULL,
            produto_id    INTEGER,
            tipo          TEXT NOT NULL,          -- visita | zap | ml | venda
            dia           TEXT NOT NULL,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_vit_ev ON vit_eventos(loja_id, dia, tipo);
        CREATE INDEX IF NOT EXISTS idx_vit_prod ON vit_produtos(loja_id, ativo, ordem);
        CREATE TABLE IF NOT EXISTS vit_midia (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            loja_id       INTEGER NOT NULL,
            arquivo       TEXT NOT NULL,
            tipo          TEXT DEFAULT 'foto',     -- foto | video
            legenda       TEXT DEFAULT '',
            produto_id    INTEGER,
            publicados    TEXT DEFAULT '[]',       -- JSON [{quando, id | erro}]
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    _col(conn, 'vit_lojas', 'ig_user_id', "TEXT DEFAULT ''")
    _col(conn, 'vit_lojas', 'ig_token', "TEXT DEFAULT ''")
    _col(conn, 'vit_lojas', 'cep', "TEXT DEFAULT ''")
    _col(conn, 'vit_lojas', 'me_token', "TEXT DEFAULT ''")
    _col(conn, 'vit_produtos', 'peso', "REAL DEFAULT 0")          # kg (0 = usa o padrão da família)
    _col(conn, 'vit_produtos', 'dim', "TEXT DEFAULT ''")           # "AxLxC cm" ex. 12x12x12
    conn.commit()
    conn.close()
