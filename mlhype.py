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

from flask import Blueprint, jsonify, request, render_template_string

from mlhype_db import (init_mlhype_db, estatisticas,
                       radar_top, radar_trends, radar_categorias_com_dados)

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


@mlhype_bp.route('/')
def mlhype_home():
    """Landing provisória do MVP (substituída pelo painel real no Passo 4)."""
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
</style></head><body>
<div class="card">
 <h1>MLhype <span class="tag">🔥</span></h1>
 <p>Inteligência para quem vende no <b>Mercado Livre</b>.</p>
 <div class="bussola">"Descubra o que bomba, ache o fornecedor NO BRASIL e roube a
   venda do líder — anunciando melhor e mais barato."</div>
 <a class="btn" href="/mlhype/radar">📡 Abrir o Radar de Demanda →</a>
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


# ── Painel do Radar de Demanda (Passo 4) ──────────────────────────────────────
def _fmt_brl(v):
    if v is None:
        return '—'
    try:
        return 'R$ ' + f'{float(v):,.2f}'.replace(',', '#').replace('.', ',').replace('#', '.')
    except Exception:
        return '—'


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
 aside{width:240px;min-width:220px;background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px}
 aside h3{margin:0 0 10px;font-size:13px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut)}
 .trend{display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--bd);font-size:14px} .trend:last-child{border:none} .trend b{color:var(--ac);width:22px;text-align:right}
 .empty{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:30px;text-align:center;color:var(--mut)}
</style></head><body><div class=wrap>
<header>
 <h1>MLhype <span class=fire>🔥</span> · Radar de Demanda</h1>
 <a href="/mlhype/">← início</a>
</header>
{% if seletor %}
<form method=get><select name=cat onchange="this.form.submit()">
 {% for c in seletor %}<option value="{{c.id}}" {{'selected' if c.id==cat else ''}}>{{c.nome}} ({{c.n}})</option>{% endfor %}
</select></form>
<div class=sub>Top de <b>{{cat_nome}}</b>{% if data %} · coletado em {{data}}{% endif %} · {{linhas|length}} produtos no radar</div>
<div class=layout>
 <div class=main>
 {% if linhas %}
 <table><tr><th>#</th><th>Produto</th><th>Preço líder</th><th>Concorrentes</th></tr>
 {% for r in linhas %}
 <tr>
  <td class=pos>{{r.pos}}{% if r.delta_pos and r.delta_pos>0 %} <span class=up title="subiu {{r.delta_pos}}">▲</span>{% elif r.delta_pos and r.delta_pos<0 %} <span class=down title="caiu">▼</span>{% endif %}</td>
  <td class=prod>{{r.titulo or r.mlb_item_id}}</td>
  <td class=preco>{{fmt(r.preco)}}{% if r.delta_preco and r.delta_preco<0 %} <span class=up>↓</span>{% elif r.delta_preco and r.delta_preco>0 %} <span class=down>↑</span>{% endif %}</td>
  <td><b>{{r.num_ofertas if r.num_ofertas is not none else '—'}}</b>{% if r.brecha %} <span class=brecha>🎯 brecha</span>{% endif %}</td>
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
        data=data, linhas=linhas, seletor=seletor, trends=radar_trends(25), fmt=_fmt_brl)


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
except Exception as _e:  # pragma: no cover
    log.warning(f'[MLHYPE] init_mlhype_db no import falhou: {_e}')
