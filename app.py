"""
app.py — 4KITEM Plataforma de Soluções Digitais
"""
import csv
import io
import json as _json
import logging
import os
import random
import re as _re
import threading
import time
import traceback
import unicodedata
import uuid
import requests
from datetime import datetime, timedelta, date
from functools import wraps
from flask import (Flask, render_template, redirect, jsonify,
                   request, abort, url_for, session)
from werkzeug.security import generate_password_hash, check_password_hash

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('4kitem')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', '4kitem-secret-2024-xk91')
app.config['TEMPLATES_AUTO_RELOAD'] = True  # templates sempre relidos do disco

# ── Sentry — monitoramento de erros em produção ────────────────────────────────
_SENTRY_DSN = os.environ.get('SENTRY_DSN', '')
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[
                FlaskIntegration(transaction_style='url'),
                LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
            ],
            traces_sample_rate=0.1,   # 10% das requests para performance tracing
            profiles_sample_rate=0.0,
            environment=os.environ.get('RAILWAY_ENVIRONMENT', 'production'),
            release=os.environ.get('RAILWAY_GIT_COMMIT_SHA', 'unknown')[:8],
            send_default_pii=False,   # não envia dados pessoais
            before_send=lambda event, hint: event,
        )
        log.info('[Sentry] Inicializado — monitoramento de erros ativo')
    except ImportError:
        log.warning('[Sentry] sentry-sdk não instalado — sem monitoramento')
    except Exception as _se:
        log.warning('[Sentry] Falha ao inicializar: %s', _se)
else:
    log.info('[Sentry] SENTRY_DSN não configurado — monitoramento desabilitado')

# ── SaaS admin password ────────────────────────────────────────────────────────
SAAS_ADMIN_PW = os.environ.get('SAAS_ADMIN_PASSWORD', 'admin4kitem2024')

# ── DEV_WHITELIST — nunca bloqueados pelo anti-golpe (re-cadastro livre) ───────
# Adicione telefones (apenas dígitos) ou e-mails separados por vírgula na env:
#   DEV_WHITELIST=47997766831,diogolessmann@gmail.com
_wl_raw = os.environ.get('DEV_WHITELIST', '47997766831,diogolessmann@gmail.com')
DEV_WHITELIST: set = {x.strip().lower() for x in _wl_raw.split(',') if x.strip()}

def _is_whitelisted(*values) -> bool:
    """Retorna True se qualquer valor (email ou dígitos de telefone) estiver no DEV_WHITELIST."""
    for v in values:
        if v and str(v).strip().lower() in DEV_WHITELIST:
            return True
    return False

# ── AgendaSC constants ────────────────────────────────────────────────────────
BUSINESS_TYPES = {
    'barbearia':    '💈 Barbearia',
    'salao':        '💇 Salão de Beleza',
    'estetica':     '💅 Estética / Spa',
    'clinica':      '🏥 Clínica Médica',
    'dentista':     '🦷 Dentista / Ortodontia',
    'psicologia':   '🧠 Psicologia / Terapia',
    'nutricao':     '🥗 Nutricionista',
    'fisioterapia': '🦵 Fisioterapia',
    'pet':          '🐾 Pet Shop / Veterinário',
    'academia':     '💪 Academia / Personal Trainer',
    'mecanica':     '🔧 Mecânica / Oficina',
    'advocacia':    '⚖️ Advocacia / Contabilidade',
    'consultoria':  '📊 Consultoria / Coaching',
    'fotografia':   '📷 Fotografia / Estúdio',
    'tatuagem':     '🖊️ Tatuagem / Piercing',
    'lavacao':      '🚗 Lavação / Estética Automotiva',
    'escola':       '🎓 Escola / Curso / Idiomas',
    'imobiliaria':  '🏠 Imobiliária / Corretor',
    'outros':       '🏢 Outro',
}

WEEKDAY_NAMES = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']

# ── AlertaSC constants ────────────────────────────────────────────────────────
ALERTA_PLANS = {
    'basico':        {'label': '👤 Individual',     'price': 'R$ 19,90', 'preco': 19.90,  'vehicles': 1},
    'familia':       {'label': '👨‍👩‍👧 Família',      'price': 'R$ 39,00', 'preco': 39.00,  'vehicles': 4},
    'pequena_frota': {'label': '🚐 Pequena Frota',  'price': 'R$ 99,00', 'preco': 99.00,  'vehicles': 9},
    'frota_media':   {'label': '🚛 Frota Média',    'price': 'R$149,00', 'preco': 149.00, 'vehicles': 20},
    'master':        {'label': '🏢 Master',         'price': 'R$229,00', 'preco': 229.00, 'vehicles': 50},
    'enterprise':    {'label': '🏭 Enterprise',     'price': 'R$399,00', 'preco': 399.00, 'vehicles': 100},
}


# ── DefesaPro — planos e preços ───────────────────────────────────────────────
DEFESAPRO_PLANOS = {
    'starter':      {'nome': 'Starter',      'preco': 390.00, 'preco_fmt': 'R$ 390',  'emoji': '⚖️'},
    'profissional': {'nome': 'Profissional', 'preco': 590.00, 'preco_fmt': 'R$ 590',  'emoji': '🏛️'},
    'premium':      {'nome': 'Premium',      'preco': 990.00, 'preco_fmt': 'R$ 990',  'emoji': '👑'},
}

# ── Helpers globais: e-mail (Resend) + Asaas ──────────────────────────────────
_ASAAS_BASE = 'https://api.asaas.com/v3'

def _enviar_email(para: str, assunto: str, html: str,
                  anexo_nome: str = None, anexo_bytes: bytes = None) -> bool:
    import base64
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        return False
    from_addr = os.environ.get('EMAIL_FROM', 'VetZap <onboarding@resend.dev>')
    payload: dict = {'from': from_addr, 'to': [para], 'subject': assunto, 'html': html}
    if anexo_nome and anexo_bytes:
        payload['attachments'] = [{'filename': anexo_nome,
                                   'content': base64.b64encode(anexo_bytes).decode()}]
    try:
        r = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=20
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


def _desp_backup_dest() -> str:
    """Retorna o email de destino do backup para o usuário atual."""
    if session.get('desp_saas_user_id'):
        from desp_db import get_config as _gc
        return _gc('desp_backup_email') or ''
    return os.environ.get('BACKUP_EMAIL', 'diogolessmann@gmail.com')


def _gerar_backup_zip(db_path: str = None) -> bytes:
    """Gera o ZIP de backup e retorna os bytes. db_path opcional para tenants."""
    import zipfile, sqlite3 as _sq3
    if db_path:
        conn = _sq3.connect(db_path)
        conn.row_factory = _sq3.Row
    else:
        conn = get_desp_conn()
    buf = io.BytesIO()
    tabelas = ['clientes', 'veiculos', 'ordens_servico', 'os_parcelas',
               'os_historico', 'debitos_veiculo', 'config', 'protocolos_renavam',
               'documentos']
    try:
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for tbl in tabelas:
                try:
                    rows = conn.execute(f'SELECT * FROM {tbl}').fetchall()
                    if not rows:
                        continue
                    cols = rows[0].keys()
                    sb   = io.StringIO()
                    w    = csv.writer(sb)
                    w.writerow(list(cols))
                    for r in rows:
                        w.writerow([r[c] for c in cols])
                    zf.writestr(f'{tbl}.csv', sb.getvalue())
                except Exception:
                    pass
            n_os  = conn.execute('SELECT COUNT(*) FROM ordens_servico').fetchone()[0]
            n_cli = conn.execute('SELECT COUNT(*) FROM clientes').fetchone()[0]
            meta  = f'Backup Despachante\nData: {date.today()}\nOS: {n_os}\nClientes: {n_cli}\n'
            zf.writestr('_info.txt', meta)
    finally:
        conn.close()
    buf.seek(0)
    return buf.read()


def _enviar_backup_email(dest: str = None, db_path: str = None):
    """Gera ZIP e envia por e-mail. dest e db_path opcionais para tenants."""
    if not dest:
        dest = os.environ.get('BACKUP_EMAIL', 'diogolessmann@gmail.com')
    if not dest:
        log.warning('[Backup] Sem email de destino configurado — backup não enviado')
        return False
    try:
        zdata = _gerar_backup_zip(db_path=db_path)
        nome  = (db_path or 'lessmann').split('/')[-1].replace('.db','')
        fname = f'{nome}_backup_{date.today()}.zip'
        ok = _enviar_email(
            para=dest,
            assunto=f'📦 Backup Despachante — {date.today()}',
            html=(f'<p>Backup automático gerado em <strong>{datetime.now().strftime("%d/%m/%Y %H:%M")}</strong>.</p>'
                  f'<p>Arquivo: <code>{fname}</code></p>'
                  f'<p><em>Amigo Despachante — Sistema Automático</em></p>'),
            anexo_nome=fname,
            anexo_bytes=zdata,
        )
        log.info(f'[Backup] Email {"enviado" if ok else "FALHOU"} → {dest}')
        return ok
    except Exception as e:
        log.error(f'[Backup] Erro ao gerar/enviar backup: {e}')
        return False


def _backup_scheduler():
    """Thread que dispara backup diário às 7h (horário do servidor / Sao Paulo)."""
    log.info('[Backup] Agendador iniciado — backup diário às 07:00')
    while True:
        now      = datetime.now()
        proximo  = now.replace(hour=7, minute=0, second=0, microsecond=0)
        if proximo <= now:
            proximo += timedelta(days=1)
        espera = (proximo - now).total_seconds()
        log.info(f'[Backup] Próximo backup em {espera/3600:.1f}h ({proximo.strftime("%d/%m %H:%M")})')
        time.sleep(espera)
        _enviar_backup_email()


threading.Thread(target=_backup_scheduler, daemon=True, name='backup-scheduler').start()

def _asaas_req(method: str, endpoint: str, data: dict = None):
    api_key = os.environ.get('ASAAS_API_KEY', '')
    if not api_key:
        return {'error': 'no_key'}
    try:
        r = requests.request(
            method, f'{_ASAAS_BASE}{endpoint}',
            headers={'access_token': api_key, 'Content-Type': 'application/json'},
            json=data, timeout=15
        )
        return r.json()
    except Exception as e:
        return {'error': str(e)}

def _asaas_criar_ou_buscar_cliente_saas(nome, email, telefone, cpf, tabela_id, tabela):
    """Cria ou busca cliente no Asaas para apps do saas.db."""
    import re as _re_asaas
    api_key = os.environ.get('ASAAS_API_KEY', '')
    if not api_key:
        log.error('[Asaas] ASAAS_API_KEY não configurada nas variáveis de ambiente!')
        return None
    cpf_limpo = ''.join(c for c in (cpf or '') if c.isdigit())
    log.info('[Asaas] Iniciando busca/criação de cliente: nome=%s email=%s cpf_len=%d tabela=%s',
             nome, email, len(cpf_limpo), tabela)
    # 1. Busca por CPF/CNPJ se disponível
    if cpf_limpo and len(cpf_limpo) in (11, 14):
        busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf_limpo}')
        log.info('[Asaas] Busca por CPF: %s', busca)
        if busca.get('data'):
            cid = busca['data'][0]['id']
            log.info('[Asaas] Cliente encontrado por CPF: %s', cid)
            return cid
    # 2. Busca por e-mail como fallback
    if email:
        busca_email = _asaas_req('GET', f'/customers?email={email}')
        log.info('[Asaas] Busca por email: %s', busca_email)
        if busca_email.get('data'):
            cid = busca_email['data'][0]['id']
            log.info('[Asaas] Cliente encontrado por e-mail: %s', cid)
            return cid
    # 3. Tenta criar o cliente
    fone_limpo = ''.join(c for c in (telefone or '') if c.isdigit())
    payload = {
        'name': nome or 'Cliente',
        'email': email or '',
        'mobilePhone': fone_limpo,
        'notificationDisabled': True,
    }
    if cpf_limpo and len(cpf_limpo) in (11, 14):
        payload['cpfCnpj'] = cpf_limpo
    log.info('[Asaas] Criando cliente: payload=%s', payload)
    resp = _asaas_req('POST', '/customers', payload)
    log.info('[Asaas] Resposta criação: %s', resp)
    if resp.get('id'):
        log.info('[Asaas] Cliente criado: %s', resp['id'])
        return resp['id']
    # 4. Se já existe, extrai o ID do erro (Asaas retorna cus_XXXX na mensagem)
    erros = resp.get('errors', [])
    for err in erros:
        desc = err.get('description', '')
        log.info('[Asaas] Erro na criação: %s', desc)
        match = _re_asaas.search(r'cus_\w+', desc)
        if match:
            cid = match.group(0)
            log.info('[Asaas] ID extraído do erro: %s', cid)
            return cid
    log.error('[Asaas] Falha total na criação do cliente. Resposta: %s', resp)
    return None

def _asaas_criar_assinatura_saas(customer_id, app_prefix, plano_key, valor, descricao, billing_type='PIX', cycle='MONTHLY'):
    import datetime as _dt
    prox = (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d')
    return _asaas_req('POST', '/subscriptions', {
        'customer': customer_id, 'billingType': billing_type,
        'value': valor, 'nextDueDate': prox,
        'cycle': cycle, 'description': descricao,
        'externalReference': f'{app_prefix}_{customer_id}_{plano_key}',
    })

def _asaas_get_pix_qr(subscription_id: str) -> dict:
    """Busca QR Code PIX do primeiro pagamento de uma assinatura Asaas."""
    try:
        payments = _asaas_req('GET', f'/subscriptions/{subscription_id}/payments?limit=1')
        if not payments.get('data'):
            return {}
        payment_id = payments['data'][0].get('id', '')
        if not payment_id:
            return {}
        qr = _asaas_req('GET', f'/payments/{payment_id}/pixQrCode')
        return {
            'encodedImage': qr.get('encodedImage', ''),
            'payload': qr.get('payload', ''),
            'payment_id': payment_id,
        }
    except Exception as e:
        log.error('[Asaas PIX QR] Erro: %s', e)
        return {}

# ── Email helpers ─────────────────────────────────────────────────────────────
def _email_base(conteudo: str, cor: str = '#22c55e') -> str:
    """Wrapper HTML base para todos os emails transacionais."""
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 0">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#111;border:1px solid #222;border-radius:16px;overflow:hidden">
<tr><td style="background:{cor};height:4px"></td></tr>
<tr><td style="padding:36px 40px 32px">
{conteudo}
<hr style="border:none;border-top:1px solid #222;margin:28px 0">
<p style="font-size:11px;color:#555;margin:0;line-height:1.6">
4KITEM · Soluções Digitais · <a href="https://4kitem.com.br" style="color:{cor}">4kitem.com.br</a><br>
Dúvidas? WhatsApp: <a href="https://wa.me/5547999606998" style="color:{cor}">(47) 99960-6998</a>
</p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def _email_boas_vindas(app_nome: str, emoji: str, cor: str, primeiro_nome: str,
                       trial_ate: str, link_painel: str, descricao: str) -> str:
    """Email HTML de boas-vindas no trial."""
    trial_fmt = trial_ate[:10] if trial_ate else ''
    conteudo = f"""
<div style="font-size:40px;margin-bottom:12px">{emoji}</div>
<h1 style="color:#fff;font-size:22px;font-weight:800;margin:0 0 8px">Bem-vindo ao {app_nome}, {primeiro_nome}!</h1>
<p style="color:#888;font-size:14px;line-height:1.7;margin:0 0 24px">{descricao}</p>

<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:20px;margin-bottom:24px">
  <div style="font-size:12px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">Seu período de teste</div>
  <div style="font-size:28px;font-weight:900;color:{cor};margin-bottom:4px">7 dias grátis</div>
  {'<div style="font-size:13px;color:#666">Trial gratuito até <strong style="color:#fff">' + trial_fmt + '</strong>. Sem cartão de crédito necessário agora.</div>' if trial_fmt else ''}
</div>

<a href="{link_painel}" style="display:block;text-align:center;padding:14px 28px;background:{cor};color:#fff;font-size:15px;font-weight:700;border-radius:12px;text-decoration:none;margin-bottom:20px">
  Acessar meu painel →
</a>

<p style="font-size:13px;color:#666;margin:0">
  Precisar de ajuda? Nossa equipe está no WhatsApp <a href="https://wa.me/5547999606998" style="color:{cor}">(47) 99960-6998</a>.
</p>"""
    return _email_base(conteudo, cor)


def _email_pagamento_confirmado(app_nome: str, emoji: str, cor: str, primeiro_nome: str,
                                 plano: str, valor: str, link_painel: str) -> str:
    """Email HTML de confirmação de pagamento / assinatura ativa."""
    conteudo = f"""
<div style="font-size:40px;margin-bottom:12px">✅</div>
<h1 style="color:#fff;font-size:22px;font-weight:800;margin:0 0 8px">Pagamento confirmado!</h1>
<p style="color:#888;font-size:14px;line-height:1.7;margin:0 0 24px">
  Sua assinatura do <strong style="color:#fff">{app_nome}</strong> está ativa, {primeiro_nome}.
</p>

<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:20px;margin-bottom:24px">
  <div style="font-size:12px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">Detalhes da assinatura</div>
  <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
    <span style="font-size:13px;color:#666">Produto</span>
    <span style="font-size:13px;color:#fff;font-weight:700">{emoji} {app_nome}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
    <span style="font-size:13px;color:#666">Plano</span>
    <span style="font-size:13px;color:#fff;font-weight:700">{plano}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:8px 0">
    <span style="font-size:13px;color:#666">Valor mensal</span>
    <span style="font-size:13px;color:{cor};font-weight:700">{valor}</span>
  </div>
</div>

<a href="{link_painel}" style="display:block;text-align:center;padding:14px 28px;background:{cor};color:#fff;font-size:15px;font-weight:700;border-radius:12px;text-decoration:none;margin-bottom:20px">
  {emoji} Ir para o painel
</a>

<p style="font-size:13px;color:#666;margin:0">
  Sua renovação é automática todo mês. Cancele quando quiser pelo WhatsApp <a href="https://wa.me/5547999606998" style="color:{cor}">(47) 99960-6998</a>.
</p>"""
    return _email_base(conteudo, cor)

# ── SaaS helpers ──────────────────────────────────────────────────────────────
def _slugify(text):
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')
    text = text.lower().strip()
    text = _re.sub(r'[^\w\s-]', '', text)
    text = _re.sub(r'[\s_-]+', '-', text)
    return text[:50]


def _agenda_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('agenda_business_id'):
            return redirect('/agenda/entrar')
        return f(*args, **kwargs)
    return decorated


def _saas_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('saas_admin'):
            return redirect('/saas-admin/login')
        return f(*args, **kwargs)
    return decorated


def _mandazap_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        uid = session.get('mz_user_id')
        if not uid:
            return redirect('/mandazap/entrar')
        # Atualiza plano na sessão sempre (evita sessão com plano desatualizado)
        conn = get_saas_db()
        user = conn.execute(
            'SELECT plan, active, trial_ends FROM mandazap_users WHERE id=?', (uid,)
        ).fetchone()
        conn.close()
        if not user or not user['active']:
            for k in ('mz_user_id', 'mz_user_name', 'mz_plan'):
                session.pop(k, None)
            return redirect('/mandazap/entrar?msg=conta_inativa')
        # Verifica trial expirado (só bloqueia se plan == 'solo' sem pagamento)
        trial_ends = user['trial_ends']
        if trial_ends and trial_ends < datetime.now().isoformat() and user['plan'] == 'solo':
            # Conta quantos já enviou — se zero, provavelmente trial real
            conn2 = get_saas_db()
            total_sent = conn2.execute(
                'SELECT COALESCE(SUM(sent),0) FROM mandazap_campaigns WHERE user_id=?', (uid,)
            ).fetchone()[0]
            conn2.close()
            if total_sent == 0:  # nunca usou de verdade
                for k in ('mz_user_id', 'mz_user_name', 'mz_plan'):
                    session.pop(k, None)
                return redirect('/mandazap/entrar?msg=trial_expirado')
        # Sincroniza plano na sessão
        session['mz_plan'] = user['plan']
        return f(*args, **kwargs)
    return decorated


def _bau_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('bau_user_id'):
            return redirect('/bau/entrar')
        return f(*args, **kwargs)
    return decorated


MANDAZAP_PLANS = {
    'solo':      {'label': 'Solo',      'numbers': 1,  'daily_limit': 399,   'contacts_limit': 500,   'price': 79},
    'duplo':     {'label': 'Duplo',     'numbers': 2,  'daily_limit': 799,   'contacts_limit': 2000,  'price': 149},
    'trio':      {'label': 'Trio',      'numbers': 3,  'daily_limit': 1199,  'contacts_limit': 5000,  'price': 219},
    'quadruplo': {'label': 'Quádruplo', 'numbers': 4,  'daily_limit': 1599,  'contacts_limit': 10000, 'price': 289},
    'agencia':   {'label': 'Agência',   'numbers': 10, 'daily_limit': 99999, 'contacts_limit': 99999, 'price': 499},
}

# ── MandaJá — Planos ─────────────────────────────────────────────────────────
MANDAJA_PLANS = {
    'micro':    {'label': 'Micro',    'products': 5,   'price': 59,  'emoji': '🌱'},
    'light':    {'label': 'Light',    'products': 10,  'price': 99,  'emoji': '⚡'},
    'plus':     {'label': 'Plus',     'products': 20,  'price': 159, 'emoji': '🚀'},
    'pro':      {'label': 'Pro',      'products': 40,  'price': 249, 'emoji': '💎'},
    'king':     {'label': 'King',     'products': 100, 'price': 349, 'emoji': '👑'},
    'ultra':    {'label': 'Ultra',    'products': 200, 'price': 499, 'emoji': '🔥'},
}

MANDAJA_STORE_CATEGORIES = {
    'restaurante':  '🍽️ Restaurante',
    'lanchonete':   '🍔 Lanchonete / Hambúrguer',
    'pizza':        '🍕 Pizzaria',
    'sushi':        '🍣 Japonês / Sushi',
    'acai':         '🍇 Açaí / Sorvete',
    'pastelaria':   '🥟 Pastelaria',
    'mercado':      '🛒 Mercado / Mercearia',
    'farmacia':     '💊 Farmácia',
    'padaria':      '🥖 Padaria / Confeitaria',
    'bebidas':      '🍺 Bebidas / Adega',
    'pet':          '🐾 Pet Shop',
    'flores':       '💐 Flores / Presentes',
    'roupas':       '👕 Roupas / Acessórios',
    'eletronicos':  '📱 Eletrônicos',
    'outros':       '📦 Outros',
}

MANDAJA_WEEKDAYS = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']


def _mandaja_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('mja_store_id'):
            return redirect('/mandaja/entrar')
        return f(*args, **kwargs)
    return decorated


def _mandaja_get_store():
    """Retorna a loja logada."""
    conn = get_saas_db()
    s = conn.execute('SELECT * FROM mandaja_stores WHERE id=?',
                     (session.get('mja_store_id'),)).fetchone()
    conn.close()
    return dict(s) if s else None


def _mandaja_next_order_number(store_id):
    conn = get_saas_db()
    count = conn.execute(
        'SELECT COUNT(*) FROM mandaja_orders WHERE store_id=?', (store_id,)
    ).fetchone()[0]
    conn.close()
    return f"#{count + 1:04d}"

BAU_CATEGORIES = {
    'trabalho': {'label': 'Trabalho',       'icon': '💼'},
    'banco':    {'label': 'Bancos / Finance','icon': '🏦'},
    'social':   {'label': 'Redes Sociais',  'icon': '📱'},
    'pessoal':  {'label': 'Pessoal',        'icon': '👤'},
    'jogos':    {'label': 'Jogos',          'icon': '🎮'},
    'email':    {'label': 'E-mail',         'icon': '📧'},
    'compras':  {'label': 'Compras',        'icon': '🛒'},
    'outros':   {'label': 'Outros',         'icon': '🔧'},
}

BAU_PLANS = {
    'mensal': {
        'label': 'Baú Mensal', 'price': 'R$ 19,90/mês',
        'preco': 19.90, 'cycle': 'MONTHLY',
        'entradas': 'Ilimitadas',
        'features': ['Entradas ilimitadas', 'Categorias', 'Busca rápida', 'Acesso em qualquer dispositivo'],
    },
    'anual': {
        'label': 'Baú Anual', 'price': 'R$ 14,90/mês (R$ 178,80/ano)',
        'preco': 178.80, 'cycle': 'YEARLY',
        'entradas': 'Ilimitadas',
        'features': ['Tudo do Mensal', '25% de desconto', 'Suporte prioritário'],
    },
}

KIDS_PLANS = {
    'mensal': {
        'label': 'KidsCurator Mensal', 'price': 'R$ 49,90/mês',
        'preco': 49.90, 'cycle': 'MONTHLY',
        'features': ['6 categorias de conteúdo', '1 código de acesso', 'Atualização automática de conteúdo', 'Suporte via WhatsApp'],
    },
    'anual': {
        'label': 'KidsCurator Anual', 'price': 'R$ 39,90/mês (R$ 478,80/ano)',
        'preco': 478.80, 'cycle': 'YEARLY',
        'features': ['Tudo do Mensal', '20% de desconto', '2 códigos de acesso'],
    },
}


def _get_slots(business_id, date_str, service_duration):
    """Gera horários disponíveis para uma data e duração de serviço."""
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        weekday = dt.weekday()
    except Exception:
        return []

    conn = get_saas_db()
    avail = conn.execute(
        'SELECT start_time, end_time FROM agenda_availability WHERE business_id=? AND weekday=? AND active=1',
        (business_id, weekday)
    ).fetchone()
    if not avail:
        conn.close()
        return []

    booked = conn.execute('''
        SELECT a.appointment_time, COALESCE(s.duration_minutes, 60) as duration_minutes
        FROM agenda_appointments a
        LEFT JOIN agenda_services s ON a.service_id = s.id
        WHERE a.business_id=? AND a.appointment_date=? AND a.status != 'cancelled'
    ''', (business_id, date_str)).fetchall()
    conn.close()

    slots = []
    start   = datetime.strptime(avail['start_time'], '%H:%M')
    end     = datetime.strptime(avail['end_time'],   '%H:%M')
    now     = datetime.now()
    current = start

    while current + timedelta(minutes=service_duration) <= end:
        slot_str = current.strftime('%H:%M')
        if dt.date() == now.date() and current.replace(year=now.year, month=now.month, day=now.day) <= now:
            current += timedelta(minutes=30)
            continue
        conflict = False
        s_end = current + timedelta(minutes=service_duration)
        for b in booked:
            b_start = datetime.strptime(b['appointment_time'], '%H:%M')
            b_end   = b_start + timedelta(minutes=b['duration_minutes'])
            if not (s_end <= b_start or current >= b_end):
                conflict = True
                break
        if not conflict:
            slots.append(slot_str)
        current += timedelta(minutes=30)

    return slots

from kids_db import (
    init_db, get_videos, get_channels, total_videos, stats,
    get_videos_for_mode, get_client, set_client_mode,
    create_client, mark_video_blocked, MODES,
    get_conn as get_kids_conn,
)
from saas_db import init_saas_db, get_db as get_saas_db, salvar_nota_dev, listar_notas_dev

# ══════════════════════════════════════════════════════════════════════════
#  LANDINGS
# ══════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')


# ══════════════════════════════════════════════════════════════════════════
#  KIDSCURATOR — Login por código
# ══════════════════════════════════════════════════════════════════════════

@app.route('/kids/entrar', methods=['GET', 'POST'])
def kids_entrar():
    erro = None
    if request.method == 'POST':
        code = (request.form.get('code') or '').strip().upper()
        client = get_client(code) if code else None
        if not client:
            erro = 'Código não encontrado ou inativo. Verifique o código enviado pelo suporte.'
        else:
            session['kids_code'] = code
            return redirect(f'/painel/{code}')
    return render_template('kids/entrar.html', erro=erro)


@app.route('/kids/sair')
def kids_sair():
    session.pop('kids_code', None)
    return redirect('/kids/entrar')


@app.route('/kids/assinar/<plano>', methods=['GET', 'POST'])
def kids_assinar(plano):
    if plano not in KIDS_PLANS:
        return redirect('/kids')
    p = KIDS_PLANS[plano]
    erro = None
    if request.method == 'POST':
        nome      = request.form.get('name', '').strip()
        empresa   = request.form.get('empresa', '').strip()
        email     = request.form.get('email', '').strip().lower()
        phone     = request.form.get('phone', '').strip()
        cpf_cnpj  = request.form.get('cpf_cnpj', '').strip()
        billing_type = request.form.get('billing_type', 'PIX').upper()
        cpf_digits = ''.join(c for c in cpf_cnpj if c.isdigit())
        if not all([nome, empresa, email, phone, cpf_cnpj]):
            erro = 'Preencha todos os campos obrigatórios.'
        elif len(cpf_digits) not in (11, 14):
            erro = 'CPF deve ter 11 dígitos ou CNPJ 14 dígitos.'
        elif billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            billing_type = 'PIX'
        else:
            try:
                import secrets as _sec
                kconn = get_kids_conn()
                # Verifica e-mail duplicado
                ex = kconn.execute('SELECT id FROM clients WHERE email=?', (email,)).fetchone()
                if ex:
                    erro = 'E-mail já cadastrado. Entre em contato pelo WhatsApp.'
                    kconn.close()
                else:
                    # Gera código único
                    while True:
                        code = ''.join(_sec.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
                        if not kconn.execute('SELECT id FROM clients WHERE code=?', (code,)).fetchone():
                            break
                    now = datetime.now().isoformat()
                    kconn.execute('''INSERT INTO clients
                        (code, name, email, phone, cpf_cnpj, plan, plan_active, active, created_at, city)
                        VALUES (?,?,?,?,?,?,0,0,?,?)''',
                        (code, empresa, email, phone, cpf_cnpj, plano, now, 'SC'))
                    kconn.commit()
                    client = kconn.execute('SELECT * FROM clients WHERE code=?', (code,)).fetchone()
                    kconn.close()
                    # Cria/busca cliente no Asaas
                    customer_id = _asaas_criar_ou_buscar_cliente_saas(
                        nome, email, phone, cpf_cnpj, client['id'], 'kids_clients_placeholder'
                    )
                    if not customer_id:
                        erro = ('Não conseguimos processar o pagamento agora. '
                                'Entre em contato pelo WhatsApp (47) 99960-6998. 💬')
                        # Remove o cliente criado
                        kconn2 = get_kids_conn()
                        kconn2.execute('DELETE FROM clients WHERE code=?', (code,))
                        kconn2.commit(); kconn2.close()
                    else:
                        kconn3 = get_kids_conn()
                        kconn3.execute('UPDATE clients SET asaas_customer_id=? WHERE code=?',
                                       (customer_id, code))
                        kconn3.commit(); kconn3.close()
                        resp = _asaas_criar_assinatura_saas(
                            customer_id, 'kids', plano, p['preco'],
                            f"KidsCurator {p['label']} — {empresa}",
                            billing_type, p.get('cycle', 'MONTHLY')
                        )
                        if resp.get('id'):
                            session['kids_pending_code'] = code
                            session['kids_pending_email'] = email
                            invoice_url = resp.get('invoiceUrl') or resp.get('bankSlipUrl') or ''
                            if invoice_url:
                                return redirect(invoice_url)
                            return redirect('/kids/aguardando-pagamento')
                        else:
                            erro = 'Não foi possível gerar o pagamento. Tente novamente.'
                            kconn4 = get_kids_conn()
                            kconn4.execute('DELETE FROM clients WHERE code=?', (code,))
                            kconn4.commit(); kconn4.close()
            except Exception:
                log.exception('[Kids] Erro no checkout')
                erro = 'Erro ao processar. Tente novamente ou entre em contato.'
    return render_template('kids/checkout.html', plano=p, plano_key=plano, erro=erro)


@app.route('/kids/aguardando-pagamento')
def kids_aguardando():
    email = session.get('kids_pending_email', '')
    return render_template('kids/aguardando.html', email=email)


# ══════════════════════════════════════════════════════════════════════════
#  ALERTA SC — Login do assinante
# ══════════════════════════════════════════════════════════════════════════

def _alerta_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('alerta_sub_id'):
            return redirect('/alerta/entrar')
        return f(*args, **kwargs)
    return decorated


@app.route('/alerta/entrar', methods=['GET', 'POST'])
def alerta_entrar():
    erro = None
    if request.method == 'POST':
        phone = (request.form.get('phone') or '').strip()
        phone_clean = phone.replace(' ','').replace('-','').replace('(','').replace(')','').replace('+','')
        conn = get_saas_db()
        sub = conn.execute(
            "SELECT * FROM alerta_subscribers WHERE REPLACE(REPLACE(REPLACE(REPLACE(phone,' ',''),'-',''),'(',''),')','') LIKE ? AND status != 'suspended'",
            (f'%{phone_clean[-8:]}',)
        ).fetchone()
        conn.close()
        if not sub:
            erro = 'Número não encontrado ou assinatura suspensa. Use o número que você cadastrou.'
        else:
            session['alerta_sub_id'] = sub['id']
            return redirect('/alerta/minha-conta')
    return render_template('alerta/entrar.html', erro=erro)


@app.route('/alerta/minha-conta')
@_alerta_login_required
def alerta_minha_conta():
    conn  = get_saas_db()
    sub   = conn.execute('SELECT * FROM alerta_subscribers WHERE id=?', (session['alerta_sub_id'],)).fetchone()
    conn.close()
    if not sub:
        session.pop('alerta_sub_id', None)
        return redirect('/alerta/entrar')
    sub   = dict(sub)
    try:
        sub['plates'] = _json.loads(sub.get('plates_json') or '[]')
    except Exception:
        sub['plates'] = []
    plan_info      = ALERTA_PLANS.get(sub.get('plano', 'basico'), {})
    trial_ends     = sub.get('trial_ends') or ''
    trial_expired  = bool(trial_ends and trial_ends < datetime.now().isoformat())
    pagamento_ok   = sub.get('status') == 'ativo'
    # Busca últimos débitos detectados (máx 20)
    conn2  = get_saas_db()
    debitos_raw = conn2.execute(
        "SELECT * FROM alerta_debitos WHERE subscriber_id=? ORDER BY found_at DESC LIMIT 20",
        (sub['id'],)
    ).fetchall()
    conn2.close()
    debitos = [dict(d) for d in debitos_raw]
    return render_template('alerta/minha_conta.html', sub=sub, plan_info=plan_info,
                           plans=ALERTA_PLANS, trial_ends=trial_ends,
                           trial_expired=trial_expired, pagamento_ok=pagamento_ok,
                           debitos=debitos)


@app.route('/alerta/sair')
def alerta_sair():
    session.pop('alerta_sub_id', None)
    return redirect('/alerta/entrar')


# ── AlertaSC — Checkout / Assinatura ─────────────────────────────────────────
@app.route('/alerta/assinar/<plano>', methods=['GET', 'POST'])
@_alerta_login_required
def alerta_assinar(plano):
    if plano not in ALERTA_PLANS:
        return redirect('/alerta/minha-conta')
    sub_id = session['alerta_sub_id']
    p = ALERTA_PLANS[plano]
    erro = None
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX').upper()
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            billing_type = 'PIX'
        conn = get_saas_db()
        sub = conn.execute('SELECT * FROM alerta_subscribers WHERE id=?', (sub_id,)).fetchone()
        conn.close()
        if not sub:
            return redirect('/alerta/entrar')
        customer_id = _asaas_criar_ou_buscar_cliente_saas(
            sub['name'], sub['email'] or '', sub['phone'], sub['cpf'], sub['id'], 'alerta_subscribers'
        )
        if not customer_id:
            erro = 'Erro ao processar pagamento. Tente novamente ou entre em contato.'
        else:
            conn2 = get_saas_db()
            conn2.execute('UPDATE alerta_subscribers SET asaas_customer_id=? WHERE id=?',
                          (customer_id, sub_id))
            conn2.commit(); conn2.close()
            resp = _asaas_criar_assinatura_saas(
                customer_id, 'alerta', plano, p['preco'],
                f'AlertaSC {p["label"]} — Assinatura Mensal',
                billing_type
            )
            if resp.get('id'):
                return redirect('/alerta/aguardando-pagamento')
            else:
                erro = f'Não foi possível gerar o pagamento. Tente novamente.'
    return render_template('alerta/checkout.html', plano=p, plano_key=plano, erro=erro)


@app.route('/alerta/aguardando-pagamento')
@_alerta_login_required
def alerta_aguardando():
    return render_template('alerta/aguardando.html')


# ══════════════════════════════════════════════════════════════════════════
#  AMIGO DESPACHANTE — Login do assinante
# ══════════════════════════════════════════════════════════════════════════

DESP_PLANS = {
    'basico':        {'label': '🥉 Básico',        'price': 'R$ 79,90/mês',  'preco': 79.90},
    'profissional':  {'label': '🥈 Profissional',   'price': 'R$149,90/mês', 'preco': 149.90},
    'premium':       {'label': '🥇 Premium',        'price': 'R$249,90/mês', 'preco': 249.90},
}

# Limites por plano — None = ilimitado
DESP_PLAN_LIMITS = {
    'basico':       {'os_mes': 50,   'clientes': 200,  'whatsapp': False},
    'profissional': {'os_mes': None, 'clientes': None,  'whatsapp': True},
    'premium':      {'os_mes': None, 'clientes': None,  'whatsapp': True},
}

AGENDA_PLAN = {'label': 'Agenda SC Pro', 'preco': 79.90, 'price': 'R$ 79,90/mês'}

# ── DefesaPro — CTB constants ─────────────────────────────────────────────────
CTB_ARTIGOS = {
    '162_i':   {'label': 'Art. 162, I — Conduzir sem CNH',              'pontos': 7, 'valor': 880.41},
    '165':     {'label': 'Art. 165 — Dirigir sob influência de álcool', 'pontos': 7, 'valor': 2934.70},
    '218_i':   {'label': 'Art. 218, I — Velocidade até 20% acima',     'pontos': 4, 'valor': 130.16},
    '218_ii':  {'label': 'Art. 218, II — Velocidade 20% a 50%',        'pontos': 5, 'valor': 195.23},
    '218_iii': {'label': 'Art. 218, III — Velocidade 50% a 100%',      'pontos': 6, 'valor': 293.47},
    '218_iv':  {'label': 'Art. 218, IV — Velocidade acima de 100%',    'pontos': 7, 'valor': 880.41},
    '230_i':   {'label': 'Art. 230, I — Sem documentação do veículo',  'pontos': 5, 'valor': 195.23},
    '244_i':   {'label': 'Art. 244, I — Motociclista sem capacete',    'pontos': 7, 'valor': 195.23},
    '167':     {'label': 'Art. 167 — Sem cinto de segurança',          'pontos': 5, 'valor': 293.47},
    '208':     {'label': 'Art. 208 — Avançar sinal vermelho',          'pontos': 7, 'valor': 293.47},
    '175':     {'label': 'Art. 175 — Disputar corrida (racha)',        'pontos': 7, 'valor': 880.41},
    '219':     {'label': 'Art. 219 — Transitar em calçada',            'pontos': 5, 'valor': 130.16},
    '228':     {'label': 'Art. 228 — Não dar passagem a pedestre',     'pontos': 5, 'valor': 130.16},
    '253':     {'label': 'Art. 253 — Parar em local proibido',         'pontos': 5, 'valor': 195.23},
    'outro':   {'label': 'Outro artigo (descrever nas obs.)',           'pontos': 0, 'valor': 0},
}
CTB_STATUS = {
    'aberto':     {'label': 'Aberto',       'color': '#3b82f6', 'emoji': '🔵'},
    'em_recurso': {'label': 'Em recurso',   'color': '#f59e0b', 'emoji': '🟡'},
    'deferido':   {'label': 'Deferido',     'color': '#22c55e', 'emoji': '✅'},
    'indeferido': {'label': 'Indeferido',   'color': '#ef4444', 'emoji': '❌'},
    'cancelado':  {'label': 'Cancelado',    'color': '#6b7280', 'emoji': '⚫'},
}
CTB_FASES = {
    'defesa_previa': '1ª Fase — Defesa Prévia',
    'recurso_jari':  '2ª Fase — Recurso JARI',
    'cetran':        '3ª Fase — CETRAN',
    'encerrado':     'Encerrado',
}
TESES_DEFESA = {
    'nulidade_notificacao': {
        'titulo': 'Nulidade — Falta de notificação válida',
        'texto': (
            'A autuação padece de nulidade, pois o autuado não foi devidamente notificado no '
            'prazo de 30 dias contados da data da infração, conforme exige o art. 281, §1º do CTB. '
            'A notificação é pressuposto de validade do auto de infração, e sua ausência ou '
            'intempestividade acarreta a caducidade do processo administrativo, nos termos da '
            'jurisprudência consolidada do Superior Tribunal de Justiça (REsp 1.115.932/RS).'
        ),
    },
    'ilegitimidade_passiva': {
        'titulo': 'Ilegitimidade passiva — Proprietário não era o condutor',
        'texto': (
            'A notificação foi dirigida ao proprietário do veículo, porém este não era o condutor '
            'no momento da infração, conforme faculta o art. 257, §7º do CTB. O proprietário tem '
            'o direito de indicar o condutor infrator, transferindo a responsabilidade pela penalidade. '
            'A não identificação do condutor pelo órgão autuador impede a imputação automática '
            'ao proprietário do veículo.'
        ),
    },
    'equipamento_nao_homologado': {
        'titulo': 'Nulidade — Equipamento sem homologação ou calibração válida',
        'texto': (
            'O auto de infração deve ser declarado nulo pela ausência de comprovação da homologação '
            'e da aferição periódica do equipamento utilizado, exigidas pelo art. 280, §2º do CTB '
            'c/c Resolução CONTRAN nº 396/2011 e normas INMETRO. A fé pública do auto não dispensa '
            'a apresentação dos certificados de calibração; o ônus da prova é do órgão autuador.'
        ),
    },
    'margem_erro': {
        'titulo': 'Velocidade efetiva dentro da margem de erro do equipamento',
        'texto': (
            'A velocidade registrada deve ser reduzida pela margem de erro do equipamento, nos '
            'termos da Portaria DENATRAN nº 12/2014 e normas INMETRO. Descontada a margem de erro '
            'legal, a velocidade efetiva do veículo fica dentro do limite permitido, tornando '
            'insubsistente a autuação. O princípio in dubio pro reo, aplicável ao processo '
            'administrativo sancionador, impõe o arquivamento do feito.'
        ),
    },
    'ausencia_sinalizacao': {
        'titulo': 'Ausência ou deficiência de sinalização na via',
        'texto': (
            'A sinalização no local da infração era inexistente ou não atendia aos requisitos '
            'mínimos de visibilidade previstos no Manual Brasileiro de Sinalização de Trânsito. '
            'Compete ao órgão gestor da via a correta sinalização (arts. 21, IV e 88 do CTB). '
            'A imposição de multa sem sinalização adequada e visível viola os princípios da '
            'legalidade e da segurança jurídica, devendo a autuação ser anulada.'
        ),
    },
    'cerceamento_defesa': {
        'titulo': 'Cerceamento de defesa — Ausência de prova fotográfica/imagem',
        'texto': (
            'O auto de infração não foi instruído com imagens ou provas suficientes para comprovar '
            'a materialidade da infração, em violação ao princípio constitucional da ampla defesa '
            '(art. 5º, LV da CF/88). A mera lavratura do auto não supre a exigência de prova '
            'concreta da conduta infracional. Na dúvida, impõe-se o princípio in dubio pro reo, '
            'também aplicável ao processo administrativo sancionatório.'
        ),
    },
    'nulidade_formal': {
        'titulo': 'Nulidade formal do auto de infração',
        'texto': (
            'O auto de infração não preenche os requisitos formais obrigatórios do art. 280 do CTB: '
            'identificação precisa do local, data, hora e circunstâncias; identificação do veículo e '
            'do condutor; tipificação correta do ato infracional e indicação da penalidade aplicável. '
            'A ausência ou incorreção de qualquer desses elementos gera nulidade absoluta, insanável '
            'por vício de forma, nos termos da doutrina e jurisprudência administrativas.'
        ),
    },
    'bons_antecedentes': {
        'titulo': 'Bons antecedentes — Histórico favorável do condutor',
        'texto': (
            'O requerente é portador de Carteira Nacional de Habilitação com histórico ilibado, '
            'condutor responsável e sem infrações anteriores relevantes, demonstrando compromisso '
            'com as normas de trânsito. Este comportamento exemplar deve ser considerado como '
            'atenuante nos termos do art. 261, §2º do CTB e do princípio da proporcionalidade das '
            'sanções administrativas.'
        ),
    },
}


def _desp_saas_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('desp_saas_user_id'):
            return redirect('/amigo-despachante/entrar')
        return f(*args, **kwargs)
    return decorated


@app.route('/amigo-despachante')
def amigo_desp_landing():
    return render_template('amigo_despachante/landing.html', plans=DESP_PLANS)


@app.route('/amigo-despachante/entrar', methods=['GET', 'POST'])
def amigo_desp_entrar():
    erro = None
    if request.method == 'POST':
        phone = (request.form.get('phone') or '').strip()
        senha = request.form.get('senha') or ''
        phone_clean = phone.replace(' ','').replace('-','').replace('(','').replace(')','').replace('+','')
        conn = get_saas_db()
        u = conn.execute(
            "SELECT * FROM despachante_users WHERE REPLACE(REPLACE(REPLACE(REPLACE(phone,' ',''),'-',''),'(',''),')','') LIKE ? AND active=1",
            (f'%{phone_clean[-8:]}',)
        ).fetchone()
        conn.close()
        if not u:
            erro = 'Telefone não encontrado ou conta inativa.'
        elif not u['password_hash']:
            erro = 'Senha ainda não definida. Entre em contato com o suporte para ativá-la.'
        elif not check_password_hash(u['password_hash'], senha):
            erro = 'Senha incorreta.'
        else:
            session['desp_saas_user_id'] = u['id']
            session['desp_saas_name']    = u['name']
            session['desp_usuario']      = u['name']
            session['desp_is_admin']     = True  # dono do tenant é sempre admin
            conn2 = get_saas_db()
            conn2.execute('UPDATE despachante_users SET last_login=? WHERE id=?',
                         (datetime.now().isoformat(), u['id']))
            conn2.commit(); conn2.close()
            return redirect('/despachante/')
    return render_template('amigo_despachante/entrar.html', erro=erro)


@app.route('/amigo-despachante/app')
@_desp_saas_login_required
def amigo_desp_app():
    """Mantido por compatibilidade — redireciona para o dashboard completo."""
    return redirect('/despachante/')


@app.route('/amigo-despachante/sair')
def amigo_desp_sair():
    for k in ('desp_saas_user_id', 'desp_saas_name', 'desp_usuario',
              'desp_is_admin', 'desp_logged', 'desp_user_id'):
        session.pop(k, None)
    return redirect('/amigo-despachante/entrar')


# ── Amigo Despachante — Recuperação de senha ─────────────────────────────────
@app.route('/amigo-despachante/esqueci-senha', methods=['GET', 'POST'])
def amigo_desp_esqueci_senha():
    enviado = False
    codigo_tela = None
    erro = None
    if request.method == 'POST':
        phone_raw = request.form.get('phone', '').strip()
        phone_clean = phone_raw.replace(' ','').replace('-','').replace('(','').replace(')','').replace('+','')
        conn = get_saas_db()
        u = conn.execute(
            "SELECT * FROM despachante_users WHERE REPLACE(REPLACE(REPLACE(REPLACE(phone,' ',''),'-',''),'(',''),')','') LIKE ?",
            (f'%{phone_clean[-8:]}',)
        ).fetchone()
        if not u:
            erro = 'Número não encontrado.'
            conn.close()
        else:
            codigo = str(random.randint(100000, 999999))
            expires = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute('UPDATE despachante_users SET reset_token=?, reset_expires=? WHERE id=?',
                         (codigo, expires, u['id']))
            conn.commit(); conn.close()
            ok = False
            if u['email']:
                html_email = f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
                  <div style="font-size:32px;margin-bottom:8px">🚗</div>
                  <h2 style="color:#0ea5e9">Recuperação de senha — Amigo Despachante</h2>
                  <p>Olá, <strong>{u['name'].split()[0]}</strong>!</p>
                  <p>Seu código de recuperação é:</p>
                  <div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#0ea5e9;
                              background:#f0f9ff;padding:20px;border-radius:12px;text-align:center;
                              margin:20px 0">{codigo}</div>
                  <p style="color:#666;font-size:13px">Válido por 2 horas.</p>
                </div>"""
                ok = _enviar_email(u['email'], 'Código de recuperação — Amigo Despachante', html_email)
            enviado = True
            if not ok:
                codigo_tela = codigo
    return render_template('amigo_despachante/esqueci_senha.html',
                           enviado=enviado, codigo_tela=codigo_tela, erro=erro)


@app.route('/amigo-despachante/redefinir-senha', methods=['GET', 'POST'])
def amigo_desp_redefinir_senha():
    sucesso = False
    erro = None
    if request.method == 'POST':
        phone_raw = request.form.get('phone', '').strip()
        phone_clean = phone_raw.replace(' ','').replace('-','').replace('(','').replace(')','').replace('+','')
        codigo = request.form.get('codigo', '').strip()
        nova = request.form.get('nova_senha', '')
        if len(nova) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        else:
            conn = get_saas_db()
            u = conn.execute(
                "SELECT * FROM despachante_users WHERE REPLACE(REPLACE(REPLACE(REPLACE(phone,' ',''),'-',''),'(',''),')','') LIKE ?",
                (f'%{phone_clean[-8:]}',)
            ).fetchone()
            if not u or u['reset_token'] != codigo:
                erro = 'Código inválido ou número incorreto.'
                conn.close()
            elif u['reset_expires'] and datetime.fromisoformat(u['reset_expires']) < datetime.now():
                erro = 'Código expirado. Solicite um novo.'
                conn.close()
            else:
                conn.execute('UPDATE despachante_users SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?',
                             (generate_password_hash(nova), u['id']))
                conn.commit(); conn.close()
                sucesso = True
    return render_template('amigo_despachante/redefinir_senha.html', sucesso=sucesso, erro=erro)


# ── Amigo Despachante — Checkout / Assinatura ────────────────────────────────
@app.route('/amigo-despachante/assinar/<plano>', methods=['GET', 'POST'])
@_desp_saas_login_required
def amigo_desp_assinar(plano):
    if plano not in DESP_PLANS:
        return redirect('/amigo-despachante/app')
    user_id = session['desp_saas_user_id']
    p = DESP_PLANS[plano]
    erro = None
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX').upper()
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            billing_type = 'PIX'
        conn = get_saas_db()
        u = conn.execute('SELECT * FROM despachante_users WHERE id=?', (user_id,)).fetchone()
        conn.close()
        if not u:
            return redirect('/amigo-despachante/entrar')
        customer_id = _asaas_criar_ou_buscar_cliente_saas(
            u['name'], u['email'] or '', u['phone'], '', u['id'], 'despachante_users'
        )
        if not customer_id:
            erro = 'Erro ao processar pagamento. Tente novamente ou entre em contato.'
        else:
            conn2 = get_saas_db()
            conn2.execute('UPDATE despachante_users SET asaas_customer_id=?, plan=? WHERE id=?',
                          (customer_id, plano, user_id))
            conn2.commit(); conn2.close()
            resp = _asaas_criar_assinatura_saas(
                customer_id, 'despachante', plano, p['preco'],
                f'Amigo Despachante {p["label"]} — Assinatura Mensal',
                billing_type
            )
            if resp.get('id'):
                return redirect('/amigo-despachante/aguardando-pagamento')
            else:
                erro = 'Não foi possível gerar o pagamento. Tente novamente.'
    return render_template('amigo_despachante/checkout.html', plano=p, plano_key=plano, erro=erro)


@app.route('/amigo-despachante/aguardando-pagamento')
@_desp_saas_login_required
def amigo_desp_aguardando():
    return render_template('amigo_despachante/aguardando.html')


# ══════════════════════════════════════════════════════════════════════════
#  AMIGO DESPACHANTE — Módulos: Clientes, OS, Financeiro, Consulta
# ══════════════════════════════════════════════════════════════════════════

def _desp_uid():
    return session.get('desp_saas_user_id')

# ── Clientes ──────────────────────────────────────────────────────────────────

@app.route('/amigo-despachante/api/clientes')
@_desp_saas_login_required
def desp_api_clientes():
    conn = get_saas_db()
    rows = conn.execute(
        'SELECT * FROM desp_clientes WHERE user_id=? ORDER BY name', (_desp_uid(),)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/amigo-despachante/api/clientes/add', methods=['POST'])
@_desp_saas_login_required
def desp_api_clientes_add():
    d    = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'erro': 'Nome obrigatório'}), 400
    conn = get_saas_db()
    cur  = conn.execute(
        'INSERT INTO desp_clientes (user_id,name,cpf_cnpj,phone,email,plate,notes,created_at) VALUES (?,?,?,?,?,?,?,?)',
        (_desp_uid(), name, d.get('cpf_cnpj',''), d.get('phone',''),
         d.get('email',''), (d.get('plate') or '').upper(),
         d.get('notes',''), datetime.now().isoformat())
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({'ok': True, 'id': new_id})


@app.route('/amigo-despachante/api/clientes/<int:cid>', methods=['PUT'])
@_desp_saas_login_required
def desp_api_clientes_edit(cid):
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'erro': 'Nome obrigatório'}), 400
    conn = get_saas_db()
    conn.execute(
        'UPDATE desp_clientes SET name=?,cpf_cnpj=?,phone=?,email=?,plate=?,notes=? WHERE id=? AND user_id=?',
        (name, d.get('cpf_cnpj',''), d.get('phone',''), d.get('email',''),
         (d.get('plate') or '').upper(), d.get('notes',''), cid, _desp_uid())
    )
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/amigo-despachante/api/clientes/<int:cid>', methods=['DELETE'])
@_desp_saas_login_required
def desp_api_clientes_delete(cid):
    conn = get_saas_db()
    conn.execute('DELETE FROM desp_clientes WHERE id=? AND user_id=?', (cid, _desp_uid()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


# ── Ordens de Serviço ────────────────────────────────────────────────────────

DESP_OS_TIPOS = [
    'CRLV', '2ª via CRLV', '1ª Habilitação', 'Renovação CNH',
    'Adição de categoria', 'Transferência de propriedade',
    'Licenciamento', 'Emplacamento', 'Recurso de multa', 'Outros'
]

@app.route('/amigo-despachante/api/os')
@_desp_saas_login_required
def desp_api_os():
    status = request.args.get('status', '')
    conn   = get_saas_db()
    if status:
        rows = conn.execute(
            'SELECT * FROM desp_os WHERE user_id=? AND status=? ORDER BY created_at DESC',
            (_desp_uid(), status)
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM desp_os WHERE user_id=? ORDER BY created_at DESC',
            (_desp_uid(),)
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/amigo-despachante/api/os/add', methods=['POST'])
@_desp_saas_login_required
def desp_api_os_add():
    d           = request.get_json() or {}
    client_name = (d.get('client_name') or '').strip()
    tipo        = (d.get('tipo') or 'Outros').strip()
    valor       = float(d.get('valor') or 0)
    conn        = get_saas_db()
    cur         = conn.execute(
        '''INSERT INTO desp_os
           (user_id,client_id,client_name,tipo,descricao,placa,status,valor,pago,prazo,created_at)
           VALUES (?,?,?,?,?,?,?,?,0,?,?)''',
        (_desp_uid(), d.get('client_id'), client_name, tipo,
         d.get('descricao',''), (d.get('placa') or '').upper(),
         d.get('status','pendente'), valor, d.get('prazo',''),
         datetime.now().isoformat())
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({'ok': True, 'id': new_id})


@app.route('/amigo-despachante/api/os/<int:oid>/status', methods=['POST'])
@_desp_saas_login_required
def desp_api_os_status(oid):
    d      = request.get_json() or {}
    status = d.get('status', 'pendente')
    if status not in ('pendente', 'em_andamento', 'concluido', 'cancelado'):
        return jsonify({'ok': False, 'erro': 'Status inválido'}), 400
    conn = get_saas_db()
    conn.execute('UPDATE desp_os SET status=? WHERE id=? AND user_id=?',
                 (status, oid, _desp_uid()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/amigo-despachante/api/os/<int:oid>/pagar', methods=['POST'])
@_desp_saas_login_required
def desp_api_os_pagar(oid):
    d    = request.get_json() or {}
    pago = 1 if d.get('pago') else 0
    conn = get_saas_db()
    conn.execute('UPDATE desp_os SET pago=? WHERE id=? AND user_id=?', (pago, oid, _desp_uid()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/amigo-despachante/api/os/<int:oid>', methods=['DELETE'])
@_desp_saas_login_required
def desp_api_os_delete(oid):
    conn = get_saas_db()
    conn.execute('DELETE FROM desp_os WHERE id=? AND user_id=?', (oid, _desp_uid()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/amigo-despachante/api/stats')
@_desp_saas_login_required
def desp_api_stats():
    uid  = _desp_uid()
    conn = get_saas_db()
    mes  = datetime.now().strftime('%Y-%m')
    total_cli    = conn.execute('SELECT COUNT(*) FROM desp_clientes WHERE user_id=?', (uid,)).fetchone()[0]
    total_os     = conn.execute('SELECT COUNT(*) FROM desp_os WHERE user_id=?', (uid,)).fetchone()[0]
    os_abertas   = conn.execute("SELECT COUNT(*) FROM desp_os WHERE user_id=? AND status IN ('pendente','em_andamento')", (uid,)).fetchone()[0]
    os_mes       = conn.execute("SELECT COUNT(*) FROM desp_os WHERE user_id=? AND created_at LIKE ?", (uid, f'{mes}%')).fetchone()[0]
    receita_mes  = conn.execute("SELECT COALESCE(SUM(valor),0) FROM desp_os WHERE user_id=? AND pago=1 AND created_at LIKE ?", (uid, f'{mes}%')).fetchone()[0]
    pendente_val = conn.execute("SELECT COALESCE(SUM(valor),0) FROM desp_os WHERE user_id=? AND pago=0 AND status != 'cancelado'", (uid,)).fetchone()[0]
    conn.close()
    return jsonify({
        'clientes': total_cli, 'os_total': total_os,
        'os_abertas': os_abertas, 'os_mes': os_mes,
        'receita_mes': receita_mes, 'pendente_val': pendente_val,
    })


@app.route('/defesapro')
def defesapro_landing():
    return render_template('defesapro/landing.html')


# ── DefesaPro — Auth helpers ───────────────────────────────────────────────────
def _defesa_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('defesa_user_id'):
            return redirect(url_for('defesa_login', next=request.path))
        return f(*args, **kwargs)
    return decorated


# ── DefesaPro — Login / Logout ─────────────────────────────────────────────────
@app.route('/defesapro/login', methods=['GET', 'POST'])
def defesa_login():
    erro = None
    pendente = False
    next_url = request.args.get('next') or request.form.get('next') or '/defesapro/app'
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        conn  = get_saas_db()
        # Busca sem filtrar active — para mostrar mensagem adequada
        u = conn.execute(
            'SELECT * FROM defesapro_users WHERE LOWER(email)=?', (email,)
        ).fetchone()
        conn.close()
        if u and u['password_hash'] and check_password_hash(u['password_hash'], senha):
            if not u['active']:
                pendente = True
                erro = 'pending'
            else:
                session['defesa_user_id']   = u['id']
                session['defesa_user_name'] = u['name']
                session['defesa_escritorio'] = u['escritorio'] or u['name']
                session['defesa_plan']       = u['plan'] or 'starter'
                c2 = get_saas_db()
                c2.execute('UPDATE defesapro_users SET last_login=? WHERE id=?',
                           (datetime.now().isoformat(), u['id']))
                c2.commit(); c2.close()
                return redirect(next_url)
        elif u and not u['password_hash']:
            erro = 'Sua conta não tem senha configurada. Entre em contato com o suporte.'
        elif u and u['password_hash'] and not check_password_hash(u['password_hash'], senha):
            erro = 'Senha incorreta.'
        else:
            erro = 'E-mail não encontrado. <a href="/defesapro/cadastro" style="color:#a855f7">Criar conta →</a>'
    return render_template('defesapro/login.html', erro=erro, pendente=pendente, next=next_url)


# ── DefesaPro — Cadastro (sem trial, ativação manual) ─────────────────────────
@app.route('/defesapro/cadastro', methods=['GET', 'POST'])
def defesa_cadastro():
    erro = None
    sucesso = False
    nome_cadastrado = ''
    if request.method == 'POST':
        name       = request.form.get('name', '').strip()
        email      = request.form.get('email', '').strip().lower()
        phone      = request.form.get('phone', '').strip()
        cpf_cnpj   = request.form.get('cpf_cnpj', '').strip()
        escritorio = request.form.get('escritorio', '').strip()
        cidade     = request.form.get('cidade', '').strip()
        plan       = request.form.get('plan', 'starter').strip()
        password   = request.form.get('password', '')
        password2  = request.form.get('password2', '')

        cpf_digits   = ''.join(c for c in cpf_cnpj if c.isdigit())
        phone_digits = ''.join(c for c in phone if c.isdigit())

        if not all([name, email, phone, cpf_cnpj, password]):
            erro = 'Preencha todos os campos obrigatórios.'
        elif len(password) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        elif password != password2:
            erro = 'As senhas não coincidem.'
        elif len(cpf_digits) not in (11, 14):
            erro = 'CPF deve ter 11 dígitos ou CNPJ deve ter 14 dígitos.'
        else:
            conn = get_saas_db()
            _wl = _is_whitelisted(phone_digits, email)
            if (not _wl) and conn.execute('SELECT id FROM defesapro_users WHERE LOWER(email)=?', (email,)).fetchone():
                erro = 'Este e-mail já possui uma conta. Faça login.'
                conn.close()
            elif (not _wl) and cpf_digits and conn.execute(
                "SELECT id FROM defesapro_users WHERE replace(replace(replace(cpf_cnpj,'.',''),'-',''),'/','')=?",
                (cpf_digits,)
            ).fetchone():
                erro = 'Este CPF/CNPJ já possui uma conta cadastrada.'
                conn.close()
            elif (not _wl) and phone_digits and conn.execute(
                "SELECT id FROM defesapro_users WHERE replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ','')=?",
                (phone_digits,)
            ).fetchone():
                erro = 'Este telefone já possui uma conta cadastrada.'
                conn.close()
            else:
                now = datetime.now().isoformat()
                conn.execute(
                    '''INSERT INTO defesapro_users
                       (name, email, phone, cpf_cnpj, escritorio, cidade, plan,
                        active, password_hash, created_at)
                       VALUES (?,?,?,?,?,?,?,0,?,?)''',
                    (name, email, phone, cpf_cnpj, escritorio, cidade, plan,
                     generate_password_hash(password), now)
                )
                conn.commit(); conn.close()
                sucesso = True
                nome_cadastrado = name.split()[0]
                # E-mail de boas-vindas
                _enviar_email(email, 'Bem-vindo ao DefesaPro!', f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
                  <div style="font-size:32px;margin-bottom:8px">⚖️</div>
                  <h2 style="color:#7c3aed">Bem-vindo ao DefesaPro, {nome_cadastrado}!</h2>
                  <p>Seu cadastro foi realizado com sucesso.</p>
                  <p style="margin-top:12px">Assim que seu pagamento for confirmado, sua conta será liberada automaticamente.</p>
                  <p style="margin-top:12px;color:#666;font-size:13px">Dúvidas? Fale pelo WhatsApp: (47) 99960-6998</p>
                </div>""")
    return render_template('defesapro/cadastro.html',
                           erro=erro, sucesso=sucesso,
                           nome_cadastrado=nome_cadastrado)


@app.route('/defesapro/logout')
def defesa_logout():
    session.pop('defesa_user_id', None)
    session.pop('defesa_user_name', None)
    session.pop('defesa_escritorio', None)
    session.pop('defesa_plan', None)
    return redirect('/defesapro/login')


# ── DefesaPro — Recuperação de senha ─────────────────────────────────────────
@app.route('/defesapro/esqueci-senha', methods=['GET', 'POST'])
def defesa_esqueci_senha():
    enviado = False
    codigo_tela = None
    erro = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        conn = get_saas_db()
        u = conn.execute('SELECT * FROM defesapro_users WHERE LOWER(email)=?', (email,)).fetchone()
        if not u:
            erro = 'E-mail não encontrado.'
            conn.close()
        else:
            codigo = str(random.randint(100000, 999999))
            expires = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute('UPDATE defesapro_users SET reset_token=?, reset_expires=? WHERE id=?',
                         (codigo, expires, u['id']))
            conn.commit(); conn.close()
            html_email = f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <div style="font-size:32px;margin-bottom:8px">⚖️</div>
              <h2 style="color:#7c3aed">Recuperação de senha — DefesaPro</h2>
              <p>Olá, <strong>{u['name'].split()[0]}</strong>!</p>
              <p>Seu código de recuperação é:</p>
              <div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#7c3aed;
                          background:#f5f3ff;padding:20px;border-radius:12px;text-align:center;
                          margin:20px 0">{codigo}</div>
              <p style="color:#666;font-size:13px">Válido por 2 horas. Se não solicitou, ignore este e-mail.</p>
            </div>"""
            ok = _enviar_email(email, 'Código de recuperação — DefesaPro', html_email)
            enviado = True
            if not ok:
                codigo_tela = codigo
    return render_template('defesapro/esqueci_senha.html',
                           enviado=enviado, codigo_tela=codigo_tela, erro=erro)


@app.route('/defesapro/redefinir-senha', methods=['GET', 'POST'])
def defesa_redefinir_senha():
    sucesso = False
    erro = None
    email_pre = request.args.get('email', '')
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        codigo = request.form.get('codigo', '').strip()
        nova = request.form.get('nova_senha', '')
        if len(nova) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        else:
            conn = get_saas_db()
            u = conn.execute('SELECT * FROM defesapro_users WHERE LOWER(email)=?', (email,)).fetchone()
            if not u or u['reset_token'] != codigo:
                erro = 'Código inválido ou expirado.'
                conn.close()
            elif u['reset_expires'] and datetime.fromisoformat(u['reset_expires']) < datetime.now():
                erro = 'Código expirado. Solicite um novo.'
                conn.close()
            else:
                conn.execute('UPDATE defesapro_users SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?',
                             (generate_password_hash(nova), u['id']))
                conn.commit(); conn.close()
                sucesso = True
    return render_template('defesapro/redefinir_senha.html',
                           sucesso=sucesso, erro=erro, email_pre=email_pre)


# ── DefesaPro — Checkout / Assinatura ────────────────────────────────────────
@app.route('/defesapro/assinar/<plano>', methods=['GET', 'POST'])
def defesa_assinar(plano):
    if plano not in DEFESAPRO_PLANOS:
        return redirect('/defesapro/planos')
    user_id = session.get('defesa_user_id')
    if not user_id:
        return redirect(f'/defesapro/login?next=/defesapro/assinar/{plano}')
    p = DEFESAPRO_PLANOS[plano]
    erro = None
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX').upper()
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            billing_type = 'PIX'
        conn = get_saas_db()
        u = conn.execute('SELECT * FROM defesapro_users WHERE id=?', (user_id,)).fetchone()
        conn.close()
        if not u:
            return redirect('/defesapro/login')
        # Cria/busca cliente no Asaas
        customer_id = _asaas_criar_ou_buscar_cliente_saas(
            u['name'], u['email'], u['phone'], u['cpf_cnpj'], u['id'], 'defesapro_users'
        )
        if not customer_id:
            erro = 'Erro ao processar pagamento. Tente novamente ou entre em contato.'
        else:
            # Salva customer_id
            conn2 = get_saas_db()
            conn2.execute('UPDATE defesapro_users SET asaas_customer_id=? WHERE id=?',
                          (customer_id, user_id))
            conn2.commit(); conn2.close()
            # Cria assinatura
            resp = _asaas_criar_assinatura_saas(
                customer_id, 'defesapro', plano, p['preco'],
                f'DefesaPro {p["nome"]} — Assinatura Mensal',
                billing_type
            )
            if resp.get('id'):
                return redirect('/defesapro/aguardando-pagamento')
            else:
                erro = 'Não foi possível gerar o pagamento. Tente novamente.'
    return render_template('defesapro/checkout.html', plano=p, plano_key=plano, erro=erro)


@app.route('/defesapro/aguardando-pagamento')
def defesa_aguardando():
    return render_template('defesapro/aguardando.html')


@app.route('/defesapro/planos')
def defesa_planos():
    return render_template('defesapro/planos.html', planos=DEFESAPRO_PLANOS,
                           user_id=session.get('defesa_user_id'),
                           plano_atual=session.get('defesa_plan'))


# ── Webhook global Asaas (todos os apps) ─────────────────────────────────────
@app.route('/webhook/asaas', methods=['GET', 'POST'])
def webhook_asaas_global():
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200
    # Validação do token
    token = os.environ.get('ASAAS_WEBHOOK_TOKEN', '')
    if token and request.headers.get('asaas-access-token') != token:
        return jsonify({'error': 'unauthorized'}), 401
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({'error': 'invalid json'}), 400

    event = payload.get('event', '')
    ref = payload.get('payment', {}).get('externalReference', '') or \
          payload.get('subscription', {}).get('externalReference', '')

    ativar = event in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED', 'SUBSCRIPTION_ACTIVATED')
    desativar = event in ('SUBSCRIPTION_CANCELLED', 'PAYMENT_OVERDUE', 'PAYMENT_DELETED')

    if not (ativar or desativar):
        return jsonify({'status': 'ignored'}), 200

    # Roteamento por prefixo
    parts       = ref.split('_')
    customer_id = parts[1] if len(parts) > 1 else None
    plano_key   = parts[2] if len(parts) > 2 else ''

    if ref.startswith('defesapro_'):
        if customer_id:
            conn = get_saas_db()
            u = conn.execute('SELECT id, name, email FROM defesapro_users WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if u:
                conn.execute('UPDATE defesapro_users SET active=?, plan_active=? WHERE id=?',
                             (1 if ativar else 0, 1 if ativar else 0, u['id']))
                conn.commit()
                if ativar and u['email']:
                    p = DEFESAPRO_PLANOS.get(plano_key, {})
                    _enviar_email(u['email'], '✅ DefesaPro — Assinatura ativa!',
                        _email_pagamento_confirmado('DefesaPro', '⚖️', '#7c3aed',
                            u['name'].split()[0], p.get('nome', plano_key),
                            p.get('preco_fmt', ''), 'https://4kitem.com.br/defesapro/app'))
            conn.close()

    elif ref.startswith('agenda_'):
        if customer_id:
            conn = get_saas_db()
            b = conn.execute('SELECT id, name, email, owner_name FROM agenda_businesses WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if b:
                conn.execute('UPDATE agenda_businesses SET active=?, plan_active=? WHERE id=?',
                             (1 if ativar else 0, 1 if ativar else 0, b['id']))
                conn.commit()
                if ativar and b['email']:
                    p = AGENDA_PLAN
                    _enviar_email(b['email'], '✅ Agenda SC — Assinatura ativa!',
                        _email_pagamento_confirmado('Agenda SC', '📅', '#22c55e',
                            b['owner_name'].split()[0], p['label'],
                            p['price'], 'https://4kitem.com.br/agenda/painel'))
            conn.close()

    elif ref.startswith('mandaja_'):
        if customer_id:
            conn = get_saas_db()
            s = conn.execute('SELECT id, name, email, owner_name, plan FROM mandaja_stores WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if s:
                conn.execute('UPDATE mandaja_stores SET plan_active=? WHERE id=?',
                             (1 if ativar else 0, s['id']))
                conn.commit()
                if ativar and s['email']:
                    p = MANDAJA_PLANS.get(plano_key or s['plan'], MANDAJA_PLANS['micro'])
                    _enviar_email(s['email'], '✅ MandaJá — Assinatura ativa!',
                        _email_pagamento_confirmado('MandaJá', '🛍️', '#f97316',
                            s['owner_name'].split()[0], p['label'],
                            f"R$ {p['price']}/mês", 'https://4kitem.com.br/mandaja/painel'))
            conn.close()

    elif ref.startswith('mandazap_'):
        if customer_id:
            conn = get_saas_db()
            u = conn.execute('SELECT id, name, email FROM mandazap_users WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if u:
                conn.execute('UPDATE mandazap_users SET active=?, plan_active=? WHERE id=?',
                             (1 if ativar else 0, 1 if ativar else 0, u['id']))
                conn.commit()
                if ativar and u['email']:
                    p = MANDAZAP_PLANS.get(plano_key, {})
                    _enviar_email(u['email'], '✅ MandaZap — Assinatura ativa!',
                        _email_pagamento_confirmado('MandaZap', '📲', '#22c55e',
                            u['name'].split()[0], p.get('label', plano_key),
                            f"R$ {p.get('price','')}/mês", 'https://4kitem.com.br/mandazap/painel'))
            conn.close()

    elif ref.startswith('despachante_'):
        if customer_id:
            conn = get_saas_db()
            u = conn.execute('SELECT id, name, email FROM despachante_users WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if u:
                conn.execute('UPDATE despachante_users SET active=?, plan_active=? WHERE id=?',
                             (1 if ativar else 0, 1 if ativar else 0, u['id']))
                conn.commit()
                if ativar and u['email']:
                    p = DESP_PLANS.get(plano_key, {})
                    _enviar_email(u['email'], '✅ Amigo Despachante — Assinatura ativa!',
                        _email_pagamento_confirmado('Amigo Despachante', '🚗', '#3b82f6',
                            u['name'].split()[0], p.get('label', plano_key),
                            p.get('price', ''), 'https://4kitem.com.br/amigo-despachante/app'))
            conn.close()

    elif ref.startswith('sz_'):
        # SlotZap — pagamento de slot avulso (externalReference = sz_{slot_id})
        try:
            slot_id = int(ref.split('_')[1])
            if ativar:
                conn = get_saas_db()
                # Marca slot como pago
                conn.execute(
                    "UPDATE slotzap_slots SET status='pago', pago_em=? WHERE id=?",
                    (datetime.now().isoformat(), slot_id)
                )
                conn.commit()

                # Busca dados para notificação WhatsApp
                row = conn.execute('''
                    SELECT s.numero, s.cliente_nome, s.cliente_tel,
                           c.nome AS camp_nome, c.preco, c.grupo_wpp_id,
                           c.evo_instance, c.msg_pagamento, c.id AS camp_id,
                           c.token_publico,
                           (SELECT COUNT(*) FROM slotzap_slots WHERE campanha_id=c.id AND status="pago")      AS pagos,
                           (SELECT COUNT(*) FROM slotzap_slots WHERE campanha_id=c.id AND status="disponivel") AS livres,
                           c.total_slots
                    FROM slotzap_slots s
                    JOIN slotzap_campanhas c ON c.id = s.campanha_id
                    WHERE s.id=?
                ''', (slot_id,)).fetchone()
                conn.close()

                if row:
                    row = dict(row)
                    log.info(f'[SlotZap] Slot #{row["numero"]} — {row["camp_nome"]} — PAGO ({row["cliente_nome"]})')

                    # Notifica grupo WhatsApp se configurado
                    grupo_id = row.get('grupo_wpp_id', '').strip()
                    instance = row.get('evo_instance', '').strip() or os.environ.get('EVO_INSTANCE', '')
                    evo_url  = os.environ.get('EVO_URL', '').rstrip('/')
                    evo_key  = os.environ.get('EVO_KEY', '')

                    if grupo_id and instance and evo_url:
                        base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
                        token    = row.get('token_publico') or ''
                        link_str = f"\n🔗 {base_url}/slotzap/p/{token}" if token else ''
                        tpl = row.get('msg_pagamento') or (
                            f"✅ *Slot #{row['numero']} — PAGO!*\n"
                            f"👤 {row['cliente_nome']}\n"
                            f"🎯 {row['camp_nome']}\n\n"
                            f"📊 {row['pagos']}/{row['total_slots']} vendidos · {row['livres']} livres"
                            f"{link_str}"
                        )
                        try:
                            requests.post(
                                f"{evo_url}/message/sendText/{instance}",
                                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                                json={'number': grupo_id, 'text': tpl},
                                timeout=10
                            )
                            log.info(f'[SlotZap] Notificação WPP enviada para grupo {grupo_id}')
                        except Exception as _wpp_err:
                            log.warning(f'[SlotZap] Erro ao notificar grupo: {_wpp_err}')
        except Exception as _sz_err:
            log.error(f'[SlotZap] Webhook error: {_sz_err}')

    elif ref.startswith('alerta_'):
        if customer_id:
            conn = get_saas_db()
            s = conn.execute('SELECT id, name, email, plano FROM alerta_subscribers WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if s:
                novo_status = 'ativo' if ativar else 'suspenso'
                conn.execute("UPDATE alerta_subscribers SET status=?, payment_status=? WHERE id=?",
                             (novo_status, 'paid' if ativar else 'overdue', s['id']))
                conn.commit()
                if ativar and s.get('email'):
                    p = ALERTA_PLANS.get(plano_key or s['plano'], {})
                    _enviar_email(s['email'], '✅ AlertaSC — Monitoramento ativado!',
                        _email_pagamento_confirmado('AlertaSC', '🚨', '#ef4444',
                            s['name'].split()[0], p.get('label', plano_key or s['plano']),
                            p.get('price', ''), 'https://4kitem.com.br/alerta/minha-conta'))
            conn.close()

    elif ref.startswith('bau_'):
        if customer_id:
            conn = get_saas_db()
            u = conn.execute('SELECT id, name, email, plan FROM bau_users WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if u:
                conn.execute('UPDATE bau_users SET active=?, plan_active=? WHERE id=?',
                             (1 if ativar else 0, 1 if ativar else 0, u['id']))
                conn.commit()
                if ativar and u['email']:
                    p = BAU_PLANS.get(plano_key or u['plan'] or 'mensal', BAU_PLANS['mensal'])
                    _enviar_email(u['email'], '✅ Baú SC — Assinatura ativa!',
                        _email_pagamento_confirmado('Baú SC', '🗝️', '#7c3aed',
                            u['name'].split()[0], p['label'],
                            p['price'], 'https://4kitem.com.br/bau/painel'))
            conn.close()

    elif ref.startswith('kids_'):
        if customer_id:
            try:
                kconn = get_kids_conn()
                c = kconn.execute('SELECT * FROM clients WHERE asaas_customer_id=?',
                                  (customer_id,)).fetchone()
                if c:
                    kconn.execute('UPDATE clients SET active=?, plan_active=? WHERE id=?',
                                  (1 if ativar else 0, 1 if ativar else 0, c['id']))
                    kconn.commit()
                    if ativar and c.get('email'):
                        p = KIDS_PLANS.get(plano_key or c.get('plan', 'mensal'), KIDS_PLANS['mensal'])
                        _enviar_email(c['email'], '✅ KidsCurator — Acesso liberado!',
                            _email_pagamento_confirmado('KidsCurator', '📺', '#3b82f6',
                                c['name'].split()[0], p['label'],
                                p['price'],
                                f'https://4kitem.com.br/painel/{c["code"]}') +
                            f'<div style="font-family:sans-serif;max-width:480px;margin:auto;padding:0 32px 24px">'
                            f'<p style="background:#1e3a5f;border-radius:10px;padding:16px;color:#93c5fd;font-size:15px">'
                            f'🔑 Seu código de acesso: <strong style="font-size:20px;color:#60a5fa">{c["code"]}</strong><br>'
                            f'<small>Use em: 4kitem.com.br/kids/entrar</small></p></div>')
                kconn.close()
            except Exception:
                log.exception('[Webhook] Erro ao ativar KidsCurator')

    log.info(f'[WEBHOOK ASAAS] event={event} ref={ref} ativar={ativar}')
    return jsonify({'status': 'ok'}), 200


# ── DefesaPro — App principal ──────────────────────────────────────────────────
@app.route('/defesapro/app')
@_defesa_login_required
def defesa_app():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    u = conn.execute('SELECT * FROM defesapro_users WHERE id=?', (user_id,)).fetchone()
    conn.close()
    if not u or not u['active']:
        session.clear()
        return redirect('/defesapro/login')
    conn2 = get_saas_db()
    ativos    = conn2.execute('SELECT COUNT(*) FROM defesapro_processos WHERE user_id=? AND status NOT IN ("cancelado","encerrado")', (user_id,)).fetchone()[0]
    prazos7   = conn2.execute(
        "SELECT COUNT(*) FROM defesapro_processos WHERE user_id=? AND prazo_defesa!='' AND prazo_defesa BETWEEN date('now') AND date('now','+7 days') AND status='aberto'",
        (user_id,)
    ).fetchone()[0]
    deferidos = conn2.execute('SELECT COUNT(*) FROM defesapro_processos WHERE user_id=? AND status="deferido"', (user_id,)).fetchone()[0]
    hon_mes   = conn2.execute(
        "SELECT COALESCE(SUM(valor),0) FROM defesapro_financeiro WHERE user_id=? AND pago=1 AND strftime('%Y-%m',data)=strftime('%Y-%m','now')",
        (user_id,)
    ).fetchone()[0]
    pendente_fin = conn2.execute(
        "SELECT COALESCE(SUM(valor),0) FROM defesapro_financeiro WHERE user_id=? AND pago=0",
        (user_id,)
    ).fetchone()[0]
    # Últimos 5 processos
    recentes = [dict(r) for r in conn2.execute(
        '''SELECT p.id, p.placa, p.numero_auto, p.artigo_ctb, p.status, p.prazo_defesa,
                  p.created_at, c.name AS cliente_nome
           FROM defesapro_processos p
           LEFT JOIN defesapro_clientes c ON c.id=p.cliente_id
           WHERE p.user_id=? ORDER BY p.created_at DESC LIMIT 5''',
        (user_id,)
    ).fetchall()]
    # Próximos prazos urgentes
    prazos_urgentes = [dict(r) for r in conn2.execute(
        '''SELECT p.id, p.placa, p.numero_auto, p.prazo_defesa, c.name AS cliente_nome
           FROM defesapro_processos p
           LEFT JOIN defesapro_clientes c ON c.id=p.cliente_id
           WHERE p.user_id=? AND p.prazo_defesa!='' AND p.prazo_defesa BETWEEN date('now') AND date('now','+7 days') AND p.status='aberto'
           ORDER BY p.prazo_defesa ASC LIMIT 5''',
        (user_id,)
    ).fetchall()]
    conn2.close()
    stats = {'ativos': ativos, 'prazos7': prazos7, 'deferidos': deferidos,
             'hon_mes': hon_mes, 'pendente_fin': pendente_fin}
    return render_template('defesapro/app.html', user=dict(u), stats=stats,
                           recentes=recentes, prazos_urgentes=prazos_urgentes,
                           ctb_status=CTB_STATUS, hoje=date.today().isoformat())


# ── DefesaPro — Clientes ──────────────────────────────────────────────────────
@app.route('/defesapro/clientes', methods=['GET', 'POST'])
@_defesa_login_required
def defesa_clientes():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    erro = sucesso = None
    if request.method == 'POST':
        name    = request.form.get('name', '').strip()
        cpf     = request.form.get('cpf', '').strip()
        phone   = request.form.get('phone', '').strip()
        email   = request.form.get('email', '').strip()
        cnh     = request.form.get('cnh', '').strip()
        address = request.form.get('address', '').strip()
        notes   = request.form.get('notes', '').strip()
        if not name:
            erro = 'Nome é obrigatório.'
        else:
            conn.execute(
                'INSERT INTO defesapro_clientes (user_id,name,cpf,phone,email,cnh,address,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (user_id, name, cpf, phone, email, cnh, address, notes, datetime.now().isoformat())
            )
            conn.commit()
            sucesso = f'Cliente "{name}" cadastrado com sucesso.'
    clientes = conn.execute(
        'SELECT * FROM defesapro_clientes WHERE user_id=? ORDER BY name ASC', (user_id,)
    ).fetchall()
    conn.close()
    return render_template('defesapro/clientes.html',
                           clientes=[dict(c) for c in clientes],
                           erro=erro, sucesso=sucesso)


@app.route('/defesapro/clientes/<int:cid>/editar', methods=['GET', 'POST'])
@_defesa_login_required
def defesa_cliente_editar(cid):
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    cli = conn.execute('SELECT * FROM defesapro_clientes WHERE id=? AND user_id=?', (cid, user_id)).fetchone()
    if not cli:
        conn.close(); return redirect('/defesapro/clientes')
    erro = None
    if request.method == 'POST':
        name    = request.form.get('name', '').strip()
        cpf     = request.form.get('cpf', '').strip()
        phone   = request.form.get('phone', '').strip()
        email   = request.form.get('email', '').strip()
        cnh     = request.form.get('cnh', '').strip()
        address = request.form.get('address', '').strip()
        notes   = request.form.get('notes', '').strip()
        if not name:
            erro = 'Nome é obrigatório.'
        else:
            conn.execute(
                'UPDATE defesapro_clientes SET name=?,cpf=?,phone=?,email=?,cnh=?,address=?,notes=? WHERE id=? AND user_id=?',
                (name, cpf, phone, email, cnh, address, notes, cid, user_id)
            )
            conn.commit(); conn.close()
            return redirect('/defesapro/clientes')
    conn.close()
    return render_template('defesapro/cliente_form.html', cliente=dict(cli), erro=erro)


@app.route('/defesapro/clientes/<int:cid>/deletar', methods=['POST'])
@_defesa_login_required
def defesa_cliente_deletar(cid):
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    conn.execute('DELETE FROM defesapro_clientes WHERE id=? AND user_id=?', (cid, user_id))
    conn.commit(); conn.close()
    return redirect('/defesapro/clientes')


# ── DefesaPro — Processos ─────────────────────────────────────────────────────
@app.route('/defesapro/processos', methods=['GET', 'POST'])
@_defesa_login_required
def defesa_processos():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    erro = sucesso = None
    if request.method == 'POST':
        artigo       = request.form.get('artigo_ctb', '').strip()
        numero_auto  = request.form.get('numero_auto', '').strip()
        placa        = request.form.get('placa', '').strip().upper()
        proprietario = request.form.get('proprietario', '').strip()
        condutor     = request.form.get('condutor', '').strip()
        data_inf     = request.form.get('data_infracao', '').strip()
        hora_inf     = request.form.get('hora_infracao', '').strip()
        local_inf    = request.form.get('local_infracao', '').strip()
        orgao        = request.form.get('orgao_autuador', '').strip()
        prazo        = request.form.get('prazo_defesa', '').strip()
        honorarios   = float(request.form.get('honorarios', 0) or 0)
        obs          = request.form.get('observacoes', '').strip()
        cliente_id   = request.form.get('cliente_id') or None
        artigo_info  = CTB_ARTIGOS.get(artigo, CTB_ARTIGOS['outro'])
        if not placa and not numero_auto:
            erro = 'Informe a placa ou o número do auto.'
        else:
            now = datetime.now().isoformat()
            pid = conn.execute(
                '''INSERT INTO defesapro_processos
                   (user_id,cliente_id,numero_auto,placa,proprietario,condutor,data_infracao,
                    hora_infracao,local_infracao,orgao_autuador,artigo_ctb,descricao,
                    pontos,valor_multa,prazo_defesa,honorarios,observacoes,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (user_id, cliente_id, numero_auto, placa, proprietario, condutor, data_inf,
                 hora_inf, local_inf, orgao, artigo, artigo_info['label'],
                 artigo_info['pontos'], artigo_info['valor'], prazo, honorarios, obs, now, now)
            ).lastrowid
            if honorarios > 0:
                conn.execute(
                    'INSERT INTO defesapro_financeiro (user_id,processo_id,cliente_id,tipo,descricao,valor,data,created_at) VALUES (?,?,?,?,?,?,?,?)',
                    (user_id, pid, cliente_id, 'honorario', f'Honorários — {placa or numero_auto}', honorarios, data_inf or now[:10], now)
                )
            conn.commit()
            sucesso = f'Processo {"placa "+placa if placa else "auto "+numero_auto} criado.'
    filtro_status = request.args.get('status', '')
    q = 'SELECT p.*, c.name AS cliente_nome FROM defesapro_processos p LEFT JOIN defesapro_clientes c ON c.id=p.cliente_id WHERE p.user_id=?'
    params = [user_id]
    if filtro_status:
        q += ' AND p.status=?'; params.append(filtro_status)
    q += ' ORDER BY p.created_at DESC'
    processos = [dict(r) for r in conn.execute(q, params).fetchall()]
    clientes  = [dict(r) for r in conn.execute('SELECT id,name FROM defesapro_clientes WHERE user_id=? ORDER BY name', (user_id,)).fetchall()]
    conn.close()
    return render_template('defesapro/processos.html',
                           processos=processos, clientes=clientes,
                           ctb_artigos=CTB_ARTIGOS, ctb_status=CTB_STATUS, ctb_fases=CTB_FASES,
                           filtro_status=filtro_status, erro=erro, sucesso=sucesso,
                           hoje=date.today().isoformat())


def _map_artigo_ctb(texto):
    """Mapeia texto livre do artigo CTB extraído por OCR para chave do CTB_ARTIGOS."""
    import re as _re_m
    t = texto.lower()
    t = _re_m.sub(r'art[igo.]*\s*', '', t)          # remove "art.", "artigo"
    t = t.replace(',', ' ').replace(';', ' ')
    # normaliza incisos romanos para _x
    t = _re_m.sub(r'\s+iv\b', '_iv', t)
    t = _re_m.sub(r'\s+iii\b', '_iii', t)
    t = _re_m.sub(r'\s+ii\b', '_ii', t)
    t = _re_m.sub(r'\s+i\b', '_i', t)
    t = t.strip()
    MAPA = {
        '162_i': '162_i', '162': '162_i',
        '165': '165',
        '218_i': '218_i', '218_ii': '218_ii', '218_iii': '218_iii', '218_iv': '218_iv', '218': '218_i',
        '230_i': '230_i', '230': '230_i',
        '244_i': '244_i', '244': '244_i',
        '167': '167', '208': '208', '175': '175',
        '219': '219', '228': '228', '253': '253',
    }
    # tenta match direto
    for k, v in MAPA.items():
        if t.startswith(k) or k in t:
            return v
    return 'outro'


@app.route('/defesapro/processos/ocr', methods=['POST'])
@_defesa_login_required
def defesa_processo_ocr():
    """Recebe foto/imagem de um auto de infração e extrai campos via Groq Vision."""
    import base64 as _b64ocr, mimetypes as _mt_ocr, re as _re_ocr, json as _json_ocr

    groq_key = os.environ.get('GROQ_API_KEY', '')
    if not groq_key:
        return jsonify({'erro': 'GROQ_API_KEY não configurada no servidor'}), 500

    # Suporta até 3 imagens: auto de infração, dados do veículo, dados do condutor
    f_auto     = request.files.get('arquivo_auto') or request.files.get('arquivo')
    f_veiculo  = request.files.get('arquivo_veiculo')
    f_condutor = request.files.get('arquivo_condutor')

    if not f_auto:
        return jsonify({'erro': 'Envie ao menos a foto do auto de infração'}), 400

    def _ocr_imagem(arq, prompt_txt):
        """Chama Groq Vision em uma imagem e retorna dict com dados extraídos."""
        dados_bytes = arq.read()
        mime = arq.mimetype or _mt_ocr.guess_type(arq.filename or '')[0] or 'image/jpeg'
        if 'pdf' in mime.lower() or (arq.filename or '').lower().endswith('.pdf'):
            return None, 'Envie foto/imagem (JPG/PNG), não PDF.'
        if len(dados_bytes) > 10 * 1024 * 1024:
            return None, 'Arquivo muito grande. Máx. 10 MB.'
        img_b64 = _b64ocr.b64encode(dados_bytes).decode()
        try:
            resp = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
                json={
                    'model': 'meta-llama/llama-4-scout-17b-16e-instruct',
                    'messages': [{'role': 'user', 'content': [
                        {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}},
                        {'type': 'text', 'text': prompt_txt},
                    ]}],
                    'max_tokens': 1024,
                    'temperature': 0.1,
                },
                timeout=90,
            )
            resp.raise_for_status()
            texto = resp.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            return None, f'Erro ao processar imagem: {e}'
        m = _re_ocr.search(r'\{[\s\S]*\}', texto)
        if not m:
            return None, 'Não foi possível extrair dados — tente com foto mais nítida'
        try:
            return _json_ocr.loads(m.group()), None
        except Exception:
            return None, 'Erro ao interpretar resposta — tente novamente'

    PROMPT_AUTO = (
        'Você está analisando a foto de um AUTO DE INFRAÇÃO DE TRÂNSITO brasileiro.\n'
        'Extraia os dados abaixo e retorne SOMENTE um objeto JSON válido — sem markdown, sem explicações:\n'
        '{\n'
        '  "numero_auto": "número/código do auto de infração",\n'
        '  "placa": "placa do veículo, formato ABC1234 ou ABC-1234",\n'
        '  "proprietario": "nome completo do proprietário do veículo conforme consta no auto",\n'
        '  "condutor": "nome completo do condutor infrator (se diferente do proprietário, caso contrário deixe vazio)",\n'
        '  "data_infracao": "data no formato YYYY-MM-DD",\n'
        '  "hora_infracao": "hora no formato HH:MM",\n'
        '  "local_infracao": "endereço ou local completo da infração",\n'
        '  "orgao_autuador": "órgão responsável (ex: PRF, DETRAN-SC, PM, DEINFRA)",\n'
        '  "artigo_ctb": "artigo e inciso do CTB, ex: 218 II, 165, 162 I, 244 I",\n'
        '  "valor_multa": 195.23,\n'
        '  "prazo_defesa": "prazo para defesa prévia no formato YYYY-MM-DD, se visível"\n'
        '}\n'
        'Use "" para campos não visíveis. Para valor_multa use número sem símbolo R$.'
    )

    PROMPT_VEICULO = (
        'Você está analisando um documento de veículo brasileiro (CRLV, DUT, nota fiscal ou similar).\n'
        'Extraia SOMENTE os campos abaixo em JSON válido:\n'
        '{\n'
        '  "placa": "placa do veículo",\n'
        '  "renavam": "número RENAVAM",\n'
        '  "proprietario": "nome do proprietário conforme o documento"\n'
        '}\n'
        'Use "" para campos não visíveis. Retorne APENAS o JSON, sem explicações.'
    )

    PROMPT_CONDUTOR = (
        'Você está analisando um documento de identificação brasileiro (RG, CNH, CPF, comprovante).\n'
        'Extraia SOMENTE os campos abaixo em JSON válido:\n'
        '{\n'
        '  "condutor": "nome completo da pessoa",\n'
        '  "cpf": "CPF da pessoa (somente números)",\n'
        '  "cnh": "número da CNH, se visível"\n'
        '}\n'
        'Use "" para campos não visíveis. Retorne APENAS o JSON, sem explicações.'
    )

    # Processa imagem do auto (obrigatório)
    data, erro = _ocr_imagem(f_auto, PROMPT_AUTO)
    if erro:
        log.error(f'DefesaPro OCR auto error: {erro}')
        return jsonify({'erro': erro}), 422

    # Mescla dados do CRLV/veículo (opcional)
    if f_veiculo and f_veiculo.filename:
        dados_v, _ = _ocr_imagem(f_veiculo, PROMPT_VEICULO)
        if dados_v:
            if dados_v.get('placa') and not data.get('placa'):
                data['placa'] = dados_v['placa']
            if dados_v.get('renavam'):
                data['renavam'] = dados_v['renavam']
            if dados_v.get('proprietario') and not data.get('proprietario'):
                data['proprietario'] = dados_v['proprietario']

    # Mescla dados do condutor/infrator (opcional)
    if f_condutor and f_condutor.filename:
        dados_c, _ = _ocr_imagem(f_condutor, PROMPT_CONDUTOR)
        if dados_c:
            if dados_c.get('condutor'):
                data['condutor'] = dados_c['condutor']
            if dados_c.get('cpf'):
                data['condutor_cpf'] = dados_c['cpf']
            if dados_c.get('cnh'):
                data['condutor_cnh'] = dados_c['cnh']

    # Mapeia artigo para chave do CTB_ARTIGOS
    artigo_raw = str(data.get('artigo_ctb') or '')
    data['artigo_ctb_key'] = _map_artigo_ctb(artigo_raw)

    # Normaliza valor_multa
    try:
        vm = data.get('valor_multa')
        if isinstance(vm, str):
            vm = _re_ocr.sub(r'[^\d,.]', '', vm).replace(',', '.')
        data['valor_multa'] = round(float(vm or 0), 2)
    except Exception:
        data['valor_multa'] = 0.0

    # Normaliza placa: remove espaços, traços extras
    placa = str(data.get('placa') or '').upper().strip()
    placa = _re_ocr.sub(r'[^A-Z0-9]', '', placa)
    if len(placa) >= 7:
        data['placa'] = placa[:3] + '-' + placa[3:]
    else:
        data['placa'] = placa

    return jsonify({'ok': True, 'dados': data})


@app.route('/defesapro/processos/<int:pid>')
@_defesa_login_required
def defesa_processo_detalhe(pid):
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    p = conn.execute(
        'SELECT p.*, c.name AS cliente_nome, c.phone AS cliente_phone FROM defesapro_processos p LEFT JOIN defesapro_clientes c ON c.id=p.cliente_id WHERE p.id=? AND p.user_id=?',
        (pid, user_id)
    ).fetchone()
    if not p:
        conn.close(); return redirect('/defesapro/processos')
    peticoes = [dict(r) for r in conn.execute(
        'SELECT * FROM defesapro_peticoes WHERE processo_id=? AND user_id=? ORDER BY created_at DESC',
        (pid, user_id)
    ).fetchall()]
    pagamentos = [dict(r) for r in conn.execute(
        'SELECT * FROM defesapro_financeiro WHERE processo_id=? AND user_id=? ORDER BY data DESC',
        (pid, user_id)
    ).fetchall()]
    conn.close()
    return render_template('defesapro/processo_detalhe.html',
                           p=dict(p), peticoes=peticoes, pagamentos=pagamentos,
                           ctb_status=CTB_STATUS, ctb_fases=CTB_FASES)


@app.route('/defesapro/processos/<int:pid>/status', methods=['POST'])
@_defesa_login_required
def defesa_processo_status(pid):
    user_id = session['defesa_user_id']
    novo_status = request.form.get('status', '')
    nova_fase   = request.form.get('fase', '')
    if novo_status not in CTB_STATUS:
        return redirect(f'/defesapro/processos/{pid}')
    conn = get_saas_db()
    conn.execute(
        'UPDATE defesapro_processos SET status=?,fase=?,updated_at=? WHERE id=? AND user_id=?',
        (novo_status, nova_fase or CTB_FASES.get(novo_status, 'encerrado'), datetime.now().isoformat(), pid, user_id)
    )
    conn.commit(); conn.close()
    return redirect(f'/defesapro/processos/{pid}')


@app.route('/defesapro/processos/<int:pid>/editar', methods=['GET', 'POST'])
@_defesa_login_required
def defesa_processo_editar(pid):
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    p = conn.execute('SELECT * FROM defesapro_processos WHERE id=? AND user_id=?', (pid, user_id)).fetchone()
    if not p:
        conn.close(); return redirect('/defesapro/processos')
    erro = None
    if request.method == 'POST':
        artigo       = request.form.get('artigo_ctb', '').strip()
        numero_auto  = request.form.get('numero_auto', '').strip()
        placa        = request.form.get('placa', '').strip().upper()
        proprietario = request.form.get('proprietario', '').strip()
        data_inf     = request.form.get('data_infracao', '').strip()
        hora_inf     = request.form.get('hora_infracao', '').strip()
        local_inf    = request.form.get('local_infracao', '').strip()
        orgao        = request.form.get('orgao_autuador', '').strip()
        prazo        = request.form.get('prazo_defesa', '').strip()
        honorarios   = float(request.form.get('honorarios', 0) or 0)
        obs          = request.form.get('observacoes', '').strip()
        cliente_id   = request.form.get('cliente_id') or None
        artigo_info  = CTB_ARTIGOS.get(artigo, CTB_ARTIGOS['outro'])
        conn.execute(
            '''UPDATE defesapro_processos SET cliente_id=?,numero_auto=?,placa=?,proprietario=?,
               data_infracao=?,hora_infracao=?,local_infracao=?,orgao_autuador=?,artigo_ctb=?,
               descricao=?,pontos=?,valor_multa=?,prazo_defesa=?,honorarios=?,observacoes=?,updated_at=?
               WHERE id=? AND user_id=?''',
            (cliente_id, numero_auto, placa, proprietario, data_inf, hora_inf, local_inf, orgao,
             artigo, artigo_info['label'], artigo_info['pontos'], artigo_info['valor'],
             prazo, honorarios, obs, datetime.now().isoformat(), pid, user_id)
        )
        conn.commit(); conn.close()
        return redirect(f'/defesapro/processos/{pid}')
    clientes = [dict(r) for r in conn.execute('SELECT id,name FROM defesapro_clientes WHERE user_id=? ORDER BY name', (user_id,)).fetchall()]
    conn.close()
    return render_template('defesapro/processo_form.html',
                           p=dict(p), clientes=clientes,
                           ctb_artigos=CTB_ARTIGOS, editando=True, erro=erro)


@app.route('/defesapro/processos/<int:pid>/deletar', methods=['POST'])
@_defesa_login_required
def defesa_processo_deletar(pid):
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    conn.execute('DELETE FROM defesapro_processos WHERE id=? AND user_id=?', (pid, user_id))
    conn.commit(); conn.close()
    return redirect('/defesapro/processos')


# ── DefesaPro — Petições ──────────────────────────────────────────────────────
@app.route('/defesapro/peticoes')
@_defesa_login_required
def defesa_peticoes():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    peticoes = [dict(r) for r in conn.execute(
        '''SELECT t.*, p.placa, p.numero_auto, c.name AS cliente_nome
           FROM defesapro_peticoes t
           LEFT JOIN defesapro_processos p ON p.id=t.processo_id
           LEFT JOIN defesapro_clientes  c ON c.id=p.cliente_id
           WHERE t.user_id=? ORDER BY t.created_at DESC''',
        (user_id,)
    ).fetchall()]
    processos = [dict(r) for r in conn.execute(
        'SELECT id, placa, numero_auto, artigo_ctb FROM defesapro_processos WHERE user_id=? AND status="aberto" ORDER BY created_at DESC',
        (user_id,)
    ).fetchall()]
    conn.close()
    return render_template('defesapro/peticoes.html',
                           peticoes=peticoes, processos=processos, teses=TESES_DEFESA)


@app.route('/defesapro/peticoes/gerar', methods=['GET', 'POST'])
@_defesa_login_required
def defesa_peticao_gerar():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    processos = [dict(r) for r in conn.execute(
        '''SELECT p.*, c.name AS cliente_nome, c.cpf AS cliente_cpf, c.cnh AS cliente_cnh
           FROM defesapro_processos p
           LEFT JOIN defesapro_clientes c ON c.id=p.cliente_id
           WHERE p.user_id=? ORDER BY p.created_at DESC''',
        (user_id,)
    ).fetchall()]
    peticao_gerada = None
    pid_sel = None
    if request.method == 'POST':
        pid_sel   = request.form.get('processo_id')
        tipo      = request.form.get('tipo', 'defesa_previa')
        teses_sel = request.form.getlist('teses')
        orgao_dest = request.form.get('orgao_dest', '').strip() or 'JARI Competente'
        cidade     = request.form.get('cidade', '').strip() or 'Florianópolis/SC'
        p = next((x for x in processos if str(x['id']) == str(pid_sel)), None)
        if p:
            tipo_labels = {'defesa_previa': 'DEFESA PRÉVIA', 'recurso_jari': 'RECURSO ADMINISTRATIVO — JARI', 'cetran': 'RECURSO ADMINISTRATIVO — CETRAN'}
            tipo_label  = tipo_labels.get(tipo, 'DEFESA PRÉVIA')
            nome_req    = p['cliente_nome'] or p['proprietario'] or '[NOME DO REQUERENTE]'
            cpf_req     = p['cliente_cpf'] or '[CPF]'
            cnh_req     = p['cliente_cnh'] or '[CNH]'
            placa       = p['placa'] or '[PLACA]'
            auto_num    = p['numero_auto'] or '[NÚMERO DO AUTO]'
            data_inf    = p['data_infracao'] or '[DATA]'
            hora_inf    = p['hora_infracao'] or '[HORA]'
            local_inf   = p['local_infracao'] or '[LOCAL]'
            artigo_desc = p['descricao'] or p['artigo_ctb'] or '[ARTIGO]'
            orgao_aut   = p['orgao_autuador'] or '[ÓRGÃO AUTUADOR]'
            hoje        = datetime.now().strftime('%d de %B de %Y')
            # Monta teses selecionadas
            teses_texto = ''
            for i, tk in enumerate(teses_sel, 1):
                t = TESES_DEFESA.get(tk)
                if t:
                    teses_texto += f'\n{i}. {t["titulo"].upper()}\n\n{t["texto"]}\n'
            if not teses_texto:
                teses_texto = '\n[Descreva aqui os argumentos de defesa]\n'
            peticao_gerada = f"""EXCELENTÍSSIMO(A) SENHOR(A) {orgao_dest.upper()}

{tipo_label}

{nome_req}, portador(a) da CNH nº {cnh_req} e CPF nº {cpf_req}, proprietário(a)/condutor(a) do veículo de placa {placa}, vem, respeitosamente, apresentar

{tipo_label} (art. 285 do Código de Trânsito Brasileiro)

em face do Auto de Infração nº {auto_num}, lavrado em {data_inf} às {hora_inf}, em {local_inf}, por {orgao_aut}, em razão de suposta infração ao {artigo_desc}, pelos motivos de fato e de direito a seguir expostos.

I – DOS FATOS

Em {data_inf}, o veículo de placa {placa} foi autuado por suposta infração ao {artigo_desc}, conforme Auto de Infração nº {auto_num}. O requerente, não concordando com a autuação, vem exercer seu direito constitucional à ampla defesa e ao contraditório, nos termos do art. 5º, LV da Constituição Federal e do art. 285 do CTB.

II – DO DIREITO
{teses_texto}
III – DOS PEDIDOS

Ante o exposto, requer:
a) O recebimento e conhecimento da presente {tipo_label.lower()};
b) O arquivamento do Auto de Infração nº {auto_num} e o cancelamento de qualquer penalidade dele decorrente;
c) Caso não seja acolhido o pedido principal, que seja aplicada a penalidade mínima prevista em lei, considerados os bons antecedentes do requerente;
d) A expedição de notificação sobre o resultado do julgamento no endereço do requerente.

Termos em que pede deferimento.

{cidade}, {hoje}.

{nome_req}
CPF: {cpf_req}
"""
            # Salva a petição
            pet_id = conn.execute(
                'INSERT INTO defesapro_peticoes (user_id,processo_id,tipo,conteudo,teses_json,created_at) VALUES (?,?,?,?,?,?)',
                (user_id, p['id'], tipo, peticao_gerada, _json.dumps(teses_sel), datetime.now().isoformat())
            ).lastrowid
            conn.commit()
    conn.close()
    return render_template('defesapro/peticao_gerar.html',
                           processos=processos, teses=TESES_DEFESA,
                           peticao_gerada=peticao_gerada, pid_sel=pid_sel,
                           pet_id_gerado=pet_id if peticao_gerada else None)


@app.route('/defesapro/peticoes/gerar-ia', methods=['POST'])
@_defesa_login_required
def defesa_peticao_gerar_ia():
    """Gera petição completa usando Groq Vision LLM com prompt jurídico maximizado."""
    import re as _re_ia, json as _json_ia

    groq_key = os.environ.get('GROQ_API_KEY', '')
    if not groq_key:
        return jsonify({'erro': 'GROQ_API_KEY não configurada no servidor'}), 500

    user_id = session['defesa_user_id']
    data    = request.get_json(silent=True) or {}

    pid_sel    = data.get('processo_id')
    tipo       = data.get('tipo', 'defesa_previa')
    teses_sel  = data.get('teses', [])
    orgao_dest = (data.get('orgao_dest') or 'JARI Competente').strip()
    cidade     = (data.get('cidade')     or 'Florianópolis/SC').strip()

    conn = get_saas_db()
    row = conn.execute(
        '''SELECT p.*, c.name AS cliente_nome, c.cpf AS cliente_cpf, c.cnh AS cliente_cnh,
                  c.phone AS cliente_phone
           FROM defesapro_processos p
           LEFT JOIN defesapro_clientes c ON c.id=p.cliente_id
           WHERE p.id=? AND p.user_id=?''',
        (pid_sel, user_id)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'erro': 'Processo não encontrado'}), 404

    p = dict(row)

    tipo_labels = {
        'defesa_previa': 'DEFESA PRÉVIA',
        'recurso_jari':  'RECURSO ADMINISTRATIVO — JARI',
        'cetran':        'RECURSO ADMINISTRATIVO — CETRAN',
    }
    tipo_label  = tipo_labels.get(tipo, 'DEFESA PRÉVIA')
    # Condutor infrator tem prioridade; se ausente, usa cliente ou proprietário
    condutor_p  = p.get('condutor', '') or ''
    nome_req    = p['cliente_nome'] or condutor_p or p['proprietario'] or '[NOME DO REQUERENTE]'
    nome_prop   = p['proprietario'] or nome_req  # proprietário do veículo
    cpf_req     = p['cliente_cpf']  or '[CPF]'
    cnh_req     = p['cliente_cnh']  or '[CNH]'
    placa       = p['placa']        or '[PLACA]'
    auto_num    = p['numero_auto']  or '[NÚMERO DO AUTO]'
    data_inf    = p['data_infracao'] or '[DATA]'
    hora_inf    = p['hora_infracao'] or '[HORA]'
    local_inf   = p['local_infracao'] or '[LOCAL]'
    artigo_desc = p['descricao']    or p['artigo_ctb'] or '[ARTIGO]'
    orgao_aut   = p['orgao_autuador'] or '[ÓRGÃO AUTUADOR]'
    valor_multa = p['valor_multa']  or 0
    hoje        = datetime.now().strftime('%d de %B de %Y')

    # Monta descrição das teses selecionadas
    teses_bloco = ''
    for i, tk in enumerate(teses_sel, 1):
        t = TESES_DEFESA.get(tk)
        if t:
            teses_bloco += f'\n  {i}. {t["titulo"]}: {t["texto"][:200]}...'
    if not teses_bloco:
        teses_bloco = '\n  (Nenhuma tese específica selecionada — use argumentação geral)'

    SYSTEM = (
        'Você é um advogado brasileiro especialista em Direito de Trânsito, com 20 anos de experiência '
        'em defesas administrativas perante DETRAN, JARI e CETRAN de todo o Brasil. '
        'Conhece profundamente o CTB (Lei 9.503/97), todas as Resoluções do CONTRAN, '
        'e a jurisprudência do STJ e tribunais estaduais em matéria de trânsito. '
        'Você redige petições técnicas, formais, completas e com máxima fundamentação legal. '
        'Nunca usa linguagem coloquial. Sempre cita artigos com seu texto ou ementa. '
        'Sempre requer efeito suspensivo. Sempre defende a necessidade da CNH para o trabalho.'
    )

    USER = f"""Redija uma {tipo_label} completa, formal e técnica para o caso abaixo.

=== DADOS DO PROCESSO ===
Auto de Infração nº: {auto_num}
Requerente (quem assina a defesa): {nome_req}
Proprietário do veículo: {nome_prop}
{f'Condutor infrator: {condutor_p}' if condutor_p and condutor_p != nome_req else ''}
CPF: {cpf_req}  |  CNH: {cnh_req}
Placa: {placa}
Artigo CTB infringido: {artigo_desc}
Data da infração: {data_inf}  |  Hora: {hora_inf}
Local: {local_inf}
Órgão autuador: {orgao_aut}
Valor da multa: R$ {valor_multa:.2f}
Destinatário: {orgao_dest}
Cidade/UF: {cidade}
Data de hoje: {hoje}

=== TESES INDICADAS PELO ADVOGADO ===
{teses_bloco}

=== ESTRUTURA OBRIGATÓRIA — SIGA EXATAMENTE ===

**CABEÇALHO**
Excelentíssimo(a) Senhor(a) [cargo apropriado] do {orgao_dest}
[Identificação completa do requerente com qualificação]
[Referência ao auto de infração]

**I — DOS FATOS**
Narração objetiva: data, hora, local, auto nº, artigo. Dizer que o requerente não concorda com a autuação e exerce seu direito à ampla defesa (art. 5º, LV CF/88 e art. 285 CTB).

**II — DO EFEITO SUSPENSIVO** ← SEMPRE INCLUIR, É PRIORIDADE
— Requerer EXPRESSAMENTE a suspensão imediata de todos os efeitos da penalidade (pontos, multa, restrição) até o julgamento final
— Fundamentar com: art. 285 §1º CTB; art. 97 da Lei 9.784/1999 (processo administrativo federal); art. 5º, LVII CF/88 (presunção de inocência); princípio da não-culpabilidade
— Argumentar que a aplicação imediata causa dano irreparável ao requerente antes do contraditório
— Requerer que nenhuma pontuação seja lançada na CNH durante a tramitação

**III — DA NECESSIDADE DA CNH PARA SUSTENTO E MANUTENÇÃO DA FAMÍLIA** ← SEMPRE INCLUIR
— Declarar que o requerente depende da CNH para exercer seu trabalho e sustentar sua família
— Citar: art. 6º CF/88 (direito social ao trabalho); art. 7º CF/88 (garantias do trabalhador); art. 170 CF/88 (livre exercício da atividade econômica); art. 1º, IV CF/88 (dignidade da pessoa humana como fundamento da República)
— Princípio da proporcionalidade: a penalidade não pode ser mais gravosa que o ilícito, especialmente quando compromete a sobrevivência do cidadão
— Princípio da menor lesividade: entre duas sanções igualmente eficazes, deve-se escolher a menos gravosa
— Qualquer suspensão/cassação futura da CNH representaria lesão irreparável ao sustento do requerente

**IV — DAS NULIDADES DO AUTO DE INFRAÇÃO** (art. 280 e 281 CTB)
— Verificar cada requisito formal do art. 280 CTB (data, hora, local, placa, conduta, artigo, identificação do agente, assinatura)
— Citar art. 281 CTB: qualquer vício nos requisitos do art. 280 torna o auto nulo
— Se infração por equipamento: questionar validade da aferição/calibração conforme Resolução CONTRAN 798/2020 e portaria INMETRO; equipamento sem certificado válido invalida autuação
— Ausência ou insuficiência de prova fotográfica/fílmica (princípio da prova material)
— Requerer juntada de todos os documentos do auto (fotos, relatório do equipamento, certificado INMETRO, escala do agente)

**V — DO MÉRITO — FUNDAMENTOS JURÍDICOS** (desenvolva com profundidade cada tese indicada)
— Desenvolver todas as teses selecionadas com fundamentação EXTENSA e completa — cada tese deve ter ao menos 3 parágrafos
— CITAR OBRIGATORIAMENTE os seguintes artigos do CTB (Lei 9.503/97):
  · art. 256 CTB (espécies de penalidades: multa, suspensão, cassação, frequência a curso)
  · art. 257 CTB (responsabilidade do proprietário e do condutor)
  · art. 258 CTB (responsabilidade solidária)
  · art. 259 CTB (atenuantes e agravantes)
  · art. 261 CTB (penalidade de multa — critérios)
  · art. 262 CTB (suspensão do direito de dirigir)
  · art. 264 CTB (cassação — requisitos)
  · art. 265 CTB (advertência por escrito)
  · art. 267 CTB (conversão em advertência para infratores primários)
  · art. 280 CTB (requisitos formais do auto de infração — todos os incisos)
  · art. 281 CTB (nulidade do auto quando ausente qualquer requisito do art. 280)
  · art. 282 CTB (processo de aplicação das penalidades)
  · art. 283 CTB (notificação do autuado — prazos e formas)
  · art. 284 CTB (prazo de 15 dias para identificação do condutor)
  · art. 285 CTB (defesa prévia — direito do autuado e prazo de 30 dias)
  · art. 286 CTB (julgamento pela autoridade de trânsito)
  · art. 288 CTB (recurso à JARI — prazo e legitimidade)
  · art. 289 CTB (recurso ao CETRAN — segunda instância)
  · art. 290 CTB (efeito suspensivo dos recursos)
— CF/88 — citar integralmente: art. 5º caput, LIV (devido processo legal), LV (contraditório e ampla defesa), LVII (presunção de inocência), LVI (inadmissibilidade de provas ilícitas), LXXVIII (razoável duração do processo); art. 6º (direito social ao trabalho); art. 37 (legalidade administrativa); art. 170 (livre exercício de atividade econômica)
— Lei 9.784/1999 (processo administrativo federal): arts. 2º (princípios), 26 (notificação), 38 (instrução), 56 (recursos), 61 (efeito suspensivo), 64 (julgamento)
— Resolução CONTRAN: citar a Resolução CONTRAN específica do artigo infringido e questionar seu cumprimento pela autoridade autuadora
— Princípios constitucionais: in dubio pro reo, legalidade estrita, proporcionalidade, razoabilidade, motivação dos atos administrativos, presunção de inocência, contraditório, ampla defesa, dignidade da pessoa humana
— Citar pelo menos 3 decisões do STJ ou tribunais estaduais favoráveis ao contribuinte em casos análogos, com ementa resumida

**VI — DOS PEDIDOS** (em cascata, do mais ao menos amplo)
a) PRINCIPAL: Recebimento e conhecimento da presente {tipo_label.lower()}; cancelamento e arquivamento do Auto de Infração nº {auto_num}; declaração de nulidade de todos os efeitos
b) SUBSIDIÁRIO 1: Caso não acolhido, conversão da penalidade em advertência por escrito, com fundamento no art. 267 CTB, considerando ser o requerente primário e de bons antecedentes
c) SUBSIDIÁRIO 2: Caso não cabível a advertência, redução ao mínimo legal da penalidade
d) SUBSIDIÁRIO 3: Caso mantida a multa, concessão de parcelamento em até 12 parcelas mensais, conforme permite a legislação vigente
e) EM QUALQUER CASO: Suspensão imediata de todos os efeitos durante a tramitação (efeito suspensivo); não lançamento de pontos na CNH até decisão definitiva; notificação sobre o resultado no endereço cadastrado; juntada de todas as provas materiais (fotos, dados do equipamento, relatório da autuação)

**FECHO**
"Termos em que, pede e espera deferimento."
{cidade}, {hoje}.
{nome_req} — CPF: {cpf_req}

=== DIRETRIZES FINAIS ===
- Mínimo 1.800 palavras — seja EXTENSO, completo, não resuma nem abrevia
- Linguagem jurídica formal, sem coloquialismos
- Cite TODOS os artigos listados acima, com o texto ou ementa do dispositivo quando relevante
- Cada seção deve ter ao menos 2–3 parágrafos completos e terminar com conclusão favorável ao requerente
- Use negrito (*texto*) para termos jurídicos e nomes de artigos importantes
- SEMPRE mencione art. 5º LV CF/88 e art. 285 CTB ao menos duas vezes cada
- A petição deve demonstrar erudição jurídica — quanto mais fundamentação e citações, melhor

Redija a petição completa agora, seguindo rigorosamente a estrutura acima."""

    try:
        resp = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [
                    {'role': 'system', 'content': SYSTEM},
                    {'role': 'user',   'content': USER},
                ],
                'max_tokens': 4096,
                'temperature': 0.3,
            },
            timeout=120,
        )
        resp.raise_for_status()
        peticao_txt = resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        log.error(f'DefesaPro IA petição error: {e}')
        conn.close()
        return jsonify({'erro': f'Erro ao gerar petição: {e}'}), 500

    # Salva no banco
    pet_id = conn.execute(
        'INSERT INTO defesapro_peticoes (user_id,processo_id,tipo,conteudo,teses_json,created_at) VALUES (?,?,?,?,?,?)',
        (user_id, p['id'], tipo + '_ia', peticao_txt, _json_ia.dumps(teses_sel), datetime.now().isoformat())
    ).lastrowid
    conn.commit(); conn.close()

    return jsonify({'ok': True, 'peticao': peticao_txt, 'pet_id': pet_id})


@app.route('/defesapro/peticoes/<int:tid>/deletar', methods=['POST'])
@_defesa_login_required
def defesa_peticao_deletar(tid):
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    conn.execute('DELETE FROM defesapro_peticoes WHERE id=? AND user_id=?', (tid, user_id))
    conn.commit(); conn.close()
    return redirect('/defesapro/peticoes')


@app.route('/defesapro/peticoes/<int:tid>/imprimir')
@_defesa_login_required
def defesa_peticao_imprimir(tid):
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    pet = conn.execute(
        'SELECT t.*, p.placa, p.numero_auto, p.proprietario, p.artigo_ctb, p.descricao,'
        '       c.name AS cliente_nome'
        ' FROM defesapro_peticoes t'
        ' LEFT JOIN defesapro_processos p ON p.id=t.processo_id'
        ' LEFT JOIN defesapro_clientes  c ON c.id=p.cliente_id'
        ' WHERE t.id=? AND t.user_id=?',
        (tid, user_id)
    ).fetchone()
    conn.close()
    if not pet:
        return redirect('/defesapro/peticoes')
    escritorio = session.get('defesa_escritorio', '')
    return render_template('defesapro/peticao_imprimir.html',
                           pet=dict(pet), escritorio=escritorio,
                           hoje=datetime.now().strftime('%d/%m/%Y'))


# ── DefesaPro — Perfil ────────────────────────────────────────────────────────
@app.route('/defesapro/perfil', methods=['GET', 'POST'])
@_defesa_login_required
def defesa_perfil():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    u = conn.execute('SELECT * FROM defesapro_users WHERE id=?', (user_id,)).fetchone()
    if not u:
        conn.close(); return redirect('/defesapro/login')
    erro = sucesso = None
    if request.method == 'POST':
        acao = request.form.get('acao', 'dados')
        if acao == 'dados':
            name       = request.form.get('name', '').strip()
            phone      = request.form.get('phone', '').strip()
            escritorio = request.form.get('escritorio', '').strip()
            cidade     = request.form.get('cidade', '').strip()
            if not name:
                erro = 'Nome é obrigatório.'
            else:
                conn.execute(
                    'UPDATE defesapro_users SET name=?,phone=?,escritorio=?,cidade=? WHERE id=?',
                    (name, phone, escritorio, cidade, user_id)
                )
                conn.commit()
                session['defesa_user_name']  = name
                session['defesa_escritorio'] = escritorio or name
                sucesso = 'Dados atualizados com sucesso.'
                u = conn.execute('SELECT * FROM defesapro_users WHERE id=?', (user_id,)).fetchone()
        elif acao == 'senha':
            senha_atual = request.form.get('senha_atual', '')
            nova_senha  = request.form.get('nova_senha', '').strip()
            confirmar   = request.form.get('confirmar_senha', '').strip()
            if not u['password_hash'] or not check_password_hash(u['password_hash'], senha_atual):
                erro = 'Senha atual incorreta.'
            elif len(nova_senha) < 6:
                erro = 'A nova senha deve ter pelo menos 6 caracteres.'
            elif nova_senha != confirmar:
                erro = 'As senhas não coincidem.'
            else:
                conn.execute(
                    'UPDATE defesapro_users SET password_hash=? WHERE id=?',
                    (generate_password_hash(nova_senha), user_id)
                )
                conn.commit()
                sucesso = 'Senha alterada com sucesso.'
    conn.close()
    return render_template('defesapro/perfil.html', u=dict(u), erro=erro, sucesso=sucesso)


# ── DefesaPro — Monitor de E-mail (Premium) ──────────────────────────────────

def _defesa_premium_required(f):
    """Decorator: só plano premium acessa."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('defesa_user_id'):
            return redirect(url_for('defesa_login', next=request.path))
        if session.get('defesa_plan') != 'premium':
            return render_template('defesapro/premium_gate.html')
        return f(*args, **kwargs)
    return decorated


def _defesa_verificar_email(user_id):
    """
    Lê e-mails novos (últimas 24h) via IMAP, usa Groq para classificar
    se são relacionados a processos de trânsito e cria notificações.
    Retorna (total_novos, erros).
    """
    import imaplib, email as _email_lib, base64 as _b64, re as _re_em
    import json as _json_em
    from email.header import decode_header as _dh
    from datetime import datetime as _dt, timedelta as _td

    groq_key = os.environ.get('GROQ_API_KEY', '')
    conn = get_saas_db()

    cfg = conn.execute(
        'SELECT * FROM defesapro_email_config WHERE user_id=? AND ativo=1', (user_id,)
    ).fetchone()
    if not cfg:
        conn.close()
        return 0, 'Configuração de e-mail não encontrada'

    try:
        senha = _b64.b64decode(cfg['senha_b64']).decode()
    except Exception:
        conn.close()
        return 0, 'Erro ao decodificar senha'

    # Conecta IMAP SSL
    try:
        mail = imaplib.IMAP4_SSL(cfg['imap_host'], cfg['imap_port'])
        mail.login(cfg['email_addr'], senha)
        mail.select('INBOX')
    except Exception as e:
        conn.close()
        return 0, f'Erro ao conectar: {e}'

    # Busca e-mails das últimas 24h
    since_date = (_dt.now() - _td(days=1)).strftime('%d-%b-%Y')
    try:
        _, msg_ids = mail.search(None, f'(SINCE {since_date} UNSEEN)')
    except Exception:
        _, msg_ids = mail.search(None, f'SINCE {since_date}')

    ids = msg_ids[0].split() if msg_ids and msg_ids[0] else []
    novos = 0

    # Processos do usuário para tentar vincular
    processos = [dict(r) for r in conn.execute(
        'SELECT id, placa, numero_auto FROM defesapro_processos WHERE user_id=?', (user_id,)
    ).fetchall()]

    for eid in ids[-20:]:  # máx 20 por vez
        try:
            _, data = mail.fetch(eid, '(RFC822)')
            raw = data[0][1]
            msg = _email_lib.message_from_bytes(raw)

            # Extrai assunto
            subj_raw = msg.get('Subject', '')
            subj_parts = _dh(subj_raw)
            subject = ''
            for part, enc in subj_parts:
                if isinstance(part, bytes):
                    subject += part.decode(enc or 'utf-8', errors='replace')
                else:
                    subject += str(part)

            from_addr = msg.get('From', '')

            # Extrai corpo texto
            body = ''
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct == 'text/plain':
                        try:
                            body += part.get_payload(decode=True).decode('utf-8', errors='replace')
                        except Exception:
                            pass
                        if len(body) > 3000:
                            break
            else:
                try:
                    body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
                except Exception:
                    body = ''

            if not body.strip() and not subject.strip():
                continue

            # Groq classifica
            if groq_key:
                prompt = (
                    'Analise este e-mail e determine se é relacionado a um auto de infração, '
                    'defesa de multa, recurso, JARI, CETRAN ou processo de trânsito.\n\n'
                    f'De: {from_addr}\nAssunto: {subject}\nConteúdo:\n{body[:2000]}\n\n'
                    'Retorne SOMENTE este JSON (sem markdown):\n'
                    '{"relacionado":true,"tipo":"deferido|indeferido|solicitacao_documento|julgamento|audiencia|recurso|outro","placa":"ou null","numero_auto":"ou null","orgao":"nome do orgao ou null","resumo":"1 frase resumindo o que o email diz"}'
                )
                try:
                    resp = requests.post(
                        'https://api.groq.com/openai/v1/chat/completions',
                        headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
                        json={
                            'model': 'llama-3.3-70b-versatile',
                            'messages': [{'role': 'user', 'content': prompt}],
                            'max_tokens': 256, 'temperature': 0.1,
                        },
                        timeout=30,
                    )
                    txt = resp.json()['choices'][0]['message']['content'].strip()
                    m = _re_em.search(r'\{[\s\S]*\}', txt)
                    info = _json_em.loads(m.group()) if m else {}
                except Exception:
                    info = {}
            else:
                # Sem Groq: heurística por palavras-chave
                kw = ['auto de infração', 'multa', 'jari', 'cetran', 'detran', 'defesa', 'recurso',
                      'autuação', 'penalidade', 'deferido', 'indeferido', 'notificação']
                relacionado = any(k in (subject + body).lower() for k in kw)
                info = {'relacionado': relacionado, 'tipo': 'outro',
                        'placa': None, 'numero_auto': None, 'orgao': None,
                        'resumo': subject[:120]}

            if not info.get('relacionado'):
                continue

            # Tenta vincular a um processo existente
            processo_id = None
            placa_ext = (info.get('placa') or '').upper().replace('-', '').replace(' ', '')
            auto_ext   = (info.get('numero_auto') or '').strip()
            for p in processos:
                p_placa = (p['placa'] or '').upper().replace('-', '').replace(' ', '')
                p_auto  = (p['numero_auto'] or '').strip()
                if placa_ext and p_placa and placa_ext in p_placa:
                    processo_id = p['id']; break
                if auto_ext and p_auto and auto_ext in p_auto:
                    processo_id = p['id']; break

            # Emojis por tipo
            emoji_map = {
                'deferido': '✅', 'indeferido': '❌', 'solicitacao_documento': '📎',
                'julgamento': '⚖️', 'audiencia': '📅', 'recurso': '📋', 'outro': '📧',
            }
            tipo = info.get('tipo', 'outro')
            emoji = emoji_map.get(tipo, '📧')
            titulo = f'{emoji} {subject[:80]}' if subject else f'{emoji} Novo e-mail de processo'

            now = datetime.now().isoformat()
            conn.execute(
                '''INSERT INTO defesapro_notificacoes
                   (user_id,tipo,titulo,corpo,processo_id,lida,email_de,email_assunto,created_at)
                   VALUES (?,?,?,?,?,0,?,?,?)''',
                (user_id, tipo, titulo, info.get('resumo', body[:300]),
                 processo_id, from_addr, subject, now)
            )
            conn.commit()
            novos += 1

        except Exception as e:
            log.error(f'DefesaPro email parse error: {e}')
            continue

    try:
        mail.logout()
    except Exception:
        pass

    # Atualiza último check
    conn.execute(
        'UPDATE defesapro_email_config SET ultimo_check=?, total_lidos=total_lidos+? WHERE user_id=?',
        (datetime.now().isoformat(), novos, user_id)
    )
    conn.commit()
    conn.close()
    return novos, None


@app.route('/defesapro/email-config', methods=['GET', 'POST'])
@_defesa_premium_required
def defesa_email_config():
    import base64 as _b64c
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    cfg = conn.execute('SELECT * FROM defesapro_email_config WHERE user_id=?', (user_id,)).fetchone()
    erro = sucesso = None

    if request.method == 'POST':
        acao = request.form.get('acao', 'salvar')
        if acao == 'verificar':
            conn.close()
            novos, err = _defesa_verificar_email(user_id)
            if err:
                erro = f'Erro: {err}'
            else:
                sucesso = f'Verificação concluída. {novos} novo(s) e-mail(s) de processo encontrado(s).'
            conn = get_saas_db()
            cfg = conn.execute('SELECT * FROM defesapro_email_config WHERE user_id=?', (user_id,)).fetchone()
        else:
            host   = request.form.get('imap_host', 'imap.gmail.com').strip()
            port   = int(request.form.get('imap_port', 993) or 993)
            email_addr = request.form.get('email_addr', '').strip()
            senha  = request.form.get('senha', '').strip()
            ativo  = 1 if request.form.get('ativo') else 0

            if not email_addr:
                erro = 'E-mail é obrigatório.'
            else:
                senha_b64 = _b64c.b64encode(senha.encode()).decode() if senha else (cfg['senha_b64'] if cfg else '')
                now = datetime.now().isoformat()
                if cfg:
                    conn.execute(
                        'UPDATE defesapro_email_config SET imap_host=?,imap_port=?,email_addr=?,senha_b64=?,ativo=? WHERE user_id=?',
                        (host, port, email_addr, senha_b64, ativo, user_id)
                    )
                else:
                    conn.execute(
                        '''INSERT INTO defesapro_email_config
                           (user_id,imap_host,imap_port,email_addr,senha_b64,ativo,created_at)
                           VALUES (?,?,?,?,?,?,?)''',
                        (user_id, host, port, email_addr, senha_b64, ativo, now)
                    )
                conn.commit()
                sucesso = 'Configuração salva com sucesso.'
                cfg = conn.execute('SELECT * FROM defesapro_email_config WHERE user_id=?', (user_id,)).fetchone()

    conn.close()
    default_cfg = {'imap_host': 'imap.gmail.com', 'imap_port': 993, 'email_addr': '',
                   'ativo': 1, 'ultimo_check': '', 'total_lidos': 0}
    config = dict(cfg) if cfg else default_cfg
    return render_template('defesapro/email_config.html',
                           config=config, msg=sucesso or erro,
                           erro=bool(erro))


@app.route('/defesapro/notificacoes')
@_defesa_login_required
def defesa_notificacoes():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    notificacoes = [dict(r) for r in conn.execute(
        'SELECT * FROM defesapro_notificacoes WHERE user_id=? ORDER BY created_at DESC LIMIT 100',
        (user_id,)
    ).fetchall()]
    nao_lidas = sum(1 for n in notificacoes if not n['lida'])
    conn.close()
    return render_template('defesapro/notificacoes.html', notificacoes=notificacoes, nao_lidas=nao_lidas)


@app.route('/defesapro/notificacoes/marcar-todas-lidas', methods=['POST'])
@_defesa_login_required
def defesa_notificacoes_marcar_lidas():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    conn.execute('UPDATE defesapro_notificacoes SET lida=1 WHERE user_id=? AND lida=0', (user_id,))
    conn.commit(); conn.close()
    return redirect('/defesapro/notificacoes')


@app.route('/defesapro/notificacoes/contagem')
@_defesa_login_required
def defesa_notificacoes_contagem():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    n = conn.execute(
        'SELECT COUNT(*) FROM defesapro_notificacoes WHERE user_id=? AND lida=0', (user_id,)
    ).fetchone()[0]
    conn.close()
    return jsonify({'nao_lidas': n})


@app.route('/defesapro/notificacoes/<int:nid>/deletar', methods=['POST'])
@_defesa_login_required
def defesa_notificacao_deletar(nid):
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    conn.execute('DELETE FROM defesapro_notificacoes WHERE id=? AND user_id=?', (nid, user_id))
    conn.commit(); conn.close()
    return redirect('/defesapro/notificacoes')


# ── DefesaPro — Check diário de e-mail (chamado pelo admin/cron) ──────────────
@app.route('/admin/defesapro/email-check-diario', methods=['POST'])
@_saas_admin_required
def saas_defesa_email_check_diario():
    """Dispara verificação de e-mail para todos os usuários Premium ativos."""
    conn = get_saas_db()
    premiums = [r['user_id'] for r in conn.execute(
        '''SELECT ec.user_id FROM defesapro_email_config ec
           JOIN defesapro_users u ON u.id=ec.user_id
           WHERE ec.ativo=1 AND u.active=1 AND u.plan='premium' '''
    ).fetchall()]
    conn.close()
    resultados = []
    for uid in premiums:
        novos, err = _defesa_verificar_email(uid)
        resultados.append({'user_id': uid, 'novos': novos, 'erro': err})
    return jsonify({'ok': True, 'processados': len(premiums), 'resultados': resultados})


# ── DefesaPro — Prazos ────────────────────────────────────────────────────────
@app.route('/defesapro/prazos')
@_defesa_login_required
def defesa_prazos():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    hoje   = date.today().isoformat()
    em7    = (date.today() + timedelta(days=7)).isoformat()
    em30   = (date.today() + timedelta(days=30)).isoformat()
    _prazos_sql = '''SELECT p.*, c.name AS cliente_nome, c.phone AS cliente_phone
           FROM defesapro_processos p
           LEFT JOIN defesapro_clientes c ON c.id=p.cliente_id
           WHERE p.user_id=?'''
    vencidos = [dict(r) for r in conn.execute(
        _prazos_sql + " AND p.prazo_defesa!='' AND p.prazo_defesa<? AND p.status='aberto' ORDER BY p.prazo_defesa ASC",
        (user_id, hoje)
    ).fetchall()]
    urgentes = [dict(r) for r in conn.execute(
        _prazos_sql + " AND p.prazo_defesa BETWEEN ? AND ? AND p.status='aberto' ORDER BY p.prazo_defesa ASC",
        (user_id, hoje, em7)
    ).fetchall()]
    proximos = [dict(r) for r in conn.execute(
        _prazos_sql + " AND p.prazo_defesa > ? AND p.prazo_defesa <= ? AND p.status='aberto' ORDER BY p.prazo_defesa ASC",
        (user_id, em7, em30)
    ).fetchall()]
    sem_prazo = [dict(r) for r in conn.execute(
        _prazos_sql + " AND (p.prazo_defesa='' OR p.prazo_defesa IS NULL) AND p.status='aberto' ORDER BY p.created_at DESC",
        (user_id,)
    ).fetchall()]
    conn.close()
    return render_template('defesapro/prazos.html',
                           vencidos=vencidos, urgentes=urgentes,
                           proximos=proximos, sem_prazo=sem_prazo,
                           ctb_status=CTB_STATUS, ctb_fases=CTB_FASES, hoje=hoje)


# ── DefesaPro — Financeiro ────────────────────────────────────────────────────
@app.route('/defesapro/financeiro', methods=['GET', 'POST'])
@_defesa_login_required
def defesa_financeiro():
    user_id = session['defesa_user_id']
    conn = get_saas_db()
    erro = sucesso = None
    if request.method == 'POST':
        acao = request.form.get('acao', '')
        if acao == 'add':
            desc       = request.form.get('descricao', '').strip()
            valor      = float(request.form.get('valor', 0) or 0)
            tipo       = request.form.get('tipo', 'honorario')
            data_pg    = request.form.get('data', datetime.now().strftime('%Y-%m-%d'))
            processo_id = request.form.get('processo_id') or None
            pago       = 1 if request.form.get('pago') else 0
            if valor <= 0:
                erro = 'Informe um valor maior que zero.'
            else:
                conn.execute(
                    'INSERT INTO defesapro_financeiro (user_id,processo_id,tipo,descricao,valor,pago,data,created_at) VALUES (?,?,?,?,?,?,?,?)',
                    (user_id, processo_id, tipo, desc, valor, pago, data_pg, datetime.now().isoformat())
                )
                conn.commit()
                sucesso = 'Lançamento registrado.'
        elif acao == 'pagar':
            fid = int(request.form.get('fid', 0))
            conn.execute('UPDATE defesapro_financeiro SET pago=1 WHERE id=? AND user_id=?', (fid, user_id))
            conn.commit()
            sucesso = 'Marcado como pago.'
        elif acao == 'deletar':
            fid = int(request.form.get('fid', 0))
            conn.execute('DELETE FROM defesapro_financeiro WHERE id=? AND user_id=?', (fid, user_id))
            conn.commit()
            sucesso = 'Lançamento removido.'
    mes_atual = datetime.now().strftime('%Y-%m')
    total_mes   = conn.execute("SELECT COALESCE(SUM(valor),0) FROM defesapro_financeiro WHERE user_id=? AND pago=1 AND strftime('%Y-%m',data)=?", (user_id, mes_atual)).fetchone()[0]
    pendente    = conn.execute("SELECT COALESCE(SUM(valor),0) FROM defesapro_financeiro WHERE user_id=? AND pago=0", (user_id,)).fetchone()[0]
    total_geral = conn.execute("SELECT COALESCE(SUM(valor),0) FROM defesapro_financeiro WHERE user_id=? AND pago=1", (user_id,)).fetchone()[0]
    lancamentos = [dict(r) for r in conn.execute(
        '''SELECT f.*, p.placa, p.numero_auto FROM defesapro_financeiro f
           LEFT JOIN defesapro_processos p ON p.id=f.processo_id
           WHERE f.user_id=? ORDER BY f.data DESC, f.id DESC LIMIT 100''',
        (user_id,)
    ).fetchall()]
    processos = [dict(r) for r in conn.execute(
        'SELECT id, placa, numero_auto FROM defesapro_processos WHERE user_id=? ORDER BY created_at DESC',
        (user_id,)
    ).fetchall()]
    conn.close()
    return render_template('defesapro/financeiro.html',
                           lancamentos=lancamentos, processos=processos,
                           total_mes=total_mes, pendente=pendente, total_geral=total_geral,
                           erro=erro, sucesso=sucesso)


# ── DefesaPro — Admin: definir senha do usuário ────────────────────────────────

@app.route('/admin/defesapro/user/<int:user_id>/set-senha', methods=['POST'])
@_saas_admin_required
def saas_defesa_set_senha(user_id):
    data  = request.get_json() or {}
    senha = (data.get('senha') or '').strip()
    if len(senha) < 6:
        return jsonify({'success': False, 'error': 'Senha deve ter pelo menos 6 caracteres'})
    h = generate_password_hash(senha)
    conn = get_saas_db()
    conn.execute('UPDATE defesapro_users SET password_hash=? WHERE id=?', (h, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/despachante-info')
def despachante_landing():
    return render_template('despachante/landing_publica.html')

@app.route('/kids')
def kids():
    return render_template('kids/landing.html', stats=stats())

@app.route('/sala')
def sala():
    return render_template('sala/landing.html', stats=stats())

@app.route('/agenda')
def agenda():
    return render_template('agenda/landing.html')

@app.route('/alerta')
def alerta():
    return render_template('alerta/landing.html', plans=ALERTA_PLANS)


# ══════════════════════════════════════════════════════════════════════════
#  AGENDA SC — SaaS de Agendamento Online
# ══════════════════════════════════════════════════════════════════════════

def _agenda_send_whatsapp(phone: str, message: str, instance: str) -> bool:
    """Envia mensagem WhatsApp via Evolution API para o Agenda SC."""
    evo_url = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    if not evo_url or not evo_key or not instance or not phone:
        return False
    digits = ''.join(c for c in phone if c.isdigit())
    if not digits:
        return False
    if not digits.startswith('55'):
        digits = '55' + digits
    try:
        import requests as _req
        resp = _req.post(
            f'{evo_url}/message/sendText/{instance}',
            json={'number': digits + '@s.whatsapp.net', 'text': message},
            headers={'apikey': evo_key},
            timeout=10
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        log.warning(f'agenda_whatsapp error: {e}')
        return False


def _agenda_upsert_customer(conn, business_id: int, name: str, phone: str):
    """Cria ou atualiza cliente no histórico."""
    from datetime import datetime as _dt
    conn.execute('''
        INSERT INTO agenda_customers (business_id, name, phone, total_visits, created_at)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(business_id, phone) DO UPDATE SET name=excluded.name
    ''', (business_id, name, phone, _dt.now().isoformat()))
    conn.commit()

@app.route('/agenda/cadastro', methods=['GET', 'POST'])
def agenda_cadastro():
    error = None
    if request.method == 'POST':
        name          = request.form.get('name', '').strip()
        owner_name    = request.form.get('owner_name', '').strip()
        phone         = request.form.get('phone', '').strip()
        email         = request.form.get('email', '').strip()
        business_type = request.form.get('business_type', 'outros')
        password      = request.form.get('password', '').strip()
        cpf_cnpj      = request.form.get('cpf_cnpj', '').strip()
        cpf_digits    = ''.join(c for c in cpf_cnpj if c.isdigit())

        if not all([name, owner_name, phone, password, cpf_cnpj]):
            error = 'Preencha todos os campos obrigatórios.'
        elif len(password) < 6:
            error = 'A senha precisa ter pelo menos 6 caracteres.'
        elif len(cpf_digits) not in (11, 14):
            error = 'CPF deve ter 11 dígitos ou CNPJ 14 dígitos.'
        else:
            # Normaliza telefone para checar duplicata
            phone_digits = ''.join(c for c in phone if c.isdigit())
            conn = get_saas_db()
            _wl = _is_whitelisted(phone_digits, email.lower() if email else '')
            existing_phone = (not _wl) and conn.execute(
                "SELECT id FROM agenda_businesses WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ?",
                (phone_digits,)
            ).fetchone()
            existing_cpf = (not _wl) and conn.execute(
                "SELECT id FROM agenda_businesses WHERE replace(replace(replace(cpf_cnpj,'.',''),'-',''),'/','') = ?",
                (cpf_digits,)
            ).fetchone()
            if existing_phone:
                conn.close()
                error = 'Este WhatsApp já possui uma conta. Faça login para acessar sua agenda.'
            elif existing_cpf:
                conn.close()
                error = 'CPF/CNPJ já possui uma conta cadastrada. Faça login ou entre em contato.'
            else:
                slug = _slugify(name) or 'negocio'
                base_slug, counter = slug, 1
                while conn.execute('SELECT id FROM agenda_businesses WHERE slug=?', (slug,)).fetchone():
                    slug = f'{base_slug}-{counter}'; counter += 1
                trial_ends = (datetime.now() + timedelta(days=7)).isoformat()
                try:
                    conn.execute('''
                        INSERT INTO agenda_businesses
                        (name, slug, owner_name, phone, email, business_type, password_hash, cpf_cnpj, active, created_at, trial_ends)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ''', (name, slug, owner_name, phone, email, business_type,
                          generate_password_hash(password), cpf_cnpj, datetime.now().isoformat(), trial_ends))
                    conn.commit()
                    biz = conn.execute('SELECT * FROM agenda_businesses WHERE slug=?', (slug,)).fetchone()
                    conn.close()
                    session['agenda_business_id']   = biz['id']
                    session['agenda_business_slug'] = biz['slug']
                    session['agenda_business_name'] = biz['name']
                    # Email de boas-vindas
                    if email:
                        _enviar_email(
                            email,
                            '📅 Bem-vindo ao Agenda SC — Seu trial de 7 dias começou!',
                            _email_boas_vindas(
                                'Agenda SC', '📅', '#22c55e',
                                owner_name.split()[0],
                                trial_ends,
                                'https://4kitem.com.br/agenda/painel',
                                'Sistema de agendamentos online para o seu negócio. Configure seus serviços, horários e comece a receber agendamentos agora.'
                            )
                        )
                    return redirect('/agenda/painel')
                except Exception as e:
                    conn.close()
                    log.error(f'Agenda cadastro error: {e}')
                    error = 'Erro ao cadastrar. Tente novamente.'

    return render_template('agenda/cadastro.html', error=error, business_types=BUSINESS_TYPES)


@app.route('/agenda/entrar', methods=['GET', 'POST'])
def agenda_entrar():
    error = None
    if request.method == 'POST':
        phone_raw = request.form.get('phone', '').strip()
        password  = request.form.get('password', '').strip()
        # Normaliza: só dígitos para comparação robusta
        phone_digits = ''.join(c for c in phone_raw if c.isdigit())
        conn = get_saas_db()
        # Busca normalizando o telefone armazenado também
        biz = conn.execute('''
            SELECT * FROM agenda_businesses
            WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ?
            AND active=1
        ''', (phone_digits,)).fetchone()
        conn.close()
        if biz and check_password_hash(biz['password_hash'], password):
            session['agenda_business_id']   = biz['id']
            session['agenda_business_slug'] = biz['slug']
            session['agenda_business_name'] = biz['name']
            return redirect('/agenda/painel')
        error = 'Telefone ou senha incorretos. Verifique o número e a senha cadastrados.'
    return render_template('agenda/entrar.html', error=error)


@app.route('/agenda/sair')
def agenda_sair():
    for k in ('agenda_business_id', 'agenda_business_slug', 'agenda_business_name'):
        session.pop(k, None)
    return redirect('/agenda')


# ── AgendaSC — Recuperação de senha ──────────────────────────────────────────
@app.route('/agenda/esqueci-senha', methods=['GET', 'POST'])
def agenda_esqueci_senha():
    enviado = False
    codigo_tela = None
    erro = None
    if request.method == 'POST':
        phone_raw = request.form.get('phone', '').strip()
        phone_digits = ''.join(c for c in phone_raw if c.isdigit())
        conn = get_saas_db()
        biz = conn.execute(
            "SELECT * FROM agenda_businesses WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ?",
            (phone_digits,)
        ).fetchone()
        if not biz:
            erro = 'Número não encontrado. Verifique o WhatsApp cadastrado.'
            conn.close()
        else:
            codigo = str(random.randint(100000, 999999))
            expires = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute('UPDATE agenda_businesses SET reset_token=?, reset_expires=? WHERE id=?',
                         (codigo, expires, biz['id']))
            conn.commit(); conn.close()
            ok = False
            if biz['email']:
                html_email = f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
                  <div style="font-size:32px;margin-bottom:8px">📅</div>
                  <h2 style="color:#27ae60">Recuperação de senha — Agenda SC</h2>
                  <p>Olá, <strong>{biz['owner_name'].split()[0]}</strong>!</p>
                  <p>Seu código de recuperação é:</p>
                  <div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#27ae60;
                              background:#f0fdf4;padding:20px;border-radius:12px;text-align:center;
                              margin:20px 0">{codigo}</div>
                  <p style="color:#666;font-size:13px">Válido por 2 horas.</p>
                </div>"""
                ok = _enviar_email(biz['email'], 'Código de recuperação — Agenda SC', html_email)
            enviado = True
            if not ok:
                codigo_tela = codigo
    return render_template('agenda/esqueci_senha.html',
                           enviado=enviado, codigo_tela=codigo_tela, erro=erro)


@app.route('/agenda/redefinir-senha', methods=['GET', 'POST'])
def agenda_redefinir_senha():
    sucesso = False
    erro = None
    if request.method == 'POST':
        phone_raw = request.form.get('phone', '').strip()
        phone_digits = ''.join(c for c in phone_raw if c.isdigit())
        codigo = request.form.get('codigo', '').strip()
        nova = request.form.get('nova_senha', '')
        if len(nova) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        else:
            conn = get_saas_db()
            biz = conn.execute(
                "SELECT * FROM agenda_businesses WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ?",
                (phone_digits,)
            ).fetchone()
            if not biz or biz['reset_token'] != codigo:
                erro = 'Código inválido. Verifique o número e o código.'
                conn.close()
            elif biz['reset_expires'] and datetime.fromisoformat(biz['reset_expires']) < datetime.now():
                erro = 'Código expirado. Solicite um novo.'
                conn.close()
            else:
                conn.execute('UPDATE agenda_businesses SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?',
                             (generate_password_hash(nova), biz['id']))
                conn.commit(); conn.close()
                sucesso = True
    return render_template('agenda/redefinir_senha.html', sucesso=sucesso, erro=erro)


@app.route('/agenda/painel')
@_agenda_login_required
def agenda_painel():
    biz_id = session['agenda_business_id']
    conn   = get_saas_db()
    biz    = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone())
    services = [dict(r) for r in conn.execute(
        'SELECT * FROM agenda_services WHERE business_id=? AND active=1 ORDER BY name', (biz_id,)
    ).fetchall()]
    availability = [dict(r) for r in conn.execute(
        'SELECT * FROM agenda_availability WHERE business_id=? ORDER BY weekday', (biz_id,)
    ).fetchall()]
    today = datetime.now().strftime('%Y-%m-%d')
    appointments = [dict(r) for r in conn.execute('''
        SELECT a.*, COALESCE(s.name, 'Serviço') as service_name,
               COALESCE(s.duration_minutes, 60) as duration_minutes,
               COALESCE(s.price, 0) as price,
               COALESCE(p.name, a.professional_name, '') as professional_name
        FROM agenda_appointments a
        LEFT JOIN agenda_services s ON a.service_id = s.id
        LEFT JOIN agenda_professionals p ON a.professional_id = p.id
        WHERE a.business_id=? AND a.appointment_date >= ?
        ORDER BY a.appointment_date, a.appointment_time
    ''', (biz_id, today)).fetchall()]
    # Stats rápidas
    hoje_count = conn.execute(
        "SELECT COUNT(*) FROM agenda_appointments WHERE business_id=? AND appointment_date=? AND status!='cancelled'",
        (biz_id, today)
    ).fetchone()[0]
    mes_str = datetime.now().strftime('%Y-%m')
    receita_mes = conn.execute('''
        SELECT COALESCE(SUM(s.price),0) FROM agenda_appointments a
        LEFT JOIN agenda_services s ON a.service_id=s.id
        WHERE a.business_id=? AND strftime('%Y-%m',a.appointment_date)=? AND a.status='done'
    ''', (biz_id, mes_str)).fetchone()[0]
    total_clientes = conn.execute(
        'SELECT COUNT(*) FROM agenda_customers WHERE business_id=?', (biz_id,)
    ).fetchone()[0]
    # Setup flags para onboarding
    tem_servicos = len(services) > 0
    tem_horarios = conn.execute(
        'SELECT COUNT(*) FROM agenda_availability WHERE business_id=? AND active=1', (biz_id,)
    ).fetchone()[0] > 0
    tem_profissionais = conn.execute(
        'SELECT COUNT(*) FROM agenda_professionals WHERE business_id=? AND active=1', (biz_id,)
    ).fetchone()[0] > 0
    # Horas economizadas (total atendimentos × duração média)
    total_atendimentos = conn.execute(
        "SELECT COUNT(*) FROM agenda_appointments WHERE business_id=? AND status='done'", (biz_id,)
    ).fetchone()[0]
    avg_dur = conn.execute(
        "SELECT COALESCE(AVG(s.duration_minutes),60) FROM agenda_appointments a "
        "LEFT JOIN agenda_services s ON a.service_id=s.id "
        "WHERE a.business_id=? AND a.status='done'", (biz_id,)
    ).fetchone()[0] or 60
    horas_economizadas = round(total_atendimentos * avg_dur / 60, 1)
    conn.close()
    # Verifica trial
    trial_ends_str = biz.get('trial_ends', '')
    trial_expired  = bool(trial_ends_str and trial_ends_str < datetime.now().isoformat())
    return render_template('agenda/painel.html',
                           biz=biz, services=services,
                           availability=availability,
                           appointments=appointments,
                           today=today,
                           weekday_names=WEEKDAY_NAMES,
                           business_types=BUSINESS_TYPES,
                           hoje_count=hoje_count,
                           receita_mes=round(receita_mes, 2),
                           total_clientes=total_clientes,
                           tem_servicos=tem_servicos,
                           tem_horarios=tem_horarios,
                           tem_profissionais=tem_profissionais,
                           total_atendimentos=total_atendimentos,
                           horas_economizadas=horas_economizadas,
                           trial_expired=trial_expired,
                           trial_ends=trial_ends_str)


@app.route('/agenda/painel/servico/add', methods=['POST'])
@_agenda_login_required
def agenda_add_service():
    biz_id   = session['agenda_business_id']
    name     = request.form.get('name', '').strip()
    duration = request.form.get('duration', '60')
    price    = request.form.get('price', '0').replace(',', '.')
    if not name:
        return jsonify({'success': False, 'error': 'Nome obrigatório'})
    try:
        conn = get_saas_db()
        cur = conn.execute('''
            INSERT INTO agenda_services (business_id, name, duration_minutes, price, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
        ''', (biz_id, name, int(duration), float(price or 0), datetime.now().isoformat()))
        conn.commit()
        svc_id = cur.lastrowid
        conn.close()
        return jsonify({'success': True, 'id': svc_id, 'name': name,
                        'duration': int(duration), 'price': float(price or 0)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/agenda/painel/servico/<int:svc_id>/delete', methods=['POST'])
@_agenda_login_required
def agenda_delete_service(svc_id):
    biz_id = session['agenda_business_id']
    conn = get_saas_db()
    conn.execute('UPDATE agenda_services SET active=0 WHERE id=? AND business_id=?', (svc_id, biz_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/agenda/painel/horario/save', methods=['POST'])
@_agenda_login_required
def agenda_save_horario():
    biz_id = session['agenda_business_id']
    data   = request.get_json() or {}
    conn   = get_saas_db()
    conn.execute('DELETE FROM agenda_availability WHERE business_id=?', (biz_id,))
    for item in data.get('availability', []):
        wday = item.get('weekday')
        s    = item.get('start_time', '')
        e    = item.get('end_time', '')
        if wday is not None and s and e:
            conn.execute('''
                INSERT INTO agenda_availability (business_id, weekday, start_time, end_time, active)
                VALUES (?, ?, ?, ?, 1)
            ''', (biz_id, wday, s, e))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/agenda/painel/agendamento/<int:appt_id>/<action>', methods=['POST'])
@_agenda_login_required
def agenda_appt_action(appt_id, action):
    biz_id = session['agenda_business_id']
    status_map = {'confirmar': 'confirmed', 'cancelar': 'cancelled', 'concluir': 'done'}
    new_status = status_map.get(action)
    if not new_status:
        return jsonify({'success': False, 'error': 'Ação inválida'})
    conn = get_saas_db()
    appt = conn.execute('''
        SELECT a.*, COALESCE(s.name,'Serviço') as service_name, COALESCE(s.price,0) as price
        FROM agenda_appointments a
        LEFT JOIN agenda_services s ON a.service_id = s.id
        WHERE a.id=? AND a.business_id=?
    ''', (appt_id, biz_id)).fetchone()
    if not appt:
        conn.close()
        return jsonify({'success': False, 'error': 'Não encontrado'})
    conn.execute('UPDATE agenda_appointments SET status=? WHERE id=? AND business_id=?',
                 (new_status, appt_id, biz_id))
    milestone_visits = None
    if new_status == 'done':
        conn.execute('''
            UPDATE agenda_customers
            SET total_visits = total_visits + 1,
                total_spent  = total_spent + ?,
                last_visit   = ?
            WHERE business_id=? AND phone=?
        ''', (appt['price'], datetime.now().date().isoformat(),
              biz_id, appt['customer_phone']))
        conn.commit()
        # Verifica marco de visitas
        row_v = conn.execute(
            'SELECT total_visits FROM agenda_customers WHERE business_id=? AND phone=?',
            (biz_id, appt['customer_phone'])
        ).fetchone()
        if row_v and row_v['total_visits'] in (5, 10, 25, 50, 100):
            milestone_visits = row_v['total_visits']
    else:
        conn.commit()

    # WhatsApp automático
    biz = conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone()
    conn.close()
    if biz and biz['mandazap_ativo'] and biz['mandazap_instance']:
        if new_status == 'confirmed':
            tpl = biz['msg_confirmacao'] or (
                f"Olá {{nome}}! ✅\n\nSeu agendamento de *{{servico}}* em *{{data}}* às *{{hora}}* foi *confirmado*!\n\n"
                f"Te esperamos em 🏢 {{negocio}}."
            )
        elif new_status == 'cancelled':
            tpl = biz['msg_cancelamento'] or (
                f"Olá {{nome}}, infelizmente seu agendamento de *{{servico}}* foi *cancelado*. 😔\n\n"
                f"Entre em contato para reagendar."
            )
        elif new_status == 'done':
            # Pedido de avaliação pós-atendimento
            try:
                _msg_aval = biz['msg_avaliacao']
            except Exception:
                _msg_aval = ''
            tpl = (_msg_aval or
                   "Olá {nome}! 😊\n\n"
                   "Foi um prazer te atender hoje em *{negocio}*! 🙌\n\n"
                   "Sua opinião é muito importante para nós. "
                   "Que tal deixar uma avaliação rápida? ⭐\n\n"
                   "Conta pra gente: como foi a experiência?")
        else:
            tpl = None
        if tpl:
            msg = (tpl
                   .replace('{nome}', appt['customer_name'].split()[0])
                   .replace('{servico}', appt['service_name'])
                   .replace('{data}', appt['appointment_date'])
                   .replace('{hora}', appt['appointment_time'])
                   .replace('{negocio}', biz['name']))
            _agenda_send_whatsapp(appt['customer_phone'], msg, biz['mandazap_instance'])

    # 🏆 Marco de conquista (5ª, 10ª, 25ª... visita)
    if milestone_visits and biz and biz['mandazap_ativo'] and biz['mandazap_instance']:
        _nome_parts = (appt['customer_name'] or '').split()
        nome = _nome_parts[0] if _nome_parts else 'Cliente'
        marcos = {
            5:   ('🥈', f'Você já é um cliente especial! Obrigado por confiar na gente. 💚'),
            10:  ('🥇', f'10 visitas! Você já faz parte da família! 🎉'),
            25:  ('💎', f'25 visitas! Incrível! Você é nosso cliente VIP! 👑'),
            50:  ('🏆', f'50 visitas! Você é uma lenda! Muito obrigado por tudo! 🙌'),
            100: ('👑', f'100 visitas! Não temos nem palavras... Obrigado de coração! ❤️'),
        }
        emoji, texto = marcos.get(milestone_visits, ('⭐', f'{milestone_visits}ª visita! Obrigado!'))
        msg_marco = (
            f"{emoji} Parabéns, {nome}!\n\n"
            f"Você acaba de completar sua *{milestone_visits}ª visita* em *{biz['name']}*!\n\n"
            f"{texto}"
        )
        _agenda_send_whatsapp(appt['customer_phone'], msg_marco, biz['mandazap_instance'])

    return jsonify({'success': True, 'status': new_status})


@app.route('/agenda/painel/agendamento/<int:appt_id>/pagar', methods=['POST'])
@_agenda_login_required
def agenda_registrar_pagamento(appt_id):
    biz_id = session['agenda_business_id']
    data   = request.get_json(silent=True) or {}
    amount = float(data.get('amount', 0) or 0)
    method = data.get('method', 'dinheiro')
    conn   = get_saas_db()
    appt   = conn.execute('SELECT * FROM agenda_appointments WHERE id=? AND business_id=?',
                          (appt_id, biz_id)).fetchone()
    if not appt:
        conn.close()
        return jsonify({'success': False, 'error': 'Não encontrado'})
    conn.execute('''UPDATE agenda_appointments SET paid=1, paid_amount=?, paid_method=?
                    WHERE id=? AND business_id=?''', (amount, method, appt_id, biz_id))
    conn.execute('''INSERT INTO agenda_payments (business_id, appointment_id, customer_phone, amount, method, paid_at)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                 (biz_id, appt_id, appt['customer_phone'], amount, method,
                  datetime.now().isoformat()))
    conn.execute('''UPDATE agenda_customers SET total_spent=total_spent+?
                    WHERE business_id=? AND phone=?''',
                 (amount, biz_id, appt['customer_phone']))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ── AgendaSC — Gestão de Profissionais ───────────────────────────────────────
@app.route('/agenda/equipe')
@_agenda_login_required
def agenda_equipe():
    biz_id = session['agenda_business_id']
    conn   = get_saas_db()
    biz    = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone())
    profs  = [dict(r) for r in conn.execute(
        'SELECT * FROM agenda_professionals WHERE business_id=? ORDER BY order_pos, name', (biz_id,)
    ).fetchall()]
    conn.close()
    return render_template('agenda/equipe.html', biz=biz, profissionais=profs)


@app.route('/agenda/equipe/novo', methods=['GET', 'POST'])
@_agenda_login_required
def agenda_equipe_novo():
    biz_id = session['agenda_business_id']
    erro = ''
    if request.method == 'POST':
        name           = request.form.get('name', '').strip()
        role           = request.form.get('role', '').strip()
        photo_url      = request.form.get('photo_url', '').strip()
        color          = request.form.get('color', '#27ae60').strip()
        bio            = request.form.get('bio', '').strip()
        commission_pct = float(request.form.get('commission_pct', '0').replace(',', '.') or 0)
        if not name:
            erro = 'Informe o nome do profissional.'
        else:
            conn = get_saas_db()
            conn.execute('''INSERT INTO agenda_professionals
                (business_id, name, role, photo_url, color, bio, commission_pct, active, created_at)
                VALUES (?,?,?,?,?,?,?,1,?)''',
                (biz_id, name, role, photo_url, color, bio, commission_pct,
                 datetime.now().isoformat()))
            conn.commit(); conn.close()
            return redirect('/agenda/equipe')
    return render_template('agenda/profissional_form.html', prof=None, erro=erro, modo='novo')


@app.route('/agenda/equipe/editar/<int:prof_id>', methods=['GET', 'POST'])
@_agenda_login_required
def agenda_equipe_editar(prof_id):
    biz_id = session['agenda_business_id']
    conn   = get_saas_db()
    prof   = conn.execute('SELECT * FROM agenda_professionals WHERE id=? AND business_id=?',
                          (prof_id, biz_id)).fetchone()
    if not prof:
        conn.close()
        return redirect('/agenda/equipe')
    prof = dict(prof)
    erro = ''
    if request.method == 'POST':
        name           = request.form.get('name', '').strip()
        role           = request.form.get('role', '').strip()
        photo_url      = request.form.get('photo_url', '').strip()
        color          = request.form.get('color', '#27ae60').strip()
        bio            = request.form.get('bio', '').strip()
        commission_pct = float(request.form.get('commission_pct', '0').replace(',', '.') or 0)
        if not name:
            erro = 'Informe o nome do profissional.'
        else:
            conn.execute('''UPDATE agenda_professionals
                SET name=?, role=?, photo_url=?, color=?, bio=?, commission_pct=?
                WHERE id=? AND business_id=?''',
                (name, role, photo_url, color, bio, commission_pct, prof_id, biz_id))
            conn.commit(); conn.close()
            return redirect('/agenda/equipe')
    conn.close()
    return render_template('agenda/profissional_form.html', prof=prof, erro=erro, modo='editar')


@app.route('/agenda/equipe/excluir/<int:prof_id>', methods=['POST'])
@_agenda_login_required
def agenda_equipe_excluir(prof_id):
    biz_id = session['agenda_business_id']
    conn   = get_saas_db()
    conn.execute('UPDATE agenda_professionals SET active=0 WHERE id=? AND business_id=?',
                 (prof_id, biz_id))
    conn.commit(); conn.close()
    return redirect('/agenda/equipe')


@app.route('/agenda/equipe/ativar/<int:prof_id>', methods=['POST'])
@_agenda_login_required
def agenda_equipe_ativar(prof_id):
    biz_id = session['agenda_business_id']
    conn   = get_saas_db()
    conn.execute('UPDATE agenda_professionals SET active=1 WHERE id=? AND business_id=?',
                 (prof_id, biz_id))
    conn.commit(); conn.close()
    return redirect('/agenda/equipe')


@app.route('/agenda/painel/financeiro-equipe')
@_agenda_login_required
def agenda_financeiro_equipe():
    """Retorna JSON com receita e comissão por profissional."""
    biz_id = session['agenda_business_id']
    mes    = request.args.get('mes', datetime.now().strftime('%Y-%m'))
    conn   = get_saas_db()
    # Por profissional
    profs = [dict(r) for r in conn.execute(
        'SELECT * FROM agenda_professionals WHERE business_id=? ORDER BY name', (biz_id,)
    ).fetchall()]
    resultado = []
    total_receita = 0
    total_comissao = 0
    for p in profs:
        r = conn.execute('''
            SELECT COUNT(*) as qtd, COALESCE(SUM(s.price),0) as receita
            FROM agenda_appointments a
            LEFT JOIN agenda_services s ON a.service_id=s.id
            WHERE a.business_id=? AND a.professional_id=?
              AND strftime('%Y-%m', a.appointment_date)=?
              AND a.status='done'
        ''', (biz_id, p['id'], mes)).fetchone()
        receita  = round(float(r['receita']), 2)
        comissao = round(receita * p['commission_pct'] / 100, 2)
        total_receita  += receita
        total_comissao += comissao
        resultado.append({
            'id': p['id'], 'name': p['name'], 'role': p['role'],
            'color': p['color'], 'photo_url': p['photo_url'],
            'commission_pct': p['commission_pct'],
            'qtd': r['qtd'], 'receita': receita, 'comissao': comissao,
            'liquido': round(receita - comissao, 2)
        })
    # Agendamentos sem profissional definido
    r_sem = conn.execute('''
        SELECT COUNT(*) as qtd, COALESCE(SUM(s.price),0) as receita
        FROM agenda_appointments a
        LEFT JOIN agenda_services s ON a.service_id=s.id
        WHERE a.business_id=? AND (a.professional_id IS NULL OR a.professional_id=0)
          AND strftime('%Y-%m', a.appointment_date)=?
          AND a.status='done'
    ''', (biz_id, mes)).fetchone()
    conn.close()
    resultado.append({
        'id': 0, 'name': 'Sem profissional', 'role': '',
        'color': '#6b7280', 'photo_url': '',
        'commission_pct': 0,
        'qtd': r_sem['qtd'], 'receita': round(float(r_sem['receita']), 2),
        'comissao': 0, 'liquido': round(float(r_sem['receita']), 2)
    })
    total_receita  += float(r_sem['receita'])
    return jsonify({
        'profissionais': resultado,
        'total_receita': round(total_receita, 2),
        'total_comissao': round(total_comissao, 2),
        'total_liquido': round(total_receita - total_comissao, 2),
        'mes': mes
    })


# ── AgendaSC — Checkout / Assinatura ─────────────────────────────────────────
@app.route('/agenda/assinar', methods=['GET', 'POST'])
@_agenda_login_required
def agenda_assinar():
    biz_id = session['agenda_business_id']
    p = AGENDA_PLAN
    erro = None
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX').upper()
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            billing_type = 'PIX'
        conn = get_saas_db()
        biz_row = conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone()
        conn.close()
        if not biz_row:
            return redirect('/agenda/entrar')
        biz = dict(biz_row)
        customer_id = _asaas_criar_ou_buscar_cliente_saas(
            biz['name'], biz['email'], biz['phone'], biz.get('cpf_cnpj', ''), biz['id'], 'agenda_businesses'
        )
        if not customer_id:
            log.error('[AgendaSC] Falha ao obter customer_id para biz_id=%s email=%s', biz_id, biz.get('email'))
            erro = ('Não conseguimos processar o pagamento agora. '
                    'Entre em contato pelo WhatsApp (47) 99960-6998 e ativamos sua conta manualmente em minutos. 💬')
        else:
            conn2 = get_saas_db()
            conn2.execute('UPDATE agenda_businesses SET asaas_customer_id=? WHERE id=?',
                          (customer_id, biz_id))
            conn2.commit(); conn2.close()
            resp = _asaas_criar_assinatura_saas(
                customer_id, 'agenda', 'pro', p['preco'],
                'Agenda SC Pro — Assinatura Mensal',
                billing_type
            )
            if resp.get('id'):
                if billing_type == 'PIX':
                    pix = _asaas_get_pix_qr(resp['id'])
                    session['agenda_pix_qr'] = pix.get('encodedImage', '')
                    session['agenda_pix_payload'] = pix.get('payload', '')
                else:
                    session.pop('agenda_pix_qr', None)
                    session.pop('agenda_pix_payload', None)
                return redirect('/agenda/aguardando-pagamento')
            else:
                erro = 'Não foi possível gerar o pagamento. Tente novamente.'
    return render_template('agenda/checkout.html', plano=p, erro=erro)


@app.route('/agenda/aguardando-pagamento')
@_agenda_login_required
def agenda_aguardando():
    pix_qr = session.pop('agenda_pix_qr', '')
    pix_payload = session.pop('agenda_pix_payload', '')
    return render_template('agenda/aguardando.html', pix_qr=pix_qr, pix_payload=pix_payload)


@app.route('/agenda/painel/configuracoes', methods=['GET', 'POST'])
@_agenda_login_required
def agenda_configuracoes():
    biz_id = session['agenda_business_id']
    conn   = get_saas_db()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        fields = ['pix_chave','pix_nome','mandazap_instance',
                  'msg_confirmacao','msg_lembrete','msg_cancelamento','msg_avaliacao',
                  'primary_color','description','address','instagram']
        updates = {f: data.get(f,'') for f in fields}
        updates['mandazap_ativo'] = 1 if data.get('mandazap_ativo') else 0
        # Valida cor hex
        import re as _re_color
        cor = updates.get('primary_color','').strip()
        if not cor or not _re_color.match(r'^#[0-9a-fA-F]{6}$', cor):
            updates['primary_color'] = '#27ae60'
        try:
            updates['max_days_advance'] = max(1, min(365, int(data.get('max_days_advance', 60))))
        except Exception:
            updates['max_days_advance'] = 60
        conn.execute('''UPDATE agenda_businesses SET
            pix_chave=?, pix_nome=?, mandazap_instance=?, mandazap_ativo=?,
            msg_confirmacao=?, msg_lembrete=?, msg_cancelamento=?, msg_avaliacao=?,
            max_days_advance=?, primary_color=?, description=?, address=?, instagram=?
            WHERE id=?''',
            (updates['pix_chave'], updates['pix_nome'], updates['mandazap_instance'],
             updates['mandazap_ativo'], updates['msg_confirmacao'],
             updates['msg_lembrete'], updates['msg_cancelamento'], updates['msg_avaliacao'],
             updates['max_days_advance'], updates['primary_color'],
             updates['description'], updates['address'], updates['instagram'], biz_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    biz = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone())
    conn.close()
    return jsonify(biz)


@app.route('/agenda/painel/upload-logo', methods=['POST'])
@_agenda_login_required
def agenda_upload_logo():
    """Faz upload do logo do negócio."""
    biz_id = session['agenda_business_id']
    f = request.files.get('logo')
    if not f or not f.filename:
        return jsonify({'success': False, 'error': 'Nenhum arquivo enviado.'})
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        return jsonify({'success': False, 'error': 'Formato inválido. Use JPG, PNG ou WEBP.'})
    f.seek(0, 2); size = f.tell(); f.seek(0)
    if size > 2 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'Imagem muito grande. Máximo 2MB.'})
    upload_dir = os.path.join(os.path.dirname(__file__), 'static', 'agenda_logos')
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"logo_{biz_id}.{ext}"
    f.save(os.path.join(upload_dir, filename))
    url = f"/static/agenda_logos/{filename}"
    conn = get_saas_db()
    conn.execute('UPDATE agenda_businesses SET logo_url=? WHERE id=?', (url, biz_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'url': f"{url}?v={uuid.uuid4().hex[:6]}"})


@app.route('/agenda/painel/upload-cover', methods=['POST'])
@_agenda_login_required
def agenda_upload_cover():
    """Faz upload da foto de capa do negócio."""
    biz_id = session['agenda_business_id']
    f = request.files.get('cover')
    if not f or not f.filename:
        return jsonify({'success': False, 'error': 'Nenhum arquivo enviado.'})
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        return jsonify({'success': False, 'error': 'Formato inválido. Use JPG, PNG ou WEBP.'})
    f.seek(0, 2)
    size = f.tell(); f.seek(0)
    if size > 4 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'Imagem muito grande. Máximo 4MB.'})
    upload_dir = os.path.join(os.path.dirname(__file__), 'static', 'agenda_covers')
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"biz_{biz_id}.{ext}"
    filepath = os.path.join(upload_dir, filename)
    f.save(filepath)
    url = f"/static/agenda_covers/{filename}?v={uuid.uuid4().hex[:6]}"
    conn = get_saas_db()
    conn.execute('UPDATE agenda_businesses SET cover_photo=? WHERE id=?', (url.split('?')[0], biz_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'url': url})


@app.route('/agenda/painel/testar-whatsapp', methods=['POST'])
@_agenda_login_required
def agenda_testar_whatsapp():
    """Envia mensagem de teste para o próprio número do negócio."""
    biz_id = session['agenda_business_id']
    conn   = get_saas_db()
    biz    = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone())
    conn.close()
    instance = biz.get('mandazap_instance', '').strip()
    phone    = biz.get('phone', '').strip()
    if not instance:
        return jsonify({'success': False, 'error': 'Nome da instância não configurado.'})
    if not phone:
        return jsonify({'success': False, 'error': 'Telefone do negócio não encontrado.'})
    evo_url = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    if not evo_url or not evo_key:
        return jsonify({'success': False, 'error': 'Evolution API não configurada no servidor. Contate o suporte.'})
    msg = (f"✅ *Teste de integração — {biz['name']}*\n\n"
           f"Sua conexão com o WhatsApp automático está funcionando!\n\n"
           f"📲 *MandaZap + Agenda SC* ativado com sucesso.\n"
           f"Seus clientes vão receber confirmações, lembretes e avisos automaticamente.")
    ok = _agenda_send_whatsapp(phone, msg, instance)
    if ok:
        return jsonify({'success': True, 'msg': f'Mensagem enviada para {phone} ✅'})
    else:
        return jsonify({'success': False, 'error': 'Falha ao enviar. Verifique se a instância está conectada no MandaZap (QR code escaneado).'})


@app.route('/agenda/painel/relatorios')
@_agenda_login_required
def agenda_relatorios():
    biz_id = session['agenda_business_id']
    conn   = get_saas_db()
    hoje   = datetime.now().date().isoformat()
    mes    = datetime.now().strftime('%Y-%m')

    receita_hoje = conn.execute('''
        SELECT COALESCE(SUM(s.price),0) FROM agenda_appointments a
        LEFT JOIN agenda_services s ON a.service_id=s.id
        WHERE a.business_id=? AND a.appointment_date=? AND a.status='done'
    ''', (biz_id, hoje)).fetchone()[0]

    receita_mes = conn.execute('''
        SELECT COALESCE(SUM(s.price),0) FROM agenda_appointments a
        LEFT JOIN agenda_services s ON a.service_id=s.id
        WHERE a.business_id=? AND strftime('%Y-%m',a.appointment_date)=? AND a.status='done'
    ''', (biz_id, mes)).fetchone()[0]

    total_clientes = conn.execute(
        'SELECT COUNT(*) FROM agenda_customers WHERE business_id=?', (biz_id,)
    ).fetchone()[0]

    top_servicos = [dict(r) for r in conn.execute('''
        SELECT s.name, COUNT(*) as qtd, COALESCE(SUM(s.price),0) as total
        FROM agenda_appointments a
        JOIN agenda_services s ON a.service_id=s.id
        WHERE a.business_id=? AND a.status='done'
        GROUP BY s.id ORDER BY qtd DESC LIMIT 5
    ''', (biz_id,)).fetchall()]

    # Faturamento últimos 6 meses
    meses_data = []
    from datetime import date as _date
    _hoje = _date.today()
    for i in range(5, -1, -1):
        _mo = _hoje.month - i
        if _mo <= 0:
            m_year, m_month = _hoje.year - 1, _mo + 12
        else:
            m_year, m_month = _hoje.year, _mo
        m_str = f'{m_year}-{m_month:02d}'
        val = conn.execute('''
            SELECT COALESCE(SUM(s.price),0) FROM agenda_appointments a
            LEFT JOIN agenda_services s ON a.service_id=s.id
            WHERE a.business_id=? AND strftime('%Y-%m',a.appointment_date)=? AND a.status='done'
        ''', (biz_id, m_str)).fetchone()[0]
        meses_data.append({'mes': m_str, 'valor': round(val, 2)})

    conn.close()
    return jsonify({
        'receita_hoje': round(receita_hoje, 2),
        'receita_mes':  round(receita_mes, 2),
        'total_clientes': total_clientes,
        'top_servicos': top_servicos,
        'historico_meses': meses_data,
    })


@app.route('/agenda/painel/clientes')
@_agenda_login_required
def agenda_lista_clientes():
    biz_id = session['agenda_business_id']
    busca  = request.args.get('q', '').strip()
    conn   = get_saas_db()
    if busca:
        rows = conn.execute('''
            SELECT * FROM agenda_customers
            WHERE business_id=? AND (name LIKE ? OR phone LIKE ?)
            ORDER BY total_visits DESC LIMIT 100
        ''', (biz_id, f'%{busca}%', f'%{busca}%')).fetchall()
    else:
        rows = conn.execute('''
            SELECT * FROM agenda_customers WHERE business_id=?
            ORDER BY total_visits DESC LIMIT 100
        ''', (biz_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/reagendar/<token>')
def agenda_reagendar_token(token):
    """Redireciona para a página de agendamento do negócio via token de cancelamento."""
    conn = get_saas_db()
    row = conn.execute(
        'SELECT b.slug FROM agenda_appointments a '
        'JOIN agenda_businesses b ON a.business_id = b.id '
        'WHERE a.cancel_token=?', (token,)
    ).fetchone()
    conn.close()
    if not row:
        return redirect('/agenda/entrar')
    return redirect(f'/agendar/{row["slug"]}')


@app.route('/cancelar/<token>', methods=['GET', 'POST'])
def agenda_cancelar_token(token):
    """Página pública de cancelamento via token enviado no WhatsApp/email."""
    conn = get_saas_db()
    appt = conn.execute(
        'SELECT a.*, b.name as biz_name, b.phone as biz_phone, s.name as svc_name '
        'FROM agenda_appointments a '
        'LEFT JOIN agenda_businesses b ON a.business_id = b.id '
        'LEFT JOIN agenda_services s ON a.service_id = s.id '
        'WHERE a.cancel_token=?', (token,)
    ).fetchone()

    if not appt:
        conn.close()
        return render_template('agenda/cancelar.html', status='invalido', appt=None)

    appt = dict(appt)

    # Já cancelado
    if appt['status'] == 'cancelled':
        conn.close()
        return render_template('agenda/cancelar.html', status='ja_cancelado', appt=appt)

    # Já concluído
    if appt['status'] == 'done':
        conn.close()
        return render_template('agenda/cancelar.html', status='concluido', appt=appt)

    # Verifica janela de 24h
    from datetime import datetime as _dt, timedelta as _td
    appt_dt_str = f"{appt['appointment_date']} {appt['appointment_time']}"
    try:
        appt_dt = _dt.strptime(appt_dt_str, '%Y-%m-%d %H:%M')
    except Exception:
        try:
            appt_dt = _dt.strptime(appt_dt_str, '%Y-%m-%d %H:%M:%S')
        except Exception:
            appt_dt = _dt.now() + _td(days=2)  # fallback seguro

    horas_restantes = (appt_dt - _dt.now()).total_seconds() / 3600
    pode_cancelar = horas_restantes >= 24

    if request.method == 'POST' and pode_cancelar:
        conn.execute(
            "UPDATE agenda_appointments SET status='cancelled' WHERE cancel_token=?", (token,)
        )
        conn.commit()
        # WhatsApp para o dono
        biz_full = conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (appt['business_id'],)).fetchone()
        conn.close()
        biz_full = dict(biz_full) if biz_full else {}
        if biz_full.get('mandazap_ativo') and biz_full.get('mandazap_instance') and biz_full.get('phone'):
            dia_fmt = appt['appointment_date'][8:10] + '/' + appt['appointment_date'][5:7] + '/' + appt['appointment_date'][:4]
            msg_cancel = (
                f"❌ *Agendamento cancelado*\n\n"
                f"*Cliente:* {appt['customer_name']}\n"
                f"*Serviço:* {appt.get('svc_name','')}\n"
                f"*Data:* {dia_fmt} às {appt['appointment_time']}\n\n"
                f"O horário ficou disponível novamente."
            )
            _agenda_send_whatsapp(biz_full['phone'], msg_cancel, biz_full['mandazap_instance'])
        return render_template('agenda/cancelar.html', status='cancelado', appt=appt)

    conn.close()
    return render_template('agenda/cancelar.html',
                           status='pendente' if pode_cancelar else 'tarde_demais',
                           appt=appt, horas=round(horas_restantes, 1))


@app.route('/agendar/<slug>')
def agenda_booking(slug):
    conn = get_saas_db()
    biz = conn.execute(
        'SELECT * FROM agenda_businesses WHERE slug=? AND active=1', (slug,)
    ).fetchone()
    if biz:
        trial_ends = biz['trial_ends'] or ''
        if trial_ends and trial_ends < datetime.now().isoformat():
            conn.close()
            return render_template('agenda/booking_paused.html', biz=dict(biz))
    if not biz:
        conn.close()
        abort(404)
    services = [dict(r) for r in conn.execute(
        'SELECT * FROM agenda_services WHERE business_id=? AND active=1 ORDER BY name', (biz['id'],)
    ).fetchall()]
    professionals = [dict(r) for r in conn.execute(
        'SELECT * FROM agenda_professionals WHERE business_id=? AND active=1 ORDER BY order_pos, name',
        (biz['id'],)
    ).fetchall()]
    conn.close()
    return render_template('agenda/booking.html', biz=dict(biz), services=services,
                           professionals=professionals)


@app.route('/api/agenda/slots/<slug>')
def api_agenda_slots(slug):
    date_str   = request.args.get('date', '')
    service_id = request.args.get('service_id', '')
    conn = get_saas_db()
    biz = conn.execute('SELECT * FROM agenda_businesses WHERE slug=? AND active=1', (slug,)).fetchone()
    if not biz:
        conn.close()
        return jsonify({'slots': []})
    biz = dict(biz)
    trial_ends = biz.get('trial_ends') or ''
    if trial_ends and trial_ends < datetime.now().isoformat():
        conn.close()
        return jsonify({'slots': []})
    # Verifica limite de antecedência
    max_days = int(biz.get('max_days_advance') or 60)
    if date_str:
        try:
            from datetime import date as _date
            req_date = _date.fromisoformat(date_str)
            limit_date = _date.today() + timedelta(days=max_days)
            if req_date > limit_date:
                conn.close()
                return jsonify({'slots': [], 'bloqueado': True, 'msg': f'Agendamentos disponíveis até {max_days} dias de antecedência.'})
        except Exception:
            pass
    duration = 60
    if service_id:
        svc = conn.execute(
            'SELECT duration_minutes FROM agenda_services WHERE id=? AND business_id=? AND active=1',
            (service_id, biz['id'])
        ).fetchone()
        if svc:
            duration = svc['duration_minutes']
    conn.close()
    return jsonify({'slots': _get_slots(biz['id'], date_str, duration)})


@app.route('/api/agenda/book/<slug>', methods=['POST'])
def api_agenda_book(slug):
    data            = request.get_json() or {}
    customer_name   = data.get('customer_name', '').strip()
    customer_phone  = data.get('customer_phone', '').strip()
    customer_email  = data.get('customer_email', '').strip()
    service_id      = data.get('service_id')
    appt_date       = data.get('date', '').strip()
    appt_time       = data.get('time', '').strip()
    notes           = data.get('notes', '').strip()
    professional_id = data.get('professional_id') or None

    if not all([customer_name, customer_phone, appt_date, appt_time]):
        return jsonify({'success': False, 'error': 'Preencha todos os campos obrigatórios.'})

    conn = get_saas_db()
    biz = conn.execute('SELECT * FROM agenda_businesses WHERE slug=? AND active=1', (slug,)).fetchone()
    if not biz:
        conn.close()
        return jsonify({'success': False, 'error': 'Negócio não encontrado.'})
    trial_ends = biz['trial_ends'] or ''
    if trial_ends and trial_ends < datetime.now().isoformat():
        conn.close()
        return jsonify({'success': False, 'error': 'Este negócio está com o período de teste encerrado. Entre em contato diretamente.'})

    duration = 60
    if service_id:
        svc = conn.execute(
            'SELECT duration_minutes FROM agenda_services WHERE id=? AND business_id=? AND active=1',
            (service_id, biz['id'])
        ).fetchone()
        if svc:
            duration = svc['duration_minutes']

    slots = _get_slots(biz['id'], appt_date, duration)
    if appt_time not in slots:
        conn.close()
        return jsonify({'success': False, 'error': 'Horário não disponível. Por favor, escolha outro.'})

    # Resolve professional name
    prof_name = ''
    if professional_id:
        p = conn.execute('SELECT name FROM agenda_professionals WHERE id=? AND business_id=?',
                         (professional_id, biz['id'])).fetchone()
        if p:
            prof_name = p['name']
        else:
            professional_id = None

    cancel_token = uuid.uuid4().hex
    conn.execute('''
        INSERT INTO agenda_appointments
        (business_id, service_id, customer_name, customer_phone, customer_notes,
         appointment_date, appointment_time, status, created_at, professional_id, professional_name,
         customer_email, cancel_token)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
    ''', (biz['id'], service_id or None, customer_name, customer_phone, notes,
          appt_date, appt_time, datetime.now().isoformat(),
          professional_id, prof_name, customer_email, cancel_token))
    conn.commit()

    # Registra/atualiza cliente
    _agenda_upsert_customer(conn, biz['id'], customer_name, customer_phone)

    # WhatsApp automático (se MandaZap ativo)
    biz_full = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz['id'],)).fetchone())
    conn.close()

    cancel_url = f"https://4kitem.com.br/cancelar/{cancel_token}"
    dia_fmt_wa = appt_date[8:10] + '/' + appt_date[5:7] + '/' + appt_date[:4]

    if biz_full.get('mandazap_ativo') and biz_full.get('mandazap_instance'):
        svc_name = ''
        if service_id:
            conn2 = get_saas_db()
            svc = conn2.execute('SELECT name FROM agenda_services WHERE id=?', (service_id,)).fetchone()
            conn2.close()
            svc_name = svc['name'] if svc else ''
        tpl = biz_full.get('msg_confirmacao') or (
            f"Olá {{nome}}! 👋\n\n"
            f"Seu agendamento foi recebido com sucesso! ✅\n\n"
            f"📋 Serviço: {{servico}}\n"
            f"📅 Data: {{data}}\n"
            f"🕐 Horário: {{hora}}\n"
            f"🏢 Local: {{negocio}}\n\n"
            f"Aguarde a confirmação. Em caso de dúvidas, entre em contato.\n\n"
            f"❌ Para cancelar (até 24h antes): {{cancelar}}"
        )
        msg = (tpl
               .replace('{nome}', customer_name.split()[0])
               .replace('{servico}', svc_name)
               .replace('{data}', dia_fmt_wa)
               .replace('{hora}', appt_time)
               .replace('{negocio}', biz_full['name'])
               .replace('{cancelar}', cancel_url))
        _agenda_send_whatsapp(customer_phone, msg, biz_full['mandazap_instance'])

        # WhatsApp para o DONO do negócio
        phone_dono = biz_full.get('phone', '').strip()
        if phone_dono:
            msg_dono = (
                f"🔔 *Novo agendamento!*\n\n"
                f"*Cliente:* {customer_name}\n"
                f"*Telefone:* {customer_phone}\n"
                f"*Serviço:* {svc_name}\n"
                f"*Data:* {dia_fmt_wa}\n"
                f"*Horário:* {appt_time}\n\n"
                f"Acesse o painel para confirmar: https://4kitem.com.br/agenda/painel"
            )
            _agenda_send_whatsapp(phone_dono, msg_dono, biz_full['mandazap_instance'])

    # ── Email de confirmação para o cliente ─────────────────────────────────
    if customer_email:
        conn3 = get_saas_db()
        svc_row = conn3.execute('SELECT name FROM agenda_services WHERE id=?', (service_id,)).fetchone() if service_id else None
        conn3.close()
        svc_nome = svc_row['name'] if svc_row else 'Serviço'
        dia_fmt  = appt_date[8:10] + '/' + appt_date[5:7] + '/' + appt_date[:4]
        html_cliente = _email_base(f"""
<div style="font-size:36px;margin-bottom:12px">📅</div>
<h1 style="color:#fff;font-size:20px;font-weight:800;margin:0 0 6px">Agendamento confirmado!</h1>
<p style="color:#888;font-size:13px;margin:0 0 24px">Olá <strong style="color:#fff">{customer_name.split()[0]}</strong>, seu agendamento foi recebido com sucesso.</p>
<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:20px;margin-bottom:24px">
  <div style="font-size:12px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">Detalhes do agendamento</div>
  <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
    <span style="font-size:13px;color:#666">Local</span>
    <span style="font-size:13px;color:#fff;font-weight:700">{biz_full['name']}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
    <span style="font-size:13px;color:#666">Serviço</span>
    <span style="font-size:13px;color:#fff;font-weight:700">{svc_nome}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
    <span style="font-size:13px;color:#666">Data</span>
    <span style="font-size:13px;color:#22c55e;font-weight:700">{dia_fmt}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:8px 0{';border-bottom:1px solid #222' if prof_name else ''}">
    <span style="font-size:13px;color:#666">Horário</span>
    <span style="font-size:13px;color:#22c55e;font-weight:700">{appt_time}</span>
  </div>
  {'<div style="display:flex;justify-content:space-between;padding:8px 0"><span style="font-size:13px;color:#666">Profissional</span><span style="font-size:13px;color:#fff;font-weight:700">' + prof_name + '</span></div>' if prof_name else ''}
</div>
<p style="font-size:13px;color:#666;margin:0">Dúvidas? Entre em contato pelo telefone <strong style="color:#fff">{biz_full.get('phone','')}</strong>.</p>
""", '#22c55e')
        _enviar_email(customer_email, f'✅ Agendamento confirmado — {biz_full["name"]}', html_cliente)

    # ── Email de notificação para o dono do negócio ──────────────────────────
    if biz_full.get('email'):
        dia_fmt = appt_date[8:10] + '/' + appt_date[5:7] + '/' + appt_date[:4]
        conn4 = get_saas_db()
        svc_row2 = conn4.execute('SELECT name FROM agenda_services WHERE id=?', (service_id,)).fetchone() if service_id else None
        conn4.close()
        svc_nome2 = svc_row2['name'] if svc_row2 else 'Serviço'
        html_dono = _email_base(f"""
<div style="font-size:36px;margin-bottom:12px">🔔</div>
<h1 style="color:#fff;font-size:20px;font-weight:800;margin:0 0 6px">Novo agendamento!</h1>
<p style="color:#888;font-size:13px;margin:0 0 24px">Um cliente acabou de agendar no <strong style="color:#fff">{biz_full['name']}</strong>.</p>
<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:20px;margin-bottom:24px">
  <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
    <span style="font-size:13px;color:#666">Cliente</span>
    <span style="font-size:13px;color:#fff;font-weight:700">{customer_name}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
    <span style="font-size:13px;color:#666">Telefone</span>
    <span style="font-size:13px;color:#fff;font-weight:700">{customer_phone}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
    <span style="font-size:13px;color:#666">Serviço</span>
    <span style="font-size:13px;color:#fff;font-weight:700">{svc_nome2}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222">
    <span style="font-size:13px;color:#666">Data</span>
    <span style="font-size:13px;color:#22c55e;font-weight:700">{dia_fmt}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:8px 0">
    <span style="font-size:13px;color:#666">Horário</span>
    <span style="font-size:13px;color:#22c55e;font-weight:700">{appt_time}</span>
  </div>
</div>
<a href="https://4kitem.com.br/agenda/painel" style="display:block;text-align:center;padding:12px 24px;background:#22c55e;color:#fff;font-size:14px;font-weight:700;border-radius:12px;text-decoration:none">Ver no painel →</a>
""", '#22c55e')
        _enviar_email(biz_full['email'], f'🔔 Novo agendamento — {customer_name} · {dia_fmt} {appt_time}', html_dono)

    return jsonify({'success': True, 'business_name': biz['name'], 'business_phone': biz['phone'],
                    'pix_chave': biz_full.get('pix_chave',''), 'pix_nome': biz_full.get('pix_nome','')})


# ══════════════════════════════════════════════════════════════════════════
#  ALERTA SC — Monitoramento automático de débitos veiculares
# ══════════════════════════════════════════════════════════════════════════

def _alerta_consultar_placa(placa: str) -> list:
    """
    Consulta débitos/situação de um veículo.
    Usa API configurada via env var ALERTA_VEICULO_API_URL + ALERTA_VEICULO_API_KEY.
    Retorna lista de dicts: {tipo, descricao, valor, vencimento, situacao}
    """
    api_url = os.environ.get('ALERTA_VEICULO_API_URL', '').rstrip('/')
    api_key = os.environ.get('ALERTA_VEICULO_API_KEY', '')
    placa_clean = _re.sub(r'[^A-Z0-9]', '', placa.upper())
    if not (api_url and api_key and placa_clean):
        return []
    try:
        r = requests.get(
            f"{api_url}/veiculo/{placa_clean}",
            headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'},
            timeout=20
        )
        if r.status_code != 200:
            log.warning(f'[AlertaSC] API retornou {r.status_code} para placa {placa_clean}')
            return []
        data = r.json()
        # Normaliza: aceita {debitos: [...]} ou lista direta
        debitos = data if isinstance(data, list) else data.get('debitos', data.get('data', []))
        result = []
        for d in debitos:
            result.append({
                'tipo':       d.get('tipo') or d.get('type') or 'Débito',
                'descricao':  d.get('descricao') or d.get('description') or '',
                'valor':      str(d.get('valor') or d.get('value') or ''),
                'vencimento': str(d.get('vencimento') or d.get('dueDate') or d.get('due_date') or ''),
                'situacao':   d.get('situacao') or d.get('status') or 'pendente',
            })
        return result
    except Exception as e:
        log.warning(f'[AlertaSC] Erro ao consultar placa {placa_clean}: {e}')
        return []


def _alerta_send_whatsapp(phone: str, mensagem: str) -> bool:
    """Envia notificação via Evolution API (mesma instância do MandaZap/MandaJá)."""
    EVO_URL  = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    EVO_KEY  = os.environ.get('EVOLUTION_API_KEY', '')
    INSTANCE = os.environ.get('ALERTA_EVO_INSTANCE',
                              os.environ.get('MANDAJA_EVO_INSTANCE', ''))
    if not (EVO_URL and EVO_KEY and INSTANCE):
        return False
    phone_clean = _re.sub(r'\D', '', phone)
    if not phone_clean.startswith('55'):
        phone_clean = '55' + phone_clean
    try:
        r = requests.post(
            f"{EVO_URL}/message/sendText/{INSTANCE}",
            headers={'apikey': EVO_KEY, 'Content-Type': 'application/json'},
            json={'number': phone_clean, 'text': mensagem},
            timeout=12
        )
        return r.status_code in (200, 201)
    except Exception as e:
        log.warning(f'[AlertaSC] WhatsApp send error: {e}')
        return False


def _alerta_notificar_assinante(sub: dict, novos: list):
    """Monta mensagem WhatsApp + email e envia ao assinante."""
    nome = sub['name'].split()[0]
    linhas = []
    for a in novos:
        tipo   = a.get('tipo', 'Débito')
        valor  = a.get('valor', '')
        venc   = a.get('vencimento', '')
        placa  = a.get('placa', '')
        desc_p = a.get('plate_desc', placa)
        linha  = f"🚗 *{desc_p}* ({placa})\n   📋 {tipo}"
        if valor:
            linha += f" — R$ {valor}"
        if venc:
            linha += f"\n   📅 Vence: {venc}"
        linhas.append(linha)

    total = len(novos)
    msg_wpp = (
        f"🚨 *AlertaSC — Novo débito detectado!*\n\n"
        f"Olá {nome}! Encontramos *{total} débito(s) pendente(s)*:\n\n"
        + '\n\n'.join(linhas) +
        f"\n\n💡 Acesse sua conta para mais detalhes:\n"
        f"4kitem.com.br/alerta/minha-conta\n\n"
        f"_AlertaSC · Monitoramento automático_"
    )
    _alerta_send_whatsapp(sub['phone'], msg_wpp)

    # Email de alerta (se tiver email cadastrado)
    if sub.get('email'):
        linhas_html = ''.join(
            f'<div style="padding:10px 0;border-bottom:1px solid #222">'
            f'<span style="color:#ef4444;font-weight:700">🚗 {a.get("plate_desc",a.get("placa",""))} ({a.get("placa","")})</span><br>'
            f'<span style="font-size:13px;color:#888">{a.get("tipo","Débito")}'
            f'{" — R$ " + a.get("valor","") if a.get("valor") else ""}'
            f'{"<br>📅 Vence: " + a.get("vencimento","") if a.get("vencimento") else ""}</span>'
            f'</div>'
            for a in novos
        )
        html_alerta = _email_base(f"""
<div style="font-size:40px;margin-bottom:12px">🚨</div>
<h1 style="color:#fff;font-size:20px;font-weight:800;margin:0 0 8px">Novo débito detectado!</h1>
<p style="color:#888;font-size:13px;margin:0 0 20px">Olá <strong style="color:#fff">{nome}</strong>, identificamos <strong style="color:#ef4444">{total} débito(s) pendente(s)</strong> nos seus veículos.</p>
<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:16px;margin-bottom:20px">
{linhas_html}
</div>
<a href="https://4kitem.com.br/alerta/minha-conta" style="display:block;text-align:center;padding:13px 24px;background:#ef4444;color:#fff;font-size:14px;font-weight:700;border-radius:12px;text-decoration:none;margin-bottom:16px">🚨 Ver minha conta</a>
<p style="font-size:12px;color:#555;margin:0">Regularize seus débitos para evitar restrições no veículo.</p>
""", '#ef4444')
        _enviar_email(sub['email'], f'🚨 AlertaSC — {total} débito(s) detectado(s) nos seus veículos', html_alerta)


def _alerta_monitorar_assinante(sub: dict):
    """Verifica e notifica um assinante específico."""
    try:
        plates = _json.loads(sub.get('plates_json') or '[]')
    except Exception:
        return

    novos_total = []
    conn = get_saas_db()

    for item in plates:
        placa      = (item.get('plate') if isinstance(item, dict) else item or '').strip().upper()
        plate_desc = (item.get('desc', '') if isinstance(item, dict) else '') or placa
        if not placa:
            continue

        debitos_atuais = _alerta_consultar_placa(placa)

        # Chaves já registradas para essa placa/assinante
        existentes = {
            row['chave_unica']
            for row in conn.execute(
                'SELECT chave_unica FROM alerta_debitos WHERE subscriber_id=? AND plate=?',
                (sub['id'], placa)
            ).fetchall()
        }

        for deb in debitos_atuais:
            chave = (f"{placa}_{deb.get('tipo','')}_{deb.get('vencimento','')}_{deb.get('valor','')}"
                     ).replace(' ', '_')[:120]
            if chave not in existentes:
                try:
                    conn.execute('''
                        INSERT OR IGNORE INTO alerta_debitos
                        (subscriber_id, plate, plate_desc, chave_unica, tipo, descricao,
                         valor, vencimento, situacao, found_at, notificado)
                        VALUES (?,?,?,?,?,?,?,?,?,?,0)
                    ''', (sub['id'], placa, plate_desc, chave,
                          deb.get('tipo',''), deb.get('descricao',''),
                          deb.get('valor',''), deb.get('vencimento',''),
                          deb.get('situacao','pendente'), datetime.now().isoformat()))
                    novos_total.append({**deb, 'placa': placa, 'plate_desc': plate_desc})
                except Exception:
                    pass

    # Atualiza last_report_at
    conn.execute('UPDATE alerta_subscribers SET last_report_at=? WHERE id=?',
                 (datetime.now().isoformat(), sub['id']))
    conn.commit()

    if novos_total:
        # Marca todos como notificados antes de enviar (evita duplicata em retry)
        conn.execute(
            "UPDATE alerta_debitos SET notificado=1, notificado_at=? WHERE subscriber_id=? AND notificado=0",
            (datetime.now().isoformat(), sub['id'])
        )
        conn.commit()
        conn.close()
        _alerta_notificar_assinante(sub, novos_total)
    else:
        conn.close()


def _alerta_run_monitoring():
    """Job principal de monitoramento — roda em background thread."""
    log.info('[AlertaSC] Iniciando ciclo de monitoramento')
    try:
        conn = get_saas_db()
        subs = conn.execute(
            "SELECT * FROM alerta_subscribers WHERE status='ativo'"
        ).fetchall()
        conn.close()
        total = len(subs)
        log.info(f'[AlertaSC] {total} assinante(s) ativo(s) para monitorar')
        for sub in subs:
            try:
                _alerta_monitorar_assinante(dict(sub))
            except Exception as e:
                log.error(f'[AlertaSC] Erro assinante {sub["id"]}: {e}')
            time.sleep(2)   # Pausa entre consultas para não sobrecarregar API
        log.info(f'[AlertaSC] Ciclo concluído — {total} assinante(s) verificados')
    except Exception as e:
        log.error(f'[AlertaSC] Erro no ciclo: {e}')


def _alerta_scheduler_loop():
    """Thread daemon que roda o monitoramento a cada 24h."""
    # Aguarda 5 min após startup para não sobrecarregar na inicialização
    time.sleep(300)
    while True:
        _alerta_run_monitoring()
        # Próxima execução em 24h
        time.sleep(86400)


# ══════════════════════════════════════════════════════════════════════════
#  AGENDA SC — Lembrete automático WhatsApp (24h antes do agendamento)
# ══════════════════════════════════════════════════════════════════════════

def _agenda_enviar_lembrete_wpp(biz: dict, appt: dict, prof_name: str = '') -> bool:
    """Envia lembrete WhatsApp para o cliente 24h antes do agendamento.
    Usa a instância Evolution configurada no negócio.
    Retorna True se enviou com sucesso."""
    evo_url, evo_key = _get_evo()
    if not evo_url or not evo_key:
        return False

    instance = (biz.get('mandazap_instance') or '').strip()
    if not instance:
        return False

    phone = (appt.get('customer_phone') or '').strip()
    if not phone:
        return False

    # Normaliza telefone
    phone_clean = ''.join(c for c in phone if c.isdigit())
    if not phone_clean.startswith('55'):
        phone_clean = '55' + phone_clean

    # Formata data e hora
    appt_date = appt.get('appointment_date', '')
    appt_time = appt.get('appointment_time', '')
    try:
        from datetime import date
        d = datetime.strptime(appt_date, '%Y-%m-%d')
        dia_fmt = d.strftime('%d/%m/%Y')
        dia_semana = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'][d.weekday()]
    except Exception:
        dia_fmt = appt_date
        dia_semana = ''

    nome_cliente = (appt.get('customer_name') or 'Cliente').split()[0].title()
    nome_negocio = biz.get('name', 'nossa empresa')
    servico      = appt.get('service_name', '') or ''

    # Mensagem personalizada do negócio ou padrão
    msg_template = (biz.get('msg_lembrete') or '').strip()
    if msg_template:
        msg = (msg_template
               .replace('{nome}', nome_cliente)
               .replace('{data}', f'{dia_semana}, {dia_fmt}')
               .replace('{hora}', appt_time)
               .replace('{servico}', servico)
               .replace('{negocio}', nome_negocio))
    else:
        linha_prof = f'\n👤 Profissional: {prof_name}' if prof_name else ''
        linha_serv = f'\n✂️ Serviço: {servico}' if servico else ''
        msg = (
            f'⏰ *Lembrete de agendamento!*\n\n'
            f'Olá {nome_cliente}! Passando para lembrar que você tem um horário marcado:\n\n'
            f'📅 *{dia_semana}, {dia_fmt}*\n'
            f'🕐 *{appt_time}*'
            f'{linha_serv}'
            f'{linha_prof}\n\n'
            f'📍 {nome_negocio}\n\n'
            f'_Caso precise remarcar, entre em contato com antecedência._'
        )

    try:
        resp = requests.post(
            f'{evo_url}/message/sendText/{instance}',
            headers={'apikey': evo_key, 'Content-Type': 'application/json'},
            json={'number': phone_clean, 'text': msg},
            timeout=15
        )
        ok = resp.status_code in (200, 201)
        if ok:
            log.info(f'[AgendaSC Lembrete] ✅ Enviado para {phone_clean} (appt {appt["id"]})')
        else:
            log.warning(f'[AgendaSC Lembrete] ⚠️ Falha {resp.status_code} para {phone_clean}')
        return ok
    except Exception as e:
        log.error(f'[AgendaSC Lembrete] Erro ao enviar para {phone_clean}: {e}')
        return False


def _agenda_run_lembretes():
    """Busca agendamentos de amanhã (janela 22h–26h a partir de agora) que
    ainda não receberam lembrete e dispara WhatsApp para cada um."""
    try:
        now      = datetime.now()
        # Janela: agendamentos entre 22h e 26h a partir de agora
        from_dt  = now + timedelta(hours=22)
        until_dt = now + timedelta(hours=26)
        # Usa string datetime para comparação correta mesmo quando from_date == until_date
        from_str  = from_dt.strftime('%Y-%m-%d %H:%M')
        until_str = until_dt.strftime('%Y-%m-%d %H:%M')

        conn = get_saas_db()
        # Busca todos os agendamentos na janela, sem lembrete, de negócios ativos com WhatsApp
        appts = conn.execute('''
            SELECT a.*, b.name as biz_name, b.mandazap_instance, b.mandazap_ativo,
                   b.msg_lembrete, b.pix_chave,
                   s.name as service_name,
                   p.name as prof_name
            FROM agenda_appointments a
            JOIN agenda_businesses b ON a.business_id = b.id
            LEFT JOIN agenda_services s ON a.service_id = s.id
            LEFT JOIN agenda_professionals p ON a.professional_id = p.id
            WHERE (a.reminded_at IS NULL OR a.reminded_at = '')
              AND a.status NOT IN ('cancelled', 'done')
              AND b.active = 1
              AND b.mandazap_ativo = 1
              AND b.mandazap_instance != ''
              AND (a.appointment_date || ' ' || a.appointment_time) >= ?
              AND (a.appointment_date || ' ' || a.appointment_time) <= ?
        ''', (from_str, until_str)).fetchall()
        conn.close()

        if not appts:
            log.info('[AgendaSC Lembrete] Nenhum agendamento para lembrete neste ciclo')
            return

        log.info(f'[AgendaSC Lembrete] {len(appts)} agendamento(s) para enviar lembrete')
        for row in appts:
            appt = dict(row)
            biz  = {
                'name':               appt.get('biz_name', ''),
                'mandazap_instance':  appt.get('mandazap_instance', ''),
                'mandazap_ativo':     appt.get('mandazap_ativo', 0),
                'msg_lembrete':       appt.get('msg_lembrete', ''),
            }
            prof_name = appt.get('prof_name') or ''
            ok = _agenda_enviar_lembrete_wpp(biz, appt, prof_name)

            # Marca como reminded (mesmo se falhou — evita flood de tentativas)
            now_iso = datetime.now().isoformat()
            conn2 = get_saas_db()
            conn2.execute(
                "UPDATE agenda_appointments SET reminded_at=? WHERE id=?",
                (now_iso, appt['id'])
            )
            conn2.commit(); conn2.close()
            time.sleep(1)   # Pausa entre envios

    except Exception as e:
        log.error(f'[AgendaSC Lembrete] Erro no ciclo: {e}')


def _agenda_lembretes_loop():
    """Thread daemon: verifica lembretes 24h antes a cada 1 hora."""
    time.sleep(180)
    while True:
        _agenda_run_lembretes()
        time.sleep(3600)


def _agenda_run_lembretes_2h():
    """Busca agendamentos confirmados daqui a ~2h que ainda não receberam lembrete de 2h
    e dispara WhatsApp: 'Você está chegando?'."""
    try:
        now      = datetime.now()
        from_dt  = now + timedelta(minutes=90)   # janela: 1h30 → 2h30
        until_dt = now + timedelta(minutes=150)
        # Usa comparação de string datetime para evitar bug de OR com mesmo dia
        from_str  = from_dt.strftime('%Y-%m-%d %H:%M')
        until_str = until_dt.strftime('%Y-%m-%d %H:%M')

        conn = get_saas_db()
        appts = conn.execute('''
            SELECT a.*, b.name as biz_name, b.mandazap_instance, b.mandazap_ativo,
                   s.name as service_name, p.name as prof_name
            FROM agenda_appointments a
            JOIN agenda_businesses b ON a.business_id = b.id
            LEFT JOIN agenda_services s ON a.service_id = s.id
            LEFT JOIN agenda_professionals p ON a.professional_id = p.id
            WHERE (a.reminded_2h_at IS NULL OR a.reminded_2h_at = '')
              AND a.status IN ('pending', 'confirmed')
              AND b.active = 1
              AND b.mandazap_ativo = 1
              AND b.mandazap_instance != ''
              AND (a.appointment_date || ' ' || a.appointment_time) >= ?
              AND (a.appointment_date || ' ' || a.appointment_time) <= ?
        ''', (from_str, until_str)).fetchall()
        conn.close()

        if not appts:
            return

        log.info(f'[AgendaSC Lembrete2h] {len(appts)} agendamento(s) para lembrete 2h')
        for row in appts:
            appt     = dict(row)
            phone    = appt.get('customer_phone', '')
            instance = appt.get('mandazap_instance', '')
            if not phone or not instance:
                continue
            nome       = (appt.get('customer_name') or '').split()[0]
            servico    = appt.get('service_name') or 'agendamento'
            hora       = appt.get('appointment_time', '')
            negocio    = appt.get('biz_name', '')
            prof_name  = appt.get('prof_name') or ''
            prof_line  = f'\n👤 Com: *{prof_name}*' if prof_name else ''
            msg = (
                f"Olá {nome}! 🔔\n\n"
                f"Lembrete: seu *{servico}* em *{negocio}* começa em breve!\n"
                f"🕐 Horário: *{hora}*{prof_line}\n\n"
                f"Você está chegando? 😊"
            )
            _agenda_send_whatsapp(phone, msg, instance)

            now_iso = datetime.now().isoformat()
            conn2 = get_saas_db()
            conn2.execute("UPDATE agenda_appointments SET reminded_2h_at=? WHERE id=?",
                          (now_iso, appt['id']))
            conn2.commit(); conn2.close()
            time.sleep(1)

    except Exception as e:
        log.error(f'[AgendaSC Lembrete2h] Erro: {e}')


def _agenda_lembretes_2h_loop():
    """Thread daemon: verifica lembretes 2h antes a cada 30 minutos."""
    time.sleep(300)   # aguarda 5 min após startup
    while True:
        _agenda_run_lembretes_2h()
        time.sleep(1800)  # a cada 30 min


def _agenda_enviar_resumo():
    """Lógica interna de envio do resumo mensal (sem verificação de dia)."""
    try:
        today = datetime.now()
        # Mês anterior
        prev_month = today.month - 1 or 12
        prev_year  = today.year if today.month > 1 else today.year - 1
        mes_str    = f'{prev_year}-{str(prev_month).zfill(2)}'
        mes_nomes  = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                      'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
        mes_nome   = mes_nomes[prev_month - 1]

        conn = get_saas_db()
        bizs = conn.execute('''
            SELECT b.id, b.name, b.owner_name, b.phone,
                   b.mandazap_instance, b.mandazap_ativo,
                   COUNT(a.id) as total_appts,
                   COALESCE(SUM(s.price), 0) as receita,
                   COUNT(DISTINCT a.customer_phone) as clientes_unicos
            FROM agenda_businesses b
            LEFT JOIN agenda_appointments a
                ON b.id = a.business_id
                AND strftime('%Y-%m', a.appointment_date) = ?
                AND a.status = 'done'
            LEFT JOIN agenda_services s ON a.service_id = s.id
            WHERE b.active = 1
              AND b.mandazap_ativo = 1
              AND b.mandazap_instance != ''
            GROUP BY b.id
        ''', (mes_str,)).fetchall()
        conn.close()

        log.info(f'[AgendaSC Resumo] Enviando resumo de {mes_nome} para {len(bizs)} negócio(s)')
        for biz in bizs:
            if not biz['phone'] or biz['total_appts'] == 0:
                continue
            nome_dono = (biz['owner_name'] or '').split()[0]
            msg = (
                f"📊 *Resumo de {mes_nome} — {biz['name']}*\n\n"
                f"✅ Atendimentos: *{biz['total_appts']}*\n"
                f"👥 Clientes atendidos: *{biz['clientes_unicos']}*\n"
                f"💰 Receita estimada: *R$ {biz['receita']:.0f}*\n\n"
                f"Parabéns, {nome_dono}! Continue assim. 💪\n\n"
                f"Ver painel: https://4kitem.com.br/agenda/painel"
            )
            _agenda_send_whatsapp(biz['phone'], msg, biz['mandazap_instance'])
            time.sleep(2)

    except Exception as e:
        log.error(f'[AgendaSC Resumo] Erro: {e}')


def _agenda_run_resumo_mensal():
    """Wrapper com verificação de dia — só executa no dia 1º."""
    if datetime.now().day != 1:
        return
    _agenda_enviar_resumo()


def _agenda_resumo_loop():
    """Thread daemon: verifica resumo mensal a cada 6 horas."""
    time.sleep(600)  # 10 min após startup
    while True:
        _agenda_run_resumo_mensal()
        time.sleep(21600)  # a cada 6h


# ══════════════════════════════════════════════════════════════════════════
#  ALERTA SC — SaaS de Monitoramento CNH & Veículo
# ══════════════════════════════════════════════════════════════════════════

@app.route('/alerta/cadastro', methods=['GET', 'POST'])
def alerta_cadastro():
    error   = None
    success = False
    phone   = ''
    plano   = request.args.get('plano', request.form.get('plano', 'familia'))
    if request.method == 'POST':
        name  = request.form.get('name', '').strip()
        cpf   = request.form.get('cpf', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        plano = request.form.get('plano', 'familia')

        _FROTA_PLANS = {'pequena_frota', 'frota_media', 'master', 'enterprise'}
        plates = []
        if plano == 'basico':
            p = request.form.get('plate_1', '').strip().upper()
            d = request.form.get('desc_1', '').strip()
            if p:
                plates.append({'plate': p, 'desc': d})
        elif plano == 'familia':
            for i in range(1, 5):
                p = request.form.get(f'plate_f{i}', '').strip().upper()
                d = request.form.get(f'desc_f{i}', '').strip()
                if p:
                    plates.append({'plate': p, 'desc': d})
        elif plano in _FROTA_PLANS:
            for line in request.form.get('plates_text', '').strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                if ',' in line:
                    parts = line.split(',', 1)
                    p, d = parts[0].strip().upper(), parts[1].strip()
                else:
                    p, d = line.strip().upper(), ''
                if p:
                    plates.append({'plate': p, 'desc': d})

        if not all([name, phone]):
            error = 'Nome e WhatsApp são obrigatórios.'
        elif not plates:
            error = 'Informe ao menos uma placa de veículo.'
        else:
            conn = get_saas_db()
            conn.execute('''
                INSERT INTO alerta_subscribers
                (name, cpf, plates_json, phone, email, plano, status, payment_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', 'pending', ?)
            ''', (name, cpf, _json.dumps(plates), phone, email, plano, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            success = True

    return render_template('alerta/cadastro.html', error=error, success=success,
                           plano=plano, phone=phone, plans=ALERTA_PLANS,
                           req_name=request.form.get('name', ''),
                           req_cpf=request.form.get('cpf', ''),
                           req_phone=request.form.get('phone', ''),
                           req_email=request.form.get('email', ''),
                           # basico pre-fill
                           req_plate_1=request.form.get('plate_1', ''),
                           req_desc_1=request.form.get('desc_1', ''),
                           # familia pre-fill
                           req_plate_2=request.form.get('plate_f2', ''),
                           req_desc_2=request.form.get('desc_f2', ''),
                           req_plate_3=request.form.get('plate_f3', ''),
                           req_desc_3=request.form.get('desc_f3', ''),
                           # frota pre-fill (textarea)
                           req_plates=request.form.get('plates_text', ''))


# ── SaaS Admin ────────────────────────────────────────────────────────────────

@app.route('/saas-admin/login', methods=['GET', 'POST'])
def saas_admin_login():
    error = None
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if pw == SAAS_ADMIN_PW:
            session['saas_admin'] = True
            return redirect('/saas-admin')
        error = 'Senha incorreta.'
    return render_template('saas_admin_login.html', error=error)


@app.route('/saas-admin/logout')
def saas_admin_logout():
    session.pop('saas_admin', None)
    return redirect('/saas-admin/login')


@app.route('/saas-admin/unban', methods=['GET', 'POST'])
@_saas_admin_required
def saas_admin_unban():
    """Desbanir e-mail ou telefone já usado em qualquer SaaS."""
    resultado = None
    mensagem = None
    busca = request.form.get('busca', '').strip() if request.method == 'POST' else ''
    acao = request.form.get('acao', '')
    tabela = request.form.get('tabela', '')
    registro_id = request.form.get('registro_id', '')

    if acao == 'deletar' and tabela and registro_id:
        try:
            if 'petmed' in tabela:
                from petmed_db import get_petmed_db as _get_pm_db
                conn = _get_pm_db()
                conn.execute('DELETE FROM petmed_users WHERE id=?', (registro_id,))
            elif 'kids' in tabela or 'clients' in tabela:
                conn = get_kids_conn()
                conn.execute('DELETE FROM clients WHERE id=?', (registro_id,))
            else:
                conn = get_saas_db()
                conn.execute(f'DELETE FROM {tabela} WHERE id=?', (registro_id,))
            conn.commit(); conn.close()
            mensagem = f'✅ Registro removido de {tabela} (id={registro_id}). O e-mail/telefone pode ser usado novamente.'
        except Exception as e:
            mensagem = f'❌ Erro ao remover: {e}'
        return render_template('saas_admin_unban.html', resultado=None, mensagem=mensagem, busca='')

    if request.method == 'POST' and busca:
        conn = get_saas_db()
        busca_lower = busca.lower()
        busca_digits = ''.join(c for c in busca if c.isdigit())
        encontrados = []

        tabelas = [
            ('agenda_businesses',  'phone', 'email', 'name'),
            ('alerta_subscribers', 'phone', 'email', 'name'),
            ('mandazap_users',     'email', 'email', 'name'),
            ('bau_users',          'email', 'email', 'name'),
            ('mandaja_stores',     'phone', 'email', 'name'),
        ]
        for (tb, col_phone, col_email, col_name) in tabelas:
            try:
                rows = conn.execute(
                    f"SELECT id, {col_name} as nome, {col_phone} as telefone, {col_email} as email, created_at FROM {tb}"
                ).fetchall()
                for r in rows:
                    r = dict(r)
                    phone_d = ''.join(c for c in (r.get('telefone') or '') if c.isdigit())
                    if (busca_lower in (r.get('email') or '').lower() or
                        (busca_digits and busca_digits in phone_d)):
                        encontrados.append({
                            'tabela': tb, 'id': r['id'],
                            'nome': r.get('nome', ''),
                            'email': r.get('email', ''),
                            'telefone': r.get('telefone', ''),
                            'created_at': r.get('created_at', ''),
                        })
            except Exception:
                pass

        conn.close()

        # VetZap (petmed.db)
        try:
            from petmed_db import get_petmed_db as _get_pm_db
            pmconn = _get_pm_db()
            pm_rows = pmconn.execute('SELECT id, nome, email, telefone, created_at FROM petmed_users').fetchall()
            for r in pm_rows:
                r = dict(r)
                phone_d = ''.join(c for c in (r.get('telefone') or '') if c.isdigit())
                if (busca_lower in (r.get('email') or '').lower() or
                    (busca_digits and busca_digits in phone_d)):
                    encontrados.append({
                        'tabela': 'petmed_users (VetZap)', 'id': r['id'],
                        'nome': r.get('nome', ''), 'email': r.get('email', ''),
                        'telefone': r.get('telefone', ''),
                        'created_at': r.get('created_at', ''),
                        'petmed_db': True,
                    })
            pmconn.close()
        except Exception:
            pass

        # KidsCurator (kids.db)
        try:
            kconn = get_kids_conn()
            kids_rows = kconn.execute('SELECT id, name, email, created_at FROM clients').fetchall()
            for r in kids_rows:
                r = dict(r)
                if busca_lower in (r.get('email') or '').lower():
                    encontrados.append({
                        'tabela': 'clients (KidsCurator)', 'id': r['id'],
                        'nome': r.get('name', ''), 'email': r.get('email', ''),
                        'telefone': '', 'created_at': r.get('created_at', ''),
                        'kids_db': True,
                    })
            kconn.close()
        except Exception:
            pass

        resultado = encontrados

    return render_template('saas_admin_unban.html', resultado=resultado, mensagem=mensagem, busca=busca)


@app.route('/saas-admin/petmed-diag')
@_saas_admin_required
def saas_petmed_diag():
    """Diagnóstico do banco PETmed — verifica tabelas, colunas e tenta INSERT de teste."""
    resultado = {}
    try:
        from petmed_db import get_petmed_db as _get_pm, init_petmed_db as _init_pm
        conn = _get_pm()
        # Lista tabelas
        tabelas = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        resultado['tabelas'] = tabelas
        # Verifica colunas de petmed_users
        if 'petmed_users' in tabelas:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(petmed_users)").fetchall()]
            resultado['colunas_petmed_users'] = cols
            resultado['total_users'] = conn.execute("SELECT COUNT(*) FROM petmed_users").fetchone()[0]
        else:
            resultado['ERRO'] = 'Tabela petmed_users NÃO EXISTE — banco não inicializado!'
            _init_pm()
            resultado['acao'] = 'init_petmed_db() chamado — tente cadastrar novamente'
        conn.close()
    except Exception as e:
        resultado['exception'] = f'{type(e).__name__}: {e}'
    return jsonify(resultado)


@app.route('/saas-admin/asaas-test')
@_saas_admin_required
def saas_asaas_test():
    """Diagnóstico da integração Asaas — mostra resposta real da API."""
    api_key = os.environ.get('ASAAS_API_KEY', '')
    resultado = {
        'api_key_set': bool(api_key),
        'api_key_prefix': api_key[:20] + '...' if api_key else '',
    }
    if api_key:
        resultado['customers'] = _asaas_req('GET', '/customers?limit=1')
        # Tenta criar cliente teste
        resultado['create_test'] = _asaas_req('POST', '/customers', {
            'name': 'Teste Diagnostico',
            'email': 'diagnostico@teste.com',
            'notificationDisabled': True,
        })
    return jsonify(resultado)


@app.route('/saas-admin')
@_saas_admin_required
def saas_admin():
    """Painel de admin do SaaS — lista assinantes do Alerta SC."""
    conn = get_saas_db()
    subscribers = [dict(r) for r in conn.execute('''
        SELECT s.*, COUNT(r.id) as reports_count
        FROM alerta_subscribers s
        LEFT JOIN alerta_reports r ON r.subscriber_id = s.id
        GROUP BY s.id
        ORDER BY s.created_at DESC
    ''').fetchall()]
    for s in subscribers:
        try:
            plates = _json.loads(s.get('plates_json') or '[]')
        except Exception:
            plates = []
        for i, pv in enumerate(plates, 1):
            s[f'plate_{i}'] = pv.get('plate', '') if isinstance(pv, dict) else str(pv)
            s[f'desc_{i}']  = pv.get('desc', '')  if isinstance(pv, dict) else ''
    businesses = [dict(r) for r in conn.execute(
        'SELECT id, name, slug, owner_name, phone, active, created_at, trial_ends FROM agenda_businesses ORDER BY created_at DESC'
    ).fetchall()]
    mz_users = [dict(r) for r in conn.execute(
        'SELECT id, name, email, plan, active, created_at, trial_ends FROM mandazap_users ORDER BY created_at DESC'
    ).fetchall()]
    bau_users = [dict(r) for r in conn.execute(
        'SELECT id, name, email, active, created_at, trial_ends FROM bau_users ORDER BY created_at DESC'
    ).fetchall()]
    conn.close()
    # KidsCurator clients
    try:
        kconn = get_kids_conn()
        kids_clients = [dict(r) for r in kconn.execute(
            'SELECT id, code, name, city, mode, active, created_at FROM clients ORDER BY created_at DESC'
        ).fetchall()]
        kconn.close()
    except Exception:
        kids_clients = []
    # Amigo Despachante — usuários/assinantes do produto
    try:
        conn2 = get_saas_db()
        desp_users = [dict(r) for r in conn2.execute(
            'SELECT id, name, email, phone, empresa, cidade, plan, active, created_at, trial_ends, notes FROM despachante_users ORDER BY created_at DESC'
        ).fetchall()]
        conn2.close()
    except Exception:
        desp_users = []
    # DefesaPro — usuários/assinantes
    try:
        conn3 = get_saas_db()
        defesa_users = [dict(r) for r in conn3.execute(
            'SELECT id, name, email, phone, escritorio, cidade, plan, active, created_at, trial_ends, notes FROM defesapro_users ORDER BY created_at DESC'
        ).fetchall()]
        conn3.close()
    except Exception:
        defesa_users = []
    # MandaJá — lojas
    try:
        conn4 = get_saas_db()
        mandaja_stores = [dict(r) for r in conn4.execute(
            'SELECT id, name, slug, owner_name, phone, email, city, plan, active, created_at FROM mandaja_stores ORDER BY id DESC'
        ).fetchall()]
        conn4.close()
    except Exception:
        mandaja_stores = []
    # VetZap — usuários
    try:
        from petmed_db import get_petmed_db as _get_pm_db
        pmconn = _get_pm_db()
        vetzap_users = [dict(r) for r in pmconn.execute(
            'SELECT id, nome, email, telefone, plano, plano_ativo, created_at FROM petmed_users ORDER BY created_at DESC'
        ).fetchall()]
        vetzap_pets_total = pmconn.execute('SELECT COUNT(*) FROM petmed_pets').fetchone()[0]
        vetzap_triagens_total = pmconn.execute('SELECT COUNT(*) FROM petmed_triagens').fetchone()[0]
        pmconn.close()
    except Exception:
        vetzap_users = []
        vetzap_pets_total = 0
        vetzap_triagens_total = 0
    # PUBSHOW — estabelecimentos
    try:
        from pubshow_db import get_pubshow_db as _get_ps_db
        psconn = _get_ps_db()
        now_iso = datetime.now().isoformat()
        pubshow_bars = [dict(r) for r in psconn.execute(
            'SELECT id, nome, email, telefone, tipo, plano, plano_ativo, suspenso, trial_ends, canal_atual, created_at FROM pubshow_businesses ORDER BY created_at DESC'
        ).fetchall()]
        # Totais globais
        pubshow_total_pedidos = psconn.execute('SELECT COUNT(*) FROM pubshow_pedidos').fetchone()[0]
        pubshow_total_receita = psconn.execute("SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos WHERE status='pago'").fetchone()[0]
        pubshow_total_videos  = psconn.execute('SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1').fetchone()[0]
        psconn.close()
        # Calcula status legível para cada bar
        for b in pubshow_bars:
            if b['suspenso']:
                b['_status'] = 'suspenso'
            elif b['plano_ativo']:
                b['_status'] = 'ativo'
            elif b['trial_ends'] and b['trial_ends'] > now_iso:
                b['_status'] = 'trial'
            else:
                b['_status'] = 'inativo'
    except Exception:
        pubshow_bars = []
        pubshow_total_pedidos = 0
        pubshow_total_receita = 0
        pubshow_total_videos  = 0
    # SlotZap users
    try:
        conn_sz = get_saas_db()
        sz_users = [dict(r) for r in conn_sz.execute(
            'SELECT id, name, email, phone, active, created_at, last_login FROM slotzap_users ORDER BY id DESC'
        ).fetchall()]
        conn_sz.close()
    except Exception:
        sz_users = []
    return render_template('saas_admin.html',
                           subscribers=subscribers, businesses=businesses,
                           mz_users=mz_users, mz_plans=MANDAZAP_PLANS,
                           bau_users=bau_users,
                           kids_clients=kids_clients, kids_modes=MODES,
                           desp_users=desp_users, desp_plans=DESP_PLANS,
                           defesa_users=defesa_users,
                           mandaja_stores=mandaja_stores, mandaja_plans=MANDAJA_PLANS,
                           vetzap_users=vetzap_users,
                           vetzap_pets_total=vetzap_pets_total,
                           vetzap_triagens_total=vetzap_triagens_total,
                           alerta_plans=ALERTA_PLANS,
                           pubshow_bars=pubshow_bars,
                           pubshow_total_pedidos=pubshow_total_pedidos,
                           pubshow_total_receita=pubshow_total_receita,
                           pubshow_total_videos=pubshow_total_videos,
                           sz_users=sz_users)


@app.route('/saas-admin/slotzap/reset-senha', methods=['POST'])
@_saas_admin_required
def saas_sz_reset_senha():
    data  = request.get_json() or {}
    uid   = data.get('user_id')
    senha = (data.get('senha') or '').strip()
    if not uid or len(senha) < 6:
        return jsonify({'erro': 'user_id e senha (mín. 6 chars) obrigatórios'}), 400
    conn = get_saas_db()
    conn.execute('UPDATE slotzap_users SET password_hash=? WHERE id=?',
                 (generate_password_hash(senha), uid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/saas-admin/pubshow/bar/<int:bid>/status', methods=['POST'])
@_saas_admin_required
def saas_pubshow_bar_status(bid):
    from pubshow_db import get_pubshow_db as _get_ps_db
    data   = request.get_json() or {}
    acao   = data.get('acao', '')  # 'ativar' | 'suspender' | 'desativar'
    psconn = _get_ps_db()
    if acao == 'suspender':
        psconn.execute('UPDATE pubshow_businesses SET suspenso=1 WHERE id=?', (bid,))
    elif acao == 'ativar':
        psconn.execute('UPDATE pubshow_businesses SET suspenso=0, plano_ativo=1 WHERE id=?', (bid,))
    elif acao == 'desativar':
        psconn.execute('UPDATE pubshow_businesses SET plano_ativo=0 WHERE id=?', (bid,))
    psconn.commit(); psconn.close()
    return jsonify({'success': True})


@app.route('/saas-admin/pubshow/bar/<int:bid>/plan', methods=['POST'])
@_saas_admin_required
def saas_pubshow_bar_plan(bid):
    from pubshow_db import get_pubshow_db as _get_ps_db
    data  = request.get_json() or {}
    plano = data.get('plano', 'bar')
    psconn = _get_ps_db()
    psconn.execute('UPDATE pubshow_businesses SET plano=?, plano_ativo=1, suspenso=0 WHERE id=?', (plano, bid))
    psconn.commit(); psconn.close()
    return jsonify({'success': True})


@app.route('/saas-admin/pubshow/bar/<int:bid>/trial', methods=['POST'])
@_saas_admin_required
def saas_pubshow_bar_trial(bid):
    from pubshow_db import get_pubshow_db as _get_ps_db
    data  = request.get_json() or {}
    trial = data.get('trial_ends', '').strip()
    psconn = _get_ps_db()
    psconn.execute('UPDATE pubshow_businesses SET trial_ends=? WHERE id=?', (trial or None, bid))
    psconn.commit(); psconn.close()
    return jsonify({'success': True})


@app.route('/saas-admin/pubshow/reseed', methods=['POST'])
@_saas_admin_required
def saas_pubshow_reseed():
    """Força re-inserção de todos os vídeos do seed (INSERT OR IGNORE — seguro)."""
    from pubshow_db import get_pubshow_db as _get_ps_db, _seed_videos as _sv
    psconn = _get_ps_db()
    before = psconn.execute('SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1').fetchone()[0]
    _sv(psconn)
    after  = psconn.execute('SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1').fetchone()[0]
    psconn.close()
    return jsonify({'success': True, 'antes': before, 'depois': after, 'novos': after - before})


@app.route('/admin/alerta/<int:sub_id>/status', methods=['POST'])
@_saas_admin_required
def saas_alerta_status(sub_id):
    data = request.get_json() or {}
    new_status = data.get('status', 'active')
    conn = get_saas_db()
    conn.execute('UPDATE alerta_subscribers SET status=? WHERE id=?', (new_status, sub_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/alerta/<int:sub_id>/payment', methods=['POST'])
@_saas_admin_required
def saas_alerta_payment(sub_id):
    now = datetime.now().isoformat()
    conn = get_saas_db()
    conn.execute(
        'UPDATE alerta_subscribers SET payment_status=?, status=?, paid_at=? WHERE id=?',
        ('paid', 'active', now, sub_id)
    )
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/alerta/<int:sub_id>/notes', methods=['POST'])
@_saas_admin_required
def saas_alerta_notes(sub_id):
    data  = request.get_json() or {}
    notes = data.get('notes', '')
    conn  = get_saas_db()
    conn.execute('UPDATE alerta_subscribers SET notes=? WHERE id=?', (notes, sub_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/mandazap/user/<int:user_id>/plan', methods=['POST'])
@_saas_admin_required
def saas_mz_set_plan(user_id):
    data = request.get_json() or {}
    plan = data.get('plan', 'solo')
    if plan not in MANDAZAP_PLANS:
        return jsonify({'success': False, 'error': 'Plano inválido'}), 400
    conn = get_saas_db()
    conn.execute('UPDATE mandazap_users SET plan=?, active=1 WHERE id=?', (plan, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'plan': plan})


@app.route('/admin/bau/user/<int:user_id>/status', methods=['POST'])
@_saas_admin_required
def saas_bau_set_status(user_id):
    data   = request.get_json() or {}
    active = 1 if data.get('active', True) else 0
    trial  = (datetime.now() + timedelta(days=3650)).isoformat()  # 10 anos
    conn   = get_saas_db()
    conn.execute('UPDATE bau_users SET active=?, trial_ends=? WHERE id=?', (active, trial, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/alerta/<int:sub_id>/report', methods=['POST'])
@_saas_admin_required
def saas_alerta_report(sub_id):
    now     = datetime.now().isoformat()
    data    = request.get_json() or {}
    message = data.get('message', '')
    conn    = get_saas_db()
    sub = conn.execute('SELECT * FROM alerta_subscribers WHERE id=?', (sub_id,)).fetchone()
    if not sub:
        conn.close()
        return jsonify({'success': False, 'error': 'Assinante não encontrado'}), 404
    conn.execute(
        'INSERT INTO alerta_reports (subscriber_id, message, sent_at, created_at) VALUES (?,?,?,?)',
        (sub_id, message, now, now)
    )
    conn.execute('UPDATE alerta_subscribers SET last_report_at=? WHERE id=?', (now, sub_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/alerta/<int:sub_id>/reports')
@_saas_admin_required
def saas_alerta_reports(sub_id):
    conn = get_saas_db()
    reports = [dict(r) for r in conn.execute(
        'SELECT * FROM alerta_reports WHERE subscriber_id=? ORDER BY created_at DESC', (sub_id,)
    ).fetchall()]
    conn.close()
    return jsonify(reports)


@app.route('/admin/alerta/<int:sub_id>/delete', methods=['POST'])
@_saas_admin_required
def saas_alerta_delete(sub_id):
    conn = get_saas_db()
    conn.execute('DELETE FROM alerta_reports WHERE subscriber_id=?', (sub_id,))
    conn.execute('DELETE FROM alerta_subscribers WHERE id=?', (sub_id,))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ── Admin Alerta SC — trial ───────────────────────────────────────────────────

@app.route('/admin/alerta/<int:sub_id>/trial', methods=['POST'])
@_saas_admin_required
def saas_alerta_trial(sub_id):
    data  = request.get_json() or {}
    trial = data.get('trial_ends', '').strip()
    if not trial:
        return jsonify({'success': False, 'error': 'Data inválida'})
    conn = get_saas_db()
    try:
        conn.execute('ALTER TABLE alerta_subscribers ADD COLUMN trial_ends TEXT')
    except Exception:
        pass
    conn.execute('UPDATE alerta_subscribers SET trial_ends=? WHERE id=?', (trial, sub_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ── Admin MandaZap — status / trial / delete ──────────────────────────────────

@app.route('/admin/mandazap/user/<int:user_id>/status', methods=['POST'])
@_saas_admin_required
def saas_mz_set_status(user_id):
    data   = request.get_json() or {}
    active = 1 if data.get('active') else 0
    conn   = get_saas_db()
    conn.execute('UPDATE mandazap_users SET active=? WHERE id=?', (active, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/mandazap/user/<int:user_id>/trial', methods=['POST'])
@_saas_admin_required
def saas_mz_set_trial(user_id):
    data  = request.get_json() or {}
    trial = data.get('trial_ends', '').strip()
    if not trial:
        return jsonify({'success': False, 'error': 'Data inválida'})
    conn = get_saas_db()
    conn.execute('UPDATE mandazap_users SET trial_ends=? WHERE id=?', (trial, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/mandazap/user/<int:user_id>/delete', methods=['POST'])
@_saas_admin_required
def saas_mz_delete(user_id):
    conn = get_saas_db()
    conn.execute('DELETE FROM mandazap_numbers   WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM mandazap_contacts  WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM mandazap_lists     WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM mandazap_campaigns WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM mandazap_templates WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM mandazap_users     WHERE id=?',      (user_id,))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ── Admin Baú — trial / delete ────────────────────────────────────────────────

@app.route('/admin/bau/user/<int:user_id>/trial', methods=['POST'])
@_saas_admin_required
def saas_bau_set_trial(user_id):
    data  = request.get_json() or {}
    trial = data.get('trial_ends', '').strip()
    if not trial:
        return jsonify({'success': False, 'error': 'Data inválida'})
    conn = get_saas_db()
    conn.execute('UPDATE bau_users SET trial_ends=? WHERE id=?', (trial, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/bau/user/<int:user_id>/delete', methods=['POST'])
@_saas_admin_required
def saas_bau_delete(user_id):
    conn = get_saas_db()
    try:
        conn.execute('DELETE FROM bau_entries WHERE user_id=?', (user_id,))
    except Exception:
        pass
    conn.execute('DELETE FROM bau_users WHERE id=?', (user_id,))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ── Admin Alerta SC — mudar plano ────────────────────────────────────────────

@app.route('/admin/alerta/<int:sub_id>/plano', methods=['POST'])
@_saas_admin_required
def saas_alerta_plano(sub_id):
    data  = request.get_json() or {}
    plano = data.get('plano', 'basico')
    if plano not in ALERTA_PLANS:
        return jsonify({'success': False, 'error': 'Plano inválido'}), 400
    conn = get_saas_db()
    conn.execute('UPDATE alerta_subscribers SET plano=? WHERE id=?', (plano, sub_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'plano': plano, 'label': ALERTA_PLANS[plano]['label']})


# ── Admin AlertaSC — monitoramento manual ─────────────────────────────────────

@app.route('/saas-admin/alerta/monitorar-agora', methods=['POST'])
@_saas_admin_required
def saas_alerta_monitorar_agora():
    """Dispara o ciclo completo de monitoramento AlertaSC em background."""
    threading.Thread(target=_alerta_run_monitoring, daemon=True).start()
    return jsonify({'ok': True, 'msg': 'Monitoramento iniciado em background'})


# ── Admin AgendaSC — lembretes manuais ────────────────────────────────────────

@app.route('/saas-admin/agenda/lembretes-agora', methods=['POST'])
@_saas_admin_required
def saas_agenda_lembretes_agora():
    """Dispara o ciclo de lembretes 24h AgendaSC em background."""
    threading.Thread(target=_agenda_run_lembretes, daemon=True).start()
    return jsonify({'ok': True, 'msg': 'Lembretes 24h iniciados em background'})


@app.route('/saas-admin/agenda/lembretes-2h-agora', methods=['POST'])
@_saas_admin_required
def saas_agenda_lembretes_2h_agora():
    """Dispara o ciclo de lembretes 2h AgendaSC em background."""
    threading.Thread(target=_agenda_run_lembretes_2h, daemon=True).start()
    return jsonify({'ok': True, 'msg': 'Lembretes 2h iniciados em background'})


@app.route('/saas-admin/agenda/resumo-agora', methods=['POST'])
@_saas_admin_required
def saas_agenda_resumo_agora():
    """Dispara o resumo mensal AgendaSC em background, ignorando verificação de dia."""
    threading.Thread(target=_agenda_enviar_resumo, daemon=True).start()
    return jsonify({'ok': True, 'msg': 'Resumo mensal disparado em background'})


# ── Admin KidsCurator — status / delete ───────────────────────────────────────

@app.route('/admin/kids/client/<int:client_id>/mode', methods=['POST'])
@_saas_admin_required
def saas_kids_set_mode(client_id):
    data = request.get_json() or {}
    mode = data.get('mode', 'kids')
    if mode not in MODES:
        return jsonify({'success': False, 'error': 'Modo inválido'}), 400
    conn = get_kids_conn()
    conn.execute('UPDATE clients SET mode=? WHERE id=?', (mode, client_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'mode': mode, 'label': MODES[mode]['label']})


@app.route('/admin/kids/client/<int:client_id>/status', methods=['POST'])
@_saas_admin_required
def saas_kids_set_status(client_id):
    data   = request.get_json() or {}
    active = 1 if data.get('active') else 0
    conn   = get_kids_conn()
    conn.execute('UPDATE clients SET active=? WHERE id=?', (active, client_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/kids/client/<int:client_id>/delete', methods=['POST'])
@_saas_admin_required
def saas_kids_delete(client_id):
    conn = get_kids_conn()
    conn.execute('DELETE FROM clients WHERE id=?', (client_id,))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ── Admin Amigo Despachante — gerenciar usuários ──────────────────────────────

@app.route('/admin/despachante/user/<int:user_id>/plan', methods=['POST'])
@_saas_admin_required
def saas_desp_set_plan(user_id):
    data = request.get_json() or {}
    plan = data.get('plan', 'basico')
    if plan not in DESP_PLANS:
        return jsonify({'success': False, 'error': 'Plano inválido'}), 400
    conn = get_saas_db()
    conn.execute('UPDATE despachante_users SET plan=? WHERE id=?', (plan, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'plan': plan, 'label': DESP_PLANS[plan]['label']})


@app.route('/admin/despachante/user/<int:user_id>/senha', methods=['POST'])
@_saas_admin_required
def saas_desp_set_senha(user_id):
    nova = (request.get_json() or {}).get('senha', '').strip()
    if not nova or len(nova) < 4:
        return jsonify({'success': False, 'error': 'Senha muito curta (mín. 4 caracteres)'})
    conn = get_saas_db()
    u = conn.execute('SELECT id FROM despachante_users WHERE id=?', (user_id,)).fetchone()
    if not u:
        conn.close()
        return jsonify({'success': False, 'error': 'Usuário não encontrado'})
    conn.execute('UPDATE despachante_users SET password_hash=? WHERE id=?',
                 (generate_password_hash(nova), user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/despachante/user/<int:user_id>/status', methods=['POST'])
@_saas_admin_required
def saas_desp_set_status(user_id):
    data   = request.get_json() or {}
    active = 1 if data.get('active') else 0
    conn   = get_saas_db()
    conn.execute('UPDATE despachante_users SET active=? WHERE id=?', (active, user_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/admin/despachante/user/<int:user_id>/trial', methods=['POST'])
@_saas_admin_required
def saas_desp_set_trial(user_id):
    data  = request.get_json() or {}
    trial = data.get('trial_ends', '').strip()
    conn  = get_saas_db()
    conn.execute('UPDATE despachante_users SET trial_ends=? WHERE id=?', (trial or None, user_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'trial_ends': trial})


@app.route('/admin/despachante/user/<int:user_id>/delete', methods=['POST'])
@_saas_admin_required
def saas_desp_delete(user_id):
    conn = get_saas_db()
    try:
        conn.execute('DELETE FROM despachante_users WHERE id=?', (user_id,))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})
    conn.close()
    return jsonify({'success': True})


@app.route('/admin/despachante/user/novo', methods=['POST'])
@_saas_admin_required
def saas_desp_novo_user():
    from datetime import datetime
    data = request.get_json() or {}
    name    = data.get('name', '').strip()
    phone   = data.get('phone', '').strip()
    email   = data.get('email', '').strip()
    empresa = data.get('empresa', '').strip()
    cidade  = data.get('cidade', '').strip()
    plan    = data.get('plan', 'basico')
    if not name or not phone:
        return jsonify({'success': False, 'error': 'Nome e telefone obrigatórios'})
    conn = get_saas_db()
    try:
        cur = conn.execute(
            'INSERT INTO despachante_users (name, email, phone, empresa, cidade, plan, active, created_at) VALUES (?,?,?,?,?,?,1,?)',
            (name, email, phone, empresa, cidade, plan, datetime.now().isoformat())
        )
        conn.commit()
        new_id = cur.lastrowid
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})
    conn.close()
    return jsonify({'success': True, 'id': new_id})


# ── Admin DefesaPro — gerenciar usuários ─────────────────────────────────────

@app.route('/admin/defesapro/user/<int:user_id>/plan', methods=['POST'])
@_saas_admin_required
def saas_defesa_set_plan(user_id):
    data  = request.get_json() or {}
    plan  = data.get('plan', 'starter')
    valid = {'starter', 'profissional', 'premium'}
    if plan not in valid:
        return jsonify({'success': False, 'error': 'Plano inválido'}), 400
    conn = get_saas_db()
    conn.execute('UPDATE defesapro_users SET plan=? WHERE id=?', (plan, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'plan': plan})


@app.route('/admin/defesapro/user/<int:user_id>/status', methods=['POST'])
@_saas_admin_required
def saas_defesa_set_status(user_id):
    data   = request.get_json() or {}
    active = 1 if data.get('active') else 0
    conn   = get_saas_db()
    conn.execute('UPDATE defesapro_users SET active=? WHERE id=?', (active, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/defesapro/user/<int:user_id>/trial', methods=['POST'])
@_saas_admin_required
def saas_defesa_set_trial(user_id):
    data  = request.get_json() or {}
    trial = data.get('trial_ends', '').strip()
    conn  = get_saas_db()
    conn.execute('UPDATE defesapro_users SET trial_ends=? WHERE id=?', (trial or None, user_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'trial_ends': trial})


@app.route('/admin/defesapro/user/<int:user_id>/delete', methods=['POST'])
@_saas_admin_required
def saas_defesa_delete(user_id):
    conn = get_saas_db()
    try:
        conn.execute('DELETE FROM defesapro_users WHERE id=?', (user_id,))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})
    conn.close()
    return jsonify({'success': True})


@app.route('/admin/defesapro/user/novo', methods=['POST'])
@_saas_admin_required
def saas_defesa_novo_user():
    from datetime import datetime
    data       = request.get_json() or {}
    name       = data.get('name', '').strip()
    phone      = data.get('phone', '').strip()
    email      = data.get('email', '').strip()
    escritorio = data.get('escritorio', '').strip()
    cidade     = data.get('cidade', '').strip()
    plan       = data.get('plan', 'starter')
    if not name or not phone:
        return jsonify({'success': False, 'error': 'Nome e telefone obrigatórios'})
    conn = get_saas_db()
    try:
        cur = conn.execute(
            'INSERT INTO defesapro_users (name, email, phone, escritorio, cidade, plan, active, created_at) VALUES (?,?,?,?,?,?,1,?)',
            (name, email, phone, escritorio, cidade, plan, datetime.now().isoformat())
        )
        conn.commit()
        new_id = cur.lastrowid
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})
    conn.close()
    return jsonify({'success': True, 'id': new_id})


# ── Admin MandaJá ─────────────────────────────────────────────────────────────

@app.route('/admin/mandaja/store/<int:store_id>/plan', methods=['POST'])
@_saas_admin_required
def saas_mandaja_set_plan(store_id):
    data = request.get_json() or {}
    plan = data.get('plan', 'micro')
    if plan not in MANDAJA_PLANS:
        return jsonify({'success': False, 'error': 'Plano inválido'}), 400
    conn = get_saas_db()
    conn.execute('UPDATE mandaja_stores SET plan=? WHERE id=?', (plan, store_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'plan': plan, 'label': MANDAJA_PLANS[plan]['label']})


@app.route('/admin/mandaja/store/<int:store_id>/toggle', methods=['POST'])
@_saas_admin_required
def saas_mandaja_toggle(store_id):
    conn = get_saas_db()
    row  = conn.execute('SELECT active FROM mandaja_stores WHERE id=?', (store_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Loja não encontrada'})
    new_active = 0 if row['active'] else 1
    conn.execute('UPDATE mandaja_stores SET active=? WHERE id=?', (new_active, store_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'active': new_active})


# ── Admin Agenda SC ───────────────────────────────────────────────────────────

@app.route('/admin/agenda/<int:biz_id>/status', methods=['POST'])
@_saas_admin_required
def saas_agenda_status(biz_id):
    data   = request.get_json() or {}
    active = 1 if data.get('active') else 0
    conn   = get_saas_db()
    conn.execute('UPDATE agenda_businesses SET active=? WHERE id=?', (active, biz_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/agenda/<int:biz_id>/trial', methods=['POST'])
@_saas_admin_required
def saas_agenda_trial(biz_id):
    data  = request.get_json() or {}
    trial = data.get('trial_ends', '').strip()
    if not trial:
        return jsonify({'success': False, 'error': 'Data inválida'})
    conn = get_saas_db()
    conn.execute('UPDATE agenda_businesses SET trial_ends=? WHERE id=?', (trial, biz_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/admin/agenda/<int:biz_id>/delete', methods=['POST'])
@_saas_admin_required
def saas_agenda_delete(biz_id):
    conn = get_saas_db()
    try:
        conn.execute('DELETE FROM agenda_appointments WHERE business_id=?', (biz_id,))
        conn.execute('DELETE FROM agenda_services     WHERE business_id=?', (biz_id,))
        conn.execute('DELETE FROM agenda_availability WHERE business_id=?', (biz_id,))
        conn.execute('DELETE FROM agenda_customers    WHERE business_id=?', (biz_id,))
        conn.execute('DELETE FROM agenda_payments     WHERE business_id=?', (biz_id,))
        conn.execute('DELETE FROM agenda_businesses   WHERE id=?',          (biz_id,))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})
    conn.close()
    return jsonify({'success': True})


@app.route('/admin/agenda/<int:biz_id>/reset-senha', methods=['POST'])
@_saas_admin_required
def saas_agenda_reset_senha(biz_id):
    nova_senha = request.json.get('senha', '').strip()
    if not nova_senha or len(nova_senha) < 4:
        return jsonify({'success': False, 'error': 'Senha muito curta (mín. 4 caracteres)'})
    conn = get_saas_db()
    biz = conn.execute('SELECT id FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone()
    if not biz:
        conn.close()
        return jsonify({'success': False, 'error': 'Negócio não encontrado'})
    conn.execute('UPDATE agenda_businesses SET password_hash=? WHERE id=?',
                 (generate_password_hash(nova_senha), biz_id))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ══════════════════════════════════════════════════════════════════════════
#  TV PLAYER  — /tv/<code>
# ══════════════════════════════════════════════════════════════════════════

@app.route('/tv')
def tv_redirect():
    return redirect(url_for('tv_player', code='DEMO'))

@app.route('/tv/<code>')
def tv_player(code):
    client = get_client(code)
    if not client:
        abort(404)
    mode_info = MODES.get(client['mode'], MODES['kids'])
    return render_template('tv/player.html',
                           client=client,
                           mode_info=mode_info,
                           modes=MODES)


# ══════════════════════════════════════════════════════════════════════════
#  PAINEL DO CLIENTE  — /painel/<code>
# ══════════════════════════════════════════════════════════════════════════

@app.route('/painel')
def painel_redirect():
    return redirect(url_for('painel', code='DEMO'))

@app.route('/painel/<code>')
def painel(code):
    client = get_client(code)
    if not client:
        abort(404)
    return render_template('painel/index.html',
                           client=client,
                           modes=MODES)


# ══════════════════════════════════════════════════════════════════════════
#  API  — /api/...
# ══════════════════════════════════════════════════════════════════════════

# ── Playlist para o TV player ─────────────────────────────────────────────
@app.route('/api/tv/<code>/playlist')
def api_tv_playlist(code):
    client = get_client(code)
    if not client:
        return jsonify({'error': 'client not found'}), 404
    limit  = min(request.args.get('limit', 30, type=int), 50)
    videos = get_videos_for_mode(client['mode'], limit=limit, shuffle=True)
    return jsonify({
        'videos':    videos,
        'mode':      client['mode'],
        'mode_info': MODES.get(client['mode'], {}),
        'client':    {
            'code':       client['code'],
            'name':       client['name'],
            'logo_url':   client['logo_url'],
            'city':       client['city'],
            'ticker_msg': client['ticker_msg'],
        },
    })

# ── Status (usado pelo TV player para detectar troca de modo) ─────────────
@app.route('/api/tv/<code>/status')
def api_tv_status(code):
    client = get_client(code)
    if not client:
        return jsonify({'error': 'client not found'}), 404
    return jsonify({
        'mode':     client['mode'],
        'name':     client['name'],
        'logo_url': client['logo_url'],
    })

# ── Mudar modo (painel → POST) ────────────────────────────────────────────
@app.route('/api/tv/<code>/mode', methods=['POST'])
def api_set_mode(code):
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', '')
    if not set_client_mode(code, mode):
        return jsonify({'error': f'modo inválido: {mode}'}), 400
    return jsonify({'ok': True, 'mode': mode,
                    'mode_label': MODES[mode]['label']})

# ── Criar cliente ─────────────────────────────────────────────────────────
@app.route('/api/clients', methods=['POST'])
def api_create_client():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    city = data.get('city', 'Brasil').strip()
    mode = data.get('mode', 'kids')
    if not name:
        return jsonify({'error': 'name obrigatório'}), 400
    client = create_client(name, city, mode)
    return jsonify({'ok': True, 'client': dict(client)}), 201

# ── Vídeos filtrados (landing pages) ─────────────────────────────────────
@app.route('/api/kids/videos')
def api_kids_videos():
    age    = request.args.get('age',    type=int)
    gender = request.args.get('gender', default=None)
    limit  = min(request.args.get('limit',  24, type=int), 48)
    offset = request.args.get('offset', 0,  type=int)
    videos = get_videos(age=age, gender=gender, limit=limit, offset=offset)
    total  = total_videos(age=age, gender=gender)
    return jsonify({
        'videos':   videos,
        'total':    total,
        'offset':   offset,
        'limit':    limit,
        'has_more': (offset + limit) < total,
    })

@app.route('/api/kids/channels')
def api_kids_channels():
    return jsonify({'channels': get_channels()})

@app.route('/api/kids/stats')
def api_kids_stats():
    return jsonify(stats())

@app.route('/api/modes')
def api_modes():
    return jsonify(MODES)

# ── Blacklist: vídeo com embed bloqueado reportado pelo player ────────────
@app.route('/api/tv/<code>/bad-video/<youtube_id>', methods=['POST'])
def api_bad_video(code, youtube_id):
    """Player reporta vídeo com embed bloqueado → remove do banco."""
    client = get_client(code)
    if not client:
        return jsonify({'error': 'client not found'}), 404
    removed = mark_video_blocked(youtube_id)
    log.info(f"Vídeo bloqueado reportado: {youtube_id} (removido={removed})")
    return jsonify({'ok': True, 'removed': removed})


# ── Admin: refresh scraper ────────────────────────────────────────────────
@app.route('/kids/admin/refresh', methods=['POST'])
def kids_refresh():
    def _run():
        try:
            from kids_scraper import scrape_all
            scrape_all()
        except Exception as e:
            log.error(f"Scrape error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'status': 'started'})


# ══════════════════════════════════════════════════════════════════════════
#  BAÚ SC — Cofre digital de credenciais
# ══════════════════════════════════════════════════════════════════════════

@app.route('/bau')
def bau():
    return render_template('bau/landing.html')


@app.route('/bau/cadastro', methods=['GET', 'POST'])
def bau_cadastro():
    error = None
    if request.method == 'POST':
        name      = request.form.get('name', '').strip()
        email     = request.form.get('email', '').strip().lower()
        password  = request.form.get('password', '')
        phone     = request.form.get('phone', '').strip()
        cpf_cnpj  = request.form.get('cpf_cnpj', '').strip()
        cpf_digits = ''.join(c for c in cpf_cnpj if c.isdigit())
        phone_digits = ''.join(c for c in phone if c.isdigit())
        if not all([name, email, password, phone, cpf_cnpj]):
            error = 'Preencha todos os campos obrigatórios.'
        elif len(password) < 6:
            error = 'A senha deve ter pelo menos 6 caracteres.'
        elif len(cpf_digits) not in (11, 14):
            error = 'CPF deve ter 11 dígitos ou CNPJ 14 dígitos.'
        else:
            conn = get_saas_db()
            _wl = _is_whitelisted(phone_digits, email)
            # Anti-golpe: e-mail único
            if (not _wl) and conn.execute('SELECT id FROM bau_users WHERE email=?', (email,)).fetchone():
                error = 'E-mail já cadastrado. Faça login.'
                conn.close()
            # Anti-golpe: CPF/CNPJ único
            elif (not _wl) and conn.execute(
                "SELECT id FROM bau_users WHERE replace(replace(replace(cpf_cnpj,'.',''),'-',''),'/','') = ?",
                (cpf_digits,)
            ).fetchone():
                error = 'CPF/CNPJ já possui uma conta. Faça login ou entre em contato.'
                conn.close()
            # Anti-golpe: telefone único
            elif (not _wl) and conn.execute(
                "SELECT id FROM bau_users WHERE replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ','') = ?",
                (phone_digits,)
            ).fetchone():
                error = 'Este WhatsApp já possui uma conta. Faça login ou entre em contato.'
                conn.close()
            else:
                now   = datetime.now()
                trial = (now + timedelta(days=7)).isoformat()
                conn.execute(
                    'INSERT INTO bau_users (name, email, password_hash, phone, cpf_cnpj, created_at, trial_ends) VALUES (?,?,?,?,?,?,?)',
                    (name, email, generate_password_hash(password), phone, cpf_cnpj, now.isoformat(), trial)
                )
                conn.commit()
                user = conn.execute('SELECT * FROM bau_users WHERE email=?', (email,)).fetchone()
                conn.close()
                session['bau_user_id']   = user['id']
                session['bau_user_name'] = user['name']
                return redirect('/bau/painel')
    return render_template('bau/cadastro.html', error=error)


@app.route('/bau/entrar', methods=['GET', 'POST'])
def bau_entrar():
    error = None
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        conn = get_saas_db()
        user = conn.execute('SELECT * FROM bau_users WHERE email=? AND active=1', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['bau_user_id']   = user['id']
            session['bau_user_name'] = user['name']
            return redirect('/bau/painel')
        error = 'E-mail ou senha incorretos.'
    return render_template('bau/entrar.html', error=error)


@app.route('/bau/sair')
def bau_sair():
    session.pop('bau_user_id', None)
    session.pop('bau_user_name', None)
    return redirect('/bau/entrar')


@app.route('/bau/painel')
@_bau_login_required
def bau_painel():
    user_id  = session['bau_user_id']
    q        = request.args.get('q', '').strip()
    cat      = request.args.get('cat', '')
    conn     = get_saas_db()
    user     = dict(conn.execute('SELECT * FROM bau_users WHERE id=?', (user_id,)).fetchone())
    query    = 'SELECT * FROM bau_entries WHERE user_id=?'
    params   = [user_id]
    if q:
        query  += ' AND (title LIKE ? OR username LIKE ? OR url LIKE ? OR hint LIKE ?)'
        params += [f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%']
    if cat:
        query  += ' AND category=?'
        params += [cat]
    query   += ' ORDER BY updated_at DESC'
    entries  = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()
    for e in entries:
        c = BAU_CATEGORIES.get(e['category'], BAU_CATEGORIES['outros'])
        e['cat_label'] = c['label']
        e['cat_icon']  = c['icon']
    # Trial status
    trial_ends = user.get('trial_ends', '')
    trial_active = False
    trial_days_left = 0
    if trial_ends:
        try:
            td = datetime.fromisoformat(trial_ends)
            delta = (td - datetime.now()).days
            trial_active = delta >= 0
            trial_days_left = max(0, delta)
        except Exception:
            pass
    plan_active = user.get('plan_active', 0)
    return render_template('bau/painel.html',
                           entries=entries, categories=BAU_CATEGORIES,
                           q=q, cat=cat,
                           user=user,
                           user_name=session.get('bau_user_name', ''),
                           trial_active=trial_active,
                           trial_days_left=trial_days_left,
                           plan_active=plan_active,
                           bau_plans=BAU_PLANS)


@app.route('/bau/entrada/add', methods=['POST'])
@_bau_login_required
def bau_add():
    user_id  = session['bau_user_id']
    title    = request.form.get('title', '').strip()
    url      = request.form.get('url', '').strip()
    username = request.form.get('username', '').strip()
    hint     = request.form.get('hint', '').strip()
    category = request.form.get('category', 'outros')
    if not title:
        return redirect('/bau/painel')
    now = datetime.now().isoformat()
    conn = get_saas_db()
    conn.execute(
        'INSERT INTO bau_entries (user_id, title, url, username, hint, category, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        (user_id, title, url, username, hint, category, now, now)
    )
    conn.commit()
    conn.close()
    return redirect('/bau/painel')


@app.route('/bau/entrada/<int:entry_id>/edit', methods=['POST'])
@_bau_login_required
def bau_edit(entry_id):
    user_id  = session['bau_user_id']
    title    = request.form.get('title', '').strip()
    url      = request.form.get('url', '').strip()
    username = request.form.get('username', '').strip()
    hint     = request.form.get('hint', '').strip()
    category = request.form.get('category', 'outros')
    now      = datetime.now().isoformat()
    conn     = get_saas_db()
    conn.execute(
        'UPDATE bau_entries SET title=?, url=?, username=?, hint=?, category=?, updated_at=? WHERE id=? AND user_id=?',
        (title, url, username, hint, category, now, entry_id, user_id)
    )
    conn.commit()
    conn.close()
    return redirect('/bau/painel')


@app.route('/bau/entrada/<int:entry_id>/delete', methods=['POST'])
@_bau_login_required
def bau_delete(entry_id):
    user_id = session['bau_user_id']
    conn    = get_saas_db()
    conn.execute('DELETE FROM bau_entries WHERE id=? AND user_id=?', (entry_id, user_id))
    conn.commit()
    conn.close()
    return redirect('/bau/painel')


# ══════════════════════════════════════════════════════════════════════════
#  MANDAZAP — Plataforma de Marketing no WhatsApp
# ══════════════════════════════════════════════════════════════════════════

@app.route('/mandazap')
def mandazap():
    return render_template('mandazap/landing.html', plans=MANDAZAP_PLANS)


@app.route('/mandazap/cadastro', methods=['GET', 'POST'])
def mandazap_cadastro():
    error = None
    if request.method == 'POST':
        name      = request.form.get('name', '').strip()
        email     = request.form.get('email', '').strip().lower()
        password  = request.form.get('password', '')
        phone     = request.form.get('phone', '').strip()
        cpf_cnpj  = request.form.get('cpf_cnpj', '').strip()
        cpf_digits   = ''.join(c for c in cpf_cnpj if c.isdigit())
        phone_digits = ''.join(c for c in phone if c.isdigit())
        if not all([name, email, password, phone, cpf_cnpj]):
            error = 'Preencha todos os campos obrigatórios.'
        elif len(password) < 6:
            error = 'A senha deve ter pelo menos 6 caracteres.'
        elif len(cpf_digits) not in (11, 14):
            error = 'CPF deve ter 11 dígitos ou CNPJ 14 dígitos.'
        else:
            conn = get_saas_db()
            _wl = _is_whitelisted(phone_digits, email)
            if (not _wl) and conn.execute('SELECT id FROM mandazap_users WHERE email=?', (email,)).fetchone():
                error = 'E-mail já cadastrado. Faça login.'
                conn.close()
            elif (not _wl) and conn.execute(
                "SELECT id FROM mandazap_users WHERE replace(replace(replace(cpf_cnpj,'.',''),'-',''),'/','') = ?",
                (cpf_digits,)
            ).fetchone():
                error = 'CPF/CNPJ já possui uma conta. Faça login ou entre em contato.'
                conn.close()
            elif (not _wl) and conn.execute(
                "SELECT id FROM mandazap_users WHERE replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ','') = ?",
                (phone_digits,)
            ).fetchone():
                error = 'Este WhatsApp já possui uma conta. Faça login ou entre em contato.'
                conn.close()
            else:
                now   = datetime.now()
                trial = (now + timedelta(days=7)).isoformat()
                conn.execute(
                    'INSERT INTO mandazap_users (name, email, password_hash, phone, cpf_cnpj, plan, created_at, trial_ends) VALUES (?,?,?,?,?,?,?,?)',
                    (name, email, generate_password_hash(password), phone, cpf_cnpj, 'solo', now.isoformat(), trial)
                )
                conn.commit()
                user = conn.execute('SELECT * FROM mandazap_users WHERE email=?', (email,)).fetchone()
                conn.close()
                session['mz_user_id']   = user['id']
                session['mz_user_name'] = user['name']
                session['mz_plan']      = user['plan']
                # Email de boas-vindas
                if email:
                    _enviar_email(
                        email,
                        '📲 Bem-vindo ao MandaZap — 7 dias grátis!',
                        _email_boas_vindas(
                            'MandaZap', '📲', '#22c55e',
                            name.split()[0],
                            trial,
                            'https://4kitem.com.br/mandazap/painel',
                            'Dispare mensagens para centenas de clientes no WhatsApp com apenas alguns cliques. Importe contatos, crie campanhas e venda mais.'
                        )
                    )
                return redirect('/mandazap/painel')
    return render_template('mandazap/cadastro.html', error=error)


@app.route('/mandazap/entrar', methods=['GET', 'POST'])
def mandazap_entrar():
    error = None
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        conn = get_saas_db()
        user = conn.execute('SELECT * FROM mandazap_users WHERE email=? AND active=1', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['mz_user_id']   = user['id']
            session['mz_user_name'] = user['name']
            session['mz_plan']      = user['plan']
            return redirect('/mandazap/painel')
        error = 'E-mail ou senha incorretos.'
    return render_template('mandazap/entrar.html', error=error)


@app.route('/mandazap/sair')
def mandazap_sair():
    for k in ('mz_user_id', 'mz_user_name', 'mz_plan'):
        session.pop(k, None)
    return redirect('/mandazap')


# ── MandaZap — Recuperação de senha ──────────────────────────────────────────
@app.route('/mandazap/esqueci-senha', methods=['GET', 'POST'])
def mandazap_esqueci_senha():
    enviado = False
    codigo_tela = None
    erro = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        conn = get_saas_db()
        u = conn.execute('SELECT * FROM mandazap_users WHERE email=?', (email,)).fetchone()
        if not u:
            erro = 'E-mail não encontrado.'
            conn.close()
        else:
            codigo = str(random.randint(100000, 999999))
            expires = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute('UPDATE mandazap_users SET reset_token=?, reset_expires=? WHERE id=?',
                         (codigo, expires, u['id']))
            conn.commit(); conn.close()
            html_email = f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <div style="font-size:32px;margin-bottom:8px">📲</div>
              <h2 style="color:#2563eb">Recuperação de senha — MandaZap</h2>
              <p>Olá, <strong>{u['name'].split()[0]}</strong>!</p>
              <p>Seu código de recuperação é:</p>
              <div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#2563eb;
                          background:#eff6ff;padding:20px;border-radius:12px;text-align:center;
                          margin:20px 0">{codigo}</div>
              <p style="color:#666;font-size:13px">Válido por 2 horas.</p>
            </div>"""
            ok = _enviar_email(email, 'Código de recuperação — MandaZap', html_email)
            enviado = True
            if not ok:
                codigo_tela = codigo
    return render_template('mandazap/esqueci_senha.html',
                           enviado=enviado, codigo_tela=codigo_tela, erro=erro)


@app.route('/mandazap/redefinir-senha', methods=['GET', 'POST'])
def mandazap_redefinir_senha():
    sucesso = False
    erro = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        codigo = request.form.get('codigo', '').strip()
        nova = request.form.get('nova_senha', '')
        if len(nova) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        else:
            conn = get_saas_db()
            u = conn.execute('SELECT * FROM mandazap_users WHERE email=?', (email,)).fetchone()
            if not u or u['reset_token'] != codigo:
                erro = 'Código inválido ou e-mail incorreto.'
                conn.close()
            elif u['reset_expires'] and datetime.fromisoformat(u['reset_expires']) < datetime.now():
                erro = 'Código expirado. Solicite um novo.'
                conn.close()
            else:
                conn.execute('UPDATE mandazap_users SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?',
                             (generate_password_hash(nova), u['id']))
                conn.commit(); conn.close()
                sucesso = True
    return render_template('mandazap/redefinir_senha.html', sucesso=sucesso, erro=erro)


# ── MandaZap — Checkout / Assinatura ─────────────────────────────────────────
@app.route('/mandazap/assinar', methods=['GET', 'POST'])
@app.route('/mandazap/assinar/<plano>', methods=['GET', 'POST'])
@_mandazap_login_required
def mandazap_assinar(plano=None):
    user_id = session['mz_user_id']
    if plano is None:
        plano = session.get('mz_plan', 'solo')
    if plano not in MANDAZAP_PLANS:
        plano = 'solo'
    p = MANDAZAP_PLANS[plano]
    erro = None
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX').upper()
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            billing_type = 'PIX'
        conn = get_saas_db()
        u = conn.execute('SELECT * FROM mandazap_users WHERE id=?', (user_id,)).fetchone()
        conn.close()
        if not u:
            return redirect('/mandazap/entrar')
        customer_id = _asaas_criar_ou_buscar_cliente_saas(
            u['name'], u['email'], u.get('phone', ''), u.get('cpf_cnpj', ''), u['id'], 'mandazap_users'
        )
        if not customer_id:
            erro = 'Erro ao processar pagamento. Tente novamente ou entre em contato.'
        else:
            conn2 = get_saas_db()
            conn2.execute('UPDATE mandazap_users SET asaas_customer_id=?, plan=? WHERE id=?',
                          (customer_id, plano, user_id))
            conn2.commit(); conn2.close()
            resp = _asaas_criar_assinatura_saas(
                customer_id, 'mandazap', plano, float(p['price']),  # price is int 79/149/etc
                f'MandaZap {p["label"]} — Assinatura Mensal',
                billing_type
            )
            if resp.get('id'):
                return redirect('/mandazap/aguardando-pagamento')
            else:
                erro = 'Não foi possível gerar o pagamento. Tente novamente.'
    return render_template('mandazap/checkout.html', plano=p, plano_key=plano,
                           planos=MANDAZAP_PLANS, erro=erro)


@app.route('/mandazap/aguardando-pagamento')
@_mandazap_login_required
def mandazap_aguardando():
    return render_template('mandazap/aguardando.html')


@app.route('/mandazap/painel')
@_mandazap_login_required
def mandazap_painel():
    user_id  = session['mz_user_id']
    plan_key = session.get('mz_plan', 'solo')
    conn     = get_saas_db()
    # Trial/plano info
    _mz_row     = conn.execute('SELECT trial_ends, plan_active FROM mandazap_users WHERE id=?', (user_id,)).fetchone()
    trial_ends  = (_mz_row['trial_ends'] or '') if _mz_row else ''
    plan_active = (_mz_row['plan_active'] if _mz_row else 1)
    trial_expired = bool(trial_ends and trial_ends < datetime.now().isoformat())

    contacts  = [dict(r) for r in conn.execute(
        'SELECT * FROM mandazap_contacts WHERE user_id=? ORDER BY name', (user_id,)
    ).fetchall()]
    lists     = [dict(r) for r in conn.execute('''
        SELECT l.*, COUNT(lc.contact_id) as contact_count
        FROM mandazap_lists l
        LEFT JOIN mandazap_list_contacts lc ON l.id = lc.list_id
        WHERE l.user_id=? GROUP BY l.id ORDER BY l.created_at DESC
    ''', (user_id,)).fetchall()]
    numbers   = [dict(r) for r in conn.execute(
        'SELECT * FROM mandazap_numbers WHERE user_id=? ORDER BY created_at DESC', (user_id,)
    ).fetchall()]
    campaigns = [dict(r) for r in conn.execute('''
        SELECT c.*, l.name as list_name, n.label as number_label
        FROM mandazap_campaigns c
        LEFT JOIN mandazap_lists l ON c.list_id = l.id
        LEFT JOIN mandazap_numbers n ON c.number_id = n.id
        WHERE c.user_id=? ORDER BY c.created_at DESC
    ''', (user_id,)).fetchall()]
    templates = [dict(r) for r in conn.execute(
        'SELECT * FROM mandazap_templates WHERE user_id=? ORDER BY created_at DESC', (user_id,)
    ).fetchall()]
    conn.close()

    today      = datetime.now().strftime('%Y-%m-%d')
    today_sent = sum(c.get('sent', 0) for c in campaigns if (c.get('created_at') or '').startswith(today))
    mz_stats   = {
        'contacts':  len(contacts),
        'lists':     len(lists),
        'campaigns': len(campaigns),
        'today_sent': today_sent,
        'numbers':   len(numbers),
        'numbers_connected': sum(1 for n in numbers if n.get('status') == 'connected'),
    }

    return render_template('mandazap/painel.html',
                           contacts=contacts, lists=lists, numbers=numbers,
                           campaigns=campaigns, templates=templates,
                           mz_stats=mz_stats, plan=plan_key,
                           plan_info=MANDAZAP_PLANS.get(plan_key, MANDAZAP_PLANS['solo']),
                           plans=MANDAZAP_PLANS,
                           user_name=session.get('mz_user_name', ''),
                           now=datetime.now(),
                           trial_ends=trial_ends, trial_expired=trial_expired,
                           plan_active=plan_active,
                           section=request.args.get('section', 'dashboard'))


# ── Admin rápido por URL ───────────────────────────────────────────────────────

@app.route('/admin/mz-set-plan-email')
def mz_set_plan_email():
    token = request.args.get('token','')
    email = request.args.get('email','')
    plan  = request.args.get('plan','agencia')
    if token != os.environ.get('DEV_TOKEN','diogo4kitem'):
        return 'Acesso negado', 403
    if plan not in MANDAZAP_PLANS:
        return f'Plano inválido. Opções: {list(MANDAZAP_PLANS.keys())}', 400
    conn = get_saas_db()
    user = conn.execute('SELECT id, name, plan FROM mandazap_users WHERE email=?',(email,)).fetchone()
    if not user:
        users = [dict(r) for r in conn.execute('SELECT id, name, email, plan FROM mandazap_users').fetchall()]
        conn.close()
        return f'Usuário não encontrado. Usuários cadastrados: {users}', 404
    conn.execute('UPDATE mandazap_users SET plan=?, active=1 WHERE email=?',(plan, email))
    conn.commit()
    conn.close()
    return f'✅ Plano de {user["name"]} atualizado para {plan}!'


@app.route('/admin/mz-criar-conta')
def mz_criar_conta_admin():
    """Cria ou recria conta MandaZap via URL segura (token admin)."""
    token    = request.args.get('token', '')
    email    = request.args.get('email', '').strip().lower()
    senha    = request.args.get('senha', '')
    nome     = request.args.get('nome', 'Admin')
    plan     = request.args.get('plan', 'agencia')
    if token != os.environ.get('DEV_TOKEN', 'diogo4kitem'):
        return 'Acesso negado', 403
    if not email or not senha:
        return 'Informe email e senha', 400
    from werkzeug.security import generate_password_hash
    now   = datetime.now().isoformat()
    trial = (datetime.now() + timedelta(days=3650)).isoformat()  # 10 anos
    conn  = get_saas_db()
    existing = conn.execute('SELECT id FROM mandazap_users WHERE email=?', (email,)).fetchone()
    if existing:
        conn.execute(
            'UPDATE mandazap_users SET name=?, password_hash=?, plan=?, active=1, trial_ends=? WHERE email=?',
            (nome, generate_password_hash(senha), plan, trial, email)
        )
        conn.commit(); conn.close()
        return f'✅ Conta <b>{email}</b> atualizada! Plano: <b>{plan}</b>. <a href="/mandazap/entrar">Entrar agora</a>'
    conn.execute(
        'INSERT INTO mandazap_users (name, email, password_hash, plan, active, created_at, trial_ends) VALUES (?,?,?,?,1,?,?)',
        (nome, email, generate_password_hash(senha), plan, now, trial)
    )
    conn.commit(); conn.close()
    return f'✅ Conta <b>{email}</b> criada com sucesso! Plano: <b>{plan}</b>. <a href="/mandazap/entrar">Entrar agora</a>'


# ── Ajuda ─────────────────────────────────────────────────────────────────────

@app.route('/mandazap/ajuda')
@_mandazap_login_required
def mz_ajuda():
    return render_template('mandazap/ajuda.html')


@app.route('/agenda/ajuda')
@_agenda_login_required
def agenda_ajuda():
    return render_template('agenda/ajuda.html')


@app.route('/alerta/ajuda')
def alerta_ajuda():
    return render_template('alerta/ajuda.html')


@app.route('/bau/ajuda')
@_bau_login_required
def bau_ajuda():
    return render_template('bau/ajuda.html')


# ── Baú SC — Checkout / assinatura ───────────────────────────────────────────

@app.route('/bau/assinar/<plano>', methods=['GET', 'POST'])
@_bau_login_required
def bau_assinar(plano):
    if plano not in BAU_PLANS:
        return redirect('/bau/painel')
    user_id = session['bau_user_id']
    p = BAU_PLANS[plano]
    erro = None
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX').upper()
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            billing_type = 'PIX'
        conn = get_saas_db()
        u = conn.execute('SELECT * FROM bau_users WHERE id=?', (user_id,)).fetchone()
        conn.close()
        if not u:
            return redirect('/bau/entrar')
        customer_id = _asaas_criar_ou_buscar_cliente_saas(
            u['name'], u['email'], u['phone'], u['cpf_cnpj'], u['id'], 'bau_users'
        )
        if not customer_id:
            erro = ('Não conseguimos processar o pagamento agora. '
                    'Entre em contato pelo WhatsApp (47) 99960-6998. 💬')
        else:
            conn2 = get_saas_db()
            conn2.execute('UPDATE bau_users SET asaas_customer_id=?, plan=? WHERE id=?',
                          (customer_id, plano, user_id))
            conn2.commit(); conn2.close()
            resp = _asaas_criar_assinatura_saas(
                customer_id, 'bau', plano, p['preco'],
                f"Baú SC {p['label']} — Cofre Digital",
                billing_type, p.get('cycle', 'MONTHLY')
            )
            if resp.get('id'):
                invoice_url = resp.get('invoiceUrl') or resp.get('bankSlipUrl') or ''
                if invoice_url:
                    return redirect(invoice_url)
                return redirect('/bau/aguardando-pagamento')
            else:
                erro = 'Não foi possível gerar o pagamento. Tente novamente.'
    return render_template('bau/checkout.html', plano=p, plano_key=plano, erro=erro)


@app.route('/bau/aguardando-pagamento')
@_bau_login_required
def bau_aguardando():
    return render_template('bau/aguardando.html')


# ── Baú SC — Recuperação de senha ────────────────────────────────────────────

@app.route('/bau/esqueci-senha', methods=['GET', 'POST'])
def bau_esqueci_senha():
    import secrets as _sec
    mensagem = None
    erro = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            erro = 'Informe seu e-mail.'
        else:
            conn = get_saas_db()
            u = conn.execute('SELECT * FROM bau_users WHERE email=?', (email,)).fetchone()
            if u:
                token = _sec.token_urlsafe(32)
                expires = (datetime.now() + timedelta(hours=2)).isoformat()
                conn.execute('UPDATE bau_users SET reset_token=?, reset_expires=? WHERE id=?',
                             (token, expires, u['id']))
                conn.commit()
                link = f'https://4kitem.com.br/bau/redefinir-senha?token={token}'
                _enviar_email(email, '🔐 Baú SC — Redefinir senha',
                    f'''<div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px">
                    <h2 style="color:#a78bfa">🗝️ Redefinir sua senha</h2>
                    <p>Olá, {u["name"].split()[0]}! Clique no botão abaixo para criar uma nova senha.</p>
                    <a href="{link}" style="display:inline-block;margin:24px 0;padding:14px 28px;
                       background:linear-gradient(135deg,#7c3aed,#a78bfa);color:#fff;
                       text-decoration:none;border-radius:10px;font-weight:700">
                       Redefinir senha →</a>
                    <p style="color:#888;font-size:12px">Link válido por 2 horas. Se não solicitou, ignore este e-mail.</p>
                    </div>''')
            conn.close()
            # Mesmo se e-mail não existir, mostramos a mesma mensagem (anti-enumeração)
            mensagem = 'Se este e-mail estiver cadastrado, você receberá as instruções em breve.'
    return render_template('bau/esqueci_senha.html', mensagem=mensagem, erro=erro)


@app.route('/bau/redefinir-senha', methods=['GET', 'POST'])
def bau_redefinir_senha():
    token = request.args.get('token', '') or request.form.get('token', '')
    erro = None
    sucesso = None
    if not token:
        return redirect('/bau/entrar')
    conn = get_saas_db()
    u = conn.execute('SELECT * FROM bau_users WHERE reset_token=?', (token,)).fetchone()
    if not u:
        conn.close()
        return render_template('bau/redefinir_senha.html', token=token,
                               erro='Link inválido ou expirado. Solicite um novo.', sucesso=None)
    if u['reset_expires'] and datetime.now().isoformat() > u['reset_expires']:
        conn.close()
        return render_template('bau/redefinir_senha.html', token=token,
                               erro='Link expirado. Solicite um novo.', sucesso=None)
    if request.method == 'POST':
        senha = request.form.get('password', '')
        confirma = request.form.get('password_confirm', '')
        if len(senha) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        elif senha != confirma:
            erro = 'As senhas não coincidem.'
        else:
            conn.execute("UPDATE bau_users SET password_hash=?, reset_token='', reset_expires='' WHERE id=?",
                         (generate_password_hash(senha), u['id']))
            conn.commit()
            conn.close()
            return render_template('bau/redefinir_senha.html', token='', erro=None,
                                   sucesso='Senha redefinida com sucesso! Faça login.')
    conn.close()
    return render_template('bau/redefinir_senha.html', token=token, erro=erro, sucesso=sucesso)


# ── QR Code ───────────────────────────────────────────────────────────────────

def _evo_delete_instance(evo_url, instance, headers):
    """Deleta instância da Evolution API tentando ambos os formatos de URL (v1/v2)."""
    import requests as _req
    for url in [
        f"{evo_url}/instance/delete/{instance}",   # formato v1
        f"{evo_url}/instance/{instance}/delete",    # formato v2
    ]:
        try:
            _req.delete(url, headers=headers, timeout=8)
        except Exception:
            pass


def _evo_extract_qr(data):
    """Procura QR base64 em vários formatos de resposta da Evolution API v1/v2."""
    if not isinstance(data, dict):
        return ''
    # Nível raiz: {"base64": "..."} ou {"qrcode": "..."}
    qr = data.get('base64') or data.get('qrcode', '')
    if isinstance(qr, dict):
        qr = qr.get('base64', '') or qr.get('code', '')
    if not qr:
        # Aninhado em 'instance' ou 'qrcode': {"instance": {"base64": ...}}
        for key in ('instance', 'qrcode'):
            inner = data.get(key, {})
            if isinstance(inner, dict):
                qr = inner.get('base64', '') or inner.get('qrcode', '')
                if isinstance(qr, dict):
                    qr = qr.get('base64', '')
                if qr:
                    break
    return qr or ''


@app.route('/mandazap/numeros/<int:num_id>/qr')
def mz_qr(num_id):
    user_id = session.get('mz_user_id')
    if not user_id:
        return jsonify({'erro': 'Não autenticado'}), 401
    conn = get_saas_db()
    num  = conn.execute(
        'SELECT * FROM mandazap_numbers WHERE id=? AND user_id=?', (num_id, user_id)
    ).fetchone()
    conn.close()
    if not num:
        return jsonify({'erro': 'Número não encontrado'}), 404

    evo_url = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    if not evo_url or not evo_key:
        return jsonify({'erro': 'Evolution API não configurada. Configure EVOLUTION_API_URL e EVOLUTION_API_KEY nas variáveis de ambiente do Railway.'})

    try:
        import requests as _req
        instance = f"mz{user_id}n{num_id}"
        headers  = {'apikey': evo_key, 'Content-Type': 'application/json'}

        def _return_qr(qr):
            if not qr.startswith('data:'):
                qr = 'data:image/png;base64,' + qr
            return jsonify({'qr': qr})

        # ── Passo 1: tenta QR na instância existente (rápido, sem delete) ─────
        try:
            r_conn = _req.get(f"{evo_url}/instance/connect/{instance}",
                              headers=headers, timeout=12)
            qr = _evo_extract_qr(r_conn.json() if r_conn.content else {})
            if qr:
                return _return_qr(qr)
        except Exception:
            pass

        # ── Passo 2: instância não existe ou está travada — reset completo ────
        # Deleta via ambos os formatos de URL (v1 e v2 da Evolution API)
        _evo_delete_instance(evo_url, instance, headers)
        # Também limpa nome legado se existir
        _evo_delete_instance(evo_url, f"mz_{user_id}_{num_id}", headers)
        time.sleep(1.5)

        # Cria instância limpa
        cr      = _req.post(f"{evo_url}/instance/create", headers=headers,
                            json={'instanceName': instance, 'qrcode': True,
                                  'integration': 'WHATSAPP-BAILEYS'}, timeout=20)
        cr_data = cr.json() if cr.content else {}
        log.info(f"Evo create [{instance}] HTTP {cr.status_code}: {str(cr_data)[:300]}")
        qr = _evo_extract_qr(cr_data)
        if qr:
            return _return_qr(qr)

        # ── Passo 3: QR ainda não pronto — polling /connect (até 3 tentativas) ─
        last_r2_data = {}
        last_r2_status = 0
        for attempt in range(3):
            time.sleep(2.5)
            r2  = _req.get(f"{evo_url}/instance/connect/{instance}",
                           headers=headers, timeout=15)
            last_r2_data   = r2.json() if r2.content else {}
            last_r2_status = r2.status_code
            qr  = _evo_extract_qr(last_r2_data)
            log.info(f"Evo connect [{instance}] #{attempt+1} HTTP {r2.status_code}: {str(r2.text[:200])}")
            if qr:
                return _return_qr(qr)

        # Devolve diagnóstico completo na resposta para facilitar debug
        return jsonify({
            'erro': 'QR Code não disponível ainda. Aguarde 5 segundos e tente novamente.',
            'diag': {
                'instance':     instance,
                'create_http':  cr.status_code,
                'create_resp':  str(cr_data)[:400],
                'connect_http': last_r2_status,
                'connect_resp': str(last_r2_data)[:400],
                'evo_url':      (evo_url[:50] + '...') if len(evo_url) > 50 else evo_url,
            }
        })
    except Exception as e:
        log.error(f"mz_qr error [{num_id}]: {e}")
        return jsonify({'erro': f'Erro ao conectar com a Evolution API: {str(e)}',
                        'diag': {'exception': str(e)}})


# ── Check status (polling após QR) ────────────────────────────────────────────

@app.route('/mandazap/numeros/<int:num_id>/check-status')
def mz_check_status(num_id):
    user_id = session.get('mz_user_id')
    if not user_id:
        return jsonify({'erro': 'Não autenticado'}), 401
    conn = get_saas_db()
    num  = conn.execute(
        'SELECT * FROM mandazap_numbers WHERE id=? AND user_id=?', (num_id, user_id)
    ).fetchone()
    if not num:
        conn.close()
        return jsonify({'erro': 'Número não encontrado'}), 404

    evo_url = os.environ.get('EVOLUTION_API_URL', '')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    if not evo_url or not evo_key:
        conn.close()
        return jsonify({'status': 'disconnected', 'reason': 'evo_not_configured'})

    try:
        import requests as _req
        instance = f"mz{user_id}n{num_id}"
        headers  = {'apikey': evo_key}
        r = _req.get(f"{evo_url}/instance/connectionState/{instance}", headers=headers, timeout=8)
        data = r.json() if r.content else {}
        # Evolution v2: {"instance": {"state": "open"}} or {"state": "open"}
        state = ''
        if isinstance(data, dict):
            inner = data.get('instance', data)
            if isinstance(inner, dict):
                state = inner.get('state', inner.get('connectionStatus', ''))
            if not state:
                state = data.get('state', data.get('connectionStatus', ''))
        is_connected = str(state).lower() in ('open', 'connected', 'online')
        new_status   = 'connected' if is_connected else 'disconnected'

        # Actualiza DB só quando muda
        if num['status'] != new_status:
            phone_info = ''
            if is_connected:
                # Tenta pegar o número de telefone da instância
                try:
                    ri = _req.get(f"{evo_url}/instance/fetchInstances", headers=headers, timeout=8)
                    instances = ri.json() if ri.content else []
                    if isinstance(instances, list):
                        for inst in instances:
                            if isinstance(inst, dict):
                                iname = inst.get('instance', {}).get('instanceName', '') if isinstance(inst.get('instance'), dict) else inst.get('instanceName', '')
                                if iname == instance:
                                    phone_info = inst.get('instance', {}).get('owner', '') if isinstance(inst.get('instance'), dict) else inst.get('owner', '')
                                    break
                except Exception:
                    pass
            conn.execute(
                'UPDATE mandazap_numbers SET status=?, phone=? WHERE id=?',
                (new_status, phone_info or num['phone'], num_id)
            )
            conn.commit()

        conn.close()
        return jsonify({'status': new_status, 'state': state})
    except Exception as e:
        conn.close()
        log.error(f"check-status error: {e}")
        return jsonify({'status': 'disconnected', 'reason': str(e)})


# ── Upload de mídia ────────────────────────────────────────────────────────────

@app.route('/mandazap/upload', methods=['POST'])
def mz_upload():
    user_id = session.get('mz_user_id')
    if not user_id:
        return jsonify({'erro': 'Não autenticado'}), 401
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400

    import uuid, re as _re2
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'jpg'
    allowed = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
    if ext not in allowed:
        return jsonify({'erro': 'Tipo não permitido. Use: JPG, PNG, GIF ou WEBP'}), 400

    # Limite de 3 MB
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > 3 * 1024 * 1024:
        return jsonify({'erro': f'Arquivo muito grande ({size//1024}KB). Limite: 3MB'}), 400

    upload_dir = os.path.join(os.path.dirname(__file__), 'static', 'mz_uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"u{user_id}_{uuid.uuid4().hex[:10]}.{ext}"
    f.save(os.path.join(upload_dir, filename))

    # URL pública
    base = request.host_url.rstrip('/')
    url  = f"{base}/static/mz_uploads/{filename}"
    return jsonify({'ok': True, 'url': url})


# ── Contatos ──────────────────────────────────────────────────────────────────

@app.route('/mandazap/contatos/add', methods=['POST'])
@_mandazap_login_required
def mz_contact_add():
    user_id = session['mz_user_id']
    name    = request.form.get('name', '').strip()
    phone   = request.form.get('phone', '').strip()
    email   = request.form.get('email', '').strip()
    tag     = request.form.get('tag', '').strip()
    notes   = request.form.get('notes', '').strip()
    if name and phone:
        phone = _re.sub(r'[^\d+]', '', phone)
        conn = get_saas_db()
        conn.execute(
            'INSERT INTO mandazap_contacts (user_id, name, phone, email, tag, notes, created_at) VALUES (?,?,?,?,?,?,?)',
            (user_id, name, phone, email, tag, notes, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    return redirect('/mandazap/painel?section=contatos')


@app.route('/mandazap/contatos/<int:cid>/delete', methods=['POST'])
@_mandazap_login_required
def mz_contact_delete(cid):
    user_id = session['mz_user_id']
    conn    = get_saas_db()
    conn.execute('DELETE FROM mandazap_list_contacts WHERE contact_id=?', (cid,))
    conn.execute('DELETE FROM mandazap_contacts WHERE id=? AND user_id=?', (cid, user_id))
    conn.commit()
    conn.close()
    return redirect('/mandazap/painel?section=contatos')


@app.route('/mandazap/contatos/import-csv', methods=['POST'])
@_mandazap_login_required
def mz_contact_import_csv():
    user_id = session['mz_user_id']
    f       = request.files.get('csv_file')
    if not f:
        return redirect('/mandazap/painel?section=contatos')
    filename = f.filename.lower()
    try:
        raw = f.read()
        # ── VCF / vCard ───────────────────────────────────────────────────────
        if filename.endswith('.vcf') or filename.endswith('.vcard'):
            content = raw.decode('utf-8', errors='ignore')
            contacts = _parse_vcf(content)

        # ── Excel .xlsx / .xls (exportado do Android/Google Contacts) ─────────
        elif filename.endswith('.xlsx') or filename.endswith('.xls'):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            contacts = []
            headers = []
            for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                if row_idx == 0:
                    # Primeira linha = cabeçalhos (normaliza)
                    headers = [str(c).lower().strip() if c else '' for c in row]
                    continue
                if not any(row):
                    continue
                row_dict = {headers[i]: str(v).strip() if v is not None else '' for i, v in enumerate(row) if i < len(headers)}
                name  = (row_dict.get('nome') or row_dict.get('name') or row_dict.get('contato') or
                         row_dict.get('contact') or row_dict.get('display name') or '').strip()
                phone = (row_dict.get('telefone') or row_dict.get('phone') or row_dict.get('whatsapp') or
                         row_dict.get('celular') or row_dict.get('mobile') or row_dict.get('phone 1 - value') or
                         row_dict.get('phone 1') or '').strip()
                email = (row_dict.get('email') or row_dict.get('e-mail') or row_dict.get('email 1 - value') or '').strip()
                tag   = (row_dict.get('tag') or row_dict.get('categoria') or row_dict.get('group') or row_dict.get('grupo') or '').strip()
                # Fallback: se não achou por nome de coluna, pega primeira e segunda coluna
                if not name and len(row) > 0 and row[0]:
                    name = str(row[0]).strip()
                if not phone and len(row) > 1 and row[1]:
                    phone = str(row[1]).strip()
                if name and phone:
                    contacts.append({'name': name, 'phone': phone, 'email': email, 'tag': tag})
            wb.close()

        else:
            # ── CSV ───────────────────────────────────────────────────────────
            content = raw.decode('utf-8-sig', errors='ignore')
            reader  = csv.DictReader(io.StringIO(content))
            contacts = []
            for row in reader:
                name  = (row.get('nome') or row.get('name') or row.get('Nome') or '').strip()
                phone = (row.get('telefone') or row.get('phone') or row.get('Telefone') or row.get('whatsapp') or '').strip()
                email = (row.get('email') or row.get('Email') or '').strip()
                tag   = (row.get('tag') or row.get('Tag') or row.get('categoria') or '').strip()
                if name and phone:
                    contacts.append({'name': name, 'phone': phone, 'email': email, 'tag': tag})
        conn  = get_saas_db()
        count = 0
        for c in contacts:
            phone = _re.sub(r'[^\d+]', '', c.get('phone', ''))
            if not phone:
                continue
            # garante DDI 55 para números brasileiros sem prefixo
            if phone.startswith('0'):
                phone = '55' + phone[1:]
            elif len(phone) <= 11 and not phone.startswith('+'):
                phone = '55' + phone
            conn.execute(
                'INSERT OR IGNORE INTO mandazap_contacts (user_id, name, phone, email, tag, created_at) VALUES (?,?,?,?,?,?)',
                (user_id, c.get('name',''), phone, c.get('email',''), c.get('tag',''), datetime.now().isoformat())
            )
            count += 1
        conn.commit()
        conn.close()
        log.info(f'Importados {count} contatos para user {user_id}')
    except Exception as e:
        log.error(f'import error: {e}')
    return redirect('/mandazap/painel?section=contatos')


def _parse_vcf(content: str) -> list:
    """Parse simples de arquivo VCF/vCard — extrai nome e telefone."""
    contacts = []
    for card in content.split('BEGIN:VCARD'):
        if 'END:VCARD' not in card:
            continue
        card = card[:card.index('END:VCARD')]
        name  = ''
        phone = ''
        email = ''
        for line in card.splitlines():
            line = line.strip()
            # Nome: FN tem preferência sobre N
            if line.startswith('FN:'):
                name = line[3:].strip()
            elif line.startswith('N:') and not name:
                parts = line[2:].split(';')
                name = ' '.join(p.strip() for p in reversed(parts) if p.strip())
            # Telefone: qualquer linha TEL
            elif line.upper().startswith('TEL') and ':' in line and not phone:
                phone = line.split(':', 1)[1].strip()
            # Email
            elif line.upper().startswith('EMAIL') and ':' in line and not email:
                email = line.split(':', 1)[1].strip()
        if name and phone:
            contacts.append({'name': name, 'phone': phone, 'email': email, 'tag': ''})
    return contacts


# ── Listas ────────────────────────────────────────────────────────────────────

@app.route('/mandazap/listas/add', methods=['POST'])
@_mandazap_login_required
def mz_list_add():
    user_id     = session['mz_user_id']
    name        = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    contact_ids = request.form.getlist('contact_ids')
    if name:
        conn = get_saas_db()
        cur  = conn.execute(
            'INSERT INTO mandazap_lists (user_id, name, description, created_at) VALUES (?,?,?,?)',
            (user_id, name, description, datetime.now().isoformat())
        )
        list_id = cur.lastrowid
        for cid in contact_ids:
            try:
                conn.execute('INSERT OR IGNORE INTO mandazap_list_contacts (list_id, contact_id) VALUES (?,?)', (list_id, int(cid)))
            except Exception:
                pass
        conn.commit()
        conn.close()
    return redirect('/mandazap/painel?section=listas')


@app.route('/mandazap/listas/<int:lid>/delete', methods=['POST'])
@_mandazap_login_required
def mz_list_delete(lid):
    user_id = session['mz_user_id']
    conn    = get_saas_db()
    conn.execute('DELETE FROM mandazap_list_contacts WHERE list_id=?', (lid,))
    conn.execute('DELETE FROM mandazap_lists WHERE id=? AND user_id=?', (lid, user_id))
    conn.commit()
    conn.close()
    return redirect('/mandazap/painel?section=listas')


# ── Números ───────────────────────────────────────────────────────────────────

@app.route('/mandazap/numeros/add', methods=['POST'])
@_mandazap_login_required
def mz_number_add():
    user_id  = session['mz_user_id']
    plan_key = session.get('mz_plan', 'solo')
    label    = request.form.get('label', '').strip()
    phone    = request.form.get('phone', '').strip()
    if not label:
        return redirect('/mandazap/painel?section=numeros')
    conn     = get_saas_db()
    count    = conn.execute('SELECT COUNT(*) FROM mandazap_numbers WHERE user_id=?', (user_id,)).fetchone()[0]
    max_nums = MANDAZAP_PLANS.get(plan_key, MANDAZAP_PLANS['solo'])['numbers']
    if count < max_nums:
        conn.execute(
            'INSERT INTO mandazap_numbers (user_id, label, phone, status, created_at) VALUES (?,?,?,?,?)',
            (user_id, label, _re.sub(r'[^\d+]', '', phone), 'disconnected', datetime.now().isoformat())
        )
        conn.commit()
    conn.close()
    return redirect('/mandazap/painel?section=numeros')


@app.route('/mandazap/numeros/<int:nid>/delete', methods=['POST'])
@_mandazap_login_required
def mz_number_delete(nid):
    user_id = session['mz_user_id']
    conn    = get_saas_db()
    conn.execute('DELETE FROM mandazap_numbers WHERE id=? AND user_id=?', (nid, user_id))
    conn.commit()
    conn.close()
    # Limpa instância da Evolution API (não bloqueia se falhar)
    evo_url = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    if evo_url and evo_key:
        headers = {'apikey': evo_key, 'Content-Type': 'application/json'}
        _evo_delete_instance(evo_url, f"mz{user_id}n{nid}", headers)
    return redirect('/mandazap/painel?section=numeros')


@app.route('/mandazap/numeros/<int:num_id>/testar', methods=['POST'])
@_mandazap_login_required
def mz_testar_envio(num_id):
    """Envia uma mensagem de teste para o próprio número e retorna o resultado bruto da API."""
    import requests as _req
    user_id  = session['mz_user_id']
    conn     = get_saas_db()
    num      = conn.execute('SELECT * FROM mandazap_numbers WHERE id=? AND user_id=?', (num_id, user_id)).fetchone()
    conn.close()
    if not num:
        return jsonify({'ok': False, 'erro': 'Número não encontrado'}), 404

    evo_url = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    if not evo_url or not evo_key:
        return jsonify({'ok': False, 'erro': 'EVOLUTION_API_URL ou EVOLUTION_API_KEY não configurados no Railway'})

    instance = f"mz{user_id}n{num_id}"
    phone    = (num['phone'] or '').replace(' ','').replace('-','').replace('+','').replace('(','').replace(')','')
    if not phone.startswith('55'):
        phone = '55' + phone

    payload = {'number': phone, 'text': '✅ Teste MandaZap — envio funcionando! (mensagem automática de diagnóstico)'}
    try:
        r = _req.post(
            f"{evo_url}/message/sendText/{instance}",
            headers={'apikey': evo_key, 'Content-Type': 'application/json'},
            json=payload, timeout=15
        )
        return jsonify({
            'ok':       r.status_code in (200, 201),
            'status':   r.status_code,
            'instance': instance,
            'phone':    phone,
            'resposta': r.text[:500],
        })
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e), 'instance': instance, 'phone': phone})


# ── Otimizador de mensagem (Anti-Spam) ────────────────────────────────────────

@app.route('/mandazap/otimizar-mensagem', methods=['POST'])
@_mandazap_login_required
def mz_otimizar_mensagem():
    """Transforma texto simples em mensagem com variações spintax anti-spam."""
    data   = request.get_json() or {}
    texto  = (data.get('texto') or '').strip()
    if not texto:
        return jsonify({'erro': 'Texto vazio'}), 400

    groq_key = os.environ.get('GROQ_API_KEY', '')
    if not groq_key:
        return jsonify({'erro': 'Serviço indisponível no momento'}), 500

    prompt = (
        "Você é um especialista em marketing via WhatsApp. "
        "Transforme a mensagem abaixo adicionando variações no formato {opção1|opção2|opção3} "
        "em TODOS os lugares possíveis para tornar cada envio único e evitar bloqueio.\n\n"
        "Regras obrigatórias:\n"
        "1. Adicione {variações} em: saudações, adjetivos, verbos de ação, conectivos, CTAs, emojis.\n"
        "2. Mantenha {nome} onde já existir ou adicione no início.\n"
        "3. URLs devem permanecer EXATAMENTE iguais, nunca as altere.\n"
        "4. Dados concretos (endereço, telefone, preços, datas) jamais viram variações.\n"
        "5. Adicione pelo menos 8 variações espalhadas pelo texto.\n"
        "6. Use sinônimos brasileiros naturais e informais.\n"
        "7. Retorne APENAS a mensagem transformada, sem explicações, sem prefixos.\n\n"
        f"Mensagem original:\n{texto}"
    )

    try:
        resp = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
            json={
                'model': 'meta-llama/llama-4-scout-17b-16e-instruct',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 1200,
                'temperature': 0.7,
            },
            timeout=25,
        )
        resp.raise_for_status()
        resultado = resp.json()['choices'][0]['message']['content'].strip()
        return jsonify({'ok': True, 'resultado': resultado})
    except Exception as ex:
        return jsonify({'erro': f'Erro ao processar: {ex}'}), 500


# ── Campanhas ─────────────────────────────────────────────────────────────────

@app.route('/mandazap/campanhas/add', methods=['POST'])
@_mandazap_login_required
def mz_campaign_add():
    user_id      = session['mz_user_id']
    name         = request.form.get('name', '').strip()
    message      = request.form.get('message', '').strip()
    media_type   = request.form.get('media_type', 'text')
    media_url    = request.form.get('media_url', '').strip()
    list_id      = request.form.get('list_id') or None
    number_id    = request.form.get('number_id') or None
    if not name or not message:
        return redirect('/mandazap/painel?section=campanhas')

    conn  = get_saas_db()
    total = 0
    if list_id:
        total = conn.execute(
            'SELECT COUNT(*) FROM mandazap_list_contacts WHERE list_id=?', (list_id,)
        ).fetchone()[0]
    elif not list_id:
        # sem lista = todos os contatos do usuário
        total = conn.execute(
            'SELECT COUNT(*) FROM mandazap_contacts WHERE user_id=?', (user_id,)
        ).fetchone()[0]

    conn.execute('''
        INSERT INTO mandazap_campaigns
        (user_id, name, message, media_type, media_url, list_id, number_id, status, total, sent, scheduled_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,0,NULL,?)
    ''', (user_id, name, message, media_type, media_url, list_id, number_id,
          'rascunho', total, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return redirect('/mandazap/painel?section=campanhas')


@app.route('/mandazap/campanhas/<int:cid>/duplicar', methods=['POST'])
@_mandazap_login_required
def mz_campaign_duplicar(cid):
    user_id = session['mz_user_id']
    conn    = get_saas_db()
    c = conn.execute(
        'SELECT * FROM mandazap_campaigns WHERE id=? AND user_id=?', (cid, user_id)
    ).fetchone()
    if c:
        conn.execute('''
            INSERT INTO mandazap_campaigns
            (user_id, name, message, media_type, media_url, list_id, number_id, status, total, sent, created_at)
            VALUES (?,?,?,?,?,?,?,'rascunho',?,0,?)
        ''', (user_id, f"Cópia — {c['name']}", c['message'],
              c['media_type'], c['media_url'] or '', c['list_id'], c['number_id'],
              c['total'], datetime.now().isoformat()))
        conn.commit()
    conn.close()
    return redirect('/mandazap/painel?section=campanhas')


@app.route('/mandazap/campanhas/<int:cid>/delete', methods=['POST'])
@_mandazap_login_required
def mz_campaign_delete(cid):
    user_id = session['mz_user_id']
    conn    = get_saas_db()
    conn.execute('DELETE FROM mandazap_campaigns WHERE id=? AND user_id=?', (cid, user_id))
    conn.commit()
    conn.close()
    return redirect('/mandazap/painel?section=campanhas')


# ── Templates ─────────────────────────────────────────────────────────────────

@app.route('/mandazap/templates/add', methods=['POST'])
@_mandazap_login_required
def mz_template_add():
    user_id    = session['mz_user_id']
    name       = request.form.get('name', '').strip()
    message    = request.form.get('message', '').strip()
    media_type = request.form.get('media_type', 'text')
    media_url  = request.form.get('media_url', '').strip()
    if name and message:
        conn = get_saas_db()
        conn.execute(
            'INSERT INTO mandazap_templates (user_id, name, message, media_type, media_url, created_at) VALUES (?,?,?,?,?,?)',
            (user_id, name, message, media_type, media_url, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    return redirect('/mandazap/painel?section=templates')


@app.route('/mandazap/templates/<int:tid>/delete', methods=['POST'])
@_mandazap_login_required
def mz_template_delete(tid):
    user_id = session['mz_user_id']
    conn    = get_saas_db()
    conn.execute('DELETE FROM mandazap_templates WHERE id=? AND user_id=?', (tid, user_id))
    conn.commit()
    conn.close()
    return redirect('/mandazap/painel?section=templates')


# ══════════════════════════════════════════════════════════════════════════
#  HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════

@app.route('/health')
def health():
    s = stats()
    return {'status': 'ok', 'app': '4KITEM', **s}, 200


# ══════════════════════════════════════════════════════════════════════════
#  DEV — Página privada de roadmap e anotações
# ══════════════════════════════════════════════════════════════════════════

DEV_TOKEN = os.environ.get('DEV_TOKEN', 'diogo4kitem')

@app.route('/dev/<token>')
def dev_page(token):
    if token != DEV_TOKEN:
        abort(404)
    notas = listar_notas_dev()
    return render_template('dev.html', notas=notas, now=datetime.now(), token=token)

@app.route('/dev/<token>/nota', methods=['POST'])
def dev_nota(token):
    if token != DEV_TOKEN:
        abort(404)
    titulo = request.form.get('titulo', '').strip() or 'Sem título'
    texto  = request.form.get('texto', '').strip()
    if texto:
        salvar_nota_dev(titulo, texto)
    return redirect(url_for('dev_page', token=token))


# ══════════════════════════════════════════════════════════════════════════
#  CAMPAIGN DISPATCHER — envia mensagens via Evolution API
# ══════════════════════════════════════════════════════════════════════════

def _get_evo():
    """Retorna (evo_url, evo_key) ou ('', '') se não configurado."""
    return (
        os.environ.get('EVOLUTION_API_URL', '').rstrip('/'),
        os.environ.get('EVOLUTION_API_KEY', ''),
    )


def _typing_delay_ms(text: str) -> int:
    """Calcula delay de typing proporcional ao tamanho da mensagem.
    ~40ms/char, mínimo 800ms, máximo 3500ms — imita velocidade humana real.
    """
    base = min(max(len(text) * 40, 800), 3500)
    return int(base * random.uniform(0.85, 1.15))


def _is_invalid_number(body: str) -> bool:
    """Detecta se a resposta da API indica número inexistente no WhatsApp."""
    b = body.lower()
    return ('"exists":false' in body or '"exists": false' in body
            or 'exists\\":false' in body or 'exists\\": false' in body
            or '"exists":0' in body
            or 'not exists' in b or 'invalid number' in b
            or 'phone not found' in b or 'number not found' in b
            or 'not in whatsapp' in b or 'does not exist' in b)


def _is_disconnected(body: str) -> bool:
    """Detecta se o WhatsApp foi desconectado/banido na instância.
    'Connection Closed' = instância desconectou — ban ou sessão expirada.
    """
    b = body.lower()
    return ('connection closed' in b
            or 'error: connection closed' in b
            or 'disconnected' in b
            or 'not connected' in b
            or 'instance not connected' in b
            or 'session not found' in b
            or 'qrcode' in b)


def _check_instance_connected(evo_url: str, evo_key: str, instance: str) -> bool:
    """Verifica se a instância WhatsApp está com sessão ativa (state=open)."""
    try:
        r = requests.get(
            f"{evo_url}/instance/connectionState/{instance}",
            headers={'apikey': evo_key},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            state = (data.get('instance', {}).get('state') or
                     data.get('state') or '').lower()
            return state == 'open'
        return False
    except Exception:
        return True  # Em caso de dúvida, tenta enviar e vê o que acontece


def _validate_numbers_batch(evo_url: str, evo_key: str, instance: str,
                            phones: list, batch_size: int = 50) -> set:
    """Verifica em lote quais números têm WhatsApp ativo via Evolution API.
    Retorna um set() com os phones VÁLIDOS (que existem no WhatsApp).
    Phones que a API não conseguiu verificar ficam no set (safe default = tenta enviar).
    """
    valid = set()
    for i in range(0, len(phones), batch_size):
        chunk = phones[i:i + batch_size]
        try:
            r = requests.post(
                f"{evo_url}/chat/whatsappNumbers/{instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'numbers': chunk},
                timeout=30,
            )
            if r.status_code in (200, 201):
                data = r.json()
                # Resposta: lista de {exists:bool, number/jid/...}
                if isinstance(data, list):
                    for item in data:
                        if item.get('exists') or item.get('numberExists'):
                            jid = item.get('jid') or item.get('number') or ''
                            # Extrai só os dígitos do jid  "5547...@s.whatsapp.net"
                            num = jid.split('@')[0] if '@' in jid else jid
                            if num:
                                valid.add(num)
                else:
                    # Se a API falhou ou retornou formato inesperado, inclui todos (fail-open)
                    valid.update(chunk)
            else:
                # Endpoint não disponível ou erro — inclui todos (fail-open)
                log.warning(f"whatsappNumbers batch error HTTP {r.status_code} — fail-open para {len(chunk)} phones")
                valid.update(chunk)
        except Exception as e:
            log.warning(f"whatsappNumbers batch exception: {e} — fail-open para {len(chunk)} phones")
            valid.update(chunk)
        # Pequena pausa entre batches para não sobrecarregar
        if i + batch_size < len(phones):
            time.sleep(random.uniform(1.5, 3.0))
    return valid


def _apply_spintax(text: str) -> str:
    """Processa variações {opção1|opção2|opção3} no template.
    Só processa grupos com pelo menos um | (preserva {nome}, {name}, etc).
    Cada mensagem sai diferente — quebra fingerprint de conteúdo repetido.
    """
    def pick(m):
        return random.choice(m.group(1).split('|'))
    return _re.sub(r'\{([^{}]*\|[^{}]*)\}', pick, text)


def _send_text(evo_url, evo_key, instance, phone, text):
    """Envia mensagem de texto com typing simulation.
    Usa presence=composing + delay proporcional ao tamanho — imita humano.
    Retorna (ok, erro_str, invalido).
    """
    try:
        delay_ms = _typing_delay_ms(text)
        r = requests.post(
            f"{evo_url}/message/sendText/{instance}",
            headers={'apikey': evo_key, 'Content-Type': 'application/json'},
            json={
                'number': phone,
                'text': text,
                'options': {'delay': delay_ms, 'presence': 'composing'},
            },
            timeout=20,
        )
        if r.status_code in (200, 201):
            return True, '', False
        body = r.text[:300]
        err_str = f"HTTP {r.status_code}: {body[:150]}"
        return False, err_str, _is_invalid_number(body)
    except requests.exceptions.Timeout:
        return False, 'Timeout: API demorou mais de 20s', False
    except Exception as e:
        return False, str(e)[:150], False


def _send_image(evo_url, evo_key, instance, phone, image_url, caption=''):
    """Envia imagem com legenda. Retorna (ok, erro_str, invalido)."""
    try:
        r = requests.post(
            f"{evo_url}/message/sendMedia/{instance}",
            headers={'apikey': evo_key, 'Content-Type': 'application/json'},
            json={
                'number': phone,
                'mediatype': 'image',
                'media': image_url,
                'caption': caption,
            },
            timeout=25,
        )
        if r.status_code in (200, 201):
            return True, '', False
        body = r.text[:300]
        err_str = f"HTTP {r.status_code}: {body[:150]}"
        return False, err_str, _is_invalid_number(body)
    except requests.exceptions.Timeout:
        return False, 'Timeout: API demorou mais de 25s', False
    except Exception as e:
        return False, str(e)[:150], False


def _antiban_delay(sent_count: int):
    """
    Delay humanizado anti-ban v3 — estratégia em 3 fases:

    FASE 1 — Warm-up (primeiras 20 msgs):
      Base 40–90s para não disparar alerta de novo número.

    FASE 2 — Ritmo normal (20–150):
      Base 20–55s com jitter ±25%.

    FASE 3 — Volume (150+):
      Base 15–40s (número já aquecido).

    Pausas obrigatórias (BARREIRA REAL = 45 consecutivas):
      A cada 20 msgs: 3–6 min  (reseta contador interno do Meta)
      A cada 40 msgs: 8–15 min (pausa crítica — antes do limite de 45)
      A cada 100 msgs: 15–25 min (simula saída do celular)
      A cada 200 msgs: 25–40 min (pausa refeição/reunião)

    Delays NUNCA são fixos — o Meta detecta padrões matemáticos.
    """
    # Pausas longas — checar do mais raro ao mais frequente
    if sent_count > 0 and sent_count % 200 == 0:
        pausa = random.uniform(1500, 2400)
        log.info(f"Anti-ban: pausa extra longa {pausa:.0f}s apos {sent_count} enviados")
        time.sleep(pausa)
        return

    if sent_count > 0 and sent_count % 100 == 0:
        pausa = random.uniform(900, 1500)
        log.info(f"Anti-ban: pausa longa {pausa:.0f}s apos {sent_count} enviados")
        time.sleep(pausa)
        return

    # CRÍTICO: pausa forte a cada 30 msgs — nunca chega na zona de risco 40-53
    if sent_count > 0 and sent_count % 30 == 0:
        pausa = random.uniform(900, 1800)  # 15–30 min — reset total do contador Meta
        log.info(f"Anti-ban: pausa critica {pausa:.0f}s apos {sent_count} enviados (antes zona 40-53)")
        time.sleep(pausa)
        return

    if sent_count > 0 and sent_count % 15 == 0:
        pausa = random.uniform(240, 480)   # 4–8 min entre blocos
        log.info(f"Anti-ban: pausa media {pausa:.0f}s apos {sent_count} enviados")
        time.sleep(pausa)
        return

    # Fase de envio baseada no aquecimento
    if sent_count < 20:
        base = random.uniform(40, 90)   # warm-up: mais devagar
    elif sent_count < 150:
        base = random.uniform(20, 55)   # ritmo normal
    else:
        base = random.uniform(15, 40)   # aquecido

    # Jitter assimétrico — evita padrão de intervalo regular
    jitter = base * random.uniform(0.75, 1.35)
    log.debug(f"Anti-ban delay: {jitter:.1f}s (sent={sent_count})")
    time.sleep(jitter)


def _dispatch_campaign(cid: int, user_id: int, delay_s: int = 3, continuar: bool = True):
    """
    Executa o disparo de uma campanha em background thread.
    Atualiza status/sent em tempo real no banco.
    continuar=True  → pula contatos já enviados (retomada)
    continuar=False → limpa log e começa do zero
    """
    try:
        _dispatch_campaign_inner(cid, user_id, delay_s, continuar=continuar)
    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"Campanha {cid} CRASH: {e}\n{tb}")
        try:
            c = get_saas_db()
            c.execute(
                "UPDATE mandazap_campaigns SET status='erro', error_log=?, finished_at=? WHERE id=?",
                (f'Erro interno: {str(e)[:200]}', datetime.now().isoformat(), cid)
            )
            c.commit(); c.close()
        except Exception:
            pass


def _dispatch_campaign_inner(cid: int, user_id: int, delay_s: int = 3, continuar: bool = True):
    evo_url, evo_key = _get_evo()
    if not evo_url or not evo_key:
        log.error(f"Campanha {cid}: Evolution API não configurada")
        get_saas_db().execute(
            "UPDATE mandazap_campaigns SET status='erro',error_log=? WHERE id=?",
            ('Evolution API não configurada (EVOLUTION_API_URL / EVOLUTION_API_KEY)', cid)
        ).connection.commit()
        return

    conn = get_saas_db()

    # Carrega campanha — checa race condition
    camp = conn.execute(
        'SELECT * FROM mandazap_campaigns WHERE id=? AND user_id=?', (cid, user_id)
    ).fetchone()
    if not camp:
        conn.close(); return
    camp = dict(camp)

    if camp['status'] == 'enviando':
        conn.close()
        log.warning(f"Campanha {cid}: já está sendo enviada (race condition evitada)")
        return

    # Verifica daily_limit do plano
    plan_key   = conn.execute('SELECT plan FROM mandazap_users WHERE id=?', (user_id,)).fetchone()
    plan_key   = (plan_key['plan'] if plan_key else 'solo')
    plan_info  = MANDAZAP_PLANS.get(plan_key, MANDAZAP_PLANS['solo'])
    daily_lim  = plan_info.get('daily_limit', 399)
    today      = datetime.now().strftime('%Y-%m-%d')
    today_sent = conn.execute(
        "SELECT COALESCE(SUM(sent),0) FROM mandazap_campaigns WHERE user_id=? AND finished_at LIKE ?",
        (user_id, f"{today}%")
    ).fetchone()[0]

    # Instância WhatsApp
    num_id   = camp.get('number_id')
    instance = f"mz{user_id}n{num_id}" if num_id else None
    if not instance:
        conn.execute(
            "UPDATE mandazap_campaigns SET status='erro',error_log=? WHERE id=?",
            ('Nenhum número WhatsApp selecionado na campanha.', cid)
        )
        conn.commit(); conn.close()
        log.error(f"Campanha {cid}: nenhum número selecionado")
        return

    # Carrega contatos da lista
    list_id = camp.get('list_id')
    if list_id:
        rows = conn.execute('''
            SELECT c.name, c.phone FROM mandazap_list_contacts lc
            JOIN mandazap_contacts c ON c.id = lc.contact_id
            WHERE lc.list_id = ? AND c.user_id = ?
        ''', (list_id, user_id)).fetchall()
    else:
        rows = conn.execute(
            'SELECT name, phone FROM mandazap_contacts WHERE user_id=?', (user_id,)
        ).fetchall()

    contacts = [dict(r) for r in rows]
    total    = len(contacts)

    if total == 0:
        conn.execute(
            "UPDATE mandazap_campaigns SET status='erro',error_log=?,total=0 WHERE id=?",
            ('Lista sem contatos.', cid)
        )
        conn.commit(); conn.close()
        log.warning(f"Campanha {cid}: sem contatos")
        return

    # ── Lógica de "continuar de onde parou" ─────────────────────────────────
    if continuar:
        # Carrega phones já enviados nesta campanha
        already_sent = set(
            r['phone'] for r in conn.execute(
                'SELECT phone FROM mandazap_sent_log WHERE campaign_id=?', (cid,)
            ).fetchall()
        )
        if already_sent:
            # Normaliza e filtra os que já receberam
            def _norm_phone(p):
                p = (p or '').replace(' ','').replace('-','').replace('+','').replace('(','').replace(')','')
                return ('55' + p) if not p.startswith('55') else p
            contacts = [c for c in contacts if _norm_phone(c.get('phone','')) not in already_sent]
            log.info(f"Campanha {cid}: retomando — {len(already_sent)} já enviados, {len(contacts)} restantes")
    else:
        # Reiniciar do zero — limpa log de envios anteriores
        conn.execute('DELETE FROM mandazap_sent_log WHERE campaign_id=?', (cid,))
        conn.commit()
        already_sent = set()
        log.info(f"Campanha {cid}: reiniciando do zero — log limpo")

    # Total real = já enviados + restantes
    prev_sent = len(already_sent) if continuar else 0
    total_real = prev_sent + len(contacts)

    if len(contacts) == 0:
        conn.execute(
            "UPDATE mandazap_campaigns SET status='concluida',sent=?,total=?,finished_at=?,error_log=? WHERE id=?",
            (total_real, total_real, datetime.now().isoformat(), 'Todos os contatos já receberam esta campanha.', cid)
        )
        conn.commit(); conn.close()
        log.info(f"Campanha {cid}: todos os {total_real} contatos já receberam. Concluída.")
        return

    # Verifica limite diário
    can_send = min(len(contacts), max(0, daily_lim - today_sent))
    if can_send == 0:
        conn.execute(
            "UPDATE mandazap_campaigns SET status='erro',error_log=? WHERE id=?",
            (f'Limite diário do plano {plan_key} atingido ({daily_lim} msgs/dia).', cid)
        )
        conn.commit(); conn.close()
        log.warning(f"Campanha {cid}: limite diário atingido ({today_sent}/{daily_lim})")
        return

    if can_send < len(contacts):
        log.info(f"Campanha {cid}: limite diário parcial — enviando {can_send}/{len(contacts)} restantes")
        contacts = contacts[:can_send]

    # Randomiza ordem dos contatos — evita padrão previsível e fingerprint de sequência
    random.shuffle(contacts)

    # Marca como "enviando" — preserva sent anterior se estiver continuando
    conn.execute(
        "UPDATE mandazap_campaigns SET status='enviando', total=?, sent=?, finished_at=NULL, error_log='' WHERE id=?",
        (total_real, prev_sent, cid)
    )
    conn.commit()

    message    = camp.get('message', '')
    media_url  = (camp.get('media_url') or '').strip()
    media_type = camp.get('media_type', 'text')
    is_image   = media_type == 'image' and bool(media_url)

    sent_count   = prev_sent  # começa do número já enviado anteriormente
    failed_count = 0
    consec_fails = 0
    first_err    = ''
    MAX_CONSEC   = 3   # aborta após 3 falhas REAIS consecutivas (ban detecta-se rápido)

    # Verifica conexão da instância ANTES de iniciar o disparo
    if not _check_instance_connected(evo_url, evo_key, instance):
        log.error(f"Campanha {cid}: instância {instance} não está conectada — abortando")
        conn.execute(
            "UPDATE mandazap_campaigns SET status='erro', error_log=? WHERE id=?",
            ('Número WhatsApp desconectado. Reconecte o número no painel Números antes de disparar.', cid)
        )
        conn.commit(); conn.close()
        return

    # ── Pré-validação de números ─────────────────────────────────────────────
    # Verifica em lote quais phones existem no WhatsApp ANTES de enviar.
    # Elimina os inválidos da fila — evita HTTP 400 "exists:false" confundidos
    # com ban e reduz API calls desnecessários.
    def _norm(p):
        p = (p or '').replace(' ','').replace('-','').replace('+','').replace('(','').replace(')','')
        return ('55' + p) if p and not p.startswith('55') else p

    raw_phones = [_norm(c.get('phone','')) for c in contacts if c.get('phone')]
    raw_phones = [p for p in raw_phones if p]

    log.info(f"Campanha {cid}: pré-validando {len(raw_phones)} números no WhatsApp...")
    conn.execute("UPDATE mandazap_campaigns SET error_log=? WHERE id=?",
                 (f'Validando {len(raw_phones)} números... aguarde.', cid))
    conn.commit()

    valid_phones = _validate_numbers_batch(evo_url, evo_key, instance, raw_phones)
    invalid_count = len(raw_phones) - len(valid_phones)
    log.info(f"Campanha {cid}: {len(valid_phones)} válidos, {invalid_count} sem WhatsApp — removidos da fila")

    # Filtra contacts mantendo só os válidos
    contacts = [c for c in contacts if _norm(c.get('phone','')) in valid_phones]
    total_real = prev_sent + len(contacts)
    conn.execute("UPDATE mandazap_campaigns SET total=?, error_log=? WHERE id=?",
                 (total_real,
                  f'{invalid_count} números sem WhatsApp removidos da fila.' if invalid_count else '',
                  cid))
    conn.commit()

    if not contacts:
        conn.execute(
            "UPDATE mandazap_campaigns SET status='concluida', sent=?, finished_at=?, error_log=? WHERE id=?",
            (prev_sent, datetime.now().isoformat(),
             f'Nenhum contato válido no WhatsApp. {invalid_count} números sem WhatsApp na lista.', cid)
        )
        conn.commit(); conn.close()
        log.warning(f"Campanha {cid}: zero contatos válidos após pré-validação")
        return

    for c in contacts:
        # Verifica se campanha foi cancelada externamente
        chk = get_saas_db()
        st  = chk.execute('SELECT status FROM mandazap_campaigns WHERE id=?', (cid,)).fetchone()
        chk.close()
        if st and st['status'] == 'cancelada':
            log.info(f"Campanha {cid} cancelada pelo usuário em {sent_count}/{total}")
            conn.execute(
                "UPDATE mandazap_campaigns SET status='cancelada', sent=?, finished_at=?, error_log=? WHERE id=?",
                (sent_count, datetime.now().isoformat(), f'Cancelada pelo usuário. {sent_count} enviados.', cid)
            )
            conn.commit(); conn.close()
            return

        phone = (c.get('phone') or '').replace(' ','').replace('-','').replace('+','').replace('(','').replace(')','')
        if not phone:
            continue
        if not phone.startswith('55'):
            phone = '55' + phone

        nome_curto    = (c.get('name') or 'Cliente').split()[0].title()
        nome_completo = (c.get('name') or 'Cliente').title()
        # 1. Substitui variáveis de contato
        msg = (message
               .replace('{nome}', nome_curto)
               .replace('{name}', nome_curto)
               .replace('{nome_completo}', nome_completo))
        # 2. Aplica spintax {opção1|opção2} — cada mensagem sai diferente
        msg = _apply_spintax(msg)

        if is_image:
            ok, err, invalido = _send_image(evo_url, evo_key, instance, phone, media_url, msg)
        else:
            ok, err, invalido = _send_text(evo_url, evo_key, instance, phone, msg)

        if ok:
            sent_count   += 1
            consec_fails  = 0
            # Registra no log de enviados para poder continuar de onde parou
            try:
                _log = get_saas_db()
                _log.execute(
                    'INSERT OR IGNORE INTO mandazap_sent_log (campaign_id, phone, sent_at) VALUES (?,?,?)',
                    (cid, phone, datetime.now().isoformat())
                )
                _log.commit(); _log.close()
            except Exception as _le:
                log.warning(f"sent_log insert error: {_le}")
        else:
            failed_count += 1
            if not first_err:
                first_err = f"Primeiro erro → {phone}: {err}"
            log.warning(f"Campanha {cid} → {phone}: {err}")

            if invalido:
                # Número não existe no WhatsApp — pula sem contar como falha consecutiva
                log.info(f"Campanha {cid} -> {phone}: numero invalido/sem WhatsApp — pulando")
                time.sleep(random.uniform(4, 10))

            elif _is_disconnected(err):
                # Instância desconectou (ban ou sessão expirada) — aborta imediatamente
                log.error(f"Campanha {cid}: instancia desconectou durante envio — {err}")
                conn.execute(
                    "UPDATE mandazap_campaigns SET status='erro', sent=?, finished_at=?, error_log=? WHERE id=?",
                    (sent_count, datetime.now().isoformat(),
                     f'Numero WhatsApp desconectado durante o disparo (possivel ban). '
                     f'{sent_count} enviados antes da desconexao. Reconecte o numero.', cid)
                )
                conn.commit(); conn.close()
                return

            else:
                # Falha real (API down, timeout, erro temporario) — conta consecutiva
                consec_fails += 1
                if consec_fails >= MAX_CONSEC:
                    # Verifica se é ban ou problema de API
                    is_ban = not _check_instance_connected(evo_url, evo_key, instance)
                    motivo = ('Numero possivelmente banido — instancia desconectada. Reconecte o numero.'
                              if is_ban else
                              f'API retornou {MAX_CONSEC} erros consecutivos. Verifique a conexao e tente novamente.')
                    log.error(f"Campanha {cid}: {MAX_CONSEC} falhas consecutivas ({'ban?' if is_ban else 'API error'}) — {err}")
                    conn.execute(
                        "UPDATE mandazap_campaigns SET status='erro', sent=?, finished_at=?, error_log=? WHERE id=?",
                        (sent_count, datetime.now().isoformat(),
                         f'{motivo} | {first_err}', cid)
                    )
                    conn.commit(); conn.close()
                    return
                # Delay progressivo: quanto mais falhas, maior a espera
                pausa_erro = random.uniform(20, 60) * consec_fails
                log.warning(f"Anti-ban: pausa {pausa_erro:.0f}s apos falha {consec_fails}/{MAX_CONSEC}")
                time.sleep(pausa_erro)

        # Atualiza progresso a cada envio
        conn2 = get_saas_db()
        conn2.execute("UPDATE mandazap_campaigns SET sent=?, updated_at=? WHERE id=?",
                      (sent_count, datetime.now().isoformat(), cid))
        conn2.commit(); conn2.close()

        # A cada 25 enviados verifica se a instância ainda está conectada
        if ok and sent_count > 0 and sent_count % 25 == 0:
            if not _check_instance_connected(evo_url, evo_key, instance):
                log.error(f"Campanha {cid}: instancia desconectou apos {sent_count} enviados — possivel ban")
                conn.execute(
                    "UPDATE mandazap_campaigns SET status='erro', sent=?, finished_at=?, error_log=? WHERE id=?",
                    (sent_count, datetime.now().isoformat(),
                     f'Numero desconectou/banido apos {sent_count} enviados. Reconecte o numero e aguarde 24h antes de tentar novamente.', cid)
                )
                conn.commit(); conn.close()
                return

        # Delay anti-ban humanizado após envio bem-sucedido
        if ok:
            _antiban_delay(sent_count)

    # Finaliza
    error_log = f"{failed_count} falhas. {first_err}" if failed_count else ''
    conn.execute(
        "UPDATE mandazap_campaigns SET status='concluida', sent=?, finished_at=?, error_log=? WHERE id=?",
        (sent_count, datetime.now().isoformat(), error_log, cid)
    )
    conn.commit(); conn.close()
    log.info(f"Campanha {cid} concluída: {sent_count}/{total} enviados")


@app.route('/mandazap/campanhas/<int:cid>/cancelar', methods=['POST'])
@_mandazap_login_required
def mz_campaign_cancel(cid):
    """Cancela uma campanha em andamento ou rascunho."""
    user_id = session['mz_user_id']
    conn    = get_saas_db()
    camp    = conn.execute('SELECT status FROM mandazap_campaigns WHERE id=? AND user_id=?', (cid, user_id)).fetchone()
    if not camp:
        conn.close(); return jsonify({'erro': 'Campanha não encontrada'}), 404
    conn.execute(
        "UPDATE mandazap_campaigns SET status='cancelada', finished_at=?, error_log='Cancelada pelo usuário.' WHERE id=?",
        (datetime.now().isoformat(), cid)
    )
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/mandazap/campanhas/<int:cid>/disparar', methods=['POST'])
@_mandazap_login_required
def mz_campaign_dispatch(cid):
    """Dispara imediatamente uma campanha."""
    user_id = session['mz_user_id']
    conn    = get_saas_db()
    camp    = conn.execute(
        'SELECT status, updated_at, created_at FROM mandazap_campaigns WHERE id=? AND user_id=?', (cid, user_id)
    ).fetchone()
    conn.close()
    if not camp:
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    status = camp['status']
    if status == 'enviando':
        # Verifica se está realmente ativa ou presa (stale > 5 min sem update)
        last_update = camp['updated_at'] or camp['created_at'] or ''
        try:
            dt_upd = datetime.fromisoformat(last_update) if last_update else None
            minutos_parada = (datetime.now() - dt_upd).total_seconds() / 60 if dt_upd else 999
        except Exception:
            minutos_parada = 999
        if minutos_parada < 5:
            return jsonify({'erro': 'Campanha já está sendo enviada (aguarde).'}), 400
        # Presa há mais de 5 minutos — permite re-dispatch (thread morta)
        log.warning(f"[dispatch] Campanha {cid} presa em 'enviando' há {minutos_parada:.0f}min — forçando re-dispatch")
        conn2 = get_saas_db()
        conn2.execute("UPDATE mandazap_campaigns SET status='rascunho' WHERE id=?", (cid,))
        conn2.commit(); conn2.close()
    elif status == 'concluida':
        return jsonify({'erro': 'Campanha já foi concluída. Duplique-a para reenviar.'}), 400
    # continuar=true (padrão) → retoma de onde parou; continuar=false → recomeça do zero
    data      = request.get_json(silent=True) or {}
    continuar = str(data.get('continuar', request.args.get('continuar', 'true'))).lower() != 'false'
    threading.Thread(target=_dispatch_campaign, args=(cid, user_id), kwargs={'continuar': continuar}, daemon=True).start()
    msg = 'Retomando de onde parou!' if continuar else 'Reiniciando do zero!'
    return jsonify({'ok': True, 'msg': msg, 'continuar': continuar})


@app.route('/mandazap/campanhas/<int:cid>/status')
@_mandazap_login_required
def mz_campaign_status(cid):
    """Polling de progresso da campanha."""
    user_id = session['mz_user_id']
    conn    = get_saas_db()
    camp    = conn.execute(
        'SELECT status, total, sent, finished_at, error_log FROM mandazap_campaigns WHERE id=? AND user_id=?',
        (cid, user_id)
    ).fetchone()
    conn.close()
    if not camp:
        return jsonify({'erro': 'Não encontrada'}), 404
    d = dict(camp)
    d['pct'] = round(d['sent'] / d['total'] * 100) if d['total'] else 0
    return jsonify(d)



# ══════════════════════════════════════════════════════════════════════════════
#  DESPACHANTE LESSMANN — integrado ao 4kitem
# ══════════════════════════════════════════════════════════════════════════════

from desp_db import (
    init_desp_db, stats_dashboard as desp_stats,
    SERVICOS as DESP_SERVICOS, SERVICOS_GRUPOS as DESP_SERVICOS_GRUPOS,
    FINAIS_PLACA as DESP_FINAIS_PLACA, MESES as DESP_MESES,
    STATUS_LABELS as DESP_STATUS_LABELS,
    KANBAN_COLUNAS as DESP_KANBAN_COLUNAS,
    kanban_os as desp_kanban_os,
    DOCS_POR_SERVICO as DESP_DOCS_POR_SERVICO,
    DOCS_PADRAO as DESP_DOCS_PADRAO,
    criar_os as desp_criar_os, get_os as desp_get_os,
    listar_os as desp_listar_os, atualizar_os as desp_atualizar_os,
    atualizar_os_status as desp_atualizar_os_status,
    criar_cliente as desp_criar_cliente, get_cliente as desp_get_cliente,
    atualizar_cliente as desp_atualizar_cliente,
    buscar_cliente_cpf as desp_buscar_cpf,
    criar_veiculo as desp_criar_veiculo,
    buscar_veiculo_placa as desp_buscar_placa,
    get_documentos_os as desp_get_docs,
    lista_final_placa as desp_lista_final_placa,
    listar_exercicios as desp_listar_exercicios,
    atualizar_situacao_pag as desp_atualizar_situacao,
    get_conn as get_desp_conn,
    listar_clientes as desp_listar_clientes,
    contar_clientes as desp_contar_clientes,
    get_cliente_detalhe as desp_get_cliente_detalhe,
    importar_clientes_bulk as desp_importar_bulk,
    listar_debitos as desp_listar_debitos,
    salvar_debitos_bulk as desp_salvar_debitos,
    deletar_debito as desp_deletar_debito,
    total_debitos as desp_total_debitos,
    # Parcelas
    criar_parcelas as desp_criar_parcelas,
    get_parcelas as desp_get_parcelas,
    dar_baixa_parcela as desp_baixa_parcela,
    estornar_parcela as desp_estornar_parcela,
    # Histórico
    registrar_historico as desp_reg_hist,
    get_historico_os as desp_get_historico,
    # Busca global
    busca_global as desp_busca_global,
    # Retenção
    relatorio_retencao as desp_rel_retencao,
    # Config / Preços
    get_tabela_precos as desp_get_precos,
    set_tabela_precos as desp_set_precos,
    get_preco_servico as desp_get_preco,
    get_tabela_custos as desp_get_custos,
    set_tabela_custos as desp_set_custos,
    get_custo_servico as desp_get_custo,
    # Relatório de Produção
    relatorio_producao as desp_rel_producao,
    # Relatório Fez / Não Fez
    relatorio_fez_nao_fez as desp_rel_fez_nao_fez,
    # Portal do cliente
    gerar_token_os as desp_gerar_token,
    get_os_por_token as desp_os_por_token,
    revogar_token_os as desp_revogar_token,
    # Protocolos RENAVAM
    listar_protocolos as desp_listar_protocolos,
    criar_protocolos_lote as desp_criar_lote_protocolos,
    deletar_protocolo as desp_deletar_protocolo,
    stats_protocolos as desp_stats_protocolos,
    # Não Licenciados
    veiculos_nao_licenciados as desp_nao_lic,
    stats_nao_licenciados as desp_stats_nao_lic,
    # Templates WhatsApp
    get_templates_wpp as desp_get_tpls,
    set_templates_wpp as desp_set_tpls,
    get_template_wpp as desp_get_tpl,
    TEMPLATES_PADRAO as DESP_TEMPLATES_PADRAO,
    # Checklist de documentos
    get_checklist_os as desp_get_checklist,
    toggle_checklist_item as desp_toggle_chk,
    add_checklist_item as desp_add_chk,
    remove_checklist_item as desp_remove_chk,
    # Usuários
    criar_usuario as desp_criar_usuario,
    get_usuario_por_login as desp_get_usuario,
    listar_usuarios as desp_listar_usuarios,
    toggle_usuario as desp_toggle_usuario,
    atualizar_senha_usuario as desp_atualizar_senha_usuario,
    registrar_ultimo_login as desp_reg_login,
    contar_usuarios as desp_contar_usuarios,
)
# ChromaDB desabilitado por padrão (evita OOM no Railway free tier)
# Para habilitar: setar DESP_RAG_ENABLED=1 no ambiente
_rag_disabled = os.environ.get('DESP_RAG_ENABLED', '0') != '1'
try:
    if _rag_disabled:
        raise ImportError("RAG desabilitado (defina DESP_RAG_ENABLED=1 para ativar)")
    import desp_rag
    _rag_ok = True
    # NÃO roda seed na inicialização — ChromaDB usa muita memória no Railway
    # Seed é disparado manualmente via /despachante/rag
    log.info('desp_rag carregado OK — seed sob demanda (não automático)')
except Exception as _e:
    _rag_ok = False
    log.warning(f'desp_rag não disponível: {_e}')

DESP_CONFIG = {
    "nome":         os.environ.get("DESP_NOME",       "DIOGO KAUE LESSMANN"),
    "cpf":          os.environ.get("DESP_CPF",        "060.625.099-99"),
    "cnpj":         os.environ.get("DESP_CNPJ",       "28.858.795/0001-92"),
    "credencial":   os.environ.get("DESP_CREDENCIAL",  "2095"),
    "cidade":       os.environ.get("DESP_CIDADE",     "SCHROEDER"),
    "citran":       os.environ.get("DESP_CITRAN",     "Guaramirim"),
    "whatsapp":     os.environ.get("DESP_WHATSAPP",   "47999606998"),
    "whatsapp_fmt": "(47) " + os.environ.get("DESP_WHATSAPP", "47999606998")[2:7] + "-" + os.environ.get("DESP_WHATSAPP", "47999606998")[7:],
}
DESP_PASSWORD       = os.environ.get("DESP_PASSWORD", "")
DESP_ADMIN_PASSWORD = os.environ.get("DESP_ADMIN_PASSWORD", "")
if not DESP_PASSWORD:
    import secrets as _sec
    DESP_PASSWORD = _sec.token_urlsafe(12)
    log.warning('[Desp] DESP_PASSWORD não configurado — usando senha temporária: %s', DESP_PASSWORD)


def _desp_tenant_db_path(user_id: int) -> str:
    """Retorna o caminho do banco SQLite isolado para um tenant SaaS."""
    data_dir = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(data_dir, f'desp_{user_id}.db')


@app.before_request
def _desp_set_tenant_context():
    """Seta g.desp_db_path para o banco correto a cada request."""
    from flask import g
    saas_uid = session.get('desp_saas_user_id')
    if saas_uid:
        g.desp_db_path = _desp_tenant_db_path(saas_uid)
        # Inicializa banco apenas uma vez por tenant (singleton em memória)
        if saas_uid not in _desp_init_done:
            from desp_db import init_db as _desp_init
            _desp_init()
            _desp_init_done.add(saas_uid)
            log.info(f'[Desp SaaS] Banco inicializado para tenant {saas_uid}: {g.desp_db_path}')
    else:
        g.desp_db_path = None  # usa DB_PATH fixo (Diogo)


def _desp_is_logged() -> bool:
    """True se logado por qualquer método (direto ou SaaS)."""
    return bool(session.get('desp_logged') or session.get('desp_saas_user_id'))


def _desp_usuario_atual():
    """Retorna o nome do usuário logado no despachante (para log de movimentações)."""
    return session.get('desp_usuario', DESP_CONFIG.get('nome', 'Sistema'))


def _desp_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _desp_is_logged():
            return redirect('/despachante/login')
        return f(*args, **kwargs)
    return decorated


def _desp_is_admin_check() -> bool:
    """
    Verifica se o usuário atual é admin.
    Ordem: sessão → banco desp_usuarios → DESP_ADMIN_PASSWORD definida.
    Seta session['desp_is_admin'] = True se confirmado.
    """
    if session.get('desp_is_admin'):
        return True
    # Re-verifica no banco (sessão pode ter sido iniciada antes do fix)
    try:
        login = session.get('desp_usuario', '')
        if login:
            u = desp_get_usuario(login)
            if u and u.get('role') == 'admin':
                session['desp_is_admin'] = True
                return True
    except Exception:
        pass
    return False


def _desp_admin_required(f):
    """Requer perfil admin (direto ou SaaS)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _desp_is_logged():
            return redirect('/despachante/login')
        if not _desp_is_admin_check():
            return redirect(url_for('desp_admin_login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def _desp_get_evo_config() -> tuple:
    """Retorna (evo_url, evo_key, evo_instance) para o usuário atual."""
    if session.get('desp_saas_user_id'):
        from desp_db import get_config as _gc
        url = (_gc('desp_evo_url') or '').rstrip('/')
        key = _gc('desp_evo_key') or ''
        inst = _gc('desp_evo_instance') or ''
    else:
        url  = os.environ.get('EVO_URL', '').rstrip('/')
        key  = os.environ.get('EVO_KEY', '')
        inst = os.environ.get('EVO_INSTANCE', '')
    return url, key, inst


def _desp_get_backup_email() -> str:
    """Retorna o email de backup do usuário atual."""
    if session.get('desp_saas_user_id'):
        from desp_db import get_config as _gc
        return _gc('desp_backup_email') or ''
    return os.environ.get('BACKUP_EMAIL', 'diogolessmann@gmail.com')


def _desp_get_plan() -> dict:
    """Retorna o plano e limites do tenant atual (sem limites para Diogo)."""
    uid = session.get('desp_saas_user_id')
    if not uid:
        return {'plan': 'premium', 'plan_active': 1, **DESP_PLAN_LIMITS['premium']}
    try:
        conn = get_saas_db()
        u = conn.execute(
            'SELECT plan, plan_active, trial_ends FROM despachante_users WHERE id=?', (uid,)
        ).fetchone()
        conn.close()
        if not u:
            return {'plan': 'basico', 'plan_active': 0, **DESP_PLAN_LIMITS['basico']}
        plan = u['plan'] if u['plan'] in DESP_PLAN_LIMITS else 'basico'
        return {'plan': plan, 'plan_active': u['plan_active'],
                'trial_ends': u['trial_ends'], **DESP_PLAN_LIMITS[plan]}
    except Exception:
        return {'plan': 'basico', 'plan_active': 1, **DESP_PLAN_LIMITS['basico']}


def _desp_check_limit(tipo: str) -> tuple:
    """
    Verifica se o tenant pode executar a ação.
    tipo: 'os_mes' | 'whatsapp'
    Retorna (permitido: bool, mensagem: str)
    """
    uid = session.get('desp_saas_user_id')
    if not uid:
        return True, ''  # Diogo: sem limites
    plano = _desp_get_plan()
    if not plano.get('plan_active'):
        return False, 'Plano inativo. Regularize sua assinatura para continuar.'
    if tipo == 'whatsapp':
        if not plano.get('whatsapp'):
            return False, f'Disparo de WhatsApp não disponível no plano {DESP_PLANS[plano["plan"]]["label"]}. Faça upgrade para o Profissional.'
        return True, ''
    if tipo == 'os_mes':
        limite = plano.get('os_mes')
        if limite is None:
            return True, ''
        from desp_db import get_conn as _gc
        conn = _gc()
        try:
            mes = datetime.now().strftime('%Y-%m')
            n = conn.execute(
                "SELECT COUNT(*) FROM ordens_servico WHERE strftime('%Y-%m', criado_em)=?", (mes,)
            ).fetchone()[0]
        finally:
            conn.close()
        if n >= limite:
            return False, (f'Limite de {limite} O.S./mês atingido no plano {DESP_PLANS[plano["plan"]]["label"]}. '
                          f'Faça upgrade para o Profissional ou aguarde o próximo mês.')
        return True, ''
    return True, ''


def _desp_get_config() -> dict:
    """
    Retorna a config do despachante ativa:
    - Tenant SaaS → lê da tabela config do banco do tenant
    - Diogo (direto) → usa DESP_CONFIG do env
    """
    if not session.get('desp_saas_user_id'):
        return DESP_CONFIG
    try:
        from desp_db import get_config as _gc
        nome  = _gc('desp_nome')  or session.get('desp_saas_name', 'Despachante')
        cpf   = _gc('desp_cpf')   or ''
        cnpj  = _gc('desp_cnpj')  or ''
        cred  = _gc('desp_cred')  or ''
        cidade= _gc('desp_cidade')or ''
        citran= _gc('desp_citran')or ''
        wpp   = _gc('desp_wpp')   or ''
        wpp_f = _gc('desp_wpp_fmt')or wpp
        return dict(nome=nome, cpf=cpf, cnpj=cnpj, credencial=cred,
                    cidade=cidade, citran=citran, whatsapp=wpp, whatsapp_fmt=wpp_f)
    except Exception:
        return dict(nome=session.get('desp_saas_name','Despachante'),
                    cpf='', cnpj='', credencial='', cidade='',
                    citran='', whatsapp='', whatsapp_fmt='')


def _desp_globals():
    hoje = datetime.now()
    try:
        _st = desp_stats()
        _n_alertas = len(_st.get('parcelas_vencidas', [])) + len(_st.get('os_paradas', []))
    except Exception:
        _n_alertas = 0
    return dict(
        desp=_desp_get_config(),
        servicos=DESP_SERVICOS,
        servicos_grupos=DESP_SERVICOS_GRUPOS,
        status_labels=DESP_STATUS_LABELS,
        hoje=hoje, mes_atual=hoje.month, meses=DESP_MESES,
        finais_placa_nav=sorted(DESP_FINAIS_PLACA.items(), key=lambda x: x[1]),
        n_alertas=_n_alertas,
        is_saas_tenant=bool(session.get('desp_saas_user_id')),
    )


def desp_render(template, **ctx):
    return render_template(f'despachante/{template}', **{**_desp_globals(), **ctx})


# ── Login ─────────────────────────────────────────────────────────────────────
@app.route('/despachante/login', methods=['GET', 'POST'])
def desp_login():
    erro = None
    if request.method == 'POST':
        login = (request.form.get('usuario') or '').strip().lower()
        senha = request.form.get('senha') or ''

        # ── Modo multi-usuário (tabela desp_usuarios populada) ─────────────
        if desp_contar_usuarios() > 0:
            u = desp_get_usuario(login)
            if not u:
                erro = 'Usuário não encontrado.'
            elif not check_password_hash(u['senha_hash'], senha):
                erro = 'Senha incorreta.'
            else:
                session['desp_logged']   = True
                session['desp_user_id']  = u['id']
                session['desp_usuario']  = u['nome']
                session['desp_is_admin'] = (u['role'] == 'admin')
                desp_reg_login(u['id'])
                return redirect('/despachante/')

        # ── Modo legado: senha única (DESP_PASSWORD) ───────────────────────
        else:
            if senha == DESP_PASSWORD:
                nome_user = request.form.get('usuario', '').strip() or 'Diogo'
                session['desp_logged']   = True
                session['desp_usuario']  = nome_user
                session['desp_is_admin'] = True

                # Se já migrou para SaaS, usa o banco do tenant automaticamente
                tenant_id = _desp_direct_config('desp_saas_tenant_id')
                if tenant_id:
                    session['desp_saas_user_id'] = int(tenant_id)
                    session['desp_saas_name']    = nome_user
                    log.info(f'[Desp] Login direto → usando tenant {tenant_id}')

                return redirect('/despachante/')
            else:
                erro = 'Senha incorreta.'

    return render_template('despachante/login.html', erro=erro)


@app.route('/despachante/logout')
def desp_logout():
    saas = bool(session.get('desp_saas_user_id'))
    for k in ('desp_logged', 'desp_user_id', 'desp_usuario', 'desp_is_admin',
              'desp_saas_user_id', 'desp_saas_name'):
        session.pop(k, None)
    return redirect('/amigo-despachante/entrar' if saas else '/despachante/login')



# ── Fase 4: Migração do Diogo para conta SaaS ────────────────────────────────

def _desp_direct_config(chave: str):
    """Lê config diretamente do desp.db fixo, ignorando contexto de tenant."""
    from desp_db import DB_PATH as _FIXED_DB
    import sqlite3 as _sq3
    try:
        c = _sq3.connect(_FIXED_DB)
        r = c.execute("SELECT valor FROM config WHERE chave=?", (chave,)).fetchone()
        c.close()
        return r[0] if r else None
    except Exception:
        return None


def _desp_direct_set_config(chave: str, valor: str):
    """Salva config diretamente no desp.db fixo."""
    from desp_db import DB_PATH as _FIXED_DB
    import sqlite3 as _sq3
    c = _sq3.connect(_FIXED_DB)
    c.execute("""INSERT INTO config (chave, valor, atualizado_em)
                 VALUES (?,?,CURRENT_TIMESTAMP)
                 ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor,
                 atualizado_em=CURRENT_TIMESTAMP""", (chave, valor))
    c.commit()
    c.close()


@app.route('/despachante/migrar', methods=['GET', 'POST'])
@_desp_admin_required
def desp_migrar_saas():
    """Migra o desp.db de Diogo para uma conta SaaS isolada."""
    import shutil
    from desp_db import DB_PATH as _FIXED_DB

    # Já migrado?
    tenant_existente = _desp_direct_config('desp_saas_tenant_id')
    if tenant_existente:
        return desp_render('migrar.html', ja_migrado=True,
                           tenant_id=tenant_existente, erro=None, sucesso=False)

    erro = None
    if request.method == 'POST':
        nome   = request.form.get('nome', '').strip()
        email  = request.form.get('email', '').strip().lower()
        phone  = request.form.get('phone', '').strip()
        senha  = request.form.get('senha', '').strip()

        if not all([nome, email, phone, senha]):
            erro = 'Todos os campos são obrigatórios.'
        elif len(senha) < 6:
            erro = 'Senha deve ter pelo menos 6 caracteres.'
        else:
            # 1. Cria conta em despachante_users (saas.db)
            phone_digits = ''.join(c for c in phone if c.isdigit())
            now = datetime.now().isoformat()
            trial = (datetime.now() + timedelta(days=36500)).isoformat()  # sem expiração
            try:
                conn_saas = get_saas_db()
                cur = conn_saas.execute("""
                    INSERT INTO despachante_users
                        (name, email, phone, plan, active, password_hash, created_at, trial_ends, plan_active)
                    VALUES (?,?,?,'profissional',1,?,?,?,1)
                """, (nome, email, phone_digits, generate_password_hash(senha), now, trial))
                tenant_id = cur.lastrowid
                conn_saas.commit()
                conn_saas.close()
            except Exception as ex:
                erro = f'Erro ao criar conta SaaS: {ex}'
                return desp_render('migrar.html', erro=erro, sucesso=False,
                                   ja_migrado=False, tenant_id=None)

            # 2. Copia desp.db → desp_<id>.db
            destino = _desp_tenant_db_path(tenant_id)
            try:
                if not os.path.exists(destino):
                    shutil.copy2(_FIXED_DB, destino)
                    log.info(f'[Migração] {_FIXED_DB} → {destino}')
                else:
                    log.info(f'[Migração] {destino} já existe, mantido.')
            except Exception as ex:
                erro = f'Erro ao copiar banco de dados: {ex}'
                return desp_render('migrar.html', erro=erro, sucesso=False,
                                   ja_migrado=False, tenant_id=None)

            # 3. Salva tenant_id no desp.db para o login direto reconhecer
            _desp_direct_set_config('desp_saas_tenant_id', str(tenant_id))

            log.info(f'[Migração] Concluída: Diogo → tenant {tenant_id}')
            return desp_render('migrar.html', sucesso=True, tenant_id=tenant_id,
                               ja_migrado=False, erro=None, email=email)

    return desp_render('migrar.html', erro=erro, sucesso=False,
                       ja_migrado=False, tenant_id=None)


# ── Admin Login ───────────────────────────────────────────────────────────────
@app.route('/despachante/admin-login', methods=['GET', 'POST'])
@_desp_login_required
def desp_admin_login():
    next_url = request.args.get('next') or request.form.get('next') or '/despachante/precos'

    # Usuário já tem admin na sessão ou tem role='admin' no banco → passa direto
    if _desp_is_admin_check():
        return redirect(next_url)

    # Fallback: senha avulsa (DESP_ADMIN_PASSWORD) para casos sem multi-usuário
    erro = None
    if request.method == 'POST':
        senha_form = request.form.get('senha', '')
        # Aceita DESP_ADMIN_PASSWORD ou a senha do login direto (DESP_PASSWORD)
        senhas_validas = [s for s in [DESP_ADMIN_PASSWORD, DESP_PASSWORD] if s]
        if senha_form and senha_form in senhas_validas:
            session['desp_is_admin'] = True
            return redirect(next_url)
        erro = 'Senha incorreta.'
    return render_template('despachante/admin_login.html', erro=erro, next=next_url)


# ── Gerenciamento de Usuários (admin only) ────────────────────────────────────
@app.route('/despachante/usuarios')
@_desp_admin_required
def desp_usuarios():
    usuarios = desp_listar_usuarios()
    return desp_render('usuarios.html', usuarios=usuarios)


@app.route('/despachante/usuarios/novo', methods=['POST'])
@_desp_admin_required
def desp_usuario_novo():
    nome   = request.form.get('nome', '').strip()
    login  = request.form.get('usuario', '').strip().lower()
    senha  = request.form.get('senha', '').strip()
    role   = request.form.get('role', 'operador')
    erros  = []
    if not nome:  erros.append('Nome obrigatório.')
    if not login: erros.append('Usuário obrigatório.')
    if len(senha) < 6: erros.append('Senha deve ter pelo menos 6 caracteres.')
    if role not in ('admin', 'operador'): role = 'operador'
    if erros:
        from flask import flash
        [flash(e, 'erro') for e in erros]
        return redirect(url_for('desp_usuarios'))
    try:
        desp_criar_usuario(nome, login, generate_password_hash(senha), role)
    except Exception:
        from flask import flash
        flash('Usuário já existe com esse login.', 'erro')
    return redirect(url_for('desp_usuarios'))


@app.route('/despachante/usuarios/<int:uid>/toggle', methods=['POST'])
@_desp_admin_required
def desp_usuario_toggle(uid):
    if uid == session.get('desp_user_id'):
        return jsonify({'erro': 'Você não pode desativar sua própria conta'}), 400
    ativo = request.get_json(silent=True) or {}
    desp_toggle_usuario(uid, bool(ativo.get('ativo', True)))
    return jsonify({'ok': True})


@app.route('/despachante/usuarios/<int:uid>/senha', methods=['POST'])
@_desp_admin_required
def desp_usuario_reset_senha(uid):
    nova = (request.get_json(silent=True) or {}).get('senha', '').strip()
    if len(nova) < 6:
        return jsonify({'erro': 'Senha deve ter pelo menos 6 caracteres'}), 400
    desp_atualizar_senha_usuario(uid, generate_password_hash(nova))
    return jsonify({'ok': True})


# ── Tutorial ──────────────────────────────────────────────────────────────────
@app.route('/despachante/tutorial')
@_desp_login_required
def desp_tutorial():
    return desp_render('tutorial.html')


# ── Onboarding — primeiro acesso de tenant SaaS ───────────────────────────────

def _desp_needs_onboarding() -> bool:
    """True se é tenant SaaS e ainda não configurou o perfil."""
    if not session.get('desp_saas_user_id'):
        return False
    from desp_db import get_config as _gc
    return not bool(_gc('desp_nome'))


@app.route('/despachante/onboarding', methods=['GET', 'POST'])
@_desp_login_required
def desp_onboarding():
    if not session.get('desp_saas_user_id'):
        return redirect('/despachante/')
    from desp_db import set_config as _sc
    erro = None
    if request.method == 'POST':
        nome   = request.form.get('nome', '').strip()
        cred   = request.form.get('credencial', '').strip()
        cidade = request.form.get('cidade', '').strip()
        wpp    = ''.join(c for c in request.form.get('whatsapp','') if c.isdigit())
        if not all([nome, cred, cidade, wpp]):
            erro = 'Nome, credencial, cidade e WhatsApp são obrigatórios.'
        else:
            # Formata WhatsApp: (47) 99101-1351
            wpp_fmt = f'({wpp[:2]}) {wpp[2:7]}-{wpp[7:]}' if len(wpp) >= 10 else wpp
            campos = {
                'desp_nome':    nome,
                'desp_cpf':     request.form.get('cpf','').strip(),
                'desp_cnpj':    request.form.get('cnpj','').strip(),
                'desp_cred':    cred,
                'desp_cidade':  cidade,
                'desp_citran':  request.form.get('citran','').strip(),
                'desp_wpp':     wpp,
                'desp_wpp_fmt': wpp_fmt,
                'desp_backup_email': request.form.get('backup_email','').strip(),
            }
            for k, v in campos.items():
                if v:
                    _sc(k, v)
            return redirect('/despachante/')
    # Pré-popula com dados da conta SaaS
    nome_saas = session.get('desp_saas_name', '')
    return render_template('despachante/onboarding.html',
                           nome_saas=nome_saas, erro=erro)


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/despachante/')
@app.route('/despachante')
@_desp_login_required
def desp_dashboard():
    # Redireciona para onboarding se tenant novo sem perfil configurado
    if _desp_needs_onboarding():
        return redirect(url_for('desp_onboarding'))
    stats    = desp_stats()
    recentes = desp_listar_os(limit=8)
    plano    = _desp_get_plan() if session.get('desp_saas_user_id') else None
    return desp_render('dashboard.html', stats=stats, recentes=recentes, plano=plano)


# ── Ordens de Serviço ─────────────────────────────────────────────────────────
@app.route('/despachante/os')
@_desp_login_required
def desp_lista_os():
    status = request.args.get('status')
    busca  = request.args.get('q', '').strip()
    page   = request.args.get('page', 1, type=int)
    offset = (page - 1) * 20
    ordens = desp_listar_os(status=status or None, busca=busca or None, limit=20, offset=offset)
    return desp_render('os/lista.html', ordens=ordens, status_sel=status, busca=busca, page=page)


@app.route('/despachante/os/nova', methods=['GET', 'POST'])
@_desp_login_required
def desp_nova_os():
    if request.method == 'POST':
        # Verifica limite de OS do plano
        ok, msg = _desp_check_limit('os_mes')
        if not ok:
            from flask import flash
            flash(f'🚫 {msg}', 'erro')
            return redirect(url_for('desp_nova_os'))
        f = request.form
        cliente_id = f.get('cliente_id') or None
        if not cliente_id:
            dados_cli = {
                'tipo': f.get('cli_tipo', 'PF'), 'nome': f.get('cli_nome', '').strip(),
                'cpf': f.get('cli_cpf', '').strip(), 'cnpj': f.get('cli_cnpj', '').strip(),
                'rg': f.get('cli_rg', '').strip(), 'nascimento': f.get('cli_nasc', ''),
                'nome_mae': f.get('cli_mae', ''), 'telefone': f.get('cli_tel', ''),
                'email': f.get('cli_email', ''), 'cep': f.get('cli_cep', ''),
                'logradouro': f.get('cli_rua', ''), 'numero': f.get('cli_num', ''),
                'complemento': f.get('cli_comp', ''), 'bairro': f.get('cli_bairro', ''),
                'cidade': f.get('cli_cidade', ''), 'uf': f.get('cli_uf', 'SC'),
            }
            if dados_cli['nome']:
                existente = desp_buscar_cpf(dados_cli['cpf']) if dados_cli['cpf'] else None
                if existente:
                    cliente_id = existente['id']
                    desp_atualizar_cliente(cliente_id, dados_cli)
                else:
                    cliente_id = desp_criar_cliente(dados_cli)
        veiculo_id = f.get('veiculo_id') or None
        if not veiculo_id and f.get('v_placa', '').strip():
            dados_vei = {
                'placa': f.get('v_placa', '').upper().replace('-', ''),
                'renavam': f.get('v_renavam', ''), 'chassi': f.get('v_chassi', ''),
                'marca': f.get('v_marca', ''), 'modelo': f.get('v_modelo', ''),
                'ano_fab': f.get('v_anofab') or None, 'ano_mod': f.get('v_anomod') or None,
                'cor': f.get('v_cor', ''), 'especie': f.get('v_especie', 'Automóvel'),
                'tipo_veiculo': f.get('v_tipo', ''), 'categoria': f.get('v_categoria', 'Particular'),
                'combustivel': f.get('v_combustivel', ''), 'num_crv': f.get('v_crv', ''),
                'proprietario_id': cliente_id,
            }
            veiculo_id = desp_criar_veiculo(dados_vei)
        dados_os = {
            'cliente_id': cliente_id, 'veiculo_id': veiculo_id,
            'servico': f.get('servico', 'outros'),
            'honorarios': float(f.get('honorarios') or 0),
            'custos': float(f.get('custos') or 0),
            'pago': float(f.get('pago') or 0),
            'forma_pagamento': f.get('forma_pagamento', ''),
            'observacoes': f.get('observacoes', ''),
            'exercicio': int(f.get('exercicio') or datetime.now().year),
            'situacao_pag': f.get('situacao_pag', ''),
        }
        os_id = desp_criar_os(dados_os)
        # Redireciona para o detalhe da OS (impressão é opcional pelo botão)
        return redirect(url_for('desp_detalhe_os', id=os_id))
    placa_pre = request.args.get('placa', '')
    cpf_pre   = request.args.get('cpf', '').strip()
    veiculo   = desp_buscar_placa(placa_pre) if placa_pre else None
    if veiculo and veiculo.get('proprietario_id'):
        cliente = desp_get_cliente(veiculo['proprietario_id'])
    elif cpf_pre:
        cliente = desp_buscar_cpf(cpf_pre)
    else:
        cliente = None
    return desp_render('os/nova.html', veiculo=veiculo, cliente=cliente,
                       placa_pre=placa_pre, cpf_pre=cpf_pre)


@app.route('/despachante/os/<int:id>')
@_desp_login_required
def desp_detalhe_os(id):
    os_ = desp_get_os(id)
    if not os_: abort(404)
    docs      = desp_get_docs(id)
    debitos   = desp_listar_debitos(id)
    total_deb = desp_total_debitos(id)
    parcelas  = desp_get_parcelas(id)
    historico = desp_get_historico(id)
    checklist = desp_get_checklist(id, os_.get('servico', ''))
    # Renderizar templates WhatsApp com dados da OS
    tpls      = desp_get_tpls()
    os_total  = float(os_.get('honorarios', 0)) + float(os_.get('custos', 0))
    os_pend   = max(os_total - float(os_.get('pago', 0)), 0)
    _vars     = dict(
        nome          = (os_.get('cliente_nome') or 'cliente').split()[0].title(),
        nome_completo = (os_.get('cliente_nome') or '').title(),
        numero        = os_.get('numero', ''),
        servico       = DESP_SERVICOS.get(os_.get('servico', ''), os_.get('servico', '')),
        placa         = (os_.get('placa') or '').upper(),
        mes           = '', exercicio = datetime.now().year,
        pendente      = f'{os_pend:.2f}'.replace('.', ','),
        pix           = _desp_get_config().get('cpf', ''),
        despachante   = _desp_get_config()['nome'].title(),
        whatsapp      = _desp_get_config()['whatsapp_fmt'],
        cidade        = _desp_get_config()['cidade'],
    )
    def _render_tpl(chave):
        try: return tpls[chave]['texto'].format(**_vars)
        except Exception: return tpls.get(chave, {}).get('texto', '')
    wpp_msgs = {k: _render_tpl(k) for k in tpls}
    return desp_render('os/detalhe.html', os=os_, docs=docs,
                       debitos=debitos, total_debitos=total_deb,
                       parcelas=parcelas, historico=historico,
                       checklist=checklist, wpp_msgs=wpp_msgs,
                       os_pendente=os_pend)


@app.route('/despachante/os/<int:id>/status', methods=['POST'])
@_desp_login_required
def desp_atualizar_status(id):
    status = request.form.get('status', 'aberta')
    nota   = request.form.get('nota', '')
    pago   = request.form.get('pago')
    desp_atualizar_os_status(id, status, float(pago) if pago else None)
    desp_reg_hist(id, status, nota, usuario=_desp_usuario_atual())
    return redirect(url_for('desp_detalhe_os', id=id))


@app.route('/despachante/os/<int:id>/entregar', methods=['POST'])
@_desp_login_required
def desp_marcar_entregue(id):
    """Marca a OS como documento entregue ao cliente — com data/hora e quem entregou."""
    usuario = _desp_usuario_atual()
    agora   = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn = get_desp_conn()
    conn.execute(
        "UPDATE ordens_servico SET entregue_em=?, entregue_por=? WHERE id=?",
        (agora, usuario, id)
    )
    conn.commit(); conn.close()
    # Registra no histórico (imutável)
    desp_reg_hist(
        id,
        'entregue',
        f'Documento entregue ao cliente por {usuario} em {agora}',
        usuario=usuario
    )
    return redirect(url_for('desp_detalhe_os', id=id))


# ── Parcelas ─────────────────────────────────────────────────────────────────

@app.route('/despachante/api/os/<int:os_id>/parcelas', methods=['POST'])
@_desp_login_required
def desp_api_criar_parcelas(os_id):
    data = request.get_json(silent=True) or {}
    n    = int(data.get('total_parcelas', 1))
    os_  = desp_get_os(os_id)
    if not os_:
        return jsonify({'erro': 'OS não encontrada'}), 404
    total = float(os_['honorarios']) + float(os_['custos'])
    if total <= 0:
        return jsonify({'erro': 'OS sem valor — defina honorários/custos primeiro'}), 400
    parcelas = desp_criar_parcelas(
        os_id, n, total,
        vencimento_1=data.get('vencimento_1'),
        forma=data.get('forma', '')
    )
    desp_reg_hist(os_id, os_['status'],
                  f"Parcelamento em {n}x configurado (total R$ {total:.2f})")
    return jsonify({'ok': True, 'parcelas': parcelas})


@app.route('/despachante/api/parcela/<int:pid>/baixa', methods=['POST'])
@_desp_login_required
def desp_api_baixa_parcela(pid):
    data = request.get_json(silent=True) or {}
    forma = data.get('forma', 'Dinheiro')
    obs   = data.get('observacao', '')
    res   = desp_baixa_parcela(pid, forma, obs)
    if 'erro' in res:
        return jsonify(res), 400
    # Registra histórico
    c = get_desp_conn()
    row = c.execute("SELECT os_id, numero, valor FROM os_parcelas WHERE id=?", (pid,)).fetchone()
    c.close()
    if row:
        desp_reg_hist(row['os_id'], None,
                      f"Parcela {row['numero']} paga — R$ {row['valor']:.2f} ({forma})")
    return jsonify(res)


@app.route('/despachante/api/parcela/<int:pid>/estornar', methods=['POST'])
@_desp_login_required
def desp_api_estornar_parcela(pid):
    res = desp_estornar_parcela(pid)
    if 'erro' in res:
        return jsonify(res), 400
    return jsonify(res)


@app.route('/despachante/print/recibo/<int:pid>')
@_desp_login_required
def desp_print_recibo(pid):
    from desp_db import get_conn as _gc
    c = _gc()
    row = c.execute("""
        SELECT p.*, os.numero AS os_numero, os.servico, os.total, os.pago,
               os.total_parcelas,
               c.nome, c.cpf, c.cnpj, c.telefone, c.cidade,
               v.placa, v.marca, v.modelo
        FROM os_parcelas p
        JOIN ordens_servico os ON os.id = p.os_id
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE p.id=?
    """, (pid,)).fetchone()
    c.close()
    if not row:
        abort(404)
    return desp_render('print/recibo.html', p=dict(row),
                       servicos=DESP_SERVICOS, hoje=datetime.now())


# ── Checklist de documentos ──────────────────────────────────────────────────

@app.route('/despachante/api/os/<int:os_id>/checklist', methods=['GET'])
@_desp_login_required
def desp_api_checklist_get(os_id):
    os_ = desp_get_os(os_id)
    if not os_: return jsonify({'erro': 'OS não encontrada'}), 404
    chk = desp_get_checklist(os_id, os_.get('servico', ''))
    feitos = sum(1 for c in chk if c['feito'])
    return jsonify({'checklist': chk, 'feitos': feitos, 'total': len(chk)})

@app.route('/despachante/api/os/<int:os_id>/checklist/toggle', methods=['POST'])
@_desp_login_required
def desp_api_checklist_toggle(os_id):
    os_ = desp_get_os(os_id)
    if not os_: return jsonify({'erro': 'OS não encontrada'}), 404
    data  = request.get_json(silent=True) or {}
    idx   = int(data.get('idx', -1))
    feito = bool(data.get('feito', False))
    chk   = desp_toggle_chk(os_id, idx, feito, os_.get('servico', ''))
    feitos = sum(1 for c in chk if c['feito'])
    return jsonify({'ok': True, 'checklist': chk, 'feitos': feitos, 'total': len(chk)})

@app.route('/despachante/api/os/<int:os_id>/checklist/add', methods=['POST'])
@_desp_login_required
def desp_api_checklist_add(os_id):
    os_ = desp_get_os(os_id)
    if not os_: return jsonify({'erro': 'OS não encontrada'}), 404
    data = request.get_json(silent=True) or {}
    item = data.get('item', '').strip()
    if not item: return jsonify({'erro': 'Item vazio'}), 400
    chk = desp_add_chk(os_id, item, os_.get('servico', ''))
    return jsonify({'ok': True, 'checklist': chk})

@app.route('/despachante/api/os/<int:os_id>/checklist/remove', methods=['POST'])
@_desp_login_required
def desp_api_checklist_remove(os_id):
    os_ = desp_get_os(os_id)
    if not os_: return jsonify({'erro': 'OS não encontrada'}), 404
    data = request.get_json(silent=True) or {}
    idx  = int(data.get('idx', -1))
    chk  = desp_remove_chk(os_id, idx, os_.get('servico', ''))
    return jsonify({'ok': True, 'checklist': chk})


# ── Busca global ─────────────────────────────────────────────────────────────

@app.route('/despachante/api/busca')
@_desp_login_required
def desp_api_busca():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'clientes': [], 'veiculos': [], 'ordens': []})
    return jsonify(desp_busca_global(q, limit=8))


# ── Relatório de Retenção ─────────────────────────────────────────────────────

@app.route('/despachante/retencao')
@_desp_login_required
def desp_rel_retencao_view():
    ano     = int(request.args.get('ano', datetime.now().year - 1))
    servico = request.args.get('servico', '')
    dados   = desp_rel_retencao(ano, servico or None)
    anos    = list(range(datetime.now().year, 2022, -1))
    return desp_render('relatorio/retencao.html',
                       dados=dados, ano=ano, servico=servico,
                       anos=anos, servicos=DESP_SERVICOS)


@app.route('/despachante/retencao/csv')
@_desp_login_required
def desp_rel_retencao_csv():
    import csv, io
    ano     = int(request.args.get('ano', datetime.now().year - 1))
    servico = request.args.get('servico', '')
    dados   = desp_rel_retencao(ano, servico or None)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['nome', 'cpf', 'telefone', 'placa', 'final_placa', 'servico',
                'data', 'exercicio', 'honorarios', 'cidade', 'os_numero'])
    for d in dados:
        w.writerow([d.get('nome',''), d.get('cpf',''), d.get('telefone',''),
                    d.get('placa',''), d.get('final_placa',''),
                    DESP_SERVICOS.get(d.get('servico',''), d.get('servico','')),
                    (d.get('criado_em','') or '')[:10],
                    d.get('exercicio',''), d.get('honorarios',''),
                    d.get('cidade',''), d.get('numero','')])
    out.seek(0)
    return out.getvalue(), 200, {
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': f'attachment; filename=retencao_{ano}.csv'
    }


# ── Relatório Fez / Não Fez ───────────────────────────────────────────────────

@app.route('/despachante/fez-nao-fez')
@_desp_login_required
def desp_fez_nao_fez():
    hoje     = datetime.now()
    servico  = request.args.get('servico', 'licenciamento')
    # Padrão: ano corrente
    ano      = int(request.args.get('ano', hoje.year))
    data_ini = request.args.get('data_ini', f'{ano}-01-01')
    data_fim = request.args.get('data_fim', f'{ano}-12-31')

    dados = desp_rel_fez_nao_fez(servico, data_ini, data_fim)
    anos  = list(range(hoje.year, 2022, -1))

    return desp_render('relatorio/fez_nao_fez.html',
                       dados=dados, servico=servico,
                       data_ini=data_ini, data_fim=data_fim, ano=ano,
                       anos=anos,
                       servicos=DESP_SERVICOS,
                       servicos_grupos=DESP_SERVICOS_GRUPOS)


# ── Kanban de OS ──────────────────────────────────────────────────────────────

@app.route('/despachante/kanban')
@_desp_login_required
def desp_kanban():
    colunas = desp_kanban_os()
    return desp_render('kanban.html',
                       colunas=colunas,
                       kanban_cols=DESP_KANBAN_COLUNAS,
                       servicos=DESP_SERVICOS)


@app.route('/despachante/api/os/<int:os_id>/mover', methods=['POST'])
@_desp_login_required
def desp_api_mover_os(os_id):
    """Move uma OS para outro status (usado pelo drag-and-drop do Kanban)."""
    data       = request.get_json(silent=True) or {}
    novo_status = data.get('status', '')
    status_validos = [c[0] for c in DESP_KANBAN_COLUNAS] + ['cancelada']
    if novo_status not in status_validos:
        return jsonify({'erro': 'Status inválido'}), 400
    try:
        desp_atualizar_os_status(os_id, novo_status)
        desp_reg_hist(os_id, novo_status,
                      f"Status alterado via Kanban → {DESP_STATUS_LABELS.get(novo_status, ('',''))[1]}")
        return jsonify({'ok': True})
    except Exception as e:
        log.error(f'desp_api_mover_os error: {e}')
        return jsonify({'erro': str(e)}), 500


# ── Portal do cliente ─────────────────────────────────────────────────────────

@app.route('/despachante/api/os/<int:os_id>/gerar-link', methods=['POST'])
@_desp_login_required
def desp_api_gerar_link(os_id):
    """Gera (ou retorna) o token público para o portal do cliente."""
    try:
        token = desp_gerar_token(os_id)
        url   = request.host_url.rstrip('/') + f'/cliente/{token}'
        return jsonify({'ok': True, 'token': token, 'url': url})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/despachante/api/os/<int:os_id>/revogar-link', methods=['POST'])
@_desp_login_required
def desp_api_revogar_link(os_id):
    """Revoga o token público da OS."""
    try:
        desp_revogar_token(os_id)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/cliente/<token>')
def portal_cliente(token):
    """Portal público do cliente — sem login, acesso por token."""
    os_ = desp_os_por_token(token)
    if not os_:
        return render_template('cliente/404.html'), 404

    checklist = desp_get_checklist(os_['id'], os_.get('servico', ''))
    parcelas  = desp_get_parcelas(os_['id'])
    feitos    = sum(1 for c in checklist if c['feito'])

    return render_template('cliente/portal.html',
                           os=os_,
                           checklist=checklist,
                           parcelas=parcelas,
                           feitos=feitos,
                           servicos=DESP_SERVICOS,
                           status_labels=DESP_STATUS_LABELS,
                           desp=DESP_CONFIG,
                           hoje=datetime.now())


@app.route('/despachante/financeiro')
@_desp_login_required
def desp_financeiro():
    """Módulo financeiro completo — 12 meses, formas de pgto, recebíveis."""
    conn = get_desp_conn()
    ano  = datetime.now().strftime("%Y")
    mes  = datetime.now().strftime("%Y-%m")

    # Últimos 12 meses de faturamento
    fat_12 = []
    for i in range(11, -1, -1):
        d = date.today().replace(day=1)
        m = d.month - i
        y = d.year
        while m <= 0:
            m += 12; y -= 1
        mes_str = f"{y}-{m:02d}"
        row = conn.execute(
            "SELECT COALESCE(SUM(honorarios+custos),0), COALESCE(SUM(pago),0), COUNT(*) "
            "FROM ordens_servico WHERE strftime('%Y-%m',criado_em)=? AND status!='cancelada'",
            (mes_str,)
        ).fetchone()
        fat_12.append({"mes": mes_str, "faturado": round(float(row[0]),2),
                       "recebido": round(float(row[1]),2), "qtd": row[2]})

    # Breakdown por serviço (ano corrente)
    fat_servico = [dict(r) for r in conn.execute("""
        SELECT servico, COUNT(*) as qtd,
               COALESCE(SUM(honorarios),0) as honorarios,
               COALESCE(SUM(custos),0) as custos,
               COALESCE(SUM(pago),0) as pago
        FROM ordens_servico
        WHERE strftime('%Y',criado_em)=? AND status!='cancelada'
        GROUP BY servico ORDER BY honorarios DESC
    """, (ano,)).fetchall()]

    # Breakdown por forma de pagamento (ano corrente)
    fat_forma = [dict(r) for r in conn.execute("""
        SELECT COALESCE(NULLIF(forma_pagamento,''),'Não informado') as forma,
               COUNT(*) as qtd, COALESCE(SUM(pago),0) as total
        FROM ordens_servico
        WHERE strftime('%Y',criado_em)=? AND pago>0 AND status!='cancelada'
        GROUP BY forma ORDER BY total DESC
    """, (ano,)).fetchall()]

    # OS com valores pendentes (a receber)
    os_pendentes = [dict(r) for r in conn.execute("""
        SELECT os.id, os.numero, os.status, os.criado_em,
               os.honorarios, os.custos, os.pago,
               (os.honorarios + os.custos - os.pago) AS pendente,
               c.nome AS cliente_nome, c.telefone,
               v.placa
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE os.status NOT IN ('cancelada')
          AND (os.honorarios + os.custos - os.pago) > 0.01
        ORDER BY pendente DESC
        LIMIT 50
    """).fetchall()]

    # Parcelas vencidas
    parcelas_vencidas = [dict(r) for r in conn.execute("""
        SELECT p.id, p.os_id, p.numero, p.valor, p.vencimento,
               os.numero AS os_numero, os.status AS os_status,
               c.nome AS cliente_nome, c.telefone,
               CAST((julianday('now') - julianday(p.vencimento)) AS INTEGER) AS dias_atraso
        FROM os_parcelas p
        JOIN ordens_servico os ON os.id = p.os_id
        LEFT JOIN clientes c ON c.id = os.cliente_id
        WHERE p.pago_em IS NULL AND p.vencimento < date('now')
        ORDER BY dias_atraso DESC
    """).fetchall()]

    # Totais consolidados do ano
    totais_ano = dict(conn.execute("""
        SELECT COALESCE(SUM(honorarios+custos),0) AS faturado,
               COALESCE(SUM(pago),0) AS recebido,
               COALESCE(SUM(CASE WHEN status!='concluida' AND (honorarios+custos-pago)>0.01
                              THEN honorarios+custos-pago ELSE 0 END),0) AS a_receber,
               COUNT(*) AS qtd_os
        FROM ordens_servico WHERE strftime('%Y',criado_em)=? AND status!='cancelada'
    """, (ano,)).fetchone())

    conn.close()
    return desp_render('financeiro.html',
        fat_12=fat_12, fat_servico=fat_servico,
        fat_forma=fat_forma, os_pendentes=os_pendentes,
        parcelas_vencidas=parcelas_vencidas,
        totais_ano=totais_ano, ano=ano,
        servicos=DESP_SERVICOS)


@app.route('/despachante/precos', methods=['GET', 'POST'])
@_desp_admin_required
def desp_precos():
    """Tabela de preços por serviço — restrita ao admin. Salva honorários + custo."""
    if request.method == 'POST':
        tabela_precos = {}
        tabela_custos = {}
        for svc in DESP_SERVICOS:
            val_p = request.form.get(f'preco_{svc}', '').strip()
            val_c = request.form.get(f'custo_{svc}', '').strip()
            if val_p:
                try:
                    tabela_precos[svc] = float(val_p.replace(',', '.'))
                except ValueError:
                    pass
            if val_c:
                try:
                    tabela_custos[svc] = float(val_c.replace(',', '.'))
                except ValueError:
                    pass
        desp_set_precos(tabela_precos)
        desp_set_custos(tabela_custos)
        from flask import flash
        flash('Tabela de preços salva com sucesso!', 'ok')
        return redirect(url_for('desp_precos'))
    precos = desp_get_precos()
    custos = desp_get_custos()
    return desp_render('precos.html', precos=precos, custos=custos,
                       servicos=DESP_SERVICOS, servicos_grupos=DESP_SERVICOS_GRUPOS)


@app.route('/despachante/api/preco/<servico>')
@_desp_login_required
def desp_api_preco(servico):
    """Retorna o preço padrão de um serviço para auto-fill na nova OS."""
    valor = desp_get_preco(servico)
    custo = desp_get_custo(servico)
    return jsonify({'servico': servico, 'preco': valor, 'custo': custo})


@app.route('/despachante/relatorio')
@_desp_login_required
def desp_relatorio():
    """Relatório de produção por período."""
    hoje    = date.today()
    ini_def = hoje.replace(day=1).strftime('%Y-%m-%d')
    fim_def = hoje.strftime('%Y-%m-%d')
    data_ini = request.args.get('ini', ini_def)
    data_fim = request.args.get('fim', fim_def)
    servico  = request.args.get('servico', '')
    status   = request.args.get('status', '')
    dados    = desp_rel_producao(data_ini, data_fim, servico or None, status or None)
    return desp_render('relatorio/producao.html',
                       dados=dados, data_ini=data_ini, data_fim=data_fim,
                       servico=servico, status=status,
                       servicos=DESP_SERVICOS, servicos_grupos=DESP_SERVICOS_GRUPOS)


@app.route('/despachante/relatorio/csv')
@_desp_login_required
def desp_relatorio_csv():
    """Export CSV do relatório de produção."""
    hoje    = date.today()
    data_ini = request.args.get('ini', hoje.replace(day=1).strftime('%Y-%m-%d'))
    data_fim = request.args.get('fim', hoje.strftime('%Y-%m-%d'))
    servico  = request.args.get('servico', '')
    status   = request.args.get('status', '')
    dados    = desp_rel_producao(data_ini, data_fim, servico or None, status or None)
    out = io.StringIO()
    w   = csv.writer(out)
    w.writerow(['numero', 'data', 'cliente', 'cpf', 'telefone', 'placa',
                'servico', 'status', 'exercicio', 'honorarios', 'custos',
                'total', 'pago', 'pendente', 'forma_pagamento'])
    for o in dados['ordens']:
        w.writerow([
            o.get('numero',''), (o.get('criado_em','') or '')[:10],
            o.get('cliente_nome',''), o.get('cpf',''), o.get('telefone',''),
            o.get('placa',''),
            DESP_SERVICOS.get(o.get('servico',''), o.get('servico','')),
            o.get('status',''), o.get('exercicio',''),
            o.get('honorarios',0), o.get('custos',0),
            o.get('total',0), o.get('pago',0), o.get('pendente',0),
            o.get('forma_pagamento',''),
        ])
    out.seek(0)
    fname = f'relatorio_{data_ini}_{data_fim}.csv'
    return out.getvalue(), 200, {
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': f'attachment; filename={fname}'
    }


# ── Protocolos RENAVAM ────────────────────────────────────────────────────────

@app.route('/despachante/protocolos')
@_desp_login_required
def desp_protocolos():
    busca       = request.args.get('busca', '').strip()
    filtro_lote = request.args.get('lote', '')
    filtro_usado= request.args.get('usado', '')
    usado_bool  = None
    if filtro_usado == '0': usado_bool = False
    elif filtro_usado == '1': usado_bool = True
    protocolos = desp_listar_protocolos(
        lote=filtro_lote or None,
        usado=usado_bool,
        busca=busca or None,
    )
    return desp_render('protocolos.html',
        protocolos=protocolos,
        stats=desp_stats_protocolos(),
        busca=busca,
        filtro_lote=filtro_lote,
        filtro_usado=filtro_usado,
        hoje=datetime.now(),
    )


@app.route('/despachante/protocolos/add', methods=['POST'])
@_desp_login_required
def desp_protocolos_add():
    f = request.form
    raw = f.get('protocolos', '')
    # Separa por linha, vírgula ou espaço
    tokens = [t.strip() for t in _re.split(r'[\n,\s]+', raw) if t.strip()]
    inseridos = desp_criar_lote_protocolos(
        protocolos=tokens,
        letra=f.get('letra', '').strip(),
        lote=f.get('lote', '').strip(),
        emitido_em=f.get('emitido_em', ''),
        observacao=f.get('observacao', '').strip(),
    )
    flash(f'✅ {inseridos} protocolo(s) cadastrado(s) com sucesso!', 'ok')
    return redirect(url_for('desp_protocolos'))


@app.route('/despachante/protocolos/<int:id>/delete', methods=['POST'])
@_desp_login_required
def desp_protocolo_delete(id):
    desp_deletar_protocolo(id)
    return jsonify({'ok': True})


@app.route('/despachante/nao-licenciados')
@_desp_login_required
def desp_nao_licenciados():
    exercicio = request.args.get('exercicio', datetime.now().year, type=int)
    final     = request.args.get('final', '').strip()
    mostrar   = request.args.get('mostrar', 'sem_os')
    veiculos  = desp_nao_lic(exercicio=exercicio,
                              final_placa=final or None,
                              mostrar=mostrar)
    stats     = desp_stats_nao_lic(exercicio=exercicio)
    exercicios = desp_listar_exercicios()
    if datetime.now().year not in exercicios:
        exercicios.insert(0, datetime.now().year)
    return desp_render('nao_licenciados.html',
        veiculos=veiculos, stats=stats,
        exercicio=exercicio, exercicios=exercicios,
        final=final, mostrar=mostrar,
        finais=sorted(DESP_FINAIS_PLACA.items(), key=lambda x: x[1]))


_desp_jobs: dict = {}           # job_id → {status, sent, failed, total, results}
_desp_init_done: set = set()    # tenant IDs já inicializados (evita init_db em todo request)


def _desp_new_job(total: int) -> str:
    """Cria um novo job de disparo e limpa jobs antigos (evita memory leak)."""
    concluidos = [k for k, v in list(_desp_jobs.items()) if v.get('status') == 'done']
    for k in concluidos[:-20]:   # mantém os 20 jobs concluídos mais recentes
        _desp_jobs.pop(k, None)
    job_id = uuid.uuid4().hex
    _desp_jobs[job_id] = {'status': 'running', 'sent': 0, 'failed': 0, 'total': total, 'results': []}
    return job_id


def _desp_dispatch_worker(job_id: str, contatos: list, mensagem_tpl: str,
                           evo_url: str, evo_key: str, evo_instance: str,
                           delay_s: int, vars_extra: dict):
    """Worker que roda em thread — envia WhatsApp para cada contato."""
    job = _desp_jobs[job_id]
    job['total'] = len(contatos)
    for c in contatos:
        if job.get('cancelado'):
            break
        tel = (c.get('telefone') or '').replace('(','').replace(')','').replace('-','').replace(' ','').replace('+','')
        if not tel:
            job['results'].append({'nome': c.get('cliente', c.get('nome','?')), 'status': 'sem_telefone'})
            job['failed'] += 1
            continue
        if not tel.startswith('55'):
            tel = '55' + tel
        nome_curto = (c.get('cliente') or c.get('nome') or 'Cliente').split()[0].title()
        try:
            msg = mensagem_tpl.format(
                nome=nome_curto,
                nome_completo=(c.get('cliente') or c.get('nome') or '').title(),
                placa=(c.get('placa') or '').upper(),
                **vars_extra,
            )
        except KeyError as e:
            job['results'].append({'nome': nome_curto, 'tel': tel, 'status': 'erro', 'detalhe': f'Variável inválida: {e}'})
            job['failed'] += 1
            continue
        try:
            resp = requests.post(
                f"{evo_url}/message/sendText/{evo_instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'number': tel, 'text': msg}, timeout=12
            )
            ok = resp.status_code in (200, 201)
            job['results'].append({'nome': nome_curto, 'tel': tel,
                                   'status': 'ok' if ok else 'erro',
                                   'detalhe': '' if ok else resp.text[:120]})
            if ok:
                job['sent'] += 1
            else:
                job['failed'] += 1
        except Exception as e:
            job['results'].append({'nome': nome_curto, 'tel': tel, 'status': 'erro', 'detalhe': str(e)[:120]})
            job['failed'] += 1
        time.sleep(delay_s)
    job['status'] = 'done'


@app.route('/despachante/dispatch-status/<job_id>')
@_desp_login_required
def desp_dispatch_status(job_id):
    job = _desp_jobs.get(job_id)
    if not job:
        return jsonify({'erro': 'Job não encontrado'}), 404
    return jsonify(job)


@app.route('/despachante/nao-licenciados/disparar', methods=['POST'])
@_desp_login_required
def desp_nao_lic_disparar():
    """Dispara WhatsApp para veículos sem licenciamento — roda em background."""
    data         = request.get_json(silent=True) or {}
    exercicio    = int(data.get('exercicio', datetime.now().year))
    final        = data.get('final', '')
    mostrar      = data.get('mostrar', 'sem_os')
    mensagem_tpl = data.get('mensagem', '').strip()
    delay_s      = max(1, min(30, int(data.get('delay', 4))))
    if not mensagem_tpl:
        return jsonify({'erro': 'Mensagem não pode estar vazia'}), 400
    ok_plano, msg_plano = _desp_check_limit('whatsapp')
    if not ok_plano:
        return jsonify({'erro': msg_plano}), 403
    evo_url, evo_key, evo_instance = _desp_get_evo_config()
    if not evo_url or not evo_key or not evo_instance:
        return jsonify({'erro': 'WhatsApp não configurado. Configure em ⚙️ Configurações.'}), 400
    contatos = desp_nao_lic(exercicio=exercicio, final_placa=final or None, mostrar=mostrar)
    vars_extra = dict(
        exercicio=exercicio,
        mes='',
        marca='', modelo='',
        despachante=_desp_get_config()['nome'].title(),
        whatsapp=_desp_get_config()['whatsapp_fmt'],
        cidade=_desp_get_config()['cidade'],
    )
    job_id = _desp_new_job(len(contatos))
    threading.Thread(target=_desp_dispatch_worker, daemon=True,
                     args=(job_id, contatos, mensagem_tpl, evo_url, evo_key, evo_instance, delay_s, vars_extra)).start()
    return jsonify({'job_id': job_id, 'total': len(contatos)})


@app.route('/despachante/configuracoes', methods=['GET', 'POST'])
@_desp_login_required
def desp_configuracoes():
    """Perfil e configurações do escritório — editável apenas para tenants SaaS."""
    from desp_db import get_config as _gc, set_config as _sc
    is_saas = bool(session.get('desp_saas_user_id'))
    sucesso = False
    if request.method == 'POST' and is_saas:
        campos = ['desp_nome','desp_cpf','desp_cnpj','desp_cred',
                  'desp_cidade','desp_citran','desp_wpp','desp_wpp_fmt',
                  'desp_evo_url','desp_evo_key','desp_evo_instance',
                  'desp_backup_email']
        for c in campos:
            val = request.form.get(c, '').strip()
            if val:
                _sc(c, val)
        sucesso = True
    cfg = _desp_get_config() if is_saas else DESP_CONFIG
    # Para tenants, pega também configs extras do banco
    evo_url      = _gc('desp_evo_url')      if is_saas else os.environ.get('EVO_URL','')
    evo_key      = _gc('desp_evo_key')      if is_saas else os.environ.get('EVO_KEY','')
    evo_instance = _gc('desp_evo_instance') if is_saas else os.environ.get('EVO_INSTANCE','')
    backup_email = _gc('desp_backup_email') if is_saas else os.environ.get('BACKUP_EMAIL','')
    return desp_render('configuracoes.html', cfg=cfg, is_saas=is_saas,
                       evo_url=evo_url, evo_key=evo_key, evo_instance=evo_instance,
                       backup_email=backup_email, sucesso=sucesso)


@app.route('/despachante/mensagens', methods=['GET', 'POST'])
@_desp_login_required
def desp_mensagens():
    """Templates de mensagem WhatsApp — visualizar e editar."""
    if request.method == 'POST':
        tpls = desp_get_tpls()
        for chave in tpls:
            novo_texto = request.form.get(f'tpl_{chave}', '').strip()
            if novo_texto:
                tpls[chave]['texto'] = novo_texto
        desp_set_tpls(tpls)
        from flask import flash
        flash('Templates salvos com sucesso! 💬', 'ok')
        return redirect(url_for('desp_mensagens'))
    tpls = desp_get_tpls()
    return desp_render('mensagens.html', tpls=tpls)


@app.route('/despachante/api/mensagem/<chave>')
@_desp_login_required
def desp_api_mensagem(chave):
    """Retorna o texto de um template para uso inline (ex: botão WhatsApp na OS)."""
    return jsonify({'chave': chave, 'texto': desp_get_tpl(chave)})


@app.route('/despachante/backup')
@_desp_login_required
def desp_backup():
    """Download direto do ZIP de backup."""
    from flask import send_file
    zdata = _gerar_backup_zip()
    fname = f'lessmann_backup_{date.today()}.zip'
    return send_file(io.BytesIO(zdata), mimetype='application/zip',
                     as_attachment=True, download_name=fname)


@app.route('/despachante/backup/email', methods=['POST'])
@_desp_login_required
def desp_backup_email():
    """Dispara backup por e-mail imediatamente (ação manual)."""
    dest     = _desp_backup_dest()
    db_path  = getattr(__import__('flask').g, 'desp_db_path', None)
    def _run():
        _enviar_backup_email(dest=dest, db_path=db_path)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True, 'msg': f'Backup sendo enviado para {dest}'})


@app.route('/despachante/manifest.json')
def desp_pwa_manifest():
    """PWA manifest para instalação como app."""
    manifest = {
        "name": "Lessmann Despachante",
        "short_name": "Lessmann",
        "description": "Sistema de gestão de OS para despachante documentalista",
        "start_url": "/despachante/",
        "display": "standalone",
        "background_color": "#111111",
        "theme_color": "#6366F1",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "/static/desp/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/desp/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ],
        "categories": ["business", "productivity"],
        "lang": "pt-BR"
    }
    from flask import Response
    return Response(_json.dumps(manifest, ensure_ascii=False),
                    mimetype='application/manifest+json')


@app.route('/despachante/os/<int:id>/editar', methods=['POST'])
@_desp_login_required
def desp_editar_os(id):
    f = request.form
    desp_atualizar_os(id, {
        'servico': f.get('servico', 'outros'),
        'honorarios': float(f.get('honorarios') or 0),
        'custos': float(f.get('custos') or 0),
        'pago': float(f.get('pago') or 0),
        'forma_pagamento': f.get('forma_pagamento', ''),
        'observacoes': f.get('observacoes', ''),
        'corpo_req': f.get('corpo_req', ''),
        'exercicio': int(f.get('exercicio') or datetime.now().year),
        'situacao_pag': f.get('situacao_pag', ''),
    })
    return redirect(url_for('desp_detalhe_os', id=id))


# ── Lista final de placa ──────────────────────────────────────────────────────
@app.route('/despachante/lista')
@_desp_login_required
def desp_lista_placa():
    final     = request.args.get('final', '5')
    exercicio = request.args.get('exercicio', datetime.now().year, type=int)
    situacao  = request.args.get('situacao', '')
    ordens    = desp_lista_final_placa(final, exercicio, situacao or None)
    exercicios = desp_listar_exercicios()
    if datetime.now().year not in exercicios:
        exercicios.insert(0, datetime.now().year)
    pendentes  = sum(1 for o in ordens if o['status'] not in ('concluida','cancelada'))
    concluidos = sum(1 for o in ordens if o['status'] == 'concluida')
    mes_placa  = DESP_MESES[DESP_FINAIS_PLACA.get(final, 0)]
    return desp_render('lista_placa.html',
        ordens=ordens, final=final, exercicio=exercicio,
        situacao=situacao, exercicios=exercicios,
        pendentes=pendentes, concluidos=concluidos,
        mes_placa=mes_placa, total=len(ordens))


@app.route('/despachante/lista/csv')
@_desp_login_required
def desp_lista_csv():
    from flask import Response
    final     = request.args.get('final', '5')
    exercicio = request.args.get('exercicio', datetime.now().year, type=int)
    situacao  = request.args.get('situacao', '')
    ordens    = desp_lista_final_placa(final, exercicio, situacao or None)
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(['nome','cpf','renavam','placa','exercicio','telefone','situacao','os_id'])
    for o in ordens:
        tel = (o.get('telefone') or '').replace('(','').replace(')','').replace('-','').replace(' ','')
        if tel and not tel.startswith('55'): tel = '55' + tel
        sit = o.get('situacao_pag') or ('CONCLUÍDO' if o['status']=='concluida' else 'AGUARDANDO PAGAMENTO')
        w.writerow([o.get('cliente',''), o.get('cpf',''), o.get('renavam',''),
                    o.get('placa',''), o.get('exercicio',''), tel, sit, o.get('os_id','')])
    buf.seek(0)
    return Response(buf.getvalue(), mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename=final{final}_ex{exercicio}.csv'})


@app.route('/despachante/lista/situacao/<int:os_id>', methods=['POST'])
@_desp_login_required
def desp_set_situacao(os_id):
    data = request.get_json(silent=True) or {}
    desp_atualizar_situacao(os_id, data.get('situacao_pag', ''))
    return jsonify({'ok': True})


@app.route('/despachante/lista/disparar', methods=['POST'])
@_desp_login_required
def desp_lista_disparar():
    """Dispara WhatsApp para lista de final de placa — roda em background."""
    data         = request.get_json(silent=True) or {}
    final        = data.get('final', '5')
    exercicio    = data.get('exercicio', datetime.now().year)
    situacao     = data.get('situacao', 'pendente')
    mensagem_tpl = data.get('mensagem', '').strip()
    delay_s      = max(1, min(30, int(data.get('delay', 4))))
    if not mensagem_tpl:
        return jsonify({'erro': 'Mensagem não pode estar vazia'}), 400
    ok_plano, msg_plano = _desp_check_limit('whatsapp')
    if not ok_plano:
        return jsonify({'erro': msg_plano}), 403
    evo_url, evo_key, evo_instance = _desp_get_evo_config()
    if not evo_url or not evo_key or not evo_instance:
        return jsonify({'erro': 'WhatsApp não configurado. Configure em ⚙️ Configurações.'}), 400
    contatos = desp_lista_final_placa(final, int(exercicio), situacao or None)
    mes_str  = DESP_MESES[DESP_FINAIS_PLACA.get(final, 0)]
    vars_extra = dict(
        exercicio=exercicio,
        mes=mes_str,
        despachante=_desp_get_config()['nome'].title(),
        whatsapp=_desp_get_config()['whatsapp_fmt'],
        cidade=_desp_get_config()['cidade'],
    )
    job_id = _desp_new_job(len(contatos))
    threading.Thread(target=_desp_dispatch_worker, daemon=True,
                     args=(job_id, contatos, mensagem_tpl, evo_url, evo_key, evo_instance, delay_s, vars_extra)).start()
    return jsonify({'job_id': job_id, 'total': len(contatos)})


# ── Print protocolo ───────────────────────────────────────────────────────────
@app.route('/despachante/print/<int:os_id>')
@_desp_login_required
def desp_print_protocolo(os_id):
    os_ = desp_get_os(os_id)
    if not os_: abort(404)
    finalidade  = DESP_SERVICOS.get(os_['servico'], os_['servico'])
    docs_needed = DESP_DOCS_POR_SERVICO.get(os_['servico'], DESP_DOCS_PADRAO)
    return render_template('despachante/print/protocolo.html',
        os=os_, finalidade=finalidade, hoje=datetime.now(), desp=DESP_CONFIG,
        docs_needed=docs_needed, servicos=DESP_SERVICOS)


# ── Print procuração ──────────────────────────────────────────────────────────
@app.route('/despachante/os/<int:os_id>/procuracao')
@_desp_login_required
def desp_print_procuracao(os_id):
    os_ = desp_get_os(os_id)
    if not os_: abort(404)
    finalidade = DESP_SERVICOS.get(os_['servico'], os_['servico'])
    return render_template('despachante/print/procuracao.html',
        os=os_, finalidade=finalidade, hoje=datetime.now(), desp=DESP_CONFIG,
        servicos=DESP_SERVICOS)


# ── Print requerimento ────────────────────────────────────────────────────────
@app.route('/despachante/os/<int:os_id>/requerimento')
@_desp_login_required
def desp_print_requerimento(os_id):
    os_ = desp_get_os(os_id)
    if not os_: abort(404)
    finalidade  = DESP_SERVICOS.get(os_['servico'], os_['servico'])
    docs_needed = DESP_DOCS_POR_SERVICO.get(os_['servico'], DESP_DOCS_PADRAO)
    return render_template('despachante/print/requerimento.html',
        os=os_, finalidade=finalidade, hoje=datetime.now(), desp=DESP_CONFIG,
        docs_needed=docs_needed, servicos=DESP_SERVICOS)


# ── Clientes ─────────────────────────────────────────────────────────────────
@app.route('/despachante/clientes')
@_desp_login_required
def desp_clientes():
    busca  = request.args.get('q', '').strip()
    page   = max(1, int(request.args.get('page', 1)))
    limit  = 40
    offset = (page - 1) * limit
    clientes = desp_listar_clientes(busca or None, limit=limit, offset=offset)
    total    = desp_contar_clientes(busca or None)
    return desp_render('clientes/lista.html',
                       clientes=clientes, busca=busca,
                       page=page, limit=limit, total=total,
                       servicos=DESP_SERVICOS)


@app.route('/despachante/clientes/<int:id>')
@_desp_login_required
def desp_detalhe_cliente(id):
    cliente = desp_get_cliente_detalhe(id)
    if not cliente:
        return "Cliente não encontrado", 404
    # Resumo financeiro do cliente
    conn = get_desp_conn()
    fin = dict(conn.execute("""
        SELECT COALESCE(SUM(honorarios+custos),0) AS faturado,
               COALESCE(SUM(pago),0) AS recebido,
               COALESCE(SUM(CASE WHEN status!='cancelada' AND (honorarios+custos-pago)>0.01
                              THEN honorarios+custos-pago ELSE 0 END),0) AS pendente,
               COUNT(*) AS total_os,
               COUNT(CASE WHEN status='concluida' THEN 1 END) AS concluidas
        FROM ordens_servico WHERE cliente_id=? AND status!='cancelada'
    """, (id,)).fetchone())
    conn.close()
    return desp_render('clientes/detalhe.html',
                       cliente=cliente, fin=fin,
                       servicos=DESP_SERVICOS,
                       status_labels=DESP_STATUS_LABELS)


@app.route('/despachante/clientes/<int:id>/editar', methods=['POST'])
@_desp_login_required
def desp_editar_cliente(id):
    f = request.form
    dados = {
        'nome':       f.get('nome', '').strip(),
        'tipo':       f.get('tipo', 'PF'),
        'cpf':        f.get('cpf', '').strip(),
        'cnpj':       f.get('cnpj', '').strip(),
        'rg':         f.get('rg', '').strip(),
        'nascimento': f.get('nascimento', '').strip(),
        'nome_mae':   f.get('nome_mae', '').strip(),
        'telefone':   f.get('telefone', '').strip(),
        'email':      f.get('email', '').strip(),
        'cep':        f.get('cep', '').strip(),
        'logradouro': f.get('logradouro', '').strip(),
        'numero':     f.get('numero', '').strip(),
        'complemento':f.get('complemento', '').strip(),
        'bairro':     f.get('bairro', '').strip(),
        'cidade':     f.get('cidade', '').strip(),
        'uf':         f.get('uf', 'SC').strip(),
    }
    dados = {k: v for k, v in dados.items() if v}  # remove campos vazios
    if dados.get('nome'):
        desp_atualizar_cliente(id, dados)
    return redirect(url_for('desp_detalhe_cliente', id=id))


@app.route('/despachante/clientes/importar', methods=['GET'])
@_desp_login_required
def desp_importar_get():
    return desp_render('clientes/importar.html')


def _parse_bludata_pdf(texto: str) -> list:
    """
    Parser direto do formato de relatório Bludata (SGDW).
    Cada veículo/cliente ocupa 4 linhas com campos separados por '......:' ou '..:'.

    Exemplo:
      Placa......: ABC1D23 Marca...: VW/GOL Exer.:2026 Lic.:
      Ano Fab/Mod: 2020/2021 Prop. Atual..: FULANO DA SILVA CPF/CNPJ:12345678901
      Fone Res......: 333-1111 Fone Com..: Celular: (47)99999-1234 Email:teste@x.com
      Origem..........: CLIENTE Nasc.:01/01/1980 CNH:123 Venc. CNH:
    """
    import re as _re
    registros = []
    linhas = texto.splitlines()

    i = 0
    while i < len(linhas):
        linha = linhas[i].strip()

        # Detecta início de um registro: linha com "Placa......:"
        if not _re.search(r'Placa\.+:', linha):
            i += 1
            continue

        # Junta as próximas linhas para capturar o bloco completo
        bloco = ' '.join(linhas[i:i+4])

        # ── Placa ──────────────────────────────────────────────────────────
        m_placa = _re.search(r'Placa\.+:\s*(\S+)', bloco)
        placa = m_placa.group(1).strip() if m_placa else ''
        # Ignora linha de cabeçalho/rodapé (placa vazia ou texto de header)
        if not placa or placa.lower() in ('placa', 'n/a', ''):
            i += 1
            continue

        # ── Nome (Prop. Atual.) ────────────────────────────────────────────
        m_nome = _re.search(r'Prop\.\s*Atual\.+:\s*(.+?)\s+CPF/CNPJ', bloco)
        nome = m_nome.group(1).strip() if m_nome else ''

        # ── CPF/CNPJ — só aceita dígitos ──────────────────────────────────
        m_cpf = _re.search(r'CPF/CNPJ\s*:\s*(\d[\d.\-/]*)', bloco)
        cpf = _re.sub(r'[.\-/]', '', m_cpf.group(1)).strip() if m_cpf else ''
        # Valida tamanho mínimo
        if len(cpf) < 11:
            cpf = ''

        # ── Telefone: prioridade Celular > Fone Com > Fone Res ────────────
        m_cel = _re.search(r'Celular\s*:\s*([\d\s\(\)\-\+]+?)(?:\s+Email|\s+Origem|$)', bloco)
        m_com = _re.search(r'Fone\s+Com\.+:\s*([\d\s\(\)\-]+?)(?:\s+Celular|$)', bloco)
        m_res = _re.search(r'Fone\s+Res\.+:\s*([\d\s\(\)\-]+?)(?:\s+Fone\s+Com|$)', bloco)

        def _limpar_tel(m):
            if not m: return ''
            t = _re.sub(r'[^\d]', '', m.group(1))  # só dígitos
            # Rejeita zeros, muito curto ou claramente inválido
            if len(t) < 8 or t == '0' * len(t):
                return ''
            return m.group(1).strip()

        telefone = _limpar_tel(m_cel) or _limpar_tel(m_com) or _limpar_tel(m_res)

        # ── E-mail ─────────────────────────────────────────────────────────
        m_email = _re.search(r'Email\s*:\s*(\S+@\S+)', bloco)
        email = m_email.group(1).strip() if m_email else ''

        # ── Marca do veículo ───────────────────────────────────────────────
        m_marca = _re.search(r'Marca\.+:\s*(.*?)\s+Exer\.', bloco)
        marca = m_marca.group(1).strip() if m_marca else ''
        # Descarta se ficou vazio ou contém lixo
        if not marca or 'Exer' in marca or 'Lic' in marca:
            marca = ''

        if nome:
            registros.append({
                'nome':     nome,
                'cpf':      cpf,
                'telefone': telefone,
                'placa':    placa,
                'email':    email,
                'cidade':   '',
                'marca':    marca,
            })

        i += 4  # avança o bloco inteiro

    return registros


@app.route('/despachante/clientes/importar/ocr', methods=['POST'])
@_desp_login_required
def desp_importar_ocr():
    """Recebe imagem ou PDF do Bludata, extrai lista de clientes.

    PDFs Bludata (SGDW): parser Python direto — sem IA, 100% confiável.
    Imagens: envia ao modelo de visão (llama-4-scout) para OCR via IA.
    """
    import base64, mimetypes, io as _io, re as _re3, json as _json3

    groq_key = os.environ.get('GROQ_API_KEY', '')

    f = request.files.get('arquivo')
    if not f:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400

    dados_bytes = f.read()
    filename    = (f.filename or '').lower()
    mime        = f.mimetype or mimetypes.guess_type(f.filename or '')[0] or 'image/jpeg'
    is_pdf      = filename.endswith('.pdf') or 'pdf' in mime.lower()

    try:
        if is_pdf:
            # ── PDF Bludata: parser Python direto ────────────────────────────
            try:
                import pdfplumber
            except ImportError:
                return jsonify({'erro': 'pdfplumber não instalado — contate o suporte'}), 500

            texto_pdf = ''
            with pdfplumber.open(_io.BytesIO(dados_bytes)) as pdf:
                for page in pdf.pages:
                    t = page.extract_text() or ''
                    if t.strip():
                        texto_pdf += t + '\n'

            if not texto_pdf.strip():
                return jsonify({
                    'erro': 'Não foi possível extrair texto do PDF. '
                            'Tente exportar como imagem (print da tela) e importar novamente.'
                }), 422

            registros = _parse_bludata_pdf(texto_pdf)

            if not registros:
                return jsonify({
                    'erro': 'Nenhum cliente encontrado no PDF. '
                            'Verifique se é um relatório do Bludata (SGDW) com o campo "Placa" visível.'
                }), 422

            return jsonify({'ok': True, 'registros': registros, 'total': len(registros)})

        else:
            # ── Imagem: OCR via IA (modelo de visão) ─────────────────────────
            if not groq_key:
                return jsonify({'erro': 'GROQ_API_KEY não configurada'}), 500

            PROMPT = (
                'Analise esta imagem de relatório/listagem de sistema de despachante (Bludata ou similar).\n'
                'Extraia TODOS os clientes/veículos que aparecerem e retorne um array JSON:\n'
                '[{"nome":"","cpf":"","telefone":"","placa":"","email":"","cidade":""}]\n'
                'Preencha apenas os campos visíveis. Deixe "" o que não aparecer.\n'
                'RETORNE SOMENTE O ARRAY JSON, sem texto adicional, sem markdown.'
            )

            img_b64 = base64.b64encode(dados_bytes).decode()
            resp = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
                json={
                    'model': 'meta-llama/llama-4-scout-17b-16e-instruct',
                    'messages': [{'role': 'user', 'content': [
                        {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}},
                        {'type': 'text', 'text': PROMPT},
                    ]}],
                    'max_tokens': 4096,
                    'temperature': 0.1,
                },
                timeout=90,
            )
            resp.raise_for_status()
            texto = resp.json()['choices'][0]['message']['content'].strip()

            match = _re3.search(r'\[[\s\S]*\]', texto)
            if not match:
                return jsonify({'erro': 'IA não retornou JSON válido — tente novamente'}), 422

            registros = _json3.loads(match.group())
            registros = [r for r in registros if r.get('nome')]
            return jsonify({'ok': True, 'registros': registros, 'total': len(registros)})

    except Exception as e:
        log.error(f'importar OCR error: {e}')
        return jsonify({'erro': str(e)}), 500


@app.route('/despachante/clientes/importar/salvar', methods=['POST'])
@_desp_login_required
def desp_importar_salvar():
    """Recebe lista confirmada de registros e insere no banco."""
    data = request.get_json(silent=True) or {}
    registros = data.get('registros', [])
    if not registros:
        return jsonify({'erro': 'Nenhum registro'}), 400
    resultado = desp_importar_bulk(registros)
    return jsonify({'ok': True, **resultado})


# ── API Débitos DETRAN ────────────────────────────────────────────────────────

@app.route('/despachante/api/os/<int:os_id>/debitos', methods=['GET'])
@_desp_login_required
def desp_api_debitos_get(os_id):
    """Lista os débitos de uma O.S."""
    debitos = desp_listar_debitos(os_id)
    total   = desp_total_debitos(os_id)
    return jsonify({'ok': True, 'debitos': debitos, 'total': total})


@app.route('/despachante/api/os/<int:os_id>/debitos/salvar', methods=['POST'])
@_desp_login_required
def desp_api_debitos_salvar(os_id):
    """Salva lista de débitos de uma O.S. (substitui os anteriores)."""
    os_row = desp_get_os(os_id)
    if not os_row:
        return jsonify({'erro': 'O.S. não encontrada'}), 404
    data    = request.get_json(silent=True) or {}
    debitos = data.get('debitos', [])
    resultado = desp_salvar_debitos(os_id, os_row.get('veiculo_id'), debitos)
    return jsonify({'ok': True, **resultado})


@app.route('/despachante/api/debitos/<int:debito_id>', methods=['DELETE'])
@_desp_login_required
def desp_api_debito_delete(debito_id):
    """Remove um débito pelo ID."""
    desp_deletar_debito(debito_id)
    return jsonify({'ok': True})


@app.route('/despachante/api/ocr/debitos', methods=['POST'])
@_desp_login_required
def desp_api_ocr_debitos():
    """
    Recebe print do DETRANET (imagem), extrai lista de débitos via IA.
    Retorna JSON com array de débitos para preview antes de salvar.
    """
    import base64, mimetypes, re as _re4, json as _json4

    groq_key = os.environ.get('GROQ_API_KEY', '')
    if not groq_key:
        return jsonify({'erro': 'GROQ_API_KEY não configurada'}), 500

    f = request.files.get('arquivo')
    if not f:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400

    dados_bytes = f.read()
    mime        = f.mimetype or mimetypes.guess_type(f.filename or '')[0] or 'image/jpeg'

    PROMPT_DEBITOS = (
        'Analise esta imagem do sistema DETRANET (DETRAN-SC) mostrando a Listagem de Débitos de um veículo.\n'
        'Extraia TODOS os itens da tabela de débitos e retorne um array JSON:\n'
        '[{"tipo":"","descricao":"","numero_detran":"","valor_nominal":"","valor_multa":"","valor_juros":"","valor":"","vencimento":"","situacao":"","auto_infracao":""}]\n'
        'Instruções por campo:\n'
        '- tipo: classifique como IPVA / Multa / Licenciamento / DPVAT / Taxa DETRAN / Outros\n'
        '- descricao: texto da coluna "Classe" exatamente como aparece (ex: "Licenciamento Anual 2026", "IPVA (Cota Unica) 2026")\n'
        '- numero_detran: número da coluna "Número DetranNET" (ex: "662.466.509")\n'
        '- valor_nominal: valor da coluna "Valor Nominal(R$)"\n'
        '- valor_multa: valor da coluna "Multa(R$)"\n'
        '- valor_juros: valor da coluna "Juros(R$)"\n'
        '- valor: valor da coluna "Valor Atual(R$)" — este é o valor a pagar\n'
        '- vencimento: data de vencimento (formato dd/mm/aaaa)\n'
        '- situacao: sempre "em aberto" a menos que claramente marcado como pago\n'
        '- auto_infracao: se for multa, o código da coluna "Classe" (ex: "UF:DN-000300-S046548067-7455"); senão ""\n'
        'Para multas: se a "Classe" contiver código de auto (ex: "UF:DN-...", "JARAGUA-..."), classifique tipo como "Multa".\n'
        'RETORNE SOMENTE O ARRAY JSON, sem texto adicional, sem markdown.'
    )

    try:
        img_b64 = base64.b64encode(dados_bytes).decode()
        resp = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
            json={
                'model': 'meta-llama/llama-4-scout-17b-16e-instruct',
                'messages': [{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}},
                    {'type': 'text', 'text': PROMPT_DEBITOS},
                ]}],
                'max_tokens': 2048,
                'temperature': 0.1,
            },
            timeout=60,
        )
        resp.raise_for_status()
        texto = resp.json()['choices'][0]['message']['content'].strip()

        match = _re4.search(r'\[[\s\S]*\]', texto)
        if not match:
            return jsonify({'erro': 'IA não identificou débitos na imagem — verifique se é um print do DETRANET'}), 422

        debitos = _json4.loads(match.group())
        debitos = [d for d in debitos if d.get('tipo') or d.get('descricao')]
        return jsonify({'ok': True, 'debitos': debitos, 'total': len(debitos)})

    except Exception as e:
        log.error(f'OCR débitos error: {e}')
        return jsonify({'erro': str(e)}), 500


# ── API busca ─────────────────────────────────────────────────────────────────
@app.route('/despachante/api/busca/placa/<placa>')
@_desp_login_required
def desp_api_placa(placa):
    v = desp_buscar_placa(placa)
    if not v: return jsonify({'encontrado': False})
    c = desp_get_cliente(v['proprietario_id']) if v.get('proprietario_id') else None
    return jsonify({'encontrado': True, 'veiculo': v, 'cliente': c})


@app.route('/despachante/api/busca/placa/<placa>/historico')
@_desp_login_required
def desp_api_placa_historico(placa):
    """Retorna as últimas OS abertas/concluídas para uma placa — usado para alertar duplicata."""
    conn = get_desp_conn()
    ano  = datetime.now().strftime("%Y")
    rows = conn.execute("""
        SELECT os.id, os.numero, os.servico, os.status, os.criado_em,
               os.honorarios, os.pago, os.exercicio,
               c.nome AS cliente_nome
        FROM veiculos v
        JOIN ordens_servico os ON os.veiculo_id = v.id
        LEFT JOIN clientes c ON c.id = os.cliente_id
        WHERE replace(v.placa,'-','') = ? AND os.status != 'cancelada'
        ORDER BY os.id DESC LIMIT 5
    """, (placa.upper().replace('-',''),)).fetchall()
    conn.close()
    return jsonify({'historico': [dict(r) for r in rows]})


@app.route('/despachante/api/busca/cpf/<cpf>')
@_desp_login_required
def desp_api_cpf(cpf):
    c = desp_buscar_cpf(cpf)
    if not c: return jsonify({'encontrado': False})
    return jsonify({'encontrado': True, 'cliente': c})


# ── API OCR (Ctrl+V → preenche formulário) ───────────────────────────────────
@app.route('/despachante/api/ocr', methods=['POST'])
@_desp_login_required
def desp_api_ocr():
    import re as _re2, json as _json2
    data    = request.get_json(silent=True) or {}
    img_b64 = (data.get('imagem') or '').strip()
    mime    = data.get('mime', 'image/png')
    if not img_b64: return jsonify({'erro': 'Nenhuma imagem recebida'}), 400
    groq_key = os.environ.get('GROQ_API_KEY','')
    if not groq_key: return jsonify({'erro': 'GROQ_API_KEY não configurada'}), 500
    prompt = '''Analise esta imagem de documento ou tela de sistema de despachante/DETRAN.
Extraia TODOS os dados visíveis de veículo, do proprietário/cliente e de débitos/taxas.
Retorne APENAS um objeto JSON válido com os campos (use null para não encontrados):
{"placa":null,"renavam":null,"chassi":null,"marca":null,"modelo":null,"ano_fab":null,
"ano_mod":null,"cor":null,"especie":null,"categoria":null,"combustivel":null,"num_crv":null,
"nome":null,"cpf":null,"cnpj":null,"rg":null,"nascimento":null,"nome_mae":null,
"telefone":null,"email":null,"cep":null,"logradouro":null,"numero":null,
"complemento":null,"bairro":null,"cidade":null,"uf":null,
"total_debitos":null,"ipva":null,"licenciamento":null,"multas":null,"dpvat":null}
Instruções para os campos de débitos (se houver uma "Listagem de Débitos" ou "Total dos Débitos" visível):
- total_debitos: valor total a pagar (campo "Total dos Débitos" ou soma de todos os débitos), como número decimal
- ipva: soma dos valores de IPVA, como número decimal
- licenciamento: soma dos valores de Licenciamento/Taxa Detran, como número decimal
- multas: soma dos valores de Multas, como número decimal
- dpvat: valor do DPVAT, como número decimal
IMPORTANTE: Retorne SOMENTE o JSON, nada mais.'''
    try:
        resp = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
            json={
                'model': 'meta-llama/llama-4-scout-17b-16e-instruct',
                'messages': [{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}},
                    {'type': 'text', 'text': prompt},
                ]}],
                'max_tokens': 1024,
                'temperature': 0.1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        texto = resp.json()['choices'][0]['message']['content'].strip()
        match = _re2.search(r'\{[\s\S]*\}', texto)
        if not match: return jsonify({'erro': 'IA não retornou JSON válido'}), 422
        dados = _json2.loads(match.group())
        dados = {k: v for k, v in dados.items() if v is not None and v != ''}
        return jsonify({'ok': True, 'dados': dados, 'campos': len(dados)})
    except Exception as e:
        log.error(f'OCR despachante error: {e}')
        return jsonify({'erro': str(e)}), 500


@app.route('/despachante/chat')
@_desp_login_required
def desp_chat():
    # Nunca chama db_stats() direto — pode disparar init do ChromaDB e causar OOM
    # Stats são carregados via AJAX pelo painel RAG (não na página do chat)
    stats_rag = {'chunks': 0, 'documentos': 0, 'arquivos': []}
    if _rag_ok:
        try:
            # Só faz a contagem se a collection JÁ estiver inicializada (sem forçar init)
            if desp_rag._collection is not None:
                stats_rag = desp_rag.db_stats()
        except Exception as e:
            log.warning(f'desp_rag.db_stats falhou: {e}')
    return desp_render('chat.html', rag_stats=stats_rag, rag_ok=_rag_ok)

@app.route('/despachante/api/chat', methods=['POST'])
@_desp_login_required
def desp_api_chat():
    data = request.get_json(silent=True) or {}
    msgs = data.get('messages', [])
    if not msgs:
        return jsonify({'erro': 'Sem mensagem'}), 400

    # Tenta usar RAG primeiro
    if _rag_ok:
        try:
            pergunta = msgs[-1].get('content', '') if msgs else ''
            historico = [{'role': m['role'], 'content': m['content']} for m in msgs[:-1]]
            resultado = desp_rag.chat(pergunta, historico)
            return jsonify({
                'ok':      True,
                'resposta': resultado['resposta'],
                'fontes':   resultado.get('fontes', []),
                'chunks':   resultado.get('chunks', 0),
            })
        except Exception as e:
            log.warning(f'desp_rag.chat falhou, fallback direto: {e}')

    # Fallback: Groq direto (sem RAG)
    groq_key = os.environ.get('GROQ_API_KEY', '')
    if not groq_key:
        return jsonify({'erro': 'GROQ_API_KEY não configurada'}), 500
    system_prompt = (
        "Você é o Assistente IA do Despachante Lessmann, especializado em legislação de trânsito brasileira, "
        "transferências de veículos, licenciamento, DETRAN-SC, IPVA, multas, recursos e serviços de despachante. "
        "Responda sempre em português brasileiro, de forma clara, objetiva e profissional. "
        "Quando não souber algo com certeza, diga que não tem essa informação e sugira consultar o DETRAN-SC. "
        "Nunca invente valores de taxas — oriente o cliente a consultar o site oficial."
    )
    try:
        resp = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [{'role': 'system', 'content': system_prompt}] + msgs,
                'max_tokens': 1024,
                'temperature': 0.7,
            },
            timeout=30,
        )
        resp.raise_for_status()
        reply = resp.json()['choices'][0]['message']['content'].strip()
        return jsonify({'ok': True, 'resposta': reply, 'fontes': [], 'chunks': 0})
    except Exception as e:
        log.error(f'desp_chat error: {e}')
        return jsonify({'erro': str(e)}), 500


# ── RAG Admin: Base de Conhecimento ─────────────────────────────────────────

@app.route('/despachante/rag')
@_desp_login_required
def desp_rag_admin():
    if not _rag_ok:
        return desp_render('rag_admin.html', rag_ok=False, stats={}, arquivos=[],
                           internos=[], externos=[])
    try:
        stats = desp_rag.db_stats()
    except Exception as _e:
        log.warning(f'desp_rag_admin db_stats falhou: {_e}')
        stats = {'chunks': 0, 'documentos': 0, 'arquivos': [], 'internos': [], 'externos': []}
    return desp_render('rag_admin.html', rag_ok=True, stats=stats,
                       arquivos=stats.get('arquivos', []),
                       internos=stats.get('internos', []),
                       externos=stats.get('externos', []))


@app.route('/despachante/rag/upload', methods=['POST'])
@_desp_login_required
def desp_rag_upload():
    if not _rag_ok:
        return jsonify({'erro': 'RAG não disponível'}), 500
    f = request.files.get('arquivo')
    if not f or not f.filename:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext == 'pdf':
        dest_dir = desp_rag.PDFS_DIR
    elif ext in ('doc', 'docx'):
        dest_dir = desp_rag.DOCS_DIR
    else:
        return jsonify({'erro': 'Formato não suportado. Use PDF, DOC ou DOCX'}), 400

    os.makedirs(dest_dir, exist_ok=True)
    safe_name = f.filename.replace('/', '_').replace('\\', '_')
    dest_path = os.path.join(dest_dir, safe_name)
    f.save(dest_path)

    try:
        if ext == 'pdf':
            salvos = desp_rag.ingest_pdf(dest_path)
        else:
            salvos = desp_rag.ingest_doc(dest_path)
        stats = desp_rag.db_stats()
        return jsonify({'ok': True, 'arquivo': safe_name, 'chunks': salvos,
                        'total_chunks': stats['chunks'], 'total_docs': stats['documentos']})
    except Exception as e:
        log.error(f'desp_rag_upload error: {e}')
        return jsonify({'erro': str(e)}), 500


@app.route('/despachante/rag/stats')
@_desp_login_required
def desp_rag_stats():
    if not _rag_ok:
        return jsonify({'chunks': 0, 'documentos': 0, 'arquivos': []})
    return jsonify(desp_rag.db_stats())


@app.route('/despachante/rag/delete', methods=['POST'])
@_desp_login_required
def desp_rag_delete():
    """Remove um documento da base vetorial e do disco."""
    if not _rag_ok:
        return jsonify({'erro': 'RAG não disponível'}), 500
    data = request.get_json(silent=True) or {}
    nome = data.get('arquivo', '').strip()
    if not nome:
        return jsonify({'erro': 'Nome do arquivo não informado'}), 400

    try:
        col = desp_rag.get_collection()
        if col:
            # remove todos os chunks deste arquivo
            results = col.get(where={'source': nome})
            ids_to_del = results.get('ids', [])
            if ids_to_del:
                col.delete(ids=ids_to_del)

        # remove arquivo físico
        for pasta in (desp_rag.PDFS_DIR, desp_rag.DOCS_DIR):
            caminho = os.path.join(pasta, nome)
            if os.path.exists(caminho):
                os.remove(caminho)
                break

        return jsonify({'ok': True, 'removidos': len(ids_to_del) if col else 0})
    except Exception as e:
        log.error(f'desp_rag_delete error: {e}')
        return jsonify({'erro': str(e)}), 500


@app.route('/despachante/rag/seed', methods=['POST'])
@_desp_login_required
def desp_rag_seed():
    """(Re)alimenta a base de conhecimento interna sobre DETRAN-SC / CTB."""
    if not _rag_ok:
        return jsonify({'erro': 'RAG não disponível'}), 500
    forcar = request.json.get('forcar', False) if request.is_json else False
    try:
        desp_rag.seed_conhecimento_base(forcar=bool(forcar))
        stats = desp_rag.db_stats()
        return jsonify({'ok': True, 'chunks': stats.get('chunks', 0),
                        'internos': len(stats.get('internos', [])),
                        'externos': len(stats.get('externos', []))})
    except Exception as e:
        log.error(f'desp_rag_seed error: {e}')
        return jsonify({'erro': str(e)}), 500


def _startup():
    try:
        init_db()
        init_saas_db()
        init_desp_db()
        from saas_db import init_slotzap_db as _init_sz
        _init_sz()
        s = stats()
        log.info(f"DB OK — {s['channels']} canais | {s['videos']} vídeos | "
                 f"{s['clients']} clientes")
        if s['videos'] < 50:
            log.info("Poucos vídeos — scrape automático iniciado...")
            def _scrape():
                try:
                    from kids_scraper import scrape_all
                    scrape_all()
                except Exception as e:
                    log.error(f"Scrape startup error: {e}")
            threading.Thread(target=_scrape, daemon=True).start()

        # ── Cleanup: marca campanhas presas em "enviando" como erro ──────────
        # Acontece quando o Railway reinicia o container durante um disparo.
        # O thread daemon morre sem chance de fazer cleanup no banco.
        try:
            _c = get_saas_db()
            presas = _c.execute(
                "SELECT id, sent, total FROM mandazap_campaigns WHERE status='enviando'"
            ).fetchall()
            if presas:
                for p in presas:
                    log.warning(f"[startup] Campanha {p['id']} presa em 'enviando' ({p['sent']}/{p['total']}) — marcando como erro")
                    _c.execute(
                        "UPDATE mandazap_campaigns SET status='erro', finished_at=?, error_log=? WHERE id=?",
                        (datetime.now().isoformat(),
                         f"Interrompida pelo servidor (reinicialização Railway). {p['sent']} de {p['total']} enviados. Clique em Disparar para continuar.",
                         p['id'])
                    )
                _c.commit()
                log.info(f"[startup] {len(presas)} campanha(s) corrigida(s)")
            _c.close()
        except Exception as e:
            log.error(f"[startup] Cleanup campanhas erro: {e}")

        # ── AlertaSC monitoring scheduler ────────────────────────────────────
        try:
            threading.Thread(target=_alerta_scheduler_loop, daemon=True).start()
            log.info('[AlertaSC] Scheduler de monitoramento iniciado (primeira execução em 5 min)')
        except Exception as e:
            log.error(f"[startup] AlertaSC scheduler erro: {e}")

        # ── AgendaSC lembrete automático WhatsApp (24h antes) ────────────
        try:
            threading.Thread(target=_agenda_lembretes_loop, daemon=True).start()
            log.info('[AgendaSC] Scheduler 24h iniciado (primeira execução em 3 min)')
        except Exception as e:
            log.error(f"[startup] AgendaSC lembretes scheduler erro: {e}")

        # ── AgendaSC lembrete 2h antes ────────────────────────────────────
        try:
            threading.Thread(target=_agenda_lembretes_2h_loop, daemon=True).start()
            log.info('[AgendaSC] Scheduler 2h iniciado (primeira execução em 5 min)')
        except Exception as e:
            log.error(f"[startup] AgendaSC lembretes 2h scheduler erro: {e}")

        # ── AgendaSC resumo mensal ────────────────────────────────────────
        try:
            threading.Thread(target=_agenda_resumo_loop, daemon=True).start()
            log.info('[AgendaSC] Scheduler resumo mensal iniciado (roda dia 1º)')
        except Exception as e:
            log.error(f"[startup] AgendaSC resumo mensal scheduler erro: {e}")

    except Exception as e:
        log.error(f"Startup error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# MandaJá — Delivery App
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/mandaja')
def mandaja_landing():
    return render_template('mandaja/landing.html')


@app.route('/mandaja/entrar', methods=['GET', 'POST'])
def mandaja_entrar():
    msg = request.args.get('msg', '')
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        conn  = get_saas_db()
        store = conn.execute(
            'SELECT * FROM mandaja_stores WHERE LOWER(email)=? AND active=1', (email,)
        ).fetchone()
        conn.close()
        if store and check_password_hash(store['password_hash'], senha):
            session['mja_store_id']   = store['id']
            session['mja_store_name'] = store['name']
            session['mja_store_slug'] = store['slug']
            session['mja_plan']       = store['plan']
            return redirect('/mandaja/painel')
        return render_template('mandaja/entrar.html', error='E-mail ou senha incorretos.')
    return render_template('mandaja/entrar.html', msg=msg)


@app.route('/mandaja/cadastro', methods=['GET', 'POST'])
def mandaja_cadastro():
    if request.method == 'POST':
        name       = request.form.get('name', '').strip()
        owner_name = request.form.get('owner_name', '').strip()
        email      = request.form.get('email', '').strip().lower()
        phone      = request.form.get('phone', '').strip()
        cpf_cnpj   = request.form.get('cpf_cnpj', '').strip()
        category   = request.form.get('category', 'restaurante')
        senha      = request.form.get('senha', '')
        city       = request.form.get('city', '').strip()
        if not all([name, owner_name, email, phone, cpf_cnpj, senha]):
            return render_template('mandaja/cadastro.html',
                                   error='Preencha todos os campos obrigatórios.',
                                   cats=MANDAJA_STORE_CATEGORIES)
        if len(senha) < 6:
            return render_template('mandaja/cadastro.html',
                                   error='Senha deve ter pelo menos 6 caracteres.',
                                   cats=MANDAJA_STORE_CATEGORIES)
        # Normaliza CPF/CNPJ — só dígitos
        cpf_cnpj_digits = ''.join(c for c in cpf_cnpj if c.isdigit())
        if len(cpf_cnpj_digits) not in (11, 14):
            return render_template('mandaja/cadastro.html',
                                   error='CPF deve ter 11 dígitos ou CNPJ 14 dígitos. Verifique e tente novamente.',
                                   cats=MANDAJA_STORE_CATEGORIES)
        # Normaliza phone — só dígitos
        phone_digits = ''.join(c for c in phone if c.isdigit())
        slug = _slugify(name)
        conn = get_saas_db()
        # Garante slug único
        base_slug = slug
        i = 1
        while conn.execute('SELECT id FROM mandaja_stores WHERE slug=?', (slug,)).fetchone():
            slug = f"{base_slug}-{i}"; i += 1
        # Verifica e-mail único
        if conn.execute('SELECT id FROM mandaja_stores WHERE LOWER(email)=?', (email,)).fetchone():
            conn.close()
            return render_template('mandaja/cadastro.html',
                                   error='Este e-mail já está cadastrado. Faça login para acessar sua loja.',
                                   cats=MANDAJA_STORE_CATEGORIES)
        # Verifica CPF/CNPJ único — anti-trial-abuse
        existing_doc = conn.execute(
            "SELECT id FROM mandaja_stores WHERE replace(replace(replace(replace(replace(cpf_cnpj,'.',''),'-',''),'/',''),' ',''),'','') = ?",
            (cpf_cnpj_digits,)
        ).fetchone()
        if existing_doc:
            conn.close()
            return render_template('mandaja/cadastro.html',
                                   error='Este CPF/CNPJ já possui uma loja cadastrada. Faça login ou entre em contato pelo WhatsApp (47) 99960-6998.',
                                   cats=MANDAJA_STORE_CATEGORIES)
        # Verifica WhatsApp único — anti-trial-abuse
        existing_phone = conn.execute(
            "SELECT id FROM mandaja_stores WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ?",
            (phone_digits,)
        ).fetchone()
        if existing_phone:
            conn.close()
            return render_template('mandaja/cadastro.html',
                                   error='Este WhatsApp já está vinculado a uma loja. Faça login ou entre em contato pelo WhatsApp (47) 99960-6998.',
                                   cats=MANDAJA_STORE_CATEGORIES)
        trial_ends = (datetime.now() + timedelta(days=7)).isoformat()
        try:
            conn.execute('''
                INSERT INTO mandaja_stores
                (name, slug, owner_name, phone, email, password_hash, category, city, plan, created_at, trial_ends, cpf_cnpj)
                VALUES (?,?,?,?,?,?,?,?,'micro',?,?,?)
            ''', (name, slug, owner_name, phone, email,
                  generate_password_hash(senha), category, city,
                  datetime.now().isoformat(), trial_ends, cpf_cnpj_digits))
        except Exception as _mja_err:
            log.error('[MandaJá] Erro no INSERT (possível coluna faltando): %s', _mja_err)
            # Tenta sem cpf_cnpj — coluna pode ainda não existir no DB de produção
            conn.execute('''
                INSERT INTO mandaja_stores
                (name, slug, owner_name, phone, email, password_hash, category, city, plan, created_at, trial_ends)
                VALUES (?,?,?,?,?,?,?,?,'micro',?,?)
            ''', (name, slug, owner_name, phone, email,
                  generate_password_hash(senha), category, city,
                  datetime.now().isoformat(), trial_ends))
        conn.commit()
        store = conn.execute('SELECT * FROM mandaja_stores WHERE email=?', (email,)).fetchone()
        # Cria horários padrão (Seg-Sex 08-22, Sab 08-20)
        for wd in range(7):
            ct = '20:00' if wd == 5 else '22:00'
            active = 0 if wd == 6 else 1
            conn.execute('''INSERT INTO mandaja_hours (store_id, weekday, open_time, close_time, active)
                            VALUES (?,?,?,?,?)''', (store['id'], wd, '08:00', ct, active))
        conn.commit()
        conn.close()
        session['mja_store_id']   = store['id']
        session['mja_store_name'] = store['name']
        session['mja_store_slug'] = store['slug']
        session['mja_plan']       = 'micro'
        # Email de boas-vindas
        if store.get('email'):
            _enviar_email(
                store['email'],
                '🛍️ Bem-vindo ao MandaJá — Sua loja digital está pronta!',
                _email_boas_vindas(
                    'MandaJá', '🛍️', '#f97316',
                    store['owner_name'].split()[0],
                    trial_ends,
                    'https://4kitem.com.br/mandaja/painel',
                    'Seu cardápio digital está no ar! Adicione produtos, configure horários e comece a receber pedidos pelo WhatsApp agora mesmo.'
                )
            )
        return redirect('/mandaja/painel?novo=1')
    return render_template('mandaja/cadastro.html', cats=MANDAJA_STORE_CATEGORIES)


@app.route('/mandaja/logout')
def mandaja_logout():
    for k in ('mja_store_id', 'mja_store_name', 'mja_store_slug', 'mja_plan'):
        session.pop(k, None)
    return redirect('/mandaja')


# ── MandaJá — Recuperação de senha ───────────────────────────────────────────
@app.route('/mandaja/esqueci-senha', methods=['GET', 'POST'])
def mandaja_esqueci_senha():
    enviado = False
    codigo_tela = None
    erro = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        conn = get_saas_db()
        store = conn.execute('SELECT * FROM mandaja_stores WHERE LOWER(email)=?', (email,)).fetchone()
        if not store:
            erro = 'E-mail não encontrado.'
            conn.close()
        else:
            codigo = str(random.randint(100000, 999999))
            expires = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute('UPDATE mandaja_stores SET reset_token=?, reset_expires=? WHERE id=?',
                         (codigo, expires, store['id']))
            conn.commit(); conn.close()
            html_email = f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <div style="font-size:32px;margin-bottom:8px">🛵</div>
              <h2 style="color:#f97316">Recuperação de senha — MandaJá</h2>
              <p>Olá, <strong>{store['owner_name'].split()[0]}</strong>!</p>
              <p>Seu código de recuperação é:</p>
              <div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#f97316;
                          background:#fff7ed;padding:20px;border-radius:12px;text-align:center;
                          margin:20px 0">{codigo}</div>
              <p style="color:#666;font-size:13px">Válido por 2 horas.</p>
            </div>"""
            ok = _enviar_email(email, 'Código de recuperação — MandaJá', html_email)
            enviado = True
            if not ok:
                codigo_tela = codigo
    return render_template('mandaja/esqueci_senha.html',
                           enviado=enviado, codigo_tela=codigo_tela, erro=erro)


@app.route('/mandaja/redefinir-senha', methods=['GET', 'POST'])
def mandaja_redefinir_senha():
    sucesso = False
    erro = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        codigo = request.form.get('codigo', '').strip()
        nova = request.form.get('nova_senha', '')
        if len(nova) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        else:
            conn = get_saas_db()
            store = conn.execute('SELECT * FROM mandaja_stores WHERE LOWER(email)=?', (email,)).fetchone()
            if not store or store['reset_token'] != codigo:
                erro = 'Código inválido ou e-mail incorreto.'
                conn.close()
            elif store['reset_expires'] and datetime.fromisoformat(store['reset_expires']) < datetime.now():
                erro = 'Código expirado. Solicite um novo.'
                conn.close()
            else:
                conn.execute('UPDATE mandaja_stores SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?',
                             (generate_password_hash(nova), store['id']))
                conn.commit(); conn.close()
                sucesso = True
    return render_template('mandaja/redefinir_senha.html', sucesso=sucesso, erro=erro)


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/mandaja/painel')
@_mandaja_login_required
def mandaja_painel():
    store = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    conn = get_saas_db()
    store_id = store['id']
    # Stats
    today = datetime.now().strftime('%Y-%m-%d')
    stats = {
        'pedidos_hoje': conn.execute(
            "SELECT COUNT(*) FROM mandaja_orders WHERE store_id=? AND DATE(created_at)=?",
            (store_id, today)).fetchone()[0],
        'receita_hoje': conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM mandaja_orders WHERE store_id=? AND DATE(created_at)=? AND status NOT IN ('cancelled')",
            (store_id, today)).fetchone()[0],
        'pedidos_abertos': conn.execute(
            "SELECT COUNT(*) FROM mandaja_orders WHERE store_id=? AND status IN ('new','confirmed','preparing','ready')",
            (store_id,)).fetchone()[0],
        'total_produtos': conn.execute(
            "SELECT COUNT(*) FROM mandaja_products WHERE store_id=? AND active=1",
            (store_id,)).fetchone()[0],
    }
    pedidos_recentes = conn.execute(
        "SELECT * FROM mandaja_orders WHERE store_id=? ORDER BY id DESC LIMIT 10",
        (store_id,)).fetchall()
    pedidos_recentes = [dict(p) for p in pedidos_recentes]
    conn.close()
    plan_info     = MANDAJA_PLANS.get(store['plan'], MANDAJA_PLANS['micro'])
    trial_ends    = store.get('trial_ends') or ''
    plan_active   = store.get('plan_active', 1)
    trial_expired = bool(trial_ends and trial_ends < datetime.now().isoformat())
    return render_template('mandaja/painel.html',
                           store=store, stats=stats,
                           pedidos_recentes=pedidos_recentes,
                           plan=plan_info, plans=MANDAJA_PLANS,
                           trial_ends=trial_ends, trial_expired=trial_expired,
                           plan_active=plan_active)


# ── Produtos ──────────────────────────────────────────────────────────────────
@app.route('/mandaja/produtos')
@_mandaja_login_required
def mandaja_produtos():
    store    = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    store_id = store['id']
    conn     = get_saas_db()
    cats     = conn.execute('SELECT * FROM mandaja_categories WHERE store_id=? ORDER BY sort_order,name', (store_id,)).fetchall()
    prods    = conn.execute('''
        SELECT p.*, c.name as cat_name
        FROM mandaja_products p
        LEFT JOIN mandaja_categories c ON p.category_id = c.id
        WHERE p.store_id=? ORDER BY p.active DESC, p.sort_order, p.name
    ''', (store_id,)).fetchall()
    conn.close()
    plan_info = MANDAJA_PLANS.get(store['plan'], MANDAJA_PLANS['micro'])
    return render_template('mandaja/produtos.html',
                           store=store, cats=[dict(c) for c in cats],
                           prods=[dict(p) for p in prods],
                           plan=plan_info)


@app.route('/mandaja/produtos/novo', methods=['GET', 'POST'])
@_mandaja_login_required
def mandaja_produto_novo():
    store    = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    store_id = store['id']
    conn     = get_saas_db()
    plan_info = MANDAJA_PLANS.get(store['plan'], MANDAJA_PLANS['micro'])
    # Verifica limite do plano
    count = conn.execute(
        'SELECT COUNT(*) FROM mandaja_products WHERE store_id=? AND active=1', (store_id,)
    ).fetchone()[0]
    if count >= plan_info['products']:
        conn.close()
        return redirect(f'/mandaja/produtos?erro=limite_plano')
    cats = conn.execute('SELECT * FROM mandaja_categories WHERE store_id=? ORDER BY name', (store_id,)).fetchall()
    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        try:
            price = float(request.form.get('price', 0) or 0)
            cost  = float(request.form.get('cost', 0) or 0)
            stock = int(request.form.get('stock', -1) or -1)
        except (ValueError, TypeError):
            price, cost, stock = 0.0, 0.0, -1
        category_id  = request.form.get('category_id') or None
        photo_url    = request.form.get('photo_url', '').strip()
        options_raw  = request.form.get('options_json', '[]').strip()
        try:
            _json.loads(options_raw)
        except Exception:
            options_raw = '[]'
        if not name:
            conn.close()
            return render_template('mandaja/produto_form.html',
                                   store=store, cats=[dict(c) for c in cats],
                                   error='Nome é obrigatório.', prod=None)
        conn.execute('''
            INSERT INTO mandaja_products (store_id, category_id, name, description, price, cost, photo_url, stock, options_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        ''', (store_id, category_id, name, description, price, cost, photo_url, stock, options_raw, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return redirect('/mandaja/produtos?ok=criado')
    conn.close()
    return render_template('mandaja/produto_form.html',
                           store=store, cats=[dict(c) for c in cats], prod=None, error=None)


@app.route('/mandaja/produtos/<int:prod_id>/editar', methods=['GET', 'POST'])
@_mandaja_login_required
def mandaja_produto_editar(prod_id):
    store    = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    store_id = store['id']
    conn     = get_saas_db()
    prod     = conn.execute('SELECT * FROM mandaja_products WHERE id=? AND store_id=?', (prod_id, store_id)).fetchone()
    if not prod:
        conn.close()
        return redirect('/mandaja/produtos')
    cats = conn.execute('SELECT * FROM mandaja_categories WHERE store_id=? ORDER BY name', (store_id,)).fetchall()
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        if action == 'delete':
            conn.execute('UPDATE mandaja_products SET active=0 WHERE id=?', (prod_id,))
            conn.commit()
            conn.close()
            return redirect('/mandaja/produtos?ok=removido')
        name        = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        try:
            price = float(request.form.get('price', 0) or 0)
            cost  = float(request.form.get('cost', 0) or 0)
            stock = int(request.form.get('stock', -1) or -1)
        except (ValueError, TypeError):
            price, cost, stock = 0.0, 0.0, -1
        category_id  = request.form.get('category_id') or None
        photo_url    = request.form.get('photo_url', '').strip()
        active       = 1 if request.form.get('active') else 0
        options_raw  = request.form.get('options_json', '[]').strip()
        try:
            _json.loads(options_raw)  # valida JSON
        except Exception:
            options_raw = '[]'
        conn.execute('''
            UPDATE mandaja_products SET name=?, description=?, price=?, cost=?,
            category_id=?, photo_url=?, stock=?, active=?, options_json=? WHERE id=?
        ''', (name, description, price, cost, category_id, photo_url, stock, active, options_raw, prod_id))
        conn.commit()
        conn.close()
        return redirect('/mandaja/produtos?ok=atualizado')
    conn.close()
    return render_template('mandaja/produto_form.html',
                           store=store, cats=[dict(c) for c in cats],
                           prod=dict(prod), error=None)


# ── Categorias (AJAX) ─────────────────────────────────────────────────────────
@app.route('/mandaja/categorias', methods=['POST'])
@_mandaja_login_required
def mandaja_categoria_nova():
    store_id = session['mja_store_id']
    name     = request.json.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Nome obrigatório'}), 400
    conn = get_saas_db()
    cur  = conn.execute('INSERT INTO mandaja_categories (store_id, name) VALUES (?,?)', (store_id, name))
    conn.commit()
    cat_id = cur.lastrowid
    conn.close()
    return jsonify({'id': cat_id, 'name': name})


@app.route('/mandaja/categorias/<int:cat_id>', methods=['DELETE'])
@_mandaja_login_required
def mandaja_categoria_del(cat_id):
    store_id = session['mja_store_id']
    conn     = get_saas_db()
    conn.execute('DELETE FROM mandaja_categories WHERE id=? AND store_id=?', (cat_id, store_id))
    conn.execute('UPDATE mandaja_products SET category_id=NULL WHERE category_id=? AND store_id=?', (cat_id, store_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# ── Horários ──────────────────────────────────────────────────────────────────
@app.route('/mandaja/horarios', methods=['GET', 'POST'])
@_mandaja_login_required
def mandaja_horarios():
    store    = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    store_id = store['id']
    conn     = get_saas_db()
    if request.method == 'POST':
        for wd in range(7):
            active     = 1 if request.form.get(f'active_{wd}') else 0
            open_time  = request.form.get(f'open_{wd}', '08:00')
            close_time = request.form.get(f'close_{wd}', '22:00')
            existing   = conn.execute('SELECT id FROM mandaja_hours WHERE store_id=? AND weekday=?', (store_id, wd)).fetchone()
            if existing:
                conn.execute('UPDATE mandaja_hours SET active=?, open_time=?, close_time=? WHERE id=?',
                             (active, open_time, close_time, existing['id']))
            else:
                conn.execute('INSERT INTO mandaja_hours (store_id, weekday, open_time, close_time, active) VALUES (?,?,?,?,?)',
                             (store_id, wd, open_time, close_time, active))
        conn.commit()
        conn.close()
        return redirect('/mandaja/horarios?ok=1')
    hours = {h['weekday']: dict(h) for h in conn.execute(
        'SELECT * FROM mandaja_hours WHERE store_id=?', (store_id,)).fetchall()}
    conn.close()
    return render_template('mandaja/horarios.html',
                           store=store, hours=hours, weekdays=MANDAJA_WEEKDAYS)


# ── Pedidos ───────────────────────────────────────────────────────────────────
@app.route('/mandaja/api/novos-pedidos')
@_mandaja_login_required
def mandaja_api_novos_pedidos():
    """Endpoint de polling — retorna pedidos novos desde um dado timestamp."""
    store    = _mandaja_get_store()
    if not store:
        return jsonify({'error': 'auth'}), 401
    store_id = store['id']
    since    = request.args.get('since', '')   # ISO string: "2024-01-01T12:00:00"
    conn     = get_saas_db()
    if since:
        rows = conn.execute(
            "SELECT id, order_number, customer_name, total, created_at FROM mandaja_orders "
            "WHERE store_id=? AND status='new' AND created_at > ? ORDER BY id DESC LIMIT 20",
            (store_id, since)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, order_number, customer_name, total, created_at FROM mandaja_orders "
            "WHERE store_id=? AND status='new' ORDER BY id DESC LIMIT 20",
            (store_id,)).fetchall()
    count_total_new = conn.execute(
        "SELECT COUNT(*) FROM mandaja_orders WHERE store_id=? AND status='new'",
        (store_id,)).fetchone()[0]
    conn.close()
    return jsonify({
        'novos': [dict(r) for r in rows],
        'count_new': count_total_new
    })


@app.route('/mandaja/pedidos')
@_mandaja_login_required
def mandaja_pedidos():
    store    = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    store_id = store['id']
    status   = request.args.get('status', '')
    conn     = get_saas_db()
    if status:
        pedidos = conn.execute(
            'SELECT * FROM mandaja_orders WHERE store_id=? AND status=? ORDER BY id DESC LIMIT 100',
            (store_id, status)).fetchall()
    else:
        pedidos = conn.execute(
            'SELECT * FROM mandaja_orders WHERE store_id=? ORDER BY id DESC LIMIT 100',
            (store_id,)).fetchall()
    conn.close()
    return render_template('mandaja/pedidos.html',
                           store=store, pedidos=[dict(p) for p in pedidos],
                           status_filter=status)


@app.route('/mandaja/pedidos/<int:order_id>')
@_mandaja_login_required
def mandaja_pedido_detalhe(order_id):
    store    = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    store_id = store['id']
    conn     = get_saas_db()
    pedido   = conn.execute('SELECT * FROM mandaja_orders WHERE id=? AND store_id=?', (order_id, store_id)).fetchone()
    conn.close()
    if not pedido:
        return redirect('/mandaja/pedidos')
    pedido = dict(pedido)
    pedido['items'] = _json.loads(pedido.get('items_json') or '[]')
    return render_template('mandaja/pedido_detalhe.html', store=store, pedido=pedido)


@app.route('/mandaja/pedidos/<int:order_id>/status', methods=['POST'])
@_mandaja_login_required
def mandaja_pedido_status(order_id):
    store      = _mandaja_get_store()
    if not store:
        return jsonify({'error': 'auth'}), 401
    store_id   = store['id']
    new_status = request.json.get('status')
    valid      = ('new', 'confirmed', 'preparing', 'ready', 'delivered', 'cancelled')
    if new_status not in valid:
        return jsonify({'error': 'Status inválido'}), 400
    conn  = get_saas_db()
    order = conn.execute('SELECT * FROM mandaja_orders WHERE id=? AND store_id=?',
                         (order_id, store_id)).fetchone()
    if not order:
        conn.close()
        return jsonify({'error': 'Pedido não encontrado'}), 404
    conn.execute('UPDATE mandaja_orders SET status=?, updated_at=? WHERE id=? AND store_id=?',
                 (new_status, datetime.now().isoformat(), order_id, store_id))
    conn.commit()
    conn.close()
    # Notifica cliente via WhatsApp se status relevante
    if new_status in ('confirmed', 'preparing', 'ready', 'delivered'):
        threading.Thread(
            target=_mandaja_wa_cliente, args=(dict(store), dict(order), new_status), daemon=True
        ).start()
    return jsonify({'ok': True, 'status': new_status})


# ── Configurações ─────────────────────────────────────────────────────────────
@app.route('/mandaja/configuracoes', methods=['GET', 'POST'])
@_mandaja_login_required
def mandaja_config():
    store    = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    store_id = store['id']
    conn     = get_saas_db()
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        if action == 'change_pass':
            senha_atual = request.form.get('senha_atual', '')
            senha_nova  = request.form.get('senha_nova', '')
            if not check_password_hash(store['password_hash'], senha_atual):
                conn.close()
                return render_template('mandaja/configuracoes.html',
                                       store=store, cats=MANDAJA_STORE_CATEGORIES,
                                       error_pass='Senha atual incorreta.')
            if len(senha_nova) < 6:
                conn.close()
                return render_template('mandaja/configuracoes.html',
                                       store=store, cats=MANDAJA_STORE_CATEGORIES,
                                       error_pass='Nova senha deve ter pelo menos 6 caracteres.')
            conn.execute('UPDATE mandaja_stores SET password_hash=? WHERE id=?',
                         (generate_password_hash(senha_nova), store_id))
            conn.commit()
            conn.close()
            return redirect('/mandaja/configuracoes?ok=senha')
        # Salvar dados da loja
        fields = ['name', 'owner_name', 'phone', 'email', 'description', 'category',
                  'address', 'neighborhood', 'city', 'state', 'cep',
                  'pix_chave', 'pix_nome', 'whatsapp', 'logo_url', 'banner_url',
                  'mandazap_instance', 'cor_primaria', 'instagram', 'facebook',
                  'tiktok', 'whatsapp_publico', 'msg_boas_vindas']
        updates = {f: request.form.get(f, '').strip() for f in fields}
        updates['delivery_fee']    = float(request.form.get('delivery_fee', 0) or 0)
        updates['min_order']       = float(request.form.get('min_order', 0) or 0)
        updates['delivery_time']   = int(request.form.get('delivery_time', 45) or 45)
        updates['accepts_card']    = 1 if request.form.get('accepts_card') else 0
        updates['accepts_cash']    = 1 if request.form.get('accepts_cash') else 0
        updates['mandazap_ativo']  = 1 if request.form.get('mandazap_ativo') else 0
        set_clause = ', '.join(f'{k}=?' for k in updates)
        conn.execute(f'UPDATE mandaja_stores SET {set_clause} WHERE id=?',
                     (*updates.values(), store_id))
        conn.commit()
        conn.close()
        session['mja_store_name'] = updates['name']
        return redirect('/mandaja/configuracoes?ok=1')
    conn.close()
    return render_template('mandaja/configuracoes.html',
                           store=store, cats=MANDAJA_STORE_CATEGORIES)


# ── WhatsApp automático para o CLIENTE (MandaJá) ─────────────────────────────
def _mandaja_wa_cliente(store, order, new_status):
    """Envia WA pro cliente quando o status do pedido muda.
    store e order são sempre dicts ao chegar aqui."""
    try:
        instance = store.get('mandazap_instance', '')
        ativo    = store.get('mandazap_ativo', 0)
        if not ativo or not instance:
            return
        phone = order.get('customer_phone', '')
        nome  = order.get('customer_name', '').split()[0]
        loja  = store.get('name', '')
        num   = order.get('order_number', '')
        tipo  = order.get('delivery_type', 'delivery')
        wa_num = store.get('whatsapp', '') or store.get('phone', '')
        wa_num_clean = ''.join(c for c in wa_num if c.isdigit())

        msgs = {
            'confirmed': (
                f"✅ Olá, {nome}! Seu pedido *#{num}* foi confirmado por *{loja}*.\n\n"
                f"Já estamos separando tudo com carinho 😊\n\n"
                f"Qualquer dúvida, fale com a gente!"
            ),
            'preparing': (
                f"👨‍🍳 Boa notícia, {nome}! Seu pedido *#{num}* está sendo preparado agora!\n\n"
                f"Em breve estará pronto 🔥"
            ),
            'ready': (
                f"📦 Pedido *#{num}* pronto!\n\n"
                + (f"🚚 Seu pedido saiu para entrega! Fique de olho 👀"
                   if tipo == 'delivery' else
                   f"🏠 Pode vir retirar! Seu pedido está te esperando em *{loja}*.")
                + (f"\n\n📲 Fale conosco: wa.me/55{wa_num_clean}" if wa_num_clean else '')
            ),
            'delivered': (
                f"🎉 Pedido *#{num}* entregue!\n\n"
                f"Obrigado pela preferência, {nome}! Esperamos que tenha curtido 😊\n\n"
                f"*{loja}* te espera na próxima!"
            ),
        }
        msg = msgs.get(new_status)
        if not msg:
            return
        _agenda_send_whatsapp(phone, msg, instance)
    except Exception as e:
        log.warning(f'[MandaJá] WA cliente error: {e}')


# ── Financeiro ────────────────────────────────────────────────────────────────
@app.route('/mandaja/financeiro')
@_mandaja_login_required
def mandaja_financeiro():
    store    = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    store_id = store['id']
    conn     = get_saas_db()
    mes      = request.args.get('mes', datetime.now().strftime('%Y-%m'))
    receita  = conn.execute(
        "SELECT COALESCE(SUM(total),0) FROM mandaja_orders WHERE store_id=? AND status='delivered' AND strftime('%Y-%m', created_at)=?",
        (store_id, mes)).fetchone()[0]
    pedidos_mes = conn.execute(
        "SELECT COUNT(*) FROM mandaja_orders WHERE store_id=? AND strftime('%Y-%m', created_at)=?",
        (store_id, mes)).fetchone()[0]
    pedidos_entregues = conn.execute(
        "SELECT COUNT(*) FROM mandaja_orders WHERE store_id=? AND status='delivered' AND strftime('%Y-%m', created_at)=?",
        (store_id, mes)).fetchone()[0]
    pedidos_list = conn.execute(
        "SELECT * FROM mandaja_orders WHERE store_id=? AND strftime('%Y-%m', created_at)=? ORDER BY id DESC",
        (store_id, mes)).fetchall()
    conn.close()
    return render_template('mandaja/financeiro.html',
                           store=store, mes=mes, receita=receita,
                           pedidos_mes=pedidos_mes,
                           pedidos_entregues=pedidos_entregues,
                           pedidos=[dict(p) for p in pedidos_list])


# ── Tela da Cozinha (sem login — acesso via slug) ────────────────────────────
@app.route('/cozinha/<slug>')
def mandaja_cozinha(slug):
    conn  = get_saas_db()
    store = conn.execute('SELECT * FROM mandaja_stores WHERE slug=? AND active=1', (slug,)).fetchone()
    conn.close()
    if not store:
        return 'Loja não encontrada', 404
    return render_template('mandaja/cozinha.html', store=dict(store))


@app.route('/cozinha/<slug>/api')
def mandaja_cozinha_api(slug):
    """API de polling para a tela da cozinha — retorna pedidos ativos."""
    conn  = get_saas_db()
    store = conn.execute('SELECT id FROM mandaja_stores WHERE slug=? AND active=1', (slug,)).fetchone()
    if not store:
        conn.close()
        return jsonify({'error': 'not found'}), 404
    rows = conn.execute(
        "SELECT id, order_number, customer_name, delivery_type, customer_notes, "
        "items_json, status, created_at, updated_at "
        "FROM mandaja_orders "
        "WHERE store_id=? AND status IN ('new','confirmed','preparing') "
        "ORDER BY id ASC",
        (store['id'],)).fetchall()
    conn.close()
    pedidos = []
    for r in rows:
        p = dict(r)
        p['items'] = _json.loads(p.get('items_json') or '[]')
        del p['items_json']
        pedidos.append(p)
    return jsonify({'pedidos': pedidos, 'ts': datetime.now().isoformat()})


@app.route('/cozinha/<slug>/status', methods=['POST'])
def mandaja_cozinha_status(slug):
    """Atualiza status do pedido direto da tela da cozinha."""
    conn  = get_saas_db()
    store = conn.execute('SELECT * FROM mandaja_stores WHERE slug=? AND active=1', (slug,)).fetchone()
    if not store:
        conn.close()
        return jsonify({'error': 'not found'}), 404
    store      = dict(store)
    data       = request.json or {}
    order_id   = data.get('order_id')
    new_status = data.get('status')
    valid      = ('confirmed', 'preparing', 'ready')
    if not order_id or new_status not in valid:
        conn.close()
        return jsonify({'error': 'invalido'}), 400
    order = conn.execute(
        'SELECT * FROM mandaja_orders WHERE id=? AND store_id=?',
        (order_id, store['id'])).fetchone()
    if not order:
        conn.close()
        return jsonify({'error': 'pedido nao encontrado'}), 404
    order = dict(order)
    conn.execute(
        'UPDATE mandaja_orders SET status=?, updated_at=? WHERE id=? AND store_id=?',
        (new_status, datetime.now().isoformat(), order_id, store['id']))
    conn.commit()
    conn.close()
    # WA pro cliente
    if new_status in ('confirmed', 'preparing', 'ready'):
        threading.Thread(
            target=_mandaja_wa_cliente, args=(store, order, new_status), daemon=True
        ).start()
    return jsonify({'ok': True})


# ── Loja pública (vitrine do cliente) ─────────────────────────────────────────
@app.route('/loja/<slug>')
def mandaja_loja(slug):
    conn  = get_saas_db()
    store = conn.execute('SELECT * FROM mandaja_stores WHERE slug=? AND active=1', (slug,)).fetchone()
    if not store:
        conn.close()
        return render_template('mandaja/loja_404.html'), 404
    store = dict(store)
    # Verifica se está aberto agora
    now = datetime.now()
    wd  = now.weekday()
    hour_row = conn.execute(
        'SELECT * FROM mandaja_hours WHERE store_id=? AND weekday=? AND active=1', (store['id'], wd)
    ).fetchone()
    is_open = False
    if hour_row:
        try:
            open_dt  = datetime.strptime(hour_row['open_time'],  '%H:%M').replace(year=now.year, month=now.month, day=now.day)
            close_dt = datetime.strptime(hour_row['close_time'], '%H:%M').replace(year=now.year, month=now.month, day=now.day)
            is_open  = open_dt <= now <= close_dt
        except Exception:
            pass
    cats  = conn.execute(
        'SELECT * FROM mandaja_categories WHERE store_id=? AND active=1 ORDER BY sort_order, name', (store['id'],)
    ).fetchall()
    prods = conn.execute(
        'SELECT * FROM mandaja_products WHERE store_id=? AND active=1 ORDER BY sort_order, name', (store['id'],)
    ).fetchall()
    hours = conn.execute(
        'SELECT * FROM mandaja_hours WHERE store_id=? ORDER BY weekday', (store['id'],)
    ).fetchall()
    conn.close()
    cats_dict  = {c['id']: dict(c) for c in cats}
    prods_list = [dict(p) for p in prods]
    hours_list = [dict(h) for h in hours]
    return render_template('mandaja/loja.html',
                           store=store, cats=list(cats_dict.values()),
                           prods=prods_list, hours=hours_list,
                           weekdays=MANDAJA_WEEKDAYS, is_open=is_open)


# ── Fazer pedido (POST da loja pública) ───────────────────────────────────────
@app.route('/loja/<slug>/pedido', methods=['POST'])
def mandaja_fazer_pedido(slug):
    conn  = get_saas_db()
    store = conn.execute('SELECT * FROM mandaja_stores WHERE slug=? AND active=1', (slug,)).fetchone()
    if not store:
        conn.close()
        return jsonify({'error': 'Loja não encontrada'}), 404
    store = dict(store)
    data  = request.json or {}
    customer_name   = data.get('customer_name', '').strip()
    customer_phone  = data.get('customer_phone', '').strip()
    customer_notes  = data.get('customer_notes', '').strip()
    delivery_type   = data.get('delivery_type', 'delivery')
    address         = data.get('address', '').strip()
    neighborhood    = data.get('neighborhood', '').strip()
    city            = data.get('city', '').strip()
    cep             = data.get('cep', '').strip()
    payment_method  = data.get('payment_method', 'pix')
    change_for      = float(data.get('change_for', 0) or 0)
    items           = data.get('items', [])
    if not customer_name or not customer_phone or not items:
        conn.close()
        return jsonify({'error': 'Dados incompletos'}), 400
    try:
        subtotal = sum(float(i.get('price', 0)) * int(i.get('qty', 1)) for i in items)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({'error': 'Itens com valores inválidos'}), 400
    delivery_fee = float(store['delivery_fee'] or 0) if delivery_type == 'delivery' else 0
    total        = subtotal + delivery_fee
    order_number = _mandaja_next_order_number(store['id'])
    cur = conn.execute('''
        INSERT INTO mandaja_orders
        (store_id, order_number, customer_name, customer_phone, customer_notes,
         delivery_type, address, neighborhood, city, cep,
         payment_method, subtotal, delivery_fee, total, change_for,
         status, items_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?)
    ''', (store['id'], order_number, customer_name, customer_phone, customer_notes,
          delivery_type, address, neighborhood, city, cep,
          payment_method, subtotal, delivery_fee, total, change_for,
          _json.dumps(items, ensure_ascii=False),
          datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()
    order_id = cur.lastrowid
    conn.close()
    # Notificação WhatsApp (se loja tiver número configurado)
    if store.get('whatsapp'):
        _notify_new_order_whatsapp(store, order_id, order_number, customer_name,
                                   customer_phone, items, total, delivery_type,
                                   address, neighborhood, payment_method)
    return jsonify({'ok': True, 'order_id': order_id, 'order_number': order_number,
                    'total': total, 'pix_chave': store.get('pix_chave', ''),
                    'pix_nome': store.get('pix_nome', '')})


def _notify_new_order_whatsapp(store, order_id, order_number, customer_name,
                                customer_phone, items, total, delivery_type,
                                address, neighborhood, payment_method):
    """Envia mensagem WhatsApp para o lojista via Evolution API."""
    try:
        EVO_URL = os.environ.get('EVOLUTION_API_URL', '')
        EVO_KEY = os.environ.get('EVOLUTION_API_KEY', '')
        INSTANCE = os.environ.get('MANDAJA_EVO_INSTANCE', '')
        if not (EVO_URL and EVO_KEY and INSTANCE):
            return
        items_text = '\n'.join(
            f"  • {i.get('qty','1')}x {i.get('name','?')} — R${float(i.get('price',0)):.2f}"
            for i in items
        )
        delivery_text = f"🚚 Entrega: {address}, {neighborhood}" if delivery_type == 'delivery' else "🏠 Retirada no local"
        pay_map = {'pix': '💳 PIX', 'dinheiro': '💵 Dinheiro', 'cartao': '💳 Cartão'}
        msg = (f"🛍️ *NOVO PEDIDO {order_number}*\n\n"
               f"👤 {customer_name} — {customer_phone}\n\n"
               f"🛒 Itens:\n{items_text}\n\n"
               f"💰 Total: R${total:.2f}\n"
               f"💳 Pagamento: {pay_map.get(payment_method, payment_method)}\n"
               f"{delivery_text}\n\n"
               f"🔗 Ver pedido: {request.host_url}mandaja/pedidos/{order_id}")
        phone_clean = _re.sub(r'\D', '', store['whatsapp'])
        requests.post(
            f"{EVO_URL}/message/sendText/{INSTANCE}",
            headers={'apikey': EVO_KEY, 'Content-Type': 'application/json'},
            json={'number': phone_clean, 'text': msg},
            timeout=8
        )
    except Exception as e:
        log.warning(f"[MandaJá] WhatsApp notify error: {e}")


# ── Checkout / Asaas ─────────────────────────────────────────────────────────
@app.route('/mandaja/assinar/<plano>', methods=['GET', 'POST'])
@_mandaja_login_required
def mandaja_assinar(plano):
    if plano not in MANDAJA_PLANS:
        return redirect('/mandaja/painel')
    store = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    p    = MANDAJA_PLANS[plano]
    erro = ''
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX')
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            erro = 'Método de pagamento inválido.'
        else:
            try:
                customer_id = _asaas_criar_ou_buscar_cliente_saas(
                    store['owner_name'], store['email'], store['phone'],
                    store.get('cpf_cnpj', ''), store['id'], 'mandaja_stores'
                )
                if not customer_id:
                    erro = 'Erro ao criar perfil de pagamento. Verifique seus dados cadastrais.'
                else:
                    # Salva customer_id no banco
                    conn = get_saas_db()
                    conn.execute('UPDATE mandaja_stores SET asaas_customer_id=? WHERE id=?',
                                 (customer_id, store['id']))
                    conn.commit()
                    conn.close()
                    sub = _asaas_criar_assinatura_saas(
                        customer_id, 'mandaja', plano, float(p['price']),
                        f"MandaJá {p['label']} — {store['name']}", billing_type
                    )
                    if sub.get('id'):
                        payment_url = sub.get('invoiceUrl') or sub.get('bankSlipUrl') or ''
                        if payment_url:
                            return redirect(payment_url)
                        return redirect('/mandaja/aguardando-pagamento')
                    else:
                        erro = (sub.get('errors') or [{}])[0].get('description', 'Erro ao criar assinatura.')
            except Exception as ex:
                log.exception('[MandaJá] Erro no checkout')
                erro = 'Erro ao processar pagamento. Tente novamente.'
    return render_template('mandaja/checkout.html', store=store, plano=plano, p=p,
                           plans=MANDAJA_PLANS, erro=erro)


@app.route('/mandaja/aguardando-pagamento')
@_mandaja_login_required
def mandaja_aguardando():
    store = _mandaja_get_store()
    return render_template('mandaja/aguardando.html', store=store)


# ══════════════════════════════════════════════════════════════════════════════
# Fim MandaJá
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# PETmed — Triagem Veterinária Inteligente 24h
# ══════════════════════════════════════════════════════════════════════════════
try:
    from petmed import petmed_bp
    from petmed_db import init_petmed_db
    app.register_blueprint(petmed_bp)
    log.info('[PETmed] Blueprint registrado em /petmed')
except Exception as _pm_err:
    log.warning(f'[PETmed] Erro ao carregar blueprint: {_pm_err}')

# ══════════════════════════════════════════════════════════════════════════════
# PUBSHOW — Jukebox digital para bares e pubs
# ══════════════════════════════════════════════════════════════════════════════
try:
    from pubshow import pubshow_bp
    app.register_blueprint(pubshow_bp)
    log.info('[PUBSHOW] Blueprint registrado em /pubshow')
except Exception as _ps_err:
    log.warning(f'[PUBSHOW] Erro ao carregar blueprint: {_ps_err}')

# ══════════════════════════════════════════════════════════════════════════════

with app.app_context():
    _startup()
    # Inicializa banco PETmed (independente do blueprint)
    try:
        from petmed_db import init_petmed_db as _init_petmed_db
        _init_petmed_db()
        log.info('[PETmed] Banco inicializado com sucesso')
    except Exception as _e:
        log.error(f'[PETmed] ERRO ao inicializar banco: {_e}', exc_info=True)
    # Inicializa banco PUBSHOW
    try:
        from pubshow_db import init_pubshow_db as _init_pubshow_db
        _init_pubshow_db()
        log.info('[PUBSHOW] Banco inicializado com sucesso')
    except Exception as _e:
        log.error(f'[PUBSHOW] ERRO ao inicializar banco: {_e}', exc_info=True)

# ══════════════════════════════════════════════════════════════════════════════
#  SLOTZAP — Venda de slots numerados com PIX automático via Asaas
# ══════════════════════════════════════════════════════════════════════════════

def _sz_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('sz_user_id'):
            return redirect('/slotzap/entrar')
        return f(*args, **kwargs)
    return decorated

def _sz_uid():
    return session.get('sz_user_id')


@app.route('/slotzap')
def slotzap_landing():
    return redirect('/slotzap/entrar')


@app.route('/slotzap/entrar', methods=['GET', 'POST'])
def slotzap_entrar():
    erro = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        conn  = get_saas_db()
        u     = conn.execute('SELECT * FROM slotzap_users WHERE email=? AND active=1', (email,)).fetchone()
        conn.close()
        if not u:
            erro = 'Email não encontrado ou conta inativa.'
        elif not check_password_hash(u['password_hash'], senha):
            erro = 'Senha incorreta.'
        else:
            session['sz_user_id']   = u['id']
            session['sz_user_name'] = u['name']
            conn2 = get_saas_db()
            conn2.execute('UPDATE slotzap_users SET last_login=? WHERE id=?',
                          (datetime.now().isoformat(), u['id']))
            conn2.commit(); conn2.close()
            return redirect('/slotzap/app')
    return render_template('slotzap/entrar.html', erro=erro)


@app.route('/slotzap/sair')
def slotzap_sair():
    session.pop('sz_user_id', None)
    session.pop('sz_user_name', None)
    return redirect('/slotzap/entrar')


@app.route('/slotzap/app')
@_sz_login_required
def slotzap_app():
    conn = get_saas_db()
    campanhas = [dict(r) for r in conn.execute('''
        SELECT c.*,
            (SELECT COUNT(*) FROM slotzap_slots s WHERE s.campanha_id=c.id AND s.status="pago")      AS pagos,
            (SELECT COUNT(*) FROM slotzap_slots s WHERE s.campanha_id=c.id AND s.status="reservado") AS reservados,
            (SELECT COUNT(*) FROM slotzap_slots s WHERE s.campanha_id=c.id AND s.status="disponivel") AS disponiveis
        FROM slotzap_campanhas c
        WHERE c.user_id=?
        ORDER BY c.id DESC
    ''', (_sz_uid(),)).fetchall()]
    conn.close()
    return render_template('slotzap/app.html',
                           campanhas=campanhas,
                           user_name=session.get('sz_user_name', ''))


@app.route('/slotzap/nova', methods=['GET', 'POST'])
@_sz_login_required
def slotzap_nova():
    erro = None
    if request.method == 'POST':
        nome    = request.form.get('nome', '').strip()
        descr   = request.form.get('descricao', '').strip()
        preco   = float(request.form.get('preco') or 0)
        total   = int(request.form.get('total_slots') or 100)
        inicio  = int(request.form.get('slots_inicio') or 1)
        if not nome or preco <= 0 or total < 2:
            erro = 'Nome, preço e quantidade são obrigatórios.'
        else:
            conn = get_saas_db()
            cur  = conn.execute(
                'INSERT INTO slotzap_campanhas (user_id,nome,descricao,preco,total_slots,slots_inicio,status,created_at) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (_sz_uid(), nome, descr, preco, total, inicio, 'ativa', datetime.now().isoformat())
            )
            camp_id = cur.lastrowid
            for n in range(inicio, inicio + total):
                conn.execute('INSERT OR IGNORE INTO slotzap_slots (campanha_id,numero,status) VALUES (?,?,?)',
                             (camp_id, n, 'disponivel'))
            conn.commit(); conn.close()
            return redirect(f'/slotzap/campanha/{camp_id}')
    return render_template('slotzap/nova.html', erro=erro)


@app.route('/slotzap/campanha/<int:camp_id>')
@_sz_login_required
def slotzap_campanha(camp_id):
    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return redirect('/slotzap/app')
    camp  = dict(camp)
    slots = [dict(r) for r in conn.execute(
        'SELECT * FROM slotzap_slots WHERE campanha_id=? ORDER BY numero', (camp_id,)
    ).fetchall()]
    conn.close()
    pagos      = sum(1 for s in slots if s['status'] == 'pago')
    reservados = sum(1 for s in slots if s['status'] == 'reservado')
    disponiveis= sum(1 for s in slots if s['status'] == 'disponivel')
    receita    = pagos * float(camp['preco'])
    return render_template('slotzap/campanha.html',
                           camp=camp, slots=slots,
                           pagos=pagos, reservados=reservados,
                           disponiveis=disponiveis, receita=receita)


@app.route('/slotzap/campanha/<int:camp_id>/reservar', methods=['POST'])
@_sz_login_required
def slotzap_reservar(camp_id):
    data         = request.get_json() or {}
    numero       = int(data.get('numero', 0))
    cliente_nome = (data.get('nome') or '').strip()
    cliente_tel  = ''.join(c for c in (data.get('tel') or '') if c.isdigit())

    if not cliente_nome:
        return jsonify({'erro': 'Nome do cliente obrigatório'}), 400

    conn  = get_saas_db()
    camp  = conn.execute('SELECT * FROM slotzap_campanhas WHERE id=? AND user_id=?',
                         (camp_id, _sz_uid())).fetchone()
    slot  = conn.execute('SELECT * FROM slotzap_slots WHERE campanha_id=? AND numero=?',
                         (camp_id, numero)).fetchone()

    if not camp or not slot:
        conn.close()
        return jsonify({'erro': 'Slot não encontrado'}), 404
    if dict(slot)['status'] != 'disponivel':
        conn.close()
        return jsonify({'erro': f'Slot #{numero} não está disponível'}), 400

    preco     = float(dict(camp)['preco'])
    slot_id   = dict(slot)['id']
    charge_id = pix_qr = pix_copia = ''

    # Cria cobrança PIX no Asaas
    customer_id = _sz_criar_cliente_asaas(cliente_nome, cliente_tel)

    if customer_id:
        venc     = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        resp_pay = _asaas_req('POST', '/payments', {
            'customer':          customer_id,
            'billingType':       'PIX',
            'value':             preco,
            'dueDate':           venc,
            'description':       f"SlotZap — {dict(camp)['nome']} — Slot #{numero}",
            'externalReference': f'sz_{slot_id}',
        })
        charge_id = resp_pay.get('id', '')
        if charge_id:
            qr_resp   = _asaas_req('GET', f'/payments/{charge_id}/pixQrCode')
            pix_qr    = qr_resp.get('encodedImage', '')
            pix_copia = qr_resp.get('payload', '')

    conn.execute(
        'UPDATE slotzap_slots SET status=?,cliente_nome=?,cliente_tel=?,'
        'asaas_charge_id=?,pix_qr_code=?,pix_copia_cola=?,reservado_em=? WHERE id=?',
        ('reservado', cliente_nome, cliente_tel, charge_id,
         pix_qr, pix_copia, datetime.now().isoformat(), slot_id)
    )
    conn.commit(); conn.close()

    return jsonify({'ok': True, 'pix_qr': pix_qr, 'pix_copia': pix_copia,
                    'charge_id': charge_id, 'slot_id': slot_id})


@app.route('/slotzap/slot/<int:slot_id>/pagar', methods=['POST'])
@_sz_login_required
def slotzap_pagar(slot_id):
    conn = get_saas_db()
    slot = conn.execute('''SELECT s.* FROM slotzap_slots s
        JOIN slotzap_campanhas c ON c.id=s.campanha_id
        WHERE s.id=? AND c.user_id=?''', (slot_id, _sz_uid())).fetchone()
    if not slot:
        conn.close()
        return jsonify({'erro': 'Slot não encontrado'}), 404
    conn.execute("UPDATE slotzap_slots SET status='pago', pago_em=? WHERE id=?",
                 (datetime.now().isoformat(), slot_id))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/slotzap/slot/<int:slot_id>/cancelar', methods=['POST'])
@_sz_login_required
def slotzap_cancelar(slot_id):
    conn = get_saas_db()
    slot = conn.execute('''SELECT s.* FROM slotzap_slots s
        JOIN slotzap_campanhas c ON c.id=s.campanha_id
        WHERE s.id=? AND c.user_id=?''', (slot_id, _sz_uid())).fetchone()
    if not slot:
        conn.close()
        return jsonify({'erro': 'Slot não encontrado'}), 404
    charge = dict(slot).get('asaas_charge_id', '')
    if charge:
        _asaas_req('DELETE', f'/payments/{charge}')
    conn.execute(
        "UPDATE slotzap_slots SET status='disponivel',cliente_nome='',cliente_tel='',"
        "asaas_charge_id='',pix_qr_code='',pix_copia_cola='',reservado_em=NULL,pago_em=NULL WHERE id=?",
        (slot_id,)
    )
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/slotzap/campanha/<int:camp_id>/gerar-link', methods=['POST'])
@_sz_login_required
def slotzap_gerar_link(camp_id):
    """Gera token público para a campanha."""
    import secrets as _sec
    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    token = dict(camp).get('token_publico') or _sec.token_urlsafe(16)
    conn.execute('UPDATE slotzap_campanhas SET token_publico=? WHERE id=?', (token, camp_id))
    conn.commit(); conn.close()
    base = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
    return jsonify({'ok': True, 'token': token, 'url': f'{base}/slotzap/p/{token}'})


@app.route('/slotzap/campanha/<int:camp_id>/config-wpp', methods=['POST'])
@_sz_login_required
def slotzap_config_wpp(camp_id):
    """Salva configuração de WhatsApp para notificações automáticas."""
    data     = request.get_json() or {}
    grupo_id = (data.get('grupo_id') or '').strip()
    instance = (data.get('instance') or '').strip()
    msg      = (data.get('msg') or '').strip()
    conn = get_saas_db()
    conn.execute('SELECT id FROM slotzap_campanhas WHERE id=? AND user_id=?',
                 (camp_id, _sz_uid())).fetchone()
    conn.execute('UPDATE slotzap_campanhas SET grupo_wpp_id=?,evo_instance=?,msg_pagamento=? WHERE id=?',
                 (grupo_id, instance, msg, camp_id))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/slotzap/campanha/<int:camp_id>/numeros-disponiveis')
@_sz_login_required
def slotzap_numeros_disponiveis(camp_id):
    """Retorna os números WhatsApp conectados no MandaZap para usar como bot."""
    evo_url = (os.environ.get('EVO_URL') or os.environ.get('EVOLUTION_API_URL') or '').rstrip('/')
    evo_key = os.environ.get('EVO_KEY') or os.environ.get('EVOLUTION_API_KEY') or ''
    if not evo_url:
        return jsonify({'numeros': []})
    conn = get_saas_db()
    numeros_db = conn.execute(
        "SELECT id, user_id, label, phone FROM mandazap_numbers WHERE status='connected' ORDER BY id"
    ).fetchall()
    conn.close()
    numeros = []
    for n in numeros_db:
        n = dict(n)
        instance = f"mz{n['user_id']}n{n['id']}"
        phone_clean = n['phone'].lstrip('55') if n['phone'] and n['phone'].startswith('55') else n['phone']
        numeros.append({
            'instance': instance,
            'label': n['label'] or phone_clean,
            'phone': phone_clean,
        })
    return jsonify({'numeros': numeros})


def _sz_criar_cliente_asaas(nome, tel):
    """Cria ou reutiliza cliente no Asaas para SlotZap.
    Nunca envia celular na criação — evita erros de validação do Asaas."""
    customer_id = None
    tel_limpo = ''.join(c for c in (tel or '') if c.isdigit())
    # Tenta reusar cliente existente pelo telefone (só se tiver 11 dígitos válidos)
    if len(tel_limpo) == 11:
        tel_fmt = ('55' + tel_limpo) if not tel_limpo.startswith('55') else tel_limpo
        busca = _asaas_req('GET', f'/customers?mobilePhone={tel_fmt}&limit=1')
        if busca.get('data'):
            customer_id = busca['data'][0].get('id')
    # Cria novo cliente só com nome — sem celular para evitar rejeição do Asaas
    if not customer_id:
        resp = _asaas_req('POST', '/customers', {
            'name': nome or 'Cliente SlotZap',
            'notificationDisabled': True,
        })
        customer_id = resp.get('id')
    return customer_id


@app.route('/slotzap/debug-pix')
@_sz_login_required
def slotzap_debug_pix():
    """Debug: testa criação de cliente + pagamento PIX no Asaas e retorna resposta completa."""
    # 1. Cria cliente de teste
    r_cliente = _asaas_req('POST', '/customers', {
        'name': 'Teste SlotZap Debug',
        'mobilePhone': '47999999999',
        'notificationDisabled': True,
    })
    customer_id = r_cliente.get('id')

    r_pagamento = None
    r_qrcode    = None

    if customer_id:
        from datetime import datetime as _dt, timedelta as _td
        venc = (_dt.now() + _td(days=1)).strftime('%Y-%m-%d')
        r_pagamento = _asaas_req('POST', '/payments', {
            'customer':          customer_id,
            'billingType':       'PIX',
            'value':             1.00,
            'dueDate':           venc,
            'description':       'SlotZap debug PIX',
            'externalReference': 'debug_test',
        })
        charge_id = r_pagamento.get('id', '')
        if charge_id:
            r_qrcode = _asaas_req('GET', f'/payments/{charge_id}/pixQrCode')

    return jsonify({
        'cliente':   r_cliente,
        'pagamento': r_pagamento,
        'qrcode':    r_qrcode,
    })


@app.route('/slotzap/campanha/<int:camp_id>/bot-info')
@_sz_login_required
def slotzap_bot_info(camp_id):
    """Retorna o número de telefone do bot WhatsApp conectado."""
    # Tenta múltiplos nomes de variável (compatibilidade Railway)
    evo_url = (os.environ.get('EVO_URL') or os.environ.get('EVOLUTION_API_URL') or '').rstrip('/')
    evo_key = os.environ.get('EVO_KEY') or os.environ.get('EVOLUTION_API_KEY') or ''
    inst    = os.environ.get('EVO_INSTANCE') or os.environ.get('EVOLUTION_INSTANCE') or ''
    if not evo_url or not inst:
        return jsonify({'numero': None, 'debug': f'EVO_URL={bool(evo_url)} INST={bool(inst)}'})
    headers = {'apikey': evo_key}
    numero  = None
    try:
        # Tentativa 1: fetchInstances (lista todas)
        ri = requests.get(f'{evo_url}/instance/fetchInstances', headers=headers, timeout=8)
        data = ri.json() if ri.content else []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict): continue
                inner = item.get('instance', item)
                iname = (inner.get('instanceName', '') if isinstance(inner, dict) else '') or item.get('instanceName', '')
                owner = (inner.get('owner', '')       if isinstance(inner, dict) else '') or item.get('owner', '')
                if iname == inst and owner:
                    numero = owner.split('@')[0]
                    if numero.startswith('55') and len(numero) > 11:
                        numero = numero[2:]
                    break
    except Exception:
        pass

    if not numero:
        try:
            # Tentativa 2: connectionState — algumas versões retornam o número aqui
            rc = requests.get(f'{evo_url}/instance/connectionState/{inst}', headers=headers, timeout=8)
            cd = rc.json() if rc.content else {}
            inner = cd.get('instance', cd) if isinstance(cd, dict) else {}
            owner = inner.get('owner', '') if isinstance(inner, dict) else ''
            if owner and '@' in owner:
                numero = owner.split('@')[0]
                if numero.startswith('55') and len(numero) > 11:
                    numero = numero[2:]
        except Exception:
            pass

    return jsonify({'numero': numero, 'instance': inst, 'evo_url': evo_url})


@app.route('/slotzap/campanha/<int:camp_id>/grupos')
@_sz_login_required
def slotzap_listar_grupos(camp_id):
    """Lista grupos WhatsApp disponíveis na instância Evolution API."""
    conn = get_saas_db()
    camp = conn.execute('SELECT evo_instance FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    conn.close()
    # Aceita instância via query param (selecionada pelo usuário no modal)
    instance = request.args.get('instance') or (dict(camp).get('evo_instance') or '') if camp else ''
    evo_url  = (os.environ.get('EVO_URL') or os.environ.get('EVOLUTION_API_URL') or '').rstrip('/')
    evo_key  = os.environ.get('EVO_KEY') or os.environ.get('EVOLUTION_API_KEY') or ''
    inst     = instance or os.environ.get('EVO_INSTANCE', '')
    if not evo_url or not inst:
        return jsonify({'grupos': []})
    try:
        r = requests.get(f'{evo_url}/group/fetchAllGroups/{inst}?getParticipants=false',
                         headers={'apikey': evo_key}, timeout=10)
        grupos = [{'id': g.get('id'), 'nome': g.get('subject', g.get('id'))}
                  for g in (r.json() if isinstance(r.json(), list) else [])]
        return jsonify({'grupos': grupos})
    except Exception as e:
        return jsonify({'grupos': [], 'erro': str(e)})


# ── Página pública (sem login) ─────────────────────────────────────────────────

@app.route('/slotzap/p/<token>')
def slotzap_publico(token):
    conn   = get_saas_db()
    camp   = conn.execute('SELECT * FROM slotzap_campanhas WHERE token_publico=? AND status="ativa"',
                          (token,)).fetchone()
    if not camp:
        conn.close()
        return render_template('slotzap/nao_encontrado.html'), 404
    camp  = dict(camp)
    slots = [dict(r) for r in conn.execute(
        'SELECT numero, status FROM slotzap_slots WHERE campanha_id=? ORDER BY numero',
        (camp['id'],)
    ).fetchall()]
    conn.close()
    pagos      = sum(1 for s in slots if s['status'] == 'pago')
    reservados = sum(1 for s in slots if s['status'] == 'reservado')
    disponiveis= sum(1 for s in slots if s['status'] == 'disponivel')
    return render_template('slotzap/publico.html',
                           camp=camp, slots=slots, token=token,
                           pagos=pagos, reservados=reservados, disponiveis=disponiveis)


@app.route('/slotzap/p/<token>/status')
def slotzap_publico_status(token):
    """Polling — retorna status atual dos slots para atualização em tempo real."""
    conn  = get_saas_db()
    camp  = conn.execute('SELECT id FROM slotzap_campanhas WHERE token_publico=?', (token,)).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'not found'}), 404
    slots = conn.execute(
        'SELECT numero, status FROM slotzap_slots WHERE campanha_id=? ORDER BY numero',
        (dict(camp)['id'],)
    ).fetchall()
    conn.close()
    return jsonify({'slots': {str(s['numero']): s['status'] for s in slots}})


@app.route('/slotzap/p/<token>/reservar', methods=['POST'])
def slotzap_publico_reservar(token):
    """Reserva slot publicamente (sem login do admin)."""
    data         = request.get_json() or {}
    numero       = int(data.get('numero', 0))
    cliente_nome = (data.get('nome') or '').strip()
    cliente_tel  = ''.join(c for c in (data.get('tel') or '') if c.isdigit())

    if not cliente_nome:
        return jsonify({'erro': 'Nome obrigatório'}), 400

    conn  = get_saas_db()
    camp  = conn.execute('SELECT * FROM slotzap_campanhas WHERE token_publico=? AND status="ativa"',
                         (token,)).fetchone()
    slot  = conn.execute('SELECT * FROM slotzap_slots WHERE campanha_id=? AND numero=?',
                         (dict(camp)['id'] if camp else -1, numero)).fetchone() if camp else None

    if not camp or not slot:
        conn.close()
        return jsonify({'erro': 'Campanha ou slot não encontrado'}), 404
    if dict(slot)['status'] != 'disponivel':
        conn.close()
        return jsonify({'erro': f'Slot #{numero} já foi reservado. Escolha outro!'}), 400

    preco   = float(dict(camp)['preco'])
    slot_id = dict(slot)['id']
    charge_id = pix_qr = pix_copia = ''

    # Cria cliente e cobrança no Asaas
    customer_id = _sz_criar_cliente_asaas(cliente_nome, cliente_tel)

    if customer_id:
        venc     = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        resp_pay = _asaas_req('POST', '/payments', {
            'customer': customer_id, 'billingType': 'PIX', 'value': preco,
            'dueDate': venc,
            'description': f"SlotZap — {dict(camp)['nome']} — Slot #{numero}",
            'externalReference': f'sz_{slot_id}',
        })
        charge_id = resp_pay.get('id', '')
        if charge_id:
            qr_resp   = _asaas_req('GET', f'/payments/{charge_id}/pixQrCode')
            pix_qr    = qr_resp.get('encodedImage', '')
            pix_copia = qr_resp.get('payload', '')

    conn.execute(
        'UPDATE slotzap_slots SET status=?,cliente_nome=?,cliente_tel=?,'
        'asaas_charge_id=?,pix_qr_code=?,pix_copia_cola=?,reservado_em=? WHERE id=?',
        ('reservado', cliente_nome, cliente_tel, charge_id,
         pix_qr, pix_copia, datetime.now().isoformat(), slot_id)
    )
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'pix_qr': pix_qr, 'pix_copia': pix_copia, 'valor': preco})


@app.route('/slotzap/campanha/<int:camp_id>/encerrar', methods=['POST'])
@_sz_login_required
def slotzap_encerrar(camp_id):
    conn = get_saas_db()
    conn.execute("UPDATE slotzap_campanhas SET status='encerrada' WHERE id=? AND user_id=?",
                 (camp_id, _sz_uid()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


# ── SlotZap no SaaS Admin ──────────────────────────────────────────────────────
@app.route('/saas-admin/slotzap/criar-usuario', methods=['POST'])
@_saas_admin_required
def saas_sz_criar_usuario():
    data  = request.get_json() or {}
    name  = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    senha = (data.get('senha') or '').strip()
    if not all([name, email, senha]):
        return jsonify({'erro': 'name, email e senha obrigatórios'}), 400
    conn = get_saas_db()
    try:
        cur = conn.execute(
            'INSERT INTO slotzap_users (name,email,phone,password_hash,created_at) VALUES (?,?,?,?,?)',
            (name, email, phone, generate_password_hash(senha), datetime.now().isoformat())
        )
        conn.commit()
        user_id = cur.lastrowid
        conn.close()
        return jsonify({'ok': True, 'id': user_id})
    except Exception as e:
        conn.close()
        return jsonify({'erro': str(e)}), 400


if __name__ == '__main__':
    app.run(debug=True, port=5001)
