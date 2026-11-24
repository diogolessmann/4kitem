"""HTTP com retry curto e User-Agent fixo."""
import time

import requests

from .config import USER_AGENT


def get(url, params=None, headers=None, tries=3, timeout=40, as_json=True):
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=h, timeout=timeout)
            if r.status_code == 200:
                return r.json() if as_json else r.text
            last = f"HTTP {r.status_code}"
            if r.status_code in (400, 404):
                break
        except requests.RequestException as e:  # rede caiu, timeout
            last = str(e)[:200]
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url} -> {last}")
