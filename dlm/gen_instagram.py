# -*- coding: utf-8 -*-
"""Shim do gen_instagram da Rádio: só o que marcas.py/mobilidade.py usam (fonte, wrap, pill, linhas,
degradê). Fontes empacotadas em dlm/fonts (DejaVu, acentos completos, livre)."""
import os
import re

from PIL import Image, ImageFont

W, H = 1080, 1350
BG = (17, 18, 24)
BLACK = (0, 0, 0)
WHITE = (245, 245, 247)

FONTS = os.environ.get("FONTS_DIR", "C:/Windows/Fonts" if os.name == "nt" else "")
_BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONT_FALLBACK = {
    "regular": [os.path.join(_BUNDLED, "DejaVuSans.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "bold": [os.path.join(_BUNDLED, "DejaVuSans-Bold.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "impact": [os.path.join(_BUNDLED, "DejaVuSans-Bold.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}


def _first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def font(size, bold=True, impact=False):
    kind = "impact" if impact else ("bold" if bold else "regular")
    win_name = {"impact": "impact.ttf", "bold": "arialbd.ttf", "regular": "arial.ttf"}[kind]
    candidates = [os.path.join(FONTS, win_name)] if FONTS else []
    candidates += _FONT_FALLBACK[kind]
    path = _first_existing(candidates)
    try:
        if path:
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002300-\U000023FF\U0000FE00-\U0000FE0F"
    "\U0000200D\U000020E3\U00002122\U00002139]+", flags=re.UNICODE)


def _semoji(s):
    if not s:
        return s
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", s)).strip()


def wrap(draw, text, fnt, max_w):
    words = _semoji(text).split()
    lines, cur = [], ""
    for wd in words:
        test = (cur + " " + wd).strip()
        if draw.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def draw_lines(draw, lines, fnt, x, y, fill, line_h, stroke=0, stroke_fill=BLACK):
    for ln in lines:
        draw.text((x, y), ln, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        y += line_h
    return y


def pill(draw, x, y, text, fnt, bg, fg, pad_x=26, pad_y=14):
    text = _semoji(text)
    w = draw.textlength(text, font=fnt)
    asc, desc = fnt.getmetrics()
    th = asc + desc
    draw.rounded_rectangle([x, y, x + w + pad_x * 2, y + th + pad_y * 2], radius=(th + pad_y * 2) // 2, fill=bg)
    draw.text((x + pad_x, y + pad_y), text, font=fnt, fill=fg)
    return x + w + pad_x * 2


def gradient_overlay(img, top=0.35, bottom=0.92):
    grad = Image.new("L", (1, H))
    for y in range(H):
        t = y / H
        grad.putpixel((0, y), int(255 * (top + (bottom - top) * (t ** 1.5))))
    alpha = grad.resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), BLACK), img, alpha)
