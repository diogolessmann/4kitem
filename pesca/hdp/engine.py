"""Junta tudo de um lugar: eventos do sol e da lua, tempo, maré, espécies, nota por faixa, melhor janela e isca.

Regras desta versão (revisão Opus de 23/11):
- faixas de 10 min geradas em UTC e mostradas no horário central: o horário de verão não cria nem some hora;
- espécie FECHADA na data (linguado de 1/nov a 14/dez, TPWD) nunca vira manchete nem "melhor hora";
- manchete = melhor janela das próximas 24 h, só entre espécies com regra de luz ou de maré com fonte;
- dia sem previsão do NWS não ganha nota;
- lago sem dado do TPWD não ganha lista de peixe (antes virava lista de água salgada).
"""
import json
from bisect import bisect_left
from datetime import date, datetime, time, timedelta, timezone

from . import astro, baits, tpwd
from .config import CACHE, DAYS, FICHAS, SLOT_MIN, TZ
from .score import KMH_TO_MPH, Tide, Weather, best_window, c_to_f, light_rule, slot_score

UTC = timezone.utc
RATING = {"Poor": 1, "Fair": 2, "Good": 3, "Excellent": 4, None: 0}
# Peixe-assinatura do lugar = maior nota do TPWD no lago; empate -> o mais procurado no Texas.
# Fonte das 2 primeiras posições de cada lado: TPWD (largemouth = "most popular freshwater sport fish in Texas",
# bagre em 2º; spotted seatrout 1º e red drum 2º no mar, folheto do red drum). O resto da ordem é julgamento nosso.
POPULAR = ["largemouth_bass", "channel_catfish", "blue_catfish", "flathead_catfish", "crappie", "white_bass",
           "striped_bass", "hybrid_striped_bass", "bluegill",
           "spotted_seatrout", "red_drum", "southern_flounder", "black_drum", "sheepshead", "atlantic_croaker"]
SWITCH = 1.5        # a manchete só troca de peixe se outro estiver 1,5 ponto acima
WIN = 6             # janela de 60 min = 6 faixas
DAY_SLOTS = 24 * 60 // SLOT_MIN
HERO_HOURS = 48     # manchetes pré-calculadas, uma por hora, que o navegador escolhe pela hora do leitor


def load_species():
    sp = {}
    for fn in ("fresh.json", "salt.json"):
        for s in json.loads((FICHAS / fn).read_text(encoding="utf-8")):
            sp[s["id"]] = s
    return sp


def load_run(run_id=None):
    run_id = run_id or (CACHE / "latest.txt").read_text(encoding="utf-8").strip()
    rd = CACHE / "runs" / run_id
    return run_id, {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in rd.glob("*.json")}


def closed_on(sp, d):
    """Fechamento com fonte que vale na data, ou None. Campo `closed` da ficha: [{"from": "MM-DD", "to": "MM-DD"}]."""
    for c in sp.get("closed") or []:
        a = date(d.year, *map(int, c["from"].split("-")))
        b = date(d.year, *map(int, c["to"].split("-")))
        if (a <= d <= b) if a <= b else (d >= a or d <= b):
            return c
    return None


def has_signal(sp, d, tide):
    """A espécie tem regra com fonte que muda a hora nesta data? (luz, ou maré que mexe de verdade)"""
    if light_rule(sp, d):
        return True
    return bool(sp.get("tide") and tide is not None and tide.ok and not tide.tiny)


def _reasons(parts, n=2):
    pos = sorted(((p, w) for p, w in parts.values() if w and p > 0), reverse=True)
    neg = sorted(((p, w) for p, w in parts.values() if w and p < 0))
    return [w for _, w in pos[:n]], [w for _, w in neg[:2]]


def _midnight(d):
    return datetime.combine(d, time(0), tzinfo=TZ)


def _plus(t, minutes):
    """Soma em UTC (soma no relógio local erra no dia do horário de verão)."""
    return (t.astimezone(UTC) + timedelta(minutes=minutes)).astimezone(TZ)


def build(place, raw, species_db, lakes, now=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    now_u = now.astimezone(UTC)
    start_u = now_u.replace(minute=(now_u.minute // SLOT_MIN) * SLOT_MIN, second=0, microsecond=0)
    dates = [now.date() + timedelta(days=d) for d in range(DAYS)]
    end_u = _midnight(dates[-1] + timedelta(days=1)).astimezone(UTC)
    lat, lon = place["lat"], place["lon"]

    slots, t = [], start_u
    while t < end_u + timedelta(minutes=SLOT_MIN * WIN):   # sobra para a última janela do último dia
        slots.append(t.astimezone(TZ))
        t += timedelta(minutes=SLOT_MIN)

    ev = astro.events(lat, lon, _midnight(dates[0] - timedelta(days=1)), _midnight(dates[-1] + timedelta(days=2)))
    windows = astro.solunar_windows(ev)
    phase_by_day = {d: astro.moon_phase(datetime.combine(d, time(12), tzinfo=TZ)) for d in dates}
    phase_by_day[dates[-1] + timedelta(days=1)] = astro.moon_phase(
        datetime.combine(dates[-1] + timedelta(days=1), time(12), tzinfo=TZ))
    collected = datetime.fromisoformat(raw["collected_at"])
    wx = Weather(raw.get("grid"), raw.get("obs"), collected, raw.get("alerts"))
    tide = Tide(raw.get("tide")) if place["kind"] == "coast" else None
    ctx = {"ev": ev, "windows": windows, "wx": wx, "tide": tide,
           "phase_at": lambda t: phase_by_day.get(t.date(), (90, 0.5))[0]}
    has_wx = [wx.at(s) is not None for s in slots]

    # espécies do lugar
    lake = lakes.get(place.get("tpwd_slug")) if place["kind"] == "lake" else None
    if place["kind"] == "coast":
        present = {sid: None for sid, s in species_db.items() if s.get("water") == "salt"}
    elif lake:
        present = tpwd.species_for_lake(lake)
    else:
        present = {}   # lago sem dado do TPWD: sem lista e sem manchete (a página avisa)
    present = {sid: r for sid, r in present.items() if sid in species_db}
    slug = place.get("tpwd_slug")

    per = {}
    for sid, rating in present.items():
        sp = species_db[sid]
        scored = [slot_score(sp, s, ctx) for s in slots]
        elig = {}
        for d in {s.date() for s in slots}:
            elig[d] = (not closed_on(sp, d)) and has_signal(sp, d, tide)
        open_d = {d: not closed_on(sp, d) for d in elig}
        parts = [p for _, p in scored]
        sourced = [p["light"][0] > 0 or p["tide"][0] > 0 for p in parts]
        per[sid] = {"rating": rating, "scores": [x for x, _ in scored], "parts": parts, "elig": elig,
                    "ok_open": [has_wx[i] and open_d[s.date()] for i, s in enumerate(slots)],
                    "ok_strong": [has_wx[i] and elig[s.date()] and sourced[i] for i, s in enumerate(slots)]}

    def rank(sid):
        return (RATING[per[sid]["rating"]], -POPULAR.index(sid) if sid in POPULAR else -99)

    def option(sid, i, m):
        sp = species_db[sid]
        good, bad = _reasons(per[sid]["parts"][i + 2])
        low_light = any(k in w for w in good for k in ("dawn", "dusk", "low light"))
        st = slots[i]
        return {"species": sid, "i": i, "score": round(m, 2), "best_start": st, "best_end": _plus(st, 60),
                "date": st.date(), "why": good, "against": bad,
                "bait": baits.pick(sp, st.date(), (lake or {}).get("tips", {}).get(sid), low_light, slug)}

    def choose(lo, hi):
        """Melhor janela que começa em [lo, hi): peixe-assinatura, salvo outro SWITCH acima.
        Cada faixa da janela tem de estar: com previsão do NWS, com a espécie aberta NAQUELA data (a janela
        pode entrar no dia seguinte) e com motivo de fonte positivo (luz ou maré). Sem nenhuma assim,
        cai para 'weak' (dia fraco): espécie aberta e com previsão, e a página avisa."""
        if lo >= hi:
            return None
        weak = False
        opts = []
        for sid in per:
            i, m = best_window(per[sid]["scores"], lo, hi, per[sid]["ok_strong"], WIN)
            if i is not None:
                opts.append((m, sid, i))
        if not opts:
            weak = True
            for sid in per:
                i, m = best_window(per[sid]["scores"], lo, hi, per[sid]["ok_open"], WIN)
                if i is not None:
                    opts.append((m, sid, i))
        if not opts:
            return None
        pool = [o[1] for o in opts]
        opts.sort(key=lambda o: (o[0], rank(o[1])), reverse=True)
        sig_id = max(pool, key=rank)   # assinatura entre as espécies que têm janela válida
        sig = next((o for o in opts if o[1] == sig_id), None)
        top = sig if sig and opts[0][0] - sig[0] < SWITCH else opts[0]
        o = option(top[1], top[2], top[0])
        o["weak"] = weak
        o["others"] = [(x[1], round(x[0], 2)) for x in opts if x[1] != top[1]][:3]
        return o

    stamps = [s.timestamp() for s in slots]

    def first_at_or_after(tu):
        return bisect_left(stamps, tu.timestamp())

    # dias
    days = []
    for d in dates:
        lo = first_at_or_after(_midnight(d))
        hi = first_at_or_after(_midnight(d + timedelta(days=1)))
        o = choose(lo, hi) if any(has_wx[lo:hi]) else None
        a, b = _midnight(d), _midnight(d + timedelta(days=1))
        day = {"date": d, "score": o["score"] if o else None, "opt": o,
               "no_forecast": not any(has_wx[lo:hi]), "no_window": lo >= hi or (o is None and any(has_wx[lo:hi])),
               "sun": _day_list(ev, ("sunrise", "sunset"), a, b),
               "moon": _day_list(ev, ("moonrise", "moonset"), a, b),
               "solunar": [(x, y, k) for x, y, k, _ in windows if a <= x + (y - x) / 2 < b],
               "phase": phase_by_day.get(d),
               "weather": _day_weather(raw, wx, a),
               "tides": [(t_, ft, ty) for t_, ft, ty in (tide.hilo if tide and tide.ok else []) if a <= t_ < b]}
        days.append(day)

    # manchete: melhor janela das próximas 24 h (não "o resto de hoje")
    head = choose(0, min(DAY_SLOTS, len(slots)))
    # uma manchete por hora para as próximas 48 h: o navegador mostra a da hora do leitor
    heroes = []
    for k in range(HERO_HOURS):
        lo = first_at_or_after((slots[0].astimezone(UTC) + timedelta(hours=k)).replace(minute=0) if k else slots[0])
        if lo + DAY_SLOTS > len(slots):
            break
        o = choose(lo, lo + DAY_SLOTS)
        if o:
            heroes.append((slots[lo], o))

    # linha do tempo (média por hora) do dia e da espécie da manchete
    timeline = []
    if head:
        v = per[head["species"]]
        by_hour = {}
        for i, s in enumerate(slots):
            if s.date() == head["date"] and has_wx[i]:
                by_hour.setdefault(s.replace(minute=0), []).append(v["scores"][i])
        timeline = [(h, sum(x) / len(x)) for h, x in sorted(by_hour.items())]

    hd = head["date"] if head else dates[0]
    lo = first_at_or_after(_midnight(hd))
    hi = first_at_or_after(_midnight(hd + timedelta(days=1)))
    species_list = []
    for sid, v in sorted(per.items(), key=lambda kv: (-RATING[kv[1]["rating"]],
                                                      POPULAR.index(kv[0]) if kv[0] in POPULAR else 99)):
        sp = species_db[sid]
        tip = (lake or {}).get("tips", {}).get(sid)
        closed_today = closed_on(sp, now.date())
        closed = closed_today or closed_on(sp, hd)
        signal = has_signal(sp, hd, tide)
        best = None
        if not closed and signal:
            i, m = best_window(v["scores"], lo, hi, v["ok_open"], WIN)
            if i is not None:
                best = (slots[i], m)
        species_list.append({"id": sid, "sp": sp, "rating": v["rating"], "best": best, "closed": closed,
                             "closed_today": closed_today, "closes_soon": (not closed_today) and closed_on(sp, hd),
                             "signal": signal, "light": light_rule(sp, hd),
                             "bait": baits.pick(sp, hd, tip, False, slug), "tip_lures": (tip or {}).get("lures", [])})

    lake_regs = tpwd.regs_for((lake or {}).get("lake_regs"), list(present)) if lake else []
    alerts = [dict(a, start=a["start"].astimezone(TZ), end=a["end"].astimezone(TZ)) for a in wx.alerts if a["end"] > now]
    valid_until = (slots[0].astimezone(UTC) + timedelta(hours=HERO_HOURS)).astimezone(TZ) if slots else now
    return {"place": place, "now": now, "start": slots[0] if slots else now, "days": days, "head": head,
            "valid_until": valid_until,
            "heroes": heroes, "timeline": timeline, "species": species_list, "lake": lake,
            "no_lake_data": place["kind"] == "lake" and not lake, "lake_regs": lake_regs, "alerts": alerts,
            "tide_tiny": bool(tide and tide.ok and tide.tiny), "wx": wx, "raw_errors": raw.get("errors", []),
            "obs_station": (raw.get("obs") or {}).get("station"), "press": wx.press_trend,
            "fronts": [f.astimezone(TZ) for f in wx.fronts], "tide_station": place.get("noaa_station"),
            "tide_datum": (raw.get("tide") or {}).get("datum"),
            "nws_update": (raw.get("grid") or {}).get("updateTime"), "collected_at": collected.astimezone(TZ)}


def _day_list(ev, keys, a, b):
    out = []
    for k in keys:
        for t in ev[k]:
            if a <= t < b:
                out.append((t, k))
    return sorted(out)


def _day_weather(raw, wx, a):
    temps, winds, pops, thunder = [], [], [], []
    au = a.astimezone(UTC)
    for h in range(24):
        hv = wx.at(au + timedelta(hours=h))
        if not hv:
            continue
        if hv.get("temp_c") is not None:
            temps.append(c_to_f(hv["temp_c"]))
        winds.append(hv.get("wind_kmh", 0) * KMH_TO_MPH)
        pops.append(hv.get("pop_pct", 0))
        thunder.append(hv.get("thunder_pct", 0))
    short = None
    for p in (raw.get("hourly") or {}).get("periods", []):
        t = datetime.fromisoformat(p["start"]).astimezone(TZ)
        if t.date() == a.date() and t.hour == 12:
            short = p["short"]
    if not temps:
        return None
    return {"hi": round(max(temps)), "lo": round(min(temps)), "wind_max": round(max(winds)),
            "pop_max": round(max(pops)), "thunder_max": round(max(thunder)), "short": short}
