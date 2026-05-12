"""
saas_db.py — Banco de dados para AgendaSC e AlertaSC
SQLite separado do kids.db para manter os dados do SaaS isolados.
"""
import os
import sqlite3

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'saas.db')


def get_db():
    if os.path.dirname(DB_PATH):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    ''')
    conn.commit()
    conn.close()
