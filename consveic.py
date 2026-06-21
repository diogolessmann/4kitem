"""
consveic.py — Blueprint do Consulta Veicular (módulo do 4kitem)

Portal White Label de débitos veiculares por cima da API B2B da Zapay.
Jornada: placa → consulta (async/webhook) → escolher débitos → pagar/parcelar
(PIX/cartão, async/webhook) → confirmação. Comissão do Diogo aplicada por cima.

Toda a API da Zapay é ASSÍNCRONA: você dispara um POST e o resultado chega
depois no nosso /webhook. Por isso o estado real mora no consveic_db, e as telas
fazem "polling" leve até o webhook atualizar.

Sandbox-ready: tudo por env var. No dia que o parceiros@usezapay.com.br mandar as
credenciais, é só preencher no Railway e virar a chave (ZAPAY_ENV=sandbox→production).

ENV VARS:
  ZAPAY_ENV         sandbox | production           (default: sandbox)
  ZAPAY_USER        username (HTTP Basic)
  ZAPAY_PASS        password (HTTP Basic)
  ZAPAY_SECRET      secret_key (valida HMAC do webhook)
  ZAPAY_WEBHOOK_URL URL pública do nosso /webhook   (ex: https://4kitem.com.br/consulta-veicular/webhook)
  CONSVEIC_MARKUP_PCT   % de comissão sobre os débitos  (ex: 8  => 8%)
  CONSVEIC_MARKUP_FIXO  taxa fixa por pedido em R$       (ex: 9.90)
  CONSVEIC_ADMIN_PW     senha do painel /admin

Docs: https://docs-b2b.usezapay.com.br/docs/bem-vindo
"""
import os
import hmac
import json
import hashlib
import logging
from datetime import datetime
from functools import wraps

import requests
from flask import (Blueprint, request, jsonify, render_template_string,
                   redirect, session, Response)

from consveic_db import (init_consveic_db, criar_consulta, atualizar_consulta,
                          obter_consulta, criar_pedido, atualizar_pedido,
                          obter_pedido, listar_pedidos, registrar_evento,
                          evento_ja_processado, estatisticas)

log = logging.getLogger('consveic')

consveic_bp = Blueprint('consveic', __name__, url_prefix='/consulta-veicular')

# ── Config (tudo via env) ─────────────────────────────────────────────────────
ZAPAY_ENV    = os.environ.get('ZAPAY_ENV', 'sandbox').strip().lower()
ZAPAY_USER   = os.environ.get('ZAPAY_USER', '').strip()
ZAPAY_PASS   = os.environ.get('ZAPAY_PASS', '').strip()
ZAPAY_SECRET = os.environ.get('ZAPAY_SECRET', '').strip()
ZAPAY_WEBHOOK_URL = os.environ.get('ZAPAY_WEBHOOK_URL', '').strip()

MARKUP_PCT   = float(os.environ.get('CONSVEIC_MARKUP_PCT', '8') or 0)
MARKUP_FIXO  = float(os.environ.get('CONSVEIC_MARKUP_FIXO', '0') or 0)
ADMIN_PW     = os.environ.get('CONSVEIC_ADMIN_PW') or os.environ.get('SAAS_ADMIN_PASSWORD') or os.urandom(24).hex()

# URLs base por ambiente (cravadas na doc /docs/preparacao-de-ambientes)
_HOST = 'api.b2b.sandbox.usezapay.com.br' if ZAPAY_ENV != 'production' else 'api.b2b.usezapay.com.br'
VEHICLE_BASE = f'https://{_HOST}/v2/vehicle'
PAYMENT_BASE = f'https://{_HOST}/v2/payment'
WEBHOOK_BASE = f'https://{_HOST}/v2/webhook'

UF_NAO_SUPORTADAS = {'SE'}   # Sergipe: DETRAN externo (doc)


def _configurado():
    """True se as credenciais estão setadas (senão roda em modo 'aguardando credenciais')."""
    return bool(ZAPAY_USER and ZAPAY_PASS)


def calcular_comissao(total_debitos):
    """Nossa margem por cima dos débitos."""
    return round(total_debitos * (MARKUP_PCT / 100.0) + MARKUP_FIXO, 2)


# ── Cliente HTTP da Zapay ─────────────────────────────────────────────────────
def _zapay(method, url, payload=None, timeout=30):
    """
    Chamada autenticada na API da Zapay.
    Auth: HTTP Basic (username/password) conforme /docs/credenciais-de-acesso.
    (Se a Zapay exigir JWT no Authorization, trocar aqui pelo fluxo de token.)
    """
    if not _configurado():
        raise RuntimeError('Credenciais Zapay ausentes (ZAPAY_USER/ZAPAY_PASS).')
    try:
        resp = requests.request(
            method, url,
            auth=(ZAPAY_USER, ZAPAY_PASS),
            json=payload,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
            timeout=timeout,
        )
        ct = resp.headers.get('content-type', '')
        data = resp.json() if 'application/json' in ct else {'raw': resp.text}
        if resp.status_code >= 400:
            log.warning('[Zapay] %s %s -> %s %s', method, url, resp.status_code, str(data)[:300])
        return resp.status_code, data
    except Exception as e:
        log.error('[Zapay] erro em %s %s: %s', method, url, e)
        return 0, {'error': str(e)}


def consultar_debitos(placa, dados_extra=None):
    """
    POST /v2/vehicle/debts — dispara a consulta. Manda só a placa (modo enriquecido:
    a Zapay coleta RENAVAM e campos obrigatórios de cada estado sozinha).
    Retorna (status_code, {request_id: ...}). O RESULTADO chega no webhook.
    """
    payload = {'plate': (placa or '').upper().strip()}
    if dados_extra:
        payload.update(dados_extra)          # modo sem enriquecimento (campos por estado)
    # TODO sandbox: confirmar nome do campo da placa ('plate' vs 'placa') e o path exato.
    return _zapay('POST', f'{VEHICLE_BASE}/debts', payload)


def criar_pagamento(cliente, itens, meio='pix', parcelas=1, external_id=None,
                    tokenizar=False):
    """
    POST /v2/payment/create_order — cria o pedido de pagamento.
    `itens`: lista de débitos (classificados Vehicle Debt / Other / Batch Payment).
    Retorna (status_code, {order_id: ..., pix?...}). STATUS chega no webhook.
    """
    payment_method = {'type': meio}
    if meio == 'credit_card' and parcelas > 1:
        payment_method['installments'] = int(parcelas)
        if tokenizar:
            payment_method['tokenize_on_success'] = True

    payload = {
        'customer': cliente,                 # {name, document, email, phone...}
        'items': itens,                      # [{type:'vehicle_debt', amount:..., ...}]
        'payment_method': payment_method,
    }
    if external_id:
        payload['external_id'] = external_id
    # TODO sandbox: confirmar shape exato de customer/items/payment_method no /docs/pedido-de-pagamento.
    return _zapay('POST', f'{PAYMENT_BASE}/create_order', payload)


def registrar_webhook_zapay(url=None, resource='vehicle_debt'):
    """
    POST /v2/webhook/ — registra nossa URL pra receber eventos.
    Roda uma vez no setup (ou via /admin). A Zapay valida mandando 'webhook_validation'.
    """
    url = url or ZAPAY_WEBHOOK_URL
    if not url:
        return 0, {'error': 'ZAPAY_WEBHOOK_URL não configurada'}
    payload = {'url': url, 'resource': resource}
    return _zapay('POST', f'{WEBHOOK_BASE}/', payload)


# ── Validação de webhook (HMAC) ───────────────────────────────────────────────
def _hmac_ok(raw_body, assinatura):
    """SHA256 HMAC do corpo cru com a secret_key, comparado ao header x-hmac-signature."""
    if not ZAPAY_SECRET or not assinatura:
        return False
    esperado = hmac.new(ZAPAY_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, assinatura.strip())


def _somar_debitos(debitos):
    """Soma defensiva do valor total dos débitos (campo pode variar; tenta vários)."""
    total = 0.0
    for d in (debitos or []):
        for k in ('amount', 'value', 'valor', 'total'):
            if isinstance(d, dict) and d.get(k) is not None:
                try:
                    total += float(d[k]); break
                except (TypeError, ValueError):
                    pass
    return round(total, 2)


# ── Auth do painel admin ──────────────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if session.get('consveic_admin'):
            return f(*a, **k)
        if request.method == 'POST' and request.form.get('pw') == ADMIN_PW:
            session['consveic_admin'] = True
            return f(*a, **k)
        return render_template_string(_LOGIN)
    return wrap


# ════════════════════════════════════════════════════════════════════════════
#  ROTAS
# ════════════════════════════════════════════════════════════════════════════
@consveic_bp.route('/')
def home():
    return render_template_string(_HOME, configurado=_configurado(), env=ZAPAY_ENV)


@consveic_bp.route('/consultar', methods=['POST'])
def consultar():
    placa = (request.form.get('placa') or request.json.get('placa') if request.is_json
             else request.form.get('placa') or '').upper().strip().replace('-', '')
    if len(placa) < 7:
        return jsonify({'ok': False, 'erro': 'Placa inválida.'}), 400

    if not _configurado():
        return jsonify({'ok': False, 'erro': 'Portal em configuração (aguardando credenciais Zapay).'}), 503

    sc, data = consultar_debitos(placa)
    req_id = data.get('request_id') or data.get('id')
    if sc not in (200, 201, 202) or not req_id:
        return jsonify({'ok': False, 'erro': 'Não consegui consultar agora. Tente em instantes.',
                        'detalhe': data}), 502

    criar_consulta(placa, request_id=req_id)
    return jsonify({'ok': True, 'request_id': req_id})


@consveic_bp.route('/api/consulta/<request_id>')
def api_consulta(request_id):
    """Polling: a tela chama isso até o webhook preencher os débitos."""
    c = obter_consulta(request_id=request_id)
    if not c:
        return jsonify({'status': 'desconhecida'}), 404
    debitos = json.loads(c['debitos_json']) if c.get('debitos_json') else []
    return jsonify({
        'status': c['status'], 'placa': c['placa'], 'total': c['total'],
        'mensagem': c.get('mensagem'), 'debitos': debitos,
        'comissao': calcular_comissao(c['total'] or 0),
    })


@consveic_bp.route('/resultado/<request_id>')
def resultado(request_id):
    c = obter_consulta(request_id=request_id)
    if not c:
        return redirect('/consulta-veicular/')
    return render_template_string(_RESULTADO, request_id=request_id, placa=c['placa'])


@consveic_bp.route('/pagar', methods=['POST'])
def pagar():
    f = request.form
    request_id = f.get('request_id')
    c = obter_consulta(request_id=request_id)
    if not c or c['status'] != 'ok':
        return jsonify({'ok': False, 'erro': 'Consulta não encontrada ou sem débitos.'}), 400

    debitos = json.loads(c['debitos_json']) if c.get('debitos_json') else []
    valor_debitos = c['total'] or _somar_debitos(debitos)
    comissao = calcular_comissao(valor_debitos)
    total = round(valor_debitos + comissao, 2)

    meio = f.get('meio', 'pix')
    parcelas = int(f.get('parcelas', 1) or 1)
    external_id = f'cv-{request_id[:8]}-{int(datetime.utcnow().timestamp())}'

    cliente = {
        'name':     f.get('nome', '').strip(),
        'document': f.get('doc', '').strip(),
        'email':    f.get('email', '').strip(),
        'phone':    f.get('fone', '').strip(),
    }
    itens = [{'type': 'vehicle_debt', 'debts': debitos}]
    if comissao > 0:
        itens.append({'type': 'other', 'description': 'Taxa de serviço', 'amount': comissao})

    sc, data = criar_pagamento(cliente, itens, meio=meio, parcelas=parcelas, external_id=external_id)
    order_id = data.get('order_id') or data.get('id')
    if sc not in (200, 201, 202) or not order_id:
        return jsonify({'ok': False, 'erro': 'Falha ao iniciar pagamento.', 'detalhe': data}), 502

    criar_pedido(order_id=order_id, external_id=external_id, consulta_id=c['id'],
                 placa=c['placa'], meio=meio, parcelas=parcelas,
                 valor_debitos=valor_debitos, comissao=comissao, valor_total=total,
                 cliente_nome=cliente['name'], cliente_doc=cliente['document'],
                 cliente_email=cliente['email'], cliente_fone=cliente['phone'])

    pix = data.get('pix', {}).get('qr_code') or data.get('pix_copia_cola')
    if pix:
        atualizar_pedido(order_id, pix_copia_cola=pix)
    return jsonify({'ok': True, 'order_id': order_id, 'pix': pix, 'total': total})


@consveic_bp.route('/pedido/<order_id>')
def pedido(order_id):
    p = obter_pedido(order_id=order_id)
    if not p:
        return redirect('/consulta-veicular/')
    return render_template_string(_PEDIDO, p=p)


@consveic_bp.route('/api/pedido/<order_id>')
def api_pedido(order_id):
    p = obter_pedido(order_id=order_id)
    if not p:
        return jsonify({'status': 'desconhecido'}), 404
    return jsonify({'status': p['status'], 'pix': p.get('pix_copia_cola'),
                    'total': p['valor_total']})


# ── WEBHOOK (recebe consulta + pagamento da Zapay) ────────────────────────────
@consveic_bp.route('/webhook', methods=['POST'])
def webhook():
    raw = request.get_data()
    assinatura = request.headers.get('x-hmac-signature', '')
    ok_assinatura = _hmac_ok(raw, assinatura)

    try:
        body = json.loads(raw or b'{}')
    except Exception:
        body = {}

    tipo     = body.get('resource') or body.get('type') or body.get('event') or 'desconhecido'
    event_id = body.get('event_id') or body.get('id')

    # validação inicial do endpoint (registro do webhook)
    if tipo == 'webhook_validation':
        registrar_evento(tipo, '-', body, ok_assinatura, event_id)
        return jsonify({'ok': True}), 200

    # idempotência: evento pode vir duplicado / fora de ordem (doc)
    if event_id and evento_ja_processado(event_id):
        return jsonify({'ok': True, 'dup': True}), 200

    ref = body.get('request_id') or body.get('order_id') or '-'
    registrar_evento(tipo, ref, body, ok_assinatura, event_id)

    # fail-closed: rejeita quando não há secret configurada OU assinatura inválida
    if (not ZAPAY_SECRET) or (not ok_assinatura):
        log.warning('[Zapay webhook] bloqueado — secret ausente ou HMAC inválido (ref=%s)', ref)
        return jsonify({'ok': False, 'erro': 'assinatura inválida'}), 401

    # ── evento de DÉBITOS VEICULARES (resultado da consulta) ──
    if 'vehicle' in tipo or 'debt' in tipo or body.get('request_id'):
        rid = body.get('request_id')
        debitos = body.get('debts') or body.get('debitos') or []
        if debitos:
            atualizar_consulta(rid, status='ok', debitos=debitos,
                               total=_somar_debitos(debitos),
                               renavam=body.get('renavam'), uf=body.get('state') or body.get('uf'))
        else:
            atualizar_consulta(rid, status=body.get('status', 'sem_debitos'),
                               mensagem=body.get('message') or body.get('mensagem'),
                               debitos=[])
        return jsonify({'ok': True}), 200

    # ── evento de PAGAMENTO (mudança de status do pedido) ──
    if 'payment' in tipo or body.get('order_id'):
        oid = body.get('order_id')
        novo = body.get('status', 'processing')
        pix = (body.get('pix') or {}).get('qr_code') or body.get('pix_copia_cola')
        atualizar_pedido(oid, status=novo, pix_copia_cola=pix)
        # TODO: aqui dá pra disparar aviso WhatsApp/Rádio SC News quando status='paid'
        return jsonify({'ok': True}), 200

    return jsonify({'ok': True}), 200


# ── Painel admin ──────────────────────────────────────────────────────────────
@consveic_bp.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin():
    return render_template_string(_ADMIN, st=estatisticas(), pedidos=listar_pedidos(50),
                                  env=ZAPAY_ENV, configurado=_configurado(),
                                  webhook_url=ZAPAY_WEBHOOK_URL)


@consveic_bp.route('/admin/registrar-webhook', methods=['POST'])
@admin_required
def admin_registrar_webhook():
    sc, data = registrar_webhook_zapay()
    return jsonify({'status_code': sc, 'resposta': data})


# ════════════════════════════════════════════════════════════════════════════
#  TEMPLATES (inline, estilo dos outros módulos)
# ════════════════════════════════════════════════════════════════════════════
_BASE_CSS = '''
  *{box-sizing:border-box;font-family:-apple-system,Segoe UI,Roboto,sans-serif}
  body{margin:0;background:#0f172a;color:#e2e8f0}
  .wrap{max-width:560px;margin:0 auto;padding:24px}
  .card{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:24px;margin:16px 0}
  h1{font-size:24px;margin:0 0 4px} .sub{color:#94a3b8;font-size:14px;margin-bottom:16px}
  input,select{width:100%;padding:14px;border-radius:10px;border:1px solid #475569;background:#0f172a;color:#fff;font-size:16px;margin:6px 0}
  button{width:100%;padding:15px;border:0;border-radius:10px;background:#22c55e;color:#06240f;font-weight:700;font-size:16px;cursor:pointer}
  button:disabled{opacity:.5}
  .deb{display:flex;justify-content:space-between;padding:12px;border-bottom:1px solid #334155}
  .tot{font-size:20px;font-weight:800;color:#22c55e}
  .badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;background:#334155}
  a{color:#38bdf8}
'''

_HOME = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Consulta Veicular</title>
<style>''' + _BASE_CSS + '''</style></head><body><div class=wrap>
<div class=card>
  <h1>🚗 Consulta Veicular</h1>
  <div class=sub>Débitos do seu veículo (IPVA, multas, licenciamento) — pague ou parcele em até 12x.</div>
  {% if not configurado %}
    <div class=badge style="background:#7c2d12;color:#fed7aa">⚙️ Portal em configuração — credenciais Zapay pendentes</div>
  {% endif %}
  <input id=placa placeholder="Placa (ABC1D23)" maxlength=8 style="text-transform:uppercase">
  <button id=btn onclick="consultar()">Consultar débitos</button>
  <div id=msg class=sub style="margin-top:10px"></div>
</div>
<div class=sub style="text-align:center">Ambiente: {{ env }} · Powered by Zapay</div>
</div>
<script>
async function consultar(){
  const placa=document.getElementById('placa').value.trim();
  const btn=document.getElementById('btn'), msg=document.getElementById('msg');
  if(placa.length<7){msg.textContent='Digite uma placa válida.';return}
  btn.disabled=true; msg.textContent='🔎 Consultando órgãos...';
  try{
    const r=await fetch('/consulta-veicular/consultar',{method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:'placa='+encodeURIComponent(placa)});
    const j=await r.json();
    if(j.ok){location.href='/consulta-veicular/resultado/'+j.request_id}
    else{msg.textContent=j.erro||'Erro';btn.disabled=false}
  }catch(e){msg.textContent='Falha de conexão.';btn.disabled=false}
}
</script></body></html>'''

_RESULTADO = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Débitos — {{ placa }}</title>
<style>''' + _BASE_CSS + '''</style></head><body><div class=wrap>
<div class=card>
  <h1>Débitos — {{ placa }}</h1>
  <div id=status class=sub>⏳ Buscando débitos nos órgãos...</div>
  <div id=lista></div>
  <form id=pgto style=display:none onsubmit="return pagar(event)">
    <input type=hidden name=request_id value="{{ request_id }}">
    <input name=nome placeholder="Seu nome" required>
    <input name=doc placeholder="CPF/CNPJ" required>
    <input name=email type=email placeholder="E-mail" required>
    <input name=fone placeholder="WhatsApp" required>
    <select name=meio id=meio onchange="toggleParc()">
      <option value=pix>PIX (à vista)</option>
      <option value=credit_card>Cartão de crédito</option>
    </select>
    <select name=parcelas id=parcelas style=display:none>
      {% for n in range(1,13) %}<option value="{{ n }}">{{ n }}x</option>{% endfor %}
    </select>
    <button>Pagar</button>
  </form>
</div></div>
<script>
const RID="{{ request_id }}";
function toggleParc(){document.getElementById('parcelas').style.display=
  document.getElementById('meio').value==='credit_card'?'block':'none'}
async function poll(){
  const r=await fetch('/consulta-veicular/api/consulta/'+RID);
  if(r.status===404){return setTimeout(poll,2500)}
  const j=await r.json();
  const st=document.getElementById('status'),lista=document.getElementById('lista');
  if(j.status==='pendente'){return setTimeout(poll,2500)}
  if(j.status==='ok'){
    let h='';
    (j.debitos||[]).forEach(d=>{h+=`<div class=deb><span>${d.description||d.descricao||d.type||'Débito'}</span>
      <b>R$ ${(d.amount||d.valor||0).toLocaleString('pt-BR',{minimumFractionDigits:2})}</b></div>`});
    h+=`<div class=deb><span>Taxa de serviço</span><b>R$ ${j.comissao.toLocaleString('pt-BR',{minimumFractionDigits:2})}</b></div>`;
    h+=`<div class=deb><span class=tot>Total</span><span class=tot>R$ ${(j.total+j.comissao).toLocaleString('pt-BR',{minimumFractionDigits:2})}</span></div>`;
    lista.innerHTML=h; st.textContent='✅ Débitos encontrados:';
    document.getElementById('pgto').style.display='block';
  }else{st.textContent='✅ Nenhum débito encontrado'+(j.mensagem?(' — '+j.mensagem):'')+'.';}
}
async function pagar(e){e.preventDefault();
  const fd=new FormData(e.target);
  const r=await fetch('/consulta-veicular/pagar',{method:'POST',body:new URLSearchParams(fd)});
  const j=await r.json();
  if(j.ok){location.href='/consulta-veicular/pedido/'+j.order_id}
  else{alert(j.erro||'Erro ao pagar')}
  return false}
poll();
</script></body></html>'''

_PEDIDO = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Pagamento</title>
<style>''' + _BASE_CSS + '''</style></head><body><div class=wrap><div class=card>
  <h1>Pagamento</h1>
  <div class=deb><span>Placa</span><b>{{ p.placa }}</b></div>
  <div class=deb><span>Total</span><span class=tot>R$ {{ '%.2f'|format(p.valor_total) }}</span></div>
  <div id=status class=sub>Status: {{ p.status }}</div>
  {% if p.pix_copia_cola %}
  <div class=sub>PIX copia-e-cola:</div>
  <textarea readonly style="width:100%;height:80px">{{ p.pix_copia_cola }}</textarea>
  {% endif %}
</div></div>
<script>
const OID="{{ p.order_id }}";
async function poll(){
  const r=await fetch('/consulta-veicular/api/pedido/'+OID); const j=await r.json();
  document.getElementById('status').textContent='Status: '+j.status;
  if(j.status==='paid'){document.getElementById('status').innerHTML='✅ <b>Pagamento confirmado!</b>';return}
  if(['canceled','failed'].includes(j.status)){return}
  setTimeout(poll,4000)}
poll();
</script></body></html>'''

_LOGIN = '''<!doctype html><meta charset=utf-8><style>''' + _BASE_CSS + '''</style>
<div class=wrap><div class=card><h1>Admin</h1>
<form method=post><input name=pw type=password placeholder=senha><button>Entrar</button></form>
</div></div>'''

_ADMIN = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Admin — Consulta Veicular</title>
<style>''' + _BASE_CSS + '''.wrap{max-width:900px}table{width:100%;border-collapse:collapse}
td,th{padding:8px;border-bottom:1px solid #334155;text-align:left;font-size:13px}</style></head>
<body><div class=wrap>
<div class=card>
  <h1>📊 Consulta Veicular — Admin</h1>
  <div class=sub>Ambiente: <b>{{ env }}</b> · Credenciais: {{ '✅ ok' if configurado else '⚠️ ausentes' }}</div>
  <div class=deb><span>Consultas</span><b>{{ st.consultas }}</b></div>
  <div class=deb><span>Pagos</span><b>{{ st.pagos }}</b></div>
  <div class=deb><span class=tot>Comissão acumulada</span><span class=tot>R$ {{ '%.2f'|format(st.comissao_total) }}</span></div>
  <p class=sub>Webhook: {{ webhook_url or '— defina ZAPAY_WEBHOOK_URL —' }}</p>
  <button onclick="reg()">Registrar webhook na Zapay</button>
  <div id=regr class=sub></div>
</div>
<div class=card>
  <h1>Últimos pedidos</h1>
  <table><tr><th>Placa</th><th>Meio</th><th>Total</th><th>Comissão</th><th>Status</th></tr>
  {% for p in pedidos %}<tr><td>{{ p.placa }}</td><td>{{ p.meio }}</td>
    <td>R$ {{ '%.2f'|format(p.valor_total) }}</td><td>R$ {{ '%.2f'|format(p.comissao) }}</td>
    <td><span class=badge>{{ p.status }}</span></td></tr>{% endfor %}
  </table>
</div></div>
<script>
async function reg(){const r=await fetch('/consulta-veicular/admin/registrar-webhook',{method:'POST'});
  document.getElementById('regr').textContent=JSON.stringify(await r.json())}
</script></body></html>'''
