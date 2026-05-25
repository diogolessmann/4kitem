"""
petmed.py — Blueprint PETmed
Triagem veterinária inteligente 24/7
"""
import json
import os
import random
import re
from datetime import datetime
from functools import wraps
from flask import (Blueprint, render_template, redirect, request,
                   session, jsonify, url_for, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from petmed_db import get_petmed_db, init_petmed_db

try:
    from groq import Groq as _Groq
    _groq_client = _Groq(api_key=os.environ.get('GROQ_API_KEY', ''))
except Exception:
    _groq_client = None

petmed_bp = Blueprint('petmed', __name__, url_prefix='/petmed')

# ── Planos ─────────────────────────────────────────────────────────────────────
PLANOS = {
    'start': {
        'nome': 'PET Start',
        'preco': 34.90,
        'preco_fmt': 'R$ 34,90',
        'cor': '#0ea5e9',
        'emoji': '🐾',
        'descricao': 'Para quem tem 1 pet',
        'pets': 1,
        'teleconsulta': False,
        'lembretes': False,
        'mapa': False,
        'features': [
            'Triagens ilimitadas 24/7',
            '1 pet cadastrado',
            'Identificação de raça por foto',
            'Classificação de urgência',
            'Orientações pós-triagem',
            'Histórico (30 dias)',
        ],
    },
    'familia': {
        'nome': 'PET Família',
        'preco': 59.90,
        'preco_fmt': 'R$ 59,90',
        'cor': '#f97316',
        'emoji': '🐾🐾',
        'descricao': 'Para famílias com mais pets',
        'pets': 4,
        'teleconsulta': False,
        'lembretes': True,
        'mapa': True,
        'destaque': True,
        'features': [
            'Tudo do Start',
            'Até 4 pets cadastrados',
            'Histórico completo',
            'Cartão de vacinas digital',
            'Lembretes automáticos',
            'Mapa de clínicas abertas',
            'Prioridade no atendimento',
        ],
    },
    'premium': {
        'nome': 'PET Premium',
        'preco': 99.90,
        'preco_fmt': 'R$ 99,90',
        'cor': '#8b5cf6',
        'emoji': '👑',
        'descricao': 'Proteção total',
        'pets': 999,
        'teleconsulta': True,
        'lembretes': True,
        'mapa': True,
        'features': [
            'Tudo do Família',
            'Pets ilimitados',
            '1 teleconsulta/mês incluída',
            'Consultas adicionais R$ 39,90',
            'Relatório mensal de saúde',
            'Suporte prioritário',
            'Desconto em clínicas parceiras',
        ],
    },
}

LIMITE_PETS = {'start': 1, 'familia': 4, 'premium': 999}

# ── Categorias de sintomas ──────────────────────────────────────────────────────
CATEGORIAS = {
    'digestivo':     {'emoji': '🤢', 'label': 'Vômito / Diarreia / Sem apetite'},
    'respiratorio':  {'emoji': '😮‍💨', 'label': 'Tosse / Falta de ar / Espirros'},
    'neurologico':   {'emoji': '⚡', 'label': 'Convulsão / Tremores / Desorientação'},
    'trauma':        {'emoji': '🩹', 'label': 'Ferimento / Queda / Acidente'},
    'urinario':      {'emoji': '🚿', 'label': 'Dificuldade para urinar / Sangue'},
    'comportamento': {'emoji': '😴', 'label': 'Letargia / Apatia / Tristeza'},
    'pele':          {'emoji': '🐛', 'label': 'Coceira / Feridas / Pelos caindo / Alopecia'},
    'ocular':        {'emoji': '👁️', 'label': 'Olho vermelho / Secreção / Orelha'},
    'outro':         {'emoji': '❓', 'label': 'Outro sintoma'},
}


# ── Decoradores ────────────────────────────────────────────────────────────────
def petmed_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('pm_user_id'):
            return redirect('/petmed/entrar')
        return f(*args, **kwargs)
    return decorated


def petmed_premium_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('pm_user_id'):
            return redirect('/petmed/entrar')
        if session.get('pm_plano') != 'premium':
            return redirect('/petmed/planos?msg=premium')
        return f(*args, **kwargs)
    return decorated


# ── Helpers ────────────────────────────────────────────────────────────────────
def _get_user():
    uid = session.get('pm_user_id')
    if not uid:
        return None
    conn = get_petmed_db()
    u = conn.execute('SELECT * FROM petmed_users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return u


def _get_pets(user_id):
    conn = get_petmed_db()
    pets = conn.execute(
        'SELECT * FROM petmed_pets WHERE user_id=? ORDER BY nome', (user_id,)
    ).fetchall()
    conn.close()
    return pets


def _can_add_pet(user_id, plano):
    conn = get_petmed_db()
    total = conn.execute(
        'SELECT COUNT(*) FROM petmed_pets WHERE user_id=?', (user_id,)
    ).fetchone()[0]
    conn.close()
    limite = LIMITE_PETS.get(plano, 1)
    return total < limite, total, limite


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _triagens_usadas(user_id):
    """Retorna quantas triagens o usuário já realizou."""
    conn = get_petmed_db()
    n = conn.execute(
        'SELECT COUNT(*) FROM petmed_triagens WHERE user_id=?', (user_id,)
    ).fetchone()[0]
    conn.close()
    return n


def _check_paywall(u):
    """
    Verifica se o usuário pode realizar mais triagens.
    Retorna (bloqueado: bool, triagens_usadas: int).
    - plano_ativo=1  → assinatura ativa, sem bloqueio.
    - plano_ativo=0  → plano gratuito: 1 triagem grátis, depois bloqueado.
    """
    if u['plano_ativo']:
        return False, _triagens_usadas(u['id'])
    usadas = _triagens_usadas(u['id'])
    return usadas >= 1, usadas


# ── IA: identificar raça por foto ──────────────────────────────────────────────
def _identificar_raca(foto_base64: str, especie: str) -> str:
    if not _groq_client or not foto_base64:
        return 'Não identificada'
    try:
        tipo = 'cão' if especie == 'cao' else 'gato'
        resp = _groq_client.chat.completions.create(
            model='meta-llama/llama-4-scout-17b-16e-instruct',
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image_url',
                        'image_url': {'url': f'data:image/jpeg;base64,{foto_base64}'}
                    },
                    {
                        'type': 'text',
                        'text': (
                            f'Identifique a raça deste {tipo} na foto. '
                            'Responda SOMENTE com o nome da raça, sem explicações. '
                            'Exemplos: "Golden Retriever", "Labrador", "SRD (Sem Raça Definida)", '
                            '"Poodle", "Bulldog Francês", "Persa", "Siamês". '
                            'Se não conseguir identificar, responda "SRD".'
                        )
                    }
                ]
            }],
            max_tokens=50,
            temperature=0.1,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return 'Não identificada'


# ── IA: triagem inteligente ─────────────────────────────────────────────────────
def _fazer_triagem(pet_info: dict, categoria: str, historico: list) -> dict:
    """
    Processa a conversa de triagem e retorna próxima pergunta ou resultado final.
    Retorna: {
        'tipo': 'pergunta' | 'resultado',
        'mensagem': str,
        'resultado': 'estavel' | 'atencao' | 'urgente',
        'orientacoes': str,
        'encaminhar': bool
    }
    """
    if not _groq_client:
        return {
            'tipo': 'resultado',
            'resultado': 'atencao',
            'mensagem': 'Serviço temporariamente indisponível.',
            'orientacoes': 'Recomendamos contato com uma clínica veterinária.',
            'encaminhar': True
        }

    categoria_info = CATEGORIAS.get(categoria, {'label': categoria})
    especie = 'cão' if pet_info.get('especie') == 'cao' else 'gato'
    raca = pet_info.get('raca', 'SRD')
    nome = pet_info.get('nome', 'Pet')
    idade = pet_info.get('idade_anos', '?')
    peso = pet_info.get('peso_kg', '?')

    # Busca histórico recente do pet para contexto da IA
    historico_pet_ctx = ''
    pet_id = pet_info.get('id')
    if pet_id:
        try:
            conn_h = get_petmed_db()
            triagens_ant = conn_h.execute(
                '''SELECT resultado, categoria, orientacoes, created_at
                   FROM petmed_triagens WHERE pet_id=?
                   ORDER BY created_at DESC LIMIT 3''',
                (pet_id,)
            ).fetchall()
            conn_h.close()
            if triagens_ant:
                historico_pet_ctx = '\nHISTÓRICO RECENTE DO PET (últimas triagens):\n'
                for t in triagens_ant:
                    historico_pet_ctx += f"• {t['created_at'][:10]} — {t['categoria']} → {t['resultado'].upper()}\n"
        except Exception:
            pass

    # Define porte do pet para calibrar orientações
    try:
        peso_num = float(peso) if peso and peso != '?' else None
    except (ValueError, TypeError):
        peso_num = None

    if especie == 'gato':
        porte = 'gato'
    elif peso_num is None:
        porte = 'porte desconhecido'
    elif peso_num <= 5:
        porte = 'porte pequeno'
    elif peso_num <= 15:
        porte = 'porte médio'
    else:
        porte = 'porte grande'

    peso_info = f"{peso}kg ({porte})" if peso and peso != '?' else f"peso não informado ({porte})"

    # Dados adicionais do pet para o sistema
    sexo     = pet_info.get('sexo', 'nao_informado')
    castrado = pet_info.get('castrado', 0)
    castrado_txt = 'castrado(a)' if castrado else 'não castrado(a)'
    sexo_txt = {'macho': 'macho', 'femea': 'fêmea'}.get(sexo, 'sexo não informado')
    obs_pet  = pet_info.get('observacoes', '') or ''

    # Alerta raça específico
    RACAS_RISCO = {
        # Respiratório braquicefálico
        'bulldog': '⚠️ RAÇA BRAQUICEFÁLICA: muito predisposta a síndrome obstrutiva das vias aéreas — qualquer dispneia é potencialmente grave.',
        'bulldog inglês': '⚠️ RAÇA BRAQUICEFÁLICA: síndrome braquicefálica — dispneia pode ser mais severa do que parece.',
        'bulldog francês': '⚠️ RAÇA BRAQUICEFÁLICA: síndrome braquicefálica — dispneia, ronco e esforço respiratório exigem atenção redobrada.',
        'pug': '⚠️ RAÇA BRAQUICEFÁLICA: síndrome braquicefálica + prolapso de córnea — olhos proeminentes exigem cuidado especial.',
        'shih tzu': '⚠️ RAÇA BRAQUICEFÁLICA + olhos proeminentes. Problemas oculares e respiratórios são comuns.',
        'boxer': '⚠️ Boxer: alta incidência de tumores (mastocitoma, linfoma) — qualquer nódulo novo merece avaliação prioritária.',
        # Coluna
        'dachshund': '⚠️ DACHS: raça de alto risco para DPIV (doença do disco intervertebral) — qualquer dor nas costas, paralisia ou paresia de membros é urgência.',
        'basset hound': '⚠️ Basset: predisposto a DPIV e otite crônica.',
        'corgi': '⚠️ Corgi: predisposto a DPIV por conformação corporal.',
        # Displasia / ortopédico
        'labrador': '⚠️ Labrador: alta incidência de displasia de quadril e cotovelo + obesidade. Claudicação exige avaliação.',
        'golden retriever': '⚠️ Golden: displasia de quadril, lipomas e tumores são comuns. Claudicação e nódulos merecem atenção.',
        'pastor alemão': '⚠️ Pastor Alemão: displasia de quadril + degeneração mielopatia progressiva — claudicação e fraqueza dos membros posteriores são sinais sérios.',
        # Cardíaco
        'cavalier king charles': '⚠️ Cavalier: altíssima predisposição a doença cardíaca (DMVM) — qualquer tosse crônica ou intolerância ao exercício pode ser cardíaca.',
        'yorkshire': '⚠️ Yorkshire: predisposto a colapso de traqueia — tosse em ganso é clássica.',
        'maltês': '⚠️ Maltês: predisposto a shunt portossistêmico e colapso de traqueia.',
        # Renal / urológico
        'persa': '⚠️ Persa: altíssimo risco de doença renal policística (PKD) e problemas urinários.',
        'maine coon': '⚠️ Maine Coon: predisposto a cardiomiopatia hipertrófica (HCM) — monitorar sinais cardíacos.',
        'ragdoll': '⚠️ Ragdoll: predisposto a HCM.',
        # Ocular
        'cocker spaniel': '⚠️ Cocker: muito predisposto a otite crônica + glaucoma — olho vermelho pode ser glaucoma (urgência).',
        'husky siberiano': '⚠️ Husky: predisposto a olho azul (uveíte) e problemas oculares hereditários.',
        # Neurológico
        'dobermann': '⚠️ Dobermann: predisposto a cardiomiopatia dilatada — síncope ou fraqueza pode ser cardíaca.',
        'são bernardo': '⚠️ Porte gigante: torção gástrica (GDV) é risco real após refeições — abdômen distendido = urgência máxima.',
        'great dane': '⚠️ Porte gigante: torção gástrica (GDV) — nunca exercitar após comer.',
        'rottweiler': '⚠️ Rottweiler: osteossarcoma é mais comum — claudicação progressiva em adulto/idoso merece avaliação urgente.',
    }
    alerta_raca = ''
    raca_lower = raca.lower() if raca else ''
    for k, v in RACAS_RISCO.items():
        if k in raca_lower:
            alerta_raca = f'\nALERTA ESPECÍFICO DA RAÇA: {v}'
            break

    system_prompt = f"""Você é um especialista veterinário de referência do VetZap com profundo conhecimento em medicina veterinária integrativa — domina clínica médica de pequenos animais, medicina integrativa, fitoterapia veterinária, nutrição animal, comportamento e medicina de suporte domiciliar para cães e gatos.

PACIENTE: {nome} | {especie} | raça {raca} | {idade} anos | {peso_info} | {sexo_txt} | {castrado_txt}
QUEIXA PRINCIPAL: {categoria_info['label']}{alerta_raca}
{f'OBSERVAÇÕES DO TUTOR SOBRE O PET: {obs_pet}' if obs_pet else ''}{historico_pet_ctx}

MISSÃO: Ser o melhor suporte veterinário pré-consulta disponível — como um especialista de confiança que trata o pet como se fosse o seu. Orientações práticas, precisas, acolhedoras e profundamente fundamentadas na medicina veterinária moderna e integrativa.

REGRAS FUNDAMENTAIS:
1. TRIAGEM + ORIENTAÇÃO DE SUPORTE — nunca diagnóstico definitivo, nunca prescrição médica.
2. Linguagem empática, clara e objetiva. O tutor está preocupado — acolha primeiro, oriente depois.
3. UMA pergunta por vez. Colete 4-6 respostas antes de concluir.
4. Ao concluir: responda SOMENTE em JSON válido.
5. NUNCA cite antibióticos, anti-inflamatórios, corticoides, antiparasitários ou qualquer medicamento prescrito.
6. CONSIDERE sempre: raça, sexo, castração, idade e peso em TODAS as orientações.

CLASSIFICAÇÕES:
• "urgente" → ir ao veterinário AGORA. (convulsão ativa, dispneia severa, inconsciência, sangramento abundante, intoxicação, trauma grave, abdômen rígido/distendido, mucosas azuladas ou pálidas, colapso, corpo estranho engasgado, suspeita de obstrução urinária em gato macho)
• "atencao" → veterinário em até 24h. (vômito 3x+ sem melhora, diarreia com sangue, febre, letargia moderada, ferimento aberto, olho com secreção abundante, dificuldade para urinar com dor, prostração, anorexia >24h)
• "estavel" → pode aguardar consulta de rotina. (episódio único e leve, coceira sem lesão ativa, inapetência pontual, comportamental leve, eliminações normais)

═══════════════════════════════════════════════
BANCO DE CONHECIMENTO VETERINÁRIO INTEGRADO
═══════════════════════════════════════════════

🤢 DIGESTIVO — VÔMITO/DIARREIA:

Vômito leve (1-2 episódios, animal alerta e hidratado):
• Jejum hídrico 1-2h; depois ofereça água em pequenas quantidades:
  Porte pequeno: 1-2 col sopa a cada 20 min | Médio: 3-4 col | Grande: 5-6 col
• Após 4h sem vômito → dieta branda: frango cozido sem pele/osso/sal/tempero + arroz branco (proporção 1:2)
  Pequeno: 2-3 col sopa/refeição, 3-4x dia | Médio: 5-7 col, 3x | Grande: 10-14 col, 3x
• Chá de camomila (SOMENTE CÃES ≥6 meses): prepare concentrado, deixe esfriar até morno. Propriedades antiespasmódicas e anti-inflamatórias no TGI.
  Pequeno: 30-50ml | Médio: 80-120ml | Grande: 150-200ml — ofereça após o jejum hídrico
• Caldo de frango caseiro (sem sal, sem cebola, sem alho, sem tempero): estimula hidratação se recusar água
• Gengibre fresco ralado (SOMENTE CÃES, dose mínima): meia colher de café diluída em água — propriedade antiemética leve. NUNCA para gatos.
• Probiótico para pets (Floravet, Fortbac — sem receita): restaura flora intestinal, ofereça junto à dieta branda
• Reidratante para pets (PetOral ou similar): especialmente se vômito >2x — sem receita em pet shops

Diarreia leve (sem sangue, sem muco, animal ativo):
• Dieta branda igual ao vômito por 24-48h
• Batata-doce cozida sem casca (fibra solúvel firma as fezes): Pequeno: 1-2 col | Médio: 3-4 col | Grande: 5-6 col — misture à refeição
• Abóbora cabotiá cozida sem tempero: efeito similar, palatável, bem tolerada por todas as raças
• Chá de hortelã-pimenta morno (SOMENTE CÃES): auxilia na regulação intestinal. Pequeno: 20-30ml | Médio: 50ml | Grande: 80ml. NÃO para gatos (mentol tóxico para felinos).
• Probiótico para pets — siga embalagem para o porte
• Monitorar hidratação: Pequeno: mín. 35ml água/kg/dia | Médio: 45ml/kg | Grande: 55ml/kg

Inapetência isolada (sem outros sintomas):
• Tente: frango cozido morno (aroma estimula apetite), atum em água natural (especialmente gatos), patê úmido de qualidade aquecido por 10-15seg
• Gatos: aquecimento do alimento libera aroma — fundamental para estimular
• Ambiente calmo e tranquilo durante a refeição (estresse reduz apetite em até 40%)
• ⚠️ Gatos com anorexia >48h: risco de lipidose hepática felina — busque vet sem demora

🐾 QUEDA DE PELO / ALOPECIA:

PRIMEIRAMENTE — diferencie troca de pelo normal de queda patológica:
→ NORMAL (troca sazonal): ocorre principalmente primavera/outono, pelo de guarda sendo substituído, pelagem inteira e uniforme, pele normal embaixo, sem coceira excessiva. Raças de duplo pelame (Husky, Golden, Labrador, Pastor Alemão) têm mudança de pelagem muito intensa — pode parecer assustador mas é natural.
→ PATOLÓGICA: regiões sem pelo (placas/manchas), pele vermelha/escamosa/com crostas embaixo, coceira intensa associada, pelo quebrando (não caindo inteiro), queda assimétrica, + outros sintomas (ganho de peso, letargia, sede excessiva → suspeita de hipotireoidismo ou Cushing).

PERGUNTAS DE TRIAGEM para queda de pelo:
• Há quanto tempo está acontecendo?
• A queda é difusa (pelo todo) ou em manchas/regiões específicas?
• Tem coceira intensa associada?
• O que o pet come? Qual marca/tipo de ração ou dieta? Há quanto tempo com essa alimentação?
• A pele embaixo do pelo está normal ou tem vermelhidão, descamação, crostas?
• Há mudança de estação climática recente? (primavera/outono)
• O pet saiu de um período de estresse? (mudança de casa, novos animais, etc.)

ORIENTAÇÕES NUTRICIONAIS PARA PELAGEM:
• Ômega-3: principal nutriente para saúde do pelame e pele. Fontes naturais:
  - Sardinha em água natural (sem sal): Porte pequeno: ½ sardinha 2-3x/sem | Médio: 1 sardinha | Grande: 2 sardinhas
  - Salmão cozido sem sal/tempero: excelente fonte, ofereça 1-2x/sem
  - Óleo de salmão para pets (sem receita, pet shops): siga embalagem conforme o peso
  - Óleo de linhaça: alternativa vegetal, mas menor biodisponibilidade
• Ovo cozido (inteiro, sem sal): rico em biotina, excelente para pelo e unhas. 1-2 ovos/sem conforme porte. Sempre cozido — clara crua bloqueia absorção de biotina
• Batata-doce cozida: vitamina A + fibras, auxilia na renovação da pele
• Cenoura crua ralada: betacaroteno, favorece brilho e saúde da pele
• Abóbora cozida: vitaminas B + E, favorece ciclos de crescimento do pelo

ATENÇÃO À RAÇÃO:
• Rações com baixa qualidade proteica: pelo opaco, quebradiço, queda excessiva
• Alergias alimentares: podem causar dermatite + queda focal de pelo — ingredientes mais comuns: frango, soja, trigo, milho
• Troca abrupta de ração: pode desencadear queda temporária — sempre faça transição gradual em 7-10 dias
• Rações grain-free: atenção ao histórico — avaliar com vet se for raça predisposta a DCM

SUPLEMENTOS DE SUPORTE (sem receita):
• Suplementos de ômega-3 específicos para pets (PetOmega, OmegaPet ou similares)
• Suplemento de biotina para pets (pet shops, sem receita)
• Levedura de cerveja: rico em complexo B + zinco, melhora pelagem — Pequeno: ½ col chá | Médio: 1 col | Grande: 2 col — junto com a ração

🐛 PELE — COCEIRA/DERMATITE/FERIDAS:

Coceira generalizada ou pontual:
• Banho com shampoo hipoalergênico para pets: deixe agir 5-8 min, enxágue completamente
• Compresa de chá de camomila frio: embeba gaze no chá frio e aplique na região por 5-10 min — anti-inflamatório e calmante tópico eficaz
• Banho de aveia coloidal: dissolva 2-3 col sopa de farinha de aveia grossa em 1L de água morna, use como enxágue final sem retirar — excelente para coceiras generalizadas e pele seca
• Aloe vera gel puro (sem álcool, sem perfume, sem corante): aplicação tópica em pequenas áreas irritadas — cicatrizante natural. Use colar elizabetano para evitar ingestão
• Colar elizabetano: essencial para prevenir automutilação e infecção secundária
• Evite: produtos humanos, água oxigenada, álcool, pomadas para uso humano
• Avalie ambiente: troca de ração, novo produto de limpeza, coleira nova, carpete, ácaros

Ferimento superficial (arranhado, corte pequeno <1cm):
• Lave com soro fisiológico 0,9% em jato suave — nunca álcool ou água oxigenada
• Mel puro de abelha (manuka ou silvestre): aplicação de fina camada sobre a ferida — comprovadas propriedades antimicrobianas, cicatrizantes e anti-inflamatórias. Cubra com gaze e troque 2x/dia. Funciona muito bem em lesões superficiais limpas
• Calêndula em pomada ou gel (farmácias naturais, sem receita): poderoso cicatrizante natural, seguro em cães. Em gatos: prefira apenas soro fisiológico (tendem a lamber mais)
• Colar elizabetano para proteger
• Sinais de infecção → busque vet: inchaço progressivo, calor excessivo, secreção amarelada/verde, odor forte, febre

👁️ OLHOS E ORELHAS:

Olho com irritação leve (sem secreção abundante, sem fechamento forçado):
• Limpeza: gaze umedecida com soro fisiológico, movimentos de dentro para fora
• Compresa de chá de camomila gelado nas pálpebras EXTERNAS (nunca dentro do olho): efeito anti-inflamatório e calmante leve
• Colar elizabetano para evitar coçar
• Nunca: colírio humano (pH incompatível), pomadas não veterinárias

Orelha com coceira/odor leve:
• Limpeza com algodão e solução auricular para pets (sem receita) — nunca cotonetes
• Chá de camomila morno (temperatura corporal): 3-5 gotas no canal + massagem suave na base por 30 seg — uso popular para limpeza e alívio de coceira leve
• Mistura de vinagre de maçã + água morna (50:50): 3-4 gotas + massagem — ambiente ácido desfavorável a fungos/bactérias. SOMENTE se não houver lesão visível ou sangramento

😴 LETARGIA/APATIA SEM OUTROS SINTOMAS:
• Ambiente confortável, temperatura entre 20-26°C, cama macia em local tranquilo
• Hidratação: ofereça água fresca com frequência (troque a cada 2-3h — animais preferem água fresca)
• Estímulo alimentar: frango morno, patê úmido aquecido
• Chá de erva-cidreira morno (SOMENTE CÃES): propriedades calmantes, auxilia em letargia por estresse. Pequeno: 30ml | Médio: 60ml | Grande: 100ml
• Registre: há quanto tempo? Comeu? Bebeu? Fez xixi e cocô normalmente? Temperatura (normal: cão 38-39°C, gato 38-39,5°C)

😮‍💨 RESPIRATÓRIO LEVE (tosse episódica, sem dispneia):
• Ambiente sem fumaça, produtos de limpeza, perfumes, velas, ar condicionado seco
• Umidificador de ar ou vasilha com água próxima ao aquecedor — ar seco agrava tosse
• Mel puro (SOMENTE CÃES ≥1 ano): meia col chá (pequeno) a 1 col chá (médio/grande) diluído em água morna — efeito calmante comprovado em mucosas. NUNCA para gatos.
• Inalação de vapor: apenas no ambiente (banheiro fechado com água quente, 10-15 min) — nunca óleos essenciais para gatos (tóxicos)
• Peitoral no lugar de coleira — coleira pode pressionar traqueia e piorar tosse
• ⚠️ Tosse produtiva (com secreção), dispneia, cianose → urgente

🚿 URINÁRIO:
• ⚠️⚠️ GATO MACHO com dificuldade para urinar = URGÊNCIA ABSOLUTA — obstrução urinária mata em horas
• Para outros casos leves: aumento da oferta de água fresca (troque com frequência, fontes em movimento estimulam gatos)
• Alimento úmido/patê: aumenta ingesta hídrica em 50-70% comparado à ração seca
• Caixas de areia: mínimo 1 por gato + 1 extra, limpeza diária, local calmo
• Reduza estressores: mudanças no ambiente, gatos novos, obras

⚡ NEUROLÓGICO/TRAUMA → sempre urgente — oriente transporte seguro:
• Não mova o animal com suspeita de trauma raqui-medular — imobilize em superfície rígida
• Mantenha aquecido (cobertor, nunca bolsa de água quente direta)
• Nada pela boca em animal inconsciente ou convulsionando
• Convulsão: afaste objetos, NÃO contenha o animal, cronometre duração. Após cessar → vet imediato
• Trauma ocular: cubra o olho com gaze úmida em soro fisiológico — nunca pressione

☠️ INTOXICAÇÕES — URGÊNCIA ABSOLUTA EM TODOS OS CASOS:
Pergunte SEMPRE: o que o pet pode ter ingerido nas últimas 4-6 horas?

ALIMENTOS TÓXICOS PARA CÃES E GATOS:
• Chocolate/cacau: teobromina — tremores, convulsão, arritmia. Quanto mais escuro, mais tóxico.
• Uva e passa: insuficiência renal aguda. Qualquer quantidade é perigosa.
• Xilitol (adoçante em chicletes, doces sem açúcar, pasta dental): hipoglicemia grave + falência hepática. URGÊNCIA MÁXIMA.
• Cebola, alho, cebolinha, alho-poró (todas as formas: cru, cozido, em pó): anemia hemolítica.
• Abacate: cardiotoxicidade + pancreatite.
• Macadâmia: fraqueza, tremores, hipertermia.
• Álcool: depressão do SNC, coma.
• Cafeína: café, chá preto, refrigerante, energético — taquicardia, tremores, convulsão.
• Sal em excesso: hipernatremia — convulsão.
• Milho-de-pipoca salgado, comida temperada com alho/cebola: tóxico gradual.

PLANTAS COMUNS TÓXICAS:
• Lírio (qualquer espécie) → FATAL para gatos: insuficiência renal. QUALQUER contacto exige vet imediato.
• Dieffenbachia (comigo-ninguém-pode): irritação oral severa + edema de glote.
• Azaleia/Rododendro: vômito, colapso, arritmia.
• Oleandro: cardiotóxico.
• Zamioculcas: irritação GI.
• Espatifilo, antúrio: oxalato de cálcio — queimação oral, vômito.

OUTROS:
• Paracetamol/Tylenol: FATAL para gatos. Extremamente tóxico.
• Ibuprofeno/Aspirina: úlcera GI, insuficiência renal.
• Veneno de rato (rodenticida): sangramento interno — sintomas aparecem 3-5 dias depois.
• Veneno de barata/formiga: avalie o produto específico.
• Sapo (Rhinella marina/sapo-cururu): vômito intenso + salivação + arritmia — lave a boca com água corrente imediatamente.
→ Em qualquer suspeita de intoxicação: NÃO induza vômito sem orientação veterinária, não dê leite, vá ao vet com a embalagem do produto se possível.

🦴 ORTOPÉDICO / MUSCULOESQUELÉTICO:

Claudicação (mancar):
→ Pergunte: qual pata? Desde quando? Após queda/trauma ou espontâneo? Suporta peso?
→ Grau leve (suporta peso, sem dor visível): aplique frio local (gelo embrulhado em pano, 10-15 min, 2-3x/dia) nas primeiras 48h. Repouso absoluto.
→ Após 48h: calor úmido (pano morno) pode ajudar. Massagem suave ao redor (não sobre o local de dor).
→ NÃO force o animal a andar, não massageie diretamente sobre área de dor.
→ Repouso em superfície macia, sem escadas, sem saltos.
→ Raças predispostas (Dachshund → DPIV, Labrador/Golden → displasia, Yorkshire → luxação de patela): qualquer claudicação merece avaliação veterinária.
→ Urgente: paralisia/paresia de membros, dor intensa, incapacidade total de apoiar.

Dor nas costas/coluna (especialmente Dachshund):
→ DPIV é URGÊNCIA — paralisia progressiva dos membros posteriores pode ocorrer em horas.
→ Oriente repouso TOTAL, restrição de movimentos (caixinha/cercadinho), e busca veterinária urgente.
→ Nunca carregue o animal pela barriga durante sintoma — apoie o corpo inteiro horizontalmente.

😰 COMPORTAMENTAL:

Ansiedade / Medo / Agitação:
→ Pergunte: gatilho identificado? (trovão, fogos, visitas, separação). Há quanto tempo? Comportamentos destruidores? Automutilação?
→ Ambiente: espaço seguro (cabaninha, caixinha), meias/camiseta com cheiro do tutor, luz ambiente baixa
→ Música clássica ou white noise: reduz ansiedade em até 50% em estudos caninos
→ Thundershirt/wrap de pressão: abraço compressivo com uma camiseta velha pode ter efeito calmante
→ Pétalas de Bach — Rescue Remedy para pets (sem álcool, versão pet): gotas na água ou no focinho — seguro e sem contraindicação
→ Lavanda: borrifar na cama do pet (nunca diretamente no animal) — propriedades calmantes. NÃO para gatos (felinos são sensíveis a óleos essenciais)
→ Exercício físico antes do evento estressante (fogos de artifício) reduz intensidade da ansiedade
→ Se automutilação (lambedura excessiva, arranhar): colar elizabetano + vet para avaliação

Agressividade:
→ Pergunte: nova ou mudança súbita? Dor associada? Mudança no ambiente/rotina?
→ ⚠️ Agressividade de início súbito em animal previamente dócil pode indicar DOR — avaliação urgente
→ Nunca puna fisicamente — piora o comportamento agressivo
→ Consulta com médico veterinário comportamentalista + avaliação clínica para descartar causas de dor

Marcação / problemas com caixa de areia (gatos):
→ Pergunte: urina fora da caixa ou marcação em paredes? Caixa sempre limpa? Quantos gatos na casa?
→ Regra 1 por gato + 1 extra para caixas de areia
→ Areia sem perfume (gatos preferem), local calmo, longe da comida
→ Estresse territorial: Feliway difusor (feromonas sintéticas — sem receita, pet shops) — altamente eficaz
→ Descarte infecção urinária antes de atribuir ao comportamento

🔬 AVALIAÇÃO DE DOR — COMO IDENTIFICAR:
O tutor muitas vezes não sabe que o pet está com dor. Oriente a observar:
• Postura encolhida, relutância em mover-se
• Relutância em subir/descer
• Vocalização ao toque em região específica
• Mudança de comportamento: animal dócil ficando reativo ao toque
• Protege região específica do corpo, lambe excessivamente um local
• Diminuição do apetite + letargia + posição "de reza" (cão) = dor abdominal
• Constrição pupilar ou dilatação assimétrica (dor severa)

🍼 FILHOTES (<6 meses) — PROTOCOLO ESPECIAL:
• Muito mais vulneráveis à desidratação — sinal de urgência em 30-60% menos tempo
• Hipoglicemia: filhotes pequenos que ficam mais de 4h sem comer podem hipoglicemiar → letargia, tremores, convulsão
• Vacinação incompleta: qualquer sintoma é mais grave (parvovirose, cinomose não estão descartadas)
• Dose de TUDO é proporcionalmente menor — 30-40% das quantidades de adulto pequeno
• Qualquer sintoma persistente >4h em filhote = mínimo atenção, frequentemente urgente

👴 GERIÁTRICOS (>8 anos cão / >10 anos gato):
• Sede excessiva + urina em excesso: suspeita de diabetes ou doença renal — atenção
• Perda de peso progressiva: hipotiroidismo, doença renal, diabetes, neoplasia
• Tosse crônica em idosos: pode ser cardíaca (não só respiratória)
• Nódulos/caroços novos: avaliação prioritária — incidência de tumores aumenta significativamente
• Confusão, desorientação, acordar à noite: síndrome disfunção cognitiva (equivalente Alzheimer)
• Fraqueza dos membros posteriores progressiva: mielopatia degenerativa em certas raças

⚥ SEXO E CASTRAÇÃO — RISCOS ESPECÍFICOS:
• FÊMEA NÃO CASTRADA: piometra (infecção uterina) — urgência. Sinais: letargia, vômito, sede excessiva, corrimento vaginal (às vezes sem corrimento = forma fechada, mais grave), abdômen distendido. Ocorre 1-2 meses após o cio.
• MACHO NÃO CASTRADO: hiperplasia prostática benigna — dificuldade para defecar, tenesmo, sangue nas fezes/urina
• FÊMEA (qualquer): tumor de mama — palpe mensalmente. Nódulos mamários em fêmeas não castradas = avaliação prioritária.
• MACHO (qualquer): torção testicular — dor aguda, testículo aumentado e quente = urgência

🤰 REPRODUTIVO:
• Parto normal (cadela/gata): intervalo máximo entre filhotes = 2h. Se ultrapassa ou fêmea faz força sem expulsar filhote → urgente.
• Piometra: diagnóstico mais comum em fêmeas não castradas 1-2 meses após o cio. CIRURGIA é o tratamento — não existe suporte domiciliar suficiente. Urgência.
• Gestação: evite vacinas, medicamentos, estresse, exposição a toxinas. Alimentação: ração para filhotes ou gestante (mais calórica) no terço final.
• Lactação: nunca separe filhotes antes de 45-60 dias (cães) / 60-90 dias (gatos).

💉 VACINAÇÃO E VERMINOSE (contexto preventivo):
• Pergunte sempre: o pet está em dia com vacinas e vermífugo?
• Filhotes sem vacinação completa têm risco de parvovirose (vômito + diarreia hemorrágica + depressão severa) e cinomose
• Sinais sugestivos de verminose: diarreia recorrente, barriga distendida, pelo opaco, emagrecimento, "trenó" (arrastar o traseiro no chão)
• Verminose pode ser confundida com alergia alimentar ou diarreia simples

═══════════════════════════════════════════════
CALIBRAÇÃO POR PORTE ({peso_info}):
═══════════════════════════════════════════════
Sempre adapte quantidades de água, dieta, chás e produtos ao porte do animal.
Para FILHOTES (<6 meses): reduza 50% das quantidades e eleve o nível de urgência — são muito mais vulneráveis.
Para IDOSOS (>8 anos): mesma elevação de cautela.
Considere SEMPRE raça ({raca}), sexo ({sexo_txt}) e castração ({castrado_txt}) nas orientações.

PRODUTOS SEM RECEITA (cite quando pertinente):
"Encontrado sem receita em pet shops:" — probióticos (Floravet, Fortbac), reidratantes (PetOral), shampoos hipoalergênicos, colares elizabetanos, soro fisiológico, solução auricular para pets, calêndula gel, aloe vera gel puro
Use: "Tutores costumam utilizar..." ou "Disponível sem receita em pet shops..."
NUNCA mencione medicamentos prescritos, antibióticos, anti-inflamatórios, corticoides ou antiparasitários.

NOTA FINAL OBRIGATÓRIA em todo resultado:
"⚠️ Estas são orientações gerais de suporte domiciliar. Não substituem avaliação veterinária presencial. Em caso de dúvida ou piora, consulte um veterinário."

FORMATO FINAL (ao concluir triagem):
{{"tipo":"resultado","resultado":"urgente|atencao|estavel","orientacoes":"orientações completas e práticas conforme o caso, com quebras de linha para leitura fácil","encaminhar":true|false,"mensagem":"encerramento acolhedor em 1-2 frases"}}

DURANTE TRIAGEM (antes de concluir):
{{"tipo":"pergunta","mensagem":"próxima pergunta objetiva e empática"}}

SEMPRE JSON válido. Nunca mencione tecnologia, sistema ou processamento."""

    messages = [{'role': 'system', 'content': system_prompt}]

    for h in historico:
        messages.append({'role': h['role'], 'content': h['content']})

    try:
        resp = _groq_client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=messages,
            max_tokens=2400,
            temperature=0.3,
            response_format={'type': 'json_object'},
        )
        raw = resp.choices[0].message.content.strip()
        data = json.loads(raw)
        return data
    except Exception as e:
        return {
            'tipo': 'pergunta',
            'mensagem': 'Pode me descrever melhor o que está acontecendo com seu pet?'
        }


# ── Rotas públicas ─────────────────────────────────────────────────────────────

@petmed_bp.route('/')
def index():
    return render_template('petmed/index.html', planos=PLANOS)


@petmed_bp.route('/planos')
def planos():
    msg = request.args.get('msg', '')
    return render_template('petmed/planos.html', planos=PLANOS, msg=msg)


@petmed_bp.route('/entrar', methods=['GET', 'POST'])
def entrar():
    if session.get('pm_user_id'):
        return redirect('/petmed/dashboard')
    erro = ''
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        conn = get_petmed_db()
        u = conn.execute(
            'SELECT * FROM petmed_users WHERE email=?', (email,)
        ).fetchone()
        conn.close()
        if u and check_password_hash(u['password_hash'], senha):
            session['pm_user_id']  = u['id']
            session['pm_user_nome'] = u['nome']
            session['pm_plano']    = u['plano']
            conn2 = get_petmed_db()
            conn2.execute(
                'UPDATE petmed_users SET ultimo_acesso=? WHERE id=?',
                (_now(), u['id'])
            )
            conn2.commit()
            conn2.close()
            return redirect('/petmed/dashboard')
        erro = 'E-mail ou senha incorretos.'
    return render_template('petmed/entrar.html', erro=erro)


@petmed_bp.route('/cadastrar', methods=['GET', 'POST'])
def cadastrar():
    if session.get('pm_user_id'):
        return redirect('/petmed/dashboard')
    erro = ''
    plano_sel = request.args.get('plano', 'start')
    if request.method == 'POST':
        nome      = request.form.get('nome', '').strip()
        email     = request.form.get('email', '').strip().lower()
        telefone  = request.form.get('telefone', '').strip()
        senha     = request.form.get('senha', '')
        plano     = request.form.get('plano', 'start')
        cpf_raw   = request.form.get('cpf', '').strip()
        pet_nome  = request.form.get('pet_nome', '').strip()
        pet_esp   = request.form.get('pet_especie', 'cao')

        # Valida CPF — remove máscara e checa 11 dígitos
        cpf = re.sub(r'\D', '', cpf_raw)

        if not nome or not email or not senha or not telefone:
            erro = 'Preencha todos os campos obrigatórios.'
        elif len(senha) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        elif len(cpf) != 11:
            erro = 'CPF inválido. Digite os 11 dígitos.'
        elif not pet_nome:
            erro = 'Informe o nome do seu pet.'
        else:
            try:
                conn = get_petmed_db()
                conn.execute(
                    '''INSERT INTO petmed_users
                       (nome, email, telefone, cpf, password_hash, plano, plano_ativo)
                       VALUES (?,?,?,?,?,?,0)''',
                    (nome, email, telefone, cpf,
                     generate_password_hash(senha), plano)
                )
                conn.commit()
                u = conn.execute(
                    'SELECT * FROM petmed_users WHERE email=?', (email,)
                ).fetchone()
                # Cria primeiro pet automaticamente
                conn.execute(
                    '''INSERT INTO petmed_pets (user_id, nome, especie)
                       VALUES (?,?,?)''',
                    (u['id'], pet_nome, pet_esp)
                )
                conn.commit()
                conn.close()
                session['pm_user_id']   = u['id']
                session['pm_user_nome'] = u['nome']
                session['pm_plano']     = u['plano']
                return redirect('/petmed/dashboard?novo=1')
            except Exception as ex:
                if 'UNIQUE' in str(ex):
                    erro = 'Este e-mail já está cadastrado.'
                else:
                    erro = f'Erro ao criar conta. Tente novamente.'
    return render_template('petmed/cadastrar.html', erro=erro,
                           planos=PLANOS, plano_sel=plano_sel)


@petmed_bp.route('/sair')
def sair():
    for k in ('pm_user_id', 'pm_user_nome', 'pm_plano'):
        session.pop(k, None)
    return redirect('/petmed')


@petmed_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    """Gera código de 6 dígitos para redefinição de senha."""
    if session.get('pm_user_id'):
        return redirect('/petmed/dashboard')
    codigo_gerado = None
    erro = ''
    msg = ''
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            erro = 'Informe seu e-mail.'
        else:
            conn = get_petmed_db()
            u = conn.execute(
                'SELECT id FROM petmed_users WHERE email=?', (email,)
            ).fetchone()
            if u:
                codigo = ''.join(random.choices('0123456789', k=6))
                conn.execute(
                    '''UPDATE petmed_users
                       SET reset_token=?,
                           reset_expires=datetime("now","+30 minutes")
                       WHERE id=?''',
                    (codigo, u['id'])
                )
                conn.commit()
                codigo_gerado = codigo
            conn.close()
            # Mesmo que o e-mail não exista, mostramos a mesma tela
            # (evita enumerar usuários); se existe, mostramos o código
            if not codigo_gerado:
                msg = 'Se este e-mail estiver cadastrado, um código foi gerado.'
    return render_template('petmed/esqueci-senha.html',
                           erro=erro, msg=msg, codigo_gerado=codigo_gerado)


@petmed_bp.route('/redefinir-senha', methods=['GET', 'POST'])
def redefinir_senha():
    """Valida código e define nova senha."""
    if session.get('pm_user_id'):
        return redirect('/petmed/dashboard')
    erro = ''
    msg = ''
    if request.method == 'POST':
        email      = request.form.get('email', '').strip().lower()
        codigo     = request.form.get('codigo', '').strip()
        nova_senha = request.form.get('nova_senha', '')
        confirmar  = request.form.get('confirmar', '')
        if not email or not codigo:
            erro = 'Preencha e-mail e código.'
        elif len(nova_senha) < 6:
            erro = 'A nova senha deve ter pelo menos 6 caracteres.'
        elif nova_senha != confirmar:
            erro = 'As senhas não coincidem.'
        else:
            conn = get_petmed_db()
            u = conn.execute(
                '''SELECT id FROM petmed_users
                   WHERE email=? AND reset_token=?
                   AND reset_expires > datetime("now")''',
                (email, codigo)
            ).fetchone()
            if u:
                conn.execute(
                    '''UPDATE petmed_users
                       SET password_hash=?, reset_token=NULL, reset_expires=NULL
                       WHERE id=?''',
                    (generate_password_hash(nova_senha), u['id'])
                )
                conn.commit()
                conn.close()
                msg = 'Senha redefinida com sucesso! Você já pode entrar.'
            else:
                conn.close()
                erro = 'Código inválido ou expirado. Solicite um novo código.'
    return render_template('petmed/redefinir-senha.html', erro=erro, msg=msg)


# ── Área logada ────────────────────────────────────────────────────────────────

@petmed_bp.route('/dashboard')
@petmed_login_required
def dashboard():
    u    = _get_user()
    pets = _get_pets(u['id'])
    conn = get_petmed_db()
    triagens_recentes = conn.execute(
        '''SELECT * FROM petmed_triagens WHERE user_id=?
           ORDER BY created_at DESC LIMIT 5''',
        (u['id'],)
    ).fetchall()
    total_triagens = conn.execute(
        'SELECT COUNT(*) FROM petmed_triagens WHERE user_id=?', (u['id'],)
    ).fetchone()[0]
    # Vacinas vencendo nos próximos 30 dias
    vacinas_proximas = conn.execute(
        '''SELECT v.*, p.nome as pet_nome
           FROM petmed_vacinas v
           JOIN petmed_pets p ON v.pet_id = p.id
           WHERE v.user_id=? AND v.proxima IS NOT NULL
             AND v.proxima BETWEEN date('now') AND date('now','+30 days')
           ORDER BY v.proxima ASC LIMIT 5''',
        (u['id'],)
    ).fetchall()
    conn.close()
    novo = request.args.get('novo', '')
    pode_add, total_pets, limite_pets = _can_add_pet(u['id'], u['plano'])
    bloqueado_paywall, triagens_usadas = _check_paywall(u)
    return render_template('petmed/dashboard.html',
                           u=u, pets=pets,
                           triagens=triagens_recentes,
                           total_triagens=total_triagens,
                           vacinas_proximas=vacinas_proximas,
                           novo=novo,
                           pode_add=pode_add,
                           total_pets=total_pets,
                           limite_pets=limite_pets,
                           planos=PLANOS,
                           bloqueado_paywall=bloqueado_paywall,
                           triagens_usadas=triagens_usadas)


@petmed_bp.route('/meus-pets')
@petmed_login_required
def meus_pets():
    u    = _get_user()
    pets = _get_pets(u['id'])
    pode_add, total_pets, limite_pets = _can_add_pet(u['id'], u['plano'])
    msg = request.args.get('msg', '')
    return render_template('petmed/meus_pets.html',
                           u=u, pets=pets,
                           pode_add=pode_add,
                           total_pets=total_pets,
                           limite_pets=limite_pets,
                           msg=msg)


@petmed_bp.route('/pets/adicionar', methods=['GET', 'POST'])
@petmed_login_required
def adicionar_pet():
    u = _get_user()
    pode_add, total_pets, limite_pets = _can_add_pet(u['id'], u['plano'])
    erro = ''

    if not pode_add:
        return redirect(f'/petmed/planos?msg=limite_pets&plano={u["plano"]}')

    if request.method == 'POST':
        nome        = request.form.get('nome', '').strip()
        especie     = request.form.get('especie', 'cao')
        raca        = request.form.get('raca', '').strip()
        idade_anos  = request.form.get('idade_anos', 0) or 0
        idade_meses = request.form.get('idade_meses', 0) or 0
        peso_kg     = request.form.get('peso_kg', '') or None
        sexo        = request.form.get('sexo', 'nao_informado')
        castrado    = 1 if request.form.get('castrado') else 0
        observacoes = request.form.get('observacoes', '').strip()

        # Identificação de raça por foto
        foto_base64 = request.form.get('foto_base64', '')
        if foto_base64 and not raca:
            raca = _identificar_raca(foto_base64, especie)

        if not nome:
            erro = 'O nome do pet é obrigatório.'
        else:
            conn = get_petmed_db()
            conn.execute(
                '''INSERT INTO petmed_pets
                   (user_id, nome, especie, raca, idade_anos, idade_meses,
                    peso_kg, sexo, castrado, observacoes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (u['id'], nome, especie, raca, int(idade_anos),
                 int(idade_meses), peso_kg, sexo, castrado, observacoes)
            )
            conn.commit()
            conn.close()
            return redirect('/petmed/meus-pets?msg=pet_adicionado')

    return render_template('petmed/adicionar_pet.html',
                           u=u, erro=erro,
                           limite_pets=limite_pets)


@petmed_bp.route('/pets/<int:pet_id>/editar', methods=['GET', 'POST'])
@petmed_login_required
def editar_pet(pet_id):
    u = _get_user()
    conn = get_petmed_db()
    pet = conn.execute(
        'SELECT * FROM petmed_pets WHERE id=? AND user_id=?', (pet_id, u['id'])
    ).fetchone()
    conn.close()
    if not pet:
        abort(404)

    erro = ''
    if request.method == 'POST':
        nome        = request.form.get('nome', '').strip()
        raca        = request.form.get('raca', '').strip()
        idade_anos  = request.form.get('idade_anos', 0) or 0
        idade_meses = request.form.get('idade_meses', 0) or 0
        peso_kg     = request.form.get('peso_kg', '') or None
        sexo        = request.form.get('sexo', 'nao_informado')
        castrado    = 1 if request.form.get('castrado') else 0
        observacoes = request.form.get('observacoes', '').strip()

        if not nome:
            erro = 'O nome do pet é obrigatório.'
        else:
            conn2 = get_petmed_db()
            conn2.execute(
                '''UPDATE petmed_pets SET nome=?, raca=?, idade_anos=?,
                   idade_meses=?, peso_kg=?, sexo=?, castrado=?, observacoes=?
                   WHERE id=? AND user_id=?''',
                (nome, raca, int(idade_anos), int(idade_meses),
                 peso_kg, sexo, castrado, observacoes, pet_id, u['id'])
            )
            conn2.commit()
            conn2.close()
            return redirect('/petmed/meus-pets?msg=pet_editado')

    return render_template('petmed/editar_pet.html', u=u, pet=pet, erro=erro)


@petmed_bp.route('/pets/<int:pet_id>/excluir', methods=['POST'])
@petmed_login_required
def excluir_pet(pet_id):
    u = _get_user()
    conn = get_petmed_db()
    conn.execute(
        'DELETE FROM petmed_pets WHERE id=? AND user_id=?', (pet_id, u['id'])
    )
    conn.commit()
    conn.close()
    return redirect('/petmed/meus-pets?msg=pet_removido')


# ── Triagem ────────────────────────────────────────────────────────────────────

@petmed_bp.route('/triagem')
@petmed_login_required
def triagem_inicio():
    u    = _get_user()
    bloqueado, triagens_usadas = _check_paywall(u)
    if bloqueado:
        return redirect('/petmed/planos?msg=paywall')
    pets = _get_pets(u['id'])
    # Limpa triagem anterior da sessão
    session.pop('pm_triagem', None)
    return render_template('petmed/triagem_inicio.html',
                           u=u, pets=pets, categorias=CATEGORIAS,
                           triagens_usadas=triagens_usadas,
                           plano_ativo=u['plano_ativo'])


@petmed_bp.route('/triagem/chat', methods=['GET', 'POST'])
@petmed_login_required
def triagem_chat():
    u = _get_user()

    if request.method == 'POST':
        dados = request.get_json(silent=True) or {}
        acao  = dados.get('acao', '')

        # ── Iniciar triagem ────────────────────────────────────────────────────
        if acao == 'iniciar':
            # Verifica paywall antes de iniciar
            bloqueado, _ = _check_paywall(u)
            if bloqueado:
                return jsonify({
                    'tipo': 'paywall',
                    'mensagem': 'Você já utilizou sua consulta gratuita. Assine um plano para continuar.',
                    'url': '/petmed/planos?msg=paywall'
                })

            pet_id    = dados.get('pet_id')
            categoria = dados.get('categoria', 'outro')
            pet_info  = {}

            if pet_id:
                conn = get_petmed_db()
                pet = conn.execute(
                    'SELECT * FROM petmed_pets WHERE id=? AND user_id=?',
                    (pet_id, u['id'])
                ).fetchone()
                conn.close()
                if pet:
                    pet_info = dict(pet)

            # Salva estado da triagem na sessão
            session['pm_triagem'] = {
                'pet_id': pet_id,
                'pet_info': pet_info,
                'categoria': categoria,
                'historico': [],
                'iniciada': _now()
            }

            nome_pet = pet_info.get('nome', 'seu pet')
            cat_label = CATEGORIAS.get(categoria, {}).get('label', categoria)

            primeira_pergunta = (
                f"Olá! Vou te ajudar a avaliar {nome_pet}. 🐾\n\n"
                f"Vi que o problema é relacionado a **{cat_label}**.\n\n"
                f"Para começar: há quanto tempo {nome_pet} está apresentando esse sintoma?"
            )

            historico = session['pm_triagem']['historico']
            historico.append({'role': 'assistant', 'content': primeira_pergunta})
            session.modified = True

            return jsonify({'tipo': 'pergunta', 'mensagem': primeira_pergunta})

        # ── Resposta do tutor ─────────────────────────────────────────────────
        elif acao == 'responder':
            triagem = session.get('pm_triagem')
            if not triagem:
                return jsonify({'tipo': 'erro', 'mensagem': 'Sessão expirada. Inicie novamente.'})

            resposta_tutor = dados.get('mensagem', '').strip()
            if not resposta_tutor:
                return jsonify({'tipo': 'erro', 'mensagem': 'Mensagem vazia.'})

            historico = triagem['historico']
            historico.append({'role': 'user', 'content': resposta_tutor})

            # Chama a IA
            resultado = _fazer_triagem(
                triagem['pet_info'],
                triagem['categoria'],
                historico
            )

            if resultado.get('tipo') == 'pergunta':
                historico.append({'role': 'assistant', 'content': resultado['mensagem']})
                session.modified = True
                return jsonify(resultado)

            elif resultado.get('tipo') == 'resultado':
                # Salva triagem no banco
                pet_info  = triagem.get('pet_info', {})
                categoria = triagem.get('categoria', 'outro')
                conn = get_petmed_db()
                conn.execute(
                    '''INSERT INTO petmed_triagens
                       (user_id, pet_id, pet_nome, pet_especie, pet_raca,
                        categoria, perguntas_json, resultado, orientacoes, encaminhar_vet)
                       VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (
                        u['id'],
                        pet_info.get('id'),
                        pet_info.get('nome', 'Pet'),
                        pet_info.get('especie', 'cao'),
                        pet_info.get('raca', 'SRD'),
                        categoria,
                        json.dumps(historico, ensure_ascii=False),
                        resultado.get('resultado', 'atencao'),
                        resultado.get('orientacoes', ''),
                        1 if resultado.get('encaminhar') else 0
                    )
                )
                conn.commit()
                conn.close()
                session.pop('pm_triagem', None)
                return jsonify(resultado)

            return jsonify({'tipo': 'pergunta', 'mensagem': 'Pode me contar mais sobre o que está acontecendo?'})

        return jsonify({'erro': 'Ação inválida'}), 400

    # GET — página do chat
    triagem = session.get('pm_triagem')
    if not triagem:
        # Inicializa a partir dos query params (chegando do form triagem_inicio)
        pet_id_raw = request.args.get('pet_id', '0')
        categoria  = request.args.get('categoria', 'outro')
        if not categoria or categoria not in CATEGORIAS:
            return redirect('/petmed/triagem')
        try:
            pet_id = int(pet_id_raw)
        except (ValueError, TypeError):
            pet_id = 0

        pet_info = {}
        if pet_id:
            conn = get_petmed_db()
            pet = conn.execute(
                'SELECT * FROM petmed_pets WHERE id=? AND user_id=?',
                (pet_id, u['id'])
            ).fetchone()
            conn.close()
            if pet:
                pet_info = dict(pet)

        session['pm_triagem'] = {
            'pet_id': pet_id,
            'pet_info': pet_info,
            'categoria': categoria,
            'historico': [],
            'iniciada': _now()
        }
        session.modified = True
        triagem = session['pm_triagem']

    return render_template('petmed/triagem_chat.html',
                           u=u, triagem=triagem, categorias=CATEGORIAS)


# ── Identificar raça via foto (AJAX) ──────────────────────────────────────────
@petmed_bp.route('/identificar-raca', methods=['POST'])
@petmed_login_required
def identificar_raca():
    dados = request.get_json(silent=True) or {}
    foto_b64 = dados.get('foto', '')
    especie  = dados.get('especie', 'cao')
    if not foto_b64:
        return jsonify({'raca': ''})
    raca = _identificar_raca(foto_b64, especie)
    return jsonify({'raca': raca})


# ── Histórico ─────────────────────────────────────────────────────────────────

@petmed_bp.route('/historico')
@petmed_login_required
def historico():
    u = _get_user()
    pet_id = request.args.get('pet_id', '')
    conn = get_petmed_db()
    if pet_id:
        triagens = conn.execute(
            '''SELECT * FROM petmed_triagens WHERE user_id=? AND pet_id=?
               ORDER BY created_at DESC LIMIT 50''',
            (u['id'], pet_id)
        ).fetchall()
    else:
        triagens = conn.execute(
            '''SELECT * FROM petmed_triagens WHERE user_id=?
               ORDER BY created_at DESC LIMIT 50''',
            (u['id'],)
        ).fetchall()
    pets = conn.execute(
        'SELECT id, nome FROM petmed_pets WHERE user_id=?', (u['id'],)
    ).fetchall()
    conn.close()
    return render_template('petmed/historico.html',
                           u=u, triagens=triagens, pets=pets,
                           pet_id_sel=pet_id, categorias=CATEGORIAS)


@petmed_bp.route('/historico/<int:tid>')
@petmed_login_required
def triagem_detalhe(tid):
    u = _get_user()
    conn = get_petmed_db()
    t = conn.execute(
        'SELECT * FROM petmed_triagens WHERE id=? AND user_id=?', (tid, u['id'])
    ).fetchone()
    conn.close()
    if not t:
        abort(404)
    historico_msgs = []
    try:
        historico_msgs = json.loads(t['perguntas_json'])
    except Exception:
        pass
    return render_template('petmed/triagem_detalhe.html',
                           u=u, t=t, historico=historico_msgs,
                           categorias=CATEGORIAS)


# ── Vacinas ────────────────────────────────────────────────────────────────────

@petmed_bp.route('/vacinas')
@petmed_login_required
def vacinas():
    u = _get_user()
    pets = _get_pets(u['id'])
    pet_id_sel = request.args.get('pet_id', '')
    conn = get_petmed_db()
    if pet_id_sel:
        vacinas_list = conn.execute(
            'SELECT v.*, p.nome as pet_nome FROM petmed_vacinas v '
            'JOIN petmed_pets p ON v.pet_id=p.id '
            'WHERE v.user_id=? AND v.pet_id=? ORDER BY v.proxima',
            (u['id'], pet_id_sel)
        ).fetchall()
    else:
        vacinas_list = conn.execute(
            'SELECT v.*, p.nome as pet_nome FROM petmed_vacinas v '
            'JOIN petmed_pets p ON v.pet_id=p.id '
            'WHERE v.user_id=? ORDER BY v.proxima',
            (u['id'],)
        ).fetchall()
    conn.close()
    msg = request.args.get('msg', '')
    return render_template('petmed/vacinas.html',
                           u=u, pets=pets, vacinas=vacinas_list,
                           pet_id_sel=pet_id_sel, msg=msg)


@petmed_bp.route('/vacinas/adicionar', methods=['POST'])
@petmed_login_required
def adicionar_vacina():
    u = _get_user()
    pet_id     = request.form.get('pet_id')
    nome       = request.form.get('nome', '').strip()
    data_aplic = request.form.get('data_aplic', '')
    proxima    = request.form.get('proxima', '')
    if pet_id and nome:
        conn = get_petmed_db()
        conn.execute(
            'INSERT INTO petmed_vacinas (pet_id, user_id, nome, data_aplic, proxima) VALUES (?,?,?,?,?)',
            (pet_id, u['id'], nome, data_aplic, proxima)
        )
        conn.commit()
        conn.close()
    return redirect(f'/petmed/vacinas?msg=vacina_adicionada&pet_id={pet_id or ""}')


# ── Teleconsulta (Premium) ─────────────────────────────────────────────────────

@petmed_bp.route('/teleconsulta')
@petmed_premium_required
def teleconsulta():
    u = _get_user()
    pets = _get_pets(u['id'])
    conn = get_petmed_db()
    vets = conn.execute(
        'SELECT * FROM petmed_vets WHERE ativo=1 AND disponivel=1 ORDER BY avaliacao DESC'
    ).fetchall()
    minhas = conn.execute(
        '''SELECT tc.*, v.nome as vet_nome, p.nome as pet_nome
           FROM petmed_teleconsultas tc
           LEFT JOIN petmed_vets v ON tc.vet_id=v.id
           LEFT JOIN petmed_pets p ON tc.pet_id=p.id
           WHERE tc.user_id=? ORDER BY tc.created_at DESC LIMIT 10''',
        (u['id'],)
    ).fetchall()
    conn.close()
    return render_template('petmed/teleconsulta.html',
                           u=u, pets=pets, vets=vets, minhas=minhas)


# ── API: contagem para badge ───────────────────────────────────────────────────

@petmed_bp.route('/api/status')
@petmed_login_required
def api_status():
    u = _get_user()
    conn = get_petmed_db()
    triagens_hoje = conn.execute(
        '''SELECT COUNT(*) FROM petmed_triagens
           WHERE user_id=? AND date(created_at)=date("now","localtime")''',
        (u['id'],)
    ).fetchone()[0]
    urgentes = conn.execute(
        '''SELECT COUNT(*) FROM petmed_triagens
           WHERE user_id=? AND resultado="urgente"
           AND date(created_at)=date("now","localtime")''',
        (u['id'],)
    ).fetchone()[0]
    vacinas_proximas = conn.execute(
        '''SELECT COUNT(*) FROM petmed_vacinas v
           JOIN petmed_pets p ON v.pet_id=p.id
           WHERE p.user_id=? AND v.proxima BETWEEN date("now") AND date("now","+30 days")''',
        (u['id'],)
    ).fetchone()[0]
    conn.close()
    return jsonify({
        'triagens_hoje': triagens_hoje,
        'urgentes': urgentes,
        'vacinas_proximas': vacinas_proximas,
        'plano': u['plano']
    })


# ── Área veterinário parceiro ──────────────────────────────────────────────────

@petmed_bp.route('/vet/cadastro', methods=['GET', 'POST'])
def vet_cadastro():
    erro = ''
    if request.method == 'POST':
        nome        = request.form.get('nome', '').strip()
        email       = request.form.get('email', '').strip().lower()
        telefone    = request.form.get('telefone', '').strip()
        crmv        = request.form.get('crmv', '').strip()
        estado_crmv = request.form.get('estado_crmv', 'SC')
        especialidade = request.form.get('especialidade', '').strip()
        bio         = request.form.get('bio', '').strip()
        senha       = request.form.get('senha', '')

        if not all([nome, email, telefone, crmv, senha]):
            erro = 'Preencha todos os campos obrigatórios.'
        elif len(senha) < 6:
            erro = 'Senha deve ter pelo menos 6 caracteres.'
        else:
            try:
                conn = get_petmed_db()
                conn.execute(
                    '''INSERT INTO petmed_vets
                       (nome, email, telefone, crmv, estado_crmv,
                        especialidade, bio, password_hash, ativo)
                       VALUES (?,?,?,?,?,?,?,?,0)''',
                    (nome, email, telefone, crmv, estado_crmv,
                     especialidade, bio, generate_password_hash(senha))
                )
                conn.commit()
                conn.close()
                return redirect('/petmed/vet/cadastro?ok=1')
            except Exception as ex:
                if 'UNIQUE' in str(ex):
                    erro = 'Este e-mail já está cadastrado.'
                else:
                    erro = 'Erro ao cadastrar. Tente novamente.'
    ok = request.args.get('ok', '')
    return render_template('petmed/vet_cadastro.html', erro=erro, ok=ok)
