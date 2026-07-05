"""arena.py — Blueprint ARENA (jogos casuais valendo prêmio, PWA em /arena).

Marca: AmbitiON. Auth isolado (molde SomaJá), créditos pré-pagos (molde DRZAP).
L0 = FUNDAÇÃO: login + portal + jogo GRÁTIS (treino). Sem dinheiro ainda.
Próximos lotes: comprar crédito no PIX (_sz_gerar_pix), sala x1 valendo (debita_creditos),
prêmio->saldo, saque (_sz_afiliado_transfer + ledger arena_pagamentos + guarda arena_premio_).
"""
import os
import logging
from datetime import datetime, timedelta
from functools import wraps
import requests as _requests
from flask import Blueprint, render_template, redirect, request, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

from arena_db import (get_arena_db, get_user, get_user_by_email, criar_user,
                      get_creditos, add_creditos)

log = logging.getLogger('arena')

arena_bp = Blueprint('arena', __name__, url_prefix='/arena')

# Catálogo de jogos do portal (MODOS x JOGOS). Só 'blocos' ativo no L0.
JOGOS = [
    {'id': 'blocos',   'nome': 'Quebra-Blocos', 'emoji': '🧱', 'ativo': True},
    {'id': 'cobrinha', 'nome': 'Cobrinha',      'emoji': '🐍', 'ativo': False},
    {'id': 'bolhas',   'nome': 'Tiro de Bolhas','emoji': '🎯', 'ativo': False},
    {'id': 'combina',  'nome': 'Combina-3',     'emoji': '🍬', 'ativo': False},
    {'id': 'labirinto','nome': 'Labirinto',     'emoji': '🧩', 'ativo': False},
    {'id': 'quiz',     'nome': 'Quiz Relâmpago','emoji': '❓', 'ativo': False},
]


def arena_login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get('arena_user_id'):
            return redirect('/arena/entrar')
        return f(*a, **k)
    return wrap


def _cur():
    uid = session.get('arena_user_id')
    return get_user(uid) if uid else None


@arena_bp.route('/')
def portal():
    u = _cur()
    cred = get_creditos(u['id']) if u else 0
    return render_template('arena/portal.html', user=u, creditos=cred, jogos=JOGOS)


@arena_bp.route('/jogar')
def jogar():
    """Jogo GRÁTIS de treino (público, sem login) — porta de entrada e viralização."""
    return render_template('arena/jogo.html', user=_cur())


@arena_bp.route('/cadastrar', methods=['GET', 'POST'])
def cadastrar():
    if request.method == 'GET':
        return render_template('arena/cadastrar.html', erro=None)
    nome = (request.form.get('nome') or '').strip()[:80]
    email = (request.form.get('email') or '').strip().lower()[:120]
    tel = ''.join(c for c in (request.form.get('tel') or '') if c.isdigit())[:15]
    senha = request.form.get('senha') or ''
    if not nome or '@' not in email or '.' not in email:
        return render_template('arena/cadastrar.html', erro='Confira nome e e-mail.')
    if len(senha) < 4:
        return render_template('arena/cadastrar.html', erro='A senha precisa de pelo menos 4 caracteres.')
    if get_user_by_email(email):
        return render_template('arena/cadastrar.html', erro='Esse e-mail já tem conta. É só entrar.')
    uid = criar_user(nome, email, tel, generate_password_hash(senha))
    if not uid:
        return render_template('arena/cadastrar.html', erro='Não deu pra criar a conta. Tente de novo.')
    session['arena_user_id'] = uid
    session['arena_user_nome'] = nome
    return redirect('/arena')


@arena_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if request.method == 'GET':
        return render_template('arena/entrar.html', erro=None)
    email = (request.form.get('email') or '').strip().lower()
    senha = request.form.get('senha') or ''
    u = get_user_by_email(email)
    if not u or not check_password_hash(u['password_hash'], senha):
        return render_template('arena/entrar.html', erro='E-mail ou senha errados.')
    session['arena_user_id'] = u['id']
    session['arena_user_nome'] = u['nome']
    return redirect('/arena')


@arena_bp.route('/sair')
def sair():
    session.pop('arena_user_id', None)
    session.pop('arena_user_nome', None)
    return redirect('/arena')


# ══════════════════════════════════════════════════════════════════════════════
# LOTE 2 — Comprar FICHAS (crédito) no PIX (molde DRZAP, autossuficiente no Asaas).
# Cada ficha = uma entrada de partida valendo prêmio. Confirma-compra idempotente.
# ══════════════════════════════════════════════════════════════════════════════
PACOTES = {
    'inicio': {'creditos': 5,  'preco': 20.0,  'rotulo': '5 fichas',  'bonus': ''},
    'turbo':  {'creditos': 15, 'preco': 50.0,  'rotulo': '15 fichas', 'bonus': '+3 grátis'},
    'mestre': {'creditos': 35, 'preco': 100.0, 'rotulo': '35 fichas', 'bonus': '+10 grátis'},
}

_ASAAS_BASE = 'https://api.asaas.com/v3'


def _asaas_req(method, endpoint, data=None):
    try:
        r = _requests.request(method, f'{_ASAAS_BASE}{endpoint}',
            headers={'access_token': os.environ.get('ASAAS_API_KEY', ''),
                     'Content-Type': 'application/json'},
            json=data, timeout=20)
        return r.json() if r.content else {}
    except Exception as e:
        return {'error': str(e)}


def _cpf_digits(s):
    return ''.join(c for c in str(s or '') if c.isdigit())


def _cpf_valido(cpf):
    cpf = _cpf_digits(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[n]) * ((i + 1) - n) for n in range(i))
        d = (soma * 10) % 11
        d = 0 if d == 10 else d
        if d != int(cpf[i]):
            return False
    return True


def _asaas_cliente(u, cpf):
    """Cria/recupera cliente Asaas e salva customer_id + cpf no arena_users. Retorna id ou ''."""
    if u.get('asaas_customer_id'):
        return u['asaas_customer_id']
    cpf = _cpf_digits(cpf)
    cid = None
    if cpf:
        busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf}&limit=1')
        if busca.get('data'):
            cid = busca['data'][0].get('id')
    if not cid:
        resp = _asaas_req('POST', '/customers', {
            'name': u['nome'], 'email': u.get('email') or '',
            'mobilePhone': _cpf_digits(u.get('tel') or ''), 'cpfCnpj': cpf,
            'notificationDisabled': True})
        cid = resp.get('id')
    if cid:
        conn = get_arena_db()
        conn.execute('UPDATE arena_users SET asaas_customer_id=?, cpf=? WHERE id=?', (cid, cpf, u['id']))
        conn.commit(); conn.close()
    return cid or ''


def _arena_confirmar_compra(compra_id):
    """Credita as fichas de UMA compra de forma ATÔMICA e idempotente.
    Retorna True só se creditou AGORA (1ª confirmação) — nunca em dobro (webhook+poll+recon)."""
    conn = get_arena_db()
    cur = conn.execute("UPDATE arena_compras SET status='pago' WHERE id=? AND status='pendente'",
                       (compra_id,))
    conn.commit()
    if cur.rowcount == 0:          # já confirmada por outra via
        conn.close()
        return False
    row = conn.execute('SELECT user_id, creditos FROM arena_compras WHERE id=?', (compra_id,)).fetchone()
    conn.close()
    if not row:
        return False
    add_creditos(row['user_id'], row['creditos'])
    log.info(f'[Arena] Compra {compra_id} PAGA — +{row["creditos"]} fichas (user {row["user_id"]})')
    return True


def arena_webhook_confirmar(external_ref, payment_id=''):
    """Chamado pelo webhook global (app.py) p/ refs 'arena_<compra_id>' (compra de fichas).
    Refs de PAYOUT ('arena_premio_...') caem no split e viram não-int → ignorados aqui (seguro)."""
    try:
        cid = int(str(external_ref).split('_')[1])
    except (IndexError, ValueError):
        return False
    return _arena_confirmar_compra(cid)


@arena_bp.route('/comprar')
@arena_login_required
def comprar():
    u = _cur()
    return render_template('arena/comprar.html', user=u, creditos=get_creditos(u['id']), pacotes=PACOTES)


@arena_bp.route('/checkout/<pacote>', methods=['GET', 'POST'])
@arena_login_required
def checkout(pacote):
    u = _cur()
    if pacote not in PACOTES:
        return redirect('/arena/comprar')
    p = PACOTES[pacote]
    erro = None
    if request.method == 'POST':
        cpf = _cpf_digits(request.form.get('cpf'))
        if not _cpf_valido(cpf):
            erro = 'CPF inválido. Confira os números.'
        else:
            customer_id = _asaas_cliente(u, cpf)
            if not customer_id:
                erro = 'Não foi possível iniciar o pagamento. Tente de novo.'
            else:
                conn = get_arena_db()
                cur = conn.execute(
                    'INSERT INTO arena_compras(user_id,pacote,creditos,valor,status,ext_ref,criado_em) '
                    'VALUES(?,?,?,?,?,?,?)',
                    (u['id'], pacote, p['creditos'], p['preco'], 'pendente', '', datetime.now().isoformat()))
                compra_id = cur.lastrowid
                conn.commit(); conn.close()
                ext = f'arena_{compra_id}'
                venc = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                pay = _asaas_req('POST', '/payments', {
                    'customer': customer_id, 'billingType': 'PIX', 'value': p['preco'],
                    'dueDate': venc, 'description': f'Arena — {p["rotulo"]}',
                    'externalReference': ext})
                pid = pay.get('id')
                if not pid:
                    erro = (pay.get('errors') or [{}])[0].get('description', 'Erro ao gerar o PIX.')
                else:
                    conn = get_arena_db()
                    conn.execute('UPDATE arena_compras SET charge_id=?, ext_ref=? WHERE id=?',
                                 (pid, ext, compra_id))
                    conn.commit(); conn.close()
                    return redirect(f'/arena/pix/{compra_id}')
    return render_template('arena/checkout.html', user=u, pacote=pacote, p=p, erro=erro)


@arena_bp.route('/pix/<int:compra_id>')
@arena_login_required
def pix(compra_id):
    u = _cur()
    conn = get_arena_db()
    row = conn.execute('SELECT * FROM arena_compras WHERE id=? AND user_id=?',
                       (compra_id, u['id'])).fetchone()
    conn.close()
    if not row:
        return redirect('/arena/comprar')
    compra = dict(row)
    qr = copia = ''
    if compra['status'] == 'pendente' and compra['charge_id']:
        resp = _asaas_req('GET', f'/payments/{compra["charge_id"]}/pixQrCode')
        qr = resp.get('encodedImage', '')
        copia = resp.get('payload', '')
    return render_template('arena/pix.html', user=u, compra=compra, qr=qr, copia=copia)


@arena_bp.route('/pix-status/<int:compra_id>', methods=['POST'])
@arena_login_required
def pix_status(compra_id):
    u = _cur()
    conn = get_arena_db()
    row = conn.execute('SELECT * FROM arena_compras WHERE id=? AND user_id=?',
                       (compra_id, u['id'])).fetchone()
    conn.close()
    if not row:
        return jsonify({'erro': 'não encontrada'}), 404
    compra = dict(row)
    if compra['status'] == 'pago':
        return jsonify({'pago': True, 'creditos': get_creditos(u['id'])})
    pid = compra['charge_id']
    if not pid:
        return jsonify({'pago': False})
    pay = _asaas_req('GET', f'/payments/{pid}')
    if (pay.get('status') or '').upper() in ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH'):
        _arena_confirmar_compra(compra_id)
        return jsonify({'pago': True, 'creditos': get_creditos(u['id'])})
    return jsonify({'pago': False})
