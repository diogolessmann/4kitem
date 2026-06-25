"""
atendezap.py — Blueprint AtendeZap
Bot de atendimento no WhatsApp que o pequeno negócio LOCAL assina. O dono conecta
o WhatsApp DELE (instância Evolution própria) e o bot responde os clientes —
pronto-por-nicho, sem construtor de fluxo, com ESCAPE PRO HUMANO sagrado.

Princípios: menos é mais · simples pro cliente · escape pro humano · simples pro
dono (escolhe o ramo + 4-5 campos) · pronto-por-nicho · anti-ban (só responde).

Lote 0: blueprint + catálogo de NICHOS.
Lote 1: MOTOR DE RESPOSTA — webhook (roteado pelo global do Evolution em app.py)
        → Gemini responde SÓ com a config do dono (NUNCA inventa) → ESCAPE PRO
        HUMANO (palavra-chave + flag da IA + crise → pausa + avisa o dono).
"""
import os
import json
import time
import random
import logging
import unicodedata
import requests as _requests
from functools import wraps
from datetime import datetime, timedelta
from flask import Blueprint, request, render_template, redirect, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

from atendezap_db import (negocio_por_instancia, tem_acesso, dias_de_trial_restantes,
                          get_or_create_conversa, set_conversa_status, voltar_pro_bot,
                          add_mensagem, contar_inbound_hoje,
                          get_negocio, get_negocio_por_email, criar_negocio,
                          set_bot_ativo, set_evo_ativo, atualizar_config,
                          listar_conversas, get_conversa, listar_mensagens, contar_escaladas,
                          set_asaas_cliente, set_asaas_sub, set_plano_ativo)

log = logging.getLogger('atendezap')

atendezap_bp = Blueprint('atendezap', __name__, url_prefix='/atendezap')


# ── Catálogo pronto-por-nicho ──────────────────────────────────────────────────
# Cada nicho traz: rótulo, terminologia, tom do bot, serviços-semente (pré-preenche
# o onboarding — o dono edita) e as perguntas repetidas que o bot já resolve.
# É SÓ DADO: adicionar um ramo NÃO custa código (1 motor, N fachadas).
#   'saude_mental': True  → ativa a trava CFP (Lote 1): nada de conversa
#   terapêutica por chat; sinal de crise → CVV 188 + escala pro humano.
NICHOS = {
    'salao': {
        'nome': '💇 Salão / Cabeleireiro',
        'cliente': 'cliente', 'clientes': 'clientes',
        'tom': 'simpático e ágil, como uma recepcionista de salão',
        'servicos_seed': 'Corte R$40 · Escova R$45 · Coloração a partir de R$150 · Manicure R$35',
        'perguntas': ['Qual o horário de vocês?', 'Quanto custa o corte/a escova?',
                      'Atende sábado?', 'Precisa agendar ou é por ordem de chegada?',
                      'Onde fica? Tem estacionamento?', 'Aceita PIX?'],
    },
    'barbearia': {
        'nome': '💈 Barbearia',
        'cliente': 'cliente', 'clientes': 'clientes',
        'tom': 'descontraído e direto, papo de barbearia',
        'servicos_seed': 'Corte R$35 · Barba R$25 · Corte + Barba R$55 · Pezinho R$15',
        'perguntas': ['Quanto é o corte?', 'Faz barba?', 'Qual o horário?',
                      'Precisa marcar?', 'Onde fica?', 'Aceita cartão?'],
    },
    'estetica': {
        'nome': '✨ Estética / Beleza',
        'cliente': 'cliente', 'clientes': 'clientes',
        'tom': 'acolhedor e cuidadoso',
        'servicos_seed': 'Limpeza de pele R$120 · Design de sobrancelha R$40 · Depilação a partir de R$60 · Massagem R$100',
        'perguntas': ['Quanto custa a limpeza de pele?', 'Quais procedimentos vocês fazem?',
                      'Qual o horário?', 'Precisa agendar?', 'Onde fica?', 'Formas de pagamento?'],
    },
    'clinica': {
        'nome': '🩺 Clínica / Consultório',
        'cliente': 'paciente', 'clientes': 'pacientes',
        'tom': 'cordial e profissional, como uma secretária de clínica',
        'servicos_seed': 'Consulta R$200 · Retorno sem custo em até 30 dias · Avaliação inicial R$250',
        'perguntas': ['Como marco uma consulta?', 'Qual o valor da consulta?',
                      'Atende convênio?', 'Qual o horário de atendimento?',
                      'Onde fica a clínica?', 'Atende sábado?'],
    },
    'dentista': {
        'nome': '🦷 Dentista / Odontologia',
        'cliente': 'paciente', 'clientes': 'pacientes',
        'tom': 'cordial e tranquilizador',
        'servicos_seed': 'Avaliação sem custo · Limpeza R$120 · Restauração a partir de R$200 · Clareamento R$600',
        'perguntas': ['A avaliação é gratuita?', 'Quanto custa a limpeza?',
                      'Atende emergência/dor?', 'Qual o horário?', 'Onde fica?', 'Parcela no cartão?'],
    },
    'psicologo': {
        'nome': '🧠 Psicólogo / Terapeuta',
        'cliente': 'paciente', 'clientes': 'pacientes',
        'tom': 'respeitoso, discreto e acolhedor — SEM aconselhar ou fazer terapia',
        'servicos_seed': 'Primeira sessão R$180 · Sessão de terapia R$150 · Atendimento online ou presencial',
        'perguntas': ['Como marco uma sessão?', 'Qual o valor da sessão?',
                      'Atende online?', 'Qual o horário?', 'Onde fica o consultório?',
                      'Atende plano de saúde?'],
        'saude_mental': True,   # trava CFP: nada de terapia por chat; crise → CVV 188 + humano
    },
    'nutricao': {
        'nome': '🥗 Nutricionista',
        'cliente': 'paciente', 'clientes': 'pacientes',
        'tom': 'cordial e motivador',
        'servicos_seed': 'Primeira consulta R$200 · Retorno R$120 · Avaliação física R$150',
        'perguntas': ['Como funciona a consulta?', 'Qual o valor?', 'Atende online?',
                      'Qual o horário?', 'Onde fica?', 'Aceita PIX?'],
    },
    'fisioterapia': {
        'nome': '🦴 Fisioterapia',
        'cliente': 'paciente', 'clientes': 'pacientes',
        'tom': 'atencioso e profissional',
        'servicos_seed': 'Avaliação R$150 · Sessão de fisioterapia R$100 · Pacotes com desconto',
        'perguntas': ['Como marco?', 'Qual o valor da sessão?', 'Faz pacote?',
                      'Qual o horário?', 'Onde fica?', 'Atende convênio?'],
    },
    'oficina': {
        'nome': '🔧 Oficina Mecânica / Autopeças',
        'cliente': 'cliente', 'clientes': 'clientes',
        'tom': 'direto e prestativo, linguagem de oficina',
        'servicos_seed': 'Troca de óleo R$120 · Revisão completa R$250 · Alinhamento e balanceamento R$120 · Diagnóstico R$80',
        'perguntas': ['Vocês fazem [serviço]?', 'Quanto custa a revisão/troca de óleo?',
                      'Tem a peça X?', 'Qual o horário?', 'Onde fica?', 'Faz orçamento?'],
    },
    'lavacao': {
        'nome': '🚗 Lavação / Estética Automotiva',
        'cliente': 'cliente', 'clientes': 'clientes',
        'tom': 'ágil e prestativo',
        'servicos_seed': 'Lavagem simples R$40 · Lavagem completa R$70 · Polimento R$250 · Higienização interna R$180',
        'perguntas': ['Quanto é a lavagem completa?', 'Faz polimento?', 'Precisa agendar?',
                      'Quanto tempo demora?', 'Qual o horário?', 'Onde fica?'],
    },
    'petshop': {
        'nome': '🐾 Petshop / Banho e Tosa',
        'cliente': 'tutor', 'clientes': 'tutores',
        'tom': 'carinhoso e prestativo',
        'servicos_seed': 'Banho a partir de R$50 · Tosa R$70 · Banho + Tosa R$100 · Consulta veterinária R$120',
        'perguntas': ['Quanto é o banho e tosa?', 'Precisa agendar?', 'Tem veterinário?',
                      'Qual o horário?', 'Onde fica? Faz leva e traz?', 'Aceita PIX?'],
    },
    'personal': {
        'nome': '🏋️ Personal Trainer / Academia',
        'cliente': 'aluno', 'clientes': 'alunos',
        'tom': 'motivador e direto',
        'servicos_seed': 'Aula experimental gratuita · Avaliação física R$80 · Personal R$100/sessão · Planos mensais',
        'perguntas': ['Como funciona a aula experimental?', 'Qual o valor do plano/da sessão?',
                      'Qual o horário?', 'Onde fica?', 'Faz avaliação física?', 'Aceita PIX?'],
    },
    'advocacia': {
        'nome': '⚖️ Advogado / Escritório',
        'cliente': 'cliente', 'clientes': 'clientes',
        'tom': 'formal e prestativo — SEM dar parecer ou orientação jurídica',
        'servicos_seed': 'Consulta jurídica R$250 · Primeira reunião sem custo · Atendimento por área',
        'perguntas': ['Como marco uma consulta?', 'Vocês atuam em [área]?',
                      'Qual o valor da consulta?', 'Qual o horário?', 'Onde fica?', 'Atende online?'],
    },
    'outros': {
        'nome': '🏢 Outro tipo de negócio',
        'cliente': 'cliente', 'clientes': 'clientes',
        'tom': 'simpático e prestativo',
        'servicos_seed': '',
        'perguntas': ['Qual o horário?', 'Quais serviços vocês oferecem?',
                      'Quanto custa?', 'Onde fica?', 'Formas de pagamento?'],
    },
}


def nicho_cfg(nicho):
    """Config do nicho, com fallback seguro para 'outros'."""
    return NICHOS.get(nicho or 'outros', NICHOS['outros'])


# ══════════════════════════════════════════════════════════════════════════════
# MOTOR DE RESPOSTA (Lote 1)
# ══════════════════════════════════════════════════════════════════════════════
EVO_URL  = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
EVO_KEY  = os.environ.get('EVOLUTION_API_KEY', '')
RATE_DIA = int(os.environ.get('ATENDE_RATE_DIA', '40'))   # cap de msgs/cliente/dia (custo IA)

GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'

# Mensagens fixas (nunca dependem da IA)
MSG_HANDOFF = ('Já vou te encaminhar para um atendente do {nome} 🙂 '
               'Aguarde só um instante que já te respondem por aqui.')
MSG_CRISE   = ('Sinto muito que você esteja passando por isso — você não está sozinho(a). 💛\n'
               'Se quiser conversar agora, ligue *188* (CVV): é gratuito, sigiloso e funciona 24h.\n'
               'Vou avisar a equipe para te dar atenção o quanto antes.')

# Cliente PEDE um humano / reclama / quer fechar → escala na hora (atalho sem IA).
HANDOFF_KEYWORDS = [
    'atendente', 'humano', 'falar com alguem', 'falar com uma pessoa', 'falar com voce',
    'pessoa de verdade', 'gente de verdade', 'quero reclamar', 'reclamacao', 'reclamar',
    'quero cancelar', 'cancelar pedido', 'me liga', 'quero falar com',
]
# Sinais de crise (só nichos saude_mental) — hard-coded, antes de qualquer IA (regra CFP).
CRISE_KEYWORDS = [
    'me matar', 'suicid', 'nao quero mais viver', 'nao aguento mais viver', 'acabar com tudo',
    'me machucar', 'tirar minha vida', 'vontade de morrer', 'queria morrer', 'quero morrer',
    'nao quero viver',
]


def _norm(s):
    """Minúsculas sem acento, p/ casar palavra-chave (atendente == ATENDENTE == atendênte)."""
    base = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode('ascii')
    return base.lower().strip()


def _wa_digits(telefone):
    d = ''.join(c for c in str(telefone) if c.isdigit())
    if d and not d.startswith('55'):
        d = '55' + d
    return d


def _typing_delay(texto):
    """Delay (ms) proporcional ao texto, com jitter — imita digitação humana (anti-ban)."""
    return min(1200 + len(texto or '') * 35, 6000) + random.randint(0, 400)


def wa_send(instance, telefone, texto):
    """Envia texto pela instância Evolution do PRÓPRIO dono, com presence=composing."""
    if not EVO_URL or not EVO_KEY:
        log.warning('[AtendeZap] Evolution não configurada — msg não enviada')
        return False
    try:
        r = _requests.post(
            f'{EVO_URL}/message/sendText/{instance}',
            json={'number': _wa_digits(telefone) + '@s.whatsapp.net', 'text': texto,
                  'options': {'delay': _typing_delay(texto), 'presence': 'composing'}},
            headers={'apikey': EVO_KEY}, timeout=15)
        return r.status_code in (200, 201)
    except Exception as e:
        log.warning(f'[AtendeZap] wa_send erro: {e}')
        return False


def _gemini_call(system, contents, json_mode=False, max_tokens=600, temperature=0.2):
    """Chama o Gemini via REST. Retorna (texto, tokens_in, tokens_out)."""
    body = {'contents': contents,
            'generationConfig': {'temperature': temperature, 'maxOutputTokens': max_tokens}}
    if system:
        body['systemInstruction'] = {'parts': [{'text': system}]}
    if json_mode:
        body['generationConfig']['responseMimeType'] = 'application/json'
    r = _requests.post(_GEMINI_URL.format(model=GEMINI_MODEL),
                       params={'key': GEMINI_KEY}, json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    txt = data['candidates'][0]['content']['parts'][0]['text'].strip()
    um  = data.get('usageMetadata', {}) or {}
    return txt, int(um.get('promptTokenCount', 0)), int(um.get('candidatesTokenCount', 0))


def build_system(negocio):
    """Monta o cérebro do bot a partir SÓ da config do dono. A trava anti-alucinação
    é a regra nº 1: se a info não está aqui, o bot NÃO inventa — escala."""
    cfg     = nicho_cfg(negocio['nicho'])
    nome    = negocio['nome']
    cliente = cfg['cliente']

    linhas = []
    def _add(rotulo, valor):
        if valor and str(valor).strip():
            linhas.append(f'- {rotulo}: {str(valor).strip()}')
    _add('Serviços e preços', negocio['servicos'])
    _add('Horário de funcionamento', negocio['horario'])
    _add('Endereço', negocio['endereco'])
    _add('Formas de pagamento', negocio['pagamentos'])
    _add('Observações', negocio['obs'])
    info = '\n'.join(linhas) or '(nenhuma informação cadastrada ainda)'

    extra_cfp = ''
    if cfg.get('saude_mental'):
        extra_cfp = (
            '\n5. SAÚDE MENTAL (OBRIGATÓRIO): você NÃO é terapeuta. NÃO faça terapia, NÃO dê '
            'conselho emocional ou clínico, NÃO interprete sintomas. Trate APENAS de horário, '
            'valor, local e como marcar. Se houver sofrimento intenso, crise ou menção a se '
            'machucar, NÃO aconselhe: acolha em uma frase, informe o CVV (ligar 188, 24h, '
            'gratuito) e escale (escalar=true).')

    return f'''Você é o atendente virtual do "{nome}". Atende {cfg['clientes']} no WhatsApp, \
com tom {cfg['tom']}.

INFORMAÇÕES DO NEGÓCIO (a ÚNICA fonte de verdade):
{info}

REGRAS (siga à risca):
1. Responda SOMENTE com base nas INFORMAÇÕES acima. NUNCA invente preço, horário, endereço, \
disponibilidade, prazo ou qualquer dado. Se a informação não estiver acima, NÃO chute.
2. Se você não tiver a informação, OU o {cliente} pedir para falar com uma pessoa/atendente, \
OU quiser agendar/marcar/fechar/pagar, OU reclamar, OU for algo fora do atendimento simples → \
defina escalar=true (um humano assume a conversa).
3. Seja breve e natural (1 a 3 frases curtas, tom de WhatsApp). Sem listas longas, sem markdown.
4. Não prometa nada que não esteja nas INFORMAÇÕES.{extra_cfp}

Responda SEMPRE em JSON: {{"resposta": "<o texto a enviar ao cliente, ou vazio se for escalar>", \
"escalar": <true|false>, "motivo": "<curto: por que escalou, ou string vazia>"}}'''


def gerar_resposta(negocio, texto):
    """Pergunta ao Gemini. Falha-segura: qualquer erro/dúvida → escala (nunca inventa)."""
    contents = [{'role': 'user', 'parts': [{'text': texto}]}]
    try:
        raw, _, _ = _gemini_call(build_system(negocio), contents, json_mode=True, max_tokens=400)
        data = json.loads(raw)
        resposta = (data.get('resposta') or '').strip()
        escalar  = bool(data.get('escalar'))
        motivo   = (data.get('motivo') or '').strip()[:80]
        if not resposta and not escalar:
            escalar, motivo = True, 'sem_resposta'
        return {'resposta': resposta, 'escalar': escalar, 'motivo': motivo or 'ia'}
    except Exception as e:
        log.warning(f'[AtendeZap] IA falhou: {e}')
        return {'resposta': '', 'escalar': True, 'motivo': 'erro_ia'}


def _handoff_keyword(low):
    for k in HANDOFF_KEYWORDS:
        if k in low:
            return f'kw:{k}'
    return None


def _escalar(negocio, conversa, telefone, push, ultima_msg, motivo):
    """Escape pro humano: pausa o bot na conversa, avisa o cliente e avisa o dono."""
    instance = negocio['evo_instance']
    set_conversa_status(conversa['id'], 'humano', motivo)
    # avisa o cliente (uma vez — as próximas mensagens ficam silenciosas)
    aviso_cli = MSG_HANDOFF.format(nome=negocio['nome'])
    wa_send(instance, telefone, aviso_cli)
    add_mensagem(conversa['id'], 'out', aviso_cli, escalou=1)
    # avisa o dono no número pessoal (se cadastrou) — do número do negócio pro dele
    alert = (negocio['alert_phone'] or '').strip()
    if alert:
        nome_cli = (push or telefone)
        wa_send(instance, alert,
                f'🔔 *AtendeZap* — {nome_cli} precisa de atendimento.\n'
                f'📱 {telefone}\n'
                f'💬 "{(ultima_msg or "")[:140]}"\n'
                f'(motivo: {motivo})')
    return {'escalado': motivo}


def processar_wa_evento(payload):
    """Núcleo: uma mensagem inbound do cliente → responde (config do dono) OU escala.
    Chamado pelo webhook GLOBAL do Evolution (app.py), roteado por instância 'atende*'.
    Retorna dict (o webhook ignora o valor; útil pra teste)."""
    msg = payload.get('data', payload)
    if isinstance(msg, list):
        msg = msg[0] if msg else {}
    key = (msg.get('key') or {}) if isinstance(msg, dict) else {}
    if key.get('fromMe'):
        return {'ignored': 'fromMe'}
    remote = key.get('remoteJid', '') or ''
    if '@g.us' in remote:                       # ignora grupos
        return {'ignored': 'group'}
    telefone = remote.split('@')[0]
    if not telefone:
        return {'ignored': 'no-phone'}

    instance = str(payload.get('instance')
                   or (msg.get('instance') if isinstance(msg, dict) else '') or '')
    negocio = negocio_por_instancia(instance)
    if not negocio:
        return {'ignored': 'no-negocio'}
    if not negocio['bot_ativo']:                # dono desligou o bot
        return {'ignored': 'bot-off'}
    if not tem_acesso(negocio):                 # sem assinatura/trial → bot fica off
        return {'ignored': 'no-access'}

    push = msg.get('pushName', '') or ''
    m = msg.get('message', {}) or {}
    texto = (m.get('conversation')
             or (m.get('extendedTextMessage') or {}).get('text', '')
             or (m.get('imageMessage') or {}).get('caption', '')
             or '').strip()
    tem_midia = bool(m.get('imageMessage') or m.get('audioMessage') or m.get('documentMessage')
                     or m.get('videoMessage') or m.get('stickerMessage'))

    conversa = get_or_create_conversa(negocio['id'], telefone, push)
    if conversa['status'] == 'humano':          # já com humano → bot não fala por cima
        return {'ignored': 'humano'}

    add_mensagem(conversa['id'], 'in', texto or '[mídia]')
    if contar_inbound_hoje(conversa['id']) > RATE_DIA:   # anti-abuso/custo
        return {'ignored': 'rate'}

    cfg = nicho_cfg(negocio['nicho'])
    low = _norm(texto)

    # 1) Crise (saúde mental) — hard-coded, ANTES da IA (regra CFP)
    if cfg.get('saude_mental') and any(k in low for k in CRISE_KEYWORDS):
        wa_send(negocio['evo_instance'], telefone, MSG_CRISE)
        add_mensagem(conversa['id'], 'out', MSG_CRISE, escalou=1)
        return _escalar(negocio, conversa, telefone, push, texto, 'crise')

    # 2) Mídia sem texto → escala (bot é texto; o dono vê o anexo na própria conversa)
    if tem_midia and not texto:
        return _escalar(negocio, conversa, telefone, push, '[mídia]', 'midia')
    if not texto:
        return {'ignored': 'empty'}

    # 3) Cliente pediu humano / reclamou (atalho sem IA)
    hit = _handoff_keyword(low)
    if hit:
        return _escalar(negocio, conversa, telefone, push, texto, hit)

    # 4) IA responde SÓ com a config; se não souber/for complexo → escala
    r = gerar_resposta(negocio, texto)
    if r['escalar']:
        return _escalar(negocio, conversa, telefone, push, texto, r['motivo'])
    wa_send(negocio['evo_instance'], telefone, r['resposta'])
    add_mensagem(conversa['id'], 'out', r['resposta'])
    return {'ok': True, 'resposta': r['resposta']}


# ══════════════════════════════════════════════════════════════════════════════
# ONBOARDING + PAINEL + CONECTAR NÚMERO (Lote 2)
# ══════════════════════════════════════════════════════════════════════════════
MZ_PUBLIC_URL    = os.environ.get('MZ_PUBLIC_URL', 'https://4kitem.com.br').rstrip('/')
MZ_WEBHOOK_TOKEN = os.environ.get('MZ_WEBHOOK_TOKEN', '')


def atende_login_required(f):
    @wraps(f)
    def _wrap(*a, **k):
        if not session.get('atende_business_id'):
            return redirect('/atendezap/entrar')
        return f(*a, **k)
    return _wrap


def _negocio_logado():
    return get_negocio(session.get('atende_business_id'))


def _evo_extract_qr(data):
    """Acha o QR base64 em vários formatos de resposta da Evolution (v1/v2)."""
    if not isinstance(data, dict):
        return ''
    qr = data.get('base64') or data.get('qrcode', '')
    if isinstance(qr, dict):
        qr = qr.get('base64', '') or qr.get('code', '')
    if not qr:
        for k in ('instance', 'qrcode'):
            inner = data.get(k, {})
            if isinstance(inner, dict):
                qr = inner.get('base64', '') or inner.get('qrcode', '')
                if isinstance(qr, dict):
                    qr = qr.get('base64', '')
                if qr:
                    break
    return qr or ''


def _set_instance_webhook(instance):
    """Aponta o webhook da instância pro endpoint GLOBAL do Evolution (que roteia
    'atende*' pra cá). Best-effort: se falhar, o número só não recebe — nada quebra."""
    url = f'{MZ_PUBLIC_URL}/mandazap/webhook/evolution' + (f'?token={MZ_WEBHOOK_TOKEN}' if MZ_WEBHOOK_TOKEN else '')
    try:
        _requests.post(f'{EVO_URL}/webhook/set/{instance}',
                       headers={'apikey': EVO_KEY, 'Content-Type': 'application/json'},
                       json={'webhook': {'enabled': True, 'url': url,
                                         'events': ['MESSAGES_UPSERT', 'CONNECTION_UPDATE'],
                                         'webhookByEvents': False, 'webhookBase64': False}},
                       timeout=8)
    except Exception as e:
        log.warning(f'[AtendeZap] webhook set [{instance}]: {e}')


# ── Rotas: landing / cadastro / login ──────────────────────────────────────────
@atendezap_bp.route('/')
def home():
    ref = request.args.get('ref')
    if ref:
        session['atende_ref'] = ref[:40]      # afiliado (creditado no Lote 4)
    if session.get('atende_business_id'):
        return redirect('/atendezap/painel')
    return render_template('atendezap/landing.html')


@atendezap_bp.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    erro = None
    if request.method == 'POST':
        nome  = (request.form.get('nome') or '').strip()
        nicho = (request.form.get('nicho') or 'outros').strip()
        owner = (request.form.get('owner_name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        phone = (request.form.get('phone') or '').strip()
        senha = (request.form.get('senha') or '')
        if not (nome and email and senha):
            erro = 'Preencha o nome do negócio, e-mail e senha.'
        elif nicho not in NICHOS:
            erro = 'Escolha o ramo do seu negócio.'
        elif len(senha) < 6:
            erro = 'A senha precisa ter pelo menos 6 caracteres.'
        elif get_negocio_por_email(email):
            erro = 'Já existe uma conta com esse e-mail. Faça login.'
        else:
            bid = criar_negocio(nome, nicho, owner, phone, email,
                                generate_password_hash(senha),
                                afiliado_ref=session.get('atende_ref'),
                                servicos=nicho_cfg(nicho)['servicos_seed'])
            session['atende_business_id'] = bid
            return redirect('/atendezap/painel')
    return render_template('atendezap/cadastro.html', erro=erro, nichos=NICHOS)


@atendezap_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    erro = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = (request.form.get('senha') or '')
        neg = get_negocio_por_email(email)
        if neg and neg['password_hash'] and check_password_hash(neg['password_hash'], senha):
            session['atende_business_id'] = neg['id']
            return redirect('/atendezap/painel')
        erro = 'E-mail ou senha incorretos.'
    return render_template('atendezap/entrar.html', erro=erro)


@atendezap_bp.route('/sair')
def sair():
    session.pop('atende_business_id', None)
    return redirect('/atendezap/entrar')


# ── Rotas: painel do dono ──────────────────────────────────────────────────────
@atendezap_bp.route('/painel')
@atende_login_required
def painel():
    neg = _negocio_logado()
    if not neg:
        session.pop('atende_business_id', None)
        return redirect('/atendezap/entrar')
    return render_template('atendezap/painel.html', neg=neg, cfg=nicho_cfg(neg['nicho']),
                           trial_dias=dias_de_trial_restantes(neg), acesso=tem_acesso(neg),
                           escaladas=contar_escaladas(neg['id']),
                           salvo=request.args.get('salvo'))


@atendezap_bp.route('/painel/config', methods=['POST'])
@atende_login_required
def painel_config():
    neg = _negocio_logado()
    atualizar_config(
        neg['id'],
        (request.form.get('servicos') or '').strip(),
        (request.form.get('horario') or '').strip(),
        (request.form.get('endereco') or '').strip(),
        (request.form.get('pagamentos') or '').strip(),
        (request.form.get('obs') or '').strip(),
        (request.form.get('alert_phone') or '').strip())
    return redirect('/atendezap/painel?salvo=1')


@atendezap_bp.route('/painel/bot', methods=['POST'])
@atende_login_required
def painel_bot():
    neg = _negocio_logado()
    novo = 0 if neg['bot_ativo'] else 1
    set_bot_ativo(neg['id'], novo)
    return jsonify({'bot_ativo': novo})


# ── Rotas: conectar o WhatsApp do dono (instância Evolution própria) ───────────
@atendezap_bp.route('/whatsapp/qr')
@atende_login_required
def wpp_qr():
    neg = _negocio_logado()
    if not EVO_URL or not EVO_KEY:
        return jsonify({'erro': 'Evolution API não configurada.'})
    instance = neg['evo_instance'] or f"atende{neg['id']}"
    headers = {'apikey': EVO_KEY, 'Content-Type': 'application/json'}

    def _ret(qr):
        if not qr.startswith('data:'):
            qr = 'data:image/png;base64,' + qr
        return jsonify({'qr': qr, 'instance': instance})

    try:
        # 1) QR da instância existente
        try:
            r = _requests.get(f'{EVO_URL}/instance/connect/{instance}', headers=headers, timeout=12)
            qr = _evo_extract_qr(r.json() if r.content else {})
            if qr:
                _set_instance_webhook(instance)
                return _ret(qr)
        except Exception:
            pass
        # 2) reset + cria limpa (com ajustes anti-ban p/ bot inbound)
        for u in (f'{EVO_URL}/instance/delete/{instance}', f'{EVO_URL}/instance/{instance}/delete'):
            try:
                _requests.delete(u, headers=headers, timeout=8)
            except Exception:
                pass
        time.sleep(1.5)
        cr = _requests.post(f'{EVO_URL}/instance/create', headers=headers,
                            json={'instanceName': instance, 'qrcode': True,
                                  'integration': 'WHATSAPP-BAILEYS',
                                  'rejectCall': True, 'groupsIgnore': True,
                                  'alwaysOnline': False, 'readMessages': False,
                                  'syncFullHistory': False}, timeout=20)
        qr = _evo_extract_qr(cr.json() if cr.content else {})
        _set_instance_webhook(instance)
        if qr:
            return _ret(qr)
        # 3) polling
        for _ in range(3):
            time.sleep(2.5)
            r2 = _requests.get(f'{EVO_URL}/instance/connect/{instance}', headers=headers, timeout=15)
            qr = _evo_extract_qr(r2.json() if r2.content else {})
            if qr:
                return _ret(qr)
        return jsonify({'erro': 'QR Code não disponível ainda. Aguarde 5s e tente de novo.'})
    except Exception as e:
        log.error(f'[AtendeZap QR] {e}')
        return jsonify({'erro': 'Erro ao gerar QR Code.'})


@atendezap_bp.route('/whatsapp/status')
@atende_login_required
def wpp_status():
    neg = _negocio_logado()
    instance = neg['evo_instance'] or f"atende{neg['id']}"
    if not EVO_URL or not EVO_KEY:
        return jsonify({'connected': False})
    try:
        r = _requests.get(f'{EVO_URL}/instance/connectionState/{instance}',
                          headers={'apikey': EVO_KEY}, timeout=8)
        d = r.json() if r.content else {}
        state = (d.get('instance', {}).get('state') if isinstance(d.get('instance'), dict)
                 else d.get('state', '')) or ''
        ok = state == 'open'
        set_evo_ativo(neg['id'], ok)
        return jsonify({'connected': ok, 'state': state})
    except Exception:
        return jsonify({'connected': False})


# ── Rotas: conversas (Lote 3 — painel enxuto) ──────────────────────────────────
@atendezap_bp.route('/conversas')
@atende_login_required
def conversas():
    neg = _negocio_logado()
    return render_template('atendezap/conversas.html', neg=neg,
                           convs=listar_conversas(neg['id']),
                           escaladas=contar_escaladas(neg['id']))


@atendezap_bp.route('/conversas/<int:cid>')
@atende_login_required
def conversa(cid):
    neg = _negocio_logado()
    conv = get_conversa(cid, neg['id'])
    if not conv:
        return redirect('/atendezap/conversas')
    return render_template('atendezap/conversa.html', neg=neg, conv=conv,
                           msgs=listar_mensagens(cid))


@atendezap_bp.route('/conversas/<int:cid>/voltar', methods=['POST'])
@atende_login_required
def conversa_voltar(cid):
    """Dono terminou o atendimento → devolve a conversa pro bot."""
    neg = _negocio_logado()
    if get_conversa(cid, neg['id']):
        voltar_pro_bot(cid)
    return redirect(f'/atendezap/conversas/{cid}')


# ══════════════════════════════════════════════════════════════════════════════
# PAYWALL + ASAAS (Lote 4) — plano único, mensal/anual no PIX
# ══════════════════════════════════════════════════════════════════════════════
_ASAAS_BASE = 'https://api.asaas.com/v3'

# Menos é mais: UM plano, 2 ciclos. Anual = 40% OFF do mensal×12 (R$1.558,80 → R$935).
PLANOS = {
    'anual':  {'label': 'Plano anual',  'valor': 935.00, 'cycle': 'YEARLY',
               'rotulo': 'R$ 935/ano', 'sub': '≈ R$ 77,92/mês · 40% de desconto', 'destaque': True},
    'mensal': {'label': 'Plano mensal', 'valor': 129.90, 'cycle': 'MONTHLY',
               'rotulo': 'R$ 129,90/mês', 'sub': 'cancela quando quiser', 'destaque': False},
}


def _asaas_req(method, endpoint, data=None):
    try:
        r = _requests.request(method, f'{_ASAAS_BASE}{endpoint}',
                              headers={'access_token': os.environ.get('ASAAS_API_KEY', ''),
                                       'Content-Type': 'application/json'},
                              json=data, timeout=20)
        return r.json() if r.content else {}
    except Exception as e:
        return {'error': str(e)}


def _doc_digits(s):
    return ''.join(c for c in (s or '') if c.isdigit())


def _doc_valido(s):
    """Aceita CPF (11) ou CNPJ (14) — o negócio pode assinar com qualquer um."""
    return len(_doc_digits(s)) in (11, 14)


def _asaas_cliente(neg, doc):
    """Acha/cria o cliente no Asaas e guarda o customer_id no negócio."""
    if neg['asaas_customer_id']:
        return neg['asaas_customer_id']
    doc = _doc_digits(doc)
    cid = None
    if doc:
        busca = _asaas_req('GET', f'/customers?cpfCnpj={doc}&limit=1')
        if busca.get('data'):
            cid = busca['data'][0].get('id')
    if not cid:
        resp = _asaas_req('POST', '/customers', {
            'name': neg['nome'], 'email': neg['email'],
            'mobilePhone': _doc_digits(neg['phone']), 'cpfCnpj': doc,
            'notificationDisabled': True})
        cid = resp.get('id')
    if cid:
        set_asaas_cliente(neg['id'], cid, doc)
    return cid or ''


def atende_webhook_ativar(biz_id, ativar, payment_id=''):
    """Chamado pelo webhook Asaas global (ref 'atendezap_<biz_id>_<plano>') e pelo
    polling do PIX. Ativa/corta a assinatura. Afiliado: paga R$25 SÓ no 1º pagamento
    (transição inativo→ativo), nunca nas renovações."""
    try:
        neg = get_negocio(int(biz_id))
    except (TypeError, ValueError):
        neg = None
    if not neg:
        return False
    ja_ativo = bool(neg['plan_active'])
    set_plano_ativo(neg['id'], 1 if ativar else 0)
    if ativar and payment_id and neg['afiliado_ref'] and not ja_ativo:
        try:
            from afiliados import registrar_comissao
            registrar_comissao(neg['afiliado_ref'], 'atendezap', payment_id,
                               neg['nome'], cliente_email=neg['email'])
        except Exception as e:
            log.warning(f'[AtendeZap] comissão afiliado: {e}')
    log.info(f"[AtendeZap] Assinatura {'ATIVADA' if ativar else 'cortada'} (negócio {neg['id']})")
    return True


@atendezap_bp.route('/assinar')
@atende_login_required
def assinar():
    neg = _negocio_logado()
    return render_template('atendezap/assinar.html', neg=neg, planos=PLANOS,
                           trial_dias=dias_de_trial_restantes(neg), acesso=tem_acesso(neg))


@atendezap_bp.route('/checkout/<plano>', methods=['GET', 'POST'])
@atende_login_required
def checkout(plano):
    neg = _negocio_logado()
    if plano not in PLANOS:
        return redirect('/atendezap/assinar')
    p = PLANOS[plano]
    erro = None
    if request.method == 'POST':
        doc = _doc_digits(request.form.get('cpf'))
        if not _doc_valido(doc):
            erro = 'CPF ou CNPJ inválido. Confira os números.'
        else:
            cid = _asaas_cliente(neg, doc)
            if not cid:
                erro = 'Não foi possível iniciar o pagamento. Tente novamente.'
            else:
                sub = _asaas_req('POST', '/subscriptions', {
                    'customer': cid, 'billingType': 'PIX', 'value': p['valor'],
                    'nextDueDate': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
                    'cycle': p['cycle'], 'description': f'AtendeZap — {p["label"]}',
                    'externalReference': f'atendezap_{neg["id"]}_{plano}'})
                sub_id = sub.get('id')
                if not sub_id:
                    erro = (sub.get('errors') or [{}])[0].get('description', 'Erro ao gerar a cobrança.')
                else:
                    set_asaas_sub(neg['id'], sub_id, plano)
                    return redirect(f'/atendezap/pix/{plano}')
    return render_template('atendezap/checkout.html', neg=neg, plano=plano, p=p, erro=erro)


@atendezap_bp.route('/pix/<plano>')
@atende_login_required
def pix(plano):
    neg = _negocio_logado()
    if plano not in PLANOS or not neg['asaas_subscription_id']:
        return redirect('/atendezap/assinar')
    qr = copia = ''
    pays = _asaas_req('GET', f'/subscriptions/{neg["asaas_subscription_id"]}/payments?limit=1')
    if pays.get('data'):
        pid = pays['data'][0].get('id', '')
        if pid:
            resp = _asaas_req('GET', f'/payments/{pid}/pixQrCode')
            qr    = resp.get('encodedImage', '')
            copia = resp.get('payload', '')
    return render_template('atendezap/pix.html', neg=neg, plano=plano, p=PLANOS[plano],
                           qr=qr, copia=copia)


@atendezap_bp.route('/pix-status', methods=['POST'])
@atende_login_required
def pix_status():
    neg = _negocio_logado()
    if not neg['asaas_subscription_id']:
        return jsonify({'pago': False})
    if neg['plan_active']:
        return jsonify({'pago': True})
    pays = _asaas_req('GET', f'/subscriptions/{neg["asaas_subscription_id"]}/payments?limit=1')
    if pays.get('data'):
        st = (pays['data'][0].get('status') or '').upper()
        if st in ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH'):
            atende_webhook_ativar(neg['id'], True, pays['data'][0].get('id', ''))
            return jsonify({'pago': True})
    return jsonify({'pago': False})
