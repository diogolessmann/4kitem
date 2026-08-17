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
                   session, jsonify, Response)
from werkzeug.security import generate_password_hash, check_password_hash
from somaja_db import (get_somaja_db, init_somaja_db, CATEGORIAS,
                       tem_acesso, dias_de_trial_restantes,
                       add_tx, ultimos_tx, del_tx, saldo_mes, resumo_categorias,
                       faturamento_ano, registrar_conselho, tx_do_mes,
                       set_modo, set_mei_onboard, set_mei_perfil)

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


# ── Modo Negócio (MEI) — fundação do SomaJá Negócio (Lote 0) ─────────────────────
# Teto CONFIGURÁVEL: PLPs 108/60/67 querem subir p/ 130–150k, mas em jun/2026 segue
# R$81k (nenhum sancionado). Dá pra trocar via env quando aprovarem.
MEI_TETO       = float(os.environ.get('SOMAJA_MEI_TETO', '81000'))
MEI_TOLERANCIA = round(MEI_TETO * 1.2, 2)   # tolerância de 20% (R$ 97.200)

# DAS mensal 2026 por atividade (vence dia 20). Fonte: Sebrae/Receita Federal.
MEI_ATIVIDADES = {
    'comercio':   {'label': 'Comércio ou Indústria',          'das': 82.05},
    'servico':    {'label': 'Serviços',                       'das': 86.05},
    'ambos':      {'label': 'Comércio + Serviços',            'das': 87.05},
    'transporte': {'label': 'Transporte (MEI Caminhoneiro)',  'das': 199.52},
}

# Categorias do modo Negócio — separam RECEITA (conta pro teto) das DESPESAS.
CATEGORIAS_MEI = [
    'venda', 'serviço prestado', 'outra receita',            # entradas (receita)
    'fornecedor', 'material', 'mercadoria', 'taxa', 'transporte',
    'aluguel', 'ferramenta', 'imposto', 'salário', 'outros',  # saídas (despesa)
]

SYSTEM_PARSER_MEI = """Você é o motor de extração financeira do SomaJá Negócio, usado por um \
MICROEMPREENDEDOR (MEI). A pessoa envia (texto, ÁUDIO transcrito ou FOTO de nota/comprovante) \
contando vendas, recebimentos ou despesas DO NEGÓCIO dela.

Extraia os lançamentos e devolva SOMENTE um JSON válido:
{"lancamentos":[{"tipo":"entrada","valor":150.0,"categoria":"venda","descricao":"venda de bolo"}]}

REGRAS:
- "tipo": "entrada" quando ENTROU dinheiro do negócio (venda, serviço prestado, recebimento de cliente) — isso conta pro FATURAMENTO.
  "saida" quando foi DESPESA do negócio (material, fornecedor, taxa, aluguel, ferramenta).
- "valor": número em reais (ponto decimal). Em foto de nota, use o valor total.
- "categoria": escolha UMA desta lista exata: %CATS%. Se não encaixar, use "outros".
- "descricao": curta (ex: "venda no balcão", "compra de farinha").
- Pode haver VÁRIOS lançamentos numa mensagem — retorne todos.
- Sem valor claro (ex: só uma pergunta ou "oi"), retorne {"lancamentos":[]}.
- NUNCA invente valor. Responda APENAS o JSON.
""".replace('%CATS%', ', '.join(CATEGORIAS_MEI))


SYSTEM_COACH_MEI = """Você é o SomaJá Negócio, o "contador de bolso" de um MICROEMPREENDEDOR (MEI) brasileiro. \
Fala como um parceiro esperto e direto — ZERO economês, frases curtas — mas que MANJA das regras do MEI.

Vou te dar os números do negócio da pessoa no ano e no mês (faturamento do ano, teto, quanto falta pro teto, \
DAS mensal, receita e despesas do mês). Sua tarefa: dar UM conselho prático e específico (máximo 4 frases) com AÇÃO CONCRETA.

REGRAS:
- Use SEMPRE número exato + prazo + o que fazer. Nada vago.
- Se estiver perto do teto (≥80%), avise com clareza e diga a saída (segurar nota, se preparar pra virar ME).
- Lembre do DAS (vence dia 20) e da DASN (até 31/maio) quando fizer sentido.
- Se as despesas estiverem altas vs a receita, aponte onde.
- Você ORGANIZA e LEMBRA — NÃO é o contador oficial nem dá parecer fiscal. Em caso sério, mande procurar o contador.
- Não invente números fora do que te dei. Use no máximo 1 ou 2 emojis.
"""


def vigia_teto(user_id):
    """Vigia do Teto (Lote 1): estado do faturamento do MEI no ano vs o teto.
    Retorna dict com faturado, teto, pct, restante, faixa e a 'linha' de alerta com AÇÃO."""
    fat  = faturamento_ano(user_id)
    teto = MEI_TETO
    pct  = (fat / teto * 100.0) if teto else 0.0
    restante = teto - fat
    ano = datetime.now().year
    if fat <= 0:
        linha = f'📈 Faturamento {ano}: {_brl(fat)} de {_brl(teto)}. Manda suas vendas que eu vou somando. 🟢'
        faixa = 'ok'
    elif pct < 80:
        linha = (f'📈 Faturado em {ano}: {_brl(fat)} de {_brl(teto)} ({pct:.0f}%). '
                 f'🟢 Faltam {_brl(restante)} pro teto.')
        faixa = 'ok'
    elif pct < 90:
        linha = (f'⚠️ Você já usou *{pct:.0f}%* do teto ({_brl(fat)} de {_brl(teto)}). '
                 f'Faltam {_brl(restante)} — fica de olho no ritmo das notas.')
        faixa = '80'
    elif pct < 100:
        linha = (f'🟠 *ATENÇÃO: {pct:.0f}% do teto!* ({_brl(fat)} de {_brl(teto)}). '
                 f'Faltam só {_brl(restante)}. Segura NF pro ano que vem ou já se prepara pra virar ME.')
        faixa = '90'
    elif fat <= MEI_TOLERANCIA:
        linha = (f'🔴 *Você passou o teto de {_brl(teto)}!* (está em {_brl(fat)}). '
                 f'Ainda dá: até {_brl(MEI_TOLERANCIA)} você paga um DAS complementar e vira ME em janeiro. '
                 f'Fala com seu contador.')
        faixa = '100'
    else:
        linha = (f'🔴🔴 *Passou de {_brl(MEI_TOLERANCIA)}* (está em {_brl(fat)})! '
                 f'Isso é desenquadramento RETROATIVO ao início do ano — procure seu contador URGENTE.')
        faixa = 'estouro'
    return {'faturado': fat, 'teto': teto, 'pct': pct, 'restante': restante, 'faixa': faixa, 'linha': linha}


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
# Menos é mais: UM plano só, 2 ciclos (anual no PIX = melhor custo). 1 login = 1 conta;
# quem divide em casa usa o MESMO login (sem carteira/família).
PLANOS = {
    'pro_anual':  {'label': 'Plano anual',  'valor': 149.00, 'cycle': 'YEARLY',  'rotulo': 'R$ 149/ano',   'destaque': True},
    'pro_mensal': {'label': 'Plano mensal', 'valor': 19.90,  'cycle': 'MONTHLY', 'rotulo': 'R$ 19,90/mês', 'destaque': False},
}

# SomaJá Negócio (MEI): mensal R$49,90 / anual R$299 à vista (50% off — âncora forte).
PLANOS_MEI = {
    'mei_anual':  {'label': 'Plano anual',  'valor': 299.00, 'cycle': 'YEARLY',  'rotulo': 'R$ 299/ano',   'destaque': True},
    'mei_mensal': {'label': 'Plano mensal', 'valor': 49.90,  'cycle': 'MONTHLY', 'rotulo': 'R$ 49,90/mês', 'destaque': False},
}

# Lookup único p/ checkout/pix (aceita plano pessoal OU MEI).
TODOS_PLANOS = {**PLANOS, **PLANOS_MEI}


def _planos_do_user(u):
    """Plano que aparece pro usuário: MEI (R$49,90/R$299) se modo negócio, senão o pessoal."""
    try:
        if u and u['modo'] == 'negocio':
            return PLANOS_MEI
    except (KeyError, IndexError):
        pass
    return PLANOS

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


def soma_webhook_ativar(customer_id, plano_key, ativar, payment_id=''):
    """Chamado pelo webhook global (app.py) p/ refs 'somaja_<customer_id>_<plano>'.
    Ativa/desativa a assinatura do usuário casado pelo asaas_customer_id.
    Se o cliente foi indicado por um afiliado, credita a comissão a cada pagamento."""
    if not customer_id:
        return False
    conn = get_somaja_db()
    u = conn.execute('SELECT id, email, nome, cpf, afiliado_ref, modo FROM somaja_users WHERE asaas_customer_id=?',
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
    # Afiliados: cliente pagou → credita+paga a comissão do afiliado que o trouxe (idempotente por payment_id)
    if ativar and payment_id:
        try:
            ref_af = u['afiliado_ref']
        except (KeyError, IndexError):
            ref_af = None
        if ref_af:
            try:
                from afiliados import registrar_comissao
                try:
                    _neg = (u['modo'] == 'negocio')
                except (KeyError, IndexError):
                    _neg = False
                _app_af = 'somaja_mei' if _neg else 'somaja'
                _ccpf = (u['cpf'] if 'cpf' in u.keys() else '') or ''
                registrar_comissao(ref_af, _app_af, payment_id, u['nome'],
                                   cliente_email=(u['email'] or ''), cliente_cpf=_ccpf)
            except Exception as _e:
                log.warning(f'[SomaJá] comissão afiliado: {_e}')
    log.info(f'[SomaJá] Assinatura {"ATIVADA" if ativar else "cortada"} (customer {customer_id})')
    return True


# ── Motor: registrar lançamento (canal-agnóstico: web hoje, WhatsApp no Lote 2) ──
def registrar_lancamento(user_id, texto=None, file_bytes=None, mime=None, fonte='texto'):
    """Lê a mensagem (texto/áudio/foto) com a IA, grava os lançamentos e devolve
    um dict com a confirmação amigável + saldo atualizado.
    Retorna (resposta:str, lancamentos:list, tokens_in, tokens_out)."""
    import base64
    # O modo do usuário decide o "cérebro": pessoal x negócio (MEI).
    _c = get_somaja_db()
    _r = _c.execute('SELECT modo FROM somaja_users WHERE id=?', (user_id,)).fetchone()
    _c.close()
    negocio = bool(_r and (_r['modo'] == 'negocio'))
    parser  = SYSTEM_PARSER_MEI if negocio else SYSTEM_PARSER
    cats_ok = CATEGORIAS_MEI if negocio else CATEGORIAS
    parts = []
    if file_bytes and mime:
        parts.append({'inlineData': {'mimeType': mime, 'data': base64.b64encode(file_bytes).decode('ascii')}})
    if texto:
        parts.append({'text': texto})
    if not parts:
        return 'Manda o gasto que eu anoto! Ex: "almoço 35" 😉', [], 0, 0
    contents = [{'role': 'user', 'parts': parts}]
    raw, tin, tout = _gemini_call(parser, contents, json_mode=True,
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
        if cat not in cats_ok:
            cat = 'outros'
        desc = (l.get('descricao') or cat)[:120]
        add_tx(user_id, tipo, valor, cat, desc, fonte=fonte)
        salvos.append({'tipo': tipo, 'valor': valor, 'categoria': cat, 'descricao': desc})

    if not salvos:
        return ('Não achei nenhum valor nessa mensagem. 🤔 Tenta assim: '
                '"mercado 130" ou manda a foto da notinha.'), [], tin, tout

    # Confirmação amigável + saldo (pessoal) ou faturamento do mês (negócio)
    linhas = []
    for s in salvos:
        if negocio:
            emoji = '💰' if s['tipo'] == 'entrada' else '🧾'
            verbo = 'Receita' if s['tipo'] == 'entrada' else 'Despesa'
        else:
            emoji = '💸' if s['tipo'] == 'saida' else '💰'
            verbo = 'Saída' if s['tipo'] == 'saida' else 'Entrada'
        linhas.append(f'{emoji} {verbo}: {_brl(s["valor"])} — {s["descricao"]} ({s["categoria"]})')
    s = saldo_mes(user_id)
    if negocio:
        # Vigia do Teto (Lote 1): mostra o faturamento do ANO vs o teto, com ação.
        v = vigia_teto(user_id)
        resposta = ('✅ Anotei!\n' + '\n'.join(linhas) + '\n\n' + v['linha'])
    else:
        bola = '🟢' if s['saldo'] >= 0 else '🔴'
        resposta = ('✅ Anotei!\n' + '\n'.join(linhas) +
                    f'\n\nSaldo do mês: {_brl(s["saldo"])} {bola}')
    return resposta, salvos, tin, tout


def gerar_conselho(user_id):
    """Monta o resumo e pede 1 conselho ao coach (mode-aware: pessoal x negócio/MEI).
    Funciona no web, no webhook do WhatsApp e no coach automático em background."""
    conn = get_somaja_db()
    u = conn.execute('SELECT * FROM somaja_users WHERE id=?', (user_id,)).fetchone()
    conn.close()
    try:
        negocio = bool(u and u['modo'] == 'negocio')
    except (KeyError, IndexError):
        negocio = False
    s = saldo_mes(user_id)

    if negocio:
        v = vigia_teto(user_id)
        if v['faturado'] <= 0 and s['saidas'] == 0:
            return ('Ainda não tenho lançamentos do teu negócio esse ano. '
                    'Manda suas vendas e despesas que eu te dou um raio-x certeiro do MEI! 🧾')
        cats = resumo_categorias(user_id, tipo='saida')[:5]
        atv = (u['mei_atividade'] or '') if u else ''
        das = (u['mei_das_valor'] or 0) if u else 0
        atv_label = MEI_ATIVIDADES.get(atv, {}).get('label', 'MEI')
        resumo = (f'Atividade: {atv_label}\n'
                  f'DAS mensal: {_brl(das)} (vence dia 20)\n'
                  f'Faturado no ano: {_brl(v["faturado"])} de {_brl(v["teto"])} ({v["pct"]:.0f}%)\n'
                  f'Falta pro teto: {_brl(v["restante"])}\n'
                  f'Receita do mês: {_brl(s["entradas"])}\n'
                  f'Despesas do mês: {_brl(s["saidas"])}\n'
                  'Maiores despesas:\n' +
                  ('\n'.join(f'- {c}: {_brl(t)}' for c, t in cats) or '- (nenhuma)'))
        prompt = SYSTEM_COACH_MEI
    else:
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
        prompt = SYSTEM_COACH

    try:
        texto, _tin, _tout = _gemini_call(
            prompt, [{'role': 'user', 'parts': [{'text': resumo}]}],
            json_mode=False, max_tokens=400, temperature=0.5)
    except Exception as e:
        log.warning(f'[SomaJá] coach falhou: {e}')
        return 'Não consegui gerar o conselho agora. Tenta de novo daqui a pouquinho. 🙏'
    if texto:
        registrar_conselho(user_id, texto)
    return texto or 'Continua firme nos registros que semana que vem eu te trago um panorama! 💪'


# ── Balanço / fechamento do mês (atual vs mês anterior + veredito) ──────────────
def _prev_ym():
    now = datetime.now()
    y, m = now.year, now.month
    return f'{y-1}-12' if m == 1 else f'{y}-{m-1:02d}'


def _balanco_texto(user_id, negocio=False):
    """Fechamento do mês em texto (WhatsApp e web). Compara com o mês anterior."""
    a = saldo_mes(user_id)                       # mês atual
    b = saldo_mes(user_id, _prev_ym())           # mês anterior
    cats = resumo_categorias(user_id, tipo='saida')[:5]
    if negocio:
        v = vigia_teto(user_id)
        txt = ('🧮 *Balanço do mês*\n'
               f'💰 Receita: {_brl(a["entradas"])}\n'
               f'🧾 Despesas: {_brl(a["saidas"])}\n'
               f'📦 Sobrou: {_brl(a["saldo"])}\n\n{v["linha"]}')
        rotulo_gasto = 'despesas'
    else:
        bola = '🟢' if a['saldo'] >= 0 else '🔴'
        vered = ('Sobrou' if a['saldo'] >= 0 else 'Faltou') + f' {_brl(abs(a["saldo"]))} {bola}'
        txt = ('🧮 *Balanço do mês*\n'
               f'📥 Entrou: {_brl(a["entradas"])}\n'
               f'📤 Saiu: {_brl(a["saidas"])}\n'
               f'💰 {vered}')
        rotulo_gasto = 'gastos'
    # Comparação com o mês anterior (só se houver histórico)
    if b['saidas'] > 0 or b['entradas'] > 0:
        dif = a['saidas'] - b['saidas']
        if dif > 0:
            comp = f'🔺 {_brl(dif)} a mais'
        elif dif < 0:
            comp = f'🔻 {_brl(abs(dif))} a menos'
        else:
            comp = 'igualzinho'
        txt += f'\n\n*vs mês passado:* seus {rotulo_gasto} ficaram {comp}.'
    if cats:
        txt += '\n\n*Maiores gastos:*\n' + '\n'.join(f'• {c}: {_brl(t)}' for c, t in cats)
    return txt


# ── Rotas: público / auth ───────────────────────────────────────────────────────
@somaja_bp.route('/')
def landing():
    ref = (request.args.get('ref') or '').strip().upper()[:12]
    if ref:
        session['soma_ref'] = ref   # guarda o afiliado que trouxe (Lote afiliados)
    session.pop('soma_modo', None)  # entrou pela porta pessoal
    return render_template('somaja/landing.html')


@somaja_bp.route('/mei')
def mei_landing():
    """Landing pública do SomaJá Negócio (MEI) — quem vem por aqui já entra no modo Negócio."""
    ref = (request.args.get('ref') or '').strip().upper()[:12]
    if ref:
        session['soma_ref'] = ref
    session['soma_modo'] = 'negocio'
    return render_template('somaja/mei.html', planos=PLANOS_MEI, teto=MEI_TETO, _brl=_brl)


# ── PWA: service worker (instalável "como app" na tela do celular) ──────────────
_SW_JS = """const CACHE='somaja-v1';
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => {});
"""


@somaja_bp.route('/sw.js')
def sw_js():
    return Response(_SW_JS, mimetype='application/javascript',
                    headers={'Service-Worker-Allowed': '/somaja/'})


@somaja_bp.route('/instalar')
def instalar():
    """Página que ensina o cliente a instalar o SomaJá como app (ícone na tela)."""
    return render_template('somaja/instalar.html')


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
                ref_af = (session.get('soma_ref') or request.args.get('ref') or '').strip().upper()[:12]
                cur = conn.execute(
                    'INSERT INTO somaja_users (nome,email,telefone,renda,password_hash,trial_until,afiliado_ref,created_at) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    (nome, email, telefone, renda, generate_password_hash(senha),
                     trial_until, (ref_af or None), datetime.now().isoformat()))
                conn.commit()
                uid = cur.lastrowid
                # Veio da landing do MEI? já entra no modo Negócio (vai escolher a atividade).
                quer_mei = (session.get('soma_modo') == 'negocio')
                if quer_mei:
                    conn.execute('UPDATE somaja_users SET modo=? WHERE id=?', ('negocio', uid))
                    conn.commit()
                conn.close()
                session.pop('soma_modo', None)
                session['soma_user_id']   = uid
                session['soma_user_nome'] = nome
                return redirect('/somaja/negocio' if quer_mei else '/somaja/app')
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


@somaja_bp.route('/esqueci', methods=['GET', 'POST'])
def esqueci():
    msg = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        conn = get_somaja_db()
        u = conn.execute('SELECT id, nome FROM somaja_users WHERE email=?', (email,)).fetchone()
        if u:
            token = secrets.token_urlsafe(32)
            exp = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute('UPDATE somaja_users SET reset_token=?, reset_expires=? WHERE id=?',
                         (token, exp, u['id']))
            conn.commit()
            link = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/') + '/somaja/redefinir?token=' + token
            try:
                _enviar_email(email, '🔑 SomaJá — redefinir sua senha',
                    f'<p>Oi, {(u["nome"] or "").split()[0]}! Pra criar uma nova senha do SomaJá, '
                    f'clica no link abaixo (vale 2 horas):</p>'
                    f'<p><a href="{link}">Redefinir minha senha</a></p>'
                    f'<p style="color:#888;font-size:13px">Se não foi você que pediu, é só ignorar este e-mail.</p>')
            except Exception:
                pass
        conn.close()
        # Sempre mostra sucesso (não revela se o e-mail tem conta — anti-enumeração)
        msg = 'Se esse e-mail tiver conta, enviamos o link de redefinição. Confira sua caixa (e o spam).'
    return render_template('somaja/esqueci.html', msg=msg)


@somaja_bp.route('/redefinir', methods=['GET', 'POST'])
def redefinir():
    token = (request.args.get('token') or request.form.get('token') or '').strip()
    conn = get_somaja_db()
    u = conn.execute('SELECT id, reset_expires FROM somaja_users WHERE reset_token=?', (token,)).fetchone() if token else None
    valido = bool(u and u['reset_expires'] and u['reset_expires'] >= datetime.now().isoformat())
    if not valido:
        conn.close()
        return render_template('somaja/redefinir.html', valido=False, token=token, erro=None, ok=False)
    erro = None
    if request.method == 'POST':
        senha = request.form.get('senha') or ''
        if len(senha) < 6:
            erro = 'A senha precisa de pelo menos 6 caracteres.'
        else:
            conn.execute('UPDATE somaja_users SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?',
                         (generate_password_hash(senha), u['id']))
            conn.commit(); conn.close()
            return render_template('somaja/redefinir.html', valido=True, token=token, ok=True, erro=None)
    conn.close()
    return render_template('somaja/redefinir.html', valido=True, token=token, ok=False, erro=erro)


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
    out = {'ok': True, 'saldo': s,
           'entradas_fmt': _brl(s['entradas']), 'saidas_fmt': _brl(s['saidas']),
           'saldo_fmt': _brl(s['saldo']), 'categorias': linhas}
    try:
        if u['modo'] == 'negocio':
            v = vigia_teto(u['id'])
            out['negocio'] = True
            out['teto'] = {'faturado_fmt': _brl(v['faturado']), 'teto_fmt': _brl(v['teto']),
                           'pct': round(v['pct']), 'restante_fmt': _brl(v['restante']),
                           'linha': v['linha'], 'faixa': v['faixa']}
    except (KeyError, IndexError):
        pass
    return jsonify(out)


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


@somaja_bp.route('/apagar', methods=['POST'])
@somaja_acesso_required
def apagar():
    """Apaga um lançamento do próprio usuário (correção de erro)."""
    u = _get_user()
    data = request.get_json(silent=True) or {}
    ok = del_tx(u['id'], data.get('id'))
    s = saldo_mes(u['id'])
    return jsonify({'ok': ok, 'saldo_fmt': _brl(s['saldo'])})


@somaja_bp.route('/balanco')
@somaja_acesso_required
def balanco():
    """Fechamento do mês (atual vs anterior)."""
    u = _get_user()
    try:
        neg = (u['modo'] == 'negocio')
    except (KeyError, IndexError):
        neg = False
    return jsonify({'ok': True, 'texto': _balanco_texto(u['id'], neg)})


@somaja_bp.route('/negocio', methods=['GET', 'POST'])
@somaja_acesso_required
def negocio():
    """SomaJá Negócio (MEI) — ativar o modo Negócio pela web (Lote 0)."""
    u = _get_user()
    msg = None
    if request.method == 'POST':
        if request.form.get('acao') == 'pessoal':
            set_modo(u['id'], 'pessoal')
            return redirect('/somaja/app')
        atv = (request.form.get('atividade') or '').strip()
        if atv in MEI_ATIVIDADES:
            set_mei_perfil(u['id'], atv, MEI_ATIVIDADES[atv]['das'])
            return redirect('/somaja/app')
        msg = 'Escolha sua atividade pra ativar o modo Negócio.'
        u = _get_user()
    return render_template('somaja/negocio.html', u=u, atividades=MEI_ATIVIDADES,
                           teto=MEI_TETO, _brl=_brl, msg=msg)


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
                           gerado=datetime.now().strftime('%d/%m/%Y às %H:%M'))


# ── Rotas: assinatura (paywall) ─────────────────────────────────────────────────
@somaja_bp.route('/assinar')
@somaja_login_required
def assinar():
    u = _get_user()
    return render_template('somaja/assinar.html', u=u, planos=_planos_do_user(u),
                           trial_dias=dias_de_trial_restantes(u))


@somaja_bp.route('/checkout/<plano>', methods=['GET', 'POST'])
@somaja_login_required
def checkout(plano):
    u = _get_user()
    if plano not in _planos_do_user(u):   # negócio só assina MEI; pessoal só assina pessoal
        return redirect('/somaja/assinar')
    p = TODOS_PLANOS[plano]
    desc_prod = 'SomaJá Negócio' if plano.startswith('mei_') else 'SomaJá'
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
                    'cycle': p['cycle'], 'description': f'{desc_prod} — {p["label"]}',
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
    if plano not in TODOS_PLANOS or not u['asaas_subscription_id']:
        return redirect('/somaja/assinar')
    qr = copia = ''
    payments = _asaas_req('GET', f'/subscriptions/{u["asaas_subscription_id"]}/payments?limit=1')
    if payments.get('data'):
        pid = payments['data'][0].get('id', '')
        if pid:
            resp = _asaas_req('GET', f'/payments/{pid}/pixQrCode')
            qr    = resp.get('encodedImage', '')
            copia = resp.get('payload', '')
    return render_template('somaja/pix.html', u=u, plano=plano, p=TODOS_PLANOS[plano], qr=qr, copia=copia)


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
        pay0 = payments['data'][0]
        st = (pay0.get('status') or '').upper()
        if st in ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH'):
            soma_webhook_ativar(u['asaas_customer_id'], u['plano'], True,
                                payment_id=pay0.get('id', ''))
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
    # Casa pelos 11 dígitos nacionais (DDD+nº), por IGUALDADE — sem LIKE '%...'
    # (o LIKE de 10 dígitos podia casar números diferentes que terminam igual).
    d11 = digits[-11:] if len(digits) >= 11 else digits
    conn = get_somaja_db()
    u = conn.execute("SELECT * FROM somaja_users WHERE substr(telefone,-11)=? ORDER BY id LIMIT 1",
                     (d11,)).fetchone()
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
    """Cap diário de mensagens por usuário (protege o custo de IA).
    Atômico: a contagem é recalculada numa única instrução (sem read-then-write)."""
    hoje = datetime.now().strftime('%Y-%m-%d')
    conn = get_somaja_db()
    conn.execute('UPDATE somaja_users SET '
                 'wa_count = CASE WHEN wa_day=? THEN COALESCE(wa_count,0)+1 ELSE 1 END, '
                 'wa_day = ? WHERE id=?', (hoje, hoje, user_id))
    conn.commit()
    row = conn.execute('SELECT wa_count FROM somaja_users WHERE id=?', (user_id,)).fetchone()
    conn.close()
    return (row['wa_count'] if row else 1) <= WA_RATE_DIA


def _wa_ajuda(u):
    dias = dias_de_trial_restantes(u)
    try:
        negocio = (u['modo'] == 'negocio')
    except (KeyError, IndexError):
        negocio = False
    if negocio:
        base = ('🧾 *SomaJá Negócio* — seu contador de bolso (MEI).\n\n'
                'Me manda as *vendas* e *despesas* do negócio:\n'
                '• Escreve: *"vendi 200"* / *"material 80"*\n'
                '• Manda um *áudio* ou a *foto da nota*\n\n'
                'Comandos:\n'
                '📊 *resumo* — faturamento do mês\n'
                '🧮 *balanço* — fechamento do mês\n'
                '🧠 *conselho* — dica do coach\n'
                '🗑️ *apagar* — corrigir um lançamento\n'
                '🐗 *modo pessoal* — voltar pras contas de casa\n')
    else:
        base = ('🐗 *Oi! Eu sou o SomaJá*, seu coach financeiro.\n\n'
                'É só me mandar seus gastos que eu somo tudo:\n'
                '• Escreve: *"mercado 130"*\n'
                '• Manda um *áudio* falando o gasto\n'
                '• Tira *foto da notinha*\n\n'
                'Comandos:\n'
                '📊 *resumo* — saldo do mês\n'
                '🧮 *balanço* — fechamento do mês\n'
                '🧠 *conselho* — dica do coach\n'
                '🗑️ *apagar* — corrigir um lançamento\n'
                '🧾 *sou mei* — virar o modo Negócio (pro MEI)\n')
    if not u['plan_active'] and dias >= 0:
        base += f'\n🎁 Você está no teste grátis ({dias} dia(s)).'
    return base


@somaja_bp.route('/wa/webhook', methods=['GET', 'POST'])
def wa_webhook():
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200
    secret = os.environ.get('SOMAJA_WA_WEBHOOK_SECRET', '').strip()
    if not secret:
        log.error('[SomaJá] SOMAJA_WA_WEBHOOK_SECRET nao configurado — webhook bloqueado.')
        return jsonify({'error': 'not configured'}), 503
    recv = (request.args.get('key', '') or request.headers.get('x-webhook-key', '')).strip()
    if recv != secret:
        return jsonify({'error': 'unauthorized'}), 401
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

        # ── Modo Negócio (MEI) — onboarding e troca de modo ──────────────────
        try:
            _onb = u['mei_onboard']
        except (KeyError, IndexError):
            _onb = None
        if _onb == 'atividade':
            _mapa = {'1': 'comercio', '2': 'servico', '3': 'ambos', '4': 'transporte',
                     'comercio': 'comercio', 'comércio': 'comercio', 'servico': 'servico',
                     'serviço': 'servico', 'servicos': 'servico', 'serviços': 'servico',
                     'ambos': 'ambos', 'os dois': 'ambos', 'transporte': 'transporte',
                     'caminhoneiro': 'transporte'}
            _atv = _mapa.get(low)
            if not _atv:
                wa_send(telefone, 'Só o número 😉  *1* Comércio/Indústria · *2* Serviços · *3* os dois · *4* Transporte')
                return jsonify({'ok': True}), 200
            _das = MEI_ATIVIDADES[_atv]['das']
            set_mei_perfil(u['id'], _atv, _das)
            wa_send(telefone,
                    f'✅ Pronto! Ativei o *modo Negócio (MEI)* — {MEI_ATIVIDADES[_atv]["label"]}.\n\n'
                    f'Seu DAS é *{_brl(_das)}* (vence dia 20).\n'
                    f'Agora me manda suas *vendas* e *despesas* que eu separo o que conta pro seu '
                    f'teto de {_brl(MEI_TETO)}/ano. 🧾\n\n'
                    'Ex: *"vendi 200"*, *"material 80"* ou a foto da nota.')
            return jsonify({'ok': True}), 200
        if low in ('sou mei', 'modo negocio', 'modo negócio', 'negocio', 'negócio', 'virar mei', 'mei'):
            set_mei_onboard(u['id'], 'atividade')
            wa_send(telefone,
                    '🧾 *Modo Negócio (MEI)* — vou virar seu contador de bolso.\n\n'
                    'Qual a sua atividade?\n'
                    '*1* Comércio ou Indústria\n*2* Serviços\n*3* os dois\n*4* Transporte (caminhoneiro)\n\n'
                    'Responde só o número.')
            return jsonify({'ok': True}), 200
        if low in ('modo pessoal', 'sair do negocio', 'sair do negócio', 'voltar pessoal'):
            set_modo(u['id'], 'pessoal')
            wa_send(telefone, 'Pronto, voltei pro *modo pessoal* 🐗 (suas contas de casa).')
            return jsonify({'ok': True}), 200

        # Comandos
        if low in ('resumo', 'saldo', 'extrato', 'teto', 'faturamento'):
            s = saldo_mes(u['id']); cats = resumo_categorias(u['id'], tipo='saida')[:5]
            try:
                _neg = (u['modo'] == 'negocio')
            except (KeyError, IndexError):
                _neg = False
            if _neg:
                v = vigia_teto(u['id'])
                txt = (f'📊 *Resumo do mês*\n💰 Receita: {_brl(s["entradas"])}\n'
                       f'🧾 Despesas: {_brl(s["saidas"])}\n📦 Sobrou: {_brl(s["saldo"])}\n\n'
                       f'{v["linha"]}')
                if cats:
                    txt += '\n\n*Maiores despesas:*\n' + '\n'.join(f'• {c}: {_brl(t)}' for c, t in cats)
            else:
                txt = (f'📊 *Resumo do mês*\n📥 Entrou: {_brl(s["entradas"])}\n'
                       f'📤 Saiu: {_brl(s["saidas"])}\n💰 Saldo: {_brl(s["saldo"])}')
                if cats:
                    txt += '\n\n*Pra onde foi:*\n' + '\n'.join(f'• {c}: {_brl(t)}' for c, t in cats)
            wa_send(telefone, txt)
            return jsonify({'ok': True}), 200
        if low in ('balanço', 'balanco', 'fechar mes', 'fechar mês', 'fechamento', 'fecha o mes', 'fecha o mês'):
            try:
                _neg = (u['modo'] == 'negocio')
            except (KeyError, IndexError):
                _neg = False
            wa_send(telefone, _balanco_texto(u['id'], _neg))
            return jsonify({'ok': True}), 200
        # Apagar lançamento (correção de erro) — 'apagar' lista, 'apagar N' apaga
        if low.startswith('apagar ') and low.split()[1].isdigit():
            n = int(low.split()[1])
            ult = ultimos_tx(u['id'], 5)
            if 1 <= n <= len(ult):
                t = ult[n - 1]
                if del_tx(u['id'], t['id']):
                    s = saldo_mes(u['id'])
                    wa_send(telefone, f'🗑️ Apaguei: {t["descricao"]} — {_brl(t["valor"])}.\n'
                                      f'Saldo do mês agora: {_brl(s["saldo"])}')
                else:
                    wa_send(telefone, 'Não consegui apagar esse. Tenta *apagar* de novo.')
            else:
                wa_send(telefone, f'Só achei {len(ult)} lançamento(s). Manda *apagar* pra ver a lista.')
            return jsonify({'ok': True}), 200
        if low in ('apagar', 'corrigir', 'excluir', 'remover', 'deletar'):
            ult = ultimos_tx(u['id'], 5)
            if not ult:
                wa_send(telefone, 'Você ainda não tem lançamentos pra apagar. 🤔')
                return jsonify({'ok': True}), 200
            linhas = []
            for i, t in enumerate(ult, 1):
                emoji = '💸' if t['tipo'] == 'saida' else '💰'
                linhas.append(f'*{i}.* {emoji} {t["descricao"]} — {_brl(t["valor"])}')
            wa_send(telefone, 'Qual apagar? Manda *apagar N* (ex: *apagar 2*):\n\n' + '\n'.join(linhas))
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


# ── Lote 2: Lembretes de DAS/DASN no WhatsApp (o "avisa sozinho" do MEI) ─────────
# OPT-IN (anti-ban): só dispara com SOMAJA_LEMBRETES=1 — ligar só com o chip aquecido.
def _lembrete_devido(u, now):
    """Decide qual lembrete o MEI deve receber hoje: 'das', 'dasn' ou None.
    Idempotente por mês/ano (colunas mei_das_lembrete='YYYY-MM' / mei_dasn_lembrete='YYYY')."""
    def g(k):
        try: return u[k]
        except (KeyError, IndexError): return None
    dia, mes, ano = now.day, now.strftime('%Y-%m'), now.strftime('%Y')
    try:
        das_valor = float(g('mei_das_valor') or 0)
    except (TypeError, ValueError):
        das_valor = 0
    # DAS vence dia 20 → lembra entre 16 e 19, 1x no mês (só quem tem atividade/DAS definido)
    if 16 <= dia <= 19 and das_valor > 0 and (g('mei_das_lembrete') or '') != mes:
        return 'das'
    # DASN vence 31/05 → lembra em maio (18 a 28), 1x no ano
    if now.month == 5 and 18 <= dia <= 28 and (g('mei_dasn_lembrete') or '') != ano:
        return 'dasn'
    return None


def _lembretes_loop():
    import time as _t
    if os.environ.get('SOMAJA_LEMBRETES', '0') != '1':
        log.info('[SomaJá] Lembretes MEI DESLIGADOS (set SOMAJA_LEMBRETES=1 — só com chip aquecido)')
        return
    _t.sleep(240)  # deixa o app subir
    log.info('[SomaJá] Lembretes MEI ATIVOS (DAS dia ~17 + DASN em maio)')
    while True:
        try:
            now = datetime.now()
            mes, ano = now.strftime('%Y-%m'), now.strftime('%Y')
            conn = get_somaja_db()
            negs = conn.execute(
                "SELECT id, telefone, plan_active, trial_until, mei_das_valor, "
                "mei_das_lembrete, mei_dasn_lembrete FROM somaja_users "
                "WHERE modo='negocio' AND telefone IS NOT NULL AND telefone<>''").fetchall()
            conn.close()
            for u in negs:
                if not tem_acesso(u):
                    continue
                tipo = _lembrete_devido(u, now)
                if not tipo:
                    continue
                if tipo == 'das':
                    msg = (f'🧾 *Lembrete do DAS* — seu imposto de {_brl(u["mei_das_valor"] or 0)} '
                           f'vence *dia 20*. Paga pelo app MEI ou no gov.br que tá tudo certo. 👍')
                    col, val = 'mei_das_lembrete', mes
                else:
                    msg = ('📋 *Declaração anual do MEI (DASN)* vence *31/05*. '
                           'Não esquece — esquecer deixa seu CNPJ irregular! '
                           'Faz no gov.br (Portal do Simples), leva 2 minutos. 🗓️')
                    col, val = 'mei_dasn_lembrete', ano
                try:
                    wa_send(u['telefone'], msg)
                    _c = get_somaja_db()
                    _c.execute(f'UPDATE somaja_users SET {col}=? WHERE id=?', (val, u['id']))
                    _c.commit(); _c.close()
                    _t.sleep(3)  # respira entre envios (anti-ban)
                except Exception as _e:
                    log.warning(f'[SomaJá] lembrete {tipo} user {u["id"]}: {_e}')
        except Exception as e:
            log.error(f'[SomaJá] lembretes loop: {e}')
        _t.sleep(43200)  # checa 2x/dia


threading.Thread(target=_lembretes_loop, daemon=True, name='somaja-lembretes').start()
