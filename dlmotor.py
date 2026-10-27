# -*- coding: utf-8 -*-
"""🤖 DL MOTOR — agenda dos perfis do grupo, dentro do 4kitem (migrado da Rádio em 20/set/2026).

A Rádio está sendo vendida; o motor do despachante e o de mobilidade saem de lá e rodam aqui.
Conteúdo e publicação estão em dlm/ (marcas · series_despachante · mobilidade · insights) —
código portado 1:1 do repo da Rádio. Este arquivo só decide QUANDO rodar o quê.
(A versão anterior deste arquivo — foto da Central DL 1x/dia — está no git, commit 7ca9560.)

Grade (America/Sao_Paulo):
  10:00       despachante · manhã   (série "1 post = 1 página", alterna documentalista/defesa) — ÚNICO post de feed/dia
  16:00       DL Mobilidade · oferta de scooter com FOTO REAL no IG PRÓPRIO (tokens DLMOB_*) — 1x/dia
  17:30 48h   DL Defesas · dia sim, dia não, IG PRÓPRIO @dldefesas.multas (tokens DEFESAS_*)
  12:30       story da FILA de peças prontas (CONTEUDO_21) — FILA_PRONTA_ON=0 desliga
  (19:00 despachante · noite saiu em 27/10: era o MESMO tema da manhã, de novo, em story)
  hh:20 x6    SC News Mobilidade    (MOB_HORAS, default 7,10,12,15,18,20) — só com MOB_ON=1 + tokens MOB_*
  23:30       insights por série    (placar)

Travas:
  DLMOTOR_ON=0            desliga tudo
  DLMOTOR_MODO=preview    monta o post e NÃO publica (default — pra calibrar antes de ligar)
  DLMOTOR_MODO=live       publica
  estado em DATA_DIR/dlmotor_estado.json: o mesmo turno não repete no mesmo dia (restart do Railway)
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:                       # pragma: no cover
    TZ = None

import dlcentral as dlc

_DATA = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
_ESTADO = os.path.join(_DATA, "dlmotor_estado.json")


def agora():
    return datetime.now(TZ) if TZ else datetime.now()


def ligado():
    return os.environ.get("DLMOTOR_ON", "1") == "1"


def ao_vivo():
    return os.environ.get("DLMOTOR_MODO", "preview").lower() == "live"


# ─────────────────────────────────────────────────────────── turnos
def _desp(slot):
    if slot == "meio" and os.environ.get("DESP_MEIO_ON", "0") != "1":
        return "13h desligado (DESP_MEIO_ON!=1)"
    from dlm import marcas
    t = marcas.BRANDS["despachante"]
    token, ig_id, _ = marcas._brand_tokens(t)
    if not (token and ig_id):
        return "sem tokens DESP_* — pulado"
    # 26/set: dia de FILA (dia par) → o post das 10h é uma PEÇA PRONTA do Cofre (CONTEUDO_21).
    # Não aumenta o feed: é a mesma vaga da série. Fila vazia → segue a série de sempre.
    if slot == "manha":
        from dlm import fila_pronta
        if fila_pronta.dia_de_fila():
            r = fila_pronta.postar_feed(ao_vivo())
            if r is not None:
                return r
    if not ao_vivo():
        paths, cap, item = marcas.generate("despachante", slot=slot)
        return f"PREVIEW {item.get('serie')} {item.get('passo')}: {paths[0]}"
    marcas.run("despachante", post=True, slot=slot)
    return "POSTADO"


def _dlmob():
    from dlm import marcas
    t = marcas.BRANDS["dl_mobilidade"]
    token, ig_id, _ = marcas._brand_tokens(t)
    if not (token and ig_id):
        return "sem tokens DLMOB_* — pulado (scooter não posta mais no IG do Despachante)"
    if not ao_vivo():
        paths, cap, _ = marcas.generate("dl_mobilidade")
        return f"PREVIEW: {paths[0]}"
    marcas.run("dl_mobilidade", post=True)
    return "POSTADO"


def _kitem():
    """27/10: IG @4kitem (conta nova) no motor. 1 post/dia (aquecimento: conta nova não pode
    disparar volume). Só roda com KITEM_ON=1; sem os tokens KITEM_* o turno pula."""
    if os.environ.get("KITEM_ON", "0") != "1":
        return "KITEM_ON!=1"
    from dlm import marcas
    t = marcas.BRANDS["4kitem"]
    token, ig_id, _ = marcas._brand_tokens(t)
    if not (token and ig_id):
        return "sem tokens KITEM_* — pulado"
    if not ao_vivo():
        paths, cap, _ = marcas.generate("4kitem")
        return f"PREVIEW: {paths[0]}"
    marcas.run("4kitem", post=True)
    return "POSTADO"


def _mob(idx):
    if os.environ.get("MOB_ON", "0") != "1":
        return "MOB_ON!=1"
    from dlm import mobilidade
    if not mobilidade.tokens_ok():
        return "sem tokens MOB_* — pulado"
    r = mobilidade.run_slot(idx, post=ao_vivo())
    if not r:
        return "fila vazia (pulado)"
    if r.get("vacina"):          # matéria barrada e já marcada: a repescagem pega a próxima
        return "vacina barrou a matéria (%s) — fila vazia até a próxima tentativa" % r["vacina"]
    return "POSTADO" if ao_vivo() else f"PREVIEW: {r.get('preview', [''])[0]}"


def _story_fila():
    """26/set: 1 story/dia da fila de peças prontas (stories clean do CONTEUDO_21)."""
    if os.environ.get("FILA_PRONTA_ON", "1") != "1":
        return "fila desligada (FILA_PRONTA_ON!=1)"
    from dlm import fila_pronta
    return fila_pronta.postar_story(ao_vivo())


def _insights():
    from dlm import insights
    n = insights.coletar_marca("despachante", dias=7)
    m = insights.coletar_marca("dl_mobilidade", dias=7)
    return f"insights: despachante {n} · dl_mobilidade {m}"


def _defesas():
    """13/10: DL Defesas entra no motor automático, reaproveitando o pipeline que já existe
    (Central DL / dlcentral.py) em vez de duplicar banco de conteudo — a vacina, o rodape
    (CRDD/razao social) e a legenda_defesas_ok ja vivem la. So falta escolher o arquivo e postar.
    25/10: cadencia = dia sim, dia nao (1 post a cada 48h). Paridade pelo ordinal do dia (nao pelo dia do
    ano, que repete paridade na virada de 31/12 para 01/01)."""
    if agora().toordinal() % 2 != 0:
        return "dia de folga (cadencia 48h)"
    if not dlc.tokens_ok("defesas"):
        return "sem tokens DEFESAS_* — pulado"
    itens = [i for i in dlc.listar("defesas") if not i["meta"].get("publicados")]
    if not itens:
        return "fila vazia (pulado)"
    item = itens[-1]   # listar() vem do mais novo pro mais velho; pega o mais velho (FIFO)
    arquivo = item["arquivo"]
    legenda = item["meta"].get("legenda_venda") or dlc.gerar_legenda("defesas", arquivo)
    if not ao_vivo():
        return f"PREVIEW defesas: {arquivo}"
    dlc.publicar("defesas", arquivo, legenda, destino="defesas")
    return "POSTADO"


def _mob_horas():
    hs = [int(h) for h in os.environ.get("MOB_HORAS", "7,10,12,15,18,20").split(",") if h.strip().isdigit()]
    return hs or [7, 10, 12, 15, 18, 20]


def agenda():
    """[(id, hora, minuto, dias|None, func)] — dias: 0=seg … 6=dom."""
    a = [
        ("desp_manha", 10, 0, None, lambda: _desp("manha")),
        ("desp_story", 12, 30, None, _story_fila),
        ("kitem", 14, 0, None, _kitem),          # KITEM_ON=1 liga; 1 post/dia
        ("dlmob", 16, 0, None, _dlmob),
        ("defesas", 17, 30, None, _defesas),     # dia sim, dia nao (48h) — a paridade esta dentro de _defesas()
        ("insights", 23, 30, None, _insights),
    ]
    for i, h in enumerate(_mob_horas()):
        a.append((f"mob_{i}", h, 20, None, (lambda i=i: _mob(i))))
    return a


# ───────────────────────────────────────────── trava anti-repetição (por dia)
def _estado():
    try:
        with open(_ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# Falha que NAO e desfecho: o turno nao rodou por um motivo que pode passar sozinho
# (token que sumiu num deploy, rede, erro pontual). Nesses casos o lock NAO e gravado.
_MAX_TENTATIVAS = 4


def _recuperavel(r):
    t = str(r or "").lower()
    return ("sem tokens" in t) or t.startswith("quebrou") or ("fila vazia" in t)


def _feito(turno, definitivo=True, resultado=""):
    """23/set: antes gravava o lock SEMPRE, ate quando o turno saia por falta de token — e o
    post do dia sumia sem retentativa e sem aviso. Agora o lock so vale com desfecho."""
    e = _estado()
    hoje = agora().strftime("%Y-%m-%d")
    chave = "_tentativas_" + turno
    if definitivo:
        e[turno] = hoje
        e.pop(chave, None)
    else:
        reg = e.get(chave) or {}
        n = (reg.get("n", 0) + 1) if reg.get("dia") == hoje else 1
        e[chave] = {"dia": hoje, "n": n, "motivo": str(resultado)[:120]}
        if n >= _MAX_TENTATIVAS:
            e[turno] = hoje
            dlc.log(f"⛔ {turno}: {n} tentativas falharam hoje ({resultado}) — paro até amanhã")
        else:
            dlc.log(f"🔁 {turno}: tentativa {n}/{_MAX_TENTATIVAS} falhou ({resultado}) — vou tentar de novo")
    e.setdefault("_hist", []).append({"turno": turno, "quando": agora().isoformat(timespec="minutes"),
                                      "r": str(resultado)[:80]})
    e["_hist"] = e["_hist"][-200:]
    try:
        with open(_ESTADO, "w", encoding="utf-8") as f:
            json.dump(e, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def ja_foi_hoje(turno):
    return _estado().get(turno) == agora().strftime("%Y-%m-%d")


def roda(turno, forcar=False):
    """Executa um turno pelo id (ex.: 'desp_manha', 'mob_0'). Usado pela agenda e pelo admin."""
    if not ligado():
        dlc.log(f"⏸️ motor desligado (DLMOTOR_ON=0) — {turno} pulado")
        return None
    if not forcar and ja_foi_hoje(turno):
        return None
    func = next((f for (tid, _h, _m, _d, f) in agenda() if tid == turno), None)
    if not func:
        dlc.log(f"❓ turno desconhecido: {turno}")
        return None
    from dlm import vacina
    try:
        r = func()
        dlc.log(f"🤖 {turno}: {r}")
    except vacina.Bloqueado as e:
        # texto fixo violou a vacina: é DESFECHO (retentar daria o mesmo texto 4x)
        r = "💉 vacina bloqueou: %s" % e
        dlc.log(f"💉 {turno}: {r}")
    except Exception as e:
        dlc.log(f"❌ {turno} quebrou: {e}")
        r = "quebrou: %s" % e
    _feito(turno, definitivo=not _recuperavel(r), resultado=r)
    return r


# ─────────────────────────────────────────────────────────── laço
def _proximo(now):
    melhor = None
    for tid, h, m, dias, _f in agenda():
        for adiante in range(8):
            d = (now + timedelta(days=adiante)).replace(hour=h, minute=m, second=0, microsecond=0)
            if d <= now or (dias is not None and d.weekday() not in dias):
                continue
            if melhor is None or d < melhor[0]:
                melhor = (d, tid)
            break
    return melhor


def _repesca(janela_h=4):
    """Turno do dia que falhou por motivo recuperavel volta a ser tentado enquanto o horario
    dele ainda faz sentido. Sem isto, o laco so olha horario FUTURO e o post do dia era perdido
    de vez por uma falha de 5 minutos."""
    now = agora()
    for tid, h, m, dias, _f in agenda():
        if ja_foi_hoje(tid):
            continue
        d = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if dias is not None and d.weekday() not in dias:
            continue
        atraso = (now - d).total_seconds()
        if 0 < atraso <= janela_h * 3600:
            dlc.log(f"🔁 repescando {tid} ({atraso / 60:.0f} min de atraso)")
            roda(tid)


def _laco():
    dlc.log(f"🤖 DL Motor iniciado — modo {'AO VIVO' if ao_vivo() else 'preview (não publica)'}; "
            f"{len(agenda())} turnos/dia")
    # 🩹 turno perdido por restart nos últimos 30 min ainda roda (misfire grace, como na Rádio)
    now = agora()
    for tid, h, m, dias, _f in agenda():
        d = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if (dias is None or d.weekday() in dias) and 0 <= (now - d).total_seconds() <= 1800:
            roda(tid)
    while True:
        _repesca()
        prox = _proximo(agora())
        if not prox:
            time.sleep(3600)
            continue
        quando, tid = prox
        espera = (quando - agora()).total_seconds()
        if espera > 0:
            time.sleep(min(espera, 900))          # acorda a cada 15 min
            continue
        roda(tid)
        time.sleep(61)


def iniciar():
    if not ligado():
        return None
    try:   # 💉 auditoria do banco inteiro no arranque — texto errado aparece no log antes de ir ao ar
        from dlm import vacina
        achados = vacina.auditar()
        if achados:
            for onde, motivo, _t in achados[:5]:
                dlc.log(f"💉 vacina: {onde} vai ser barrado — {motivo}")
        else:
            dlc.log(f"💉 vacina: banco limpo ({len(vacina.REGRAS)} regras)")
    except Exception as e:
        dlc.log(f"💉 vacina: auditoria falhou ({e})")
    t = threading.Thread(target=_laco, daemon=True, name="dlmotor")
    t.start()
    return t
