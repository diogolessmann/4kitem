"""arena_seguranca.py — cinto de segurança do Arena (o app que mexe com dinheiro).

Fecha as portas baratas SEM dependência nova:
  - rate-limit em memória (deploy = gunicorn --workers 1 => dict compartilhado);
  - honeypot de formulário (bot preenche campo escondido, humano não);
  - trava de força-bruta de login (bloqueia após N erros).

Só cobre as PORTAS DE ENTRADA (cadastro/login/checkout/saque/criar-duelo/
entrar-mesa). O loop de jogo (streaming peça-a-peça) já é server-authoritative
e NÃO é limitado aqui, senão travaria a partida. Nada de dinheiro/aposta aqui.
"""
import time
import threading
from functools import wraps
from flask import request, jsonify

_LOCK = threading.RLock()
_HITS = {}          # chave -> [timestamps]  (janela deslizante)
_FAILS = {}         # chave -> (contador, bloqueado_ate_ts)  (login)

HONEYPOT_FIELD = 'website'   # campo escondido no form; se vier preenchido = bot


def client_ip():
    """IP real atrás do proxy do Railway (mesmo padrão já usado no app.py)."""
    return (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip() or '?'


def _janela(lst, janela_s, agora):
    return [t for t in lst if agora - t < janela_s]


def rate_ok(chave, limite, janela_s):
    """True se ainda pode; False se estourou o limite na janela. Registra o hit se ok."""
    agora = time.time()
    with _LOCK:
        lst = _janela(_HITS.get(chave, []), janela_s, agora)
        if len(lst) >= limite:
            _HITS[chave] = lst
            return False
        lst.append(agora)
        _HITS[chave] = lst
        if len(_HITS) > 20000:      # limpeza preguiçosa anti-leak de RAM
            _prune(agora)
        return True


def _prune(agora):
    for k in list(_HITS.keys()):
        v = _janela(_HITS[k], 3600, agora)
        if v:
            _HITS[k] = v
        else:
            _HITS.pop(k, None)


# ── força-bruta de login ──
def login_bloqueado_seg(chave):
    """Segundos restantes de bloqueio (0 = liberado). NÃO apaga o contador de erros
    que ainda está acumulando (só limpa quando o lock de fato expira)."""
    with _LOCK:
        c = _FAILS.get(chave)
        if not c:
            return 0
        _, ate = c
        if not ate:                       # tem erros acumulando, mas sem lock ativo
            return 0
        if time.time() >= ate:            # o lock expirou
            _FAILS.pop(chave, None)
            return 0
        return int(ate - time.time())


def login_falhou(chave, max_tentativas=6, bloqueio_s=600):
    with _LOCK:
        cnt, ate = _FAILS.get(chave, (0, 0.0))
        if ate and time.time() >= ate:    # lock anterior expirou -> recomeça a contagem
            cnt = 0
            ate = 0.0
        cnt += 1
        if cnt >= max_tentativas:
            ate = time.time() + bloqueio_s
        _FAILS[chave] = (cnt, ate)


def login_ok(chave):
    with _LOCK:
        _FAILS.pop(chave, None)


# ── honeypot ──
def honeypot_falhou(form):
    """True se o campo-armadilha veio preenchido (= bot)."""
    return bool((form.get(HONEYPOT_FIELD) or '').strip())


def honeypot_html():
    """Campo escondido pra colar no template (o macro do form)."""
    return ('<input type="text" name="%s" value="" tabindex="-1" autocomplete="off" '
            'style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0" aria-hidden="true">'
            % HONEYPOT_FIELD)


# ── decorator p/ rotas JSON (jogo valendo / mesa) ──
def limite_json(limite, janela_s):
    """Rate-limit por IP+rota pra endpoints que respondem JSON. 429 JSON se estourar."""
    def deco(f):
        @wraps(f)
        def wrap(*a, **k):
            if not rate_ok('j:' + f.__name__ + ':' + client_ip(), limite, janela_s):
                return jsonify({'ok': False, 'erro': 'Devagar aí — tente de novo em instantes.'}), 429
            return f(*a, **k)
        return wrap
    return deco
