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
import time as _time
import threading as _threading
import requests as _requests
from datetime import datetime, timedelta
from functools import wraps
from flask import (Blueprint, render_template, redirect, request,
                   session, jsonify, send_from_directory)
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
    'starter': {
        'nome': 'Starter',
        'emoji': '🌱',
        'preco': 69.90,
        'preco_fmt': 'R$ 69,90',
        'descricao': 'Para começar',
        'features': ['TV com clips 24/7', '3 tipos de pedido', '1 slide de propaganda', 'QR Code de mesa', 'Painel de gestão'],
        'max_tipos': ['musica', 'musica_especifica', 'dedicatoria'],
        'max_anuncios': 1,
        'analytics': False,
        'whatsapp': False,
        'happy_hour': False,
        # Starter: só canais de música na TV
        'grupos_tv': ['musica'],
    },
    'bar': {
        'nome': 'Bar',
        'emoji': '🍺',
        'preco': 129.90,
        'preco_fmt': 'R$ 129,90',
        'descricao': 'Para bares e botecos',
        'destaque': True,
        'features': ['Tudo do Starter', 'Todos os tipos de pedido', '3 slides de propaganda', 'Analytics de horários', 'Parabéns e Dedicatórias', 'Promoção Relâmpago'],
        'max_tipos': None,
        'max_anuncios': 3,
        'analytics': True,
        'whatsapp': False,
        'happy_hour': False,
        # Bar: música + shows + esporte principal
        'grupos_tv': ['musica', 'shows', 'sport'],
    },
    'pro': {
        'nome': 'Pro',
        'emoji': '🚀',
        'preco': 199.90,
        'preco_fmt': 'R$ 199,90',
        'descricao': 'Para maximizar o lucro',
        'features': ['Tudo do Bar', 'Happy Hour automático', 'Notificação WhatsApp', 'Ranking da noite na TV', '5 slides de propaganda', 'Relatórios completos'],
        'max_tipos': None,
        'max_anuncios': 5,
        'analytics': True,
        'whatsapp': True,
        'happy_hour': True,
        # Pro: todos os canais
        'grupos_tv': None,
    },
    'rede': {
        'nome': 'Rede',
        'emoji': '🏟️',
        'preco': 349.90,
        'preco_fmt': 'R$ 349,90',
        'descricao': 'Para redes e múltiplos locais',
        'features': ['Tudo do Pro', 'Múltiplos locais', 'Painel unificado', 'Suporte prioritário', 'Slides ilimitados'],
        'max_tipos': None,
        'max_anuncios': 999,
        'analytics': True,
        'whatsapp': True,
        'happy_hour': True,
        'multi': True,
        # Rede: todos os canais
        'grupos_tv': None,
    },
}

def _plano_permite(b, feature: str) -> bool:
    """Verifica se o plano atual do bar permite uma feature."""
    plano_key = b.get('plano', 'bar') or 'bar'
    p = PLANOS.get(plano_key, PLANOS['bar'])
    return bool(p.get(feature, False))


def _plano_canais_permitidos(b) -> dict:
    """Retorna o subconjunto de CANAIS permitido pelo plano do bar.
    None nos grupos_tv significa todos os canais liberados.
    """
    plano_key = b.get('plano', 'bar') or 'bar'
    p = PLANOS.get(plano_key, PLANOS['bar'])
    grupos = p.get('grupos_tv')          # None = sem restrição
    if grupos is None:
        return CANAIS
    return {k: v for k, v in CANAIS.items() if v.get('grupo') in grupos}


def _plano_max_anuncios(b) -> int:
    plano_key = b.get('plano', 'bar') or 'bar'
    p = PLANOS.get(plano_key, PLANOS['bar'])
    return int(p.get('max_anuncios', 3))


def _plano_tipos_permitidos(b) -> list | None:
    """Retorna lista de tipos permitidos ou None (= todos)."""
    plano_key = b.get('plano', 'bar') or 'bar'
    p = PLANOS.get(plano_key, PLANOS['bar'])
    return p.get('max_tipos', None)  # None = sem restrição


# ── Diretório de uploads de slides ───────────────────────────────────────────
_SLIDES_DIR = os.path.join(
    os.environ.get('DATA_DIR', os.path.abspath(os.path.dirname(__file__) or '.')),
    'pubshow_slides'
)


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
    # chave 'casamento' mantida por compatibilidade com o banco; rótulo agora é "Namoro"
    'casamento':        {'nome': 'Pedido de Namoro 💕',   'emoji': '💕', 'preco': 25.00, 'cor': '#a855f7'},
}

# Taxa que o Asaas DESCONTA por cobrança PIX recebida (custo do gateway).
# Ajuste para o valor real do seu contrato Asaas via env PUBSHOW_TAXA_ASAAS.
PIX_TAXA_ASAAS = float(os.environ.get('PUBSHOW_TAXA_ASAAS', '1.00'))

# Taxa de conveniência cobrada do CLIENTE quando o pagamento é automático via
# Asaas. Por padrão = a taxa do Asaas, então o bar recebe o valor cheio do item
# (a taxa do gateway sai do bolso de quem pede). No PIX manual (chave do próprio
# bar) não há taxa. Ajustável via env PUBSHOW_TAXA_CONVENIENCIA.
PIX_TAXA_CONVENIENCIA = float(os.environ.get('PUBSHOW_TAXA_CONVENIENCIA', str(PIX_TAXA_ASAAS)))


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
    # Sub-tag 00 = GUI (br.gov.bcb.pix), sub-tag 01 = chave PIX
    mai = tlv('26', tlv('00', 'br.gov.bcb.pix') + tlv('01', chave))

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


def _ts_to_brt(ts: str) -> str:
    """Converte um timestamp ISO (UTC ou sem fuso) para horário de Brasília (UTC-3).
    Se o servidor já estiver em BRT (TZ=America/Sao_Paulo), detecta e não duplica.
    Retorna string no formato 'YYYY-MM-DD HH:MM:SS' já em hora local Brasil.
    """
    if not ts:
        return ts
    try:
        # Normaliza: aceita 'T' ou ' ', descarta microsegundos e offset
        ts_clean = ts[:19].replace('T', ' ')
        dt = datetime.strptime(ts_clean, '%Y-%m-%d %H:%M:%S')

        # Detecta se o servidor já está rodando em BRT checando a hora do sistema
        import time as _t
        utc_offset_h = -(_t.timezone if not _t.daylight else _t.altzone) / 3600
        if utc_offset_h <= -2.5:
            # Servidor já em BRT (UTC-3) ou parecido — não ajusta
            return ts_clean
        # Servidor em UTC (offset=0) ou UTC-1, UTC+x: subtrai para chegar a UTC-3
        # Ajuste = offset_servidor - (-3) = offset_servidor + 3
        ajuste = -(utc_offset_h + 3)
        dt_brt = dt + timedelta(hours=ajuste)
        return dt_brt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return ts[:19].replace('T', ' ')


def _pedidos_com_hora_local(pedidos: list) -> list:
    """Converte created_at de cada pedido para hora de Brasília."""
    resultado = []
    for p in pedidos:
        d = dict(p) if not isinstance(p, dict) else p
        d['created_at'] = _ts_to_brt(d.get('created_at', ''))
        resultado.append(d)
    return resultado


# ── Web Push — notificações nativas no celular do bar ────────────────────────

_vapid_cache: dict = {}

def _vapid_keys():
    """Retorna (pub_b64url, priv_pem) — gera e persiste no DB se não existir."""
    global _vapid_cache
    if _vapid_cache:
        return _vapid_cache['pub'], _vapid_cache['priv']
    # Env vars têm prioridade
    pub  = os.environ.get('VAPID_PUBLIC_KEY', '')
    priv = os.environ.get('VAPID_PRIVATE_KEY', '')
    if pub and priv:
        _vapid_cache = {'pub': pub, 'priv': priv}
        return pub, priv
    # Tenta banco
    try:
        conn = get_pubshow_db()
        row = conn.execute('SELECT pub_key, priv_key FROM pubshow_vapid_keys LIMIT 1').fetchone()
        if row:
            _vapid_cache = {'pub': row['pub_key'], 'priv': row['priv_key']}
            conn.close()
            return row['pub_key'], row['priv_key']
        # Gera novo par
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        import base64 as _b64
        priv_key = ec.generate_private_key(ec.SECP256R1())
        pub_raw  = priv_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        pub_b64  = _b64.urlsafe_b64encode(pub_raw).rstrip(b'=').decode()
        priv_pem = priv_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()).decode()
        conn.execute('INSERT OR IGNORE INTO pubshow_vapid_keys (id, pub_key, priv_key) VALUES (1,?,?)',
                     (pub_b64, priv_pem))
        conn.commit(); conn.close()
        _vapid_cache = {'pub': pub_b64, 'priv': priv_pem}
        return pub_b64, priv_pem
    except Exception as _e:
        log.warning('[Push] Erro ao gerar VAPID: %s', _e)
        try: conn.close()
        except: pass
        return '', ''


def _enviar_push_pedido(business_id: int, tipo_emoji: str, tipo_nome: str,
                        nome_cliente: str, valor: float, pedido_id: int = 0):
    """Envia Web Push para todas as subscriptions ativas do bar."""
    pub, priv = _vapid_keys()
    if not pub or not priv:
        return
    try:
        import json as _json
        try:
            from pywebpush import webpush, WebPushException
        except ImportError:
            log.debug('[Push] pywebpush não instalado — push desabilitado')
            return
        conn = get_pubshow_db()
        subs = conn.execute(
            'SELECT subscription FROM pubshow_push_subscriptions WHERE business_id=?',
            (business_id,)
        ).fetchall()
        conn.close()
        if not subs:
            return
        payload = _json.dumps({
            'titulo':    f'{tipo_emoji} Novo pedido no Jukebox!',
            'corpo':     f'{nome_cliente} — {tipo_nome} · R$ {valor:.2f}',
            'url':       '/pubshow/painel',
            'pedido_id': pedido_id  # usado no SW para tag única por pedido
        })
        mortos = []
        for row in subs:
            try:
                sub_info = _json.loads(row['subscription'])
                webpush(
                    subscription_info=sub_info,
                    data=payload,
                    vapid_private_key=priv,
                    vapid_claims={'sub': 'mailto:contato@4kitem.com.br'}
                )
            except WebPushException as _wpe:
                if _wpe.response and _wpe.response.status_code in (404, 410):
                    mortos.append(row['subscription'])
            except Exception as _pe:
                log.debug('[Push] Erro ao enviar push: %s', _pe)
        # Remove subscriptions expiradas
        if mortos:
            conn2 = get_pubshow_db()
            for s in mortos:
                conn2.execute(
                    'DELETE FROM pubshow_push_subscriptions WHERE subscription=?', (s,))
            conn2.commit(); conn2.close()
    except Exception as _e:
        log.warning('[Push] Erro geral: %s', _e)


# ── Email onboarding ──────────────────────────────────────────────────────────

_email_last_check = 0.0   # timestamp da última verificação da fila (rate limit)

def _pubshow_enviar_email(para: str, assunto: str, html: str) -> bool:
    """Envia email via Resend API. Usa RESEND_API_KEY do environment."""
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        log.warning('[PUBSHOW email] RESEND_API_KEY não configurada — email não enviado')
        return False
    from_addr = os.environ.get('PUBSHOW_EMAIL_FROM',
                  os.environ.get('EMAIL_FROM', 'PUBSHOW Jukebox <noreply@pubshow.com.br>'))
    try:
        r = _requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'from': from_addr, 'to': [para], 'subject': assunto, 'html': html},
            timeout=10
        )
        ok = r.status_code in (200, 201)
        if not ok:
            log.error('[PUBSHOW email] Resend erro %s: %s', r.status_code, r.text[:200])
        return ok
    except Exception as e:
        log.error('[PUBSHOW email] Exceção ao enviar: %s', e)
        return False


def _email_html_base(titulo: str, corpo: str) -> str:
    """Wrapper HTML base para todos os emails — dark theme PUBSHOW."""
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo}</title>
</head>
<body style="margin:0;padding:0;background:#0d0d14;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d0d14;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#08080f 0%,#131320 100%);border-radius:16px 16px 0 0;padding:32px 40px;text-align:center;border-bottom:2px solid #4ade80;">
          <div style="font-size:32px;margin-bottom:8px;">🎵</div>
          <div style="color:#4ade80;font-size:22px;font-weight:800;letter-spacing:2px;">PUBSHOW</div>
          <div style="color:#6b7280;font-size:12px;letter-spacing:1px;margin-top:4px;">JUKEBOX DIGITAL</div>
        </td></tr>

        <!-- Body -->
        <tr><td style="background:#131320;padding:40px;border-radius:0 0 16px 16px;">
          {corpo}
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:24px 0;text-align:center;">
          <p style="color:#374151;font-size:12px;margin:0;">
            PUBSHOW Jukebox Digital &bull; Você recebeu este email por ser cliente PUBSHOW.<br>
            <a href="mailto:suporte@pubshow.com.br" style="color:#4ade80;text-decoration:none;">suporte@pubshow.com.br</a>
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _email_boas_vindas(nome: str, code: str, token: str, plano: str, trial_ends: str) -> tuple:
    """Email dia 0 — boas-vindas com links da TV e Jukebox."""
    base_url = os.environ.get('BASE_URL', 'https://pubshow.com.br')
    tv_url = f'{base_url}/pubshow/tv/{code}'
    jk_url = f'{base_url}/pubshow/jukebox/{token}'
    painel_url = f'{base_url}/pubshow/painel'
    nome_plano = {'starter': 'Starter 🌱', 'bar': 'Bar 🍺', 'pro': 'Pro ⚡', 'rede': 'Rede 🏢'}.get(plano, plano)

    try:
        trial_fmt = datetime.fromisoformat(trial_ends[:19]).strftime('%d/%m/%Y')
    except Exception:
        trial_fmt = '7 dias'

    corpo = f"""
      <h1 style="color:#f9fafb;font-size:26px;font-weight:800;margin:0 0 8px;">
        Bem-vindo ao PUBSHOW, {nome.split()[0]}! 🎉
      </h1>
      <p style="color:#9ca3af;font-size:15px;margin:0 0 28px;line-height:1.6;">
        Seu Jukebox está pronto e no ar. Seu trial gratuito vai até <strong style="color:#4ade80">{trial_fmt}</strong>.
        Abra a TV, cole o link e os clientes já podem pedir músicas pelo celular!
      </p>

      <!-- Links box -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#08080f;border-radius:12px;border:1px solid #1f2937;margin-bottom:28px;">
        <tr><td style="padding:20px 24px;">
          <p style="color:#6b7280;font-size:11px;font-weight:700;letter-spacing:1px;margin:0 0 12px;text-transform:uppercase;">Seus links</p>

          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;">
            <tr>
              <td style="color:#4ade80;font-size:13px;font-weight:700;padding-bottom:4px;">📺 TV / Telão</td>
            </tr>
            <tr>
              <td>
                <a href="{tv_url}" style="color:#e5e7eb;font-size:13px;text-decoration:none;word-break:break-all;">{tv_url}</a>
              </td>
            </tr>
          </table>

          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="color:#a78bfa;font-size:13px;font-weight:700;padding-bottom:4px;">🎵 Jukebox (clientes)</td>
            </tr>
            <tr>
              <td>
                <a href="{jk_url}" style="color:#e5e7eb;font-size:13px;text-decoration:none;word-break:break-all;">{jk_url}</a>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>

      <!-- Steps -->
      <p style="color:#9ca3af;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin:0 0 16px;">Como funciona em 3 passos</p>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
        <tr>
          <td style="padding:12px 16px;background:#1a1a2e;border-radius:8px;margin-bottom:8px;" valign="top">
            <div style="color:#4ade80;font-size:18px;font-weight:800;display:inline;">1.</div>
            <span style="color:#e5e7eb;font-size:14px;"> Abra o link da TV em qualquer navegador no telão do seu bar</span>
          </td>
        </tr>
        <tr><td style="height:8px;"></td></tr>
        <tr>
          <td style="padding:12px 16px;background:#1a1a2e;border-radius:8px;" valign="top">
            <div style="color:#4ade80;font-size:18px;font-weight:800;display:inline;">2.</div>
            <span style="color:#e5e7eb;font-size:14px;"> Coloque o QR code visível para os clientes escanear com o celular</span>
          </td>
        </tr>
        <tr><td style="height:8px;"></td></tr>
        <tr>
          <td style="padding:12px 16px;background:#1a1a2e;border-radius:8px;" valign="top">
            <div style="color:#4ade80;font-size:18px;font-weight:800;display:inline;">3.</div>
            <span style="color:#e5e7eb;font-size:14px;"> Pedidos chegam em tempo real — você aprova no painel, a música toca na TV!</span>
          </td>
        </tr>
      </table>

      <!-- CTA -->
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td align="center">
            <a href="{painel_url}" style="display:inline-block;background:#4ade80;color:#08080f;font-weight:800;font-size:16px;padding:14px 40px;border-radius:8px;text-decoration:none;letter-spacing:0.5px;">
              Abrir painel de gestão →
            </a>
          </td>
        </tr>
      </table>
    """
    assunto = f'🎵 Seu Jukebox está no ar! Código TV: {code}'
    return assunto, _email_html_base('Bem-vindo ao PUBSHOW', corpo)


def _email_dica_setup(nome: str, code: str) -> tuple:
    """Email dia 2 — dicas de configuração (PIX, QR, preços)."""
    base_url = os.environ.get('BASE_URL', 'https://pubshow.com.br')
    painel_url = f'{base_url}/pubshow/painel'

    corpo = f"""
      <h1 style="color:#f9fafb;font-size:24px;font-weight:800;margin:0 0 8px;">
        Já configurou seu Jukebox, {nome.split()[0]}? ⚙️
      </h1>
      <p style="color:#9ca3af;font-size:15px;margin:0 0 28px;line-height:1.6;">
        Em 5 minutos você deixa o PUBSHOW completo e começa a faturar com pedidos de música.
        Veja o que ainda falta configurar:
      </p>

      <!-- Checklist -->
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">

        <tr><td style="padding:14px 16px 14px 16px;background:#0d1f0d;border-radius:8px;border-left:3px solid #4ade80;margin-bottom:8px;">
          <div style="color:#4ade80;font-size:15px;font-weight:700;margin-bottom:4px;">💰 Configure seu PIX</div>
          <div style="color:#9ca3af;font-size:13px;line-height:1.5;">
            Receba pagamentos direto na sua conta. Vá em Painel → Configurações → Chave PIX.
          </div>
        </td></tr>

        <tr><td style="height:10px;"></td></tr>

        <tr><td style="padding:14px 16px;background:#12121f;border-radius:8px;border-left:3px solid #a78bfa;">
          <div style="color:#a78bfa;font-size:15px;font-weight:700;margin-bottom:4px;">🎛️ Personalize os preços</div>
          <div style="color:#9ca3af;font-size:13px;line-height:1.5;">
            Defina o valor de cada tipo de pedido (VIP, Flash, Parabéns...) conforme o seu público.
          </div>
        </td></tr>

        <tr><td style="height:10px;"></td></tr>

        <tr><td style="padding:14px 16px;background:#12121f;border-radius:8px;border-left:3px solid #f59e0b;">
          <div style="color:#f59e0b;font-size:15px;font-weight:700;margin-bottom:4px;">📺 Imprima o QR Code</div>
          <div style="color:#9ca3af;font-size:13px;line-height:1.5;">
            Coloque na mesa ou balcão para os clientes escanear. Quanto mais visível, mais pedidos!
          </div>
        </td></tr>

        <tr><td style="height:10px;"></td></tr>

        <tr><td style="padding:14px 16px;background:#12121f;border-radius:8px;border-left:3px solid #06b6d4;">
          <div style="color:#06b6d4;font-size:15px;font-weight:700;margin-bottom:4px;">🎬 Adicione seus anúncios</div>
          <div style="color:#9ca3af;font-size:13px;line-height:1.5;">
            Anuncie promoções e pratos especiais diretamente na tela entre os vídeos.
          </div>
        </td></tr>

      </table>

      <!-- CTA -->
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td align="center">
            <a href="{painel_url}" style="display:inline-block;background:#4ade80;color:#08080f;font-weight:800;font-size:16px;padding:14px 40px;border-radius:8px;text-decoration:none;">
              Configurar agora →
            </a>
          </td>
        </tr>
      </table>
    """
    assunto = '⚙️ Configure seu PIX e comece a receber pedidos hoje'
    return assunto, _email_html_base('Dicas de configuração', corpo)


def _email_trial_ending(nome: str, plano: str, trial_ends: str) -> tuple:
    """Email dia 5 — trial acaba em 2 dias."""
    base_url = os.environ.get('BASE_URL', 'https://pubshow.com.br')
    planos_url = f'{base_url}/pubshow/planos'
    nome_plano = {'starter': 'Starter', 'bar': 'Bar', 'pro': 'Pro', 'rede': 'Rede'}.get(plano, 'Bar')
    preco_plano = {k: p['preco_fmt'] for k, p in PLANOS.items()}.get(plano, 'R$ 129,90')

    try:
        trial_fmt = datetime.fromisoformat(trial_ends[:19]).strftime('%d/%m/%Y')
    except Exception:
        trial_fmt = 'em 2 dias'

    corpo = f"""
      <h1 style="color:#f9fafb;font-size:24px;font-weight:800;margin:0 0 8px;">
        ⏰ Seu trial acaba em 2 dias, {nome.split()[0]}
      </h1>
      <p style="color:#9ca3af;font-size:15px;margin:0 0 28px;line-height:1.6;">
        Seu período gratuito encerra em <strong style="color:#f59e0b">{trial_fmt}</strong>.
        Assine agora para não perder o acesso ao seu Jukebox.
      </p>

      <!-- Perda de acesso -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#1f0a0a;border:1px solid #7f1d1d;border-radius:12px;margin-bottom:24px;">
        <tr><td style="padding:20px 24px;">
          <p style="color:#f87171;font-size:14px;font-weight:700;margin:0 0 12px;">Se você não assinar até {trial_fmt}:</p>
          <p style="color:#9ca3af;font-size:13px;margin:4px 0;">❌ O Jukebox fica offline para seus clientes</p>
          <p style="color:#9ca3af;font-size:13px;margin:4px 0;">❌ Os pedidos de música param de funcionar</p>
          <p style="color:#9ca3af;font-size:13px;margin:4px 0;">❌ A TV para de tocar os clips</p>
        </td></tr>
      </table>

      <!-- Plano recomendado -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1f0d;border:1px solid #166534;border-radius:12px;margin-bottom:28px;">
        <tr><td style="padding:20px 24px;">
          <p style="color:#4ade80;font-size:14px;font-weight:700;margin:0 0 8px;">✅ Continue com o plano {nome_plano}</p>
          <p style="color:#6b7280;font-size:13px;margin:0 0 4px;">Apenas <strong style="color:#4ade80;font-size:18px;">{preco_plano}/mês</strong></p>
          <p style="color:#9ca3af;font-size:13px;margin:0;">Cancele a qualquer momento &bull; Sem fidelidade</p>
        </td></tr>
      </table>

      <!-- CTA -->
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td align="center">
            <a href="{planos_url}" style="display:inline-block;background:#f59e0b;color:#08080f;font-weight:800;font-size:16px;padding:14px 40px;border-radius:8px;text-decoration:none;">
              Garantir minha assinatura →
            </a>
          </td>
        </tr>
      </table>
    """
    assunto = f'⏰ Faltam 2 dias! Garanta o PUBSHOW no seu bar'
    return assunto, _email_html_base('Trial acabando', corpo)


def _email_trial_expirado(nome: str, plano: str) -> tuple:
    """Email dia 7+ — trial expirado."""
    base_url = os.environ.get('BASE_URL', 'https://pubshow.com.br')
    planos_url = f'{base_url}/pubshow/planos'
    nome_plano = {'starter': 'Starter', 'bar': 'Bar', 'pro': 'Pro', 'rede': 'Rede'}.get(plano, 'Bar')
    preco_plano = {k: p['preco_fmt'] for k, p in PLANOS.items()}.get(plano, 'R$ 129,90')

    corpo = f"""
      <h1 style="color:#f9fafb;font-size:24px;font-weight:800;margin:0 0 8px;">
        Seu trial expirou 😢
      </h1>
      <p style="color:#9ca3af;font-size:15px;margin:0 0 28px;line-height:1.6;">
        Oi {nome.split()[0]}, seu período gratuito no PUBSHOW encerrou.
        Mas a boa notícia: você pode reativar o Jukebox agora mesmo e voltar a receber pedidos de música!
      </p>

      <!-- O que você estava usando -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#12121f;border-radius:12px;border:1px solid #1f2937;margin-bottom:24px;">
        <tr><td style="padding:20px 24px;">
          <p style="color:#6b7280;font-size:12px;font-weight:700;letter-spacing:1px;margin:0 0 12px;text-transform:uppercase;">O que você vai reativar</p>
          <p style="color:#e5e7eb;font-size:13px;margin:6px 0;">🎵 TV com clips musicais 24/7</p>
          <p style="color:#e5e7eb;font-size:13px;margin:6px 0;">📱 Jukebox para os clientes pedirem músicas</p>
          <p style="color:#e5e7eb;font-size:13px;margin:6px 0;">💰 Recebimento via PIX integrado</p>
          <p style="color:#e5e7eb;font-size:13px;margin:6px 0;">📊 Painel de gestão completo</p>
        </td></tr>
      </table>

      <!-- Preço -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1f0d;border:1px solid #166534;border-radius:12px;margin-bottom:28px;">
        <tr><td style="padding:20px 24px;text-align:center;">
          <p style="color:#4ade80;font-size:13px;font-weight:700;margin:0 0 4px;">Plano {nome_plano}</p>
          <p style="color:#4ade80;font-size:32px;font-weight:800;margin:0 0 4px;">{preco_plano}<span style="font-size:16px;font-weight:400;color:#6b7280;">/mês</span></p>
          <p style="color:#6b7280;font-size:12px;margin:0;">Cancele quando quiser &bull; Sem fidelidade</p>
        </td></tr>
      </table>

      <!-- CTA -->
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td align="center">
            <a href="{planos_url}" style="display:inline-block;background:#4ade80;color:#08080f;font-weight:800;font-size:16px;padding:14px 40px;border-radius:8px;text-decoration:none;">
              Reativar meu Jukebox →
            </a>
          </td>
        </tr>
      </table>

      <p style="color:#4b5563;font-size:12px;margin-top:24px;text-align:center;">
        Dúvidas? Responda este email ou fale com a gente: <a href="mailto:suporte@pubshow.com.br" style="color:#4ade80;">suporte@pubshow.com.br</a>
      </p>
    """
    assunto = '🔴 Seu trial expirou — Reative o PUBSHOW agora'
    return assunto, _email_html_base('Trial expirado', corpo)


def _agendar_emails_onboarding(business_id: int, trial_ends: str):
    """Agenda os 4 emails de onboarding para um novo cadastro."""
    agora = datetime.now()
    emails = [
        ('boas_vindas',   agora + timedelta(minutes=1)),    # imediato (~1min)
        ('dica_setup',    agora + timedelta(days=2)),         # dia 2
        ('trial_ending',  agora + timedelta(days=5)),         # dia 5 (2 dias antes do fim)
        ('trial_expirado', agora + timedelta(days=7, hours=1)), # dia 7 (logo após expirar)
    ]
    try:
        conn = get_pubshow_db()
        for tipo, quando in emails:
            conn.execute(
                'INSERT INTO pubshow_email_queue (business_id, tipo, scheduled_at) VALUES (?,?,?)',
                (business_id, tipo, quando.isoformat())
            )
        conn.commit()
        conn.close()
        log.info('[PUBSHOW email] %d emails agendados para business_id=%d', len(emails), business_id)
    except Exception as e:
        log.error('[PUBSHOW email] Erro ao agendar: %s', e)


def _processar_fila_emails():
    """Verifica e envia emails pendentes da fila. Chame de qualquer rota frequente."""
    global _email_last_check
    now_ts = _time.time()
    # Rate limit: só verifica a cada 10 minutos
    if now_ts - _email_last_check < 600:
        return
    _email_last_check = now_ts

    try:
        conn = get_pubshow_db()
        pendentes = conn.execute(
            '''SELECT q.id, q.tipo, q.scheduled_at, q.business_id,
                      b.nome, b.email, b.code, b.jukebox_token, b.plano,
                      b.trial_ends, b.plano_ativo
               FROM pubshow_email_queue q
               JOIN pubshow_businesses b ON b.id = q.business_id
               WHERE q.sent_at IS NULL
                 AND q.scheduled_at <= ?
               ORDER BY q.scheduled_at ASC
               LIMIT 20''',
            (datetime.now().isoformat(),)
        ).fetchall()
        conn.close()
    except Exception as e:
        log.error('[PUBSHOW email] Erro ao buscar fila: %s', e)
        return

    for row in pendentes:
        tipo      = row['tipo']
        nome      = row['nome']
        email     = row['email']
        code      = row['code']
        token     = row['jukebox_token'] or code
        plano     = row['plano'] or 'bar'
        trial_end = row['trial_ends'] or ''

        try:
            if tipo == 'boas_vindas':
                assunto, html = _email_boas_vindas(nome, code, token, plano, trial_end)
            elif tipo == 'dica_setup':
                assunto, html = _email_dica_setup(nome, code)
            elif tipo == 'trial_ending':
                # Só envia se ainda não assinou
                if row['plano_ativo']:
                    _marcar_email_enviado(row['id'])
                    continue
                assunto, html = _email_trial_ending(nome, plano, trial_end)
            elif tipo == 'trial_expirado':
                if row['plano_ativo']:
                    _marcar_email_enviado(row['id'])
                    continue
                assunto, html = _email_trial_expirado(nome, plano)
            else:
                _marcar_email_enviado(row['id'])
                continue

            ok = _pubshow_enviar_email(email, assunto, html)
            if ok:
                _marcar_email_enviado(row['id'])
                log.info('[PUBSHOW email] Enviado "%s" para %s (id=%d)', tipo, email, row['id'])
            else:
                log.warning('[PUBSHOW email] Falhou "%s" para %s', tipo, email)
        except Exception as e:
            log.error('[PUBSHOW email] Erro ao processar id=%d tipo=%s: %s', row['id'], tipo, e)


def _marcar_email_enviado(queue_id: int):
    """Marca um email da fila como enviado."""
    try:
        conn = get_pubshow_db()
        conn.execute(
            'UPDATE pubshow_email_queue SET sent_at=? WHERE id=?',
            (datetime.now().isoformat(), queue_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error('[PUBSHOW email] Erro ao marcar enviado id=%d: %s', queue_id, e)


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


def _happy_hour_desconto(b):
    """Retorna o percentual de desconto ativo no momento (0 se plano não permite ou não há happy hour)."""
    if not _plano_permite(b, 'happy_hour'):
        return 0
    import json as _json
    try:
        hh = _json.loads(b['happy_hour_json'] or 'null')
    except Exception:
        return 0
    if not hh:
        return 0
    agora = datetime.now()
    hora_str = agora.strftime('%H:%M')
    wd = agora.weekday()  # 0=segunda … 6=domingo
    # Converte domingo: Python usa 6, mas UI usa 0 pra domingo — normaliza para 0-6 (0=Dom)
    wd_ui = (wd + 1) % 7  # 0=Dom, 1=Seg … 6=Sáb
    dias = hh.get('dias', [0, 1, 2, 3, 4, 5, 6])
    if wd_ui not in dias:
        return 0
    ini = hh.get('ini', '00:00')
    fim = hh.get('fim', '23:59')
    if ini <= hora_str <= fim:
        return int(hh.get('desconto', 0))
    return 0


def _precos_do_bar(b):
    """Retorna preços customizados + Happy Hour aplicado."""
    import json
    try:
        custom = json.loads(b['precos_custom'] or '{}')
    except Exception:
        custom = {}
    desconto_pct = _happy_hour_desconto(b)
    tipos = {}
    for k, v in TIPOS_PEDIDO.items():
        t = dict(v)
        if k in custom:
            t['preco'] = float(custom[k])
        if desconto_pct > 0:
            preco_original = t['preco']
            t['preco'] = round(preco_original * (1 - desconto_pct / 100), 2)
            t['preco_original'] = preco_original
            t['happy_hour'] = True
        tipos[k] = t
    return tipos, desconto_pct


def _limite_atingido(b, ip):
    """Verifica se o IP já atingiu o limite de pedidos na última hora."""
    limite = b['limite_pedidos_hora'] or 10
    if limite <= 0:
        return False
    conn = get_pubshow_db()
    try:
        count = conn.execute(
            '''SELECT COUNT(*) FROM pubshow_pedidos
               WHERE business_id=? AND ip_cliente=?
               AND created_at >= datetime("now", "-1 hours")''',
            (b['id'], ip)
        ).fetchone()[0]
        return count >= limite
    except Exception:
        return False  # em caso de erro, não bloqueia o pedido
    finally:
        conn.close()  # A4 fix: sempre fecha a conexão

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
                              headers=_asaas_headers(), json=data, timeout=4)
        return r.json()
    except Exception as e:
        log.debug('[PUBSHOW] Asaas timeout/erro: %s %s — %s', method, endpoint, e)
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


def _pubshow_notify_bar(b, mensagem: str):
    """Envia notificação WhatsApp para o dono do bar via Evolution API.
    Silencioso — nunca levanta exceção.
    """
    if not _plano_permite(b, 'whatsapp'):
        return
    if not b.get('whatsapp_notif'):
        return
    telefone = (b.get('telefone') or '').strip()
    if not telefone:
        return
    evo_url = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    instance = os.environ.get('PUBSHOW_WA_INSTANCE', '')
    if not evo_url or not evo_key or not instance:
        return
    digits = re.sub(r'\D', '', telefone)
    if not digits.startswith('55'):
        digits = '55' + digits
    try:
        _requests.post(
            f'{evo_url}/message/sendText/{instance}',
            json={'number': digits + '@s.whatsapp.net', 'text': mensagem},
            headers={'apikey': evo_key},
            timeout=8
        )
    except Exception as ex:
        log.warning('[PUBSHOW] WhatsApp notify error: %s', ex)


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
        # Pega um pool dos mais populares e embaralha — a TV nunca começa
        # sempre pelas mesmas músicas (pedido do dono: aleatório dentro do canal).
        videos = conn.execute(
            'SELECT * FROM pubshow_videos WHERE categoria=? AND ativo=1 ORDER BY ordem, views_milhoes DESC LIMIT ?',
            (cat, limit)
        ).fetchall()
        videos = [dict(v) for v in videos]
        random.shuffle(videos)

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
            conn2.execute("UPDATE pubshow_businesses SET ultimo_acesso=datetime('now','-3 hours') WHERE id=?", (b['id'],))
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


# ── RECUPERAÇÃO DE SENHA ───────────────────────────────────────────────────────

def _pubshow_send_wa(telefone: str, mensagem: str) -> bool:
    """Envia mensagem WhatsApp via Evolution API. Retorna True se enviou."""
    evo_url  = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key  = os.environ.get('EVOLUTION_API_KEY', '')
    instance = os.environ.get('PUBSHOW_WA_INSTANCE', '')
    if not evo_url or not evo_key or not instance:
        return False
    digits = re.sub(r'\D', '', telefone)
    if not digits.startswith('55'):
        digits = '55' + digits
    try:
        r = _requests.post(
            f'{evo_url}/message/sendText/{instance}',
            json={'number': digits + '@s.whatsapp.net', 'text': mensagem},
            headers={'apikey': evo_key},
            timeout=8
        )
        return r.status_code < 300
    except Exception as ex:
        log.warning('[PUBSHOW] WA send error: %s', ex)
        return False


@pubshow_bp.route('/recuperar', methods=['GET', 'POST'])
def recuperar_senha():
    msg  = ''
    erro = ''
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        conn  = get_pubshow_db()
        b = conn.execute(
            'SELECT * FROM pubshow_businesses WHERE email=?', (email,)
        ).fetchone()
        if not b:
            # Resposta genérica — não vaza se e-mail existe
            msg = 'Se este e-mail estiver cadastrado, você receberá o link de redefinição no WhatsApp.'
        else:
            token   = _gerar_code(32)
            expira  = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute(
                'UPDATE pubshow_businesses SET reset_token=?, reset_expires=? WHERE id=?',
                (token, expira, b['id'])
            )
            conn.commit()
            _base = os.environ.get('BASE_URL', 'https://www.4kitem.com.br')
            link = f'{_base}/pubshow/recuperar/{token}'
            sent = _pubshow_send_wa(
                b['telefone'] or '',
                f'🔐 *PUBSHOW — Redefinição de senha*\n\n'
                f'Olá, {b["nome"]}!\n\n'
                f'Clique no link abaixo para criar uma nova senha. O link expira em 2 horas.\n\n'
                f'{link}\n\n'
                f'Se não foi você que pediu, ignore esta mensagem.'
            )
            if sent:
                msg = 'Link enviado! Verifique o WhatsApp do número cadastrado. O link expira em 2 horas.'
            else:
                # Fallback: mostra o link na tela (modo dev / sem WA configurado)
                msg = f'Link gerado. Acesse: {link}'
        conn.close()
    return render_template('pubshow/recuperar.html', msg=msg, erro=erro)


@pubshow_bp.route('/recuperar/<token>', methods=['GET', 'POST'])
def recuperar_senha_form(token):
    conn = get_pubshow_db()
    b = conn.execute(
        "SELECT * FROM pubshow_businesses WHERE reset_token=? AND reset_expires>?",
        (token, datetime.now().isoformat())
    ).fetchone()
    conn.close()
    if not b:
        return render_template('pubshow/recuperar.html',
                               msg='', erro='Link inválido ou expirado. Solicite um novo.')
    erro = ''
    if request.method == 'POST':
        nova = request.form.get('senha', '')
        conf = request.form.get('confirma', '')
        if len(nova) < 6:
            erro = 'A senha deve ter ao menos 6 caracteres.'
        elif nova != conf:
            erro = 'As senhas não coincidem.'
        else:
            conn2 = get_pubshow_db()
            conn2.execute(
                'UPDATE pubshow_businesses SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?',
                (generate_password_hash(nova), b['id'])
            )
            conn2.commit(); conn2.close()
            return redirect('/pubshow/entrar?ok=senha')
    return render_template('pubshow/recuperar_form.html', token=token, erro=erro)


@pubshow_bp.route('/bem-vindo')
@pubshow_login_required
def bem_vindo():
    """Página de onboarding pós-cadastro — guia o bar pelos primeiros passos."""
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')
    return render_template('pubshow/bem_vindo.html', b=dict(b), planos=PLANOS, canais=CANAIS)


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
        cupom_cod= request.form.get('cupom', '').strip()
        if not all([nome, email, telefone, senha, cpf_cnpj]):
            erro = 'Preencha todos os campos obrigatórios.'
        elif len(senha) < 6:
            erro = 'A senha deve ter ao menos 6 caracteres.'
        elif plano_sel not in PLANOS:
            plano_sel = 'bar'
            erro = ''
        else:
            # ── Anti-fraude: 1 trial por IP e por CPF/CNPJ ───────────────────
            ip_cliente = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
            try:
                conn_chk = get_pubshow_db()
                dup_cpf = conn_chk.execute(
                    'SELECT id FROM pubshow_businesses WHERE cpf_cnpj=? LIMIT 1', (cpf_cnpj,)
                ).fetchone()
                dup_ip = conn_chk.execute(
                    "SELECT id FROM pubshow_businesses WHERE signup_ip=? AND trial_ends IS NOT NULL AND trial_ends > ? LIMIT 1",
                    (ip_cliente, datetime.now().isoformat())
                ).fetchone() if ip_cliente else None
                conn_chk.close()
            except Exception:
                dup_cpf = dup_ip = None
            if dup_cpf:
                erro = 'CPF/CNPJ já cadastrado. Faça login ou entre em contato.'
            elif dup_ip:
                erro = 'Já existe um trial ativo neste dispositivo. Aguarde o período encerrar.'
            else:
                try:
                    code  = _gerar_code()
                    jtoken = _gerar_jukebox_token()
                    # Aplica cupom — estende trial se válido
                    trial_dias = 7
                    cupom_dados = _aplicar_cupom(cupom_cod) if cupom_cod else None
                    if cupom_dados and cupom_dados['tipo'] == 'trial':
                        trial_dias = int(cupom_dados['valor'])
                    trial = (datetime.now() + timedelta(days=trial_dias)).isoformat()
                    conn = get_pubshow_db()
                    conn.execute(
                        '''INSERT INTO pubshow_businesses
                           (nome, tipo, email, telefone, cpf_cnpj, password_hash, code,
                            plano, plano_ativo, canal_atual, trial_ends, jukebox_token, signup_ip)
                           VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?)''',
                        (nome, tipo, email, telefone, cpf_cnpj,
                         generate_password_hash(senha), code, plano_sel, 'rock', trial, jtoken, ip_cliente)
                    )
                    conn.commit()
                    b = conn.execute('SELECT * FROM pubshow_businesses WHERE email=?', (email,)).fetchone()
                    conn.close()
                    session['pub_business_id']   = b['id']
                    session['pub_business_nome'] = b['nome']
                    session['pub_canal']         = b['canal_atual']
                    # Agenda sequência de emails de onboarding
                    try:
                        _agendar_emails_onboarding(b['id'], trial)
                    except Exception:
                        pass
                    return redirect('/pubshow/bem-vindo')
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
    if not b:
        conn.close()
        return 'Estabelecimento não encontrado', 404
    import json as _json
    canal_key = b['canal_atual'] or 'rock'
    canal     = CANAIS.get(canal_key, CANAIS['rock'])
    videos    = _videos_do_canal(canal_key)
    # 1) Filtra canais pelo plano do bar (grupos_tv)
    canais_plano = _plano_canais_permitidos(dict(b))
    # 2) Filtra pelos temas que o bar habilitou dentro do que o plano permite
    try:    temas_hab = _json.loads(b['temas_habilitados'] or 'null')
    except: temas_hab = None
    if temas_hab:
        canais_tv = {k: v for k, v in canais_plano.items() if k in temas_hab}
        # Garante que o canal atual sempre aparece
        if canal_key not in canais_tv:
            canais_tv[canal_key] = CANAIS.get(canal_key, CANAIS['rock'])
    else:
        canais_tv = canais_plano
    try:    anuncios = _json.loads(b['anuncios_json'] or '[]')
    except: anuncios = []

    # Slides do sistema — obrigatórios em TODAS as TVs, exibidos a cada 10 min via JS
    try:
        _ss_rows = conn.execute(
            'SELECT * FROM pubshow_slides_sistema WHERE ativo=1 ORDER BY ordem, id'
        ).fetchall()
        slides_sistema = [dict(s) for s in _ss_rows]
    except Exception:
        slides_sistema = []

    conn.close()

    # Gera QR do Jukebox server-side — mais confiável que API externa
    _jk_token = b['jukebox_token'] or b['code']
    _jk_url   = f"https://www.4kitem.com.br/pubshow/jukebox/{_jk_token}"
    try:
        import qrcode as _qrcode, io as _io, base64 as _base64
        _qr = _qrcode.QRCode(version=None,
                              error_correction=_qrcode.constants.ERROR_CORRECT_M,
                              box_size=7, border=2)
        _qr.add_data(_jk_url)
        _qr.make(fit=True)
        _img = _qr.make_image(fill_color='black', back_color='white')
        _buf = _io.BytesIO()
        _img.save(_buf, format='PNG')
        tv_qr_b64 = _base64.b64encode(_buf.getvalue()).decode()
    except Exception:
        tv_qr_b64 = None

    return render_template('pubshow/tv.html', b=dict(b), canal=canal,
                           canal_key=canal_key, videos=videos, canais=canais_tv,
                           anuncios=anuncios, slides_sistema=slides_sistema,
                           tv_qr_b64=tv_qr_b64, jukebox_url=_jk_url)


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
    b = dict(b)  # converte para dict — sqlite3.Row não tem .get() em Python < 3.12

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
    tipos_bar              = _tipos_disponiveis(b)
    precos_bar, hh_desconto = _precos_do_bar(b)

    # ── Feature gating: filtra tipos pelo plano ───────────────────────────────
    tipos_permitidos = _plano_tipos_permitidos(b)
    if tipos_permitidos is not None:
        precos_bar = {k: v for k, v in precos_bar.items() if k in tipos_permitidos}

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

            if tipo not in tipos_bar or tipo not in precos_bar:
                erro = 'Tipo de pedido inválido.'
            elif not nome_cliente:
                erro = 'Informe seu nome.'
            elif tipo in ('musica_especifica', 'musica_externa') and not youtube_id:
                erro = 'Selecione uma música antes de confirmar.'
            else:
                preco = precos_bar[tipo]['preco']
                usar_pix = bool(b['requer_pix']) and bool(b['pix_key'])
                tipo_nome  = precos_bar[tipo]['nome']
                tipo_emoji = precos_bar[tipo]['emoji']

                if usar_pix:
                    # ── Anti-spam: não cria pedido duplicado em menos de 60s ────────
                    conn_dedup = get_pubshow_db()
                    _dup = conn_dedup.execute(
                        """SELECT id FROM pubshow_pedidos
                           WHERE business_id=? AND ip_cliente=? AND tipo=?
                           AND COALESCE(youtube_id,'')=COALESCE(?,'')
                           AND status IN ('aguardando_pix','pendente')
                           AND created_at >= datetime('now','-60 seconds')
                           LIMIT 1""",
                        (b['id'], ip_cliente, tipo, youtube_id or None)
                    ).fetchone()
                    conn_dedup.close()
                    if _dup:
                        erro = 'Pedido já enviado! Aguarde um momento antes de tentar novamente.'

                if usar_pix and not erro:
                    # ── Incremento de centavos por pedido — facilita identificação no extrato ─
                    conn_off = get_pubshow_db()
                    _offset = conn_off.execute(
                        "SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND date(created_at)=date('now','-3 hours')",
                        (b['id'],)
                    ).fetchone()[0]
                    conn_off.close()
                    preco_final = round(preco + _offset * 0.01, 2)

                    # ── Cria pedido como aguardando_pix (sem asaas_payment_id ainda) ─
                    txid = f'P{b["id"]}T{int(datetime.now().timestamp())}'
                    conn2 = get_pubshow_db()
                    cur = conn2.execute(
                        '''INSERT INTO pubshow_pedidos
                           (business_id, tipo, nome_cliente, mensagem, categoria, status, valor,
                            youtube_id, titulo_pedido, thumb_url, ip_cliente, pix_txid)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (b['id'], tipo, nome_cliente, mensagem, categoria,
                         'aguardando_pix', preco_final,
                         youtube_id or None, titulo_pedido or None, thumb_url or None,
                         ip_cliente, txid)
                    )
                    pedido_id = cur.lastrowid
                    conn2.commit(); conn2.close()

                    # ── Tenta Asaas primeiro (confirmação automática) ──────────
                    pix_qr = ''      # inicializa antes do if/else (Bug #1)
                    pix_payload = '' # evita NameError se algum ramo falhar
                    descricao_pedido = tipo_nome
                    if titulo_pedido:
                        descricao_pedido += f' — {titulo_pedido[:40]}'
                    # Taxa de conveniência: só no caminho Asaas (que tem custo de
                    # transação). O cliente paga +R$1 e o bar recebe o valor cheio
                    # do pedido. No PIX manual (chave do próprio bar) não há taxa.
                    _valor_cobranca = round(preco_final + PIX_TAXA_CONVENIENCIA, 2)
                    asaas_data = _asaas_criar_cobranca_pix_jukebox(b, pedido_id, _valor_cobranca, descricao_pedido)

                    if asaas_data:
                        # Asaas gerou QR — salva payment_id e usa payload do Asaas.
                        # Grava o valor LÍQUIDO do bar: cliente paga item + conveniência,
                        # o Asaas desconta a taxa dele, e o bar recebe o que sobra.
                        # Com os defaults (conveniência = taxa Asaas), o líquido = item cheio.
                        _valor_liquido_bar = round(preco_final + PIX_TAXA_CONVENIENCIA - PIX_TAXA_ASAAS, 2)
                        conn3 = get_pubshow_db()
                        conn3.execute(
                            'UPDATE pubshow_pedidos SET asaas_payment_id=?, pix_payload=?, valor=? WHERE id=?',
                            (asaas_data['payment_id'], asaas_data['payload'], _valor_liquido_bar, pedido_id)
                        )
                        conn3.commit(); conn3.close()
                        pix_qr    = asaas_data['qr_b64']
                        pix_payload = asaas_data['payload']
                    else:
                        # Fallback: EMV manual (confirmar no painel)
                        pix_payload = _pix_emv(
                            b['pix_key'], b['pix_tipo'] or 'telefone',
                            b['pix_nome_recebedor'] or b['nome'],
                            preco_final, txid
                        )
                        pix_qr = _pix_qr_b64(pix_payload)
                        conn3 = get_pubshow_db()
                        conn3.execute(
                            'UPDATE pubshow_pedidos SET pix_payload=? WHERE id=?',
                            (pix_payload, pedido_id)
                        )
                        conn3.commit(); conn3.close()

                    # ── Notificação WhatsApp + Push em background (não bloqueia resposta) ──
                    _hh_txt = f' 🎉 Happy Hour {hh_desconto}% off!' if hh_desconto else ''
                    _bid, _emoji, _tnome, _nome, _preco = b['id'], tipo_emoji, tipo_nome, nome_cliente, preco_final
                    _via_asaas = bool(asaas_data)
                    _bd_copy = dict(b)
                    def _notif_pix():
                        if _via_asaas:
                            # Confirmação automática — o dono não precisa fazer nada
                            _msg = f'🎵 *Novo pedido no Jukebox!*{_hh_txt}\n{_emoji} {_tnome}\n👤 {_nome}\n💰 R$ {_preco:.2f}\n✅ Confirma sozinho assim que o PIX cair.'
                        else:
                            # PIX manual — o dono precisa confirmar no painel
                            _msg = f'🎵 *Novo pedido PIX aguardando!*{_hh_txt}\n{_emoji} {_tnome}\n👤 {_nome}\n💰 R$ {_preco:.2f}\n📋 Acesse o painel para confirmar.'
                        try: _pubshow_notify_bar(_bd_copy, _msg)
                        except: pass
                        try: _enviar_push_pedido(_bid, _emoji, _tnome, _nome, _preco)
                        except: pass
                    _threading.Thread(target=_notif_pix, daemon=True).start()

                    pix_pendente = {
                        'pedido_id':  pedido_id,
                        'payload':    pix_payload,
                        'qr_b64':     pix_qr,
                        # valor = total que o cliente paga (com taxa, se via Asaas)
                        'valor':      _valor_cobranca if asaas_data else preco_final,
                        'valor_item': preco_final,
                        'taxa_conv':  PIX_TAXA_CONVENIENCIA if asaas_data else 0.0,
                        'recebedor':  b['pix_nome_recebedor'] or b['nome'],
                        'tipo_nome':  tipo_nome,
                        'tipo_emoji': tipo_emoji,
                        'via_asaas':  bool(asaas_data),
                    }
                    sucesso = None
                elif not erro:
                    # ── Fluxo direto (sem PIX ou PIX não exigido) ─────────────
                    # Anti-spam: bloqueia duplicata em 60s
                    conn_dedup2 = get_pubshow_db()
                    _dup2 = conn_dedup2.execute(
                        """SELECT id FROM pubshow_pedidos
                           WHERE business_id=? AND ip_cliente=? AND tipo=?
                           AND COALESCE(youtube_id,'')=COALESCE(?,'')
                           AND status IN ('pendente','exibido')
                           AND created_at >= datetime('now','-60 seconds')
                           LIMIT 1""",
                        (b['id'], ip_cliente, tipo, youtube_id or None)
                    ).fetchone()
                    conn_dedup2.close()
                    if _dup2:
                        erro = 'Pedido já enviado! Aguarde antes de tentar novamente.'

                if not usar_pix and not erro:
                    # Aplica offset de centavos se bar tem PIX configurado (manual)
                    if b.get('pix_key'):
                        conn_off2 = get_pubshow_db()
                        _offset2 = conn_off2.execute(
                            "SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND date(created_at)=date('now','-3 hours')",
                            (b['id'],)
                        ).fetchone()[0]
                        conn_off2.close()
                        preco_direto = round(preco + _offset2 * 0.01, 2)
                    else:
                        preco_direto = preco

                    conn2 = get_pubshow_db()
                    conn2.execute(
                        '''INSERT INTO pubshow_pedidos
                           (business_id, tipo, nome_cliente, mensagem, categoria, status, valor,
                            youtube_id, titulo_pedido, thumb_url, ip_cliente)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        (b['id'], tipo, nome_cliente, mensagem, categoria, 'pendente', preco_direto,
                         youtube_id or None, titulo_pedido or None, thumb_url or None, ip_cliente)
                    )
                    pedido_id_sucesso = conn2.lastrowid
                    conn2.commit(); conn2.close()
                    sucesso = tipo

                    # ── Notificação WhatsApp + Push em background (não bloqueia resposta) ──
                    _hh_txt2 = f' 🎉 Happy Hour {hh_desconto}% off!' if hh_desconto else ''
                    _bid2, _emoji2, _tnome2, _nome2, _preco2 = b['id'], tipo_emoji, tipo_nome, nome_cliente, preco_direto
                    _bd_copy2 = dict(b)
                    _msg_extra = (f'\n📝 {mensagem}' if mensagem else '') + (f'\n🎶 {titulo_pedido}' if titulo_pedido else '')
                    def _notif_direto():
                        try: _pubshow_notify_bar(_bd_copy2, f'🎵 *Novo pedido no Jukebox!*{_hh_txt2}\n{_emoji2} {_tnome2}\n👤 {_nome2}{_msg_extra}\n💰 R$ {_preco2:.2f}')
                        except: pass
                        try: _enviar_push_pedido(_bid2, _emoji2, _tnome2, _nome2, _preco2)
                        except: pass
                    _threading.Thread(target=_notif_direto, daemon=True).start()

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

    # PIX cent offset — cada pedido do dia recebe +N centavos para identificação no extrato
    # Ex: 1º pedido = R$5,00 / 2º = R$5,01 / 3º = R$5,02 ...
    pix_offset = 0
    if b.get('pix_key'):
        try:
            pix_offset = conn3.execute(
                "SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND date(created_at)=date('now','-3 hours')",
                (b['id'],)
            ).fetchone()[0]
        except Exception:
            pix_offset = 0

    conn3.close()

    return render_template('pubshow/jukebox.html',
                           b=b, canal=canal,
                           tipos=precos_bar,
                           tipos_disponiveis=tipos_bar,
                           hh_desconto=hh_desconto,
                           fila=[dict(f) for f in fila],
                           sucesso=sucesso, erro=erro,
                           pix_pendente=pix_pendente,
                           total_videos=total_videos,
                           aberto=aberto, motivo_fechado=motivo_fechado,
                           aviso=aviso, token=token,
                           pix_offset=pix_offset,
                           pedido_id_sucesso=locals().get('pedido_id_sucesso'))


@pubshow_bp.route('/jukebox/<token>/ja-paguei/<int:pedido_id>', methods=['POST'])
def jukebox_ja_paguei(token, pedido_id):
    """Cliente avisa que pagou o PIX.
    Se o bar tem 'requer_pix=1' (confirmação manual ativa):
      → pedido fica em 'aguardando_pix', aguarda bar confirmar no painel
      → retorna ok=True com modo='aguardando_bar'
    Se requer_pix=0 (trust-based):
      → entra direto na fila (comportamento original)
    """
    conn = get_pubshow_db()
    b = conn.execute(
        'SELECT id, requer_pix FROM pubshow_businesses WHERE jukebox_token=? OR code=?',
        (token, token)
    ).fetchone()
    if not b:
        conn.close()
        return jsonify({'ok': False, 'error': 'Bar não encontrado'}), 404

    # Verifica se o bar exige confirmação manual de PIX
    if b['requer_pix']:
        # Não move para 'pendente' — bar precisa confirmar no painel
        # Apenas registra que o cliente avisou que pagou
        conn.execute(
            """UPDATE pubshow_pedidos SET pix_cliente_avisou=1
               WHERE id=? AND business_id=? AND status='aguardando_pix'""",
            (pedido_id, b['id'])
        )
        conn.commit(); conn.close()
        return jsonify({'ok': True, 'modo': 'aguardando_bar',
                        'msg': 'Aviso enviado! O bar vai confirmar seu pagamento em instantes.'})

    # Trust-based: sem exigência de PIX — entra direto na fila
    updated = conn.execute(
        """UPDATE pubshow_pedidos SET status='pendente'
           WHERE id=? AND business_id=? AND status='aguardando_pix'""",
        (pedido_id, b['id'])
    ).rowcount
    conn.commit(); conn.close()
    if updated:
        return jsonify({'ok': True, 'modo': 'direto'})
    return jsonify({'ok': False, 'error': 'Pedido não encontrado ou já confirmado'})


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

    # Promoção Relâmpago — verifica se ainda está dentro do prazo
    promo = None
    if b['promo_msg'] and b['promo_expira']:
        if b['promo_expira'] > datetime.now().strftime('%Y-%m-%dT%H:%M:%S'):
            promo = {
                'msg':    b['promo_msg'],
                'emoji':  b['promo_emoji'] or '🍺',
                'expira': b['promo_expira'],
            }
        else:
            # Limpeza automática quando expirou
            conn.execute('UPDATE pubshow_businesses SET promo_msg=NULL, promo_expira=NULL WHERE id=?', (b['id'],))
            conn.commit()

    result = {
        'canal_atual':    b['canal_atual'],
        'jukebox_ativo':  bool(b['jukebox_ativo']),
        'pedido':         dict(pedido_especial) if pedido_especial else None,
        'pedido_musica':  dict(pedido_musica)   if pedido_musica   else None,
        'total_fila':     total_fila,
        'aguardando_pix': aguardando_pix,
        'promo':          promo,
        'skip_seq':       b['skip_seq'] or 0,
    }
    conn.close()
    return jsonify(result)


@pubshow_bp.route('/api/pedido-status/<token>/<int:pedido_id>')
def api_pedido_status(token, pedido_id):
    """Polling do cliente: retorna status + posição na fila + tempo estimado."""
    conn = get_pubshow_db()
    row = conn.execute(
        '''SELECT p.status, p.business_id, p.created_at FROM pubshow_pedidos p
           JOIN pubshow_businesses b ON p.business_id = b.id
           WHERE p.id=? AND (b.jukebox_token=? OR b.code=?)''',
        (pedido_id, token, token)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'not_found'}), 404

    status = row['status']
    posicao = None
    tempo_min = None

    if status == 'pendente':
        # Conta pedidos confirmados ANTES deste na fila (pela data de criação)
        posicao = conn.execute(
            """SELECT COUNT(*) FROM pubshow_pedidos
               WHERE business_id=? AND status='pendente'
               AND created_at <= (SELECT created_at FROM pubshow_pedidos WHERE id=?)
               AND id != ?""",
            (row['business_id'], pedido_id, pedido_id)
        ).fetchone()[0] + 1  # posição 1-indexed

        # Tempo estimado: 4 min por posição + 2 min de overhead do vídeo atual
        # Clips do YouTube duram em média 3-5 min, usando 4 como base
        if posicao == 1:
            tempo_min = None  # 1º = toca quando o atual acabar, sem estimativa fixa
        else:
            tempo_min = (posicao - 1) * 4 + 2  # músicas antes + metade do atual

    conn.close()
    return jsonify({
        'status': status,
        'posicao': posicao,
        'tempo_min': tempo_min
    })


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
    if not code:  # code é obrigatório — sempre enviado pela TV
        conn.close()
        return jsonify({'error': 'unauthorized'}), 401
    b = conn.execute(
        'SELECT id FROM pubshow_businesses WHERE code=?', (code,)
    ).fetchone()
    if not b or b['id'] != pedido['business_id']:
        conn.close()
        return jsonify({'error': 'unauthorized'}), 403
    conn.execute(
        "UPDATE pubshow_pedidos SET status='exibido', exibido_at=datetime('now','-3 hours') WHERE id=?",
        (pedido_id,)
    )
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@pubshow_bp.route('/api/trocar-canal/<code>', methods=['POST'])
def api_trocar_canal(code):
    # Verifica token de TV — usa jukebox_token ou session do painel
    conn = get_pubshow_db()
    b = conn.execute(
        'SELECT id FROM pubshow_businesses WHERE code=? OR jukebox_token=?', (code, code)
    ).fetchone()
    conn.close()
    if not b:
        return jsonify({'error': 'não autorizado'}), 403
    # Aceita da TV (token na URL) ou do painel logado (session)
    if session.get('pub_business_id') and session['pub_business_id'] != b['id']:
        return jsonify({'error': 'não autorizado'}), 403
    data = (request.json or {}) if request.is_json else request.form
    canal = data.get('canal', 'rock')
    if canal not in CANAIS:
        return jsonify({'error': 'canal inválido'}), 400
    conn2 = get_pubshow_db()
    conn2.execute('UPDATE pubshow_businesses SET canal_atual=? WHERE id=?', (canal, b['id']))
    conn2.commit(); conn2.close()
    return jsonify({'ok': True, 'canal': canal})


@pubshow_bp.route('/api/videos/<categoria>')
def api_videos(categoria):
    videos = _videos_do_canal(categoria)
    return jsonify({'videos': videos, 'total': len(videos)})


@pubshow_bp.route('/api/ranking/<code>')
def api_ranking(code):
    """Retorna top músicas pedidas nas últimas 6h para o bar (ranking da noite)."""
    conn = get_pubshow_db()
    b = conn.execute('SELECT id FROM pubshow_businesses WHERE code=?', (code,)).fetchone()
    if not b:
        conn.close()
        return jsonify({'ranking': []})
    rows = conn.execute(
        '''SELECT titulo_pedido, COUNT(*) n
           FROM pubshow_pedidos
           WHERE business_id=? AND titulo_pedido IS NOT NULL
           AND status != "aguardando_pix"
           AND created_at >= datetime("now", "-6 hours")
           GROUP BY titulo_pedido
           ORDER BY n DESC
           LIMIT 5''',
        (b['id'],)
    ).fetchall()
    conn.close()
    return jsonify({'ranking': [dict(r) for r in rows]})


@pubshow_bp.route('/api/buscar-biblioteca')
def api_buscar_biblioteca():
    """Busca na biblioteca curada de vídeos (rápido, sem API externa).
    Se ?bar=<token> for passado, filtra pelos gêneros que o bar liberou.
    Se ?q= vazio → retorna top 150 por popularidade (lista completa inicial).
    Se ?q=termo  → filtra por título/artista (limite 30 resultados).
    """
    import json as _json
    q          = request.args.get('q', '').strip()
    bar_token  = request.args.get('bar', '').strip()
    lista_full = not q  # sem query = carregamento inicial completo

    conn = get_pubshow_db()

    # Descobre gêneros permitidos para este bar (opcional)
    generos_permitidos = None
    if bar_token:
        brow = conn.execute(
            '''SELECT generos_jukebox FROM pubshow_businesses
               WHERE jukebox_token=? OR code=? LIMIT 1''',
            (bar_token, bar_token)
        ).fetchone()
        if brow and brow['generos_jukebox']:
            try:
                generos_permitidos = _json.loads(brow['generos_jukebox'])
            except Exception:
                generos_permitidos = None

    limite = 150 if lista_full else 30

    if lista_full:
        # Carregamento inicial — top N por popularidade
        if generos_permitidos:
            placeholders = ','.join('?' * len(generos_permitidos))
            rows = conn.execute(
                f'''SELECT youtube_id, titulo, artista, categoria, duracao_seg
                    FROM pubshow_videos
                    WHERE ativo=1 AND categoria IN ({placeholders})
                    ORDER BY views_milhoes DESC LIMIT {limite}''',
                tuple(generos_permitidos)
            ).fetchall()
        else:
            rows = conn.execute(
                f'''SELECT youtube_id, titulo, artista, categoria, duracao_seg
                    FROM pubshow_videos WHERE ativo=1
                    ORDER BY views_milhoes DESC LIMIT {limite}'''
            ).fetchall()
    else:
        # Busca por termo
        like = f'%{q}%'
        if generos_permitidos:
            placeholders = ','.join('?' * len(generos_permitidos))
            rows = conn.execute(
                f'''SELECT youtube_id, titulo, artista, categoria, duracao_seg
                    FROM pubshow_videos
                    WHERE ativo=1 AND categoria IN ({placeholders})
                      AND (titulo LIKE ? OR artista LIKE ?)
                    ORDER BY views_milhoes DESC LIMIT {limite}''',
                (*generos_permitidos, like, like)
            ).fetchall()
        else:
            rows = conn.execute(
                f'''SELECT youtube_id, titulo, artista, categoria, duracao_seg
                    FROM pubshow_videos
                    WHERE ativo=1 AND (titulo LIKE ? OR artista LIKE ?)
                    ORDER BY views_milhoes DESC LIMIT {limite}''',
                (like, like)
            ).fetchall()

    conn.close()
    return jsonify({'resultados': [dict(r) for r in rows], 'total': len(rows)})


@pubshow_bp.route('/api/buscar')
def api_buscar():
    """Busca músicas no YouTube via InnerTube API (sem chave)."""
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'resultados': []})
    try:
        # B2: chave configurável via env — fallback para chave pública do cliente web YT
        _yt_key = os.environ.get('YOUTUBE_INNERTUBE_KEY', 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8')
        resp = _requests.post(
            f'https://www.youtube.com/youtubei/v1/search?key={_yt_key}',
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
    # Processa fila de emails pendentes (rate-limited: 1x/10min por processo)
    try:
        _processar_fila_emails()
    except Exception:
        pass
    conn = get_pubshow_db()
    pedidos_hoje = conn.execute(
        '''SELECT * FROM pubshow_pedidos WHERE business_id=?
           AND date(created_at, "-3 hours")=date("now", "-3 hours")
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
    total_pedidos_bar = conn.execute(
        'SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=?', (b['id'],)
    ).fetchone()[0]

    # ── Receita acumulada ─────────────────────────────────────────────────────
    _base_q = "SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos WHERE business_id=? AND status NOT IN ('aguardando_pix')"
    receita_semana = float(conn.execute(
        _base_q + " AND date(created_at,'-3 hours') >= date('now','-3 hours','-6 days')", (b['id'],)
    ).fetchone()[0])
    receita_mes = float(conn.execute(
        _base_q + " AND strftime('%Y-%m', datetime(created_at,'-3 hours')) = strftime('%Y-%m', datetime('now','-3 hours'))", (b['id'],)
    ).fetchone()[0])
    receita_total = float(conn.execute(_base_q, (b['id'],)).fetchone()[0])
    # Últimos 7 dias para mini-gráfico
    dias_raw = conn.execute(
        """SELECT date(created_at,'-3 hours') as dia, COALESCE(SUM(valor),0) as tot
           FROM pubshow_pedidos WHERE business_id=?
           AND status NOT IN ('aguardando_pix')
           AND date(created_at,'-3 hours') >= date('now','-3 hours','-6 days')
           GROUP BY dia ORDER BY dia""",
        (b['id'],)
    ).fetchall()
    receita_7dias = {str(r['dia']): float(r['tot']) for r in dias_raw}

    assinatura = conn.execute(
        'SELECT * FROM pubshow_assinaturas WHERE business_id=?', (b['id'],)
    ).fetchone()
    conn.close()
    import json as _json
    bd = dict(b)
    bd['total_pedidos'] = total_pedidos_bar
    try:    bloqueados_parsed = _json.loads(bd.get('tipos_bloqueados') or '[]')
    except: bloqueados_parsed = []
    try:    precos_parsed = _json.loads(bd.get('precos_custom') or '{}')
    except: precos_parsed = {}
    try:    temas_habilitados = _json.loads(bd.get('temas_habilitados') or 'null')
    except: temas_habilitados = None
    try:    anuncios_parsed = _json.loads(bd.get('anuncios_json') or '[]')
    except: anuncios_parsed = []
    try:    happy_hour = _json.loads(bd.get('happy_hour_json') or 'null')
    except: happy_hour = None
    try:    generos_jukebox = _json.loads(bd.get('generos_jukebox') or 'null')
    except: generos_jukebox = None
    hh_desconto_atual = _happy_hour_desconto(bd)
    # Canais que o plano do bar permite ver/selecionar na TV
    canais_plano = _plano_canais_permitidos(bd)
    return render_template('pubshow/painel.html',
                           b=bd, canais=canais_plano,
                           pedidos_hoje=_pedidos_com_hora_local([dict(p) for p in pedidos_hoje]),
                           total_hoje=total_hoje,
                           fila=_pedidos_com_hora_local([dict(f) for f in fila]),
                           aguardando_pix=_pedidos_com_hora_local([dict(p) for p in aguardando_pix]),
                           tipos=TIPOS_PEDIDO,
                           planos=PLANOS,
                           bloqueados_parsed=bloqueados_parsed,
                           precos_parsed=precos_parsed,
                           temas_habilitados=temas_habilitados,
                           anuncios_parsed=anuncios_parsed,
                           happy_hour=happy_hour,
                           hh_desconto_atual=hh_desconto_atual,
                           generos_jukebox=generos_jukebox,
                           canais_todos=CANAIS,
                           plano_max_anuncios=_plano_max_anuncios(bd),
                           plano_permite_hh=_plano_permite(bd, 'happy_hour'),
                           plano_permite_wa=_plano_permite(bd, 'whatsapp'),
                           plano_permite_analytics=_plano_permite(bd, 'analytics'),
                           receita_semana=receita_semana,
                           receita_mes=receita_mes,
                           receita_total=receita_total,
                           receita_7dias=receita_7dias,
                           now_brt=datetime.utcnow() - timedelta(hours=3),
                           timedelta=timedelta,
                           assinatura=dict(assinatura) if assinatura else None,
                           promo_ativa=bool(bd.get('promo_msg') and bd.get('promo_expira') and bd['promo_expira'] > datetime.now().strftime('%Y-%m-%dT%H:%M:%S')))


@pubshow_bp.route('/painel/fila-json')
@pubshow_login_required
def painel_fila_json():
    """Retorna fila + aguardando_pix como JSON — usado pelo polling do painel sem recarregar a página."""
    b = _get_business()
    if not b:
        return jsonify({'error': 'não autorizado'}), 401
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
           AND date(created_at)=date("now","-3 hours")''',
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
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    canal = request.form.get('canal', 'rock')
    if canal in CANAIS:
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET canal_atual=? WHERE id=?', (canal, b['id']))
        conn.commit(); conn.close()
        session['pub_canal'] = canal
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/promo', methods=['POST'])
@pubshow_login_required
def painel_promo():
    """Inicia ou encerra uma Promoção Relâmpago na TV."""
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    acao = request.form.get('acao', 'iniciar')
    conn = get_pubshow_db()
    if acao == 'encerrar':
        conn.execute('UPDATE pubshow_businesses SET promo_msg=NULL, promo_expira=NULL WHERE id=?', (b['id'],))
    else:
        msg    = request.form.get('promo_msg', '').strip()[:80]
        emoji  = request.form.get('promo_emoji', '🍺').strip()[:4]
        try: minutos = int(request.form.get('promo_minutos', 10) or 10)
        except (ValueError, TypeError): minutos = 10
        minutos = max(1, min(minutos, 120))  # entre 1 e 120 min
        expira  = (datetime.now() + timedelta(minutes=minutos)).strftime('%Y-%m-%dT%H:%M:%S')
        if msg:
            conn.execute('UPDATE pubshow_businesses SET promo_msg=?, promo_expira=?, promo_emoji=? WHERE id=?',
                         (msg, expira, emoji, b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/push/subscribe', methods=['POST'])
@pubshow_login_required
def painel_push_subscribe():
    """Salva subscription Web Push do dispositivo do bar."""
    import json as _json
    b = _get_business()
    sub = request.json
    if not sub or 'endpoint' not in sub:
        return jsonify({'ok': False, 'erro': 'subscription inválida'}), 400
    conn = get_pubshow_db()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO pubshow_push_subscriptions (business_id, subscription) VALUES (?,?)',
            (b['id'], _json.dumps(sub))
        )
        conn.commit()
    except Exception as _e:
        log.warning('[Push] Erro ao salvar subscription: %s', _e)
    finally:
        conn.close()
    return jsonify({'ok': True})


@pubshow_bp.route('/painel/push/unsubscribe', methods=['POST'])
@pubshow_login_required
def painel_push_unsubscribe():
    """Remove subscription Web Push do dispositivo."""
    import json as _json
    b = _get_business()
    sub = request.json
    if not sub:
        return jsonify({'ok': False}), 400
    conn = get_pubshow_db()
    conn.execute(
        'DELETE FROM pubshow_push_subscriptions WHERE business_id=? AND subscription=?',
        (b['id'], _json.dumps(sub))
    )
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@pubshow_bp.route('/painel/push/test', methods=['POST'])
@pubshow_login_required
def painel_push_test():
    """Envia push de teste para confirmar que está funcionando."""
    b = _get_business()
    if not b: return jsonify({'ok': False}), 401
    _enviar_push_pedido(b['id'], '🧪', 'Teste', 'Sistema PUBSHOW', 0.0)
    return jsonify({'ok': True})


# ── Cupons de desconto ────────────────────────────────────────────────────────

@pubshow_bp.route('/api/validar-cupom/<codigo>')
def api_validar_cupom(codigo):
    """Valida um cupom e retorna seus benefícios (público — sem login)."""
    conn = get_pubshow_db()
    try:
        c = conn.execute(
            "SELECT * FROM pubshow_cupons WHERE codigo=? COLLATE NOCASE AND ativo=1",
            (codigo.strip().upper(),)
        ).fetchone()
    finally:
        conn.close()
    if not c:
        return jsonify({'ok': False, 'erro': 'Cupom inválido ou expirado.'})
    c = dict(c)
    # Verifica validade por data
    if c.get('valido_ate') and c['valido_ate'] < datetime.now().isoformat():
        return jsonify({'ok': False, 'erro': 'Este cupom expirou.'})
    # Verifica limite de usos
    if c.get('max_usos') is not None and c['usos'] >= c['max_usos']:
        return jsonify({'ok': False, 'erro': 'Cupom esgotado.'})
    # Monta mensagem
    if c['tipo'] == 'trial':
        msg = f"🎉 {c['valor']} dias grátis (em vez de 7)"
    elif c['tipo'] == 'desconto_pct':
        msg = f"💸 {c['valor']}% de desconto no primeiro mês"
    else:
        msg = f"✅ Benefício aplicado: {c['descricao'] or c['codigo']}"
    return jsonify({'ok': True, 'tipo': c['tipo'], 'valor': c['valor'],
                    'descricao': c['descricao'], 'msg': msg})


def _aplicar_cupom(codigo: str) -> dict | None:
    """Aplica um cupom — incrementa uso atomicamente. M5 fix: sem race condition."""
    if not codigo:
        return None
    conn = get_pubshow_db()
    try:
        c = conn.execute(
            "SELECT * FROM pubshow_cupons WHERE codigo=? COLLATE NOCASE AND ativo=1",
            (codigo.strip().upper(),)
        ).fetchone()
        if not c:
            return None
        c = dict(c)
        if c.get('valido_ate') and c['valido_ate'] < datetime.now().isoformat():
            return None
        # M5 fix: UPDATE atômico com verificação de max_usos — evita race condition
        # Se max_usos for NULL (ilimitado) ou usos < max_usos, incrementa e retorna 1 row
        if c.get('max_usos') is not None:
            updated = conn.execute(
                "UPDATE pubshow_cupons SET usos=usos+1 WHERE id=? AND usos < max_usos",
                (c['id'],)
            ).rowcount
        else:
            updated = conn.execute(
                "UPDATE pubshow_cupons SET usos=usos+1 WHERE id=?",
                (c['id'],)
            ).rowcount
        if not updated:
            return None  # max_usos atingido por outro request simultâneo
        conn.commit()
        return c
    except Exception as _e:
        log.warning('[Cupom] Erro ao aplicar: %s', _e)
        return None
    finally:
        conn.close()


@pubshow_bp.route('/admin/cupons')
@_admin_required
def admin_cupons():
    conn = get_pubshow_db()
    cupons = [dict(c) for c in conn.execute(
        'SELECT * FROM pubshow_cupons ORDER BY criado_em DESC'
    ).fetchall()]
    conn.close()
    return render_template('pubshow/admin_cupons.html', cupons=cupons)


@pubshow_bp.route('/admin/cupons/criar', methods=['POST'])
@_admin_required
def admin_cupons_criar():
    codigo   = request.form.get('codigo', '').strip().upper()
    descricao= request.form.get('descricao', '').strip()
    tipo     = request.form.get('tipo', 'trial')
    valor    = int(request.form.get('valor', 30))
    max_usos = request.form.get('max_usos', '').strip()
    max_usos = int(max_usos) if max_usos else None
    valido_ate = request.form.get('valido_ate', '').strip() or None
    if not codigo:
        return redirect('/pubshow/admin/cupons')
    conn = get_pubshow_db()
    try:
        conn.execute(
            'INSERT OR IGNORE INTO pubshow_cupons (codigo, descricao, tipo, valor, max_usos, valido_ate) VALUES (?,?,?,?,?,?)',
            (codigo, descricao, tipo, valor, max_usos, valido_ate)
        )
        conn.commit()
    except Exception: pass
    finally: conn.close()
    return redirect('/pubshow/admin/cupons')


@pubshow_bp.route('/admin/cupons/toggle/<int:cid>', methods=['POST'])
@_admin_required
def admin_cupons_toggle(cid):
    conn = get_pubshow_db()
    atual = conn.execute('SELECT ativo FROM pubshow_cupons WHERE id=?', (cid,)).fetchone()
    if atual:
        conn.execute('UPDATE pubshow_cupons SET ativo=? WHERE id=?', (0 if atual[0] else 1, cid))
        conn.commit()
    conn.close()
    return redirect('/pubshow/admin/cupons')


@pubshow_bp.route('/admin/cupons/excluir/<int:cid>', methods=['POST'])
@_admin_required
def admin_cupons_excluir(cid):
    conn = get_pubshow_db()
    conn.execute('DELETE FROM pubshow_cupons WHERE id=?', (cid,))
    conn.commit(); conn.close()
    return redirect('/pubshow/admin/cupons')


@pubshow_bp.route('/painel/push/vapid-key')
@pubshow_login_required
def painel_push_vapid_key():
    """Retorna a chave pública VAPID para o cliente registrar o SW."""
    pub, _ = _vapid_keys()
    return jsonify({'publicKey': pub})


@pubshow_bp.route('/painel/toggle-slides-sistema', methods=['POST'])
@pubshow_login_required
def painel_toggle_slides_sistema():
    """Bar ativa/desativa slides globais do sistema na sua TV."""
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    conn = get_pubshow_db()
    atual = conn.execute('SELECT usar_slides_sistema FROM pubshow_businesses WHERE id=?', (b['id'],)).fetchone()
    novo = 0 if (atual and atual[0]) else 1
    conn.execute('UPDATE pubshow_businesses SET usar_slides_sistema=? WHERE id=?', (novo, b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/anuncios', methods=['POST'])
@pubshow_login_required
def painel_anuncios():
    """Salva slides de propaganda do bar (até 3)."""
    import json as _json
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    slides = []
    for i in range(1, 4):
        titulo    = request.form.get(f'slide_{i}_titulo', '').strip()[:60]
        subtitulo = request.form.get(f'slide_{i}_sub', '').strip()[:80]
        emoji     = request.form.get(f'slide_{i}_emoji', '').strip()[:4]
        cor       = request.form.get(f'slide_{i}_cor', '#ef4444').strip()[:7]
        if titulo:
            slides.append({'titulo': titulo, 'subtitulo': subtitulo, 'emoji': emoji or '📢', 'cor': cor})
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET anuncios_json=? WHERE id=?',
                 (_json.dumps(slides, ensure_ascii=False), b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/upload-slide', methods=['POST'])
@pubshow_login_required
def painel_upload_slide():
    """Upload de imagem como slide de propaganda."""
    import json as _json
    b = dict(_get_business())  # dict() para .get() funcionar em _plano_max_anuncios
    max_slides = _plano_max_anuncios(b)
    try:
        anuncios = _json.loads(b['anuncios_json'] or '[]')
    except Exception:
        anuncios = []
    # Remove slides de imagem existentes para contar total
    imgs_existentes = [s for s in anuncios if s.get('tipo') == 'imagem']
    texto_existentes = [s for s in anuncios if s.get('tipo') != 'imagem']
    total = len(texto_existentes) + len(imgs_existentes)
    if total >= max_slides:
        return redirect('/pubshow/painel?erro=limite_slides')
    f = request.files.get('imagem')
    if not f or not f.filename:
        return redirect('/pubshow/painel')
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return redirect('/pubshow/painel?erro=formato_invalido')
    code = b['code']
    pasta = os.path.join(_SLIDES_DIR, code)
    os.makedirs(pasta, exist_ok=True)
    fname = f'{random.randint(10000,99999)}.{ext}'
    f.save(os.path.join(pasta, fname))
    url = f'/pubshow/slide/{code}/{fname}'
    anuncios.append({'tipo': 'imagem', 'url': url, 'titulo': ''})
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET anuncios_json=? WHERE id=?',
                 (_json.dumps(anuncios, ensure_ascii=False), b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/slide/<code>/<filename>')
def slide_serve(code, filename):
    """Serve imagens de slide do bar."""
    pasta = os.path.join(_SLIDES_DIR, code)
    return send_from_directory(pasta, filename)


# ── Cron externo — ping por UptimeRobot/Railway Cron ────────────────────────

@pubshow_bp.route('/_cron/emails')
def cron_emails():
    """Endpoint para disparar o processamento de emails via cron externo.
    Requer header X-Cron-Key com PUBSHOW_CRON_KEY do environment.
    Se a variável não estiver configurada, qualquer chamada funciona (desenvolvimento)."""
    from flask import Response as _Response
    cron_key = os.environ.get('PUBSHOW_CRON_KEY', '')
    req_key  = request.headers.get('X-Cron-Key', request.args.get('key', ''))
    if cron_key and req_key != cron_key:
        return _Response('Unauthorized', status=401)
    # M8 fix: não reseta _email_last_check — respeita o rate-limit de 10min
    # mesmo quando chamado pelo cron externo, evitando emails duplicados
    global _email_last_check
    try:
        _processar_fila_emails()
        return jsonify({'ok': True, 'ts': datetime.now().isoformat()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@pubshow_bp.route('/_cron/sync-assinaturas')
def cron_sync_assinaturas():
    """Sync proativo de assinaturas com Asaas.
    Verifica o status real de cada assinatura e corrige discrepâncias.
    Deve ser chamado 1x/dia (Railway Cron, UptimeRobot, etc)."""
    cron_key = os.environ.get('PUBSHOW_CRON_KEY', '')
    req_key  = request.headers.get('X-Cron-Key', request.args.get('key', ''))
    if cron_key and req_key != cron_key:
        return jsonify({'error': 'unauthorized'}), 401

    if not os.environ.get('ASAAS_API_KEY'):
        return jsonify({'ok': False, 'msg': 'ASAAS_API_KEY não configurada'}), 200

    resultado = {'verificados': 0, 'ativados': 0, 'suspensos': 0, 'erros': 0}

    try:
        conn = get_pubshow_db()
        assinaturas = conn.execute(
            '''SELECT a.*, b.nome, b.email, b.telefone, b.plano_ativo
               FROM pubshow_assinaturas a
               JOIN pubshow_businesses b ON b.id = a.business_id
               WHERE a.asaas_subscription_id IS NOT NULL
                 AND a.status NOT IN ("cancelado")
               LIMIT 100'''
        ).fetchall()
        conn.close()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

    for ass in assinaturas:
        resultado['verificados'] += 1
        try:
            sub_data = _asaas_req('GET', f'/subscriptions/{ass["asaas_subscription_id"]}')
            asaas_status = sub_data.get('status', '')
            next_due     = sub_data.get('nextDueDate', '')

            conn = get_pubshow_db()
            conn.execute(
                'UPDATE pubshow_assinaturas SET asaas_sub_status=?, proximo_vencimento=?, ultima_sync=? WHERE id=?',
                (asaas_status, next_due, datetime.now().isoformat(), ass['id'])
            )
            conn.commit()
            conn.close()

            if asaas_status == 'ACTIVE' and not ass['plano_ativo']:
                # Asaas diz ativo mas nós temos desativado — reativa
                _assinatura_ativar(ass['business_id'], ass['plano'], ass['asaas_subscription_id'], next_due)
                resultado['ativados'] += 1
                log.info('[PUBSHOW sync] Reativado business_id=%s (Asaas ACTIVE, local inativo)', ass['business_id'])

            elif asaas_status in ('CANCELLED', 'INACTIVE') and ass['plano_ativo']:
                # Asaas diz cancelado mas nós temos ativo — desativa
                _assinatura_cancelar(ass['business_id'])
                resultado['suspensos'] += 1
                log.info('[PUBSHOW sync] Suspenso business_id=%s (Asaas %s, local ativo)', ass['business_id'], asaas_status)

            elif asaas_status == 'OVERDUE':
                # Verifica carência
                conn2 = get_pubshow_db()
                ass2 = conn2.execute('SELECT * FROM pubshow_assinaturas WHERE id=?', (ass['id'],)).fetchone()
                conn2.close()
                if ass2 and not ass2.get('inadimplente_desde'):
                    _assinatura_inadimplente(ass['business_id'], ass['id'])
                    _notify_assinatura(ass['business_id'], 'inadimplente')
                elif ass2 and ass2.get('inadimplente_desde'):
                    _assinatura_inadimplente(ass['business_id'], ass['id'])

        except Exception as e:
            log.error('[PUBSHOW sync] Erro business_id=%s: %s', ass['business_id'], e)
            resultado['erros'] += 1

    log.info('[PUBSHOW sync] Resultado: %s', resultado)
    return jsonify({'ok': True, **resultado, 'ts': datetime.now().isoformat()})


# ── Service Worker do Painel (Push Notifications) ────────────────────────────

@pubshow_bp.route('/painel/manifest.json')
def painel_manifest():  # público — browser busca sem session
    """Web App Manifest para o painel do bar — PWA instalável."""
    import json as _json2
    from flask import Response as _Resp2
    b = _get_business()
    nome = dict(b)['nome'] if b else 'PUBSHOW'
    manifest = {
        'name': f'PUBSHOW — {nome}',
        'short_name': 'PUBSHOW',
        'description': 'Painel de gestão do Jukebox',
        'start_url': '/pubshow/painel',
        'display': 'standalone',
        'background_color': '#08080f',
        'theme_color': '#ef4444',
        'orientation': 'portrait',
        'icons': [
            {'src': '/static/pubshow/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
            {'src': '/static/pubshow/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ]
    }
    return _Resp2(_json2.dumps(manifest), mimetype='application/manifest+json')


@pubshow_bp.route('/sw-painel.js')
def painel_sw():
    """Serve o Service Worker do painel com escopo /pubshow/ permitido.
    Precisa estar em /pubshow/sw-painel.js (não em /static/) para que o
    browser aceite o escopo /pubshow/ via header Service-Worker-Allowed.
    """
    import os as _os
    sw_path = _os.path.join(_os.path.dirname(__file__), 'static', 'pubshow', 'sw-painel.js')
    try:
        with open(sw_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        content = '// sw-painel.js not found'
    from flask import Response as _Resp
    resp = _Resp(content, mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/pubshow/'
    resp.headers['Cache-Control'] = 'no-store'
    return resp


# ── PWA — Manifest dinâmico + Ícone ──────────────────────────────────────────

@pubshow_bp.route('/manifest/<token>.json')
def jukebox_manifest(token):
    """Web App Manifest dinâmico — nome do bar, start_url com o token correto."""
    import json as _json2
    from flask import Response as _Response
    conn = get_pubshow_db()
    b = conn.execute(
        'SELECT nome FROM pubshow_businesses WHERE jukebox_token=? OR code=? LIMIT 1',
        (token, token)
    ).fetchone()
    conn.close()
    nome = (b['nome'] if b else 'Jukebox') or 'Jukebox'
    manifest = {
        'name':             f'Jukebox — {nome}',
        'short_name':       'Jukebox',
        'description':      f'Peça músicas no {nome} direto pelo seu celular',
        'start_url':        f'/pubshow/jukebox/{token}',
        'scope':            f'/pubshow/jukebox/{token}',
        'display':          'standalone',
        'background_color': '#08080f',
        'theme_color':      '#08080f',
        'orientation':      'portrait',
        'lang':             'pt-BR',
        'icons': [
            {'src': f'/pubshow/pwa-icon/{token}/192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
            {'src': f'/pubshow/pwa-icon/{token}/512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
        'screenshots': [],
    }
    return _Response(
        _json2.dumps(manifest, ensure_ascii=False),
        mimetype='application/manifest+json',
        headers={'Cache-Control': 'public, max-age=3600'}
    )


@pubshow_bp.route('/pwa-icon/<token>/<int:size>.png')
def pwa_icon(token, size):
    """Gera ícone PNG para o PWA do Jukebox — fundo escuro + nota musical verde."""
    from flask import Response as _Response
    if size not in (192, 512):
        size = 192
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io as _io2

        img  = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # ── Fundo com cantos arredondados ─────────────────────────────────────
        r = size // 6
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(8, 8, 15))

        # ── Círculo de brilho (glow) ──────────────────────────────────────────
        glow_r = size // 3
        cx = cy = size // 2
        for spread in range(glow_r, 0, -4):
            alpha = int(40 * spread / glow_r)
            draw.ellipse([cx - spread, cy - spread, cx + spread, cy + spread],
                         fill=(74, 222, 128, alpha))

        # ── Nota musical desenhada com formas básicas ─────────────────────────
        # Cabeça da nota (elipse inclinada)
        u = size // 20
        # Nota simples: haste vertical + cabeça oval
        stem_x = cx + u * 3
        stem_y_top = cy - u * 5
        stem_y_bot = cy + u * 3
        head_cx  = cx
        head_cy  = cy + u * 3
        head_rx  = u * 4
        head_ry  = u * 2

        # Haste
        draw.rectangle([stem_x - u, stem_y_top, stem_x + u, stem_y_bot],
                       fill=(74, 222, 128))
        # Cabeça oval
        draw.ellipse([head_cx - head_rx, head_cy - head_ry,
                      head_cx + head_rx, head_cy + head_ry],
                     fill=(74, 222, 128))
        # Bandeirinha
        bx, by = stem_x + u, stem_y_top
        draw.line([(bx, by), (bx + u*3, by + u*2), (bx, by + u*4)],
                  fill=(74, 222, 128), width=max(2, u))

        buf = _io2.BytesIO()
        img.save(buf, format='PNG', optimize=True)
        buf.seek(0)
        return _Response(buf.read(), mimetype='image/png',
                         headers={'Cache-Control': 'public, max-age=86400'})

    except Exception as ex:
        log.warning('[PWA] icon gen error: %s', ex)
        # Fallback: PNG 1×1 transparente
        import base64 as _b64
        px = _b64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ'
            'AABjkB6QAAAABJRU5ErkJggg=='
        )
        return _Response(px, mimetype='image/png')


@pubshow_bp.route('/painel/delete-slide', methods=['POST'])
@pubshow_login_required
def painel_delete_slide():
    """Remove um slide de imagem."""
    import json as _json
    b = _get_business()
    url_del = request.form.get('url', '')
    try:
        anuncios = _json.loads(b['anuncios_json'] or '[]')
    except Exception:
        anuncios = []
    anuncios = [s for s in anuncios if s.get('url') != url_del]
    # Apaga o arquivo físico
    if url_del.startswith('/pubshow/slide/'):
        partes = url_del.split('/')
        if len(partes) >= 5:
            try:
                os.remove(os.path.join(_SLIDES_DIR, partes[3], partes[4]))
            except Exception:
                pass
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET anuncios_json=? WHERE id=?',
                 (_json.dumps(anuncios, ensure_ascii=False), b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/temas', methods=['POST'])
@pubshow_login_required
def painel_temas():
    """Salva quais temas o bar quer habilitar na TV."""
    import json as _json
    b = _get_business()
    selecionados = request.form.getlist('temas')
    # Filtra só keys válidas
    validos = [k for k in selecionados if k in CANAIS]
    # Se marcou tudo ou nada → NULL (= todos habilitados)
    if not validos or len(validos) == len(CANAIS):
        valor = None
    else:
        valor = _json.dumps(validos)
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET temas_habilitados=? WHERE id=?', (valor, b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/happy-hour', methods=['POST'])
@pubshow_login_required
def painel_happy_hour():
    """Salva configuração de Happy Hour do bar."""
    import json as _json
    b = _get_business()
    if not _plano_permite(dict(b), 'happy_hour'):
        return redirect('/pubshow/planos')
    ativo = request.form.get('hh_ativo') == '1'
    if not ativo:
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET happy_hour_json=NULL WHERE id=?', (b['id'],))
        conn.commit(); conn.close()
        return redirect('/pubshow/painel?aba=config')
    ini      = request.form.get('hh_ini', '18:00').strip()
    fim      = request.form.get('hh_fim', '20:00').strip()
    try:
        desconto = max(1, min(80, int(request.form.get('hh_desconto', '20') or '20')))
    except Exception:
        desconto = 20
    dias = [int(d) for d in request.form.getlist('hh_dias') if d.isdigit() and 0 <= int(d) <= 6]
    if not dias:
        dias = [0, 1, 2, 3, 4, 5, 6]
    hh = {'ini': ini, 'fim': fim, 'desconto': desconto, 'dias': dias}
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET happy_hour_json=? WHERE id=?',
                 (_json.dumps(hh), b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/notif', methods=['POST'])
@pubshow_login_required
def painel_notif():
    """Ativa/desativa notificações WhatsApp ao bar."""
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    if not _plano_permite(dict(b), 'whatsapp'):
        return redirect('/pubshow/planos')
    ativo = 1 if request.form.get('whatsapp_notif') else 0
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET whatsapp_notif=? WHERE id=?', (ativo, b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=config')


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
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/qrcode')
@pubshow_login_required
def painel_qrcode():
    """Página de impressão de QR Codes para as mesas."""
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')

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


@pubshow_bp.route('/painel/pular-musica', methods=['POST'])
@pubshow_login_required
def painel_pular_musica():
    """Botão do dono: pula a música/vídeo que está tocando na TV agora.
    Incrementa skip_seq; a TV detecta no polling (~2,5s) e avança a fila."""
    b = _get_business()
    conn = get_pubshow_db()
    conn.execute(
        'UPDATE pubshow_businesses SET skip_seq = COALESCE(skip_seq,0) + 1 WHERE id=?',
        (b['id'],)
    )
    conn.commit(); conn.close()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    return redirect('/pubshow/painel')


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
    if not b: return redirect('/pubshow/entrar')
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
    if not b: return redirect('/pubshow/entrar')

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

    # Gêneros do Jukebox (quais categorias o cliente pode buscar na biblioteca)
    todos_generos = set(CANAIS.keys())
    generos_sel   = [g for g in request.form.getlist('generos_jukebox') if g in todos_generos]
    # NULL = todos liberados; lista = apenas os selecionados
    generos_json  = json.dumps(generos_sel) if generos_sel else None

    conn = get_pubshow_db()
    conn.execute(
        '''UPDATE pubshow_businesses SET
           jukebox_ativo=?, jukebox_hora_ini=?, jukebox_hora_fim=?,
           mensagem_jukebox=?, aviso_jukebox=?, aviso_expira=?,
           limite_pedidos_hora=?, tipos_bloqueados=?, precos_custom=?,
           requer_pix=?, generos_jukebox=?
           WHERE id=?''',
        (jukebox_ativo, hora_ini, hora_fim,
         mensagem or None, aviso or None, aviso_expira,
         limite, json.dumps(bloqueados), json.dumps(precos),
         requer_pix, generos_json, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/senha', methods=['POST'])
@pubshow_login_required
def painel_senha():
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
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
    if not b: return redirect('/pubshow/entrar')
    if not _plano_permite(dict(b), 'analytics'):
        return redirect('/pubshow/planos')
    conn = get_pubshow_db()

    # Receita por período (exclui pedidos aguardando PIX — ainda não pagos)
    receita_hoje = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE business_id=? AND status!="aguardando_pix"
           AND date(created_at)=date("now","-3 hours")''', (b['id'],)
    ).fetchone()[0]
    receita_semana = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now", -7 days)''', (b['id'],)
    ).fetchone()[0]
    receita_mes = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE business_id=? AND status!="aguardando_pix"
           AND strftime("%Y-%m",datetime(created_at,"-3 hours"))=strftime("%Y-%m",date("now","-3 hours"))''', (b['id'],)
    ).fetchone()[0]
    receita_total = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE business_id=? AND status!="aguardando_pix"''', (b['id'],)
    ).fetchone()[0]

    # Top tipos pedidos (último mês — apenas pagos)
    top_tipos = conn.execute(
        '''SELECT tipo, COUNT(*) n, COALESCE(SUM(valor),0) receita
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now", -30 days)
           GROUP BY tipo ORDER BY n DESC''', (b['id'],)
    ).fetchall()

    # Músicas mais pedidas (último mês — apenas pagas)
    top_musicas = conn.execute(
        '''SELECT titulo_pedido, COUNT(*) n FROM pubshow_pedidos
           WHERE business_id=? AND titulo_pedido IS NOT NULL AND status!="aguardando_pix"
           AND created_at>=datetime("now", -30 days)
           GROUP BY titulo_pedido ORDER BY n DESC LIMIT 10''', (b['id'],)
    ).fetchall()

    # Pedidos por dia (últimos 14 dias — apenas pagos)
    por_dia = conn.execute(
        '''SELECT date(created_at,"-3 hours") dia, COUNT(*) n, COALESCE(SUM(valor),0) r
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now", -14 days)
           GROUP BY dia ORDER BY dia''', (b['id'],)
    ).fetchall()

    # Pedidos por hora do dia — últimos 30 dias (horários de pico)
    por_hora_raw = conn.execute(
        '''SELECT CAST(strftime('%H', datetime(created_at, '-3 hours')) AS INTEGER) hora, COUNT(*) n
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now", -30 days)
           GROUP BY hora ORDER BY hora''', (b['id'],)
    ).fetchall()
    # Normaliza para 0..23 com zeros nos horários sem pedido
    horas_dict = {row['hora']: row['n'] for row in por_hora_raw}
    por_hora = [{'hora': h, 'n': horas_dict.get(h, 0)} for h in range(24)]

    # Pedidos por dia da semana (0=Dom ... 6=Sáb) — últimos 30 dias
    por_weekday_raw = conn.execute(
        '''SELECT CAST(strftime('%w', datetime(created_at, '-3 hours')) AS INTEGER) wd, COUNT(*) n
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now", -30 days)
           GROUP BY wd''', (b['id'],)
    ).fetchall()
    dias_semana = ['Dom','Seg','Ter','Qua','Qui','Sex','Sáb']
    wd_dict = {row['wd']: row['n'] for row in por_weekday_raw}
    por_weekday = [{'dia': dias_semana[i], 'n': wd_dict.get(i, 0)} for i in range(7)]

    # Ticket médio e total pedidos do mês
    mes_stats = conn.execute(
        '''SELECT COUNT(*) n, COALESCE(AVG(valor),0) ticket
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND strftime("%Y-%m",datetime(created_at,"-3 hours"))=strftime("%Y-%m",date("now","-3 hours"))''',
        (b['id'],)
    ).fetchone()
    total_pedidos_mes = mes_stats['n'] if mes_stats else 0
    ticket_medio = float(mes_stats['ticket'] if mes_stats else 0)

    # Receita mês anterior
    receita_mes_anterior = float(conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos WHERE business_id=?
           AND status!="aguardando_pix"
           AND strftime("%Y-%m",datetime(created_at,"-3 hours"))=strftime("%Y-%m",date("now","-3 hours","-1 month"))''',
        (b['id'],)
    ).fetchone()[0])

    # Projeção do mês (ritmo atual × dias restantes)
    import calendar as _cal
    _hoje = datetime.utcnow() - timedelta(hours=3)
    _dias_no_mes = _cal.monthrange(_hoje.year, _hoje.month)[1]
    _dia_atual = _hoje.day
    projecao_mes = float(receita_mes) / _dia_atual * _dias_no_mes if _dia_atual > 0 else 0

    # Melhor dia da semana e melhor hora
    melhor_wd = max(por_weekday, key=lambda x: x['n']) if por_weekday else None
    melhor_hora = max(por_hora, key=lambda x: x['n']) if por_hora else None

    conn.close()
    return render_template('pubshow/relatorio.html',
                           b=dict(b),
                           receita_hoje=float(receita_hoje),
                           receita_semana=float(receita_semana),
                           receita_mes=float(receita_mes),
                           receita_total=float(receita_total),
                           receita_mes_anterior=receita_mes_anterior,
                           total_pedidos_mes=total_pedidos_mes,
                           ticket_medio=ticket_medio,
                           projecao_mes=projecao_mes,
                           melhor_wd=melhor_wd,
                           melhor_hora=melhor_hora,
                           top_tipos=[dict(t) for t in top_tipos],
                           top_musicas=[dict(m) for m in top_musicas],
                           por_dia=[dict(d) for d in por_dia],
                           por_hora=por_hora,
                           por_weekday=por_weekday,
                           tipos=TIPOS_PEDIDO,
                           mes_atual=_hoje.strftime('%B de %Y').capitalize())


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


# ── MULTI-LOCAIS (plano Rede) ─────────────────────────────────────────────────

@pubshow_bp.route('/painel/rede')
@pubshow_login_required
def painel_rede():
    """Painel unificado para donos com múltiplos locais (plano Rede)."""
    b = _get_business()
    if not _plano_permite(dict(b), 'multi'):
        return redirect('/pubshow/planos')
    conn = get_pubshow_db()
    # Busca todas as filiais vinculadas a este dono
    filiais = conn.execute(
        '''SELECT b.*,
           (SELECT COUNT(*) FROM pubshow_pedidos p WHERE p.business_id=b.id AND p.status="pendente") fila,
           (SELECT COALESCE(SUM(p.valor),0) FROM pubshow_pedidos p
            WHERE p.business_id=b.id AND p.status!="aguardando_pix"
            AND date(p.created_at)=date("now","-3 hours")) receita_hoje
           FROM pubshow_businesses b
           WHERE b.owner_business_id=? OR b.id=?
           ORDER BY b.nome''',
        (b['id'], b['id'])
    ).fetchall()
    conn.close()
    return render_template('pubshow/painel_rede.html',
                           b=dict(b),
                           filiais=[dict(f) for f in filiais],
                           planos=PLANOS)


@pubshow_bp.route('/painel/rede/nova-filial', methods=['GET', 'POST'])
@pubshow_login_required
def painel_rede_nova_filial():
    """Cadastra uma nova unidade/filial vinculada ao dono atual (plano Rede)."""
    b = dict(_get_business())  # dict() para .get() funcionar abaixo
    if not _plano_permite(b, 'multi'):
        return redirect('/pubshow/planos')
    erro = ''
    if request.method == 'POST':
        nome     = request.form.get('nome', '').strip()
        tipo     = request.form.get('tipo', 'bar')
        email    = request.form.get('email', '').strip().lower()
        telefone = request.form.get('telefone', '').strip()
        senha    = request.form.get('senha', '')
        if not all([nome, email, telefone, senha]):
            erro = 'Preencha todos os campos.'
        elif len(senha) < 6:
            erro = 'Senha mínima de 6 caracteres.'
        else:
            try:
                code   = _gerar_code()
                jtoken = _gerar_jukebox_token()
                trial  = (datetime.now() + timedelta(days=365 * 10)).isoformat()  # plano herdado do dono
                conn = get_pubshow_db()
                conn.execute(
                    '''INSERT INTO pubshow_businesses
                       (nome, tipo, email, telefone, cpf_cnpj, password_hash, code,
                        plano, plano_ativo, canal_atual, trial_ends, jukebox_token, owner_business_id)
                       VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?)''',
                    (nome, tipo, email, telefone,
                     f'filial_{b["id"]}_{_gerar_code(6)}',  # CPF/CNPJ sintético
                     generate_password_hash(senha), code,
                     b.get('plano', 'rede'), 'rock', trial, jtoken, b['id'])
                )
                conn.commit(); conn.close()
                log.info('[PUBSHOW-REDE] Nova filial criada por business_id=%s: %s', b['id'], nome)
                return redirect('/pubshow/painel/rede?ok=filial')
            except Exception as ex:
                if 'UNIQUE' in str(ex):
                    erro = 'Este e-mail já está cadastrado.'
                else:
                    erro = f'Erro ao cadastrar filial: {ex}'
    return render_template('pubshow/painel_rede_nova.html',
                           b=dict(b), erro=erro, tipos=TIPOS_ESTABELECIMENTO)


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
           WHERE date(created_at)=date("now","-3 hours")'''
    ).fetchone()[0]
    receita_hoje = conn.execute(
        '''SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos
           WHERE date(created_at)=date("now","-3 hours") AND status!="aguardando_pix"'''
    ).fetchone()[0]
    # Emails pendentes de enviar
    emails_pendentes = conn.execute(
        '''SELECT COUNT(*) FROM pubshow_email_queue
           WHERE sent_at IS NULL AND scheduled_at <= ?''',
        (datetime.now().isoformat(),)
    ).fetchone()[0]
    emails_total = conn.execute('SELECT COUNT(*) FROM pubshow_email_queue').fetchone()[0]
    conn.close()
    return render_template('pubshow/admin.html',
                           bars=[dict(b) for b in bars],
                           total_videos=total_videos,
                           total_pedidos_hoje=total_pedidos_hoje,
                           receita_hoje=receita_hoje,
                           emails_pendentes=emails_pendentes,
                           emails_total=emails_total,
                           canais=CANAIS, planos=PLANOS,
                           now=datetime.now().isoformat())


@pubshow_bp.route('/admin/bar/<int:bid>')
@_admin_required
def admin_bar(bid):
    import json as _json
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
    try:
        anuncios_list = _json.loads(b['anuncios_json'] or '[]')
    except Exception:
        anuncios_list = []
    return render_template('pubshow/admin_bar.html',
                           b=dict(b), pedidos=[dict(p) for p in pedidos],
                           ass=dict(ass) if ass else None,
                           canais=CANAIS, planos=PLANOS, tipos=TIPOS_PEDIDO,
                           anuncios_list=anuncios_list)


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


@pubshow_bp.route('/admin/bar/<int:bid>/upload-slide', methods=['POST'])
@_admin_required
def admin_bar_upload_slide(bid):
    """Admin envia um slide de imagem para a TV de um bar (venda de mídia)."""
    import json as _json
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE id=?', (bid,)).fetchone()
    if not b:
        conn.close(); return redirect('/pubshow/admin')
    f = request.files.get('imagem')
    if not f or not f.filename:
        conn.close(); return redirect(f'/pubshow/admin/bar/{bid}')
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        conn.close(); return redirect(f'/pubshow/admin/bar/{bid}?erro=formato')
    code = b['code']
    pasta = os.path.join(_SLIDES_DIR, code)
    os.makedirs(pasta, exist_ok=True)
    fname = f'adm_{random.randint(10000,99999)}.{ext}'
    f.save(os.path.join(pasta, fname))
    url = f'/pubshow/slide/{code}/{fname}'
    try:
        anuncios = _json.loads(b['anuncios_json'] or '[]')
    except Exception:
        anuncios = []
    anuncios.append({'tipo': 'imagem', 'url': url, 'titulo': '', 'admin': True})
    conn.execute('UPDATE pubshow_businesses SET anuncios_json=? WHERE id=?',
                 (_json.dumps(anuncios, ensure_ascii=False), bid))
    conn.commit(); conn.close()
    log.info('[PUBSHOW-ADMIN] Slide enviado para bar %s: %s', bid, url)
    return redirect(f'/pubshow/admin/bar/{bid}')


# ── ADMIN — SLIDES DO SISTEMA (globais, aparecem em todas as TVs) ─────────────

@pubshow_bp.route('/admin/slides-sistema')
@_admin_required
def admin_slides_sistema():
    conn = get_pubshow_db()
    slides = conn.execute(
        'SELECT * FROM pubshow_slides_sistema ORDER BY ordem, id'
    ).fetchall()
    conn.close()
    return render_template('pubshow/admin_slides_sistema.html',
                           slides=[dict(s) for s in slides])


@pubshow_bp.route('/admin/slides-sistema/add', methods=['POST'])
@_admin_required
def admin_slides_sistema_add():
    import json as _json
    titulo    = request.form.get('titulo', '').strip()[:60]
    subtitulo = request.form.get('subtitulo', '').strip()[:80]
    emoji     = request.form.get('emoji', '📺').strip()[:4]
    cor       = request.form.get('cor', '#ef4444').strip()[:7]
    if titulo:
        conn = get_pubshow_db()
        conn.execute(
            'INSERT INTO pubshow_slides_sistema (tipo,titulo,subtitulo,emoji,cor) VALUES (?,?,?,?,?)',
            ('texto', titulo, subtitulo, emoji or '📺', cor)
        )
        conn.commit(); conn.close()
    return redirect('/pubshow/admin/slides-sistema')


@pubshow_bp.route('/admin/slides-sistema/upload', methods=['POST'])
@_admin_required
def admin_slides_sistema_upload():
    f = request.files.get('imagem')
    if not f or not f.filename:
        return redirect('/pubshow/admin/slides-sistema')
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return redirect('/pubshow/admin/slides-sistema?erro=formato')
    pasta = os.path.join(_SLIDES_DIR, 'sistema')
    os.makedirs(pasta, exist_ok=True)
    fname = f'sis_{random.randint(100000, 999999)}.{ext}'
    f.save(os.path.join(pasta, fname))
    url = f'/pubshow/slide-sistema/{fname}'
    conn = get_pubshow_db()
    conn.execute(
        'INSERT INTO pubshow_slides_sistema (tipo,url) VALUES (?,?)',
        ('imagem', url)
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/admin/slides-sistema')


@pubshow_bp.route('/slide-sistema/<filename>')
def slide_sistema_serve(filename):
    """Serve imagens dos slides globais do sistema."""
    pasta = os.path.join(_SLIDES_DIR, 'sistema')
    return send_from_directory(pasta, filename)


@pubshow_bp.route('/admin/slides-sistema/toggle/<int:sid>', methods=['POST'])
@_admin_required
def admin_slides_sistema_toggle(sid):
    conn = get_pubshow_db()
    row = conn.execute('SELECT ativo FROM pubshow_slides_sistema WHERE id=?', (sid,)).fetchone()
    if row:
        conn.execute('UPDATE pubshow_slides_sistema SET ativo=? WHERE id=?', (0 if row[0] else 1, sid))
        conn.commit()
    conn.close()
    return redirect('/pubshow/admin/slides-sistema')


@pubshow_bp.route('/admin/slides-sistema/delete/<int:sid>', methods=['POST'])
@_admin_required
def admin_slides_sistema_delete(sid):
    conn = get_pubshow_db()
    row = conn.execute('SELECT url FROM pubshow_slides_sistema WHERE id=?', (sid,)).fetchone()
    if row and row['url'] and '/slide-sistema/' in (row['url'] or ''):
        try:
            fname = row['url'].split('/')[-1]
            os.remove(os.path.join(_SLIDES_DIR, 'sistema', fname))
        except Exception:
            pass
    conn.execute('DELETE FROM pubshow_slides_sistema WHERE id=?', (sid,))
    conn.commit(); conn.close()
    return redirect('/pubshow/admin/slides-sistema')


# ── ADMIN — GESTÃO DE VÍDEOS ───────────────────────────────────────────────────

@pubshow_bp.route('/admin/videos')
@_admin_required
def admin_videos():
    conn = get_pubshow_db()
    videos = conn.execute(
        'SELECT id, youtube_id, titulo, artista, categoria, ativo FROM pubshow_videos ORDER BY categoria, titulo'
    ).fetchall()
    conn.close()

    # Monta por_cat (categoria → lista de vídeos)
    por_cat = {}
    for v in videos:
        cat = v['categoria']
        por_cat.setdefault(cat, []).append(dict(v))

    # Ordem e rótulos dos nichos
    _NICHO_META = {
        'musica':         ('🎵', 'Música'),
        'shows':          ('🎤', 'Shows ao Vivo'),
        'sport':          ('🏆', 'Esporte'),
        'viral':          ('💥', 'Viral'),
        'entretenimento': ('🎭', 'Entretenimento'),
    }
    _NICHO_ORDEM = ['musica', 'shows', 'sport', 'viral', 'entretenimento']

    # Estrutura nicho → [(cat_key, canal_info, vids)]
    por_nicho = []
    cats_mapeadas = set()
    for gkey in _NICHO_ORDEM:
        gemoji, glabel = _NICHO_META.get(gkey, ('📁', gkey))
        cats_nicho = []
        for cat_key, cinfo in CANAIS.items():
            if cinfo.get('grupo') == gkey and isinstance(cinfo.get('cat'), str):
                if cat_key in por_cat:
                    cats_nicho.append((cat_key, cinfo, por_cat[cat_key]))
                    cats_mapeadas.add(cat_key)
        if cats_nicho:
            total_nicho = sum(len(vids) for _, _, vids in cats_nicho)
            por_nicho.append((gkey, gemoji, glabel, cats_nicho, total_nicho))

    # Categorias sem nicho (multi-cat como sport_mix, ou categorias órfãs)
    sem_nicho = [(cat, {'nome': cat, 'emoji': '📁', 'cor': '#64748b'}, vids)
                 for cat, vids in por_cat.items() if cat not in cats_mapeadas]
    if sem_nicho:
        total_sem = sum(len(v) for _, _, v in sem_nicho)
        por_nicho.append(('outros', '📁', 'Outros / Multi-cat', sem_nicho, total_sem))

    # Canais agrupados para os <select> dos modais
    canais_grupos = []
    for gkey in _NICHO_ORDEM:
        gemoji, glabel = _NICHO_META.get(gkey, ('', gkey))
        itens = [(k, v) for k, v in CANAIS.items()
                 if v.get('grupo') == gkey and isinstance(v.get('cat'), str)]
        if itens:
            canais_grupos.append((f'{gemoji} {glabel}', itens))

    return render_template('pubshow/admin_videos.html',
                           por_nicho=por_nicho,
                           total=len(videos),
                           canais_grupos=canais_grupos)


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

    # Guarda anti-"lista toda": Mixes/Radios automáticos do YouTube têm list=RD…
    # São listas infinitas geradas pelo algoritmo. Colar um link de vídeo comum
    # (que vem com &list=RD…) importaria dezenas de vídeos aleatórios sem querer.
    if playlist_id.startswith('RD'):
        return jsonify({'ok': False, 'erro':
            'Esse link é um Mix automático do YouTube (lista RD…), não uma playlist real. '
            'Para um vídeo só, use "Importar em Lote". Para uma playlist de verdade, '
            'cole a URL dela (o ID começa com PL…).'})

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
               (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, qualidade, ativo)
               VALUES (?,?,?,?,?,180,'HD',1)''',
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
    yid = (request.json or {}).get('youtube_id', '')
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


@pubshow_bp.route('/admin/videos/check-bulk', methods=['POST'])
@_admin_required
def admin_videos_check_bulk():
    """Verifica uma lista de youtube_ids em paralelo via oEmbed.
    Retorna {ok: True, resultados: [{youtube_id, ok, erro}]}
    Muito mais rápido que check-one sequencial no cliente."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ids = (request.json or {}).get('ids', [])
    if not ids:
        return jsonify({'ok': False, 'erro': 'Lista vazia'})
    ids = ids[:600]   # limite de segurança

    def _check(yid):
        try:
            r = _requests.get(
                'https://www.youtube.com/oembed',
                params={'url': f'https://www.youtube.com/watch?v={yid}', 'format': 'json'},
                timeout=7
            )
            if r.status_code == 200:
                return {'youtube_id': yid, 'ok': True}
            elif r.status_code == 401:
                return {'youtube_id': yid, 'ok': False, 'erro': 'embed bloqueado'}
            elif r.status_code == 404:
                return {'youtube_id': yid, 'ok': False, 'erro': 'removido'}
            else:
                return {'youtube_id': yid, 'ok': False, 'erro': f'HTTP {r.status_code}'}
        except Exception as e:
            return {'youtube_id': yid, 'ok': False, 'erro': 'timeout'}

    resultados = []
    # 20 threads paralelas — rápido sem sobrecarregar o YouTube
    with ThreadPoolExecutor(max_workers=20) as ex:
        futuros = {ex.submit(_check, yid): yid for yid in ids}
        for fut in as_completed(futuros):
            try:
                resultados.append(fut.result())
            except Exception:
                resultados.append({'youtube_id': futuros[fut], 'ok': False, 'erro': 'erro'})

    quebrados = [r['youtube_id'] for r in resultados if not r['ok']]
    ok_count  = sum(1 for r in resultados if r['ok'])
    log.info('[PUBSHOW admin] check-bulk: %d ok, %d quebrados', ok_count, len(quebrados))
    return jsonify({'ok': True, 'resultados': resultados,
                    'total': len(resultados), 'quebrados': quebrados,
                    'ok_count': ok_count})


def _limpar_videos_quebrados(max_videos=None):
    """Checa todos os vídeos ativos via oEmbed (em paralelo) e desativa
    (ativo=0) os comprovadamente quebrados — 404 (removido) ou 401 (embed
    bloqueado). Timeouts são ignorados (dúvida não remove).

    É o coração do faxineiro: usado tanto pelo botão 1-click do admin quanto
    pelo agendador automático. Retorna (verificados, removidos, ids_removidos).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    conn = get_pubshow_db()
    todos = conn.execute(
        'SELECT youtube_id FROM pubshow_videos WHERE ativo=1'
    ).fetchall()
    conn.close()

    ids = [r['youtube_id'] for r in todos]
    if max_videos:
        ids = ids[:max_videos]
    if not ids:
        return 0, 0, []

    def _check(yid):
        try:
            r = _requests.get(
                'https://www.youtube.com/oembed',
                params={'url': f'https://www.youtube.com/watch?v={yid}', 'format': 'json'},
                timeout=7
            )
            return yid, r.status_code == 200
        except Exception:
            return yid, None   # None = timeout, não remove (dúvida)

    quebrados = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futuros = {ex.submit(_check, yid): yid for yid in ids}
        for fut in as_completed(futuros):
            yid, ok = fut.result()
            if ok is False:   # explicitamente quebrado (não timeout)
                quebrados.append(yid)

    removidos = 0
    if quebrados:
        conn2 = get_pubshow_db()
        ph = ','.join('?' * len(quebrados))
        conn2.execute(f'UPDATE pubshow_videos SET ativo=0 WHERE youtube_id IN ({ph})', quebrados)
        removidos = conn2.execute('SELECT changes()').fetchone()[0]
        conn2.commit()
        conn2.close()

    log.info('[PUBSHOW] faxineiro: %d verificados, %d desativados', len(ids), removidos)
    return len(ids), removidos, quebrados


# ── Faxineiro automático de vídeos ──────────────────────────────────────────
_FAXINEIRO_INICIADO = False

def iniciar_faxineiro_videos(intervalo_horas=12, delay_inicial_seg=300):
    """Inicia uma thread daemon que limpa vídeos quebrados periodicamente,
    sem o dono precisar abrir o admin. Idempotente — só inicia uma vez por
    processo. Desligável via env PUBSHOW_FAXINEIRO=0.
    """
    global _FAXINEIRO_INICIADO
    if _FAXINEIRO_INICIADO:
        return
    if os.environ.get('PUBSHOW_FAXINEIRO', '1') == '0':
        log.info('[PUBSHOW] Faxineiro de vídeos desativado (PUBSHOW_FAXINEIRO=0)')
        return
    _FAXINEIRO_INICIADO = True

    import time as _time
    def _loop():
        # Jitter no boot: evita que múltiplos workers batam no YouTube ao mesmo tempo
        _time.sleep(delay_inicial_seg + random.randint(0, 120))
        while True:
            try:
                _limpar_videos_quebrados()
            except Exception as ex:
                log.error('[PUBSHOW] faxineiro erro: %s', ex)
            _time.sleep(intervalo_horas * 3600)

    _threading.Thread(target=_loop, daemon=True, name='pubshow-faxineiro').start()
    log.info('[PUBSHOW] Faxineiro de vídeos iniciado (a cada %dh)', intervalo_horas)


@pubshow_bp.route('/admin/videos/remover-quebrados', methods=['POST'])
@_admin_required
def admin_videos_remover_quebrados():
    """Verifica TODOS os vídeos ativos em paralelo e remove os quebrados.
    Endpoint de 1-click para limpeza manual da biblioteca."""
    verificados, removidos, quebrados = _limpar_videos_quebrados()
    if not verificados:
        return jsonify({'ok': True, 'removidos': 0, 'msg': 'Nenhum vídeo na biblioteca'})
    return jsonify({'ok': True, 'verificados': verificados,
                    'quebrados': len(quebrados), 'removidos': removidos,
                    'ids_removidos': quebrados})


@pubshow_bp.route('/admin/videos/deletar', methods=['POST'])
@_admin_required
def admin_videos_deletar():
    """Deleta vídeos pelo youtube_id (lista de IDs ruins)."""
    ids = (request.json or {}).get('ids', [])
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
    """Insere vários vídeos de uma vez. Recebe lista de {youtube_id, titulo, artista, categoria}.
    Respeita um teto de vídeos por categoria (padrão 50, via PUBSHOW_MAX_POR_CATEGORIA)."""
    items = request.json or []
    if not isinstance(items, list):
        return jsonify({'ok': False, 'erro': 'Esperado uma lista JSON'})
    MAX_POR_CAT = int(os.environ.get('PUBSHOW_MAX_POR_CATEGORIA', '50'))
    conn = get_pubshow_db()
    inseridos = 0
    duplicados = 0
    erros = 0
    limitados = 0
    _cont_cat = {}  # cache da contagem atual por categoria
    for item in items:
        yid  = (item.get('youtube_id') or '').strip()
        tit  = (item.get('titulo') or '').strip()
        art  = (item.get('artista') or '').strip()
        cat  = (item.get('categoria') or '').strip()
        if not yid or not tit or not cat:
            erros += 1
            continue
        # Teto por categoria: não deixa passar de MAX_POR_CAT
        if cat not in _cont_cat:
            _cont_cat[cat] = conn.execute(
                'SELECT COUNT(*) FROM pubshow_videos WHERE categoria=?', (cat,)
            ).fetchone()[0]
        if _cont_cat[cat] >= MAX_POR_CAT:
            limitados += 1
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
                _cont_cat[cat] += 1
            else:
                duplicados += 1
        except Exception:
            erros += 1
    conn.commit()
    conn.close()
    resp = {'ok': True, 'inseridos': inseridos, 'duplicados': duplicados, 'erros': erros}
    if limitados:
        resp['limitados'] = limitados
        resp['msg_limite'] = f'{limitados} barrados — categoria no limite de {MAX_POR_CAT}.'
    return jsonify(resp)


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


@pubshow_bp.route('/admin/videos/seed-categorias', methods=['POST'])
@_admin_required
def admin_videos_seed_categorias():
    """Busca e importa vídeos para categorias vazias via YouTube Search API.
    Usa queries curadas por categoria — importa até 20 vídeos por categoria.
    Só importa categorias que têm menos de MIN_VIDEOS vídeos ativos."""

    api_key = os.environ.get('YOUTUBE_API_KEY', '')
    if not api_key:
        return jsonify({'ok': False, 'erro': 'YOUTUBE_API_KEY não configurada'})

    MIN_VIDEOS = 5   # só seed se tiver menos que isso

    # Queries curadas por categoria — ajustadas para conteúdo viral de qualidade
    SEED_QUERIES = {
        'rally': [
            'Ken Block Gymkhana official',
            'WRC Rally onboard highlights',
            'rally car drift extreme',
            'rally crash compilation',
        ],
        'wingsuit': [
            'wingsuit proximity flying POV',
            'BASE jump wingsuit extreme',
            'wingsuit flying compilation 4K',
            'wingsuit aerobatics highlights',
        ],
        'aviacao': [
            'fighter jet aerobatics airshow',
            'Blue Angels airshow 4K',
            'F-22 Raptor extreme maneuvers',
            'airshow best moments compilation',
        ],
        'lutas': [
            'UFC best knockouts compilation',
            'UFC highlights official',
            'MMA incredible moments',
            'UFC greatest fights highlights',
        ],
        'skate': [
            'skateboarding best tricks compilation',
            'street skateboarding highlights',
            'Tony Hawk skateboarding',
            'skate best moments viral',
        ],
        'kitesurf': [
            'kitesurfing tricks compilation 4K',
            'kiteboarding best moments',
            'wakeboarding extreme tricks',
            'kitesurf highlights viral',
        ],
        'batidas': [
            'crash compilation funny',
            'epic fail compilation viral',
            'amazing catches fails compilation',
            'sports crash compilation',
        ],
        'standup': [
            'stand up comedy viral moments',
            'comédia stand up brasileiro',
            'Whindersson Nunes stand up',
            'stand up comedy best moments',
        ],
    }

    conn = get_pubshow_db()
    resultado = {}
    total_inseridos = 0

    for categoria, queries in SEED_QUERIES.items():
        # Checa quantos vídeos já existem
        atual = conn.execute(
            'SELECT COUNT(*) FROM pubshow_videos WHERE categoria=? AND ativo=1', (categoria,)
        ).fetchone()[0]

        if atual >= MIN_VIDEOS:
            resultado[categoria] = {'skip': True, 'existentes': atual}
            continue

        inseridos_cat = 0
        vistos = set()

        for query in queries:
            if inseridos_cat >= 20:
                break
            try:
                r = _requests.get(
                    'https://www.googleapis.com/youtube/v3/search',
                    params={
                        'part': 'snippet',
                        'q': query,
                        'type': 'video',
                        'videoDuration': 'medium',   # 4-20 min
                        'videoEmbeddable': 'true',
                        'maxResults': 10,
                        'key': api_key,
                        'relevanceLanguage': 'pt',
                        'safeSearch': 'moderate',
                    },
                    timeout=12
                )
                if r.status_code == 403:
                    resultado[categoria] = {'erro': 'API quota excedida'}
                    break
                if r.status_code != 200:
                    continue

                for item in r.json().get('items', []):
                    if inseridos_cat >= 20:
                        break
                    vid_id = item.get('id', {}).get('videoId', '')
                    sn = item.get('snippet', {})
                    titulo = sn.get('title', '').strip()
                    artista = sn.get('channelTitle', '').strip()

                    if not vid_id or not titulo or vid_id in vistos:
                        continue
                    if any(w in titulo.lower() for w in ['private', 'deleted', '#shorts']):
                        continue
                    vistos.add(vid_id)

                    try:
                        conn.execute(
                            '''INSERT OR IGNORE INTO pubshow_videos
                               (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, qualidade, ativo)
                               VALUES (?,?,?,?,?,180,"HD",1)''',
                            (vid_id, titulo, artista, categoria, query[:30])
                        )
                        if conn.execute('SELECT changes()').fetchone()[0]:
                            inseridos_cat += 1
                            total_inseridos += 1
                    except Exception:
                        pass

            except Exception as e:
                log.error('[PUBSHOW seed] Erro query "%s": %s', query, e)

        conn.commit()
        resultado[categoria] = {'inseridos': inseridos_cat, 'existentes': atual}

    conn.close()
    log.info('[PUBSHOW seed] Total inseridos: %d', total_inseridos)
    return jsonify({'ok': True, 'total': total_inseridos, 'categorias': resultado})


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


@pubshow_bp.route('/painel/cancelar', methods=['GET', 'POST'])
@pubshow_login_required
def painel_cancelar():
    """Cancelamento de assinatura pelo próprio bar."""
    b = _get_business()
    bd = dict(b)
    conn = get_pubshow_db()
    ass = conn.execute(
        'SELECT * FROM pubshow_assinaturas WHERE business_id=?', (b['id'],)
    ).fetchone()
    conn.close()

    if request.method == 'POST':
        confirmacao = request.form.get('confirmar', '').strip().upper()
        if confirmacao != 'CANCELAR':
            return render_template('pubshow/cancelar.html', b=bd, ass=dict(ass) if ass else None,
                                   erro='Digite CANCELAR para confirmar.')
        # Tenta cancelar no Asaas
        if ass and ass.get('asaas_subscription_id'):
            try:
                _asaas_req('DELETE', f'/subscriptions/{ass["asaas_subscription_id"]}', {})
            except Exception as ex:
                log.warning('[PUBSHOW] Erro ao cancelar Asaas: %s', ex)
        # Marca como cancelado no banco
        conn2 = get_pubshow_db()
        if ass:
            conn2.execute(
                "UPDATE pubshow_assinaturas SET status='cancelado' WHERE business_id=?", (b['id'],)
            )
        conn2.execute(
            'UPDATE pubshow_businesses SET plano_ativo=0 WHERE id=?', (b['id'],)
        )
        conn2.commit(); conn2.close()
        log.info('[PUBSHOW] Assinatura cancelada por business_id=%s', b['id'])
        # Notifica admin via WhatsApp
        _pubshow_send_wa(
            os.environ.get('PUBSHOW_ADMIN_PHONE', ''),
            f'⚠️ *PUBSHOW — Cancelamento*\n'
            f'Bar: {bd["nome"]}\n'
            f'E-mail: {bd["email"]}\n'
            f'Plano: {bd.get("plano","—")}\n'
            f'Data: {datetime.now().strftime("%d/%m/%Y %H:%M")}'
        )
        return redirect('/pubshow/painel?ok=cancelamento')

    return render_template('pubshow/cancelar.html', b=bd, ass=dict(ass) if ass else None, erro='')


@pubshow_bp.route('/planos')
def planos():
    return render_template('pubshow/planos.html', planos=PLANOS, canais=CANAIS)


# ── Cobrança Automática — Helpers ─────────────────────────────────────────────

_GRACE_PERIOD_DAYS = 3   # dias de carência após vencimento antes de desativar

def _assinatura_ativar(business_id: int, plano: str, sub_id: str = None,
                       proximo_venc: str = None):
    """Ativa plano de um bar no DB."""
    conn = get_pubshow_db()
    conn.execute(
        'UPDATE pubshow_businesses SET plano=?, plano_ativo=1 WHERE id=?',
        (plano, business_id)
    )
    conn.execute(
        '''UPDATE pubshow_assinaturas
           SET status="ativo", plano=?, inadimplente_desde=NULL,
               asaas_sub_status="ACTIVE", ultima_sync=?, proximo_vencimento=?
           WHERE business_id=?''',
        (plano, datetime.now().isoformat(), proximo_venc, business_id)
    )
    conn.commit(); conn.close()
    log.info('[PUBSHOW] Assinatura ATIVADA: business_id=%s plano=%s', business_id, plano)


def _assinatura_inadimplente(business_id: int, ass_id: int):
    """Marca assinatura como inadimplente. Desativa após carência."""
    conn = get_pubshow_db()
    ass = conn.execute(
        'SELECT inadimplente_desde FROM pubshow_assinaturas WHERE id=?', (ass_id,)
    ).fetchone()
    agora = datetime.now()

    if not ass or not ass['inadimplente_desde']:
        # Primeira vez — registra início da inadimplência, mantém ativo
        conn.execute(
            "UPDATE pubshow_assinaturas SET status='inadimplente', inadimplente_desde=? WHERE id=?",
            (agora.isoformat(), ass_id)
        )
        conn.commit(); conn.close()
        log.info('[PUBSHOW] Assinatura INADIMPLENTE (inicio carencia): business_id=%s', business_id)
    else:
        # Verifica se passou a carência
        try:
            desde = datetime.fromisoformat(ass['inadimplente_desde'][:19])
            if (agora - desde).days >= _GRACE_PERIOD_DAYS:
                conn.execute('UPDATE pubshow_businesses SET plano_ativo=0 WHERE id=?', (business_id,))
                conn.execute(
                    "UPDATE pubshow_assinaturas SET status='suspenso' WHERE id=?", (ass_id,)
                )
                conn.commit()
                log.info('[PUBSHOW] Assinatura SUSPENSA (apos carencia): business_id=%s', business_id)
            else:
                conn.commit()
        except Exception as e:
            log.error('[PUBSHOW] Erro carencia: %s', e)
        conn.close()


def _assinatura_cancelar(business_id: int):
    """Cancela e desativa assinatura."""
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET plano_ativo=0 WHERE id=?', (business_id,))
    conn.execute(
        "UPDATE pubshow_assinaturas SET status='cancelado', asaas_sub_status='CANCELLED' WHERE business_id=?",
        (business_id,)
    )
    conn.commit(); conn.close()
    log.info('[PUBSHOW] Assinatura CANCELADA: business_id=%s', business_id)


def _notify_assinatura(b_id: int, tipo: str):
    """Envia notificações (email + WA) sobre eventos de assinatura."""
    try:
        conn = get_pubshow_db()
        b = conn.execute('SELECT * FROM pubshow_businesses WHERE id=?', (b_id,)).fetchone()
        ass = conn.execute('SELECT * FROM pubshow_assinaturas WHERE business_id=?', (b_id,)).fetchone()
        conn.close()
        if not b:
            return
        bd = dict(b)
        base_url = os.environ.get('BASE_URL', 'https://pubshow.com.br')
        planos_url = f'{base_url}/pubshow/planos'
        nome_plano = {'starter':'Starter','bar':'Bar','pro':'Pro','rede':'Rede'}.get(bd.get('plano','bar'), 'Bar')
        preco_plano = {'starter':'R$ 69,90','bar':'R$ 129,90','pro':'R$ 249,90','rede':'R$ 499,90'}.get(bd.get('plano','bar'), 'R$ 129,90')
        proximo = (ass['proximo_vencimento'] or '')[:10] if ass else ''
        try:
            proximo_fmt = datetime.fromisoformat(proximo).strftime('%d/%m/%Y') if proximo else ''
        except Exception:
            proximo_fmt = proximo

        if tipo == 'pagamento_confirmado':
            assunto = f'✅ Pagamento confirmado — PUBSHOW {nome_plano}'
            corpo = f"""
              <h1 style="color:#f9fafb;font-size:22px;font-weight:800;margin:0 0 8px;">
                Pagamento confirmado! ✅
              </h1>
              <p style="color:#9ca3af;font-size:15px;margin:0 0 24px;line-height:1.6;">
                Oi {bd['nome'].split()[0]}, seu pagamento do plano <strong style="color:#4ade80">{nome_plano}</strong>
                foi confirmado. Jukebox ativo e funcionando!
              </p>
              <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1f0d;border-radius:12px;border:1px solid #166534;margin-bottom:24px;">
                <tr><td style="padding:20px;">
                  <p style="color:#4ade80;font-size:14px;font-weight:700;margin:0 0 8px;">Detalhes da assinatura</p>
                  <p style="color:#e5e7eb;font-size:13px;margin:4px 0;">📦 Plano: <strong>{nome_plano}</strong> — {preco_plano}/mês</p>
                  {'<p style="color:#e5e7eb;font-size:13px;margin:4px 0;">📅 Próximo vencimento: <strong>' + proximo_fmt + '</strong></p>' if proximo_fmt else ''}
                </td></tr>
              </table>
              <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
                <a href="{base_url}/pubshow/painel" style="display:inline-block;background:#4ade80;color:#08080f;font-weight:800;font-size:15px;padding:12px 32px;border-radius:8px;text-decoration:none;">
                  Ir para o Painel →
                </a>
              </td></tr></table>
            """
            wa_msg = (f'✅ *PUBSHOW — Pagamento confirmado*\n'
                      f'Bar: {bd["nome"]}\nPlano: {nome_plano}\n'
                      + (f'Próximo: {proximo_fmt}' if proximo_fmt else ''))

        elif tipo == 'inadimplente':
            assunto = f'⚠️ Pagamento pendente — PUBSHOW {nome_plano}'
            corpo = f"""
              <h1 style="color:#f9fafb;font-size:22px;font-weight:800;margin:0 0 8px;">
                Pagamento pendente ⚠️
              </h1>
              <p style="color:#9ca3af;font-size:15px;margin:0 0 24px;line-height:1.6;">
                Oi {bd['nome'].split()[0]}, identificamos um pagamento pendente da sua assinatura PUBSHOW.
                Você tem <strong style="color:#f59e0b">{_GRACE_PERIOD_DAYS} dias de carência</strong> antes do Jukebox ser pausado.
              </p>
              <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
                <a href="{planos_url}" style="display:inline-block;background:#f59e0b;color:#08080f;font-weight:800;font-size:15px;padding:12px 32px;border-radius:8px;text-decoration:none;">
                  Regularizar pagamento →
                </a>
              </td></tr></table>
            """
            wa_msg = (f'⚠️ *PUBSHOW — Pagamento pendente*\n'
                      f'Bar: {bd["nome"]}\nPlano: {nome_plano}\n'
                      f'Carência: {_GRACE_PERIOD_DAYS} dias')

        elif tipo == 'suspenso':
            assunto = f'🔴 Jukebox pausado — regularize seu pagamento'
            corpo = f"""
              <h1 style="color:#f9fafb;font-size:22px;font-weight:800;margin:0 0 8px;">
                Jukebox pausado 🔴
              </h1>
              <p style="color:#9ca3af;font-size:15px;margin:0 0 24px;line-height:1.6;">
                Oi {bd['nome'].split()[0]}, o período de carência encerrou e seu Jukebox foi temporariamente pausado.
                Regularize o pagamento para reativar imediatamente.
              </p>
              <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
                <a href="{planos_url}" style="display:inline-block;background:#ef4444;color:#fff;font-weight:800;font-size:15px;padding:12px 32px;border-radius:8px;text-decoration:none;">
                  Reativar Jukebox →
                </a>
              </td></tr></table>
            """
            wa_msg = (f'🔴 *PUBSHOW — Jukebox PAUSADO*\n'
                      f'Bar: {bd["nome"]}\nAção necessária: regularizar pagamento')
        else:
            return

        # Envia email
        try:
            _pubshow_enviar_email(bd['email'], assunto,
                                  _email_html_base('PUBSHOW — Assinatura', corpo))
        except Exception as e:
            log.error('[PUBSHOW] Email assinatura erro: %s', e)

        # Envia WA para o bar e para o admin
        try:
            _pubshow_send_wa(re.sub(r'\D', '', bd['telefone'] or ''), wa_msg)
            admin_phone = os.environ.get('PUBSHOW_ADMIN_PHONE', '')
            if admin_phone:
                _pubshow_send_wa(admin_phone,
                                 f'[ADMIN] {wa_msg}\nEmail: {bd["email"]}')
        except Exception as e:
            log.error('[PUBSHOW] WA assinatura erro: %s', e)

    except Exception as e:
        log.error('[PUBSHOW] _notify_assinatura erro: %s', e)


def _sync_assinaturas_asaas():
    """Sync proativo com Asaas — verifica status real de todas as assinaturas ativas.
    Rate-limited: 1x por hora por processo."""
    global _email_last_check
    # Reutiliza rate-limit slot separado via módulo-level var
    pass  # Implementação via _cron/sync-assinaturas (com rate limit próprio)


# ── WEBHOOK ASAAS ─────────────────────────────────────────────────────────────

@pubshow_bp.route('/webhook/asaas', methods=['GET', 'POST'])
def webhook_asaas():
    """Recebe eventos de assinatura do Asaas.

    Configurar no painel Asaas → Configurações → Webhooks:
      URL: https://<dominio>/pubshow/webhook/asaas
      Eventos: PAYMENT_RECEIVED, PAYMENT_CONFIRMED, PAYMENT_OVERDUE,
               SUBSCRIPTION_ACTIVATED, SUBSCRIPTION_CANCELLED, SUBSCRIPTION_INACTIVATED
    """
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200

    token_esp = os.environ.get('ASAAS_WEBHOOK_TOKEN', '').strip().strip('"').strip("'")
    token_rec = (request.headers.get('asaas-access-token', '') or '').strip().strip('"').strip("'")
    # A8 fix: rejeita se token não configurado OU se token não bate
    if not token_esp:
        log.warning('[PUBSHOW] Webhook recebido mas ASAAS_WEBHOOK_TOKEN não configurado — bloqueado')
        return jsonify({'error': 'not configured'}), 403
    if token_rec != token_esp:
        return jsonify({'error': 'unauthorized'}), 401

    dados     = request.get_json(silent=True) or {}
    evento    = dados.get('event', '')
    pagamento = dados.get('payment', {})
    sub_obj   = dados.get('subscription', {})  # para eventos SUBSCRIPTION_*
    ext_ref   = pagamento.get('externalReference', '') or sub_obj.get('externalReference', '')
    sub_id    = pagamento.get('subscription', '') or sub_obj.get('id', '')

    log.info('[PUBSHOW] Webhook Asaas evento=%s ext_ref=%s sub_id=%s', evento, ext_ref, sub_id)

    # ── Pagamento confirmado / assinatura ativada ─────────────────────────────
    if evento in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED', 'SUBSCRIPTION_ACTIVATED'):
        if ext_ref.startswith('pubshow_'):
            partes = ext_ref.split('_')
            if len(partes) >= 3:
                try:
                    bid   = int(partes[1])
                    plano = partes[2]
                    if plano in PLANOS:
                        # Busca próximo vencimento na assinatura Asaas
                        proximo_venc = None
                        if sub_id:
                            try:
                                sub_data = _asaas_req('GET', f'/subscriptions/{sub_id}')
                                proximo_venc = sub_data.get('nextDueDate')
                            except Exception:
                                pass
                        _assinatura_ativar(bid, plano, sub_id, proximo_venc)
                        _notify_assinatura(bid, 'pagamento_confirmado')
                except Exception as ex:
                    log.error('[PUBSHOW] Webhook ativar erro: %s', ex)

    # ── Pagamento em atraso — carência de N dias ──────────────────────────────
    elif evento == 'PAYMENT_OVERDUE':
        if sub_id:
            try:
                conn = get_pubshow_db()
                ass = conn.execute(
                    'SELECT * FROM pubshow_assinaturas WHERE asaas_subscription_id=?', (sub_id,)
                ).fetchone()
                conn.close()
                if ass:
                    bid_overdue = ass['business_id']
                    _assinatura_inadimplente(bid_overdue, ass['id'])
                    # Notifica apenas se acabou de entrar em inadimplência
                    if not ass.get('inadimplente_desde'):
                        _notify_assinatura(bid_overdue, 'inadimplente')
                elif ext_ref.startswith('pubshow_'):
                    partes = ext_ref.split('_')
                    if len(partes) >= 3:
                        bid_overdue = int(partes[1])
                        plano_overdue = partes[2]
                        conn2 = get_pubshow_db()
                        ass2 = conn2.execute(
                            'SELECT * FROM pubshow_assinaturas WHERE business_id=?', (bid_overdue,)
                        ).fetchone()
                        conn2.close()
                        if ass2:
                            _assinatura_inadimplente(bid_overdue, ass2['id'])
            except Exception as ex:
                log.error('[PUBSHOW] Webhook overdue erro: %s', ex)

    # ── Assinatura cancelada / inativada ──────────────────────────────────────
    elif evento in ('SUBSCRIPTION_CANCELLED', 'SUBSCRIPTION_INACTIVATED'):
        target_sub = sub_id or (sub_obj.get('id') if sub_obj else '')
        if target_sub:
            try:
                conn = get_pubshow_db()
                ass = conn.execute(
                    'SELECT * FROM pubshow_assinaturas WHERE asaas_subscription_id=?', (target_sub,)
                ).fetchone()
                conn.close()
                if ass:
                    _assinatura_cancelar(ass['business_id'])
            except Exception as ex:
                log.error('[PUBSHOW] Webhook cancelar erro: %s', ex)

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

    token_esp = os.environ.get('ASAAS_WEBHOOK_TOKEN', '').strip().strip('"').strip("'")
    token_rec = (request.headers.get('asaas-access-token', '') or '').strip().strip('"').strip("'")
    # A8 fix: rejeita se token não configurado OU se token não bate
    if not token_esp:
        log.warning('[PUBSHOW] Webhook recebido mas ASAAS_WEBHOOK_TOKEN não configurado — bloqueado')
        return jsonify({'error': 'not configured'}), 403
    if token_rec != token_esp:
        return jsonify({'error': 'unauthorized'}), 401

    dados     = request.get_json(silent=True) or {}
    evento    = dados.get('event', '')
    pagamento = dados.get('payment', {})
    ext_ref   = pagamento.get('externalReference', '')
    pay_id    = pagamento.get('id', '')

    log.info('[PUBSHOW] Webhook Jukebox: evento=%s ref=%s', evento, ext_ref)

    if evento in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED') and ext_ref.startswith('jukebox_'):
        try:
            _parts = ext_ref.split('_', 1)
            if len(_parts) < 2 or not _parts[1].isdigit():
                log.warning('[PUBSHOW] Webhook jukebox: ext_ref inválida: %s', ext_ref)
                return jsonify({'ok': False}), 200
            pedido_id = int(_parts[1])
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
