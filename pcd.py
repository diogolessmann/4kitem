"""
pcd.py — Blueprint PCD Fácil (módulo do 4kitem)
Guia, passo a passo, a isenção de impostos para PCD (carro 0km / IPVA).
A IA confere os documentos (OCR) e o caso avança por fases. Créditos pré-pagos.
⚠️ Organiza e orienta o processo — quem defere é o órgão (Receita/SEFAZ/Detran).
"""
import os
import io
import time
import base64
import logging
import secrets
import threading
import requests as _requests
from datetime import datetime, timedelta
from functools import wraps
from flask import (Blueprint, render_template, redirect, request,
                   session, jsonify, flash, url_for)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pcd_db import (get_pcd_db, init_pcd_db,
                    get_creditos, add_creditos, debita_creditos)

log = logging.getLogger('pcd')

pcd_bp = Blueprint('pcd', __name__, url_prefix='/pcd')

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', '').strip().lower()
_UPLOAD_DIR = os.path.join(os.environ.get('DATA_DIR', os.path.dirname(__file__)), 'pcd_uploads')

# ── Créditos (modelo HÍBRIDO) ────────────────────────────────────────────────
CUSTO_ABRIR_CASO = 40
CUSTO_DOC_EXTRA  = 5
FREE_CREDITS     = 0

# ── Pacotes de crédito (premium; mercado cobra R$300-900 de honorário) ───────
PACOTES = {
    'avulso':       {'creditos': 50,  'preco': 49.0,  'rotulo': '1 caso'},
    'profissional': {'creditos': 150, 'preco': 129.0, 'rotulo': '~3 casos'},
    'escritorio':   {'creditos': 500, 'preco': 399.0, 'rotulo': '~12 casos'},
}

PERFIS = {
    'cliente':  'Sou PCD / familiar (quero minha isenção)',
    'operador': 'Despachante / escritório / concessionária (atendo vários clientes)',
}

DEFICIENCIAS = {
    'fisica': 'Física', 'visual': 'Visual', 'mental': 'Mental severa/profunda',
    'down': 'Síndrome de Down', 'tea': 'Autismo (TEA)',
}

# ── Tetos por UF (convênios mudam por decreto → ajustar SÓ aqui) ──────────────
TETOS_UF = {'SC': {'ipi': 200000, 'ipva': 200000, 'icms_total': 70000, 'icms_parcial': 120000}}
UF_PADRAO = 'SC'


def avaliar_icms(uf, valor_carro):
    t = TETOS_UF.get(uf, TETOS_UF[UF_PADRAO])
    if valor_carro <= t['icms_total']:
        return {'nivel': 'total',
                'mensagem': f"Carro de até R$ {t['icms_total']:,.0f}: ICMS 100% isento. ✅".replace(',', '.')}
    if valor_carro <= t['icms_parcial']:
        return {'nivel': 'parcial',
                'mensagem': (f"Entre R$ {t['icms_total']:,.0f} e R$ {t['icms_parcial']:,.0f}: isenção PARCIAL "
                             f"— você economiza o ICMS sobre R$ {t['icms_total']:,.0f} e paga só sobre o excedente. ⚠️").replace(',', '.')}
    return {'nivel': 'nenhum',
            'mensagem': (f"Acima de R$ {t['icms_parcial']:,.0f}: sem isenção de ICMS "
                         f"(mas IPI e IPVA valem até R$ {t['ipi']:,.0f}). ❌").replace(',', '.')}


# ── O ROTEIRO das trilhas (a alma) ───────────────────────────────────────────
TRILHAS = {
    'A': {
        'nome': 'Carro 0km com isenção', 'emoji': '📗',
        'resumo': 'Comprar veículo NOVO com isenção de IPI, IOF (Receita) e ICMS, IPVA (SEFAZ-SC).',
        'fases': [
            {'n': 1, 'titulo': 'Quem é você', 'objetivo': 'Confirmar o direito e enquadrar o caso.',
             'docs': [
                 {'id': 'rg_cpf', 'label': 'RG e CPF do beneficiário', 'ajuda': 'Foto legível dos dois.'},
                 {'id': 'residencia', 'label': 'Comprovante de residência (SC)', 'ajuda': 'Conta recente no nome do beneficiário.'},
                 {'id': 'laudo', 'label': 'Laudo médico com CID', 'ajuda': 'Emitido pelo SUS ou perito do Detran. Para IPI, preenchido no próprio SISEN.'},
                 {'id': 'cnh', 'label': 'CNH do condutor PCD', 'cond': 'condutor', 'ajuda': 'Com observação de adaptação, registrada no Detran-SC.'},
                 {'id': 'condutores', 'label': 'Dados dos condutores autorizados (até 2)', 'cond': 'nao_condutor', 'ajuda': 'Não-condutor indica até 2 condutores (CNH no Detran-SC + residência).'},
             ]},
            {'n': 2, 'titulo': 'Os pedidos de isenção', 'objetivo': 'Dar entrada nos 3 pedidos e escolher o carro no teto certo.',
             'docs': [
                 {'id': 'ipi_sisen', 'label': 'Pedido de IPI/IOF (SISEN — Receita Federal)', 'ajuda': 'Online no SISEN. Carro até R$200 mil, motor até 2.0, 4 portas, combustível renovável/flex/híbrido/elétrico. 1 carro a cada 3 anos.'},
                 {'id': 'icms_ttd', 'label': 'Pedido de ICMS (TTD — SEFAZ-SC)', 'ajuda': 'Online no TTD. ⚠️ HOMOLOGAR ANTES de comprar. Até R$70 mil = total; R$70-120 mil = parcial.'},
                 {'id': 'escolha_carro', 'label': 'Veículo escolhido (proposta da concessionária)', 'ajuda': 'Confira o valor x teto pra saber sua economia real de ICMS.'},
             ]},
            {'n': 3, 'titulo': 'Compra e protocolo', 'objetivo': 'Comprar com isenção, emplacar e encaminhar o IPVA.',
             'docs': [
                 {'id': 'nota_fiscal', 'label': 'Nota fiscal do veículo (com isenção)', 'ajuda': 'Emitida pela concessionária já com as isenções aprovadas.'},
                 {'id': 'ipva_ttd', 'label': 'Pedido de IPVA (TTD código 596 — SEFAZ-SC)', 'ajuda': 'Após registrar o carro no seu nome no Detran-SC. Pague a taxa (DARE).'},
             ]},
        ],
    },
    'B': {
        'nome': 'Isenção de IPVA (carro que já tenho)', 'emoji': '🚙',
        'resumo': 'Conseguir a isenção do IPVA do veículo já no seu nome (SEFAZ-SC, TTD 596).',
        'fases': [
            {'n': 1, 'titulo': 'Você e o veículo', 'objetivo': 'Reunir seus dados, o laudo e o documento do carro.',
             'docs': [
                 {'id': 'rg_cpf', 'label': 'RG e CPF do beneficiário', 'ajuda': 'Foto legível dos dois.'},
                 {'id': 'residencia', 'label': 'Comprovante de residência (SC)', 'ajuda': 'Conta recente no nome do beneficiário.'},
                 {'id': 'laudo', 'label': 'Laudo de avaliação (modelo SEF-SC) + declaração SUS', 'ajuda': 'No modelo da SEF-SC, com declaração de que o emitente integra o SUS.'},
                 {'id': 'doc_veiculo', 'label': 'Documento do veículo (CRLV)', 'ajuda': 'Carro no nome do beneficiário, registrado no Detran-SC, até R$200 mil, sem débitos com a Fazenda.'},
                 {'id': 'cnh', 'label': 'CNH do beneficiário', 'cond': 'condutor', 'ajuda': 'Registrada no Detran-SC.'},
                 {'id': 'condutores', 'label': 'Condutores autorizados (até 2)', 'cond': 'nao_condutor', 'ajuda': 'Se não dirige: declaração de destinação + CNH dos condutores.'},
             ]},
            {'n': 2, 'titulo': 'Pedido no TTD', 'objetivo': 'Protocolar a isenção de IPVA na SEFAZ-SC.',
             'docs': [
                 {'id': 'requerimento_ttd', 'label': 'Requerimento TTD (Benefícios IPVA — código 596)', 'ajuda': 'No TTD: identificação → benefício IPVA → código 596 → escolher o veículo → anexar tudo.'},
                 {'id': 'dare', 'label': 'Comprovante da taxa (DARE)', 'ajuda': 'Emita o DARE da Taxa de Serviços Gerais e pague.'},
             ]},
            {'n': 3, 'titulo': 'Acompanhamento', 'objetivo': 'Acompanhar o deferimento e guardar o dossiê.',
             'docs': [
                 {'id': 'protocolo', 'label': 'Protocolo/comprovante do pedido', 'ajuda': 'Guarde o número. Execução pela SEFAZ em até 90 dias.'},
             ]},
        ],
    },
}


def docs_da_fase(trilha, fase_n, condutor):
    fase = TRILHAS[trilha]['fases'][fase_n - 1]
    saida = []
    for d in fase['docs']:
        cond = d.get('cond')
        if cond == 'condutor' and condutor != 'sim':
            continue
        if cond == 'nao_condutor' and condutor != 'nao':
            continue
        saida.append(d)
    return saida


def total_fases(trilha):
    return len(TRILHAS[trilha]['fases'])


# ── Modelos oficiais SEFAZ-SC (Portaria SEF 362/2019) p/ baixar dentro do app ──
SEF_PAGINA = ('https://www.sef.sc.gov.br/servicos/'
              'solicitar-isencao-ou-imunidade-de-ipva-pessoa-com-deficiencia-ou-autista')
_SEF_DL = 'https://www.sef.sc.gov.br/api/download?id={id}&nomeArquivo={nome}'
# laudo conforme o tipo de deficiência do caso
MODELOS_LAUDO = {
    'fisica': ('Laudo de Deficiência Física', _SEF_DL.format(id=1133, nome='02._Laudo_Deficiencia_Fisica.pdf')),
    'mental': ('Laudo de Deficiência Mental', _SEF_DL.format(id=1134, nome='01._Laudo_Deficiencia_Mental.docx')),
    'down':   ('Laudo de Deficiência Mental', _SEF_DL.format(id=1134, nome='01._Laudo_Deficiencia_Mental.docx')),
    'tea':    ('Laudo de Autismo',            _SEF_DL.format(id=1132, nome='03._Laudo_Autismo.docx')),
}
MODELO_SUS = ('Declaração Integrante do SUS', _SEF_DL.format(id=1135, nome='Declaracao_Integrante_SUS.docx'))
MODELO_DESTINACAO = ('Declaração de Destinação do Veículo',
                     _SEF_DL.format(id=1131, nome='Declaracao_de_Destinacao_do_Veiculo_ao_Uso_do_Portador_de_Deficiencia_ou_Autista.doc'))


def modelos_do_doc(doc_id, caso):
    """Modelos oficiais (SEFAZ-SC) pra baixar conforme o documento e a deficiência.
    Por ora aplicados à Trilha B (IPVA — SEF-SC)."""
    if caso['trilha'] != 'B':
        return []
    if doc_id == 'laudo':
        out = []
        m = MODELOS_LAUDO.get(caso['tipo_deficiencia'])
        if not m:  # tipo sem modelo específico (ex: visual) → manda pra página oficial
            m = ('Laudo de avaliação (ver modelos)', SEF_PAGINA)
        out.append(m)
        out.append(MODELO_SUS)
        return out
    if doc_id == 'condutores':
        return [MODELO_DESTINACAO]
    return []


# ── IA: Gemini (reaproveita a chave do 4kitem) ───────────────────────────────
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


def _ia_ativa():
    return bool(GEMINI_KEY)


def _gemini_call(system, contents, json_mode=False, max_tokens=1024, temperature=0.2):
    body = {'contents': contents,
            'generationConfig': {'temperature': temperature, 'maxOutputTokens': max_tokens}}
    if system:
        body['systemInstruction'] = {'parts': [{'text': system}]}
    if json_mode:
        body['generationConfig']['responseMimeType'] = 'application/json'
    r = _requests.post(_GEMINI_URL.format(model=GEMINI_MODEL),
                       params={'key': GEMINI_KEY}, json=body, timeout=90)
    r.raise_for_status()
    data = r.json()
    txt = data['candidates'][0]['content']['parts'][0]['text'].strip()
    um  = data.get('usageMetadata', {}) or {}
    return txt, int(um.get('promptTokenCount', 0)), int(um.get('candidatesTokenCount', 0))


SYSTEM_DOC = (
    "Você é o assistente do PCD Fácil, que AJUDA a organizar o processo de isenção de "
    "impostos para Pessoa com Deficiência. Olhe o documento anexado e confira se serve para a "
    "etapa, em linguagem simples e acolhedora (usuário leigo, no celular). NÃO valide "
    "autenticidade nem detecte fraude — isso é com o órgão. Confira: é o tipo certo de documento? "
    "Está legível (sem corte/borrão/dedo na frente)? Os dados essenciais aparecem (nome, datas, "
    "CID no laudo, placa/chassi quando do veículo)? Está vencido? Seja direto e gentil.\n\n"
    'Responda APENAS em JSON: {"status":"ok|atencao|falta","mensagem":"1 a 2 frases simples"}. '
    "status: 'ok' = serve e legível; 'atencao' = serve mas revise algo; 'falta' = não serve/ilegível."
)


def _analisar_doc(label, ajuda, contexto, file_bytes, mime):
    """Confere um documento via Gemini. Retorna {status, mensagem, tin, tout} ou {erro}."""
    import json as _json, re as _re
    if not _ia_ativa():
        return {'erro': 'IA não configurada.'}
    b64 = base64.b64encode(file_bytes).decode('ascii')
    prompt = (f"=== ETAPA ===\n{contexto}\nDocumento esperado: {label}.\nO que se espera: {ajuda}.")
    contents = [{'role': 'user', 'parts': [
        {'inlineData': {'mimeType': mime or 'image/jpeg', 'data': b64}},
        {'text': prompt}]}]
    # max_tokens folgado: o gemini-2.5 "pensa" antes de responder e pode truncar o JSON
    try:
        txt, tin, tout = _gemini_call(SYSTEM_DOC, contents, json_mode=True, max_tokens=2048)
    except Exception as e:
        return {'erro': f'Falha ao consultar a IA: {e}'}
    # parsing à prova de falha (tira cercas markdown, extrai o objeto, e degrada com elegância)
    raw = (txt or '').strip()
    if raw.startswith('```'):
        raw = _re.sub(r'^```[a-zA-Z]*\n?', '', raw).rstrip('`').strip()
    data = None
    try:
        data = _json.loads(raw)
    except Exception:
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if m:
            try:
                data = _json.loads(m.group(0))
            except Exception:
                data = None
    if not isinstance(data, dict):
        return {'status': 'atencao',
                'mensagem': 'Documento recebido. Não consegui analisar automaticamente agora — confira manualmente.',
                'tin': tin, 'tout': tout}
    status = data.get('status', 'atencao')
    if status not in ('ok', 'atencao', 'falta'):
        status = 'atencao'
    return {'status': status, 'mensagem': data.get('mensagem') or 'Documento recebido.',
            'tin': tin, 'tout': tout}


# ── Helpers de auth / sessão ─────────────────────────────────────────────────
def pcd_login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get('pcd_user_id'):
            return redirect('/pcd/entrar')
        return f(*a, **k)
    return wrap


def _get_user():
    uid = session.get('pcd_user_id')
    if not uid:
        return None
    conn = get_pcd_db()
    u = conn.execute('SELECT * FROM pcd_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return u


def _is_admin(u):
    return bool(u and ADMIN_EMAIL and (u['email'] or '').lower() == ADMIN_EMAIL)


def _cpf_digits(cpf):
    return ''.join(c for c in (cpf or '') if c.isdigit())


def _cpf_valido(cpf):
    cpf = _cpf_digits(cpf)
    if len(cpf) not in (11, 14):
        return False
    return True  # validação leve (aceita CPF 11 ou CNPJ 14); Asaas valida de verdade


def _registrar_uso(user_id, tipo, creditos, tin=0, tout=0):
    try:
        conn = get_pcd_db()
        conn.execute('INSERT INTO pcd_uso_log (user_id,tipo,creditos,tokens_in,tokens_out,created_at) '
                     'VALUES (?,?,?,?,?,?)', (user_id, tipo, creditos, int(tin), int(tout),
                                             datetime.now().isoformat()))
        conn.commit(); conn.close()
    except Exception:
        pass


@pcd_bp.context_processor
def _inject():
    u = _get_user()
    return {'pcd_user': u, 'pcd_admin': _is_admin(u), 'TRILHAS': TRILHAS,
            'DEFICIENCIAS': DEFICIENCIAS, 'PERFIS': PERFIS, 'PACOTES': PACOTES,
            'CUSTO_ABRIR_CASO': CUSTO_ABRIR_CASO, 'CUSTO_DOC_EXTRA': CUSTO_DOC_EXTRA,
            'pcd_creditos': ('∞' if _is_admin(u) else (u['creditos'] if u else 0))}


# ── Motor de fases (helpers) ─────────────────────────────────────────────────
def _get_caso(caso_id):
    u = _get_user()
    conn = get_pcd_db()
    c = conn.execute('SELECT * FROM pcd_casos WHERE id=?', (caso_id,)).fetchone()
    conn.close()
    if not c or (c['user_id'] != u['id'] and not _is_admin(u)):
        return None
    return c


def _docs_enviados(caso_id, fase):
    conn = get_pcd_db()
    rows = conn.execute('SELECT * FROM pcd_documentos WHERE caso_id=? AND fase=? ORDER BY id DESC',
                        (caso_id, fase)).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r['tipo_doc'], r)
    return out


def _fase_estado(caso):
    reqs = docs_da_fase(caso['trilha'], caso['fase_atual'], caso['condutor'])
    enviados = _docs_enviados(caso['id'], caso['fase_atual'])
    lista, completa = [], True
    for d in reqs:
        env = enviados.get(d['id'])
        status = env['status'] if env else 'falta'
        if status != 'ok':
            completa = False
        lista.append({**d, 'status': status, 'enviado': bool(env),
                      'feedback': env['analise'] if env else '',
                      'modelos': modelos_do_doc(d['id'], caso)})
    return lista, completa


# ── Rotas: público / auth ────────────────────────────────────────────────────
@pcd_bp.route('/')
def landing():
    return render_template('pcd/landing.html')


@pcd_bp.route('/cadastrar', methods=['GET', 'POST'])
def cadastrar():
    if session.get('pcd_user_id'):
        return redirect('/pcd/app')
    if request.method == 'POST':
        nome     = (request.form.get('nome') or '').strip()
        email    = (request.form.get('email') or '').strip().lower()
        telefone = _cpf_digits(request.form.get('telefone'))
        senha    = request.form.get('senha') or ''
        perfil   = request.form.get('perfil') if request.form.get('perfil') in PERFIS else 'cliente'
        if not nome or not email or len(senha) < 6:
            flash('Preencha nome, e-mail e senha de ao menos 6 caracteres.', 'erro')
            return redirect('/pcd/cadastrar')
        conn = get_pcd_db()
        if conn.execute('SELECT 1 FROM pcd_users WHERE email=?', (email,)).fetchone():
            conn.close()
            flash('Esse e-mail já tem conta. Faça login.', 'erro')
            return redirect('/pcd/entrar')
        cur = conn.execute(
            'INSERT INTO pcd_users (nome,email,telefone,password_hash,perfil,creditos,created_at) '
            'VALUES (?,?,?,?,?,?,?)',
            (nome, email, telefone, generate_password_hash(senha), perfil, FREE_CREDITS,
             datetime.now().isoformat()))
        conn.commit()
        session['pcd_user_id'] = cur.lastrowid
        conn.close()
        flash('Conta criada! Bem-vindo ao PCD Fácil. 🎉', 'ok')
        return redirect('/pcd/app')
    return render_template('pcd/cadastrar.html')


@pcd_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if session.get('pcd_user_id'):
        return redirect('/pcd/app')
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        conn = get_pcd_db()
        u = conn.execute('SELECT * FROM pcd_users WHERE email=?', (email,)).fetchone()
        if u and check_password_hash(u['password_hash'], senha):
            conn.execute('UPDATE pcd_users SET ultimo_acesso=? WHERE id=?',
                         (datetime.now().isoformat(), u['id']))
            conn.commit(); conn.close()
            session['pcd_user_id'] = u['id']
            return redirect('/pcd/app')
        conn.close()
        flash('E-mail ou senha incorretos.', 'erro')
    return render_template('pcd/entrar.html')


@pcd_bp.route('/sair')
def sair():
    session.pop('pcd_user_id', None)
    return redirect('/pcd')


# ── Painel ───────────────────────────────────────────────────────────────────
@pcd_bp.route('/app')
@pcd_login_required
def painel():
    u = _get_user()
    if not u:
        session.clear(); return redirect('/pcd/entrar')
    q       = (request.args.get('q') or '').strip()
    fstatus = request.args.get('status') or ''
    ftrilha = request.args.get('trilha') or ''
    sql = 'SELECT * FROM pcd_casos WHERE user_id=?'
    params = [u['id']]
    if q:       sql += ' AND nome_cliente LIKE ?'; params.append(f'%{q}%')
    if fstatus: sql += ' AND status=?';            params.append(fstatus)
    if ftrilha: sql += ' AND trilha=?';            params.append(ftrilha)
    sql += ' ORDER BY id DESC'
    conn = get_pcd_db()
    casos = conn.execute(sql, params).fetchall()
    stats = {
        'total':     conn.execute('SELECT COUNT(*) c FROM pcd_casos WHERE user_id=?', (u['id'],)).fetchone()['c'],
        'andamento': conn.execute("SELECT COUNT(*) c FROM pcd_casos WHERE user_id=? AND status='em_andamento'", (u['id'],)).fetchone()['c'],
        'montados':  conn.execute("SELECT COUNT(*) c FROM pcd_casos WHERE user_id=? AND status='montado'", (u['id'],)).fetchone()['c'],
    }
    conn.close()
    return render_template('pcd/painel.html', casos=casos, stats=stats, q=q, fstatus=fstatus, ftrilha=ftrilha)


# ── Casos / fases ────────────────────────────────────────────────────────────
@pcd_bp.route('/caso/novo', methods=['GET', 'POST'])
@pcd_login_required
def caso_novo():
    u = _get_user()
    if request.method == 'POST':
        trilha = request.form.get('trilha')
        if trilha not in TRILHAS:
            flash('Escolha uma trilha.', 'erro')
            return redirect('/pcd/caso/novo')
        tipo_def = request.form.get('tipo_deficiencia') or ''
        condutor = 'sim' if request.form.get('condutor') == 'sim' else 'nao'
        nome_cliente = (request.form.get('nome_cliente') or '').strip() or u['nome']
        if not _is_admin(u) and not debita_creditos(u['id'], CUSTO_ABRIR_CASO):
            flash(f'Abrir um caso custa {CUSTO_ABRIR_CASO} créditos. Compre mais pra continuar.', 'erro')
            return redirect('/pcd/comprar')
        agora = datetime.now().isoformat()
        conn = get_pcd_db()
        cur = conn.execute(
            'INSERT INTO pcd_casos (user_id,trilha,uf,nome_cliente,tipo_deficiencia,condutor,'
            "fase_atual,status,created_at,updated_at) VALUES (?,?,?,?,?,?,1,'em_andamento',?,?)",
            (u['id'], trilha, UF_PADRAO, nome_cliente, tipo_def, condutor, agora, agora))
        cid = cur.lastrowid
        conn.commit(); conn.close()
        _registrar_uso(u['id'], 'abrir_caso', CUSTO_ABRIR_CASO)
        return redirect(f'/pcd/caso/{cid}')
    return render_template('pcd/caso_novo.html')


@pcd_bp.route('/caso/<int:caso_id>')
@pcd_login_required
def caso_ver(caso_id):
    c = _get_caso(caso_id)
    if not c:
        flash('Caso não encontrado.', 'erro')
        return redirect('/pcd/app')
    docs, completa = _fase_estado(c)
    icms = None
    valor_raw = (request.args.get('valor') or '').replace('.', '').replace(',', '.')
    if valor_raw:
        try:
            icms = avaliar_icms(c['uf'], float(valor_raw))
        except ValueError:
            icms = None
    return render_template('pcd/caso.html', c=c, docs=docs, completa=completa,
                           total=total_fases(c['trilha']),
                           fase_def=TRILHAS[c['trilha']]['fases'][c['fase_atual'] - 1], icms=icms)


@pcd_bp.route('/caso/<int:caso_id>/doc', methods=['POST'])
@pcd_login_required
def caso_doc(caso_id):
    c = _get_caso(caso_id)
    if not c:
        return redirect('/pcd/app')
    u = _get_user()
    tipo_doc = request.form.get('tipo_doc')
    ids_validos = {d['id'] for d in docs_da_fase(c['trilha'], c['fase_atual'], c['condutor'])}
    if tipo_doc not in ids_validos:
        flash('Documento inválido para esta fase.', 'erro')
        return redirect(f'/pcd/caso/{caso_id}')
    f = request.files.get('arquivo')
    if not f or not f.filename:
        flash('Selecione um arquivo (foto ou PDF).', 'erro')
        return redirect(f'/pcd/caso/{caso_id}')
    file_bytes = f.read()
    if len(file_bytes) > 12 * 1024 * 1024:
        flash('Arquivo muito grande (máx. 12MB). Tire a foto com menos resolução.', 'erro')
        return redirect(f'/pcd/caso/{caso_id}')
    mime = f.mimetype or 'image/jpeg'

    conn = get_pcd_db()
    ja_ok = conn.execute("SELECT 1 FROM pcd_documentos WHERE caso_id=? AND tipo_doc=? AND status='ok'",
                         (caso_id, tipo_doc)).fetchone()
    conn.close()
    cobrar = CUSTO_DOC_EXTRA if (ja_ok and _ia_ativa() and not _is_admin(u)) else 0
    if cobrar and not debita_creditos(u['id'], cobrar):
        flash(f'Reanalisar um documento custa {CUSTO_DOC_EXTRA} créditos. Compre mais.', 'erro')
        return redirect('/pcd/comprar')

    tin = tout = 0
    if _ia_ativa():
        rotulo = next((d['label'] for d in docs_da_fase(c['trilha'], c['fase_atual'], c['condutor']) if d['id'] == tipo_doc), tipo_doc)
        ajuda  = next((d['ajuda'] for d in docs_da_fase(c['trilha'], c['fase_atual'], c['condutor']) if d['id'] == tipo_doc), '')
        ctx = f"Trilha {c['trilha']} ({TRILHAS[c['trilha']]['nome']}), Fase {c['fase_atual']}."
        res = _analisar_doc(rotulo, ajuda, ctx, file_bytes, mime)
        if 'erro' in res:
            if cobrar:
                add_creditos(u['id'], cobrar)  # estorna
            flash(res['erro'], 'erro')
            return redirect(f'/pcd/caso/{caso_id}')
        status, msg, tin, tout = res['status'], res['mensagem'], res['tin'], res['tout']
    else:
        status = 'ok'
        msg = 'Recebido. (Conferência por IA desligada — configure GEMINI_API_KEY.)'

    pasta = os.path.join(_UPLOAD_DIR, f'caso_{caso_id}')
    os.makedirs(pasta, exist_ok=True)
    nome = f"f{c['fase_atual']}_{tipo_doc}_{secure_filename(f.filename)}"
    with open(os.path.join(pasta, nome), 'wb') as out:
        out.write(file_bytes)

    conn = get_pcd_db()
    conn.execute('INSERT INTO pcd_documentos (caso_id,fase,tipo_doc,arquivo,status,analise,created_at) '
                 'VALUES (?,?,?,?,?,?,?)',
                 (caso_id, c['fase_atual'], tipo_doc, nome, status, msg, datetime.now().isoformat()))
    conn.execute('UPDATE pcd_casos SET updated_at=? WHERE id=?', (datetime.now().isoformat(), caso_id))
    conn.commit(); conn.close()
    if cobrar:
        _registrar_uso(u['id'], 'reanalise_doc', cobrar, tin, tout)
    icon = {'ok': '✅', 'atencao': '⚠️', 'falta': '❌'}.get(status, '')
    flash(f'{icon} {msg}' if msg else 'Documento anexado.', 'ok' if status == 'ok' else 'erro')
    return redirect(f'/pcd/caso/{caso_id}')


@pcd_bp.route('/caso/<int:caso_id>/avancar', methods=['POST'])
@pcd_login_required
def caso_avancar(caso_id):
    c = _get_caso(caso_id)
    if not c:
        return redirect('/pcd/app')
    _, completa = _fase_estado(c)
    if not completa:
        flash('Ainda faltam documentos nesta fase.', 'erro')
        return redirect(f'/pcd/caso/{caso_id}')
    conn = get_pcd_db()
    if c['fase_atual'] < total_fases(c['trilha']):
        conn.execute('UPDATE pcd_casos SET fase_atual=fase_atual+1, updated_at=? WHERE id=?',
                     (datetime.now().isoformat(), caso_id))
        flash('Fase concluída! Avançando para a próxima. 🎉', 'ok')
    else:
        conn.execute("UPDATE pcd_casos SET status='montado', updated_at=? WHERE id=?",
                     (datetime.now().isoformat(), caso_id))
        flash('Processo montado! 🏆', 'ok')
    conn.commit(); conn.close()
    return redirect(f'/pcd/caso/{caso_id}')


# ── Créditos / PIX (Asaas — reaproveita conta do 4kitem) ─────────────────────
_ASAAS_BASE = 'https://api.asaas.com/v3'
PAGO_OK = ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH')


def _asaas_req(method, endpoint, data=None):
    try:
        r = _requests.request(method, f'{_ASAAS_BASE}{endpoint}',
            headers={'access_token': os.environ.get('ASAAS_API_KEY', ''), 'Content-Type': 'application/json'},
            json=data, timeout=20)
        return r.json() if r.content else {}
    except Exception as e:
        return {'error': str(e)}


def _asaas_ativo():
    return bool(os.environ.get('ASAAS_API_KEY', ''))


def _asaas_cliente(u, cpf):
    if u['asaas_customer_id']:
        return u['asaas_customer_id']
    cpf = _cpf_digits(cpf)
    cid = None
    if cpf:
        busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf}&limit=1')
        if busca.get('data'):
            cid = busca['data'][0].get('id')
    if not cid:
        resp = _asaas_req('POST', '/customers', {
            'name': u['nome'], 'email': u['email'],
            'mobilePhone': _cpf_digits(u['telefone']), 'cpfCnpj': cpf, 'notificationDisabled': True})
        cid = resp.get('id')
    if cid:
        conn = get_pcd_db()
        conn.execute('UPDATE pcd_users SET asaas_customer_id=?, cpf=? WHERE id=?', (cid, cpf, u['id']))
        conn.commit(); conn.close()
    return cid or ''


def _pcd_confirmar_compra(compra_id):
    """Credita uma compra de forma ATÔMICA e idempotente (não dobra)."""
    conn = get_pcd_db()
    cur = conn.execute("UPDATE pcd_compras SET status='pago' WHERE id=? AND status='pendente'", (compra_id,))
    conn.commit()
    if cur.rowcount == 0:
        conn.close(); return False
    row = conn.execute('SELECT user_id, creditos FROM pcd_compras WHERE id=?', (compra_id,)).fetchone()
    conn.close()
    if not row:
        return False
    add_creditos(row['user_id'], row['creditos'])
    log.info(f'[PCD] Compra {compra_id} PAGA — +{row["creditos"]} créditos (user {row["user_id"]})')
    return True


def pcd_webhook_confirmar(external_ref, payment_id=''):
    """Chamado pelo webhook global (app.py) p/ refs 'pcd_<compra_id>'."""
    try:
        cid = int(str(external_ref).split('_')[1])
    except (IndexError, ValueError):
        return False
    return _pcd_confirmar_compra(cid)


@pcd_bp.route('/comprar')
@pcd_login_required
def comprar():
    return render_template('pcd/comprar.html', pacotes=PACOTES)


@pcd_bp.route('/checkout/<pacote>', methods=['POST'])
@pcd_login_required
def checkout(pacote):
    u = _get_user()
    if pacote not in PACOTES:
        return redirect('/pcd/comprar')
    if not _asaas_ativo():
        flash('Pagamento PIX indisponível no momento.', 'erro')
        return redirect('/pcd/comprar')
    cpf = _cpf_digits(request.form.get('cpf'))
    if not _cpf_valido(cpf):
        flash('Informe um CPF (11) ou CNPJ (14) válido.', 'erro')
        return redirect('/pcd/comprar')
    p = PACOTES[pacote]
    customer_id = _asaas_cliente(u, cpf)
    if not customer_id:
        flash('Não foi possível iniciar o pagamento. Tente novamente.', 'erro')
        return redirect('/pcd/comprar')
    conn = get_pcd_db()
    cur = conn.execute('INSERT INTO pcd_compras (user_id,pacote,creditos,valor,status,billing_type,created_at) '
                       'VALUES (?,?,?,?,"pendente","PIX",?)',
                       (u['id'], pacote, p['creditos'], p['preco'], datetime.now().isoformat()))
    compra_id = cur.lastrowid
    conn.commit(); conn.close()
    venc = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    pay = _asaas_req('POST', '/payments', {
        'customer': customer_id, 'billingType': 'PIX', 'value': p['preco'],
        'dueDate': venc, 'description': f'PCD Facil — {p["rotulo"]}',
        'externalReference': f'pcd_{compra_id}'})
    pid = pay.get('id')
    if not pid:
        flash((pay.get('errors') or [{}])[0].get('description', 'Erro ao gerar o PIX.'), 'erro')
        return redirect('/pcd/comprar')
    conn = get_pcd_db()
    conn.execute('UPDATE pcd_compras SET asaas_payment_id=? WHERE id=?', (pid, compra_id))
    conn.commit(); conn.close()
    return redirect(f'/pcd/pix/{compra_id}')


@pcd_bp.route('/pix/<int:compra_id>')
@pcd_login_required
def pix(compra_id):
    u = _get_user()
    conn = get_pcd_db()
    compra = conn.execute('SELECT * FROM pcd_compras WHERE id=? AND user_id=?', (compra_id, u['id'])).fetchone()
    conn.close()
    if not compra:
        return redirect('/pcd/comprar')
    qr = copia = ''
    if compra['status'] == 'pendente' and compra['asaas_payment_id']:
        resp = _asaas_req('GET', f'/payments/{compra["asaas_payment_id"]}/pixQrCode')
        qr = resp.get('encodedImage', ''); copia = resp.get('payload', '')
    return render_template('pcd/pix.html', compra=compra, qr=qr, copia=copia)


@pcd_bp.route('/pix-status/<int:compra_id>', methods=['POST'])
@pcd_login_required
def pix_status(compra_id):
    u = _get_user()
    conn = get_pcd_db()
    compra = conn.execute('SELECT * FROM pcd_compras WHERE id=? AND user_id=?', (compra_id, u['id'])).fetchone()
    conn.close()
    if not compra:
        return jsonify({'erro': 'não encontrada'}), 404
    if compra['status'] == 'pago':
        return jsonify({'pago': True, 'creditos': get_creditos(u['id'])})
    pid = compra['asaas_payment_id']
    if pid and (_asaas_req('GET', f'/payments/{pid}').get('status') or '').upper() in PAGO_OK:
        _pcd_confirmar_compra(compra_id)
        return jsonify({'pago': True, 'creditos': get_creditos(u['id'])})
    return jsonify({'pago': False})


# ── Reconciliador 24/7 (rede de segurança do PIX) ────────────────────────────
def _pcd_reconciliar_loop():
    time.sleep(150)
    log.info('[PCD] Reconciliador de pagamentos ATIVO (3 min)')
    while True:
        try:
            conn = get_pcd_db()
            limite = (datetime.now() - timedelta(seconds=90)).isoformat()
            rows = conn.execute(
                "SELECT id, asaas_payment_id FROM pcd_compras "
                "WHERE status='pendente' AND asaas_payment_id IS NOT NULL AND asaas_payment_id<>'' "
                "AND created_at < ?", (limite,)).fetchall()
            conn.close()
            for r in rows:
                try:
                    if (_asaas_req('GET', f'/payments/{r["asaas_payment_id"]}').get('status') or '').upper() in PAGO_OK:
                        _pcd_confirmar_compra(r['id'])
                except Exception as _e:
                    log.warning(f'[PCD] reconciliador compra {r["id"]}: {_e}')
        except Exception as _e:
            log.error(f'[PCD] reconciliador loop: {_e}')
        time.sleep(180)


threading.Thread(target=_pcd_reconciliar_loop, daemon=True, name='pcd-reconciliador').start()
