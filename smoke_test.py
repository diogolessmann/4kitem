#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
smoke_test.py — Rede de segurança do 4KITEM.

Roda verificações rápidas que pegam as quebras mais comuns (especialmente as
causadas por edição concorrente do app.py):

  1. O app.py importa sem erro
  2. TODOS os templates parseiam (pega {% %} quebrado, como o admin_bar)
  3. TODA rota GET sem parâmetro responde < 500 (pega crash em view function)

Uso:
    python smoke_test.py            # roda tudo
    python smoke_test.py -q         # só mostra falhas

Sai com código 1 se algo falhar (dá pra usar antes de commit/deploy).
"""
import os
import sys
import importlib.util

try:                       # Windows: garante saída UTF-8 (emoji/acentos)
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

QUIET = '-q' in sys.argv
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

# Rotas GET sem-parâmetro que podem legitimamente dar 500 fora de contexto
# (ex.: dependem de integração externa). Adicione aqui se houver falso-positivo.
IGNORE = set()

failures = []


def ok(msg):
    if not QUIET:
        print(f'  [PASS]  {msg}')


def fail(msg):
    failures.append(msg)
    print(f'  [FAIL]  {msg}')


print('\n🧪 SMOKE TEST 4KITEM\n' + '─' * 50)

# ── 1. Importa o app ──────────────────────────────────────────────────────────
print('\n[1] Importando app.py...')
try:
    spec = importlib.util.spec_from_file_location('app_under_test', 'app.py')
    app_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app_mod)
    app = app_mod.app
    ok('app.py importou e registrou as rotas')
except Exception as e:
    print(f'  [FAIL]  app.py NAO importa: {e}')
    print('\n❌ Erro fatal — abortando.\n')
    sys.exit(1)

# ── 2. Todos os templates parseiam ────────────────────────────────────────────
print('\n[2] Parseando templates...')
from jinja2 import TemplateSyntaxError  # noqa: E402
tpl_total = tpl_bad = 0
for r, _, files in os.walk('templates'):
    for f in files:
        if not f.endswith('.html'):
            continue
        rel = os.path.relpath(os.path.join(r, f), 'templates').replace(os.sep, '/')
        tpl_total += 1
        try:
            app.jinja_env.get_template(rel)
        except TemplateSyntaxError as e:
            tpl_bad += 1
            fail(f'template {rel} (linha {e.lineno}): {e.message}')
        except Exception:
            pass  # erro de runtime (variavel) nao conta aqui — so sintaxe
if tpl_bad == 0:
    ok(f'{tpl_total} templates parseiam sem erro de sintaxe')

# ── 3. Toda rota GET sem-parâmetro responde < 500 ─────────────────────────────
print('\n[3] Testando rotas GET (sem parâmetro)...')
app.config['TESTING'] = True
client = app.test_client()
rotas = []
for rule in app.url_map.iter_rules():
    if 'GET' not in (rule.methods or set()):
        continue
    if rule.arguments:           # pula rotas com <param>
        continue
    if rule.rule.startswith('/static'):
        continue
    if rule.rule in IGNORE:
        continue
    rotas.append(rule.rule)

rotas = sorted(set(rotas))
erros_rota = 0
for url in rotas:
    try:
        resp = client.get(url)
        if resp.status_code >= 500:
            erros_rota += 1
            fail(f'GET {url} -> {resp.status_code}')
    except Exception as e:
        erros_rota += 1
        fail(f'GET {url} -> EXCEÇÃO {type(e).__name__}: {e}')
if erros_rota == 0:
    ok(f'{len(rotas)} rotas GET responderam < 500')

# ── Resultado ─────────────────────────────────────────────────────────────────
print('\n' + '─' * 50)
if failures:
    print(f'❌ {len(failures)} FALHA(S):')
    for fmsg in failures:
        print('   • ' + fmsg)
    print()
    sys.exit(1)
print('✅ TUDO OK — app, templates e rotas saudáveis.\n')
sys.exit(0)
