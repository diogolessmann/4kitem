# -*- coding: utf-8 -*-
"""💉 VACINA — o que NUNCA vai ao ar, em nenhum perfil do dono (24/set/2026).

Todo erro que a gente acha vira uma linha aqui, e daí em diante bloqueia a publicação para
sempre. Sem IA, sem token: é lista. A IA erra "provavelmente"; a lista não.

Onde ela é conferida (as 4 portas):
  1. séries do Despachante e da DL — TEXTO DO SLIDE (título/bullets) e legenda   → marcas.py
  2. Roda Norte — manchete, bullets e legenda escritas pelo Gemini              → mobilidade.py
  3. Central DL (painel manual) — legenda antes de publicar                      → dlcentral.py
  4. auditoria do banco inteiro: `python -m dlm.vacina` (rodar antes de todo deploy)

Tudo que ela bloqueia fica em DATA_DIR/vacina_bloqueios.jsonl e aparece no log da Central DL —
é assim que a gente descobre qual erro a IA mais comete e afina o prompt.

Escopos:
  "desp" = voz do Grupo Lessmann (Despachante, DL Mobilidade, post "nosso" do Roda Norte, painel)
  "mob"  = notícia do Roda Norte (pode citar polícia/outro estado: regras de voz não valem)

COMO ACRESCENTAR UMA REGRA: (regex, motivo curto, escopos[, exceção]). Regex em minúsculas, sobre
o texto já sem acento, frase a frase. A exceção é o contexto em que o termo está CERTO.
Depois, `python -m dlm.vacina` tem que continuar limpo.
"""
import json
import os
import re
import unicodedata
from datetime import datetime

_T = ("desp", "mob")     # todos
_D = ("desp",)           # só na voz da casa

# (padrão, motivo, escopos[, exceção]) — padrão casa contra o texto em minúsculas e SEM acento
REGRAS = [
    # ── promessa / claim que o despachante não pode fazer (CDC art. 35 + episódio ADEVI 14/set)
    (r"\brenovamos\b", "diz que renova CNH (quem renova é o cidadão, no gov.br)", _T),
    (r"\b(fazemos|emitimos|tiramos) (a )?sua (cnh|habilitacao|carteira)\b", "diz que faz/emite CNH", _T),
    (r"\bnoss[oa]s? (curso|cursos|autoescola)\b", "diz que o curso/autoescola é nosso (a gente REPRESENTA)", _T),
    (r"\bgarantimos\b|\b(resultado|aprovacao) garantid[oa]\b|\bgarantia de (resultado|aprovacao|sucesso)\b",
     "promete resultado (CDC art. 35)", _T),
    (r"\b100 ?% de (aprovacao|sucesso|exito|acerto)\b", "promete taxa de sucesso", _T),
    (r"\b(anulamos|cancelamos|zeramos|derrubamos)\b", "promete anular/cancelar multa", _T),
    (r"\btiramos (os |seus )?pontos\b|\blimpamos (a |sua )?(cnh|carteira)\b", "promete tirar pontos", _T),
    (r"\bsem p[o]r o pe no detran\b", "'sem pôr o pé no DETRAN' (proibido desde 14/set)", _T),
    (r"\b(prioridade|furar (a )?fila|sem fila)( no| do| dentro do)? detran\b",
     "insinua furar a fila de órgão público", _T),
    # ── erros de lei conferidos no CTB (24/set) — ver memória reference_regras_transito_sc_conferidas
    (r"\b233\b[^.\n]{0,80}\bgrave\b|\bgrave\b[^.\n]{0,80}\b233\b",
     "art. 233 é MÉDIA (4 pontos, remoção), não grave", _T),
    (r"165-?c\b[^.\n]{0,100}venc|venc[^.\n]{0,100}165-?c\b",
     "toxicológico vencido é art. 165-D (o 165-C é resultado positivo)", _T),
    (r"vencid[ao][^.\n]{0,100}recolhimento|recolhimento[^.\n]{0,100}vencid[ao]",
     "CNH vencida = RETENÇÃO do veículo (Lei 14.440), não recolhimento", _T),
    (r"reciclagem[^.\n]{0,80}\b30 ?(h|horas)\b|\b30 ?(h|horas)\b[^.\n]{0,80}reciclagem",
     "reciclagem agora é 45 h (R1020/2025) — não citar carga horária", _T),
    # ── voz da casa: termos certos na lei, errados na nossa boca
    (r"\bapreensao d[oe] (veiculo|carro|moto)\b|\b(veiculo|carro|moto) (sera |e |fica |vai ser )?apreendid[oa]\b",
     "'apreensão' foi revogada em 2016 — é remoção ao pátio", _D,
     r"descaminho|contrabando|crime|furt|roub|receita federal|policia|droga"),   # apreensão penal/fiscal existe
    (r"\bipva\b[^.\n]{0,80}\bdesconto\b|\bdesconto\b[^.\n]{0,80}\bipva\b",
     "IPVA em SC não tem desconto à vista", _D),
    (r"\bisen[^.\n]{0,60}\bipva\b[^.\n]{0,80}\b(20|30) anos\b|\b(20|30) anos\b[^.\n]{0,80}\bisen[^.\n]{0,60}\bipva\b",
     "isenção por idade está em conflito (EC 137 × lei de SC) — não publicar", _D),
]
# 4º item opcional = EXCEÇÃO: se aparecer na mesma frase, a regra não vale (contexto em que o termo é certo)
_COMPILADAS = [(re.compile(r[0]), r[1], r[2], re.compile(r[3]) if len(r) > 3 else None) for r in REGRAS]

_DATA = os.environ.get("DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG = os.path.join(_DATA, "vacina_bloqueios.jsonl")


class Bloqueado(Exception):
    """Levantada quando um texto fixo (slide/série) viola a vacina — o post NÃO sai."""


def _norm(txt):
    txt = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode("ascii").lower()
    # "art. 233" não é fim de frase: sem isto a frase partia no ponto e o erro de lei passava
    txt = re.sub(r"\b(art|arts|inc|par|n|no|res|resol|port)\.\s*", r"\1 ", txt)
    txt = re.sub(r"(\d)\.(\d)", r"\1\2", txt)          # 1.027 / R$ 4.990 também não
    return re.sub(r"[ \t]+", " ", txt)


def _textos(obj):
    """Achata str / list / dict em pedaços de texto (o item da série é um dict)."""
    if obj is None:
        return []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [t for v in obj.values() for t in _textos(v)]
    if isinstance(obj, (list, tuple)):
        return [t for v in obj for t in _textos(v)]
    return []


def checar(*partes, escopo="desp"):
    """Devolve a lista de violações [(motivo, trecho)] — vazia = pode ir ao ar."""
    achou = []
    for bruto in _textos(list(partes)):
        for frase in re.split(r"(?<=[.!?])\s+|\n+", _norm(bruto)):
            for rx, motivo, escopos, excecao in _COMPILADAS:
                if escopo not in escopos:
                    continue
                m = rx.search(frase)
                if m and not (excecao and excecao.search(frase)):
                    ini = max(0, m.start() - 30)
                    achou.append((motivo, frase[ini:m.end() + 30].strip()))
    return achou


def registrar(onde, violacoes, extra=None):
    """Anota o bloqueio (jsonl no volume + log da Central DL). Nunca quebra o chamador."""
    if not violacoes:
        return
    try:
        os.makedirs(os.path.dirname(_LOG) or ".", exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="minutes"), "onde": onde,
                                "violacoes": [{"motivo": m, "trecho": t} for m, t in violacoes],
                                **(extra or {})}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    try:
        import dlcentral
        dlcentral.log(f"💉 vacina barrou ({onde}): {violacoes[0][0]}")
    except Exception:
        pass


def exigir(onde, *partes, escopo="desp", extra=None):
    """Para texto FIXO (slide/série): se violar, registra e levanta Bloqueado."""
    v = checar(*partes, escopo=escopo)
    if v:
        registrar(onde, v, extra)
        raise Bloqueado(f"{v[0][0]} — «{v[0][1]}»")


def ultimos(n=20):
    try:
        with open(_LOG, encoding="utf-8") as f:
            return [json.loads(l) for l in f.readlines()[-n:]][::-1]
    except Exception:
        return []


# ─────────────────────────────────────────────── auditoria do banco inteiro (antes do deploy)
def auditar():
    """Confere TODO texto fixo que o motor pode publicar. Devolve [(onde, motivo, trecho)]."""
    from dlm import series_despachante as sd
    from dlm import marcas
    achados = []
    for it in sd._flat(list(sd.SERIES)):
        for m, t in checar({k: it.get(k) for k in ("titulo", "bullets", "cta_seal", "cta_big", "pagina")}):
            achados.append((f"série {it['serie']} passo {it['passo']}", m, t))
    for chave, t in marcas.BRANDS.items():
        for i, it in enumerate(t.get("conteudo") or []):
            for m, tr in checar(it):
                achados.append((f"{chave} conteudo[{i}]", m, tr))
    for i, a in enumerate(getattr(marcas, "DL_ANGLES", [])):
        for m, tr in checar(a):
            achados.append((f"DL_ANGLES[{i}]", m, tr))
    for m, tr in checar(getattr(marcas, "DL_BENEFITS", [])):
        achados.append(("DL_BENEFITS", m, tr))
    return achados


if __name__ == "__main__":
    import sys
    r = auditar()
    for onde, motivo, trecho in r:
        print(f"✗ {onde}: {motivo}\n    «{trecho}»")
    print(f"vacina: {len(REGRAS)} regras · {len(r)} violação(ões) no banco")
    sys.exit(1 if r else 0)
