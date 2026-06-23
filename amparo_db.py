"""
amparo_db.py — Banco de dados do Amparo
SaaS de engajamento entre sessões para psicólogos (módulo 4kitem).

Privacidade (LGPD — dado de saúde é SENSÍVEL):
- NÃO guardamos conteúdo de sessão nem "conversa terapêutica".
- O motor (Lote 3) guarda apenas SINAIS estruturados (humor, escala, adesão) + log de uso.
- Consentimento do paciente é registrado e auditável; pode ser revogado a qualquer momento.

Multi-perfil:
- amparo_psicologos  → quem PAGA (B2B). Faz login.
- amparo_pacientes   → vinculados a um psicólogo. Interagem via WhatsApp / link com token.
"""
import os
import sqlite3
import secrets

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'amparo.db')


def get_amparo_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_amparo_db():
    conn = get_amparo_db()
    conn.executescript('''
        -- ── Psicólogos (B2B, quem paga) ────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS amparo_psicologos (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            nome                TEXT NOT NULL,
            email               TEXT NOT NULL UNIQUE,
            crp                 TEXT,                       -- registro profissional (CRP)
            telefone            TEXT,
            password_hash       TEXT NOT NULL,
            plano               TEXT DEFAULT 'trial',       -- trial | essencial | pro | clinica
            status              TEXT DEFAULT 'ativo',       -- ativo | suspenso | cancelado
            pacientes_limite    INTEGER DEFAULT 5,          -- teto de pacientes no motor (por plano)
            trial_expires       TEXT,
            asaas_customer_id   TEXT,
            asaas_subscription_id TEXT,
            termo_aceito        INTEGER DEFAULT 0,          -- aceite dos Termos + Política de Privacidade
            reset_token         TEXT,
            reset_expires       TEXT,
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso       TEXT
        );

        -- ── Pacientes (vinculados a um psicólogo) ──────────────────────────────
        CREATE TABLE IF NOT EXISTS amparo_pacientes (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            psicologo_id        INTEGER NOT NULL,
            nome                TEXT NOT NULL,
            telefone            TEXT,                       -- WhatsApp (E.164, ex: 5547999999999)
            email               TEXT,
            motor_ativo         INTEGER DEFAULT 0,          -- engajamento ligado? (só após consentir)
            consentimento       TEXT DEFAULT 'pendente',   -- pendente | aceito | revogado
            consentimento_data  TEXT,
            consent_token       TEXT UNIQUE,                -- link mágico p/ o paciente consentir/optar-out
            is_menor            INTEGER DEFAULT 0,          -- menor de idade?
            responsavel_nome    TEXT,                       -- se menor: nome do responsável legal
            responsavel_consent INTEGER DEFAULT 0,          -- consentimento do responsável + assentimento
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_contato      TEXT,
            FOREIGN KEY (psicologo_id) REFERENCES amparo_psicologos(id)
        );

        -- ── Log de consentimento (auditoria LGPD — quem aceitou/revogou e quando)
        CREATE TABLE IF NOT EXISTS amparo_consent_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_id   INTEGER NOT NULL,
            acao          TEXT,                  -- aceite | revogacao | opt_out
            versao_termo  TEXT,                  -- versão do texto de consentimento aceito
            canal         TEXT,                  -- web | whatsapp
            detalhe       TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (paciente_id) REFERENCES amparo_pacientes(id)
        );

        -- ── Configuração de agenda (1 por psicólogo) ───────────────────────────
        CREATE TABLE IF NOT EXISTS amparo_agenda (
            psicologo_id      INTEGER PRIMARY KEY,
            slug              TEXT UNIQUE,             -- link público de agendamento
            duracao_min       INTEGER DEFAULT 50,
            intervalo_min     INTEGER DEFAULT 10,     -- folga entre sessões
            antecedencia_horas INTEGER DEFAULT 12,    -- mínimo p/ o paciente marcar
            booking_ativo     INTEGER DEFAULT 0,      -- autoagendamento ligado?
            valor_sessao      REAL,                   -- usado no Lote 2 (PIX)
            FOREIGN KEY (psicologo_id) REFERENCES amparo_psicologos(id)
        );

        -- ── Horários de atendimento (janelas por dia da semana) ────────────────
        CREATE TABLE IF NOT EXISTS amparo_horarios (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            psicologo_id  INTEGER NOT NULL,
            dia_semana    INTEGER NOT NULL,           -- 0=segunda ... 6=domingo
            inicio        TEXT NOT NULL,              -- 'HH:MM'
            fim           TEXT NOT NULL,              -- 'HH:MM'
            FOREIGN KEY (psicologo_id) REFERENCES amparo_psicologos(id)
        );

        -- ── Agendamentos ───────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS amparo_agendamentos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            psicologo_id  INTEGER NOT NULL,
            paciente_id   INTEGER,
            data          TEXT NOT NULL,              -- 'YYYY-MM-DD'
            hora          TEXT NOT NULL,              -- 'HH:MM'
            duracao_min   INTEGER DEFAULT 50,
            status        TEXT DEFAULT 'agendado',    -- agendado|confirmado|cancelado|realizado|faltou
            origem        TEXT DEFAULT 'paciente',    -- paciente|psicologo
            lembrete_enviado   INTEGER DEFAULT 0,
            confirmacao_enviada INTEGER DEFAULT 0,
            observacao    TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (psicologo_id) REFERENCES amparo_psicologos(id),
            FOREIGN KEY (paciente_id)  REFERENCES amparo_pacientes(id)
        );

        -- ── Pagamentos (histórico de assinatura do psicólogo — Lote 2) ─────────
        CREATE TABLE IF NOT EXISTS amparo_pagamentos (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            psicologo_id     INTEGER NOT NULL,
            tipo             TEXT DEFAULT 'assinatura',
            plano            TEXT,
            ciclo            TEXT,                  -- mensal | anual
            valor            REAL,
            status           TEXT DEFAULT 'pendente',
            asaas_payment_id TEXT,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (psicologo_id) REFERENCES amparo_psicologos(id)
        );

        -- ── Interações do motor (Lote 3) — o feed de sinais ────────────────────
        -- NÃO guarda "conversa terapêutica": só a pergunta enviada e a resposta
        -- estruturada (humor/escala/tarefa). Conteúdo livre fica fora.
        CREATE TABLE IF NOT EXISTS amparo_interacoes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_id   INTEGER NOT NULL,
            psicologo_id  INTEGER NOT NULL,
            tipo          TEXT,                  -- checkin | escala | tarefa | psicoedu
            status        TEXT DEFAULT 'enviada',-- enviada | respondida | ignorada
            pergunta      TEXT,
            resposta      TEXT,                  -- resposta curta/estruturada do paciente
            escala_nome   TEXT,                  -- ex: PHQ-9 | GAD-7 (quando tipo=escala)
            escala_score  INTEGER,
            humor         INTEGER,               -- 1..5 (quando tipo=checkin)
            risco         INTEGER DEFAULT 0,     -- 1 se a resposta acionou o protocolo de crise
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            respondida_at TEXT,
            FOREIGN KEY (paciente_id)  REFERENCES amparo_pacientes(id),
            FOREIGN KEY (psicologo_id) REFERENCES amparo_psicologos(id)
        );

        -- ── Tarefas terapêuticas (configuradas PELO psicólogo) ─────────────────
        CREATE TABLE IF NOT EXISTS amparo_tarefas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_id   INTEGER NOT NULL,
            psicologo_id  INTEGER NOT NULL,
            descricao     TEXT NOT NULL,
            ativa         INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (paciente_id)  REFERENCES amparo_pacientes(id)
        );

        -- ── Log de crise (auditoria + alerta ao psicólogo) — guard-rail CFP ────
        CREATE TABLE IF NOT EXISTS amparo_crise_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_id      INTEGER NOT NULL,
            psicologo_id     INTEGER,
            trecho           TEXT,                 -- trecho que disparou (p/ o psicólogo avaliar)
            psicologo_avisado INTEGER DEFAULT 0,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (paciente_id) REFERENCES amparo_pacientes(id)
        );

        CREATE INDEX IF NOT EXISTS idx_amparo_pac_psi   ON amparo_pacientes(psicologo_id);
        CREATE INDEX IF NOT EXISTS idx_amparo_pac_token ON amparo_pacientes(consent_token);
        CREATE INDEX IF NOT EXISTS idx_amparo_consent   ON amparo_consent_log(paciente_id);
        CREATE INDEX IF NOT EXISTS idx_amparo_hor_psi   ON amparo_horarios(psicologo_id);
        CREATE INDEX IF NOT EXISTS idx_amparo_ag_psi    ON amparo_agendamentos(psicologo_id, data);
        CREATE INDEX IF NOT EXISTS idx_amparo_ag_pac    ON amparo_agendamentos(paciente_id);
        CREATE INDEX IF NOT EXISTS idx_amparo_int_pac   ON amparo_interacoes(paciente_id);
        CREATE INDEX IF NOT EXISTS idx_amparo_int_psi   ON amparo_interacoes(psicologo_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_amparo_tar_pac   ON amparo_tarefas(paciente_id);
        CREATE INDEX IF NOT EXISTS idx_amparo_crise_psi ON amparo_crise_log(psicologo_id);
    ''')
    conn.commit()

    # ── Migrações seguras (ADD COLUMN se não existir) ──────────────────────────
    for migration in [
        "ALTER TABLE amparo_psicologos ADD COLUMN crp TEXT",
        "ALTER TABLE amparo_psicologos ADD COLUMN pacientes_limite INTEGER DEFAULT 5",
        "ALTER TABLE amparo_psicologos ADD COLUMN asaas_subscription_id TEXT",
        "ALTER TABLE amparo_psicologos ADD COLUMN trial_expires TEXT",
        "ALTER TABLE amparo_pacientes ADD COLUMN consent_token TEXT",
        "ALTER TABLE amparo_pacientes ADD COLUMN is_menor INTEGER DEFAULT 0",
        "ALTER TABLE amparo_pacientes ADD COLUMN responsavel_nome TEXT",
        "ALTER TABLE amparo_pacientes ADD COLUMN responsavel_consent INTEGER DEFAULT 0",
        "ALTER TABLE amparo_pacientes ADD COLUMN cashback REAL DEFAULT 0",
        "ALTER TABLE amparo_psicologos ADD COLUMN cpf TEXT",
    ]:
        try:
            conn.execute(migration); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    conn.close()


# ── Helpers ────────────────────────────────────────────────────────────────────
def get_psicologo_by_email(email):
    conn = get_amparo_db()
    row = conn.execute('SELECT * FROM amparo_psicologos WHERE email=?',
                       (email.strip().lower(),)).fetchone()
    conn.close()
    return row


def get_psicologo(psi_id):
    conn = get_amparo_db()
    row = conn.execute('SELECT * FROM amparo_psicologos WHERE id=?', (psi_id,)).fetchone()
    conn.close()
    return row


def novo_consent_token():
    """Token único p/ o link de consentimento/opt-out do paciente."""
    return secrets.token_urlsafe(24)


def conta_pacientes_ativos(psi_id):
    """Quantos pacientes estão com o motor ligado (conta contra o limite do plano)."""
    conn = get_amparo_db()
    row = conn.execute(
        'SELECT COUNT(*) AS n FROM amparo_pacientes WHERE psicologo_id=? AND motor_ativo=1',
        (psi_id,)).fetchone()
    conn.close()
    return (row['n'] or 0) if row else 0


def registra_consentimento(paciente_id, acao, versao_termo, canal='web', detalhe=''):
    """Registra aceite/revogação no log de auditoria E atualiza o paciente."""
    conn = get_amparo_db()
    conn.execute(
        'INSERT INTO amparo_consent_log (paciente_id, acao, versao_termo, canal, detalhe) '
        'VALUES (?,?,?,?,?)', (paciente_id, acao, versao_termo, canal, detalhe))
    if acao == 'aceite':
        conn.execute("UPDATE amparo_pacientes SET consentimento='aceito', "
                     "consentimento_data=CURRENT_TIMESTAMP, motor_ativo=1 WHERE id=?",
                     (paciente_id,))
    elif acao in ('revogacao', 'opt_out'):
        conn.execute("UPDATE amparo_pacientes SET consentimento='revogado', "
                     "motor_ativo=0 WHERE id=?", (paciente_id,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# AGENDA (Lote 1)
# ══════════════════════════════════════════════════════════════════════════════
import re as _re


def norm_fone(tel):
    """Normaliza telefone p/ E.164 BR (só dígitos, prefixo 55). Vazio → ''."""
    d = _re.sub(r'\D', '', tel or '')
    if not d:
        return ''
    if not d.startswith('55'):
        d = '55' + d
    return d


def _slugify(txt):
    s = _re.sub(r'[^a-z0-9]+', '-', (txt or '').lower().strip())
    return _re.sub(r'-+', '-', s).strip('-')[:32] or 'psi'


def ensure_agenda_config(psi_id):
    """Retorna a config de agenda do psicólogo, criando uma padrão (com slug único) se faltar."""
    conn = get_amparo_db()
    row = conn.execute('SELECT * FROM amparo_agenda WHERE psicologo_id=?', (psi_id,)).fetchone()
    if not row:
        psi = conn.execute('SELECT nome FROM amparo_psicologos WHERE id=?', (psi_id,)).fetchone()
        base = _slugify(psi['nome'] if psi else 'psi')
        slug = f'{base}-{secrets.token_hex(3)}'
        conn.execute('INSERT INTO amparo_agenda (psicologo_id, slug) VALUES (?,?)', (psi_id, slug))
        conn.commit()
        row = conn.execute('SELECT * FROM amparo_agenda WHERE psicologo_id=?', (psi_id,)).fetchone()
    conn.close()
    return row


def set_agenda_config(psi_id, duracao_min, intervalo_min, antecedencia_horas, booking_ativo, valor_sessao=None):
    conn = get_amparo_db()
    conn.execute('''UPDATE amparo_agenda SET duracao_min=?, intervalo_min=?,
                    antecedencia_horas=?, booking_ativo=?, valor_sessao=? WHERE psicologo_id=?''',
                 (int(duracao_min), int(intervalo_min), int(antecedencia_horas),
                  1 if booking_ativo else 0, valor_sessao, psi_id))
    conn.commit(); conn.close()


def get_agenda_by_slug(slug):
    """Config + dados do psicólogo pelo slug público. None se não existir/booking desligado."""
    conn = get_amparo_db()
    row = conn.execute('''SELECT a.*, p.nome AS psi_nome, p.id AS psi_id
                          FROM amparo_agenda a JOIN amparo_psicologos p ON p.id = a.psicologo_id
                          WHERE a.slug=?''', (slug,)).fetchone()
    conn.close()
    return row


def get_horarios(psi_id):
    conn = get_amparo_db()
    rows = conn.execute('SELECT * FROM amparo_horarios WHERE psicologo_id=? ORDER BY dia_semana, inicio',
                        (psi_id,)).fetchall()
    conn.close()
    return rows


def replace_horarios(psi_id, janelas):
    """janelas = [(dia_semana:int, 'HH:MM', 'HH:MM'), ...]. Substitui tudo."""
    conn = get_amparo_db()
    conn.execute('DELETE FROM amparo_horarios WHERE psicologo_id=?', (psi_id,))
    for dia, ini, fim in janelas:
        if ini and fim and ini < fim:
            conn.execute('INSERT INTO amparo_horarios (psicologo_id, dia_semana, inicio, fim) VALUES (?,?,?,?)',
                         (psi_id, int(dia), ini, fim))
    conn.commit(); conn.close()


def horas_ocupadas(psi_id, data):
    """Conjunto de horas 'HH:MM' já tomadas numa data (ignora cancelados)."""
    conn = get_amparo_db()
    rows = conn.execute("SELECT hora FROM amparo_agendamentos WHERE psicologo_id=? AND data=? "
                        "AND status != 'cancelado'", (psi_id, data)).fetchall()
    conn.close()
    return {r['hora'] for r in rows}


def get_or_create_paciente(psi_id, nome, telefone, email=''):
    """Acha o paciente pelo WhatsApp (no psicólogo) ou cria. Retorna (id, consent_token, is_new)."""
    fone = norm_fone(telefone)
    conn = get_amparo_db()
    row = None
    if fone:
        row = conn.execute('SELECT id, consent_token FROM amparo_pacientes WHERE psicologo_id=? AND telefone=?',
                           (psi_id, fone)).fetchone()
    if row:
        conn.close()
        return row['id'], row['consent_token'], False
    token = novo_consent_token()
    cur = conn.execute('INSERT INTO amparo_pacientes (psicologo_id, nome, telefone, email, consent_token) '
                       'VALUES (?,?,?,?,?)', (psi_id, nome.strip(), fone, (email or '').strip(), token))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid, token, True


def criar_agendamento(psi_id, paciente_id, data, hora, duracao_min, origem='paciente'):
    conn = get_amparo_db()
    cur = conn.execute('''INSERT INTO amparo_agendamentos
                          (psicologo_id, paciente_id, data, hora, duracao_min, origem)
                          VALUES (?,?,?,?,?,?)''',
                       (psi_id, paciente_id, data, hora, int(duracao_min), origem))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


def listar_agendamentos(psi_id, desde=None):
    """Próximos agendamentos (com nome/telefone do paciente)."""
    conn = get_amparo_db()
    q = ('''SELECT ag.*, pa.nome AS paciente_nome, pa.telefone AS paciente_fone
            FROM amparo_agendamentos ag LEFT JOIN amparo_pacientes pa ON pa.id = ag.paciente_id
            WHERE ag.psicologo_id=? ''')
    params = [psi_id]
    if desde:
        q += 'AND ag.data >= ? '; params.append(desde)
    q += 'ORDER BY ag.data, ag.hora'
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


def set_status_agendamento(psi_id, ag_id, status):
    conn = get_amparo_db()
    conn.execute('UPDATE amparo_agendamentos SET status=? WHERE id=? AND psicologo_id=?',
                 (status, ag_id, psi_id))
    conn.commit(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# ASSINATURA (Lote 2) — quem paga é o psicólogo (B2B), via Asaas
# ══════════════════════════════════════════════════════════════════════════════
def set_assinatura_pendente(psi_id, cpf, asaas_customer_id, asaas_subscription_id):
    """Marca a intenção de assinar. O acesso ao plano só é liberado pelo webhook (pago)."""
    conn = get_amparo_db()
    conn.execute('''UPDATE amparo_psicologos SET cpf=?, asaas_customer_id=?,
                    asaas_subscription_id=? WHERE id=?''',
                 (cpf, asaas_customer_id, asaas_subscription_id, psi_id))
    conn.commit(); conn.close()


def atualiza_assinatura_por_customer(asaas_customer_id, plano, limite, status):
    """Chamado pelo webhook: ativa/suspende o plano do psicólogo pelo customer Asaas.
    Retorna nº de linhas afetadas (0 = não achou)."""
    conn = get_amparo_db()
    cur = conn.execute('''UPDATE amparo_psicologos SET plano=?, pacientes_limite=?, status=?
                          WHERE asaas_customer_id=?''',
                       (plano, limite, status, asaas_customer_id))
    conn.commit(); n = cur.rowcount; conn.close()
    return n


def registra_pagamento(psi_id, plano, ciclo, valor, status='pendente', asaas_payment_id=''):
    conn = get_amparo_db()
    conn.execute('''INSERT INTO amparo_pagamentos (psicologo_id, plano, ciclo, valor, status, asaas_payment_id)
                    VALUES (?,?,?,?,?,?)''', (psi_id, plano, ciclo, valor, status, asaas_payment_id))
    conn.commit(); conn.close()


def pode_ativar_paciente(psi_id, limite):
    """True se o psicólogo ainda pode ligar o motor de mais um paciente (dentro do plano)."""
    return conta_pacientes_ativos(psi_id) < (limite or 0)


# ── Cashback do paciente (reusa a lógica de crédito; usado a partir do Lote 3) ──
def get_cashback(paciente_id):
    conn = get_amparo_db()
    row = conn.execute('SELECT cashback FROM amparo_pacientes WHERE id=?', (paciente_id,)).fetchone()
    conn.close()
    return (row['cashback'] or 0.0) if row else 0.0


def add_cashback(paciente_id, valor):
    conn = get_amparo_db()
    conn.execute('UPDATE amparo_pacientes SET cashback = COALESCE(cashback,0) + ? WHERE id=?',
                 (float(valor), paciente_id))
    conn.commit(); conn.close()


def debita_cashback(paciente_id, valor):
    """Débito ATÔMICO de cashback (não fica negativo). Retorna True se debitou."""
    valor = float(valor)
    conn = get_amparo_db()
    cur = conn.execute('UPDATE amparo_pacientes SET cashback = cashback - ? '
                       'WHERE id=? AND cashback >= ?', (valor, paciente_id, valor))
    conn.commit(); ok = cur.rowcount > 0; conn.close()
    return ok


# ══════════════════════════════════════════════════════════════════════════════
# MOTOR (Lote 3) — pacientes, interações, tarefas, crise
# ══════════════════════════════════════════════════════════════════════════════
def listar_pacientes(psi_id):
    conn = get_amparo_db()
    rows = conn.execute('SELECT * FROM amparo_pacientes WHERE psicologo_id=? ORDER BY nome',
                        (psi_id,)).fetchall()
    conn.close()
    return rows


def get_paciente(psi_id, pac_id):
    conn = get_amparo_db()
    row = conn.execute('SELECT * FROM amparo_pacientes WHERE id=? AND psicologo_id=?',
                       (pac_id, psi_id)).fetchone()
    conn.close()
    return row


def get_paciente_por_fone(telefone):
    """Acha o paciente pelo WhatsApp (E.164) — usado pelo webhook de entrada."""
    fone = norm_fone(telefone)
    if not fone:
        return None
    conn = get_amparo_db()
    row = conn.execute('SELECT * FROM amparo_pacientes WHERE telefone=? ORDER BY id DESC LIMIT 1',
                       (fone,)).fetchone()
    conn.close()
    return row


def set_motor_paciente(psi_id, pac_id, ativo):
    conn = get_amparo_db()
    conn.execute('UPDATE amparo_pacientes SET motor_ativo=? WHERE id=? AND psicologo_id=?',
                 (1 if ativo else 0, pac_id, psi_id))
    conn.commit(); conn.close()


# ── Interações ─────────────────────────────────────────────────────────────────
def criar_interacao(paciente_id, psicologo_id, tipo, pergunta, escala_nome=None):
    conn = get_amparo_db()
    cur = conn.execute('''INSERT INTO amparo_interacoes
                          (paciente_id, psicologo_id, tipo, pergunta, escala_nome)
                          VALUES (?,?,?,?,?)''',
                       (paciente_id, psicologo_id, tipo, pergunta, escala_nome))
    conn.commit(); iid = cur.lastrowid; conn.close()
    return iid


def interacao_aberta(paciente_id):
    """Última interação 'enviada' aguardando resposta (p/ casar a resposta do paciente)."""
    conn = get_amparo_db()
    row = conn.execute("SELECT * FROM amparo_interacoes WHERE paciente_id=? AND status='enviada' "
                       "ORDER BY id DESC LIMIT 1", (paciente_id,)).fetchone()
    conn.close()
    return row


def registrar_resposta(interacao_id, resposta, humor=None, escala_score=None, risco=0):
    conn = get_amparo_db()
    conn.execute('''UPDATE amparo_interacoes SET status='respondida', resposta=?, humor=?,
                    escala_score=?, risco=?, respondida_at=CURRENT_TIMESTAMP WHERE id=?''',
                 (resposta, humor, escala_score, 1 if risco else 0, interacao_id))
    conn.commit(); conn.close()


def feed_interacoes(psi_id, limit=50):
    """Feed do painel: interações recentes com nome do paciente."""
    conn = get_amparo_db()
    rows = conn.execute('''SELECT i.*, p.nome AS paciente_nome FROM amparo_interacoes i
                           JOIN amparo_pacientes p ON p.id = i.paciente_id
                           WHERE i.psicologo_id=? ORDER BY i.created_at DESC LIMIT ?''',
                        (psi_id, limit)).fetchall()
    conn.close()
    return rows


def humor_serie(paciente_id, limit=30):
    """Série de humor do paciente (p/ o gráfico de evolução — Lote 4)."""
    conn = get_amparo_db()
    rows = conn.execute("SELECT humor, respondida_at FROM amparo_interacoes WHERE paciente_id=? "
                        "AND humor IS NOT NULL ORDER BY respondida_at DESC LIMIT ?",
                        (paciente_id, limit)).fetchall()
    conn.close()
    return list(reversed(rows))


def stats_adesao(paciente_id):
    """(respondidas, enviadas) — base do cashback de adesão e do painel."""
    conn = get_amparo_db()
    row = conn.execute("SELECT COUNT(*) AS env, SUM(status='respondida') AS resp "
                       "FROM amparo_interacoes WHERE paciente_id=?", (paciente_id,)).fetchone()
    conn.close()
    return (row['resp'] or 0, row['env'] or 0)


# ── Tarefas ────────────────────────────────────────────────────────────────────
def criar_tarefa(paciente_id, psicologo_id, descricao):
    conn = get_amparo_db()
    conn.execute('INSERT INTO amparo_tarefas (paciente_id, psicologo_id, descricao) VALUES (?,?,?)',
                 (paciente_id, psicologo_id, descricao.strip()))
    conn.commit(); conn.close()


def listar_tarefas(paciente_id, ativas=True):
    conn = get_amparo_db()
    q = 'SELECT * FROM amparo_tarefas WHERE paciente_id=?'
    if ativas:
        q += ' AND ativa=1'
    rows = conn.execute(q + ' ORDER BY created_at DESC', (paciente_id,)).fetchall()
    conn.close()
    return rows


# ── Crise (guard-rail CFP) ─────────────────────────────────────────────────────
def log_crise(paciente_id, psicologo_id, trecho):
    conn = get_amparo_db()
    cur = conn.execute('INSERT INTO amparo_crise_log (paciente_id, psicologo_id, trecho) VALUES (?,?,?)',
                       (paciente_id, psicologo_id, (trecho or '')[:300]))
    conn.commit(); cid = cur.lastrowid; conn.close()
    return cid


def crises_recentes(psi_id, limit=20):
    conn = get_amparo_db()
    rows = conn.execute('''SELECT c.*, p.nome AS paciente_nome, p.telefone AS paciente_fone
                           FROM amparo_crise_log c JOIN amparo_pacientes p ON p.id = c.paciente_id
                           WHERE c.psicologo_id=? ORDER BY c.created_at DESC LIMIT ?''',
                        (psi_id, limit)).fetchall()
    conn.close()
    return rows


def marcar_crise_avisada(crise_id):
    conn = get_amparo_db()
    conn.execute('UPDATE amparo_crise_log SET psicologo_avisado=1 WHERE id=?', (crise_id,))
    conn.commit(); conn.close()
