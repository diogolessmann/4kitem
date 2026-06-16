"""
consveic_db.py — Banco do módulo Consulta Veicular (4kitem)

Portal de débitos veiculares (IPVA/multas/licenciamento/DPVAT) rodando por cima
da API B2B da Zapay (White Label). A Zapay resolve consulta + pagamento + repasse
ao órgão; aqui guardamos só o necessário pra orquestrar a jornada e aplicar a
comissão do Diogo.

Tabelas:
  • consveic_consultas — cada consulta de placa (idempotente por request_id da Zapay)
  • consveic_pedidos   — cada pedido de pagamento (idempotente por order_id)
  • consveic_eventos   — log cru dos webhooks recebidos (auditoria + dedupe)

Tudo é dirigido por WEBHOOK (assíncrono): a consulta e o pagamento voltam pela
URL de retorno, então o estado "real" vive nessas tabelas, atualizado pelo hook.
"""
import os
import json
import sqlite3
from datetime import datetime

_base   = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(_base, 'consveic.db')


def get_consveic_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_consveic_db():
    conn = get_consveic_db()
    conn.executescript('''
        -- ── Consultas de débito por placa ───────────────────────────────────
        CREATE TABLE IF NOT EXISTS consveic_consultas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id    TEXT UNIQUE,          -- id devolvido pela Zapay (chave natural)
            placa         TEXT,
            renavam       TEXT,
            uf            TEXT,
            status        TEXT DEFAULT 'pendente',  -- pendente|ok|erro|sem_debitos
            debitos_json  TEXT,                 -- payload de débitos (cru, vindo do webhook)
            total         REAL DEFAULT 0,       -- soma dos débitos (R$)
            mensagem      TEXT,                 -- aviso (ex: precisa de dado do cliente)
            criado_em     TEXT,
            atualizado_em TEXT
        );

        -- ── Pedidos de pagamento ────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS consveic_pedidos (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id       TEXT UNIQUE,         -- id do pedido na Zapay
            external_id    TEXT,               -- nosso id de rastreio
            consulta_id    INTEGER,            -- FK -> consveic_consultas.id
            placa          TEXT,
            meio           TEXT,               -- pix|credit_card|bank_slip
            parcelas       INTEGER DEFAULT 1,
            valor_debitos  REAL DEFAULT 0,     -- quanto é dos débitos (vai pra Zapay)
            comissao       REAL DEFAULT 0,     -- nossa margem por cima
            valor_total    REAL DEFAULT 0,     -- débitos + comissão (o que o cliente paga)
            status         TEXT DEFAULT 'pending',  -- pending|processing|paid|canceled|failed
            pix_copia_cola TEXT,               -- BR Code do PIX (se meio=pix)
            cliente_nome   TEXT,
            cliente_doc    TEXT,
            cliente_email  TEXT,
            cliente_fone   TEXT,
            criado_em      TEXT,
            atualizado_em  TEXT,
            FOREIGN KEY (consulta_id) REFERENCES consveic_consultas (id)
        );

        -- ── Log cru de webhooks (auditoria + idempotência) ──────────────────
        CREATE TABLE IF NOT EXISTS consveic_eventos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    TEXT,                  -- id do evento (se a Zapay mandar) p/ dedupe
            tipo        TEXT,                  -- vehicle_debt|payment|webhook_validation...
            ref         TEXT,                  -- request_id/order_id relacionado
            assinatura_ok INTEGER DEFAULT 0,   -- HMAC bateu? (1/0)
            payload     TEXT,
            recebido_em TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_cv_ev_ref  ON consveic_eventos (ref);
        CREATE INDEX IF NOT EXISTS ix_cv_ped_st  ON consveic_pedidos (status);
    ''')
    conn.commit()
    conn.close()


def _agora():
    return datetime.utcnow().isoformat()


# ── Consultas ────────────────────────────────────────────────────────────────
def criar_consulta(placa, request_id=None, renavam=None, uf=None):
    """Registra uma consulta recém-disparada (status pendente). Idempotente por request_id."""
    conn = get_consveic_db()
    try:
        cur = conn.execute(
            '''INSERT INTO consveic_consultas (request_id, placa, renavam, uf, status, criado_em, atualizado_em)
               VALUES (?,?,?,?, 'pendente', ?, ?)
               ON CONFLICT(request_id) DO UPDATE SET atualizado_em=excluded.atualizado_em''',
            (request_id, (placa or '').upper().strip(), renavam, (uf or '').upper(), _agora(), _agora()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def atualizar_consulta(request_id, status=None, debitos=None, total=None, mensagem=None,
                       renavam=None, uf=None):
    """Atualiza a consulta quando o webhook traz o resultado."""
    conn = get_consveic_db()
    try:
        campos, vals = [], []
        if status   is not None: campos.append('status=?');       vals.append(status)
        if debitos  is not None: campos.append('debitos_json=?');  vals.append(json.dumps(debitos, ensure_ascii=False))
        if total    is not None: campos.append('total=?');         vals.append(float(total))
        if mensagem is not None: campos.append('mensagem=?');      vals.append(mensagem)
        if renavam  is not None: campos.append('renavam=?');       vals.append(renavam)
        if uf       is not None: campos.append('uf=?');            vals.append((uf or '').upper())
        campos.append('atualizado_em=?'); vals.append(_agora())
        vals.append(request_id)
        conn.execute(f'UPDATE consveic_consultas SET {", ".join(campos)} WHERE request_id=?', vals)
        conn.commit()
    finally:
        conn.close()


def obter_consulta(request_id=None, consulta_id=None):
    conn = get_consveic_db()
    try:
        if request_id:
            row = conn.execute('SELECT * FROM consveic_consultas WHERE request_id=?', (request_id,)).fetchone()
        else:
            row = conn.execute('SELECT * FROM consveic_consultas WHERE id=?', (consulta_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Pedidos ──────────────────────────────────────────────────────────────────
def criar_pedido(**kw):
    """Registra um pedido de pagamento recém-criado. Idempotente por order_id."""
    conn = get_consveic_db()
    try:
        cur = conn.execute(
            '''INSERT INTO consveic_pedidos
                 (order_id, external_id, consulta_id, placa, meio, parcelas,
                  valor_debitos, comissao, valor_total, status, cliente_nome,
                  cliente_doc, cliente_email, cliente_fone, criado_em, atualizado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(order_id) DO UPDATE SET atualizado_em=excluded.atualizado_em''',
            (kw.get('order_id'), kw.get('external_id'), kw.get('consulta_id'),
             (kw.get('placa') or '').upper(), kw.get('meio'), int(kw.get('parcelas', 1)),
             float(kw.get('valor_debitos', 0)), float(kw.get('comissao', 0)),
             float(kw.get('valor_total', 0)), kw.get('status', 'pending'),
             kw.get('cliente_nome'), kw.get('cliente_doc'), kw.get('cliente_email'),
             kw.get('cliente_fone'), _agora(), _agora()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def atualizar_pedido(order_id, status=None, pix_copia_cola=None):
    conn = get_consveic_db()
    try:
        campos, vals = [], []
        if status         is not None: campos.append('status=?');         vals.append(status)
        if pix_copia_cola is not None: campos.append('pix_copia_cola=?');  vals.append(pix_copia_cola)
        campos.append('atualizado_em=?'); vals.append(_agora())
        vals.append(order_id)
        conn.execute(f'UPDATE consveic_pedidos SET {", ".join(campos)} WHERE order_id=?', vals)
        conn.commit()
    finally:
        conn.close()


def obter_pedido(order_id=None, external_id=None):
    conn = get_consveic_db()
    try:
        if order_id:
            row = conn.execute('SELECT * FROM consveic_pedidos WHERE order_id=?', (order_id,)).fetchone()
        else:
            row = conn.execute('SELECT * FROM consveic_pedidos WHERE external_id=?', (external_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def listar_pedidos(limit=100):
    conn = get_consveic_db()
    try:
        rows = conn.execute('SELECT * FROM consveic_pedidos ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Eventos / webhooks ───────────────────────────────────────────────────────
def evento_ja_processado(event_id):
    """Dedupe: a Zapay avisa que eventos podem vir duplicados e fora de ordem."""
    if not event_id:
        return False
    conn = get_consveic_db()
    try:
        row = conn.execute('SELECT 1 FROM consveic_eventos WHERE event_id=?', (event_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def registrar_evento(tipo, ref, payload, assinatura_ok=False, event_id=None):
    conn = get_consveic_db()
    try:
        conn.execute(
            '''INSERT INTO consveic_eventos (event_id, tipo, ref, assinatura_ok, payload, recebido_em)
               VALUES (?,?,?,?,?,?)''',
            (event_id, tipo, ref, 1 if assinatura_ok else 0,
             json.dumps(payload, ensure_ascii=False)[:20000], _agora()))
        conn.commit()
    finally:
        conn.close()


def estatisticas():
    conn = get_consveic_db()
    try:
        c = conn.execute('SELECT COUNT(*) FROM consveic_consultas').fetchone()[0]
        p = conn.execute("SELECT COUNT(*) FROM consveic_pedidos WHERE status='paid'").fetchone()[0]
        fat = conn.execute("SELECT COALESCE(SUM(comissao),0) FROM consveic_pedidos WHERE status='paid'").fetchone()[0]
        return {'consultas': c, 'pagos': p, 'comissao_total': round(fat or 0, 2)}
    finally:
        conn.close()
