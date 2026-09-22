# -*- coding: utf-8 -*-
"""Shim do scraper da Rádio: helpers de texto/RSS que o mobilidade.py usa."""
import json
import logging
import re
from datetime import datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_LANG_ESTRANHA = re.compile(
    r"\b(der|die|das|und|ist|zum|zur|auf|dem|den|mit|für|nicht|sich|immer|viel|"
    r"the|and|with|from|this|that|will|has|have|his|her|their|about)\b", re.I)


def _idioma_estranho(texto):
    return len(_LANG_ESTRANHA.findall(texto or "")) >= 2


_FONTES_CONHECIDAS = (r"Money Times|NSC Total|Gazeta do Povo|JDV(?: - Jornal do Vale)?|Jornal do Vale|"
                      r"OneFootball|sportlife\.com\.br|ND Mais|G1(?: [A-Za-zÀ-ú ]+)?|OCP News|"
                      r"Di[áa]rio da Jaragu[áa]|Portal de Schroeder|CNN Brasil|UOL|Terra|Folha|Estad[ãa]o|"
                      r"Metr[óo]poles|Poder360|InfoMoney|Valor|Exame|Lance!?|GE|ge\.globo|Agência Brasil")
_SUFIXO_FONTE = re.compile(r"\s+[-–—|]\s+(?:" + _FONTES_CONHECIDAS + r")\s*$", re.I)


def _tira_fonte_titulo(title, gnews=False):
    t = " ".join((title or "").split())
    if gnews:
        m = re.search(r"\s+-\s+[^-]{2,60}$", t)
        if m and len(t[:m.start()].split()) >= 4:
            t = t[:m.start()].rstrip()
    m = _SUFIXO_FONTE.search(t)
    if m:
        t = t[:m.start()].rstrip()
    return t


def _ano_velho_no_titulo(title):
    anos = re.findall(r"\b(20[12]\d)\b", title or "")
    if not anos:
        return False
    if any(int(a) >= datetime.now().year for a in anos):
        return False
    return not re.search(r"\b(anos?|desde|anivers[áa]rio|edi[çc][ãa]o|hist[óo]ria|relembr|retrospectiva)\b",
                         title or "", re.I)


_RODAPE_FEED = re.compile(
    r"(The post\b.{0,300}?\bappeared first on\b[^.]{0,80}\.?|"
    r"O post\b.{0,300}?\bapareceu primeiro em\b[^.]{0,80}\.?|"
    r"Leia mais em[^.]{0,80}\.?)\s*$", re.IGNORECASE | re.DOTALL)


def clean_html(text):
    if not text:
        return ""
    txt = BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()
    return _RODAPE_FEED.sub("", txt).strip()


_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": "https://www.google.com/",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}
_TEXT_LIXO = re.compile(
    r"leia (mais|tamb[eé]m)|compartilh|publicidade|continua ap[oó]s|aceit[ae].*cookies|"
    r"(siga|participe|receba).*(instagram|whatsapp|telegram|grupo|not[ií]cias)|"
    r"fale conosco|grupo no whatsapp|todos os direitos|clique aqui|"
    r"\bfoto:|\bfonte:|inscreva-se|newsletter", re.IGNORECASE)


def fetch_article_text(link, min_total=180, max_total=1400):
    if not link or not link.startswith(("http://", "https://")):
        return None
    try:
        r = requests.get(link, headers=_BROWSER_HEADERS, timeout=8, verify=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        for tag in soup(["script", "style", "nav", "aside", "footer", "header", "form", "figure"]):
            tag.decompose()
        scope = soup.find("article")
        if scope is None:
            best, best_len = None, 0
            for cont in soup.find_all(["div", "section", "main"]):
                tlen = len(cont.get_text(strip=True))
                if tlen > best_len:
                    best, best_len = cont, tlen
            scope = best or soup
        partes, total, seen = [], 0, set()
        for frag in scope.stripped_strings:
            t = re.sub(r"\s+", " ", frag).strip()
            if len(t) < 40 or t in seen or _TEXT_LIXO.search(t):
                continue
            seen.add(t)
            partes.append(t)
            total += len(t)
            if total >= max_total:
                break
        corpo = " ".join(partes).strip()
        if len(corpo) >= min_total:
            return corpo[:max_total]
    except Exception as e:
        logger.info(f"corpo da matéria falhou ({link[:50]}): {e}")
    return None


_UA_NAV = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}


def _resolve_gnews(link):
    """Destrava a URL real por trás de news.google.com/rss/articles/... (fail-open)."""
    try:
        m = re.search(r"/articles/([^?/]+)", link)
        if not m:
            return link
        art_id = m.group(1)
        pg = requests.get(f"https://news.google.com/articles/{art_id}", headers=_UA_NAV, timeout=15).text
        sg = re.search(r'data-n-a-sg="([^"]*)"', pg).group(1)
        ts = re.search(r'data-n-a-ts="([^"]*)"', pg).group(1)
        payload = ["Fbv4je",
                   f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
                   f'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                   f'"{art_id}",{ts},"{sg}"]']
        r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                          headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8", **_UA_NAV},
                          data="f.req=" + quote(json.dumps([[payload]])), timeout=15)
        real = json.loads(json.loads(r.text.split("\n\n")[1])[:-2][0][2])[1]
        if real and isinstance(real, str) and real.startswith("http"):
            return real
    except Exception as e:
        logger.info(f"gnews: não destravei a URL real ({type(e).__name__})")
    return link
