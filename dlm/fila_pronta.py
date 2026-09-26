# -*- coding: utf-8 -*-
"""📦 FILA DE PEÇAS PRONTAS — o material feito à mão entra no motor (26/set/2026).

As 26 peças do CONTEUDO_21 (carrossel 6 slides + reel narrado) e os 27 stories moram no Cofre
(static/dlcentral/<aba>/c21_*) e a ordem/legenda/janela de data em dlm/fila_pronta.json.

Regras (decididas com o dono):
- NÃO aumenta o feed: o turno das 10h alterna — dia de fila = peça pronta; dia sem = série de sempre.
  (a evidência de 15/set desligou o 3º post do dia; a fila não o religa)
- Cada peça sai 2 vezes, em rodadas: 1ª volta reel/carrossel alternados, 2ª volta o formato que faltou.
- Peça com data (placa final 7/8/9/0) só sai dentro da janela e FURA a fila quando a janela abre.
- Story: 1 por dia, próprio turno.
- A legenda passa pela vacina (se o módulo existir) — barrou, pula e registra.
- Estado em DATA_DIR/fila_pronta_estado.json: o que já saiu não repete; falha não marca como feito.
"""
import json
import os
from datetime import date

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
FILA = os.path.join(AQUI, "fila_pronta.json")
_DATA = os.environ.get("DATA_DIR", RAIZ)
ESTADO = os.path.join(_DATA, "fila_pronta_estado.json")
COFRE = os.path.join(RAIZ, "static", "dlcentral")


def _fila():
    with open(FILA, encoding="utf-8") as f:
        return json.load(f)


def _estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"feito": {}}


def _marca_feito(chave, info):
    e = _estado()
    e.setdefault("feito", {})[chave] = info
    try:
        with open(ESTADO, "w", encoding="utf-8") as f:
            json.dump(e, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _na_janela(item, hoje):
    de, ate = item.get("de"), item.get("ate")
    if not de:
        return True
    return de <= hoje.isoformat() <= ate


def ordem_feed():
    """[(chave, peça, tipo)] — 1ª volta alterna reel/carrossel; 2ª volta o formato que faltou."""
    pecas = _fila()["pecas"]
    v1, v2 = [], []
    for i, p in enumerate(pecas):
        a, b = ("reel", "carrossel") if i % 2 == 0 else ("carrossel", "reel")
        v1.append((p["id"] + ":" + a, p, a))
        v2.append((p["id"] + ":" + b, p, b))
    return v1 + v2


def proximo_feed(hoje=None):
    hoje = hoje or date.today()
    feito = _estado().get("feito", {})
    livres = [(c, p, t) for c, p, t in ordem_feed() if c not in feito and _na_janela(p, hoje)]
    if not livres:
        return None
    datados = [x for x in livres if x[1].get("de")]
    return (datados or livres)[0]          # peça com data fura a fila quando a janela abre


def proximo_story(hoje=None):
    hoje = hoje or date.today()
    feito = _estado().get("feito", {})
    ordem = sorted(_fila()["stories"], key=lambda s: (0, int(s["id"])) if s["id"].isdigit() else (1, s["id"]))
    livres = [s for s in ordem if ("story:" + s["id"]) not in feito and _na_janela(s, hoje)]
    if not livres:
        return None
    datados = [s for s in livres if s.get("de")]
    return (datados or livres)[0]


def dia_de_fila(hoje=None):
    hoje = hoje or date.today()
    return hoje.timetuple().tm_yday % 2 == 0 and os.environ.get("FILA_PRONTA_ON", "1") == "1"


def _legenda(p):
    from dlcentral import MARCAS
    tags = MARCAS.get(p["aba"], {}).get("hashtags", "")
    return (p["legenda"].strip() + ("\n\n" + tags if tags else "")).strip()


def _vacina_barra(onde, texto):
    try:
        from dlm import vacina
    except Exception:
        return False                          # vacina ainda não subiu: segue (as peças já foram vacinadas na produção)
    v = vacina.checar(texto)
    if v:
        vacina.registrar(onde, v)
        return True
    return False


def postar_feed(ao_vivo, hoje=None):
    """Publica a próxima peça. None = fila vazia (o motor cai na série)."""
    prox = proximo_feed(hoje)
    if not prox:
        return None
    chave, p, tipo = prox
    leg = _legenda(p)
    if _vacina_barra("fila %s" % chave, leg):
        _marca_feito(chave, {"quando": date.today().isoformat(), "r": "vacina barrou"})
        return "💉 fila: %s barrada pela vacina" % chave
    if not ao_vivo:
        return "PREVIEW fila %s (%s): %s" % (chave, p["aba"], p["titulo"])
    from dlm import marcas
    t = marcas.BRANDS["despachante"]
    if tipo == "reel":
        import dlcentral
        token, ig_id, _ = marcas._brand_tokens(t)
        if not (token and ig_id):
            return "sem tokens DESP_* — fila pulada"
        url = "%s/dlmedia/%s/%s" % (dlcentral.PUBLIC_BASE, p["aba"], p["reel"])
        r = marcas._publish_reel(token, ig_id, p["reel"], leg, video_url=url)
    else:
        paths = [os.path.join(COFRE, p["aba"], s) for s in p["carrossel"]]
        r = marcas.publish_brand(t, "fila_%s" % chave.replace(":", "_"), paths, leg)
    _marca_feito(chave, {"quando": date.today().isoformat(), "r": str(r)[:120]})
    return "POSTADO fila %s (%s)" % (chave, p["titulo"])


def postar_story(ao_vivo, hoje=None):
    s = proximo_story(hoje)
    if not s:
        return "stories da fila acabaram"   # sem "fila vazia": o dlmotor trataria como recuperável e retentaria
    if not ao_vivo:
        return "PREVIEW story %s (%s)" % (s["id"], s["aba"])
    from dlm import marcas
    t = marcas.BRANDS["despachante"]
    token, ig_id, _ = marcas._brand_tokens(t)
    if not (token and ig_id):
        return "sem tokens DESP_* — story pulado"
    r = marcas.publish_story_brand(t, "fila_story_%s" % s["id"], os.path.join(COFRE, s["aba"], s["arquivo"]))
    _marca_feito("story:" + s["id"], {"quando": date.today().isoformat(), "r": str(r)[:120]})
    return "POSTADO story %s" % s["id"]


def resumo():
    feito = _estado().get("feito", {})
    tot_feed = len(ordem_feed()); tot_st = len(_fila()["stories"])
    f_feed = sum(1 for c in feito if not c.startswith("story:")); f_st = sum(1 for c in feito if c.startswith("story:"))
    return "fila: feed %d/%d · stories %d/%d" % (f_feed, tot_feed, f_st, tot_st)
