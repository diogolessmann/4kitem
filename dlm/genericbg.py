# -*- coding: utf-8 -*-
"""Shim do genericbg da Rádio: arsenal próprio de fundos em dlm/bg/<slug>[-N].jpg (subconjunto
copiado da Rádio em 20/set: transito, obra, acidente_rodovia, economia, comercio, supermercado,
justica, cidade_geral). Aposentar imagem = apagar o arquivo."""
import os

BG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bg")
EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _file(slug, seed=0):
    if not slug or not os.path.isdir(BG_DIR):
        return None
    cands = []
    for ext in EXTS:
        base = os.path.join(BG_DIR, slug + ext)
        if os.path.exists(base):
            cands.append(base)
        i = 1
        while True:
            v = os.path.join(BG_DIR, f"{slug}-{i}{ext}")
            if not os.path.exists(v):
                break
            cands.append(v)
            i += 1
    if not cands:
        return None
    return sorted(cands)[seed % len(cands)]
