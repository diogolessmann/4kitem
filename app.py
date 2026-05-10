"""
app.py — 4KITEM Plataforma de Soluções Digitais
"""
from flask import Flask, render_template, redirect, url_for

app = Flask(__name__)

# ── Rotas principais ──────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/kids')
def kids():
    return render_template('kids/landing.html')

@app.route('/agenda')
def agenda():
    return redirect('https://radioscsews.com.br/agenda', 301)

@app.route('/alerta')
def alerta():
    return redirect('https://radioscsews.com.br/alerta', 301)

# ── Health check Railway ──────────────────────────────────
@app.route('/health')
def health():
    return {'status': 'ok', 'app': '4KITEM'}, 200

if __name__ == '__main__':
    app.run(debug=True, port=5001)
