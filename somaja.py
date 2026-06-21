"""
somaja.py — Blueprint SomaJá
Coach financeiro por IA no WhatsApp ("Porquim anabolizado"). Modelo: assinatura + trial 7 dias.
O usuário fala/escreve/fotografa o gasto, a IA anota, soma e dá conselho.
Lote 1: motor (texto/áudio/foto) + saldo + resumo + coach + auth + paywall (web de teste).
"""
import os
import json
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
from somaja_db import (get_somaja_db, init_somaja_db, CATEGORIAS,
                       tem_acesso, dias_de_trial_restantes,
                       add_tx, ultimos_tx, saldo_mes, resumo_categorias,
                       registrar_conselho,
                       garantir_carteira, entrar_carteira, sair_carteira, membros_carteira,
                       tx_do_mes)

log = logging.getLogger('somaja')

somaja_bp = Blueprint('somaja', __name__, url_prefix='/somaja')

TRIAL_DIAS = 7

# ── IA: Gemini ─────────────────────────────────────────────────────────────────
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


def _gemini_call(system, contents, json_mode=False, max_tokens=2048, temperature=0.2):
    """Chama o Gemini via REST. Retorna (texto, tokens_in, tokens_out)."""
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


# ── Cérebros do SomaJá ─────────────────────────────────────────────────────────
SYSTEM_PARSER = """Você é o motor de extração financeira do SomaJá. A pessoa vai enviar uma mensagem \
(texto, ÁUDIO transcrito ou FOTO de um comprovante/nota/fatura) contando um ou mais gastos ou recebimentos.

Sua tarefa: EXTRAIR os lançamentos financeiros e devolver SOMENTE um JSON válido neste formato:
{"lancamentos":[{"tipo":"saida","valor":50.0,"categoria":"mercado","descricao":"compras no mercado"}]}

REGRAS:
- "tipo": "saida" quando a pessoa GASTOU/pagou/comprou; "entrada" quando RECEBEU (salário, pix recebido, venda).
- "valor": número em reais (ponto decimal). Se a foto for um comprovante, use o valor total.
- "categoria": escolha UMA desta lista exata: %CATS%. Se não encaixar, use "outros".
- "descricao": curta, do que se trata (ex: "almoço", "uber", "salário").
- Pode haver VÁRIOS lançamentos numa mensagem só — retorne todos.
- Se NÃO houver nenhum valor/gasto claro (ex: a pessoa só fez uma pergunta ou mandou "oi"), retorne {"lancamentos":[]}.
- NUNCA invente valor. Sem valor explícito = não crie lançamento.
- Responda APENAS o JSON, sem texto fora dele.
""".replace('%CATS%', ', '.join(CATEGORIAS))


SYSTEM_COACH = """Você é o SomaJá, um coach financeiro simpático e direto que fala com gente comum no Brasil. \
Linguagem do dia a dia, ZERO economês, frases curtas, tom de amigo que torce pela pessoa.

Vou te dar um resumo do mês da pessoa (renda, total que entrou, total que saiu, saldo e os maiores gastos por categoria). \
Sua tarefa: escrever UM conselho prático e específico, curto (máximo 4 frases), olhando os NÚMEROS REAIS que recebeu.

REGRAS:
- Comece com um elogio ou um alerta honesto, conforme os números.
- Aponte 1 gasto concreto onde dá pra economizar (cite a categoria e o valor real).
- Dê uma sugestão prática e factível (ex: "cortando 2 deliveries por semana sobra ~R$X").
- Se o saldo estiver negativo, seja gentil mas franco.
- Não invente números que não estão no resumo. Use só o que te dei.
- Termine com uma frase de incentivo. Use no máximo 1 ou 2 emojis.
"""


# ── E-mail (Resend) ─────────────────────────────────────────────────────────────
def _enviar_email(para: str, assunto: str, html: str) -> bool:
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        return False
    from_addr = os.environ.get('EMAIL_FROM', 'SomaJá <onboarding@resend.dev>')
    try:
        resp = _requests.post('https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'from': from_addr, 'to': [para], 'subject': assunto, 'html': html}, timeout=10)
        return resp.status_code in (200, 201)
    except Exception:
        return False


# ── Decorador / helpers ──────────────────────────────────────────────────────────
def somaja_login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get('soma_user_id'):
            return redirect('/somaja/entrar')
        return f(*a, **k)
    return wrap


def somaja_acesso_required(f):
    """Logado E com acesso (assinatura ativa ou trial válido)."""
    @wraps(f)
    def wrap(*a, **k):
        if not session.get('soma_user_id'):
            return redirect('/somaja/entrar')
        u = _get_user()
        if not u:
            session.clear()
            return redirect('/somaja/entrar')
        if not tem_acesso(u):
            return redirect('/somaja/assinar')
        return f(*a, **k)
    return wrap


def _get_user():
    uid = session.get('soma_user_id')
    if not uid:
        return None
    conn = get_somaja_db()
    u = conn.execute('SELECT * FROM somaja_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return u


@somaja_bp.context_processor
def _inject_user():
    return {'soma_nome': session.get('soma_user_nome', '')}


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


def _brl(v):
    return ('R$ ' + f'{float(v or 0):,.2f}').replace(',', 'X').replace('.', ',').replace('X', '.')


# ── Planos (assinatura) ─────────────────────────────────────────────────────────
# Anual = PIX (taxa Asaas R$1,99 fixa vira ~1%). Carteira família vem no Lote 3.
PLANOS = {
    'pro_anual':     {'label': 'Pro anual',     'valor': 149.00, 'cycle': 'YEARLY',  'rotulo': 'R$ 149/ano',   'destaque': True},
    'pro_mensal':    {'label': 'Pro mensal',    'valor': 14.90,  'cycle': 'MONTHLY', 'rotulo': 'R$ 14,90/mês', 'destaque': False},
    'familia_anual': {'label': 'Família anual', 'valor': 249.00, 'cycle': 'YEARLY',  'rotulo': 'R$ 249/ano',   'destaque': False},
    'familia_mensal':{'label': 'Família mensal','valor': 29.90,  'cycle': 'MONTHLY', 'rotulo': 'R$ 29,90/mês', 'destaque': False},
}

# ── Asaas ──────────────────────────────────────────────────────────────────────
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
        conn = get_somaja_db()
        conn.execute('UPDATE somaja_users SET asaas_customer_id=?, cpf=? WHERE id=?', (cid, cpf, u['id']))
        conn.commit(); conn.close()
    return cid or ''


def soma_webhook_ativar(customer_id, plano_key, ativar):
    """Chamado pelo webhook global (app.py) p/ refs 'somaja_<customer_id>_<plano>'.
    Ativa/desativa a assinatura do usuário casado pelo asaas_customer_id."""
    if not customer_id:
        return False
    conn = get_somaja_db()
    u = conn.execute('SELECT id, email, nome FROM somaja_users WHERE asaas_customer_id=?',
                     (customer_id,)).fetchone()
    if not u:
        conn.close()
        return False
    conn.execute('UPDATE somaja_users SET plan_active=?, plano=COALESCE(?,plano) WHERE id=?',
                 (1 if ativar else 0, plano_key, u['id']))
    conn.commit(); conn.close()
    if ativar and u['email']:
        try:
            _enviar_email(u['email'], '✅ SomaJá — Assinatura ativa!',
                f'<p>Oi, {(u["nome"] or "").split()[0]}! Sua assinatura do <b>SomaJá</b> está ativa. '
                f'Agora é só mandar seus gastos que eu somo tudo pra você. 🐗💰</p>'
                f'<p><a href="https://4kitem.com.br/somaja/app">Abrir o SomaJá</a></p>')
        except Exception:
            pass
    log.info(f'[SomaJá] Assinatura {"ATIVADA" if ativar else "cortada"} (customer {customer_id})')
    return True


# ── Motor: registrar lançamento (canal-agnóstico: web hoje, WhatsApp no Lote 2) ──
def registrar_lancamento(user_id, texto=None, file_bytes=None, mime=None, fonte='texto'):
    """Lê a mensagem (texto/áudio/foto) com a IA, grava os lançamentos e devolve
    um dict com a confirmação amigável + saldo atualizado.
    Retorna (resposta:str, lancamentos:list, tokens_in, tokens_out)."""
    import base64
    parts = []
    if file_bytes and mime:
        parts.append({'inlineData': {'mimeType': mime, 'data': base64.b64encode(file_bytes).decode('ascii')}})
    if texto:
        parts.append({'text': texto})
    if not parts:
        return 'Manda o gasto que eu anoto! Ex: "almoço 35" 😉', [], 0, 0
    contents = [{'role': 'user', 'parts': parts}]
    raw, tin, tout = _gemini_call(SYSTEM_PARSER, contents, json_mode=True,
                                  max_tokens=1024, temperature=0.1)
    try:
        dados = json.loads(raw)
        lancs = dados.get('lancamentos', []) if isinstance(dados, dict) else []
    except Exception:
        lancs = []

    salvos = []
    for l in lancs:
        try:
            valor = float(l.get('valor') or 0)
        except (TypeError, ValueError):
            valor = 0
        if valor <= 0:
            continue
        tipo = 'entrada' if (l.get('tipo') == 'entrada') else 'saida'
        cat  = (l.get('categoria') or 'outros').lower().strip()
        if cat not in CATEGORIAS:
            cat = 'outros'
        desc = (l.get('descricao') or cat)[:120]
        add_tx(user_id, tipo, valor, cat, desc, fonte=fonte)
        salvos.append({'tipo': tipo, 'valor': valor, 'categoria': cat, 'descricao': desc})

    if not salvos:
        return ('Não achei nenhum valor nessa mensagem. 🤔 Tenta assim: '
                '"mercado 130" ou manda a foto da notinha.'), [], tin, tout

    # Confirmação amigável + saldo do mês
    linhas = []
    for s in salvos:
        emoji = '💸' if s['tipo'] == 'saida' else '💰'
        verbo = 'Saída' if s['tipo'] == 'saida' else 'Entrada'
        linhas.append(f'{emoji} {verbo}: {_brl(s["valor"])} — {s["descricao"]} ({s["categoria"]})')
    s = saldo_mes(user_id)
    bola = '🟢' if s['saldo'] >= 0 else '🔴'
    resposta = ('✅ Anotei!\n' + '\n'.join(linhas) +
                f'\n\nSaldo do mês: {_brl(s["saldo"])} {bola}')
    return resposta, salvos, tin, tout


def gerar_conselho(user_id):
    """Monta o resumo do mês e pede 1 conselho ao coach. Busca o usuário por ID
    (funciona no web, no webhook do WhatsApp e no coach automático em background)."""
    conn = get_somaja_db()
    u = conn.execute('SELECT * FROM somaja_users WHERE id=?', (user_id,)).fetchone()
    conn.close()
    s = saldo_mes(user_id)
    cats = resumo_categorias(user_id, tipo='saida')[:5]
    if not cats and s['entradas'] == 0:
        return ('Ainda não tenho gastos seus esse mês pra analisar. '
                'Manda alguns lançamentos que eu te dou um conselho certeiro! 😉')
    renda = 0
    try:
        renda = float(u['renda'] or 0)
    except (TypeError, ValueError, KeyError):
        renda = 0
    resumo = (f'Renda informada: {_brl(renda)}\n'
              f'Entrou no mês: {_brl(s["entradas"])}\n'
              f'Saiu no mês: {_brl(s["saidas"])}\n'
              f'Saldo: {_brl(s["saldo"])}\n'
              f'Maiores gastos por categoria:\n' +
              '\n'.join(f'- {c}: {_brl(t)}' for c, t in cats))
    try:
        texto, _tin, _tout = _gemini_call(
            SYSTEM_COACH, [{'role': 'user', 'parts': [{'text': resumo}]}],
            json_mode=False, max_tokens=400, temperature=0.5)
    except Exception as e:
        log.warning(f'[SomaJá] coach falhou: {e}')
        return 'Não consegui gerar o conselho agora. Tenta de novo daqui a pouquinho. 🙏'
    if texto:
        registrar_conselho(user_id, texto)
    return texto or 'Continua firme nos registros que semana que vem eu te trago um panorama! 💪'


# ── Rotas: público / auth ───────────────────────────────────────────────────────
@somaja_bp.route('/')
def landing():
    return render_template('somaja/landing.html')


@somaja_bp.route('/cadastrar', methods=['GET', 'POST'])
def cadastrar():
    if session.get('soma_user_id'):
        return redirect('/somaja/app')
    erro = None
    if request.method == 'POST':
        nome     = (request.form.get('nome') or '').strip()
        email    = (request.form.get('email') or '').strip().lower()
        telefone = _cpf_digits(request.form.get('telefone'))
        senha    = request.form.get('senha') or ''
        try:
            renda = float((request.form.get('renda') or '0').replace('.', '').replace(',', '.'))
        except ValueError:
            renda = 0
        if not nome or not email or not senha:
            erro = 'Preencha nome, e-mail e senha.'
        elif len(senha) < 6:
            erro = 'A senha precisa ter pelo menos 6 caracteres.'
        else:
            conn = get_somaja_db()
            existe = conn.execute('SELECT id FROM somaja_users WHERE email=?', (email,)).fetchone()
            if existe:
                conn.close()
                erro = 'Já existe uma conta com esse e-mail. Faça login.'
            else:
                trial_until = (datetime.now() + timedelta(days=TRIAL_DIAS)).strftime('%Y-%m-%d')
                cur = conn.execute(
                    'INSERT INTO somaja_users (nome,email,telefone,renda,password_hash,trial_until,created_at) '
                    'VALUES (?,?,?,?,?,?,?)',
                    (nome, email, telefone, renda, generate_password_hash(senha),
                     trial_until, datetime.now().isoformat()))
                conn.commit()
                uid = cur.lastrowid
                conn.close()
                session['soma_user_id']   = uid
                session['soma_user_nome'] = nome
                return redirect('/somaja/app')
    return render_template('somaja/cadastrar.html', erro=erro)


@somaja_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if session.get('soma_user_id'):
        return redirect('/somaja/app')
    erro = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        conn = get_somaja_db()
        u = conn.execute('SELECT * FROM somaja_users WHERE email=?', (email,)).fetchone()
        if u and check_password_hash(u['password_hash'], senha):
            conn.execute('UPDATE somaja_users SET ultimo_acesso=? WHERE id=?',
                         (datetime.now().isoformat(), u['id']))
            conn.commit(); conn.close()
            session['soma_user_id']   = u['id']
            session['soma_user_nome'] = u['nome']
            return redirect('/somaja/app')
        conn.close()
        erro = 'E-mail ou senha incorretos.'
    return render_template('somaja/entrar.html', erro=erro)


@somaja_bp.route('/sair')
def sair():
    for k in ('soma_user_id', 'soma_user_nome'):
        session.pop(k, None)
    return redirect('/somaja')


# ── Rotas: área logada (precisa de acesso) ──────────────────────────────────────
@somaja_bp.route('/app')
@somaja_login_required
def app_home():
    u = _get_user()
    if not u:
        session.clear()
        return redirect('/somaja/entrar')
    if not tem_acesso(u):
        return redirect('/somaja/assinar')
    s = saldo_mes(u['id'])
    return render_template('somaja/app.html', u=u, saldo=s, _brl=_brl,
                           trial_dias=dias_de_trial_restantes(u),
                           assinante=bool(u['plan_active']),
                           ultimos=ultimos_tx(u['id'], 8))


@somaja_bp.route('/lancar', methods=['POST'])
@somaja_acesso_required
def lancar():
    u = _get_user()
    data = request.get_json(silent=True) or {}
    texto = (data.get('texto') or '').strip()[:500]
    if len(texto) < 2:
        return jsonify({'erro': 'Escreve o gasto, ex: "almoço 35".'}), 400
    try:
        resposta, salvos, _tin, _tout = registrar_lancamento(u['id'], texto=texto, fonte='texto')
    except Exception as e:
        log.warning(f'[SomaJá] lancar falhou: {e}')
        return jsonify({'erro': 'Tive um problema pra anotar agora. Tenta de novo. 🙏'}), 502
    s = saldo_mes(u['id'])
    return jsonify({'ok': True, 'resposta': resposta, 'saldo': s, 'saldo_fmt': _brl(s['saldo'])})


@somaja_bp.route('/lancar-arquivo', methods=['POST'])
@somaja_acesso_required
def lancar_arquivo():
    u = _get_user()
    f = request.files.get('arquivo')
    if not f or not f.filename:
        return jsonify({'erro': 'Envie uma foto do comprovante ou um áudio.'}), 400
    raw = f.read()
    if len(raw) > 10 * 1024 * 1024:
        return jsonify({'erro': 'Arquivo muito grande (máx. 10MB).'}), 400
    mime = (f.mimetype or '').lower()
    if mime.startswith('image/'):
        fonte = 'foto'
    elif mime.startswith('audio/'):
        fonte = 'audio'
    else:
        return jsonify({'erro': 'Envie uma foto (JPG/PNG) ou um áudio.'}), 400
    try:
        resposta, salvos, _tin, _tout = registrar_lancamento(
            u['id'], file_bytes=raw, mime=mime, fonte=fonte)
    except Exception as e:
        log.warning(f'[SomaJá] lancar-arquivo falhou: {e}')
        return jsonify({'erro': 'Não consegui ler esse arquivo agora. Tenta uma foto mais nítida. 🙏'}), 502
    s = saldo_mes(u['id'])
    return jsonify({'ok': True, 'resposta': resposta, 'saldo': s, 'saldo_fmt': _brl(s['saldo'])})


@somaja_bp.route('/resumo')
@somaja_acesso_required
def resumo():
    u = _get_user()
    s = saldo_mes(u['id'])
    cats = resumo_categorias(u['id'], tipo='saida')
    total = s['saidas'] or 1
    linhas = [{'categoria': c, 'total': t, 'total_fmt': _brl(t),
               'pct': round(100 * t / total)} for c, t in cats]
    return jsonify({'ok': True, 'saldo': s,
                    'entradas_fmt': _brl(s['entradas']), 'saidas_fmt': _brl(s['saidas']),
                    'saldo_fmt': _brl(s['saldo']), 'categorias': linhas})


@somaja_bp.route('/coach', methods=['POST'])
@somaja_acesso_required
def coach():
    u = _get_user()
    try:
        texto = gerar_conselho(u['id'])
    except Exception as e:
        log.warning(f'[SomaJá] coach rota falhou: {e}')
        return jsonify({'erro': 'O coach tirou um cochilo. Tenta de novo. 😴'}), 502
    return jsonify({'ok': True, 'conselho': texto})


@somaja_bp.route('/familia', methods=['GET', 'POST'])
@somaja_acesso_required
def familia():
    u = _get_user()
    msg = erro = None
    if request.method == 'POST':
        acao = request.form.get('acao')
        if acao == 'entrar':
            nome_cart = entrar_carteira(u['id'], request.form.get('codigo'))
            msg = f'Você entrou na {nome_cart}!' if nome_cart else None
            erro = None if nome_cart else 'Código não encontrado. Confira e tente de novo.'
        elif acao == 'sair':
            sair_carteira(u['id'])
            msg = 'Você saiu da carteira compartilhada.'
        u = _get_user()
    _cid, codigo = garantir_carteira(u['id'], u['nome'])
    return render_template('somaja/familia.html', codigo=codigo,
                           membros=membros_carteira(u['id']), msg=msg, erro=erro)


# ── Painel + Relatório PDF (Lote 4) ─────────────────────────────────────────────
_MESES = ['', 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
          'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']


def _nome_mes(ym):
    try:
        y, m = ym.split('-')
        return f'{_MESES[int(m)]} de {y}'
    except Exception:
        return ym


def _mes_param():
    return (request.args.get('mes') or datetime.now().strftime('%Y-%m'))[:7]


def _vizinhos_mes(mes):
    y, m = int(mes[:4]), int(mes[5:7])
    prev = f'{y-1}-12' if m == 1 else f'{y}-{m-1:02d}'
    nxt  = f'{y+1}-01' if m == 12 else f'{y}-{m+1:02d}'
    return prev, nxt


def _dados_mes(user_id, mes):
    s = saldo_mes(user_id, mes)
    cats = resumo_categorias(user_id, mes, tipo='saida')
    total = s['saidas'] or 1
    catlist = [{'cat': c, 'total': t, 'fmt': _brl(t), 'pct': round(100 * t / total)}
               for c, t in cats]
    return s, catlist, tx_do_mes(user_id, mes)


@somaja_bp.route('/painel')
@somaja_acesso_required
def painel():
    u = _get_user()
    mes = _mes_param()
    s, cats, txs = _dados_mes(u['id'], mes)
    prev, nxt = _vizinhos_mes(mes)
    return render_template('somaja/painel.html', u=u, mes=mes, nome_mes=_nome_mes(mes),
                           saldo=s, cats=cats, txs=txs, prev=prev, nxt=nxt, _brl=_brl,
                           assinante=bool(u['plan_active']))


@somaja_bp.route('/relatorio')
@somaja_acesso_required
def relatorio():
    u = _get_user()
    mes = _mes_param()
    s, cats, txs = _dados_mes(u['id'], mes)
    return render_template('somaja/relatorio.html', u=u, mes=mes, nome_mes=_nome_mes(mes),
                           saldo=s, cats=cats, txs=txs, _brl=_brl,
                           membros=membros_carteira(u['id']),
                           gerado=datetime.now().strftime('%d/%m/%Y às %H:%M'))


# ── Rotas: assinatura (paywall) ─────────────────────────────────────────────────
@somaja_bp.route('/assinar')
@somaja_login_required
def assinar():
    u = _get_user()
    return render_template('somaja/assinar.html', u=u, planos=PLANOS,
                           trial_dias=dias_de_trial_restantes(u))


@somaja_bp.route('/checkout/<plano>', methods=['GET', 'POST'])
@somaja_login_required
def checkout(plano):
    u = _get_user()
    if plano not in PLANOS:
        return redirect('/somaja/assinar')
    p = PLANOS[plano]
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
                billing = 'PIX'
                sub = _asaas_req('POST', '/subscriptions', {
                    'customer': customer_id, 'billingType': billing, 'value': p['valor'],
                    'nextDueDate': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
                    'cycle': p['cycle'], 'description': f'SomaJá — {p["label"]}',
                    'externalReference': f'somaja_{customer_id}_{plano}'})
                sub_id = sub.get('id')
                if not sub_id:
                    erro = (sub.get('errors') or [{}])[0].get('description', 'Erro ao gerar a cobrança.')
                else:
                    conn = get_somaja_db()
                    conn.execute('UPDATE somaja_users SET asaas_subscription_id=?, plano=? WHERE id=?',
                                 (sub_id, plano, u['id']))
                    conn.commit(); conn.close()
                    return redirect(f'/somaja/pix/{plano}')
    return render_template('somaja/checkout.html', u=u, plano=plano, p=p, erro=erro)


@somaja_bp.route('/pix/<plano>')
@somaja_login_required
def pix(plano):
    u = _get_user()
    if plano not in PLANOS or not u['asaas_subscription_id']:
        return redirect('/somaja/assinar')
    qr = copia = ''
    payments = _asaas_req('GET', f'/subscriptions/{u["asaas_subscription_id"]}/payments?limit=1')
    if payments.get('data'):
        pid = payments['data'][0].get('id', '')
        if pid:
            resp = _asaas_req('GET', f'/payments/{pid}/pixQrCode')
            qr    = resp.get('encodedImage', '')
            copia = resp.get('payload', '')
    return render_template('somaja/pix.html', u=u, plano=plano, p=PLANOS[plano], qr=qr, copia=copia)


@somaja_bp.route('/pix-status', methods=['POST'])
@somaja_login_required
def pix_status():
    u = _get_user()
    if not u['asaas_subscription_id']:
        return jsonify({'pago': False})
    if u['plan_active']:
        return jsonify({'pago': True})
    payments = _asaas_req('GET', f'/subscriptions/{u["asaas_subscription_id"]}/payments?limit=1')
    if payments.get('data'):
        st = (payments['data'][0].get('status') or '').upper()
        if st in ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH'):
            soma_webhook_ativar(u['asaas_customer_id'], u['plano'], True)
            return jsonify({'pago': True})
    return jsonify({'pago': False})


# ════════════════════════════════════════════════════════════════════════════════
# LOTE 2 — Canal WhatsApp (Evolution API, mesma engine do MandaZap/VetZap)
# Aponte o webhook da Evolution para: /somaja/wa/webhook?key=SOMAJA_WA_WEBHOOK_SECRET
# ════════════════════════════════════════════════════════════════════════════════
EVO_URL     = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
EVO_KEY     = os.environ.get('EVOLUTION_API_KEY', '')
EVO_INST    = os.environ.get('SOMAJA_WA_INSTANCE', 'somaja')
WA_RATE_DIA = int(os.environ.get('SOMAJA_WA_RATE_DIA', '60'))


def _wa_digits(telefone):
    d = ''.join(c for c in str(telefone) if c.isdigit())
    if d and not d.startswith('55'):
        d = '55' + d
    return d


def wa_send(telefone, texto):
    """Envia texto pelo WhatsApp via Evolution API."""
    if not EVO_URL or not EVO_KEY:
        log.warning('[SomaJá] Evolution não configurada — msg não enviada')
        return False
    try:
        r = _requests.post(f'{EVO_URL}/message/sendText/{EVO_INST}',
                           json={'number': _wa_digits(telefone) + '@s.whatsapp.net', 'text': texto},
                           headers={'apikey': EVO_KEY}, timeout=10)
        return r.status_code in (200, 201)
    except Exception as e:
        log.warning(f'[SomaJá] wa_send erro: {e}')
        return False


def _evo_media(msg):
    """Extrai foto/áudio de uma mensagem da Evolution → (raw_bytes, mime) ou (None, None)."""
    import base64
    m = msg.get('message', {}) or {}
    obj = mime = None
    if 'imageMessage' in m:
        obj, mime = m['imageMessage'], m['imageMessage'].get('mimetype', 'image/jpeg')
    elif 'audioMessage' in m:
        obj, mime = m['audioMessage'], m['audioMessage'].get('mimetype', 'audio/ogg')
    if obj is None:
        return None, None
    mime = (mime or 'application/octet-stream').split(';')[0].strip()
    b64 = m.get('base64') or msg.get('base64') or obj.get('base64')
    if not b64 and EVO_URL and EVO_KEY:
        try:
            r = _requests.post(f'{EVO_URL}/chat/getBase64FromMediaMessage/{EVO_INST}',
                               json={'message': {'key': msg.get('key', {})}},
                               headers={'apikey': EVO_KEY}, timeout=25)
            j = r.json()
            if isinstance(j, dict) and j.get('base64'):
                b64 = j['base64']
                mime = (j.get('mimetype') or mime).split(';')[0]
        except Exception as e:
            log.warning(f'[SomaJá] media fetch falhou: {e}')
    if not b64:
        return None, None
    try:
        return base64.b64decode(b64), mime
    except Exception:
        return None, None


def _wa_get_or_create_user(telefone, push_name=''):
    """Acha o usuário pelo telefone (últimos 10 dígitos) ou cria um 'lite' com trial.
    Permite onboarding 100% no WhatsApp, sem cadastro web."""
    digits = ''.join(c for c in str(telefone) if c.isdigit())
    d10 = digits[-10:] if len(digits) >= 10 else digits
    conn = get_somaja_db()
    u = conn.execute("SELECT * FROM somaja_users WHERE telefone LIKE ? ORDER BY id LIMIT 1",
                     ('%' + d10,)).fetchone()
    if u:
        conn.close()
        return u, False
    trial = (datetime.now() + timedelta(days=TRIAL_DIAS)).strftime('%Y-%m-%d')
    nome  = (push_name or 'Cliente SomaJá').strip()[:60]
    email = f'wa{digits}@somaja.wa'
    try:
        cur = conn.execute(
            'INSERT INTO somaja_users (nome,email,telefone,password_hash,trial_until,created_at) '
            'VALUES (?,?,?,?,?,?)',
            (nome, email, digits, generate_password_hash(secrets.token_urlsafe(16)),
             trial, datetime.now().isoformat()))
        conn.commit()
        uid = cur.lastrowid
    except Exception:
        uid = None
    u = (conn.execute('SELECT * FROM somaja_users WHERE id=?', (uid,)).fetchone() if uid
         else conn.execute('SELECT * FROM somaja_users WHERE email=?', (email,)).fetchone())
    conn.close()
    return u, True


def _wa_rate_ok(user_id):
    """Cap diário de mensagens por usuário (protege o custo de IA)."""
    hoje = datetime.now().strftime('%Y-%m-%d')
    conn = get_somaja_db()
    row = conn.execute('SELECT wa_day, wa_count FROM somaja_users WHERE id=?', (user_id,)).fetchone()
    if not row or (row['wa_day'] or '') != hoje:
        conn.execute('UPDATE somaja_users SET wa_day=?, wa_count=1 WHERE id=?', (hoje, user_id))
        conn.commit(); conn.close()
        return True
    n = (row['wa_count'] or 0) + 1
    conn.execute('UPDATE somaja_users SET wa_count=? WHERE id=?', (n, user_id))
    conn.commit(); conn.close()
    return n <= WA_RATE_DIA


def _wa_ajuda(u):
    dias = dias_de_trial_restantes(u)
    base = ('🐗 *Oi! Eu sou o SomaJá*, seu coach financeiro.\n\n'
            'É só me mandar seus gastos que eu somo tudo:\n'
            '• Escreve: *"mercado 130"*\n'
            '• Manda um *áudio* falando o gasto\n'
            '• Tira *foto da notinha*\n\n'
            'Comandos:\n'
            '📊 *resumo* — saldo do mês\n'
            '🧠 *conselho* — dica do coach\n'
            '👨‍👩‍👧 *família* — juntar o bolso de todos\n')
    if not u['plan_active'] and dias >= 0:
        base += f'\n🎁 Você está no teste grátis ({dias} dia(s)).'
    return base


@somaja_bp.route('/wa/webhook', methods=['GET', 'POST'])
def wa_webhook():
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200
    secret = os.environ.get('SOMAJA_WA_WEBHOOK_SECRET', '').strip()
    if secret:
        recv = (request.args.get('key', '') or request.headers.get('x-webhook-key', '')).strip()
        if recv != secret:
            return jsonify({'error': 'unauthorized'}), 401
    else:
        log.warning('[SomaJá] SOMAJA_WA_WEBHOOK_SECRET não setado — webhook aberto!')
    return processar_wa_evento(request.get_json(silent=True) or {})


def processar_wa_evento(data):
    """Núcleo do processamento de uma mensagem do WhatsApp do SomaJá (sem auth).
    Recebe o payload da Evolution. Usado pela rota /somaja/wa/webhook E pelo webhook
    GLOBAL do Evolution (/mandazap/webhook/evolution), roteado por instância no app.py."""
    try:
        msg = data.get('data', data)
        if isinstance(msg, list):
            msg = msg[0] if msg else {}
        key = (msg.get('key') or {}) if isinstance(msg, dict) else {}
        if key.get('fromMe'):
            return jsonify({'ignored': 'fromMe'}), 200
        remote = key.get('remoteJid', '') or ''
        if '@g.us' in remote:                       # ignora grupos
            return jsonify({'ignored': 'group'}), 200
        telefone = remote.split('@')[0]
        if not telefone:
            return jsonify({'ignored': 'no-phone'}), 200
        push = msg.get('pushName', '') or ''
        m = msg.get('message', {}) or {}
        texto = (m.get('conversation')
                 or (m.get('extendedTextMessage') or {}).get('text', '')
                 or (m.get('imageMessage') or {}).get('caption', '')
                 or '')
        media_bytes, media_mime = _evo_media(msg)

        u, novo = _wa_get_or_create_user(telefone, push)
        if not u:
            return jsonify({'ignored': 'no-user'}), 200

        # 1ª mensagem de um número novo → boas-vindas
        if novo:
            wa_send(telefone, _wa_ajuda(u))

        # Sem acesso (trial venceu, sem assinatura) → manda o link de assinatura e para
        if not tem_acesso(u):
            link = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/') + '/somaja/assinar'
            wa_send(telefone, f'Seu teste grátis acabou 😢\nPra continuar somando seus gastos e '
                              f'receber os conselhos do coach, assine aqui:\n{link}')
            return jsonify({'ignored': 'no-access'}), 200

        # Anti-abuso (protege custo de IA)
        if not _wa_rate_ok(u['id']):
            wa_send(telefone, 'Você atingiu o limite de mensagens de hoje 🐗 Volta amanhã!')
            return jsonify({'ignored': 'rate_limited'}), 200

        low = (texto or '').strip().lower()

        # Família (Lote 3) — vários no mesmo bolso
        if low in ('familia', 'família', 'convidar', 'convite'):
            _cid, codigo = garantir_carteira(u['id'], u['nome'])
            membros = membros_carteira(u['id'])
            wa_send(telefone,
                    '👨‍👩‍👧 *Carteira família*\n\n'
                    f'Seu código de convite: *{codigo}*\n\n'
                    'Peça pra quem você quer juntar mandar aqui:\n'
                    f'*entrar {codigo}*\n\n'
                    f'Já no bolso: {", ".join(membros)}\n'
                    'Todo mundo soma junto. 💰')
            return jsonify({'ok': True}), 200
        if low.startswith('entrar ') and len(texto.split(None, 1)) > 1:
            nome_cart = entrar_carteira(u['id'], texto.split(None, 1)[1])
            if nome_cart:
                membros = membros_carteira(u['id'])
                wa_send(telefone, f'✅ Você entrou na *{nome_cart}*!\nA partir de agora vocês somam juntos. '
                                  f'👨‍👩‍👧\nQuem está: {", ".join(membros)}')
            else:
                wa_send(telefone, 'Código não encontrado 🤔 Confere com quem te convidou e tenta: *entrar CODIGO*')
            return jsonify({'ok': True}), 200
        if low in ('sair familia', 'sair família', 'sair da familia', 'sair da família'):
            sair_carteira(u['id'])
            wa_send(telefone, 'Pronto, você saiu da carteira compartilhada. Agora suas contas voltam a ser só suas. 👍')
            return jsonify({'ok': True}), 200

        # Comandos
        if low in ('resumo', 'saldo', 'extrato'):
            s = saldo_mes(u['id']); cats = resumo_categorias(u['id'], tipo='saida')[:5]
            txt = (f'📊 *Resumo do mês*\n📥 Entrou: {_brl(s["entradas"])}\n'
                   f'📤 Saiu: {_brl(s["saidas"])}\n💰 Saldo: {_brl(s["saldo"])}')
            if cats:
                txt += '\n\n*Pra onde foi:*\n' + '\n'.join(f'• {c}: {_brl(t)}' for c, t in cats)
            wa_send(telefone, txt)
            return jsonify({'ok': True}), 200
        if low in ('conselho', 'coach', 'dica', 'analise', 'análise'):
            wa_send(telefone, '🧠 ' + gerar_conselho(u['id']))
            return jsonify({'ok': True}), 200
        if low in ('ajuda', 'help', 'menu', 'oi', 'olá', 'ola', 'começar', 'comecar', 'start'):
            wa_send(telefone, _wa_ajuda(u))
            return jsonify({'ok': True}), 200

        # Lançamento (texto / foto / áudio) — mesmo motor do web
        fonte = ('foto' if (media_mime or '').startswith('image')
                 else 'audio' if (media_mime or '').startswith('audio') else 'texto')
        if not (texto or media_bytes):
            wa_send(telefone, _wa_ajuda(u))
            return jsonify({'ok': True}), 200
        resposta, _salvos, _tin, _tout = registrar_lancamento(
            u['id'], texto=(texto or None), file_bytes=media_bytes, mime=media_mime, fonte=fonte)
        wa_send(telefone, resposta)
        return jsonify({'ok': True}), 200
    except Exception as e:
        log.error(f'[SomaJá] wa webhook erro: {e}', exc_info=True)
        return jsonify({'ok': False}), 200


# ── Coach proativo automático (o diferencial) ───────────────────────────────────
# OPT-IN por segurança (anti-ban): só liga com SOMAJA_COACH_AUTO=1.
# Roda 1x/dia e manda o raio-x semanal a quem tem acesso, atividade e ainda não recebeu na semana.
def _coach_proativo_loop():
    import time as _t
    if os.environ.get('SOMAJA_COACH_AUTO', '0') != '1':
        log.info('[SomaJá] Coach proativo DESLIGADO (set SOMAJA_COACH_AUTO=1 p/ ligar)')
        return
    _t.sleep(200)  # deixa o app subir
    log.info('[SomaJá] Coach proativo ATIVO (1x/dia, envia raio-x semanal)')
    while True:
        try:
            conn = get_somaja_db()
            users = conn.execute('SELECT id, telefone, plan_active, trial_until FROM somaja_users '
                                 'WHERE telefone IS NOT NULL AND telefone<>""').fetchall()
            conn.close()
            limite = (datetime.now() - timedelta(days=7)).isoformat()
            for u in users:
                if not tem_acesso(u):
                    continue
                s = saldo_mes(u['id'])
                if s['saidas'] <= 0 and s['entradas'] <= 0:
                    continue
                conn = get_somaja_db()
                last = conn.execute('SELECT created_at FROM somaja_coach_log WHERE user_id=? '
                                    'ORDER BY id DESC LIMIT 1', (u['id'],)).fetchone()
                conn.close()
                if last and last['created_at'] > limite:
                    continue
                try:
                    txt = gerar_conselho(u['id'])
                    wa_send(u['telefone'], '🧠 *Seu raio-x da semana, no SomaJá:*\n\n' + txt)
                    _t.sleep(3)  # respira entre envios (anti-ban)
                except Exception as _e:
                    log.warning(f'[SomaJá] coach proativo user {u["id"]}: {_e}')
        except Exception as e:
            log.error(f'[SomaJá] coach loop: {e}')
        _t.sleep(86400)  # 1x por dia


threading.Thread(target=_coach_proativo_loop, daemon=True, name='somaja-coach').start()
