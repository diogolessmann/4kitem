"""Conferência automática das fichas: cada `quote` precisa existir no texto da `source_url`.

Independente do coletor que montou as fichas. Baixa cada fonte uma vez (HTML ou PDF), normaliza espaço,
caixa e aspas, e procura a citação. Uso: python verifica_fichas.py
"""
import io
import json
import re
import sys
import unicodedata

import requests

from hdp.config import FICHAS

UA = {"User-Agent": "Mozilla/5.0 (HoraDePescarPrototype verification)"}


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("­", "")
    s = s.replace("°", "o").replace("º", "o")  # PDFs da SRAC extraem "°C" como "oC"
    s = re.sub(r"-\s+", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def page_text(url, cache={}):
    if url in cache:
        return cache[url]
    try:
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code != 200:
            cache[url] = None
            return None
        if url.lower().endswith(".pdf") or r.headers.get("content-type", "").startswith("application/pdf"):
            from pypdf import PdfReader
            txt = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages)
        else:
            t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
            import html
            txt = html.unescape(re.sub(r"<[^>]+>", " ", t))
        cache[url] = norm(txt)
    except Exception:
        cache[url] = None
    return cache[url]


def walk(obj, path=""):
    if isinstance(obj, dict):
        if obj.get("source_url") and obj.get("quote"):
            yield path, obj
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


def main():
    ok = bad = unreach = 0
    for fn in ("fresh.json", "salt.json"):
        for sp in json.loads((FICHAS / fn).read_text(encoding="utf-8")):
            for path, o in walk(sp, sp["id"]):
                txt = page_text(o["source_url"])
                if txt is None:
                    unreach += 1
                    print(f"  SEM ACESSO  {path}  {o['source_url'][:90]}")
                    continue
                q = norm(re.sub(r"\(.*?extracted.*?\)", "", o["quote"]))
                q_loose = re.sub(r"[^a-z0-9]", "", q)
                if q in txt or (len(q_loose) > 20 and q_loose in re.sub(r"[^a-z0-9]", "", txt)):
                    ok += 1
                else:
                    bad += 1
                    print(f"  NÃO ACHEI   {path}: \"{o['quote'][:80]}\"  {o['source_url'][:80]}")
    print(f"\nCitações: {ok} achadas, {bad} não achadas, {unreach} fontes sem acesso")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
