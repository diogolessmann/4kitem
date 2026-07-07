"""arena_quiz_stream.py — MOTOR DE QUIZ STREAMADO (torneio valendo, anti-cheat).

O que blinda o quiz pra valer dinheiro (o que o formato solto não tinha):
- O SERVIDOR entrega UMA pergunta por vez (o cliente nunca vê as 27 de uma vez pra pré-pesquisar).
- TEMPO curto por pergunta, cravado no servidor (janela pequena pro Google).
- Cada jogador recebe perguntas SORTEADAS DIFERENTES (do banco misto) — colar no grupo do zap
  não adianta, as perguntas do seu amigo não são as suas.
- A resposta certa NUNCA vai pro cliente antes de ele responder (só depois, pra feedback).
- O placar é 100% do servidor (conta acertos, corrige cada resposta).

Estado serializável (dict -> JSON) pra persistir entre requisições, igual arena_game_stream.
"""
import random
import arena_quiz

PERGUNTAS = 27          # 25-29 (Diogo: pra não ter ninguém 100%)
TEMPO_POR_PERGUNTA = 12 # segundos por pergunta — cravado no servidor


def iniciar(n=PERGUNTAS):
    """Sorteia n perguntas DIFERENTES do banco misto (baixa sobreposição entre jogadores =
    anti-colar) e embaralha as alternativas. Guarda o gabarito no estado do SERVIDOR."""
    todas = []
    for _tema, qs in arena_quiz.BANCO.items():
        todas.extend(qs)
    random.shuffle(todas)
    escolhidas = todas[:n]
    out = []
    for q in escolhidas:
        pares = list(enumerate(q['ops']))
        random.shuffle(pares)
        nc = next(i for i, (o, _) in enumerate(pares) if o == q['c'])
        out.append({'q': q['q'], 'ops': [t for _, t in pares], 'c': nc})
    return {'qs': out, 'idx': 0, 'score': 0, 'respostas': []}


def cliente_pergunta(st):
    """O que o cliente pode VER: a pergunta atual + alternativas — NUNCA o índice correto (c)."""
    i = st['idx']
    if i >= len(st['qs']):
        return {'over': True, 'score': st['score'], 'total': len(st['qs'])}
    q = st['qs'][i]
    return {'over': False, 'q': q['q'], 'ops': q['ops'], 'idx': i, 'total': len(st['qs']),
            'score': st['score'], 'tempo': TEMPO_POR_PERGUNTA}


def responder(st, escolha, dentro_do_tempo=True):
    """Corrige UMA resposta no servidor. escolha=índice tocado (ou -1 = passou/estourou o tempo).
    dentro_do_tempo=False (cronômetro do servidor estourou) conta como errada. Muta st.
    Retorna {correto, correta, score, over} — 'correta' revelado SÓ agora (o jogador já respondeu)."""
    i = st['idx']
    if i >= len(st['qs']):
        return {'over': True, 'score': st['score'], 'total': len(st['qs'])}
    q = st['qs'][i]
    try:
        escolha = int(escolha)
    except Exception:
        escolha = -1
    correto = bool(dentro_do_tempo) and (escolha == q['c'])
    if correto:
        st['score'] += 1
    st['respostas'].append(escolha)
    st['idx'] += 1
    return {'correto': correto, 'correta': q['c'], 'score': st['score'],
            'idx': i, 'over': st['idx'] >= len(st['qs']), 'total': len(st['qs'])}
