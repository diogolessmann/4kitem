"""Isca: nome curto para a manchete, a partir do texto com fonte (ficha ou página do lago no TPWD).

A ordem importa: a frase longa vem antes da curta. Cada achado é apagado do texto para a curta não casar dentro.
"shad", "perch" e "sunfish" soltos ficaram de fora: no texto do TPWD costumam ser presa ou espécie.
"""
import re

VOCAB = [
    ("lipless crankbait", "lipless crankbait"), ("rattle trap", "lipless crankbait"), ("crankbait", "crankbait"),
    ("hard-body", "crankbait"), ("spinnerbait", "spinnerbait"), ("spinner bait", "spinnerbait"),
    ("plastic worm", "plastic worm"), ("lizard", "plastic lizard"), ("soft plastic", "soft plastic"),
    ("jigging spoon", "jigging spoon"), ("slab", "slab spoon"), ("crappie jig", "crappie jig"),
    ("topwater", "topwater lure"), ("top-water", "topwater lure"), ("swimbait", "swimbait"),
    ("jerkbait", "jerkbait"), ("carolina", "Carolina rig"), ("texas rig", "Texas rig"), ("drop shot", "drop shot"),
    ("spoon", "spoon"), ("jig", "jig"), ("plug", "plug"), ("small spinner", "small spinner"),
    ("live shrimp", "live shrimp"), ("dead shrimp", "dead shrimp"), ("shrimp", "shrimp"),
    ("finger mullet", "finger mullet"), ("mullet", "mullet"), ("mud minnow", "mud minnow"),
    ("minnow", "live minnow"), ("live bait", "live bait"), ("cutbait", "cut bait"), ("cut bait", "cut bait"),
    ("stinkbait", "stinkbait"), ("stink bait", "stinkbait"), ("punch bait", "punch bait"), ("dip bait", "dip bait"),
    ("chicken liver", "chicken liver"), ("liver", "chicken liver"), ("nightcrawler", "nightcrawler"),
    ("earthworm", "earthworm"), ("worms", "worm"), ("worm", "worm"), ("cricket", "cricket"),
    ("blue crab", "blue crab"), ("fiddler", "fiddler crab"), ("crab", "crab"), ("squid", "squid"),
    ("barnacle", "barnacle"), ("sand flea", "sand flea"), ("clam", "clam"), ("croaker", "live croaker"),
    ("live fish", "live fish"), ("live sunfish", "live sunfish"), ("live perch", "live perch"),
    ("live gizzard shad", "live shad"), ("live shad", "live shad"), ("mealworm", "mealworm"),
    ("grasshopper", "grasshopper"),
]
# frases "live X" e "mealworm" precisam vir antes das curtas: reordena pelo tamanho da chave
VOCAB.sort(key=lambda kv: -len(kv[0]))


def find(text, exclude=""):
    """Iscas citadas, na ordem em que aparecem no texto (a primeira citada é a da manchete)."""
    t = " " + (text or "").lower() + " "
    ex = (exclude or "").lower()
    hits = []
    for key, label in VOCAB:
        start = 0
        while (i := t.find(key, start)) >= 0:
            if not (ex and key in ex):
                hits.append((i, label))
            t = t[:i] + "#" * len(key) + t[i + len(key):]   # apaga mantendo as posições
            start = i + len(key)
    out = []
    for _, label in sorted(hits):
        if label not in out:
            out.append(label)
    return out


SEASON = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
          6: "summer", 7: "summer", 8: "summer", 9: "fall", 10: "fall", 11: "fall"}


def _lake_of(url):
    """Slug do lago quando a fonte da isca é a página de UM lago no TPWD (/recreational/lakes/<slug>/)."""
    m = re.search(r"/recreational/lakes/([a-z_]+)/", url or "")
    return m.group(1) if m else None


def pick(sp, date, lake_tip=None, low_light=False, slug=None):
    """Escolhe a isca da manchete. Devolve dict com nome curto, fonte e a lista do lago.
    Isca tirada da página de um lago só vale para aquele lago (revisão B10). Ordem: ficha geral da estação,
    ficha geral do ano todo, dicas do TPWD para este lago, ficha geral de outra estação."""
    season = SEASON[date.month]
    entries = sp.get("baits") or []
    here = [b for b in entries if _lake_of(b.get("source_url")) in (None, slug)]
    ordered = ([b for b in here if b.get("season") == season] +
               [b for b in here if b.get("season") in ("all", None)])
    lake_lures = (lake_tip or {}).get("lures", [])
    if lake_lures:
        ordered.append({"bait": " ".join(lake_lures), "season": None, "source_url": None})
    ordered += [b for b in here if b.get("season") not in (season, "all", None)]
    # `other` (dica de outro lago) fica de fora: isca de um lago não vira regra de todos (revisão B10)
    for b in ordered:
        names = find(f"{b.get('bait', '')} {b.get('technique', '')}", exclude=sp.get("common_name", ""))
        if not names:
            continue
        name = names[0]
        if low_light and "topwater lure" in names:
            name = "topwater lure"
        return {"short": name, "all": names, "season": b.get("season"), "source_url": b.get("source_url"),
                "lake_lures": lake_lures, "from_lake_tip": b.get("source_url") is None}
    return {"short": None, "all": [], "season": None, "source_url": None, "lake_lures": lake_lures}
