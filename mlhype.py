"""
mlhype.py — MLhype: inteligência para vendedores do Mercado Livre.
Blueprint Flask registrado em /mlhype (padrão dos demais módulos do 4kitem).

ESTADO: Passo 1 (fundação) — banco + blueprint + health.
A esteira de 5 agentes, o coletor diário do ML e o billing vêm nos passos
seguintes (ver ULTRAPLAN). Este arquivo mantém o ponto de registro estável
no app.py para não precisar mexer nele a cada passo.
"""
import os
import logging

from flask import Blueprint, jsonify, request, render_template_string, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash

from mlhype_db import (init_mlhype_db, estatisticas,
                       radar_top, radar_trends, radar_categorias_com_dados,
                       criar_usuario, usuario_por_email, usuario_por_id, marcar_acesso,
                       plano_efetivo, plano_libera, contar_buscas_hoje, registrar_uso,
                       FREE_BUSCAS_DIA, get_mlhype_db, oportunidades)

log = logging.getLogger(__name__)

mlhype_bp = Blueprint('mlhype', __name__, url_prefix='/mlhype')

VERSAO = '0.4.0'  # Passo 4 — painel do Radar

# Nomes das 32 categorias-raiz do Mercado Livre Brasil (p/ o seletor do Radar).
CATEGORIAS_ML = {
    'MLB5672': 'Acessórios para Veículos', 'MLB1574': 'Casa, Móveis e Decoração',
    'MLB1430': 'Calçados, Roupas e Bolsas', 'MLB1196': 'Livros, Revistas e Comics',
    'MLB263532': 'Ferramentas', 'MLB1276': 'Esportes e Fitness',
    'MLB1132': 'Brinquedos e Hobbies', 'MLB1368': 'Arte, Papelaria e Armarinho',
    'MLB1246': 'Beleza e Cuidado Pessoal', 'MLB1648': 'Informática',
    'MLB1500': 'Construção', 'MLB3937': 'Joias e Relógios',
    'MLB1051': 'Celulares e Telefones', 'MLB1000': 'Eletrônicos, Áudio e Vídeo',
    'MLB12404': 'Festas e Lembrancinhas', 'MLB5726': 'Eletrodomésticos',
    'MLB1499': 'Indústria e Comércio', 'MLB1953': 'Mais Categorias',
    'MLB1071': 'Animais', 'MLB1384': 'Bebês',
    'MLB1168': 'Música, Filmes e Seriados', 'MLB264586': 'Saúde',
    'MLB1182': 'Instrumentos Musicais', 'MLB1039': 'Câmeras e Acessórios',
    'MLB1403': 'Alimentos e Bebidas', 'MLB1367': 'Antiguidades e Coleções',
    'MLB271599': 'Agro', 'MLB1144': 'Games', 'MLB1743': 'Carros, Motos e Outros',
    'MLB1540': 'Serviços', 'MLB218519': 'Ingressos', 'MLB1459': 'Imóveis',
}


def _nome_cat(cid):
    return CATEGORIAS_ML.get(cid, cid)


# ── Sessão / login (Passo 5 — auth) ────────────────────────────────────────────
def _usuario_atual():
    uid = session.get('mlhype_user_id')
    if not uid:
        return None
    try:
        return usuario_por_id(uid)
    except Exception:
        return None


def _exige_login():
    """Retorna um redirect p/ a tela de entrar se não estiver logado; senão None."""
    if not session.get('mlhype_user_id'):
        return redirect('/mlhype/entrar?next=' + request.path)
    return None


# ── Planos (preços ancorados no valor; cobrança Asaas é o último fio a ligar) ──
PLANOS = {
    'free':     {'nome': 'Grátis', 'preco': 0, 'preco_fmt': 'R$ 0',
                 'desc': f'Prove o valor: {FREE_BUSCAS_DIA} Fichas de Ataque por dia.',
                 'features': ['Radar de Demanda', f'{FREE_BUSCAS_DIA} Fichas de Ataque/dia', 'Trends do dia']},
    'starter':  {'nome': 'Starter', 'preco': 47, 'preco_fmt': 'R$ 47',
                 'desc': 'O Radar completo, sem limite de consulta.',
                 'features': ['Radar completo das 32 categorias', 'Tendência e brechas', 'Trends do dia']},
    'pro':      {'nome': 'Pro', 'preco': 97, 'preco_fmt': 'R$ 97',
                 'desc': 'A Ficha de Ataque por IA, ilimitada + histórico.',
                 'features': ['Tudo do Starter', 'Ficha de Ataque ILIMITADA', 'Histórico de tendências']},
    'business': {'nome': 'Business', 'preco': 197, 'preco_fmt': 'R$ 197',
                 'desc': 'O moat: fornecedor nacional + alertas.',
                 'features': ['Tudo do Pro', '🎯 Caçador de Fornecedor BR', 'Alertas de oportunidade']},
}


@mlhype_bp.route('/')
def mlhype_home():
    """Landing pública do MLhype (ciente de login)."""
    u = _usuario_atual()
    if u:
        primeiro = ((u.get('nome') or '').split() or ['você'])[0]
        botoes = ('<a class="btn" href="/mlhype/oportunidades">💡 Ver dicas do que vender →</a>'
                  f'<div class="sub2"><a class="link" href="/mlhype/radar">explorar o Radar</a> · {primeiro} · <a class="link" href="/mlhype/sair">sair</a></div>')
    else:
        botoes = ('<a class="btn" href="/mlhype/cadastrar">Criar conta grátis →</a>'
                  '<div class="sub2"><a class="link" href="/mlhype/entrar">já tenho conta · entrar</a></div>')
    return f'''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MLhype — Inteligência para vendedores do Mercado Livre</title>
<style>
 body{{font-family:system-ui,Segoe UI,sans-serif;background:#0b1020;color:#e7ecf5;
   margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
 .card{{max-width:560px;text-align:center}}
 h1{{font-size:30px;margin:0 0 6px;letter-spacing:.5px}}
 .tag{{color:#7cc0ff;font-weight:700}}
 p{{color:#9fb0d0;line-height:1.6;font-size:15px}}
 .bussola{{background:#0f1730;border:1px solid #21304f;border-radius:12px;
   padding:16px 20px;margin:20px 0;font-style:italic;color:#cfe0ff}}
 .btn{{display:inline-block;background:linear-gradient(135deg,#2f6bff,#7c3aed);color:#fff;
   padding:13px 26px;border-radius:10px;text-decoration:none;font-weight:700;font-size:16px;
   box-shadow:0 6px 20px rgba(124,58,237,.35)}}
 .sub2{{margin-top:14px;color:#9fb0d0;font-size:14px}} .link{{color:#7cc0ff;text-decoration:none}}
</style></head><body>
<div class="card">
 <h1>MLhype <span class="tag">🔥</span></h1>
 <p>Inteligência para quem vende no <b>Mercado Livre</b>.</p>
 <div class="bussola">"Descubra o que bomba, ache o fornecedor NO BRASIL e roube a
   venda do líder — anunciando melhor e mais barato."</div>
 {botoes}
</div>
</body></html>'''


@mlhype_bp.route('/health')
def mlhype_health():
    """Health-check + termômetro do histórico acumulado (o tesouro)."""
    try:
        st = estatisticas()
        return jsonify({'ok': True, 'versao': VERSAO, 'modulo': 'mlhype', 'stats': st})
    except Exception as e:
        log.error(f'[MLHYPE] health erro: {e}')
        return jsonify({'ok': False, 'erro': str(e)}), 500


# ── Auth: cadastrar / entrar / sair (Passo 5) ─────────────────────────────────
_AUTH_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MLhype · {{titulo}}</title>
<style>
 body{font-family:system-ui,Segoe UI,sans-serif;background:#0b1020;color:#e7ecf5;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
 .box{width:100%;max-width:380px} h1{font-size:24px;margin:0 0 4px;text-align:center}
 .fire{background:linear-gradient(135deg,#2f6bff,#7c3aed);-webkit-background-clip:text;background-clip:text;color:transparent}
 p.sub{color:#9fb0d0;margin:0 0 18px;font-size:14px;text-align:center}
 form{background:#0f1730;border:1px solid #21304f;border-radius:12px;padding:20px}
 label{display:block;font-size:12px;color:#8aa0c6;margin:10px 0 4px}
 input{width:100%;box-sizing:border-box;background:#0b1020;color:#e7ecf5;border:1px solid #21304f;border-radius:8px;padding:11px;font-size:15px}
 button{width:100%;margin-top:16px;background:linear-gradient(135deg,#2f6bff,#7c3aed);color:#fff;border:0;border-radius:8px;padding:12px;font-weight:700;font-size:16px;cursor:pointer}
 .erro{background:#2a0a0a;color:#ff8a8a;border:1px solid #5a1a1a;border-radius:8px;padding:9px 12px;font-size:13px;margin-bottom:12px}
 .alt{text-align:center;margin-top:14px;font-size:14px;color:#9fb0d0} a{color:#7cc0ff;text-decoration:none}
</style></head><body><div class=box>
<h1>MLhype <span class=fire>🔥</span></h1>
<p class=sub>{{sub}}</p>
<form method=post>
 {% if erro %}<div class=erro>{{erro}}</div>{% endif %}
 {% if next %}<input type=hidden name=next value="{{next}}">{% endif %}
 {% if cadastro %}<label>Nome</label><input name=nome required>{% endif %}
 <label>E-mail</label><input name=email type=email required autocomplete=email>
 <label>Senha</label><input name=senha type=password required minlength=6>
 <button type=submit>{{titulo}}</button>
</form>
<div class=alt>{{alt|safe}}</div>
</div></body></html>'''


@mlhype_bp.route('/cadastrar', methods=['GET', 'POST'])
def mlhype_cadastrar():
    if session.get('mlhype_user_id'):
        return redirect('/mlhype/radar')
    erro = None
    if request.method == 'POST':
        nome = (request.form.get('nome') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        if not (nome and email and senha):
            erro = 'Preencha nome, e-mail e senha.'
        elif len(senha) < 6:
            erro = 'A senha precisa de ao menos 6 caracteres.'
        elif usuario_por_email(email):
            erro = 'Já existe uma conta com esse e-mail. Faça login.'
        else:
            uid = criar_usuario(nome, email, '', generate_password_hash(senha))
            session['mlhype_user_id'] = uid
            session['mlhype_user_nome'] = nome
            return redirect('/mlhype/radar')
    return render_template_string(
        _AUTH_HTML, titulo='Criar conta', cadastro=True, next='',
        sub='Crie sua conta grátis e descubra o que bomba no Mercado Livre.', erro=erro,
        alt='Já tem conta? <a href="/mlhype/entrar">Entrar</a>')


@mlhype_bp.route('/entrar', methods=['GET', 'POST'])
def mlhype_entrar():
    if session.get('mlhype_user_id'):
        return redirect('/mlhype/radar')
    erro = None
    nxt = request.values.get('next') or '/mlhype/radar'
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        u = usuario_por_email(email)
        if u and u.get('password_hash') and check_password_hash(u['password_hash'], senha):
            session['mlhype_user_id'] = u['id']
            session['mlhype_user_nome'] = u['nome']
            try:
                marcar_acesso(u['id'])
            except Exception:
                pass
            return redirect(nxt if nxt.startswith('/mlhype') else '/mlhype/radar')
        erro = 'E-mail ou senha incorretos.'
    return render_template_string(
        _AUTH_HTML, titulo='Entrar', cadastro=False, next=nxt,
        sub='Bem-vindo de volta.', erro=erro,
        alt='Não tem conta? <a href="/mlhype/cadastrar">Criar grátis</a>')


@mlhype_bp.route('/sair')
def mlhype_sair():
    for k in ('mlhype_user_id', 'mlhype_user_nome'):
        session.pop(k, None)
    return redirect('/mlhype/')


# ── Painel do Radar de Demanda (Passo 4) ──────────────────────────────────────
def _fmt_brl(v):
    if v is None:
        return '—'
    try:
        return 'R$ ' + f'{float(v):,.2f}'.replace(',', '#').replace('.', ',').replace('#', '.')
    except Exception:
        return '—'


def calcular_margem(preco_venda, custo, categoria, frete=0, tipo='classico'):
    """Lucro líquido REAL: preço − comissão (taxa oficial do ML) − frete − custo.
    Usa a taxa real do endpoint listing_prices; cai p/ ~14% só se ele falhar."""
    import mlhype_ml as ml
    try:
        preco_venda = float(preco_venda)
    except (TypeError, ValueError):
        return None
    custo = float(custo or 0)
    frete = float(frete or 0)
    t = (ml.taxas_ml(preco_venda, categoria) or {})
    t = t.get(tipo) or t.get('classico') or t.get('premium') or {}
    comissao = t.get('fee')
    estimado = comissao is None
    if estimado:
        comissao = round(preco_venda * 0.14, 2)
        pct = 14
    else:
        comissao = round(float(comissao), 2)
        pct = t.get('pct')
    lucro = round(preco_venda - comissao - frete - custo, 2)
    margem = round(lucro / preco_venda * 100, 1) if preco_venda else 0
    markup = round(lucro / custo * 100, 1) if custo else None
    return {'preco': preco_venda, 'comissao': comissao, 'comissao_pct': pct,
            'frete': frete, 'custo': custo, 'lucro': lucro, 'margem_pct': margem,
            'markup_pct': markup, 'estimado': estimado, 'tipo': tipo}


_RADAR_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>MLhype · Radar de Demanda</title>
<style>
 :root{--bg:#0b1020;--card:#0f1730;--bd:#21304f;--mut:#8aa0c6;--txt:#e7ecf5;--ac:#7cc0ff}
 *{box-sizing:border-box} body{font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:18px}
 .wrap{max-width:1000px;margin:0 auto}
 header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:14px}
 h1{font-size:21px;margin:0} .fire{background:linear-gradient(135deg,#2f6bff,#7c3aed);-webkit-background-clip:text;background-clip:text;color:transparent}
 a{color:var(--ac);text-decoration:none}
 select{background:var(--card);color:var(--txt);border:1px solid var(--bd);border-radius:8px;padding:9px 12px;font-size:15px;max-width:100%}
 .sub{color:var(--mut);font-size:13px;margin:8px 0 16px}
 .layout{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}
 .main{flex:1;min-width:300px}
 table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--bd);border-radius:10px;overflow:hidden}
 th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--bd);font-size:14px} tr:last-child td{border-bottom:none}
 th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
 .pos{font-weight:800;font-size:16px;width:58px} .up{color:#5ee0a0}.down{color:#ff8a8a}.flat{color:var(--mut)}
 .prod{font-weight:600;line-height:1.3} .preco{white-space:nowrap;font-weight:700}
 .brecha{display:inline-block;background:#062a17;color:#5ee0a0;border:1px solid #155a38;padding:2px 8px;border-radius:20px;font-size:12px;font-weight:700}
 .analisar{display:inline-block;background:linear-gradient(135deg,#2f6bff,#7c3aed);color:#fff;padding:6px 12px;border-radius:8px;font-size:13px;font-weight:700;white-space:nowrap}
 aside{width:240px;min-width:220px;background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px}
 aside h3{margin:0 0 10px;font-size:13px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut)}
 .trend{display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--bd);font-size:14px} .trend:last-child{border:none} .trend b{color:var(--ac);width:22px;text-align:right}
 .empty{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:30px;text-align:center;color:var(--mut)}
</style></head><body><div class=wrap>
<header>
 <h1>MLhype <span class=fire>🔥</span> · Radar de Demanda</h1>
 <span style="font-size:13px;color:var(--mut)"><a href="/mlhype/oportunidades" style="color:#5ee0a0;font-weight:700">💡 Dicas</a> · <a href="/mlhype/calculadora">🧮 Calculadora</a> · <a href="/mlhype/planos" style="color:#b9a6ff;font-weight:700">planos</a> · {% if nome %}{{nome.split()[0]}} · {% endif %}<a href="/mlhype/sair">sair</a></span>
</header>
{% if seletor %}
<form method=get><select name=cat onchange="this.form.submit()">
 {% for c in seletor %}<option value="{{c.id}}" {{'selected' if c.id==cat else ''}}>{{c.nome}} ({{c.n}})</option>{% endfor %}
</select></form>
<div class=sub>Top de <b>{{cat_nome}}</b>{% if data %} · coletado em {{data}}{% endif %} · {{linhas|length}} produtos no radar</div>
<div class=layout>
 <div class=main>
 {% if linhas %}
 <table><tr><th>#</th><th>Produto</th><th>Preço líder</th><th>Concorrentes</th><th></th></tr>
 {% for r in linhas %}
 <tr>
  <td class=pos>{{r.pos}}{% if r.delta_pos and r.delta_pos>0 %} <span class=up title="subiu {{r.delta_pos}}">▲</span>{% elif r.delta_pos and r.delta_pos<0 %} <span class=down title="caiu">▼</span>{% endif %}</td>
  <td class=prod>{{r.titulo or r.mlb_item_id}}</td>
  <td class=preco>{{fmt(r.preco)}}{% if r.delta_preco and r.delta_preco<0 %} <span class=up>↓</span>{% elif r.delta_preco and r.delta_preco>0 %} <span class=down>↑</span>{% endif %}</td>
  <td><b>{{r.num_ofertas if r.num_ofertas is not none else '—'}}</b>{% if r.brecha %} <span class=brecha>🎯 brecha</span>{% endif %}</td>
  <td><a class=analisar href="/mlhype/analisar/{{r.mlb_item_id}}{{ '?cat=' ~ cat if cat else '' }}">⚡ Analisar</a></td>
 </tr>
 {% endfor %}
 </table>
 {% else %}<div class=empty>Ainda sem dados desta categoria. O coletor roda diariamente — volte em instantes.</div>{% endif %}
 </div>
 <aside>
  <h3>🔥 Em alta agora</h3>
  {% for t in trends %}<div class=trend><b>{{t.posicao}}</b><span>{{t.termo}}</span></div>{% endfor %}
  {% if not trends %}<div class=flat>sem trends ainda</div>{% endif %}
 </aside>
</div>
{% else %}<div class=empty>O coletor ainda não rodou. Assim que a primeira coleta acontecer, o Radar aparece aqui.</div>{% endif %}
</div></body></html>'''


@mlhype_bp.route('/radar')
def mlhype_radar():
    """Radar de Demanda — Top por categoria + tendência (lê do banco/histórico)."""
    _r = _exige_login()
    if _r:
        return _r
    cats_dados = radar_categorias_com_dados()
    cat = request.args.get('cat') or (cats_dados[0]['categoria_id'] if cats_dados else None)
    linhas, data = [], None
    if cat:
        brutos, data = radar_top(cat, limit=25)
        for r in brutos:
            pos, pos_ant = r.get('pos'), r.get('pos_ant')
            r['delta_pos'] = (pos_ant - pos) if (pos_ant and pos) else None   # >0 = subiu no ranking
            pr, pr_ant = r.get('preco'), r.get('preco_ant')
            r['delta_preco'] = (pr - pr_ant) if (pr is not None and pr_ant is not None) else None
            r['brecha'] = (r.get('num_ofertas') is not None and r['num_ofertas'] <= 3)
            linhas.append(r)
    seletor = [{'id': c['categoria_id'], 'nome': _nome_cat(c['categoria_id']), 'n': c['n']}
               for c in cats_dados]
    return render_template_string(
        _RADAR_HTML, cat=cat, cat_nome=_nome_cat(cat) if cat else '',
        data=data, linhas=linhas, seletor=seletor, trends=radar_trends(25), fmt=_fmt_brl,
        nome=session.get('mlhype_user_nome', ''))


# ── PENTE FINO: "Dicas do que vender" — feed proativo de oportunidades ─────────
_OPS_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MLhype · Dicas do que vender</title>
<style>
 :root{--bg:#0b1020;--card:#0f1730;--bd:#21304f;--mut:#8aa0c6;--txt:#e7ecf5;--ac:#7cc0ff}
 *{box-sizing:border-box} body{font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:18px}
 .wrap{max-width:980px;margin:0 auto} a{color:var(--ac);text-decoration:none}
 header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:6px}
 h1{font-size:21px;margin:0} .fire{background:linear-gradient(135deg,#2f6bff,#7c3aed);-webkit-background-clip:text;background-clip:text;color:transparent}
 .sub{color:var(--mut);font-size:13px;margin:0 0 16px}
 .op{display:flex;align-items:center;gap:14px;background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px 14px;margin:9px 0}
 .sc{flex:0 0 64px;text-align:center} .scn{font-size:26px;font-weight:800;line-height:1} .scl{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.4px}
 .sc-hi .scn{color:#5ee0a0} .sc-mid .scn{color:#ffd35e} .sc-lo .scn{color:#9fb0d0}
 .info{flex:1;min-width:0} .pn{font-weight:600;line-height:1.3} .meta{color:var(--mut);font-size:12px;margin-top:2px}
 .tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
 .tag{font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px}
 .t-brecha{background:#062a17;color:#5ee0a0;border:1px solid #155a38} .t-sobe{background:#0a1f3a;color:#7cc0ff;border:1px solid #1c3a5e}
 .t-forn{background:#1c1340;color:#cbbaf5;border:1px solid #4a2da0} .t-cat{background:#11182e;color:#8aa0c6;border:1px solid #21304f}
 .go{flex:0 0 auto;background:linear-gradient(135deg,#2f6bff,#7c3aed);color:#fff;padding:9px 14px;border-radius:8px;font-weight:700;font-size:13px;white-space:nowrap}
 .empty{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:30px;text-align:center;color:var(--mut)}
</style></head><body><div class=wrap>
<header>
 <h1>MLhype <span class=fire>🔥</span> · Dicas do que vender</h1>
 <span style="font-size:13px;color:var(--mut)"><a href="/mlhype/radar">Radar</a> · <a href="/mlhype/calculadora">🧮 Calculadora</a> · <a href="/mlhype/planos" style="color:#b9a6ff;font-weight:700">planos</a> · {% if nome %}{{nome.split()[0]}} · {% endif %}<a href="/mlhype/sair">sair</a></span>
</header>
<p class=sub>O motor varreu <b>{{cat_nome}}</b> e rankeou onde mais vale a pena atacar: <b>muita procura × pouca concorrência</b>. Quanto maior o Score, melhor a brecha.</p>
{% if ops %}
{% for o in ops %}
 {% set cls = 'sc-hi' if o.score>=70 else ('sc-mid' if o.score>=50 else 'sc-lo') %}
 <div class="op {{cls}}">
  <div class=sc><div class=scn>{{o.score}}</div><div class=scl>oportun.</div></div>
  <div class=info>
   <div class=pn>{{o.titulo or o.mlb_item_id}}</div>
   <div class=meta>#{{o.pos}} no Top · {{o.num_ofertas if o.num_ofertas is not none else '—'}} concorrentes · líder {{fmt(o.preco)}}</div>
   <div class=tags>
    <span class="tag t-cat">{{o.cat_nome}}</span>
    {% if o.brecha %}<span class="tag t-brecha">🎯 brecha</span>{% endif %}
    {% if o.tendencia=='subindo' %}<span class="tag t-sobe">📈 subindo</span>{% endif %}
    {% if o.tem_fornecedor %}<span class="tag t-forn">🏷️ tem fornecedor</span>{% endif %}
   </div>
  </div>
  <a class=go href="/mlhype/analisar/{{o.mlb_item_id}}?cat={{o.categoria_id}}">⚡ Analisar</a>
 </div>
{% endfor %}
{% else %}<div class=empty>O coletor ainda está enchendo o mercado — volte em alguns minutos pras dicas aparecerem.</div>{% endif %}
</div></body></html>'''


@mlhype_bp.route('/oportunidades')
def mlhype_oportunidades():
    """Pente fino: as melhores oportunidades do mercado, rankeadas pelo motor."""
    _r = _exige_login()
    if _r:
        return _r
    cat = request.args.get('cat')
    ops = oportunidades(limit=30, categoria=cat)
    for o in ops:
        o['cat_nome'] = _nome_cat(o['categoria_id'])
    return render_template_string(
        _OPS_HTML, ops=ops, fmt=_fmt_brl, nome=session.get('mlhype_user_nome', ''),
        cat=cat, cat_nome=_nome_cat(cat) if cat else 'todo o mercado')


# ── Calculadora de Margem REAL (Lote M — ataca a dor #1: "vou ter lucro?") ─────
_CALC_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MLhype · Calculadora de Margem</title>
<style>
 :root{--bg:#0b1020;--card:#0f1730;--bd:#21304f;--mut:#8aa0c6;--txt:#e7ecf5;--ac:#7cc0ff}
 body{font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:18px}
 .wrap{max-width:560px;margin:0 auto} a{color:var(--ac);text-decoration:none}
 header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
 h1{font-size:20px;margin:0} .fire{background:linear-gradient(135deg,#2f6bff,#7c3aed);-webkit-background-clip:text;background-clip:text;color:transparent}
 .sub{color:var(--mut);font-size:13px;margin:4px 0 16px}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px;margin:12px 0}
 label{display:block;font-size:12px;color:var(--mut);margin:10px 0 4px}
 input,select{width:100%;box-sizing:border-box;background:#0b1020;color:var(--txt);border:1px solid var(--bd);border-radius:8px;padding:11px;font-size:15px}
 .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
 button{width:100%;margin-top:14px;background:linear-gradient(135deg,#2f6bff,#7c3aed);color:#fff;border:0;border-radius:8px;padding:12px;font-weight:700;font-size:16px;cursor:pointer}
 .bk{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--bd);font-size:15px} .bk:last-of-type{border:none}
 .bk .neg{color:#ff8a8a} .lucro{font-size:30px;font-weight:800;margin-top:8px} .ok{color:#5ee0a0} .ruim{color:#ff8a8a}
 .mut{color:var(--mut);font-size:12px}
</style></head><body><div class=wrap>
<header><h1>MLhype <span class=fire>🔥</span> · Calculadora</h1>
 <span class=mut><a href="/mlhype/oportunidades">💡 Dicas</a> · <a href="/mlhype/radar">Radar</a> · <a href="/mlhype/sair">sair</a></span></header>
<p class=sub>O lucro <b>de verdade</b>, com a taxa REAL do Mercado Livre — pra você nunca mais ser surpreendido pelo que cai na conta.</p>
<div class=card>
 <form method=get>
  <div class=row>
   <div><label>Preço de venda (R$)</label><input name=preco value="{{preco}}" placeholder="149,90" required></div>
   <div><label>Custo do produto (R$)</label><input name=custo value="{{custo}}" placeholder="80,00"></div>
  </div>
  <label>Categoria</label>
  <select name=cat>{% for c in cats %}<option value="{{c.id}}" {{'selected' if c.id==cat else ''}}>{{c.nome}}</option>{% endfor %}</select>
  <div class=row>
   <div><label>Frete que você paga (R$)</label><input name=frete value="{{frete}}" placeholder="0,00"></div>
   <div><label>Tipo de anúncio</label><select name=tipo><option value=classico {{'selected' if tipo=='classico' else ''}}>Clássico</option><option value=premium {{'selected' if tipo=='premium' else ''}}>Premium (parcela s/ juros)</option></select></div>
  </div>
  <button type=submit>Calcular lucro real</button>
 </form>
</div>
{% if res %}
<div class=card>
 <div class=bk><span>Preço de venda</span><span>{{fmt(res.preco)}}</span></div>
 <div class=bk><span>− Comissão do ML{% if res.comissao_pct %} ({{res.comissao_pct}}%){% endif %}{% if res.estimado %} <span class=mut>estimada</span>{% endif %}</span><span class=neg>− {{fmt(res.comissao)}}</span></div>
 {% if res.frete %}<div class=bk><span>− Frete</span><span class=neg>− {{fmt(res.frete)}}</span></div>{% endif %}
 {% if res.custo %}<div class=bk><span>− Custo do produto</span><span class=neg>− {{fmt(res.custo)}}</span></div>{% endif %}
 <div class="lucro {{'ok' if res.lucro>0 else 'ruim'}}">= {{fmt(res.lucro)}} de lucro</div>
 <div class=mut>margem {{res.margem_pct}}% sobre a venda{% if res.markup_pct is not none %} · markup {{res.markup_pct}}% sobre o custo{% endif %}{% if res.tipo=='premium' %} · anúncio Premium{% endif %}</div>
 {% if res.lucro <= 0 %}<div class=mut style="color:#ff8a8a;margin-top:8px">⚠️ No vermelho — esse preço não fecha com esse custo. Renegocie o fornecedor ou suba o preço.</div>{% endif %}
</div>
{% endif %}
</div></body></html>'''


@mlhype_bp.route('/calculadora')
def mlhype_calculadora():
    """Calculadora de Margem REAL — ataca a dor #1 (taxas comem o lucro)."""
    _r = _exige_login()
    if _r:
        return _r
    preco = request.args.get('preco')
    res = None
    if preco:
        def _num(v):
            return float((v or '0').replace('.', '').replace(',', '.')) if v else 0.0
        try:
            res = calcular_margem(_num(preco), _num(request.args.get('custo')),
                                  request.args.get('cat') or 'MLB1051',
                                  _num(request.args.get('frete')),
                                  request.args.get('tipo', 'classico'))
        except Exception:
            res = None
    cats = [{'id': k, 'nome': v} for k, v in sorted(CATEGORIAS_ML.items(), key=lambda x: x[1])]
    return render_template_string(
        _CALC_HTML, res=res, fmt=_fmt_brl, cats=cats,
        preco=preco or '', custo=request.args.get('custo') or '',
        cat=request.args.get('cat') or 'MLB1051', frete=request.args.get('frete') or '',
        tipo=request.args.get('tipo', 'classico'))


# ── A ESTEIRA: orquestrador dos agentes (Passos 6, 7, 9) ──────────────────────
def rodar_esteira(pid, cat_id=None):
    """Encadeia: produto+ofertas → Dissecador → Fornecedor BR → Avaliador →
    Ficha de Ataque. Degradação graciosa: se um agente falha, segue com o resto."""
    import mlhype_ml as ml
    import mlhype_ia as ia
    import mlhype_db as db

    res = {'pid': pid, 'cat_id': cat_id, 'erros': [], 'ia_ok': ia.ia_disponivel()}

    prod = ml.produto(pid) or {}
    nome = prod.get('name') or pid
    res['produto'] = nome

    ofertas, num_conc = [], None
    try:
        of = ml.ofertas_produto(pid, limit=50)
        ofertas = of.get('results') or []
        num_conc = (of.get('paging') or {}).get('total') or len(ofertas)
    except Exception:
        res['erros'].append('ofertas indisponíveis')
    lider = ofertas[0] if ofertas else {}
    preco_lider = lider.get('price')
    res['num_concorrentes'] = num_conc

    # anúncio do líder p/ o Dissecador — usa os dados do PRÓPRIO produto de
    # catálogo (mais ricos que /items, que hoje dá 403): fotos, atributos, descrição.
    sd = prod.get('short_description')
    descricao = sd.get('content', '') if isinstance(sd, dict) else (sd or '')
    anuncio = {
        'titulo': nome, 'preco': preco_lider, 'descricao': descricao[:500],
        'fotos_qtd': len(prod.get('pictures') or []),
        'atributos': [a.get('name') for a in (prod.get('attributes') or [])
                      if a.get('value_name')][:15],
        'garantia': lider.get('warranty'), 'frete_gratis': None,
    }
    res['anuncio_lider'] = anuncio

    # Caçador de Fornecedor BR (o MOAT) — ANTES da IA, p/ a IA usar o custo
    cat_nome = _nome_cat(cat_id) if cat_id else ''
    fornecedores = db.match_fornecedores(cat_id or prod.get('category_id') or '',
                                         cat_nome, nome, limit=5)
    res['fornecedores'] = fornecedores
    menor_custo = None
    for f in fornecedores:
        p = f.get('menor_preco')
        if p is not None:
            menor_custo = p if menor_custo is None else min(menor_custo, p)
    res['menor_custo_br'] = menor_custo
    tend = db.tendencia_produto(pid)
    res['tendencia'] = tend

    # Taxa REAL do ML p/ esse produto (margem precisa, não chute) — Lote M
    cat_taxa = cat_id or prod.get('category_id')
    taxas = ml.taxas_ml(preco_lider, cat_taxa) if preco_lider else None
    comissao_pct = (taxas.get('classico') or {}).get('pct') if taxas else None
    res['comissao_ml_pct'] = comissao_pct

    # Esteira de IA: Dissecador + Avaliador + Ficha numa ÚNICA chamada
    # (3x menos cota; Gemini com fallback p/ Groq lá dentro)
    fraquezas, avaliacao, ficha = [], {}, {}
    res['ia_falhou'] = False
    if ia.ia_disponivel():
        try:
            out = ia.analisar_esteira({
                'nome_produto': nome,
                'anuncio_lider': {'titulo': anuncio['titulo'], 'preco': preco_lider,
                                  'fotos_qtd': anuncio['fotos_qtd'],
                                  'atributos': anuncio['atributos'], 'garantia': anuncio['garantia']},
                'num_concorrentes': num_conc, 'tendencia': tend,
                'menor_preco_fornecedor_br': menor_custo, 'comissao_ml_pct': comissao_pct})
            fraquezas = out['fraquezas']
            avaliacao = out['avaliacao']
            if avaliacao.get('veredito') != 'evite':
                ficha = out['ficha']
        except Exception as e:
            res['ia_falhou'] = True
            log.warning(f'[MLhype] IA esteira falhou: {e}')

    # Margem REAL (substitui a estimativa da IA) quando há custo de fornecedor
    if menor_custo and preco_lider and avaliacao:
        m_lider = calcular_margem(preco_lider, menor_custo, cat_taxa)
        if m_lider:
            avaliacao['margem_pct'] = m_lider['margem_pct']
    if ficha and ficha.get('preco_venda_sugerido') and menor_custo:
        m_ficha = calcular_margem(ficha['preco_venda_sugerido'], menor_custo, cat_taxa)
        if m_ficha:
            ficha['margem_pct'] = m_ficha['margem_pct']
            ficha['lucro_liquido'] = m_ficha['lucro']

    res['fraquezas'] = fraquezas
    res['avaliacao'] = avaliacao
    res['ficha'] = ficha

    # persiste a oportunidade + ficha
    try:
        oid = db.salvar_oportunidade(nome, cat_id, avaliacao.get('score'),
                                     avaliacao.get('margem_pct'), avaliacao.get('veredito'), res)
        if ficha:
            db.salvar_ficha(oid, ficha)
    except Exception as e:
        res['erros'].append(f'persist: {e}')
    return res


_FICHA_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>MLhype · Ficha de Ataque</title>
<style>
 :root{--bg:#0b1020;--card:#0f1730;--bd:#21304f;--mut:#8aa0c6;--txt:#e7ecf5;--ac:#7cc0ff}
 *{box-sizing:border-box} body{font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:18px}
 .wrap{max-width:760px;margin:0 auto} a{color:var(--ac);text-decoration:none}
 h1{font-size:20px;margin:6px 0 2px} .prod{color:var(--mut);font-size:14px;margin-bottom:16px}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px 18px;margin:14px 0}
 .card h3{margin:0 0 10px;font-size:13px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut)}
 .score{display:flex;align-items:center;gap:16px}
 .scoreN{font-size:42px;font-weight:800;line-height:1}
 .vd{padding:4px 12px;border-radius:20px;font-weight:800;font-size:14px}
 .ataque{background:#062a17;color:#5ee0a0;border:1px solid #155a38}
 .observe{background:#2a2206;color:#ffd35e;border:1px solid #5a4a15}
 .evite{background:#2a0a0a;color:#ff8a8a;border:1px solid #5a1a1a}
 ul{margin:8px 0 0;padding-left:18px;line-height:1.6} li{margin:3px 0}
 .meta{color:var(--mut);font-size:13px;margin-top:8px}
 .ataque-card{border:2px solid #7c3aed;background:#120f24}
 .titulo-ot{font-size:18px;font-weight:800;color:#fff;line-height:1.3;margin:0 0 4px}
 .chars{color:var(--mut);font-size:12px;margin-bottom:12px}
 .preco-row{display:flex;gap:16px;flex-wrap:wrap;margin:10px 0}
 .pbox{background:#0f1730;border:1px solid #21304f;border-radius:8px;padding:8px 14px} .pbox span{display:block;color:var(--mut);font-size:11px} .pbox b{font-size:18px;color:#5ee0a0}
 .dif{background:#1c1340;border:1px solid #4a2da0;border-radius:8px;padding:10px 14px;margin-top:8px;color:#cbbaf5;font-weight:600}
 .forn{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--bd);font-size:14px} .forn:last-child{border:none}
 .warn{background:#2a2206;color:#ffd35e;border:1px solid #5a4a15;border-radius:8px;padding:10px 14px;font-size:13px}
 .empty{color:var(--mut);font-size:14px}
</style></head><body><div class=wrap>
<a href="/mlhype/radar{{ '?cat=' ~ r.cat_id if r.cat_id else '' }}">← voltar ao Radar</a>
<h1>⚡ Ficha de Ataque</h1>
<div class=prod>{{r.produto}}</div>

{% if not r.ia_ok %}<div class=warn>⚙️ A IA não está configurada neste ambiente — aqui aparecem os dados de mercado e o fornecedor.</div>{% endif %}
{% if r.ia_falhou %}<div class=warn>⚙️ A IA está sobrecarregada agora (cota estourada) — os dados de mercado e o fornecedor abaixo já estão prontos. Tente gerar a Ficha de novo em alguns minutos.</div>{% endif %}

{% if r.avaliacao and r.avaliacao.veredito %}
<div class=card>
 <h3>Avaliador de Oportunidade</h3>
 <div class=score>
  <div class=scoreN>{{r.avaliacao.score}}<span style="font-size:18px;color:#8aa0c6">/100</span></div>
  <span class="vd {{r.avaliacao.veredito}}">{{r.avaliacao.veredito|upper}}</span>
  {% if r.avaliacao.margem_pct is not none %}<span class=meta>margem ~{{r.avaliacao.margem_pct}}%</span>{% endif %}
 </div>
 {% if r.avaliacao.porque %}<div class=meta>{{r.avaliacao.porque}}</div>{% endif %}
 {% if r.avaliacao.como_melhorar %}<ul>{% for c in r.avaliacao.como_melhorar %}<li>{{c}}</li>{% endfor %}</ul>{% endif %}
 <div class=meta>{{r.num_concorrentes or '—'}} concorrentes · tendência: {{r.tendencia}}{% if r.comissao_ml_pct %} · taxa ML ~{{r.comissao_ml_pct}}%{% endif %}</div>
 {% if r.comissao_ml_pct and not r.menor_custo_br %}<div class=meta>💡 Cadastre o custo do fornecedor pra ver o <b>lucro líquido real</b> (a taxa do ML já está descontada).</div>{% endif %}
</div>
{% endif %}

<div class=card>
 <h3>Dissecador do Líder — fraquezas a explorar</h3>
 <div class=meta>Líder: {{r.anuncio_lider.titulo}} · {{fmt(r.anuncio_lider.preco)}}{% if r.anuncio_lider.fotos_qtd is not none %} · {{r.anuncio_lider.fotos_qtd}} fotos{% endif %}</div>
 {% if r.fraquezas %}<ul>{% for f in r.fraquezas %}<li>{{f}}</li>{% endfor %}</ul>
 {% else %}<div class=empty>{% if r.ia_falhou %}A IA está sobrecarregada — tente de novo em instantes.{% else %}—{% endif %}</div>{% endif %}
</div>

<div class=card>
 <h3>🎯 Caçador de Fornecedor BR (o diferencial)</h3>
 {% if not r.pode_fornecedor %}
   <div class=warn>🔒 O Caçador de Fornecedor BR é exclusivo do plano <b>Business</b> — é o moat que ninguém mais tem. <a href="/mlhype/planos" style="color:#7cc0ff;font-weight:700">Ver planos →</a></div>
 {% elif r.fornecedores %}
  {% for f in r.fornecedores %}
  <div class=forn><span><b>{{f.nome}}</b>{% if f.uf %} · {{f.uf}}{% endif %}{% if f.contato %} · {{f.contato}}{% endif %}{% if f.whatsapp %} · {{f.whatsapp}}{% endif %}</span><span>{{fmt(f.menor_preco)}}</span></div>
  {% endfor %}
 {% else %}<div class=empty>Nenhum fornecedor cadastrado p/ esta categoria ainda. Cadastre os seus em <b>/mlhype/admin/fornecedores</b> — é aqui que mora o moat.</div>{% endif %}
</div>

{% if r.ficha and r.ficha.titulo_otimizado %}
<div class="card ataque-card">
 <h3 style="color:#b9a6ff">🔥 Ficha de Ataque — anúncio pronto pra publicar</h3>
 <p class=titulo-ot>{{r.ficha.titulo_otimizado}}</p>
 <div class=chars>{{r.ficha.titulo_otimizado|length}}/60 caracteres</div>
 {% if r.ficha.bullets %}<ul>{% for b in r.ficha.bullets %}<li>{{b}}</li>{% endfor %}</ul>{% endif %}
 <div class=preco-row>
  <div class=pbox><span>Preço de venda sugerido</span><b>{{fmt(r.ficha.preco_venda_sugerido)}}</b></div>
  {% if r.ficha.lucro_liquido is not none %}<div class=pbox><span>Lucro líquido/un.</span><b>{{fmt(r.ficha.lucro_liquido)}}</b></div>{% endif %}
  {% if r.ficha.margem_pct is not none %}<div class=pbox><span>Margem real</span><b>{{r.ficha.margem_pct}}%</b></div>{% endif %}
 </div>
 {% if r.ficha.diferencial_ataque %}<div class=dif>⚔️ Diferencial de ataque: {{r.ficha.diferencial_ataque}}</div>{% endif %}
</div>
{% elif r.avaliacao.veredito == 'evite' %}
<div class=card><div class=warn>O Avaliador marcou <b>EVITE</b> — a esteira parou aqui pra te poupar de uma cilada.</div></div>
{% endif %}
</div></body></html>'''


_ANALISANDO_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MLhype · Analisando…</title>
<style>
 body{font-family:system-ui,Segoe UI,sans-serif;background:#0b1020;color:#e7ecf5;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;text-align:center}
 .box{max-width:420px} .sp{width:54px;height:54px;border:4px solid #21304f;border-top-color:#7c3aed;border-radius:50%;margin:0 auto 18px;animation:spin 1s linear infinite}
 @keyframes spin{to{transform:rotate(360deg)}}
 h1{font-size:19px;margin:0 0 6px} p{color:#9fb0d0;font-size:14px;line-height:1.5} .step{color:#7cc0ff;font-weight:600} a{color:#7cc0ff;text-decoration:none}
</style></head><body><div class=box id=box>
 <div class=sp></div>
 <h1>🔍 Analisando…</h1>
 <p>Rodando os 5 agentes: <span class=step id=step>dissecando o líder</span><br>pode levar alguns segundos.</p>
</div>
<script>
 var steps=['dissecando o líder','caçando fornecedor BR','avaliando a oportunidade','montando a Ficha de Ataque'];
 var i=0; setInterval(function(){i=(i+1)%steps.length;var s=document.getElementById('step');if(s){s.textContent=steps[i];}},2200);
 var ctrl=new AbortController(); var to=setTimeout(function(){ctrl.abort();},75000);
 fetch('/mlhype/analisar/{{pid}}/run{{q|safe}}',{signal:ctrl.signal})
  .then(function(r){return r.text();})
  .then(function(html){clearTimeout(to);document.open();document.write(html);document.close();})
  .catch(function(e){document.getElementById('box').innerHTML='<h1>Ops…</h1><p>A análise demorou demais ou falhou. <a href="/mlhype/radar">voltar ao Radar</a> e tentar de novo.</p>';});
</script></body></html>'''


@mlhype_bp.route('/analisar/<pid>')
def mlhype_analisar(pid):
    """Tela de 'analisando…' instantânea; o trabalho pesado roda no /run via fetch."""
    _r = _exige_login()
    if _r:
        return _r
    cat = request.args.get('cat')
    q = ('?cat=' + cat) if cat else ''
    return render_template_string(_ANALISANDO_HTML, pid=pid, q=q)


@mlhype_bp.route('/analisar/<pid>/run')
def mlhype_analisar_run(pid):
    """Roda a esteira de fato e devolve o HTML da Ficha (chamado via fetch)."""
    _r = _exige_login()
    if _r:
        return _r
    u = _usuario_atual()
    plano = plano_efetivo(u)
    cat = request.args.get('cat')
    if not plano_libera(plano, 'ficha') and contar_buscas_hoje(u['id']) >= FREE_BUSCAS_DIA:
        return render_template_string(_LIMITE_HTML, limite=FREE_BUSCAS_DIA, plano=plano)
    registrar_uso(u['id'], 'busca', categoria=cat)
    try:
        r = rodar_esteira(pid, cat)
    except Exception as e:
        log.error(f'[MLhype] esteira {pid} erro: {e}')
        return ('<div style="font-family:system-ui;color:#e7ecf5;background:#0b1020;min-height:100vh;'
                'padding:40px;text-align:center">Erro ao analisar este produto.<br><br>'
                f'<a href="/mlhype/radar" style="color:#7cc0ff">← voltar ao Radar</a></div>'), 500
    r['pode_fornecedor'] = plano_libera(plano, 'fornecedor')
    return render_template_string(_FICHA_HTML, r=r, fmt=_fmt_brl)


# ── Admin de Fornecedores (Passo 8) — gate simples por ML_SECRET ──────────────
_ADMIN_FORN_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MLhype · Fornecedores</title>
<style>
 :root{--bg:#0b1020;--card:#0f1730;--bd:#21304f;--mut:#8aa0c6;--txt:#e7ecf5;--ac:#7cc0ff}
 body{font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:18px}
 .wrap{max-width:820px;margin:0 auto} a{color:var(--ac);text-decoration:none} h1{font-size:20px}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin:14px 0}
 label{display:block;font-size:12px;color:var(--mut);margin:8px 0 3px}
 input{width:100%;background:#0b1020;color:var(--txt);border:1px solid var(--bd);border-radius:8px;padding:9px 11px;font-size:14px}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
 button{margin-top:12px;background:linear-gradient(135deg,#2f6bff,#7c3aed);color:#fff;border:0;border-radius:8px;padding:11px 20px;font-weight:700;font-size:15px;cursor:pointer}
 table{width:100%;border-collapse:collapse;font-size:14px} td,th{padding:8px;border-bottom:1px solid var(--bd);text-align:left} th{color:var(--mut);font-size:12px}
 .hint{color:var(--mut);font-size:12px}
</style></head><body><div class=wrap>
<a href="/mlhype/radar">← Radar</a>
<h1>🎯 Fornecedores nacionais — o moat</h1>
<p class=hint>Cada fornecedor cadastrado aqui aparece nas Fichas de Ataque das categorias que ele atende. Em <b>categorias</b>, liste IDs (ex.: MLB1051) e palavras-chave separadas por espaço.</p>
<div class=card>
 <form method=post action="/mlhype/admin/fornecedores?k={{k}}">
  <div class=grid>
   <div><label>Nome *</label><input name=nome required></div>
   <div><label>Categorias atendidas (IDs + palavras)</label><input name=categorias placeholder="MLB1051 celular fone capa"></div>
   <div><label>Contato (email/site)</label><input name=contato></div>
   <div><label>WhatsApp</label><input name=whatsapp></div>
   <div><label>UF</label><input name=uf placeholder=SP></div>
   <div><label>Palavra-chave do preço</label><input name=keyword placeholder=celular></div>
   <div><label>Preço de custo (R$)</label><input name=preco placeholder=450,00></div>
   <div><label>Prazo (dias)</label><input name=prazo placeholder=7></div>
   <div><label>Garantia</label><input name=garantia placeholder="3 meses"></div>
   <div><label>Obs</label><input name=obs></div>
  </div>
  <button type=submit>+ Cadastrar fornecedor</button>
 </form>
</div>
<div class=card>
 <h3 style="color:var(--mut);font-size:13px;text-transform:uppercase">Cadastrados ({{fornes|length}})</h3>
 <table><tr><th>Nome</th><th>Categorias</th><th>UF</th><th>Menor custo</th><th>Contato</th></tr>
 {% for f in fornes %}<tr><td><b>{{f.nome}}</b></td><td class=hint>{{f.categorias}}</td><td>{{f.uf}}</td><td>{{fmt(f.menor_preco)}}</td><td class=hint>{{f.contato}}{% if f.whatsapp %} · {{f.whatsapp}}{% endif %}</td></tr>{% endfor %}
 </table>
</div>
</div></body></html>'''


@mlhype_bp.route('/admin/fornecedores', methods=['GET', 'POST'])
def mlhype_admin_fornecedores():
    if request.args.get('k') != os.environ.get('ML_SECRET'):
        return 'acesso negado — use ?k=<ML_SECRET>', 403
    import mlhype_db as db
    if request.method == 'POST':
        nome = (request.form.get('nome') or '').strip()
        if nome:
            sid = db.add_fornecedor(
                nome, categorias=request.form.get('categorias', ''),
                contato=request.form.get('contato', ''), whatsapp=request.form.get('whatsapp', ''),
                uf=request.form.get('uf', ''), obs=request.form.get('obs', ''))
            preco = (request.form.get('preco') or '').strip().replace('.', '').replace(',', '.')
            if preco:
                try:
                    db.add_fornecedor_preco(
                        sid, request.form.get('keyword') or nome, float(preco),
                        prazo_entrega_dias=int(request.form.get('prazo') or 0) or None,
                        garantia=request.form.get('garantia', ''))
                except Exception:
                    pass
        return redirect(f"/mlhype/admin/fornecedores?k={request.args.get('k')}")
    return render_template_string(_ADMIN_FORN_HTML, fornes=db.listar_fornecedores(),
                                  k=request.args.get('k'), fmt=_fmt_brl)


# ── Planos / upsell / admin de plano (Passo 5 — sem o PIX ao vivo ainda) ──────
_PLANOS_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MLhype · Planos</title>
<style>
 :root{--bg:#0b1020;--card:#0f1730;--bd:#21304f;--mut:#8aa0c6;--txt:#e7ecf5}
 body{font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:18px}
 .wrap{max-width:920px;margin:0 auto} a{color:#7cc0ff;text-decoration:none}
 h1{font-size:22px;text-align:center;margin:8px 0 2px} .sub{text-align:center;color:var(--mut);margin:0 0 20px;font-size:14px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}
 .p{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px;display:flex;flex-direction:column}
 .p.cur{border:2px solid #7c3aed}
 .pn{font-weight:800;font-size:18px} .pp{font-size:30px;font-weight:800;margin:6px 0} .pp small{font-size:13px;color:var(--mut);font-weight:400}
 .pd{color:var(--mut);font-size:13px;min-height:34px}
 ul{list-style:none;padding:0;margin:12px 0;font-size:14px} li{padding:4px 0;color:#cfe0ff} li::before{content:"✓ ";color:#5ee0a0;font-weight:800}
 .btn{margin-top:auto;display:block;text-align:center;background:linear-gradient(135deg,#2f6bff,#7c3aed);color:#fff;padding:11px;border-radius:8px;font-weight:700}
 .btn.cur{background:#0b1020;border:1px solid #2f5e44;color:#5ee0a0} .badge{font-size:11px;color:#5ee0a0}
</style></head><body><div class=wrap>
<div style="text-align:center"><a href="/mlhype/radar">← Radar</a></div>
<h1>Planos do MLhype 🔥</h1>
<p class=sub>O custo da assinatura é uma fração do lucro de um único produto vencedor.</p>
<div class=grid>
{% for c in cards %}
 <div class="p {{'cur' if c.key==atual else ''}}">
  <div class=pn>{{c.nome}}{% if c.key==atual %} <span class=badge>· seu plano</span>{% endif %}</div>
  <div class=pp>{{c.preco_fmt}}<small>{% if c.preco %}/mês{% endif %}</small></div>
  <div class=pd>{{c.desc}}</div>
  <ul>{% for f in c.features %}<li>{{f}}</li>{% endfor %}</ul>
  {% if c.key==atual %}<span class="btn cur">Plano atual</span>
  {% elif c.key=='free' %}<a class=btn href="/mlhype/cadastrar">Começar grátis</a>
  {% elif not logado %}<a class=btn href="/mlhype/cadastrar">Criar conta</a>
  {% else %}<a class=btn href="/mlhype/assinar/{{c.key}}">Assinar</a>{% endif %}
 </div>
{% endfor %}
</div></div></body></html>'''

_ORDEM_PLANOS = ['free', 'starter', 'pro', 'business']


@mlhype_bp.route('/planos')
def mlhype_planos():
    u = _usuario_atual()
    atual = plano_efetivo(u) if u else None
    cards = [{'key': k, **PLANOS[k]} for k in _ORDEM_PLANOS]
    return render_template_string(_PLANOS_HTML, cards=cards, atual=atual, logado=bool(u))


_MINI_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MLhype</title>
<style>body{font-family:system-ui,Segoe UI,sans-serif;background:#0b1020;color:#e7ecf5;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;text-align:center}
 .box{max-width:460px} h1{font-size:22px} p{color:#9fb0d0;line-height:1.6} a{color:#7cc0ff;text-decoration:none}
 .btn{display:inline-block;margin-top:14px;background:linear-gradient(135deg,#2f6bff,#7c3aed);color:#fff;padding:12px 24px;border-radius:8px;font-weight:700}
 .card{background:#0f1730;border:1px solid #21304f;border-radius:12px;padding:18px;margin:16px 0}</style>
</head><body><div class=box>{{corpo|safe}}</div></body></html>'''


@mlhype_bp.route('/assinar/<plano>')
def mlhype_assinar(plano):
    _r = _exige_login()
    if _r:
        return _r
    if plano not in PLANOS or plano == 'free':
        return redirect('/mlhype/planos')
    p = PLANOS[plano]
    corpo = (f'<a href="/mlhype/planos">← planos</a><h1>Assinar {p["nome"]} · {p["preco_fmt"]}/mês</h1>'
             '<div class=card><p>O pagamento automático via <b>PIX</b> está em ativação final '
             '(último fio: ligar o Asaas no webhook compartilhado, com teste real).</p>'
             '<p style="color:#9fb0d0">Enquanto isso, dá pra liberar tua conta de teste pelo admin.</p></div>'
             '<a class=btn href="/mlhype/radar">voltar ao Radar</a>')
    return render_template_string(_MINI_HTML, corpo=corpo)


_LIMITE_HTML = '''<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MLhype · Limite</title>
<style>body{font-family:system-ui,Segoe UI,sans-serif;background:#0b1020;color:#e7ecf5;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;text-align:center}
 .box{max-width:460px} h1{font-size:22px} p{color:#9fb0d0;line-height:1.6} a{color:#7cc0ff;text-decoration:none}
 .btn{display:inline-block;margin-top:14px;background:linear-gradient(135deg,#2f6bff,#7c3aed);color:#fff;padding:12px 24px;border-radius:8px;font-weight:700}</style>
</head><body><div class=box>
<h1>Você usou suas {{limite}} análises grátis de hoje 🚀</h1>
<p>Gostou? No <b>Pro</b> a Ficha de Ataque é ilimitada — e no <b>Business</b> você ainda recebe o <b>fornecedor nacional</b> pra desbancar o líder.</p>
<a class=btn href="/mlhype/planos">Ver planos →</a>
<div style="margin-top:12px"><a href="/mlhype/radar">voltar ao Radar</a></div>
</div></body></html>'''


@mlhype_bp.route('/admin/plano')
def mlhype_admin_plano():
    """Define o plano de uma conta (p/ TESTE de cada tier). Gate por ML_SECRET."""
    if request.args.get('k') != os.environ.get('ML_SECRET'):
        return 'acesso negado — use ?k=<ML_SECRET>&email=<email>&plano=<free|starter|pro|business>', 403
    email = (request.args.get('email') or '').strip().lower()
    plano = (request.args.get('plano') or '').strip()
    if plano not in PLANOS:
        return 'plano inválido — use free | starter | pro | business', 400
    u = usuario_por_email(email)
    if not u:
        return f'conta não encontrada: {email}', 404
    conn = get_mlhype_db()
    conn.execute('UPDATE mlhype_users SET plano=?, plan_active=? WHERE id=?',
                 (plano, 0 if plano == 'free' else 1, u['id']))
    conn.commit(); conn.close()
    return f'✅ {email} agora é plano <b>{plano}</b>. <a href="/mlhype/radar">ir pro Radar →</a>'


def _seed_fornecedores_exemplo():
    """Semeia 2 fornecedores de EXEMPLO (claramente marcados) só pra a Ficha
    mostrar o formato do moat. Diogo substitui pelos reais no /admin."""
    try:
        import mlhype_db as db
        if db.listar_fornecedores():
            return
        s1 = db.add_fornecedor('Fornecedor Exemplo — Eletrônicos (edite no admin)',
                               categorias='MLB1051 MLB1000 celular fone carregador capa eletronicos',
                               uf='SP', contato='exemplo@fornecedor.com.br', fonte='exemplo',
                               obs='EXEMPLO — apague e cadastre os reais')
        db.add_fornecedor_preco(s1, 'celular', 480.0, prazo_entrega_dias=7, garantia='3 meses')
        db.add_fornecedor_preco(s1, 'fone', 16.0, prazo_entrega_dias=7)
        s2 = db.add_fornecedor('Fornecedor Exemplo — Casa & Eletro (edite no admin)',
                               categorias='MLB5726 MLB1574 liquidificador chaleira fritadeira eletrodomestico casa',
                               uf='SC', contato='exemplo2@fornecedor.com.br', fonte='exemplo',
                               obs='EXEMPLO — apague e cadastre os reais')
        db.add_fornecedor_preco(s2, 'eletrodomestico', 42.0, prazo_entrega_dias=5)
        log.info('[MLhype] fornecedores de exemplo semeados')
    except Exception as e:
        log.warning(f'[MLhype] seed fornecedores falhou: {e}')


# ── Coletor diário multi-categoria (Passo 3) ──────────────────────────────────
_COLETOR_INICIADO = False


def _data_brt():
    """Data de hoje no fuso BRT (UTC-3), 'YYYY-MM-DD' — chave do snapshot."""
    from datetime import datetime, timedelta
    return (datetime.utcnow() - timedelta(hours=3)).strftime('%Y-%m-%d')


# Categorias-raiz que NÃO têm "mais vendidos"/highlights (não são catálogo de
# best-seller): Carros/Motos, Imóveis, Ingressos, Serviços. Pulamos no scan.
_CAT_SEM_HIGHLIGHTS = {'MLB1743', 'MLB1459', 'MLB218519', 'MLB1540'}


def _categorias_alvo(ml):
    """Categorias a varrer: TARGET_CATEGORIES (lista CSV) ou TODAS as raízes do ML."""
    cfg = os.environ.get('TARGET_CATEGORIES', '').strip()
    if cfg and cfg.lower() not in ('todas', 'all', '*'):
        return [c.strip() for c in cfg.split(',') if c.strip()]
    try:
        return [c['id'] for c in ml.categorias() if c['id'] not in _CAT_SEM_HIGHLIGHTS]
    except Exception as e:
        log.error(f'[MLhype] não consegui listar categorias-raiz: {e}')
        return []


def _coletar_produto(ml, db, pid, cat_id, posicao, hoje):
    """Grava o snapshot do dia de UM produto de catálogo (idempotente)."""
    # nome: só busca /products/{id} se ainda não temos o título do produto
    conn = db.get_mlhype_db()
    row = conn.execute('SELECT titulo FROM mlhype_listings WHERE mlb_item_id=?', (pid,)).fetchone()
    conn.close()
    nome = row['titulo'] if (row and row['titulo']) else None
    if not nome:
        try:
            nome = (ml.produto(pid) or {}).get('name')
        except Exception:
            nome = None
    lid = db.upsert_listing(pid, titulo=nome, categoria_id=cat_id)

    # preço do líder (1ª oferta = vencedora do Buy Box) + nº de concorrentes
    preco = None
    num_ofertas = None
    try:
        of = ml.ofertas_produto(pid, limit=50)
        results = of.get('results') or []
        num_ofertas = (of.get('paging') or {}).get('total') or len(results) or None
        if results and isinstance(results[0].get('price'), (int, float)):
            preco = results[0]['price']
    except Exception:
        pass

    db.gravar_snapshot(lid, hoje, categoria_id=cat_id, preco=preco,
                       posicao_ranking=posicao, num_ofertas=num_ofertas)


def _coletar_trends(ml, db, hoje):
    """Termos em alta do dia (idempotente: limpa os de hoje e regrava)."""
    tr = ml.trends() or []
    conn = db.get_mlhype_db()
    conn.execute("DELETE FROM mlhype_trends WHERE collected_at=? AND categoria_id IS NULL", (hoje,))
    for i, t in enumerate(tr, start=1):
        kw = t.get('keyword') if isinstance(t, dict) else str(t)
        if kw:
            conn.execute('INSERT INTO mlhype_trends (categoria_id, termo, posicao, collected_at) '
                         'VALUES (NULL,?,?,?)', (kw, i, hoje))
    conn.commit()
    conn.close()


def coletar(categorias=None, top_n=None):
    """Uma varredura: para cada categoria-alvo pega o Top (highlights), o
    preço/concorrência dos líderes e grava snapshot datado. Idempotente."""
    import mlhype_ml as ml
    import mlhype_db as db
    if not ml.credenciais_ok():
        log.warning('[MLhype] coleta abortada: sem ML_APP_ID/ML_SECRET')
        return {'erro': 'sem credenciais'}
    top_n = top_n or int(os.environ.get('MLHYPE_TOP_N', '20'))
    cats = categorias or _categorias_alvo(ml)
    hoje = _data_brt()
    res = {'data': hoje, 'categorias': 0, 'produtos': 0, 'erros': 0}
    for cat_id in cats:
        try:
            content = (ml.highlights(cat_id).get('content') or [])[:top_n]
        except Exception as e:
            log.warning(f'[MLhype] highlights {cat_id} falhou: {e}')
            res['erros'] += 1
            continue
        res['categorias'] += 1
        for item in content:
            if item.get('type') != 'PRODUCT':
                continue
            try:
                _coletar_produto(ml, db, item['id'], cat_id, item.get('position'), hoje)
                res['produtos'] += 1
            except Exception as e:
                log.warning(f"[MLhype] produto {item.get('id')} falhou: {e}")
                res['erros'] += 1
    try:
        _coletar_trends(ml, db, hoje)
    except Exception as e:
        log.warning(f'[MLhype] trends falhou: {e}')
    log.info(f'[MLhype] coleta {hoje}: {res}')
    return res


def iniciar_coletor_mlhype():
    """Thread daemon que coleta o ML periodicamente (idempotente), no próprio
    web service (padrão do Radar). Desligável via MLHYPE_AUTO_COLETA=0;
    intervalo via MLHYPE_COLETA_HORAS (default 24)."""
    global _COLETOR_INICIADO
    if _COLETOR_INICIADO:
        return
    if os.environ.get('MLHYPE_AUTO_COLETA', '1') == '0':
        log.info('[MLhype] Auto-coleta desativada (MLHYPE_AUTO_COLETA=0)')
        return
    try:
        import mlhype_ml as ml
        if not ml.credenciais_ok():
            log.info('[MLhype] Sem ML_APP_ID/ML_SECRET — coletor não inicia (ligo quando as vars existirem)')
            return
    except Exception as e:
        log.warning(f'[MLhype] coletor não pôde iniciar: {e}')
        return
    _COLETOR_INICIADO = True
    horas = int(os.environ.get('MLHYPE_COLETA_HORAS', '24'))

    import time as _time
    import random as _random
    import threading as _threading

    def _loop():
        _time.sleep(120 + _random.randint(0, 120))   # jitter no boot (multi-worker safe)
        while True:
            try:
                coletar()
            except Exception as ex:
                log.error(f'[MLhype] auto-coleta erro: {ex}')
            _time.sleep(horas * 3600)

    _threading.Thread(target=_loop, daemon=True, name='mlhype-coletor').start()
    log.info(f'[MLhype] Auto-coleta iniciada (a cada {horas}h)')


# Garante que o banco exista mesmo se o módulo for importado isoladamente.
try:
    init_mlhype_db()
    _seed_fornecedores_exemplo()
except Exception as _e:  # pragma: no cover
    log.warning(f'[MLHYPE] init_mlhype_db no import falhou: {_e}')
