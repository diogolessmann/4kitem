"""
petmed.py — Blueprint PETmed
Triagem veterinária inteligente 24/7
"""
import json
import logging
import os
import random
import re
import requests as _requests
from datetime import datetime, timedelta
from functools import wraps
from flask import (Blueprint, render_template, redirect, request,
                   session, jsonify, url_for, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from petmed_db import get_petmed_db, init_petmed_db

log = logging.getLogger('petmed')

# ── DEV_WHITELIST — nunca bloqueados pelo anti-golpe (re-cadastro livre) ───────
_pm_wl_raw = os.environ.get('DEV_WHITELIST', '47997766831,diogolessmann@gmail.com')
_PM_WHITELIST: set = {x.strip().lower() for x in _pm_wl_raw.split(',') if x.strip()}

def _pm_is_whitelisted(*values) -> bool:
    for v in values:
        if v and str(v).strip().lower() in _PM_WHITELIST:
            return True
    return False

try:
    from groq import Groq as _Groq
    _groq_client = _Groq(api_key=os.environ.get('GROQ_API_KEY', ''))
except Exception:
    _groq_client = None

# ── IA: Gemini (multimodal) + Groq (rápido) — os dois ligados ───────────────────
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
# VETZAP_AI: 'gemini' | 'groq' | 'auto' (default: gemini primário, groq de reserva)
AI_PROVIDER  = os.environ.get('VETZAP_AI', 'auto').strip().lower()
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'

def _gemini_on():
    return bool(GEMINI_KEY)

def _use_gemini():
    """Decide se o Gemini é o motor primário."""
    if AI_PROVIDER == 'groq':
        return False
    if AI_PROVIDER == 'gemini':
        return True
    return _gemini_on()  # auto: usa Gemini se tiver chave

def _gemini_call(system, contents, json_mode=True, max_tokens=2048, temperature=0.3):
    """Chama o Gemini via REST. `contents` no formato Gemini. Retorna o texto (str)."""
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

petmed_bp = Blueprint('petmed', __name__, url_prefix='/vetzap')

# ── MODELO DE CRÉDITOS (pago por atendimento) ──────────────────────────────────
# 1 crédito = 1 atendimento completo (triagem com IA do início ao resultado).
PRECO_UNIT = 29.0
PACOTES_CREDITO = {
    'p1':  {'creditos': 1,  'preco': 29.0,  'preco_fmt': 'R$ 29',  'rotulo': '1 atendimento',
            'cada': 'R$ 29,00/atend.', 'emoji': '🩺'},
    'p5':  {'creditos': 5,  'preco': 119.0, 'preco_fmt': 'R$ 119', 'rotulo': '5 atendimentos',
            'cada': 'R$ 23,80/atend.', 'emoji': '🐾', 'destaque': True, 'economia': 'Economize R$ 26'},
    'p10': {'creditos': 10, 'preco': 199.0, 'preco_fmt': 'R$ 199', 'rotulo': '10 atendimentos',
            'cada': 'R$ 19,90/atend.', 'emoji': '👑', 'economia': 'Economize R$ 91'},
}

# ── Consulta Avulsa (pagamento único, acesso por 24h) ──────────────────────────
CONSULTA_AVULSA = {
    'preco': 32.90,
    'preco_fmt': 'R$ 32,90',
    'horas': 24,
    'nome': 'Consulta 24h',
    'emoji': '🩺',
    'descricao': 'Acesso imediato por 24 horas — sem compromisso',
    'features': [
        'Triagem completa 24/7',
        '1 pet por atendimento',
        'Classificação de urgência',
        'Orientações pós-triagem',
        'Identificação de raça por foto',
        'Acesso por 24 horas após o pagamento',
    ],
}

# ── Planos mensais ─────────────────────────────────────────────────────────────
PLANOS = {
    'start': {
        'nome': 'PET Start',
        'preco': 49.90,
        'preco_fmt': 'R$ 49,90',
        'cor': '#0ea5e9',
        'emoji': '🐾',
        'descricao': 'Para quem tem 1 pet',
        'pets': 1,
        'teleconsulta': False,
        'lembretes': False,
        'mapa': False,
        'features': [
            'Triagens ilimitadas 24/7',
            '1 pet cadastrado',
            'Identificação de raça por foto',
            'Classificação de urgência',
            'Orientações pós-triagem',
            'Histórico completo',
        ],
    },
    'familia': {
        'nome': 'PET Família',
        'preco': 79.90,
        'preco_fmt': 'R$ 79,90',
        'cor': '#f97316',
        'emoji': '🐾🐾',
        'descricao': 'Para famílias com mais pets',
        'pets': 4,
        'teleconsulta': False,
        'lembretes': True,
        'mapa': True,
        'destaque': True,
        'features': [
            'Tudo do Start',
            'Até 4 pets cadastrados',
            'Histórico completo',
            'Cartão de vacinas digital',
            'Lembretes automáticos',
            'Mapa de clínicas abertas',
            'Prioridade no atendimento',
        ],
    },
    'premium': {
        'nome': 'PET Premium',
        'preco': 119.90,
        'preco_fmt': 'R$ 119,90',
        'cor': '#8b5cf6',
        'emoji': '👑',
        'descricao': 'Proteção total',
        'pets': 999,
        'teleconsulta': True,
        'lembretes': True,
        'mapa': True,
        'features': [
            'Tudo do Família',
            'Pets ilimitados',
            '1 teleconsulta/mês incluída',
            'Consultas adicionais com desconto',
            'Relatório mensal de saúde',
            'Suporte prioritário',
            'Desconto em clínicas parceiras',
        ],
    },
}

LIMITE_PETS = {'start': 1, 'familia': 4, 'premium': 999}

# ── Categorias de sintomas ──────────────────────────────────────────────────────
CATEGORIAS = {
    'digestivo':     {'emoji': '🤢', 'label': 'Vômito / Diarreia / Sem apetite'},
    'respiratorio':  {'emoji': '😮‍💨', 'label': 'Tosse / Falta de ar / Espirros'},
    'neurologico':   {'emoji': '⚡', 'label': 'Convulsão / Tremores / Desorientação'},
    'trauma':        {'emoji': '🩹', 'label': 'Ferimento / Queda / Acidente'},
    'urinario':      {'emoji': '🚿', 'label': 'Dificuldade para urinar / Sangue'},
    'comportamento': {'emoji': '😴', 'label': 'Letargia / Apatia / Tristeza'},
    'pele':          {'emoji': '🐛', 'label': 'Coceira / Feridas / Pelos caindo / Alopecia'},
    'ocular':        {'emoji': '👁️', 'label': 'Olho vermelho / Secreção / Orelha'},
    'outro':         {'emoji': '❓', 'label': 'Outro sintoma'},
}


# ── Decoradores ────────────────────────────────────────────────────────────────
def petmed_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('pm_user_id'):
            return redirect('/vetzap/entrar')
        return f(*args, **kwargs)
    return decorated


def petmed_premium_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('pm_user_id'):
            return redirect('/vetzap/entrar')
        if session.get('pm_plano') != 'premium':
            return redirect('/vetzap/planos?msg=premium')
        return f(*args, **kwargs)
    return decorated


# ── Helpers ────────────────────────────────────────────────────────────────────
def _get_user():
    uid = session.get('pm_user_id')
    if not uid:
        return None
    conn = get_petmed_db()
    u = conn.execute('SELECT * FROM petmed_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return u


def _get_pets(user_id):
    conn = get_petmed_db()
    pets = conn.execute(
        'SELECT * FROM petmed_pets WHERE user_id=? ORDER BY nome', (user_id,)
    ).fetchall()
    conn.close()
    return pets


def _can_add_pet(user_id, plano=None):
    """Modelo de crédito: pets ilimitados (sem trava de plano)."""
    conn = get_petmed_db()
    total = conn.execute(
        'SELECT COUNT(*) FROM petmed_pets WHERE user_id=?', (user_id,)
    ).fetchone()[0]
    conn.close()
    return True, total, 999


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── Asaas — Gateway de Pagamento ──────────────────────────────────────────────

_ASAAS_BASE = 'https://api.asaas.com/v3'

def _asaas_headers():
    return {
        'access_token': os.environ.get('ASAAS_API_KEY', ''),
        'Content-Type': 'application/json',
    }

def _asaas_req(method: str, endpoint: str, data: dict = None):
    """Faz requisição autenticada à API do Asaas."""
    try:
        resp = _requests.request(
            method,
            f'{_ASAAS_BASE}{endpoint}',
            headers=_asaas_headers(),
            json=data,
            timeout=15
        )
        return resp.json()
    except Exception as e:
        return {'error': str(e)}

def _asaas_criar_ou_buscar_cliente(u) -> str:
    """Cria ou busca cliente no Asaas. Retorna o customer_id."""
    # Verifica se já tem ID salvo
    if u['asaas_customer_id']:
        return u['asaas_customer_id']

    cpf = u['cpf'] or ''
    # Busca por CPF primeiro
    busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf}')
    if busca.get('data'):
        cid = busca['data'][0]['id']
    else:
        # Cria novo cliente
        resp = _asaas_req('POST', '/customers', {
            'name': u['nome'],
            'email': u['email'],
            'mobilePhone': re.sub(r'\D', '', u['telefone'] or ''),
            'cpfCnpj': cpf,
        })
        cid = resp.get('id')

    if cid:
        conn = get_petmed_db()
        conn.execute('UPDATE petmed_users SET asaas_customer_id=? WHERE id=?',
                     (cid, u['id']))
        conn.commit()
        conn.close()
    return cid

def _asaas_criar_pagamento_avulso(customer_id: str, user_id: int, billing_type: str) -> dict:
    """Cria cobrança única (não recorrente) para Consulta 24h no Asaas."""
    import datetime as _dt
    venc = (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d')
    return _asaas_req('POST', '/payments', {
        'customer': customer_id,
        'billingType': billing_type,
        'value': CONSULTA_AVULSA['preco'],
        'dueDate': venc,
        'description': 'VetZap — Consulta 24h (acesso imediato)',
        'externalReference': f'vetzap_consulta_avulsa_{user_id}',
    })

def _asaas_criar_pagamento_creditos(customer_id: str, user_id: int, pacote: str, billing_type: str) -> dict:
    """Cria cobrança única (PIX/boleto/cartão) para compra de um pacote de créditos."""
    import datetime as _dt
    p = PACOTES_CREDITO[pacote]
    venc = (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d')
    return _asaas_req('POST', '/payments', {
        'customer': customer_id,
        'billingType': billing_type,
        'value': p['preco'],
        'dueDate': venc,
        'description': f'VetZap — {p["rotulo"]} (créditos)',
        'externalReference': f'vetzap_cred_{user_id}_{pacote}',
    })


def _asaas_criar_assinatura(customer_id: str, plano: str, billing_type: str) -> dict:
    """Cria assinatura recorrente mensal no Asaas."""
    p = PLANOS.get(plano, PLANOS['start'])
    import datetime as _dt
    prox_venc = (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d')
    return _asaas_req('POST', '/subscriptions', {
        'customer': customer_id,
        'billingType': billing_type,      # PIX, BOLETO, CREDIT_CARD
        'value': p['preco'],
        'nextDueDate': prox_venc,
        'cycle': 'MONTHLY',
        'description': f'VetZap — {p["nome"]}',
        'externalReference': f'vetzap_{customer_id}_{plano}',
    })

# ── E-mail transacional (Resend) ───────────────────────────────────────────────

def _enviar_email(para: str, assunto: str, html: str) -> bool:
    """Envia e-mail via Resend API. Retorna True se enviado com sucesso."""
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        return False
    from_addr = os.environ.get('EMAIL_FROM', 'VetZap <onboarding@resend.dev>')
    try:
        resp = _requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={'from': from_addr, 'to': [para], 'subject': assunto, 'html': html},
            timeout=10
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def _email_consulta_avulsa_ativada(primeiro_nome: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 0">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#111;border:1px solid #222;border-radius:16px;overflow:hidden">
<tr><td style="background:#10b981;height:4px"></td></tr>
<tr><td style="padding:36px 40px 32px">
  <div style="font-size:40px;margin-bottom:12px">🩺</div>
  <h1 style="color:#fff;font-size:22px;font-weight:800;margin:0 0 8px">Consulta ativada, {primeiro_nome}!</h1>
  <p style="color:#888;font-size:14px;line-height:1.7;margin:0 0 24px">
    Sua Consulta 24h está ativa agora. Acesse o VetZap e inicie o atendimento do seu pet.
  </p>
  <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:20px;margin-bottom:24px">
    <div style="font-size:12px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">Consulta ativa</div>
    <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
      <span style="font-size:13px;color:#666">Tipo</span>
      <span style="font-size:13px;color:#fff;font-weight:700">🩺 Consulta 24h</span>
    </div>
    <div style="display:flex;justify-content:space-between;padding:8px 0">
      <span style="font-size:13px;color:#666">Validade</span>
      <span style="font-size:13px;color:#10b981;font-weight:700">24 horas a partir de agora</span>
    </div>
  </div>
  <a href="https://4kitem.com.br/vetzap/dashboard" style="display:block;text-align:center;padding:14px 28px;background:#10b981;color:#fff;font-size:15px;font-weight:700;border-radius:12px;text-decoration:none;margin-bottom:20px">
    🐾 Iniciar atendimento agora
  </a>
  <hr style="border:none;border-top:1px solid #222;margin:28px 0">
  <p style="font-size:11px;color:#555;margin:0;line-height:1.6">
    4KITEM · VetZap · <a href="https://4kitem.com.br" style="color:#10b981">4kitem.com.br</a><br>
    Dúvidas? WhatsApp: <a href="https://wa.me/5547999606998" style="color:#10b981">(47) 99960-6998</a>
  </p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def _email_creditos_liberados(primeiro_nome: str, qtd: int) -> str:
    plural = 'atendimentos' if qtd != 1 else 'atendimento'
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 0">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#111;border:1px solid #222;border-radius:16px;overflow:hidden">
<tr><td style="background:#10b981;height:4px"></td></tr>
<tr><td style="padding:36px 40px 32px">
  <div style="font-size:40px;margin-bottom:12px">🐾</div>
  <h1 style="color:#fff;font-size:22px;font-weight:800;margin:0 0 8px">Créditos liberados, {primeiro_nome}!</h1>
  <p style="color:#888;font-size:14px;line-height:1.7;margin:0 0 24px">
    Pagamento confirmado. Você tem <strong style="color:#10b981">{qtd} {plural}</strong> prontos pra usar no VetZap.
  </p>
  <a href="https://4kitem.com.br/vetzap/triagem" style="display:block;text-align:center;padding:14px 28px;background:#10b981;color:#fff;font-size:15px;font-weight:700;border-radius:12px;text-decoration:none;margin-bottom:20px">
    🩺 Iniciar atendimento agora
  </a>
  <hr style="border:none;border-top:1px solid #222;margin:28px 0">
  <p style="font-size:11px;color:#555;margin:0;line-height:1.6">
    4KITEM · VetZap · <a href="https://4kitem.com.br" style="color:#10b981">4kitem.com.br</a><br>
    Dúvidas? WhatsApp: <a href="https://wa.me/5547999606998" style="color:#10b981">(47) 99960-6998</a>
  </p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def _email_pagamento_confirmado_petmed(primeiro_nome: str, plano_nome: str, preco_fmt: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 0">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#111;border:1px solid #222;border-radius:16px;overflow:hidden">
<tr><td style="background:#0ea5e9;height:4px"></td></tr>
<tr><td style="padding:36px 40px 32px">
  <div style="font-size:40px;margin-bottom:12px">✅</div>
  <h1 style="color:#fff;font-size:22px;font-weight:800;margin:0 0 8px">Pagamento confirmado!</h1>
  <p style="color:#888;font-size:14px;line-height:1.7;margin:0 0 24px">
    Sua assinatura do <strong style="color:#fff">VetZap</strong> está ativa, {primeiro_nome}. Seu pet está protegido 24h!
  </p>
  <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:20px;margin-bottom:24px">
    <div style="font-size:12px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">Assinatura ativa</div>
    <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
      <span style="font-size:13px;color:#666">Plano</span>
      <span style="font-size:13px;color:#fff;font-weight:700">🐾 {plano_nome}</span>
    </div>
    <div style="display:flex;justify-content:space-between;padding:8px 0">
      <span style="font-size:13px;color:#666">Valor mensal</span>
      <span style="font-size:13px;color:#0ea5e9;font-weight:700">{preco_fmt}/mês</span>
    </div>
  </div>
  <a href="https://4kitem.com.br/vetzap/dashboard" style="display:block;text-align:center;padding:14px 28px;background:#0ea5e9;color:#fff;font-size:15px;font-weight:700;border-radius:12px;text-decoration:none;margin-bottom:20px">
    🐾 Acessar o VetZap
  </a>
  <hr style="border:none;border-top:1px solid #222;margin:28px 0">
  <p style="font-size:11px;color:#555;margin:0;line-height:1.6">
    4KITEM · VetZap · <a href="https://4kitem.com.br" style="color:#0ea5e9">4kitem.com.br</a><br>
    Dúvidas? WhatsApp: <a href="https://wa.me/5547999606998" style="color:#0ea5e9">(47) 99960-6998</a>
  </p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def _email_recuperacao(codigo: str) -> str:
    return f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:0 auto;background:#f0f9ff;padding:32px 20px">
      <div style="text-align:center;margin-bottom:24px">
        <span style="font-size:36px">🐾</span>
        <h1 style="font-size:22px;font-weight:800;color:#0c4a6e;margin:8px 0 4px">VetZap</h1>
        <p style="font-size:13px;color:#7ea8bf;margin:0">Triagem Veterinária 24h</p>
      </div>
      <div style="background:#fff;border-radius:16px;padding:28px 24px;border:1px solid #e0f2fe">
        <h2 style="font-size:18px;font-weight:700;color:#0c4a6e;margin-top:0">🔑 Recuperação de senha</h2>
        <p style="font-size:14px;color:#075985;line-height:1.6">
          Recebemos uma solicitação para redefinir a senha da sua conta VetZap.
          Use o código abaixo para continuar:
        </p>
        <div style="background:#f0fdf4;border:2px solid #22c55e;border-radius:12px;padding:20px;text-align:center;margin:20px 0">
          <div style="font-size:11px;color:#166534;font-weight:600;margin-bottom:6px">SEU CÓDIGO</div>
          <div style="font-size:44px;font-weight:900;letter-spacing:10px;color:#15803d">{codigo}</div>
          <div style="font-size:12px;color:#166534;margin-top:8px">⏱️ Válido por 30 minutos</div>
        </div>
        <p style="font-size:13px;color:#7ea8bf;margin-bottom:0">
          Se você não solicitou a recuperação de senha, ignore este e-mail.
          Sua senha permanece a mesma.
        </p>
      </div>
      <p style="text-align:center;font-size:11px;color:#7ea8bf;margin-top:20px">
        VetZap — Proteção 24h para o seu pet 🐾
      </p>
    </div>
    """


def _email_boas_vindas(nome: str, pet_nome: str) -> str:
    primeiro = (nome.split()[0] if nome else 'tutor')
    tem_pet = bool(pet_nome)
    linha_pet = (f"Sua conta foi criada e <strong>{pet_nome}</strong> já está cadastrado(a) no VetZap."
                 if tem_pet else
                 "Sua conta foi criada! Cadastre seu pet e faça a primeira triagem em 2 minutos.")
    return f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:0 auto;background:#f0f9ff;padding:32px 20px">
      <div style="text-align:center;margin-bottom:24px">
        <span style="font-size:36px">🐾</span>
        <h1 style="font-size:22px;font-weight:800;color:#0c4a6e;margin:8px 0 4px">VetZap</h1>
        <p style="font-size:13px;color:#7ea8bf;margin:0">Triagem Veterinária 24h</p>
      </div>
      <div style="background:#fff;border-radius:16px;padding:28px 24px;border:1px solid #e0f2fe">
        <h2 style="font-size:18px;font-weight:700;color:#0c4a6e;margin-top:0">
          Bem-vindo, {primeiro}! 🎉
        </h2>
        <p style="font-size:14px;color:#075985;line-height:1.6">
          {linha_pet}
        </p>
        <div style="background:#f0f9ff;border-radius:10px;padding:16px;margin:20px 0">
          <div style="font-size:13px;font-weight:700;color:#0c4a6e;margin-bottom:8px">O que você pode fazer:</div>
          <div style="font-size:13px;color:#075985;line-height:2">
            🩺 Triagem com IA em minutos<br>
            💉 Registrar e acompanhar vacinas<br>
            📋 Histórico de saúde do pet<br>
            🚨 Orientação em emergências 24h
          </div>
        </div>
        <a href="https://4kitem.com.br/vetzap/triagem"
           style="display:block;text-align:center;background:#0ea5e9;color:#fff;padding:14px;border-radius:10px;font-weight:700;font-size:14px;text-decoration:none">
          🩺 Fazer minha primeira consulta
        </a>
        <p style="font-size:12px;color:#7ea8bf;margin-top:16px;margin-bottom:0;line-height:1.5">
          ⚠️ O VetZap oferece triagem e orientação geral — não substitui avaliação veterinária presencial.
          Em emergências, procure uma clínica imediatamente.
        </p>
      </div>
      <p style="text-align:center;font-size:11px;color:#7ea8bf;margin-top:20px">
        VetZap — Proteção 24h para o seu pet 🐾
      </p>
    </div>
    """


def _triagens_usadas(user_id):
    """Retorna quantas triagens o usuário já realizou."""
    conn = get_petmed_db()
    n = conn.execute(
        'SELECT COUNT(*) FROM petmed_triagens WHERE user_id=?', (user_id,)
    ).fetchone()[0]
    conn.close()
    return n


def _get_creditos(user_id):
    """Saldo de créditos do usuário."""
    conn = get_petmed_db()
    row = conn.execute('SELECT creditos FROM petmed_users WHERE id=?', (user_id,)).fetchone()
    conn.close()
    return (row['creditos'] or 0) if row else 0


def _add_creditos(user_id, qtd):
    """Soma créditos (usado pelo webhook e admin)."""
    conn = get_petmed_db()
    conn.execute('UPDATE petmed_users SET creditos = COALESCE(creditos,0) + ? WHERE id=?',
                 (int(qtd), user_id))
    conn.commit()
    conn.close()


def _debita_credito(user_id):
    """Debita 1 crédito de forma atômica. Retorna True se debitou, False se sem saldo."""
    conn = get_petmed_db()
    cur = conn.execute(
        'UPDATE petmed_users SET creditos = creditos - 1 WHERE id=? AND creditos > 0',
        (user_id,)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def _check_paywall(u):
    """
    Modelo de CRÉDITOS: pode atender se tiver saldo > 0.
    Retorna (bloqueado: bool, creditos: int).
    """
    cred = (u['creditos'] if 'creditos' in u.keys() and u['creditos'] is not None else 0)
    return (cred < 1), cred


@petmed_bp.app_context_processor
def _pm_inject_creditos():
    """Expõe pm_creditos nos templates do VetZap (sem pesar o resto do site)."""
    try:
        if request.endpoint and request.endpoint.startswith('petmed.') and session.get('pm_user_id'):
            return {'pm_creditos': _get_creditos(session['pm_user_id'])}
    except Exception:
        pass
    return {}


# ── IA: identificar raça por foto (Gemini primário + Groq reserva) ─────────────
def _identificar_raca(foto_base64: str, especie: str) -> str:
    if not foto_base64 or (not _gemini_on() and not _groq_client):
        return 'Não identificada'
    tipo = 'cão' if especie == 'cao' else 'gato'
    prompt = (
        f'Identifique a raça deste {tipo} na foto. '
        'Responda SOMENTE com o nome da raça, sem explicações. '
        'Exemplos: "Golden Retriever", "Labrador", "SRD (Sem Raça Definida)", '
        '"Poodle", "Bulldog Francês", "Persa", "Siamês". '
        'Se não conseguir identificar, responda "SRD".'
    )

    # 1) Gemini (multimodal nativo)
    if _use_gemini() and _gemini_on():
        try:
            contents = [{'role': 'user', 'parts': [
                {'inline_data': {'mime_type': 'image/jpeg', 'data': foto_base64}},
                {'text': prompt},
            ]}]
            return _gemini_call(None, contents, json_mode=False, max_tokens=50, temperature=0.1)
        except Exception as e:
            log.warning('[PETmed] Gemini raça falhou, tentando Groq: %s', e)

    # 2) Groq (reserva)
    if _groq_client:
        try:
            resp = _groq_client.chat.completions.create(
                model='meta-llama/llama-4-scout-17b-16e-instruct',
                messages=[{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{foto_base64}'}},
                    {'type': 'text', 'text': prompt},
                ]}],
                max_tokens=50, temperature=0.1,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            pass
    return 'Não identificada'


# ── IA: triagem inteligente ─────────────────────────────────────────────────────
def _fazer_triagem(pet_info: dict, categoria: str, historico: list) -> dict:
    """
    Processa a conversa de triagem e retorna próxima pergunta ou resultado final.
    Retorna: {
        'tipo': 'pergunta' | 'resultado',
        'mensagem': str,
        'resultado': 'estavel' | 'atencao' | 'urgente',
        'orientacoes': str,
        'encaminhar': bool
    }
    """
    if not _gemini_on() and not _groq_client:
        return {
            'tipo': 'resultado',
            'resultado': 'atencao',
            'mensagem': 'Serviço temporariamente indisponível.',
            'orientacoes': 'Recomendamos contato com uma clínica veterinária.',
            'encaminhar': True
        }

    categoria_info = CATEGORIAS.get(categoria, {'label': categoria})
    especie = 'cão' if pet_info.get('especie') == 'cao' else 'gato'
    raca = pet_info.get('raca', 'SRD')
    nome = pet_info.get('nome', 'Pet')
    idade = pet_info.get('idade_anos', '?')
    peso = pet_info.get('peso_kg', '?')

    # Busca histórico recente do pet para contexto da IA
    historico_pet_ctx = ''
    pet_id = pet_info.get('id')
    if pet_id:
        try:
            conn_h = get_petmed_db()
            triagens_ant = conn_h.execute(
                '''SELECT resultado, categoria, orientacoes, created_at
                   FROM petmed_triagens WHERE pet_id=?
                   ORDER BY created_at DESC LIMIT 3''',
                (pet_id,)
            ).fetchall()
            conn_h.close()
            if triagens_ant:
                historico_pet_ctx = '\nHISTÓRICO RECENTE DO PET (últimas triagens):\n'
                for t in triagens_ant:
                    historico_pet_ctx += f"• {t['created_at'][:10]} — {t['categoria']} → {t['resultado'].upper()}\n"
        except Exception:
            pass

    # Define porte do pet para calibrar orientações
    try:
        peso_num = float(peso) if peso and peso != '?' else None
    except (ValueError, TypeError):
        peso_num = None

    if especie == 'gato':
        porte = 'gato'
    elif peso_num is None:
        porte = 'porte desconhecido'
    elif peso_num <= 5:
        porte = 'porte pequeno'
    elif peso_num <= 15:
        porte = 'porte médio'
    else:
        porte = 'porte grande'

    peso_info = f"{peso}kg ({porte})" if peso and peso != '?' else f"peso não informado ({porte})"

    # Dados adicionais do pet para o sistema
    sexo     = pet_info.get('sexo', 'nao_informado')
    castrado = pet_info.get('castrado', 0)
    castrado_txt = 'castrado(a)' if castrado else 'não castrado(a)'
    sexo_txt = {'macho': 'macho', 'femea': 'fêmea'}.get(sexo, 'sexo não informado')
    obs_pet  = pet_info.get('observacoes', '') or ''

    # Alerta raça específico
    RACAS_RISCO = {
        # Respiratório braquicefálico
        'bulldog': '⚠️ RAÇA BRAQUICEFÁLICA: muito predisposta a síndrome obstrutiva das vias aéreas — qualquer dispneia é potencialmente grave.',
        'bulldog inglês': '⚠️ RAÇA BRAQUICEFÁLICA: síndrome braquicefálica — dispneia pode ser mais severa do que parece.',
        'bulldog francês': '⚠️ RAÇA BRAQUICEFÁLICA: síndrome braquicefálica — dispneia, ronco e esforço respiratório exigem atenção redobrada.',
        'pug': '⚠️ RAÇA BRAQUICEFÁLICA: síndrome braquicefálica + prolapso de córnea — olhos proeminentes exigem cuidado especial.',
        'shih tzu': '⚠️ RAÇA BRAQUICEFÁLICA + olhos proeminentes. Problemas oculares e respiratórios são comuns.',
        'boxer': '⚠️ Boxer: alta incidência de tumores (mastocitoma, linfoma) — qualquer nódulo novo merece avaliação prioritária.',
        # Coluna
        'dachshund': '⚠️ DACHS: raça de alto risco para DPIV (doença do disco intervertebral) — qualquer dor nas costas, paralisia ou paresia de membros é urgência.',
        'basset hound': '⚠️ Basset: predisposto a DPIV e otite crônica.',
        'corgi': '⚠️ Corgi: predisposto a DPIV por conformação corporal.',
        # Displasia / ortopédico
        'labrador': '⚠️ Labrador: alta incidência de displasia de quadril e cotovelo + obesidade. Claudicação exige avaliação.',
        'golden retriever': '⚠️ Golden: displasia de quadril, lipomas e tumores são comuns. Claudicação e nódulos merecem atenção.',
        'pastor alemão': '⚠️ Pastor Alemão: displasia de quadril + degeneração mielopatia progressiva — claudicação e fraqueza dos membros posteriores são sinais sérios.',
        # Cardíaco
        'cavalier king charles': '⚠️ Cavalier: altíssima predisposição a doença cardíaca (DMVM) — qualquer tosse crônica ou intolerância ao exercício pode ser cardíaca.',
        'yorkshire': '⚠️ Yorkshire: predisposto a colapso de traqueia — tosse em ganso é clássica.',
        'maltês': '⚠️ Maltês: predisposto a shunt portossistêmico e colapso de traqueia.',
        # Renal / urológico
        'persa': '⚠️ Persa: altíssimo risco de doença renal policística (PKD) e problemas urinários.',
        'maine coon': '⚠️ Maine Coon: predisposto a cardiomiopatia hipertrófica (HCM) — monitorar sinais cardíacos.',
        'ragdoll': '⚠️ Ragdoll: predisposto a HCM.',
        # Ocular
        'cocker spaniel': '⚠️ Cocker: muito predisposto a otite crônica + glaucoma — olho vermelho pode ser glaucoma (urgência).',
        'husky siberiano': '⚠️ Husky: predisposto a olho azul (uveíte) e problemas oculares hereditários.',
        # Neurológico
        'dobermann': '⚠️ Dobermann: predisposto a cardiomiopatia dilatada — síncope ou fraqueza pode ser cardíaca.',
        'são bernardo': '⚠️ Porte gigante: torção gástrica (GDV) é risco real após refeições — abdômen distendido = urgência máxima.',
        'great dane': '⚠️ Porte gigante: torção gástrica (GDV) — nunca exercitar após comer.',
        'rottweiler': '⚠️ Rottweiler: osteossarcoma é mais comum — claudicação progressiva em adulto/idoso merece avaliação urgente.',
    }
    alerta_raca = ''
    raca_lower = raca.lower() if raca else ''
    for k, v in RACAS_RISCO.items():
        if k in raca_lower:
            alerta_raca = f'\nALERTA ESPECÍFICO DA RAÇA: {v}'
            break

    system_prompt = f"""Você é um especialista veterinário de referência do VetZap com profundo conhecimento em medicina veterinária integrativa — domina clínica médica de pequenos animais, medicina integrativa, fitoterapia veterinária, nutrição animal, comportamento e medicina de suporte domiciliar para cães e gatos.

PACIENTE: {nome} | {especie} | raça {raca} | {idade} anos | {peso_info} | {sexo_txt} | {castrado_txt}
QUEIXA PRINCIPAL: {categoria_info['label']}{alerta_raca}
{f'OBSERVAÇÕES DO TUTOR SOBRE O PET: {obs_pet}' if obs_pet else ''}{historico_pet_ctx}

MISSÃO: Ser o melhor suporte veterinário pré-consulta disponível — como um especialista de confiança que trata o pet como se fosse o seu. Orientações práticas, precisas, acolhedoras e profundamente fundamentadas na medicina veterinária moderna e integrativa.

REGRAS FUNDAMENTAIS:
1. TRIAGEM + ORIENTAÇÃO DE SUPORTE — nunca diagnóstico definitivo, nunca prescrição médica.
2. Linguagem empática, clara e objetiva. O tutor está preocupado — acolha primeiro, oriente depois.
3. UMA pergunta por vez. Colete 4-6 respostas antes de concluir.
4. Ao concluir: responda SOMENTE em JSON válido.
5. NUNCA cite antibióticos, anti-inflamatórios, corticoides, antiparasitários ou qualquer medicamento prescrito.
6. CONSIDERE sempre: raça, sexo, castração, idade e peso em TODAS as orientações.

CLASSIFICAÇÕES:
• "urgente" → ir ao veterinário AGORA. (convulsão ativa, dispneia severa, inconsciência, sangramento abundante, intoxicação, trauma grave, abdômen rígido/distendido, mucosas azuladas ou pálidas, colapso, corpo estranho engasgado, suspeita de obstrução urinária em gato macho)
• "atencao" → veterinário em até 24h. (vômito 3x+ sem melhora, diarreia com sangue, febre, letargia moderada, ferimento aberto, olho com secreção abundante, dificuldade para urinar com dor, prostração, anorexia >24h)
• "estavel" → pode aguardar consulta de rotina. (episódio único e leve, coceira sem lesão ativa, inapetência pontual, comportamental leve, eliminações normais)

═══════════════════════════════════════════════
BANCO DE CONHECIMENTO VETERINÁRIO INTEGRADO
═══════════════════════════════════════════════

🤢 DIGESTIVO — VÔMITO/DIARREIA:

Vômito leve (1-2 episódios, animal alerta e hidratado):
• Jejum hídrico 1-2h; depois ofereça água em pequenas quantidades:
  Porte pequeno: 1-2 col sopa a cada 20 min | Médio: 3-4 col | Grande: 5-6 col
• Após 4h sem vômito → dieta branda: frango cozido sem pele/osso/sal/tempero + arroz branco (proporção 1:2)
  Pequeno: 2-3 col sopa/refeição, 3-4x dia | Médio: 5-7 col, 3x | Grande: 10-14 col, 3x
• Chá de camomila (SOMENTE CÃES ≥6 meses): prepare concentrado, deixe esfriar até morno. Propriedades antiespasmódicas e anti-inflamatórias no TGI.
  Pequeno: 30-50ml | Médio: 80-120ml | Grande: 150-200ml — ofereça após o jejum hídrico
• Caldo de frango caseiro (sem sal, sem cebola, sem alho, sem tempero): estimula hidratação se recusar água
• Gengibre fresco ralado (SOMENTE CÃES, dose mínima): meia colher de café diluída em água — propriedade antiemética leve. NUNCA para gatos.
• Probiótico para pets (Floravet, Fortbac — sem receita): restaura flora intestinal, ofereça junto à dieta branda
• Reidratante para pets (PetOral ou similar): especialmente se vômito >2x — sem receita em pet shops

Diarreia leve (sem sangue, sem muco, animal ativo):
• Dieta branda igual ao vômito por 24-48h
• Batata-doce cozida sem casca (fibra solúvel firma as fezes): Pequeno: 1-2 col | Médio: 3-4 col | Grande: 5-6 col — misture à refeição
• Abóbora cabotiá cozida sem tempero: efeito similar, palatável, bem tolerada por todas as raças
• Chá de hortelã-pimenta morno (SOMENTE CÃES): auxilia na regulação intestinal. Pequeno: 20-30ml | Médio: 50ml | Grande: 80ml. NÃO para gatos (mentol tóxico para felinos).
• Probiótico para pets — siga embalagem para o porte
• Monitorar hidratação: Pequeno: mín. 35ml água/kg/dia | Médio: 45ml/kg | Grande: 55ml/kg

Inapetência isolada (sem outros sintomas):
• Tente: frango cozido morno (aroma estimula apetite), atum em água natural (especialmente gatos), patê úmido de qualidade aquecido por 10-15seg
• Gatos: aquecimento do alimento libera aroma — fundamental para estimular
• Ambiente calmo e tranquilo durante a refeição (estresse reduz apetite em até 40%)
• ⚠️ Gatos com anorexia >48h: risco de lipidose hepática felina — busque vet sem demora

🐾 QUEDA DE PELO / ALOPECIA:

PRIMEIRAMENTE — diferencie troca de pelo normal de queda patológica:
→ NORMAL (troca sazonal): ocorre principalmente primavera/outono, pelo de guarda sendo substituído, pelagem inteira e uniforme, pele normal embaixo, sem coceira excessiva. Raças de duplo pelame (Husky, Golden, Labrador, Pastor Alemão) têm mudança de pelagem muito intensa — pode parecer assustador mas é natural.
→ PATOLÓGICA: regiões sem pelo (placas/manchas), pele vermelha/escamosa/com crostas embaixo, coceira intensa associada, pelo quebrando (não caindo inteiro), queda assimétrica, + outros sintomas (ganho de peso, letargia, sede excessiva → suspeita de hipotireoidismo ou Cushing).

PERGUNTAS DE TRIAGEM para queda de pelo:
• Há quanto tempo está acontecendo?
• A queda é difusa (pelo todo) ou em manchas/regiões específicas?
• Tem coceira intensa associada?
• O que o pet come? Qual marca/tipo de ração ou dieta? Há quanto tempo com essa alimentação?
• A pele embaixo do pelo está normal ou tem vermelhidão, descamação, crostas?
• Há mudança de estação climática recente? (primavera/outono)
• O pet saiu de um período de estresse? (mudança de casa, novos animais, etc.)

ORIENTAÇÕES NUTRICIONAIS PARA PELAGEM:
• Ômega-3: principal nutriente para saúde do pelame e pele. Fontes naturais:
  - Sardinha em água natural (sem sal): Porte pequeno: ½ sardinha 2-3x/sem | Médio: 1 sardinha | Grande: 2 sardinhas
  - Salmão cozido sem sal/tempero: excelente fonte, ofereça 1-2x/sem
  - Óleo de salmão para pets (sem receita, pet shops): siga embalagem conforme o peso
  - Óleo de linhaça: alternativa vegetal, mas menor biodisponibilidade
• Ovo cozido (inteiro, sem sal): rico em biotina, excelente para pelo e unhas. 1-2 ovos/sem conforme porte. Sempre cozido — clara crua bloqueia absorção de biotina
• Batata-doce cozida: vitamina A + fibras, auxilia na renovação da pele
• Cenoura crua ralada: betacaroteno, favorece brilho e saúde da pele
• Abóbora cozida: vitaminas B + E, favorece ciclos de crescimento do pelo

ATENÇÃO À RAÇÃO:
• Rações com baixa qualidade proteica: pelo opaco, quebradiço, queda excessiva
• Alergias alimentares: podem causar dermatite + queda focal de pelo — ingredientes mais comuns: frango, soja, trigo, milho
• Troca abrupta de ração: pode desencadear queda temporária — sempre faça transição gradual em 7-10 dias
• Rações grain-free: atenção ao histórico — avaliar com vet se for raça predisposta a DCM

SUPLEMENTOS DE SUPORTE (sem receita):
• Suplementos de ômega-3 específicos para pets (PetOmega, OmegaPet ou similares)
• Suplemento de biotina para pets (pet shops, sem receita)
• Levedura de cerveja: rico em complexo B + zinco, melhora pelagem — Pequeno: ½ col chá | Médio: 1 col | Grande: 2 col — junto com a ração

🐛 PELE — COCEIRA/DERMATITE/FERIDAS:

Coceira generalizada ou pontual:
• Banho com shampoo hipoalergênico para pets: deixe agir 5-8 min, enxágue completamente
• Compresa de chá de camomila frio: embeba gaze no chá frio e aplique na região por 5-10 min — anti-inflamatório e calmante tópico eficaz
• Banho de aveia coloidal: dissolva 2-3 col sopa de farinha de aveia grossa em 1L de água morna, use como enxágue final sem retirar — excelente para coceiras generalizadas e pele seca
• Aloe vera gel puro (sem álcool, sem perfume, sem corante): aplicação tópica em pequenas áreas irritadas — cicatrizante natural. Use colar elizabetano para evitar ingestão
• Colar elizabetano: essencial para prevenir automutilação e infecção secundária
• Evite: produtos humanos, água oxigenada, álcool, pomadas para uso humano
• Avalie ambiente: troca de ração, novo produto de limpeza, coleira nova, carpete, ácaros

Ferimento superficial (arranhado, corte pequeno <1cm):
• Lave com soro fisiológico 0,9% em jato suave — nunca álcool ou água oxigenada
• Mel puro de abelha (manuka ou silvestre): aplicação de fina camada sobre a ferida — comprovadas propriedades antimicrobianas, cicatrizantes e anti-inflamatórias. Cubra com gaze e troque 2x/dia. Funciona muito bem em lesões superficiais limpas
• Calêndula em pomada ou gel (farmácias naturais, sem receita): poderoso cicatrizante natural, seguro em cães. Em gatos: prefira apenas soro fisiológico (tendem a lamber mais)
• Colar elizabetano para proteger
• Sinais de infecção → busque vet: inchaço progressivo, calor excessivo, secreção amarelada/verde, odor forte, febre

👁️ OLHOS E ORELHAS:

Olho com irritação leve (sem secreção abundante, sem fechamento forçado):
• Limpeza: gaze umedecida com soro fisiológico, movimentos de dentro para fora
• Compresa de chá de camomila gelado nas pálpebras EXTERNAS (nunca dentro do olho): efeito anti-inflamatório e calmante leve
• Colar elizabetano para evitar coçar
• Nunca: colírio humano (pH incompatível), pomadas não veterinárias

Orelha com coceira/odor leve:
• Limpeza com algodão e solução auricular para pets (sem receita) — nunca cotonetes
• Chá de camomila morno (temperatura corporal): 3-5 gotas no canal + massagem suave na base por 30 seg — uso popular para limpeza e alívio de coceira leve
• Mistura de vinagre de maçã + água morna (50:50): 3-4 gotas + massagem — ambiente ácido desfavorável a fungos/bactérias. SOMENTE se não houver lesão visível ou sangramento

😴 LETARGIA/APATIA SEM OUTROS SINTOMAS:
• Ambiente confortável, temperatura entre 20-26°C, cama macia em local tranquilo
• Hidratação: ofereça água fresca com frequência (troque a cada 2-3h — animais preferem água fresca)
• Estímulo alimentar: frango morno, patê úmido aquecido
• Chá de erva-cidreira morno (SOMENTE CÃES): propriedades calmantes, auxilia em letargia por estresse. Pequeno: 30ml | Médio: 60ml | Grande: 100ml
• Registre: há quanto tempo? Comeu? Bebeu? Fez xixi e cocô normalmente? Temperatura (normal: cão 38-39°C, gato 38-39,5°C)

😮‍💨 RESPIRATÓRIO LEVE (tosse episódica, sem dispneia):
• Ambiente sem fumaça, produtos de limpeza, perfumes, velas, ar condicionado seco
• Umidificador de ar ou vasilha com água próxima ao aquecedor — ar seco agrava tosse
• Mel puro (SOMENTE CÃES ≥1 ano): meia col chá (pequeno) a 1 col chá (médio/grande) diluído em água morna — efeito calmante comprovado em mucosas. NUNCA para gatos.
• Inalação de vapor: apenas no ambiente (banheiro fechado com água quente, 10-15 min) — nunca óleos essenciais para gatos (tóxicos)
• Peitoral no lugar de coleira — coleira pode pressionar traqueia e piorar tosse
• ⚠️ Tosse produtiva (com secreção), dispneia, cianose → urgente

🚿 URINÁRIO:
• ⚠️⚠️ GATO MACHO com dificuldade para urinar = URGÊNCIA ABSOLUTA — obstrução urinária mata em horas
• Para outros casos leves: aumento da oferta de água fresca (troque com frequência, fontes em movimento estimulam gatos)
• Alimento úmido/patê: aumenta ingesta hídrica em 50-70% comparado à ração seca
• Caixas de areia: mínimo 1 por gato + 1 extra, limpeza diária, local calmo
• Reduza estressores: mudanças no ambiente, gatos novos, obras

⚡ NEUROLÓGICO/TRAUMA → sempre urgente — oriente transporte seguro:
• Não mova o animal com suspeita de trauma raqui-medular — imobilize em superfície rígida
• Mantenha aquecido (cobertor, nunca bolsa de água quente direta)
• Nada pela boca em animal inconsciente ou convulsionando
• Convulsão: afaste objetos, NÃO contenha o animal, cronometre duração. Após cessar → vet imediato
• Trauma ocular: cubra o olho com gaze úmida em soro fisiológico — nunca pressione

☠️ INTOXICAÇÕES — URGÊNCIA ABSOLUTA EM TODOS OS CASOS:
Pergunte SEMPRE: o que o pet pode ter ingerido nas últimas 4-6 horas?

ALIMENTOS TÓXICOS PARA CÃES E GATOS:
• Chocolate/cacau: teobromina — tremores, convulsão, arritmia. Quanto mais escuro, mais tóxico.
• Uva e passa: insuficiência renal aguda. Qualquer quantidade é perigosa.
• Xilitol (adoçante em chicletes, doces sem açúcar, pasta dental): hipoglicemia grave + falência hepática. URGÊNCIA MÁXIMA.
• Cebola, alho, cebolinha, alho-poró (todas as formas: cru, cozido, em pó): anemia hemolítica.
• Abacate: cardiotoxicidade + pancreatite.
• Macadâmia: fraqueza, tremores, hipertermia.
• Álcool: depressão do SNC, coma.
• Cafeína: café, chá preto, refrigerante, energético — taquicardia, tremores, convulsão.
• Sal em excesso: hipernatremia — convulsão.
• Milho-de-pipoca salgado, comida temperada com alho/cebola: tóxico gradual.

PLANTAS COMUNS TÓXICAS:
• Lírio (qualquer espécie) → FATAL para gatos: insuficiência renal. QUALQUER contacto exige vet imediato.
• Dieffenbachia (comigo-ninguém-pode): irritação oral severa + edema de glote.
• Azaleia/Rododendro: vômito, colapso, arritmia.
• Oleandro: cardiotóxico.
• Zamioculcas: irritação GI.
• Espatifilo, antúrio: oxalato de cálcio — queimação oral, vômito.

OUTROS:
• Paracetamol/Tylenol: FATAL para gatos. Extremamente tóxico.
• Ibuprofeno/Aspirina: úlcera GI, insuficiência renal.
• Veneno de rato (rodenticida): sangramento interno — sintomas aparecem 3-5 dias depois.
• Veneno de barata/formiga: avalie o produto específico.
• Sapo (Rhinella marina/sapo-cururu): vômito intenso + salivação + arritmia — lave a boca com água corrente imediatamente.
→ Em qualquer suspeita de intoxicação: NÃO induza vômito sem orientação veterinária, não dê leite, vá ao vet com a embalagem do produto se possível.

🦴 ORTOPÉDICO / MUSCULOESQUELÉTICO:

Claudicação (mancar):
→ Pergunte: qual pata? Desde quando? Após queda/trauma ou espontâneo? Suporta peso?
→ Grau leve (suporta peso, sem dor visível): aplique frio local (gelo embrulhado em pano, 10-15 min, 2-3x/dia) nas primeiras 48h. Repouso absoluto.
→ Após 48h: calor úmido (pano morno) pode ajudar. Massagem suave ao redor (não sobre o local de dor).
→ NÃO force o animal a andar, não massageie diretamente sobre área de dor.
→ Repouso em superfície macia, sem escadas, sem saltos.
→ Raças predispostas (Dachshund → DPIV, Labrador/Golden → displasia, Yorkshire → luxação de patela): qualquer claudicação merece avaliação veterinária.
→ Urgente: paralisia/paresia de membros, dor intensa, incapacidade total de apoiar.

Dor nas costas/coluna (especialmente Dachshund):
→ DPIV é URGÊNCIA — paralisia progressiva dos membros posteriores pode ocorrer em horas.
→ Oriente repouso TOTAL, restrição de movimentos (caixinha/cercadinho), e busca veterinária urgente.
→ Nunca carregue o animal pela barriga durante sintoma — apoie o corpo inteiro horizontalmente.

😰 COMPORTAMENTAL:

Ansiedade / Medo / Agitação:
→ Pergunte: gatilho identificado? (trovão, fogos, visitas, separação). Há quanto tempo? Comportamentos destruidores? Automutilação?
→ Ambiente: espaço seguro (cabaninha, caixinha), meias/camiseta com cheiro do tutor, luz ambiente baixa
→ Música clássica ou white noise: reduz ansiedade em até 50% em estudos caninos
→ Thundershirt/wrap de pressão: abraço compressivo com uma camiseta velha pode ter efeito calmante
→ Pétalas de Bach — Rescue Remedy para pets (sem álcool, versão pet): gotas na água ou no focinho — seguro e sem contraindicação
→ Lavanda: borrifar na cama do pet (nunca diretamente no animal) — propriedades calmantes. NÃO para gatos (felinos são sensíveis a óleos essenciais)
→ Exercício físico antes do evento estressante (fogos de artifício) reduz intensidade da ansiedade
→ Se automutilação (lambedura excessiva, arranhar): colar elizabetano + vet para avaliação

Agressividade:
→ Pergunte: nova ou mudança súbita? Dor associada? Mudança no ambiente/rotina?
→ ⚠️ Agressividade de início súbito em animal previamente dócil pode indicar DOR — avaliação urgente
→ Nunca puna fisicamente — piora o comportamento agressivo
→ Consulta com médico veterinário comportamentalista + avaliação clínica para descartar causas de dor

Marcação / problemas com caixa de areia (gatos):
→ Pergunte: urina fora da caixa ou marcação em paredes? Caixa sempre limpa? Quantos gatos na casa?
→ Regra 1 por gato + 1 extra para caixas de areia
→ Areia sem perfume (gatos preferem), local calmo, longe da comida
→ Estresse territorial: Feliway difusor (feromonas sintéticas — sem receita, pet shops) — altamente eficaz
→ Descarte infecção urinária antes de atribuir ao comportamento

🔬 AVALIAÇÃO DE DOR — COMO IDENTIFICAR:
O tutor muitas vezes não sabe que o pet está com dor. Oriente a observar:
• Postura encolhida, relutância em mover-se
• Relutância em subir/descer
• Vocalização ao toque em região específica
• Mudança de comportamento: animal dócil ficando reativo ao toque
• Protege região específica do corpo, lambe excessivamente um local
• Diminuição do apetite + letargia + posição "de reza" (cão) = dor abdominal
• Constrição pupilar ou dilatação assimétrica (dor severa)

🍼 FILHOTES (<6 meses) — PROTOCOLO ESPECIAL:
• Muito mais vulneráveis à desidratação — sinal de urgência em 30-60% menos tempo
• Hipoglicemia: filhotes pequenos que ficam mais de 4h sem comer podem hipoglicemiar → letargia, tremores, convulsão
• Vacinação incompleta: qualquer sintoma é mais grave (parvovirose, cinomose não estão descartadas)
• Dose de TUDO é proporcionalmente menor — 30-40% das quantidades de adulto pequeno
• Qualquer sintoma persistente >4h em filhote = mínimo atenção, frequentemente urgente

👴 GERIÁTRICOS (>8 anos cão / >10 anos gato):
• Sede excessiva + urina em excesso: suspeita de diabetes ou doença renal — atenção
• Perda de peso progressiva: hipotiroidismo, doença renal, diabetes, neoplasia
• Tosse crônica em idosos: pode ser cardíaca (não só respiratória)
• Nódulos/caroços novos: avaliação prioritária — incidência de tumores aumenta significativamente
• Confusão, desorientação, acordar à noite: síndrome disfunção cognitiva (equivalente Alzheimer)
• Fraqueza dos membros posteriores progressiva: mielopatia degenerativa em certas raças

⚥ SEXO E CASTRAÇÃO — RISCOS ESPECÍFICOS:
• FÊMEA NÃO CASTRADA: piometra (infecção uterina) — urgência. Sinais: letargia, vômito, sede excessiva, corrimento vaginal (às vezes sem corrimento = forma fechada, mais grave), abdômen distendido. Ocorre 1-2 meses após o cio.
• MACHO NÃO CASTRADO: hiperplasia prostática benigna — dificuldade para defecar, tenesmo, sangue nas fezes/urina
• FÊMEA (qualquer): tumor de mama — palpe mensalmente. Nódulos mamários em fêmeas não castradas = avaliação prioritária.
• MACHO (qualquer): torção testicular — dor aguda, testículo aumentado e quente = urgência

🤰 REPRODUTIVO:
• Parto normal (cadela/gata): intervalo máximo entre filhotes = 2h. Se ultrapassa ou fêmea faz força sem expulsar filhote → urgente.
• Piometra: diagnóstico mais comum em fêmeas não castradas 1-2 meses após o cio. CIRURGIA é o tratamento — não existe suporte domiciliar suficiente. Urgência.
• Gestação: evite vacinas, medicamentos, estresse, exposição a toxinas. Alimentação: ração para filhotes ou gestante (mais calórica) no terço final.
• Lactação: nunca separe filhotes antes de 45-60 dias (cães) / 60-90 dias (gatos).

💉 VACINAÇÃO E VERMINOSE (contexto preventivo):
• Pergunte sempre: o pet está em dia com vacinas e vermífugo?
• Filhotes sem vacinação completa têm risco de parvovirose (vômito + diarreia hemorrágica + depressão severa) e cinomose
• Sinais sugestivos de verminose: diarreia recorrente, barriga distendida, pelo opaco, emagrecimento, "trenó" (arrastar o traseiro no chão)
• Verminose pode ser confundida com alergia alimentar ou diarreia simples

═══════════════════════════════════════════════
CALIBRAÇÃO POR PORTE ({peso_info}):
═══════════════════════════════════════════════
Sempre adapte quantidades de água, dieta, chás e produtos ao porte do animal.
Para FILHOTES (<6 meses): reduza 50% das quantidades e eleve o nível de urgência — são muito mais vulneráveis.
Para IDOSOS (>8 anos): mesma elevação de cautela.
Considere SEMPRE raça ({raca}), sexo ({sexo_txt}) e castração ({castrado_txt}) nas orientações.

PRODUTOS SEM RECEITA (cite quando pertinente):
"Encontrado sem receita em pet shops:" — probióticos (Floravet, Fortbac), reidratantes (PetOral), shampoos hipoalergênicos, colares elizabetanos, soro fisiológico, solução auricular para pets, calêndula gel, aloe vera gel puro
Use: "Tutores costumam utilizar..." ou "Disponível sem receita em pet shops..."
NUNCA mencione medicamentos prescritos, antibióticos, anti-inflamatórios, corticoides ou antiparasitários.

NOTA FINAL OBRIGATÓRIA em todo resultado:
"⚠️ Estas são orientações gerais de suporte domiciliar. Não substituem avaliação veterinária presencial. Em caso de dúvida ou piora, consulte um veterinário."

FORMATO FINAL (ao concluir triagem):
{{"tipo":"resultado","resultado":"urgente|atencao|estavel","orientacoes":"orientações completas e práticas conforme o caso, com quebras de linha para leitura fácil","encaminhar":true|false,"mensagem":"encerramento acolhedor em 1-2 frases"}}

DURANTE TRIAGEM (antes de concluir):
{{"tipo":"pergunta","mensagem":"próxima pergunta objetiva e empática"}}

SEMPRE JSON válido. Nunca mencione tecnologia, sistema ou processamento."""

    # ── Motor 1: Gemini (primário) ──────────────────────────────────────────────
    if _use_gemini() and _gemini_on():
        try:
            contents = []
            for h in historico:
                role = 'model' if h['role'] == 'assistant' else 'user'
                contents.append({'role': role, 'parts': [{'text': h['content']}]})
            if not contents:
                contents = [{'role': 'user', 'parts': [{'text': 'Inicie a triagem.'}]}]
            raw = _gemini_call(system_prompt, contents, json_mode=True,
                               max_tokens=2400, temperature=0.3)
            return json.loads(raw)
        except Exception as e:
            log.warning('[PETmed] Gemini triagem falhou, tentando Groq: %s', e)

    # ── Motor 2: Groq (reserva / turbo texto) ───────────────────────────────────
    if _groq_client:
        try:
            messages = [{'role': 'system', 'content': system_prompt}]
            for h in historico:
                messages.append({'role': h['role'], 'content': h['content']})
            resp = _groq_client.chat.completions.create(
                model='llama-3.3-70b-versatile',
                messages=messages,
                max_tokens=2400,
                temperature=0.3,
                response_format={'type': 'json_object'},
            )
            return json.loads(resp.choices[0].message.content.strip())
        except Exception as e:
            log.warning('[PETmed] Groq triagem falhou: %s', e)

    return {
        'tipo': 'pergunta',
        'mensagem': 'Pode me descrever melhor o que está acontecendo com seu pet?'
    }


# ── Rotas públicas ─────────────────────────────────────────────────────────────

# (landing pública '/vetzap/' é servida pelo vetzap_bot; não há index aqui p/ evitar colisão)

@petmed_bp.route('/planos')
def planos():
    # modelo de planos foi descontinuado → redireciona para créditos
    return redirect('/vetzap/creditos?' + request.query_string.decode('utf-8'))


@petmed_bp.route('/creditos')
def creditos():
    """Vitrine de pacotes de crédito (1 crédito = 1 atendimento)."""
    msg = request.args.get('msg', '')
    saldo = None
    if session.get('pm_user_id'):
        saldo = _get_creditos(session['pm_user_id'])
    return render_template('petmed/creditos.html',
                           pacotes=PACOTES_CREDITO, msg=msg, saldo=saldo,
                           preco_unit=PRECO_UNIT)


@petmed_bp.route('/comprar/<pacote>', methods=['POST'])
@petmed_login_required
def comprar(pacote):
    """Cria cobrança única (PIX/boleto/cartão) para um pacote de créditos."""
    if pacote not in PACOTES_CREDITO:
        return redirect('/vetzap/creditos?msg=pacote_invalido')
    u = _get_user()
    billing_type = request.form.get('billing_type', 'PIX')
    if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
        billing_type = 'PIX'

    # CPF é exigido pelo Asaas — coleta na compra (cadastro não pede mais)
    cpf = re.sub(r'\D', '', request.form.get('cpf', '') or '')
    if not (u['cpf'] and len(re.sub(r'\D', '', u['cpf'])) == 11):
        if len(cpf) != 11:
            return redirect('/vetzap/creditos?msg=cpf')
        conn = get_petmed_db()
        conn.execute('UPDATE petmed_users SET cpf=? WHERE id=?', (cpf, u['id']))
        conn.commit()
        conn.close()
        u = _get_user()  # refetch com o CPF salvo

    try:
        customer_id = _asaas_criar_ou_buscar_cliente(u)
        if not customer_id:
            return redirect('/vetzap/creditos?msg=erro_pagamento')
        pag = _asaas_criar_pagamento_creditos(customer_id, u['id'], pacote, billing_type)
        if pag.get('id'):
            p = PACOTES_CREDITO[pacote]
            conn = get_petmed_db()
            conn.execute(
                '''INSERT INTO petmed_compras
                   (user_id, pacote, creditos, valor, status, asaas_payment_id, billing_type)
                   VALUES (?,?,?,?,?,?,?)''',
                (u['id'], pacote, p['creditos'], p['preco'], 'pendente', pag['id'], billing_type)
            )
            conn.commit()
            conn.close()
            payment_url = pag.get('invoiceUrl') or pag.get('bankSlipUrl') or ''
            if payment_url:
                return redirect(payment_url)
            return redirect('/vetzap/dashboard?msg=aguardando_pgto')
        else:
            log.error('[PETmed] Asaas créditos sem id: %s', pag)
            return redirect('/vetzap/creditos?msg=erro_pagamento')
    except Exception as ex:
        log.error('[PETmed] Erro compra créditos: %s', ex, exc_info=True)
        return redirect('/vetzap/creditos?msg=erro_pagamento')


@petmed_bp.route('/consulta-agora', methods=['GET', 'POST'])
@petmed_login_required
def consulta_agora():
    """Checkout para Consulta Avulsa — pagamento único, libera 24h de acesso."""
    u = _get_user()
    # Se já tem assinatura ativa, vai direto pro dashboard
    if u['plano_ativo']:
        return redirect('/vetzap/dashboard')
    erro = ''
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX')
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            erro = 'Método de pagamento inválido.'
        else:
            try:
                customer_id = _asaas_criar_ou_buscar_cliente(u)
                if not customer_id:
                    erro = 'Erro ao criar perfil de pagamento. Verifique seus dados cadastrais.'
                else:
                    pag = _asaas_criar_pagamento_avulso(customer_id, u['id'], billing_type)
                    if pag.get('id'):
                        # Salva payment_id pendente
                        conn = get_petmed_db()
                        conn.execute(
                            '''INSERT OR REPLACE INTO petmed_assinaturas
                               (user_id, plano, valor, status, asaas_payment_id, billing_type)
                               VALUES (?,?,?,?,?,?)''',
                            (u['id'], 'consulta_avulsa', CONSULTA_AVULSA['preco'],
                             'pendente', pag['id'], billing_type)
                        )
                        conn.commit()
                        conn.close()
                        payment_url = pag.get('invoiceUrl') or pag.get('bankSlipUrl') or ''
                        if payment_url:
                            return redirect(payment_url)
                        return redirect('/vetzap/aguardando-pagamento?tipo=avulsa')
                    else:
                        desc = pag.get('errors', [{}])
                        erro = desc[0].get('description', 'Erro ao gerar pagamento.') if desc else 'Erro ao gerar pagamento.'
            except Exception as ex:
                log.error('[PETmed] Erro consulta avulsa: %s', ex, exc_info=True)
                erro = 'Erro ao processar. Tente novamente ou contate (47) 99960-6998'
    return render_template('petmed/consulta_checkout.html',
                           u=u, c=CONSULTA_AVULSA, erro=erro)


@petmed_bp.route('/assinar/<plano>', methods=['GET', 'POST'])
@petmed_login_required
def assinar(plano):
    """Checkout: escolhe método de pagamento e cria assinatura no Asaas."""
    if plano not in PLANOS:
        return redirect('/vetzap/planos')
    u = _get_user()
    erro = ''
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX')
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            erro = 'Método de pagamento inválido.'
        else:
            try:
                customer_id = _asaas_criar_ou_buscar_cliente(u)
                if not customer_id:
                    erro = 'Erro ao criar perfil de pagamento. Verifique seus dados cadastrais.'
                else:
                    sub = _asaas_criar_assinatura(customer_id, plano, billing_type)
                    if sub.get('id'):
                        # Salva assinatura no banco (pendente — webhook ativa)
                        conn = get_petmed_db()
                        conn.execute(
                            '''INSERT OR REPLACE INTO petmed_assinaturas
                               (user_id, plano, valor, status, asaas_subscription_id, billing_type)
                               VALUES (?,?,?,?,?,?)''',
                            (u['id'], plano, PLANOS[plano]['preco'],
                             'pendente', sub['id'], billing_type)
                        )
                        conn.commit()
                        conn.close()
                        # Redireciona para link de pagamento do Asaas
                        payment_url = sub.get('invoiceUrl') or sub.get('bankSlipUrl') or ''
                        if payment_url:
                            return redirect(payment_url)
                        return redirect('/vetzap/aguardando-pagamento?sub=' + sub['id'])
                    else:
                        erro = sub.get('errors', [{}])[0].get('description', 'Erro ao criar assinatura.')
            except Exception as ex:
                erro = 'Erro ao processar pagamento. Tente novamente.'
    p = PLANOS[plano]
    return render_template('petmed/checkout.html', u=u, plano=plano, p=p, erro=erro)


@petmed_bp.route('/aguardando-pagamento')
@petmed_login_required
def aguardando_pagamento():
    """Página de aguardo após criar assinatura."""
    u = _get_user()
    sub_id = request.args.get('sub', '')
    return render_template('petmed/aguardando.html', u=u, sub_id=sub_id)


@petmed_bp.route('/webhook/asaas', methods=['GET', 'POST'])
def webhook_asaas():
    """Recebe notificações de pagamento do Asaas."""
    # GET = validação da URL pelo Asaas
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200

    # Valida token de autenticação do Asaas
    token_esperado = os.environ.get('ASAAS_WEBHOOK_TOKEN', '').strip().strip('"').strip("'")
    token_recebido = (request.headers.get('asaas-access-token', '') or '').strip().strip('"').strip("'")
    if token_esperado and token_recebido != token_esperado:
        return jsonify({'error': 'unauthorized'}), 401

    dados = request.get_json(silent=True) or {}
    evento = dados.get('event', '')
    pagamento = dados.get('payment', {})

    # Eventos de pagamento confirmado
    if evento in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED', 'SUBSCRIPTION_ACTIVATED'):
        ext_ref = pagamento.get('externalReference', '')       # vetzap_custid_plano ou vetzap_consulta_avulsa_USERID
        subscription_id = pagamento.get('subscription', '')
        payment_id = pagamento.get('id', '')

        conn = get_petmed_db()

        # ── Compra de CRÉDITOS — CLAIM ATÔMICO (idempotente + sem dupla-creditação) ──
        if payment_id:
            # Reivindica o pagamento de forma atômica: só UMA execução "ganha" (rowcount=1).
            # Reenvio do Asaas ou 2 webhooks simultâneos → os demais veem rowcount=0 e não creditam.
            claim = conn.execute(
                "UPDATE petmed_compras SET status='pago' "
                "WHERE asaas_payment_id=? AND status='pendente'",
                (payment_id,)
            )
            conn.commit()
            if claim.rowcount > 0:
                compra = conn.execute(
                    "SELECT * FROM petmed_compras WHERE asaas_payment_id=?", (payment_id,)
                ).fetchone()
                conn.execute(
                    'UPDATE petmed_users SET creditos = COALESCE(creditos,0) + ? WHERE id=?',
                    (compra['creditos'], compra['user_id'])
                )
                conn.commit()
                log.info('[PETmed] +%s créditos (compra %s) p/ user_id=%s',
                         compra['creditos'], compra['id'], compra['user_id'])
                u_row = conn.execute('SELECT nome, email FROM petmed_users WHERE id=?',
                                     (compra['user_id'],)).fetchone()
                if u_row and u_row['email']:
                    _enviar_email(
                        u_row['email'],
                        '✅ VetZap — Créditos liberados!',
                        _email_creditos_liberados(u_row['nome'].split()[0] if u_row['nome'] else 'tutor',
                                                  compra['creditos'])
                    )
                conn.close()
                return jsonify({'status': 'ok'}), 200

        # ── Consulta Avulsa: pagamento único (legado) ────────────────────────────
        if ext_ref.startswith('vetzap_consulta_avulsa_'):
            try:
                uid = int(ext_ref.split('_')[-1])
                exp = (datetime.now() + timedelta(hours=CONSULTA_AVULSA['horas'])).strftime('%Y-%m-%d %H:%M:%S')
                conn.execute('UPDATE petmed_users SET consulta_expires=? WHERE id=?', (exp, uid))
                conn.execute("UPDATE petmed_assinaturas SET status='ativo' WHERE user_id=? AND plano='consulta_avulsa'", (uid,))
                conn.commit()
                log.info('[PETmed] Consulta avulsa ativada para user_id=%s até %s', uid, exp)
                u_row = conn.execute('SELECT nome, email FROM petmed_users WHERE id=?', (uid,)).fetchone()
                if u_row and u_row['email']:
                    _enviar_email(
                        u_row['email'],
                        '✅ VetZap — Consulta ativada! Seu pet tem 24h de atendimento',
                        _email_consulta_avulsa_ativada(u_row['nome'].split()[0])
                    )
            except Exception as ex:
                log.error('[PETmed] Webhook avulsa erro: %s', ex)
            conn.close()
            return jsonify({'status': 'ok'}), 200

        # ── Assinatura mensal recorrente ──────────────────────────────────────────
        plano_novo = None
        if ext_ref.startswith('vetzap_'):
            partes = ext_ref.split('_')
            if len(partes) >= 3:
                plano_novo = partes[-1]

        if subscription_id or ext_ref:
            # Busca assinatura pelo subscription_id
            ass = conn.execute(
                'SELECT * FROM petmed_assinaturas WHERE asaas_subscription_id=?',
                (subscription_id,)
            ).fetchone()
            if ass and plano_novo in PLANOS:
                conn.execute(
                    '''UPDATE petmed_users
                       SET plano=?, plano_ativo=1, consulta_expires=NULL
                       WHERE id=?''',
                    (plano_novo, ass['user_id'])
                )
                conn.execute(
                    '''UPDATE petmed_assinaturas
                       SET status="ativo", plano=?
                       WHERE user_id=?''',
                    (plano_novo, ass['user_id'])
                )
                conn.commit()
                u_row = conn.execute('SELECT nome, email FROM petmed_users WHERE id=?',
                                 (ass['user_id'],)).fetchone()
                if u_row and u_row['email']:
                    p = PLANOS[plano_novo]
                    _enviar_email(u_row['email'], '✅ VetZap — Assinatura ativa!',
                        _email_pagamento_confirmado_petmed(
                            u_row['nome'].split()[0], p['nome'], p['preco_fmt']))
        conn.close()

    elif evento in ('SUBSCRIPTION_CANCELLED', 'PAYMENT_OVERDUE'):
        subscription_id = pagamento.get('subscription', '')
        if subscription_id:
            conn = get_petmed_db()
            ass = conn.execute(
                'SELECT * FROM petmed_assinaturas WHERE asaas_subscription_id=?',
                (subscription_id,)
            ).fetchone()
            if ass:
                conn.execute(
                    'UPDATE petmed_users SET plano_ativo=0 WHERE id=?',
                    (ass['user_id'],)
                )
                conn.execute(
                    "UPDATE petmed_assinaturas SET status='cancelado' WHERE user_id=?",
                    (ass['user_id'],)
                )
                conn.commit()
            conn.close()

    return jsonify({'received': True}), 200


@petmed_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if session.get('pm_user_id'):
        return redirect('/vetzap/dashboard')
    erro = ''
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        conn = get_petmed_db()
        u = conn.execute(
            'SELECT * FROM petmed_users WHERE email=?', (email,)
        ).fetchone()
        conn.close()
        if u and check_password_hash(u['password_hash'], senha):
            session['pm_user_id']  = u['id']
            session['pm_user_nome'] = u['nome']
            session['pm_plano']    = u['plano']
            conn2 = get_petmed_db()
            conn2.execute(
                'UPDATE petmed_users SET ultimo_acesso=? WHERE id=?',
                (_now(), u['id'])
            )
            conn2.commit()
            conn2.close()
            return redirect('/vetzap/dashboard')
        erro = 'E-mail ou senha incorretos.'
    return render_template('petmed/entrar.html', erro=erro)


@petmed_bp.route('/cadastrar', methods=['GET', 'POST'])
def cadastrar():
    if session.get('pm_user_id'):
        return redirect('/vetzap/dashboard')
    erro = ''
    plano_sel = request.args.get('plano', 'start')
    if request.method == 'POST':
        email     = request.form.get('email', '').strip().lower()
        senha     = request.form.get('senha', '')
        # Campos opcionais (cadastro mínimo: só email + senha)
        nome      = request.form.get('nome', '').strip()
        telefone  = request.form.get('telefone', '').strip()
        pet_nome  = request.form.get('pet_nome', '').strip()
        pet_esp   = request.form.get('pet_especie', 'cao')
        # Se não informar nome, usa o início do e-mail
        if not nome:
            nome = email.split('@')[0].replace('.', ' ').replace('_', ' ').title() or 'Tutor'

        if not email or '@' not in email:
            erro = 'Informe um e-mail válido.'
        elif len(senha) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        else:
            _u_id = None
            try:
                conn = get_petmed_db()
                # DEV_WHITELIST: se email/telefone está na whitelist e já existe, remove antes de re-cadastrar
                _tel_digits = re.sub(r'\D', '', telefone)
                if _pm_is_whitelisted(email, _tel_digits):
                    existing = conn.execute('SELECT id FROM petmed_users WHERE email=?', (email,)).fetchone()
                    if existing:
                        conn.execute('DELETE FROM petmed_pets WHERE user_id=?', (existing['id'],))
                        conn.execute('DELETE FROM petmed_users WHERE id=?', (existing['id'],))
                        conn.commit()
                        log.info('[PETmed] Whitelist: registro antigo de %s removido para re-cadastro', email)
                conn.execute(
                    '''INSERT INTO petmed_users
                       (nome, email, telefone, password_hash, plano_ativo, creditos)
                       VALUES (?,?,?,?,0,0)''',
                    (nome, email, telefone, generate_password_hash(senha))
                )
                conn.commit()
                u = conn.execute(
                    'SELECT * FROM petmed_users WHERE email=?', (email,)
                ).fetchone()
                if u is None:
                    raise Exception('Usuário não encontrado após INSERT')
                # Cria o primeiro pet só se o tutor informou (opcional)
                if pet_nome:
                    conn.execute(
                        '''INSERT INTO petmed_pets (user_id, nome, especie)
                           VALUES (?,?,?)''',
                        (u['id'], pet_nome, pet_esp)
                    )
                    conn.commit()
                _u_id    = u['id']
                _u_nome  = u['nome']
                _u_plano = u['plano']
                conn.close()
            except Exception as ex:
                log.error('[PETmed] Erro no cadastro de %s: %s | detalhe: %s', email, type(ex).__name__, str(ex), exc_info=True)
                try: conn.close()
                except: pass
                if 'UNIQUE' in str(ex):
                    erro = 'Este e-mail já está cadastrado. Faça login ou use outro e-mail.'
                elif 'no such table' in str(ex).lower():
                    # Banco não inicializado — tenta recriar e pede para tentar de novo
                    try:
                        from petmed_db import init_petmed_db as _reinit
                        _reinit()
                        log.warning('[PETmed] Banco recriado após "no such table". Usuário deve tentar novamente.')
                    except Exception as _re:
                        log.error('[PETmed] Falha ao recriar banco: %s', _re)
                    erro = 'Sistema reiniciado. Por favor, tente cadastrar novamente.'
                elif 'no column' in str(ex).lower():
                    erro = 'Erro de estrutura no banco de dados. Contate o suporte: (47) 99960-6998'
                    log.critical('[PETmed] COLUNA INEXISTENTE: %s', ex)
                else:
                    erro = f'Erro ao criar conta: {ex}. Tente novamente ou contate (47) 99960-6998'

            if _u_id:
                session['pm_user_id']   = _u_id
                session['pm_user_nome'] = _u_nome
                session['pm_plano']     = _u_plano
                # E-mail de boas-vindas (assíncrono best-effort, fora do try principal)
                try:
                    _enviar_email(
                        para=email,
                        assunto='🐾 Bem-vindo ao VetZap!',
                        html=_email_boas_vindas(nome, pet_nome)
                    )
                except Exception:
                    pass
                return redirect('/vetzap/dashboard?novo=1')
    return render_template('petmed/cadastrar.html', erro=erro,
                           planos=PLANOS, plano_sel=plano_sel)


@petmed_bp.route('/sair')
def sair():
    for k in ('pm_user_id', 'pm_user_nome', 'pm_plano'):
        session.pop(k, None)
    return redirect('/vetzap')


@petmed_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    """Gera código de 6 dígitos para redefinição de senha."""
    if session.get('pm_user_id'):
        return redirect('/vetzap/dashboard')
    codigo_gerado = None
    erro = ''
    msg = ''
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            erro = 'Informe seu e-mail.'
        else:
            conn = get_petmed_db()
            u = conn.execute(
                'SELECT id FROM petmed_users WHERE email=?', (email,)
            ).fetchone()
            if u:
                codigo = ''.join(random.choices('0123456789', k=6))
                conn.execute(
                    '''UPDATE petmed_users
                       SET reset_token=?,
                           reset_expires=datetime("now","+30 minutes")
                       WHERE id=?''',
                    (codigo, u['id'])
                )
                conn.commit()
                # Tenta enviar por e-mail
                email_ok = _enviar_email(
                    para=email,
                    assunto='🔑 Seu código de recuperação — VetZap',
                    html=_email_recuperacao(codigo)
                )
                if not email_ok:
                    # Fallback: exibe o código na tela (sem e-mail configurado)
                    codigo_gerado = codigo
            conn.close()
            if not codigo_gerado:
                msg = 'Código enviado para seu e-mail. Verifique a caixa de entrada (e o spam).'
    return render_template('petmed/esqueci-senha.html',
                           erro=erro, msg=msg, codigo_gerado=codigo_gerado)


@petmed_bp.route('/redefinir-senha', methods=['GET', 'POST'])
def redefinir_senha():
    """Valida código e define nova senha."""
    if session.get('pm_user_id'):
        return redirect('/vetzap/dashboard')
    erro = ''
    msg = ''
    if request.method == 'POST':
        email      = request.form.get('email', '').strip().lower()
        codigo     = request.form.get('codigo', '').strip()
        nova_senha = request.form.get('nova_senha', '')
        confirmar  = request.form.get('confirmar', '')
        if not email or not codigo:
            erro = 'Preencha e-mail e código.'
        elif len(nova_senha) < 6:
            erro = 'A nova senha deve ter pelo menos 6 caracteres.'
        elif nova_senha != confirmar:
            erro = 'As senhas não coincidem.'
        else:
            conn = get_petmed_db()
            u = conn.execute(
                '''SELECT id FROM petmed_users
                   WHERE email=? AND reset_token=?
                   AND reset_expires > datetime("now")''',
                (email, codigo)
            ).fetchone()
            if u:
                conn.execute(
                    '''UPDATE petmed_users
                       SET password_hash=?, reset_token=NULL, reset_expires=NULL
                       WHERE id=?''',
                    (generate_password_hash(nova_senha), u['id'])
                )
                conn.commit()
                conn.close()
                msg = 'Senha redefinida com sucesso! Você já pode entrar.'
            else:
                conn.close()
                erro = 'Código inválido ou expirado. Solicite um novo código.'
    return render_template('petmed/redefinir-senha.html', erro=erro, msg=msg)


# ── Área logada ────────────────────────────────────────────────────────────────

@petmed_bp.route('/dashboard')
@petmed_login_required
def dashboard():
    u    = _get_user()
    pets = _get_pets(u['id'])
    conn = get_petmed_db()
    triagens_recentes = conn.execute(
        '''SELECT * FROM petmed_triagens WHERE user_id=?
           ORDER BY created_at DESC LIMIT 5''',
        (u['id'],)
    ).fetchall()
    total_triagens = conn.execute(
        'SELECT COUNT(*) FROM petmed_triagens WHERE user_id=?', (u['id'],)
    ).fetchone()[0]
    # Vacinas vencendo nos próximos 30 dias
    vacinas_proximas = conn.execute(
        '''SELECT v.*, p.nome as pet_nome
           FROM petmed_vacinas v
           JOIN petmed_pets p ON v.pet_id = p.id
           WHERE v.user_id=? AND v.proxima IS NOT NULL
             AND v.proxima BETWEEN date('now') AND date('now','+30 days')
           ORDER BY v.proxima ASC LIMIT 5''',
        (u['id'],)
    ).fetchall()
    conn.close()
    novo = request.args.get('novo', '')
    pode_add, total_pets, limite_pets = _can_add_pet(u['id'], u['plano'])
    bloqueado_paywall, triagens_usadas = _check_paywall(u)
    return render_template('petmed/dashboard.html',
                           u=u, pets=pets,
                           triagens=triagens_recentes,
                           total_triagens=total_triagens,
                           vacinas_proximas=vacinas_proximas,
                           novo=novo,
                           pode_add=pode_add,
                           total_pets=total_pets,
                           limite_pets=limite_pets,
                           planos=PLANOS,
                           bloqueado_paywall=bloqueado_paywall,
                           triagens_usadas=triagens_usadas,
                           creditos=(u['creditos'] if 'creditos' in u.keys() and u['creditos'] is not None else 0),
                           pacotes=PACOTES_CREDITO)


@petmed_bp.route('/meus-pets')
@petmed_login_required
def meus_pets():
    u    = _get_user()
    pets = _get_pets(u['id'])
    pode_add, total_pets, limite_pets = _can_add_pet(u['id'], u['plano'])
    msg = request.args.get('msg', '')
    return render_template('petmed/meus_pets.html',
                           u=u, pets=pets,
                           pode_add=pode_add,
                           total_pets=total_pets,
                           limite_pets=limite_pets,
                           msg=msg)


@petmed_bp.route('/pets/adicionar', methods=['GET', 'POST'])
@petmed_login_required
def adicionar_pet():
    u = _get_user()
    pode_add, total_pets, limite_pets = _can_add_pet(u['id'], u['plano'])
    erro = ''

    if not pode_add:
        return redirect(f'/vetzap/planos?msg=limite_pets&plano={u["plano"]}')

    if request.method == 'POST':
        nome        = request.form.get('nome', '').strip()
        especie     = request.form.get('especie', 'cao')
        raca        = request.form.get('raca', '').strip()
        idade_anos  = request.form.get('idade_anos', 0) or 0
        idade_meses = request.form.get('idade_meses', 0) or 0
        peso_kg     = request.form.get('peso_kg', '') or None
        sexo        = request.form.get('sexo', 'nao_informado')
        castrado    = 1 if request.form.get('castrado') else 0
        observacoes = request.form.get('observacoes', '').strip()

        # Identificação de raça por foto
        foto_base64 = request.form.get('foto_base64', '')
        if foto_base64 and not raca:
            raca = _identificar_raca(foto_base64, especie)

        if not nome:
            erro = 'O nome do pet é obrigatório.'
        else:
            conn = get_petmed_db()
            conn.execute(
                '''INSERT INTO petmed_pets
                   (user_id, nome, especie, raca, idade_anos, idade_meses,
                    peso_kg, sexo, castrado, observacoes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (u['id'], nome, especie, raca, int(idade_anos),
                 int(idade_meses), peso_kg, sexo, castrado, observacoes)
            )
            conn.commit()
            conn.close()
            return redirect('/vetzap/meus-pets?msg=pet_adicionado')

    return render_template('petmed/adicionar_pet.html',
                           u=u, erro=erro,
                           limite_pets=limite_pets)


@petmed_bp.route('/pets/<int:pet_id>/editar', methods=['GET', 'POST'])
@petmed_login_required
def editar_pet(pet_id):
    u = _get_user()
    conn = get_petmed_db()
    pet = conn.execute(
        'SELECT * FROM petmed_pets WHERE id=? AND user_id=?', (pet_id, u['id'])
    ).fetchone()
    conn.close()
    if not pet:
        abort(404)

    erro = ''
    if request.method == 'POST':
        nome        = request.form.get('nome', '').strip()
        raca        = request.form.get('raca', '').strip()
        idade_anos  = request.form.get('idade_anos', 0) or 0
        idade_meses = request.form.get('idade_meses', 0) or 0
        peso_kg     = request.form.get('peso_kg', '') or None
        sexo        = request.form.get('sexo', 'nao_informado')
        castrado    = 1 if request.form.get('castrado') else 0
        observacoes = request.form.get('observacoes', '').strip()

        if not nome:
            erro = 'O nome do pet é obrigatório.'
        else:
            conn2 = get_petmed_db()
            conn2.execute(
                '''UPDATE petmed_pets SET nome=?, raca=?, idade_anos=?,
                   idade_meses=?, peso_kg=?, sexo=?, castrado=?, observacoes=?
                   WHERE id=? AND user_id=?''',
                (nome, raca, int(idade_anos), int(idade_meses),
                 peso_kg, sexo, castrado, observacoes, pet_id, u['id'])
            )
            conn2.commit()
            conn2.close()
            return redirect('/vetzap/meus-pets?msg=pet_editado')

    return render_template('petmed/editar_pet.html', u=u, pet=pet, erro=erro)


@petmed_bp.route('/pets/<int:pet_id>/excluir', methods=['POST'])
@petmed_login_required
def excluir_pet(pet_id):
    u = _get_user()
    conn = get_petmed_db()
    conn.execute(
        'DELETE FROM petmed_pets WHERE id=? AND user_id=?', (pet_id, u['id'])
    )
    conn.commit()
    conn.close()
    return redirect('/vetzap/meus-pets?msg=pet_removido')


# ── Cartão público do pet ─────────────────────────────────────────────────────

@petmed_bp.route('/pets/<int:pet_id>/cartao')
def cartao_pet(pet_id):
    """Página pública compartilhável do pet — sem login necessário."""
    conn = get_petmed_db()
    pet = conn.execute(
        'SELECT * FROM petmed_pets WHERE id=?', (pet_id,)
    ).fetchone()
    if not pet:
        conn.close()
        return redirect('/vetzap'), 302
    pet = dict(pet)
    # Vacinas do pet (só nomes e status)
    vacinas = conn.execute(
        'SELECT nome, data_aplic, proxima FROM petmed_vacinas WHERE pet_id=? ORDER BY data_aplic DESC',
        (pet_id,)
    ).fetchall()
    conn.close()
    vacinas = [dict(v) for v in vacinas]
    return render_template('petmed/cartao_pet.html',
                           pet=pet, vacinas=vacinas,
                           portal_url='https://vetzap.4kitem.com.br')


# ── Triagem ────────────────────────────────────────────────────────────────────

@petmed_bp.route('/triagem')
@petmed_login_required
def triagem_inicio():
    u    = _get_user()
    bloqueado, triagens_usadas = _check_paywall(u)
    if bloqueado:
        return redirect('/vetzap/creditos?msg=sem_credito')
    pets = _get_pets(u['id'])
    # Limpa triagem anterior da sessão
    session.pop('pm_triagem', None)
    return render_template('petmed/triagem_inicio.html',
                           u=u, pets=pets, categorias=CATEGORIAS,
                           triagens_usadas=triagens_usadas,
                           plano_ativo=u['plano_ativo'])


@petmed_bp.route('/triagem/chat', methods=['GET', 'POST'])
@petmed_login_required
def triagem_chat():
    u = _get_user()

    if request.method == 'POST':
        dados = request.get_json(silent=True) or {}
        acao  = dados.get('acao', '')

        # ── Iniciar triagem ────────────────────────────────────────────────────
        if acao == 'iniciar':
            # Verifica paywall antes de iniciar
            bloqueado, _ = _check_paywall(u)
            if bloqueado:
                return jsonify({
                    'tipo': 'paywall',
                    'mensagem': 'Você está sem créditos. Compre um atendimento para continuar.',
                    'url': '/vetzap/creditos?msg=sem_credito'
                })

            pet_id    = dados.get('pet_id')
            categoria = dados.get('categoria', 'outro')
            pet_info  = {}

            if pet_id:
                conn = get_petmed_db()
                pet = conn.execute(
                    'SELECT * FROM petmed_pets WHERE id=? AND user_id=?',
                    (pet_id, u['id'])
                ).fetchone()
                conn.close()
                if pet:
                    pet_info = dict(pet)

            # Salva estado da triagem na sessão
            session['pm_triagem'] = {
                'pet_id': pet_id,
                'pet_info': pet_info,
                'categoria': categoria,
                'historico': [],
                'iniciada': _now()
            }

            nome_pet = pet_info.get('nome', 'seu pet')
            cat_label = CATEGORIAS.get(categoria, {}).get('label', categoria)

            primeira_pergunta = (
                f"Olá! Vou te ajudar a avaliar {nome_pet}. 🐾\n\n"
                f"Vi que o problema é relacionado a **{cat_label}**.\n\n"
                f"Para começar: há quanto tempo {nome_pet} está apresentando esse sintoma?"
            )

            historico = session['pm_triagem']['historico']
            historico.append({'role': 'assistant', 'content': primeira_pergunta})
            session.modified = True

            return jsonify({'tipo': 'pergunta', 'mensagem': primeira_pergunta})

        # ── Resposta do tutor ─────────────────────────────────────────────────
        elif acao == 'responder':
            triagem = session.get('pm_triagem')
            if not triagem:
                return jsonify({'tipo': 'erro', 'mensagem': 'Sessão expirada. Inicie novamente.'})

            resposta_tutor = dados.get('mensagem', '').strip()
            if not resposta_tutor:
                return jsonify({'tipo': 'erro', 'mensagem': 'Mensagem vazia.'})

            historico = triagem['historico']
            historico.append({'role': 'user', 'content': resposta_tutor})

            # Chama a IA
            resultado = _fazer_triagem(
                triagem['pet_info'],
                triagem['categoria'],
                historico
            )

            if resultado.get('tipo') == 'pergunta':
                historico.append({'role': 'assistant', 'content': resultado['mensagem']})
                session.modified = True
                return jsonify(resultado)

            elif resultado.get('tipo') == 'resultado':
                # Salva triagem no banco
                pet_info  = triagem.get('pet_info', {})
                categoria = triagem.get('categoria', 'outro')
                conn = get_petmed_db()
                conn.execute(
                    '''INSERT INTO petmed_triagens
                       (user_id, pet_id, pet_nome, pet_especie, pet_raca,
                        categoria, perguntas_json, resultado, orientacoes, encaminhar_vet)
                       VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (
                        u['id'],
                        pet_info.get('id'),
                        pet_info.get('nome', 'Pet'),
                        pet_info.get('especie', 'cao'),
                        pet_info.get('raca', 'SRD'),
                        categoria,
                        json.dumps(historico, ensure_ascii=False),
                        resultado.get('resultado', 'atencao'),
                        resultado.get('orientacoes', ''),
                        1 if resultado.get('encaminhar') else 0
                    )
                )
                conn.commit()
                conn.close()
                # Debita 1 crédito por atendimento concluído
                _debita_credito(u['id'])
                session.pop('pm_triagem', None)
                resultado['creditos_restantes'] = _get_creditos(u['id'])
                return jsonify(resultado)

            return jsonify({'tipo': 'pergunta', 'mensagem': 'Pode me contar mais sobre o que está acontecendo?'})

        return jsonify({'erro': 'Ação inválida'}), 400

    # GET — página do chat
    triagem = session.get('pm_triagem')
    if not triagem:
        # Inicializa a partir dos query params (chegando do form triagem_inicio)
        pet_id_raw = request.args.get('pet_id', '0')
        categoria  = request.args.get('categoria', 'outro')
        if not categoria or categoria not in CATEGORIAS:
            return redirect('/vetzap/triagem')
        try:
            pet_id = int(pet_id_raw)
        except (ValueError, TypeError):
            pet_id = 0

        pet_info = {}
        if pet_id:
            conn = get_petmed_db()
            pet = conn.execute(
                'SELECT * FROM petmed_pets WHERE id=? AND user_id=?',
                (pet_id, u['id'])
            ).fetchone()
            conn.close()
            if pet:
                pet_info = dict(pet)

        session['pm_triagem'] = {
            'pet_id': pet_id,
            'pet_info': pet_info,
            'categoria': categoria,
            'historico': [],
            'iniciada': _now()
        }
        session.modified = True
        triagem = session['pm_triagem']

    return render_template('petmed/triagem_chat.html',
                           u=u, triagem=triagem, categorias=CATEGORIAS)


# ── Identificar raça via foto (AJAX) ──────────────────────────────────────────
@petmed_bp.route('/identificar-raca', methods=['POST'])
@petmed_login_required
def identificar_raca():
    dados = request.get_json(silent=True) or {}
    foto_b64 = dados.get('foto', '')
    especie  = dados.get('especie', 'cao')
    if not foto_b64:
        return jsonify({'raca': ''})
    raca = _identificar_raca(foto_b64, especie)
    return jsonify({'raca': raca})


# ── Histórico ─────────────────────────────────────────────────────────────────

@petmed_bp.route('/historico')
@petmed_login_required
def historico():
    u = _get_user()
    pet_id = request.args.get('pet_id', '')
    conn = get_petmed_db()
    if pet_id:
        triagens = conn.execute(
            '''SELECT * FROM petmed_triagens WHERE user_id=? AND pet_id=?
               ORDER BY created_at DESC LIMIT 50''',
            (u['id'], pet_id)
        ).fetchall()
    else:
        triagens = conn.execute(
            '''SELECT * FROM petmed_triagens WHERE user_id=?
               ORDER BY created_at DESC LIMIT 50''',
            (u['id'],)
        ).fetchall()
    pets = conn.execute(
        'SELECT id, nome FROM petmed_pets WHERE user_id=?', (u['id'],)
    ).fetchall()
    conn.close()
    return render_template('petmed/historico.html',
                           u=u, triagens=triagens, pets=pets,
                           pet_id_sel=pet_id, categorias=CATEGORIAS)


@petmed_bp.route('/historico/<int:tid>')
@petmed_login_required
def triagem_detalhe(tid):
    u = _get_user()
    conn = get_petmed_db()
    t = conn.execute(
        'SELECT * FROM petmed_triagens WHERE id=? AND user_id=?', (tid, u['id'])
    ).fetchone()
    conn.close()
    if not t:
        abort(404)
    historico_msgs = []
    try:
        historico_msgs = json.loads(t['perguntas_json'])
    except Exception:
        pass
    return render_template('petmed/triagem_detalhe.html',
                           u=u, t=t, historico=historico_msgs,
                           categorias=CATEGORIAS)


# ── Vacinas ────────────────────────────────────────────────────────────────────

@petmed_bp.route('/vacinas')
@petmed_login_required
def vacinas():
    u = _get_user()
    pets = _get_pets(u['id'])
    pet_id_sel = request.args.get('pet_id', '')
    conn = get_petmed_db()
    if pet_id_sel:
        vacinas_list = conn.execute(
            'SELECT v.*, p.nome as pet_nome FROM petmed_vacinas v '
            'JOIN petmed_pets p ON v.pet_id=p.id '
            'WHERE v.user_id=? AND v.pet_id=? ORDER BY v.proxima',
            (u['id'], pet_id_sel)
        ).fetchall()
    else:
        vacinas_list = conn.execute(
            'SELECT v.*, p.nome as pet_nome FROM petmed_vacinas v '
            'JOIN petmed_pets p ON v.pet_id=p.id '
            'WHERE v.user_id=? ORDER BY v.proxima',
            (u['id'],)
        ).fetchall()
    conn.close()
    msg = request.args.get('msg', '')
    return render_template('petmed/vacinas.html',
                           u=u, pets=pets, vacinas=vacinas_list,
                           pet_id_sel=pet_id_sel, msg=msg)


@petmed_bp.route('/vacinas/adicionar', methods=['POST'])
@petmed_login_required
def adicionar_vacina():
    u = _get_user()
    pet_id     = request.form.get('pet_id')
    nome       = request.form.get('nome', '').strip()
    data_aplic = request.form.get('data_aplic', '')
    proxima    = request.form.get('proxima', '')
    if pet_id and nome:
        conn = get_petmed_db()
        conn.execute(
            'INSERT INTO petmed_vacinas (pet_id, user_id, nome, data_aplic, proxima) VALUES (?,?,?,?,?)',
            (pet_id, u['id'], nome, data_aplic, proxima)
        )
        conn.commit()
        conn.close()
    return redirect(f'/vetzap/vacinas?msg=vacina_adicionada&pet_id={pet_id or ""}')


# ── Teleconsulta (Premium) ─────────────────────────────────────────────────────

@petmed_bp.route('/teleconsulta')
@petmed_premium_required
def teleconsulta():
    u = _get_user()
    pets = _get_pets(u['id'])
    conn = get_petmed_db()
    vets = conn.execute(
        'SELECT * FROM petmed_vets WHERE ativo=1 AND disponivel=1 ORDER BY avaliacao DESC'
    ).fetchall()
    minhas = conn.execute(
        '''SELECT tc.*, v.nome as vet_nome, p.nome as pet_nome
           FROM petmed_teleconsultas tc
           LEFT JOIN petmed_vets v ON tc.vet_id=v.id
           LEFT JOIN petmed_pets p ON tc.pet_id=p.id
           WHERE tc.user_id=? ORDER BY tc.created_at DESC LIMIT 10''',
        (u['id'],)
    ).fetchall()
    conn.close()
    return render_template('petmed/teleconsulta.html',
                           u=u, pets=pets, vets=vets, minhas=minhas)


# ── API: contagem para badge ───────────────────────────────────────────────────

@petmed_bp.route('/api/status')
@petmed_login_required
def api_status():
    u = _get_user()
    conn = get_petmed_db()
    triagens_hoje = conn.execute(
        '''SELECT COUNT(*) FROM petmed_triagens
           WHERE user_id=? AND date(created_at)=date("now","localtime")''',
        (u['id'],)
    ).fetchone()[0]
    urgentes = conn.execute(
        '''SELECT COUNT(*) FROM petmed_triagens
           WHERE user_id=? AND resultado="urgente"
           AND date(created_at)=date("now","localtime")''',
        (u['id'],)
    ).fetchone()[0]
    vacinas_proximas = conn.execute(
        '''SELECT COUNT(*) FROM petmed_vacinas v
           JOIN petmed_pets p ON v.pet_id=p.id
           WHERE p.user_id=? AND v.proxima BETWEEN date("now") AND date("now","+30 days")''',
        (u['id'],)
    ).fetchone()[0]
    conn.close()
    return jsonify({
        'triagens_hoje': triagens_hoje,
        'urgentes': urgentes,
        'vacinas_proximas': vacinas_proximas,
        'plano': u['plano']
    })


# ── Área veterinário parceiro ──────────────────────────────────────────────────

@petmed_bp.route('/vet/cadastro', methods=['GET', 'POST'])
def vet_cadastro():
    erro = ''
    if request.method == 'POST':
        nome        = request.form.get('nome', '').strip()
        email       = request.form.get('email', '').strip().lower()
        telefone    = request.form.get('telefone', '').strip()
        crmv        = request.form.get('crmv', '').strip()
        estado_crmv = request.form.get('estado_crmv', 'SC')
        especialidade = request.form.get('especialidade', '').strip()
        bio         = request.form.get('bio', '').strip()
        senha       = request.form.get('senha', '')

        if not all([nome, email, telefone, crmv, senha]):
            erro = 'Preencha todos os campos obrigatórios.'
        elif len(senha) < 6:
            erro = 'Senha deve ter pelo menos 6 caracteres.'
        else:
            try:
                conn = get_petmed_db()
                conn.execute(
                    '''INSERT INTO petmed_vets
                       (nome, email, telefone, crmv, estado_crmv,
                        especialidade, bio, password_hash, ativo)
                       VALUES (?,?,?,?,?,?,?,?,0)''',
                    (nome, email, telefone, crmv, estado_crmv,
                     especialidade, bio, generate_password_hash(senha))
                )
                conn.commit()
                conn.close()
                return redirect('/vetzap/vet/cadastro?ok=1')
            except Exception as ex:
                if 'UNIQUE' in str(ex):
                    erro = 'Este e-mail já está cadastrado.'
                else:
                    erro = 'Erro ao cadastrar. Tente novamente.'
    ok = request.args.get('ok', '')
    return render_template('petmed/vet_cadastro.html', erro=erro, ok=ok)
