"""
mlhype_db.py — Banco de dados do MLhype
SaaS de inteligência para vendedores do Mercado Livre.

Princípio central (regra de ouro): a API do ML só dá a "foto de agora".
Toda coleta vira um SNAPSHOT datado — NUNCA sobrescreve, sempre INSERE nova
linha. O histórico acumulado dia a dia é o tesouro/MOAT do produto.

Padrão idêntico aos demais módulos do 4kitem (drzap_db, radar_db):
SQLite via DATA_DIR + WAL + migrações seguras (ADD COLUMN em try/except).
"""
import os
import sqlite3
from datetime import datetime, timedelta

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'mlhype.db')

# ── Planos e gating de features (enforce no BACKEND, nunca só na UI) ────────────
PLAN_RANK = {'free': 0, 'starter': 1, 'pro': 2, 'business': 3}

# Cada feature exige um plano MÍNIMO (rank). A esteira de 5 agentes é cortada
# pelo orquestrador no ponto que o plano permite.
FEATURE_MIN = {
    'radar_basico':   'free',      # Top parcial, 3 buscas/dia
    'radar_completo': 'starter',   # Top 5 + tendência + arbitragem geográfica
    'historico':      'pro',       # série temporal (a API não dá)
    'dissecador':     'pro',       # agente 2
    'avaliador':      'pro',       # agente 4
    'ficha':          'pro',       # agente 5 (Ficha de Ataque)
    'fornecedor':     'business',  # agente 3 — o MOAT / upsell central
    'alertas':        'business',
}

FREE_BUSCAS_DIA = int(os.environ.get('MLHYPE_FREE_BUSCAS_DIA', '3'))


def get_mlhype_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_mlhype_db():
    conn = get_mlhype_db()
    conn.executescript('''
        -- ── Usuários (B2B: vendedores do ML) ───────────────────────────────────
        CREATE TABLE IF NOT EXISTS mlhype_users (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nome              TEXT NOT NULL,
            email             TEXT NOT NULL UNIQUE,
            telefone          TEXT,
            cpf               TEXT,
            password_hash     TEXT,
            plano             TEXT DEFAULT 'free',   -- free | starter | pro | business
            plan_active       INTEGER DEFAULT 0,     -- 1 = assinatura paga em dia
            ciclo             TEXT,                  -- mensal | anual
            asaas_customer_id TEXT,
            afiliado_ref      TEXT,                  -- código do afiliado que indicou
            termo_aceito      INTEGER DEFAULT 0,
            reset_token       TEXT,
            reset_expires     TEXT,
            created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso     TEXT
        );

        -- ── Anúncio estável (entidade canônica do item rastreado) ──────────────
        CREATE TABLE IF NOT EXISTS mlhype_listings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            mlb_item_id    TEXT NOT NULL UNIQUE,     -- ex.: MLB1234567890
            titulo         TEXT,
            seller_id      TEXT,
            seller_nome    TEXT,
            categoria_id   TEXT,
            categoria_nome TEXT,
            uf             TEXT,                     -- região do vendedor (base da arbitragem)
            permalink      TEXT,
            thumbnail      TEXT,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- ── CORAÇÃO DO HISTÓRICO: 1 linha por anúncio por dia ──────────────────
        -- UNIQUE(listing_id, collected_at) garante idempotência: rodar a coleta
        -- 2x no mesmo dia NÃO duplica.
        CREATE TABLE IF NOT EXISTS mlhype_snapshots (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id       INTEGER NOT NULL,
            categoria_id     TEXT,                  -- denormalizado p/ índice (cat, data)
            collected_at     TEXT NOT NULL,         -- 'YYYY-MM-DD' (dia-calendário BRT)
            preco            REAL,
            vendidos_total   INTEGER,
            vendidos_periodo INTEGER,               -- calc vs snapshot anterior do mesmo anúncio
            visitas          INTEGER,
            num_ofertas      INTEGER,               -- nº de vendedores/ofertas competindo (concorrência)
            posicao_ranking  INTEGER,
            disponivel       INTEGER,
            frete_gratis     INTEGER,
            regiao           TEXT,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(listing_id, collected_at),
            FOREIGN KEY (listing_id) REFERENCES mlhype_listings(id)
        );

        -- ── Tendências / mais-vendidos por categoria ───────────────────────────
        CREATE TABLE IF NOT EXISTS mlhype_trends (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            categoria_id TEXT,
            termo        TEXT,
            posicao      INTEGER,
            regiao       TEXT,
            collected_at TEXT NOT NULL,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Base curada de FORNECEDORES NACIONAIS (o MOAT) ─────────────────────
        CREATE TABLE IF NOT EXISTS mlhype_suppliers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            nome           TEXT NOT NULL,
            cnpj           TEXT,
            site           TEXT,
            contato        TEXT,
            whatsapp       TEXT,
            categorias     TEXT,                    -- palavras-chave/categorias atendidas (csv)
            uf             TEXT,
            fonte          TEXT DEFAULT 'manual',   -- manual | scraping
            confiabilidade INTEGER DEFAULT 3,       -- 1-5 (curadoria humana)
            curado_por     TEXT,
            obs            TEXT,
            ativo          INTEGER DEFAULT 1,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Preços de fornecedor (versionado por data) ─────────────────────────
        CREATE TABLE IF NOT EXISTS mlhype_supplier_prices (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id        INTEGER NOT NULL,
            produto_keyword    TEXT,
            preco_unitario     REAL,
            qtd_minima         INTEGER,
            prazo_entrega_dias INTEGER,
            garantia           TEXT,
            collected_at       TEXT NOT NULL,
            FOREIGN KEY (supplier_id) REFERENCES mlhype_suppliers(id)
        );

        -- ── Oportunidades avaliadas (resultado da esteira) ─────────────────────
        CREATE TABLE IF NOT EXISTS mlhype_opportunities (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            produto      TEXT,
            categoria_id TEXT,
            score        INTEGER,
            margem_pct   REAL,
            veredito     TEXT,                      -- ataque | observe | evite
            payload_json TEXT,                      -- resultado completo da esteira
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES mlhype_users(id)
        );

        -- ── Ficha de Ataque gerada (saída final do produto) ────────────────────
        CREATE TABLE IF NOT EXISTS mlhype_attack_sheets (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id      INTEGER NOT NULL,
            titulo_otimizado    TEXT,
            bullets_json        TEXT,
            preco_sugerido      REAL,
            margem_pct          REAL,
            diferencial_ataque  TEXT,
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (opportunity_id) REFERENCES mlhype_opportunities(id)
        );

        -- ── Log de uso (impõe limite de plano + mede custo real da IA) ─────────
        CREATE TABLE IF NOT EXISTS mlhype_uso_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            tipo        TEXT,                        -- busca | dissecador | avaliador | ficha | fornecedor
            categoria   TEXT,
            tokens_in   INTEGER DEFAULT 0,
            tokens_out  INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES mlhype_users(id)
        );

        -- ── Tokens OAuth do ML (nível PLATAFORMA — 1 linha) ────────────────────
        -- access/refresh devem ser CRIPTOGRAFADOS em repouso (Passo 2).
        CREATE TABLE IF NOT EXISTS mlhype_ml_tokens (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            escopo        TEXT DEFAULT 'ml_app',
            access_token  TEXT,
            refresh_token TEXT,
            ml_user_id    TEXT,
            expires_at    TEXT,
            updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Índices ────────────────────────────────────────────────────────────
        CREATE INDEX IF NOT EXISTS idx_mlhype_snap_listing  ON mlhype_snapshots(listing_id, collected_at);
        CREATE INDEX IF NOT EXISTS idx_mlhype_snap_cat      ON mlhype_snapshots(categoria_id, collected_at);
        CREATE INDEX IF NOT EXISTS idx_mlhype_trends_cat    ON mlhype_trends(categoria_id, collected_at);
        CREATE INDEX IF NOT EXISTS idx_mlhype_listings_cat  ON mlhype_listings(categoria_id);
        CREATE INDEX IF NOT EXISTS idx_mlhype_supplier_kw   ON mlhype_supplier_prices(produto_keyword);
        CREATE INDEX IF NOT EXISTS idx_mlhype_supplier_sup  ON mlhype_supplier_prices(supplier_id);
        CREATE INDEX IF NOT EXISTS idx_mlhype_uso_user      ON mlhype_uso_log(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_mlhype_opp_user      ON mlhype_opportunities(user_id, created_at);
    ''')
    conn.commit()

    # ── Migrações seguras (ADD COLUMN se a tabela já existia sem a coluna) ──────
    for migration in [
        "ALTER TABLE mlhype_users ADD COLUMN plano TEXT DEFAULT 'free'",
        'ALTER TABLE mlhype_users ADD COLUMN plan_active INTEGER DEFAULT 0',
        'ALTER TABLE mlhype_users ADD COLUMN ciclo TEXT',
        'ALTER TABLE mlhype_users ADD COLUMN asaas_customer_id TEXT',
        'ALTER TABLE mlhype_users ADD COLUMN afiliado_ref TEXT',
        'ALTER TABLE mlhype_users ADD COLUMN termo_aceito INTEGER DEFAULT 0',
        'ALTER TABLE mlhype_snapshots ADD COLUMN categoria_id TEXT',
        'ALTER TABLE mlhype_snapshots ADD COLUMN visitas INTEGER',
        'ALTER TABLE mlhype_snapshots ADD COLUMN num_ofertas INTEGER',
        'ALTER TABLE mlhype_suppliers ADD COLUMN whatsapp TEXT',
        'ALTER TABLE mlhype_suppliers ADD COLUMN ativo INTEGER DEFAULT 1',
    ]:
        try:
            conn.execute(migration); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    conn.close()


# ── Helpers de plano / gating (usados no backend, não na UI) ───────────────────
def plano_efetivo(user_row):
    """Plano que vale de fato: se a assinatura não está paga (plan_active=0),
    o cliente cai para 'free'. Aceita sqlite3.Row, dict ou string do plano."""
    if user_row is None:
        return 'free'
    if isinstance(user_row, str):
        return user_row if user_row in PLAN_RANK else 'free'
    plano  = (user_row['plano'] if 'plano' in user_row.keys() else 'free') or 'free'
    ativo  = (user_row['plan_active'] if 'plan_active' in user_row.keys() else 0) or 0
    if plano == 'free':
        return 'free'
    return plano if ativo else 'free'


def plano_libera(plano, feature):
    """True se o `plano` (string) tem acesso à `feature`."""
    need = FEATURE_MIN.get(feature)
    if need is None:
        return False
    return PLAN_RANK.get(plano, 0) >= PLAN_RANK.get(need, 99)


# ── Limite de buscas do plano Free (3/dia, dia-calendário BRT/UTC-3) ───────────
def _inicio_dia_brt_utc():
    """Retorna o timestamp UTC ('YYYY-MM-DD HH:MM:SS') do início do dia em BRT.
    created_at é gravado em UTC (CURRENT_TIMESTAMP), então comparamos em UTC."""
    agora_brt = datetime.utcnow() - timedelta(hours=3)
    inicio_brt = agora_brt.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_utc = inicio_brt + timedelta(hours=3)
    return inicio_utc.strftime('%Y-%m-%d %H:%M:%S')


def contar_buscas_hoje(user_id):
    conn = get_mlhype_db()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM mlhype_uso_log "
        "WHERE user_id=? AND tipo='busca' AND created_at >= ?",
        (user_id, _inicio_dia_brt_utc())).fetchone()
    conn.close()
    return row['n'] if row else 0


def registrar_uso(user_id, tipo, categoria=None, tokens_in=0, tokens_out=0):
    conn = get_mlhype_db()
    conn.execute(
        'INSERT INTO mlhype_uso_log (user_id, tipo, categoria, tokens_in, tokens_out) '
        'VALUES (?,?,?,?,?)',
        (user_id, tipo, categoria, int(tokens_in or 0), int(tokens_out or 0)))
    conn.commit()
    conn.close()


# ── Coleta: anúncio + snapshot idempotente (usado pelo coletor do Passo 3) ─────
def upsert_listing(mlb_item_id, **campos):
    """Garante o anúncio canônico e devolve seu id interno. Atualiza metadados."""
    conn = get_mlhype_db()
    row = conn.execute('SELECT id FROM mlhype_listings WHERE mlb_item_id=?',
                       (mlb_item_id,)).fetchone()
    if row:
        lid = row['id']
        if campos:
            cols = ', '.join(f'{k}=?' for k in campos)
            conn.execute(f'UPDATE mlhype_listings SET {cols} WHERE id=?',
                         (*campos.values(), lid))
    else:
        cols = ', '.join(['mlb_item_id', *campos.keys()])
        ph   = ', '.join(['?'] * (1 + len(campos)))
        cur  = conn.execute(f'INSERT INTO mlhype_listings ({cols}) VALUES ({ph})',
                            (mlb_item_id, *campos.values()))
        lid = cur.lastrowid
    conn.commit()
    conn.close()
    return lid


def gravar_snapshot(listing_id, collected_at, **dados):
    """Grava (ou atualiza) o snapshot do dia de forma IDEMPOTENTE.
    Calcula vendidos_periodo vs. o snapshot anterior do mesmo anúncio.
    Rodar 2x no mesmo dia NÃO cria linha duplicada (UNIQUE listing_id+collected_at)."""
    conn = get_mlhype_db()

    # vendidos_periodo = vendidos_total de hoje - vendidos_total do último snapshot anterior
    vp = None
    vt = dados.get('vendidos_total')
    if vt is not None:
        prev = conn.execute(
            'SELECT vendidos_total FROM mlhype_snapshots '
            'WHERE listing_id=? AND collected_at < ? '
            'ORDER BY collected_at DESC LIMIT 1',
            (listing_id, collected_at)).fetchone()
        if prev and prev['vendidos_total'] is not None:
            vp = max(0, int(vt) - int(prev['vendidos_total']))

    campos = {
        'categoria_id':     dados.get('categoria_id'),
        'preco':            dados.get('preco'),
        'vendidos_total':   vt,
        'vendidos_periodo': vp,
        'visitas':          dados.get('visitas'),
        'num_ofertas':      dados.get('num_ofertas'),
        'posicao_ranking':  dados.get('posicao_ranking'),
        'disponivel':       dados.get('disponivel'),
        'frete_gratis':     dados.get('frete_gratis'),
        'regiao':           dados.get('regiao'),
    }
    cols = ', '.join(['listing_id', 'collected_at', *campos.keys()])
    ph   = ', '.join(['?'] * (2 + len(campos)))
    update = ', '.join(f'{k}=excluded.{k}' for k in campos)
    conn.execute(
        f'INSERT INTO mlhype_snapshots ({cols}) VALUES ({ph}) '
        f'ON CONFLICT(listing_id, collected_at) DO UPDATE SET {update}',
        (listing_id, collected_at, *campos.values()))
    conn.commit()
    conn.close()
    return vp


def estatisticas():
    """Resumo p/ painel admin (quanto histórico já acumulamos)."""
    conn = get_mlhype_db()
    def _c(q, *a):
        r = conn.execute(q, a).fetchone()
        return (r[0] if r else 0) or 0
    st = {
        'usuarios':   _c('SELECT COUNT(*) FROM mlhype_users'),
        'listings':   _c('SELECT COUNT(*) FROM mlhype_listings'),
        'snapshots':  _c('SELECT COUNT(*) FROM mlhype_snapshots'),
        'dias_hist':  _c('SELECT COUNT(DISTINCT collected_at) FROM mlhype_snapshots'),
        'fornecedor': _c('SELECT COUNT(*) FROM mlhype_suppliers WHERE ativo=1'),
        'oport':      _c('SELECT COUNT(*) FROM mlhype_opportunities'),
    }
    conn.close()
    return st


# ── Leitura para o painel do Radar (Passo 4) ───────────────────────────────────
def radar_categorias_com_dados():
    """Categorias que já têm snapshot coletado (p/ montar o seletor do Radar)."""
    conn = get_mlhype_db()
    rows = conn.execute(
        "SELECT categoria_id, COUNT(DISTINCT listing_id) AS n, MAX(collected_at) AS ult "
        "FROM mlhype_snapshots WHERE categoria_id IS NOT NULL "
        "GROUP BY categoria_id ORDER BY n DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def radar_top(cat_id, limit=20):
    """Top da categoria no dia mais recente coletado, com a posição/preço do dia
    ANTERIOR (p/ calcular tendência ▲▼ quando houver histórico)."""
    conn = get_mlhype_db()
    ult = conn.execute("SELECT MAX(collected_at) AS d FROM mlhype_snapshots WHERE categoria_id=?",
                       (cat_id,)).fetchone()
    if not ult or not ult['d']:
        conn.close()
        return [], None
    data = ult['d']
    rows = conn.execute("""
        SELECT l.titulo, l.mlb_item_id, s.posicao_ranking AS pos, s.preco, s.num_ofertas,
          (SELECT p.posicao_ranking FROM mlhype_snapshots p
             WHERE p.listing_id=s.listing_id AND p.collected_at < s.collected_at
             ORDER BY p.collected_at DESC LIMIT 1) AS pos_ant,
          (SELECT p.preco FROM mlhype_snapshots p
             WHERE p.listing_id=s.listing_id AND p.collected_at < s.collected_at
             ORDER BY p.collected_at DESC LIMIT 1) AS preco_ant
        FROM mlhype_snapshots s JOIN mlhype_listings l ON l.id = s.listing_id
        WHERE s.categoria_id=? AND s.collected_at=?
        ORDER BY s.posicao_ranking LIMIT ?""", (cat_id, data, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows], data


def radar_trends(limit=30):
    """Termos em alta do dia mais recente."""
    conn = get_mlhype_db()
    ult = conn.execute("SELECT MAX(collected_at) AS d FROM mlhype_trends "
                       "WHERE categoria_id IS NULL").fetchone()
    if not ult or not ult['d']:
        conn.close()
        return []
    rows = conn.execute("SELECT termo, posicao FROM mlhype_trends "
                        "WHERE categoria_id IS NULL AND collected_at=? "
                        "ORDER BY posicao LIMIT ?", (ult['d'], limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Tendência de um produto pelo histórico (sinal do Avaliador) ────────────────
def tendencia_produto(mlb_item_id):
    """Compara a posição mais recente com a mais antiga registrada: a onda está
    subindo, estável ou caindo? 'desconhecida' se só houver 1 dia de histórico."""
    conn = get_mlhype_db()
    rows = conn.execute("""
        SELECT s.posicao_ranking AS pos FROM mlhype_snapshots s
        JOIN mlhype_listings l ON l.id = s.listing_id
        WHERE l.mlb_item_id=? AND s.posicao_ranking IS NOT NULL
        ORDER BY s.collected_at""", (mlb_item_id,)).fetchall()
    conn.close()
    if len(rows) < 2:
        return 'desconhecida'
    primeiro, ultimo = rows[0]['pos'], rows[-1]['pos']
    if ultimo < primeiro - 1:      # posição menor = mais perto do topo = subindo
        return 'subindo'
    if ultimo > primeiro + 1:
        return 'caindo'
    return 'estavel'


# ── Base curada de FORNECEDORES NACIONAIS — o MOAT (Passo 8) ───────────────────
def add_fornecedor(nome, categorias='', contato='', whatsapp='', uf='', site='',
                   cnpj='', confiabilidade=3, obs='', fonte='manual', curado_por='admin'):
    conn = get_mlhype_db()
    cur = conn.execute("""INSERT INTO mlhype_suppliers
        (nome, cnpj, site, contato, whatsapp, categorias, uf, fonte, confiabilidade, curado_por, obs, ativo)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
        (nome, cnpj, site, contato, whatsapp, categorias, uf, fonte, confiabilidade, curado_por, obs))
    sid = cur.lastrowid
    conn.commit(); conn.close()
    return sid


def add_fornecedor_preco(supplier_id, produto_keyword, preco_unitario,
                         qtd_minima=1, prazo_entrega_dias=None, garantia=''):
    from datetime import datetime
    conn = get_mlhype_db()
    conn.execute("""INSERT INTO mlhype_supplier_prices
        (supplier_id, produto_keyword, preco_unitario, qtd_minima, prazo_entrega_dias, garantia, collected_at)
        VALUES (?,?,?,?,?,?,?)""",
        (supplier_id, produto_keyword, preco_unitario, qtd_minima, prazo_entrega_dias,
         garantia, datetime.utcnow().strftime('%Y-%m-%d')))
    conn.commit(); conn.close()


def listar_fornecedores():
    conn = get_mlhype_db()
    rows = conn.execute("""
        SELECT f.*, (SELECT MIN(preco_unitario) FROM mlhype_supplier_prices p
                     WHERE p.supplier_id=f.id) AS menor_preco
        FROM mlhype_suppliers f ORDER BY f.ativo DESC, f.created_at DESC""").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def match_fornecedores(categoria_id, categoria_nome='', titulo='', limit=5):
    """Caçador de Fornecedor BR: acha fornecedores que atendem a categoria/produto
    e o MENOR preço de custo deles. Match fuzzy por categoria_id, nome da categoria
    ou palavras do título contra o campo `categorias` (csv) do fornecedor."""
    termos = [categoria_id, categoria_nome] + [w for w in (titulo or '').split() if len(w) > 3][:4]
    termos = [t.lower() for t in termos if t]
    if not termos:
        return []
    conn = get_mlhype_db()
    where = ' OR '.join(["LOWER(categorias) LIKE ?"] * len(termos))
    params = [f'%{t}%' for t in termos]
    rows = conn.execute(f"""
        SELECT f.id, f.nome, f.contato, f.whatsapp, f.uf, f.confiabilidade, f.categorias,
               (SELECT MIN(preco_unitario) FROM mlhype_supplier_prices p WHERE p.supplier_id=f.id) AS menor_preco,
               (SELECT prazo_entrega_dias FROM mlhype_supplier_prices p WHERE p.supplier_id=f.id
                ORDER BY preco_unitario LIMIT 1) AS prazo
        FROM mlhype_suppliers f
        WHERE f.ativo=1 AND ({where})
        ORDER BY f.confiabilidade DESC, menor_preco ASC LIMIT ?""",
        (*params, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Persistência da esteira (oportunidade + Ficha de Ataque) ───────────────────
def salvar_oportunidade(produto, categoria_id, score, margem_pct, veredito, payload, user_id=None):
    import json as _json
    conn = get_mlhype_db()
    cur = conn.execute("""INSERT INTO mlhype_opportunities
        (user_id, produto, categoria_id, score, margem_pct, veredito, payload_json)
        VALUES (?,?,?,?,?,?,?)""",
        (user_id, produto, categoria_id, score, margem_pct, veredito,
         _json.dumps(payload, ensure_ascii=False)))
    oid = cur.lastrowid
    conn.commit(); conn.close()
    return oid


def salvar_ficha(opportunity_id, ficha):
    import json as _json
    conn = get_mlhype_db()
    conn.execute("""INSERT INTO mlhype_attack_sheets
        (opportunity_id, titulo_otimizado, bullets_json, preco_sugerido, margem_pct, diferencial_ataque)
        VALUES (?,?,?,?,?,?)""",
        (opportunity_id, ficha.get('titulo_otimizado'),
         _json.dumps(ficha.get('bullets', []), ensure_ascii=False),
         ficha.get('preco_venda_sugerido'), ficha.get('margem_pct'),
         ficha.get('diferencial_ataque')))
    conn.commit(); conn.close()


# ── Usuários / autenticação (Passo 5 — auth; billing fica p/ depois) ───────────
def criar_usuario(nome, email, telefone, senha_hash):
    conn = get_mlhype_db()
    cur = conn.execute(
        "INSERT INTO mlhype_users (nome, email, telefone, password_hash, plano, created_at) "
        "VALUES (?,?,?,?, 'free', CURRENT_TIMESTAMP)", (nome, email, telefone, senha_hash))
    uid = cur.lastrowid
    conn.commit(); conn.close()
    return uid


def usuario_por_email(email):
    conn = get_mlhype_db()
    r = conn.execute('SELECT * FROM mlhype_users WHERE email=?', (email,)).fetchone()
    conn.close()
    return dict(r) if r else None


def usuario_por_id(uid):
    conn = get_mlhype_db()
    r = conn.execute('SELECT * FROM mlhype_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return dict(r) if r else None


def marcar_acesso(uid):
    from datetime import datetime
    conn = get_mlhype_db()
    conn.execute('UPDATE mlhype_users SET ultimo_acesso=? WHERE id=?',
                 (datetime.utcnow().isoformat(), uid))
    conn.commit(); conn.close()


# ── PENTE FINO: Índice de Oportunidade (o motor que sabe o que vale a pena) ────
# Heurística determinística (sem IA, roda sobre TODO o mercado coletado):
# o ouro = ALTA demanda (posição no Top) × BAIXA concorrência (poucas ofertas),
# com bônus pra tendência subindo e pra ter fornecedor (margem garantida).
def _demanda_score(pos):
    """Posição no Top → demanda. pos 1 = 100; cai ~4 pts por posição."""
    return max(5, 100 - (int(pos) - 1) * 4) if pos else 30


def _facilidade_score(num_ofertas):
    """Menos concorrentes = mais fácil atacar. 1 oferta≈87, 10≈40, 100≈6."""
    if not num_ofertas:
        return 55
    return max(3, round(100 / (1 + int(num_ofertas) * 0.15)))


def oportunidades(limit=20, categoria=None):
    """Varre o último snapshot de cada produto, pontua a oportunidade e devolve
    as melhores rankeadas. base = média geométrica(demanda, facilidade) — exige
    as DUAS coisas decentes (vende E dá pra atacar); + bônus tendência/fornecedor."""
    conn = get_mlhype_db()
    q = '''SELECT l.id AS lid, l.mlb_item_id, l.titulo, l.categoria_id,
                  s.posicao_ranking AS pos, s.num_ofertas, s.preco
           FROM mlhype_listings l
           JOIN mlhype_snapshots s ON s.listing_id = l.id
           WHERE s.collected_at = (SELECT MAX(collected_at) FROM mlhype_snapshots
                                   WHERE listing_id = l.id)
             AND s.posicao_ranking IS NOT NULL'''
    params = []
    if categoria:
        q += ' AND l.categoria_id = ?'
        params.append(categoria)
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()

    for r in rows:
        d = _demanda_score(r['pos'])
        f = _facilidade_score(r['num_ofertas'])
        r['base'] = round((d * f) ** 0.5)            # média geométrica
    rows.sort(key=lambda x: x['base'], reverse=True)

    # Trabalho mais caro (tendência + fornecedor) só nos finalistas
    cand = rows[:max(limit * 2, 40)]
    for r in cand:
        tend = tendencia_produto(r['mlb_item_id'])
        bonus = 10 if tend == 'subindo' else (-8 if tend == 'caindo' else 0)
        tem_forn = bool(match_fornecedores(r['categoria_id'] or '', '', r['titulo'] or '', limit=1))
        if tem_forn:
            bonus += 8
        r['tendencia'] = tend
        r['tem_fornecedor'] = tem_forn
        r['brecha'] = (r['num_ofertas'] is not None and r['num_ofertas'] <= 3)
        r['score'] = max(0, min(100, r['base'] + bonus))
    cand.sort(key=lambda x: x['score'], reverse=True)
    return cand[:limit]
