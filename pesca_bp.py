"""Best Time to Fish — guia de pesca do Texas, em inglês, servido pelo 4kitem (decisão do Diogo em 24/11/2026).

O site é estático. Um robô gera as páginas a cada 2 h num PROCESSO SEPARADO (pesca/atualiza.py: coleta do
NWS, da NOAA e do TPWD -> páginas -> conferência) e grava no volume, em DATA_DIR/pesca/site_<data>/.
Só depois de a conferência passar o ponteiro DATA_DIR/pesca/ATUAL muda para a pasta nova; se vier ruim,
o site anterior continua. Aqui o 4kitem só entrega arquivo: nada de Skyfield nem numpy no processo web.

Endereços:
- sem domínio próprio: https://www.4kitem.com.br/fish/
- com domínio próprio (variável HDP_DOMINIO=besttimetofishtoday.com no Railway + domínio adicionado no
  serviço): o domínio cai no /fish sem mexer no app.py (igual à Vitrine), e o /fish do 4kitem vira 301
  para o domínio, para o Google não ver o mesmo site em dois lugares.
Variáveis: HDP_DOMINIO (opcional), HDP_SITE_URL (força o endereço), HDP_ROBO=0 desliga o robô.
"""
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, Response, redirect, request, send_from_directory
from werkzeug.security import safe_join
from werkzeug.utils import redirect as _wz_redirect

log = logging.getLogger('pesca')

PESCA = Path(__file__).resolve().parent / 'pesca'          # o motor (cópia do projeto HORA_DE_PESCAR_23nov/app)
WORK = Path(os.environ.get('DATA_DIR') or PESCA.parent) / 'pesca'
DOMINIO = os.environ.get('HDP_DOMINIO', '').strip().lower().removeprefix('https://').removeprefix('www.').strip('/')
PUBLIC = os.environ.get('VITRINE_PUBLIC_BASE', 'https://www.4kitem.com.br').rstrip('/')
SITE_URL = (os.environ.get('HDP_SITE_URL') or (f'https://{DOMINIO}' if DOMINIO else f'{PUBLIC}/fish')).rstrip('/')
CICLO = 2 * 3600            # site novo a cada 2 h
ESPERA_FALHA = 30 * 60      # deu errado: tenta de novo em 30 min (não martela o NWS)

pesca_bp = Blueprint('pesca', __name__, url_prefix='/fish')

_AQUECENDO = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
              '<title>Best Time to Fish</title><body style="font-family:system-ui;padding:24px">'
              '<h1>Best Time to Fish</h1><p>Loading today\'s fishing times. Back in a few minutes.</p>')


def _atual():
    """Pasta do site no ar (a que o ponteiro ATUAL indica), ou None antes da 1ª rodada."""
    try:
        nome = (WORK / 'ATUAL').read_text(encoding='utf-8').strip()
    except OSError:
        return None
    d = WORK / nome
    return d if nome.startswith('site_') and d.is_dir() else None


def _host():
    return (request.host or '').split(':')[0].lower()


def _fora(caminho):
    """Endereço que o visitante vê: no domínio próprio a raiz; no 4kitem, /fish/."""
    return ('/' if _host() == DOMINIO else '/fish/') + caminho


@pesca_bp.route('/', defaults={'caminho': ''})
@pesca_bp.route('/<path:caminho>')
def pagina(caminho):
    if DOMINIO and _host() != DOMINIO:                       # endereço oficial é o domínio
        return redirect(f'https://{DOMINIO}/{caminho}', 301)
    site = _atual()
    if site is None:                                         # 1ª rodada ainda não terminou (~3 min após o 1º deploy)
        return Response(_AQUECENDO, 200, {'Content-Type': 'text/html; charset=utf-8',
                                          'X-Robots-Tag': 'noindex', 'Cache-Control': 'no-store'})
    alvo = caminho + 'index.html' if (caminho == '' or caminho.endswith('/')) else caminho
    cheio = safe_join(str(site), alvo)
    if cheio and alvo != '_headers' and os.path.isfile(cheio):
        return send_from_directory(str(site), alvo, max_age=600)
    if cheio and os.path.isfile(os.path.join(cheio, 'index.html')):   # /lake-fork -> /lake-fork/ (links relativos)
        return redirect(_fora(caminho + '/'), 301)
    resp = send_from_directory(str(site), '404.html', max_age=60)
    resp.status_code = 404
    return resp


class _HostPesca:
    """Domínio próprio -> /fish/…, sem mexer no app.py. www vira o domínio sem www (um endereço só)."""

    def __init__(self, wsgi):
        self.wsgi = wsgi

    def __call__(self, environ, start_response):
        h = (environ.get('HTTP_HOST') or '').split(':')[0].lower()
        if DOMINIO and h in (DOMINIO, 'www.' + DOMINIO):
            p = environ.get('PATH_INFO') or '/'
            if h != DOMINIO:
                return _wz_redirect(f'https://{DOMINIO}{p}', 301)(environ, start_response)
            environ['PATH_INFO'] = '/fish' + p
        return self.wsgi(environ, start_response)


@pesca_bp.record_once
def _instala_host(state):
    if DOMINIO:
        state.app.wsgi_app = _HostPesca(state.app.wsgi_app)


# ── Robô: site novo a cada 2 h ────────────────────────────────────────────────
def _idade():
    s = _atual()
    try:
        return time.time() - (s / 'versao.txt').stat().st_mtime
    except (OSError, TypeError):
        return float('inf')


def gera_agora():
    """Uma rodada completa num processo à parte. True = site novo no ar."""
    WORK.mkdir(parents=True, exist_ok=True)
    trava = WORK / 'robo.lock'
    try:
        if time.time() - trava.stat().st_mtime < 25 * 60:     # outra rodada em andamento (ou caiu há pouco)
            return False
    except OSError:
        pass
    trava.write_text(str(os.getpid()), encoding='utf-8')
    nome = 'site_' + time.strftime('%Y%m%d_%H%M%S')
    novo = WORK / nome
    env = dict(os.environ, HDP_SITE_URL=SITE_URL, HDP_WORK=str(WORK), HDP_SITE_DIR=str(novo), PYTHONIOENCODING='utf-8')
    cmd = [sys.executable, str(PESCA / 'atualiza.py')]
    if shutil.which('nice'):
        cmd = ['nice', '-n', '10'] + cmd                       # o PubShow tem prioridade na CPU
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(PESCA), env=env, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=20 * 60)
        ok = r.returncode == 0
        fim = (r.stdout or '')[-600:] + (r.stderr or '')[-600:]
    except subprocess.TimeoutExpired:
        ok, fim = False, 'passou de 20 min'
    finally:
        try:
            trava.unlink()
        except OSError:
            pass
    if not ok:
        shutil.rmtree(novo, ignore_errors=True)
        log.warning(f'[Pesca] rodada falhou em {time.time() - t0:.0f}s; o site anterior continua. Fim da saída: {fim}')
        return False
    tmp = WORK / 'ATUAL.tmp'
    tmp.write_text(nome, encoding='utf-8')
    os.replace(tmp, WORK / 'ATUAL')
    for d in sorted(WORK.glob('site_*'))[:-2]:                 # fica o site no ar e o anterior
        if d.name != nome:
            shutil.rmtree(d, ignore_errors=True)
    log.info(f'[Pesca] site novo no ar ({nome}, {time.time() - t0:.0f}s)')
    return True


def _robo():
    time.sleep(90)                                             # deixa o 4kitem subir primeiro
    while True:
        espera = 600
        try:
            if _idade() >= CICLO - 300 and not gera_agora():
                espera = ESPERA_FALHA
        except Exception as e:                                 # o robô nunca derruba o 4kitem
            log.warning(f'[Pesca] erro no robô: {e}')
            espera = ESPERA_FALHA
        time.sleep(espera)


def iniciar_robo_pesca():
    """Só no Railway (ou com HDP_ROBO=1): no teste local e no smoke test não sai buscando previsão."""
    no_railway = any(os.environ.get(k) for k in ('RAILWAY_ENVIRONMENT', 'RAILWAY_ENVIRONMENT_NAME', 'RAILWAY_PROJECT_ID'))
    liga = os.environ.get('HDP_ROBO', '1' if no_railway else '0')
    if liga != '1':
        return
    threading.Thread(target=_robo, daemon=True, name='pesca-robo').start()
    log.info(f'[Pesca] robô ligado: {SITE_URL} a cada 2 h')
