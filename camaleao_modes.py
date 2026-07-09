"""camaleao_modes.py — modos do jogo CAMALEAO como DADO.

Adicionar um modo novo = adicionar uma entrada aqui. O motor (camaleao_engine)
e as rotas (arena_camaleao) sao agnosticos: leem timings/scoring/ratio/velocidade
daqui. Jogo 100% GRATIS — nada de dinheiro/aposta em lugar nenhum.
"""

MODES = {
    'classico': {
        'id': 'classico',
        'nome': 'Clássico',
        'scene': 'beco_neon',
        'min_jogadores': 2,
        'max_jogadores': 10,
        'seeker_ratio': 0.25,       # ceil(ratio*N) viram caçadores (>=1); resto escondidos
        'hiding_s': 25,             # tempo pra se esconder
        'seeking_s': 100,           # tempo pra caçar
        'result_s': 12,             # tela de resultado antes de voltar pro lobby
        'reach': 95.0,              # alcance da cutucada (unidades de mundo)
        'seeker_speed': 280.0,      # velocidade máx do caçador (u/seg) — clamp no servidor
        'hider_speed': 250.0,       # velocidade máx do escondido (u/seg)
        'idle_lock_ms': 1500,       # parado por este tempo => auto-camufla (só cliente/visual)
        'cooldown_miss_ms': 3500,   # errar a cutucada => cooldown do caçador
        'score_hit': 100,           # caçador acha um escondido
        'score_time_bonus': 1.0,    # + por segundo restante no momento do achou
        'score_miss': -15,          # cutucar decoy / errado
        'score_survive': 150,       # escondido sobrevive até o fim
    },
}

DEFAULT_MODE = 'classico'


def get_mode(mode_id):
    """Retorna o dict do modo; cai no padrão se o id não existir."""
    return MODES.get(mode_id) or MODES[DEFAULT_MODE]
