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
        # Música específica — cliente escolhe o vídeo
        'ALTER TABLE pubshow_pedidos ADD COLUMN youtube_id TEXT',
        'ALTER TABLE pubshow_pedidos ADD COLUMN titulo_pedido TEXT',
        'ALTER TABLE pubshow_pedidos ADD COLUMN thumb_url TEXT',
        # QR Code com token rotativo — separado do code da TV
        'ALTER TABLE pubshow_businesses ADD COLUMN jukebox_token TEXT',
        # Admin pode suspender um bar
        'ALTER TABLE pubshow_businesses ADD COLUMN suspenso INTEGER DEFAULT 0',
        'ALTER TABLE pubshow_businesses ADD COLUMN notas_admin TEXT',
        # Configurações do painel do bar
        'ALTER TABLE pubshow_businesses ADD COLUMN jukebox_hora_ini TEXT DEFAULT "00:00"',
        'ALTER TABLE pubshow_businesses ADD COLUMN jukebox_hora_fim TEXT DEFAULT "23:59"',
        'ALTER TABLE pubshow_businesses ADD COLUMN tipos_bloqueados TEXT DEFAULT "[]"',
        'ALTER TABLE pubshow_businesses ADD COLUMN mensagem_jukebox TEXT',
        'ALTER TABLE pubshow_businesses ADD COLUMN aviso_jukebox TEXT',
        'ALTER TABLE pubshow_businesses ADD COLUMN aviso_expira TEXT',
        'ALTER TABLE pubshow_businesses ADD COLUMN limite_pedidos_hora INTEGER DEFAULT 10',
        'ALTER TABLE pubshow_businesses ADD COLUMN precos_custom TEXT DEFAULT "{}"',
        # Pedidos guardam IP para limite anti-spam
        'ALTER TABLE pubshow_pedidos ADD COLUMN ip_cliente TEXT',
        # PIX verification — pedido fica aguardando_pix até bar confirmar
        'ALTER TABLE pubshow_pedidos ADD COLUMN pix_txid TEXT',
        'ALTER TABLE pubshow_pedidos ADD COLUMN pix_payload TEXT',
        # PIX via Asaas — payment_id para rastrear cobrança
        'ALTER TABLE pubshow_pedidos ADD COLUMN asaas_payment_id TEXT',
        # Bar pode exigir PIX antes de entrar na fila
        'ALTER TABLE pubshow_businesses ADD COLUMN requer_pix INTEGER DEFAULT 0',
        # Temas habilitados pelo bar (JSON array de keys; NULL = todos habilitados)
        'ALTER TABLE pubshow_businesses ADD COLUMN temas_habilitados TEXT DEFAULT NULL',
        # Slides de propaganda do bar na TV (JSON array de {titulo, subtitulo, emoji, cor})
        'ALTER TABLE pubshow_businesses ADD COLUMN anuncios_json TEXT DEFAULT "[]"',
        # Promoção Relâmpago — expira em ISO datetime, NULL = sem promoção ativa
        'ALTER TABLE pubshow_businesses ADD COLUMN promo_msg TEXT DEFAULT NULL',
        'ALTER TABLE pubshow_businesses ADD COLUMN promo_expira TEXT DEFAULT NULL',
        'ALTER TABLE pubshow_businesses ADD COLUMN promo_emoji TEXT DEFAULT "🍺"',
        # Notificação WhatsApp ao dono do bar quando chega pedido
        'ALTER TABLE pubshow_businesses ADD COLUMN whatsapp_notif INTEGER DEFAULT 0',
        # Happy Hour dinâmico — JSON {ini, fim, desconto, dias}
        'ALTER TABLE pubshow_businesses ADD COLUMN happy_hour_json TEXT DEFAULT NULL',
        # Anti-fraude: IP de cadastro para detectar multi-trials
        'ALTER TABLE pubshow_businesses ADD COLUMN signup_ip TEXT DEFAULT NULL',
        # Multi-locais (plano Rede): FK para o dono/negócio principal
        # NULL = bar independente; preenchido = filial vinculada a outro business_id
        'ALTER TABLE pubshow_businesses ADD COLUMN owner_business_id INTEGER DEFAULT NULL',
    ]:
        try:
            conn.execute(m); conn.commit()
        except Exception:
            try: conn.rollback()
            except: pass

    # Seed da biblioteca de vídeos — INSERT OR IGNORE, seguro rodar sempre
    _seed_videos(conn)

    # Corrige vídeos que ficaram indisponíveis no YouTube (UPDATE por youtube_id)
    _corrigir_videos_quebrados(conn)

    conn.close()


def _corrigir_videos_quebrados(conn):
    """Substitui IDs de vídeos que foram removidos/bloqueados no YouTube.
    Cada tupla: (id_antigo, id_novo, titulo_novo, artista_novo)
    """
    substituicoes = [
        # Radical TV — Motocross Insane foi removido
        ('MObp71DNOJM', 'vKXzrFciFp8', 'Motocross — Red Bull Best Moments', 'Red Bull Moto'),
        # Sertanejo — alguns MCs/funk não permitem embed
        ('WEVa_1ZG85I', 'nrTX7jLmgQI', 'Balada (Tchê Tchê Rere)', 'Gusttavo Lima'),
        # Radical — Skate vídeo removido
        ('VqB9pSlkq2A', 'aZobFSKGrdo', 'Skate — Mega Ramp Best Tricks', 'X-Games'),
    ]
    for id_antigo, id_novo, titulo_novo, artista_novo in substituicoes:
        try:
            conn.execute(
                'UPDATE pubshow_videos SET youtube_id=?, titulo=?, artista=? WHERE youtube_id=?',
                (id_novo, titulo_novo, artista_novo, id_antigo)
            )
            conn.commit()
        except Exception:
            try: conn.rollback()
            except: pass


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
        # Clássicos punk que completam 50 — experiência garantida
        ('3JDLMoAiB4I', 'Creep',                       'Radiohead',             'punk', 'indie',    237, 500),
        ('u9Dg-g7t2l4', 'Smells Like Teen Spirit',    'Nirvana',               'punk', 'grunge',   301, 1200),
        ('XFkzRNTy2ko', 'Everlong',                    'Foo Fighters',          'punk', 'grunge',   250, 400),
        ('1VQ_3sBZEm0', 'Best of You',                 'Foo Fighters',          'punk', 'grunge',   256, 350),
        ('hTWKbfoikeg', 'Seven Nation Army',           'The White Stripes',     'punk', 'indie',    231, 600),
        ('Q0oIoR9mLrc', 'Boulevard of Broken Dreams',  'Green Day',             'punk', 'punk',     261, 800),
        ('6Ejga4jAnSA', 'When I Come Around',          'Green Day',             'punk', 'punk',     188, 350),

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
        # Mais raízes — memória afetiva total
        ('1oYDiB5-PbI', 'Faz Parte do Meu Show',     'Roberto Carlos',         'sertanejo','raiz',     263, 200),
        ('I0JtmfMhXaU', 'Não Aprendi Dizer Adeus',   'Leandro & Leonardo',     'sertanejo','raiz',     255, 180),
        ('5G4gASYqFQo', 'Saudade do Nordeste',        'Chitãozinho & Xororó',   'sertanejo','raiz',     270, 150),
        ('lMWGMlILlj4', 'A Lua Me Traiu',             'Zezé di Camargo',        'sertanejo','raiz',     241, 130),
        # Universitário que lotou forró e balada
        ('AqNlKoWMjnk', 'Não Era Amor',               'Gusttavo Lima',          'sertanejo','universitario', 248, 280),
        ('vYHpBVfNDKg', 'Tô Indo',                    'Luan Santana',           'sertanejo','universitario', 237, 200),
        ('r1sAVOJjpXE', 'Flor e o Beija-Flor',        'Henrique & Juliano',     'sertanejo','universitario', 243, 180),
        ('YBsFvKdF0Po', 'Largado às Traças Ao Vivo',  'Zé Neto & Cristiano',    'sertanejo','universitario', 242, 350),
        # Atual — os números de Spotify e YouTube não mentem
        ('t3T7H_3Tp9g', 'Seu Eu',                     'Ana Castela',            'sertanejo','atual',     198, 300),
        ('0YPqd0UZFGM', 'Abalo Emocional',             'Marília Mendonça',       'sertanejo','feminejo', 231, 280),

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
        # Mais clássicos que todo boteco conhece
        ('eOKmCTmHHZE', 'Pé na Areia',                'Zeca Pagodinho',        'pagode','classic',  231,  90),
        ('xWqMzqISs9g', 'Sem Limite',                  'Zeca Pagodinho',        'pagode','classic',  244,  80),
        ('e5S4BKmlARM', 'Amor Colorido',               'Raça Negra',            'pagode','classic',  252, 120),
        ('IZXYzMBoq0k', 'Não Precisa',                 'Raça Negra',            'pagode','classic',  237, 100),
        ('bq3ZD2BmFmY', 'Se Você Pensa',               'Fundo de Quintal',      'pagode','roots',    248,  80),
        ('7xWUkMyX4Dk', 'Alguém Me Avisou',            'Fundo de Quintal',      'pagode','roots',    231,  70),
        ('DaC_PRlqQ4Y', 'Coração em Desalinho',        'Grupo Revelação',       'pagode','classic',  241,  85),
        ('0C9e7MBCJSY', 'Amor Que Se Cuida',           'Exaltasamba',           'pagode','classic',  247,  75),
        # Pagode anos 2000 — bailão do coração
        ('vPWpyDsNEYk', 'Lembranças',                  'Thiaguinho',            'pagode','moderno',  219, 110),
        ('JOVQbgjPKaE', 'Flor',                        'Ferrugem',              'pagode','moderno',  235,  85),
        ('E-jzv-8JNKI', 'Faltando um Pedaço',          'Belo',                  'pagode','moderno',  241,  70),
        ('eLKBaJVJjwE', 'Prazer em te Conhecer',       'Sorriso Maroto',        'pagode','moderno',  228,  65),
        ('5wqGkx_nVJU', 'Inseguro',                    'Péricles',              'pagode','moderno',  234,  60),
        ('m3x6IKASP5U', 'Vagabundo',                   'Mumuzinho',             'pagode','moderno',  222,  55),
        ('Z7pqrqr3XlI', 'Cilada',                      'Rodriguinho',           'pagode','moderno',  218,  50),

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
        # Completando 50 pop — os que não podem faltar
        ('hT_nvWreIhg', 'Counting Stars',             'OneRepublic',           'pop', 'atual', 257, 1400),
        ('MV_3Dpw-BRY', 'Wake Me Up',                 'Avicii',                'pop', 'atual', 247, 1700),
        ('YkgkThdzX-8', 'We Are Young',               'fun.',                  'pop', 'atual', 250, 600),
        ('d27gTrPPAyk', 'Get Lucky',                  'Daft Punk ft. Pharrell','pop', 'atual', 248, 900),
        ('CduA0TJLXq4', 'Somebody That I Used to Know','Gotye ft. Kimbra',     'pop', 'atual', 244, 1100),

        # ══════════════════════════════════════════════════════════════════════
        # 🏎️ F1 / SPEED — 30+ vídeos — velocidade pura, drift, rally
        # ══════════════════════════════════════════════════════════════════════
        # Senna — patrimônio do Brasil — quem assiste para de respirar
        ('qZsyrJXr6no', 'Ayrton Senna — Greatest Moments',   'Ayrton Senna',    'f1','senna',  480, 200),
        ('6aCkFSGEXhI', 'Senna — Qualifying Monaco 1984',    'Ayrton Senna',    'f1','senna',  360, 150),
        ('aHjmCXDpNcs', 'Senna vs Prost — The Rivalry',      'F1 History',      'f1','senna',  600, 100),
        # F1 moderna — os melhores momentos
        ('wiCCEbkz_IM', 'F1 Greatest Overtakes Ever',        'Formula 1',       'f1','f1',     600, 120),
        ('yk2P7Gn3mDs', 'F1 Best Crashes — Survival Moments','Formula 1',       'f1','f1',     480,  80),
        ('w5MBtVxOBQs', 'Max Verstappen — Best Laps',        'Red Bull Racing', 'f1','f1',     360,  70),
        ('HzLLGxQGhiM', 'Lewis Hamilton — Greatest Drives',  'Mercedes AMG F1', 'f1','f1',     480,  90),
        ('9lNZ_Rnr4Yc', 'Michael Schumacher — Best Moments', 'Schumacher',      'f1','f1',     600,  80),
        ('e-GCGP_BQKI', 'F1 Onboard — Best Qualifying Laps', 'Formula 1',       'f1','f1',     300,  60),
        ('tOa4OT2aMbc', 'F1 2023 — Season Highlights',       'Formula 1',       'f1','f1',     480,  50),
        # NASCAR — oval americano raiz
        ('YbHuGPHaURQ', 'NASCAR Greatest Crashes & Moments', 'NASCAR',          'f1','nascar', 360,  50),
        ('7DljRMCpYZk', 'NASCAR — Best Finishes Ever',       'NASCAR',          'f1','nascar', 300,  40),
        ('X-YFJh4sFa8', 'Daytona 500 — Greatest Moments',   'NASCAR',          'f1','nascar', 480,  35),
        # Ken Block Gymkhana — série mais vista de drift da história
        ('n_RMXCQZ8Gs', 'Ken Block Gymkhana 10',             'Hoonigan',        'f1','drift',  543, 200),
        ('AasSKmDDFxk', 'Ken Block Gymkhana 3 (Original)',   'Hoonigan',        'f1','drift',  338, 180),
        ('PcgWJ0UjDh4', 'Ken Block — Climbkhana Pikes Peak', 'Hoonigan',        'f1','drift',  600, 120),
        # Drift puro — D1GP e Formula Drift
        ('0GiAiRJsYh8', 'Best Drift Moments Compilation',    'D1GP',            'f1','drift',  360,  40),
        ('4XrTpG-P_Yw', 'Formula Drift — Best Battles',      'Formula Drift',   'f1','drift',  300,  35),
        ('ZJpHExQs0dk', 'Drift — Insane Angles 2023',        'Drift Alliance',  'f1','drift',  240,  30),
        # WRC Rally — velocidade off-road — Grupo B era lendária
        ('Aqe_bvkTbJM', 'WRC Rally — Best of All Time',      'WRC',             'f1','rally',  600,  60),
        ('ysVJ4XRmtJM', 'WRC Rally Group B — Era Proibida',  'WRC History',     'f1','rally',  480,  50),
        ('ltyGVXVGmhA', 'Rally — Closest Calls Ever',        'WRC',             'f1','rally',  360,  40),
        ('9hnY1GOSHJA', 'Sébastien Loeb — 9x World Champion','Citroën WRC',     'f1','rally',  480,  35),
        # Pikes Peak — a corrida mais insana do mundo
        ('T2sNMoJJb-8', 'Pikes Peak — Top Runs History',     'Pikes Peak',      'f1','pikes',  480,  40),
        # Nürburgring — circuito sagrado
        ('TqfKEbwJeek', 'Nürburgring — Fastest Laps Ever',   'Nürburgring',     'f1','track',  600,  45),
        # MotoGP — velocidade das duas rodas
        ('k5W_-dLdJJc', 'MotoGP — Greatest Overtakes',       'MotoGP',          'f1','moto',   360,  55),
        ('_HpBsxS0XTg', 'Marc Marquez — Impossible Saves',   'Repsol Honda',    'f1','moto',   300,  60),
        ('oZRfBFWfGX4', 'Valentino Rossi — The Doctor',      'Valentino Rossi', 'f1','moto',   480,  80),

        # ══════════════════════════════════════════════════════════════════════
        # ⚽ FUTEBOL — 30+ vídeos — os maiores de todos os tempos
        # ══════════════════════════════════════════════════════════════════════
        # Ronaldinho — magia pura, o mais amado
        ('PFivhFVDfhU', 'Ronaldinho — Skills & Goals',       'Ronaldinho Gaúcho', 'futebol','skills', 600, 400),
        ('G4dFsH0EXTU', 'Ronaldinho — Barcelona Best Moments','FC Barcelona',    'futebol','skills', 480, 300),
        ('9Xvf0GbGPfM', 'Ronaldinho — Ballon d\'Or 2005',    'FIFA',            'futebol','award',  600, 200),
        # Ronaldo R9 — Fenômeno
        ('lBe1OPSHCqk', 'Ronaldo R9 — Best Goals Ever',      'Ronaldo Fenômeno','futebol','goals',  480, 250),
        ('W-nDEoO_mgo', 'Ronaldo R9 — Copa 2002 (Brasil)',   'FIFA',            'futebol','world',  600, 200),
        # Messi — GOAT
        ('8qPqXBpfAFw', 'Messi — Top 50 Goals of His Career','Leo Messi',       'futebol','goals',  600, 600),
        ('VYs4G2eRIgk', 'Messi vs Ronaldo — The GOAT Debate','Compilação',      'futebol','goat',   600, 500),
        ('H0Ej8f-KZGE', 'Messi — World Cup 2022 Qatar',      'FIFA',            'futebol','world',  600, 400),
        # CR7
        ('F_-5HC8BTzM', 'Cristiano Ronaldo — Top 50 Goals',  'CR7',             'futebol','goals',  600, 500),
        ('4yLOLLKwdPY', 'Ronaldo — Bicycle Kick vs Juventus', 'Champions League','futebol','goals',  120, 300),
        # Roberto Carlos — o chute impossível
        ('Qa5jSRiVFnA', 'Roberto Carlos — Free Kicks',       'Roberto Carlos',  'futebol','goals',  300, 250),
        ('NJg_NE61vFs', 'Roberto Carlos vs França 1997',     'FIFA',            'futebol','goals',   60, 400),
        # Pelé — o rei
        ('gTq5i1RRyCA', 'Pelé — Greatest Goals Collection',  'Pelé',            'futebol','legend', 600, 300),
        ('RL5i8gWf3z8', 'Pelé — World Cup 1970 Brazil',      'FIFA History',    'futebol','world',  480, 200),
        # Neymar
        ('yh4OmhMnCww', 'Neymar Jr — Skills & Goals',        'Neymar Jr',       'futebol','skills', 480, 200),
        ('GRF9bj3OVWE', 'Neymar — Best Goals PSG & Brazil',  'Neymar Jr',       'futebol','goals',  480, 150),
        # Zidane
        ('AQd7Fh5hbhI', 'Zinedine Zidane — Skills & Goals',  'Zidane',          'futebol','legend', 600, 200),
        ('rBZLcVSpSok', 'Zidane — UCL Final 2002 (Volley)',   'Real Madrid',     'futebol','goals',   90, 300),
        # Zlatan — o mais único
        ('5Ifrz_s7mJc', 'Zlatan Ibrahimovic — Impossible Goals','Zlatan',       'futebol','goals',  480, 350),
        # Brasil — seleção e Copa
        ('3t08VWK0Sc0', 'Brasil — Copa 1994 Gols e Momentos', 'CBF',            'futebol','world',  600, 150),
        ('w5bFaAqSQNc', 'Brasil — Jogo Bonito Compilation',   'CBF',            'futebol','skills', 480, 120),
        # Champions League — os maiores
        ('Bln9FJz9k9c', 'Top 50 Goals Champions League',      'UEFA',            'futebol','goals',  600, 150),
        ('xJ-e0dSsLCk', 'UCL Greatest Finals Ever',           'UEFA',            'futebol','final',  600, 100),
        # Goleiros — defesas impossíveis
        ('vCmOkEfZeWk', 'Greatest Goalkeeper Saves Ever',     'Compilação',      'futebol','saves',  480, 120),
        # Street Football — jogo bonito da rua
        ('W74lHFb4U9A', 'Street Football — Best Skills',      'World Street Football','futebol','street',360,100),

        # ══════════════════════════════════════════════════════════════════════
        # 🏄 SURF — 25+ vídeos — do barrel ao big wave
        # ══════════════════════════════════════════════════════════════════════
        # Gabriel Medina — orgulho brasileiro
        ('WwRqc56YQXI', 'Gabriel Medina — Best Rides',        'Gabriel Medina',  'surf','pro',     360, 40),
        ('5_s-8KHoSBY', 'Gabriel Medina — Olympic Gold 2021', 'WSL/Olympics',    'surf','olymp',   300, 60),
        ('dFj4KTfJJFE', 'Medina — Backflip que parou o mundo','WSL',            'surf','pro',     180, 80),
        # Kelly Slater — 11x campeão mundial
        ('E_EEwEZPjbg', 'Kelly Slater — Legend of Surfing',   'Kelly Slater',    'surf','legend',  480, 80),
        ('0muqgEqWsFA', 'Kelly Slater — Perfect 10 Pipeline', 'WSL',             'surf','barrel',  120, 70),
        ('TxaJbqNbQiA', 'Kelly Slater — 11th World Title',    'WSL',             'surf','champion',300, 60),
        # Pipeline — o tubo mais famoso do mundo
        ('d_QQFqHM3DQ', 'Pipeline — Best Barrels Ever',       'WSL Surf',        'surf','barrel',  360, 30),
        ('YkS6pUcE5bA', 'Pipeline — Banzai Best Session',     'Pipeline Masters','surf','barrel',  300, 25),
        # Nazaré — ondas gigantes de Portugal
        ('c8JnDhBHXJM', 'Nazaré — Big Wave Surfing',          'WSL Big Wave',    'surf','bigwave', 300, 50),
        ('j9l5e5RMuSc', 'Nazaré — 30 metros de onda',         'WSL',             'surf','bigwave', 240, 45),
        ('x9X5i2iI-rU', 'Nazaré — Laird Hamilton Tow-In',     'Big Wave',        'surf','bigwave', 300, 40),
        # Teahupo'o — Taiti — o mais pesado
        ('VlSAEBh0KBQ', 'Teahupo\'o — Heaviest Wave on Earth','WSL',            'surf','heavy',   360, 35),
        ('VfXoFiTXGEo', 'Teahupo\'o — Code Red Session',      'WSL',             'surf','heavy',   300, 30),
        # John John Florence — o mais estiloso
        ('O0VOpzSY_lQ', 'John John Florence — Best Moments',  'WSL',             'surf','pro',     480, 35),
        # Italo Ferreira
        ('1EW_uH29JJk', 'Italo Ferreira — Olympic Champion',  'WSL',             'surf','olymp',   300, 30),
        # Big Waves — Pe\'ahi (Jaws) / Mavericks
        ('0kY7O9YBNB8', 'Pe\'ahi (Jaws) — Big Wave Event',    'WSL Big Wave',    'surf','bigwave', 360, 25),
        ('h3oRaZjmFsQ', 'Mavericks — Big Wave California',    'Big Wave',        'surf','bigwave', 300, 20),
        # Red Bull Surf — conteúdo premium
        ('HHdBD3UrJvU', 'Red Bull — Best Surf Moments',       'Red Bull Surf',   'surf','redbull', 480, 40),
        ('CduGxKeSaOo', 'Cloudbreak Fiji — Perfect Barrels',  'WSL',             'surf','barrel',  300, 25),
        # WSL compilações
        ('xRkbCQ5UT_c', 'WSL — Best Moments 2023',            'World Surf League','surf','pro',    420, 20),
        ('gHx4l4OxFPs', 'Surf — Greatest Wipeouts',           'WSL',             'surf','wipeout', 300, 30),
        ('qNDPTBFKOSE', 'Women Surfing — Best Rides WSL',     'WSL Women',       'surf','women',   360, 20),
        # Tahiti — sonho de qualquer surfista
        ('sQP6HiTCRuY', 'Tahiti — Perfect Blue Barrels',      'WSL',             'surf','barrel',  300, 30),
        # Tom Curren / Andy Irons — lendas clássicas
        ('y3GpEgMq8F8', 'Andy Irons — Greatest Rides',        'WSL',             'surf','legend',  480, 45),
        ('yRqbBLRj7lQ', 'Tom Curren — Lost Tapes',            'Surf History',    'surf','legend',  360, 35),
        # Owen Wright / Julian Wilson
        ('VsRPeVh4JCk', 'Julian Wilson — Best Backflips',     'WSL',             'surf','pro',     300, 25),
        # Brasil na WSL — galera unida
        ('FjF3SVNeDd4', 'Brasil Surf — Melhores Momentos',    'WSL Brazil',      'surf','brasil',  360, 30),
        # Artificiais — Wavegarden e Kelly Slater's pool
        ('PIzIIq9NeGE', 'Kelly Slater Wave Pool — Perfect Wave','WSL',           'surf','pool',    240, 50),
        # Bodyboard — Jaws no belly
        ('RXZFBLU4Gxk', 'Bodyboard — Jaws Biggest Waves',     'IBA World Tour',  'surf','bodyboard',300,20),
        # Longboard clássico — estilo raiz
        ('DdKhQZ0e7A4', 'Longboard — Classic Style Noseriders','Surf History',   'surf','long',    300, 15),

        # ══════════════════════════════════════════════════════════════════════
        # 🪂 AÉREO — 25+ vídeos — impossível não travar de ver
        # ══════════════════════════════════════════════════════════════════════
        # Felix Baumgartner — o salto do espaço
        ('vvbMQjGgPEg', 'Felix Baumgartner — Space Jump 39km', 'Red Bull Stratos','aerio','record', 220, 120),
        ('FHtvDA0W34I', 'Baumgartner — Full Jump Documentary', 'Red Bull Stratos','aerio','record', 600, 80),
        # Alan Eustace — quebrou o recorde de Baumgartner
        ('hk__q-NHEF8', 'Alan Eustace — Higher than Baumgartner','Google',       'aerio','record', 300, 40),
        # Wingsuit — voar como pássaro
        ('8CsuH6DDs_U', 'Wingsuit — Flying Through Mountain Arch','Red Bull',    'aerio','wingsuit',300,140),
        ('rHg6rHBLJBQ', 'Wingsuit — Swoop Through Village',    'Red Bull',        'aerio','wingsuit',180,100),
        ('6B3kiAMPyPw', 'Wingsuit — World Record Low Pass',    'Red Bull',        'aerio','wingsuit',240, 80),
        ('dT9uJdWFzAE', 'Wingsuit Formation — 100 wingsuits',  'Red Bull',        'aerio','wingsuit',300, 60),
        # Base Jump — salto de pontes e edifícios
        ('1dPFEePgFAA', 'Base Jump — World\'s Highest Buildings','Red Bull',     'aerio','basejump',240,100),
        ('cFMgJoVPTjU', 'Base Jump — Best 2023 Compilation',   'Base Jump World', 'aerio','basejump',360, 60),
        ('s6NhFHvFpig', 'BASE — Yosemite El Capitan Jump',     'Base Jump',       'aerio','basejump',180, 50),
        # Aerobática — aviões de acrobacia
        ('B-2tUPGrRaU', 'Red Bull Air Race — Best Moments',    'Red Bull',        'aerio','plane',  360, 60),
        ('nHp4YBWLKWM', 'Blue Angels — US Navy Airshow',       'US Navy',         'aerio','plane',  480, 80),
        ('T1V4VRrCqEo', 'Aerobatics World Championships',      'FAI',             'aerio','plane',  600, 40),
        ('WNJPaFOPLLM', 'Su-27 Cobra Maneuver — Insane',       'Military Aviation','aerio','plane', 120, 70),
        # Paraquedismo — formações e CRW
        ('vEuMSAQhivw', 'Skydiving — 164 Formation Record',    'Skydive',         'aerio','skydive',300, 50),
        ('MoNuyFl68Zo', 'Skydive — Best Freefly Moments',      'Red Bull',        'aerio','skydive',360, 40),
        # Paragliding / Hangliding extremo
        ('4kjx_6FxFGM', 'Paragliding — Extreme Alps',          'Red Bull',        'aerio','para',   300, 35),
        ('lB8zmmqE8e8', 'Hang Gliding — World Record Distance','Hang Glide',      'aerio','para',   240, 25),
        # Helicóptero acrobático
        ('oT2PCBNXFCE', 'Helicopter Aerobatics — Insane Tricks','Heli',          'aerio','heli',   240, 30),
        # Speed riding — ski + paraglider
        ('TGLF2sNmcVY', 'Speed Riding — Alps Mountains',       'Red Bull',        'aerio','speed',  180, 40),
        # Jet Wingsuit — asa movida a motor
        ('kC7GEiMO8IY', 'Jet Wingsuit — Powered Human Flight', 'Jetman Dubai',    'aerio','wingsuit',300, 90),
        # Jetman — voo em formação com avião
        ('PvGlCnxoMOA', 'Jetman Dubai — Flying with A380',     'Jetman',          'aerio','wingsuit',180, 70),
        # Hot Air Balloon extremo
        ('4sMYf_STBCQ', 'Balloon — Record Altitude Journey',   'Red Bull',        'aerio','balo',   300, 25),
        # Stunt Plane — pilotos acrobáticos
        ('n6FfV8mMCFQ', 'Stunt Pilot — Insane Maneuvers',      'Red Bull',        'aerio','plane',  240, 35),
        # Proximity Flying — raspar o chão voando
        ('ETyzOlXMwgM', 'Proximity Flying — Extreme Low Pass', 'Red Bull',        'aerio','wingsuit',240, 55),
        # Human Cannonball
        ('VY4-DKqFdac', 'Human Cannonball — World Record',     'Red Bull',        'aerio','record', 180, 40),
        # Speed gliding
        ('pRW-z4yOlnI', 'Speed Gliding — Fastest Alpine Runs', 'Red Bull',        'aerio','speed',  240, 30),
        # Skyrunning — correr em montanhas
        ('ZGi5Xt-P-S4', 'Skyrunning — Ultra High Altitude',    'Red Bull',        'aerio','sky',    300, 20),
        # Air Guitar do céu — formação de paraquedistas
        ('Cqg0jYJXlT4', 'Skydiving Formation — World Record',  'IBA',             'aerio','skydive',300, 35),

        # ══════════════════════════════════════════════════════════════════════
        # 🛹 RADICAL — 25+ vídeos — extremo ao máximo
        # ══════════════════════════════════════════════════════════════════════
        # X-Games — o maior evento de esportes radicais
        ('Bw-VFzFD5kM', 'X-Games — Best Moments All Time',     'X-Games',         'radical','xgames', 600, 60),
        ('Kg5ZXIj5qcI', 'X-Games 2023 — Top Runs',             'X-Games',         'radical','xgames', 480, 40),
        # Skate — Tony Hawk e era moderna
        ('E4kbBqoucGo', 'Tony Hawk — First 900 Ever (1999)',   'X-Games',         'radical','skate',  120,100),
        ('VqB9pSlkq2A', 'Skate — Best Tricks Street League',   'SLS',             'radical','skate',  480, 50),
        ('JvJW8JKMPQA', 'Skate — Nyjah Huston Best Moments',   'Street League',   'radical','skate',  360, 60),
        ('aZobFSKGrdo', 'Skate — Mega Ramp Best Tricks',       'X-Games',         'radical','skate',  300, 45),
        # Danny MacAskill — trials bike lendário
        ('Z19R4AOhG6M', 'Danny MacAskill — Wee Day Out',       'Red Bull',        'radical','bike',   330,100),
        ('x5JnQfHhJaE', 'Danny MacAskill — The Ridge',         'Red Bull',        'radical','bike',   300, 80),
        # BMX
        ('hDFn8AnPX70', 'BMX — Best Tricks Compilation',       'Red Bull BMX',    'radical','bmx',    360, 50),
        ('b9gOVzBE7VY', 'BMX Freestyle — X-Games Best',        'X-Games',         'radical','bmx',    300, 45),
        ('vHX2-iJJhfg', 'BMX — Mat Hoffman Greatest Moments',  'BMX',             'radical','bmx',    480, 35),
        # Motocross / FMX
        ('MObp71DNOJM', 'Motocross — Most Insane Moments',     'Red Bull Moto',   'radical','moto',   360, 50),
        ('dpj6I_EvB38', 'FMX — Travis Pastrana Best Tricks',   'X-Games',         'radical','moto',   300, 60),
        ('LzJBjKdGbEg', 'Supercross — Best Battles 2023',      'Supercross',      'radical','moto',   480, 35),
        # Mountain Bike — Red Bull Rampage
        ('oEfLUDiCMB0', 'Red Bull Rampage — Best Runs',        'Red Bull MTB',    'radical','mtb',    480, 40),
        ('i6xGVqWXelY', 'Mountain Bike — Sickest Lines Ever',  'Red Bull',        'radical','mtb',    300, 35),
        # Snowboard / Ski extremo
        ('QdMkyvnXLb8', 'Snowboard — Best Halfpipe X-Games',   'X-Games',         'radical','snow',   300, 40),
        ('SfCubzXiZ_s', 'Ski — Big Mountain Best Runs',        'Red Bull Ski',    'radical','ski',    360, 30),
        # Wakeboard / Kiteboard
        ('1vXmFE6D5w4', 'Wakeboard — Hyperlite Best Tricks',   'Wakeboard',       'radical','water',  300, 25),
        # Parkour / Freerunning
        ('aZSs5b04pYs', 'Parkour — Best Athletes 2023',        'Red Bull',        'radical','parkour',300, 45),
        ('cvXg-hoQTKs', 'Freerunning — Most Creative Moves',   'Red Bull',        'radical','parkour',360, 35),
        # Cliff Diving — saltos de pedras altas
        ('LLRrMbEPkuU', 'Cliff Diving — Red Bull World Series', 'Red Bull',       'radical','dive',   300, 40),
        ('Lm7VwYMgqHo', 'High Diving — World Record Jump',     'Red Bull',        'radical','dive',   240, 35),
        # Longboard / Downhill
        ('_tMQzMFsYRY', 'Downhill Longboard — Fastest Ever',   'Red Bull',        'radical','skate',  300, 30),
        # Rollerblading agressivo
        ('6xIhWLqkaMQ', 'Aggressive Inline — Best Tricks',     'Blade World',     'radical','blade',  360, 20),
        # Kitesurf
        ('l-ZmrGKYvds', 'Kitesurf — Insane Tricks',            'Red Bull Surf',   'radical','water',  300, 25),
        # Skysurf — paraquedas + prancha
        ('5f9oTq3KFBY', 'Skysurf — Surfing the Clouds',        'Red Bull',        'radical','air',    240, 30),
        # Slackline — equilibrismo entre montanhas
        ('jK5qMtf6gQM', 'Highline — Between the Mountains',    'Red Bull',        'radical','slack',  300, 35),
        # Street Luge
        ('aBQOqXIQkpA', 'Street Luge — Downhill Racing',       'Red Bull',        'radical','luge',   240, 20),

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
        # Nirvana — Unplugged MTV — uma das sessões mais icônicas
        ('fRuSmops8E4', 'Nirvana — MTV Unplugged 1993',    'Nirvana',          'show_rock', 'lendario', 2700, 250),
        # Rolling Stones — Havana Moon
        ('SmPzGKRnFTU', 'Rolling Stones — Havana Moon',   'Rolling Stones',   'show_rock', 'lendario', 600, 150),
        # Foo Fighters — ao vivo em Wembley
        ('o5iQGBJg7LM', 'Foo Fighters — Live Wembley',    'Foo Fighters',     'show_rock', 'classico', 600, 100),
        # Red Hot Chili Peppers — Rock in Rio
        ('OgvE-YB_IkA', 'RHCP — Rock in Rio Highlights',  'Red Hot Chili Peppers','show_rock','atual', 600, 90),
        # Led Zeppelin — The Song Remains the Same
        ('BQzNBBiVuvQ', 'Led Zeppelin — Live Madison Square Garden','Led Zeppelin','show_rock','lendario',600,120),

        # ══════════════════════════════════════════════════════════════════════
        # SERTANEJO SHOWS — Grandes palcos
        # ══════════════════════════════════════════════════════════════════════
        ('7mRdJRbzJow', 'Gusttavo Lima — Ao Vivo',        'Gusttavo Lima',        'show_sertanejo', 'atual', 1800, 200),
        ('xQkPHoJiuBE', 'Marília Mendonça — Ao Vivo',   'Marília Mendonça',     'show_sertanejo', 'atual', 1800, 300),
        ('EJnV78LtoKA', 'Jorge & Mateus — Live',         'Jorge & Mateus',       'show_sertanejo', 'atual', 1800, 150),
        ('SJc1S2IlONM', 'Henrique & Juliano — Ao Vivo',  'Henrique & Juliano',   'show_sertanejo', 'atual', 1800, 100),
        ('cOBTxMdQPZU', 'Luan Santana — Ao Vivo',        'Luan Santana',         'show_sertanejo', 'atual', 1800, 120),
        ('6VHsS_nkNaY', 'Zezé Di Camargo & Luciano — Ao Vivo','ZDC&L',          'show_sertanejo', 'raiz',  1800,  80),
        ('laMk3EUBWXU', 'Fernando & Sorocaba — Ao Vivo', 'Fernando & Sorocaba',  'show_sertanejo', 'atual', 1800,  70),
        ('n-9BHfVKVKo', 'Chitãozinho & Xororó — Ao Vivo','Chitãozinho & Xororó','show_sertanejo', 'raiz',  1800,  60),
        ('fjvVN3X4wac', 'Maiara & Maraisa — Ao Vivo',    'Maiara & Maraisa',     'show_sertanejo', 'atual', 1800,  90),
        ('4KkQgcVKgPQ', 'Israel & Rodolffo — Ao Vivo',   'Israel & Rodolffo',    'show_sertanejo', 'atual', 1800,  70),

        # ══════════════════════════════════════════════════════════════════════
        # PAGODE SHOWS — Boteco pra arena
        # ══════════════════════════════════════════════════════════════════════
        ('JqHC4cjDH38', 'Thiaguinho — Tardezinha Ao Vivo',    'Thiaguinho',           'show_pagode', 'atual',   1800, 100),
        ('m2pt9sOlG_c', 'Péricles — Ao Vivo',                'Péricles',             'show_pagode', 'atual',   1800,  60),
        ('9sNGCZpuKq8', 'Pagodão de Boteco — Clássicos',      'Vários',               'show_pagode', 'classico',1800,  50),
        ('b4XRRjLSpbw', 'Sorriso Maroto — Ao Vivo',           'Sorriso Maroto',       'show_pagode', 'atual',   1800,  70),
        ('7kjHURtTD7E', 'Grupo Menos É Mais — Ao Vivo',       'Menos É Mais',         'show_pagode', 'atual',   1800,  55),
        ('OHlGDGEfMTE', 'Seu Jorge — Ao Vivo',                'Seu Jorge',            'show_pagode', 'mpb',     1800,  65),
        ('lS2LCp4bKk0', 'Pagode Anos 90 — As Melhores',       'Compilação',           'show_pagode', 'classico',1800,  80),
        ('BYKZhgDoW68', 'Mumuzinho — Ao Vivo',                'Mumuzinho',            'show_pagode', 'atual',   1800,  45),
        ('vJKHtS5TGbk', 'Belo — Ao Vivo',                     'Belo',                 'show_pagode', 'atual',   1800,  40),
        ('PbX0P0mbLDU', 'Dilsinho — Ao Vivo',                 'Dilsinho',             'show_pagode', 'atual',   1800,  50),

        # ══════════════════════════════════════════════════════════════════════
        # ROCK extra — mais clássicos e nacionais
        # ══════════════════════════════════════════════════════════════════════
        ('4RvWE6j2374', 'Knockin\' on Heaven\'s Door',    'Guns N\' Roses',        'rock', 'classic', 337, 300),
        ('NMTnz7cFbYY', 'Hysteria',                       'Def Leppard',           'rock', 'classic', 242, 200),
        ('vkiEws7-RDQ', 'Jump',                           'Van Halen',             'rock', 'classic', 244, 300),
        ('2X_2IdybTV0', 'Running with the Devil',         'Van Halen',             'rock', 'classic', 213, 180),
        ('kZD-1HXKL4c', 'Pour Some Sugar on Me',         'Def Leppard',           'rock', 'classic', 272, 200),
        ('ePlRbNNJWcs', 'I Love Rock \'n\' Roll',         'Joan Jett',             'rock', 'classic', 175, 200),
        ('btPJPFnesV4', 'Eye of the Tiger',               'Survivor',              'rock', 'classic', 245, 700),
        ('u02id7zS4jk', 'Space Oddity',                   'David Bowie',           'rock', 'classic', 314, 300),
        ('yVNp_T8n8-E', 'Heroes',                         'David Bowie',           'rock', 'classic', 360, 250),
        ('HiMBFBAFkyQ', 'Roxanne',                        'The Police',            'rock', 'classic', 195, 300),
        ('EOrEfSP0k2k', 'Every Breath You Take',         'The Police',            'rock', 'classic', 255, 400),
        ('xLYiIBCN9ec', 'Don\'t You (Forget About Me)',  'Simple Minds',          'rock', 'classic', 256, 400),
        ('swYjSMCI2X8', 'Don\'t Stop (Fleetwood Mac)',   'Fleetwood Mac',         'rock', 'classic', 195, 300),
        ('dUMdFPkSJls', 'Go Your Own Way',               'Fleetwood Mac',         'rock', 'classic', 219, 250),
        ('EkHTsc9KGcQ', 'Girls Just Wanna Have Fun',     'Cyndi Lauper',          'rock', 'classic', 238, 350),
        ('YkgkThdzX-8', 'We Are Young',                  'fun.',                  'rock', '2010s',   250, 600),
        ('oUzB9MioFX4', 'Wonderwall',                    'Oasis',                 'rock', 'britpop', 258, 400),
        ('6hzrDeceDF0', 'Don\'t Look Back in Anger',     'Oasis',                 'rock', 'britpop', 277, 350),
        ('hpSpTuzbocY', 'Champagne Supernova',           'Oasis',                 'rock', 'britpop', 428, 300),
        ('JDH4BZ3bfsg', 'Yellow',                        'Coldplay',              'rock', '2000s',   268, 800),
        ('pPFXn3cWqNA', 'Speed of Sound',                'Coldplay',              'rock', '2000s',   272, 400),
        ('3YxaaGgTQYM', 'Bring Me to Life',              'Evanescence',           'rock', 'emo',     223, 600),
        ('YDhNBnaMFM8', 'Wish You Were Here',            'Pink Floyd',            'rock', 'classic', 313, 400),
        ('yKNxeF4KMsY', 'Yellow (Live)',                 'Coldplay',              'rock', 'live',    270, 400),
        # Brasil rock nacional extra
        ('EiMCqmNMelU', 'Pálido',                        'Titãs',                 'rock', 'brasil',  220, 80),
        ('kGbAO6ZQKZM', 'Comida',                        'Titãs',                 'rock', 'brasil',  283, 100),
        ('S_tDEJwRpMk', 'É Uma Partida de Futebol',     'Skank',                 'rock', 'brasil',  225, 90),
        ('cqMaHbg0AiU', 'Jackie Tequila',               'Skank',                 'rock', 'brasil',  218, 80),
        ('eSz-4zFlSoI', 'Nada Sei',                      'Roupa Nova',            'rock', 'brasil',  255, 70),
        ('GPvl5uG-hLU', 'Separação',                     'Barão Vermelho',        'rock', 'brasil',  241, 60),
        ('1YYR4apxDo', 'Enter Sandman (Live)',           'Metallica',             'rock', 'live',    330, 400),
        ('UCt9sWFJSmM', 'Battery',                       'Metallica',             'rock', 'metal',   312, 250),
        ('oZa-6sCq5_A', 'For Whom the Bell Tolls',      'Metallica',             'rock', 'metal',   310, 220),
        ('oC8MZLBvW_U', 'Crazy Little Thing Called Love','Queen',                 'rock', 'classic', 162, 350),
        ('m-GpfMhReMg', 'I Want to Break Free',         'Queen',                 'rock', 'classic', 245, 500),
        ('sRznObHiWFU', 'Radio Ga Ga',                  'Queen',                 'rock', 'classic', 305, 280),

        # ══════════════════════════════════════════════════════════════════════
        # POP extra — hits internacionais 2010-2024
        # ══════════════════════════════════════════════════════════════════════
        ('9bZkp7q19f0', 'Gangnam Style',                 'PSY',                   'pop', 'viral',   219, 4500),
        ('RgKAFK5djSk', 'See You Again',                 'Wiz Khalifa',           'pop', 'atual',   229, 5500),
        ('kffacxfA7G4', 'Despacito',                     'Luis Fonsi',            'pop', 'latin',   229, 8000),
        ('fRh_vgS2dFE', 'Sorry',                         'Justin Bieber',         'pop', 'atual',   221, 3200),
        ('6Zbi0XmGtMw', 'Baby',                          'Justin Bieber',         'pop', 'atual',   215, 2900),
        ('DjMkfURSqEM', 'We Found Love',                 'Rihanna',               'pop', 'atual',   212, 1400),
        ('CvUK-8ke-KU', 'Umbrella',                      'Rihanna',               'pop', 'atual',   280, 700),
        ('32OAc4bIW7A', 'Chandelier',                    'Sia',                   'pop', 'atual',   217, 1500),
        ('2vjPBrBU-TM', 'Cheap Thrills',                 'Sia',                   'pop', 'atual',   207, 2700),
        ('0KSOMA3QBU0', 'Dark Horse',                    'Katy Perry',            'pop', 'atual',   217, 1400),
        ('HLQNRMpgnAc', 'Into You',                      'Ariana Grande',         'pop', 'atual',   199, 1800),
        ('pB-5XG-DbAA', 'God is a Woman',                'Ariana Grande',         'pop', 'atual',   197, 1500),
        ('v-Dur3uXXCQ', 'Thank U, Next',                 'Ariana Grande',         'pop', 'atual',   207, 1800),
        ('TbwlC2B-BIg', '7 Rings',                       'Ariana Grande',         'pop', 'atual',   179, 2200),
        ('8xg3vE8Ie_E', 'Love Story',                    'Taylor Swift',          'pop', 'atual',   234, 1200),
        ('IdneKLhsWOQ', 'Anti-Hero',                     'Taylor Swift',          'pop', 'atual',   196, 1500),
        ('kU9miEN2T0c', 'Cruel Summer',                  'Taylor Swift',          'pop', 'atual',   178, 1300),
        ('NG8xtMGIBOQ', 'Bad Guy',                       'Billie Eilish',         'pop', 'atual',   194, 2000),
        ('H5v3kku4y6Q', 'Happier Than Ever',             'Billie Eilish',         'pop', 'atual',   245, 900),
        ('Yd9vlbRGE7A', 'Flowers',                       'Miley Cyrus',           'pop', 'atual',   200, 1500),
        ('MrOqgs-pDss', 'Heart of Glass',                'Blondie',               'pop', 'classic', 214, 300),
        ('MV_3Dpw-BRY', 'Wake Me Up',                    'Avicii',                'pop', 'atual',   247, 1700),
        ('UtF6Jej8yb4', 'Levels',                        'Avicii',                'pop', 'atual',   201, 1400),
        ('1-xGerv5FOk', 'The nights',                    'Avicii',                'pop', 'atual',   185, 1100),
        ('ZbZSe6N_BXs', 'Happy',                         'Pharrell Williams',     'pop', 'atual',   233, 1400),
        ('d27gTrPPAyk', 'Get Lucky',                     'Daft Punk',             'pop', 'atual',   248, 900),
        ('5NV6Rdv1h3Q', 'Around the World',              'Daft Punk',             'pop', 'classic', 235, 400),
        ('CduA0TJLXq4', 'Somebody That I Used to Know',  'Gotye',                 'pop', 'atual',   244, 1100),
        ('hT_nvWreIhg', 'Counting Stars',                'OneRepublic',           'pop', 'atual',   257, 1400),
        ('UceaB4D0jpo', 'Stressed Out',                  'Twenty One Pilots',     'pop', 'atual',   201, 1700),
        ('pXRviuL6vMY', 'Heathens',                      'Twenty One Pilots',     'pop', 'atual',   181, 1000),
        ('SlPhMPnQ58k', 'Envolver',                      'Anitta',                'pop', 'brasil',  185, 800),
        ('qB3kK0QTe2g', 'Girl from Rio',                 'Anitta',                'pop', 'brasil',  193, 400),
        ('NvKPJHNGSqY', 'Funk Rave',                     'Anitta',                'pop', 'brasil',  180, 350),
        ('VrpGhEVyrg0', 'Vai Malandra',                  'MC Zaac & Maejor',      'pop', 'brasil',  192, 600),
        ('nNGvF6DQRPE', 'Bum Bum Tam Tam',               'MC Fioti',              'pop', 'brasil',  181, 800),

        # ══════════════════════════════════════════════════════════════════════
        # SERTANEJO extra — raíz e atual
        # ══════════════════════════════════════════════════════════════════════
        ('b0mq7LKggkk', 'Camarote',                      'Vitor & Luan',          'sertanejo', 'universitario', 241, 350),
        ('7Ve503XQDBA', 'Amor de Copo em Copo',          'Vitor & Luan',          'sertanejo', 'universitario', 238, 250),
        ('jVe503XQDBA', 'Trem Bala (Pagode)',             'Ana Vilela',            'sertanejo', 'atual',         234, 300),
        ('FIJFdQWKXiU', 'Vou Cuidar de Você',            'Bruno e Marrone',        'sertanejo', 'raiz',          258, 150),
        ('6MpXDHuuSLM', 'Dormi na Praça',                'Bruno e Marrone',        'sertanejo', 'raiz',          245, 130),
        ('l30HOv-zglI', 'Mil Vezes',                     'Gusttavo Lima',          'sertanejo', 'universitario', 253, 200),
        ('jve503xqdba', 'Tô Bebendo',                    'Gusttavo Lima',          'sertanejo', 'universitario', 247, 180),
        ('z3iWxMrMGOM', 'Sofrência',                     'Gusttavo Lima',          'sertanejo', 'universitario', 250, 220),
        ('iPUmE-tne5U', 'Caiu na Net',                   'Forró Boys',             'sertanejo', 'atual',         228, 160),
        ('_KiQDVBWv4Q', 'Batom de Cereja',               'Jorge & Mateus',         'sertanejo', 'universitario', 240, 200),
        ('W-nDEOO_mgo', 'Propaganda',                    'Jorge & Mateus',         'sertanejo', 'universitario', 238, 180),
        ('J5tMFGCWMhc', 'Meu Pedaço de Pecado',         'João Gomes',             'sertanejo', 'atual',         197, 400),
        ('o83aaJL4hzs', 'Eu Tenho a Senha',              'João Gomes',             'sertanejo', 'atual',         198, 350),
        ('MiNBSIJIoEA', 'Traição',                       'MC Livinho',             'sertanejo', 'atual',         192, 280),
        ('eSz4zFlSoI1', 'De Quem É a Culpa',             'Zé Neto & Cristiano',   'sertanejo', 'universitario', 238, 230),
        ('9A5J57EeGrA', 'Cheirosa',                      'Xand Avião',             'sertanejo', 'forró',         218, 180),
        ('aHGE6BdSP7E', 'Isso Cê Não Esperava',         'Wesley Safadão',         'sertanejo', 'forró',         225, 300),
        ('x5mVF3mIIoA', 'Camarote',                      'Wesley Safadão',         'sertanejo', 'forró',         219, 280),
        ('3qHsTCn1j5U', 'Caiu na Net',                   'Wesley Safadão',         'sertanejo', 'forró',         212, 250),
        ('PjxEGdOt7yY', 'Morena',                        'Felipe Araújo',          'sertanejo', 'atual',         228, 180),
        ('kZVyVfC0LMY', 'Esquecer Você',                 'Matheus & Kauan',        'sertanejo', 'universitario', 235, 160),
        ('z3iWXmrMGOM', 'De Janeiro a Janeiro',          'Rodolffo',               'sertanejo', 'atual',         229, 220),
        ('sWQi7nQcEXQ', 'Cê Topa?',                      'Ana Castela & Gustavo Mioto','sertanejo','atual',      195, 380),

        # ══════════════════════════════════════════════════════════════════════
        # PAGODE extra — boteco e moderno
        # ══════════════════════════════════════════════════════════════════════
        ('Hix6m6v7y4g', 'Deus Salve o Rei',              'Ferrugem',              'pagode', 'moderno', 248, 90),
        ('OTXe4yCJow0', 'Amigos',                        'Dilsinho',              'pagode', 'moderno', 237, 80),
        ('UJCNKBh4l6Q', 'Intenção',                      'Dilsinho',              'pagode', 'moderno', 230, 70),
        ('YJn7oABPaxc', 'Largado às Traças',             'Dilsinho',              'pagode', 'moderno', 241, 130),
        ('GXSv9_5ZpCo', 'Haja Coração',                  'Thiaguinho',            'pagode', 'moderno', 228, 120),
        ('oAZzVLJP3lo', 'Sorriso Aberto',                'Thiaguinho',            'pagode', 'moderno', 237, 100),
        ('t7Wq1GDSwcY', 'Se Você Quer Me Ver',           'Ferrugem',              'pagode', 'moderno', 243, 80),
        ('eiMvEFymQnk', 'Compasso',                      'Ferrugem',              'pagode', 'moderno', 235, 70),
        ('MtSGNxAKBKA', 'Compasso do Amor',              'Sorriso Maroto',        'pagode', 'moderno', 240, 80),
        ('pPBB8SCPQDE', 'Episódio Triste',               'Sorriso Maroto',        'pagode', 'moderno', 248, 70),
        ('6BFWT_h5fYA', 'Meu Vício',                     'Belo',                  'pagode', 'moderno', 258, 80),
        ('w5RuK2MLOGY', 'Sem Querer',                    'Belo',                  'pagode', 'moderno', 248, 70),
        ('JuF-EJHhPbE', 'Lealdade',                      'Péricles',              'pagode', 'moderno', 240, 65),
        ('xnFHMLuQHaU', 'Não Chores Mais',              'Péricles',              'pagode', 'moderno', 233, 60),
        ('3qqGfE6FoxY', 'Boa Noite Cinderela',           'Mumuzinho',             'pagode', 'moderno', 225, 60),
        ('vPHXnKzSSe8', 'Recaída',                       'Mumuzinho',             'pagode', 'moderno', 231, 55),
        ('e5mDxTOaWQE', 'Não Vou Desistir',              'Grupo Revelação',       'pagode', 'classic', 245, 75),
        ('R0V0kGaxCnE', 'Sublime',                       'Grupo Revelação',       'pagode', 'classic', 238, 65),
        ('7KO_wHDFE6U', 'Sorte Grande',                  'Exaltasamba',           'pagode', 'classic', 252, 70),
        ('yOgAbKJGrTA', 'Magia',                         'Exaltasamba',           'pagode', 'classic', 248, 65),
        ('CuSmV4QSXHU', 'Apaga o Abajur',               'Fundo de Quintal',      'pagode', 'roots',   235, 60),
        ('kh-oJX3Iqpg', 'Que Saudade da Amélia',        'Fundo de Quintal',      'pagode', 'roots',   241, 55),
        ('nG5jkiVNhUE', 'Pago pra Ver',                 'Zeca Pagodinho',        'pagode', 'classic', 238, 100),
        ('DyNUhk5XHDE', 'Ogum',                          'Zeca Pagodinho',        'pagode', 'classic', 244, 85),
        ('aBHT9rBJkJ4', 'Menos é Mais',                 'Grupo Menos É Mais',    'pagode', 'moderno', 235, 55),
        ('MJfIuB2HFTU', 'Me Tira do Sufoco',            'Grupo Menos É Mais',    'pagode', 'moderno', 228, 50),

        # ══════════════════════════════════════════════════════════════════════
        # FUTEBOL extra — mais conteúdo
        # ══════════════════════════════════════════════════════════════════════
        ('3t08VWK0Sc0', 'Brasil 1994 — Todos os Gols',   'CBF Brasil',            'futebol', 'world',  480, 150),
        ('HK-E6hLfCgs', 'Haaland — Best Goals 2023',    'Manchester City',        'futebol', 'goals',  360, 200),
        ('UrZAJbT2jxQ', 'Mbappe — Speed & Skills',      'PSG',                   'futebol', 'skills', 360, 180),
        ('6aeVGE8Jmcc', 'Vinicius Jr — Best Moments',   'Real Madrid',           'futebol', 'skills', 360, 150),
        ('UCIjdfjcSO8', 'Rodri — UCL Final 2024',       'Man City',              'futebol', 'goals',  300, 120),
        ('HEBDFpigTJE', 'Futebol de Rua Brasil',        'Compilação',            'futebol', 'street', 300, 80),
        ('nDcSf8XcfQU', 'Garrincha — A Alegria do Povo','CBF História',          'futebol', 'legend', 480, 100),
        ('r79l6x2Pqgg', 'Romário — Greatest Goals',     'Romário',               'futebol', 'legend', 480, 90),
        ('LCRTQl8MHQE', 'Kaka — AC Milan Best Moments', 'AC Milan',              'futebol', 'legend', 480, 110),
        ('zBZla1PKGYE', 'Ronaldinho — FC Barcelona Top 10','FC Barcelona',       'futebol', 'goals',  300, 250),
        ('sN2kIH3oWqg', 'Brasil 2002 — Campeões do Mundo','CBF',                 'futebol', 'world',  600, 200),

        # ══════════════════════════════════════════════════════════════════════
        # SURF extra
        # ══════════════════════════════════════════════════════════════════════
        ('G8FIuvQqVhk', 'Surf — Best Barrels 2023',     'WSL',                   'surf', 'barrel',  360, 20),
        ('pGXnm8Ej7NQ', 'Surfe Brasileiro — Melhores',  'CBSurf',                'surf', 'brasil',  300, 25),
        ('PiPHF2FNxRo', 'Tatiana Weston-Webb Best Rides','WSL Women',            'surf', 'women',   300, 18),

        # ══════════════════════════════════════════════════════════════════════
        # SHOW ROCK extra
        # ══════════════════════════════════════════════════════════════════════
        ('iWnyCs3TDZU', 'Metallica — Master of Puppets Live','Metallica',         'show_rock', 'lendario', 600, 300),
        ('BqRSRlOXFLg', 'AC/DC — Highway to Hell Live',  'AC/DC',                'show_rock', 'lendario', 300, 200),
        ('WSeNSzJ2-Pw', 'Nirvana — Where Did You Sleep Last Night','Nirvana',    'show_rock', 'lendario', 330, 150),
        ('GZoXvFaOa48', 'Pearl Jam — Alive Live',        'Pearl Jam',            'show_rock', 'classico', 540, 90),
        ('JWBFfxelNJ0', 'Iron Maiden — The Trooper Live','Iron Maiden',          'show_rock', 'metal',    240, 100),
        ('DvkU4NQh3XA', 'System of a Down — Chop Suey! Live','SOAD',            'show_rock', 'metal',    215, 120),
        ('yqq7MgPmqcI', 'Soundgarden — Black Hole Sun Live','Soundgarden',       'show_rock', 'grunge',  240, 80),
        ('szm-zVQyyos', 'The Doors — Light My Fire Live', 'The Doors',           'show_rock', 'classic',  428, 90),
        ('P2EDjVTpEm0', 'Aerosmith — Dream On Live',    'Aerosmith',             'show_rock', 'classic',  263, 85),
        ('SHOmFSB3CKU', 'Legião Urbana — Faroeste Caboclo Ao Vivo','Legião',    'show_rock', 'brasil',   420, 80),

        # ══════════════════════════════════════════════════════════════════════
        # SHOW SERTANEJO extra
        # ══════════════════════════════════════════════════════════════════════
        ('g0PdE4LKhRc', 'Gusttavo Lima — Buteco',       'Gusttavo Lima',         'show_sertanejo', 'atual',  1800, 150),
        ('uWN6rLZYw2I', 'Marília Mendonça — Mineirinha', 'Marília Mendonça',     'show_sertanejo', 'atual',  1800, 180),
        ('kZW3eSjAOAQ', 'Zé Neto & Cristiano — Ao Vivo','Zé Neto & Cristiano',  'show_sertanejo', 'atual',  1800,  90),
        ('TGMgQ7VCUQ0', 'Wesley Safadão — Camarote Show','Wesley Safadão',       'show_sertanejo', 'forró',  1800, 120),
        ('hE5QsZXqzgQ', 'Ana Castela — Boiadeira Live', 'Ana Castela',           'show_sertanejo', 'atual',  1800,  80),
        ('JfIyaB6mxOs', 'Matheus & Kauan — Ao Vivo',   'Matheus & Kauan',       'show_sertanejo', 'atual',  1800,  65),

        # ══════════════════════════════════════════════════════════════════════
        # SHOW PAGODE extra
        # ══════════════════════════════════════════════════════════════════════
        ('HiGlFUyuYb0', 'Thiaguinho — Espetáculo',      'Thiaguinho',            'show_pagode', 'atual',   1800, 80),
        ('Ib3VwKBhOBs', 'Zeca Pagodinho — 40 Anos',    'Zeca Pagodinho',        'show_pagode', 'classico',1800, 70),
        ('yH4bMbAFHj8', 'Grupo Revelação — 25 Anos',   'Grupo Revelação',       'show_pagode', 'classico',1800, 55),
        ('WJdYoU9CZOQ', 'Exaltasamba — Ao Vivo',        'Exaltasamba',           'show_pagode', 'classico',1800, 60),
        ('Aw9O0AQMWMQ', 'Rodriguinho — Ao Vivo',        'Rodriguinho',           'show_pagode', 'atual',   1800, 45),
    ]

    conn.executemany(
        '''INSERT OR IGNORE INTO pubshow_videos
           (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, views_milhoes)
           VALUES (?,?,?,?,?,?,?)''',
        videos
    )
    conn.commit()
