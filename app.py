"""
app.py — 4KITEM Plataforma de Soluções Digitais
"""
import logging
import threading
from flask import Flask, render_template, redirect, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('4kitem')

app = Flask(__name__)

# ── Kids DB ───────────────────────────────────────────────────────────────
from kids_db import init_db, get_videos, get_channels, total_videos, stats

# ── Rotas principais ──────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/kids')
def kids():
    db_stats = stats()
    return render_template('kids/landing.html', stats=db_stats)

@app.route('/agenda')
def agenda():
    return redirect('https://radioscsews.com.br/agenda', 301)

@app.route('/alerta')
def alerta():
    return redirect('https://radioscsews.com.br/alerta', 301)

# ── API: Vídeos Kids ──────────────────────────────────────────────────────
@app.route('/api/kids/videos')
def api_kids_videos():
    age    = request.args.get('age',    type=int)
    gender = request.args.get('gender', default=None, type=str)
    limit  = min(request.args.get('limit',  24, type=int), 48)
    offset = request.args.get('offset', 0,  type=int)

    videos = get_videos(age=age, gender=gender, limit=limit, offset=offset)
    total  = total_videos(age=age, gender=gender)

    return jsonify({
        'videos': videos,
        'total':  total,
        'offset': offset,
        'limit':  limit,
        'has_more': (offset + limit) < total,
    })

# ── API: Canais Kids ──────────────────────────────────────────────────────
@app.route('/api/kids/channels')
def api_kids_channels():
    return jsonify({'channels': get_channels()})

# ── API: Stats ────────────────────────────────────────────────────────────
@app.route('/api/kids/stats')
def api_kids_stats():
    return jsonify(stats())

# ── Admin: disparar scrape manual ─────────────────────────────────────────
@app.route('/kids/admin/refresh', methods=['POST'])
def kids_refresh():
    """Dispara scrape em background. Proteger com senha em produção."""
    def _run():
        try:
            from kids_scraper import scrape_all
            result = scrape_all()
            log.info(f"Scrape manual concluído: {result}")
        except Exception as e:
            log.error(f"Erro no scrape manual: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'status': 'started', 'message': 'Scrape disparado em background'})

# ── Health check Railway ──────────────────────────────────────────────────
@app.route('/health')
def health():
    s = stats()
    return {
        'status':      'ok',
        'app':         '4KITEM',
        'kids_videos': s['videos'],
        'kids_channels': s['channels'],
    }, 200

# ── Startup ───────────────────────────────────────────────────────────────
def _startup():
    """Inicializa DB e dispara scrape de fundo na primeira execução."""
    try:
        init_db()
        s = stats()
        log.info(f"DB pronto — {s['channels']} canais, {s['videos']} vídeos")

        # Só scrapa se tiver poucos vídeos (primeira execução)
        if s['videos'] < 50:
            log.info("Poucos vídeos — iniciando scrape automático...")
            def _scrape():
                try:
                    from kids_scraper import scrape_all
                    scrape_all()
                except Exception as e:
                    log.error(f"Erro no scrape automático: {e}")
            threading.Thread(target=_scrape, daemon=True).start()
    except Exception as e:
        log.error(f"Erro no startup: {e}")

with app.app_context():
    _startup()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
