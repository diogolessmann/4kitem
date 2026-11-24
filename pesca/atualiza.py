"""Atualização automática: coleta -> site de publicação -> conferência. Não sobe nada sozinho.

Uso:  HDP_SITE_URL=https://dominio python atualiza.py
      HDP_SITE_URL=https://dominio python atualiza.py --sem-coleta   (teste: usa a última coleta)
Sai com erro (código 1) se a coleta ou o site vierem ruins. Aí o fluxo do GitHub NÃO sobe,
e o site que está no ar continua valendo: as manchetes cobrem 48 h e o JS troca pela hora do leitor.
Quem sobe é o passo seguinte do fluxo (.github/workflows/atualiza.yml), com `wrangler pages deploy site`.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import coleta
from hdp.config import CACHE, DATA, SITE, SITE_URL

MIN_PREVISAO = 15        # de 20 lugares; abaixo disso o NWS está fora e é melhor manter o site anterior
PROIBIDO = (".md", ".py", ".json", ".txt.bak", ".bsp")   # documento interno nunca vai junto (lição do site do DL)


def falha(msg):
    print(f"PAROU: {msg}")
    sys.exit(1)


def confere_coleta(run_id):
    places = json.loads((DATA / "places.json").read_text(encoding="utf-8"))["places"]
    rd = CACHE / "runs" / run_id
    com_previsao = 0
    for p in places:
        f = rd / f"{p['id']}.json"
        if not f.exists():
            continue
        rec = json.loads(f.read_text(encoding="utf-8"))
        com_previsao += bool((rec.get("grid") or {}).get("hours"))
    print(f"Conferência da coleta: {com_previsao}/{len(places)} lugares com previsão do NWS")
    if com_previsao < MIN_PREVISAO:
        falha(f"só {com_previsao} lugares com previsão (mínimo {MIN_PREVISAO}); site anterior fica no ar")
    return places


def confere_site(places):
    erros = []
    paginas = 0
    for p in places:
        f = SITE / "best-time-to-fish" / p["id"] / "index.html"
        if not f.exists():
            erros.append(f"falta a página de {p['id']}")
            continue
        html = f.read_text(encoding="utf-8")
        paginas += 1
        m = re.search(r'<script[^>]*id="heroes"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            erros.append(f"{p['id']}: sem bloco de manchetes")
        else:
            try:
                n_heroes = len(json.loads(m.group(1)))
            except ValueError:
                n_heroes = -1
            if n_heroes < 1:
                erros.append(f"{p['id']}: manchetes vazias ou quebradas")
        if f'<link rel="canonical" href="{SITE_URL}/best-time-to-fish/{p["id"]}/">' not in html:
            erros.append(f"{p['id']}: canonical errado")
        if re.search(r">\s*(None|nan|undefined)\s*<", html):
            erros.append(f"{p['id']}: valor vazio impresso (None/nan)")
        if 'class="regs"' in html:
            erros.append(f"{p['id']}: texto do TPWD copiado (regras palavra por palavra)")
    for nome in ("index.html", "404.html", "how-we-score/index.html", "sitemap.xml", "robots.txt", "_headers", "versao.txt"):
        if not (SITE / nome).exists():
            erros.append(f"falta {nome}")
    if (SITE / "sitemap.xml").exists():
        n = (SITE / "sitemap.xml").read_text(encoding="utf-8").count("<loc>")
        if n != paginas + 2:
            erros.append(f"sitemap com {n} endereços (esperado {paginas + 2})")
    estranhos = [str(x.relative_to(SITE)) for x in SITE.rglob("*") if x.is_file() and x.name.endswith(PROIBIDO)]
    if estranhos:
        erros.append(f"arquivo interno na pasta do site: {estranhos[:5]}")
    print(f"Conferência do site: {paginas} páginas de lugar, {len(erros)} problemas")
    if erros:
        falha("; ".join(erros[:8]))


def main():
    if not SITE_URL:
        falha("falta HDP_SITE_URL (o endereço do site, ex.: https://besttimetofish.com)")
    if "--sem-coleta" in sys.argv:    # teste local: usa a última coleta em vez de baixar de novo
        run_id = (CACHE / "latest.txt").read_text(encoding="utf-8").strip()
    else:
        run_id = coleta.main()
    places = confere_coleta(run_id)
    if SITE.exists() and SITE.name.startswith("site"):   # o site é gerado: começa limpo (trava: só pasta site*)
        shutil.rmtree(SITE)
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("gera.py")), "--deploy", run_id])
    if r.returncode:
        falha(f"gera.py saiu com código {r.returncode}")
    confere_site(places)
    faxina_coletas(run_id)
    print(f"OK: site/ pronto para subir ({(SITE / 'versao.txt').read_text(encoding='utf-8').strip()})")


def faxina_coletas(atual, fica=3):
    """Cada coleta ocupa ~2 MB; a cada 2 h, sem faxina, enche o volume do 4kitem em um mês. Fica a atual + as 2 anteriores."""
    runs = sorted((d for d in (CACHE / "runs").iterdir() if d.is_dir()), key=lambda d: d.name)
    for d in runs[:-fica]:
        if d.name != atual:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
