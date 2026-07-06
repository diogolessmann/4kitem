"""arena_game_cobrinha.py — MOTOR DA COBRINHA NO SERVIDOR (autoridade do placar, anti-cheat).

Mesma ideia do arena_game.py (blocos): o cliente manda as JOGADAS (mudanças de direção, cada
uma no seu TICK), o servidor re-simula a partida com a MESMA semente e calcula o placar
verdadeiro. Trapacear o placar = impossível.

Determinismo:
- Comida gerada por mulberry32 (reusa arena_game._Rng) — MESMA semente => mesma sequência de
  comidas (com rejeição determinística das células ocupadas pela cobra; o servidor re-simula a
  cobra EXATA do jogador, então a rejeição bate).
- Velocidade cresce com o placar (determinística); o SERVIDOR controla o limite de 90s somando
  o tempo simulado de cada tick (o cliente não decide quantos ticks jogou — o tempo simulado
  corta em 90s). Assim ninguém "joga mais tempo" pra somar mais pontos.

Contrato do cliente (o jogo_cobrinha valendo DEVE seguir isto pra bater com o servidor):
- Grade N=17, cobra começa em [(m,m),(m,m-1),(m,m-2)] indo pra DIREITA (dir=(0,1)).
- A cada tick: aplica a mudança de direção agendada pra esse tick (sem ré), move a cabeça,
  morre na parede ou no corpo (exceto a cauda), come se a cabeça cair na comida.
- Comida: _food(rng, snake) com rejeição das células da cobra.
- moves = lista de [tick, dr, dc] com ticks ESTRITAMENTE crescentes; dir ∈ {(-1,0),(1,0),(0,-1),(0,1)}.
"""
import arena_game as _ag   # reusa _Rng (mulberry32) — mesma base do blocos

N = 17
SPEED_BASE = 200
SPEED_MIN = 130
SPEED_STEP = 2
TIME_LIMIT_MS = 90000
MAX_TICKS = 6000
MAX_MOVES = 3000
_VALID_DIR = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _food(rng, snake_set):
    """Próxima comida por semente, pulando células ocupadas pela cobra (rejeição determinística)."""
    for _ in range(400):
        r = int(rng.next() * N)
        c = int(rng.next() * N)
        if (r, c) not in snake_set:
            return (r, c)
    return (int(rng.next() * N), int(rng.next() * N))   # fallback (cobra enorme) — praticamente nunca


def pontuar(seed, moves, max_moves=MAX_MOVES):
    """Re-simula a partida (seed + jogadas) e devolve o placar AUTORITATIVO (comidas comidas).
    None se qualquer jogada for inválida (formato, direção, ticks fora de ordem) = submissão rejeitada."""
    try:
        seed = int(seed) & 0xFFFFFFFF
    except Exception:
        return None
    if not isinstance(moves, list) or len(moves) > max_moves:
        return None
    inputs = {}
    last_tick = -1
    for mv in moves:
        if (not isinstance(mv, (list, tuple))) or len(mv) != 3:
            return None
        try:
            tk, dr, dc = int(mv[0]), int(mv[1]), int(mv[2])
        except Exception:
            return None
        if tk < 0 or tk > MAX_TICKS or (dr, dc) not in _VALID_DIR or tk <= last_tick:
            return None
        last_tick = tk
        inputs[tk] = (dr, dc)

    rng = _ag._Rng(seed)
    m = N // 2
    snake = [(m, m), (m, m - 1), (m, m - 2)]   # cabeça primeiro
    sset = set(snake)
    dirr = (0, 1)
    food = _food(rng, sset)
    score = 0
    simtime = 0
    tick = 0
    while simtime < TIME_LIMIT_MS and tick <= MAX_TICKS:
        nd = inputs.get(tick)
        if nd is not None and not (nd[0] == -dirr[0] and nd[1] == -dirr[1]):
            dirr = nd
        head = snake[0]
        h = (head[0] + dirr[0], head[1] + dirr[1])
        if h[0] < 0 or h[0] >= N or h[1] < 0 or h[1] >= N:
            break   # parede
        if h in sset and h != snake[-1]:   # corpo (a cauda vai sair, então vale entrar nela)
            break
        eating = (h == food)
        snake.insert(0, h); sset.add(h)
        if eating:
            score += 1
            food = _food(rng, sset)
        else:
            t = snake.pop()
            if t not in snake:          # cauda saiu do tabuleiro
                sset.discard(t)
        speed = max(SPEED_MIN, SPEED_BASE - score * SPEED_STEP)
        simtime += speed
        tick += 1
    return score
