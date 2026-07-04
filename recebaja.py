"""
recebaja.py — Blueprint RecebaJá
Cobrança de boleto no WhatsApp (a régua de lembrete). O banco do lojista emite o
boleto; o RecebaJá lê por OCR (Gemini) e cobra sozinho no Zap (Evolution).
NÃO processa pagamento — baixa MANUAL no MVP. O "dente" (protesto/Serasa) é v2.

v1 (este arquivo): login simples + cadastrar cobrança por foto + régua automática
+ baixa manual. Sem paywall e sem CNPJ (isso é do tier Pro/dente).
"""
import os
import io
import time
import json
import base64
import logging
import threading
import requests as _requests
from datetime import datetime, date, timedelta
from functools import wraps
from flask import (Blueprint, render_template, redirect, request,
                   session, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash
from recebaja_db import get_recebaja_db, init_recebaja_db

log = logging.getLogger('recebaja')

recebaja_bp = Blueprint('recebaja', __name__, url_prefix='/recebaja')


# ── IA: Gemini (mesmo padrão do DRZAP) ─────────────────────────────────────────
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


def _gemini_call(system, contents, json_mode=False, max_tokens=1024, temperature=0.1):
    body = {
        'contents': contents,
        'generationConfig': {'temperature': temperature, 'maxOutputTokens': max_tokens},
    }
    if system:
        body['systemInstruction'] = {'parts': [{'text': system}]}
    if json_mode:
        body['generationConfig']['responseMimeType'] = 'application/json'
    r = _requests.post(_GEMINI_URL.format(model=GEMINI_MODEL),
                       params={'key': GEMINI_KEY}, json=body, timeout=90)
    r.raise_for_status()
    data = r.json()
    return data['candidates'][0]['content']['parts'][0]['text'].strip()


SYSTEM_OCR_BOLETO = """Você é o leitor de boletos do RecebaJá. A pessoa enviou a FOTO ou PDF de um \
BOLETO bancário brasileiro. Leia e devolva APENAS um JSON válido (sem texto fora do JSON) com as chaves:
{
 "cliente": "nome do sacado/pagador (quem deve). Se não achar, string vazia.",
 "valor": "valor do documento em reais, só números com ponto decimal (ex: 450.00)",
 "vencimento": "data de vencimento no formato AAAA-MM-DD",
 "linha_digitavel": "a linha digitável do boleto, SÓ os números (47 ou 48 dígitos), sem pontos/espaços"
}
Se algum campo não estiver legível, use string vazia. NUNCA invente dados."""


# ── WhatsApp: Evolution API (mesmo padrão do amparo_wa) ────────────────────────
_EVO_URL  = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
_EVO_KEY  = os.environ.get('EVOLUTION_API_KEY', '')
_EVO_INST = os.environ.get('RECEBAJA_WA_INSTANCE', 'recebaja')


def _wa_configurado():
    return bool(_EVO_URL and _EVO_KEY and _EVO_INST)


def _so_digitos(v):
    d = ''.join(c for c in str(v or '') if c.isdigit())
    if d and not d.startswith('55'):
        d = '55' + d
    return d


def _wa_enviar(to, texto):
    """Envia texto via Evolution. Se não configurada, só loga (não quebra o fluxo)."""
    d = _so_digitos(to)
    if not d:
        return {'ok': False, 'reason': 'sem_destinatario'}
    if not _wa_configurado():
        log.info(f'[RecebaJá WA dev] (Evolution off) enviaria p/ {d}: {texto!r}')
        return {'ok': False, 'configurado': False, 'preview': texto}
    try:
        r = _requests.post(f'{_EVO_URL}/message/sendText/{_EVO_INST}',
                           json={'number': d + '@s.whatsapp.net', 'text': texto},
                           headers={'apikey': _EVO_KEY}, timeout=15)
        ok = r.status_code in (200, 201)
        if not ok:
            log.warning(f'[RecebaJá WA] falha {r.status_code} p/ {d}: {r.text[:200]}')
        return {'ok': ok, 'status': r.status_code}
    except Exception as e:
        log.warning(f'[RecebaJá WA] erro p/ {d}: {e}')
        return {'ok': False, 'reason': str(e)}


# ── Auth / helpers ─────────────────────────────────────────────────────────────
def recebaja_login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get('rj_user_id'):
            return redirect('/recebaja/entrar')
        return f(*a, **k)
    return wrap


def _get_user():
    uid = session.get('rj_user_id')
    if not uid:
        return None
    conn = get_recebaja_db()
    u = conn.execute('SELECT * FROM recebaja_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return u


def _fmt_brl(centavos):
    v = (centavos or 0) / 100.0
    s = f'{v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'R$ {s}'


def _valor_para_centavos(txt):
    s = str(txt or '').strip().replace('R$', '').replace(' ', '')
    if not s:
        return 0
    if ',' in s and '.' in s:            # 1.234,56
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:                        # 1234,56
        s = s.replace(',', '.')
    try:
        return int(round(float(s) * 100))
    except Exception:
        return 0


def _parse_date(s):
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(str(s or '')[:10], fmt).date()
        except Exception:
            continue
    return None


def _fmt_data(iso):
    d = _parse_date(iso)
    return d.strftime('%d/%m/%Y') if d else (iso or '')


# ── Régua de cobrança ──────────────────────────────────────────────────────────
REGUA = {-3: 'D-3', 0: 'D0', 3: 'D+3', 7: 'D+7'}   # offset (dias após vencimento) → etapa


def _regua_texto(tipo, cliente_nome, negocio, valor_fmt, venc_fmt, linha):
    primeiro = (cliente_nome or '').split()[0] if cliente_nome else 'tudo bem'
    assina   = f'\n\n— {negocio}' if negocio else ''
    codigo   = f'\n\n{linha}' if linha else ''
    if tipo == 'D-3':
        return (f'Oi {primeiro}, tudo bem? 😊 Passando pra lembrar que seu boleto de '
                f'{valor_fmt} vence dia {venc_fmt}. É só copiar o código pra pagar 👇{codigo}{assina}')
    if tipo == 'D0':
        return (f'Oi {primeiro}! Seu boleto de {valor_fmt} vence *hoje* 🗓️ '
                f'Código pra pagar 👇{codigo}{assina}')
    if tipo == 'D+3':
        return (f'Oi {primeiro}, tudo certo? Vi que o boleto de {valor_fmt} (venc. {venc_fmt}) '
                f'ainda consta em aberto. Se já pagou, pode ignorar 🙏 Se não, é só copiar 👇{codigo}{assina}')
    if tipo == 'D+7':
        return (f'Oi {primeiro}, o boleto de {valor_fmt} (venceu em {venc_fmt}) ainda está em aberto. '
                f'Consegue acertar hoje? Qualquer coisa me chama 🙏{codigo}{assina}')
    return (f'Oi {primeiro}, sobre seu boleto de {valor_fmt} (venc. {venc_fmt}) 👇{codigo}{assina}')


def _set_status(cobranca_id, status, pago_em=None):
    conn = get_recebaja_db()
    if pago_em:
        conn.execute('UPDATE recebaja_cobrancas SET status=?, pago_em=? WHERE id=?',
                     (status, pago_em, cobranca_id))
    else:
        conn.execute('UPDATE recebaja_cobrancas SET status=? WHERE id=?', (status, cobranca_id))
    conn.commit(); conn.close()


def _reservar_envio(cobranca_id, tipo):
    """Idempotência ATÔMICA: reserva o envio de uma etapa. Retorna True só na 1ª vez.
    Se dois workers (gunicorn) tentarem juntos, só um passa — o outro cai no UNIQUE."""
    conn = get_recebaja_db()
    try:
        conn.execute('INSERT INTO recebaja_msg_log (cobranca_id, tipo, enviado_em) VALUES (?,?,?)',
                     (cobranca_id, tipo, datetime.now().isoformat()))
        conn.commit(); conn.close()
        return True
    except Exception:
        try: conn.close()
        except Exception: pass
        return False


def _rodar_regua_uma_vez():
    hoje = date.today()
    hora = datetime.now().hour
    conn = get_recebaja_db()
    rows = conn.execute(
        "SELECT c.*, cl.nome AS cli_nome, cl.whatsapp AS cli_wa, u.negocio AS negocio "
        "FROM recebaja_cobrancas c "
        "JOIN recebaja_clientes cl ON cl.id = c.cliente_id "
        "JOIN recebaja_users    u  ON u.id  = c.user_id "
        "WHERE c.status IN ('a_vencer','atrasado') AND c.opt_in=1").fetchall()
    conn.close()
    for c in rows:
        try:
            venc = _parse_date(c['vencimento'])
            if not venc:
                continue
            offset = (hoje - venc).days
            if offset > 0 and c['status'] == 'a_vencer':
                _set_status(c['id'], 'atrasado')
            tipo = REGUA.get(offset)
            if not tipo:
                continue
            if hora < 8 or hora >= 20:        # fora do horário comercial → tenta na próxima hora
                continue
            if not _reservar_envio(c['id'], tipo):
                continue
            texto = _regua_texto(tipo, c['cli_nome'], c['negocio'],
                                 _fmt_brl(c['valor_centavos']), _fmt_data(c['vencimento']),
                                 c['linha_digitavel'] or '')
            _wa_enviar(c['cli_wa'], texto)
            log.info(f"[RecebaJá] régua {tipo} enviada — cobrança {c['id']}")
        except Exception as e:
            log.warning(f"[RecebaJá] régua cobrança {c['id']}: {e}")


def _recebaja_regua_loop():
    time.sleep(160)  # deixa o app subir
    log.info('[RecebaJá] Régua de cobrança ATIVA (verifica de hora em hora)')
    while True:
        try:
            _rodar_regua_uma_vez()
        except Exception as e:
            log.error(f'[RecebaJá] régua loop: {e}')
        time.sleep(3600)


# ── Rotas: público / auth ──────────────────────────────────────────────────────
@recebaja_bp.route('/')
def landing():
    return render_template('recebaja/landing.html')


@recebaja_bp.route('/cadastrar', methods=['GET', 'POST'])
def cadastrar():
    if session.get('rj_user_id'):
        return redirect('/recebaja/app')
    erro = None
    if request.method == 'POST':
        nome     = (request.form.get('nome') or '').strip()
        email    = (request.form.get('email') or '').strip().lower()
        negocio  = (request.form.get('negocio') or '').strip()
        telefone = _so_digitos(request.form.get('telefone'))
        senha    = request.form.get('senha') or ''
        if not nome or not email or not senha:
            erro = 'Preencha nome, e-mail e senha.'
        elif len(senha) < 6:
            erro = 'A senha precisa ter pelo menos 6 caracteres.'
        else:
            conn = get_recebaja_db()
            if conn.execute('SELECT id FROM recebaja_users WHERE email=?', (email,)).fetchone():
                conn.close()
                erro = 'Já existe uma conta com esse e-mail. Faça login.'
            else:
                cur = conn.execute(
                    'INSERT INTO recebaja_users (nome,email,telefone,negocio,password_hash,termo_aceito,created_at) '
                    'VALUES (?,?,?,?,?,1,?)',
                    (nome, email, telefone, negocio, generate_password_hash(senha),
                     datetime.now().isoformat()))
                conn.commit()
                uid = cur.lastrowid
                conn.close()
                session['rj_user_id'] = uid
                session['rj_nome']    = nome
                return redirect('/recebaja/app')
    return render_template('recebaja/cadastrar.html', erro=erro)


@recebaja_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if session.get('rj_user_id'):
        return redirect('/recebaja/app')
    erro = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        conn = get_recebaja_db()
        u = conn.execute('SELECT * FROM recebaja_users WHERE email=?', (email,)).fetchone()
        if u and check_password_hash(u['password_hash'], senha):
            conn.execute('UPDATE recebaja_users SET ultimo_acesso=? WHERE id=?',
                         (datetime.now().isoformat(), u['id']))
            conn.commit(); conn.close()
            session['rj_user_id'] = u['id']
            session['rj_nome']    = u['nome']
            return redirect('/recebaja/app')
        conn.close()
        erro = 'E-mail ou senha incorretos.'
    return render_template('recebaja/entrar.html', erro=erro)


@recebaja_bp.route('/sair')
def sair():
    for k in ('rj_user_id', 'rj_nome'):
        session.pop(k, None)
    return redirect('/recebaja')


# ── Rotas: área logada ─────────────────────────────────────────────────────────
def _cobranca_view(c):
    """Monta o dict de exibição de uma cobrança (linha do JOIN com cliente)."""
    venc = _parse_date(c['vencimento'])
    hoje = date.today()
    dias = (venc - hoje).days if venc else None
    status = c['status']
    if status == 'pago':
        rotulo = 'Pago'
    elif status == 'cancelado':
        rotulo = 'Cancelado'
    elif dias is None:
        rotulo = ''
    elif dias > 1:
        rotulo = f'Vence em {dias} dias'
    elif dias == 1:
        rotulo = 'Vence amanhã'
    elif dias == 0:
        rotulo = 'Vence hoje'
    elif dias == -1:
        rotulo = 'Venceu ontem'
    else:
        rotulo = f'Atrasado {abs(dias)} dias'
    return {
        'id': c['id'], 'cliente': c['cli_nome'], 'whatsapp': c['cli_wa'],
        'valor': _fmt_brl(c['valor_centavos']), 'valor_centavos': c['valor_centavos'],
        'vencimento': _fmt_data(c['vencimento']), 'venc_iso': c['vencimento'],
        'linha': c['linha_digitavel'] or '', 'status': status, 'rotulo': rotulo,
        'opt_in': c['opt_in'],
    }


@recebaja_bp.route('/app')
@recebaja_login_required
def app_home():
    u = _get_user()
    if not u:
        session.clear()
        return redirect('/recebaja/entrar')
    conn = get_recebaja_db()
    rows = conn.execute(
        "SELECT c.*, cl.nome AS cli_nome, cl.whatsapp AS cli_wa "
        "FROM recebaja_cobrancas c JOIN recebaja_clientes cl ON cl.id=c.cliente_id "
        "WHERE c.user_id=? ORDER BY "
        "  CASE c.status WHEN 'atrasado' THEN 0 WHEN 'a_vencer' THEN 1 ELSE 2 END, "
        "  c.vencimento ASC", (u['id'],)).fetchall()
    conn.close()
    cobrancas = [_cobranca_view(c) for c in rows]
    a_receber = sum(c['valor_centavos'] for c, r in zip(rows, cobrancas) if r['status'] == 'a_vencer')
    atrasado  = sum(c['valor_centavos'] for c, r in zip(rows, cobrancas) if r['status'] == 'atrasado')
    recebido  = sum(c['valor_centavos'] for c, r in zip(rows, cobrancas) if r['status'] == 'pago')
    return render_template('recebaja/app.html', u=u, cobrancas=cobrancas,
                           a_receber=_fmt_brl(a_receber), atrasado=_fmt_brl(atrasado),
                           recebido=_fmt_brl(recebido))


@recebaja_bp.route('/nova')
@recebaja_login_required
def nova():
    return render_template('recebaja/nova.html', u=_get_user())


@recebaja_bp.route('/ocr', methods=['POST'])
@recebaja_login_required
def ocr():
    f = request.files.get('arquivo')
    if not f or not f.filename:
        return jsonify({'erro': 'Envie uma foto do boleto.'}), 400
    raw = f.read()
    if len(raw) > 10 * 1024 * 1024:
        return jsonify({'erro': 'Arquivo muito grande (máximo 10MB).'}), 400
    if len(raw) < 50:
        return jsonify({'erro': 'Arquivo vazio ou inválido.'}), 400
    mime = (f.mimetype or '').lower()
    if mime == 'application/pdf' or f.filename.lower().endswith('.pdf'):
        mime = 'application/pdf'
    elif not mime.startswith('image/'):
        return jsonify({'erro': 'Envie uma foto (JPG/PNG) ou PDF do boleto.'}), 400
    try:
        b64 = base64.b64encode(raw).decode('ascii')
        contents = [{'role': 'user', 'parts': [
            {'inlineData': {'mimeType': mime, 'data': b64}},
            {'text': 'Leia este boleto e devolva o JSON pedido.'}]}]
        txt = _gemini_call(SYSTEM_OCR_BOLETO, contents, json_mode=True, max_tokens=600)
        dados = json.loads(txt)
    except Exception as e:
        log.warning(f'[RecebaJá] OCR falhou: {e}')
        return jsonify({'erro': 'Não consegui ler o boleto. Tente uma foto mais nítida ou digite à mão.'}), 502
    return jsonify({
        'ok': True,
        'cliente': (dados.get('cliente') or '').strip(),
        'valor': (str(dados.get('valor') or '')).strip(),
        'vencimento': (dados.get('vencimento') or '').strip(),
        'linha_digitavel': ''.join(ch for ch in str(dados.get('linha_digitavel') or '') if ch.isdigit()),
    })


@recebaja_bp.route('/nova', methods=['POST'])
@recebaja_login_required
def nova_salvar():
    u = _get_user()
    cliente   = (request.form.get('cliente') or '').strip()
    whatsapp  = _so_digitos(request.form.get('whatsapp'))
    venc      = _parse_date(request.form.get('vencimento'))
    centavos  = _valor_para_centavos(request.form.get('valor'))
    linha     = ''.join(ch for ch in (request.form.get('linha_digitavel') or '') if ch.isdigit())
    opt_in    = 1 if request.form.get('opt_in') else 0
    if not cliente or not whatsapp:
        return jsonify({'erro': 'Preencha o nome e o WhatsApp do cliente.'}), 400
    if not venc:
        return jsonify({'erro': 'Confira a data de vencimento.'}), 400
    if centavos < 1:
        return jsonify({'erro': 'Confira o valor do boleto.'}), 400
    conn = get_recebaja_db()
    cli = conn.execute('SELECT id FROM recebaja_clientes WHERE user_id=? AND whatsapp=?',
                       (u['id'], whatsapp)).fetchone()
    if cli:
        cliente_id = cli['id']
    else:
        cur = conn.execute('INSERT INTO recebaja_clientes (user_id,nome,whatsapp,created_at) '
                           'VALUES (?,?,?,?)', (u['id'], cliente, whatsapp, datetime.now().isoformat()))
        cliente_id = cur.lastrowid
    status = 'atrasado' if (date.today() - venc).days > 0 else 'a_vencer'
    conn.execute(
        'INSERT INTO recebaja_cobrancas (user_id,cliente_id,valor_centavos,vencimento,'
        'linha_digitavel,status,opt_in,criado_em) VALUES (?,?,?,?,?,?,?,?)',
        (u['id'], cliente_id, centavos, venc.isoformat(), linha, status, opt_in,
         datetime.now().isoformat()))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'redirect': '/recebaja/app'})


@recebaja_bp.route('/cobranca/<int:cid>')
@recebaja_login_required
def cobranca(cid):
    u = _get_user()
    conn = get_recebaja_db()
    c = conn.execute(
        "SELECT c.*, cl.nome AS cli_nome, cl.whatsapp AS cli_wa "
        "FROM recebaja_cobrancas c JOIN recebaja_clientes cl ON cl.id=c.cliente_id "
        "WHERE c.id=? AND c.user_id=?", (cid, u['id'])).fetchone()
    if not c:
        conn.close()
        return redirect('/recebaja/app')
    logs = conn.execute('SELECT tipo, enviado_em FROM recebaja_msg_log WHERE cobranca_id=? '
                        'ORDER BY enviado_em', (cid,)).fetchall()
    conn.close()
    enviados = {l['tipo'] for l in logs}
    etapas = [
        {'tipo': 'D-3', 'rotulo': '3 dias antes', 'feito': 'D-3' in enviados},
        {'tipo': 'D0',  'rotulo': 'No dia do vencimento', 'feito': 'D0' in enviados},
        {'tipo': 'D+3', 'rotulo': '3 dias depois', 'feito': 'D+3' in enviados},
        {'tipo': 'D+7', 'rotulo': '7 dias depois', 'feito': 'D+7' in enviados},
    ]
    return render_template('recebaja/cobranca.html', u=u, c=_cobranca_view(c), etapas=etapas)


@recebaja_bp.route('/cobranca/<int:cid>/pago', methods=['POST'])
@recebaja_login_required
def marcar_pago(cid):
    u = _get_user()
    conn = get_recebaja_db()
    row = conn.execute('SELECT id FROM recebaja_cobrancas WHERE id=? AND user_id=?',
                       (cid, u['id'])).fetchone()
    conn.close()
    if not row:
        return jsonify({'erro': 'não encontrada'}), 404
    _set_status(cid, 'pago', pago_em=datetime.now().isoformat())
    return jsonify({'ok': True})


@recebaja_bp.route('/cobranca/<int:cid>/cobrar', methods=['POST'])
@recebaja_login_required
def cobrar_agora(cid):
    u = _get_user()
    conn = get_recebaja_db()
    c = conn.execute(
        "SELECT c.*, cl.nome AS cli_nome, cl.whatsapp AS cli_wa, u.negocio AS negocio "
        "FROM recebaja_cobrancas c JOIN recebaja_clientes cl ON cl.id=c.cliente_id "
        "JOIN recebaja_users u ON u.id=c.user_id "
        "WHERE c.id=? AND c.user_id=?", (cid, u['id'])).fetchone()
    conn.close()
    if not c:
        return jsonify({'erro': 'não encontrada'}), 404
    if c['status'] in ('pago', 'cancelado'):
        return jsonify({'erro': 'Essa cobrança já foi encerrada.'}), 400
    venc = _parse_date(c['vencimento'])
    offset = (date.today() - venc).days if venc else 0
    tipo = 'D0' if offset <= 0 else ('D+3' if offset < 7 else 'D+7')
    texto = _regua_texto(tipo, c['cli_nome'], c['negocio'], _fmt_brl(c['valor_centavos']),
                         _fmt_data(c['vencimento']), c['linha_digitavel'] or '')
    res = _wa_enviar(c['cli_wa'], texto)
    _reservar_envio(cid, f'manual_{int(time.time())}')
    if res.get('ok'):
        return jsonify({'ok': True, 'msg': 'Cobrança enviada no WhatsApp! ✅'})
    if res.get('configurado') is False:
        return jsonify({'ok': True, 'msg': 'Prévia gerada (WhatsApp ainda não conectado neste ambiente).',
                        'preview': res.get('preview', '')})
    return jsonify({'erro': 'Não consegui enviar agora. Tente de novo.'}), 502


@recebaja_bp.route('/pro')
@recebaja_login_required
def pro():
    return render_template('recebaja/pro.html', u=_get_user())


# ── Worker da régua (auto-start, como o reconciliador do DRZAP/SlotZap) ─────────
threading.Thread(target=_recebaja_regua_loop, daemon=True, name='recebaja-regua').start()
