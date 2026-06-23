"""
mlhype_ia.py — O cérebro de IA do MLhype (Dissecador + Avaliador + Ficha).

Resiliência: tenta a API do **Gemini** e, se ela falhar (ex.: 429 cota
estourada — comum no free tier compartilhado), cai pro **Groq** (llama, que o
4kitem já usa). E faz os 3 agentes numa ÚNICA chamada (3x menos cota, 3x mais
rápido). Degrada sem quebrar: se as duas IAs falharem, devolve vazio e o
orquestrador marca `ia_falhou` (a tela mostra os dados de mercado mesmo assim).
"""
import os
import json
import logging

import requests

log = logging.getLogger(__name__)

GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
_GEMINI_URL  = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'

GROQ_KEY   = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')
_GROQ_URL  = 'https://api.groq.com/openai/v1/chat/completions'


def ia_disponivel():
    return bool(GEMINI_KEY or GROQ_KEY)


# ── Chamadas de baixo nível (cada uma devolve TEXTO ou levanta) ────────────────
def _gemini_call(system, payload, max_tokens, temperature):
    gen = {'temperature': temperature, 'maxOutputTokens': max_tokens,
           'responseMimeType': 'application/json'}
    if '2.5' in GEMINI_MODEL:                     # desliga o "thinking" lento
        gen['thinkingConfig'] = {'thinkingBudget': 0}
    r = requests.post(_GEMINI_URL.format(model=GEMINI_MODEL),
                      params={'key': GEMINI_KEY},
                      json={'system_instruction': {'parts': [{'text': system}]},
                            'contents': [{'role': 'user',
                                          'parts': [{'text': json.dumps(payload, ensure_ascii=False)}]}],
                            'generationConfig': gen},
                      timeout=35)
    if r.status_code != 200:
        raise RuntimeError(f'Gemini HTTP {r.status_code}')
    return r.json()['candidates'][0]['content']['parts'][0]['text']


def _groq_call(system, payload, max_tokens, temperature):
    r = requests.post(_GROQ_URL,
                      headers={'Authorization': f'Bearer {GROQ_KEY}', 'Content-Type': 'application/json'},
                      json={'model': GROQ_MODEL, 'temperature': temperature, 'max_tokens': max_tokens,
                            'response_format': {'type': 'json_object'},
                            'messages': [{'role': 'system', 'content': system},
                                         {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]},
                      timeout=35)
    if r.status_code != 200:
        raise RuntimeError(f'Groq HTTP {r.status_code}')
    return r.json()['choices'][0]['message']['content']


def _ia_json(system, payload, max_tokens=2048, temperature=0.4):
    """Tenta Gemini; se falhar (cota/erro), cai pro Groq. Devolve dict."""
    erros = []
    for nome, fn, tem_key in (('gemini', _gemini_call, GEMINI_KEY),
                              ('groq', _groq_call, GROQ_KEY)):
        if not tem_key:
            continue
        try:
            txt = fn(system, payload, max_tokens, temperature)
            return json.loads(txt)
        except Exception as e:
            erros.append(f'{nome}: {e}')
            log.warning(f'[MLhype IA] {nome} falhou: {e}')
    raise RuntimeError('IA indisponível — ' + ' | '.join(erros or ['sem chave configurada']))


def _cap60(t):
    """Limita o título a 60 chars cortando na ÚLTIMA palavra inteira (não no meio)."""
    t = (t or '').strip()
    if len(t) <= 60:
        return t
    corte = t[:60]
    sp = corte.rfind(' ')
    return (corte[:sp] if sp > 40 else corte).rstrip(' ,-+')


# ── A esteira inteira numa tacada (Dissecador + Avaliador + Ficha) ─────────────
_SYS_ESTEIRA = '''Você é o cérebro do MLhype, analista frio de oportunidades de
venda no Mercado Livre Brasil. Recebe o produto LÍDER de uma categoria e faz 3
trabalhos de uma vez:

1) DISSECADOR: 3 a 6 fraquezas CONCRETAS e ACIONÁVEIS do anúncio líder (título
   fraco, poucas fotos, sem garantia/kit, preço com gordura, descrição pobre,
   atributos faltando).
2) AVALIADOR: nota 0-100 + veredito. Considere demanda (tendência), margem
   (preco_lider vs menor_preco_fornecedor_br, descontando ~15% de taxas do ML +
   uma estimativa de frete — diga a premissa no "porque") e concorrência
   (num_concorrentes; muitos = guerra de preço). Penalize margem fina, tendência
   caindo, mercado lotado, ausência de fornecedor. Sem dado crítico → reduza o
   score e explique.
3) FICHA DE ATAQUE: um anúncio MELHOR pra roubar a venda — título ATÉ 60
   caracteres com as palavras mais buscadas, 5 bullets vencendo objeções e
   explorando as fraquezas, preço de venda competitivo vs o líder preservando
   margem sobre o custo do fornecedor (se houver), e 1 diferencial de ataque
   (kit/brinde/frete/garantia) que o líder NÃO oferece.

ENTRADA (JSON): nome_produto, anuncio_lider{titulo,preco,fotos_qtd,atributos,
garantia}, num_concorrentes, tendencia, menor_preco_fornecedor_br (pode ser null).

SAÍDA: responda SOMENTE com JSON válido, exatamente neste formato:
{"fraquezas":["...","..."],
 "avaliacao":{"score":<0-100>,"margem_pct":<número>,"veredito":"ataque|observe|evite",
   "porque":"<1 frase>","como_melhorar":["...","...","..."]},
 "ficha":{"titulo_otimizado":"<=60 chars","bullets":["b1","b2","b3","b4","b5"],
   "preco_venda_sugerido":<número>,"margem_pct":<número>,
   "diferencial_ataque":"<kit|brinde|frete|garantia: descrição curta>"}}
Não invente dados que não recebeu.'''


def analisar_esteira(dados):
    """Roda os 3 agentes numa chamada só. Devolve {fraquezas, avaliacao, ficha}
    já normalizados. Levanta se as duas IAs falharem."""
    out = _ia_json(_SYS_ESTEIRA, dados, max_tokens=2048, temperature=0.45)
    if not isinstance(out, dict):
        out = {}

    fr = out.get('fraquezas')
    fraquezas = [str(x) for x in fr][:6] if isinstance(fr, list) else []

    av = out.get('avaliacao') if isinstance(out.get('avaliacao'), dict) else {}
    if av.get('veredito') not in ('ataque', 'observe', 'evite'):
        av['veredito'] = 'observe'
    try:
        av['score'] = max(0, min(100, int(av.get('score', 0))))
    except Exception:
        av['score'] = 0
    av.setdefault('margem_pct', None)
    av.setdefault('porque', '')
    if not isinstance(av.get('como_melhorar'), list):
        av['como_melhorar'] = []

    fi = out.get('ficha') if isinstance(out.get('ficha'), dict) else {}
    fi['titulo_otimizado'] = _cap60(fi.get('titulo_otimizado'))
    fi['bullets'] = [str(b) for b in fi.get('bullets', [])][:5] if isinstance(fi.get('bullets'), list) else []
    fi.setdefault('preco_venda_sugerido', None)
    fi.setdefault('margem_pct', None)
    fi.setdefault('diferencial_ataque', '')

    return {'fraquezas': fraquezas, 'avaliacao': av, 'ficha': fi}
