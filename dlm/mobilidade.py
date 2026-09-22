# -*- coding: utf-8 -*-
"""
mobilidade.py — Motor do 3º perfil: MOBILIDADE NORTE SC (decisão do dono, 19/set/2026).

A Rádio fala de tudo; o perfil do despachante é vitrine. Este perfil junta QUEM TEM CARRO, MOTO
OU SCOOTER no Vale com o que mexe no bolso e na rua deles. Sem venda no conteúdo — a venda é
1 post/dia, ROTULADO ("DO BALCÃO DO DESPACHANTE"). MiroFish 19/set: propaganda disfarçada mata o
perfil; lei sem número de resolução vira "guia sem rigor"; anunciante quer cupom/CPF, não alcance.

5 CATEGORIAS (aprovadas "adorei, aprovadíssimo"):
  bolso    40%  lei · prazo · valor (CONTRAN, DETRAN/SC, SEF/SC IPVA, recall, multa, CNH)
  rua      20%  radar · obra · desvio (BR-280, BR-101, ruas das 6 cidades) — vem do banco da Rádio
  preco    20%  gasolina · pneu · óleo (ANP, promoção de parceiro local)
  novidade 10%  lançamento · moto · elétrico · emplacamentos (nacional SÓ com a fatura local dentro)
  nosso    10%  despachante · defesa · scooter · curso — ROTULADO, 1/dia, aponta pra página do site

CUSTO MÍNIMO (ordem do dono): só card e carrossel (sem reel, sem TTS); fundo = arsenal próprio
(static/bg) ou pool assets/mobilidade/<cat>/; texto = cerebro.completar (Gemini centavos → Groq).

Foco: Norte de SC (Jaraguá, Schroeder, Guaramirim, Corupá, Massaranduba, Joinville).

ENV (Railway):  MOB_PAGE_TOKEN · MOB_IG_USER_ID  (token da Página vinculada ao IG novo)
                MOB_COLLAB="radioscnews"  usernames (vírgula) marcados como colaboradores
                MOB_COLLAB_SLOTS="0,1"  slots que saem em collab (a Rádio aceita no app; 2/dia é o que cabe)
                MOB_HORAS="7,10,12,15,18,20"  horários (default)   MOB_ON=1 liga no scheduler
                MOB_TAG="SC NEWS MOBILIDADE"  MOB_HANDLE="@scnewsmobilidade" (batismo é do dono)

USO local:  python mobilidade.py coletar          # coleta e mostra a fila por categoria
            python mobilidade.py gerar bolso      # gera 1 carrossel (preview em instagram_posts/)
            python mobilidade.py gerar nosso
            python mobilidade.py postar bolso     # publica (precisa dos tokens)
            python mobilidade.py slot 0..5        # o que o scheduler faz nesse slot
"""
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import feedparser
import requests

from dlm import gen_instagram as gi
from dlm import distribuidor as dist

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

W, H = 1080, 1350
OUT_BASE = os.path.join(os.environ.get("DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dlm_posts")
DATA_DIR = dist.DATA_DIR
DB_PATH = dist.DB_PATH
HERE = os.path.dirname(os.path.abspath(__file__))
POOL_DIR = os.path.join(HERE, "assets", "mobilidade")     # assets/mobilidade/<cat>/*.jpg (opcional)

CIDADES = ["Jaraguá do Sul", "Schroeder", "Guaramirim", "Corupá", "Massaranduba", "Joinville"]

# ----------------------------------------------------------------- marca (tema visual)
# asfalto escuro + âmbar de placa de trânsito + azul. Tudo que marcas.py precisa pra reaproveitar
# os slides da série (slide_capa/slide_conteudo/slide_site) está aqui.
BRAND = {
    "nome": dist._env("MOB_NOME", "SC News Mobilidade"),
    "brand_tag": dist._env("MOB_TAG", "SC NEWS MOBILIDADE"),
    "instagram": dist._env("MOB_HANDLE", "@scnewsmobilidade"),
    "site": "dldespachante.com.br",
    "whats": "(47) 99716-2967",
    "bg": (14, 16, 20), "card": (24, 27, 34), "accent": (255, 196, 0),
    "accent2": (0, 140, 210), "white": (245, 247, 250), "muted": (165, 172, 182),
    "env": {"token": "MOB_PAGE_TOKEN", "ig": "MOB_IG_USER_ID", "page": "MOB_PAGE_ID"},
    "ig_only": True,
    "hashtags": ["#nortedesc", "#jaraguadosul", "#joinville", "#schroeder", "#guaramirim", "#corupa",
                 "#transito", "#detransc", "#ipva", "#carros", "#motos", "#mobilidade"],
    # voz usada SÓ no post 'nosso' (reaproveita marcas.groq_caption)
    "voz": ("Você é o social media do Despachante Lessmann (Schroeder/SC) escrevendo um post "
            "ROTULADO como publicidade dentro de um perfil de notícias de mobilidade. Fale claro, "
            "sem juridiquês, sem medo como isca, sem prometer resultado. NUNCA se passe por DETRAN."),
}

CATS = {
    "bolso":    {"badge": "MEXEU NO BOLSO",   "slugs": ["economia", "justica", "comercio"]},
    "rua":      {"badge": "MEXEU NA RUA",     "slugs": ["transito", "obra", "acidente_rodovia"]},
    "preco":    {"badge": "PREÇO DA SEMANA",  "slugs": ["comercio", "supermercado", "economia"]},
    "novidade": {"badge": "NOVIDADE",         "slugs": ["transito", "cidade_geral"]},
    "nosso":    {"badge": "DO BALCÃO DO DESPACHANTE", "slugs": []},
}

# grade do dia: 6 slots. 'novidade' só ter/qui/sáb (≈10% da semana). Sem matéria → cai pra bolso;
# bolso vazio → slot pula (slot vazio é seguro, igual na Rádio).
GRADE = ["bolso", "rua", "preco", "bolso", "novidade", "nosso"]
NOVIDADE_DIAS = (1, 3, 5)          # weekday: ter, qui, sáb


def horas():
    hs = [int(h) for h in dist._env("MOB_HORAS", "7,10,12,15,18,20").split(",") if h.strip().isdigit()]
    return hs or [7, 10, 12, 15, 18, 20]


# ----------------------------------------------------------------- fontes (Google News RSS)
def _gn(q):
    return f"https://news.google.com/rss/search?q={quote(q)}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


FEEDS = {
    "bolso": [
        (_gn('CONTRAN resolução trânsito'), "CONTRAN"),
        (_gn('Senatran trânsito regra'), "Senatran"),
        (_gn('"Detran-SC" OR "DETRAN/SC" OR "Detran SC"'), "DETRAN/SC"),
        (_gn('IPVA "Santa Catarina"'), "SEF/SC"),
        (_gn('licenciamento veículo "Santa Catarina"'), "SEF/SC"),
        (_gn('multa de trânsito nova regra'), "Trânsito"),
        (_gn('recall veículos Senacon'), "Senacon"),
        (_gn('CNH mudança regra habilitação'), "CNH"),
        (_gn('placa Mercosul emplacamento regra'), "Emplacamento"),
    ],
    # 22/set: MEDIDO (itens frescos em 7 dias). A fonte antiga era o banco da Rádio, que saiu
    # com a migração pro 4kitem — estas buscas foram testadas uma a uma e só ficou o que traz.
    "rua": [
        (_gn('"BR-280"'), "BR-280"),                                        # 48 frescos/7d — a novela do Vale
        (_gn('acidente OR congestionamento "BR-280" OR "SC-108" OR "SC-416"'), "Rodovias"),   # 18
        (_gn('radar de velocidade "Santa Catarina"'), "Radar"),             # 16
        (_gn('"BR-101" Joinville OR "Barra Velha" OR "São Francisco do Sul"'), "BR-101"),     # 9
        (_gn('trânsito "Jaraguá do Sul"'), "Trânsito local"),               # 9
        (_gn('trânsito Joinville'), "Trânsito local"),                      # 8
        (_gn('trânsito OR obra Corupá'), "Trânsito local"),                 # 6
        (_gn('obra OR pavimentação OR asfalto rua "Jaraguá do Sul"'), "Obras"),               # 2
        (_gn('trânsito OR obra OR rua Guaramirim OR Schroeder SC'), "Trânsito local"),        # 0 hoje: a
        # imprensa não cobre essas duas — o que vier delas virá de foto no zap (rodapé do post)
    ],
    "preco": [   # 19/set: testado — estas trazem item fresco (Procon Joinville, ANP semanal)
        (_gn('gasolina preço Joinville OR Jaraguá OR Blumenau'), "Combustível"),
        (_gn('Procon pesquisa preço combustível Santa Catarina'), "Procon"),
        (_gn('ANP preço gasolina semana'), "ANP"),
        (_gn('ANP preço diesel etanol semana'), "ANP"),
        (_gn('preço pneu OR óleo OR revisão carro promoção'), "Manutenção"),
    ],
    "novidade": [
        (_gn('lançamento carro Brasil preço 2026'), "Lançamento"),
        (_gn('moto lançamento Brasil preço'), "Motos"),
        (_gn('carro elétrico Brasil preço lançamento'), "Elétricos"),
        (_gn('Fenabrave emplacamentos mês'), "Fenabrave"),
        (_gn('scooter elétrica OR ciclomotor CONTRAN 996'), "Scooter"),
    ],
}

# o que faz a matéria SER de mobilidade (bolso/preco/novidade são nacionais: sem isso, entra lixo)
_TEMA = re.compile(
    r"contran|senatran|denatran|detran|ipva|licenciamento|crlv|multa|infra[çc][ãa]o|cnh|habilita[çc][ãa]o|"
    r"recall|placa|emplacamento|renavam|radar|ped[áa]gio|combust[íi]vel|gasolina|etanol|diesel|"
    r"ve[íi]culo|carro|moto|scooter|ciclomotor|el[ée]tric|montadora|concession[áa]ria|pneu|"
    r"[óo]leo|revis[ãa]o|fenabrave|abraciclo|tr[âa]nsito|rodovia|br-?\d{2,3}|sc-?\d{3}", re.I)

# fora: crime/violência (não é perfil policial), política partidária, outro estado sem SC/Brasil
_VETO = re.compile(
    r"homic[íi]dio|assassin|estupr|tr[áa]fico|preso|presa\b|delegacia|tiro|morte|morreu|morto|"
    r"vereador|deputad|senador|prefeit|elei[çc][ãa]o|partido|bolsonaro|lula|"
    r"aposta|bet\b|loteria|f[óo]rmula 1|\bf1\b|stock car|rally|iphone|smartphone|celular", re.I)
_OUTRO_UF = re.compile(
    r"detran[-\s]?(sp|rj|mg|rs|pr|ba|go|df|pe|ce|es|am|pa|mt|ms|pb|rn|al|se|pi|ma|to|ro|ac|ap|rr)\b|"
    r"\b(s[ãa]o paulo|paulista|rio de janeiro|carioca|minas gerais|mineir|paran[áa]|curitiba|"
    r"rio grande do sul|ga[úu]ch|porto alegre|bahia|goi[áa]s|goi[âa]nia|distrito federal|bras[íi]lia|"
    r"pernambuco|recife|cear[áa]|fortaleza|esp[íi]rito santo|amazonas|manaus|par[áa]\b|bel[ée]m|"
    r"mato grosso|cuiab[áa]|campo grande|salvador|belo horizonte|nordeste|\bnatal\b|rio grande do norte|"
    r"piau[íi]|teresina|maranh[ãa]o|s[ãa]o lu[íi]s|sergipe|aracaju|alagoas|macei[óo]|para[íi]ba|jo[ãa]o pessoa|"
    r"tocantins|\bpalmas\b|rond[ôo]nia|porto velho|\bacre\b|rio branco|roraima|boa vista|amap[áa]|macap[áa])\b", re.I)
_SC_OU_BR = re.compile(r"santa catarina|catarinense|\bsc\b|joinville|jaragu[áa]|schroeder|guaramirim|"
                       r"corup[áa]|massaranduba|brasil|nacional|contran|senatran|todo o pa[íi]s|"
                       r"recall|fenabrave|abraciclo|montadora|lan[çc]amento", re.I)


def _key(title):
    return hashlib.sha1(" ".join(sorted(str(k) for k in dist._stem_keys(title))).encode("utf-8")).hexdigest()[:16]


# 22/set: acidente que TRAVA A PISTA é informação de trânsito (entra); acidente com gente
# ferida/morta é boletim policial (fora) — o Roda Norte é perfil de mobilidade, não de plantão.
_VITIMA = re.compile(
    r"morr|morte|morto|[óo]bito|fatal|v[íi]tima|ferid|amputa|decapit|socorrid|resgatad[oa]|"
    r"UTI\b|estado grave|hospitalizad|fratur|traumatism|entubad|desencarcerad|"
    r"perde[ru]?\s+(?:o|a|um|uma)\s+(?:dedo|bra[çc]o|perna|m[ãa]o|p[ée]|vis[ãa]o|olho)|"
    r"(?:levad|encaminhad)[oa]s?\s+(?:ao|para o|pro)\s+hospital|\bsamu\b|bombeiros socorr", re.I)


def _relevante(cat, title, summary):
    txt = f"{title} {summary}"
    if not _TEMA.search(txt):
        return False, "sem tema"
    if _VETO.search(title):
        return False, "veto"
    if _VITIMA.search(title):
        return False, "vítima (vai pro plantão, não pro perfil)"
    if cat != "rua" and _OUTRO_UF.search(title) and not _SC_OU_BR.search(title):
        return False, "outro estado"
    return True, ""


# ----------------------------------------------------------------- banco próprio (mob_news)
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS mob_news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        k TEXT UNIQUE, cat TEXT, title TEXT, summary TEXT, link TEXT, source TEXT,
        published_at TEXT, created_at TEXT, posted_at TEXT, ig_media_id TEXT, radio_id INTEGER)""")
    conn.commit()
    return conn


def _recentes(conn, dias=7):
    since = (datetime.now() - timedelta(days=dias)).isoformat()
    return [r["title"] for r in conn.execute(
        "SELECT title FROM mob_news WHERE created_at>=?", (since,)).fetchall()]


def _duplicada(title, vistos):
    for t in vistos:
        if dist._overlap(title, t) >= 0.42:       # _overlap recebe texto cru
            return True
    return False


def coletar(cats=None, verbose=True):
    """Coleta as fontes próprias (bolso/preco/novidade/rua-RSS) + rua do banco da Rádio."""
    from dlm import scraper
    conn = get_db()
    vistos = _recentes(conn)
    novos = 0
    corte = datetime.now() - timedelta(days=int(dist._env("MAX_NEWS_AGE_DIAS", "3") or 3))
    for cat in (cats or ["bolso", "rua", "preco", "novidade"]):
        for url, fonte_padrao in FEEDS.get(cat, []):
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (compatible; RadioSCBot/1.0)"})
                feed = feedparser.parse(r.content)
            except Exception as e:
                if verbose:
                    print(f"   ! feed falhou ({fonte_padrao}): {e}")
                continue
            for e in feed.entries[:15]:
                title = scraper.clean_html(getattr(e, "title", ""))
                title = scraper._tira_fonte_titulo(title, gnews=True)
                summary = scraper.clean_html(getattr(e, "summary", "") or "")
                link = getattr(e, "link", "")
                if not title or not link:
                    continue
                if scraper._idioma_estranho(f"{title} {summary[:200]}") or scraper._ano_velho_no_titulo(title):
                    continue
                pub = getattr(e, "published_parsed", None)
                pub_dt = datetime(*pub[:6]) if pub else None
                if pub_dt and pub_dt < corte:
                    continue
                ok, why = _relevante(cat, title, summary)
                if not ok:
                    continue
                if _duplicada(title, vistos):
                    continue
                # fonte real (o Google cola '- Fonte' no título)
                source = fonte_padrao
                partes = getattr(e, "title", "").rsplit(" - ", 1)
                if len(partes) == 2 and 0 < len(partes[1].strip()) <= 40:
                    source = partes[1].strip()
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO mob_news (k,cat,title,summary,link,source,published_at,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (_key(title), cat, title[:300], summary[:1500], link, source,
                         (pub_dt or datetime.now()).isoformat(timespec="minutes"),
                         datetime.now().isoformat(timespec="minutes")))
                    if conn.total_changes:
                        novos += 1
                        vistos.append(title)
                except Exception:
                    pass
            time.sleep(0.3)
        conn.commit()
    # (22/set) O bloco que lia a tabela `news` do banco da Rádio saiu daqui: o motor mudou de
    # casa e a Rádio está sendo vendida. Rua agora tem feeds próprios (ver FEEDS["rua"]).
    if verbose:
            print(f"   ! rua (banco da Rádio) indisponível: {e}")
    if verbose:
        print(f"coleta: {novos} novas")
        for cat in CATS:
            if cat == "nosso":
                continue
            n = conn.execute("SELECT COUNT(*) FROM mob_news WHERE cat=? AND posted_at IS NULL", (cat,)).fetchone()[0]
            print(f"  fila {cat:9} {n}")
    conn.close()
    return novos


def proxima(conn, cat):
    corte = (datetime.now() - timedelta(days=int(dist._env("MAX_NEWS_AGE_DIAS", "3") or 3))).isoformat()
    return conn.execute(
        "SELECT * FROM mob_news WHERE cat=? AND posted_at IS NULL AND published_at>=? "
        "ORDER BY published_at DESC LIMIT 1", (cat, corte)).fetchone()


# ----------------------------------------------------------------- texto (IA com fonte obrigatória)
_LOCAL_HINT = {
    "bolso": "Se a notícia for nacional, diga o que muda pra quem tem veículo em Santa Catarina (sem inventar valor).",
    "rua": "Diga a cidade e o trecho. Quem passa ali precisa entender em 3 segundos.",
    "preco": "Números só os que estão no texto. Cite a cidade/semana a que o preço se refere.",
    "novidade": "Fatura local: cite preço só se estiver no texto; NUNCA calcule IPVA nem parcela.",
}


def _corpo(row):
    """Texto-base: resumo do feed; se curto, tenta o corpo real (scraper.fetch_article_text)."""
    from dlm import scraper
    body = (row["summary"] or "").strip()
    if len(body) < 220:
        try:
            link = row["link"]
            if "news.google.com" in link:
                link = scraper._resolve_gnews(link)
            txt = scraper.fetch_article_text(link)
            if txt and len(txt) > len(body):
                body = txt
        except Exception:
            pass
    return re.sub(r"\s+", " ", body)[:1600]


def redigir(row):
    """Devolve dict: manchete, bullets[3], legenda, norma, fonte. Só do texto; sem norma no texto → norma ''."""
    cat = row["cat"]
    title = re.sub(r"\s+", " ", row["title"] or "").strip()
    body = _corpo(row)
    fonte = row["source"] or "fonte"
    prompt = (
        "Você edita um perfil de Instagram de NOTÍCIAS DE MOBILIDADE do Norte de Santa Catarina "
        "(Jaraguá do Sul, Schroeder, Guaramirim, Corupá, Massaranduba, Joinville). Público: gente que "
        "tem carro, moto ou scooter. Tom: útil, direto, sem sensacionalismo, sem opinião, sem venda.\n"
        f"CATEGORIA: {CATS[cat]['badge']}. {_LOCAL_HINT.get(cat, '')}\n"
        "Responda SOMENTE um JSON válido, sem comentário, com as chaves:\n"
        '  "manchete": frase de até 80 caracteres, em caixa normal (não CAPS), sem ponto final, que diga '
        "o que muda pra quem dirige;\n"
        '  "bullets": lista de 3 frases curtas (até 90 caracteres) com o fato, o prazo/valor e o que a pessoa faz;\n'
        '  "norma": o número EXATO da resolução, lei, portaria ou decreto citado no texto (ex: "Resolução CONTRAN '
        'nº 996/2023"); se o texto NÃO citar número, devolva "" — PROIBIDO inventar;\n'
        '  "legenda": 3 a 5 linhas curtas em português do Brasil, 1ª linha é gancho (no máximo 1 emoji), '
        "sem hashtags, sem 'clique aqui', sem chamar de 'nosso' o que não é da região.\n"
        "REGRAS: use SÓ o que está no texto. Nada de número, valor ou data que não esteja lá. "
        "Se o texto for de outro estado, deixe claro que é de lá.\n\n"
        f"TÍTULO: {title}\nFONTE: {fonte}\nTEXTO: {body}"
    )
    out = None
    try:
        from dlm import cerebro
        txt = cerebro.completar(prompt)
        if txt:
            m = re.search(r"\{.*\}", txt, re.S)
            out = json.loads(m.group(0)) if m else None
    except Exception as e:
        print(f"   ! IA indisponível ({e}) — fallback local")
    if not isinstance(out, dict) or not out.get("manchete"):
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if 30 < len(s.strip()) < 140][:3]
        out = {"manchete": title[:80], "bullets": sents or [title[:90]], "norma": "",
               "legenda": f"{title}\n\n" + " ".join(sents[:2])}
    out["manchete"] = str(out.get("manchete", title))[:90].rstrip(".")
    out["bullets"] = [str(b)[:110] for b in (out.get("bullets") or [])][:3] or [title[:90]]
    out["norma"] = str(out.get("norma") or "").strip()[:80]
    # anti-alucinação de norma: o número tem que existir no texto-base
    if out["norma"]:
        nums = re.findall(r"\d{2,5}", out["norma"])
        if not nums or not all(n in f"{title} {body}" for n in nums):
            out["norma"] = ""
    out["legenda"] = str(out.get("legenda") or title).strip()
    out["fonte"] = fonte
    return out


def legenda_final(row, tx):
    cat = row["cat"]
    partes = [tx["legenda"].strip()]
    if tx.get("norma"):
        partes.append(f"📄 Base: {tx['norma']}")
    partes.append(f"📰 Fonte: {tx['fonte']}")
    partes.append("Salva e manda pra quem dirige. Siga " + BRAND["instagram"] + " — carro, moto e scooter no Norte de SC.")
    tags = list(BRAND["hashtags"])
    extra = {"bolso": ["#multa", "#cnh", "#licenciamento"], "rua": ["#br280", "#br101", "#radar"],
             "preco": ["#gasolina", "#combustivel", "#pneu"], "novidade": ["#lancamento", "#carronovo", "#eletrico"]}
    tags += extra.get(cat, [])
    partes.append(" ".join(tags[:14]))
    return "\n\n".join(partes)


# ----------------------------------------------------------------- render (card + carrossel)
def _font(size, bold=True):
    return gi.font(size, bold=bold)


def _fundo(cat, seed, top=0.30, bottom=0.94):
    """Foto de fundo: pool assets/mobilidade/<cat>/ (se houver) → arsenal static/bg por slug → None."""
    from PIL import Image
    pool = sorted(glob.glob(os.path.join(POOL_DIR, cat, "*.jp*g")) + glob.glob(os.path.join(POOL_DIR, cat, "*.webp")))
    fp = pool[seed % len(pool)] if pool else None
    if not fp:
        from dlm import genericbg
        for i, slug in enumerate(CATS[cat]["slugs"]):
            fp = genericbg._file(slug, seed + i)
            if fp:
                break
    if not fp:
        return None
    try:
        img = Image.open(fp).convert("RGB")
    except Exception:
        return None
    r = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * r), round(img.height * r)), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return gi.gradient_overlay(img.crop((x, y, x + W, y + H)), top=top, bottom=bottom)


def _base(cat, seed, com_foto=True, escuro=False):
    from PIL import Image, ImageDraw
    img = (_fundo(cat, seed, 0.86, 0.94) if escuro else _fundo(cat, seed)) if com_foto else None
    if img is None:
        img = Image.new("RGB", (W, H), BRAND["bg"])
    d = ImageDraw.Draw(img)
    # header da marca
    f = _font(32)
    tag = BRAND["brand_tag"]
    w = d.textlength(tag, font=f)
    d.rounded_rectangle([56, 56, 56 + w + 70, 56 + 62], radius=31, fill=BRAND["accent2"])
    d.ellipse([56 + 26, 56 + 25, 56 + 38, 56 + 37], fill=BRAND["white"])
    d.text((56 + 52, 56 + 12), tag, font=f, fill=BRAND["white"])
    return img, d


def _rodape(d, fonte=None):
    f = _font(28, bold=False)
    txt = BRAND["instagram"] + "  ·  Norte de SC"
    if fonte:
        txt = f"Fonte: {fonte}   ·   " + txt
    w = d.textlength(txt, font=f)
    d.text(((W - w) // 2, H - 76), txt, font=f, fill=BRAND["muted"])


def slide_capa_news(row, tx, outdir, seed):
    cat = row["cat"]
    img, d = _base(cat, seed)
    gi.pill(d, 56, 190, CATS[cat]["badge"], _font(34), BRAND["accent"], (10, 10, 10))
    # manchete grande, embaixo (foto fica em cima)
    ft = _font(78)
    lines = gi.wrap(d, tx["manchete"], ft, W - 112)[:4]
    if len(lines) == 4:
        ft = _font(66)
        lines = gi.wrap(d, tx["manchete"], ft, W - 112)[:4]
    lh = int(ft.size * 1.12)
    y = H - 210 - lh * len(lines)
    for ln in lines:
        d.text((56, y), ln, font=ft, fill=BRAND["white"], stroke_width=2, stroke_fill=(0, 0, 0))
        y += lh
    if tx.get("norma"):
        gi.pill(d, 56, y + 6, tx["norma"], _font(26), BRAND["card"], BRAND["white"])
    _rodape(d, tx["fonte"])
    p = os.path.join(outdir, "slide_1.png")
    img.save(p)
    return p


def slide_bullets_news(row, tx, outdir, seed):
    cat = row["cat"]
    img, d = _base(cat, seed, com_foto=True, escuro=True)
    gi.pill(d, 56, 190, "O QUE MUDA PRA VOCÊ", _font(32), BRAND["accent2"], BRAND["white"])
    fb = _font(56)
    lh = int(fb.size * 1.28)
    blocos = [gi.wrap(d, b, fb, W - 112 - 50)[:3] for b in tx["bullets"]]
    alt = sum(len(bl) * lh + 56 for bl in blocos)
    y = max(300, (H - 120 - alt) // 2 + 60)
    for lines in blocos:
        d.ellipse([56, y + 18, 56 + 24, y + 42], fill=BRAND["accent"])
        y = gi.draw_lines(d, lines, fb, 106, y, BRAND["white"], lh, stroke=2) + 56
    if tx.get("norma"):
        gi.pill(d, 56, min(y + 10, H - 260), "BASE: " + tx["norma"].upper(), _font(26), BRAND["card"], BRAND["accent"])
    _rodape(d, tx["fonte"])
    p = os.path.join(outdir, "slide_2.png")
    img.save(p)
    return p


def slide_cta_news(row, outdir):
    img, d = _base(row["cat"], 0, com_foto=False)
    gi.pill(d, 56, 190, "SALVA E COMPARTILHA", _font(32), BRAND["accent"], (10, 10, 10))
    ft = _font(70)
    y = 320
    for ln in gi.wrap(d, "Quem dirige no Vale precisa saber disso.", ft, W - 112)[:3]:
        d.text((56, y), ln, font=ft, fill=BRAND["white"]); y += int(ft.size * 1.12)
    y += 40
    fs = _font(40, bold=False)
    for ln in gi.wrap(d, "Manda pra quem tem carro, moto ou scooter em " + " · ".join(CIDADES[:5]) + ".", fs, W - 112)[:4]:
        d.text((56, y), ln, font=fs, fill=BRAND["muted"]); y += 54
    y += 60
    fh = _font(60)
    w = d.textlength(BRAND["instagram"], font=fh)
    d.rounded_rectangle([56, y, 56 + w + 80, y + 104], radius=22, fill=BRAND["accent2"])
    d.text((56 + 40, y + 22), BRAND["instagram"], font=fh, fill=BRAND["white"])
    d.text((56, y + 130), "Perfil do Grupo Lessmann · Rádio SC News · Despachante Lessmann · DL Mobilidade",
           font=_font(24, bold=False), fill=BRAND["muted"])
    _rodape(d)
    p = os.path.join(outdir, "slide_3.png")
    img.save(p)
    return p


def gerar_news(row, outdir):
    os.makedirs(outdir, exist_ok=True)
    tx = redigir(row)
    seed = int(row["id"] or 0) + datetime.now().timetuple().tm_yday
    paths = [slide_capa_news(row, tx, outdir, seed),
             slide_bullets_news(row, tx, outdir, seed),
             slide_cta_news(row, outdir)]
    cap = legenda_final(row, tx)
    with open(os.path.join(outdir, "legenda.txt"), "w", encoding="utf-8") as f:
        f.write(cap)
    return paths, cap, tx


# ----------------------------------------------------------------- 'nosso' (rotulado)
ROTULO = "📌 Publicidade · Grupo Lessmann (dono deste perfil)"


def gerar_nosso(outdir):
    """1/dia: item das séries de dinheiro do despachante (mesmo banco 1 post = 1 página), com o
    TEMA deste perfil e o rótulo de publicidade na capa e na legenda. Sábado = scooter."""
    from dlm import marcas
    from dlm import series_despachante as sd
    now = datetime.now()
    slot = "tarde" if now.weekday() == 5 else "manha"     # sáb: scooter (tarde seg/qua/sex → aqui só sáb)
    item = sd.escolher(slot, now.timetuple().tm_yday, 0 if slot == "tarde" else now.weekday())
    t = dict(BRAND)
    t["brand_tag"] = CATS["nosso"]["badge"]
    t["series"] = True
    os.makedirs(outdir, exist_ok=True)
    paths = [marcas.slide_capa(t, item, outdir, 1),
             marcas.slide_conteudo(t, item, outdir, 2),
             marcas.slide_site(t, item, outdir, 3)]
    cap = marcas.groq_caption(t, item)
    cap = ROTULO + "\n\n" + cap
    with open(os.path.join(outdir, "legenda.txt"), "w", encoding="utf-8") as f:
        f.write(cap)
    return paths, cap, item


# ----------------------------------------------------------------- publicação (collab)
def tokens_ok():
    token, ig_id, _ = (dist._env(BRAND["env"]["token"]), dist._env(BRAND["env"]["ig"]), None)
    return bool(token and ig_id)


def publicar(prefix, image_paths, caption, collab=True):
    """Carrossel no IG do perfil de mobilidade; collab=True marca os colaboradores (MOB_COLLAB) — o outro
    perfil aceita no app (não há API pra aceitar), por isso só nos slots MOB_COLLAB_SLOTS. Story da capa junto."""
    from PIL import Image
    token, ig_id = dist._env("MOB_PAGE_TOKEN"), dist._env("MOB_IG_USER_ID")
    if not (token and ig_id):
        raise RuntimeError("Tokens MOB_PAGE_TOKEN / MOB_IG_USER_ID ausentes.")
    os.makedirs(dist.PUBLIC_IMG_DIR, exist_ok=True)
    urls = []
    for i, p in enumerate(image_paths, 1):
        fname = f"{prefix}_s{i}.jpg"
        Image.open(p).convert("RGB").save(os.path.join(dist.PUBLIC_IMG_DIR, fname), "JPEG", quality=90)
        urls.append(f"{dist.PUBLIC_BASE_URL}/static/social/{fname}")
    children = []
    for u in urls:
        children.append(dist._graph_post(f"{dist.GRAPH}/{ig_id}/media",
                                         {"image_url": u, "is_carousel_item": "true", "access_token": token})["id"])
        time.sleep(2)
    data = {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption, "access_token": token}
    collab = [c.strip().lstrip("@") for c in dist._env("MOB_COLLAB", "").split(",") if c.strip()] if collab else []
    if collab:
        data["collaborators"] = json.dumps(collab[:3])     # até 3 (Graph API); o outro perfil aceita no app
    cont = dist._graph_post(f"{dist.GRAPH}/{ig_id}/media", data)["id"]
    time.sleep(3)
    ig = dist._graph_post(f"{dist.GRAPH}/{ig_id}/media_publish", {"creation_id": cont, "access_token": token})
    story = None
    if dist._env("SOCIAL_STORY", "1") == "1":
        try:
            sj = os.path.join(dist.PUBLIC_IMG_DIR, f"{prefix}_story.jpg")
            dist._story_image(image_paths[0], sj)
            sc = dist._graph_post(f"{dist.GRAPH}/{ig_id}/media",
                                  {"media_type": "STORIES", "image_url": f"{dist.PUBLIC_BASE_URL}/static/social/{prefix}_story.jpg",
                                   "access_token": token})["id"]
            time.sleep(2)
            story = dist._graph_post(f"{dist.GRAPH}/{ig_id}/media_publish", {"creation_id": sc, "access_token": token})
        except Exception as e:
            print(f"   ! Story falhou (segue): {e}")
    return {"instagram": ig, "story": story, "collab": collab}


def _log(cat, row, r, extra=None):
    """DATA_DIR/mob_posts.jsonl — placar por categoria (o que dá view, o que dá zap)."""
    try:
        with open(os.path.join(DATA_DIR, "mob_posts.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="minutes"), "cat": cat,
                                "titulo": (row["title"] if row is not None else (extra or {}).get("titulo")),
                                "fonte": (row["source"] if row is not None else "nosso"),
                                "link": (extra or {}).get("link"),
                                "ig_media_id": ((r or {}).get("instagram") or {}).get("id"),
                                "collab": (r or {}).get("collab")}, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"   ! log falhou (segue): {e}")


# ----------------------------------------------------------------- run
def categoria_do_slot(idx, weekday=None):
    wd = datetime.now().weekday() if weekday is None else weekday
    cat = GRADE[idx % len(GRADE)]
    if cat == "novidade" and wd not in NOVIDADE_DIAS:
        cat = "bolso"
    return cat


def run(cat, post=False, collab=True):
    """Gera (e opcionalmente posta) 1 carrossel da categoria. Sem matéria → tenta 'bolso' → pula."""
    day = datetime.now().strftime("%Y-%m-%d")
    if cat == "nosso":
        outdir = os.path.join(OUT_BASE, f"{day}_mob_nosso")
        paths, cap, item = gerar_nosso(outdir)
        print(f"   nosso: {item.get('titulo')}  -> {outdir}")
        if post:
            r = publicar(f"mob_{day}_nosso", paths, cap, collab=collab)
            _log("nosso", None, r, {"titulo": item.get("titulo"), "link": item.get("link")})
            return r
        return {"preview": paths}
    conn = get_db()
    row = proxima(conn, cat)
    if row is None and cat != "bolso":
        print(f"   fila '{cat}' vazia — caindo pra 'bolso'")
        cat, row = "bolso", proxima(conn, "bolso")
    if row is None:
        print(f"   fila vazia ({cat}) — slot pulado")
        conn.close()
        return None
    outdir = os.path.join(OUT_BASE, f"{day}_mob_{cat}_{row['id']}")
    paths, cap, tx = gerar_news(row, outdir)
    print(f"   {cat}: {tx['manchete']}  [{tx['fonte']}]  -> {outdir}")
    if post:
        r = publicar(f"mob_{day}_{cat}_{row['id']}", paths, cap, collab=collab)
        ig_id = ((r or {}).get("instagram") or {}).get("id")
        conn.execute("UPDATE mob_news SET posted_at=?, ig_media_id=? WHERE id=?",
                     (datetime.now().isoformat(timespec="minutes"), ig_id, row["id"]))
        conn.commit()
        _log(cat, row, r, {"link": row["link"]})
        conn.close()
        return r
    conn.close()
    return {"preview": paths, "legenda": cap}


def run_slot(idx, post=False):
    """O que o scheduler chama: coleta rápida + posta a categoria do slot."""
    cat = categoria_do_slot(idx)
    if cat != "nosso":
        try:
            coletar(cats=[cat] if cat != "rua" else ["rua"], verbose=False)
        except Exception as e:
            print(f"   ! coleta falhou (segue com a fila): {e}")
    slots = [int(x) for x in dist._env("MOB_COLLAB_SLOTS", "0,1").split(",") if x.strip().isdigit()]
    return run(cat, post=post, collab=(idx in slots))


def main():
    a = sys.argv[1:]
    if not a or a[0] == "coletar":
        coletar()
    elif a[0] in ("gerar", "postar"):
        cat = a[1] if len(a) > 1 else "bolso"
        if a[0] == "gerar" and cat != "nosso":
            coletar(cats=[cat], verbose=False)
        r = run(cat, post=(a[0] == "postar"))
        print(r if r else "nada")
    elif a[0] == "slot":
        print(run_slot(int(a[1]), post="--post" in a))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
