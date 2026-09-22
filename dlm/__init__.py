# -*- coding: utf-8 -*-
"""dlm — MOTOR DE INSTAGRAM do Grupo Lessmann dentro do 4kitem (migrado da Rádio em 20/set/2026).

Por que aqui: a Rádio SC News está sendo vendida (Gabriel, 10x R$900). O motor do despachante e o
motor de mobilidade moravam DENTRO do repo da Rádia — código não entra na venda.

Módulos (portados 1:1 do repo da Rádio, só os imports mudaram):
  series_despachante  banco "1 post = 1 página" do despachante (séries, URLs curtas, travas)
  marcas              carrossel do despachante + oferta de scooter (foto real) + publicação Meta
  mobilidade          3º perfil SC News Mobilidade (coleta por categoria, IA com fonte, collab)
  insights            placar por série/post (Graph API insights)
Shims (o que era gen_instagram/distribuidor/scraper/genericbg/cerebro na Rádio):
  gen_instagram (fontes/PIL) · distribuidor (env, Graph API, story) · scraper (RSS text helpers) ·
  genericbg (arsenal dlm/bg) · cerebro (Gemini → Groq)

Agenda: dlmotor.py (thread própria, America/Sao_Paulo). Estado/log em DATA_DIR.
"""
