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

# ── SaaS admin password ────────────────────────────────────────────────────────
SAAS_ADMIN_PW = os.environ.get('SAAS_ADMIN_PASSWORD', 'admin4kitem2024')

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
    'basico':  {'label': '🚗 Básico',    'price': 'R$ 19,90', 'vehicles': 1},
    'familia': {'label': '👨‍👩‍👧 Família', 'price': 'R$ 34,90', 'vehicles': 3},
    'frota':   {'label': '🚛 Frota',     'price': 'R$ 89,90', 'vehicles': 10},
}


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

        if not all([name, owner_name, phone, password]):
            error = 'Preencha todos os campos obrigatórios.'
        elif len(password) < 6:
            error = 'A senha precisa ter pelo menos 6 caracteres.'
        else:
            slug = _slugify(name) or 'negocio'
            conn = get_saas_db()
            base_slug, counter = slug, 1
            while conn.execute('SELECT id FROM agenda_businesses WHERE slug=?', (slug,)).fetchone():
                slug = f'{base_slug}-{counter}'; counter += 1
            trial_ends = (datetime.now() + timedelta(days=30)).isoformat()
            try:
                conn.execute('''
                    INSERT INTO agenda_businesses
                    (name, slug, owner_name, phone, email, business_type, password_hash, active, created_at, trial_ends)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ''', (name, slug, owner_name, phone, email, business_type,
                      generate_password_hash(password), datetime.now().isoformat(), trial_ends))
                conn.commit()
                biz = conn.execute('SELECT * FROM agenda_businesses WHERE slug=?', (slug,)).fetchone()
                conn.close()
                session['agenda_business_id']   = biz['id']
                session['agenda_business_slug'] = biz['slug']
                session['agenda_business_name'] = biz['name']
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
               COALESCE(s.price, 0) as price
        FROM agenda_appointments a
        LEFT JOIN agenda_services s ON a.service_id = s.id
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
    conn.close()
    return render_template('agenda/painel.html',
                           biz=biz, services=services,
                           availability=availability,
                           appointments=appointments,
                           today=today,
                           weekday_names=WEEKDAY_NAMES,
                           business_types=BUSINESS_TYPES,
                           hoje_count=hoje_count,
                           receita_mes=round(receita_mes, 2),
                           total_clientes=total_clientes)


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


@app.route('/agenda/painel/configuracoes', methods=['GET', 'POST'])
@_agenda_login_required
def agenda_configuracoes():
    biz_id = session['agenda_business_id']
    conn   = get_saas_db()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        fields = ['pix_chave','pix_nome','mandazap_instance',
                  'msg_confirmacao','msg_lembrete','msg_cancelamento']
        updates = {f: data.get(f,'') for f in fields}
        updates['mandazap_ativo'] = 1 if data.get('mandazap_ativo') else 0
        conn.execute('''UPDATE agenda_businesses SET
            pix_chave=?, pix_nome=?, mandazap_instance=?, mandazap_ativo=?,
            msg_confirmacao=?, msg_lembrete=?, msg_cancelamento=?
            WHERE id=?''',
            (updates['pix_chave'], updates['pix_nome'], updates['mandazap_instance'],
             updates['mandazap_ativo'], updates['msg_confirmacao'],
             updates['msg_lembrete'], updates['msg_cancelamento'], biz_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    biz = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone())
    conn.close()
    return jsonify(biz)


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


@app.route('/agendar/<slug>')
def agenda_booking(slug):
    conn = get_saas_db()
    biz = conn.execute(
        'SELECT * FROM agenda_businesses WHERE slug=? AND active=1', (slug,)
    ).fetchone()
    if not biz:
        conn.close()
        abort(404)
    services = [dict(r) for r in conn.execute(
        'SELECT * FROM agenda_services WHERE business_id=? AND active=1 ORDER BY name', (biz['id'],)
    ).fetchall()]
    conn.close()
    return render_template('agenda/booking.html', biz=dict(biz), services=services)


@app.route('/api/agenda/slots/<slug>')
def api_agenda_slots(slug):
    date_str   = request.args.get('date', '')
    service_id = request.args.get('service_id', '')
    conn = get_saas_db()
    biz = conn.execute('SELECT id FROM agenda_businesses WHERE slug=? AND active=1', (slug,)).fetchone()
    if not biz:
        conn.close()
        return jsonify({'slots': []})
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
    data           = request.get_json() or {}
    customer_name  = data.get('customer_name', '').strip()
    customer_phone = data.get('customer_phone', '').strip()
    service_id     = data.get('service_id')
    appt_date      = data.get('date', '').strip()
    appt_time      = data.get('time', '').strip()
    notes          = data.get('notes', '').strip()

    if not all([customer_name, customer_phone, appt_date, appt_time]):
        return jsonify({'success': False, 'error': 'Preencha todos os campos obrigatórios.'})

    conn = get_saas_db()
    biz = conn.execute('SELECT * FROM agenda_businesses WHERE slug=? AND active=1', (slug,)).fetchone()
    if not biz:
        conn.close()
        return jsonify({'success': False, 'error': 'Negócio não encontrado.'})

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

    conn.execute('''
        INSERT INTO agenda_appointments
        (business_id, service_id, customer_name, customer_phone, customer_notes,
         appointment_date, appointment_time, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    ''', (biz['id'], service_id or None, customer_name, customer_phone, notes,
          appt_date, appt_time, datetime.now().isoformat()))
    conn.commit()

    # Registra/atualiza cliente
    _agenda_upsert_customer(conn, biz['id'], customer_name, customer_phone)

    # WhatsApp automático (se MandaZap ativo)
    biz_full = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz['id'],)).fetchone())
    conn.close()

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
            f"Aguarde a confirmação. Em caso de dúvidas, entre em contato."
        )
        msg = (tpl
               .replace('{nome}', customer_name.split()[0])
               .replace('{servico}', svc_name)
               .replace('{data}', appt_date)
               .replace('{hora}', appt_time)
               .replace('{negocio}', biz_full['name']))
        _agenda_send_whatsapp(customer_phone, msg, biz_full['mandazap_instance'])

    return jsonify({'success': True, 'business_name': biz['name'], 'business_phone': biz['phone'],
                    'pix_chave': biz_full.get('pix_chave',''), 'pix_nome': biz_full.get('pix_nome','')})


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

        max_veh = {'basico': 1, 'familia': 3, 'frota': 10}.get(plano, 1)
        plates = []
        for i in range(1, max_veh + 1):
            p = request.form.get(f'plate_{i}', '').strip().upper()
            d = request.form.get(f'desc_{i}', '').strip()
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
                           plano=plano, phone=phone,
                           req_name=request.form.get('name', ''),
                           req_cpf=request.form.get('cpf', ''),
                           req_phone=request.form.get('phone', ''),
                           req_email=request.form.get('email', ''),
                           req_plate_1=request.form.get('plate_1', ''),
                           req_desc_1=request.form.get('desc_1', ''),
                           req_plate_2=request.form.get('plate_2', ''),
                           req_desc_2=request.form.get('desc_2', ''),
                           req_plate_3=request.form.get('plate_3', ''),
                           req_desc_3=request.form.get('desc_3', ''))


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
    return render_template('saas_admin.html',
                           subscribers=subscribers, businesses=businesses,
                           mz_users=mz_users, mz_plans=MANDAZAP_PLANS,
                           bau_users=bau_users,
                           kids_clients=kids_clients,
                           desp_users=desp_users)


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


# ── Admin KidsCurator — status / delete ───────────────────────────────────────

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
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not all([name, email, password]):
            error = 'Preencha todos os campos.'
        elif len(password) < 6:
            error = 'A senha deve ter pelo menos 6 caracteres.'
        else:
            conn = get_saas_db()
            exists = conn.execute('SELECT id FROM bau_users WHERE email=?', (email,)).fetchone()
            if exists:
                error = 'E-mail já cadastrado. Faça login.'
                conn.close()
            else:
                now = datetime.now()
                trial = (now + timedelta(days=30)).isoformat()
                conn.execute(
                    'INSERT INTO bau_users (name, email, password_hash, created_at, trial_ends) VALUES (?,?,?,?,?)',
                    (name, email, generate_password_hash(password), now.isoformat(), trial)
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
    return render_template('bau/painel.html',
                           entries=entries, categories=BAU_CATEGORIES,
                           q=q, cat=cat,
                           user_name=session.get('bau_user_name', ''))


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
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not all([name, email, password]):
            error = 'Preencha todos os campos.'
        elif len(password) < 6:
            error = 'A senha deve ter pelo menos 6 caracteres.'
        else:
            conn = get_saas_db()
            exists = conn.execute('SELECT id FROM mandazap_users WHERE email=?', (email,)).fetchone()
            if exists:
                error = 'E-mail já cadastrado. Faça login.'
                conn.close()
            else:
                now   = datetime.now()
                trial = (now + timedelta(days=2)).isoformat()
                conn.execute(
                    'INSERT INTO mandazap_users (name, email, password_hash, plan, created_at, trial_ends) VALUES (?,?,?,?,?,?)',
                    (name, email, generate_password_hash(password), 'solo', now.isoformat(), trial)
                )
                conn.commit()
                user = conn.execute('SELECT * FROM mandazap_users WHERE email=?', (email,)).fetchone()
                conn.close()
                session['mz_user_id']   = user['id']
                session['mz_user_name'] = user['name']
                session['mz_plan']      = user['plan']
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


@app.route('/mandazap/painel')
@_mandazap_login_required
def mandazap_painel():
    user_id  = session['mz_user_id']
    plan_key = session.get('mz_plan', 'solo')
    conn     = get_saas_db()

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


# ── QR Code ───────────────────────────────────────────────────────────────────

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
    # Tenta buscar QR da Evolution API
    evo_url = os.environ.get('EVOLUTION_API_URL', '')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    if not evo_url or not evo_key:
        return jsonify({'erro': 'Evolution API não configurada. Configure EVOLUTION_API_URL e EVOLUTION_API_KEY nas variáveis de ambiente do Railway.'})
    try:
        import requests as _req
        instance = f"mz{user_id}n{num_id}"
        headers  = {'apikey': evo_key, 'Content-Type': 'application/json'}

        def _extract_qr(data):
            """Procura base64 em vários formatos da Evolution API v1/v2."""
            if isinstance(data, dict):
                # v2: {"base64": "data:image/png;base64,..."}
                qr = data.get('base64') or data.get('qrcode', '')
                if isinstance(qr, dict):
                    qr = qr.get('base64', '')
                if not qr:
                    # aninha dentro de 'instance'
                    inner = data.get('instance', data.get('qrcode', {}))
                    if isinstance(inner, dict):
                        qr = inner.get('base64', '')
                return qr or ''
            return ''

        # 1. Força delete da instância antiga (limpa estado preso)
        for old_name in [instance, f"mz_{user_id}_{num_id}"]:
            try:
                _req.delete(f"{evo_url}/instance/{old_name}/delete", headers=headers, timeout=8)
            except Exception:
                pass

        time.sleep(1)

        # 2. Cria instância nova limpa
        cr     = _req.post(f"{evo_url}/instance/create", headers=headers,
                           json={'instanceName': instance, 'qrcode': True,
                                 'integration': 'WHATSAPP-BAILEYS'}, timeout=20)
        cr_data = cr.json() if cr.content else {}
        qr = _extract_qr(cr_data)

        # 3. Se não veio no create, chama /connect
        if not qr:
            time.sleep(2)
            r2 = _req.get(f"{evo_url}/instance/connect/{instance}", headers=headers, timeout=15)
            qr = _extract_qr(r2.json() if r2.content else {})

        if qr:
            if not qr.startswith('data:'):
                qr = 'data:image/png;base64,' + qr
            return jsonify({'qr': qr})

        return jsonify({'erro': 'QR Code não disponível ainda. Aguarde 5 segundos e tente novamente.'})
    except Exception as e:
        log.error(f"QR error: {e}")
        return jsonify({'erro': f'Erro ao conectar com a Evolution API: {str(e)}'})


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
    return ('"exists":false' in body or 'exists\\":false' in body
            or 'not exists' in b or 'invalid number' in b
            or 'phone not found' in b)


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
        return False, f"HTTP {r.status_code}: {body[:120]}", _is_invalid_number(body)
    except Exception as e:
        return False, str(e)[:120], False


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
            timeout=20,
        )
        if r.status_code in (200, 201):
            return True, '', False
        body = r.text[:300]
        return False, f"HTTP {r.status_code}: {body[:120]}", _is_invalid_number(body)
    except Exception as e:
        return False, str(e)[:120], False


def _antiban_delay(sent_count: int):
    """
    Delay humanizado anti-ban — imita comportamento humano:
    - Base: 15–45s aleatório com jitter ±20%
    - A cada 200 enviados: pausa extra longa 3–8 min (check ANTES do de 50)
    - A cada 50 enviados: pausa longa 1–3 min
    - Delays nunca são fixos (detectável pelo Meta)
    """
    base = random.uniform(15, 45)

    # IMPORTANTE: checar 200 ANTES do 50 — todo múltiplo de 200 também é de 50
    if sent_count > 0 and sent_count % 200 == 0:
        pausa = random.uniform(180, 480)
        log.info(f"Anti-ban: pausa extra longa de {pausa:.0f}s após {sent_count} enviados")
        time.sleep(pausa)
        return

    if sent_count > 0 and sent_count % 50 == 0:
        pausa = random.uniform(60, 180)
        log.info(f"Anti-ban: pausa longa de {pausa:.0f}s após {sent_count} enviados")
        time.sleep(pausa)
        return

    # Jitter ±20% para nunca ter intervalo previsível
    jitter = base * random.uniform(0.8, 1.2)
    log.debug(f"Anti-ban delay: {jitter:.1f}s")
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
    MAX_CONSEC   = 10  # aborta se 10 falhas reais consecutivas

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
                log.info(f"Campanha {cid} → {phone}: número inválido/sem WhatsApp — pulando")
                # Delay pequeno mesmo em inválido para não bater na API em rajada
                time.sleep(random.uniform(3, 8))
            else:
                # Falha real (API down, ban, timeout) — conta consecutiva
                consec_fails += 1
                if consec_fails >= MAX_CONSEC:
                    log.error(f"Campanha {cid}: {MAX_CONSEC} falhas reais consecutivas — abortando. Último erro: {err}")
                    conn.execute(
                        "UPDATE mandazap_campaigns SET status='erro', sent=?, finished_at=?, error_log=? WHERE id=?",
                        (sent_count, datetime.now().isoformat(),
                         f'Abortado após {MAX_CONSEC} falhas consecutivas (possível ban). {first_err}', cid)
                    )
                    conn.commit(); conn.close()
                    return
                # Delay progressivo em falhas reais: quanto mais falhas, maior a espera
                pausa_erro = random.uniform(10, 30) * consec_fails
                log.warning(f"Anti-ban: pausa de {pausa_erro:.0f}s após falha real ({consec_fails}/{MAX_CONSEC})")
                time.sleep(pausa_erro)

        # Atualiza progresso a cada envio
        conn2 = get_saas_db()
        conn2.execute("UPDATE mandazap_campaigns SET sent=? WHERE id=?", (sent_count, cid))
        conn2.commit(); conn2.close()

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
        'SELECT status FROM mandazap_campaigns WHERE id=? AND user_id=?', (cid, user_id)
    ).fetchone()
    conn.close()
    if not camp:
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    status = camp['status']
    if status == 'enviando':
        # Verifica se está realmente ativa ou presa (stale > 10 min sem update)
        last_update = camp.get('updated_at') or camp.get('created_at') or ''
        try:
            from datetime import timezone
            dt_upd = datetime.fromisoformat(last_update) if last_update else None
            minutos_parada = (datetime.now() - dt_upd).total_seconds() / 60 if dt_upd else 999
        except Exception:
            minutos_parada = 999
        if minutos_parada < 5:
            return jsonify({'erro': 'Campanha já está sendo enviada (aguarde).'}), 400
        # Presa há mais de 5 minutos — permite re-dispatch (thread morta)
        log.warning(f"[dispatch] Campanha {cid} presa em 'enviando' há {minutos_parada:.0f}min — forçando re-dispatch")
        conn.execute("UPDATE mandazap_campaigns SET status='rascunho' WHERE id=?", (cid,))
        conn.commit()
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
)
try:
    import desp_rag
    _rag_ok = True
    # Alimenta base interna de conhecimento em background (idempotente)
    threading.Thread(target=desp_rag.seed_conhecimento_base, daemon=True).start()
except ImportError:
    _rag_ok = False

DESP_CONFIG = {
    "nome":         os.environ.get("DESP_NOME",       "DIOGO KAUE LESSMANN"),
    "cpf":          os.environ.get("DESP_CPF",        "060.625.099-99"),
    "cnpj":         os.environ.get("DESP_CNPJ",       "28.858.795/0001-92"),
    "credencial":   os.environ.get("DESP_CREDENCIAL",  "2095"),
    "cidade":       os.environ.get("DESP_CIDADE",     "SCHROEDER"),
    "citran":       os.environ.get("DESP_CITRAN",     "Guaramirim"),
    "whatsapp":     os.environ.get("DESP_WHATSAPP",   "47991011351"),
    "whatsapp_fmt": "(47) 99101-1351",
}
DESP_PASSWORD = os.environ.get("DESP_PASSWORD", "lessmann2026")


def _desp_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('desp_logged'):
            return redirect('/despachante/login')
        return f(*args, **kwargs)
    return decorated


def _desp_globals():
    hoje = datetime.now()
    # Badge de alertas globais na sidebar — calculado 1x por request
    try:
        _st = desp_stats()
        _n_alertas = len(_st.get('parcelas_vencidas', [])) + len(_st.get('os_paradas', []))
    except Exception:
        _n_alertas = 0
    return dict(
        desp=DESP_CONFIG,
        servicos=DESP_SERVICOS,
        servicos_grupos=DESP_SERVICOS_GRUPOS,
        status_labels=DESP_STATUS_LABELS,
        hoje=hoje, mes_atual=hoje.month, meses=DESP_MESES,
        finais_placa_nav=sorted(DESP_FINAIS_PLACA.items(), key=lambda x: x[1]),
        n_alertas=_n_alertas,
    )


def desp_render(template, **ctx):
    return render_template(f'despachante/{template}', **{**_desp_globals(), **ctx})


# ── Login ─────────────────────────────────────────────────────────────────────
@app.route('/despachante/login', methods=['GET', 'POST'])
def desp_login():
    erro = None
    if request.method == 'POST':
        if request.form.get('senha') == DESP_PASSWORD:
            session['desp_logged'] = True
            return redirect('/despachante/')
        erro = 'Senha incorreta.'
    return render_template('despachante/login.html', erro=erro)

@app.route('/despachante/logout')
def desp_logout():
    session.pop('desp_logged', None)
    return redirect('/despachante/login')


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/despachante/')
@app.route('/despachante')
@_desp_login_required
def desp_dashboard():
    stats   = desp_stats()
    recentes = desp_listar_os(limit=8)
    return desp_render('dashboard.html', stats=stats, recentes=recentes)


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
        pix           = DESP_CONFIG.get('cpf', ''),
        despachante   = DESP_CONFIG['nome'].title(),
        whatsapp      = DESP_CONFIG['whatsapp_fmt'],
        cidade        = DESP_CONFIG['cidade'],
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
    desp_reg_hist(id, status, nota)
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
@_desp_login_required
def desp_precos():
    """Tabela de preços por serviço — visualizar e editar."""
    if request.method == 'POST':
        tabela = {}
        for svc in DESP_SERVICOS:
            val = request.form.get(f'preco_{svc}', '').strip()
            if val:
                try:
                    tabela[svc] = float(val.replace(',', '.'))
                except ValueError:
                    pass
        desp_set_precos(tabela)
        from flask import flash
        flash('Tabela de preços salva com sucesso!', 'ok')
        return redirect(url_for('desp_precos'))
    precos = desp_get_precos()
    return desp_render('precos.html', precos=precos, servicos=DESP_SERVICOS,
                       servicos_grupos=DESP_SERVICOS_GRUPOS)


@app.route('/despachante/api/preco/<servico>')
@_desp_login_required
def desp_api_preco(servico):
    """Retorna o preço padrão de um serviço para auto-fill na nova OS."""
    valor = desp_get_preco(servico)
    return jsonify({'servico': servico, 'preco': valor})


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


@app.route('/despachante/nao-licenciados/disparar', methods=['POST'])
@_desp_login_required
def desp_nao_lic_disparar():
    """Dispara WhatsApp para veículos sem licenciamento via Evolution API."""
    _req          = requests
    data          = request.get_json(silent=True) or {}
    exercicio     = int(data.get('exercicio', datetime.now().year))
    final         = data.get('final', '')
    mostrar       = data.get('mostrar', 'sem_os')
    mensagem_tpl  = data.get('mensagem', '').strip()
    delay_s       = max(1, min(30, int(data.get('delay', 4))))
    if not mensagem_tpl:
        return jsonify({'erro': 'Mensagem não pode estar vazia'}), 400
    evo_url      = os.environ.get('EVO_URL', '').rstrip('/')
    evo_key      = os.environ.get('EVO_KEY', '')
    evo_instance = os.environ.get('EVO_INSTANCE', '')
    if not evo_url or not evo_key or not evo_instance:
        return jsonify({'erro': 'WhatsApp não configurado (EVO_URL / EVO_KEY / EVO_INSTANCE).'}), 400
    veiculos = desp_nao_lic(exercicio=exercicio,
                             final_placa=final or None,
                             mostrar=mostrar)
    results = []
    for v in veiculos:
        tel = (v.get('telefone') or '').replace('(','').replace(')','').replace('-','').replace(' ','').replace('+','')
        if not tel:
            results.append({'nome': v.get('cliente','?'), 'status': 'sem_telefone'})
            continue
        if not tel.startswith('55'): tel = '55' + tel
        nome_curto = (v.get('cliente') or 'Cliente').split()[0].title()
        try:
            msg = mensagem_tpl.format(
                nome=nome_curto,
                nome_completo=(v.get('cliente') or '').title(),
                placa=(v.get('placa') or '').upper(),
                exercicio=exercicio,
                mes=v.get('mes_venc_nome', ''),
                marca=v.get('marca') or '',
                modelo=v.get('modelo') or '',
                despachante=DESP_CONFIG['nome'].title(),
                whatsapp=DESP_CONFIG['whatsapp_fmt'],
                cidade=DESP_CONFIG['cidade'],
            )
        except KeyError as e:
            return jsonify({'erro': f'Variável inválida na mensagem: {e}'}), 400
        try:
            resp = _req.post(
                f"{evo_url}/message/sendText/{evo_instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'number': tel, 'text': msg}, timeout=12
            )
            ok = resp.status_code in (200, 201)
            results.append({'nome': v.get('cliente',''), 'tel': tel,
                            'status': 'ok' if ok else 'erro',
                            'detalhe': '' if ok else resp.text[:120]})
        except Exception as e:
            results.append({'nome': v.get('cliente',''), 'tel': tel,
                            'status': 'erro', 'detalhe': str(e)[:120]})
        time.sleep(delay_s)
    sent   = sum(1 for r in results if r['status'] == 'ok')
    failed = len(results) - sent
    return jsonify({'sent': sent, 'failed': failed, 'results': results})


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
    """Download ZIP com todas as tabelas em CSV."""
    import zipfile
    conn = get_desp_conn()
    buf  = io.BytesIO()
    tabelas = ['clientes', 'veiculos', 'ordens_servico', 'os_parcelas',
               'os_historico', 'debitos_veiculo']
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
        # Metadados
        meta = f'Backup Lessmann Despachante\nData: {date.today()}\n'
        meta += f'OS: {conn.execute("SELECT COUNT(*) FROM ordens_servico").fetchone()[0]}\n'
        meta += f'Clientes: {conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]}\n'
        zf.writestr('_info.txt', meta)
    conn.close()
    buf.seek(0)
    from flask import send_file
    fname = f'lessmann_backup_{date.today()}.zip'
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True, download_name=fname)


@app.route('/despachante/manifest.json')
def desp_pwa_manifest():
    """PWA manifest para instalação como app."""
    manifest = {
        "name": "Lessmann Despachante",
        "short_name": "Lessmann",
        "description": "Sistema de gestão de OS para despachante documentalista",
        "start_url": "/despachante/dashboard",
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
    _req = requests
    data         = request.get_json(silent=True) or {}
    final        = data.get('final', '5')
    exercicio    = data.get('exercicio', datetime.now().year)
    situacao     = data.get('situacao', 'pendente')
    mensagem_tpl = data.get('mensagem', '').strip()
    delay_s      = max(1, min(30, int(data.get('delay', 4))))
    if not mensagem_tpl: return jsonify({'erro': 'Mensagem não pode estar vazia'}), 400
    evo_url = os.environ.get('EVO_URL','').rstrip('/')
    evo_key = os.environ.get('EVO_KEY','')
    evo_instance = os.environ.get('EVO_INSTANCE','')
    if not evo_url or not evo_key or not evo_instance:
        return jsonify({'erro': 'WhatsApp não configurado. Preencha EVO_URL, EVO_KEY e EVO_INSTANCE.'}), 400
    ordens  = desp_lista_final_placa(final, int(exercicio), situacao or None)
    mes_str = DESP_MESES[DESP_FINAIS_PLACA.get(final, 0)]
    results = []
    for o in ordens:
        tel = (o.get('telefone') or '').replace('(','').replace(')','').replace('-','').replace(' ','').replace('+','')
        if not tel: results.append({'nome': o.get('cliente','?'), 'status': 'sem_telefone'}); continue
        if not tel.startswith('55'): tel = '55' + tel
        nome_curto = (o.get('cliente') or 'Cliente').split()[0].title()
        try:
            msg = mensagem_tpl.format(
                nome=nome_curto, nome_completo=(o.get('cliente') or '').title(),
                placa=(o.get('placa') or '').upper(), exercicio=o.get('exercicio') or exercicio,
                mes=mes_str, despachante=DESP_CONFIG['nome'].title(),
                whatsapp=DESP_CONFIG['whatsapp_fmt'], cidade=DESP_CONFIG['cidade'],
            )
        except KeyError as e:
            return jsonify({'erro': f'Variável inválida: {e}'}), 400
        try:
            resp = _req.post(f"{evo_url}/message/sendText/{evo_instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'number': tel, 'text': msg}, timeout=12)
            ok = resp.status_code in (200, 201)
            results.append({'nome': o.get('cliente',''), 'tel': tel,
                            'status': 'ok' if ok else 'erro',
                            'detalhe': '' if ok else resp.text[:120]})
        except Exception as e:
            results.append({'nome': o.get('cliente',''), 'tel': tel, 'status': 'erro', 'detalhe': str(e)[:120]})
        time.sleep(delay_s)
    sent = sum(1 for r in results if r['status'] == 'ok')
    failed = len(results) - sent
    return jsonify({'sent': sent, 'failed': failed, 'results': results})


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
Extraia TODOS os dados visíveis de veículo e do proprietário/cliente.
Retorne APENAS um objeto JSON válido com os campos (use null para não encontrados):
{"placa":null,"renavam":null,"chassi":null,"marca":null,"modelo":null,"ano_fab":null,
"ano_mod":null,"cor":null,"especie":null,"categoria":null,"combustivel":null,"num_crv":null,
"nome":null,"cpf":null,"cnpj":null,"rg":null,"nascimento":null,"nome_mae":null,
"telefone":null,"email":null,"cep":null,"logradouro":null,"numero":null,
"complemento":null,"bairro":null,"cidade":null,"uf":null}
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
    stats_rag = desp_rag.db_stats() if _rag_ok else {'chunks': 0, 'documentos': 0, 'arquivos': []}
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
    stats = desp_rag.db_stats()
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

    except Exception as e:
        log.error(f"Startup error: {e}")

with app.app_context():
    _startup()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
