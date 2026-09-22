# -*- coding: utf-8 -*-
"""Shim do cerebro da Rádio: completar(prompt) = Gemini (centavos) -> Groq (grátis) -> None."""
import os
import re

import requests

from dlm.distribuidor import _env

GEMINI_API_KEY = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
GEMINI_MODEL = _env("DLM_GEMINI_MODEL", _env("GEMINI_MODEL", "gemini-2.5-flash"))
GROQ_API_KEY = _env("GROQ_API_KEY")
GROQ_MODEL = _env("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _mask(msg):
    return re.sub(r"(key=|Bearer\s+)[A-Za-z0-9._-]+", r"\1***", str(msg))


def _gemini(prompt, model=None):
    if not GEMINI_API_KEY:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model or GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    cfg = {"temperature": 0.4, "maxOutputTokens": 1024}
    tentativas = [
        {"contents": [{"parts": [{"text": prompt}]}],
         "generationConfig": {**cfg, "thinkingConfig": {"thinkingBudget": 0}}},
        {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": cfg},
    ]
    for body in tentativas:
        try:
            r = requests.post(url, headers={"Content-Type": "application/json"}, json=body, timeout=40)
            r.raise_for_status()
            cand = (r.json().get("candidates") or [{}])[0]
            if cand.get("finishReason") == "MAX_TOKENS":
                continue
            txt = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])).strip()
            if txt:
                return txt
        except Exception as e:
            print(f"[dlm.cerebro] Gemini falhou: {_mask(e)}")
    return None


def _groq(prompt):
    if not GROQ_API_KEY:
        return None
    try:
        r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                          json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
                                "temperature": 0.4, "max_tokens": 600}, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[dlm.cerebro] Groq falhou: {_mask(e)}")
        return None


def disponiveis():
    return {"gemini": bool(GEMINI_API_KEY), "groq": bool(GROQ_API_KEY)}


def completar(prompt, brain="auto", model=None):
    ordem = [brain] if brain in ("gemini", "groq") else ["gemini", "groq"]
    for nome in ordem:
        out = _gemini(prompt, model) if nome == "gemini" else _groq(prompt)
        if out:
            return out.strip()
    return None
