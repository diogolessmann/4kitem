"""arena_quiz.py — QUIZ GRÁTIS do Arena (isca viral, vários temas).

NÃO é valendo dinheiro (não dá pra impedir o Google numa outra aba). É o motor de atração:
"fiz 9/10 no quiz de anime, bate meu recorde 👉 link". Traz gente pro app, que descobre os
jogos de HABILIDADE (esses sim valendo, onde o anti-cheat segura).

BANCO é preenchido pela fábrica de perguntas (workflow arena-quiz-bancos). Este arquivo começa
com um banco inicial pequeno e é substituído pelo grande quando a fábrica termina.
"""
import os
import json
import random

TEMAS = [
    {'id': 'anime',     'nome': 'Anime & Mangá',    'emoji': '🍥'},
    {'id': 'filmes',    'nome': 'Filmes & Séries',  'emoji': '🎬'},
    {'id': 'brasil',    'nome': 'Brasil',           'emoji': '🇧🇷'},
    {'id': 'geografia', 'nome': 'Geografia',        'emoji': '🌍'},
    {'id': 'carros',    'nome': 'Carros',           'emoji': '🚗'},
    {'id': 'comida',    'nome': 'Comida',           'emoji': '🍔'},
    {'id': 'ingles',    'nome': 'Inglês',           'emoji': '🔤'},
    {'id': 'ciencia',   'nome': 'Ciência',          'emoji': '🔬'},
    {'id': 'historia',  'nome': 'História',         'emoji': '📜'},
    {'id': 'esportes',  'nome': 'Esportes',         'emoji': '⚽'},
]

# {tema: [{'q': pergunta, 'ops': [4 alternativas], 'c': índice da correta}]}
_STARTER = {   # banco inicial pequeno — fallback se o JSON grande faltar
    'anime': [
        {'q': 'Qual o nome do protagonista de Naruto?', 'ops': ['Sasuke', 'Naruto', 'Kakashi', 'Gaara'], 'c': 1},
        {'q': 'Em One Piece, qual o sonho do Luffy?', 'ops': ['Ser Hokage', 'Ser o Rei dos Piratas', 'Achar as Dragon Balls', 'Virar shinigami'], 'c': 1},
        {'q': 'Quem é o autor de Dragon Ball?', 'ops': ['Eiichiro Oda', 'Akira Toriyama', 'Masashi Kishimoto', 'Tite Kubo'], 'c': 1},
        {'q': 'Em qual anime titãs devoram humanos?', 'ops': ['Naruto', 'Attack on Titan', 'Bleach', 'Death Note'], 'c': 1},
        {'q': 'Qual o tipo (elemento) do Pikachu?', 'ops': ['Fogo', 'Água', 'Elétrico', 'Planta'], 'c': 2},
    ],
    'filmes': [
        {'q': 'Quem dirigiu o filme Titanic (1997)?', 'ops': ['Steven Spielberg', 'James Cameron', 'Christopher Nolan', 'Martin Scorsese'], 'c': 1},
        {'q': 'Qual o nome verdadeiro do Homem de Ferro?', 'ops': ['Bruce Wayne', 'Tony Stark', 'Peter Parker', 'Steve Rogers'], 'c': 1},
        {'q': 'Em Star Wars, qual a arma clássica dos Jedi?', 'ops': ['Blaster', 'Sabre de luz', 'Arco', 'Bastão'], 'c': 1},
        {'q': 'De qual filme é a frase "I\'ll be back"?', 'ops': ['Rambo', 'O Exterminador do Futuro', 'Rocky', 'Duro de Matar'], 'c': 1},
        {'q': 'Qual estúdio produziu Toy Story?', 'ops': ['DreamWorks', 'Pixar', 'Illumination', 'Blue Sky'], 'c': 1},
    ],
    'brasil': [
        {'q': 'Qual é a capital do Brasil?', 'ops': ['Rio de Janeiro', 'São Paulo', 'Brasília', 'Salvador'], 'c': 2},
        {'q': 'Quem comandou a esquadra que chegou ao Brasil em 1500?', 'ops': ['Cristóvão Colombo', 'Pedro Álvares Cabral', 'Vasco da Gama', 'Américo Vespúcio'], 'c': 1},
        {'q': 'Prato brasileiro feito com feijão preto e carnes:', 'ops': ['Feijoada', 'Moqueca', 'Acarajé', 'Vatapá'], 'c': 0},
        {'q': 'Qual o maior estado do Brasil em área?', 'ops': ['Bahia', 'Amazonas', 'Pará', 'Mato Grosso'], 'c': 1},
        {'q': 'Qual cor NÃO aparece na bandeira do Brasil?', 'ops': ['Verde', 'Amarelo', 'Azul', 'Vermelho'], 'c': 3},
    ],
    'geografia': [
        {'q': 'Qual é a capital da França?', 'ops': ['Madri', 'Paris', 'Roma', 'Berlim'], 'c': 1},
        {'q': 'Qual é o maior oceano do mundo?', 'ops': ['Atlântico', 'Índico', 'Pacífico', 'Ártico'], 'c': 2},
        {'q': 'Qual o maior país do mundo em área?', 'ops': ['China', 'EUA', 'Rússia', 'Canadá'], 'c': 2},
        {'q': 'Em qual continente fica o Egito?', 'ops': ['Ásia', 'Europa', 'África', 'Oceania'], 'c': 2},
        {'q': 'Qual o maior deserto quente do mundo?', 'ops': ['Saara', 'Gobi', 'Atacama', 'Kalahari'], 'c': 0},
    ],
    'carros': [
        {'q': 'De qual país é a marca Ferrari?', 'ops': ['Alemanha', 'Itália', 'França', 'Japão'], 'c': 1},
        {'q': 'Qual marca tem o logo das 4 argolas?', 'ops': ['BMW', 'Audi', 'Mercedes', 'Volkswagen'], 'c': 1},
        {'q': 'De qual país é a montadora Toyota?', 'ops': ['China', 'Coreia do Sul', 'Japão', 'EUA'], 'c': 2},
        {'q': 'Qual destas é uma montadora alemã?', 'ops': ['Fiat', 'Renault', 'BMW', 'Volvo'], 'c': 2},
        {'q': 'Quantas rodas tem um carro de passeio comum?', 'ops': ['2', '3', '4', '6'], 'c': 2},
    ],
    'comida': [
        {'q': 'De qual país é originária a pizza?', 'ops': ['França', 'Itália', 'EUA', 'Grécia'], 'c': 1},
        {'q': 'O guacamole é feito principalmente de qual fruta?', 'ops': ['Tomate', 'Abacate', 'Manga', 'Pepino'], 'c': 1},
        {'q': 'O sushi é um prato típico de qual país?', 'ops': ['China', 'Tailândia', 'Japão', 'Coreia do Sul'], 'c': 2},
        {'q': 'Qual fruta é a base do vinho?', 'ops': ['Maçã', 'Uva', 'Laranja', 'Pera'], 'c': 1},
        {'q': 'O que faz o pão crescer?', 'ops': ['Sal', 'Fermento', 'Açúcar', 'Óleo'], 'c': 1},
    ],
    'ingles': [
        {'q': 'O que significa "dog"?', 'ops': ['Gato', 'Cachorro', 'Pássaro', 'Peixe'], 'c': 1},
        {'q': 'Como se diz "casa" em inglês?', 'ops': ['House', 'Horse', 'Mouse', 'Hose'], 'c': 0},
        {'q': 'O que significa "apple"?', 'ops': ['Abacaxi', 'Maçã', 'Uva', 'Pera'], 'c': 1},
        {'q': 'Complete: "I ___ a student."', 'ops': ['am', 'is', 'are', 'be'], 'c': 0},
        {'q': 'O que significa a cor "blue"?', 'ops': ['Vermelho', 'Verde', 'Azul', 'Amarelo'], 'c': 2},
    ],
}

# banco grande (gerado pela fábrica de perguntas) fica num JSON ao lado; fallback = _STARTER por tema
try:
    with open(os.path.join(os.path.dirname(__file__), 'arena_quiz_banco.json'), encoding='utf-8') as _f:
        BANCO = json.load(_f)
    for _t in _STARTER:
        if not BANCO.get(_t):
            BANCO[_t] = _STARTER[_t]
except Exception:
    BANCO = dict(_STARTER)


def sortear(tema, n=10):
    """N perguntas aleatórias do tema, com a ORDEM das alternativas embaralhada (e o índice
    correto recalculado). Retorna [] se o tema não existe."""
    qs = BANCO.get(tema) or []
    n = min(n, len(qs))
    escolhidas = random.sample(qs, n) if n else []
    out = []
    for q in escolhidas:
        pares = list(enumerate(q['ops']))
        random.shuffle(pares)
        nova_c = next(i for i, (orig, _) in enumerate(pares) if orig == q['c'])
        out.append({'q': q['q'], 'ops': [t for _, t in pares], 'c': nova_c})
    return out
