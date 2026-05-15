"""
app.py — 4KITEM Plataforma de Soluções Digitais
"""
import os
import logging
import threading
import re as _re
import unicodedata
import json as _json
from datetime import datetime, timedelta
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
        if not session.get('mz_user_id'):
            return redirect('/mandazap/entrar')
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
    'solo':      {'label': 'Solo',      'numbers': 1,  'daily_limit': 399,   'pdf_limit': 5,    'price': 79},
    'duplo':     {'label': 'Duplo',     'numbers': 2,  'daily_limit': 799,   'pdf_limit': 15,   'price': 149},
    'trio':      {'label': 'Trio',      'numbers': 3,  'daily_limit': 1199,  'pdf_limit': 30,   'price': 219},
    'quadruplo': {'label': 'Quádruplo', 'numbers': 4,  'daily_limit': 1599,  'pdf_limit': 60,   'price': 289},
    'agencia':   {'label': 'Agência',   'numbers': 10, 'daily_limit': 99999, 'pdf_limit': 9999, 'price': 499},
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
        phone    = request.form.get('phone', '').strip()
        password = request.form.get('password', '').strip()
        conn = get_saas_db()
        biz = conn.execute(
            'SELECT * FROM agenda_businesses WHERE phone=? AND active=1', (phone,)
        ).fetchone()
        conn.close()
        if biz and check_password_hash(biz['password_hash'], password):
            session['agenda_business_id']   = biz['id']
            session['agenda_business_slug'] = biz['slug']
            session['agenda_business_name'] = biz['name']
            return redirect('/agenda/painel')
        error = 'Telefone ou senha incorretos.'
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
    conn.close()
    return render_template('agenda/painel.html',
                           biz=biz, services=services,
                           availability=availability,
                           appointments=appointments,
                           today=today,
                           weekday_names=WEEKDAY_NAMES,
                           business_types=BUSINESS_TYPES)


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
    conn.execute('UPDATE agenda_appointments SET status=? WHERE id=? AND business_id=?',
                 (new_status, appt_id, biz_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'status': new_status})


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
    conn.close()
    return jsonify({'success': True, 'business_name': biz['name'], 'business_phone': biz['phone']})


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
    return render_template('saas_admin.html', subscribers=subscribers, businesses=businesses,
                           mz_users=mz_users, mz_plans=MANDAZAP_PLANS, bau_users=bau_users)


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
    return redirect('/mandazap/entrar')


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
def mz_ajuda():
    if not session.get('mz_user_id'):
        return redirect('/mandazap/entrar')
    return render_template('mandazap/ajuda.html')


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

        # 1. Tenta criar instância (ignora erro se já existir)
        cr = _req.post(f"{evo_url}/instance/create", headers=headers,
                       json={'instanceName': instance, 'qrcode': True,
                             'integration': 'WHATSAPP-BAILEYS'}, timeout=15)
        cr_data = cr.json() if cr.content else {}
        qr = _extract_qr(cr_data)

        # 2. Se não veio no create, chama /connect
        if not qr:
            r2   = _req.get(f"{evo_url}/instance/connect/{instance}", headers=headers, timeout=15)
            qr   = _extract_qr(r2.json() if r2.content else {})

        # 3. Último recurso: /instance/fetchInstances + connect
        if not qr:
            _req.delete(f"{evo_url}/instance/{instance}/delete", headers=headers, timeout=10)
            cr2  = _req.post(f"{evo_url}/instance/create", headers=headers,
                             json={'instanceName': instance, 'qrcode': True,
                                   'integration': 'WHATSAPP-BAILEYS'}, timeout=15)
            qr   = _extract_qr(cr2.json() if cr2.content else {})

        if qr:
            if not qr.startswith('data:'):
                qr = 'data:image/png;base64,' + qr
            return jsonify({'qr': qr})

        return jsonify({'erro': 'QR Code não disponível ainda. Aguarde 5 segundos e tente novamente.'})
    except Exception as e:
        log.error(f"QR error: {e}")
        return jsonify({'erro': f'Erro ao conectar com a Evolution API: {str(e)}'})


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
    import csv, io
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


# ── Campanhas ─────────────────────────────────────────────────────────────────

@app.route('/mandazap/campanhas/add', methods=['POST'])
@_mandazap_login_required
def mz_campaign_add():
    user_id      = session['mz_user_id']
    name         = request.form.get('name', '').strip()
    message      = request.form.get('message', '').strip()
    media_type   = request.form.get('media_type', 'text')
    list_id      = request.form.get('list_id') or None
    number_id    = request.form.get('number_id') or None
    scheduled_at = request.form.get('scheduled_at', '').strip() or None
    if name and message:
        total = 0
        if list_id:
            conn  = get_saas_db()
            total = conn.execute(
                'SELECT COUNT(*) FROM mandazap_list_contacts WHERE list_id=?', (list_id,)
            ).fetchone()[0]
            conn.close()
        conn = get_saas_db()
        conn.execute('''
            INSERT INTO mandazap_campaigns
            (user_id, name, message, media_type, list_id, number_id, status, total, sent, scheduled_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,0,?,?)
        ''', (user_id, name, message, media_type, list_id, number_id,
              'agendada' if scheduled_at else 'rascunho', total,
              scheduled_at, datetime.now().isoformat()))
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
    if name and message:
        conn = get_saas_db()
        conn.execute(
            'INSERT INTO mandazap_templates (user_id, name, message, media_type, created_at) VALUES (?,?,?,?,?)',
            (user_id, name, message, media_type, datetime.now().isoformat())
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
#  STARTUP
# ══════════════════════════════════════════════════════════════════════════

def _startup():
    try:
        init_db()
        init_saas_db()
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
    except Exception as e:
        log.error(f"Startup error: {e}")

with app.app_context():
    _startup()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
