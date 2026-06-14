"""
radar.py — Blueprint do Radar de Licitações de TI (módulo do 4kitem)

LOTE 0+1: fundação + coletor PNCP.
  • Coletor: lê a API pública do PNCP (/contratacoes/proposta) e enche o banco.
  • Classificação básica embutida (Eixo 1 = é TI? / Eixo 2 = que porte?).
  • Painel simples pra VER o que entrou (dogfood do Diogo).

Foco: serviços TI médio/pequenos que ele + IA entregam (cauda longa municipal).
Nada "Microsoft" (licença de terceiros / hardware são excluídos na triagem).

API PNCP: https://pncp.gov.br/api/consulta  (REST, JSON, grátis, sem login)
Validação ao vivo: rodar /radar/coletar numa máquina com internet.
"""
import os
import logging
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps

import requests
from flask import Blueprint, request, jsonify, render_template_string, redirect

from flask import session
from werkzeug.security import generate_password_hash, check_password_hash
from radar_db import (init_radar_db, upsert_licitacao, listar_licitacoes,
                      estatisticas, registrar_coleta, obter_licitacao, salvar_analise,
                      upsert_contrato, listar_contratos, stats_contratos,
                      get_radar_user, get_radar_user_by_email, contar_radar_users,
                      criar_radar_user, listar_radar_users, radar_exec)

log = logging.getLogger('radar')

radar_bp = Blueprint('radar', __name__, url_prefix='/radar')

# ── Config (tudo via env, com defaults sensatos) ─────────────────────────────
PNCP_BASE      = os.environ.get('PNCP_BASE', 'https://pncp.gov.br/api/consulta')
RADAR_TOKEN    = os.environ.get('RADAR_TOKEN', '').strip()      # se vazio = aberto (dev)
RADAR_UF       = os.environ.get('RADAR_UF', '').strip().upper()  # '' = Brasil todo
RADAR_JANELA   = int(os.environ.get('RADAR_JANELA_DIAS', '20'))  # editais fechando em N dias
RADAR_MAXPAG   = int(os.environ.get('RADAR_MAX_PAGINAS', '20'))  # teto por modalidade/rodada

# Modalidades do PNCP (código -> nome). Foco MVP: pregão eletrônico + dispensa.
MODALIDADES = {
    1: 'Leilão eletrônico', 4: 'Concorrência eletrônica', 6: 'Pregão eletrônico',
    8: 'Dispensa de licitação', 9: 'Inexigibilidade', 12: 'Credenciamento',
}
MODALIDADES_FOCO = [int(x) for x in os.environ.get('RADAR_MODALIDADES', '6,8').split(',') if x.strip()]

# ── Dicionário do Eixo 1 (é TI?) ─────────────────────────────────────────────
# Tier 1 = casa com produto pronto (AgendaJá/DRZAP/Pente Fino/VetZap→humano/portal/ouvidoria)
KW_TIER1 = [
    'agendamento', 'ouvidoria', 'protocolo eletronico', 'protocolo eletrônico',
    'protocolo digital', 'processo eletronico', 'processo administrativo eletronico',
    'e-sic', 'esic', 'lei de acesso', 'portal da transparencia', 'portal da transparência',
    'site institucional', 'portal institucional', 'portal do cidadao', 'portal do cidadão',
    'app do cidadao', 'aplicativo do cidadao', 'atendimento ao cidadao', 'atendimento ao cidadão',
    'central de atendimento', 'gestao documental', 'gestão documental', 'ged ', 'sei ',
    'digitalizacao', 'digitalização', 'assinatura eletronica', 'assinatura digital',
    'chatbot', 'assistente virtual', 'telemedicina', 'telessaude', 'telessaúde',
    'diario oficial eletronico', 'diário oficial eletrônico', 'nota fiscal eletronica',
    'nfs-e', 'nfse', 'nota fiscal de servico', 'pesquisa de satisfacao',
]
# Tier 2 = software/dev inequívoco (a palavra já indica que é software)
KW_TIER2 = [
    'desenvolvimento de sistema', 'desenvolvimento de software', 'fabrica de software',
    'sistema de informacao', 'sistema de informação', 'sistema de gestao', 'sistema de gestão',
    'sistema integrado', 'software', 'sistema informatizado', 'sistema web', 'sistema online',
    'aplicativo', 'aplicativo movel', 'aplicativo móvel', ' app ', 'plataforma digital',
    'plataforma web', 'solucao web', 'solução web', 'solucao tecnologica', 'solução tecnológica',
    'licenciamento de sistema', 'licenciamento de software', 'licenca de software de gestao',
    'hospedagem de site', 'hospedagem de sistema', 'cloud', 'computacao em nuvem',
    'sustentacao de sistema', 'sustentação de sistema',
    'manutencao de sistema', 'manutenção de sistema', 'manutencao de software',
    'dashboard', 'painel de indicadores', 'business intelligence', 'lgpd',
    'sistema tributario', 'sistema tributário', 'sistema de saude', 'sistema de educacao',
    'gestao escolar', 'sistema escolar', 'portal web', 'sitio eletronico',
    'transformacao digital', 'modernizacao tecnologica', 'integracao de sistemas',
]
# DOMÍNIO ambíguo: só vira TI se houver INDÍCIO de software junto (senão pega
# "prêmio do IPTU", "pallets do almoxarifado", "banco p/ folha de pagamento"...)
KW_CONTEXTO = [
    'folha de pagamento', 'recursos humanos', 'ponto eletronico', 'almoxarifado',
    'patrimonio', 'geoprocessamento', 'georreferenciamento', 'iptu', 'arrecadacao',
    'tributos', 'gestao tributaria', 'frota', 'contabilidade publica', 'nota fiscal',
]
KW_INDICIO = [
    'sistema', 'software', 'plataforma', 'aplicativo', ' app ', 'digital', 'informatizad',
    'modulo', 'módulo', 'licenciamento', ' web', 'online', 'tecnologia da informacao',
]
# Armadilhas (Tier 3) — "parece TI" mas NÃO é nosso. Se dominar, descarta.
KW_TRAP = [
    'microsoft', 'office 365', 'google workspace', 'windows', 'antivirus', 'antivírus',
    'oracle', 'sap', 'vmware', 'adobe', 'autocad',
    'licenca de uso', 'licença de uso',  # revenda de licença de terceiros
    'impressora', 'outsourcing de impressao', 'outsourcing de impressão', 'toner', 'cartucho',
    'computador', 'notebook', 'desktop', 'tablet', 'monitor ', 'mobiliario',
    'servidor de rede', 'switch', 'roteador', 'cabeamento', 'link de internet', 'links de internet',
    'telefonia', 'voip', 'no-break', 'nobreak', 'datacenter',
    'camera', 'cftv', 'videomonitoramento', 'video monitoramento', 'reconhecimento facial',
    'rastreador', 'rastreamento veicular', 'radio comunicador', 'totem', 'catraca',
    'locacao de equipamento', 'locação de equipamento', 'locacao de computador',
    'aquisicao de equipamento', 'aquisição de equipamento', 'manutencao predial', 'reforma',
    'combustivel', 'pneu', 'veiculo',  # frota física
    'audesp',  # ERP contábil integrado ao TCE — monstro travado pelo incumbente
    # ── armadilhas vistas no dado real (jun/2026) ──
    'kaspersky', 'endpoint security', 'endpoint detection', 'fortinet',  # antivírus/segurança de marca = revenda
    'premio', 'premios', 'prêmio', 'prêmios',  # "aquisição de prêmios p/ programa IPTU premiado"
    'pallet', 'madeira',                         # almoxarifado físico
    'instituicao financeira', 'instituição financeira',
    'servicos bancarios', 'serviços bancários', 'banco central',  # contrato bancário ≠ sistema
    'equipamentos de informatica', 'equipamentos de informática',  # manutenção de hardware
]

# ── Eixo 2 (porte) — faixas de valor, AFINÁVEIS por env ──────────────────────
# Régua do Diogo: ouro ≤65k (dispensa, sem atestado) · boa ≤250k (sobe c/ atestado).
# Quer focar até R$100k? RADAR_ZONA_BOA=100000. Sem tocar no código.
ZONA_OURO    = float(os.environ.get('RADAR_ZONA_OURO',    '65492.11'))
ZONA_BOA     = float(os.environ.get('RADAR_ZONA_BOA',     '250000'))
ZONA_DIFICIL = float(os.environ.get('RADAR_ZONA_DIFICIL', '1000000'))


def _norm(s):
    return (s or '').lower()


def classificar(objeto, valor, modalidade_id=None):
    """Eixo 1 (é TI?) + Eixo 2 (porte) + score básico. Lote 2 refina com IA."""
    o = _norm(objeto)
    matched = []

    trap = any(k in o for k in KW_TRAP)
    t1 = [k for k in KW_TIER1 if k in o]
    t2 = [k for k in KW_TIER2 if k in o]
    # domínio ambíguo (folha, iptu, almoxarifado...) só vira TI com indício de software
    ctx = [k for k in KW_CONTEXTO if k in o]
    if ctx and any(ind in o for ind in KW_INDICIO):
        t2 = t2 + ctx
    matched = (t1 + t2)[:6]

    if t1:
        tier = 1
    elif t2:
        tier = 2
    else:
        tier = None

    # é TI se casou keyword de TI E não é dominado por armadilha
    is_ti = 1 if (tier is not None and not trap) else 0
    if trap and tier is not None:
        tier = 3  # marca como armadilha (fica fora do is_ti)

    # Eixo 2 — zona de porte
    try:
        v = float(valor or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        zona = 'indef'
    elif v <= ZONA_OURO:
        zona = 'ouro'
    elif v <= ZONA_BOA:
        zona = 'boa'
    elif v <= ZONA_DIFICIL:
        zona = 'dificil'
    else:
        zona = 'nao'

    # Score 0..100 (Lote 2 soma atestado/garantia lidos do PDF)
    score = 0
    score += {'ouro': 40, 'boa': 25, 'indef': 15, 'dificil': 5, 'nao': 0}[zona]
    score += {1: 30, 2: 15, 3: 0, None: 0}[tier]
    if modalidade_id in (6, 8):     # pregão eletrônico ou dispensa = fácil de disputar
        score += 15
    if modalidade_id == 8:          # dispensa = entrada sem atestado pesado
        score += 10
    score = max(0, min(100, score))
    if not is_ti:                   # não é nosso -> score zero (não polui rankings)
        score = 0

    return {'is_ti': is_ti, 'tier': tier, 'zona_valor': zona,
            'score': score, 'keywords_match': ', '.join(matched)}


# ── Cliente PNCP ─────────────────────────────────────────────────────────────
def _pncp_get(endpoint, params, tentativas=2):
    url = PNCP_BASE.rstrip('/') + endpoint
    for i in range(tentativas):
        try:
            r = requests.get(url, params=params, timeout=30,
                             headers={'Accept': 'application/json'})
            if r.status_code == 204:        # sem conteúdo nessa página
                return {'data': [], 'totalPaginas': 0}
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning(f'[RADAR] PNCP {endpoint} tentativa {i+1} falhou: {e}')
    return None


def _mapear(item):
    """Mapeia um item do PNCP -> linha do banco (defensivo a variação de campo)."""
    unidade = item.get('unidadeOrgao') or {}
    orgao   = item.get('orgaoEntidade') or {}
    objeto  = item.get('objetoCompra') or item.get('objeto') or ''
    valor   = item.get('valorTotalEstimado') or item.get('valorTotal') or 0
    mod_id  = item.get('modalidadeId') or item.get('codigoModalidadeContratacao')
    cl = classificar(objeto, valor, mod_id)
    return {
        'pncp_id':           item.get('numeroControlePNCP') or item.get('numeroControlePncp'),
        'objeto':            objeto,
        'valor':             valor,
        'modalidade':        item.get('modalidadeNome') or MODALIDADES.get(mod_id, ''),
        'modalidade_id':     mod_id,
        'situacao':          item.get('situacaoCompraNome') or item.get('situacaoNome'),
        'orgao':             orgao.get('razaoSocial') or unidade.get('nomeUnidade'),
        'orgao_cnpj':        orgao.get('cnpj'),
        'uf':                unidade.get('ufSigla') or unidade.get('uf'),
        'municipio':         unidade.get('municipioNome'),
        'codigo_ibge':       unidade.get('codigoIbge'),
        'data_publicacao':   item.get('dataPublicacaoPncp') or item.get('dataInclusao'),
        'data_abertura':     item.get('dataAberturaProposta'),
        'data_encerramento': item.get('dataEncerramentoProposta'),
        'link':              item.get('linkSistemaOrigem') or item.get('linkSistemaExterno'),
        'raw_json':          item,
        **cl,
    }


def coletar(uf=None, modalidades=None, janela_dias=None, max_paginas=None):
    """Lê o PNCP e enche o banco. Idempotente (upsert por pncp_id)."""
    uf          = (uf if uf is not None else RADAR_UF) or None
    modalidades = modalidades or MODALIDADES_FOCO
    janela_dias = janela_dias if janela_dias is not None else RADAR_JANELA
    max_paginas = max_paginas if max_paginas is not None else RADAR_MAXPAG
    data_final  = (datetime.now() + timedelta(days=janela_dias)).strftime('%Y%m%d')

    novos = atualizados = erros = paginas = 0
    for mod in modalidades:
        pagina = 1
        while pagina <= max_paginas:
            params = {'dataFinal': data_final, 'codigoModalidadeContratacao': mod,
                      'pagina': pagina, 'tamanhoPagina': 50}
            if uf:
                params['uf'] = uf
            resp = _pncp_get('/v1/contratacoes/proposta', params)
            if not resp:
                erros += 1
                break
            data = resp.get('data') or []
            if not data:
                break
            paginas += 1
            for item in data:
                try:
                    r = _mapear(item)
                    if not r.get('pncp_id'):
                        erros += 1; continue
                    res = upsert_licitacao(r)
                    if res == 'novo':        novos += 1
                    elif res == 'atualizado': atualizados += 1
                    else:                     erros += 1
                except Exception as e:
                    erros += 1
                    log.warning(f'[RADAR] erro ao processar item: {e}')
            total_pag = resp.get('totalPaginas') or 1
            if pagina >= total_pag:
                break
            pagina += 1

    registrar_coleta(uf, modalidades, paginas, novos, atualizados, erros)
    log.info(f'[RADAR] coleta uf={uf or "BR"} mod={modalidades} '
             f'paginas={paginas} novos={novos} atualizados={atualizados} erros={erros}')
    return {'uf': uf or 'BR', 'modalidades': modalidades, 'paginas': paginas,
            'novos': novos, 'atualizados': atualizados, 'erros': erros,
            'data_final': data_final}


# ── Lote 5: coletor de CONTRATOS (inteligência de preço) ─────────────────────
RADAR_CONTRATOS_DIAS = int(os.environ.get('RADAR_CONTRATOS_DIAS', '90'))  # lookback


def _mapear_contrato(item):
    unidade = item.get('unidadeOrgao') or {}
    orgao   = item.get('orgaoEntidade') or {}
    objeto  = item.get('objetoContrato') or item.get('objeto') or ''
    valor   = (item.get('valorGlobal') or item.get('valorInicial')
               or item.get('valorTotal') or 0)
    cl = classificar(objeto, valor)
    return {
        'pncp_id':         item.get('numeroControlePNCP') or item.get('numeroControlePncpCompra'),
        'objeto':          objeto,
        'valor':           valor,
        'fornecedor':      (item.get('nomeRazaoSocialFornecedor') or item.get('nomeFornecedor')
                            or (item.get('fornecedor') or {}).get('nome')),
        'fornecedor_doc':  (item.get('niFornecedor') or item.get('cnpjCpfFornecedor')
                            or (item.get('fornecedor') or {}).get('ni')),
        'orgao':           orgao.get('razaoSocial') or unidade.get('nomeUnidade'),
        'uf':              unidade.get('ufSigla') or unidade.get('uf'),
        'municipio':       unidade.get('municipioNome'),
        'modalidade':      item.get('modalidadeNome'),
        'data_assinatura': item.get('dataAssinatura'),
        'vigencia_inicio': item.get('dataVigenciaInicio'),
        'vigencia_fim':    item.get('dataVigenciaFim'),
        'link':            item.get('linkSistemaOrigem'),
        'is_ti':           cl['is_ti'],
        'keywords_match':  cl['keywords_match'],
        'raw_json':        item,
    }


def coletar_contratos(uf=None, dias=None, max_paginas=None):
    """Lê contratos assinados do PNCP (/v1/contratos) — inteligência de preço.
    Guarda só os de TI (is_ti) p/ não inchar o banco. Filtra UF client-side."""
    uf          = (uf if uf is not None else RADAR_UF) or None
    dias        = dias if dias is not None else RADAR_CONTRATOS_DIAS
    max_paginas = max_paginas if max_paginas is not None else RADAR_MAXPAG
    hoje        = datetime.now()
    di = (hoje - timedelta(days=dias)).strftime('%Y%m%d')
    df = hoje.strftime('%Y%m%d')

    novos = atualizados = erros = paginas = pulados = 0
    pagina = 1
    while pagina <= max_paginas:
        resp = _pncp_get('/v1/contratos', {'dataInicial': di, 'dataFinal': df,
                                           'pagina': pagina, 'tamanhoPagina': 50})
        if not resp:
            erros += 1; break
        data = resp.get('data') or []
        if not data:
            break
        paginas += 1
        for item in data:
            try:
                r = _mapear_contrato(item)
                if not r.get('pncp_id'):
                    erros += 1; continue
                if uf and (r.get('uf') or '').upper() != uf.upper():
                    pulados += 1; continue          # filtra UF aqui (param não confiável na API)
                if not r.get('is_ti'):
                    pulados += 1; continue          # só guarda TI (moat enxuto)
                res = upsert_contrato(r)
                if res == 'novo':        novos += 1
                elif res == 'atualizado': atualizados += 1
                else:                     erros += 1
            except Exception as e:
                erros += 1
                log.warning(f'[RADAR] erro contrato: {e}')
        if pagina >= (resp.get('totalPaginas') or 1):
            break
        pagina += 1

    log.info(f'[RADAR] contratos uf={uf or "BR"} paginas={paginas} novos={novos} '
             f'atualizados={atualizados} pulados={pulados} erros={erros}')
    return {'uf': uf or 'BR', 'paginas': paginas, 'novos': novos,
            'atualizados': atualizados, 'pulados': pulados, 'erros': erros}


# ── Coleta em segundo plano (evita travar a requisição web) ──────────────────
_bg = {'rodando': False, 'msg': 'ainda não rodou', 'em': None}


def _coleta_background(uf, licitacoes=True, precos=True):
    try:
        partes = []
        if licitacoes:
            r = coletar(uf=uf)
            partes.append(f"licitações: +{r['novos']} novos, {r['atualizados']} atual.")
        if precos:
            r = coletar_contratos(uf=uf)
            partes.append(f"preços: +{r['novos']} novos")
        _bg['msg'] = ' | '.join(partes) or 'nada coletado'
        _bg['em'] = datetime.now().strftime('%d/%m %H:%M')
        log.info(f'[RADAR] coleta bg OK: {_bg["msg"]}')
    except Exception as e:
        _bg['msg'] = f'erro: {e}'
        log.error(f'[RADAR] coleta bg erro: {e}', exc_info=True)
    finally:
        _bg['rodando'] = False


def _disparar_bg(uf, licitacoes=True, precos=True):
    if _bg['rodando']:
        return False
    _bg['rodando'] = True
    threading.Thread(target=_coleta_background, kwargs={'uf': uf, 'licitacoes': licitacoes,
                     'precos': precos}, daemon=True, name='radar-coleta-web').start()
    return True


# ══════════════════════════════════════════════════════════════════════════════
# LOTE 4 — Análise de Edital por IA (Gemini primário + Groq reserva)
# ══════════════════════════════════════════════════════════════════════════════
import json as _json

GEMINI_KEY    = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL  = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
_GEMINI_URL   = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
GROQ_KEY      = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL    = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')
_GROQ_URL     = 'https://api.groq.com/openai/v1/chat/completions'
MAX_TEXTO_PDF = int(os.environ.get('RADAR_MAX_TEXTO_PDF', '40000'))  # corta p/ controlar tokens

SYSTEM_EDITAL = """Você é o analista do RADAR, um assistente que avalia EDITAIS de licitação \
do governo brasileiro para um FORNECEDOR PEQUENO de TI/software que entrega de forma REMOTA \
(desenvolvimento de sistema/site, agendamento, ouvidoria, portal, gestão documental, telemedicina, \
licenciamento de SaaS). Ele trabalha sozinho com ajuda de IA — NÃO tem equipe grande nem atestados \
robustos no começo. Objetivo: dizer, sem juridiquês, SE VALE A PENA disputar e COMO.

Responda SOMENTE com um JSON válido nesta estrutura exata:
{
 "viavel": "sim | talvez | nao",
 "resumo": "1-2 frases: o que o órgão quer comprar",
 "e_ti": true/false,
 "exige_atestado": "sim | nao | nao_claro",
 "atestado_detalhe": "o que o edital pede de capacidade técnica (ou vazio)",
 "exige_garantia": "sim | nao | nao_claro",
 "valor": "valor estimado se aparecer (ou 'não informado')",
 "prazo": "data/horário de entrega das propostas se aparecer",
 "habilitacao": ["principais documentos de habilitação exigidos"],
 "dificuldade": "facil | media | dificil",
 "riscos": ["pontos de atenção: atestado que ele talvez não tenha, garantia, prazo curto, edital direcionado..."],
 "plano": ["passo a passo objetivo do que fazer para participar"],
 "veredito": "1 frase direta: VAI ou NÃO VAI, e por quê"
}
Regras: seja honesto e prático. Se o texto for pobre (só o objeto, sem o edital completo), faça a melhor \
leitura possível e marque 'nao_claro' onde faltar informação. Nunca invente exigência que não está no texto."""


def _gemini_call(system, user_text, max_tokens=2048):
    body = {'contents': [{'parts': [{'text': user_text}]}],
            'generationConfig': {'temperature': 0.2, 'maxOutputTokens': max_tokens,
                                 'responseMimeType': 'application/json'}}
    if system:
        body['systemInstruction'] = {'parts': [{'text': system}]}
    r = requests.post(_GEMINI_URL.format(model=GEMINI_MODEL),
                      params={'key': GEMINI_KEY}, json=body, timeout=90)
    r.raise_for_status()
    data = r.json()
    return data['candidates'][0]['content']['parts'][0]['text'].strip()


def _groq_call(system, user_text, max_tokens=2048):
    body = {'model': GROQ_MODEL, 'temperature': 0.2, 'max_tokens': max_tokens,
            'response_format': {'type': 'json_object'},
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': user_text}]}
    r = requests.post(_GROQ_URL, headers={'Authorization': f'Bearer {GROQ_KEY}'},
                      json=body, timeout=90)
    r.raise_for_status()
    return r.json()['choices'][0]['message']['content'].strip()


def _ia_json(system, user_text):
    """Tenta Gemini; se falhar, cai pro Groq. Retorna (dict, engine)."""
    erros = []
    if GEMINI_KEY:
        try:
            return _json.loads(_gemini_call(system, user_text)), 'gemini'
        except Exception as e:
            erros.append(f'gemini: {e}')
    if GROQ_KEY:
        try:
            return _json.loads(_groq_call(system, user_text)), 'groq'
        except Exception as e:
            erros.append(f'groq: {e}')
    raise RuntimeError('Nenhuma IA respondeu — ' + ' | '.join(erros) if erros
                       else 'Nenhuma IA configurada (GEMINI_API_KEY ou GROQ_API_KEY)')


def _texto_de_pdf(pdf_bytes):
    """Extrai texto de um PDF de edital (pdfplumber já está no requirements)."""
    try:
        import pdfplumber, io
        partes = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for pg in pdf.pages:
                partes.append(pg.extract_text() or '')
                if sum(len(p) for p in partes) > MAX_TEXTO_PDF:
                    break
        return '\n'.join(partes)[:MAX_TEXTO_PDF]
    except Exception as e:
        log.warning(f'[RADAR] falha ao ler PDF: {e}')
        return ''


def analisar_edital(licitacao: dict, texto_edital: str = ''):
    """Monta o contexto e pede a leitura à IA. Retorna (analise_dict, engine)."""
    ctx = (f"Objeto: {licitacao.get('objeto','')}\n"
           f"Valor estimado: {licitacao.get('valor') or 'não informado'}\n"
           f"Modalidade: {licitacao.get('modalidade','')}\n"
           f"Órgão: {licitacao.get('orgao','')} — {licitacao.get('uf','')} {licitacao.get('municipio','')}\n"
           f"Encerramento das propostas: {licitacao.get('data_encerramento','')}\n")
    if texto_edital.strip():
        ctx += f"\n--- TEXTO DO EDITAL (pode estar truncado) ---\n{texto_edital.strip()[:MAX_TEXTO_PDF]}"
    else:
        ctx += ("\n(OBS: o texto completo do edital NÃO foi fornecido — faça a leitura preliminar "
                "apenas com os metadados acima e marque como 'nao_claro' o que depender do edital.)")
    return _ia_json(SYSTEM_EDITAL, ctx)


# ══════════════════════════════════════════════════════════════════════════════
# AUTENTICAÇÃO (SaaS: login / senha / redefinir senha) + ADMIN
# ══════════════════════════════════════════════════════════════════════════════
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', '').strip().lower()


def _enviar_email(para, assunto, html):
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        return False
    from_addr = os.environ.get('EMAIL_FROM', 'Radar <onboarding@resend.dev>')
    try:
        resp = requests.post('https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'from': from_addr, 'to': [para], 'subject': assunto, 'html': html}, timeout=10)
        return resp.status_code in (200, 201)
    except Exception:
        return False


def _user():
    uid = session.get('radar_user_id')
    return get_radar_user(uid) if uid else None


def _is_admin(u):
    return bool(u and (u.get('is_admin') or (ADMIN_EMAIL and u.get('email') == ADMIN_EMAIL)))


def radar_login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get('radar_user_id'):
            return redirect('/radar/entrar')
        return f(*a, **k)
    return wrap


def radar_admin_required(f):
    @wraps(f)
    def wrap(*a, **k):
        # admin logado OU token válido (p/ cron/externo)
        if RADAR_TOKEN and (request.args.get('t') == RADAR_TOKEN
                            or request.headers.get('X-Radar-Token') == RADAR_TOKEN):
            return f(*a, **k)
        u = _user()
        if not _is_admin(u):
            if not u:
                return redirect('/radar/entrar')
            return 'Acesso restrito ao admin.', 403
        return f(*a, **k)
    return wrap


@radar_bp.context_processor
def _inject_user():
    u = _user()
    return {'radar_nome': (u or {}).get('nome', ''), 'radar_is_admin': _is_admin(u)}


# ── Rotas de auth ────────────────────────────────────────────────────────────
@radar_bp.route('/cadastrar', methods=['GET', 'POST'])
def rota_cadastrar():
    if session.get('radar_user_id'):
        return redirect('/radar/')
    erro = None
    if request.method == 'POST':
        nome  = (request.form.get('nome') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        tel   = ''.join(c for c in (request.form.get('telefone') or '') if c.isdigit())
        senha = request.form.get('senha') or ''
        if not nome or not email or not senha:
            erro = 'Preencha nome, e-mail e senha.'
        elif len(senha) < 6:
            erro = 'A senha precisa ter pelo menos 6 caracteres.'
        elif get_radar_user_by_email(email):
            erro = 'Já existe uma conta com esse e-mail. Faça login.'
        else:
            # 1º usuário (ou e-mail = ADMIN_EMAIL) vira admin automaticamente
            admin = 1 if (contar_radar_users() == 0 or (ADMIN_EMAIL and email == ADMIN_EMAIL)) else 0
            uid = criar_radar_user(nome, email, tel, generate_password_hash(senha), admin)
            session['radar_user_id'] = uid
            return redirect('/radar/')
    return render_template_string(_AUTH, modo='cadastrar', erro=erro)


@radar_bp.route('/entrar', methods=['GET', 'POST'])
def rota_entrar():
    if session.get('radar_user_id'):
        return redirect('/radar/')
    # 1ª vez (nenhum usuário ainda): manda direto pro cadastro (vira admin)
    if request.method == 'GET' and contar_radar_users() == 0:
        return redirect('/radar/cadastrar')
    erro = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        u = get_radar_user_by_email(email)
        if u and check_password_hash(u['password_hash'], senha):
            session['radar_user_id'] = u['id']
            radar_exec('UPDATE radar_users SET ultimo_acesso=CURRENT_TIMESTAMP WHERE id=?', (u['id'],))
            return redirect('/radar/')
        erro = 'E-mail ou senha incorretos.'
    return render_template_string(_AUTH, modo='entrar', erro=erro)


@radar_bp.route('/sair')
def rota_sair():
    session.pop('radar_user_id', None)
    return redirect('/radar/entrar')


@radar_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def rota_esqueci():
    msg = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        u = get_radar_user_by_email(email)
        if u:
            token = secrets.token_urlsafe(32)
            exp = (datetime.now() + timedelta(hours=2)).isoformat()
            radar_exec('UPDATE radar_users SET reset_token=?, reset_expires=? WHERE id=?',
                       (token, exp, u['id']))
            base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
            link = f"{base}/radar/redefinir-senha?token={token}"
            _enviar_email(email, 'Redefinir senha — Radar de Licitações',
                          f'<p>Olá! Clique para criar uma nova senha (vale 2h):</p>'
                          f'<p><a href="{link}">{link}</a></p>')
        msg = 'Se o e-mail existir, enviamos um link para redefinir a senha.'
    return render_template_string(_AUTH, modo='esqueci', msg=msg, erro=None)


@radar_bp.route('/redefinir-senha', methods=['GET', 'POST'])
def rota_redefinir():
    token = request.values.get('token', '')
    erro = msg = None
    from radar_db import get_radar_db as _grdb
    db = _grdb()
    u = db.execute("SELECT * FROM radar_users WHERE reset_token=? AND reset_token<>''",
                   (token,)).fetchone()
    db.close()
    if not u or (u['reset_expires'] or '') < datetime.now().isoformat():
        return render_template_string(_AUTH, modo='redefinir', erro='Link inválido ou expirado.',
                                      token='', msg=None)
    if request.method == 'POST':
        nova = request.form.get('senha') or ''
        if len(nova) < 6:
            erro = 'A senha precisa ter pelo menos 6 caracteres.'
        else:
            radar_exec("UPDATE radar_users SET password_hash=?, reset_token='', reset_expires='' WHERE id=?",
                       (generate_password_hash(nova), u['id']))
            return render_template_string(_AUTH, modo='entrar', erro=None,
                                          msg='Senha alterada! Faça login.')
    return render_template_string(_AUTH, modo='redefinir', token=token, erro=erro, msg=msg)


# ── Rotas ────────────────────────────────────────────────────────────────────
@radar_bp.route('/coletar', methods=['GET', 'POST'])
@radar_admin_required
def rota_coletar():
    uf = request.args.get('uf', RADAR_UF) or None
    iniciou = _disparar_bg(uf, licitacoes=True, precos=False)
    return jsonify({'ok': True,
                    'status': ('🚀 Coleta iniciada em segundo plano! Atualize /radar/ em 1-3 min.'
                               if iniciou else '⏳ Já existe uma coleta em andamento — aguarde terminar.'),
                    'ultima_coleta': _bg['msg']})


@radar_bp.route('/api/licitacoes')
@radar_login_required
def rota_api():
    return jsonify(listar_licitacoes(
        uf=request.args.get('uf'),
        somente_ti=request.args.get('todas') != '1',
        zona=request.args.get('zona'),
        valor_max=request.args.get('valor_max', type=float),
        ordem=request.args.get('ordem', 'score'),
        limite=request.args.get('limite', default=200, type=int),
    ))


@radar_bp.route('/stats')
@radar_admin_required
def rota_stats():
    return jsonify(estatisticas())


@radar_bp.route('/coletar-precos', methods=['GET', 'POST'])
@radar_admin_required
def rota_coletar_precos():
    uf = request.args.get('uf', RADAR_UF) or None
    iniciou = _disparar_bg(uf, licitacoes=False, precos=True)
    return jsonify({'ok': True,
                    'status': ('🚀 Coleta de preços iniciada em segundo plano! Veja /radar/precos em 1-3 min.'
                               if iniciou else '⏳ Já existe uma coleta em andamento — aguarde terminar.'),
                    'ultima_coleta': _bg['msg']})


@radar_bp.route('/coletar-status')
@radar_admin_required
def rota_coletar_status():
    return jsonify(_bg)


@radar_bp.route('/reclassificar', methods=['GET', 'POST'])
@radar_admin_required
def rota_reclassificar():
    """Reprocessa a classificação do que JÁ está no banco com as regras atuais
    (sem re-baixar do PNCP). Útil depois de afinar o dicionário de triagem."""
    from radar_db import get_radar_db
    conn = get_radar_db()
    nl = nc = 0
    for r in conn.execute('SELECT id, objeto, valor, modalidade_id FROM radar_licitacoes').fetchall():
        c = classificar(r['objeto'], r['valor'], r['modalidade_id'])
        conn.execute('UPDATE radar_licitacoes SET is_ti=?, tier=?, zona_valor=?, score=?, '
                     'keywords_match=? WHERE id=?',
                     (c['is_ti'], c['tier'], c['zona_valor'], c['score'], c['keywords_match'], r['id']))
        nl += 1
    for r in conn.execute('SELECT id, objeto, valor FROM radar_contratos').fetchall():
        c = classificar(r['objeto'], r['valor'])
        conn.execute('UPDATE radar_contratos SET is_ti=?, keywords_match=? WHERE id=?',
                     (c['is_ti'], c['keywords_match'], r['id']))
        nc += 1
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'licitacoes_reclassificadas': nl, 'contratos_reclassificados': nc})


@radar_bp.route('/precos')
@radar_login_required
def rota_precos():
    vencendo = request.args.get('vencendo', type=int)
    lst = listar_contratos(uf=request.args.get('uf'), busca=request.args.get('q'),
                           vencendo_em=vencendo, ordem=request.args.get('ordem', 'valor'),
                           limite=300)
    return render_template_string(_PRECOS, lst=lst, st=stats_contratos(),
                                  q=request.args.get('q', ''), vencendo=vencendo,
                                  token=request.args.get('t', ''))


@radar_bp.route('/admin')
@radar_admin_required
def rota_admin():
    st  = estatisticas()
    stc = stats_contratos()
    return render_template_string(_ADMIN, st=st, stc=stc, users=listar_radar_users())


@radar_bp.route('/')
@radar_login_required
def rota_painel():
    uf   = request.args.get('uf')
    zona = request.args.get('zona')
    lst  = listar_licitacoes(uf=uf, zona=zona,
                             ordem=request.args.get('ordem', 'score'), limite=300)
    st   = estatisticas()
    return render_template_string(_PAINEL, lst=lst, st=st, uf=uf or '', zona=zona or '',
                                  token=request.args.get('t', ''))


@radar_bp.route('/l/<path:pncp_id>')
@radar_login_required
def rota_detalhe(pncp_id):
    l = obter_licitacao(pncp_id)
    if not l:
        return 'Licitação não encontrada', 404
    analise = None
    if l.get('analise_json'):
        try: analise = _json.loads(l['analise_json'])
        except Exception: analise = None
    return render_template_string(_DETALHE, l=l, analise=analise, token=request.args.get('t', ''))


@radar_bp.route('/l/<path:pncp_id>/analisar', methods=['POST'])
@radar_login_required
def rota_analisar(pncp_id):
    l = obter_licitacao(pncp_id)
    if not l:
        return 'Licitação não encontrada', 404
    tok = request.args.get('t', '')
    sufixo = f'?t={tok}' if tok else ''
    texto = ''
    f = request.files.get('edital')
    if f and f.filename:
        try:
            texto = _texto_de_pdf(f.read())
        except Exception as e:
            log.warning(f'[RADAR] upload PDF falhou: {e}')
    try:
        analise, engine = analisar_edital(l, texto)
        salvar_analise(pncp_id, analise, engine)
    except Exception as e:
        log.error(f'[RADAR] análise falhou: {e}', exc_info=True)
        return render_template_string(_DETALHE, l=l, analise=None,
                                      erro=str(e), token=tok)
    return redirect(f'/radar/l/{pncp_id}{sufixo}')


# ── Lote 2: auto-coleta agendada (o Radar respira sozinho) ───────────────────
_COLETOR_INICIADO = False

def iniciar_coletor_automatico(intervalo_horas=None, delay_inicial_seg=120):
    """Thread daemon que coleta o PNCP periodicamente, sem clicar. Idempotente.
    Desligável via env RADAR_AUTO_COLETA=0. Intervalo via RADAR_COLETA_HORAS."""
    global _COLETOR_INICIADO
    if _COLETOR_INICIADO:
        return
    if os.environ.get('RADAR_AUTO_COLETA', '1') == '0':
        log.info('[RADAR] Auto-coleta desativada (RADAR_AUTO_COLETA=0)')
        return
    _COLETOR_INICIADO = True
    horas = intervalo_horas or int(os.environ.get('RADAR_COLETA_HORAS', '6'))

    import time as _time
    import threading as _threading
    import random as _random

    def _loop():
        # jitter no boot: com múltiplos workers no Railway, evita todos baterem
        # no PNCP no mesmo segundo (a coleta é idempotente, mas poupa requisições)
        _time.sleep(delay_inicial_seg + _random.randint(0, 120))
        while True:
            try:
                res = coletar()
                log.info(f'[RADAR] auto-coleta ok: {res}')
            except Exception as ex:
                log.error(f'[RADAR] auto-coleta erro: {ex}')
            try:
                resc = coletar_contratos()
                log.info(f'[RADAR] auto-coleta preços ok: {resc}')
            except Exception as ex:
                log.error(f'[RADAR] auto-coleta preços erro: {ex}')
            _time.sleep(horas * 3600)

    _threading.Thread(target=_loop, daemon=True, name='radar-coletor').start()
    log.info(f'[RADAR] Auto-coleta iniciada (a cada {horas}h)')


_DETALHE = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Licitação — Radar TI</title>
<style>
 body{font-family:system-ui,Segoe UI,sans-serif;background:#0b1020;color:#e7ecf5;margin:0;padding:22px;max-width:860px}
 a{color:#7cc0ff} .pill{padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700}
 .ouro{background:#2b2300;color:#ffd95e} .boa{background:#062a17;color:#5ee0a0}
 .dificil{background:#2a1a06;color:#ffb267} .nao{background:#2a0a0a;color:#ff8a8a} .indef{background:#1b2036;color:#9fb0d0}
 h1{font-size:19px;line-height:1.4} .row{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
 .box{background:#0f1730;border:1px solid #21304f;border-radius:10px;padding:12px 16px;flex:1;min-width:160px}
 .box span{color:#8aa0c6;font-size:12px;display:block} .box b{font-size:18px}
 .voltar{color:#8aa0c6;text-decoration:none;font-size:14px}
 .btn{display:inline-block;background:#1c3a2a;border:1px solid #2f5e44;color:#8ff0b8;
   padding:10px 16px;border-radius:8px;text-decoration:none;margin-top:8px}
</style></head><body>
{% set q = '?t=' ~ token if token else '' %}
<a class="voltar" href="/radar/{{ q }}">← voltar ao Radar</a>
<h1>{{ l.objeto }}</h1>
<div class="row">
 <div class="box"><span>Score</span><b>{{ l.score }}/100</b></div>
 <div class="box"><span>Porte</span><b><span class="pill {{ l.zona_valor }}">{{ l.zona_valor }}</span></b></div>
 <div class="box"><span>Valor estimado</span><b>{% if l.valor %}R$ {{ '{:,.2f}'.format(l.valor).replace(',','#').replace('.',',').replace('#','.') }}{% else %}—{% endif %}</b></div>
 <div class="box"><span>Tier</span><b>{{ 'T' ~ l.tier if l.tier else '—' }}</b></div>
</div>
<div class="row">
 <div class="box"><span>Modalidade</span><b style="font-size:14px">{{ l.modalidade or '—' }}</b></div>
 <div class="box"><span>Órgão</span><b style="font-size:14px">{{ l.orgao or '—' }}</b></div>
 <div class="box"><span>Local</span><b style="font-size:14px">{{ l.uf or '' }} {{ l.municipio or '' }}</b></div>
</div>
<div class="row">
 <div class="box"><span>Abertura</span><b style="font-size:14px">{{ (l.data_abertura or '—')[:16] }}</b></div>
 <div class="box"><span>Encerramento</span><b style="font-size:14px">{{ (l.data_encerramento or '—')[:16] }}</b></div>
 <div class="box"><span>Situação</span><b style="font-size:14px">{{ l.situacao or '—' }}</b></div>
</div>
{% if l.keywords_match %}<p>🔎 <b>Casou com:</b> {{ l.keywords_match }}</p>{% endif %}
<p style="color:#8aa0c6;font-size:13px">PNCP: {{ l.pncp_id }}</p>
{% if l.link %}<a class="btn" href="{{ l.link }}" target="_blank" rel="noopener">Abrir edital no sistema de origem →</a>{% endif %}

<hr style="border:none;border-top:1px solid #21304f;margin:26px 0">
{% if erro %}<p style="color:#ff8a8a">⚠️ A análise falhou: {{ erro }}</p>{% endif %}

{% if analise %}
{% set cor = {'sim':'#5ee0a0','talvez':'#ffd95e','nao':'#ff8a8a'}.get(analise.viavel,'#9fb0d0') %}
<h2 style="font-size:17px">🤖 Análise da IA <span style="font-size:12px;color:#5a6b8c">({{ l.analise_engine }})</span></h2>
<p style="font-size:18px;font-weight:800;color:{{ cor }}">{{ {'sim':'✅ VIÁVEL','talvez':'🟡 TALVEZ','nao':'❌ NÃO VALE'}.get(analise.viavel, analise.viavel) }}</p>
<p><b>{{ analise.veredito }}</b></p>
<p style="color:#cfe0ff">{{ analise.resumo }}</p>
<div class="row">
 <div class="box"><span>Exige atestado?</span><b style="font-size:15px">{{ analise.exige_atestado }}</b></div>
 <div class="box"><span>Exige garantia?</span><b style="font-size:15px">{{ analise.exige_garantia }}</b></div>
 <div class="box"><span>Dificuldade</span><b style="font-size:15px">{{ analise.dificuldade }}</b></div>
</div>
{% if analise.atestado_detalhe %}<p>📄 <b>Atestado:</b> {{ analise.atestado_detalhe }}</p>{% endif %}
{% if analise.riscos %}<p>⚠️ <b>Riscos:</b></p><ul>{% for r in analise.riscos %}<li>{{ r }}</li>{% endfor %}</ul>{% endif %}
{% if analise.habilitacao %}<p>📋 <b>Habilitação exigida:</b></p><ul>{% for h in analise.habilitacao %}<li>{{ h }}</li>{% endfor %}</ul>{% endif %}
{% if analise.plano %}<p>🗺️ <b>Plano de ação:</b></p><ol>{% for p in analise.plano %}<li>{{ p }}</li>{% endfor %}</ol>{% endif %}
<p style="color:#5a6b8c;font-size:12px">⚠️ Leitura automática por IA — confira no edital oficial antes de decidir.</p>
<form method="post" action="/radar/l/{{ l.pncp_id }}/analisar{% if token %}?t={{ token }}{% endif %}" enctype="multipart/form-data" style="margin-top:10px">
 <input type="file" name="edital" accept="application/pdf" style="color:#8aa0c6">
 <button class="btn" type="submit">🔄 Reanalisar (com PDF do edital)</button>
</form>
{% else %}
<h2 style="font-size:17px">🤖 Análise da IA</h2>
<p style="color:#8aa0c6">Deixe a IA ler este edital e dizer <b>se vale a pena</b>, se exige atestado, o prazo e o plano de ação.
 Anexe o PDF do edital (leitura completa) ou rode só com os metadados (leitura preliminar).</p>
<form method="post" action="/radar/l/{{ l.pncp_id }}/analisar{% if token %}?t={{ token }}{% endif %}" enctype="multipart/form-data">
 <input type="file" name="edital" accept="application/pdf" style="color:#8aa0c6">
 <button class="btn" type="submit">🤖 Analisar este edital</button>
</form>
{% endif %}
</body></html>'''


# ── Painel de inteligência de preço (Lote 5) ────────────────────────────────
_PRECOS = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>💰 Inteligência de Preço — Radar TI</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#0b1020;color:#e7ecf5;margin:0}
 header{padding:18px 22px;background:#11182e;border-bottom:1px solid #21304f;position:sticky;top:0}
 h1{margin:0;font-size:20px} .sub{color:#8aa0c6;font-size:13px;margin-top:4px}
 .stats{display:flex;gap:14px;flex-wrap:wrap;margin-top:12px}
 .card{background:#0f1730;border:1px solid #21304f;border-radius:10px;padding:10px 14px;min-width:120px}
 .card b{font-size:20px;display:block} .card span{color:#8aa0c6;font-size:12px}
 .filtros{padding:12px 22px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
 .filtros a,.filtros input{color:#cfe0ff;text-decoration:none;background:#152042;border:1px solid #2a3c63;
   padding:6px 12px;border-radius:20px;font-size:13px}
 .filtros input{min-width:200px}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #1b2742;vertical-align:top}
 th{color:#8aa0c6;font-weight:600} tr:hover{background:#0f1730}
 .obj{max-width:440px} .val{font-weight:800;color:#5ee0a0;white-space:nowrap}
 .forn{color:#cfe0ff} .muni{color:#8aa0c6;font-size:12px}
 .vence{font-weight:700} .vence.perto{color:#ffb267} .vence.urgente{color:#ff8a8a}
 .empty{padding:60px;text-align:center;color:#8aa0c6}
</style></head><body>
{% set q = '?t=' ~ token if token else '?' %}
<header>
 <h1>💰 Inteligência de Preço</h1>
 <div class="sub">Quem pagou quanto, em quê, e <b>quando vence</b> — pra você oferecer na cidade vizinha. 🎯</div>
 <div class="stats">
  <div class="card"><b>{{ st.total }}</b><span>contratos TI</span></div>
  <div class="card"><b>R$ {{ '{:,.0f}'.format(st.soma).replace(',','.') }}</b><span>movimentado</span></div>
  <div class="card"><b>{{ st.vencendo90 }}</b><span>vencem em 90 dias</span></div>
 </div>
</header>
<div class="filtros">
 <a href="/radar/precos{{ q }}">Todos</a>
 <a href="/radar/precos{{ q }}&vencendo=90">⏰ Vencem em 90d</a>
 <a href="/radar/precos{{ q }}&vencendo=180">Vencem em 180d</a>
 <a href="/radar/precos{{ q }}&ordem=vence">Por vencimento</a>
 <a href="/radar/{{ q }}">← voltar ao Radar</a>
 <a href="/radar/coletar-precos{{ q }}" style="background:#1c3a2a;border-color:#2f5e44;color:#8ff0b8">▶ Coletar preços</a>
 <form method="get" action="/radar/precos" style="display:inline">
   {% if token %}<input type="hidden" name="t" value="{{ token }}">{% endif %}
   <input type="text" name="q" placeholder="buscar objeto/órgão/fornecedor" value="{{ q if false else request.args.get('q','') }}">
 </form>
</div>
{% if lst %}
<table>
 <tr><th class="obj">Objeto</th><th>Valor</th><th>Fornecedor (quem ganhou)</th>
     <th>Órgão / Local</th><th>Vence em</th></tr>
 {% for c in lst %}
 <tr>
  <td class="obj">{% if c.link %}<a href="{{ c.link }}" target="_blank" rel="noopener" style="color:#e7ecf5;text-decoration:none">{{ c.objeto[:140] }}</a>{% else %}{{ c.objeto[:140] }}{% endif %}</td>
  <td class="val">{% if c.valor %}R$ {{ '{:,.0f}'.format(c.valor).replace(',','.') }}{% else %}—{% endif %}</td>
  <td class="forn">{{ (c.fornecedor or '—')[:40] }}</td>
  <td><span style="font-weight:700">{{ c.uf or '' }}</span> <span class="muni">{{ c.municipio or '' }}</span>
      <div class="muni">{{ (c.orgao or '')[:46] }}</div></td>
  <td class="vence">{{ (c.vigencia_fim or '—')[:10] }}</td>
 </tr>
 {% endfor %}
</table>
{% else %}
<div class="empty">Nenhum contrato ainda.<br><br>
 Clique em <b>▶ Coletar preços</b> (precisa de internet) pra puxar os contratos de TI do PNCP.</div>
{% endif %}
</body></html>'''


# ── Login / Cadastro / Senha (1 template, vários modos) ─────────────────────
_AUTH = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>📡 Radar de Licitações de TI — Acesso</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#0b1020;color:#e7ecf5;margin:0;
   display:flex;min-height:100vh;align-items:center;justify-content:center;padding:20px}
 .card{background:#0f1730;border:1px solid #21304f;border-radius:16px;padding:32px;max-width:380px;width:100%}
 h1{font-size:22px;margin:0 0 4px} .sub{color:#8aa0c6;font-size:13px;margin-bottom:20px}
 label{display:block;font-size:13px;color:#8aa0c6;margin:12px 0 4px}
 input{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:8px;border:1px solid #2a3c63;
   background:#0b1020;color:#e7ecf5;font-size:15px}
 button{width:100%;margin-top:18px;padding:12px;border:none;border-radius:8px;background:#2563eb;
   color:#fff;font-size:15px;font-weight:700;cursor:pointer}
 .erro{background:#2a0a0a;color:#ff8a8a;padding:10px;border-radius:8px;font-size:13px;margin-bottom:8px}
 .ok{background:#062a17;color:#5ee0a0;padding:10px;border-radius:8px;font-size:13px;margin-bottom:8px}
 .links{margin-top:16px;font-size:13px;text-align:center} a{color:#7cc0ff;text-decoration:none}
</style></head><body>
<div class="card">
 <h1>📡 Radar de Licitações de TI</h1>
 <div class="sub">Licitações de tecnologia do Brasil, filtradas pra você.</div>
 {% if erro %}<div class="erro">⚠️ {{ erro }}</div>{% endif %}
 {% if msg %}<div class="ok">✅ {{ msg }}</div>{% endif %}
 {% if modo == 'cadastrar' %}
 <form method="post" action="/radar/cadastrar">
  <label>Nome</label><input name="nome" required>
  <label>E-mail</label><input type="email" name="email" required>
  <label>Telefone (opcional)</label><input name="telefone">
  <label>Senha (mín. 6)</label><input type="password" name="senha" required>
  <button>Criar conta grátis</button>
 </form>
 <div class="links">Já tem conta? <a href="/radar/entrar">Entrar</a></div>
 {% elif modo == 'esqueci' %}
 <form method="post" action="/radar/esqueci-senha">
  <label>Seu e-mail</label><input type="email" name="email" required>
  <button>Enviar link de redefinição</button>
 </form>
 <div class="links"><a href="/radar/entrar">← voltar ao login</a></div>
 {% elif modo == 'redefinir' %}
 <form method="post" action="/radar/redefinir-senha">
  <input type="hidden" name="token" value="{{ token }}">
  <label>Nova senha (mín. 6)</label><input type="password" name="senha" required>
  <button>Salvar nova senha</button>
 </form>
 {% else %}
 <form method="post" action="/radar/entrar">
  <label>E-mail</label><input type="email" name="email" required>
  <label>Senha</label><input type="password" name="senha" required>
  <button>Entrar</button>
 </form>
 <div style="text-align:center;margin:18px 0 6px;color:#8aa0c6;font-size:13px">— ainda não tem conta? —</div>
 <a href="/radar/cadastrar"><button type="button" style="background:#152042;border:1px solid #2a3c63;color:#7cc0ff;margin-top:0">✨ Criar conta grátis</button></a>
 <div class="links"><a href="/radar/esqueci-senha">Esqueci a senha</a></div>
 {% endif %}
</div>
</body></html>'''


# ── Tela ADMIN do Radar (confere tudo) ──────────────────────────────────────
_ADMIN = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🛠️ Admin — Radar de Licitações</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#0b1020;color:#e7ecf5;margin:0;padding:22px}
 h1{font-size:20px} h2{font-size:16px;color:#8aa0c6;margin-top:26px}
 .stats{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
 .card{background:#0f1730;border:1px solid #21304f;border-radius:10px;padding:12px 16px;min-width:120px}
 .card b{font-size:22px;display:block} .card span{color:#8aa0c6;font-size:12px}
 .btns{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 8px}
 .btn{background:#1c3a2a;border:1px solid #2f5e44;color:#8ff0b8;padding:8px 14px;border-radius:8px;
   text-decoration:none;font-size:13px} .btn.b2{background:#152042;border-color:#2a3c63;color:#cfe0ff}
 table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
 th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #1b2742}
 th{color:#8aa0c6} .adm{color:#ffd95e;font-weight:700} a{color:#7cc0ff;text-decoration:none}
</style></head><body>
<h1>🛠️ Admin — Radar de Licitações de TI</h1>
<a href="/radar/" style="color:#8aa0c6;font-size:14px">← painel</a> ·
<a href="/radar/precos" style="color:#8aa0c6;font-size:14px">inteligência de preço</a> ·
<a href="/radar/sair" style="color:#8aa0c6;font-size:14px">sair</a>

<h2>📊 Coleta</h2>
<div class="stats">
 <div class="card"><b>{{ st.total }}</b><span>licitações coletadas</span></div>
 <div class="card"><b>{{ st.ti }}</b><span>são TI</span></div>
 <div class="card"><b>{{ st.ouro }}</b><span>zona ouro ≤65k</span></div>
 <div class="card"><b>{{ stc.total }}</b><span>contratos TI (preço)</span></div>
 <div class="card"><b>{{ stc.vencendo90 }}</b><span>contratos vencendo 90d</span></div>
</div>
<div class="btns">
 <a class="btn" href="/radar/coletar">▶ Coletar licitações</a>
 <a class="btn" href="/radar/coletar-precos">▶ Coletar preços</a>
 <a class="btn b2" href="/radar/reclassificar">♻️ Reclassificar (regras novas)</a>
 <a class="btn b2" href="/radar/stats">ver stats (JSON)</a>
</div>
{% if st.ultima_coleta %}<p style="color:#8aa0c6;font-size:13px">Última coleta: +{{ st.ultima_coleta.novos }} novos / {{ st.ultima_coleta.atualizados }} atualizados</p>{% endif %}

<h2>👥 Usuários ({{ users|length }})</h2>
<table>
 <tr><th>#</th><th>Nome</th><th>E-mail</th><th>Telefone</th><th>Plano</th><th>Cadastro</th><th>Último acesso</th></tr>
 {% for u in users %}
 <tr>
  <td>{{ u.id }}</td>
  <td>{{ u.nome }}{% if u.is_admin %} <span class="adm">★admin</span>{% endif %}</td>
  <td>{{ u.email }}</td>
  <td>{{ u.telefone or '—' }}</td>
  <td>{{ 'ativo' if u.plan_active else u.plano }}</td>
  <td>{{ (u.created_at or '')[:10] }}</td>
  <td>{{ (u.ultimo_acesso or '—')[:16] }}</td>
 </tr>
 {% endfor %}
</table>
</body></html>'''


# ── Painel inline (Lote 3 vira template bonito; aqui é o dogfood funcional) ──
_PAINEL = '''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>📡 Radar de Licitações de TI</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#0b1020;color:#e7ecf5;margin:0}
 header{padding:18px 22px;background:#11182e;border-bottom:1px solid #21304f;position:sticky;top:0}
 h1{margin:0;font-size:20px} .sub{color:#8aa0c6;font-size:13px;margin-top:4px}
 .stats{display:flex;gap:14px;flex-wrap:wrap;margin-top:12px}
 .card{background:#0f1730;border:1px solid #21304f;border-radius:10px;padding:10px 14px;min-width:110px}
 .card b{font-size:22px;display:block} .card span{color:#8aa0c6;font-size:12px}
 .filtros{padding:12px 22px;display:flex;gap:8px;flex-wrap:wrap}
 .filtros a{color:#cfe0ff;text-decoration:none;background:#152042;border:1px solid #2a3c63;
   padding:6px 12px;border-radius:20px;font-size:13px}
 .filtros a:hover{background:#1d2c57}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #1b2742;vertical-align:top}
 th{color:#8aa0c6;font-weight:600;position:sticky;top:0;background:#0b1020}
 tr:hover{background:#0f1730}
 .obj{max-width:520px} .obj a{color:#e7ecf5;text-decoration:none}
 .pill{padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700;white-space:nowrap}
 .ouro{background:#2b2300;color:#ffd95e} .boa{background:#062a17;color:#5ee0a0}
 .dificil{background:#2a1a06;color:#ffb267} .nao{background:#2a0a0a;color:#ff8a8a}
 .indef{background:#1b2036;color:#9fb0d0}
 .t1{background:#10233f;color:#7cc0ff} .t2{background:#1a1f33;color:#b7c2e0}
 .sc{font-weight:800} .muni{color:#8aa0c6}
 .empty{padding:60px;text-align:center;color:#8aa0c6}
</style></head><body>
<header>
 <h1>📡 Radar de Licitações de TI</h1>
 <div class="sub">Serviços médio/pequenos de TI — cauda longa municipal. Nada "Microsoft". 😉</div>
 <div class="stats">
  <div class="card"><b>{{ st.total }}</b><span>coletadas</span></div>
  <div class="card"><b>{{ st.ti }}</b><span>são TI</span></div>
  <div class="card"><b>{{ st.ouro }}</b><span>zona ouro ≤65k</span></div>
  {% if st.ultima_coleta %}<div class="card"><b>+{{ st.ultima_coleta.novos }}</b><span>última coleta</span></div>{% endif %}
 </div>
</header>
{% set q = '?t=' ~ token if token else '?' %}
<div class="filtros">
 <a href="/radar/{{ q }}">🌐 Brasil</a>
 <a href="/radar/{{ q }}&uf=SC">🟢 SC</a>
 <a href="/radar/{{ q }}&zona=ouro">⭐ Zona Ouro</a>
 <a href="/radar/{{ q }}&zona=boa">Zona Boa</a>
 <a href="/radar/{{ q }}&ordem=prazo">⏰ Por prazo</a>
 <a href="/radar/precos{{ q }}" style="background:#2a2300;border-color:#5e5230;color:#ffd95e">💰 Inteligência de Preço</a>
 {% if radar_is_admin %}
 <a href="/radar/coletar{{ q }}" style="background:#1c3a2a;border-color:#2f5e44;color:#8ff0b8">▶ Coletar agora</a>
 <a href="/radar/admin" style="background:#231a3a;border-color:#463a6e;color:#c7b3ff">🛠️ Admin</a>
 {% endif %}
 <span style="margin-left:auto;color:#8aa0c6;font-size:13px">👤 {{ radar_nome }} · <a href="/radar/sair" style="color:#8aa0c6">sair</a></span>
</div>
{% if lst %}
<table>
 <tr><th>Score</th><th>Porte</th><th>Tier</th><th class="obj">Objeto</th>
     <th>Valor</th><th>Órgão / Local</th><th>Prazo</th></tr>
 {% for l in lst %}
 <tr>
  <td class="sc"><a href="/radar/l/{{ l.pncp_id }}{% if token %}?t={{ token }}{% endif %}" style="color:#7cc0ff;text-decoration:none">{{ l.score }}</a></td>
  <td><span class="pill {{ l.zona_valor }}">{{ l.zona_valor }}</span></td>
  <td>{% if l.tier %}<span class="pill t{{ l.tier }}">T{{ l.tier }}</span>{% endif %}</td>
  <td class="obj">{% if l.link %}<a href="{{ l.link }}" target="_blank" rel="noopener">{{ l.objeto[:160] }}</a>
      {% else %}{{ l.objeto[:160] }}{% endif %}
      {% if l.keywords_match %}<div class="muni" style="font-size:11px">🔎 {{ l.keywords_match }}</div>{% endif %}</td>
  <td>{% if l.valor %}R$ {{ '{:,.0f}'.format(l.valor).replace(',','.') }}{% else %}—{% endif %}</td>
  <td><span class="sc">{{ l.uf or '' }}</span> <span class="muni">{{ l.municipio or '' }}</span>
      <div class="muni" style="font-size:11px">{{ (l.orgao or '')[:50] }}</div></td>
  <td>{{ (l.data_encerramento or '')[:10] }}</td>
 </tr>
 {% endfor %}
</table>
{% else %}
<div class="empty">Nenhuma licitação ainda.<br><br>
 Clique em <b>▶ Coletar agora</b> (precisa de internet) pra puxar do PNCP.</div>
{% endif %}
</body></html>'''
