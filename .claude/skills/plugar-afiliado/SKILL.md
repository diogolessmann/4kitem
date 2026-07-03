---
name: plugar-afiliado
description: Pluga um SaaS do 4kitem no motor de afiliados (migracao afiliado_ref + captura ?ref no cadastro + credito no webhook Asaas, idempotente). Use ao adicionar comissao de afiliado a um app existente.
allowed-tools: Read, Grep, Glob, Edit, Bash
---

## Objetivo
Plugar o app **$ARGUMENTS** no programa de afiliados do 4kitem, seguindo o padrao JA VALIDADO em 12 apps (DefesaPro = molde de referencia).

## Regras inquebraveis (4kitem)
- NUNCA `git add -A`. Use `git add -p` + conferir `git diff --cached`.
- RELER o trecho do `app.py` IMEDIATAMENTE antes de cada Edit (read-state invalida entre sessoes — ja causou edit perdido/falha "File has not been read yet").
- NAO editar `afiliados.py` nem `somaja_*` (sessao paralela do SomaJa pode clobar). O motor `registrar_comissao` JA tem anti-autoindicacao central — nao mexer.
- `python -m py_compile` antes de propor push. Commit so quando o Diogo pedir.

## O padrao (5 partes)
1. **Chave no APPS**: confirmar que `$ARGUMENTS` existe em `APPS` (afiliados.py) com a comissao FIXA em R$. Se nao existir, PARAR e perguntar o valor.
2. **Migracao `afiliado_ref`**: `ALTER TABLE <tabela_users> ADD COLUMN afiliado_ref TEXT DEFAULT ''` na lista de migracoes (saas_db.py OU o `<app>_db.py` — descobrir onde a tabela vive).
3. **Captura ?ref na landing**: `_ref = (request.args.get('ref') or '').strip().upper()[:12]` → `if _ref: session['<app>_ref'] = _ref`.
4. **Grava no cadastro**: incluir `afiliado_ref` no INSERT, valor = `(session.get('<app>_ref') or request.args.get('ref') or '').strip().upper()[:12] or None`.
5. **Credita no webhook**: no bloco do webhook Asaas (global em app.py `elif ref.startswith('<app>_'):` OU webhook proprio do modulo), ao ATIVAR: incluir `afiliado_ref` no SELECT e, se setado:
   `from afiliados import registrar_comissao` →
   `registrar_comissao(u['afiliado_ref'], '<APPKEY>', (payload.get('payment') or {}).get('id',''), nome, cliente_email=email, cliente_cpf=cpf)` dentro de `try/except log.warning`. Idempotente por `UNIQUE(app, payment_id)`.

## Execucao
1. Mapear (Grep): tabela de usuarios, rota da landing, INSERT do cadastro, bloco do webhook.
2. Aplicar as 5 partes (Edit CIRURGICO, relendo o app.py antes de cada edit).
3. CUIDADO `sqlite3.Row`: usar indexacao `u['col']`, NUNCA `u.get()` (Row nao tem .get — ja foi bug).
4. Testar: criar afiliado + user com afiliado_ref + asaas_customer_id (custid SEM underscore) + POST `/webhook/asaas` (header `asaas-access-token`) → conferir `afiliado_conversoes` (valor = comissao do APPS; status 'erro' e ESPERADO sem ASAAS_API_KEY real). Molde: `C:\Users\Diogo\AppData\Local\Temp\test_afil_*.py`.
5. `py_compile` dos arquivos tocados.

## Entregavel
Arquivos editados + valor da comissao + resultado do teste (conversao registrada) + py_compile.
