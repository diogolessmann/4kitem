"""
afiliados.py — Blueprint do Programa de Afiliados do 4kitem (/afiliados).
Cadastro único → link de indicação por app → comissão recorrente quando o cliente paga.
Pagamento ao afiliado: PIX via Asaas /transfers (grátis). Motor compartilhado p/ todos os apps.
"""
import os
import logging
import requests as _requests
from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, request, session, redirect, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from afiliados_db import (get_afil_db, init_afil_db, criar_afiliado, get_por_codigo,
                          get_por_email, get_por_id, registrar_conversao, marcar_conversao,
                          conversoes_do_afiliado, stats_afiliado)

log = logging.getLogger('afiliados')
afiliados_bp = Blueprint('afiliados', __name__, url_prefix='/afiliados')

BASE_URL = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')

# ── Registro de apps: comissão (R$ recorrente) por app ──────────────────────────
# Adicionar um app aqui já o disponibiliza pros afiliados venderem.
APPS = {
    'somaja': {'nome': 'SomaJá', 'comissao': 4.00, 'url': '/somaja',
               'desc': 'Coach financeiro no WhatsApp', 'icone': '/static/somaja/logo.png'},
}


# ── Asaas (PIX transfer — grátis, igual SlotZap) ────────────────────────────────
_ASAAS_BASE = 'https://api.asaas.com/v3'


def _asaas_req(method, endpoint, data=None):
    try:
        r = _requests.request(method, f'{_ASAAS_BASE}{endpoint}',
            headers={'access_token': os.environ.get('ASAAS_API_KEY', ''), 'Content-Type': 'application/json'},
            json=data, timeout=20)
        return r.json() if r.content else {}
    except Exception as e:
        return {'error': str(e)}


def _pix_transfer(pix_chave, pix_tipo, valor, descricao, ext_ref):
    """Envia comissão via PIX (Asaas /transfers). Retorna (transfer_id, erro)."""
    payload = {'value': round(float(valor), 2), 'operationType': 'PIX',
               'pixAddressKey': pix_chave, 'description': (descricao or '')[:100],
               'externalReference': ext_ref}
    if pix_tipo:
        payload['pixAddressKeyType'] = pix_tipo
    resp = _asaas_req('POST', '/transfers', payload)
    tid = resp.get('id', '')
    if tid:
        return tid, None
    errs = resp.get('errors') or []
    msg = (errs[0].get('description') if errs and errs[0].get('description')
           else (resp.get('error') or 'Falha na transferência PIX.'))
    return '', str(msg)[:200]


# ── O CORAÇÃO: creditar comissão quando o cliente paga ──────────────────────────
def registrar_comissao(codigo, app, payment_id, cliente_nome=''):
    """Chamado pelo webhook do Asaas quando um cliente (indicado pelo afiliado) paga.
    Credita a comissão do app (idempotente) e paga o afiliado via PIX na hora.
    Best-effort: nunca levanta exceção (não pode derrubar o webhook)."""
    try:
        codigo = (codigo or '').strip().upper()
        if not codigo or app not in APPS or not payment_id:
            return False
        af = get_por_codigo(codigo)
        if not af:
            return False
        comissao = float(APPS[app]['comissao'])
        conv_id = registrar_conversao(af['id'], app, payment_id, cliente_nome, comissao)
        if not conv_id:
            return False  # já creditado (reenvio de webhook) — não paga 2×
        if not af['pix_chave']:
            log.info(f'[Afiliados] comissão {conv_id} fica pendente — afiliado {af["codigo"]} sem chave PIX')
            return True
        tid, erro = _pix_transfer(af['pix_chave'], af['pix_tipo'], comissao,
                                  f'Comissão {APPS[app]["nome"]} — 4kitem', f'afil_{app}_{payment_id}')
        if tid:
            marcar_conversao(conv_id, 'pago', tid, '')
            log.info(f'[Afiliados] R${comissao:.2f} -> {af["nome"]} ({app}, pay {payment_id})')
        else:
            marcar_conversao(conv_id, 'erro', '', erro or '')
            log.warning(f'[Afiliados] falha PIX p/ {af["nome"]}: {erro}')
        return True
    except Exception as e:
        log.error(f'[Afiliados] registrar_comissao erro: {e}')
        return False


# ── Auth ────────────────────────────────────────────────────────────────────────
def afil_login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get('afil_id'):
            return redirect('/afiliados/entrar')
        return f(*a, **k)
    return wrap


def _af():
    aid = session.get('afil_id')
    return get_por_id(aid) if aid else None


def _brl(v):
    return ('R$ ' + f'{float(v or 0):,.2f}').replace(',', 'X').replace('.', ',').replace('X', '.')


def _link(codigo, app):
    return f'{BASE_URL}{APPS[app]["url"]}?ref={codigo}'


@afiliados_bp.route('/')
def landing():
    if session.get('afil_id'):
        return redirect('/afiliados/painel')
    return render_template('afiliados/landing.html', apps=APPS, _brl=_brl)


@afiliados_bp.route('/cadastrar', methods=['GET', 'POST'])
def cadastrar():
    if session.get('afil_id'):
        return redirect('/afiliados/painel')
    erro = None
    if request.method == 'POST':
        nome  = (request.form.get('nome') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        tel   = ''.join(c for c in (request.form.get('telefone') or '') if c.isdigit())
        senha = request.form.get('senha') or ''
        pixk  = (request.form.get('pix_chave') or '').strip()
        pixt  = (request.form.get('pix_tipo') or '').strip().upper()
        if not nome or not email or not senha:
            erro = 'Preencha nome, e-mail e senha.'
        elif len(senha) < 6:
            erro = 'A senha precisa de pelo menos 6 caracteres.'
        else:
            aid, codigo = criar_afiliado(nome, email, tel, generate_password_hash(senha), pixk, pixt)
            if not aid:
                erro = 'Já existe uma conta com esse e-mail. Faça login.'
            else:
                session['afil_id'] = aid
                session['afil_nome'] = nome
                return redirect('/afiliados/painel')
    return render_template('afiliados/cadastrar.html', erro=erro)


@afiliados_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if session.get('afil_id'):
        return redirect('/afiliados/painel')
    erro = None
    if request.method == 'POST':
        af = get_por_email(request.form.get('email') or '')
        if af and check_password_hash(af['password_hash'], request.form.get('senha') or ''):
            conn = get_afil_db()
            conn.execute('UPDATE afiliados SET ultimo_acesso=? WHERE id=?', (datetime.now().isoformat(), af['id']))
            conn.commit(); conn.close()
            session['afil_id'] = af['id']; session['afil_nome'] = af['nome']
            return redirect('/afiliados/painel')
        erro = 'E-mail ou senha incorretos.'
    return render_template('afiliados/entrar.html', erro=erro)


@afiliados_bp.route('/sair')
def sair():
    session.pop('afil_id', None); session.pop('afil_nome', None)
    return redirect('/afiliados')


@afiliados_bp.route('/painel')
@afil_login_required
def painel():
    af = _af()
    if not af:
        session.clear(); return redirect('/afiliados/entrar')
    links = [{'app': k, 'nome': v['nome'], 'desc': v['desc'], 'icone': v.get('icone', ''),
              'comissao': _brl(v['comissao']), 'link': _link(af['codigo'], k)} for k, v in APPS.items()]
    return render_template('afiliados/painel.html', af=af, links=links, _brl=_brl,
                           stats=stats_afiliado(af['id']),
                           conversoes=conversoes_do_afiliado(af['id']),
                           apps=APPS, sem_pix=not af['pix_chave'])


@afiliados_bp.route('/pix', methods=['POST'])
@afil_login_required
def atualizar_pix():
    af = _af()
    pixk = (request.form.get('pix_chave') or '').strip()
    pixt = (request.form.get('pix_tipo') or '').strip().upper()
    conn = get_afil_db()
    conn.execute('UPDATE afiliados SET pix_chave=?, pix_tipo=? WHERE id=?', (pixk, pixt, af['id']))
    conn.commit(); conn.close()
    return redirect('/afiliados/painel')
