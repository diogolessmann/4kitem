"""
kids_db.py — 4KITEM: banco de dados SQLite
Tabelas: channels, videos, clients
"""
import sqlite3
import os
import secrets
import string

# Suporte a DATA_DIR para Railway (volume persistente)
_base = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'kids.db')

# ── 6 Modos de ambiente ─────────────────────────────────────────────────────
MODES = {
    'kids': {
        'label': '🧒 Kids',     'desc': 'Conteúdo infantil verificado — PT-BR',
        'categories': ['Musical', 'Animação', 'Educativo', 'Humor',
                       'Aventura', 'Entretenimento', 'Minecraft',
                       'Brincadeiras', 'Bonecas', 'Brinquedos', 'Vlogs'],
        'age_min': 0, 'age_max': 14,
        'languages': ['PT-BR', 'Instrumental'],
        'safe_only': True,   # apenas canais is_safe=1
    },
    'saude': {
        'label': '🏥 Saúde',   'desc': 'Saúde, bem-estar e natureza',
        'categories': ['Saúde', 'Natureza', 'Bem-estar', 'Educativo'],
        'age_min': 16, 'age_max': 99,
        'languages': ['PT-BR'],
        'safe_only': True,
    },
    'escola': {
        'label': '📚 Escola',  'desc': 'Educação e ciência para jovens',
        'categories': ['Educativo', 'Ciência', 'História', 'Musical'],
        'age_min': 5, 'age_max': 17,
        'languages': ['PT-BR', 'Instrumental'],
        'safe_only': True,
    },
    'fitness': {
        'label': '💪 Fitness', 'desc': 'Motivação, esportes e performance',
        'categories': ['Esportes', 'Motivacional', 'Saúde', 'Fitness'],
        'age_min': 16, 'age_max': 99,
        'languages': ['PT-BR'],
        'safe_only': False,
    },
    'beleza': {
        'label': '💅 Beleza',  'desc': 'Lifestyle, culinária e beleza',
        'categories': ['Lifestyle', 'Culinária', 'Beleza'],
        'age_min': 14, 'age_max': 99,
        'languages': ['PT-BR'],
        'safe_only': False,
    },
    'vibe': {
        'label': '🎵 Vibe',    'desc': 'Música ambiente, lofi e shows',
        'categories': ['Lofi', 'Jazz', 'Shows', 'Música', 'Musical',
                       'Entretenimento'],
        'age_min': 0, 'age_max': 99,
        'languages': None,   # qualquer idioma — música instrumental
        'safe_only': False,
    },
}

# ── Canais semente ─────────────────────────────────────────────────────────
# (name, handle, channel_id, age_min, age_max, gender, category, language, is_safe)
# is_safe=1 → adequado para qualquer ambiente público (clínica, escola, salão)
# is_safe=0 → pode ter linguagem informal/publi excessiva — filtrado em kids/escola/saude
SEED_CHANNELS = [
    # ── KIDS 0-4 ──────────────────────────────────────────────────────────
    ('Galinha Pintadinha',   '@galinhapintadinha',        'UCBAb_DK4GYZqZR9MFA7y2Xg',  0,  4, 'N', 'Musical',        'PT-BR',        1),
    ('Mundo Bita',           '@mundobita',                'UC0cGVh96osM7yqMu0ENSKKQ',  0,  5, 'N', 'Musical',        'PT-BR',        1),
    ('Super Simple Songs',   '@SuperSimpleSongs',         'UCLsooMJoIpl_7ux2jvdPB-Q',  0,  4, 'N', 'Musical',        'Instrumental', 1),
    ('Pocoyo PT-BR',         '@Pocoyo',                   'UCEnXFTwKRy4zSafAGPkooNA',  2,  6, 'N', 'Animação',       'PT-BR',        1),
    ('Peppa Pig PT-BR',      '@PeppaPigBrasil',           'UCvD9GB-E4q_TuMpwUtFBhLA',  2,  6, 'N', 'Animação',       'PT-BR',        1),
    ('CoComelon PT-BR',      '@CoComelon',                'UCbCmjCuTUZos6Inko4u57BA',  0,  5, 'N', 'Musical',        'PT-BR',        1),
    # ── KIDS 3-8 ──────────────────────────────────────────────────────────
    ('Patati Patatá',        '@PatatiPatataOficial',      'UCe-BBpsnL89BMr6WbvBQ9dw',  3,  6, 'N', 'Humor',          'PT-BR',        1),
    ('Cocoricó',             '@cocorico',                 'UCA6Roeo-qFVk3jvjAdag7Uw',  3,  6, 'N', 'Educativo',      'PT-BR',        1),
    ('Numberblocks PT-BR',   '@numberblocks_pt',          'UCkup3lAYe6aCRyIIHQbIZWg',  3,  7, 'N', 'Educativo',      'PT-BR',        1),
    ('Patrulha Canina',      '@PAWPatrolPortuguesBrasil', 'UCgukxHgi0zZXBHhuzksN54A',  3,  7, 'N', 'Aventura',       'PT-BR',        1),
    ('Bluey PT-BR',          '@BlueyBrasil',              'UCGbO3KpKFBfHkFQVG49WKMA',  3,  8, 'N', 'Animação',       'PT-BR',        1),
    ('Larva TUBA',           '@LarvaOfficialChannel',     'UCph-WGR0oCbJDpaWmNHb5zg',  3,  7, 'N', 'Humor',          'Instrumental', 1),
    ('Oddbods',              '@Oddbods',                  'UCtlth0w7_mYqpHPViMhQ99Q',  3,  7, 'N', 'Humor',          'Instrumental', 1),
    ('Masha e o Urso',       '@MashaandBear',             'UCu59yAFE8fM0sVNTipR4edw',  3,  8, 'F', 'Animação',       'PT-BR',        1),
    ('Mônica Toy',           '@monicatoy',                'UC54BNgZlPoWZg-Jg66Dt9SA',  4,  9, 'N', 'Animação',       'PT-BR',        1),
    ('Turma da Mônica',      '@TurmaDaMonica',            'UCV4XcEqBswMCryorV_gNENw',  4, 10, 'N', 'Animação',       'PT-BR',        1),
    # ── KIDS 6-14 ─────────────────────────────────────────────────────────
    ('Canal da Belinha',     '@CanaldaBelinhaOficial',    'UCaxmitHJDRZn2PPe5RAmMkA',  6, 10, 'F', 'Entretenimento', 'PT-BR',        1),
    ('Authentic Games',      '@Authenticgames',           'UCIPA6iWNaoetaa1T46RkzXw',  9, 14, 'M', 'Minecraft',      'PT-BR',        1),
    ('Julia MineGirl',       '@juliaminegirl',            'UCEOGSdXwcXcNfcuDGbjmgOw',  9, 14, 'F', 'Minecraft',      'PT-BR',        1),
    # is_safe=0 → não aparecem em kids/escola/saude (publi excessiva / linguagem informal)
    ('Luccas Neto',          '@Luccasneto',               'UC_gV70G_Y51LTa3qhu8KiEA',  6, 12, 'N', 'Humor',          'PT-BR',        0),
    ('Enaldinho',            '@Enaldinho',                'UC2bYhAHyaqfWlPXWBVk4BcA',  9, 14, 'N', 'Humor',          'PT-BR',        0),
    # ── SAÚDE / EDUCATIVO ADULTO ──────────────────────────────────────────
    ('Drauzio Varella',      '@drauziovarella',           'UC9zqTTVeClpqMQ5CLuJdWtw', 18, 99, 'N', 'Saúde',          'PT-BR',        1),
    ('Minha Vida',           '@minhavidaoficial',         'UCMYTIyqS1-7wT-3pBB8bR5A', 18, 99, 'N', 'Saúde',          'PT-BR',        1),
    ('Manual do Mundo',      '@manualdomundo',            'UCKHhA5hN2UohhFDfNXB_cvQ', 16, 99, 'N', 'Educativo',      'PT-BR',        1),
    # ── FITNESS / ESPORTES ────────────────────────────────────────────────
    ('Paulo Muzy',           '@paulomuzy',                'UCUOsr03iLj627hJm55cmIPw', 16, 99, 'M', 'Fitness',        'PT-BR',        1),
    ('CazéTV',               '@CazeTv',                   'UCZiYbVptd3PVPf4f6eR6UaQ', 14, 99, 'N', 'Esportes',       'PT-BR',        1),
    ('Foca no Esporte',      '@focanosporte',              'UCm2eXKSQJovV8iiD6RGLJBw', 14, 99, 'N', 'Esportes',       'PT-BR',        1),
    # ── CULINÁRIA / LIFESTYLE / BELEZA ────────────────────────────────────
    ('GNT Cozinha',          '@gnttv',                    'UC0f866RMRdL5mSVnipiOHxg', 18, 99, 'N', 'Culinária',      'PT-BR',        1),
    ('Panelinha',            '@panelinha',                'UCfSPnAlDUTiIOAvNOI-a4yQ', 18, 99, 'N', 'Culinária',      'PT-BR',        1),
    ('Tastemade Brasil',     '@TastemadeBrasil',          'UCfGbJsLYpGJ1OxoCMIhCG2Q', 16, 99, 'N', 'Culinária',      'PT-BR',        1),
    ('Kefera',               '@kefera',                   'UCk3JZr7eS8jXOOFhV0CUJRA', 16, 99, 'F', 'Lifestyle',      'PT-BR',        0),
    # ── MÚSICA / LOFI / VIBE ─────────────────────────────────────────────
    ('Lofi Girl',            '@LofiGirl',                 'UCSJ4gkVC6NrvII8umztf0Ow',  0, 99, 'N', 'Lofi',           'Instrumental', 1),
    ('Chillhop Music',       '@ChillhopMusic',            'UCOxqgCwgOqC2lMqC5PYz_Dg',  0, 99, 'N', 'Jazz',           'Instrumental', 1),
    ('The Jazz Hop Café',    '@TheJazzHopCafe',            'UCnc_1wG6PwBJoEUJqJO6YHA',  0, 99, 'N', 'Jazz',           'Instrumental', 1),
    ('Cercle',               '@cerclemusic',              'UCPKT_csvP72boVX0XrMtagQ', 18, 99, 'N', 'Shows',          'EN',           1),
    ('COLORS',               '@COLORSxSTUDIOS',           'UC2Qw1dzXDBAZPwS7zm37g8g', 16, 99, 'N', 'Shows',          'EN',           1),
    ('NPR Music',            '@NPRMusic',                 'UC4eYXhJI4-7wSWc8UNRwD4A', 16, 99, 'N', 'Shows',          'EN',           1),
    ('TV Cultura Música',    '@tvculturaoficial',          'UCb6ogGALMHaJTvXMm9QibXA', 16, 99, 'N', 'Musical',        'PT-BR',        1),
]


# ── Conexão ────────────────────────────────────────────────────────────────
def get_conn():
    if os.path.dirname(DB_PATH):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _gen_code(n=6):
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(n))


# ── Inicialização ──────────────────────────────────────────────────────────
def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS channels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            handle      TEXT    UNIQUE NOT NULL,
            channel_id  TEXT    UNIQUE,
            age_min     INTEGER DEFAULT 0,
            age_max     INTEGER DEFAULT 14,
            gender      TEXT    DEFAULT 'N',
            category    TEXT    DEFAULT 'Geral',
            language    TEXT    DEFAULT 'PT-BR',
            is_safe     INTEGER DEFAULT 1,
            active      INTEGER DEFAULT 1,
            added_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS videos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_ref   INTEGER REFERENCES channels(id) ON DELETE CASCADE,
            youtube_id    TEXT    UNIQUE NOT NULL,
            title         TEXT,
            thumbnail     TEXT,
            published_at  TEXT,
            age_min       INTEGER DEFAULT 0,
            age_max       INTEGER DEFAULT 14,
            gender        TEXT    DEFAULT 'N',
            embedding_ok  INTEGER DEFAULT 1,
            last_seen     TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        -- Adiciona coluna se já existir tabela sem ela (migração)
        CREATE TABLE IF NOT EXISTS _migration_embedding_ok (done INTEGER);


        CREATE TABLE IF NOT EXISTS clients (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    UNIQUE NOT NULL,
            name        TEXT    NOT NULL,
            logo_url    TEXT,
            mode        TEXT    DEFAULT 'kids',
            city        TEXT    DEFAULT 'Brasil',
            whatsapp    TEXT,
            ticker_msg  TEXT    DEFAULT '',
            active      INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_vid_gender ON videos(gender);
        CREATE INDEX IF NOT EXISTS idx_vid_age    ON videos(age_min, age_max);
        CREATE INDEX IF NOT EXISTS idx_vid_pub    ON videos(published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vid_ch     ON videos(channel_ref);
    """)

    # Seed canais
    n_ch = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    if n_ch == 0:
        conn.executemany("""
            INSERT OR IGNORE INTO channels
                (name, handle, channel_id, age_min, age_max, gender, category, language, is_safe)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, SEED_CHANNELS)

    # Adiciona canais novos que ainda não existem (para updates futuros)
    else:
        for ch in SEED_CHANNELS:
            conn.execute("""
                INSERT OR IGNORE INTO channels
                    (name, handle, channel_id, age_min, age_max, gender, category, language, is_safe)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, ch)
            # Atualiza channel_id se ainda estiver NULL
            conn.execute("""
                UPDATE channels SET channel_id = ?
                WHERE handle = ? AND channel_id IS NULL
            """, (ch[2], ch[1]))
            # Atualiza is_safe sempre (pode ter mudado)
            conn.execute("""
                UPDATE channels SET is_safe = ?
                WHERE handle = ?
            """, (ch[8], ch[1]))

    # Demo client
    conn.execute("""
        INSERT OR IGNORE INTO clients (code, name, mode, city, ticker_msg)
        VALUES ('DEMO', 'Clínica Demonstração — 4KITEM', 'kids',
                'Schroeder - SC',
                'Bem-vindo! · Informe à recepção sua chegada · Obrigado pela preferência')
    """)

    # Migrações de colunas — sempre safe (try/except)
    _kids_migrations = [
        "ALTER TABLE videos ADD COLUMN embedding_ok INTEGER DEFAULT 1",
        "ALTER TABLE clients ADD COLUMN email TEXT DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN phone TEXT DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN cpf_cnpj TEXT DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN plan TEXT DEFAULT 'mensal'",
        "ALTER TABLE clients ADD COLUMN plan_active INTEGER DEFAULT 0",
        "ALTER TABLE clients ADD COLUMN trial_ends TEXT DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN asaas_customer_id TEXT DEFAULT ''",
    ]
    for sql in _kids_migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            try: conn.rollback()
            except: pass

    # Corrige clientes que tinham modo removido (juridico/evento) → vibe
    conn.execute("""
        UPDATE clients SET mode = 'vibe'
        WHERE mode IN ('juridico', 'evento')
    """)

    # Desativa canais EN que não devem aparecer em modos PT-BR
    canais_desativar = [
        '@BlueyOfficialChannel',  # substituído por PT-BR
        '@HeyDuggee',             # sem versão PT-BR
        '@PeppaPig',              # duplicata — já temos PT-BR
    ]
    for handle in canais_desativar:
        conn.execute("UPDATE channels SET active = 0 WHERE handle = ?", (handle,))

    conn.commit()
    conn.close()


# ── Queries: vídeos por modo ───────────────────────────────────────────────
def get_videos_for_mode(mode: str, limit: int = 30, shuffle: bool = True) -> list:
    """Retorna vídeos filtrados pelo modo de ambiente."""
    cfg       = MODES.get(mode, MODES['kids'])
    cats      = cfg['categories']
    langs     = cfg.get('languages')       # None = sem filtro de idioma
    safe_only = cfg.get('safe_only', False)

    cat_ph = ','.join('?' * len(cats))
    params = list(cats)

    lang_clause = ''
    if langs:
        lang_ph = ','.join('?' * len(langs))
        lang_clause = f'AND c.language IN ({lang_ph})'
        params += list(langs)

    safe_clause = 'AND c.is_safe = 1' if safe_only else ''

    params += [cfg['age_max'], cfg['age_min'], limit]

    conn  = get_conn()
    order = 'RANDOM()' if shuffle else 'v.published_at DESC'
    rows  = conn.execute(f"""
        SELECT
            v.youtube_id, v.title, v.thumbnail, v.published_at,
            v.age_min, v.age_max, v.gender,
            c.name AS channel_name, c.category
        FROM videos v
        JOIN channels c ON c.id = v.channel_ref
        WHERE c.active = 1
          AND c.category IN ({cat_ph})
          {lang_clause}
          {safe_clause}
          AND v.embedding_ok = 1
          AND v.age_min  <= ?
          AND v.age_max  >= ?
        ORDER BY {order}
        LIMIT ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Queries: vídeos genéricos (API pública) ───────────────────────────────
def get_videos(age=None, gender=None, limit=24, offset=0):
    conn   = get_conn()
    params = []
    where  = ["c.active = 1"]

    if age is not None:
        where.append("v.age_min <= ? AND v.age_max >= ?")
        params += [age, age]
    if gender and gender in ('M', 'F'):
        where.append("(v.gender = ? OR v.gender = 'N')")
        params.append(gender)

    params += [limit, offset]
    rows = conn.execute(f"""
        SELECT v.youtube_id, v.title, v.thumbnail, v.published_at,
               v.age_min, v.age_max, v.gender,
               c.name AS channel_name, c.handle AS channel_handle,
               c.category, c.is_safe
        FROM videos v
        JOIN channels c ON c.id = v.channel_ref
        WHERE {' AND '.join(where)}
        ORDER BY v.published_at DESC
        LIMIT ? OFFSET ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Queries: clientes ─────────────────────────────────────────────────────
def get_client(code: str) -> dict | None:
    conn = get_conn()
    row  = conn.execute("SELECT * FROM clients WHERE code = ? AND active = 1",
                        (code.upper(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_client_mode(code: str, mode: str) -> bool:
    if mode not in MODES:
        return False
    conn = get_conn()
    conn.execute("UPDATE clients SET mode = ? WHERE code = ?", (mode, code.upper()))
    conn.commit()
    conn.close()
    return True


def create_client(name: str, city: str = 'Brasil', mode: str = 'kids') -> dict:
    conn = get_conn()
    while True:
        code = _gen_code(6)
        if not conn.execute("SELECT 1 FROM clients WHERE code=?", (code,)).fetchone():
            break
    conn.execute("""
        INSERT INTO clients (code, name, city, mode, ticker_msg)
        VALUES (?, ?, ?, ?,
          'Bem-vindo! · Informe sua chegada à recepção · Obrigado pela preferência')
    """, (code, name, city, mode))
    conn.commit()
    row = conn.execute("SELECT * FROM clients WHERE code=?", (code,)).fetchone()
    conn.close()
    return dict(row)


# ── Queries: canais ───────────────────────────────────────────────────────
def get_channels(active_only=True):
    conn  = get_conn()
    where = "WHERE active = 1" if active_only else ""
    rows  = conn.execute(f"SELECT * FROM channels {where} ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def total_videos(age=None, gender=None):
    conn   = get_conn()
    params = []
    where  = ["c.active = 1"]
    if age is not None:
        where.append("v.age_min <= ? AND v.age_max >= ?")
        params += [age, age]
    if gender and gender in ('M', 'F'):
        where.append("(v.gender = ? OR v.gender = 'N')")
        params.append(gender)
    n = conn.execute(f"""
        SELECT COUNT(*) FROM videos v
        JOIN channels c ON c.id = v.channel_ref
        WHERE {' AND '.join(where)}
    """, params).fetchone()[0]
    conn.close()
    return n


def update_channel_id(db_id: int, yt_channel_id: str):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE channels SET channel_id = NULL WHERE channel_id = ? AND id != ?",
            (yt_channel_id, db_id)
        )
        conn.execute("UPDATE channels SET channel_id = ? WHERE id = ?",
                     (yt_channel_id, db_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def mark_video_blocked(youtube_id: str) -> bool:
    """Marca vídeo como embedding_ok=0 (embed bloqueado pelo canal).
    Retorna True se o vídeo existia no banco."""
    conn = get_conn()
    cur  = conn.execute(
        "UPDATE videos SET embedding_ok = 0 WHERE youtube_id = ?", (youtube_id,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def add_channel(name, handle, channel_id=None, age_min=0, age_max=14,
                gender='N', category='Geral', language='PT-BR', is_safe=1):
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO channels
            (name, handle, channel_id, age_min, age_max, gender, category, language, is_safe)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (name, handle, channel_id, age_min, age_max, gender, category, language, is_safe))
    conn.commit()
    conn.close()


def stats():
    conn = get_conn()
    r = {
        'channels':          conn.execute("SELECT COUNT(*) FROM channels WHERE active=1").fetchone()[0],
        'channels_resolved': conn.execute("SELECT COUNT(*) FROM channels WHERE channel_id IS NOT NULL AND active=1").fetchone()[0],
        'videos':            conn.execute("SELECT COUNT(*) FROM videos WHERE embedding_ok=1").fetchone()[0],
        'videos_blocked':    conn.execute("SELECT COUNT(*) FROM videos WHERE embedding_ok=0").fetchone()[0],
        'clients':           conn.execute("SELECT COUNT(*) FROM clients WHERE active=1").fetchone()[0],
        'modes':             list(MODES.keys()),
    }
    conn.close()
    return r
