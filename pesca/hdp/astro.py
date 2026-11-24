"""Sol, lua e solunar calculados com Skyfield (licença MIT) + efeméride JPL DE421 (data/de421.bsp).

Solunar (convenção de John Alden Knight, "Moon Up, Moon Down", 1942):
- período MAIOR: 2 h centradas na lua no meio do céu (trânsito superior) e no "pé" (trânsito inferior);
- período MENOR: 1 h centrada no nascer e no pôr da lua.
É teoria tradicional de pescador, sem prova científica forte: pesa pouco na nota e o site diz isso.
"""
from datetime import timedelta, timezone

from skyfield import almanac
from skyfield.api import Loader, wgs84

from .config import EPHEM, TZ

EPHEM.mkdir(parents=True, exist_ok=True)
_load = Loader(str(EPHEM), verbose=False)
ts = _load.timescale(builtin=True)
eph = _load("de421.bsp")
EARTH, SUN, MOON = eph["earth"], eph["sun"], eph["moon"]

MAJOR_HALF = timedelta(minutes=60)
MINOR_HALF = timedelta(minutes=30)


def _loc(t):
    return t.utc_datetime().astimezone(TZ)


def events(lat, lon, start, end):
    """Eventos entre `start` e `end` (datetimes com fuso). Devolve listas de datetimes locais."""
    topos = wgs84.latlon(lat, lon)
    obs = EARTH + topos
    t0, t1 = ts.from_datetime(start), ts.from_datetime(end)
    out = {}
    for name, body, fn in (("sunrise", SUN, almanac.find_risings), ("sunset", SUN, almanac.find_settings),
                           ("moonrise", MOON, almanac.find_risings), ("moonset", MOON, almanac.find_settings)):
        t, ok = fn(obs, body, t0, t1)
        out[name] = [_loc(x) for x, y in zip(t, ok) if y]
    t, y = almanac.find_discrete(t0, t1, almanac.meridian_transits(eph, MOON, topos))
    out["moon_over"] = [_loc(x) for x, k in zip(t, y) if k == 1]
    out["moon_under"] = [_loc(x) for x, k in zip(t, y) if k == 0]
    t, y = almanac.find_discrete(t0, t1, almanac.moon_phases(eph))
    out["phases"] = [(_loc(x), almanac.MOON_PHASES[k]) for x, k in zip(t, y)]
    return out


def solunar_windows(ev):
    """Janelas solunares [(início, fim, 'major'|'minor', evento)]."""
    def around(t, half):   # conta em UTC: no relógio local a noite da troca de horário criava "2:26 AM"
        u = t.astimezone(timezone.utc)
        return (u - half).astimezone(TZ), (u + half).astimezone(TZ)

    w = []
    for key in ("moon_over", "moon_under"):
        for t in ev[key]:
            w.append((*around(t, MAJOR_HALF), "major", key))
    for key in ("moonrise", "moonset"):
        for t in ev[key]:
            w.append((*around(t, MINOR_HALF), "minor", key))
    return sorted(w, key=lambda x: x[0].timestamp())


def moon_phase(dt):
    """(ângulo de fase em graus 0=nova 180=cheia, fração iluminada 0..1)."""
    t = ts.from_datetime(dt)
    ang = almanac.moon_phase(eph, t).degrees
    frac = almanac.fraction_illuminated(eph, "moon", t)
    return float(ang), float(frac)


def phase_name(angle):
    """Nome da fase. As 4 principais só a ±7° do instante exato (antes saía 'Last Quarter' com 56% iluminada)."""
    a = angle % 360
    for center, n in ((0, "New Moon"), (90, "First Quarter"), (180, "Full Moon"), (270, "Last Quarter"), (360, "New Moon")):
        if abs(a - center) <= 7:
            return n
    return "Waxing Crescent" if a < 90 else "Waxing Gibbous" if a < 180 else "Waning Gibbous" if a < 270 else "Waning Crescent"
