"""
atendezap_db.py — Banco de dados AtendeZap
Bot de atendimento no WhatsApp (B2B): o DONO assina, conecta o WhatsApp DELE
(instância Evolution própria, igual MandaZap/AgendaJá) e o bot responde os
CLIENTES dele — pronto-por-nicho, sem construtor de fluxo. Modelo: assinatura
(plano único, trial 7 dias).

Lote 0 (Fundação): schema (negócios / conversas / mensagens) + helpers básicos.
Reusa o molde do SomaJá (DATA_DIR + WAL + CREATE IF NOT EXISTS + migrações ALTER).
"""
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'atendezap.db')

TRIAL_DIAS = 7   # dias de teste grátis ao cadastrar


def get_atende_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_atende_db():
    conn = get_atende_db()
    conn.executescript('''
        -- ── Negócios (o DONO que assina; 1 linha por negócio) ─────────────────
        CREATE TABLE IF NOT EXISTS atende_negocios (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            slug                  TEXT UNIQUE,
            nome                  TEXT NOT NULL,            -- nome do negócio
            nicho                 TEXT DEFAULT 'outros',    -- chave do NICHOS (salao, oficina...)
            owner_name            TEXT,
            phone                 TEXT,                     -- telefone de contato/login
            email                 TEXT UNIQUE,
            cpf_cnpj              TEXT,
            password_hash         TEXT,
            -- ── Config pronta-por-nicho (os "5 campos" que o dono preenche) ──
            servicos              TEXT,                     -- texto livre: "Corte R$40 · Barba R$30"
            horario               TEXT,                     -- "Seg-Sex 9-19, Sáb 9-13"
            endereco              TEXT,                     -- com ponto de referência
            pagamentos            TEXT,                     -- "PIX, cartão, dinheiro"
            obs                   TEXT,                     -- observações livres p/ o bot
            alert_phone           TEXT,                     -- número pessoal p/ avisos de handoff (opcional)
            -- ── WhatsApp / Evolution (instância do PRÓPRIO dono) ─────────────
            evo_instance          TEXT,                     -- 'atende{id}'
            evo_ativo             INTEGER DEFAULT 0,        -- número conectado?
            bot_ativo             INTEGER DEFAULT 1,        -- liga/desliga o bot
            -- ── Assinatura (plano único: mensal/anual PIX) ───────────────────
            plano                 TEXT,
            plan_active           INTEGER DEFAULT 0,        -- 1 = assinatura paga ativa
            trial_until           TEXT,                     -- ISO date: acesso grátis até aqui
            asaas_customer_id     TEXT,
            asaas_subscription_id TEXT,
            afiliado_ref          TEXT,                     -- código do afiliado que trouxe
            reset_token           TEXT,
            reset_expires         TEXT,
            created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso         TEXT
        );

        -- ── Conversas (1 por cliente do negócio; carrega o estado bot/humano) ─
        CREATE TABLE IF NOT EXISTS atende_conversas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id      INTEGER NOT NULL,
            cliente_phone   TEXT NOT NULL,
            cliente_nome    TEXT,
            status          TEXT DEFAULT 'bot',   -- 'bot' | 'humano' (escalado/pausado)
            escalado_em     TEXT,                 -- quando o bot entregou pro humano
            escalado_motivo TEXT,                 -- por que escalou (keyword / fora-de-escopo...)
            ultima_msg_em   TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(negocio_id, cliente_phone)
        );

        -- ── Mensagens (log enxuto; expurgável por retenção/LGPD) ──────────────
        CREATE TABLE IF NOT EXISTS atende_mensagens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            conversa_id INTEGER NOT NULL,
            direcao     TEXT NOT NULL,            -- 'in' (cliente) | 'out' (bot)
            texto       TEXT,
            escalou     INTEGER DEFAULT 0,        -- esta msg disparou o handoff?
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    conn.commit()

    # ── Migrações seguras (ADD COLUMN se não existir) — p/ bancos já no ar ──────
    for migration in [
        'ALTER TABLE atende_negocios ADD COLUMN obs TEXT',
        'ALTER TABLE atende_negocios ADD COLUMN alert_phone TEXT',
        'ALTER TABLE atende_negocios ADD COLUMN bot_ativo INTEGER DEFAULT 1',
        'ALTER TABLE atende_negocios ADD COLUMN afiliado_ref TEXT',
        'ALTER TABLE atende_negocios ADD COLUMN asaas_subscription_id TEXT',
        'ALTER TABLE atende_negocios ADD COLUMN reset_token TEXT',
        'ALTER TABLE atende_negocios ADD COLUMN reset_expires TEXT',
        'ALTER TABLE atende_negocios ADD COLUMN ultimo_acesso TEXT',
        'ALTER TABLE atende_conversas ADD COLUMN escalado_motivo TEXT',
    ]:
        try:
            conn.execute(migration); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    # Índices (depois das migrações, p/ não quebrar em banco antigo)
    for idx in [
        'CREATE INDEX IF NOT EXISTS idx_atende_neg_inst  ON atende_negocios(evo_instance)',
        'CREATE INDEX IF NOT EXISTS idx_atende_neg_email ON atende_negocios(email)',
        'CREATE INDEX IF NOT EXISTS idx_atende_neg_cust  ON atende_negocios(asaas_customer_id)',
        'CREATE INDEX IF NOT EXISTS idx_atende_conv_neg  ON atende_conversas(negocio_id)',
        'CREATE INDEX IF NOT EXISTS idx_atende_msg_conv  ON atende_mensagens(conversa_id)',
    ]:
        try:
            conn.execute(idx); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    conn.close()


# ── Acesso (assinatura ativa OU trial válido) ──────────────────────────────────
def tem_acesso(n) -> bool:
    """True se o negócio pode usar o bot: assinatura paga OU trial dentro do prazo."""
    if not n:
        return False
    try:
        if n['plan_active']:
            return True
    except (KeyError, IndexError):
        pass
    try:
        tu = n['trial_until']
    except (KeyError, IndexError):
        tu = None
    return bool(tu and tu >= datetime.now().strftime('%Y-%m-%d'))


def dias_de_trial_restantes(n) -> int:
    try:
        tu = n['trial_until']
    except (KeyError, IndexError):
        tu = None
    if not tu:
        return 0
    try:
        fim = datetime.strptime(tu, '%Y-%m-%d').date()
    except ValueError:
        return 0
    return max(0, (fim - datetime.now().date()).days)


# ── Negócios ───────────────────────────────────────────────────────────────────
def _slugify(texto):
    # Dobra acentos (Salão -> salao) antes de cortar p/ não virar "sal-o"
    base = unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[^a-z0-9]+', '-', base.lower().strip()).strip('-')
    return s[:40] or 'negocio'


def _slug_unico(conn, base):
    slug = base
    i = 2
    while conn.execute('SELECT 1 FROM atende_negocios WHERE slug=?', (slug,)).fetchone():
        slug = f'{base}-{i}'
        i += 1
    return slug


def criar_negocio(nome, nicho, owner_name, phone, email, password_hash,
                  cpf_cnpj='', afiliado_ref='', servicos='', horario='',
                  endereco='', pagamentos='', trial_dias=TRIAL_DIAS):
    """Cria o negócio, define slug único e a instância Evolution 'atende{id}'.
    Retorna o id. A semente de serviços por nicho é responsabilidade da rota
    de cadastro (Lote 2); aqui aceitamos os campos já prontos."""
    conn = get_atende_db()
    slug = _slug_unico(conn, _slugify(nome))
    trial_until = (datetime.now() + timedelta(days=int(trial_dias))).strftime('%Y-%m-%d')
    cur = conn.execute(
        '''INSERT INTO atende_negocios
             (slug, nome, nicho, owner_name, phone, email, cpf_cnpj, password_hash,
              servicos, horario, endereco, pagamentos, afiliado_ref,
              trial_until, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (slug, nome, nicho or 'outros', owner_name, phone, email, cpf_cnpj,
         password_hash, servicos, horario, endereco, pagamentos,
         (afiliado_ref or None), trial_until, datetime.now().isoformat()))
    biz_id = cur.lastrowid
    conn.execute('UPDATE atende_negocios SET evo_instance=? WHERE id=?',
                 (f'atende{biz_id}', biz_id))
    conn.commit(); conn.close()
    return biz_id


def get_negocio(biz_id):
    conn = get_atende_db()
    r = conn.execute('SELECT * FROM atende_negocios WHERE id=?', (biz_id,)).fetchone()
    conn.close()
    return r


def get_negocio_por_email(email):
    conn = get_atende_db()
    r = conn.execute('SELECT * FROM atende_negocios WHERE email=?',
                     ((email or '').strip().lower(),)).fetchone()
    conn.close()
    return r


def negocio_por_instancia(instance):
    """O webhook do Evolution roteia por nome de instância → acha o negócio dono."""
    conn = get_atende_db()
    r = conn.execute('SELECT * FROM atende_negocios WHERE evo_instance=?',
                     (instance,)).fetchone()
    conn.close()
    return r


def set_bot_ativo(biz_id, ativo):
    conn = get_atende_db()
    conn.execute('UPDATE atende_negocios SET bot_ativo=? WHERE id=?',
                 (1 if ativo else 0, biz_id))
    conn.commit(); conn.close()


def set_evo_ativo(biz_id, ativo):
    conn = get_atende_db()
    conn.execute('UPDATE atende_negocios SET evo_ativo=? WHERE id=?',
                 (1 if ativo else 0, biz_id))
    conn.commit(); conn.close()


def atualizar_config(biz_id, servicos, horario, endereco, pagamentos, obs, alert_phone):
    """Salva os campos pronto-por-nicho que o dono preenche no painel (Lote 2)."""
    conn = get_atende_db()
    conn.execute('''UPDATE atende_negocios
                       SET servicos=?, horario=?, endereco=?, pagamentos=?, obs=?, alert_phone=?
                     WHERE id=?''',
                 (servicos, horario, endereco, pagamentos, obs, alert_phone, biz_id))
    conn.commit(); conn.close()


# ── Conversas / mensagens (motor de resposta — Lote 1) ──────────────────────────
def get_or_create_conversa(negocio_id, phone, nome=''):
    """1 conversa por (negócio, telefone do cliente). Cria 'bot' se não existir."""
    conn = get_atende_db()
    r = conn.execute('SELECT * FROM atende_conversas WHERE negocio_id=? AND cliente_phone=?',
                     (negocio_id, phone)).fetchone()
    if not r:
        agora = datetime.now().isoformat()
        conn.execute('''INSERT INTO atende_conversas
                          (negocio_id, cliente_phone, cliente_nome, status, ultima_msg_em, created_at)
                        VALUES (?,?,?,?,?,?)''',
                     (negocio_id, phone, (nome or '')[:60], 'bot', agora, agora))
        conn.commit()
        r = conn.execute('SELECT * FROM atende_conversas WHERE negocio_id=? AND cliente_phone=?',
                         (negocio_id, phone)).fetchone()
    conn.close()
    return r


def set_conversa_status(conversa_id, status, motivo=None):
    """status: 'bot' | 'humano'. Ao escalar grava quando/por quê; ao voltar, limpa."""
    conn = get_atende_db()
    if status == 'humano':
        conn.execute('UPDATE atende_conversas SET status=?, escalado_em=?, escalado_motivo=? WHERE id=?',
                     (status, datetime.now().isoformat(), motivo, conversa_id))
    else:
        conn.execute('UPDATE atende_conversas SET status=?, escalado_em=NULL, escalado_motivo=NULL WHERE id=?',
                     (status, conversa_id))
    conn.commit(); conn.close()


def voltar_pro_bot(conversa_id):
    """Painel (Lote 3): dono devolve a conversa pro bot."""
    set_conversa_status(conversa_id, 'bot')


def add_mensagem(conversa_id, direcao, texto, escalou=0):
    """Loga uma mensagem ('in' cliente | 'out' bot) e atualiza ultima_msg_em."""
    conn = get_atende_db()
    agora = datetime.now().isoformat()
    conn.execute('''INSERT INTO atende_mensagens (conversa_id, direcao, texto, escalou, created_at)
                    VALUES (?,?,?,?,?)''',
                 (conversa_id, direcao, (texto or '')[:2000], 1 if escalou else 0, agora))
    conn.execute('UPDATE atende_conversas SET ultima_msg_em=? WHERE id=?', (agora, conversa_id))
    conn.commit(); conn.close()


def contar_inbound_hoje(conversa_id):
    """Quantas mensagens o cliente mandou HOJE nesta conversa (cap anti-abuso/custo IA)."""
    hoje = datetime.now().strftime('%Y-%m-%d')
    conn = get_atende_db()
    n = conn.execute("SELECT COUNT(*) AS n FROM atende_mensagens "
                     "WHERE conversa_id=? AND direcao='in' AND substr(created_at,1,10)=?",
                     (conversa_id, hoje)).fetchone()['n']
    conn.close()
    return n


# ── Painel de conversas (Lote 3) ────────────────────────────────────────────────
def listar_conversas(negocio_id, limit=100):
    """Conversas do negócio (mais recentes primeiro) + prévia da última mensagem.
    As escaladas ('humano') aparecem destacadas no painel."""
    conn = get_atende_db()
    rows = conn.execute(
        '''SELECT c.*,
                  (SELECT texto FROM atende_mensagens m WHERE m.conversa_id=c.id
                    ORDER BY m.id DESC LIMIT 1) AS ultima_texto
             FROM atende_conversas c
            WHERE c.negocio_id=?
            ORDER BY (c.ultima_msg_em IS NULL), c.ultima_msg_em DESC
            LIMIT ?''', (negocio_id, int(limit))).fetchall()
    conn.close()
    return rows


def get_conversa(conversa_id, negocio_id):
    """Busca a conversa GARANTINDO que pertence ao negócio logado (segurança)."""
    conn = get_atende_db()
    r = conn.execute('SELECT * FROM atende_conversas WHERE id=? AND negocio_id=?',
                     (conversa_id, negocio_id)).fetchone()
    conn.close()
    return r


def listar_mensagens(conversa_id, limit=200):
    conn = get_atende_db()
    rows = conn.execute('SELECT * FROM atende_mensagens WHERE conversa_id=? ORDER BY id ASC LIMIT ?',
                        (conversa_id, int(limit))).fetchall()
    conn.close()
    return rows


def contar_escaladas(negocio_id):
    """Quantas conversas estão aguardando o humano (status='humano')."""
    conn = get_atende_db()
    n = conn.execute("SELECT COUNT(*) AS n FROM atende_conversas "
                     "WHERE negocio_id=? AND status='humano'", (negocio_id,)).fetchone()['n']
    conn.close()
    return n


# ── Assinatura / Asaas (Lote 4) ─────────────────────────────────────────────────
def get_negocio_por_asaas(customer_id):
    conn = get_atende_db()
    r = conn.execute('SELECT * FROM atende_negocios WHERE asaas_customer_id=?',
                     (customer_id,)).fetchone()
    conn.close()
    return r


def set_asaas_cliente(biz_id, customer_id, doc):
    conn = get_atende_db()
    conn.execute('UPDATE atende_negocios SET asaas_customer_id=?, cpf_cnpj=? WHERE id=?',
                 (customer_id, doc, biz_id))
    conn.commit(); conn.close()


def set_asaas_sub(biz_id, sub_id, plano):
    conn = get_atende_db()
    conn.execute('UPDATE atende_negocios SET asaas_subscription_id=?, plano=? WHERE id=?',
                 (sub_id, plano, biz_id))
    conn.commit(); conn.close()


def set_plano_ativo(biz_id, ativo):
    conn = get_atende_db()
    conn.execute('UPDATE atende_negocios SET plan_active=? WHERE id=?',
                 (1 if ativo else 0, biz_id))
    conn.commit(); conn.close()
