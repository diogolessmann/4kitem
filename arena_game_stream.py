"""arena_game_stream.py — MOTOR COM ESTADO (blindagem anti-cheat definitiva).

Diferença pro arena_game.py: em vez de mandar a SEMENTE pro cliente (que aí gera todas as
peças e pode pré-calcular a partida ótima com um solver offline), aqui o SERVIDOR guarda a
semente e entrega a BANDEJA (3 peças) por vez — a próxima leva só é revelada depois que a
atual foi usada. Sem conhecer as peças futuras, NÃO dá pra pré-solucionar a partida.

O placar é calculado 100% no servidor, jogada por jogada (o cliente nunca envia pontos, só
[slot, linha, coluna] da peça da bandeja atual). Estado serializável (dict -> JSON) pra
persistir entre requisições (uma tabela guarda a partida em andamento).

Reusa o gerador de peças provado (mesma sequência por semente do arena_game).
"""
from arena_game import _Rng, _gen_piece, N, _MASK


def _offs(t):
    return [list(p) for p in t]


def _fits(board, offs, r, c):
    for dr, dc in offs:
        rr, cc = r + dr, c + dc
        if rr < 0 or rr >= N or cc < 0 or cc >= N or board[rr][cc]:
            return False
    return True


def _algum_cabe(board, tray, used):
    """Alguma peça não-usada da bandeja cabe em algum lugar? (senão = fim de jogo)."""
    for s in range(3):
        if used[s]:
            continue
        offs = tray[s]
        for r in range(N):
            for c in range(N):
                if _fits(board, offs, r, c):
                    return True
    return False


def _nova_bandeja(st):
    """Gera a próxima bandeja (3 peças). Rebobina o rng pela contagem de peças já geradas —
    determinístico, sem precisar guardar o estado interno do gerador."""
    rng = _Rng(st['seed'])
    for _ in range(st['geradas']):
        _gen_piece(rng)                       # fast-forward até a posição atual
    st['tray'] = [_offs(_gen_piece(rng)) for _ in range(3)]
    st['used'] = [False, False, False]
    st['geradas'] += 3


def iniciar(seed):
    """Começa uma partida: tabuleiro vazio + primeira bandeja. Retorna o estado (dict serializável).
    A semente FICA no estado do servidor — nunca vai pro cliente."""
    seed = int(seed) & _MASK
    st = {'seed': seed, 'board': [[0] * N for _ in range(N)],
          'tray': None, 'used': None, 'score': 0, 'streak': 0, 'geradas': 0}
    _nova_bandeja(st)
    return st


def cliente_bandeja(st):
    """O que o cliente pode VER: só a bandeja atual (peças + usadas) + placar. NUNCA a semente."""
    return {'tray': st['tray'], 'used': st['used'], 'score': st['score'],
            'over': not _algum_cabe(st['board'], st['tray'], st['used'])}


def jogar(st, slot, r, c):
    """Aplica UMA jogada (peça 'slot' da bandeja em (r,c)). Muta st. Retorna:
      {ok:True, over, score, tray, used, novo_tray}  se válida
      {erro, over:True}                              se inválida (peça não cabe / slot já usado)
    over=True quando não cabe mais NENHUMA peça (fim de jogo)."""
    try:
        slot, r, c = int(slot), int(r), int(c)
    except Exception:
        return {'erro': 'jogada inválida', 'over': True}
    board, tray, used = st['board'], st['tray'], st['used']
    if slot < 0 or slot > 2 or used[slot]:
        return {'erro': 'slot inválido ou já usado', 'over': True}
    offs = tray[slot]
    if not _fits(board, offs, r, c):
        return {'erro': 'peça não cabe aí', 'over': True}
    for dr, dc in offs:
        board[r + dr][c + dc] = 1
    used[slot] = True
    st['score'] += len(offs)
    full_rows = [i for i in range(N) if all(board[i][j] for j in range(N))]
    full_cols = [j for j in range(N) if all(board[i][j] for i in range(N))]
    lines = len(full_rows) + len(full_cols)
    if lines:
        for i in full_rows:
            for j in range(N):
                board[i][j] = 0
        for j in full_cols:
            for i in range(N):
                board[i][j] = 0
        st['streak'] += 1
        st['score'] += 10 * lines * lines + ((st['streak'] - 1) * 10 if st['streak'] > 1 else 0)
    else:
        st['streak'] = 0
    novo = False
    if used[0] and used[1] and used[2]:
        _nova_bandeja(st)                     # revela a PRÓXIMA leva só agora
        novo = True
    over = not _algum_cabe(st['board'], st['tray'], st['used'])
    return {'ok': True, 'over': over, 'score': st['score'],
            'tray': st['tray'], 'used': st['used'], 'novo_tray': novo}
