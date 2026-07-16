"""camaleao_engine.py — motor do CAMALEAO (agnóstico de modo/cena).

Responsável por: carregar cena, máquina de fases (tick por timestamp, sem
scheduler), sorteio de papéis, clamp de movimento, resolução do /tag no
servidor e — o ponto crítico — o BUILDER DE SNAPSHOT FILTRADO POR PAPEL
(anti-leak). Toda mutação de sala passa por aqui; as rotas são finas.

ANTI-LEAK (o gate de justiça):
  - O cliente do CAÇADOR nunca recebe quem é hider. Durante HIDING recebe só
    {phase:'hiding', blindfold:True} (zero dado espacial). Durante SEEKING
    recebe `props` = (hiders vivos + decoys da cena) no MESMO schema
    {id,x,y,sprite}, ids OPACOS e ESTÁVEIS por entidade, ORDEM EMBARALHADA a
    cada resposta, SEM flag de tipo/vida.
  - CRÍTICO: o cliente NÃO carrega os decoys pela cena estática (senão o
    caçador subtrai props−decoys = hiders). O cliente pega só o CENÁRIO-BASE
    (paredes/skins/dimensões); os decoys chegam SÓ pelo servidor — pro hider
    (pra ele se esconder no meio) e pro caçador dentro de `props`.
  - A resolução "é hider ou decoy?" acontece SÓ no servidor, no /tag.
"""
import os
import json
import math
import random
import secrets

from camaleao_modes import get_mode
from camaleao_store import now_ms, novo_token
import camaleao_db

_SCENES = {}   # cache: scene_id -> dict completo (com decoys/spawns) — uso do SERVIDOR
_SCENE_DIR = os.path.join(os.path.dirname(__file__), 'static', 'arena', 'camaleao', 'scenes')


def load_scene(scene_id):
    """Cena COMPLETA (com decoys/spawns) — uso interno do servidor. Cacheada."""
    sc = _SCENES.get(scene_id)
    if sc is None:
        with open(os.path.join(_SCENE_DIR, scene_id + '.json'), encoding='utf-8') as f:
            sc = json.load(f)
        _SCENES[scene_id] = sc
    return sc


def scene_backdrop(scene_id):
    """Cenário-base pro CLIENTE: SEM decoys, SEM spawns (anti-leak)."""
    sc = load_scene(scene_id)
    return {
        'id': sc['id'], 'nome': sc.get('nome', ''),
        'w': sc['w'], 'h': sc['h'],
        'skins': sc.get('skins', []),
        'palette': sc.get('palette', []),
        'walls': sc.get('walls', []),
    }


def _pid():
    return secrets.token_hex(4)


def _prop_id():
    return secrets.token_hex(5)


def _dist(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


# ─────────────────────────── ciclo de vida da sala ───────────────────────────

def criar_sala(mode_id, host_identity, host_nick):
    """Monta uma sala nova em LOBBY com o host dentro. Retorna (room, host_pid)."""
    mode = get_mode(mode_id)
    scene_id = mode['scene']
    load_scene(scene_id)                      # valida que a cena existe
    now = now_ms()
    token = novo_token()
    host_pid = _pid()
    room = {
        'token': token, 'mode_id': mode['id'], 'scene_id': scene_id,
        'phase': 'lobby', 'phase_started_at': None,
        'host_pid': host_pid, 'seq': 1,
        'players': {}, 'gravado': False, 'vencedor': None,
        'ultima_atividade': now,
    }
    room['players'][host_pid] = _novo_player(host_pid, host_identity, host_nick, now)
    return room, host_pid


def _novo_player(pid, identity, nick, now):
    return {
        'pid': pid, 'identity': identity, 'nick': (nick or 'Anônimo')[:24],
        'role': 'lobby', 'x': 0.0, 'y': 0.0, 'skin': None, 'tint': None, 'locked': False,
        'alive': 1, 'score': 0.0, 'last_seen': now, 'cooldown_ate': 0.0,
        'prop_id': None, 'achado_por': None, 'achado_em': None,
    }


def add_player(room, identity, nick):
    """Entra na sala. Reconexão (mesma identidade) reentra no mesmo slot.
    Retorna (pid, status) — status: 'ok' | 'reconnect' | 'cheio' | 'em_andamento'."""
    now = now_ms()
    for p in room['players'].values():
        if p['identity'] == identity:
            p['last_seen'] = now
            return p['pid'], 'reconnect'
    mode = get_mode(room['mode_id'])
    if room['phase'] != 'lobby':
        return None, 'em_andamento'
    if len(room['players']) >= mode['max_jogadores']:
        return None, 'cheio'
    pid = _pid()
    room['players'][pid] = _novo_player(pid, identity, nick, now)
    room['seq'] += 1
    return pid, 'ok'


def start(room, pid):
    """Host inicia a rodada: sorteia papéis+spawns e entra em HIDING."""
    tick(room)
    if room['phase'] != 'lobby':
        return False, 'ja_comecou'
    if pid != room['host_pid']:
        return False, 'so_host'
    mode = get_mode(room['mode_id'])
    players = list(room['players'].values())
    n = len(players)
    if n < mode['min_jogadores']:
        return False, 'poucos'
    now = now_ms()
    scene = load_scene(room['scene_id'])
    hs, ss = scene['spawns_hider'], scene['spawns_seeker']
    random.shuffle(players)
    n_seek = max(1, math.ceil(mode['seeker_ratio'] * n))
    n_seek = min(n_seek, n - 1)               # garante ao menos 1 escondido
    for i, p in enumerate(players):
        p['alive'] = 1
        p['locked'] = False
        p['score'] = 0.0
        p['cooldown_ate'] = 0.0
        p['achado_por'] = None
        p['achado_em'] = None
        p['last_seen'] = now
        if i < n_seek:
            p['role'] = 'seeker'
            sp = ss[i % len(ss)]
            p['skin'] = None
            p['prop_id'] = None
        else:
            p['role'] = 'hider'
            sp = hs[(i - n_seek) % len(hs)]
            p['skin'] = scene['skins'][0]
            p['prop_id'] = _prop_id()
        p['x'] = float(sp['x'])
        p['y'] = float(sp['y'])
        # hider nasce com a cor do decoy mais próximo (valor que JÁ existe nos decoys → nunca exclusivo de hider)
        p['tint'] = _tint_perto(scene, p['x'], p['y']) if p['role'] == 'hider' else None
    room['phase'] = 'hiding'
    room['phase_started_at'] = now
    room['gravado'] = False
    room['vencedor'] = None
    room['seq'] += 1
    room['ultima_atividade'] = now
    return True, 'ok'


def leave(room, pid):
    p = room['players'].pop(pid, None)
    if not p:
        return
    room['seq'] += 1
    if room['host_pid'] == pid:
        _migrar_host(room)


def _migrar_host(room):
    """Host saiu: passa pro jogador mais antigo presente (rodada não depende do host)."""
    vivos = sorted(room['players'].values(), key=lambda p: p['last_seen'])
    room['host_pid'] = vivos[0]['pid'] if vivos else None


# ─────────────────────────── máquina de fases (tick) ───────────────────────────

def tick(room):
    """Avança a fase por timestamp. Chamado no topo de toda rota mutável. Idempotente."""
    ph = room['phase']
    if ph == 'lobby':
        return
    now = now_ms()
    st = room['phase_started_at'] or now
    mode = get_mode(room['mode_id'])
    if ph == 'hiding':
        if now - st >= mode['hiding_s'] * 1000:
            room['phase'] = 'seeking'
            room['phase_started_at'] = now
            room['seq'] += 1
    elif ph == 'seeking':
        acabou_tempo = now - st >= mode['seeking_s'] * 1000
        if _restantes(room) == 0 or acabou_tempo or not _algum_seeker_presente(room, now):
            _to_result(room, now)
    elif ph == 'result':
        if now - st >= mode['result_s'] * 1000:
            _to_lobby(room)


def _restantes(room):
    return sum(1 for p in room['players'].values() if p['role'] == 'hider' and p['alive'])


def _algum_seeker_presente(room, now):
    seekers = [p for p in room['players'].values() if p['role'] == 'seeker']
    if not seekers:
        return False
    return any(now - p['last_seen'] <= 8000 for p in seekers)


def _to_result(room, now):
    mode = get_mode(room['mode_id'])
    for p in room['players'].values():
        if p['role'] == 'hider' and p['alive']:
            p['score'] += mode['score_survive']
    room['vencedor'] = 'cacadores' if _restantes(room) == 0 else 'escondidos'
    room['phase'] = 'result'
    room['phase_started_at'] = now
    room['seq'] += 1
    if not room.get('gravado'):
        room['gravado'] = True
        jogadores = [{'nick': p['nick'], 'papel': p['role'],
                      'pontos': round(p['score']), 'sobreviveu': bool(p['alive'])}
                     for p in room['players'].values()]
        camaleao_db.gravar_partida(room['token'], room['mode_id'], room['scene_id'],
                                   jogadores, room['vencedor'])


def _to_lobby(room):
    """Revancha: volta pro lobby zerando a rodada (mantém jogadores e host)."""
    for p in room['players'].values():
        p['role'] = 'lobby'
        p['alive'] = 1
        p['locked'] = False
        p['skin'] = None
        p['prop_id'] = None
        p['score'] = 0.0
        p['achado_por'] = None
        p['achado_em'] = None
        p['cooldown_ate'] = 0.0
    room['phase'] = 'lobby'
    room['phase_started_at'] = None
    room['gravado'] = False
    room['vencedor'] = None
    room['seq'] += 1


# ─────────────────────────── movimento + /tag ───────────────────────────

def sync(room, pid, inp):
    """Aplica input (movimento clampado + skin/lock) e devolve o snapshot do papel."""
    tick(room)
    p = room['players'].get(pid)
    if not p:
        return None
    now = now_ms()
    room['ultima_atividade'] = now
    mode = get_mode(room['mode_id'])
    pode_mover = (
        (p['role'] == 'hider' and p['alive'] and room['phase'] in ('hiding', 'seeking')) or
        (p['role'] == 'seeker' and room['phase'] == 'seeking')
    )
    if pode_mover and isinstance(inp, dict):
        try:
            dx = float(inp.get('x', p['x']))
            dy = float(inp.get('y', p['y']))
        except (TypeError, ValueError):
            dx, dy = p['x'], p['y']
        speed = mode['seeker_speed'] if p['role'] == 'seeker' else mode['hider_speed']
        dt = min(max((now - p['last_seen']) / 1000.0, 0.0), 0.5)   # cap 0.5s
        maxstep = speed * dt + 8.0                                  # folga p/ 1º tick
        d = _dist(p['x'], p['y'], dx, dy)
        if d <= maxstep:
            p['x'], p['y'] = dx, dy
        elif d > 0:
            p['x'] += (dx - p['x']) * (maxstep / d)
            p['y'] += (dy - p['y']) * (maxstep / d)
        # skin / tint / trava só valem pro hider
        if p['role'] == 'hider':
            sc = load_scene(room['scene_id'])
            skin = inp.get('skin')
            if skin in sc['skins']:
                p['skin'] = skin
            t = inp.get('tint')
            if isinstance(t, int) and 0 <= t < len(sc.get('palette', [])):   # clampa ao domínio: nunca cor-outlier no fio
                p['tint'] = t
            p['locked'] = bool(inp.get('locked'))
    p['last_seen'] = now
    return snapshot(room, pid)


def tag(room, pid, target_id):
    """Caçador cutuca um objeto. Resolve 100% no servidor."""
    tick(room)
    p = room['players'].get(pid)
    if not p:
        return {'v': 1, 'result': 'nope'}
    now = now_ms()
    room['ultima_atividade'] = now
    p['last_seen'] = now
    mode = get_mode(room['mode_id'])
    if room['phase'] != 'seeking' or p['role'] != 'seeker':
        return {'v': 1, 'result': 'nope'}
    if now < p['cooldown_ate']:
        return {'v': 1, 'result': 'cooldown', 'cooldown_ms': round(p['cooldown_ate'] - now)}
    # localizar alvo (decoy da cena OU hider vivo pelo prop_id opaco)
    tpos = None
    alvo_hider = None
    for d in load_scene(room['scene_id'])['decoys']:
        if d['id'] == target_id:
            tpos = (d['x'], d['y'])
            break
    if tpos is None:
        for q in room['players'].values():
            if q['role'] == 'hider' and q['alive'] and q.get('prop_id') == target_id:
                tpos = (q['x'], q['y'])
                alvo_hider = q
                break
    if tpos is None:
        return {'v': 1, 'result': 'miss', 'score': round(p['score'])}   # id desconhecido/já pego
    if _dist(p['x'], p['y'], tpos[0], tpos[1]) > mode['reach']:
        return {'v': 1, 'result': 'far'}                                # longe -> sem punir
    if alvo_hider is not None:
        alvo_hider['alive'] = 0
        alvo_hider['achado_por'] = pid
        alvo_hider['achado_em'] = now
        bonus = mode['score_time_bonus'] * max(0.0, _t_left_ms(room, now) / 1000.0)
        p['score'] += mode['score_hit'] + bonus
        room['seq'] += 1
        tick(room)                                                      # pode virar RESULT se zerou
        return {'v': 1, 'result': 'hit', 'score': round(p['score']), 'restantes': _restantes(room)}
    # decoy: erro
    p['cooldown_ate'] = now + mode['cooldown_miss_ms']
    p['score'] += mode['score_miss']
    room['seq'] += 1
    return {'v': 1, 'result': 'miss', 'cooldown_ms': mode['cooldown_miss_ms'], 'score': round(p['score'])}


# ─────────────────────────── snapshot (anti-leak) ───────────────────────────

def _t_left_ms(room, now=None):
    if room['phase'] in ('lobby',) or not room['phase_started_at']:
        return 0
    now = now or now_ms()
    mode = get_mode(room['mode_id'])
    dur = {'hiding': mode['hiding_s'], 'seeking': mode['seeking_s'], 'result': mode['result_s']}.get(room['phase'], 0)
    return max(0, round(dur * 1000 - (now - room['phase_started_at'])))


def _props_seeker(room):
    """hiders vivos + decoys, MESMO schema, ids opacos estáveis, ORDEM EMBARALHADA, sem flag."""
    props = [{'id': d['id'], 'x': d['x'], 'y': d['y'], 'sprite': d['sprite'], 'tint': d.get('tint', 0)}
             for d in load_scene(room['scene_id'])['decoys']]
    for p in room['players'].values():
        if p['role'] == 'hider' and p['alive'] and p.get('prop_id'):
            props.append({'id': p['prop_id'], 'x': round(p['x'], 1), 'y': round(p['y'], 1),
                          'sprite': p['skin'] or 'barril', 'tint': p['tint'] if p.get('tint') is not None else 0})
    random.shuffle(props)
    return props


def _decoys_cliente(room):
    return [{'id': d['id'], 'x': d['x'], 'y': d['y'], 'sprite': d['sprite'], 'tint': d.get('tint', 0)}
            for d in load_scene(room['scene_id'])['decoys']]


def _amigos_hider(room, p):
    """Outros hiders vivos como props — SÓ pro cliente de hider (se fantasiar juntos, igual o original).
    Nunca entra em payload de seeker."""
    return [{'id': q['prop_id'], 'x': round(q['x'], 1), 'y': round(q['y'], 1),
             'sprite': q['skin'] or 'barril', 'tint': q['tint'] if q.get('tint') is not None else 0}
            for q in room['players'].values()
            if q['role'] == 'hider' and q['alive'] and q.get('prop_id') and q['pid'] != p['pid']]


def _tint_perto(scene, x, y):
    """Tint do decoy mais próximo (pro spawn do hider nascer com cor que já existe no mundo)."""
    ds = scene.get('decoys') or []
    if not ds:
        return 0
    d = min(ds, key=lambda d: _dist(x, y, d['x'], d['y']))
    return d.get('tint', 0)


def _camo_dica(room, p):
    """(camo 0..100, dica_tint) do hider — SÓ vai no bloco `you` do hider, NUNCA no seeker."""
    sc = load_scene(room['scene_id'])
    decoys = sc['decoys']
    R = 180.0
    same = [d for d in decoys if d['sprite'] == p['skin'] and _dist(p['x'], p['y'], d['x'], d['y']) <= R]
    if same:
        tints = [d.get('tint', 0) for d in same]
        dom = max(set(tints), key=tints.count)
        mt = p.get('tint')
        if mt is None:
            return 20, dom
        dt = min(abs(mt - t) for t in tints)          # 0 = casou; erro grande = estoura
        return int(max(0, min(100, 100 - dt * 34))), dom
    # sem cacho do mesmo objeto por perto = exposto; dica = cor do decoy mais próximo
    near = min(decoys, key=lambda d: _dist(p['x'], p['y'], d['x'], d['y']))
    return 12, near.get('tint', 0)


def snapshot(room, pid):
    p = room['players'].get(pid)
    if not p:
        return None
    now = now_ms()
    phase = room['phase']
    base = {
        'v': 1, 'phase': phase, 'seq': room['seq'],
        't_left_ms': _t_left_ms(room, now),
        'scene_id': room['scene_id'], 'restantes': _restantes(room),
        'n_jogadores': len(room['players']),
        'you': {
            'pid': pid, 'role': p['role'], 'alive': p['alive'],
            'score': round(p['score']),
            'cooldown_ms': max(0, round(p['cooldown_ate'] - now)),
            'x': round(p['x'], 1), 'y': round(p['y'], 1),
            'skin': p['skin'], 'tint': p.get('tint'), 'locked': p['locked'],
        },
    }
    if phase == 'lobby':
        base['host_pid'] = room['host_pid']
        base['host'] = (pid == room['host_pid'])
        base['jogadores'] = [{'pid': q['pid'], 'nick': q['nick']} for q in room['players'].values()]
        base['min_jogadores'] = get_mode(room['mode_id'])['min_jogadores']
        return base
    if phase == 'result':
        base['result'] = resultado(room)
        base['host'] = (pid == room['host_pid'])
        return base
    if phase == 'hiding':
        if p['role'] == 'seeker':
            base['blindfold'] = True                       # ZERO dado espacial
            return base
        base['decoys'] = _decoys_cliente(room) + _amigos_hider(room, p)   # decoys + colegas escondidos (dress-up juntos)
        base['seekers'] = [{'x': round(q['x'], 1), 'y': round(q['y'], 1)}
                           for q in room['players'].values() if q['role'] == 'seeker']
        base['you']['camo'], base['you']['dica_tint'] = _camo_dica(room, p)
        return base
    # seeking
    if p['role'] == 'seeker':
        base['seekers'] = [{'pid': q['pid'], 'x': round(q['x'], 1), 'y': round(q['y'], 1), 'nick': q['nick']}
                           for q in room['players'].values() if q['role'] == 'seeker']
        base['props'] = _props_seeker(room)                # decoys+hiders indistinguíveis
        return base
    base['decoys'] = _decoys_cliente(room) + _amigos_hider(room, p)
    base['seekers'] = [{'x': round(q['x'], 1), 'y': round(q['y'], 1)}
                       for q in room['players'].values() if q['role'] == 'seeker']
    base['you']['camo'], base['you']['dica_tint'] = _camo_dica(room, p)
    return base


def resultado(room):
    """Revelação completa pro fim de rodada."""
    jogadores = []
    achador_nick = {q['pid']: q['nick'] for q in room['players'].values()}
    for q in room['players'].values():
        jogadores.append({
            'nick': q['nick'], 'papel': q['role'], 'pontos': round(q['score']),
            'sobreviveu': bool(q['alive']) if q['role'] == 'hider' else None,
            'achado_por': achador_nick.get(q['achado_por']) if q['achado_por'] else None,
        })
    jogadores.sort(key=lambda j: j['pontos'], reverse=True)
    return {'vencedor': room.get('vencedor'), 'jogadores': jogadores}
