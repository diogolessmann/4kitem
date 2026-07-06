"""arena.py — Blueprint ARENA (jogos casuais valendo prêmio, PWA em /arena).

Marca: AmbitiON. Auth isolado (molde SomaJá), créditos pré-pagos (molde DRZAP).
L0 = FUNDAÇÃO: login + portal + jogo GRÁTIS (treino). Sem dinheiro ainda.
Próximos lotes: comprar crédito no PIX (_sz_gerar_pix), sala x1 valendo (debita_creditos),
prêmio->saldo, saque (_sz_afiliado_transfer + ledger arena_pagamentos + guarda arena_premio_).
"""
import os
import json
import random
import secrets
import logging
from datetime import datetime, timedelta
from functools import wraps
import requests as _requests
from flask import Blueprint, render_template, redirect, request, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

import arena_game
try:
    import efi_pix   # RECEBER PIX pela Efí (barato no ticket baixo). Payout continua no Asaas.
except Exception:
    efi_pix = None
from arena_db import (get_arena_db, get_user, get_user_by_email, criar_user,
                      get_creditos, add_creditos, debita_creditos,
                      get_saldo, add_saldo, debita_saldo)

log = logging.getLogger('arena')

arena_bp = Blueprint('arena', __name__, url_prefix='/arena')

# Catálogo de jogos do portal (MODOS x JOGOS). Só 'blocos' ativo no L0.
JOGOS = [
    {'id': 'blocos',   'nome': 'Quebra-Blocos', 'emoji': '🧱', 'ativo': True,  'url': '/arena/jogar'},
    {'id': 'bolhas',   'nome': 'Tiro de Bolhas','emoji': '🎯', 'ativo': False, 'url': ''},
    {'id': 'combina',  'nome': 'Combina-3',     'emoji': '🍬', 'ativo': False, 'url': ''},
    {'id': 'labirinto','nome': 'Labirinto',     'emoji': '🧩', 'ativo': False, 'url': ''},
    {'id': 'quiz',     'nome': 'Quiz Relâmpago','emoji': '❓', 'ativo': False, 'url': ''},
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
    if u:
        _arena_reconciliar_efi(u['id'])   # confirma compra Efí que ficou pendente (pagou e fechou)
    cred = get_creditos(u['id']) if u else 0
    sal = get_saldo(u['id']) if u else 0.0
    return render_template('arena/portal.html', user=u, creditos=cred, saldo=sal, jogos=JOGOS)


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
    # PIX opcional no cadastro (pra pagar o prêmio depois). Se vier inválido, NÃO bloqueia o
    # cadastro — a gente pede de novo (com validação) na hora do saque.
    pchave_raw = (request.form.get('pix_chave') or '').strip()
    if pchave_raw:
        _t, _c, _e = _pix_normaliza(request.form.get('pix_tipo'), pchave_raw)
        if not _e and _c:
            conn = get_arena_db()
            conn.execute('UPDATE arena_users SET pix_chave=?, pix_tipo=? WHERE id=?',
                         (_c, (request.form.get('pix_tipo') or '').strip().upper(), uid))
            conn.commit(); conn.close()
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
# ECONOMIA: 1 ficha = R$1 (FICHA_VALOR). Preço/ficha = R$1 em TODO pacote, então o prêmio
# (2*aposta*FICHA_VALOR*(1-rake)) nunca supera o arrecadado — a casa sempre fica com o rake.
PACOTES = {
    'inicio': {'creditos': 5,  'preco': 5.0,   'rotulo': '5 fichas',  'bonus': ''},
    'turbo':  {'creditos': 20, 'preco': 20.0,  'rotulo': '20 fichas', 'bonus': ''},
    'mestre': {'creditos': 50, 'preco': 50.0,  'rotulo': '50 fichas', 'bonus': ''},
}

_ASAAS_BASE = 'https://api.asaas.com/v3'


def _asaas_req(method, endpoint, data=None):
    try:
        r = _requests.request(method, f'{_ASAAS_BASE}{endpoint}',
            headers={'access_token': os.environ.get('ASAAS_API_KEY', ''),
                     'Content-Type': 'application/json'},
            json=data, timeout=20)
        out = r.json() if r.content else {}
        # 5xx = a operação PODE ter sido processada (ambíguo) — marca pra não estornar às cegas
        if r.status_code >= 500:
            out['_neterr'] = True
        return out
    except Exception as e:
        # timeout / conexão caiu = AMBÍGUO: o PIX pode ter saído. NUNCA estornar automático nisso.
        return {'error': str(e), '_neterr': True}


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
    """Credita as fichas de UMA compra ATÔMICO + idempotente: o flip de status='pago' E o
    crédito das fichas acontecem na MESMA transação (1 conexão, 1 commit). Assim um crash no
    meio nunca deixa a compra 'pago' com 0 fichas (bug irrecuperável que o red-team pegou)."""
    conn = get_arena_db()
    cur = conn.execute("UPDATE arena_compras SET status='pago' WHERE id=? AND status='pendente'",
                       (compra_id,))
    if cur.rowcount == 0:          # já confirmada por outra via (idempotente)
        conn.commit(); conn.close()
        return False
    conn.execute("UPDATE arena_users SET creditos=COALESCE(creditos,0)+"
                 "(SELECT creditos FROM arena_compras WHERE id=?) "
                 "WHERE id=(SELECT user_id FROM arena_compras WHERE id=?)", (compra_id, compra_id))
    conn.commit()   # status + crédito juntos
    row = conn.execute('SELECT user_id, creditos FROM arena_compras WHERE id=?', (compra_id,)).fetchone()
    conn.close()
    log.info(f'[Arena] Compra {compra_id} PAGA — +{row["creditos"] if row else "?"} fichas')
    return True


def arena_webhook_confirmar(external_ref, payment_id=''):
    """Chamado pelo webhook global (app.py) p/ refs 'arena_<compra_id>' (compra de fichas Asaas).
    Refs de PAYOUT ('arena_premio_...') caem no split e viram não-int → ignorados aqui (seguro)."""
    try:
        cid = int(str(external_ref).split('_')[1])
    except (IndexError, ValueError):
        return False
    return _arena_confirmar_compra(cid)


# ── Efí: dinheiro que ENTRA (compra de ficha). Reusa efi_pix.py (mesma cobrança do SlotZap,
#    sem tocar na rifa da Jaya). Payout (saque do prêmio) continua no Asaas. ──
def _efi_ok():
    """True se a Efí está configurada em produção (Railway). Local sem cert → cai no Asaas."""
    try:
        return bool(efi_pix and efi_pix.configurado())
    except Exception:
        return False


def _qr_png_b64(texto):
    """Gera o QR do copia-e-cola no SERVIDOR (a Efí só devolve o texto, não a imagem)."""
    if not texto:
        return ''
    try:
        import qrcode, io, base64
        buf = io.BytesIO()
        qrcode.make(texto).save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        log.warning(f'[Arena] QR falhou: {e}')
        return ''


def _arena_reconciliar_efi(user_id):
    """Confirma compras Efí pendentes desse usuário (rede de segurança: pagou e fechou a página).
    Chamado ao abrir o portal — barato (só consulta se houver pendência Efí)."""
    if not _efi_ok():
        return
    try:
        conn = get_arena_db()
        rows = conn.execute("SELECT id, charge_id FROM arena_compras WHERE user_id=? "
                            "AND status='pendente' AND gateway='efi' AND charge_id IS NOT NULL "
                            "AND charge_id<>'' ORDER BY id DESC LIMIT 3", (user_id,)).fetchall()
        conn.close()
        for r in rows:
            if efi_pix.consultar_cobranca(r['charge_id']).get('pago'):
                _arena_confirmar_compra(r['id'])
    except Exception as e:
        log.warning(f'[Arena] reconciliar Efí: {e}')


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
        if _efi_ok():
            # dinheiro ENTRA pela Efí (1,19% no R$5 vs R$0,99-1,99 fixo do Asaas). Sem CPF.
            conn = get_arena_db()
            cur = conn.execute(
                'INSERT INTO arena_compras(user_id,pacote,creditos,valor,status,gateway,ext_ref,criado_em) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (u['id'], pacote, p['creditos'], p['preco'], 'pendente', 'efi', '', datetime.now().isoformat()))
            compra_id = cur.lastrowid
            conn.commit(); conn.close()
            cob = efi_pix.criar_cobranca(p['preco'], f'Arena — {p["rotulo"]}')
            if cob.get('erro') or not cob.get('txid'):
                erro = 'Não foi possível gerar o PIX agora. Tente de novo.'
                log.error(f'[Arena] Efí cobrança falhou: {cob.get("erro")}')
            else:
                conn = get_arena_db()
                conn.execute('UPDATE arena_compras SET charge_id=?, copia_cola=?, ext_ref=? WHERE id=?',
                             (cob['txid'], cob.get('copia_cola', ''), f'arena_{compra_id}', compra_id))
                conn.commit(); conn.close()
                return redirect(f'/arena/pix/{compra_id}')
        else:
            # fallback Asaas (exige CPF) — só se a Efí não estiver configurada
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
                        'INSERT INTO arena_compras(user_id,pacote,creditos,valor,status,gateway,ext_ref,criado_em) '
                        'VALUES(?,?,?,?,?,?,?,?)',
                        (u['id'], pacote, p['creditos'], p['preco'], 'pendente', 'asaas', '', datetime.now().isoformat()))
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
    return render_template('arena/checkout.html', user=u, pacote=pacote, p=p, erro=erro, efi=_efi_ok())


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
    if compra['status'] == 'pendente':
        if compra.get('gateway') == 'efi':
            copia = compra.get('copia_cola') or ''
            qr = _qr_png_b64(copia)                       # QR gerado no servidor
        elif compra['charge_id']:
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
    cid = compra['charge_id']
    if not cid:
        return jsonify({'pago': False})
    pago = False
    if compra.get('gateway') == 'efi':
        if _efi_ok():
            try:
                pago = bool(efi_pix.consultar_cobranca(cid).get('pago'))
            except Exception:
                pago = False
    else:
        pay = _asaas_req('GET', f'/payments/{cid}')
        pago = (pay.get('status') or '').upper() in ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH')
    if pago:
        _arena_confirmar_compra(compra_id)
        return jsonify({'pago': True, 'creditos': get_creditos(u['id'])})
    return jsonify({'pago': False})


# ══════════════════════════════════════════════════════════════════════════════
# LOTE 3 — Sala VALENDO (duelo assíncrono por mesma semente).
# A ficha entra, o SERVIDOR sorteia a semente e re-simula as jogadas (arena_game),
# maior pontuação leva o PRÊMIO pro SALDO (R$). Tudo atômico e idempotente.
# ══════════════════════════════════════════════════════════════════════════════
FICHA_VALOR = 1.0      # 1 ficha = R$1 (crédito em reais). Deve ser <= menor preço/ficha dos pacotes.
ARENA_RAKE = 0.15      # taxa de serviço da casa
STAKES = [2, 5, 10, 20, 50]   # entradas de duelo permitidas (em fichas = R$)

# Jogos que rodam VALENDO (têm motor server-side de placar). Cada um: motor + template + rótulo.
JOGOS_VALENDO = {
    'blocos':   {'motor': arena_game,          'tpl': 'arena/jogo_valendo.html',
                 'nome': 'Quebra-Blocos', 'emoji': '🧱'},
}


def _pontuar(jogo, seed, moves):
    """Despacha pro motor server-side do jogo. Fallback = blocos."""
    cfg = JOGOS_VALENDO.get(jogo) or JOGOS_VALENDO['blocos']
    return cfg['motor'].pontuar(seed, moves)


def _resultado_dict(s):
    return {'status': s['status'], 'criador_pontos': s['criador_pontos'],
            'oponente_pontos': s['oponente_pontos'], 'vencedor_id': s['vencedor_id'],
            'premio': s['premio'], 'aposta': s['aposta']}


def sala_criar(user_id, aposta=1, jogo='blocos'):
    """Cria sala valendo: debita a ficha e o SERVIDOR sorteia a semente (ninguém pré-escaneia).
    Retorna (token, seed) ou (None, erro)."""
    aposta = int(aposta) if int(aposta) >= 1 else 1
    if jogo not in JOGOS_VALENDO:
        jogo = 'blocos'
    if not debita_creditos(user_id, aposta):
        return (None, 'Você não tem fichas. Compre pra jogar valendo.')
    token = secrets.token_urlsafe(9)
    seed = random.randint(0, 0xFFFFFFFF)
    conn = get_arena_db()
    conn.execute('INSERT INTO arena_salas(token,jogo,seed,aposta,criador_id,status,criado_em) '
                 'VALUES(?,?,?,?,?,?,?)',
                 (token, jogo, seed, aposta, user_id, 'criada', datetime.now().isoformat()))
    conn.commit(); conn.close()
    return (token, seed)


def sala_confirmar_compartilhar(token, user_id):
    """DESAFIAR: o criador confirma que compartilhou e SE COMPROMETE a jogar
    ('criada' -> 'esperando_criador'). Daí em diante o placar é UM TIRO SÓ, sem reroll
    (não dá mais pra jogar, ver placar ruim e cancelar). Atômico via rowcount."""
    conn = get_arena_db()
    cur = conn.execute("UPDATE arena_salas SET status='esperando_criador', criador_em=? "
                       "WHERE token=? AND criador_id=? AND status='criada' AND oponente_id IS NULL",
                       (datetime.now().isoformat(), token, user_id))
    conn.commit(); ok = cur.rowcount == 1; conn.close()
    if not ok:
        return {'erro': 'Não deu pra começar (a sala já está em andamento ou não é sua).'}
    return {'ok': True}


def _crosscheck(token, quem, servidor, cliente):
    """Compara o placar que o servidor recalculou com o que o cliente diz ter feito.
    Divergência = possível bug de porte JS/Python OU tentativa de fraude — loga alto pra
    a gente pegar ANTES de mover dinheiro (o que vale é sempre o do SERVIDOR)."""
    try:
        if cliente is not None and int(cliente) != int(servidor):
            log.warning(f'[Arena] PLACAR DIVERGE sala {token} {quem}: '
                        f'servidor={servidor} cliente={cliente} — investigar!')
    except Exception:
        pass


JOGO_JANELA_S = 210   # segundos do carimbo (semente entregue) até o envio. > 150s do jogo + folga de
                      # carregamento/rede. Fecha o solver-offline: quem "pensa" além disso = forfeit.


def _dentro_da_janela(base_iso):
    """True se o envio chegou dentro da janela de jogo, a partir do carimbo do SERVIDOR (criador_em/
    oponente_em). O relógio de 90s do cliente não vale nada sozinho — isto amarra o placar ao tempo
    real: sem isto, dá pra ler a semente, rodar um solver com tempo infinito e mandar o placar ótimo.
    Sem carimbo confiável, não pune (raro)."""
    if not base_iso:
        return True
    try:
        return (datetime.now() - datetime.fromisoformat(base_iso)).total_seconds() <= JOGO_JANELA_S
    except Exception:
        return True


def sala_registrar_jogada_criador(token, user_id, moves, cliente_score=None):
    """Criador submete as JOGADAS. Servidor pontua (uma chance só) e abre a sala p/ desafio."""
    conn = get_arena_db()
    row = conn.execute('SELECT * FROM arena_salas WHERE token=?', (token,)).fetchone()
    if not row:
        conn.close(); return {'erro': 'Sala não encontrada.'}
    s = dict(row)
    if s['criador_id'] != user_id:
        conn.close(); return {'erro': 'Essa sala não é sua.'}
    if s['status'] != 'esperando_criador' or s['criador_pontos'] is not None:
        conn.close(); return {'erro': 'A partida ainda não começou ou você já jogou.'}
    pontos = _pontuar(s['jogo'], s['seed'], moves)
    if pontos is None:
        pontos = 0   # jogada inválida OU acima do teto de jogadas = forfeit (uma chance só)
        log.warning(f'[Arena] Sala {token}: jogada do criador invalida -> 0 (poss. bug/tamper)')
    if not _dentro_da_janela(s.get('criador_em')):
        pontos = 0   # enviou fora dos ~90s reais = forfeit (poss. solver offline)
        log.warning(f'[Arena] Sala {token}: criador enviou fora da janela ({JOGO_JANELA_S}s) -> forfeit')
    _crosscheck(token, 'criador', pontos, cliente_score)
    conn.execute("UPDATE arena_salas SET criador_pontos=?, criador_moves=?, status='aguardando' "
                 "WHERE token=? AND status='esperando_criador'",
                 (pontos, json.dumps(moves)[:20000], token))
    conn.commit(); conn.close()
    return {'ok': True, 'pontos': pontos, 'token': token}


def sala_entrar(token, user_id):
    """Oponente ENTRA (paga ANTES de jogar, pra não poder enviar só as vitórias):
    claim atômico -> debita ficha. Retorna {seed, aposta} ou {erro}. Sem-ficha libera a sala.
    Reentrada (refresh) devolve a semente sem cobrar de novo."""
    conn = get_arena_db()
    row = conn.execute('SELECT * FROM arena_salas WHERE token=?', (token,)).fetchone()
    if not row:
        conn.close(); return {'erro': 'Sala não encontrada.'}
    s = dict(row)
    # já entrou (pagou) e ainda não enviou -> reabre a MESMA partida, sem cobrar de novo
    if s['status'] == 'apurando' and s['oponente_id'] == user_id and s['oponente_pontos'] is None:
        conn.close(); return {'ok': True, 'seed': s['seed'], 'aposta': s['aposta']}
    # claim atômico: só se aguardando, sem oponente, e NÃO é a sua própria sala
    cur = conn.execute("UPDATE arena_salas SET status='apurando', oponente_id=?, oponente_em=? "
                       "WHERE token=? AND status='aguardando' AND oponente_id IS NULL AND criador_id<>?",
                       (user_id, datetime.now().isoformat(), token, user_id))
    conn.commit(); claimed = cur.rowcount == 1; conn.close()
    if not claimed:
        return {'erro': 'Essa sala não está disponível (já tem oponente, é sua, ou já foi jogada).'}
    if not debita_creditos(user_id, s['aposta']):
        conn = get_arena_db()
        conn.execute("UPDATE arena_salas SET status='aguardando', oponente_id=NULL "
                     "WHERE token=? AND status='apurando' AND oponente_id=?", (token, user_id))
        conn.commit(); conn.close()
        return {'erro': 'Você não tem fichas. Compre pra entrar.'}
    return {'ok': True, 'seed': s['seed'], 'aposta': s['aposta']}


def sala_enviar(token, user_id, moves, cliente_score=None):
    """Oponente ENVIA as jogadas (depois de já ter entrado/pago): pontua e apura."""
    conn = get_arena_db()
    row = conn.execute('SELECT * FROM arena_salas WHERE token=?', (token,)).fetchone()
    if not row:
        conn.close(); return {'erro': 'Sala não encontrada.'}
    s = dict(row)
    if s['oponente_id'] != user_id or s['status'] not in ('apurando', 'paga'):
        conn.close(); return {'erro': 'Você não está nessa partida.'}
    if s['oponente_pontos'] is not None:
        conn.close(); return _apurar_sala(token)   # já enviou -> devolve o resultado (idempotente)
    conn.close()
    pontos = _pontuar(s['jogo'], s['seed'], moves)
    if pontos is None:
        pontos = 0
        log.warning(f'[Arena] Sala {token}: jogada do oponente invalida -> 0 (poss. bug/tamper)')
    if not _dentro_da_janela(s.get('oponente_em')):
        pontos = 0   # enviou fora dos ~90s reais = forfeit (poss. solver offline)
        log.warning(f'[Arena] Sala {token}: oponente enviou fora da janela ({JOGO_JANELA_S}s) -> forfeit')
    _crosscheck(token, 'oponente', pontos, cliente_score)
    conn = get_arena_db()
    conn.execute("UPDATE arena_salas SET oponente_pontos=?, oponente_moves=? "
                 "WHERE token=? AND oponente_pontos IS NULL", (pontos, json.dumps(moves)[:20000], token))
    conn.commit(); conn.close()
    return _apurar_sala(token)


def _apurar_sala(token):
    """Compara e paga o vencedor (SALDO) — ou devolve fichas no empate. IDEMPOTENTE:
    a transição 'apurando'->'paga' com guarda de rowcount credita EXATAMENTE 1 vez."""
    conn = get_arena_db()
    row = conn.execute('SELECT * FROM arena_salas WHERE token=?', (token,)).fetchone()
    if not row:
        conn.close(); return {'erro': 'Sala sumiu.'}
    s = dict(row)
    if s['status'] == 'paga':          # já apurada — devolve o resultado (idempotente)
        conn.close(); return _resultado_dict(s)
    if s['status'] != 'apurando' or s['criador_pontos'] is None or s['oponente_pontos'] is None:
        conn.close(); return {'erro': 'Ainda não dá pra apurar.'}
    cp, op = s['criador_pontos'], s['oponente_pontos']
    agora = datetime.now().isoformat()
    # ATÔMICO: o flip 'apurando'->'paga' E o crédito do dinheiro na MESMA transação/commit.
    # Se cair no meio (deploy/OOM Railway), ou os dois acontecem ou nenhum — nunca 'paga' com R$0
    # no saldo do vencedor (bug irrecuperável que o red-team pegou; mesmo padrão de _arena_confirmar_compra).
    if cp == op:
        cur = conn.execute("UPDATE arena_salas SET status='paga', vencedor_id=NULL, premio=0, apurado_em=? "
                           "WHERE token=? AND status='apurando'", (agora, token))
        if cur.rowcount == 1:
            conn.execute('UPDATE arena_users SET creditos=COALESCE(creditos,0)+? WHERE id=?', (s['aposta'], s['criador_id']))
            conn.execute('UPDATE arena_users SET creditos=COALESCE(creditos,0)+? WHERE id=?', (s['aposta'], s['oponente_id']))
            conn.commit()
            log.info(f'[Arena] Sala {token} EMPATE — fichas devolvidas aos dois')
        else:
            conn.commit()
    else:
        venc = s['criador_id'] if cp > op else s['oponente_id']
        pote = 2 * s['aposta'] * FICHA_VALOR
        premio = round(pote * (1 - ARENA_RAKE), 2)
        cur = conn.execute("UPDATE arena_salas SET status='paga', vencedor_id=?, premio=?, rake=?, apurado_em=? "
                           "WHERE token=? AND status='apurando'",
                           (venc, premio, round(pote * ARENA_RAKE, 2), agora, token))
        if cur.rowcount == 1:
            conn.execute('UPDATE arena_users SET saldo=ROUND(COALESCE(saldo,0)+?,2) WHERE id=?', (premio, venc))
            conn.commit()
            log.info(f'[Arena] Sala {token} vencedor={venc} +R${premio:.2f} saldo')
        else:
            conn.commit()
    row2 = conn.execute('SELECT * FROM arena_salas WHERE token=?', (token,)).fetchone()
    conn.close()
    return _resultado_dict(dict(row2))


REAP_MIN = 5          # min sem enviar depois de entrar/pagar = oponente perde por WO
CRIADOR_REAP_MIN = 30 # criador criou/compartilhou e sumiu sem jogar (sem oponente) = devolve a ficha


def _reap_sala_se_preciso(token):
    """Reaper LAZY (roda ao abrir a página da sala, sem scheduler). Duas limpezas:
    (a) oponente que ENTROU (pagou) e não enviou em REAP_MIN = forfeit, o criador leva;
    (b) criador que criou/compartilhou e sumiu SEM jogar (sem oponente) em CRIADOR_REAP_MIN =
        devolve a ficha e cancela (senão a ficha fica presa pra sempre)."""
    conn = get_arena_db()
    s = conn.execute('SELECT status, criador_id, criador_pontos, criador_em, oponente_id, '
                     'oponente_pontos, oponente_em, aposta, criado_em FROM arena_salas WHERE token=?',
                     (token,)).fetchone()
    if not s:
        conn.close(); return
    s = dict(s)
    # (a) oponente entrou e não enviou -> WO
    if s['status'] == 'apurando' and s['oponente_pontos'] is None and s.get('oponente_em'):
        try:
            velho = datetime.fromisoformat(s['oponente_em']) < (datetime.now() - timedelta(minutes=REAP_MIN))
        except Exception:
            velho = False
        if velho:
            conn.execute("UPDATE arena_salas SET oponente_pontos=0 WHERE token=? AND oponente_pontos IS NULL", (token,))
            conn.commit(); conn.close()
            log.info(f'[Arena] Sala {token}: oponente abandonou (WO) -> forfeit, apurando')
            _apurar_sala(token)
            return
    # (b) criador criou/compartilhou e sumiu sem jogar -> devolve a ficha
    if s['status'] in ('criada', 'esperando_criador') and s['criador_pontos'] is None and s['oponente_id'] is None:
        base = s.get('criador_em') or s.get('criado_em')
        try:
            velho = bool(base) and datetime.fromisoformat(base) < (datetime.now() - timedelta(minutes=CRIADOR_REAP_MIN))
        except Exception:
            velho = False
        if velho:
            cur = conn.execute("UPDATE arena_salas SET status='cancelada' WHERE token=? AND "
                               "status IN ('criada','esperando_criador') AND criador_pontos IS NULL "
                               "AND oponente_id IS NULL", (token,))
            conn.commit(); ok = cur.rowcount == 1; conn.close()
            if ok:
                add_creditos(s['criador_id'], s['aposta'] or 1)
                log.info(f'[Arena] Sala {token}: criador sumiu sem jogar -> ficha devolvida')
            return
    conn.close()


def sala_cancelar(token, user_id):
    """Criador cancela e recupera a ficha — SÓ ANTES DE JOGAR (criador_pontos IS NULL) e sem
    oponente. Depois de jogar NÃO cancela: é o que mata o reroll (jogar, ver placar ruim, cancelar
    e tentar de novo). Atômico via rowcount."""
    conn = get_arena_db()
    row = conn.execute('SELECT aposta FROM arena_salas WHERE token=? AND criador_id=?',
                       (token, user_id)).fetchone()
    if not row:
        conn.close(); return False
    aposta = row['aposta'] or 1
    cur = conn.execute("UPDATE arena_salas SET status='cancelada' "
                       "WHERE token=? AND criador_id=? AND status IN ('criada','esperando_criador') "
                       "AND criador_pontos IS NULL AND oponente_id IS NULL",
                       (token, user_id))
    conn.commit(); ok = cur.rowcount == 1; conn.close()
    if ok:
        add_creditos(user_id, aposta)
    return ok


# ══════════════════════════════════════════════════════════════════════════════
# MESAS FREE-FOR-ALL (N jogadores, "mesa do dia"). Mesma semente pra todos, assíncrono:
# entra e joga quando quiser dentro da janela; fecha -> ranqueia -> VENCEDOR LEVA TUDO (−rake).
# Reusa o motor de placar + anti-cheat de tempo do duelo. Pagamento atômico/idempotente.
# ══════════════════════════════════════════════════════════════════════════════
MESA_JANELA_H = 24        # janela padrão ("mesa do dia"). Longa de propósito: resolve cold-start.
MESA_MIN_JOGADORES = 2    # menos que isso ao fechar = ninguém ganha, devolve as entradas.
MESA_MAX_VAGAS = 30       # teto de vagas padrão.


def mesa_criar(user_id, entrada, jogo='blocos', janela_h=MESA_JANELA_H, max_vagas=MESA_MAX_VAGAS):
    """Cria a mesa e JÁ inscreve o criador (paga a entrada). Servidor sorteia a semente."""
    entrada = int(entrada) if int(entrada) in STAKES else 5
    if jogo not in JOGOS_VALENDO:
        jogo = 'blocos'
    if not debita_creditos(user_id, entrada):
        return (None, 'Você não tem fichas. Compre pra criar a mesa.')
    token = secrets.token_urlsafe(9)
    seed = random.randint(0, 0xFFFFFFFF)
    agora = datetime.now()
    conn = get_arena_db()
    cur = conn.execute('INSERT INTO arena_mesas(token,jogo,seed,entrada,max_vagas,criador_id,status,criado_em,fecha_em) '
                       'VALUES(?,?,?,?,?,?,?,?,?)',
                       (token, jogo, seed, entrada, int(max_vagas), user_id, 'aberta',
                        agora.isoformat(), (agora + timedelta(hours=janela_h)).isoformat()))
    mesa_id = cur.lastrowid
    conn.execute('INSERT INTO arena_mesa_jogadores(mesa_id,user_id,entrou_em) VALUES(?,?,?)',
                 (mesa_id, user_id, agora.isoformat()))
    conn.commit(); conn.close()
    return (token, seed)


def mesa_entrar(token, user_id):
    """Entra na mesa (paga a entrada, pega a semente). Reentrada não re-cobra. Corrida (UNIQUE) estorna."""
    conn = get_arena_db()
    m = conn.execute('SELECT * FROM arena_mesas WHERE token=?', (token,)).fetchone()
    if not m:
        conn.close(); return {'erro': 'Mesa não encontrada.'}
    m = dict(m)
    ja = conn.execute('SELECT id FROM arena_mesa_jogadores WHERE mesa_id=? AND user_id=?',
                      (m['id'], user_id)).fetchone()
    if ja:
        conn.close(); return {'ok': True, 'seed': m['seed'], 'entrada': m['entrada']}   # já dentro
    if m['status'] != 'aberta':
        conn.close(); return {'erro': 'Essa mesa já fechou.'}
    if m['max_vagas'] and m['max_vagas'] > 0:
        n = conn.execute('SELECT COUNT(*) c FROM arena_mesa_jogadores WHERE mesa_id=?', (m['id'],)).fetchone()['c']
        if n >= m['max_vagas']:
            conn.close(); return {'erro': 'Mesa lotada.'}
    conn.close()
    if not debita_creditos(user_id, m['entrada']):
        return {'erro': 'Você não tem fichas. Compre pra entrar.'}
    conn = get_arena_db()
    try:
        conn.execute('INSERT INTO arena_mesa_jogadores(mesa_id,user_id,entrou_em) VALUES(?,?,?)',
                     (m['id'], user_id, datetime.now().isoformat()))
        conn.commit(); conn.close()
    except Exception:
        conn.close(); add_creditos(user_id, m['entrada'])   # corrida: já entrou -> estorna esta cobrança
    return {'ok': True, 'seed': m['seed'], 'entrada': m['entrada']}


def mesa_registrar_jogada(token, user_id, moves, cliente_score=None):
    """Jogador da mesa envia as jogadas (UM tiro). Servidor pontua + confere a janela de tempo."""
    conn = get_arena_db()
    m = conn.execute('SELECT * FROM arena_mesas WHERE token=?', (token,)).fetchone()
    if not m:
        conn.close(); return {'erro': 'Mesa não encontrada.'}
    m = dict(m)
    j = conn.execute('SELECT * FROM arena_mesa_jogadores WHERE mesa_id=? AND user_id=?',
                     (m['id'], user_id)).fetchone()
    if not j:
        conn.close(); return {'erro': 'Você não está nessa mesa.'}
    j = dict(j)
    if j['jogou_em'] is not None or j['pontos'] is not None:
        conn.close(); return {'erro': 'Você já jogou essa mesa.'}
    if m['status'] != 'aberta':
        conn.close(); return {'erro': 'Essa mesa já fechou.'}
    conn.close()
    pontos = _pontuar(m['jogo'], m['seed'], moves)
    if pontos is None:
        pontos = 0
        log.warning(f'[Arena] Mesa {token}: jogada invalida -> 0 (poss. bug/tamper)')
    if not _dentro_da_janela(j.get('entrou_em')):
        pontos = 0
        log.warning(f'[Arena] Mesa {token}: envio fora da janela ({JOGO_JANELA_S}s) -> forfeit (poss. solver)')
    _crosscheck(token, 'mesa', pontos, cliente_score)
    conn = get_arena_db()
    conn.execute("UPDATE arena_mesa_jogadores SET pontos=?, moves=?, jogou_em=? "
                 "WHERE mesa_id=? AND user_id=? AND jogou_em IS NULL",
                 (pontos, json.dumps(moves)[:20000], datetime.now().isoformat(), m['id'], user_id))
    conn.commit(); conn.close()
    return {'ok': True, 'pontos': pontos}


def mesa_apurar(token):
    """Fecha a mesa: ranqueia e paga o TOPO (winner-take-all). ATÔMICO (flip+crédito na mesma
    transação) + IDEMPOTENTE (rowcount). <MIN jogadores = devolve entradas. Empate no 1º = divide."""
    conn = get_arena_db()
    m = conn.execute('SELECT * FROM arena_mesas WHERE token=?', (token,)).fetchone()
    if not m:
        conn.close(); return {'erro': 'Mesa sumiu.'}
    m = dict(m)
    if m['status'] in ('paga', 'cancelada'):
        conn.close(); return _mesa_resultado(token)
    if m['status'] != 'aberta':
        conn.close(); return {'erro': 'Mesa em processamento.'}
    jogs = [dict(r) for r in conn.execute('SELECT * FROM arena_mesa_jogadores WHERE mesa_id=?', (m['id'],)).fetchall()]
    n = len(jogs)
    agora = datetime.now().isoformat()
    # poucos jogadores -> cancela e devolve as entradas (na mesma transação do flip)
    if n < MESA_MIN_JOGADORES:
        cur = conn.execute("UPDATE arena_mesas SET status='cancelada', apurado_em=? WHERE token=? AND status='aberta'",
                           (agora, token))
        if cur.rowcount == 1:
            for j in jogs:
                conn.execute('UPDATE arena_users SET creditos=COALESCE(creditos,0)+? WHERE id=?', (m['entrada'], j['user_id']))
            log.info(f'[Arena] Mesa {token} cancelada (poucos jogadores) — entradas devolvidas')
        conn.commit(); conn.close(); return _mesa_resultado(token)
    # ranking: quem não jogou conta 0. empate no topo divide o prêmio.
    def sc(j): return j['pontos'] if j['pontos'] is not None else 0
    topo = max(sc(j) for j in jogs)
    vencedores = [j for j in jogs if sc(j) == topo]
    pote = n * m['entrada'] * FICHA_VALOR
    premio_total = round(pote * (1 - ARENA_RAKE), 2)
    premio_cada = round(premio_total / len(vencedores), 2)
    venc_id = vencedores[0]['user_id'] if len(vencedores) == 1 else None
    cur = conn.execute("UPDATE arena_mesas SET status='paga', premio=?, rake=?, vencedor_id=?, apurado_em=? "
                       "WHERE token=? AND status='aberta'",
                       (premio_total, round(pote * ARENA_RAKE, 2), venc_id, agora, token))
    if cur.rowcount == 1:
        for jv in vencedores:
            conn.execute('UPDATE arena_users SET saldo=ROUND(COALESCE(saldo,0)+?,2) WHERE id=?', (premio_cada, jv['user_id']))
            conn.execute('UPDATE arena_mesa_jogadores SET premio=?, posicao=1 WHERE mesa_id=? AND user_id=?',
                         (premio_cada, m['id'], jv['user_id']))
        log.info(f'[Arena] Mesa {token} PAGA — {len(vencedores)} vencedor(es) R${premio_cada:.2f} cada (pote R${pote:.2f})')
    conn.commit(); conn.close()
    return _mesa_resultado(token)


def mesa_encerrar(token, user_id):
    """O DONO encerra a mesa na hora (fecha o balcão e apura). Só o criador pode."""
    conn = get_arena_db()
    m = conn.execute('SELECT criador_id, status FROM arena_mesas WHERE token=?', (token,)).fetchone()
    conn.close()
    if not m or m['criador_id'] != user_id:
        return {'erro': 'Só quem criou a mesa pode encerrar.'}
    if m['status'] != 'aberta':
        return _mesa_resultado(token)
    return mesa_apurar(token)


def _reap_mesa_se_preciso(token):
    """Lazy (ao abrir a página): se a janela fechou, apura a mesa."""
    conn = get_arena_db()
    m = conn.execute('SELECT status, fecha_em FROM arena_mesas WHERE token=?', (token,)).fetchone()
    conn.close()
    if not m:
        return
    m = dict(m)
    if m['status'] == 'aberta' and m.get('fecha_em'):
        try:
            if datetime.fromisoformat(m['fecha_em']) < datetime.now():
                mesa_apurar(token)
        except Exception:
            pass


def _mesa_resultado(token):
    """Estado + ranking da mesa (pro placar/leaderboard)."""
    conn = get_arena_db()
    m = conn.execute('SELECT * FROM arena_mesas WHERE token=?', (token,)).fetchone()
    if not m:
        conn.close(); return {'erro': 'Mesa sumiu.'}
    m = dict(m)
    jogs = [dict(r) for r in conn.execute(
        'SELECT j.user_id, j.pontos, j.jogou_em, j.premio, u.nome FROM arena_mesa_jogadores j '
        'JOIN arena_users u ON u.id=j.user_id WHERE j.mesa_id=? '
        'ORDER BY (j.pontos IS NULL), j.pontos DESC, j.jogou_em', (m['id'],)).fetchall()]
    conn.close()
    return {'status': m['status'], 'entrada': m['entrada'], 'premio': m['premio'],
            'vencedor_id': m['vencedor_id'], 'jogadores': jogs, 'total': len(jogs)}


# ── rotas (finas — auth + chama o miolo) ──────────────────────────────────────
@arena_bp.route('/valendo')
@arena_login_required
def valendo_page():
    u = _cur()
    jogos = [{'id': k, 'nome': v['nome'], 'emoji': v['emoji']} for k, v in JOGOS_VALENDO.items()]
    return render_template('arena/valendo.html', user=u, creditos=get_creditos(u['id']),
                           stakes=STAKES, rake=ARENA_RAKE, ficha=FICHA_VALOR, jogos=jogos)


@arena_bp.route('/valendo/criar', methods=['POST'])
@arena_login_required
def valendo_criar():
    u = _cur()
    body = request.get_json(silent=True) or {}
    try:
        aposta = int(body.get('aposta') or 5)
    except Exception:
        aposta = 5
    if aposta not in STAKES:
        return jsonify({'erro': 'Valor de entrada inválido.'}), 400
    jogo = body.get('jogo') or 'blocos'
    if jogo not in JOGOS_VALENDO:
        return jsonify({'erro': 'Jogo inválido.'}), 400
    token, seed = sala_criar(u['id'], aposta, jogo)
    if token is None:
        return jsonify({'erro': seed}), 400
    return jsonify({'ok': True, 'token': token, 'seed': seed})


@arena_bp.route('/sala/<token>')
@arena_login_required
def sala_page(token):
    """Roteador de estado da sala: serve o JOGO quando é a vez de jogar, senão a casca
    (aceitar desafio / aguardando / resultado)."""
    u = _cur()
    _reap_sala_se_preciso(token)   # se o oponente abandonou, resolve por WO antes de renderizar
    conn = get_arena_db()
    row = conn.execute('SELECT * FROM arena_salas WHERE token=?', (token,)).fetchone()
    conn.close()
    if not row:
        return render_template('arena/sala.html', estado='inexistente', sala=None, user=u, token=token)
    s = dict(row)
    ehcriador = (s['criador_id'] == u['id'])
    ehoponente = (s['oponente_id'] == u['id'])
    # ── é a vez de JOGAR? serve o jogo valendo (template conforme o jogo da sala) ──
    # Criador só joga DEPOIS de compartilhar/comprometer (status 'esperando_criador') — sem reroll.
    _tpl = (JOGOS_VALENDO.get(s['jogo']) or JOGOS_VALENDO['blocos'])['tpl']
    if ehcriador and s['status'] == 'esperando_criador' and s['criador_pontos'] is None:
        return render_template(_tpl, token=token, seed=s['seed'],
                               aposta=s['aposta'], endpoint='/arena/sala/' + token + '/jogar', alvo=None)
    if ehoponente and s['status'] == 'apurando' and s['oponente_pontos'] is None:
        # alvo=None: NÃO revela o placar do criador antes do oponente jogar (vantagem de info)
        return render_template(_tpl, token=token, seed=s['seed'],
                               aposta=s['aposta'], endpoint='/arena/sala/' + token + '/enviar', alvo=None)
    # ── casca de estado ──
    if s['status'] == 'cancelada':
        estado = 'cancelada'
    elif s['status'] == 'paga':
        estado = 'resultado'
    elif s['status'] == 'criada':
        estado = 'criar_compartilhar' if ehcriador else 'criador_preparando'
    elif s['status'] == 'esperando_criador':
        estado = 'criador_preparando'   # o criador cai no serve-jogo acima; aqui é o oponente esperando
    elif s['status'] == 'aguardando':
        estado = 'aguardando' if ehcriador else 'aceitar'
    else:   # apurando, mas não é a vez desse usuário
        estado = 'processando'
    venceu = None
    if s['status'] == 'paga':
        venceu = 'empate' if s['vencedor_id'] is None else ('ganhou' if s['vencedor_id'] == u['id'] else 'perdeu')
    return render_template('arena/sala.html', estado=estado, sala=s, user=u,
                           ehcriador=ehcriador, venceu=venceu, token=token,
                           creditos=get_creditos(u['id']))


@arena_bp.route('/sala/<token>/jogar', methods=['POST'])
@arena_login_required
def sala_jogar_rt(token):
    if request.content_length and request.content_length > 300000:
        return jsonify({'erro': 'Requisição grande demais.'}), 413
    u = _cur()
    body = request.get_json(silent=True) or {}
    r = sala_registrar_jogada_criador(token, u['id'], body.get('moves'), body.get('score'))
    return jsonify(r), (200 if r.get('ok') else 400)


@arena_bp.route('/sala/<token>/confirmar-compartilhar', methods=['POST'])
@arena_login_required
def sala_confirmar_compartilhar_rt(token):
    u = _cur()
    r = sala_confirmar_compartilhar(token, u['id'])
    return jsonify(r), (200 if r.get('ok') else 400)


@arena_bp.route('/sala/<token>/entrar', methods=['POST'])
@arena_login_required
def sala_entrar_rt(token):
    u = _cur()
    r = sala_entrar(token, u['id'])
    return jsonify(r), (200 if not r.get('erro') else 400)


@arena_bp.route('/sala/<token>/enviar', methods=['POST'])
@arena_login_required
def sala_enviar_rt(token):
    if request.content_length and request.content_length > 300000:
        return jsonify({'erro': 'Requisição grande demais.'}), 413
    u = _cur()
    body = request.get_json(silent=True) or {}
    r = sala_enviar(token, u['id'], body.get('moves'), body.get('score'))
    return jsonify(r), (200 if not r.get('erro') else 400)


@arena_bp.route('/sala/<token>/cancelar', methods=['POST'])
@arena_login_required
def sala_cancelar_rt(token):
    u = _cur()
    return jsonify({'ok': sala_cancelar(token, u['id'])})


# ── rotas das MESAS free-for-all ──
@arena_bp.route('/mesa/criar', methods=['GET', 'POST'])
@arena_login_required
def mesa_criar_rt():
    u = _cur()
    if request.method == 'GET':
        return render_template('arena/mesa_criar.html', user=u, creditos=get_creditos(u['id']),
                               stakes=STAKES, rake=ARENA_RAKE, ficha=FICHA_VALOR, maxvagas=MESA_MAX_VAGAS)
    body = request.get_json(silent=True) or {}
    try:
        entrada = int(body.get('entrada') or 5)
    except Exception:
        entrada = 5
    if entrada not in STAKES:
        return jsonify({'erro': 'Valor de entrada inválido.'}), 400
    token, seed = mesa_criar(u['id'], entrada)
    if token is None:
        return jsonify({'erro': seed}), 400
    return jsonify({'ok': True, 'token': token})


@arena_bp.route('/mesa/<token>')
@arena_login_required
def mesa_page(token):
    """Roteador da mesa: serve o JOGO quando é a vez de jogar, senão a casca (entrar/aguardando/resultado)."""
    u = _cur()
    _reap_mesa_se_preciso(token)   # fecha a mesa se a janela passou
    conn = get_arena_db()
    m = conn.execute('SELECT * FROM arena_mesas WHERE token=?', (token,)).fetchone()
    if not m:
        conn.close(); return render_template('arena/mesa.html', estado='inexistente', token=token, user=u)
    m = dict(m)
    j = conn.execute('SELECT * FROM arena_mesa_jogadores WHERE mesa_id=? AND user_id=?',
                     (m['id'], u['id'])).fetchone()
    n = conn.execute('SELECT COUNT(*) c FROM arena_mesa_jogadores WHERE mesa_id=?', (m['id'],)).fetchone()['c']
    conn.close()
    j = dict(j) if j else None
    # é a vez de JOGAR? (inscrito, ainda não jogou, mesa aberta) — serve o mesmo jogo do duelo
    if j and j['jogou_em'] is None and j['pontos'] is None and m['status'] == 'aberta':
        _tpl = (JOGOS_VALENDO.get(m['jogo']) or JOGOS_VALENDO['blocos'])['tpl']
        return render_template(_tpl, token=token, seed=m['seed'], aposta=m['entrada'],
                               endpoint='/arena/mesa/' + token + '/jogar', alvo=None)
    pote = round(n * m['entrada'] * FICHA_VALOR, 2)
    premio = round(pote * (1 - ARENA_RAKE), 2)
    if m['status'] in ('paga', 'cancelada'):
        res = _mesa_resultado(token)
        minha = next((x for x in res['jogadores'] if x['user_id'] == u['id']), None)
        venceu = bool(minha and minha.get('premio') and minha['premio'] > 0)
        return render_template('arena/mesa.html', estado='resultado', token=token, user=u, mesa=m,
                               res=res, venceu=venceu, meu_premio=(minha['premio'] if minha else 0),
                               creditos=get_creditos(u['id']))
    if j:   # já jogou, aguardando a mesa fechar
        return render_template('arena/mesa.html', estado='aguardando', token=token, user=u, mesa=m,
                               jogadores=n, pote=pote, premio=premio, meu=j, creditos=get_creditos(u['id']))
    return render_template('arena/mesa.html', estado='entrar', token=token, user=u, mesa=m,
                           jogadores=n, pote=pote, premio=premio, creditos=get_creditos(u['id']))


@arena_bp.route('/mesa/<token>/entrar', methods=['POST'])
@arena_login_required
def mesa_entrar_rt(token):
    u = _cur()
    r = mesa_entrar(token, u['id'])
    return jsonify(r), (200 if not r.get('erro') else 400)


@arena_bp.route('/mesa/<token>/jogar', methods=['POST'])
@arena_login_required
def mesa_jogar_rt(token):
    if request.content_length and request.content_length > 300000:
        return jsonify({'erro': 'Requisição grande demais.'}), 413
    u = _cur()
    body = request.get_json(silent=True) or {}
    r = mesa_registrar_jogada(token, u['id'], body.get('moves'), body.get('score'))
    return jsonify(r), (200 if r.get('ok') else 400)


@arena_bp.route('/mesa/<token>/encerrar', methods=['POST'])
@arena_login_required
def mesa_encerrar_rt(token):
    u = _cur()
    r = mesa_encerrar(token, u['id'])
    return jsonify(r), (200 if not r.get('erro') else 400)


# ══════════════════════════════════════════════════════════════════════════════
# LOTE 4 — SAQUE do saldo (R$) pro PIX. Reusa o motor de payout do CAMPonline:
# POST /transfers no Asaas (autossuficiente) + ledger arena_pagamentos UNIQUE(ext_ref)
# + guarda de saque (prefixo arena_premio_). Débito atômico + estorno se o PIX falhar.
# ══════════════════════════════════════════════════════════════════════════════
MIN_SAQUE = 10.0       # saque mínimo por vez
MAX_SAQUE_DIA = 50.0   # teto de saque por dia
SAQUES_DIA = 1         # nº de saques por dia (1x/dia — anti-hacker: janela curtíssima de estrago)


def _saque_hoje(user_id):
    """(soma, quantidade) de saques HOJE (enviando/pago/incerto) — pros limites diários."""
    conn = get_arena_db()
    hoje = datetime.now().strftime('%Y-%m-%d')
    r = conn.execute("SELECT COALESCE(SUM(valor),0), COUNT(*) FROM arena_pagamentos "
                     "WHERE user_id=? AND status IN ('enviando','pago','incerto') AND substr(criado_em,1,10)=?",
                     (user_id, hoje)).fetchone()
    conn.close()
    return (round(float(r[0] or 0), 2), int(r[1] or 0))


def _pix_normaliza(tipo, chave):
    """Valida/normaliza a chave PIX. Retorna (tipo_asaas, chave, erro). Tipos: CPF/PHONE/EMAIL/EVP."""
    tipo = (tipo or '').strip().upper()
    chave = (chave or '').strip()
    if tipo == 'CPF':
        d = _cpf_digits(chave)
        return ('CPF', d, None) if _cpf_valido(d) else ('CPF', '', 'CPF inválido.')
    if tipo in ('CELULAR', 'PHONE', 'TELEFONE'):
        d = _cpf_digits(chave)
        if d.startswith('55') and len(d) > 11:
            d = d[2:]
        if len(d) not in (10, 11):
            return ('PHONE', '', 'Celular precisa de DDD + número.')
        return ('PHONE', '+55' + d, None)
    if tipo in ('EMAIL', 'E-MAIL'):
        return ('EMAIL', chave.lower(), None) if ('@' in chave and '.' in chave) else ('EMAIL', '', 'E-mail inválido.')
    if tipo in ('ALEATORIA', 'ALEATÓRIA', 'EVP'):
        return ('EVP', chave, None) if len(chave) >= 32 else ('EVP', '', 'Chave aleatória inválida (copie do banco).')
    return (tipo, '', 'Escolha o tipo da chave PIX.')


def _asaas_transfer(chave, tipo, valor, desc, ext_ref):
    """Envia PIX (saída) via Asaas /transfers. Retorna (transfer_id, erro, ambiguo).
    ambiguo=True => falha de REDE/5xx (o PIX PODE ter saído) => NÃO estornar automático.
    ambiguo=False => rejeição EXPLÍCITA do Asaas (chave/saldo) => seguro estornar."""
    payload = {'value': round(float(valor), 2), 'operationType': 'PIX',
               'pixAddressKey': chave, 'description': (desc or '')[:100],
               'externalReference': ext_ref}
    if tipo:
        payload['pixAddressKeyType'] = tipo
    resp = _asaas_req('POST', '/transfers', payload)
    tid = resp.get('id', '')
    if tid:
        return (tid, None, False)
    if resp.get('_neterr'):
        return ('', str(resp.get('error') or 'instabilidade de rede'), True)
    errs = resp.get('errors') or []
    if errs:
        return ('', str(errs[0].get('description') or 'rejeitado')[:200], False)
    return ('', str(resp.get('error') or 'resposta inesperada')[:200], True)   # desconhecido -> seguro=ambíguo


def _asaas_transfer_ja_saiu(ext_ref):
    """Anti-duplo: confere no Asaas se a transferência com esse ext já existe (não estornar em cima)."""
    try:
        chk = _asaas_req('GET', f'/transfers?externalReference={ext_ref}')
        for t in (chk.get('data') or []):
            st = (t.get('status') or '').upper()
            if t.get('externalReference') == ext_ref and t.get('id') and st not in ('CANCELLED', 'FAILED'):
                return t['id']
    except Exception:
        pass
    return ''


def _saque_debita_e_registra(user_id, valor, ext, chave, tipo):
    """Débito de saldo + INSERT do ledger na MESMA transação — um crash nunca deixa saldo
    debitado sem registro (o red-team pegou essa janela). True se debitou+registrou; False sem saldo."""
    conn = get_arena_db()
    cur = conn.execute('UPDATE arena_users SET saldo=ROUND(saldo-?,2) WHERE id=? AND ROUND(saldo,2)>=?',
                       (valor, user_id, valor))
    if cur.rowcount != 1:
        conn.commit(); conn.close(); return False
    conn.execute("INSERT INTO arena_pagamentos(user_id,pix_chave,pix_tipo,valor,ext_ref,status,criado_em) "
                 "VALUES(?,?,?,?,?,?,?)", (user_id, chave, tipo, valor, ext, 'enviando', datetime.now().isoformat()))
    conn.commit(); conn.close(); return True


def sacar(user_id, valor, pix_tipo, pix_chave):
    """Saca R$ do saldo pro PIX. Débito+ledger ATÔMICO -> transfere. Só ESTORNA em rejeição
    EXPLÍCITA do Asaas; em falha AMBÍGUA (rede/timeout/5xx) marca 'incerto' e NÃO estorna — o
    PIX pode ter saído (evita duplo prejuízo). Anti-duplo via ext_ref UNIQUE + pré-check."""
    try:
        valor = round(float(valor or 0), 2)
    except Exception:
        return {'erro': 'Valor inválido.'}
    if valor < MIN_SAQUE:
        return {'erro': f'O saque mínimo é R$ {MIN_SAQUE:.2f}.'}
    if valor > MAX_SAQUE_DIA:
        return {'erro': f'O saque máximo é R$ {MAX_SAQUE_DIA:.0f} por dia.'}
    tipo, chave, perr = _pix_normaliza(pix_tipo, pix_chave)
    if perr:
        return {'erro': perr}
    soma_hoje, qtd_hoje = _saque_hoje(user_id)
    if qtd_hoje >= SAQUES_DIA:
        return {'erro': 'Você já sacou hoje. Pode sacar de novo amanhã! 🌙'}
    if soma_hoje + valor > MAX_SAQUE_DIA:
        return {'erro': f'Limite de R$ {MAX_SAQUE_DIA:.0f} por dia. O resto você saca amanhã.'}
    ext = 'arena_premio_saque_' + secrets.token_hex(8)
    if not _saque_debita_e_registra(user_id, valor, ext, chave, tipo):
        return {'erro': 'Saldo insuficiente pra esse valor.'}
    tid, erro, ambiguo = _asaas_transfer(chave, tipo, valor, 'Saque Arena - premio', ext)
    if not tid:                       # apesar do erro, confere se por acaso saiu (anti-duplo)
        tid = _asaas_transfer_ja_saiu(ext)
    conn = get_arena_db()
    if tid:
        conn.execute("UPDATE arena_pagamentos SET status='pago', transfer_id=?, erro='' WHERE ext_ref=?", (tid, ext))
        conn.commit(); conn.close()
        log.info(f'[Arena] SAQUE pago user {user_id} R${valor:.2f} ({tid})')
        return {'ok': True, 'valor': valor}
    if ambiguo:                        # AMBÍGUO: pode ter saído -> NÃO estorna, marca p/ conferência
        conn.execute("UPDATE arena_pagamentos SET status='incerto', erro=? WHERE ext_ref=?", (str(erro)[:200], ext))
        conn.commit(); conn.close()
        log.error(f'[Arena] SAQUE INCERTO user {user_id} R${valor:.2f} ext={ext}: {erro} — NAO estornado, conferir manual')
        return {'erro': 'Deu uma instabilidade no envio. Seu saque está EM ANÁLISE — se não cair em minutos, o valor volta pro saldo. Não repita agora.'}
    # rejeição EXPLÍCITA do Asaas (chave/saldo) -> seguro estornar
    conn.execute("UPDATE arena_pagamentos SET status='erro', erro=? WHERE ext_ref=?", (str(erro)[:200], ext))
    conn.commit(); conn.close()
    add_saldo(user_id, valor)
    log.warning(f'[Arena] SAQUE rejeitado user {user_id} R${valor:.2f}: {erro} — saldo devolvido')
    return {'erro': f'Não deu pra enviar o PIX ({erro}). Seu saldo foi devolvido — confira a chave e tente de novo.'}


@arena_bp.route('/sacar', methods=['GET', 'POST'])
@arena_login_required
def sacar_rt():
    u = _cur()
    if request.method == 'GET':
        return render_template('arena/sacar.html', user=u, saldo=get_saldo(u['id']), minimo=MIN_SAQUE,
                               pix_chave=u.get('pix_chave') or '', pix_tipo=(u.get('pix_tipo') or 'CPF'))
    body = request.get_json(silent=True) or request.form
    r = sacar(u['id'], body.get('valor'), body.get('pix_tipo'), body.get('pix_chave'))
    return jsonify(r), (200 if r.get('ok') else 400)
