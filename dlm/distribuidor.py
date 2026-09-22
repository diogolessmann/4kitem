# -*- coding: utf-8 -*-
"""Shim do distribuidor da Rádio: env tolerante, Graph API, story 9:16, dedup por stem.
PUBLIC_BASE_URL = onde o Meta busca as imagens (4kitem serve /static/social)."""
import os
import re
import time
import unicodedata

import requests

from dlm import gen_instagram as gi

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # raiz do 4kitem
DATA_DIR = os.environ.get("DATA_DIR", _BASE_DIR)
DB_PATH = os.path.join(DATA_DIR, "dlm.db")                                  # banco próprio do motor
PUBLIC_BASE_URL = os.environ.get("DL_PUBLIC_BASE", "https://www.4kitem.com.br").rstrip("/")
PUBLIC_IMG_DIR = os.path.join(_BASE_DIR, "static", "social")
GRAPH = "https://graph.facebook.com/v21.0"


def _env(name, default=""):
    """Le variavel de ambiente tolerando espacos acidentais no nome ou no valor."""
    v = os.environ.get(name)
    if not v:
        target = name.strip()
        for k, val in os.environ.items():
            if k.strip() == target and val:
                v = val
                break
    if v is None:
        return default
    v = v.strip()
    return v if v else default


# tokens da Rádio (só usados se alguém publicar "na Rádio" a partir daqui — ex.: publish_reel_dest)
META_PAGE_TOKEN = _env("RADIO_PAGE_TOKEN") or _env("META_PAGE_TOKEN")
META_IG_USER_ID = _env("RADIO_IG_USER_ID") or _env("META_IG_USER_ID")
META_PAGE_ID = _env("RADIO_PAGE_ID") or _env("META_PAGE_ID")


def _graph_post(url, data, tries=2):
    last = ""
    for _ in range(tries):
        r = requests.post(url, data=data, timeout=60)
        if r.ok:
            return r.json()
        last = r.text[:400]
        if r.status_code in (400, 500) and ("media" in last.lower() or "process" in last.lower()):
            time.sleep(4)
            continue
        break
    raise RuntimeError(f"Graph {url.rsplit('/', 1)[-1]} -> {last}")


def _story_image(slide_path, out_path):
    """Slide 1080x1350 -> quadro 9:16 (1080x1920) pro Story."""
    from PIL import Image
    canvas = Image.new("RGB", (1080, 1920), gi.BG)
    im = Image.open(slide_path).convert("RGB")
    if im.width != 1080:
        im = im.resize((1080, int(im.height * 1080 / im.width)))
    if im.height > 1920:
        top = (im.height - 1920) // 2
        im = im.crop((0, top, 1080, top + 1920))
    canvas.paste(im, (0, max(0, (1920 - im.height) // 2)))
    canvas.save(out_path, "JPEG", quality=90)
    return out_path


_DEDUP_STOP = set((
    "de da do das dos a o e os as um uma uns umas no na nos nas ao aos que com por "
    "para pra apos sobre entre ate sem sob desde como mais menos muito pouco urgente "
    "video veja confira saiba assista foto fotos imagem imagens noticia em e é foi sao "
    "ser tem ter um dois tres anos ano hoje agora cidade regiao").split())


def _stem_keys(text):
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    keys = set()
    for w in re.findall(r"[a-z0-9]+", t):
        if len(w) < 3 or w in _DEDUP_STOP:
            continue
        keys.add(w[:5])
    return keys


def _overlap(a, b):
    ka, kb = _stem_keys(a), _stem_keys(b)
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / min(len(ka), len(kb))
