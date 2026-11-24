"""Imprime, para conferir à mão, os números que o motor guardou de 3 lugares (NWS e NOAA).

Uso: python confere.py lake-fork galveston corpus-christi
Comparar com: forecast.weather.gov (MapClick lat/lon) e tidesandcurrents.noaa.gov (predições da estação).
"""
import json
import sys
from datetime import datetime, timedelta

from hdp.config import DATA, TZ
from hdp.engine import load_run
from hdp.score import KMH_TO_MPH, Weather, c_to_f


def main(ids):
    run_id, run = load_run()
    places = {p["id"]: p for p in json.loads((DATA / "places.json").read_text(encoding="utf-8"))["places"]}
    print(f"coleta {run_id}")
    for pid in ids:
        p, raw = places[pid], run[pid]
        wx = Weather(raw.get("grid"), raw.get("obs"), datetime.fromisoformat(raw["collected_at"]))
        print(f"\n== {p['name']}  ({p['lat']}, {p['lon']})  grade {raw['point']['gridId']} {raw['point']['gridX']},{raw['point']['gridY']}")
        print(f"   NWS: https://forecast.weather.gov/MapClick.php?lat={p['lat']}&lon={p['lon']}")
        day0 = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        for d in range(3):
            a = day0 + timedelta(days=d)
            # máxima do dia (6h-18h) e mínima da noite seguinte (18h-6h), como o texto do NWS faz
            hi = [c_to_f(v["temp_c"]) for h in range(6, 19) if (v := wx.at(a + timedelta(hours=h))) and "temp_c" in v]
            lo = [c_to_f(v["temp_c"]) for h in range(18, 31) if (v := wx.at(a + timedelta(hours=h))) and "temp_c" in v]
            wind = [v["wind_kmh"] * KMH_TO_MPH for h in range(6, 19) if (v := wx.at(a + timedelta(hours=h))) and "wind_kmh" in v]
            print(f"   {a:%a %d/%m}: max dia {round(max(hi)) if hi else '-'}°F | min noite {round(min(lo)) if lo else '-'}°F | vento dia {round(min(wind)) if wind else '-'}-{round(max(wind)) if wind else '-'} mph")
        tr = wx.press_trend
        print(f"   pressão obs ({(raw.get('obs') or {}).get('station')}): {tr}")
        if raw.get("tide"):
            print(f"   NOAA: https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id={p['noaa_station']}  datum {raw['tide']['datum']}")
            for t in raw["tide"]["hilo"][:8]:
                print(f"     {t['type']} {t['t']}  {t['ft']:.2f} ft")


if __name__ == "__main__":
    main(sys.argv[1:] or ["lake-fork", "galveston", "corpus-christi"])
