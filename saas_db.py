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

    # ── Amigo Despachante users (assinantes do produto) ───────────────────────
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS despachante_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT,
            phone         TEXT NOT NULL,
            empresa       TEXT,
            cidade        TEXT,
            plan          TEXT DEFAULT 'basico',
            active        INTEGER DEFAULT 1,
            created_at    TEXT,
            trial_ends    TEXT,
            notes         TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_desp_users_active ON despachante_users(active);

        CREATE TABLE IF NOT EXISTS defesapro_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT,
            phone         TEXT NOT NULL,
            escritorio    TEXT,
            cidade        TEXT,
            plan          TEXT DEFAULT 'starter',
            active        INTEGER DEFAULT 1,
            created_at    TEXT,
            trial_ends    TEXT,
            notes         TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_defesapro_users_active ON defesapro_users(active);
    ''')
    conn.commit()

    # ── Migrations suaves (adicionadas após schema inicial) ─────────────────
    _saas_migrations = [
        # MandaZap campaigns
        "ALTER TABLE mandazap_campaigns ADD COLUMN media_url TEXT DEFAULT ''",
        "ALTER TABLE mandazap_templates ADD COLUMN media_url TEXT DEFAULT ''",
        "ALTER TABLE mandazap_campaigns ADD COLUMN error_log TEXT DEFAULT ''",
        "ALTER TABLE mandazap_campaigns ADD COLUMN updated_at TEXT DEFAULT ''",
        # Amigo Despachante — autenticação
        "ALTER TABLE despachante_users ADD COLUMN password_hash TEXT DEFAULT ''",
        "ALTER TABLE despachante_users ADD COLUMN last_login TEXT DEFAULT ''",
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
        # DefesaPro — autenticação e anti-golpe
        "ALTER TABLE defesapro_users ADD COLUMN password_hash TEXT DEFAULT ''",
        "ALTER TABLE defesapro_users ADD COLUMN last_login TEXT DEFAULT ''",
        "ALTER TABLE defesapro_users ADD COLUMN cpf_cnpj TEXT DEFAULT ''",
        # Baú — anti-golpe (telefone + CPF/CNPJ únicos)
        "ALTER TABLE bau_users ADD COLUMN phone TEXT DEFAULT ''",
        "ALTER TABLE bau_users ADD COLUMN cpf_cnpj TEXT DEFAULT ''",
        # MandaZap — anti-golpe
        "ALTER TABLE mandazap_users ADD COLUMN phone TEXT DEFAULT ''",
        "ALTER TABLE mandazap_users ADD COLUMN cpf_cnpj TEXT DEFAULT ''",
        # Agenda SC — CPF do responsável
        "ALTER TABLE agenda_businesses ADD COLUMN cpf_cnpj TEXT DEFAULT ''",
        # Agenda SC — Multi-profissional
        "ALTER TABLE agenda_appointments ADD COLUMN professional_id INTEGER",
        "ALTER TABLE agenda_appointments ADD COLUMN professional_name TEXT DEFAULT ''",
        '''CREATE TABLE IF NOT EXISTS agenda_professionals (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id    INTEGER NOT NULL,
            name           TEXT NOT NULL,
            role           TEXT DEFAULT '',
            photo_url      TEXT DEFAULT '',
            color          TEXT DEFAULT '#27ae60',
            bio            TEXT DEFAULT '',
            commission_pct REAL DEFAULT 0,
            active         INTEGER DEFAULT 1,
            order_pos      INTEGER DEFAULT 0,
            created_at     TEXT DEFAULT ''
        )''',
        "CREATE INDEX IF NOT EXISTS idx_agenda_prof_biz ON agenda_professionals(business_id)",
        # DefesaPro Premium — monitor de e-mail e notificações
        '''CREATE TABLE IF NOT EXISTS defesapro_email_config (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER UNIQUE NOT NULL,
            imap_host   TEXT DEFAULT 'imap.gmail.com',
            imap_port   INTEGER DEFAULT 993,
            email_addr  TEXT NOT NULL DEFAULT '',
            senha_b64   TEXT NOT NULL DEFAULT '',
            ativo       INTEGER DEFAULT 1,
            ultimo_check TEXT DEFAULT '',
            total_lidos  INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT ''
        )''',
        '''CREATE TABLE IF NOT EXISTS defesapro_notificacoes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            tipo        TEXT DEFAULT 'email',
            titulo      TEXT NOT NULL DEFAULT '',
            corpo       TEXT DEFAULT '',
            processo_id INTEGER,
            lida        INTEGER DEFAULT 0,
            email_de    TEXT DEFAULT '',
            email_assunto TEXT DEFAULT '',
            created_at  TEXT DEFAULT ''
        )''',
        "CREATE INDEX IF NOT EXISTS idx_defesa_notif_user ON defesapro_notificacoes(user_id, lida)",
    ]
    for sql in _saas_migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # Coluna já existe — ok

    # ── MandaJá — Delivery App ───────────────────────────────────────────────
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS mandaja_stores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            slug            TEXT NOT NULL UNIQUE,
            owner_name      TEXT NOT NULL,
            phone           TEXT NOT NULL,
            email           TEXT DEFAULT '',
            password_hash   TEXT NOT NULL DEFAULT '',
            logo_url        TEXT DEFAULT '',
            description     TEXT DEFAULT '',
            category        TEXT DEFAULT 'restaurante',
            address         TEXT DEFAULT '',
            neighborhood    TEXT DEFAULT '',
            city            TEXT DEFAULT '',
            state           TEXT DEFAULT 'SC',
            cep             TEXT DEFAULT '',
            pix_chave       TEXT DEFAULT '',
            pix_nome        TEXT DEFAULT '',
            delivery_fee    REAL DEFAULT 0,
            min_order       REAL DEFAULT 0,
            delivery_time   INTEGER DEFAULT 45,
            delivery_radius INTEGER DEFAULT 5,
            whatsapp        TEXT DEFAULT '',
            active          INTEGER DEFAULT 1,
            plan            TEXT DEFAULT 'micro',
            created_at      TEXT DEFAULT '',
            trial_ends      TEXT DEFAULT '',
            notes           TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS mandaja_categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id    INTEGER NOT NULL,
            name        TEXT NOT NULL,
            sort_order  INTEGER DEFAULT 0,
            active      INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS mandaja_products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id    INTEGER NOT NULL,
            category_id INTEGER DEFAULT NULL,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            price       REAL NOT NULL DEFAULT 0,
            cost        REAL DEFAULT 0,
            photo_url   TEXT DEFAULT '',
            stock       INTEGER DEFAULT -1,
            active      INTEGER DEFAULT 1,
            sort_order  INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS mandaja_hours (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id    INTEGER NOT NULL,
            weekday     INTEGER NOT NULL,
            open_time   TEXT NOT NULL DEFAULT '08:00',
            close_time  TEXT NOT NULL DEFAULT '22:00',
            active      INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS mandaja_orders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id        INTEGER NOT NULL,
            order_number    TEXT NOT NULL DEFAULT '',
            customer_name   TEXT NOT NULL DEFAULT '',
            customer_phone  TEXT NOT NULL DEFAULT '',
            customer_notes  TEXT DEFAULT '',
            delivery_type   TEXT DEFAULT 'delivery',
            address         TEXT DEFAULT '',
            neighborhood    TEXT DEFAULT '',
            city            TEXT DEFAULT '',
            cep             TEXT DEFAULT '',
            payment_method  TEXT DEFAULT 'pix',
            payment_status  TEXT DEFAULT 'pending',
            subtotal        REAL DEFAULT 0,
            delivery_fee    REAL DEFAULT 0,
            total           REAL DEFAULT 0,
            status          TEXT DEFAULT 'new',
            items_json      TEXT DEFAULT '[]',
            created_at      TEXT DEFAULT '',
            updated_at      TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS mandaja_financial (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id    INTEGER NOT NULL,
            order_id    INTEGER DEFAULT NULL,
            type        TEXT DEFAULT 'receita',
            description TEXT DEFAULT '',
            amount      REAL NOT NULL DEFAULT 0,
            date        TEXT DEFAULT '',
            created_at  TEXT DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_mja_stores_slug     ON mandaja_stores(slug);
        CREATE INDEX IF NOT EXISTS idx_mja_products_store  ON mandaja_products(store_id);
        CREATE INDEX IF NOT EXISTS idx_mja_orders_store    ON mandaja_orders(store_id);
        CREATE INDEX IF NOT EXISTS idx_mja_orders_status   ON mandaja_orders(status);
        CREATE INDEX IF NOT EXISTS idx_mja_financial_store ON mandaja_financial(store_id);
        CREATE INDEX IF NOT EXISTS idx_mja_hours_store     ON mandaja_hours(store_id);
        CREATE INDEX IF NOT EXISTS idx_mja_categories_store ON mandaja_categories(store_id);
    ''')
    conn.commit()

    # ── MandaJá migrations suaves ────────────────────────────────────────────
    _mandaja_migrations = [
        "ALTER TABLE mandaja_stores ADD COLUMN banner_url TEXT DEFAULT ''",
        "ALTER TABLE mandaja_stores ADD COLUMN accepts_card INTEGER DEFAULT 1",
        "ALTER TABLE mandaja_stores ADD COLUMN accepts_cash INTEGER DEFAULT 1",
        "ALTER TABLE mandaja_products ADD COLUMN options_json TEXT DEFAULT '[]'",
        "ALTER TABLE mandaja_orders ADD COLUMN change_for REAL DEFAULT 0",
        "ALTER TABLE mandaja_stores ADD COLUMN cpf_cnpj TEXT DEFAULT ''",
        "ALTER TABLE mandaja_stores ADD COLUMN mandazap_instance TEXT DEFAULT ''",
        "ALTER TABLE mandaja_stores ADD COLUMN mandazap_ativo INTEGER DEFAULT 0",
        "ALTER TABLE mandaja_orders ADD COLUMN wa_sent_confirmed INTEGER DEFAULT 0",
        "ALTER TABLE mandaja_orders ADD COLUMN wa_sent_ready INTEGER DEFAULT 0",
        "ALTER TABLE mandaja_orders ADD COLUMN wa_sent_delivered INTEGER DEFAULT 0",
    ]
    # ── DefesaPro / apps gerais — reset de senha e Asaas ────────────────────────
    _auth_migrations = [
        "ALTER TABLE defesapro_users ADD COLUMN reset_token TEXT DEFAULT ''",
        "ALTER TABLE defesapro_users ADD COLUMN reset_expires TEXT DEFAULT ''",
        "ALTER TABLE defesapro_users ADD COLUMN asaas_customer_id TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN reset_token TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN reset_expires TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN asaas_customer_id TEXT DEFAULT ''",
        "ALTER TABLE mandaja_stores ADD COLUMN reset_token TEXT DEFAULT ''",
        "ALTER TABLE mandaja_stores ADD COLUMN reset_expires TEXT DEFAULT ''",
        "ALTER TABLE mandaja_stores ADD COLUMN asaas_customer_id TEXT DEFAULT ''",
        "ALTER TABLE mandaja_stores ADD COLUMN plan_active INTEGER DEFAULT 1",
        "ALTER TABLE defesapro_users ADD COLUMN plan_active INTEGER DEFAULT 1",
        "ALTER TABLE mandazap_users ADD COLUMN reset_token TEXT DEFAULT ''",
        "ALTER TABLE mandazap_users ADD COLUMN reset_expires TEXT DEFAULT ''",
        "ALTER TABLE mandazap_users ADD COLUMN asaas_customer_id TEXT DEFAULT ''",
        "ALTER TABLE mandazap_users ADD COLUMN plan_active INTEGER DEFAULT 1",
        "ALTER TABLE despachante_users ADD COLUMN reset_token TEXT DEFAULT ''",
        "ALTER TABLE despachante_users ADD COLUMN reset_expires TEXT DEFAULT ''",
        "ALTER TABLE despachante_users ADD COLUMN password_hash TEXT DEFAULT ''",
        "ALTER TABLE despachante_users ADD COLUMN asaas_customer_id TEXT DEFAULT ''",
        "ALTER TABLE despachante_users ADD COLUMN plan_active INTEGER DEFAULT 1",
        "ALTER TABLE alerta_subscribers ADD COLUMN asaas_customer_id TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN plan_active INTEGER DEFAULT 1",
        # AlertaSC — tabela de débitos monitorados
        """CREATE TABLE IF NOT EXISTS alerta_debitos (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            subscriber_id  INTEGER NOT NULL,
            plate          TEXT NOT NULL,
            plate_desc     TEXT DEFAULT '',
            chave_unica    TEXT NOT NULL,
            tipo           TEXT DEFAULT '',
            descricao      TEXT DEFAULT '',
            valor          TEXT DEFAULT '',
            vencimento     TEXT DEFAULT '',
            situacao       TEXT DEFAULT 'pendente',
            found_at       TEXT,
            notificado     INTEGER DEFAULT 0,
            notificado_at  TEXT,
            UNIQUE(subscriber_id, chave_unica)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_alerta_deb_sub ON alerta_debitos(subscriber_id)",
        "CREATE INDEX IF NOT EXISTS idx_alerta_deb_notif ON alerta_debitos(notificado)",
        # AgendaSC — email do cliente (para enviar confirmação de agendamento)
        "ALTER TABLE agenda_appointments ADD COLUMN customer_email TEXT DEFAULT ''",
        # AgendaSC — lembrete automático WhatsApp (24h antes)
        "ALTER TABLE agenda_appointments ADD COLUMN reminded_at TEXT DEFAULT ''",
        # Baú — reset de senha + plano + Asaas
        "ALTER TABLE bau_users ADD COLUMN reset_token TEXT DEFAULT ''",
        "ALTER TABLE bau_users ADD COLUMN reset_expires TEXT DEFAULT ''",
        "ALTER TABLE bau_users ADD COLUMN plan TEXT DEFAULT 'mensal'",
        "ALTER TABLE bau_users ADD COLUMN plan_active INTEGER DEFAULT 0",
        "ALTER TABLE bau_users ADD COLUMN asaas_customer_id TEXT DEFAULT ''",
        # Amigo Despachante SaaS — Clientes e Ordens de Serviço
        """CREATE TABLE IF NOT EXISTS desp_clientes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            name       TEXT NOT NULL,
            cpf_cnpj   TEXT DEFAULT '',
            phone      TEXT DEFAULT '',
            email      TEXT DEFAULT '',
            plate      TEXT DEFAULT '',
            notes      TEXT DEFAULT '',
            created_at TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_desp_cli_user ON desp_clientes(user_id)",
        """CREATE TABLE IF NOT EXISTS desp_os (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            client_id   INTEGER,
            client_name TEXT DEFAULT '',
            tipo        TEXT DEFAULT 'outros',
            descricao   TEXT DEFAULT '',
            placa       TEXT DEFAULT '',
            status      TEXT DEFAULT 'pendente',
            valor       REAL DEFAULT 0,
            pago        INTEGER DEFAULT 0,
            prazo       TEXT DEFAULT '',
            created_at  TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_desp_os_user ON desp_os(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_desp_os_status ON desp_os(status)",
        # DefesaPro — campo condutor separado do proprietário
        "ALTER TABLE defesapro_processos ADD COLUMN condutor TEXT DEFAULT ''",
        # AgendaSC — foto de capa, limite de antecedência e token de cancelamento
        "ALTER TABLE agenda_businesses ADD COLUMN cover_photo TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN max_days_advance INTEGER DEFAULT 60",
        "ALTER TABLE agenda_appointments ADD COLUMN cancel_token TEXT DEFAULT ''",
        # AgendaSC — customização da página pública
        "ALTER TABLE agenda_businesses ADD COLUMN primary_color TEXT DEFAULT '#27ae60'",
        "ALTER TABLE agenda_businesses ADD COLUMN description TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN address TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN instagram TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN logo_url TEXT DEFAULT ''",
        # AgendaSC — Bloco 2: lembrete 2h antes + avaliação pós-atendimento
        "ALTER TABLE agenda_appointments ADD COLUMN reminded_2h_at TEXT DEFAULT ''",
        "ALTER TABLE agenda_businesses ADD COLUMN msg_avaliacao TEXT DEFAULT ''",
    ]
    for sql in _auth_migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            try: conn.rollback()
            except: pass

    for sql in _mandaja_migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            try: conn.rollback()
            except: pass

    # ── DefesaPro — módulos funcionais ────────────────────────────────────────
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS defesapro_clientes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            name       TEXT NOT NULL,
            cpf        TEXT DEFAULT '',
            phone      TEXT DEFAULT '',
            email      TEXT DEFAULT '',
            cnh        TEXT DEFAULT '',
            address    TEXT DEFAULT '',
            notes      TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS defesapro_processos (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            cliente_id     INTEGER DEFAULT NULL,
            numero_auto    TEXT DEFAULT '',
            placa          TEXT DEFAULT '',
            renavam        TEXT DEFAULT '',
            proprietario   TEXT DEFAULT '',
            data_infracao  TEXT DEFAULT '',
            hora_infracao  TEXT DEFAULT '',
            local_infracao TEXT DEFAULT '',
            orgao_autuador TEXT DEFAULT '',
            artigo_ctb     TEXT DEFAULT '',
            descricao      TEXT DEFAULT '',
            pontos         INTEGER DEFAULT 0,
            valor_multa    REAL DEFAULT 0,
            status         TEXT DEFAULT 'aberto',
            fase           TEXT DEFAULT 'defesa_previa',
            prazo_defesa   TEXT DEFAULT '',
            honorarios     REAL DEFAULT 0,
            pago           INTEGER DEFAULT 0,
            observacoes    TEXT DEFAULT '',
            created_at     TEXT DEFAULT '',
            updated_at     TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS defesapro_peticoes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            processo_id INTEGER DEFAULT NULL,
            tipo        TEXT DEFAULT 'defesa_previa',
            conteudo    TEXT DEFAULT '',
            teses_json  TEXT DEFAULT '[]',
            created_at  TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS defesapro_financeiro (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            processo_id INTEGER DEFAULT NULL,
            cliente_id  INTEGER DEFAULT NULL,
            tipo        TEXT DEFAULT 'honorario',
            descricao   TEXT DEFAULT '',
            valor       REAL NOT NULL DEFAULT 0,
            pago        INTEGER DEFAULT 0,
            data        TEXT DEFAULT '',
            created_at  TEXT DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_defesa_cli_user  ON defesapro_clientes(user_id);
        CREATE INDEX IF NOT EXISTS idx_defesa_proc_user ON defesapro_processos(user_id);
        CREATE INDEX IF NOT EXISTS idx_defesa_proc_stat ON defesapro_processos(status);
        CREATE INDEX IF NOT EXISTS idx_defesa_pet_user  ON defesapro_peticoes(user_id);
        CREATE INDEX IF NOT EXISTS idx_defesa_fin_user  ON defesapro_financeiro(user_id);
    ''')
    conn.commit()

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
