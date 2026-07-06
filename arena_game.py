"""arena_game.py — MOTOR DO JOGO NO SERVIDOR (autoridade do placar).

Anti-cheat por construção: o cliente NÃO envia pontos, envia as JOGADAS (lista de
[slot, linha, coluna]). O servidor re-simula a partida com a MESMA semente (mulberry32,
idêntico ao JS de templates/arena/jogo.html) e calcula o placar VERDADEIRO. Jogada inválida
(peça que não cabe, slot já usado) = submissão rejeitada. Placar não se declara, se prova.

Determinismo: a sequência de peças depende SÓ da semente + quantas peças foram geradas
(cada peça consome 2 rnd: forma, depois cor — a cor é ignorada no placar mas consome o
gerador pra manter o sincronismo com o cliente). Mesma semente => peças idênticas p/ os dois
jogadores => a diferença de placar é SÓ habilidade (defesa legal: skill, não sorte).
"""

N = 8
_MASK = 0xFFFFFFFF

# Formas (peso, offsets) — CÓPIA EXATA do array SHAPES do jogo.html (mesma ordem/pesos).
_SHAPES = [
    (5, [[0, 0]]),
    (7, [[0, 0], [0, 1]]), (7, [[0, 0], [1, 0]]),
    (7, [[0, 0], [0, 1], [0, 2]]), (7, [[0, 0], [1, 0], [2, 0]]),
    (5, [[0, 0], [0, 1], [0, 2], [0, 3]]), (5, [[0, 0], [1, 0], [2, 0], [3, 0]]),
    (2, [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4]]), (2, [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]]),
    (6, [[0, 0], [0, 1], [1, 0], [1, 1]]),
    (2, [[0, 0], [0, 1], [0, 2], [1, 0], [1, 1], [1, 2], [2, 0], [2, 1], [2, 2]]),
    (6, [[0, 0], [1, 0], [1, 1]]), (6, [[0, 0], [0, 1], [1, 0]]),
    (6, [[0, 0], [0, 1], [1, 1]]), (6, [[0, 1], [1, 0], [1, 1]]),
    (4, [[0, 0], [1, 0], [2, 0], [2, 1]]), (4, [[0, 1], [1, 1], [2, 1], [2, 0]]),
    (4, [[0, 0], [0, 1], [0, 2], [1, 0]]), (4, [[0, 0], [0, 1], [0, 2], [1, 2]]),
    (4, [[0, 0], [0, 1], [0, 2], [1, 1]]),
    (3, [[0, 1], [0, 2], [1, 0], [1, 1]]), (3, [[0, 0], [0, 1], [1, 1], [1, 2]]),
    (3, [[0, 0], [0, 1], [0, 2], [1, 0], [1, 1], [1, 2]]),
    (3, [[0, 0], [0, 1], [1, 0], [1, 1], [2, 0], [2, 1]]),
]
# POOL ponderado — mesma expansão do JS: para cada forma, 'peso' cópias, na ordem.
_POOL = []
for _w, _offs in _SHAPES:
    for _ in range(_w):
        _POOL.append(_offs)
_NPOOL = len(_POOL)
_NCOLORS = 8   # COLORS.length no JS (a cor é irrelevante pro placar, mas consome 1 rnd)


def _imul(a, b):
    """Equivalente ao Math.imul do JS: multiplicação inteira 32-bit (low 32 bits)."""
    return ((a & _MASK) * (b & _MASK)) & _MASK


class _Rng:
    """mulberry32 — bit-a-bit idêntico ao rnd() do jogo.html (unsigned 32-bit)."""
    def __init__(self, seed):
        self.s = (int(seed) & _MASK)

    def next(self):
        self.s = (self.s + 0x6D2B79F5) & _MASK
        t = self.s
        t = _imul(t ^ (t >> 15), 1 | t)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) ^ t) & _MASK   # JS: t = t + Math.imul(...) ^ t
        t = (t ^ (t >> 14)) & _MASK
        return t / 4294967296.0


def _gen_piece(rng):
    """Gera a próxima peça: 1 rnd p/ a forma + 1 rnd p/ a cor (descartada). Retorna offsets."""
    shape = _POOL[int(rng.next() * _NPOOL)]
    rng.next()   # cor — consumida pra manter o sincronismo, valor ignorado
    return shape


def pontuar(seed, moves, max_moves=2000):
    """Re-simula a partida (seed + jogadas) e devolve o placar AUTORITATIVO.

    moves: lista de [slot, linha, coluna] na ordem em que o jogador encaixou.
    Retorna int (placar) se toda jogada for válida; None se qualquer jogada for inválida
    (fora do tabuleiro, célula ocupada, slot vazio/já usado) — submissão rejeitada.
    """
    try:
        seed = int(seed) & _MASK
    except Exception:
        return None
    if not isinstance(moves, list) or len(moves) > max_moves:
        return None

    rng = _Rng(seed)
    board = [[None] * N for _ in range(N)]
    tray = [_gen_piece(rng), _gen_piece(rng), _gen_piece(rng)]
    used = [False, False, False]
    score = 0
    streak = 0

    for mv in moves:
        if (not isinstance(mv, (list, tuple))) or len(mv) != 3:
            return None
        try:
            slot, r, c = int(mv[0]), int(mv[1]), int(mv[2])
        except Exception:
            return None
        if slot < 0 or slot > 2 or used[slot]:
            return None
        offs = tray[slot]
        # valida encaixe
        for dr, dc in offs:
            rr, cc = r + dr, c + dc
            if rr < 0 or rr >= N or cc < 0 or cc >= N or board[rr][cc] is not None:
                return None
        # coloca
        for dr, dc in offs:
            board[r + dr][c + dc] = 1
        used[slot] = True
        score += len(offs)
        # limpa linhas/colunas cheias
        full_rows = [i for i in range(N) if all(board[i][j] is not None for j in range(N))]
        full_cols = [j for j in range(N) if all(board[i][j] is not None for i in range(N))]
        lines = len(full_rows) + len(full_cols)
        if lines:
            for i in full_rows:
                for j in range(N):
                    board[i][j] = None
            for j in full_cols:
                for i in range(N):
                    board[i][j] = None
            streak += 1
            score += 10 * lines * lines + ((streak - 1) * 10 if streak > 1 else 0)
        else:
            streak = 0
        # reabastece o tray quando as 3 peças foram usadas
        if used[0] and used[1] and used[2]:
            tray = [_gen_piece(rng), _gen_piece(rng), _gen_piece(rng)]
            used = [False, False, False]

    return score


def sequencia_pecas(seed, n):
    """Debug/teste: os offsets das primeiras n peças de uma semente (p/ conferir vs o cliente)."""
    rng = _Rng(int(seed) & _MASK)
    return [_gen_piece(rng) for _ in range(n)]
