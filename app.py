"""
app.py — 4KITEM Plataforma de Soluções Digitais
"""
import logging
import threading
from flask import (Flask, render_template, redirect, jsonify,
                   request, abort, url_for)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('4kitem')

app = Flask(__name__)

from kids_db import (
    init_db, get_videos, get_channels, total_videos, stats,
    get_videos_for_mode, get_client, set_client_mode,
    create_client, MODES,
)

# ══════════════════════════════════════════════════════════════════════════
#  LANDINGS
# ══════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/kids')
def kids():
    return render_template('kids/landing.html', stats=stats())

@app.route('/sala')
def sala():
    return render_template('sala/landing.html', stats=stats())

@app.route('/agenda')
def agenda():
    return render_template('agenda/landing.html')

@app.route('/alerta')
def alerta():
    return render_template('alerta/landing.html')


# ══════════════════════════════════════════════════════════════════════════
#  TV PLAYER  — /tv/<code>
# ══════════════════════════════════════════════════════════════════════════

@app.route('/tv')
def tv_redirect():
    return redirect(url_for('tv_player', code='DEMO'))

@app.route('/tv/<code>')
def tv_player(code):
    client = get_client(code)
    if not client:
        abort(404)
    mode_info = MODES.get(client['mode'], MODES['kids'])
    return render_template('tv/player.html',
                           client=client,
                           mode_info=mode_info,
                           modes=MODES)


# ══════════════════════════════════════════════════════════════════════════
#  PAINEL DO CLIENTE  — /painel/<code>
# ══════════════════════════════════════════════════════════════════════════

@app.route('/painel')
def painel_redirect():
    return redirect(url_for('painel', code='DEMO'))

@app.route('/painel/<code>')
def painel(code):
    client = get_client(code)
    if not client:
        abort(404)
    return render_template('painel/index.html',
                           client=client,
                           modes=MODES)


# ══════════════════════════════════════════════════════════════════════════
#  API  — /api/...
# ══════════════════════════════════════════════════════════════════════════

# ── Playlist para o TV player ─────────────────────────────────────────────
@app.route('/api/tv/<code>/playlist')
def api_tv_playlist(code):
    client = get_client(code)
    if not client:
        return jsonify({'error': 'client not found'}), 404
    limit  = min(request.args.get('limit', 30, type=int), 50)
    videos = get_videos_for_mode(client['mode'], limit=limit, shuffle=True)
    return jsonify({
        'videos':    videos,
        'mode':      client['mode'],
        'mode_info': MODES.get(client['mode'], {}),
        'client':    {
            'code':       client['code'],
            'name':       client['name'],
            'logo_url':   client['logo_url'],
            'city':       client['city'],
            'ticker_msg': client['ticker_msg'],
        },
    })

# ── Status (usado pelo TV player para detectar troca de modo) ─────────────
@app.route('/api/tv/<code>/status')
def api_tv_status(code):
    client = get_client(code)
    if not client:
        return jsonify({'error': 'client not found'}), 404
    return jsonify({
        'mode':     client['mode'],
        'name':     client['name'],
        'logo_url': client['logo_url'],
    })

# ── Mudar modo (painel → POST) ────────────────────────────────────────────
@app.route('/api/tv/<code>/mode', methods=['POST'])
def api_set_mode(code):
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', '')
    if not set_client_mode(code, mode):
        return jsonify({'error': f'modo inválido: {mode}'}), 400
    return jsonify({'ok': True, 'mode': mode,
                    'mode_label': MODES[mode]['label']})

# ── Criar cliente ─────────────────────────────────────────────────────────
@app.route('/api/clients', methods=['POST'])
def api_create_client():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    city = data.get('city', 'Brasil').strip()
    mode = data.get('mode', 'kids')
    if not name:
        return jsonify({'error': 'name obrigatório'}), 400
    client = create_client(name, city, mode)
    return jsonify({'ok': True, 'client': dict(client)}), 201

# ── Vídeos filtrados (landing pages) ─────────────────────────────────────
@app.route('/api/kids/videos')
def api_kids_videos():
    age    = request.args.get('age',    type=int)
    gender = request.args.get('gender', default=None)
    limit  = min(request.args.get('limit',  24, type=int), 48)
    offset = request.args.get('offset', 0,  type=int)
    videos = get_videos(age=age, gender=gender, limit=limit, offset=offset)
    total  = total_videos(age=age, gender=gender)
    return jsonify({
        'videos':   videos,
        'total':    total,
        'offset':   offset,
        'limit':    limit,
        'has_more': (offset + limit) < total,
    })

@app.route('/api/kids/channels')
def api_kids_channels():
    return jsonify({'channels': get_channels()})

@app.route('/api/kids/stats')
def api_kids_stats():
    return jsonify(stats())

@app.route('/api/modes')
def api_modes():
    return jsonify(MODES)

# ── Admin: refresh scraper ────────────────────────────────────────────────
@app.route('/kids/admin/refresh', methods=['POST'])
def kids_refresh():
    def _run():
        try:
            from kids_scraper import scrape_all
            scrape_all()
        except Exception as e:
            log.error(f"Scrape error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'status': 'started'})


# ══════════════════════════════════════════════════════════════════════════
#  HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════

@app.route('/health')
def health():
    s = stats()
    return {'status': 'ok', 'app': '4KITEM', **s}, 200


# ══════════════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════════════

def _startup():
    try:
        init_db()
        s = stats()
        log.info(f"DB OK — {s['channels']} canais | {s['videos']} vídeos | "
                 f"{s['clients']} clientes")
        if s['videos'] < 50:
            log.info("Poucos vídeos — scrape automático iniciado...")
            def _scrape():
                try:
                    from kids_scraper import scrape_all
                    scrape_all()
                except Exception as e:
                    log.error(f"Scrape startup error: {e}")
            threading.Thread(target=_scrape, daemon=True).start()
    except Exception as e:
        log.error(f"Startup error: {e}")

with app.app_context():
    _startup()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
