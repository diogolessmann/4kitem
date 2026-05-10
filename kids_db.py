"""
kids_db.py — KidsCurator: banco de dados SQLite
Tabelas: channels + videos
"""
import sqlite3
import os

# Suporte a DATA_DIR para Railway (volume persistente)
_base = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'kids.db')

# ── Canais semente (23 canais verificados) ─────────────────────────────────
# (name, handle, age_min, age_max, gender, category, language, is_safe)
# gender: M=menino  F=menina  N=neutro/ambos
SEED_CHANNELS = [
    # 0-4 anos
    ('Galinha Pintadinha',   '@galinhapintadinha',          0,  4, 'N', 'Musical',        'PT-BR', 1),
    ('Mundo Bita',           '@mundobita',                  0,  5, 'N', 'Musical',        'PT-BR', 1),
    ('Super Simple Songs',   '@SuperSimpleSongs',           0,  4, 'N', 'Musical',        'Sem fala', 1),
    ('Pocoyo PT-BR',         '@Pocoyo',                     2,  6, 'N', 'Animação',       'PT-BR', 1),
    ('Peppa Pig PT-BR',      '@PeppaPigBrasil',             2,  6, 'N', 'Animação',       'PT-BR', 1),
    # 3-7 anos
    ('Patati Patatá',        '@PatatiPatataOficial',        3,  6, 'N', 'Humor',          'PT-BR', 1),
    ('Cocoricó',             '@cocorico',                   3,  6, 'N', 'Educativo',      'PT-BR', 1),
    ('Numberblocks PT-BR',   '@numberblocks_pt',            3,  7, 'N', 'Educativo',      'PT-BR', 1),
    ('Patrulha Canina',      '@PAWPatrolPortuguesBrasil',   3,  7, 'N', 'Aventura',       'PT-BR', 1),
    ('Bluey',                '@BlueyOfficialChannel',       3,  8, 'N', 'Animação',       'EN',    1),
    ('Larva TUBA',           '@LarvaOfficialChannel',       3,  7, 'N', 'Humor',          'Sem fala', 1),
    ('Oddbods',              '@Oddbods',                    3,  7, 'N', 'Humor',          'Sem fala', 1),
    ('Hey Duggee',           '@HeyDuggee',                  3,  7, 'N', 'Educativo',      'EN',    1),
    ('Masha e o Urso',       '@MashaandBear',               3,  8, 'F', 'Animação',       'PT-BR', 1),
    ('Peppa Pig',            '@PeppaPig',                   3,  6, 'F', 'Animação',       'EN',    1),
    # 4-10 anos
    ('Mônica Toy',           '@monicatoy',                  4,  9, 'N', 'Animação',       'PT-BR', 1),
    ('Turma da Mônica',      '@TurmaDaMonica',              4, 10, 'N', 'Animação',       'PT-BR', 1),
    ('CoComelon PT-BR',      '@CoComelon',                  0,  5, 'N', 'Musical',        'PT-BR', 1),
    # 6-12 anos
    ('Luccas Neto',          '@Luccasneto',                 6, 12, 'N', 'Humor',          'PT-BR', 0),
    ('Manual do Mundo Kids', '@manualdomundokids9300',      6, 12, 'N', 'Educativo',      'PT-BR', 1),
    ('Canal da Belinha',     '@CanaldaBelinhaOficial',      6, 10, 'F', 'Entretenimento', 'PT-BR', 1),
    # 9-14 anos
    ('Enaldinho',            '@Enaldinho',                  9, 14, 'N', 'Humor',          'PT-BR', 0),
    ('Authentic Games',      '@Authenticgames',             9, 14, 'M', 'Minecraft',      'PT-BR', 1),
    ('Julia MineGirl',       '@juliaminegirl',              9, 14, 'F', 'Minecraft',      'PT-BR', 1),
]


# ── Conexão ────────────────────────────────────────────────────────────────
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if os.path.dirname(DB_PATH) else None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_ref  INTEGER REFERENCES channels(id) ON DELETE CASCADE,
            youtube_id   TEXT    UNIQUE NOT NULL,
            title        TEXT,
            thumbnail    TEXT,
            published_at TEXT,
            age_min      INTEGER DEFAULT 0,
            age_max      INTEGER DEFAULT 14,
            gender       TEXT    DEFAULT 'N',
            last_seen    TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_vid_gender ON videos(gender);
        CREATE INDEX IF NOT EXISTS idx_vid_age    ON videos(age_min, age_max);
        CREATE INDEX IF NOT EXISTS idx_vid_pub    ON videos(published_at DESC);
    """)

    # Seed canais se banco vazio
    n = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    if n == 0:
        conn.executemany("""
            INSERT OR IGNORE INTO channels
                (name, handle, age_min, age_max, gender, category, language, is_safe)
            VALUES (?,?,?,?,?,?,?,?)
        """, SEED_CHANNELS)

    conn.commit()
    conn.close()


# ── Leitura ────────────────────────────────────────────────────────────────
def get_videos(age=None, gender=None, limit=24, offset=0):
    """
    Retorna lista de vídeos filtrados por idade e género.
    age   = número inteiro (ex: 3) → filtra age_min <= age <= age_max
    gender= 'M' | 'F' | 'N' | None  (N ou None = todos)
    """
    conn = get_conn()
    params = []
    where  = ["c.active = 1"]

    if age is not None:
        where.append("v.age_min <= ? AND v.age_max >= ?")
        params += [age, age]

    if gender and gender in ('M', 'F'):
        where.append("(v.gender = ? OR v.gender = 'N')")
        params.append(gender)

    where_sql = " AND ".join(where)
    params += [limit, offset]

    rows = conn.execute(f"""
        SELECT
            v.youtube_id, v.title, v.thumbnail, v.published_at,
            v.age_min, v.age_max, v.gender,
            c.name      AS channel_name,
            c.handle    AS channel_handle,
            c.category,
            c.is_safe
        FROM videos v
        JOIN channels c ON c.id = v.channel_ref
        WHERE {where_sql}
        ORDER BY v.published_at DESC
        LIMIT ? OFFSET ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_channels(active_only=True):
    conn = get_conn()
    q = "SELECT * FROM channels"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY name"
    rows = conn.execute(q).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def total_videos(age=None, gender=None):
    conn = get_conn()
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
        WHERE {" AND ".join(where)}
    """, params).fetchone()[0]
    conn.close()
    return n


def update_channel_id(db_id: int, yt_channel_id: str):
    conn = get_conn()
    try:
        # Limpa qualquer canal que já tenha esse channel_id (pode ter sido atribuído errado num teste)
        conn.execute(
            "UPDATE channels SET channel_id = NULL WHERE channel_id = ? AND id != ?",
            (yt_channel_id, db_id)
        )
        conn.execute("UPDATE channels SET channel_id = ? WHERE id = ?", (yt_channel_id, db_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def add_channel(name, handle, age_min, age_max, gender, category, language='PT-BR', is_safe=1):
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO channels
            (name, handle, age_min, age_max, gender, category, language, is_safe)
        VALUES (?,?,?,?,?,?,?,?)
    """, (name, handle, age_min, age_max, gender, category, language, is_safe))
    conn.commit()
    conn.close()


def stats():
    conn = get_conn()
    r = {
        'channels': conn.execute("SELECT COUNT(*) FROM channels WHERE active=1").fetchone()[0],
        'channels_resolved': conn.execute("SELECT COUNT(*) FROM channels WHERE channel_id IS NOT NULL AND active=1").fetchone()[0],
        'videos': conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
    }
    conn.close()
    return r
