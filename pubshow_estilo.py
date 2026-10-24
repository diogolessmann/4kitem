"""
pubshow_estilo.py — "Aqui só toca rock" com a busca do YouTube livre (17/10/26).

Diogo: "deixando o YouTube livre, existe forma de limitar o estilo musical da busca? tipo pode pesquisar no
YouTube mas aqui só toca rock". O estilo do bar é o que ele JÁ marca no painel (generos_jukebox: Rock, Punk,
Sertanejo...): vale pra lista do bar E pro YouTube. Nenhum estilo marcado = tudo liberado (como sempre foi).

Classifica pelo ARTISTA, não pelo vídeo, e cada artista é perguntado UMA vez só (tabela pubshow_artista_estilo):
  1. o que já está na tabela (por vídeo 'v:<id>' ou pelo nome do artista)
  2. SEMENTE: os ~300 artistas mais pedidos em bar no Brasil (funciona mesmo com o Gemini fora ou sem crédito)
  3. 1 pergunta curta ao Gemini com os vídeos que faltam (título + canal), num fio separado e com tempo máximo
Deezer, Last.fm e MusicBrainz ficaram de fora: os termos deles proíbem uso comercial (o PubShow é pago).
Artista desconhecido PASSA (não barra cliente à toa); artista de vários estilos conta como todos.
Desliga tudo sem deploy: PUBSHOW_ESTILO_YT=0.
"""
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import deque

import requests

from pubshow_db import get_pubshow_db

log = logging.getLogger(__name__)

ESTILOS = ('rock', 'sertanejo', 'pagode', 'pop', 'funk', 'eletronica', 'rap', 'mpb', 'reggae', 'blues', 'outros')
NOMES = {'rock': 'Rock', 'sertanejo': 'Sertanejo', 'pagode': 'Pagode', 'pop': 'Pop', 'funk': 'Funk',
         'eletronica': 'Eletrônica', 'rap': 'Rap', 'mpb': 'MPB', 'reggae': 'Reggae', 'blues': 'Blues', 'outros': 'Outros'}
EMOJI = {'rock': '🎸', 'sertanejo': '🤠', 'pagode': '🥁', 'pop': '🎤', 'funk': '🔊', 'eletronica': '🎧', 'rap': '🎙️',
         'mpb': '🎼', 'reggae': '🌴', 'blues': '🎷', 'outros': '🎶'}
# canal/gênero do acervo (CANAIS do pubshow.py, o que o bar marca no painel) → estilo do artista.
# Esporte, stand-up e batidas não são estilo de música: marcar só eles não trava o YouTube.
CANAL_ESTILO = {'rock': 'rock', 'punk': 'rock', 'show_rock': 'rock',
                'sertanejo': 'sertanejo', 'show_sertanejo': 'sertanejo', 'country': 'sertanejo',
                'pagode': 'pagode', 'show_pagode': 'pagode', 'pop': 'pop', 'reggae': 'reggae',
                'mpb': 'mpb', 'blues': 'blues'}   # 22/10/26: botões MPB e Blues do Estilo do bar

# o Gemini às vezes responde fora da lista ("country", "samba"): vira o estilo da casa
_SINONIMOS = {'country': 'sertanejo', 'arrocha': 'sertanejo', 'samba': 'pagode', 'metal': 'rock', 'punk': 'rock',
              'hard rock': 'rock', 'blues rock': 'blues', 'trap': 'rap', 'hip hop': 'rap', 'hip-hop': 'rap',
              'eletrônica': 'eletronica', 'electronic': 'eletronica', 'edm': 'eletronica', 'house': 'eletronica',
              'forro': 'outros', 'forró': 'outros', 'piseiro': 'outros', 'axe': 'outros', 'axé': 'outros',
              'gospel': 'outros', 'infantil': 'outros', 'funk carioca': 'funk', 'pop rock': 'rock'}
ESPERA_BUSCA = 3.0              # segundos que a busca espera o Gemini (depois disso o desconhecido passa)
_REPERGUNTA_DIAS = 7            # "não sei" do Gemini é perguntado de novo depois disso (banda que cresceu)
_GEMINI_MAX_HORA = 200          # teto de perguntas por hora (≈ R$ 1,50/h no pior caso, estimativa): busca maluca não vira conta
_GEMINI_PAUSA_SEG = 600         # Gemini respondeu erro (sem crédito, fora do ar): 10 min sem perguntar
_GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent'

# ── SEMENTE (17/10/26): os artistas mais pedidos em bar. Conferida 1 a 1 com o Gemini antes de subir. ──────────
_SEMENTE_TXT = {
    'rock': '''AC/DC|Metallica|Guns N' Roses|The Rolling Stones|Led Zeppelin|Pink Floyd|Nirvana|Pearl Jam|Foo Fighters|
        Iron Maiden|Black Sabbath|Ozzy Osbourne|Kiss|Aerosmith|Scorpions|Deep Purple|Van Halen|Def Leppard|
        Mötley Crüe|The Doors|Creedence Clearwater Revival|Lynyrd Skynyrd|ZZ Top|Eagles|Dire Straits|The Who|
        Jimi Hendrix|Green Day|The Offspring|Blink-182|System of a Down|Slipknot|Rammstein|Evanescence|
        Arctic Monkeys|Oasis|Radiohead|Muse|Kings of Leon|Audioslave|Soundgarden|Alice in Chains|Stone Temple Pilots|
        Creed|Nickelback|3 Doors Down|Bruce Springsteen|Motörhead|Judas Priest|Megadeth|Pantera|Ramones|Sex Pistols|
        The Clash|The Cure|Journey|Survivor|Europe|Whitesnake|Twisted Sister|Skid Row|Ghost|Avenged Sevenfold|
        Papa Roach|Limp Bizkit|Korn|Disturbed|Three Days Grace|Shinedown|Steppenwolf|Chuck Berry|The Black Keys|
        The White Stripes|Queens of the Stone Age|Rush|Legião Urbana|Titãs|Barão Vermelho|Capital Inicial|
        Engenheiros do Hawaii|Pitty|CPM 22|NX Zero|Detonautas Roque Clube|Ira!|Ultraje a Rigor|Biquini Cavadão|RPM|
        Sepultura|Angra|Matanza|Ratos de Porão|Dead Fish|Fresno|Scalene|Nenhum de Nós|Supercombo|Black Pantera|
        Krisiun|Velhas Virgens|Tihuana|Plebe Rude|Camisa de Vênus|Lobão|Forfun|Hateen|Dinho Ouro Preto''',
    'sertanejo': '''Gusttavo Lima|Marília Mendonça|Henrique & Juliano|Jorge & Mateus|Zé Neto & Cristiano|Maiara & Maraisa|
        Simone Mendes|Simone & Simaria|Chitãozinho & Xororó|Zezé Di Camargo & Luciano|Leonardo|Bruno & Marrone|
        Matheus & Kauan|Israel & Rodolffo|Hugo & Guilherme|Lauana Prado|Murilo Huff|Gustavo Mioto|
        Diego & Victor Hugo|Felipe Araújo|Cristiano Araújo|Naiara Azevedo|Marcos & Belutti|Fernando & Sorocaba|
        Paula Fernandes|Victor & Leo|César Menotti & Fabiano|Guilherme & Benuto|Clayton & Romário|Us Agroboy|
        Bruninho & Davi|Henrique & Diego|Eduardo Costa|Rionegro & Solimões|Milionário & José Rico|
        Tião Carreiro & Pardinho|Teodoro & Sampaio|João Neto & Frederico|Edson & Hudson|Munhoz & Mariano|
        Mato Grosso & Mathias|João Paulo & Daniel|Chrystian & Ralf|Gian & Giovani|Rick & Renner|Thaeme & Thiago|
        Henrique Casttro|Hugo Henrique|Guilherme & Santiago|Day & Lara|Antony & Gabriel|Léo Magalhães|
        Humberto & Ronaldo|Jads & Jadson''',
    'pagode': '''Thiaguinho|Menos é Mais|Péricles|Ferrugem|Dilsinho|Sorriso Maroto|Turma do Pagode|Exaltasamba|Raça Negra|
        Belo|Pixote|Revelação|Só Pra Contrariar|Molejo|Atitude 67|Fundo de Quintal|Pique Novo|Tiee|Suel|Vou Zuar|
        Di Propósito|Kamisa 10|Os Travessos|Art Popular|Clareou|Grupo Bom Gosto|Negritude Júnior|Soweto|Katinguelê|
        Thiago Soares|Rodriguinho|Marvvila''',
    'pop': '''Dua Lipa|Lady Gaga|Katy Perry|Justin Bieber|Ariana Grande|Billie Eilish|Michael Jackson|Madonna|Adele|
        Sam Smith|Sabrina Carpenter|Britney Spears|Backstreet Boys|Jão|Manu Gavassi|Sandy & Junior|Kelly Key|
        Wanessa Camargo|Lana Del Rey|ABBA|Bee Gees|Whitney Houston|Mariah Carey|Celine Dion|Justin Timberlake|
        Selena Gomez|Demi Lovato|One Direction|Shawn Mendes|Charlie Puth|Camila Cabello|SZA|Lizzo|Spice Girls|
        Cyndi Lauper|George Michael|Wham!|Rick Astley|Enrique Iglesias|Ricky Martin|Luis Fonsi''',
    'funk': '''MC Kevinho|MC Ryan SP|MC Hariel|MC IG|MC Livinho|MC Pedrinho|MC Don Juan|MC Kekel|MC Daniel|Kevin o Chris|
        MC Lan|MC Kevin|MC Marks|MC Loma e as Gêmeas Lacração|MC Guimê|MC Mirella|Valesca Popozuda|
        Tati Quebra Barraco|MC Fioti|MC Zaac|MC Davi|Jerry Smith|MC Paiva|MC Tuto|MC GW|MC Brinquedo|
        MC Neguinho do Kaxeta|MC Rodolfinho|MC João|MC Bin Laden|MC Menor da VG|MC Joãozinho VT|MC Luuky|MC PH|
        MC Kadu|MC Lipi|MC Leozinho ZS|Bonde do Tigrão|Furacão 2000|Os Hawaianos|MC Créu|MC Catra''',
    'eletronica': '''Alok|Vintage Culture|Avicii|Tiësto|Martin Garrix|Marshmello|Kygo|Dimitri Vegas & Like Mike|Skrillex|
        Swedish House Mafia|Bhaskar|Chemical Surfers|KVSH|Armin van Buuren|Hardwell|Steve Aoki|Deadmau5|Fatboy Slim|
        Bob Sinclar|Kaskade|Illenium|Cat Dealers|Dubdogz|Mochakk|Felguk''',
    'rap': '''Matuê|Racionais MC's|Filipe Ret|L7nnon|Djonga|Hungria Hip Hop|Sabotage|MV Bill|Veigh|Kayblack|Xamã|
        Gabriel o Pensador|Eminem|Kendrick Lamar|50 Cent|Snoop Dogg|Travis Scott|2Pac|The Notorious B.I.G.|Jay-Z|
        Dr. Dre|Ice Cube|Cypress Hill|Wu-Tang Clan|Facção Central|Rashid|Kamaitachi|Major RD|Black Alien|Costa Gold|
        Haikaiss|Mano Brown''',
    'mpb': '''Djavan|Caetano Veloso|Chico Buarque|Milton Nascimento|Marisa Monte|Elis Regina|Tom Jobim|Vinicius de Moraes|
        Gal Costa|Maria Bethânia|Ivan Lins|João Gilberto|Belchior|Fagner|Zeca Baleiro|Lenine|Oswaldo Montenegro|
        Gonzaguinha|Toquinho|Tulipa Ruiz|Rubel|Tribalistas|Jorge Vercillo|Silva|Ana Vilela|Adriana Calcanhotto|
        Arnaldo Antunes|Paulinho Moska|Roberta Sá|Maria Rita|Céu|Mart'nália''',
    # 22/10/26: Blues virou estilo (antes ia junto com o rock). Todo blues CONTINUA valendo no bar de rock
    # (_BLUES_TAMBEM_ROCK): o bar só de rock não passa a barrar B.B. King. O bar só de blues fica só com o blues.
    'blues': '''B.B. King|Stevie Ray Vaughan|Muddy Waters|John Lee Hooker|Buddy Guy|Etta James|Albert King|Freddie King|
        Howlin' Wolf|Robert Johnson|Willie Dixon|Elmore James|Albert Collins|Robert Cray|Joe Bonamassa|Gary Clark Jr.|
        Gary Moore|Eric Clapton|Cream|The Allman Brothers Band|The Black Keys|ZZ Top|Kenny Wayne Shepherd|Jonny Lang|
        Beth Hart|Tedeschi Trucks Band|Susan Tedeschi|Derek Trucks|Keb' Mo'|Taj Mahal|Christone Kingfish Ingram|
        Marcus King|Larkin Poe|Seasick Steve|Rory Gallagher|John Mayall|Johnny Winter|Bonnie Raitt|Walter Trout|
        Eric Gales|Samantha Fish|Popa Chubby|Celso Blues Boy|Blues Etílicos|Nuno Mindelis|André Christovam|Big Gilson|
        Baseado em Blues|Igor Prado|Flávio Guimarães|Irmandade do Blues''',
    'reggae': '''Bob Marley|Natiruts|Cidade Negra|Planta e Raiz|Maneva|Ponto de Equilíbrio|Alma Djem|Tribo de Jah|Chimarruts|
        Edson Gomes|UB40|Damian Marley|Peter Tosh|Jimmy Cliff|Mato Seco|Adão Negro''',
    'outros': '''Xand Avião|Bell Marques|Chiclete com Banana|Asa de Águia|Banda Eva|Xuxa|Aline Barros|Fernandinho|
        Gabriela Rocha|Anderson Freire|Bruna Karla|Thalles Roberto|Harpa Cristã|Mastruz com Leite|Calcinha Preta|
        Aviões do Forró|Garota Safada|Falamansa|Luiz Gonzaga|Dominguinhos|Frank Sinatra|Louis Armstrong|Ray Charles|
        Galinha Pintadinha|Patati Patatá|Mundo Bita|Palavra Cantada|Eric Land|Raí Saia Rodada|Felipe Amorim''',
}
# Artista de vários estilos (conta como todos): pop rock passa no bar de rock, Anitta não.
_SEMENTE_MULTI_TXT = '''Bon Jovi=rock pop|Queen=rock pop|The Beatles=rock pop|Red Hot Chili Peppers=rock funk|Linkin Park=rock rap|
    U2=rock pop|The Killers=rock pop|The Police=rock pop|Toto=rock pop|Billy Idol=rock pop|
    Bring Me the Horizon=rock pop eletronica|Rage Against the Machine=rock rap|Elvis Presley=rock outros|
    Johnny Cash=rock outros|Charlie Brown Jr.=rock rap|Raimundos=rock outros|Os Paralamas do Sucesso=rock reggae|
    Mamonas Assassinas=rock outros|Raul Seixas=rock outros|Os Mutantes=rock outros|Cazuza=rock mpb|
    Blitz=rock pop|Pato Fu=rock pop|Frejat=rock mpb|Luan Santana=sertanejo pop|Ana Castela=sertanejo outros|
    Luan Pereira=sertanejo outros|Michel Teló=sertanejo pop|Lucas Lucco=sertanejo pop|Zé Felipe=sertanejo pop|
    Zeca Pagodinho=pagode mpb|Mumuzinho=pagode mpb|Xande de Pilares=pagode mpb|Arlindo Cruz=pagode mpb|
    Diogo Nogueira=pagode mpb|Jorge Aragão=pagode mpb|Harmonia do Samba=pagode outros|Alcione=pagode mpb|
    Beth Carvalho=pagode mpb|Martinho da Vila=pagode mpb|Nego do Borel=pop funk|Orochi=funk rap|Projota=pop rap|
    Baco Exu do Blues=rap mpb|Drake=pop rap|Kanye West=pop rap|Chefin=funk rap|Daft Punk=pop eletronica|
    The Prodigy=rock eletronica|Zedd=pop eletronica|Taylor Swift=pop outros|Ed Sheeran=pop outros|
    The Weeknd=pop outros|Bruno Mars=pop outros|Rihanna=pop outros|Beyoncé=pop outros|Shakira=pop outros|
    Harry Styles=rock pop|Olivia Rodrigo=rock pop|Miley Cyrus=rock pop|IZA=pop outros|Vitor Kley=rock pop|
    Melim=pop reggae|Jonas Brothers=rock pop|Doja Cat=pop rap|Duran Duran=rock pop|Daddy Yankee=pop outros|
    Bad Bunny=pop outros|J Balvin=pop outros|Karol G=pop outros|Maluma=pop outros|Rosalía=pop outros|
    Becky G=pop outros|Marina Sena=pop outros|Pabllo Vittar=pop outros|Agnes Nunes=pop mpb|
    Gilberto Gil=mpb reggae|Tim Maia=mpb outros|Jorge Ben Jor=mpb outros|Ana Carolina=pop mpb|
    Zé Ramalho=mpb outros|Alceu Valença=mpb outros|Ney Matogrosso=pop mpb|Liniker=pop mpb|
    Elba Ramalho=mpb outros|Geraldo Azevedo=mpb outros|Cartola=pagode mpb|Adoniran Barbosa=pagode mpb|
    Novos Baianos=rock mpb outros|Moraes Moreira=mpb outros|Maria Gadú=pop mpb|Johnny Hooker=pop mpb|
    Chico César=mpb outros|Armandinho=pop reggae|Onze:20=pop reggae|Wesley Safadão=sertanejo outros|
    João Gomes=sertanejo outros|Tarcísio do Acordeon=sertanejo outros|Zé Vaqueiro=sertanejo outros|
    Barões da Pisadinha=sertanejo outros|Ivete Sangalo=pop outros|Claudia Leitte=pop outros|
    Léo Santana=funk outros|Nattan=sertanejo outros|Mari Fernandez=sertanejo outros|Anitta=pop funk outros|
    Ludmilla=pagode pop funk|Luísa Sonza=pop funk|Gloria Groove=pop funk rap|Pocah=pop funk|Lexa=pop funk|
    Pedro Sampaio=pop funk eletronica|Dennis DJ=funk eletronica|MC Cabelinho=funk rap|MC Poze do Rodo=funk rap|
    Oruam=funk rap|Emicida=rap mpb|Criolo=rap mpb|Marcelo D2=rock pagode rap|Planet Hemp=rock rap|
    O Rappa=rock rap mpb reggae|Post Malone=pop rap|Calvin Harris=pop eletronica|David Guetta=pop eletronica|
    The Chainsmokers=pop eletronica|Coldplay=rock pop|Maroon 5=rock pop|OneRepublic=rock pop|
    Imagine Dragons=rock pop|Skank=rock pop reggae|Jota Quest=rock pop|Kid Abelha=rock pop|
    Lulu Santos=rock pop mpb|Nando Reis=rock pop mpb|Rita Lee=rock pop mpb|Erasmo Carlos=rock mpb|
    Cássia Eller=rock mpb|Los Hermanos=rock mpb|Roberto Carlos=pop mpb outros|Seu Jorge=pagode mpb|
    Vanessa da Mata=pop mpb|Anavitória=pop mpb|Tiago Iorc=pop mpb|Elton John=rock pop|Billy Joel=rock pop|
    Phil Collins=rock pop|Genesis=rock pop|Fleetwood Mac=rock pop|Tears for Fears=rock pop|a-ha=rock pop|
    Roxette=rock pop|Bryan Adams=rock pop|P!nk=rock pop|Pink=rock pop|Avril Lavigne=rock pop|Paramore=rock pop|
    Shaggy=pop reggae|Sean Paul=pop reggae|Alexandre Pires=sertanejo pagode pop|Sandy=pop mpb'''
_PREFIXOS = ('grupo ', 'banda ', 'the ', 'os ', 'as ', 'mc ', 'dj ')
# separa 'A, B feat. C' (o texto já vem normalizado, sem pontuação); duo com & / 'e' fica junto
_SEP_CREDITO = re.compile(r'\s+(?:feat|ft|featuring|part|participacao|com|x|vs)\s+')


def norm(s):
    """Igual ao _yt_norm do pubshow.py: minúsculo, sem acento, & = e, só letra e número."""
    s = unicodedata.normalize('NFKD', (s or '').replace('&', ' e ')).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', s).split())


def _sem_prefixo(k):
    for p in _PREFIXOS:
        if k.startswith(p) and len(k) > len(p) + 2:
            return k[len(p):]
    return k


def _montar_semente():
    out = {}
    for est, txt in _SEMENTE_TXT.items():
        for nome in txt.split('|'):
            k = norm(nome)
            if k:
                out.setdefault(k, set()).add(est)
    for item in _SEMENTE_MULTI_TXT.split('|'):
        nome, _, ests = item.strip().rpartition('=')
        k = norm(nome)
        if k:
            out[k] = {x for x in ests.split() if x in ESTILOS}
    for v in out.values():           # 22/10/26: blues também vale no bar de rock (como era antes do botão Blues)
        if 'blues' in v:
            v.add('rock')
    # 'ACDC' (canal VEVO) acha 'AC/DC'. Sem prefixo ('MC Daniel' → 'daniel', 'Banda Eva' → 'eva') NÃO: vira outro
    # artista ou nome de música (verificador, 17/10/26). Só o nome inteiro conta.
    for k in list(out):
        if ' ' in k:
            out.setdefault(k.replace(' ', ''), out[k])
    return out


SEMENTE = _montar_semente()


# ── O estilo do BAR ──────────────────────────────────────────────────────────
# Os 5 botões grandes do painel ("Aqui toca"): os MESMOS 5 do "Estilo do bar" do painel novo (Variado = Tudo) e as
# mesmas famílias do acervo. Gravam o generos_jukebox: vale pra lista do bar e pro YouTube.
BOTOES = {
    'tudo':      {'nome': 'Tudo',      'emoji': '🎶', 'cor': '#a855f7', 'temas': None},
    'rock':      {'nome': 'Rock',      'emoji': '🎸', 'cor': '#ef4444', 'temas': ['rock', 'punk', 'show_rock']},
    'sertanejo': {'nome': 'Sertanejo', 'emoji': '🤠', 'cor': '#eab308', 'temas': ['sertanejo', 'show_sertanejo', 'country']},
    'pagode':    {'nome': 'Pagode',    'emoji': '🥁', 'cor': '#22c55e', 'temas': ['pagode', 'show_pagode']},
    'pop':       {'nome': 'Pop',       'emoji': '🎤', 'cor': '#ec4899', 'temas': ['pop']},
}


def ligado():
    return (os.environ.get('PUBSHOW_ESTILO_YT', '1') or '1').strip() != '0'


def _campo(b, k):
    try:
        return b.get(k) if hasattr(b, 'get') else (b[k] if k in b.keys() else None)
    except Exception:
        return None


def garantir_coluna(conn):
    """estilo_yt = 1 quando o dono TOCOU num estilo (botão do painel). Os gêneros marcados antes nos Ajustes seguem
    valendo só pra lista do bar: nada muda no YouTube sem o dono escolher (verificador, 17/10/26)."""
    try:
        conn.execute('ALTER TABLE pubshow_businesses ADD COLUMN estilo_yt INTEGER DEFAULT 0')
        conn.commit()
    except Exception:
        pass    # já existe


def _marcados(b):
    """Os estilos dos gêneros marcados (generos_jukebox), ligados ou não no YouTube."""
    try:
        g = json.loads(_campo(b, 'generos_jukebox') or 'null')
    except Exception:
        return None
    if not isinstance(g, list):
        return None
    s = {CANAL_ESTILO[x] for x in g if isinstance(x, str) and x in CANAL_ESTILO}
    return s or None


def estilos_do_bar(b):
    """{'rock'} / {'rock','pop'} — o que o YouTube deste bar aceita; None = tudo liberado (não escolheu, Tudo, ou
    PUBSHOW_ESTILO_YT=0)."""
    if not b or not ligado() or not _campo(b, 'estilo_yt'):
        return None
    return _marcados(b)


def botao_aceso(b):
    """Qual dos 5 botões acende: 'tudo', 'rock'... ou '' (mistura marcada antes, ou gêneros que ainda não valem no YouTube)."""
    m = _marcados(b)
    if not m:
        return 'tudo'
    if not _campo(b, 'estilo_yt'):
        return ''
    return next((k for k in BOTOES if k != 'tudo' and {k} == m), '')


def texto_aqui_toca(b):
    """A frase curta embaixo dos botões (sem palavra técnica)."""
    m = _marcados(b)
    if not m:
        return 'O cliente pede qualquer música.'
    if not _campo(b, 'estilo_yt'):
        return 'Toque no estilo do bar pra valer também no YouTube.'
    if not ligado():
        return f'O cliente só acha {nomes_txt(m)} na lista do bar (o filtro do YouTube está desligado agora).'
    return f'O cliente só acha {nomes_txt(m)}, até no YouTube.'


def nomes_txt(estilos):
    """{'rock','pop'} → 'Rock e Pop' (na ordem de ESTILOS)."""
    n = [NOMES[e] for e in ESTILOS if e in (estilos or ())]
    if len(n) <= 1:
        return ''.join(n)
    return ', '.join(n[:-1]) + ' e ' + n[-1]


def _emoji(estilos):
    ests = [e for e in ESTILOS if e in (estilos or ())]
    return EMOJI[ests[0]] if len(ests) == 1 else '🎶'


def msg_fora(b, estilos):
    """Texto curto pro cliente (17/10/26, Diogo: "tudo fácil, como pra criança de 3 anos")."""
    return f'{_emoji(estilos)} Aqui só toca {nomes_txt(estilos)}! Escolhe outra música.'


def aviso_fora(estilos):
    """O que o celular precisa pra montar o aviso grande quando a busca inteira é de outro estilo."""
    return {'msg': f'{_emoji(estilos)} Aqui só toca {nomes_txt(estilos)}!', 'estilo_emoji': _emoji(estilos),
            'estilo_nome': nomes_txt(estilos)}


def passa(estilos_artista, estilos_bar):
    """Bar sem estilo: tudo passa. Artista desconhecido (None ou vazio): passa. Senão: algum estilo em comum."""
    if not estilos_bar or not estilos_artista:
        return True
    return bool(set(estilos_artista) & set(estilos_bar))


def estilo_da_categoria(categoria):
    """Categoria do acervo CURADO → estilo ('show_rock' → 'rock'); fora de música → None."""
    return CANAL_ESTILO.get(categoria or '')


# ── A tabela (cache permanente por artista e por vídeo) ─────────────────────
_TABELA_OK = {'ok': False}


def _tabela(conn):
    if _TABELA_OK['ok']:
        return
    conn.execute('''CREATE TABLE IF NOT EXISTS pubshow_artista_estilo (
                        chave         TEXT PRIMARY KEY,      -- artista normalizado, ou 'v:<youtube_id>'
                        nome          TEXT,                  -- o artista como veio (do Gemini ou do título)
                        estilos       TEXT NOT NULL DEFAULT '',   -- 'rock,pop'; '' = não sei
                        fonte         TEXT,                  -- 'gemini' | 'semente' | 'manual'
                        atualizado_em TEXT DEFAULT (datetime('now')))''')
    # 22/10/26: nasceu o estilo 'blues' (antes o Gemini punha blues em 'rock'). 1 vez só: o que já estava guardado de
    # um artista da semente de blues (pelo nome do artista ou do vídeo) ganha o blues; 'manual' fica como está.
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS pubshow_meta (chave TEXT PRIMARY KEY, valor TEXT)')
        if not conn.execute("SELECT 1 FROM pubshow_meta WHERE chave='estilo_blues_v1'").fetchone():
            n = 0
            for r in conn.execute("SELECT chave, nome, estilos FROM pubshow_artista_estilo "
                                  "WHERE COALESCE(fonte, '') <> 'manual'").fetchall():
                sem = SEMENTE.get(norm(r[1])) or (SEMENTE.get(r[0]) if not str(r[0]).startswith('v:') else None)
                if sem and 'blues' in sem:
                    novo = (_ler(r[2]) or set()) | sem
                    conn.execute('UPDATE pubshow_artista_estilo SET estilos=? WHERE chave=?',
                                 (','.join(e for e in ESTILOS if e in novo), r[0]))
                    n += 1
            conn.execute("INSERT OR REPLACE INTO pubshow_meta (chave, valor) VALUES ('estilo_blues_v1', datetime('now'))")
            if n:
                log.info('[PUBSHOW estilo] blues: %d artista(s)/vídeo(s) guardados ganharam o estilo blues', n)
    except Exception as e:
        log.warning('[PUBSHOW estilo] ajuste do blues falhou: %s', e)
    conn.commit()
    _TABELA_OK['ok'] = True


def _ler(estilos_txt):
    s = {e for e in (estilos_txt or '').split(',') if e in ESTILOS}
    return s or None


def _virgulas(nome):
    """Normaliza mantendo a vírgula do crédito ('Anitta, Mc Zaac & Maejor' → 'anitta, mc zaac e maejor')."""
    return ', '.join(norm(x) for x in (nome or '').split(',') if norm(x))


def _partes(nome):
    """'MC Ryan SP x MC Hariel' → ['mc ryan sp', 'mc hariel']; 'Anitta, Mc Zaac & Maejor' → ['anitta', 'mc zaac e maejor']."""
    out = []
    for pedaco in _virgulas(nome).split(', '):
        out += [p.strip() for p in _SEP_CREDITO.split(f' {pedaco} ') if p.strip()]
    return out


def _variantes(nome):
    """Chaves pra procurar um crédito no banco: inteiro, sem 'Grupo/Banda/MC/The', e cada artista do 'feat.'."""
    k = norm(nome)
    if not k:
        return []
    out = [k, _sem_prefixo(k)]
    for p in _partes(nome):
        out += [p, _sem_prefixo(p)]
    return list(dict.fromkeys(x for x in out if len(x) >= 2))


def _do_nome(nome, banco):
    """Estilos conhecidos de um crédito (banco > semente), juntando os de cada artista do 'feat.'. None = não sei."""
    k = norm(nome)
    if not k:
        return None

    def achar(x):
        return (banco.get(x) or SEMENTE.get(x) or banco.get(_sem_prefixo(x)) or SEMENTE.get(_sem_prefixo(x))
                or SEMENTE.get(x.replace(' ', '')))

    inteiro = achar(k)
    if inteiro:
        return set(inteiro)
    junto = set()
    partes = _partes(nome)
    if len(partes) >= 2:
        for p in partes:
            junto |= set(achar(p) or ())
    if not junto:            # último recurso: 'Traia Véia e Gusttavo Lima' (a dupla conhecida já achou inteira lá em cima)
        for p in partes or [k]:
            pedacos = [x.strip() for x in re.split(r'\s+e\s+', p) if x.strip()]
            if len(pedacos) >= 2:
                for x in pedacos:
                    if ' ' in x:         # 'gusttavo lima' sim; 'maria', 'belo' sozinhos não
                        junto |= set(achar(x) or ())
    return junto or None


def _banco_ler(chaves):
    """{chave: (estilos ou None, fresco)} das chaves que existem na tabela."""
    chaves = [c for c in dict.fromkeys(chaves) if c]
    if not chaves:
        return {}
    out = {}
    conn = get_pubshow_db()
    try:
        _tabela(conn)
        for i in range(0, len(chaves), 400):
            parte = chaves[i:i + 400]
            for r in conn.execute(
                    f"""SELECT chave, estilos, atualizado_em >= datetime('now', '-{_REPERGUNTA_DIAS} days') AS fresco
                        FROM pubshow_artista_estilo WHERE chave IN ({','.join('?' * len(parte))})""",
                    tuple(parte)).fetchall():
                out[r['chave']] = (_ler(r['estilos']), bool(r['fresco']))
    except Exception as e:
        _TABELA_OK['ok'] = False         # banco trocado/restaurado com o processo no ar: recria na próxima
        log.warning('[PUBSHOW estilo] leitura falhou: %s', e)
    finally:
        conn.close()
    return out


def _banco_gravar(linhas):
    """linhas = [(chave, nome, {estilos} ou None, fonte)]. Nome de artista só grava quando sabe o estilo."""
    if not linhas:
        return
    conn = get_pubshow_db()
    try:
        _tabela(conn)
        for chave, nome, ests, fonte in linhas:
            txt = ','.join(e for e in ESTILOS if e in (ests or ()))
            if not chave or (not txt and not chave.startswith('v:')):
                continue
            conn.execute('''INSERT INTO pubshow_artista_estilo (chave, nome, estilos, fonte, atualizado_em)
                            VALUES (?,?,?,?, datetime('now'))
                            ON CONFLICT(chave) DO UPDATE SET nome=excluded.nome, estilos=excluded.estilos,
                                fonte=excluded.fonte, atualizado_em=excluded.atualizado_em
                            WHERE pubshow_artista_estilo.fonte <> 'manual' ''',
                         (chave[:120], (nome or '')[:80], txt, fonte))
        conn.commit()
    except Exception as e:
        _TABELA_OK['ok'] = False
        log.warning('[PUBSHOW estilo] gravação falhou: %s', e)
    finally:
        conn.close()


# ── Gemini: 1 pergunta curta, num fio separado, com teto ─────────────────────
_LOCK = threading.Lock()
_EM_VOO = {}                    # youtube_id → Event (a mesma pergunta não sai 2x ao mesmo tempo)
_RESP = {}                      # youtube_id → ({estilos} ou None, artista): o que o Gemini respondeu agora há pouco
_RESP_MAX = 3000
_VISTO = {}                     # youtube_id → o que a BUSCA concluiu (título do YouTube): o pedido usa isso, não o título
_VISTO_MAX = 5000               #   que vem do celular (verificador B1, 17/10/26). Some no deploy: aí vale o título.
_GEMINI = {'pausa_ate': 0.0, 'chamadas': deque()}


def _gemini_disponivel():
    if not os.environ.get('GEMINI_API_KEY'):
        return False
    agora = time.time()
    if agora < _GEMINI['pausa_ate']:
        return False
    ch = _GEMINI['chamadas']
    while ch and agora - ch[0] > 3600:
        ch.popleft()
    if len(ch) >= _GEMINI_MAX_HORA:
        return False
    return True


def _prompt(itens):
    linhas = {}
    for it in itens:
        txt = it.get('titulo') or ''
        if it.get('canal'):
            txt += f" | canal: {it['canal']}"
        if _palpites(it):
            txt += f" | artista provável: {_palpites(it)[0]}"
        linhas[it['id']] = txt[:200]
    return ('Vídeos do YouTube pedidos num bar no Brasil. Para cada um, diga o ARTISTA principal e o ESTILO musical dele.\n'
            'Estilos: rock, sertanejo, pagode, pop, funk, eletronica, rap, mpb, reggae, blues, outros.\n'
            '- rock inclui metal, punk, hard rock, rock nacional, rock alternativo e pop rock\n'
            '- blues inclui blues rock, blues elétrico e blues nacional\n'
            '- sertanejo inclui sertanejo universitário, arrocha e country; forró, piseiro, axé, gospel e infantil = outros\n'
            '- pagode inclui samba; funk = funk brasileiro; rap inclui trap e hip hop; eletronica = DJ, EDM, house\n'
            '- até 3 estilos, só os principais do artista\n'
            '- não reconheceu o artista com segurança (banda pequena, vídeo que não é música)? estilos = []\n'
            'Responda só JSON: {"<id>": {"artista": "nome", "estilos": ["..."]}}\n'
            'Vídeos: ' + json.dumps(linhas, ensure_ascii=False))


def _gemini_chamar(itens):
    """Texto JSON da resposta do Gemini (ou exceção). Separado pra os testes trocarem por respostas gravadas."""
    modelo = os.environ.get('PUBSHOW_ESTILO_MODELO', 'gemini-2.5-flash')
    corpo = {'contents': [{'role': 'user', 'parts': [{'text': _prompt(itens)}]}],
             'generationConfig': {'temperature': 0, 'maxOutputTokens': 1200, 'responseMimeType': 'application/json',
                                  'thinkingConfig': {'thinkingBudget': 0}}}
    r = requests.post(_GEMINI_URL.format(m=modelo), params={'key': os.environ.get('GEMINI_API_KEY', '')},
                      json=corpo, timeout=8)
    if r.status_code != 200:
        raise RuntimeError(f'gemini http {r.status_code}')
    return r.json()['candidates'][0]['content']['parts'][0]['text']


def _rodar(itens, ev):
    try:
        _GEMINI['chamadas'].append(time.time())
        try:
            txt = _gemini_chamar(itens)
            m = re.search(r'\{.*\}', txt or '', re.S)
            dados = json.loads(m.group(0) if m else txt)
            if not isinstance(dados, dict):
                raise ValueError('resposta não é objeto')
        except Exception as e:
            _GEMINI['pausa_ate'] = time.time() + _GEMINI_PAUSA_SEG
            log.warning('[PUBSHOW estilo] Gemini falhou (%s): pausa de %d min, desconhecido passa',
                        e, _GEMINI_PAUSA_SEG // 60)
            return
        linhas = []
        for it in itens:
            d = dados.get(it['id'])
            d = d if isinstance(d, dict) else {}
            ests = d.get('estilos') if isinstance(d.get('estilos'), list) else []
            ests = {e for e in (_SINONIMOS.get(x, x) for x in (str(y).strip().lower() for y in ests[:3]))
                    if e in ESTILOS} or None
            if ests and 'blues' in ests:     # 22/10/26: blues também vale no bar de rock
                ests.add('rock')
            artista = str(d.get('artista') or '').strip()[:80]
            with _LOCK:
                _RESP[it['id']] = (ests, artista)
                if len(_RESP) > _RESP_MAX:
                    for k in list(_RESP)[:_RESP_MAX // 4]:
                        _RESP.pop(k, None)
            linhas.append(('v:' + it['id'], artista or ''.join(_palpites(it)[:1]), ests, 'gemini'))
            # o estilo fica com o NOME que o Gemini deu, nunca com o palpite (canal 'Various Artists - Topic',
            # título editado): senão um erro vale pra todos os bares (verificador, 17/10/26)
            k = norm(artista)
            if ests and k and k not in SEMENTE and k not in _GENERICOS and len(k) >= 3:
                linhas.append((k, artista, ests, 'gemini'))
        _banco_gravar(linhas)
    finally:
        with _LOCK:
            for it in itens:
                if _EM_VOO.get(it['id']) is ev:
                    _EM_VOO.pop(it['id'], None)
        ev.set()


# ── A função que o pubshow.py usa ────────────────────────────────────────────
def _palpites(it):
    """Os nomes de artista que já se sabe do vídeo (do título limpo, do canal oficial), sem repetir."""
    ps = it.get('palpites')
    if ps is None:
        ps = [it.get('palpite')]
    return [p for p in dict.fromkeys(str(x).strip()[:80] for x in ps if x) if p]


_SEP_TITULO = re.compile(r'\s+(?:-{1,2}|–|—|\||·|~+)\s+')


_RUIDO_TITULO = re.compile(r'[\(\[\{][^\)\]\}]*[\)\]\}]|(?i:\b(?:letra|lyrics?|legendad[oa]|tradu[cç][aã]o|oficial|official|'
                           r'clipe|video|vídeo|ao vivo|audio|áudio|hd|4k)\b)')
_GENERICOS = {'various artists', 'varios artistas', 'artistas variados', 'diversos', 'diversos artistas', 'vevo', 'topic',
              'desconhecido', 'unknown', 'musica', 'lyrics', 'letras'}


def _talvez(it):
    """Pedaços do título 'A - B (Letra)' (um deles costuma ser o artista). Só valem se baterem com artista CONHECIDO,
    e só quando o Gemini não respondeu (fora, sem crédito, lento, ou nem perguntado): com ele no ar,
    'Beastie Boys - Sabotage' não vira o rapper Sabotage (verificador, 17/10/26)."""
    pedacos = [' '.join(_RUIDO_TITULO.sub(' ', x).split()).strip(' -') for x in _SEP_TITULO.split(it.get('titulo') or '')]
    return [x for x in pedacos[:3] if x] if len(pedacos) > 1 else []


def classificar(itens, espera=ESPERA_BUSCA):
    """itens = [{'id': youtube_id, 'titulo': título do vídeo, 'canal': canal, 'palpites': [artista que já se sabe]}].
    Devolve {youtube_id: {estilos} ou None (não sei → passa)}. espera = segundos que espera o Gemini;
    espera=None: nem pergunta (pedido e listas: ali o título vem do celular e não pode ensinar nada ao cache)."""
    itens = [it for it in itens if it.get('id')]
    if not itens:
        return {}
    chaves = []
    for it in itens:
        chaves.append('v:' + it['id'])
        for p in _palpites(it) + _talvez(it):
            chaves += _variantes(p)
    banco = _banco_ler(chaves)
    banco_nomes = {k: v[0] for k, v in banco.items() if v[0] and not k.startswith('v:')}
    out, faltam, respondidos = {}, [], set()
    for it in itens:
        with _LOCK:
            vi = _VISTO.get(it['id'])
        if vi:                                   # a busca já viu este vídeo e sabe o estilo ("não sei" segue aprendendo)
            out[it['id']] = vi
            continue
        v = banco.get('v:' + it['id'])
        if v and (v[0] or v[1]):                 # já perguntado (sabe, ou "não sei" de menos de 7 dias)
            out[it['id']] = v[0]
            continue
        conhecido = set()
        for p in _palpites(it):
            conhecido |= _do_nome(p, banco_nomes) or set()
        if conhecido:
            out[it['id']] = conhecido
            continue
        with _LOCK:
            r = _RESP.get(it['id'])
        if r is not None:
            out[it['id']] = r[0]
            continue
        out[it['id']] = None
        faltam.append(it)
    if faltam and espera is not None:
        esperar, novos = [], []
        with _LOCK:
            for it in faltam:
                ev = _EM_VOO.get(it['id'])
                if ev:
                    esperar.append(ev)
                else:
                    novos.append(it)
            novos = novos[:12]                   # mais de 12 de uma vez: o resto fica pra próxima
            if novos and _gemini_disponivel():
                ev = threading.Event()
                try:
                    threading.Thread(target=_rodar, args=(novos, ev), daemon=True).start()
                    for it in novos:
                        _EM_VOO[it['id']] = ev
                    esperar.append(ev)
                except Exception as e:           # sem fio (memória): ninguém fica esperando à toa
                    log.warning('[PUBSHOW estilo] não abriu o fio do Gemini: %s', e)
        fim = time.time() + max(0.0, espera)
        for ev in esperar:
            resto = fim - time.time()
            if resto <= 0:
                break
            ev.wait(resto)
        with _LOCK:
            for it in faltam:
                r = _RESP.get(it['id'])
                if r is not None:
                    out[it['id']] = r[0]
                    respondidos.add(it['id'])
    for it in faltam:                            # o Gemini não respondeu: pedaço do título que é artista conhecido
        if it['id'] in respondidos:
            continue
        conhecido = set()
        for p in _talvez(it):
            conhecido |= _do_nome(p, banco_nomes) or set()
        if conhecido:
            out[it['id']] = conhecido
    if espera is not None:                       # só a busca (título do próprio YouTube) ensina o _VISTO
        with _LOCK:
            for it in itens:
                if out.get(it['id']):
                    _VISTO.pop(it['id'], None)
                    _VISTO[it['id']] = out[it['id']]
            if len(_VISTO) > _VISTO_MAX:
                for k in list(_VISTO)[:_VISTO_MAX // 4]:
                    _VISTO.pop(k, None)
    return out


def artista_do_titulo(titulo):
    """'Back In Black · AC/DC' (título limpo da busca) → 'AC/DC'; sem o ' · ' → ''."""
    t = (titulo or '').strip()
    return t.rsplit(' · ', 1)[1].strip() if ' · ' in t else ''
