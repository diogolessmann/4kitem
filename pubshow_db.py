"""
pubshow_db.py — Banco de dados PUBSHOW
Jukebox digital para bares, pubs e estabelecimentos
"""
import os
import sqlite3

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'pubshow.db')


def get_pubshow_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_pubshow_db():
    conn = get_pubshow_db()
    conn.executescript('''

        -- ── Estabelecimentos (B2B) ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS pubshow_businesses (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            nome                 TEXT NOT NULL,
            tipo                 TEXT DEFAULT "bar",
            estilo               TEXT DEFAULT "rock",
            email                TEXT NOT NULL UNIQUE,
            telefone             TEXT,
            cpf_cnpj             TEXT,
            password_hash        TEXT NOT NULL,
            code                 TEXT NOT NULL UNIQUE,
            pix_key              TEXT,
            pix_tipo             TEXT DEFAULT "telefone",
            pix_nome_recebedor   TEXT,
            plano                TEXT DEFAULT "bar",
            plano_ativo          INTEGER DEFAULT 0,
            jukebox_ativo        INTEGER DEFAULT 1,
            canal_atual          TEXT DEFAULT "rock",
            asaas_customer_id    TEXT,
            trial_ends           TEXT,
            reset_token          TEXT,
            reset_expires        TEXT,
            created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso        TEXT
        );

        -- ── Biblioteca de vídeos curados ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS pubshow_videos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_id    TEXT NOT NULL UNIQUE,
            titulo        TEXT NOT NULL,
            artista       TEXT,
            categoria     TEXT NOT NULL,
            subcategoria  TEXT,
            duracao_seg   INTEGER DEFAULT 180,
            views_milhoes REAL DEFAULT 0,
            qualidade     TEXT DEFAULT "HD",
            ativo         INTEGER DEFAULT 1,
            ordem         INTEGER DEFAULT 999,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Fila do Jukebox ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS pubshow_pedidos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id   INTEGER NOT NULL,
            tipo          TEXT NOT NULL,
            nome_cliente  TEXT,
            mensagem      TEXT,
            categoria     TEXT,
            status        TEXT DEFAULT "pendente",
            valor         REAL,
            exibido_at    TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id) REFERENCES pubshow_businesses(id)
        );

        -- ── Assinaturas / pagamentos ───────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS pubshow_assinaturas (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id            INTEGER NOT NULL UNIQUE,
            plano                  TEXT NOT NULL DEFAULT "bar",
            valor                  REAL,
            status                 TEXT DEFAULT "pendente",
            asaas_subscription_id  TEXT,
            asaas_payment_id       TEXT,
            billing_type           TEXT,
            created_at             TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id) REFERENCES pubshow_businesses(id)
        );

        -- ── Índices ────────────────────────────────────────────────────────────
        CREATE INDEX IF NOT EXISTS idx_pubshow_pedidos_biz    ON pubshow_pedidos(business_id);
        CREATE INDEX IF NOT EXISTS idx_pubshow_pedidos_status ON pubshow_pedidos(status);
        CREATE INDEX IF NOT EXISTS idx_pubshow_videos_cat     ON pubshow_videos(categoria, ativo);

    ''')
    conn.commit()

    # Migrações seguras
    for m in [
        'ALTER TABLE pubshow_businesses ADD COLUMN pix_nome_recebedor TEXT',
        'ALTER TABLE pubshow_assinaturas ADD COLUMN asaas_subscription_id TEXT',
        'ALTER TABLE pubshow_assinaturas ADD COLUMN asaas_payment_id TEXT',
        'ALTER TABLE pubshow_assinaturas ADD COLUMN billing_type TEXT',
    ]:
        try:
            conn.execute(m); conn.commit()
        except Exception:
            try: conn.rollback()
            except: pass

    # Seed da biblioteca de vídeos (só insere se estiver vazia)
    total = conn.execute('SELECT COUNT(*) FROM pubshow_videos').fetchone()[0]
    if total == 0:
        _seed_videos(conn)

    conn.close()


def _seed_videos(conn):
    """Biblioteca inicial curada — top vídeos por categoria."""
    videos = [
        # ── ROCK ──────────────────────────────────────────────────────────────
        ('iYYRH4apXDo', 'Enter Sandman',          'Metallica',             'rock', None, 330, 1400),
        ('v2AC41dglnM', 'Thunderstruck',           'AC/DC',                 'rock', None, 292, 1500),
        ('1w7OgIMMRc4', 'Sweet Child O\' Mine',   'Guns N\' Roses',        'rock', None, 356, 1100),
        ('hTWKbfoikeg', 'Smells Like Teen Spirit', 'Nirvana',              'rock', None, 301, 1600),
        ('GLvohMXgcBo', 'Under the Bridge',        'Red Hot Chili Peppers','rock', None, 264, 500),
        ('LnFHBaXBFSI', 'Best of You',             'Foo Fighters',         'rock', None, 256, 300),
        ('kXYiU_JCYtU', 'Numb',                    'Linkin Park',          'rock', None, 187, 700),
        ('8UVNT4wvIGY', 'Boulevard of Broken Dreams','Green Day',          'rock', None, 261, 800),
        ('bAvPpWsMvS0', 'Tempo Perdido',            'Legião Urbana',       'rock', None, 352, 200),
        ('XstCMbMjyKQ', 'Epitáfio',                'Titãs',               'rock', None, 293, 150),
        ('QF-iFtSIYiQ', 'Come as You Are',          'Nirvana',             'rock', None, 219, 600),
        ('pv-GLUvH_hQ', 'Seven Nation Army',        'The White Stripes',   'rock', None, 232, 400),

        # ── PUNK ──────────────────────────────────────────────────────────────
        ('9DR9e9frHc4', 'Basket Case',             'Green Day',            'punk', None, 178, 600),
        ('CnQ_o4hTsoQ', 'What I Got',              'Sublime',              'punk', None, 158, 200),
        ('VN2XlmMGaH8', 'All the Small Things',   'Blink-182',            'punk', None, 168, 500),
        ('_u2vy1hLEqE', 'Fat Lip',                 'Sum 41',               'punk', None, 207, 300),
        ('JGDsLZpnj9s', 'In the End',              'Linkin Park',          'punk', None, 219, 1200),
        ('bWXazVeUID0', 'My Own Summer',            'Deftones',            'punk', None, 234, 100),
        ('KW1pSMGPbkE', 'Breed',                   'Nirvana',              'punk', None, 183, 80),
        ('O4erFVLaqlE', 'Sangue Latino',             'Raimundos',           'punk', None, 195, 50),

        # ── SERTANEJO ─────────────────────────────────────────────────────────
        ('L4SJHm3VTBQ', 'Coração de Cowboy',      'Gusttavo Lima',        'sertanejo', None, 246, 800),
        ('aKAOYPl-y_0', 'Por Enquanto',            'Jorge & Mateus',       'sertanejo', None, 268, 400),
        ('7G_MskxUqeQ', 'Infiel',                  'Marília Mendonça',    'sertanejo', None, 230, 600),
        ('T2hGgxBTmF4', 'Amor de Verdade',         'Zé Neto & Cristiano', 'sertanejo', None, 248, 300),
        ('yiDSY7QMB2E', 'Oi Balde',                'Gusttavo Lima',       'sertanejo', None, 255, 350),
        ('F_7R-VFLPaE', 'Que Pena',                'Henrique & Juliano',  'sertanejo', None, 241, 250),
        ('b0mq7LKggKk', 'Camarote',                'Vitor & Luan',        'sertanejo', None, 215, 150),
        ('jVe5O3HbNsM', 'Desce pro Play',          'MC Kevinho feat.',    'sertanejo', None, 198, 900),

        # ── PAGODE ────────────────────────────────────────────────────────────
        ('t8KYJmVVrTs', 'Cheia de Manias',         'Raça Negra',          'pagode', None, 265, 250),
        ('U3x8jAs4q_E', 'Inevitável',              'Thiaguinho',          'pagode', None, 237, 180),
        ('HoKKbnPYMi4', 'Boa Sorte pra Você',      'Ferrugem',            'pagode', None, 249, 120),
        ('MlLWTeApqIM', 'Me Apaixonei pela Pessoa Errada','Sorriso Maroto','pagode', None, 270, 150),
        ('fEHXHJtP3Hs', 'Tá Escrito',              'Péricles',            'pagode', None, 243, 90),
        ('zKb6R4QLYSI', 'Amor Maior',              'Thiaguinho',          'pagode', None, 232, 200),
        ('8kLAbPXxFgY', 'Esse Cara Sou Eu',        'Roberto Carlos',      'pagode', None, 285, 300),

        # ── POP ────────────────────────────────────────────────────────────────
        ('RBumgq5yVrA', 'Uptown Funk',             'Mark Ronson ft. Bruno Mars','pop', None, 270, 4000),
        ('YqeW9_5kURI', 'Shape of You',            'Ed Sheeran',          'pop', None, 235, 5000),
        ('kTJczUoc26U', 'Blinding Lights',         'The Weeknd',          'pop', None, 200, 3000),
        ('JGwWNGJdvx8', 'Happy',                   'Pharrell Williams',   'pop', None, 233, 1400),
        ('09R8_2nJtjg', 'Sugar',                   'Maroon 5',            'pop', None, 235, 3000),
        ('OPf0YbXqDm0', 'Roar',                    'Katy Perry',          'pop', None, 231, 3300),
        ('SlPhMPnQ58k', 'Envolver',                'Anitta',              'pop', None, 185, 800),
        ('E07s5ZYygMg', 'As It Was',               'Harry Styles',        'pop', None, 167, 2000),

        # ── F1 / SPEED ────────────────────────────────────────────────────────
        ('wiCCEbkz_IM', 'F1 Greatest Overtakes',   'Fórmula 1',           'f1', None, 300, 50),
        ('yk2P7Gn3mDs', 'Best F1 Crashes',         'Fórmula 1',           'f1', None, 420, 30),
        ('qZsyrJXr6no', 'Senna Greatest Moments',  'Ayrton Senna',        'f1', None, 480, 80),
        ('w5MBtVxOBQs', 'Max Verstappen Highlights','Red Bull Racing',    'f1', None, 360, 40),
        ('YbHuGPHaURQ', 'NASCAR Greatest Moments', 'NASCAR',              'f1', 'nascar', 300, 20),
        ('n_RMXCQZ8Gs', 'Ken Block Gymkhana',      'Hoonigan',            'f1', 'drift', 360, 200),
        ('Aqe_bvkTbJM', 'WRC Rally Best Moments',  'WRC',                 'f1', 'rally', 420, 30),
        ('0GiAiRJsYh8', 'Drift King Moments',      'Drift',               'f1', 'drift', 240, 25),

        # ── FUTEBOL ───────────────────────────────────────────────────────────
        ('PFivhFVDfhU', 'Ronaldinho Best Skills',  'Ronaldinho Gaúcho',   'futebol', None, 600, 300),
        ('lBe1OPSHCqk', 'Ronaldo R9 Best Goals',   'Ronaldo Fenômeno',    'futebol', None, 480, 200),
        ('VYs4G2eRIgk', 'Messi vs Ronaldo',         'CR7 vs Messi',        'futebol', None, 600, 500),
        ('Bln9FJz9k9c', 'Top 50 Goals 2023-24',    'Futebol Mundial',     'futebol', None, 600, 80),
        ('yh4OmhMnCww', 'Neymar Amazing Skills',   'Neymar Jr',           'futebol', None, 480, 150),
        ('gqnLMX_KFSY', 'Gols Impossíveis',        'Compilação',          'futebol', None, 360, 50),

        # ── SURF ──────────────────────────────────────────────────────────────
        ('WwRqc56YQXI', 'Gabriel Medina Best Rides','Gabriel Medina',     'surf', None, 360, 20),
        ('d_QQFqHM3DQ', 'Pipeline Best Barrels',    'WSL Surf',           'surf', None, 300, 15),
        ('c8JnDhBHXJM', 'Big Wave Surfing Nazaré',  'Praia do Norte',     'surf', None, 300, 30),
        ('E_EEwEZPjbg', 'Kelly Slater Highlights',  'Kelly Slater',       'surf', None, 480, 50),
        ('xRkbCQ5UT_c', 'Best Surf Moments 2024',   'WSL',                'surf', None, 420, 10),

        # ── AÉREO / RADICAL ────────────────────────────────────────────────────
        ('8CsuH6DDs_U', 'Wingsuit Flying',          'Red Bull',            'aerio', None, 300, 100),
        ('1dPFEePgFAA', 'Base Jump World Record',   'Red Bull',            'aerio', None, 240, 80),
        ('B-2tUPGrRaU', 'Aerobatics Champions',     'Breitling',           'aerio', None, 360, 30),
        ('Bw-VFzFD5kM', 'Skate X-Games Best',       'X-Games',             'radical', None, 300, 40),
        ('hDFn8AnPX70', 'BMX Best Tricks',          'Red Bull BMX',        'radical', None, 240, 30),
        ('MObp71DNOJM', 'Motocross Insane',         'Motocross',           'radical', None, 360, 25),

        # ── ROCK SHOW ─────────────────────────────────────────────────────────
        ('rf0gudLiwBk', 'Metallica Live Moscou 1991','Metallica',          'show_rock', None, 600, 500),
        ('gIDwZKPMEdg', 'AC/DC Live River Plate',   'AC/DC',               'show_rock', None, 600, 200),
        ('Q6tqSWjmqj0', 'Guns N\' Roses Rock in Rio','Guns N\' Roses',    'show_rock', None, 600, 150),
        ('8gKBz77GFB4', 'Queen Live Aid 1985',      'Queen',               'show_rock', None, 600, 400),
        ('9jK-NcRmVcw', 'Legião Urbana Ao Vivo',   'Legião Urbana',       'show_rock', None, 600, 80),

        # ── SERTANEJO SHOW ─────────────────────────────────────────────────────
        ('7mRdJRbzJow', 'Gusttavo Lima Ao Vivo',   'Gusttavo Lima',       'show_sertanejo', None, 600, 150),
        ('xQkPHoJiuBE', 'Marília Mendonça Ao Vivo','Marília Mendonça',    'show_sertanejo', None, 600, 200),
        ('EJnV78LtoKA', 'Jorge & Mateus Live',     'Jorge & Mateus',      'show_sertanejo', None, 600, 100),
        ('SJc1S2IlONM', 'Henrique & Juliano Live', 'Henrique & Juliano',  'show_sertanejo', None, 600, 80),

        # ── PAGODE SHOW ────────────────────────────────────────────────────────
        ('9sNGCZpuKq8', 'Pagodão de Boteco',       'Compilação',          'show_pagode', None, 600, 50),
        ('JqHC4cjDH38', 'Thiaguinho Ao Vivo',      'Thiaguinho',          'show_pagode', None, 600, 70),
        ('m2pt9sOlG_c', 'Péricles Ao Vivo',        'Péricles',            'show_pagode', None, 600, 40),
    ]

    conn.executemany(
        '''INSERT OR IGNORE INTO pubshow_videos
           (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, views_milhoes)
           VALUES (?,?,?,?,?,?,?)''',
        videos
    )
    conn.commit()
