#!/usr/bin/env bash
# 4kitem — lembrete no inicio/compactacao da sessao (reinjeta a regra que o Claude esquece).
echo "[4kitem] branch: $(git branch --show-current 2>/dev/null) | app.py ~22 blueprints; VARIAS sessoes simultaneas."
echo "[REGRA] git add -p SEMPRE (NUNCA -A/.). Conferir git diff --cached. Reler o trecho do app.py antes de cada Edit. py_compile antes de push."
git status --porcelain 2>/dev/null | head -10
exit 0
