"""
petmed_db.py — Banco de dados PETmed
Triagem veterinária inteligente 24/7
"""
import os
import sqlite3

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'petmed.db')


def get_petmed_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_petmed_db():
    conn = get_petmed_db()
    conn.executescript('''

        -- ── Tutores (usuários B2C) ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS petmed_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nome          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            telefone      TEXT,
            cpf           TEXT,
            password_hash TEXT NOT NULL,
            plano         TEXT DEFAULT "start",
            plano_ativo   INTEGER DEFAULT 1,
            trial_ends    TEXT,
            reset_token   TEXT,
            reset_expires TEXT,
            asaas_customer_id TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso TEXT
        );

        -- ── Pets cadastrados ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS petmed_pets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            nome       TEXT NOT NULL,
            especie    TEXT NOT NULL DEFAULT "cao",
            raca       TEXT,
            idade_anos INTEGER,
            idade_meses INTEGER DEFAULT 0,
            peso_kg    REAL,
            sexo       TEXT DEFAULT "nao_informado",
            castrado   INTEGER DEFAULT 0,
            cor        TEXT,
            foto_url   TEXT,
            observacoes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES petmed_users(id)
        );

        -- ── Triagens realizadas ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS petmed_triagens (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            pet_id          INTEGER,
            pet_nome        TEXT,
            pet_especie     TEXT,
            pet_raca        TEXT,
            categoria       TEXT,
            sintomas        TEXT,
            perguntas_json  TEXT,
            resultado       TEXT DEFAULT "estavel",
            orientacoes     TEXT,
            encaminhar_vet  INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES petmed_users(id),
            FOREIGN KEY (pet_id)  REFERENCES petmed_pets(id)
        );

        -- ── Veterinários parceiros ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS petmed_vets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nome          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            telefone      TEXT NOT NULL,
            crmv          TEXT NOT NULL,
            estado_crmv   TEXT DEFAULT "SC",
            especialidade TEXT,
            bio           TEXT,
            foto_url      TEXT,
            disponivel    INTEGER DEFAULT 1,
            avaliacao     REAL DEFAULT 5.0,
            total_consultas INTEGER DEFAULT 0,
            valor_consulta  REAL DEFAULT 79.90,
            password_hash   TEXT NOT NULL,
            ativo         INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Teleconsultas ──────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS petmed_teleconsultas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            vet_id       INTEGER,
            pet_id       INTEGER,
            triagem_id   INTEGER,
            status       TEXT DEFAULT "aguardando",
            valor        REAL DEFAULT 79.90,
            link_sala    TEXT,
            notas_vet    TEXT,
            prescricao   TEXT,
            duracao_min  INTEGER,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            iniciada_at  TEXT,
            finalizada_at TEXT,
            FOREIGN KEY (user_id)    REFERENCES petmed_users(id),
            FOREIGN KEY (vet_id)     REFERENCES petmed_vets(id),
            FOREIGN KEY (pet_id)     REFERENCES petmed_pets(id),
            FOREIGN KEY (triagem_id) REFERENCES petmed_triagens(id)
        );

        -- ── Vacinas e lembretes ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS petmed_vacinas (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pet_id     INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            nome       TEXT NOT NULL,
            data_aplic TEXT,
            proxima    TEXT,
            lembrar    INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (pet_id)  REFERENCES petmed_pets(id),
            FOREIGN KEY (user_id) REFERENCES petmed_users(id)
        );

        -- ── Assinaturas / pagamentos ───────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS petmed_assinaturas (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL UNIQUE,
            plano          TEXT NOT NULL DEFAULT "start",
            valor          REAL,
            status         TEXT DEFAULT "ativo",
            gateway_id     TEXT,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            renovacao_em   TEXT,
            cancelado_em   TEXT,
            FOREIGN KEY (user_id) REFERENCES petmed_users(id)
        );

        -- ── Compras de crédito (pago por atendimento) ──────────────────────────
        CREATE TABLE IF NOT EXISTS petmed_compras (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            pacote          TEXT,
            creditos        INTEGER NOT NULL,
            valor           REAL,
            status          TEXT DEFAULT "pendente",
            asaas_payment_id TEXT,
            billing_type    TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES petmed_users(id)
        );

        -- ── Clínicas B2B parceiras ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS petmed_clinicas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nome          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            telefone      TEXT,
            whatsapp      TEXT,
            cidade        TEXT,
            estado        TEXT DEFAULT "SC",
            endereco      TEXT,
            crmv_rt       TEXT,
            logo_url      TEXT,
            password_hash TEXT NOT NULL,
            plano         TEXT DEFAULT "clinica",
            ativo         INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Índices ────────────────────────────────────────────────────────────
        CREATE INDEX IF NOT EXISTS idx_petmed_pets_user      ON petmed_pets(user_id);
        CREATE INDEX IF NOT EXISTS idx_petmed_triagens_user  ON petmed_triagens(user_id);
        CREATE INDEX IF NOT EXISTS idx_petmed_triagens_pet   ON petmed_triagens(pet_id);
        CREATE INDEX IF NOT EXISTS idx_petmed_triagens_data  ON petmed_triagens(created_at);
        CREATE INDEX IF NOT EXISTS idx_petmed_consul_user    ON petmed_teleconsultas(user_id);
        CREATE INDEX IF NOT EXISTS idx_petmed_consul_vet     ON petmed_teleconsultas(vet_id);
        CREATE INDEX IF NOT EXISTS idx_petmed_compras_user   ON petmed_compras(user_id);
        CREATE INDEX IF NOT EXISTS idx_petmed_compras_pay    ON petmed_compras(asaas_payment_id);

    ''')
    conn.commit()

    # ── Migrações seguras (ADD COLUMN se não existir) ──────────────────────────
    for migration in [
        'ALTER TABLE petmed_users ADD COLUMN cpf TEXT',
        'ALTER TABLE petmed_users ADD COLUMN plano TEXT DEFAULT "start"',
        'ALTER TABLE petmed_users ADD COLUMN plano_ativo INTEGER DEFAULT 1',
        'ALTER TABLE petmed_users ADD COLUMN trial_ends TEXT',
        'ALTER TABLE petmed_users ADD COLUMN reset_token TEXT',
        'ALTER TABLE petmed_users ADD COLUMN reset_expires TEXT',
        'ALTER TABLE petmed_users ADD COLUMN asaas_customer_id TEXT',
        'ALTER TABLE petmed_users ADD COLUMN ultimo_acesso TEXT',
        'ALTER TABLE petmed_users ADD COLUMN consulta_expires TEXT',
        'ALTER TABLE petmed_assinaturas ADD COLUMN asaas_subscription_id TEXT',
        'ALTER TABLE petmed_assinaturas ADD COLUMN asaas_payment_id TEXT',
        'ALTER TABLE petmed_assinaturas ADD COLUMN billing_type TEXT',
        # ── Modelo de CRÉDITOS (pago por atendimento) ──────────────────────────
        'ALTER TABLE petmed_users ADD COLUMN creditos INTEGER DEFAULT 0',
    ]:
        try:
            conn.execute(migration)
            conn.commit()
        except Exception:
            try: conn.rollback()
            except: pass

    conn.close()
