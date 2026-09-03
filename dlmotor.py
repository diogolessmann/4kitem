# -*- coding: utf-8 -*-
"""🤖 DL MOTOR — publica sozinho as marcas do grupo no IG do Despachante.

Substitui o `marca_job` que morava no scheduler.py da Rádio SC News. Aquele
gerava card de texto no PIL; este usa as FOTOS REAIS que já estão na Central DL,
e publica feed + story.

Como escolhe a mídia do dia:
  1. o que nunca foi publicado vem primeiro;
  2. depois, o publicado há mais tempo;
  3. nada que tenha ido ao ar nos últimos DLMOTOR_DESCANSO_DIAS entra na roda.

Travas (o motor da Rádio não tinha nenhuma):
  - marca no disco o que já postou HOJE — restart do Railway não republica;
  - DLMOTOR_MODO=preview monta o post e NÃO publica (default, pra calibrar);
  - DLMOTOR_ON=0 desliga tudo.

Ligar pra valer: DLMOTOR_MODO=live no Railway.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta

import dlcentral as dlc

_DATA = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
_ESTADO = os.path.join(_DATA, "dlmotor_estado.json")

# ─────────────────────────────────────────────────────────────────── agenda
# Mesmo ritmo que já roda hoje na Rádio — motor novo, cadência igual, pra dar
# pra medir o que mudou. dia_semana: 0=seg … 6=dom; None = todo dia.
AGENDA = [
    {"marca": "despachante", "hora": 10, "dias": None},
    {"marca": "dlmob", "hora": 16, "dias": (1, 3, 5)},          # ter/qui/sáb
    # {"marca": "defesas", "hora": 15, "dias": (0, 2, 4)},      # descomentar p/ ligar
]

DESCANSO = int(os.environ.get("DLMOTOR_DESCANSO_DIAS", "30"))


def ligado():
    return os.environ.get("DLMOTOR_ON", "1") == "1"


def ao_vivo():
    return os.environ.get("DLMOTOR_MODO", "preview").lower() == "live"


# ─────────────────────────────────────────────────────── trava anti-republicação
def _estado():
    try:
        with open(_ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _marca_feito(marca, arquivo):
    d = _estado()
    d[marca] = {"dia": datetime.now().strftime("%Y-%m-%d"), "arquivo": arquivo}
    try:
        with open(_ESTADO, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        dlc.log(f"⚠️ motor: não consegui gravar a trava ({e})")


def ja_foi_hoje(marca):
    return _estado().get(marca, {}).get("dia") == datetime.now().strftime("%Y-%m-%d")


# ────────────────────────────────────────────────────────── escolha da mídia
def _ultima_publicacao(item):
    """datetime da última vez que foi ao ar, ou None se nunca foi."""
    pubs = (item.get("meta") or {}).get("publicados") or []
    if not pubs:
        return None
    ano = datetime.now().year
    quando = []
    for p in pubs:
        try:                                   # meta guarda "dd/mm HH:MM"
            d = datetime.strptime(p.get("quando", ""), "%d/%m %H:%M").replace(year=ano)
            if d > datetime.now() + timedelta(days=1):
                d = d.replace(year=ano - 1)    # virada de ano
            quando.append(d)
        except Exception:
            pass
    return max(quando) if quando else datetime(2000, 1, 1)


def escolhe(marca):
    """A mídia da vez, ou None se não sobrou nada descansado."""
    agora = datetime.now()
    fila = []
    for it in dlc.listar(marca):
        nome = it["arquivo"]
        if nome.rsplit(".", 1)[0].endswith("_story"):
            continue                            # story entra pareado, não sozinho
        ult = _ultima_publicacao(it)
        if ult and (agora - ult).days < DESCANSO:
            continue
        # nunca publicado primeiro, na ordem do nome (01-, 02-, … é sequência
        # que o dono montou); depois, o publicado há mais tempo
        fila.append(((1, ult) if ult else (0, nome.lower()), it))
    if not fila:
        return None
    fila.sort(key=lambda x: x[0])
    return fila[0][1]


def _story_de(marca, arquivo):
    """O `<nome>_story.jpg` correspondente, se existir no acervo."""
    base, ext = arquivo.rsplit(".", 1)
    alvo = f"{base}_story.{ext}"
    for it in dlc.listar(marca):
        if it["arquivo"] == alvo:
            return alvo
    return None


# ───────────────────────────────────────────────────────────────── story
def publica_story(marca, arquivo):
    """Story é media_type=STORIES — o dlcentral só sabe feed e reel."""
    tok, ig = dlc._tokens()
    _caminho, url, tipo = dlc.acha(marca, arquivo)
    campo = "image_url" if tipo == "foto" else "video_url"
    cont = dlc._graph_post(f"{dlc.GRAPH}/{ig}/media",
                           {"media_type": "STORIES", campo: url,
                            "access_token": tok})["id"]
    time.sleep(5)
    r = dlc._graph_post(f"{dlc.GRAPH}/{ig}/media_publish",
                        {"creation_id": cont, "access_token": tok})
    return r.get("id")


# ────────────────────────────────────────────────────────────────── o turno
def roda(marca, forcar=False):
    """Publica (ou monta, em preview) o post do dia dessa marca."""
    if not ligado():
        dlc.log(f"⏸️ motor desligado (DLMOTOR_ON=0) — {marca} pulada")
        return None
    if not forcar and ja_foi_hoje(marca):
        dlc.log(f"↩️ {marca} já foi hoje — nada a fazer")
        return None
    if not dlc.tokens_ok():
        dlc.log(f"⚠️ {marca}: sem DESP_PAGE_TOKEN/DESP_IG_USER_ID — pulada")
        return None

    item = escolhe(marca)
    if not item:
        dlc.log(f"📭 {marca}: acervo inteiro publicado nos últimos {DESCANSO} dias — "
                f"hora de subir material novo na Central DL")
        return None

    arquivo = item["arquivo"]
    legenda = (item.get("meta") or {}).get("legenda_venda") or dlc.gerar_legenda(marca, arquivo)

    if not ao_vivo():
        _marca_feito(marca, arquivo)
        dlc.log(f"👁️ PREVIEW {marca}: escolhi {arquivo} e escrevi a legenda — "
                f"NÃO publiquei (DLMOTOR_MODO=live pra valer)")
        return arquivo

    dlc._publicar_job(marca, arquivo, legenda)        # feed, síncrono aqui na thread
    story = _story_de(marca, arquivo)
    if story:
        try:
            publica_story(marca, story)
            dlc.log(f"📲 story publicado: {marca}/{story}")
        except Exception as e:
            dlc.log(f"⚠️ story falhou ({marca}/{story}): {e}")
    _marca_feito(marca, arquivo)
    return arquivo


# ─────────────────────────────────────────────────────────────── agendador
def _proximo(agora):
    """(datetime do próximo disparo, marca)."""
    melhor = None
    for slot in AGENDA:
        for adiante in range(8):
            d = (agora + timedelta(days=adiante)).replace(
                hour=slot["hora"], minute=0, second=0, microsecond=0)
            if d <= agora:
                continue
            if slot["dias"] is not None and d.weekday() not in slot["dias"]:
                continue
            if melhor is None or d < melhor[0]:
                melhor = (d, slot["marca"])
            break
    return melhor


def _laco():
    modo = "AO VIVO" if ao_vivo() else "preview (não publica)"
    dlc.log(f"🤖 motor iniciado — modo {modo}, descanso de {DESCANSO} dias")
    while True:
        prox = _proximo(datetime.now())
        if not prox:
            time.sleep(3600)
            continue
        quando, marca = prox
        espera = (quando - datetime.now()).total_seconds()
        if espera > 0:
            time.sleep(min(espera, 3600))       # acorda de hora em hora
        if datetime.now() >= quando:
            try:
                roda(marca)
            except Exception as e:
                dlc.log(f"❌ motor: turno de {marca} quebrou: {e}")
            time.sleep(90)                       # não repete o mesmo horário


def iniciar():
    if not ligado():
        return None
    t = threading.Thread(target=_laco, daemon=True, name="dlmotor")
    t.start()
    return t
