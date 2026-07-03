#!/usr/bin/env bash
# 4kitem — roda py_compile no arquivo .py recem-editado; exit 2 devolve o erro ao Claude
# no mesmo turno (ele conserta antes de seguir). Primeira linha de defesa (pre-push = ultima).
fp=$(python -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); sys.exit(0)
print((d.get("tool_input") or {}).get("file_path", "") or "")
')
case "$fp" in
  *.py)
    if ! out=$(python -m py_compile "$fp" 2>&1); then
      echo "[4kitem hook] SMOKE py_compile FALHOU em $fp:" >&2
      echo "$out" >&2
      exit 2
    fi ;;
esac
exit 0
