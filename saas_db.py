"""
saas_db.py — Banco de dados para AgendaSC e AlertaSC
SQLite separado do kids.db para manter os dados do SaaS isolados.
"""
import os
import sqlite3

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'saas.db')


def get_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_saas_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS agenda_businesses (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            slug          TEXT NOT NULL UNIQUE,
            owner_name    TEXT NOT NULL,
            phone         TEXT NOT NULL,
            email         TEXT,
            business_type TEXT DEFAULT "outros",
            description   TEXT,
            address       TEXT,
            password_hash TEXT NOT NULL,
            active        INTEGER DEFAULT 1,
            created_at    TEXT,
            trial_ends    TEXT
        );

        CREATE TABLE IF NOT EXISTS agenda_services (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id      INTEGER NOT NULL,
            name             TEXT NOT NULL,
            duration_minutes INTEGER DEFAULT 60,
            price            REAL DEFAULT 0,
            active           INTEGER DEFAULT 1,
            created_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS agenda_availability (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            weekday     INTEGER NOT NULL,
            start_time  TEXT NOT NULL,
            end_time    TEXT NOT NULL,
            active      INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS agenda_appointments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id      INTEGER NOT NULL,
            service_id       INTEGER,
            customer_name    TEXT NOT NULL,
            customer_phone   TEXT NOT NULL,
            customer_notes   TEXT,
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            status           TEXT DEFAULT "pending",
            created_at       TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_agenda_biz_slug    ON agenda_businesses(slug);
        CREATE INDEX IF NOT EXISTS idx_agenda_appt_biz    ON agenda_appointments(business_id);
        CREATE INDEX IF NOT EXISTS idx_agenda_appt_date   ON agenda_appointments(appointment_date);
        CREATE INDEX IF NOT EXISTS idx_agenda_appt_status ON agenda_appointments(status);

        CREATE TABLE IF NOT EXISTS alerta_subscribers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            cpf            TEXT,
            plates_json    TEXT,
            phone          TEXT NOT NULL,
            email          TEXT,
            plano          TEXT DEFAULT "basico",
            status         TEXT DEFAULT "pending",
            payment_status TEXT DEFAULT "pending",
            paid_at        TEXT,
            notes          TEXT,
            created_at     TEXT,
            last_report_at TEXT
        );

        CREATE TABLE IF NOT EXISTS alerta_reports (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            subscriber_id INTEGER NOT NULL,
            message       TEXT,
            sent_at       TEXT,
            created_at    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_alerta_sub_status ON alerta_subscribers(status);
        CREATE INDEX IF NOT EXISTS idx_alerta_rep_sub    ON alerta_reports(subscriber_id);

        CREATE TABLE IF NOT EXISTS bau_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            active        INTEGER DEFAULT 1,
            created_at    TEXT,
            trial_ends    TEXT
        );

        CREATE TABLE IF NOT EXISTS bau_entries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            title      TEXT NOT NULL,
            url        TEXT,
            username   TEXT,
            hint       TEXT,
            category   TEXT DEFAULT 'outros',
            created_at TEXT,
            updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_bau_entries_user ON bau_entries(user_id);

        -- ── MandaZap ─────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS mandazap_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            plan          TEXT DEFAULT 'solo',
            active        INTEGER DEFAULT 1,
            created_at    TEXT,
            trial_ends    TEXT
        );

        CREATE TABLE IF NOT EXISTS mandazap_numbers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            label      TEXT NOT NULL,
            phone      TEXT,
            status     TEXT DEFAULT 'disconnected',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS mandazap_contacts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            name       TEXT NOT NULL,
            phone      TEXT NOT NULL,
            email      TEXT,
            tag        TEXT,
            notes      TEXT,
            created_at TEXT,
            UNIQUE(user_id, phone)
        );

        CREATE TABLE IF NOT EXISTS mandazap_lists (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            name        TEXT NOT NULL,
            description TEXT,
            created_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS mandazap_list_contacts (
            list_id    INTEGER NOT NULL,
            contact_id INTEGER NOT NULL,
            PRIMARY KEY (list_id, contact_id)
        );

        CREATE TABLE IF NOT EXISTS mandazap_campaigns (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            name         TEXT NOT NULL,
            message      TEXT NOT NULL,
            media_type   TEXT DEFAULT 'text',
            list_id      INTEGER,
            number_id    INTEGER,
            status       TEXT DEFAULT 'draft',
            total        INTEGER DEFAULT 0,
            sent         INTEGER DEFAULT 0,
            scheduled_at TEXT,
            created_at   TEXT,
            finished_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS mandazap_templates (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            name       TEXT NOT NULL,
            message    TEXT NOT NULL,
            media_type TEXT DEFAULT 'text',
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_mz_users_email    ON mandazap_users(email);
        CREATE INDEX IF NOT EXISTS idx_mz_contacts_user  ON mandazap_contacts(user_id);
        CREATE INDEX IF NOT EXISTS idx_mz_campaigns_user ON mandazap_campaigns(user_id);
        CREATE INDEX IF NOT EXISTS idx_mz_numbers_user   ON mandazap_numbers(user_id);
        CREATE INDEX IF NOT EXISTS idx_mz_lists_user     ON mandazap_lists(user_id);
        CREATE INDEX IF NOT EXISTS idx_mz_templates_user ON mandazap_templates(user_id);

        -- ── MandaZap migrations suaves ───────────────────────────────────────
        -- (ignoradas se a coluna já existe — SQLite não tem IF NOT EXISTS em ALTER)

        -- ── Dev Notes ─────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS dev_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo     TEXT NOT NULL DEFAULT '',
            texto      TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    conn.commit()

    # ── Novas tabelas Agenda SC ────────────────────────────────────────────────
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS agenda_customers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id  INTEGER NOT NULL,
            name         TEXT NOT NULL,
            phone        TEXT NOT NULL,
            total_visits INTEGER DEFAULT 0,
            total_spent  REAL DEFAULT 0,
            last_visit   TEXT,
            created_at   TEXT,
            UNIQUE(business_id, phone)
        );

        CREATE TABLE IF NOT EXISTS agenda_payments (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id    INTEGER NOT NULL,
            appointment_id INTEGER,
            customer_phone TEXT,
            amount         REAL NOT NULL,
            method         TEXT DEFAULT "dinheiro",
            paid_at        TEXT,
            notes          TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_agenda_cust_biz   ON agenda_customers(business_id);
        CREATE INDEX IF NOT EXISTS idx_agenda_pay_biz    ON agenda_payments(business_id);
        CREATE INDEX IF NOT EXISTS idx_agenda_pay_appt   ON agenda_payments(appointment_id);
    ''')
    conn.commit()

    # ── Tabela de log de envios por campanha (para "continuar de onde parou") ──
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS mandazap_sent_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            phone       TEXT    NOT NULL,
            sent_at     TEXT,
            UNIQUE(campaign_id, phone)
        );
        CREATE INDEX IF NOT EXISTS idx_mz_sent_log_camp ON mandazap_sent_log(campaign_id);
    ''')

    # ── Migrations suaves (adicionadas após schema inicial) ─────────────────
    _saas_migrations = [
        # MandaZap campaigns
        "ALTER TABLE mandazap_campaigns ADD COLUMN media_url TEXT DEFAULT ''",
        "ALTER TABLE mandazap_templates ADD COLUMN media_url TEXT DEFAULT ''",
        "ALTER TABLE mandazap_campaigns ADD COLUMN error_log TEXT DEFAULT ''",
        # Agenda SC — configurações por negócio
        "ALTER TABLE agenda_businesses ADD COLUMN mandazap_ativo INTEGER DEFAULT 0",
        "ALTER TABLE agenda_businesses ADD COLUMN mandazap_instance TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN pix_chave TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN pix_nome TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN msg_confirmacao TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN msg_lembrete TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN msg_cancelamento TEXT DEFAULT ''",
        # Agenda appointments — pagamento
        "ALTER TABLE agenda_appointments ADD COLUMN paid INTEGER DEFAULT 0",
        "ALTER TABLE agenda_appointments ADD COLUMN paid_amount REAL DEFAULT 0",
        "ALTER TABLE agenda_appointments ADD COLUMN paid_method TEXT DEFAULT ''",
    ]
    for sql in _saas_migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # Coluna já existe — ok

    conn.close()


def salvar_nota_dev(titulo: str, texto: str) -> int:
    conn = get_db()
    cur  = conn.execute(
        'INSERT INTO dev_notes (titulo, texto) VALUES (?, ?)', (titulo, texto)
    )
    conn.commit()
    id_  = cur.lastrowid
    conn.close()
    return id_


def listar_notas_dev() -> list:
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM dev_notes ORDER BY id DESC'
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
