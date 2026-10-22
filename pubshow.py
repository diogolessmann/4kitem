"""
pubshow.py — Blueprint PUBSHOW
Jukebox digital 2.0 para bares, pubs e estabelecimentos
"""
import base64
import hmac
import io
import logging
import math
import os
import random
import re
import secrets
import string
import unicodedata
import time as _time
import uuid
import threading as _threading
import requests as _requests
from datetime import datetime, timedelta
from functools import wraps
from html import escape as _html_escape
from flask import (Blueprint, render_template, redirect, request,
                   session, jsonify, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash
from pubshow_db import get_pubshow_db, init_pubshow_db, STATUS_PAGOS
from pubshow_db import marcar_fundador, fundador_vagas as _fundador_vagas_db   # Lote D A1 (16/10/26)
from pubshow_db import FUNDADOR_VAGAS, FUNDADOR_MESES   # Lote D B (16/10/26): a oferta do site
from pubshow_db import SHOWS_FORA   # 17/10/26: o grupo Shows saiu (o admin não recria vídeo nessas categorias)
import pubshow_estilo   # "aqui só toca rock" com o YouTube livre: estilo pelo ARTISTA (17/10/26)
try:   # Lote C (16/10/26): pagamento Efí (split e, desde 17/10/26, a ponte). Só é CHAMADO pros bares de PUBSHOW_EFI_BARES
    import pubshow_pay
except ImportError:   # VCa-04: deploy sem o pubshow_pay.py/efi_client.py → PubShow sobe no Asaas, Efí desligada
    pubshow_pay = None
# 17/10/26 (caminho único): a ponte saiu do efi_pix (RifaJá) e usa o mesmo efi_client do split (_efi_cli)

log = logging.getLogger('pubshow')

pubshow_bp = Blueprint('pubshow', __name__, url_prefix='/pubshow')

# ── Configurações ──────────────────────────────────────────────────────────────
_ASAAS_BASE = 'https://api.asaas.com/v3'

TIPOS_ESTABELECIMENTO = {
    'bar':        '🍺 Bar / Boteco',
    'pub':        '🎸 Pub Rock',
    'esportivo':  '⚽ Bar Esportivo',
    'churrasco':  '🥩 Churrascaria',
    'restaurante':'🍽️ Restaurante',
    'academia':   '💪 Academia',
    'barbearia':  '✂️ Barbearia',
    'outro':      '🏢 Outro',
}

CANAIS = {
    # ── MÚSICA ───────────────────────────────────────────────────────────────
    'rock':           {'nome': 'Rock Clips',        'emoji': '🎸',  'cor': '#ef4444', 'cat': 'rock',      'grupo': 'musica'},
    'punk':           {'nome': 'Punk Clips',        'emoji': '🤘',  'cor': '#f97316', 'cat': 'punk',      'grupo': 'musica'},
    'sertanejo':      {'nome': 'Sertanejo Clips',   'emoji': '🤠',  'cor': '#eab308', 'cat': 'sertanejo', 'grupo': 'musica'},
    'pagode':         {'nome': 'Pagode Clips',      'emoji': '🥁',  'cor': '#22c55e', 'cat': 'pagode',    'grupo': 'musica'},
    'pop':            {'nome': 'Pop Clips',         'emoji': '🎤',  'cor': '#a855f7', 'cat': 'pop',       'grupo': 'musica'},
    # Lote D C (16/10/26): o botão "Variado" do Estilo do bar — canal-lista (como o Sport Mix) que mistura a música.
    # VDC-05 (16/10/26): + a categoria própria 'variado' (o que os bares no Variado pedem e tocou entra nela, e não no Rock)
    'variado':        {'nome': 'Variado',           'emoji': '🎶',  'cor': '#f97316',
                       'cat': ['rock', 'sertanejo', 'pagode', 'pop', 'punk', 'country', 'reggae', 'variado'], 'grupo': 'musica'},
    # 17/10/26: SHOWS AO VIVO saiu (show passa de 10 min). Canal e vídeos: pubshow_db.SHOWS_FORA
    # ── SPORT ────────────────────────────────────────────────────────────────
    'sport_mix':      {'nome': 'Sport Mix',         'emoji': '🏆',  'cor': '#f59e0b',
                       'cat': ['f1', 'futebol', 'surf', 'aerio', 'radical',
                               'rally', 'wingsuit', 'aviacao', 'lutas', 'skate', 'kitesurf'],
                       'grupo': 'sport'},
    'f1':             {'nome': 'Speed Clips',       'emoji': '🏎️',  'cor': '#e11d48', 'cat': 'f1',        'grupo': 'sport'},
    'futebol':        {'nome': 'Futebol Clips',     'emoji': '⚽',  'cor': '#16a34a', 'cat': 'futebol',   'grupo': 'sport'},
    'surf':           {'nome': 'Surf Clips',        'emoji': '🏄',  'cor': '#0ea5e9', 'cat': 'surf',      'grupo': 'sport'},
    'aerio':          {'nome': 'Aéreo Clips',       'emoji': '🪂',  'cor': '#6366f1', 'cat': 'aerio',     'grupo': 'sport'},
    'radical':        {'nome': 'Radical Clips',     'emoji': '🛹',  'cor': '#f43f5e', 'cat': 'radical',   'grupo': 'sport'},
    'rally':          {'nome': 'Rally / Drift',     'emoji': '🏎️',  'cor': '#f97316', 'cat': 'rally',     'grupo': 'sport'},
    'wingsuit':       {'nome': 'Wingsuit / BASE',   'emoji': '🪂',  'cor': '#3b82f6', 'cat': 'wingsuit',  'grupo': 'sport'},
    'aviacao':        {'nome': 'Jatos / Aviação',   'emoji': '✈️',  'cor': '#06b6d4', 'cat': 'aviacao',   'grupo': 'sport'},
    'lutas':          {'nome': 'UFC / Lutas',       'emoji': '🥊',  'cor': '#dc2626', 'cat': 'lutas',     'grupo': 'sport'},
    'skate':          {'nome': 'Skate Clips',       'emoji': '🛹',  'cor': '#8b5cf6', 'cat': 'skate',     'grupo': 'sport'},
    'kitesurf':       {'nome': 'Kitesurf / Wake',   'emoji': '🌊',  'cor': '#0891b2', 'cat': 'kitesurf',  'grupo': 'sport'},
    # ── MÚSICA EXTRA ─────────────────────────────────────────────────────────
    'country':        {'nome': 'Country / Rodeio',  'emoji': '🤠',  'cor': '#a16207', 'cat': 'country',   'grupo': 'musica'},
    'reggae':         {'nome': 'Reggae Clips',      'emoji': '🎸',  'cor': '#15803d', 'cat': 'reggae',    'grupo': 'musica'},
    # ── ENTRETENIMENTO ───────────────────────────────────────────────────────
    'batidas':        {'nome': 'Batidas / Crashes', 'emoji': '💥',  'cor': '#b45309', 'cat': 'batidas',   'grupo': 'viral'},
    'standup':        {'nome': 'Stand-up Comedy',   'emoji': '🎭',  'cor': '#7c3aed', 'cat': 'standup',   'grupo': 'entretenimento'},
    # 20/10/26: o botão grande 😂 Comédia do Estilo do bar (Diogo: o dono não abre o "Mais estilos")
    'comedia':        {'nome': 'Comédia',           'emoji': '😂',  'cor': '#a855f7', 'cat': ['batidas', 'standup'],
                       'grupo': 'entretenimento'},
}

# Lote D C (16/10/26): "Estilo do bar" do painel = 5 botões grandes. Cada um põe a TV num canal E deixa na busca do
# cliente só os estilos da família (as MESMAS colunas da lista antiga: temas_habilitados = generos_jukebox).
# 'variado' = a TV mistura a música e o cliente busca em tudo (NULL). Os outros canais ("mais estilos") só trocam a
# TV e liberam a busca inteira. Nada muda no banco até o dono tocar num botão (o salvo antes continua valendo).
ESTILOS_BAR = {
    'rock':      {'nome': 'Rock',      'emoji': '🎸', 'canal': 'rock',      'temas': ['rock', 'punk']},
    'sertanejo': {'nome': 'Sertanejo', 'emoji': '🤠', 'canal': 'sertanejo', 'temas': ['sertanejo', 'country']},
    'pagode':    {'nome': 'Pagode',    'emoji': '🥁', 'canal': 'pagode',    'temas': ['pagode']},
    'pop':       {'nome': 'Pop',       'emoji': '🎤', 'canal': 'pop',       'temas': ['pop']},
    'reggae':    {'nome': 'Reggae',    'emoji': '🌴', 'canal': 'reggae',    'temas': ['reggae']},   # pedido do Garage, 20/10/26
    # 20/10/26 (Diogo: "clicar no mais estilo os burro nao le"): o que importa vira botão grande. Esporte = Sport Mix;
    # Comédia = batidas + stand-up. Os temas deles entram só na mistura da TV: o cliente continua pedindo música
    # (o filtro do celular e do YouTube usa só os temas de música, _so_musica).
    'esporte':   {'nome': 'Esporte',   'emoji': '⚽', 'canal': 'sport_mix', 'temas': list(CANAIS['sport_mix']['cat'])},
    'comedia':   {'nome': 'Comédia',   'emoji': '😂', 'canal': 'comedia',   'temas': ['batidas', 'standup']},
    'variado':   {'nome': 'Variado',   'emoji': '🎶', 'canal': 'variado',   'temas': None},
}
# categoria → botão (Rock, Esporte…): o rodízio da mistura vai de botão em botão, não de categoria em categoria
# (senão Rock + Esporte = 1 rock a cada 11 esportes)
_BOTAO_DA_CAT = {t: k for k, e in ESTILOS_BAR.items() for t in (e['temas'] or [])}


def _so_musica(temas):
    """Só os temas que são estilo de MÚSICA (o filtro do celular e do YouTube); Esporte e Comédia ficam só na TV."""
    return [t for t in (temas or []) if t in pubshow_estilo.CANAL_ESTILO] or None


# 20/10/26 (Diogo: "vídeos legais de esporte... mas pôr música sobre eles"): ⚽ Esporte + um estilo de MÚSICA acesos →
# a TV mostra em tela cheia um vídeo de esporte que NÃO é do YouTube (Pexels, mudo, no bucket R2 dele) e o clipe do
# YouTube numa janela, com o som dele. Regras do YouTube: 1 player só tocando, nada na frente dele, ≥ 480x270, som só
# dele (não se troca o som de vídeo do YouTube). Nesse modo o YouTube da TV só toca música.
# Liga com a env PUBSHOW_FUNDO_URL (endereço público da pasta dos vídeos no bucket); sem ela, Esporte + Rock = a
# mistura de antes (vídeos de esporte do YouTube no meio das músicas).
def _fundo_url():
    return (os.environ.get('PUBSHOW_FUNDO_URL') or '').strip().rstrip('/')


def _fundo_ativo(b):
    """Esporte + algum estilo de música acesos (canal Variado com os temas dos dois), com o bucket configurado."""
    if not b or not _fundo_url():
        return False
    try:
        ck = (b.get('canal_atual') if hasattr(b, 'get') else b['canal_atual']) or ''
    except Exception:
        return False
    temas = set(_temas_do_bar(b) or [])
    return ck == 'variado' and set(ESTILOS_BAR['esporte']['temas']) <= temas and bool(_so_musica(list(temas)))


def _fundo_clipes(b):
    """Os endereços dos vídeos de fundo (pubshow_fundos.FUNDOS no bucket) quando o modo está ligado; senão None."""
    if not _fundo_ativo(b):
        return None
    try:
        from pubshow_fundos import FUNDOS
    except Exception as e:
        log.error('[PUBSHOW] lista dos vídeos de fundo falhou: %s', e)
        return None
    base = _fundo_url()
    return [f'{base}/{f[0]}' for f in FUNDOS] or None

# Plano ÚNICO (15/10/26, decisão padrão 1 — reversível): acabou Starter/Bar/Pro/Rede. A chave fica
# 'bar' (é o que está gravado no banco, nos links e no externalReference do Asaas) e TUDO é liberado.
# Preço em 1 lugar só: env PUBSHOW_PLANO_CENT (centavos; padrão 7990 = R$ 79,90 — decisão do Diogo, 17/10/26).
# Painel, planos, e-mails, home do 4kitem, llms.txt e saas-admin leem daqui — nunca número escrito à mão.
def _plano_cent() -> int:
    try:
        v = int(str(os.environ.get('PUBSHOW_PLANO_CENT', '7990')).strip())
        return v if v > 0 else 7990
    except Exception:
        return 7990


def _brl_cent(cent: int) -> str:
    """7990 → 'R$ 79,90' (milhar com ponto); -550 → 'R$ -5,50' (VCa-06, 16/10/26: o divmod do negativo dava -6,50)."""
    cent = int(cent)
    reais, cents = divmod(abs(cent), 100)
    return 'R$ {}{},{:02d}'.format('-' if cent < 0 else '', f'{reais:,}'.replace(',', '.'), cents)


PLANO_CENT = _plano_cent()
SUPORTE_WA     = '5547997766831'      # WhatsApp de suporte do PubShow (decisão padrão 6, 15/10/26)
SUPORTE_WA_FMT = '(47) 99776-6831'

PLANO = {
    'nome': 'PubShow',
    'emoji': '🍺',
    'preco': PLANO_CENT / 100,
    'preco_cent': PLANO_CENT,
    'preco_fmt': _brl_cent(PLANO_CENT),
    'descricao': 'Um plano só. Tudo incluso.',
    'features': ['TV com clipes a noite toda, todos os estilos',
                 'Pedir música é grátis pelo QR, sem app',
                 'Todos os momentos pagos no PIX (furar a fila, parabéns, namoro…)',
                 'Gorjeta pra equipe, contada à parte',
                 'OK do gerente pra cantada e pedido de namoro',
                 'Promoção relâmpago na TV',
                 '1 propaganda do bar na TV (texto ou imagem)',
                 'Relatório do que os momentos renderam',
                 'Aviso no celular quando chega pedido',
                 f'Suporte no WhatsApp {SUPORTE_WA_FMT}'],
    'max_tipos': None,        # todos os tipos de pedido
    'max_anuncios': 1,        # 17/10/26 (Diogo): 1 propaganda só, texto ou imagem; quem já tinha mais guarda as dele
    'analytics': True,
    'whatsapp': True,
    'happy_hour': False,      # 17/10/26 (decisão 2): sem happy hour, preço fixo
    'grupos_tv': None,        # todos os canais
}
PLANOS = {'bar': PLANO}
# Chaves antigas que ainda podem chegar (banco antigo, externalReference de assinatura Asaas, link
# velho /assinar/pro): todas valem o plano único. O init do banco normaliza pra 'bar'.
PLANOS_LEGADOS = ('starter', 'pro', 'rede', 'premium')


def _rede_ligada() -> bool:
    """Plano Rede DESLIGADO (decisão padrão 2, 15/10/26): rotas dão 404 sem PUBSHOW_REDE=1.
    Dados e tabelas ficam (owner_business_id não é apagado)."""
    return os.environ.get('PUBSHOW_REDE', '') == '1'


def _plano_permite(b, feature: str) -> bool:
    """Plano único: tudo liberado. 'multi' (Rede) só com PUBSHOW_REDE=1."""
    if feature == 'multi':
        return _rede_ligada()
    return bool(PLANO.get(feature, False))


def _plano_canais_permitidos(b) -> dict:
    """Plano único: todos os canais."""
    return CANAIS


def _plano_max_anuncios(b) -> int:
    return int(PLANO['max_anuncios'])


def _plano_tipos_permitidos(b) -> list | None:
    """Plano único: None (= todos os tipos)."""
    return None


@pubshow_bp.context_processor
def _ctx_pubshow():
    """Suporte e plano único em TODO template do blueprint (15/10/26) — nada de número à mão."""
    return {'SUPORTE_WA': SUPORTE_WA, 'SUPORTE_WA_FMT': SUPORTE_WA_FMT, 'PLANO': PLANO,
            'rede_ligada': _rede_ligada(),
            # taxa de serviço que o CLIENTE paga a mais no PIX automático (Ajustes mostra o preço final, 16/10/26)
            'TAXA_SERVICO': (PIX_TAXA_CONVENIENCIA if os.environ.get('ASAAS_API_KEY') else 0.0)}


# ── Diretório de uploads de slides ───────────────────────────────────────────
_SLIDES_DIR = os.path.join(
    os.environ.get('DATA_DIR', os.path.abspath(os.path.dirname(__file__) or '.')),
    'pubshow_slides'
)


GORJETA_VALORES = (5.0, 10.0, 20.0, 50.0)   # opções de gorjeta no celular do cliente
TIPOS_SEMPRE_GRATIS = ('musica', 'musica_especifica', 'musica_externa')   # nunca cobrados, mesmo com precos_custom (YouTube incluído — 12/09/26, Garage tinha 0,50)
TIPOS_OCULTOS = ('vip',)       # existem no banco/TV, mas o cliente não vê ("Tocar agora" saiu, YouTube entrou — 11/09/26)
TIPOS_EQUIPE  = ('gorjeta',)   # dinheiro da EQUIPE, não do caixa da casa — somado à parte no painel

TIPOS_PEDIDO = {
    'musica':           {'nome': 'Música aleatória',       'emoji': '🎵', 'preco': 0.00,  'cor': '#3b82f6'},   # GRÁTIS (decisão 11/09/26)
    'musica_especifica':{'nome': 'Escolher a música',      'emoji': '🎯', 'preco': 0.00,  'cor': '#06b6d4'},   # GRÁTIS (decisão 11/09/26)
    'musica_externa':   {'nome': 'Buscar no YouTube',      'emoji': '🌐', 'preco': 0.00,  'cor': '#dc2626'},   # GRÁTIS por padrão (11/09/26); o bar pode cobrar em precos_custom
    'flash':            {'nome': 'Furar a fila',           'emoji': '⚡', 'preco': 4.00,  'cor': '#f59e0b'},   # R$4 + R$1 = R$5 no PIX: abaixo de R$5 o Asaas recusou e o PIX caiu na chave do bar (16/10/26)
    # ⏩ Pular a espera (20/10/26, Diogo: "pedir sem delay 2 reais"): pede a música AGORA, sem esperar os
    # GRATIS_INTERVALO_MIN da grátis; entra na fila NORMAL (na frente é o furar). R$ 2 no TOTAL e só em bar que cobra
    # pela Efí (_pular_espera_para): no Asaas o mínimo é R$ 5 e empataria com o furar. Preço fixo, como o furar:
    # ponte = PRECO_PULAR_EFI + R$ 1 do app; split = R$ 2 + a taxa do split (PUBSHOW_CONV_CENT=0 → R$ 2).
    # Não conta como a grátis: a próxima grátis segue contando da última GRÁTIS (quem pagou não perde a vez dela).
    'pular_espera':     {'nome': 'Pular a espera',         'emoji': '⏩', 'preco': 2.00,  'cor': '#14b8a6'},
    'vip':              {'nome': 'Tocar AGORA',            'emoji': '👑', 'preco': 10.00, 'cor': '#8b5cf6'},   # OCULTO do cliente (TIPOS_OCULTOS, 11/09/26); fica p/ histórico e TV
    'parabens':         {'nome': 'Parabéns! 🎂',           'emoji': '🎂', 'preco': 15.00, 'cor': '#ec4899'},
    'dedicatoria':      {'nome': 'Dedicatória ❤️',        'emoji': '💌', 'preco': 10.00, 'cor': '#ef4444'},
    'brinde':           {'nome': 'Brinde Geral! 🍻',       'emoji': '🍻', 'preco': 5.00,  'cor': '#22c55e'},
    'chegada':          {'nome': 'Chegamos! 🎉',           'emoji': '🎉', 'preco': 5.00,  'cor': '#f97316'},
    # Cantada (11/09/26): o cliente escreve a cantada e ela aparece na tela — o "esquenta" do pedido de namoro
    'cantada':          {'nome': 'Mandar uma cantada 😏',  'emoji': '😏', 'preco': 5.00,  'cor': '#f472b6', 'ok': True},   # 'ok': passa pelo gerente antes da TV
    # Salve pra outra mesa (12/09/26): recado de mesa pra mesa — alvo é a MESA, não uma pessoa; sem gate
    'salve':            {'nome': 'Salve pra outra mesa 📣', 'emoji': '📣', 'preco': 5.00,  'cor': '#0ea5e9'},
    # chave 'casamento' mantida por compatibilidade com o banco; rótulo agora é "Namoro"
    'casamento':        {'nome': 'Pedido de Namoro 💕',   'emoji': '💕', 'preco': 25.00, 'cor': '#a855f7', 'ok': True},
    # Gorjeta pra EQUIPE (11/09/26): valor escolhido pelo cliente entre GORJETA_VALORES; 5 é só o mínimo exibido.
    # É dinheiro dos funcionários, não do caixa — contado à parte no painel (TIPOS_EQUIPE).
    'gorjeta':          {'nome': 'Gorjeta pra equipe 🙏', 'emoji': '🙏', 'preco': 5.00,  'cor': '#22c55e'},
}

# Taxa que o Asaas DESCONTA por cobrança PIX recebida (custo do gateway).
# Ajuste para o valor real do seu contrato Asaas via env PUBSHOW_TAXA_ASAAS.
PIX_TAXA_ASAAS = float(os.environ.get('PUBSHOW_TAXA_ASAAS', '1.00'))

# Taxa de conveniência cobrada do CLIENTE quando o pagamento é automático via
# Asaas. Por padrão = a taxa do Asaas, então o bar recebe o valor cheio do item
# (a taxa do gateway sai do bolso de quem pede). No PIX manual (chave do próprio
# bar) não há taxa. Ajustável via env PUBSHOW_TAXA_CONVENIENCIA.
PIX_TAXA_CONVENIENCIA = float(os.environ.get('PUBSHOW_TAXA_CONVENIENCIA', str(PIX_TAXA_ASAAS)))

# Comissão do PubShow sobre o jukebox (take-rate). Só entra em ação na Fase 2,
# quando há split entre o pub (recebe a maior parte) e o PubShow (sua comissão).
# Modelo: mensalidade reduzida + 15% do jukebox. Ajustável via env.
PIX_TAKE_RATE = float(os.environ.get('PUBSHOW_TAKE_RATE', '0.15'))

# Piso de preço por pedido quando há taxa de transação (Asaas): abaixo disso a
# taxa fixa come o valor. Protege a economia do bar. Ajustável via env.
PIX_PISO_PRECO = float(os.environ.get('PUBSHOW_PISO_PRECO', '4.00'))   # 16/10/26: R$4 + R$1 = R$5 (com R$3+1=R$4 o Asaas recusou e o PIX caiu na chave do bar)

# ── Efí: caminho único (17/10/26, proposta aprovada pelo Diogo) ──────────────────────────────────────────
# Regra POR BAR, automática, 1 env só (PUBSHOW_EFI_BARES: code do bar, separado por vírgula; '*' = todos;
# vazia/ausente = NENHUM):
#   1. bar fora da lista → Asaas (como sempre);
#   2. bar na lista com a conta Efí dele ACEITA pela Efí (pubshow_pay.conta_aceita) → SPLIT (pubshow_pay): a parte do
#      bar cai na hora na conta Efí dele;
#   3. bar na lista sem conta (ou com a conta ainda não aceita) → PONTE: cobra na conta Efí do PubShow e o dinheiro do
#      bar vai no repasse semanal (/pubshow/admin/repasses, coluna Efí). Conectou a conta → vira split sozinho.
# 1 conta Efí só pros 2 (pubshow_pay.cliente(): PUBSHOW_EFI_*, ou as EFI_* da RifaJá); o efi_pix.py fica intocado.
# Preço igual nos 2: furar a fila R$ 3 + R$ 1 = R$ 4, piso R$ 1, teto R$ 50 (a Efí não tem o mínimo de R$ 5 do Asaas).
# PUBSHOW_GATEWAY virou só o botão de emergência: =asaas desliga a Efí inteira (os bares da lista voltam pro Asaas).
# O "modo Efí" do bar (sem teste grátis, mensalidade saindo de 30% dos momentos, pausa de 60 dias, Meu dinheiro) é
# o do SPLIT (_efi_para). O bar da ponte segue o modelo de hoje da ponte: assinatura no Asaas + repasse semanal.
PRECO_FLASH_EFI    = 3.00   # R$3 + R$1 de taxa = R$4 no PIX
PIX_PISO_PRECO_EFI = 1.00   # a Efí não tem mínimo; o piso do preço customizado cai pra R$1 nos bares da Efí
PRECO_PULAR_EFI    = 1.00   # ⏩ Pular a espera na ponte: R$1 + R$1 de taxa = R$2 no PIX (20/10/26)


def _efi_bares():
    return {c.strip().upper() for c in (os.environ.get('PUBSHOW_EFI_BARES') or '').split(',') if c.strip()}


def _gateway_ativo(b):
    """True quando o PIX do bar sai por gateway (Asaas, ponte Efí ou split): o cliente paga item + taxa e confirma sozinho."""
    return bool(os.environ.get('ASAAS_API_KEY')) or bool(_rota_efi(b))


def _piso_do_bar(b):
    """Piso do preço de um momento: R$ 1 na Efí, ponte ou split (sem o mínimo do Asaas); senão PIX_PISO_PRECO (R$ 4 +
    R$ 1 do app = R$ 5, o mínimo do Asaas). O painel mostra o MESMO piso que a cobrança usa (junção 17/10/26)."""
    return PIX_PISO_PRECO_EFI if _rota_efi(b) else PIX_PISO_PRECO


def _taxa_pix_bar(b, tipo='flash'):
    """O R$ 1 do app que o CLIENTE paga a mais no PIX deste bar (junção 17/10/26): split = a do pubshow_pay
    (PUBSHOW_CONV_CENT; a produção usa 0 = o cliente paga só o item); Asaas ou ponte Efí = PIX_TAXA_CONVENIENCIA;
    PIX manual / sem gateway = 0. Caminho único (21/10/26): o mesmo desenho do master; só a ROTA mudou."""
    if _efi_para(b):
        return pubshow_pay.conv_cent_para(tipo) / 100
    return PIX_TAXA_CONVENIENCIA if _gateway_ativo(b) else 0.0


def _pular_espera_para(b):
    """⏩ Pular a espera (20/10/26) existe neste bar? Só se ele cobra pela Efí (ponte ou split) e a regra da grátis
    (1 a cada GRATIS_INTERVALO_MIN) está ligada. No Asaas não: o mínimo de R$ 5 empataria com o furar a fila."""
    try:
        return GRATIS_INTERVALO_MIN > 0 and bool(_ponte_efi_para(b) or _efi_para(b))
    except Exception:
        return False

# Tetos anti-flood (15/10/26). SEM trava por IP (decisão 11/09/26: no Wi-Fi do bar todo mundo tem o
# mesmo IP) — por bar e por client_id do celular. "Aberto" = aguardando_pix recente.
# 16/10/26 (críticos C3/C5): teto do bar 15 → 50 numa janela de 15 min (1 pessoa com 15 QR abandonados
# travava o caixa da noite); o do celular segue 2 em 30 min. Env torta não derruba mais o blueprint.
def _env_int(nome, padrao):
    try:
        return int(os.environ.get(nome, str(padrao)))
    except (TypeError, ValueError):
        return padrao


MAX_PIX_ABERTOS_BAR     = _env_int('PUBSHOW_MAX_PIX_ABERTOS', 50)
MAX_PIX_ABERTOS_CLIENTE = 2
MAX_FILA                = _env_int('PUBSHOW_MAX_FILA', 25)
# Pendente de outra noite não trava a fila de hoje (16/10/26, C5): os tetos só olham as últimas 12 h
_JANELA_FILA = "datetime('now','-12 hours')"


_MESES_PT = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto',
             'setembro', 'outubro', 'novembro', 'dezembro')


def _hoje_brt():
    return (datetime.utcnow() - timedelta(hours=3)).date()


def somar_pagos(conn, bid=None, de=None, ate=None, conta='casa', por=None):
    """A ÚNICA soma de dinheiro do PubShow (15/10/26) — painel, fila-json, relatório, Rede, admin
    e saas-admin usam esta. Só conta pedido com pago_em (gravado só pela confirmação do pagamento)
    e status na lista positiva STATUS_PAGOS.
    bid: um bar (None = todos). de/ate: datas de Brasília (date ou 'AAAA-MM-DD'), inclusivas, pelo
    dia do pagamento. conta: 'casa' (sem gorjeta), 'gorjeta' (só equipe) ou 'tudo'.
    Devolve {'total': float, 'n': int}; com por='dia'|'tipo'|'bar' → {chave: {'total', 'n'}}."""
    ph = ','.join('?' * len(STATUS_PAGOS))
    where = ['pago_em IS NOT NULL', f'status IN ({ph})',
             # crítico dinheiro M4 (17/10/26): conferido no gateway e NÃO pago (PENDING, sumiu, devolvido) sai das somas
             "(pix_conferido IS NULL OR pix_conferido IN ('HISTORICO','ADMIN','RECEIVED','CONFIRMED','EFI:CONCLUIDA') "
             "OR pix_conferido LIKE 'ERRO%')"]
    params = list(STATUS_PAGOS)
    if bid is not None:
        where.append('business_id=?'); params.append(bid)
    eq = ','.join('?' * len(TIPOS_EQUIPE))
    if conta == 'casa':
        where.append(f'tipo NOT IN ({eq})'); params.extend(TIPOS_EQUIPE)
    elif conta == 'gorjeta':
        where.append(f'tipo IN ({eq})'); params.extend(TIPOS_EQUIPE)
    if de is not None:
        where.append("date(pago_em,'-3 hours') >= ?"); params.append(str(de))
    if ate is not None:
        where.append("date(pago_em,'-3 hours') <= ?"); params.append(str(ate))
    chave = {'dia': "date(pago_em,'-3 hours')", 'tipo': 'tipo', 'bar': 'business_id'}.get(por)
    sql_w = ' AND '.join(where)
    if chave:
        rows = conn.execute(
            f'SELECT {chave} AS k, COALESCE(SUM(valor),0) AS tot, COUNT(*) AS n FROM pubshow_pedidos '
            f'WHERE {sql_w} GROUP BY k', params).fetchall()
        return {r[0]: {'total': round(float(r[1]), 2), 'n': int(r[2])} for r in rows}
    r = conn.execute(
        f'SELECT COALESCE(SUM(valor),0) AS tot, COUNT(*) AS n FROM pubshow_pedidos WHERE {sql_w}',
        params).fetchone()
    return {'total': round(float(r[0]), 2), 'n': int(r[1])}


def _somar_pagos_fora_da_efi(conn, bid, de, ate, conta):
    """Lote D, críticos (17/10/26, CD2): o MESMO critério do somar_pagos (centavos), só dos pedidos SEM cobrança Efí
    (pagos pelo Asaas). Pro "dia do pagamento" da semana da migração. Sem a tabela da Efí = o somar_pagos inteiro."""
    ph = ','.join('?' * len(STATUS_PAGOS))
    eq = ','.join('?' * len(TIPOS_EQUIPE))
    where = ['business_id=?', 'pago_em IS NOT NULL', f'status IN ({ph})',
             f'tipo {"NOT " if conta == "casa" else ""}IN ({eq})',
             "date(pago_em,'-3 hours') >= ?", "date(pago_em,'-3 hours') <= ?"]
    params = [bid, *STATUS_PAGOS, *TIPOS_EQUIPE, str(de), str(ate)]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pubshow_pay_cobrancas'").fetchone():
        where.append('id NOT IN (SELECT pedido_id FROM pubshow_pay_cobrancas WHERE business_id=?)')
        params.append(bid)
    r = conn.execute(f'SELECT COALESCE(SUM(valor),0) FROM pubshow_pedidos WHERE {" AND ".join(where)}', params).fetchone()
    return int(round(float(r[0]) * 100))


# ── Lote C (16/10/26): pagamento Efí com split ─────────────────────────────────────────────────────────
# Decisão do Diogo: o dinheiro do bar cai direto na conta Efí dele e a mensalidade sai pelo split. Liga bar a
# bar (PUBSHOW_EFI_BARES). Lista vazia = NENHUMA função do pubshow_pay é chamada: o caminho de hoje fica igual.
# 17/10/26 (caminho único): split só com a conta Efí do bar aceita; sem ela o bar da lista vende pela ponte.
MSG_EFI_SEM_CONTA = ('Os momentos pagos ainda não estão ligados neste bar. '
                     'A música grátis funciona normal: escolhe uma e manda! 🎵')


def _efi_ligado() -> bool:
    """A Efí está ligada: algum bar em PUBSHOW_EFI_BARES e sem o botão de emergência (PUBSHOW_GATEWAY=asaas).
    A MESMA regra do pubshow_pay.ligado(), lida aqui direto da env: com a lista vazia nenhuma função do pubshow_pay
    é chamada (o probe_dormente confere)."""
    if pubshow_pay is None:   # VCa-04: sem o módulo no deploy, nunca liga
        return False
    if (os.environ.get('PUBSHOW_GATEWAY') or '').strip().lower() == 'asaas':
        return False
    return bool(_efi_bares())


def _efi_lista(b) -> bool:
    """O bar está na Efí: o code dele em PUBSHOW_EFI_BARES (ou '*') e a Efí ligada. Ponte OU split (_rota_efi decide).
    16/10/26 (CC1 X6 / CC3 R6): lista vazia = NENHUM bar (falha fechada). É o que o passo 4 (conectar a conta) usa."""
    return _efi_ligado() and pubshow_pay.ligado_para(b)


def _efi_cli():
    """A conta Efí do PubShow, a MESMA pra ponte e split (pubshow_pay.cliente). None sem o módulo no deploy."""
    if pubshow_pay is None:
        return None
    try:
        return pubshow_pay.cliente()
    except Exception as ex:
        log.warning('[PUBSHOW] cliente Efí indisponível: %s', ex)
        return None


def _efi_cli_ok() -> bool:
    cli = _efi_cli()
    try:
        return bool(cli is not None and cli.configurado())
    except Exception:
        return False


_ROTA_SEM_CONTA_PUBSHOW = set()


def _cache_rota():
    """Cache da rota por request (o painel e o jukebox perguntam várias vezes pelo mesmo bar). Fora de request = None."""
    from flask import g, has_request_context
    if not has_request_context():
        return None
    c = getattr(g, '_pubshow_rota_efi', None)
    if c is None:
        c = g._pubshow_rota_efi = {}
    return c


def _rota_efi(b):
    """Caminho único da Efí (17/10/26): por onde sai o PIX DESTE bar — 'split', 'ponte' ou None (Asaas / PIX manual).
    Na lista + conta Efí aceita pela Efí = 'split'; na lista sem ela = 'ponte'. Sem a conta Efí do PubShow
    configurada = None (o bar cai no Asaas, nunca num QR quebrado). Erro lendo a conta = 'ponte' (o dinheiro fica
    no PubShow e vai no repasse: nunca some)."""
    if not b or not _efi_lista(b):
        return None
    try:
        bid = int(b['id'])
    except Exception:
        return None
    cache = _cache_rota()
    if cache is not None and bid in cache:
        return cache[bid]
    rota = None
    if _efi_cli_ok():
        rota = 'ponte'
        try:
            conn = _pp_conn()
            try:
                if pubshow_pay.conta_aceita(conn, bid):
                    rota = 'split'
            finally:
                conn.close()
        except Exception as ex:
            log.warning('[PUBSHOW] conta Efí do bar %s não lida (fica na ponte): %s', bid, ex)
    elif bid not in _ROTA_SEM_CONTA_PUBSHOW:
        _ROTA_SEM_CONTA_PUBSHOW.add(bid)   # 1 aviso por bar por processo (isto roda várias vezes por request)
        log.warning('[PUBSHOW] bar %s na lista da Efí, mas a conta Efí do PubShow não está configurada: vai pelo Asaas', bid)
    if cache is not None:
        cache[bid] = rota
    return rota


def _efi_para(b) -> bool:
    """SPLIT pra este bar (o "modo Efí": sem teste grátis, mensalidade dos momentos, pausa, Meu dinheiro).
    17/10/26 (caminho único): na lista E com a conta Efí aceita. Antes: PUBSHOW_GATEWAY=efi + lista."""
    return _rota_efi(b) == 'split'


def _ponte_efi_para(b) -> bool:
    """PONTE Efí: bar na lista sem a conta Efí aceita → cobra na conta Efí do PubShow (repasse semanal). Um bar nunca
    cobra pelos 2 caminhos (_rota_efi devolve um só). 17/10/26: pelo efi_client (pubshow_pay.cliente), não pelo efi_pix."""
    return _rota_efi(b) == 'ponte'


def _pp_conn():
    """Conexão do pubshow.db com as tabelas do pubshow_pay (cria se faltar). Só no modo Efí.
    Lote D (16/10/26): confere a tabela MAIS NOVA (pubshow_pay_pausa): banco que já tinha as do Lote C ganha a nova."""
    conn = get_pubshow_db()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pubshow_pay_pausa'").fetchone() \
            or 'dev_aprovacao' not in {r[1] for r in conn.execute('PRAGMA table_info(pubshow_pay_cobrancas)')}:
        pubshow_pay.init_tables(conn)   # 17/10/26 (decisão 3): colunas da aprovação da devolução
    return conn


def _efi_conta_ok(b) -> bool:
    """O bar já conectou a conta Efí (titular = CPF/CNPJ do cadastro)? Sem ela não há momento pago."""
    try:
        conn = _pp_conn()
        try:
            return bool(pubshow_pay.conta_do_bar(conn, b['id']))
        finally:
            conn.close()
    except Exception as ex:
        log.warning('[PUBSHOW] conta Efí do bar %s não lida: %s', b['id'], ex)
        return False


def _efi_pix_vivo(pedido_id) -> bool:
    """O QR Efí deste pedido ainda vale (cobrança 'ativa' e mais de 1 min até vencer)? Vencido não reabre."""
    try:
        conn = _pp_conn()
        try:
            c = conn.execute('SELECT status, expira_em FROM pubshow_pay_cobrancas WHERE pedido_id=?',
                             (pedido_id,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return False
    limite = (datetime.utcnow() + timedelta(seconds=60)).strftime('%Y-%m-%d %H:%M:%S')
    return bool(c) and c['status'] == 'ativa' and (c['expira_em'] or '') > limite


def _efi_validade(expira_em):
    """Lote C, fatia b (16/10/26): validade do QR Efí pra tela do PIX (expira_em em UTC 'AAAA-MM-DD HH:MM:SS').
    {'expira_min': minutos que faltam (0 = venceu), 'expira_hora': 'HH:MM' de Brasília}; vazio se não deu pra ler."""
    try:
        exp = datetime.strptime((expira_em or '')[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return {}
    falta = (exp - datetime.utcnow()).total_seconds()
    return {'expira_min': int((falta + 59) // 60) if falta > 0 else 0,
            'expira_hora': (exp - timedelta(hours=3)).strftime('%H:%M')}


def _pubshow_on_pago(conn, cob):
    """pubshow_pay: o PIX Efí caiu. Roda DENTRO da transação da confirmação (não dá commit).
    True = o pedido aceitou (vai pra fila ou pro OK do gerente, com pago_em); False = o pedido já não espera
    PIX (recusado, apagado, estornado) e o módulo devolve o PIX sozinho. Mesma regra do webhook do Asaas."""
    novo = _status_apos_pix(conn, cob['pedido_id'])
    n = conn.execute("UPDATE pubshow_pedidos SET status=?, pago_em=COALESCE(pago_em, datetime('now')) "
                     "WHERE id=? AND business_id=? AND status='aguardando_pix' AND estornado_em IS NULL",
                     (novo, cob['pedido_id'], cob['business_id'])).rowcount
    if n == 1 and int(cob.get('mens_cent') or 0) > 0:
        # 17/10/26 (decisão 8): a 1ª mensalidade de verdade (30% de um momento pago) dá a vaga de fundador, se sobrar
        try:
            if marcar_fundador(conn, cob['business_id']):
                log.info('[PUBSHOW] bar %s virou fundador (1ª mensalidade pela Efí)', cob['business_id'])
        except Exception as ex:
            log.warning('[PUBSHOW] fundador do bar %s não marcado: %s', cob['business_id'], ex)
    return n == 1


def _virar_fundador(business_id):
    """17/10/26 (decisão 8): 1º pagamento de verdade da assinatura (webhook do Asaas, PAYMENT_RECEIVED/CONFIRMED) →
    vaga de fundador, se ainda houver. Bar de teste/demo nunca. Erro aqui nunca derruba o webhook."""
    try:
        conn = get_pubshow_db()
        try:
            ok = marcar_fundador(conn, int(business_id))
            conn.commit()
        finally:
            conn.close()
        if ok:
            log.info('[PUBSHOW] bar %s virou fundador (1º pagamento da assinatura)', business_id)
        return ok
    except Exception as ex:
        log.warning('[PUBSHOW] fundador do bar %s não marcado: %s', business_id, ex)
        return False


# VCb-05 (16/10/26): depois de vencer, a tela ainda confirma por GRACA_PRAZO_S (PIX no prazo + 2 min = aceito pelo
# robô) + 1 min de folga pra o PIX aparecer na Efí. Passou disso, quem resolve é o robô (aceita ou devolve).
_EFI_TELA_GRACA_S = 60


def _efi_pix_da_tela(conn, pedido_id, status):
    """Tela do cliente (pedido-status): pedido esperando PIX com cobrança ativa → confirma na Efí (no máximo
    1 consulta a cada 5 s por cobrança; o robô do app.py confirma também). VCb-05: a cobrança 'expirada' também
    confirma até GRACA_PRAZO_S + 1 min depois do expira_em (o robô ACEITA PIX pago nesse prazo; a tela não pode
    dizer "venceu" e parar). Depois disso, vencida/cancelada fica pro robô (ele devolve PIX atrasado).
    Devolve (status da cobrança Efí, na_graca), ou (None, False) se o pedido não é da Efí.
    na_graca = venceu, mas ainda dá pra confirmar um PIX feito agora há pouco."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pubshow_pay_cobrancas'").fetchone():
        return None, False
    c = conn.execute('SELECT txid, status, expira_em FROM pubshow_pay_cobrancas WHERE pedido_id=?',
                     (pedido_id,)).fetchone()
    if not c:
        return None, False
    st = c['status']
    graca = False
    if st == 'expirada':
        try:
            _exp = datetime.strptime((c['expira_em'] or '')[:19], '%Y-%m-%d %H:%M:%S')
            graca = datetime.utcnow() <= _exp + timedelta(seconds=pubshow_pay.GRACA_PRAZO_S + _EFI_TELA_GRACA_S)
        except ValueError:
            graca = False
    if status == 'aguardando_pix' and (st in ('criando', 'ativa') or graca):
        st = pubshow_pay.confirmar(conn, c['txid'], on_pago=_pubshow_on_pago, freio_s=5).get('status') or st
    return st, (graca and st == 'expirada')


def _efi_estornar(b, pid, motivo):
    """Recusa com a Efí ligada pro bar: devolve o PIX inteiro (pago) ou cancela a cobrança (não pago; se o PIX
    cair depois, o robô devolve). Idempotente; business_id impede um bar de mexer no pedido de outro.
    Chamar DEPOIS do UPDATE + commit (o módulo exige a conexão sem transação aberta).
    VCa-05 (16/10/26): decide pela COBRANÇA Efí do pedido (com PUBSHOW_GATEWAY=efi), não pela lista: o bar
    tirado do PUBSHOW_EFI_BARES (rollback por bar) ainda devolve o PIX que já recebeu pela Efí.
    CC3 R3 (16/10/26): no rollback (PUBSHOW_GATEWAY=asaas) o robô está parado e o pubshow_pay não é chamado. Se o
    pedido tem cobrança Efí PAGA, marca estornar=1 nela (só SQL, sem criar tabela nem importar o efi_client): quando
    voltar pra efi, o robô (reconciliar) devolve sozinho. Antes ninguém devolvia nunca."""
    if not _efi_ligado():
        try:
            conn = get_pubshow_db()
            try:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                    "AND name='pubshow_pay_cobrancas'").fetchone():
                    return None
                n = conn.execute("""UPDATE pubshow_pay_cobrancas SET estornar=1, motivo_estorno=COALESCE(motivo_estorno, ?),
                                        atualizado_em=datetime('now')
                                    WHERE pedido_id=? AND business_id=? AND status='paga' AND estornar=0""",
                                 ((motivo or 'estorno')[:120], pid, b['id'])).rowcount
                conn.commit()
            finally:
                conn.close()
            if n:
                log.warning('[PUBSHOW] Efí desligada (rollback): pedido #%s marcado pra devolver quando voltar pra efi', pid)
                return {'ok': True, 'marcado': True, 'status': 'paga'}
        except Exception as ex:
            log.error('[PUBSHOW] Efí desligada: não marquei o estorno do pedido #%s: %s', pid, ex)
        return None
    try:
        conn = _pp_conn()
        try:
            if not conn.execute('SELECT 1 FROM pubshow_pay_cobrancas WHERE pedido_id=?', (pid,)).fetchone():
                return None
            r = pubshow_pay.estornar(conn, pid, motivo=motivo, business_id=b['id'])
        finally:
            conn.close()
        log.info('[PUBSHOW] Efí: estorno do pedido #%s (%s): %s', pid, motivo, r.get('status') or r.get('erro'))
        return r
    except Exception as ex:
        log.error('[PUBSHOW] Efí: estorno do pedido #%s falhou: %s', pid, ex, exc_info=True)
        return {'ok': False, 'erro': str(ex)[:200]}


# ── Lote D (16/10/26, aprovado pelo Diogo): preço do bar, teto, pausa ──────────────────────────────────
# A1: cada bar guarda o preço do dia do cadastro (pubshow_businesses.plano_cent); os 10 primeiros travam por 24
# meses (preco_trava_ate). Vale nos 2 modos: no Asaas é o valor da assinatura; na Efí, o teto da mensalidade.
# A4: teto de R$ 50 por momento só no modo Efí (CC1 X1: no split a devolução sai inteira da conta do PubShow).
# A3: pausa de 60 dias sem momento pago, só no modo Efí. Tudo passa por _efi_para(b): no Asaas nada disso roda.
PRECO_TETO_EFI = 50.0
MSG_PAUSADO = ('O PubShow está pausado neste bar. Responsável pelo bar: fale com o suporte no WhatsApp '
               f'{SUPORTE_WA_FMT}.')


def _preco_cent_bar(b) -> int:
    """Preço (centavos) da mensalidade DESTE bar: o guardado no cadastro; sem ele, PUBSHOW_PLANO_CENT."""
    try:
        v = b['plano_cent'] if 'plano_cent' in b.keys() else None
        if v is not None and int(v) > 0:
            return int(v)
    except Exception:
        pass
    return _plano_cent()


def _plano_do_bar(b) -> dict:
    """O plano único com o preço DESTE bar (fundador: o travado). Painel e checkout mostram o que /assinar cobra."""
    c = _preco_cent_bar(b) if b else _plano_cent()
    return dict(PLANO, preco=c / 100, preco_cent=c, preco_fmt=_brl_cent(c))


def fundador_vagas() -> int:
    """Lote D A1: vagas de fundador que ainda sobram (das 10, contado de verdade no banco). Pro site (fatia B): a
    oferta "R$ 79,90 travado por 24 meses para os 10 primeiros bares" some quando der 0. Erro de leitura = 0."""
    try:
        conn = get_pubshow_db()
        try:
            return _fundador_vagas_db(conn)
        finally:
            conn.close()
    except Exception as ex:
        log.warning('[PUBSHOW] vagas de fundador não lidas: %s', ex)
        return 0


# Lote D B (16/10/26, aprovado): cadastro, boas-vindas e e-mail falam do jeito do MODO do bar. Asaas = o texto de hoje.
DICA_2_BARES = 'Tem mais de um bar? Cadastre cada um com um e-mail diferente.'
DICA_TESTE_1_BAR = 'O teste grátis vale só no 1º bar de cada CPF/CNPJ.'   # 17/10/26 (decisão 6): só fora da Efí


def _dica_2_bares(efi: bool) -> str:
    """A dica do 2º bar; a frase do teste grátis só no modo Asaas (na Efí não existe teste grátis)."""
    return DICA_2_BARES if efi else f'{DICA_2_BARES} {DICA_TESTE_1_BAR}'


def _segundo_bar_do_doc(b) -> str:
    """Crítico leigo A1 (17/10/26): nome do 1º bar do mesmo CPF/CNPJ quando ESTE é um bar posterior ('' se não é)."""
    try:
        d = _doc_norm(b.get('cpf_cnpj'))
        if not d:
            return ''
        conn = get_pubshow_db()
        try:
            for r in conn.execute("SELECT id, nome, cpf_cnpj FROM pubshow_businesses WHERE id < ? ORDER BY id",
                                  (b['id'],)):
                if _doc_norm(r['cpf_cnpj']) == d:
                    return r['nome'] or 'teu 1º bar'
        finally:
            conn.close()
    except Exception:
        pass
    return ''


def _doc_ja_tem_bar(conn, doc) -> bool:
    """17/10/26 (decisão 6 do Diogo): já existe bar com este CPF/CNPJ? (compara normalizado: cadastro antigo pode ter
    pontuação). O 2º bar do mesmo dono nasce sem teste grátis, direto no Assinar (o cadastro não é bloqueado)."""
    d = _doc_norm(doc)
    if not d:
        return False
    for r in conn.execute("SELECT cpf_cnpj FROM pubshow_businesses WHERE COALESCE(cpf_cnpj,'')<>''"):
        if _doc_norm(r[0]) == d:
            return True
    return False


def _efi_cadastro_novo() -> bool:
    """O bar que se cadastra AGORA já nasce na Efí? Só com PUBSHOW_EFI_BARES='*' (e sem o botão de emergência): o code
    dele ainda não existe, e a lista por code nunca inclui um bar novo (ele entra pelo Asaas, como hoje).
    17/10/26 (caminho único): nasce sem conta Efí, então vende pela PONTE até conectar a conta (aí vira split)."""
    return _efi_ligado() and pubshow_pay.ligado_para({'code': ''})


def _texto_sem_bolso(b=None) -> str:
    """A frase aprovada do modo Efí. O % (PUBSHOW_MENSAL_PCT) e o preço (o do bar; sem bar, o de hoje) vêm do código."""
    pct = pubshow_pay.mensal_pct() if pubshow_pay is not None else 30
    preco_cent = _plano_do_bar(b)['preco_cent']
    # VDB-07 (16/10/26): bar já na Efí lê o preço que o SPLIT usa (pubshow_pay.plano_do_bar = o do bar + a exceção
    # do definir_plano, ex.: o demo a R$ 3). Falhou a leitura = o preço do bar (como antes)
    if b is not None and _efi_lista(b):
        try:
            _c = _pp_conn()
            try:
                preco_cent = int(pubshow_pay.plano_do_bar(_c, b))
            finally:
                _c.close()
        except Exception as ex:
            log.warning('[PUBSHOW] preço do split do bar não lido: %s', ex)
    if preco_cent <= 0:   # isento (exceção 0): não fala de mensalidade que não existe
        return 'Sem mensalidade: este bar não paga o PubShow. Gorjeta é 100% da equipe.'
    return (f'Sem mensalidade do bolso: o PubShow se paga com {pct}% dos momentos até '
            f'{_brl_cent(preco_cent)} por mês. Gorjeta é 100% da equipe.')


def _preco_teto(b):
    """Lote D A4: teto do momento (reais) — R$ 50 na Efí (ponte ou split, caminho único 17/10/26); None = sem teto
    (Asaas, como hoje)."""
    return PRECO_TETO_EFI if _rota_efi(b) else None


def _efi_pausa(b):
    """Lote D A3: estado da pausa do bar (pubshow_pay.pausa_do_bar) — só com a Efí ligada pro bar. None fora da Efí
    ou se a leitura falhar: erro de leitura NUNCA pausa um bar."""
    if not _efi_para(b):
        return None
    try:
        conn = _pp_conn()
        try:
            return pubshow_pay.pausa_do_bar(conn, b)
        finally:
            conn.close()
    except Exception as ex:
        log.warning('[PUBSHOW] pausa do bar %s não lida: %s', b['id'], ex)
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────
def _gerar_code(n=8):
    # secrets (15/10/26): random.choices é previsível pra código de TV/token
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(n))


# ── PIX Copia-e-Cola (EMV padrão Banco Central) ────────────────────────────────

def _crc16(data: str) -> int:
    """CRC16-CCITT — exigido pelo padrão PIX EMV."""
    crc = 0xFFFF
    for byte in data.encode('utf-8'):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def _formatar_chave_pix(chave: str, tipo: str) -> str:
    """Normaliza a chave PIX conforme tipo para o campo EMV."""
    chave = chave.strip()
    if tipo == 'telefone':
        digits = re.sub(r'\D', '', chave)
        if len(digits) == 11:          # DDD + 9 dígitos
            return f'+55{digits}'
        if len(digits) == 13 and digits.startswith('55'):
            return f'+{digits}'
        return f'+55{digits}'
    if tipo in ('cpf', 'cnpj'):
        return re.sub(r'\D', '', chave)
    return chave.lower()               # email / evp


def _pix_emv(chave_raw: str, tipo: str, nome_recebedor: str, valor: float, txid: str) -> str:
    """Gera payload EMV Pix Copia-e-Cola — padrão BCB."""
    import unicodedata

    def tlv(tag, value):
        return f'{tag}{len(value):02d}{value}'

    chave = _formatar_chave_pix(chave_raw, tipo)

    # Tag 26 — Merchant Account Info
    # Sub-tag 00 = GUI (br.gov.bcb.pix), sub-tag 01 = chave PIX
    mai = tlv('26', tlv('00', 'br.gov.bcb.pix') + tlv('01', chave))

    # Tag 62 — Additional Data (referência da transação, max 25 chars alfanumérico)
    ref = re.sub(r'[^A-Za-z0-9]', '', txid)[:25] or 'PUBSHOW'
    adf = tlv('62', tlv('05', ref))

    # Nome: sem acento, uppercase, max 25
    nome_ascii = unicodedata.normalize('NFD', nome_recebedor or 'PUBSHOW JUKEBOX')
    nome_ascii = ''.join(c for c in nome_ascii if unicodedata.category(c) != 'Mn')
    nome_ascii = re.sub(r'[^A-Za-z0-9 ]', '', nome_ascii).upper()[:25].strip()

    payload = (
        '000201'                          # Payload Format Indicator
        '010212'                          # Point of Initiation = 12 (QR único)
        + mai                             # Merchant Account Info
        + '52040000'                      # Merchant Category Code
        + '5303986'                       # Currency BRL
        + tlv('54', f'{valor:.2f}')       # Transaction Amount
        + '5802BR'                        # Country Code
        + tlv('59', nome_ascii)           # Merchant Name
        + tlv('60', 'SAO PAULO')          # Merchant City
        + adf                             # Additional Data
        + '6304'                          # CRC placeholder
    )
    return payload + f'{_crc16(payload):04X}'


def _pix_qr_b64(payload: str) -> str:
    """Gera QR code do payload PIX como PNG base64."""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8, border=2
        )
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        log.error('[PUBSHOW] Erro QR PIX: %s', e)
        return ''

def _gerar_jukebox_token():
    """Token rotativo para o QR do Jukebox — independente do code da TV."""
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))


def _ts_to_brt(ts: str) -> str:
    """Converte um timestamp ISO (UTC ou sem fuso) para horário de Brasília (UTC-3).
    Se o servidor já estiver em BRT (TZ=America/Sao_Paulo), detecta e não duplica.
    Retorna string no formato 'YYYY-MM-DD HH:MM:SS' já em hora local Brasil.
    """
    if not ts:
        return ts
    try:
        # Normaliza: aceita 'T' ou ' ', descarta microsegundos e offset
        ts_clean = ts[:19].replace('T', ' ')
        dt = datetime.strptime(ts_clean, '%Y-%m-%d %H:%M:%S')

        # created_at vem de CURRENT_TIMESTAMP do SQLite = SEMPRE UTC (o SQLite
        # ignora o TZ do SO). Então converte sempre UTC→BRT (UTC-3), igual as
        # queries SQL com '-3 hours'. (A detecção de "servidor BRT" quebrava
        # justamente quando TZ=America/Sao_Paulo era setado no Railway.)
        dt_brt = dt - timedelta(hours=3)
        return dt_brt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return ts[:19].replace('T', ' ')


def _pedidos_com_hora_local(pedidos: list) -> list:
    """Converte created_at de cada pedido para hora de Brasília."""
    resultado = []
    for p in pedidos:
        d = dict(p) if not isinstance(p, dict) else p
        d['created_at'] = _ts_to_brt(d.get('created_at', ''))
        resultado.append(d)
    return resultado


# ── Web Push — notificações nativas no celular do bar ────────────────────────

_vapid_cache: dict = {}

def _vapid_keys():
    """Retorna (pub_b64url, priv_pem) — gera e persiste no DB se não existir."""
    global _vapid_cache
    if _vapid_cache:
        return _vapid_cache['pub'], _vapid_cache['priv']
    # Env vars têm prioridade
    pub  = os.environ.get('VAPID_PUBLIC_KEY', '')
    priv = os.environ.get('VAPID_PRIVATE_KEY', '')
    if pub and priv:
        _vapid_cache = {'pub': pub, 'priv': priv}
        return pub, priv
    # Tenta banco
    try:
        conn = get_pubshow_db()
        row = conn.execute('SELECT pub_key, priv_key FROM pubshow_vapid_keys LIMIT 1').fetchone()
        if row:
            _vapid_cache = {'pub': row['pub_key'], 'priv': row['priv_key']}
            conn.close()
            return row['pub_key'], row['priv_key']
        # Gera novo par
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        import base64 as _b64
        priv_key = ec.generate_private_key(ec.SECP256R1())
        pub_raw  = priv_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        pub_b64  = _b64.urlsafe_b64encode(pub_raw).rstrip(b'=').decode()
        priv_pem = priv_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()).decode()
        conn.execute('INSERT OR IGNORE INTO pubshow_vapid_keys (id, pub_key, priv_key) VALUES (1,?,?)',
                     (pub_b64, priv_pem))
        conn.commit(); conn.close()
        _vapid_cache = {'pub': pub_b64, 'priv': priv_pem}
        return pub_b64, priv_pem
    except Exception as _e:
        log.warning('[Push] Erro ao gerar VAPID: %s', _e)
        try: conn.close()
        except: pass
        return '', ''


def _enviar_push_pedido(business_id: int, tipo_emoji: str, tipo_nome: str,
                        nome_cliente: str, valor: float, pedido_id: int = 0):
    """Envia Web Push para todas as subscriptions ativas do bar."""
    pub, priv = _vapid_keys()
    if not pub or not priv:
        return
    try:
        import json as _json
        try:
            from pywebpush import webpush, WebPushException
        except ImportError:
            log.debug('[Push] pywebpush não instalado — push desabilitado')
            return
        conn = get_pubshow_db()
        subs = conn.execute(
            'SELECT subscription FROM pubshow_push_subscriptions WHERE business_id=?',
            (business_id,)
        ).fetchall()
        conn.close()
        if not subs:
            return
        payload = _json.dumps({
            'titulo':    f'{tipo_emoji} Novo pedido no Jukebox!',
            'corpo':     f'{nome_cliente} — {tipo_nome} · R$ {valor:.2f}',
            'url':       '/pubshow/painel',
            'pedido_id': pedido_id  # usado no SW para tag única por pedido
        })
        mortos = []
        for row in subs:
            try:
                sub_info = _json.loads(row['subscription'])
                webpush(
                    subscription_info=sub_info,
                    data=payload,
                    vapid_private_key=priv,
                    vapid_claims={'sub': 'mailto:contato@4kitem.com.br'}
                )
            except WebPushException as _wpe:
                if _wpe.response and _wpe.response.status_code in (404, 410):
                    mortos.append(row['subscription'])
            except Exception as _pe:
                log.debug('[Push] Erro ao enviar push: %s', _pe)
        # Remove subscriptions expiradas
        if mortos:
            conn2 = get_pubshow_db()
            for s in mortos:
                conn2.execute(
                    'DELETE FROM pubshow_push_subscriptions WHERE subscription=?', (s,))
            conn2.commit(); conn2.close()
    except Exception as _e:
        log.warning('[Push] Erro geral: %s', _e)


# ── Email onboarding ──────────────────────────────────────────────────────────

_email_last_check = 0.0   # timestamp da última verificação da fila (rate limit)
_email_lock = _threading.Lock()

def _pubshow_enviar_email(para: str, assunto: str, html: str):
    """Envia email via Resend API. Usa RESEND_API_KEY do environment.

    True = enviado · False = falhou ESTE e-mail · None = problema de CONFIGURAÇÃO ou do Resend
    (sem chave, chave/domínio recusado 401/403, limite 429): a fila não queima a tentativa (15/10/26).
    None é falso como False pra quem só testa `if not ok`."""
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        log.warning('[PUBSHOW email] RESEND_API_KEY não configurada — email não enviado')
        return None
    # Padrão @4kitem.com.br (15/10/26): pubshow.com.br não existe — o Resend recusava em silêncio.
    from_addr = os.environ.get('PUBSHOW_EMAIL_FROM',
                  os.environ.get('EMAIL_FROM', 'PUBSHOW <noreply@4kitem.com.br>'))
    try:
        r = _requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'from': from_addr, 'to': [para], 'subject': assunto, 'html': html},
            timeout=10
        )
        ok = r.status_code in (200, 201)
        if not ok:
            log.error('[PUBSHOW email] Resend erro %s: %s', r.status_code, r.text[:200])
            if r.status_code in (401, 403, 429):   # chave errada / domínio não verificado / limite
                return None
        return ok
    except Exception as e:
        log.error('[PUBSHOW email] Exceção ao enviar: %s', e)
        return False


def _email_html_base(titulo: str, corpo: str) -> str:
    """Wrapper HTML base para todos os emails — dark theme PUBSHOW."""
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo}</title>
</head>
<body style="margin:0;padding:0;background:#0d0d14;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d0d14;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#08080f 0%,#131320 100%);border-radius:16px 16px 0 0;padding:32px 40px;text-align:center;border-bottom:2px solid #4ade80;">
          <div style="font-size:32px;margin-bottom:8px;">🎵</div>
          <div style="color:#4ade80;font-size:22px;font-weight:800;letter-spacing:2px;">PUBSHOW</div>
          <div style="color:#6b7280;font-size:12px;letter-spacing:1px;margin-top:4px;">JUKEBOX DIGITAL</div>
        </td></tr>

        <!-- Body -->
        <tr><td style="background:#131320;padding:40px;border-radius:0 0 16px 16px;">
          {corpo}
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:24px 0;text-align:center;">
          <p style="color:#374151;font-size:12px;margin:0;">
            PUBSHOW Jukebox Digital &bull; Você recebeu este email por ser cliente PUBSHOW.<br>
            Suporte: <a href="https://wa.me/{SUPORTE_WA}" style="color:#4ade80;text-decoration:none;">WhatsApp {SUPORTE_WA_FMT}</a>
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _email_boas_vindas(nome: str, code: str, token: str, plano: str, trial_ends: str, bar=None) -> tuple:
    """Email dia 0 — boas-vindas com links da TV e Jukebox.
    Lote D B (16/10/26): `bar` (a linha do bar) decide o modo. Efí → sem teste grátis (não existe na Efí), a frase
    "Sem mensalidade do bolso…" e o passo 4 "Abra a conta Efí do bar". Asaas (ou sem bar) → o texto de hoje."""
    efi = bool(bar is not None and _efi_lista(bar))   # 17/10/26: bar da lista (nasce sem conta: ponte até conectar)
    nome = _html_escape(nome or '')   # 15/10/26: nome é texto livre do cadastro e vai no HTML do e-mail
    base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br')
    tv_url = f'{base_url}/bar/{code}'   # link curto, dá pra digitar no controle da TV (15/10/26)
    jk_url = f'{base_url}/pubshow/jukebox/{token}'
    painel_url = f'{base_url}/pubshow/painel'

    try:
        trial_fmt = datetime.fromisoformat(trial_ends[:19]).strftime('%d/%m/%Y')
    except Exception:
        trial_fmt = '7 dias'
    _sem_teste = bool(bar is not None and not efi and not bar['plano_ativo']
                      and (trial_ends or '')[:19] <= datetime.now().isoformat()[:19])
    if efi:
        intro = _html_escape(_texto_sem_bolso(bar)) + '<br>'
    elif _sem_teste:
        # 17/10/26 (decisão 6): 2º bar do mesmo CPF/CNPJ — sem teste grátis
        intro = ('Seu Jukebox está pronto. O teste grátis vale só no 1º bar de cada CPF/CNPJ: '
                 'assine no painel pra ligar este bar.')
    else:
        intro = ('Seu Jukebox está pronto e no ar. Seu teste grátis vai até '
                 f'<strong style="color:#4ade80">{trial_fmt}</strong>.')
    passo_conta = _email_passo_conta_efi(painel_url) if efi else ''   # VDB-04 (16/10/26): título "em 4 passos" na Efí

    corpo = f"""
      <h1 style="color:#f9fafb;font-size:26px;font-weight:800;margin:0 0 8px;">
        Bem-vindo ao PUBSHOW, {nome.split()[0]}! 🎉
      </h1>
      <p style="color:#9ca3af;font-size:15px;margin:0 0 28px;line-height:1.6;">
        {intro}
        Abra a TV, cole o link e os clientes já podem pedir músicas pelo celular!
      </p>

      <!-- Links box -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#08080f;border-radius:12px;border:1px solid #1f2937;margin-bottom:28px;">
        <tr><td style="padding:20px 24px;">
          <p style="color:#6b7280;font-size:11px;font-weight:700;letter-spacing:1px;margin:0 0 12px;text-transform:uppercase;">Seus links</p>

          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;">
            <tr>
              <td style="color:#4ade80;font-size:13px;font-weight:700;padding-bottom:4px;">📺 TV / Telão</td>
            </tr>
            <tr>
              <td>
                <a href="{tv_url}" style="color:#e5e7eb;font-size:13px;text-decoration:none;word-break:break-all;">{tv_url}</a>
              </td>
            </tr>
          </table>

          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="color:#a78bfa;font-size:13px;font-weight:700;padding-bottom:4px;">🎵 Jukebox (clientes)</td>
            </tr>
            <tr>
              <td>
                <a href="{jk_url}" style="color:#e5e7eb;font-size:13px;text-decoration:none;word-break:break-all;">{jk_url}</a>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>

      <!-- Steps -->
      <p style="color:#9ca3af;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin:0 0 16px;">Como funciona em {4 if efi else 3} passos</p>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
        <tr>
          <td style="padding:12px 16px;background:#1a1a2e;border-radius:8px;margin-bottom:8px;" valign="top">
            <div style="color:#4ade80;font-size:18px;font-weight:800;display:inline;">1.</div>
            <span style="color:#e5e7eb;font-size:14px;"> Abra o link da TV em qualquer navegador no telão do seu bar</span>
          </td>
        </tr>
        <tr><td style="height:8px;"></td></tr>
        <tr>
          <td style="padding:12px 16px;background:#1a1a2e;border-radius:8px;" valign="top">
            <div style="color:#4ade80;font-size:18px;font-weight:800;display:inline;">2.</div>
            <span style="color:#e5e7eb;font-size:14px;"> Coloque o QR code visível para os clientes escanear com o celular</span>
          </td>
        </tr>
        <tr><td style="height:8px;"></td></tr>
        <tr>
          <td style="padding:12px 16px;background:#1a1a2e;border-radius:8px;" valign="top">
            <div style="color:#4ade80;font-size:18px;font-weight:800;display:inline;">3.</div>
            <span style="color:#e5e7eb;font-size:14px;"> O cliente pede pelo celular e aparece na TV sozinho. Só cantada e pedido de namoro esperam o seu OK no painel.</span>
          </td>
        </tr>{passo_conta}
      </table>
      <p style="color:#6b7280;font-size:12px;margin:0 0 20px;text-align:center;">{_dica_2_bares(efi)}</p>

      <!-- CTA -->
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td align="center">
            <a href="{painel_url}" style="display:inline-block;background:#4ade80;color:#08080f;font-weight:800;font-size:16px;padding:14px 40px;border-radius:8px;text-decoration:none;letter-spacing:0.5px;">
              Abrir painel de gestão →
            </a>
          </td>
        </tr>
      </table>
    """
    assunto = '🎵 Seu Jukebox está no ar!'   # 15/10/26: o código da TV saiu do assunto (aparecia em notificação)
    return assunto, _email_html_base('Bem-vindo ao PUBSHOW', corpo)


def _email_passo_conta_efi(painel_url: str) -> str:
    """Lote D B (16/10/26): o passo 4 do e-mail de boas-vindas, só no modo Efí. Sem a conta, só a música grátis."""
    return f"""
        <tr><td style="height:8px;"></td></tr>
        <tr>
          <td style="padding:12px 16px;background:#1a1a2e;border-radius:8px;" valign="top">
            <div style="color:#4ade80;font-size:18px;font-weight:800;display:inline;">4.</div>
            <span style="color:#e5e7eb;font-size:14px;"> <strong>Abra a conta Efí do bar</strong> (grátis, pelo app da Efí, no mesmo CPF/CNPJ do cadastro) e diga o número dela no <a href="{painel_url}#tut-conta" style="color:#4ade80;">passo 4 do painel</a>. É nela que o dinheiro dos momentos cai, na hora. Sem ela, só a música grátis funciona.</span>
          </td>
        </tr>"""


def _email_dica_setup(nome: str, code: str) -> tuple:
    """Email dia 2 — dicas de configuração (PIX, QR, preços)."""
    nome = _html_escape(nome or '')   # 15/10/26: nome é texto livre do cadastro e vai no HTML do e-mail
    base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br')
    painel_url = f'{base_url}/pubshow/painel'

    corpo = f"""
      <h1 style="color:#f9fafb;font-size:24px;font-weight:800;margin:0 0 8px;">
        Já configurou seu Jukebox, {nome.split()[0]}? ⚙️
      </h1>
      <p style="color:#9ca3af;font-size:15px;margin:0 0 28px;line-height:1.6;">
        Em 5 minutos você deixa o PUBSHOW tocando. Pedir música é grátis pro cliente; ele paga no PIX só os momentos (furar a fila, parabéns, recado na TV).
        Veja o que ainda falta configurar:
      </p>

      <!-- Checklist -->
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">

        <tr><td style="padding:14px 16px 14px 16px;background:#0d1f0d;border-radius:8px;border-left:3px solid #4ade80;margin-bottom:8px;">
          <div style="color:#4ade80;font-size:15px;font-weight:700;margin-bottom:4px;">📺 Ligue a TV do bar</div>
          <div style="color:#9ca3af;font-size:13px;line-height:1.5;">
            No navegador da TV, digite 4kitem.com.br/bar e o código <strong style="color:#e5e7eb">{code}</strong>. O painel mostra quando a TV está ligada.
          </div>
        </td></tr>

        <tr><td style="height:10px;"></td></tr>

        <tr><td style="padding:14px 16px;background:#12121f;border-radius:8px;border-left:3px solid #a78bfa;">
          <div style="color:#a78bfa;font-size:15px;font-weight:700;margin-bottom:4px;">🎛️ Personalize os preços</div>
          <div style="color:#9ca3af;font-size:13px;line-height:1.5;">
            Defina o valor de cada momento pago (furar a fila, parabéns...) conforme o seu público. Música é sempre grátis.
          </div>
        </td></tr>

        <tr><td style="height:10px;"></td></tr>

        <tr><td style="padding:14px 16px;background:#12121f;border-radius:8px;border-left:3px solid #f59e0b;">
          <div style="color:#f59e0b;font-size:15px;font-weight:700;margin-bottom:4px;">📺 Imprima o QR Code</div>
          <div style="color:#9ca3af;font-size:13px;line-height:1.5;">
            Coloque na mesa ou balcão para os clientes escanear. Quanto mais visível, mais pedidos!
          </div>
        </td></tr>

        <tr><td style="height:10px;"></td></tr>

        <tr><td style="padding:14px 16px;background:#12121f;border-radius:8px;border-left:3px solid #06b6d4;">
          <div style="color:#06b6d4;font-size:15px;font-weight:700;margin-bottom:4px;">🎬 Adicione seus anúncios</div>
          <div style="color:#9ca3af;font-size:13px;line-height:1.5;">
            Anuncie promoções e pratos especiais diretamente na tela entre os vídeos.
          </div>
        </td></tr>

      </table>

      <!-- CTA -->
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td align="center">
            <a href="{painel_url}" style="display:inline-block;background:#4ade80;color:#08080f;font-weight:800;font-size:16px;padding:14px 40px;border-radius:8px;text-decoration:none;">
              Configurar agora →
            </a>
          </td>
        </tr>
      </table>
    """
    assunto = '📺 Já ligou a TV e colou o QR nas mesas?'
    return assunto, _email_html_base('Dicas de configuração', corpo)


def _email_trial_ending(nome: str, plano: str, trial_ends: str, bar=None) -> tuple:
    """Email dia 5 — trial acaba em 2 dias."""
    nome = _html_escape(nome or '')   # 15/10/26: nome é texto livre do cadastro e vai no HTML do e-mail
    base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br')
    planos_url = f'{base_url}/pubshow/planos'
    # VDA-04 (16/10/26): o preço guardado DESTE bar (fundador: o travado), o mesmo que o /assinar cobra
    nome_plano, preco_plano = PLANO['nome'], _plano_do_bar(bar)['preco_fmt']

    try:
        trial_fmt = datetime.fromisoformat(trial_ends[:19]).strftime('%d/%m/%Y')
    except Exception:
        trial_fmt = 'em 2 dias'

    corpo = f"""
      <h1 style="color:#f9fafb;font-size:24px;font-weight:800;margin:0 0 8px;">
        ⏰ Seu teste grátis acaba em 2 dias, {nome.split()[0]}
      </h1>
      <p style="color:#9ca3af;font-size:15px;margin:0 0 28px;line-height:1.6;">
        Seu período gratuito encerra em <strong style="color:#f59e0b">{trial_fmt}</strong>.
        Assine agora para não perder o acesso ao seu Jukebox.
      </p>

      <!-- Perda de acesso -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#1f0a0a;border:1px solid #7f1d1d;border-radius:12px;margin-bottom:24px;">
        <tr><td style="padding:20px 24px;">
          <p style="color:#f87171;font-size:14px;font-weight:700;margin:0 0 12px;">Se você não assinar até {trial_fmt}:</p>
          <p style="color:#9ca3af;font-size:13px;margin:4px 0;">❌ O Jukebox fica offline para seus clientes</p>
          <p style="color:#9ca3af;font-size:13px;margin:4px 0;">❌ Os pedidos de música param de funcionar</p>
          <p style="color:#9ca3af;font-size:13px;margin:4px 0;">❌ A TV para de tocar os clips</p>
        </td></tr>
      </table>

      <!-- Plano recomendado -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1f0d;border:1px solid #166534;border-radius:12px;margin-bottom:28px;">
        <tr><td style="padding:20px 24px;">
          <p style="color:#4ade80;font-size:14px;font-weight:700;margin:0 0 8px;">✅ Continue com o plano {nome_plano}</p>
          <p style="color:#6b7280;font-size:13px;margin:0 0 4px;">Apenas <strong style="color:#4ade80;font-size:18px;">{preco_plano}/mês</strong></p>
          <p style="color:#9ca3af;font-size:13px;margin:0;">Cancele a qualquer momento &bull; Sem fidelidade</p>
        </td></tr>
      </table>

      <!-- CTA -->
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td align="center">
            <a href="{planos_url}" style="display:inline-block;background:#f59e0b;color:#08080f;font-weight:800;font-size:16px;padding:14px 40px;border-radius:8px;text-decoration:none;">
              Garantir minha assinatura →
            </a>
          </td>
        </tr>
      </table>
    """
    assunto = f'⏰ Faltam 2 dias! Garanta o PUBSHOW no seu bar'
    return assunto, _email_html_base('Teste grátis acabando', corpo)


def _email_trial_expirado(nome: str, plano: str, bar=None) -> tuple:
    """Email dia 7+ — trial expirado."""
    nome = _html_escape(nome or '')   # 15/10/26: nome é texto livre do cadastro e vai no HTML do e-mail
    base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br')
    planos_url = f'{base_url}/pubshow/planos'
    # VDA-04 (16/10/26): o preço guardado DESTE bar (fundador: o travado), o mesmo que o /assinar cobra
    nome_plano, preco_plano = PLANO['nome'], _plano_do_bar(bar)['preco_fmt']

    corpo = f"""
      <h1 style="color:#f9fafb;font-size:24px;font-weight:800;margin:0 0 8px;">
        Seu teste grátis acabou 😢
      </h1>
      <p style="color:#9ca3af;font-size:15px;margin:0 0 28px;line-height:1.6;">
        Oi {nome.split()[0]}, seu período gratuito no PUBSHOW encerrou.
        Mas a boa notícia: você pode reativar o Jukebox agora mesmo e voltar a receber pedidos de música!
      </p>

      <!-- O que você estava usando -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#12121f;border-radius:12px;border:1px solid #1f2937;margin-bottom:24px;">
        <tr><td style="padding:20px 24px;">
          <p style="color:#6b7280;font-size:12px;font-weight:700;letter-spacing:1px;margin:0 0 12px;text-transform:uppercase;">O que você vai reativar</p>
          <p style="color:#e5e7eb;font-size:13px;margin:6px 0;">🎵 TV do bar tocando os clipes</p>
          <p style="color:#e5e7eb;font-size:13px;margin:6px 0;">📱 Jukebox para os clientes pedirem músicas</p>
          <p style="color:#e5e7eb;font-size:13px;margin:6px 0;">💰 Momentos pagos no PIX (parabéns, furar a fila…)</p>
          <p style="color:#e5e7eb;font-size:13px;margin:6px 0;">📊 Painel de gestão completo</p>
        </td></tr>
      </table>

      <!-- Preço -->
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1f0d;border:1px solid #166534;border-radius:12px;margin-bottom:28px;">
        <tr><td style="padding:20px 24px;text-align:center;">
          <p style="color:#4ade80;font-size:13px;font-weight:700;margin:0 0 4px;">Plano {nome_plano}</p>
          <p style="color:#4ade80;font-size:32px;font-weight:800;margin:0 0 4px;">{preco_plano}<span style="font-size:16px;font-weight:400;color:#6b7280;">/mês</span></p>
          <p style="color:#6b7280;font-size:12px;margin:0;">Cancele quando quiser &bull; Sem fidelidade</p>
        </td></tr>
      </table>

      <!-- CTA -->
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td align="center">
            <a href="{planos_url}" style="display:inline-block;background:#4ade80;color:#08080f;font-weight:800;font-size:16px;padding:14px 40px;border-radius:8px;text-decoration:none;">
              Reativar meu Jukebox →
            </a>
          </td>
        </tr>
      </table>

      <p style="color:#4b5563;font-size:12px;margin-top:24px;text-align:center;">
        Dúvidas? Fale com a gente no <a href="https://wa.me/{SUPORTE_WA}" style="color:#4ade80;">WhatsApp {SUPORTE_WA_FMT}</a>
      </p>
    """
    assunto = '🔴 Seu teste grátis acabou — Reative o PUBSHOW agora'
    return assunto, _email_html_base('Teste grátis encerrado', corpo)


def _agendar_emails_onboarding(business_id: int, trial_ends: str, sem_teste: bool = False):
    """Agenda os 4 emails de onboarding para um novo cadastro.
    17/10/26 (decisão 6): sem_teste=True (2º bar do mesmo CPF/CNPJ) → só boas-vindas e dica (sem "teste acaba")."""
    agora = datetime.now()
    emails = [
        ('boas_vindas',   agora + timedelta(minutes=1)),    # imediato (~1min)
        ('dica_setup',    agora + timedelta(days=2)),         # dia 2
        ('trial_ending',  agora + timedelta(days=5)),         # dia 5 (2 dias antes do fim)
        ('trial_expirado', agora + timedelta(days=7, hours=1)), # dia 7 (logo após expirar)
    ]
    if sem_teste:
        emails = emails[:2]
    try:
        conn = get_pubshow_db()
        for tipo, quando in emails:
            conn.execute(
                'INSERT INTO pubshow_email_queue (business_id, tipo, scheduled_at) VALUES (?,?,?)',
                (business_id, tipo, quando.isoformat())
            )
        conn.commit()
        conn.close()
        log.info('[PUBSHOW email] %d emails agendados para business_id=%d', len(emails), business_id)
    except Exception as e:
        log.error('[PUBSHOW email] Erro ao agendar: %s', e)


def _processar_fila_emails():
    """Verifica e envia emails pendentes da fila. Chame de qualquer rota frequente."""
    global _email_last_check
    now_ts = _time.time()
    # Rate limit: só verifica a cada 10 minutos (lock: 2 threads ao mesmo tempo não mandam em dobro)
    with _email_lock:
        if now_ts - _email_last_check < 600:
            return
        _email_last_check = now_ts

    try:
        conn = get_pubshow_db()
        pendentes = conn.execute(
            '''SELECT q.id, q.tipo, q.scheduled_at, q.business_id,
                      b.nome, b.email, b.code, b.jukebox_token, b.plano,
                      b.trial_ends, b.plano_ativo, b.plano_cent
               FROM pubshow_email_queue q
               JOIN pubshow_businesses b ON b.id = q.business_id
               WHERE q.sent_at IS NULL
                 AND COALESCE(q.tentativas, 0) < ?
                 AND q.scheduled_at <= ?
               ORDER BY COALESCE(q.tentativas, 0) ASC, q.scheduled_at ASC
               LIMIT 20''',
            (_EMAIL_MAX_TENTATIVAS, datetime.now().isoformat())
        ).fetchall()
        conn.close()
    except Exception as e:
        log.error('[PUBSHOW email] Erro ao buscar fila: %s', e)
        return

    for row in pendentes:
        tipo      = row['tipo']
        nome      = row['nome']
        email     = row['email']
        code      = row['code']
        token     = row['jukebox_token'] or code
        plano     = row['plano'] or 'bar'
        trial_end = row['trial_ends'] or ''
        # 17/10/26 (caminho único): o row['id'] é o da FILA. As funções do modo do bar (rota Efí, preço do split) leem
        # bar['id'] = o do BAR (antes o "sem bolso" do e-mail lia a exceção de preço de outro bar pelo id da fila)
        bar = dict(row)
        bar['id'] = row['business_id']

        try:
            if tipo == 'boas_vindas':
                assunto, html = _email_boas_vindas(nome, code, token, plano, trial_end, bar)   # Lote D B: modo do bar
            elif tipo == 'dica_setup':
                assunto, html = _email_dica_setup(nome, code)
            elif tipo == 'trial_ending':
                # Só envia se ainda não assinou. VCa-01 (16/10/26): bar na Efí não tem teste grátis (a mensalidade
                # sai dos momentos; cancelou = já desligou na hora) → "o teste acaba / fica offline" seria falso
                if row['plano_ativo'] or _efi_para(bar):
                    _marcar_email_enviado(row['id'])
                    continue
                assunto, html = _email_trial_ending(nome, plano, trial_end, bar)
            elif tipo == 'trial_expirado':
                if row['plano_ativo'] or _efi_para(bar):
                    _marcar_email_enviado(row['id'])
                    continue
                assunto, html = _email_trial_expirado(nome, plano, bar)
            else:
                _marcar_email_enviado(row['id'])
                continue

            ok = _pubshow_enviar_email(email, assunto, html)
            if ok is None:
                # configuração (sem RESEND_API_KEY, domínio não verificado…): vale pra todos os
                # e-mails — para a rodada SEM contar tentativa; a próxima rodada tenta de novo
                log.warning('[PUBSHOW email] fila parada: e-mail não configurado (nenhuma tentativa contada)')
                break
            if ok:
                _marcar_email_enviado(row['id'])
                log.info('[PUBSHOW email] Enviado "%s" para %s (id=%d)', tipo, email, row['id'])
            else:
                log.warning('[PUBSHOW email] Falhou "%s" para %s', tipo, email)
                _marcar_email_falha(row['id'])
        except Exception as e:
            log.error('[PUBSHOW email] Erro ao processar id=%d tipo=%s: %s', row['id'], tipo, e)
            _marcar_email_falha(row['id'])


_EMAIL_MAX_TENTATIVAS = 4
# Espera depois da 1ª, 2ª e 3ª falha (15/10/26): uma queda do Resend de meia hora não queima o e-mail.
_EMAIL_ESPERA_MIN = (10, 60, 360)


def _marcar_email_falha(queue_id: int):
    """Conta 1 tentativa e empurra o scheduled_at (10 min → 1 h → 6 h); com 4 falhas o e-mail sai
    da fila (fica no banco com tentativas=4, sent_at NULL)."""
    try:
        conn = get_pubshow_db()
        row = conn.execute('SELECT COALESCE(tentativas,0) FROM pubshow_email_queue WHERE id=?', (queue_id,)).fetchone()
        n = (row[0] if row else 0) + 1
        espera = _EMAIL_ESPERA_MIN[min(n, len(_EMAIL_ESPERA_MIN)) - 1]
        conn.execute('UPDATE pubshow_email_queue SET tentativas=?, scheduled_at=? WHERE id=?',
                     (n, (datetime.now() + timedelta(minutes=espera)).isoformat(), queue_id))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error('[PUBSHOW email] Erro ao contar falha id=%d: %s', queue_id, e)


def _processar_fila_emails_em_fundo():
    """Para rotas de tela (GET /painel): nunca envia e-mail dentro da requisição (15/10/26 — até
    20 envios × 10 s seguravam o worker). Respeita a mesma janela de 10 min e roda numa thread."""
    if _time.time() - _email_last_check < 600:
        return
    _threading.Thread(target=_processar_fila_emails, daemon=True, name='pubshow-emails').start()


def _marcar_email_enviado(queue_id: int):
    """Marca um email da fila como enviado."""
    try:
        conn = get_pubshow_db()
        conn.execute(
            'UPDATE pubshow_email_queue SET sent_at=? WHERE id=?',
            (datetime.now().isoformat(), queue_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error('[PUBSHOW email] Erro ao marcar enviado id=%d: %s', queue_id, e)


def _jukebox_aberto(b):
    """Verifica se o jukebox está aberto — plano/trial, horário, status."""
    if b['suspenso']:
        return False, 'Estabelecimento temporariamente indisponível.'

    # ── Verifica plano ativo ou trial vigente ─────────────────────────────────
    # Lote C (16/10/26): com a Efí ligada pro bar, abre pelo plano Efí (mensalidade saindo de 30% dos momentos
    # pelo split, Lote D A2), não pela assinatura do Asaas. Suspenso segue fechado (acima).
    # VCa-01: no modo Efí o interruptor é o plano_ativo (cadastro grava 1; cancelar/desativar/inadimplência
    # gravam 0). Não há teste grátis na Efí (o bar nunca tira do bolso): cancelou = desliga na hora, como a
    # tela promete. Mês pago no Asaas (ativo_ate) segue valendo, sem tirar mensalidade no split (jukebox()).
    _efi_bar = _efi_para(b)
    if _efi_bar:
        # Lote D A3 (16/10/26): 60 dias sem momento pago → pausa tudo (momentos E música grátis) até o suporte reativar
        _pz = _efi_pausa(b)
        if _pz and _pz['pausado']:
            return False, MSG_PAUSADO
    plano_ok = bool(b['plano_ativo'])
    if not plano_ok and not _efi_bar:
        trial_ends = (b['trial_ends'] or '')[:19]  # 'YYYY-MM-DDTHH:MM:SS'
        if trial_ends and trial_ends >= datetime.now().isoformat()[:19]:
            plano_ok = True   # ainda no trial
    if not plano_ok:
        # Cancelou com o mês pago (15/10/26): fica aberto até o fim do período pago
        ativo_ate = ((b['ativo_ate'] if 'ativo_ate' in b.keys() else None) or '')[:10]
        if ativo_ate and ativo_ate >= datetime.now().strftime('%Y-%m-%d'):
            plano_ok = True
    if not plano_ok:
        return False, 'Jukebox indisponível. Fale com o responsável do estabelecimento.'

    if not b['jukebox_ativo']:
        return False, 'Jukebox desativado pelo estabelecimento.'

    # ── Verifica horário de funcionamento ─────────────────────────────────────
    hora_ini = b['jukebox_hora_ini'] or '00:00'
    hora_fim = b['jukebox_hora_fim'] or '23:59'
    if hora_ini != '00:00' or hora_fim != '23:59':
        agora = datetime.now().strftime('%H:%M')
        if hora_ini <= hora_fim:
            if not (hora_ini <= agora <= hora_fim):
                return False, f'Jukebox disponível das {hora_ini} às {hora_fim}.'
        else:  # passa da meia-noite
            if not (agora >= hora_ini or agora <= hora_fim):
                return False, f'Jukebox disponível das {hora_ini} às {hora_fim}.'
    return True, ''


def _tipos_disponiveis(b):
    """Retorna TIPOS_PEDIDO filtrado pelo que o bar não bloqueou."""
    import json
    try:
        bloqueados = json.loads(b['tipos_bloqueados'] or '[]')
    except Exception:
        bloqueados = []
    # 21/10/26 (pedido do Laranjeira): "Escolher a música" da lista do bar é sempre grátis e vale sempre, igual à do
    # YouTube. O painel não tem mais o interruptor dele (saiu na versão grande, 405ebba): bar que tinha desligado antes
    # ficava preso, e na lista do bar só aparecia "Furar a fila".
    tipos = {k: v for k, v in TIPOS_PEDIDO.items()
             if (k not in bloqueados or k == 'musica_especifica') and k not in TIPOS_OCULTOS}
    # ⏩ Pular a espera (20/10/26): só na Efí e com música pra pedir (YouTube ou a lista do bar)
    if 'pular_espera' in tipos and not (('musica_externa' in tipos or 'musica_especifica' in tipos)
                                        and _pular_espera_para(b)):
        del tipos['pular_espera']
    return tipos


def _youtube_liberado(b):
    """Interruptor do BAR (11/09/26): o checkbox "Buscar no YouTube" nos Ajustes manda em TUDO
    (Escolher, Furar a fila). Desligado = cliente só escolhe do acervo curado.
    18/10/26 (trava eleitoral, C2 do verificador): no dia da votação, desligado em todo bar."""
    return 'musica_externa' in _tipos_disponiveis(b) and not _dia_da_votacao()   # plano único: sem trava de plano (15/10/26)


def _videos_bloqueados(b):
    import json
    try:
        v = json.loads((b['videos_bloqueados'] if 'videos_bloqueados' in b.keys() else None) or '[]')
        return [x for x in v if isinstance(x, str)]
    except Exception:
        return []


def _oembed_status(yid):
    """(status_http, json|None) do oEmbed do YouTube. 200 = existe e é embutível; 401/403 = dono
    bloqueou embed; 404 = removido; 0 = rede falhou (não bloquear o cliente por isso)."""
    try:
        r = _requests.get('https://www.youtube.com/oembed',
                          params={'url': f'https://www.youtube.com/watch?v={yid}', 'format': 'json'}, timeout=4)
        return r.status_code, (r.json() if r.status_code == 200 else None)
    except Exception:
        return 0, None


def _validar_video_pedido(b, youtube_id, info=None):
    """Retorna '' se o vídeo pode entrar na fila deste bar, senão a mensagem de erro pro cliente.
    Acervo curado passa direto; fora do acervo só com o YouTube liberado pelo bar E embed OK.
    `info` (dict) recebe info['titulo'] = título do ACERVO ou do oEmbed — 15/10/26: o título que
    vai pra TV/ranking/acervo não vem mais do formulário (era texto livre do cliente)."""
    if info is None:
        info = {}
    if youtube_id in _videos_bloqueados(b):
        return 'Essa música foi bloqueada pelo bar. Escolhe outra. 🙏'
    _vot = _dia_da_votacao()   # 18/10/26 (C3): no domingo, vídeo que entrou no acervo por pedido não conta como curado
    conn = get_pubshow_db()
    curado = conn.execute('SELECT titulo FROM pubshow_videos WHERE youtube_id=? AND ativo=1'
                          + (" AND COALESCE(subcategoria,'')<>'pedido_publico'" if _vot else ''), (youtube_id,)).fetchone()
    conn.close()
    if curado:
        info['titulo'] = (curado['titulo'] or '').strip()[:80]
        return ''
    if not _youtube_liberado(b):
        return MSG_SO_ACERVO_ELEICAO if _vot else 'Esse bar só aceita músicas do acervo. Escolhe na lista. 🎵'
    st, _oj = _oembed_status(youtube_id)
    if st == 200 and isinstance(_oj, dict):
        info['titulo'] = str(_oj.get('title') or '').strip()[:80]
    if st in (401, 403):
        return 'Esse clipe não toca na TV (o dono do vídeo bloqueou). Escolhe outro. 🙏'
    if st == 404:
        return 'Esse vídeo não existe mais no YouTube. Escolhe outro.'
    return ''


def _precisa_ok(b, tipo):
    """Texto livre de risco (cantada, pedido de namoro) espera o OK do gerente antes de ir pra TV —
    se o bar deixou a moderação ligada (moderar_ok, default 1).
    Trava eleitoral (18/10/26): na véspera e no dia da eleição TODO momento com texto espera o OK,
    mesmo com a moderação desligada (sem auto-OK). Música não muda: o nome dela passa pelo filtro."""
    if tipo in TIPOS_PEDIDO and tipo != 'musica' and tipo not in TIPOS_MUSICA_FILA and _janela_eleitoral():
        return True
    if not TIPOS_PEDIDO.get(tipo, {}).get('ok'):
        return False
    try:
        v = b['moderar_ok'] if 'moderar_ok' in b.keys() else 1
    except Exception:
        v = 1
    return bool(1 if v is None else v)


# ── Filtro de palavrões (16/10/26, B9) ────────────────────────────────────────
# Local, R$ 0, lista CURTA e conservadora: só palavras que não têm sentido inocente. Vale pro nome
# e pra mensagem do celular (o que vai pra TV). Título que vem do acervo/YouTube não passa aqui
# (música de verdade pode ter palavrão no nome). Desliga com PUBSHOW_FILTRO=0.
_PALAVROES = frozenset((
    'porra', 'porras', 'caralho', 'caralhos', 'krl', 'puta', 'putas', 'puto', 'putos', 'putinha',
    'putaria', 'foda', 'fodas', 'fodase', 'foder', 'fuder', 'fodido', 'fodida', 'fudido', 'fudida',
    'cu', 'cuzao', 'cuzinho', 'buceta', 'boceta', 'xoxota', 'xereca', 'piroca', 'punheta',
    'viado', 'viados', 'viadinho', 'corno', 'cornos', 'corna', 'cornudo', 'merda', 'merdas',
    'bosta', 'arrombado', 'arrombada', 'vagabunda', 'vagabundo', 'vadia', 'otario', 'otaria',
    'babaca', 'fdp', 'pqp', 'vsf', 'vtnc', 'tnc',
    'caraio', 'carai', 'krlh', 'xota', 'bct',   # 16/10/26 (crítico C2): variações que passavam
))
_PALAVROES_RAIZ = ('caralh', 'bucet', 'bocet', 'punhet', 'xoxot', 'filhodaput', 'filhadaput',
                   'arrombad', 'tomanocu')   # dentro de UMA palavra ("bucetinha", "arrombadinho")
_LEET = str.maketrans({'0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't', '@': 'a', '$': 's'})


def _sem_repeticao(s):
    return re.sub(r'(.)\1+', r'\1', s)


_PALAVROES_N = frozenset(_sem_repeticao(p) for p in _PALAVROES)
_PALAVROES_RAIZ_N = tuple(_sem_repeticao(r) for r in _PALAVROES_RAIZ)


def _filtro_ligado():
    return os.environ.get('PUBSHOW_FILTRO', '1').strip() != '0'


def _tem_palavrao(texto):
    """True se o texto tem palavrão da lista (sem acento, sem maiúscula, 'p0rr4' = 'porra', 'porrraaa' = 'porra')."""
    if not texto or not _filtro_ligado():
        return False
    import unicodedata
    t = unicodedata.normalize('NFKD', str(texto).lower())
    t = ''.join(c for c in t if not unicodedata.combining(c)).translate(_LEET)
    # letras soltas separadas por espaço/ponto viram 1 palavra: "p o r r a", "P.O.R.R.A" → "porra" (16/10/26, C2)
    t = re.sub(r'(?<![a-z])(?:[a-z][\s.\-_*]+){2,}[a-z](?![a-z])', lambda m: re.sub(r'[^a-z]', '', m.group()), t)
    t = _sem_repeticao(t)
    palavras = re.findall(r'[a-z]+', t)
    return any(p in _PALAVROES_N or any(r in p for r in _PALAVROES_RAIZ_N) for p in palavras)


# ── Trava eleitoral (18/10/26, pente fino CEL-3; Lote E aprovado pelo Diogo em 16/10) ───────────────
# 2º turno = domingo 25/10/2026, Flávio Bolsonaro (PL, 22) × Lula (PT, 13): conferido no TSE (notícia de 05/10).
# Governador só em AC, AM, DF, RN, ES e TO (TSE, 14/10); SC não tem. Lei 9.504: art. 37 §4º (bar é bem de uso
# comum: multa) e art. 39 §5º (no dia da eleição: crime).
# 1) Filtro de política, link e telefone: SEMPRE ligado (PUBSHOW_FILTRO=0 desliga junto com o de palavrão, nunca
#    na janela). Vale pro nome, o recado, o remetente, o texto junto como sai na TV, o título da música e a rotação.
#    Palavra ambígua só barra junto de um 2º sinal: "Lula Molusco", "Coração Partido", "Flávio da 22", "22 anos" e
#    "Vai dar PT" passam; "Lula 13", "Flávio presidente", "vota no 22" não.
# 2) Janela eleitoral (véspera + dia, horário de Brasília; o Railway roda em UTC): todo momento com texto espera o
#    OK do gerente (_precisa_ok) e o filtro fica estrito ("Lula" sozinho barra). Itens M1-M3 e A2 do verificador
#    adversarial de 18/10. Datas na env PUBSHOW_DIAS_ELEICAO (AAAA-MM-DD ou DD/MM/AAAA; vazia ou 0 desliga a janela).
# 3) Dia da votação (o último dia da janela, domingo 25): só toca o acervo curado (sem YouTube e sem vídeo que entrou
#    por pedido) e o nome da música não aparece na TV (C1-C3 do verificador). Diogo, 19/10: no sábado o YouTube e o
#    nome seguem como sempre.
_DIAS_ELEICAO_PADRAO = '2026-10-24,2026-10-25'
MSG_POLITICA = '🗳️ Política não aparece na TV (lei eleitoral). Troca o nome ou a mensagem. 🙏'
MSG_POLITICA_MUSICA = '🗳️ Política não toca aqui (lei eleitoral). Escolhe outra música. 🙏'
MSG_LINK_FONE = 'Sem link e sem telefone na TV, beleza? 🙏 Troca o nome ou a mensagem.'
MSG_SO_ACERVO_ELEICAO = '🗳️ Hoje é dia de eleição: só as músicas do acervo do bar. Escolhe na lista. 🎵'
_DIAS_ELEICAO_AVISADO = []


def _ler_datas(raw):
    dias = set()
    for d in re.split(r'[,;\s]+', raw.strip()):
        for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
            try:
                dias.add(datetime.strptime(d, fmt).date())
                break
            except ValueError:
                pass
    return dias


def _dias_eleicao():
    raw = os.environ.get('PUBSHOW_DIAS_ELEICAO', _DIAS_ELEICAO_PADRAO)
    if raw.strip().lower() in ('', '0', 'nao', 'não', 'desligado'):
        return set()   # janela desligada (a bateria de testes roda com a env vazia)
    dias = _ler_datas(raw)
    if not dias:   # M2: env preenchida errada não desliga a trava em silêncio
        if raw not in _DIAS_ELEICAO_AVISADO:
            _DIAS_ELEICAO_AVISADO.append(raw)
            log.error('[PUBSHOW] PUBSHOW_DIAS_ELEICAO=%r sem data válida: usando %s', raw, _DIAS_ELEICAO_PADRAO)
        dias = _ler_datas(_DIAS_ELEICAO_PADRAO)
    return dias


def _janela_eleitoral(agora_utc=None):
    """True na véspera e no dia da eleição, no horário de Brasília (UTC-3, sem horário de verão)."""
    return ((agora_utc or datetime.utcnow()) - timedelta(hours=3)).date() in _dias_eleicao()


def _dia_da_votacao(agora_utc=None):
    """True só no último dia de cada janela (o domingo da votação), no horário de Brasília."""
    d = ((agora_utc or datetime.utcnow()) - timedelta(hours=3)).date()
    dias = _dias_eleicao()
    return d in dias and d + timedelta(days=1) not in dias


def _inicio_janela_utc():
    """00:00 de Brasília do 1º dia da janela em curso, em UTC no formato do banco (created_at)."""
    dias = _dias_eleicao()
    d = (datetime.utcnow() - timedelta(hours=3)).date()
    while d - timedelta(days=1) in dias:
        d -= timedelta(days=1)
    return (datetime(d.year, d.month, d.day) + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')


# letra disfarçada que o NFKC não desfaz: russo, grego e as "miúdas" dos geradores de fonte
_LETRA_DISFARCADA = str.maketrans({
    'а': 'a', 'о': 'o', 'е': 'e', 'р': 'p', 'с': 'c', 'у': 'y', 'х': 'x', 'к': 'k', 'м': 'm', 'т': 't', 'і': 'i',
    'л': 'l', 'ѕ': 's', 'ο': 'o', 'α': 'a', 'ι': 'i', 'ρ': 'p', 'ν': 'v', 'ᴀ': 'a', 'ʙ': 'b', 'ᴄ': 'c', 'ᴅ': 'd',
    'ᴇ': 'e', 'ꜰ': 'f', 'ɢ': 'g', 'ʜ': 'h', 'ɪ': 'i', 'ᴊ': 'j', 'ᴋ': 'k', 'ʟ': 'l', 'ᴍ': 'm', 'ɴ': 'n', 'ᴏ': 'o',
    'ᴘ': 'p', 'ʀ': 'r', 'ꜱ': 's', 'ᴛ': 't', 'ᴜ': 'u', 'ᴠ': 'v', 'ᴡ': 'w', 'ʏ': 'y', 'ᴢ': 'z'})
def _pol_lista(*palavras):
    """As palavras do texto passam por _sem_repeticao ("nosso" → "noso"): a lista passa pela mesma regra."""
    return frozenset(_sem_repeticao(p) for p in palavras)


_POL_FORTE = _pol_lista('bolsonaro', 'bolsonarista', 'bolsonaristas', 'bolsominion', 'bolsominions', 'bozonaro',
                        'petista', 'petistas', 'petralha', 'petralhas', 'lulista', 'lulistas', 'lulopetista', 'fazol',
                        'fazoele', 'lulala', 'lulalivre', 'foralula', 'forabolsonaro', 'lulapresidente',
                        'flaviopresidente')
_POL_RAIZ = ('bolsonar', 'bolsonaz', 'bolsomin', 'petralh', 'lulopet', 'luladr')   # dentro de 1 palavra ou de 2 juntas
# nome → número da chapa: sozinho NÃO barra, e com o número do outro também não ("Flávio 13" é só um Flávio)
_POL_NOME = {_sem_repeticao(k): v for k, v in (('lula', '13'), ('alckmin', '13'), ('inacio', '13'), ('molusco', '13'),
                                               ('flavio', '22'), ('messias', '22'), ('capitao', '22'), ('bozo', '22'))}
_POL_NOME_ESTRITO = _pol_lista('lula', 'alckmin')   # na janela barra sozinho ("Flávio" é nome comum: nunca)
_POL_NUM = frozenset(('13', '22'))
_POL_CARGO = _pol_lista('presidente', 'presidencia', 'governador', 'senador', 'deputado', 'candidato', 'candidata',
                        'eleicao', 'eleicoes', 'eleito', 'eleita', 'reeleicao', 'reeleito', 'turno', 'urna', 'urnas',
                        'campanha', 'partido', 'jingle', 'mito', 'pt', 'pl')   # + nome ou número
_POL_CARGO_CLUBE = _pol_lista('presidente', 'presidencia', 'governador')   # "presidente do motoclube" não é política
_POL_VOTO = _pol_lista('vota', 'vote', 'votem', 'votar', 'voto', 'votei', 'votando', 'neles')   # + nome ou número
_POL_URNA = _pol_lista('aperta', 'aperte', 'apertem', 'digita', 'digite', 'confirma', 'confirme', 'confirmem',
                       'vai', 'fecha', 'fechado', 'fechamos')   # só + número ("Vai de 22", "Fecha com o 13")
_POL_LIGA = _pol_lista('o', 'a', 'no', 'na', 'em', 'pro', 'pra', 'de', 'do', 'da', 'e', 'eh', 'n', 'numero', 'num', 'nr',
                       'nosso', 'nossa', 'meu', 'minha', 'seu', 'sua', 'teu', 'tua')
# "13"/"22" que é mesa ("Flávio da 22"), idade, hora, preço ou data não é número de candidato
_POL_NUM_NEUTRO_ANTES = _pol_lista('mesa', 'mesas', 'casa', 'apto', 'ap', 'quarto', 'camisa', 'sala', 'box', 'dia',
                                   'as', 'ate', 'da', 'na')
_POL_NUM_NEUTRO_DEPOIS = _pol_lista('anos', 'ano', 'aninhos', 'primaveras', 'reais', 'real', 'pila', 'conto', 'contos',
                                    'cerveja', 'cervejas', 'chopp', 'chopps', 'chope', 'litros', 'min', 'minutos', 'h',
                                    'hs', 'horas', 'pessoas', 'km', 'kg', 'mesas', 'meses', 'dias')
_MESES = _pol_lista('janeiro', 'fevereiro', 'marco', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
                    'outubro', 'novembro', 'dezembro')
_POL_FRASES = re.compile(r'\bfaz o (?:l|ele)\b|\blula la\b|\blula livre\b|\bfora (?:lula|flavio|bolsonaro|alckmin|pt|pl)\b'
                         r'|\bpartido (?:liberal|dos trabalhadores)\b|\bnove dedos\b')
_POL_HASHTAG = re.compile(r'#\s*(?:lula|alckmin|fora\s*(?:lula|flavio|bolsonaro|pt|pl)|flavio\s*(?:presidente|22)'
                          r'|eles?\s*(?:nao|sim))')
_POL_PAR = ({'num', 'nome'}, {'num', 'cargo'}, {'num', 'voto'}, {'num', 'urna'}, {'nome', 'cargo'}, {'nome', 'voto'})


def _pol_normalizar(texto):
    import unicodedata
    t = unicodedata.normalize('NFKC', str(texto)).lower().translate(_LETRA_DISFARCADA)
    if any(ord(ch) >= 0x1F130 for ch in t):   # 🅱🅾🅻 e letra de bandeira viram letra
        t = ''.join(next((chr(97 + ord(ch) - i) for i in (0x1F130, 0x1F150, 0x1F170, 0x1F1E6) if i <= ord(ch) < i + 26), ch)
                    for ch in t)
    t = unicodedata.normalize('NFKD', t)
    # some acento, letra invisível (espaço de largura zero, hífen invisível) e o "⃣" do emoji de número: "2️⃣2️⃣" = 22
    t = ''.join(ch for ch in t if unicodedata.category(ch) not in ('Mn', 'Me', 'Cf'))
    t = re.sub(r'\bvinte e dois\b', '22', re.sub(r'\btreze\b', '13', t))
    t = re.sub(r'(?<![\d,])(\d)[\s.\-]+(\d)(?![\d,])', r'\1\2', t)   # "Vota 1 3", "Aperta 1-3" = 13
    # letras soltas viram 1 palavra: "B O L S O N A R O", "L.U.L.A" (igual ao filtro de palavrão)
    return re.sub(r'(?<![a-z0-9])(?:[a-z][\s.\-_*]+){2,}[a-z](?![a-z0-9])', lambda m: re.sub(r'[^a-z]', '', m.group()), t)


def _pol_palavras(t):
    """Palavras e números separados: "lula13" → lula 13; "b0ls0nar0" e "lul4" → bolsonaro, lula; "22h" → 22 h."""
    out = []
    for tok in re.findall(r'[a-z0-9@$]+', re.sub(r'(?<![a-z0-9])@', ' ', t)):   # "@lula13" = lula 13
        if not re.fullmatch(r'[a-z]+\d{2,}|\d+[a-z]+', tok) and re.search(r'[a-z@$]', tok):
            tok = tok.translate(_LEET)
        out.extend(p if p.isdigit() else _sem_repeticao(p) for p in re.findall(r'[a-z]+|\d+', tok))
    return out


def _tem_politica(texto, estrito=False):
    """True se o texto (nome, recado, título) é propaganda política. estrito = janela eleitoral."""
    if not texto or (not estrito and not _filtro_ligado()):
        return False
    t = _pol_normalizar(texto)
    ps = _pol_palavras(t)
    if any(p in _POL_FORTE or any(r in p for r in _POL_RAIZ) for p in ps):
        return True
    if any(r in a + b for a, b in zip(ps, ps[1:]) for r in _POL_RAIZ):   # "Bolso naro", "BOLSO-NARO"
        return True
    if estrito and any(p in _POL_NOME_ESTRITO for p in ps):
        return True
    junto = ' '.join(ps)
    if _POL_FRASES.search(junto) or _POL_HASHTAG.search(t):
        return True
    if 'jingle' in ps and not any(p.startswith('bel') or p == 'jangle' for p in ps):   # "Jingle Bells" passa
        return True
    papeis = []   # (papel, número) de cada palavra que conta, pulando as de ligação ("vota NO NOSSO 22" = vota 22)
    for i, p in enumerate(ps):
        if p in _POL_LIGA:
            continue
        prox = ps[i + 1] if i + 1 < len(ps) else ''
        if p in _POL_NUM:
            neutro = ((i > 0 and ps[i - 1] in _POL_NUM_NEUTRO_ANTES) or prox in _POL_NUM_NEUTRO_DEPOIS
                      or (prox == 'de' and i + 2 < len(ps) and ps[i + 2] in _MESES))   # "13 de outubro"
            papeis.append(None if neutro else ('num', p))
        elif p in _POL_NOME:
            papeis.append(('nome', _POL_NOME[p]))
        elif p in _POL_CARGO_CLUBE and prox in ('do', 'da') and ps[i + 2:i + 3] != ['brasil']:
            papeis.append(None)   # "Flávio, presidente do motoclube"
        else:
            r = ('cargo' if p in _POL_CARGO else 'voto' if p in _POL_VOTO else 'urna' if p in _POL_URNA else None)
            papeis.append((r, None) if r else None)

    def _par(a, b):
        if not a or not b or {a[0], b[0]} not in _POL_PAR:
            return False
        return a[1] == b[1] if {a[0], b[0]} == {'num', 'nome'} else True

    if any(_par(a, b) for a, b in zip(papeis, papeis[1:])):
        return True
    if ' dia 25 ' in f' {junto} ' and any(a and a[0] == 'num' for a in papeis):   # "Dia 25 é 13"
        return True
    # verbo de voto chega no número com 1 palavra no meio: "vote consciente 22", "bora votar galera 13"
    return any(a and c and a[0] in ('voto', 'urna') and c[0] == 'num' for a, c in zip(papeis, papeis[2:]))


def _texto_politico(*campos):
    """Cada campo e o texto junto, como a TV mostra (A2: "Ana" + "Aperta o" + "de 13" separados passavam)."""
    est = _janela_eleitoral()
    return any(_tem_politica(x, est) for x in (*campos, ' '.join(c for c in campos if c)))


_FONE = re.compile(r'(?<!\d)(?:\+?\s?55[\s.\-]?)?\(?\d{2}\)?[\s.\-/_]?9?\d{4}[\s.\-/_]?\d{4}(?!\d)'   # com DDD
                   r'|(?<!\d)9\d{4}[\s.\-/_]?\d{4}(?!\d)')                                       # celular sem DDD


def _tem_link_ou_fone(texto):
    """Link (http, www, algo.com) ou telefone (celular de 9 dígitos, ou com DDD) no que vai pra TV.
    "1994 - 2024 - 30 anos" e "mesas 10 11 12 13" não são telefone (M3 do verificador)."""
    if not texto or not _filtro_ligado():
        return False
    t = _pol_normalizar(texto)
    return bool(re.search(r'https?:|www\.|\b[a-z0-9-]{2,}\.(?:com|net|org|io|me|ly|gl|app|xyz|site|online|info|tv|link'
                          r'|bet|vip)\b|\b[a-z0-9-]{2,}\.br\b', t) or _FONE.search(t))


def _categoria_do_bar(b):
    """Categoria do pedido = canal que está no ar no bar, lida no SERVIDOR (16/10/26, D3V-03).
    Antes vinha do formulário; canal-lista (ex.: Sport Mix) vira a 1ª categoria dele."""
    if (b['canal_atual'] or '') == 'variado':
        return 'variado'   # VDC-05 (16/10/26): categoria neutra (antes virava 'rock' e sujava o Rock dos outros bares)
    c = CANAIS.get(b['canal_atual'] or '') or CANAIS['rock']
    cat = c.get('cat')
    if isinstance(cat, (list, tuple)):
        cat = cat[0] if cat else 'rock'
    return cat if cat in CANAIS else 'rock'


def _status_apos_pix(conn, pedido_id):
    """Pedido pago: vai direto pra fila ('pendente') ou pra fila de OK do gerente ('aguardando_ok')."""
    row = conn.execute('SELECT tipo, business_id FROM pubshow_pedidos WHERE id=?', (pedido_id,)).fetchone()
    if not row:
        return 'pendente'
    b = conn.execute('SELECT moderar_ok FROM pubshow_businesses WHERE id=?', (row['business_id'],)).fetchone()
    return 'aguardando_ok' if (b and _precisa_ok(b, row['tipo'])) else 'pendente'


def _tipos_config(b=None):
    """Tipos que o gerente vê nas configurações (sem os ocultos do cliente). ⏩ Pular a espera (20/10/26): só no
    painel do bar que cobra pela Efí (sem o bar, fica de fora)."""
    return {k: v for k, v in TIPOS_PEDIDO.items()
            if k not in TIPOS_OCULTOS and (k != 'pular_espera' or (b is not None and _pular_espera_para(b)))}


def _happy_hour_desconto(b):
    """Retorna o percentual de desconto ativo no momento (0 se não há happy hour).
    17/10/26 (decisão 2 do Diogo): SEM happy hour — preço fixo. Sempre 0; o happy_hour_json que já está no banco
    fica guardado (rollback não perde nada), mas não desconta mais nada e o card saiu do painel."""
    return 0
    import json as _json
    try:
        hh = _json.loads(b['happy_hour_json'] or 'null')
    except Exception:
        return 0
    if not hh or not isinstance(hh, dict) or hh.get('ativo') is False:
        return 0   # VDC-03 (16/10/26): desligado guarda a configuração com 'ativo': False (pra poder religar)
    agora = datetime.now()
    hora_str = agora.strftime('%H:%M')
    wd = agora.weekday()  # 0=segunda … 6=domingo
    # Converte domingo: Python usa 6, mas UI usa 0 pra domingo — normaliza para 0-6 (0=Dom)
    wd_ui = (wd + 1) % 7  # 0=Dom, 1=Seg … 6=Sáb
    dias = hh.get('dias', [0, 1, 2, 3, 4, 5, 6])
    if wd_ui not in dias:
        return 0
    ini = hh.get('ini', '00:00')
    fim = hh.get('fim', '23:59')
    if ini <= hora_str <= fim:
        return int(hh.get('desconto', 0))
    return 0


def _precos_do_bar(b):
    """Retorna preços customizados + Happy Hour aplicado."""
    import json
    try:
        custom = json.loads(b['precos_custom'] or '{}')
    except Exception:
        custom = {}
    desconto_pct = _happy_hour_desconto(b)
    _ponte = _ponte_efi_para(b)   # ponte Efí: furar a fila R$3 e ⏩ R$1 (+ R$1 do app); no split o item da tabela
    _piso = _piso_do_bar(b)
    teto = _preco_teto(b)   # Lote D A4 (16/10/26): R$ 50 no modo Efí, também pros preços JÁ salvos; None no Asaas
    tipos = {}
    for k, v in TIPOS_PEDIDO.items():
        t = dict(v)
        if k == 'pular_espera':
            # preço fixo (20/10/26): R$ 2 no total, sem preço do bar, teto ou happy hour ("só mantém os preços")
            if _ponte:
                t['preco'] = PRECO_PULAR_EFI
            tipos[k] = t
            continue
        if _ponte and k == 'flash':
            t['preco'] = PRECO_FLASH_EFI
        if k in custom:
            # piso R$5 (decisão 12/09/26: "grátis ou a partir de 5,00" — abaixo disso a taxa do PIX come o pedido)
            t['preco'] = max(float(custom[k]), _piso)
        if teto is not None and t['preco'] > teto:
            t['preco'] = teto
        if k in TIPOS_SEMPRE_GRATIS:
            t['preco'] = 0.0          # decisão 11/09/26: pedir música é grátis em TODO bar, custom não sobrepõe
            tipos[k] = t
            continue
        if desconto_pct > 0 and k not in TIPOS_EQUIPE:   # gorjeta: o cliente escolhe o valor, sem desconto
            preco_original = t['preco']
            # piso R$5 vale também no happy hour (15/10/26): o desconto nunca deixa abaixo do piso
            preco_hh = max(PIX_PISO_PRECO, round(preco_original * (1 - desconto_pct / 100), 2))
            if preco_hh < preco_original:
                t['preco'] = preco_hh
                t['preco_original'] = preco_original
                t['happy_hour'] = True
        tipos[k] = t
    return tipos, desconto_pct


def _hh_maior_desconto(precos, visiveis):
    """Maior desconto REAL (%, arredondado pra baixo) entre os cards que o cliente vê (15/10/26).
    Com o piso de R$ 5, o % configurado no painel não vale pra todo item: o banner diz só o que é verdade (CDC)."""
    maior = 0
    for k, t in precos.items():
        if k in visiveis and t.get('happy_hour') and t.get('preco_original'):
            maior = max(maior, int(round((1 - t['preco'] / t['preco_original']) * 100, 2)))
    return maior


def _limite_atingido(b, ip):
    """Verifica se o IP já atingiu o limite de pedidos na última hora."""
    limite = b['limite_pedidos_hora'] or 10
    if limite <= 0:
        return False
    conn = get_pubshow_db()
    try:
        count = conn.execute(
            '''SELECT COUNT(*) FROM pubshow_pedidos
               WHERE business_id=? AND ip_cliente=?
               AND created_at >= datetime("now", "-1 hours")''',
            (b['id'], ip)
        ).fetchone()[0]
        return count >= limite
    except Exception:
        return False  # em caso de erro, não bloqueia o pedido
    finally:
        conn.close()  # A4 fix: sempre fecha a conexão

# Música GRÁTIS: 1 a cada N minutos por CELULAR (16/10/26, Diogo). Pago (furar a fila, momentos) = sem esse limite.
# Só vale quando o celular mandou o client_id (ip_cliente com '#'): sem ele, no Wi-Fi do bar todo mundo tem o
# mesmo IP e a trava pegaria a mesa vizinha. 0 desliga (env PUBSHOW_GRATIS_INTERVALO_MIN).
# 20/10/26 (pedido do Garage): 20 → 8 min.
try:
    GRATIS_INTERVALO_MIN = max(0, int(os.environ.get('PUBSHOW_GRATIS_INTERVALO_MIN', '8') or 0))
except (TypeError, ValueError):
    GRATIS_INTERVALO_MIN = 8


def _gratis_espera_min(b, ip):
    """Minutos que este celular ainda espera pra pedir outra música grátis (0 = pode pedir agora)."""
    if GRATIS_INTERVALO_MIN <= 0 or '#' not in (ip or ''):
        return 0
    conn = get_pubshow_db()
    try:
        marcas = ','.join('?' * len(TIPOS_SEMPRE_GRATIS))
        row = conn.execute(
            f"""SELECT (julianday(created_at) - julianday('now')) * 1440 + ? AS falta
                FROM pubshow_pedidos
                WHERE business_id=? AND ip_cliente=? AND tipo IN ({marcas})
                  AND status <> 'recusado'
                  AND created_at >= datetime('now', ?)
                ORDER BY created_at DESC LIMIT 1""",
            (GRATIS_INTERVALO_MIN, b['id'], ip, *TIPOS_SEMPRE_GRATIS, f'-{GRATIS_INTERVALO_MIN} minutes')
        ).fetchone()
        if not row or row[0] is None or row[0] <= 0:
            return 0
        return max(1, int(-(-row[0] // 1)))   # arredonda pra cima
    except Exception:
        return 0   # em erro, não bloqueia
    finally:
        conn.close()


TIPOS_MUSICA_FILA = ('musica_especifica', 'musica_externa', 'flash', 'vip', 'pular_espera')   # + ⏩ (20/10/26)


def _musica_na_fila(b, youtube_id, ip_cliente=None, tipo=None, nome=None):
    """True se esta música (youtube_id) já está na fila do bar — pendente, ou com PIX sendo pago agora
    (16/10/26, Diogo: cliente pedindo a mesma música várias vezes).
    Junção 17/10/26: o PIX aberto do PRÓPRIO celular (ip_cliente com o client_id) não conta — o mesmo pedido
    reenviado (voltou do banco, tocou 2x) reabre o MESMO PIX (crítico C2) em vez de dizer "já está na fila"."""
    yid = (youtube_id or '').strip()
    if not yid:
        return False
    conn = get_pubshow_db()
    try:
        marcas = ','.join('?' * len(TIPOS_MUSICA_FILA))
        # verificador (17/10/26, F3): só o MESMO pedido (mesmo celular, tipo e nome) fica de fora; outro tipo ou outro
        # nome do mesmo celular com a mesma música é recusado, como na produção (1dd1c74)
        _proprio = ("AND NOT (ip_cliente=? AND tipo=? AND COALESCE(nome_cliente,'')=?)"
                    if (ip_cliente and '#' in ip_cliente and tipo) else '')
        row = conn.execute(
            f"""SELECT 1 FROM pubshow_pedidos
                WHERE business_id=? AND youtube_id=? AND tipo IN ({marcas})
                  AND (status='pendente'
                       OR (status='aguardando_pix' AND created_at >= datetime('now', '-15 minutes') {_proprio}))
                LIMIT 1""",
            (b['id'], yid, *TIPOS_MUSICA_FILA, *((ip_cliente, tipo, nome or '') if _proprio else ()))
        ).fetchone()
        return bool(row)
    except Exception:
        return False   # em erro, não bloqueia
    finally:
        conn.close()



def _teto_pedidos(b, tipo, usar_pix, cid):
    """Tetos anti-flood (15/10/26). '' = pode pedir; senão a mensagem pro cliente.
    PIX: no máx. 50 aguardando_pix abertos no bar (15 min) e 2 por celular (client_id, 30 min).
    Entrada direta na fila (grátis/balcão): fila no máx. 25 e 1 música grátis pendente por celular,
    contando só as últimas 12 h (pendente de outra noite não trava hoje, 16/10/26).
    Sem trava por IP; sem client_id, só valem os tetos do bar."""
    conn = get_pubshow_db()
    try:
        _por_cid = "instr(ip_cliente,'#')>0 AND substr(ip_cliente, instr(ip_cliente,'#')+1)=?"
        if usar_pix:
            abertos = conn.execute(
                "SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status='aguardando_pix' "
                "AND created_at >= datetime('now','-15 minutes')", (b['id'],)).fetchone()[0]
            if abertos >= MAX_PIX_ABERTOS_BAR:
                return 'Muitos pagamentos abertos agora. Tenta de novo em alguns minutos.'
            if cid and conn.execute(
                    "SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status='aguardando_pix' "
                    f"AND created_at >= datetime('now','-30 minutes') AND {_por_cid}",
                    (b['id'], cid)).fetchone()[0] >= MAX_PIX_ABERTOS_CLIENTE:
                return 'Você já tem PIX abertos. Paga ou espera uns minutos pra pedir outro.'
            return ''
        fila = conn.execute("SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status='pendente' "
                            f"AND created_at >= {_JANELA_FILA}", (b['id'],)).fetchone()[0]
        if fila >= MAX_FILA:
            # Com o Asaas o Furar a fila é PIX e não passa por este teto: aí a dica é verdade (16/10/26, V4-05)
            if _gateway_ativo(b) and 'flash' in _tipos_disponiveis(b):
                return 'A fila está cheia agora. Pede de novo daqui a pouco, ou fura a fila no PIX. ⚡'
            return 'A fila está cheia agora. Espera algumas músicas tocarem e pede de novo. 🎶'
        if cid and tipo in TIPOS_SEMPRE_GRATIS:
            _ph = ','.join('?' * len(TIPOS_SEMPRE_GRATIS))
            if conn.execute(
                    f"SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status='pendente' "
                    f"AND created_at >= {_JANELA_FILA} AND tipo IN ({_ph}) AND {_por_cid}",
                    (b['id'], *TIPOS_SEMPRE_GRATIS, cid)).fetchone()[0] >= 1:
                return 'Tua música já está na fila. Espera ela tocar pra pedir outra. 🎶'
        return ''
    finally:
        conn.close()


def _admin_ok():
    """Verifica se a sessão atual é admin — aceita login PUBSHOW ou SaaS master."""
    return session.get('pubshow_admin') is True or session.get('saas_admin') is True

def _admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _admin_ok():
            return redirect('/pubshow/admin/login')
        return f(*args, **kwargs)
    return decorated


def _get_business():
    bid = session.get('pub_business_id')
    if not bid:
        return None
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE id=?', (bid,)).fetchone()
    conn.close()
    return b


def pubshow_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('pub_business_id'):
            return redirect('/pubshow/entrar')
        return f(*args, **kwargs)
    return decorated


def _asaas_headers():
    return {'access_token': os.environ.get('ASAAS_API_KEY', ''), 'Content-Type': 'application/json'}


def _asaas_req(method, endpoint, data=None):
    try:
        r = _requests.request(method, f'{_ASAAS_BASE}{endpoint}',
                              headers=_asaas_headers(), json=data, timeout=4)
        return r.json()
    except Exception as e:
        log.debug('[PUBSHOW] Asaas timeout/erro: %s %s — %s', method, endpoint, e)
        return {'error': str(e)}


def _asaas_descricao(texto: str) -> str:
    """Descrição que o Asaas aceita. Emoji de 4 bytes (🙏 🎂 🍻 🎉 😏 📣 💕, e os do título do
    YouTube) faz o Asaas recusar a cobrança inteira ("O campo 'description' não pode conter
    caracteres especiais") e o cliente via "Pagamento indisponível" — gorjeta no Garage, 18/10/26.
    Acento, ·, —, ⚡ e ❤️ passam (conferido na API), então só sai o que está fora do BMP."""
    texto = ''.join(c for c in (texto or '') if ord(c) <= 0xFFFF and c != '\u200d')
    return re.sub(r'\s+', ' ', texto).strip()[:500]


def _asaas_criar_ou_buscar_cliente(b, forcar_novo=False) -> str:
    if not forcar_novo and b['asaas_customer_id']:
        return b['asaas_customer_id']
    cpf = _doc_norm(b['cpf_cnpj'])   # 16/10/26 (V3-08): mantém as letras do CNPJ alfanumérico
    cid = None
    # Na recuperação (forcar_novo) pula a busca e cria um cliente NOVO direto —
    # a busca pode devolver justamente o cliente deletado que causou o erro.
    if not forcar_novo:
        busca = _asaas_req('GET', f'/customers?cpfCnpj={cpf}')
        # Ignora clientes REMOVIDOS — senão recria cobrança num cliente deletado (erro 400)
        validos = [c for c in (busca.get('data') or []) if not c.get('deleted')]
        if validos:
            cid = validos[0]['id']
    if not cid:
        resp = _asaas_req('POST', '/customers', {
            'name': b['nome'], 'email': b['email'],
            'mobilePhone': re.sub(r'\D', '', b['telefone'] or ''),
            'cpfCnpj': cpf,
            # 15/10/26: sem isso o Asaas cobra R$0,99 de notificação por PIX pago e liga (robô) pro
            # dono do bar a cada QR abandonado — os outros módulos do 4kitem já mandam
            'notificationDisabled': True,
        })
        cid = resp.get('id')
        if not cid:
            log.error('[PUBSHOW] Asaas criar customer falhou: %s', resp)
    if cid:
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET asaas_customer_id=? WHERE id=?', (cid, b['id']))
        conn.commit(); conn.close()
    return cid


def _primeiro_vencimento(b):
    """1ª cobrança da assinatura (16/10/26, críticos C3/C4): nunca antes de amanhã, nem antes do fim do
    teste grátis (o checkout promete 'a cobrança só começa depois do teste'), nem antes do fim do mês já
    pago de quem cancelou e reassinou (ativo_ate) — senão o bar pagava 2x os mesmos dias."""
    import datetime as _dt
    venc = (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d')
    if b:
        keys = b.keys() if hasattr(b, 'keys') else []
        for campo in ('trial_ends', 'ativo_ate'):
            v = ((b[campo] if campo in keys else None) or '')[:10]
            if len(v) == 10 and re.match(r'^\d{4}-\d{2}-\d{2}$', v) and v > venc:
                venc = v
    return venc


def _asaas_criar_assinatura(customer_id, plano, billing_type, business_id, primeiro_venc=None, p=None):
    # Lote D A1 (16/10/26): p = o plano com o preço DESTE bar (_plano_do_bar: fundador = o travado)
    p = p or PLANOS[plano]
    import datetime as _dt
    venc = primeiro_venc or (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d')
    return _asaas_req('POST', '/subscriptions', {
        'customer': customer_id,
        'billingType': billing_type,
        'value': p['preco'],
        'nextDueDate': venc,
        'cycle': 'MONTHLY',
        'description': f'PUBSHOW — {p["nome"]}',
        'externalReference': f'pubshow_{business_id}_{plano}',
    })


def _asaas_criar_cobranca_pix_jukebox(b, pedido_id: int, valor: float, descricao: str):
    """Cria cobrança PIX avulsa no Asaas para pedido de Jukebox.

    Retorna dict com {payment_id, qr_b64, payload, expiracao}
    ou None em caso de erro (usar fallback manual).
    """
    if not os.environ.get('ASAAS_API_KEY'):
        return None   # Asaas não configurado — usar PIX manual
    if round(float(valor or 0), 2) < 5.00:
        # crítico dinheiro B8 (17/10/26): abaixo de R$ 5 o Asaas recusa (16/10: o PIX caiu na chave do bar). Envs
        # PUBSHOW_PISO_PRECO/PUBSHOW_TAXA_CONVENIENCIA tortas não chegam ao Asaas: "Pagamento indisponível"
        log.error('[PUBSHOW] Asaas: cobrança de R$ %.2f abaixo do mínimo de R$ 5 (pedido #%s) — não criada',
                  float(valor or 0), pedido_id)
        return None

    # Garante que o bar tem cliente no Asaas
    customer_id = _asaas_criar_ou_buscar_cliente(b)
    if not customer_id:
        log.warning('[PUBSHOW] Asaas: sem customer para bar %s', b['id'])
        return None

    import datetime as _dt
    venc = (_dt.date.today() + _dt.timedelta(days=1)).strftime('%Y-%m-%d')

    def _criar_cobranca(cust):
        return _asaas_req('POST', '/payments', {
            'customer':          cust,
            'billingType':       'PIX',
            'value':             round(valor, 2),
            'dueDate':           venc,
            'description':       _asaas_descricao(f'Jukebox — {descricao} — {b["nome"]}'),
            'externalReference': f'jukebox_{pedido_id}',
            'postalService':     False,
        })

    # 1) Cria cobrança PIX avulsa
    resp = _criar_cobranca(customer_id)
    payment_id = resp.get('id')

    # Recuperação automática: se o cliente foi REMOVIDO no Asaas, limpa o cache,
    # recria o cliente e tenta a cobrança de novo (1x). Era isso que jogava no
    # PIX manual sem auto-confirmar.
    if not payment_id and any(e.get('code') == 'invalid_customer' for e in (resp.get('errors') or [])):
        log.warning('[PUBSHOW] Asaas customer removido (bar %s) — recriando e tentando de novo', b['id'])
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET asaas_customer_id=NULL WHERE id=?', (b['id'],))
        conn.commit(); conn.close()
        _b2 = dict(b); _b2['asaas_customer_id'] = None
        novo_cust = _asaas_criar_ou_buscar_cliente(_b2, forcar_novo=True)
        if novo_cust:
            resp = _criar_cobranca(novo_cust)
            payment_id = resp.get('id')

    if not payment_id:
        log.error('[PUBSHOW] Asaas criar cobrança falhou: %s', resp)
        return None

    # 2) Busca QR code PIX
    qr_resp = _asaas_req('GET', f'/payments/{payment_id}/pixQrCode')
    encoded  = qr_resp.get('encodedImage', '')
    payload  = qr_resp.get('payload', '')
    expira   = qr_resp.get('expirationDate', '')

    if not payload:
        log.error('[PUBSHOW] Asaas QR vazio para payment %s', payment_id)
        return None

    return {
        'payment_id': payment_id,
        'qr_b64':     encoded,
        'payload':    payload,
        'expiracao':  expira,
    }


# ── Efí direto (PONTE): cobrança + confirmação (16/10/26) ────────────────────
# O webhook da Efí não valida no Railway (ECONNRESET), então a baixa é por CONSULTA:
# a tela do cliente (polling) e um robô a cada 20 s perguntam à Efí se o PIX caiu.
# 17/10/26 (caminho único): pela MESMA conta do split (efi_client, _efi_cli), com txid NOSSO (PUT /v2/cob/:txid):
# re-tentar com o mesmo txid nunca cria 2 cobranças. O efi_pix.py (RifaJá) não é mais usado aqui.

def _ponte_txid(pedido_id):
    """txid da ponte: 'pp<pedido>x' + aleatório (26-35 letras/dígitos, padrão BCB). O split usa 'ps…': nunca colidem."""
    return (f'pp{int(pedido_id)}x' + secrets.token_hex(16))[:32]


def _efi_criar_cobranca_jukebox(pedido_id, valor, descricao):
    """Cobrança PIX imediata na Efí (15 min). Retorna {txid, copia_cola} ou None (erro no log)."""
    cli = _efi_cli()
    if cli is None:
        log.error('[PUBSHOW] Efí: cliente da conta Efí indisponível (pedido #%s)', pedido_id)
        return None
    txid = _ponte_txid(pedido_id)
    try:
        # pente fino DIN-2 (18/10/26): mesma limpeza do Asaas (sem emoji de 4 bytes)
        cob = cli.criar_cobranca_txid(txid, int(round(float(valor) * 100)), _asaas_descricao(descricao), expira=900)
    except Exception as e:
        cob = {'erro': str(e)[:200]}
    if not isinstance(cob, dict) or cob.get('erro') or not cob.get('txid') or not cob.get('copia_cola'):
        log.error('[PUBSHOW] Efí: cobrança falhou (pedido #%s, R$ %.2f): %s', pedido_id, float(valor),
                  (cob.get('erro') if isinstance(cob, dict) else cob) or cob)
        try:
            cli.cancelar_cobranca(txid)   # se o timeout escondeu uma cobrança criada, ela não fica viva
        except Exception:
            pass
        return None
    return {'txid': cob['txid'], 'copia_cola': cob['copia_cola']}


_EFI_FREIO_SEG = 5          # no máx. 1 consulta à Efí a cada 5 s por pedido (polling + robô somados)
_efi_ultima_consulta = {}   # pedido_id → time.monotonic() da última consulta
_efi_em_curso = set()       # pedidos com consulta em andamento (não dispara outra em paralelo)
_efi_lock = _threading.Lock()


def _efi_reservar_consulta(pedido_id):
    """Freio: True (e marca) se pode consultar a Efí agora pra este pedido."""
    agora = _time.monotonic()
    with _efi_lock:
        if pedido_id in _efi_em_curso:
            return False
        if agora - _efi_ultima_consulta.get(pedido_id, float('-inf')) < _EFI_FREIO_SEG:
            return False
        _efi_ultima_consulta[pedido_id] = agora
        _efi_em_curso.add(pedido_id)
        if len(_efi_ultima_consulta) > 5000:   # não deixa o dict crescer pra sempre
            for k in [k for k, v in _efi_ultima_consulta.items() if agora - v > 3 * 3600]:
                _efi_ultima_consulta.pop(k, None)
        return True


def _efi_consultar_e_confirmar(pedido_id, txid):
    """Pergunta à Efí; se CONCLUIDA, faz a MESMA transição do webhook do Asaas
    (aguardando_pix → pendente/aguardando_ok). Idempotente. True se ESTA chamada confirmou.
    Chamar só depois de _efi_reservar_consulta()."""
    try:
        try:
            cli = _efi_cli()
            r = cli.consultar(txid) if cli is not None else {'erro': 'cliente Efí indisponível'}
        except Exception as e:
            r = {'erro': str(e)[:200]}
        if not isinstance(r, dict) or not r.get('pago'):
            if isinstance(r, dict) and r.get('erro'):
                log.warning('[PUBSHOW] Efí: consulta do pedido #%s (%s) falhou: %s', pedido_id, txid, r['erro'])
            return False
        conn = get_pubshow_db()
        try:
            _novo = _status_apos_pix(conn, pedido_id)
            n = conn.execute(
                # junção 17/10/26: grava pago_em (as somas da versão grande só contam PIX com pago_em)
                "UPDATE pubshow_pedidos SET status=?, pago_em=COALESCE(pago_em, datetime('now')) "
                "WHERE id=? AND status='aguardando_pix' AND efi_txid=?",
                (_novo, pedido_id, txid)
            ).rowcount
            conn.commit()
            if not n:
                # crítico dinheiro A2 (17/10/26): pago num pedido que já não espera PIX (recusado pelo bar) → grava
                # pago_em sem mexer no status: cai em "↩️ PIX a devolver" do admin (devolver pela Efí da RifaJá)
                if conn.execute("UPDATE pubshow_pedidos SET pago_em=datetime('now') WHERE id=? AND efi_txid=? "
                                "AND status='recusado' AND pago_em IS NULL AND estornado_em IS NULL",
                                (pedido_id, txid)).rowcount:
                    conn.commit()
                    log.warning('[PUBSHOW] Efí (ponte): PIX do pedido #%s (%s) caiu depois de recusado — '
                                'DEVOLVER pela conta Efí (ver "PIX a devolver" no admin)', pedido_id, txid)
        finally:
            conn.close()
        if n:
            log.info('[PUBSHOW] Jukebox PIX confirmado — pedido #%s (Efí %s) → %s', pedido_id, txid, _novo)
        return bool(n)
    finally:
        with _efi_lock:
            _efi_em_curso.discard(pedido_id)


def _efi_conferir_pedido(pedido_id, txid, espera_seg=None):
    """Consulta (com freio de 5 s) e confirma se pago. espera_seg=None: roda aqui mesmo (robô).
    espera_seg=N: roda numa thread e espera no máx. N s — com 1 worker gunicorn a tela do
    cliente não pode ficar presa se a Efí demorar (o resultado cai no próximo polling)."""
    if not txid or _efi_cli() is None or not _efi_reservar_consulta(pedido_id):
        return False
    if espera_seg is None:
        return _efi_consultar_e_confirmar(pedido_id, txid)
    _res = {}
    def _run():
        _res['ok'] = _efi_consultar_e_confirmar(pedido_id, txid)
    t = _threading.Thread(target=_run, daemon=True, name=f'pubshow-efi-{pedido_id}')
    t.start()
    t.join(espera_seg)
    return bool(_res.get('ok'))


_CONTAS_PENDENTES_SEG = 600     # a cada 10 min (o robô da ponte roda a cada 20 s)
_contas_pendentes_ultima = [float('-inf')]


def _efi_conferir_contas_pendentes(forcar=False):
    """Caminho único (17/10/26): bar da lista que salvou a conta Efí quando a Efí não respondeu fica na PONTE (a conta
    não foi aceita). Aqui a conta é conferida de novo (pubshow_pay.conferir_conta, a mesma config de split do passo 4):
    aceitou → o bar vai pro split no próximo pedido, sem o dono fazer nada. No máximo 1 rodada a cada 10 min.
    Devolve quantos bares a Efí aceitou nesta rodada."""
    agora = _time.monotonic()
    if not forcar and agora - _contas_pendentes_ultima[0] < _CONTAS_PENDENTES_SEG:
        return 0
    _contas_pendentes_ultima[0] = agora
    if not _efi_ligado():
        return 0
    conn = _pp_conn()
    try:
        bares = [dict(r) for r in conn.execute(
            'SELECT b.* FROM pubshow_businesses b JOIN pubshow_pay_contas c ON c.business_id=b.id AND c.ativo=1 '
            'WHERE COALESCE(b.suspenso,0)=0')]
        aceitas = 0
        for b in bares:
            if not _efi_lista(b) or pubshow_pay.conta_aceita(conn, b['id']):
                continue
            if pubshow_pay.conferir_conta(conn, b['id']).get('aceita'):
                aceitas += 1
        return aceitas
    finally:
        conn.close()


def _efi_robo_passada():
    """Uma volta do robô: confere os pedidos Efí aguardando_pix dos últimos 30 min (cobrança expira em 15). Retorna quantos confirmou.
    Roda até no botão de emergência (PUBSHOW_GATEWAY=asaas): ele só para as cobranças NOVAS; o PIX que já saiu confirma.
    17/10/26 (caminho único): também confere de novo, a cada 10 min, a conta Efí salva que a Efí ainda não aceitou."""
    if not _efi_cli_ok():
        return 0
    try:
        _efi_conferir_contas_pendentes()
    except Exception as ex:
        log.warning('[PUBSHOW] robô Efí: conferir as contas pendentes falhou: %s', ex)
    conn = get_pubshow_db()
    try:
        rows = conn.execute(
            """SELECT id, efi_txid FROM pubshow_pedidos
               WHERE (status='aguardando_pix' OR (status='recusado' AND pago_em IS NULL))   -- crítico dinheiro A2
               AND efi_txid IS NOT NULL AND efi_txid<>''
               AND created_at >= datetime('now','-30 minutes')
               ORDER BY id"""
        ).fetchall()
    finally:
        conn.close()
    confirmados = 0
    for r in rows:
        try:
            if _efi_conferir_pedido(r['id'], r['efi_txid']):
                confirmados += 1
        except Exception as ex:
            log.warning('[PUBSHOW] robô Efí: pedido #%s: %s', r['id'], ex)
    return confirmados


_ROBO_EFI_INICIADO = False

def iniciar_robo_efi(intervalo_seg=20, delay_inicial_seg=30):
    """Thread daemon que confere na Efí, a cada 20 s, os PIX dos pedidos aguardando_pix
    (pagou e fechou a tela? confirma igual). Mesmo padrão do faxineiro: idempotente,
    uma vez por processo. Só sobe se PUBSHOW_EFI_BARES não estiver vazio."""
    global _ROBO_EFI_INICIADO
    if _ROBO_EFI_INICIADO:
        return
    if not _efi_bares():
        log.info('[PUBSHOW] Robô Efí desligado (PUBSHOW_EFI_BARES vazio)')
        return
    _ROBO_EFI_INICIADO = True

    def _loop():
        _time.sleep(delay_inicial_seg)
        while True:
            try:
                _efi_robo_passada()
            except Exception as ex:
                log.error('[PUBSHOW] robô Efí erro: %s', ex)
            _time.sleep(intervalo_seg)

    _threading.Thread(target=_loop, daemon=True, name='pubshow-robo-efi').start()
    log.info('[PUBSHOW] Robô Efí iniciado (a cada %ds)', intervalo_seg)


def _pubshow_notify_bar(b, mensagem: str):
    """Envia notificação WhatsApp para o dono do bar via Evolution API.
    Silencioso — nunca levanta exceção.
    """
    if b.get('whatsapp_notif') != 1:   # VDC-03 (16/10/26): 2 = desligado pelo dono (a chave segue no painel)
        return
    telefone = (b.get('telefone') or '').strip()
    if not telefone:
        return
    evo_url = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key = os.environ.get('EVOLUTION_API_KEY', '')
    instance = os.environ.get('PUBSHOW_WA_INSTANCE', '')
    if not evo_url or not evo_key or not instance:
        return
    digits = re.sub(r'\D', '', telefone)
    if not digits.startswith('55'):
        digits = '55' + digits
    try:
        _requests.post(
            f'{evo_url}/message/sendText/{instance}',
            json={'number': digits + '@s.whatsapp.net', 'text': mensagem},
            headers={'apikey': evo_key},
            timeout=8
        )
    except Exception as ex:
        log.warning('[PUBSHOW] WhatsApp notify error: %s', ex)


def _yid_ok(yid):
    """id do YouTube tem 11 letras [A-Za-z0-9_-] (16/10/26, V4-01): o mesmo teste do POST do jukebox.
    Id fora disso não toca na TV e o pedido seria recusado, então nem aparece nas listas."""
    return bool(yid) and re.fullmatch(r'[A-Za-z0-9_-]{11}', yid) is not None


def _temas_do_bar(b):
    """Os temas acesos do bar (temas_habilitados) ou None (= tudo)."""
    import json as _json
    try:
        v = _json.loads(b['temas_habilitados'] or 'null')
    except Exception:
        return None
    return [t for t in v if isinstance(t, str)] or None if isinstance(v, list) else None


def _selo_estilos(b):
    """Selo do topo do celular do cliente, com o nome do BOTÃO do painel: vários acesos (Variado só com eles) =
    '🎸 Rock · 🌴 Reggae'; 1 estilo = '🌴 Reggae'. '' = usa o nome do canal (Variado de verdade ou "Mais estilos")."""
    try:
        canal = b['canal_atual'] or ''
        if canal != 'variado':
            # o botão pelo canal também: ⚽ Esporte toca o canal 'sport_mix' (senão o selo dizia "Sport Mix")
            e = ESTILOS_BAR.get(canal) or next((x for x in ESTILOS_BAR.values() if x.get('canal') == canal), None)
            return f"{e['emoji']} {e['nome']}" if e else ''
        temas = _temas_do_bar(b)
    except Exception:
        return ''
    if not temas:
        return ''
    acesos = [e for e in ESTILOS_BAR.values() if e['temas'] and all(t in temas for t in e['temas'])]
    return ' · '.join(f"{e['emoji']} {e['nome']}" for e in acesos)


def _videos_do_canal(canal_key, limit=500, bloqueados=None, temas=None):
    """bloqueados = youtube_ids que o bar baniu ("🚫 Nunca mais") — saem da lista da TV (15/10/26).
    temas = estilos acesos do bar (20/10/26, pedido do Garage: "clicar em 3 estilos e tocar só aquilo"): o Variado
    com vários botões acesos vira a mistura SÓ deles."""
    import random
    cat = CANAIS.get(canal_key, {}).get('cat', canal_key)
    if canal_key == 'variado' and temas and isinstance(cat, list):
        # 20/10/26: + Esporte e Comédia na mistura (as categorias deles não estão na lista do Variado)
        cat = [c for c in cat if c in temas] + [c for c in temas if c not in cat and c in _BOTAO_DA_CAT] or cat
    conn = get_pubshow_db()

    if isinstance(cat, list):
        # Multi-categoria (ex: sport_mix) — busca de todas e embaralha
        placeholders = ','.join('?' * len(cat))
        videos = conn.execute(
            f'SELECT * FROM pubshow_videos WHERE categoria IN ({placeholders}) AND ativo=1',
            cat
        ).fetchall()
        videos = [dict(v) for v in videos]
        random.shuffle(videos)
        if canal_key == 'variado' and temas:
            # vários estilos acesos: um de cada estilo, em rodízio (Rock, Reggae, Pop, Rock…). Embaralhar tudo junto
            # deixava o Rock (200+ clipes) abafar o Reggae (10 no ar em 20/10/26); o estilo pequeno se repete no rodízio.
            fam = {}
            for v in videos:
                _c = v.get('categoria')
                fam.setdefault(_BOTAO_DA_CAT.get(_c) or pubshow_estilo.CANAL_ESTILO.get(_c, _c), []).append(v)
            grupos = list(fam.values())
            random.shuffle(grupos)
            n = max((len(g) for g in grupos), default=0)
            videos = [g[i % len(g)] for i in range(n) for g in grupos]
        videos = videos[:limit]
    else:
        # Pega um pool dos mais populares e embaralha — a TV nunca começa
        # sempre pelas mesmas músicas (pedido do dono: aleatório dentro do canal).
        videos = conn.execute(
            'SELECT * FROM pubshow_videos WHERE categoria=? AND ativo=1 ORDER BY ordem, views_milhoes DESC LIMIT ?',
            (cat, limit)
        ).fetchall()
        videos = [dict(v) for v in videos]
        random.shuffle(videos)

    conn.close()
    # 18/10/26 (trava eleitoral): jingle que entrou no acervo por pedido antigo não toca na rotação; na janela, filtro
    # estrito; no domingo, nada que entrou por pedido de cliente (C3 do verificador: o título engana)
    _jan, _vot = _janela_eleitoral(), _dia_da_votacao()
    videos = [v for v in videos if _yid_ok(v.get('youtube_id')) and not _tem_politica(v.get('titulo'), _jan)
              and not (_vot and v.get('subcategoria') == 'pedido_publico')]
    if bloqueados:
        _ban = set(bloqueados)
        videos = [v for v in videos if v.get('youtube_id') not in _ban]
    return videos


# ── ROTAS PÚBLICAS ─────────────────────────────────────────────────────────────

def _reais_curto(v: float) -> str:
    """8.0 → 'R$ 8' · 7.5 → 'R$ 7,50' (preço de card: sem ',00')."""
    v = round(float(v), 2)
    return 'R$ ' + (f'{v:.0f}' if abs(v - round(v)) < 0.005 else f'{v:.2f}'.replace('.', ','))


def _site_modelo_novo() -> bool:
    """Site com o modelo NOVO (Efí, dinheiro na hora, mensalidade saindo de 30% dos momentos até fechar).
    Desligado por padrão (decisão padrão 7, 16/10/26): só liga junto com o Lote C."""
    return os.environ.get('PUBSHOW_SITE_MODELO_NOVO', '') == '1'


def _site_novo_regras():
    """Lote C, fatia b (16/10/26): o que o site NOVO (split Efí) diz sai das MESMAS regras do pubshow_pay:
    o R$ 1 do app, o R$ 1 na gorjeta (PUBSHOW_GORJETA_CONV) e o % da mensalidade (PUBSHOW_MENSAL_PCT).
    Lote D B (16/10/26): a garantia de 3 meses saiu do site (saiu do dinheiro no Lote D A2)."""
    if pubshow_pay is None:
        return {'taxa': PIX_TAXA_CONVENIENCIA, 'taxa_gorjeta': PIX_TAXA_CONVENIENCIA, 'pct': 30}
    return {'taxa': pubshow_pay.conv_cent_para('flash') / 100,
            'taxa_gorjeta': pubshow_pay.conv_cent_para('gorjeta') / 100,
            'pct': pubshow_pay.mensal_pct()}


def _texto_fundador(vagas: int) -> str:
    """Lote D B (16/10/26, aprovado): a oferta de fundador, com as vagas contadas no banco. '' quando acabaram.
    Nunca "pra sempre" nem "depois R$ X" (Lote D A1)."""
    if vagas <= 0:
        return ''
    return (f'{PLANO["preco_fmt"]} travado por {FUNDADOR_MESES} meses para os {FUNDADOR_VAGAS} primeiros bares. '
            + ('Resta 1 vaga.' if vagas == 1 else f'Restam {vagas} vagas.'))


def taxa_app_txt() -> str:
    """Lote D B (16/10/26): o "R$ 1 do app" que o site diz (llms.txt), pela MESMA regra da landing: só com o PIX
    automático ligado (ASAAS_API_KEY) ou o site do modelo novo (a do pubshow_pay). '' = não fala de valor a mais."""
    taxa = PIX_TAXA_CONVENIENCIA if os.environ.get('ASAAS_API_KEY') else 0.0
    if _site_modelo_novo():
        taxa = _site_novo_regras()['taxa']
    return _reais_curto(taxa) if taxa > 0 else ''


def _site_faq(taxa_txt: str, modelo_novo: bool, fundador: str = '') -> list:
    """Perguntas do site: a MESMA lista vai pra tela e pro JSON-LD (FAQPage) — nunca divergem."""
    preco = PLANO['preco_fmt']
    musica = ('Não. Música é grátis: escolhida na lista do bar ou buscada no YouTube (se o bar liberar). '
              'O cliente paga só pelos momentos na TV.')
    ecad = ('O PubShow toca clipes públicos do YouTube. A licença de música ambiente do ECAD é do '
            'estabelecimento, como já é com rádio ou som ambiente.')
    instalar = ('Não. A TV abre um link no navegador (Smart TV, Chromecast ou notebook). '
                'O cliente usa a câmera do celular, sem baixar app.')
    if modelo_novo:
        # Lote C, fatia b (16/10/26): o SPLIT aprovado pelo Diogo (RELATORIO_FINAL f.2, blocos 4-6). "Sem o R$ 1"
        # na gorjeta só quando for verdade (hoje PUBSHOW_GORJETA_CONV=1: o cliente paga o R$ 1 do app nela).
        # Lote D B (16/10/26, aprovado): a regra dos 30% no lugar de "os primeiros R$ 49,90"; sem garantia; fundador.
        rg = _site_novo_regras()
        pct = rg['pct']
        gorj_taxa = ('O cliente não paga nada a mais nela.' if rg['taxa_gorjeta'] <= 0 else
                     f'O cliente paga {_reais_curto(rg["taxa_gorjeta"])} do app por cima, que ele vê antes.')
        app_txt = (f'o {_reais_curto(rg["taxa"])} do app fica com o PubShow' if rg['taxa'] > 0
                   else 'o PubShow não fica com nada a mais')
        custa = (f'{preco} por mês, que saem de {pct}% de cada momento até fechar. '
                 'Você nunca tira do bolso.' + (f' Oferta de fundador: {fundador}' if fundador else ''))
        render_pouco = (f'A mensalidade sai de {pct}% de cada momento, até fechar {preco} no mês. Se render pouco, '
                        f'você paga só esses {pct}% e não paga a diferença: nunca tira do bolso.')
        return [
            ('Pedir música é pago?', musica),
            ('Quanto custa pro bar?', custa),
            ('Como e quando eu recebo?', 'Na hora, na conta Efí do bar. O PIX do cliente já cai dividido: o valor '
                                         f'do momento vai pra conta do bar e {app_txt}.'),
            ('Preciso abrir conta?', 'Sim, uma conta Efí grátis pelo app (uns 15 minutos), no mesmo CPF/CNPJ do '
                                     'cadastro do bar. Sem ela, só a música grátis funciona.'),
            ('E a gorjeta?', 'Vai inteira pra equipe, separada no painel. A gorjeta nunca paga a mensalidade. '
                             + gorj_taxa),
            ('E se o jukebox render pouco?', render_pouco),
            ('Preciso instalar algo?', instalar),
            ('E direito autoral?', ecad),
            ('Posso cancelar?', 'Sim, pelo painel, sem multa.'),
        ]
    faq = [
        ('Pedir música é pago?', musica),
        ('Quanto custa pro bar?', f'{preco} por mês, plano único, tudo incluso. 7 dias grátis, sem cartão.'),
        ('Como eu acompanho o dinheiro?',
         'No painel você vê, a qualquer hora, quanto os momentos renderam pro bar e quanto a equipe ganhou '
         'de gorjeta, separado. Dúvida sobre como o dinheiro chega até o bar? Chama a gente no WhatsApp '
         'antes de assinar.'),
        ('E a gorjeta?', 'Fica separada no painel, como dinheiro da equipe: não entra no caixa da casa.'),
    ]
    if taxa_txt:
        # V6-02 (16/10/26): "o bar não paga taxa" só é verdade se a taxa de serviço cobre a do Asaas
        # (o líquido gravado é item + conveniência − taxa Asaas; com Asaas R$ 1,99 o bar paga a diferença)
        _sem_taxa_bar = round(PIX_TAXA_CONVENIENCIA, 2) >= round(PIX_TAXA_ASAAS, 2)
        # Lote D B (16/10/26): "R$ 1 do app", nunca "taxa de serviço" (no bar isso é o 10% do garçom)
        faq.append((f'Quem paga o {taxa_txt} do app?',
                    f'O cliente: {taxa_txt} por PIX, que ele vê antes de pagar.'
                    + (' O bar não paga taxa por pedido.' if _sem_taxa_bar else '')))
    faq += [
        ('Preciso instalar algo?', instalar),
        ('E direito autoral?', ecad),
        ('Posso cancelar?', 'Sim, pelo painel, sem multa. O jukebox fica ativo até o fim do mês já pago.'),
    ]
    return faq


def _tem_assinatura_ativa(business_id) -> bool:
    """V6-05 (16/10/26): já existe assinatura status='ativo' no banco? (evita criar a 2ª no Asaas)."""
    if not business_id:
        return False
    conn = get_pubshow_db()
    r = conn.execute("SELECT 1 FROM pubshow_assinaturas WHERE business_id=? AND status='ativo' LIMIT 1",
                     (business_id,)).fetchone()
    conn.close()
    return bool(r)


@pubshow_bp.route('/')
def index():
    """Landing em 7 blocos (S6/B11, 16/10/26): preço, taxa, FAQ e JSON-LD saem do código, nunca à mão."""
    conn = get_pubshow_db()
    total_videos = conn.execute('SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1').fetchone()[0]
    total_canais  = len([k for k in CANAIS if k != 'variado'])   # Lote D C: o "Variado" é mistura, não estilo novo
    conn.close()
    modelo_novo = _site_modelo_novo()
    # o "R$ 1 do app" (era "taxa de serviço"): a mesma regra do celular (só existe com o PIX automático ligado)
    taxa = PIX_TAXA_CONVENIENCIA if os.environ.get('ASAAS_API_KEY') else 0.0
    rg = _site_novo_regras() if modelo_novo else None
    if modelo_novo:
        taxa = rg['taxa']   # fatia b: no split a taxa é a do pubshow_pay (a mesma do celular Efí)
    # Lote D B (16/10/26): oferta de fundador só no site do modelo novo, com as vagas contadas no banco (some no 0)
    vagas = fundador_vagas() if modelo_novo else 0
    fundador = _texto_fundador(vagas)
    taxa_txt = _reais_curto(taxa) if taxa > 0 else ''
    momentos = []
    for k in ('flash', 'parabens', 'dedicatoria', 'casamento', 'cantada', 'salve', 'brinde', 'chegada'):
        t = TIPOS_PEDIDO[k]
        momentos.append({'emoji': t['emoji'], 'nome': re.sub(r'\s*[^\w\s!?.,-]+$', '', t['nome']).strip(),
                         'preco': _reais_curto(t['preco'] + taxa), 'ok': bool(t.get('ok'))})
    faq = _site_faq(taxa_txt, modelo_novo, fundador)
    # V6-05: bar logado que já paga vê "Seu plano está ativo → Painel", não "Assinar" de novo
    _b = _get_business()
    plano_em_dia = bool(_b and _b['plano_ativo'] and _tem_assinatura_ativa(_b['id']))
    # VDB-06 (16/10/26): bar na Efí não assina pelo Asaas (a mensalidade sai dos momentos) → nunca "Assinar". Ativo =
    # "Seu plano está ativo → Painel"; cancelado = "Abrir o painel" (o /assinar já mandava ele pro Meu dinheiro)
    efi_logado = bool(_b and _efi_para(_b))
    if efi_logado and _b['plano_ativo']:
        plano_em_dia = True
    preco_str = f"{PLANO['preco_cent'] / 100:.2f}"
    ld = [
        {'@context': 'https://schema.org', '@type': 'SoftwareApplication', 'name': 'PubShow',
         'applicationCategory': 'BusinessApplication', 'operatingSystem': 'Navegador (web)',
         'url': 'https://www.4kitem.com.br/pubshow/',
         'description': 'Jukebox digital para bar: o cliente pede música de graça pelo QR Code e paga no PIX '
                        'só pelos momentos na TV (furar a fila, parabéns, pedido de namoro).',
         'offers': {'@type': 'Offer', 'price': preco_str, 'priceCurrency': 'BRL',
                    'url': 'https://www.4kitem.com.br/pubshow/#preco',
                    'priceSpecification': {'@type': 'UnitPriceSpecification', 'price': preco_str,
                                           'priceCurrency': 'BRL', 'unitCode': 'MON',
                                           'referenceQuantity': {'@type': 'QuantitativeValue', 'value': 1,
                                                                 'unitCode': 'MON'}}},
         'publisher': {'@type': 'Organization', 'name': '4KITEM',
                       'legalName': 'LESSMANN COMÉRCIO, SERVIÇOS E TECNOLOGIA LTDA',
                       'url': 'https://www.4kitem.com.br/'}},
        {'@context': 'https://schema.org', '@type': 'FAQPage',
         'mainEntity': [{'@type': 'Question', 'name': q,
                         'acceptedAnswer': {'@type': 'Answer', 'text': a}} for q, a in faq]},
    ]
    return render_template('pubshow/landing.html',
                           planos=PLANOS, canais=CANAIS,
                           total_videos=total_videos, total_canais=total_canais,
                           modelo_novo=modelo_novo, taxa=taxa, taxa_txt=taxa_txt, momentos=momentos,
                           gorjeta_txt=' · '.join(f'{v:.0f}' for v in GORJETA_VALORES),
                           gorjeta_sem_taxa=bool(modelo_novo and rg['taxa_gorjeta'] <= 0),
                           # Lote D B (16/10/26): a calculadora e o texto leem o MESMO % do FAQ (era a garantia, que saiu)
                           mensal_pct=(rg['pct'] if modelo_novo else 0),
                           fundador_vagas=vagas, fundador_txt=fundador,
                           faq=faq, ld_json=ld, plano_em_dia=plano_em_dia, efi_logado=efi_logado,
                           empresa_cnpj=os.environ.get('EMPRESA_CNPJ', '').strip())


# ── Limite de tentativas (15/10/26) — login, admin e "esqueci a senha" ──────────
# Em memória, por processo (1 worker hoje). 5 erros em 15 min por IP OU por e-mail = espera.
_RL_LOCK = _threading.Lock()
_RL_FALHAS = {}            # chave -> [timestamps]
_RL_MAX, _RL_JANELA = 5, 15 * 60


def _ip_real():
    """IP do visitante: Cloudflare (CF-Connecting-IP) → 1º do X-Forwarded-For → remote_addr."""
    ip = request.headers.get('CF-Connecting-IP') or \
        (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip() or request.remote_addr or ''
    return ip[:64]


def _rl_bloqueado(*chaves, max_n=_RL_MAX, janela=_RL_JANELA):
    agora = _time.time()
    with _RL_LOCK:
        for k in chaves:
            v = [t for t in _RL_FALHAS.get(k, []) if agora - t < janela]
            if v:
                _RL_FALHAS[k] = v
            else:
                _RL_FALHAS.pop(k, None)
            if len(v) >= max_n:
                return True
    return False


def _rl_falhou(*chaves):
    agora = _time.time()
    with _RL_LOCK:
        if len(_RL_FALHAS) > 5000:      # não cresce sem fim
            for k in [k for k, v in _RL_FALHAS.items() if not v or agora - v[-1] >= _RL_JANELA]:
                _RL_FALHAS.pop(k, None)
        for k in chaves:
            _RL_FALHAS.setdefault(k, []).append(agora)


def _rl_limpar(*chaves):
    with _RL_LOCK:
        for k in chaves:
            _RL_FALHAS.pop(k, None)


_RL_MSG = 'Muitas tentativas. Espere 15 minutos e tente de novo.'


@pubshow_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if session.get('pub_business_id'):
        return redirect('/pubshow/painel')
    erro = ''
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        senha    = request.form.get('senha', '')
        _chaves  = ('login_ip:' + _ip_real(), 'login_email:' + email)
        if _rl_bloqueado(*_chaves):
            return render_template('pubshow/entrar.html', erro=_RL_MSG), 429
        conn     = get_pubshow_db()
        b = conn.execute('SELECT * FROM pubshow_businesses WHERE email=?', (email,)).fetchone()
        conn.close()
        if b and check_password_hash(b['password_hash'], senha):
            session['pub_business_id']   = b['id']
            session['pub_business_nome'] = b['nome']
            session['pub_canal']         = b['canal_atual']
            conn2 = get_pubshow_db()
            conn2.execute("UPDATE pubshow_businesses SET ultimo_acesso=datetime('now','-3 hours') WHERE id=?", (b['id'],))
            conn2.commit(); conn2.close()
            _rl_limpar('login_email:' + email)
            return redirect('/pubshow/painel')
        _rl_falhou(*_chaves)
        erro = 'E-mail ou senha incorretos.'
    return render_template('pubshow/entrar.html', erro=erro)


@pubshow_bp.route('/sair')
def sair():
    session.pop('pub_business_id', None)
    session.pop('pub_business_nome', None)
    session.pop('pub_canal', None)
    return redirect('/pubshow/entrar')


# ── RECUPERAÇÃO DE SENHA ───────────────────────────────────────────────────────

def _pubshow_send_wa(telefone: str, mensagem: str) -> bool:
    """Envia mensagem WhatsApp via Evolution API. Retorna True se enviou."""
    evo_url  = os.environ.get('EVOLUTION_API_URL', '').rstrip('/')
    evo_key  = os.environ.get('EVOLUTION_API_KEY', '')
    instance = os.environ.get('PUBSHOW_WA_INSTANCE', '')
    if not evo_url or not evo_key or not instance:
        return False
    digits = re.sub(r'\D', '', telefone)
    if not digits.startswith('55'):
        digits = '55' + digits
    try:
        r = _requests.post(
            f'{evo_url}/message/sendText/{instance}',
            json={'number': digits + '@s.whatsapp.net', 'text': mensagem},
            headers={'apikey': evo_key},
            timeout=8
        )
        return r.status_code < 300
    except Exception as ex:
        log.warning('[PUBSHOW] WA send error: %s', ex)
        return False


_MSG_RECUPERAR = ('Se este e-mail estiver cadastrado, enviamos o link pra criar uma senha nova no WhatsApp '
                  'do número cadastrado (ou no próprio e-mail). O link vale 2 horas.')


@pubshow_bp.route('/recuperar', methods=['GET', 'POST'])
def recuperar_senha():
    """15/10/26: NUNCA mostra o link na tela (antes, com o WhatsApp fora, qualquer um tomava a conta);
    a mesma frase sempre (não revela quais e-mails existem); token com secrets; 5 pedidos por IP a
    cada 15 min e no máximo 1 envio por conta a cada 10 min (não vira metralhadora de WhatsApp)."""
    msg  = ''
    erro = ''
    if request.method == 'POST':
        _chave_ip = 'recuperar_ip:' + _ip_real()
        if _rl_bloqueado(_chave_ip):
            return render_template('pubshow/recuperar.html', msg='', erro=_RL_MSG), 429
        _rl_falhou(_chave_ip)
        email = request.form.get('email', '').strip().lower()
        conn  = get_pubshow_db()
        b = conn.execute(
            'SELECT * FROM pubshow_businesses WHERE email=?', (email,)
        ).fetchone() if email else None
        msg = _MSG_RECUPERAR
        # Já mandou um link nos últimos 10 min (o token vale 2 h) → não manda outro
        _recente = bool(b and b['reset_expires'] and
                        b['reset_expires'] > (datetime.now() + timedelta(hours=2, minutes=-10)).isoformat())
        if b and not _recente:
            token   = secrets.token_urlsafe(32)
            expira  = (datetime.now() + timedelta(hours=2)).isoformat()
            conn.execute(
                'UPDATE pubshow_businesses SET reset_token=?, reset_expires=? WHERE id=?',
                (token, expira, b['id'])
            )
            conn.commit()
            _base = os.environ.get('BASE_URL', 'https://www.4kitem.com.br')
            link = f'{_base}/pubshow/recuperar/{token}'
            sent = _pubshow_send_wa(
                b['telefone'] or '',
                f'🔐 *PUBSHOW — Redefinição de senha*\n\n'
                f'Olá, {b["nome"]}!\n\n'
                f'Clique no link abaixo para criar uma nova senha. O link expira em 2 horas.\n\n'
                f'{link}\n\n'
                f'Se não foi você que pediu, ignore esta mensagem.'
            )
            if not sent:
                # WhatsApp fora → vai pro e-mail cadastrado (é o dono que recebe, nunca a tela)
                _nome = _html_escape(b['nome'] or '')
                sent = _pubshow_enviar_email(
                    b['email'], 'PUBSHOW — link pra criar uma senha nova',
                    _email_html_base('Senha nova',
                        f'<p style="color:#e5e7eb;font-size:15px">Olá, {_nome}!</p>'
                        f'<p style="color:#e5e7eb;font-size:15px">Clique no link pra criar uma senha nova. Ele vale 2 horas.</p>'
                        f'<p><a href="{link}" style="color:#4ade80;font-weight:700">Criar senha nova</a></p>'
                        f'<p style="color:#9ca3af;font-size:13px">Se não foi você que pediu, ignore este e-mail.</p>'))
            if not sent:
                log.warning('[PUBSHOW] recuperar senha: WhatsApp e e-mail falharam (bar %s)', b['id'])
        conn.close()
    return render_template('pubshow/recuperar.html', msg=msg, erro=erro)


def _avisar_senha_trocada(b):
    """Avisa o dono no WhatsApp que a senha mudou (15/10/26) — em fundo, não segura a resposta."""
    tel, nome = (b['telefone'] or ''), (b['nome'] or '')
    if not tel:
        return
    def _envia():
        try:
            _pubshow_send_wa(tel, f'🔐 *PUBSHOW*\n\nOlá, {nome}! A senha do seu painel acabou de ser trocada.\n\n'
                                  f'Se não foi você, fale com o suporte agora.')
        except Exception as ex:
            log.warning('[PUBSHOW] aviso de senha trocada falhou: %s', ex)
    _threading.Thread(target=_envia, daemon=True).start()


@pubshow_bp.route('/recuperar/<token>', methods=['GET', 'POST'])
def recuperar_senha_form(token):
    conn = get_pubshow_db()
    b = conn.execute(
        "SELECT * FROM pubshow_businesses WHERE reset_token=? AND reset_expires>?",
        (token, datetime.now().isoformat())
    ).fetchone()
    conn.close()
    if not b:
        return render_template('pubshow/recuperar.html',
                               msg='', erro='Link inválido ou expirado. Solicite um novo.')
    erro = ''
    if request.method == 'POST':
        nova = request.form.get('senha', '')
        conf = request.form.get('confirma', '')
        if len(nova) < 6:
            erro = 'A senha deve ter ao menos 6 caracteres.'
        elif nova != conf:
            erro = 'As senhas não coincidem.'
        else:
            conn2 = get_pubshow_db()
            conn2.execute(
                'UPDATE pubshow_businesses SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?',
                (generate_password_hash(nova), b['id'])
            )
            conn2.commit(); conn2.close()
            _avisar_senha_trocada(b)
            return redirect('/pubshow/entrar?ok=senha')
    return render_template('pubshow/recuperar_form.html', token=token, erro=erro)


@pubshow_bp.route('/bem-vindo')
@pubshow_login_required
def bem_vindo():
    """Página de onboarding pós-cadastro — guia o bar pelos primeiros passos."""
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')
    # Lote D B (16/10/26): modo Efí → a frase "Sem mensalidade do bolso…" no lugar do teste grátis + passo da conta Efí
    # 17/10/26 (caminho único): o bar recém-cadastrado ainda não tem conta (vende pela ponte): conta a lista
    _efi = _efi_lista(b)
    return render_template('pubshow/bem_vindo.html', b=dict(b), planos=PLANOS, canais=CANAIS,
                           efi_modo=_efi, sem_bolso_txt=(_texto_sem_bolso(b) if _efi else ''),
                           dica_2_bares=_dica_2_bares(_efi))


def _doc_norm(doc):
    """CPF/CNPJ sem pontuação e em maiúscula (16/10/26, V3-08: o CNPJ novo da Receita, de jul/2026,
    pode ter LETRAS nas 12 primeiras posições; antes o filtro de só-números apagava as letras)."""
    return re.sub(r'[^0-9A-Z]', '', str(doc or '').upper())


def _doc_valido(doc):
    """CPF (11) ou CNPJ (14, numérico ou alfanumérico) com dígito verificador certo (mesma regra do pubshow_pay.doc_valido)."""
    d = _doc_norm(doc)
    if len(d) == 11 and d.isdigit() and d != d[0] * 11:
        for n in (9, 10):
            soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
            if int(d[n]) != (soma * 10 % 11) % 10:
                return False
        return True
    if len(d) == 14 and d[12:].isdigit() and d != d[0] * 14:
        for n in (12, 13):
            pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if n == 12 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
            r = sum((ord(d[i]) - 48) * pesos[i] for i in range(n)) % 11   # letra = ASCII − 48 (A=17)
            if int(d[n]) != (0 if r < 2 else 11 - r):
                return False
        return True
    return False


# Lote D A6 (16/10/26): saiu o _SQL_DOC_DIGITOS (só servia pra trava "1 teste por CPF/CNPJ", que caiu)


@pubshow_bp.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    """15/10/26: plano único (o ?plano= da URL é ignorado — link velho/errado não trava mais o
    cadastro em silêncio); CPF/CNPJ só dígitos e com dígito verificador; sem a trava "1 teste por IP"
    (no 4G muita gente divide o mesmo IP — barrava dono de bar de verdade); erro genérico na tela
    (detalhe no log); os campos voltam preenchidos (`form`) quando dá erro."""
    if session.get('pub_business_id'):
        return redirect('/pubshow/painel')
    erro = ''
    plano_sel = 'bar'
    form = {}
    if request.method == 'POST':
        nome     = request.form.get('nome', '').strip()
        tipo     = request.form.get('tipo', 'bar')
        email    = request.form.get('email', '').strip().lower()
        telefone = request.form.get('telefone', '').strip()
        cpf_cnpj = _doc_norm(request.form.get('cpf_cnpj', ''))
        senha    = request.form.get('senha', '')
        cupom_cod= request.form.get('cupom', '').strip()
        form = {'nome': nome, 'email': email, 'telefone': telefone,
                'cpf_cnpj': request.form.get('cpf_cnpj', '').strip(), 'cupom': cupom_cod}
        ip_cliente = _ip_real()   # só registro (signup_ip); não bloqueia mais nada
        if not all([nome, email, telefone, senha, cpf_cnpj]):
            erro = 'Preencha todos os campos obrigatórios.'
        elif len(senha) < 6:
            erro = 'A senha deve ter ao menos 6 caracteres.'
        elif not _doc_valido(cpf_cnpj):
            erro = 'CPF ou CNPJ inválido. Confere os números.'
        else:
            # Lote D A6 (16/10/26, aprovado pelo Diogo): 2º bar com o MESMO CPF/CNPJ é liberado (dono de 2 bares).
            # Saiu a trava "1 teste por CPF/CNPJ"; o e-mail continua único (cada bar entra com o seu). Os 2 bares
            # podem usar a mesma conta Efí do mesmo titular (o titular é conferido contra o CPF/CNPJ do cadastro).
            try:
                code  = _gerar_code()
                jtoken = _gerar_jukebox_token()
                # Aplica cupom — estende trial se válido
                trial_dias = 7
                # VDB-01 (16/10/26): bar que nasce na Efí não tem teste grátis — o cupom é ignorado (nem gasta uso)
                if cupom_cod and _efi_cadastro_novo():
                    cupom_cod = ''
                # 17/10/26 (decisão 6): o 2º bar do mesmo CPF/CNPJ nasce SEM teste grátis (plano_ativo=0, teste vencido) e
                # entra direto no Assinar. Na Efí não existe teste grátis (o interruptor é o plano_ativo). O cupom de
                # teste não vale (nem gasta uso).
                _cx = get_pubshow_db()
                try:
                    _sem_teste = (not _efi_cadastro_novo()) and _doc_ja_tem_bar(_cx, cpf_cnpj)
                finally:
                    _cx.close()
                if _sem_teste:
                    cupom_cod, trial_dias = '', 0
                cupom_dados = _aplicar_cupom(cupom_cod) if cupom_cod else None
                if cupom_dados and cupom_dados['tipo'] == 'trial':
                    trial_dias = int(cupom_dados['valor'])
                conn = get_pubshow_db()
                try:
                    # sem teste (decisão 6): o "teste" já nasce vencido (1 min atrás), nem o 1º segundo fica aberto
                    trial = (datetime.now() + (timedelta(minutes=-1) if _sem_teste else timedelta(days=trial_dias))).isoformat()
                    # Lote D A1: o bar guarda o preço do DIA do cadastro (PUBSHOW_PLANO_CENT agora).
                    # 17/10/26 (decisão 8): a vaga de fundador NÃO é mais dada no cadastro — só no 1º pagamento de
                    # verdade (assinatura paga no Asaas ou mensalidade saindo de um momento na Efí): _virar_fundador.
                    cur = conn.execute(
                        '''INSERT INTO pubshow_businesses
                           (nome, tipo, email, telefone, cpf_cnpj, password_hash, code,
                            plano, plano_ativo, canal_atual, trial_ends, jukebox_token, signup_ip, plano_cent)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (nome, tipo, email, telefone, cpf_cnpj,
                         generate_password_hash(senha), code, plano_sel, 0 if _sem_teste else 1, 'rock', trial,
                         jtoken, ip_cliente, _plano_cent())
                    )
                    conn.commit()
                    b = conn.execute('SELECT * FROM pubshow_businesses WHERE email=?', (email,)).fetchone()
                finally:
                    # Lote D (16/10/26): fecha SEMPRE. Antes o e-mail repetido (UNIQUE) deixava esta conexão aberta com
                    # a transação do INSERT, e o banco ficava travado pra escrita ("database is locked") até o GC
                    conn.close()
                if _sem_teste:
                    log.info('[PUBSHOW] cadastro: bar %s é o 2º+ do mesmo CPF/CNPJ — sem teste grátis', b['id'])
                session['pub_business_id']   = b['id']
                session['pub_business_nome'] = b['nome']
                session['pub_canal']         = b['canal_atual']
                # Agenda sequência de emails de onboarding (sem teste grátis: só boas-vindas e dica, nada de "teste acaba")
                try:
                    _agendar_emails_onboarding(b['id'], trial, sem_teste=_sem_teste)
                except Exception:
                    pass
                return redirect('/pubshow/assinar/bar' if _sem_teste else '/pubshow/bem-vindo')
            except Exception as ex:
                if 'UNIQUE' in str(ex):
                    erro = ('Este e-mail já está cadastrado. Faça login. Tem mais de um bar? '
                            'Cadastre cada um com um e-mail diferente.')
                else:
                    log.exception('[PUBSHOW] erro no cadastro (%s)', email)
                    erro = 'Não deu pra criar a conta agora. Tenta de novo em instantes ou fale com o suporte.'
    # Lote D B (16/10/26): o cadastro fala do jeito do modo em que o bar NOVO vai nascer (Efí só com EFI_BARES='*')
    _efi = _efi_cadastro_novo()
    return render_template('pubshow/cadastro.html', erro=erro,
                           planos=PLANOS, plano_sel=plano_sel,
                           tipos=TIPOS_ESTABELECIMENTO, form=form,
                           efi_modo=_efi, sem_bolso_txt=(_texto_sem_bolso() if _efi else ''),
                           dica_2_bares=_dica_2_bares(_efi))


# ── TV PLAYER (roda na tela do bar) ───────────────────────────────────────────

@pubshow_bp.route('/tv/<code>')
def tv(code):
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE code=?', (code,)).fetchone()
    if not b:
        conn.close()
        return 'Estabelecimento não encontrado', 404
    if _efi_para(b):
        # Lote D A3 (16/10/26): bar pausado (60 dias sem momento pago) → a TV mostra "pausado, fale com o suporte"
        # e volta sozinha quando o suporte reativar (a tela pergunta o /api/status)
        _pz = _efi_pausa(b)
        if _pz and _pz['pausado']:
            conn.close()
            return render_template('pubshow/tv_pausado.html', b=dict(b), msg=MSG_PAUSADO)
    import json as _json
    canal_key = b['canal_atual'] or 'rock'
    canal     = CANAIS.get(canal_key, CANAIS['rock'])
    # 20/10/26: no modo fundo (Esporte + música) o esporte é o fundo; o YouTube da TV só toca a música
    fundo_clipes = _fundo_clipes(b)
    _temas_tv = _so_musica(_temas_do_bar(b)) if fundo_clipes else _temas_do_bar(b)
    videos    = _videos_do_canal(canal_key, bloqueados=_videos_bloqueados(b), temas=_temas_tv)
    # 1) Filtra canais pelo plano do bar (grupos_tv)
    canais_plano = _plano_canais_permitidos(dict(b))
    # 2) Filtra pelos temas que o bar habilitou dentro do que o plano permite
    try:    temas_hab = _json.loads(b['temas_habilitados'] or 'null')
    except: temas_hab = None
    if temas_hab:
        canais_tv = {k: v for k, v in canais_plano.items() if k in temas_hab}
        # Garante que o canal atual sempre aparece
        if canal_key not in canais_tv:
            canais_tv[canal_key] = CANAIS.get(canal_key, CANAIS['rock'])
    else:
        canais_tv = canais_plano
    try:    anuncios = _json.loads(b['anuncios_json'] or '[]')
    except: anuncios = []
    # Slides do sistema: DESLIGADOS desde 11/09/26 (só propaganda que o gerente do bar lançar aparece na TV).
    # 16/10/26 (B7): saiu a consulta que a TV nunca usava.
    conn.close()

    # Gera QR do Jukebox server-side — mais confiável que API externa
    _jk_token = b['jukebox_token'] or b['code']
    _jk_url   = f"https://www.4kitem.com.br/pubshow/jukebox/{_jk_token}"
    try:
        import qrcode as _qrcode, io as _io, base64 as _base64
        _qr = _qrcode.QRCode(version=None,
                              error_correction=_qrcode.constants.ERROR_CORRECT_M,
                              box_size=7, border=2)
        _qr.add_data(_jk_url)
        _qr.make(fit=True)
        _img = _qr.make_image(fill_color='black', back_color='white')
        _buf = _io.BytesIO()
        _img.save(_buf, format='PNG')
        tv_qr_b64 = _base64.b64encode(_buf.getvalue()).decode()
    except Exception:
        tv_qr_b64 = None

    return render_template('pubshow/tv.html', b=dict(b), canal=canal,
                           canal_key=canal_key, videos=videos, canais=canais_tv,
                           anuncios=anuncios, qr_preco_txt=_qr_preco_tv(b),
                           tv_qr_b64=tv_qr_b64, jukebox_url=_jk_url,
                           # 🔇 17/10/26: grupo de TODO canal (o painel pode pôr a TV num canal fora da lista dela)
                           grupos_canal={k: v.get('grupo', 'musica') for k, v in CANAIS.items()},
                           # 20/10/26: grupo de cada CATEGORIA (canais de 1 categoria só): o mudo decide pelo vídeo,
                           # porque o 'variado' (2+ estilos acesos) mistura música e esporte na mesma lista
                           grupos_cat={v['cat']: v.get('grupo', 'musica') for v in CANAIS.values()
                                       if isinstance(v.get('cat'), str)},
                           # 20/10/26: categoria → botão do Estilo do bar (⚽ Esporte, 🎸 Rock…): o selo da faixa no 'variado'
                           rotulos_cat={t: f"{e['emoji']} {e['nome']}" for e in ESTILOS_BAR.values()
                                        for t in (e.get('temas') or [])},
                           fundo_clipes=fundo_clipes)   # 20/10/26: esporte no fundo + clipe na janela (None = desligado)


def _qr_preco_tv(b):
    """Texto de preço do QR fixo da TV. O "furar a fila" = o MESMO que o celular mostra (preço do bar + taxa
    de serviço do PIX automático). 16/10/26: antes a TV dizia R$ 7 e o celular R$ 8; R$ 7,50 virava "R$ 8".
    Bar que desligou o furar a fila: só "GRÁTIS". V-S5-06: o /api/status manda este texto também, então o
    happy hour que começa/acaba com a TV aberta muda o preço da TV junto com o do celular."""
    if 'flash' not in _tipos_disponiveis(b):
        return 'GRÁTIS'
    _fl = round(_precos_do_bar(b)[0]['flash']['preco'] + _taxa_pix_bar(b, 'flash'), 2)
    fmt = (f'{_fl:.0f}' if abs(_fl - round(_fl)) < 0.005 else f'{_fl:.2f}').replace('.', ',')
    pix = bool(_gateway_ativo(b) or (b['requer_pix'] and b['pix_key']))
    return f'GRÁTIS · furar a fila R$ {fmt}' + (' no PIX' if pix else '')


# ── JUKEBOX MOBILE (cliente do bar escaneia QR) ───────────────────────────────

def _nome_tv(nome, n=20):
    """Nome na TV: até n letras, cortando no último espaço ('Maria Eduarda Albuquerque' → 'Maria Eduarda')."""
    nome = ' '.join((nome or '').split())
    if len(nome) <= n:
        return nome
    corte = nome[:n]
    if nome[n] != ' ' and ' ' in corte:
        corte = corte[:corte.rfind(' ')]
    return corte.strip()


def _ponte_pix_vivo(row) -> bool:
    """Ponte Efí: a cobrança vale 15 min (_efi_criar_cobranca_jukebox, expira=900). Margem de 1 min pro cliente pagar."""
    try:
        criado = datetime.strptime(str(row['created_at'])[:19], '%Y-%m-%d %H:%M:%S')
    except Exception:
        return False
    return (datetime.utcnow() - criado).total_seconds() < 14 * 60


def _pix_pendente_de_pedido(b, row, precos_bar):
    """Tela do PIX de um pedido que JÁ existe (16/10/26, crítico C2): o celular que recarrega a página
    (volta do app do banco) ou reenvia o mesmo pedido vê o MESMO PIX, nunca uma 2ª cobrança."""
    row = dict(row)
    via_asaas = bool(row.get('asaas_payment_id'))
    cobrado = row.get('valor_cobrado')
    efi_tela = {}   # Lote C, fatia b: {'efi': True, 'expira_min', 'expira_hora'} só no pedido da Efí
    if via_asaas and cobrado is not None:
        valor = float(cobrado)
        # valor (líquido) = item + conveniência - taxa Asaas → item = cobrado - conveniência
        item = round(valor - PIX_TAXA_CONVENIENCIA, 2)
        taxa = round(valor - item, 2)
    elif via_asaas:
        # crítico dinheiro B3 (17/10/26): PIX do Asaas de antes do deploy (sem valor_cobrado) = item + R$ 1 do app
        item = float(row.get('valor') or 0)
        taxa = PIX_TAXA_CONVENIENCIA
        valor = round(item + taxa, 2)
    elif row.get('efi_txid') and cobrado is not None:
        # ponte Efí (junção 17/10/26): valor = o item (do bar), valor_cobrado = item + taxa; o recebedor é a conta
        # Efí do PubShow (não o bar): a tela não mostra "Recebedor"
        valor = float(cobrado)
        item = float(row.get('valor') or 0)
        taxa = round(valor - item, 2)
        efi_tela = {'via_efi': True}
    elif row.get('via_gateway') and cobrado is not None and _efi_ligado():
        # Lote C (16/10/26): pedido da Efí — valor = o item (do bar), valor_cobrado = item + taxa de serviço;
        # confirma sozinho (a tela consulta a Efí), como no Asaas
        valor, item = float(cobrado), float(row.get('valor') or 0)
        taxa = round(valor - item, 2)
        via_asaas = True
        # fatia b (16/10/26): a tela da Efí diz quanto tempo o QR ainda vale ("vale 15 min")
        try:
            _cx = _pp_conn()
            try:
                _c = _cx.execute('SELECT expira_em FROM pubshow_pay_cobrancas WHERE pedido_id=?', (row['id'],)).fetchone()
            finally:
                _cx.close()
            efi_tela = dict(_efi_validade(_c['expira_em'] if _c else ''), efi=True)
        except Exception:
            efi_tela = {'efi': True}
    else:
        valor = item = float(row.get('valor') or 0)
        taxa = 0.0
    t = precos_bar.get(row.get('tipo')) or TIPOS_PEDIDO.get(row.get('tipo')) or {}
    payload = row.get('pix_payload') or ''
    return {**efi_tela,
        'pedido_id':  row['id'],
        'payload':    payload,
        'qr_b64':     _pix_qr_b64(payload) if payload else '',
        'valor':      valor,
        'valor_item': item,
        'taxa_conv':  taxa,
        'recebedor':  '' if row.get('efi_txid') else (b.get('pix_nome_recebedor') or b.get('nome')),
        'tipo_nome':  t.get('nome', row.get('tipo')),
        'tipo_emoji': t.get('emoji', '🎵'),
        'tipo':       row.get('tipo'),
        'via_asaas':  via_asaas or bool(row.get('efi_txid')),   # ponte Efí também confirma sozinha (sem "Já paguei")
    }


@pubshow_bp.route('/jukebox/<token>', methods=['GET', 'POST'])
# 16/10/26 (C2): GET da tela de um PIX já criado — o celular troca a URL pra cá (history.replaceState)
# depois do POST, então recarregar/voltar do banco reabre o MESMO PIX. Só abre pro mesmo celular (?c=).
@pubshow_bp.route('/jukebox/<token>/pix/<int:pix_id>', methods=['GET'])
def jukebox(token, pix_id=None):
    conn = get_pubshow_db()
    # Busca por jukebox_token primeiro; fallback para code (retrocompatibilidade)
    b = conn.execute(
        'SELECT * FROM pubshow_businesses WHERE jukebox_token=?', (token,)
    ).fetchone()
    if not b:
        b = conn.execute(
            'SELECT * FROM pubshow_businesses WHERE code=?', (token,)
        ).fetchone()
    conn.close()
    if not b:
        return render_template('pubshow/qr_invalido.html'), 404
    if b['suspenso']:
        # texto neutro (16/10/26, CEL-33): o dono que testa não pode achar que o QR quebrou
        return render_template('pubshow/qr_invalido.html', suspenso=True), 403
    b = dict(b)  # converte para dict — sqlite3.Row não tem .get() em Python < 3.12
    _efi   = _efi_para(b)          # Lote C (16/10/26): este bar cobra pela Efí com SPLIT (lista + conta Efí aceita)
    _ponte = _ponte_efi_para(b)    # ponte Efí (lista, sem conta aceita: conta Efí do PubShow); nunca junto do split
    _gw    = _gateway_ativo(b)     # Asaas, ponte ou split: cliente paga item + taxa, confirma sozinho

    sucesso      = None
    erro         = ''
    erro_tipo    = ''   # crítico leigo (17/10/26): 'gratis_espera' | 'musica_na_fila' | 'musica_longa' → a tela age diferente
    pix_pendente = None
    status_http  = 200
    _nome_lembrar = ''   # o nome que o celular lembra (no parabéns é o de quem MANDA, não o do aniversariante)
    ip_cliente   = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    # Identidade por CELULAR (11/09/26): no Wi-Fi do bar todos os clientes saem com o mesmo IP, então
    # limite/hora e dedup de 60 s por IP barravam o 2º cliente. O celular manda um client_id
    # (localStorage) e ele vira parte da chave; sem JS/localStorage cai no IP puro como antes.
    _cid = (request.form.get('client_id') or '').strip()
    if re.fullmatch(r'[A-Za-z0-9_-]{8,40}', _cid):
        ip_cliente = f'{ip_cliente}#{_cid}'
    else:
        _cid = ''

    # Aviso temporário
    aviso = None
    if b['aviso_jukebox']:
        expira = b['aviso_expira'] or ''
        if not expira or expira > datetime.now().isoformat():
            aviso = b['aviso_jukebox']

    # Verifica horário e status
    aberto, motivo_fechado = _jukebox_aberto(b)
    tipos_bar              = _tipos_disponiveis(b)
    precos_bar, hh_desconto = _precos_do_bar(b)

    # banner do happy hour = maior desconto real dos cards visíveis, não o % do painel (piso R$ 5, 15/10/26)
    hh_desconto = _hh_maior_desconto(precos_bar, tipos_bar)

    if pix_id is not None and request.method == 'GET':
        _c = (request.args.get('c') or '').strip()
        _row = None
        if re.fullmatch(r'[A-Za-z0-9_-]{8,40}', _c):
            _cx = get_pubshow_db()
            _row = _cx.execute('SELECT * FROM pubshow_pedidos WHERE id=? AND business_id=?', (pix_id, b['id'])).fetchone()
            _cx.close()
        if not _row or not (_row['ip_cliente'] or '').endswith('#' + _c) or not _row['pix_payload']:
            return redirect(f'/pubshow/jukebox/{token}')
        pix_pendente = _pix_pendente_de_pedido(b, _row, precos_bar)

    if request.method == 'POST':
        if not aberto:
            erro = motivo_fechado
        elif _limite_atingido(b, ip_cliente):
            erro = 'Muitos pedidos em pouco tempo. Aguarde um momento.'
        else:
            tipo          = request.form.get('tipo', '')
            # nome na TV: até 20 letras (16/10/26, B9), cortando no último espaço (C2)
            nome_cliente  = _nome_tv(request.form.get('nome_cliente', ''))
            mensagem      = request.form.get('mensagem', '').strip()[:80]
            # 16/10/26 (C2): no parabéns o nome GRANDE da TV é o do aniversariante; quem manda é opcional e
            # sai pequeno, junto do recado ("· de Zé")
            remetente     = _nome_tv(request.form.get('remetente', '')) if tipo == 'parabens' else ''
            _nome_lembrar = remetente if tipo == 'parabens' else nome_cliente
            categoria     = _categoria_do_bar(b)   # do servidor, não do formulário (16/10/26, D3V-03)
            youtube_id    = request.form.get('youtube_id', '').strip()
            titulo_pedido = request.form.get('titulo_pedido', '').strip()[:80]
            thumb_url     = request.form.get('thumb_url', '').strip()[:200]

            if youtube_id and not re.fullmatch(r'[A-Za-z0-9_-]{11}', youtube_id):
                # id do YouTube tem 11 letras (16/10/26, CEL-31): lixo virava pedido que a TV não toca
                erro, status_http = 'Música inválida. Escolhe na lista. 🎵', 400
            elif tipo not in tipos_bar or tipo not in precos_bar:
                erro = 'Tipo de pedido inválido.'
            elif not nome_cliente:
                erro = 'Informe o nome do aniversariante.' if tipo == 'parabens' else 'Informe seu nome.'
            elif _tem_palavrao(nome_cliente) or _tem_palavrao(mensagem) or _tem_palavrao(remetente):
                erro = 'Sem palavrão, beleza? 😉 Troca o nome ou a mensagem, que aparecem na TV.'
            elif _texto_politico(nome_cliente, mensagem, remetente):
                erro = MSG_POLITICA   # 18/10/26: trava eleitoral (vale pro grátis e pro pago, antes de qualquer PIX)
            elif any(_tem_link_ou_fone(x) for x in (nome_cliente, mensagem, remetente)):
                erro = MSG_LINK_FONE
            elif tipo in ('musica_especifica', 'musica_externa', 'flash', 'pular_espera') and not youtube_id:
                erro = 'Selecione uma música antes de confirmar.'
            elif tipo == 'cantada' and not mensagem:
                erro = 'Escreve a cantada antes de mandar. 😏'
            elif tipo in TIPOS_MUSICA_FILA and youtube_id and _musica_longa(b, youtube_id, tipo):
                # 17/10/26: máx. 10 min em todo pedido de música, grátis ou pago (antes de qualquer PIX)
                erro, erro_tipo = MSG_MUSICA_LONGA, 'musica_longa'
            elif tipo in TIPOS_MUSICA_FILA and youtube_id and (_fe := _fora_do_estilo(b, youtube_id, titulo_pedido)):
                erro, erro_tipo = _fe, 'fora_estilo'   # 17/10/26: "aqui só toca rock" — antes de gerar o PIX
            elif tipo in TIPOS_MUSICA_FILA and youtube_id and _musica_na_fila(b, youtube_id, ip_cliente, tipo, nome_cliente):
                erro, erro_tipo = '🎵 Essa música já foi pedida e vai tocar. Escolhe outra pra ti.', 'musica_na_fila'
            elif tipo in TIPOS_SEMPRE_GRATIS and (_espera := _gratis_espera_min(b, ip_cliente)) > 0:
                _fl = precos_bar.get('flash') if 'flash' in tipos_bar else None
                if 'pular_espera' in tipos_bar:
                    # 20/10/26: na Efí a saída mais barata é o ⏩ Pular a espera (fila normal); o furar segue junto
                    _br = lambda x: ('%.0f' % x) if x == int(x) else ('%.2f' % x).replace('.', ',')
                    _of = f'⏩ Pula a espera por R$ {_br(precos_bar["pular_espera"]["preco"] + _taxa_pix_bar(b, "pular_espera"))}'
                    if _fl and _fl.get('preco', 0) > 0:
                        _of += f' ou ⚡ fura a fila por R$ {_br(_fl["preco"] + _taxa_pix_bar(b, "flash"))}'
                    erro = f'⏳ Tua próxima música grátis libera em {_espera} min. Quer tocar antes? {_of}.'
                elif _fl and _fl.get('preco', 0) > 0:
                    _tot = _fl['preco'] + _taxa_pix_bar(b, 'flash')
                    _tot_txt = ('%.0f' % _tot) if _tot == int(_tot) else ('%.2f' % _tot).replace('.', ',')
                    erro = (f'⏳ Tua próxima música grátis libera em {_espera} min. '
                            f'Quer tocar antes? ⚡ Fura a fila por R$ {_tot_txt}: passa na frente das músicas grátis.')
                else:
                    erro = f'⏳ Tua próxima música grátis libera em {_espera} min.'
                erro_tipo = 'gratis_espera'   # leigo A4: a tela troca o "tentar de novo" pelo caminho do furar a fila
            elif youtube_id and (_erro_yt := _validar_video_pedido(b, youtube_id, _info_yt := {})):
                erro = _erro_yt
            elif youtube_id and _tem_politica(_info_yt.get('titulo') or titulo_pedido, _janela_eleitoral()):
                erro, erro_tipo = MSG_POLITICA_MUSICA, 'politica_musica'   # 18/10/26: jingle não toca (acervo ou YouTube)
            else:
                # Título = o do acervo/oEmbed (15/10/26); o do formulário só se o YouTube não respondeu.
                # Sem vídeo não há título (antes um pedido 'musica' levava texto livre pro ranking da TV).
                if not youtube_id:
                    titulo_pedido = ''
                elif _info_yt.get('titulo'):
                    titulo_pedido = _info_yt['titulo']
                elif _tem_palavrao(titulo_pedido):
                    titulo_pedido = ''   # título do formulário (YouTube fora) com palavrão: a TV fica sem título
                if remetente:
                    mensagem = (f'{mensagem} · de {remetente}' if mensagem else f'de {remetente}')
                preco = precos_bar[tipo]['preco']
                if tipo == 'gorjeta':
                    # valor escolhido pelo cliente (5/10/20/50); happy hour não desconta gorjeta
                    try:    _vg = float(request.form.get('valor_gorjeta') or 0)
                    except (TypeError, ValueError): _vg = 0.0
                    preco = _vg if _vg in GORJETA_VALORES else max(GORJETA_VALORES[0], float(TIPOS_PEDIDO['gorjeta']['preco']))
                    if not mensagem:
                        mensagem = f'Mandou R$ {preco:.0f} pra equipe. Obrigado! 🙏'
                # Asaas é o PADRÃO de pagamento: se está configurado, TODO pedido
                # passa por ele (cobrança automática + confirmação sozinha). O modo
                # manual (chave PIX do próprio bar) fica só como fallback p/ quem tem.
                # Lote C (16/10/26): com a Efí ligada pro bar, o gateway é a Efí (nunca a chave do bar).
                # Ponte Efí (produção 16/10/26): idem, pela conta Efí do PubShow (17/10/26: a mesma do split).
                _gateway = bool(os.environ.get('ASAAS_API_KEY')) or _efi or _ponte
                usar_pix = _gateway or (bool(b['requer_pix']) and bool(b['pix_key']))
                if preco <= 0:
                    usar_pix = False   # pedido GRÁTIS: entra direto na fila, sem cobrança
                tipo_nome  = precos_bar[tipo]['nome']
                tipo_emoji = precos_bar[tipo]['emoji']
                # taxa de serviço que o cliente paga a mais (a da Efí vem do pubshow_pay: a mesma env de hoje)
                _taxa_cli = (pubshow_pay.conv_cent_para(tipo) / 100) if _efi else PIX_TAXA_CONVENIENCIA

                # 16/10/26 (C2): o MESMO pedido com PIX ainda aberto (recarregou a página, voltou do banco,
                # tocou 2x) reabre o PIX que já existe — antes, depois de 60 s, criava a 2ª cobrança no Asaas
                # e a tela passava a esperar a 2ª, enquanto o cliente tinha pago a 1ª.
                if usar_pix:
                    _cx = get_pubshow_db()
                    _ab = _cx.execute(
                        """SELECT * FROM pubshow_pedidos
                           WHERE business_id=? AND ip_cliente=? AND tipo=?
                           AND COALESCE(youtube_id,'')=COALESCE(?,'') AND COALESCE(nome_cliente,'')=?
                           AND COALESCE(mensagem,'')=? AND status='aguardando_pix' AND COALESCE(pix_payload,'')<>''
                           AND created_at >= datetime('now', ?)
                           ORDER BY id DESC LIMIT 1""",
                        (b['id'], ip_cliente, tipo, youtube_id or None, nome_cliente, mensagem,
                         '-30 minutes' if _cid else '-60 seconds')
                    ).fetchone()
                    _cx.close()
                    if (_ab and _ab['via_gateway'] and _ab['efi_txid'] is None and not _ab['asaas_payment_id']
                            and not _efi_pix_vivo(_ab['id'])):
                        # Lote C: o QR Efí do split vale 15 min — vencido/cancelado não reabre, sai um novo.
                        # 17/10/26 (caminho único): decide pelo PEDIDO (cobrança do split), não pela rota de agora:
                        # o bar que acabou de trocar ponte→split reabre o PIX da ponte que ainda vale (abaixo)
                        _ab = None
                    if _ab and _ab['efi_txid'] is not None and not _ponte_pix_vivo(_ab):
                        _ab = None   # ponte Efí: o QR vale 15 min — vencido não reabre, sai um novo
                    if _ab and (_ab['valor_cobrado'] is None or not _gateway or
                                abs(float(_ab['valor_cobrado']) - round(preco + _taxa_cli, 2)) < 0.005):
                        pix_pendente = _pix_pendente_de_pedido(b, _ab, precos_bar)

                # Tetos anti-flood (15/10/26): PIX abertos por bar/celular, fila, 1 grátis por celular
                erro = '' if pix_pendente else _teto_pedidos(b, tipo, usar_pix, _cid)
                if usar_pix and _efi and not erro and not pix_pendente and not _efi_conta_ok(b):
                    # Lote C: Efí ligada e o bar SEM conta Efí conectada → sem momento pago (o dinheiro dele nunca
                    # cai na conta do PubShow). A música grátis segue normal; o painel avisa o dono (passo 4).
                    erro = MSG_EFI_SEM_CONTA

                if usar_pix and not erro and not pix_pendente:
                    # ── Anti-spam: não cria pedido duplicado em menos de 60s ────────
                    conn_dedup = get_pubshow_db()
                    _dup = conn_dedup.execute(
                        """SELECT id FROM pubshow_pedidos
                           WHERE business_id=? AND ip_cliente=? AND tipo=?
                           AND COALESCE(youtube_id,'')=COALESCE(?,'')
                           AND status IN ('aguardando_pix','pendente')
                           AND created_at >= datetime('now','-60 seconds')
                           LIMIT 1""",
                        (b['id'], ip_cliente, tipo, youtube_id or None)
                    ).fetchone()
                    conn_dedup.close()
                    if _dup:
                        erro = 'Pedido já enviado! Aguarde um momento antes de tentar novamente.'

                if usar_pix and not erro and not pix_pendente:
                    # ── Incremento de centavos por pedido — facilita identificação no extrato ─
                    conn_off = get_pubshow_db()
                    _offset = conn_off.execute(
                        "SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND date(created_at,'-3 hours')=date('now','-3 hours')",
                        (b['id'],)
                    ).fetchone()[0]
                    conn_off.close()
                    # centavos de identificação só no PIX manual — no Asaas o payment_id identifica e o cliente vê o preço cheio
                    # (na Efí, split ou ponte, o txid identifica: preço cheio também)
                    preco_final = preco if _gateway else round(preco + _offset * 0.01, 2)

                    # ── Cria pedido como aguardando_pix (sem asaas_payment_id ainda) ─
                    # via_gateway=1 já no INSERT (15/10/26): na janela até o Asaas responder, o
                    # "Já paguei" não consegue pôr o pedido na fila sem PIX.
                    # Ponte Efí: efi_txid nasce '' (não NULL) → o "Já paguei" sem cobrança nunca solta este pedido
                    txid = f'P{b["id"]}T{int(datetime.now().timestamp())}'
                    conn2 = get_pubshow_db()
                    cur = conn2.execute(
                        '''INSERT INTO pubshow_pedidos
                           (business_id, tipo, nome_cliente, mensagem, categoria, status, valor,
                            youtube_id, titulo_pedido, thumb_url, ip_cliente, pix_txid, via_gateway, efi_txid)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (b['id'], tipo, nome_cliente, mensagem, categoria,
                         'aguardando_pix', preco_final,
                         youtube_id or None, titulo_pedido or None, thumb_url or None,
                         ip_cliente, txid, 1 if _gateway else 0, '' if _ponte else None)
                    )
                    pedido_id = cur.lastrowid
                    conn2.commit(); conn2.close()

                    # ── Tenta Asaas primeiro (confirmação automática) ──────────
                    pix_qr = ''      # inicializa antes do if/else (Bug #1)
                    pix_payload = '' # evita NameError se algum ramo falhar
                    descricao_pedido = tipo_nome
                    if titulo_pedido:
                        descricao_pedido += f' — {titulo_pedido[:40]}'
                    # Taxa de conveniência: só no caminho Asaas (que tem custo de
                    # transação). O cliente paga +R$1 e o bar recebe o valor cheio
                    # do pedido. No PIX manual (chave do próprio bar) não há taxa.
                    _valor_cobranca = round(preco_final + PIX_TAXA_CONVENIENCIA, 2)
                    _taxa_tela = PIX_TAXA_CONVENIENCIA
                    _efi_ok = False
                    # Ponte Efí (produção 16/10/26): cobra SÓ pela Efí (nada de Asaas, nada de chave do bar se ela falhar)
                    efi_data   = (_efi_criar_cobranca_jukebox(pedido_id, _valor_cobranca, f'Jukebox — {descricao_pedido} — {b["nome"]}')
                                  if _ponte else None)
                    asaas_data = None if (_efi or _ponte) else _asaas_criar_cobranca_pix_jukebox(b, pedido_id, _valor_cobranca, descricao_pedido)

                    if _efi:
                        # Lote C (16/10/26): cobrança Efí com txid nosso (15 min) + split — a parte do bar cai na conta
                        # Efí dele na hora; a taxa de serviço e a mensalidade ficam com o PubShow (pubshow_pay)
                        try:
                            _cp = _pp_conn()
                            try:
                                # VCa-01/02 (16/10/26): a mensalidade nunca sai pelos 2 lados nem de quem cancelou:
                                # assinatura ativa no Asaas (migração) ou plano_ativo=0 (mês pago, ativo_ate) → 0
                                _sem_mens = (not b['plano_ativo']) or _tem_assinatura_ativa(b['id'])
                                pay = pubshow_pay.criar_cobranca_momento(_cp, b, pedido_id, int(round(preco_final * 100)),
                                                                         None, tipo, _asaas_descricao(descricao_pedido),   # DIN-2: sem emoji de 4 bytes
                                                                         sem_mensalidade=_sem_mens)
                            finally:
                                _cp.close()
                        except Exception as _ex_pay:
                            log.error('[PUBSHOW] Efí: cobrança do pedido #%s falhou: %s', pedido_id, _ex_pay, exc_info=True)
                            pay = {'erro': 'excecao'}
                        if pay.get('ok') and pay.get('copia_cola'):
                            _efi_ok = True
                            pix_payload = pay['copia_cola']
                            pix_qr = _pix_qr_b64(pix_payload)
                            _valor_cobranca = pay['valor_cent'] / 100
                            _taxa_tela = pay['conv_cent'] / 100
                            conn3 = get_pubshow_db()
                            conn3.execute(
                                'UPDATE pubshow_pedidos SET pix_txid=?, pix_payload=?, valor=?, valor_cobrado=? WHERE id=?',
                                (pay['txid'], pix_payload, preco_final, _valor_cobranca, pedido_id)
                            )
                            conn3.commit(); conn3.close()
                        else:
                            if pay.get('ok'):
                                _efi_estornar(b, pedido_id, 'sem copia e cola')   # raro (hipótese 7): a cobrança não fica viva
                            # igual ao "gateway falhou" (A1.6): apaga o pedido, nada de QR quebrado nem chave do bar
                            conn3 = get_pubshow_db()
                            conn3.execute("DELETE FROM pubshow_pedidos WHERE id=? AND status='aguardando_pix'", (pedido_id,))
                            conn3.commit(); conn3.close()
                            erro = (MSG_EFI_SEM_CONTA if pay.get('erro') == 'bar_sem_conta_efi'
                                    else 'Pagamento indisponível agora, tenta de novo em instantes.')
                    elif efi_data:
                        # Ponte Efí gerou a cobrança: guarda o txid (coluna própria), o copia e cola, o valor do
                        # bar = preço do item (mesma regra do Asaas) e o total cobrado; QR feito aqui no servidor.
                        conn3 = get_pubshow_db()
                        conn3.execute(
                            'UPDATE pubshow_pedidos SET efi_txid=?, pix_payload=?, valor=?, valor_cobrado=? WHERE id=?',
                            (efi_data['txid'], efi_data['copia_cola'], preco_final, _valor_cobranca, pedido_id)
                        )
                        conn3.commit(); conn3.close()
                        pix_payload = efi_data['copia_cola']
                        pix_qr      = _pix_qr_b64(pix_payload)
                    elif _ponte:
                        # Ponte Efí falhou: apaga o pedido e pede pra tentar de novo (o erro já foi pro log)
                        conn3 = get_pubshow_db()
                        conn3.execute("DELETE FROM pubshow_pedidos WHERE id=? AND status='aguardando_pix'", (pedido_id,))
                        conn3.commit(); conn3.close()
                        erro = 'Pagamento indisponível agora, tenta de novo em instantes.'
                    elif asaas_data:
                        # Asaas gerou QR — salva payment_id e usa payload do Asaas.
                        # Grava o valor LÍQUIDO do bar: cliente paga item + conveniência,
                        # o Asaas desconta a taxa dele, e o bar recebe o que sobra.
                        # Com os defaults (conveniência = taxa Asaas), o líquido = item cheio.
                        _valor_liquido_bar = round(preco_final + PIX_TAXA_CONVENIENCIA - PIX_TAXA_ASAAS, 2)
                        conn3 = get_pubshow_db()
                        conn3.execute(
                            'UPDATE pubshow_pedidos SET asaas_payment_id=?, pix_payload=?, valor=?, valor_cobrado=? WHERE id=?',
                            (asaas_data['payment_id'], asaas_data['payload'], _valor_liquido_bar, _valor_cobranca, pedido_id)
                        )
                        conn3.commit(); conn3.close()
                        pix_qr    = asaas_data['qr_b64']
                        pix_payload = asaas_data['payload']
                    elif b['pix_key'] and not _gateway:
                        # PIX manual (chave própria do bar — confirmar no painel): SÓ sem gateway configurado.
                        # Com gateway falhando, nunca cai na chave do bar (15/10/26): o dinheiro sairia do
                        # sistema e o "Já paguei" viraria momento de graça.
                        pix_payload = _pix_emv(
                            b['pix_key'], b['pix_tipo'] or 'telefone',
                            b['pix_nome_recebedor'] or b['nome'],
                            preco_final, txid
                        )
                        pix_qr = _pix_qr_b64(pix_payload)
                        conn3 = get_pubshow_db()
                        conn3.execute(
                            'UPDATE pubshow_pedidos SET pix_payload=? WHERE id=?',
                            (pix_payload, pedido_id)
                        )
                        conn3.commit(); conn3.close()
                    else:
                        # Gateway falhou (ou sem gateway e sem chave PIX manual) → não dá pra
                        # cobrar. Cancela o pedido e mostra erro (nada de QR quebrado).
                        conn3 = get_pubshow_db()
                        conn3.execute("DELETE FROM pubshow_pedidos WHERE id=? AND status='aguardando_pix'", (pedido_id,))
                        conn3.commit(); conn3.close()
                        erro = 'Pagamento indisponível agora, tenta de novo em instantes.'

                    if not erro:
                        # ── Notificação WhatsApp + Push em background (não bloqueia resposta) ──
                        _hh_txt = ' 🎉 Happy Hour!' if (precos_bar.get(tipo) or {}).get('happy_hour') else ''   # só se ESTE item teve desconto (15/10/26)
                        _bid, _emoji, _tnome, _nome, _preco = b['id'], tipo_emoji, tipo_nome, nome_cliente, preco_final
                        _via_asaas = bool(asaas_data or efi_data) or _efi_ok   # confirma sozinho (Asaas, ponte ou split)
                        _bd_copy = dict(b)
                        def _notif_pix():
                            if _via_asaas:
                                # Confirmação automática — o dono não precisa fazer nada
                                _msg = f'🎵 *Novo pedido no Jukebox!*{_hh_txt}\n{_emoji} {_tnome}\n👤 {_nome}\n💰 R$ {_preco:.2f}\n✅ Confirma sozinho assim que o PIX cair.'
                            else:
                                # PIX manual — o dono precisa confirmar no painel
                                _msg = f'🎵 *Novo pedido PIX aguardando!*{_hh_txt}\n{_emoji} {_tnome}\n👤 {_nome}\n💰 R$ {_preco:.2f}\n📋 Acesse o painel para confirmar.'
                            try: _pubshow_notify_bar(_bd_copy, _msg)
                            except: pass
                            try: _enviar_push_pedido(_bid, _emoji, _tnome, _nome, _preco)
                            except: pass
                        _threading.Thread(target=_notif_pix, daemon=True).start()

                        pix_pendente = {
                            'pedido_id':  pedido_id,
                            'payload':    pix_payload,
                            'qr_b64':     pix_qr,
                            # valor = total que o cliente paga (com taxa, se via Asaas ou Efí)
                            'valor':      _valor_cobranca if _via_asaas else preco_final,
                            'valor_item': preco_final,
                            'taxa_conv':  _taxa_tela if _via_asaas else 0.0,
                            # ponte Efí: o nome da conta recebedora não é o do bar — a tela não mostra "Recebedor"
                            'recebedor':  '' if efi_data else (b['pix_nome_recebedor'] or b['nome']),
                            'tipo_nome':  tipo_nome,
                            'tipo_emoji': tipo_emoji,
                            'tipo':       tipo,   # a tela do PIX troca o texto por tipo quando paga (16/10/26)
                            'via_asaas':  _via_asaas,       # = gateway automático (Asaas, ponte ou split): sem "Já paguei"
                            'via_efi':    bool(efi_data),   # ponte Efí
                            # Lote C, fatia b (16/10/26): tela da Efí ("vale 15 min", "confirma sozinho em segundos")
                            **(dict(_efi_validade(pay.get('expira_em')), efi=True) if _efi_ok else {}),
                        }
                        sucesso = None
                elif not erro and not usar_pix:
                    # ── Fluxo direto (sem PIX ou PIX não exigido) ─────────────
                    # Anti-spam: bloqueia duplicata em 60s
                    conn_dedup2 = get_pubshow_db()
                    _dup2 = conn_dedup2.execute(
                        """SELECT id FROM pubshow_pedidos
                           WHERE business_id=? AND ip_cliente=? AND tipo=?
                           AND COALESCE(youtube_id,'')=COALESCE(?,'')
                           AND status IN ('pendente','exibido')
                           AND created_at >= datetime('now','-60 seconds')
                           LIMIT 1""",
                        (b['id'], ip_cliente, tipo, youtube_id or None)
                    ).fetchone()
                    conn_dedup2.close()
                    if _dup2:
                        erro = 'Pedido já enviado! Aguarde antes de tentar novamente.'

                if not usar_pix and not erro:
                    # Aplica offset de centavos se bar tem PIX configurado (manual)
                    if b.get('pix_key'):
                        conn_off2 = get_pubshow_db()
                        _offset2 = conn_off2.execute(
                            "SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND date(created_at,'-3 hours')=date('now','-3 hours')",
                            (b['id'],)
                        ).fetchone()[0]
                        conn_off2.close()
                        preco_direto = round(preco + _offset2 * 0.01, 2)
                    else:
                        preco_direto = preco
                    if preco <= 0:
                        preco_direto = 0.0   # grátis não leva centavos de identificação

                    conn2 = get_pubshow_db()
                    _cur2 = conn2.execute(
                        '''INSERT INTO pubshow_pedidos
                           (business_id, tipo, nome_cliente, mensagem, categoria, status, valor,
                            youtube_id, titulo_pedido, thumb_url, ip_cliente)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        (b['id'], tipo, nome_cliente, mensagem, categoria, ('aguardando_ok' if _precisa_ok(b, tipo) else 'pendente'), preco_direto,
                         youtube_id or None, titulo_pedido or None, thumb_url or None, ip_cliente)
                    )
                    # BUG antigo (corrigido 11/09/26): Connection não tem lastrowid — todo pedido
                    # sem PIX dava 500 e deixava a transação aberta (database is locked).
                    pedido_id_sucesso = _cur2.lastrowid
                    conn2.commit(); conn2.close()
                    sucesso = tipo

                    # ── Notificação WhatsApp + Push em background (não bloqueia resposta) ──
                    _hh_txt2 = ' 🎉 Happy Hour!' if (precos_bar.get(tipo) or {}).get('happy_hour') else ''
                    _bid2, _emoji2, _tnome2, _nome2, _preco2 = b['id'], tipo_emoji, tipo_nome, nome_cliente, preco_direto
                    _bd_copy2 = dict(b)
                    _msg_extra = (f'\n📝 {mensagem}' if mensagem else '') + (f'\n🎶 {titulo_pedido}' if titulo_pedido else '')
                    def _notif_direto():
                        try: _pubshow_notify_bar(_bd_copy2, f'🎵 *Novo pedido no Jukebox!*{_hh_txt2}\n{_emoji2} {_tnome2}\n👤 {_nome2}{_msg_extra}\n💰 R$ {_preco2:.2f}')
                        except: pass
                        try: _enviar_push_pedido(_bid2, _emoji2, _tnome2, _nome2, _preco2)
                        except: pass
                    _threading.Thread(target=_notif_direto, daemon=True).start()

    # Fila atual (últimos 5 pedidos pendentes)
    conn3 = get_pubshow_db()
    fila = conn3.execute(   # crítico leigo A2 (17/10/26): a ordem da TV (vip, furar a fila, chegada)
        '''SELECT * FROM pubshow_pedidos
           WHERE business_id=? AND status="pendente"
           ORDER BY CASE tipo WHEN "vip" THEN 1 WHEN "flash" THEN 2 ELSE 3 END, created_at ASC, id ASC LIMIT 10''',
        (b['id'],)
    ).fetchall()
    canal        = CANAIS.get(b['canal_atual'], CANAIS['rock'])
    try:
        import json as _jsn
        _gj = _jsn.loads(b.get('generos_jukebox') or 'null')
    except Exception:
        _gj = None
    _curta = f'AND COALESCE(duracao_seg, 0) <= {int(YT_DUR_MAX_SEG)}'   # 17/10/26: show longo não conta (não dá pra pedir)
    if _gj:
        _ph = ','.join('?' * len(_gj))
        total_videos = conn3.execute(f'SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1 {_curta} AND categoria IN ({_ph})', tuple(_gj)).fetchone()[0]
    else:
        total_videos = conn3.execute(f'SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1 {_curta}').fetchone()[0]

    # PIX cent offset — cada pedido do dia recebe +N centavos para identificação no extrato
    # Ex: 1º pedido = R$5,00 / 2º = R$5,01 / 3º = R$5,02 ...
    pix_offset = 0
    if b.get('pix_key') and not _gw:
        try:
            pix_offset = conn3.execute(
                "SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND date(created_at,'-3 hours')=date('now','-3 hours')",
                (b['id'],)
            ).fetchone()[0]
        except Exception:
            pix_offset = 0

    conn3.close()

    # Fechado por horário: "Volta às {hora}" (16/10/26, A4.3)
    volta_as = ''
    if not aberto and (motivo_fechado or '').startswith('Jukebox disponível das'):
        volta_as = b.get('jukebox_hora_ini') or ''

    return render_template('pubshow/jukebox.html',
                           b=b, canal=canal,
                           volta_as=volta_as,
                           pix_titular=os.environ.get('PUBSHOW_PIX_TITULAR', '').strip(),
                           # só os tipos que o bar NÃO bloqueou (antes o card aparecia e o POST dava 'Tipo de pedido inválido')
                           tipos={k: v for k, v in precos_bar.items() if k in tipos_bar},
                           tipos_disponiveis=tipos_bar,
                           hh_desconto=hh_desconto,
                           fila=[dict(f) for f in fila],
                           sucesso=sucesso, erro=erro,
                           pix_pendente=pix_pendente,
                           total_videos=total_videos,
                           # 20/10/26: filtros do cliente com nome simples ("Rock", não "Rock Clips")
                           canais_nomes={k: {'nome': re.sub(r'\s+Clips$', '', v['nome']),
                                             'emoji': ESTILOS_BAR.get(k, v)['emoji']}   # 🌴 Reggae (o canal usa 🎸)
                                         for k, v in CANAIS.items()},
                           selo_estilos=_selo_estilos(b),   # 20/10/26: "🎸 Rock · 🌴 Reggae" em vez de "Variado"
                           youtube_liberado=_youtube_liberado(b),
                           aberto=aberto, motivo_fechado=motivo_fechado,
                           aviso=aviso, token=token,
                           pix_offset=pix_offset,
                           # asaas_ativo = "PIX por gateway" (Asaas, ponte Efí ou split): QR automático + taxa no total
                           asaas_ativo=_gw,
                           pix_conv=_taxa_pix_bar(b, 'flash'),
                           # VDB-03 (16/10/26): o R$ 1 do app da GORJETA (Efí com PUBSHOW_GORJETA_CONV=0 = R$ 0, o PIX
                           # cobra só a gorjeta); no Asaas é o mesmo do momento, como hoje
                           pix_conv_gorjeta=_taxa_pix_bar(b, 'gorjeta'),
                           # Efí ligada e o bar sem a conta Efí conectada: momentos pagos indisponíveis (só música grátis)
                           efi_sem_conta=(_efi and not _efi_conta_ok(b)),
                           # o celular só lembra o nome que o servidor ACEITOU (16/10/26, V4-02); no parabéns, o
                           # de quem manda (C2); na reabertura por GET, nada muda
                           nome_aceito=(_nome_lembrar or '') if (sucesso or pix_pendente) else '',
                           pix_url=(f'/pubshow/jukebox/{b["jukebox_token"] or b["code"]}/pix/{pix_pendente["pedido_id"]}'
                                    if pix_pendente else ''),
                           erro_tipo=erro_tipo, gratis_min=GRATIS_INTERVALO_MIN,   # crítico leigo A4/M3/M4
                           eleicao=_janela_eleitoral(),   # 18/10/26: aviso "o bar confere antes da TV" nos momentos
                           pedido_id_sucesso=locals().get('pedido_id_sucesso')), status_http


@pubshow_bp.route('/jukebox/<token>/ja-paguei/<int:pedido_id>', methods=['POST'])
def jukebox_ja_paguei(token, pedido_id):
    """Cliente avisa que pagou o PIX.
    Se o bar tem 'requer_pix=1' (confirmação manual ativa):
      → pedido fica em 'aguardando_pix', aguarda bar confirmar no painel
      → retorna ok=True com modo='aguardando_bar'
    Se requer_pix=0 (trust-based):
      → entra direto na fila (comportamento original)
    """
    conn = get_pubshow_db()
    b = conn.execute(
        'SELECT id, requer_pix FROM pubshow_businesses WHERE jukebox_token=? OR code=?',
        (token, token)
    ).fetchone()
    if not b:
        conn.close()
        return jsonify({'ok': False, 'error': 'Bar não encontrado'}), 404

    # Pedido de GATEWAY (15/10/26): só confirma pelo gateway (webhook / admin "PIX caiu").
    # "Já paguei" nunca muda o status dele — nem na janela antes do Asaas responder.
    ped = conn.execute(
        'SELECT status, asaas_payment_id, via_gateway FROM pubshow_pedidos WHERE id=? AND business_id=?',
        (pedido_id, b['id'])
    ).fetchone()
    if ped and (ped['asaas_payment_id'] or ped['via_gateway']):
        conn.close()
        if ped['status'] in ('pendente', 'exibido'):
            return jsonify({'ok': True, 'modo': 'direto'})
        return jsonify({'ok': False, 'modo': 'gateway',
                        'error': 'Esse PIX confirma sozinho assim que cair. Espera uns segundinhos.'})

    # Verifica se o bar exige confirmação manual de PIX
    if b['requer_pix']:
        # Não move para 'pendente' — bar precisa confirmar no painel
        # Apenas registra que o cliente avisou que pagou
        conn.execute(
            """UPDATE pubshow_pedidos SET pix_cliente_avisou=1
               WHERE id=? AND business_id=? AND status='aguardando_pix'""",
            (pedido_id, b['id'])
        )
        conn.commit(); conn.close()
        return jsonify({'ok': True, 'modo': 'aguardando_bar',
                        'msg': 'Aviso enviado! O bar vai confirmar seu pagamento em instantes.'})

    # Trust-based: sem exigência de PIX — entra direto na fila
    updated = conn.execute(
        """UPDATE pubshow_pedidos SET status=?
           WHERE id=? AND business_id=? AND status='aguardando_pix' AND asaas_payment_id IS NULL
           AND COALESCE(via_gateway,0)=0
           AND efi_txid IS NULL""",   # pedido da ponte Efí só sai pela consulta (pago de verdade)
        (_status_apos_pix(conn, pedido_id), pedido_id, b['id'])
    ).rowcount
    if updated:
        conn.commit(); conn.close()
        return jsonify({'ok': True, 'modo': 'direto'})
    # Não estava mais 'aguardando_pix' — pode já ter sido confirmado (duplo
    # clique, webhook Asaas, bar confirmou). Se o pedido existe e já está na
    # fila/exibido, é sucesso de verdade — não mostra "erro" pro cliente.
    ja = conn.execute(
        'SELECT status FROM pubshow_pedidos WHERE id=? AND business_id=?',
        (pedido_id, b['id'])
    ).fetchone()
    conn.commit(); conn.close()
    if ja and ja['status'] in ('pendente', 'exibido'):
        return jsonify({'ok': True, 'modo': 'direto'})
    return jsonify({'ok': False, 'error': 'Pedido não encontrado ou já confirmado'})


# ── API (usada pelo TV player via JS polling) ─────────────────────────────────

# Campos do pedido que a TV usa (tv.html, conferido 15/10/26). Antes ia a linha inteira
# (SELECT *): IP do cliente, payload/txid do PIX, id do Asaas, valor — dado pessoal à toa.
_CAMPOS_PEDIDO_TV = ('id', 'tipo', 'nome_cliente', 'mensagem', 'youtube_id', 'titulo_pedido')


def _pedido_para_tv(row):
    if not row:
        return None
    d = {k: row[k] for k in _CAMPOS_PEDIDO_TV}
    if (d['tipo'] == 'musica' or d['tipo'] in TIPOS_MUSICA_FILA) and _dia_da_votacao():
        d['nome_cliente'] = ''   # 18/10/26 (C1): no domingo o nome livre da música não vai pra TV (ela mostra "alguém")
    return d


# ── Sinal da TV + tutorial do dono (15/10/26, B6) ──────────────────────────────
_TV_GRAVA_A_CADA_S = 60      # /api/status chega a cada ~3 s; o banco só é escrito 1x por minuto
_TV_SEM_SINAL_MIN = 5        # depois disso o painel mostra "sem sinal há X min"
_FMT_UTC = '%Y-%m-%d %H:%M:%S'


def _marcar_tv_vista(conn, b, som=None):
    """Grava o sinal REAL da TV (tv_visto_em, UTC) no máximo 1x por minuto. Nunca derruba a TV.
    som (16/10/26, V-S5-02): 1/0 que a TV manda (?som=); mudou → grava na hora; None = TV antiga, não mexe."""
    try:
        agora = datetime.utcnow()
        visto = b['tv_visto_em'] if 'tv_visto_em' in b.keys() else None
        som_mudou = som is not None and 'tv_som' in b.keys() and b['tv_som'] != som
        if not som_mudou and visto and visto > (agora - timedelta(seconds=_TV_GRAVA_A_CADA_S)).strftime(_FMT_UTC):
            return
        if som is None:
            conn.execute('UPDATE pubshow_businesses SET tv_visto_em=? WHERE id=?', (agora.strftime(_FMT_UTC), b['id']))
        else:
            conn.execute('UPDATE pubshow_businesses SET tv_visto_em=?, tv_som=? WHERE id=?',
                         (agora.strftime(_FMT_UTC), som, b['id']))
        conn.commit()
    except Exception as ex:
        log.warning('[PUBSHOW] tv_visto_em não gravou (bar %s): %s', b['id'], ex)


def _tempo_txt(minutos):
    if minutos < 120:
        return f'{minutos} min'
    if minutos < 48 * 60:
        return f'{minutos // 60} h'
    return f'{minutos // 1440} dias'


def _tv_sinal(bd):
    """Selo do painel: {'estado': 'nunca'|'ligada'|'sem_som'|'sem_sinal', 'min', 'texto'} — o texto sai daqui
    (o HTML do painel e o polling usam o mesmo)."""
    visto = (bd.get('tv_visto_em') or '')[:19]
    minutos = None
    if visto:
        try:
            minutos = int((datetime.utcnow() - datetime.strptime(visto, _FMT_UTC)).total_seconds() // 60)
        except Exception:
            minutos = None
    if minutos is None:
        return {'estado': 'nunca', 'min': None, 'texto': '📺 A TV ainda não foi ligada'}
    if minutos < _TV_SEM_SINAL_MIN:
        # 16/10/26 (V-S5-02): TV muda não mostra pedido nenhum (a fila espera) → o gerente precisa saber
        if bd.get('tv_som') == 0:
            return {'estado': 'sem_som', 'min': max(0, minutos),
                    'texto': '⚠️ TV sem som: toque nela. Os pedidos das mesas esperam até o som voltar.'}
        return {'estado': 'ligada', 'min': max(0, minutos), 'texto': '📺 TV ligada'}
    return {'estado': 'sem_sinal', 'min': minutos,
            'texto': f'⚠️ A TV parou de responder há {_tempo_txt(minutos)}. Confira se ela está ligada e na internet.'}


def _tutorial_do_bar(conn, bd, total_pedidos):
    """Checklist do topo da aba Hoje. Passos marcam por SINAL (nunca por clique): TV que respondeu,
    QR aberto/impresso, 1º pedido. O 4º (conta pra receber) só existe pros bares da Efí.
    Completo → grava tutorial_ok_em e some de vez. Devolve None quando não deve aparecer."""
    # VCa-03 (16/10/26): a mesma regra POR BAR do resto da integração (o code no PUBSHOW_EFI_BARES).
    # Ligar a Efí pro bar demo não pode dar o passo 4 pro Garage, que segue no Asaas.
    # 17/10/26 (caminho único): o bar da PONTE também vê o passo 4 — é por ele que vira split.
    gateway_efi = _efi_lista(bd)
    if bd.get('tutorial_ok_em') and not gateway_efi:
        return None
    conta = False
    if gateway_efi:
        try:
            pubshow_pay.init_tables(conn)
            # 17/10/26 (caminho único): o passo 4 só fica pronto com a conta ACEITA pela Efí — salva sem a Efí
            # responder, o bar segue na ponte e o formulário fica aberto pra salvar de novo (a Efí confere de novo)
            conta = bool(pubshow_pay.conta_aceita(conn, bd['id']))
        except Exception as ex:
            log.warning('[PUBSHOW] tutorial: conta Efí do bar %s não lida: %s', bd['id'], ex)
    if bd.get('tutorial_ok_em'):
        # 16/10/26 (V3-07): com o Efí ligado, quem já tinha concluído (ou veio do backfill, ex.: Garage)
        # mas ainda não disse onde o dinheiro cai vê só o passo 4 pendente
        if conta:
            return None
        return {'tv': True, 'qr': True, 'pedido': True, 'conta': False, 'feitos': 3, 'total': 4}
    passos = {'tv': bool(bd.get('tv_visto_em')), 'qr': bool(bd.get('qr_aberto_em')),
              'pedido': (total_pedidos or 0) > 0}
    if gateway_efi:
        passos['conta'] = conta
    if all(passos.values()):
        try:
            conn.execute('UPDATE pubshow_businesses SET tutorial_ok_em=? WHERE id=? AND tutorial_ok_em IS NULL',
                         (datetime.utcnow().strftime(_FMT_UTC), bd['id']))
            conn.commit()
        except Exception as ex:
            log.warning('[PUBSHOW] tutorial_ok_em não gravou (bar %s): %s', bd['id'], ex)
        return None
    passos['feitos'] = sum(1 for k, v in passos.items() if v is True)
    passos['total'] = 4 if 'conta' in passos else 3
    return passos


@pubshow_bp.route('/api/status/<code>')
def api_status(code):
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE code=?', (code,)).fetchone()
    if not b:
        conn.close()
        return jsonify({'error': 'not_found'}), 404
    if request.args.get('teste') != '1':   # 16/10/26 (V3-02): TV aberta no celular pelo "Testar" não é TV ligada
        _som = request.args.get('som')
        _marcar_tv_vista(conn, b, som=int(_som) if _som in ('0', '1') else None)

    if _janela_eleitoral():
        # 18/10/26 (trava eleitoral, M1): recado que entrou na fila ANTES da janela e ainda não passou na TV vai pro OK
        _ini, _mus = _inicio_janela_utc(), ('musica', 'musica_especifica', 'musica_externa', 'flash', 'vip', 'pular_espera')
        _q = (" FROM pubshow_pedidos WHERE business_id=? AND status='pendente' AND created_at < ? "
              "AND tipo NOT IN (?,?,?,?,?,?)")
        if conn.execute('SELECT 1' + _q + ' LIMIT 1', (b['id'], _ini, *_mus)).fetchone():
            conn.execute("UPDATE pubshow_pedidos SET status='aguardando_ok' WHERE id IN (SELECT id" + _q + ')',
                         (b['id'], _ini, *_mus))
            conn.commit()

    # Pedido especial — aparece como overlay na TV (parabéns, dedicatória etc.)
    # Só conta pedidos confirmados (pendente), nunca aguardando_pix
    pedido_especial = conn.execute(
        '''SELECT * FROM pubshow_pedidos
           WHERE business_id=? AND status="pendente"
           AND tipo IN ("parabens","dedicatoria","brinde","chegada","casamento","gorjeta","cantada","salve")
           ORDER BY created_at ASC LIMIT 1''',
        (b['id'],)
    ).fetchone()

    # Pedido de música — espera o vídeo atual acabar
    # VIP → toca na frente de tudo; flash → prioridade; restantes → ordem de chegada
    pedido_musica = conn.execute(
        '''SELECT * FROM pubshow_pedidos
           WHERE business_id=? AND status="pendente"
           AND tipo IN ("vip","flash","musica","musica_especifica","musica_externa","pular_espera")
           ORDER BY
             CASE tipo WHEN "vip" THEN 1 WHEN "flash" THEN 2 ELSE 3 END,
             created_at ASC, id ASC
           LIMIT 1''',
        (b['id'],)
    ).fetchone()

    # Contagem da fila: só pedidos confirmados (exclui aguardando_pix)
    total_fila = conn.execute(
        'SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status="pendente"',
        (b['id'],)
    ).fetchone()[0]

    # Pedidos aguardando PIX: para mostrar badge no painel do bar
    aguardando_pix = conn.execute(
        'SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status="aguardando_pix"',
        (b['id'],)
    ).fetchone()[0]

    # Promoção Relâmpago — verifica se ainda está dentro do prazo
    promo = None
    if b['promo_msg'] and b['promo_expira']:
        if b['promo_expira'] > datetime.now().strftime('%Y-%m-%dT%H:%M:%S'):
            # segundos_restantes (15/10/26): a conta é feita aqui, no relógio do servidor que gravou o
            # promo_expira (sem fuso). A TV lia o texto como hora local e mostrava 3 h a mais.
            try:
                _rest = int((datetime.fromisoformat(b['promo_expira'][:19]) - datetime.now()).total_seconds())
            except Exception:
                _rest = 0
            promo = {
                'msg':    b['promo_msg'],
                'emoji':  b['promo_emoji'] or '🍺',
                'expira': b['promo_expira'],
                'segundos_restantes': max(0, _rest),
            }
        else:
            # Limpeza automática quando expirou
            conn.execute('UPDATE pubshow_businesses SET promo_msg=NULL, promo_expira=NULL WHERE id=?', (b['id'],))
            conn.commit()

    result = {
        'canal_atual':    b['canal_atual'],
        'jukebox_ativo':  bool(b['jukebox_ativo']),
        'pedido':         _pedido_para_tv(pedido_especial),
        'pedido_musica':  _pedido_para_tv(pedido_musica),
        'total_fila':     total_fila,
        'aguardando_pix': aguardando_pix,
        'promo':          promo,
        'skip_seq':       b['skip_seq'] or 0,
        'tv_volume':      b['tv_volume'] if b['tv_volume'] is not None else 100,
        'videos_version': _videos_version(),
        'tv_version':     _tv_version(b),
        'qr_preco':       _qr_preco_tv(b),   # V-S5-06: preço do QR da TV ao vivo (happy hour)
    }
    conn.close()
    if _efi_para(b):
        # Lote D A3 (16/10/26): só no modo Efí (no Asaas o JSON fica igual). A TV aberta recarrega pela tv_version
        # (que muda quando pausa) e a tela de pausa volta sozinha quando 'pausado' vira false
        _pz = _efi_pausa(b)
        result['pausado'] = bool(_pz and _pz['pausado'])
        if result['pausado']:
            result['tv_version'] = result['tv_version'] + ':pausa'
    return jsonify(result)


@pubshow_bp.route('/api/pedido-status/<token>/<int:pedido_id>')
def api_pedido_status(token, pedido_id):
    """Polling do cliente: retorna status + posição na fila + tempo estimado."""
    conn = get_pubshow_db()
    row = conn.execute(
        '''SELECT p.status, p.business_id, p.created_at, p.efi_txid, p.tipo,
                  (p.created_at >= datetime('now','-20 minutes')) AS efi_recente FROM pubshow_pedidos p
           JOIN pubshow_businesses b ON p.business_id = b.id
           WHERE p.id=? AND (b.jukebox_token=? OR b.code=?)''',
        (pedido_id, token, token)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'not_found'}), 404

    status = row['status']
    # Efí (sem webhook no Railway): a própria tela do cliente pergunta se o PIX caiu
    # — no máx. 1x a cada 5 s por pedido, só nos primeiros 20 min (a cobrança expira em 15),
    # esperando no máx. 0,5 s: com 1 worker a TV/painel não podem ficar lentos; se a Efí
    # demorar, a thread confirma sozinha e o próximo polling já vê.
    if status == 'aguardando_pix' and row['efi_txid'] and row['efi_recente']:
        if _efi_conferir_pedido(pedido_id, row['efi_txid'], espera_seg=0.5):
            status = conn.execute('SELECT status FROM pubshow_pedidos WHERE id=?', (pedido_id,)).fetchone()['status']
    posicao = None
    tempo_min = None
    dev_aprov = ''   # crítico leigo A5 (17/10/26): 'pendente' (com o suporte) | 'negada…' — só no split
    pix_efi = None   # Lote C (16/10/26): status da cobrança Efí deste pedido (só com PUBSHOW_GATEWAY=efi)
    pix_graca = False   # VCb-05: venceu agora há pouco, um PIX feito no prazo ainda confirma
    if _efi_ligado():
        try:
            pix_efi, pix_graca = _efi_pix_da_tela(conn, pedido_id, status)
            if pix_efi is not None:
                status = conn.execute('SELECT status FROM pubshow_pedidos WHERE id=?', (pedido_id,)).fetchone()['status']
        except Exception as ex:
            log.warning('[PUBSHOW] Efí: confirmação na tela do pedido #%s falhou: %s', pedido_id, ex)

    if status == 'pendente' and row['tipo'] in TIPOS_MUSICA_FILA + ('musica',):
        # crítico leigo A2 (17/10/26) + busca 2.0 (16/10/26): música = a MESMA ordem da TV (/api/status): vip, furar a
        # fila, depois chegada (empate no mesmo segundo: quem entrou primeiro, pelo id). Os recados (parabéns etc.)
        # aparecem por cima da TV e não ocupam vez de música. O /api/fila-info promete "entra em Nº" com esta conta.
        _pr = {'vip': 1, 'flash': 2}.get(row['tipo'], 3)
        posicao = conn.execute(
            """SELECT COUNT(*) FROM pubshow_pedidos
               WHERE business_id=? AND status='pendente' AND id != ?
                 AND tipo IN ('vip','flash','musica','musica_especifica','musica_externa','pular_espera')
                 AND (CASE tipo WHEN 'vip' THEN 1 WHEN 'flash' THEN 2 ELSE 3 END < ?
                      OR (CASE tipo WHEN 'vip' THEN 1 WHEN 'flash' THEN 2 ELSE 3 END = ?
                          AND (created_at < ? OR (created_at = ? AND id < ?))))""",
            (row['business_id'], pedido_id, _pr, _pr, row['created_at'], row['created_at'], pedido_id)
        ).fetchone()[0] + 1  # posição 1-indexed
    elif status == 'pendente':
        # Conta pedidos confirmados ANTES deste na fila (pela data de criação)
        posicao = conn.execute(
            """SELECT COUNT(*) FROM pubshow_pedidos
               WHERE business_id=? AND status='pendente'
               AND created_at <= (SELECT created_at FROM pubshow_pedidos WHERE id=?)
               AND id != ?""",
            (row['business_id'], pedido_id, pedido_id)
        ).fetchone()[0] + 1  # posição 1-indexed
    if status == 'pendente':

        # Tempo estimado: 4 min por posição + 2 min de overhead do vídeo atual
        # Clips do YouTube duram em média 3-5 min, usando 4 como base
        if posicao == 1:
            tempo_min = None  # 1º = toca quando o atual acabar, sem estimativa fixa
        else:
            tempo_min = (posicao - 1) * 4 + 2  # músicas antes + metade do atual

    conn.close()
    resp = {
        'status': status,
        'posicao': posicao,
        'tempo_min': tempo_min
    }
    if _efi_ligado():
        # Lote C: a tela diz "Teu PIX já foi devolvido" (recusa/atraso) ou "O QR venceu, pede de novo"
        # VCa-07 (16/10/26): "já foi devolvido" só depois da Efí confirmar (DEVOLVIDO); no meio do caminho é
        # pix_devolvendo ("estamos devolvendo")
        resp['pix_devolvido'] = pix_efi == 'devolvida'
        resp['pix_devolvendo'] = pix_efi == 'devolvendo'
        resp['pix_expirou'] = pix_efi == 'expirada'
        resp['pix_graca'] = bool(pix_graca)
        if status == 'recusado':   # crítico leigo A5 (17/10/26): passou do teto → "está com o suporte" / "não aprovada"
            try:
                _cx = get_pubshow_db()
                try:
                    _r = _cx.execute('SELECT dev_aprovacao FROM pubshow_pay_cobrancas WHERE pedido_id=?',
                                     (pedido_id,)).fetchone()
                finally:
                    _cx.close()
                dev_aprov = (_r['dev_aprovacao'] if _r else '') or ''
            except Exception:
                dev_aprov = ''
            resp['pix_devolucao_suporte'] = dev_aprov == 'pendente'
            resp['pix_devolucao_negada'] = dev_aprov.startswith('negada')
    return jsonify(resp)


@pubshow_bp.route('/api/pedido-exibido/<int:pedido_id>', methods=['POST'])
def api_pedido_exibido(pedido_id):
    """Marca pedido como exibido. Valida que a TV (code) é dona do pedido."""
    data = request.get_json(silent=True) or {}
    code = data.get('code') or request.form.get('code', '')
    conn = get_pubshow_db()
    pedido = conn.execute(
        'SELECT id, business_id, youtube_id, titulo_pedido, categoria FROM pubshow_pedidos WHERE id=?', (pedido_id,)
    ).fetchone()
    if not pedido:
        conn.close()
        return jsonify({'error': 'not_found'}), 404
    if not code:  # code é obrigatório — sempre enviado pela TV
        conn.close()
        return jsonify({'error': 'unauthorized'}), 401
    b = conn.execute(
        'SELECT id FROM pubshow_businesses WHERE code=?', (code,)
    ).fetchone()
    if not b or b['id'] != pedido['business_id']:
        conn.close()
        return jsonify({'error': 'unauthorized'}), 403
    # Só pedido 'pendente' vira exibido (15/10/26 junção + 17/10/26 befa911): a TV só recebe pendente (/api/status).
    # Sem isso, um pedido não pago ou recusado virava 'exibido' e contava como pago; dispensado no meio fica dispensado.
    ok = conn.execute(
        "UPDATE pubshow_pedidos SET status='exibido', exibido_at=datetime('now','-3 hours') "
        "WHERE id=? AND business_id=? AND status='pendente'",
        (pedido_id, b['id'])
    ).rowcount == 1
    if not ok:
        conn.close()
        return jsonify({'ok': False, 'error': 'status'}), 409
    # Acervo que cresce com o uso (11/09/26): música do YouTube que TOCOU na TV entra no acervo,
    # na categoria do canal que estava no ar. O cardápio vira o que o público daquele bar pede.
    _yid = (pedido['youtube_id'] or '').strip()
    # 18/10/26 (trava eleitoral): título político não entra no acervo dos outros bares
    if ok and _yid_ok(_yid) and not _tem_politica(pedido['titulo_pedido']):   # 11 letras, igual ao POST (16/10/26, V4-01)
        _cat = SHOWS_FORA.get(pedido['categoria'], pedido['categoria'])   # 17/10/26: pedido feito no show → o clipe
        _cat = _cat if _cat in CANAIS else 'rock'
        _seg = _YT_DURACAO.get(_yid) or 180       # 17/10/26: a duração real que a busca viu (antes: 180 fixo)
        conn.execute(
            'INSERT OR IGNORE INTO pubshow_videos (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, views_milhoes, ativo) '
            'VALUES (?,?,?,?,?,?,0,1)',
            (_yid, (pedido['titulo_pedido'] or _yid)[:120], '', _cat, 'pedido_publico',
             _seg if _seg < _YT_SEG_AO_VIVO else 180)
        )
        if _yid in _YT_DURACAO and _seg < _YT_SEG_AO_VIVO:   # já estava no acervo com os 180 falsos: grava a real
            conn.execute("UPDATE pubshow_videos SET duracao_seg=? WHERE youtube_id=? AND subcategoria='pedido_publico'",
                         (_seg, _yid))
    conn.commit(); conn.close()
    return jsonify({'ok': ok})


@pubshow_bp.route('/api/trocar-canal/<code>', methods=['POST'])
def api_trocar_canal(code):
    # Só a TV (code dela na URL) ou o painel logado do PRÓPRIO bar (15/10/26). Antes aceitava
    # o jukebox_token do QR da mesa: qualquer cliente trocava o canal da TV do bar.
    conn = get_pubshow_db()
    b = conn.execute(
        'SELECT id, plano FROM pubshow_businesses WHERE code=?', (code,)
    ).fetchone()
    conn.close()
    if not b:
        return jsonify({'error': 'não autorizado'}), 403
    if session.get('pub_business_id') and session['pub_business_id'] != b['id']:
        return jsonify({'error': 'não autorizado'}), 403
    data = (request.json or {}) if request.is_json else request.form
    canal = data.get('canal', 'rock')
    # Só canal que o plano do bar libera. (Os temas do bar não entram aqui: o fallback da TV
    # sem vídeos manda 'rock'/'pop' e recarrega — barrar isso faria a TV recarregar sem fim.)
    if canal not in CANAIS or canal not in _plano_canais_permitidos(dict(b)):
        return jsonify({'error': 'canal inválido'}), 400
    conn2 = get_pubshow_db()
    conn2.execute('UPDATE pubshow_businesses SET canal_atual=? WHERE id=?', (canal, b['id']))
    conn2.commit(); conn2.close()
    return jsonify({'ok': True, 'canal': canal})


@pubshow_bp.route('/api/videos/<categoria>')
def api_videos(categoria):
    # ?bar=<code da TV>: tira os vídeos que o bar baniu (15/10/26 — antes o "Nunca mais" não saía da TV)
    bloqueados = temas = None
    _code = request.args.get('bar', '').strip()
    if _code:
        conn = get_pubshow_db()
        _b = conn.execute('SELECT videos_bloqueados, temas_habilitados, canal_atual FROM pubshow_businesses WHERE code=?', (_code,)).fetchone()
        conn.close()
        if _b:
            bloqueados = _videos_bloqueados(_b)
            temas = _temas_do_bar(_b)   # 20/10/26: Variado com vários estilos acesos = mistura só deles
            if _fundo_ativo(_b):        # 20/10/26: esporte no fundo → o YouTube só toca a música
                temas = _so_musica(temas)
    videos = _videos_do_canal(categoria, bloqueados=bloqueados, temas=temas)
    return jsonify({'videos': videos, 'total': len(videos)})


@pubshow_bp.route('/api/ranking/<code>')
def api_ranking(code):
    """Retorna top músicas pedidas nas últimas 6h para o bar (ranking da noite)."""
    conn = get_pubshow_db()
    b = conn.execute('SELECT id FROM pubshow_businesses WHERE code=?', (code,)).fetchone()
    if not b:
        conn.close()
        return jsonify({'ranking': []})
    rows = conn.execute(
        '''SELECT titulo_pedido, COUNT(*) n
           FROM pubshow_pedidos
           WHERE business_id=? AND titulo_pedido IS NOT NULL
           AND status != "aguardando_pix"
           AND created_at >= datetime("now", "-6 hours")
           GROUP BY titulo_pedido
           ORDER BY n DESC
           LIMIT 5''',
        (b['id'],)
    ).fetchall()
    conn.close()
    return jsonify({'ranking': [dict(r) for r in rows]})


@pubshow_bp.route('/api/buscar-biblioteca')
def api_buscar_biblioteca():
    """Busca na biblioteca curada de vídeos (rápido, sem API externa).
    Se ?bar=<token> for passado, filtra pelos gêneros que o bar liberou.
    Se ?q= vazio → retorna top 150 por popularidade (lista completa inicial).
    Se ?q=termo  → filtra por título/artista (limite 30 resultados).
    """
    import json as _json
    q          = request.args.get('q', '').strip()
    bar_token  = request.args.get('bar', '').strip()
    lista_full = not q  # sem query = carregamento inicial completo

    conn = get_pubshow_db()

    # Descobre gêneros permitidos para este bar (opcional)
    generos_permitidos = None
    if bar_token:
        brow = conn.execute(
            '''SELECT generos_jukebox FROM pubshow_businesses
               WHERE jukebox_token=? OR code=? LIMIT 1''',
            (bar_token, bar_token)
        ).fetchone()
        if brow and brow['generos_jukebox']:
            try:
                generos_permitidos = _json.loads(brow['generos_jukebox'])
            except Exception:
                generos_permitidos = None
    banidos = set()
    if bar_token:
        _bb = conn.execute('SELECT videos_bloqueados FROM pubshow_businesses WHERE jukebox_token=? OR code=? LIMIT 1',
                           (bar_token, bar_token)).fetchone()
        if _bb: banidos = set(_videos_bloqueados(_bb))

    limite = 600 if lista_full else 30   # 11/09/26: lista inicial = acervo INTEIRO (cliente filtra por estilo no celular)

    if lista_full:
        # Carregamento inicial — top N por popularidade
        if generos_permitidos:
            placeholders = ','.join('?' * len(generos_permitidos))
            rows = conn.execute(
                f'''SELECT youtube_id, titulo, artista, categoria, duracao_seg
                    FROM pubshow_videos
                    WHERE ativo=1 AND categoria IN ({placeholders})
                    ORDER BY views_milhoes DESC LIMIT {limite}''',
                tuple(generos_permitidos)
            ).fetchall()
        else:
            rows = conn.execute(
                f'''SELECT youtube_id, titulo, artista, categoria, duracao_seg
                    FROM pubshow_videos WHERE ativo=1
                    ORDER BY views_milhoes DESC LIMIT {limite}'''
            ).fetchall()
    else:
        # Busca por termo
        like = f'%{q}%'
        if generos_permitidos:
            placeholders = ','.join('?' * len(generos_permitidos))
            rows = conn.execute(
                f'''SELECT youtube_id, titulo, artista, categoria, duracao_seg
                    FROM pubshow_videos
                    WHERE ativo=1 AND categoria IN ({placeholders})
                      AND (titulo LIKE ? OR artista LIKE ?)
                    ORDER BY views_milhoes DESC LIMIT {limite}''',
                (*generos_permitidos, like, like)
            ).fetchall()
        else:
            rows = conn.execute(
                f'''SELECT youtube_id, titulo, artista, categoria, duracao_seg
                    FROM pubshow_videos
                    WHERE ativo=1 AND (titulo LIKE ? OR artista LIKE ?)
                    ORDER BY views_milhoes DESC LIMIT {limite}''',
                (like, like)
            ).fetchall()

    rows = [r for r in rows if _yid_ok(r['youtube_id'])]   # só o que o POST aceita (16/10/26, V4-01)
    if banidos:
        rows = [r for r in rows if r['youtube_id'] not in banidos]
    if rows:   # 17/10/26: música de mais de 10 min nem aparece pra pedir (os shows longos seguem só na TV)
        _dur = _duracoes_conhecidas([r['youtube_id'] for r in rows], conn)
        rows = [r for r in rows if (_dur.get(r['youtube_id']) or 0) <= YT_DUR_MAX_SEG]
    # 16/10/26 (crítico C1): num moto pub de Rock a lista começava com Despacito e Gangnam Style — a lista
    # inicial começa pelo tema que está no ar no bar (o resto segue por popularidade)…
    if lista_full and bar_token:
        _cb = conn.execute('SELECT canal_atual FROM pubshow_businesses WHERE jukebox_token=? OR code=? LIMIT 1',
                           (bar_token, bar_token)).fetchone()
        _cat = (CANAIS.get((_cb['canal_atual'] if _cb else '') or '') or {}).get('cat')
        _cats = set(_cat) if isinstance(_cat, (list, tuple)) else ({_cat} if _cat else set())
        if _cats:
            rows.sort(key=lambda r: 0 if r['categoria'] in _cats else 1)   # sort estável: mantém a popularidade
    # …e a mesma música (título + artista) aparece 1 vez só ("Happy · Pharrell Williams" saía 2x)
    _vistos, _uni = set(), []
    for r in rows:
        k = ((r['titulo'] or '').strip().lower(), (r['artista'] or '').strip().lower())
        if k in _vistos:
            continue
        _vistos.add(k); _uni.append(r)
    rows = _uni
    # 17/10/26 ("aqui só toca rock"): o que entrou no acervo pelo YouTube foi pra categoria do canal no ar (sertanejo
    # pedido no bar de rock virou "rock"). Esses valem pelo ARTISTA; só o que já se sabe (o Gemini roda no fundo).
    _eb = pubshow_estilo.estilos_do_bar(_bar_por_token(bar_token)) if generos_permitidos else None
    if rows and _eb:
        try:
            _ids, _pp = [r['youtube_id'] for r in rows], set()
            for i in range(0, len(_ids), 400):
                _parte = _ids[i:i + 400]
                _pp |= {x[0] for x in conn.execute(
                    f"""SELECT youtube_id FROM pubshow_videos WHERE subcategoria='pedido_publico'
                        AND youtube_id IN ({','.join('?' * len(_parte))})""", tuple(_parte)).fetchall()}
            if _pp:
                _est = pubshow_estilo.classificar(
                    [{'id': r['youtube_id'], 'titulo': r['titulo'] or '',
                      'palpites': [pubshow_estilo.artista_do_titulo(r['titulo']), r['artista']]}
                     for r in rows if r['youtube_id'] in _pp], espera=None)
                rows = [r for r in rows if r['youtube_id'] not in _pp
                        or pubshow_estilo.passa(_est.get(r['youtube_id']), _eb)]
        except Exception as e:
            log.warning('[PUBSHOW] estilo da lista do bar falhou: %s', e)
    conn.close()
    return jsonify({'resultados': [dict(r) for r in rows], 'total': len(rows)})


# ── Busca do YouTube no celular (16/10/26, Diogo: "digito a música, ele puxa imagens maiores pro cliente ver
# qual é o videoclipe; sempre filtrar só os mais vistos do assunto, não todos"). Base: REGRAS_MUSICA (27 buscas reais).
# Ranking: bate com o que digitou > canal oficial do artista > mais visto. Fora: menos de 1 ou mais de 10 min (peido,
# show, "10 horas"), ao vivo sem duração, Shorts, karaokê/aula/cover/react... (voltam se a pessoa digitar a palavra).
# Só áudio, letra ou foto parada (canal "- Topic", vídeo sem data) vai pro fim. O mesmo clipe em 2 canais conta 1.
YT_TOP, YT_JANELA = 5, 15            # cards no celular · candidatos lidos na ordem (relevância) do YouTube
YT_DUR_MIN_SEG, YT_DUR_MAX_SEG = 60, 600
_YT_CACHE = {}                       # termo → (instante, vídeos lidos): 1 worker no Railway, não bate 2x no YouTube
_YT_CACHE_SEG, _YT_CACHE_MAX = 600, 300
# Regra dos 10 min NO PEDIDO (17/10/26, Diogo: "precisa lançar a regra de vídeo com no máximo 10 min"): vale pra todo
# pedido de música (acervo, YouTube, furar a fila). O celular não manda duração: o servidor guarda a de tudo que a busca
# do YouTube leu e usa a do acervo. Duração desconhecida passa (e vai pro log): nada de barrar por engano.
_YT_DURACAO = {}                     # youtube_id → segundos (ao vivo sem fim = _YT_SEG_AO_VIVO)
_YT_DURACAO_MAX, _YT_SEG_AO_VIVO = 5000, 86400
MSG_MUSICA_LONGA = '🎵 Essa é longa demais (máx. 10 min). Escolhe outra!'
_YT_STOP = {'e', 'de', 'da', 'do', 'das', 'dos', 'o', 'a', 'os', 'as', 'no', 'na', 'nos', 'nas', 'em', 'um', 'uma',
            'com', 'pra', 'para', 'ao', 'the', 'of', 'and', 'ft', 'feat', 'musica', 'musicas', 'clipe', 'video',
            'videoclipe', 'oficial', 'official'}
_YT_CONECT = {'da', 'de', 'do', 'das', 'dos', 'e', 'na', 'no', 'nas', 'nos', 'em', 'a', 'o', 'as', 'os', 'um',
              'uma', 'com', 'pra', 'para', 'of', 'the', 'and', 'in', 'on', 'to'}
# os regex abaixo rodam no texto normalizado (_yt_norm: minúsculo e sem acento)
_YT_FORA_RE  = re.compile(r'\b(karaoke|playback|instrumental|backing ?track|covers?|aula|como tocar|tutorial|'
                          r'guitar tab|lesson|drum ?cover|react\w*|reacao|reagindo|8d|slowed|sped up|nightcore|'
                          r'bass ?boost\w*|10 ?horas?|10 ?hours?|1 ?hora|making ?of|bastidores|coreografia|'
                          r'filme completo|full movie)\b')
_YT_AUDIO_RE = re.compile(r'\b(audio|visuali[sz]er|lyrics?|letras?|legendad[oa]|traducao|sub esp|subtitulad[oa]|'
                          r'subtitled)\b')
_YT_VIVO_RE  = re.compile(r'\b(ao ?vivo|live|en vivo|en directo)\b')
_YT_RUIDO_RE = re.compile(r'\b(official|oficial|music ?video|videoclipe|videoclip|video ?clipe|clipe|clip|video|'
                          r'lyrics?|letras?|legendad[oa]|traducao|audio|visuali[sz]er|hd|hq|4k|8k|1080p?|720p|'
                          r'remaster\w*|full|completo|explicit|explicito|dvd|cd|album|single|lancamento|exclusivo|'
                          r'prod)\b')
_YT_BRK_RE   = re.compile(r'[\(\[\{]([^\(\)\[\]\{\}]*)[\)\]\}]')
_YT_SEP_RE   = re.compile(r'\s+(?:-{1,2}|–|—|\||·|~+)\s+')
_YID_RE      = re.compile(r'[A-Za-z0-9_-]{11}')


def _yt_norm(s):
    s = unicodedata.normalize('NFKD', (s or '').replace('&', ' e ')).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', s).split())


def _yt_norm_canal(c):
    """'Legião Urbana - Topic' / 'ClassicoVEVO' / 'Zezo - Oficial' → nome do artista normalizado."""
    n = _yt_norm(c)
    n = re.sub(r'\s*\b(topic|oficial|official)$', '', n)
    return re.sub(r'vevo$', '', n).strip()


def _yt_seg(txt):
    """'4:41' → 281 · '1:26:37' → 5197 · '' (ao vivo) → 0"""
    try:
        seg = 0
        for p in (txt or '').strip().split(':'):
            seg = seg * 60 + int(p)
        return seg
    except (TypeError, ValueError):
        return 0


def _yt_views_txt(n):
    """112.519.286 → '113 mi de views' · 1.237.144.857 → '1,2 bi de views' · 45.300 → '45 mil views'"""
    if n >= 1_000_000_000:
        v, u = n / 1e9, 'bi de views'
    elif n >= 1_000_000:
        v, u = n / 1e6, 'mi de views'
    elif n >= 1_000:
        v, u = n / 1e3, 'mil views'
    else:
        return f'{n} views' if n else ''
    txt = f'{v:.1f}' if v < 9.95 else f'{v:.0f}'
    if txt.endswith('.0'):
        txt = txt[:-2]
    return f"{txt.replace('.', ',')} {u}"


def _yt_ler(data):
    """Os vídeos normais (videoRenderer) da resposta da InnerTube, na ordem do YouTube. Shorts, playlists, Mix e o
    cartão do artista chegam em outros formatos e ficam fora sozinhos."""
    out = []
    try:
        secs = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents']
        secs = [s for s in secs if isinstance(s, dict)]
    except Exception:
        return out
    for sec in secs:
        isr = sec.get('itemSectionRenderer')
        itens = (isr.get('contents') if isinstance(isr, dict) else None) or []
        for item in itens if isinstance(itens, list) else []:
            try:
                vr = item.get('videoRenderer') or {}
                vid = str(vr.get('videoId') or '')
                titulo = ''.join(str(r.get('text') or '') for r in (vr.get('title') or {}).get('runs', []))
                if not (_YID_RE.fullmatch(vid) and titulo):
                    continue
                canal = str(((vr.get('longBylineText') or {}).get('runs') or [{}])[0].get('text') or '')
                dur_txt = str((vr.get('lengthText') or {}).get('simpleText') or '')
                views = int(re.sub(r'\D', '', str((vr.get('viewCountText') or {}).get('simpleText') or ''))[:15] or 0)
                selos = [((b or {}).get('metadataBadgeRenderer') or {}).get('style', '')
                         for b in (vr.get('ownerBadges') or []) + (vr.get('badges') or [])]
                capas = [((o or {}).get('thumbnailOverlayTimeStatusRenderer') or {}).get('style', '')
                         for o in (vr.get('thumbnailOverlays') or [])]
                url = (((vr.get('navigationEndpoint') or {}).get('commandMetadata') or {})
                       .get('webCommandMetadata') or {}).get('url', '')
                out.append({
                    'id': vid, 'titulo': titulo, 'canal': canal, 'dur': _yt_seg(dur_txt), 'dur_txt': dur_txt,
                    'views': views,
                    'oficial': 'BADGE_STYLE_TYPE_VERIFIED_ARTIST' in selos or _yt_norm(canal).endswith('vevo'),
                    'topic': _yt_norm(canal).endswith(' topic'),
                    'publicado': bool(vr.get('publishedTimeText')),
                    'ao_vivo': not dur_txt or 'BADGE_STYLE_TYPE_LIVE_NOW' in selos or 'LIVE' in capas,
                    'shorts': url.startswith('/shorts/') or 'SHORTS' in capas,
                })
            except Exception:
                continue
    return out


def _yt_capitaliza(s):
    """Título TODO EM MAIÚSCULA ('NA HORA DA RAIVA') vira 'Na Hora da Raiva'; o resto fica como veio."""
    letras = [c for c in s if c.isalpha()]
    palavras = s.split(' ')
    if (len(letras) < 4 or sum(c.isupper() for c in letras) < 0.8 * len(letras)
            or sum(1 for w in palavras if any(c.isalpha() for c in w)) < 2):
        return s
    out = []
    for i, w in enumerate(palavras):
        if re.search(r'[\d/\.&\x00]', w):     # sigla e número ficam como vieram: AC/DC, R.E.M., 2pac
            out.append(w)
            continue
        lw = w.lower()
        out.append(lw if (i and lw in _YT_CONECT) else lw[:1].upper() + lw[1:])
    return ' '.join(out)


def _yt_canal_limpo(c):
    """'Legião Urbana - Topic' → 'Legião Urbana' · 'Zezo - Oficial' → 'Zezo' (nome pra mostrar)."""
    return re.sub(r'(?i)(\s*-\s*topic|\s*-?\s*oficial|\s*-?\s*official|vevo)$', '', (c or '').strip()).strip()


def _yt_titulo_limpo(titulo, canal_artista='', artistas=None):
    """'Pearl Jam - Black (Official Video) [HD]' → 'Black · Pearl Jam'. `canal_artista` = o canal, só quando ele é o
    próprio artista (selo oficial ou '- Topic'); `artistas` = {nome normalizado: nome} dos artistas da mesma busca.
    Sem saber quem é o artista, mantém a ordem e só tira o ruído. Máx. 80 letras (o mesmo corte do POST)."""
    t = re.sub(r'(^|\s)[@#][\w\.]+', ' ', (titulo or '').replace('\x00', ''))
    t = re.sub(r'[\*"“”]+', ' ', t)
    vivo = False
    guardados = []      # parêntese que fica: some do texto enquanto separa ("(Slane Castle - Meath, Ireland)")

    def _colchete(m):
        nonlocal vivo
        n = _yt_norm(m.group(1))
        if _YT_VIVO_RE.search(n):
            vivo = True
            return ' '
        if not n or _YT_RUIDO_RE.search(n):
            return ' '
        guardados.append(m.group(0))
        return f' \x00{len(guardados) - 1}\x00 '

    def _volta(s):
        return ' '.join(re.sub(r'\x00(\d+)\x00', lambda m: guardados[int(m.group(1))], s).split())

    t = ' '.join(_YT_BRK_RE.sub(_colchete, t).split()).strip(' -–—|·~:')
    sep = ' · ' if ' · ' in t else ' - '
    segs = [s.strip(' -–—|·~:') for s in _YT_SEP_RE.split(t)]
    segs = [s for s in segs if s]
    if not segs:
        return ' '.join((titulo or '').split())[:80]
    canal_artista = (canal_artista or '').replace('\x00', '')
    conhecidos = {k: str(v).replace('\x00', '') for k, v in (artistas or {}).items()}
    nc = _yt_norm_canal(canal_artista)
    if nc:
        conhecidos.setdefault(nc, _yt_canal_limpo(canal_artista))
    if len(segs) == 1 and ': ' in segs[0] and _yt_norm(segs[0].split(': ', 1)[0]) in conhecidos:
        segs = segs[0].split(': ', 1)          # "Metallica: One" → só quando a esquerda é o artista
    artista, musica, resto = '', '', []
    idx = next((i for i, s in enumerate(segs) if _yt_norm(s) in conhecidos), None)
    if idx is None and len(segs) > 1:
        n0 = f' {_yt_norm(segs[0])} '          # "Anitta, Mc Zaac & Maejor - Vai Malandra": a linha de crédito inteira
        if any(len(a) >= 4 and f' {a} ' in n0 for a in conhecidos):
            idx = 0
    if idx is not None:
        artista = segs[idx]
        resto = [s for i, s in enumerate(segs) if i != idx]
    elif len(segs) > 1:                        # "Paradinha - Anitta 16/06/2017 Centro de Eventos": começa com o artista
        for i, s in enumerate(segs[1:], 1):
            ns = _yt_norm(s)
            a = next((a for a in conhecidos if len(a) >= 4 and (ns == a or ns.startswith(a + ' '))), None)
            if a:
                artista, resto = conhecidos[a], [x for j, x in enumerate(segs) if j != i]
                break
    if artista:
        if not resto:
            return _volta(artista)[:80]
        musica = resto[0]
        extra = ''
        for s in resto[1:]:
            ns = _yt_norm(_volta(s))
            if _YT_VIVO_RE.search(ns):
                vivo = True
            elif not extra and ns and not _YT_RUIDO_RE.search(ns):
                extra = s                      # "Faroeste Caboclo | Globo de Ouro 1988"
        if extra and not vivo:
            musica = f'{musica} ({extra})'
    elif len(segs) == 1 and canal_artista:     # "Evidências" no canal oficial "Chitãozinho & Xororó"
        musica, artista = segs[0], _yt_canal_limpo(canal_artista)
    else:                                      # não sei quem é o artista: mantém a ordem, tira só o ruído
        extras = []
        for s in segs[2:]:
            ns = _yt_norm(_volta(s))
            if _YT_VIVO_RE.search(ns):
                vivo = True
            elif ns and not _YT_RUIDO_RE.search(ns):
                extras.append(s)
        musica = sep.join([_yt_capitaliza(s) for s in segs[:2]] + extras)
    musica = _volta(_yt_capitaliza(musica))
    artista = _volta(artista)
    if vivo and not _YT_VIVO_RE.search(_yt_norm(musica)):
        musica += ' (Ao Vivo)'
    if not artista:
        return musica[:80]
    if len(musica) + len(artista) + 3 > 80:
        musica = musica[:max(20, 76 - len(artista))].rstrip() + '…'
    return f'{musica} · {artista}'[:80]


def _yt_ranquear(lidos, q, top=YT_TOP):
    """Os YT_TOP melhores pro celular: bate com o que digitou > oficial do artista > mais visto (só áudio no fim).
    Bar com estilo pede mais (top) pra sobrar 5 depois do filtro do estilo."""
    qn = _yt_norm(q)
    tokens = [w for w in qn.split() if w not in _YT_STOP and (len(w) >= 2 or w.isdigit())]
    fora_q = {m.group(0).replace(' ', '') for m in _YT_FORA_RE.finditer(qn)}
    audio_q = bool(_YT_AUDIO_RE.search(qn))
    artistas = {}
    for v in lidos:
        if v['oficial'] or v['topic']:
            a = _yt_norm_canal(v['canal'])
            if a:
                artistas.setdefault(a, _yt_canal_limpo(v['canal']))
    cands = []
    for v in lidos:
        if v['ao_vivo'] or v['shorts'] or not (YT_DUR_MIN_SEG <= v['dur'] <= YT_DUR_MAX_SEG):
            continue
        tn = _yt_norm(v['titulo'])
        if {m.group(0).replace(' ', '') for m in _YT_FORA_RE.finditer(tn)} - fora_q:
            continue
        so_audio = v['topic'] or not v['publicado'] or (not audio_q and bool(_YT_AUDIO_RE.search(tn)))
        palavras = (tn + ' ' + _yt_norm(v['canal'])).split()
        bate = sum(1 for t in tokens if any(p.startswith(t) for p in palavras))
        cands.append((so_audio, -bate, not v['oficial'], -v['views'], len(cands), v))
        if len(cands) >= YT_JANELA:
            break
    cands.sort(key=lambda c: c[:5])
    out = []
    for so_audio, _b, _o, _v, _i, v in cands:
        dono = v['canal'] if (v['oficial'] or v['topic']) else ''
        limpo = _yt_titulo_limpo(v['titulo'], dono, artistas)
        chave = _yt_norm(limpo)
        if any(o['_chave'] == chave and abs(o['dur_seg'] - v['dur']) <= 2 for o in out):
            continue                           # o mesmo clipe repostado em outro canal
        out.append({
            'id': v['id'], 'titulo': limpo, 'titulo_original': v['titulo'][:100], 'canal': v['canal'][:40],
            'duracao': v['dur_txt'], 'dur_seg': v['dur'],
            'thumb': f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg",
            'views': v['views'], 'views_txt': _yt_views_txt(v['views']),
            'oficial': bool(v['oficial']), 'so_audio': bool(so_audio), '_chave': chave,
        })
        if len(out) >= top:
            break
    for o in out:
        o.pop('_chave', None)
    return out


def _yt_buscar_innertube(q):
    """Vídeos lidos da busca do YouTube (InnerTube, sem chave), com cache de 10 min por termo."""
    chave = ' '.join(q.lower().split())
    agora = _time.time()
    hit = _YT_CACHE.get(chave)
    if hit and agora - hit[0] < _YT_CACHE_SEG:
        _yt_lembrar_duracao(hit[1])
        return hit[1]
    # B2: chave configurável via env — fallback para chave pública do cliente web YT
    _yt_key = os.environ.get('YOUTUBE_INNERTUBE_KEY', 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8')
    resp = _requests.post(
        f'https://www.youtube.com/youtubei/v1/search?key={_yt_key}',
        json={
            'query': q,
            'context': {
                'client': {
                    'clientName': 'WEB',
                    'clientVersion': '2.20231121.08.00',
                    'hl': 'pt',
                    'gl': 'BR',
                }
            }
        },
        headers={'Content-Type': 'application/json'},
        timeout=5   # 1 worker: busca travada segura a TV e o painel junto (era 8)
    )
    lidos = _yt_ler(resp.json())
    _yt_lembrar_duracao(lidos)
    if lidos:
        if len(_YT_CACHE) >= _YT_CACHE_MAX:
            for k in sorted(_YT_CACHE, key=lambda k: _YT_CACHE[k][0])[:_YT_CACHE_MAX // 4]:
                _YT_CACHE.pop(k, None)
        _YT_CACHE[chave] = (agora, lidos)
    return lidos


def _yt_lembrar_duracao(lidos):
    """Guarda a duração de TODO vídeo que a busca leu (até os longos e os ao vivo, que nem viram card)."""
    for v in lidos:
        seg = _YT_SEG_AO_VIVO if v.get('ao_vivo') else int(v.get('dur') or 0)
        if seg > 0:
            _YT_DURACAO.pop(v['id'], None)
            _YT_DURACAO[v['id']] = seg
    if len(_YT_DURACAO) > _YT_DURACAO_MAX:
        for k in list(_YT_DURACAO)[:len(_YT_DURACAO) - _YT_DURACAO_MAX + 500]:
            _YT_DURACAO.pop(k, None)


def _duracoes_conhecidas(ids, conn=None):
    """{youtube_id: segundos ou None}. Ordem: acervo curado > o que a busca do YouTube leu > pedido antigo que entrou
    no acervo com duração real (o 'pedido_publico' antigo grava 180 fixo: isso não vale como duração)."""
    ids = [i for i in dict.fromkeys(ids) if i]
    out = {i: None for i in ids}
    if not ids:
        return out
    fechar = conn is None
    conn = conn or get_pubshow_db()
    try:
        acervo = {r['youtube_id']: r for r in conn.execute(
            f"SELECT youtube_id, duracao_seg, subcategoria FROM pubshow_videos WHERE youtube_id IN ({','.join('?' * len(ids))})",
            tuple(ids)).fetchall()}
    finally:
        if fechar:
            conn.close()
    for i in ids:
        r = acervo.get(i)
        seg = int(r['duracao_seg'] or 0) if r else 0
        if r and seg > 0 and (r['subcategoria'] or '') != 'pedido_publico':
            out[i] = seg
        elif i in _YT_DURACAO:
            out[i] = _YT_DURACAO[i]
        elif r and seg > 0 and seg != 180:
            out[i] = seg
    return out


def _musica_longa(b, youtube_id, tipo):
    """True se a duração conhecida passa de 10 min. Desconhecida: deixa passar e grava no log."""
    seg = _duracoes_conhecidas([youtube_id]).get(youtube_id)
    if seg is None:
        log.warning('[PUBSHOW] pedido com duração desconhecida (passou): bar=%s tipo=%s yid=%s',
                    b.get('code') if hasattr(b, 'get') else '', tipo, youtube_id)
        return False
    return seg > YT_DUR_MAX_SEG


def _palpites_artista(card):
    """Quem é o artista de um card: o do título limpo ('Back In Black · AC/DC') e o canal, quando é do próprio
    artista (selo oficial ou '- Topic')."""
    canal = card.get('canal') or ''
    ps = [pubshow_estilo.artista_do_titulo(card.get('titulo'))]
    if card.get('oficial') or _yt_norm(canal).endswith(' topic'):
        ps.append(_yt_canal_limpo(canal))
    return [p for p in ps if p]


def _so_do_estilo(b, ests_bar, cards, espera=pubshow_estilo.ESPERA_BUSCA):
    """Os cards que podem tocar no estilo do bar ("aqui só toca rock", 17/10/26). Acervo curado na categoria do
    estilo passa direto; o resto vale pelo ARTISTA (pubshow_estilo): desconhecido passa, de vários estilos conta como
    todos. Acervo curado de outra categoria só passa se o artista for (também) do estilo."""
    if not ests_bar or not cards:
        return list(cards)
    ids = [c['id'] for c in cards if c.get('id')]
    cat = {}
    if ids:
        conn = get_pubshow_db()
        try:
            cat = {r['youtube_id']: r['categoria'] for r in conn.execute(
                f"""SELECT youtube_id, categoria FROM pubshow_videos
                    WHERE youtube_id IN ({','.join('?' * len(ids))}) AND ativo=1
                      AND COALESCE(subcategoria, '') <> 'pedido_publico'""", tuple(ids)).fetchall()}
        finally:
            conn.close()
    est_cat = {i: pubshow_estilo.estilo_da_categoria(c) for i, c in cat.items()}
    pelo_artista = [c for c in cards if est_cat.get(c.get('id')) not in ests_bar]
    est = pubshow_estilo.classificar(
        [{'id': c['id'], 'titulo': c.get('titulo_original') or c.get('titulo') or '', 'canal': c.get('canal') or '',
          'palpites': _palpites_artista(c)} for c in pelo_artista], espera) if pelo_artista else {}
    out = []
    for c in cards:
        e_cat, e_art = est_cat.get(c.get('id')), est.get(c.get('id'))
        if e_cat in ests_bar or (pubshow_estilo.passa(e_art, ests_bar) and (not e_cat or e_art)):
            out.append(c)
    return out


def _fora_do_estilo(b, youtube_id, titulo):
    """'' se a música pode tocar no estilo do bar; senão a mensagem pro cliente (vale pro grátis e pro pago)."""
    ests_bar = pubshow_estilo.estilos_do_bar(b)
    if not ests_bar:
        return ''
    try:   # sem Gemini aqui: vale o que a busca já aprendeu (o título do pedido vem do celular)
        if _so_do_estilo(b, ests_bar, [{'id': youtube_id, 'titulo': titulo or ''}], espera=None):
            return ''
    except Exception as e:      # nunca barra o pedido por erro nosso
        log.warning('[PUBSHOW] estilo do pedido falhou (passou): %s', e)
        return ''
    return pubshow_estilo.msg_fora(b, ests_bar)


def _bar_por_token(token):
    token = (token or '').strip()
    if not token or len(token) > 40:
        return None
    conn = get_pubshow_db()
    try:
        b = conn.execute('''SELECT * FROM pubshow_businesses
                            WHERE (jukebox_token=? OR code=?) AND COALESCE(suspenso, 0)=0 LIMIT 1''',
                         (token, token)).fetchone()   # bar suspenso: o jukebox dá 403, as rotas novas também somem
        return dict(b) if b else None
    finally:
        conn.close()


def _yt_selos(business_id, itens, ip_proprio=None):
    """Marca em cada vídeo: 'na_fila' (mesma regra do _musica_na_fila) e 'tocou_min' (tocou neste bar nas últimas 3 h).
    ip_proprio (IP#client_id do celular, rebase 17/10/26): o PIX aberto DESTE celular não conta como "na fila" — na
    junção o mesmo pedido do mesmo celular reabre o mesmo PIX (crítico C2), não pode aparecer como música bloqueada."""
    ids = [i['id'] for i in itens if i.get('id')]
    fila, tocou = set(), {}
    if ids:
        ph = ','.join('?' * len(ids))
        marcas = ','.join('?' * len(TIPOS_MUSICA_FILA))
        _proprio = 'AND ip_cliente <> ?' if (ip_proprio and '#' in ip_proprio) else ''
        conn = get_pubshow_db()
        try:
            fila = {r[0] for r in conn.execute(
                f"""SELECT DISTINCT youtube_id FROM pubshow_pedidos
                    WHERE business_id=? AND youtube_id IN ({ph}) AND tipo IN ({marcas})
                      AND (status='pendente'
                           OR (status='aguardando_pix' AND created_at >= datetime('now', '-15 minutes') {_proprio}))""",
                (business_id, *ids, *TIPOS_MUSICA_FILA, *((ip_proprio,) if _proprio else ()))).fetchall()}
            # exibido_at é gravado no horário de Brasília (now -3 h)
            tocou = {r[0]: r[1] for r in conn.execute(
                f"""SELECT youtube_id, MIN((julianday(datetime('now', '-3 hours')) - julianday(exibido_at)) * 1440)
                    FROM pubshow_pedidos
                    WHERE business_id=? AND youtube_id IN ({ph}) AND status='exibido'
                      AND exibido_at >= datetime('now', '-3 hours', '-180 minutes')
                    GROUP BY youtube_id""",
                (business_id, *ids)).fetchall()}
        except Exception:
            fila, tocou = set(), {}
        finally:
            conn.close()
    for i in itens:
        i['na_fila'] = i.get('id') in fila
        m = tocou.get(i.get('id'))
        i['tocou_min'] = max(0, int(m)) if m is not None else None
    return itens


_BUSCAR_MAX_BAR, _BUSCAR_MAX_IP = 120, 150   # buscas por minuto (15/10/26); IP ≥ bar: no Wi-Fi do bar todos têm o mesmo IP (16/10/26, C5)


@pubshow_bp.route('/api/buscar')
def api_buscar():
    """Busca músicas no YouTube via InnerTube API (sem chave): os 5 melhores, com capa grande e selos do bar."""
    # 15/10/26: só com o token de um bar de verdade (?bar=) e com teto por bar + teto alto por IP.
    # Antes era pública e sem limite: um loop aqui (timeout 8 s) segurava o worker do 4kitem inteiro.
    # No Wi-Fi do bar todo mundo divide o IP (decisão 11/09/26), por isso o teto principal é do BAR.
    bar_token = request.args.get('bar', '').strip()
    if not bar_token:
        return jsonify({'resultados': [], 'error': 'não autorizado'}), 403
    _cb = get_pubshow_db()
    _bar = _cb.execute('SELECT id FROM pubshow_businesses WHERE jukebox_token=? OR code=? LIMIT 1',
                       (bar_token, bar_token)).fetchone()
    _cb.close()
    if not _bar:
        return jsonify({'resultados': [], 'error': 'não autorizado'}), 403
    q = request.args.get('q', '').strip()[:80]
    if not q or len(q) < 2:
        return jsonify({'resultados': []})
    _chave_bar, _chave_ip = f'buscar_bar:{_bar["id"]}', 'buscar_ip:' + _ip_real()
    if _rl_bloqueado(_chave_bar, max_n=_BUSCAR_MAX_BAR, janela=60) or \
       _rl_bloqueado(_chave_ip, max_n=_BUSCAR_MAX_IP, janela=60):
        return jsonify({'resultados': [], 'error': 'Muitas buscas agora. Espera uns segundos e tenta de novo.'}), 429
    _rl_falhou(_chave_bar, _chave_ip)
    try:
        lidos = _yt_buscar_innertube(q)
    except Exception as e:
        log.error('YouTube search error: %s', e)
        # crítico leigo A3 (17/10/26): fora do ar ≠ "nenhum resultado" — o celular libera o furar a fila no acervo
        return jsonify({'resultados': [], 'erro': 'youtube_fora'}), 503
    b = _bar_por_token(bar_token)
    ests_bar = pubshow_estilo.estilos_do_bar(b)    # 17/10/26: "aqui só toca rock" (None = tudo liberado)
    try:
        resultados = _yt_ranquear(lidos, q, top=YT_TOP * 2 if ests_bar else YT_TOP)
    except Exception as e:   # resposta esquisita do YouTube: nunca 500, e não guarda no cache
        log.error('YouTube ranking error: %s', e)
        _YT_CACHE.pop(' '.join(q.lower().split()), None)
        return jsonify({'resultados': [], 'erro': 'youtube_fora'}), 503
    if ests_bar and resultados:
        try:
            resultados = _so_do_estilo(b, ests_bar, resultados)[:YT_TOP]
        except Exception as e:   # erro nosso no filtro: mostra a busca como sempre (nunca 500)
            log.warning('[PUBSHOW] filtro de estilo falhou (busca sem filtro): %s', e)
            resultados = resultados[:YT_TOP]
        if not resultados:
            return jsonify({'resultados': [], 'fora_estilo': True, **pubshow_estilo.aviso_fora(ests_bar)})
    if resultados:   # selos "já na fila" / "tocou há X min" do bar do token (já conferido acima)
        _yt_selos(_bar['id'], resultados)
    return jsonify({'resultados': resultados})


@pubshow_bp.route('/api/mais-pedidas/<token>')
def api_mais_pedidas(token):
    """🔥 Mais pedidas no bar: as músicas que mais tocaram por pedido nos últimos 7 dias (antes de digitar)."""
    b = _bar_por_token(token)
    if not b:
        return jsonify({'resultados': []}), 404
    banidos = set(_videos_bloqueados(b))
    conn = get_pubshow_db()
    try:
        rows = conn.execute(
            """SELECT youtube_id, COUNT(*) AS vezes, MAX(id) AS ultimo
               FROM pubshow_pedidos
               WHERE business_id=? AND status='exibido' AND youtube_id IS NOT NULL
                 AND exibido_at >= datetime('now', '-3 hours', '-7 days')
               GROUP BY youtube_id
               ORDER BY vezes DESC, ultimo DESC
               LIMIT 20""",
            (b['id'],)).fetchall()
        rows = [r for r in rows if r['youtube_id'] not in banidos and _YID_RE.fullmatch(r['youtube_id'] or '')]
        # 17/10/26: só música com duração CONHECIDA e de até 10 min (no ar, um show de 2 h pedido antes da regra,
        # sem duração, aparecia aqui). A lista se refaz sozinha: a busca e a TV vão aprendendo as durações.
        _dur = _duracoes_conhecidas([r['youtube_id'] for r in rows], conn)
        rows = [r for r in rows if _dur.get(r['youtube_id']) is not None
                and _dur[r['youtube_id']] <= YT_DUR_MAX_SEG]
        _ests_bar = pubshow_estilo.estilos_do_bar(b)   # 17/10/26: bar de rock não mostra o sertanejo de antes
        rows = rows if _ests_bar else rows[:6]
        ids = [r['youtube_id'] for r in rows]
        titulos = {}
        if ids:
            ph = ','.join('?' * len(ids))
            # o título que o celular manda é texto livre: vale o do acervo curado e, fora dele, o MAIS repetido
            # (1 pedido forjado não troca o card de todo mundo por 7 dias)
            for t in conn.execute(
                    f"""SELECT youtube_id, titulo_pedido FROM pubshow_pedidos
                        WHERE business_id=? AND youtube_id IN ({ph}) AND status='exibido' AND titulo_pedido IS NOT NULL
                        GROUP BY youtube_id, titulo_pedido
                        ORDER BY COUNT(*) ASC, MAX(id) ASC""", (b['id'], *ids)).fetchall():
                titulos[t[0]] = t[1]          # o último de cada vídeo (o mais repetido) fica
            for v in conn.execute(
                    f"""SELECT youtube_id, titulo, artista FROM pubshow_videos
                        WHERE youtube_id IN ({ph}) AND COALESCE(subcategoria, '') <> 'pedido_publico'""",
                    tuple(ids)).fetchall():
                tit, art = (v['titulo'] or '').strip(), (v['artista'] or '').strip()
                if tit:
                    titulos[v['youtube_id']] = f'{tit} · {art}' if art and art.lower() not in tit.lower() else tit
    finally:
        conn.close()
    out = []
    for r in rows:
        yid = r['youtube_id']
        t = (titulos.get(yid) or '').strip()
        titulo = t if ' · ' in t else _yt_titulo_limpo(t)   # " · " = já veio limpo da busca nova
        out.append({'id': yid, 'titulo': titulo[:80] or 'Música pedida',
                    'vezes': r['vezes'], 'thumb': f'https://i.ytimg.com/vi/{yid}/hqdefault.jpg'})
    if _ests_bar and out:
        try:
            out = _so_do_estilo(b, _ests_bar, out, espera=None)   # só o que já se sabe (título vem do celular)
        except Exception as e:
            log.warning('[PUBSHOW] estilo das mais pedidas falhou: %s', e)
        out = out[:6]
    _yt_selos(b['id'], out)
    return jsonify({'resultados': out})


@pubshow_bp.route('/api/fila-info/<token>')
def api_fila_info(token):
    """O que o celular mostra ao escolher a música: em que lugar entra (grátis e furando a fila), quanto falta pra
    próxima grátis deste celular e, com ?yid=, se ela já está na fila ou tocou há pouco."""
    b = _bar_por_token(token)
    if not b:
        return jsonify({'error': 'not_found'}), 404
    ip_cliente = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    _cid = (request.args.get('cid') or '').strip()
    if re.fullmatch(r'[A-Za-z0-9_-]{8,40}', _cid):
        ip_cliente = f'{ip_cliente}#{_cid}'   # mesma chave do POST do jukebox (1 grátis a cada 20 min por celular)
    conn = get_pubshow_db()
    try:
        n = {r[0]: r[1] for r in conn.execute(
            """SELECT tipo, COUNT(*) FROM pubshow_pedidos
               WHERE business_id=? AND status='pendente'
                 AND tipo IN ('vip','flash','musica','musica_especifica','musica_externa','pular_espera')
               GROUP BY tipo""", (b['id'],)).fetchall()}
    finally:
        conn.close()

    def _estimativa(na_frente):   # mesma conta do /api/pedido-status: 4 min por música + metade da atual
        return {'na_frente': na_frente, 'posicao': na_frente + 1,
                'min': None if na_frente == 0 else na_frente * 4 + 2}

    out = {'gratis': _estimativa(sum(n.values())),
           'flash': _estimativa(n.get('vip', 0) + n.get('flash', 0)),
           'espera_gratis_min': _gratis_espera_min(b, ip_cliente)}
    yid = (request.args.get('yid') or '').strip()
    if _YID_RE.fullmatch(yid):
        s = _yt_selos(b['id'], [{'id': yid}], ip_cliente)[0]
        out['na_fila'], out['tocou_min'] = s['na_fila'], s['tocou_min']
    return jsonify(out)


# ── PAINEL DO BAR ─────────────────────────────────────────────────────────────

@pubshow_bp.route('/painel')
@pubshow_login_required
def painel():
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')
    # Fila de e-mails: em FUNDO (rate-limited: 1x/10min por processo) — o painel não espera o Resend
    try:
        _processar_fila_emails_em_fundo()
    except Exception:
        pass
    conn = get_pubshow_db()
    pedidos_hoje = conn.execute(
        '''SELECT * FROM pubshow_pedidos WHERE business_id=?
           AND date(created_at, "-3 hours")=date("now", "-3 hours")
           ORDER BY created_at DESC''',
        (b['id'],)
    ).fetchall()
    # Dinheiro = só pagamento CONFIRMADO, pela soma única (15/10/26): casa (sem gorjeta) e equipe
    _hoje = _hoje_brt()
    _7d = _hoje - timedelta(days=6)
    _casa = somar_pagos(conn, b['id'], _hoje, _hoje, 'casa')
    total_hoje = _casa['total']
    momentos_hoje_n = _casa['n']   # 17/10/26 (decisão 1): "N momentos pagos" = só PIX confirmado, sem gorjeta
    _gorj = somar_pagos(conn, b['id'], _hoje, _hoje, 'gorjeta')
    gorjeta_hoje   = _gorj['total']
    gorjeta_hoje_n = _gorj['n']
    gorjeta_7dias = [{'dia': d, 'n': v['n'], 'tot': v['total']} for d, v in
                     sorted(somar_pagos(conn, b['id'], _7d, _hoje, 'gorjeta', por='dia').items(), reverse=True)]
    fila = conn.execute(   # crítico leigo A2 (17/10/26): a ordem da TV (vip, furar a fila, chegada)
        '''SELECT * FROM pubshow_pedidos WHERE business_id=? AND status="pendente"
           ORDER BY CASE tipo WHEN "vip" THEN 1 WHEN "flash" THEN 2 ELSE 3 END, created_at ASC, id ASC LIMIT 20''',
        (b['id'],)
    ).fetchall()
    aguardando_pix = conn.execute(
        '''SELECT * FROM pubshow_pedidos WHERE business_id=? AND status="aguardando_pix"
           AND created_at >= datetime('now','-30 minutes')
           ORDER BY created_at DESC LIMIT 20''',
        (b['id'],)
    ).fetchall()
    # 🛡️ mensagens pagas esperando o OK do gerente (cantada / pedido de namoro)
    aguardando_ok = conn.execute(
        '''SELECT * FROM pubshow_pedidos WHERE business_id=? AND status="aguardando_ok"
           ORDER BY created_at ASC LIMIT 20''',
        (b['id'],)
    ).fetchall()
    total_pedidos_bar = conn.execute(
        'SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=?', (b['id'],)
    ).fetchone()[0]

    # ── Receita acumulada (casa, só pago confirmado — soma única) ────────────
    receita_semana = somar_pagos(conn, b['id'], _7d, _hoje, 'casa')['total']
    receita_mes    = somar_pagos(conn, b['id'], _hoje.replace(day=1), _hoje, 'casa')['total']
    receita_total  = somar_pagos(conn, b['id'], conta='casa')['total']
    # Últimos 7 dias para mini-gráfico
    receita_7dias = {str(d): v['total'] for d, v in
                     sorted(somar_pagos(conn, b['id'], _7d, _hoje, 'casa', por='dia').items())}

    assinatura = conn.execute(
        'SELECT * FROM pubshow_assinaturas WHERE business_id=?', (b['id'],)
    ).fetchone()
    # 16/10/26 (V3-03): o passo 3 ("seu 1º pedido chegou") só conta pedido que chegou na fila/TV —
    # PIX abandonado (aguardando_pix), recusado ou parado na moderação não fecha o tutorial
    pedidos_que_chegaram = conn.execute(
        "SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status IN ('pendente','exibido','dispensado')",
        (b['id'],)
    ).fetchone()[0]
    tutorial = _tutorial_do_bar(conn, dict(b), pedidos_que_chegaram)   # checklist do topo de "Hoje" (B6)
    conn.close()
    import json as _json
    bd = dict(b)
    bd['total_pedidos'] = total_pedidos_bar
    try:    bloqueados_parsed = _json.loads(bd.get('tipos_bloqueados') or '[]')
    except: bloqueados_parsed = []
    try:    precos_parsed = _json.loads(bd.get('precos_custom') or '{}')
    except: precos_parsed = {}
    # VDA-01 (16/10/26): no modo Efí o preço salvo acima do teto aparece JÁ cortado (R$ 50). Assim o campo nunca
    # nasce acima do max (o navegador travaria o "Salvar" do formulário inteiro) e o dono vê o que o cliente paga.
    # 17/10/26 (Lote D, críticos CD2/CD3): precos_salvos = o que está no banco, sem corte. O campo leva ele em
    # data-salvo e o "salva sozinho" manda ele enquanto o dono não mexer naquele preço: salvar outra chave nunca mais
    # grava R$ 50 por cima do R$ 120 (nem R$ 5 por cima dos R$ 4 do Garage). A cobrança corta do mesmo jeito.
    # precos_cortados = [{'k', 'nome', 'antes', 'depois', 'motivo': 'teto'|'piso'}]: o aviso diz QUAL momento mudou.
    precos_salvos = dict(precos_parsed) if isinstance(precos_parsed, dict) else {}
    precos_cortados = []
    _teto_painel = _preco_teto(bd)
    if isinstance(precos_parsed, dict):
        for _k, _v in list(precos_parsed.items()):
            if _k not in TIPOS_PEDIDO or _k in TIPOS_SEMPRE_GRATIS:
                continue
            try:
                _f = float(_v)
            except (TypeError, ValueError):
                continue
            _nome = TIPOS_PEDIDO[_k].get('nome', _k)
            if _teto_painel is not None and _f > _teto_painel:
                precos_parsed[_k] = _teto_painel
                precos_cortados.append({'k': _k, 'nome': _nome, 'antes': _f, 'depois': _teto_painel, 'motivo': 'teto'})
            elif 0 < _f < _piso_do_bar(bd):
                precos_cortados.append({'k': _k, 'nome': _nome, 'antes': _f, 'depois': _piso_do_bar(bd), 'motivo': 'piso'})
    try:    temas_habilitados = _json.loads(bd.get('temas_habilitados') or 'null')
    except: temas_habilitados = None
    try:    anuncios_parsed = _json.loads(bd.get('anuncios_json') or '[]')
    except: anuncios_parsed = []
    try:    happy_hour = _json.loads(bd.get('happy_hour_json') or 'null')
    except: happy_hour = None
    try:    generos_jukebox = _json.loads(bd.get('generos_jukebox') or 'null')
    except: generos_jukebox = None
    hh_desconto_atual = _happy_hour_desconto(bd)
    # Canais que o plano do bar permite ver/selecionar na TV
    canais_plano = _plano_canais_permitidos(bd)
    # Lote C, fatia b (16/10/26): card "💰 Meu dinheiro" (só Efí ligada pro bar E conta conectada) e o retorno
    # do formulário do passo 4 (?conta_efi=ok|<erro>). Falhou a leitura do livro-caixa = sem card, nunca 500.
    _efi_bar = _efi_para(bd)
    _efi_sem_conta = _efi_bar and not _efi_conta_ok(bd)
    # 17/10/26 (caminho único): bar da lista sem a conta aceita vende pela PONTE e vê o passo 4 pra conectar a conta
    # (aí vira split). efi_conta_salva = salvou, mas a Efí ainda não aceitou (o robô confere de novo a cada 10 min).
    _efi_lista_bar = _efi_lista(bd)
    efi_ponte = _ponte_efi_para(bd)
    efi_conta_salva = bool(efi_ponte and _efi_conta_ok(bd))
    # CC2 (16/10/26), só Efí: quanto dos momentos do mês paga a mensalidade (texto do passo 4: "cai na hora" só
    # depois disso) e, nas cantadas/namoros esperando o OK, o que o cliente pagou e o que já caiu na conta do bar
    # (texto do "Não mostrar"). Falhou a leitura = textos genéricos, nunca 500.
    efi_mens_cent, efi_ok_info = 0, {}
    if _efi_lista_bar:
        try:
            _cm = _pp_conn()
            try:
                if not (_tem_assinatura_ativa(bd['id']) or not bd.get('plano_ativo', 1)):
                    efi_mens_cent = pubshow_pay.plano_do_bar(_cm, bd)
                _ids_ok = [int(p['id']) for p in aguardando_ok] if _efi_bar else []
                if _ids_ok:
                    efi_ok_info = {r[0]: {'valor_cent': int(r[1]), 'bar_cent': int(r[2]) if r[3] else 0}
                                   for r in _cm.execute(
                                       'SELECT pedido_id, valor_cent, bar_cent, split_ok FROM pubshow_pay_cobrancas '
                                       'WHERE pedido_id IN (%s)' % ','.join('?' * len(_ids_ok)), _ids_ok)}
            finally:
                _cm.close()
        except Exception as _ex_cm:
            log.warning('[PUBSHOW] textos da Efí do bar %s não leram: %s', bd['id'], _ex_cm)
    meu_dinheiro = None
    if _efi_bar and not _efi_sem_conta:
        try:
            meu_dinheiro = _meu_dinheiro(bd)
        except Exception as _ex_md:
            log.warning('[PUBSHOW] Meu dinheiro do bar %s não leu: %s', bd['id'], _ex_md)
    _ce = (request.args.get('conta_efi') or '').strip()
    conta_efi_msg = ('' if not (_efi_lista_bar and _ce) else 'ok' if _ce in _OK_CONTA_EFI
                     else _erro_conta_efi(_ce, bd))
    conta_efi_ok_txt = _OK_CONTA_EFI.get(_ce, '') if conta_efi_msg == 'ok' else ''
    if conta_efi_msg == 'ok' and efi_ponte:
        conta_efi_ok_txt = _OK_CONTA_EFI_PONTE   # a Efí ainda não aceitou: o bar segue na ponte por enquanto
    # VCb-01 (16/10/26): a conta passou no passo 4 mas a Efí recusou o split no último momento pago (todo momento
    # dá "Pagamento indisponível"): aviso fixo no topo de Hoje + o formulário do passo 4 reaberto pra corrigir
    efi_split_recusado = False
    if _efi_bar and not _efi_sem_conta:
        try:
            _cs = _pp_conn()
            try:
                efi_split_recusado = bool(pubshow_pay.split_recusado(_cs, bd['id']))
            finally:
                _cs.close()
        except Exception as _ex_sr:
            log.warning('[PUBSHOW] split recusado do bar %s não lido: %s', bd['id'], _ex_sr)
    if efi_split_recusado:
        conta_efi_msg, conta_efi_ok_txt = ('' if conta_efi_msg == 'ok' else conta_efi_msg), ''
        if tutorial is None:
            tutorial = {'tv': True, 'qr': True, 'pedido': True, 'conta': False, 'feitos': 3, 'total': 4}
        elif tutorial.get('conta'):
            tutorial = dict(tutorial, conta=False, feitos=max(0, (tutorial.get('feitos') or 1) - 1))
    # Lote D (16/10/26): A3 aviso/pausa (só Efí), A5 faixa do "dia do pagamento" (os 2 modos), A4 "o PIX caiu" do
    # Recusar PIX (só Efí) e o teto de R$ 50 no formulário de preços (só Efí). Sem nada disso o HTML fica igual.
    efi_pausa = _efi_pausa(bd) if _efi_bar else None
    if efi_pausa and not (efi_pausa['pausado'] or efi_pausa['aviso']):
        efi_pausa = None
    faixa_semana = _faixa_semana(bd['id'])
    musicas_bloqueadas, ultimo_repasse = _painel_bloqueadas_e_repasse(bd, _efi_bar)
    repasse_pedido = _painel_pedido_repasse(bd, _efi_bar)
    return render_template('pubshow/painel.html',
                           musicas_bloqueadas=musicas_bloqueadas,   # painel por assunto (17/10/26): aba 🎵 Estilo
                           ultimo_repasse=ultimo_repasse,           # aba 💰 Dinheiro ("quando cai na sua conta")
                           repasse_pedido=repasse_pedido,           # aba 💰 Dinheiro: "Pedir meu repasse" no WhatsApp
                           gratis_min=GRATIS_INTERVALO_MIN,         # aba ✨ Pedidos: "1 música grátis a cada N min"
                           eleicao=_janela_eleitoral(),             # 18/10/26: 🛡️ "todo recado passa por aqui" (24-25/10)
                           taxa_gorjeta=_taxa_pix_bar(bd, 'gorjeta'),   # o selo da gorjeta = o que o cliente paga
                           # 🔇 modo esporte (outra sessão): a chave só aparece com a coluna E a rota no ar juntas
                           rota_modo_esporte=_rota_modo_esporte(),
                           meu_dinheiro=meu_dinheiro, conta_efi_msg=conta_efi_msg, brl_cent=_brl_cent,
                           conta_efi_ok_txt=conta_efi_ok_txt, efi_split_recusado=efi_split_recusado,
                           efi_mens_cent=efi_mens_cent, efi_ok_info=efi_ok_info,
                           efi_mens_pct=(pubshow_pay.mensal_pct() if _efi_lista_bar else 0),
                           efi_pausa=efi_pausa, faixa_semana=faixa_semana,
                           pix_caiu=(_efi_bar and (request.args.get('pix_caiu') or '').isdigit()),
                           pix_conferir_erro=(_efi_bar and request.args.get('pix_conferir') == 'erro'),
                           devolucao_suporte=(request.args.get('devolucao') == 'suporte'),   # 17/10/26 (decisão 3b)
                           preco_teto=_preco_teto(bd),
                           # VDB-02 (16/10/26): no modo Efí o "o cliente paga" dos Ajustes usa o MESMO R$ 1 do app do
                           # celular e da cobrança (pubshow_pay.conv_cent_para); no Asaas fica o do context_processor
                           **({'TAXA_SERVICO': pubshow_pay.conv_cent_para('flash') / 100} if _efi_bar else {}),
                           # Lote D A1: o preço DESTE bar (fundador: o travado) no "Assinar R$ X/mês"
                           PLANO=_plano_do_bar(bd),
                           b=bd, canais=canais_plano,
                           estilos_bar=ESTILOS_BAR,   # Lote D C (16/10/26): os 5 botões do Estilo do bar
                           no_ar_txt=_no_ar_txt(bd), yt_txt=_yt_txt(bd),   # 20/10/26: 📺 No ar e o Pedir do YouTube
                           pedidos_hoje=_pedidos_com_hora_local([dict(p) for p in pedidos_hoje]),
                           total_hoje=total_hoje, momentos_hoje_n=momentos_hoje_n,
                           # 17/10/26 (decisão 6): sem teste grátis (2º bar do CPF/CNPJ) ou teste já vencido
                           teste_acabou=((bd.get('trial_ends') or '')[:19] <= datetime.now().isoformat()[:19]),
                           segundo_bar=_segundo_bar_do_doc(bd),   # crítico leigo A1
                           gorjeta_hoje=gorjeta_hoje, gorjeta_hoje_n=gorjeta_hoje_n,
                           gorjeta_7dias=[dict(r) for r in gorjeta_7dias],
                           # verificador B12: no bar da ponte Efí o furar a fila padrão é R$ 3 (o painel mostra o mesmo)
                           tipos_config=({k: (dict(v, preco=PRECO_FLASH_EFI) if k == 'flash' else
                                              dict(v, preco=PRECO_PULAR_EFI) if k == 'pular_espera' else v)
                                          for k, v in _tipos_config(bd).items()} if _ponte_efi_para(bd) else _tipos_config(bd)),
                           fila=_pedidos_com_hora_local([dict(f) for f in fila]),
                           aguardando_pix=_pedidos_com_hora_local([dict(p) for p in aguardando_pix]),
                           aguardando_ok=_pedidos_com_hora_local([dict(p) for p in aguardando_ok]),
                           tipos=TIPOS_PEDIDO,
                           planos=PLANOS,
                           bloqueados_parsed=bloqueados_parsed,
                           precos_parsed=precos_parsed, precos_cortados=precos_cortados,
                           precos_salvos=precos_salvos,   # 17/10/26 (CD2/CD3): o salvo, sem corte (data-salvo)
                           piso_preco=_piso_do_bar(bd),   # 17/10/26 (CD3): o "o cliente paga" segue o piso da cobrança
                           temas_habilitados=temas_habilitados,
                           anuncios_parsed=anuncios_parsed,
                           happy_hour=None,               # 17/10/26 (decisão 2): sem happy hour, o card some
                           hh_desconto_atual=0,
                           generos_jukebox=generos_jukebox,
                           canais_todos=CANAIS,
                           plano_max_anuncios=_plano_max_anuncios(bd),
                           tutorial=tutorial,
                           tv_sinal=_tv_sinal(bd),
                           tipos_gratis=TIPOS_SEMPRE_GRATIS,
                           receita_semana=receita_semana,
                           receita_mes=receita_mes,
                           receita_total=receita_total,
                           receita_7dias=receita_7dias,
                           now_brt=datetime.utcnow() - timedelta(hours=3),
                           timedelta=timedelta,
                           assinatura=dict(assinatura) if assinatura else None,
                           asaas_ativo=_gateway_ativo(bd),   # Asaas, ponte Efí ou split: PIX confirma sozinho
                           # Lote C (16/10/26): Efí ligada pro bar (link "Meu dinheiro") e, sem a conta Efí
                           # conectada, o aviso de que os momentos pagos estão indisponíveis (passo 4)
                           efi=_efi_bar,
                           efi_sem_conta=_efi_sem_conta,
                           efi_passo4=_efi_lista_bar, efi_ponte=efi_ponte, efi_conta_salva=efi_conta_salva,
                           promo_ativa=bool(bd.get('promo_msg') and bd.get('promo_expira') and bd['promo_expira'] > datetime.now().strftime('%Y-%m-%dT%H:%M:%S')))


def _rota_modo_esporte():
    """Painel por assunto (17/10/26): a chave "🔇 Vídeo sem música → sem som" (sessão "modo esporte") só aparece quando a
    rota dela existe neste deploy — coluna sem rota = chave que sempre falha."""
    from flask import current_app
    try:
        return any(r.rule == '/pubshow/painel/tv-mudo-sem-musica' for r in current_app.url_map.iter_rules())
    except Exception:
        return False


def _painel_bloqueadas_e_repasse(bd, efi_bar):
    """Painel por assunto (17/10/26). Só leitura; falhou = lista vazia / sem repasse, nunca 500.
    → ([{'id', 'titulo'}] das músicas do "🚫 nunca mais", mais nova em cima (até 50),
       {'valor_bar', 'valor_gorjeta', 'quando'} do último repasse manual do bar ou None — só fora da Efí do split,
       onde o dinheiro cai na conta do PubShow e o suporte repassa)."""
    ids = list(reversed(_videos_bloqueados(bd)))[:50]
    lista, rep = [{'id': y, 'titulo': ''} for y in ids], None
    try:
        conn = get_pubshow_db()
        try:
            if ids:
                q = ','.join('?' * len(ids))
                tit = {r[0]: r[1] for r in conn.execute(
                    f'SELECT youtube_id, titulo_pedido FROM pubshow_pedidos WHERE business_id=? AND youtube_id IN ({q}) '
                    "AND TRIM(COALESCE(titulo_pedido,''))<>'' ORDER BY id", [bd['id']] + ids)}
                tit.update({r[0]: (r[1] + (' - ' + r[2] if r[2] else '')) for r in conn.execute(
                    f'SELECT youtube_id, titulo, artista FROM pubshow_videos WHERE youtube_id IN ({q})', ids)})
                for m in lista:
                    m['titulo'] = tit.get(m['id']) or ''
            if not efi_bar:
                r = conn.execute('SELECT valor_bar, valor_gorjeta, ate_em, criado_em FROM pubshow_repasses_manuais '
                                 'WHERE business_id=? ORDER BY id DESC LIMIT 1', (bd['id'],)).fetchone()
                if r:
                    rep = {'valor_bar': float(r[0] or 0), 'valor_gorjeta': float(r[1] or 0),
                           'quando': _repasse_brt(r[3] or r[2], '%d/%m')}
        finally:
            conn.close()
    except Exception as ex:
        log.warning('[PUBSHOW] painel: bloqueadas/repasse do bar %s não leram: %s', bd.get('id'), ex)
    return lista, rep


def _painel_pedido_repasse(bd, efi_bar):
    """20/10/26 (Diogo: "como sacar vai ser manual, poderia ter aqui o pedido via WhatsApp, e ele já me manda qual é o
    bar, o pix do bar e o valor somado do dia X ao Y"): o que o bar tem a receber — a MESMA conta da tela 💸 Repasses
    (só PIX com prova, ainda não marcado como repassado) — e o link do WhatsApp do suporte com tudo escrito.
    Bar no split Efí não tem repasse (cai na conta dele). Erro aqui nunca derruba o painel."""
    if efi_bar:
        return None
    from urllib.parse import quote as _quote
    try:
        conn = get_pubshow_db()
        try:
            a = _utc_txt(_utc_agora())
            d = _repasse_somar(conn, 'AND repasse_manual_id IS NULL AND datetime(created_at) <= datetime(?)', [a])
            d = d.get(bd['id']) or {}
            w, wp = _repasse_where_pago()
            ini = conn.execute(f'SELECT MIN(created_at) FROM pubshow_pedidos WHERE business_id=? AND repasse_manual_id IS NULL '
                               f'AND datetime(created_at) <= datetime(?) AND {w}', [bd['id'], a] + wp).fetchone()[0]
        finally:
            conn.close()
        bar, gorj = round(float(d.get('bar') or 0), 2), round(float(d.get('gorj') or 0), 2)
        total = round(bar + gorj, 2)
        reais = lambda v: f'R$ {v:.2f}'.replace('.', ',')
        de, ate = (_repasse_brt(ini, '%d/%m') if ini else ''), _repasse_brt(a, '%d/%m')
        chave = (bd.get('pix_key') or '').strip()
        titular = (bd.get('pix_nome_recebedor') or '').strip()
        linhas = ['💸 Pedido de repasse PubShow',
                  f'Bar: {bd.get("nome") or ""} (código {bd.get("code") or ""})',
                  f'Período: {de} a {ate}' if de else f'Até {ate}',
                  f'Momentos (caixa da casa): {reais(bar)}',
                  f'Gorjetas da equipe: {reais(gorj)}',
                  f'Total: {reais(total)}',
                  f'Chave PIX: {chave}' if chave else 'Chave PIX: ainda não cadastrada no painel']
        if titular:
            linhas.append(f'Titular: {titular}')
        return {'bar': bar, 'gorj': gorj, 'total': total, 'de': de, 'ate': ate,
                'bar_fmt': reais(bar), 'gorj_fmt': reais(gorj), 'total_fmt': reais(total),
                'chave': chave, 'titular': titular,
                'wa_url': f'https://wa.me/{SUPORTE_WA}?text=' + _quote('\n'.join(linhas))}
    except Exception as ex:
        log.warning('[PUBSHOW] painel: pedido de repasse do bar %s não montou: %s', bd.get('id'), ex)
        return None


# ── Lote C (16/10/26): "Meu dinheiro" e a conta Efí do bar (só com a Efí ligada pro bar) ─────────────────
def _mes_mais(mes, n):
    """'2026-10' + n meses (n pode ser negativo)."""
    a, m = int(mes[:4]), int(mes[5:7]) - 1 + n
    return f'{a + m // 12:04d}-{m % 12 + 1:02d}'


def _utc_para_brt(ts):
    try:
        return datetime.strptime((ts or '')[:19], '%Y-%m-%d %H:%M:%S') - timedelta(hours=3)
    except ValueError:
        return None


def _meu_dinheiro(b, mes=None):
    """Lote C, fatia b (16/10/26): tudo que a tela "Meu dinheiro" (e o card do painel) mostra, lido do
    livro-caixa do pubshow_pay. Só com a Efí ligada pro bar. Não grava nada. Tudo em centavos."""
    atual = pubshow_pay.competencia()
    if not (mes and re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', mes)):
        mes = atual
    conn = _pp_conn()
    try:
        f = pubshow_pay.fechamento_mes(conn, b, mes)   # só relatório (split-only)
        ex = pubshow_pay.extrato(conn, b['id'], mes)
        conta = pubshow_pay.conta_do_bar(conn, b['id'])
        estornados = {r[0] for r in conn.execute(
            "SELECT pedido_id FROM pubshow_ledger WHERE business_id=? AND tipo='estorno' AND pedido_id IS NOT NULL",
            (b['id'],))}
        pids = sorted({l['pedido_id'] for l in ex['linhas'] if l['pedido_id']})
        # CC2 (16/10/26): momento recusado com a devolução ainda em andamento (o robô fecha em até 60 s, ou a Efí
        # demora) ainda está nas somas: a tela diz quanto está voltando pro cliente, em vez de somar calada
        em_devolucao = sum(int(r[0] or 0) for r in conn.execute(
            '''SELECT item_cent FROM pubshow_pay_cobrancas WHERE business_id=? AND competencia=?
                 AND (status='devolvendo' OR (status='paga' AND estornar=1))''', (b['id'], mes)))
        cobs = {}
        if pids:
            cobs = {r['pedido_id']: dict(r) for r in conn.execute(
                'SELECT pedido_id, tipo, recupera_cent FROM pubshow_pay_cobrancas WHERE pedido_id IN (%s)'
                % ','.join('?' * len(pids)), pids)}
    finally:
        conn.close()
    hoje = _hoje_brt()
    # VCa-07 (16/10/26): o que caiu DE VERDADE na conta Efí do bar = os repasses do split (rsp) do mês, não o
    # total dos momentos (a mensalidade e a compensação saem antes). E o "Hoje" não lista momento estornado.
    caiu = {c: -sum(l['valor_cent'] for l in ex['linhas'] if l['tipo'] == 'repasse_split' and l['conta'] == c)
            for c in ('casa', 'gorjeta')}
    hoje_linhas = [l for l in ex['linhas'] if l['tipo'] == 'credito' and l['pedido_id'] not in estornados
                   and (_utc_para_brt(l['criado_em']) or datetime.min).date() == hoje]
    hoje_cent = {c: sum(l['valor_cent'] for l in hoje_linhas if l['conta'] == c) for c in ('casa', 'gorjeta')}

    # Extrato simples: 1 linha por momento/gorjeta (o que entrou, o que pagou a mensalidade, o que caiu na conta),
    # estorno como linha própria (pode ser de um momento do mês passado) e ajuste manual. Mais novo em cima.
    por_pedido, outros = {}, []
    for l in ex['linhas']:
        if l['pedido_id'] and l['conta'] in ('casa', 'gorjeta'):
            por_pedido.setdefault(l['pedido_id'], []).append(l)
        elif l['tipo'] == 'ajuste' and l['conta'] in ('casa', 'gorjeta'):
            outros.append(l)
    extrato_simples = []
    for pid, ls in por_pedido.items():
        tipo = (cobs.get(pid) or {}).get('tipo') or ''
        t = TIPOS_PEDIDO.get(tipo, {})
        _n = re.sub(r'^[^\w]+|[^\w!?.)]+$', '', t.get('nome') or '').strip()   # "🍻 Brinde Geral! 🍻" → "Brinde Geral!"
        nome = (_n or ('Gorjeta' if ls[0]['conta'] == 'gorjeta' else 'Momento')) + f' #{pid}'
        cred = [l for l in ls if l['tipo'] == 'credito']
        if cred:
            item = cred[0]['valor_cent']
            mens = -sum(l['valor_cent'] for l in ls if l['tipo'] == 'mensalidade')
            bar = -sum(l['valor_cent'] for l in ls if l['tipo'] == 'repasse_split')
            partes = []
            if bar:
                partes.append(f'{_brl_cent(bar)} caiu na conta Efí do bar')
            if mens:
                partes.append(f'{_brl_cent(mens)} pagou a mensalidade')
            resto = item - mens - bar
            if resto > 0:   # CC2 (16/10/26): sem jargão (era "acertou estorno anterior")
                partes.append(f'{_brl_cent(resto)} cobriu um momento devolvido antes')
            if pid in estornados:
                partes.append('depois devolvido ao cliente')
            dt = _utc_para_brt(cred[0]['criado_em'])
            extrato_simples.append({'id': cred[0]['id'], 'emoji': t.get('emoji', '💰'), 'nome': nome,
                                    'conta': cred[0]['conta'], 'valor_cent': item,
                                    'quando': dt.strftime('%d/%m %H:%M') if dt else '',
                                    'dia': dt.strftime('%d/%m') if dt else '', 'caiu_cent': bar,
                                    'detalhe': ' · '.join(partes)})
        for l in ls:
            if l['tipo'] == 'estorno':
                dt = _utc_para_brt(l['criado_em'])
                extrato_simples.append({'id': l['id'], 'emoji': '↩️', 'nome': f'Devolvido ao cliente ({nome})',
                                        'conta': l['conta'], 'valor_cent': l['valor_cent'],
                                        'quando': dt.strftime('%d/%m %H:%M') if dt else '',
                                        'dia': dt.strftime('%d/%m') if dt else '', 'caiu_cent': 0,
                                        'detalhe': 'O PIX voltou pro cliente (a devolução saiu da conta do PubShow).'})
    for l in outros:
        dt = _utc_para_brt(l['criado_em'])
        extrato_simples.append({'id': l['id'], 'emoji': '✍️', 'nome': 'Ajuste', 'conta': l['conta'],
                                'valor_cent': l['valor_cent'], 'quando': dt.strftime('%d/%m %H:%M') if dt else '',
                                'dia': dt.strftime('%d/%m') if dt else '', 'caiu_cent': 0, 'detalhe': ''})
    extrato_simples.sort(key=lambda e: e['id'], reverse=True)
    # CC2 (16/10/26): com 200 momentos no mês, 1 linha por momento vira rolagem sem fim. Agrupa por dia (mais novo
    # em cima): total do dia (o que entrou menos o devolvido) e quanto caiu na conta; os detalhes abrem ao tocar.
    extrato_dias = []
    for e in extrato_simples:
        if not extrato_dias or extrato_dias[-1]['dia'] != e['dia']:
            extrato_dias.append({'dia': e['dia'], 'total_cent': 0, 'caiu_cent': 0, 'n': 0, 'itens': []})
        d = extrato_dias[-1]
        d['itens'].append(e)
        d['total_cent'] += e['valor_cent']
        d['caiu_cent'] += e['caiu_cent']
        d['n'] += 1 if (e['valor_cent'] > 0 and e['emoji'] != '✍️') else 0

    _mens_asaas = _tem_assinatura_ativa(b['id'])
    # Seletor de mês: do mês do cadastro (no máximo 12 pra trás) até o mês atual
    ini = _utc_para_brt(b.get('created_at') or '')
    primeiro = max(ini.strftime('%Y-%m') if ini else atual, _mes_mais(atual, -12))
    return {'f': f, 'ex': ex, 'mes': mes, 'conta': conta, 'caiu_cent': caiu, 'hoje_linhas': hoje_linhas,
            'hoje_cent': hoje_cent, 'extrato_simples': extrato_simples, 'extrato_dias': extrato_dias,
            'em_devolucao_cent': em_devolucao,
            'recupera_cent': sum((c.get('recupera_cent') or 0) for p, c in cobs.items() if p in por_pedido),
            'mes_atual': mes == atual, 'mes_nome': f'{_MESES_PT[int(mes[5:7]) - 1]} {mes[:4]}',
            'mes_ant': _mes_mais(mes, -1) if mes > primeiro else '',
            'mes_prox': _mes_mais(mes, 1) if mes < atual else '',
            # VCa-02: bar que ainda paga a assinatura no Asaas não tem mensalidade tirada no split (jukebox())
            'mens_no_asaas': _mens_asaas,
            # VCb-02 (16/10/26): o MESMO critério do jukebox() (sem_mensalidade): quem cancelou (plano_ativo=0) e segue
            # aberto até o fim do mês pago (ativo_ate) não tem mensalidade tirada dos momentos. Só no mês atual.
            'mens_mes_pago': bool(mes == atual and not _mens_asaas and not b.get('plano_ativo', 1))}


@pubshow_bp.route('/painel/dinheiro')
@pubshow_login_required
def painel_dinheiro():
    """Tela "Meu dinheiro" (§3.5 do PAGAMENTO_NOVO): o mês ao vivo, do livro-caixa. Não grava nada.
    ?mes=AAAA-MM (torto = mês atual). Lote C fatia b (16/10/26): a conta sai do _meu_dinheiro()."""
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')
    if not _efi_para(b):
        return redirect('/pubshow/painel')
    md = _meu_dinheiro(dict(b), (request.args.get('mes') or '').strip())
    return render_template('pubshow/dinheiro.html', b=dict(b), tipos=TIPOS_PEDIDO, brl_cent=_brl_cent, **md)


_ERROS_CONTA_EFI = {
    'titular_diferente_do_cadastro': 'A conta Efí tem que estar no MESMO CPF/CNPJ do cadastro do bar.',
    # CC1 X7 (16/10/26): trocar a conta já conectada (e que a Efí não recusou) só pelo suporte
    'conta_ja_conectada': ('A conta Efí do bar já está conectada. Pra trocar de conta, chama o suporte no WhatsApp '
                           f'{SUPORTE_WA_FMT}.'),
    'documento_invalido': 'CPF/CNPJ inválido. Confere os números.',
    'conta_invalida': 'Número da conta Efí inválido. Ele aparece no app da Efí, em "Minha conta".',
    # VCb-01 (16/10/26): a Efí conferiu e recusou (conta de outro titular, número errado, PJ com outro CNPJ)
    'efi_recusou_conta': 'A Efí não achou essa conta nesse CPF/CNPJ. Confere o número da conta no app da Efí, em "Minha conta".',
    # 17/10/26 (decisão 3c): conta Efí (ou titular) com devolução em aberto em outro bar
    'conta_com_divida': ('Essa conta Efí tem devolução em aberto em outro bar. Pra conectar, fale com o suporte no '
                         f'WhatsApp {SUPORTE_WA_FMT}.'),
}
# VCb-01: os 2 "deu certo" do passo 4 — a Efí aceitou a conta, ou não respondeu agora (a conta fica salva)
# CC2 (16/10/26): "o 1º momento confirma" era falso — os primeiros momentos do mês pagam a mensalidade e não caem
# na conta do bar (o 1º split só sai depois). O texto agora aponta pro "Meu dinheiro", onde ele vê cada centavo.
_OK_CONTA_EFI = {
    'ok': '✅ Conta salva e aceita pela Efí. Você vê cada centavo em 💰 Meu dinheiro.',
    'salva': ('✅ Conta salva. A Efí não respondeu agora: se ela recusar a conta quando o dinheiro for cair nela, '
              'o painel avisa aqui. Você vê cada centavo em 💰 Meu dinheiro.'),
}
# 17/10/26 (caminho único): conta salva sem a Efí aceitar = o bar segue na PONTE até ela aceitar (o robô confere)
_OK_CONTA_EFI_PONTE = ('✅ Conta salva. A Efí não respondeu agora: até ela confirmar a conta, o dinheiro dos momentos '
                       'segue no repasse de toda semana. Quando ela confirmar, cada momento cai na hora na tua conta.')


def _erro_conta_efi(cod, b):
    """Texto do erro do passo 4. CC2 (16/10/26): no 'titular diferente', diz em qual documento está o cadastro
    (só o fim) e o que fazer: o dono não consegue trocar o CPF/CNPJ do cadastro sozinho."""
    if cod == 'titular_diferente_do_cadastro':
        doc = re.sub(r'[^0-9A-Z]', '', str((b['cpf_cnpj'] if b else '') or '').upper())
        if len(doc) in (11, 14):
            tipo = 'CPF' if len(doc) == 11 else 'CNPJ'
            return (f'A conta Efí tem que estar no MESMO CPF/CNPJ do cadastro do bar. Teu cadastro está no {tipo} '
                    f'terminado em {doc[-2:]} e essa conta é de outro documento: abra a conta Efí nesse {tipo}, ou '
                    f'chama o suporte no WhatsApp {SUPORTE_WA_FMT} se o cadastro está errado.')
    return _ERROS_CONTA_EFI.get(cod, 'Não deu pra salvar. Tenta de novo.')


def _avisar_conta_efi(b, conta, trocou):
    """CC1 X7 (16/10/26): avisa o dono no WhatsApp (e no e-mail) quando a conta Efí que recebe os momentos é
    conectada ou trocada. Em fundo, não segura a resposta. Se não foi ele, chama o suporte."""
    tel, nome, email = (b['telefone'] or ''), (b['nome'] or ''), (b['email'] or '')
    fim = str(conta or '')[-4:]
    acao = 'trocada' if trocou else 'conectada'
    txt = (f'💰 *PUBSHOW*\n\nOlá, {nome}! A conta Efí que recebe o dinheiro dos momentos foi {acao}: '
           f'conta final {fim}.\n\nSe não foi você, fale com o suporte agora: {SUPORTE_WA_FMT}.')
    def _envia():
        try:
            if tel:
                _pubshow_send_wa(tel, txt)
            if email:
                _pubshow_enviar_email(email, f'PUBSHOW — conta Efí do bar {acao}',
                                      f'<p>Olá, {_html_escape(nome)}!</p><p>A conta Efí que recebe o dinheiro dos '
                                      f'momentos foi {acao}: conta final <strong>{_html_escape(fim)}</strong>.</p>'
                                      f'<p>Se não foi você, fale com o suporte agora: WhatsApp {SUPORTE_WA_FMT}.</p>')
        except Exception as ex:
            log.warning('[PUBSHOW] aviso de conta Efí falhou: %s', ex)
    _threading.Thread(target=_envia, daemon=True).start()


@pubshow_bp.route('/painel/conta-efi', methods=['POST'])
@pubshow_login_required
def painel_conta_efi():
    """Passo 4 do tutorial (§3.6): o dono diz em qual conta Efí o dinheiro dos momentos cai. O titular tem que
    ser o CPF/CNPJ do cadastro (C7: conta de outra pessoa é recusada; a Efí confere de novo no split)."""
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')
    xhr = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not _efi_lista(b):   # 17/10/26 (caminho único): o bar da ponte conecta aqui (e vira split)
        if xhr:
            return jsonify({'ok': False, 'erro': 'Recebimento pela Efí ainda não está ligado pra este bar.'}), 400
        return redirect('/pubshow/painel')
    conn = _pp_conn()
    try:
        # CC1 X7 (16/10/26): a rota não tem CSRF (SameSite=None no app inteiro, A2.11) e trocava em silêncio uma conta
        # já conectada. Agora: conta que a Efí JÁ ACEITOU só troca pelo suporte. Salvar de novo só vale quando ela
        # nunca foi aceita (a Efí não respondeu no passo 4) ou quando a Efí recusou o split no último momento pago
        # (o aviso "Corrigir a conta" do VCb-01). E o dono recebe aviso no WhatsApp/e-mail a cada conta salva.
        _antes = pubshow_pay.conta_do_bar(conn, b['id'])
        if (_antes and pubshow_pay.conta_aceita(conn, b['id'])
                and not pubshow_pay.split_recusado(conn, b['id'])):
            r = {'erro': 'conta_ja_conectada'}
        else:
            # VCb-01 (16/10/26): conferir=True → a Efí confere a conta (config de split de verdade) ANTES de gravar
            r = pubshow_pay.salvar_conta_efi(conn, b['id'], request.form.get('conta_efi', ''),
                                             request.form.get('doc', ''), b['cpf_cnpj'],
                                             (request.form.get('titular_nome') or '').strip(), conferir=True)
    finally:
        conn.close()
    _c_rota = _cache_rota()
    if _c_rota is not None:
        _c_rota.pop(int(b['id']), None)   # a conta mudou: a rota (ponte/split) deste bar é lida de novo
    erro = '' if r.get('ok') else _erro_conta_efi(r.get('erro'), b)
    msg = '' if erro else ('ok' if r.get('conferida') else 'salva')
    if not erro:
        log.info('[PUBSHOW] Efí: bar %s conectou a conta Efí %s (%s)', b['id'], r.get('efi_conta'),
                 'aceita pela Efí' if msg == 'ok' else 'a Efí não respondeu')
        _avisar_conta_efi(b, r.get('efi_conta'), bool(_antes))
    if xhr:
        return jsonify({'ok': not erro, 'erro': erro, 'msg': msg}), (200 if not erro else 400)
    return redirect('/pubshow/painel?conta_efi=' + (msg if not erro else (r.get('erro') or 'erro')))


# ── Lote D A5 (16/10/26): "dia do pagamento" — toda segunda às 10h (Brasília), 1 aviso por bar por semana ──────
# WhatsApp do bar (e-mail de reserva) + faixa no painel até o dono fechar. Os 2 modos: na Efí o valor é o que CAIU na
# conta Efí do bar (repasses do split) e a frase termina com "Já está na sua conta Efí."; no Asaas, os momentos e as
# gorjetas pagos na semana, sem essa frase. Semana com R$ 0 não manda nada. A chave (bar, semana) impede mandar 2x.
_SEMANA_HORA_BRT = 10


def _aviso_semana_asaas() -> bool:
    """17/10/26 (decisão 7 do Diogo): no modo Asaas o dinheiro dos momentos está na conta do PubShow (repasse manual)
    e o Garage tem o acumulado desde 12/set pra acertar: o aviso "rendeu R$ X" fica DESLIGADO no Asaas (hoje = só o
    Garage) até o Diogo pôr PUBSHOW_AVISO_SEMANA_ASAAS=1. Na Efí (dinheiro já na conta do bar) segue ligado."""
    return (os.environ.get('PUBSHOW_AVISO_SEMANA_ASAAS') or '0').strip() == '1'


def _texto_semana(b, momentos_cent, gorjeta_cent, efi):
    txt = (f'🎉 Semana passada o PubShow rendeu {_brl_cent(momentos_cent)} pro {b["nome"]} e '
           f'{_brl_cent(gorjeta_cent)} de gorjeta pra equipe.')
    return txt + (' Já está na sua conta Efí.' if efi else '')


def _faixa_semana(bid):
    """Faixa do painel: o aviso da semana mais nova que o dono ainda não fechou ({'semana', 'texto'}) ou None."""
    try:
        conn = get_pubshow_db()
        try:
            r = conn.execute('''SELECT semana, texto FROM pubshow_semana_aviso WHERE business_id=? AND fechado_em IS NULL
                                ORDER BY semana DESC LIMIT 1''', (bid,)).fetchone()
        finally:
            conn.close()
        return dict(r) if r else None
    except Exception as ex:
        log.warning('[PUBSHOW] faixa da semana do bar %s não lida: %s', bid, ex)
        return None


def _manda_ao_dono(b, txt, assunto, titulo, os_dois=False):
    """WhatsApp do bar; o e-mail é a reserva (ou vai junto, com os_dois=True). Devolve o canal que saiu."""
    tel, email = (b.get('telefone') or '').strip(), (b.get('email') or '').strip()
    canal = ''
    try:
        if tel and _pubshow_send_wa(tel, txt):
            canal = 'whatsapp'
    except Exception as ex:
        log.warning('[PUBSHOW] WhatsApp pro bar %s falhou: %s', b.get('id'), ex)
    if email and (os_dois or not canal):
        try:
            if _pubshow_enviar_email(email, assunto, _email_html_base(
                    titulo, f'<p style="color:#e5e7eb;font-size:16px;line-height:1.6;margin:0">{_html_escape(txt)}</p>')):
                canal = f'{canal}+email' if canal else 'email'
        except Exception as ex:
            log.warning('[PUBSHOW] e-mail pro bar %s falhou: %s', b.get('id'), ex)
    return canal or 'nenhum'


def dia_do_pagamento(agora=None):
    """Roda o "dia do pagamento" (o robô chama a cada 10 min; só faz algo na segunda depois das 10h de Brasília).
    agora = UTC naive (testes). Devolve um resumo {'rodou', 'semana', 'enviados', 'zerados', 'ja'}."""
    agora = agora or datetime.utcnow()
    brt = agora - timedelta(hours=3)
    if brt.weekday() != 0 or brt.hour < _SEMANA_HORA_BRT:
        return {'rodou': False}
    seg, dom = brt.date() - timedelta(days=7), brt.date() - timedelta(days=1)   # a semana passada (seg a dom)
    ini_utc = datetime(seg.year, seg.month, seg.day) + timedelta(hours=3)
    fim_utc = datetime(brt.year, brt.month, brt.day) + timedelta(hours=3)
    semana = seg.isoformat()
    out = {'rodou': True, 'semana': semana, 'enviados': 0, 'zerados': 0, 'ja': 0, 'desligados': 0}
    conn = get_pubshow_db()
    try:
        bares = [dict(r) for r in conn.execute('SELECT * FROM pubshow_businesses WHERE COALESCE(suspenso,0)=0')]
        ja = {r[0] for r in conn.execute('SELECT business_id FROM pubshow_semana_aviso WHERE semana=?', (semana,))}
    finally:
        conn.close()
    for b in bares:
        if b['id'] in ja:
            out['ja'] += 1
            continue
        try:
            efi = _efi_para(b)
            modo = 'efi' if efi else 'asaas'
            if efi:
                cx = _pp_conn()
                try:
                    # VDA-03 (16/10/26): o líquido (momento devolvido não conta), igual ao Asaas (STATUS_PAGOS)
                    v = pubshow_pay.rendeu_no_periodo(cx, b['id'], ini_utc, fim_utc)
                    # 17/10/26 (Lote D, críticos CD2): na semana da migração o bar também vendeu pelo Asaas (antes de
                    # entrar na lista): soma os 2. Pedido com cobrança Efí fica de fora daqui (já está no da Efí).
                    va = {c: _somar_pagos_fora_da_efi(cx, b['id'], seg, dom, c) for c in ('casa', 'gorjeta')}
                finally:
                    cx.close()
                mc, gc = v['casa'] + va['casa'], v['gorjeta'] + va['gorjeta']
                # o dinheiro do Asaas não está na conta Efí: com ele na soma, a frase sai sem "Já está na sua conta Efí."
                efi = not (va['casa'] or va['gorjeta'])
                modo = 'efi' if efi else 'misto'
            else:
                if not _aviso_semana_asaas():
                    out['desligados'] += 1   # decisão 7: sem aviso no modo Asaas até o acerto do Garage
                    continue
                cx = get_pubshow_db()
                try:
                    mc = int(round(somar_pagos(cx, b['id'], seg, dom, 'casa')['total'] * 100))
                    gc = int(round(somar_pagos(cx, b['id'], seg, dom, 'gorjeta')['total'] * 100))
                finally:
                    cx.close()
            if mc + gc <= 0:
                out['zerados'] += 1          # semana com R$ 0: não manda nada (nem faixa)
                continue
            txt = _texto_semana(b, mc, gc, efi)
            cx = get_pubshow_db()
            try:
                n = cx.execute('''INSERT OR IGNORE INTO pubshow_semana_aviso
                                  (business_id, semana, modo, momentos_cent, gorjeta_cent, texto, criado_em)
                                  VALUES (?,?,?,?,?,?,datetime('now'))''',
                               (b['id'], semana, modo, mc, gc, txt)).rowcount
                cx.commit()
            finally:
                cx.close()
            if n != 1:                       # outro worker já reservou esta semana deste bar
                out['ja'] += 1
                continue
            canal = _manda_ao_dono(b, txt, 'PUBSHOW — sua semana', 'Sua semana no PubShow')
            cx = get_pubshow_db()
            try:
                cx.execute("UPDATE pubshow_semana_aviso SET canal=?, enviado_em=datetime('now') "
                           "WHERE business_id=? AND semana=?", (canal, b['id'], semana))
                cx.commit()
            finally:
                cx.close()
            out['enviados'] += 1
        except Exception as ex:
            log.error('[PUBSHOW] dia do pagamento do bar %s falhou: %s', b.get('id'), ex)
    return out


# ── Lote D A3 (16/10/26): aviso de pausa 7 dias antes (só modo Efí) ─────────────────────────────────────────────
def _texto_pausa(b, pz, sem_conta=False):
    if pz['pausado']:
        return ('⏸️ O PubShow está pausado neste bar: faz mais de 60 dias sem nenhum momento pago. Os momentos, a '
                f'música e a TV estão parados. Pra religar, fale com o suporte no WhatsApp {SUPORTE_WA_FMT}.')
    return (f'⚠️ Faz {pz["dias"]} dias que nenhum momento é pago no PubShow do {b["nome"]}. Se nenhum momento for '
            f'pago até {pz["pausa_em_brt"]}, o PubShow pausa neste bar (momentos, música e TV). '
            + ('Pra continuar, conecte a conta Efí do bar no passo 4 do painel ou fale com o suporte no WhatsApp '
               f'{SUPORTE_WA_FMT}.' if sem_conta else
               f'Pra continuar, é só vender um momento, ou fale com o suporte no WhatsApp {SUPORTE_WA_FMT}.'))


def _precos_acima_do_teto(b):
    """[(nome do momento, preço salvo)] dos preços salvos acima do teto da Efí (R$ 50), em ordem de TIPOS_PEDIDO."""
    import json as _json
    try:
        custom = _json.loads(b.get('precos_custom') or '{}')
    except Exception:
        return []
    if not isinstance(custom, dict):
        return []
    out = []
    for k, t in TIPOS_PEDIDO.items():
        if k in TIPOS_SEMPRE_GRATIS or k not in custom:
            continue
        try:
            v = float(custom[k])
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v > PRECO_TETO_EFI:
            out.append(((t.get('nome') or k).replace(t.get('emoji') or '', '').strip(), v))
    return out


def _avisar_precos_teto(b):
    """Lote D, críticos (17/10/26, CD2): bar que entrou na Efí com preço salvo acima de R$ 50 recebe 1 WhatsApp (e-mail
    de reserva) dizendo QUAIS momentos passaram a sair por R$ 50. 1 vez por bar (pubshow_meta). Só na Efí (ponte ou
    split: o teto vale nos 2 desde o caminho único, 17/10/26)."""
    if not _rota_efi(b):
        return False
    cortados = _precos_acima_do_teto(b)
    if not cortados:
        return False
    conn = get_pubshow_db()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS pubshow_meta (chave TEXT PRIMARY KEY, valor TEXT)')
        n = conn.execute("INSERT OR IGNORE INTO pubshow_meta (chave, valor) VALUES (?, datetime('now'))",
                         (f'aviso_teto_efi:{b["id"]}',)).rowcount
        conn.commit()
    finally:
        conn.close()
    if n != 1:
        return False
    lista = '; '.join(f'{nome}: era {_brl_cent(int(round(v * 100)))}' for nome, v in cortados)
    txt = (f'⚠️ PubShow do {b["nome"]}: no pagamento novo, cada momento custa no máximo '
           f'{_brl_cent(int(PRECO_TETO_EFI * 100))}. Estes passaram a sair por {_brl_cent(int(PRECO_TETO_EFI * 100))}: '
           f'{lista}. Pra mudar, abra o painel em 💰 Dinheiro. Dúvida? WhatsApp {SUPORTE_WA_FMT}.')
    _manda_ao_dono(b, txt, 'PUBSHOW — preços dos momentos', 'Preços dos momentos')
    return True


def avisos_pausa(agora=None):
    """Manda o aviso dos 7 dias antes da pausa (WhatsApp E e-mail), 1 vez por ciclo por bar. Só modo Efí.
    17/10/26 (Lote D, críticos CD2): também o aviso dos preços cortados no teto (1 vez por bar, _avisar_precos_teto)."""
    if not _efi_ligado():
        return {'rodou': False}
    out = {'rodou': True, 'avisados': 0}
    conn = get_pubshow_db()
    try:
        bares = [dict(r) for r in conn.execute('SELECT * FROM pubshow_businesses WHERE COALESCE(suspenso,0)=0')]
    finally:
        conn.close()
    hoje = datetime.now().strftime('%Y-%m-%d')
    for b in bares:
        _rota = _rota_efi(b)
        if not _rota:
            continue
        # VDA-05 (16/10/26): bar cancelado (plano_ativo=0 e sem mês pago valendo) já está fechado: não recebe aviso
        if not b.get('plano_ativo') and not ((b.get('ativo_ate') or '')[:10] >= hoje):
            continue
        try:
            # 17/10/26 (Lote D, críticos CD2): 1 WhatsApp (1 vez só) com os preços que viraram R$ 50 ao entrar na Efí
            if _avisar_precos_teto(b):
                out['teto'] = out.get('teto', 0) + 1
        except Exception as ex:
            log.error('[PUBSHOW] aviso dos preços cortados do bar %s falhou: %s', b.get('id'), ex)
        if _rota != 'split':
            continue   # caminho único (17/10/26): a pausa de 60 dias é só do split (o bar da ponte segue o Asaas)
        try:
            cx = _pp_conn()
            try:
                # robo=True: o robô marca que viu o bar na Efí (17/10/26, CD2: quem volta depois de 1 dia fora recomeça)
                pz = pubshow_pay.pausa_do_bar(cx, b, agora, robo=True)
                sem_conta = not pubshow_pay.conta_do_bar(cx, b['id'])
                mandar = (pz['aviso'] and not pz['avisado']
                          and pubshow_pay.marcar_aviso_pausa(cx, b['id'], pz['ref'], agora))
            finally:
                cx.close()
            if mandar:
                # VDA-05: sem conta Efí ele não consegue "vender um momento" (momentos pagos ficam indisponíveis)
                _manda_ao_dono(b, _texto_pausa(b, pz, sem_conta), 'PUBSHOW — aviso de pausa', 'Aviso de pausa',
                               os_dois=True)
                out['avisados'] += 1
        except Exception as ex:
            log.error('[PUBSHOW] aviso de pausa do bar %s falhou: %s', b.get('id'), ex)
    return out


def rodar_avisos(agora=None):
    """1 rodada do robô de avisos do Lote D: dia do pagamento (os 2 modos) + aviso de pausa (só Efí)."""
    return {'semana': dia_do_pagamento(agora), 'pausa': avisos_pausa(agora)}


_AVISOS_INICIADO = False


def iniciar_avisos(intervalo_min=10, delay_inicial_seg=180):
    """Thread daemon do Lote D (a cada 10 min). 1 por processo; vários workers não mandam 2x (a chave no banco).
    Desliga com PUBSHOW_AVISOS=0 (e com PUBSHOW_FAXINEIRO=0, a chave que os testes usam pros robôs de fundo)."""
    global _AVISOS_INICIADO
    if _AVISOS_INICIADO:
        return False
    if os.environ.get('PUBSHOW_AVISOS', '1') == '0' or os.environ.get('PUBSHOW_FAXINEIRO', '1') == '0':
        log.info('[PUBSHOW] robô de avisos desligado (PUBSHOW_AVISOS/PUBSHOW_FAXINEIRO=0)')
        return False
    _AVISOS_INICIADO = True

    def _loop():
        _time.sleep(delay_inicial_seg + random.randint(0, 60))
        while True:
            try:
                r = rodar_avisos()
                if (r.get('semana') or {}).get('enviados') or (r.get('pausa') or {}).get('avisados'):
                    log.info('[PUBSHOW] avisos: %s', r)
            except Exception as ex:
                log.error('[PUBSHOW] robô de avisos: %s', ex)
            _time.sleep(intervalo_min * 60)

    _threading.Thread(target=_loop, daemon=True, name='pubshow-avisos').start()
    log.info('[PUBSHOW] robô de avisos iniciado (a cada %d min)', intervalo_min)
    return True


@pubshow_bp.route('/painel/semana/fechar', methods=['POST'])
@pubshow_login_required
def painel_semana_fechar():
    """Lote D A5: o dono fechou a faixa do "dia do pagamento" (ela some até a próxima segunda)."""
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')
    conn = get_pubshow_db()
    conn.execute("UPDATE pubshow_semana_aviso SET fechado_em=datetime('now') WHERE business_id=? AND fechado_em IS NULL",
                 (b['id'],))
    conn.commit(); conn.close()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/fila-json')
@pubshow_login_required
def painel_fila_json():
    """Retorna fila + aguardando_pix como JSON — usado pelo polling do painel sem recarregar a página."""
    b = _get_business()
    if not b:
        return jsonify({'error': 'não autorizado'}), 401
    conn = get_pubshow_db()
    fila = conn.execute(
        '''SELECT id, nome_cliente, tipo, mensagem, valor, created_at, youtube_id, titulo_pedido,
                  pago_em, valor_cobrado
           FROM pubshow_pedidos WHERE business_id=? AND status="pendente"
           ORDER BY CASE tipo WHEN "vip" THEN 1 WHEN "flash" THEN 2 ELSE 3 END, created_at ASC, id ASC LIMIT 20''',   # crítico leigo A2: a ordem da TV
        (b['id'],)
    ).fetchall()
    aguardando = conn.execute(
        '''SELECT id, nome_cliente, tipo, mensagem, valor, created_at, youtube_id, titulo_pedido
           FROM pubshow_pedidos WHERE business_id=? AND status="aguardando_pix"
           AND created_at >= datetime('now','-30 minutes')
           ORDER BY created_at DESC LIMIT 20''',
        (b['id'],)
    ).fetchall()
    aguardando_ok_n = conn.execute(
        'SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND status="aguardando_ok"', (b['id'],)
    ).fetchone()[0]
    _hoje = _hoje_brt()   # mesma soma do painel (15/10/26) — só pago confirmado
    _casa = somar_pagos(conn, b['id'], _hoje, _hoje, 'casa')
    pedidos_hoje = _casa['total']
    gorjeta_hoje = somar_pagos(conn, b['id'], _hoje, _hoje, 'gorjeta')['total']
    conn.close()
    return jsonify({
        'fila': [dict(r) for r in fila],
        'aguardando_pix': [dict(r) for r in aguardando],
        'total_hoje': float(pedidos_hoje),
        'momentos_hoje_n': int(_casa['n']),   # 17/10/26 (decisão 1): "N momentos pagos" ao vivo
        'gorjeta_hoje': float(gorjeta_hoje),
        'aguardando_ok': int(aguardando_ok_n),
        'fila_count': len(fila),
        'tv_sinal': _tv_sinal(dict(b)),   # selo "TV ligada / sem sinal há X min" ao vivo (15/10/26)
    })


@pubshow_bp.route('/painel/canal', methods=['POST'])
@pubshow_login_required
def painel_canal():
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    canal = request.form.get('canal', 'rock')
    if canal in CANAIS:
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET canal_atual=? WHERE id=?', (canal, b['id']))
        conn.commit(); conn.close()
        session['pub_canal'] = canal
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/promo', methods=['POST'])
@pubshow_login_required
def painel_promo():
    """Inicia ou encerra uma Promoção Relâmpago na TV."""
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    acao = request.form.get('acao', 'iniciar')
    conn = get_pubshow_db()
    if acao == 'encerrar':
        conn.execute('UPDATE pubshow_businesses SET promo_msg=NULL, promo_expira=NULL WHERE id=?', (b['id'],))
    else:
        msg    = request.form.get('promo_msg', '').strip()[:80]
        emoji  = request.form.get('promo_emoji', '🍺').strip()[:4]
        try: minutos = int(request.form.get('promo_minutos', 10) or 10)
        except (ValueError, TypeError): minutos = 10
        minutos = max(1, min(minutos, 120))  # entre 1 e 120 min
        expira  = (datetime.now() + timedelta(minutes=minutos)).strftime('%Y-%m-%dT%H:%M:%S')
        if msg:
            conn.execute('UPDATE pubshow_businesses SET promo_msg=?, promo_expira=?, promo_emoji=? WHERE id=?',
                         (msg, expira, emoji, b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/push/subscribe', methods=['POST'])
@pubshow_login_required
def painel_push_subscribe():
    """Salva subscription Web Push do dispositivo do bar."""
    import json as _json
    b = _get_business()
    sub = request.json
    if not sub or 'endpoint' not in sub:
        return jsonify({'ok': False, 'erro': 'subscription inválida'}), 400
    conn = get_pubshow_db()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO pubshow_push_subscriptions (business_id, subscription) VALUES (?,?)',
            (b['id'], _json.dumps(sub))
        )
        conn.commit()
    except Exception as _e:
        log.warning('[Push] Erro ao salvar subscription: %s', _e)
    finally:
        conn.close()
    return jsonify({'ok': True})


@pubshow_bp.route('/painel/push/unsubscribe', methods=['POST'])
@pubshow_login_required
def painel_push_unsubscribe():
    """Remove subscription Web Push do dispositivo."""
    import json as _json
    b = _get_business()
    sub = request.json
    if not sub:
        return jsonify({'ok': False}), 400
    conn = get_pubshow_db()
    conn.execute(
        'DELETE FROM pubshow_push_subscriptions WHERE business_id=? AND subscription=?',
        (b['id'], _json.dumps(sub))
    )
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@pubshow_bp.route('/painel/push/test', methods=['POST'])
@pubshow_login_required
def painel_push_test():
    """Envia push de teste para confirmar que está funcionando."""
    b = _get_business()
    if not b: return jsonify({'ok': False}), 401
    _enviar_push_pedido(b['id'], '🧪', 'Teste', 'Sistema PUBSHOW', 0.0)
    return jsonify({'ok': True})


# ── Cupons de desconto ────────────────────────────────────────────────────────

@pubshow_bp.route('/api/validar-cupom/<codigo>')
def api_validar_cupom(codigo):
    """Valida um cupom e retorna seus benefícios (público — sem login)."""
    # VDB-01 (16/10/26): bar novo nasce na Efí (EFI_BARES='*') = sem teste grátis e sem desconto no 1º mês: nenhum
    # cupom vale (antes o PUBSHOW30 respondia "30 dias grátis" na tela que diz "sem mensalidade do bolso")
    if _efi_cadastro_novo():
        return jsonify({'ok': False, 'erro': 'Cupom não vale neste plano.'})
    conn = get_pubshow_db()
    try:
        c = conn.execute(
            "SELECT * FROM pubshow_cupons WHERE codigo=? COLLATE NOCASE AND ativo=1",
            (codigo.strip().upper(),)
        ).fetchone()
    finally:
        conn.close()
    if not c:
        return jsonify({'ok': False, 'erro': 'Cupom inválido ou expirado.'})
    c = dict(c)
    # Verifica validade por data
    if c.get('valido_ate') and c['valido_ate'] < datetime.now().isoformat():
        return jsonify({'ok': False, 'erro': 'Este cupom expirou.'})
    # Verifica limite de usos
    if c.get('max_usos') is not None and c['usos'] >= c['max_usos']:
        return jsonify({'ok': False, 'erro': 'Cupom esgotado.'})
    # Monta mensagem
    if c['tipo'] == 'trial':
        msg = f"🎉 {c['valor']} dias grátis (em vez de 7)"
    elif c['tipo'] == 'desconto_pct':
        msg = f"💸 {c['valor']}% de desconto no primeiro mês"
    else:
        msg = f"✅ Benefício aplicado: {c['descricao'] or c['codigo']}"
    return jsonify({'ok': True, 'tipo': c['tipo'], 'valor': c['valor'],
                    'descricao': c['descricao'], 'msg': msg})


def _aplicar_cupom(codigo: str) -> dict | None:
    """Aplica um cupom — incrementa uso atomicamente. M5 fix: sem race condition."""
    if not codigo:
        return None
    conn = get_pubshow_db()
    try:
        c = conn.execute(
            "SELECT * FROM pubshow_cupons WHERE codigo=? COLLATE NOCASE AND ativo=1",
            (codigo.strip().upper(),)
        ).fetchone()
        if not c:
            return None
        c = dict(c)
        if c.get('valido_ate') and c['valido_ate'] < datetime.now().isoformat():
            return None
        # M5 fix: UPDATE atômico com verificação de max_usos — evita race condition
        # Se max_usos for NULL (ilimitado) ou usos < max_usos, incrementa e retorna 1 row
        if c.get('max_usos') is not None:
            updated = conn.execute(
                "UPDATE pubshow_cupons SET usos=usos+1 WHERE id=? AND usos < max_usos",
                (c['id'],)
            ).rowcount
        else:
            updated = conn.execute(
                "UPDATE pubshow_cupons SET usos=usos+1 WHERE id=?",
                (c['id'],)
            ).rowcount
        if not updated:
            return None  # max_usos atingido por outro request simultâneo
        conn.commit()
        return c
    except Exception as _e:
        log.warning('[Cupom] Erro ao aplicar: %s', _e)
        return None
    finally:
        conn.close()


@pubshow_bp.route('/admin/cupons')
@_admin_required
def admin_cupons():
    conn = get_pubshow_db()
    cupons = [dict(c) for c in conn.execute(
        'SELECT * FROM pubshow_cupons ORDER BY criado_em DESC'
    ).fetchall()]
    conn.close()
    return render_template('pubshow/admin_cupons.html', cupons=cupons)


@pubshow_bp.route('/admin/cupons/criar', methods=['POST'])
@_admin_required
def admin_cupons_criar():
    codigo   = request.form.get('codigo', '').strip().upper()
    descricao= request.form.get('descricao', '').strip()
    tipo     = request.form.get('tipo', 'trial')
    valor    = int(request.form.get('valor', 30))
    max_usos = request.form.get('max_usos', '').strip()
    max_usos = int(max_usos) if max_usos else None
    valido_ate = request.form.get('valido_ate', '').strip() or None
    if not codigo:
        return redirect('/pubshow/admin/cupons')
    conn = get_pubshow_db()
    try:
        conn.execute(
            'INSERT OR IGNORE INTO pubshow_cupons (codigo, descricao, tipo, valor, max_usos, valido_ate) VALUES (?,?,?,?,?,?)',
            (codigo, descricao, tipo, valor, max_usos, valido_ate)
        )
        conn.commit()
    except Exception: pass
    finally: conn.close()
    return redirect('/pubshow/admin/cupons')


@pubshow_bp.route('/admin/cupons/toggle/<int:cid>', methods=['POST'])
@_admin_required
def admin_cupons_toggle(cid):
    conn = get_pubshow_db()
    atual = conn.execute('SELECT ativo FROM pubshow_cupons WHERE id=?', (cid,)).fetchone()
    if atual:
        conn.execute('UPDATE pubshow_cupons SET ativo=? WHERE id=?', (0 if atual[0] else 1, cid))
        conn.commit()
    conn.close()
    return redirect('/pubshow/admin/cupons')


@pubshow_bp.route('/admin/cupons/excluir/<int:cid>', methods=['POST'])
@_admin_required
def admin_cupons_excluir(cid):
    conn = get_pubshow_db()
    conn.execute('DELETE FROM pubshow_cupons WHERE id=?', (cid,))
    conn.commit(); conn.close()
    return redirect('/pubshow/admin/cupons')


@pubshow_bp.route('/painel/push/vapid-key')
@pubshow_login_required
def painel_push_vapid_key():
    """Retorna a chave pública VAPID para o cliente registrar o SW."""
    pub, _ = _vapid_keys()
    return jsonify({'publicKey': pub})


@pubshow_bp.route('/painel/toggle-slides-sistema', methods=['POST'])
@pubshow_login_required
def painel_toggle_slides_sistema():
    """Bar ativa/desativa slides globais do sistema na sua TV."""
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    conn = get_pubshow_db()
    atual = conn.execute('SELECT usar_slides_sistema FROM pubshow_businesses WHERE id=?', (b['id'],)).fetchone()
    novo = 0 if (atual and atual[0]) else 1
    conn.execute('UPDATE pubshow_businesses SET usar_slides_sistema=? WHERE id=?', (novo, b['id']))
    conn.commit(); conn.close()
    _bump_tv_bar(b['id'])   # só a TV deste bar recarrega (15/10/26)
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/anuncios', methods=['POST'])
@pubshow_login_required
def painel_anuncios():
    """Salva a propaganda de texto do bar (o plano tem 1; bar com mais, do plano antigo, guarda as dele)."""
    import json as _json
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    # 15/10/26 (D4-16): salvar os textos NÃO apaga mais os slides de imagem (antes sumiam da TV)
    try:
        _atuais = _json.loads(b['anuncios_json'] or '[]')
    except Exception:
        _atuais = []
    imagens = [s for s in _atuais if isinstance(s, dict) and s.get('tipo') == 'imagem']
    # 16/10/26 (V3-04): imagem ocupa vaga — texto + imagem nunca passa do máximo de slides
    # 17/10/26 (Lote D, críticos CD3): bar que JÁ tem mais textos que isso (Garage no Pro: 3 textos + 2 imagens) guarda
    # os que já tinha: corrigir 1 letra não apaga mais os outros do banco. Só não cresce (o painel bloqueia "Adicionar").
    _textos_atuais = len([s for s in _atuais if isinstance(s, dict) and s.get('tipo') != 'imagem'])
    vagas_texto = max(0, _plano_max_anuncios(b) - len(imagens), _textos_atuais)
    slides = []
    for i in range(1, max(_plano_max_anuncios(b), vagas_texto) + 1):
        titulo    = request.form.get(f'slide_{i}_titulo', '').strip()[:60]
        subtitulo = request.form.get(f'slide_{i}_sub', '').strip()[:80]
        emoji     = request.form.get(f'slide_{i}_emoji', '').strip()[:4]
        cor       = request.form.get(f'slide_{i}_cor', '#ef4444').strip()[:7]
        if titulo:
            slides.append({'titulo': titulo, 'subtitulo': subtitulo, 'emoji': emoji or '📢', 'cor': cor})
    slides = slides[:vagas_texto]
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET anuncios_json=? WHERE id=?',
                 (_json.dumps(slides + imagens, ensure_ascii=False), b['id']))
    conn.commit(); conn.close()
    _bump_tv_bar(b['id'])   # só a TV deste bar recarrega e aplica os anúncios (15/10/26)
    if _xhr():   # Lote D C (16/10/26): o painel salva sozinho ("✓ salvo"), sem recarregar
        return jsonify({'ok': True, 'textos': len(slides)})
    return redirect('/pubshow/painel')


def _xhr():
    """Lote D C (16/10/26): pedido do "salva sozinho" do painel (fetch com X-Requested-With)."""
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


_SLIDE_MAX_BYTES = 8 * 1024 * 1024
# 15/10/26 (V-S2-01): teto em PIXELS, lido do cabeçalho antes de decodificar. Um PNG de 500 KB
# pode abrir como 144 Mpx (~1,2 GB de RAM no worker = o 4kitem inteiro). Medido no Pillow 12:
# PNG/WEBP gastam ~10-16 bytes por pixel no thumbnail; JPEG com draft() ~2; GIF animado ~1,2 por
# pixel × quadro. Foto real de até 8 MB cabe (JPEG de celular 48-50 Mpx passa).
_SLIDE_MAX_LADO = 10000
_SLIDE_MAX_PX_JPEG = 50_000_000
_SLIDE_MAX_PX = 16_000_000                 # PNG, WEBP e GIF parado
_SLIDE_GIF_MAX_LADO, _SLIDE_GIF_MAX_QUADROS, _SLIDE_GIF_MAX_PX_TOTAL = 1920, 300, 150_000_000
_SLIDE_VOLTAR = '/pubshow/painel?aba=ajustes'
# 413 com página simples e link de volta (V-S2-05). O painel já barra no celular antes de enviar.
_SLIDE_GRANDE_HTML = ('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
                      '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Imagem grande</title></head>'
                      '<body style="font-family:sans-serif;background:#0d0d14;color:#e5e7eb;padding:32px;text-align:center">'
                      '<p style="font-size:17px">Imagem grande demais (máximo 8 MB).</p>'
                      f'<p><a href="{_SLIDE_VOLTAR}&erro=imagem_grande" style="color:#4ade80;font-weight:700">'
                      'Voltar pro painel e escolher outra</a></p></body></html>')


def _slide_grande_resp():
    return (_SLIDE_GRANDE_HTML, 413, {'Content-Type': 'text/html; charset=utf-8'})


def _slide_dimensao_ok(img, fmt, animado):
    """Confere o tamanho em pixels SEM decodificar (img.size e n_frames vêm do cabeçalho)."""
    w, h = img.size
    if w < 1 or h < 1 or max(w, h) > _SLIDE_MAX_LADO:
        return False
    if animado:
        if max(w, h) > _SLIDE_GIF_MAX_LADO:
            return False
        n = getattr(img, 'n_frames', 1)
        return n <= _SLIDE_GIF_MAX_QUADROS and n * w * h <= _SLIDE_GIF_MAX_PX_TOTAL
    return w * h <= (_SLIDE_MAX_PX_JPEG if fmt == 'jpg' else _SLIDE_MAX_PX)


@pubshow_bp.route('/painel/upload-slide', methods=['POST'])
@pubshow_login_required
def painel_upload_slide():
    """Upload de imagem como slide de propaganda."""
    import json as _json
    # 15/10/26: limite de 8 MB NESTA rota (não global — o resto do 4kitem tem outros uploads);
    # antes qualquer conta nova mandava arquivo de qualquer tamanho e enchia o volume.
    if (request.content_length or 0) > _SLIDE_MAX_BYTES:
        return _slide_grande_resp()
    b = dict(_get_business())  # dict() para .get() funcionar em _plano_max_anuncios
    max_slides = _plano_max_anuncios(b)
    try:
        anuncios = _json.loads(b['anuncios_json'] or '[]')
    except Exception:
        anuncios = []
    # Remove slides de imagem existentes para contar total
    imgs_existentes = [s for s in anuncios if s.get('tipo') == 'imagem']
    texto_existentes = [s for s in anuncios if s.get('tipo') != 'imagem']
    total = len(texto_existentes) + len(imgs_existentes)
    if total >= max_slides:
        return redirect(_SLIDE_VOLTAR + '&erro=limite_slides')
    f = request.files.get('imagem')
    if not f or not f.filename:
        return redirect('/pubshow/painel')
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return redirect(_SLIDE_VOLTAR + '&erro=formato_invalido')
    dados = f.stream.read(_SLIDE_MAX_BYTES + 1)
    if len(dados) > _SLIDE_MAX_BYTES:
        # sem Content-Length (raro): o formulário já foi lido inteiro, então dá pra voltar pro painel
        return redirect(_SLIDE_VOLTAR + '&erro=imagem_grande')
    # Regrava com o Pillow: só imagem de verdade entra (arquivo disfarçado de .png é recusado),
    # sem metadados, no máx. 1920 px (TV). Nome aleatório (uuid4), nunca o do usuário.
    try:
        from PIL import Image
        try:
            img = Image.open(io.BytesIO(dados))
        except Image.DecompressionBombError:
            return redirect(_SLIDE_VOLTAR + '&erro=imagem_grande')
        # MPO = JPEG de alguns celulares (foto com "profundidade"); o Pillow chama de MPO
        fmt = {'JPEG': 'jpg', 'MPO': 'jpg', 'PNG': 'png', 'GIF': 'gif', 'WEBP': 'webp'}.get(img.format or '')
        if not fmt:
            return redirect(_SLIDE_VOLTAR + '&erro=formato_invalido')
        animado = fmt == 'gif' and getattr(img, 'is_animated', False)
        # ANTES de verify/thumbnail/save (que decodificam): teto de pixels e de quadros
        if not _slide_dimensao_ok(img, fmt, animado):
            log.warning('[PUBSHOW] slide recusado por tamanho em pixels (bar %s): %sx%s',
                        b.get('id'), *img.size)
            return redirect(_SLIDE_VOLTAR + '&erro=imagem_grande')
        img.verify()
        img = Image.open(io.BytesIO(dados))
        if not animado:
            if fmt == 'jpg':
                img.draft(None, (1920, 1920))   # o libjpeg já decodifica reduzido (RAM ~4x menor)
            img.thumbnail((1920, 1920))
            if fmt == 'jpg' and img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
        code = b['code']
        pasta = os.path.join(_SLIDES_DIR, code)
        os.makedirs(pasta, exist_ok=True)
        fname = f'{uuid.uuid4().hex}.{fmt}'
        if animado:
            img.save(os.path.join(pasta, fname), format='GIF', save_all=True)
        else:
            img.save(os.path.join(pasta, fname), format={'jpg': 'JPEG'}.get(fmt, fmt.upper()))
    except Exception as ex:
        log.warning('[PUBSHOW] upload de slide recusado (bar %s): %s', b.get('id'), ex)
        return redirect(_SLIDE_VOLTAR + '&erro=formato_invalido')
    url = f'/pubshow/slide/{code}/{fname}'
    anuncios.append({'tipo': 'imagem', 'url': url, 'titulo': ''})
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET anuncios_json=? WHERE id=?',
                 (_json.dumps(anuncios, ensure_ascii=False), b['id']))
    conn.commit(); conn.close()
    _bump_tv_bar(b['id'])   # só a TV deste bar recarrega e aplica o slide novo (15/10/26)
    return redirect('/pubshow/painel')


@pubshow_bp.route('/slide/<code>/<filename>')
def slide_serve(code, filename):
    """Serve imagens de slide do bar."""
    # Segurança (11/09/26): 'code' e 'filename' vêm da URL. Sem esta trava, '..' sobe
    # até o DATA_DIR e serve qualquer .db do monólito — sem login.
    if not re.fullmatch(r'[A-Za-z0-9]{4,16}', code or '') \
       or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', filename or '') or '..' in filename:
        return ('', 404)
    pasta = os.path.join(_SLIDES_DIR, code)
    return send_from_directory(pasta, filename)


# ── Cron externo — ping por UptimeRobot/Railway Cron ────────────────────────

@pubshow_bp.route('/_cron/emails')
def cron_emails():
    """Endpoint para disparar o processamento de emails via cron externo.
    Requer header X-Cron-Key com PUBSHOW_CRON_KEY do environment.
    Se a variável não estiver configurada, qualquer chamada funciona (desenvolvimento)."""
    from flask import Response as _Response
    cron_key = os.environ.get('PUBSHOW_CRON_KEY', '')
    req_key  = request.headers.get('X-Cron-Key', request.args.get('key', ''))
    if cron_key and req_key != cron_key:
        return _Response('Unauthorized', status=401)
    # M8 fix: não reseta _email_last_check — respeita o rate-limit de 10min
    # mesmo quando chamado pelo cron externo, evitando emails duplicados
    global _email_last_check
    try:
        _processar_fila_emails()
        return jsonify({'ok': True, 'ts': datetime.now().isoformat()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@pubshow_bp.route('/_cron/sync-assinaturas')
def cron_sync_assinaturas():
    """Sync proativo de assinaturas com Asaas.
    Verifica o status real de cada assinatura e corrige discrepâncias.
    Deve ser chamado 1x/dia (Railway Cron, UptimeRobot, etc)."""
    cron_key = os.environ.get('PUBSHOW_CRON_KEY', '')
    req_key  = request.headers.get('X-Cron-Key', request.args.get('key', ''))
    if cron_key and req_key != cron_key:
        return jsonify({'error': 'unauthorized'}), 401

    if not os.environ.get('ASAAS_API_KEY'):
        return jsonify({'ok': False, 'msg': 'ASAAS_API_KEY não configurada'}), 200

    resultado = {'verificados': 0, 'ativados': 0, 'suspensos': 0, 'erros': 0}

    try:
        conn = get_pubshow_db()
        assinaturas = conn.execute(
            '''SELECT a.*, b.nome, b.email, b.telefone, b.plano_ativo
               FROM pubshow_assinaturas a
               JOIN pubshow_businesses b ON b.id = a.business_id
               WHERE a.asaas_subscription_id IS NOT NULL
                 AND a.status NOT IN ("cancelado")
               LIMIT 100'''
        ).fetchall()
        conn.close()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

    for ass in assinaturas:
        resultado['verificados'] += 1
        try:
            sub_data = _asaas_req('GET', f'/subscriptions/{ass["asaas_subscription_id"]}')
            asaas_status = sub_data.get('status', '')
            next_due     = sub_data.get('nextDueDate', '')

            conn = get_pubshow_db()
            conn.execute(
                'UPDATE pubshow_assinaturas SET asaas_sub_status=?, proximo_vencimento=?, ultima_sync=? WHERE id=?',
                (asaas_status, next_due, datetime.now().isoformat(), ass['id'])
            )
            conn.commit()
            conn.close()

            if asaas_status == 'ACTIVE' and not ass['plano_ativo']:
                # Asaas diz ativo mas nós temos desativado — reativa
                _assinatura_ativar(ass['business_id'], ass['plano'], ass['asaas_subscription_id'], next_due)
                resultado['ativados'] += 1
                log.info('[PUBSHOW sync] Reativado business_id=%s (Asaas ACTIVE, local inativo)', ass['business_id'])

            elif asaas_status in ('CANCELLED', 'INACTIVE') and ass['plano_ativo']:
                # Asaas diz cancelado mas nós temos ativo — desativa
                _assinatura_cancelar(ass['business_id'])
                resultado['suspensos'] += 1
                log.info('[PUBSHOW sync] Suspenso business_id=%s (Asaas %s, local ativo)', ass['business_id'], asaas_status)

            elif asaas_status == 'OVERDUE':
                # Verifica carência
                conn2 = get_pubshow_db()
                ass2 = conn2.execute('SELECT * FROM pubshow_assinaturas WHERE id=?', (ass['id'],)).fetchone()
                conn2.close()
                ass2 = dict(ass2) if ass2 else None   # sqlite3.Row não tem .get() (15/10/26)
                if ass2 and not ass2.get('inadimplente_desde'):
                    _assinatura_inadimplente(ass['business_id'], ass['id'])
                    _notify_assinatura(ass['business_id'], 'inadimplente')
                elif ass2 and ass2.get('inadimplente_desde'):
                    _assinatura_inadimplente(ass['business_id'], ass['id'])

        except Exception as e:
            log.error('[PUBSHOW sync] Erro business_id=%s: %s', ass['business_id'], e)
            resultado['erros'] += 1

    log.info('[PUBSHOW sync] Resultado: %s', resultado)
    return jsonify({'ok': True, **resultado, 'ts': datetime.now().isoformat()})


# ── Service Worker do Painel (Push Notifications) ────────────────────────────

@pubshow_bp.route('/painel/manifest.json')
def painel_manifest():  # público — browser busca sem session
    """Web App Manifest para o painel do bar — PWA instalável."""
    import json as _json2
    from flask import Response as _Resp2
    b = _get_business()
    nome = dict(b)['nome'] if b else 'PUBSHOW'
    manifest = {
        'name': f'PUBSHOW — {nome}',
        'short_name': 'PUBSHOW',
        'description': 'Painel de gestão do Jukebox',
        'start_url': '/pubshow/painel',
        'display': 'standalone',
        'background_color': '#08080f',
        'theme_color': '#ef4444',
        'orientation': 'portrait',
        'icons': [
            {'src': '/static/pubshow/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
            {'src': '/static/pubshow/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ]
    }
    return _Resp2(_json2.dumps(manifest), mimetype='application/manifest+json')


@pubshow_bp.route('/sw-painel.js')
def painel_sw():
    """Serve o Service Worker do painel com escopo /pubshow/ permitido.
    Precisa estar em /pubshow/sw-painel.js (não em /static/) para que o
    browser aceite o escopo /pubshow/ via header Service-Worker-Allowed.
    """
    import os as _os
    sw_path = _os.path.join(_os.path.dirname(__file__), 'static', 'pubshow', 'sw-painel.js')
    try:
        with open(sw_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        content = '// sw-painel.js not found'
    from flask import Response as _Resp
    resp = _Resp(content, mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/pubshow/'
    resp.headers['Cache-Control'] = 'no-store'
    return resp


# ── PWA — Manifest dinâmico + Ícone ──────────────────────────────────────────

@pubshow_bp.route('/manifest/<token>.json')
def jukebox_manifest(token):
    """Web App Manifest dinâmico — nome do bar, start_url com o token correto."""
    import json as _json2
    from flask import Response as _Response
    conn = get_pubshow_db()
    b = conn.execute(
        'SELECT nome FROM pubshow_businesses WHERE jukebox_token=? OR code=? LIMIT 1',
        (token, token)
    ).fetchone()
    conn.close()
    nome = (b['nome'] if b else 'Jukebox') or 'Jukebox'
    manifest = {
        'name':             f'Jukebox — {nome}',
        'short_name':       'Jukebox',
        'description':      f'Peça músicas no {nome} direto pelo seu celular',
        'start_url':        f'/pubshow/jukebox/{token}',
        'scope':            f'/pubshow/jukebox/{token}',
        'display':          'standalone',
        'background_color': '#08080f',
        'theme_color':      '#08080f',
        'orientation':      'portrait',
        'lang':             'pt-BR',
        'icons': [
            {'src': f'/pubshow/pwa-icon/{token}/192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
            {'src': f'/pubshow/pwa-icon/{token}/512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
        'screenshots': [],
    }
    return _Response(
        _json2.dumps(manifest, ensure_ascii=False),
        mimetype='application/manifest+json',
        headers={'Cache-Control': 'public, max-age=3600'}
    )


@pubshow_bp.route('/pwa-icon/<token>/<int:size>.png')
def pwa_icon(token, size):
    """Gera ícone PNG para o PWA do Jukebox — fundo escuro + nota musical verde."""
    from flask import Response as _Response
    if size not in (192, 512):
        size = 192
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io as _io2

        img  = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # ── Fundo com cantos arredondados ─────────────────────────────────────
        r = size // 6
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(8, 8, 15))

        # ── Círculo de brilho (glow) ──────────────────────────────────────────
        glow_r = size // 3
        cx = cy = size // 2
        for spread in range(glow_r, 0, -4):
            alpha = int(40 * spread / glow_r)
            draw.ellipse([cx - spread, cy - spread, cx + spread, cy + spread],
                         fill=(74, 222, 128, alpha))

        # ── Nota musical desenhada com formas básicas ─────────────────────────
        # Cabeça da nota (elipse inclinada)
        u = size // 20
        # Nota simples: haste vertical + cabeça oval
        stem_x = cx + u * 3
        stem_y_top = cy - u * 5
        stem_y_bot = cy + u * 3
        head_cx  = cx
        head_cy  = cy + u * 3
        head_rx  = u * 4
        head_ry  = u * 2

        # Haste
        draw.rectangle([stem_x - u, stem_y_top, stem_x + u, stem_y_bot],
                       fill=(74, 222, 128))
        # Cabeça oval
        draw.ellipse([head_cx - head_rx, head_cy - head_ry,
                      head_cx + head_rx, head_cy + head_ry],
                     fill=(74, 222, 128))
        # Bandeirinha
        bx, by = stem_x + u, stem_y_top
        draw.line([(bx, by), (bx + u*3, by + u*2), (bx, by + u*4)],
                  fill=(74, 222, 128), width=max(2, u))

        buf = _io2.BytesIO()
        img.save(buf, format='PNG', optimize=True)
        buf.seek(0)
        return _Response(buf.read(), mimetype='image/png',
                         headers={'Cache-Control': 'public, max-age=86400'})

    except Exception as ex:
        log.warning('[PWA] icon gen error: %s', ex)
        # Fallback: PNG 1×1 transparente
        import base64 as _b64
        px = _b64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ'
            'AABjkB6QAAAABJRU5ErkJggg=='
        )
        return _Response(px, mimetype='image/png')


@pubshow_bp.route('/painel/delete-slide', methods=['POST'])
@pubshow_login_required
def painel_delete_slide():
    """Remove um slide de imagem."""
    import json as _json
    b = _get_business()
    url_del = request.form.get('url', '')
    try:
        anuncios = _json.loads(b['anuncios_json'] or '[]')
    except Exception:
        anuncios = []
    anuncios = [s for s in anuncios if s.get('url') != url_del]
    # Apaga o arquivo físico
    if url_del.startswith('/pubshow/slide/'):
        partes = url_del.split('/')
        # só apaga dentro da pasta do PRÓPRIO bar e só nome de arquivo simples (sem '..')
        if len(partes) >= 5 and partes[3] == b['code'] \
           and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', partes[4]) and '..' not in partes[4]:
            try:
                os.remove(os.path.join(_SLIDES_DIR, partes[3], partes[4]))
            except Exception:
                pass
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET anuncios_json=? WHERE id=?',
                 (_json.dumps(anuncios, ensure_ascii=False), b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/temas', methods=['POST'])
@pubshow_login_required
def painel_temas():
    """Salva quais temas o bar quer habilitar na TV."""
    import json as _json
    b = _get_business()
    selecionados = request.form.getlist('temas')
    # Filtra só keys válidas
    validos = [k for k in selecionados if k in CANAIS]
    # Se marcou tudo ou nada → NULL (= todos habilitados)
    if not validos or len(validos) == len(CANAIS):
        valor = None
    else:
        valor = _json.dumps(validos)
    conn = get_pubshow_db()
    # 15/10/26 (B10): UMA lista "Estilos do bar" — os mesmos estilos valem pra TV e pra busca do
    # jukebox (antes eram 2 listas de 23 gêneros, uma em cada canto do painel)
    conn.execute('UPDATE pubshow_businesses SET temas_habilitados=?, generos_jukebox=? WHERE id=?',
                 (valor, valor, b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=ajustes')


def _acesos_do_bar(b):
    """Os botões grandes acesos, lidos do que está salvo (mesma regra do painel.html): Variado com temas = cada botão
    cujos temas estão todos lá; senão o botão do canal no ar (ou da família dele: Punk → Rock, Skate → Esporte)."""
    ck = (b.get('canal_atual') if hasattr(b, 'get') else b['canal_atual']) or 'rock'
    ck = ck if ck in CANAIS else 'rock'          # VDC-06: vazio ou inexistente = Rock, o fallback da TV
    tm = set(_temas_do_bar(b) or [])
    if ck == 'variado' and tm:
        ac = [k for k, e in ESTILOS_BAR.items() if e['temas'] and set(e['temas']) <= tm]
        if ac:
            return ac
    return [k for k, e in ESTILOS_BAR.items() if ck == e['canal'] or (e['temas'] and ck in e['temas'])][:1]


def _no_ar_txt(b):
    """'⚽ Esporte + 🎸 Rock' / '🎶 Variado' / '🛹 Skate Clips' (canal antigo que não é botão): a linha 📺 No ar."""
    ck = (b.get('canal_atual') if hasattr(b, 'get') else b['canal_atual']) or 'rock'
    ck = ck if ck in CANAIS else 'rock'
    ac = _acesos_do_bar(b)
    if ck not in {e['canal'] for e in ESTILOS_BAR.values()}:
        return f"{CANAIS[ck]['emoji']} {CANAIS[ck]['nome']}"
    return ' + '.join(f"{ESTILOS_BAR[k]['emoji']} {ESTILOS_BAR[k]['nome']}" for k in ac) or '🎶 Variado'


def _yt_txt(b):
    """O "Pedir do YouTube": ligado, o cliente busca só nos estilos acesos (antes dizia "qualquer música" sempre)."""
    ests = pubshow_estilo.estilos_do_bar(b)
    if ests:
        return f'Ligado: o cliente busca só {pubshow_estilo.nomes_txt(ests)}. Desligado: só a lista do bar.'
    return 'Ligado: qualquer música. Desligado: só a lista do bar.'


@pubshow_bp.route('/painel/estilo', methods=['POST'])
@pubshow_login_required
def painel_estilo():
    """Lote D C (16/10/26): 1 toque no Estilo do bar = canal da TV + o que o cliente acha na busca (ESTILOS_BAR).
    Canal de "mais estilos" = só a TV troca e a busca fica inteira. Salva sozinho (fetch) ou volta pra aba."""
    import json as _json
    b = _get_business()
    if not b:
        return (jsonify({'ok': False, 'erro': 'não autorizado'}), 401) if _xhr() else redirect('/pubshow/entrar')
    k = (request.form.get('estilo') or '').strip()
    acesos = None
    if 'estilos' in request.form:
        # 20/10/26 (pedido do Garage: "clicar em 3 estilos e vai tocar só aquilo"): os botões grandes acendem
        # vários. 1 aceso = igual antes; 2 ou mais = Variado SÓ com eles (TV, lista do bar e YouTube); Variado = tudo.
        acesos = [x for x in dict.fromkeys(s.strip() for s in request.form.get('estilos', '').split(','))
                  if x in ESTILOS_BAR]
        if not acesos or 'variado' in acesos:
            acesos = ['variado']
        k = acesos[0]
    if acesos and len(acesos) > 1:
        canal, k = 'variado', 'variado'
        temas = list(dict.fromkeys(t for x in acesos for t in (ESTILOS_BAR[x]['temas'] or [])))
    elif k in ESTILOS_BAR:
        canal, temas = ESTILOS_BAR[k]['canal'], ESTILOS_BAR[k]['temas']
    elif k in _plano_canais_permitidos(dict(b)):
        canal, temas = k, None
    else:
        return (jsonify({'ok': False, 'erro': 'estilo inválido'}), 400) if _xhr() else redirect('/pubshow/painel?aba=musica')
    valor = _json.dumps(temas) if temas else None
    # 20/10/26: Esporte/Comédia acesos mexem só na TV (temas_habilitados); o celular e o YouTube ficam com a música
    musica = _so_musica(temas)
    valor_cel = _json.dumps(musica) if musica else None
    conn = get_pubshow_db()
    pubshow_estilo.garantir_coluna(conn)   # 17/10/26: o toque também vale pra busca do YouTube ("aqui só toca rock")
    _antes = conn.execute('SELECT canal_atual, temas_habilitados FROM pubshow_businesses WHERE id=?', (b['id'],)).fetchone()
    conn.execute('UPDATE pubshow_businesses SET canal_atual=?, temas_habilitados=?, generos_jukebox=?, estilo_yt=? WHERE id=?',
                 (canal, valor, valor_cel, 1 if musica else 0, b['id']))
    conn.commit(); conn.close()
    # o canal não mudou (Variado → Variado) mas a mistura sim: a TV só troca quando o canal muda, então recarrega
    # (com pedido tocando, ela espera a música acabar — TVP-1)
    if _antes and (_antes['canal_atual'] or '') == canal == 'variado' and (_antes['temas_habilitados'] or None) != valor:
        _bump_tv_bar(b['id'])
    # 20/10/26: entrou ou saiu do modo fundo (Esporte + música): a TV troca o layout, então recarrega (espera o pedido)
    elif _antes and _fundo_ativo(_antes) != _fundo_ativo({'canal_atual': canal, 'temas_habilitados': valor}):
        _bump_tv_bar(b['id'])
    session['pub_canal'] = canal   # a TV aberta troca sozinha pelo /api/status (canal_atual)
    if _xhr():
        resp = {'ok': True, 'estilo': k, 'canal': canal}
        if acesos is not None:
            resp['acesos'] = acesos
        # 20/10/26: as 2 linhas que mudam junto (📺 No ar: … e o "Pedir do YouTube")
        _nb = {'canal_atual': canal, 'temas_habilitados': valor, 'generos_jukebox': valor_cel, 'estilo_yt': 1 if musica else 0}
        resp.update(no_ar=_no_ar_txt(_nb), yt_txt=_yt_txt(_nb))
        return jsonify(resp)
    return redirect('/pubshow/painel?aba=musica')


@pubshow_bp.route('/painel/happy-hour', methods=['POST'])
@pubshow_login_required
def painel_happy_hour():
    """Salva configuração de Happy Hour do bar."""
    import json as _json
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    ativo = request.form.get('hh_ativo') == '1'
    # VDC-03 (16/10/26): desligar NÃO apaga mais horário/desconto/dias (1 toque sem querer sumia com tudo e o card
    # saía do painel). Guarda a configuração com 'ativo': False; o card continua e dá pra religar.
    try:
        salvo = _json.loads(b['happy_hour_json'] or 'null')
    except Exception:
        salvo = None
    salvo = salvo if isinstance(salvo, dict) else {}
    if not ativo and not salvo and 'hh_ini' not in request.form:
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET happy_hour_json=NULL WHERE id=?', (b['id'],))
        conn.commit(); conn.close()
        if _xhr():   # Lote D C (16/10/26): salva sozinho
            return jsonify({'ok': True, 'ativo': False})
        return redirect('/pubshow/painel?aba=config')
    ini      = request.form.get('hh_ini', salvo.get('ini', '18:00')).strip()
    fim      = request.form.get('hh_fim', salvo.get('fim', '20:00')).strip()
    try:
        desconto = max(1, min(80, int(request.form.get('hh_desconto', salvo.get('desconto', 20)) or '20')))
    except Exception:
        desconto = 20
    if 'hh_dias' in request.form or 'hh_ini' in request.form:   # veio do formulário: vale o que está marcado
        dias = [int(d) for d in request.form.getlist('hh_dias') if d.isdigit() and 0 <= int(d) <= 6]
    else:
        dias = [int(d) for d in (salvo.get('dias') or []) if str(d).isdigit() and 0 <= int(d) <= 6]
    if not dias:
        dias = [0, 1, 2, 3, 4, 5, 6]
    hh = {'ini': ini, 'fim': fim, 'desconto': desconto, 'dias': dias}
    if not ativo:
        hh['ativo'] = False
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET happy_hour_json=? WHERE id=?',
                 (_json.dumps(hh), b['id']))
    conn.commit(); conn.close()
    if _xhr():   # Lote D C (16/10/26): salva sozinho
        return jsonify({'ok': True, 'ativo': ativo})
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/notif', methods=['POST'])
@pubshow_login_required
def painel_notif():
    """Ativa/desativa notificações WhatsApp ao bar."""
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    ativo = 1 if request.form.get('whatsapp_notif') else 0
    # VDC-03 (16/10/26): 2 = "desligado pelo dono" — a chave continua no painel pra religar (0 = nunca usou: não
    # aparece). Só o 1 manda aviso (_pubshow_notify_bar).
    _grava = ativo if ativo else (2 if b['whatsapp_notif'] else 0)
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET whatsapp_notif=? WHERE id=?', (_grava, b['id']))
    conn.commit(); conn.close()
    if _xhr():   # Lote D C (16/10/26): salva sozinho
        return jsonify({'ok': True, 'ativo': bool(ativo)})
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/pix', methods=['POST'])
@pubshow_login_required
def painel_pix():
    b = _get_business()
    pix_key  = request.form.get('pix_key', '').strip()
    # a chave pra receber o repasse (aba 💰 Dinheiro, 20/10/26) vem sem o tipo: o banco reconhece sozinho; mantém o salvo
    pix_tipo = request.form.get('pix_tipo') or (b['pix_tipo'] if b and b['pix_tipo'] else 'telefone')
    pix_nome = request.form.get('pix_nome', '').strip()
    conn = get_pubshow_db()
    conn.execute(
        'UPDATE pubshow_businesses SET pix_key=?, pix_tipo=?, pix_nome_recebedor=? WHERE id=?',
        (pix_key, pix_tipo, pix_nome, b['id'])
    )
    conn.commit(); conn.close()
    # 20/10/26: a chave pra RECEBER o repasse é salva na aba 💰 Dinheiro e volta pra ela
    if request.form.get('volta') == 'dinheiro':
        return redirect('/pubshow/painel?aba=dinheiro#sec-repasse')
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/qrcode')
@pubshow_login_required
def painel_qrcode():
    """Página de impressão de QR Codes para as mesas."""
    b = _get_business()
    if not b:
        return redirect('/pubshow/entrar')

    token = b['jukebox_token'] or b['code']
    jukebox_url = f"https://www.4kitem.com.br/pubshow/jukebox/{token}"

    # Passo 2 do tutorial (15/10/26): abriu a página de imprimir o QR = marcado (1x só)
    if not b['qr_aberto_em']:
        try:
            _c = get_pubshow_db()
            _c.execute('UPDATE pubshow_businesses SET qr_aberto_em=? WHERE id=? AND qr_aberto_em IS NULL',
                       (datetime.utcnow().strftime(_FMT_UTC), b['id']))
            _c.commit(); _c.close()
        except Exception as ex:
            log.warning('[PUBSHOW] qr_aberto_em não gravou: %s', ex)

    # Gera QR como PNG base64
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=2,
        )
        qr.add_data(jukebox_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as ex:
        log.error('QR error: %s', ex)
        qr_b64 = None

    # dict(b): sqlite3.Row não tem .get() → _plano_permite estourava 500 no QR (bug 11/09/26)
    # 16/10/26 (integração): o cartão impresso diz o MESMO preço do celular e da TV (_qr_preco_tv:
    # item + taxa de serviço, centavos com vírgula). Antes: "R$ 7" no papel e "R$ 8 no PIX" no celular.
    _qp = _qr_preco_tv(dict(b))
    furar_txt = _qp.split(' · ', 1)[1] if ' · ' in _qp else ''
    return render_template('pubshow/qrcode.html', precos=_precos_do_bar(dict(b))[0],
                           furar_txt=furar_txt, youtube_ok=_youtube_liberado(dict(b)),
                           b=dict(b),
                           jukebox_url=jukebox_url,
                           qr_b64=qr_b64)


@pubshow_bp.route('/painel/pedido/<int:pid>/dispensar', methods=['POST'])
@pubshow_login_required
def painel_dispensar_pedido(pid):
    b = _get_business()
    conn = get_pubshow_db()
    # só da fila (15/10/26): pedido não pago (aguardando_pix) não vira 'dispensado'
    conn.execute(
        "UPDATE pubshow_pedidos SET status='dispensado' WHERE id=? AND business_id=? AND status='pendente'",
        (pid, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=fila')


@pubshow_bp.route('/painel/pular-musica', methods=['POST'])
@pubshow_login_required
def painel_pular_musica():
    """Botão do dono: pula a música/vídeo que está tocando na TV agora.
    Incrementa skip_seq; a TV detecta no polling (~2,5s) e avança a fila."""
    b = _get_business()
    conn = get_pubshow_db()
    conn.execute(
        'UPDATE pubshow_businesses SET skip_seq = COALESCE(skip_seq,0) + 1 WHERE id=?',
        (b['id'],)
    )
    conn.commit(); conn.close()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/bloquear-video', methods=['POST'])
@pubshow_login_required
def painel_bloquear_video():
    """🚫 "Nunca mais": bane um youtube_id neste bar (some da lista e é recusado no pedido)
    e dispensa o pedido que o trouxe, se informado."""
    import json as _json
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    yid = (request.form.get('youtube_id') or '').strip()[:20]
    if not re.fullmatch(r'[A-Za-z0-9_-]{6,20}', yid):
        return redirect('/pubshow/painel')
    lista = _videos_bloqueados(b)
    if yid not in lista:
        lista.append(yid)
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET videos_bloqueados=? WHERE id=?', (_json.dumps(lista[-300:]), b['id']))
    pid = request.form.get('pedido_id', '')
    if pid.isdigit():
        # só da fila (15/10/26): aguardando_pix dispensado entrava no caixa sem ter sido pago
        conn.execute("UPDATE pubshow_pedidos SET status='dispensado' WHERE id=? AND business_id=? AND status='pendente'",
                     (int(pid), b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/desbloquear-video', methods=['POST'])
@pubshow_login_required
def painel_desbloquear_video():
    """Painel por assunto (17/10/26): "Liberar de novo" uma música do "🚫 nunca mais" (aba 🎵 Estilo).
    Só tira o youtube_id da lista do bar; não mexe em pedido nenhum."""
    import json as _json
    b = _get_business()
    if not b:
        return (jsonify({'ok': False, 'erro': 'não autorizado'}), 401) if _xhr() else redirect('/pubshow/entrar')
    yid = (request.form.get('youtube_id') or '').strip()[:20]
    lista = _videos_bloqueados(b)
    if yid in lista:
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET videos_bloqueados=? WHERE id=?',
                     (_json.dumps([y for y in lista if y != yid]), b['id']))
        conn.commit(); conn.close()
    if _xhr():
        return jsonify({'ok': True, 'youtube_id': yid})
    return redirect('/pubshow/painel?aba=estilo')


@pubshow_bp.route('/painel/volume', methods=['POST'])
@pubshow_login_required
def painel_volume():
    """Controle remoto de volume da TV (0-100). A TV lê tv_volume no polling
    de /api/status e aplica no player — o gerente ajusta pelo celular."""
    b = _get_business()
    # `is None` (15/10/26): volume 0 em JSON era "falso" e virava 100 — mutar punha a TV no talo
    _v = request.form.get('volume')
    if _v is None or _v == '':
        _v = (request.get_json(silent=True) or {}).get('volume')
    try:
        vol = int(100 if _v is None or _v == '' else _v)
    except (TypeError, ValueError):
        vol = 100
    vol = max(0, min(100, vol))
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET tv_volume=? WHERE id=?', (vol, b['id']))
    conn.commit(); conn.close()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True, 'volume': vol})
    return redirect('/pubshow/painel')


# 🔇 21/10/26 (Diogo: "Esporte sempre mudo, sem interruptor"): saiu a rota POST /painel/tv-mudo-sem-musica. A TV
# muda o vídeo de esporte sozinha (tv.html); a coluna tv_mudo_sem_musica fica no banco, sem uso.


@pubshow_bp.route('/painel/pedido/<int:pid>/confirmar-pix', methods=['POST'])
@pubshow_login_required
def painel_confirmar_pix(pid):
    """Bar confirma que o PIX foi recebido → pedido entra na fila.
    Só PIX MANUAL (chave do bar): pedido de gateway só confirma pelo gateway (15/10/26)."""
    b = _get_business()
    conn = get_pubshow_db()
    conn.execute(
        """UPDATE pubshow_pedidos SET status=?, pago_em=datetime('now')
           WHERE id=? AND business_id=? AND status='aguardando_pix'
           AND asaas_payment_id IS NULL AND COALESCE(via_gateway,0)=0
           AND efi_txid IS NULL""",   # crítico dinheiro B2: QR da ponte (até os de antes do deploy) só pela consulta
        (_status_apos_pix(conn, pid), pid, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=fila')


@pubshow_bp.route('/admin/devolucao/<int:pid>/<acao>', methods=['POST'])
@_admin_required
def admin_devolucao_ok(pid, acao):
    """17/10/26 (decisão 3b): devolução de momento já pago que passou do teto do dono (R$ 100/dia) — o Diogo aprova
    (devolve agora) ou nega (o PIX fica como pago) no /saas-admin. Só existe com a Efí ligada."""
    if acao not in ('aprovar', 'negar') or not _efi_ligado():
        return redirect('/saas-admin')
    try:
        conn = _pp_conn()
        try:
            if acao == 'aprovar':
                r = pubshow_pay.aprovar_devolucao(conn, pid, 'saas-admin')
            else:
                r = pubshow_pay.negar_devolucao(conn, pid, 'saas-admin', (request.form.get('obs') or '')[:80])
        finally:
            conn.close()
        log.warning('[PUBSHOW] devolução do pedido #%s: %s pelo saas-admin → %s', pid, acao, r)
    except Exception as ex:
        log.error('[PUBSHOW] devolução do pedido #%s (%s) falhou: %s', pid, acao, ex, exc_info=True)
    return redirect('/saas-admin')


@pubshow_bp.route('/painel/pedido/<int:pid>/aprovar', methods=['POST'])
@pubshow_login_required
def painel_aprovar(pid):
    """🛡️ Gerente liberou a mensagem → entra na fila da TV."""
    b = _get_business()
    conn = get_pubshow_db()
    conn.execute("UPDATE pubshow_pedidos SET status='pendente' WHERE id=? AND business_id=? AND status='aguardando_ok'", (pid, b['id']))
    conn.commit(); conn.close()
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/pedido/<int:pid>/recusar', methods=['POST'])
@pubshow_login_required
def painel_recusar(pid):
    """🛡️ Gerente NÃO liberou a mensagem → 'recusado' (não vai pra TV, fora do caixa).
    Estorno: manual no Asaas; AUTOMÁTICO com a Efí ligada pro bar (Lote C, 16/10/26)."""
    b = _get_business()
    conn = get_pubshow_db()
    n = conn.execute("UPDATE pubshow_pedidos SET status='recusado' WHERE id=? AND business_id=? AND status='aguardando_ok'", (pid, b['id'])).rowcount
    conn.commit(); conn.close()
    if n == 1:   # só quem ESTE clique recusou (pedido já liberado/na TV nunca é devolvido)
        r = _efi_estornar(b, pid, 'recusado pelo gerente')
        if r and r.get('aprovacao') == 'pendente':
            # 17/10/26 (decisão 3b): passou de R$ 100 de devolução no dia (por dono) → o suporte aprova
            return redirect('/pubshow/painel?devolucao=suporte')
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/pedido/<int:pid>/recusar-pix', methods=['POST'])
@pubshow_login_required
def painel_recusar_pix(pid):
    """Bar recusa o pedido PIX (não recebeu pagamento) → 'recusado' (fora do caixa).
    Com a Efí ligada pro bar (Lote C, 16/10/26): cancela a cobrança; se o PIX cair depois, o robô devolve.
    Lote D A4 (16/10/26, crítico CC1 X1b): no modo Efí confere na Efí ANTES (confirmar). Se o PIX JÁ caiu, o pedido
    segue como pago (fila ou OK do gerente), NÃO é recusado nem devolvido, e o painel avisa "o PIX caiu". Antes o
    dono que clicava antes do robô fazia o PubShow devolver um PIX pago cuja parte já tinha caído na conta do bar."""
    b = _get_business()
    pix_caiu = False
    conferir_erro = False   # VDA-02 (16/10/26): a conferência falhou → NÃO recusa (falha fechada)
    # crítico dinheiro A2 (17/10/26): na PONTE Efí, confere na Efí antes de recusar (o QR vive 15 min)
    try:
        _cx = get_pubshow_db()
        try:
            _pr = _cx.execute("SELECT efi_txid FROM pubshow_pedidos WHERE id=? AND business_id=? "
                              "AND status='aguardando_pix'", (pid, b['id'])).fetchone()
        finally:
            _cx.close()
        if _pr and _pr['efi_txid'] and _efi_cli() is not None:
            with _efi_lock:
                _efi_ultima_consulta.pop(pid, None)   # sem o freio de 5 s: o dono clicou agora
            if _efi_conferir_pedido(pid, _pr['efi_txid']):
                return redirect(f'/pubshow/painel?aba=fila&pix_caiu={int(pid)}')
    except Exception as ex:
        log.warning('[PUBSHOW] ponte Efí: conferir antes de recusar o pedido #%s falhou: %s', pid, ex)
    if _efi_ligado():
        tem_cob = None      # None = nem deu pra ler se tem cobrança Efí; False = não tem (Asaas/sem PIX)
        try:
            cx = _pp_conn()
            try:
                c = cx.execute('SELECT txid, status FROM pubshow_pay_cobrancas WHERE pedido_id=? AND business_id=?',
                               (pid, b['id'])).fetchone()
                tem_cob = bool(c and c['status'] in ('criando', 'ativa', 'expirada'))
                if tem_cob:
                    r = pubshow_pay.confirmar(cx, c['txid'], on_pago=_pubshow_on_pago)
                    if r.get('ok'):
                        pix_caiu = bool(r.get('pago')) and not r.get('estorno')
                    elif r.get('erro') != 'valor_divergente':
                        # Efí fora do ar / timeout / 5xx: não sabemos se o PIX caiu. Recusar agora cancelaria a
                        # cobrança e, se tiver caído, o PubShow devolveria o PIX inteiro (a parte do bar vira dívida)
                        conferir_erro = True
            finally:
                cx.close()
        except Exception as ex:
            log.warning('[PUBSHOW] Efí: conferir antes de recusar o pedido #%s falhou: %s', pid, ex)
            if tem_cob is None:
                try:
                    conferir_erro = _efi_para(b)
                except Exception:
                    conferir_erro = True
            else:
                conferir_erro = bool(tem_cob)
    if conferir_erro:
        return redirect('/pubshow/painel?aba=fila&pix_conferir=erro')
    conn = get_pubshow_db()
    n = conn.execute(
        """UPDATE pubshow_pedidos SET status='recusado'
           WHERE id=? AND business_id=? AND status='aguardando_pix'""",
        (pid, b['id'])
    ).rowcount
    conn.commit(); conn.close()
    if n == 1:
        r = _efi_estornar(b, pid, 'recusado pelo bar')
        if r and r.get('aprovacao') == 'pendente':   # 17/10/26 (decisão 3b): passou do teto do dono no dia
            return redirect('/pubshow/painel?aba=fila&devolucao=suporte')
    if pix_caiu:
        return redirect(f'/pubshow/painel?aba=fila&pix_caiu={int(pid)}')
    return redirect('/pubshow/painel?aba=fila')


@pubshow_bp.route('/painel/toggle-pix', methods=['POST'])
@pubshow_login_required
def painel_toggle_pix():
    """Ativa/desativa a exigência de PIX — atualiza APENAS requer_pix, sem tocar em outras configs."""
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    requer_pix = 1 if request.form.get('requer_pix') else 0
    conn = get_pubshow_db()
    conn.execute(
        'UPDATE pubshow_businesses SET requer_pix=? WHERE id=?',
        (requer_pix, b['id'])
    )
    conn.commit(); conn.close()
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/jukebox-ativo', methods=['POST'])
@pubshow_login_required
def painel_jukebox_ativo():
    """Interruptor do jukebox no topo de "Hoje" (15/10/26, B10): salva sozinho, sem botão Salvar.
    Antes ficava no meio do formulário de Ajustes e só valia depois de rolar até "Salvar"."""
    b = _get_business()
    if not b:
        return jsonify({'ok': False, 'erro': 'não autorizado'}), 401
    ativo = 1 if str(request.form.get('ativo', '')).strip() in ('1', 'true', 'on') else 0
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET jukebox_ativo=? WHERE id=?', (ativo, b['id']))
    conn.commit(); conn.close()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True, 'ativo': bool(ativo)})
    return redirect('/pubshow/painel')


@pubshow_bp.route('/painel/config', methods=['POST'])
@pubshow_login_required
def painel_config():
    """Salva todas as configurações do Jukebox do bar."""
    import json
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')

    # Lote D C (16/10/26): o painel enxuto não mostra mais o limite por cliente/hora nem os campos escondidos de
    # boas-vindas/aviso. Campo que NÃO veio no formulário = fica o que está salvo (antes virava 10/vazio).
    f = request.form

    # Horário
    hora_ini = f['hora_ini'].strip() if 'hora_ini' in f else b['jukebox_hora_ini']
    hora_fim = f['hora_fim'].strip() if 'hora_fim' in f else b['jukebox_hora_fim']

    # Mensagem de boas-vindas
    mensagem = f.get('mensagem_jukebox', '').strip()[:120] if 'mensagem_jukebox' in f else b['mensagem_jukebox']

    # Aviso temporário
    if 'aviso_jukebox' in f:
        aviso      = f.get('aviso_jukebox', '').strip()[:120]
        aviso_horas= f.get('aviso_horas', '4')
        aviso_expira = None
        if aviso:
            try:
                horas = max(1, min(72, int(aviso_horas)))
                aviso_expira = (datetime.now() + timedelta(hours=horas)).isoformat()
            except Exception:
                aviso_expira = None
    else:
        aviso, aviso_expira = b['aviso_jukebox'], b['aviso_expira']

    # Limite anti-spam (saiu da tela; o valor salvo continua valendo)
    if 'limite_pedidos_hora' in f:
        try:
            limite = max(1, min(50, int(f.get('limite_pedidos_hora', 10))))
        except Exception:
            limite = 10
    else:
        limite = b['limite_pedidos_hora'] or 10

    # Tipos bloqueados
    todos_tipos = list(TIPOS_PEDIDO.keys())
    ativos      = request.form.getlist('tipos_ativos')
    bloqueados  = [t for t in todos_tipos if t not in ativos]
    if not _pular_espera_para(b):
        # ⏩ Pular a espera (20/10/26) só aparece no painel do bar da Efí: fora dela, salvar não liga nem desliga
        try:
            _bl_ant = json.loads(b['tipos_bloqueados'] or '[]')
        except Exception:
            _bl_ant = []
        bloqueados = [t for t in bloqueados if t != 'pular_espera'] + (['pular_espera'] if 'pular_espera' in _bl_ant else [])

    # Preços custom — só dos itens pagos (música é sempre grátis; o campo nem aparece mais)
    # Lote D A4 (16/10/26): no modo Efí o preço máximo é R$ 50 (o que passar disso grava R$ 50)
    _teto = _preco_teto(b)
    # 17/10/26 (Lote D, críticos CD2/CD3): veio o MESMO valor que já está salvo = fica o salvo, sem cortar. É o preço
    # que o dono não mexeu (o painel manda o data-salvo): o R$ 120 de antes da Efí não vira R$ 50 de vez (e continua
    # R$ 120 num rollback pro Asaas), e os R$ 4 do Garage não viram R$ 5. Quem cobra já corta: _precos_do_bar.
    try:
        _salvos = json.loads(b['precos_custom'] or '{}')
        if not isinstance(_salvos, dict):
            _salvos = {}
    except Exception:
        _salvos = {}
    precos = {}
    for k in todos_tipos:
        if k in TIPOS_SEMPRE_GRATIS or k == 'pular_espera':   # ⏩: preço fixo (R$ 2), o bar não muda
            continue
        val = request.form.get(f'preco_{k}', '').strip()
        if val:
            try:
                _v = float(val.replace(',', '.'))
                # VDC-07 (16/10/26): só número finito ('nan'/'inf' passavam até pelo teto da Efí) e dentro da
                # faixa da tela: de R$ 5 (piso) até R$ 50 no modo Efí ou R$ 200 no Asaas
                if math.isfinite(_v):
                    try:
                        _ant = float(_salvos[k]) if k in _salvos else None
                    except (TypeError, ValueError):
                        _ant = None
                    if _ant is not None and math.isfinite(_ant) and abs(_v - _ant) < 0.005:
                        precos[k] = _salvos[k]
                    else:
                        precos[k] = round(min(max(_v, _piso_do_bar(b)), _teto if _teto is not None else 200.0), 2)
            except Exception:
                pass

    # requer_pix NÃO é deste form (só /painel/toggle-pix) — salvar config zerava e o celular passava a mostrar a chave PIX do bar
    moderar_ok = 1 if request.form.get('moderar_ok') else 0

    # 15/10/26 (B10): o liga/desliga do jukebox saiu daqui (interruptor que salva sozinho em
    # /painel/jukebox-ativo) e os gêneros viraram a lista única "Estilos do bar" (/painel/temas).
    # Este formulário NÃO mexe mais nessas 2 colunas (antes salvar aqui desligava o jukebox).
    conn = get_pubshow_db()
    conn.execute(
        '''UPDATE pubshow_businesses SET
           jukebox_hora_ini=?, jukebox_hora_fim=?,
           mensagem_jukebox=?, aviso_jukebox=?, aviso_expira=?,
           limite_pedidos_hora=?, tipos_bloqueados=?, precos_custom=?,
           moderar_ok=?
           WHERE id=?''',
        (hora_ini, hora_fim,
         mensagem or None, aviso or None, aviso_expira,
         limite, json.dumps(bloqueados), json.dumps(precos),
         moderar_ok, b['id'])
    )
    conn.commit(); conn.close()
    if _xhr():   # Lote D C (16/10/26): salva sozinho; devolve o que gravou (preço cortado no teto, por ex.)
        return jsonify({'ok': True, 'precos': precos})
    return redirect('/pubshow/painel?aba=config')


@pubshow_bp.route('/painel/senha', methods=['POST'])
@pubshow_login_required
def painel_senha():
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    atual  = request.form.get('senha_atual', '')
    nova   = request.form.get('senha_nova', '')
    conf   = request.form.get('senha_conf', '')
    if not check_password_hash(b['password_hash'], atual):
        return redirect('/pubshow/painel?aba=conta&erro=senha_errada')
    if len(nova) < 6:
        return redirect('/pubshow/painel?aba=conta&erro=senha_curta')
    if nova != conf:
        return redirect('/pubshow/painel?aba=conta&erro=senha_diferente')
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET password_hash=? WHERE id=?',
                 (generate_password_hash(nova), b['id']))
    conn.commit(); conn.close()
    _avisar_senha_trocada(b)
    return redirect('/pubshow/painel?aba=conta&ok=senha')


@pubshow_bp.route('/painel/relatorio')
@pubshow_login_required
def painel_relatorio():
    import json
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    conn = get_pubshow_db()

    # Dinheiro (15/10/26): a MESMA soma do painel — só pagamento confirmado; casa = sem gorjeta.
    # Semana = últimos 7 dias de Brasília (igual ao painel). As contagens de pedidos (tipos,
    # músicas, horários) seguem contando pedidos, pagos ou grátis.
    _hoje_d = _hoje_brt()
    _ini_mes = _hoje_d.replace(day=1)
    _fim_mes_ant = _ini_mes - timedelta(days=1)
    receita_hoje   = somar_pagos(conn, b['id'], _hoje_d, _hoje_d, 'casa')['total']
    receita_semana = somar_pagos(conn, b['id'], _hoje_d - timedelta(days=6), _hoje_d, 'casa')['total']
    _mes           = somar_pagos(conn, b['id'], _ini_mes, _hoje_d, 'casa')
    receita_mes    = _mes['total']
    receita_total  = somar_pagos(conn, b['id'], conta='casa')['total']

    # Top tipos pedidos (último mês) — contagem de pedidos; a receita vem da soma única
    _rec_tipo = somar_pagos(conn, b['id'], _hoje_d - timedelta(days=29), _hoje_d, 'tudo', por='tipo')
    top_tipos = [dict(t, receita=_rec_tipo.get(t['tipo'], {}).get('total', 0.0)) for t in conn.execute(
        '''SELECT tipo, COUNT(*) n
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now", "-30 days")
           GROUP BY tipo ORDER BY n DESC''', (b['id'],)
    ).fetchall()]

    # Músicas mais pedidas (último mês — apenas pagas)
    top_musicas = conn.execute(
        '''SELECT titulo_pedido, COUNT(*) n FROM pubshow_pedidos
           WHERE business_id=? AND titulo_pedido IS NOT NULL AND status!="aguardando_pix"
           AND created_at>=datetime("now", "-30 days")
           GROUP BY titulo_pedido ORDER BY n DESC LIMIT 10''', (b['id'],)
    ).fetchall()

    # Pedidos por dia (últimos 14 dias): n = pedidos; r = dinheiro da casa (soma única)
    _n_dia = {r['dia']: r['n'] for r in conn.execute(
        '''SELECT date(created_at,"-3 hours") dia, COUNT(*) n
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now", "-14 days")
           GROUP BY dia ORDER BY dia''', (b['id'],)
    ).fetchall()}
    _rec_dia = somar_pagos(conn, b['id'], _hoje_d - timedelta(days=13), _hoje_d, 'casa', por='dia')
    por_dia = [{'dia': d, 'n': _n_dia.get(d, 0), 'r': _rec_dia.get(d, {}).get('total', 0.0)}
               for d in sorted(set(_n_dia) | set(_rec_dia))]

    # Pedidos por hora do dia — últimos 30 dias (horários de pico)
    por_hora_raw = conn.execute(
        '''SELECT CAST(strftime('%H', datetime(created_at, '-3 hours')) AS INTEGER) hora, COUNT(*) n
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now", "-30 days")
           GROUP BY hora ORDER BY hora''', (b['id'],)
    ).fetchall()
    # Normaliza para 0..23 com zeros nos horários sem pedido
    horas_dict = {row['hora']: row['n'] for row in por_hora_raw}
    por_hora = [{'hora': h, 'n': horas_dict.get(h, 0)} for h in range(24)]

    # Pedidos por dia da semana (0=Dom ... 6=Sáb) — últimos 30 dias
    por_weekday_raw = conn.execute(
        '''SELECT CAST(strftime('%w', datetime(created_at, '-3 hours')) AS INTEGER) wd, COUNT(*) n
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND created_at>=datetime("now", "-30 days")
           GROUP BY wd''', (b['id'],)
    ).fetchall()
    dias_semana = ['Dom','Seg','Ter','Qua','Qui','Sex','Sáb']
    wd_dict = {row['wd']: row['n'] for row in por_weekday_raw}
    por_weekday = [{'dia': dias_semana[i], 'n': wd_dict.get(i, 0)} for i in range(7)]

    # Total de pedidos do mês (contagem) e ticket médio dos PAGOS (música grátis não puxa pra baixo)
    mes_stats = conn.execute(
        '''SELECT COUNT(*) n
           FROM pubshow_pedidos WHERE business_id=? AND status!="aguardando_pix"
           AND strftime("%Y-%m",datetime(created_at,"-3 hours"))=strftime("%Y-%m",date("now","-3 hours"))''',
        (b['id'],)
    ).fetchone()
    total_pedidos_mes = mes_stats['n'] if mes_stats else 0
    ticket_medio = round(_mes['total'] / _mes['n'], 2) if _mes['n'] else 0.0
    # 16/10/26 (C1-12): a conta fecha — momentos pagos, músicas grátis e gorjetas separados
    momentos_mes = _mes['n']
    gorjetas_mes = somar_pagos(conn, b['id'], _ini_mes, _hoje_d, 'gorjeta')['n']
    _phg = ','.join('?' * len(TIPOS_SEMPRE_GRATIS))
    musicas_mes = conn.execute(
        f'''SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND tipo IN ({_phg})
            AND status NOT IN ("aguardando_pix","recusado")
            AND strftime("%Y-%m",datetime(created_at,"-3 hours"))=strftime("%Y-%m",date("now","-3 hours"))''',
        (b['id'], *TIPOS_SEMPRE_GRATIS)).fetchone()[0]

    # Receita mês anterior — mesmo critério do mês atual (antes somava gorjeta e recusado)
    receita_mes_anterior = somar_pagos(conn, b['id'], _fim_mes_ant.replace(day=1), _fim_mes_ant, 'casa')['total']

    # Projeção do mês (ritmo atual × dias restantes)
    import calendar as _cal
    _hoje = datetime.utcnow() - timedelta(hours=3)
    _dias_no_mes = _cal.monthrange(_hoje.year, _hoje.month)[1]
    _dia_atual = _hoje.day
    # 16/10/26 (C1-12): o ritmo conta só os dias em que o bar EXISTIA no mês, e com menos de 7 dias de
    # dados não tem projeção (1 dia de dados virava "Projeção R$ 10")
    _dias_dados = _dia_atual
    try:
        _cad = datetime.strptime((b['created_at'] or '')[:19], _FMT_UTC) - timedelta(hours=3)
        if (_cad.year, _cad.month) == (_hoje.year, _hoje.month):
            _dias_dados = _dia_atual - _cad.day + 1
    except Exception:
        pass
    if _dias_dados >= 7:
        projecao_mes = float(receita_mes) + float(receita_mes) / _dias_dados * (_dias_no_mes - _dia_atual)
    else:
        projecao_mes = 0

    # Melhor dia da semana e melhor hora
    melhor_wd = max(por_weekday, key=lambda x: x['n']) if por_weekday else None
    melhor_hora = max(por_hora, key=lambda x: x['n']) if por_hora else None

    conn.close()
    return render_template('pubshow/relatorio.html',
                           b=dict(b),
                           receita_hoje=float(receita_hoje),
                           receita_semana=float(receita_semana),
                           receita_mes=float(receita_mes),
                           receita_total=float(receita_total),
                           receita_mes_anterior=receita_mes_anterior,
                           total_pedidos_mes=total_pedidos_mes,
                           momentos_mes=momentos_mes, musicas_mes=musicas_mes, gorjetas_mes=gorjetas_mes,
                           ticket_medio=ticket_medio,
                           projecao_mes=projecao_mes,
                           melhor_wd=melhor_wd,
                           melhor_hora=melhor_hora,
                           top_tipos=top_tipos,
                           top_musicas=[dict(m) for m in top_musicas],
                           por_dia=por_dia,
                           por_hora=por_hora,
                           por_weekday=por_weekday,
                           tipos=TIPOS_PEDIDO,
                           # '%B' saía em inglês no servidor ("October de 2026")
                           mes_atual=f'{_MESES_PT[_hoje.month - 1]} de {_hoje.year}'.capitalize())


@pubshow_bp.route('/painel/novo-qr', methods=['POST'])
@pubshow_login_required
def painel_novo_qr():
    """Gera novo jukebox_token — invalida QR codes antigos imediatamente."""
    b = _get_business()
    if not b: return redirect('/pubshow/entrar')
    # 15/10/26 (D4-10): só com "NOVO QR" digitado — um toque sem querer matava os QRs de todas as mesas
    if request.form.get('confirmar', '').strip().upper() != 'NOVO QR':
        return redirect('/pubshow/painel?aba=ajustes&erro=novo_qr')
    novo = _gerar_jukebox_token()
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET jukebox_token=? WHERE id=?', (novo, b['id']))
    conn.commit(); conn.close()
    _bump_tv_bar(b['id'])   # a TV mostra o QR novo (antes ficava com o velho até alguém recarregar)
    log.info('[PUBSHOW] Novo QR gerado para business_id=%s', b['id'])
    return redirect('/pubshow/painel/qrcode')


# ── MULTI-LOCAIS (plano Rede) ─────────────────────────────────────────────────

@pubshow_bp.route('/painel/rede')
@pubshow_login_required
def painel_rede():
    """Painel unificado para donos com múltiplos locais (plano Rede)."""
    if not _rede_ligada():   # Rede desligada no plano único (15/10/26): 404, dados ficam
        return 'Página não encontrada', 404
    b = _get_business()
    conn = get_pubshow_db()
    # Busca todas as filiais vinculadas a este dono
    filiais = [dict(f) for f in conn.execute(
        '''SELECT b.*,
           (SELECT COUNT(*) FROM pubshow_pedidos p WHERE p.business_id=b.id AND p.status="pendente") fila
           FROM pubshow_businesses b
           WHERE b.owner_business_id=? OR b.id=?
           ORDER BY b.nome''',
        (b['id'], b['id'])
    ).fetchall()]
    # receita de hoje = a mesma soma do painel de cada filial (15/10/26)
    _hoje = _hoje_brt()
    _rec = somar_pagos(conn, None, _hoje, _hoje, 'casa', por='bar')
    for f in filiais:
        f['receita_hoje'] = _rec.get(f['id'], {}).get('total', 0.0)
    conn.close()
    return render_template('pubshow/painel_rede.html',
                           b=dict(b),
                           filiais=filiais,
                           planos=PLANOS)


@pubshow_bp.route('/painel/rede/nova-filial', methods=['GET', 'POST'])
@pubshow_login_required
def painel_rede_nova_filial():
    """Cadastra uma nova unidade/filial vinculada ao dono atual (plano Rede)."""
    if not _rede_ligada():   # Rede desligada no plano único (15/10/26): 404, dados ficam
        return 'Página não encontrada', 404
    b = dict(_get_business())  # dict() para .get() funcionar abaixo
    erro = ''
    if request.method == 'POST':
        nome     = request.form.get('nome', '').strip()
        tipo     = request.form.get('tipo', 'bar')
        email    = request.form.get('email', '').strip().lower()
        telefone = request.form.get('telefone', '').strip()
        senha    = request.form.get('senha', '')
        if not all([nome, email, telefone, senha]):
            erro = 'Preencha todos os campos.'
        elif len(senha) < 6:
            erro = 'Senha mínima de 6 caracteres.'
        else:
            try:
                code   = _gerar_code()
                jtoken = _gerar_jukebox_token()
                trial  = (datetime.now() + timedelta(days=365 * 10)).isoformat()  # plano herdado do dono
                conn = get_pubshow_db()
                conn.execute(
                    '''INSERT INTO pubshow_businesses
                       (nome, tipo, email, telefone, cpf_cnpj, password_hash, code,
                        plano, plano_ativo, canal_atual, trial_ends, jukebox_token, owner_business_id)
                       VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?)''',
                    (nome, tipo, email, telefone,
                     f'filial_{b["id"]}_{_gerar_code(6)}',  # CPF/CNPJ sintético
                     generate_password_hash(senha), code,
                     'bar', 'rock', trial, jtoken, b['id'])
                )
                conn.commit(); conn.close()
                log.info('[PUBSHOW-REDE] Nova filial criada por business_id=%s: %s', b['id'], nome)
                return redirect('/pubshow/painel/rede?ok=filial')
            except Exception as ex:
                if 'UNIQUE' in str(ex):
                    erro = 'Este e-mail já está cadastrado.'
                else:
                    erro = f'Erro ao cadastrar filial: {ex}'
    return render_template('pubshow/painel_rede_nova.html',
                           b=dict(b), erro=erro, tipos=TIPOS_ESTABELECIMENTO)


# ── ADMIN MASTER ──────────────────────────────────────────────────────────────

_ADMIN_MAX_GLOBAL = 10


@pubshow_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    erro = ''
    if request.method == 'POST':
        _chave = 'admin_ip:' + _ip_real()
        # + teto GLOBAL (15/10/26): o IP vem de cabeçalho; quem chama a origem direto troca a cada
        # tentativa. 10 erros em 15 min no total travam o admin pra todos (o /saas-admin segue).
        if _rl_bloqueado(_chave) or _rl_bloqueado('admin_global', max_n=_ADMIN_MAX_GLOBAL):
            return render_template('pubshow/admin_login.html', erro=_RL_MSG), 429
        senha = request.form.get('senha', '')
        admin_pass = os.environ.get('PUBSHOW_ADMIN_PASS', '')
        if admin_pass and hmac.compare_digest(senha.encode(), admin_pass.encode()):
            session['pubshow_admin'] = True
            return redirect('/pubshow/admin')
        _rl_falhou(_chave, 'admin_global')
        erro = 'Senha incorreta.'
    return render_template('pubshow/admin_login.html', erro=erro)


@pubshow_bp.route('/admin/logout')
def admin_logout():
    session.pop('pubshow_admin', None)
    return redirect('/pubshow/admin/login')


@pubshow_bp.route('/admin')
@_admin_required
def admin_dashboard():
    conn = get_pubshow_db()
    bars = [dict(r) for r in conn.execute(
        '''SELECT b.*,
           (SELECT COUNT(*) FROM pubshow_pedidos p WHERE p.business_id=b.id) total_pedidos,
           (SELECT COUNT(*) FROM pubshow_pedidos p WHERE p.business_id=b.id AND p.status="pendente") fila_atual
           FROM pubshow_businesses b ORDER BY b.created_at DESC'''
    ).fetchall()]
    # Receita Jukebox = a mesma soma do painel (15/10/26): só pago confirmado, casa sem gorjeta
    _rec = somar_pagos(conn, None, conta='casa', por='bar')
    for _b in bars:
        _b['total_receita'] = _rec.get(_b['id'], {}).get('total', 0.0)
    total_videos = conn.execute('SELECT COUNT(*) FROM pubshow_videos WHERE ativo=1').fetchone()[0]
    total_pedidos_hoje = conn.execute(
        '''SELECT COUNT(*) FROM pubshow_pedidos
           WHERE date(created_at,'-3 hours')=date("now","-3 hours")'''
    ).fetchone()[0]
    _hoje = _hoje_brt()
    receita_hoje = somar_pagos(conn, None, _hoje, _hoje, 'casa')['total']
    # Emails pendentes de enviar
    emails_pendentes = conn.execute(
        '''SELECT COUNT(*) FROM pubshow_email_queue
           WHERE sent_at IS NULL AND scheduled_at <= ?''',
        (datetime.now().isoformat(),)
    ).fetchone()[0]
    emails_total = conn.execute('SELECT COUNT(*) FROM pubshow_email_queue').fetchone()[0]
    # 16/10/26 (crítico C3): PIX PAGO e recusado pelo gerente = dinheiro do cliente parado na conta do
    # PubShow, fora de toda soma. Lista pra devolver (até ter o estorno automático).
    pix_a_devolver = [dict(r) for r in conn.execute(
        '''SELECT p.id, p.business_id, b.nome AS bar_nome, p.tipo, p.nome_cliente, p.valor, p.valor_cobrado,
                  p.pago_em, p.asaas_payment_id, p.efi_txid
           FROM pubshow_pedidos p JOIN pubshow_businesses b ON b.id = p.business_id
           WHERE p.status='recusado' AND p.pago_em IS NOT NULL AND p.estornado_em IS NULL
           ORDER BY p.pago_em DESC LIMIT 100''').fetchall()]
    if pix_a_devolver and _efi_ligado():
        # Lote C (16/10/26): o PIX Efí que o robô já está devolvendo (ou devolveu) não é "a devolver na mão".
        # A devolução recusada pela Efí (devolver_erro) continua aqui e no card "devoluções travadas" do saas-admin.
        try:
            _cx = _pp_conn()
            _auto = {r[0] for r in _cx.execute("SELECT pedido_id FROM pubshow_pay_cobrancas "
                                               "WHERE status IN ('devolvendo','devolvida')")}
            _cx.close()
            pix_a_devolver = [p for p in pix_a_devolver if p['id'] not in _auto]
        except Exception as ex:
            log.warning('[PUBSHOW] admin: devoluções Efí não lidas: %s', ex)
    # VCa-05 (16/10/26): o texto do card depende do gateway do PEDIDO. Pedido com cobrança Efí não se estorna
    # no Asaas. Só SQL (a tabela pode nem existir): no modo asaas o pubshow_pay continua sem ser chamado.
    _efi_ped = set()
    if pix_a_devolver and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                       "AND name='pubshow_pay_cobrancas'").fetchone():
        _ids = [p['id'] for p in pix_a_devolver]
        _efi_rows = {r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT pedido_id, status, estornar FROM pubshow_pay_cobrancas WHERE pedido_id IN ({','.join('?' * len(_ids))})",
            _ids)}
        _efi_st = {k: v[0] for k, v in _efi_rows.items()}
        # já devolvido/devolvendo pela Efí não é "a devolver" (também depois de um rollback pro asaas)
        pix_a_devolver = [p for p in pix_a_devolver if _efi_st.get(p['id']) not in ('devolvendo', 'devolvida')]
        _efi_ped = set(_efi_st)
    for p in pix_a_devolver:
        p['efi'] = p['id'] in _efi_ped
        # CC3 R3 (16/10/26): o texto do card sai do status REAL da cobrança Efí (não só do gateway)
        if p['efi']:
            p['efi_status'], p['efi_marcado'] = _efi_rows[p['id']][0], bool(_efi_rows[p['id']][1])
    conn.close()
    # Lote D A3 (16/10/26): pausa de 60 dias (só bar com a Efí ligada) — o admin vê quem está pausado ou perto disso
    # e reativa com 1 botão. No modo Asaas nada disso é lido (o pubshow_pay não é chamado).
    for _b in bars:
        _pz = _efi_pausa(_b) if _efi_ligado() else None
        _b['_pausa'] = _pz if (_pz and (_pz['pausado'] or _pz['aviso'])) else None
    return render_template('pubshow/admin.html',
                           pix_a_devolver=pix_a_devolver,
                           efi_robo=_efi_ligado(),   # VCa-05: o robô Efí só roda com PUBSHOW_GATEWAY=efi
                           bars=[dict(b) for b in bars],
                           total_videos=total_videos,
                           total_pedidos_hoje=total_pedidos_hoje,
                           receita_hoje=receita_hoje,
                           emails_pendentes=emails_pendentes,
                           emails_total=emails_total,
                           canais=CANAIS, planos=PLANOS,
                           now=datetime.now().isoformat())


@pubshow_bp.route('/admin/bar/<int:bid>')
@_admin_required
def admin_bar(bid):
    import json as _json
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE id=?', (bid,)).fetchone()
    if not b:
        conn.close(); return redirect('/pubshow/admin')
    pedidos = conn.execute(
        '''SELECT * FROM pubshow_pedidos WHERE business_id=?
           ORDER BY created_at DESC LIMIT 50''', (bid,)
    ).fetchall()
    ass = conn.execute(
        'SELECT * FROM pubshow_assinaturas WHERE business_id=?', (bid,)
    ).fetchone()
    conn.close()
    try:
        anuncios_list = _json.loads(b['anuncios_json'] or '[]')
    except Exception:
        anuncios_list = []
    return render_template('pubshow/admin_bar.html',
                           b=dict(b), pedidos=[dict(p) for p in pedidos],
                           # Lote D A3 (16/10/26): estado da pausa de 60 dias (só com a Efí ligada pro bar)
                           pausa=(_efi_pausa(dict(b)) if _efi_ligado() else None),
                           ass=dict(ass) if ass else None,
                           canais=CANAIS, planos=PLANOS, tipos=TIPOS_PEDIDO,
                           asaas_ativo=bool(os.environ.get('ASAAS_API_KEY')),
                           anuncios_list=anuncios_list)


# ── 💸 REPASSES MANUAIS (16/10/26; junção 17/10/26: + PIX da ponte Efí) ─────────────────────────────────────
# Sem split ligado: todo PIX do jukebox cai na conta do Diogo — no Asaas, ou na conta Efí da RifaJá (ponte Efí,
# bares de PUBSHOW_EFI_BARES) — e ele repassa ao bar toda segunda, em 2 valores: BAR (momentos) e GORJETA (equipe).
# Bar no SPLIT (PUBSHOW_GATEWAY=efi) não entra aqui: a parte dele já cai na conta Efí dele na hora.
#
# "PAGO" aqui = pago DE VERDADE pelo gateway, com PROVA:
#   - cobrança no gateway (asaas_payment_id OU efi_txid) E pago_em preenchido E status liberado;
#   - E a prova: pago_em gravado por uma CONFIRMAÇÃO (webhook do Asaas, consulta da Efí, "PIX caiu" do admin: nesses o
#     pix_conferido fica vazio) OU a conferência no gateway (pix_conferido = RECEIVED/CONFIRMED/EFI:CONCLUIDA).
#     O boot da versão grande preenche o histórico (pago_em vazio) confiando no STATUS e marca pix_conferido='HISTORICO':
#     o status sozinho não prova pagamento (rotas antigas do painel/TV mudavam status de QR não pago). Esses só contam
#     depois de conferidos no gateway (_repasse_conferir: GET /payments/{id} no Asaas, GET /v2/cob/{txid} na Efí).
# Status que entra no devido: 'pendente', 'exibido', 'dispensado'. 'aguardando_ok' (pago, gerente ainda não decidiu)
# fica À PARTE ("em moderação"). 'aguardando_pix' e 'recusado' NUNCA contam.
# Pedido pago SEM cobrança no gateway (PIX manual na chave do bar / "já paguei") não passou pela conta do Diogo:
# aparece à parte, "conferir à mão", sem somar.
_REPASSE_STATUS_DEVIDO = ('pendente', 'exibido', 'dispensado')
_REPASSE_STATUS_PAGO = ('pendente', 'aguardando_ok', 'exibido', 'dispensado')   # "parece pago"
_ASAAS_STATUS_PAGO = ('RECEIVED', 'CONFIRMED')   # PIX caiu (RECEIVED_IN_CASH = baixa manual, sem dinheiro no Asaas)
_BRT_OFFSET = timedelta(hours=-3)   # Brasília (sem horário de verão desde 2019); o banco grava UTC
_TEM_ASAAS = "TRIM(COALESCE(asaas_payment_id,''))<>''"
_TEM_EFI = "TRIM(COALESCE(efi_txid,''))<>''"


def _brl(v):
    """1234.5 → 'R$ 1.234,50'."""
    try:
        v = float(v or 0)
    except Exception:
        v = 0.0
    s = f'{abs(v):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return ('-R$ ' if v < 0 else 'R$ ') + s


def _utc_agora():
    from datetime import timezone as _tz
    return datetime.now(_tz.utc).replace(tzinfo=None, microsecond=0)


def _utc_txt(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _repasse_brt(s, fmt='%d/%m %H:%M'):
    """'2026-10-20 15:00:00' (UTC do banco) → '20/10 12:00' (Brasília)."""
    try:
        dt = datetime.strptime(str(s)[:19].replace('T', ' '), '%Y-%m-%d %H:%M:%S')
        return (dt + _BRT_OFFSET).strftime(fmt)
    except Exception:
        return str(s or '')


def _repasse_semanas(agora_utc):
    """Fronteiras da semana de Brasília (segunda 00:00 BRT), devolvidas em UTC.
    → (ini_semana_passada_utc, ini_esta_semana_utc, segunda_brt)."""
    agora_brt = agora_utc + _BRT_OFFSET
    seg_brt = datetime(agora_brt.year, agora_brt.month, agora_brt.day) - timedelta(days=agora_brt.weekday())
    ini_esta = seg_brt - _BRT_OFFSET          # 00:00 BRT = 03:00 UTC
    return ini_esta - timedelta(days=7), ini_esta, seg_brt


def _repasse_where_pago(statuses=_REPASSE_STATUS_DEVIDO):
    """Pago COM prova (ver o cabeçalho): cobrança no gateway + pago_em de uma confirmação ou conferido no gateway."""
    st = ','.join('?' * len(statuses))
    pagos = ','.join('?' * len(_ASAAS_STATUS_PAGO))
    return (f"({_TEM_ASAAS} OR {_TEM_EFI}) "
            f"AND TRIM(COALESCE(pago_em,''))<>'' "
            f"AND (pix_conferido IS NULL OR pix_conferido IN ({pagos}, 'EFI:CONCLUIDA')) "
            f"AND status IN ({st}) AND COALESCE(valor,0) > 0"), list(_ASAAS_STATUS_PAGO) + list(statuses)


def _repasse_somar(conn, extra_sql, extra_params, statuses=_REPASSE_STATUS_DEVIDO):
    """{business_id: {bar, bar_n, gorj, gorj_n, asaas, efi}} dos pedidos pagos com prova (+ filtro extra).
    asaas/efi = quanto do total (bar + gorjeta) está em cada conta do Diogo."""
    w, wp = _repasse_where_pago(statuses)
    eq = ','.join('?' * len(TIPOS_EQUIPE))
    rows = conn.execute(
        f'''SELECT business_id,
               COALESCE(SUM(CASE WHEN tipo IN ({eq}) THEN 0 ELSE valor END),0) AS bar,
               COALESCE(SUM(CASE WHEN tipo IN ({eq}) THEN 0 ELSE 1 END),0)     AS bar_n,
               COALESCE(SUM(CASE WHEN tipo IN ({eq}) THEN valor ELSE 0 END),0) AS gorj,
               COALESCE(SUM(CASE WHEN tipo IN ({eq}) THEN 1 ELSE 0 END),0)     AS gorj_n,
               COALESCE(SUM(CASE WHEN {_TEM_EFI} THEN valor ELSE 0 END),0)     AS efi,
               COALESCE(MAX(id),0) AS max_id
            FROM pubshow_pedidos WHERE {w} {extra_sql} GROUP BY business_id''',
        list(TIPOS_EQUIPE) * 4 + wp + list(extra_params)
    ).fetchall()
    out = {}
    for r in rows:
        bar, gorj, efi = round(float(r['bar']), 2), round(float(r['gorj']), 2), round(float(r['efi']), 2)
        out[r['business_id']] = {'bar': bar, 'bar_n': int(r['bar_n']), 'gorj': gorj, 'gorj_n': int(r['gorj_n']),
                                 'efi': efi, 'asaas': round(bar + gorj - efi, 2), 'max_id': int(r['max_id'])}
    return out


def _repasses_calcular(conn, agora_utc=None):
    """Tudo que a tela /admin/repasses mostra (sem os saldos)."""
    agora = agora_utc or _utc_agora()
    a = _utc_txt(agora)
    ini_p, ini_e, seg_brt = _repasse_semanas(agora)
    # Devido = pago com prova, ainda não marcado por nenhum repasse (marca é por pedido)
    devido  = _repasse_somar(conn, 'AND repasse_manual_id IS NULL AND datetime(created_at) <= datetime(?)', [a])
    esta    = _repasse_somar(conn, 'AND datetime(created_at) >= datetime(?) AND datetime(created_at) <= datetime(?)',
                             [_utc_txt(ini_e), a])
    passada = _repasse_somar(conn, 'AND datetime(created_at) >= datetime(?) AND datetime(created_at) < datetime(?)',
                             [_utc_txt(ini_p), _utc_txt(ini_e)])
    # Pago mas o gerente ainda não decidiu (aguardando_ok): mostra, NÃO soma
    moder   = _repasse_somar(conn, 'AND repasse_manual_id IS NULL AND datetime(created_at) <= datetime(?)', [a],
                             statuses=('aguardando_ok',))
    ultimos = {r['business_id']: dict(r) for r in conn.execute(
        '''SELECT r.* FROM pubshow_repasses_manuais r
           JOIN (SELECT business_id, MAX(id) AS mid FROM pubshow_repasses_manuais GROUP BY business_id) u
             ON u.mid = r.id''').fetchall()}
    nomes = {r['id']: r['nome'] for r in conn.execute('SELECT id, nome FROM pubshow_businesses').fetchall()}
    zero = {'bar': 0.0, 'bar_n': 0, 'gorj': 0.0, 'gorj_n': 0, 'asaas': 0.0, 'efi': 0.0, 'max_id': 0}
    bares = []
    for bid in set(devido) | set(esta) | set(passada) | set(ultimos) | set(moder):
        d = devido.get(bid, zero)
        u = ultimos.get(bid)
        if u:
            u['ate_brt'] = _repasse_brt(u['ate_em'])
        bares.append({
            'id': bid, 'nome': nomes.get(bid) or f'Bar #{bid}',
            'devido': d, 'devido_total': round(d['bar'] + d['gorj'], 2),
            'esta': esta.get(bid, zero), 'passada': passada.get(bid, zero),
            'moderacao': moder.get(bid, zero),
            'ultimo': u,
        })
    bares.sort(key=lambda x: (-x['devido_total'], (x['nome'] or '').lower()))
    total_bar  = round(sum(x['devido']['bar'] for x in bares), 2)
    total_gorj = round(sum(x['devido']['gorj'] for x in bares), 2)
    total_efi  = round(sum(x['devido']['efi'] for x in bares), 2)

    # À parte: pagos SEM cobrança no gateway (PIX manual na chave do bar / "já paguei") — NÃO somam
    st = ','.join('?' * len(_REPASSE_STATUS_PAGO))
    conferir = [dict(r) for r in conn.execute(
        f'''SELECT p.id, p.business_id, p.tipo, p.valor, p.status, p.nome_cliente, p.created_at,
                   b.nome AS bar_nome
            FROM pubshow_pedidos p LEFT JOIN pubshow_businesses b ON b.id = p.business_id
            WHERE TRIM(COALESCE(p.asaas_payment_id,''))='' AND TRIM(COALESCE(p.efi_txid,''))=''
              AND COALESCE(p.via_gateway,0)=0
              AND p.status IN ({st}) AND COALESCE(p.valor,0) > 0
              AND datetime(p.created_at) >= datetime(?, '-30 days')
            ORDER BY p.created_at DESC LIMIT 100''',
        list(_REPASSE_STATUS_PAGO) + [a]).fetchall()]
    for c in conferir:
        c['quando'] = _repasse_brt(c['created_at'])
        c['tipo_nome'] = TIPOS_PEDIDO.get(c['tipo'], {}).get('nome', c['tipo'])
        c['equipe'] = c['tipo'] in TIPOS_EQUIPE

    # À parte: status "de pago" + cobrança no gateway, mas SEM prova — não somam. Ou ainda não conferido (histórico
    # de antes da versão grande / confirmação perdida), ou o gateway disse que NÃO foi pago (QR abandonado).
    w_ok, wp_ok = _repasse_where_pago(_REPASSE_STATUS_PAGO)
    sem_prova = [dict(r) for r in conn.execute(
        f'''SELECT p.id, p.business_id, p.tipo, p.valor, p.status, p.created_at, p.pix_conferido,
                   ({_TEM_EFI.replace('efi_txid', 'p.efi_txid')}) AS eh_efi, b.nome AS bar_nome
            FROM pubshow_pedidos p LEFT JOIN pubshow_businesses b ON b.id = p.business_id
            WHERE ({_TEM_ASAAS.replace('asaas_payment_id', 'p.asaas_payment_id')}
                   OR {_TEM_EFI.replace('efi_txid', 'p.efi_txid')})
              AND p.repasse_manual_id IS NULL
              AND p.status IN ({st}) AND COALESCE(p.valor,0) > 0
              AND p.id NOT IN (SELECT id FROM pubshow_pedidos WHERE {w_ok})
            ORDER BY p.created_at DESC LIMIT 100''',
        list(_REPASSE_STATUS_PAGO) + wp_ok).fetchall()]
    for c in sem_prova:
        c['quando'] = _repasse_brt(c['created_at'])
        c['tipo_nome'] = TIPOS_PEDIDO.get(c['tipo'], {}).get('nome', c['tipo'])
        _ac = c['pix_conferido'] or ''
        c['pendente_conf'] = (not _ac) or _ac.startswith('ERRO') or _ac in ('HISTORICO', 'ADMIN')
        _onde = 'Efí' if c['eh_efi'] else 'Asaas'
        c['gateway_txt'] = (f'ainda não conferido na {_onde}' if c['pendente_conf']
                            else f'{_onde} diz: {_ac.replace("EFI:", "")}')

    _pend_bar = {}
    for c in sem_prova:
        if c['pendente_conf']:
            _pend_bar[c['business_id']] = _pend_bar.get(c['business_id'], 0) + 1
    for x in bares:
        x['pendente_conf_n'] = _pend_bar.get(x['id'], 0)   # crítico dinheiro C1: conferir antes de repassar
    historico = [dict(r) for r in conn.execute(
        '''SELECT r.*, b.nome AS bar_nome FROM pubshow_repasses_manuais r
           LEFT JOIN pubshow_businesses b ON b.id = r.business_id
           ORDER BY r.id DESC LIMIT 30''').fetchall()]
    for h in historico:
        h['ate_brt'] = _repasse_brt(h['ate_em'])

    return {
        'agora_brt': _repasse_brt(a, '%d/%m %H:%M'),
        'seg_brt': seg_brt.strftime('%d/%m'),
        'passada_ini': (seg_brt - timedelta(days=7)).strftime('%d/%m'),
        'passada_fim': (seg_brt - timedelta(days=1)).strftime('%d/%m'),
        'bares': bares,
        'total_bar': total_bar, 'total_gorj': total_gorj,
        'total_devido': round(total_bar + total_gorj, 2),
        'total_efi': total_efi, 'total_asaas': round(total_bar + total_gorj - total_efi, 2),
        'total_moderacao': round(sum(x['moderacao']['bar'] + x['moderacao']['gorj'] for x in bares), 2),
        'conferir': conferir, 'historico': historico, 'sem_prova': sem_prova,
        'sem_prova_pend': sum(1 for c in sem_prova if c['pendente_conf']),
    }


def _asaas_saldo():
    """Saldo da conta Asaas. GET /v3/finance/balance → {"balance": 5210.96}
    (docs.asaas.com, 'Recuperar saldo da conta'). None se não der pra ler."""
    if not os.environ.get('ASAAS_API_KEY'):
        return None
    try:
        r = _asaas_req('GET', '/finance/balance')
    except Exception as ex:
        r = {'error': str(ex)}
    bal = r.get('balance') if isinstance(r, dict) else None
    if isinstance(bal, (int, float)) and not isinstance(bal, bool):
        return round(float(bal), 2)
    log.warning('[PUBSHOW] Repasses: não consegui ler o saldo do Asaas: %s', str(r)[:200])
    return None


def _repasse_conferir(bid=None, limite=15):
    """Pedido com status "de pago" + cobrança no gateway mas SEM prova (pago_em vazio, ou o histórico preenchido no boot:
    pix_conferido='HISTORICO') → pergunta ao gateway: Asaas GET /payments/{id} (RECEIVED/CONFIRMED = pago); ponte
    Efí GET /v2/cob/{txid} pela conta Efí do PubShow (_efi_cli; CONCLUIDA e sem devolução = pago). Grava em pix_conferido
    (e pago_em, se pago). Falha de rede/erro → 'ERRO|<utc>', tenta de novo depois (o mais antigo primeiro).
    Retorna quantos conferiu. Só leitura nos gateways."""
    st = ','.join('?' * len(_REPASSE_STATUS_PAGO))
    filtro_bar = 'AND business_id=?' if bid is not None else ''
    conn = get_pubshow_db()
    try:
        rows = conn.execute(
            f'''SELECT id, asaas_payment_id, efi_txid FROM pubshow_pedidos
                WHERE ({_TEM_ASAAS} OR {_TEM_EFI})
                  AND ((TRIM(COALESCE(pago_em,''))='' AND pix_conferido IS NULL)
                       OR pix_conferido IN ('HISTORICO','ADMIN') OR pix_conferido LIKE 'ERRO%')
                  AND repasse_manual_id IS NULL
                  AND status IN ({st}) AND COALESCE(valor,0) > 0 {filtro_bar}
                ORDER BY (CASE WHEN pix_conferido LIKE 'ERRO%' THEN 1 ELSE 0 END), pix_conferido, id
                LIMIT ?''',
            list(_REPASSE_STATUS_PAGO) + ([bid] if bid is not None else []) + [int(limite)]
        ).fetchall()
        feitos, erros_seguidos = 0, 0
        for r in rows:
            efi_tx = (r['efi_txid'] or '').strip()
            pay = (r['asaas_payment_id'] or '').strip()
            res = None
            if efi_tx:
                _cli = _efi_cli()
                if _cli is None:
                    continue
                try:
                    c = _cli.consultar(efi_tx)
                except Exception as ex:
                    c = {'erro': str(ex)[:200]}
                if isinstance(c, dict) and not c.get('erro') and c.get('status'):
                    _dev = any(str(d.get('status') or '').upper() == 'DEVOLVIDO'
                               for p in (c.get('pix') or []) for d in (p.get('devolucoes') or []))
                    res = 'EFI:DEVOLVIDO' if _dev else f"EFI:{str(c['status']).strip().upper()[:30]}"
            else:
                if not os.environ.get('ASAAS_API_KEY'):
                    continue
                if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}', pay):
                    conn.execute('UPDATE pubshow_pedidos SET pix_conferido=? WHERE id=?', ('ID_INVALIDO', r['id']))
                    conn.commit()
                    continue
                try:
                    resp = _asaas_req('GET', f'/payments/{pay}')
                except Exception as ex:
                    resp = {'error': str(ex)}
                ast = resp.get('status') if isinstance(resp, dict) else None
                if isinstance(ast, str) and ast.strip():
                    res = ast.strip().upper()[:40]
                elif isinstance(resp, dict) and resp.get('errors'):
                    res = 'NAO_ACHADO'   # o Asaas respondeu que não existe: definitivo (não trava a fila da conferência)
            if res:
                erros_seguidos = 0
                if res in _ASAAS_STATUS_PAGO or res == 'EFI:CONCLUIDA':
                    conn.execute(
                        "UPDATE pubshow_pedidos SET pago_em=COALESCE(NULLIF(TRIM(pago_em),''), created_at, CURRENT_TIMESTAMP), "
                        "pix_conferido=? WHERE id=?", (res, r['id']))
                else:
                    conn.execute('UPDATE pubshow_pedidos SET pix_conferido=? WHERE id=?', (res, r['id']))
                feitos += 1
            else:
                erros_seguidos += 1
                conn.execute('UPDATE pubshow_pedidos SET pix_conferido=? WHERE id=?',
                             ('ERRO|' + _utc_txt(_utc_agora()), r['id']))
            conn.commit()
            if erros_seguidos >= 3:   # gateway fora / chave errada: não trava a tela
                log.warning('[PUBSHOW] Repasses: conferência no gateway parou (3 erros seguidos)')
                break
        return feitos
    except Exception as ex:
        log.error('[PUBSHOW] Repasses: conferência no gateway erro: %s', ex, exc_info=True)
        return 0
    finally:
        conn.close()


def _repasse_registrar(bid, obs='', agora_utc=None, ate_id=None, total_cent=None):
    """Grava 1 repasse do bar com o valor calculado AQUI (nunca o do formulário) e marca os pedidos que ele pagou.
    Crítico dinheiro C1 (17/10/26): só os pedidos que a TELA somou (id <= ate_id) e só se o total recalculado bater
    com o que a tela mostrou (total_cent); mudou (PIX novo, conferência achou mais) → 'mudou', nada gravado.
    Retorna {'codigo': ok|duplo|zero|bar|mudou|erro, ...}."""
    agora = agora_utc or _utc_agora()
    a = _utc_txt(agora)
    conn = get_pubshow_db()
    try:
        conn.execute('BEGIN IMMEDIATE')   # serializa cliques simultâneos
        if not conn.execute('SELECT 1 FROM pubshow_businesses WHERE id=?', (bid,)).fetchone():
            conn.rollback(); return {'codigo': 'bar'}
        # Clique duplo: outro repasse deste bar há menos de 60 s → ignora
        if conn.execute(
            "SELECT 1 FROM pubshow_repasses_manuais WHERE business_id=? "
            "AND datetime(criado_em) > datetime(?, '-60 seconds')", (bid, a)
        ).fetchone():
            conn.rollback(); return {'codigo': 'duplo'}
        w, wp = _repasse_where_pago()
        rows = conn.execute(
            f'''SELECT id, tipo, valor FROM pubshow_pedidos
                WHERE business_id=? AND {w} AND repasse_manual_id IS NULL
                  AND datetime(created_at) <= datetime(?) AND id <= ?''',
            [bid] + wp + [a, int(ate_id) if ate_id is not None else 2 ** 62]
        ).fetchall()
        if not rows:
            conn.rollback(); return {'codigo': 'zero'}
        vb = round(sum(float(r['valor'] or 0) for r in rows if r['tipo'] not in TIPOS_EQUIPE), 2)
        vg = round(sum(float(r['valor'] or 0) for r in rows if r['tipo'] in TIPOS_EQUIPE), 2)
        if total_cent is not None and int(round((vb + vg) * 100)) != int(total_cent):
            conn.rollback(); return {'codigo': 'mudou'}
        rid = conn.execute(
            '''INSERT INTO pubshow_repasses_manuais
               (business_id, valor_bar, valor_gorjeta, ate_em, criado_em, obs)
               VALUES (?,?,?,?,?,?)''',
            (bid, vb, vg, a, a, (obs or '').strip()[:300] or None)
        ).lastrowid
        ids = [r['id'] for r in rows]
        for i in range(0, len(ids), 500):
            parte = ids[i:i + 500]
            conn.execute(
                f"UPDATE pubshow_pedidos SET repasse_manual_id=? "
                f"WHERE repasse_manual_id IS NULL AND id IN ({','.join('?' * len(parte))})",
                [rid] + parte
            )
        conn.commit()
        log.info('[PUBSHOW] Repasse manual #%s bar=%s bar=%.2f gorjeta=%.2f (%d pedidos)', rid, bid, vb, vg, len(ids))
        return {'codigo': 'ok', 'id': rid, 'valor_bar': vb, 'valor_gorjeta': vg, 'n': len(ids)}
    except Exception as ex:
        try: conn.rollback()
        except Exception: pass
        log.error('[PUBSHOW] Repasse manual erro bar=%s: %s', bid, ex, exc_info=True)
        return {'codigo': 'erro'}
    finally:
        conn.close()


def _admin_csrf_token():
    t = session.get('pubshow_admin_csrf')
    if not t:
        t = secrets.token_urlsafe(24)
        session['pubshow_admin_csrf'] = t
    return t


def _admin_csrf_ok():
    import hmac as _hmac
    t = session.get('pubshow_admin_csrf') or ''
    f = request.form.get('csrf') or ''
    return bool(t) and _hmac.compare_digest(str(t), str(f))


_REPASSE_MSGS = {
    'ok':    ('ok',   '✅ Repasse registrado. O "devido" desse bar zerou.'),
    'duplo': ('aviso', '⏳ Esse bar acabou de ter um repasse registrado (menos de 1 min). Não gravei de novo.'),
    'zero':  ('aviso', 'Nada a repassar pra esse bar agora.'),
    'mudou': ('aviso', '⚠️ O valor desse bar mudou desde que a tela abriu (entrou PIX novo ou a conferência achou mais). '
                       'Nada foi gravado: confira o valor novo e repasse de novo.'),
    'bar':   ('erro', 'Bar não encontrado.'),
    'erro':  ('erro', 'Deu erro ao gravar o repasse. Nada foi gravado — tente de novo.'),
}


_REPASSE_CONF_LOCK = _threading.Lock()


def _repasse_conferir_fundo(limite=30):
    """Crítico operação M1 (17/10/26): confere no gateway numa thread (o gunicorn tem 1 worker: a tela de repasses
    consultando o Asaas 15-60x na própria requisição deixava o site inteiro parado). 1 por vez."""
    if not _REPASSE_CONF_LOCK.acquire(blocking=False):
        return False
    def _run():
        try:
            _repasse_conferir(limite=limite)
        finally:
            _REPASSE_CONF_LOCK.release()
    _threading.Thread(target=_run, daemon=True, name='pubshow-repasse-conferir').start()
    return True


@pubshow_bp.route('/admin/repasses')
@_admin_required
def admin_repasses():
    _repasse_conferir_fundo(limite=30)   # histórico/sem confirmação: pergunta ao gateway (em segundo plano)
    conn = get_pubshow_db()
    try:
        d = _repasses_calcular(conn)
    finally:
        conn.close()
    saldo = _asaas_saldo()
    falta = None if saldo is None else round(max(0.0, d['total_asaas'] - saldo), 2)
    msg = _REPASSE_MSGS.get(request.args.get('msg', ''))
    return render_template('pubshow/admin_repasses.html', d=d, saldo=saldo, falta=falta,
                           brl=_brl, csrf=_admin_csrf_token(), msg=msg)


@pubshow_bp.route('/admin/repasses/<int:bid>/repassei', methods=['POST'])
@_admin_required
def admin_repasse_registrar(bid):
    if not _admin_csrf_ok():
        return 'Sessão expirada — volte e recarregue a tela de repasses.', 403
    # crítico dinheiro C1: nada de conferir aqui (mudaria o valor depois do PIX feito); grava o que a tela mostrou
    try:
        ate_id, total_cent = int(request.form.get('ate_id', '')), int(request.form.get('total_cent', ''))
    except (TypeError, ValueError):
        return redirect('/pubshow/admin/repasses?msg=mudou')
    res = _repasse_registrar(bid, request.form.get('obs', ''), ate_id=ate_id, total_cent=total_cent)
    return redirect(f"/pubshow/admin/repasses?msg={res['codigo']}#bar-{bid}")


@pubshow_bp.route('/admin/bar/<int:bid>/acao', methods=['POST'])
@_admin_required
def admin_bar_acao(bid):
    acao = request.form.get('acao', '')
    conn = get_pubshow_db()
    if acao == 'ativar_plano':
        plano = request.form.get('plano', 'bar')
        plano = plano if plano in PLANOS else 'bar'   # plano único: nunca grava chave inválida (15/10/26)
        conn.execute('UPDATE pubshow_businesses SET plano=?, plano_ativo=1 WHERE id=?', (plano, bid))
    elif acao == 'desativar_plano':
        conn.execute('UPDATE pubshow_businesses SET plano_ativo=0, ativo_ate=NULL WHERE id=?', (bid,))
    elif acao == 'suspender':
        conn.execute('UPDATE pubshow_businesses SET suspenso=1 WHERE id=?', (bid,))
    elif acao == 'reativar':
        conn.execute('UPDATE pubshow_businesses SET suspenso=0 WHERE id=?', (bid,))
    elif acao == 'reativar_pausa' and _efi_ligado():
        # Lote D A3 (16/10/26): bar pausado por 60 dias sem momento pago → o relógio recomeça agora
        _cx = _pp_conn()
        try:
            pubshow_pay.reativar_pausa(_cx, bid, 'admin')
        finally:
            _cx.close()
        _bump_tv_bar(bid)   # a TV pausada volta na hora (também volta sozinha pelo /api/status)
    elif acao == 'novo_qr':
        novo = _gerar_jukebox_token()
        conn.execute('UPDATE pubshow_businesses SET jukebox_token=?, tv_version=? WHERE id=?',
                     (novo, str(int(_time.time() * 1000)), bid))   # a TV deste bar recarrega com o QR novo
    elif acao == 'toggle_jukebox':
        atual = conn.execute('SELECT jukebox_ativo FROM pubshow_businesses WHERE id=?', (bid,)).fetchone()[0]
        conn.execute('UPDATE pubshow_businesses SET jukebox_ativo=? WHERE id=?', (0 if atual else 1, bid))
    elif acao == 'trocar_canal':
        canal = request.form.get('canal', 'rock')
        if canal in CANAIS:
            conn.execute('UPDATE pubshow_businesses SET canal_atual=? WHERE id=?', (canal, bid))
    elif acao == 'nota':
        nota = request.form.get('nota', '').strip()[:500]
        conn.execute('UPDATE pubshow_businesses SET notas_admin=? WHERE id=?', (nota, bid))
    elif acao == 'limpar_fila':
        conn.execute(
            "UPDATE pubshow_pedidos SET status='dispensado' WHERE business_id=? AND status='pendente'",
            (bid,)
        )
        # QR abandonado há mais de 2 h vira 'recusado' (some do painel do gerente)
        # Lote C (16/10/26): com a Efí, esses pedidos têm a cobrança cancelada (PIX que cair depois volta sozinho)
        _recusados_efi = [r[0] for r in conn.execute(
            "SELECT id FROM pubshow_pedidos WHERE business_id=? AND status='aguardando_pix' AND created_at < datetime('now','-2 hours')",
            (bid,))] if _efi_ligado() else []
        conn.execute(
            "UPDATE pubshow_pedidos SET status='recusado' WHERE business_id=? AND status='aguardando_pix' AND created_at < datetime('now','-2 hours')",
            (bid,)
        )
    elif acao == 'confirmar_pix':
        # suporte: webhook não chegou mas o PIX caiu no Asaas → libera na mão
        _pid = request.form.get('pedido_id', '')
        _da_efi = False
        if _pid.isdigit() and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                           "AND name='pubshow_pay_cobrancas'").fetchone():
            # Lote C (16/10/26): pedido da Efí só confirma pela Efí (tela/robô). Liberar na mão poria o momento na TV
            # sem dinheiro no livro-caixa — e, se o PIX caísse depois, o robô devolveria (momento de graça).
            # CC3 R2 (16/10/26): a trava é a EXISTÊNCIA da cobrança Efí (só SQL), não o gateway: vale também no
            # rollback pra asaas (antes o "PIX caiu" liberava, e na volta pra efi o robô devolvia o momento que tocou).
            _da_efi = bool(conn.execute('SELECT 1 FROM pubshow_pay_cobrancas WHERE pedido_id=?', (int(_pid),)).fetchone())
        if _pid.isdigit() and not _da_efi:
            # "PIX caiu" do admin é confirmação de pagamento → grava pago_em (15/10/26)
            # crítico dinheiro M2 (17/10/26): com cobrança no gateway, marca 'ADMIN' — os Repasses conferem no
            # gateway antes de mandar repassar (o painel conta na hora, como antes)
            conn.execute("UPDATE pubshow_pedidos SET status=?, pago_em=datetime('now'), "
                         "pix_conferido=CASE WHEN TRIM(COALESCE(asaas_payment_id,''))<>'' "
                         "OR TRIM(COALESCE(efi_txid,''))<>'' THEN 'ADMIN' ELSE pix_conferido END "
                         "WHERE id=? AND business_id=? AND status='aguardando_pix'",
                         (_status_apos_pix(conn, int(_pid)), int(_pid), bid))
    conn.commit(); conn.close()
    if acao == 'limpar_fila' and _recusados_efi:
        _cx = get_pubshow_db()
        _bar = _cx.execute('SELECT * FROM pubshow_businesses WHERE id=?', (bid,)).fetchone()
        _cx.close()
        for _p in (_recusados_efi if _bar else []):
            _efi_estornar(_bar, _p, 'fila limpa pelo admin')
    return redirect(f'/pubshow/admin/bar/{bid}')


@pubshow_bp.route('/admin/bar/<int:bid>/upload-slide', methods=['POST'])
@_admin_required
def admin_bar_upload_slide(bid):
    """Admin envia um slide de imagem para a TV de um bar (venda de mídia)."""
    import json as _json
    conn = get_pubshow_db()
    b = conn.execute('SELECT * FROM pubshow_businesses WHERE id=?', (bid,)).fetchone()
    if not b:
        conn.close(); return redirect('/pubshow/admin')
    f = request.files.get('imagem')
    if not f or not f.filename:
        conn.close(); return redirect(f'/pubshow/admin/bar/{bid}')
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        conn.close(); return redirect(f'/pubshow/admin/bar/{bid}?erro=formato')
    code = b['code']
    pasta = os.path.join(_SLIDES_DIR, code)
    os.makedirs(pasta, exist_ok=True)
    fname = f'adm_{random.randint(10000,99999)}.{ext}'
    f.save(os.path.join(pasta, fname))
    url = f'/pubshow/slide/{code}/{fname}'
    try:
        anuncios = _json.loads(b['anuncios_json'] or '[]')
    except Exception:
        anuncios = []
    anuncios.append({'tipo': 'imagem', 'url': url, 'titulo': '', 'admin': True})
    conn.execute('UPDATE pubshow_businesses SET anuncios_json=? WHERE id=?',
                 (_json.dumps(anuncios, ensure_ascii=False), bid))
    conn.commit(); conn.close()
    log.info('[PUBSHOW-ADMIN] Slide enviado para bar %s: %s', bid, url)
    return redirect(f'/pubshow/admin/bar/{bid}')


# ── ADMIN — SLIDES DO SISTEMA (globais, aparecem em todas as TVs) ─────────────

@pubshow_bp.route('/admin/slides-sistema')
@_admin_required
def admin_slides_sistema():
    conn = get_pubshow_db()
    slides = conn.execute(
        'SELECT * FROM pubshow_slides_sistema ORDER BY ordem, id'
    ).fetchall()
    conn.close()
    return render_template('pubshow/admin_slides_sistema.html',
                           slides=[dict(s) for s in slides])


@pubshow_bp.route('/admin/slides-sistema/add', methods=['POST'])
@_admin_required
def admin_slides_sistema_add():
    import json as _json
    titulo    = request.form.get('titulo', '').strip()[:60]
    subtitulo = request.form.get('subtitulo', '').strip()[:80]
    emoji     = request.form.get('emoji', '📺').strip()[:4]
    cor       = request.form.get('cor', '#ef4444').strip()[:7]
    if titulo:
        conn = get_pubshow_db()
        conn.execute(
            'INSERT INTO pubshow_slides_sistema (tipo,titulo,subtitulo,emoji,cor) VALUES (?,?,?,?,?)',
            ('texto', titulo, subtitulo, emoji or '📺', cor)
        )
        conn.commit(); conn.close()
        _bump_videos_version()
    return redirect('/pubshow/admin/slides-sistema')


@pubshow_bp.route('/admin/slides-sistema/upload', methods=['POST'])
@_admin_required
def admin_slides_sistema_upload():
    f = request.files.get('imagem')
    if not f or not f.filename:
        return redirect('/pubshow/admin/slides-sistema')
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return redirect('/pubshow/admin/slides-sistema?erro=formato')
    pasta = os.path.join(_SLIDES_DIR, 'sistema')
    os.makedirs(pasta, exist_ok=True)
    fname = f'sis_{random.randint(100000, 999999)}.{ext}'
    f.save(os.path.join(pasta, fname))
    url = f'/pubshow/slide-sistema/{fname}'
    conn = get_pubshow_db()
    conn.execute(
        'INSERT INTO pubshow_slides_sistema (tipo,url) VALUES (?,?)',
        ('imagem', url)
    )
    conn.commit(); conn.close()
    _bump_videos_version()
    return redirect('/pubshow/admin/slides-sistema')


@pubshow_bp.route('/slide-sistema/<filename>')
def slide_sistema_serve(filename):
    """Serve imagens dos slides globais do sistema."""
    pasta = os.path.join(_SLIDES_DIR, 'sistema')
    return send_from_directory(pasta, filename)


@pubshow_bp.route('/admin/slides-sistema/toggle/<int:sid>', methods=['POST'])
@_admin_required
def admin_slides_sistema_toggle(sid):
    conn = get_pubshow_db()
    row = conn.execute('SELECT ativo FROM pubshow_slides_sistema WHERE id=?', (sid,)).fetchone()
    if row:
        conn.execute('UPDATE pubshow_slides_sistema SET ativo=? WHERE id=?', (0 if row[0] else 1, sid))
        conn.commit()
    conn.close()
    _bump_videos_version()   # avisa as TVs: recarregam e respeitam o ativo/OFF na hora
    return redirect('/pubshow/admin/slides-sistema')


@pubshow_bp.route('/admin/slides-sistema/delete/<int:sid>', methods=['POST'])
@_admin_required
def admin_slides_sistema_delete(sid):
    conn = get_pubshow_db()
    row = conn.execute('SELECT url FROM pubshow_slides_sistema WHERE id=?', (sid,)).fetchone()
    if row and row['url'] and '/slide-sistema/' in (row['url'] or ''):
        try:
            fname = row['url'].split('/')[-1]
            os.remove(os.path.join(_SLIDES_DIR, 'sistema', fname))
        except Exception:
            pass
    conn.execute('DELETE FROM pubshow_slides_sistema WHERE id=?', (sid,))
    conn.commit(); conn.close()
    _bump_videos_version()
    return redirect('/pubshow/admin/slides-sistema')


# ── ADMIN — GESTÃO DE VÍDEOS ───────────────────────────────────────────────────

@pubshow_bp.route('/admin/videos')
@_admin_required
def admin_videos():
    conn = get_pubshow_db()
    videos = conn.execute(
        'SELECT id, youtube_id, titulo, artista, categoria, ativo FROM pubshow_videos ORDER BY categoria, titulo'
    ).fetchall()
    conn.close()

    # Monta por_cat (categoria → lista de vídeos)
    por_cat = {}
    for v in videos:
        cat = v['categoria']
        por_cat.setdefault(cat, []).append(dict(v))

    # Ordem e rótulos dos nichos
    _NICHO_META = {
        'musica':         ('🎵', 'Música'),
        'sport':          ('🏆', 'Esporte'),
        'viral':          ('💥', 'Viral'),
        'entretenimento': ('🎭', 'Entretenimento'),
    }
    _NICHO_ORDEM = ['musica', 'sport', 'viral', 'entretenimento']   # 17/10/26: 'shows' saiu (desligados ficam em "Outros")

    # Estrutura nicho → [(cat_key, canal_info, vids)]
    por_nicho = []
    cats_mapeadas = set()
    for gkey in _NICHO_ORDEM:
        gemoji, glabel = _NICHO_META.get(gkey, ('📁', gkey))
        cats_nicho = []
        for cat_key, cinfo in CANAIS.items():
            if cinfo.get('grupo') == gkey and isinstance(cinfo.get('cat'), str):
                if cat_key in por_cat:
                    cats_nicho.append((cat_key, cinfo, por_cat[cat_key]))
                    cats_mapeadas.add(cat_key)
        if cats_nicho:
            total_nicho = sum(len(vids) for _, _, vids in cats_nicho)
            por_nicho.append((gkey, gemoji, glabel, cats_nicho, total_nicho))

    # Categorias sem nicho (multi-cat como sport_mix, ou categorias órfãs)
    sem_nicho = [(cat, {'nome': cat, 'emoji': '📁', 'cor': '#64748b'}, vids)
                 for cat, vids in por_cat.items() if cat not in cats_mapeadas]
    if sem_nicho:
        total_sem = sum(len(v) for _, _, v in sem_nicho)
        por_nicho.append(('outros', '📁', 'Outros / Multi-cat', sem_nicho, total_sem))

    # Canais agrupados para os <select> dos modais
    canais_grupos = []
    for gkey in _NICHO_ORDEM:
        gemoji, glabel = _NICHO_META.get(gkey, ('', gkey))
        itens = [(k, v) for k, v in CANAIS.items()
                 if v.get('grupo') == gkey and isinstance(v.get('cat'), str)]
        if itens:
            canais_grupos.append((f'{gemoji} {glabel}', itens))

    return render_template('pubshow/admin_videos.html',
                           por_nicho=por_nicho,
                           total=len(videos),
                           canais_grupos=canais_grupos)


@pubshow_bp.route('/admin/import-playlist', methods=['POST'])
@_admin_required
def admin_import_playlist():
    """Importa todos os vídeos de uma playlist do YouTube via Data API v3."""
    try:
        return _import_playlist_impl()
    except Exception as _e:
        import traceback as _tb
        log.error('[PUBSHOW] import-playlist crash: %s', _tb.format_exc())
        return jsonify({'ok': False, 'erro': f'{type(_e).__name__}: {str(_e)[:200]}'})


def _import_playlist_impl():
    import os, re
    api_key = os.environ.get('YOUTUBE_API_KEY', '')
    if not api_key:
        return jsonify({'ok': False, 'erro': 'Configure YOUTUBE_API_KEY nas variáveis do Railway'})

    data      = request.get_json() or {}
    url_input = data.get('playlist_url', '').strip()
    categoria = data.get('categoria', 'rock').strip()
    subcategoria = data.get('subcategoria', '').strip() or 'playlist'
    if categoria in SHOWS_FORA:   # 17/10/26: o grupo Shows saiu
        return jsonify({'ok': False, 'erro': 'Shows ao vivo saíram do PubShow (passam de 10 min). Escolha outra categoria.'})

    # Extrai playlist ID da URL ou aceita ID direto
    m = re.search(r'[?&]list=([A-Za-z0-9_-]+)', url_input)
    playlist_id = m.group(1) if m else url_input

    # Guarda anti-"lista toda": Mixes/Radios automáticos do YouTube têm list=RD…
    # São listas infinitas geradas pelo algoritmo. Colar um link de vídeo comum
    # (que vem com &list=RD…) importaria dezenas de vídeos aleatórios sem querer.
    if playlist_id.startswith('RD'):
        return jsonify({'ok': False, 'erro':
            'Esse link é um Mix automático do YouTube (lista RD…), não uma playlist real. '
            'Para um vídeo só, use "Importar em Lote". Para uma playlist de verdade, '
            'cole a URL dela (o ID começa com PL…).'})

    videos     = []
    page_token = None
    paginas    = 0

    while paginas < 100:  # máx 5000 vídeos (100 × 50)
        params = {
            'part':       'snippet',
            'playlistId': playlist_id,
            'maxResults': 50,
            'key':        api_key,
        }
        if page_token:
            params['pageToken'] = page_token

        try:
            r = _requests.get(
                'https://www.googleapis.com/youtube/v3/playlistItems',
                params=params, timeout=15
            )
        except Exception as e:
            return jsonify({'ok': False, 'erro': f'Erro de rede: {e}'})

        if r.status_code == 400:
            return jsonify({'ok': False, 'erro': 'Playlist ID inválido'})
        if r.status_code == 403:
            return jsonify({'ok': False, 'erro': 'API Key inválida ou cota excedida'})
        if r.status_code != 200:
            return jsonify({'ok': False, 'erro': f'YouTube API HTTP {r.status_code}'})

        d = r.json()
        for item in d.get('items', []):
            sn     = item.get('snippet', {})
            vid_id = sn.get('resourceId', {}).get('videoId', '')
            titulo = sn.get('title', '')
            artista = sn.get('videoOwnerChannelTitle', '') or sn.get('channelTitle', '')
            # Ignora vídeos privados/removidos
            if vid_id and titulo and titulo not in ('Private video', 'Deleted video'):
                videos.append((vid_id, titulo, artista, categoria, subcategoria))

        page_token = d.get('nextPageToken')
        paginas   += 1
        if not page_token:
            break

    if not videos:
        return jsonify({'ok': False, 'erro': 'Playlist vazia ou não encontrada'})

    # Insere no banco (INSERT OR IGNORE — não duplica)
    conn     = get_pubshow_db()
    inseridos = 0
    for vid_id, titulo, artista, cat, subcat in videos:
        conn.execute(
            '''INSERT OR IGNORE INTO pubshow_videos
               (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, qualidade, ativo)
               VALUES (?,?,?,?,?,180,'HD',1)''',
            (vid_id, titulo, artista, cat, subcat)
        )
        inseridos += conn.execute('SELECT changes()').fetchone()[0]
    conn.commit()
    conn.close()
    if inseridos:
        _bump_videos_version()

    return jsonify({
        'ok':       True,
        'total':    len(videos),
        'inseridos': inseridos,
        'duplicados': len(videos) - inseridos,
        'msg': f'✅ {inseridos} vídeos novos importados de {len(videos)} na playlist!'
    })


@pubshow_bp.route('/admin/videos/check-one', methods=['POST'])
@_admin_required
def admin_videos_check_one():
    """Verifica se um vídeo específico está disponível e embeddable via oEmbed."""
    yid = (request.json or {}).get('youtube_id', '')
    if not yid:
        return jsonify({'ok': False, 'erro': 'ID vazio'})
    try:
        r = _requests.get(
            'https://www.youtube.com/oembed',
            params={'url': f'https://www.youtube.com/watch?v={yid}', 'format': 'json'},
            timeout=8
        )
        if r.status_code == 200:
            data = r.json()
            return jsonify({'ok': True, 'titulo': data.get('title',''), 'autor': data.get('author_name','')})
        elif r.status_code == 401:
            return jsonify({'ok': False, 'erro': 'Embed bloqueado (401)'})
        elif r.status_code == 404:
            return jsonify({'ok': False, 'erro': 'Vídeo removido (404)'})
        else:
            return jsonify({'ok': False, 'erro': f'HTTP {r.status_code}'})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)[:80]})


@pubshow_bp.route('/admin/videos/check-bulk', methods=['POST'])
@_admin_required
def admin_videos_check_bulk():
    """Verifica uma lista de youtube_ids em paralelo via oEmbed.
    Retorna {ok: True, resultados: [{youtube_id, ok, erro}]}
    Muito mais rápido que check-one sequencial no cliente."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ids = (request.json or {}).get('ids', [])
    if not ids:
        return jsonify({'ok': False, 'erro': 'Lista vazia'})
    ids = ids[:600]   # limite de segurança

    def _check(yid):
        try:
            r = _requests.get(
                'https://www.youtube.com/oembed',
                params={'url': f'https://www.youtube.com/watch?v={yid}', 'format': 'json'},
                timeout=7
            )
            if r.status_code == 200:
                return {'youtube_id': yid, 'ok': True}
            elif r.status_code == 401:
                return {'youtube_id': yid, 'ok': False, 'erro': 'embed bloqueado'}
            elif r.status_code == 404:
                return {'youtube_id': yid, 'ok': False, 'erro': 'removido'}
            else:
                return {'youtube_id': yid, 'ok': False, 'erro': f'HTTP {r.status_code}'}
        except Exception as e:
            return {'youtube_id': yid, 'ok': False, 'erro': 'timeout'}

    resultados = []
    # 20 threads paralelas — rápido sem sobrecarregar o YouTube
    with ThreadPoolExecutor(max_workers=20) as ex:
        futuros = {ex.submit(_check, yid): yid for yid in ids}
        for fut in as_completed(futuros):
            try:
                resultados.append(fut.result())
            except Exception:
                resultados.append({'youtube_id': futuros[fut], 'ok': False, 'erro': 'erro'})

    quebrados = [r['youtube_id'] for r in resultados if not r['ok']]
    ok_count  = sum(1 for r in resultados if r['ok'])
    log.info('[PUBSHOW admin] check-bulk: %d ok, %d quebrados', ok_count, len(quebrados))
    return jsonify({'ok': True, 'resultados': resultados,
                    'total': len(resultados), 'quebrados': quebrados,
                    'ok_count': ok_count})


def _videos_version(chave='videos_version') -> str:
    """Versão GLOBAL guardada no pubshow_meta. 'videos_version' = acervo (o admin apaga/adiciona): a
    TV troca a LISTA em memória, sem recarregar. 'tv_reload_version' = só o botão "Atualizar TVs"
    do admin: todas as TVs recarregam a página (15/10/26)."""
    try:
        conn = get_pubshow_db()
        row = conn.execute("SELECT valor FROM pubshow_meta WHERE chave=?", (chave,)).fetchone()
        conn.close()
        return (row['valor'] if row else '0') or '0'
    except Exception:
        return '0'


def _bump_videos_version(chave='videos_version'):
    """Marca que a biblioteca mudou — as TVs buscam a lista nova no próximo poll."""
    try:
        conn = get_pubshow_db()
        conn.execute("CREATE TABLE IF NOT EXISTS pubshow_meta (chave TEXT PRIMARY KEY, valor TEXT)")
        conn.execute("INSERT OR REPLACE INTO pubshow_meta (chave, valor) VALUES (?, ?)",
                     (chave, str(int(_time.time() * 1000))))
        conn.commit(); conn.close()
    except Exception as ex:
        log.error('[PUBSHOW] bump %s: %s', chave, ex)


def _bump_tv_bar(bid):
    """Versão da TV de UM bar (15/10/26): anúncio, slide, Novo QR. Só a TV DESTE bar recarrega —
    antes a versão era global e um bar salvando propaganda recarregava a TV de todos os bares."""
    try:
        conn = get_pubshow_db()
        conn.execute('UPDATE pubshow_businesses SET tv_version=? WHERE id=?',
                     (str(int(_time.time() * 1000)), bid))
        conn.commit(); conn.close()
    except Exception as ex:
        log.error('[PUBSHOW] bump tv_version bar %s: %s', bid, ex)


def _tv_version(b) -> str:
    """O que a TV compara pra decidir recarregar a página: botão global do admin + versão do bar."""
    try:
        v_bar = (b['tv_version'] if 'tv_version' in b.keys() else None) or '0'
    except Exception:
        v_bar = '0'
    return f"{_videos_version('tv_reload_version')}:{v_bar}"


@pubshow_bp.route('/admin/videos/atualizar-tvs', methods=['POST'])
@_admin_required
def admin_videos_atualizar_tvs():
    """Botão 'Atualizar TVs': todas as TVs pegam a lista nova E recarregam a página (como antes)."""
    _bump_videos_version()
    _bump_videos_version('tv_reload_version')
    return jsonify({'ok': True})


def _limpar_videos_quebrados(max_videos=None):
    """Checa todos os vídeos ativos via oEmbed (em paralelo) e desativa
    (ativo=0) os comprovadamente quebrados — 404 (removido) ou 401 (embed
    bloqueado). Timeouts são ignorados (dúvida não remove).

    É o coração do faxineiro: usado tanto pelo botão 1-click do admin quanto
    pelo agendador automático. Retorna (verificados, removidos, ids_removidos).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    conn = get_pubshow_db()
    todos = conn.execute(
        'SELECT youtube_id FROM pubshow_videos WHERE ativo=1'
    ).fetchall()
    conn.close()

    ids = [r['youtube_id'] for r in todos]
    if max_videos:
        ids = ids[:max_videos]
    if not ids:
        return 0, 0, []

    def _check(yid):
        try:
            r = _requests.get(
                'https://www.youtube.com/oembed',
                params={'url': f'https://www.youtube.com/watch?v={yid}', 'format': 'json'},
                timeout=7
            )
            # 15/10/26: só 400/401/403/404 = quebrado. 429 ("muitas requisições"), 5xx e outros = DÚVIDA
            # (antes qualquer resposta != 200 desligava vídeo bom e podia esvaziar a biblioteca)
            if r.status_code == 200:
                return yid, True
            if r.status_code in (400, 401, 403, 404):
                return yid, False
            return yid, None
        except Exception:
            return yid, None   # None = timeout, não remove (dúvida)

    quebrados = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futuros = {ex.submit(_check, yid): yid for yid in ids}
        for fut in as_completed(futuros):
            yid, ok = fut.result()
            if ok is False:   # explicitamente quebrado (não timeout)
                quebrados.append(yid)

    # Trava (15/10/26): se o lote for desligar mais de 10% do que checou, algo está errado do lado do
    # YouTube/rede — não desliga nada e avisa no log.
    if quebrados and len(quebrados) > max(1, int(len(ids) * 0.10)):
        log.error('[PUBSHOW] faxineiro ABORTADO: %d de %d vídeos dariam como quebrados (> 10%%) — nada desligado',
                  len(quebrados), len(ids))
        return len(ids), 0, []

    removidos = 0
    if quebrados:
        conn2 = get_pubshow_db()
        ph = ','.join('?' * len(quebrados))
        conn2.execute(f'UPDATE pubshow_videos SET ativo=0 WHERE youtube_id IN ({ph})', quebrados)
        removidos = conn2.execute('SELECT changes()').fetchone()[0]
        conn2.commit()
        conn2.close()

    log.info('[PUBSHOW] faxineiro: %d verificados, %d desativados', len(ids), removidos)
    # 15/10/26: o faxineiro NÃO avisa as TVs (a TV já pula vídeo quebrado sozinha). Antes cada rodada
    # (5 min depois de todo deploy) recarregava todas as TVs — e elas voltavam mudas.
    return len(ids), removidos, quebrados


# ── Faxineiro automático de vídeos ──────────────────────────────────────────
_FAXINEIRO_INICIADO = False

def iniciar_faxineiro_videos(intervalo_horas=12, delay_inicial_seg=300):
    """Inicia uma thread daemon que limpa vídeos quebrados periodicamente,
    sem o dono precisar abrir o admin. Idempotente — só inicia uma vez por
    processo. Desligável via env PUBSHOW_FAXINEIRO=0.
    """
    global _FAXINEIRO_INICIADO
    # Robô da Efí sobe junto (mesmo ponto de partida no app.py), independente do faxineiro
    try:
        iniciar_robo_efi()
    except Exception as _re:
        log.warning('[PUBSHOW] Robô Efí não iniciou: %s', _re)
    if _FAXINEIRO_INICIADO:
        return
    if os.environ.get('PUBSHOW_FAXINEIRO', '1') == '0':
        log.info('[PUBSHOW] Faxineiro de vídeos desativado (PUBSHOW_FAXINEIRO=0)')
        return
    _FAXINEIRO_INICIADO = True

    import time as _time
    def _loop():
        # Jitter no boot: evita que múltiplos workers batam no YouTube ao mesmo tempo
        _time.sleep(delay_inicial_seg + random.randint(0, 120))
        while True:
            try:
                _limpar_videos_quebrados()
            except Exception as ex:
                log.error('[PUBSHOW] faxineiro erro: %s', ex)
            _time.sleep(intervalo_horas * 3600)

    _threading.Thread(target=_loop, daemon=True, name='pubshow-faxineiro').start()
    log.info('[PUBSHOW] Faxineiro de vídeos iniciado (a cada %dh)', intervalo_horas)


@pubshow_bp.route('/admin/videos/remover-quebrados', methods=['POST'])
@_admin_required
def admin_videos_remover_quebrados():
    """Verifica TODOS os vídeos ativos em paralelo e remove os quebrados.
    Endpoint de 1-click para limpeza manual da biblioteca."""
    verificados, removidos, quebrados = _limpar_videos_quebrados()
    if not verificados:
        return jsonify({'ok': True, 'removidos': 0, 'msg': 'Nenhum vídeo na biblioteca'})
    return jsonify({'ok': True, 'verificados': verificados,
                    'quebrados': len(quebrados), 'removidos': removidos,
                    'ids_removidos': quebrados})


@pubshow_bp.route('/admin/videos/deletar', methods=['POST'])
@_admin_required
def admin_videos_deletar():
    """Deleta vídeos pelo youtube_id (lista de IDs ruins)."""
    ids = (request.json or {}).get('ids', [])
    if not ids:
        return jsonify({'ok': False})
    conn = get_pubshow_db()
    placeholders = ','.join('?' * len(ids))
    deleted = conn.execute(f'DELETE FROM pubshow_videos WHERE youtube_id IN ({placeholders})', ids).rowcount
    conn.commit(); conn.close()
    log.info(f'[PUBSHOW ADMIN] Deletados {deleted} vídeos quebrados')
    if deleted:
        _bump_videos_version()   # avisa as TVs pra recarregarem a lista
    return jsonify({'ok': True, 'deleted': deleted})


@pubshow_bp.route('/admin/videos/add', methods=['POST'])
@_admin_required
def admin_videos_add():
    """Adiciona um vídeo manualmente à biblioteca."""
    data = request.json or {}
    yid      = (data.get('youtube_id') or '').strip()
    titulo   = (data.get('titulo') or '').strip()
    artista  = (data.get('artista') or '').strip()
    categoria = (data.get('categoria') or '').strip()
    if not yid or not titulo or not categoria:
        return jsonify({'ok': False, 'erro': 'Campos obrigatórios: youtube_id, titulo, categoria'})
    if categoria in SHOWS_FORA:   # 17/10/26: o grupo Shows saiu
        return jsonify({'ok': False, 'erro': 'Shows ao vivo saíram do PubShow (passam de 10 min). Escolha outra categoria.'})
    try:
        conn = get_pubshow_db()
        conn.execute(
            'INSERT OR IGNORE INTO pubshow_videos (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, views_milhoes, ativo) VALUES (?,?,?,?,?,?,?,1)',
            (yid, titulo, artista, categoria, '', 0, 0)
        )
        conn.commit(); conn.close()
        _bump_videos_version()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)[:100]})


@pubshow_bp.route('/admin/videos/add-batch', methods=['POST'])
@_admin_required
def admin_videos_add_batch():
    """Insere vários vídeos de uma vez. Recebe lista de {youtube_id, titulo, artista, categoria}.
    Respeita um teto de vídeos por categoria (padrão 50, via PUBSHOW_MAX_POR_CATEGORIA)."""
    items = request.json or []
    if not isinstance(items, list):
        return jsonify({'ok': False, 'erro': 'Esperado uma lista JSON'})
    MAX_POR_CAT = int(os.environ.get('PUBSHOW_MAX_POR_CATEGORIA', '50'))
    conn = get_pubshow_db()
    inseridos = 0
    duplicados = 0
    erros = 0
    limitados = 0
    _cont_cat = {}  # cache da contagem atual por categoria
    for item in items:
        yid  = (item.get('youtube_id') or '').strip()
        tit  = (item.get('titulo') or '').strip()
        art  = (item.get('artista') or '').strip()
        cat  = (item.get('categoria') or '').strip()
        if not yid or not tit or not cat or cat in SHOWS_FORA:   # 17/10/26: o grupo Shows saiu
            erros += 1
            continue
        # Teto por categoria: não deixa passar de MAX_POR_CAT
        if cat not in _cont_cat:
            _cont_cat[cat] = conn.execute(
                'SELECT COUNT(*) FROM pubshow_videos WHERE categoria=?', (cat,)
            ).fetchone()[0]
        if _cont_cat[cat] >= MAX_POR_CAT:
            limitados += 1
            continue
        try:
            conn.execute(
                'INSERT OR IGNORE INTO pubshow_videos '
                '(youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, views_milhoes, ativo) '
                'VALUES (?,?,?,?,?,0,0,1)',
                (yid, tit, art, cat, '')
            )
            changed = conn.execute('SELECT changes()').fetchone()[0]
            if changed:
                inseridos += 1
                _cont_cat[cat] += 1
            else:
                duplicados += 1
        except Exception:
            erros += 1
    conn.commit()
    conn.close()
    if inseridos:
        _bump_videos_version()
    resp = {'ok': True, 'inseridos': inseridos, 'duplicados': duplicados, 'erros': erros}
    if limitados:
        resp['limitados'] = limitados
        resp['msg_limite'] = f'{limitados} barrados — categoria no limite de {MAX_POR_CAT}.'
    return jsonify(resp)


@pubshow_bp.route('/admin/videos/oembed', methods=['POST'])
@_admin_required
def admin_videos_oembed():
    """Busca título e autor via oEmbed (sem API key). Recebe {youtube_id}."""
    yid = (request.json or {}).get('youtube_id', '').strip()
    if not yid:
        return jsonify({'ok': False})
    try:
        r = _requests.get(
            'https://www.youtube.com/oembed',
            params={'url': f'https://www.youtube.com/watch?v={yid}', 'format': 'json'},
            timeout=8
        )
        if r.status_code == 200:
            d = r.json()
            return jsonify({'ok': True, 'titulo': d.get('title', ''), 'artista': d.get('author_name', '')})
        return jsonify({'ok': False, 'erro': f'HTTP {r.status_code}'})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)[:60]})


@pubshow_bp.route('/admin/videos/seed-categorias', methods=['POST'])
@_admin_required
def admin_videos_seed_categorias():
    """Busca e importa vídeos para categorias vazias via YouTube Search API.
    Usa queries curadas por categoria — importa até 20 vídeos por categoria.
    Só importa categorias que têm menos de MIN_VIDEOS vídeos ativos."""

    api_key = os.environ.get('YOUTUBE_API_KEY', '')
    if not api_key:
        return jsonify({'ok': False, 'erro': 'YOUTUBE_API_KEY não configurada'})

    MIN_VIDEOS = 5   # só seed se tiver menos que isso

    # Queries curadas por categoria — ajustadas para conteúdo viral de qualidade
    SEED_QUERIES = {
        'rally': [
            'Ken Block Gymkhana official',
            'WRC Rally onboard highlights',
            'rally car drift extreme',
            'rally crash compilation',
        ],
        'wingsuit': [
            'wingsuit proximity flying POV',
            'BASE jump wingsuit extreme',
            'wingsuit flying compilation 4K',
            'wingsuit aerobatics highlights',
        ],
        'aviacao': [
            'fighter jet aerobatics airshow',
            'Blue Angels airshow 4K',
            'F-22 Raptor extreme maneuvers',
            'airshow best moments compilation',
        ],
        'lutas': [
            'UFC best knockouts compilation',
            'UFC highlights official',
            'MMA incredible moments',
            'UFC greatest fights highlights',
        ],
        'skate': [
            'skateboarding best tricks compilation',
            'street skateboarding highlights',
            'Tony Hawk skateboarding',
            'skate best moments viral',
        ],
        'kitesurf': [
            'kitesurfing tricks compilation 4K',
            'kiteboarding best moments',
            'wakeboarding extreme tricks',
            'kitesurf highlights viral',
        ],
        'batidas': [
            'crash compilation funny',
            'epic fail compilation viral',
            'amazing catches fails compilation',
            'sports crash compilation',
        ],
        'standup': [
            'stand up comedy viral moments',
            'comédia stand up brasileiro',
            'Whindersson Nunes stand up',
            'stand up comedy best moments',
        ],
    }

    conn = get_pubshow_db()
    resultado = {}
    total_inseridos = 0

    for categoria, queries in SEED_QUERIES.items():
        # Checa quantos vídeos já existem
        atual = conn.execute(
            'SELECT COUNT(*) FROM pubshow_videos WHERE categoria=? AND ativo=1', (categoria,)
        ).fetchone()[0]

        if atual >= MIN_VIDEOS:
            resultado[categoria] = {'skip': True, 'existentes': atual}
            continue

        inseridos_cat = 0
        vistos = set()

        for query in queries:
            if inseridos_cat >= 20:
                break
            try:
                r = _requests.get(
                    'https://www.googleapis.com/youtube/v3/search',
                    params={
                        'part': 'snippet',
                        'q': query,
                        'type': 'video',
                        'videoDuration': 'medium',   # 4-20 min
                        'videoEmbeddable': 'true',
                        'maxResults': 10,
                        'key': api_key,
                        'relevanceLanguage': 'pt',
                        'safeSearch': 'moderate',
                    },
                    timeout=12
                )
                if r.status_code == 403:
                    resultado[categoria] = {'erro': 'API quota excedida'}
                    break
                if r.status_code != 200:
                    continue

                for item in r.json().get('items', []):
                    if inseridos_cat >= 20:
                        break
                    vid_id = item.get('id', {}).get('videoId', '')
                    sn = item.get('snippet', {})
                    titulo = sn.get('title', '').strip()
                    artista = sn.get('channelTitle', '').strip()

                    if not vid_id or not titulo or vid_id in vistos:
                        continue
                    if any(w in titulo.lower() for w in ['private', 'deleted', '#shorts']):
                        continue
                    vistos.add(vid_id)

                    try:
                        conn.execute(
                            '''INSERT OR IGNORE INTO pubshow_videos
                               (youtube_id, titulo, artista, categoria, subcategoria, duracao_seg, qualidade, ativo)
                               VALUES (?,?,?,?,?,180,"HD",1)''',
                            (vid_id, titulo, artista, categoria, query[:30])
                        )
                        if conn.execute('SELECT changes()').fetchone()[0]:
                            inseridos_cat += 1
                            total_inseridos += 1
                    except Exception:
                        pass

            except Exception as e:
                log.error('[PUBSHOW seed] Erro query "%s": %s', query, e)

        conn.commit()
        resultado[categoria] = {'inseridos': inseridos_cat, 'existentes': atual}

    conn.close()
    log.info('[PUBSHOW seed] Total inseridos: %d', total_inseridos)
    return jsonify({'ok': True, 'total': total_inseridos, 'categorias': resultado})


# ── CHECKOUT / ASSINATURA ─────────────────────────────────────────────────────

@pubshow_bp.route('/assinar/<plano>', methods=['GET', 'POST'])
@pubshow_login_required
def assinar(plano):
    if plano not in PLANOS:
        # plano único (15/10/26): link velho /assinar/pro|starter|rede cai no plano único
        return redirect('/pubshow/assinar/bar' if plano in PLANOS_LEGADOS else '/pubshow/planos')
    b = _get_business()
    # VCa-02 (16/10/26): bar na Efí paga a mensalidade pelo split (sai dos momentos). Assinar no Asaas seria
    # cobrar 2x → nunca chama o Asaas; vai pro "Meu dinheiro", que mostra a mensalidade do mês
    if b and _efi_para(b):
        return redirect('/pubshow/painel/dinheiro')
    # V6-05 (16/10/26): já paga → não cria a 2ª assinatura no Asaas (a antiga continuaria cobrando)
    if b and _tem_assinatura_ativa(b['id']):
        return redirect('/pubshow/painel')
    # 16/10/26 (C3, D1-6): assinatura pendente/atrasada → manda pra fatura que JÁ existe no Asaas
    # (o "Regularizar → Assinar" criava a 2ª assinatura e a 1ª seguia cobrando)
    if b:
        _fatura = _fatura_aberta_da_assinatura(b['id'])
        if _fatura:
            return redirect(_fatura)
    erro = ''
    _p_bar = _plano_do_bar(b)   # Lote D A1 (16/10/26): o preço guardado deste bar (fundador: o travado)
    if request.method == 'POST':
        billing_type = request.form.get('billing_type', 'PIX')
        try:
            customer_id = _asaas_criar_ou_buscar_cliente(b)
            if not customer_id:
                erro = 'Erro ao criar perfil de pagamento.'
            else:
                sub = _asaas_criar_assinatura(customer_id, plano, billing_type, b['id'],
                                              primeiro_venc=_primeiro_vencimento(b), p=_p_bar)
                if sub.get('id'):
                    conn = get_pubshow_db()
                    conn.execute(
                        '''INSERT OR REPLACE INTO pubshow_assinaturas
                           (business_id, plano, valor, status, asaas_subscription_id, billing_type)
                           VALUES (?,?,?,?,?,?)''',
                        (b['id'], plano, _p_bar['preco'], 'pendente', sub['id'], billing_type)
                    )
                    conn.commit(); conn.close()
                    # 18/10/26 (pente fino DIN-1): a assinatura não traz invoiceUrl (a fatura é um pagamento à
                    # parte) e o cliente tem as notificações desligadas → sem isto o dono caía no "Aguardando"
                    # sem link e esperando um e-mail que não sai. Busca a 1ª fatura da assinatura recém-criada.
                    pay_url = (sub.get('invoiceUrl') or sub.get('bankSlipUrl')
                               or _fatura_aberta_da_assinatura(b['id']) or '')
                    if pay_url:
                        return redirect(pay_url)
                    return redirect('/pubshow/aguardando')
                else:
                    erro = sub.get('errors', [{}])[0].get('description', 'Erro ao criar assinatura.')
        except Exception as ex:
            log.error('[PUBSHOW] Erro assinar: %s', ex, exc_info=True)
            erro = 'Erro ao processar. Tente novamente.'
    # crítico leigo A1 (17/10/26): o 2º bar do mesmo CPF/CNPJ (ou teste vencido) não lê "período gratuito de 7 dias"
    teste_vigente = ((b['trial_ends'] or '')[:19] > datetime.now().isoformat()[:19]) if b else False
    return render_template('pubshow/checkout.html', b=dict(b), plano=plano, p=_p_bar, PLANO=_p_bar, erro=erro,
                           teste_vigente=teste_vigente)


def _fatura_aberta_da_assinatura(business_id):
    """URL da fatura em aberto (PENDING/OVERDUE) da assinatura pendente/inadimplente/suspensa do bar no
    Asaas, ou '' (16/10/26). Só leitura; sem Asaas, sem assinatura ou em erro → '' (segue o fluxo normal)."""
    if not os.environ.get('ASAAS_API_KEY'):
        return ''
    try:
        conn = get_pubshow_db()
        ass = conn.execute(
            "SELECT asaas_subscription_id FROM pubshow_assinaturas WHERE business_id=? "
            "AND status IN ('pendente','inadimplente','suspenso') AND COALESCE(asaas_subscription_id,'')<>''",
            (business_id,)).fetchone()
        conn.close()
        if not ass:
            return ''
        r = _asaas_req('GET', f'/subscriptions/{ass[0]}/payments') or {}
        for pg in (r.get('data') or []):
            if (pg.get('status') in ('PENDING', 'OVERDUE')) and pg.get('invoiceUrl'):
                return pg['invoiceUrl']
    except Exception as ex:
        log.warning('[PUBSHOW] fatura aberta da assinatura (bar %s): %s', business_id, ex)
    return ''


@pubshow_bp.route('/aguardando')
@pubshow_login_required
def aguardando():
    b = _get_business()
    # DIN-1 (18/10/26): botão "Abrir a fatura" (o Asaas não manda e-mail: notificações desligadas)
    fatura = _fatura_aberta_da_assinatura(b['id'])
    if not fatura.startswith('https://'):
        fatura = ''
    return render_template('pubshow/aguardando.html', b=dict(b), fatura=fatura)


def _cancelar_fica_ate(ass):
    """Cancelar = não renovar (15/10/26): com assinatura ativa e vencimento no futuro, o PubShow fica
    ativo até o proximo_vencimento ('AAAA-MM-DD'). Sem mês pago (trial/atrasado) → None (desliga na hora)."""
    _pv = ((ass or {}).get('proximo_vencimento') or '')[:10]
    if ass and ass.get('status') == 'ativo' and _pv >= datetime.now().strftime('%Y-%m-%d'):
        return _pv
    return None


def _data_br(iso):
    """'AAAA-MM-DD...' → 'DD/MM/AAAA' ('' se vazio)."""
    iso = (iso or '')[:10]
    return '/'.join(reversed(iso.split('-'))) if len(iso) == 10 else ''


@pubshow_bp.route('/painel/cancelar', methods=['GET', 'POST'])
@pubshow_login_required
def painel_cancelar():
    """Cancelamento de assinatura pelo próprio bar."""
    b = _get_business()
    bd = dict(b)
    conn = get_pubshow_db()
    ass = conn.execute(
        'SELECT * FROM pubshow_assinaturas WHERE business_id=?', (b['id'],)
    ).fetchone()
    conn.close()
    ass = dict(ass) if ass else None   # sqlite3.Row não tem .get() — dava 500 ao cancelar (15/10/26)

    if request.method == 'POST':
        confirmacao = request.form.get('confirmar', '').strip().upper()
        if confirmacao != 'CANCELAR':
            return render_template('pubshow/cancelar.html', planos=PLANOS, b=bd, ass=ass,
                                   PLANO=_plano_do_bar(bd),   # crítico operação A1: o preço DESTE bar (fundador)
                                   fica_ate=_data_br(_cancelar_fica_ate(ass)),
                                   erro='Digite CANCELAR para confirmar.')
        # Tenta cancelar no Asaas
        if ass and ass.get('asaas_subscription_id'):
            try:
                _asaas_req('DELETE', f'/subscriptions/{ass["asaas_subscription_id"]}', {})
            except Exception as ex:
                log.warning('[PUBSHOW] Erro ao cancelar Asaas: %s', ex)
        # Cancelar = não renovar (15/10/26): com o mês pago, o jukebox fica ativo até o
        # proximo_vencimento (ativo_ate). Sem mês pago (trial/atrasado), desliga como antes.
        ativo_ate = _cancelar_fica_ate(ass)
        # Marca como cancelado no banco
        conn2 = get_pubshow_db()
        if ass:
            conn2.execute(
                "UPDATE pubshow_assinaturas SET status='cancelado' WHERE business_id=?", (b['id'],)
            )
        conn2.execute(
            'UPDATE pubshow_businesses SET plano_ativo=0, ativo_ate=? WHERE id=?', (ativo_ate, b['id'])
        )
        conn2.commit(); conn2.close()
        log.info('[PUBSHOW] Assinatura cancelada por business_id=%s', b['id'])
        # Notifica admin via WhatsApp
        _pubshow_send_wa(
            os.environ.get('PUBSHOW_ADMIN_PHONE', ''),
            f'⚠️ *PUBSHOW — Cancelamento*\n'
            f'Bar: {bd["nome"]}\n'
            f'E-mail: {bd["email"]}\n'
            f'Plano: {bd.get("plano","—")}\n'
            f'Data: {datetime.now().strftime("%d/%m/%Y %H:%M")}'
        )
        return redirect('/pubshow/painel?ok=cancelamento')

    return render_template('pubshow/cancelar.html', planos=PLANOS, b=bd, ass=ass,
                           PLANO=_plano_do_bar(bd),   # crítico operação A1: o preço DESTE bar (fundador)
                           fica_ate=_data_br(_cancelar_fica_ate(ass)), erro='')


@pubshow_bp.route('/planos')
def planos():
    # Plano único (S6, 16/10/26): a página de planos virou o bloco de preço da landing. Link velho
    # (Google, e-mail, painel) cai lá; logado, o botão do bloco já é "Assinar".
    return redirect('/pubshow/#preco', code=301)


# ── Cobrança Automática — Helpers ─────────────────────────────────────────────

_GRACE_PERIOD_DAYS = 3   # dias de carência após vencimento antes de desativar

def _assinatura_ativar(business_id: int, plano: str, sub_id: str = None,
                       proximo_venc: str = None):
    """Ativa plano de um bar no DB."""
    plano = plano if plano in PLANOS else 'bar'   # plano único: chave antiga vira 'bar' (15/10/26)
    conn = get_pubshow_db()
    conn.execute(
        'UPDATE pubshow_businesses SET plano=?, plano_ativo=1, ativo_ate=NULL WHERE id=?',
        (plano, business_id)
    )
    conn.execute(
        '''UPDATE pubshow_assinaturas
           SET status="ativo", plano=?, inadimplente_desde=NULL,
               asaas_sub_status="ACTIVE", ultima_sync=?, proximo_vencimento=?
           WHERE business_id=?''',
        (plano, datetime.now().isoformat(), proximo_venc, business_id)
    )
    conn.commit(); conn.close()
    log.info('[PUBSHOW] Assinatura ATIVADA: business_id=%s plano=%s', business_id, plano)


def _assinatura_inadimplente(business_id: int, ass_id: int):
    """Marca assinatura como inadimplente. Desativa após carência."""
    conn = get_pubshow_db()
    ass = conn.execute(
        'SELECT inadimplente_desde FROM pubshow_assinaturas WHERE id=?', (ass_id,)
    ).fetchone()
    agora = datetime.now()

    if not ass or not ass['inadimplente_desde']:
        # Primeira vez — registra início da inadimplência, mantém ativo
        conn.execute(
            "UPDATE pubshow_assinaturas SET status='inadimplente', inadimplente_desde=? WHERE id=?",
            (agora.isoformat(), ass_id)
        )
        conn.commit(); conn.close()
        log.info('[PUBSHOW] Assinatura INADIMPLENTE (inicio carencia): business_id=%s', business_id)
    else:
        # Verifica se passou a carência
        try:
            desde = datetime.fromisoformat(ass['inadimplente_desde'][:19])
            if (agora - desde).days >= _GRACE_PERIOD_DAYS:
                conn.execute('UPDATE pubshow_businesses SET plano_ativo=0 WHERE id=?', (business_id,))
                conn.execute(
                    "UPDATE pubshow_assinaturas SET status='suspenso' WHERE id=?", (ass_id,)
                )
                conn.commit()
                log.info('[PUBSHOW] Assinatura SUSPENSA (apos carencia): business_id=%s', business_id)
            else:
                conn.commit()
        except Exception as e:
            log.error('[PUBSHOW] Erro carencia: %s', e)
        conn.close()


def _assinatura_cancelar(business_id: int):
    """Cancela e desativa assinatura."""
    conn = get_pubshow_db()
    conn.execute('UPDATE pubshow_businesses SET plano_ativo=0 WHERE id=?', (business_id,))
    conn.execute(
        "UPDATE pubshow_assinaturas SET status='cancelado', asaas_sub_status='CANCELLED' WHERE business_id=?",
        (business_id,)
    )
    conn.commit(); conn.close()
    log.info('[PUBSHOW] Assinatura CANCELADA: business_id=%s', business_id)


def _notify_assinatura(b_id: int, tipo: str):
    """Envia notificações (email + WA) sobre eventos de assinatura."""
    try:
        conn = get_pubshow_db()
        b = conn.execute('SELECT * FROM pubshow_businesses WHERE id=?', (b_id,)).fetchone()
        ass = conn.execute('SELECT * FROM pubshow_assinaturas WHERE business_id=?', (b_id,)).fetchone()
        conn.close()
        if not b:
            return
        bd = dict(b)
        _nome_html = _html_escape(((bd.get('nome') or '').split() or [''])[0])   # 15/10/26: escapa no HTML
        base_url = os.environ.get('BASE_URL', 'https://www.4kitem.com.br')
        planos_url = f'{base_url}/pubshow/planos'
        # plano único (15/10/26); VDA-04 (16/10/26): com o preço guardado DESTE bar, o mesmo que o /assinar cobra
        nome_plano, preco_plano = PLANO['nome'], _plano_do_bar(bd)['preco_fmt']
        proximo = (ass['proximo_vencimento'] or '')[:10] if ass else ''
        try:
            proximo_fmt = datetime.fromisoformat(proximo).strftime('%d/%m/%Y') if proximo else ''
        except Exception:
            proximo_fmt = proximo

        if tipo == 'pagamento_confirmado':
            assunto = f'✅ Pagamento confirmado — PUBSHOW {nome_plano}'
            corpo = f"""
              <h1 style="color:#f9fafb;font-size:22px;font-weight:800;margin:0 0 8px;">
                Pagamento confirmado! ✅
              </h1>
              <p style="color:#9ca3af;font-size:15px;margin:0 0 24px;line-height:1.6;">
                Oi {_nome_html}, seu pagamento do plano <strong style="color:#4ade80">{nome_plano}</strong>
                foi confirmado. Jukebox ativo e funcionando!
              </p>
              <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1f0d;border-radius:12px;border:1px solid #166534;margin-bottom:24px;">
                <tr><td style="padding:20px;">
                  <p style="color:#4ade80;font-size:14px;font-weight:700;margin:0 0 8px;">Detalhes da assinatura</p>
                  <p style="color:#e5e7eb;font-size:13px;margin:4px 0;">📦 Plano: <strong>{nome_plano}</strong> — {preco_plano}/mês</p>
                  {'<p style="color:#e5e7eb;font-size:13px;margin:4px 0;">📅 Próximo vencimento: <strong>' + proximo_fmt + '</strong></p>' if proximo_fmt else ''}
                </td></tr>
              </table>
              <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
                <a href="{base_url}/pubshow/painel" style="display:inline-block;background:#4ade80;color:#08080f;font-weight:800;font-size:15px;padding:12px 32px;border-radius:8px;text-decoration:none;">
                  Ir para o Painel →
                </a>
              </td></tr></table>
            """
            wa_msg = (f'✅ *PUBSHOW — Pagamento confirmado*\n'
                      f'Bar: {bd["nome"]}\nPlano: {nome_plano}\n'
                      + (f'Próximo: {proximo_fmt}' if proximo_fmt else ''))

        elif tipo == 'inadimplente':
            assunto = f'⚠️ Pagamento pendente — PUBSHOW {nome_plano}'
            corpo = f"""
              <h1 style="color:#f9fafb;font-size:22px;font-weight:800;margin:0 0 8px;">
                Pagamento pendente ⚠️
              </h1>
              <p style="color:#9ca3af;font-size:15px;margin:0 0 24px;line-height:1.6;">
                Oi {_nome_html}, identificamos um pagamento pendente da sua assinatura PUBSHOW.
                Você tem <strong style="color:#f59e0b">{_GRACE_PERIOD_DAYS} dias de carência</strong> antes do Jukebox ser pausado.
              </p>
              <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
                <a href="{planos_url}" style="display:inline-block;background:#f59e0b;color:#08080f;font-weight:800;font-size:15px;padding:12px 32px;border-radius:8px;text-decoration:none;">
                  Regularizar pagamento →
                </a>
              </td></tr></table>
            """
            wa_msg = (f'⚠️ *PUBSHOW — Pagamento pendente*\n'
                      f'Bar: {bd["nome"]}\nPlano: {nome_plano}\n'
                      f'Carência: {_GRACE_PERIOD_DAYS} dias')

        elif tipo == 'suspenso':
            assunto = f'🔴 Jukebox pausado — regularize seu pagamento'
            corpo = f"""
              <h1 style="color:#f9fafb;font-size:22px;font-weight:800;margin:0 0 8px;">
                Jukebox pausado 🔴
              </h1>
              <p style="color:#9ca3af;font-size:15px;margin:0 0 24px;line-height:1.6;">
                Oi {_nome_html}, o período de carência encerrou e seu Jukebox foi temporariamente pausado.
                Regularize o pagamento para reativar imediatamente.
              </p>
              <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
                <a href="{planos_url}" style="display:inline-block;background:#ef4444;color:#fff;font-weight:800;font-size:15px;padding:12px 32px;border-radius:8px;text-decoration:none;">
                  Reativar Jukebox →
                </a>
              </td></tr></table>
            """
            wa_msg = (f'🔴 *PUBSHOW — Jukebox PAUSADO*\n'
                      f'Bar: {bd["nome"]}\nAção necessária: regularizar pagamento')
        else:
            return

        # Envia email
        try:
            _pubshow_enviar_email(bd['email'], assunto,
                                  _email_html_base('PUBSHOW — Assinatura', corpo))
        except Exception as e:
            log.error('[PUBSHOW] Email assinatura erro: %s', e)

        # Envia WA para o bar e para o admin
        try:
            _pubshow_send_wa(re.sub(r'\D', '', bd['telefone'] or ''), wa_msg)
            admin_phone = os.environ.get('PUBSHOW_ADMIN_PHONE', '')
            if admin_phone:
                _pubshow_send_wa(admin_phone,
                                 f'[ADMIN] {wa_msg}\nEmail: {bd["email"]}')
        except Exception as e:
            log.error('[PUBSHOW] WA assinatura erro: %s', e)

    except Exception as e:
        log.error('[PUBSHOW] _notify_assinatura erro: %s', e)


def _sync_assinaturas_asaas():
    """Sync proativo com Asaas — verifica status real de todas as assinaturas ativas.
    Rate-limited: 1x por hora por processo."""
    global _email_last_check
    # Reutiliza rate-limit slot separado via módulo-level var
    pass  # Implementação via _cron/sync-assinaturas (com rate limit próprio)


# ── WEBHOOK ASAAS ─────────────────────────────────────────────────────────────

def _pedido_id_de_ext_ref(ext_ref):
    """'jukebox_<id>' → id (int) ou None."""
    if not ext_ref or not ext_ref.startswith('jukebox_'):
        return None
    _parts = ext_ref.split('_', 1)
    if len(_parts) < 2 or not _parts[1].isdigit():
        log.warning('[PUBSHOW] Webhook jukebox: ext_ref inválida: %s', ext_ref)
        return None
    return int(_parts[1])


def _confirmar_jukebox_por_ext_ref(ext_ref, pay_id='', valor=None):
    """Confirma um pedido de jukebox a partir do externalReference 'jukebox_<id>'.
    Idempotente. Chamada pelos DOIS webhooks (assinatura e jukebox) — assim um
    único webhook configurado no Asaas (qualquer das duas URLs) confirma tudo.
    No-op se o ext_ref não for de jukebox.
    15/10/26: só confirma se o payment.id for o DESTE pedido e o valor for o cobrado;
    grava pago_em (a hora do dinheiro, que alimenta todas as somas).
    """
    pedido_id = _pedido_id_de_ext_ref(ext_ref)
    if pedido_id is None:
        return
    try:
        conn = get_pubshow_db()
        ped = conn.execute('SELECT status, asaas_payment_id, valor_cobrado FROM pubshow_pedidos WHERE id=?',
                           (pedido_id,)).fetchone()
        if not ped or not pay_id or ped['asaas_payment_id'] != pay_id:
            conn.close()
            log.warning('[PUBSHOW] Webhook jukebox IGNORADO: pedido #%s, payment %s não é o da cobrança (%s)',
                        pedido_id, pay_id, ped['asaas_payment_id'] if ped else 'sem pedido')
            return
        if ped['valor_cobrado'] is not None:
            try:
                _valor_ok = valor is not None and abs(float(valor) - float(ped['valor_cobrado'])) < 0.005
            except (TypeError, ValueError):
                _valor_ok = False
            if not _valor_ok:
                conn.close()
                log.warning('[PUBSHOW] Webhook jukebox IGNORADO: pedido #%s, valor %s ≠ cobrado %s',
                            pedido_id, valor, ped['valor_cobrado'])
                return
        _novo = _status_apos_pix(conn, pedido_id)
        # estornado_em: o PIX já foi devolvido — um RECEIVED atrasado (ex.: da outra URL de webhook,
        # que deduplica com outro prefixo) não pode pôr o dinheiro de volta nas somas (15/10/26)
        n = conn.execute(
            "UPDATE pubshow_pedidos SET status=?, pago_em=COALESCE(pago_em, datetime('now')) "
            "WHERE id=? AND status='aguardando_pix' AND estornado_em IS NULL",
            (_novo, pedido_id)
        ).rowcount
        if not n:
            # PIX caiu num pedido que já saiu do aguardando_pix (ex.: recusado): registra o dinheiro
            # (pago_em) sem mexer no status — fica visível pra estorno; as somas não contam recusado.
            if conn.execute("UPDATE pubshow_pedidos SET pago_em=datetime('now') "
                            "WHERE id=? AND pago_em IS NULL AND estornado_em IS NULL",
                            (pedido_id,)).rowcount:
                log.warning('[PUBSHOW] PIX caiu no pedido #%s com status %s — conferir estorno', pedido_id, ped['status'])
        conn.commit(); conn.close()
        log.info('[PUBSHOW] Jukebox PIX confirmado — pedido #%s (Asaas %s) → %s', pedido_id, pay_id, _novo)
    except Exception as ex:
        log.error('[PUBSHOW] Confirmar jukebox erro: %s', ex, exc_info=True)


def _estornar_jukebox_por_ext_ref(ext_ref, pay_id=''):
    """PAYMENT_REFUNDED (15/10/26): o dinheiro voltou pro cliente → tira o pago_em (sai das somas)."""
    pedido_id = _pedido_id_de_ext_ref(ext_ref)
    if pedido_id is None or not pay_id:
        return
    try:
        conn = get_pubshow_db()
        n = conn.execute("UPDATE pubshow_pedidos SET pago_em=NULL, estornado_em=COALESCE(estornado_em, datetime('now')) "
                         "WHERE id=? AND asaas_payment_id=?",
                         (pedido_id, pay_id)).rowcount
        conn.commit(); conn.close()
        log.info('[PUBSHOW] Jukebox PIX ESTORNADO — pedido #%s (Asaas %s): %s', pedido_id, pay_id,
                 'fora das somas' if n else 'pedido não confere')
    except Exception as ex:
        log.error('[PUBSHOW] Estornar jukebox erro: %s', ex, exc_info=True)


def _webhook_token_ok():
    """(ok, resposta_de_erro). Fecha sem ASAAS_WEBHOOK_TOKEN; compara em tempo constante (15/10/26)."""
    token_esp = os.environ.get('ASAAS_WEBHOOK_TOKEN', '').strip().strip('"').strip("'")
    token_rec = (request.headers.get('asaas-access-token', '') or '').strip().strip('"').strip("'")
    if not token_esp:
        log.warning('[PUBSHOW] Webhook recebido mas ASAAS_WEBHOOK_TOKEN não configurado — bloqueado')
        return False, (jsonify({'error': 'not configured'}), 403)
    if not hmac.compare_digest(token_rec.encode(), token_esp.encode()):
        return False, (jsonify({'error': 'unauthorized'}), 401)
    return True, None


def _webhook_ja_processado(chave):
    """True se esta chave (id do evento, por webhook) já teve efeito — o Asaas reenvia (15/10/26)."""
    if not chave:
        return False
    try:
        conn = get_pubshow_db()
        n = conn.execute('INSERT OR IGNORE INTO pubshow_webhook_eventos (id) VALUES (?)',
                         (str(chave)[:200],)).rowcount
        conn.commit(); conn.close()
        return n == 0
    except Exception as ex:
        log.error('[PUBSHOW] Dedup webhook erro: %s', ex)
        return False


def _webhook_desmarcar(*chaves):
    """Apaga a marca de 'já processado' quando o efeito FALHOU, pro próximo evento/reenvio do Asaas
    tentar de novo (16/10/26, C3: CONFIRMED falhava com 'database is locked' e o RECEIVED era pulado)."""
    try:
        conn = get_pubshow_db()
        for ch in chaves:
            if ch:
                conn.execute('DELETE FROM pubshow_webhook_eventos WHERE id=?', (str(ch)[:200],))
        conn.commit(); conn.close()
    except Exception as ex:
        log.error('[PUBSHOW] Desmarcar webhook erro: %s', ex)


@pubshow_bp.route('/webhook/asaas', methods=['GET', 'POST'])
def webhook_asaas():
    """Recebe eventos de assinatura do Asaas.

    Configurar no painel Asaas → Configurações → Webhooks:
      URL: https://<dominio>/pubshow/webhook/asaas
      Eventos: PAYMENT_RECEIVED, PAYMENT_CONFIRMED, PAYMENT_OVERDUE,
               SUBSCRIPTION_ACTIVATED, SUBSCRIPTION_CANCELLED, SUBSCRIPTION_INACTIVATED
    """
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200

    # A8 fix: rejeita se token não configurado OU se token não bate (compare_digest, 15/10/26)
    _ok, _resp = _webhook_token_ok()
    if not _ok:
        return _resp

    dados     = request.get_json(silent=True) or {}
    evento    = dados.get('event', '')
    pagamento = dados.get('payment') or {}
    sub_obj   = dados.get('subscription') or {}  # para eventos SUBSCRIPTION_*
    ext_ref   = pagamento.get('externalReference', '') or sub_obj.get('externalReference', '') or ''
    sub_id    = pagamento.get('subscription', '') or sub_obj.get('id', '')

    log.info('[PUBSHOW] Webhook Asaas evento=%s ext_ref=%s sub_id=%s', evento, ext_ref, sub_id)

    # O Asaas entrega "pelo menos 1 vez": o mesmo evento só tem efeito 1 vez (15/10/26)
    if _webhook_ja_processado(f'ass:{dados.get("id")}' if dados.get('id') else ''):
        return jsonify({'status': 'ok', 'duplicado': True}), 200

    # ── Pagamento confirmado / assinatura ativada ─────────────────────────────
    if evento in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED', 'SUBSCRIPTION_ACTIVATED'):
        # Um único webhook serve assinatura E jukebox — confirma o jukebox se for o caso
        _confirmar_jukebox_por_ext_ref(ext_ref, pagamento.get('id', ''), pagamento.get('value'))
        # CONFIRMED + RECEIVED do MESMO pagamento (cartão) ativavam e avisavam 2x (15/10/26)
        _pay_dup = bool(pagamento.get('id')) and ext_ref.startswith('pubshow_') and \
            _webhook_ja_processado(f'ass_pago:{pagamento.get("id")}')
        if ext_ref.startswith('pubshow_') and not _pay_dup:
            partes = ext_ref.split('_')
            if len(partes) >= 3:
                _ativou = False
                try:
                    bid   = int(partes[1])
                    plano = partes[2]
                    # assinatura antiga (pubshow_<id>_pro|starter|rede) ativa o plano único (15/10/26)
                    if plano in PLANOS_LEGADOS:
                        plano = 'bar'
                    if plano in PLANOS:
                        # Busca próximo vencimento na assinatura Asaas
                        proximo_venc = None
                        if sub_id:
                            try:
                                sub_data = _asaas_req('GET', f'/subscriptions/{sub_id}')
                                proximo_venc = sub_data.get('nextDueDate')
                            except Exception:
                                pass
                        _assinatura_ativar(bid, plano, sub_id, proximo_venc)
                        _ativou = True
                        # 17/10/26 (decisão 8): PAGAMENTO de verdade (não só "assinatura ativada") = vaga de fundador
                        if evento in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED') and float(pagamento.get('value') or 0) > 0:
                            _virar_fundador(bid)
                        _notify_assinatura(bid, 'pagamento_confirmado')
                except Exception as ex:
                    log.error('[PUBSHOW] Webhook ativar erro: %s', ex)
                    if not _ativou:
                        # não ativou: o RECEIVED/reenvio do mesmo pagamento precisa poder ativar (16/10/26, C3)
                        _webhook_desmarcar(f'ass_pago:{pagamento.get("id")}' if pagamento.get('id') else '',
                                           f'ass:{dados.get("id")}' if dados.get('id') else '')

    # ── Pagamento em atraso — carência de N dias ──────────────────────────────
    elif evento == 'PAYMENT_OVERDUE':
        if sub_id:
            try:
                conn = get_pubshow_db()
                ass = conn.execute(
                    'SELECT * FROM pubshow_assinaturas WHERE asaas_subscription_id=?', (sub_id,)
                ).fetchone()
                conn.close()
                ass = dict(ass) if ass else None   # sqlite3.Row não tem .get() (15/10/26)
                if ass:
                    bid_overdue = ass['business_id']
                    _assinatura_inadimplente(bid_overdue, ass['id'])
                    # Notifica apenas se acabou de entrar em inadimplência
                    if not ass.get('inadimplente_desde'):
                        _notify_assinatura(bid_overdue, 'inadimplente')
                elif ext_ref.startswith('pubshow_'):
                    partes = ext_ref.split('_')
                    if len(partes) >= 3:
                        bid_overdue = int(partes[1])
                        plano_overdue = partes[2]
                        conn2 = get_pubshow_db()
                        ass2 = conn2.execute(
                            'SELECT * FROM pubshow_assinaturas WHERE business_id=?', (bid_overdue,)
                        ).fetchone()
                        conn2.close()
                        if ass2:
                            _assinatura_inadimplente(bid_overdue, ass2['id'])
            except Exception as ex:
                log.error('[PUBSHOW] Webhook overdue erro: %s', ex)

    # ── PIX do jukebox devolvido ao cliente → sai das somas (15/10/26) ────────
    elif evento == 'PAYMENT_REFUNDED':
        _estornar_jukebox_por_ext_ref(ext_ref, pagamento.get('id', ''))

    # ── Assinatura cancelada / inativada ──────────────────────────────────────
    # (os eventos reais do Asaas são SUBSCRIPTION_INACTIVATED/DELETED; CANCELLED fica por compatibilidade)
    elif evento in ('SUBSCRIPTION_CANCELLED', 'SUBSCRIPTION_INACTIVATED', 'SUBSCRIPTION_DELETED'):
        target_sub = sub_id or (sub_obj.get('id') if sub_obj else '')
        if target_sub:
            try:
                conn = get_pubshow_db()
                ass = conn.execute(
                    'SELECT * FROM pubshow_assinaturas WHERE asaas_subscription_id=?', (target_sub,)
                ).fetchone()
                conn.close()
                if ass:
                    _assinatura_cancelar(ass['business_id'])
            except Exception as ex:
                log.error('[PUBSHOW] Webhook cancelar erro: %s', ex)

    return jsonify({'status': 'ok'}), 200


# ── WEBHOOK ASAAS — JUKEBOX (cobranças avulsas PIX) ──────────────────────────

@pubshow_bp.route('/webhook/asaas-jukebox', methods=['GET', 'POST'])
def webhook_asaas_jukebox():
    """Recebe confirmação de pagamento PIX do Asaas para pedidos de Jukebox.

    Configurar no painel Asaas:
      URL: https://<seu-dominio>/pubshow/webhook/asaas-jukebox
      Eventos: PAYMENT_RECEIVED, PAYMENT_CONFIRMED
    """
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200

    # A8 fix: rejeita se token não configurado OU se token não bate (compare_digest, 15/10/26)
    _ok, _resp = _webhook_token_ok()
    if not _ok:
        return _resp

    dados     = request.get_json(silent=True) or {}
    evento    = dados.get('event', '')
    pagamento = dados.get('payment') or {}
    ext_ref   = pagamento.get('externalReference', '') or ''
    pay_id    = pagamento.get('id', '')

    log.info('[PUBSHOW] Webhook Jukebox: evento=%s ref=%s', evento, ext_ref)

    # O Asaas entrega "pelo menos 1 vez": o mesmo evento só tem efeito 1 vez (15/10/26)
    if _webhook_ja_processado(f'jk:{dados.get("id")}' if dados.get('id') else ''):
        return jsonify({'status': 'ok', 'duplicado': True}), 200

    if evento in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED'):
        _confirmar_jukebox_por_ext_ref(ext_ref, pay_id, pagamento.get('value'))
    elif evento == 'PAYMENT_REFUNDED':
        _estornar_jukebox_por_ext_ref(ext_ref, pay_id)

    return jsonify({'status': 'ok'}), 200
