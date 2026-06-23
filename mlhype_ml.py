"""
mlhype_ml.py — Conector da API oficial do Mercado Livre (Passo 2).

DESCOBERTA (sondagem 22/jun/2026): a busca antiga /sites/MLB/search está MORTA
(403), mas os endpoints modernos (/trends, /highlights, /products, /categories)
funcionam com TOKEN DE APP via client_credentials. Ou seja, para o Radar NÃO
precisamos do fluxo "autorizar no navegador" (authorization_code) — o token de
app já é o "token de plataforma" (decisão R5). Se um dia precisarmos de dados
PRIVADOS do usuário (pedidos/métricas dele), aí sim entra o authorization_code.

Este módulo:
  - obtém e cacheia o token de app (client_credentials), renovando ao expirar;
  - guarda o token CRIPTOGRAFADO em repouso (Fernet, derivado de MLHYPE_TOKEN_KEY);
  - expõe um cliente HTTP central (ml_get) que injeta o Bearer, trata 401/403
    (renova token + 1 retry) e respeita rate-limit (429) com backoff + jitter.
"""
import os
import time
import base64
import random
import hashlib
import logging
from datetime import datetime, timedelta

import requests

from mlhype_db import get_mlhype_db

log = logging.getLogger(__name__)


# ── carrega .env local (dev) sem sobrescrever env já definido (no Railway as
#    variáveis já vêm setadas e o setdefault não as toca) ───────────────────────
def _carregar_env_local():
    p = os.path.join(os.path.dirname(__file__), '.env')
    if not os.path.exists(p):
        return
    try:
        for linha in open(p, encoding='utf-8'):
            linha = linha.strip()
            if linha and not linha.startswith('#') and '=' in linha:
                k, v = linha.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


_carregar_env_local()

API_BASE     = 'https://api.mercadolibre.com'
APP_ID       = os.environ.get('ML_APP_ID', '')
APP_SECRET   = os.environ.get('ML_SECRET', '')
SITE         = os.environ.get('MLHYPE_SITE', 'MLB')      # MLB = Brasil
HTTP_TIMEOUT = int(os.environ.get('MLHYPE_HTTP_TIMEOUT', '30'))


class MLApiError(Exception):
    """Falha persistente numa chamada ao ML (após os retries previstos)."""
    def __init__(self, status, body, path):
        self.status = status
        self.body = body
        self.path = path
        super().__init__(f'ML {status} em {path}: {str(body)[:160]}')


def credenciais_ok():
    return bool(APP_ID and APP_SECRET)


# ── Criptografia dos tokens em repouso (Fernet) ────────────────────────────────
def _fernet():
    key = os.environ.get('MLHYPE_TOKEN_KEY', '')
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        # deriva uma chave Fernet válida (32 bytes base64) da MLHYPE_TOKEN_KEY
        fk = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
        return Fernet(fk)
    except Exception as e:  # pragma: no cover
        log.warning(f'[MLhype] Fernet indisponível ({e}); token ficará sem cripto')
        return None


def _enc(texto):
    f = _fernet()
    if not f or texto is None:
        return texto
    return 'enc:' + f.encrypt(texto.encode()).decode()


def _dec(texto):
    if texto is None:
        return None
    if not texto.startswith('enc:'):
        return texto                 # compat: token salvo sem cripto
    f = _fernet()
    if not f:
        return None
    try:
        return f.decrypt(texto[4:].encode()).decode()
    except Exception:
        return None


# ── Token de app (client_credentials) ──────────────────────────────────────────
def _solicitar_token_app():
    r = requests.post(f'{API_BASE}/oauth/token',
                      headers={'accept': 'application/json'},
                      data={'grant_type': 'client_credentials',
                            'client_id': APP_ID, 'client_secret': APP_SECRET},
                      timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        raise MLApiError(r.status_code, _safe_json(r), '/oauth/token')
    j = r.json()
    return j['access_token'], int(j.get('expires_in', 21600))


def _salvar_token(access_token, expires_in):
    expira = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
    agora = datetime.utcnow().isoformat()
    conn = get_mlhype_db()
    row = conn.execute("SELECT id FROM mlhype_ml_tokens WHERE escopo='ml_app'").fetchone()
    if row:
        conn.execute("UPDATE mlhype_ml_tokens SET access_token=?, expires_at=?, updated_at=? WHERE id=?",
                     (_enc(access_token), expira, agora, row['id']))
    else:
        conn.execute("INSERT INTO mlhype_ml_tokens (escopo, access_token, expires_at, updated_at) "
                     "VALUES ('ml_app',?,?,?)", (_enc(access_token), expira, agora))
    conn.commit()
    conn.close()
    return expira


def _ler_token():
    conn = get_mlhype_db()
    row = conn.execute("SELECT access_token, expires_at FROM mlhype_ml_tokens "
                       "WHERE escopo='ml_app'").fetchone()
    conn.close()
    if not row:
        return None, None
    return _dec(row['access_token']), row['expires_at']


def obter_token(forcar=False):
    """Token de app válido. Cacheia no banco e renova ao expirar (margem 5 min)."""
    if not credenciais_ok():
        raise MLApiError(0, 'sem ML_APP_ID/ML_SECRET no ambiente', '/oauth/token')
    if not forcar:
        tok, expira = _ler_token()
        if tok and expira:
            try:
                if datetime.fromisoformat(expira) - timedelta(minutes=5) > datetime.utcnow():
                    return tok
            except Exception:
                pass
    access_token, expires_in = _solicitar_token_app()
    _salvar_token(access_token, expires_in)
    return access_token


# ── Cliente HTTP central ───────────────────────────────────────────────────────
def _safe_json(r):
    try:
        return r.json()
    except Exception:
        return r.text[:200]


def _backoff(n):
    return min(8.0, 2.0 ** n) + random.uniform(0, 0.5)


def ml_get(path, params=None, _max_tentativas=5):
    """GET autenticado na API do ML.
    - injeta o Bearer do token de app;
    - 401/403: renova o token e tenta UMA vez (token pode ter expirado/invalidado);
    - 429: backoff exponencial + jitter e retenta;
    - falha persistente: levanta MLApiError (não derruba o app)."""
    token = obter_token()
    renovou = False
    for tentativa in range(_max_tentativas):
        try:
            r = requests.get(API_BASE + path, params=params,
                             headers={'Authorization': f'Bearer {token}'},
                             timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            if tentativa < _max_tentativas - 1:
                time.sleep(_backoff(tentativa)); continue
            raise MLApiError(0, repr(e), path)

        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403) and not renovou:
            token = obter_token(forcar=True)
            renovou = True
            continue
        if r.status_code == 429 and tentativa < _max_tentativas - 1:
            time.sleep(_backoff(tentativa)); continue
        raise MLApiError(r.status_code, _safe_json(r), path)

    raise MLApiError(0, 'falha após retries', path)


# ── Helpers de alto nível (usados pelo coletor — Passo 3 — e pelos agentes) ─────
def categorias():
    """Categorias-raiz do site (lista)."""
    return ml_get(f'/sites/{SITE}/categories')


def categoria(cat_id):
    """Detalhe de uma categoria (inclui total_items_in_this_category, path_from_root)."""
    return ml_get(f'/categories/{cat_id}')


def highlights(cat_id):
    """Mais vendidos de uma categoria: content = [{id, position, type}] (o Top N)."""
    return ml_get(f'/highlights/{SITE}/category/{cat_id}')


def produto(pid):
    """Produto de catálogo: nome, atributos, pickers, status."""
    return ml_get(f'/products/{pid}')


def ofertas_produto(pid, limit=50):
    """Ofertas (vendedores) de um produto de catálogo: price, seller_id, item_id…
    A 1ª oferta costuma ser a vencedora/mais barata (o 'líder' a desbancar)."""
    return ml_get(f'/products/{pid}/items', {'limit': limit})


def trends():
    """Termos em alta no site: [{keyword, url}]."""
    return ml_get(f'/trends/{SITE}')


def buscar_produtos(q, limit=20):
    """Busca no catálogo de produtos (substitui a /search antiga)."""
    return ml_get('/products/search', {'site_id': SITE, 'status': 'active', 'q': q, 'limit': limit})


def item(item_id):
    """Detalhe de um anúncio específico (título, fotos, atributos, garantia…).
    Usado pelo Dissecador p/ ler o anúncio do líder. Degrada se o endpoint barrar."""
    try:
        return ml_get(f'/items/{item_id}')
    except MLApiError as e:
        log.info(f'[MLhype] /items/{item_id} indisponível ({e.status})')
        return None
