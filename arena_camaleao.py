"""arena_camaleao.py — blueprint do CAMALEAO (esconde-esconde de camuflagem 2D).

Rotas FINAS: toda a lógica vive em camaleao_engine; o estado quente em
camaleao_store (RAM). Jogo 100% GRÁTIS — sem nenhuma palavra de aposta/bet/
sorte/dinheiro (mantém o Camaleão separado do 'valendo' do Arena, pra não
contaminar o risco de gateway).

Identidade: usa session['arena_user_id'] quando logado; senão cria um convidado
(session['cam_guest_id']) — assim o link "joga comigo!" funciona sem cadastro.
"""
import secrets
from flask import Blueprint, request, jsonify, session, render_template

import camaleao_store as store
import camaleao_engine as engine
from camaleao_modes import DEFAULT_MODE

arena_camaleao_bp = Blueprint('arena_camaleao', __name__, url_prefix='/arena/camaleao')

PROTO_V = 1


def _identity():
    uid = session.get('arena_user_id')
    if uid:
        return 'u:' + str(uid)
    gid = session.get('cam_guest_id')
    if not gid:
        gid = secrets.token_hex(8)
        session['cam_guest_id'] = gid
    return 'g:' + gid


def _bad_version(body):
    return isinstance(body, dict) and body.get('v') not in (None, PROTO_V)


# ─────────────────────────── páginas (HTML) ───────────────────────────

@arena_camaleao_bp.route('/')
def home():
    return render_template('arena/camaleao/jogo.html', token=None)


@arena_camaleao_bp.route('/j/<token>')
def entrar_link(token):
    return render_template('arena/camaleao/jogo.html', token=token)


# ─────────────────────────── API v1 (JSON) ───────────────────────────

@arena_camaleao_bp.route('/api/v1/scene/<scene_id>')
def api_scene(scene_id):
    try:
        return jsonify({'v': PROTO_V, 'scene': engine.scene_backdrop(scene_id)})
    except Exception:
        return jsonify({'v': PROTO_V, 'erro': 'cena_inexistente'}), 404


@arena_camaleao_bp.route('/api/v1/room/create', methods=['POST'])
def api_create():
    body = request.get_json(silent=True) or {}
    if _bad_version(body):
        return jsonify({'v': PROTO_V, 'erro': 'versao'}), 409
    nick = (body.get('nick') or '').strip()[:24] or 'Anfitrião'
    mode = body.get('mode') or DEFAULT_MODE
    with store.lock():
        room, host_pid = engine.criar_sala(mode, _identity(), nick)
        if not store.criar(room):
            return jsonify({'v': PROTO_V, 'erro': 'servidor_cheio'}), 503
        token = room['token']
    return jsonify({'v': PROTO_V, 'token': token, 'pid': host_pid, 'host': True,
                    'join_url': '/arena/camaleao/j/' + token})


@arena_camaleao_bp.route('/api/v1/room/<token>/join', methods=['POST'])
def api_join(token):
    body = request.get_json(silent=True) or {}
    if _bad_version(body):
        return jsonify({'v': PROTO_V, 'erro': 'versao'}), 409
    nick = (body.get('nick') or '').strip()[:24] or 'Convidado'
    with store.lock():
        room = store.get(token)
        if not room:
            return jsonify({'v': PROTO_V, 'erro': 'sala_inexistente'}), 404
        engine.tick(room)
        pid, status = engine.add_player(room, _identity(), nick)
        if pid is None:
            return jsonify({'v': PROTO_V, 'erro': status}), 409
        room['ultima_atividade'] = store.now_ms()
        return jsonify({'v': PROTO_V, 'pid': pid, 'status': status, 'phase': room['phase'],
                        'host': pid == room['host_pid']})


def _auth_player(room, body):
    """Confere que o pid do corpo pertence à identidade do chamador. Retorna pid ou None."""
    pid = (body or {}).get('pid')
    p = room['players'].get(pid)
    if not p or p['identity'] != _identity():
        return None
    return pid


@arena_camaleao_bp.route('/api/v1/room/<token>/start', methods=['POST'])
def api_start(token):
    body = request.get_json(silent=True) or {}
    if _bad_version(body):
        return jsonify({'v': PROTO_V, 'erro': 'versao'}), 409
    with store.lock():
        room = store.get(token)
        if not room:
            return jsonify({'v': PROTO_V, 'erro': 'sala_inexistente'}), 404
        pid = _auth_player(room, body)
        if not pid:
            return jsonify({'v': PROTO_V, 'erro': 'nao_autorizado'}), 403
        ok, msg = engine.start(room, pid)
        return jsonify({'v': PROTO_V, 'ok': ok, 'erro': None if ok else msg})


@arena_camaleao_bp.route('/api/v1/room/<token>/sync', methods=['POST'])
def api_sync(token):
    body = request.get_json(silent=True) or {}
    if _bad_version(body):
        return jsonify({'v': PROTO_V, 'erro': 'versao'}), 409
    with store.lock():
        room = store.get(token)
        if not room:
            return jsonify({'v': PROTO_V, 'erro': 'sala_encerrada'}), 404
        pid = _auth_player(room, body)
        if not pid:
            return jsonify({'v': PROTO_V, 'erro': 'nao_autorizado'}), 403
        p = room['players'][pid]
        now = store.now_ms()
        # rate-limit: /sync mais rápido que SYNC_MIN_MS reusa o último snapshot
        if p.get('_snap') and now - p.get('_last_sync', 0) < store.SYNC_MIN_MS:
            p['last_seen'] = now
            return jsonify(p['_snap'])
        snap = engine.sync(room, pid, body.get('input') or {})
        if snap is None:
            return jsonify({'v': PROTO_V, 'erro': 'fora_da_sala'}), 404
        p['_last_sync'] = now
        p['_snap'] = snap
        return jsonify(snap)


@arena_camaleao_bp.route('/api/v1/room/<token>/tag', methods=['POST'])
def api_tag(token):
    body = request.get_json(silent=True) or {}
    if _bad_version(body):
        return jsonify({'v': PROTO_V, 'erro': 'versao'}), 409
    with store.lock():
        room = store.get(token)
        if not room:
            return jsonify({'v': PROTO_V, 'erro': 'sala_encerrada'}), 404
        pid = _auth_player(room, body)
        if not pid:
            return jsonify({'v': PROTO_V, 'erro': 'nao_autorizado'}), 403
        res = engine.tag(room, pid, body.get('target_id'))
        p = room['players'].get(pid)
        if p:                                    # invalida o cache pra o próximo /sync refletir na hora
            p['_snap'] = None
        return jsonify(res)


@arena_camaleao_bp.route('/api/v1/room/<token>/leave', methods=['POST'])
def api_leave(token):
    body = request.get_json(silent=True) or {}
    with store.lock():
        room = store.get(token)
        if not room:
            return jsonify({'v': PROTO_V, 'ok': True})
        pid = _auth_player(room, body)
        if pid:
            engine.leave(room, pid)
        return jsonify({'v': PROTO_V, 'ok': True})


@arena_camaleao_bp.route('/api/v1/room/<token>/result')
def api_result(token):
    with store.lock():
        room = store.get(token)
        if not room:
            return jsonify({'v': PROTO_V, 'erro': 'sala_encerrada'}), 404
        engine.tick(room)
        return jsonify({'v': PROTO_V, 'phase': room['phase'], 'result': engine.resultado(room)})
