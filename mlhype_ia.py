"""
mlhype_ia.py — A esteira de agentes de IA do MLhype (Passos 6, 7, 9).

3 agentes em sequência, coordenados pelo orquestrador (em mlhype.py):
  2. Dissecador do Líder      — IA: acha as fraquezas do anúncio campeão.
  4. Avaliador de Oportunidade — IA+cálculo: nota 0-100 + veredito (atacar/evitar).
  5. Gerador da Ficha de Ataque — IA/copy: o anúncio pronto pra roubar a venda.

IA = API do Gemini (mesmo padrão REST do drzap/radar). Degrada graciosamente
se GEMINI_API_KEY não estiver setada (retorna erro claro, não quebra).
Toda saída de IA é validada (schema mínimo) com 1 retry se vier malformada.
"""
import os
import json
import logging

import requests

log = logging.getLogger(__name__)

GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


def ia_disponivel():
    return bool(GEMINI_KEY)


def _gemini_json(system, payload, max_tokens=2048, temperature=0.3, _retry=True):
    """Chama o Gemini forçando saída JSON e devolve um dict. Levanta RuntimeError
    se a IA não estiver configurada ou a resposta não for JSON válido (após 1 retry)."""
    if not GEMINI_KEY:
        raise RuntimeError('IA não configurada (defina GEMINI_API_KEY)')
    gen = {
        'temperature': temperature,
        'maxOutputTokens': max_tokens,
        'responseMimeType': 'application/json',
    }
    # gemini-2.5-* "pensa" por padrão e fica LENTO; p/ tarefas estruturadas (JSON)
    # desligamos o thinking — corta o tempo de ~20s p/ ~3s por chamada.
    if '2.5' in GEMINI_MODEL:
        gen['thinkingConfig'] = {'thinkingBudget': 0}
    body = {
        'system_instruction': {'parts': [{'text': system}]},
        'contents': [{'role': 'user', 'parts': [{'text': json.dumps(payload, ensure_ascii=False)}]}],
        'generationConfig': gen,
    }
    r = requests.post(_GEMINI_URL.format(model=GEMINI_MODEL),
                      params={'key': GEMINI_KEY}, json=body, timeout=35)
    if r.status_code != 200:
        if _retry:
            return _gemini_json(system, payload, max_tokens, temperature, _retry=False)
        raise RuntimeError(f'Gemini HTTP {r.status_code}: {r.text[:200]}')
    try:
        txt = r.json()['candidates'][0]['content']['parts'][0]['text']
        return json.loads(txt)
    except (KeyError, IndexError, ValueError) as e:
        if _retry:
            return _gemini_json(system, payload, max_tokens, temperature, _retry=False)
        raise RuntimeError(f'Gemini devolveu JSON inválido: {e}')


# ── Agente 2 — Dissecador do Líder ─────────────────────────────────────────────
_SYS_DISSECADOR = '''Você é um analista de anúncios do Mercado Livre Brasil. Recebe
o anúncio campeão de uma categoria e identifica as fraquezas que um concorrente
pode explorar para vender mais.
ENTRADA (JSON): titulo, preco, descricao, fotos_qtd, atributos, garantia, frete_gratis.
TAREFA: aponte de 3 a 6 fraquezas CONCRETAS e ACIONÁVEIS (título mal otimizado /
poucas fotos / falta de garantia ou kit / preço com gordura / descrição pobre /
atributos faltando / sem frete grátis, etc.).
SAÍDA: responda SOMENTE com JSON válido: {"fraquezas": ["<fraqueza acionável 1>", "..."]}
Não invente dados que não recebeu. Cada fraqueza deve ser específica, não genérica.'''


def dissecar_lider(anuncio):
    """anuncio: dict com titulo, preco, descricao, fotos_qtd, atributos, garantia,
    frete_gratis. Retorna {'fraquezas': [...]}."""
    out = _gemini_json(_SYS_DISSECADOR, anuncio, max_tokens=1024, temperature=0.4)
    fr = out.get('fraquezas') if isinstance(out, dict) else None
    if not isinstance(fr, list) or not fr:
        return {'fraquezas': []}
    return {'fraquezas': [str(x) for x in fr][:6]}


# ── Agente 4 — Avaliador de Oportunidade ───────────────────────────────────────
_SYS_AVALIADOR = '''Você é um analista FRIO de oportunidades de venda no Mercado
Livre Brasil. Sua função é dar uma nota de viabilidade e impedir que o vendedor
entre numa cilada.
ENTRADA (JSON): nome_produto, ranking_lider (posição no Top da categoria, 1=topo),
preco_lider, num_concorrentes, tendencia (subindo|estavel|caindo|desconhecida),
menor_preco_fornecedor_br (pode ser null se não houver fornecedor cadastrado).
CRITÉRIOS: demanda real e onda subindo? margem possível? (preco_lider vs custo do
fornecedor, descontando ~15% de taxas do ML e uma estimativa de frete — informe a
premissa) concorrência batível? (muitos concorrentes = guerra de preço).
Penalize forte: margem fina, tendência caindo, mercado lotado, ausência de fornecedor.
Se faltar dado crítico (ex.: sem fornecedor), REDUZA o score e diga no "porque".
SAÍDA: responda SOMENTE com JSON válido:
{"score": <0-100>, "margem_pct": <número>, "veredito": "ataque"|"observe"|"evite",
 "porque": "<1 frase objetiva>", "como_melhorar": ["<ação 1>","<ação 2>","<ação 3>"]}'''


def avaliar_oportunidade(dados):
    """dados: nome_produto, ranking_lider, preco_lider, num_concorrentes,
    tendencia, menor_preco_fornecedor_br. Retorna o JSON do veredito."""
    out = _gemini_json(_SYS_AVALIADOR, dados, max_tokens=1024, temperature=0.2)
    if not isinstance(out, dict):
        out = {}
    veredito = out.get('veredito')
    if veredito not in ('ataque', 'observe', 'evite'):
        out['veredito'] = 'observe'
    try:
        out['score'] = max(0, min(100, int(out.get('score', 0))))
    except Exception:
        out['score'] = 0
    out.setdefault('margem_pct', None)
    out.setdefault('porque', '')
    if not isinstance(out.get('como_melhorar'), list):
        out['como_melhorar'] = []
    return out


# ── Agente 5 — Gerador da Ficha de Ataque ──────────────────────────────────────
_SYS_FICHA = '''Você é um copywriter especialista em anúncios campeões do Mercado
Livre Brasil. Sua missão: pegar o produto líder e entregar um anúncio MELHOR e
mais competitivo para roubar a venda dele, explorando as fraquezas apontadas.
ENTRADA (JSON): produto, anuncio_lider {titulo, preco, fraquezas:[...]},
custo_fornecedor_br (pode ser null).
REGRAS:
- titulo_otimizado: ATÉ 60 caracteres, usando as palavras mais buscadas do nicho,
  superando o título do líder.
- bullets: EXATAMENTE 5, cada um vencendo uma objeção de compra e explorando uma
  fraqueza do líder.
- preco_venda_sugerido: competitivo vs. o líder, mas preservando margem saudável
  sobre o custo do fornecedor (se houver). Mostre a margem_pct.
- diferencial_ataque: 1 único, concreto, que o líder NÃO oferece (kit, brinde,
  frete grátis ou garantia estendida).
SAÍDA: responda SOMENTE com JSON válido:
{"titulo_otimizado": "<=60 chars", "bullets": ["b1","b2","b3","b4","b5"],
 "preco_venda_sugerido": <número>, "margem_pct": <número>,
 "diferencial_ataque": "<kit|brinde|frete|garantia: descrição curta>"}'''


def gerar_ficha_ataque(dados):
    """dados: produto, anuncio_lider {titulo, preco, fraquezas}, custo_fornecedor_br.
    Retorna o JSON da Ficha de Ataque."""
    out = _gemini_json(_SYS_FICHA, dados, max_tokens=1536, temperature=0.6)
    if not isinstance(out, dict):
        out = {}
    titulo = (out.get('titulo_otimizado') or '')[:60]
    out['titulo_otimizado'] = titulo
    bullets = out.get('bullets')
    if not isinstance(bullets, list):
        bullets = []
    out['bullets'] = [str(b) for b in bullets][:5]
    out.setdefault('preco_venda_sugerido', None)
    out.setdefault('margem_pct', None)
    out.setdefault('diferencial_ataque', '')
    return out
