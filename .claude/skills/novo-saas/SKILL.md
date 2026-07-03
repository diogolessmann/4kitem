---
name: novo-saas
description: Gera o esqueleto de um SaaS novo no monolito 4kitem seguindo o molde SomaJa/SlotZap (blueprint + _db + rota no webhook Asaas global + paywall PIX). Use ao iniciar um SaaS novo.
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
---

## Objetivo
Criar o esqueleto do SaaS novo **$ARGUMENTS** no padrao do 4kitem, reusando o que ja existe.

## Regras inquebraveis
- NUNCA `git add -A`. `git add -p` + conferir `git diff --cached`. Commit so quando o Diogo pedir.
- RELER o trecho do `app.py` IMEDIATAMENTE antes de cada Edit (read-state invalida entre sessoes).
- REUSAR, nao recriar: helper Asaas/PIX, cliente Gemini, helpers Evolution (copiar inline), `afiliados_db.py`. Molde canonico = `somaja.py` + `somaja_db.py`.
- SQLite SEMPRE com `DATA_DIR` (volume Railway), nunca path fixo.
- `python -m py_compile` antes de terminar.

## Passos
1. Ler `somaja.py`, `somaja_db.py` e a rota do webhook Asaas GLOBAL no `app.py` (copiar imports, init_db, registro de blueprint, fluxo de assinatura/PIX).
2. Criar `$ARGUMENTS_db.py`: schema minimo (usuarios, assinaturas) com `DATA_DIR`; `init_db()` idempotente.
3. Criar `$ARGUMENTS.py`: Blueprint Flask `$ARGUMENTS`, rotas minimas (landing, painel, paywall); helper `_$ARGUMENTS_gemini` se usar IA.
4. `app.py` (Edit CIRURGICO, 1 hunk): registrar o blueprint + branch no webhook Asaas global pro `ext_ref` deste SaaS (ext_ref UNICO; idempotencia por payment_id; fail-closed via `ASAAS_WEBHOOK_TOKEN`).
5. Paywall: reusar o helper de cobranca PIX existente (NAO duplicar chave/endpoint). Preco: **plano unico** (mensal + anual no PIX) — perguntar o valor.
6. Afiliado: se o Diogo pedir, aplicar o padrao da skill `/plugar-afiliado`. Senao, deixar `# TODO afiliado`.
7. `python -m py_compile app.py $ARGUMENTS.py $ARGUMENTS_db.py` e reportar.

## Entregavel
Arquivos criados/editados + o hunk exato do `app.py` + resultado do py_compile + as rotas criadas.
