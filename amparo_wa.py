"""
amparo_wa.py — Ponte de WhatsApp do Amparo (Cloud API oficial da Meta)

Decisão de projeto: usamos a API OFICIAL (não automação não-oficial). Produto de saúde
não pode cair, e o custo é irrisório (mensagem de serviço na janela de 24h = grátis;
template de "utilidade" p/ iniciar contato ~R$0,07).

PLUGÁVEL: se as credenciais não estiverem configuradas (WHATSAPP_TOKEN / WHATSAPP_PHONE_ID),
NÃO quebra nada — apenas registra no log o que seria enviado e retorna ok=False. Assim o
Lote 1 roda em dev sem depender da aprovação da Meta; quando o token chegar, passa a enviar.
"""
import os
import logging
import requests as _requests

log = logging.getLogger('amparo.wa')

_TOKEN    = os.environ.get('WHATSAPP_TOKEN', '')
_PHONE_ID = os.environ.get('WHATSAPP_PHONE_ID', '')
_API_VER  = os.environ.get('WHATSAPP_API_VER', 'v21.0')


def wa_configurado():
    return bool(_TOKEN and _PHONE_ID)


def _url():
    return f'https://graph.facebook.com/{_API_VER}/{_PHONE_ID}/messages'


def enviar(to, texto, template=None, variaveis=None, lang='pt_BR'):
    """Envia uma mensagem ao paciente.
    - to: telefone E.164 (ex: 5547999999999)
    - texto: corpo legível (usado no envio de serviço e no log)
    - template: nome de um template de "utilidade" aprovado (p/ iniciar contato fora da janela 24h)
    - variaveis: lista de strings p/ preencher {{1}},{{2}}... do template
    Retorna dict {ok, configurado, ...}.
    """
    to = (to or '').strip()
    if not to:
        return {'ok': False, 'configurado': wa_configurado(), 'reason': 'sem_destinatario'}

    if not wa_configurado():
        # Modo dev: não envia, só registra a intenção (não derruba o fluxo).
        log.info(f'[WA dev] (não configurado) enviaria p/ {to}: {texto!r}')
        return {'ok': False, 'configurado': False, 'reason': 'wa_nao_configurado',
                'preview': texto}

    if template:
        payload = {
            'messaging_product': 'whatsapp', 'to': to, 'type': 'template',
            'template': {'name': template, 'language': {'code': lang}},
        }
        if variaveis:
            payload['template']['components'] = [{
                'type': 'body',
                'parameters': [{'type': 'text', 'text': str(v)} for v in variaveis],
            }]
    else:
        payload = {'messaging_product': 'whatsapp', 'to': to,
                   'type': 'text', 'text': {'body': texto}}

    try:
        r = _requests.post(_url(), headers={'Authorization': f'Bearer {_TOKEN}'},
                           json=payload, timeout=20)
        ok = r.status_code < 300
        if not ok:
            log.warning(f'[WA] falha {r.status_code} p/ {to}: {r.text[:300]}')
        return {'ok': ok, 'configurado': True, 'status': r.status_code}
    except Exception as e:
        log.warning(f'[WA] erro ao enviar p/ {to}: {e}')
        return {'ok': False, 'configurado': True, 'reason': str(e)}
