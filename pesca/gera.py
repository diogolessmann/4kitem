"""Gera o site estático (inglês dos EUA) a partir da última coleta, e um resumo em português para o Diogo.

Uso: python gera.py      -> site/index.html, site/how-we-score.html, site/best-time-to-fish/<lugar>/index.html
                            data/out/RESUMO_PT.txt
     python gera.py --deploy   (com HDP_SITE_URL) -> URLs limpas, canonical, sitemap.xml, robots.txt,
                            _headers e versao.txt, prontos para subir. Quem limpa site/ antes é o atualiza.py.
"""
import json
import sys
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

from hdp import astro
from hdp.config import DATA, OUT, SITE, SITE_URL, TEMPLATES, TZ
from hdp.engine import build, load_run, load_species

BRAND = "Best Time to Fish"   # nome provisório: a marca é decisão do Diogo
NAMES = {"bluegill": "Sunfish (bluegill)", "crappie": "Crappie"}
PT_FISH = {"largemouth_bass": "black bass", "white_bass": "white bass", "striped_bass": "striped bass",
           "hybrid_striped_bass": "híbrido (striper)", "crappie": "crappie", "channel_catfish": "bagre (channel)",
           "blue_catfish": "bagre azul", "flathead_catfish": "bagre flathead", "bluegill": "sunfish (bluegill)",
           "red_drum": "red drum", "spotted_seatrout": "pescada (seatrout)", "southern_flounder": "linguado",
           "black_drum": "black drum", "sheepshead": "sheepshead", "atlantic_croaker": "corvina (croaker)"}
PT_BAIT = {"plastic worm": "minhoca de borracha", "topwater lure": "isca de superfície", "crankbait": "crankbait",
           "lipless crankbait": "crankbait sem barbela", "spinnerbait": "spinnerbait", "jig": "jig",
           "live shrimp": "camarão vivo", "shrimp": "camarão", "dead shrimp": "camarão morto", "cut bait": "isca cortada",
           "stinkbait": "massa fedida", "live minnow": "lambari vivo", "live bait": "isca viva", "worm": "minhoca",
           "earthworm": "minhoca", "nightcrawler": "minhocuçu", "cricket": "grilo", "soft plastic": "isca de silicone",
           "finger mullet": "tainha pequena", "mullet": "tainha", "blue crab": "siri", "crab": "caranguejo",
           "squid": "lula", "chicken liver": "fígado de galinha", "spoon": "colher", "jigging spoon": "colher de jig",
           "slab spoon": "colher pesada", "crappie jig": "jig de crappie", "live croaker": "corvina viva",
           "fiddler crab": "chama-maré", "barnacle": "craca", "sand flea": "tatuíra", "clam": "marisco",
           "live fish": "peixe vivo", "live sunfish": "sunfish vivo", "live perch": "perca viva", "live shad": "shad vivo",
           "mealworm": "larva-da-farinha", "grasshopper": "gafanhoto", "plastic lizard": "lagartixa de silicone",
           "small spinner": "spinner pequeno", "punch bait": "massa (punch bait)", "dip bait": "massa (dip bait)",
           "mud minnow": "mud minnow", "plug": "plug", "swimbait": "swimbait"}


def hm(t):
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def make_env(now):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))
    env.filters["sc"] = lambda s: int(round(s))
    env.filters["scls"] = lambda s: "s-great" if s >= 7.5 else "s-good" if s >= 5.5 else "s-fair" if s >= 3.5 else "s-poor"
    env.filters["hm"] = hm
    env.filters["hshort"] = lambda t: f"{t.hour % 12 or 12}{'a' if t.hour < 12 else 'p'}"
    env.filters["is_past"] = lambda t, n: t.replace(minute=59) < n

    def dayname(d):
        delta = (d - now.date()).days
        return "Today" if delta == 0 else "Tomorrow" if delta == 1 else d.strftime("%a, %b ") + str(d.day)
    env.filters["dayname"] = dayname
    env.filters["months"] = lambda ms: ", ".join(datetime(2026, m, 1).strftime("%b") for m in ms)
    env.filters["phasename"] = astro.phase_name
    # uma palavra para a nota (mesmos cortes das cores): 8-10 Great, 6-7 Good, 4-5 Fair, 0-3 Poor
    env.filters["sword"] = lambda s: "Great" if s >= 7.5 else "Good" if s >= 5.5 else "Fair" if s >= 3.5 else "Poor"

    def sky(t):
        """Um ícone para o céu do meio-dia (texto curto do NWS)."""
        t = (t or "").lower()
        for keys, ico in ((("thunder",), "⛈️"), (("rain", "shower", "drizzle"), "🌧️"), (("snow", "sleet"), "🌨️"),
                          (("fog",), "🌫️"), (("partly", "mostly sunny"), "⛅"), (("cloud", "overcast"), "☁️"),
                          (("sunny", "clear"), "☀️")):
            if any(k in t for k in keys):
                return ico
        return "🌤️"
    env.filters["sky"] = sky
    env.filters["monthrange"] = lambda ms: (datetime(2026, ms[0], 1).strftime("%b") + " to " +
                                            datetime(2026, ms[-1], 1).strftime("%b")) if ms else ""
    return env


def main():
    deploy = "--deploy" in sys.argv     # URLs limpas, canonical, sitemap, robots, _headers, versao.txt
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run_id, run = load_run(args[0] if args else None)
    if deploy and not SITE_URL:
        sys.exit("--deploy precisa de HDP_SITE_URL (ex.: https://besttimetofish.example)")
    species = load_species()
    lakes = json.loads((DATA / "tpwd_lakes.json").read_text(encoding="utf-8"))
    places = json.loads((DATA / "places.json").read_text(encoding="utf-8"))["places"]
    now = datetime.now(TZ)
    env = make_env(now)

    def fishname(sid):
        return NAMES.get(sid, species[sid]["common_name"].capitalize() if sid in species else sid)

    env.globals["fishname"] = fishname
    macros = env.get_template("macros.html").make_module({})
    common = {"brand": BRAND, "built": now.strftime("%b %d, %Y, ") + hm(now) + " CT", "collected": run_id,
              "idx": "" if deploy else "index.html", "ext": "/" if deploy else ".html"}
    page_paths = []
    results, resumo, rows_json = [], [], {}

    def timed(r, fn):
        """Manchetes por hora (sem repetir a anterior) para o navegador escolher pela hora do leitor."""
        out, last = [], None
        for t, o in r["heroes"]:
            h = str(fn(o))
            if h != last:
                out.append({"t": t.isoformat(), "h": h})
                last = h
        return out
    def nows(raw):
        """Tempo "agora" por hora (previsão horária do NWS); o navegador escolhe a hora de quem lê."""
        out, first = [], None
        for per in (raw.get("hourly") or {}).get("periods", [])[:72]:
            out.append({"t": per["start"], "h": str(macros.nowwx(per))})
            if datetime.fromisoformat(per["start"]) <= now:
                first = per
        return out, first

    # 1ª passada: monta todos os lugares (a aba Places de cada página lista os 20)
    for p in places:
        if p["id"] not in run:
            print(f"  {p['id']}: sem coleta")
            continue
        r = build(p, run[p["id"]], species, lakes, now)
        r["url"] = f"best-time-to-fish/{p['id']}/" + ("" if deploy else "index.html")
        page_paths.append(f"best-time-to-fish/{p['id']}/")
        r["hero_json"] = timed(r, macros.hero)
        rows_json[p["id"]] = timed(r, lambda o, r=r: macros.row(r, o))
        results.append(r)
    spots = [{"id": r["place"]["id"], "name": r["place"]["name"], "near": r["place"].get("near", ""),
              "kind": r["place"]["kind"], "lat": r["place"]["lat"], "lon": r["place"]["lon"], "url": r["url"]}
             for r in results]
    # 2ª passada: páginas de lugar + resumo em português
    for r in results:
        p = r["place"]
        name = p.get("short") or p["name"]
        now_json, now_first = nows(run[p["id"]])
        out = SITE / "best-time-to-fish" / p["id"]
        out.mkdir(parents=True, exist_ok=True)
        html = env.get_template("place.html").render(
            r=r, spots=spots, now_json=now_json, now_first=now_first,
            root="../../", canonical=f"{SITE_URL}/best-time-to-fish/{p['id']}/" if deploy else None,
            title=f"Best Time to Fish {name} Today", title_h1=f"Best Time to Fish {name}",
            description=f"Today's best fishing time at {p['name']}, Texas: hourly score, fish, bait, solunar table"
                        f"{', tides' if p['kind'] == 'coast' else ''} and NWS weather for the next 7 days.", **common)
        (out / "index.html").write_text(html, encoding="utf-8")
        h = r["head"]
        if h:
            dia = "Hoje" if h["date"] == now.date() else "Amanhã" if (h["date"] - now.date()).days == 1 else str(h["date"])
            t = h["best_start"]
            isca = PT_BAIT.get(h["bait"]["short"], h["bait"]["short"]) if h["bait"]["short"] else "sem isca com fonte"
            resumo.append(f"{dia}, {t.hour}h{t.minute:02d}, {p['name']}, {PT_FISH.get(h['species'], h['species'])}, "
                          f"{isca}. Nota {int(round(h['score']))}." + (" (dia fraco)" if h.get("weak") else ""))
        else:
            resumo.append(f"{p['name']}: sem manchete ({'sem dado do TPWD' if r['no_lake_data'] else 'sem previsão'}).")
    (SITE / "index.html").write_text(env.get_template("index.html").render(
        results=results, rows_json=rows_json, root="", canonical=f"{SITE_URL}/" if deploy else None, title="Best Time to Fish in Texas Today | Hourly Fishing Times",
        description="Best time to fish today at 20 Texas lakes and coastal spots: hourly score, fish, bait, "
                    "solunar tables, tides and NWS weather.", **common), encoding="utf-8")
    # prévia local: site/how-we-score.html; publicação: site/how-we-score/index.html (URL limpa em qualquer host)
    how_file = SITE / "how-we-score" / "index.html" if deploy else SITE / "how-we-score.html"
    how_file.parent.mkdir(parents=True, exist_ok=True)
    how_file.write_text(env.get_template("how.html").render(
        root="../" if deploy else "", canonical=f"{SITE_URL}/how-we-score/" if deploy else None,
        title="How We Score Fishing Times (Do Solunar Tables Work?)",
        description="Exactly how much light, tides, solunar periods, pressure and weather weigh in our fishing score.",
        **common), encoding="utf-8")
    if deploy:
        # sem 404.html o Pages trata o site como SPA e devolve a página inicial com 200 para qualquer endereço
        (SITE / "404.html").write_text(env.get_template("notfound.html").render(
            results=results, root=f"{SITE_URL}/", canonical=None, title="Page Not Found | Best Time to Fish",
            description="Page not found.", **common), encoding="utf-8")
        _deploy_files(now, run_id, ["", "how-we-score/"] + page_paths)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "RESUMO_PT.txt").write_text("\n".join(resumo) + "\n", encoding="utf-8")
    print(f"Gerado: {len(results)} páginas de lugar + índice + como pontuamos (coleta {run_id})")
    print("\n".join(resumo))


def _deploy_files(now, run_id, paths):
    """Arquivos de publicação. versao.txt = a trava do site do DL: abrir /versao.txt e ver a hora."""
    day = now.strftime("%Y-%m-%d")
    urls = "".join(f"<url><loc>{SITE_URL}/{p}</loc><lastmod>{day}</lastmod></url>" for p in paths)
    (SITE / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                      + urls + "</urlset>\n", encoding="utf-8")
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    # página muda a cada 2 h: o navegador e a borda guardam no máximo 10 min
    (SITE / "_headers").write_text("/*\n  Cache-Control: public, max-age=600\n", encoding="utf-8")
    (SITE / "versao.txt").write_text(f"{now.isoformat(timespec='minutes')} coleta {run_id}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
