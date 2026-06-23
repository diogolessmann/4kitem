"""
amparo.py — Blueprint Amparo
SaaS de engajamento entre sessões para psicólogos (módulo 4kitem).

POSICIONAMENTO: não é sistema de gestão/prontuário. É a camada das "167 horas fora
da sessão": um motor que mantém o paciente no processo entre as sessões e prova isso
pro psicólogo — SEM nunca substituir o humano.

⚠️ LINHA VERMELHA (CFP, cartilhas jul+dez/2025): a IA é FERRAMENTA SUPERVISIONADA,
jamais terapeuta autônomo. Proibido chat terapêutico aberto / diagnóstico / intervenção.
Ver os guard-rails abaixo (crise, consentimento, transparência).

Lote 0 = Fundação: auth do psicólogo + modelo do paciente + consentimento/LGPD + identidade.
"""
import os
import logging
import requests as _requests
from functools import wraps
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, redirect, request,
                   session, jsonify, url_for, abort)
from werkzeug.security import generate_password_hash, check_password_hash

from amparo_db import (get_amparo_db, init_amparo_db, get_psicologo,
                       get_psicologo_by_email, novo_consent_token,
                       conta_pacientes_ativos, registra_consentimento,
                       ensure_agenda_config, set_agenda_config, get_agenda_by_slug,
                       get_horarios, replace_horarios, horas_ocupadas,
                       get_or_create_paciente, criar_agendamento,
                       listar_agendamentos, set_status_agendamento,
                       set_assinatura_pendente, atualiza_assinatura_por_customer,
                       registra_pagamento, pode_ativar_paciente)
import amparo_wa

log = logging.getLogger('amparo')

amparo_bp = Blueprint('amparo', __name__, url_prefix='/amparo')

# ── Planos (B2B — quem paga é o psicólogo) ─────────────────────────────────────
# Custo variável real ~R$1/paciente ativo/mês (WhatsApp é o driver; IA = centavos).
PLANOS = {
    'essencial': {'nome': 'Essencial', 'preco': 49.90, 'limite': 10,
                  'desc': 'Agenda + lembrete + motor para até 10 pacientes'},
    'pro':       {'nome': 'Pro',       'preco': 89.90, 'limite': 30,
                  'desc': 'Tudo do Essencial + motor para até 30 pacientes + planos/cashback'},
    'clinica':   {'nome': 'Clínica',   'preco': 199.00, 'limite': 9999,
                  'desc': 'Pacientes ilimitados + multi-psicólogo'},
}
TRIAL_DIAS = 14
TRIAL_LIMITE = 5

# ── IA: Gemini via REST (mesmo padrão do DRZAP) — Flash-Lite é o mais barato ────
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('AMPARO_GEMINI_MODEL', 'gemini-2.5-flash-lite')
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


def _gemini_call(system, contents, json_mode=False, max_tokens=1024, temperature=0.5):
    """Chama o Gemini via REST. Retorna (texto, tokens_in, tokens_out).
    Usado a partir do Lote 3 (camada de calor do motor) — SEMPRE sob as regras CFP."""
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


# ══════════════════════════════════════════════════════════════════════════════
# GUARD-RAILS DO CFP (esqueleto — o motor do Lote 3 OBRIGATORIAMENTE passa por aqui)
# ══════════════════════════════════════════════════════════════════════════════

# (1) Protocolo de crise — HARD-CODED. Se o paciente sinalizar risco, o motor PARA de
#     conversar, mostra ajuda humana e alerta o psicólogo. Nunca "resolve" sozinho.
CRISE_KEYWORDS = [
    'me matar', 'suicíd', 'suicid', 'tirar minha vida', 'não quero mais viver',
    'queria morrer', 'quero morrer', 'me cortar', 'me machucar', 'sumir pra sempre',
    'acabar com tudo', 'tirar a vida', 'não aguento mais viver',
]
MSG_CRISE = (
    "Sinto muito que você esteja passando por isso. 💙 Isso é sério e você merece falar "
    "com uma pessoa AGORA. Ligue para o CVV no 188 (gratuito, 24h, sigiloso) ou, em "
    "emergência, SAMU 192. Você também pode procurar o CAPS mais próximo. "
    "Vou avisar seu psicólogo(a). Você não está sozinho(a)."
)

# (5) A IA NUNCA conduz terapia. Estas regras vão no system prompt do motor (Lote 3).
SYSTEM_MOTOR_BASE = """Você é o assistente de acompanhamento do Amparo, falando pelo WhatsApp \
com o PACIENTE de um(a) psicólogo(a), ENTRE as sessões. Você é uma IA — e se identifica como tal.

O QUE VOCÊ FAZ: acolher com calor, lembrar tarefas combinadas com o psicólogo, coletar como a \
pessoa está (humor/escala), e SEMPRE devolver pro profissional humano. Mensagens curtas, gentis, em \
português do Brasil.

O QUE VOCÊ NUNCA FAZ (proibido pelo Conselho Federal de Psicologia):
- NÃO faz terapia, diagnóstico, interpretação ou avaliação clínica.
- NÃO dá conselho sobre remédio, relacionamento ou decisões de vida.
- NÃO concorda automaticamente com pensamento distorcido ou perigoso só pra agradar.
- Diante de qualquer sinal de risco/crise, NÃO conversa: aciona o protocolo de crise.

Diante de tema profundo, acolha o sentimento e ROTEIE pro humano: \
"Que importante você me contar isso. Anotei pra você levar pra sua sessão." """

DISCLAIMER_IA = "🤖 Sou um assistente de IA do seu psicólogo(a), não um profissional. Em emergência: CVV 188 / SAMU 192."

# (3) Consentimento informado (LGPD + transparência) — versionado p/ auditoria.
CONSENT_VERSAO = 'v1-2026-06'
CONSENT_TEXTO = (
    "Seu psicólogo(a) usa o Amparo para te acompanhar entre as sessões por mensagens no "
    "WhatsApp (lembretes, um 'como você está?' e perguntas combinadas). As mensagens são "
    "geradas com apoio de inteligência artificial, que NÃO é um profissional e NÃO faz "
    "terapia — tudo é supervisionado pelo seu psicólogo(a). Seus dados de saúde são "
    "sensíveis e tratados com sigilo (LGPD). Você pode parar de receber as mensagens "
    "(opt-out) a qualquer momento, sem qualquer prejuízo ao seu atendimento."
)


def detecta_crise(texto):
    """True se a mensagem do paciente contém sinal de risco. Usado pelo motor (Lote 3)."""
    t = (texto or '').lower()
    return any(k in t for k in CRISE_KEYWORDS)


# ── Auth do psicólogo ──────────────────────────────────────────────────────────
def _login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get('amparo_psi_id'):
            return redirect('/amparo/entrar')
        return f(*a, **kw)
    return wrapper


def _psi_atual():
    pid = session.get('amparo_psi_id')
    return get_psicologo(pid) if pid else None


# ══════════════════════════════════════════════════════════════════════════════
# ROTAS — Lote 0 (Fundação)
# ══════════════════════════════════════════════════════════════════════════════

@amparo_bp.route('/')
def landing():
    return render_template('amparo/landing.html', planos=PLANOS)


@amparo_bp.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if request.method == 'POST':
        nome  = (request.form.get('nome') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        crp   = (request.form.get('crp') or '').strip()
        tel   = (request.form.get('telefone') or '').strip()
        senha = request.form.get('senha') or ''
        termo = request.form.get('termo')

        if not (nome and email and senha):
            return render_template('amparo/cadastro.html', erro='Preencha nome, e-mail e senha.')
        if len(senha) < 6:
            return render_template('amparo/cadastro.html', erro='A senha precisa ter ao menos 6 caracteres.')
        if not termo:
            return render_template('amparo/cadastro.html', erro='É preciso aceitar os Termos e a Política de Privacidade.')
        if get_psicologo_by_email(email):
            return render_template('amparo/cadastro.html', erro='Já existe uma conta com esse e-mail. Faça login.')

        conn = get_amparo_db()
        cur = conn.execute(
            'INSERT INTO amparo_psicologos (nome, email, crp, telefone, password_hash, '
            'plano, pacientes_limite, trial_expires, termo_aceito, ultimo_acesso) '
            'VALUES (?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)',
            (nome, email, crp, tel, generate_password_hash(senha),
             'trial', TRIAL_LIMITE, (datetime.utcnow() + timedelta(days=TRIAL_DIAS)).isoformat()))
        conn.commit()
        psi_id = cur.lastrowid
        conn.close()
        session['amparo_psi_id'] = psi_id
        log.info(f'[Amparo] Novo psicólogo cadastrado: {email} (id={psi_id})')
        return redirect('/amparo/painel')

    return render_template('amparo/cadastro.html')


@amparo_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        psi = get_psicologo_by_email(email)
        if not psi or not check_password_hash(psi['password_hash'], senha):
            return render_template('amparo/entrar.html', erro='E-mail ou senha incorretos.')
        session['amparo_psi_id'] = psi['id']
        conn = get_amparo_db()
        conn.execute('UPDATE amparo_psicologos SET ultimo_acesso=CURRENT_TIMESTAMP WHERE id=?', (psi['id'],))
        conn.commit(); conn.close()
        return redirect('/amparo/painel')
    return render_template('amparo/entrar.html')


@amparo_bp.route('/sair')
def sair():
    session.pop('amparo_psi_id', None)
    return redirect('/amparo')


@amparo_bp.route('/painel')
@_login_required
def painel():
    psi = _psi_atual()
    if not psi:
        session.pop('amparo_psi_id', None)
        return redirect('/amparo/entrar')
    ativos = conta_pacientes_ativos(psi['id'])
    plano = PLANOS.get(psi['plano'], {'nome': 'Avaliação (trial)', 'limite': psi['pacientes_limite']})
    return render_template('amparo/painel.html', psi=psi, plano=plano,
                           pacientes_ativos=ativos, limite=psi['pacientes_limite'])


# ── Páginas legais (LGPD / transparência — exigência do CFP) ───────────────────
@amparo_bp.route('/termos')
def termos():
    return render_template('amparo/termos.html')


@amparo_bp.route('/privacidade')
def privacidade():
    return render_template('amparo/privacidade.html', consent_texto=CONSENT_TEXTO)


# ── Consentimento do paciente (link mágico com token) ──────────────────────────
# O paciente abre este link (enviado pelo psicólogo) p/ consentir OU optar por sair.
@amparo_bp.route('/p/<token>', methods=['GET', 'POST'])
def consentimento_paciente(token):
    conn = get_amparo_db()
    pac = conn.execute('SELECT * FROM amparo_pacientes WHERE consent_token=?', (token,)).fetchone()
    conn.close()
    if not pac:
        abort(404)

    if request.method == 'POST':
        acao = request.form.get('acao')  # 'aceite' | 'opt_out'
        if acao == 'aceite':
            registra_consentimento(pac['id'], 'aceite', CONSENT_VERSAO, canal='web')
        elif acao == 'opt_out':
            registra_consentimento(pac['id'], 'opt_out', CONSENT_VERSAO, canal='web')
        # recarrega
        conn = get_amparo_db()
        pac = conn.execute('SELECT * FROM amparo_pacientes WHERE id=?', (pac['id'],)).fetchone()
        conn.close()

    return render_template('amparo/consentimento.html', pac=pac,
                           consent_texto=CONSENT_TEXTO)


# ══════════════════════════════════════════════════════════════════════════════
# AGENDA + AUTOAGENDAMENTO — Lote 1
# ══════════════════════════════════════════════════════════════════════════════
import threading
import time as _time

WEEKDAYS_PT   = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
WEEKDAYS_FULL = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
BRT = timedelta(hours=-3)  # horário de Brasília (simplificado; refinar com tz no futuro)


def _agora_brt():
    return datetime.utcnow() + BRT


def _hhmm_to_min(s):
    h, m = s.split(':'); return int(h) * 60 + int(m)


def _min_to_hhmm(t):
    return f'{t // 60:02d}:{t % 60:02d}'


def _slots_do_dia(psi_id, cfg, data_str):
    """Lista de horas livres ('HH:MM') numa data, respeitando janelas, duração, folga,
    antecedência mínima e horários já ocupados."""
    try:
        dt = datetime.strptime(data_str, '%Y-%m-%d')
    except Exception:
        return []
    wd = dt.weekday()  # 0=segunda ... 6=domingo (igual ao nosso dia_semana)
    janelas = [h for h in get_horarios(psi_id) if h['dia_semana'] == wd]
    if not janelas:
        return []
    ocupadas = horas_ocupadas(psi_id, data_str)
    dur   = cfg['duracao_min'] or 50
    passo = dur + (cfg['intervalo_min'] or 0)
    limite = _agora_brt() + timedelta(hours=(cfg['antecedencia_horas'] or 0))
    out = []
    for j in janelas:
        ini, fim = _hhmm_to_min(j['inicio']), _hhmm_to_min(j['fim'])
        t = ini
        while t + dur <= fim:
            hhmm = _min_to_hhmm(t)
            slot_dt = datetime.strptime(f'{data_str} {hhmm}', '%Y-%m-%d %H:%M')
            if hhmm not in ocupadas and slot_dt >= limite:
                out.append(hhmm)
            t += passo
    return sorted(set(out))


def _proximos_dias(psi_id, n=14):
    """Próximos n dias que têm janela de atendimento (p/ o seletor de data público)."""
    dias_com_horario = {h['dia_semana'] for h in get_horarios(psi_id)}
    hoje = _agora_brt().date()
    dias = []
    for i in range(n):
        d = hoje + timedelta(days=i)
        if d.weekday() in dias_com_horario:
            dias.append({'data': d.strftime('%Y-%m-%d'),
                         'label': f'{WEEKDAYS_PT[d.weekday()]} {d.strftime("%d/%m")}'})
    return dias


# ── Painel do psicólogo: configurar agenda + ver agendamentos ──────────────────
@amparo_bp.route('/agenda')
@_login_required
def agenda():
    psi = _psi_atual()
    cfg = ensure_agenda_config(psi['id'])
    horarios = {h['dia_semana']: h for h in get_horarios(psi['id'])}
    proximos = listar_agendamentos(psi['id'], desde=_agora_brt().strftime('%Y-%m-%d'))
    link = url_for('amparo.agendar_publico', slug=cfg['slug'], _external=True)
    return render_template('amparo/agenda.html', psi=psi, cfg=cfg, horarios=horarios,
                           proximos=proximos, link_publico=link,
                           weekdays=list(enumerate(WEEKDAYS_FULL)),
                           wa_ok=amparo_wa.wa_configurado())


@amparo_bp.route('/agenda/salvar', methods=['POST'])
@_login_required
def agenda_salvar():
    psi = _psi_atual()
    ensure_agenda_config(psi['id'])
    f = request.form
    janelas = []
    for d in range(7):
        ini = (f.get(f'inicio_{d}') or '').strip()
        fim = (f.get(f'fim_{d}') or '').strip()
        if ini and fim:
            janelas.append((d, ini, fim))
    replace_horarios(psi['id'], janelas)
    set_agenda_config(
        psi['id'],
        duracao_min=f.get('duracao_min') or 50,
        intervalo_min=f.get('intervalo_min') or 10,
        antecedencia_horas=f.get('antecedencia_horas') or 12,
        booking_ativo=1 if f.get('booking_ativo') else 0,
        valor_sessao=(f.get('valor_sessao') or None))
    return redirect('/amparo/agenda')


@amparo_bp.route('/agenda/<int:ag_id>/status', methods=['POST'])
@_login_required
def agenda_status(ag_id):
    psi = _psi_atual()
    novo = request.form.get('status')
    if novo in ('agendado', 'confirmado', 'cancelado', 'realizado', 'faltou'):
        set_status_agendamento(psi['id'], ag_id, novo)
    return redirect('/amparo/agenda')


# ── Página pública de autoagendamento (o paciente marca sozinho) ───────────────
@amparo_bp.route('/agendar/<slug>', methods=['GET', 'POST'])
def agendar_publico(slug):
    cfg = get_agenda_by_slug(slug)
    if not cfg or not cfg['booking_ativo']:
        return render_template('amparo/agendar_off.html', cfg=cfg), 404 if not cfg else 200

    psi_id = cfg['psi_id']

    if request.method == 'POST':
        nome = (request.form.get('nome') or '').strip()
        fone = (request.form.get('telefone') or '').strip()
        data = (request.form.get('data') or '').strip()
        hora = (request.form.get('hora') or '').strip()
        erro = None
        if not (nome and fone and data and hora):
            erro = 'Preencha seu nome, WhatsApp e escolha um horário.'
        elif hora not in _slots_do_dia(psi_id, cfg, data):
            erro = 'Ops, esse horário acabou de ser ocupado. Escolha outro, por favor.'
        if erro:
            dias = _proximos_dias(psi_id)
            slots = _slots_do_dia(psi_id, cfg, data) if data else []
            return render_template('amparo/agendar.html', cfg=cfg, dias=dias,
                                   data_sel=data, slots=slots, erro=erro)

        pid, token, is_new = get_or_create_paciente(psi_id, nome, fone)
        criar_agendamento(psi_id, pid, data, hora, cfg['duracao_min'], origem='paciente')

        # Confirmação por WhatsApp (graceful: loga se não houver credencial)
        d_br = datetime.strptime(data, '%Y-%m-%d').strftime('%d/%m')
        link_consent = url_for('amparo.consentimento_paciente', token=token, _external=True)
        msg = (f'Olá, {nome.split(" ")[0]}! Sua sessão com {cfg["psi_nome"]} está marcada para '
               f'{d_br} às {hora}. 💙')
        if is_new:
            msg += (f'\n\nSeu psicólogo(a) usa o Amparo para te acompanhar entre as sessões. '
                    f'Veja como funciona e autorize (é opcional): {link_consent}')
        amparo_wa.enviar(fone, msg, template=os.environ.get('WHATSAPP_TMPL_CONFIRMACAO'))

        return render_template('amparo/agendar_ok.html', cfg=cfg, nome=nome,
                               data_br=d_br, hora=hora)

    # GET
    data_sel = request.args.get('data', '')
    dias = _proximos_dias(psi_id)
    slots = _slots_do_dia(psi_id, cfg, data_sel) if data_sel else []
    return render_template('amparo/agendar.html', cfg=cfg, dias=dias,
                           data_sel=data_sel, slots=slots, erro=None)


# ══════════════════════════════════════════════════════════════════════════════
# Lembretes automáticos (thread) — envia lembrete ~24h antes da sessão.
# Graceful: se o WhatsApp não estiver configurado, fica ocioso (não spamma o log).
# ══════════════════════════════════════════════════════════════════════════════
def _ciclo_lembretes():
    intervalo = int(os.environ.get('AMPARO_LEMBRETE_INTERVALO_MIN', '30')) * 60
    while True:
        try:
            if amparo_wa.wa_configurado():
                _enviar_lembretes_devidos()
        except Exception as e:
            log.warning(f'[Amparo] ciclo de lembretes falhou: {e}')
        _time.sleep(intervalo)


def _enviar_lembretes_devidos():
    """Envia lembrete p/ sessões que acontecem entre +20h e +28h e ainda não foram lembradas."""
    agora = _agora_brt()
    ini = (agora + timedelta(hours=20))
    fim = (agora + timedelta(hours=28))
    conn = get_amparo_db()
    rows = conn.execute(
        '''SELECT ag.*, pa.nome AS pnome, pa.telefone AS pfone, p.nome AS psinome
           FROM amparo_agendamentos ag
           JOIN amparo_pacientes pa ON pa.id = ag.paciente_id
           JOIN amparo_psicologos p ON p.id = ag.psicologo_id
           WHERE ag.lembrete_enviado=0 AND ag.status IN ('agendado','confirmado')''').fetchall()
    conn.close()
    for r in rows:
        try:
            quando = datetime.strptime(f'{r["data"]} {r["hora"]}', '%Y-%m-%d %H:%M')
        except Exception:
            continue
        if not (ini <= quando <= fim) or not r['pfone']:
            continue
        msg = (f'Oi, {r["pnome"].split(" ")[0]}! Passando pra lembrar da sua sessão com '
               f'{r["psinome"]} amanhã às {r["hora"]}. Até lá! 💙')
        res = amparo_wa.enviar(r['pfone'], msg, template=os.environ.get('WHATSAPP_TMPL_LEMBRETE'))
        if res.get('ok'):
            c = get_amparo_db()
            c.execute('UPDATE amparo_agendamentos SET lembrete_enviado=1 WHERE id=?', (r['id'],))
            c.commit(); c.close()


def iniciar_lembretes_amparo():
    if os.environ.get('AMPARO_LEMBRETES', '1') != '1':
        log.info('[Amparo] lembretes desligados (AMPARO_LEMBRETES=0)')
        return
    t = threading.Thread(target=_ciclo_lembretes, daemon=True)
    t.start()
    log.info('[Amparo] thread de lembretes iniciada')


# ══════════════════════════════════════════════════════════════════════════════
# ASSINATURA (Lote 2) — Asaas. Quem paga é o psicólogo. Anual no PIX, mensal no cartão.
# ══════════════════════════════════════════════════════════════════════════════
_ASAAS_BASE = 'https://api.asaas.com/v3'


def _so_digitos(s):
    return ''.join(c for c in (s or '') if c.isdigit())


def _asaas_req(method, endpoint, data=None):
    try:
        r = _requests.request(method, f'{_ASAAS_BASE}{endpoint}',
            headers={'access_token': os.environ.get('ASAAS_API_KEY', ''),
                     'Content-Type': 'application/json'},
            json=data, timeout=20)
        return r.json() if r.content else {}
    except Exception as e:
        return {'error': str(e)}


def _asaas_cliente_psi(psi, cpf):
    """Cria/recupera o cliente Asaas do psicólogo e salva o customer_id. Retorna id ou ''."""
    if psi['asaas_customer_id']:
        return psi['asaas_customer_id']
    cpf = _so_digitos(cpf)
    cid = None
    if cpf:
        busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf}&limit=1')
        if busca.get('data'):
            cid = busca['data'][0].get('id')
    if not cid:
        resp = _asaas_req('POST', '/customers', {
            'name': psi['nome'], 'email': psi['email'],
            'mobilePhone': _so_digitos(psi['telefone']), 'cpfCnpj': cpf,
            'notificationDisabled': True})
        cid = resp.get('id')
    return cid or ''


def _valor_e_ciclo(plano_key, ciclo):
    """(valor, cycle_asaas). Anual = 10x o mês (2 meses grátis)."""
    preco = PLANOS[plano_key]['preco']
    if ciclo == 'anual':
        return round(preco * 10, 2), 'YEARLY'
    return preco, 'MONTHLY'


@amparo_bp.route('/planos')
@_login_required
def planos():
    psi = _psi_atual()
    return render_template('amparo/planos.html', psi=psi, planos=PLANOS,
                           asaas_ok=bool(os.environ.get('ASAAS_API_KEY')))


@amparo_bp.route('/assinar', methods=['POST'])
@_login_required
def assinar():
    psi = _psi_atual()
    plano = request.form.get('plano')
    ciclo = request.form.get('ciclo', 'mensal')
    cpf   = _so_digitos(request.form.get('cpf'))

    if plano not in PLANOS:
        return render_template('amparo/planos.html', psi=psi, planos=PLANOS,
                               asaas_ok=True, erro='Plano inválido.')
    if len(cpf) < 11:
        return render_template('amparo/planos.html', psi=psi, planos=PLANOS,
                               asaas_ok=True, erro='Informe um CPF/CNPJ válido para a cobrança.')

    cid = _asaas_cliente_psi(psi, cpf)
    if not cid:
        return render_template('amparo/planos.html', psi=psi, planos=PLANOS, asaas_ok=True,
                               erro='Não foi possível iniciar a cobrança (verifique o CPF ou tente mais tarde).')

    valor, cycle = _valor_e_ciclo(plano, ciclo)
    # billingType UNDEFINED = o psicólogo escolhe PIX ou cartão no checkout do Asaas.
    sub = _asaas_req('POST', '/subscriptions', {
        'customer': cid, 'billingType': 'UNDEFINED', 'value': valor, 'cycle': cycle,
        'nextDueDate': _agora_brt().strftime('%Y-%m-%d'),
        'description': f'Amparo {PLANOS[plano]["nome"]} ({ciclo})',
        'externalReference': f'amparo_{cid}_{plano}',
    })
    sub_id = sub.get('id')
    if not sub_id:
        log.warning(f'[Amparo] Falha ao criar assinatura Asaas: {sub}')
        return render_template('amparo/planos.html', psi=psi, planos=PLANOS, asaas_ok=True,
                               erro='Não foi possível criar a assinatura. Tente novamente.')

    set_assinatura_pendente(psi['id'], cpf, cid, sub_id)
    registra_pagamento(psi['id'], plano, ciclo, valor, 'pendente')

    # Leva o psicólogo direto pra fatura (PIX/cartão) hospedada no Asaas.
    pagtos = _asaas_req('GET', f'/subscriptions/{sub_id}/payments?limit=1')
    url = (pagtos.get('data') or [{}])[0].get('invoiceUrl')
    return redirect(url or '/amparo/assinatura')


@amparo_bp.route('/assinatura')
@_login_required
def assinatura():
    psi = _psi_atual()
    plano = PLANOS.get(psi['plano'])
    return render_template('amparo/assinatura.html', psi=psi, plano=plano)


def amparo_webhook_assinatura(asaas_customer_id, plano_key, ativar):
    """Chamado pelo webhook global (app.py) p/ refs 'amparo_<customer_id>_<plano>'.
    ativar=True → libera o plano; False → suspende (corta acesso)."""
    if ativar:
        plano = PLANOS.get(plano_key)
        if not plano:
            return
        n = atualiza_assinatura_por_customer(asaas_customer_id, plano_key, plano['limite'], 'ativo')
        log.info(f'[Amparo] Assinatura ATIVA (customer {asaas_customer_id}, plano {plano_key}, {n} linha)')
    else:
        conn = get_amparo_db()
        conn.execute('UPDATE amparo_psicologos SET status=? WHERE asaas_customer_id=?',
                     ('suspenso', asaas_customer_id))
        conn.commit(); conn.close()
        log.info(f'[Amparo] Assinatura SUSPENSA (customer {asaas_customer_id})')
