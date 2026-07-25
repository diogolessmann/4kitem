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


# ── Tokens (app via client_credentials + USUÁRIO via authorization_code) ───────
# 25/jun: o ML cortou o acesso do token de app puro ("access not granted by
# applications" — enforcement do PolicyAgent). O caminho oficial é o GRANT de
# usuário: o dono autoriza 1x no navegador e usamos o token DELE (com refresh).
REDIRECT_URI = os.environ.get('ML_REDIRECT_URI', 'https://4kitem.com.br/mlhype/oauth/callback')
AUTH_URL     = 'https://auth.mercadolivre.com.br/authorization'


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


def _salvar_token(access_token, expires_in, escopo='ml_app', refresh_token=None,
                  ml_user_id=None):
    expira = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
    agora = datetime.utcnow().isoformat()
    conn = get_mlhype_db()
    row = conn.execute("SELECT id FROM mlhype_ml_tokens WHERE escopo=?", (escopo,)).fetchone()
    campos = {'access_token': _enc(access_token), 'expires_at': expira, 'updated_at': agora}
    if refresh_token is not None:
        campos['refresh_token'] = _enc(refresh_token)
    if ml_user_id is not None:
        campos['ml_user_id'] = str(ml_user_id)
    if row:
        sets = ', '.join(f'{k}=?' for k in campos)
        conn.execute(f"UPDATE mlhype_ml_tokens SET {sets} WHERE id=?",
                     (*campos.values(), row['id']))
    else:
        cols = ', '.join(['escopo', *campos.keys()])
        ph = ', '.join(['?'] * (1 + len(campos)))
        conn.execute(f"INSERT INTO mlhype_ml_tokens ({cols}) VALUES ({ph})",
                     (escopo, *campos.values()))
    conn.commit()
    conn.close()
    return expira


def _ler_token(escopo='ml_app'):
    conn = get_mlhype_db()
    row = conn.execute("SELECT access_token, refresh_token, expires_at FROM mlhype_ml_tokens "
                       "WHERE escopo=?", (escopo,)).fetchone()
    conn.close()
    if not row:
        return None, None, None
    return _dec(row['access_token']), _dec(row['refresh_token']), row['expires_at']


# ── OAuth de USUÁRIO (authorization_code + refresh) ────────────────────────────
def url_autorizacao(state=''):
    """Link que o dono abre 1x pra autorizar o app com a conta ML dele."""
    from urllib.parse import urlencode
    return AUTH_URL + '?' + urlencode({
        'response_type': 'code', 'client_id': APP_ID,
        'redirect_uri': REDIRECT_URI, 'state': state})


def trocar_code_por_token(code):
    """Callback do OAuth: troca o code pelo par access+refresh e salva (ml_user)."""
    r = requests.post(f'{API_BASE}/oauth/token',
                      headers={'accept': 'application/json'},
                      data={'grant_type': 'authorization_code',
                            'client_id': APP_ID, 'client_secret': APP_SECRET,
                            'code': code, 'redirect_uri': REDIRECT_URI},
                      timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        raise MLApiError(r.status_code, _safe_json(r), '/oauth/token(code)')
    j = r.json()
    _salvar_token(j['access_token'], int(j.get('expires_in', 21600)),
                  escopo='ml_user', refresh_token=j.get('refresh_token'),
                  ml_user_id=j.get('user_id'))
    return j.get('user_id')


def _renovar_token_user(refresh):
    """refresh_token do ML é de USO ÚNICO — a resposta traz um novo par; salvamos."""
    r = requests.post(f'{API_BASE}/oauth/token',
                      headers={'accept': 'application/json'},
                      data={'grant_type': 'refresh_token',
                            'client_id': APP_ID, 'client_secret': APP_SECRET,
                            'refresh_token': refresh},
                      timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        raise MLApiError(r.status_code, _safe_json(r), '/oauth/token(refresh)')
    j = r.json()
    _salvar_token(j['access_token'], int(j.get('expires_in', 21600)),
                  escopo='ml_user', refresh_token=j.get('refresh_token'),
                  ml_user_id=j.get('user_id'))
    return j['access_token']


def tem_grant_usuario():
    tok, refresh, _ = _ler_token('ml_user')
    return bool(tok or refresh)


def obter_token(forcar=False):
    """Token válido pra chamar a API. PREFERE o token de USUÁRIO (grant — o que
    o ML aceita hoje); cai pro token de app se não houver grant. Margem 5 min."""
    if not credenciais_ok():
        raise MLApiError(0, 'sem ML_APP_ID/ML_SECRET no ambiente', '/oauth/token')

    # 1) token de USUÁRIO (se o dono já autorizou)
    tok, refresh, expira = _ler_token('ml_user')
    if tok or refresh:
        if tok and expira and not forcar:
            try:
                if datetime.fromisoformat(expira) - timedelta(minutes=5) > datetime.utcnow():
                    return tok
            except Exception:
                pass
        if refresh:
            try:
                return _renovar_token_user(refresh)
            except Exception as e:
                log.warning(f'[MLhype] refresh do token de usuário falhou: {e}')
        if tok and not forcar:
            return tok                      # tenta com o que tem; 401 força refresh

    # 2) fallback: token de app (client_credentials)
    if not forcar:
        tok, _, expira = _ler_token('ml_app')
        if tok and expira:
            try:
                if datetime.fromisoformat(expira) - timedelta(minutes=5) > datetime.utcnow():
                    return tok
            except Exception:
                pass
    access_token, expires_in = _solicitar_token_app()
    _salvar_token(access_token, expires_in, escopo='ml_app')
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


_TAXA_CACHE = {}


def taxas_ml(preco, categoria_id):
    """Taxa de venda REAL do ML p/ um preço+categoria (endpoint oficial
    /sites/MLB/listing_prices). Devolve {'classico': {pct, fixed, fee},
    'premium': {...}} ou None. Cacheado por faixa de R$25 (a comissão muda pouco
    com o preço; os custos fixos mudam por faixa)."""
    try:
        preco = float(preco)
    except (TypeError, ValueError):
        return None
    if preco <= 0 or not categoria_id:
        return None
    chave = (categoria_id, int(preco // 25))
    if chave in _TAXA_CACHE:
        return _TAXA_CACHE[chave]
    try:
        data = ml_get('/sites/MLB/listing_prices',
                      {'price': round(preco, 2), 'category_id': categoria_id})
    except MLApiError:
        return None
    mapa = {'gold_special': 'classico', 'gold_pro': 'premium'}
    out = {}
    for lt in (data or []):
        key = mapa.get(lt.get('listing_type_id'))
        if key:
            det = lt.get('sale_fee_details') or {}
            out[key] = {'pct': det.get('percentage_fee'),
                        'fixed': det.get('fixed_fee', 0) or 0,
                        'fee': lt.get('sale_fee_amount')}
    resultado = out or None
    if len(_TAXA_CACHE) < 5000:
        _TAXA_CACHE[chave] = resultado
    return resultado
