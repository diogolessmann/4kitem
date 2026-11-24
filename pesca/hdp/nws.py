"""Tempo do National Weather Service (api.weather.gov): dado aberto, sem chave, exige User-Agent.

- /points/{lat},{lon}          -> grade da previsão + estações de observação
- /gridpoints/{wfo}/{x},{y}    -> camadas numéricas por hora (temperatura, vento, rajada, céu, chuva, trovoada)
- .../forecast/hourly          -> texto curto por hora ("Mostly Cloudy"), o mesmo que forecast.weather.gov mostra
- /stations/{id}/observations  -> pressão observada (a previsão do NWS não traz pressão: camada vazia, conferido 23/11)
- /alerts/active?point=lat,lon -> alertas ativos no ponto (no litoral inclui os de zona marinha, ex.: Small Craft Advisory)
"""
import re
from datetime import datetime, timedelta, timezone

from .net import get

API = "https://api.weather.gov"
GEO = {"Accept": "application/geo+json"}
LAYERS = {
    "temperature": "temp_c",
    "windSpeed": "wind_kmh",
    "windGust": "gust_kmh",
    "windDirection": "wind_dir",
    "skyCover": "sky_pct",
    "probabilityOfPrecipitation": "pop_pct",
    "probabilityOfThunder": "thunder_pct",
}
_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?")


def point(lat, lon):
    p = get(f"{API}/points/{lat:.4f},{lon:.4f}", headers=GEO)["properties"]
    return {k: p.get(k) for k in ("gridId", "gridX", "gridY", "forecastHourly", "forecastGridData",
                                  "observationStations", "timeZone")}


def _expand(valid_time):
    """'2026-11-23T02:00:00+00:00/PT3H' -> lista de horas UTC cheias cobertas."""
    start_s, dur_s = valid_time.split("/")
    start = datetime.fromisoformat(start_s).astimezone(timezone.utc)
    m = _DUR.fullmatch(dur_s)
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    n = max(1, d * 24 + h + (1 if mi else 0))
    return [start + timedelta(hours=i) for i in range(n)]


def grid_hourly(grid_url):
    """Camadas numéricas da grade -> {hora_utc_iso: {campo: valor}}."""
    g = get(grid_url, headers=GEO)["properties"]
    hours = {}
    for layer, key in LAYERS.items():
        for v in (g.get(layer) or {}).get("values", []):
            if v.get("value") is None:
                continue
            for t in _expand(v["validTime"]):
                hours.setdefault(t.isoformat(), {})[key] = v["value"]
    return {"updateTime": g.get("updateTime"), "hours": hours}


def forecast_hourly(url):
    props = get(url, headers=GEO)["properties"]
    out = []
    for p in props.get("periods", []):
        out.append({"start": p["startTime"], "temp_f": p.get("temperature"), "wind": p.get("windSpeed"),
                    "wind_dir": p.get("windDirection"), "short": p.get("shortForecast"),
                    "pop": (p.get("probabilityOfPrecipitation") or {}).get("value")})
    return {"updateTime": props.get("updateTime") or props.get("generatedAt"), "periods": out}


FRESH_H = 3  # leitura de pressão mais velha que isso não serve (ex.: KGLS parada em 22/11, achado na conferência)


def observations(stations_url, hours=72):
    """Observações das últimas `hours` horas da estação mais próxima com pressão RECENTE (< FRESH_H)."""
    st = get(stations_url, headers=GEO).get("features", [])
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = None
    for f in st[:4]:
        sid = f["properties"]["stationIdentifier"]
        try:
            obs = get(f"{API}/stations/{sid}/observations", params={"start": start}, headers=GEO).get("features", [])
        except RuntimeError:
            continue
        # um campo só de pressão por estação: misturar barometricPressure e seaLevelPressure cria salto falso
        props = [o["properties"] for o in obs]
        nb = sum(1 for p in props if (p.get("barometricPressure") or {}).get("value"))
        ns = sum(1 for p in props if (p.get("seaLevelPressure") or {}).get("value"))
        field = "barometricPressure" if nb >= ns else "seaLevelPressure"
        rows = []
        for p in props:
            pa = (p.get(field) or {}).get("value")
            rows.append({"t": p["timestamp"], "temp_c": (p.get("temperature") or {}).get("value"),
                         "pressure_hpa": round(pa / 100, 2) if pa else None})
        rows.sort(key=lambda r: r["t"])
        withp = [r for r in rows if r["pressure_hpa"]]
        if not withp:
            continue
        rec = {"station": sid, "name": f["properties"].get("name"), "pressure_field": field, "rows": rows}
        age_h = (now - datetime.fromisoformat(withp[-1]["t"])).total_seconds() / 3600
        if age_h <= FRESH_H:
            return rec
        stale = stale or dict(rec, stale_hours=round(age_h, 1))
    return stale or {"station": None, "rows": []}


def alerts(lat, lon):
    """Alertas ativos do NWS no ponto: evento, gravidade, início e fim (ISO com fuso)."""
    feats = get(f"{API}/alerts/active", params={"point": f"{lat:.4f},{lon:.4f}"}, headers=GEO).get("features", [])
    out = []
    for f in feats:
        p = f["properties"]
        out.append({"event": p.get("event"), "severity": p.get("severity"), "headline": p.get("headline"),
                    "start": p.get("onset") or p.get("effective"), "end": p.get("ends") or p.get("expires"),
                    "id": p.get("id")})
    return out
