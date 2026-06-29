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
                   request, abort, url_for, session, Response, send_from_directory,
                   make_response)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('4kitem')

app = Flask(__name__)
# SECRET_KEY: sem fallback fixo. Sem a env, gera chave aleatória por boot
# (sessões caem a cada restart, mas NUNCA há chave pública no código-fonte).
_sk = os.environ.get('SECRET_KEY')
if not _sk:
    _sk = os.urandom(32).hex()
    log.warning('[SECRET_KEY] nao configurada — usando chave aleatoria temporaria. '
                'Defina SECRET_KEY no ambiente para manter sessoes entre deploys.')
app.secret_key = _sk
app.config['TEMPLATES_AUTO_RELOAD'] = True  # templates sempre relidos do disco


# ── Headers de segurança (aplicados a todas as respostas) ──────────────────────
@app.after_request
def _security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    resp.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    # Cache de assets estáticos: hoje vinham sem cache (re-download a cada visita).
    # 7 dias é seguro mesmo sem versionamento de URL; p/ cache imutável de 1 ano,
    # seria preciso versionar os nomes dos arquivos (polimento futuro).
    if request.path.startswith('/static/'):
        resp.headers['Cache-Control'] = 'public, max-age=604800'
    return resp


# ── SEO técnico + páginas legais ───────────────────────────────────────────────
@app.route('/robots.txt')
def _robots():
    body = ('User-agent: *\nAllow: /\nDisallow: /saas-admin\n'
            'Sitemap: https://4kitem.com.br/sitemap.xml\n')
    return Response(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def _sitemap():
    urls = ['/', '/agenda', '/alerta', '/bau', '/mandazap', '/mandaja',
            '/pubshow', '/slotzap', '/drzap', '/radar/', '/licita-norte/',
            '/amparo', '/privacidade', '/termos']
    items = ''.join(
        '<url><loc>https://4kitem.com.br%s</loc></url>' % u for u in urls)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + items + '</urlset>')
    return Response(xml, mimetype='application/xml')


@app.route('/privacidade')
def _privacidade():
    return render_template('legal/privacidade.html')


@app.route('/termos')
def _termos():
    return render_template('legal/termos.html')

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
SAAS_ADMIN_PW = os.environ.get('SAAS_ADMIN_PASSWORD') or os.urandom(24).hex()
if not os.environ.get('SAAS_ADMIN_PASSWORD'):
    log.warning('[SAAS_ADMIN] SAAS_ADMIN_PASSWORD nao configurada — painel admin '
                'inacessivel ate definir a env (sem senha publica no codigo).')

# ── DEV_WHITELIST — nunca bloqueados pelo anti-golpe (re-cadastro livre) ───────
# Adicione telefones (apenas dígitos) ou e-mails separados por vírgula na env:
#   DEV_WHITELIST=5511999999999,voce@exemplo.com
_wl_raw = os.environ.get('DEV_WHITELIST', '')
DEV_WHITELIST: set = {x.strip().lower() for x in _wl_raw.split(',') if x.strip()}

# Token de admin/dev por URL — FAIL-CLOSED: só autoriza se DEV_TOKEN estiver
# configurado no ambiente (sem fallback inseguro para um token conhecido).
_DEV_TOKEN_ENV = os.environ.get('DEV_TOKEN')
def _dev_token_ok(token):
    return bool(_DEV_TOKEN_ENV) and (token == _DEV_TOKEN_ENV)

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

# ── AgendaJá — Customização por ramo ──────────────────────────────────────────
# Para cada business_type: terminologia adaptada + serviços-modelo semeados no
# cadastro. 'servicos' = lista de (nome, duração_min, preço). Fallback = 'outros'.
AGENDA_SEGMENTS = {
    'barbearia': {
        'prof': 'Barbeiro', 'profs': 'Barbeiros', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Serviço', 'agendar_cta': 'Agendar horário',
        'servicos': [
            ('Corte de cabelo', 30, 35.0), ('Barba', 20, 25.0),
            ('Corte + Barba', 50, 55.0), ('Sobrancelha', 10, 10.0), ('Pezinho / acabamento', 10, 15.0),
        ],
    },
    'salao': {
        'prof': 'Profissional', 'profs': 'Profissionais', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Serviço', 'agendar_cta': 'Agendar horário',
        'servicos': [
            ('Corte feminino', 60, 60.0), ('Escova', 40, 45.0), ('Coloração', 120, 150.0),
            ('Hidratação', 60, 70.0), ('Manicure', 40, 35.0), ('Pedicure', 50, 40.0),
        ],
    },
    'estetica': {
        'prof': 'Esteticista', 'profs': 'Esteticistas', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Procedimento', 'agendar_cta': 'Agendar horário',
        'servicos': [
            ('Limpeza de pele', 60, 120.0), ('Design de sobrancelha', 30, 40.0),
            ('Massagem relaxante', 60, 100.0), ('Depilação', 40, 60.0), ('Drenagem linfática', 60, 110.0),
        ],
    },
    'clinica': {
        'prof': 'Profissional', 'profs': 'Profissionais', 'cliente': 'Paciente', 'clientes': 'Pacientes',
        'servico': 'Atendimento', 'agendar_cta': 'Marcar consulta',
        'servicos': [
            ('Consulta', 30, 200.0), ('Retorno', 20, 0.0), ('Avaliação inicial', 40, 250.0),
        ],
    },
    'dentista': {
        'prof': 'Dentista', 'profs': 'Dentistas', 'cliente': 'Paciente', 'clientes': 'Pacientes',
        'servico': 'Procedimento', 'agendar_cta': 'Marcar consulta',
        'servicos': [
            ('Consulta / avaliação', 30, 0.0), ('Limpeza', 40, 120.0),
            ('Restauração', 60, 200.0), ('Clareamento', 60, 600.0),
        ],
    },
    'psicologia': {
        'prof': 'Terapeuta', 'profs': 'Terapeutas', 'cliente': 'Paciente', 'clientes': 'Pacientes',
        'servico': 'Sessão', 'agendar_cta': 'Marcar sessão',
        'servicos': [
            ('Primeira sessão', 60, 180.0), ('Sessão de terapia', 50, 150.0),
        ],
    },
    'nutricao': {
        'prof': 'Nutricionista', 'profs': 'Nutricionistas', 'cliente': 'Paciente', 'clientes': 'Pacientes',
        'servico': 'Consulta', 'agendar_cta': 'Marcar consulta',
        'servicos': [
            ('Primeira consulta', 60, 200.0), ('Retorno', 30, 120.0), ('Avaliação física', 40, 150.0),
        ],
    },
    'fisioterapia': {
        'prof': 'Fisioterapeuta', 'profs': 'Fisioterapeutas', 'cliente': 'Paciente', 'clientes': 'Pacientes',
        'servico': 'Sessão', 'agendar_cta': 'Marcar sessão',
        'servicos': [
            ('Avaliação', 40, 150.0), ('Sessão de fisioterapia', 50, 100.0),
        ],
    },
    'pet': {
        'prof': 'Profissional', 'profs': 'Profissionais', 'cliente': 'Tutor', 'clientes': 'Tutores',
        'servico': 'Serviço', 'agendar_cta': 'Agendar horário',
        'servicos': [
            ('Banho', 60, 50.0), ('Tosa', 60, 70.0), ('Banho + Tosa', 90, 100.0),
            ('Consulta veterinária', 30, 120.0), ('Vacina', 15, 80.0),
        ],
    },
    'academia': {
        'prof': 'Instrutor', 'profs': 'Instrutores', 'cliente': 'Aluno', 'clientes': 'Alunos',
        'servico': 'Atividade', 'agendar_cta': 'Agendar horário',
        'servicos': [
            ('Aula experimental', 60, 0.0), ('Avaliação física', 40, 80.0), ('Personal trainer', 60, 100.0),
        ],
    },
    'mecanica': {
        'prof': 'Mecânico', 'profs': 'Mecânicos', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Serviço', 'agendar_cta': 'Agendar serviço',
        'servicos': [
            ('Troca de óleo', 40, 120.0), ('Revisão completa', 90, 250.0),
            ('Alinhamento e balanceamento', 60, 120.0), ('Diagnóstico', 30, 80.0),
        ],
    },
    'advocacia': {
        'prof': 'Profissional', 'profs': 'Profissionais', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Atendimento', 'agendar_cta': 'Agendar reunião',
        'servicos': [
            ('Consulta jurídica', 60, 250.0), ('Reunião', 45, 0.0),
        ],
    },
    'consultoria': {
        'prof': 'Consultor', 'profs': 'Consultores', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Sessão', 'agendar_cta': 'Agendar reunião',
        'servicos': [
            ('Sessão de consultoria', 60, 200.0), ('Reunião de diagnóstico', 45, 0.0),
        ],
    },
    'fotografia': {
        'prof': 'Fotógrafo', 'profs': 'Fotógrafos', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Sessão', 'agendar_cta': 'Agendar sessão',
        'servicos': [
            ('Ensaio fotográfico', 90, 350.0), ('Sessão em estúdio', 60, 250.0),
        ],
    },
    'tatuagem': {
        'prof': 'Tatuador', 'profs': 'Tatuadores', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Serviço', 'agendar_cta': 'Agendar horário',
        'servicos': [
            ('Orçamento / avaliação', 30, 0.0), ('Sessão de tatuagem', 120, 300.0), ('Piercing', 30, 80.0),
        ],
    },
    'lavacao': {
        'prof': 'Atendente', 'profs': 'Atendentes', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Serviço', 'agendar_cta': 'Agendar serviço',
        'servicos': [
            ('Lavagem simples', 40, 40.0), ('Lavagem completa', 60, 70.0),
            ('Polimento', 120, 250.0), ('Higienização interna', 90, 180.0), ('Enceramento', 40, 60.0),
        ],
    },
    'escola': {
        'prof': 'Professor', 'profs': 'Professores', 'cliente': 'Aluno', 'clientes': 'Alunos',
        'servico': 'Aula', 'agendar_cta': 'Agendar aula',
        'servicos': [
            ('Aula experimental', 60, 0.0), ('Entrevista / matrícula', 30, 0.0), ('Aula particular', 60, 80.0),
        ],
    },
    'imobiliaria': {
        'prof': 'Corretor', 'profs': 'Corretores', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Atendimento', 'agendar_cta': 'Agendar visita',
        'servicos': [
            ('Visita a imóvel', 60, 0.0), ('Reunião', 45, 0.0), ('Avaliação de imóvel', 60, 0.0),
        ],
    },
    'outros': {
        'prof': 'Profissional', 'profs': 'Profissionais', 'cliente': 'Cliente', 'clientes': 'Clientes',
        'servico': 'Serviço', 'agendar_cta': 'Agendar horário',
        'servicos': [
            ('Atendimento', 60, 0.0),
        ],
    },
}

def agenda_seg(business_type):
    """Retorna a config do ramo (terminologia + serviços-modelo), com fallback."""
    return AGENDA_SEGMENTS.get(business_type or 'outros', AGENDA_SEGMENTS['outros'])

def agenda_seg_da_sessao():
    """Config do ramo do negócio logado (via session). Usa fallback se não achar."""
    try:
        biz_id = session.get('agenda_business_id')
        if not biz_id:
            return AGENDA_SEGMENTS['outros']
        _c = get_saas_db()
        _row = _c.execute('SELECT business_type FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone()
        _c.close()
        return agenda_seg(_row['business_type'] if _row else None)
    except Exception:
        return AGENDA_SEGMENTS['outros']

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


def _sz_backup_email():
    """Backup diário do saas.db INTEIRO (SlotZap: rifas, números, vendedores, comissões —
    + todos os outros SaaS) por e-mail. Snapshot consistente via API de backup do SQLite
    (seguro com WAL). É a recuperação de desastre off-volume da operação de dinheiro."""
    import zipfile, sqlite3 as _sq3, tempfile
    dest = os.environ.get('BACKUP_EMAIL', 'diogolessmann@gmail.com')
    if not dest:
        log.warning('[Backup] saas.db: sem email de destino')
        return False
    tmp = os.path.join(tempfile.gettempdir(), 'saas_snapshot.db')
    try:
        from saas_db import DB_PATH as _SAAS_DB
        src = _sq3.connect(_SAAS_DB)
        dst = _sq3.connect(tmp)
        with dst:
            src.backup(dst)          # snapshot consistente (mesmo com escrita em andamento)
        src.close(); dst.close()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp, 'saas.db')
        buf.seek(0)
        fname = f'saas_backup_{date.today()}.zip'
        ok = _enviar_email(
            para=dest,
            assunto=f'🗄️ Backup SlotZap/SaaS — {date.today()}',
            html=(f'<p>Backup automático do <code>saas.db</code> (SlotZap + SaaS) em '
                  f'<strong>{datetime.now().strftime("%d/%m/%Y %H:%M")}</strong>.</p>'
                  f'<p>Guarde este arquivo — é a recuperação completa em caso de desastre.</p>'),
            anexo_nome=fname, anexo_bytes=buf.read())
        log.info(f'[Backup] saas.db email {"enviado" if ok else "FALHOU"} → {dest}')
        return ok
    except Exception as e:
        log.error(f'[Backup] Erro backup saas.db: {e}')
        return False
    finally:
        try: os.remove(tmp)
        except Exception: pass


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
        _sz_backup_email()   # backup do saas.db (SlotZap + SaaS) — DR da operação de dinheiro


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
            'SELECT plan, active, trial_ends, plan_active, email, phone FROM mandazap_users WHERE id=?', (uid,)
        ).fetchone()
        conn.close()
        if not user or not user['active']:
            for k in ('mz_user_id', 'mz_user_name', 'mz_plan'):
                session.pop(k, None)
            return redirect('/mandazap/entrar?msg=conta_inativa')
        # Paywall (A5): bloqueia trial VENCIDO sem plano ativo. Antes só pegava plan=='solo'
        # com 0 envios → bastava mandar 1 msg p/ furar. Exceções: plano pago ativo ou whitelist.
        trial_ends = user['trial_ends']
        expired    = bool(trial_ends and trial_ends < datetime.now().isoformat())
        _wl_ok     = _is_whitelisted(user['email'], _re.sub(r'\D', '', user['phone'] or ''))
        if expired and not user['plan_active'] and not _wl_ok:
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


# daily_safe_cap = teto CONSERVADOR de msgs/dia POR número (anti-ban, "número não morre").
# É o limite que de fato vale no dia a dia — bem abaixo da zona de risco. Planos maiores
# ganham mais NÚMEROS (mais volume total distribuído), não mais msgs por número.
# Mensal aprox. (22 dias úteis): 45→~990 · 65→~1.430 · 85→~1.870 por número.
# monthly_safe = capacidade mensal segura aprox. (daily_safe_cap × números × ~22 dias úteis),
# arredondada — é o que a landing mostra ("até X msgs/mês"). Vende segurança, não volume bruto.
MANDAZAP_PLANS = {
    'solo':      {'label': 'Solo',      'numbers': 1,  'daily_limit': 399,   'daily_safe_cap': 45, 'monthly_safe': '1.000',  'contacts_limit': 500,   'price': 79},
    'duplo':     {'label': 'Duplo',     'numbers': 2,  'daily_limit': 799,   'daily_safe_cap': 65, 'monthly_safe': '2.800',  'contacts_limit': 2000,  'price': 149},
    'trio':      {'label': 'Trio',      'numbers': 3,  'daily_limit': 1199,  'daily_safe_cap': 85, 'monthly_safe': '5.500',  'contacts_limit': 5000,  'price': 397},
    'quadruplo': {'label': 'Quádruplo', 'numbers': 4,  'daily_limit': 1599,  'daily_safe_cap': 85, 'monthly_safe': '7.400',  'contacts_limit': 10000, 'price': 597},
    'agencia':   {'label': 'Agência',   'numbers': 10, 'daily_limit': 99999, 'daily_safe_cap': 85, 'monthly_safe': '18.000', 'contacts_limit': 99999, 'price': 1097},
}

# Planos do SlotZap (venda de slots numerados com PIX)
SLOTZAP_PLANS = {
    'start': {'label': 'Start', 'price': 69,
              'desc': 'Campanhas e números ilimitados, link público, baixa automática e notificação no grupo do WhatsApp.'},
    'pro':   {'label': 'Pro',   'price': 137,
              'desc': 'Tudo do Start + suporte prioritário e (em breve) taxa por venda zerada.'},
}

# Desconto de combo: quem já assina um produto ativo paga 25% menos no outro
COMBO_DESCONTO = 0.25

# Taxa da plataforma por venda (Asaas Split): você retém 10%, cliente recebe o resto
SZ_TAXA_VENDA = 0.10

def _combo_desconto_ativo(email, produto_atual) -> bool:
    """True se o e-mail já tem assinatura ATIVA do outro produto (MandaZap <-> SlotZap)."""
    if not email:
        return False
    email = email.strip().lower()
    conn = get_saas_db()
    try:
        if produto_atual == 'slotzap':
            r = conn.execute("SELECT 1 FROM mandazap_users WHERE lower(email)=? AND plan_active=1 LIMIT 1",
                             (email,)).fetchone()
        else:
            r = conn.execute("SELECT 1 FROM slotzap_users WHERE lower(email)=? AND plan_active=1 LIMIT 1",
                             (email,)).fetchone()
    except Exception:
        r = None
    conn.close()
    return bool(r)

# ── MandaJá — Planos ─────────────────────────────────────────────────────────
MANDAJA_PLANS = {
    'jr':       {'label': 'MandaJr',  'products': 10,  'price': 29,  'emoji': '🍔'},
    'micro':    {'label': 'Micro',    'products': 15,  'price': 59,  'emoji': '🌱'},
    'light':    {'label': 'Light',    'products': 20,  'price': 99,  'emoji': '⚡'},
    'plus':     {'label': 'Plus',     'products': 25,  'price': 159, 'emoji': '🚀'},
    'pro':      {'label': 'Pro',      'products': 30,  'price': 249, 'emoji': '💎'},
    'king':     {'label': 'King',     'products': 50,  'price': 349, 'emoji': '👑'},
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


def _mandaja_bloqueado(store):
    """True se a loja deve ser travada: trial vencido E não pagou.
    Lojas Pro têm plan_active=1 por padrão → nunca travam por aqui."""
    if not store or store.get('plan_active'):
        return False
    te = store.get('trial_ends') or ''
    return bool(te and te < datetime.now().isoformat())


def _mja_tpl(store, name):
    """Escolhe o template Jr (estilo app, simples) ou Pro pra mesma tela."""
    if store and store.get('mode') == 'jr':
        return f'mandaja/jr_{name}.html'
    return f'mandaja/{name}.html'


# Fotos de produto: guardadas no volume persistente (DATA_DIR), não somem no redeploy
MANDAJA_UPLOAD_DIR = os.path.join(
    os.environ.get('DATA_DIR', os.path.dirname(__file__)), 'uploads', 'mandaja')


@app.route('/uploads/mandaja/<path:filename>')
def mandaja_uploaded_file(filename):
    return send_from_directory(MANDAJA_UPLOAD_DIR, filename)


@app.route('/mandaja/upload-foto', methods=['POST'])
@_mandaja_login_required
def mandaja_upload_foto():
    """Recebe a foto do celular e comprime forte (6MB → ~40KB WebP)."""
    f = request.files.get('foto')
    if not f or not f.filename:
        return jsonify({'error': 'Nenhuma imagem enviada.'}), 400
    try:
        from PIL import Image, ImageOps
        import io, secrets as _sec
        img = Image.open(f.stream)
        img = ImageOps.exif_transpose(img)   # corrige foto de celular girada
        img = img.convert('RGB')
        maxd = 720
        w, h = img.size
        if max(w, h) > maxd:
            if w >= h:
                img = img.resize((maxd, round(h * maxd / w)), Image.LANCZOS)
            else:
                img = img.resize((round(w * maxd / h), maxd), Image.LANCZOS)
        os.makedirs(MANDAJA_UPLOAD_DIR, exist_ok=True)
        name = f"{session['mja_store_id']}_{_sec.token_urlsafe(6)}.webp"
        img.save(os.path.join(MANDAJA_UPLOAD_DIR, name), 'WEBP', quality=72, method=6)
        return jsonify({'ok': True, 'url': f'/uploads/mandaja/{name}'})
    except Exception as e:
        log.warning(f'[MandaJá] upload foto error: {e}')
        return jsonify({'error': 'Não consegui processar essa imagem. Tente outra foto.'}), 400


def _mja_preco(v):
    """Parser de preço à prova de vírgula (BR): '35,90' e '1.234,56' viram float."""
    v = (v or '').strip()
    if ',' in v:
        v = v.replace('.', '').replace(',', '.')
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return 0.0


def _mandaja_loja_aberta(store, conn):
    """True se a loja está recebendo pedidos agora.
    Jr: interruptor manual (aberto). Pro: horário do dia."""
    if store.get('mode') == 'jr':
        return bool(store.get('aberto', 1))
    now = datetime.now()
    hour_row = conn.execute(
        'SELECT * FROM mandaja_hours WHERE store_id=? AND weekday=? AND active=1',
        (store['id'], now.weekday())).fetchone()
    if not hour_row:
        return False
    try:
        open_dt  = datetime.strptime(hour_row['open_time'],  '%H:%M').replace(year=now.year, month=now.month, day=now.day)
        close_dt = datetime.strptime(hour_row['close_time'], '%H:%M').replace(year=now.year, month=now.month, day=now.day)
        return open_dt <= now <= close_dt
    except Exception:
        return False


def _pix_brcode(chave, nome, cidade='', valor=0.0, txid='***'):
    """Gera o PIX 'copia e cola' (BR Code EMV) com o valor já preenchido.
    PIX direto: dinheiro cai na chave do próprio dono, sem intermediário.
    Retorna a string copia-e-cola, ou '' se faltar chave."""
    chave = (chave or '').strip()
    if not chave:
        return ''

    def _emv(_id, _val):
        _val = str(_val)
        return f"{_id}{len(_val):02d}{_val}"

    # Sanitiza nome/cidade: ASCII, sem acento, maiúsculas (padrão do BR Code)
    def _ascii(s, limite):
        import unicodedata as _ud
        s = _ud.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode('ascii')
        return s.upper()[:limite].strip() or 'N'

    nome   = _ascii(nome, 25)
    cidade = _ascii(cidade or 'BRASIL', 15)
    txid   = (''.join(c for c in str(txid) if c.isalnum()) or '***')[:25]

    # Merchant Account Information (GUI br.gov.bcb.pix + chave)
    mai = _emv('00', 'br.gov.bcb.pix') + _emv('01', chave)
    payload = (
        _emv('00', '01')                # Payload Format Indicator
        + _emv('26', mai)               # Merchant Account Information
        + _emv('52', '0000')            # Merchant Category Code
        + _emv('53', '986')             # Moeda: BRL
    )
    if valor and float(valor) > 0:
        payload += _emv('54', f"{float(valor):.2f}")   # Valor da transação
    payload += (
        _emv('58', 'BR')                # País
        + _emv('59', nome)              # Nome do recebedor
        + _emv('60', cidade)            # Cidade
        + _emv('62', _emv('05', txid))  # Additional Data (txid)
    )
    payload += '6304'                   # CRC16: id+len; valor calculado abaixo

    # CRC16-CCITT (0xFFFF), polinômio 0x1021
    crc = 0xFFFF
    for ch in payload.encode('utf-8'):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return payload + f"{crc:04X}"

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
        'label': 'SalaTV Mensal', 'price': 'R$ 49,90/mês',
        'preco': 49.90, 'cycle': 'MONTHLY',
        'features': ['6 categorias de conteúdo', '1 código de acesso', 'Atualização automática de conteúdo', 'Suporte via WhatsApp'],
    },
    'anual': {
        'label': 'SalaTV Anual', 'price': 'R$ 39,90/mês (R$ 478,80/ano)',
        'preco': 478.80, 'cycle': 'YEARLY',
        'features': ['Tudo do Mensal', '20% de desconto', '2 códigos de acesso'],
    },
}


def _get_slots(business_id, date_str, service_duration, professional_id=None):
    """Gera horários disponíveis para uma data e duração de serviço.

    Se professional_id for informado e o profissional tiver horário próprio
    configurado, usa o horário dele (e checa conflitos só com os agendamentos
    dele). Caso contrário, usa o horário geral do negócio (linhas com
    professional_id NULL) e checa conflitos com todos os agendamentos."""
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        weekday = dt.weekday()
    except Exception:
        return []

    conn = get_saas_db()

    avail = None
    if professional_id:
        tem_proprio = conn.execute(
            'SELECT 1 FROM agenda_availability WHERE business_id=? AND professional_id=? AND active=1 LIMIT 1',
            (business_id, professional_id)
        ).fetchone()
        if tem_proprio:
            avail = conn.execute(
                'SELECT start_time, end_time FROM agenda_availability '
                'WHERE business_id=? AND professional_id=? AND weekday=? AND active=1',
                (business_id, professional_id, weekday)
            ).fetchone()
            if not avail:
                conn.close()
                return []
    if avail is None:
        avail = conn.execute(
            'SELECT start_time, end_time FROM agenda_availability '
            'WHERE business_id=? AND professional_id IS NULL AND weekday=? AND active=1',
            (business_id, weekday)
        ).fetchone()
    if not avail:
        conn.close()
        return []

    if professional_id:
        booked = conn.execute('''
            SELECT a.appointment_time, COALESCE(s.duration_minutes, 60) as duration_minutes
            FROM agenda_appointments a
            LEFT JOIN agenda_services s ON a.service_id = s.id
            WHERE a.business_id=? AND a.appointment_date=? AND a.status != 'cancelled'
              AND a.professional_id=?
        ''', (business_id, date_str, professional_id)).fetchall()
    else:
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
                    _ref_af = (session.get('kids_ref') or request.args.get('ref') or '').strip().upper()[:12]
                    kconn.execute('''INSERT INTO clients
                        (code, name, email, phone, cpf_cnpj, plan, plan_active, active, created_at, city, afiliado_ref)
                        VALUES (?,?,?,?,?,?,0,0,?,?,?)''',
                        (code, empresa, email, phone, cpf_cnpj, plano, now, 'SC', (_ref_af or None)))
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
                        # Cupom de desconto (opcional)
                        import kids_db as _kdb
                        cupom_raw = request.form.get('cupom', '').strip()
                        valor_final = p['preco']
                        cupom_obj = _kdb.validar_cupom(cupom_raw) if cupom_raw else None
                        if cupom_obj:
                            valor_final = round(p['preco'] * (1 - cupom_obj['desconto_pct'] / 100.0), 2)
                        descricao = f"SalaTV {p['label']} — {empresa}"
                        if cupom_obj:
                            descricao += f" (cupom {cupom_obj['codigo']} -{cupom_obj['desconto_pct']}%)"
                        resp = _asaas_criar_assinatura_saas(
                            customer_id, 'kids', plano, valor_final,
                            descricao,
                            billing_type, p.get('cycle', 'MONTHLY')
                        )
                        if resp.get('id'):
                            if cupom_obj:
                                try: _kdb.registrar_uso_cupom(cupom_obj['codigo'])
                                except Exception: pass
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

AGENDA_PLAN = {'label': 'AgendaJá Pro', 'preco': 49.90, 'price': 'R$ 49,90/mês'}

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
    # Página oficial única do produto = /despachante-info (marketing completo).
    # A área do cliente continua em /amigo-despachante/entrar.
    return redirect('/despachante-info')


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
    _ref = (request.args.get('ref') or '').strip().upper()[:12]
    if _ref:
        session['defesa_ref'] = _ref   # afiliado que trouxe (programa de afiliados)
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
                _ref_af = (session.get('defesa_ref') or request.args.get('ref') or '').strip().upper()[:12]
                conn.execute(
                    '''INSERT INTO defesapro_users
                       (name, email, phone, cpf_cnpj, escritorio, cidade, plan,
                        active, password_hash, created_at, afiliado_ref)
                       VALUES (?,?,?,?,?,?,?,0,?,?,?)''',
                    (name, email, phone, cpf_cnpj, escritorio, cidade, plan,
                     generate_password_hash(password), now, (_ref_af or None))
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
    # Validação do token (tolerante a espaços/quebras de linha e aspas acidentais
    # no valor da env — causa comum de 401 ao colar o token no Railway)
    token = os.environ.get('ASAAS_WEBHOOK_TOKEN', '').strip().strip('"').strip("'")
    recebido = (request.headers.get('asaas-access-token') or '').strip().strip('"').strip("'")
    if (not token) or recebido != token:
        log.warning('[Webhook Asaas] 401 — token ausente/incorreto (len env=%d, len recebido=%d). '
                    'Configure ASAAS_WEBHOOK_TOKEN no ambiente.', len(token), len(recebido))
        return jsonify({'error': 'unauthorized'}), 401
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({'error': 'invalid json'}), 400

    event = payload.get('event', '')
    ref = payload.get('payment', {}).get('externalReference', '') or \
          payload.get('subscription', {}).get('externalReference', '')

    ativar = event in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED', 'SUBSCRIPTION_ACTIVATED')
    # Corta o acesso em qualquer sinal de não-pagamento/cancelamento/estorno.
    # (Asaas usa SUBSCRIPTION_DELETED/INACTIVATED p/ cancelamento; antes só pegava CANCELLED.)
    desativar = event in ('SUBSCRIPTION_CANCELLED', 'SUBSCRIPTION_DELETED', 'SUBSCRIPTION_INACTIVATED',
                          'PAYMENT_OVERDUE', 'PAYMENT_DELETED', 'PAYMENT_REFUNDED')

    if not (ativar or desativar):
        return jsonify({'status': 'ignored'}), 200

    # Roteamento por prefixo
    parts       = ref.split('_')
    customer_id = parts[1] if len(parts) > 1 else None
    plano_key   = parts[2] if len(parts) > 2 else ''

    if ref.startswith('agendawpp_'):
        # Add-on WhatsApp Automático do AgendaJá (R$ 39,90/mês)
        if customer_id:
            conn = get_saas_db()
            b = conn.execute('SELECT id, name, email, owner_name FROM agenda_businesses WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if b:
                conn.execute('UPDATE agenda_businesses SET wpp_addon=? WHERE id=?',
                             (1 if ativar else 0, b['id']))
                conn.commit()
                if ativar and b['email']:
                    _enviar_email(b['email'], '✅ AgendaJá — WhatsApp Automático ativo!',
                        _email_pagamento_confirmado('AgendaJá', '📲', '#22c55e',
                            b['owner_name'].split()[0], 'WhatsApp Automático (add-on)',
                            'R$ 39,90/mês', 'https://4kitem.com.br/agenda/painel'))
            conn.close()

    elif ref.startswith('defesapro_'):
        if customer_id:
            conn = get_saas_db()
            u = conn.execute('SELECT id, name, email, cpf_cnpj, afiliado_ref FROM defesapro_users WHERE asaas_customer_id=?',
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
                # Comissão de afiliado — só em ativação (PAYMENT_CONFIRMED/RECEIVED),
                # recorrente, com anti-autoindicação central no registrar_comissao.
                if ativar and u['afiliado_ref']:
                    try:
                        from afiliados import registrar_comissao
                        registrar_comissao(u['afiliado_ref'], 'defesapro',
                                           (payload.get('payment') or {}).get('id', ''),
                                           u['name'], cliente_email=u['email'],
                                           cliente_cpf=u['cpf_cnpj'])
                    except Exception as _eaf:
                        log.warning(f'[Afiliados] defesapro: {_eaf}')
            conn.close()

    elif ref.startswith('agenda_'):
        if customer_id:
            conn = get_saas_db()
            b = conn.execute('SELECT id, name, email, owner_name, cpf_cnpj, afiliado_ref FROM agenda_businesses WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if b:
                conn.execute('UPDATE agenda_businesses SET active=?, plan_active=? WHERE id=?',
                             (1 if ativar else 0, 1 if ativar else 0, b['id']))
                conn.commit()
                if ativar and b['email']:
                    p = AGENDA_PLAN
                    _enviar_email(b['email'], '✅ AgendaJá — Assinatura ativa!',
                        _email_pagamento_confirmado('AgendaJá', '📅', '#22c55e',
                            b['owner_name'].split()[0], p['label'],
                            p['price'], 'https://4kitem.com.br/agenda/painel'))
                if ativar and b['afiliado_ref']:
                    try:
                        from afiliados import registrar_comissao
                        registrar_comissao(b['afiliado_ref'], 'agenda',
                                           (payload.get('payment') or {}).get('id', ''),
                                           b['owner_name'], cliente_email=b['email'],
                                           cliente_cpf=b['cpf_cnpj'])
                    except Exception as _eaf:
                        log.warning(f'[Afiliados] agenda: {_eaf}')
            conn.close()

    elif ref.startswith('mandaja_'):
        if customer_id:
            conn = get_saas_db()
            s = conn.execute('SELECT id, name, email, owner_name, plan, mode, cpf_cnpj, afiliado_ref FROM mandaja_stores WHERE asaas_customer_id=?',
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
                if ativar and s['afiliado_ref']:
                    try:
                        from afiliados import registrar_comissao
                        _appkey = 'mandajr' if (s['mode'] == 'jr') else 'mandaja'
                        registrar_comissao(s['afiliado_ref'], _appkey,
                                           (payload.get('payment') or {}).get('id', ''),
                                           s['owner_name'], cliente_email=s['email'], cliente_cpf=s['cpf_cnpj'])
                    except Exception as _eaf:
                        log.warning(f'[Afiliados] mandaja: {_eaf}')
            conn.close()

    elif ref.startswith('mandazap_'):
        if customer_id:
            conn = get_saas_db()
            u = conn.execute('SELECT id, name, email, cpf_cnpj, afiliado_ref FROM mandazap_users WHERE asaas_customer_id=?',
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
                # Comissão de afiliado (recorrente, anti-autoindicação central)
                if ativar and u['afiliado_ref']:
                    try:
                        from afiliados import registrar_comissao
                        registrar_comissao(u['afiliado_ref'], 'mandazap',
                                           (payload.get('payment') or {}).get('id', ''),
                                           u['name'], cliente_email=u['email'],
                                           cliente_cpf=u['cpf_cnpj'])
                    except Exception as _eaf:
                        log.warning(f'[Afiliados] mandazap: {_eaf}')
            conn.close()

    elif ref.startswith('despachante_'):
        if customer_id:
            conn = get_saas_db()
            u = conn.execute('SELECT id, name, email, afiliado_ref FROM despachante_users WHERE asaas_customer_id=?',
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
                if ativar and u['afiliado_ref']:
                    try:
                        from afiliados import registrar_comissao
                        registrar_comissao(u['afiliado_ref'], 'despachante',
                                           (payload.get('payment') or {}).get('id', ''),
                                           u['name'], cliente_email=u['email'])
                    except Exception as _eaf:
                        log.warning(f'[Afiliados] despachante: {_eaf}')
            conn.close()

    elif ref.startswith('slotzap_'):
        # SlotZap — assinatura mensal (externalReference = slotzap_{customer_id}_{plano})
        if customer_id:
            conn = get_saas_db()
            u = conn.execute('SELECT id, name, email FROM slotzap_users WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if u:
                conn.execute('UPDATE slotzap_users SET active=?, plan_active=? WHERE id=?',
                             (1 if ativar else 0, 1 if ativar else 0, u['id']))
                conn.commit()
                if ativar and u['email']:
                    p = SLOTZAP_PLANS.get(plano_key, {})
                    _enviar_email(u['email'], '✅ SlotZap — Assinatura ativa!',
                        _email_pagamento_confirmado('SlotZap', '🎯', '#6366f1',
                            u['name'].split()[0], p.get('label', plano_key),
                            f"R$ {p.get('price','')}/mês", 'https://4kitem.com.br/slotzap/app'))
            conn.close()

    elif ref.startswith('sz_'):
        # SlotZap — pagamento de número(s). Processa em SEGUNDO PLANO para responder
        # rápido ao Asaas (envios de WhatsApp são lentos e causavam timeout/pausa do webhook).
        if ativar:
            cid = payload.get('payment', {}).get('id', '')
            try:    slot_fb = int(ref.split('_')[1])
            except (IndexError, ValueError): slot_fb = 0
            def _sz_baixa_bg(cid=cid, slot_fb=slot_fb):
                try:
                    if not (cid and _sz_marcar_pago_charge(cid)):
                        if slot_fb: _sz_marcar_pago(slot_fb)
                except Exception as _e:
                    log.error(f'[SlotZap] Webhook bg error: {_e}')
            threading.Thread(target=_sz_baixa_bg, daemon=True).start()

    elif ref.startswith('drzap_'):
        # DRZAP — compra de créditos paga: credita (idempotente/atômico)
        if ativar:
            try:
                from drzap import drz_webhook_confirmar
                drz_webhook_confirmar(ref, payload.get('payment', {}).get('id', ''))
            except Exception as _drz_e:
                log.error(f'[DRZAP] Webhook error: {_drz_e}')

    elif ref.startswith('pcd_'):
        # PCD Fácil — compra de créditos paga: credita (idempotente/atômico)
        if ativar:
            try:
                from pcd import pcd_webhook_confirmar
                pcd_webhook_confirmar(ref, payload.get('payment', {}).get('id', ''))
            except Exception as _pcd_e:
                log.error(f'[PCD] Webhook error: {_pcd_e}')

    elif ref.startswith('amparo_'):
        # Amparo — assinatura do psicólogo: paga=ativa o plano, vence/cancela=suspende
        if customer_id:
            try:
                from amparo import amparo_webhook_assinatura
                amparo_webhook_assinatura(customer_id, plano_key, ativar,
                                          payload.get('payment', {}).get('id', ''),
                                          payload.get('payment', {}).get('value', 0))
            except Exception as _amp_e:
                log.error(f'[Amparo] Webhook error: {_amp_e}')

    elif ref.startswith('somaja_'):
        # SomaJá — assinatura mensal/anual: paga=ativa, vence/cancela=corta
        if customer_id:
            try:
                from somaja import soma_webhook_ativar
                _soma_plano = ref.split('_', 2)[2] if ref.count('_') >= 2 else None
                soma_webhook_ativar(customer_id, _soma_plano, ativar,
                                    payload.get('payment', {}).get('id', ''))
            except Exception as _soma_e:
                log.error(f'[SomaJá] Webhook error: {_soma_e}')

    elif ref.startswith('atendezap_'):
        # AtendeZap — assinatura do negócio: paga=ativa o bot, vence/cancela=corta.
        # ref = 'atendezap_<biz_id>_<plano>' (plano sem '_': anual|mensal) → customer_id=parts[1]=biz_id
        if customer_id:
            try:
                from atendezap import atende_webhook_ativar
                atende_webhook_ativar(customer_id, ativar,
                                      (payload.get('payment') or {}).get('id', ''))
            except Exception as _ate:
                log.error(f'[AtendeZap] Webhook error: {_ate}')

    elif ref.startswith('radar_') or ref.startswith('licita_'):
        # Radar TI / Radar Licita Norte — assinatura mensal: paga=ativa, vence/cancela=corta
        if customer_id:
            _tab = 'radar_users' if ref.startswith('radar_') else 'licita_users'
            try:
                from radar_db import get_radar_db
                conn = get_radar_db()
                u = conn.execute(f'SELECT id, nome, email, afiliado_ref FROM {_tab} WHERE asaas_customer_id=?',
                                 (customer_id,)).fetchone()
                if u:
                    conn.execute(f'UPDATE {_tab} SET plan_active=? WHERE id=?',
                                 (1 if ativar else 0, u['id']))
                    conn.commit()
                    if ativar and u['email']:
                        _nome = 'Radar de Licitações de TI' if _tab == 'radar_users' else 'Radar Licita Norte'
                        _url = ('https://4kitem.com.br/radar/' if _tab == 'radar_users'
                                else 'https://4kitem.com.br/licita-norte/')
                        try:
                            _enviar_email(u['email'], f'✅ {_nome} — Assinatura ativa!',
                                _email_pagamento_confirmado(_nome, '📡', '#2563eb',
                                    (u['nome'] or '').split()[0], 'Mensal', '', _url))
                        except Exception: pass
                    if ativar and u['afiliado_ref']:
                        try:
                            from afiliados import registrar_comissao
                            _appkey = 'radar' if _tab == 'radar_users' else 'licita_norte'
                            registrar_comissao(u['afiliado_ref'], _appkey,
                                               (payload.get('payment') or {}).get('id', ''),
                                               (u['nome'] or ''), cliente_email=u['email'])
                        except Exception as _eaf:
                            log.warning(f'[Afiliados] radar/licita: {_eaf}')
                conn.close()
            except Exception as _rd_e:
                log.error(f'[RADAR/LICITA] Webhook error: {_rd_e}')

    elif ref.startswith('radarcred_') or ref.startswith('licitacred_'):
        # Radar/Licita — compra de créditos de análise paga: credita (idempotente/atômico)
        if ativar:
            try:
                from radar_db import confirmar_compra
                confirmar_compra(int(ref.split('_')[1]), payload.get('payment', {}).get('id', ''))
            except Exception as _cc_e:
                log.error(f'[RADAR/LICITA cred] Webhook error: {_cc_e}')

    elif ref.startswith('alerta_'):
        if customer_id:
            conn = get_saas_db()
            s = conn.execute('SELECT id, name, email, plano, cpf, afiliado_ref FROM alerta_subscribers WHERE asaas_customer_id=?',
                             (customer_id,)).fetchone()
            if s:
                novo_status = 'ativo' if ativar else 'suspenso'
                conn.execute("UPDATE alerta_subscribers SET status=?, payment_status=? WHERE id=?",
                             (novo_status, 'paid' if ativar else 'overdue', s['id']))
                conn.commit()
                if ativar and s['email']:
                    p = ALERTA_PLANS.get(plano_key or s['plano'], {})
                    _enviar_email(s['email'], '✅ AlertaSC — Monitoramento ativado!',
                        _email_pagamento_confirmado('AlertaSC', '🚨', '#ef4444',
                            s['name'].split()[0], p.get('label', plano_key or s['plano']),
                            p.get('price', ''), 'https://4kitem.com.br/alerta/minha-conta'))
                if ativar and s['afiliado_ref']:
                    try:
                        from afiliados import registrar_comissao
                        registrar_comissao(s['afiliado_ref'], 'alerta',
                                           (payload.get('payment') or {}).get('id', ''),
                                           s['name'], cliente_email=s['email'], cliente_cpf=s['cpf'])
                    except Exception as _eaf:
                        log.warning(f'[Afiliados] alerta: {_eaf}')
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
                    if ativar and c['email']:
                        p = KIDS_PLANS.get(plano_key or (c['plan'] or 'mensal'), KIDS_PLANS['mensal'])
                        _enviar_email(c['email'], '✅ SalaTV — Acesso liberado!',
                            _email_pagamento_confirmado('SalaTV', '📺', '#3b82f6',
                                c['name'].split()[0], p['label'],
                                p['price'],
                                f'https://4kitem.com.br/painel/{c["code"]}') +
                            f'<div style="font-family:sans-serif;max-width:480px;margin:auto;padding:0 32px 24px">'
                            f'<p style="background:#1e3a5f;border-radius:10px;padding:16px;color:#93c5fd;font-size:15px">'
                            f'🔑 Seu código de acesso: <strong style="font-size:20px;color:#60a5fa">{c["code"]}</strong><br>'
                            f'<small>Use em: 4kitem.com.br/kids/entrar</small></p></div>')
                    if ativar and c['afiliado_ref']:
                        try:
                            from afiliados import registrar_comissao
                            registrar_comissao(c['afiliado_ref'], 'salatv',
                                               (payload.get('payment') or {}).get('id', ''),
                                               c['name'], cliente_email=c['email'], cliente_cpf=c['cpf_cnpj'])
                        except Exception as _eaf:
                            log.warning(f'[Afiliados] salatv: {_eaf}')
                kconn.close()
            except Exception:
                log.exception('[Webhook] Erro ao ativar SalaTV')

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
    _ref = (request.args.get('ref') or '').strip().upper()[:12]
    if _ref:
        session['kids_ref'] = _ref   # afiliado que trouxe (programa de afiliados)
    return render_template('kids/landing.html', stats=stats())

@app.route('/sala')
def sala():
    return render_template('sala/landing.html', stats=stats())

@app.route('/agenda')
def agenda():
    _ref = (request.args.get('ref') or '').strip().upper()[:12]
    if _ref:
        session['agenda_ref'] = _ref   # afiliado que trouxe (programa de afiliados)
    return render_template('agenda/landing.html')

@app.route('/alerta')
def alerta():
    _ref = (request.args.get('ref') or '').strip().upper()[:12]
    if _ref:
        session['alerta_ref'] = _ref   # afiliado que trouxe (programa de afiliados)
    return render_template('alerta/landing.html', plans=ALERTA_PLANS)


# ══════════════════════════════════════════════════════════════════════════
#  AGENDA SC — SaaS de Agendamento Online
# ══════════════════════════════════════════════════════════════════════════

def _agenda_send_whatsapp(phone: str, message: str, instance: str) -> bool:
    """Envia mensagem WhatsApp via Evolution API para o AgendaJá."""
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
                    _ref_af = (session.get('agenda_ref') or request.args.get('ref') or '').strip().upper()[:12]
                    conn.execute('''
                        INSERT INTO agenda_businesses
                        (name, slug, owner_name, phone, email, business_type, password_hash, cpf_cnpj, active, created_at, trial_ends, afiliado_ref)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ''', (name, slug, owner_name, phone, email, business_type,
                          generate_password_hash(password), cpf_cnpj, datetime.now().isoformat(), trial_ends, (_ref_af or None)))
                    conn.commit()
                    biz = conn.execute('SELECT * FROM agenda_businesses WHERE slug=?', (slug,)).fetchone()
                    # Semeia serviços-modelo de acordo com o ramo escolhido
                    try:
                        _now_iso = datetime.now().isoformat()
                        for _sn, _sd, _sp in agenda_seg(business_type)['servicos']:
                            conn.execute(
                                'INSERT INTO agenda_services (business_id, name, duration_minutes, price, active, created_at) '
                                'VALUES (?, ?, ?, ?, 1, ?)',
                                (biz['id'], _sn, _sd, _sp, _now_iso)
                            )
                        conn.commit()
                    except Exception as _seed_err:
                        log.error(f'Agenda seed servicos error: {_seed_err}')
                    conn.close()
                    session['agenda_business_id']   = biz['id']
                    session['agenda_business_slug'] = biz['slug']
                    session['agenda_business_name'] = biz['name']
                    # Email de boas-vindas
                    if email:
                        _enviar_email(
                            email,
                            '📅 Bem-vindo ao AgendaJá — Seu trial de 7 dias começou!',
                            _email_boas_vindas(
                                'AgendaJá', '📅', '#22c55e',
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
                  <h2 style="color:#27ae60">Recuperação de senha — AgendaJá</h2>
                  <p>Olá, <strong>{biz['owner_name'].split()[0]}</strong>!</p>
                  <p>Seu código de recuperação é:</p>
                  <div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#27ae60;
                              background:#f0fdf4;padding:20px;border-radius:12px;text-align:center;
                              margin:20px 0">{codigo}</div>
                  <p style="color:#666;font-size:13px">Válido por 2 horas.</p>
                </div>"""
                ok = _enviar_email(biz['email'], 'Código de recuperação — AgendaJá', html_email)
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
        'SELECT * FROM agenda_availability WHERE business_id=? AND professional_id IS NULL ORDER BY weekday', (biz_id,)
    ).fetchall()]
    profissionais = [dict(r) for r in conn.execute(
        'SELECT id, name, color FROM agenda_professionals WHERE business_id=? AND active=1 ORDER BY order_pos, name', (biz_id,)
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
                           seg=agenda_seg(biz.get('business_type')),
                           profissionais=profissionais,
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
    prof_id = data.get('professional_id') or None
    conn   = get_saas_db()
    # Valida que o profissional pertence ao negócio (ou trata como horário geral)
    if prof_id:
        ok = conn.execute('SELECT 1 FROM agenda_professionals WHERE id=? AND business_id=?',
                          (prof_id, biz_id)).fetchone()
        if not ok:
            prof_id = None
    if prof_id:
        conn.execute('DELETE FROM agenda_availability WHERE business_id=? AND professional_id=?',
                     (biz_id, prof_id))
    else:
        conn.execute('DELETE FROM agenda_availability WHERE business_id=? AND professional_id IS NULL',
                     (biz_id,))
    for item in data.get('availability', []):
        wday = item.get('weekday')
        s    = item.get('start_time', '')
        e    = item.get('end_time', '')
        if wday is not None and s and e:
            conn.execute('''
                INSERT INTO agenda_availability (business_id, professional_id, weekday, start_time, end_time, active)
                VALUES (?, ?, ?, ?, ?, 1)
            ''', (biz_id, prof_id, wday, s, e))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/agenda/painel/horario/load')
@_agenda_login_required
def agenda_load_horario():
    """Retorna os horários de um profissional (professional_id) ou do negócio
    (sem professional_id). Usado pelo seletor de profissional na aba Horários."""
    biz_id  = session['agenda_business_id']
    prof_id = request.args.get('professional_id') or None
    conn    = get_saas_db()
    if prof_id:
        rows = conn.execute(
            'SELECT weekday, start_time, end_time FROM agenda_availability '
            'WHERE business_id=? AND professional_id=? AND active=1 ORDER BY weekday',
            (biz_id, prof_id)
        ).fetchall()
        tem_proprio = len(rows) > 0
        # fallback visual: se não tem próprio, mostra o do negócio como base
        if not tem_proprio:
            rows = conn.execute(
                'SELECT weekday, start_time, end_time FROM agenda_availability '
                'WHERE business_id=? AND professional_id IS NULL AND active=1 ORDER BY weekday',
                (biz_id,)
            ).fetchall()
    else:
        tem_proprio = True
        rows = conn.execute(
            'SELECT weekday, start_time, end_time FROM agenda_availability '
            'WHERE business_id=? AND professional_id IS NULL AND active=1 ORDER BY weekday',
            (biz_id,)
        ).fetchall()
    conn.close()
    return jsonify({
        'tem_proprio': tem_proprio,
        'availability': [dict(r) for r in rows]
    })


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
    return render_template('agenda/equipe.html', biz=biz, profissionais=profs,
                           seg=agenda_seg(biz.get('business_type')))


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
    return render_template('agenda/profissional_form.html', prof=None, erro=erro, modo='novo',
                           seg=agenda_seg_da_sessao())


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
    return render_template('agenda/profissional_form.html', prof=prof, erro=erro, modo='editar',
                           seg=agenda_seg_da_sessao())


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
                'AgendaJá Pro — Assinatura Mensal',
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


# ── AgendaJá — Add-on WhatsApp Automático (R$ 39,90/mês) ──────────────────────
AGENDA_WPP_ADDON = {'label': 'AgendaJá — WhatsApp Automático', 'preco': 39.90, 'price': 'R$ 39,90/mês'}


@app.route('/agenda/whatsapp/contratar', methods=['GET', 'POST'])
@_agenda_login_required
def agenda_wpp_contratar():
    biz_id = session['agenda_business_id']
    p = AGENDA_WPP_ADDON
    erro = None
    conn = get_saas_db()
    biz = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone())
    conn.close()
    if biz.get('wpp_addon'):
        return redirect('/agenda/painel')
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX').upper()
        if billing_type not in ('PIX', 'BOLETO', 'CREDIT_CARD'):
            billing_type = 'PIX'
        customer_id = _asaas_criar_ou_buscar_cliente_saas(
            biz['name'], biz['email'], biz['phone'], biz.get('cpf_cnpj', ''), biz['id'], 'agenda_businesses'
        )
        if not customer_id:
            log.error('[AgendaWpp] Falha customer_id biz_id=%s', biz_id)
            erro = ('Não conseguimos processar o pagamento agora. '
                    'Fale no WhatsApp (47) 99960-6998 e ativamos manualmente. 💬')
        else:
            conn2 = get_saas_db()
            conn2.execute('UPDATE agenda_businesses SET asaas_customer_id=? WHERE id=?',
                          (customer_id, biz_id))
            conn2.commit(); conn2.close()
            resp = _asaas_criar_assinatura_saas(
                customer_id, 'agendawpp', 'addon', p['preco'],
                'AgendaJá — WhatsApp Automático (add-on)', billing_type
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
    return render_template('agenda/checkout_wpp.html', plano=p, erro=erro)


@app.route('/agenda/whatsapp/qr')
@_agenda_login_required
def agenda_wpp_qr():
    """Cria/retorna o QR Code da instância Evolution do negócio (add-on ativo)."""
    biz_id = session['agenda_business_id']
    conn = get_saas_db()
    biz = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone())
    conn.close()
    if not biz.get('wpp_addon'):
        return jsonify({'erro': 'Add-on de WhatsApp não contratado.'}), 402
    evo_url = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    if not evo_url or not evo_key:
        return jsonify({'erro': 'Evolution API não configurada.'})
    instance = f'agenda{biz_id}'
    headers = {'apikey': evo_key, 'Content-Type': 'application/json'}
    # Já salva a instância no negócio (os envios usam esse nome)
    conn2 = get_saas_db()
    conn2.execute('UPDATE agenda_businesses SET mandazap_instance=?, mandazap_ativo=1 WHERE id=?',
                  (instance, biz_id))
    conn2.commit(); conn2.close()
    try:
        import requests as _req

        def _return_qr(qr):
            if not qr.startswith('data:'):
                qr = 'data:image/png;base64,' + qr
            return jsonify({'qr': qr, 'instance': instance})

        # 1) tenta QR na instância existente
        try:
            r_conn = _req.get(f'{evo_url}/instance/connect/{instance}', headers=headers, timeout=12)
            qr = _evo_extract_qr(r_conn.json() if r_conn.content else {})
            if qr:
                return _return_qr(qr)
        except Exception:
            pass
        # 2) reset + cria limpa
        _evo_delete_instance(evo_url, instance, headers)
        time.sleep(1.5)
        cr = _req.post(f'{evo_url}/instance/create', headers=headers,
                       json={'instanceName': instance, 'qrcode': True,
                             'integration': 'WHATSAPP-BAILEYS'}, timeout=20)
        qr = _evo_extract_qr(cr.json() if cr.content else {})
        if qr:
            return _return_qr(qr)
        # 3) polling
        for _ in range(3):
            time.sleep(2.5)
            r2 = _req.get(f'{evo_url}/instance/connect/{instance}', headers=headers, timeout=15)
            qr = _evo_extract_qr(r2.json() if r2.content else {})
            if qr:
                return _return_qr(qr)
        return jsonify({'erro': 'QR Code não disponível ainda. Aguarde 5s e tente de novo.'})
    except Exception as e:
        log.error(f'[AgendaWpp QR] {e}')
        return jsonify({'erro': 'Erro ao gerar QR Code.'})


@app.route('/agenda/whatsapp/status')
@_agenda_login_required
def agenda_wpp_status():
    """Verifica se a instância do negócio está conectada ao WhatsApp."""
    biz_id = session['agenda_business_id']
    conn = get_saas_db()
    biz = dict(conn.execute('SELECT * FROM agenda_businesses WHERE id=?', (biz_id,)).fetchone())
    conn.close()
    evo_url = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    instance = biz.get('mandazap_instance') or f'agenda{biz_id}'
    if not evo_url or not evo_key:
        return jsonify({'connected': False})
    try:
        import requests as _req
        r = _req.get(f'{evo_url}/instance/connectionState/{instance}',
                     headers={'apikey': evo_key}, timeout=8)
        d = r.json() if r.content else {}
        state = (d.get('instance', {}).get('state') if isinstance(d.get('instance'), dict)
                 else d.get('state', '')) or ''
        return jsonify({'connected': state == 'open', 'state': state,
                        'addon': bool(biz.get('wpp_addon'))})
    except Exception:
        return jsonify({'connected': False})



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
           f"📲 *MandaZap + AgendaJá* ativado com sucesso.\n"
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
                           professionals=professionals,
                           seg=agenda_seg(biz['business_type']))


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
    professional_id = request.args.get('professional_id') or None
    return jsonify({'slots': _get_slots(biz['id'], date_str, duration, professional_id)})


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

    slots = _get_slots(biz['id'], appt_date, duration, professional_id)
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

    # Anti duplo-agendamento: re-checa logo antes de inserir (fecha a janela de corrida)
    if professional_id:
        _dup = conn.execute(
            "SELECT 1 FROM agenda_appointments WHERE business_id=? AND appointment_date=? "
            "AND appointment_time=? AND professional_id=? AND status!='cancelled' LIMIT 1",
            (biz['id'], appt_date, appt_time, professional_id)
        ).fetchone()
    else:
        _dup = conn.execute(
            "SELECT 1 FROM agenda_appointments WHERE business_id=? AND appointment_date=? "
            "AND appointment_time=? AND status!='cancelled' LIMIT 1",
            (biz['id'], appt_date, appt_time)
        ).fetchone()
    if _dup:
        conn.close()
        return jsonify({'success': False, 'error': 'Esse horário acabou de ser reservado. Escolha outro.'})

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
                (name, cpf, plates_json, phone, email, plano, status, payment_status, created_at, afiliado_ref)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', 'pending', ?, ?)
            ''', (name, cpf, _json.dumps(plates), phone, email, plano, datetime.now().isoformat(),
                  ((session.get('alerta_ref') or request.args.get('ref') or '').strip().upper()[:12] or None)))
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
                # Anti-SQLi: nome de tabela vem do form; exige identificador SQL válido
                # (sem espaços/aspas/;/parênteses) — bloqueia injeção sem quebrar tabelas reais.
                if not _re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', tabela):
                    return render_template('saas_admin_unban.html', resultado=None,
                                           mensagem='❌ Nome de tabela inválido.', busca='')
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

        # SalaTV (kids.db)
        try:
            kconn = get_kids_conn()
            kids_rows = kconn.execute('SELECT id, name, email, created_at FROM clients').fetchall()
            for r in kids_rows:
                r = dict(r)
                if busca_lower in (r.get('email') or '').lower():
                    encontrados.append({
                        'tabela': 'clients (SalaTV)', 'id': r['id'],
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


@app.route('/webhook/efi', methods=['GET', 'POST'])
@app.route('/webhook/efi/pix', methods=['GET', 'POST'])
def efi_webhook():
    """Webhook da Efí. O Efí EXIGE um webhook na chave PIX recebedora pra liberar o Pix Envio
    (comissão). Só precisa responder 200 — a confirmação de pagamento roda pelo reconciliador."""
    return jsonify({'ok': True}), 200


@app.route('/saas-admin/efi-webhook')
@_saas_admin_required
def saas_efi_webhook():
    """Cadastra/re-cadastra o webhook na chave Efí — destrava o Pix Envio (pagamento de comissão).
    Roda 1× e resolve o 'conta_chave_sem_webhook'."""
    try:
        import efi_pix
        if not efi_pix.configurado():
            return jsonify({'erro': 'Efí não configurada (faltam env vars).'}), 400
        # Doc Efí (setup nuvem SEM mTLS): a URL PRECISA terminar com '?ignorar=' — isso impede o
        # Efí de anexar '/pix' e destrava a validação (sem isso dá ECONNRESET). '?host=' permite
        # testar outro domínio (ex: o *.up.railway.app) sem precisar de novo deploy.
        # O Railway RESETA a conexão de validação da Efí (ECONNRESET) — confirmado: o endpoint
        # responde 200 pra qualquer outro cliente, só a Efí toma reset. Então o webhook precisa
        # ficar FORA do Railway. ?wurl= permite apontar pra uma URL externa (ex: webhook.site).
        # O webhook só precisa EXISTIR + responder 200 (a baixa roda pelo reconciliador, não por ele).
        url = (request.args.get('wurl') or '').strip()
        if url:
            if 'ignorar' not in url:
                url += ('&' if '?' in url else '?') + 'ignorar='
        else:
            host = request.args.get('host', request.host).strip().rstrip('/')
            url  = f"https://{host}/webhook/efi?ignorar="
        res  = efi_pix.configurar_webhook(url)
        return jsonify({'url_usada': url, 'cadastrar': res,
                        'webhook_atual': efi_pix.consultar_webhook()})
    except Exception as _e:
        return jsonify({'erro': str(_e)[:300]}), 500


@app.route('/saas-admin/afiliados')
@_saas_admin_required
def saas_admin_afiliados():
    """Painel admin do programa de afiliados: quem está vendendo + comissões."""
    from afiliados_db import get_afil_db
    _brl = lambda v: ('R$ %.2f' % float(v or 0)).replace('.', ',')
    conn = get_afil_db()
    afs = conn.execute('''
        SELECT a.id, a.nome, a.email, a.telefone, a.codigo, a.pix_chave, a.pix_tipo,
               COALESCE(SUM(c.valor),0) AS total,
               COALESCE(SUM(CASE WHEN c.status='pago' THEN c.valor ELSE 0 END),0) AS pago,
               COALESCE(SUM(CASE WHEN c.status<>'pago' THEN c.valor ELSE 0 END),0) AS pendente,
               COUNT(DISTINCT c.cliente_nome) AS clientes
        FROM afiliados a LEFT JOIN afiliado_conversoes c ON c.afiliado_id=a.id
        GROUP BY a.id ORDER BY pago DESC, a.id DESC''').fetchall()
    convs = conn.execute('''
        SELECT c.app, c.cliente_nome, c.valor, c.status, c.created_at,
               a.nome AS af_nome, a.codigo AS af_codigo
        FROM afiliado_conversoes c JOIN afiliados a ON a.id=c.afiliado_id
        ORDER BY c.id DESC LIMIT 100''').fetchall()
    tot = conn.execute('SELECT COUNT(*) FROM afiliados').fetchone()[0]
    sm = conn.execute("SELECT COALESCE(SUM(valor),0), "
                      "COALESCE(SUM(CASE WHEN status='pago' THEN valor ELSE 0 END),0), "
                      "COALESCE(SUM(CASE WHEN status<>'pago' THEN valor ELSE 0 END),0) "
                      "FROM afiliado_conversoes").fetchone()
    conn.close()
    resumo = {'afiliados': tot, 'total': sm[0], 'pago': sm[1], 'pendente': sm[2]}
    return render_template('saas_admin_afiliados.html', afs=afs, convs=convs, resumo=resumo, brl=_brl)


@app.route('/saas-admin')
@_saas_admin_required
def saas_admin():
    """Painel de admin do SaaS — lista assinantes do AlertaJá."""
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
    # SalaTV clients
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
            'SELECT id, name, slug, owner_name, phone, email, city, plan, mode, active, plan_active, trial_ends, created_at FROM mandaja_stores ORDER BY id DESC'
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
            "SELECT u.id, u.name, u.email, u.phone, u.active, u.plan, u.plan_active, "
            "u.created_at, u.last_login, "
            "(SELECT COUNT(*) FROM mandazap_users m WHERE lower(m.email)=lower(u.email)) AS tem_mandazap "
            "FROM slotzap_users u ORDER BY u.id DESC"
        ).fetchall()]
        conn_sz.close()
    except Exception:
        sz_users = []
    # DRZAP users (banco próprio)
    try:
        from drzap_db import get_drzap_db as _get_drz_db
        drzconn = _get_drz_db()
        drz_users = [dict(r) for r in drzconn.execute(
            'SELECT id, nome, email, telefone, creditos, created_at, ultimo_acesso FROM drzap_users ORDER BY id DESC'
        ).fetchall()]
        drz_compras_total = drzconn.execute("SELECT COUNT(*) FROM drzap_compras WHERE status='pago'").fetchone()[0]
        drz_receita       = drzconn.execute("SELECT COALESCE(SUM(valor),0) FROM drzap_compras WHERE status='pago'").fetchone()[0]
        drz_consultas     = drzconn.execute('SELECT COUNT(*) FROM drzap_uso_log').fetchone()[0]
        drzconn.close()
    except Exception:
        drz_users = []; drz_compras_total = 0; drz_receita = 0; drz_consultas = 0
    # PCD Fácil (banco próprio)
    try:
        from pcd_db import get_pcd_db as _get_pcd_db
        pcdconn = _get_pcd_db()
        pcd_users = [dict(r) for r in pcdconn.execute(
            'SELECT id, nome, email, telefone, perfil, creditos, created_at FROM pcd_users ORDER BY id DESC'
        ).fetchall()]
        pcd_casos_total   = pcdconn.execute('SELECT COUNT(*) FROM pcd_casos').fetchone()[0]
        pcd_montados      = pcdconn.execute("SELECT COUNT(*) FROM pcd_casos WHERE status='montado'").fetchone()[0]
        pcd_compras_total = pcdconn.execute("SELECT COUNT(*) FROM pcd_compras WHERE status='pago'").fetchone()[0]
        pcd_receita       = pcdconn.execute("SELECT COALESCE(SUM(valor),0) FROM pcd_compras WHERE status='pago'").fetchone()[0]
        pcdconn.close()
    except Exception:
        pcd_users = []; pcd_casos_total = 0; pcd_montados = 0; pcd_compras_total = 0; pcd_receita = 0
    # Radar de Licitações de TI (banco próprio)
    try:
        from radar_db import (listar_radar_users as _r_users, estatisticas as _r_st,
                              stats_contratos as _r_stc)
        radar_users = _r_users()
        _rs = _r_st(); _rc = _r_stc()
        radar_total_lic = _rs.get('total', 0); radar_ti = _rs.get('ti', 0)
        radar_ouro = _rs.get('ouro', 0)
        radar_contratos = _rc.get('total', 0); radar_vencendo = _rc.get('vencendo90', 0)
    except Exception:
        radar_users = []; radar_total_lic = 0; radar_ti = 0; radar_ouro = 0
        radar_contratos = 0; radar_vencendo = 0
    # Radar Licita Norte (regional)
    try:
        from radar_db import (listar_licita_users as _l_users,
                              stats_licita_norte as _l_st)
        licita_users = _l_users()
        _ls = _l_st()
        licita_total = _ls.get('total', 0); licita_noticia = _ls.get('noticia', 0)
        licita_cidades = _ls.get('cidades', 0)
    except Exception:
        licita_users = []; licita_total = 0; licita_noticia = 0; licita_cidades = 0
    # SomaJá — coach financeiro no WhatsApp (banco próprio)
    try:
        from somaja_db import get_somaja_db as _get_soma_db
        somaconn = _get_soma_db()
        somaja_users = [dict(r) for r in somaconn.execute(
            'SELECT id, nome, email, telefone, plano, plan_active, trial_until, created_at '
            'FROM somaja_users ORDER BY id DESC').fetchall()]
        somaja_lancamentos = somaconn.execute('SELECT COUNT(*) FROM somaja_tx').fetchone()[0]
        somaja_ativos = somaconn.execute('SELECT COUNT(*) FROM somaja_users WHERE plan_active=1').fetchone()[0]
        somaconn.close()
    except Exception:
        somaja_users = []; somaja_lancamentos = 0; somaja_ativos = 0
    # MLhype — inteligência p/ vendedores do Mercado Livre (banco próprio)
    try:
        from mlhype_db import get_mlhype_db as _get_mlhype_db
        mlconn = _get_mlhype_db()
        mlhype_users = [dict(r) for r in mlconn.execute(
            'SELECT id, nome, email, telefone, plano, plan_active, created_at, ultimo_acesso '
            'FROM mlhype_users ORDER BY id DESC').fetchall()]
        mlhype_ativos = mlconn.execute('SELECT COUNT(*) FROM mlhype_users WHERE plan_active=1').fetchone()[0]
        mlconn.close()
    except Exception:
        mlhype_users = []; mlhype_ativos = 0
    # RifaJá — rifas baratinhas (campanhas com gateway Efí)
    try:
        conn_rj = get_saas_db()
        rifaja_rifas = [dict(r) for r in conn_rj.execute('''
            SELECT c.id, c.nome, c.preco, c.total_slots, c.status, c.created_at,
                   u.name AS dono_nome, u.email AS dono_email,
                   (SELECT COUNT(*) FROM slotzap_slots s WHERE s.campanha_id=c.id AND s.status="pago") AS pagos
            FROM slotzap_campanhas c
            LEFT JOIN slotzap_users u ON u.id=c.user_id
            WHERE c.gateway='efi'
            ORDER BY c.id DESC
        ''').fetchall()]
        conn_rj.close()
        for r in rifaja_rifas:
            r['receita'] = (r['pagos'] or 0) * float(r['preco'] or 0)
            r['pct'] = round(100 * (r['pagos'] or 0) / (r['total_slots'] or 1))
        rifaja_total_rifas = len(rifaja_rifas)
        rifaja_vendidos    = sum((r['pagos'] or 0) for r in rifaja_rifas)
        rifaja_receita     = sum(r['receita'] for r in rifaja_rifas)
    except Exception:
        rifaja_rifas = []; rifaja_total_rifas = 0; rifaja_vendidos = 0; rifaja_receita = 0
    return render_template('saas_admin.html',
                           subscribers=subscribers, businesses=businesses,
                           mz_users=mz_users, mz_plans=MANDAZAP_PLANS,
                           bau_users=bau_users,
                           kids_clients=kids_clients, kids_modes=MODES,
                           desp_users=desp_users, desp_plans=DESP_PLANS,
                           defesa_users=defesa_users,
                           mandaja_stores=mandaja_stores, mandaja_plans=MANDAJA_PLANS,
                           now_iso=datetime.now().isoformat(),
                           vetzap_users=vetzap_users,
                           vetzap_pets_total=vetzap_pets_total,
                           vetzap_triagens_total=vetzap_triagens_total,
                           alerta_plans=ALERTA_PLANS,
                           pubshow_bars=pubshow_bars,
                           pubshow_total_pedidos=pubshow_total_pedidos,
                           pubshow_total_receita=pubshow_total_receita,
                           pubshow_total_videos=pubshow_total_videos,
                           sz_users=sz_users,
                           drz_users=drz_users, drz_compras_total=drz_compras_total,
                           drz_receita=drz_receita, drz_consultas=drz_consultas,
                           pcd_users=pcd_users, pcd_casos_total=pcd_casos_total,
                           pcd_montados=pcd_montados, pcd_compras_total=pcd_compras_total,
                           pcd_receita=pcd_receita,
                           radar_users=radar_users, radar_total_lic=radar_total_lic,
                           radar_ti=radar_ti, radar_ouro=radar_ouro,
                           radar_contratos=radar_contratos, radar_vencendo=radar_vencendo,
                           licita_users=licita_users, licita_total=licita_total,
                           licita_noticia=licita_noticia, licita_cidades=licita_cidades,
                           somaja_users=somaja_users, somaja_lancamentos=somaja_lancamentos,
                           somaja_ativos=somaja_ativos,
                           mlhype_users=mlhype_users, mlhype_ativos=mlhype_ativos,
                           rifaja_rifas=rifaja_rifas, rifaja_total_rifas=rifaja_total_rifas,
                           rifaja_vendidos=rifaja_vendidos, rifaja_receita=rifaja_receita)


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


@app.route('/saas-admin/slotzap/set-plan', methods=['POST'])
@_saas_admin_required
def saas_sz_set_plan():
    """Define o plano (start/pro) e ativa a assinatura do usuário SlotZap."""
    data = request.get_json() or {}
    uid  = data.get('user_id')
    plan = (data.get('plan') or 'start').strip()
    if plan not in ('start', 'pro'):
        plan = 'start'
    if not uid:
        return jsonify({'erro': 'user_id obrigatório'}), 400
    conn = get_saas_db()
    conn.execute('UPDATE slotzap_users SET plan=?, plan_active=1, active=1 WHERE id=?', (plan, uid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/saas-admin/mlhype/set-plan', methods=['POST'])
@_saas_admin_required
def saas_mlhype_set_plan():
    """Define o plano (free/starter/pro/business) do usuário MLhype."""
    data  = request.get_json() or {}
    uid   = data.get('user_id')
    plano = (data.get('plano') or 'free').strip()
    if plano not in ('free', 'starter', 'pro', 'business'):
        plano = 'free'
    if not uid:
        return jsonify({'erro': 'user_id obrigatório'}), 400
    try:
        from mlhype_db import get_mlhype_db as _gml
        conn = _gml()
        conn.execute('UPDATE mlhype_users SET plano=?, plan_active=? WHERE id=?',
                     (plano, 0 if plano == 'free' else 1, uid))
        conn.commit(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/saas-admin/mlhype/reset-senha', methods=['POST'])
@_saas_admin_required
def saas_mlhype_reset_senha():
    data  = request.get_json() or {}
    uid   = data.get('user_id')
    senha = (data.get('senha') or '').strip()
    if not uid or len(senha) < 6:
        return jsonify({'erro': 'user_id e senha (mín. 6 chars) obrigatórios'}), 400
    try:
        from mlhype_db import get_mlhype_db as _gml
        conn = _gml()
        conn.execute('UPDATE mlhype_users SET password_hash=? WHERE id=?',
                     (generate_password_hash(senha), uid))
        conn.commit(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/saas-admin/somaja/set-plan', methods=['POST'])
@_saas_admin_required
def saas_somaja_set_plan():
    """Ativa/corta a assinatura e (opcional) define o plano do usuário SomaJá."""
    data  = request.get_json() or {}
    uid   = data.get('user_id')
    ativo = 1 if data.get('ativo') else 0
    plano = (data.get('plano') or '').strip() or None
    if not uid:
        return jsonify({'erro': 'user_id obrigatório'}), 400
    try:
        from somaja_db import get_somaja_db as _gsoma
        conn = _gsoma()
        conn.execute('UPDATE somaja_users SET plan_active=?, plano=COALESCE(?,plano) WHERE id=?',
                     (ativo, plano, uid))
        conn.commit(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)[:120]}), 500


@app.route('/saas-admin/somaja/reset-senha', methods=['POST'])
@_saas_admin_required
def saas_somaja_reset_senha():
    data  = request.get_json() or {}
    uid   = data.get('user_id')
    senha = (data.get('senha') or '').strip()
    if not uid or len(senha) < 6:
        return jsonify({'erro': 'user_id e senha (mín. 6 caracteres) obrigatórios'}), 400
    try:
        from somaja_db import get_somaja_db as _gsoma
        conn = _gsoma()
        conn.execute('UPDATE somaja_users SET password_hash=? WHERE id=?',
                     (generate_password_hash(senha), uid))
        conn.commit(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)[:120]}), 500


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


# ── Admin AlertaJá — trial ───────────────────────────────────────────────────

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


# ── Admin AlertaJá — mudar plano ────────────────────────────────────────────

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


@app.route('/saas-admin/agenda/backfill-servicos', methods=['POST'])
@_saas_admin_required
def saas_agenda_backfill_servicos():
    """Adiciona serviços-modelo do ramo nos negócios que NÃO têm nenhum serviço.
    Seguro e idempotente: nunca apaga nem altera serviços existentes — só semeia
    quando o negócio tem ZERO serviços cadastrados."""
    conn = get_saas_db()
    bizs = conn.execute('SELECT id, name, business_type FROM agenda_businesses').fetchall()
    now_iso = datetime.now().isoformat()
    negocios_seedados, servicos_add, detalhes = 0, 0, []
    for b in bizs:
        total = conn.execute(
            'SELECT COUNT(*) FROM agenda_services WHERE business_id=?', (b['id'],)
        ).fetchone()[0]
        if total > 0:
            continue  # já tem serviços — não mexe
        servicos = agenda_seg(b['business_type'])['servicos']
        for nome, dur, preco in servicos:
            conn.execute(
                'INSERT INTO agenda_services (business_id, name, duration_minutes, price, active, created_at) '
                'VALUES (?, ?, ?, ?, 1, ?)',
                (b['id'], nome, dur, preco, now_iso)
            )
        negocios_seedados += 1
        servicos_add += len(servicos)
        detalhes.append({'id': b['id'], 'nome': b['name'],
                         'ramo': b['business_type'], 'servicos': len(servicos)})
    conn.commit(); conn.close()
    return jsonify({'ok': True,
                    'negocios_atualizados': negocios_seedados,
                    'servicos_adicionados': servicos_add,
                    'detalhes': detalhes})


# ── Admin SalaTV — status / delete ───────────────────────────────────────

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
    afiliado_ref = (data.get('afiliado_ref') or '').strip().upper()[:12]
    if not name or not phone:
        return jsonify({'success': False, 'error': 'Nome e telefone obrigatórios'})
    conn = get_saas_db()
    try:
        cur = conn.execute(
            'INSERT INTO despachante_users (name, email, phone, empresa, cidade, plan, active, created_at, afiliado_ref) VALUES (?,?,?,?,?,?,1,?,?)',
            (name, email, phone, empresa, cidade, plan, datetime.now().isoformat(), (afiliado_ref or None))
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


@app.route('/admin/mandaja/store/<int:store_id>/delete', methods=['POST'])
@_saas_admin_required
def saas_mandaja_delete(store_id):
    """Exclui a loja e todos os dados ligados a ela (Jr ou Pro)."""
    conn = get_saas_db()
    row = conn.execute('SELECT id FROM mandaja_stores WHERE id=?', (store_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Loja não encontrada'}), 404
    for t in ('mandaja_hours', 'mandaja_products', 'mandaja_categories', 'mandaja_orders'):
        conn.execute(f'DELETE FROM {t} WHERE store_id=?', (store_id,))
    conn.execute('DELETE FROM mandaja_stores WHERE id=?', (store_id,))
    conn.commit(); conn.close()
    return jsonify({'success': True})


# ── Admin AgendaJá ───────────────────────────────────────────────────────────

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
    # Trava: só o estabelecimento logado (com o código) acessa o painel
    if (session.get('kids_code') or '').strip().upper() != code.strip().upper():
        return redirect('/kids/entrar')
    # QR Code do link da TV
    tv_qr_b64 = None
    try:
        import qrcode as _qr, io as _io, base64 as _b64
        q = _qr.QRCode(error_correction=_qr.constants.ERROR_CORRECT_M, box_size=7, border=2)
        q.add_data(f'https://www.4kitem.com.br/tv/{client["code"]}')
        q.make(fit=True)
        _buf = _io.BytesIO()
        q.make_image(fill_color='black', back_color='white').save(_buf, format='PNG')
        tv_qr_b64 = _b64.b64encode(_buf.getvalue()).decode()
    except Exception:
        pass
    import kids_db as _kdb
    return render_template('painel/index.html',
                           client=client, modes=MODES, tv_qr_b64=tv_qr_b64,
                           ads=_kdb.list_ads(client['code']), ad_limit=_kdb.AD_LIMIT,
                           uptime=_kdb.uptime_summary(client['code']))


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
    # Trava: só o estabelecimento logado pode trocar o canal (paciente não troca)
    if (session.get('kids_code') or '').strip().upper() != code.strip().upper():
        return jsonify({'error': 'nao_autorizado'}), 403
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', '')
    if not set_client_mode(code, mode):
        return jsonify({'error': f'modo inválido: {mode}'}), 400
    return jsonify({'ok': True, 'mode': mode,
                    'mode_label': MODES[mode]['label']})


# ── SalaTV — Anúncios do estabelecimento (entre os vídeos) ────────────────────
def _salatv_owner(code):
    return (session.get('kids_code') or '').strip().upper() == (code or '').strip().upper()


@app.route('/api/tv/<code>/ads')
def api_tv_ads(code):
    """Anúncios ativos do estabelecimento — consumido pelo player da TV."""
    import kids_db
    return jsonify({'ads': kids_db.list_ads(code, active_only=True)})


@app.route('/painel/<code>/ad/add', methods=['POST'])
def salatv_ad_add(code):
    if not _salatv_owner(code):
        return redirect('/kids/entrar')
    import kids_db
    kids_db.add_ad(
        code, tipo='texto',
        titulo=request.form.get('titulo', '').strip(),
        subtitulo=request.form.get('subtitulo', '').strip(),
        emoji=(request.form.get('emoji', '').strip() or '📢'),
        cor=(request.form.get('cor', '').strip() or '#6366f1'),
    )
    return redirect(f'/painel/{code}#anuncios')


@app.route('/painel/<code>/ad/upload', methods=['POST'])
def salatv_ad_upload(code):
    if not _salatv_owner(code):
        return redirect('/kids/entrar')
    import kids_db, random as _rnd
    fobj = request.files.get('imagem')
    if not fobj or not fobj.filename:
        return redirect(f'/painel/{code}#anuncios')
    ext = fobj.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return redirect(f'/painel/{code}?erro=formato#anuncios')
    pasta = os.path.join(app.static_folder, 'salatv_ads', code)
    os.makedirs(pasta, exist_ok=True)
    fname = f'{_rnd.randint(10000,99999)}.{ext}'
    fobj.save(os.path.join(pasta, fname))
    kids_db.add_ad(code, tipo='imagem', image_url=f'/static/salatv_ads/{code}/{fname}')
    return redirect(f'/painel/{code}#anuncios')


@app.route('/painel/<code>/ad/<int:ad_id>/delete', methods=['POST'])
def salatv_ad_delete(code, ad_id):
    if not _salatv_owner(code):
        return jsonify({'error': 'nao_autorizado'}), 403
    import kids_db
    removed = kids_db.delete_ad(ad_id, code)
    if removed and removed.get('tipo') == 'imagem' and removed.get('image_url', '').startswith('/static/'):
        try:
            os.remove(os.path.join(app.static_folder, removed['image_url'].split('/static/', 1)[1]))
        except Exception:
            pass
    return jsonify({'ok': True})


@app.route('/painel/<code>/ad/<int:ad_id>/toggle', methods=['POST'])
def salatv_ad_toggle(code, ad_id):
    if not _salatv_owner(code):
        return jsonify({'error': 'nao_autorizado'}), 403
    import kids_db
    kids_db.toggle_ad(ad_id, code)
    return jsonify({'ok': True})


# ── SalaTV — Heartbeat (horas no ar) + PWA (manifest/ícone/service worker) ─────
@app.route('/api/tv/<code>/heartbeat', methods=['POST'])
def api_tv_heartbeat(code):
    import kids_db
    if not get_client(code):
        return jsonify({'ok': False}), 404
    kids_db.bump_uptime(code, 1)
    return jsonify({'ok': True})


@app.route('/tv/<code>/manifest.json')
def tv_manifest(code):
    client = get_client(code)
    nome = (client['name'] if client else 'SalaTV') or 'SalaTV'
    manifest = {
        'name':             f'SalaTV — {nome}',
        'short_name':       'SalaTV',
        'description':      f'TV de {nome} — conteúdo curado para a sala de espera',
        'start_url':        f'/tv/{code}',
        'scope':            f'/tv/{code}',
        'display':          'fullscreen',
        'orientation':      'landscape',
        'background_color': '#000000',
        'theme_color':      '#0f0f1f',
        'lang':             'pt-BR',
        'icons': [
            {'src': f'/tv/{code}/icon/192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
            {'src': f'/tv/{code}/icon/512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
    }
    import json as _json
    return app.response_class(_json.dumps(manifest), mimetype='application/manifest+json')


@app.route('/tv/<code>/icon/<int:size>.png')
def tv_icon(code, size):
    if size not in (192, 512):
        size = 192
    try:
        from PIL import Image, ImageDraw
        import io as _io
        img  = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r = size // 6
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(15, 15, 31))
        cx = cy = size // 2
        rr = size // 3
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(99, 102, 241, 255))
        st = size // 7
        draw.polygon([(cx - st + size // 22, cy - int(st * 1.25)),
                      (cx - st + size // 22, cy + int(st * 1.25)),
                      (cx + int(st * 1.4), cy)], fill=(255, 255, 255, 255))
        buf = _io.BytesIO()
        img.save(buf, format='PNG')
        return app.response_class(buf.getvalue(), mimetype='image/png')
    except Exception as e:
        log.error(f'[SalaTV icon] {e}')
        abort(404)


@app.route('/salatv-sw.js')
def salatv_sw():
    js = (
        "const C='salatv-v1';"
        "self.addEventListener('install',e=>self.skipWaiting());"
        "self.addEventListener('activate',e=>self.clients.claim());"
        "self.addEventListener('fetch',e=>{});"
    )
    resp = app.response_class(js, mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

# ── Criar cliente ─────────────────────────────────────────────────────────
@app.route('/api/clients', methods=['POST'])
@_saas_admin_required
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
    # Anti-abuso: só estabelecimento logado pode disparar a busca de vídeos
    if not session.get('kids_code'):
        return jsonify({'error': 'nao_autorizado'}), 403
    def _run():
        try:
            from kids_scraper import scrape_all
            scrape_all()
        except Exception as e:
            log.error(f"Scrape error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'status': 'started'})


# ══════════════════════════════════════════════════════════════════════════
#  SalaTV — Admin de conteúdo (canais + vídeos + revisor de links do YouTube)
# ══════════════════════════════════════════════════════════════════════════
_salatv_revisao = {'running': False, 'total': 0, 'checked': 0, 'broken': 0, 'done_at': ''}


def _yt_extract_id(s):
    import re as _re
    s = (s or '').strip()
    if not s:
        return ''
    m = _re.search(r'(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})', s)
    if m:
        return m.group(1)
    if _re.fullmatch(r'[A-Za-z0-9_-]{11}', s):
        return s
    return ''


def _yt_alive(youtube_id):
    """True se o vídeo existe e permite embed (via oEmbed do YouTube)."""
    try:
        import requests as _req
        r = _req.get('https://www.youtube.com/oembed',
                     params={'url': f'https://www.youtube.com/watch?v={youtube_id}',
                             'format': 'json'}, timeout=8)
        return r.status_code == 200
    except Exception:
        return True  # erro de rede: não marca como quebrado


@app.route('/saas-admin/salatv')
@_saas_admin_required
def salatv_admin():
    import kids_db
    ch_id = request.args.get('canal', type=int)
    only_blocked = request.args.get('problema') == '1'
    q = request.args.get('q', '').strip()
    return render_template('kids/admin.html',
                           channels=kids_db.list_channels_admin(),
                           videos=kids_db.list_videos_admin(channel_ref=ch_id, only_blocked=only_blocked, q=q),
                           stats=kids_db.stats(), modes=MODES,
                           filtro_canal=ch_id, filtro_problema=only_blocked, q=q,
                           revisao=_salatv_revisao, cupons=kids_db.list_cupons())


@app.route('/saas-admin/salatv/canal/add', methods=['POST'])
@_saas_admin_required
def salatv_canal_add():
    import kids_db
    name = request.form.get('name', '').strip()
    handle = request.form.get('handle', '').strip().lstrip('@')
    category = request.form.get('category', 'Geral').strip() or 'Geral'
    try:
        age_min = int(request.form.get('age_min') or 0)
        age_max = int(request.form.get('age_max') or 14)
    except ValueError:
        age_min, age_max = 0, 14
    gender = request.form.get('gender', 'N').strip() or 'N'
    is_safe = 1 if request.form.get('is_safe') else 0
    if name and handle:
        kids_db.add_channel(name, '@' + handle, None, age_min, age_max, gender, category, 'PT-BR', is_safe)
    return redirect('/saas-admin/salatv')


@app.route('/saas-admin/salatv/canal/<int:ch_id>/toggle', methods=['POST'])
@_saas_admin_required
def salatv_canal_toggle(ch_id):
    import kids_db
    kids_db.toggle_channel(ch_id)
    return jsonify({'ok': True})


@app.route('/saas-admin/salatv/canal/<int:ch_id>/delete', methods=['POST'])
@_saas_admin_required
def salatv_canal_delete(ch_id):
    import kids_db
    kids_db.delete_channel(ch_id)
    return jsonify({'ok': True})


@app.route('/saas-admin/salatv/canal/<int:ch_id>/scrape', methods=['POST'])
@_saas_admin_required
def salatv_canal_scrape(ch_id):
    def _run():
        try:
            import kids_db
            from kids_scraper import resolve_channel_id, fetch_channel_videos
            conn = kids_db.get_conn()
            row = conn.execute('SELECT * FROM channels WHERE id=?', (ch_id,)).fetchone()
            conn.close()
            if not row:
                return
            ch = dict(row)
            yt = ch.get('channel_id')
            if not yt:
                yt = resolve_channel_id(ch['handle'])
                if yt:
                    kids_db.update_channel_id(ch_id, yt)
            if yt:
                fetch_channel_videos(yt, ch_id, ch['age_min'], ch['age_max'], ch['gender'])
        except Exception as e:
            log.error(f'[SalaTV scrape canal] {e}')
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True, 'msg': 'Buscando vídeos em background...'})


@app.route('/saas-admin/salatv/video/add', methods=['POST'])
@_saas_admin_required
def salatv_video_add():
    import kids_db
    yid = _yt_extract_id(request.form.get('youtube', ''))
    ch_id = request.form.get('canal', type=int)
    title = request.form.get('title', '').strip()
    erro = '' if yid else 'link_invalido'
    if yid:
        kids_db.add_video_manual(yid, title, ch_id)
    return redirect('/saas-admin/salatv' + ('?erro=' + erro if erro else ''))


@app.route('/saas-admin/salatv/video/<int:vid>/delete', methods=['POST'])
@_saas_admin_required
def salatv_video_delete(vid):
    import kids_db
    kids_db.delete_video(vid)
    return jsonify({'ok': True})


@app.route('/saas-admin/salatv/revisar-links', methods=['POST'])
@_saas_admin_required
def salatv_revisar_links():
    if _salatv_revisao['running']:
        return jsonify({'ok': False, 'msg': 'Revisão já em andamento'})

    def _run():
        import kids_db
        vids = kids_db.all_video_ids()
        _salatv_revisao.update(running=True, total=len(vids), checked=0, broken=0, done_at='')
        broken = 0
        for v in vids:
            alive = _yt_alive(v['youtube_id'])
            kids_db.set_video_ok(v['youtube_id'], 1 if alive else 0)
            if not alive:
                broken += 1
            _salatv_revisao['checked'] += 1
            _salatv_revisao['broken'] = broken
        _salatv_revisao.update(running=False, done_at=datetime.now().strftime('%d/%m %H:%M'))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/saas-admin/salatv/revisar-links/status')
@_saas_admin_required
def salatv_revisar_status():
    return jsonify(_salatv_revisao)


# ── SalaTV — Cupons de desconto ───────────────────────────────────────────────
@app.route('/api/kids/validar-cupom/<codigo>')
def api_kids_validar_cupom(codigo):
    """Valida um cupom e devolve o preço com desconto para o plano informado."""
    import kids_db
    plano = request.args.get('plano', 'mensal')
    p = KIDS_PLANS.get(plano)
    if not p:
        return jsonify({'ok': False, 'erro': 'plano inválido'}), 400
    c = kids_db.validar_cupom(codigo)
    if not c:
        return jsonify({'ok': False, 'erro': 'Cupom inválido ou expirado'})
    preco_final = round(p['preco'] * (1 - c['desconto_pct'] / 100.0), 2)
    return jsonify({
        'ok': True,
        'codigo': c['codigo'],
        'desconto_pct': c['desconto_pct'],
        'preco_original': p['preco'],
        'preco_final': preco_final,
        'descricao': c.get('descricao', ''),
    })


@app.route('/saas-admin/salatv/cupom/add', methods=['POST'])
@_saas_admin_required
def salatv_cupom_add():
    import kids_db
    kids_db.add_cupom(
        request.form.get('codigo', '').strip(),
        request.form.get('desconto_pct', '10'),
        request.form.get('descricao', '').strip(),
        request.form.get('max_usos', '').strip() or None,
        request.form.get('valido_ate', '').strip() or None,
    )
    return redirect('/saas-admin/salatv#cupons')


@app.route('/saas-admin/salatv/cupom/<int:cid>/toggle', methods=['POST'])
@_saas_admin_required
def salatv_cupom_toggle(cid):
    import kids_db
    kids_db.toggle_cupom(cid)
    return jsonify({'ok': True})


@app.route('/saas-admin/salatv/cupom/<int:cid>/delete', methods=['POST'])
@_saas_admin_required
def salatv_cupom_delete(cid):
    import kids_db
    kids_db.delete_cupom(cid)
    return jsonify({'ok': True})


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
    _ref = (request.args.get('ref') or '').strip().upper()[:12]
    if _ref:
        session['mz_ref'] = _ref   # afiliado que trouxe (programa de afiliados)
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
                trial = (now + timedelta(days=2)).isoformat()   # trial curto = força a compra (decisão do Diogo)
                _ref_af = (session.get('mz_ref') or request.args.get('ref') or '').strip().upper()[:12]
                conn.execute(
                    'INSERT INTO mandazap_users (name, email, password_hash, phone, cpf_cnpj, plan, created_at, trial_ends, afiliado_ref) VALUES (?,?,?,?,?,?,?,?,?)',
                    (name, email, generate_password_hash(password), phone, cpf_cnpj, 'solo', now.isoformat(), trial, (_ref_af or None))
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
                        '📲 Bem-vindo ao MandaZap — 2 dias grátis!',
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
    # Desconto combo: se já tem SlotZap ativo (mesmo e-mail)
    conn0  = get_saas_db()
    _urow  = conn0.execute('SELECT email FROM mandazap_users WHERE id=?', (user_id,)).fetchone()
    conn0.close()
    _email      = dict(_urow)['email'] if _urow else ''
    combo       = _combo_desconto_ativo(_email, 'mandazap')
    preco_final = round(p['price'] * (1 - COMBO_DESCONTO), 2) if combo else float(p['price'])
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
            desc = f'MandaZap {p["label"]} — Assinatura Mensal' + (' (combo -25%)' if combo else '')
            resp = _asaas_criar_assinatura_saas(
                customer_id, 'mandazap', plano, preco_final, desc, billing_type)
            if resp.get('id'):
                return redirect('/mandazap/aguardando-pagamento')
            else:
                erro = 'Não foi possível gerar o pagamento. Tente novamente.'
    return render_template('mandazap/checkout.html', plano=p, plano_key=plano,
                           planos=MANDAZAP_PLANS, erro=erro,
                           combo=combo, preco_final=preco_final)


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
    # Info de aquecimento (warm-up) e cooldown por número, para exibir no painel
    _plan_info   = MANDAZAP_PLANS.get(plan_key, MANDAZAP_PLANS['solo'])
    _per_num_pln = max(1, _plan_info['daily_limit'] // max(1, _plan_info.get('numbers', 1)))
    for _n in numbers:
        _age = _mz_number_age_days(_n)
        _n['warmup_day']  = _age
        _n['warmup_done'] = _age >= 25
        _n['warmup_cap']  = _mz_warmup_cap(_age)
        # Teto efetivo do dia = menor entre warm-up, cota do plano, teto conservador do plano e teto rígido
        _n['daily_cap']   = _mz_effective_daily_cap(_n, _per_num_pln, _plan_info.get('daily_safe_cap'))
        _n['sent_today']  = _mz_number_sent_today(conn, _n['id'])
        _n['health']      = _mz_number_health(conn, _n['id'])   # Fase 3: saúde (reply-ratio)
        _n['in_cooldown'] = _mz_in_cooldown(_n)
        _n['cooldown_hm'] = ''
        if _n['in_cooldown']:
            try:
                _n['cooldown_hm'] = datetime.fromisoformat(_n['cooldown_until']).strftime('%d/%m %H:%M')
            except Exception:
                pass
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
    if not _dev_token_ok(token):
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
    if not _dev_token_ok(token):
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


def _mz_trial_blocked(user_id) -> bool:
    """A1: True se a conta tem trial vencido SEM plano ativo (e não é whitelist).
    Para rotas JSON (qr/status/upload) que não passam pelo decorator de redirect."""
    try:
        c = get_saas_db()
        u = c.execute('SELECT trial_ends, plan_active, email, phone FROM mandazap_users WHERE id=?', (user_id,)).fetchone()
        c.close()
        if not u:
            return True
        expired = bool(u['trial_ends'] and u['trial_ends'] < datetime.now().isoformat())
        if not expired or u['plan_active']:
            return False
        return not _is_whitelisted(u['email'], _re.sub(r'\D', '', u['phone'] or ''))
    except Exception:
        return False  # na dúvida, não bloqueia (fail-open)


@app.route('/mandazap/numeros/<int:num_id>/qr')
def mz_qr(num_id):
    user_id = session.get('mz_user_id')
    if not user_id:
        return jsonify({'erro': 'Não autenticado'}), 401
    if _mz_trial_blocked(user_id):
        return jsonify({'erro': 'Período de teste encerrado. Assine para conectar números.', 'paywall': True}), 402
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

        # Cria instância limpa — settings "humanos" anti-ban (Fase 1):
        # rejeita chamadas c/ mensagem automática, ignora grupos, fica online,
        # marca msgs como lidas e NÃO sincroniza histórico (mais leve + menos cara de bot).
        cr      = _req.post(f"{evo_url}/instance/create", headers=headers,
                            json={'instanceName': instance, 'qrcode': True,
                                  'integration': 'WHATSAPP-BAILEYS',
                                  'rejectCall': True,
                                  'msgCall': 'Olá! 🙂 No momento não consigo atender chamadas por aqui. '
                                             'Me manda uma mensagem que eu te respondo!',
                                  'groupsIgnore': True,
                                  'alwaysOnline': True,
                                  'readMessages': True,
                                  'readStatus': False,
                                  'syncFullHistory': False}, timeout=20)
        cr_data = cr.json() if cr.content else {}
        log.info(f"Evo create [{instance}] HTTP {cr.status_code}: {str(cr_data)[:300]}")
        _mz_set_instance_webhook(evo_url, evo_key, instance)  # Fase 3: recebe respostas p/ reply-ratio
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
            # Marca o início do aquecimento (warm-up) no PRIMEIRO connect deste número
            has_warmup = ('warmup_start' in num.keys()) and (num['warmup_start'])
            if is_connected and not has_warmup:
                conn.execute(
                    'UPDATE mandazap_numbers SET status=?, phone=?, warmup_start=? WHERE id=?',
                    (new_status, phone_info or num['phone'], datetime.now().isoformat(), num_id)
                )
            else:
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


# ── Webhook Evolution → conta respostas (Fase 3: reply-ratio) ──────────────────

def _mz_webhook_url() -> str:
    """URL pública do webhook do MandaZap (com token opcional)."""
    base = os.environ.get('MZ_PUBLIC_URL', 'https://4kitem.com.br').rstrip('/')
    tok  = os.environ.get('MZ_WEBHOOK_TOKEN', '')
    return f"{base}/mandazap/webhook/evolution" + (f"?token={tok}" if tok else '')


def _mz_set_instance_webhook(evo_url, evo_key, instance):
    """Best-effort: aponta o webhook da instância p/ o MandaZap (evento messages.upsert).
    Se falhar, o número só não terá reply-ratio — nada quebra.
    """
    try:
        import requests as _req
        _req.post(
            f"{evo_url}/webhook/set/{instance}",
            headers={'apikey': evo_key, 'Content-Type': 'application/json'},
            json={'webhook': {'enabled': True, 'url': _mz_webhook_url(),
                              'events': ['MESSAGES_UPSERT', 'CONNECTION_UPDATE'],
                              'webhookByEvents': False, 'webhookBase64': False}},
            timeout=8,
        )
    except Exception as e:
        log.warning(f"webhook set error [{instance}]: {e}")


@app.route('/mandazap/webhook/evolution', methods=['POST'])
def mz_webhook_evolution():
    """Recebe eventos da Evolution. Conta mensagens RECEBIDAS (respostas dos clientes)
    por número → alimenta o reply-ratio (saúde anti-ban). Best-effort: nunca derruba."""
    try:
        tok = os.environ.get('MZ_WEBHOOK_TOKEN', '')
        if tok and request.args.get('token', '') != tok:
            return jsonify({'ok': False}), 403
        payload = request.get_json(silent=True) or {}
        event   = str(payload.get('event', '')).lower().replace('_', '.')
        # R2: desconexão/logout do número → marca 'disconnected' (detecta queda/ban em
        # SEGUNDOS; antes só percebia a cada 25 envios ou 3 falhas). Self-contained.
        if 'connection.update' in event or 'logout' in event:
            _d = payload.get('data', {})
            if isinstance(_d, list): _d = _d[0] if _d else {}
            _inst = str(payload.get('instance') or (_d.get('instance') if isinstance(_d, dict) else '') or '')
            _mm = _re.match(r'^mz(\d+)n(\d+)$', _inst)
            _state = str((_d.get('state') or _d.get('connection') or '') if isinstance(_d, dict) else '').lower()
            if _mm and ('close' in _state or 'disconnect' in _state or 'logout' in event):
                try:
                    _c = get_saas_db()
                    _c.execute("UPDATE mandazap_numbers SET status='disconnected' WHERE id=? AND user_id=?",
                               (int(_mm.group(2)), int(_mm.group(1))))
                    _c.commit(); _c.close()
                    log.warning(f"[MZ webhook] {_inst} desconectou (state={_state or 'logout'}) — marcado disconnected")
                except Exception as _ce:
                    log.warning(f"mz_webhook conn.update error: {_ce}")
            return jsonify({'ok': True}), 200
        if 'messages.upsert' not in event:
            return jsonify({'ok': True, 'skip': 'event'}), 200
        data = payload.get('data', {})
        if isinstance(data, list):
            data = data[0] if data else {}
        key = (data.get('key') or {}) if isinstance(data, dict) else {}
        if key.get('fromMe'):                       # enviada por nós — não conta como resposta
            return jsonify({'ok': True, 'skip': 'fromMe'}), 200
        instance = str(payload.get('instance') or (data.get('instance') if isinstance(data, dict) else '') or '')
        # SomaJá — roteia eventos da instância do SomaJá pro módulo dele (webhook global do Evolution)
        if instance and instance == os.environ.get('SOMAJA_WA_INSTANCE', 'somaja'):
            try:
                from somaja import processar_wa_evento as _soma_proc
                _soma_proc(payload)
            except Exception as _se:
                log.warning(f'[SomaJá] webhook evolution: {_se}')
            return jsonify({'ok': True, 'somaja': True}), 200
        # Amparo — roteia a instância do Amparo pro módulo dele (mesmo padrão do SomaJá)
        if instance and instance == os.environ.get('AMPARO_WA_INSTANCE', 'amparo'):
            try:
                from amparo import processar_wa_evento as _amp_proc
                _amp_proc(payload)
            except Exception as _ae:
                log.warning(f'[Amparo] webhook evolution: {_ae}')
            return jsonify({'ok': True, 'amparo': True}), 200
        # AtendeZap — instâncias 'atende{id}' (bot de atendimento por negócio)
        if instance.startswith('atende'):
            try:
                from atendezap import processar_wa_evento as _at_proc
                _at_proc(payload)
            except Exception as _ate:
                log.warning(f'[AtendeZap] webhook evolution: {_ate}')
            return jsonify({'ok': True, 'atendezap': True}), 200
        m = _re.match(r'^mz(\d+)n(\d+)$', instance)
        if not m:
            return jsonify({'ok': True, 'skip': 'instance'}), 200
        # A2: só conta se a instância existir de verdade (evita inflar replies de número
        # inexistente via POST forjado, já que o nome da instância é previsível).
        _u, _n = int(m.group(1)), int(m.group(2))
        _c = get_saas_db()
        _ok = _c.execute('SELECT 1 FROM mandazap_numbers WHERE id=? AND user_id=?', (_n, _u)).fetchone()
        _c.close()
        if not _ok:
            return jsonify({'ok': True, 'skip': 'unknown_instance'}), 200
        _mz_inc_number_replies(_n)
        return jsonify({'ok': True}), 200
    except Exception as e:
        log.warning(f"mz_webhook error: {e}")
        return jsonify({'ok': False}), 200          # 200 p/ a Evolution não reenviar em loop


# ── Upload de mídia (motor do MandaJá: volume persistente + compressão) ─────────
# Hospeda local (sem Imgur), no DATA_DIR (não some no redeploy). Comprime forte:
# imagem pesada (até 15MB) → JPEG 1280px q80 (ex.: 10MB → ~150KB). URL pública completa
# (MZ_PUBLIC_URL) porque a Evolution busca a imagem de FORA do app.
MANDAZAP_UPLOAD_DIR = os.path.join(
    os.environ.get('DATA_DIR', os.path.dirname(__file__)), 'uploads', 'mandazap')


@app.route('/uploads/mandazap/<path:filename>')
def mandazap_uploaded_file(filename):
    return send_from_directory(MANDAZAP_UPLOAD_DIR, filename)


@app.route('/mandazap/upload', methods=['POST'])
def mz_upload():
    user_id = session.get('mz_user_id')
    if not user_id:
        return jsonify({'erro': 'Não autenticado'}), 401
    if _mz_trial_blocked(user_id):
        return jsonify({'erro': 'Período de teste encerrado. Assine para enviar mídia.', 'paywall': True}), 402
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'jpg'
    f.seek(0, 2); size = f.tell(); f.seek(0)
    if size > 15 * 1024 * 1024:                       # entrada generosa — vamos comprimir
        return jsonify({'erro': f'Arquivo muito grande ({size//1024//1024}MB). Limite: 15MB'}), 400

    os.makedirs(MANDAZAP_UPLOAD_DIR, exist_ok=True)
    import secrets as _sec
    base = os.environ.get('MZ_PUBLIC_URL', 'https://4kitem.com.br').rstrip('/')

    # ── Imagem: comprime forte (PIL → JPEG, redimensiona p/ 1280px) ──
    if ext in ('jpg', 'jpeg', 'png', 'webp', 'bmp', 'heic', 'heif'):
        try:
            from PIL import Image, ImageOps
            img = Image.open(f.stream)
            img = ImageOps.exif_transpose(img)        # corrige rotação de foto de celular
            img = img.convert('RGB')
            maxd = 1280
            w, h = img.size
            if max(w, h) > maxd:
                if w >= h:
                    img = img.resize((maxd, round(h * maxd / w)), Image.LANCZOS)
                else:
                    img = img.resize((round(w * maxd / h), maxd), Image.LANCZOS)
            name = f"u{user_id}_{_sec.token_urlsafe(8)}.jpg"
            img.save(os.path.join(MANDAZAP_UPLOAD_DIR, name), 'JPEG', quality=80, optimize=True)
            return jsonify({'ok': True, 'url': f"{base}/uploads/mandazap/{name}", 'tipo': 'image'})
        except Exception as e:
            log.warning(f'[MandaZap] upload imagem error: {e}')
            return jsonify({'erro': 'Não consegui processar essa imagem. Tente outra.'}), 400

    # ── GIF: mantém animado (sem recompressão) ──
    if ext == 'gif':
        name = f"u{user_id}_{_sec.token_urlsafe(8)}.gif"
        f.save(os.path.join(MANDAZAP_UPLOAD_DIR, name))
        return jsonify({'ok': True, 'url': f"{base}/uploads/mandazap/{name}", 'tipo': 'image'})

    return jsonify({'erro': 'Tipo não permitido. Use JPG, PNG ou GIF. (Vídeo em breve.)'}), 400


# ── Contatos ──────────────────────────────────────────────────────────────────

def _mz_contacts_room(conn, user_id) -> int:
    """A6: vagas de contato restantes no plano (limite - atuais). ≥9999 = ilimitado."""
    lim = MANDAZAP_PLANS.get(session.get('mz_plan', 'solo'), MANDAZAP_PLANS['solo']).get('contacts_limit', 500)
    if lim >= 9999:
        return 10**9
    cur = conn.execute('SELECT COUNT(*) FROM mandazap_contacts WHERE user_id=?', (user_id,)).fetchone()[0]
    return max(0, lim - cur)


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
        if _mz_contacts_room(conn, user_id) <= 0:   # A6: limite do plano atingido
            conn.close()
            return redirect('/mandazap/painel?section=contatos&erro=limite_contatos')
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
    conn.execute('DELETE FROM mandazap_list_contacts WHERE contact_id=? AND contact_id IN '
                 '(SELECT id FROM mandazap_contacts WHERE user_id=?)', (cid, user_id))
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
        room  = _mz_contacts_room(conn, user_id)   # A6: limite de contatos do plano
        count = 0; bloqueados = 0
        for c in contacts:
            phone = _re.sub(r'[^\d+]', '', c.get('phone', ''))
            if not phone:
                continue
            # garante DDI 55 para números brasileiros sem prefixo
            if phone.startswith('0'):
                phone = '55' + phone[1:]
            elif len(phone) <= 11 and not phone.startswith('+'):
                phone = '55' + phone
            if count >= room:        # A6: respeita o limite de contatos do plano
                bloqueados += 1
                continue
            conn.execute(
                'INSERT OR IGNORE INTO mandazap_contacts (user_id, name, phone, email, tag, created_at) VALUES (?,?,?,?,?,?)',
                (user_id, c.get('name',''), phone, c.get('email',''), c.get('tag',''), datetime.now().isoformat())
            )
            count += 1
        conn.commit()
        conn.close()
        log.info(f'Importados {count} contatos para user {user_id}' + (f' — {bloqueados} bloqueados (limite do plano)' if bloqueados else ''))
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


def _mz_normalize_phone(phone: str) -> str:
    """Limpa e garante DDI 55 para números brasileiros sem prefixo."""
    phone = _re.sub(r'[^\d+]', '', phone or '')
    if not phone:
        return ''
    if phone.startswith('0'):
        phone = '55' + phone[1:]
    elif len(phone) <= 11 and not phone.startswith('+'):
        phone = '55' + phone
    return phone


@app.route('/mandazap/contatos/delete-bulk', methods=['POST'])
@_mandazap_login_required
def mz_contact_delete_bulk():
    """Apaga vários contatos de uma vez (seleção em massa)."""
    user_id = session['mz_user_id']
    ids = request.form.getlist('ids')
    if not ids:
        ids = (request.get_json(silent=True) or {}).get('ids', [])
    ids = [int(i) for i in ids if str(i).isdigit()]
    if ids:
        conn = get_saas_db()
        ph = ','.join('?' * len(ids))
        owned = [r['id'] for r in conn.execute(
            f'SELECT id FROM mandazap_contacts WHERE user_id=? AND id IN ({ph})',
            [user_id] + ids
        ).fetchall()]
        if owned:
            ph2 = ','.join('?' * len(owned))
            conn.execute(f'DELETE FROM mandazap_list_contacts WHERE contact_id IN ({ph2})', owned)
            conn.execute(f'DELETE FROM mandazap_contacts WHERE id IN ({ph2}) AND user_id=?', owned + [user_id])
            conn.commit()
        conn.close()
        log.info(f'Bulk delete: {len(owned)} contatos do user {user_id}')
    return redirect('/mandazap/painel?section=contatos')


def _mz_extract_contacts_ai(text: str) -> list:
    """Usa a IA (Groq) para extrair pares Nome + Telefone de um texto bruto de PDF."""
    groq_key = os.environ.get('GROQ_API_KEY', '')
    if not groq_key:
        return []
    text = (text or '')[:12000]
    prompt = (
        "Você recebe o texto bruto extraído de um PDF que contém uma lista de pessoas/clientes. "
        "Extraia TODOS os contatos encontrados, cada um com nome e telefone.\n\n"
        "Regras:\n"
        "1. 'phone' deve conter apenas dígitos (sem espaços, parênteses ou traços).\n"
        "2. Mantenha o DDD. Se não houver país, NÃO invente — apenas os dígitos do telefone.\n"
        "3. Se um item tiver telefone mas não nome, use 'Contato' como nome.\n"
        "4. Ignore cabeçalhos, rodapés, totais e linhas sem telefone.\n"
        "5. Não invente contatos que não estejam no texto.\n"
        'Responda APENAS em JSON no formato: {"contacts":[{"name":"...","phone":"..."}]}\n\n'
        f"Texto do PDF:\n{text}"
    )
    try:
        resp = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
            json={
                'model': 'meta-llama/llama-4-scout-17b-16e-instruct',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 4000,
                'temperature': 0.1,
                'response_format': {'type': 'json_object'},
            },
            timeout=40,
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content'].strip()
        data = _json.loads(content)
        out = []
        for c in (data.get('contacts') or []):
            name  = str(c.get('name', '')).strip() or 'Contato'
            phone = _re.sub(r'[^\d]', '', str(c.get('phone', '')))
            if len(phone) >= 8:
                out.append({'name': name[:120], 'phone': phone})
        return out
    except Exception as ex:
        log.error(f'AI pdf extract error: {ex}')
        return []


def _mz_extract_contacts_regex(text: str) -> list:
    """Fallback sem IA: acha telefones por regex e tenta pegar o nome na mesma linha."""
    out = []
    seen = set()
    for line in (text or '').splitlines():
        m = _re.search(r'(\+?\d[\d\s().\-]{7,}\d)', line)
        if not m:
            continue
        phone = _re.sub(r'[^\d]', '', m.group(1))
        if len(phone) < 8 or len(phone) > 13 or phone in seen:
            continue
        # nome = texto antes do telefone, sem dígitos/símbolos
        name = line[:m.start()].strip(' :|-\t')
        name = _re.sub(r'[\d]', '', name).strip(' :|-\t') or 'Contato'
        seen.add(phone)
        out.append({'name': name[:120], 'phone': phone})
    return out


@app.route('/mandazap/contatos/extrair-pdf', methods=['POST'])
@_mandazap_login_required
def mz_contact_extract_pdf():
    """Lê um PDF, extrai o texto e usa IA para detectar Nome + Telefone (preview)."""
    f = request.files.get('pdf_file')
    if not f or not f.filename:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'erro': 'Envie um arquivo PDF'}), 400
    try:
        import pdfplumber
        raw = f.read()
        if len(raw) > 15 * 1024 * 1024:
            return jsonify({'erro': 'PDF muito grande (limite 15MB)'}), 400
        parts = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages[:40]:
                parts.append(page.extract_text() or '')
        full_text = '\n'.join(parts).strip()
    except Exception as ex:
        log.error(f'pdf read error: {ex}')
        return jsonify({'erro': 'Não consegui ler este PDF.'}), 500

    if not full_text:
        return jsonify({'erro': 'PDF sem texto extraível (parece ser digitalizado/imagem). Use um PDF gerado digitalmente.'}), 422

    contacts = _mz_extract_contacts_ai(full_text) or _mz_extract_contacts_regex(full_text)
    if not contacts:
        return jsonify({'erro': 'Não encontrei nome + telefone neste PDF.'}), 422

    # dedup por telefone preservando ordem
    uniq, seen = [], set()
    for c in contacts:
        p = c['phone']
        if p not in seen:
            seen.add(p)
            uniq.append(c)
    return jsonify({'ok': True, 'contacts': uniq[:1000], 'count': len(uniq[:1000])})


@app.route('/mandazap/contatos/import-json', methods=['POST'])
@_mandazap_login_required
def mz_contact_import_json():
    """Importa contatos a partir de uma lista JSON (usado após preview do PDF)."""
    user_id = session['mz_user_id']
    items   = (request.get_json(silent=True) or {}).get('contacts', [])
    if not items:
        return jsonify({'erro': 'Nenhum contato para importar'}), 400
    conn  = get_saas_db()
    room  = _mz_contacts_room(conn, user_id)   # A6: limite de contatos do plano
    count = 0; bloqueados = 0
    for c in items:
        name  = str(c.get('name', '')).strip()[:120] or 'Contato'
        phone = _mz_normalize_phone(str(c.get('phone', '')))
        if not phone:
            continue
        if count >= room:        # A6: respeita o limite do plano
            bloqueados += 1
            continue
        conn.execute(
            'INSERT OR IGNORE INTO mandazap_contacts (user_id, name, phone, email, tag, created_at) VALUES (?,?,?,?,?,?)',
            (user_id, name, phone, '', 'pdf', datetime.now().isoformat())
        )
        count += 1
    conn.commit()
    conn.close()
    log.info(f'Import JSON: {count} contatos para user {user_id}' + (f' — {bloqueados} bloqueados (limite do plano)' if bloqueados else ''))
    return jsonify({'ok': True, 'count': count, 'bloqueados': bloqueados})


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
    conn.execute('DELETE FROM mandazap_list_contacts WHERE list_id=? AND list_id IN '
                 '(SELECT id FROM mandazap_lists WHERE user_id=?)', (lid, user_id))
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


@app.route('/mandazap/numeros/<int:nid>/aquecido', methods=['POST'])
@_mandazap_login_required
def mz_number_prewarm(nid):
    """Marca/desmarca o número como JÁ AQUECIDO (chip antigo/usado) — pula a curva de
    warm-up e vai direto ao teto cheio do plano. Use só em número realmente velho."""
    user_id = session['mz_user_id']
    val     = 1 if request.form.get('prewarmed') == '1' else 0
    conn    = get_saas_db()
    conn.execute('UPDATE mandazap_numbers SET prewarmed=? WHERE id=? AND user_id=?', (val, nid, user_id))
    conn.commit(); conn.close()
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
        "Você é um especialista em marketing via WhatsApp e anti-bloqueio. "
        "Transforme a mensagem abaixo adicionando variações no formato {opção1|opção2|opção3} "
        "para tornar cada envio único e evitar bloqueio.\n\n"
        "Regras obrigatórias:\n"
        "1. Adicione {variações} em: saudações, adjetivos, verbos de ação, conectivos e CTAs.\n"
        "2. Varie TAMBÉM emojis quando houver, ex: {🛵|🛴|⚡} ou {😊|😄|🙂}.\n"
        "3. Mantenha {nome} onde já existir ou adicione no início.\n"
        "4. URLs permanecem EXATAMENTE iguais — nunca altere nem coloque variação em links.\n"
        "5. NUNCA varie dados FACTUAIS nem números: preços, parcelas (ex: 72x), prazos, "
        "garantias, datas, horários, endereço, telefone, CEP, %. Eles são FIXOS e idênticos.\n"
        "6. NUNCA troque por sinônimos que mudem o SENTIDO ou façam afirmação diferente "
        "(ex: não troque 'revendedor' por 'autorizado/oficial' se não for o mesmo significado).\n"
        "7. As variações devem ser sinônimos naturais e informais do Brasil, todas com o MESMO sentido.\n"
        "8. Evite saudação dupla (não gere 'Oi, Olá' junto) — escolha UMA saudação variável.\n"
        "9. Adicione pelo menos 8 variações espalhadas pelo texto.\n"
        "10. Retorne APENAS a mensagem transformada, sem explicações, sem prefixos.\n\n"
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
    multi_number = 1 if request.form.get('multi_number') else 0
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
        (user_id, name, message, media_type, media_url, list_id, number_id, multi_number, status, total, sent, scheduled_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,0,NULL,?)
    ''', (user_id, name, message, media_type, media_url, list_id, number_id, multi_number,
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
            (user_id, name, message, media_type, media_url, list_id, number_id, multi_number, status, total, sent, created_at)
            VALUES (?,?,?,?,?,?,?,?,'rascunho',?,0,?)
        ''', (user_id, f"Cópia — {c['name']}", c['message'],
              c['media_type'], c['media_url'] or '', c['list_id'], c['number_id'],
              (c['multi_number'] if 'multi_number' in c.keys() else 0),
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

DEV_TOKEN = os.environ.get('DEV_TOKEN', '')  # sem default adivinhável (auth real é via _dev_token_ok, fail-closed)

@app.route('/dev/<token>')
def dev_page(token):
    if not _dev_token_ok(token):
        abort(404)
    notas = listar_notas_dev()
    return render_template('dev.html', notas=notas, now=datetime.now(), token=token)

@app.route('/dev/<token>/nota', methods=['POST'])
def dev_nota(token):
    if not _dev_token_ok(token):
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


def _mz_phone_key(p: str) -> str:
    """Chave canônica p/ casar número mesmo com o 9º dígito do celular BR variando.
    O WhatsApp às vezes registra o número SEM o 9 (ex.: 554797766831), mesmo você
    enviando COM o 9 (5547997766831). Sem isso, a validação descartava ~80% da lista.
    Ex.: '5547997766831' e '554797766831' -> '4797766831'.
    """
    d = _re.sub(r'\D', '', p or '')
    if d.startswith('55') and len(d) >= 12:
        d = d[2:]                  # remove o DDI 55
    if len(d) == 11 and d[2] == '9':
        d = d[:2] + d[3:]          # remove o 9 do celular -> DDD + 8 dígitos
    return d


def _validate_numbers_batch(evo_url: str, evo_key: str, instance: str,
                            phones: list, batch_size: int = 50) -> set:
    """Verifica em lote quais números têm WhatsApp ativo via Evolution API.
    Retorna um set() de CHAVES CANÔNICAS válidas (tolerante ao 9º dígito BR).
    Phones que a API não conseguiu verificar entram no set (safe default = tenta enviar).
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
                            num = jid.split('@')[0] if '@' in jid else jid
                            if num:
                                valid.add(_mz_phone_key(num))   # ← casa pelo 9º dígito
                else:
                    # Formato inesperado — inclui todos (fail-open)
                    valid.update(_mz_phone_key(p) for p in chunk)
            else:
                log.warning(f"whatsappNumbers batch error HTTP {r.status_code} — fail-open para {len(chunk)} phones")
                valid.update(_mz_phone_key(p) for p in chunk)
        except Exception as e:
            log.warning(f"whatsappNumbers batch exception: {e} — fail-open para {len(chunk)} phones")
            valid.update(_mz_phone_key(p) for p in chunk)
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


def _mz_uniquify_urls(text: str) -> str:
    """Faz cada URL da mensagem sair ÚNICA, anexando um token aleatório.
    Link idêntico enviado em massa é o MAIOR gatilho de ban do WhatsApp —
    com um ?r=token diferente por mensagem, o fingerprint de URL repetida some.
    Os domínios próprios ignoram o parâmetro, então a página abre normal.
    """
    alfa = 'abcdefghijklmnopqrstuvwxyz0123456789'
    def repl(m):
        url = m.group(0)
        # devolve a pontuação final que o regex possa ter capturado
        trail = ''
        while url and url[-1] in '.,;:!?)]}':
            trail = url[-1] + trail
            url = url[:-1]
        token = ''.join(random.choices(alfa, k=6))
        sep = '&' if '?' in url else '?'
        return f"{url}{sep}r={token}{trail}"
    return _re.sub(r'https?://[^\s]+', repl, text)


def _mz_personalize(message: str, contact: dict) -> str:
    """Aplica variáveis do contato + spintax + URLs únicas. Cada msg sai diferente."""
    nome_curto    = (contact.get('name') or 'Cliente').split()[0].title()
    nome_completo = (contact.get('name') or 'Cliente').title()
    msg = (message
           .replace('{nome}', nome_curto)
           .replace('{name}', nome_curto)
           .replace('{nome_completo}', nome_completo))
    msg = _apply_spintax(msg)        # variações {a|b|c}
    msg = _mz_uniquify_urls(msg)     # URLs únicas por mensagem (anti-fingerprint)
    return msg


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
    Delay humanizado anti-ban v4 — LOTES COM PAUSA LONGA (pacing por número).

    `sent_count` = nº de envios DESTE número na sessão.

    - A cada lote de MZ_BATCH_SIZE envios (padrão 30): descansa 40–75 min
      (como uma pessoa que manda um bloco e larga o celular).
    - A cada 12 envios: micro-pausa de 2–5 min (olhou outra coisa).
    - Entre mensagens: intervalo aleatório que começa devagar (número frio)
      e acelera conforme aquece. Nunca fixo — o Meta detecta padrão matemático.
    """
    # Pausa LONGA entre lotes — o que mais imita comportamento humano real
    if sent_count > 0 and sent_count % MZ_BATCH_SIZE == 0:
        pausa = random.uniform(2400, 4500)   # 40–75 min de descanso entre lotes
        log.info(f"Anti-ban: descanso de lote {pausa/60:.0f}min apos {sent_count} envios")
        time.sleep(pausa)
        return

    # Micro-pausa periódica (simula distração)
    if sent_count > 0 and sent_count % 12 == 0:
        pausa = random.uniform(120, 300)     # 2–5 min
        log.info(f"Anti-ban: micro-pausa {pausa:.0f}s apos {sent_count} envios")
        time.sleep(pausa)
        return

    # Intervalo entre mensagens, mais lento enquanto o número ainda está frio
    if sent_count < 20:
        base = random.uniform(45, 100)   # número frio: bem devagar
    elif sent_count < 80:
        base = random.uniform(25, 60)    # esquentando
    else:
        base = random.uniform(18, 45)    # já no ritmo

    jitter = base * random.uniform(0.8, 1.35)   # jitter assimétrico
    log.debug(f"Anti-ban delay: {jitter:.1f}s (sent={sent_count})")
    time.sleep(jitter)


# ── Anti-ban v4: aquecimento, limite por número e janela de horário ───────────

# Curva de aquecimento (warm-up): teto de msgs/dia conforme a IDADE do número.
# Número novo manda pouco e cresce gradualmente até ~21 dias (depois só o plano limita).
# É a defesa nº1 contra ban de número novo.
MZ_WARMUP_CURVE = [
    (0,   15),       # primeiras 24h — bem devagar
    (1,   25),
    (2,   35),
    (3,   45),
    (5,   60),
    (7,   80),
    (10,  100),
    (14,  140),
    (18,  200),
    (25,  10**9),    # 25+ dias: número maduro, sem teto de warm-up (só o plano limita)
]

# Janela de horário "humano" (hora local do servidor). Fora disso, pausa e retoma sozinho.
MZ_SEND_HOUR_START = int(os.environ.get('MZ_SEND_HOUR_START', '8'))
MZ_SEND_HOUR_END   = int(os.environ.get('MZ_SEND_HOUR_END', '21'))

# Só dias úteis (seg-sex): negócio de verdade não dispara fim de semana — mais humano,
# menos ban. Padrão LIGADO. Desligue (=0) p/ permitir sábado/domingo (varejo, etc).
MZ_SEND_WEEKDAYS_ONLY = os.environ.get('MZ_SEND_WEEKDAYS_ONLY', '1') not in ('0', 'false', 'False', '')

# Cooldown pós-ban: ao detectar ban, pausa TODOS os números do usuário por N horas
# (o conteúdo/lista que banou um número vai banar os outros — circuit breaker).
MZ_BAN_COOLDOWN_HOURS = float(os.environ.get('MZ_BAN_COOLDOWN_HOURS', '6'))

# Lotes: envia em blocos e descansa bastante entre eles (mais humano, menos ban).
MZ_BATCH_SIZE = int(os.environ.get('MZ_BATCH_SIZE', '30'))

# Teto RÍGIDO de envios/dia POR número — proteção anti-ban acima do warm-up e do plano.
# Mesmo número maduro em plano grande nunca passa disso (evita o "≈9999/dia" do Agência,
# que é suicídio). A faixa segura de número aquecido é 80–200/dia; 150 é o ponto conservador.
MZ_DAILY_HARD_CAP = int(os.environ.get('MZ_DAILY_HARD_CAP', '150'))

# R1 anti-ban: a pré-validação em massa (/chat/whatsappNumbers) é um gatilho de ban,
# sobretudo em chip novo. Só pré-valida quando o número mais novo do pool já estiver
# maduro (>= dias abaixo). MZ_PREVALIDATE=0 desliga a pré-validação de vez.
MZ_PREVALIDATE         = os.environ.get('MZ_PREVALIDATE', '1') not in ('0', 'false', 'False', '')
MZ_PREVALIDATE_MIN_AGE = int(os.environ.get('MZ_PREVALIDATE_MIN_AGE', '7'))

# Imagem só sai de número AQUECIDO (>= dias). Chip novo mandando foto é gatilho forte
# de ban — abaixo disso a campanha manda só o texto (legenda).
MZ_IMAGE_MIN_AGE = int(os.environ.get('MZ_IMAGE_MIN_AGE', '14'))


def _mz_warmup_cap(days_active: int) -> int:
    """Teto diário de mensagens conforme a idade do número (curva de aquecimento)."""
    cap = MZ_WARMUP_CURVE[0][1]
    for d, c in MZ_WARMUP_CURVE:
        if days_active >= d:
            cap = c
    return cap


def _mz_number_age_days(num_row: dict) -> int:
    # Dono marcou como "já aquecido" (chip antigo/usado) → pula a curva de warm-up
    if num_row.get('prewarmed'):
        return 999
    ref = (num_row.get('warmup_start') or num_row.get('created_at') or '')
    try:
        return max(0, (datetime.now() - datetime.fromisoformat(ref)).days)
    except Exception:
        return 999  # sem data confiável → trata como número maduro


def _mz_number_sent_today(conn, number_id: int) -> int:
    today = datetime.now().strftime('%Y-%m-%d')
    row = conn.execute(
        'SELECT sent FROM mandazap_number_daily WHERE number_id=? AND day=?',
        (number_id, today)
    ).fetchone()
    return (row['sent'] if row else 0)


def _mz_inc_number_sent(number_id: int, n: int = 1):
    """Incrementa o contador diário de envios DESTE número (conexão própria)."""
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        c = get_saas_db()
        c.execute(
            'INSERT INTO mandazap_number_daily (number_id, day, sent) VALUES (?,?,?) '
            'ON CONFLICT(number_id, day) DO UPDATE SET sent = sent + ?',
            (number_id, today, n, n)
        )
        c.commit(); c.close()
    except Exception as e:
        log.warning(f"number_daily inc error: {e}")


def _mz_inc_number_replies(number_id: int, n: int = 1):
    """Incrementa o contador diário de RESPOSTAS recebidas deste número (via webhook).
    reply-ratio = replies/sent é o sinal nº1 de saúde anti-ban (número que conversa não bana).
    """
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        c = get_saas_db()
        c.execute(
            'INSERT INTO mandazap_number_daily (number_id, day, sent, replies) VALUES (?,?,0,?) '
            'ON CONFLICT(number_id, day) DO UPDATE SET replies = replies + ?',
            (number_id, today, n, n)
        )
        c.commit(); c.close()
    except Exception as e:
        log.warning(f"number_daily replies inc error: {e}")


# ── Fase 3: saúde do número via reply-ratio ───────────────────────────────────
# Limiares (pesquisa): >20% resposta = saudável; 8-20% = atenção; <8% com volume = risco de ban.
MZ_HEALTH_MIN_VOL    = int(os.environ.get('MZ_HEALTH_MIN_VOL', '40'))      # envios mínimos na janela p/ avaliar
MZ_HEALTH_GREEN      = float(os.environ.get('MZ_HEALTH_GREEN', '0.20'))    # >=20% = verde
MZ_HEALTH_YELLOW     = float(os.environ.get('MZ_HEALTH_YELLOW', '0.08'))   # >=8% = amarelo; abaixo = vermelho
MZ_HEALTH_WINDOW     = int(os.environ.get('MZ_HEALTH_WINDOW_DAYS', '7'))   # janela de avaliação (dias)
MZ_AUTOBRAKE_ENABLED = os.environ.get('MZ_AUTOBRAKE_ENABLED', '1') not in ('0', 'false', 'False', '')
MZ_AUTOBRAKE_FLOOR   = int(os.environ.get('MZ_AUTOBRAKE_FLOOR', '15'))     # teto de um número "vermelho"


def _mz_number_health(conn, number_id: int, days: int = None) -> dict:
    """Saúde do número na janela: soma sent/replies, calcula reply-ratio e status.
    status: 'sem_dados' (volume baixo), 'verde', 'amarelo', 'vermelho'.
    """
    days  = days or MZ_HEALTH_WINDOW
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    row = conn.execute(
        'SELECT COALESCE(SUM(sent),0) AS s, COALESCE(SUM(replies),0) AS r '
        'FROM mandazap_number_daily WHERE number_id=? AND day>=?',
        (number_id, since)
    ).fetchone()
    sent  = (row['s'] if row else 0) or 0
    repl  = (row['r'] if row else 0) or 0
    ratio = (repl / sent) if sent > 0 else None
    if sent < MZ_HEALTH_MIN_VOL:
        status = 'sem_dados'
    elif ratio is not None and ratio >= MZ_HEALTH_GREEN:
        status = 'verde'
    elif ratio is not None and ratio >= MZ_HEALTH_YELLOW:
        status = 'amarelo'
    else:
        status = 'vermelho'
    return {'sent': sent, 'replies': repl, 'ratio': ratio,
            'ratio_pct': (min(100, round(ratio * 100)) if ratio is not None else None),
            'status': status}


def _mz_user_has_engagement(conn, user_id: int, days: int = None) -> bool:
    """True se ALGUM número do usuário recebeu resposta na janela — prova que o webhook
    está entregando. Evita auto-freio falso quando o webhook ainda não foi configurado.
    """
    days  = days or MZ_HEALTH_WINDOW
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    row = conn.execute(
        'SELECT COALESCE(SUM(d.replies),0) AS r FROM mandazap_number_daily d '
        'JOIN mandazap_numbers n ON n.id = d.number_id '
        'WHERE n.user_id=? AND d.day>=?',
        (user_id, since)
    ).fetchone()
    return bool(row and (row['r'] or 0) > 0)


def _mz_effective_daily_cap(num_row: dict, plan_daily: int, plan_safe_cap: int = None) -> int:
    """Teto diário efetivo deste número = o MENOR entre warm-up, cota bruta do plano,
    o teto conservador do plano (daily_safe_cap) e o teto rígido global.
    É o que de fato limita o envio do dia (e o que o painel mostra como 'hoje X/cap').
    """
    caps = [_mz_warmup_cap(_mz_number_age_days(num_row)), plan_daily, MZ_DAILY_HARD_CAP]
    if plan_safe_cap:
        caps.append(plan_safe_cap)
    return min(caps)


def _mz_number_remaining(conn, num_row: dict, plan_daily: int, plan_safe_cap: int = None) -> int:
    """Quantas msgs este número ainda pode enviar HOJE (mínimo entre warm-up, plano,
    teto conservador do plano e teto rígido)."""
    cap_day = _mz_effective_daily_cap(num_row, plan_daily, plan_safe_cap)
    return max(0, cap_day - _mz_number_sent_today(conn, num_row['id']))


def _mz_in_send_window(now=None) -> bool:
    """True se o momento atual está dentro da janela de envio humano (horário + dia útil)."""
    now = now or datetime.now()
    # Dias úteis: sáb (5) e dom (6) fecham a janela — campanha pausa e retoma na segunda.
    if MZ_SEND_WEEKDAYS_ONLY and now.weekday() >= 5:
        return False
    if MZ_SEND_HOUR_START == MZ_SEND_HOUR_END:
        return True  # 24h
    h = now.hour
    if MZ_SEND_HOUR_START < MZ_SEND_HOUR_END:
        return MZ_SEND_HOUR_START <= h < MZ_SEND_HOUR_END
    return h >= MZ_SEND_HOUR_START or h < MZ_SEND_HOUR_END  # janela que cruza meia-noite


def _mz_in_cooldown(num_row: dict) -> bool:
    """True se o número está em cooldown de segurança pós-ban."""
    cu = (num_row.get('cooldown_until') or '')
    if not cu:
        return False
    try:
        return datetime.fromisoformat(cu) > datetime.now()
    except Exception:
        return False


def _mz_set_cooldown(user_id: int, hours: float = None):
    """Circuit breaker: pausa TODOS os números do usuário por N horas após um ban.
    O conteúdo/lista que banou um número provavelmente banaria os outros também.
    """
    hours = MZ_BAN_COOLDOWN_HOURS if hours is None else hours
    until = (datetime.now() + timedelta(hours=hours)).isoformat()
    try:
        c = get_saas_db()
        c.execute('UPDATE mandazap_numbers SET cooldown_until=? WHERE user_id=?', (until, user_id))
        c.commit(); c.close()
        log.warning(f"[MZ Cooldown] user {user_id}: TODOS os números pausados por {hours}h (até {until[:16]})")
    except Exception as e:
        log.error(f"set cooldown error: {e}")


def _mz_campaign_scheduler():
    """Daemon: retoma campanhas 'agendada' quando entra a janela de horário.
    Também dispara campanhas com scheduled_at já vencido. Sobrevive a restart do app.
    """
    time.sleep(90)  # espera o app subir por completo
    while True:
        try:
            if _mz_in_send_window():
                conn = get_saas_db()
                rows = [dict(r) for r in conn.execute(
                    "SELECT id, user_id, scheduled_at, updated_at FROM mandazap_campaigns WHERE status='agendada'"
                ).fetchall()]
                conn.close()
                now = datetime.now()
                for r in rows:
                    # Respeita agendamento futuro (scheduled_at)
                    sched = r.get('scheduled_at')
                    if sched:
                        try:
                            if datetime.fromisoformat(sched) > now:
                                continue
                        except Exception:
                            pass
                    log.info(f"[MZ Scheduler] retomando campanha agendada {r['id']}")
                    threading.Thread(
                        target=_dispatch_campaign,
                        args=(r['id'], r['user_id']),
                        kwargs={'continuar': True}, daemon=True
                    ).start()
                    time.sleep(8)  # espaça os disparos entre campanhas
        except Exception as e:
            log.error(f"[MZ Scheduler] erro: {e}")
        time.sleep(300)  # checa a cada 5 min


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

    # Claim ATÔMICO contra disparo duplicado: só UMA thread consegue marcar 'enviando'.
    # (Antes era ler-depois-checar, NÃO atômico → o botão "Disparar" + o agendador podiam
    #  rodar a MESMA campanha 2x em paralelo, duplicando mensagens = gatilho de ban.)
    claim = conn.execute(
        "UPDATE mandazap_campaigns SET status='enviando', updated_at=? WHERE id=? AND user_id=? AND status != 'enviando'",
        (datetime.now().isoformat(), cid, user_id)
    )
    conn.commit()
    if claim.rowcount == 0:
        conn.close()
        log.warning(f"Campanha {cid}: já está sendo enviada (claim atômico evitou disparo duplicado)")
        return

    # Plano do usuário
    plan_key  = conn.execute('SELECT plan FROM mandazap_users WHERE id=?', (user_id,)).fetchone()
    plan_key  = (plan_key['plan'] if plan_key else 'solo')
    plan_info = MANDAZAP_PLANS.get(plan_key, MANDAZAP_PLANS['solo'])
    daily_lim = plan_info.get('daily_limit', 399)
    # Cota POR número = limite do plano dividido pela qtd de números (~399/número)
    per_num_plan = max(1, daily_lim // max(1, plan_info.get('numbers', 1)))

    # ── POOL de números (multi-número é OPT-IN via camp.multi_number) ───────
    multi = bool(camp.get('multi_number'))
    if multi:
        num_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM mandazap_numbers WHERE user_id=? AND status='connected' ORDER BY created_at",
            (user_id,)
        ).fetchall()]
        if not num_rows:
            one = conn.execute('SELECT * FROM mandazap_numbers WHERE id=? AND user_id=?',
                               (camp.get('number_id'), user_id)).fetchone()
            num_rows = [dict(one)] if one else []
    else:
        one = conn.execute('SELECT * FROM mandazap_numbers WHERE id=? AND user_id=?',
                           (camp.get('number_id'), user_id)).fetchone()
        num_rows = [dict(one)] if one else []

    if not num_rows:
        conn.execute("UPDATE mandazap_campaigns SET status='erro',error_log=? WHERE id=?",
                     ('Nenhum número WhatsApp selecionado/conectado na campanha.', cid))
        conn.commit(); conn.close()
        log.error(f"Campanha {cid}: nenhum número")
        return

    senders = [{
        'id': nr['id'],
        'instance': f"mz{user_id}n{nr['id']}",
        'row': nr,
        'cap': _mz_effective_daily_cap(nr, per_num_plan, plan_info.get('daily_safe_cap')),  # C2: teto real do dia
        'remaining': _mz_number_remaining(conn, nr, per_num_plan, plan_info.get('daily_safe_cap')),
        'sent': 0,
    } for nr in num_rows]
    instance = senders[0]['instance']  # usado na pré-validação de números

    # ── Fase 3: AUTO-FREIO por reply-ratio ──────────────────────────────────
    # Só freia se o webhook está entregando respostas (usuário tem engajamento) — assim
    # nunca freia por engano quando o webhook ainda não foi configurado. Número "vermelho"
    # (muito envio, quase nenhuma resposta = lista fria) tem o teto cortado p/ não queimar.
    if MZ_AUTOBRAKE_ENABLED and _mz_user_has_engagement(conn, user_id):
        for s in senders:
            h = _mz_number_health(conn, s['id'])
            if h['status'] == 'vermelho' and s['remaining'] > MZ_AUTOBRAKE_FLOOR:
                log.warning(f"[MZ AutoFreio] campanha {cid}: número {s['id']} vermelho "
                            f"({h['ratio_pct']}% resposta em {h['sent']} envios) — teto cortado "
                            f"de {s['remaining']} p/ {MZ_AUTOBRAKE_FLOOR}")
                s['remaining'] = MZ_AUTOBRAKE_FLOOR

    # ── Janela de horário humano: fora dela, agenda p/ retomar sozinho ──────
    if not _mz_in_send_window():
        conn.execute(
            "UPDATE mandazap_campaigns SET status='agendada', error_log=? WHERE id=?",
            (f'⏰ Fora do horário de envio ({MZ_SEND_HOUR_START}h–{MZ_SEND_HOUR_END}h{", dias úteis" if MZ_SEND_WEEKDAYS_ONLY else ""}). '
             f'Retoma automaticamente quando abrir.', cid)
        )
        conn.commit(); conn.close()
        log.info(f"Campanha {cid}: fora da janela de horário — agendada")
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

    # Randomiza ordem dos contatos — evita padrão previsível e fingerprint de sequência
    # (a capacidade do dia é aplicada após a pré-validação dos números)
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

    # Verifica conexão de cada número do pool — descarta os desconectados
    senders = [s for s in senders if _check_instance_connected(evo_url, evo_key, s['instance'])]
    if not senders:
        log.error(f"Campanha {cid}: nenhum número conectado — abortando")
        conn.execute(
            "UPDATE mandazap_campaigns SET status='erro', error_log=? WHERE id=?",
            ('Número WhatsApp desconectado. Reconecte no painel Números antes de disparar.', cid)
        )
        conn.commit(); conn.close()
        return

    # Remove números em cooldown de segurança pós-ban (circuit breaker)
    senders = [s for s in senders if not _mz_in_cooldown(s['row'])]
    if not senders:
        conn.execute(
            "UPDATE mandazap_campaigns SET status='agendada', error_log=? WHERE id=?",
            ('Números em cooldown de segurança pós-ban. Retoma automaticamente quando o '
             'período de proteção passar.', cid)
        )
        conn.commit(); conn.close()
        log.info(f"Campanha {cid}: todos os números em cooldown pós-ban — agendada")
        return

    # Remove números que já bateram o teto diário / aquecimento de hoje
    senders = [s for s in senders if s['remaining'] > 0]
    if not senders:
        conn.execute(
            "UPDATE mandazap_campaigns SET status='agendada', error_log=? WHERE id=?",
            ('Limite diário/aquecimento dos números atingido. Retoma amanhã automaticamente.', cid)
        )
        conn.commit(); conn.close()
        log.info(f"Campanha {cid}: sem capacidade hoje (warm-up/limite) — agendada p/ amanhã")
        return

    total_cap = sum(s['remaining'] for s in senders)
    instance  = senders[0]['instance']  # número conectado p/ a pré-validação em lote
    log.info(f"Campanha {cid}: pool de {len(senders)} número(s), capacidade hoje={total_cap} (multi={multi})")

    # ── Pré-validação de números (R1: só com número MADURO) ─────────────────
    # Checar centenas de números no /chat/whatsappNumbers ANTES de enviar é um GATILHO
    # de ban (Evolution #2228), principalmente com CHIP NOVO. Então só pré-validamos
    # quando o número mais novo do pool já está maduro (>= MZ_PREVALIDATE_MIN_AGE dias)
    # e a feature está ligada. Senão, PULA: inválidos são filtrados INLINE no envio
    # (_is_invalid_number → 'exists:false' já é tratado como pular, não como ban).
    def _norm(p):
        p = (p or '').replace(' ','').replace('-','').replace('+','').replace('(','').replace(')','')
        return ('55' + p) if p and not p.startswith('55') else p

    _youngest_age   = min((_mz_number_age_days(s['row']) for s in senders), default=999)
    _do_prevalidate = MZ_PREVALIDATE and _youngest_age >= MZ_PREVALIDATE_MIN_AGE
    invalid_count   = 0
    if _do_prevalidate:
        raw_phones = [_norm(c.get('phone','')) for c in contacts if c.get('phone')]
        raw_phones = [p for p in raw_phones if p]
        log.info(f"Campanha {cid}: pré-validando {len(raw_phones)} números no WhatsApp...")
        conn.execute("UPDATE mandazap_campaigns SET error_log=? WHERE id=?",
                     (f'Validando {len(raw_phones)} números... aguarde.', cid))
        conn.commit()
        valid_keys     = _validate_numbers_batch(evo_url, evo_key, instance, raw_phones)
        contacts_valid = [c for c in contacts if _mz_phone_key(_norm(c.get('phone',''))) in valid_keys]
        # Rede de segurança: se derrubou >50% de lista grande, usa a lista completa.
        if len(contacts) >= 20 and len(contacts_valid) < 0.5 * len(contacts):
            log.warning(f"Campanha {cid}: validação suspeita ({len(contacts_valid)}/{len(contacts)} válidos) "
                        f"— usando a lista completa (provável falha de validação)")
        else:
            invalid_count = len(contacts) - len(contacts_valid)
            contacts = contacts_valid
        log.info(f"Campanha {cid}: {len(contacts)} na fila, {invalid_count} sem WhatsApp removidos")
    else:
        log.info(f"Campanha {cid}: pré-validação PULADA (nº mais novo={_youngest_age}d / feature off) "
                 f"— inválidos filtrados no envio. {len(contacts)} na fila.")

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

    # Aplica a capacidade do dia: o excedente fica p/ retomar amanhã
    capped = len(contacts) > total_cap
    if capped:
        log.info(f"Campanha {cid}: capacidade {total_cap} < {len(contacts)} restantes — "
                 f"envia {total_cap} hoje, resto amanhã")
        contacts = contacts[:total_cap]

    # ── C2: não segurar a conexão do banco aberta durante o loop ──
    # O loop tem pausas de até ~75min e a campanha roda horas; o saas.db é compartilhado
    # por TODOS os SaaS. Fechamos a conexão principal e usamos conexões CURTAS (helper)
    # só nas saídas/finalização — evita WAL inchado e contenção.
    conn.close()
    def _camp_set(sql, params):
        c = get_saas_db(); c.execute(sql, params); c.commit(); c.close()

    rr = 0  # ponteiro de round-robin entre os números do pool

    for c in contacts:
        # Verifica se campanha foi cancelada externamente
        chk = get_saas_db()
        st  = chk.execute('SELECT status FROM mandazap_campaigns WHERE id=?', (cid,)).fetchone()
        chk.close()
        if st and st['status'] == 'cancelada':
            log.info(f"Campanha {cid} cancelada pelo usuário em {sent_count}/{total}")
            _camp_set(
                "UPDATE mandazap_campaigns SET status='cancelada', sent=?, finished_at=?, error_log=? WHERE id=?",
                (sent_count, datetime.now().isoformat(), f'Cancelada pelo usuário. {sent_count} enviados.', cid)
            )
            return

        # Janela de horário: se fechou no meio, pausa e agenda retomada
        if not _mz_in_send_window():
            _camp_set(
                "UPDATE mandazap_campaigns SET status='agendada', sent=?, error_log=? WHERE id=?",
                (sent_count,
                 f'⏰ Pausada fora do horário ({MZ_SEND_HOUR_START}h–{MZ_SEND_HOUR_END}h{", dias úteis" if MZ_SEND_WEEKDAYS_ONLY else ""}). '
                 f'{sent_count} enviados. Retoma automaticamente.', cid)
            )
            log.info(f"Campanha {cid}: janela fechou — pausada em {sent_count}")
            return

        # Seleciona o próximo número do pool com capacidade (round-robin)
        active = [s for s in senders if s['remaining'] > 0]
        if not active:
            break  # capacidade esgotada no meio → finaliza (resto fica p/ amanhã)
        sender   = active[rr % len(active)]
        rr      += 1
        instance = sender['instance']

        phone = (c.get('phone') or '').replace(' ','').replace('-','').replace('+','').replace('(','').replace(')','')
        if not phone:
            continue
        if not phone.startswith('55'):
            phone = '55' + phone

        # Personaliza + spintax + URLs únicas — cada mensagem sai diferente
        msg = _mz_personalize(message, c)

        # Segurança imagem: foto só sai de número AQUECIDO (>= MZ_IMAGE_MIN_AGE dias).
        # Chip novo manda só o TEXTO (legenda) — imagem em chip novo é gatilho forte de ban.
        if is_image and _mz_number_age_days(sender['row']) >= MZ_IMAGE_MIN_AGE:
            ok, err, invalido = _send_image(evo_url, evo_key, instance, phone, media_url, msg)
        else:
            ok, err, invalido = _send_text(evo_url, evo_key, instance, phone, msg)

        if ok:
            sent_count          += 1
            consec_fails         = 0
            sender['sent']      += 1
            sender['remaining'] -= 1
            _mz_inc_number_sent(sender['id'])  # contador diário deste número
            # C2: a cada 10 envios, re-sincroniza o teto real do banco. Protege contra DUAS
            # campanhas do mesmo usuário no mesmo número (cada uma tinha o snapshot inteiro →
            # mandaria 2× o cap = ban). Só APERTA o remaining; nunca aumenta além do cap.
            if sent_count % 10 == 0:
                try:
                    _rc = get_saas_db()
                    for _s in senders:
                        _s['remaining'] = min(_s['remaining'], max(0, _s['cap'] - _mz_number_sent_today(_rc, _s['id'])))
                    _rc.close()
                except Exception as _re_sync:
                    log.warning(f"C2 re-sync error: {_re_sync}")
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
                # CIRCUIT BREAKER: um número caiu (provável ban). O conteúdo/lista que
                # banou este vai banar os outros — então PARA tudo e poe todos em cooldown.
                log.error(f"Campanha {cid}: número {sender['id']} desconectou (ban?) — circuit breaker — {err}")
                _mz_set_cooldown(user_id)
                _camp_set(
                    "UPDATE mandazap_campaigns SET status='erro', sent=?, finished_at=?, error_log=? WHERE id=?",
                    (sent_count, datetime.now().isoformat(),
                     f'🛑 Possível ban detectado após {sent_count} envios. Por segurança, TODOS os seus '
                     f'números foram pausados por {MZ_BAN_COOLDOWN_HOURS:.0f}h. Reconecte o número banido e '
                     f'revise a lista/mensagem antes de retomar.', cid)
                )
                return

            else:
                # Falha real (API down, timeout, erro temporario) — conta consecutiva
                consec_fails += 1
                if consec_fails >= MAX_CONSEC:
                    # Verifica se é ban ou problema de API
                    is_ban = not _check_instance_connected(evo_url, evo_key, instance)
                    if is_ban:
                        _mz_set_cooldown(user_id)   # circuit breaker — protege os outros números
                        motivo = (f'🛑 Possível ban — instância desconectada. TODOS os seus números foram '
                                  f'pausados por {MZ_BAN_COOLDOWN_HOURS:.0f}h por segurança. Reconecte e revise a lista/mensagem.')
                    else:
                        motivo = f'API retornou {MAX_CONSEC} erros consecutivos. Verifique a conexão e tente novamente.'
                    log.error(f"Campanha {cid}: {MAX_CONSEC} falhas consecutivas ({'ban?' if is_ban else 'API error'}) — {err}")
                    _camp_set(
                        "UPDATE mandazap_campaigns SET status='erro', sent=?, finished_at=?, error_log=? WHERE id=?",
                        (sent_count, datetime.now().isoformat(),
                         f'{motivo} | {first_err}', cid)
                    )
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

        # A cada 25 envios DESTE número, confere se ele ainda está conectado
        if ok and sender['sent'] > 0 and sender['sent'] % 25 == 0:
            if not _check_instance_connected(evo_url, evo_key, sender['instance']):
                # CIRCUIT BREAKER: ban detectado na checagem periódica — para tudo e cooldown
                log.error(f"Campanha {cid}: número {sender['id']} desconectou após {sender['sent']} envios — circuit breaker")
                _mz_set_cooldown(user_id)
                _camp_set(
                    "UPDATE mandazap_campaigns SET status='erro', sent=?, finished_at=?, error_log=? WHERE id=?",
                    (sent_count, datetime.now().isoformat(),
                     f'🛑 Possível ban detectado após {sent_count} envios. Por segurança, TODOS os seus '
                     f'números foram pausados por {MZ_BAN_COOLDOWN_HOURS:.0f}h. Reconecte e revise a '
                     f'lista/mensagem antes de retomar.', cid)
                )
                return

        # Delay anti-ban humanizado — baseado no contador DESTE número (pacing por número)
        if ok:
            _antiban_delay(sender['sent'])

    # Finaliza — se sobrou fila por causa do teto diário, agenda p/ amanhã
    if capped:
        _camp_set(
            "UPDATE mandazap_campaigns SET status='agendada', sent=?, error_log=? WHERE id=?",
            (sent_count,
             f'Limite diário/aquecimento atingido. {sent_count} enviados hoje. '
             f'O restante é enviado automaticamente amanhã.', cid)
        )
        log.info(f"Campanha {cid}: parcial {sent_count} (teto diário) — agendada p/ amanhã")
        return
    error_log = f"{failed_count} falhas. {first_err}" if failed_count else ''
    _camp_set(
        "UPDATE mandazap_campaigns SET status='concluida', sent=?, finished_at=?, error_log=? WHERE id=?",
        (sent_count, datetime.now().isoformat(), error_log, cid)
    )
    log.info(f"Campanha {cid} concluída: {sent_count} enviados")


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
    listar_usuarios_picker as desp_listar_usuarios_picker,
    atualizar_foto_usuario as desp_atualizar_foto_usuario,
    toggle_usuario as desp_toggle_usuario,
    atualizar_senha_usuario as desp_atualizar_senha_usuario,
    deletar_usuario as desp_deletar_usuario,
    registrar_ultimo_login as desp_reg_login,
    contar_usuarios as desp_contar_usuarios,
    # Itens unificados da O.S. (estilo Bludata)
    salvar_itens_os as desp_salvar_itens,
    itens_os_view as desp_itens_view,
    sincronizar_parcelas_de_itens as desp_sync_parcelas,
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


def _desp_avatar_data_uri(file_storage):
    """Comprime a foto do usuário (até ~12MB tirada no celular) num avatar
    quadrado 256px WebP (~15-30KB) e devolve como data URI. Guardado no próprio
    banco do tenant — não depende de arquivo/volume no Railway. Mesmo motor do
    MandaJá/MandaJr (PIL + EXIF-transpose + LANCZOS)."""
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None
    try:
        from PIL import Image, ImageOps
        import io, base64
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)             # corrige foto girada do celular
        img = img.convert('RGB')
        img = ImageOps.fit(img, (256, 256), Image.LANCZOS)  # corta no centro → quadrado
        buf = io.BytesIO()
        img.save(buf, 'WEBP', quality=72, method=6)
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f'data:image/webp;base64,{b64}'
    except Exception as e:
        log.warning(f'[Desp] avatar compress error: {e}')
        return None


def _desp_money(v) -> float:
    """Converte valor monetário em formato BR para float, tolerante a None/vazio/'R$'.
    Aceita '1.500,00' (1500.0), '150,00' (150.0), '150' (150.0) e '150.50' (150.5)."""
    import re as _re_m
    s = _re_m.sub(r'[^\d,.\-]', '', str(v or '').strip())
    if not s:
        return 0.0
    if ',' in s and '.' in s:        # 1.500,00 → '.' é milhar, ',' é decimal
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:                    # 150,00 → ',' é decimal
        s = s.replace(',', '.')
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


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

    # Multi-usuário → tela de seleção de rostos (estilo Google/Chrome "Quem está usando?")
    if desp_contar_usuarios() > 0:
        return render_template(
            'despachante/login_picker.html',
            erro=erro,
            usuarios=desp_listar_usuarios_picker(),
            tentou=(request.form.get('usuario') or '').strip().lower(),
        )
    # Modo legado (0 usuários): login clássico por senha única
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
    foto = _desp_avatar_data_uri(request.files.get('foto'))
    try:
        desp_criar_usuario(nome, login, generate_password_hash(senha), role, foto)
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


@app.route('/despachante/usuarios/<int:uid>/deletar', methods=['POST'])
@_desp_admin_required
def desp_usuario_deletar(uid):
    if uid == session.get('desp_user_id'):
        return jsonify({'erro': 'Você não pode deletar a sua própria conta'}), 400
    desp_deletar_usuario(uid)
    return jsonify({'ok': True})


@app.route('/despachante/usuarios/<int:uid>/foto', methods=['POST'])
@_desp_admin_required
def desp_usuario_foto(uid):
    """Define/troca o avatar do usuário (comprime a foto enviada do celular).
    Envie 'foto' (multipart) para definir, ou campo 'remover' p/ tirar a foto."""
    if (request.form.get('remover') or '').strip():
        desp_atualizar_foto_usuario(uid, None)
        return jsonify({'ok': True, 'foto': None})
    foto = _desp_avatar_data_uri(request.files.get('foto'))
    if not foto:
        return jsonify({'erro': 'Não consegui processar essa imagem. Tente outra foto.'}), 400
    desp_atualizar_foto_usuario(uid, foto)
    return jsonify({'ok': True, 'foto': foto})


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
    # Checklist de primeiros passos (some quando tudo concluído)
    try:
        evo_ok = all(_desp_get_evo_config())
    except Exception:
        evo_ok = False
    try:
        precos_ok = bool(desp_get_precos())
    except Exception:
        precos_ok = False
    onboarding = {
        'whatsapp': evo_ok,
        'precos':   precos_ok,
        'os':       (stats.get('os_total', 0) or 0) > 0,
    }
    onboarding['completo'] = all(onboarding.values())
    return desp_render('dashboard.html', stats=stats, recentes=recentes,
                       plano=plano, onboarding=onboarding)


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
            'honorarios': _desp_money(f.get('honorarios')),
            'custos': _desp_money(f.get('custos')),
            'pago': _desp_money(f.get('pago')),
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
    desp_atualizar_os_status(id, status, _desp_money(pago) if pago else None)
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
    os_  = desp_get_os(os_id)
    if not os_:
        return jsonify({'erro': 'OS não encontrada'}), 404

    # Trava de segurança: não reconfigurar se já há parcela paga (apagaria o pagamento)
    conn  = get_desp_conn()
    pagas = conn.execute(
        "SELECT COUNT(*) FROM os_parcelas WHERE os_id=? AND pago_em IS NOT NULL", (os_id,)
    ).fetchone()[0]
    conn.close()
    if pagas and not data.get('forcar'):
        return jsonify({'erro': f'Já há {pagas} parcela(s) paga(s). Reconfigurar vai apagar os pagamentos registrados.',
                        'precisa_forcar': True}), 409

    total   = float(os_['honorarios']) + float(os_['custos'])
    manuais = data.get('parcelas')  # modo manual: [{valor, vencimento, forma}, ...]

    if manuais:
        norm, soma = [], 0.0
        for p in manuais:
            v = _desp_money(p.get('valor'))
            if v <= 0:
                continue
            norm.append({'valor': v, 'vencimento': p.get('vencimento'), 'forma': p.get('forma', '')})
            soma += v
        if not norm:
            return jsonify({'erro': 'Informe ao menos uma parcela com valor.'}), 400
        parcelas = desp_criar_parcelas(os_id, len(norm), soma, parcelas_custom=norm)
        desp_reg_hist(os_id, os_['status'],
                      f"Parcelamento manual em {len(norm)}x configurado (total R$ {soma:.2f})")
    else:
        n = int(data.get('total_parcelas', 1))
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
    # Dados completos da parcela + cliente + veículo (histórico e comprovante)
    c = get_desp_conn()
    row = c.execute("""
        SELECT p.os_id, p.numero, p.valor, p.vencimento,
               os.numero AS os_numero, os.servico,
               os.honorarios, os.custos, os.pago,
               cli.nome AS cliente_nome, cli.telefone,
               v.placa
        FROM os_parcelas p
        JOIN ordens_servico os ON os.id = p.os_id
        LEFT JOIN clientes cli ON cli.id = os.cliente_id
        LEFT JOIN veiculos  v  ON v.id  = os.veiculo_id
        WHERE p.id=?
    """, (pid,)).fetchone()
    c.close()
    res['whatsapp'] = None
    if row:
        row = dict(row)
        desp_reg_hist(row['os_id'], None,
                      f"Parcela {row['numero']} paga — R$ {row['valor']:.2f} ({forma})")
        if data.get('enviar_whatsapp') and (row.get('telefone') or '').strip():
            res['whatsapp'] = _desp_enviar_comprovante(row, forma)
    return jsonify(res)


def _desp_enviar_comprovante(row: dict, forma: str) -> str:
    """Envia comprovante de pagamento de parcela ao WhatsApp do cliente.
    Retorna: 'ok' | 'sem_config' | 'sem_tel' | 'erro'."""
    import re as _re_cp
    evo_url, evo_key, evo_instance = _desp_get_evo_config()
    if not (evo_url and evo_key and evo_instance):
        return 'sem_config'
    tel = _re_cp.sub(r'\D', '', row.get('telefone') or '')
    if not tel:
        return 'sem_tel'
    if not tel.startswith('55'):
        tel = '55' + tel
    cfg     = _desp_get_config()
    total   = float(row.get('honorarios') or 0) + float(row.get('custos') or 0)
    saldo   = max(total - float(row.get('pago') or 0), 0)
    nome    = (row.get('cliente_nome') or 'Cliente').split()[0].title()
    servico = DESP_SERVICOS.get(row.get('servico', ''), row.get('servico', ''))
    brl     = lambda x: f"{float(x or 0):.2f}".replace('.', ',')
    msg = (
        "✅ *Comprovante de Pagamento*\n\n"
        f"Olá {nome}! Recebemos o pagamento da *parcela {row.get('numero')}*.\n\n"
        f"🧾 Serviço: {servico}\n"
        f"🚗 Veículo: {(row.get('placa') or '—')}\n"
        f"💰 Valor pago: R$ {brl(row.get('valor'))}\n"
        f"💳 Forma: {forma}\n"
        + (f"📌 Saldo restante: R$ {brl(saldo)}\n" if saldo > 0.01 else "🎉 *Quitado!* Sem saldo restante.\n")
        + f"\nObrigado pela preferência! — {cfg['nome'].title()}"
    )
    try:
        resp = requests.post(
            f"{evo_url}/message/sendText/{evo_instance}",
            headers={'apikey': evo_key, 'Content-Type': 'application/json'},
            json={'number': tel, 'text': msg}, timeout=12,
        )
        return 'ok' if resp.status_code in (200, 201) else 'erro'
    except Exception as e:
        log.warning(f'comprovante whatsapp falhou: {e}')
        return 'erro'


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


# ── Cobrança / Inadimplência ──────────────────────────────────────────────────

def _fmt_data_br(iso: str) -> str:
    """'2026-07-10' → '10/07/2026'. Devolve o original se não der pra converter."""
    try:
        return datetime.strptime((iso or '')[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception:
        return iso or ''


def _desp_cobranca_rows(filtro: str = 'vencidas') -> list:
    """Parcelas em aberto (não pagas) com dados do cliente/veículo, por filtro.
    filtro: vencidas | hoje | semana | todas. Usa o banco do tenant atual."""
    conn = get_desp_conn()
    sql = """
        SELECT p.id AS parcela_id, p.os_id, p.numero AS parcela, p.valor, p.vencimento,
               os.numero AS os_numero, os.servico,
               c.id AS cliente_id, c.nome AS cliente_nome, c.telefone,
               v.placa,
               CAST(julianday('now') - julianday(p.vencimento) AS INTEGER) AS dias_atraso
        FROM os_parcelas p
        JOIN ordens_servico os ON os.id = p.os_id
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE p.pago_em IS NULL AND os.status != 'cancelada'
          AND p.vencimento IS NOT NULL AND p.vencimento != ''
    """
    if filtro == 'vencidas':
        sql += " AND p.vencimento < date('now')"
    elif filtro == 'hoje':
        sql += " AND p.vencimento = date('now')"
    elif filtro == 'semana':
        sql += " AND p.vencimento >= date('now') AND p.vencimento <= date('now','+7 day')"
    sql += " ORDER BY p.vencimento ASC"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    conn.close()
    return rows


_DESP_COBRANCA_MSG_PADRAO = (
    "Olá {nome}! 👋\n\n"
    "Passando pra lembrar da *parcela {parcela}* no valor de *R$ {valor}* "
    "com vencimento em *{vencimento}*, referente ao serviço do veículo {placa}.\n\n"
    "Se já pagou, pode desconsiderar. Qualquer dúvida, estou à disposição. 🙂\n"
    "— {despachante}"
)


@app.route('/despachante/cobranca')
@_desp_login_required
def desp_cobranca():
    filtro = request.args.get('f', 'vencidas')
    if filtro not in ('vencidas', 'hoje', 'semana', 'todas'):
        filtro = 'vencidas'
    rows = _desp_cobranca_rows(filtro)
    for r in rows:
        r['vencimento_br'] = _fmt_data_br(r['vencimento'])
        r['dias_atraso']   = max(int(r.get('dias_atraso') or 0), 0)
    total_aberto = round(sum(float(r['valor'] or 0) for r in rows), 2)
    sem_tel      = sum(1 for r in rows if not (r.get('telefone') or '').strip())
    return desp_render('cobranca.html',
        parcelas=rows, filtro=filtro, total_aberto=total_aberto,
        sem_tel=sem_tel, msg_padrao=_DESP_COBRANCA_MSG_PADRAO,
        servicos=DESP_SERVICOS)


@app.route('/despachante/cobranca/disparar', methods=['POST'])
@_desp_login_required
def desp_cobranca_disparar():
    """Dispara WhatsApp de cobrança para as parcelas em aberto do filtro — em background."""
    data         = request.get_json(silent=True) or {}
    filtro       = data.get('filtro', 'vencidas')
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
    rows = _desp_cobranca_rows(filtro if filtro in ('vencidas','hoje','semana','todas') else 'vencidas')
    contatos = []
    for r in rows:
        contatos.append({
            'cliente':    r['cliente_nome'],
            'telefone':   r['telefone'],
            'placa':      r['placa'],
            'valor_fmt':  f"{float(r['valor'] or 0):.2f}".replace('.', ','),
            'vencimento': _fmt_data_br(r['vencimento']),
            'parcela':    r['parcela'],
            'dias':       max(int(r.get('dias_atraso') or 0), 0),
        })
    vars_extra = dict(
        despachante=_desp_get_config()['nome'].title(),
        whatsapp=_desp_get_config()['whatsapp_fmt'],
        cidade=_desp_get_config()['cidade'],
    )
    job_id = _desp_new_job(len(contatos))
    threading.Thread(target=_desp_dispatch_worker, daemon=True,
                     args=(job_id, contatos, mensagem_tpl, evo_url, evo_key, evo_instance, delay_s, vars_extra)).start()
    return jsonify({'job_id': job_id, 'total': len(contatos)})


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
            fmt = {
                'nome':          nome_curto,
                'nome_completo': (c.get('cliente') or c.get('nome') or '').title(),
                'placa':         (c.get('placa') or '').upper(),
                # Variáveis por contato (usadas na cobrança; vazias nas demais campanhas)
                'valor':         c.get('valor_fmt', ''),
                'vencimento':    c.get('vencimento', ''),
                'parcela':       c.get('parcela', ''),
                'dias':          c.get('dias', ''),
            }
            fmt.update(vars_extra)
            msg = mensagem_tpl.format(**fmt)
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
        'honorarios': _desp_money(f.get('honorarios')),
        'custos': _desp_money(f.get('custos')),
        'pago': _desp_money(f.get('pago')),
        'forma_pagamento': f.get('forma_pagamento', ''),
        'observacoes': f.get('observacoes', ''),
        'corpo_req': f.get('corpo_req', ''),
        'exercicio': int(f.get('exercicio') or datetime.now().year),
        'situacao_pag': f.get('situacao_pag', ''),
    })
    return redirect(url_for('desp_detalhe_os', id=id))


# ══════════════════════════════════════════════════════════════════════════════
#  O.S. estilo BLUDATA — tela única, itens unificados (rota paralela /os2)
# ══════════════════════════════════════════════════════════════════════════════

def _desp_os2_extra(os_id):
    """Lê acréscimo/desconto da O.S. (colunas que get_os não retorna)."""
    conn = get_desp_conn()
    row = conn.execute(
        "SELECT COALESCE(acrescimo,0) ac, COALESCE(desconto,0) de FROM ordens_servico WHERE id=?",
        (os_id,)
    ).fetchone()
    conn.close()
    return (float(row['ac']), float(row['de'])) if row else (0.0, 0.0)


@app.route('/despachante/os2/nova')
@_desp_login_required
def desp_os2_nova():
    placa_pre = request.args.get('placa', '')
    cpf_pre   = request.args.get('cpf', '').strip()
    veiculo   = desp_buscar_placa(placa_pre) if placa_pre else None
    cliente   = None
    if veiculo and veiculo.get('proprietario_id'):
        cliente = desp_get_cliente(veiculo['proprietario_id'])
    elif cpf_pre:
        cliente = desp_buscar_cpf(cpf_pre)
    return desp_render('os2/editar.html', os=None, itens=[], parcelas=[],
                       veiculo=veiculo, cliente=cliente)


@app.route('/despachante/os2/<int:id>')
@_desp_login_required
def desp_os2_editar(id):
    os_ = desp_get_os(id)
    if not os_:
        abort(404)
    os_['acrescimo'], os_['desconto'] = _desp_os2_extra(id)
    return desp_render('os2/editar.html', os=os_,
                       itens=desp_itens_view(id),
                       parcelas=desp_get_parcelas(id),
                       checklist=desp_get_checklist(id, os_.get('servico', '')),
                       veiculo=None, cliente=None)


@app.route('/despachante/os2/salvar', methods=['POST'])
@app.route('/despachante/os2/<int:id>/salvar', methods=['POST'])
@_desp_login_required
def desp_os2_salvar(id=None):
    data  = request.get_json(silent=True) or {}
    cli   = data.get('cliente') or {}
    vei   = data.get('veiculo') or {}
    itens = data.get('itens') or []
    cab   = data.get('os') or {}

    if not id:
        ok, msg = _desp_check_limit('os_mes')
        if not ok:
            return jsonify({'erro': msg}), 403

    # ── Cliente (cria OU atualiza — edição em O.S. existente também persiste) ──
    cliente_id = cli.get('id') or None
    if (cli.get('nome') or '').strip():
        dados_cli = {k: (cli.get(k) or '') for k in (
            'tipo','nome','cpf','cnpj','rg','nascimento','nome_mae','telefone','email',
            'cep','logradouro','numero','complemento','bairro','cidade','uf')}
        dados_cli['tipo'] = dados_cli.get('tipo') or 'PF'
        dados_cli['uf']   = dados_cli.get('uf') or 'SC'
        if cliente_id:
            desp_atualizar_cliente(cliente_id, dados_cli)            # edição persiste
        else:
            existente = desp_buscar_cpf(dados_cli['cpf']) if dados_cli['cpf'] else None
            if existente:
                cliente_id = existente['id']; desp_atualizar_cliente(cliente_id, dados_cli)
            else:
                cliente_id = desp_criar_cliente(dados_cli)

    # ── Veículo (cria OU atualiza) ──
    veiculo_id = vei.get('id') or None
    if (vei.get('placa') or '').strip():
        dados_vei = {
            'placa': (vei.get('placa') or '').upper().replace('-',''),
            'renavam': vei.get('renavam',''), 'chassi': vei.get('chassi',''),
            'marca': vei.get('marca',''), 'modelo': vei.get('modelo',''),
            'ano_fab': vei.get('ano_fab') or None, 'ano_mod': vei.get('ano_mod') or None,
            'cor': vei.get('cor',''), 'especie': vei.get('especie','Automóvel'),
            'tipo_veiculo': vei.get('tipo_veiculo',''), 'categoria': vei.get('categoria','Particular'),
            'combustivel': vei.get('combustivel',''), 'num_crv': vei.get('num_crv',''),
        }
        if veiculo_id:
            conn = get_desp_conn()
            conn.execute(
                "UPDATE veiculos SET placa=?, renavam=?, chassi=?, marca=?, modelo=?, "
                "ano_fab=?, ano_mod=?, cor=?, especie=?, tipo_veiculo=?, categoria=?, "
                "combustivel=?, num_crv=? WHERE id=?",
                (dados_vei['placa'], dados_vei['renavam'], dados_vei['chassi'], dados_vei['marca'],
                 dados_vei['modelo'], dados_vei['ano_fab'], dados_vei['ano_mod'], dados_vei['cor'],
                 dados_vei['especie'], dados_vei['tipo_veiculo'], dados_vei['categoria'],
                 dados_vei['combustivel'], dados_vei['num_crv'], veiculo_id)
            )
            conn.commit(); conn.close()
        else:
            dados_vei['proprietario_id'] = cliente_id
            veiculo_id = desp_criar_veiculo(dados_vei)

    # Serviço principal = 1º item tipo serviço com código (p/ compat com docs/checklist)
    servico = 'outros'
    for it in itens:
        if (it.get('tipo') or 'servico') == 'servico' and (it.get('codigo') or ''):
            servico = it['codigo']; break

    cab_comum = {
        'servico': servico, 'honorarios': 0, 'custos': 0,
        'pago': 0,  # 'pago' é dirigido pelas baixas das parcelas (sincronizar abaixo)
        'forma_pagamento': cab.get('forma_pagamento',''),
        'observacoes': cab.get('observacoes',''),
        'exercicio': int(cab.get('exercicio') or datetime.now().year),
        'situacao_pag': cab.get('situacao_pag',''),
    }
    if not id:
        cab_comum['cliente_id'] = cliente_id
        cab_comum['veiculo_id'] = veiculo_id
        os_id = desp_criar_os(cab_comum)
    else:
        os_id = id
        cab_comum['corpo_req'] = cab.get('corpo_req','')
        desp_atualizar_os(os_id, cab_comum)

    # Vínculo cliente/veículo + acréscimo/desconto (UPDATE direto: atualizar_os não cobre)
    conn = get_desp_conn()
    conn.execute(
        "UPDATE ordens_servico SET cliente_id=COALESCE(?,cliente_id), "
        "veiculo_id=COALESCE(?,veiculo_id), acrescimo=?, desconto=? WHERE id=?",
        (cliente_id, veiculo_id, _desp_money(cab.get('acrescimo')),
         _desp_money(cab.get('desconto')), os_id)
    )
    conn.commit(); conn.close()

    # Itens unificados + recalc (bridge honorarios/custos/total)
    totais = desp_salvar_itens(os_id, itens)
    # Gera os títulos (parcelas) a partir dos itens → aparece em Títulos/Cobrança
    sync = desp_sync_parcelas(os_id)
    return jsonify({'ok': True, 'os_id': os_id, 'totais': totais, 'parcelas': sync})


def _valor_extenso(valor) -> str:
    """Valor em R$ por extenso (pt-BR). Cobre o intervalo típico de uma O.S."""
    valor = round(float(valor or 0), 2)
    inteiro  = int(valor)
    centavos = int(round((valor - inteiro) * 100))
    uni = ['', 'um','dois','três','quatro','cinco','seis','sete','oito','nove','dez',
           'onze','doze','treze','quatorze','quinze','dezesseis','dezessete','dezoito','dezenove']
    dez = ['', '', 'vinte','trinta','quarenta','cinquenta','sessenta','setenta','oitenta','noventa']
    cen = ['', 'cento','duzentos','trezentos','quatrocentos','quinhentos','seiscentos','setecentos','oitocentos','novecentos']
    def ate999(n):
        if n == 0: return ''
        if n == 100: return 'cem'
        p = []
        if n // 100: p.append(cen[n // 100])
        r = n % 100
        if r:
            if r < 20: p.append(uni[r])
            else:
                u = r % 10
                p.append(dez[r // 10] + (' e ' + uni[u] if u else ''))
        return ' e '.join(p)
    def ext(n):
        if n == 0: return 'zero'
        mi = n // 1000000; mil = (n % 1000000) // 1000; r = n % 1000
        p = []
        if mi:  p.append(ate999(mi) + (' milhão' if mi == 1 else ' milhões'))
        if mil: p.append('mil' if mil == 1 else ate999(mil) + ' mil')
        if r:   p.append(ate999(r))
        return ' e '.join([x for x in p if x])
    txt = ext(inteiro) + (' real' if inteiro == 1 else ' reais')
    if centavos:
        txt += ' e ' + ext(centavos) + (' centavo' if centavos == 1 else ' centavos')
    return txt


@app.route('/despachante/os2/<int:id>/print')
@_desp_login_required
def desp_os2_print(id):
    os_ = desp_get_os(id)
    if not os_:
        abort(404)
    os_['acrescimo'], os_['desconto'] = _desp_os2_extra(id)
    itens     = desp_itens_view(id)
    parcelas  = desp_get_parcelas(id)
    bruto     = round(sum(float(i['valor'] or 0) for i in itens), 2)
    total     = round(bruto + os_['acrescimo'] - os_['desconto'], 2)
    pago      = float(os_.get('pago') or 0)
    a_receber = max(round(total - pago, 2), 0)
    docs_needed = DESP_DOCS_POR_SERVICO.get(os_.get('servico', ''), DESP_DOCS_PADRAO)
    return desp_render('os2/print_bludata.html', os=os_, itens=itens, parcelas=parcelas,
        bruto=bruto, total=total, pago=pago, a_receber=a_receber,
        extenso=_valor_extenso(pago if pago > 0 else total),
        docs_needed=docs_needed, hoje=datetime.now())


# Colunas da trilha de processo (whitelist — evita SQL injection no UPDATE dinâmico)
_OS2_PROC_COLS = [
    'proc_situacao', 'ent_escritorio', 'entr_detran', 'lib_detran', 'ret_problema',
    'entrega_cliente', 'postagem', 'venc_vistoria', 'venc_crv', 'licenc_data',
    'protocolo_crlv', 'protocolo_crlv_em', 'protocolo_crv', 'protocolo_crv_em', 'num_seg_crv',
]


@app.route('/despachante/os2/<int:id>/processo', methods=['GET', 'POST'])
@_desp_login_required
def desp_os2_processo(id):
    os_ = desp_get_os(id)
    if not os_:
        abort(404)
    if request.method == 'POST':
        f = request.form
        sets, vals = [], []
        for col in _OS2_PROC_COLS:                       # só colunas da whitelist
            if col in f:
                sets.append(f"{col}=?")
                vals.append((f.get(col) or '').strip() or None)
        if sets:
            vals.append(id)
            conn = get_desp_conn()
            conn.execute(f"UPDATE ordens_servico SET {', '.join(sets)} WHERE id=?", vals)
            conn.commit(); conn.close()
        nova_sit = (f.get('proc_situacao') or '').strip()
        if nova_sit:
            desp_reg_hist(id, os_.get('status'),
                          f"Situação do processo: {nova_sit}", usuario=_desp_usuario_atual())
        from flask import flash
        flash('Processo atualizado.', 'ok')
        return redirect(url_for('desp_os2_processo', id=id))
    conn = get_desp_conn()
    proc = conn.execute(
        f"SELECT {', '.join(_OS2_PROC_COLS)} FROM ordens_servico WHERE id=?", (id,)
    ).fetchone()
    conn.close()
    return desp_render('os2/processo.html', os=os_, proc=dict(proc) if proc else {},
                       historico=desp_get_historico(id))


@app.route('/despachante/titulos')
@_desp_login_required
def desp_titulos():
    """Relatório de Títulos (livro-caixa estilo Bludata): cada parcela é um título
    AR (a receber, não paga) ou RE (recebido, paga). Baixa reusa /api/parcela/<id>/baixa."""
    tipo  = request.args.get('tipo', '')          # '' | AR | RE
    busca = request.args.get('q', '').strip()
    ini   = request.args.get('ini', '')
    fim   = request.args.get('fim', '')
    where, params = ["os.status != 'cancelada'", "p.vencimento IS NOT NULL", "p.vencimento != ''"], []
    if tipo == 'AR':
        where.append("p.pago_em IS NULL")
    elif tipo == 'RE':
        where.append("p.pago_em IS NOT NULL")
    if busca:
        where.append("(c.nome LIKE ? OR v.placa LIKE ? OR os.numero LIKE ?)")
        b = f"%{busca}%"; params += [b, b, b]
    if ini:
        where.append("date(p.vencimento) >= date(?)"); params.append(ini)
    if fim:
        where.append("date(p.vencimento) <= date(?)"); params.append(fim)
    conn = get_desp_conn()
    rows = conn.execute(f"""
        SELECT p.id AS parcela_id, p.numero AS parcela, p.valor, p.vencimento,
               p.pago_em, p.forma_pagamento,
               os.id AS os_id, os.numero AS os_numero,
               c.nome AS cliente_nome, c.telefone, v.placa
        FROM os_parcelas p
        JOIN ordens_servico os ON os.id = p.os_id
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE {' AND '.join(where)}
        ORDER BY (p.pago_em IS NOT NULL), date(p.vencimento) ASC
    """, params).fetchall()
    conn.close()
    titulos  = [dict(r) for r in rows]
    total_ar = round(sum(float(t['valor'] or 0) for t in titulos if not t['pago_em']), 2)
    total_re = round(sum(float(t['valor'] or 0) for t in titulos if t['pago_em']), 2)
    return desp_render('titulos.html', titulos=titulos, tipo=tipo, busca=busca,
                       ini=ini, fim=fim, total_ar=total_ar, total_re=total_re)


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


# ══ OCR multimodal: Gemini (melhor leitura de tabela/valores) com fallback Groq ══

def _desp_gemini_ocr(prompt: str, img_b64: str, mime: str, max_tokens: int = 4096):
    """Chama o Gemini (visão) com imagem + prompt em modo JSON. Retorna texto bruto, ou None se sem chave/resposta."""
    key = os.environ.get('GEMINI_API_KEY', '')
    if not key:
        return None
    # OCR de tabela é difícil: usa o Pro por padrão (lê muito melhor). Override via DESP_OCR_MODEL.
    model = os.environ.get('DESP_OCR_MODEL') or 'gemini-2.5-pro'
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    # Deixa o modelo "pensar" (melhora MUITO a leitura de tabela, inclusive no Flash);
    # tokens com folga p/ não truncar (thinking + resposta). Fallback p/ Groq se ainda truncar.
    gen = {'temperature': 0.1, 'responseMimeType': 'application/json',
           'maxOutputTokens': max(max_tokens, 8192)}
    body = {
        'contents': [{'role': 'user', 'parts': [
            {'inlineData': {'mimeType': mime or 'image/png', 'data': img_b64}},
            {'text': prompt},
        ]}],
        'generationConfig': gen,
    }
    r = requests.post(url, params={'key': key}, json=body, timeout=90)
    r.raise_for_status()
    cands = r.json().get('candidates') or []
    if not cands:
        return None
    parts = (cands[0].get('content') or {}).get('parts') or []
    txt = ''.join(p.get('text', '') for p in parts).strip()
    return txt or None


def _desp_ocr_call(prompt: str, img_b64: str, mime: str, max_tokens: int = 4096):
    """Prefere Gemini (melhor leitura de tabela), MAS só aceita se vier JSON parseável;
    senão cai pro Groq. Retorna (texto, motor) — motor para diagnóstico."""
    try:
        txt = _desp_gemini_ocr(prompt, img_b64, mime, max_tokens)
        if txt and _desp_json_loads(txt) is not None:
            return txt, 'gemini'
        if txt:
            log.warning(f'Gemini devolveu JSON inválido ({len(txt)} chars) — usando Groq')
    except Exception as e:
        log.warning(f'Gemini OCR falhou ({e}) — usando Groq')
    return _desp_groq_ocr(prompt, img_b64, mime, max_tokens), 'groq'


def _desp_groq_ocr(prompt: str, img_b64: str, mime: str, max_tokens: int = 2048):
    """Fallback: OCR via Groq (llama-4-scout). Retorna texto bruto, ou None se sem chave."""
    key = os.environ.get('GROQ_API_KEY', '')
    if not key:
        return None
    resp = requests.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        json={'model': 'meta-llama/llama-4-scout-17b-16e-instruct',
              'messages': [{'role': 'user', 'content': [
                  {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}},
                  {'type': 'text', 'text': prompt}]}],
              'max_tokens': max_tokens, 'temperature': 0.1},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content'].strip()


def _desp_json_loads(texto: str):
    """Parse tolerante: tira cercas markdown e extrai objeto/array JSON."""
    import json as _j, re as _r
    raw = (texto or '').strip()
    if raw.startswith('```'):
        raw = _r.sub(r'^```[a-zA-Z]*\n?', '', raw).rstrip('`').strip()
    try:
        return _j.loads(raw)
    except Exception:
        m = _r.search(r'\{[\s\S]*\}', raw) or _r.search(r'\[[\s\S]*\]', raw)
        if m:
            try:
                return _j.loads(m.group())
            except Exception:
                return None
    return None


PROMPT_DEBITOS = (
    'Você está vendo a tabela "Listagem de Débitos" do DETRANET (DETRAN-SC).\n'
    'As COLUNAS estão NESTA ordem (esquerda→direita): "Classe" | "Número DetranNET" | "Vencimento" | '
    '"Valor Nominal(R$)" | "Multa(R$)" | "Juros(R$)" | "Valor Atual(R$)".\n'
    'Para cada linha: descricao = coluna "Classe"; vencimento = coluna "Vencimento"; '
    'valor = a ÚLTIMA coluna "Valor Atual(R$)" (NÃO use "Valor Nominal"). Mantenha os dados da MESMA linha juntos — é PROIBIDO deslocar valor/data entre linhas.\n'
    'A 1ª linha geralmente é "Licenciamento Anual" (texto em azul/link) — NÃO pule a primeira linha.\n'
    'Devolva {"debitos":[{"tipo","descricao","numero_detran","valor","vencimento"}], "total_tela":"valor do campo \\"Total dos Débitos\\" mostrado na tela"} com TODAS as linhas que compõem o Total.\n'
    'REGRAS (é dinheiro — máxima atenção):\n'
    '- descricao: copie o texto EXATO da coluna "Classe" (ex.: "Licenciamento Anual 2026", "IPVA (1a. Cota) 2026").\n'
    '- tipo: pela palavra na Classe — se contém "Licenciamento" → "Licenciamento"; se contém "IPVA" → "IPVA"; auto/infração → "Multa"; taxa → "Taxa DETRAN"; senão "Outros". Uma cota de IPVA é SEMPRE "IPVA" (nunca Multa nem Licenciamento).\n'
    '- valor: a coluna "Valor Atual(R$)" DAQUELA linha. vencimento: a coluna "Vencimento" DAQUELA linha (dd/mm/aaaa). numero_detran: coluna "Número DetranNET".\n'
    '- Traga TODAS as linhas da tabela: Licenciamento, IPVA (Cota Única), CADA cota parcelada (1ª/2ª/3ª), Multas e Taxas. '
    'NÃO exclua nenhuma linha — o despachante decide quais manter e apaga o resto. A "Cota Única" costuma ter "*" (não entra no total): traga mesmo assim.\n'
    '- Em Santa Catarina NÃO existe DPVAT — nunca inclua.\n'
    '- Extraia SOMENTE o que está visível; não invente nem calcule. Cada linha com seu próprio valor e vencimento.\n'
    '\nEXEMPLO (só o FORMATO — use SEMPRE os dados REAIS da imagem):\n'
    'Tabela:\n'
    '  Licenciamento Anual 2026 | 30/09/2026 | (Valor Atual) 149,37\n'
    '  IPVA (Cota Unica) 2026   | 31/07/2026 | (Valor Atual) 361,46\n'
    '  IPVA (1a. Cota) 2026     | 10/07/2026 | (Valor Atual) 120,49\n'
    '  IPVA (2a. Cota) 2026     | 10/08/2026 | (Valor Atual) 120,49\n'
    '  IPVA (3a. Cota) 2026     | 10/09/2026 | (Valor Atual) 120,48\n'
    'Resposta correta:\n'
    '{"debitos":['
    '{"tipo":"Licenciamento","descricao":"Licenciamento Anual 2026","valor":"149,37","vencimento":"30/09/2026"},'
    '{"tipo":"IPVA","descricao":"IPVA (Cota Unica) 2026","valor":"361,46","vencimento":"31/07/2026"},'
    '{"tipo":"IPVA","descricao":"IPVA (1a. Cota) 2026","valor":"120,49","vencimento":"10/07/2026"},'
    '{"tipo":"IPVA","descricao":"IPVA (2a. Cota) 2026","valor":"120,49","vencimento":"10/08/2026"},'
    '{"tipo":"IPVA","descricao":"IPVA (3a. Cota) 2026","valor":"120,48","vencimento":"10/09/2026"}'
    ']}\n'
    'REPARE: cada linha tem o SEU valor. A "1a. Cota" é 120,49 — NUNCA repita o valor da Cota Única (361,46) nas cotas parceladas.\n'
    'Responda SOMENTE o JSON.'
)


@app.route('/despachante/api/ocr/debitos', methods=['POST'])
@_desp_login_required
def desp_api_ocr_debitos():
    """Recebe print do DETRANET (imagem) e extrai a lista de débitos via IA (Gemini→Groq)."""
    import base64, mimetypes
    if not (os.environ.get('GEMINI_API_KEY') or os.environ.get('GROQ_API_KEY')):
        return jsonify({'erro': 'Nenhuma IA de OCR configurada (GEMINI_API_KEY ou GROQ_API_KEY)'}), 500
    f = request.files.get('arquivo')
    if not f:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400
    mime    = f.mimetype or mimetypes.guess_type(f.filename or '')[0] or 'image/jpeg'
    img_b64 = base64.b64encode(f.read()).decode()
    try:
        texto, motor = _desp_ocr_call(PROMPT_DEBITOS, img_b64, mime, max_tokens=4096)
        parsed = _desp_json_loads(texto)
        total_tela = None
        if isinstance(parsed, dict):
            total_tela = parsed.get('total_tela') or parsed.get('total')
            data = parsed.get('debitos') or parsed.get('itens') or []
        else:
            data = parsed
        if not isinstance(data, list):
            return jsonify({'erro': 'IA não identificou débitos — verifique se é um print do DETRANET',
                            'motor': motor, 'raw': (texto or '')[:200]}), 422
        debitos = [d for d in data if isinstance(d, dict) and (d.get('tipo') or d.get('descricao'))]
        if not debitos:
            return jsonify({'erro': 'Nenhum débito encontrado na imagem', 'motor': motor}), 422
        # total_tela vai só como referência (a soma NÃO bate de propósito: cota única + parcelas etc.)
        return jsonify({'ok': True, 'debitos': debitos, 'total': len(debitos), 'motor': motor,
                        'total_tela': _desp_money(total_tela) if total_tela else None})
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
    import re as _re2
    data    = request.get_json(silent=True) or {}
    img_b64 = (data.get('imagem') or '').strip()
    mime    = data.get('mime', 'image/png')
    if not img_b64: return jsonify({'erro': 'Nenhuma imagem recebida'}), 400
    if not (os.environ.get('GEMINI_API_KEY') or os.environ.get('GROQ_API_KEY')):
        return jsonify({'erro': 'Nenhuma IA de OCR configurada (GEMINI_API_KEY ou GROQ_API_KEY)'}), 500
    prompt = '''Analise esta imagem de documento ou tela de sistema de despachante/DETRAN.
Extraia TODOS os dados visíveis de veículo, do proprietário/cliente e de débitos/taxas.
Retorne APENAS um objeto JSON válido com os campos (use null para não encontrados):
{"placa":null,"renavam":null,"chassi":null,"marca":null,"modelo":null,"ano_fab":null,
"ano_mod":null,"cor":null,"especie":null,"categoria":null,"combustivel":null,"num_crv":null,
"nome":null,"cpf":null,"cnpj":null,"rg":null,"nascimento":null,"nome_mae":null,
"telefone":null,"email":null,"cep":null,"logradouro":null,"numero":null,
"complemento":null,"bairro":null,"cidade":null,"uf":null,
"total_debitos":null,"ipva":null,"licenciamento":null,"multas":null,
"debitos":[]}
Instruções para o veículo (MUITO IMPORTANTE — não troque os campos):
- marca: APENAS a marca/fabricante (ex.: "FORD", "VW", "FIAT", "CHEVROLET"). Num campo "Marca/Modelo: I/FORD FOCUS 2.0L", a marca é "FORD".
- modelo: APENAS o modelo, sem a marca (ex.: "FOCUS 2.0L").
- chassi: SOMENTE o número do chassi/VIN — 17 caracteres alfanuméricos (letras+dígitos), sem espaços, barras ou parênteses. Se não houver um chassi claramente identificável, use null. NUNCA coloque marca/modelo/código de tipo no chassi.
- NUNCA use o nome/rótulo de um campo como valor. Se ler "RENAVAM", "CHASSI", "MARCA", "PLACA" como rótulo e não souber o valor real, use null.
Instruções para os campos de débitos:
- Extraia SOMENTE valores que apareçam EXPLICITAMENTE na imagem, com o número visível. NUNCA invente, estime, calcule ou complete valores que não estejam na tela. Se um débito não aparecer, deixe null.
- Em Santa Catarina NÃO existe cobrança de DPVAT/seguro obrigatório. NÃO inclua DPVAT em hipótese alguma, mesmo que o modelo "ache" que deveria existir.
- ipva: soma dos valores de IPVA visíveis, como número decimal
- licenciamento: soma dos valores de Licenciamento/Taxa Detran visíveis, como número decimal
- multas: soma dos valores de Multas visíveis, como número decimal
- total_debitos: use null A MENOS QUE exista na tela um campo escrito "Total dos Débitos" (ou equivalente); nesse caso copie EXATAMENTE o valor mostrado. NÃO some você mesmo.
- debitos: SE houver uma tabela "Listagem de Débitos", devolva um array com TODAS as linhas: [{"tipo","descricao","valor","vencimento"}].
  • Colunas na ordem: Classe | Número DetranNET | Vencimento | Valor Nominal | Multa | Juros | Valor Atual. descricao=Classe; vencimento=Vencimento; valor=ÚLTIMA coluna "Valor Atual(R$)" (NÃO use Valor Nominal). Não pule a 1ª linha (geralmente "Licenciamento Anual", em azul/link).
  • tipo: IPVA / Licenciamento / Multa / Taxa DETRAN / Outros. Uma cota de IPVA ("Cota Única", "1ª/2ª/3ª Cota") é SEMPRE "IPVA", NUNCA "Multa".
  • Traga TODAS as linhas (Licenciamento, IPVA Cota Única, cada Cota, Multas, Taxas) — NÃO exclua nenhuma; o despachante apaga o que não usar. Cada linha com seu próprio valor e vencimento.
  Se não houver tabela de débitos visível, devolva "debitos":[].
IMPORTANTE: Retorne SOMENTE o JSON, nada mais.'''
    try:
        texto, motor = _desp_ocr_call(prompt, img_b64, mime, max_tokens=4096)
        dados = _desp_json_loads(texto)
        if not isinstance(dados, dict):
            return jsonify({'erro': 'IA não retornou JSON válido',
                            'motor': motor, 'raw': (texto or '')[:200]}), 422
        dados = {k: v for k, v in dados.items() if v is not None and v != ''}
        # ── Sanidade: rótulo não vira valor; marca/modelo não vira chassi ──
        _LBL = {'renavam','chassi','marca','modelo','placa','cpf','cnpj','rg',
                'cor','especie','categoria','combustivel','nome','telefone'}
        for campo in ('marca', 'modelo', 'cor'):
            if str(dados.get(campo, '')).strip().lower() in _LBL:
                dados.pop(campo, None)
        ch = str(dados.get('chassi', '') or '')
        ch_clean = _re2.sub(r'[^A-Za-z0-9]', '', ch)
        # chassi válido = 11–17 alfanuméricos, sem espaço/barra/parênteses (senão é marca/modelo/lixo)
        if any(c in ch for c in ' /()') or not (11 <= len(ch_clean) <= 17):
            dados.pop('chassi', None)
        return jsonify({'ok': True, 'dados': dados, 'campos': len(dados), 'motor': motor})
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
    safe_name = secure_filename(f.filename) or 'arquivo'
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

        # ── MandaZap scheduler (retoma campanhas agendadas / janela de horário) ─
        try:
            threading.Thread(target=_mz_campaign_scheduler, daemon=True).start()
            log.info(f'[MandaZap] Scheduler anti-ban iniciado (janela {MZ_SEND_HOUR_START}h–{MZ_SEND_HOUR_END}h, checa a cada 5 min)')
        except Exception as e:
            log.error(f"[startup] MandaZap scheduler erro: {e}")

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
    _ref = (request.args.get('ref') or '').strip().upper()[:12]
    if _ref:
        session['mja_ref'] = _ref   # afiliado que trouxe (programa de afiliados)
    return render_template('mandaja/landing.html')


# ── MandaJr — fachada "basicão" (mesma engine, mode='jr') ────────────────────
@app.route('/mandajr')
def mandajr_landing():
    _ref = (request.args.get('ref') or '').strip().upper()[:12]
    if _ref:
        session['mja_ref'] = _ref   # afiliado que trouxe (programa de afiliados)
    return render_template('mandaja/jr_landing.html')


@app.route('/mandajr/entrar', methods=['GET', 'POST'])
def mandajr_entrar():
    """Login com cara do MandaJr (mesma base; aceita WhatsApp ou e-mail)."""
    if session.get('mja_store_id'):
        return redirect('/mandaja/painel')
    erro = None
    if request.method == 'POST':
        ident = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        conn  = get_saas_db()
        store = conn.execute('SELECT * FROM mandaja_stores WHERE LOWER(email)=? AND email!="" AND active=1',
                             (ident,)).fetchone()
        if not store:
            d = ''.join(c for c in ident if c.isdigit())
            if len(d) >= 10:
                store = conn.execute(
                    "SELECT * FROM mandaja_stores WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ? AND active=1",
                    (d,)).fetchone()
        conn.close()
        if store and check_password_hash(store['password_hash'], senha):
            session['mja_store_id']   = store['id']
            session['mja_store_name'] = store['name']
            session['mja_store_slug'] = store['slug']
            session['mja_plan']       = store['plan']
            return redirect('/mandaja/painel')
        erro = 'WhatsApp/e-mail ou senha incorretos.'
    return render_template('mandaja/jr_entrar.html', erro=erro)


@app.route('/mandajr/comecar', methods=['GET', 'POST'])
def mandajr_comecar():
    """Onboarding enxuto: nome + WhatsApp + senha + chave PIX → loja no ar."""
    if session.get('mja_store_id'):
        return redirect('/mandaja/painel')
    if request.method == 'POST':
        name      = request.form.get('name', '').strip()
        phone     = request.form.get('phone', '').strip()
        senha     = request.form.get('senha', '')
        pix_chave = request.form.get('pix_chave', '').strip()
        if not all([name, phone, senha]):
            return render_template('mandaja/jr_comecar.html',
                                   error='Preencha o nome, o WhatsApp e a senha.')
        if len(senha) < 6:
            return render_template('mandaja/jr_comecar.html',
                                   error='A senha precisa de pelo menos 6 caracteres.')
        phone_digits = ''.join(c for c in phone if c.isdigit())
        if len(phone_digits) < 10:
            return render_template('mandaja/jr_comecar.html',
                                   error='Digite um WhatsApp válido com DDD.')
        conn = get_saas_db()
        # WhatsApp único (anti-abuso, igual ao MandaJá)
        existing = conn.execute(
            "SELECT id FROM mandaja_stores WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ?",
            (phone_digits,)).fetchone()
        if existing:
            conn.close()
            return render_template('mandaja/jr_comecar.html',
                                   error='Esse WhatsApp já tem uma loja. É só entrar com a sua senha.',
                                   ja_existe=True)
        slug = _slugify(name); base = slug; i = 1
        while conn.execute('SELECT id FROM mandaja_stores WHERE slug=?', (slug,)).fetchone():
            slug = f"{base}-{i}"; i += 1
        trial_ends = (datetime.now() + timedelta(days=7)).isoformat()
        conn.execute('''
            INSERT INTO mandaja_stores
            (name, slug, owner_name, phone, whatsapp, email, password_hash, category, city,
             pix_chave, pix_nome, plan, mode, aberto, plan_active, created_at, trial_ends, afiliado_ref)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'jr','jr',1,0,?,?,?)
        ''', (name, slug, name, phone, phone, '', generate_password_hash(senha),
              'lanchonete', '', pix_chave, name, datetime.now().isoformat(), trial_ends,
              ((session.get('mja_ref') or request.args.get('ref') or '').strip().upper()[:12] or None)))
        conn.commit()
        store = conn.execute('SELECT * FROM mandaja_stores WHERE slug=?', (slug,)).fetchone()
        # Horários permissivos por padrão (o controle real é o botão Aberto/Fechado)
        for wd in range(7):
            conn.execute('''INSERT INTO mandaja_hours (store_id, weekday, open_time, close_time, active)
                            VALUES (?,?,?,?,?)''', (store['id'], wd, '08:00', '23:59', 1))
        conn.commit()
        conn.close()
        session['mja_store_id']   = store['id']
        session['mja_store_name'] = store['name']
        session['mja_store_slug'] = store['slug']
        session['mja_plan']       = 'jr'
        return redirect('/mandaja/painel?novo=1')
    return render_template('mandaja/jr_comecar.html')


@app.route('/mandajr/aberto', methods=['POST'])
@_mandaja_login_required
def mandajr_toggle_aberto():
    """Interruptor Aberto/Fechado da loja Jr."""
    aberto = 1 if (request.json or {}).get('aberto') else 0
    conn = get_saas_db()
    conn.execute('UPDATE mandaja_stores SET aberto=? WHERE id=?',
                 (aberto, session['mja_store_id']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'aberto': aberto})


@app.route('/mandajr/compartilhar')
@_mandaja_login_required
def mandajr_compartilhar():
    """Link limpo + QR Code da loja pra divulgar."""
    store = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    loja_url = f"{request.host_url}loja/{store['slug']}"
    qr_b64 = ''
    try:
        import qrcode as _qr, io as _io, base64 as _b64
        q = _qr.QRCode(error_correction=_qr.constants.ERROR_CORRECT_M, box_size=8, border=2)
        q.add_data(loja_url); q.make(fit=True)
        _buf = _io.BytesIO()
        q.make_image(fill_color='#0B0B12', back_color='white').save(_buf, format='PNG')
        qr_b64 = _b64.b64encode(_buf.getvalue()).decode()
    except Exception as _qe:
        log.warning(f'[MandaJr] QR error: {_qe}')
    return render_template('mandaja/jr_compartilhar.html',
                           store=store, loja_url=loja_url, qr_b64=qr_b64)


@app.route('/mandajr/assinar', methods=['GET', 'POST'])
@_mandaja_login_required
def mandajr_assinar():
    """Assinatura MandaJr R$29/mês via Asaas (PIX). Coleta CPF na hora."""
    store = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    p    = MANDAJA_PLANS['jr']
    erro = None
    pix  = None
    if request.method == 'POST':
        cpf   = ''.join(c for c in request.form.get('cpf_cnpj', '') if c.isdigit())
        email = request.form.get('email', '').strip().lower()
        if len(cpf) not in (11, 14):
            erro = 'Digite um CPF (11 dígitos) ou CNPJ (14 dígitos).'
        elif len(cpf) == 11 and not _cpf_valido(cpf):
            erro = 'CPF inválido. Confira os números.'
        else:
            conn = get_saas_db()
            conn.execute("UPDATE mandaja_stores SET cpf_cnpj=?, email=COALESCE(NULLIF(?,''), email) WHERE id=?",
                         (cpf, email, store['id']))
            conn.commit(); conn.close()
            customer_id = _asaas_criar_ou_buscar_cliente_saas(
                store.get('owner_name') or store['name'],
                email or store.get('email', ''), store.get('phone', ''),
                cpf, store['id'], 'mandaja_stores')
            if not customer_id:
                erro = 'Não foi possível iniciar o pagamento. Confira seu CPF e tente de novo.'
            else:
                conn = get_saas_db()
                conn.execute('UPDATE mandaja_stores SET asaas_customer_id=? WHERE id=?',
                             (customer_id, store['id']))
                conn.commit(); conn.close()
                sub = _asaas_criar_assinatura_saas(
                    customer_id, 'mandaja', 'jr', float(p['price']),
                    f"MandaJr — {store['name']}", 'PIX')
                if sub.get('id'):
                    pix = _asaas_get_pix_qr(sub['id'])
                    if not pix:
                        return redirect('/mandaja/painel?assinatura=processando')
                else:
                    erro = (sub.get('errors') or [{}])[0].get('description', 'Erro ao gerar a cobrança.')
    return render_template('mandaja/jr_assinar.html', store=store, p=p, erro=erro, pix=pix)


@app.route('/mandajr/assinatura-status')
@_mandaja_login_required
def mandajr_assinatura_status():
    """Polling pra tela de pagamento detectar a ativação na hora."""
    store = _mandaja_get_store()
    return jsonify({'ativo': bool(store and store.get('plan_active'))})


@app.route('/mandajr/virar-pro', methods=['GET', 'POST'])
@_mandaja_login_required
def mandajr_virar_pro():
    """Escada Jr → Pro: 1 clique, mesma loja/produtos/pedidos, só destrava as telas."""
    store = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    if store.get('mode') != 'jr':
        return redirect('/mandaja/painel')
    if request.method == 'POST':
        conn = get_saas_db()
        conn.execute("UPDATE mandaja_stores SET mode='pro', plan='micro' WHERE id=?", (store['id'],))
        conn.commit(); conn.close()
        session['mja_plan'] = 'micro'
        return redirect('/mandaja/painel?virou_pro=1')
    return render_template('mandaja/jr_virar_pro.html', store=store)


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
        # MandaJr cadastra sem e-mail: deixa entrar pelo WhatsApp também
        if not store:
            login_digits = ''.join(c for c in email if c.isdigit())
            if len(login_digits) >= 10:
                store = conn.execute(
                    "SELECT * FROM mandaja_stores WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ? AND active=1",
                    (login_digits,)).fetchone()
        conn.close()
        if store and check_password_hash(store['password_hash'], senha):
            session['mja_store_id']   = store['id']
            session['mja_store_name'] = store['name']
            session['mja_store_slug'] = store['slug']
            session['mja_plan']       = store['plan']
            return redirect('/mandaja/painel')
        return render_template('mandaja/entrar.html', error='E-mail/WhatsApp ou senha incorretos.')
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
                (name, slug, owner_name, phone, email, password_hash, category, city, plan, plan_active, created_at, trial_ends, cpf_cnpj, afiliado_ref)
                VALUES (?,?,?,?,?,?,?,?,'micro',0,?,?,?,?)
            ''', (name, slug, owner_name, phone, email,
                  generate_password_hash(senha), category, city,
                  datetime.now().isoformat(), trial_ends, cpf_cnpj_digits,
                  ((session.get('mja_ref') or request.args.get('ref') or '').strip().upper()[:12] or None)))
        except Exception as _mja_err:
            log.error('[MandaJá] Erro no INSERT (possível coluna faltando): %s', _mja_err)
            # Tenta sem cpf_cnpj — coluna pode ainda não existir no DB de produção
            conn.execute('''
                INSERT INTO mandaja_stores
                (name, slug, owner_name, phone, email, password_hash, category, city, plan, plan_active, created_at, trial_ends, afiliado_ref)
                VALUES (?,?,?,?,?,?,?,?,'micro',0,?,?,?)
            ''', (name, slug, owner_name, phone, email,
                  generate_password_hash(senha), category, city,
                  datetime.now().isoformat(), trial_ends,
                  ((session.get('mja_ref') or request.args.get('ref') or '').strip().upper()[:12] or None)))
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
        if store['email']:
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
    # Volta pro site certo: loja Jr → MandaJr; Pro → MandaJá
    destino = '/mandajr' if session.get('mja_plan') == 'jr' else '/mandaja'
    for k in ('mja_store_id', 'mja_store_name', 'mja_store_slug', 'mja_plan'):
        session.pop(k, None)
    return redirect(destino)


# ── MandaJá — Recuperação de senha ───────────────────────────────────────────
@app.route('/mandaja/esqueci-senha', methods=['GET', 'POST'])
def mandaja_esqueci_senha():
    enviado = False
    codigo_tela = None
    erro = None
    if request.method == 'POST':
        ident = request.form.get('email', '').strip().lower()
        conn = get_saas_db()
        # Busca por e-mail OU por WhatsApp (loja Jr cadastra sem e-mail)
        store = conn.execute('SELECT * FROM mandaja_stores WHERE LOWER(email)=? AND email!=""', (ident,)).fetchone()
        if not store:
            d = ''.join(c for c in ident if c.isdigit())
            if len(d) >= 10:
                store = conn.execute(
                    "SELECT * FROM mandaja_stores WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ?",
                    (d,)).fetchone()
        if not store:
            erro = 'E-mail ou WhatsApp não encontrado.'
            conn.close()
        else:
            store = dict(store)
            codigo = str(random.randint(100000, 999999))
            expires = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute('UPDATE mandaja_stores SET reset_token=?, reset_expires=? WHERE id=?',
                         (codigo, expires, store['id']))
            conn.commit(); conn.close()
            primeiro = (store.get('owner_name') or store.get('name') or 'lojista').split()[0]
            ok = False
            # 1) Tenta por e-mail (se tiver)
            if store.get('email'):
                html_email = f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
                  <div style="font-size:32px;margin-bottom:8px">🛵</div>
                  <h2 style="color:#f97316">Recuperação de senha — MandaJá</h2>
                  <p>Olá, <strong>{primeiro}</strong>!</p>
                  <p>Seu código de recuperação é:</p>
                  <div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#f97316;
                              background:#fff7ed;padding:20px;border-radius:12px;text-align:center;
                              margin:20px 0">{codigo}</div>
                  <p style="color:#666;font-size:13px">Válido por 2 horas.</p>
                </div>"""
                ok = _enviar_email(store['email'], 'Código de recuperação — MandaJá', html_email)
            # 2) Sem e-mail (ou falhou): manda pelo WhatsApp
            if not ok and store.get('phone'):
                msg = (f"🔑 *MandaJr* — recuperação de senha\n\n"
                       f"Olá, {primeiro}! Seu código é:\n\n*{codigo}*\n\n"
                       f"Válido por 2 horas. Se não foi você, ignore.")
                ok = _agenda_send_whatsapp(store['phone'], msg,
                                           os.environ.get('MANDAJA_EVO_INSTANCE', ''))
            enviado = True
            if not ok:
                codigo_tela = codigo   # fallback: mostra na tela
    return render_template('mandaja/esqueci_senha.html',
                           enviado=enviado, codigo_tela=codigo_tela, erro=erro)


@app.route('/mandaja/redefinir-senha', methods=['GET', 'POST'])
def mandaja_redefinir_senha():
    sucesso = False
    erro = None
    if request.method == 'POST':
        ident = request.form.get('email', '').strip().lower()
        codigo = request.form.get('codigo', '').strip()
        nova = request.form.get('nova_senha', '')
        if len(nova) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        else:
            conn = get_saas_db()
            # Busca por e-mail OU WhatsApp (loja Jr cadastra sem e-mail)
            store = conn.execute('SELECT * FROM mandaja_stores WHERE LOWER(email)=? AND email!=""', (ident,)).fetchone()
            if not store:
                d = ''.join(c for c in ident if c.isdigit())
                if len(d) >= 10:
                    store = conn.execute(
                        "SELECT * FROM mandaja_stores WHERE replace(replace(replace(replace(replace(phone,'(',''),')',''),'-',''),' ',''),'+','') = ?",
                        (d,)).fetchone()
            if not store or store['reset_token'] != codigo:
                erro = 'Código inválido ou e-mail/WhatsApp incorreto.'
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
    # MandaJr usa a casca simples (3 botões + Aberto/Fechado); Pro usa o painel completo
    template = 'mandaja/jr_painel.html' if store.get('mode') == 'jr' else 'mandaja/painel.html'
    return render_template(template,
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
    return render_template(_mja_tpl(store, 'produtos'),
                           store=store, cats=[dict(c) for c in cats],
                           prods=[dict(p) for p in prods],
                           plan=plan_info)


@app.route('/mandaja/produtos/novo', methods=['GET', 'POST'])
@_mandaja_login_required
def mandaja_produto_novo():
    store    = _mandaja_get_store()
    if not store:
        return redirect('/mandaja/logout')
    # Paywall: trial vencido e não pagou → manda assinar
    if _mandaja_bloqueado(store):
        return redirect('/mandajr/assinar' if store.get('mode') == 'jr' else '/mandaja/painel')
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
        price = _mja_preco(request.form.get('price'))
        cost  = _mja_preco(request.form.get('cost'))
        try:
            stock = int(request.form.get('stock', -1) or -1)
        except (ValueError, TypeError):
            stock = -1
        category_id  = request.form.get('category_id') or None
        photo_url    = request.form.get('photo_url', '').strip()
        options_raw  = request.form.get('options_json', '[]').strip()
        try:
            _json.loads(options_raw)
        except Exception:
            options_raw = '[]'
        if not name:
            conn.close()
            return render_template(_mja_tpl(store, 'produto_form'),
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
    return render_template(_mja_tpl(store, 'produto_form'),
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
        price = _mja_preco(request.form.get('price'))
        cost  = _mja_preco(request.form.get('cost'))
        try:
            stock = int(request.form.get('stock', -1) or -1)
        except (ValueError, TypeError):
            stock = -1
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
    return render_template(_mja_tpl(store, 'produto_form'),
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
    return render_template(_mja_tpl(store, 'pedidos'),
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
    pedido['endereco_txt'], pedido['maps_url'] = _mandaja_endereco(
        pedido.get('address', ''), pedido.get('address_number', ''),
        pedido.get('address_complement', ''), pedido.get('neighborhood', ''),
        pedido.get('city', ''), pedido.get('address_reference', ''))
    return render_template(_mja_tpl(store, 'pedido_detalhe'), store=store, pedido=pedido)


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
    is_jr    = store.get('mode') == 'jr'
    tpl      = _mja_tpl(store, 'configuracoes')
    conn     = get_saas_db()
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        if action == 'change_pass':
            senha_atual = request.form.get('senha_atual', '')
            senha_nova  = request.form.get('senha_nova', '')
            if not check_password_hash(store['password_hash'], senha_atual):
                conn.close()
                return render_template(tpl,
                                       store=store, cats=MANDAJA_STORE_CATEGORIES,
                                       error_pass='Senha atual incorreta.')
            if len(senha_nova) < 6:
                conn.close()
                return render_template(tpl,
                                       store=store, cats=MANDAJA_STORE_CATEGORIES,
                                       error_pass='Nova senha deve ter pelo menos 6 caracteres.')
            conn.execute('UPDATE mandaja_stores SET password_hash=? WHERE id=?',
                         (generate_password_hash(senha_nova), store_id))
            conn.commit()
            conn.close()
            return redirect('/mandaja/configuracoes?ok=senha')
        if is_jr:
            # MandaJr: salva só o essencial (não apaga os campos avançados)
            name = request.form.get('name', '').strip() or store['name']
            conn.execute('UPDATE mandaja_stores SET name=?, pix_chave=?, pix_nome=?, delivery_fee=? WHERE id=?',
                         (name, request.form.get('pix_chave', '').strip(),
                          request.form.get('pix_nome', '').strip() or name,
                          _mja_preco(request.form.get('delivery_fee')), store_id))
            conn.commit(); conn.close()
            session['mja_store_name'] = name
            return redirect('/mandaja/configuracoes?ok=1')
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
        # Taxa por bairro (zonas de entrega)
        bairros = request.form.getlist('zone_bairro')
        taxas   = request.form.getlist('zone_taxa')
        zones = [{'bairro': b.strip(), 'taxa': _mja_preco(t)}
                 for b, t in zip(bairros, taxas) if b.strip()]
        conn.execute('UPDATE mandaja_stores SET delivery_zones=? WHERE id=?',
                     (_json.dumps(zones, ensure_ascii=False), store_id))
        conn.commit()
        conn.close()
        session['mja_store_name'] = updates['name']
        return redirect('/mandaja/configuracoes?ok=1')
    conn.close()
    return render_template(tpl, store=store, cats=MANDAJA_STORE_CATEGORIES)


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
        _np   = (order.get('customer_name') or '').strip().split()
        nome  = _np[0] if _np else 'Cliente'
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


# ── Tela da Cozinha (protegida por PIN — acesso via slug público) ────────────
def _cozinha_autorizado(store):
    """Pode ver a cozinha: dono logado OU PIN certo (na URL ou no cookie)."""
    if session.get('mja_store_id') == store['id']:
        return True
    pin = store.get('kitchen_pin') or ''
    if not pin:
        return False
    return request.args.get('pin') == pin or request.cookies.get(f"coz_{store['id']}") == pin


def _cozinha_ensure_pin(store, conn):
    """Gera o PIN de 4 dígitos na primeira vez que o dono abre a cozinha."""
    if not store.get('kitchen_pin'):
        pin = f"{random.randint(1000, 9999)}"
        conn.execute('UPDATE mandaja_stores SET kitchen_pin=? WHERE id=?', (pin, store['id']))
        conn.commit()
        store['kitchen_pin'] = pin
    return store['kitchen_pin']


@app.route('/cozinha/<slug>')
def mandaja_cozinha(slug):
    conn  = get_saas_db()
    store = conn.execute('SELECT * FROM mandaja_stores WHERE slug=? AND active=1', (slug,)).fetchone()
    if not store:
        conn.close()
        return 'Loja não encontrada', 404
    store = dict(store)
    is_owner = session.get('mja_store_id') == store['id']
    if is_owner:
        _cozinha_ensure_pin(store, conn)   # dono logado gera o PIN na 1ª vez
    conn.close()
    if not _cozinha_autorizado(store):
        return render_template('mandaja/cozinha_pin.html', store=store,
                               erro=bool(request.args.get('pin')))
    resp = make_response(render_template('mandaja/cozinha.html', store=store, is_owner=is_owner))
    resp.set_cookie(f"coz_{store['id']}", store['kitchen_pin'] or '',
                    max_age=60 * 60 * 24 * 30, samesite='Lax')
    return resp


@app.route('/cozinha/<slug>/api')
def mandaja_cozinha_api(slug):
    """API de polling para a tela da cozinha — retorna pedidos ativos."""
    conn  = get_saas_db()
    store = conn.execute('SELECT id, kitchen_pin FROM mandaja_stores WHERE slug=? AND active=1', (slug,)).fetchone()
    if not store:
        conn.close()
        return jsonify({'error': 'not found'}), 404
    if not _cozinha_autorizado(dict(store)):
        conn.close()
        return jsonify({'error': 'auth'}), 401
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
    if not _cozinha_autorizado(store):
        conn.close()
        return jsonify({'error': 'auth'}), 401
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
    # Paywall: trial vencido e não pagou → loja indisponível
    if _mandaja_bloqueado(store):
        conn.close()
        return render_template('mandaja/jr_indisponivel.html', store=store), 503
    # Verifica se está aberto agora (Jr: interruptor; Pro: horário)
    is_open = _mandaja_loja_aberta(store, conn)
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
    try:
        delivery_zones = _json.loads(store.get('delivery_zones') or '[]')
    except Exception:
        delivery_zones = []
    return render_template('mandaja/loja.html',
                           store=store, cats=list(cats_dict.values()),
                           prods=prods_list, hours=hours_list,
                           weekdays=MANDAJA_WEEKDAYS, is_open=is_open,
                           delivery_zones=delivery_zones)


# ── Fazer pedido (POST da loja pública) ───────────────────────────────────────
@app.route('/loja/<slug>/pedido', methods=['POST'])
def mandaja_fazer_pedido(slug):
    conn  = get_saas_db()
    store = conn.execute('SELECT * FROM mandaja_stores WHERE slug=? AND active=1', (slug,)).fetchone()
    if not store:
        conn.close()
        return jsonify({'error': 'Loja não encontrada'}), 404
    store = dict(store)
    # Paywall: loja com trial vencido e não paga não aceita pedido
    if _mandaja_bloqueado(store):
        conn.close()
        return jsonify({'error': 'Esta loja está temporariamente indisponível.'}), 503
    # Loja fechada agora não aceita pedido (Jr: interruptor; Pro: horário)
    if not _mandaja_loja_aberta(store, conn):
        conn.close()
        return jsonify({'error': 'A loja está fechada agora. Volte no horário de funcionamento. 😊'}), 409
    data  = request.json or {}
    customer_name   = data.get('customer_name', '').strip()
    customer_phone  = data.get('customer_phone', '').strip()
    customer_notes  = data.get('customer_notes', '').strip()
    delivery_type   = data.get('delivery_type', 'delivery')
    address         = data.get('address', '').strip()
    addr_number     = data.get('number', '').strip()
    addr_complement = data.get('complement', '').strip()
    addr_reference  = data.get('reference', '').strip()
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
    # Pedido mínimo (só para entrega) — validado no servidor, não só no JS
    min_order = float(store.get('min_order') or 0)
    if delivery_type == 'delivery' and min_order > 0 and subtotal < min_order:
        conn.close()
        return jsonify({'error': f'Pedido mínimo para entrega é R$ {min_order:.2f}.'.replace('.', ',')}), 400
    # Taxa de entrega: por bairro (se a loja tiver zonas) ou taxa fixa
    delivery_fee = 0.0
    if delivery_type == 'delivery':
        try:
            zones = _json.loads(store.get('delivery_zones') or '[]')
        except Exception:
            zones = []
        if zones:
            zona = next((z for z in zones if (z.get('bairro') or '').strip().lower() == neighborhood.lower()), None)
            if not zona:
                conn.close()
                return jsonify({'error': 'Não entregamos nesse bairro. Escolha um bairro atendido ou retire no local.'}), 400
            delivery_fee = float(zona.get('taxa') or 0)
        else:
            delivery_fee = float(store['delivery_fee'] or 0)
    total        = subtotal + delivery_fee
    order_number = _mandaja_next_order_number(store['id'])
    cur = conn.execute('''
        INSERT INTO mandaja_orders
        (store_id, order_number, customer_name, customer_phone, customer_notes,
         delivery_type, address, address_number, address_complement, address_reference,
         neighborhood, city, cep,
         payment_method, subtotal, delivery_fee, total, change_for,
         status, items_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?)
    ''', (store['id'], order_number, customer_name, customer_phone, customer_notes,
          delivery_type, address, addr_number, addr_complement, addr_reference,
          neighborhood, city, cep,
          payment_method, subtotal, delivery_fee, total, change_for,
          _json.dumps(items, ensure_ascii=False),
          datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()
    order_id = cur.lastrowid
    # Token público pra página de acompanhamento (cliente não adivinha pedido alheio)
    import secrets as _sec
    track_token = _sec.token_urlsafe(8)
    conn.execute('UPDATE mandaja_orders SET track_token=? WHERE id=?', (track_token, order_id))
    conn.commit()
    conn.close()
    # Notificação WhatsApp (se loja tiver número configurado)
    if store.get('whatsapp'):
        endereco_txt, maps_url = _mandaja_endereco(address, addr_number, addr_complement,
                                                   neighborhood, city, addr_reference)
        _notify_new_order_whatsapp(store, order_id, order_number, customer_name,
                                   customer_phone, items, total, delivery_type,
                                   endereco_txt, maps_url, payment_method)
    # PIX direto: gera o copia-e-cola com o valor já preenchido + QR
    pix_payload, pix_qr_b64 = '', ''
    if store.get('pix_chave') and payment_method == 'pix':
        pix_nome = store.get('pix_nome') or store.get('name', '')
        txid     = ''.join(c for c in order_number if c.isalnum()) or 'PED'
        pix_payload = _pix_brcode(store['pix_chave'], pix_nome,
                                  store.get('city', ''), total, txid)
        if pix_payload:
            try:
                import qrcode as _qr, io as _io, base64 as _b64
                q = _qr.QRCode(error_correction=_qr.constants.ERROR_CORRECT_M, box_size=7, border=2)
                q.add_data(pix_payload); q.make(fit=True)
                _buf = _io.BytesIO()
                q.make_image(fill_color='black', back_color='white').save(_buf, format='PNG')
                pix_qr_b64 = _b64.b64encode(_buf.getvalue()).decode()
            except Exception as _qe:
                log.warning(f'[MandaJá] PIX QR error: {_qe}')
    return jsonify({'ok': True, 'order_id': order_id, 'order_number': order_number,
                    'total': total, 'pix_chave': store.get('pix_chave', ''),
                    'pix_nome': store.get('pix_nome', ''),
                    'pix_payload': pix_payload, 'pix_qr': pix_qr_b64,
                    'track_url': f"{request.host_url}acompanhar/{track_token}"})


def _mandaja_endereco(address, number, complement, neighborhood, city, reference):
    """Monta o endereço legível + o link do Google Maps pro motoboy (sem geocoding)."""
    from urllib.parse import quote
    linha = (address or '') + (f", {number}" if number else "")
    extras = [x for x in [complement, neighborhood, city] if x]
    texto = linha + ((" · " + " · ".join(extras)) if extras else "")
    if reference:
        texto += f"\n📌 Referência: {reference}"
    q = ", ".join(x for x in [linha, neighborhood, city] if x).strip(', ')
    maps = ('https://www.google.com/maps/search/?api=1&query=' + quote(q)) if q else ''
    return texto, maps


def _notify_new_order_whatsapp(store, order_id, order_number, customer_name,
                                customer_phone, items, total, delivery_type,
                                endereco_txt, maps_url, payment_method):
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
        if delivery_type == 'delivery':
            delivery_text = f"🚚 *Entrega:* {endereco_txt}"
            if maps_url:
                delivery_text += f"\n🗺️ Abrir no Maps: {maps_url}"
        else:
            delivery_text = "🏠 Retirada no local"
        pay_map = {'pix': '💳 PIX', 'dinheiro': '💵 Dinheiro', 'cartao': '💳 Cartão'}
        msg = (f"🛍️ *NOVO PEDIDO {order_number}*\n\n"
               f"👤 {customer_name} — {customer_phone}\n\n"
               f"🛒 Itens:\n{items_text}\n\n"
               f"💰 Total: R${total:.2f}\n"
               f"💳 Pagamento: {pay_map.get(payment_method, payment_method)}\n"
               f"{delivery_text}\n\n"
               f"🔗 Ver pedido: {request.host_url}mandaja/pedidos/{order_id}")
        phone_clean = _re.sub(r'\D', '', store['whatsapp'])
        if phone_clean and not phone_clean.startswith('55'):
            phone_clean = '55' + phone_clean   # Evolution exige o código do país
        requests.post(
            f"{EVO_URL}/message/sendText/{INSTANCE}",
            headers={'apikey': EVO_KEY, 'Content-Type': 'application/json'},
            json={'number': phone_clean, 'text': msg},
            timeout=8
        )
    except Exception as e:
        log.warning(f"[MandaJá] WhatsApp notify error: {e}")


# ── Acompanhar pedido (público, via token) ───────────────────────────────────
@app.route('/acompanhar/<token>')
def mandaja_acompanhar(token):
    conn  = get_saas_db()
    order = conn.execute('SELECT * FROM mandaja_orders WHERE track_token=?', (token,)).fetchone()
    if not order:
        conn.close()
        return render_template('mandaja/loja_404.html'), 404
    order = dict(order)
    store = conn.execute(
        'SELECT name, slug, cor_primaria, whatsapp, delivery_time FROM mandaja_stores WHERE id=?',
        (order['store_id'],)).fetchone()
    conn.close()
    order['items'] = _json.loads(order.get('items_json') or '[]')
    return render_template('mandaja/acompanhar.html',
                           order=order, store=dict(store) if store else {})


@app.route('/acompanhar/<token>/status')
def mandaja_acompanhar_status(token):
    """Polling do status pro cliente (página de acompanhamento)."""
    conn = get_saas_db()
    row  = conn.execute('SELECT status FROM mandaja_orders WHERE track_token=?', (token,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'status': row['status']})


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
# PCD Fácil — Isenção PCD guiada por IA (carro 0km / IPVA)
# ══════════════════════════════════════════════════════════════════════════════
try:
    from pcd import pcd_bp
    from pcd_db import init_pcd_db
    init_pcd_db()
    app.register_blueprint(pcd_bp)
    log.info('[PCD] Blueprint registrado em /pcd')
except Exception as _pcd_err:
    log.warning(f'[PCD] Erro ao carregar blueprint: {_pcd_err}')

# VetZap Bot — "Uber do Veterinário" no WhatsApp (Fase 1)
try:
    from vetzap_bot import vetzap_bp
    app.register_blueprint(vetzap_bp)
    log.info('[VetZap] Bot blueprint registrado em /vetzap')
except Exception as _vz_err:
    log.warning(f'[VetZap] Erro ao carregar bot blueprint: {_vz_err}')

# ══════════════════════════════════════════════════════════════════════════════
# DRZAP — Assistente Jurídico por IA (orientação ao consumidor) — créditos pré-pagos
# ══════════════════════════════════════════════════════════════════════════════
try:
    from drzap import drzap_bp
    from drzap_db import init_drzap_db
    init_drzap_db()
    app.register_blueprint(drzap_bp)
    log.info('[DRZAP] Blueprint registrado em /drzap')
except Exception as _e:
    log.error(f'[DRZAP] Falha ao registrar: {_e}')

try:
    from somaja import somaja_bp
    from somaja_db import init_somaja_db
    init_somaja_db()
    app.register_blueprint(somaja_bp)
    log.info('[SomaJá] Blueprint registrado em /somaja')
except Exception as _soma_err:
    log.warning(f'[SomaJá] Erro ao carregar blueprint: {_soma_err}')

try:
    from afiliados import afiliados_bp
    from afiliados_db import init_afil_db
    init_afil_db()
    app.register_blueprint(afiliados_bp)
    log.info('[Afiliados] Blueprint registrado em /afiliados')
except Exception as _afil_err:
    log.warning(f'[Afiliados] Erro ao carregar blueprint: {_afil_err}')

# ══════════════════════════════════════════════════════════════════════════════
# RADAR — Monitor de Licitações de TI (PNCP) — Lote 0+1
# ══════════════════════════════════════════════════════════════════════════════
try:
    from radar import radar_bp, iniciar_coletor_automatico
    from radar_db import init_radar_db
    init_radar_db()
    app.register_blueprint(radar_bp)
    iniciar_coletor_automatico()   # Lote 2: o Radar coleta sozinho (RADAR_AUTO_COLETA=0 desliga)
    log.info('[RADAR] Blueprint registrado em /radar')
except Exception as _radar_err:
    log.warning(f'[RADAR] Erro ao carregar blueprint: {_radar_err}')

# ══════════════════════════════════════════════════════════════════════════════
# RADAR LICITA NORTE — versão regional (Norte de SC), reusa o motor do Radar
# ══════════════════════════════════════════════════════════════════════════════
try:
    from licita_norte import licita_bp, iniciar_coletor_sc
    app.register_blueprint(licita_bp)
    iniciar_coletor_sc()   # coleta SC sozinho a cada 8h (LICITA_AUTO_COLETA=0 desliga)
    log.info('[LICITA NORTE] Blueprint registrado em /licita-norte')
except Exception as _lic_err:
    log.warning(f'[LICITA NORTE] Erro ao carregar blueprint: {_lic_err}')

# ══════════════════════════════════════════════════════════════════════════════
# CONSULTA VEICULAR — débitos do veículo (IPVA/multas/licenciamento) via API Zapay
# ══════════════════════════════════════════════════════════════════════════════
try:
    from consveic import consveic_bp
    from consveic_db import init_consveic_db
    init_consveic_db()
    app.register_blueprint(consveic_bp)
    log.info('[CONSVEIC] Blueprint registrado em /consulta-veicular')
except Exception as _cv_err:
    log.warning(f'[CONSVEIC] Erro ao carregar blueprint: {_cv_err}')

# Rebrand: redireciona URLs antigas /petmed/* -> /vetzap/* (links/e-mails/webhooks antigos)
@app.route('/petmed', methods=['GET', 'POST'])
@app.route('/petmed/', methods=['GET', 'POST'])
@app.route('/petmed/<path:sub>', methods=['GET', 'POST'])
def _vetzap_legacy_redirect(sub=''):
    qs = ('?' + request.query_string.decode('utf-8')) if request.query_string else ''
    # 307 preserva método+corpo (POST de webhooks antigos não quebra); 301 p/ GET (links/SEO)
    code = 307 if request.method == 'POST' else 301
    return redirect('/vetzap/' + sub + qs, code=code)

# ══════════════════════════════════════════════════════════════════════════════
# PUBSHOW — Jukebox digital para bares e pubs
# ══════════════════════════════════════════════════════════════════════════════
try:
    from pubshow import pubshow_bp, iniciar_faxineiro_videos
    app.register_blueprint(pubshow_bp)
    log.info('[PUBSHOW] Blueprint registrado em /pubshow')
    # Faxineiro automático: desativa vídeos quebrados sozinho, a cada 12h
    try:
        iniciar_faxineiro_videos()
    except Exception as _fx_err:
        log.warning(f'[PUBSHOW] Faxineiro não iniciou: {_fx_err}')
except Exception as _ps_err:
    log.warning(f'[PUBSHOW] Erro ao carregar blueprint: {_ps_err}')

# PUBSHOW EN — versão internacional (inglês / USD)
try:
    from pubshow_en import pubshow_en_bp
    app.register_blueprint(pubshow_en_bp)
    log.info('[PUBSHOW EN] Blueprint registrado em /pubshow-en')
except Exception as _ps_en_err:
    log.warning(f'[PUBSHOW EN] Erro ao carregar blueprint: {_ps_en_err}')

# ══════════════════════════════════════════════════════════════════════════════
# MLhype — Inteligência para vendedores do Mercado Livre — Passo 1 (fundação)
# ══════════════════════════════════════════════════════════════════════════════
try:
    from mlhype import mlhype_bp, iniciar_coletor_mlhype
    from mlhype_db import init_mlhype_db
    init_mlhype_db()
    app.register_blueprint(mlhype_bp)
    iniciar_coletor_mlhype()   # Passo 3: coletor diário do ML (MLHYPE_AUTO_COLETA=0 desliga)
    log.info('[MLhype] Blueprint registrado em /mlhype')
except Exception as _mlhype_err:
    log.warning(f'[MLhype] Erro ao carregar blueprint: {_mlhype_err}')

# ══════════════════════════════════════════════════════════════════════════════
# AMPARO — Engajamento entre sessões para psicólogos — Lote 0 (Fundação)
# ══════════════════════════════════════════════════════════════════════════════
try:
    from amparo import amparo_bp, iniciar_lembretes_amparo
    from amparo_db import init_amparo_db
    init_amparo_db()
    app.register_blueprint(amparo_bp)
    iniciar_lembretes_amparo()   # lembrete de sessão ~24h antes (AMPARO_LEMBRETES=0 desliga)
    log.info('[Amparo] Blueprint registrado em /amparo')
except Exception as _amparo_err:
    log.warning(f'[Amparo] Erro ao carregar blueprint: {_amparo_err}')

# ══════════════════════════════════════════════════════════════════════════════
# ATENDEZAP — Bot de atendimento no WhatsApp (B2B, pronto-por-nicho) — Lote 0
# ══════════════════════════════════════════════════════════════════════════════
try:
    from atendezap import atendezap_bp
    from atendezap_db import init_atende_db
    init_atende_db()
    app.register_blueprint(atendezap_bp)
    log.info('[AtendeZap] Blueprint registrado em /atendezap')
except Exception as _atende_err:
    log.warning(f'[AtendeZap] Erro ao carregar blueprint: {_atende_err}')

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
    # Inicializa banco PUBSHOW EN (internacional)
    try:
        from pubshow_en_db import init_pubshow_db as _init_pubshow_en_db
        _init_pubshow_en_db()
        log.info('[PUBSHOW EN] Banco inicializado com sucesso')
    except Exception as _e:
        log.error(f'[PUBSHOW EN] ERRO ao inicializar banco: {_e}', exc_info=True)

# ══════════════════════════════════════════════════════════════════════════════
#  SLOTZAP — Venda de slots numerados com PIX automático via Asaas
# ══════════════════════════════════════════════════════════════════════════════

def _sz_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('sz_user_id'):
            base = '/rifaja' if request.path.startswith('/rifaja') else '/slotzap'
            return redirect(f'{base}/entrar')
        return f(*args, **kwargs)
    return decorated

def _sz_uid():
    return session.get('sz_user_id')

def _sz_plan_active() -> bool:
    """True se o usuário logado tem assinatura ativa."""
    uid = _sz_uid()
    if not uid:
        return False
    conn = get_saas_db()
    u = conn.execute('SELECT plan_active FROM slotzap_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return bool(u and dict(u).get('plan_active'))

def _cpf_valido(cpf) -> bool:
    """Valida CPF pelos dígitos verificadores (não só o tamanho)."""
    cpf = ''.join(c for c in (cpf or '') if c.isdigit())
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[n]) * ((i + 1) - n) for n in range(i))
        dig  = (soma * 10) % 11 % 10
        if dig != int(cpf[i]):
            return False
    return True

_sz_reserve_hits = {}  # ip -> [timestamps] (anti-abuso da reserva pública)
def _sz_rate_ok(ip, limite=20, janela=900) -> bool:
    """Limita tentativas de reserva por IP (default: 20 em 15 min)."""
    import time as _t
    now  = _t.time()
    hits = [t for t in _sz_reserve_hits.get(ip, []) if now - t < janela]
    if len(hits) >= limite:
        _sz_reserve_hits[ip] = hits
        return False
    hits.append(now)
    _sz_reserve_hits[ip] = hits
    if len(_sz_reserve_hits) > 5000:   # limpeza leve para não crescer sem fim
        _sz_reserve_hits.clear()
    return True


@app.route('/rifaja')
def rifaja_landing():
    """Landing da RifaJá (fachada própria sobre o motor SlotZap, gateway Efí)."""
    return render_template('rifaja/landing.html')


@app.route('/slotzap')
def slotzap_landing():
    return redirect('/slotzap/planos')


@app.route('/slotzap/planos')
def slotzap_planos():
    return render_template('slotzap/planos.html', planos=SLOTZAP_PLANS)


@app.route('/slotzap/cadastro', methods=['GET', 'POST'])
@app.route('/slotzap/cadastro/<plano>', methods=['GET', 'POST'])
def slotzap_cadastro(plano='start'):
    if plano not in SLOTZAP_PLANS:
        plano = 'start'
    erro = None
    if request.method == 'POST':
        plano    = request.form.get('plano', plano)
        if plano not in SLOTZAP_PLANS:
            plano = 'start'
        name     = (request.form.get('name') or '').strip()
        email    = (request.form.get('email') or '').strip().lower()
        senha    = request.form.get('senha') or ''
        phone    = (request.form.get('phone') or '').strip()
        cpf_cnpj = (request.form.get('cpf_cnpj') or '').strip()
        cpf_digits = ''.join(c for c in cpf_cnpj if c.isdigit())
        if not all([name, email, senha, phone, cpf_cnpj]):
            erro = 'Preencha todos os campos.'
        elif len(senha) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        elif len(cpf_digits) not in (11, 14):
            erro = 'CPF deve ter 11 dígitos ou CNPJ 14 dígitos.'
        elif len(cpf_digits) == 11 and not _cpf_valido(cpf_digits):
            erro = 'CPF inválido. Confira os números.'
        else:
            conn = get_saas_db()
            if conn.execute('SELECT id FROM slotzap_users WHERE email=?', (email,)).fetchone():
                erro = 'E-mail já cadastrado. Faça login.'
                conn.close()
            else:
                cur = conn.execute(
                    'INSERT INTO slotzap_users (name,email,phone,cpf_cnpj,password_hash,plan,plan_active,active,created_at) '
                    'VALUES (?,?,?,?,?,?,0,1,?)',
                    (name, email, phone, cpf_cnpj, generate_password_hash(senha), plano, datetime.now().isoformat())
                )
                conn.commit()
                uid = cur.lastrowid
                conn.close()
                session['sz_user_id']   = uid
                session['sz_user_name'] = name
                return redirect(f'/slotzap/assinar/{plano}')
    return render_template('slotzap/cadastro.html', erro=erro, plano=plano, planos=SLOTZAP_PLANS)


@app.route('/slotzap/assinar', methods=['GET', 'POST'])
@app.route('/slotzap/assinar/<plano>', methods=['GET', 'POST'])
@_sz_login_required
def slotzap_assinar(plano=None):
    uid = _sz_uid()
    conn = get_saas_db()
    u = conn.execute('SELECT * FROM slotzap_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    if not u:
        return redirect('/slotzap/entrar')
    u = dict(u)
    if plano is None:
        plano = u.get('plan') or 'start'
    if plano not in SLOTZAP_PLANS:
        plano = 'start'
    p = SLOTZAP_PLANS[plano]
    combo       = _combo_desconto_ativo(u['email'], 'slotzap')
    preco_final = round(p['price'] * (1 - COMBO_DESCONTO), 2) if combo else float(p['price'])
    erro = None
    if request.method == 'POST':
        customer_id = _asaas_criar_ou_buscar_cliente_saas(
            u['name'], u['email'], u.get('phone', ''), u.get('cpf_cnpj', ''), u['id'], 'slotzap_users')
        if not customer_id:
            erro = 'Erro ao processar o pagamento. Confira seu CPF/CNPJ e tente novamente.'
        else:
            conn2 = get_saas_db()
            conn2.execute('UPDATE slotzap_users SET asaas_customer_id=?, plan=? WHERE id=?',
                          (customer_id, plano, uid))
            conn2.commit(); conn2.close()
            desc = f'SlotZap {p["label"]} — Assinatura Mensal' + (' (combo -25%)' if combo else '')
            resp = _asaas_criar_assinatura_saas(
                customer_id, 'slotzap', plano, preco_final, desc, 'PIX')
            if resp.get('id'):
                pix = _asaas_get_pix_qr(resp['id'])
                return render_template('slotzap/aguardando.html', pix=pix, p=p)
            erro = 'Não foi possível gerar a cobrança. Tente novamente.'
    return render_template('slotzap/assinar.html', plano=plano, p=p,
                           planos=SLOTZAP_PLANS, erro=erro, user_name=u['name'],
                           combo=combo, preco_final=preco_final)


@app.route('/slotzap/aguardando-pagamento')
@_sz_login_required
def slotzap_aguardando():
    return render_template('slotzap/aguardando.html')


@app.route('/slotzap/assinatura-status')
@_sz_login_required
def slotzap_assinatura_status():
    return jsonify({'ativo': _sz_plan_active()})


@app.route('/slotzap/entrar', methods=['GET', 'POST'], defaults={'brand': 'slotzap'})
@app.route('/rifaja/entrar', methods=['GET', 'POST'], defaults={'brand': 'rifaja'})
def slotzap_entrar(brand='slotzap'):
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
            return redirect('/rifaja/nova' if brand == 'rifaja' else '/slotzap/app')
    return render_template('slotzap/entrar.html', erro=erro, brand=brand)


@app.route('/slotzap/sair')
def slotzap_sair():
    session.pop('sz_user_id', None)
    session.pop('sz_user_name', None)
    return redirect('/slotzap/entrar')


@app.route('/slotzap/recuperar-senha', methods=['GET', 'POST'])
def slotzap_recuperar_senha():
    fase        = 'pedir'
    erro        = None
    sucesso     = False
    codigo_tela = None
    email_in    = ''
    if request.method == 'POST':
        etapa    = request.form.get('etapa', 'pedir')
        email_in = (request.form.get('email') or '').strip().lower()
        if etapa == 'pedir':
            conn = get_saas_db()
            u = conn.execute('SELECT * FROM slotzap_users WHERE email=?', (email_in,)).fetchone()
            if not u:
                erro = 'E-mail não encontrado.'
                conn.close()
            else:
                codigo  = str(random.randint(100000, 999999))
                expires = (datetime.now() + timedelta(hours=2)).isoformat()
                conn.execute('UPDATE slotzap_users SET reset_token=?, reset_expires=? WHERE id=?',
                             (codigo, expires, u['id']))
                conn.commit(); conn.close()
                html_email = f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
                  <div style="font-size:32px;margin-bottom:8px">🎯</div>
                  <h2 style="color:#6366f1">Recuperação de senha — SlotZap</h2>
                  <p>Olá, <strong>{u['name'].split()[0]}</strong>!</p>
                  <p>Seu código de recuperação é:</p>
                  <div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#6366f1;
                              background:#eef2ff;padding:20px;border-radius:12px;text-align:center;margin:20px 0">{codigo}</div>
                  <p style="color:#666;font-size:13px">Válido por 2 horas.</p>
                </div>"""
                ok = _enviar_email(u['email'], 'Código de recuperação — SlotZap', html_email)
                fase = 'redefinir'
                if not ok:
                    codigo_tela = codigo
        else:  # redefinir
            codigo = (request.form.get('codigo') or '').strip()
            nova   = request.form.get('nova_senha') or ''
            fase   = 'redefinir'
            if len(nova) < 6:
                erro = 'A senha deve ter pelo menos 6 caracteres.'
            else:
                conn = get_saas_db()
                u = conn.execute('SELECT * FROM slotzap_users WHERE email=?', (email_in,)).fetchone()
                if not u or (u['reset_token'] or '') != codigo:
                    erro = 'Código inválido ou e-mail incorreto.'
                    conn.close()
                elif u['reset_expires'] and datetime.fromisoformat(u['reset_expires']) < datetime.now():
                    erro = 'Código expirado. Solicite um novo.'
                    conn.close()
                else:
                    conn.execute("UPDATE slotzap_users SET password_hash=?, reset_token='', reset_expires='' WHERE id=?",
                                 (generate_password_hash(nova), u['id']))
                    conn.commit(); conn.close()
                    sucesso = True
                    fase    = 'pedir'
    return render_template('slotzap/recuperar_senha.html',
                           fase=fase, erro=erro, sucesso=sucesso,
                           codigo_tela=codigo_tela, email_in=email_in)


@app.route('/slotzap/app')
@_sz_login_required
def slotzap_app():
    if not _sz_plan_active():
        return redirect('/slotzap/assinar')
    conn = get_saas_db()
    campanhas = [dict(r) for r in conn.execute('''
        SELECT c.*,
            (SELECT COUNT(*) FROM slotzap_slots s WHERE s.campanha_id=c.id AND s.status="pago")      AS pagos,
            (SELECT COUNT(*) FROM slotzap_slots s WHERE s.campanha_id=c.id AND s.status="pago" AND IFNULL(s.brinde,0)=0) AS pagos_reais,
            (SELECT COUNT(*) FROM slotzap_slots s WHERE s.campanha_id=c.id AND s.status="reservado") AS reservados,
            (SELECT COUNT(*) FROM slotzap_slots s WHERE s.campanha_id=c.id AND s.status="disponivel") AS disponiveis
        FROM slotzap_campanhas c
        WHERE c.user_id=?
        ORDER BY c.id DESC
    ''', (_sz_uid(),)).fetchall()]
    _u = dict(conn.execute('SELECT asaas_wallet_id FROM slotzap_users WHERE id=?',
                           (_sz_uid(),)).fetchone() or {})
    conn.close()
    tem_wallet       = bool((_u.get('asaas_wallet_id') or '').strip())
    total_arrecadado = sum((c['pagos_reais'] or 0) * float(c['preco'] or 0) for c in campanhas)
    total_vendidos   = sum((c['pagos'] or 0) for c in campanhas)
    ativas           = sum(1 for c in campanhas if c['status'] == 'ativa')
    return render_template('slotzap/app.html',
                           campanhas=campanhas,
                           total_arrecadado=total_arrecadado,
                           total_vendidos=total_vendidos,
                           ativas=ativas,
                           tem_wallet=tem_wallet,
                           taxa=int(SZ_TAXA_VENDA * 100),
                           user_name=session.get('sz_user_name', ''))


@app.route('/slotzap/configuracoes', methods=['GET', 'POST'])
@_sz_login_required
def slotzap_config():
    if not _sz_plan_active():
        return redirect('/slotzap/assinar')
    uid  = _sz_uid()
    msg  = None
    conn = get_saas_db()
    is_pro = (dict(conn.execute('SELECT plan FROM slotzap_users WHERE id=?', (uid,)).fetchone() or {}).get('plan') == 'pro')
    if request.method == 'POST':
        wallet = (request.form.get('wallet_id') or '').strip()
        conn.execute('UPDATE slotzap_users SET asaas_wallet_id=? WHERE id=?', (wallet, uid))
        if is_pro:  # white-label só no Pro
            marca = (request.form.get('marca') or '').strip()[:40]
            cor   = (request.form.get('cor') or '').strip()
            if cor and not (cor.startswith('#') and len(cor) in (4, 7)):
                cor = ''
            conn.execute('UPDATE slotzap_users SET marca=?, cor=? WHERE id=?', (marca, cor, uid))
        conn.commit()
        msg = 'Configuração salva!'
    u = dict(conn.execute('SELECT name, email, asaas_wallet_id, plan, marca, cor FROM slotzap_users WHERE id=?',
                          (uid,)).fetchone())
    conn.close()
    return render_template('slotzap/configuracoes.html', u=u, msg=msg,
                           taxa=int(SZ_TAXA_VENDA * 100), is_pro=is_pro)


@app.route('/slotzap/guia')
@_sz_login_required
def slotzap_guia():
    conn = get_saas_db()
    u = dict(conn.execute('SELECT plan FROM slotzap_users WHERE id=?', (_sz_uid(),)).fetchone() or {})
    conn.close()
    return render_template('slotzap/guia.html', planos=SLOTZAP_PLANS,
                           taxa=int(SZ_TAXA_VENDA * 100), plan=u.get('plan', 'start'))


@app.route('/slotzap/nova', methods=['GET', 'POST'], defaults={'brand': 'slotzap'})
@app.route('/rifaja/nova', methods=['GET', 'POST'], defaults={'brand': 'rifaja'})
@_sz_login_required
def slotzap_nova(brand='slotzap'):
    if not _sz_plan_active():
        return redirect('/slotzap/assinar')
    erro = None
    if request.method == 'POST':
        nome    = request.form.get('nome', '').strip()
        descr   = request.form.get('descricao', '').strip()[:180]  # "menos é mais": descrição curta
        imagem  = request.form.get('imagem', '').strip()[:300]      # foto do prêmio (URL do upload AJAX)
        # Preço aceita vírgula (34,90) e ponto de milhar (1.234,56)
        preco_raw = (request.form.get('preco') or '0').strip()
        if ',' in preco_raw:
            preco_raw = preco_raw.replace('.', '').replace(',', '.')
        try:
            preco = float(preco_raw or 0)
        except ValueError:
            preco = 0
        try:
            total = int(float((request.form.get('total_slots') or '0').replace(',', '.')))
        except ValueError:
            total = 0
        try:
            inicio = int(float((request.form.get('slots_inicio') or '1').replace(',', '.')))
        except ValueError:
            inicio = 1
        if not nome or preco <= 0 or total < 2:
            erro = 'Nome, preço e quantidade são obrigatórios.'
        elif total > 5000:
            erro = 'Quantidade máxima de 5.000 números por campanha.'
        elif preco < 5:
            erro = 'O valor mínimo por número é R$ 5,00 (exigência do Asaas para gerar PIX).'
        else:
            import secrets as _sec
            token_pub = _sec.token_urlsafe(16)
            gw = 'efi' if brand == 'rifaja' else 'asaas'
            # RifaJá = estratégia de exército de afiliados → já nasce com Vendedores LIGADO
            # e comissão default de ~40% do preço (editável depois em Vendedores). "menos é mais".
            afil_ativo    = 1 if brand == 'rifaja' else 0
            afil_comissao = round(preco * 0.40, 2) if brand == 'rifaja' else 3.0
            conn = get_saas_db()
            cur  = conn.execute(
                'INSERT INTO slotzap_campanhas (user_id,nome,descricao,preco,total_slots,slots_inicio,status,created_at,token_publico,gateway,imagem,afiliados_ativo,afiliado_comissao) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (_sz_uid(), nome, descr, preco, total, inicio, 'ativa', datetime.now().isoformat(), token_pub, gw, imagem, afil_ativo, afil_comissao)
            )
            camp_id = cur.lastrowid
            for n in range(inicio, inicio + total):
                conn.execute('INSERT OR IGNORE INTO slotzap_slots (campanha_id,numero,status) VALUES (?,?,?)',
                             (camp_id, n, 'disponivel'))
            conn.commit(); conn.close()
            return redirect(f'/slotzap/campanha/{camp_id}')
    return render_template('slotzap/nova.html', erro=erro, brand=brand)


# Foto do prêmio: guardada no volume persistente (DATA_DIR), não some no redeploy
SLOTZAP_UPLOAD_DIR = os.path.join(
    os.environ.get('DATA_DIR', os.path.dirname(__file__)), 'uploads', 'slotzap')


@app.route('/uploads/slotzap/<path:filename>')
def slotzap_uploaded_file(filename):
    return send_from_directory(SLOTZAP_UPLOAD_DIR, filename)


@app.route('/slotzap/upload-imagem', methods=['POST'])
@_sz_login_required
def slotzap_upload_imagem():
    """Recebe a foto do prêmio e comprime forte (foto do celular → ~60KB WebP)."""
    f = request.files.get('imagem')
    if not f or not f.filename:
        return jsonify({'error': 'Nenhuma imagem enviada.'}), 400
    try:
        from PIL import Image, ImageOps
        import secrets as _sec
        img = Image.open(f.stream)
        img = ImageOps.exif_transpose(img)   # corrige foto de celular girada
        img = img.convert('RGB')
        maxd = 900
        w, h = img.size
        if max(w, h) > maxd:
            if w >= h:
                img = img.resize((maxd, round(h * maxd / w)), Image.LANCZOS)
            else:
                img = img.resize((round(w * maxd / h), maxd), Image.LANCZOS)
        os.makedirs(SLOTZAP_UPLOAD_DIR, exist_ok=True)
        name = f"{_sz_uid()}_{_sec.token_urlsafe(6)}.webp"
        img.save(os.path.join(SLOTZAP_UPLOAD_DIR, name), 'WEBP', quality=78, method=6)
        return jsonify({'ok': True, 'url': f'/uploads/slotzap/{name}'})
    except Exception as e:
        log.warning(f'[SlotZap] upload imagem error: {e}')
        return jsonify({'error': 'Não consegui processar essa imagem. Tente outra foto.'}), 400


@app.route('/slotzap/campanha/<int:camp_id>')
@_sz_login_required
def slotzap_campanha(camp_id):
    if not _sz_plan_active():
        return redirect('/slotzap/assinar')
    _sz_expirar_reservas(camp_id)
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
    brand = 'rifaja' if camp.get('gateway') == 'efi' else 'slotzap'
    return render_template('slotzap/campanha.html',
                           camp=camp, slots=slots,
                           pagos=pagos, reservados=reservados,
                           disponiveis=disponiveis, receita=receita, brand=brand)


@app.route('/slotzap/campanha/<int:camp_id>/exportar')
@_sz_login_required
def slotzap_exportar(camp_id):
    """Exporta a lista de compradores da campanha em CSV (para o dono operar/entregar)."""
    if not _sz_plan_active():
        return redirect('/slotzap/assinar')
    conn = get_saas_db()
    camp = conn.execute('SELECT nome FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return redirect('/slotzap/app')
    slots = [dict(r) for r in conn.execute(
        'SELECT numero, status, cliente_nome, cliente_tel, reservado_em, pago_em '
        'FROM slotzap_slots WHERE campanha_id=? ORDER BY numero', (camp_id,)).fetchall()]
    conn.close()
    buf = io.StringIO()
    w   = csv.writer(buf, delimiter=';')
    w.writerow(['Numero', 'Status', 'Cliente', 'WhatsApp', 'Reservado em', 'Pago em'])
    st_map = {'disponivel': 'Disponível', 'reservado': 'Reservado', 'pago': 'Pago'}
    for s in slots:
        w.writerow([s['numero'], st_map.get(s['status'], s['status']),
                    s['cliente_nome'] or '', s['cliente_tel'] or '',
                    s['reservado_em'] or '', s['pago_em'] or ''])
    nome_arq = (''.join(c for c in dict(camp)['nome'] if c.isalnum() or c in ' -_')
                .strip().replace(' ', '_') or 'campanha')
    conteudo = '﻿' + buf.getvalue()  # BOM p/ o Excel abrir acentos corretamente
    return Response(conteudo, mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename=slotzap_{nome_arq}.csv'})


@app.route('/slotzap/campanha/<int:camp_id>/editar', methods=['POST'])
@_sz_login_required
def slotzap_editar(camp_id):
    """Edita nome/descrição/preço e permite AUMENTAR a quantidade de números."""
    if not _sz_plan_active():
        return jsonify({'erro': 'Assinatura inativa.'}), 402
    data  = request.get_json() or {}
    nome  = (data.get('nome') or '').strip()
    descr = (data.get('descricao') or '').strip()[:180]  # "menos é mais": descrição curta
    imagem = (data.get('imagem') or '').strip()[:300]    # foto do prêmio (URL do upload AJAX)
    try:    preco = float(data.get('preco') or 0)
    except (TypeError, ValueError): preco = 0
    try:    novo_total = int(data.get('total_slots') or 0)
    except (TypeError, ValueError): novo_total = 0
    # Data do sorteio: aceita 'YYYY-MM-DDTHH:MM' (datetime-local) ou vazio p/ remover
    data_sorteio = (data.get('data_sorteio') or '').strip()[:16]
    # Indicação premiada
    indic_ativa = 1 if data.get('indicacao_ativa') else 0
    try:    indic_meta = int(data.get('indicacao_meta') or 10)
    except (TypeError, ValueError): indic_meta = 10
    indic_meta = max(1, min(1000, indic_meta))
    # Custo do prêmio (aceita vírgula) — trava o sorteio até cobrir
    custo_raw = str(data.get('custo_premio') or '0').strip()
    if ',' in custo_raw:
        custo_raw = custo_raw.replace('.', '').replace(',', '.')
    try:    custo_premio = max(0.0, float(custo_raw or 0))
    except (TypeError, ValueError): custo_premio = 0.0
    # Trava do sorteio (a senha agora é a de LOGIN da conta — nada a guardar por campanha)
    so_esgotado = 1 if data.get('sortear_so_esgotado') else 0
    if not nome:
        return jsonify({'erro': 'Nome obrigatório'}), 400
    if preco < 5:
        return jsonify({'erro': 'O valor mínimo por número é R$ 5,00.'}), 400

    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    camp = dict(camp)
    conn.execute('UPDATE slotzap_campanhas SET nome=?, descricao=?, preco=?, data_sorteio=?, '
                 'indicacao_ativa=?, indicacao_meta=?, custo_premio=?, imagem=?, '
                 'sortear_so_esgotado=? WHERE id=?',
                 (nome, descr, preco, data_sorteio, indic_ativa, indic_meta, custo_premio, imagem,
                  so_esgotado, camp_id))
    add = 0
    if novo_total and novo_total > camp['total_slots']:
        inicio = camp['slots_inicio'] or 1
        for n in range(inicio + camp['total_slots'], inicio + novo_total):
            conn.execute('INSERT OR IGNORE INTO slotzap_slots (campanha_id,numero,status) VALUES (?,?,?)',
                         (camp_id, n, 'disponivel'))
        conn.execute('UPDATE slotzap_campanhas SET total_slots=? WHERE id=?', (novo_total, camp_id))
        add = novo_total - camp['total_slots']
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'numeros_adicionados': add})


@app.route('/slotzap/campanha/<int:camp_id>/cancelar-estornar', methods=['POST'])
@_sz_login_required
def slotzap_cancelar_estornar(camp_id):
    """Cancela a campanha e ESTORNA no Asaas todos os PIX pagos (devolve o dinheiro).
    Também cancela cobranças pendentes. Protegido pela senha de LOGIN da conta."""
    if not _sz_plan_active():
        return jsonify({'erro': 'Assinatura inativa.'}), 402
    data = request.get_json() or {}
    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    camp = dict(camp)
    # Estorno SEMPRE exige a senha da CONTA (ação destrutiva: devolve dinheiro de verdade)
    u = conn.execute('SELECT password_hash FROM slotzap_users WHERE id=?', (_sz_uid(),)).fetchone()
    if not u or not check_password_hash(dict(u)['password_hash'], data.get('senha') or ''):
        conn.close()
        return jsonify({'erro': 'senha_errada', 'msg': 'Senha da conta incorreta.'}), 403
    # Charges PAGOS (exclui brindes — não houve pagamento) e PENDENTES (reservados)
    pagos_ch = [dict(r)['asaas_charge_id'] for r in conn.execute(
        "SELECT DISTINCT asaas_charge_id FROM slotzap_slots WHERE campanha_id=? "
        "AND status='pago' AND IFNULL(brinde,0)=0 AND asaas_charge_id<>''", (camp_id,)).fetchall()]
    pend_ch = [dict(r)['asaas_charge_id'] for r in conn.execute(
        "SELECT DISTINCT asaas_charge_id FROM slotzap_slots WHERE campanha_id=? "
        "AND status='reservado' AND asaas_charge_id<>''", (camp_id,)).fetchall()]
    conn.close()

    gateway = camp.get('gateway', 'asaas')   # estorna pelo MESMO gateway que recebeu
    estornados, falhas = 0, 0
    for cid in pagos_ch:
        try:
            if gateway == 'efi':
                import efi_pix
                info = efi_pix.consultar_e2e(cid)
                if info.get('e2eid'):
                    rr = efi_pix.devolver_pix(info['e2eid'],
                                              info.get('valor') or camp['preco'], 'dev' + cid)
                    if not rr.get('erro'):
                        estornados += 1
                    else:
                        falhas += 1
                        log.warning(f'[RifaJá] estorno efi falhou {cid}: {rr.get("erro")}')
                else:
                    falhas += 1
                    log.warning(f'[RifaJá] estorno efi sem e2e {cid}: {info.get("erro")}')
            else:
                resp = _asaas_req('POST', f'/payments/{cid}/refund')
                if resp.get('id') or (resp.get('status', '').upper() in ('REFUNDED', 'REFUND_REQUESTED', 'PENDING')):
                    estornados += 1
                else:
                    falhas += 1
                    log.warning(f'[SlotZap] estorno falhou {cid}: {resp}')
        except Exception as _e:
            falhas += 1
            log.warning(f'[SlotZap] estorno erro {cid}: {_e}')
    # Cobranças PENDENTES: Asaas precisa cancelar; Efí expira sozinho (nada a fazer)
    if gateway != 'efi':
        for cid in pend_ch:
            try:
                _asaas_req('DELETE', f'/payments/{cid}')
            except Exception:
                pass

    conn = get_saas_db()
    conn.execute("UPDATE slotzap_campanhas SET status='cancelada' WHERE id=?", (camp_id,))
    conn.commit(); conn.close()
    log.info(f'[SlotZap] Campanha {camp_id} CANCELADA — {estornados} estornos, {falhas} falhas')
    return jsonify({'ok': True, 'estornados': estornados, 'falhas': falhas,
                    'total': len(pagos_ch)})


def _evo_cfg():
    """(url, key) da Evolution API — aceita EVO_* e EVOLUTION_API_* (nomes variam no Railway)."""
    url = (os.environ.get('EVO_URL') or os.environ.get('EVOLUTION_API_URL') or '').rstrip('/')
    key = os.environ.get('EVO_KEY') or os.environ.get('EVOLUTION_API_KEY') or ''
    return url, key


def _sz_seed_commit(conn, camp_id, camp=None):
    """Garante que a campanha tenha um seed secreto + commit (sha256 do seed).
    O commit é publicado ANTES do sorteio; o seed só é revelado DEPOIS.
    Retorna (seed, commit)."""
    import hashlib, secrets as _sec
    row = camp
    if row is None:
        r = conn.execute('SELECT sorteio_seed, sorteio_commit FROM slotzap_campanhas WHERE id=?',
                         (camp_id,)).fetchone()
        row = dict(r) if r else {}
    seed   = (row.get('sorteio_seed') or '').strip()
    commit = (row.get('sorteio_commit') or '').strip()
    if not seed or not commit:
        seed   = _sec.token_urlsafe(24)
        commit = hashlib.sha256(seed.encode()).hexdigest()
        conn.execute('UPDATE slotzap_campanhas SET sorteio_seed=?, sorteio_commit=? WHERE id=?',
                     (seed, commit, camp_id))
        conn.commit()
        if isinstance(row, dict):
            row['sorteio_seed'], row['sorteio_commit'] = seed, commit
    return seed, commit


def _sz_ref_get_or_create(conn, camp_id, nome, tel):
    """Cria/recupera o código de indicação de um comprador (1 por telefone na campanha).
    Sem telefone não há código (não dá pra premiar nem avisar). Retorna o código ou ''."""
    import secrets as _sec
    tel = ''.join(c for c in (tel or '') if c.isdigit())
    if not tel:
        return ''
    row = conn.execute('SELECT codigo FROM slotzap_indicadores WHERE campanha_id=? AND tel=?',
                       (camp_id, tel)).fetchone()
    if row:
        return dict(row)['codigo']
    for _ in range(5):
        codigo = _sec.token_urlsafe(8)
        try:
            conn.execute('INSERT INTO slotzap_indicadores (campanha_id,codigo,nome,tel,criado_em) '
                         'VALUES (?,?,?,?,?)',
                         (camp_id, codigo, (nome or '').strip()[:60], tel, datetime.now().isoformat()))
            conn.commit()
            return codigo
        except Exception:
            continue  # colisão de código (raríssimo) — tenta outro
    return ''


def _sz_premiar_indicacao(conn, camp_id, codigo, meta, qtd=1):
    """Credita as VENDAS (números) feitas por indicação ao código e concede número(s)-brinde
    ao bater a meta (ex.: a cada 10 números vendidos pelo link, 1 grátis).
    Retorna lista de premiados [{numero, nome, tel}] para notificar."""
    if not codigo or qtd < 1:
        return []
    ref = conn.execute('SELECT * FROM slotzap_indicadores WHERE campanha_id=? AND codigo=?',
                       (camp_id, codigo)).fetchone()
    if not ref:
        return []
    ref   = dict(ref)
    novos = (ref['indicados_pagos'] or 0) + qtd   # soma os números vendidos por esta indicação
    conn.execute('UPDATE slotzap_indicadores SET indicados_pagos=? WHERE id=?', (novos, ref['id']))
    conn.commit()
    meta = max(1, int(meta or 10))
    premiados = []
    while (ref['premios_dados'] or 0) < (novos // meta):
        livre = conn.execute(
            "SELECT id, numero FROM slotzap_slots WHERE campanha_id=? AND status='disponivel' "
            "ORDER BY RANDOM() LIMIT 1", (camp_id,)).fetchone()
        if not livre:
            break  # sem estoque livre pra premiar agora
        livre = dict(livre)
        conn.execute("UPDATE slotzap_slots SET status='pago', cliente_nome=?, cliente_tel=?, "
                     "pago_em=?, brinde=1, afiliado_codigo='' WHERE id=?",
                     ((ref['nome'] or 'Indicador'), ref['tel'], datetime.now().isoformat(), livre['id']))
        ref['premios_dados'] = (ref['premios_dados'] or 0) + 1
        conn.execute('UPDATE slotzap_indicadores SET premios_dados=? WHERE id=?',
                     (ref['premios_dados'], ref['id']))
        conn.commit()
        premiados.append({'numero': livre['numero'], 'nome': ref['nome'], 'tel': ref['tel']})
    return premiados


@app.route('/slotzap/campanha/<int:camp_id>/sortear', methods=['POST'])
@_sz_login_required
def slotzap_sortear(camp_id):
    """Sorteia um ganhador entre os números PAGOS, de forma AUDITÁVEL (provably fair),
    e anuncia no grupo. Ganhador = sha256(seed + '|' + lista_pagos) % qtd_pagos."""
    import hashlib
    if not _sz_plan_active():
        return jsonify({'erro': 'Assinatura inativa.'}), 402
    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    camp  = dict(camp)
    pagos = sorted([dict(r) for r in conn.execute(
        "SELECT numero, cliente_nome, IFNULL(brinde,0) AS brinde FROM slotzap_slots "
        "WHERE campanha_id=? AND status='pago'",
        (camp_id,)).fetchall()], key=lambda x: x['numero'])
    if not pagos:
        conn.close()
        return jsonify({'erro': 'Nenhum número pago para sortear ainda.'}), 400

    # ── TRAVA ANTI-PREJUÍZO: não deixa sortear antes de cobrir o custo do prêmio ──
    custo = float(camp.get('custo_premio') or 0)
    if custo > 0:
        _ow = conn.execute('SELECT asaas_wallet_id FROM slotzap_users WHERE id=?', (_sz_uid(),)).fetchone()
        tem_wallet = bool((dict(_ow).get('asaas_wallet_id') or '').strip()) if _ow else False
        net        = (1 - SZ_TAXA_VENDA) if tem_wallet else 1.0   # quanto VOCÊ recebe por número
        pagos_reais = sum(1 for p in pagos if not p.get('brinde'))   # brinde não é dinheiro
        arrecadado  = pagos_reais * float(camp['preco']) * net
        # Desconta a comissão de afiliado JÁ comprometida (cada nº vendido por vendedor custa comissão)
        if camp.get('afiliados_ativo'):
            n_afil = conn.execute(
                "SELECT COUNT(*) FROM slotzap_slots WHERE campanha_id=? AND status='pago' "
                "AND IFNULL(afiliado_codigo,'')<>'' AND IFNULL(brinde,0)=0", (camp_id,)).fetchone()[0]
            arrecadado -= n_afil * float(camp.get('afiliado_comissao') or 0)
        forcar      = bool((request.get_json(silent=True) or {}).get('forcar'))
        if arrecadado < custo and not forcar:
            conn.close()
            faltam = custo - arrecadado
            return jsonify({
                'erro': 'trava_custo',
                'msg': (f'⚠️ Você arrecadou R$ {arrecadado:.2f} de R$ {custo:.2f} necessários para cobrir o prêmio.\n'
                        f'Sortear agora geraria PREJUÍZO de R$ {faltam:.2f}.\n\n'
                        f'Venda mais números antes de sortear.'),
                'arrecadado': round(arrecadado, 2), 'custo': round(custo, 2),
                'faltam': round(faltam, 2)}), 409
    # ── TRAVA "só sortear quando esgotar" (100% pago) ──
    if camp.get('sortear_so_esgotado'):
        total_camp = camp.get('total_slots') or len(pagos)
        if len(pagos) < total_camp:
            conn.close()
            faltam_n = total_camp - len(pagos)
            return jsonify({'erro': 'trava_esgotado',
                'msg': (f'🔒 Esta campanha está configurada para sortear SOMENTE quando esgotar.\n'
                        f'Ainda faltam {faltam_n} número(s) serem pagos.')}), 409

    # ── SENHA: sortear exige a senha de LOGIN da conta (anti-clique errado + autenticação) ──
    _u = conn.execute('SELECT password_hash FROM slotzap_users WHERE id=?', (_sz_uid(),)).fetchone()
    senha = (request.get_json(silent=True) or {}).get('senha') or ''
    if not _u or not check_password_hash(dict(_u)['password_hash'], senha):
        conn.close()
        return jsonify({'erro': 'senha_errada', 'msg': 'Senha da conta incorreta.'}), 403

    # Provably fair: seed travado de antemão + lista pública dos pagos → resultado determinístico
    seed, commit = _sz_seed_commit(conn, camp_id, camp)
    pagos_str = ','.join(str(p['numero']) for p in pagos)
    resultado = hashlib.sha256((seed + '|' + pagos_str).encode()).hexdigest()
    indice    = int(resultado, 16) % len(pagos)
    ganhador  = pagos[indice]
    conn.execute('UPDATE slotzap_campanhas SET ganhador_numero=?, ganhador_nome=?, sorteado_em=?, '
                 'sorteio_hash=?, sorteio_pagos=? WHERE id=?',
                 (ganhador['numero'], ganhador['cliente_nome'] or '', datetime.now().isoformat(),
                  resultado, pagos_str, camp_id))
    conn.commit(); conn.close()

    # Anuncia no grupo (se configurado)
    grupo_id = (camp.get('grupo_wpp_id') or '').strip()
    instance = (camp.get('evo_instance') or '').strip() or os.environ.get('EVO_INSTANCE', '')
    evo_url, evo_key = _evo_cfg()
    grupo_enviado = False
    if grupo_id and instance and evo_url:
        msg = (f"🎉🏆 *RESULTADO DO SORTEIO* 🏆🎉\n\n🎯 {camp['nome']}\n\n"
               f"🥇 Número *#{ganhador['numero']}*\n👤 {ganhador['cliente_nome'] or '—'}\n\nParabéns! 🎊")
        try:
            r = requests.post(f"{evo_url}/message/sendText/{instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'number': grupo_id, 'text': msg}, timeout=15)
            grupo_enviado = r.status_code in (200, 201)
            if not grupo_enviado:
                log.warning(f'[SlotZap] sorteio grupo HTTP {r.status_code}: {r.text[:200]}')
        except Exception as _e:
            log.warning(f'[SlotZap] sorteio grupo: {_e}')
    return jsonify({'ok': True, 'numero': ganhador['numero'],
                    'nome': ganhador['cliente_nome'] or '', 'grupo_enviado': grupo_enviado})


@app.route('/slotzap/campanha/<int:camp_id>/reservar', methods=['POST'])
@_sz_login_required
def slotzap_reservar(camp_id):
    data         = request.get_json() or {}
    numero       = int(data.get('numero', 0))
    cliente_nome = (data.get('nome') or '').strip()
    cliente_cpf  = ''.join(c for c in (data.get('cpf') or '') if c.isdigit())
    cliente_tel  = ''.join(c for c in (data.get('tel') or '') if c.isdigit())

    if not cliente_nome:
        return jsonify({'erro': 'Nome do cliente obrigatório'}), 400
    if not _cpf_valido(cliente_cpf):
        return jsonify({'erro': 'CPF inválido. Confira os números.'}), 400

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

    preco   = float(dict(camp)['preco'])
    slot_id = dict(slot)['id']
    _ow     = conn.execute('SELECT asaas_wallet_id FROM slotzap_users WHERE id=?', (_sz_uid(),)).fetchone()
    owner_wallet = (dict(_ow).get('asaas_wallet_id') or '').strip() if _ow else ''

    # Cria cliente + cobrança PIX no Asaas — só reserva se o PIX for gerado
    erro_msg, charge_id, pix_qr, pix_copia = _sz_gerar_pix(
        cliente_nome, cliente_tel, cliente_cpf, preco,
        f"SlotZap — {dict(camp)['nome']} — Slot #{numero}", f'sz_{slot_id}', owner_wallet,
        gateway=dict(camp).get('gateway', 'asaas'))
    if erro_msg:
        conn.close()
        return jsonify({'erro': erro_msg}), 502

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
    conn.close()
    # Usa o helper: marca pago (idempotente) e notifica o grupo, como no fluxo automático
    _sz_marcar_pago(slot_id)
    return jsonify({'ok': True})


@app.route('/slotzap/slot/<int:slot_id>/cancelar', methods=['POST'])
@_sz_login_required
def slotzap_cancelar(slot_id):
    conn = get_saas_db()
    slot = conn.execute('''SELECT s.*, c.gateway AS gateway FROM slotzap_slots s
        JOIN slotzap_campanhas c ON c.id=s.campanha_id
        WHERE s.id=? AND c.user_id=?''', (slot_id, _sz_uid())).fetchone()
    if not slot:
        conn.close()
        return jsonify({'erro': 'Slot não encontrado'}), 404
    _slotd = dict(slot)
    charge = _slotd.get('asaas_charge_id', '')
    if charge:
        _sz_cancelar_cobranca(_slotd.get('gateway', 'asaas'), charge)  # efi: cob expira sozinha
    conn.execute(
        "UPDATE slotzap_slots SET status='disponivel',cliente_nome='',cliente_tel='',"
        "asaas_charge_id='',pix_qr_code='',pix_copia_cola='',reservado_em=NULL,pago_em=NULL,"
        "afiliado_codigo='',indicado_por='' WHERE id=?",
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
    if instance:
        conn.execute('UPDATE slotzap_campanhas SET grupo_wpp_id=?,evo_instance=?,msg_pagamento=? WHERE id=? AND user_id=?',
                     (grupo_id, instance, msg, camp_id, _sz_uid()))
    else:
        # Sem número selecionado: NÃO apaga o evo_instance já salvo
        conn.execute('UPDATE slotzap_campanhas SET grupo_wpp_id=?,msg_pagamento=? WHERE id=? AND user_id=?',
                     (grupo_id, msg, camp_id, _sz_uid()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/slotzap/campanha/<int:camp_id>/numeros-disponiveis')
@_sz_login_required
def slotzap_numeros_disponiveis(camp_id):
    """Números WhatsApp conectados da conta MandaZap com o MESMO e-mail do usuário
    SlotZap logado (isolamento por cliente). Sem conta MandaZap = nenhum número."""
    evo_url = (os.environ.get('EVO_URL') or os.environ.get('EVOLUTION_API_URL') or '').rstrip('/')
    if not evo_url:
        return jsonify({'numeros': []})
    conn = get_saas_db()
    sz    = conn.execute('SELECT email FROM slotzap_users WHERE id=?', (_sz_uid(),)).fetchone()
    email = (dict(sz).get('email') or '').strip().lower() if sz else ''
    numeros = []
    if email:
        rows = conn.execute(
            "SELECT n.id, n.user_id, n.label, n.phone FROM mandazap_numbers n "
            "JOIN mandazap_users u ON u.id = n.user_id "
            "WHERE n.status='connected' AND lower(u.email)=? ORDER BY n.id", (email,)).fetchall()
        for n in rows:
            n = dict(n)
            instance = f"mz{n['user_id']}n{n['id']}"
            phone_clean = n['phone'].lstrip('55') if n['phone'] and n['phone'].startswith('55') else n['phone']
            numeros.append({
                'instance': instance,
                'label': n['label'] or phone_clean,
                'phone': phone_clean,
            })
    conn.close()
    return jsonify({'numeros': numeros, 'tem_mandazap': bool(numeros)})


def _sz_criar_cliente_asaas(nome, tel, cpf=''):
    """Cria ou reutiliza cliente no Asaas para SlotZap com CPF obrigatório."""
    customer_id = None
    cpf_limpo = ''.join(c for c in (cpf or '') if c.isdigit())
    # Busca cliente existente pelo CPF
    if cpf_limpo:
        busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf_limpo}&limit=1')
        if busca.get('data'):
            customer_id = busca['data'][0].get('id')
    # Cria novo cliente com CPF
    if not customer_id:
        dados = {
            'name': nome or 'Cliente SlotZap',
            'notificationDisabled': True,
        }
        if cpf_limpo:
            dados['cpfCnpj'] = cpf_limpo
        resp = _asaas_req('POST', '/customers', dados)
        customer_id = resp.get('id')
    return customer_id


def _sz_gerar_pix(nome, tel, cpf, valor, descricao, ext_ref, split_wallet='', gateway='asaas'):
    """Cria cobrança PIX e retorna (erro_msg, charge_id, pix_qr, pix_copia).
    gateway='efi' (RifaJá): usa a Efí — charge_id=txid, pix_qr='' (QR gerado no front
    a partir do copia-e-cola). gateway='asaas' (default — SlotZap/Jaya): INALTERADO."""
    # ── Gateway Efí (RifaJá) — aditivo, só roda quando gateway='efi' ──
    if gateway == 'efi':
        try:
            import efi_pix
            cob = efi_pix.criar_cobranca(valor, descricao)
        except Exception as _e:
            log.error('[RifaJá] efi_pix indisponível: %s', _e)
            return ('Pagamento indisponível no momento. Tente novamente.', '', '', '')
        if cob.get('erro') or not cob.get('txid'):
            log.error('[RifaJá] Falha cobrança Efí: %s', cob.get('erro'))
            return ('Não foi possível gerar a cobrança PIX. Tente novamente.', '', '', '')
        return (None, cob['txid'], '', cob.get('copia_cola', ''))

    # ── Gateway Asaas (default — SlotZap/Jaya): código original, inalterado ──
    customer_id = _sz_criar_cliente_asaas(nome, tel, cpf)
    if not customer_id:
        log.error('[SlotZap] Falha ao criar cliente Asaas (nome=%s)', nome)
        return ('Não foi possível criar o cadastro de pagamento. Tente novamente.',
                '', '', '')

    venc    = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    payload = {
        'customer':          customer_id,
        'billingType':       'PIX',
        'value':             round(float(valor), 2),
        'dueDate':           venc,
        'description':       descricao,
        'externalReference': ext_ref,
    }
    # Asaas Split: cliente recebe (100 - taxa)%, plataforma retém a taxa
    if split_wallet:
        payload['split'] = [{
            'walletId':        split_wallet,
            'percentualValue': round((1 - SZ_TAXA_VENDA) * 100, 2),
        }]
    resp_pay = _asaas_req('POST', '/payments', payload)
    charge_id = resp_pay.get('id', '')
    if not charge_id:
        errs = resp_pay.get('errors') or []
        msg  = errs[0].get('description') if errs and errs[0].get('description') \
               else 'Não foi possível gerar a cobrança PIX. Tente novamente.'
        log.error('[SlotZap] Falha na cobrança Asaas: %s', resp_pay)
        return (msg, '', '', '')

    qr_resp   = _asaas_req('GET', f'/payments/{charge_id}/pixQrCode')
    pix_qr    = qr_resp.get('encodedImage', '')
    pix_copia = qr_resp.get('payload', '')
    if not pix_copia:
        log.error('[SlotZap] Cobrança %s criada mas sem PIX: %s', charge_id, qr_resp)
        return ('A cobrança foi criada, mas o PIX não foi gerado. Tente novamente.',
                charge_id, '', '')

    return (None, charge_id, pix_qr, pix_copia)


def _sz_pagamento_confirmado(gateway, cid):
    """True se a cobrança foi PAGA, no gateway certo.
    asaas: GET /payments/{cid} (RECEIVED/CONFIRMED). efi: consultar_cobranca (CONCLUIDA)."""
    if not cid:
        return False
    if gateway == 'efi':
        try:
            import efi_pix
            return bool(efi_pix.consultar_cobranca(cid).get('pago'))
        except Exception as _e:
            log.warning(f'[RifaJá] consultar cobrança Efí ({cid}): {_e}')
            return False
    pay = _asaas_req('GET', f'/payments/{cid}')
    return (pay.get('status') or '').upper() in ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH')


def _sz_cancelar_cobranca(gateway, cid):
    """Cancela cobrança não paga. asaas: DELETE. efi: cob expira sozinha (nada a fazer)."""
    if gateway == 'efi' or not cid:
        return
    try:
        _asaas_req('DELETE', f'/payments/{cid}')
    except Exception:
        pass


def _sz_marcar_pago(slot_id):
    """Marca um slot como pago (idempotente) e notifica o grupo WhatsApp.
    Retorna True se deu baixa agora; False se já estava pago ou não existe."""
    conn = get_saas_db()
    slot = conn.execute('SELECT status FROM slotzap_slots WHERE id=?', (slot_id,)).fetchone()
    if not slot or dict(slot)['status'] == 'pago':
        conn.close()
        return False
    conn.execute("UPDATE slotzap_slots SET status='pago', pago_em=? WHERE id=?",
                 (datetime.now().isoformat(), slot_id))
    conn.commit()
    row = conn.execute('''
        SELECT s.numero, s.cliente_nome, s.cliente_tel, s.id AS slot_id, s.afiliado_codigo,
               c.nome AS camp_nome, c.preco, c.grupo_wpp_id,
               c.evo_instance, c.msg_pagamento, c.id AS camp_id, c.token_publico,
               c.afiliados_ativo, c.afiliado_comissao, c.gateway,
               (SELECT COUNT(*) FROM slotzap_slots WHERE campanha_id=c.id AND status="pago")      AS pagos,
               (SELECT COUNT(*) FROM slotzap_slots WHERE campanha_id=c.id AND status="disponivel") AS livres,
               c.total_slots
        FROM slotzap_slots s
        JOIN slotzap_campanhas c ON c.id = s.campanha_id
        WHERE s.id=?
    ''', (slot_id,)).fetchone()
    conn.close()
    if not row:
        return True
    row = dict(row)
    log.info(f'[SlotZap] Slot #{row["numero"]} — {row["camp_nome"]} — PAGO ({row["cliente_nome"]})')

    # Comissão do afiliado também pelo caminho SINGULAR (idempotente via ledger) —
    # fecha o buraco em que o webhook caía no fallback e pulava a comissão.
    if row.get('afiliados_ativo') and (row.get('afiliado_codigo') or '').strip():
        _camp_a = {'id': row['camp_id'], 'nome': row['camp_nome'],
                   'afiliados_ativo': row['afiliados_ativo'],
                   'afiliado_comissao': row['afiliado_comissao'],
                   'gateway': row.get('gateway', 'asaas')}
        _slot_a = {'id': row['slot_id'], 'numero': row['numero'],
                   'afiliado_codigo': row['afiliado_codigo']}
        threading.Thread(target=_sz_pagar_afiliados_bg, args=(_camp_a, [_slot_a]),
                         daemon=True, name='sz-pagar-afiliado-s').start()

    grupo_id = (row.get('grupo_wpp_id') or '').strip()
    instance = (row.get('evo_instance') or '').strip() or os.environ.get('EVO_INSTANCE', '')
    evo_url, evo_key = _evo_cfg()
    if grupo_id and instance and evo_url:
        base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
        token    = row.get('token_publico') or ''
        link_str = f"\n🔗 {base_url}/slotzap/p/{token}" if token else ''
        total    = row.get('total_slots') or 0
        pct      = round(row['pagos'] / total * 100) if total else 0
        tpl = row.get('msg_pagamento') or (
            f"✅ *Slot #{row['numero']} — PAGO!*\n"
            f"👤 {row['cliente_nome']}\n"
            f"🎯 {row['camp_nome']}\n\n"
            f"📊 {pct}% concluído"
            f"{link_str}"
        )
        try:
            requests.post(
                f"{evo_url}/message/sendText/{instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'number': grupo_id, 'text': tpl}, timeout=10
            )
            log.info(f'[SlotZap] Notificação WPP enviada para grupo {grupo_id}')
        except Exception as _wpp_err:
            log.warning(f'[SlotZap] Erro ao notificar grupo: {_wpp_err}')

    # Confirmação no WhatsApp do COMPRADOR (se ele informou o número)
    tel = ''.join(c for c in (row.get('cliente_tel') or '') if c.isdigit())
    if tel and instance and evo_url:
        numero_wpp = tel if tel.startswith('55') else ('55' + tel)
        primeiro   = (row['cliente_nome'] or '').split()[0] if row.get('cliente_nome') else ''
        msg_cli = (f"✅ *Pagamento confirmado!*\n\n"
                   f"Seu número *{row['numero']}* na *{row['camp_nome']}* está garantido. 🎯\n"
                   f"Obrigado{(', ' + primeiro) if primeiro else ''}!")
        try:
            requests.post(
                f"{evo_url}/message/sendText/{instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'number': numero_wpp, 'text': msg_cli}, timeout=10)
            log.info(f'[SlotZap] Confirmação enviada ao comprador {numero_wpp}')
        except Exception as _cli_err:
            log.warning(f'[SlotZap] Erro ao confirmar p/ comprador: {_cli_err}')
    return True


def _sz_efi_comissao_via_asaas():
    """Por padrão a comissão de rifa EFÍ sai pelo ASAAS (transferência GRÁTIS), porque o
    Pix Envio do Efí exige webhook mTLS que o Railway não termina (Cloudflare BYO-CA é
    Enterprise-only). Pra voltar ao Efí-puro quando houver proxy mTLS: EFI_COMISSAO_VIA_ASAAS=0.
    NÃO afeta a Jaya/Asaas — campanha gateway='asaas' nunca entra nesse ramo."""
    return os.environ.get('EFI_COMISSAO_VIA_ASAAS', '1').strip().lower() in ('1', 'true', 'yes', 'sim', 'on')


def _sz_afiliado_transfer(pix_chave, pix_tipo, valor, descricao, ext_ref, gateway='asaas'):
    """Envia a comissão ao afiliado via PIX. Retorna (transfer_id, erro).
    asaas: /transfers (grátis). efi-puro: efi_pix.enviar_pix com idEnvio determinístico.
    FALLBACK (padrão): comissão de rifa EFÍ também sai via Asaas (Pix Envio do Efí bloqueado
    por falta de webhook mTLS); ver _sz_efi_comissao_via_asaas()."""
    if gateway == 'efi' and not _sz_efi_comissao_via_asaas():
        try:
            import efi_pix
            idenv = ''.join(c for c in ext_ref if c.isalnum())[:35]
            r = efi_pix.enviar_pix(valor, pix_chave, info=descricao, id_envio=idenv)
        except Exception as _e:
            return ('', f'efi enviar_pix: {_e}'[:200])
        if r.get('erro'):
            return ('', str(r['erro'])[:200])
        return (r.get('e2eId') or r.get('idEnvio') or 'efi_ok', None)
    payload = {
        'value':            round(float(valor), 2),
        'operationType':    'PIX',
        'pixAddressKey':    pix_chave,
        'description':      (descricao or '')[:100],
        'externalReference': ext_ref,
    }
    if pix_tipo:
        payload['pixAddressKeyType'] = pix_tipo
    resp = _asaas_req('POST', '/transfers', payload)
    tid  = resp.get('id', '')
    if tid:
        return (tid, None)
    errs = resp.get('errors') or []
    msg  = (errs[0].get('description') if errs and errs[0].get('description')
            else (resp.get('error') or 'Falha na transferência PIX.'))
    return ('', str(msg)[:200])


def _sz_pagar_afiliados(conn, camp, slots):
    """Paga a comissão (PIX) ao afiliado de CADA número pago — AUTO-CURÁVEL e ANTI-DUPLO.
    - Reusa o registro do ledger: 'pago' é pulado; 'pendente'/'erro' são RE-TENTADOS.
    - ANTES de enviar, consulta o Asaas se já existe transferência com o mesmo
      externalReference (szaf_<slot>): se já existe, só sincroniza (NUNCA paga 2×).
    Seguro de chamar pelo fluxo inline E pelo reconciliador."""
    comissao = float(camp.get('afiliado_comissao') or 0)
    if not camp.get('afiliados_ativo') or comissao <= 0:
        return
    gateway = camp.get('gateway', 'asaas')   # paga a comissão pelo MESMO gateway que recebeu
    marca   = 'RifaJa' if gateway == 'efi' else 'SlotZap'
    agora = datetime.now().isoformat()
    for s in slots:
        cod = (s.get('afiliado_codigo') or '').strip()
        if not cod:
            continue
        af = conn.execute('SELECT * FROM slotzap_afiliados WHERE campanha_id=? AND codigo=?',
                          (camp['id'], cod)).fetchone()
        if not af:
            continue
        af  = dict(af)
        ext = f"szaf_{s['id']}"
        row = conn.execute("SELECT status FROM slotzap_afiliado_pagamentos WHERE slot_id=?",
                           (s['id'],)).fetchone()
        if row and dict(row)['status'] in ('pago', 'enviando'):
            continue  # já pago (ou em envio por um lote) — nunca paga 2×
        if not row:
            # cria o registro (claim). Se outra via criar ao mesmo tempo, pula (corrida).
            try:
                conn.execute(
                    'INSERT INTO slotzap_afiliado_pagamentos '
                    '(afiliado_id,campanha_id,slot_id,valor,status,criado_em) VALUES (?,?,?,?,?,?)',
                    (af['id'], camp['id'], s['id'], comissao, 'pendente', agora))
                conn.commit()
            except Exception:
                continue
        # ── ANTI-DUPLO via Asaas: roda sempre que o PIX sai pelo Asaas (asaas OU efi-fallback).
        #     No Efí-puro pula (idempotência = idEnvio determinístico). ──
        if gateway != 'efi' or _sz_efi_comissao_via_asaas():
            ja_tid = ''
            try:
                chk = _asaas_req('GET', f'/transfers?externalReference={ext}')
                for t in (chk.get('data') or []):
                    st = (t.get('status') or '').upper()
                    # só conta como "já enviada" se NÃO foi cancelada/falhou (senão re-envia)
                    if (t.get('externalReference') == ext and t.get('id')
                            and st not in ('CANCELLED', 'FAILED')):
                        ja_tid = t['id']; break
            except Exception:
                pass
            if ja_tid:
                conn.execute("UPDATE slotzap_afiliado_pagamentos SET status='pago', asaas_transfer_id=?, erro='' WHERE slot_id=?",
                             (ja_tid, s['id']))
                conn.commit()
                log.info(f"[SlotZap] comissao num {s['numero']} ja transferida ({ja_tid}) — ledger sincronizado")
                continue
        # ── Envia o PIX da comissão PELO GATEWAY CERTO (efi=RifaJá / asaas=SlotZap) ──
        desc = f"Comissao {marca} - {camp.get('nome','')} - num {s['numero']}"
        tid, erro = _sz_afiliado_transfer(af.get('pix_chave'), af.get('pix_tipo'), comissao, desc, ext, gateway=gateway)
        if tid:
            conn.execute("UPDATE slotzap_afiliado_pagamentos SET status='pago', asaas_transfer_id=?, erro='' WHERE slot_id=?",
                         (tid, s['id']))
            conn.commit()
            log.info(f"[SlotZap] Comissao R${comissao:.2f} -> afiliado {af.get('nome')} (num {s['numero']}, t {tid})")
        else:
            conn.execute("UPDATE slotzap_afiliado_pagamentos SET status='erro', erro=?, "
                         "tentativas=IFNULL(tentativas,0)+1 WHERE slot_id=?",
                         (erro, s['id']))
            conn.commit()
            log.warning(f"[SlotZap] Falha comissao afiliado {af.get('nome')} (num {s['numero']}): {erro}")


def _sz_pagar_afiliados_bg(camp, slots):
    """Roda o pagamento de comissões em thread separada, com conexão própria —
    não trava a baixa do número nem a resposta do 'Já paguei'. Idempotente via ledger."""
    conn = get_saas_db()
    try:
        _sz_pagar_afiliados(conn, camp, slots)
    except Exception as _e:
        log.warning(f'[SlotZap] pagar afiliados (bg): {_e}')
    finally:
        conn.close()


def _sz_marcar_pago_charge(charge_id):
    """Dá baixa em TODOS os slots reservados com este charge_id (suporta multi-compra).
    Notifica o grupo uma única vez e avisa o comprador. Idempotente. Retorna nº de slots."""
    if not charge_id:
        return 0
    conn  = get_saas_db()
    slots = [dict(r) for r in conn.execute(
        "SELECT id, numero, cliente_nome, cliente_tel, campanha_id, indicado_por, afiliado_codigo FROM slotzap_slots "
        "WHERE asaas_charge_id=? AND status='reservado'", (charge_id,)).fetchall()]
    if not slots:
        conn.close()
        return 0
    agora = datetime.now().isoformat()
    # Baixa ATÔMICA: marca como pago condicionado a ainda estar 'reservado'.
    # Se outra chamada (webhook + "Já paguei") já marcou, rowcount=0 e abortamos
    # ANTES de creditar indicação — evita brinde/aviso em dobro (prejuízo).
    cur = conn.execute(
        "UPDATE slotzap_slots SET status='pago', pago_em=? "
        "WHERE asaas_charge_id=? AND status='reservado'", (agora, charge_id))
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        return 0   # já processado por outra via — não credita/avisa de novo
    camp = dict(conn.execute('''
        SELECT c.id, c.nome, c.grupo_wpp_id, c.evo_instance, c.token_publico, c.total_slots, c.preco,
               c.indicacao_ativa, c.indicacao_meta, c.afiliados_ativo, c.afiliado_comissao, c.gateway,
               (SELECT COUNT(*) FROM slotzap_slots WHERE campanha_id=c.id AND status="pago") AS pagos
        FROM slotzap_campanhas c WHERE c.id=?''', (slots[0]['campanha_id'],)).fetchone())

    # ── Indicação premiada: credita o indicador e concede brinde se bater a meta ──
    if camp.get('indicacao_ativa') and (slots[0].get('indicado_por') or '').strip():
        try:
            premiados = _sz_premiar_indicacao(conn, camp['id'],
                                              slots[0]['indicado_por'].strip(),
                                              camp.get('indicacao_meta') or 10,
                                              qtd=len(slots))   # conta NÚMEROS vendidos, não amigos
        except Exception as _e:
            premiados = []
            log.warning(f'[SlotZap] indicacao: {_e}')
    else:
        premiados = []
    conn.close()

    # ── Comissão do afiliado/vendedor: paga PIX na hora, em THREAD própria ──
    # (não trava a baixa nem o "Já paguei"; idempotente via ledger; nunca quebra a baixa)
    if camp.get('afiliados_ativo'):
        _aff_slots = [s for s in slots if (s.get('afiliado_codigo') or '').strip()]
        if _aff_slots:
            threading.Thread(target=_sz_pagar_afiliados_bg, args=(camp, _aff_slots),
                             daemon=True, name='sz-pagar-afiliado').start()

    numeros  = sorted(s['numero'] for s in slots)
    nums_str = ', '.join('#' + str(n) for n in numeros)
    nome_cli = slots[0]['cliente_nome'] or ''
    tel_cli  = ''.join(c for c in (slots[0]['cliente_tel'] or '') if c.isdigit())
    log.info(f'[SlotZap] PAGO ({charge_id}): {nums_str} — {camp["nome"]} ({nome_cli})')

    instance = (camp.get('evo_instance') or '').strip() or os.environ.get('EVO_INSTANCE', '')
    evo_url, evo_key = _evo_cfg()
    total    = camp.get('total_slots') or 0
    pct      = round(camp['pagos'] / total * 100) if total else 0

    grupo_id = (camp.get('grupo_wpp_id') or '').strip()
    if grupo_id and instance and evo_url:
        base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
        token    = camp.get('token_publico') or ''
        link     = f"\n🔗 {base_url}/slotzap/p/{token}" if token else ''
        tpl = (f"✅ *{nums_str} — PAGO!*\n👤 {nome_cli}\n🎯 {camp['nome']}\n\n📊 {pct}% concluído{link}")
        try:
            requests.post(f"{evo_url}/message/sendText/{instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'number': grupo_id, 'text': tpl}, timeout=10)
        except Exception as _e:
            log.warning(f'[SlotZap] grupo: {_e}')

    if tel_cli and instance and evo_url:
        nwpp     = tel_cli if tel_cli.startswith('55') else ('55' + tel_cli)
        prim     = nome_cli.split()[0] if nome_cli else ''
        preco    = float(camp.get('preco') or 0)
        valor    = preco * len(slots)
        data_fmt = datetime.now().strftime('%d/%m/%Y às %H:%M')
        codigo   = (charge_id[-8:] if charge_id else str(slots[0]['id'])).upper()
        base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
        token    = camp.get('token_publico') or ''
        link     = f"\n🔗 Acompanhe: {base_url}/slotzap/p/{token}" if token else ''
        msg = (f"🧾 *COMPROVANTE — Pagamento confirmado!*\n\n"
               f"🎯 {camp['nome']}\n"
               f"🔢 Número(s): {nums_str}\n"
               f"💰 Valor: R$ {valor:.2f}\n"
               f"📅 {data_fmt}\n"
               f"🧾 Código: {codigo}\n\n"
               f"Seu(s) número(s) está(ão) garantido(s)! 🎉\n"
               f"🔒 Sorteio auditável — você poderá conferir o resultado pelo link."
               f"{link}\n\n"
               f"Guarde este comprovante. Boa sorte{(', ' + prim) if prim else ''}! 🍀")
        try:
            requests.post(f"{evo_url}/message/sendText/{instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'number': nwpp, 'text': msg}, timeout=10)
        except Exception as _e:
            log.warning(f'[SlotZap] DM: {_e}')

    # Avisa quem GANHOU número grátis por indicação
    if premiados and instance and evo_url:
        base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
        token    = camp.get('token_publico') or ''
        link     = f"\n🔗 {base_url}/slotzap/p/{token}" if token else ''
        for p in premiados:
            ptel = ''.join(c for c in (p.get('tel') or '') if c.isdigit())
            if not ptel:
                continue
            pwpp = ptel if ptel.startswith('55') else ('55' + ptel)
            pmsg = (f"🎁 *VOCÊ GANHOU UM NÚMERO GRÁTIS!*\n\n"
                    f"Por indicar amigos que compraram na *{camp['nome']}*, "
                    f"você acaba de ganhar o número *#{p['numero']}*! 🍀\n"
                    f"Ele já está garantido no seu nome e concorre ao sorteio."
                    f"{link}\n\nObrigado por divulgar! Continue indicando pra ganhar mais. 🚀")
            try:
                requests.post(f"{evo_url}/message/sendText/{instance}",
                    headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                    json={'number': pwpp, 'text': pmsg}, timeout=10)
            except Exception as _e:
                log.warning(f'[SlotZap] premio indicacao DM: {_e}')
    return len(slots)


def _sz_reconciliar_loop():
    """Robô 24/7 (camada de segurança independente do webhook): a cada ~3 min confere
    no Asaas as cobranças de slots ainda 'reservado' e credita as que foram pagas.
    Funciona mesmo se o webhook cair, ninguém estiver na página, etc."""
    time.sleep(120)  # deixa o app subir
    log.info('[SlotZap] Reconciliador de pagamentos ATIVO (verifica a cada 3 min)')
    while True:
        try:
            conn = get_saas_db()
            limite = (datetime.now() - timedelta(seconds=90)).isoformat()
            charges = [dict(r) for r in conn.execute(
                "SELECT DISTINCT s.asaas_charge_id AS cid, IFNULL(c.gateway,'asaas') AS gw "
                "FROM slotzap_slots s JOIN slotzap_campanhas c ON c.id=s.campanha_id "
                "WHERE s.status='reservado' AND s.asaas_charge_id<>'' AND s.reservado_em<>'' "
                "AND s.reservado_em < ?", (limite,)).fetchall()]
            conn.close()
            for ch in charges:
                cid, gw = ch['cid'], ch['gw']
                try:
                    if _sz_pagamento_confirmado(gw, cid):
                        n = _sz_marcar_pago_charge(cid)
                        if n:
                            log.info(f'[SlotZap] Reconciliador creditou {cid} ({n} slot(s)) — webhook nao pegou')
                except Exception as _e:
                    log.warning(f'[SlotZap] reconciliador charge {cid}: {_e}')
        except Exception as _e:
            log.error(f'[SlotZap] reconciliador loop: {_e}')
        time.sleep(180)  # a cada 3 minutos


threading.Thread(target=_sz_reconciliar_loop, daemon=True, name='sz-reconciliador').start()


# ════════════════════════════════════════════════════════════════════════════
#  PAGAMENTO DE COMISSÕES EM LOTE (1 PIX por vendedor) — "Trimania-proof"
#  O Asaas TRAVA transferências idênticas (mesmo valor + mesma chave) feitas
#  juntas (janela anti-duplicata de vários minutos). Pagar N×R$4 separado = trava.
#  Solução: somar tudo do vendedor e mandar UM PIX (valor único nunca trava).
#  Idempotência à prova de queda: claim 'enviando' + lote_ref único; antes de
#  reenviar, consulta o Asaas por aquele lote_ref (recupera envio que já saiu).
# ════════════════════════════════════════════════════════════════════════════
_SZ_LOTE_LOCK = threading.Lock()   # serializa os envios (evita rajada simultânea)
# Cap de re-tentativas do reconciliador AUTOMÁTICO: alto o bastante p/ qualquer
# bloqueio TRANSITÓRIO de duplicata do Asaas passar sozinho (25 × ~75s ≈ 30 min);
# se uma chave PIX for de fato inválida, para depois disso. O botão MANUAL do dono
# (force=True) ignora o cap — é ordem direta de pagar.
_SZ_CAP_TENTATIVAS = 25


def _sz_flush_lote(conn, ref):
    """Resolve UM lote já reservado (status='enviando', lote_ref=ref):
    confere no Asaas se já saiu; se não, manda 1 PIX com a SOMA e baixa tudo.
    Retorna o valor pago (0 se nada). À prova de duplo via o próprio ref."""
    rows = [dict(r) for r in conn.execute(
        "SELECT slot_id, afiliado_id, valor, campanha_id FROM slotzap_afiliado_pagamentos "
        "WHERE lote_ref=? AND status='enviando'", (ref,)).fetchall()]
    if not rows:
        return 0.0
    total = round(sum(float(r['valor'] or 0) for r in rows), 2)
    afid  = rows[0]['afiliado_id']
    _gwr  = conn.execute("SELECT IFNULL(gateway,'asaas') AS gw FROM slotzap_campanhas WHERE id=?",
                         (rows[0].get('campanha_id'),)).fetchone()
    gateway = dict(_gwr)['gw'] if _gwr else 'asaas'
    # ── 1) (Asaas) Já existe transferência com este lote_ref? (recupera queda). ──
    #     Roda sempre que o PIX sai pelo Asaas (asaas OU efi-fallback); Efí-puro pula
    #     (idempotência = idEnvio determinístico). ──
    if gateway != 'efi' or _sz_efi_comissao_via_asaas():
        try:
            chk = _asaas_req('GET', f'/transfers?externalReference={ref}')
            for t in (chk.get('data') or []):
                st = (t.get('status') or '').upper()
                if (t.get('externalReference') == ref and t.get('id')
                        and st not in ('CANCELLED', 'FAILED')):
                    conn.execute("UPDATE slotzap_afiliado_pagamentos SET status='pago', "
                                 "asaas_transfer_id=?, erro='' WHERE lote_ref=?", (t['id'], ref))
                    conn.commit()
                    log.info(f"[SlotZap] Lote {ref} já estava no Asaas ({t['id']}) — baixado R${total:.2f}")
                    return total
        except Exception:
            pass
    # ── 2) Envia 1 PIX com a soma ──
    af = conn.execute('SELECT nome, pix_chave, pix_tipo FROM slotzap_afiliados WHERE id=?',
                      (afid,)).fetchone()
    if not af:
        conn.execute("UPDATE slotzap_afiliado_pagamentos SET status='erro', "
                     "erro='afiliado sumiu' WHERE lote_ref=?", (ref,))
        conn.commit()
        return 0.0
    af   = dict(af)
    desc = f"Comissao SlotZap - {len(rows)} numero(s)"
    tid, erro = _sz_afiliado_transfer(af.get('pix_chave'), af.get('pix_tipo'), total, desc, ref,
                                      gateway=gateway)
    if tid:
        conn.execute("UPDATE slotzap_afiliado_pagamentos SET status='pago', "
                     "asaas_transfer_id=?, erro='' WHERE lote_ref=?", (tid, ref))
        conn.commit()
        log.info(f"[SlotZap] LOTE pago R${total:.2f} -> {af.get('nome')} "
                 f"({len(rows)} num, t {tid}, ref {ref})")
        return total
    conn.execute("UPDATE slotzap_afiliado_pagamentos SET status='erro', erro=? WHERE lote_ref=?",
                 (erro, ref))
    conn.commit()
    log.warning(f"[SlotZap] Falha LOTE {af.get('nome')} R${total:.2f} (ref {ref}): {erro}")
    return 0.0


def _sz_pagar_pendentes(conn, camp_id=None, force=False):
    """Paga TODAS as comissões pendentes/erro, AGRUPADAS POR VENDEDOR (1 PIX cada).
    Usado pelo reconciliador (24/7) e pelo botão 'Pagar pendentes agora' do dono.
    Fases: (A) recupera lotes 'enviando' presos por queda; (B) monta lotes novos.
    force=True (botão do dono): ignora o cap de tentativas — paga até o que o
    automático já desistiu (ex.: travou no limite por bloqueio transitório do Asaas).
    Serializado por trava global p/ não disparar PIX idênticos em paralelo."""
    pago_total = 0.0
    with _SZ_LOTE_LOCK:
        # ── Fase A: recupera lotes 'enviando' antigos (>90s) — queda no meio do envio ──
        try:
            refs = [dict(r)['lote_ref'] for r in conn.execute(
                "SELECT DISTINCT lote_ref FROM slotzap_afiliado_pagamentos "
                "WHERE status='enviando' AND IFNULL(lote_ref,'')<>''").fetchall()]
            for ref in refs:
                # o timestamp vai no próprio ref (szaf_l<afid>_<ts>): só mexe se >90s
                try:
                    ts = int(ref.rsplit('_', 1)[-1])
                    if (time.time() - ts) < 90:
                        continue   # provavelmente um envio em andamento — não atropela
                except Exception:
                    pass
                pago_total += _sz_flush_lote(conn, ref)
        except Exception as _e:
            log.warning(f'[SlotZap] recuperar lotes enviando: {_e}')

        # ── Fase B: monta lotes novos a partir das vendas sem repasse ──
        filtro_camp = ' AND s.campanha_id=? ' if camp_id else ''
        params = (camp_id,) if camp_id else ()
        # cap de tentativas: o automático respeita; o botão do dono (force) ignora
        cap_cond = '' if force else f'AND IFNULL(p.tentativas,0) < {_SZ_CAP_TENTATIVAS}'
        cand = [dict(r) for r in conn.execute(f'''
            SELECT s.id AS slot_id, s.campanha_id, a.id AS afiliado_id, c.afiliado_comissao AS comissao
            FROM slotzap_slots s
            JOIN slotzap_campanhas c ON c.id = s.campanha_id
            JOIN slotzap_afiliados  a ON a.campanha_id = s.campanha_id AND a.codigo = s.afiliado_codigo
            LEFT JOIN slotzap_afiliado_pagamentos p ON p.slot_id = s.id
            WHERE s.status='pago' AND IFNULL(s.afiliado_codigo,'')<>'' AND IFNULL(s.brinde,0)=0
              AND c.afiliados_ativo=1 AND IFNULL(c.afiliado_comissao,0) > 0
              AND (p.id IS NULL OR (p.status IN ('pendente','erro') {cap_cond}))
              {filtro_camp}
        ''', params).fetchall()]
        # agrupa por vendedor
        por_af = {}
        for r in cand:
            por_af.setdefault(r['afiliado_id'], []).append(r)
        for afid, slots in por_af.items():
            # ref ÚNICO (random + timestamp no fim): random evita colisão entre workers
            # do Railway (impede 2 lotes iguais p/ o mesmo número); ts no fim é lido na Fase A.
            ref = f"szaf_l{afid}_{os.urandom(3).hex()}_{int(time.time())}"
            try:
                # garante 1 linha no ledger por slot (cria 'pendente' se faltar)
                for r in slots:
                    conn.execute(
                        "INSERT OR IGNORE INTO slotzap_afiliado_pagamentos "
                        "(afiliado_id,campanha_id,slot_id,valor,status,criado_em) "
                        "SELECT ?,?,?,?,?,? WHERE NOT EXISTS "
                        "(SELECT 1 FROM slotzap_afiliado_pagamentos WHERE slot_id=?)",
                        (afid, r['campanha_id'], r['slot_id'], float(r['comissao'] or 0), 'pendente',
                         datetime.now().isoformat(), r['slot_id']))
                conn.commit()
                ids = [r['slot_id'] for r in slots]
                # claim ATÔMICO: só pega as que ainda estão livres (pendente/erro)
                qs = ','.join('?' * len(ids))
                conn.execute(
                    f"UPDATE slotzap_afiliado_pagamentos "
                    f"SET status='enviando', lote_ref=?, tentativas=IFNULL(tentativas,0)+1 "
                    f"WHERE slot_id IN ({qs}) AND status IN ('pendente','erro')",
                    (ref, *ids))
                conn.commit()
                pago_total += _sz_flush_lote(conn, ref)
            except Exception as _e:
                log.warning(f'[SlotZap] lote afiliado {afid}: {_e}')
    return round(pago_total, 2)


def _sz_comissao_reconciliar_loop():
    """Robô 24/7 (rede de segurança): paga comissões de vendas JÁ pagas e atribuídas
    a um vendedor que ficaram SEM repasse (qualquer caminho que pulou o pagamento inline,
    corrida, trava anti-duplicata do Asaas, lock do banco, etc.). Agora paga EM LOTE
    (1 PIX por vendedor) via _sz_pagar_pendentes — idempotente e à prova de queda."""
    time.sleep(120)  # deixa o app subir
    log.info('[SlotZap] Reconciliador de COMISSÕES ATIVO (lote, verifica a cada ~75s)')
    while True:
        try:
            conn = get_saas_db()
            pago = _sz_pagar_pendentes(conn)
            if pago:
                log.info(f'[SlotZap] Reconciliador comissão: liquidou R${pago:.2f} em lote(s)')
        except Exception as _e:
            log.error(f'[SlotZap] reconciliador comissão loop: {_e}')
        finally:
            try: conn.close()
            except Exception: pass
        time.sleep(75)  # rede de segurança rápida


threading.Thread(target=_sz_comissao_reconciliar_loop, daemon=True, name='sz-comissao-recon').start()


SZ_RESERVA_EXPIRA_MIN = 30  # libera o número se a reserva não for paga neste tempo

def _sz_expirar_reservas(camp_id):
    """Libera slots reservados (não pagos) que passaram do tempo de expiração.
    ANTES de liberar, confere no Asaas se a cobrança foi PAGA — se foi, credita o número
    em vez de liberar (evita 'pagou atrasado e ficou sem número')."""
    limite = (datetime.now() - timedelta(minutes=SZ_RESERVA_EXPIRA_MIN)).isoformat()
    conn = get_saas_db()
    _gwrow = conn.execute("SELECT IFNULL(gateway,'asaas') AS gw FROM slotzap_campanhas WHERE id=?",
                          (camp_id,)).fetchone()
    gw = dict(_gwrow)['gw'] if _gwrow else 'asaas'
    expirados = [dict(r) for r in conn.execute(
        "SELECT id, asaas_charge_id FROM slotzap_slots "
        "WHERE campanha_id=? AND status='reservado' AND reservado_em!='' AND reservado_em < ?",
        (camp_id, limite)).fetchall()]
    conn.close()
    if not expirados:
        return 0
    pagos_recuperar, a_liberar = [], []
    for s in expirados:
        cid = (s.get('asaas_charge_id') or '').strip()
        pago = _sz_pagamento_confirmado(gw, cid) if cid else False
        if pago:
            pagos_recuperar.append(cid)          # estava paga! credita
        else:
            _sz_cancelar_cobranca(gw, cid)        # cancela p/ não pagarem (asaas DELETE; efi expira só)
            a_liberar.append(s['id'])
    # Credita as que estavam pagas (cada uma abre/fecha sua conexão — slot ainda 'reservado')
    for cid in pagos_recuperar:
        try: _sz_marcar_pago_charge(cid)
        except Exception as _e: log.warning(f'[SlotZap] expirar/recuperar pago: {_e}')
    # Libera de fato as não pagas
    if a_liberar:
        conn = get_saas_db()
        for sid in a_liberar:
            conn.execute(
                "UPDATE slotzap_slots SET status='disponivel',cliente_nome='',cliente_tel='',"
                "asaas_charge_id='',pix_qr_code='',pix_copia_cola='',reservado_em='',"
                "afiliado_codigo='',indicado_por='' WHERE id=?",
                (sid,))
        conn.commit(); conn.close()
    return len(a_liberar)


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
    # fetchAllGroups do Evolution é lento — timeout generoso + 1 retry
    last_err = ''
    for tentativa in range(2):
        try:
            r = requests.get(f'{evo_url}/group/fetchAllGroups/{inst}?getParticipants=false',
                             headers={'apikey': evo_key}, timeout=45)
            data = r.json() if r.content else []
            grupos = [{'id': g.get('id'), 'nome': g.get('subject', g.get('id'))}
                      for g in (data if isinstance(data, list) else [])]
            return jsonify({'grupos': grupos})
        except requests.exceptions.Timeout:
            last_err = 'timeout'
        except Exception as e:
            last_err = str(e)
            break
    return jsonify({'grupos': [], 'erro': last_err})


@app.route('/slotzap/campanha/<int:camp_id>/test-wpp')
@_sz_login_required
def slotzap_test_wpp(camp_id):
    """Diagnóstico: envia uma mensagem de teste ao grupo configurado e retorna
    a resposta crua do Evolution API (para descobrir erros de envio)."""
    conn = get_saas_db()
    camp = conn.execute('SELECT grupo_wpp_id, evo_instance FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    conn.close()
    if not camp:
        return jsonify({'erro': 'campanha não encontrada'}), 404
    camp     = dict(camp)
    grupo_id = (camp.get('grupo_wpp_id') or '').strip()
    instance = (camp.get('evo_instance') or '').strip() or os.environ.get('EVO_INSTANCE', '')
    evo_url  = (os.environ.get('EVO_URL') or os.environ.get('EVOLUTION_API_URL') or '').rstrip('/')
    evo_key  = os.environ.get('EVO_KEY') or os.environ.get('EVOLUTION_API_KEY') or ''
    if not grupo_id:
        return jsonify({'erro': 'campanha sem grupo configurado'})
    if not evo_url or not instance:
        return jsonify({'erro': f'config incompleta: evo_url={bool(evo_url)} instance={instance}'})
    try:
        r = requests.post(
            f"{evo_url}/message/sendText/{instance}",
            headers={'apikey': evo_key, 'Content-Type': 'application/json'},
            json={'number': grupo_id, 'text': '🔔 Teste SlotZap — se você vê isto no grupo, as notificações estão OK!'},
            timeout=20)
        return jsonify({'status': r.status_code, 'instance': instance, 'grupo': grupo_id,
                        'resposta': (r.json() if r.content else {})})
    except Exception as e:
        return jsonify({'erro': str(e), 'instance': instance, 'grupo': grupo_id})


@app.route('/slotzap/campanha/<int:camp_id>/enviar-lista', methods=['POST'])
@_sz_login_required
def slotzap_enviar_lista(camp_id):
    """Envia a lista de números (só nomes, sem expor pago/total) ao grupo do WhatsApp."""
    if not _sz_plan_active():
        return jsonify({'erro': 'Assinatura inativa.'}), 402
    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    camp  = dict(camp)
    slots = [dict(r) for r in conn.execute(
        'SELECT numero, status, cliente_nome FROM slotzap_slots WHERE campanha_id=? ORDER BY numero',
        (camp_id,)).fetchall()]
    # Mensagem personalizada (cabeçalho da lista) — salva pra reutilizar
    msg_topo = ((request.get_json(silent=True) or {}).get('msg') or '').strip()
    conn.execute('UPDATE slotzap_campanhas SET msg_lista=? WHERE id=?', (msg_topo, camp_id))
    conn.commit()
    conn.close()

    grupo_id = (camp.get('grupo_wpp_id') or '').strip()
    if not grupo_id:
        return jsonify({'erro': 'Configure o grupo do WhatsApp primeiro (botão 💬 WhatsApp).'}), 400
    instance = (camp.get('evo_instance') or '').strip() or os.environ.get('EVO_INSTANCE', '')
    evo_url  = (os.environ.get('EVO_URL') or os.environ.get('EVOLUTION_API_URL') or '').rstrip('/')
    evo_key  = os.environ.get('EVO_KEY') or os.environ.get('EVOLUTION_API_KEY') or ''
    if not evo_url or not instance:
        return jsonify({'erro': 'WhatsApp não configurado.'}), 400

    # Monta as linhas: número - nome (sem diferenciar pago/reservado, sem expor total)
    pad    = len(str(max((s['numero'] for s in slots), default=1)))
    linhas = []
    for s in slots:
        nome = s['cliente_nome'] if s['status'] in ('reservado', 'pago') else ''
        linhas.append(f"{str(s['numero']).zfill(pad)} - {nome}".rstrip())

    # Quebra em blocos de ~3500 caracteres (limite do WhatsApp ~4096)
    header = (msg_topo + "\n\n") if msg_topo else f"🎯 *{camp['nome']}* — Lista de números\n\n"
    blocos, atual = [], header
    for ln in linhas:
        if len(atual) + len(ln) + 1 > 3500:
            blocos.append(atual)
            atual = ''
        atual += (('\n' if atual else '') + ln)
    if atual.strip():
        blocos.append(atual)

    enviados = 0
    ultimo_erro = ''
    for txt in blocos:
        try:
            r = requests.post(
                f"{evo_url}/message/sendText/{instance}",
                headers={'apikey': evo_key, 'Content-Type': 'application/json'},
                json={'number': grupo_id, 'text': txt}, timeout=(10, 60))
            if r.status_code in (200, 201):
                enviados += 1
            else:
                _resp = (r.text or '')
                if r.status_code == 404 and 'does not exist' in _resp:
                    ultimo_erro = ('O número do bot (WhatsApp) está desconectado. Abra o botão '
                                   '💬 WhatsApp, selecione um número conectado e clique em Buscar grupos.')
                else:
                    ultimo_erro = f'Evolution HTTP {r.status_code}: {_resp[:180]}'
                log.warning(f'[SlotZap] enviar-lista falhou {r.status_code}: {_resp[:400]}')
        except Exception as _e:
            ultimo_erro = str(_e)[:180]
            log.warning(f'[SlotZap] Erro ao enviar lista: {_e}')
    if enviados:
        return jsonify({'ok': True, 'mensagens': enviados})
    return jsonify({'erro': f'Não foi possível enviar. {ultimo_erro or "Verifique o grupo/instância."}'}), 502


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
    conn.close()
    _sz_expirar_reservas(camp['id'])
    conn  = get_saas_db()
    slots = [dict(r) for r in conn.execute(
        'SELECT numero, status FROM slotzap_slots WHERE campanha_id=? ORDER BY numero',
        (camp['id'],)
    ).fetchall()]
    # White-label (plano Pro): marca e cor do dono
    dono  = conn.execute('SELECT plan, plan_active, marca, cor FROM slotzap_users WHERE id=?',
                         (camp['user_id'],)).fetchone()
    conn.close()
    marca, cor = '', ''
    if dono:
        dono = dict(dono)
        if dono.get('plan') == 'pro' and dono.get('plan_active'):
            marca = (dono.get('marca') or '').strip()
            cor   = (dono.get('cor') or '').strip()
    # RifaJá: campanha no gateway Efí usa a marca própria (se o dono não tem white-label)
    if not marca and camp.get('gateway') == 'efi':
        marca = '🎟️ RifaJá'
    pagos      = sum(1 for s in slots if s['status'] == 'pago')
    reservados = sum(1 for s in slots if s['status'] == 'reservado')
    disponiveis= sum(1 for s in slots if s['status'] == 'disponivel')
    total      = len(slots)
    pct        = round(pagos / total * 100) if total else 0
    # Sorteio auditável: garante o commit (publicado antes) e só revela o seed DEPOIS do sorteio
    conn = get_saas_db()
    _sz_seed_commit(conn, camp['id'], camp)
    conn.close()
    if not camp.get('ganhador_numero'):
        camp['sorteio_seed'] = ''   # NUNCA expõe o código secreto antes do resultado
    return render_template('slotzap/publico.html',
                           camp=camp, slots=slots, token=token,
                           pagos=pagos, reservados=reservados, disponiveis=disponiveis,
                           pct=pct, marca=marca, cor=cor)


@app.route('/slotzap/p/<token>/status')
def slotzap_publico_status(token):
    """Polling — retorna status atual dos slots para atualização em tempo real."""
    conn  = get_saas_db()
    camp  = conn.execute('SELECT id FROM slotzap_campanhas WHERE token_publico=?', (token,)).fetchone()
    conn.close()
    if not camp:
        return jsonify({'erro': 'not found'}), 404
    cid = dict(camp)['id']
    _sz_expirar_reservas(cid)
    conn  = get_saas_db()
    slots = conn.execute(
        'SELECT numero, status FROM slotzap_slots WHERE campanha_id=? ORDER BY numero', (cid,)
    ).fetchall()
    # Feed de prova social: últimos pagos (só 1º nome, por privacidade)
    recs  = conn.execute(
        "SELECT numero, cliente_nome FROM slotzap_slots "
        "WHERE campanha_id=? AND status='pago' AND pago_em<>'' "
        "ORDER BY pago_em DESC LIMIT 12", (cid,)
    ).fetchall()
    conn.close()
    def _primeiro_nome(n):
        p = (n or '').strip().split()
        return p[0][:18].title() if p else 'Alguém'
    recentes = [{'numero': dict(r)['numero'], 'nome': _primeiro_nome(dict(r)['cliente_nome'])}
                for r in recs]
    return jsonify({'slots': {str(s['numero']): s['status'] for s in slots},
                    'recentes': recentes})


@app.route('/slotzap/p/<token>/confirmar', methods=['POST'])
def slotzap_publico_confirmar(token):
    """Confere ativamente no Asaas se o PIX do slot foi pago e dá baixa.
    Usado pelo botão 'Já paguei' — funciona mesmo sem webhook."""
    data   = request.get_json() or {}
    numero = int(data.get('numero', 0))
    conn = get_saas_db()
    camp = conn.execute("SELECT id, IFNULL(gateway,'asaas') AS gateway "
                        "FROM slotzap_campanhas WHERE token_publico=?", (token,)).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'not found'}), 404
    camp = dict(camp)
    slot = conn.execute('SELECT * FROM slotzap_slots WHERE campanha_id=? AND numero=?',
                        (camp['id'], numero)).fetchone()
    conn.close()
    if not slot:
        return jsonify({'erro': 'slot não encontrado'}), 404
    slot = dict(slot)
    if slot['status'] == 'pago':
        return jsonify({'pago': True})
    charge_id = (slot.get('asaas_charge_id') or '').strip()
    if not charge_id:
        return jsonify({'pago': False})
    if _sz_pagamento_confirmado(camp['gateway'], charge_id):
        _sz_marcar_pago_charge(charge_id)
        return jsonify({'pago': True})
    return jsonify({'pago': False})


@app.route('/slotzap/p/<token>/reservar', methods=['POST'])
def slotzap_publico_reservar(token):
    """Reserva slot publicamente (sem login do admin)."""
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
    if not _sz_rate_ok(ip):
        return jsonify({'erro': 'Muitas tentativas. Aguarde alguns minutos e tente novamente.'}), 429
    data         = request.get_json() or {}
    cliente_nome = (data.get('nome') or '').strip()
    cliente_cpf  = ''.join(c for c in (data.get('cpf') or '') if c.isdigit())
    cliente_tel  = ''.join(c for c in (data.get('tel') or '') if c.isdigit())
    ref_in       = (data.get('ref') or '').strip()[:32]   # código de quem indicou (opcional)
    aff_in       = (data.get('aff') or '').strip()[:32]   # código do afiliado/vendedor (opcional)
    # Aceita 'numeros' (lista) ou 'numero' (único, compatibilidade)
    brutos = data.get('numeros') if data.get('numeros') is not None else [data.get('numero', 0)]
    try:
        numeros = sorted({int(n) for n in brutos if int(n) > 0})
    except (TypeError, ValueError):
        numeros = []

    if not numeros:
        return jsonify({'erro': 'Escolha pelo menos um número.'}), 400
    if len(numeros) > 50:
        return jsonify({'erro': 'Máximo de 50 números por compra.'}), 400
    if not cliente_nome:
        return jsonify({'erro': 'Nome obrigatório'}), 400
    if not _cpf_valido(cliente_cpf):
        return jsonify({'erro': 'CPF inválido. Confira os números.'}), 400
    if len(cliente_tel) < 10:
        return jsonify({'erro': 'Informe seu WhatsApp com DDD — é por ele que você recebe o número e o aviso se ganhar.'}), 400

    conn  = get_saas_db()
    camp  = conn.execute('SELECT * FROM slotzap_campanhas WHERE token_publico=? AND status="ativa"',
                         (token,)).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    camp = dict(camp)
    ph    = ','.join('?' * len(numeros))
    slots = [dict(r) for r in conn.execute(
        f'SELECT * FROM slotzap_slots WHERE campanha_id=? AND numero IN ({ph})',
        (camp['id'], *numeros)).fetchall()]
    if len(slots) != len(numeros):
        conn.close()
        return jsonify({'erro': 'Algum número não existe nesta campanha.'}), 404
    ocupados = sorted(s['numero'] for s in slots if s['status'] != 'disponivel')
    if ocupados:
        conn.close()
        return jsonify({'erro': 'Já reservado(s): ' + ', '.join('#' + str(n) for n in ocupados)
                        + '. Atualize a página e escolha outros.'}), 400

    preco = float(camp['preco'])
    total = round(preco * len(numeros), 2)
    _ow   = conn.execute('SELECT asaas_wallet_id FROM slotzap_users WHERE id=?', (camp['user_id'],)).fetchone()
    owner_wallet = (dict(_ow).get('asaas_wallet_id') or '').strip() if _ow else ''
    first_id = slots[0]['id']
    desc = (f"SlotZap — {camp['nome']} — "
            + (f"{len(numeros)} números" if len(numeros) > 1 else f"Slot #{numeros[0]}"))

    # Uma única cobrança PIX cobre todos os números — só reserva se gerar o PIX
    erro_msg, charge_id, pix_qr, pix_copia = _sz_gerar_pix(
        cliente_nome, cliente_tel, cliente_cpf, total, desc, f'sz_{first_id}', owner_wallet,
        gateway=camp.get('gateway', 'asaas'))
    if erro_msg:
        conn.close()
        return jsonify({'erro': erro_msg}), 502

    agora = datetime.now().isoformat()
    indic_ativa = bool(camp.get('indicacao_ativa'))
    ref_save = ref_in if indic_ativa else ''
    # Afiliado: só grava o código se a campanha tem afiliados ON e o código existe nela
    aff_save = ''
    if camp.get('afiliados_ativo') and aff_in and conn.execute(
            'SELECT 1 FROM slotzap_afiliados WHERE campanha_id=? AND codigo=?',
            (camp['id'], aff_in)).fetchone():
        aff_save = aff_in
    conn.executemany(
        'UPDATE slotzap_slots SET status=?,cliente_nome=?,cliente_tel=?,'
        'asaas_charge_id=?,pix_qr_code=?,pix_copia_cola=?,reservado_em=?,indicado_por=?,afiliado_codigo=? WHERE id=?',
        [('reservado', cliente_nome, cliente_tel, charge_id, pix_qr, pix_copia, agora, ref_save, aff_save, s['id'])
         for s in slots])
    conn.commit()
    # Código de indicação do PRÓPRIO comprador (pra ele compartilhar) — só se a campanha permite e deu telefone
    meu_ref = ''
    if indic_ativa and cliente_tel:
        meu_ref = _sz_ref_get_or_create(conn, camp['id'], cliente_nome, cliente_tel)
    conn.close()
    return jsonify({'ok': True, 'pix_qr': pix_qr, 'pix_copia': pix_copia,
                    'valor': total, 'numeros': numeros,
                    'meu_ref': meu_ref,
                    'indicacao_ativa': indic_ativa,
                    'indicacao_meta': camp.get('indicacao_meta') or 10})


# ─────────────────────────── AFILIADOS / VENDEDORES ───────────────────────────
# Programa de afiliados POR CAMPANHA: a pessoa se cadastra sozinha, ganha um link
# pessoal e recebe comissão em dinheiro (PIX na hora) por cada número vendido.
# Lote 1 = cadastro + painel (aditivo, nada toca o fluxo de venda/pagamento atual).

def _sz_pix_normaliza(tipo, chave):
    """Valida/normaliza a chave PIX conforme o tipo escolhido pelo afiliado.
    Retorna (tipo_canonico_asaas, chave_limpa, erro). Tipos Asaas: CPF, CNPJ, EMAIL, PHONE, EVP."""
    tipo  = (tipo or '').strip().upper()
    chave = (chave or '').strip()
    if tipo == 'CPF':
        d = ''.join(c for c in chave if c.isdigit())
        return ('CPF', d, None) if len(d) == 11 else ('CPF', '', 'A chave PIX tipo CPF precisa ter 11 dígitos.')
    if tipo == 'CNPJ':
        d = ''.join(c for c in chave if c.isdigit())
        return ('CNPJ', d, None) if len(d) == 14 else ('CNPJ', '', 'A chave PIX tipo CNPJ precisa ter 14 dígitos.')
    if tipo in ('CELULAR', 'PHONE', 'TELEFONE'):
        d = ''.join(c for c in chave if c.isdigit())
        if d.startswith('55') and len(d) > 11:
            d = d[2:]
        if len(d) not in (10, 11):
            return ('PHONE', '', 'A chave PIX tipo Celular precisa do DDD + número.')
        return ('PHONE', '+55' + d, None)
    if tipo in ('EMAIL', 'E-MAIL'):
        return ('EMAIL', chave.lower(), None) if ('@' in chave and '.' in chave) else ('EMAIL', '', 'E-mail da chave PIX inválido.')
    if tipo in ('ALEATORIA', 'ALEATÓRIA', 'EVP'):
        return ('EVP', chave, None) if len(chave) >= 32 else ('EVP', '', 'Chave aleatória inválida (copie do app do banco).')
    return (tipo, '', 'Escolha o tipo da chave PIX.')


def _sz_afil_codigo_novo(conn):
    """Gera um código de link curto e único para o afiliado."""
    import secrets as _sec
    for _ in range(6):
        c = _sec.token_urlsafe(6)
        if not conn.execute('SELECT 1 FROM slotzap_afiliados WHERE codigo=?', (c,)).fetchone():
            return c
    return _sec.token_urlsafe(12)


@app.route('/slotzap/p/<token>/afiliado', methods=['GET', 'POST'])
def slotzap_afiliado_cadastro(token):
    """Cadastro público de afiliado (super simples). Cria o vendedor e leva pro painel dele."""
    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE token_publico=? AND status="ativa"',
                        (token,)).fetchone()
    if not camp:
        conn.close()
        return render_template('slotzap/nao_encontrado.html'), 404
    camp = dict(camp)
    comissao = float(camp.get('afiliado_comissao') or 0)

    def _form(erro=''):
        return render_template('slotzap/afiliado_cadastro.html', token=token, camp=camp,
                               comissao=comissao, indisponivel=not camp.get('afiliados_ativo'),
                               erro=erro)

    if not camp.get('afiliados_ativo'):
        conn.close()
        return _form()
    if request.method == 'GET':
        conn.close()
        return _form()

    # POST — cria o afiliado
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
    if not _sz_rate_ok(ip):
        conn.close()
        return _form('Muitas tentativas. Aguarde alguns minutos e tente de novo.')

    f        = request.form
    nome     = (f.get('nome') or '').strip()[:80]
    tel      = ''.join(c for c in (f.get('telefone') or '') if c.isdigit())
    cpf      = ''.join(c for c in (f.get('cpf') or '') if c.isdigit())
    endereco = (f.get('endereco') or '').strip()[:160]
    pix_tipo, pix_chave, perr = _sz_pix_normaliza(f.get('pix_tipo'), f.get('pix_chave'))

    if not nome:
        conn.close(); return _form('Digite seu nome.')
    if len(tel) < 10:
        conn.close(); return _form('Informe seu WhatsApp com DDD.')
    if perr:
        conn.close(); return _form(perr)
    if cpf and not _cpf_valido(cpf):
        conn.close(); return _form('CPF inválido. Confira os números (ou deixe em branco).')

    # Já cadastrado nesta rifa (mesmo telefone)? Leva direto pro painel dele.
    ja = conn.execute('SELECT codigo FROM slotzap_afiliados WHERE campanha_id=? AND telefone=?',
                      (camp['id'], tel)).fetchone()
    if ja:
        codigo = dict(ja)['codigo']
        conn.close()
        return redirect(url_for('slotzap_afiliado_painel', token=token, codigo=codigo))

    codigo = _sz_afil_codigo_novo(conn)
    conn.execute(
        'INSERT INTO slotzap_afiliados (campanha_id,codigo,nome,cpf,telefone,endereco,'
        'pix_chave,pix_tipo,criado_em) VALUES (?,?,?,?,?,?,?,?,?)',
        (camp['id'], codigo, nome, cpf, tel, endereco, pix_chave, pix_tipo,
         datetime.now().isoformat()))
    conn.commit(); conn.close()
    log.info(f'[SlotZap] Novo afiliado "{nome}" na campanha {camp["id"]} (cod {codigo})')
    return redirect(url_for('slotzap_afiliado_painel', token=token, codigo=codigo))


@app.route('/slotzap/p/<token>/afiliado/<codigo>')
def slotzap_afiliado_painel(token, codigo):
    """Painel do afiliado: vê vendas, quanto já ganhou e o link pessoal pra divulgar."""
    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE token_publico=?', (token,)).fetchone()
    if not camp:
        conn.close()
        return render_template('slotzap/nao_encontrado.html'), 404
    camp = dict(camp)
    af = conn.execute('SELECT * FROM slotzap_afiliados WHERE campanha_id=? AND codigo=?',
                      (camp['id'], codigo)).fetchone()
    if not af:
        conn.close()
        return render_template('slotzap/nao_encontrado.html'), 404
    af = dict(af)
    # Conta AO VIVO direto dos slots (mais confiável que o contador) + total já recebido (ledger)
    vendas = conn.execute(
        "SELECT COUNT(*) FROM slotzap_slots WHERE campanha_id=? AND afiliado_codigo=? AND status='pago'",
        (camp['id'], codigo)).fetchone()[0]
    ganho_pago = conn.execute(
        "SELECT IFNULL(SUM(valor),0) FROM slotzap_afiliado_pagamentos WHERE afiliado_id=? AND status='pago'",
        (af['id'],)).fetchone()[0]
    conn.close()
    comissao = float(camp.get('afiliado_comissao') or 0)
    base = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
    link = f"{base}/slotzap/p/{token}?aff={codigo}"
    return render_template('slotzap/afiliado_painel.html', token=token, camp=camp, af=af,
                           link=link, vendas=vendas, ganho_pago=ganho_pago, comissao=comissao,
                           a_receber=round(vendas * comissao - ganho_pago, 2))


@app.route('/slotzap/p/<token>/afiliado/<codigo>/totem')
def slotzap_afiliado_totem(token, codigo):
    """Totem A4 pronto pra imprimir, JÁ com o QR do link pessoal (?aff=) do afiliado.
    Marketing (título, valor do prêmio, banner do despachante) é editável no navegador."""
    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE token_publico=?', (token,)).fetchone()
    if not camp:
        conn.close()
        return render_template('slotzap/nao_encontrado.html'), 404
    camp = dict(camp)
    af = conn.execute('SELECT * FROM slotzap_afiliados WHERE campanha_id=? AND codigo=?',
                      (camp['id'], codigo)).fetchone()
    conn.close()
    if not af:
        return render_template('slotzap/nao_encontrado.html'), 404
    af = dict(af)
    base = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
    link_compra = f"{base}/slotzap/p/{token}?aff={codigo}"
    # Telefone do vendedor formatado pra exibir no totem (dúvidas dos clientes)
    _d = ''.join(c for c in (af.get('telefone') or '') if c.isdigit())
    if _d.startswith('55') and len(_d) > 11:
        _d = _d[2:]
    if len(_d) == 11:
        tel_fmt = f"({_d[:2]}) {_d[2:7]}-{_d[7:]}"
    elif len(_d) == 10:
        tel_fmt = f"({_d[:2]}) {_d[2:6]}-{_d[6:]}"
    else:
        tel_fmt = af.get('telefone') or ''
    return render_template('slotzap/afiliado_totem.html', camp=camp, af=af,
                           link_compra=link_compra, tel_fmt=tel_fmt,
                           grupo_convite=(camp.get('grupo_convite') or ''))


@app.route('/slotzap/campanha/<int:camp_id>/afiliados')
@_sz_login_required
def slotzap_afiliados_admin(camp_id):
    """Painel do DONO: liga/desliga afiliados, define comissão e vê a lista de vendedores."""
    if not _sz_plan_active():
        return redirect('/slotzap/assinar')
    conn = get_saas_db()
    camp = conn.execute('SELECT * FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return redirect('/slotzap/app')
    camp = dict(camp)
    afs = [dict(r) for r in conn.execute('''
        SELECT a.*,
          (SELECT COUNT(*) FROM slotzap_slots s
             WHERE s.campanha_id=a.campanha_id AND s.afiliado_codigo=a.codigo AND s.status='pago') AS vendas_pagas,
          (SELECT IFNULL(SUM(p.valor),0) FROM slotzap_afiliado_pagamentos p
             WHERE p.afiliado_id=a.id AND p.status='pago') AS pago_total,
          (SELECT COUNT(*) FROM slotzap_afiliado_pagamentos p
             WHERE p.afiliado_id=a.id AND p.status='erro') AS erros,
          (SELECT COUNT(*) FROM slotzap_afiliado_pagamentos p
             WHERE p.afiliado_id=a.id AND p.status IN ('pendente','erro')) AS a_pagar,
          (SELECT p.erro FROM slotzap_afiliado_pagamentos p
             WHERE p.afiliado_id=a.id AND IFNULL(p.erro,'')<>'' ORDER BY p.id DESC LIMIT 1) AS ultimo_erro
        FROM slotzap_afiliados a WHERE a.campanha_id=?
        ORDER BY vendas_pagas DESC, a.criado_em''', (camp_id,)).fetchall()]
    conn.close()
    base  = os.environ.get('BASE_URL', 'https://www.4kitem.com.br').rstrip('/')
    token = camp.get('token_publico') or ''
    cad_link = f"{base}/slotzap/p/{token}/afiliado" if token else ''
    return render_template('slotzap/afiliados_admin.html', camp=camp, afs=afs,
                           token=token, cad_link=cad_link)


@app.route('/slotzap/campanha/<int:camp_id>/afiliados/config', methods=['POST'])
@_sz_login_required
def slotzap_afiliados_config(camp_id):
    """Salva ligar/desligar + comissão + link do grupo da campanha."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'ativo' not in data:
        return jsonify({'erro': 'payload inválido'}), 400   # evita zerar config por engano
    ativo = 1 if str(data.get('ativo')) in ('1', 'true', 'on', 'True') else 0
    try:
        comissao = max(0.0, float(str(data.get('comissao') or '0').replace(',', '.')))
    except (TypeError, ValueError):
        comissao = 0.0
    grupo = (data.get('grupo_convite') or '').strip()[:300]
    conn  = get_saas_db()
    camp  = conn.execute('SELECT id, preco FROM slotzap_campanhas WHERE id=? AND user_id=?',
                         (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    preco = float(dict(camp).get('preco') or 0)
    # Anti-prejuízo/anti-farming: comissão nunca pode ser >= o preço do número
    if ativo and preco > 0 and comissao >= preco:
        conn.close()
        return jsonify({'erro': f'A comissão (R$ {comissao:.2f}) não pode ser maior ou igual ao '
                                f'preço do número (R$ {preco:.2f}) — daria prejuízo. Reduza a comissão.'}), 400
    conn.execute('UPDATE slotzap_campanhas SET afiliados_ativo=?, afiliado_comissao=?, grupo_convite=? '
                 'WHERE id=? AND user_id=?', (ativo, comissao, grupo, camp_id, _sz_uid()))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'ativo': ativo, 'comissao': comissao})


@app.route('/slotzap/campanha/<int:camp_id>/afiliados/pagar-pendentes', methods=['POST'])
@_sz_login_required
def slotzap_afiliados_pagar_pendentes(camp_id):
    """Botão do dono: paga AGORA todas as comissões pendentes/erro desta campanha,
    agrupadas por vendedor (1 PIX cada). Mesma engine do reconciliador, idempotente."""
    if not _sz_plan_active():
        return jsonify({'erro': 'assinatura inativa'}), 403
    conn = get_saas_db()
    camp = conn.execute('SELECT id FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not camp:
        conn.close()
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    try:
        pago = _sz_pagar_pendentes(conn, camp_id, force=True)
    except Exception as _e:
        log.warning(f'[SlotZap] pagar-pendentes manual camp {camp_id}: {_e}')
        conn.close()
        return jsonify({'erro': 'Falha ao processar — tente de novo.'}), 500
    err_row = conn.execute(
        "SELECT erro FROM slotzap_afiliado_pagamentos WHERE campanha_id=? AND status='erro' "
        "AND IFNULL(erro,'')<>'' ORDER BY id DESC LIMIT 1", (camp_id,)).fetchone()
    ultimo_erro = dict(err_row)['erro'] if err_row else ''
    conn.close()
    return jsonify({'ok': True, 'pago': pago, 'ultimo_erro': ultimo_erro})


@app.route('/webhook/asaas/saque-validacao', methods=['POST'])
def asaas_saque_validacao():
    """Webhook de VALIDAÇÃO DE SAQUE do Asaas (Integrações → Segurança).
    O Asaas chama esta URL pra cada transferência via API; respondemos APPROVED/REFUSED.
    Aprova automaticamente APENAS os saques que o SlotZap gerou (comissão de afiliado,
    externalReference 'szaf_'); recusa qualquer outro — anti-abuso se a chave vazar."""
    token_cfg = os.environ.get('ASAAS_SAQUE_TOKEN', '')
    token_req = (request.headers.get('asaas-access-token')
                 or request.headers.get('Asaas-Access-Token') or '')
    if token_cfg and token_req != token_cfg:
        log.warning('[SlotZap] saque-validacao: token invalido')
        return jsonify({'status': 'REFUSED', 'refuseReason': 'token invalido'}), 200
    data     = request.get_json(silent=True) or {}
    transfer = data.get('transfer') or {}
    ext      = (transfer.get('externalReference') or '')
    val      = transfer.get('value')
    # Aprova comissões do SlotZap. Sem ref (ext vazio) também aprova: nesta conta todo
    # saque via API é comissão de afiliado iniciada por nós.
    if ext.startswith('szaf_') or ext == '':
        log.info(f'[SlotZap] saque-validacao APPROVED ref={ext} valor={val}')
        return jsonify({'status': 'APPROVED'}), 200
    log.warning(f'[SlotZap] saque-validacao REFUSED ref={ext} valor={val}')
    return jsonify({'status': 'REFUSED', 'refuseReason': 'saque nao reconhecido pelo SlotZap'}), 200


def _sz_check_senha_conta(conn, senha):
    """Confere a senha de LOGIN da conta SlotZap do usuário logado."""
    u = conn.execute('SELECT password_hash FROM slotzap_users WHERE id=?', (_sz_uid(),)).fetchone()
    return bool(u) and check_password_hash(dict(u)['password_hash'], senha or '')


@app.route('/slotzap/campanha/<int:camp_id>/encerrar', methods=['POST'])
@_sz_login_required
def slotzap_encerrar(camp_id):
    senha = (request.get_json(silent=True) or {}).get('senha') or ''
    conn = get_saas_db()
    if not _sz_check_senha_conta(conn, senha):
        conn.close()
        return jsonify({'erro': 'senha_errada', 'msg': 'Senha da conta incorreta.'}), 403
    conn.execute("UPDATE slotzap_campanhas SET status='encerrada' WHERE id=? AND user_id=?",
                 (camp_id, _sz_uid()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/slotzap/campanha/<int:camp_id>/reabrir', methods=['POST'])
@_sz_login_required
def slotzap_reabrir(camp_id):
    """Reabre uma campanha ENCERRADA (volta a vender). Não reabre canceladas/estornadas."""
    senha = (request.get_json(silent=True) or {}).get('senha') or ''
    conn = get_saas_db()
    if not _sz_check_senha_conta(conn, senha):
        conn.close()
        return jsonify({'erro': 'senha_errada', 'msg': 'Senha da conta incorreta.'}), 403
    cur = conn.execute("UPDATE slotzap_campanhas SET status='ativa' "
                       "WHERE id=? AND user_id=? AND status='encerrada'", (camp_id, _sz_uid()))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    if not ok:
        return jsonify({'erro': 'Só dá pra reabrir campanhas encerradas (cancelada/estornada não reabre).'}), 400
    return jsonify({'ok': True})


@app.route('/slotzap/campanha/<int:camp_id>/marcar-pago-manual', methods=['POST'])
@_sz_login_required
def slotzap_marcar_pago_manual(camp_id):
    """Marca um número como PAGO manualmente (ex.: cliente pagou fora do fluxo ou após a
    reserva expirar). Conta como venda REAL (brinde=0). Protegido pela senha de login."""
    if not _sz_plan_active():
        return jsonify({'erro': 'Assinatura inativa.'}), 402
    data = request.get_json() or {}
    try:    numero = int(data.get('numero') or 0)
    except (TypeError, ValueError): numero = 0
    nome = (data.get('nome') or '').strip()[:80]
    tel  = ''.join(c for c in (data.get('tel') or '') if c.isdigit())
    conn = get_saas_db()
    if not _sz_check_senha_conta(conn, data.get('senha') or ''):
        conn.close()
        return jsonify({'erro': 'senha_errada', 'msg': 'Senha da conta incorreta.'}), 403
    dono = conn.execute('SELECT 1 FROM slotzap_campanhas WHERE id=? AND user_id=?',
                        (camp_id, _sz_uid())).fetchone()
    if not dono:
        conn.close()
        return jsonify({'erro': 'Campanha não encontrada'}), 404
    slot = conn.execute('SELECT id, status FROM slotzap_slots WHERE campanha_id=? AND numero=?',
                        (camp_id, numero)).fetchone()
    if not slot:
        conn.close()
        return jsonify({'erro': 'Esse número não existe na campanha.'}), 404
    slot = dict(slot)
    if slot['status'] == 'pago':
        conn.close()
        return jsonify({'erro': 'Esse número já está pago.'}), 400
    conn.execute("UPDATE slotzap_slots SET status='pago', cliente_nome=?, cliente_tel=?, "
                 "pago_em=?, brinde=0 WHERE id=?",
                 (nome or 'Pagamento manual', tel, datetime.now().isoformat(), slot['id']))
    conn.commit(); conn.close()
    log.info(f'[SlotZap] Slot #{numero} (camp {camp_id}) marcado PAGO MANUAL — {nome}')
    return jsonify({'ok': True, 'numero': numero})


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
