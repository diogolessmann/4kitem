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
                   session, jsonify, url_for)
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
    'rock':           {'nome': 'Rock TV',          'emoji': '🎸',  'cor': '#ef4444', 'cat': 'rock',      'grupo': 'musica'},
    'punk':           {'nome': 'Punk TV',           'emoji': '🤘',  'cor': '#f97316', 'cat': 'punk',      'grupo': 'musica'},
    'sertanejo':      {'nome': 'Sertanejo TV',      'emoji': '🤠',  'cor': '#eab308', 'cat': 'sertanejo', 'grupo': 'musica'},
    'pagode':         {'nome': 'Pagode TV',         'emoji': '🥁',  'cor': '#22c55e', 'cat': 'pagode',    'grupo': 'musica'},
    'pop':            {'nome': 'Pop TV',            'emoji': '🎤',  'cor': '#a855f7', 'cat': 'pop',       'grupo': 'musica'},
    # ── SHOWS AO VIVO ────────────────────────────────────────────────────────
    'show_rock':      {'nome': 'Rock Shows',        'emoji': '🎸🎤', 'cor': '#dc2626', 'cat': 'show_rock',      'grupo': 'shows'},
    'show_sertanejo': {'nome': 'Sertanejo Shows',   'emoji': '🤠🎤', 'cor': '#ca8a04', 'cat': 'show_sertanejo', 'grupo': 'shows'},
    'show_pagode':    {'nome': 'Pagode Shows',      'emoji': '🥁🎤', 'cor': '#15803d', 'cat': 'show_pagode',    'grupo': 'shows'},
    # ── SPORT ────────────────────────────────────────────────────────────────
    'sport_mix':      {'nome': 'Sport Mix',         'emoji': '🏆',  'cor': '#f59e0b',
                       'cat': ['f1', 'futebol', 'surf', 'aerio', 'radical'],       'grupo': 'sport'},
    'f1':             {'nome': 'Speed TV',          'emoji': '🏎️',  'cor': '#e11d48', 'cat': 'f1',        'grupo': 'sport'},
    'futebol':        {'nome': 'Futebol TV',        'emoji': '⚽',  'cor': '#16a34a', 'cat': 'futebol',   'grupo': 'sport'},
    'surf':           {'nome': 'Surf TV',           'emoji': '🏄',  'cor': '#0ea5e9', 'cat': 'surf',      'grupo': 'sport'},
    'aerio':          {'nome': 'Aéreo TV',          'emoji': '🪂',  'cor': '#6366f1', 'cat': 'aerio',     'grupo': 'sport'},
    'radical':        {'nome': 'Radical TV',        'emoji': '🛹',  'cor': '#f59e0b', 'cat': 'radical',   'grupo': 'sport'},
}

PLANOS = {
    'casa': {
        'nome': 'Casa',
        'emoji': '🏠',
        'preco': 19.90,
        'preco_fmt': 'R$ 19,90',
        'descricao': 'Para fãs em casa',
        'features': ['1 tela', 'Todos os canais', 'Sem Jukebox', 'Atualização mensal da biblioteca'],
    },
    'bar': {
        'nome': 'Bar / Pub',
        'emoji': '🍺',
        'preco': 89.90,
        'preco_fmt': 'R$ 89,90',
        'descricao': 'Para estabelecimentos',
        'destaque': True,
        'features': ['Telas ilimitadas', 'Todos os canais', 'Jukebox ativo (100% pra você)', 'QR Code de mesa', 'Parabéns e Dedicatórias', 'Painel de gestão'],
    },
    'premium': {
        'nome': 'Premium / Rede',
        'emoji': '🏟️',
        'preco': 189.90,
        'preco_fmt': 'R$ 189,90',
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

def _gerar_jukebox_token():
    """Token rotativo para o QR do Jukebox — independente do code da TV."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))

def _admin_ok():
    """Verifica se a sessão atual é admin."""
    return session.get('pubshow_admin') is True

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


def _videos_do_canal(canal_key, limit=60):
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


def _pedido_pendente(business_id):
    """Busca o próximo pedido especial (parabéns/dedicatória etc.) pendente."""
    conn = get_pubshow_db()
    p = conn.execute(
        '''SELECT * FROM pubshow_pedidos
           WHERE business_id=? AND status="pendente"
           AND tipo IN ("parabens","dedicatoria","brinde","chegada","casamento","vip")
           ORDER BY created_at ASC LIMIT 1''',
        (business_id,)
    ).fetchone()
    conn.close()
    return dict(p) if p else None


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

    sucesso = None
    erro    = ''

    if request.method == 'POST':
        tipo          = request.form.get('tipo', '')
        nome_cliente  = request.form.get('nome_cliente', '').strip()
        mensagem      = request.form.get('mensagem', '').strip()
        categoria     = request.form.get('categoria', b['canal_atual'])
        youtube_id    = request.form.get('youtube_id', '').strip()[:20]
        titulo_pedido = request.form.get('titulo_pedido', '').strip()[:80]
        thumb_url     = request.form.get('thumb_url', '').strip()[:200]

        if tipo not in TIPOS_PEDIDO:
            erro = 'Tipo de pedido inválido.'
        elif not nome_cliente:
            erro = 'Informe seu nome.'
        elif tipo in ('musica_especifica', 'musica_externa') and not youtube_id:
            erro = 'Selecione uma música antes de confirmar.'
        else:
            t = TIPOS_PEDIDO[tipo]
            conn2 = get_pubshow_db()
            conn2.execute(
                '''INSERT INTO pubshow_pedidos
                   (business_id, tipo, nome_cliente, mensagem, categoria, status, valor,
                    youtube_id, titulo_pedido, thumb_url)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (b['id'], tipo, nome_cliente, mensagem, categoria, 'pendente', t['preco'],
                 youtube_id or None, titulo_pedido or None, thumb_url or None)
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
                           tipos=TIPOS_PEDIDO, fila=[dict(f) for f in fila],
                           sucesso=sucesso, erro=erro,
                           total_videos=total_videos)


# ── API (usada pelo TV player via JS polling) ─────────────────────────────────

@pubshow_bp.route('/api/status/<code>')
def api_status(code):
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE code=?', (code,)).fetchone()
    if not b:
        conn.close()
        return jsonify({'error': 'not_found'}), 404

    # Pedido especial — aparece como overlay na TV (parabéns, dedicatória etc.)
    pedido_especial = conn.execute(
        '''SELECT * FROM pubshow_pedidos
           WHERE business_id=? AND status="pendente"
           AND tipo IN ("parabens","dedicatoria","brinde","chegada","casamento")
           ORDER BY created_at ASC LIMIT 1''',
        (b['id'],)
    ).fetchone()

    # Pedido de música — espera o vídeo atual acabar (VIP, flash, musica)
    pedido_musica = conn.execute(
        '''SELECT * FROM pubshow_pedidos
           WHERE business_id=? AND status="pendente"
           AND tipo IN ("vip","flash","musica")
           ORDER BY
             CASE tipo WHEN "vip" THEN 1 WHEN "flash" THEN 2 ELSE 3 END,
             created_at ASC
           LIMIT 1''',
        (b['id'],)
    ).fetchone()

    # Contagem total na fila (para mostrar no indicador)
    total_fila = conn.execute(
        'SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status="pendente"',
        (b['id'],)
    ).fetchone()[0]

    result = {
        'canal_atual': b['canal_atual'],
        'jukebox_ativo': bool(b['jukebox_ativo']),
        'pedido': dict(pedido_especial) if pedido_especial else None,      # overlay imediato
        'pedido_musica': dict(pedido_musica) if pedido_musica else None,   # espera fim do vídeo
        'total_fila': total_fila,
    }
    conn.close()
    return jsonify(result)


@pubshow_bp.route('/api/pedido-exibido/<int:pedido_id>', methods=['POST'])
def api_pedido_exibido(pedido_id):
    conn = get_pubshow_db()
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
    conn.close()
    return render_template('pubshow/painel.html',
                           b=dict(b), canais=CANAIS,
                           pedidos_hoje=[dict(p) for p in pedidos_hoje],
                           total_hoje=total_hoje,
                           fila=[dict(f) for f in fila],
                           tipos=TIPOS_PEDIDO,
                           planos=PLANOS)


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

    # URL pública do Jukebox — usa jukebox_token rotativo
    base = request.host_url.rstrip('/')
    token = b['jukebox_token'] or b['code']
    jukebox_url = f"{base}/pubshow/jukebox/{token}"

    # Gera QR como PNG base64
    try:
        import qrcode
        from qrcode.image.styledpil import StyledPilImage
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
    return redirect('/pubshow/painel')


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
           (SELECT COALESCE(SUM(p.valor),0) FROM pubshow_pedidos p WHERE p.business_id=b.id) total_receita,
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
           WHERE date(created_at)=date("now","localtime")'''
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
