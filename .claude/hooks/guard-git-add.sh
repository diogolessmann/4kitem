#!/usr/bin/env bash
# 4kitem — bloqueia 'git add -A/./--all' e 'commit -a' (commit cirurgico obrigatorio;
# varias sessoes editam app.py ao mesmo tempo). Parser em Python (git-bash nao tem jq).
python -c '
import sys, json, re
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
cmd = (d.get("tool_input") or {}).get("command", "") or ""
bad_add    = re.search(r"\bgit\s+add\s+(?:[^;&|]*\s)?(-A\b|--all\b|\.)(\s|$)", cmd)
bad_commit = re.search(r"\bgit\s+commit\s+[^;&|]*-[A-Za-z]*a", cmd)
if bad_add or bad_commit:
    print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"git add -A/./--all (ou commit -a) PROIBIDO no 4kitem: app.py costuma ter hunks de OUTRAS sessoes. Use git add -p ou git add <arquivos especificos> e confira git diff --cached antes de commitar."}}))
'
exit 0
