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
        # ROCK — 50+ vídeos — clássicos eternos + sing-alongs de bar
        # ══════════════════════════════════════════════════════════════════════
        # Queens of rock — obrigatórios
        ('fJ9rUzIMcZQ', 'Bohemian Rhapsody',           'Queen',                 'rock', 'classic', 354, 1900),
        ('HgzGwKwLmgM', 'Don\'t Stop Me Now',          'Queen',                 'rock', 'classic', 210, 800),
        ('-tJYN-eG1zk', 'We Will Rock You',            'Queen',                 'rock', 'classic', 122, 700),
        ('D_uhlBOhCxs', 'We Are the Champions',        'Queen',                 'rock', 'classic', 180, 600),
        # AC/DC — energia de boteco
        ('v2AC41dglnM', 'Thunderstruck',               'AC/DC',                 'rock', 'classic', 292, 1500),
        ('l482T0yNkeo', 'Highway to Hell',             'AC/DC',                 'rock', 'classic', 208, 900),
        ('_CL6n0FJZpk', 'Back in Black',               'AC/DC',                 'rock', 'classic', 254, 800),
        ('HQmmM_qwG4k', 'You Shook Me All Night Long', 'AC/DC',                 'rock', 'classic', 210, 700),
        # GNR
        ('8SbUC-UaAxE', 'November Rain',               'Guns N\' Roses',        'rock', 'classic', 537, 1500),
        ('1w7OgIMMRc4', 'Sweet Child O\' Mine',        'Guns N\' Roses',        'rock', 'classic', 356, 1400),
        ('o1tj2zJ2Wvg', 'Welcome to the Jungle',       'Guns N\' Roses',        'rock', 'classic', 272, 700),
        ('Rbm6GXllroo', 'Paradise City',               'Guns N\' Roses',        'rock', 'classic', 407, 600),
        # Metallica
        ('iYYRH4apXDo', 'Enter Sandman',               'Metallica',             'rock', 'metal',   330, 1400),
        ('tAGnKpE4NCI', 'Nothing Else Matters',        'Metallica',             'rock', 'metal',   388, 700),
        ('ClYdR4W_K8g', 'Master of Puppets',           'Metallica',             'rock', 'metal',   515, 600),
        ('WM8bTdBs-cw', 'One',                         'Metallica',             'rock', 'metal',   447, 500),
        # Led Zeppelin
        ('QkF3oxziUI4', 'Stairway to Heaven',          'Led Zeppelin',          'rock', 'classic', 482, 800),
        ('BcL---4xQYA', 'Whole Lotta Love',            'Led Zeppelin',          'rock', 'classic', 334, 400),
        # Pink Floyd
        ('x-xTttimcNk', 'Comfortably Numb',            'Pink Floyd',            'rock', 'classic', 382, 600),
        ('YR5ApYxkU-U', 'Another Brick in the Wall',   'Pink Floyd',            'rock', 'classic', 240, 500),
        ('Ojd_MZTV0Tk', 'Wish You Were Here',          'Pink Floyd',            'rock', 'classic', 313, 400),
        # Nirvana
        ('hTWKbfoikeg', 'Smells Like Teen Spirit',     'Nirvana',               'rock', 'grunge',  301, 1600),
        ('QF-iFtSIYiQ', 'Come as You Are',             'Nirvana',               'rock', 'grunge',  219, 600),
        ('PVyS9JwtFoQ', 'Heart-Shaped Box',            'Nirvana',               'rock', 'grunge',  278, 400),
        # Eagles / Journey / Bon Jovi — sing-alongs
        ('EqPtz5qN7HM', 'Hotel California (Live)',      'Eagles',                'rock', 'classic', 468, 500),
        ('1k8craCGpgs', 'Don\'t Stop Believin\'',      'Journey',               'rock', 'classic', 251, 600),
        ('lDK9QqIzhwk', 'Livin\' on a Prayer',         'Bon Jovi',              'rock', 'classic', 250, 800),
        ('y_gkFi3ynkI', 'Wanted Dead or Alive',        'Bon Jovi',              'rock', 'classic', 340, 400),
        # RHCP / Foo / White Stripes
        ('GLvohMXgcBo', 'Under the Bridge',             'Red Hot Chili Peppers', 'rock', '90s',    264, 500),
        ('YlUKcNNmywk', 'Californication',              'Red Hot Chili Peppers', 'rock', '90s',    322, 400),
        ('eBG7P-K-r1Y', 'Everlong',                    'Foo Fighters',          'rock', '90s',     251, 500),
        ('LnFHBaXBFSI', 'Best of You',                 'Foo Fighters',          'rock', '2000s',   256, 300),
        ('0J2QdDbelmY', 'Seven Nation Army',            'The White Stripes',     'rock', '2000s',   232, 700),
        # Clássicos anos 80/70 — nostalgia garantida
        ('89dGC8de0CA', 'Dream On',                    'Aerosmith',             'rock', 'classic', 263, 400),
        ('oRdxUFDoQe0', 'Beat It',                     'Michael Jackson',       'rock', 'pop-rock', 258, 600),
        ('btPJPFnesV4', 'Eye of the Tiger',            'Survivor',              'rock', 'classic', 245, 700),
        ('n4RjJKxsamQ', 'Wind of Change',              'Scorpions',             'rock', 'classic', 310, 500),
        ('zUwEWorAMnQ', 'Smoke on the Water',          'Deep Purple',           'rock', 'classic', 337, 350),
        ('uk_wUT1CvWM', 'Paranoid',                    'Black Sabbath',         'rock', 'metal',   173, 400),
        ('vdMCKB9qAr0', 'Crazy Train',                 'Ozzy Osbourne',         'rock', 'metal',   257, 400),
        ('wTP2RsmObe4', 'Money for Nothing',            'Dire Straits',          'rock', 'classic', 515, 300),
        ('XmSdTa9kaiQ', 'With or Without You',         'U2',                    'rock', 'classic', 296, 500),
        ('3FsrPEUt5Xc', 'Where the Streets Have No Name','U2',                  'rock', 'classic', 337, 400),
        ('3mbBbFH9fAg', 'Black Hole Sun',              'Soundgarden',           'rock', 'grunge',  320, 300),
        ('ETzSfBDFCMU', 'Simple Man',                  'Lynyrd Skynyrd',        'rock', 'classic', 427, 700),
        ('CqNS2hqFYr8', 'Free Bird',                   'Lynyrd Skynyrd',        'rock', 'classic', 540, 500),
        ('8UVNT4wvIGY', 'Boulevard of Broken Dreams',  'Green Day',             'rock', '2000s',   261, 800),
        ('pv-GLUvH_hQ', 'Seven Nation Army (Live)',    'The White Stripes',     'rock', 'live',    232, 300),
        # Brasil — alma do rock nacional
        ('bAvPpWsMvS0', 'Tempo Perdido',               'Legião Urbana',         'rock', 'brasil',  352, 200),
        ('QcJMGLHjL4s', 'Pais e Filhos',               'Legião Urbana',         'rock', 'brasil',  349, 180),
        ('XstCMbMjyKQ', 'Epitáfio',                    'Titãs',                 'rock', 'brasil',  293, 150),
        ('Iy8MzpyMqrw', 'O Tempo Não Para',            'Cazuza',                'rock', 'brasil',  247, 120),
        ('GHBbNmGGWYA', 'Que País É Este',             'Legião Urbana',         'rock', 'brasil',  224, 100),
        ('K5raBKEOoGw', 'Alagados',                    'Paralamas do Sucesso',  'rock', 'brasil',  273,  80),
        ('VBfQGiD1esc', 'Pro Dia Nascer Feliz',        'Barão Vermelho',        'rock', 'brasil',  258,  70),

        # ══════════════════════════════════════════════════════════════════════
        # PUNK / ALTERNATIVO — 50+ vídeos — energia, identidade, nostalgia
        # ══════════════════════════════════════════════════════════════════════
        # Green Day — raiz do punk moderno
        ('9DR9e9frHc4', 'Basket Case',                 'Green Day',             'punk', 'punk',     178, 600),
        ('Ee_uujKuJMI', 'American Idiot',              'Green Day',             'punk', 'punk',     174, 700),
        ('NU9JoFKlaHU', 'Wake Me Up When September Ends','Green Day',           'punk', 'punk',     285, 600),
        ('hDlHpBNcQTs', 'Holiday',                     'Green Day',             'punk', 'punk',     230, 400),
        ('kPmNpMDOHnY', 'Good Riddance (Time of Your Life)','Green Day',        'punk', 'punk',     153, 500),
        # Blink-182 — pop-punk que todo mundo sabe
        ('VN2XlmMGaH8', 'All the Small Things',        'Blink-182',             'punk', 'pop-punk', 168, 500),
        ('K0gOEm3SNUM', 'What\'s My Age Again?',       'Blink-182',             'punk', 'pop-punk', 149, 400),
        ('_wNPd3PBbkA', 'Dammit',                      'Blink-182',             'punk', 'pop-punk', 170, 300),
        ('8Puih-R8DiU', 'I Miss You',                  'Blink-182',             'punk', 'emo',      227, 400),
        # Linkin Park — nu-metal que marcou geração
        ('kXYiU_JCYtU', 'Numb',                        'Linkin Park',           'punk', 'nu-metal', 187, 700),
        ('JGDsLZpnj9s', 'In the End',                  'Linkin Park',           'punk', 'nu-metal', 219, 1200),
        ('Gd9OhYroLN0', 'Crawling',                    'Linkin Park',           'punk', 'nu-metal', 210, 400),
        ('eVTXPUF4Oz4', 'Breaking the Habit',          'Linkin Park',           'punk', 'nu-metal', 196, 350),
        ('4qlCC1GOwFw', 'What I\'ve Done',             'Linkin Park',           'punk', 'nu-metal', 215, 400),
        ('FSsppGgPMEk', 'Somewhere I Belong',          'Linkin Park',           'punk', 'nu-metal', 214, 350),
        # Sum 41 / The Offspring / Sublime
        ('_u2vy1hLEqE', 'Fat Lip',                     'Sum 41',                'punk', 'pop-punk', 207, 300),
        ('1CR0QmKDvqI', 'In Too Deep',                 'Sum 41',                'punk', 'pop-punk', 210, 250),
        ('4fndeDfaWCg', 'Come Out and Play',           'The Offspring',         'punk', 'punk',     171, 300),
        ('CsGd-fOYUYI', 'Pretty Fly (for a White Guy)','The Offspring',        'punk', 'punk',     193, 400),
        ('CnQ_o4hTsoQ', 'What I Got',                  'Sublime',               'punk', 'ska-punk', 158, 200),
        # MCR / Paramore / Evanescence — emo era
        ('n0H3RRDRyxI', 'Welcome to the Black Parade', 'My Chemical Romance',   'punk', 'emo',      306, 500),
        ('UCIjdfjcSO0', 'Helena',                      'My Chemical Romance',   'punk', 'emo',      196, 400),
        ('aCyGvGEtOwc', 'Misery Business',             'Paramore',              'punk', 'emo',      224, 500),
        ('6vBR0_UjuuM', 'Still Into You',              'Paramore',              'punk', 'pop-punk', 222, 600),
        ('3YxaaGgTQYM', 'Bring Me to Life',            'Evanescence',           'punk', 'emo',      223, 600),
        ('nSD-TIXaLGI', 'My Immortal',                 'Evanescence',           'punk', 'emo',      220, 400),
        # The Killers / Arctic Monkeys — indie alternativo
        ('gGdGFtwCNBE', 'Mr. Brightside',              'The Killers',           'punk', 'indie',    222, 700),
        ('RIZdjT9SCNU', 'Human',                       'The Killers',           'punk', 'indie',    237, 500),
        ('bpOSxM0UIJ4', 'Do I Wanna Know?',            'Arctic Monkeys',        'punk', 'indie',    272, 700),
        ('oaO1YaK_0cQ', 'R U Mine?',                  'Arctic Monkeys',        'punk', 'indie',    202, 400),
        ('VaMfDOq0iao', 'Fluorescent Adolescent',      'Arctic Monkeys',        'punk', 'indie',    177, 300),
        # Panic! / Fall Out Boy — pop-punk 2000s
        ('cGdT9bEMhNg', 'I Write Sins Not Tragedies', 'Panic! at the Disco',   'punk', 'pop-punk', 196, 500),
        ('vA_-0MJSxuM', 'Sugar We\'re Goin Down',     'Fall Out Boy',          'punk', 'pop-punk', 211, 400),
        # Avril Lavigne — nostalgia
        ('TIhy4u1cM6M', 'Sk8er Boi',                  'Avril Lavigne',         'punk', 'pop-punk', 204, 500),
        ('5NPBIwQyPWE', 'Complicated',                 'Avril Lavigne',         'punk', 'pop-punk', 244, 400),
        # Nirvana extra / Deftones / alternativo
        ('QF-iFtSIYiQ', 'Come as You Are',             'Nirvana',               'punk', 'grunge',   219, 600),
        ('bWXazVeUID0', 'My Own Summer',               'Deftones',              'punk', 'nu-metal',  234, 100),
        # Ramones / Clash / Sex Pistols — raiz histórica
        ('wS2i87Sn9K0', 'Blitzkrieg Bop',             'Ramones',               'punk', 'punk',      170, 200),
        ('gNaUmFCMkAE', 'Should I Stay or Should I Go','The Clash',            'punk', 'punk',      183, 300),
        ('7lQMnMHFVgQ', 'London Calling',              'The Clash',             'punk', 'punk',      199, 200),
        # Brasil punk/rock alternativo
        ('O4erFVLaqlE', 'Sangue Latino',               'Raimundos',             'punk', 'brasil',   195,  50),
        ('Wg8cg5rBhkE', 'Olhos Certos',               'Detonautas Roque Clube','punk', 'brasil',   234,  40),
        ('pYxW8VbHpQM', 'Proibida pra Mim',           'Charlie Brown Jr',      'punk', 'brasil',   248,  60),
        ('d0iCJlmqQs4', 'Ela Vai Voltar',             'Charlie Brown Jr',      'punk', 'brasil',   221,  50),
        ('TGjn0bFB8HU', 'Garota Nacional',            'Skank',                 'punk', 'brasil',   241,  70),

        # ══════════════════════════════════════════════════════════════════════
        # SERTANEJO — 50+ vídeos — da raiz ao moderno
        # ══════════════════════════════════════════════════════════════════════
        # Raízes — décadas de 80/90 — quem tem 35+ anos conhece de cor
        ('K7mfTi-h10M', 'Evidências',                  'Chitãozinho & Xororó',  'sertanejo','raiz',  285, 400),
        ('bHFpSH_GDFY', 'Fio de Cabelo',              'Chitãozinho & Xororó',  'sertanejo','raiz',  258, 300),
        ('o4P9U0k7pRI', 'Pense em Mim',               'Leandro & Leonardo',    'sertanejo','raiz',  278, 300),
        ('yvpXVoGh7ew', 'Temporal de Amor',           'Leandro & Leonardo',    'sertanejo','raiz',  261, 200),
        ('2bFP4WBIP4M', 'Tudo de Bom',               'Leandro & Leonardo',    'sertanejo','raiz',  244, 200),
        ('lSk4PqeMjY4', 'É o Amor',                   'Zezé di Camargo',       'sertanejo','raiz',  261, 250),
        ('o7CYfN6YVas', 'Dois Amores',                'Zezé di Camargo',       'sertanejo','raiz',  248, 180),
        ('HpJDCiEMFVI', 'Estrada da Vida',            'João Paulo & Daniel',   'sertanejo','raiz',  270, 150),
        # Universitário anos 2000-2010
        ('nrTX7jLmgQI', 'Balada (Tchê Tchê Rere)',    'Gusttavo Lima',         'sertanejo','universitario', 243, 900),
        ('L4SJHm3VTBQ', 'Coração de Cowboy',          'Gusttavo Lima',         'sertanejo','universitario', 246, 600),
        ('yiDSY7QMB2E', 'Oi Balde',                   'Gusttavo Lima',         'sertanejo','universitario', 255, 350),
        ('gFYqcZNyMhA', 'Bloqueio',                   'Gusttavo Lima',         'sertanejo','universitario', 238, 300),
        ('xQjFTuHVoik', 'Fui Fiel',                   'Gusttavo Lima',         'sertanejo','universitario', 252, 250),
        ('aKAOYPl-y_0', 'Por Enquanto',               'Jorge & Mateus',        'sertanejo','universitario', 268, 400),
        ('M-lNVHMb82A', 'Uai',                        'Jorge & Mateus',        'sertanejo','universitario', 235, 300),
        ('jqnGBjGGPwI', 'Me Encontra',                'Jorge & Mateus',        'sertanejo','universitario', 241, 250),
        ('WmJHiPxLHF8', 'Leva Eu',                    'Luan Santana',          'sertanejo','universitario', 253, 200),
        ('8fhBwMqFCBo', 'Te Esperando',               'Luan Santana',          'sertanejo','universitario', 247, 180),
        ('F_7R-VFLPaE', 'Que Pena',                   'Henrique & Juliano',    'sertanejo','universitario', 241, 250),
        ('jNfPKuRgHnU', 'Cuida Bem Dela',             'Henrique & Juliano',    'sertanejo','universitario', 248, 220),
        ('zPfjBqsMGPg', 'Liberdade Provisória',       'Henrique & Juliano',    'sertanejo','universitario', 239, 200),
        ('T2hGgxBTmF4', 'Amor de Verdade',            'Zé Neto & Cristiano',   'sertanejo','universitario', 248, 300),
        ('HuiDfIxKH48', 'Largado às Traças',          'Zé Neto & Cristiano',   'sertanejo','universitario', 235, 400),
        ('qVkJi6R96Wk', 'Notificação Preferida',      'Zé Neto & Cristiano',   'sertanejo','universitario', 228, 250),
        # Feminejo — Marília Mendonça e geração
        ('7G_MskxUqeQ', 'Infiel',                     'Marília Mendonça',      'sertanejo','feminejo',  230, 700),
        ('L2rqGWgaYrI', 'Vou Pro Sereno',             'Marília Mendonça',      'sertanejo','feminejo',  245, 180),
        ('8oQcPiF3fLs', 'Graveto',                    'Marília Mendonça',      'sertanejo','feminejo',  221, 200),
        ('L9bnx1WqbG4', 'Supera',                     'Marília Mendonça',      'sertanejo','feminejo',  234, 250),
        ('A2qFQ1pDuI4', 'Todo Mundo Vai Sofrer',      'Marília Mendonça',      'sertanejo','feminejo',  248, 200),
        ('JlQDl7GRHUU', '10%',                        'Maiara & Maraisa',      'sertanejo','feminejo',  219, 300),
        ('OVfq95RNZPU', 'Medo Bobo',                  'Maiara & Maraisa',      'sertanejo','feminejo',  213, 250),
        # Nova geração 2020+
        ('9dEFg0HvBtg', 'Boiadeira',                  'Ana Castela',           'sertanejo','atual',     193, 400),
        ('hjRlDlBVjJI', 'Pipoco',                     'Ana Castela',           'sertanejo','atual',     185, 350),
        ('AEnwMvxkdWM', '50 Reais',                   'Naiara Azevedo',        'sertanejo','atual',     208, 500),
        ('yRxLGBJYBvo', 'Esqueci de Esquecer',        'Dilsinho',              'sertanejo','atual',     217, 300),
        ('ej7-f_XhAh4', 'Bombonzinho',                'Israel & Rodolffo',     'sertanejo','atual',     195, 250),
        ('UJe8X-IM2Lg', 'Dona de Mim',               'Simone & Simaria',      'sertanejo','atual',     223, 200),
        ('zMOPL5gRFM8', 'Regime Fechado',             'Simone & Simaria',      'sertanejo','atual',     218, 180),
        ('TQB0YX8hqrI', 'Aqui e Agora',              'Victor & Leo',           'sertanejo','atual',     231, 150),
        ('qYqkQ7BPKHY', 'Borboletas',                'Victor & Leo',           'sertanejo','atual',     245, 130),

        # ══════════════════════════════════════════════════════════════════════
        # PAGODE — 50+ vídeos — boteco clássico até pagode moderno
        # ══════════════════════════════════════════════════════════════════════
        # Zeca Pagodinho — rei do boteco
        ('bHvmkRUzxHU', 'Deixa a Vida Me Levar',      'Zeca Pagodinho',        'pagode','classic',  248, 300),
        ('K9S6Aev8Jbs', 'Verdade',                    'Zeca Pagodinho',        'pagode','classic',  215, 200),
        ('m0GIQQjAWpc', 'Maneira',                    'Zeca Pagodinho',        'pagode','classic',  227, 150),
        ('fxJPVbOKGdM', 'Vai Vadiar',                 'Zeca Pagodinho',        'pagode','classic',  241, 130),
        ('3pbyDhVU6to', 'Camarão que Dorme a Onda Leva','Zeca Pagodinho',      'pagode','classic',  218, 120),
        # Raça Negra — pagode dos anos 90
        ('t8KYJmVVrTs', 'Cheia de Manias',            'Raça Negra',            'pagode','classic',  265, 250),
        ('xrjPP6BVPHU', 'O Amor',                     'Raça Negra',            'pagode','classic',  248, 150),
        ('fJLfMXTiN40', 'Fruto Proibido',             'Raça Negra',            'pagode','classic',  235, 130),
        # Roberto Carlos — popular e nostálgico
        ('8kLAbPXxFgY', 'Esse Cara Sou Eu',           'Roberto Carlos',        'pagode','classic',  285, 300),
        ('oFRbp9Y7xgA', 'Emoções',                   'Roberto Carlos',        'pagode','classic',  267, 200),
        # Thiaguinho — pagode moderno
        ('U3x8jAs4q_E', 'Inevitável',                 'Thiaguinho',            'pagode','moderno',  237, 180),
        ('zKb6R4QLYSI', 'Amor Maior',                 'Thiaguinho',            'pagode','moderno',  232, 200),
        ('mxqDmfJiR0k', 'Deixa Chegar',               'Thiaguinho',            'pagode','moderno',  229, 160),
        ('TkBO0oL3JD8', 'Fica',                       'Thiaguinho',            'pagode','moderno',  218, 150),
        ('W4WYhWnQFpA', 'Te Vejo de Longe',           'Thiaguinho',            'pagode','moderno',  225, 130),
        # Ferrugem — voz marcante
        ('HoKKbnPYMi4', 'Boa Sorte pra Você',         'Ferrugem',              'pagode','moderno',  249, 120),
        ('MFXZ5nX9uXw', 'Tudo de Bom',               'Ferrugem',              'pagode','moderno',  238, 100),
        ('VpPjctLbPDg', 'Nosso Sonho',                'Ferrugem',              'pagode','moderno',  231, 90),
        # Sorriso Maroto
        ('MlLWTeApqIM', 'Me Apaixonei pela Pessoa Errada','Sorriso Maroto',    'pagode','moderno',  270, 150),
        ('TcsPkFsq3Gg', 'Com Você',                   'Sorriso Maroto',        'pagode','moderno',  241, 120),
        ('gEa0Q-5QmOc', 'Pra te Ter',                'Sorriso Maroto',        'pagode','moderno',  235, 100),
        # Péricles
        ('fEHXHJtP3Hs', 'Tá Escrito',                 'Péricles',              'pagode','moderno',  243,  90),
        ('oqRZInf4pF8', 'Preferência',                'Péricles',              'pagode','moderno',  237,  80),
        ('vv6tFPWEpU4', 'Esse Alguém Sou Eu',        'Péricles',              'pagode','moderno',  228,  70),
        # Belo — romantismo
        ('kD3BoGFAOrA', 'Perfume',                    'Belo',                  'pagode','moderno',  252, 100),
        ('lDKrqFWKyLw', 'Boa Noite',                  'Belo',                  'pagode','moderno',  244,  90),
        # Mumuzinho
        ('fG4JYPt8c-A', 'Antes de Você',              'Mumuzinho',             'pagode','moderno',  237,  80),
        # Dilsinho
        ('OTXe-4yCJow', 'Passado',                    'Dilsinho',              'pagode','moderno',  241,  75),
        # Grupo Revelação
        ('KJqhGkTqmqE', 'Swingando',                  'Grupo Revelação',       'pagode','classic',  248, 110),
        ('W3wElWJxbS4', 'Volta pra Mim',              'Grupo Revelação',       'pagode','classic',  235,  90),
        # Exaltasamba
        ('1v4pL8bfG0c', 'Preciso te Encontrar',       'Exaltasamba',           'pagode','classic',  252, 100),
        ('5e6fQToZSWI', 'Faz Parte',                  'Exaltasamba',           'pagode','classic',  241,  85),
        # Seu Jorge — sambajazz
        ('1hzDdFUhNgM', 'Tive Razão',                 'Seu Jorge',             'pagode','mpb',      253,  80),
        # Rodriguinho
        ('mA80kMjKMak', 'Cheia de Marra',             'Rodriguinho',           'pagode','moderno',  229,  70),
        # Pagode roots — Fundo de Quintal
        ('xCF7QRhm3-Y', 'Minha Liberdade',            'Fundo de Quintal',      'pagode','roots',    242,  60),

        # ══════════════════════════════════════════════════════════════════════
        # POP — 50+ vídeos — os maiores de todos os tempos
        # ══════════════════════════════════════════════════════════════════════
        # Michael Jackson — intocável
        ('sOnqjkJTMaA', 'Billie Jean',                'Michael Jackson',        'pop', 'classic', 294, 900),
        ('h_D3VFfhvs4', 'Thriller',                   'Michael Jackson',        'pop', 'classic', 857, 800),
        ('oRdxUFDoQe0', 'Beat It',                    'Michael Jackson',        'pop', 'classic', 258, 600),
        ('Os0pqUbElGk', 'Smooth Criminal',            'Michael Jackson',        'pop', 'classic', 259, 500),
        # Ed Sheeran — recordes de views
        ('YqeW9_5kURI', 'Shape of You',               'Ed Sheeran',            'pop', 'atual', 235, 6000),
        ('lp-EJBh1v6E', 'Thinking Out Loud',          'Ed Sheeran',            'pop', 'atual', 281, 3000),
        ('JMjAd8VDe-A', 'Perfect',                    'Ed Sheeran',            'pop', 'atual', 263, 2800),
        ('iLnmTe5Q2Qw', 'Bad Habits',                 'Ed Sheeran',            'pop', 'atual', 231, 1500),
        # Bruno Mars
        ('RBumgq5yVrA', 'Uptown Funk',                'Bruno Mars',            'pop', 'atual', 270, 4900),
        ('LjhCEyeSahE', 'Just the Way You Are',       'Bruno Mars',            'pop', 'atual', 220, 1800),
        ('OPf0YbXqDm0', 'Roar',                       'Katy Perry',            'pop', 'atual', 231, 3300),
        ('QGJuMBdaqIw', 'Firework',                   'Katy Perry',            'pop', 'atual', 228, 1400),
        ('9fxm6dSUWZQ', 'Teenage Dream',              'Katy Perry',            'pop', 'atual', 224, 700),
        # The Weeknd — bilhões
        ('kTJczUoc26U', 'Blinding Lights',            'The Weeknd',            'pop', 'atual', 200, 4000),
        ('34Na4j8AVgA', 'Starboy',                    'The Weeknd',            'pop', 'atual', 230, 2000),
        ('KEI4qSrkPAs', 'Can\'t Feel My Face',        'The Weeknd',            'pop', 'atual', 213, 1500),
        # Adele — emoção
        ('YQHsXMglC9A', 'Hello',                      'Adele',                 'pop', 'atual', 295, 3200),
        ('bo_efYLyWrA', 'Rolling in the Deep',        'Adele',                 'pop', 'atual', 228, 2200),
        ('_oimAFbBVsY', 'Someone Like You',           'Adele',                 'pop', 'atual', 285, 1800),
        # Taylor Swift
        ('nfWlot6h_JM', 'Shake It Off',               'Taylor Swift',          'pop', 'atual', 219, 3500),
        ('e-ORhEE9VVg', 'Blank Space',                'Taylor Swift',          'pop', 'atual', 237, 3000),
        ('8xg3vE8Ie_E', 'Love Story',                 'Taylor Swift',          'pop', 'atual', 234, 1200),
        # Beyoncé
        ('4m1EFMoRFvY', 'Single Ladies',              'Beyoncé',               'pop', 'atual', 200, 900),
        ('ViwtNLUqkMY', 'Crazy in Love',              'Beyoncé',               'pop', 'atual', 236, 700),
        # Dua Lipa
        ('TUVcZfQe-Kw', 'Levitating',                 'Dua Lipa',              'pop', 'atual', 203, 1500),
        ('oygrmJFkYEM', 'Don\'t Start Now',           'Dua Lipa',              'pop', 'atual', 183, 1200),
        ('k2qgadSvNyU', 'New Rules',                  'Dua Lipa',              'pop', 'atual', 210, 1400),
        # Lady Gaga
        ('qrO4YZeyl0I', 'Bad Romance',                'Lady Gaga',             'pop', 'atual', 294, 1400),
        ('2Abk1W5dCoQ', 'Just Dance',                 'Lady Gaga',             'pop', 'atual', 244, 800),
        ('CevxZvSJLk8', 'Poker Face',                 'Lady Gaga',             'pop', 'atual', 237, 700),
        # Imagine Dragons — energia
        ('7wtfhZwyrcc', 'Believer',                   'Imagine Dragons',        'pop', 'atual', 204, 1800),
        ('ktvTqknDobU', 'Radioactive',                'Imagine Dragons',        'pop', 'atual', 187, 1400),
        ('mWRsgZuwf_8', 'Demons',                     'Imagine Dragons',        'pop', 'atual', 177, 1000),
        # Coldplay — emoção garantida
        ('yKNxeF4KMsY', 'Yellow',                     'Coldplay',              'pop', 'atual', 270, 800),
        ('k4V3Mo61fJM', 'Fix You',                    'Coldplay',              'pop', 'atual', 295, 900),
        ('dvgZkm1xWPE', 'The Scientist',              'Coldplay',              'pop', 'atual', 309, 700),
        # Maroon 5
        ('09R8_2nJtjg', 'Sugar',                      'Maroon 5',              'pop', 'atual', 235, 3000),
        ('9FKjpFLbJBo', 'Moves Like Jagger',          'Maroon 5',              'pop', 'atual', 201, 1200),
        ('i3hab_ATbEg', 'Animals',                    'Maroon 5',              'pop', 'atual', 231, 1000),
        # Clássicos eternos
        ('E07s5ZYygMg', 'As It Was',                  'Harry Styles',          'pop', 'atual', 167, 2000),
        ('JGwWNGJdvx8', 'Happy',                      'Pharrell Williams',      'pop', 'atual', 233, 1400),
        ('09R8_2nJtjg', 'Sugar (Acoustic)',            'Maroon 5',              'pop', 'atual', 235, 800),
        ('xFrGuyw1V8s', 'Dancing Queen',              'ABBA',                  'pop', 'classic', 230, 500),
        ('unfzfe8f9NI', 'Mamma Mia',                  'ABBA',                  'pop', 'classic', 213, 400),
        ('e7tSeQjAJ3Y', 'I Will Always Love You',     'Whitney Houston',        'pop', 'classic', 273, 900),
        ('bo_efYLyWrA', 'I Have Nothing',             'Whitney Houston',        'pop', 'classic', 272, 400),
        # Brasil pop
        ('SlPhMPnQ58k', 'Envolver',                   'Anitta',                'pop', 'brasil', 185, 800),
        ('qB3kK0QTe2g', 'Girl from Rio',              'Anitta',                'pop', 'brasil', 193, 400),
        ('ZbZSe6N_BXs', 'Happy',                      'Pharrell Williams',      'pop', 'atual', 233, 700),
        ('CevxZvSJLk8', 'Poker Face (Remix)',         'Lady Gaga',             'pop', 'atual', 237, 600),

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
