# -*- coding: utf-8 -*-
"""
marcas.py — Motor de conteúdo multi-marca (Instagram + Facebook)
Grupo DL / 4kitem

Diferente da Rádio (que coleta notícia por RSS), aqui o conteúdo é PRÓPRIO da
marca: serviços, produtos e dicas — conteúdo "evergreen" que rotaciona por dia.

Cada marca tem:
  - tema (cores, nome, contato, site)
  - banco de conteúdo (lista de tópicos: serviço ou dica)
  - tokens Meta próprios (env por marca)
  - voz própria na legenda

Postar de verdade precisa, no ambiente, dos tokens da marca, ex. Despachante:
  DESP_PAGE_TOKEN, DESP_IG_USER_ID, DESP_PAGE_ID

USO local (dry-run, só gera as imagens):
  python marcas.py despachante
USO real (posta):
  python marcas.py despachante --post
"""
import argparse
import glob
import os
from datetime import datetime

from dlm import gen_instagram as gi
from dlm import distribuidor as dist

W, H = 1080, 1350
OUT_BASE = os.path.join(os.environ.get("DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dlm_posts")
PUBLIC_IMG_DIR = dist.PUBLIC_IMG_DIR


# ----------------------------------------------------------------- bancos de conteúdo
# Conteúdo 100% legítimo: a DL AJUDA o cliente — nunca se passa por Detran/governo.
DESPACHANTE_CONTEUDO = [
    {"cat": "DICA DE LEI",
     "titulo": "Comprou ou vendeu um veículo? Você tem 30 dias",
     "bullets": ["A lei dá 30 dias pra fazer a transferência após a compra.",
                 "Quem vende deve comunicar a venda pra não levar multa do comprador.",
                 "A gente cuida de tudo isso pra você, sem dor de cabeça."]},
    {"cat": "SERVIÇO",
     "titulo": "Tomou multa? Dá pra recorrer",
     "bullets": ["Muita multa tem erro e pode ser cancelada com recurso.",
                 "A DL Defesas analisa seu caso e monta a defesa.",
                 "Você não perde pontos à toa."]},
    {"cat": "DICA DE LEI",
     "titulo": "Comunicação de venda (ATPV-e): por que é tão importante",
     "bullets": ["Sem comunicar a venda, as multas do novo dono caem no SEU nome.",
                 "O ATPV-e é o documento digital que oficializa a venda.",
                 "A gente emite e comunica pra te proteger."]},
    {"cat": "SERVIÇO",
     "titulo": "CNH suspensa ou ameaçada? Não rode sem orientação",
     "bullets": ["Dirigir com a CNH suspensa é infração gravíssima.",
                 "A DL Defesas atua na suspensão e na cassação.",
                 "Chama a gente antes de tomar qualquer decisão."]},
    {"cat": "DICA DE LEI",
     "titulo": "Quantos pontos você pode ter na CNH?",
     "bullets": ["O limite varia conforme as infrações gravíssimas no período.",
                 "Passou do limite, a CNH pode ser suspensa.",
                 "A gente acompanha sua pontuação e te orienta."]},
    {"cat": "SERVIÇO",
     "titulo": "Proteção Veicular a partir de R$ 90/mês",
     "bullets": ["Cobertura para roubo, furto e colisão.",
                 "Assistência 24 horas quando você mais precisa.",
                 "DL Proteção Veicular — tranquilidade pra rodar."]},
    {"cat": "SERVIÇO",
     "titulo": "Precisa renovar a CNH? A gente te orienta",
     "bullets": ["Renovação A a E pelo gov.br — a gente orienta cada etapa.",
                 "MOPP, mototáxi, escolar: curso homologado que representamos.",
                 "DL CNH — quem faz é você, a gente acompanha."]},
    {"cat": "DICA DE LEI",
     "titulo": "Você é PCD? Pode ter isenção de impostos no carro",
     "bullets": ["Pessoas com deficiência têm direito a isenções na compra do veículo.",
                 "A papelada é chata, mas a gente cuida de tudo.",
                 "DL Assessoria — você economiza de verdade."]},
    {"cat": "SERVIÇO",
     "titulo": "Quer abrir seu MEI ou regularizar o nome?",
     "bullets": ["Abertura de MEI e regularização de empresa.",
                 "Recuperação de crédito e questões de score.",
                 "DL Assessoria resolve a burocracia por você."]},
    {"cat": "SERVIÇO",
     "titulo": "Licenciamento atrasado? Resolve com a gente",
     "bullets": ["Licenciamento, débitos, histórico e consulta de leilão.",
                 "Tudo certinho pra você não ser parado na blitz.",
                 "Despachante Lessmann — rápido e sem complicação."]},
    # ───────── DL MOBILIDADE (27/jul: o despachante É a DL Mobilidade — scooters no mesmo
    # perfil; fonte: dldespachante.com.br/mobilidade + Resolução CONTRAN 996/2023) ─────────
    {"cat": "SCOOTER ELÉTRICA",
     "titulo": "Sem CNH. Sem placa. Sem IPVA. É sério.",
     "bullets": ["Scooter elétrica autopropelida (CONTRAN 996): até 1000W e 32 km/h.",
                 "Não precisa de habilitação, emplacamento nem licenciamento.",
                 "Vem conhecer na loja — Mal. Castelo Branco, 2838, Schroeder."]},
    {"cat": "DICA DE LEI",
     "titulo": "Onde a scooter elétrica PODE andar?",
     "bullets": ["Ciclovia e ciclofaixa: pode, até 20 km/h.",
                 "Rua urbana com limite de até 40 km/h: pode.",
                 "Calçada e rodovia: NÃO pode. Anda certo, anda tranquilo."]},
    {"cat": "DICA DE LEI",
     "titulo": "Os equipamentos obrigatórios da scooter elétrica",
     "bullets": ["Campainha, farol dianteiro e lanterna traseira.",
                 "Faixas refletivas, retrovisor e velocímetro.",
                 "As nossas já vêm completas de fábrica — é ligar e andar."]},
    {"cat": "CURIOSIDADE",
     "titulo": "Quanto custa 'abastecer' uma scooter elétrica?",
     "bullets": ["Recarga em tomada comum de casa, em 4 a 6 horas.",
                 "Custo por km muitas vezes menor que gasolina.",
                 "Sem óleo, sem vela, sem filtro — manutenção mínima."]},
    {"cat": "OFERTA",
     "titulo": "A parcela que cabe no lugar da gasolina",
     "bullets": ["Financiamento em até 48x pela Viacredi, cooperativa da nossa região.",
                 "Parcelas a partir de R$ 200 — sujeito a análise de crédito.",
                 "Troca o gasto do posto por um patrimônio teu."]},
    {"cat": "CURIOSIDADE",
     "titulo": "Bateria de grafeno: até 100 km com uma carga",
     "bullets": ["Modelos com autonomia de 50 a 100 km por recarga.",
                 "Motor silencioso: sem barulho, sem vibração, sem cheiro.",
                 "Tecnologia NXT — a primeira montadora nacional de autopropelidos."]},
    {"cat": "CURIOSIDADE",
     "titulo": "Scooter elétrica pode pegar chuva?",
     "bullets": ["Pode: os componentes são vedados de fábrica.",
                 "O que NÃO pode é lava-jato — isso anula a garantia.",
                 "Lavou com balde e sabão neutro, tá novo de novo."]},
    {"cat": "DICA DE LEI",
     "titulo": "O perigo da scooter 'baratinha' do Paraguai",
     "bullets": ["Na blitz, nota paraguaia = veículo apreendido + descaminho.",
                 "Sem garantia, sem peça, sem assistência — fica na mão.",
                 "Aqui: nota fiscal brasileira e 2 anos de garantia. Durma em paz."]},
    {"cat": "TEST-RIDE",
     "titulo": "NÃO compre scooter sem dar uma volta antes",
     "bullets": ["Vem na loja, senta, liga e anda. Sem compromisso.",
                 "Só a gente tem loja física pra testar na região.",
                 "Mal. Castelo Branco, 2838, Schroeder — te esperamos."]},
    {"cat": "SCOOTER ELÉTRICA",
     "titulo": "Conhece o triciclo elétrico? Estabilidade total",
     "bullets": ["Pancho: 3 rodas, aguenta até 150 kg, 70 km de autonomia.",
                 "Perfeito pra quem quer segurança extra no dia a dia.",
                 "Sem CNH e sem emplacar, como toda a linha autopropelida."]},
    {"cat": "GARANTIA",
     "titulo": "Comprar scooter em site ou em loja da tua cidade?",
     "bullets": ["Aqui tem garantia de 2 anos no motor e 6 meses na bateria.",
                 "Assistência e peças NXT no Brasil inteiro.",
                 "E uma loja de verdade, do lado do despachante de 8 anos."]},
    {"cat": "SCOOTER ELÉTRICA",
     "titulo": "Nunca tirou CNH? Tu ainda assim pode ter tua condução",
     "bullets": ["A scooter autopropelida não exige habilitação.",
                 "Mercado, trabalho, visita — liberdade sem burocracia.",
                 "Vem descobrir qual modelo cabe na tua rotina."]},
]

# Intercala despachante × mobilidade na rotação diária (senão os 12 de scooter rodariam em
# bloco: 12 dias seguidos só de scooter). Dia sim, dia não, cada frente aparece.
import itertools as _it
_desp, _mob = DESPACHANTE_CONTEUDO[:10], DESPACHANTE_CONTEUDO[10:]
DESPACHANTE_CONTEUDO = [x for par in _it.zip_longest(_desp, _mob) for x in par if x]


# 4kitem — sistemas/apps que facilitam o dia a dia de pequenos negocios.
# SlotZap (rifa/sorteio) fica de FORA: a Meta bane rifa e pode derrubar a conta.
KITEM_CONTEUDO = [
    {"cat": "AGENDAMENTO", "titulo": "AgendaJá — sua agenda no automático",
     "bullets": ["Página de agendamento com link próprio",
                 "Cliente marca pelo celular, sem baixar app",
                 "Sem horário duplo — você só atende"]},
    {"cat": "DELIVERY", "titulo": "MandaJá — delivery SEM comissão",
     "bullets": ["Sua loja online pronta em minutos",
                 "PIX direto, sem intermediário levando %",
                 "Pedido chega no seu WhatsApp na hora"]},
    {"cat": "WHATSAPP", "titulo": "MandaZap — marketing no WhatsApp com anti-ban",
     "bullets": ["Importe sua lista (CSV ou PDF)",
                 "Mensagem personalizada com o nome do cliente",
                 "Disparo em massa com anti-ban inteligente"]},
    {"cat": "TRÂNSITO", "titulo": "AlertaJá — CNH e veículo de olho pra você",
     "bullets": ["Pontos, vencimento e categoria da CNH",
                 "IPVA, licenciamento e multas do veículo",
                 "Relatório todo mês no seu WhatsApp"]},
    {"cat": "BAR & PUB", "titulo": "PubShow — a jukebox digital do seu bar",
     "bullets": ["Cliente pede música pelo celular",
                 "Paga via PIX e toca na hora",
                 "Sua TV exibe videoclipes e seus avisos"]},
    {"cat": "SALA DE ESPERA", "titulo": "SalaTV — a TV certa pro seu ambiente",
     "bullets": ["Conteúdo curado e seguro (clínica, salão, kids)",
                 "Sem anúncios do YouTube atrapalhando",
                 "Exiba seus próprios avisos na tela"]},
    {"cat": "PET", "titulo": "VetZap — triagem do seu pet 24 horas",
     "bullets": ["Saiba se é urgência em 3 minutos",
                 "Classifica: Estável, Atenção ou Urgente",
                 "Cartão digital de vacinas do pet"]},
    {"cat": "PRODUTIVIDADE", "titulo": "Baú — cofre das suas senhas na nuvem",
     "bullets": ["Guarde sites, logins e dicas de senha",
                 "Nunca mais perca senha ao formatar o PC",
                 "Acesse de qualquer dispositivo"]},
    {"cat": "DESPACHANTE", "titulo": "Amigo Despachante — sua loja organizada",
     "bullets": ["Ordens de serviço em quadro Kanban",
                 "Controle de licenciamento por final de placa",
                 "IA que ajuda no dia a dia"]},
    {"cat": "DEFESA DE MULTAS", "titulo": "DefesaPro — defesa de multas sem retrabalho",
     "bullets": ["Motor de petições baseado no CTB",
                 "OCR: preenche o processo por foto ou PDF",
                 "Controle de prazos e honorários"]},
]


BRANDS = {
    "despachante": {
        "nome": "Despachante Lessmann",
        "brand_tag": "DESPACHANTE LESSMANN",
        "tagline": "Trânsito descomplicado em Schroeder e região",
        "site": "dldespachante.com.br",
        "whats": "(47) 99716-2967",
        "instagram": "@despachantelessmann",
        # tema (azul confiança + dourado)
        "bg": (11, 31, 51), "card": (18, 42, 66), "accent": (242, 183, 5),
        "accent2": (46, 134, 222), "white": (245, 247, 250), "muted": (160, 175, 190),
        # tokens (env)
        "env": {"token": "DESP_PAGE_TOKEN", "ig": "DESP_IG_USER_ID", "page": "DESP_PAGE_ID"},
        "conteudo": DESPACHANTE_CONTEUDO,   # fallback (rotação antiga)
        "series": True,                     # 15/set: jornadas por slot (series_despachante.py)
        "hashtags": ["#despachante", "#schroeder", "#jaraguadosul", "#guaramirim",
                     "#transito", "#detran", "#cnh", "#multas", "#veiculos", "#dllessmann"],
        "voz": ("Você é o social media do Despachante Lessmann (Schroeder/SC). Fale como "
                "um especialista amigo que DESCOMPLICA o trânsito: claro, confiável e "
                "acolhedor. NUNCA se passe por Detran/governo — a DL AJUDA o cliente. "
                "Sem juridiquês, sem sensacionalismo."),
    },

    "dl_mobilidade": {
        "nome": "DL Mobilidade",
        "brand_tag": "DL MOBILIDADE",
        "tagline": "Scooters elétricas NXT em Schroeder e região",
        "site": "dldespachante.com.br",
        "whats": "(47) 99716-2967",
        "instagram": "@despachantelessmann",
        # tema laranja/preto (energia + scooter)
        "bg": (15, 17, 22), "card": (26, 29, 38), "accent": (255, 120, 20),
        "accent2": (245, 197, 24), "white": (245, 247, 250), "muted": (170, 178, 188),
        # 27/jul — DECISÃO DO DONO: sem IG separado; "Despachante Lessmann É a DL Mobilidade".
        # A oferta de scooter (foto real + preço) posta NO PERFIL DO DESPACHANTE (tokens DESP_*),
        # 3x/semana (ter/qui/sáb 16h) pra não virar spam comercial no feed diário.
        "env": {"token": "DESP_PAGE_TOKEN", "ig": "DESP_IG_USER_ID", "page": "DESP_PAGE_ID"},
        "hashtags": ["#scootereletrica", "#nxt", "#mobilidadeeletrica", "#schroeder",
                     "#jaraguadosul", "#guaramirim", "#dlmobilidade", "#semcnh",
                     "#scooter", "#viacredi"],
        "voz": ("Você é o social media da DL Mobilidade (scooters elétricas NXT, Schroeder/SC). "
                "Fale como gente da região: direto, animado, sem marketês. Venda a SOLUÇÃO — "
                "liberdade de rodar sem CNH, zero gasolina, patrimônio no lugar da despesa da "
                "gasolina. Seja honesto: financiamento é sujeito a análise de crédito. Sem "
                "sensacionalismo e sem prometer o que não pode."),
        "photo_based": True,   # usa FOTOS REAIS (assets/dl_scooters) com oferta por cima
        "ig_only": True,       # só Instagram (não posta no Facebook)
    },

    "4kitem": {
        "nome": "4kitem",
        "brand_tag": "4KITEM",
        "tagline": "Sistemas que facilitam o seu negócio",
        "site": "4kitem.com.br",
        "whats": "(47) 99960-6998",
        "instagram": "",   # preenche quando o IG estiver pronto
        # tema tech (indigo + ciano)
        "bg": (13, 14, 26), "card": (24, 26, 44), "accent": (108, 99, 255),
        "accent2": (0, 210, 200), "white": (245, 247, 250), "muted": (165, 170, 190),
        "env": {"token": "KITEM_PAGE_TOKEN", "ig": "KITEM_IG_USER_ID", "page": "KITEM_PAGE_ID"},
        "conteudo": KITEM_CONTEUDO,
        "cta_seal": "TESTE GRÁTIS",
        "cta_big": ["COMECE", "HOJE MESMO"],
        "hashtags": ["#4kitem", "#sistema", "#app", "#pequenonegocio", "#empreendedor",
                     "#automatizacao", "#whatsappbusiness", "#agendamento", "#delivery",
                     "#santacatarina"],
        "voz": ("Você é o social media do 4kitem, empresa de sistemas/apps que facilitam o "
                "dia a dia de pequenos negócios. Tom: moderno, direto e simples, SEM "
                "tecniquês. Foque no BENEFÍCIO pro dono do negócio: economiza tempo, vende "
                "mais, menos dor de cabeça. Convide pra testar grátis."),
        "ig_only": True,       # só Instagram (não posta no Facebook)
    },
}


# ----------------------------------------------------------------- DL Mobilidade (foto + oferta)
DL_PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "dl_scooters")

# Angulos que rotacionam por dia (a foto tambem rotaciona).
DL_ANGLES = [
    {"badge": "EXCLUSIVO · SÓ A DL TEM", "h1": "ATÉ 48x", "h2": "pela Viacredi",
     "sub": "Scooters NXT a partir de R$ 4.990"},
    {"badge": "SEM BUROCRACIA · CONTRAN 996", "h1": "SEM CNH,", "h2": "SEM EMPLACAMENTO",
     "sub": "Liberdade pra rodar sem dor de cabeça"},
    {"badge": "ECONOMIA DE VERDADE", "h1": "ZERO", "h2": "GASOLINA",
     "sub": "Recarrega na tomada · economiza todo mês"},
    {"badge": "VÁRIOS MODELOS NXT", "h1": "A PARTIR DE", "h2": "R$ 4.990",
     "sub": "Do urbano ao premium — tem o seu aqui"},
]

# Bullets do slide (SEM emoji — a fonte do slide nao renderiza emoji).
DL_BENEFITS = [
    "Scooters elétricas NXT — vários modelos",
    "Sem CNH e sem emplacamento (CONTRAN 996)",
    "Autonomia de 50 a 140 km por carga",
    "Até 24x no cartão ou 48x pela Viacredi",
    "Zero gasolina — economia de verdade",
    "Respaldo do Grupo DL",
]


def _dl_photo_card(angle, photo_path, t, outdir, n=1):
    """Slide de oferta: foto real do scooter + gradiente + oferta por cima."""
    from PIL import Image, ImageDraw
    ph = Image.open(photo_path).convert("RGB")
    r = max(W / ph.width, H / ph.height)
    ph = ph.resize((int(ph.width * r), int(ph.height * r)))
    l = (ph.width - W) // 2
    tp = (ph.height - H) // 2
    ph = ph.crop((l, tp, l + W, tp + H))
    # gradiente escuro de baixo p/ cima (leitura do texto)
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        grad.putpixel((0, y), int(238 * max(0, (y - H * 0.36) / (H * 0.64))))
    ph = Image.composite(Image.new("RGB", (W, H), (8, 10, 16)), ph, grad.resize((W, H)))
    d = ImageDraw.Draw(ph)
    OR, GO, WH = t["accent"], t["accent2"], t["white"]
    gi.pill(d, 56, 56, t["brand_tag"], _font(40), OR, WH)
    gi.pill(d, 56, H - 560, angle["badge"], _font(34), GO, (10, 10, 10))
    d.text((50, H - 500), angle["h1"], font=_font(140, impact=True), fill=WH,
           stroke_width=3, stroke_fill=(0, 0, 0))
    d.text((56, H - 350), angle["h2"], font=_font(62, impact=True), fill=OR)
    d.text((56, H - 262), angle["sub"], font=_font(38), fill=WH)
    d.text((56, H - 200), "Sem CNH · Sem emplacamento · até 32 km/h",
           font=_font(34, bold=False), fill=(195, 200, 210))
    wtxt = f"WhatsApp {t['whats']}"
    fw = _font(40)
    tw = d.textlength(wtxt, font=fw)
    d.rounded_rectangle([56, H - 135, 56 + tw + 60, H - 65], radius=18, fill=(37, 211, 102))
    d.text((86, H - 125), wtxt, font=fw, fill=(5, 50, 30))
    d.text((56, H - 44), "*Financiamento sujeito a análise de crédito.",
           font=_font(26, bold=False), fill=(150, 155, 165))
    p = os.path.join(outdir, f"slide_{n}.png")
    ph.save(p, quality=90)
    return p


def _dl_beneficios(t, outdir, n=2):
    img, d = _canvas(t)
    _brand_header(d, t)
    gi.pill(d, 56, 200, "POR QUE NA DL?", _font(50, impact=True), t["accent"], t["white"])
    y = 360
    fb = _font(44, bold=False)
    for b in DL_BENEFITS:
        d.ellipse([56, y + 12, 78, y + 34], fill=t["accent2"])
        lines = gi.wrap(d, b, fb, W - 180)
        gi.draw_lines(d, lines, fb, 100, y, t["white"], int(fb.size * 1.32))
        y += max(108, len(lines) * int(fb.size * 1.32) + 34)
    _footer(d, t)
    p = os.path.join(outdir, f"slide_{n}.png")
    img.save(p, quality=92)
    return p


def _dl_cta(t, photo_path, outdir, n=3):
    """CTA com foto de fundo + chamada pro WhatsApp."""
    from PIL import Image, ImageDraw
    ph = Image.open(photo_path).convert("RGB")
    r = max(W / ph.width, H / ph.height)
    ph = ph.resize((int(ph.width * r), int(ph.height * r)))
    l = (ph.width - W) // 2
    tp = (ph.height - H) // 2
    ph = ph.crop((l, tp, l + W, tp + H))
    dark = Image.new("RGB", (W, H), (8, 10, 16))
    ph = Image.blend(ph, dark, 0.62)
    d = ImageDraw.Draw(ph)
    OR, WH = t["accent"], t["white"]
    gi.pill(d, 56, 56, t["brand_tag"], _font(40), OR, WH)
    cy = H // 2 - 160
    for i, ln in enumerate(["VEM CONHECER", "SUA NOVA SCOOTER"]):
        f = _font(78, impact=True)
        w = d.textlength(ln, font=f)
        d.text(((W - w) // 2, cy + i * 90), ln, font=f, fill=WH, stroke_width=2, stroke_fill=(0, 0, 0))
    wtxt = t["whats"]
    fw = _font(56)
    w = d.textlength(wtxt, font=fw)
    d.rounded_rectangle([(W - w) // 2 - 50, cy + 240, (W + w) // 2 + 50, cy + 340],
                        radius=22, fill=(37, 211, 102))
    d.text(((W - w) // 2, cy + 260), wtxt, font=fw, fill=(5, 50, 30))
    fl = _font(40, bold=False)
    loc = "Schroeder/SC  ·  " + t["site"]
    w2 = d.textlength(loc, font=fl)
    d.text(((W - w2) // 2, cy + 370), loc, font=fl, fill=(210, 215, 225))
    p = os.path.join(outdir, f"slide_{n}.png")
    ph.save(p, quality=90)
    return p


def _hashtags_do_dia(t):
    """Evita postar o MESMO bloco de hashtags todo dia (sinal de spam do IG).
    Mantém as âncoras locais fixas e rotaciona o resto por dia."""
    import random as _r
    tags = list(t.get("hashtags", []))
    fixas = [x for x in tags if x in ("#schroeder", "#jaraguadosul", "#guaramirim")]
    resto = [x for x in tags if x not in fixas]
    _r.seed(datetime.now().timetuple().tm_yday)
    _r.shuffle(resto)
    return " ".join(fixas + resto[:6])


# Legendas-base ROTATIVAS (fallback se a IA nao responder) — nunca a mesma 2 posts seguidos.
_DL_CAP_FALLBACK = [
    "🛴⚡ Liberdade de rodar sem CNH e sem gasolina.\nScooter elétrica NXT, do lado do despachante de 8 anos em Schroeder.",
    "🛴 Cansou do posto? A recarga é na tomada de casa.\nScooter elétrica NXT — a economia fica no teu bolso todo mês.",
    "⚡ Sem CNH, sem placa, sem IPVA (CONTRAN 996).\nVem conhecer a linha NXT aqui na loja, sem compromisso.",
    "🛴 A parcela que cabe no lugar da gasolina.\nScooter elétrica NXT, até 48x pela Viacredi da nossa região.",
]


def _dl_caption(t, angle):
    """Legenda da oferta de scooter — gerada na VOZ da marca e variando pelo ângulo do dia.
    (Antes era FIXA: a mesma legenda em todo post = sinal de spam / perde alcance.)
    Fallback: rotação de legendas-base (nunca idêntica 2 posts seguidos)."""
    ig = f"Siga {t['instagram']}\n" if t.get("instagram") else ""
    rodape = (f"\n📍 Schroeder/SC · a partir de R$ 4.990\n"
              f"📲 Simule no WhatsApp: {t['whats']}\n"
              f"🌐 Acesse e veja mais informações: {t['site']}/mobilidade\n{ig}"
              "*Financiamento sujeito a análise de crédito (com juros).\n\n"
              + _hashtags_do_dia(t))
    try:
        from dlm import cerebro
        prompt = (
            f"{t.get('voz', '')}\n\n"
            "Escreva a legenda de UM post de Instagram (portugues BR) para a oferta de scooter "
            "eletrica de hoje. "
            f"ANGULO DE HOJE: {angle['badge']} — {angle['h1']} {angle['h2']} ({angle['sub']}).\n"
            "Regras: 1a linha e um gancho curto (no max 1 emoji). Depois 3-4 linhas curtas. "
            "Bata no angulo do dia. Termine convidando a CHAMAR NO WHATSAPP. "
            "NAO invente preco nem modelo alem do citado. NAO use hashtags (eu adiciono depois)."
        )
        txt = cerebro.completar(prompt)
        if txt:
            return txt.strip().strip('"') + "\n" + rodape
    except Exception:
        pass
    base = _DL_CAP_FALLBACK[datetime.now().timetuple().tm_yday % len(_DL_CAP_FALLBACK)]
    return base + "\n" + rodape


def generate_dl(brand_key, outdir=None):
    t = BRANDS[brand_key]
    yday = datetime.now().timetuple().tm_yday
    angle = DL_ANGLES[yday % len(DL_ANGLES)]
    photos = sorted(glob.glob(os.path.join(DL_PHOTOS_DIR, "*.jpg")))
    if not photos:
        raise RuntimeError("Sem fotos em assets/dl_scooters.")
    ph1 = photos[yday % len(photos)]
    ph2 = photos[(yday + 7) % len(photos)]   # foto diferente no CTA
    if outdir is None:
        outdir = os.path.join(OUT_BASE, datetime.now().strftime("%Y-%m-%d") + f"_{brand_key}")
    os.makedirs(outdir, exist_ok=True)
    paths = [_dl_photo_card(angle, ph1, t, outdir, 1),
             _dl_beneficios(t, outdir, 2),
             _dl_cta(t, ph2, outdir, 3)]
    caption = _dl_caption(t, angle)
    with open(os.path.join(outdir, "legenda.txt"), "w", encoding="utf-8") as f:
        f.write(caption)
    return paths, caption, {"titulo": f"{angle['h1']} {angle['h2']}"}


# ----------------------------------------------------------------- desenho
def _font(size, bold=True, impact=False):
    return gi.font(size, bold=bold, impact=impact)


def _canvas(t):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), t["bg"])
    return img, ImageDraw.Draw(img)


def _brand_header(d, t):
    txt = t["brand_tag"]
    f = _font(34)
    w = d.textlength(txt, font=f)
    d.rounded_rectangle([56, 60, 56 + w + 70, 60 + 64], radius=32, fill=t["accent2"])
    d.ellipse([56 + 26, 60 + 26, 56 + 38, 60 + 38], fill=t["white"])
    d.text((56 + 52, 60 + 14), txt, font=f, fill=t["white"])


def _footer(d, t):
    f = _font(36)
    txt = f"📲 {t['whats']}   ·   {t['site']}"
    # sem emoji na fonte do slide
    txt = f"{t['whats']}   ·   {t['site']}"
    w = d.textlength(txt, font=f)
    d.text(((W - w) // 2, H - 90), txt, font=f, fill=t["muted"])


SERIES_IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "series")


def _foto_fundo(img, t, item, y0):
    """15/set — foto da SÉRIE (assets/series/<serie>.webp, gerada 1x) cobrindo do y0 até o pé do
    card, com degradê do navy por cima (texto fica no navy, foto fica embaixo). Sem foto → card
    liso, como antes. É o que mata o 'card escuro genérico' sem custo por post."""
    from PIL import Image
    key = item.get("serie") if isinstance(item, dict) else None
    if not key:
        return None
    # 15/set: POOL por série (assets/series/<serie>/*.jpg — fotos reais da loja/site) girando por
    # dia + passo; sem pool, cai na foto única <serie>.webp. Sem nada, card liso.
    pool = sorted(glob.glob(os.path.join(SERIES_IMG_DIR, key, "*.jp*g")) +
                  glob.glob(os.path.join(SERIES_IMG_DIR, key, "*.webp")))
    if pool:
        idx = (datetime.now().timetuple().tm_yday * 7 + int(item.get("passo") or 0)) % len(pool)
        fp = pool[idx]
    else:
        fp = os.path.join(SERIES_IMG_DIR, f"{key}.webp")
    if not os.path.exists(fp):
        return None
    try:
        foto = Image.open(fp).convert("RGB")
    except Exception:
        return None
    hh = H - y0
    r = max(W / foto.width, hh / foto.height)
    foto = foto.resize((round(foto.width * r), round(foto.height * r)), Image.LANCZOS)
    x = (foto.width - W) // 2
    y = max(0, (foto.height - hh) // 3)
    foto = foto.crop((x, y, x + W, y + hh))
    img.paste(foto, (0, y0))
    band = 260
    grad = Image.new("L", (1, band))
    for i in range(band):
        grad.putpixel((0, i), int(255 * (1 - i / (band - 1)) ** 1.6))
    grad = grad.resize((W, band))
    img.paste(Image.new("RGB", (W, band), t["bg"]), (0, y0), grad)
    # rodapé legível sobre a foto
    foot = Image.new("L", (1, 140))
    for i in range(140):
        foot.putpixel((0, i), int(230 * (i / 139) ** 1.2))
    img.paste(Image.new("RGB", (W, 140), t["bg"]), (0, H - 140), foot.resize((W, 140)))
    return True


def slide_capa(t, item, outdir, n=1):
    img, d = _canvas(t)
    _foto_fundo(img, t, item, 640)
    _brand_header(d, t)
    # badge categoria
    fb = _font(34)
    badge = item["cat"]
    y = 320
    if item.get("serie_nome"):
        # 15/set: nome da SÉRIE (jornada) em cima, categoria/passo embaixo
        x = gi.pill(d, 56, 200, item["serie_nome"], fb, t["accent"], (10, 10, 10))
        if item.get("total", 1) > 1:
            gi.pill(d, x + 14, 200, f"{item['passo']}/{item['total']}", fb, t["card"], t["white"])
        gi.pill(d, 56, 272, badge, _font(28), t["accent2"], t["white"])
        y = 380
    else:
        gi.pill(d, 56, 200, badge, fb, t["accent"], (10, 10, 10))
    # titulo grande
    ft = _font(82, impact=True)
    lines = gi.wrap(d, item["titulo"], ft, W - 120)[:5]
    for ln in lines:
        d.text((56, y), ln, font=ft, fill=t["white"], stroke_width=2, stroke_fill=(0, 0, 0))
        y += int(ft.size * 1.02)
    # faixa inferior
    gi.pill(d, 56, H - 170, "ARRASTA PARA O LADO  ->", _font(34), t["accent"], (10, 10, 10))
    p = os.path.join(outdir, f"slide_{n}.png")
    img.save(p, quality=92)
    return p


def slide_conteudo(t, item, outdir, n=2):
    img, d = _canvas(t)
    _brand_header(d, t)
    ft = _font(50, impact=True)
    gi.pill(d, 56, 200, "COMO FUNCIONA", ft, t["accent2"], t["white"])
    y = 360
    fb = _font(46, bold=False)
    for b in item["bullets"]:
        # marcador
        d.ellipse([56, y + 12, 56 + 22, y + 34], fill=t["accent"])
        lines = gi.wrap(d, b, fb, W - 180)
        gi.draw_lines(d, lines, fb, 100, y, t["white"], int(fb.size * 1.32))
        y += max(120, len(lines) * int(fb.size * 1.32) + 40)
    _footer(d, t)
    p = os.path.join(outdir, f"slide_{n}.png")
    img.save(p, quality=92)
    return p


def slide_site(t, item, outdir, n=3):
    """15/set — 'VEJA MAIS NO SITE': o post mostra o problema, o site dá o próximo passo."""
    img, d = _canvas(t)
    _foto_fundo(img, t, item, 900)
    _brand_header(d, t)
    fs = _font(44)
    gi.pill(d, 56, 200, "VEJA MAIS NO SITE", fs, t["accent"], (10, 10, 10))
    ft = _font(72, impact=True)
    y = 330
    for ln in gi.wrap(d, item.get("pagina", t["site"]), ft, W - 112)[:4]:
        d.text((56, y), ln, font=ft, fill=t["white"]); y += int(ft.size * 1.05)
    y += 30
    fb = _font(40, bold=False)
    sub = "Consulta, prazos e o que fazer — explicado sem juridiquês. E o botão do WhatsApp lá dentro."
    for ln in gi.wrap(d, sub, fb, W - 112):
        d.text((56, y), ln, font=fb, fill=t["muted"]); y += 52
    # caixa com a URL curta (15/set: digitável — dldespachante.com.br/multa — o Instagram não linka)
    try:
        from dlm import series_despachante as _sd
        url = _sd.curta(item) if item.get("serie") else t["site"] + item.get("link", "")
    except Exception:
        url = t["site"] + item.get("link", "")
    size = 46
    fu = _font(size)
    while d.textlength(url, font=fu) > W - 112 - 80 and size > 26:
        size -= 2; fu = _font(size)
    w = d.textlength(url, font=fu)
    y += 40
    d.rounded_rectangle([56, y, 56 + w + 80, y + 104], radius=22, fill=t["accent2"])
    d.text((56 + 40, y + 26), url, font=fu, fill=t["white"])
    d.text((56, y + 150), "digita no navegador ou toca no link da bio", font=_font(30, bold=False), fill=t["accent"])
    if item.get("serie") == "multa":
        try:
            from dlm import series_despachante as _sd
            fr = _font(24, bold=False)
            for i_, ln in enumerate(gi.wrap(d, _sd.RODAPE_DEFESA, fr, W - 112)[:2]):
                d.text((56, y + 200 + i_ * 30), ln, font=fr, fill=t["muted"])
        except Exception:
            pass
    _footer(d, t)
    p = os.path.join(outdir, f"slide_{n}.png")
    img.save(p, quality=92)
    return p


def slide_cta(t, outdir, n=3, item=None):
    img, d = _canvas(t)
    _brand_header(d, t)
    cy = H // 2 - 180
    fs = _font(44)
    item = item or {}
    seal = item.get("cta_seal") or t.get("cta_seal", "FALA COM A GENTE")
    sw = d.textlength(seal, font=fs)
    gi.pill(d, (W - sw) // 2 - 30, cy, seal, fs, t["accent"], (10, 10, 10))
    big = item.get("cta_big") or t.get("cta_big", ["RESOLVEMOS", "PRA VOCÊ"])
    fbig = _font(84 if max(len(x) for x in big) > 14 else 92, impact=True)
    y = cy + 110
    for ln in big:
        w = d.textlength(ln, font=fbig)
        d.text(((W - w) // 2, y), ln, font=fbig, fill=t["white"])
        y += int(fbig.size * 1.03)
    # caixa whats
    fw = _font(54)
    wtxt = t["whats"]
    w = d.textlength(wtxt, font=fw)
    d.rounded_rectangle([(W - w) // 2 - 50, y + 50, (W + w) // 2 + 50, y + 150],
                        radius=22, fill=(37, 211, 102))
    d.text(((W - w) // 2, y + 70), wtxt, font=fw, fill=(5, 50, 30))
    fsite = _font(40, bold=False)
    w2 = d.textlength(t["site"], font=fsite)
    d.text(((W - w2) // 2, y + 180), t["site"], font=fsite, fill=t["muted"])
    p = os.path.join(outdir, f"slide_{n}.png")
    img.save(p, quality=92)
    return p


# ----------------------------------------------------------------- legenda
def _rodape_serie(t, item):
    """Fecho fixo da legenda: página do site da série + zap (15/set)."""
    # 15/set — regra do dono: TODA legenda manda acessar o site e ver mais informações.
    if item.get("link"):
        try:
            from dlm import series_despachante as _sd
            _u = _sd.curta(item) if item.get("serie") else t["site"] + item["link"]
        except Exception:
            _u = t["site"] + item["link"]
        _rod = ("\n" + _sd.RODAPE_DEFESA) if item.get("serie") == "multa" else ""
        return (f"\n\n🌐 Acesse e veja mais informações: {_u}"
                f"\n📲 Manda a placa no WhatsApp: {t['whats']}{_rod}\n\n")
    return (f"\n\n🌐 Acesse e veja mais informações: {t['site']}"
            f"\n📲 Fala com a gente no WhatsApp: {t['whats']}\n\n")


def build_caption(t, item):
    base = f"{item['titulo']}\n\n"
    base += "\n".join(f"✅ {b}" for b in item["bullets"])
    base += _rodape_serie(t, item)
    if t.get("instagram"):
        base += f"Siga {t['instagram']}\n\n"
    base += _hashtags_do_dia(t)
    return base


def groq_caption(t, item):
    """Reescreve a legenda na voz da marca (Groq, se houver chave). Fallback: base."""
    bullets = " | ".join(item["bullets"])
    travas = ""
    if item.get("serie"):
        from dlm import series_despachante as _sd
        travas = _sd.TRAVAS + " "
        serie_ctx = (f"SÉRIE: {item.get('serie_nome')} (passo {item.get('passo')}/{item.get('total')}). "
                     f"Termine dizendo que tem mais no site {t['site']}{item['link']} e pra mandar a placa no WhatsApp. ")
    else:
        serie_ctx = (f"Termine convidando a acessar o site {t['site']} pra ver mais informações "
                     "e a falar no WhatsApp. ")
    prompt = (
        f"{t['voz']}\n\n"
        "Escreva uma legenda de Instagram (português BR) sobre o tema abaixo. "
        "Regras: 1ª linha é um gancho curto (no máx 1 emoji). Depois 3-4 linhas curtas. "
        + serie_ctx + travas +
        "NÃO invente serviços além dos listados. "
        "Despachante NÃO renova CNH, NÃO emite habilitação e NÃO dá curso: ele ORIENTA o cidadão "
        "no gov.br/DETRAN e REPRESENTA cursos homologados (reciclagem, MOPP). Nunca escreva 'renovamos', "
        "'fazemos sua CNH' ou 'nosso curso'. "
        "NÃO use hashtags (eu adiciono depois).\n\n"
        f"TEMA: {item['titulo']}\nPONTOS: {bullets}"
    )
    try:
        from dlm import cerebro
        txt = cerebro.completar(prompt)          # Gemini -> Groq
        if txt and item.get("serie") and _legenda_viola(txt):
            txt = ""                             # trava: legenda com claim proibido → fallback fixo
        if txt:
            txt = txt.strip().strip('"')
            return f"{txt}\n\n📲 WhatsApp: {t['whats']}  ·  🌐 {t['site']}\n\n" + _hashtags_do_dia(t)
    except Exception:
        pass
    return build_caption(t, item)


# ----------------------------------------------------------------- seleção do dia
def topic_of_the_day(t):
    banco = t["conteudo"]
    idx = datetime.now().timetuple().tm_yday % len(banco)
    return banco[idx]


# ----------------------------------------------------------------- postagem Meta (por marca)
def _brand_tokens(t):
    e = t["env"]
    return (dist._env(e["token"]), dist._env(e["ig"]), dist._env(e["page"]))


def publish_brand(t, prefix, image_paths, caption):
    """Posta carrossel no IG (+ foto no FB, exceto marcas ig_only) usando os TOKENS DA MARCA."""
    token, ig_id, page_id = _brand_tokens(t)
    ig_only = t.get("ig_only", False)
    # IG-only ainda precisa de token + ig_id (o token é da Página vinculada, exigência do Meta),
    # mas NÃO precisa de page_id pois não publicamos nada no feed do Facebook.
    falta = not (token and ig_id) if ig_only else not (token and ig_id and page_id)
    if falta:
        raise RuntimeError(f"Tokens Meta da marca ausentes ({t['env']}).")
    from PIL import Image
    os.makedirs(PUBLIC_IMG_DIR, exist_ok=True)
    GRAPH = dist.GRAPH
    base = dist.PUBLIC_BASE_URL
    public_urls = []
    for i, p in enumerate(image_paths, 1):
        fname = f"{prefix}_s{i}.jpg"
        Image.open(p).convert("RGB").save(os.path.join(PUBLIC_IMG_DIR, fname), "JPEG", quality=90)
        public_urls.append(f"{base}/static/social/{fname}")

    # Instagram carrossel
    children = []
    for u in public_urls:
        res = dist._graph_post(f"{GRAPH}/{ig_id}/media",
                               {"image_url": u, "is_carousel_item": "true", "access_token": token})
        children.append(res["id"])
    cont = dist._graph_post(f"{GRAPH}/{ig_id}/media",
                            {"media_type": "CAROUSEL", "children": ",".join(children),
                             "caption": caption, "access_token": token})["id"]
    import time
    time.sleep(3)
    ig = dist._graph_post(f"{GRAPH}/{ig_id}/media_publish",
                          {"creation_id": cont, "access_token": token})
    # Facebook foto (pulado nas marcas ig_only — DL Mobilidade e 4kitem)
    fb = None
    if not ig_only:
        fb = dist._graph_post(f"{GRAPH}/{page_id}/photos",
                              {"caption": caption, "url": public_urls[0], "access_token": token})
    # Story automatico (capa em 9:16) — desligavel com SOCIAL_STORY=0
    story = None
    if dist._env("SOCIAL_STORY", "1") == "1":
        try:
            story_jpg = os.path.join(PUBLIC_IMG_DIR, f"{prefix}_story.jpg")
            dist._story_image(image_paths[0], story_jpg)
            story_url = f"{base}/static/social/{prefix}_story.jpg"
            sc = dist._graph_post(f"{GRAPH}/{ig_id}/media",
                                  {"media_type": "STORIES", "image_url": story_url,
                                   "access_token": token})["id"]
            time.sleep(2)
            story = dist._graph_post(f"{GRAPH}/{ig_id}/media_publish",
                                     {"creation_id": sc, "access_token": token})
        except Exception as e:
            print(f"   ! Story da marca falhou (segue): {e}")
    return {"instagram": ig, "facebook": fb, "story": story}


# ----------------------------------------------------------------- run
_PROIBIDO = ("renovamos", "fazemos sua cnh", "fazemos a sua cnh", "nosso curso", "garantimos",
             "sem pôr o pé no detran", "sem por o pe no detran", "emitimos sua cnh", "nossa autoescola")


def _legenda_viola(txt):
    low = (txt or "").lower()
    return any(p in low for p in _PROIBIDO)


def generate(brand_key, outdir=None, item=None, slot=None):
    t = BRANDS[brand_key]
    if t.get("photo_based"):
        return generate_dl(brand_key, outdir)
    if item is None and t.get("series"):
        from dlm import series_despachante as _sd
        now = datetime.now()
        item = _sd.escolher(slot or "manha", now.timetuple().tm_yday, now.weekday())
    item = item or topic_of_the_day(t)
    if outdir is None:
        day = datetime.now().strftime("%Y-%m-%d")
        outdir = os.path.join(OUT_BASE, f"{day}_{brand_key}" + (f"_{slot}" if slot else ""))
    os.makedirs(outdir, exist_ok=True)
    if item.get("serie"):
        paths = [slide_capa(t, item, outdir, 1),
                 slide_conteudo(t, item, outdir, 2),
                 slide_site(t, item, outdir, 3),
                 slide_cta(t, outdir, 4, item=item)]
    else:
        paths = [slide_capa(t, item, outdir, 1),
                 slide_conteudo(t, item, outdir, 2),
                 slide_cta(t, outdir, 3)]
    caption = groq_caption(t, item)
    with open(os.path.join(outdir, "legenda.txt"), "w", encoding="utf-8") as f:
        f.write(caption)
    return paths, caption, item


def _log_post(brand_key, slot, item, r):
    """Registro do que foi ao ar (DATA_DIR/marcas_posts.jsonl) — o insights.py lê daqui
    pra medir alcance/salvamento POR SÉRIE (15/set)."""
    try:
        import json
        d = dist.DATA_DIR
        ig = (r or {}).get("instagram") or {}
        with open(os.path.join(d, "marcas_posts.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="minutes"),
                                "brand": brand_key, "slot": slot,
                                "serie": item.get("serie"), "passo": item.get("passo"),
                                "titulo": item.get("titulo"), "link": item.get("link"),
                                "ig_media_id": ig.get("id"),
                                "story_id": ((r or {}).get("story") or {}).get("id")},
                               ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"   ! log do post falhou (segue): {e}")


def publish_story_brand(t, prefix, image_path):
    """Só o STORY (9:16 da capa) — slot da noite 'veja mais no site' (15/set)."""
    token, ig_id, _ = _brand_tokens(t)
    if not (token and ig_id):
        raise RuntimeError(f"Tokens Meta da marca ausentes ({t['env']}).")
    import time
    os.makedirs(PUBLIC_IMG_DIR, exist_ok=True)
    story_jpg = os.path.join(PUBLIC_IMG_DIR, f"{prefix}_story.jpg")
    dist._story_image(image_path, story_jpg)
    story_url = f"{dist.PUBLIC_BASE_URL}/static/social/{prefix}_story.jpg"
    sc = dist._graph_post(f"{dist.GRAPH}/{ig_id}/media",
                          {"media_type": "STORIES", "image_url": story_url,
                           "access_token": token})["id"]
    time.sleep(2)
    return {"story": dist._graph_post(f"{dist.GRAPH}/{ig_id}/media_publish",
                                      {"creation_id": sc, "access_token": token})}


def run(brand_key, post=False, slot=None):
    t = BRANDS[brand_key]
    paths, caption, item = generate(brand_key, slot=slot)
    print(f"[{brand_key}{'/' + slot if slot else ''}] tópico: {item['titulo']} | {len(paths)} slides")
    if post:
        day = datetime.now().strftime("%Y%m%d")
        prefix = f"{brand_key}_{day}" + (f"_{slot}" if slot else "")
        if slot == "noite":
            # story só com o slide "veja mais no site" (3º) — sem repetir o carrossel da manhã
            r = publish_story_brand(t, prefix, paths[2] if len(paths) > 2 else paths[0])
        else:
            r = publish_brand(t, prefix, paths, caption)
        _log_post(brand_key, slot, item, r)
        print(f"[{brand_key}] publicado: {r}")
    return paths, caption


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brand", help="chave da marca (ex: despachante)")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--slot", default=None, help="manha|meio|tarde|noite (despachante)")
    args = ap.parse_args()
    if args.brand not in BRANDS:
        print(f"Marca '{args.brand}' nao existe. Disponiveis: {list(BRANDS)}")
        return
    run(args.brand, post=args.post, slot=args.slot)


if __name__ == "__main__":
    main()


# ----------------------------------------------------------------- reel da marca (27/jul)
def publish_reel_marca(brand_key, video_filename, caption):
    """Publica um REEL no IG da marca (vídeo já em static/social/, servido pelo site).
    Reusa o fluxo assíncrono do reels.py, mas com os TOKENS DA MARCA."""
    t = BRANDS[brand_key]
    token, ig_id, _ = _brand_tokens(t)
    if not (token and ig_id):
        raise RuntimeError(f"Tokens da marca ausentes ({t['env']}).")
    return _publish_reel(token, ig_id, video_filename, caption)


def publish_reel_dest(dest, video_filename, caption, video_url=None):
    """🎞️ VIDEOTECA/MIDIATECA (7/ago + 18/ago). dest='radio' usa os tokens da PRÓPRIA
    Rádio (META_*); dest='desp' usa os da marca (DESP_*). video_url opcional: vídeo
    servido de fora de static/videos (ex.: upload da midiateca no volume)."""
    if dest == "radio":
        # 24/set/26: a Rádio foi vendida ao Gabriel. O 4kitem não publica mais nela.
        raise RuntimeError("Destino 'radio' desativado: a Rádio SC News não é mais do grupo.")
    t = BRANDS["dl_mobilidade"]
    token, ig_id, _ = _brand_tokens(t)
    if not (token and ig_id):
        raise RuntimeError(f"Tokens da marca ausentes ({t['env']}).")
    return _publish_reel(token, ig_id, video_filename, caption, video_url=video_url)


def _publish_reel(token, ig_id, video_filename, caption, video_url=None):
    import time
    import requests as rq
    GRAPH = dist.GRAPH
    video_url = video_url or f"{dist.PUBLIC_BASE_URL}/static/videos/{video_filename}"
    cont = dist._graph_post(f"{GRAPH}/{ig_id}/media",
                            {"media_type": "REELS", "video_url": video_url,
                             "caption": caption, "share_to_feed": "true",
                             "access_token": token})["id"]
    for _ in range(40):
        time.sleep(6)
        st = rq.get(f"{GRAPH}/{cont}",
                    params={"fields": "status_code", "access_token": token}, timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Processamento do reel falhou: {st}")
    else:
        raise RuntimeError("Reel não ficou pronto a tempo.")
    return dist._graph_post(f"{GRAPH}/{ig_id}/media_publish",
                            {"creation_id": cont, "access_token": token})


# ── 🎞️ VIDEOTECA DL (7/ago — pedido do dono: todos os vídeos numa prateleira no admin,
#    botão pra publicar no IG da RÁDIO ou do DESPACHANTE, manual, quando ele quiser) ──
import os as _os

VIDEOTECA_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "static", "videos", "dlmob")


def videoteca():
    """Vídeos da prateleira (static/videos/dlmob), prontos pra publicar."""
    try:
        return sorted(f for f in _os.listdir(VIDEOTECA_DIR) if f.endswith(".mp4"))
    except Exception:
        return []


_VT_MODELOS = [
    ("akasha", "Akasha", "1000W de força, farol full LED e porte de moto grande."),
    ("gataka", "Gataka", "a queridinha do dia a dia — confortável e estilosa."),
    ("vega", "Vega", "leve, ágil e a mais econômica da linha."),
    ("zilla", "Zilla", "a porta de entrada da linha NXT — pronta entrega."),
    ("classe_b", "Scooter elétrica", "praticidade pra família inteira."),
    ("classe_c", "Scooter elétrica", "chega de esperar ônibus — vá e volte no seu tempo."),
    ("todas", "Linha NXT completa", "modelos pra todo perfil e bolso."),
]


def caption_videoteca(filename):
    nome, pitch = "Scooter elétrica", "a mobilidade que cabe no teu bolso."
    for chave, n, p in _VT_MODELOS:
        if chave in filename:
            nome, pitch = n, p
            break
    return (f"🛵 {nome} na DL Mobilidade — Schroeder!\n\n"
            f"{pitch}\n\n"
            "✅ Sem CNH e sem emplacamento (CONTRAN 996)\n"
            "✅ Zero gasolina — recarrega na tomada de casa\n"
            "💳 Até 48x ViaCredi · parcelas a partir de R$ 200*\n\n"
            "🏁 TEST-RIDE GRÁTIS: vem dar uma volta antes de decidir!\n"
            "📍 R. Mal. Castelo Branco, 2838 — Centro, Schroeder\n"
            "📲 WhatsApp (47) 99776-6831\n\n"
            "*sujeito a análise de crédito\n\n"
            "#scootereletrica #Schroeder #JaraguaDoSul #ValeDoItapocu #DLMobilidade")


REELS_DLMOB = {
    "zilla": {
        "arquivo": "dlmob_zilla.mp4",
        "titulo": "Zilla — test-ride / pronta entrega (R$ 4.990)",
        "caption": ("🛵 NÃO COMPRE SCOOTER SEM DAR UMA VOLTA ANTES.\n\n"
                    "Vem na loja, senta, liga e anda. Sem compromisso.\n\n"
                    "✅ Pronta entrega\n"
                    "✅ Sem CNH e sem emplacamento (CONTRAN 996)\n"
                    "✅ Zero gasolina — recarrega na tomada\n"
                    "💳 Até 24x no cartão ou 48x pela Viacredi*\n\n"
                    "📍 R. Mal. Castelo Branco, 2838 — Centro, Schroeder\n"
                    "📲 WhatsApp (47) 99716-2967\n\n"
                    "*Financiamento sujeito a análise de crédito.\n\n"
                    "#scootereletrica #schroeder #jaraguadosul #guaramirim #semcnh "
                    "#mobilidadeeletrica #dlmobilidade #nxt #testride"),
    },
    "akasha": {
        "arquivo": "dlmob_akasha.mp4",
        "titulo": "Akasha — 1000W LED / pronta entrega (R$ 7.990)",
        "caption": ("⚡ AKASHA 1000W: a scooter que TODO MUNDO olha quando passa.\n\n"
                    "Iluminação em LED, design agressivo e pronta entrega em Schroeder.\n\n"
                    "✅ Sem CNH e sem emplacamento (CONTRAN 996)\n"
                    "✅ Zero gasolina — recarrega na tomada\n"
                    "💳 Até 24x no cartão ou 48x pela Viacredi*\n\n"
                    "🛵 Vem dar uma volta antes de decidir — sem compromisso.\n"
                    "📍 R. Mal. Castelo Branco, 2838 — Centro, Schroeder\n"
                    "📲 WhatsApp (47) 99716-2967\n\n"
                    "*Financiamento sujeito a análise de crédito.\n\n"
                    "#scootereletrica #schroeder #jaraguadosul #guaramirim #semcnh "
                    "#mobilidadeeletrica #dlmobilidade #nxt"),
    },
}
