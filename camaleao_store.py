"""camaleao_store.py — salas do CAMALEAO em RAM (dict sob RLock).

O estado quente vive AQUI, não em SQLite: o deploy roda
`gunicorn app:app --workers 1 --preload` (UM processo), então um dict global é
compartilhado por todas as requisições e aguenta as mutações ~5x/s do /sync sem
thrash de disco. O SQLite (camaleao_db) só grava o histórico no RESULT.
Tradeoff assumido: redeploy do Railway zera isto -> a rodada em curso morre
(rodada ~2min, deploy raro; o cliente avisa "servidor reiniciou").
"""
import threading
import time
import secrets

_LOCK = threading.RLock()
ROOMS = {}                 # token -> room(dict)

REAP_VAZIA_S = 60          # sala sem atividade por mais que isso sai da RAM
MAX_SALAS = 400            # teto de salas simultâneas (anti-leak de RAM)
SYNC_MIN_MS = 120          # rate-limit: /sync mais rápido que isso reusa o snapshot em cache


def now_ms():
    return time.time() * 1000.0


def novo_token():
    return secrets.token_urlsafe(9)


def lock():
    """RLock global — pegue com `with camaleao_store.lock():` ao mutar uma sala."""
    return _LOCK


def criar(room):
    """Insere uma sala nova (já montada). Retorna o token, ou None se lotou a RAM."""
    with _LOCK:
        _reap()
        if len(ROOMS) >= MAX_SALAS:
            return None
        ROOMS[room['token']] = room
        return room['token']


def get(token):
    with _LOCK:
        return ROOMS.get(token)


def drop(token):
    with _LOCK:
        ROOMS.pop(token, None)


def _reap():
    """Remove salas sem atividade recente (lazy — roda no criar). Chame já com o lock."""
    now = now_ms()
    mortas = [t for t, r in ROOMS.items()
              if now - r.get('ultima_atividade', 0) > REAP_VAZIA_S * 1000]
    for t in mortas:
        ROOMS.pop(t, None)


def salas_ativas():
    with _LOCK:
        return len(ROOMS)
