"""Nota de 0 a 10 por faixa de 10 minutos, por espécie, por lugar.

nota = 3 + 7 x (1 - e^(-positivos/4)) + negativos, cortada em 0..10.
  positivos: luz da espécie, solunar, maré, pressão caindo / antes da frente
  negativos: vento, chuva, trovoada, alerta do NWS, depois da frente, luz errada para a espécie

Por que assim (revisão de 23/11): com base 5 quase todo dia dava 8. Agora um entardecer comum dá ~6,
entardecer + solunar maior ~7, tudo alinhado ~8,5, e o tempo ruim derruba para 3 a 5.

Regra de ouro: fator de ESPÉCIE (luz, maré, lua) só entra se a ficha tiver o dado com fonte, em campo
estruturado. Nunca procurar palavra em texto livre. Sem fonte = fator neutro (0) e a página diz isso.
Solunar, frente, vento, chuva e alerta são regras do produto, explicadas na página "How we score".
"""
import math
from bisect import bisect_left
from datetime import datetime, timedelta, timezone

from .config import SLOT_MIN, TZ

BASE, SPAN, K = 3.0, 7.0, 4.0
W = {  # pesos
    "light_peak": 2.5, "light_near": 1.25, "light_off_crep": -0.5,
    "noct_night": 2.0, "noct_twilight": 1.5, "noct_day": -1.0,
    "diur_day": 1.0, "diur_twilight": 0.5, "diur_night": -1.0,
    "sol_major": 1.5, "sol_minor": 0.75, "sol_phase": 0.25,
    "tide_strong": 1.5, "tide_some": 0.5, "tide_slack": -1.0, "tide_incoming": 0.5,
    "press_fall": 1.0, "press_rise": -0.5, "prefront": 1.0, "postfront": -1.0,
    "temp_in": 1.0, "temp_far": -1.0,
    "thunder": -3.0, "thunder_high": -4.0,
    "wind_10": -0.75, "wind_15": -1.5, "wind_20": -2.5, "wind_25": -3.5,
    "rain_30": -0.5, "rain_50": -1.0, "rain_70": -1.5,
    "alert": -4.0,
}
PRESS_FALL, PRESS_RISE = -2.0, 2.0   # hPa em 3 h. -1 disparava todo dia com a maré diária do ar (revisão M1)
TIDE_FLOOR = 0.15                     # pés por hora: abaixo disso a maré não conta como "mexendo" (revisão A4)
TIDE_REF_MIN = 0.30                   # normaliza pelo maior entre o máximo da semana e 0,30 pé/h
# Alertas do NWS que mexem com quem está na água ou na margem. Os outros aparecem no aviso, sem tirar ponto.
ALERT_WORDS = ("marine", "small craft", "gale", "storm", "thunderstorm", "tornado", "hurricane", "tropical",
               "wind", "flood", "fog", "surf", "rip current", "hazardous seas", "lake wind", "brisk")
KMH_TO_MPH = 0.621371
UTC = timezone.utc


def c_to_f(c):
    return c * 9 / 5 + 32


def _nearest(sorted_times, t):
    """Distância em minutos até o evento mais próximo."""
    if not sorted_times:
        return None
    i = bisect_left(sorted_times, t)
    cands = [sorted_times[j] for j in (i - 1, i) if 0 <= j < len(sorted_times)]
    return min(abs((c - t).total_seconds()) / 60 for c in cands)


def _sun_up(t, rises, sets):
    last_rise = max((r for r in rises if r <= t), default=None)
    last_set = max((s for s in sets if s <= t), default=None)
    if last_rise is None:
        return False if last_set is not None else None
    return last_set is None or last_rise > last_set


# ---------------------------------------------------------------- séries de tempo
class Weather:
    """Série horária unificada: observação (passado) + grade do NWS (futuro)."""

    def __init__(self, grid, obs, collected_at, alerts=None):
        self.hours = {}  # hora UTC (datetime) -> dict
        for k, v in (grid or {}).get("hours", {}).items():
            self.hours[datetime.fromisoformat(k)] = v
        self.temp_f = {}
        for h, v in self.hours.items():
            if v.get("temp_c") is not None:
                self.temp_f[h] = c_to_f(v["temp_c"])
        for r in (obs or {}).get("rows", []):
            if r.get("temp_c") is None:
                continue
            h = datetime.fromisoformat(r["t"]).astimezone(UTC).replace(minute=0, second=0, microsecond=0)
            self.temp_f.setdefault(h, c_to_f(r["temp_c"]))
        self.collected_at = collected_at
        self.press_trend = self._pressure_trend(obs)
        if self.press_trend:  # leitura velha (estação parada) não vale para "agora"
            age = (collected_at - datetime.fromisoformat(self.press_trend["at"])).total_seconds() / 3600
            if age > 3:
                self.press_trend = None
        self.fronts = self._fronts()
        self.alerts = []
        for a in alerts or []:
            try:
                s = datetime.fromisoformat(a["start"]) if a.get("start") else collected_at
                e = datetime.fromisoformat(a["end"]) if a.get("end") else collected_at + timedelta(hours=12)
            except (TypeError, ValueError):
                continue
            water = any(w in (a.get("event") or "").lower() for w in ALERT_WORDS)
            self.alerts.append({"event": a.get("event"), "start": s, "end": e, "water": water,
                                "headline": a.get("headline")})

    def at(self, t):
        h = t.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return self.hours.get(h)

    def temp_mean_72h(self, t):
        h = t.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        vals = [self.temp_f[h - timedelta(hours=i)] for i in range(72) if (h - timedelta(hours=i)) in self.temp_f]
        return sum(vals) / len(vals) if len(vals) >= 24 else None

    @staticmethod
    def _pressure_trend(obs):
        rows = [r for r in (obs or {}).get("rows", []) if r.get("pressure_hpa")]
        if len(rows) < 2:
            return None
        last = rows[-1]
        t_last = datetime.fromisoformat(last["t"])
        ref = min(rows, key=lambda r: abs((t_last - datetime.fromisoformat(r["t"])).total_seconds() - 3 * 3600))
        dt_h = (t_last - datetime.fromisoformat(ref["t"])).total_seconds() / 3600
        if dt_h < 2:
            return None
        return {"delta_3h": round((last["pressure_hpa"] - ref["pressure_hpa"]) * 3 / dt_h, 2),
                "hpa": last["pressure_hpa"], "at": last["t"]}

    def _fronts(self):
        """Frente fria pela previsão: vento vira de sul para norte (>= 10 mph) e a máxima das 24 h seguintes
        cai >= 8 °F em relação às 24 h anteriores. A NWS não publica pressão prevista, então é por aqui."""
        hs = sorted(h for h, v in self.hours.items() if "wind_dir" in v and "wind_kmh" in v)
        out = []
        for h in hs:
            prev = self.hours.get(h - timedelta(hours=3))
            cur = self.hours[h]
            if not prev or "wind_dir" not in prev:
                continue
            north = (cur["wind_dir"] >= 300 or cur["wind_dir"] <= 60) and cur["wind_kmh"] * KMH_TO_MPH >= 10
            was_south = 90 <= prev["wind_dir"] <= 270
            if not (north and was_south):
                continue
            before = [self.temp_f[h - timedelta(hours=i)] for i in range(1, 25) if (h - timedelta(hours=i)) in self.temp_f]
            after = [self.temp_f[h + timedelta(hours=i)] for i in range(0, 24) if (h + timedelta(hours=i)) in self.temp_f]
            if len(before) >= 12 and len(after) >= 12 and max(after) <= max(before) - 8:
                if not out or (h - out[-1]).total_seconds() > 36 * 3600:
                    out.append(h)
        return out


class Tide:
    def __init__(self, data):
        self.ok = bool(data and data.get("series"))
        if not self.ok:
            return
        tz = UTC if data.get("tz") == "gmt" else TZ   # coleta nova em GMT; a antiga era hora local
        parse = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        self.t = [parse(p["t"]) for p in data["series"]]
        self.v = [p["ft"] for p in data["series"]]
        self.hilo = [(parse(p["t"]).astimezone(TZ), p["ft"], p["type"]) for p in data["hilo"]]
        rates = [abs(self.v[i + 5] - self.v[i - 5]) for i in range(5, len(self.v) - 5)]  # pés por hora
        self.max_rate = max(rates) if rates else 0.0
        self.tiny = self.max_rate < TIDE_FLOOR   # maré mínima a semana toda: o vento manda mais que ela

    def rate(self, t):
        """(pés por hora, taxa relativa 0..1, subindo?)."""
        if not self.ok:
            return None
        i = bisect_left(self.t, t)
        if i < 5 or i + 5 >= len(self.v):
            return None
        r = abs(self.v[i + 5] - self.v[i - 5])
        return r, r / max(self.max_rate, TIDE_REF_MIN), self.v[i + 5] > self.v[i - 5]


# ---------------------------------------------------------------- fatores
def light_rule(sp, d):
    """Padrão de luz com fonte que vale nesta data (o seatrout só no calor), ou None."""
    act = sp.get("activity") or {}
    pat = act.get("pattern")
    if pat not in ("crepuscular", "nocturnal", "diurnal"):
        return None   # "mixed" descreve modo de comer, não hora: neutro
    if act.get("months") and d.month not in act["months"]:
        return None
    return pat


def f_light(sp, t, ev):
    act = light_rule(sp, t.date())
    if not act:
        return 0.0, None
    ds, dn = _nearest(ev["sunrise"], t), _nearest(ev["sunset"], t)
    d = min(x for x in (ds, dn) if x is not None)
    when = "dawn" if ds is not None and (dn is None or ds <= dn) else "dusk"
    up = _sun_up(t, ev["sunrise"], ev["sunset"])
    if act == "crepuscular":
        if d <= 60:
            return W["light_peak"], f"{when} feeding time"
        if d <= 120:
            return W["light_near"], f"close to {when}"
        return W["light_off_crep"], ("bright midday" if up else "dark (feeds at dawn and dusk)")
    if act == "nocturnal":
        if d <= 60:
            return W["noct_twilight"], "low light"
        return (W["noct_day"], "daylight (night feeder)") if up else (W["noct_night"], "night feeding")
    if d <= 60:
        return W["diur_twilight"], "low light"
    return (W["diur_day"], "daylight feeder") if up else (W["diur_night"], "night")


def f_solunar(sp, t, windows, phase_angle):
    best, why = 0.0, None
    slot_end = t + timedelta(minutes=SLOT_MIN)
    names = {"moon_over": "moon overhead", "moon_under": "moon underfoot", "moonrise": "moonrise", "moonset": "moonset"}
    for a, b, kind, ev in windows:
        if a < slot_end and b > t:
            pts = W["sol_major"] if kind == "major" else W["sol_minor"]
            if pts > best:
                best, why = pts, names.get(ev, ev)
    full = abs(phase_angle - 180) <= 15
    new = phase_angle <= 15 or phase_angle >= 345
    if best and (full or new) and sp.get("moon_phase_bonus", True):   # linguado: TPWD diz noite escura (sem bônus)
        best += W["sol_phase"]
        why += ", full moon" if full else ", new moon"
    return best, why


def f_tide(sp, t, tide):
    if tide is None or not tide.ok or tide.tiny:
        return 0.0, None
    rule = sp.get("tide")
    if not rule:
        return 0.0, None
    rr = tide.rate(t)
    if rr is None:
        return 0.0, None
    r, rel, rising = rr
    pts, why = 0.0, None
    if r >= TIDE_FLOOR and rel >= 0.6:
        pts, why = W["tide_strong"], "strong tidal flow"
    elif r >= TIDE_FLOOR and rel >= 0.3:
        pts, why = W["tide_some"], "tide moving"
    elif rel < 0.15:
        pts, why = W["tide_slack"], "slack tide"
    if rule.get("incoming_bonus") and rising and r >= TIDE_FLOOR and rel >= 0.3:   # campo estruturado (só linguado)
        pts += W["tide_incoming"]
        why = (why or "") + ", incoming"
    return pts, why


def f_pressure(t, wx):
    pts, why = 0.0, None
    tr = wx.press_trend
    if tr and t <= wx.collected_at + timedelta(hours=6):
        if tr["delta_3h"] <= PRESS_FALL:
            pts, why = W["press_fall"], f"pressure falling ({tr['delta_3h']:+.1f} hPa in 3 h)"
        elif tr["delta_3h"] >= PRESS_RISE:
            pts, why = W["press_rise"], f"pressure rising fast ({tr['delta_3h']:+.1f} hPa in 3 h)"
    tu = t.astimezone(UTC)
    for fr in wx.fronts:
        if fr - timedelta(hours=12) <= tu < fr:
            return pts + W["prefront"], "ahead of a cold front"
        if fr <= tu < fr + timedelta(hours=36):
            h = wx.at(t) or {}
            if h.get("sky_pct", 100) <= 30:
                return pts + W["postfront"], "clear skies behind a cold front"
    return pts, why


TEMP_ON = False  # v1: neutro. As faixas das fichas são de água (crescimento ótimo), e a v1 não mede a água:
# comparar o ar de novembro com 75-86 °F tirava ponto do bass justo no outono que o TPWD chama de bom.
# Volta quando houver temperatura da água (ex.: sensores do USGS) e faixa de captura, não de crescimento.


def f_temp(sp, t, wx):
    if not TEMP_ON:
        return 0.0, None
    tp = sp.get("temp_preferred_f") or {}
    lo, hi = tp.get("min"), tp.get("max")
    if lo is None and hi is None:
        return 0.0, None
    m = wx.temp_mean_72h(t)
    if m is None:
        return 0.0, None
    lo_ = lo if lo is not None else -999
    hi_ = hi if hi is not None else 999
    if lo_ <= m <= hi_:
        return W["temp_in"], f"3-day air avg {m:.0f}°F inside its range"
    if m < lo_ - 10 or m > hi_ + 10:
        return W["temp_far"], f"3-day air avg {m:.0f}°F far from its range"
    return 0.0, None


def f_safety(t, wx):
    pts, why = 0.0, []
    for a in wx.alerts:
        if a["water"] and a["start"] <= t < a["end"]:
            pts += W["alert"]
            why.append(f"NWS {a['event']}")
            break
    h = wx.at(t)
    if not h:
        return pts, (", ".join(why) or None)
    th = h.get("thunder_pct", 0)
    if th >= 60:
        pts += W["thunder_high"]
        why.append(f"thunder {th:.0f}%")
    elif th >= 30:
        pts += W["thunder"]
        why.append(f"thunder {th:.0f}%")
    wind = h.get("wind_kmh", 0) * KMH_TO_MPH
    gust = h.get("gust_kmh", 0) * KMH_TO_MPH
    if wind >= 25 or gust >= 35:
        pts += W["wind_25"]
    elif wind >= 20:
        pts += W["wind_20"]
    elif wind >= 15:
        pts += W["wind_15"]
    elif wind >= 10:
        pts += W["wind_10"]
    if wind >= 10 or gust >= 35:
        why.append(f"wind {wind:.0f} mph" + (f", gusts {gust:.0f}" if gust >= wind + 8 else ""))
    pop = h.get("pop_pct", 0)
    if pop >= 30:
        pts += W["rain_70"] if pop >= 70 else W["rain_50"] if pop >= 50 else W["rain_30"]
        why.append(f"rain {pop:.0f}%")
    return pts, (", ".join(why) or None)


def slot_score(sp, t, ctx):
    parts = {
        "light": f_light(sp, t, ctx["ev"]),
        "solunar": f_solunar(sp, t, ctx["windows"], ctx["phase_at"](t)),
        "tide": f_tide(sp, t, ctx["tide"]),
        "pressure": f_pressure(t, ctx["wx"]),
        "temp": f_temp(sp, t, ctx["wx"]),
        "safety": f_safety(t, ctx["wx"]),
    }
    pos = sum(p for p, _ in parts.values() if p > 0)
    neg = sum(p for p, _ in parts.values() if p < 0)
    total = BASE + SPAN * (1 - math.exp(-pos / K)) + neg
    return max(0.0, min(10.0, total)), parts


def best_window(scores, lo, hi, ok=None, n_slots=6):
    """Melhor janela de 60 min (6 faixas) que começa em [lo, hi): (índice, média) ou (None, None).
    `ok[i]` falso (ex.: sem previsão do NWS) tira a faixa do jogo."""
    best_i, best_m = None, None
    for i in range(lo, min(hi, len(scores) - n_slots + 1)):
        if ok is not None and not all(ok[i:i + n_slots]):
            continue
        m = sum(scores[i:i + n_slots]) / n_slots
        if best_m is None or m > best_m + 1e-9:
            best_i, best_m = i, m
    return best_i, best_m
