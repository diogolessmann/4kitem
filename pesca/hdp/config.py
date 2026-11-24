"""Caminhos e constantes do Hora de Pescar (protótipo local, v1 Texas)."""
import os as _os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"                      # entradas fixas (places.json, tpwd_lakes.json)
# No 4kitem o que é gerado mora no volume (HDP_WORK = DATA_DIR/pesca) e cada rodada grava num site novo
# (HDP_SITE_DIR); sem as variáveis fica tudo dentro da pasta do projeto, como no teste local e no GitHub.
WORK = Path(_os.environ["HDP_WORK"]) if _os.environ.get("HDP_WORK") else DATA
CACHE = WORK / "cache"
OUT = WORK / "out"
EPHEM = WORK                              # de421.bsp (17 MB): baixa sozinho na 1ª rodada se faltar
SITE = Path(_os.environ["HDP_SITE_DIR"]) if _os.environ.get("HDP_SITE_DIR") else ROOT / "site"
TEMPLATES = ROOT / "templates"
FICHAS = ROOT / "fichas"                 # fresh.json + salt.json (fichas com fonte)

TZ = ZoneInfo("America/Chicago")          # todos os 20 lugares estão no horário central
# NWS pede User-Agent com contato; por decisão da missão, contato genérico (sem o e-mail do Diogo).
USER_AGENT = "HoraDePescarPrototype/0.1 (local test build)"
NOAA_APP = "HoraDePescarPrototype"
DAYS = 8                                  # hoje + 7 dias

# Preço do Pro (decisão do Diogo em 23/11/2026). Fica só aqui: nenhuma página escreve preço fixo no texto.
# A v1 ainda não mostra paywall; quando mostrar, ler daqui. Cobrança pelo Paddle (conta é do Diogo).
PRICE = {"currency": "USD", "annual": 19.99, "monthly": 2.99}
SLOT_MIN = 10                             # resolução da nota: 10 minutos
# Endereço do site publicado (canonical, sitemap). Vem da variável HDP_SITE_URL no CI; vazio = prévia local.
SITE_URL = _os.environ.get("HDP_SITE_URL", "").rstrip("/")
if SITE_URL:   # publicado: o contato do User-Agent é o endereço do site (o NWS aceita site ou e-mail)
    USER_AGENT = f"BestTimeToFish/1.0 (+{SITE_URL})"
