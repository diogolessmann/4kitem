"""
drzap.py — Blueprint DRZAP
Assistente jurídico por IA (orientação ao consumidor/trabalhista) — créditos pré-pagos.
⚖️ Orientação informativa. NÃO substitui advogado.
"""
import os
import time
import logging
import secrets
import threading
import requests as _requests
from datetime import datetime, timedelta
from functools import wraps
from flask import (Blueprint, render_template, redirect, request,
                   session, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash
from drzap_db import (get_drzap_db, init_drzap_db,
                      get_creditos, add_creditos, debita_creditos)

log = logging.getLogger('drzap')

drzap_bp = Blueprint('drzap', __name__, url_prefix='/drzap')

# ── IA: Gemini ─────────────────────────────────────────────────────────────────
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


def _gemini_call(system, contents, json_mode=False, max_tokens=2048, temperature=0.2):
    """Chama o Gemini via REST. `contents` no formato Gemini.
    Retorna (texto, tokens_in, tokens_out)."""
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
    txt = data['candidates'][0]['content']['parts'][0]['text'].strip()
    um  = data.get('usageMetadata', {}) or {}
    return txt, int(um.get('promptTokenCount', 0)), int(um.get('candidatesTokenCount', 0))


# ── E-mail (Resend) ─────────────────────────────────────────────────────────────
def _enviar_email(para: str, assunto: str, html: str) -> bool:
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        return False
    from_addr = os.environ.get('EMAIL_FROM', 'DRZAP <onboarding@resend.dev>')
    try:
        resp = _requests.post('https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'from': from_addr, 'to': [para], 'subject': assunto, 'html': html}, timeout=10)
        return resp.status_code in (200, 201)
    except Exception:
        return False


# ── Decorador / helpers ──────────────────────────────────────────────────────────
def drzap_login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get('drz_user_id'):
            return redirect('/drzap/entrar')
        return f(*a, **k)
    return wrap


def _get_user():
    uid = session.get('drz_user_id')
    if not uid:
        return None
    conn = get_drzap_db()
    u = conn.execute('SELECT * FROM drzap_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return u


@drzap_bp.context_processor
def _inject_creditos():
    if session.get('drz_user_id'):
        try:
            return {'drz_creditos': get_creditos(session['drz_user_id']),
                    'drz_nome': session.get('drz_user_nome', '')}
        except Exception:
            pass
    return {'drz_creditos': 0, 'drz_nome': ''}


def _cpf_digits(cpf):
    return ''.join(c for c in (cpf or '') if c.isdigit())


def _cpf_valido(cpf):
    cpf = _cpf_digits(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[j]) * ((i + 1) - j) for j in range(i))
        dig = (soma * 10) % 11
        if dig == 10:
            dig = 0
        if dig != int(cpf[i]):
            return False
    return True


# ── Pacotes de crédito ─────────────────────────────────────────────────────────
PACOTES = {
    'p10': {'creditos': 10, 'preco': 10.0, 'rotulo': '10 créditos',            'bonus': ''},
    'p25': {'creditos': 30, 'preco': 25.0, 'rotulo': '30 créditos',            'bonus': '+5 grátis'},
    'p50': {'creditos': 65, 'preco': 50.0, 'rotulo': '65 créditos',            'bonus': '+15 grátis'},
}

# ── Asaas (PIX) ──────────────────────────────────────────────────────────────────
_ASAAS_BASE = 'https://api.asaas.com/v3'

def _asaas_req(method, endpoint, data=None):
    try:
        r = _requests.request(method, f'{_ASAAS_BASE}{endpoint}',
            headers={'access_token': os.environ.get('ASAAS_API_KEY', ''), 'Content-Type': 'application/json'},
            json=data, timeout=20)
        return r.json() if r.content else {}
    except Exception as e:
        return {'error': str(e)}


def _asaas_cliente(u, cpf):
    """Cria/recupera cliente Asaas e salva o customer_id + cpf. Retorna customer_id ou ''."""
    if u['asaas_customer_id']:
        return u['asaas_customer_id']
    cpf = _cpf_digits(cpf)
    cid = None
    if cpf:
        busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf}&limit=1')
        if busca.get('data'):
            cid = busca['data'][0].get('id')
    if not cid:
        resp = _asaas_req('POST', '/customers', {
            'name': u['nome'], 'email': u['email'],
            'mobilePhone': _cpf_digits(u['telefone']), 'cpfCnpj': cpf,
            'notificationDisabled': True})
        cid = resp.get('id')
    if cid:
        conn = get_drzap_db()
        conn.execute('UPDATE drzap_users SET asaas_customer_id=?, cpf=? WHERE id=?', (cid, cpf, u['id']))
        conn.commit(); conn.close()
    return cid or ''


def _drz_confirmar_compra(compra_id):
    """Credita os créditos de uma compra de forma ATÔMICA e idempotente.
    Retorna True só se creditou AGORA (1ª confirmação). Evita crédito em dobro."""
    conn = get_drzap_db()
    cur = conn.execute("UPDATE drzap_compras SET status='pago' WHERE id=? AND status='pendente'",
                       (compra_id,))
    conn.commit()
    if cur.rowcount == 0:        # já confirmada por outra via (webhook/poll/reconciliador)
        conn.close()
        return False
    row = conn.execute('SELECT user_id, creditos FROM drzap_compras WHERE id=?', (compra_id,)).fetchone()
    conn.close()
    if not row:
        return False
    add_creditos(row['user_id'], row['creditos'])
    log.info(f'[DRZAP] Compra {compra_id} PAGA — +{row["creditos"]} créditos (user {row["user_id"]})')
    return True


def drz_webhook_confirmar(external_ref, payment_id=''):
    """Chamado pelo webhook global (app.py) p/ refs 'drzap_<compra_id>'."""
    try:
        cid = int(str(external_ref).split('_')[1])
    except (IndexError, ValueError):
        return False
    return _drz_confirmar_compra(cid)


# ── Rotas: público ────────────────────────────────────────────────────────────────
@drzap_bp.route('/')
def landing():
    return render_template('drzap/landing.html')


@drzap_bp.route('/cadastrar', methods=['GET', 'POST'])
def cadastrar():
    if session.get('drz_user_id'):
        return redirect('/drzap/app')
    erro = None
    if request.method == 'POST':
        nome     = (request.form.get('nome') or '').strip()
        email    = (request.form.get('email') or '').strip().lower()
        telefone = ''.join(c for c in (request.form.get('telefone') or '') if c.isdigit())
        senha    = request.form.get('senha') or ''
        termo    = request.form.get('termo')  # checkbox
        if not nome or not email or not senha:
            erro = 'Preencha nome, e-mail e senha.'
        elif len(senha) < 6:
            erro = 'A senha precisa ter pelo menos 6 caracteres.'
        elif not termo:
            erro = 'Você precisa aceitar o aviso (orientação, não substitui advogado).'
        else:
            conn = get_drzap_db()
            existe = conn.execute('SELECT id FROM drzap_users WHERE email=?', (email,)).fetchone()
            if existe:
                conn.close()
                erro = 'Já existe uma conta com esse e-mail. Faça login.'
            else:
                cur = conn.execute(
                    'INSERT INTO drzap_users (nome,email,telefone,password_hash,termo_aceito,creditos,created_at) '
                    'VALUES (?,?,?,?,1,0,?)',
                    (nome, email, telefone, generate_password_hash(senha), datetime.now().isoformat()))
                conn.commit()
                uid = cur.lastrowid
                conn.close()
                session['drz_user_id']   = uid
                session['drz_user_nome'] = nome
                return redirect('/drzap/comprar')
    return render_template('drzap/cadastrar.html', erro=erro)


@drzap_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if session.get('drz_user_id'):
        return redirect('/drzap/app')
    erro = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        conn = get_drzap_db()
        u = conn.execute('SELECT * FROM drzap_users WHERE email=?', (email,)).fetchone()
        if u and check_password_hash(u['password_hash'], senha):
            conn.execute('UPDATE drzap_users SET ultimo_acesso=? WHERE id=?',
                         (datetime.now().isoformat(), u['id']))
            conn.commit(); conn.close()
            session['drz_user_id']   = u['id']
            session['drz_user_nome'] = u['nome']
            return redirect('/drzap/app')
        conn.close()
        erro = 'E-mail ou senha incorretos.'
    return render_template('drzap/entrar.html', erro=erro)


@drzap_bp.route('/sair')
def sair():
    for k in ('drz_user_id', 'drz_user_nome'):
        session.pop(k, None)
    return redirect('/drzap')


@drzap_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    msg = erro = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        conn = get_drzap_db()
        u = conn.execute('SELECT * FROM drzap_users WHERE email=?', (email,)).fetchone()
        if u:
            token   = secrets.token_urlsafe(32)
            expires = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute('UPDATE drzap_users SET reset_token=?, reset_expires=? WHERE id=?',
                         (token, expires, u['id']))
            conn.commit()
            base = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
            link = f'{base}/drzap/redefinir-senha?token={token}'
            _enviar_email(email, 'DRZAP — Redefinir senha',
                f'<p>Olá! Para criar uma nova senha, clique no link (válido por 2h):</p>'
                f'<p><a href="{link}">{link}</a></p><p>Se não foi você, ignore este e-mail.</p>')
        conn.close()
        msg = 'Se o e-mail existir, enviamos um link para redefinir a senha. Confira sua caixa de entrada.'
    return render_template('drzap/esqueci.html', msg=msg, erro=erro)


@drzap_bp.route('/redefinir-senha', methods=['GET', 'POST'])
def redefinir_senha():
    token = request.args.get('token') or request.form.get('token') or ''
    msg = erro = None
    if request.method == 'POST':
        nova = request.form.get('senha') or ''
        if len(nova) < 6:
            erro = 'A senha precisa ter pelo menos 6 caracteres.'
        else:
            conn = get_drzap_db()
            u = conn.execute('SELECT * FROM drzap_users WHERE reset_token=? AND reset_token<>""',
                             (token,)).fetchone()
            valido = u and u['reset_expires'] and u['reset_expires'] > datetime.now().isoformat()
            if not valido:
                conn.close()
                erro = 'Link inválido ou expirado. Peça um novo.'
            else:
                conn.execute('UPDATE drzap_users SET password_hash=?, reset_token="", reset_expires="" WHERE id=?',
                             (generate_password_hash(nova), u['id']))
                conn.commit(); conn.close()
                msg = 'Senha alterada com sucesso! Já pode entrar.'
    return render_template('drzap/redefinir.html', token=token, msg=msg, erro=erro)


# ── Rotas: área logada ────────────────────────────────────────────────────────────
@drzap_bp.route('/app')
@drzap_login_required
def app_home():
    u = _get_user()
    if not u:
        session.clear()
        return redirect('/drzap/entrar')
    return render_template('drzap/app.html', u=u, creditos=get_creditos(u['id']))


@drzap_bp.route('/comprar')
@drzap_login_required
def comprar():
    u = _get_user()
    return render_template('drzap/comprar.html', u=u, creditos=get_creditos(u['id']), pacotes=PACOTES)


@drzap_bp.route('/checkout/<pacote>', methods=['GET', 'POST'])
@drzap_login_required
def checkout(pacote):
    u = _get_user()
    if pacote not in PACOTES:
        return redirect('/drzap/comprar')
    p = PACOTES[pacote]
    erro = None
    if request.method == 'POST':
        cpf = _cpf_digits(request.form.get('cpf'))
        if not _cpf_valido(cpf):
            erro = 'CPF inválido. Confira os números.'
        else:
            customer_id = _asaas_cliente(u, cpf)
            if not customer_id:
                erro = 'Não foi possível iniciar o pagamento. Tente novamente.'
            else:
                # cria a compra (pendente) e a cobrança PIX
                conn = get_drzap_db()
                cur = conn.execute(
                    'INSERT INTO drzap_compras (user_id,pacote,creditos,valor,status,billing_type,created_at) '
                    'VALUES (?,?,?,?,"pendente","PIX",?)',
                    (u['id'], pacote, p['creditos'], p['preco'], datetime.now().isoformat()))
                compra_id = cur.lastrowid
                conn.commit(); conn.close()
                venc = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                pay = _asaas_req('POST', '/payments', {
                    'customer': customer_id, 'billingType': 'PIX', 'value': p['preco'],
                    'dueDate': venc, 'description': f'DRZAP — {p["rotulo"]}',
                    'externalReference': f'drzap_{compra_id}'})
                pid = pay.get('id')
                if not pid:
                    erro = (pay.get('errors') or [{}])[0].get('description', 'Erro ao gerar o PIX.')
                else:
                    conn = get_drzap_db()
                    conn.execute('UPDATE drzap_compras SET asaas_payment_id=? WHERE id=?', (pid, compra_id))
                    conn.commit(); conn.close()
                    return redirect(f'/drzap/pix/{compra_id}')
    return render_template('drzap/checkout.html', u=u, pacote=pacote, p=p, erro=erro)


@drzap_bp.route('/pix/<int:compra_id>')
@drzap_login_required
def pix(compra_id):
    u = _get_user()
    conn = get_drzap_db()
    compra = conn.execute('SELECT * FROM drzap_compras WHERE id=? AND user_id=?',
                          (compra_id, u['id'])).fetchone()
    conn.close()
    if not compra:
        return redirect('/drzap/comprar')
    qr = copia = ''
    if compra['status'] == 'pendente' and compra['asaas_payment_id']:
        resp = _asaas_req('GET', f'/payments/{compra["asaas_payment_id"]}/pixQrCode')
        qr    = resp.get('encodedImage', '')
        copia = resp.get('payload', '')
    return render_template('drzap/pix.html', u=u, compra=compra, qr=qr, copia=copia)


@drzap_bp.route('/pix-status/<int:compra_id>', methods=['POST'])
@drzap_login_required
def pix_status(compra_id):
    u = _get_user()
    conn = get_drzap_db()
    compra = conn.execute('SELECT * FROM drzap_compras WHERE id=? AND user_id=?',
                          (compra_id, u['id'])).fetchone()
    conn.close()
    if not compra:
        return jsonify({'erro': 'não encontrada'}), 404
    if compra['status'] == 'pago':
        return jsonify({'pago': True, 'creditos': get_creditos(u['id'])})
    pid = compra['asaas_payment_id']
    if not pid:
        return jsonify({'pago': False})
    pay = _asaas_req('GET', f'/payments/{pid}')
    if (pay.get('status') or '').upper() in ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH'):
        _drz_confirmar_compra(compra_id)
        return jsonify({'pago': True, 'creditos': get_creditos(u['id'])})
    return jsonify({'pago': False})


# ── Reconciliador 24/7 (rede de segurança independente do webhook) ──────────────
def _drz_reconciliar_loop():
    time.sleep(150)  # deixa o app subir
    log.info('[DRZAP] Reconciliador de pagamentos ATIVO (verifica a cada 3 min)')
    while True:
        try:
            conn = get_drzap_db()
            limite = (datetime.now() - timedelta(seconds=90)).isoformat()
            rows = conn.execute(
                "SELECT id, asaas_payment_id FROM drzap_compras "
                "WHERE status='pendente' AND asaas_payment_id IS NOT NULL AND asaas_payment_id<>'' "
                "AND created_at < ?", (limite,)).fetchall()
            conn.close()
            for r in rows:
                try:
                    pay = _asaas_req('GET', f'/payments/{r["asaas_payment_id"]}')
                    if (pay.get('status') or '').upper() in ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH'):
                        _drz_confirmar_compra(r['id'])
                except Exception as _e:
                    log.warning(f'[DRZAP] reconciliador compra {r["id"]}: {_e}')
        except Exception as _e:
            log.error(f'[DRZAP] reconciliador loop: {_e}')
        time.sleep(180)  # a cada 3 minutos


threading.Thread(target=_drz_reconciliar_loop, daemon=True, name='drzap-reconciliador').start()
