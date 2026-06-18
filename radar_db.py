"""
radar_db.py — Banco do Radar de Licitações de TI (módulo do 4kitem)

Coleta licitações do PNCP (API pública) e guarda só o essencial + a classificação
(é TI? que porte? score). Foco: serviços médio/pequenos de TI/software que o
Diogo + IA conseguem entregar (cauda longa municipal) — nada "Microsoft".

Camadas de filtro (ver radar.py):
  Eixo 1 — É TI?      (palavra-chave do objeto)
  Eixo 2 — É do porte? (faixa de valor: ouro <=65k / boa / dificil / nao)
"""
import os
import json
import sqlite3

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'radar.db')


def get_radar_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_radar_db():
    conn = get_radar_db()
    conn.executescript('''
        -- ── Licitações coletadas do PNCP ─────────────────────────────────────
        CREATE TABLE IF NOT EXISTS radar_licitacoes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            pncp_id           TEXT UNIQUE,            -- numeroControlePNCP (chave natural)
            objeto            TEXT,
            valor             REAL,
            modalidade        TEXT,
            modalidade_id     INTEGER,
            situacao          TEXT,
            orgao             TEXT,
            orgao_cnpj        TEXT,
            uf                TEXT,
            municipio         TEXT,
            codigo_ibge       TEXT,
            data_publicacao   TEXT,
            data_abertura     TEXT,
            data_encerramento TEXT,                   -- fim do recebimento de propostas
            link              TEXT,
            -- ── classificação (Lote 1 já grava básico; Lote 2 refina) ──
            is_ti             INTEGER DEFAULT 0,       -- 1 = passou no Eixo 1 (é TI)
            tier              INTEGER,                 -- 1/2/3 (produto pronto -> sob encomenda)
            zona_valor        TEXT,                    -- ouro | boa | dificil | nao
            score             INTEGER DEFAULT 0,       -- viabilidade 0..100
            keywords_match    TEXT,                    -- quais termos casaram (debug)
            -- ── Análise por IA (Lote 4) ──
            analise_json      TEXT,                    -- resposta estruturada da IA (JSON)
            analise_veredito  TEXT,                    -- veredito em 1 linha
            analise_viavel    TEXT,                    -- sim | talvez | nao
            analise_engine    TEXT,                    -- gemini | groq
            analisado_em      TEXT,
            coletado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
            raw_json          TEXT                     -- bruto p/ reprocessar sem nova chamada
        );

        -- ── Log das rodadas do coletor (ops/diagnóstico) ─────────────────────
        CREATE TABLE IF NOT EXISTS radar_coleta_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            iniciado_em   TEXT DEFAULT CURRENT_TIMESTAMP,
            terminado_em  TEXT,
            uf            TEXT,
            modalidades   TEXT,
            paginas_lidas INTEGER DEFAULT 0,
            novos         INTEGER DEFAULT 0,
            atualizados   INTEGER DEFAULT 0,
            erros         INTEGER DEFAULT 0,
            obs           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_radar_uf     ON radar_licitacoes(uf);
        CREATE INDEX IF NOT EXISTS idx_radar_ti     ON radar_licitacoes(is_ti);
        CREATE INDEX IF NOT EXISTS idx_radar_valor  ON radar_licitacoes(valor);
        CREATE INDEX IF NOT EXISTS idx_radar_zona   ON radar_licitacoes(zona_valor);
        CREATE INDEX IF NOT EXISTS idx_radar_enc    ON radar_licitacoes(data_encerramento);

        -- ── Inteligência de preço: contratos já assinados (Lote 5) ───────────
        CREATE TABLE IF NOT EXISTS radar_contratos (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            pncp_id          TEXT UNIQUE,
            objeto           TEXT,
            valor            REAL,
            fornecedor       TEXT,            -- quem ganhou (razão social)
            fornecedor_doc   TEXT,            -- CNPJ/CPF do fornecedor
            orgao            TEXT,
            uf               TEXT,
            municipio        TEXT,
            modalidade       TEXT,
            data_assinatura  TEXT,
            vigencia_inicio  TEXT,
            vigencia_fim     TEXT,            -- ⭐ quando o contrato VENCE (radar de renovação)
            link             TEXT,
            is_ti            INTEGER DEFAULT 0,
            keywords_match   TEXT,
            coletado_em      TEXT DEFAULT CURRENT_TIMESTAMP,
            raw_json         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_rc_ti    ON radar_contratos(is_ti);
        CREATE INDEX IF NOT EXISTS idx_rc_uf    ON radar_contratos(uf);
        CREATE INDEX IF NOT EXISTS idx_rc_fim   ON radar_contratos(vigencia_fim);
        CREATE INDEX IF NOT EXISTS idx_rc_forn  ON radar_contratos(fornecedor_doc);

        -- ── Usuários do Radar (SaaS: login/senha/reset) ──────────────────────
        CREATE TABLE IF NOT EXISTS radar_users (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nome              TEXT NOT NULL,
            email             TEXT NOT NULL UNIQUE,
            telefone          TEXT DEFAULT '',
            password_hash     TEXT NOT NULL,
            plano             TEXT DEFAULT 'free',
            plan_active       INTEGER DEFAULT 0,
            is_admin          INTEGER DEFAULT 0,
            reset_token       TEXT DEFAULT '',
            reset_expires     TEXT DEFAULT '',
            asaas_customer_id TEXT DEFAULT '',
            created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ru_email ON radar_users(email);

        -- ── Usuários do Radar Licita Norte (módulo regional, marca própria) ──
        CREATE TABLE IF NOT EXISTS licita_users (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nome              TEXT NOT NULL,
            email             TEXT NOT NULL UNIQUE,
            telefone          TEXT DEFAULT '',
            password_hash     TEXT NOT NULL,
            plano             TEXT DEFAULT 'free',
            plan_active       INTEGER DEFAULT 0,
            is_admin          INTEGER DEFAULT 0,
            reset_token       TEXT DEFAULT '',
            reset_expires     TEXT DEFAULT '',
            asaas_customer_id TEXT DEFAULT '',
            created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            ultimo_acesso     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_lu_email ON licita_users(email);
    ''')
    conn.commit()

    # ── Migrações seguras (ADD COLUMN se rodar versão antiga do schema) ───────
    for migration in [
        'ALTER TABLE radar_licitacoes ADD COLUMN keywords_match TEXT',
        'ALTER TABLE radar_licitacoes ADD COLUMN tier INTEGER',
        'ALTER TABLE radar_licitacoes ADD COLUMN zona_valor TEXT',
        'ALTER TABLE radar_licitacoes ADD COLUMN analise_json TEXT',
        'ALTER TABLE radar_licitacoes ADD COLUMN analise_veredito TEXT',
        'ALTER TABLE radar_licitacoes ADD COLUMN analise_viavel TEXT',
        'ALTER TABLE radar_licitacoes ADD COLUMN analise_engine TEXT',
        'ALTER TABLE radar_licitacoes ADD COLUMN analisado_em TEXT',
    ]:
        try:
            conn.execute(migration); conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

    # admin é sempre PRO/ativo (dono não paga; futura paywall ignora admin)
    for _t in ('radar_users', 'licita_users'):
        try:
            conn.execute(f'UPDATE {_t} SET plan_active=1 WHERE is_admin=1 AND plan_active=0')
            conn.commit()
        except Exception:
            pass

    conn.close()


# ── Upsert por pncp_id (idempotente: coletar de novo não duplica) ────────────
_CAMPOS = ('pncp_id', 'objeto', 'valor', 'modalidade', 'modalidade_id', 'situacao',
           'orgao', 'orgao_cnpj', 'uf', 'municipio', 'codigo_ibge', 'data_publicacao',
           'data_abertura', 'data_encerramento', 'link', 'is_ti', 'tier', 'zona_valor',
           'score', 'keywords_match', 'raw_json')


def upsert_licitacao(d: dict) -> str:
    """Insere ou atualiza por pncp_id. Retorna 'novo' | 'atualizado' | 'erro'."""
    conn = get_radar_db()
    try:
        existe = conn.execute('SELECT id FROM radar_licitacoes WHERE pncp_id=?',
                              (d.get('pncp_id'),)).fetchone()
        vals = {k: d.get(k) for k in _CAMPOS}
        if isinstance(vals.get('raw_json'), (dict, list)):
            vals['raw_json'] = json.dumps(vals['raw_json'], ensure_ascii=False)
        if existe:
            sets = ', '.join(f'{k}=?' for k in _CAMPOS if k != 'pncp_id')
            conn.execute(f'UPDATE radar_licitacoes SET {sets} WHERE pncp_id=?',
                         [vals[k] for k in _CAMPOS if k != 'pncp_id'] + [vals['pncp_id']])
            conn.commit(); return 'atualizado'
        cols = ', '.join(_CAMPOS)
        ph   = ', '.join('?' for _ in _CAMPOS)
        conn.execute(f'INSERT INTO radar_licitacoes ({cols}) VALUES ({ph})',
                     [vals[k] for k in _CAMPOS])
        conn.commit(); return 'novo'
    except Exception:
        try: conn.rollback()
        except Exception: pass
        return 'erro'
    finally:
        conn.close()


def listar_licitacoes(uf=None, somente_ti=True, zona=None, valor_max=None,
                      limite=200, ordem='score'):
    """Lista licitações para o painel, com filtros."""
    conn = get_radar_db()
    where, params = [], []
    if somente_ti:
        where.append('is_ti=1')
    if uf:
        where.append('uf=?'); params.append(uf.upper())
    if zona:
        where.append('zona_valor=?'); params.append(zona)
    if valor_max:
        where.append('(valor IS NULL OR valor<=?)'); params.append(valor_max)
    sql = 'SELECT * FROM radar_licitacoes'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    ordem_sql = {'score': 'score DESC, valor ASC',
                 'valor': 'valor ASC',
                 'novo':  'coletado_em DESC',
                 'prazo': 'data_encerramento ASC'}.get(ordem, 'score DESC')
    sql += f' ORDER BY {ordem_sql} LIMIT ?'
    params.append(limite)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obter_licitacao(pncp_id):
    conn = get_radar_db()
    row = conn.execute('SELECT * FROM radar_licitacoes WHERE pncp_id=?', (pncp_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def salvar_analise(pncp_id, analise: dict, engine: str):
    """Grava a análise da IA (Lote 4) na licitação."""
    conn = get_radar_db()
    try:
        conn.execute('''UPDATE radar_licitacoes SET
            analise_json=?, analise_veredito=?, analise_viavel=?, analise_engine=?,
            analisado_em=CURRENT_TIMESTAMP WHERE pncp_id=?''',
            (json.dumps(analise, ensure_ascii=False),
             (analise.get('veredito') or '')[:300],
             analise.get('viavel'), engine, pncp_id))
        conn.commit()
    finally:
        conn.close()


def estatisticas():
    conn = get_radar_db()
    s = {}
    s['total']    = conn.execute('SELECT COUNT(*) FROM radar_licitacoes').fetchone()[0]
    s['ti']       = conn.execute('SELECT COUNT(*) FROM radar_licitacoes WHERE is_ti=1').fetchone()[0]
    s['ouro']     = conn.execute("SELECT COUNT(*) FROM radar_licitacoes WHERE is_ti=1 AND zona_valor='ouro'").fetchone()[0]
    s['por_uf']   = [dict(r) for r in conn.execute(
        "SELECT uf, COUNT(*) n FROM radar_licitacoes WHERE is_ti=1 GROUP BY uf ORDER BY n DESC LIMIT 15").fetchall()]
    s['ultima_coleta'] = conn.execute(
        'SELECT terminado_em, novos, atualizados FROM radar_coleta_log ORDER BY id DESC LIMIT 1').fetchone()
    s['ultima_coleta'] = dict(s['ultima_coleta']) if s['ultima_coleta'] else None
    conn.close()
    return s


def registrar_coleta(uf, modalidades, paginas, novos, atualizados, erros, obs=''):
    conn = get_radar_db()
    conn.execute('''INSERT INTO radar_coleta_log
        (terminado_em, uf, modalidades, paginas_lidas, novos, atualizados, erros, obs)
        VALUES (CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)''',
        (uf or 'BR', str(modalidades), paginas, novos, atualizados, erros, obs))
    conn.commit(); conn.close()


# ── Inteligência de preço — contratos (Lote 5) ───────────────────────────────
_CAMPOS_C = ('pncp_id', 'objeto', 'valor', 'fornecedor', 'fornecedor_doc', 'orgao',
             'uf', 'municipio', 'modalidade', 'data_assinatura', 'vigencia_inicio',
             'vigencia_fim', 'link', 'is_ti', 'keywords_match', 'raw_json')


def upsert_contrato(d: dict) -> str:
    conn = get_radar_db()
    try:
        vals = {k: d.get(k) for k in _CAMPOS_C}
        if isinstance(vals.get('raw_json'), (dict, list)):
            vals['raw_json'] = json.dumps(vals['raw_json'], ensure_ascii=False)
        existe = conn.execute('SELECT id FROM radar_contratos WHERE pncp_id=?',
                              (vals['pncp_id'],)).fetchone()
        if existe:
            sets = ', '.join(f'{k}=?' for k in _CAMPOS_C if k != 'pncp_id')
            conn.execute(f'UPDATE radar_contratos SET {sets} WHERE pncp_id=?',
                         [vals[k] for k in _CAMPOS_C if k != 'pncp_id'] + [vals['pncp_id']])
            conn.commit(); return 'atualizado'
        cols = ', '.join(_CAMPOS_C); ph = ', '.join('?' for _ in _CAMPOS_C)
        conn.execute(f'INSERT INTO radar_contratos ({cols}) VALUES ({ph})',
                     [vals[k] for k in _CAMPOS_C])
        conn.commit(); return 'novo'
    except Exception:
        try: conn.rollback()
        except Exception: pass
        return 'erro'
    finally:
        conn.close()


def listar_contratos(uf=None, somente_ti=True, busca=None, vencendo_em=None,
                     ordem='valor', limite=200):
    """Lista contratos (inteligência de preço). vencendo_em=N => só os que vencem em N dias."""
    conn = get_radar_db()
    where, params = [], []
    if somente_ti:
        where.append('is_ti=1')
    if uf:
        where.append('uf=?'); params.append(uf.upper())
    if busca:
        where.append('(objeto LIKE ? OR orgao LIKE ? OR fornecedor LIKE ?)')
        params += [f'%{busca}%'] * 3
    if vencendo_em:
        where.append("vigencia_fim IS NOT NULL AND vigencia_fim != ''")
        where.append("date(vigencia_fim) <= date('now', ?)")
        params.append(f'+{int(vencendo_em)} days')
        where.append("date(vigencia_fim) >= date('now')")
    sql = 'SELECT * FROM radar_contratos'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    ordem_sql = {'valor': 'valor DESC', 'vence': 'vigencia_fim ASC',
                 'novo': 'coletado_em DESC'}.get(ordem, 'valor DESC')
    sql += f' ORDER BY {ordem_sql} LIMIT ?'; params.append(limite)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def stats_contratos():
    conn = get_radar_db()
    s = {}
    s['total'] = conn.execute('SELECT COUNT(*) FROM radar_contratos WHERE is_ti=1').fetchone()[0]
    s['soma']  = conn.execute('SELECT COALESCE(SUM(valor),0) FROM radar_contratos WHERE is_ti=1').fetchone()[0]
    s['vencendo90'] = conn.execute(
        "SELECT COUNT(*) FROM radar_contratos WHERE is_ti=1 AND vigencia_fim!='' "
        "AND date(vigencia_fim) BETWEEN date('now') AND date('now','+90 days')").fetchone()[0]
    conn.close()
    return s


# ── Usuários (SaaS: login/senha/reset) ───────────────────────────────────────
def get_radar_user(uid):
    conn = get_radar_db()
    u = conn.execute('SELECT * FROM radar_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return dict(u) if u else None


def get_radar_user_by_email(email):
    conn = get_radar_db()
    u = conn.execute('SELECT * FROM radar_users WHERE email=?', ((email or '').strip().lower(),)).fetchone()
    conn.close()
    return dict(u) if u else None


def contar_radar_users():
    conn = get_radar_db()
    n = conn.execute('SELECT COUNT(*) FROM radar_users').fetchone()[0]
    conn.close()
    return n


def criar_radar_user(nome, email, telefone, password_hash, is_admin=0):
    conn = get_radar_db()
    try:
        cur = conn.execute(
            'INSERT INTO radar_users (nome,email,telefone,password_hash,is_admin,plan_active,created_at) '
            'VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)',
            (nome, (email or '').strip().lower(), telefone, password_hash, is_admin,
             1 if is_admin else 0))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def listar_radar_users():
    conn = get_radar_db()
    rows = conn.execute('SELECT id, nome, email, telefone, plano, plan_active, is_admin, '
                        'created_at, ultimo_acesso FROM radar_users ORDER BY id DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def radar_exec(sql, params=()):
    """Helper genérico p/ UPDATEs simples (reset token, ultimo_acesso, etc.)."""
    conn = get_radar_db()
    try:
        conn.execute(sql, params); conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# RADAR LICITA NORTE — usuários próprios + consulta regional (lê radar_licitacoes)
# ══════════════════════════════════════════════════════════════════════════════
def get_licita_user(uid):
    conn = get_radar_db()
    u = conn.execute('SELECT * FROM licita_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return dict(u) if u else None


def get_licita_user_by_email(email):
    conn = get_radar_db()
    u = conn.execute('SELECT * FROM licita_users WHERE email=?',
                     ((email or '').strip().lower(),)).fetchone()
    conn.close()
    return dict(u) if u else None


def contar_licita_users():
    conn = get_radar_db()
    n = conn.execute('SELECT COUNT(*) FROM licita_users').fetchone()[0]
    conn.close()
    return n


def criar_licita_user(nome, email, telefone, password_hash, is_admin=0):
    conn = get_radar_db()
    try:
        cur = conn.execute(
            'INSERT INTO licita_users (nome,email,telefone,password_hash,is_admin,plan_active,created_at) '
            'VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)',
            (nome, (email or '').strip().lower(), telefone, password_hash, is_admin,
             1 if is_admin else 0))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def listar_licita_users():
    conn = get_radar_db()
    rows = conn.execute('SELECT id, nome, email, telefone, plano, plan_active, is_admin, '
                        'created_at, ultimo_acesso FROM licita_users ORDER BY id DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Cidades do Norte de SC + Vale do Itajaí, raio ~30-70km (normalizadas: sem acento, MAIÚSCULAS)
NORTE_CIDADES = {
    # Vale do Itapocu (núcleo)
    'SCHROEDER', 'GUARAMIRIM', 'JARAGUA DO SUL', 'CORUPA', 'MASSARANDUBA',
    # Norte / Joinville e litoral norte
    'JOINVILLE', 'ARAQUARI', 'SAO FRANCISCO DO SUL', 'BARRA VELHA',
    'BALNEARIO PICARRAS', 'PENHA', 'NAVEGANTES', 'GARUVA', 'ITAPOA',
    # Planalto Norte
    'SAO BENTO DO SUL', 'RIO NEGRINHO', 'CAMPO ALEGRE', 'MAFRA',
    # Vale do Itajaí
    'BLUMENAU', 'POMERODE', 'TIMBO', 'INDAIAL', 'GASPAR', 'BRUSQUE',
    # Litoral central
    'ITAJAI', 'BALNEARIO CAMBORIU',
}
# objeto que cheira a notícia/comunicação → selo 🗞️ (filé p/ Rádio SC News)
_KW_NOTICIA = ['noticia', 'jornal', 'comunicacao', 'imprensa', 'publicidade',
               'divulgacao', 'midia', 'portal de noticia', 'assessoria de comunicacao',
               'radiodifusao', 'jornalismo']


def _sem_acento(s):
    import unicodedata
    s = (s or '').lower()
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


def listar_licita_norte(valor_min=0, valor_max=500000, busca=None,
                        so_noticia=False, ordem='prazo', limite=400):
    """Licitações das 6 cidades do Norte de SC, faixa de valor, TODAS as categorias.
    Lê o mesmo radar_licitacoes (preenchido pelo coletor nacional)."""
    conn = get_radar_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM radar_licitacoes WHERE uf='SC'").fetchall()]
    conn.close()
    out = []
    for r in rows:
        if _sem_acento(r.get('municipio')).upper() not in NORTE_CIDADES:
            continue
        v = r.get('valor') or 0
        if v:  # valor 0/desconhecido sempre passa
            if valor_min and v < valor_min: continue
            if valor_max and v > valor_max: continue
        o = _sem_acento(r.get('objeto'))
        r['eh_noticia'] = any(k in o for k in _KW_NOTICIA)
        if so_noticia and not r['eh_noticia']:
            continue
        if busca and _sem_acento(busca) not in o:
            continue
        out.append(r)
    # ordena: notícia primeiro, depois pela ordem escolhida
    if ordem == 'valor':
        out.sort(key=lambda r: (not r['eh_noticia'], r.get('valor') or 1e12))
    elif ordem == 'novo':
        out.sort(key=lambda r: (not r['eh_noticia'], r.get('coletado_em') or ''), reverse=False)
    else:  # prazo
        out.sort(key=lambda r: (not r['eh_noticia'], r.get('data_encerramento') or '9999'))
    return out[:limite]


def stats_licita_norte():
    lst = listar_licita_norte(limite=99999)
    return {'total': len(lst),
            'noticia': sum(1 for r in lst if r.get('eh_noticia')),
            'cidades': len({r.get('municipio') for r in lst})}
