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
                       registra_pagamento, pode_ativar_paciente,
                       listar_pacientes, get_paciente, get_paciente_por_fone,
                       set_motor_paciente, criar_interacao, interacao_aberta,
                       registrar_resposta, feed_interacoes, humor_serie, stats_adesao,
                       criar_tarefa, listar_tarefas, log_crise, crises_recentes,
                       marcar_crise_avisada, add_cashback, get_cashback,
                       criar_evolucao, listar_evolucoes, get_evolucao, update_evolucao)
import amparo_wa

log = logging.getLogger('amparo')

amparo_bp = Blueprint('amparo', __name__, url_prefix='/amparo')

# ── Planos (B2B — quem paga é o psicólogo) ─────────────────────────────────────
# Custo variável real ~R$1/paciente ativo/mês (WhatsApp é o driver; IA = centavos).
PLANOS = {
    'essencial': {'nome': 'Essencial', 'preco': 149.90, 'limite': 20,
                  'desc': 'Agenda + lembrete + motor de cuidado para até 20 pacientes'},
    'pro':       {'nome': 'Pro',       'preco': 249.90, 'limite': 50,
                  'desc': 'Tudo do Essencial + até 50 pacientes + planos e cashback de adesão'},
    'clinica':   {'nome': 'Clínica',   'preco': 449.90, 'limite': 9999,
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
    if request.args.get('ref'):                       # indicação de afiliado
        session['amparo_ref'] = request.args.get('ref')
    return render_template('amparo/landing.html', planos=PLANOS)


@amparo_bp.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if request.args.get('ref'):
        session['amparo_ref'] = request.args.get('ref')
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
            'plano, pacientes_limite, trial_expires, afiliado_ref, termo_aceito, ultimo_acesso) '
            'VALUES (?,?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)',
            (nome, email, crp, tel, generate_password_hash(senha),
             'trial', TRIAL_LIMITE, (datetime.utcnow() + timedelta(days=TRIAL_DIAS)).isoformat(),
             session.get('amparo_ref')))
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


def amparo_webhook_assinatura(asaas_customer_id, plano_key, ativar, payment_id='', valor_pago=0):
    """Chamado pelo webhook global (app.py) p/ refs 'amparo_<customer_id>_<plano>'.
    ativar=True → libera o plano; False → suspende (corta acesso)."""
    if ativar:
        plano = PLANOS.get(plano_key)
        if not plano:
            return
        n = atualiza_assinatura_por_customer(asaas_customer_id, plano_key, plano['limite'], 'ativo')
        log.info(f'[Amparo] Assinatura ATIVA (customer {asaas_customer_id}, plano {plano_key}, {n} linha)')
        # Comissão de afiliado (recorrente, idempotente + anti-autoindicação na própria função)
        try:
            conn = get_amparo_db()
            p = conn.execute('SELECT nome, email, cpf, afiliado_ref FROM amparo_psicologos '
                             'WHERE asaas_customer_id=?', (asaas_customer_id,)).fetchone()
            conn.close()
            if p and p['afiliado_ref'] and payment_id:
                from afiliados import registrar_comissao
                registrar_comissao(p['afiliado_ref'], 'amparo', payment_id, p['nome'],
                                   cliente_email=p['email'], cliente_cpf=p['cpf'],
                                   valor_pago=valor_pago)   # 20% recorrente do que o cliente pagou
        except Exception as _eaf:
            log.warning(f'[Amparo] comissão afiliado: {_eaf}')
    else:
        conn = get_amparo_db()
        conn.execute('UPDATE amparo_psicologos SET status=? WHERE asaas_customer_id=?',
                     ('suspenso', asaas_customer_id))
        conn.commit(); conn.close()
        log.info(f'[Amparo] Assinatura SUSPENSA (customer {asaas_customer_id})')


# ══════════════════════════════════════════════════════════════════════════════
# MOTOR DE ENGAJAMENTO (Lote 3) — interações no WhatsApp + painel + crise
# Mensagens ROTEIRIZADAS (escopo fechado). A camada de calor com IA (Gemini) é o L4.
# ══════════════════════════════════════════════════════════════════════════════
import re as _re2

CASHBACK_POR_RESPOSTA = float(os.environ.get('AMPARO_CASHBACK_RESPOSTA', '0.50'))

MSG_CHECKIN = ("Oi, {nome}! 💙 Como você está se sentindo hoje, numa escala de 1 a 5? "
               "(1 = bem difícil · 5 = muito bem). Se quiser, me conta em uma palavra também.")
MSG_TAREFA  = ("Oi, {nome}! 🌱 Passando pra lembrar de algo combinado com {psi}: {tarefa}. "
               "Como está indo?")
MSG_ESCALA  = ("Oi, {nome}. {psi} pediu um check-in rápido pra entender como você tem estado. "
               "Quando puder, me conta como foram seus últimos dias — pode ser uma frase curta. 💙")
MSG_RECEBIDO   = "Obrigado por compartilhar comigo 💙 Anotei aqui pra você levar pra sua sessão. Cuide-se!"
MSG_FORA_FLUXO = ("Eu sou o assistente de acompanhamento do seu psicólogo(a) e fico por aqui só pro "
                  "combinado entre as sessões 💙 Se precisar conversar, fale com ele(a). "
                  "Em emergência: CVV 188 (24h) ou SAMU 192.")


def _extrai_humor(texto):
    """Extrai um humor 1..5 de uma resposta de check-in (dígito isolado)."""
    m = _re2.search(r'\b([1-5])\b', texto or '')
    return int(m.group(1)) if m else None


# ── Camada de calor com IA (Lote 4) — Gemini, SEMPRE dentro das regras do CFP ──
# Liga só se houver GEMINI_API_KEY e AMPARO_IA_CALOR!=0. Cai pro texto fixo em
# qualquer falha. A IA NUNCA faz terapia: só dá calor a uma mensagem de escopo fechado.
def _ia_ligada():
    return bool(GEMINI_KEY) and os.environ.get('AMPARO_IA_CALOR', '1') == '1'


def _mensagem_calorosa(base, nome, psi_nome):
    """Reescreve uma mensagem-base (já de escopo fechado) de forma mais calorosa.
    Mantém o objetivo, sem adicionar conselho/diagnóstico. Fallback = a própria base."""
    if not _ia_ligada():
        return base
    instr = ("Reescreva a MENSAGEM BASE de forma calorosa, curta e natural, em português do "
             "Brasil, mantendo EXATAMENTE o mesmo objetivo e a mesma pergunta/recado. "
             "Máximo 2 frases. NÃO acrescente conselho, interpretação, diagnóstico ou opinião. "
             "Use o primeiro nome do paciente. Pode usar 1 emoji.")
    contents = [{'role': 'user', 'parts': [{'text':
        f"Paciente: {nome}\nPsicólogo(a): {psi_nome}\nMENSAGEM BASE: {base}"}]}]
    try:
        txt, _, _ = _gemini_call(SYSTEM_MOTOR_BASE + '\n\n' + instr, contents,
                                 max_tokens=160, temperature=0.7)
        return (txt or base).strip() or base
    except Exception as e:
        log.warning(f'[Amparo] IA calor falhou (usando texto fixo): {e}')
        return base


def _acolhe_resposta_ia(nome, resposta):
    """Resposta calorosa ao que o paciente respondeu — APENAS acolhe e devolve ao humano.
    NUNCA aconselha/interpreta. Fallback = MSG_RECEBIDO. (A crise já foi tratada antes.)"""
    if not _ia_ligada():
        return MSG_RECEBIDO
    instr = ("O paciente respondeu a um check-in do acompanhamento. Escreva uma resposta MUITO "
             "curta (1 a 2 frases), calorosa e em pt-BR, que APENAS acolhe o sentimento e diz "
             "que você anotou para ele(a) levar à sessão com o psicólogo(a). É PROIBIDO dar "
             "conselho, interpretação, diagnóstico, opinião ou fazer nova pergunta sobre o "
             "problema. Termine devolvendo ao profissional humano. Pode usar 1 emoji.")
    contents = [{'role': 'user', 'parts': [{'text':
        f"O paciente {nome} respondeu: \"{(resposta or '')[:300]}\""}]}]
    try:
        txt, _, _ = _gemini_call(SYSTEM_MOTOR_BASE + '\n\n' + instr, contents,
                                 max_tokens=120, temperature=0.7)
        return (txt or MSG_RECEBIDO).strip() or MSG_RECEBIDO
    except Exception as e:
        log.warning(f'[Amparo] IA acolhimento falhou (usando texto fixo): {e}')
        return MSG_RECEBIDO


def _humor_chart(serie):
    """Pontos de um gráfico de linha do humor (1-5) p/ render inline em SVG. None se vazio."""
    vals = [r['humor'] for r in serie if r['humor']]
    if not vals:
        return None
    W, H, pad = 320, 90, 12
    n = len(vals)
    span = (W - 2 * pad)
    pts = []
    for i, v in enumerate(vals):
        x = pad + (span * (i / (n - 1)) if n > 1 else span / 2)
        y = pad + (H - 2 * pad) * (1 - (v - 1) / 4.0)   # humor 5 = topo
        pts.append((round(x, 1), round(y, 1)))
    return {'W': W, 'H': H, 'pts': pts,
            'poly': ' '.join(f'{x},{y}' for x, y in pts),
            'last': vals[-1], 'n': n}


# ── Pacientes (psicólogo) ──────────────────────────────────────────────────────
@amparo_bp.route('/pacientes')
@_login_required
def pacientes():
    psi = _psi_atual()
    return render_template('amparo/pacientes.html', psi=psi,
                           pacientes=listar_pacientes(psi['id']),
                           ativos=conta_pacientes_ativos(psi['id']),
                           limite=psi['pacientes_limite'],
                           crises=crises_recentes(psi['id'], 5))


@amparo_bp.route('/pacientes/novo', methods=['POST'])
@_login_required
def paciente_novo():
    psi = _psi_atual()
    nome = (request.form.get('nome') or '').strip()
    fone = (request.form.get('telefone') or '').strip()
    if nome and fone:
        pid, tok, _ = get_or_create_paciente(psi['id'], nome, fone, request.form.get('email', ''))
        link = url_for('amparo.consentimento_paciente', token=tok, _external=True)
        msg = (f"Olá, {nome.split(' ')[0]}! {psi['nome']} usa o Amparo para te acompanhar entre as "
               f"sessões pelo WhatsApp. Veja como funciona e autorize (é opcional): {link}")
        amparo_wa.enviar(fone, msg, template=os.environ.get('WHATSAPP_TMPL_CONVITE'))
    return redirect('/amparo/pacientes')


@amparo_bp.route('/pacientes/<int:pid>')
@_login_required
def paciente_detalhe(pid):
    psi = _psi_atual()
    pac = get_paciente(psi['id'], pid)
    if not pac:
        abort(404)
    conn = get_amparo_db()
    inter = conn.execute('SELECT * FROM amparo_interacoes WHERE paciente_id=? '
                         'ORDER BY created_at DESC LIMIT 30', (pid,)).fetchall()
    conn.close()
    resp, env = stats_adesao(pid)
    serie = humor_serie(pid)
    return render_template('amparo/paciente_detalhe.html', psi=psi, pac=pac,
                           interacoes=inter, humor=serie, chart=_humor_chart(serie),
                           tarefas=listar_tarefas(pid), evolucoes=listar_evolucoes(pid),
                           gemini_ok=bool(GEMINI_KEY),
                           adesao_resp=resp, adesao_env=env, cashback=get_cashback(pid),
                           erro=request.args.get('erro'))


@amparo_bp.route('/pacientes/<int:pid>/motor', methods=['POST'])
@_login_required
def paciente_motor(pid):
    psi = _psi_atual()
    pac = get_paciente(psi['id'], pid)
    if not pac:
        abort(404)
    ligar = request.form.get('ativo') == '1'
    if ligar:
        if pac['consentimento'] != 'aceito':
            return redirect(f'/amparo/pacientes/{pid}?erro=consentimento')
        if not pode_ativar_paciente(psi['id'], psi['pacientes_limite']):
            return redirect(f'/amparo/pacientes/{pid}?erro=limite')
    set_motor_paciente(psi['id'], pid, ligar)
    return redirect(f'/amparo/pacientes/{pid}')


@amparo_bp.route('/pacientes/<int:pid>/enviar', methods=['POST'])
@_login_required
def paciente_enviar(pid):
    psi = _psi_atual()
    pac = get_paciente(psi['id'], pid)
    if not pac:
        abort(404)
    # Só dispara com consentimento aceito E motor ligado (guard-rail).
    if pac['consentimento'] != 'aceito' or not pac['motor_ativo']:
        return redirect(f'/amparo/pacientes/{pid}?erro=consentimento')

    tipo = request.form.get('tipo')
    nome1 = pac['nome'].split(' ')[0]
    psi1 = psi['nome'].split(' ')[0]

    escala = None
    if tipo == 'checkin':
        base = MSG_CHECKIN.format(nome=nome1); tipo_db = 'checkin'
    elif tipo == 'tarefa':
        desc = (request.form.get('descricao') or '').strip()
        if not desc:
            return redirect(f'/amparo/pacientes/{pid}')
        criar_tarefa(pid, psi['id'], desc)
        base = MSG_TAREFA.format(nome=nome1, psi=psi1, tarefa=desc); tipo_db = 'tarefa'
    elif tipo in ('PHQ-9', 'GAD-7'):
        base = MSG_ESCALA.format(nome=nome1, psi=psi1); tipo_db = 'escala'; escala = tipo
    else:
        return redirect(f'/amparo/pacientes/{pid}')

    # Camada de calor (IA) — reescreve a mensagem-base de escopo fechado. Registra o que foi enviado.
    msg = _mensagem_calorosa(base, nome1, psi1)
    criar_interacao(pid, psi['id'], tipo_db, msg, escala_nome=escala)
    amparo_wa.enviar(pac['telefone'], msg, template=os.environ.get('WHATSAPP_TMPL_MOTOR'))
    return redirect(f'/amparo/pacientes/{pid}')


# ── Painel de sinais ───────────────────────────────────────────────────────────
@amparo_bp.route('/sinais')
@_login_required
def sinais():
    psi = _psi_atual()
    return render_template('amparo/sinais.html', psi=psi,
                           feed=feed_interacoes(psi['id'], 50),
                           crises=crises_recentes(psi['id'], 20))


# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK DE ENTRADA (WhatsApp Cloud API) — onde o paciente responde
# 🆘 O protocolo de crise roda ANTES de tudo e é HARD-CODED.
# ══════════════════════════════════════════════════════════════════════════════
@amparo_bp.route('/wa/webhook', methods=['GET', 'POST'])
def wa_webhook():
    # Verificação inicial da Meta (handshake)
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == os.environ.get('WHATSAPP_VERIFY_TOKEN', ''):
            return request.args.get('hub.challenge', ''), 200
        return 'forbidden', 403
    try:
        data = request.get_json(force=True) or {}
        _processa_entrada_wa(data)
    except Exception as e:
        log.warning(f'[Amparo] webhook entrada erro: {e}')
    # Sempre 200 — senão a Meta re-tenta em loop.
    return jsonify({'ok': True}), 200


def _processa_entrada_wa(data):
    for entry in data.get('entry', []):
        for ch in entry.get('changes', []):
            for m in (ch.get('value', {}) or {}).get('messages', []):
                if m.get('type') != 'text':
                    continue
                frm = m.get('from', '')
                texto = ((m.get('text') or {}).get('body') or '')
                _trata_resposta_paciente(frm, texto)


def _trata_resposta_paciente(fone, texto):
    pac = get_paciente_por_fone(fone)
    if not pac:
        return  # número desconhecido — ignora (não é chat aberto)
    psi_id = pac['psicologo_id']

    # (1) 🆘 CRISE — vem ANTES de qualquer outra coisa. NÃO conversa, encaminha.
    if detecta_crise(texto):
        cid = log_crise(pac['id'], psi_id, texto)
        amparo_wa.enviar(fone, MSG_CRISE)               # CVV 188 / SAMU 192 / CAPS
        psi = get_psicologo(psi_id)
        if psi and psi['telefone']:
            alerta = (f"⚠️ Amparo: {pac['nome']} enviou algo que pode indicar risco. "
                      f"Entre em contato. (Trecho: \"{texto[:80]}\")")
            if amparo_wa.enviar(psi['telefone'], alerta).get('ok'):
                marcar_crise_avisada(cid)
        ab = interacao_aberta(pac['id'])
        if ab:
            registrar_resposta(ab['id'], '[risco — encaminhado a ajuda humana]', risco=1)
        log.info(f'[Amparo] 🆘 CRISE detectada (paciente {pac["id"]}) — encaminhado + psicólogo avisado')
        return

    # (2) resposta normal: só processa se houver interação aberta (sem chat terapêutico livre)
    ab = interacao_aberta(pac['id'])
    if not ab:
        amparo_wa.enviar(fone, MSG_FORA_FLUXO)
        return
    humor = _extrai_humor(texto) if ab['tipo'] == 'checkin' else None
    registrar_resposta(ab['id'], texto[:500], humor=humor)
    if CASHBACK_POR_RESPOSTA > 0:
        add_cashback(pac['id'], CASHBACK_POR_RESPOSTA)   # cashback de adesão
    # Acolhimento caloroso (IA) que SEMPRE devolve ao humano — fallback p/ texto fixo.
    amparo_wa.enviar(fone, _acolhe_resposta_ia(pac['nome'].split(' ')[0], texto))


# ══════════════════════════════════════════════════════════════════════════════
# AI SCRIBE (Lote 6) — evolução clínica assistida. 🟡 CFP: permitido COM
# consentimento + supervisão. A IA ORGANIZA o que o psicólogo deu; ele revisa.
# ══════════════════════════════════════════════════════════════════════════════
SYSTEM_EVOLUCAO = """Você organiza ANOTAÇÕES de uma sessão de psicoterapia (fornecidas pelo \
próprio psicólogo) em uma EVOLUÇÃO CLÍNICA estruturada e profissional, em português do Brasil.

REGRAS (Conselho Federal de Psicologia) — INEGOCIÁVEIS:
- Você é ferramenta de APOIO. NÃO invente fatos, sintomas, hipóteses ou diagnósticos que o \
psicólogo não tenha registrado. Apenas organize e redija melhor o que foi fornecido.
- NÃO dê diagnóstico (CID/DSM), conduta ou prescrição por conta própria. Se o psicólogo registrou, \
mantenha; nunca acrescente o que ele não disse.
- O resultado é um RASCUNHO para o psicólogo revisar e editar. Ele é o responsável técnico.

ESTRUTURE assim (omita seções sem informação):
📋 QUEIXA/DEMANDA DA SESSÃO
🗣️ RELATO / CONTEÚDO TRABALHADO
🔍 OBSERVAÇÕES CLÍNICAS (apenas as registradas pelo profissional)
🎯 CONDUTA / ENCAMINHAMENTOS / TAREFAS (apenas as registradas)

Seja conciso, técnico e fiel. Ao final, em linha separada, escreva:
"— Rascunho gerado por IA a partir das suas anotações. Revise e edite antes de salvar no prontuário."
"""


def _gera_evolucao(notas, audio_b64=None, mime=None):
    """Gera um rascunho de evolução clínica (texto e/ou áudio). Requer GEMINI_API_KEY.
    Retorna o texto, ou None se sem chave / sem entrada / falha."""
    if not GEMINI_KEY:
        return None
    parts = []
    if notas:
        parts.append({'text': f'ANOTAÇÕES DO PSICÓLOGO:\n{notas}'})
    if audio_b64 and mime:
        parts.append({'inline_data': {'mime_type': mime, 'data': audio_b64}})
        parts.append({'text': 'Transcreva o áudio da sessão e organize na evolução estruturada acima.'})
    if not parts:
        return None
    try:
        txt, _, _ = _gemini_call(SYSTEM_EVOLUCAO, [{'role': 'user', 'parts': parts}],
                                 max_tokens=1200, temperature=0.3)
        return txt
    except Exception as e:
        log.warning(f'[Amparo] geração de evolução falhou: {e}')
        return None


@amparo_bp.route('/pacientes/<int:pid>/evolucao', methods=['POST'])
@_login_required
def evolucao_gerar(pid):
    psi = _psi_atual()
    pac = get_paciente(psi['id'], pid)
    if not pac:
        abort(404)
    # Consentimento do paciente p/ registrar a sessão — exigência do CFP.
    if not request.form.get('consentimento'):
        return redirect(f'/amparo/pacientes/{pid}?erro=evo_consent')
    notas = (request.form.get('notas') or '').strip()
    audio_b64 = mime = None
    f = request.files.get('audio')
    if f and f.filename:
        import base64
        raw = f.read()
        if len(raw) > 18 * 1024 * 1024:        # ~18MB = limite do envio inline ao Gemini
            return redirect(f'/amparo/pacientes/{pid}?erro=evo_audio')
        audio_b64 = base64.b64encode(raw).decode()
        mime = f.mimetype or 'audio/ogg'
    if not (notas or audio_b64):
        return redirect(f'/amparo/pacientes/{pid}?erro=evo_vazio')
    texto = _gera_evolucao(notas, audio_b64, mime)
    if not texto:
        return redirect(f'/amparo/pacientes/{pid}?erro=evo_ia')
    eid = criar_evolucao(pid, psi['id'], 'audio' if audio_b64 else 'texto', texto)
    return redirect(f'/amparo/evolucao/{eid}')


@amparo_bp.route('/evolucao/<int:eid>')
@_login_required
def evolucao_ver(eid):
    psi = _psi_atual()
    evo = get_evolucao(psi['id'], eid)
    if not evo:
        abort(404)
    pac = get_paciente(psi['id'], evo['paciente_id'])
    return render_template('amparo/evolucao.html', psi=psi, evo=evo, pac=pac)


@amparo_bp.route('/evolucao/<int:eid>/salvar', methods=['POST'])
@_login_required
def evolucao_salvar(eid):
    psi = _psi_atual()
    evo = get_evolucao(psi['id'], eid)
    if not evo:
        abort(404)
    update_evolucao(psi['id'], eid, (request.form.get('conteudo') or '').strip())
    return redirect(f'/amparo/pacientes/{evo["paciente_id"]}')
