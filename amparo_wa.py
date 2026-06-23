"""
amparo_wa.py — Ponte de WhatsApp do Amparo (Evolution API — o mesmo stack do 4kitem)

Decisão: usar o Evolution API (igual SomaJá/MandaZap/VetZap), NÃO o Cloud API oficial.
Motivo: reusa a infra existente, sem fila de templates da Meta, mensagem grátis e
lançamento rápido. Trade-off: é não-oficial (risco de ban) — mitigado por número
DEDICADO ao Amparo e volume moderado (sem disparo em massa).

Envio:  POST {EVOLUTION_API_URL}/message/sendText/{AMPARO_WA_INSTANCE}
Entrada: chega pelo webhook GLOBAL da Evolution (/mandazap/webhook/evolution), que o
         app.py roteia pela instância → amparo.processar_wa_evento(payload).

PLUGÁVEL: se a Evolution não estiver configurada, NÃO quebra nada — apenas registra no
log o que seria enviado e retorna ok=False. Liga sozinho quando o chip conectar.
"""
import os
import logging
import requests as _requests

log = logging.getLogger('amparo.wa')

_EVO_URL  = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
_EVO_KEY  = os.environ.get('EVOLUTION_API_KEY', '')
_EVO_INST = os.environ.get('AMPARO_WA_INSTANCE', 'amparo')


def wa_configurado():
    return bool(_EVO_URL and _EVO_KEY and _EVO_INST)


def _digits(telefone):
    """Só dígitos, com DDI 55 do Brasil."""
    d = ''.join(c for c in str(telefone or '') if c.isdigit())
    if d and not d.startswith('55'):
        d = '55' + d
    return d


def enviar(to, texto, template=None, variaveis=None, lang='pt_BR'):
    """Envia uma mensagem de texto ao paciente via Evolution API.
    (template/variaveis/lang ficam por compatibilidade de assinatura — ignorados aqui,
    porque o Evolution envia texto livre, sem template aprovado.)
    Retorna dict {ok, configurado, ...}.
    """
    d = _digits(to)
    if not d:
        return {'ok': False, 'configurado': wa_configurado(), 'reason': 'sem_destinatario'}

    if not wa_configurado():
        # Modo dev: não envia, só registra a intenção (não derruba o fluxo).
        log.info(f'[WA dev] (Evolution não configurada) enviaria p/ {d}: {texto!r}')
        return {'ok': False, 'configurado': False, 'reason': 'wa_nao_configurado',
                'preview': texto}

    try:
        r = _requests.post(f'{_EVO_URL}/message/sendText/{_EVO_INST}',
                           json={'number': d + '@s.whatsapp.net', 'text': texto},
                           headers={'apikey': _EVO_KEY}, timeout=15)
        ok = r.status_code in (200, 201)
        if not ok:
            log.warning(f'[WA] falha {r.status_code} p/ {d}: {r.text[:200]}')
        return {'ok': ok, 'configurado': True, 'status': r.status_code}
    except Exception as e:
        log.warning(f'[WA] erro ao enviar p/ {d}: {e}')
        return {'ok': False, 'configurado': True, 'reason': str(e)}
