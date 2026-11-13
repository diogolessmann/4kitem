# -*- coding: utf-8 -*-
"""🎬 Material da Central DL no motor da DL Mobilidade (13/11/2026).

O dono, 13/11: "quero que use todas as imagens, tem muito material" (Central DL, aba dlmob) e "tá aí pra
usar o material". A regra da casa é "aprovado vai pro cofre": o que está na Central já passou pelo dono.
Então vale TUDO o que está lá. 13/11 à tarde: "tudo ali tá liberado, auditado e temos autorização"
(inclusive entregas com criança). A única trava é no TEXTO que o motor escreve: ciclomotor (X8, XE-30)
nunca recebe "sem CNH" (nem no card do carrossel, nem na legenda do reel).

1. FOTOS: entram no sorteio do carrossel diário, junto com as fotos do repositório (marcas.generate_dl).
2. REELS: terça, quinta e sábado a vaga das 16h vira um reel da Central, no lugar do carrossel. Não aumenta
   o feed: é a mesma vaga. O nunca publicado vai primeiro; empate, o mais novo. Sem reel ou se a Meta
   recusar → sai o carrossel. Publica pelo mesmo caminho do botão 🚀 (dlcentral._publicar_job).
   Desliga com DLMOB_REELS=0 no Railway.

Upload novo na Central entra sozinho. Para tirar um arquivo: 🗑️ na Central.
"""
import os
import re
from datetime import datetime

DIAS_REEL = (1, 3, 5)     # ter, qui, sáb (0 = segunda)
MARCA = "dlmob"
DESTINO = "dl_mobilidade"

# 13/11 tarde: a lista de fora (Vega, IA, criança, "zap", "conferir"...) caiu. O dono: "tudo ali tá liberado,
# auditado e temos autorização". Sobra só o que o MOTOR escreveria errado: o card do carrossel e a legenda
# fixa dizem "sem CNH", e X8 e XE-30 são ciclomotores (Mostruário DL v2, p. 6: "exige CNH A/ACC e placa").
CICLOMOTOR = re.compile(r"xe ?-?30|(?<![a-z0-9])x ?8(?![0-9])", re.I)
_SEM_CNH = re.compile(r"sem\s+(cnh|placa|emplacamento|habilita)", re.I)
# crítica 13/11: foto de X8 cujo NOME não diz X8 (sai pelo nome exato; o card diz "sem CNH")
FORA_FOTO = {"WhatsApp Image 2026-09-14 at 12.11.58 2.jpg"}
# "CONFERIR" no nome é recado do próprio dono ("5 Kay 200 por mes 100 km - CONFERIR PARCELA ANTES": a Kay
# em 48x com juro passa de R$ 200). Só vai para a fila depois que ele conferir e renomear.
_CONFERIR = re.compile(r"conferir", re.I)

# Os dois primeiros da fila (legenda conferida na checagem de lei de 13/11; banner 48x: risco assumido)
PRIORIDADE = ["DLMOB_AKASHA_A_visual_reel.mp4", "DLMOB_VZERO_1_divertida_reel.mp4"]

_RODAPE = ("📍 R. Mal. Castelo Branco, 2838 · Schroeder/SC\n"
           "📲 Pergunta o preço no WhatsApp (47) 99776-6831")
_TAGS = "#scootereletrica #schroeder #jaraguadosul #dlmobilidade #nxt"
_MODELOS = [("akasha", "Akasha"), ("v ?-?zero|v0", "V-Zero"), ("pancho", "Pancho"), ("kay", "Kay"),
            ("zilla", "Zilla"), ("jaya", "Jaya"), ("x13", "X13"), ("x8", "X8")]

LEGENDAS = {
    "DLMOB_AKASHA_A_visual_reel.mp4": (
        "Akasha: linhas angulares e luzes de LED que mudam de cor. 💜\n"
        "Motor de 1.000 W e até 32 km/h, dentro das medidas do autopropelido, segundo o fabricante: "
        "sem placa e sem CNH (Resolução CONTRAN 996).\n"
        "Bateria de grafeno 72 V 26 Ah, segundo o fabricante.\n"
        "Senta, liga e anda, sem compromisso: R. Mal. Castelo Branco, 2838, Schroeder/SC.\n"
        "📲 Pergunta o preço no WhatsApp (47) 99776-6831\n"
        "#scootereletrica #akasha #schroeder #jaraguadosul #dlmobilidade"),
    "DLMOB_VZERO_1_divertida_reel.mp4": (
        "Quer uma scooter divertida de pilotar? 🛵\n"
        "V-Zero: 90 kg e motor de 800 W, até 32 km/h, dentro das medidas do autopropelido, segundo o "
        "fabricante: sem placa e sem CNH (Resolução CONTRAN 996).\n"
        "Bateria de grafeno 60 V 23 Ah, segundo o fabricante.\n"
        "Vem dar uma volta na loja: R. Mal. Castelo Branco, 2838, Schroeder/SC.\n"
        "📲 Pergunta o preço no WhatsApp (47) 99776-6831\n"
        "#scootereletrica #vzero #schroeder #jaraguadosul #dlmobilidade"),
}


# ─────────────────────────────────────────────────────────────── fotos
def foto_ok(arquivo):
    return not CICLOMOTOR.search(arquivo) and arquivo not in FORA_FOTO   # card diz "sem CNH": ciclomotor fora


def fotos_central():
    """Caminhos locais das fotos da Central que podem ir ao sorteio (vazio fora do Railway)."""
    import dlcentral
    out = []
    for it in dlcentral.listar(MARCA):
        if it.get("tipo") == "foto" and foto_ok(it["arquivo"]):
            try:
                out.append(dlcentral.acha(MARCA, it["arquivo"])[0])
            except Exception:
                pass
    return out


# ─────────────────────────────────────────────────────────────── reels
def ligado():
    return os.environ.get("DLMOB_REELS", "1") == "1"


def dia_de_reel(weekday=None):
    wd = datetime.now().weekday() if weekday is None else weekday
    return ligado() and wd in DIAS_REEL


def modelo_de(arquivo):
    a = arquivo.lower().replace("_", " ")
    return next((nome for rx, nome in _MODELOS if re.search(rx, a)), "")


def _publicado(i):
    return bool((i.get("meta") or {}).get("publicados"))


def escolher(itens, tamanho=None):
    """itens = dlcentral.listar('dlmob'). Devolve o arquivo do reel da vez, ou None.
    tamanho(arquivo) → bytes: o mesmo vídeo subido 2x com nomes diferentes (13/11: "1 Kay preta 100 km.mp4"
    e "DLMOB_kay-preta_100km_13nov.mp4") não sai de novo depois que um deles foi publicado."""
    vids = [i for i in itens if i.get("tipo") == "video" and not _CONFERIR.search(i["arquivo"])]
    if tamanho:
        ja = {tamanho(i["arquivo"]) for i in itens if i.get("tipo") == "video" and _publicado(i)} - {None}
        vids = [i for i in vids if _publicado(i) or tamanho(i["arquivo"]) not in ja]
    if not vids:
        return None

    def chave(i):
        pubs = len((i.get("meta") or {}).get("publicados") or [])
        pri = PRIORIDADE.index(i["arquivo"]) if i["arquivo"] in PRIORIDADE else len(PRIORIDADE)
        return (pubs, pri, -(i.get("mtime") or 0))
    return min(vids, key=chave)["arquivo"]


def _legenda_fixa(modelo, ciclomotor=False):
    nome = f"{modelo} na DL Mobilidade" if modelo else "Scooter elétrica NXT na DL Mobilidade"
    if ciclomotor:      # Mostruário DL v2, p. 6
        return (f"🛵 {nome}, em Schroeder.\n"
                "É ciclomotor: exige CNH (A ou ACC) e placa. Zero gasolina, recarga na tomada.\n"
                "Vem conhecer na loja, sem compromisso.\n\n"
                f"{_RODAPE}\n\n{_TAGS}")
    return (f"🛵 {nome}, em Schroeder.\n"
            "Sem CNH e sem placa (CONTRAN 996), zero gasolina, recarga na tomada de casa.\n"
            "Até 48x pela Viacredi*. Vem dar uma volta, sem compromisso.\n\n"
            f"{_RODAPE}\n*Financiamento sujeito a análise de crédito.\n\n{_TAGS}")


def _inventou(txt):
    """O que a IA da Central pode inventar e engana: autonomia, outro nº de parcelas, preço que não é do dono."""
    from dlm import marcas
    # só "a partir de R$ 200" é dado do dono (mostruário p. 8); "Kay por R$ 200" engana (crítica 13/11)
    limpo = re.sub(r"a partir de R\$\s?200\b", "", txt or "", flags=re.I)
    return marcas._dl_viola(limpo)


def legenda(arquivo):
    if arquivo in LEGENDAS:
        return LEGENDAS[arquivo]
    if CICLOMOTOR.search(arquivo):     # crítica 13/11: a IA recebe "sem CNH" nos FATOS; ciclomotor = texto fixo
        return _legenda_fixa(modelo_de(arquivo), True)
    try:
        import dlcentral
        txt = (dlcentral.meta_get(MARCA, arquivo).get("legenda_venda")
               or dlcentral.gerar_legenda(MARCA, arquivo))
    except Exception:
        txt = ""
    cic = bool(CICLOMOTOR.search(arquivo))
    if not txt or _inventou(txt) or (cic and _SEM_CNH.search(txt)):
        return _legenda_fixa(modelo_de(arquivo), cic)
    if re.search(r"48 ?x|parcela|financ", txt, re.I) and "análise de crédito" not in txt:
        txt += "\n*Financiamento sujeito a análise de crédito."
    return txt


def postar_reel(ao_vivo=True):
    """None = não tem reel hoje (o turno segue o carrossel). Senão, o resultado do turno."""
    import dlcentral

    def tamanho(a):
        try:
            return os.path.getsize(dlcentral.acha(MARCA, a)[0])
        except Exception:
            return None
    arquivo = escolher(dlcentral.listar(MARCA), tamanho)
    if not arquivo:
        return None
    leg = legenda(arquivo)
    if not ao_vivo:
        return f"PREVIEW reel: {arquivo} · {leg.splitlines()[0][:60]}"
    antes = len(dlcentral.meta_get(MARCA, arquivo).get("publicados") or [])
    dlcentral._publicar_job(MARCA, arquivo, leg, destino=DESTINO)     # síncrono; loga ✅/❌ na Central
    depois = len(dlcentral.meta_get(MARCA, arquivo).get("publicados") or [])
    if depois > antes:
        return f"POSTADO reel {arquivo}"
    return None      # falhou (o motivo está no log da Central) → o turno posta o carrossel no lugar
