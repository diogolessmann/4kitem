# CLAUDE.md — 4kitem

Monólito Flask, deploy no Railway no **`git push` para `master`**. `app.py` (~780KB) hospeda **~22 SaaS como blueprints**, cada um com seus módulos `<nome>.py` / `<nome>_db.py`.

## ⚠️ REGRA DE OURO — commit cirúrgico (VÁRIAS sessões editam este repo ao mesmo tempo)
- **NUNCA** use `git add -A`, `git add .` ou `git commit -am`. **SEMPRE `git add -p`** (ou `git add <arquivos específicos>`).
- Antes de QUALQUER commit: rode `git status` + `git diff --cached` e confirme que **só os SEUS hunks** entraram. O `app.py` quase sempre tem hunks de OUTRA sessão — não os arraste.
- Ao editar `app.py`: **leia o trecho imediatamente antes de cada Edit** (outra sessão pode ter salvado e invalidado o read-state → erro "File has not been read yet").
- Commit/push **só quando o Diogo pedir**.

## Smoke antes de propor push
- `python -m py_compile app.py <modulo>.py <modulo>_db.py` → zero erro. (Há um pre-push hook de smoke como última linha de defesa.)

## Padrão de SaaS novo (molde canônico = `somaja.py` / `somaja_db.py`)
1. `<nome>_db.py`: schema + `init_db()` idempotente, **SQLite com `DATA_DIR`** (volume Railway) — nunca path fixo.
2. `<nome>.py`: Blueprint Flask (landing, painel, paywall); helper Gemini se usar IA.
3. `app.py` (Edit cirúrgico, 1 hunk): registrar o blueprint + adicionar branch no **webhook Asaas GLOBAL** com `ext_ref` único.
4. **REUSAR, não recriar:** helper de cobrança Asaas/PIX, `afiliados_db.py` (`registrar_comissao`, dedupe `UNIQUE(app, payment_id)`), cliente Gemini, helpers do Evolution (copiar inline — não há lib compartilhada).
5. Webhook Asaas é **fail-closed** (exige `ASAAS_WEBHOOK_TOKEN`; sem env → 401/503).

## Stack
Evolution API (WhatsApp, instância por cliente) · Gemini Flash (IA, custo centavos) · Asaas (PIX; anual no PIX = taxa fixa vira ~1%) · SQLite + `DATA_DIR` · `.python-version` fixo.

## Ambiente
Windows: PowerShell + git-bash (Bash tool). Memória persistente do Claude em `~/.claude/.../memory` (o `MEMORY.md` é o índice — consultar/atualizar).
