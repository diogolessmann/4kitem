"""
licita_norte.py — Radar Licita Norte (módulo do 4kitem)

Versão REGIONAL do Radar: licitações das 6 cidades do Norte de SC
(Schroeder, Guaramirim, Jaraguá do Sul, Joinville, Massaranduba, Corupá),
TODAS as categorias, faixa R$1k–200k ("licitações fáceis"), com selo 🗞️ pras
de notícia/comunicação (filé pra Rádio SC News).

Reusa o MOTOR do Radar (mesmo banco radar_licitacoes, mesma IA, mesmo PNCP) —
não duplica coletor. Marca/login/paywall próprios. SaaS: admin grátis + pago.
"""
import os
import logging
import secrets
import threading
import json as _json
from datetime import datetime, timedelta
from functools import wraps

from flask import (Blueprint, request, jsonify, render_template_string,
                   redirect, session)
from werkzeug.security import generate_password_hash, check_password_hash

# Reusa o motor do Radar (IA, coletor, e-mail)
from radar import analisar_edital, _texto_de_pdf, _enviar_email, coletar, coletar_contratos, _ASSINAR, _COMPRAR, _LP
from radar_db import (obter_licitacao, salvar_analise, radar_exec,
                      get_licita_user, get_licita_user_by_email, contar_licita_users,
                      criar_licita_user, listar_licita_users,
                      listar_licita_norte, stats_licita_norte, contagem_cidades_norte)

log = logging.getLogger('licita_norte')
licita_bp = Blueprint('licita_norte', __name__, url_prefix='/licita-norte')

ADMIN_EMAIL    = os.environ.get('ADMIN_EMAIL', '').strip().lower()
LICITA_VMIN    = 0          # faixa fixa (sem env): R$0 a R$500.000
LICITA_VMAX    = 500000
LICITA_PRECO   = os.environ.get('LICITA_PRECO', '67')
LICITA_WHATS   = os.environ.get('LICITA_WHATSAPP', '').strip()
CIDADES_TXT    = 'Norte de SC + Vale do Itajaí · raio ~70km · 29 cidades'


# ── Auth ─────────────────────────────────────────────────────────────────────
def _user():
    uid = session.get('licita_user_id')
    return get_licita_user(uid) if uid else None


def _is_admin(u):
    return bool(u and (u.get('is_admin') or (ADMIN_EMAIL and u.get('email') == ADMIN_EMAIL)))


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get('licita_user_id'):
            return redirect('/licita-norte/entrar')
        return f(*a, **k)
    return w


def pago_required(f):
    @wraps(f)
    def w(*a, **k):
        u = _user()
        if not u:
            return redirect('/licita-norte/entrar')
        if not _is_admin(u) and not u.get('plan_active'):
            return render_template_string(_AGUARDANDO, nome=u.get('nome', ''),
                                          whatsapp=LICITA_WHATS, preco=LICITA_PRECO)
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        u = _user()
        if not _is_admin(u):
            return redirect('/licita-norte/entrar') if not u else ('Acesso restrito.', 403)
        return f(*a, **k)
    return w


@licita_bp.context_processor
def _inject():
    u = _user()
    return {'lic_nome': (u or {}).get('nome', ''), 'lic_is_admin': _is_admin(u)}


@licita_bp.route('/lp')
@licita_bp.route('/sobre')
def rota_lp():
    if session.get('licita_user_id'):
        return redirect('/licita-norte/')
    return render_template_string(
        _LP, base='/licita-norte', marca='Radar Licita Norte',
        cor='#2f8f5e', cor2='#5ee0a0', selo='🗞️ Norte de SC + Vale do Itajaí',
        logo='/static/img/licita/logo.webp', hero='/static/img/licita/hero.webp',
        og='/static/img/licita/og.jpg',
        titulo='As licitações <span class="hl">perto de você</span>, num lugar só',
        sub='Schroeder, Guaramirim, Jaraguá, Joinville, Massaranduba, Corupá e região — '
            'todas as áreas, de R$ 1 mil a R$ 500 mil. As fáceis, do seu lado.',
        cta='Quero começar', preco='R$ 67',
        publico='Pra empresa e MEI da região que quer pegar licitação pequena e média perto de casa — qualquer ramo.',
        dor_t='A prefeitura do seu lado abre licitação e você nem fica sabendo.',
        dor='Cada cidade publica num canto. Olhar 29 portais todo dia é impossível. O Licita '
            'Norte junta tudo num painel só, filtra por cidade e te avisa do que dá pra você.',
        beneficios=[
            {'ico': '🗺️', 'tit': '29 cidades, 1 painel',
             'txt': 'Norte de SC e Vale do Itajaí reunidos. Filtre por cidade: todas de Joinville, todas de Schroeder, num clique.'},
            {'ico': '🤖', 'tit': 'IA diz se vale PRA VOCÊ',
             'txt': 'Buffet, obra, uniforme, TI, notícia... a IA lê o edital sob a ótica da SUA área e diz se compensa.'},
            {'ico': '🗞️', 'tit': 'Selo de notícia',
             'txt': 'Licitações que viram pauta ganham selo — ouro pra quem tem portal de notícia local.'},
        ],
        passos=[
            {'t': 'Cadastre-se', 'd': 'Conta em 1 minuto. Diga o que sua empresa faz.'},
            {'t': 'Ative o PIX', 'd': 'Assinatura mensal pelo Asaas. Sem fidelidade.'},
            {'t': 'Receba o filé', 'd': 'Licitações da sua região filtradas + análise da IA.'},
        ],
        inclui=['29 cidades do Norte de SC e Vale do Itajaí', 'Filtro por cidade e por valor (R$1k–500k)',
                'Selo de notícia 🗞️', '10 análises de IA por mês inclusas',
                'Créditos extras quando precisar (a partir de R$19)'])


@licita_bp.route('/cadastrar', methods=['GET', 'POST'])
def rota_cadastrar():
    if session.get('licita_user_id'):
        return redirect('/licita-norte/')
    erro = None
    if request.method == 'POST':
        nome  = (request.form.get('nome') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        tel   = ''.join(c for c in (request.form.get('telefone') or '') if c.isdigit())
        senha = request.form.get('senha') or ''
        if not nome or not email or not senha:
            erro = 'Preencha nome, e-mail e senha.'
        elif len(senha) < 6:
            erro = 'Senha de pelo menos 6 caracteres.'
        elif get_licita_user_by_email(email):
            erro = 'Já existe conta com esse e-mail. Faça login.'
        else:
            admin = 1 if (contar_licita_users() == 0 or (ADMIN_EMAIL and email == ADMIN_EMAIL)) else 0
            uid = criar_licita_user(nome, email, tel, generate_password_hash(senha), admin)
            from radar_db import set_area
            set_area('licita_users', uid, request.form.get('area', ''))
            session['licita_user_id'] = uid
            return redirect('/licita-norte/')
    return render_template_string(_AUTH, modo='cadastrar', erro=erro, preco=LICITA_PRECO)


@licita_bp.route('/entrar', methods=['GET', 'POST'])
def rota_entrar():
    if session.get('licita_user_id'):
        return redirect('/licita-norte/')
    if request.method == 'GET' and contar_licita_users() == 0:
        return redirect('/licita-norte/cadastrar')
    erro = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        u = get_licita_user_by_email(email)
        if u and check_password_hash(u['password_hash'], senha):
            session['licita_user_id'] = u['id']
            radar_exec('UPDATE licita_users SET ultimo_acesso=CURRENT_TIMESTAMP WHERE id=?', (u['id'],))
            return redirect('/licita-norte/')
        erro = 'E-mail ou senha incorretos.'
    return render_template_string(_AUTH, modo='entrar', erro=erro, preco=LICITA_PRECO)


@licita_bp.route('/sair')
def rota_sair():
    session.pop('licita_user_id', None)
    return redirect('/licita-norte/entrar')


@licita_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def rota_esqueci():
    msg = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        u = get_licita_user_by_email(email)
        if u:
            token = secrets.token_urlsafe(32)
            radar_exec('UPDATE licita_users SET reset_token=?, reset_expires=? WHERE id=?',
                       (token, (datetime.now()+timedelta(hours=2)).isoformat(), u['id']))
            base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
            link = f"{base}/licita-norte/redefinir-senha?token={token}"
            _enviar_email(email, 'Redefinir senha — Radar Licita Norte',
                          f'<p>Crie uma nova senha (vale 2h):</p><p><a href="{link}">{link}</a></p>')
        msg = 'Se o e-mail existir, enviamos o link de redefinição.'
    return render_template_string(_AUTH, modo='esqueci', msg=msg, erro=None, preco=LICITA_PRECO)


@licita_bp.route('/redefinir-senha', methods=['GET', 'POST'])
def rota_redefinir():
    token = request.values.get('token', '')
    from radar_db import get_radar_db
    db = get_radar_db()
    u = db.execute("SELECT * FROM licita_users WHERE reset_token=? AND reset_token<>''",
                   (token,)).fetchone()
    db.close()
    if not u or (u['reset_expires'] or '') < datetime.now().isoformat():
        return render_template_string(_AUTH, modo='redefinir', erro='Link inválido ou expirado.',
                                      token='', msg=None, preco=LICITA_PRECO)
    erro = None
    if request.method == 'POST':
        nova = request.form.get('senha') or ''
        if len(nova) < 6:
            erro = 'Senha de pelo menos 6 caracteres.'
        else:
            radar_exec("UPDATE licita_users SET password_hash=?, reset_token='', reset_expires='' WHERE id=?",
                       (generate_password_hash(nova), u['id']))
            return render_template_string(_AUTH, modo='entrar', erro=None,
                                          msg='Senha alterada! Faça login.', preco=LICITA_PRECO)
    return render_template_string(_AUTH, modo='redefinir', token=token, erro=erro, msg=None, preco=LICITA_PRECO)


# ── Assinatura (Asaas PIX mensal) — Lote B, espelha o Radar ─────────────────
@licita_bp.route('/assinar', methods=['GET', 'POST'])
@login_required
def rota_assinar():
    u = _user()
    if not u:
        return redirect('/licita-norte/entrar')
    if _is_admin(u) or u.get('plan_active'):
        return redirect('/licita-norte/')
    from app import _asaas_req, _asaas_criar_assinatura_saas, _asaas_get_pix_qr
    erro = None
    if request.method == 'POST':
        cpf = ''.join(c for c in (request.form.get('cpf') or '') if c.isdigit())
        if len(cpf) not in (11, 14):
            erro = 'Informe um CPF ou CNPJ válido.'
        else:
            cid = u.get('asaas_customer_id')
            if not cid:
                busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf}')
                if busca.get('data'):
                    cid = busca['data'][0].get('id')
                if not cid:
                    resp = _asaas_req('POST', '/customers', {
                        'name': u['nome'], 'email': u['email'],
                        'mobilePhone': u.get('telefone') or '', 'cpfCnpj': cpf,
                        'notificationDisabled': True})
                    cid = resp.get('id')
                if cid:
                    radar_exec('UPDATE licita_users SET asaas_customer_id=? WHERE id=?', (cid, u['id']))
            if not cid:
                erro = 'Não consegui criar o cadastro de pagamento. Tente de novo.'
            else:
                sub = _asaas_criar_assinatura_saas(cid, 'licita', 'mensal', float(LICITA_PRECO),
                                                   'Assinatura Radar Licita Norte')
                if not sub.get('id'):
                    erro = 'Erro ao gerar a assinatura. Tente novamente.'
                else:
                    qr = _asaas_get_pix_qr(sub['id'])
                    return render_template_string(_ASSINAR, modo='pix', qr=qr, preco=LICITA_PRECO,
                                                  marca='Radar Licita Norte', base='/licita-norte', erro=None)
    return render_template_string(_ASSINAR, modo='cpf', qr=None, preco=LICITA_PRECO,
                                  marca='Radar Licita Norte', base='/licita-norte', erro=erro)


@licita_bp.route('/assinar-status')
@login_required
def rota_assinar_status():
    u = _user()
    return jsonify({'ativo': bool(u and (_is_admin(u) or u.get('plan_active')))})


LICITA_PACOTES = {
    'p10':  {'creditos': 10,  'valor': 19.0,  'rotulo': '10 análises'},
    'p30':  {'creditos': 30,  'valor': 49.0,  'rotulo': '30 análises'},
    'p100': {'creditos': 100, 'valor': 129.0, 'rotulo': '100 análises'},
}


@licita_bp.route('/comprar', methods=['GET', 'POST'])
@login_required
def rota_comprar():
    u = _user()
    if not u:
        return redirect('/licita-norte/entrar')
    from radar_db import saldo_analises, criar_compra
    from app import _asaas_req
    erro = None
    if request.method == 'POST':
        pack = LICITA_PACOTES.get(request.form.get('pacote'))
        cpf = ''.join(c for c in (request.form.get('cpf') or '') if c.isdigit())
        if not pack or len(cpf) not in (11, 14):
            erro = 'Escolha um pacote e informe um CPF/CNPJ válido.'
        else:
            cid = u.get('asaas_customer_id')
            if not cid:
                busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf}')
                if busca.get('data'):
                    cid = busca['data'][0].get('id')
                if not cid:
                    cid = _asaas_req('POST', '/customers', {
                        'name': u['nome'], 'email': u['email'],
                        'mobilePhone': u.get('telefone') or '', 'cpfCnpj': cpf,
                        'notificationDisabled': True}).get('id')
                if cid:
                    radar_exec('UPDATE licita_users SET asaas_customer_id=? WHERE id=?', (cid, u['id']))
            if not cid:
                erro = 'Não consegui criar o cadastro de pagamento.'
            else:
                compra_id = criar_compra('licita_users', u['id'], pack['creditos'], pack['valor'])
                import datetime as _dt
                pay = _asaas_req('POST', '/payments', {
                    'customer': cid, 'billingType': 'PIX', 'value': pack['valor'],
                    'dueDate': (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d'),
                    'externalReference': f'licitacred_{compra_id}',
                    'description': f"{pack['creditos']} créditos de análise — Radar Licita Norte"})
                qr = _asaas_req('GET', f"/payments/{pay.get('id')}/pixQrCode") if pay.get('id') else {}
                return render_template_string(_COMPRAR, modo='pix', qr=qr, pack=pack, base='/licita-norte',
                                              marca='Radar Licita Norte',
                                              saldo=saldo_analises('licita_users', u['id']), erro=None,
                                              pacotes=LICITA_PACOTES)
    return render_template_string(_COMPRAR, modo='packs', qr=None, pacotes=LICITA_PACOTES,
                                  base='/licita-norte', marca='Radar Licita Norte',
                                  saldo=saldo_analises('licita_users', u['id']), erro=erro, pack=None)


@licita_bp.route('/saldo-status')
@login_required
def rota_saldo_status():
    u = _user()
    from radar_db import saldo_analises
    return jsonify(saldo_analises('licita_users', u['id']) if u else {'gratis': 0, 'creditos': 0})


# ── Painel / detalhe / admin ─────────────────────────────────────────────────
@licita_bp.route('/')
@pago_required
def rota_painel():
    cidade = request.args.get('cidade')
    lst = listar_licita_norte(LICITA_VMIN, LICITA_VMAX,
                              busca=request.args.get('q'),
                              so_noticia=request.args.get('noticia') == '1',
                              cidade=cidade,
                              ordem=request.args.get('ordem', 'prazo'), limite=400)
    return render_template_string(_PAINEL, lst=lst, st=stats_licita_norte(),
                                  cidades=CIDADES_TXT, cidades_lista=contagem_cidades_norte(),
                                  cidade_sel=cidade or '', vmin=LICITA_VMIN, vmax=LICITA_VMAX)


@licita_bp.route('/l/<path:pncp_id>')
@pago_required
def rota_detalhe(pncp_id):
    l = obter_licitacao(pncp_id)
    if not l:
        return 'Licitação não encontrada', 404
    analise = None
    if l.get('analise_json'):
        try: analise = _json.loads(l['analise_json'])
        except Exception: pass
    u = _user()
    from radar_db import saldo_analises
    saldo = None if (u and _is_admin(u)) else (saldo_analises('licita_users', u['id']) if u else None)
    return render_template_string(_DETALHE, l=l, analise=analise, saldo=saldo)


@licita_bp.route('/l/<path:pncp_id>/analisar', methods=['POST'])
@pago_required
def rota_analisar(pncp_id):
    l = obter_licitacao(pncp_id)
    if not l:
        return 'Licitação não encontrada', 404
    u = _user()
    admin = _is_admin(u)
    from radar_db import saldo_analises, consumir_analise
    if not admin:
        s = saldo_analises('licita_users', u['id'])
        if s['gratis'] <= 0 and s['creditos'] <= 0:
            return redirect('/licita-norte/comprar')
    texto = ''
    f = request.files.get('edital')
    if f and f.filename:
        try: texto = _texto_de_pdf(f.read())
        except Exception: pass
    try:
        analise, engine = analisar_edital(l, texto, area=(u or {}).get('area', ''))
        salvar_analise(pncp_id, analise, engine)
        if not admin:
            consumir_analise('licita_users', u['id'])
    except Exception as e:
        log.error(f'[LICITA] análise falhou: {e}', exc_info=True)
        return render_template_string(_DETALHE, l=l, analise=None, erro=str(e))
    return redirect(f'/licita-norte/l/{pncp_id}')


# ── Coleta regional (garante cobertura de SC) — background ───────────────────
_bg = {'rodando': False, 'msg': 'ainda não rodou'}


def _coleta_sc():
    try:
        r = coletar(uf='SC')
        try: coletar_contratos(uf='SC')
        except Exception: pass
        _bg['msg'] = f"SC: +{r['novos']} novos, {r['atualizados']} atual."
        log.info(f'[LICITA] coleta SC ok: {_bg["msg"]}')
    except Exception as e:
        _bg['msg'] = f'erro: {e}'; log.error(f'[LICITA] coleta SC erro: {e}')
    finally:
        _bg['rodando'] = False


# ── Auto-coleta SC (Licita Norte se atualiza sozinho) ───────────────────────
_SC_INICIADO = False


def iniciar_coletor_sc(intervalo_horas=None, delay_inicial=180):
    """Thread daemon que coleta SC periodicamente. Independente do Radar nacional.
    Desliga via env LICITA_AUTO_COLETA=0."""
    global _SC_INICIADO
    if _SC_INICIADO:
        return
    if os.environ.get('LICITA_AUTO_COLETA', '1') == '0':
        log.info('[LICITA] auto-coleta SC desativada (LICITA_AUTO_COLETA=0)')
        return
    _SC_INICIADO = True
    horas = intervalo_horas or int(os.environ.get('LICITA_COLETA_HORAS', '8'))
    import time as _t, threading as _th, random as _r

    def _loop():
        _t.sleep(delay_inicial + _r.randint(0, 120))   # jitter (vários workers)
        while True:
            if not _bg['rodando']:
                _bg['rodando'] = True
                _coleta_sc()
            _t.sleep(horas * 3600)

    _th.Thread(target=_loop, daemon=True, name='licita-sc-auto').start()
    log.info(f'[LICITA] auto-coleta SC iniciada (a cada {horas}h)')


@licita_bp.route('/coletar', methods=['GET', 'POST'])
@admin_required
def rota_coletar():
    if not _bg['rodando']:
        _bg['rodando'] = True
        threading.Thread(target=_coleta_sc, daemon=True, name='licita-coleta').start()
        return jsonify({'ok': True, 'status': '🚀 Coleta de SC iniciada — atualize em 1-2 min.'})
    return jsonify({'ok': True, 'status': '⏳ Já em andamento.', 'ultima': _bg['msg']})


@licita_bp.route('/admin')
@admin_required
def rota_admin():
    return render_template_string(_ADMIN, st=stats_licita_norte(), users=listar_licita_users())


@licita_bp.route('/admin/ativar/<int:uid>')
@admin_required
def rota_ativar(uid):
    radar_exec('UPDATE licita_users SET plan_active=1 WHERE id=?', (uid,))
    return redirect('/licita-norte/admin')


@licita_bp.route('/admin/desativar/<int:uid>')
@admin_required
def rota_desativar(uid):
    radar_exec('UPDATE licita_users SET plan_active=0 WHERE id=? AND is_admin=0', (uid,))
    return redirect('/licita-norte/admin')


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES (inline, marca "Radar Licita Norte" — verde/local)
# ══════════════════════════════════════════════════════════════════════════════
_BASE_CSS = '''
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#0a1410;color:#e7f5ec;margin:0}
 a{color:#7ce0a0}
 .pill{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;white-space:nowrap}
 .ouro{background:#2b2300;color:#ffd95e}.boa{background:#06291a;color:#5ee0a0}
 .dificil{background:#2a1a06;color:#ffb267}.nao{background:#2a0a0a;color:#ff8a8a}.indef{background:#16261d;color:#9fd0b4}
 .news{background:#10233f;color:#7cc0ff;font-weight:800}'''

_AUTH = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>📡 Radar Licita Norte — Acesso</title>
<link rel="icon" type="image/png" href="/static/img/licita/logo.webp">
<style>''' + _BASE_CSS + '''
 body{display:flex;min-height:100vh;align-items:center;justify-content:center;padding:20px}
 .card{background:#0e1c16;border:1px solid #21402f;border-radius:16px;padding:32px;max-width:380px;width:100%}
 h1{font-size:21px;margin:0 0 4px}.sub{color:#8ac0a0;font-size:13px;margin-bottom:20px}
 label{display:block;font-size:13px;color:#8ac0a0;margin:12px 0 4px}
 input{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:8px;border:1px solid #2a4d3a;background:#0a1410;color:#e7f5ec;font-size:15px}
 button{width:100%;margin-top:18px;padding:12px;border:none;border-radius:8px;background:#16a34a;color:#fff;font-size:15px;font-weight:700;cursor:pointer}
 .erro{background:#2a0a0a;color:#ff8a8a;padding:10px;border-radius:8px;font-size:13px;margin-bottom:8px}
 .ok{background:#06291a;color:#5ee0a0;padding:10px;border-radius:8px;font-size:13px;margin-bottom:8px}
 .links{margin-top:16px;font-size:13px;text-align:center}
</style></head><body>
<div class="card">
 <h1>📡 Radar Licita Norte</h1>
 <div class="sub">Licitações fáceis do Norte de SC, na tua mão.</div>
 {% if erro %}<div class="erro">⚠️ {{ erro }}</div>{% endif %}
 {% if msg %}<div class="ok">✅ {{ msg }}</div>{% endif %}
 {% if modo == 'cadastrar' %}
 <form method="post" action="/licita-norte/cadastrar">
  <label>Nome</label><input name="nome" required>
  <label>E-mail</label><input type="email" name="email" required>
  <label>Telefone (opcional)</label><input name="telefone">
  <label>Sua área / o que você faz</label><input name="area" placeholder="ex: buffet, construção, uniformes, TI, notícias...">
  <label>Senha (mín. 6)</label><input type="password" name="senha" required>
  <button>Criar conta</button>
 </form>
 <div class="links" style="color:#8ac0a0">Serviço por assinatura (R$ {{ preco }}/mês) — ative após criar.</div>
 <div class="links">Já tem conta? <a href="/licita-norte/entrar">Entrar</a></div>
 {% elif modo == 'esqueci' %}
 <form method="post" action="/licita-norte/esqueci-senha">
  <label>Seu e-mail</label><input type="email" name="email" required><button>Enviar link</button></form>
 <div class="links"><a href="/licita-norte/entrar">← voltar</a></div>
 {% elif modo == 'redefinir' %}
 <form method="post" action="/licita-norte/redefinir-senha">
  <input type="hidden" name="token" value="{{ token }}">
  <label>Nova senha (mín. 6)</label><input type="password" name="senha" required><button>Salvar</button></form>
 {% else %}
 <form method="post" action="/licita-norte/entrar">
  <label>E-mail</label><input type="email" name="email" required>
  <label>Senha</label><input type="password" name="senha" required><button>Entrar</button></form>
 <div class="links" style="margin:14px 0 6px;color:#8ac0a0">— ainda não tem conta? —</div>
 <a href="/licita-norte/cadastrar"><button type="button" style="background:#152b20;border:1px solid #2a4d3a;color:#7ce0a0">✨ Criar conta</button></a>
 <div class="links"><a href="/licita-norte/esqueci-senha">Esqueci a senha</a></div>
 {% endif %}
</div></body></html>'''

_AGUARDANDO = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Assine — Radar Licita Norte</title>
<style>''' + _BASE_CSS + '''
 body{display:flex;min-height:100vh;align-items:center;justify-content:center;padding:20px}
 .card{background:#0e1c16;border:1px solid #21402f;border-radius:16px;padding:34px;max-width:440px;text-align:center}
 .preco{font-size:40px;font-weight:800;color:#5ee0a0;margin:16px 0 2px}.preco span{font-size:16px;color:#8ac0a0;font-weight:500}
 ul{text-align:left;color:#cfeeda;font-size:14px;line-height:1.9;max-width:330px;margin:16px auto}
 .btn{display:block;background:#25D366;color:#06210f;font-weight:800;text-decoration:none;padding:14px;border-radius:10px;margin-top:16px}
 a.sair{color:#8ac0a0;font-size:13px;display:inline-block;margin-top:14px}
</style></head><body><div class="card">
 <h1>📡 Radar Licita Norte</h1>
 <p>Olá, {{ nome }}! Conta criada. ✅ É um serviço <b>por assinatura</b>.</p>
 <div class="preco">R$ {{ preco }}<span>/mês</span></div>
 <ul><li>✅ Licitações fáceis das 6 cidades do Norte de SC</li>
 <li>🗞️ Selo especial pras de notícia/comunicação</li>
 <li>🤖 IA que lê o edital e diz se vale a pena</li>
 <li>💰 Faixa R$1k–200k (o que dá pra ganhar)</li></ul>
 <a class="btn" href="/licita-norte/assinar" style="background:#16a34a">💠 Assinar com PIX</a>
 {% if whatsapp %}<a class="btn" href="https://wa.me/{{ whatsapp }}?text=Quero%20assinar%20o%20Radar%20Licita%20Norte" target="_blank" style="background:#152b20;color:#cfeeda">💬 Falar no WhatsApp</a>{% endif %}
 <a class="sair" href="/licita-norte/sair">sair</a>
</div></body></html>'''

_PAINEL = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>📡 Radar Licita Norte</title>
<link rel="icon" type="image/png" href="/static/img/licita/logo.webp">
<style>''' + _BASE_CSS + '''
 header{padding:18px 22px;background:#0e1c16;border-bottom:1px solid #21402f;position:sticky;top:0}
 h1{margin:0;font-size:20px}.sub{color:#8ac0a0;font-size:13px;margin-top:4px}
 .stats{display:flex;gap:14px;flex-wrap:wrap;margin-top:12px}
 .card{background:#0e1c16;border:1px solid #21402f;border-radius:10px;padding:10px 14px;min-width:110px}
 .card b{font-size:22px;display:block}.card span{color:#8ac0a0;font-size:12px}
 .filtros{padding:12px 22px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
 .filtros a{color:#cfeeda;text-decoration:none;background:#152b20;border:1px solid #2a4d3a;padding:6px 12px;border-radius:20px;font-size:13px}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #16261d;vertical-align:top}
 th{color:#8ac0a0;position:sticky;top:0;background:#0a1410}tr:hover{background:#0e1c16}
 .obj{max-width:520px}.obj a{color:#e7f5ec;text-decoration:none}.muni{color:#8ac0a0}
 .empty{padding:60px;text-align:center;color:#8ac0a0}
</style></head><body>
<header>
 <h1><img src="/static/img/licita/logo.webp" alt="" style="height:30px;width:30px;border-radius:7px;vertical-align:-6px;margin-right:8px" onerror="this.outerHTML='📡 '">Radar Licita Norte</h1>
 <div class="sub">{{ cidades }} — R$ {{ '{:,.0f}'.format(vmin).replace(',','.') }} a {{ '{:,.0f}'.format(vmax).replace(',','.') }}, todas as categorias.</div>
 <div class="stats">
  <div class="card"><b>{{ st.total }}</b><span>oportunidades</span></div>
  <div class="card"><b>{{ st.noticia }}</b><span>🗞️ notícia/comunicação</span></div>
  <div class="card"><b>{{ st.cidades }}</b><span>cidades ativas</span></div>
 </div>
</header>
<div class="filtros">
 <a href="/licita-norte/">Todas</a>
 <a href="/licita-norte/?noticia=1" style="background:#10233f;border-color:#2a3c63;color:#7cc0ff">🗞️ Só notícia</a>
 <a href="/licita-norte/?ordem=valor">Por valor</a>
 <a href="/licita-norte/?ordem=prazo">⏰ Por prazo</a>
 {% if lic_is_admin %}<a href="/licita-norte/coletar" style="background:#13351f;border-color:#2f5e44;color:#8ff0b8">▶ Coletar SC</a>
 <a href="/licita-norte/admin" style="background:#231a3a;border-color:#463a6e;color:#c7b3ff">🛠️ Admin</a>{% endif %}
 <span style="margin-left:auto;color:#8ac0a0;font-size:13px">👤 {{ lic_nome }} · <a href="/licita-norte/sair" style="color:#8ac0a0">sair</a></span>
</div>
<div class="filtros" style="padding-top:0;padding-bottom:14px;border-bottom:1px solid #16261d">
 <span style="color:#8ac0a0;font-size:12px;align-self:center">🏙️ Cidade:</span>
 <a href="/licita-norte/"{% if not cidade_sel %} style="background:#16a34a;color:#fff;border-color:#16a34a"{% endif %}>Todas</a>
 {% for cid, n in cidades_lista %}
 <a href="/licita-norte/?cidade={{ cid }}"{% if cidade_sel|lower == cid|lower %} style="background:#16a34a;color:#fff;border-color:#16a34a"{% endif %}>{{ cid }} <b>{{ n }}</b></a>
 {% endfor %}
</div>
{% if lst %}
<table><tr><th>Porte</th><th class="obj">Objeto</th><th>Valor</th><th>Cidade / Órgão</th><th>Prazo</th></tr>
{% for l in lst %}<tr>
 <td>{% if l.eh_noticia %}<span class="pill news">🗞️ NOTÍCIA</span><br>{% endif %}<span class="pill {{ l.zona_valor }}">{{ l.zona_valor }}</span></td>
 <td class="obj"><a href="/licita-norte/l/{{ l.pncp_id }}">{{ l.objeto[:150] }}</a></td>
 <td>{% if l.valor %}R$ {{ '{:,.0f}'.format(l.valor).replace(',','.') }}{% else %}—{% endif %}</td>
 <td><a href="/licita-norte/?cidade={{ l.municipio }}" style="color:#e7f5ec;font-weight:700;text-decoration:none">{{ l.municipio or '' }}</a><div class="muni" style="font-size:11px">{{ (l.orgao or '')[:46] }}</div></td>
 <td>{{ (l.data_encerramento or '')[:10] }}</td>
</tr>{% endfor %}</table>
{% else %}
<div class="empty">
 <img src="/static/img/licita/vazio.webp" alt="" style="max-width:240px;width:60%;opacity:.9;margin-bottom:14px" onerror="this.style.display='none'"><br>
 Nenhuma oportunidade na região ainda.<br><br>
{% if lic_is_admin %}Clique em <b>▶ Coletar SC</b> pra puxar do PNCP.{% else %}Volte em breve — o radar atualiza sozinho.{% endif %}</div>
{% endif %}
</body></html>'''

_DETALHE = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Licitação — Radar Licita Norte</title>
<style>''' + _BASE_CSS + '''
 body{padding:22px;max-width:860px}
 .box{background:#0e1c16;border:1px solid #21402f;border-radius:10px;padding:12px 16px;display:inline-block;min-width:150px;margin:4px}
 .box span{color:#8ac0a0;font-size:12px;display:block}.box b{font-size:17px}
 .btn{display:inline-block;background:#13351f;border:1px solid #2f5e44;color:#8ff0b8;padding:10px 16px;border-radius:8px;text-decoration:none;margin-top:8px}
 h1{font-size:19px;line-height:1.4}
</style></head><body>
<a href="/licita-norte/" style="color:#8ac0a0;font-size:14px">← voltar</a>
<h1>{% if l.eh_noticia %}🗞️ {% endif %}{{ l.objeto }}</h1>
<div>
 <div class="box"><span>Porte</span><b><span class="pill {{ l.zona_valor }}">{{ l.zona_valor }}</span></b></div>
 <div class="box"><span>Valor</span><b>{% if l.valor %}R$ {{ '{:,.2f}'.format(l.valor).replace(',','#').replace('.',',').replace('#','.') }}{% else %}—{% endif %}</b></div>
 <div class="box"><span>Cidade</span><b style="font-size:14px">{{ l.uf }} {{ l.municipio }}</b></div>
 <div class="box"><span>Modalidade</span><b style="font-size:14px">{{ l.modalidade or '—' }}</b></div>
 <div class="box"><span>Encerramento</span><b style="font-size:14px">{{ (l.data_encerramento or '—')[:16] }}</b></div>
</div>
<p style="color:#8ac0a0;font-size:13px">{{ l.orgao or '' }} · PNCP: {{ l.pncp_id }}</p>
{% if l.link %}<a class="btn" href="{{ l.link }}" target="_blank" rel="noopener">Abrir edital no sistema de origem →</a>{% endif %}
<hr style="border:none;border-top:1px solid #21402f;margin:24px 0">
{% if erro %}<p style="color:#ff8a8a">⚠️ {{ erro }}</p>{% endif %}
{% if analise %}
{% set cor = {'sim':'#5ee0a0','talvez':'#ffd95e','nao':'#ff8a8a'}.get(analise.viavel,'#9fd0b4') %}
<h2 style="font-size:17px">🤖 Análise da IA</h2>
<p style="font-size:18px;font-weight:800;color:{{ cor }}">{{ {'sim':'✅ VIÁVEL','talvez':'🟡 TALVEZ','nao':'❌ NÃO VALE'}.get(analise.viavel, analise.viavel) }}</p>
<p><b>{{ analise.veredito }}</b></p><p style="color:#cfeeda">{{ analise.resumo }}</p>
<div><div class="box"><span>Atestado?</span><b style="font-size:15px">{{ analise.exige_atestado }}</b></div>
 <div class="box"><span>Garantia?</span><b style="font-size:15px">{{ analise.exige_garantia }}</b></div>
 <div class="box"><span>Dificuldade</span><b style="font-size:15px">{{ analise.dificuldade }}</b></div></div>
{% if analise.riscos %}<p>⚠️ <b>Riscos:</b></p><ul>{% for r in analise.riscos %}<li>{{ r }}</li>{% endfor %}</ul>{% endif %}
{% if analise.plano %}<p>🗺️ <b>Plano:</b></p><ol>{% for p in analise.plano %}<li>{{ p }}</li>{% endfor %}</ol>{% endif %}
<form method="post" action="/licita-norte/l/{{ l.pncp_id }}/analisar" enctype="multipart/form-data" style="margin-top:10px">
 <input type="file" name="edital" accept="application/pdf" style="color:#8ac0a0"><button class="btn" type="submit">🔄 Reanalisar (com PDF)</button></form>
{% else %}
<h2 style="font-size:17px">🤖 Análise da IA</h2>
<p style="color:#8ac0a0">Deixe a IA ler e dizer <b>se vale a pena</b>, atestado, prazo e plano. Anexe o PDF (completo) ou rode só com os metadados.</p>
{% if saldo %}<p style="color:#5ee0a0;font-size:13px">💠 {{ saldo.gratis }} análises grátis este mês{% if saldo.creditos %} + {{ saldo.creditos }} créditos{% endif %}.{% if saldo.gratis <= 0 and saldo.creditos <= 0 %} Acabou — <a href="/licita-norte/comprar" style="color:#ffd95e">comprar créditos →</a>{% endif %}</p>{% endif %}
<form method="post" action="/licita-norte/l/{{ l.pncp_id }}/analisar" enctype="multipart/form-data">
 <input type="file" name="edital" accept="application/pdf" style="color:#8ac0a0"><button class="btn" type="submit">🤖 Analisar este edital</button></form>
{% endif %}
</body></html>'''

_ADMIN = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>🛠️ Admin — Radar Licita Norte</title>
<style>''' + _BASE_CSS + '''
 body{padding:22px}h1{font-size:20px}h2{font-size:16px;color:#8ac0a0;margin-top:24px}
 .stats{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
 .card{background:#0e1c16;border:1px solid #21402f;border-radius:10px;padding:12px 16px;min-width:120px}
 .card b{font-size:22px;display:block}.card span{color:#8ac0a0;font-size:12px}
 .btn{background:#13351f;border:1px solid #2f5e44;color:#8ff0b8;padding:8px 14px;border-radius:8px;text-decoration:none;font-size:13px}
 table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
 th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #16261d}th{color:#8ac0a0}
</style></head><body>
<h1>🛠️ Admin — Radar Licita Norte</h1>
<a href="/licita-norte/" style="color:#8ac0a0;font-size:14px">← painel</a> · <a href="/licita-norte/sair" style="color:#8ac0a0;font-size:14px">sair</a>
<h2>📊 Cobertura</h2>
<div class="stats">
 <div class="card"><b>{{ st.total }}</b><span>oportunidades</span></div>
 <div class="card"><b>{{ st.noticia }}</b><span>🗞️ notícia</span></div>
 <div class="card"><b>{{ st.cidades }}</b><span>cidades</span></div>
</div>
<a class="btn" href="/licita-norte/coletar">▶ Coletar SC agora</a>
<h2>👥 Usuários ({{ users|length }})</h2>
<table><tr><th>#</th><th>Nome</th><th>E-mail</th><th>Status</th><th>Cadastro</th><th>Ação</th></tr>
{% for u in users %}<tr>
 <td>{{ u.id }}</td><td>{{ u.nome }}{% if u.is_admin %} <span style="color:#ffd95e">★admin</span>{% endif %}</td>
 <td>{{ u.email }}</td>
 <td>{% if u.plan_active %}<span style="color:#5ee0a0">✅ ativo</span>{% else %}<span style="color:#ffd95e">⏳ aguardando</span>{% endif %}</td>
 <td>{{ (u.created_at or '')[:10] }}</td>
 <td>{% if u.is_admin %}—{% elif u.plan_active %}<a href="/licita-norte/admin/desativar/{{ u.id }}" style="color:#ff8a8a">desativar</a>{% else %}<a href="/licita-norte/admin/ativar/{{ u.id }}" style="color:#5ee0a0">✓ ativar (pago)</a>{% endif %}</td>
</tr>{% endfor %}</table>
</body></html>'''
