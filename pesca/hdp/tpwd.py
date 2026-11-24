"""Lê a página de cada lago no TPWD: espécies principais, nota do TPWD, dicas e link das regras.

Fonte: https://tpwd.texas.gov/fishboat/fish/recreational/lakes/<slug>/
Guardamos fatos (nomes de espécie, nota, nomes de isca citados, link). O texto das dicas
fica só no cache local; a página do site mostra a lista de iscas + link para o TPWD.
"""
import html as _html
import re

from .net import get

BASE = "https://tpwd.texas.gov/fishboat/fish/recreational/lakes/{slug}/"
TPWD = "https://tpwd.texas.gov"
CATS = ("channel_catfish", "blue_catfish", "flathead_catfish")


def _text(fragment):
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def _one(part):
    """Um nome simples ("hybrid striped bass", "blue catfish") -> id da ficha, 'catfish' (genérico) ou None."""
    n = part.lower()
    if "hybrid" in n or "palmetto" in n or "sunshine" in n:
        return "hybrid_striped_bass"
    if "striped" in n or "striper" in n:
        return "striped_bass"
    if "largemouth" in n:
        return "largemouth_bass"
    if "white bass" in n:
        return "white_bass"
    if "crappie" in n:
        return "crappie"
    if "channel" in n:
        return "channel_catfish"
    if "blue cat" in n:
        return "blue_catfish"
    if "flathead" in n:
        return "flathead_catfish"
    if "bluegill" in n or re.fullmatch(r"(sunfish|sunfishes|bream)", n.strip()):
        return "bluegill"          # "Redear sunfish" e outros sunfish NÃO viram bluegill
    if re.fullmatch(r"cat(fish|fishing)?s?", n.strip()):
        return "catfish"
    return None


def species_ids(name):
    """'Blue , channel & flathead catfish' -> blue, channel, flathead. O substantivo final passa para as partes
    que não o têm ('White & hybrid striped bass' -> 'White bass' + 'hybrid striped bass')."""
    parts = [p.strip() for p in re.split(r"\s*(?:&|,|/|\band\b)\s*", name.lower()) if p.strip()]
    if not parts:
        return []
    noun = next((w for w in ("catfish", "bass", "crappie", "sunfish") if parts[-1].endswith(w)), None)
    out = []
    for p in parts:
        full = p if (not noun or p.endswith(noun) or " " in p and _one(p)) else f"{p} {noun}"
        sid = _one(full) or _one(p)
        if sid and sid not in out:
            out.append(sid)
    return out


def _cats_named(lake):
    """Quais bagres a própria página cita (dicas + 'Angling Opportunities'). Nenhum citado -> só o channel."""
    text = " ".join([lake.get("tips_all", ""), lake.get("angling", "")]).lower()
    pats = (("channel_catfish", r"channel cat|channels?\s*(?:,|&|and)\s*(?:blue|flathead)|(?:blue|flathead)s?\s*(?:,|&|and)\s*channel"),
            ("blue_catfish", r"blue cat|blues?\s*(?:,|&|and)\s*(?:channel|flathead)|(?:channel|flathead)s?\s*(?:,|&|and)\s*blue"),
            ("flathead_catfish", r"flathead"))
    named = [c for c, rx in pats if re.search(rx, text)]   # "creek channel" não é bagre
    return named or ["channel_catfish"]


def lures_in(text):
    """Iscas citadas no texto do lago (vocabulário único em baits.py)."""
    from .baits import find
    return find(text)


def parse(page_html, slug):
    h = page_html
    out = {"slug": slug, "url": BASE.format(slug=slug)}
    txt = _text(re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S))

    m = re.search(r"Location:\s*(.+?)\s*Surface area:\s*([\d,]+)\s*acres", txt)
    out["location"] = m.group(1) if m else None
    out["surface_acres"] = int(m.group(2).replace(",", "")) if m else None

    out["predominant"] = []
    m = re.search(r"Predominant Fish Species</h6>\s*<ul>(.*?)</ul>", h, flags=re.S)
    if m:
        out["predominant"] = [_text(li) for li in re.findall(r"<li>(.*?)</li>", m.group(1), flags=re.S)]

    out["ratings"] = {}
    m = re.search(r'<table id="Ratings".*?</table>', h, flags=re.S)
    if m:
        cols = ["Poor", "Fair", "Good", "Excellent"]
        for row in re.findall(r"<tr>(.*?)</tr>", m.group(0), flags=re.S):
            th = re.search(r'<th[^>]*scope="row"[^>]*>(.*?)</th>', row, flags=re.S)
            if not th:
                continue
            for i, td in enumerate(re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)[:4]):
                if "<img" in td:
                    out["ratings"][_text(th.group(1))] = cols[i]

    m = re.search(r'href="(?:https?://tpwd\.texas\.gov)?(/fishboat/fish/action/fishregs2\.php\?water=\d+)"', h)
    out["regs_url"] = TPWD + m.group(1) if m else None
    m = re.search(r"Fishing Regulations</h6>(.*?)<h6", h, flags=re.S)
    out["regs_text"] = _text(m.group(1)) if m else None
    rt = (out["regs_text"] or "").lower()
    # "special" sozinho casava com "Bow fishers ... special regulations enforced by the LCRA" (Travis): só frases de pesca
    out["special_regs"] = bool(out["regs_url"]) or any(k in rt for k in (
        "special regulations on", "special bag and size", "special catfish limits", "special limits", "special length"))
    out["statewide_only"] = (not out["special_regs"]) and bool(re.search(r"managed under (?:current )?statewide regulations", rt))
    out["mexico_license"] = "mexico fishing license" in rt

    m = re.search(r'id="howgood"></a>Angling Opportunities</h6>(.*?)<h6', h, flags=re.S)
    out["angling"] = _text(m.group(1)) if m else ""

    out["tips"] = {}
    m = re.search(r'id="tactics"></a>Tips &amp; Tactics</h6>(.*?)(?:<h6|<div class="email|Email Updates)', h, flags=re.S)
    out["tips_all"] = _text(m.group(1)) if m else ""
    generic_cat_tip = None
    if m:
        for p in re.split(r"<strong>", m.group(1))[1:]:
            name, _, rest = p.partition("</strong>")
            seg = _text(rest)
            ids = species_ids(_text(name))
            if "catfish" in ids:
                ids.remove("catfish")
                generic_cat_tip = (generic_cat_tip or "") + " " + seg
            for sid in ids:
                prev = out["tips"].setdefault(sid, {"text": "", "lures": []})
                prev["text"] = (prev["text"] + " " + seg).strip()
                prev["lures"] = list(dict.fromkeys(prev["lures"] + lures_in(seg)))
    if generic_cat_tip:
        for sid in _cats_named(out):
            prev = out["tips"].setdefault(sid, {"text": "", "lures": []})
            prev["text"] = (prev["text"] + " " + generic_cat_tip.strip()).strip()
            prev["lures"] = list(dict.fromkeys(prev["lures"] + lures_in(generic_cat_tip)))
    return out


def parse_lake_regs(page_html):
    """Página fishregs2.php?water=NNNN: [(grupo, texto da regra)], na ordem do TPWD. Texto curto de regra oficial."""
    m = re.search(r"<dl>(.*?)</dl>", page_html, flags=re.S)
    if not m:
        return []
    out = []
    for dt, rest in re.findall(r"<dt>(.*?)</dt>(.*?)(?=<dt>|\Z)", m.group(1), flags=re.S):
        g = _text(dt).rstrip(":")
        t = " ".join(_text(dd) for dd in re.findall(r"<dd>(.*?)</dd>", rest, flags=re.S)).strip()
        if g and t and g.lower() != "note":
            out.append([g, t])
    return out


REG_GROUPS = {  # grupo do TPWD -> espécies da v1 que ele cobre
    "black bass": ["largemouth_bass"], "largemouth": ["largemouth_bass"], "bluegill": ["bluegill"], "catfish": list(CATS), "crappie": ["crappie"], "sunfish": ["bluegill"],
    "white bass": ["white_bass"], "striped bass": ["striped_bass", "hybrid_striped_bass"],
    "hybrid": ["hybrid_striped_bass"],
}


def regs_for(lake_regs, species_ids_present):
    """Só os grupos que tocam as espécies da página."""
    out = []
    for g, t in lake_regs or []:
        gl = g.lower()
        cover = {sid for key, sids in REG_GROUPS.items() if key in gl for sid in sids}
        if cover & set(species_ids_present):
            out.append((g, t))
    return out


def fetch_regs(url):
    return get(url, headers={"User-Agent": "Mozilla/5.0 (HoraDePescarPrototype)"}, as_json=False)


def species_for_lake(lake):
    """Espécies da v1 presentes no lago (lista 'Predominant' + tabela de notas), com a nota do TPWD."""
    present, generic_pred = {}, False
    for name in lake.get("predominant", []):
        for sid in species_ids(name):
            if sid == "catfish":
                generic_pred = True
            else:
                present.setdefault(sid, None)
    if generic_pred:  # "Catfish" genérico na lista: os bagres que a página cita
        for c in _cats_named(lake):
            present.setdefault(c, None)
    for rname, rating in lake.get("ratings", {}).items():
        for sid in species_ids(rname):
            if sid == "catfish":  # nota do grupo vale para os bagres já presentes
                for c in [c for c in CATS if c in present] or _cats_named(lake):
                    present[c] = rating
            else:
                present[sid] = rating
    return present


def fetch(slug):
    return get(BASE.format(slug=slug), headers={"User-Agent": "Mozilla/5.0 (HoraDePescarPrototype)"}, as_json=False)
