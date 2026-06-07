# -*- coding: utf-8 -*-
"""
vetzap_bot.py — Fase 1 do VetZap: o "Uber do Veterinário" no WhatsApp.

Fluxo (Teste da Rifa: tudo dentro do WhatsApp, zero app):
  tutor manda zap (texto/áudio/foto) → IA (Gemini) acolhe + tria de graça
  → oferece vet (chat/vídeo, preço por horário) → PIX → "corrida" pro vet
  → vet aceita e liga em VÍDEO pelo próprio WhatsApp.

Este módulo é ISOLADO (não mexe no petmed/produção). Para ligar de verdade,
basta registrar o blueprint e apontar o webhook da Evolution API para
/vetzap/wa/webhook. Conversa é testável offline via `processar()`.
"""
import os
import json
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
import requests as _req

log = logging.getLogger('vetzap_bot')

vetzap_bp = Blueprint('vetzap_bot', __name__, url_prefix='/vetzap')

# ── IA (Gemini multimodal — texto, foto e áudio) ───────────────────────────────
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent'

# ── WhatsApp (Evolution API — mesmo padrão do MandaZap/AgendaJá) ────────────────
EVO_URL  = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
EVO_KEY  = os.environ.get('EVOLUTION_API_KEY', '')
EVO_INST = os.environ.get('VETZAP_WA_INSTANCE', 'vetzap')

# ── Veterinários de plantão (Fase 0/1: lista simples por env, vírgula) ─────────
#    Ex: VETZAP_VETS="5547999999999:Dra. Ana,5547988888888:Dr. Léo"
def _vets_plantao():
    raw = os.environ.get('VETZAP_VETS', '')
    vets = []
    for item in raw.split(','):
        item = item.strip()
        if not item:
            continue
        if ':' in item:
            tel, nome = item.split(':', 1)
        else:
            tel, nome = item, 'Veterinário(a)'
        vets.append({'telefone': ''.join(c for c in tel if c.isdigit()), 'nome': nome.strip()})
    return vets


# ── Preço por horário (plantão: noite/madrugada custam mais) ───────────────────
def preco_por_horario(agora=None):
    h = (agora or datetime.now()).hour
    if 0 <= h < 6:        # madrugada (pico de desespero, menos vet online)
        return {'faixa': 'madrugada', 'chat': 79, 'video': 159}
    if 18 <= h < 24:      # noite
        return {'faixa': 'noite', 'chat': 59, 'video': 129}
    return {'faixa': 'dia', 'chat': 39, 'video': 89}   # 6h–18h


# ── Pagamento (Asaas — PIX) ────────────────────────────────────────────────────
_ASAAS_BASE = os.environ.get('ASAAS_BASE', 'https://api.asaas.com/v3')

def _asaas_h():
    return {'access_token': os.environ.get('ASAAS_API_KEY', ''),
            'Content-Type': 'application/json', 'User-Agent': 'VetZap'}

def _asaas_pix(cpf, valor, descricao, telefone):
    """Cria cobrança PIX única. Retorna {payment_id, copia_cola, qr} ou {erro}."""
    if not os.environ.get('ASAAS_API_KEY', ''):
        return {'erro': 'ASAAS_API_KEY ausente'}
    import datetime as _dt, time as _t
    try:
        digits = ''.join(c for c in str(telefone) if c.isdigit())
        cust = _req.post(f'{_ASAAS_BASE}/customers',
                         json={'name': f'Tutor VetZap {digits[-4:]}', 'cpfCnpj': cpf,
                               'mobilePhone': digits},
                         headers=_asaas_h(), timeout=20).json()
        cid = cust.get('id')
        if not cid:
            return {'erro': str(cust)[:140]}
        venc = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
        pay = _req.post(f'{_ASAAS_BASE}/payments',
                        json={'customer': cid, 'billingType': 'PIX', 'value': float(valor),
                              'dueDate': venc, 'description': descricao,
                              'externalReference': f'vetzap_wa_{digits}_{int(_t.time())}'},
                        headers=_asaas_h(), timeout=20).json()
        pid = pay.get('id')
        if not pid:
            return {'erro': str(pay)[:140]}
        qr = _req.get(f'{_ASAAS_BASE}/payments/{pid}/pixQrCode',
                      headers=_asaas_h(), timeout=20).json()
        return {'payment_id': pid, 'copia_cola': qr.get('payload', ''),
                'qr': qr.get('encodedImage', '')}
    except Exception as e:
        return {'erro': str(e)[:140]}


# ── Cérebro da conversa (Gemini) ───────────────────────────────────────────────
_SYSTEM = """Você é o VetZap, um assistente veterinário de PRONTO-ATENDIMENTO no WhatsApp.
O tutor está preocupado com o pet dele. Você é a "tia veterinária" de quem não tem uma.

ESTILO (é WhatsApp!):
- Acolha primeiro, com empatia. Mensagens CURTAS, calorosas, emojis com moderação.
- No máximo 1 pergunta por vez, e só se realmente precisar entender melhor. Senão, já oriente.

O QUE VOCÊ PODE (teleorientação — legal):
- Orientação de suporte domiciliar e produtos SEM RECEITA, sempre como SUGESTÃO,
  nunca como ordem/prescrição. Use sempre frases de apoio:
  "Tutores costumam...", "Muita gente nesse caso faz...", "Disponível sem receita em pet shops...".
- DEIXE CLARO que você é um assistente (IA) e NÃO um veterinário. Ex:
  "Eu não sou veterinário(a), mas posso te orientar enquanto isso..." ou
  "Não substituo um vet, porém o que costuma ajudar é...".
- Classificar urgência.

O QUE VOCÊ NUNCA FAZ:
- NUNCA receite medicamento de prescrição (antibiótico, anti-inflamatório, corticoide,
  antiparasitário com dose, etc). Se o caso pede isso, encaminhe para um veterinário.

QUANDO OFERECER VET HUMANO (oferecer_vet = true):
- Caso URGENTE; ou quando precisa de receita/exame/procedimento; ou o tutor pedir.
- Em urgência, dê a 1ª orientação imediata E ofereça o vet ao vivo.

RESPONDA SEMPRE só em JSON válido:
{"mensagem":"texto curto pronto pro WhatsApp","urgencia":"leve|atencao|urgente","oferecer_vet":true|false}

Sempre que orientar, lembre em 1 frase: isto é orientação e não substitui avaliação presencial."""


def _gemini_turn(historico, media=None):
    """historico: lista [{'role':'user'|'assistant','content':str}]. media: {'mime','data_b64'} ou None.
    Retorna dict {mensagem, urgencia, oferecer_vet}."""
    if not GEMINI_KEY:
        return {'mensagem': 'Estou aqui pra ajudar com seu pet! Me conta o que está acontecendo. 🐾',
                'urgencia': 'leve', 'oferecer_vet': False}
    contents = []
    for h in historico:
        role = 'model' if h['role'] == 'assistant' else 'user'
        contents.append({'role': role, 'parts': [{'text': h['content']}]})
    # anexa mídia (foto/áudio) na última fala do usuário
    if media and contents and contents[-1]['role'] == 'user':
        contents[-1]['parts'].insert(0, {'inline_data': {'mime_type': media['mime'], 'data': media['data_b64']}})
    if not contents:
        contents = [{'role': 'user', 'parts': [{'text': 'Oi'}]}]
    body = {
        'systemInstruction': {'parts': [{'text': _SYSTEM}]},
        'contents': contents,
        'generationConfig': {'temperature': 0.3, 'maxOutputTokens': 800,
                             'responseMimeType': 'application/json'},
    }
    try:
        r = _req.post(_GEMINI_URL.format(m=GEMINI_MODEL), params={'key': GEMINI_KEY},
                      json=body, timeout=60)
        r.raise_for_status()
        txt = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        data = json.loads(txt)
        data.setdefault('mensagem', 'Pode me contar um pouco mais sobre o seu pet?')
        data.setdefault('urgencia', 'leve')
        data.setdefault('oferecer_vet', False)
        return data
    except Exception as e:
        log.warning('[vetzap_bot] Gemini falhou: %s', e)
        return {'mensagem': 'Tive um probleminha aqui, pode repetir o que está acontecendo com seu pet? 🐾',
                'urgencia': 'leve', 'oferecer_vet': False}


# ── Sessões (estado da conversa por telefone) ──────────────────────────────────
def _db():
    from petmed_db import get_petmed_db
    conn = get_petmed_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS vetzap_wa (
        telefone       TEXT PRIMARY KEY,
        estado         TEXT DEFAULT 'conversando',
        historico      TEXT DEFAULT '[]',
        canal          TEXT DEFAULT '',
        valor          REAL DEFAULT 0,
        cpf            TEXT DEFAULT '',
        pix_payment_id TEXT DEFAULT '',
        updated_at     TEXT
    )''')
    for mig in ("ALTER TABLE vetzap_wa ADD COLUMN canal TEXT DEFAULT ''",
                "ALTER TABLE vetzap_wa ADD COLUMN valor REAL DEFAULT 0",
                "ALTER TABLE vetzap_wa ADD COLUMN cpf TEXT DEFAULT ''",
                "ALTER TABLE vetzap_wa ADD COLUMN pix_payment_id TEXT DEFAULT ''"):
        try:
            conn.execute(mig); conn.commit()
        except Exception:
            pass
    conn.commit()
    return conn


def _carrega(telefone):
    conn = _db()
    row = conn.execute('SELECT * FROM vetzap_wa WHERE telefone=?', (telefone,)).fetchone()
    conn.close()
    if not row:
        return {'estado': 'conversando', 'historico': [], 'canal': '', 'valor': 0,
                'cpf': '', 'pix_payment_id': ''}
    return {'estado': row['estado'], 'historico': json.loads(row['historico'] or '[]'),
            'canal': row['canal'] or '', 'valor': row['valor'] or 0,
            'cpf': row['cpf'] or '', 'pix_payment_id': row['pix_payment_id'] or ''}


def _salva(telefone, estado, historico, canal=None, valor=None, cpf=None, pix_payment_id=None):
    cur = _carrega(telefone)  # mantém campos não informados
    canal = cur['canal'] if canal is None else canal
    valor = cur['valor'] if valor is None else valor
    cpf = cur['cpf'] if cpf is None else cpf
    pix_payment_id = cur['pix_payment_id'] if pix_payment_id is None else pix_payment_id
    h = json.dumps(historico[-20:], ensure_ascii=False)
    conn = _db()
    conn.execute('''INSERT INTO vetzap_wa (telefone,estado,historico,canal,valor,cpf,pix_payment_id,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(telefone) DO UPDATE SET
                        estado=?,historico=?,canal=?,valor=?,cpf=?,pix_payment_id=?,updated_at=?''',
                 (telefone, estado, h, canal, valor, cpf, pix_payment_id, _agora(),
                  estado, h, canal, valor, cpf, pix_payment_id, _agora()))
    conn.commit()
    conn.close()


def _agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── O processador: recebe msg do tutor, devolve a(s) resposta(s) do bot ────────
def processar(telefone, texto='', media=None):
    """Retorna {'mensagens': [str, ...], 'acao': None|'gerar_pix'|'acionar_vet', 'urgencia': str}."""
    sess = _carrega(telefone)
    estado = sess['estado']
    hist = sess['historico']

    txt = (texto or '').strip()
    low = txt.lower()

    # ── Tutor escolheu o canal → pede CPF (pra gerar o PIX) ────────────────────
    if estado == 'ofereceu_vet':
        if any(k in low for k in ('video', 'vídeo', 'chamada', 'chat', 'vet', 'sim', 'quero', 'agora')):
            p = preco_por_horario()
            canal = 'video' if ('video' in low or 'vídeo' in low or 'chamada' in low) else 'chat'
            valor = p[canal]
            _salva(telefone, 'aguardando_cpf', hist, canal=canal, valor=valor)
            return {'mensagens': [f"Ótimo! 🩺 Pra gerar o PIX de *R$ {valor}* "
                                  f"({'videochamada' if canal=='video' else 'chat'} com vet), "
                                  f"me manda só o seu *CPF* (só os números). 🔒"],
                    'acao': 'pedir_cpf', 'urgencia': 'atencao'}
        if any(k in low for k in ('não', 'nao', 'depois', 'agora não')):
            _salva(telefone, 'conversando', hist)
            return {'mensagens': ['Tudo bem! 🐾 Qualquer coisa é só me chamar. Cuida bem do seu pet 💚'],
                    'acao': None, 'urgencia': 'leve'}

    # ── Recebendo o CPF → gera o PIX de verdade (Asaas) ────────────────────────
    if estado == 'aguardando_cpf':
        cpf = ''.join(c for c in txt if c.isdigit())
        if len(cpf) != 11:
            return {'mensagens': ['Preciso do CPF com *11 números* pra emitir o PIX 🙏 (só os números)'],
                    'acao': None, 'urgencia': 'leve'}
        canal = sess['canal'] or 'video'
        valor = int(sess['valor'] or preco_por_horario()[canal])
        pix = _asaas_pix(cpf, valor, f"VetZap - atendimento ({canal})", telefone)
        if 'erro' in pix:
            _salva(telefone, 'ofereceu_vet', hist)
            log.warning('[vetzap_bot] PIX falhou: %s', pix['erro'])
            return {'mensagens': ['Ops, não consegui gerar o PIX agora 😕 Pode tentar de novo? '
                                  'Responde *vídeo* ou *chat*.'], 'acao': None, 'urgencia': 'leve'}
        _salva(telefone, 'aguardando_pix', hist, cpf=cpf, pix_payment_id=pix['payment_id'])
        return {'mensagens': [f"Prontinho! 💸 PIX de *R$ {valor}* — é só copiar e colar no seu banco:",
                              pix['copia_cola'],
                              "Assim que cair, eu chamo um veterinário na hora ⏱️ "
                              "(se ninguém aceitar em 5 min, devolvo o PIX)"],
                'acao': 'pix_gerado', 'pix': pix, 'urgencia': 'atencao'}

    # ── Conversa normal: roda a IA ─────────────────────────────────────────────
    if txt:
        hist.append({'role': 'user', 'content': txt})
    elif media:
        hist.append({'role': 'user', 'content': '[o tutor enviou uma imagem/áudio]'})

    r = _gemini_turn(hist, media=media)
    msg = r['mensagem']
    hist.append({'role': 'assistant', 'content': msg})

    mensagens = [msg]
    acao = None
    novo_estado = 'conversando'

    if r.get('oferecer_vet'):
        p = preco_por_horario()
        oferta = (f"\n\nQuer falar com um *veterinário de verdade agora*? 🩺\n"
                  f"💬 Chat: *R$ {p['chat']}*\n"
                  f"📹 Vídeo (ao vivo): *R$ {p['video']}*\n"
                  f"É só responder *vídeo* ou *chat*.")
        mensagens.append(oferta)
        novo_estado = 'ofereceu_vet'

    _salva(telefone, novo_estado, hist)
    return {'mensagens': mensagens, 'acao': acao, 'urgencia': r.get('urgencia', 'leve')}


# ── WhatsApp: enviar via Evolution API ─────────────────────────────────────────
def wa_send(telefone, texto):
    if not EVO_URL or not EVO_KEY:
        log.warning('[vetzap_bot] Evolution não configurada — msg não enviada')
        return False
    digits = ''.join(c for c in str(telefone) if c.isdigit())
    if digits and not digits.startswith('55'):
        digits = '55' + digits
    try:
        resp = _req.post(f'{EVO_URL}/message/sendText/{EVO_INST}',
                         json={'number': digits + '@s.whatsapp.net', 'text': texto},
                         headers={'apikey': EVO_KEY}, timeout=10)
        return resp.status_code in (200, 201)
    except Exception as e:
        log.warning('[vetzap_bot] wa_send erro: %s', e)
        return False


def acionar_vets(telefone_tutor, resumo):
    """Dispara a 'corrida' pros vets de plantão (Fase 1 simples: avisa todos)."""
    vets = _vets_plantao()
    if not vets:
        return 0
    for v in vets:
        wa_send(v['telefone'],
                f"🚨 *Corrida VetZap!*\nCaso: {resumo}\n"
                f"Aceita? Responda *ACEITO* e ligue em vídeo pro tutor.\n"
                f"Tutor: wa.me/{telefone_tutor}")
    return len(vets)


# ── Webhook que recebe as mensagens da Evolution API ───────────────────────────
@vetzap_bp.route('/wa/webhook', methods=['GET', 'POST'])
def wa_webhook():
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200
    data = request.get_json(silent=True) or {}
    try:
        msg = data.get('data', data)
        key = msg.get('key', {})
        if key.get('fromMe'):
            return jsonify({'ignored': 'fromMe'}), 200
        telefone = (key.get('remoteJid', '') or '').split('@')[0]
        m = msg.get('message', {}) or {}
        texto = (m.get('conversation')
                 or (m.get('extendedTextMessage') or {}).get('text', '')
                 or '')
        media = None  # TODO: baixar imageMessage/audioMessage da Evolution e passar base64
        if not telefone:
            return jsonify({'ignored': 'no-phone'}), 200

        out = processar(telefone, texto=texto, media=media)
        for txt in out['mensagens']:
            wa_send(telefone, txt)
        return jsonify({'ok': True, 'acao': out.get('acao')}), 200
    except Exception as e:
        log.error('[vetzap_bot] webhook erro: %s', e, exc_info=True)
        return jsonify({'ok': False}), 200


# ── Webhook do Asaas: pagamento confirmado → aciona a "corrida" ────────────────
@vetzap_bp.route('/wa/asaas-webhook', methods=['POST'])
def wa_asaas_webhook():
    tok = os.environ.get('ASAAS_WEBHOOK_TOKEN', '').strip().strip('"').strip("'")
    rec = (request.headers.get('asaas-access-token', '') or '').strip().strip('"').strip("'")
    if tok and rec != tok:
        return jsonify({'error': 'unauthorized'}), 401
    d = request.get_json(silent=True) or {}
    if d.get('event') in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED'):
        pid = (d.get('payment') or {}).get('id', '')
        if pid:
            conn = _db()
            row = conn.execute('SELECT * FROM vetzap_wa WHERE pix_payment_id=?', (pid,)).fetchone()
            conn.close()
            if row:
                tel = row['telefone']
                _salva(tel, 'na_fila', json.loads(row['historico'] or '[]'))
                wa_send(tel, '✅ Pagamento recebido! Procurando um veterinário disponível agora... ⏱️')
                n = acionar_vets(tel, f"atendimento {row['canal']} pago (R$ {row['valor']})")
                log.info('[vetzap_bot] corrida disparada p/ %s vets (tutor %s)', n, tel)
    return jsonify({'ok': True}), 200
