"""Maré prevista da NOAA CO-OPS (sem chave). Hora em GMT (o horário de verão não embaralha a série), pés, MLLW."""
from .config import NOAA_APP
from .net import get

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"


def _pred(station, begin, hours, interval):
    last = None
    for datum in ("MLLW", "MSL"):
        try:
            d = get(API, params={"product": "predictions", "application": NOAA_APP, "begin_date": begin,
                                 "range": hours, "datum": datum, "station": station, "time_zone": "gmt",
                                 "units": "english", "interval": interval, "format": "json"})
        except RuntimeError as e:
            last = str(e)
            continue
        if "predictions" in d:
            return datum, d["predictions"]
        last = str(d.get("error"))
    raise RuntimeError(f"NOAA {station}: {last}")


def tides(station, begin_yyyymmdd, hours=192):
    datum, six = _pred(station, begin_yyyymmdd, hours, "6")
    _, hilo = _pred(station, begin_yyyymmdd, hours, "hilo")
    return {"station": station, "datum": datum, "tz": "gmt",
            "series": [{"t": p["t"], "ft": float(p["v"])} for p in six],
            "hilo": [{"t": p["t"], "ft": float(p["v"]), "type": p["type"]} for p in hilo]}
