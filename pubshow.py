"""
pubshow.py — Blueprint PUBSHOW
Jukebox digital 2.0 para bares, pubs e estabelecimentos
"""
import base64
import io
import logging
import os
import random
import re
import string
import requests as _requests
from datetime import datetime, timedelta
from functools import wraps
from flask import (Blueprint, render_template, redirect, request,
                   session, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash
from pubshow_db import get_pubshow_db, init_pubshow_db

log = logging.getLogger('pubshow')

pubshow_bp = Blueprint('pubshow', __name__, url_prefix='/pubshow')

# ── Configurações ──────────────────────────────────────────────────────────────
_ASAAS_BASE = 'https://api.asaas.com/v3'

TIPOS_ESTABELECIMENTO = {
    'bar':        '🍺 Bar / Boteco',
    'pub':        '🎸 Pub Rock',
    'esportivo':  '⚽ Bar Esportivo',
    'churrasco':  '🥩 Churrascaria',
    'restaurante':'🍽️ Restaurante',
    'academia':   '💪 Academia',
    'barbearia':  '✂️ Barbearia',
    'outro':      '🏢 Outro',
}

CANAIS = {
    # ── MÚSICA ───────────────────────────────────────────────────────────────
    'rock':           {'nome': 'Rock Clips',        'emoji': '🎸',  'cor': '#ef4444', 'cat': 'rock',      'grupo': 'musica'},
    'punk':           {'nome': 'Punk Clips',        'emoji': '🤘',  'cor': '#f97316', 'cat': 'punk',      'grupo': 'musica'},
    'sertanejo':      {'nome': 'Sertanejo Clips',   'emoji': '🤠',  'cor': '#eab308', 'cat': 'sertanejo', 'grupo': 'musica'},
    'pagode':         {'nome': 'Pagode Clips',      'emoji': '🥁',  'cor': '#22c55e', 'cat': 'pagode',    'grupo': 'musica'},
    'pop':            {'nome': 'Pop Clips',         'emoji': '🎤',  'cor': '#a855f7', 'cat': 'pop',       'grupo': 'musica'},
    # ── SHOWS AO VIVO ────────────────────────────────────────────────────────
    'show_rock':      {'nome': 'Rock Shows',        'emoji': '🎸🎤', 'cor': '#dc2626', 'cat': 'show_rock',      'grupo': 'shows'},
    'show_sertanejo': {'nome': 'Sertanejo Shows',   'emoji': '🤠🎤', 'cor': '#ca8a04', 'cat': 'show_sertanejo', 'grupo': 'shows'},
    'show_pagode':    {'nome': 'Pagode Shows',      'emoji': '🥁🎤', 'cor': '#15803d', 'cat': 'show_pagode',    'grupo': 'shows'},
    # ── SPORT ────────────────────────────────────────────────────────────────
    'sport_mix':      {'nome': 'Sport Mix',         'emoji': '🏆',  'cor': '#f59e0b',
                       'cat': ['f1', 'futebol', 'surf', 'aerio', 'radical',
                               'rally', 'wingsuit', 'aviacao', 'lutas', 'skate', 'kitesurf'],
                       'grupo': 'sport'},
    'f1':             {'nome': 'Speed Clips',       'emoji': '🏎️',  'cor': '#e11d48', 'cat': 'f1',        'grupo': 'sport'},
    'futebol':        {'nome': 'Futebol Clips',     'emoji': '⚽',  'cor': '#16a34a', 'cat': 'futebol',   'grupo': 'sport'},
    'surf':           {'nome': 'Surf Clips',        'emoji': '🏄',  'cor': '#0ea5e9', 'cat': 'surf',      'grupo': 'sport'},
    'aerio':          {'nome': 'Aéreo Clips',       'emoji': '🪂',  'cor': '#6366f1', 'cat': 'aerio',     'grupo': 'sport'},
    'radical':        {'nome': 'Radical Clips',     'emoji': '🛹',  'cor': '#f43f5e', 'cat': 'radical',   'grupo': 'sport'},
    'rally':          {'nome': 'Rally / Drift',     'emoji': '🏎️',  'cor': '#f97316', 'cat': 'rally',     'grupo': 'sport'},
    'wingsuit':       {'nome': 'Wingsuit / BASE',   'emoji': '🪂',  'cor': '#3b82f6', 'cat': 'wingsuit',  'grupo': 'sport'},
    'aviacao':        {'nome': 'Jatos / Aviação',   'emoji': '✈️',  'cor': '#06b6d4', 'cat': 'aviacao',   'grupo': 'sport'},
    'lutas':          {'nome': 'UFC / Lutas',       'emoji': '🥊',  'cor': '#dc2626', 'cat': 'lutas',     'grupo': 'sport'},
    'skate':          {'nome': 'Skate Clips',       'emoji': '🛹',  'cor': '#8b5cf6', 'cat': 'skate',     'grupo': 'sport'},
    'kitesurf':       {'nome': 'Kitesurf / Wake',   'emoji': '🌊',  'cor': '#0891b2', 'cat': 'kitesurf',  'grupo': 'sport'},
    # ── MÚSICA EXTRA ─────────────────────────────────────────────────────────
    'country':        {'nome': 'Country / Rodeio',  'emoji': '🤠',  'cor': '#a16207', 'cat': 'country',   'grupo': 'musica'},
    'reggae':         {'nome': 'Reggae Clips',      'emoji': '🎸',  'cor': '#15803d', 'cat': 'reggae',    'grupo': 'musica'},
    # ── ENTRETENIMENTO ───────────────────────────────────────────────────────
    'batidas':        {'nome': 'Batidas / Crashes', 'emoji': '💥',  'cor': '#b45309', 'cat': 'batidas',   'grupo': 'viral'},
    'standup':        {'nome': 'Stand-up Comedy',   'emoji': '🎭',  'cor': '#7c3aed', 'cat': 'standup',   'grupo': 'entretenimento'},
}

PLANOS = {
    'bar': {
        'nome': 'Bar / Pub',
        'emoji': '🍺',
        'preco': 129.90,
        'preco_fmt': 'R$ 129,90',
        'descricao': 'Para estabelecimentos',
        'destaque': True,
        'features': ['Telas ilimitadas', 'Todos os temas de clips', 'Jukebox ativo (100% pra você)', 'QR Code de mesa', 'Parabéns e Dedicatórias', 'Painel de gestão'],
    },
    'premium': {
        'nome': 'Premium / Rede',
        'emoji': '🏟️',
        'preco': 249.90,
        'preco_fmt': 'R$ 249,90',
        'descricao': 'Para redes e múltiplos locais',
        'features': ['Tudo do Bar', 'Múltiplos locais', 'Painel unificado', 'Suporte prioritário', 'Relatório de pedidos'],
    },
}

TIPOS_PEDIDO = {
    'musica':           {'nome': 'Música aleatória',       'emoji': '🎵', 'preco': 2.00,  'cor': '#3b82f6'},
    'musica_especifica':{'nome': 'Buscar na biblioteca',   'emoji': '🎯', 'preco': 5.00,  'cor': '#06b6d4'},
    'musica_externa':   {'nome': 'Buscar no YouTube',      'emoji': '🌐', 'preco': 20.00, 'cor': '#dc2626'},
    'flash':            {'nome': 'Prioridade na fila',     'emoji': '⚡', 'preco': 5.00,  'cor': '#f59e0b'},
    'vip':              {'nome': 'Tocar AGORA',            'emoji': '👑', 'preco': 10.00, 'cor': '#8b5cf6'},
    'parabens':         {'nome': 'Parabéns! 🎂',           'emoji': '🎂', 'preco': 15.00, 'cor': '#ec4899'},
    'dedicatoria':      {'nome': 'Dedicatória ❤️',        'emoji': '💌', 'preco': 10.00, 'cor': '#ef4444'},
    'brinde':           {'nome': 'Brinde Geral! 🍻',       'emoji': '🍻', 'preco': 5.00,  'cor': '#22c55e'},
    'chegada':          {'nome': 'Chegamos! 🎉',           'emoji': '🎉', 'preco': 5.00,  'cor': '#f97316'},
    'casamento':        {'nome': 'Pedido de Casamento 💍', 'emoji': '💍', 'preco': 25.00, 'cor': '#a855f7'},
}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _gerar_code(n=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))


# ── PIX Copia-e-Cola (EMV padrão Banco Central) ────────────────────────────────

def _crc16(data: str) -> int:
    """CRC16-CCITT — exigido pelo padrão PIX EMV."""
    crc = 0xFFFF
    for byte in data.encode('utf-8'):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def _formatar_chave_pix(chave: str, tipo: str) -> str:
    """Normaliza a chave PIX conforme tipo para o campo EMV."""
    chave = chave.strip()
    if tipo == 'telefone':
        digits = re.sub(r'\D', '', chave)
        if len(digits) == 11:          # DDD + 9 dígitos
            return f'+55{digits}'
        if len(digits) == 13 and digits.startswith('55'):
            return f'+{digits}'
        return f'+55{digits}'
    if tipo in ('cpf', 'cnpj'):
        return re.sub(r'\D', '', chave)
    return chave.lower()               # email / evp


def _pix_emv(chave_raw: str, tipo: str, nome_recebedor: str, valor: float, txid: str) -> str:
    """Gera payload EMV Pix Copia-e-Cola — padrão BCB."""
    import unicodedata

    def tlv(tag, value):
        return f'{tag}{len(value):02d}{value}'

    chave = _formatar_chave_pix(chave_raw, tipo)

    # Tag 26 — Merchant Account Info
    mai = tlv('26', tlv('0014', 'br.gov.bcb.pix') + tlv('01', chave))

    # Tag 62 — Additional Data (referência da transação, max 25 chars alfanumérico)
    ref = re.sub(r'[^A-Za-z0-9]', '', txid)[:25] or 'PUBSHOW'
    adf = tlv('62', tlv('05', ref))

    # Nome: sem acento, uppercase, max 25
    nome_ascii = unicodedata.normalize('NFD', nome_recebedor or 'PUBSHOW JUKEBOX')
    nome_ascii = ''.join(c for c in nome_ascii if unicodedata.category(c) != 'Mn')
    nome_ascii = re.sub(r'[^A-Za-z0-9 ]', '', nome_ascii).upper()[:25].strip()

    payload = (
        '000201'                          # Payload Format Indicator
        '010212'                          # Point of Initiation = 12 (QR único)
        + mai                             # Merchant Account Info
        + '52040000'                      # Merchant Category Code
        + '5303986'                       # Currency BRL
        + tlv('54', f'{valor:.2f}')       # Transaction Amount
        + '5802BR'                        # Country Code
        + tlv('59', nome_ascii)           # Merchant Name
        + tlv('60', 'SAO PAULO')          # Merchant City
        + adf                             # Additional Data
        + '6304'                          # CRC placeholder
    )
    return payload + f'{_crc16(payload):04X}'


def _pix_qr_b64(payload: str) -> str:
    """Gera QR code do payload PIX como PNG base64."""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8, border=2
        )
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        log.error('[PUBSHOW] Erro QR PIX: %s', e)
        return ''

def _gerar_jukebox_token():
    """Token rotativo para o QR do Jukebox — independente do code da TV."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))


def _jukebox_aberto(b):
    """Verifica se o jukebox está aberto — plano/trial, horário, status."""
    if b['suspenso']:
        return False, 'Estabelecimento temporariamente indisponível.'

    # ── Verifica plano ativo ou trial vigente ─────────────────────────────────
    plano_ok = bool(b['plano_ativo'])
    if not plano_ok:
        trial_ends = (b['trial_ends'] or '')[:19]  # 'YYYY-MM-DDTHH:MM:SS'
        if trial_ends and trial_ends >= datetime.now().isoformat()[:19]:
            plano_ok = True   # ainda no trial
    if not plano_ok:
        return False, 'Jukebox indisponível. Fale com o responsável do estabelecimento.'

    if not b['jukebox_ativo']:
        return False, 'Jukebox desativado pelo estabelecimento.'

    # ── Verifica horário de funcionamento ─────────────────────────────────────
    hora_ini = b['jukebox_hora_ini'] or '00:00'
    hora_fim = b['jukebox_hora_fim'] or '23:59'
    if hora_ini != '00:00' or hora_fim != '23:59':
        agora = datetime.now().strftime('%H:%M')
        if hora_ini <= hora_fim:
            if not (hora_ini <= agora <= hora_fim):
                return False, f'Jukebox disponível das {hora_ini} às {hora_fim}.'
        else:  # passa da meia-noite
            if not (agora >= hora_ini or agora <= hora_fim):
                return False, f'Jukebox disponível das {hora_ini} às {hora_fim}.'
    return True, ''


def _tipos_disponiveis(b):
    """Retorna TIPOS_PEDIDO filtrado pelo que o bar não bloqueou."""
    import json
    try:
        bloqueados = json.loads(b['tipos_bloqueados'] or '[]')
    except Exception:
        bloqueados = []
    return {k: v for k, v in TIPOS_PEDIDO.items() if k not in bloqueados}


def _precos_do_bar(b):
    """Retorna preços customizados ou padrão."""
    import json
    try:
        custom = json.loads(b['precos_custom'] or '{}')
    except Exception:
        custom = {}
    tipos = {}
    for k, v in TIPOS_PEDIDO.items():
        t = dict(v)
        if k in custom:
            t['preco'] = float(custom[k])
        tipos[k] = t
    return tipos


def _limite_atingido(b, ip):
    """Verifica se o IP já atingiu o limite de pedidos na última hora."""
    limite = b['limite_pedidos_hora'] or 10
    if limite <= 0:
        return False
    conn = get_pubshow_db()
    count = conn.execute(
        '''SELECT COUNT(*) FROM pubshow_pedidos
           WHERE business_id=? AND ip_cliente=?
           AND created_at >= datetime("now", "-1 hour", "localtime")''',
        (b['id'], ip)
    ).fetchone()[0]
    conn.close()
    return count >= limite

def _admin_ok():
    """Verifica se a sessão atual é admin — aceita login PUBSHOW ou SaaS master."""
    return session.get('pubshow_admin') is True or session.get('saas_admin') is True

def _admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _admin_ok():
            return redirect('/pubshow/admin/login')
        return f(*args, **kwargs)
    return decorated


def _get_business():
    bid = session.get('pub_business_id')
    if not bid:
        return None
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE id=?', (bid,)).fetchone()
    conn.close()
    return b


def pubshow_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('pub_business_id'):
            return redirect('/pubshow/entrar')
        return f(*args, **kwargs)
    return decorated


def _asaas_headers():
    return {'access_token': os.environ.get('ASAAS_API_KEY', ''), 'Content-Type': 'application/json'}


def _asaas_req(method, endpoint, data=None):
    try:
        r = _requests.request(method, f'{_ASAAS_BASE}{endpoint}',
                              headers=_asaas_headers(), json=data, timeout=15)
        return r.json()
    except Exception as e:
        return {'error': str(e)}


def _asaas_criar_ou_buscar_cliente(b) -> str:
    if b['asaas_customer_id']:
        return b['asaas_customer_id']
    cpf = re.sub(r'\D', '', b['cpf_cnpj'] or '')
    busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf}')
    if busca.get('data'):
        cid = busca['data'][0]['id']
    else:
        resp = _asaas_req('POST', '/customers', {
            'name': b['nome'], 'email': b['email'],
            'mobilePhone': re.sub(r'\D', '', b['telefone'] or ''),
            'cpfCnpj': cpf,
        })
        cid = resp.get('id')
    if cid:
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET asaas_customer_id=? WHERE id=?', (cid, b['id']))
        conn.commit(); conn.close()
    return cid


def _asaas_criar_assinatura(customer_id, plano, billing_type, business_id):
    p = PLANOS[plano]
    import datetime as _dt
    venc = (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d')
    return _asaas_req('POST', '/subscriptions', {
        'customer': customer_id,
        'billingType': billing_type,
        'value': p['preco'],
        'nextDueDate': venc,
        'cycle': 'MONTHLY',
        'description': f'PUBSHOW — {p["nome"]}',
        'externalReference': f'pubshow_{business_id}_{plano}',
    })


def _asaas_criar_cobranca_pix_jukebox(b, pedido_id: int, valor: float, descricao: str):
    """Cria cobrança PIX avulsa no Asaas para pedido de Jukebox.

    Retorna dict com {payment_id, qr_b64, payload, expiracao}
    ou None em caso de erro (usar fallback manual).
    """
    if not os.environ.get('ASAAS_API_KEY'):
        return None   # Asaas não configurado — usar PIX manual

    # Garante que o bar tem cliente no Asaas
    customer_id = _asaas_criar_ou_buscar_cliente(b)
    if not customer_id:
        log.warning('[PUBSHOW] Asaas: sem customer para bar %s', b['id'])
        return None

    import datetime as _dt
    venc = (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d')

    # 1) Cria cobrança PIX avulsa
    resp = _asaas_req('POST', '/payments', {
        'customer':          customer_id,
        'billingType':       'PIX',
        'value':             round(valor, 2),
        'dueDate':           venc,
        'description':       f'Jukebox — {descricao} — {b["nome"]}',
        'externalReference': f'jukebox_{pedido_id}',
        'postalService':     False,
    })

    payment_id = resp.get('id')
    if not payment_id:
        log.error('[PUBSHOW] Asaas criar cobrança falhou: %s', resp)
        return None

    # 2) Busca QR code PIX
    qr_resp = _asaas_req('GET', f'/payments/{payment_id}/pixQrCode')
    encoded  = qr_resp.get('encodedImage', '')
    payload  = qr_resp.get('payload', '')
    expira   = qr_resp.get('expirationDate', '')

    if not payload:
        log.error('[PUBSHOW] Asaas QR vazio para payment %s', payment_id)
        return None

    return {
        'payment_id': payment_id,
        'qr_b64':     encoded,
        'payload':    payload,
        'expiracao':  expira,
    }


def _videos_do_canal(canal_key, limit=500):
    import random
    cat = CANAIS.get(canal_key, {}).get('cat', canal_key)
    conn = get_pubshow_db()

    if isinstance(cat, list):
        # Multi-categoria (ex: sport_mix) — busca de todas e embaralha
        placeholders = ','.join('?' * len(cat))
        videos = conn.execute(
            f'SELECT * FROM pubshow_videos WHERE categoria IN ({placeholders}) AND ativo=1',
            cat
        ).fetchall()
        videos = [dict(v) for v in videos]
        random.shuffle(videos)
        videos = videos[:limit]
    else:
        videos = conn.execute(
            'SELECT * FROM pubshow_videos WHERE categoria=? AND ativo=1 ORDER BY ordem, views_milhoes DESC LIMIT ?',
            (cat, limit)
        ).fetchall()
        videos = [dict(v) for v in videos]

    conn.close()
    return videos


# ── ROTAS PÚBLICAS ─────────────────────────────────────────────────────────────

@pubshow_bp.route('/')
def index():
    conn = get_pubshow_db()
    total_videos = conn.execute('SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1').fetchone()[0]
    total_canais  = len(CANAIS)
    conn.close()
    return render_template('pubshow/landing.html',
                           planos=PLANOS, canais=CANAIS,
                           total_videos=total_videos, total_canais=total_canais)


@pubshow_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if session.get('pub_business_id'):
        return redirect('/pubshow/painel')
    erro = ''
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        senha    = request.form.get('senha', '')
        conn     = get_pubshow_db()
        b = conn.execute('SELECT * FROM pubshow_businesses WHERE email=?', (email,)).fetchone()
        conn.close()
        if b and check_password_hash(b['password_hash'], senha):
            session['pub_business_id']   = b['id']
            session['pub_business_nome'] = b['nome']
            session['pub_canal']         = b['canal_atual']
            conn2 = get_pubshow_db()
            conn2.execute("UPDATE pubshow_businesses SET ultimo_acesso=datetime('now','localtime') WHERE id=?", (b['id'],))
            conn2.commit(); conn2.close()
            return redirect('/pubshow/painel')
        erro = 'E-mail ou senha incorretos.'
    return render_template('pubshow/entrar.html', erro=erro)


@pubshow_bp.route('/sair')
def sair():
    session.pop('pub_business_id', None)
    session.pop('pub_business_nome', None)
    session.pop('pub_canal', None)
    return redirect('/pubshow/entrar')


@pubshow_bp.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if session.get('pub_business_id'):
        return redirect('/pubshow/painel')
    erro = ''
    plano_sel = request.args.get('plano', 'bar')
    if request.method == 'POST':
        nome     = request.form.get('nome', '').strip()
        tipo     = request.form.get('tipo', 'bar')
        email    = request.form.get('email', '').strip().lower()
        telefone = request.form.get('telefone', '').strip()
        cpf_cnpj = request.form.get('cpf_cnpj', '').strip()
        senha    = request.form.get('senha', '')
        plano_sel= request.form.get('plano', 'bar')
        if not all([nome, email, telefone, senha, cpf_cnpj]):
            erro = 'Preencha todos os campos obrigatórios.'
        elif len(senha) < 6:
            erro = 'A senha deve ter ao menos 6 caracteres.'
        else:
            try:
                code  = _gerar_code()
                jtoken = _gerar_jukebox_token()
                trial = (datetime.now() + timedelta(days=7)).isoformat()
                conn = get_pubshow_db()
                conn.execute(
                    '''INSERT INTO pubshow_businesses
                       (nome, tipo, email, telefone, cpf_cnpj, password_hash, code,
                        plano, plano_ativo, canal_atual, trial_ends, jukebox_token)
                       VALUES (?,?,?,?,?,?,?,?,1,?,?,?)''',
                    (nome, tipo, email, telefone, cpf_cnpj,
                     generate_password_hash(senha), code, plano_sel, 'rock', trial, jtoken)
                )
                conn.commit()
                b = conn.execute('SELECT * FROM pubshow_businesses WHERE email=?', (email,)).fetchone()
                conn.close()
                session['pub_business_id']   = b['id']
                session['pub_business_nome'] = b['nome']
                session['pub_canal']         = b['canal_atual']
                return redirect('/pubshow/painel')
            except Exception as ex:
                if 'UNIQUE' in str(ex):
                    erro = 'Este e-mail já está cadastrado. Faça login.'
                else:
                    erro = f'Erro ao cadastrar: {ex}'
    return render_template('pubshow/cadastro.html', erro=erro,
                           planos=PLANOS, plano_sel=plano_sel,
                           tipos=TIPOS_ESTABELECIMENTO)


# ── TV PLAYER (roda na tela do bar) ───────────────────────────────────────────

@pubshow_bp.route('/tv/<code>')
def tv(code):
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE code=?', (code,)).fetchone()
    conn.close()
    if not b:
        return 'Estabelecimento não encontrado', 404
    canal_key = b['canal_atual'] or 'rock'
    canal     = CANAIS.get(canal_key, CANAIS['rock'])
    videos    = _videos_do_canal(canal_key)
    return render_template('pubshow/tv.html', b=dict(b), canal=canal,
                           canal_key=canal_key, videos=videos, canais=CANAIS)


# ── JUKEBOX MOBILE (cliente do bar escaneia QR) ───────────────────────────────

@pubshow_bp.route('/jukebox/<token>', methods=['GET', 'POST'])
def jukebox(token):
    conn = get_pubshow_db()
    # Busca por jukebox_token primeiro; fallback para code (retrocompatibilidade)
    b = conn.execute(
        'SELECT * FROM pubshow_businesses WHERE jukebox_token=?', (token,)
    ).fetchone()
    if not b:
        b = conn.execute(
            'SELECT * FROM pubshow_businesses WHERE code=?', (token,)
        ).fetchone()
    conn.close()
    if not b:
        return render_template('pubshow/qr_invalido.html'), 404
    if b['suspenso']:
        return render_template('pubshow/qr_invalido.html'), 403

    sucesso      = None
    erro         = ''
    pix_pendente = None
    ip_cliente   = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()

    # Aviso temporário
    aviso = None
    if b['aviso_jukebox']:
        expira = b['aviso_expira'] or ''
        if not expira or expira > datetime.now().isoformat():
            aviso = b['aviso_jukebox']

    # Verifica horário e status
    aberto, motivo_fechado = _jukebox_aberto(b)
    tipos_bar  = _tipos_disponiveis(b)
    precos_bar = _precos_do_bar(b)

    if request.method == 'POST':
        if not aberto:
            erro = motivo_fechado
        elif _limite_atingido(b, ip_cliente):
            erro = 'Muitos pedidos em pouco tempo. Aguarde um momento.'
        else:
            tipo          = request.form.get('tipo', '')
            nome_cliente  = request.form.get('nome_cliente', '').strip()
            mensagem      = request.form.get('mensagem', '').strip()
            categoria     = request.form.get('categoria', b['canal_atual'])
            youtube_id    = request.form.get('youtube_id', '').strip()[:20]
            titulo_pedido = request.form.get('titulo_pedido', '').strip()[:80]
            thumb_url     = request.form.get('thumb_url', '').strip()[:200]

            if tipo not in tipos_bar:
                erro = 'Tipo de pedido inválido.'
            elif not nome_cliente:
                erro = 'Informe seu nome.'
            elif tipo in ('musica_especifica', 'musica_externa') and not youtube_id:
                erro = 'Selecione uma música antes de confirmar.'
            else:
                preco = precos_bar[tipo]['preco']
                usar_pix = bool(b['requer_pix']) and bool(b['pix_key'])

                if usar_pix:
                    # ── Cria pedido como aguardando_pix (sem asaas_payment_id ainda) ─
                    txid = f'P{b["id"]}T{int(datetime.now().timestamp())}'
                    conn2 = get_pubshow_db()
                    cur = conn2.execute(
                        '''INSERT INTO pubshow_pedidos
                           (business_id, tipo, nome_cliente, mensagem, categoria, status, valor,
                            youtube_id, titulo_pedido, thumb_url, ip_cliente, pix_txid)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (b['id'], tipo, nome_cliente, mensagem, categoria,
                         'aguardando_pix', preco,
                         youtube_id or None, titulo_pedido or None, thumb_url or None,
                         ip_cliente, txid)
                    )
                    pedido_id = cur.lastrowid
                    conn2.commit(); conn2.close()

                    # ── Tenta Asaas primeiro (confirmação automática) ──────────
                    descricao_pedido = precos_bar[tipo]['nome']
                    if titulo_pedido:
                        descricao_pedido += f' — {titulo_pedido[:40]}'
                    asaas_data = _asaas_criar_cobranca_pix_jukebox(b, pedido_id, preco, descricao_pedido)

                    if asaas_data:
                        # Asaas gerou QR — salva payment_id e usa payload do Asaas
                        conn3 = get_pubshow_db()
                        conn3.execute(
                            'UPDATE pubshow_pedidos SET asaas_payment_id=?, pix_payload=? WHERE id=?',
                            (asaas_data['payment_id'], asaas_data['payload'], pedido_id)
                        )
                        conn3.commit(); conn3.close()
                        pix_qr    = asaas_data['qr_b64']
                        pix_payload = asaas_data['payload']
                    else:
                        # Fallback: EMV manual (confirmar no painel)
                        pix_payload = _pix_emv(
                            b['pix_key'], b['pix_tipo'] or 'telefone',
                            b['pix_nome_recebedor'] or b['nome'],
                            preco, txid
                        )
                        pix_qr = _pix_qr_b64(pix_payload)
                        conn3 = get_pubshow_db()
                        conn3.execute(
                            'UPDATE pubshow_pedidos SET pix_payload=? WHERE id=?',
                            (pix_payload, pedido_id)
                        )
                        conn3.commit(); conn3.close()

                    pix_pendente = {
                        'pedido_id':  pedido_id,
                        'payload':    pix_payload,
                        'qr_b64':     pix_qr,
                        'valor':      preco,
                        'recebedor':  b['pix_nome_recebedor'] or b['nome'],
                        'tipo_nome':  precos_bar[tipo]['nome'],
                        'tipo_emoji': precos_bar[tipo]['emoji'],
                        'via_asaas':  bool(asaas_data),
                    }
                    sucesso = None
                else:
                    # ── Fluxo direto (sem PIX ou PIX não exigido) ─────────────
                    conn2 = get_pubshow_db()
                    conn2.execute(
                        '''INSERT INTO pubshow_pedidos
                           (business_id, tipo, nome_cliente, mensagem, categoria, status, valor,
                            youtube_id, titulo_pedido, thumb_url, ip_cliente)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        (b['id'], tipo, nome_cliente, mensagem, categoria, 'pendente', preco,
                         youtube_id or None, titulo_pedido or None, thumb_url or None, ip_cliente)
                    )
                    conn2.commit(); conn2.close()
                    sucesso = tipo

    # Fila atual (últimos 5 pedidos pendentes)
    conn3 = get_pubshow_db()
    fila = conn3.execute(
        '''SELECT * FROM pubshow_pedidos
           WHERE business_id=? AND status="pendente"
           ORDER BY created_at ASC LIMIT 10''',
        (b['id'],)
    ).fetchall()
    canal        = CANAIS.get(b['canal_atual'], CANAIS['rock'])
    total_videos = conn3.execute('SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1').fetchone()[0]
    conn3.close()

    return render_template('pubshow/jukebox.html',
                           b=dict(b), canal=canal,
                           tipos=precos_bar,
                           tipos_disponiveis=tipos_bar,
                           fila=[dict(f) for f in fila],
                           sucesso=sucesso, erro=erro,
                           pix_pendente=pix_pendente,
                           total_videos=total_videos,
                           aberto=aberto, motivo_fechado=motivo_fechado,
                           aviso=aviso)


# ── API (usada pelo TV player via JS polling) ─────────────────────────────────

@pubshow_bp.route('/api/status/<code>')
def api_status(code):
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE code=?', (code,)).fetchone()
    if not b:
        conn.close()
        return jsonify({'error': 'not_found'}), 404

    # Pedido especial — aparece como overlay na TV (parabéns, dedicatória etc.)
    # Só conta pedidos confirmados (pendente), nunca aguardando_pix
    pedido_especial = conn.execute(
        '''SELECT * FROM pubshow_pedidos
           WHERE business_id=? AND status="pendente"
           AND tipo IN ("parabens","dedicatoria","brinde","chegada","casamento")
           ORDER BY created_at ASC LIMIT 1''',
        (b['id'],)
    ).fetchone()

    # Pedido de música — espera o vídeo atual acabar
    # VIP → toca na frente de tudo; flash → prioridade; restantes → ordem de chegada
    pedido_musica = conn.execute(
        '''SELECT * FROM pubshow_pedidos
           WHERE business_id=? AND status="pendente"
           AND tipo IN ("vip","flash","musica","musica_especifica","musica_externa")
           ORDER BY
             CASE tipo WHEN "vip" THEN 1 WHEN "flash" THEN 2 ELSE 3 END,
             created_at ASC
           LIMIT 1''',
        (b['id'],)
    ).fetchone()

    # Contagem da fila: só pedidos confirmados (exclui aguardando_pix)
    total_fila = conn.execute(
        'SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status="pendente"',
        (b['id'],)
    ).fetchone()[0]

    # Pedidos aguardando PIX: para mostrar badge no painel do bar
    aguardando_pix = conn.execute(
        'SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status="aguardando_pix"',
        (b['id'],)
    ).fetchone()[0]

    result = {
        'canal_atual':    b['canal_atual'],
        'jukebox_ativo':  bool(b['jukebox_ativo']),
        'pedido':         dict(pedido_especial) if pedido_especial else None,
        'pedido_musica':  dict(pedido_musica)   if pedido_musica   else None,
        'total_fila':     total_fila,
        'aguardando_pix': aguardando_pix,
    }
    conn.close()
    return jsonify(result)


@pubshow_bp.route('/api/pedido-status/<int:pedido_id>')
def api_pedido_status(pedido_id):
    """Polling do cliente: retorna status atual do pedido."""
    conn = get_pubshow_db()
    row = conn.execute(
        'SELECT status FROM pubshow_pedidos WHERE id=?', (pedido_id,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'not_found'}), 404
    return jsonify({'status': row['status']})


@pubshow_bp.route('/api/pedido-exibido/<int:pedido_id>', methods=['POST'])
def api_pedido_exibido(pedido_id):
    """Marca pedido como exibido. Valida que a TV (code) é dona do pedido."""
    data = request.get_json(silent=True) or {}
    code = data.get('code') or request.form.get('code', '')
    conn = get_pubshow_db()
    pedido = conn.execute(
        'SELECT id, business_id FROM pubshow_pedidos WHERE id=?', (pedido_id,)
    ).fetchone()
    if not pedido:
        conn.close()
        return jsonify({'error': 'not_found'}), 404
    if code:  # se code foi enviado, verifica ownership
        b = conn.execute(
            'SELECT id FROM pubshow_businesses WHERE code=?', (code,)
        ).fetchone()
        if not b or b['id'] != pedido['business_id']:
            conn.close()
            return jsonify({'error': 'unauthorized'}), 403
    conn.execute(
        "UPDATE pubshow_pedidos SET status='exibido', exibido_at=datetime('now','localtime') WHERE id=?",
        (pedido_id,)
    )
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@pubshow_bp.route('/api/trocar-canal/<code>', methods=['POST'])
def api_trocar_canal(code):
    canal = request.json.get('canal', 'rock') if request.is_json else request.form.get('canal', 'rock')
    if canal not in CANAIS:
        return jsonify({'error': 'canal inválido'}), 400
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET canal_atual=? WHERE code=?', (canal, code))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'canal': canal})


@pubshow_bp.route('/api/videos/<categoria>')
def api_videos(categoria):
    videos = _videos_do_canal(categoria)
    return jsonify({'videos': videos, 'total': len(videos)})


@pubshow_bp.route('/api/buscar-biblioteca')
def api_buscar_biblioteca():
    """Busca na biblioteca curada de vídeos (rápido, sem API externa)."""
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'resultados': []})
    conn = get_pubshow_db()
    like = f'%{q}%'
    rows = conn.execute(
        '''SELECT youtube_id, titulo, artista, categoria, duracao_seg
           FROM pubshow_videos
           WHERE ativo=1 AND (titulo LIKE ? OR artista LIKE ?)
           ORDER BY views_milhoes DESC LIMIT 12''',
        (like, like)
    ).fetchall()
    conn.close()
    return jsonify({'resultados': [dict(r) for r in rows]})


@pubshow_bp.route('/api/buscar')
def api_buscar():
    """Busca músicas no YouTube via InnerTube API (sem chave)."""
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'resultados': []})
    try:
        resp = _requests.post(
            'https://www.youtube.com/youtubei/v1/search'
            '?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8',
            json={
                'query': q,
                'context': {
                    'client': {
                        'clientName': 'WEB',
                        'clientVersion': '2.20231121.08.00',
                        'hl': 'pt',
                        'gl': 'BR',
                    }
                }
            },
            headers={'Content-Type': 'application/json'},
            timeout=8
        )
        data = resp.json()
        resultados = []
        sections = (data.get('contents', {})
                    .get('twoColumnSearchResultsRenderer', {})
                    .get('primaryContents', {})
                    .get('sectionListRenderer', {})
                    .get('contents', []))
        for sec in sections:
            for item in sec.get('itemSectionRenderer', {}).get('contents', []):
                vr = item.get('videoRenderer', {})
                if not vr:
                    continue
                vid_id = vr.get('videoId', '')
                titulo = ''.join(r.get('text', '') for r in vr.get('title', {}).get('runs', []))
                canal  = (vr.get('longBylineText', {}).get('runs') or [{}])[0].get('text', '')
                duracao= vr.get('lengthText', {}).get('simpleText', '')
                if vid_id and titulo:
                    resultados.append({
                        'id':      vid_id,
                        'titulo':  titulo[:70],
                        'canal':   canal[:40],
                        'duracao': duracao,
                        'thumb':   f'https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg',
                    })
                if len(resultados) >= 8:
                    break
            if len(resultados) >= 8:
                break
        return jsonify({'resultados': resultados})
    except Exception as e:
        log.error('YouTube search error: %s', e)
        return jsonify({'resultados': []})


# ── PAINEL DO BAR ─────────────────────────────────────────────────────────────

@pubshow_bp.route('/painel')
@pubshow_login_required
def painel():
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')
    conn = get_pubshow_db()
    pedidos_hoje = conn.execute(
        '''SELECT * FROM pubshow_pedidos WHERE business_id=?
           AND date(created_at)=date("now","localtime")
           ORDER BY created_at DESC''',
        (b['id'],)
    ).fetchall()
    total_hoje = sum(float(p['valor'] or 0) for p in pedidos_hoje)
    fila = conn.execute(
        '''SELECT * FROM pubshow_pedidos WHERE business_id=? AND status="pendente"
           ORDER BY created_at ASC LIMIT 20''',
        (b['id'],)
    ).fetchall()
    aguardando_pix = conn.execute(
        '''SELECT * FROM pubshow_pedidos WHERE business_id=? AND status="aguardando_pix"
           ORDER BY created_at DESC LIMIT 20''',
        (b['id'],)
    ).fetchall()
    conn.close()
    import json as _json
    bd = dict(b)
    try:    bloqueados_parsed = _json.loads(bd.get('tipos_bloqueados') or '[]')
    except: bloqueados_parsed = []
    try:    precos_parsed = _json.loads(bd.get('precos_custom') or '{}')
    except: precos_parsed = {}
    return render_template('pubshow/painel.html',
                           b=bd, canais=CANAIS,
                           pedidos_hoje=[dict(p) for p in pedidos_hoje],
                           total_hoje=total_hoje,
                           fila=[dict(f) for f in fila],
                           aguardando_pix=[dict(p) for p in aguardando_pix],
                           tipos=TIPOS_PEDIDO,
                           planos=PLANOS,
                           bloqueados_parsed=bloqueados_parsed,
                           precos_parsed=precos_parsed)


@pubshow_bp.route('/painel/fila-json')
@pubshow_login_required
def painel_fila_json():
    """Retorna fila + aguardando_pix como JSON — usado pelo polling do painel sem recarregar a página."""
    b = _get_business()
    conn = get_pubshow_db()
    fila = conn.execute(
        '''SELECT id, nome_cliente, tipo, mensagem, valor, created_at
           FROM pubshow_pedidos WHERE business_id=? AND status="pendente"
           ORDER BY created_at ASC LIMIT 20''',
        (b['id'],)
    ).fetchall()
    aguardando = conn.execute(
        '''SELECT id, nome_cliente, tipo, mensagem, valor, created_at
           FROM pubshow_pedidos WHERE business_id=? AND status="aguardando_pix"
           ORDER BY created_at DESC LIMIT 20''',
        (b['id'],)
    ).fetchall()
    pedidos_hoje = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE business_id=? AND status!="aguardando_pix"
           AND date(created_at)=date("now","localtime")''',
        (b['id'],)
    ).fetchone()[0]
    conn.close()
    return jsonify({
        'fila': [dict(r) for r in fila],
        'aguardando_pix': [dict(r) for r in aguardando],
        'total_hoje': float(pedidos_hoje),
        'fila_count': len(fila),
    })


@pubshow_bp.route('/painel/canal', methods=['POST'])
@pubshow_login_required
def painel_canal():
    b    = _get_business()
    canal = request.form.get('canal', 'rock')
    if canal in CANAIS:
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET canal_atual=? WHERE id=?', (canal, b['id']))
        conn.commit(); conn.close()
        session['pub_canal'] = canal
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/pix', methods=['POST'])
@pubshow_login_required
def painel_pix():
    b = _get_business()
    pix_key  = request.form.get('pix_key', '').strip()
    pix_tipo = request.form.get('pix_tipo', 'telefone')
    pix_nome = request.form.get('pix_nome', '').strip()
    conn = get_pubshow_db()
    conn.execute(
        'UPDATE pubshow_businesses SET pix_key=?, pix_tipo=?, pix_nome_recebedor=? WHERE id=?',
        (pix_key, pix_tipo, pix_nome, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/qrcode')
@pubshow_login_required
def painel_qrcode():
    """Página de impressão de QR Codes para as mesas."""
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')

    # URL pública do Jukebox — sempre www para evitar erro de certificado SSL
    token = b['jukebox_token'] or b['code']
    jukebox_url = f"https://www.4kitem.com.br/pubshow/jukebox/{token}"

    # Gera QR como PNG base64
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=2,
        )
        qr.add_data(jukebox_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as ex:
        log.error('QR error: %s', ex)
        qr_b64 = None

    return render_template('pubshow/qrcode.html',
                           b=dict(b),
                           jukebox_url=jukebox_url,
                           qr_b64=qr_b64)


@pubshow_bp.route('/painel/pedido/<int:pid>/dispensar', methods=['POST'])
@pubshow_login_required
def painel_dispensar_pedido(pid):
    b = _get_business()
    conn = get_pubshow_db()
    conn.execute(
        "UPDATE pubshow_pedidos SET status='dispensado' WHERE id=? AND business_id=?",
        (pid, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=fila')


@pubshow_bp.route('/painel/pedido/<int:pid>/confirmar-pix', methods=['POST'])
@pubshow_login_required
def painel_confirmar_pix(pid):
    """Bar confirma que o PIX foi recebido → pedido entra na fila."""
    b = _get_business()
    conn = get_pubshow_db()
    conn.execute(
        """UPDATE pubshow_pedidos SET status='pendente'
           WHERE id=? AND business_id=? AND status='aguardando_pix'""",
        (pid, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=fila')


@pubshow_bp.route('/painel/pedido/<int:pid>/recusar-pix', methods=['POST'])
@pubshow_login_required
def painel_recusar_pix(pid):
    """Bar recusa o pedido PIX (não recebeu pagamento)."""
    b = _get_business()
    conn = get_pubshow_db()
    conn.execute(
        """UPDATE pubshow_pedidos SET status='dispensado'
           WHERE id=? AND business_id=? AND status='aguardando_pix'""",
        (pid, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=fila')


@pubshow_bp.route('/painel/toggle-pix', methods=['POST'])
@pubshow_login_required
def painel_toggle_pix():
    """Ativa/desativa a exigência de PIX — atualiza APENAS requer_pix, sem tocar em outras configs."""
    b = _get_business()
    requer_pix = 1 if request.form.get('requer_pix') else 0
    conn = get_pubshow_db()
    conn.execute(
        'UPDATE pubshow_businesses SET requer_pix=? WHERE id=?',
        (requer_pix, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/config', methods=['POST'])
@pubshow_login_required
def painel_config():
    """Salva todas as configurações do Jukebox do bar."""
    import json
    b = _get_business()

    # Horário
    hora_ini = request.form.get('hora_ini', '00:00').strip()
    hora_fim = request.form.get('hora_fim', '23:59').strip()

    # Jukebox on/off
    jukebox_ativo = 1 if request.form.get('jukebox_ativo') else 0

    # Mensagem de boas-vindas
    mensagem = request.form.get('mensagem_jukebox', '').strip()[:120]

    # Aviso temporário
    aviso      = request.form.get('aviso_jukebox', '').strip()[:120]
    aviso_horas= request.form.get('aviso_horas', '4')
    aviso_expira = None
    if aviso:
        try:
            horas = max(1, min(72, int(aviso_horas)))
            aviso_expira = (datetime.now() + timedelta(hours=horas)).isoformat()
        except Exception:
            aviso_expira = None

    # Limite anti-spam
    try:
        limite = max(1, min(50, int(request.form.get('limite_pedidos_hora', 10))))
    except Exception:
        limite = 10

    # Tipos bloqueados
    todos_tipos = list(TIPOS_PEDIDO.keys())
    ativos      = request.form.getlist('tipos_ativos')
    bloqueados  = [t for t in todos_tipos if t not in ativos]

    # Preços custom
    precos = {}
    for k in todos_tipos:
        val = request.form.get(f'preco_{k}', '').strip()
        if val:
            try:
                precos[k] = round(float(val.replace(',', '.')), 2)
            except Exception:
                pass

    requer_pix = 1 if request.form.get('requer_pix') else 0

    conn = get_pubshow_db()
    conn.execute(
        '''UPDATE pubshow_businesses SET
           jukebox_ativo=?, jukebox_hora_ini=?, jukebox_hora_fim=?,
           mensagem_jukebox=?, aviso_jukebox=?, aviso_expira=?,
           limite_pedidos_hora=?, tipos_bloqueados=?, precos_custom=?,
           requer_pix=?
           WHERE id=?''',
        (jukebox_ativo, hora_ini, hora_fim,
         mensagem or None, aviso or None, aviso_expira,
         limite, json.dumps(bloqueados), json.dumps(precos),
         requer_pix, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/senha', methods=['POST'])
@pubshow_login_required
def painel_senha():
    b = _get_business()
    atual  = request.form.get('senha_atual', '')
    nova   = request.form.get('senha_nova', '')
    conf   = request.form.get('senha_conf', '')
    if not check_password_hash(b['password_hash'], atual):
        return redirect('/pubshow/painel?aba=conta&erro=senha_errada')
    if len(nova) < 6:
        return redirect('/pubshow/painel?aba=conta&erro=senha_curta')
    if nova != conf:
        return redirect('/pubshow/painel?aba=conta&erro=senha_diferente')
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET password_hash=? WHERE id=?',
                 (generate_password_hash(nova), b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=conta&ok=senha')


@pubshow_bp.route('/painel/relatorio')
@pubshow_login_required
def painel_relatorio():
    import json
    b = _get_business()
    conn = get_pubshow_db()

    # Receita por período (exclui pedidos aguardando PIX — ainda não pagos)
    receita_hoje = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE business_id=? AND status!="aguardando_pix"
           AND date(created_at)=date("now","localtime")''', (b['id'],)
    ).fetchone()[0]
    receita_semana = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now","-7 days","localtime")''', (b['id'],)
    ).fetchone()[0]
    receita_mes = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE business_id=? AND status!="aguardando_pix"
           AND strftime("%Y-%m",created_at)=strftime("%Y-%m","now","localtime")''', (b['id'],)
    ).fetchone()[0]
    receita_total = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE business_id=? AND status!="aguardando_pix"''', (b['id'],)
    ).fetchone()[0]

    # Top tipos pedidos (último mês — apenas pagos)
    top_tipos = conn.execute(
        '''SELECT tipo, COUNT(*) n, COALESCE(SUM(valor),0) receita
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now","-30 days","localtime")
           GROUP BY tipo ORDER BY n DESC''', (b['id'],)
    ).fetchall()

    # Músicas mais pedidas (último mês — apenas pagas)
    top_musicas = conn.execute(
        '''SELECT titulo_pedido, COUNT(*) n FROM pubshow_pedidos
           WHERE business_id=? AND titulo_pedido IS NOT NULL AND status!="aguardando_pix"
           AND created_at>=datetime("now","-30 days","localtime")
           GROUP BY titulo_pedido ORDER BY n DESC LIMIT 10''', (b['id'],)
    ).fetchall()

    # Pedidos por dia (últimos 14 dias — apenas pagos)
    por_dia = conn.execute(
        '''SELECT date(created_at,"localtime") dia, COUNT(*) n, COALESCE(SUM(valor),0) r
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now","-14 days","localtime")
           GROUP BY dia ORDER BY dia''', (b['id'],)
    ).fetchall()

    conn.close()
    return render_template('pubshow/relatorio.html',
                           b=dict(b),
                           receita_hoje=receita_hoje,
                           receita_semana=receita_semana,
                           receita_mes=receita_mes,
                           receita_total=receita_total,
                           top_tipos=[dict(t) for t in top_tipos],
                           top_musicas=[dict(m) for m in top_musicas],
                           por_dia=[dict(d) for d in por_dia],
                           tipos=TIPOS_PEDIDO)


@pubshow_bp.route('/painel/novo-qr', methods=['POST'])
@pubshow_login_required
def painel_novo_qr():
    """Gera novo jukebox_token — invalida QR codes antigos imediatamente."""
    b = _get_business()
    novo = _gerar_jukebox_token()
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET jukebox_token=? WHERE id=?', (novo, b['id']))
    conn.commit(); conn.close()
    log.info('[PUBSHOW] Novo QR gerado para business_id=%s', b['id'])
    return redirect('/pubshow/painel/qrcode')


# ── ADMIN MASTER ──────────────────────────────────────────────────────────────

@pubshow_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    erro = ''
    if request.method == 'POST':
        senha = request.form.get('senha', '')
        admin_pass = os.environ.get('PUBSHOW_ADMIN_PASS', '')
        if admin_pass and senha == admin_pass:
            session['pubshow_admin'] = True
            return redirect('/pubshow/admin')
        erro = 'Senha incorreta.'
    return render_template('pubshow/admin_login.html', erro=erro)


@pubshow_bp.route('/admin/logout')
def admin_logout():
    session.pop('pubshow_admin', None)
    return redirect('/pubshow/admin/login')


@pubshow_bp.route('/admin')
@_admin_required
def admin_dashboard():
    conn = get_pubshow_db()
    bars = conn.execute(
        '''SELECT b.*,
           (SELECT COUNT(*) FROM pubshow_pedidos p WHERE p.business_id=b.id) total_pedidos,
           (SELECT COALESCE(SUM(p.valor),0) FROM pubshow_pedidos p WHERE p.business_id=b.id AND p.status!="aguardando_pix") total_receita,
           (SELECT COUNT(*) FROM pubshow_pedidos p WHERE p.business_id=b.id AND p.status="pendente") fila_atual
           FROM pubshow_businesses b ORDER BY b.created_at DESC'''
    ).fetchall()
    total_videos = conn.execute('SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1').fetchone()[0]
    total_pedidos_hoje = conn.execute(
        '''SELECT COUNT(*) FROM pubshow_pedidos
           WHERE date(created_at)=date("now","localtime")'''
    ).fetchone()[0]
    receita_hoje = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE date(created_at)=date("now","localtime") AND status!="aguardando_pix"'''
    ).fetchone()[0]
    conn.close()
    return render_template('pubshow/admin.html',
                           bars=[dict(b) for b in bars],
                           total_videos=total_videos,
                           total_pedidos_hoje=total_pedidos_hoje,
                           receita_hoje=receita_hoje,
                           canais=CANAIS, planos=PLANOS,
                           now=datetime.now().isoformat())


@pubshow_bp.route('/admin/bar/<int:bid>')
@_admin_required
def admin_bar(bid):
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE id=?', (bid,)).fetchone()
    if not b:
        conn.close(); return redirect('/pubshow/admin')
    pedidos = conn.execute(
        '''SELECT * FROM pubshow_pedidos WHERE business_id=?
           ORDER BY created_at DESC LIMIT 50''', (bid,)
    ).fetchall()
    ass = conn.execute(
        'SELECT * FROM pubshow_assinaturas WHERE business_id=?', (bid,)
    ).fetchone()
    conn.close()
    return render_template('pubshow/admin_bar.html',
                           b=dict(b), pedidos=[dict(p) for p in pedidos],
                           ass=dict(ass) if ass else None,
                           canais=CANAIS, planos=PLANOS, tipos=TIPOS_PEDIDO)


@pubshow_bp.route('/admin/bar/<int:bid>/acao', methods=['POST'])
@_admin_required
def admin_bar_acao(bid):
    acao = request.form.get('acao', '')
    conn = get_pubshow_db()
    if acao == 'ativar_plano':
        plano = request.form.get('plano', 'bar')
        conn.execute('UPDATE pubshow_businesses SET plano=?, plano_ativo=1 WHERE id=?', (plano, bid))
    elif acao == 'desativar_plano':
        conn.execute('UPDATE pubshow_businesses SET plano_ativo=0 WHERE id=?', (bid,))
    elif acao == 'suspender':
        conn.execute('UPDATE pubshow_businesses SET suspenso=1 WHERE id=?', (bid,))
    elif acao == 'reativar':
        conn.execute('UPDATE pubshow_businesses SET suspenso=0 WHERE id=?', (bid,))
    elif acao == 'novo_qr':
        novo = _gerar_jukebox_token()
        conn.execute('UPDATE pubshow_businesses SET jukebox_token=? WHERE id=?', (novo, bid))
    elif acao == 'toggle_jukebox':
        atual = conn.execute('SELECT jukebox_ativo FROM pubshow_businesses WHERE id=?', (bid,)).fetchone()[0]
        conn.execute('UPDATE pubshow_businesses SET jukebox_ativo=? WHERE id=?', (0 if atual else 1, bid))
    elif acao == 'trocar_canal':
        canal = request.form.get('canal', 'rock')
        if canal in CANAIS:
            conn.execute('UPDATE pubshow_businesses SET canal_atual=? WHERE id=?', (canal, bid))
    elif acao == 'nota':
        nota = request.form.get('nota', '').strip()[:500]
        conn.execute('UPDATE pubshow_businesses SET notas_admin=? WHERE id=?', (nota, bid))
    elif acao == 'limpar_fila':
        conn.execute(
            "UPDATE pubshow_pedidos SET status='dispensado' WHERE business_id=? AND status='pendente'",
            (bid,)
        )
    conn.commit(); conn.close()
    return redirect(f'/pubshow/admin/bar/{bid}')


# ── ADMIN — GESTÃO DE VÍDEOS ───────────────────────────────────────────────────

@pubshow_bp.route('/admin/videos')
@_admin_required
def admin_videos():
    conn = get_pubshow_db()
    videos = conn.execute(
        'SELECT id, youtube_id, titulo, artista, categoria, ativo FROM pubshow_videos ORDER BY categoria, titulo'
    ).fetchall()
    conn.close()
    por_cat = {}
    for v in videos:
        cat = v['categoria']
        por_cat.setdefault(cat, []).append(dict(v))
    return render_template('pubshow/admin_videos.html', por_cat=por_cat,
                           total=len(videos))


@pubshow_bp.route('/admin/import-playlist', methods=['POST'])
@_admin_required
def admin_import_playlist():
    """Importa todos os vídeos de uma playlist do YouTube via Data API v3."""
    import os, re
    api_key = os.environ.get('YOUTUBE_API_KEY', '')
    if not api_key:
        return jsonify({'ok': False, 'erro': 'Configure YOUTUBE_API_KEY nas variáveis do Railway'})

    data      = request.get_json() or {}
    url_input = data.get('playlist_url', '').strip()
    categoria = data.get('categoria', 'rock').strip()
    subcategoria = data.get('subcategoria', '').strip() or 'playlist'

    # Extrai playlist ID da URL ou aceita ID direto
    m = re.search(r'[?&]list=([A-Za-z0-9_-]+)', url_input)
    playlist_id = m.group(1) if m else url_input

    videos     = []
    page_token = None
    paginas    = 0

    while paginas < 100:  # máx 5000 vídeos (100 × 50)
        params = {
            'part':       'snippet',
            'playlistId': playlist_id,
            'maxResults': 50,
            'key':        api_key,
        }
        if page_token:
            params['pageToken'] = page_token

        try:
            r = _requests.get(
                'https://www.googleapis.com/youtube/v3/playlistItems',
                params=params, timeout=15
            )
        except Exception as e:
            return jsonify({'ok': False, 'erro': f'Erro de rede: {e}'})

        if r.status_code == 400:
            return jsonify({'ok': False, 'erro': 'Playlist ID inválido'})
        if r.status_code == 403:
            return jsonify({'ok': False, 'erro': 'API Key inválida ou cota excedida'})
        if r.status_code != 200:
            return jsonify({'ok': False, 'erro': f'YouTube API HTTP {r.status_code}'})

        d = r.json()
        for item in d.get('items', []):
            sn     = item.get('snippet', {})
            vid_id = sn.get('resourceId', {}).get('videoId', '')
            titulo = sn.get('title', '')
            artista = sn.get('videoOwnerChannelTitle', '') or sn.get('channelTitle', '')
            # Ignora vídeos privados/removidos
            if vid_id and titulo and titulo not in ('Private video', 'Deleted video'):
                videos.append((vid_id, titulo, artista, categoria, subcategoria))

        page_token = d.get('nextPageToken')
        paginas   += 1
        if not page_token:
            break

    if not videos:
        return jsonify({'ok': False, 'erro': 'Playlist vazia ou não encontrada'})

    # Insere no banco (INSERT OR IGNORE — não duplica)
    conn     = get_pubshow_db()
    inseridos = 0
    for vid_id, titulo, artista, cat, subcat in videos:
        conn.execute(
            '''INSERT OR IGNORE INTO pubshow_videos
               (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, qualidade)
               VALUES (?,?,?,?,?,180,'HD')''',
            (vid_id, titulo, artista, cat, subcat)
        )
        inseridos += conn.execute('SELECT changes()').fetchone()[0]
    conn.commit()
    conn.close()

    return jsonify({
        'ok':       True,
        'total':    len(videos),
        'inseridos': inseridos,
        'duplicados': len(videos) - inseridos,
        'msg': f'✅ {inseridos} vídeos novos importados de {len(videos)} na playlist!'
    })


@pubshow_bp.route('/admin/videos/check-one', methods=['POST'])
@_admin_required
def admin_videos_check_one():
    """Verifica se um vídeo específico está disponível e embeddable via oEmbed."""
    yid = request.json.get('youtube_id', '')
    if not yid:
        return jsonify({'ok': False, 'erro': 'ID vazio'})
    try:
        r = _requests.get(
            'https://www.youtube.com/oembed',
            params={'url': f'https://www.youtube.com/watch?v={yid}', 'format': 'json'},
            timeout=8
        )
        if r.status_code == 200:
            data = r.json()
            return jsonify({'ok': True, 'titulo': data.get('title',''), 'autor': data.get('author_name','')})
        elif r.status_code == 401:
            return jsonify({'ok': False, 'erro': 'Embed bloqueado (401)'})
        elif r.status_code == 404:
            return jsonify({'ok': False, 'erro': 'Vídeo removido (404)'})
        else:
            return jsonify({'ok': False, 'erro': f'HTTP {r.status_code}'})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)[:80]})


@pubshow_bp.route('/admin/videos/deletar', methods=['POST'])
@_admin_required
def admin_videos_deletar():
    """Deleta vídeos pelo youtube_id (lista de IDs ruins)."""
    ids = request.json.get('ids', [])
    if not ids:
        return jsonify({'ok': False})
    conn = get_pubshow_db()
    placeholders = ','.join('?' * len(ids))
    deleted = conn.execute(f'DELETE FROM pubshow_videos WHERE youtube_id IN ({placeholders})', ids).rowcount
    conn.commit(); conn.close()
    log.info(f'[PUBSHOW ADMIN] Deletados {deleted} vídeos quebrados')
    return jsonify({'ok': True, 'deleted': deleted})


@pubshow_bp.route('/admin/videos/add', methods=['POST'])
@_admin_required
def admin_videos_add():
    """Adiciona um vídeo manualmente à biblioteca."""
    data = request.json or {}
    yid      = (data.get('youtube_id') or '').strip()
    titulo   = (data.get('titulo') or '').strip()
    artista  = (data.get('artista') or '').strip()
    categoria = (data.get('categoria') or '').strip()
    if not yid or not titulo or not categoria:
        return jsonify({'ok': False, 'erro': 'Campos obrigatórios: youtube_id, titulo, categoria'})
    try:
        conn = get_pubshow_db()
        conn.execute(
            'INSERT OR IGNORE INTO pubshow_videos (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, views_milhoes, ativo) VALUES (?,?,?,?,?,?,?,1)',
            (yid, titulo, artista, categoria, '', 0, 0)
        )
        conn.commit(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)[:100]})


@pubshow_bp.route('/admin/videos/add-batch', methods=['POST'])
@_admin_required
def admin_videos_add_batch():
    """Insere vários vídeos de uma vez. Recebe lista de {youtube_id, titulo, artista, categoria}."""
    items = request.json or []
    if not isinstance(items, list):
        return jsonify({'ok': False, 'erro': 'Esperado uma lista JSON'})
    conn = get_pubshow_db()
    inseridos = 0
    duplicados = 0
    erros = 0
    for item in items:
        yid  = (item.get('youtube_id') or '').strip()
        tit  = (item.get('titulo') or '').strip()
        art  = (item.get('artista') or '').strip()
        cat  = (item.get('categoria') or '').strip()
        if not yid or not tit or not cat:
            erros += 1
            continue
        try:
            conn.execute(
                'INSERT OR IGNORE INTO pubshow_videos '
                '(youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, views_milhoes, ativo) '
                'VALUES (?,?,?,?,?,0,0,1)',
                (yid, tit, art, cat, '')
            )
            changed = conn.execute('SELECT changes()').fetchone()[0]
            if changed:
                inseridos += 1
            else:
                duplicados += 1
        except Exception:
            erros += 1
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'inseridos': inseridos, 'duplicados': duplicados, 'erros': erros})


@pubshow_bp.route('/admin/videos/oembed', methods=['POST'])
@_admin_required
def admin_videos_oembed():
    """Busca título e autor via oEmbed (sem API key). Recebe {youtube_id}."""
    yid = (request.json or {}).get('youtube_id', '').strip()
    if not yid:
        return jsonify({'ok': False})
    try:
        r = _requests.get(
            'https://www.youtube.com/oembed',
            params={'url': f'https://www.youtube.com/watch?v={yid}', 'format': 'json'},
            timeout=8
        )
        if r.status_code == 200:
            d = r.json()
            return jsonify({'ok': True, 'titulo': d.get('title', ''), 'artista': d.get('author_name', '')})
        return jsonify({'ok': False, 'erro': f'HTTP {r.status_code}'})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)[:60]})


# ── CHECKOUT / ASSINATURA ─────────────────────────────────────────────────────

@pubshow_bp.route('/assinar/<plano>', methods=['GET', 'POST'])
@pubshow_login_required
def assinar(plano):
    if plano not in PLANOS:
        return redirect('/pubshow/planos')
    b = _get_business()
    erro = ''
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX')
        try:
            customer_id = _asaas_criar_ou_buscar_cliente(b)
            if not customer_id:
                erro = 'Erro ao criar perfil de pagamento.'
            else:
                sub = _asaas_criar_assinatura(customer_id, plano, billing_type, b['id'])
                if sub.get('id'):
                    conn = get_pubshow_db()
                    conn.execute(
                        '''INSERT OR REPLACE INTO pubshow_assinaturas
                           (business_id, plano, valor, status, asaas_subscription_id, billing_type)
                           VALUES (?,?,?,?,?,?)''',
                        (b['id'], plano, PLANOS[plano]['preco'], 'pendente', sub['id'], billing_type)
                    )
                    conn.commit(); conn.close()
                    pay_url = sub.get('invoiceUrl') or sub.get('bankSlipUrl') or ''
                    if pay_url:
                        return redirect(pay_url)
                    return redirect('/pubshow/aguardando')
                else:
                    erro = sub.get('errors', [{}])[0].get('description', 'Erro ao criar assinatura.')
        except Exception as ex:
            log.error('[PUBSHOW] Erro assinar: %s', ex, exc_info=True)
            erro = 'Erro ao processar. Tente novamente.'
    return render_template('pubshow/checkout.html', b=dict(b), plano=plano, p=PLANOS[plano], erro=erro)


@pubshow_bp.route('/aguardando')
@pubshow_login_required
def aguardando():
    b = _get_business()
    return render_template('pubshow/aguardando.html', b=dict(b))


@pubshow_bp.route('/planos')
def planos():
    return render_template('pubshow/planos.html', planos=PLANOS, canais=CANAIS)


# ── WEBHOOK ASAAS ─────────────────────────────────────────────────────────────

@pubshow_bp.route('/webhook/asaas', methods=['GET', 'POST'])
def webhook_asaas():
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200

    token_esp = os.environ.get('ASAAS_WEBHOOK_TOKEN', '')
    token_rec = request.headers.get('asaas-access-token', '')
    if token_esp and token_rec != token_esp:
        return jsonify({'error': 'unauthorized'}), 401

    dados    = request.get_json(silent=True) or {}
    evento   = dados.get('event', '')
    pagamento= dados.get('payment', {})
    ext_ref  = pagamento.get('externalReference', '')
    sub_id   = pagamento.get('subscription', '')

    if evento in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED', 'SUBSCRIPTION_ACTIVATED'):
        if ext_ref.startswith('pubshow_'):
            partes = ext_ref.split('_')
            if len(partes) >= 3:
                try:
                    bid   = int(partes[1])
                    plano = partes[2]
                    if plano in PLANOS:
                        conn = get_pubshow_db()
                        conn.execute(
                            'UPDATE pubshow_businesses SET plano=?, plano_ativo=1 WHERE id=?',
                            (plano, bid)
                        )
                        conn.execute(
                            "UPDATE pubshow_assinaturas SET status='ativo', plano=? WHERE business_id=?",
                            (plano, bid)
                        )
                        conn.commit(); conn.close()
                        log.info('[PUBSHOW] Assinatura ativada: business_id=%s plano=%s', bid, plano)
                except Exception as ex:
                    log.error('[PUBSHOW] Webhook erro: %s', ex)

    elif evento in ('SUBSCRIPTION_CANCELLED', 'PAYMENT_OVERDUE'):
        if sub_id:
            conn = get_pubshow_db()
            ass = conn.execute(
                'SELECT * FROM pubshow_assinaturas WHERE asaas_subscription_id=?', (sub_id,)
            ).fetchone()
            if ass:
                conn.execute('UPDATE pubshow_businesses SET plano_ativo=0 WHERE id=?', (ass['business_id'],))
                conn.execute("UPDATE pubshow_assinaturas SET status='cancelado' WHERE id=?", (ass['id'],))
                conn.commit()
            conn.close()

    return jsonify({'status': 'ok'}), 200


# ── WEBHOOK ASAAS — JUKEBOX (cobranças avulsas PIX) ──────────────────────────

@pubshow_bp.route('/webhook/asaas-jukebox', methods=['GET', 'POST'])
def webhook_asaas_jukebox():
    """Recebe confirmação de pagamento PIX do Asaas para pedidos de Jukebox.

    Configurar no painel Asaas:
      URL: https://<seu-dominio>/pubshow/webhook/asaas-jukebox
      Eventos: PAYMENT_RECEIVED, PAYMENT_CONFIRMED
    """
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200

    token_esp = os.environ.get('ASAAS_WEBHOOK_TOKEN', '')
    token_rec = request.headers.get('asaas-access-token', '')
    if token_esp and token_rec != token_esp:
        return jsonify({'error': 'unauthorized'}), 401

    dados     = request.get_json(silent=True) or {}
    evento    = dados.get('event', '')
    pagamento = dados.get('payment', {})
    ext_ref   = pagamento.get('externalReference', '')
    pay_id    = pagamento.get('id', '')

    log.info('[PUBSHOW] Webhook Jukebox: evento=%s ref=%s', evento, ext_ref)

    if evento in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED') and ext_ref.startswith('jukebox_'):
        try:
            pedido_id = int(ext_ref.split('_', 1)[1])
            conn = get_pubshow_db()
            conn.execute(
                "UPDATE pubshow_pedidos SET status='pendente' WHERE id=? AND status='aguardando_pix'",
                (pedido_id,)
            )
            conn.commit()
            conn.close()
            log.info('[PUBSHOW] Jukebox PIX confirmado — pedido #%s (Asaas %s)', pedido_id, pay_id)
        except Exception as ex:
            log.error('[PUBSHOW] Webhook Jukebox erro: %s', ex, exc_info=True)

    return jsonify({'status': 'ok'}), 200
