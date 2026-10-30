# -*- coding: utf-8 -*-
"""pubshow_karaoke.py — 🎤 Modo karaokê do PubShow (29/10/26, V1 de sexta, GRÁTIS pro cliente).

Só funções PURAS (sem banco, sem Flask, sem rede): o pubshow.py busca as linhas e chama estas contas. Assim o
teste roda sem servidor e o pubshow.py não cresce com regra de karaokê espalhada.

O que mora aqui:
  - ligado(b): o interruptor do bar (coluna modo_karaoke);
  - teto() / basta(): a cota de busca própria do karaokê (env PUBSHOW_KARAOKE_TETO, padrão 8 por dia; COTA_MAX 60 é a reserva do Garage) e o "basta"
    do local (env PUBSHOW_KARAOKE_BASTA, padrão 3);
  - termo_busca(q) e OK_RE: a busca só traz versão com letra;
  - nome_curto(nome): o apelido da TV, até 14 letras;
  - ranking(linhas) / rei(rank): "Rei da noite" (agrupa por celular + nome; pulada, escondida e "sem ranking" não contam);
  - acabou(...) / bloco(...): o bloco 'karaoke' do /api/status e das telas do celular;
  - acervo_ids() / acervo_validos(): o acervo karaokê curado (módulo pubshow_karaoke_acervo.py).

Regras que não mudam (ESTADO_MODO_KARAOKE.md): no celular nunca o nome da plataforma de vídeo nem gíria pro WhatsApp; na TV nada por cima do vídeo;
a cobrança de R$ 2 NÃO existe no V1 (o karaokê é grátis).
"""
import os
import re
import unicodedata
from datetime import datetime, timedelta

TETO_PADRAO = 8             # buscas novas no Google por dia, por bar em modo karaokê (crítica de 29/10: 8 na sexta)
COTA_MAX_PADRAO = 60        # com o balde do dia (todos os bares) em 60, o karaokê para de ir no Google: o resto é do Garage
BASTA_PADRAO = 3            # com 3 cards de karaokê no local, nem gasta o Google
CACHE_SEG = 5               # o bloco do /api/status fica 5 s em memória (1 worker: a TV chama a cada ~3 s)
NOME_MAX = 14               # "Teu nome na TV": primeiro nome ou apelido
REFRESH_DIAS = 20           # acervo: confere de novo no videos.list antes dos 30 dias da III.E.4.c
MUSICA_MIN = 4              # ~4 min por música (a mesma conta do /api/pedido-status)
PALCO_SEM_DUR_SEG = 6 * 60  # sem a duração do vídeo, "acabou" depois de 6 min
PALCO_FOLGA_SEG = 30        # com a duração: a duração + 30 s
FILA_MAX = 10               # nomes da fila que vão no bloco (a TV mostra os primeiros)
CONVIDADO = 'Convidado'     # nome escondido pelo dono ("🙈 Esconder nome")

# a busca acrescenta " karaoke" quando o termo não tem nenhuma destas palavras
_TEM_KARAOKE_RE = re.compile(r'\b(karaoke|videoke|playback)\b')
# só entra card com uma destas no título normalizado (minúsculo, sem acento)
OK_RE = re.compile(r'\b(karaoke|playback|com letra|videoke|sing ?along)\b')

# ── Textos do celular (PLANO_FINAL §6) ──────────────────────────────────────────────────────────────────────────────
MSG_NOME_VAZIO = 'Põe teu nome ou apelido pra gente chamar na TV.'
MSG_NOME_RUIM = 'Esse nome não dá pra pôr na TV. Tenta outro.'
MSG_JA_NA_FILA = 'Tua música já tá na fila. Cantou, pode pedir outra.'
MSG_SO_MUSICA = '🎤 Hoje é karaokê: escolhe a música que tu vai cantar.'
MSG_LOTOU = 'A busca de hoje lotou. Escolhe um dos clássicos:'
MSG_NADA = 'Não achei essa com letra. Tenta só o nome da música.'


def msg_nome_repetido(nome):
    """Dois 'João' na fila (crítica de 29/10): o 2º põe um apelido."""
    n = nome_curto(nome) or 'alguém'
    return f'{n} já tá na fila. Põe um apelido (ex.: {nome_curto(n, NOME_MAX - 2)} B).'   # sem 'um'/'uma' (29/10)


# ── Título limpo (crítica de tela de 29/10: "Evidências – Karaok…" na TV, "- Karaokê Playback" no celular) ─────────────
_MARCA = (r'karaok[eê]s?|videok[eê]|playback|com letra|letra|lyrics?|instrumental|sing ?along|vers[aã]o karaok[eê]|'
          r'karaok[eê] vers(ion|[aã]o)|vers(ion|[aã]o)|cover|\(g\)')
_MARCA_PAR_RE = re.compile(r'\s*[\(\[][^)\]]*\b(' + _MARCA + r')\b[^)\]]*[\)\]]', re.I)
_MARCA_RE = re.compile(r'(\s*[-–—|:]\s*)?\b(' + _MARCA + r')\b', re.I)
_SEP_RE = re.compile(r'\s*[-–—|]\s*')


def titulo_limpo(titulo, yid=None, por_id=None):
    """'Evidências – Karaokê Playback' → 'Evidências'; 'Chitãozinho & Xororó - Evidências (Com Letra)' →
    'Chitãozinho & Xororó · Evidências'. Vídeo do acervo curado: 'Música · Artista' dos campos do acervo. Nada sobra
    = o título como veio."""
    if yid and por_id and yid in por_id:
        a = por_id[yid]
        if a.get('musica') and a.get('artista'):
            return f"{a['musica']} · {a['artista']}"[:80]
    t = ' '.join(str(titulo or '').split())
    s = _MARCA_PAR_RE.sub(' ', t)
    s = _MARCA_RE.sub(' ', s)
    partes = [p.strip(' ·.:') for p in _SEP_RE.split(s.replace(' · ', ' - ')) if p.strip(' ·.:')]
    s = ' · '.join(' '.join(p.split()) for p in partes)
    return (s or t)[:80]


def _int_env(nome, padrao):
    try:
        v = int(str(os.environ.get(nome, '') or '').strip() or padrao)
        return max(0, v)
    except (TypeError, ValueError):
        return padrao


def teto():
    """Buscas novas no Google por dia num bar em modo karaokê (env PUBSHOW_KARAOKE_TETO, padrão 8)."""
    return _int_env('PUBSHOW_KARAOKE_TETO', TETO_PADRAO)


def cota_max():
    """Reserva do Garage (crítica de 29/10): com o balde do dia de TODOS os bares neste número, o karaokê não vai mais
    no Google (só acervo e cache). Env PUBSHOW_KARAOKE_COTA_MAX, padrão 60 (o teto do dia é 97)."""
    return _int_env('PUBSHOW_KARAOKE_COTA_MAX', COTA_MAX_PADRAO)


def bares():
    """A trava do piloto (crítica de 29/10): os bares que PODEM usar o modo karaokê (env PUBSHOW_KARAOKE_BARES, códigos
    ou ids separados por vírgula ou espaço). Vazia = o modo desligado em todo lugar: é o interruptor de emergência do
    Diogo no Railway (mudou a env, o servidor reinicia e o karaokê some de todos os bares)."""
    return {s.strip().upper() for s in re.split(r'[\s,;]+', os.environ.get('PUBSHOW_KARAOKE_BARES', '') or '')
            if s.strip()}


def permitido(b):
    """True se o bar está na lista do piloto (PUBSHOW_KARAOKE_BARES). Fora dela o card do painel nem aparece e as rotas
    /painel/karaoke* dão 403."""
    if not b:
        return False
    lista = bares()
    if not lista:
        return False
    try:
        keys = b.keys()
        code = str(b['code'] or '').strip().upper() if 'code' in keys else ''
        bid = str(b['id']) if 'id' in keys else ''
    except Exception:
        return False
    return bool((code and code in lista) or (bid and bid in lista))


def basta():
    """Quantos cards de karaokê no local já são a resposta (env PUBSHOW_KARAOKE_BASTA, padrão 3)."""
    return _int_env('PUBSHOW_KARAOKE_BASTA', BASTA_PADRAO)


def ligado(b):
    """True se o bar está em modo karaokê: na lista do piloto (permitido) E com o interruptor ligado. Aceita dict ou
    sqlite3.Row; banco sem a coluna = False."""
    if not b or not permitido(b):
        return False
    try:
        keys = b.keys()
    except Exception:
        return False
    if 'modo_karaoke' not in keys:
        return False
    try:
        return bool(int(b['modo_karaoke'] or 0))
    except (TypeError, ValueError):
        return False


def norm(s):
    """Minúsculo, sem acento, só letras e números (a mesma ideia do _yt_norm do pubshow.py)."""
    s = unicodedata.normalize('NFKD', (s or '').replace('&', ' e ')).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', s).split())


def termo_busca(q):
    """'evidencias' → 'evidencias karaoke'. Termo que já tem karaokê/videokê/playback fica como veio."""
    q = ' '.join((q or '').split())
    if not q or _TEM_KARAOKE_RE.search(norm(q)):
        return q
    return f'{q} karaoke'


def titulo_ok(titulo, yid=None, ids_acervo=()):
    """O card entra na busca do karaokê: título com karaokê/playback/com letra/videokê, ou vídeo do acervo curado."""
    return bool(OK_RE.search(norm(titulo))) or (yid is not None and yid in ids_acervo)


def nome_curto(nome, n=NOME_MAX):
    """O apelido da TV: espaços juntados, até n letras, cortando no último espaço (a regra do _nome_tv)."""
    nome = ' '.join((nome or '').split())
    if len(nome) <= n:
        return nome
    corte = nome[:n]
    if nome[n] != ' ' and ' ' in corte:
        corte = corte[:corte.rfind(' ')]
    return corte.strip()


def cid(ip_cliente):
    """O id do celular gravado no pedido ('IP#client_id' → 'client_id'; sem '#', o IP inteiro)."""
    ip = ip_cliente or ''
    return ip.split('#', 1)[1] if '#' in ip else ip


def nome_exibido(linha, votacao=False):
    """O nome que a TV e os celulares mostram: escondido pelo dono = 'Convidado'; no dia da votação = '' (a TV
    escreve "alguém", a regra que já existe)."""
    if votacao:
        return ''
    try:
        if int(linha.get('karaoke_nome_oculto') or 0):
            return CONVIDADO
    except (TypeError, ValueError):
        pass
    return nome_curto(linha.get('nome_cliente') or '') or CONVIDADO


def conta_no_ranking(linha):
    """Só música cantada (exibido) de karaokê, sem 'não quero ranking', sem Pular e sem nome escondido."""
    try:
        return (int(linha.get('karaoke') or 0) == 1 and (linha.get('status') or '') == 'exibido'
                and not int(linha.get('karaoke_sem_rank') or 0) and not int(linha.get('karaoke_pulado') or 0)
                and not int(linha.get('karaoke_nome_oculto') or 0) and bool((linha.get('nome_cliente') or '').strip()))
    except (TypeError, ValueError):
        return False


def ranking(linhas, zerado_em=None):
    """Ranking da noite: [{'pid', 'nome', 'musicas', 'primeiro', 'cid'}], mais músicas primeiro; empate = quem cantou
    primeiro (o menor exibido_at). Agrupa por celular + nome (dois "João" de celulares diferentes = 2 linhas).
    linhas: dicts com id, ip_cliente, nome_cliente, exibido_at, status e as colunas karaoke*. zerado_em (horário de
    Brasília, o mesmo do exibido_at): só conta o que passou na TV DEPOIS do zerar."""
    grupos = {}
    for r in linhas or []:
        if not conta_no_ranking(r):
            continue
        ex = r.get('exibido_at') or ''
        if zerado_em and not ex > zerado_em:
            continue
        nome = nome_curto(r.get('nome_cliente') or '')
        chave = (cid(r.get('ip_cliente')), nome.lower())
        g = grupos.get(chave)
        if g is None:
            grupos[chave] = {'pid': r.get('id'), 'nome': nome, 'musicas': 1, 'primeiro': ex, 'cid': chave[0]}
        else:
            g['musicas'] += 1
            if ex < g['primeiro']:
                g['primeiro'] = ex
            if (r.get('id') or 0) > (g['pid'] or 0):
                g['pid'] = r.get('id')   # o pedido mais novo do grupo (o "🙈 Esconder nome" usa ele)
    return sorted(grupos.values(), key=lambda g: (-g['musicas'], g['primeiro'], g['pid'] or 0))


def rei(rank):
    """{'nome', 'n'} do 1º do ranking, ou None (ninguém cantou ainda)."""
    if not rank:
        return None
    return {'nome': rank[0]['nome'], 'n': rank[0]['musicas']}


def _dt(s):
    try:
        return datetime.strptime(str(s or '')[:19], '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None


def acabou(exibido_at, agora_br, dur_seg=None, tem_depois=False, folga=PALCO_FOLGA_SEG):
    """A música que a TV começou já acabou? tem_depois = outro pedido do bar passou na TV depois deste; senão passou a
    duração + folga (30 s; o ⏭️ Pular usa 0) ou 6 min sem a duração. exibido_at e agora_br no horário de Brasília."""
    if tem_depois:
        return True
    ini, agora = _dt(exibido_at), _dt(agora_br)
    if not ini or not agora:
        return False
    limite = (int(dur_seg) + int(folga)) if dur_seg and int(dur_seg) > 0 else PALCO_SEM_DUR_SEG
    return agora >= ini + timedelta(seconds=limite)


def bloco(palco, fila, rank, votacao=False):
    """O bloco 'karaoke' do /api/status e das telas do celular.
    palco: a linha do pedido que está no palco (ou None); fila: as linhas pendentes de karaokê NA ORDEM REAL da TV;
    rank: ranking(...). Nunca leva IP, celular nem id de cliente."""
    nomes = [nome_exibido(f, votacao) for f in (fila or [])]
    n = len(nomes)
    return {
        'ligado': True,
        'palco': ({'id': palco.get('id'), 'nome': nome_exibido(palco, votacao),
                   'titulo': titulo_limpo(palco.get('titulo_pedido') or '', palco.get('youtube_id'), acervo_por_id())}
                  if palco else None),
        'fila': nomes[:FILA_MAX],
        'fila_n': n,
        'espera_min': n * MUSICA_MIN + (2 if (palco or n) else 0),
        'rei': None if votacao else rei(rank),
        'gratis': True,   # V1: o karaokê é grátis (o R$ 2 fica pro V2a, atrás do advogado)
    }


# ── Acervo karaokê curado (pubshow_karaoke_acervo.ACERVO) ────────────────────────────────────────────────────────────
_YID_RE = re.compile(r'[A-Za-z0-9_-]{11}')

# Licença por canal (crítica de negócio/lei de 29/10, trava B2): só estes canais DECLARAM licença das letras (selo de
# empresa de karaokê). O resto é canal caseiro ('sem prova': Clubinho do Karaokê, Viny Teclas, Ponto do Karaokê...).
# Padrão = só 'declarada' no acervo. Com o OK do Diogo pros caseiros: env PUBSHOW_KARAOKE_ACERVO=todos.
# A busca (Google) continua achando qualquer canal: o filtro vale pro acervo curado (as "mais cantadas" e a semeadura).
CANAIS_DECLARADA = ('stingray karaoke', 'zoom karaoke official', 'party tyme karaoke', 'singer karaoke')


def licenca(canal):
    """'declarada' (canal com selo/licença declarada) ou 'sem prova'."""
    c = norm(canal)
    return 'declarada' if any(c == d or c.startswith(d + ' ') for d in CANAIS_DECLARADA) else 'sem prova'


def acervo_todos():
    """True = o Diogo aprovou os canais 'sem prova' (env PUBSHOW_KARAOKE_ACERVO=todos)."""
    return str(os.environ.get('PUBSHOW_KARAOKE_ACERVO', '') or '').strip().lower() == 'todos'


def acervo_por_id(acervo=None):
    return {a['id']: a for a in acervo_validos(acervo)}


def acervo_validos(acervo=None, todos=None):
    """As entradas do acervo com id de 11 letras e título, sem repetir (a 1ª vence). Cada uma: id, titulo, canal,
    artista, musica, seg, genero, licenca. Sem a env PUBSHOW_KARAOKE_ACERVO=todos, só os canais de licença
    'declarada' (todos=True devolve tudo, com a licença marcada)."""
    if acervo is None:
        try:
            import pubshow_karaoke_acervo as _ac
            acervo = getattr(_ac, 'ACERVO', []) or []
        except Exception:
            acervo = []
    if todos is None:
        todos = acervo_todos()
    out, vistos = [], set()
    for a in acervo if isinstance(acervo, (list, tuple)) else []:
        if not isinstance(a, dict):
            continue
        vid = str(a.get('id') or '').strip()
        tit = ' '.join(str(a.get('titulo') or '').split())
        if not (_YID_RE.fullmatch(vid) and tit) or vid in vistos:
            continue
        vistos.add(vid)
        try:
            seg = int(a.get('seg') or 0)
        except (TypeError, ValueError):
            seg = 0
        canal = ' '.join(str(a.get('canal') or '').split())[:100]
        lic = licenca(canal)
        if not todos and lic != 'declarada':
            continue
        out.append({'id': vid, 'titulo': tit[:200], 'canal': canal,
                    'artista': str(a.get('artista') or '')[:80], 'musica': str(a.get('musica') or '')[:80],
                    'seg': seg, 'genero': str(a.get('genero') or '')[:30], 'licenca': lic})
    return out


def acervo_ids(acervo=None):
    return {a['id'] for a in acervo_validos(acervo)}


def acervo_precisa(validos, vistos, agora_utc=None):
    """Os ids do acervo que faltam no acervo vivo ou que foram vistos há mais de REFRESH_DIAS dias.
    vistos: {youtube_id: visto_em (UTC 'AAAA-MM-DD HH:MM:SS')}."""
    agora = agora_utc or datetime.utcnow()
    corte = (agora - timedelta(days=REFRESH_DIAS)).strftime('%Y-%m-%d %H:%M:%S')
    return [a['id'] for a in validos if a['id'] not in vistos or str(vistos[a['id']] or '') < corte]
