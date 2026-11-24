"""Coleta: TPWD (lagos), NWS (tempo + pressão observada) e NOAA (maré) para os 20 lugares.

Uso:  python coleta.py            (grava data/cache/runs/<AAAAMMDD_HHMM>/ e data/cache/latest.txt)
      python coleta.py --tpwd     (força baixar de novo as páginas dos lagos)
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone

from hdp import noaa, nws, tpwd
from hdp.config import CACHE, DATA, DAYS, TZ


def _velho(f, dias=7):
    """Página do TPWD falta ou tem mais de 7 dias: baixa de novo (regra muda pouco, mas muda; no volume o cache não some)."""
    return not f.exists() or time.time() - f.stat().st_mtime > dias * 86400


def main(force_tpwd=False):
    places = json.loads((DATA / "places.json").read_text(encoding="utf-8"))["places"]
    now = datetime.now(TZ)
    run_id = now.strftime("%Y%m%d_%H%M")
    rd = CACHE / "runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (CACHE / "tpwd").mkdir(parents=True, exist_ok=True)

    # 1) TPWD: página de cada lago (muda pouco; baixa se faltar ou com --tpwd)
    lakes_path = DATA / "tpwd_lakes.json"
    lakes = json.loads(lakes_path.read_text(encoding="utf-8")) if lakes_path.exists() else {}
    for p in places:
        slug = p.get("tpwd_slug")
        if not slug:
            continue
        cached = CACHE / "tpwd" / f"{slug}.html"
        try:
            if force_tpwd or _velho(cached):
                cached.write_text(tpwd.fetch(slug), encoding="utf-8")
                fetched = now.isoformat(timespec="minutes")
                time.sleep(1)
            else:
                fetched = (lakes.get(slug) or {}).get("fetched") or now.isoformat(timespec="minutes")
            lakes[slug] = dict(tpwd.parse(cached.read_text(encoding="utf-8"), slug), fetched=fetched)  # sempre re-lê
            if lakes[slug]["regs_url"]:   # limites do próprio lago (fishregs2.php)
                rc = CACHE / "tpwd" / f"regs_{slug}.html"
                if force_tpwd or _velho(rc):
                    rc.write_text(tpwd.fetch_regs(lakes[slug]["regs_url"]), encoding="utf-8")
                    time.sleep(1)
                lakes[slug]["lake_regs"] = tpwd.parse_lake_regs(rc.read_text(encoding="utf-8"))
                lakes[slug]["regs_fetched"] = datetime.fromtimestamp(rc.stat().st_mtime, TZ).isoformat(timespec="minutes")
                if not lakes[slug]["lake_regs"]:
                    print(f"  TPWD {slug}: página de limites sem regras legíveis")
        except Exception as e:  # segue sem o lago: a página do lugar mostra o aviso e não inventa peixe
            print(f"  TPWD {slug}: ERRO {e}")
    lakes_path.write_text(json.dumps(lakes, indent=1, ensure_ascii=False), encoding="utf-8")

    # 2) NWS + NOAA por lugar
    pts_path = CACHE / "nws_points.json"
    pts = json.loads(pts_path.read_text(encoding="utf-8")) if pts_path.exists() else {}
    ok = 0
    for p in places:
        rec = {"place": p["id"], "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "errors": []}
        key = f"{p['lat']:.4f},{p['lon']:.4f}"
        pt = pts.get(key)
        if not pt:
            try:
                pt = pts[key] = nws.point(p["lat"], p["lon"])
            except Exception as e:
                rec["errors"].append(f"point: {e}")
        rec["point"] = pt
        if pt:
            for name, fn, arg in (("grid", nws.grid_hourly, "forecastGridData"),
                                  ("hourly", nws.forecast_hourly, "forecastHourly"),
                                  ("obs", nws.observations, "observationStations")):
                try:
                    rec[name] = fn(pt[arg])
                except Exception as e:
                    rec["errors"].append(f"{name}: {e}")
                time.sleep(0.5)
            try:
                rec["alerts"] = nws.alerts(p["lat"], p["lon"])
            except Exception as e:
                rec["errors"].append(f"alerts: {e}")
        if p.get("noaa_station"):
            try:
                begin = now.replace(hour=0, minute=0).astimezone(timezone.utc) - timedelta(hours=2)
                rec["tide"] = noaa.tides(p["noaa_station"], begin.strftime("%Y%m%d %H:%M"), 24 * DAYS + 6)
            except Exception as e:
                rec["errors"].append(f"tide: {e}")
        (rd / f"{p['id']}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        g = len((rec.get("grid") or {}).get("hours", {}))
        o = len((rec.get("obs") or {}).get("rows", []))
        tdn = len((rec.get("tide") or {}).get("series", []))
        status = "ok" if not rec["errors"] else "FALHA: " + "; ".join(rec["errors"])[:160]
        ok += not rec["errors"]
        al = ", ".join(a["event"] for a in rec.get("alerts") or []) or "-"
        print(f"  {p['id']:<20} grade {g:>3} h | obs {o:>3} | maré {tdn:>4} pts | alertas: {al} | {status}")
    pts_path.write_text(json.dumps(pts, indent=1), encoding="utf-8")
    (CACHE / "latest.txt").write_text(run_id, encoding="utf-8")
    print(f"Coleta {run_id}: {ok}/{len(places)} lugares sem erro; lagos TPWD: {len(lakes)}")
    return run_id


if __name__ == "__main__":
    main(force_tpwd="--tpwd" in sys.argv)
