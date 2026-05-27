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
    """Biblioteca curada — os maiores de todos os tempos por categoria.
    Critério: vídeos icônicos, nostalgia garantida, maior audiência histórica.
    Formato: (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, views_milhoes)
    """
    videos = [

        # ══════════════════════════════════════════════════════════════════════
        # ROCK — Os absolutos de todos os tempos
        # ══════════════════════════════════════════════════════════════════════
        # Clássicos eternos — todo bar canta junto
        ('fJ9rUzIMcZQ', 'Bohemian Rhapsody',          'Queen',                 'rock', 'classic', 354, 1900),
        ('v2AC41dglnM', 'Thunderstruck',               'AC/DC',                 'rock', 'classic', 292, 1500),
        ('8SbUC-UaAxE', 'November Rain',               'Guns N\' Roses',        'rock', 'classic', 537, 1500),
        ('hTWKbfoikeg', 'Smells Like Teen Spirit',     'Nirvana',               'rock', 'grunge',  301, 1600),
        ('1w7OgIMMRc4', 'Sweet Child O\' Mine',        'Guns N\' Roses',        'rock', 'classic', 356, 1400),
        ('iYYRH4apXDo', 'Enter Sandman',               'Metallica',             'rock', 'metal',   330, 1400),
        # Sing-alongs obrigatórios de bar
        ('1k8craCGpgs', 'Don\'t Stop Believin\'',      'Journey',               'rock', 'classic', 251, 600),
        ('lDK9QqIzhwk', 'Livin\' on a Prayer',         'Bon Jovi',              'rock', 'classic', 250, 800),
        ('8UVNT4wvIGY', 'Boulevard of Broken Dreams',  'Green Day',             'rock', '2000s',   261, 800),
        # Peso pesado — emoção garantida
        ('tAGnKpE4NCI', 'Nothing Else Matters',        'Metallica',             'rock', 'metal',   388, 700),
        ('x-xTttimcNk', 'Comfortably Numb',            'Pink Floyd',            'rock', 'classic', 382, 600),
        ('EqPtz5qN7HM', 'Hotel California (Live)',      'Eagles',                'rock', 'classic', 468, 500),
        ('GLvohMXgcBo', 'Under the Bridge',             'Red Hot Chili Peppers', 'rock', '90s',     264, 500),
        ('eBG7P-K-r1Y', 'Everlong',                    'Foo Fighters',          'rock', '90s',     251, 500),
        ('0J2QdDbelmY', 'Seven Nation Army',            'The White Stripes',     'rock', '2000s',   232, 700),
        ('89dGC8de0CA', 'Dream On',                    'Aerosmith',             'rock', 'classic', 263, 400),
        # Brasil — identidade e nostalgia
        ('bAvPpWsMvS0', 'Tempo Perdido',               'Legião Urbana',         'rock', 'brasil',  352, 200),
        ('QcJMGLHjL4s', 'Pais e Filhos',               'Legião Urbana',         'rock', 'brasil',  349, 180),
        ('XstCMbMjyKQ', 'Epitáfio',                    'Titãs',                 'rock', 'brasil',  293, 150),
        # Classics para o bar cantar
        ('ETzSfBDFCMU', 'Simple Man',                  'Lynyrd Skynyrd',        'rock', 'classic', 427, 700),
        ('CqNS2hqFYr8', 'Free Bird',                   'Lynyrd Skynyrd',        'rock', 'classic', 540, 500),
        ('vdMCKB9qAr0', 'Crazy Train',                 'Ozzy Osbourne',         'rock', 'metal',   257, 400),
        ('zUwEWorAMnQ', 'Smoke on the Water',           'Deep Purple',           'rock', 'classic', 337, 350),
        ('uk_wUT1CvWM', 'Paranoid',                    'Black Sabbath',          'rock', 'metal',   173, 400),
        ('wTP2RsmObe4', 'Money for Nothing',            'Dire Straits',          'rock', 'classic', 515, 300),
        ('QkF3oxziUI4', 'Stairway to Heaven',           'Led Zeppelin',          'rock', 'classic', 482, 800),

        # ══════════════════════════════════════════════════════════════════════
        # PUNK / ALTERNATIVO — Energia pura
        # ══════════════════════════════════════════════════════════════════════
        ('9DR9e9frHc4', 'Basket Case',                 'Green Day',             'punk', 'punk',    178, 600),
        ('VN2XlmMGaH8', 'All the Small Things',        'Blink-182',             'punk', 'pop-punk', 168, 500),
        ('kXYiU_JCYtU', 'Numb',                        'Linkin Park',           'punk', 'nu-metal', 187, 700),
        ('JGDsLZpnj9s', 'In the End',                  'Linkin Park',           'punk', 'nu-metal', 219, 1200),
        ('_u2vy1hLEqE', 'Fat Lip',                     'Sum 41',                'punk', 'pop-punk', 207, 300),
        ('CnQ_o4hTsoQ', 'What I Got',                  'Sublime',               'punk', 'ska-punk', 158, 200),
        ('QF-iFtSIYiQ', 'Come as You Are',             'Nirvana',               'punk', 'grunge',   219, 600),
        ('O4erFVLaqlE', 'Sangue Latino',               'Raimundos',             'punk', 'brasil',   195, 50),
        ('bWXazVeUID0', 'My Own Summer',               'Deftones',              'punk', 'nu-metal', 234, 100),
        ('n0H3RRDRyxI', 'Welcome to the Black Parade', 'My Chemical Romance',   'punk', 'emo',      306, 500),

        # ══════════════════════════════════════════════════════════════════════
        # SERTANEJO — Do clássico ao moderno
        # ══════════════════════════════════════════════════════════════════════
        # Raízes — quem tem 35-50 anos conhece de cor
        ('K7mfTi-h10M', 'Evidências',                  'Chitãozinho & Xororó', 'sertanejo', 'raiz', 285, 400),
        ('o4P9U0k7pRI', 'Pense em Mim',               'Leandro & Leonardo',    'sertanejo', 'raiz', 278, 300),
        ('lSk4PqeMjY4', 'É o Amor',                   'Zezé di Camargo',       'sertanejo', 'raiz', 261, 250),
        # Universitário e atual
        ('7G_MskxUqeQ', 'Infiel',                     'Marília Mendonça',      'sertanejo', 'moderno', 230, 700),
        ('L4SJHm3VTBQ', 'Coração de Cowboy',          'Gusttavo Lima',         'sertanejo', 'moderno', 246, 600),
        ('aKAOYPl-y_0', 'Por Enquanto',               'Jorge & Mateus',        'sertanejo', 'moderno', 268, 400),
        ('T2hGgxBTmF4', 'Amor de Verdade',            'Zé Neto & Cristiano',   'sertanejo', 'moderno', 248, 300),
        ('F_7R-VFLPaE', 'Que Pena',                   'Henrique & Juliano',    'sertanejo', 'moderno', 241, 250),
        ('yiDSY7QMB2E', 'Oi Balde',                   'Gusttavo Lima',         'sertanejo', 'moderno', 255, 350),
        ('WmJHiPxLHF8', 'Leva Eu',                    'Luan Santana',          'sertanejo', 'moderno', 253, 200),
        ('L2rqGWgaYrI', 'Vou Pro Sereno',             'Marília Mendonça',      'sertanejo', 'moderno', 245, 180),

        # ══════════════════════════════════════════════════════════════════════
        # PAGODE — Boteco clássico brasileiro
        # ══════════════════════════════════════════════════════════════════════
        ('t8KYJmVVrTs', 'Cheia de Manias',            'Raça Negra',            'pagode', 'classic', 265, 250),
        ('bHvmkRUzxHU', 'Deixa a Vida Me Levar',      'Zeca Pagodinho',        'pagode', 'classic', 248, 300),
        ('K9S6Aev8Jbs', 'Verdade',                    'Zeca Pagodinho',        'pagode', 'classic', 215, 200),
        ('U3x8jAs4q_E', 'Inevitável',                 'Thiaguinho',            'pagode', 'moderno', 237, 180),
        ('HoKKbnPYMi4', 'Boa Sorte pra Você',         'Ferrugem',              'pagode', 'moderno', 249, 120),
        ('MlLWTeApqIM', 'Me Apaixonei pela Pessoa Errada','Sorriso Maroto',    'pagode', 'moderno', 270, 150),
        ('fEHXHJtP3Hs', 'Tá Escrito',                 'Péricles',              'pagode', 'moderno', 243, 90),
        ('zKb6R4QLYSI', 'Amor Maior',                 'Thiaguinho',            'pagode', 'moderno', 232, 200),
        ('8kLAbPXxFgY', 'Esse Cara Sou Eu',           'Roberto Carlos',        'pagode', 'classic', 285, 300),

        # ══════════════════════════════════════════════════════════════════════
        # POP — Os maiores hits de todos os tempos por views
        # ══════════════════════════════════════════════════════════════════════
        # Top mundial — bilhões de views
        ('YqeW9_5kURI', 'Shape of You',               'Ed Sheeran',            'pop', 'atual', 235, 6000),
        ('RBumgq5yVrA', 'Uptown Funk',                'Bruno Mars ft. Mark Ronson','pop','atual',270, 4900),
        ('kTJczUoc26U', 'Blinding Lights',            'The Weeknd',            'pop', 'atual', 200, 4000),
        ('OPf0YbXqDm0', 'Roar',                       'Katy Perry',            'pop', 'atual', 231, 3300),
        ('09R8_2nJtjg', 'Sugar',                      'Maroon 5',              'pop', 'atual', 235, 3000),
        # MJ — Intocáveis
        ('sOnqjkJTMaA', 'Billie Jean',                'Michael Jackson',        'pop', 'classic', 294, 900),
        ('h_D3VFfhvs4', 'Thriller',                   'Michael Jackson',        'pop', 'classic', 857, 800),
        # Adele — emoção pura
        ('YQHsXMglC9A', 'Hello',                      'Adele',                 'pop', 'atual', 295, 3200),
        ('bo_efYLyWrA', 'Rolling in the Deep',        'Adele',                 'pop', 'atual', 228, 2200),
        # Era atual
        ('E07s5ZYygMg', 'As It Was',                  'Harry Styles',          'pop', 'atual', 167, 2000),
        ('SlPhMPnQ58k', 'Envolver',                   'Anitta',                'pop', 'brasil', 185, 800),
        ('JGwWNGJdvx8', 'Happy',                      'Pharrell Williams',      'pop', 'atual', 233, 1400),

        # ══════════════════════════════════════════════════════════════════════
        # F1 / SPEED — Adrenalina
        # ══════════════════════════════════════════════════════════════════════
        # Senna — patrimônio brasileiro
        ('qZsyrJXr6no', 'Ayrton Senna — Greatest Moments','Ayrton Senna',     'f1', 'f1', 480, 150),
        ('wiCCEbkz_IM', 'F1 Greatest Overtakes Ever', 'Formula 1',             'f1', 'f1', 300, 80),
        ('w5MBtVxOBQs', 'Max Verstappen Best Laps',   'Red Bull Racing',        'f1', 'f1', 360, 60),
        # Drift e Gymkhana — insano visual
        ('n_RMXCQZ8Gs', 'Ken Block Gymkhana 10',      'Hoonigan',              'f1', 'drift', 543, 200),
        ('0GiAiRJsYh8', 'Drift — Best Moments',       'D1GP',                  'f1', 'drift', 240, 30),
        # Rally — grupo B era lendária
        ('Aqe_bvkTbJM', 'WRC Rally — Best of All Time','WRC',                  'f1', 'rally', 420, 50),
        # NASCAR
        ('YbHuGPHaURQ', 'NASCAR Greatest Crashes & Moments','NASCAR',          'f1', 'nascar', 360, 40),

        # ══════════════════════════════════════════════════════════════════════
        # FUTEBOL — Os momentos mais vistos da história
        # ══════════════════════════════════════════════════════════════════════
        # Ronaldinho — o melhor de todos os tempos em habilidade
        ('PFivhFVDfhU', 'Ronaldinho Gaúcho — Skills & Goals','Ronaldinho',    'futebol', 'skills', 600, 350),
        # R9 — Ronaldo Fenômeno
        ('lBe1OPSHCqk', 'Ronaldo R9 — Best Goals Ever','Ronaldo Fenômeno',    'futebol', 'goals',  480, 250),
        # Messi vs CR7
        ('VYs4G2eRIgk', 'Messi vs Ronaldo — The GOAT Debate','Compilação',    'futebol', 'goat',   600, 500),
        # Roberto Carlos — cobranças impossíveis
        ('Qa5jSRiVFnA', 'Roberto Carlos — Free Kicks','Roberto Carlos',        'futebol', 'goals',  300, 200),
        # Neymar e Brasil
        ('yh4OmhMnCww', 'Neymar Jr — Skills & Goals','Neymar Jr',             'futebol', 'skills', 480, 200),
        # Melhores gols da história — top compilações
        ('Bln9FJz9k9c', 'Top 50 Goals Champions League','UEFA',               'futebol', 'goals',  600, 100),

        # ══════════════════════════════════════════════════════════════════════
        # SURF — O melhor do mundo
        # ══════════════════════════════════════════════════════════════════════
        ('WwRqc56YQXI', 'Gabriel Medina — Best Rides','Gabriel Medina',        'surf', 'pro', 360, 25),
        ('c8JnDhBHXJM', 'Nazaré — Big Wave Surfing',  'WSL Big Wave',          'surf', 'bigwave', 300, 40),
        ('E_EEwEZPjbg', 'Kelly Slater — Legend',      'Kelly Slater',          'surf', 'pro', 480, 60),
        ('d_QQFqHM3DQ', 'Pipeline — Best Barrels',    'WSL Surf',              'surf', 'barrel', 300, 20),
        ('xRkbCQ5UT_c', 'WSL Best Moments',           'World Surf League',     'surf', 'pro', 420, 15),

        # ══════════════════════════════════════════════════════════════════════
        # AÉREO — Impossível não travar de ver
        # ══════════════════════════════════════════════════════════════════════
        ('vvbMQjGgPEg', 'Felix Baumgartner — Space Jump (24mi de altura)','Red Bull Stratos','aerio','record',220,90),
        ('8CsuH6DDs_U', 'Wingsuit — Flying Through Arch','Red Bull',           'aerio', 'wingsuit', 300, 120),
        ('1dPFEePgFAA', 'Base Jump — World\'s Highest','Red Bull',             'aerio', 'basejump', 240, 80),
        ('B-2tUPGrRaU', 'Aerobatics — Red Bull Air Race','Red Bull',           'aerio', 'plane', 360, 40),

        # ══════════════════════════════════════════════════════════════════════
        # RADICAL — Extremo ao máximo
        # ══════════════════════════════════════════════════════════════════════
        ('Bw-VFzFD5kM', 'X-Games — Best Skate Moments','X-Games',             'radical', 'skate', 300, 50),
        ('hDFn8AnPX70', 'BMX — Best Tricks Ever',     'Red Bull BMX',          'radical', 'bmx', 240, 40),
        ('MObp71DNOJM', 'Motocross — Insane Moments', 'Red Bull Motocross',    'radical', 'moto', 360, 35),
        ('oEfLUDiCMB0', 'Mountain Bike — Red Bull Rampage','Red Bull MTB',     'radical', 'mtb', 300, 30),

        # ══════════════════════════════════════════════════════════════════════
        # ROCK SHOWS — Os maiores shows da história do rock
        # ══════════════════════════════════════════════════════════════════════
        # Queen Live Aid 1985 — considerado o melhor show de todos os tempos
        ('8gKBz77GFB4', 'Queen — Live Aid 1985 (Freddie Mercury)',
                                                       'Queen',                'show_rock', 'lendario', 1200, 500),
        # Metallica Moscou 1991 — 500.000 pessoas
        ('rf0gudLiwBk', 'Metallica — Moscou 1991 (500k pessoas)',
                                                       'Metallica',            'show_rock', 'lendario', 1800, 500),
        # AC/DC River Plate
        ('gIDwZKPMEdg', 'AC/DC — River Plate 2009',   'AC/DC',                'show_rock', 'lendario', 1800, 200),
        # Guns N Roses
        ('Q6tqSWjmqj0', 'Guns N\' Roses — Rock in Rio','Guns N\' Roses',      'show_rock', 'classico', 600, 150),
        # Legião Urbana — cult no Brasil
        ('9jK-NcRmVcw', 'Legião Urbana — Ao Vivo Brasília','Legião Urbana',   'show_rock', 'brasil', 600, 80),
        # Pink Floyd — The Wall / Pulse
        ('x-xTttimcNk', 'Pink Floyd — Pulse Concert', 'Pink Floyd',           'show_rock', 'lendario', 600, 300),

        # ══════════════════════════════════════════════════════════════════════
        # SERTANEJO SHOWS — Grandes palcos
        # ══════════════════════════════════════════════════════════════════════
        ('7mRdJRbzJow', 'Gusttavo Lima — Ao Vivo',    'Gusttavo Lima',        'show_sertanejo', 'atual', 1800, 200),
        ('xQkPHoJiuBE', 'Marília Mendonça — Ao Vivo', 'Marília Mendonça',     'show_sertanejo', 'atual', 1800, 300),
        ('EJnV78LtoKA', 'Jorge & Mateus — Live',      'Jorge & Mateus',       'show_sertanejo', 'atual', 1800, 150),
        ('SJc1S2IlONM', 'Henrique & Juliano — Ao Vivo','Henrique & Juliano',  'show_sertanejo', 'atual', 1800, 100),

        # ══════════════════════════════════════════════════════════════════════
        # PAGODE SHOWS — Boteco pra arena
        # ══════════════════════════════════════════════════════════════════════
        ('JqHC4cjDH38', 'Thiaguinho — Tardezinha Ao Vivo','Thiaguinho',       'show_pagode', 'atual', 1800, 100),
        ('m2pt9sOlG_c', 'Péricles — Ao Vivo',         'Péricles',             'show_pagode', 'atual', 1800, 60),
        ('9sNGCZpuKq8', 'Pagodão de Boteco — Clássicos','Vários',             'show_pagode', 'classico', 1800, 50),
    ]

    conn.executemany(
        '''INSERT OR IGNORE INTO pubshow_videos
           (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, views_milhoes)
           VALUES (?,?,?,?,?,?,?)''',
        videos
    )
    conn.commit()
