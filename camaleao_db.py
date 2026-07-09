"""camaleao_db.py — histórico das partidas do CAMALEAO (SQLite no DATA_DIR).

Única escrita em disco do jogo: uma linha por partida encerrada (gravada no
RESULT). O estado quente vive em RAM (camaleao_store); aqui é só o log histórico.
Reusa o mesmo arquivo do Arena (arena.db) no volume DATA_DIR do Railway.
"""
import os
import json
import sqlite3
import time

DATA_DIR = os.environ.get('DATA_DIR', '.')
DB_PATH = os.path.join(DATA_DIR, 'arena.db')


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_schema():
    c = _conn()
    c.execute('''CREATE TABLE IF NOT EXISTS camaleao_partidas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT,
        modo TEXT,
        cena TEXT,
        n_jogadores INTEGER,
        vencedor TEXT,
        jogadores_json TEXT,
        criado_em REAL
    )''')
    c.commit()
    c.close()


def gravar_partida(token, modo, cena, jogadores, vencedor):
    """Grava o histórico de uma partida encerrada. Nunca derruba a rodada se falhar."""
    try:
        c = _conn()
        c.execute(
            'INSERT INTO camaleao_partidas '
            '(token, modo, cena, n_jogadores, vencedor, jogadores_json, criado_em) '
            'VALUES (?,?,?,?,?,?,?)',
            (token, modo, cena, len(jogadores), vencedor,
             json.dumps(jogadores, ensure_ascii=False), time.time()))
        c.commit()
        c.close()
    except Exception:
        pass
