"""
pubshow_pay.py — PubShow: pagamento NOVO pela Efí (Lote C do RELATORIO_FINAL de 15/10/26).

DESLIGADO por padrão: só vale com PUBSHOW_GATEWAY=efi (padrão 'asaas' = o fluxo de hoje, intocado).
Não importa pubshow.py nem app.py; cria as próprias tabelas (init_tables) no pubshow.db.

O que faz:
- Cobra o momento numa cobrança Efí com txid NOSSO (PUT /v2/cob/:txid, idempotente), 15 min de validade.
- Confirma por CONSULTA (tela do cliente + reconciliar() a cada 60 s): o webhook Efí não valida no Railway.
- Livro-caixa pubshow_ledger em CENTAVOS (INTEGER), imutável (gatilhos), ref UNIQUE = idempotência.
  Contas por bar: 'casa' (momentos do bar), 'gorjeta' (da equipe), 'plataforma' (o que ficou com o PubShow).
  Saldo = SUM(valor_cent). Errou? Lançamento novo de ajuste; nunca UPDATE/DELETE.
- "O app se paga": a parte do PubShow em cada momento = R$ 1 do app (taxa de serviço) + min(30% do item, o que
  falta da mensalidade do mês). A GORJETA NUNCA paga mensalidade (CLT art. 457 §3º).
- 16/10/26 (Lote D A2, aprovado pelo Diogo): o bar NUNCA tira do bolso. Saiu a garantia de 3 meses e não existe
  cobrança da diferença (o C9 morreu): mês que não fechou a mensalidade, o bar pagou só os 30% do que rendeu.
- 16/10/26 (Lote D A3/A4): pausa de 60 dias sem momento pago (só Efí), teto de R$ 50 por momento e PIX confirmado
  mais de 30 min depois de vencer = devolução automática.
- SPLIT-ONLY (16/10/26, decisão do Diogo: "o dinheiro do bar cai direto na conta Efí dele e a mensalidade
  sai pelo split"): a parte do bar cai NA HORA na conta Efí do próprio bar. O bolsão (dinheiro do bar parado
  na conta do PubShow + repasse no fim do mês) foi DESCARTADO e saiu do código. Bar sem conta Efí = sem
  cobrança ('bar_sem_conta_efi'): o dinheiro do bar nunca cai na conta do PubShow.
- Estorno automático e idempotente: id da devolução = psd<pedido> (Efí recusa id repetido).
  A devolução sai só da conta do PubShow [L doc Efí]; a parte do bar vira saldo negativo
  ("o bar deve") e é compensada nas próximas cobranças da MESMA conta (Código Civil art. 368).

Cliente Efí: efi_client.EfiClient('PUBSHOW_EFI_') (o efi_pix.py da RifaJá não é usado aqui).
Uso no pubshow.py: ver C:\\Users\\Diogo\\Desktop\\PROJETOS\\PUBSHOW_15out\\PAGAMENTO_NOVO.md.
Toda função recebe `conn` (sqlite3) SEM transação aberta e cuida do próprio commit.
"""
import os, re, hashlib, logging, secrets, sqlite3, threading, time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, date

log = logging.getLogger('pubshow_pay')

TIPOS_EQUIPE = ('gorjeta',)            # igual ao pubshow.TIPOS_EQUIPE (dinheiro da equipe)
CONTAS = ('casa', 'gorjeta', 'plataforma')
BRT = timezone(timedelta(hours=-3))    # Brasil sem horário de verão desde 2019
_FMT = '%Y-%m-%d %H:%M:%S'

# status da cobrança
ST_RESERVA = ('criando', 'ativa')                      # seguram a parte da mensalidade
ST_CONFIRMAVEL = ('criando', 'ativa', 'expirada', 'cancelada')
ST_PAGAS = ('paga', 'devolvendo', 'devolver_erro', 'devolvida')
DEV_MAX_TENTATIVAS = 8
# 15/10/26 (V7-04): espera (min) depois da n-ésima recusa da Efí antes da próxima tentativa (id novo).
# Antes era a cada 60 s: esgotava as tentativas em ~5 min (ex.: conta do PubShow sem saldo no split).
# Soma ≈ 46 h; a devolução Pix vale até 90 dias.
DEV_ESPERA_MIN = (5, 15, 60, 180, 360, 720, 1440)
# 16/10/26 (CC1 X5): devolução 'devolvendo' que nenhum caminho lê (hipótese 9 ruim nos 2 GETs). Antes o robô fazia
# ~4 chamadas à Efí por minuto por cobrança, pra sempre, e re-mandava o PUT a cada rodada. Agora: as primeiras
# DEV_CHECA_RAPIDO conferências seguem a cada rodada; depois, espera crescente (DEV_ESPERA_MIN, até 1×/dia).
# O PUT de novo com o MESMO id (V7-01) só nas primeiras DEV_REPUT_MAX conferências. Com DEV_TRAVADA_CHECAGENS
# conferências sem resposta (~meia hora) a cobrança já aparece em "Devoluções travadas" (antes só depois de 24 h).
DEV_CHECA_RAPIDO = 5
DEV_REPUT_MAX = 3
DEV_TRAVADA_CHECAGENS = 7
GRACA_PRAZO_S = 120                                   # PIX até 2 min depois do prazo = no prazo
# 16/10/26 (CC1 X2): a cobrança 'expirada' ainda RESERVA a parte da mensalidade enquanto o confirmar() pode aceitar
# um PIX dela como "no prazo" (GRACA_PRAZO_S) + folga pro robô (roda a cada 60 s) achar esse PIX. Antes a reserva
# caía na hora de expirar: outra cobrança reservava a MESMA parte e, pagas as 2, a mensalidade passava do plano
# (o excesso, do bar, ficava no PubShow). Segurar a mais só adia a mensalidade pros próximos momentos (nunca cobra 2×).
RESERVA_POS_PRAZO_S = GRACA_PRAZO_S + 600
JANELA_ATRASO_H = 2                                   # procura PIX atrasado em cobrança expirada/cancelada por 2 h
# VCb-01 (16/10/26): divisão típica de um momento (R$ 1 de taxa pro PubShow, R$ 5 pro bar) usada pra conferir a
# conta Efí do bar no passo 4 (a Efí confere o titular ao criar a config de split). Mesma divisão = config reaproveitada.
CONFERE_PLAT_CENT, CONFERE_BAR_CENT = 100, 500
# 16/10/26 (Lote D A4, crítico CC1 X1/X1b): teto de R$ 50 por momento no modo Efí. No split a parte do bar cai na
# hora e a devolução sai INTEIRA da conta do PubShow: preço livre (R$ 1.000) + recusa = o PubShow banca R$ 1.000.
# O pubshow.py corta os preços do bar (_precos_do_bar e o formulário); aqui é a 2ª trava (nunca cria acima disso).
TETO_ITEM_CENT = 5000
# Lote D A4: PIX que só foi CONFIRMADO mais de 30 min depois de vencer (robô parado, rollback de uma noite) não vai
# pra TV — o cliente já foi embora. Devolve sozinho (antes tocava no dia seguinte um brinde pago na véspera, CC3).
CONFIRMA_TARDE_S = 1800
# 17/10/26 (decisão 5 do Diogo): os 30 min contam do PAGAMENTO (horário do PIX na Efí), não da nossa confirmação.
# Pane da Efí (consulta fora do ar 50 min numa sexta) não vira devolução em massa: o PIX pago no prazo vai pra TV
# quando a confirmação chegar. Só devolve sozinho o PIX que o cliente PAGOU mais de 30 min depois de vencer.
# 17/10/26 (decisão 3 do Diogo): teto de devolução de momento JÁ PAGO por dono (CPF/CNPJ + conta Efí) por dia
# (Brasília). Passou disso, a devolução espera o OK no /saas-admin (o painel diz "a devolução está com o suporte").
TETO_DEVOLUCAO_DIA_CENT = 10000
# Lote D A3: bar no modo Efí sem nenhum momento pago há PAUSA_DIAS → pausa (momentos, música grátis e TV);
# aviso no painel e por WhatsApp/e-mail PAUSA_AVISO_DIAS antes. Reativar = botão no /pubshow/admin e no saas-admin.
PAUSA_DIAS = 60
PAUSA_AVISO_DIAS = 7
# Lote D, críticos (17/10/26, CD2): o robô de avisos (a cada 10 min) marca visto_em de cada bar da Efí. Se ele não viu o
# bar por mais de PAUSA_FORA_DIAS (saiu da lista da Efí, rollback pro Asaas, servidor fora), o relógio dos 60 dias
# recomeça quando ele volta: o bar não volta já pausado por um tempo em que nem estava na Efí.
PAUSA_FORA_DIAS = 1


# ══ Configuração (lida na HORA: dá pra trocar env sem reiniciar nos testes) ═══════════════
def _env_int(nome, padrao):
    try:
        v = (os.environ.get(nome) or '').strip()
        return int(v) if v else int(padrao)
    except Exception:
        return int(padrao)


def ligado():
    """Só liga com PUBSHOW_GATEWAY=efi. Padrão 'asaas' = este módulo não é usado."""
    return (os.environ.get('PUBSHOW_GATEWAY') or 'asaas').strip().lower() == 'efi'


def ligado_para(bar):
    """Liga bar a bar: PUBSHOW_GATEWAY=efi E o code do bar em PUBSHOW_EFI_BARES (separada por vírgula).
    Ex.: PUBSHOW_EFI_BARES=CC4TN7PG liga só o bar demo "Lessmann"; depois soma o Garage.
    16/10/26 (CC1 X6 / CC3 R6): falha FECHADA — vazio (ou env apagada/com nome errado) = NENHUM bar; pra ligar
    todos, o valor explícito '*'. Antes vazio = todos: o Garage caía na Efí sem conta e perdia os momentos pagos."""
    if not ligado():
        return False
    lista = [c.strip().upper() for c in (os.environ.get('PUBSHOW_EFI_BARES') or '').split(',') if c.strip()]
    if not lista:
        return False
    if '*' in lista:
        return True
    try:
        code = str(bar['code'] or '').strip().upper()
    except Exception:
        return False
    return code in lista


def configurado():
    """Ligado E com a conta Efí do PubShow (PUBSHOW_EFI_*) preenchida."""
    try:
        return ligado() and cliente().configurado()
    except Exception:
        return False


def plano_cent(bar=None):
    """Mensalidade em centavos: bar['plano_cent'] > PUBSHOW_PLANO_CENT > 7990 (R$ 79,90, decisão de 17/10/26).
    16/10/26 (Lote D A1): bar['plano_cent'] é a coluna do pubshow_businesses = o preço do bar guardado no cadastro
    (fundador: travado 24 meses, preco_trava_ate). A exceção do pubshow_pay_planos (definir_plano) vem por cima."""
    try:
        if bar is not None and 'plano_cent' in bar.keys() and bar['plano_cent'] is not None:
            return max(0, int(bar['plano_cent']))
    except Exception:
        pass
    return max(0, _env_int('PUBSHOW_PLANO_CENT', 7990))


def conv_cent_para(tipo):
    """Taxa de serviço (centavos) que o CLIENTE paga a mais. Padrão = o de HOJE
    (PUBSHOW_TAXA_CONVENIENCIA, que por padrão = PUBSHOW_TAXA_ASAAS = R$ 1,00), inclusive na gorjeta.
    PUBSHOW_CONV_CENT sobrescreve. PUBSHOW_GORJETA_CONV=0 tira a taxa da gorjeta (decisão 3 do Diogo)."""
    if tipo in TIPOS_EQUIPE and (os.environ.get('PUBSHOW_GORJETA_CONV') or '1').strip() == '0':
        return 0
    if (os.environ.get('PUBSHOW_CONV_CENT') or '').strip():
        return max(0, _env_int('PUBSHOW_CONV_CENT', 100))
    try:
        reais = os.environ.get('PUBSHOW_TAXA_CONVENIENCIA') or os.environ.get('PUBSHOW_TAXA_ASAAS') or '1.00'
        return max(0, int(round(float(reais) * 100)))
    except Exception:
        return 100


def teto_devolucao_cent():
    """Decisão 3 (17/10/26): R$ 100/dia por dono. Env PUBSHOW_TETO_DEVOLUCAO_CENT (centavos) sobrescreve."""
    return max(0, _env_int('PUBSHOW_TETO_DEVOLUCAO_CENT', TETO_DEVOLUCAO_DIA_CENT))


def expira_s():
    return max(60, _env_int('PUBSHOW_PIX_EXPIRA_S', 900))


# 16/10/26 (Lote D, integrador): saiu garantia_meses(). A fatia A tirou a garantia do dinheiro e só a deixou pro
# site do modelo novo; a fatia B tirou do site. Ninguém mais lê PUBSHOW_GARANTIA_MESES (env sem efeito).
def mensal_pct():
    """16/10/26 (Lote D A2): quanto de CADA momento paga a mensalidade, até fechar o preço do bar no mês.
    PUBSHOW_MENSAL_PCT (padrão 30; 0 a 100). Gorjeta nunca paga mensalidade."""
    return min(100, max(0, _env_int('PUBSHOW_MENSAL_PCT', 30)))


# ══ Tempo e competência (mês em horário de Brasília) ═════════════════════════════════════
def _agora(agora=None):
    """UTC 'naive' (igual ao CURRENT_TIMESTAMP do SQLite)."""
    return agora if agora is not None else datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(dt):
    return dt.strftime(_FMT)


def _parse(ts):
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=None) if ts.tzinfo is None else ts.astimezone(timezone.utc).replace(tzinfo=None)
    s = str(ts).strip().replace('T', ' ')
    try:
        if s.endswith('Z'):
            s = s[:-1]
        m = re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(\.\d+)?([+-]\d{2}:?\d{2})?$', s)
        if not m:
            return datetime.strptime(s[:19], _FMT)
        dt = datetime.strptime(m.group(1), _FMT)
        if m.group(3):
            off = m.group(3).replace(':', '')
            sinal = 1 if off[0] == '+' else -1
            dt = dt - sinal * timedelta(hours=int(off[1:3]), minutes=int(off[3:5]))
        return dt
    except Exception:
        return None


def competencia(agora=None):
    """'AAAA-MM' do mês em Brasília (UTC-3)."""
    dt = _agora(agora).replace(tzinfo=timezone.utc).astimezone(BRT)
    return dt.strftime('%Y-%m')


def _mes_ok(mes):
    if not re.match(r'^\d{4}-(0[1-9]|1[0-2])$', str(mes or '')):
        raise ValueError(f'competência inválida: {mes!r} (use AAAA-MM)')
    return mes


# 16/10/26 (Lote D A2): saíram _mes_idx(), _inicio_bar() e em_garantia() — a garantia de 3 meses não existe mais (o bar nunca
# tira do bolso: o mês que não fecha a mensalidade fica só com os 30% do que rendeu, sem cobrança da diferença).


# ══ Banco ═════════════════════════════════════════════════════════════════════════════════
_SCHEMA = '''
CREATE TABLE IF NOT EXISTS pubshow_pay_cobrancas (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pedido_id        INTEGER NOT NULL UNIQUE,          -- 1 cobrança por pedido (expirou = pedido novo)
    business_id      INTEGER NOT NULL,
    gateway          TEXT NOT NULL DEFAULT 'efi',
    txid             TEXT NOT NULL UNIQUE,             -- nosso: 'ps<pedido>x' + aleatório (32 letras/dígitos)
    tipo             TEXT NOT NULL,
    conta            TEXT NOT NULL CHECK (conta IN ('casa','gorjeta')),
    competencia      TEXT NOT NULL,                    -- 'AAAA-MM' (BRT) da criação
    item_cent        INTEGER NOT NULL CHECK (item_cent > 0),
    conv_cent        INTEGER NOT NULL CHECK (conv_cent >= 0),   -- taxa de serviço (do PubShow)
    valor_cent       INTEGER NOT NULL,                 -- cobrado do cliente = item + taxa
    mens_cent        INTEGER NOT NULL DEFAULT 0 CHECK (mens_cent >= 0),      -- parte da mensalidade
    recupera_cent    INTEGER NOT NULL DEFAULT 0 CHECK (recupera_cent >= 0),  -- compensação do estorno
    plataforma_cent  INTEGER NOT NULL CHECK (plataforma_cent >= 0),          -- fica na conta do PubShow
    bar_cent         INTEGER NOT NULL DEFAULT 0 CHECK (bar_cent >= 0),       -- vai pro bar pelo split, na hora
    split_config_id  TEXT,
    split_ok         INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'criando',
        -- criando|ativa|erro|expirada|cancelada|paga|devolvendo|devolver_erro|devolvida|divergente
    copia_cola       TEXT,
    e2eid            TEXT,
    pago_cent        INTEGER,
    pago_em          TEXT,
    pix_horario      TEXT,
    atrasado         INTEGER NOT NULL DEFAULT 0,       -- PIX caiu depois do prazo / pedido cancelado
    estornar         INTEGER NOT NULL DEFAULT 0,       -- devolução pedida (o reconciliador termina)
    motivo_estorno   TEXT,
    devolucao_id     TEXT,
    devolucao_status TEXT,
    dev_tentativas   INTEGER NOT NULL DEFAULT 0,
    dev_checagens    INTEGER NOT NULL DEFAULT 0,       -- CC1 X5: conferências sem resposta da devolução atual
    devolvido_em     TEXT,
    dev_pedida_em    TEXT,                             -- 17/10/26 (decisão 3): quando a devolução do PAGO começou
    dev_aprovacao    TEXT,                             -- 'pendente' (passou do teto) | 'aprovada:<quem>' | 'negada:<quem>'
    expira_em        TEXT NOT NULL,
    ultima_consulta  TEXT,
    erro             TEXT,
    criado_em        TEXT NOT NULL,
    atualizado_em    TEXT,
    CHECK (valor_cent = item_cent + conv_cent),
    CHECK (plataforma_cent = conv_cent + mens_cent + recupera_cent),
    -- split-only (16/10/26): cada centavo tem dono — o que não é do PubShow vai pro bar pelo split
    CHECK (plataforma_cent + bar_cent = valor_cent)
);
CREATE INDEX IF NOT EXISTS idx_pspay_cob_status ON pubshow_pay_cobrancas(status, criado_em);
CREATE INDEX IF NOT EXISTS idx_pspay_cob_bar    ON pubshow_pay_cobrancas(business_id, competencia, status);

-- Livro-caixa IMUTÁVEL. Saldo = SUM(valor_cent). + entra, - sai. ref = idempotência.
CREATE TABLE IF NOT EXISTS pubshow_ledger (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id  INTEGER NOT NULL,
    conta        TEXT NOT NULL CHECK (conta IN ('casa','gorjeta','plataforma')),
    tipo         TEXT NOT NULL,
        -- credito|taxa_servico|mensalidade|repasse_split|estorno|estorno_taxa|estorno_mensalidade|ajuste
    valor_cent   INTEGER NOT NULL CHECK (typeof(valor_cent) = 'integer' AND valor_cent <> 0),
    ref          TEXT NOT NULL UNIQUE,
    pedido_id    INTEGER,
    cobranca_id  INTEGER,
    competencia  TEXT NOT NULL,
    obs          TEXT,
    criado_em    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_psledger_bar ON pubshow_ledger(business_id, conta, competencia);
CREATE TRIGGER IF NOT EXISTS pubshow_ledger_noupd BEFORE UPDATE ON pubshow_ledger
    BEGIN SELECT RAISE(ABORT, 'ledger imutavel'); END;
CREATE TRIGGER IF NOT EXISTS pubshow_ledger_nodel BEFORE DELETE ON pubshow_ledger
    BEGIN SELECT RAISE(ABORT, 'ledger imutavel'); END;

-- Conta Efí do bar (favorecida do split). Titular = CPF/CNPJ do cadastro do bar.
CREATE TABLE IF NOT EXISTS pubshow_pay_contas (
    business_id   INTEGER PRIMARY KEY,
    efi_conta     TEXT NOT NULL,
    titular_doc   TEXT NOT NULL,
    titular_nome  TEXT,
    ativo         INTEGER NOT NULL DEFAULT 1,
    criado_em     TEXT NOT NULL,
    atualizado_em TEXT
);

-- Exceções por bar (opcional): preço combinado (ex.: bar demo isento = 0). NULL = o preço do bar (cadastro).
-- garantia_inicio: sem efeito desde 16/10/26 (Lote D A2: a garantia saiu); a coluna fica pros bancos que já têm.
CREATE TABLE IF NOT EXISTS pubshow_pay_planos (
    business_id      INTEGER PRIMARY KEY,
    plano_cent       INTEGER CHECK (plano_cent IS NULL OR plano_cent >= 0),
    garantia_inicio  TEXT,                               -- 'AAAA-MM-DD' (Brasília) — sem efeito (Lote D)
    obs              TEXT,
    atualizado_em    TEXT NOT NULL
);

-- Lote D A3 (16/10/26): pausa de 60 dias sem momento pago (só modo Efí). desde = 1ª vez que o sistema viu o bar
-- na Efí (o relógio começa aí); aviso_em = aviso dos 7 dias já mandado neste ciclo; reativado_em = botão do admin.
CREATE TABLE IF NOT EXISTS pubshow_pay_pausa (
    business_id    INTEGER PRIMARY KEY,
    desde          TEXT NOT NULL,
    aviso_em       TEXT,
    reativado_em   TEXT,
    reativado_por  TEXT,
    visto_em       TEXT                                -- 17/10/26 (CD2): última vez que o robô viu o bar na Efí
);

-- Configs de split já criadas na Efí (id determinístico: mesma divisão = mesma config).
CREATE TABLE IF NOT EXISTS pubshow_pay_splitcfg (
    id               TEXT PRIMARY KEY,
    business_id      INTEGER NOT NULL,
    efi_conta        TEXT NOT NULL,
    plataforma_cent  INTEGER NOT NULL,
    bar_cent         INTEGER NOT NULL,
    criado_em        TEXT NOT NULL
);
'''


def init_tables(conn):
    """Cria as tabelas do pagamento novo (idempotente). Não mexe em nenhuma tabela do pubshow_db."""
    conn.executescript(_SCHEMA)
    # 16/10/26 (CC1 X5): coluna nova em banco que já tinha a tabela (bar demo de teste)
    if 'dev_checagens' not in {r[1] for r in conn.execute('PRAGMA table_info(pubshow_pay_cobrancas)')}:
        conn.execute('ALTER TABLE pubshow_pay_cobrancas ADD COLUMN dev_checagens INTEGER NOT NULL DEFAULT 0')
    # 17/10/26 (decisão 3): teto de devolução por dono (banco que já tinha a tabela)
    _cols = {r[1] for r in conn.execute('PRAGMA table_info(pubshow_pay_cobrancas)')}
    for _c in ('dev_pedida_em', 'dev_aprovacao'):
        if _c not in _cols:
            conn.execute(f'ALTER TABLE pubshow_pay_cobrancas ADD COLUMN {_c} TEXT')
    # 17/10/26 (Lote D, críticos CD2): visto_em da pausa (banco que já tinha a tabela do Lote D)
    if 'visto_em' not in {r[1] for r in conn.execute('PRAGMA table_info(pubshow_pay_pausa)')}:
        conn.execute('ALTER TABLE pubshow_pay_pausa ADD COLUMN visto_em TEXT')
    conn.commit()


def _uma(conn, sql, args=()):
    cur = conn.execute(sql, args)
    r = cur.fetchone()
    if r is None:
        return None
    return {d[0]: r[i] for i, d in enumerate(cur.description)}


def _todas(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [{c: r[i] for i, c in enumerate(cols)} for r in cur.fetchall()]


def _soma(conn, sql, args=()):
    r = conn.execute(sql, args).fetchone()
    return int((r[0] if r else 0) or 0)


@contextmanager
def _imediata(conn):
    """BEGIN IMMEDIATE: trava de escrita ANTES de ler (2 confirmações ao mesmo tempo = 1 vence)."""
    if conn.in_transaction:
        raise RuntimeError('pubshow_pay: a conexão já tem transação aberta (dê commit antes)')
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def _exec_commit(conn, sql, args=()):
    """Escrita avulsa (fora de transação) com commit na hora."""
    if conn.in_transaction:
        raise RuntimeError('pubshow_pay: a conexão já tem transação aberta (dê commit antes)')
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.rowcount


def _lanca(conn, bid, conta, tipo, valor_cent, ref, comp, agora, pedido_id=None,
           cobranca_id=None, obs=None):
    """1 linha no livro-caixa. valor 0 não grava. INSERT puro: ref repetido = erro (bug), nunca 2×."""
    if isinstance(valor_cent, bool) or not isinstance(valor_cent, int):
        raise TypeError(f'valor em centavos tem que ser int (veio {type(valor_cent).__name__})')
    if conta not in CONTAS:
        raise ValueError(f'conta inválida: {conta}')
    if valor_cent == 0:
        return
    conn.execute(
        '''INSERT INTO pubshow_ledger (business_id, conta, tipo, valor_cent, ref, pedido_id,
               cobranca_id, competencia, obs, criado_em)
           VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (int(bid), conta, tipo, valor_cent, ref, pedido_id, cobranca_id, comp, obs, _fmt(agora)))


# ══ Cliente Efí (conta SEPARADA do PubShow: envs PUBSHOW_EFI_*) ═══════════════════════════
_cli = None


def cliente():
    """16/10/26 (Lote C): efi_client.py (novo). O efi_pix.py da RifaJá fica intocado (= cd052dd)."""
    global _cli
    if _cli is None:
        import efi_client
        _cli = efi_client.EfiClient('PUBSHOW_EFI_')
    return _cli


def set_cliente(cli):
    """Troca o cliente Efí (testes usam um falso). None = volta pro padrão."""
    global _cli
    _cli = cli


# ══ Conta Efí do bar (passo 4 do tutorial) ════════════════════════════════════════════════
def _so_digitos(s):
    return re.sub(r'\D', '', str(s or ''))


def _doc_norm(s):
    """CPF/CNPJ sem pontuação e em maiúscula. 16/10/26 (V3-08): CNPJ novo pode ter LETRAS
    (IN RFB 2.229/2024, inscrições a partir de jul/2026): 12 letras/números + 2 DV numéricos."""
    return re.sub(r'[^0-9A-Z]', '', str(s or '').upper())


def doc_valido(doc):
    """CPF (11 números) ou CNPJ (14, numérico ou alfanumérico) com dígito verificador certo."""
    d = _doc_norm(doc)
    if len(d) == 11 and d.isdigit() and d != d[0] * 11:
        for n in (9, 10):
            s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
            if int(d[n]) != (s * 10 % 11) % 10:
                return False
        return True
    if len(d) == 14 and d[12:].isdigit() and d != d[0] * 14:
        for n in (12, 13):
            pesos = ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if n == 12 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
            s = sum((ord(d[i]) - 48) * pesos[i] for i in range(n))   # letra vale ASCII − 48 (A=17); número igual a antes
            r = s % 11
            if int(d[n]) != (0 if r < 2 else 11 - r):
                return False
        return True
    return False


def salvar_conta_efi(conn, business_id, efi_conta, titular_doc, doc_cadastro, titular_nome='', agora=None,
                     conferir=False, cli=None):
    """Liga a conta Efí do bar. O titular TEM que ser o CPF/CNPJ do cadastro do bar (C7):
    conta de outra pessoa é recusada aqui. A Efí confere de novo ao criar o split [L: erro
    'documento ... não condiz com o documento real da conta'].
    conferir=True (VCb-01, 16/10/26): ANTES de gravar, cria na Efí uma config de split de verdade com essa conta
    (a mesma rotina do _split_cfg, divisão típica de um momento). A Efí recusou (HTTP 400/404/422) →
    {'erro': 'efi_recusou_conta'} e nada muda (a conta antiga, se havia, fica). A Efí não respondeu (rede,
    5xx, credencial) → grava assim mesmo com 'conferida': False (o 1º momento pago confirma)."""
    agora = _agora(agora)
    conta = _so_digitos(efi_conta)
    doc = _doc_norm(titular_doc)
    if not (4 <= len(conta) <= 15):                       # formato do nº da conta Efí [H]
        return {'erro': 'conta_invalida'}
    if not doc_valido(doc):
        return {'erro': 'documento_invalido'}
    if doc != _doc_norm(doc_cadastro):
        return {'erro': 'titular_diferente_do_cadastro'}
    # 17/10/26 (decisão 3c do Diogo): conta Efí (ou titular) com dívida de estorno aberta em OUTRO bar não conecta
    # aqui. Antes: devolvia pro próprio bolso, abria o 2º bar com a mesma conta e a dívida ficava no 1º.
    outros = {int(r['business_id']) for r in _todas(
        conn, 'SELECT business_id, efi_conta, titular_doc FROM pubshow_pay_contas WHERE business_id<>?', (int(business_id),))
        if _so_digitos(r['efi_conta']) == conta or _doc_norm(r['titular_doc']) == doc}
    devendo = sum(divida_dono_cent(conn, o) for o in sorted(outros)[:1])   # o grupo de 1 já soma todos os bares do dono
    if devendo > 0:
        log.warning('[PubShow pay] conta Efí %s recusada no bar %s: dívida de estorno aberta (%s centavos) em outro bar',
                    conta, business_id, devendo)
        return {'erro': 'conta_com_divida', 'divida_cent': devendo}
    conferida = False
    if conferir:
        falha = {}
        cfg = _split_cfg(conn, cli or cliente(), int(business_id), {'efi_conta': conta, 'titular_doc': doc},
                         CONFERE_PLAT_CENT, CONFERE_BAR_CENT, agora, falha=falha)
        if not cfg and falha.get('http') in (400, 404, 422):
            log.warning('[PubShow pay] a Efí recusou a conta %s do bar %s: %s', conta, business_id, falha.get('erro'))
            return {'erro': 'efi_recusou_conta', 'detalhe': str(falha.get('erro') or '')[:200]}
        conferida = bool(cfg)
    _exec_commit(conn,
        '''INSERT INTO pubshow_pay_contas (business_id, efi_conta, titular_doc, titular_nome, ativo, criado_em, atualizado_em)
           VALUES (?,?,?,?,1,?,?)
           ON CONFLICT(business_id) DO UPDATE SET efi_conta=excluded.efi_conta, titular_doc=excluded.titular_doc,
               titular_nome=excluded.titular_nome, ativo=1, atualizado_em=excluded.atualizado_em''',
        (int(business_id), conta, doc, (titular_nome or '')[:120], _fmt(agora), _fmt(agora)))
    # VCb-01: o dono salvou a conta de novo (corrigiu) → as falhas de split de antes deixam de acender o aviso
    # do painel (split_recusado). Só o rótulo do erro muda; a cobrança segue 'erro', sem dinheiro nenhum.
    _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET erro='split_antes_da_correcao' || substr(erro, 6)
                          WHERE business_id=? AND status='erro' AND erro LIKE 'split:%' ''', (int(business_id),))
    return {'ok': True, 'efi_conta': conta, 'titular_doc': doc, 'conferida': conferida}


def grupo_dono(conn, business_id):
    """17/10/26 (decisão 3 do Diogo): os bares do MESMO dono = o mesmo CPF/CNPJ (do cadastro ou do titular da conta
    Efí) ou a mesma conta Efí. "O bar deve" (estorno) e o teto de devolução contam pelo dono, não por bar: abrir o
    2º bar (A6) não apaga a dívida do 1º, e os momentos de qualquer bar dele compensam. Devolve a lista de ids."""
    bid = int(business_id)
    docs, contas = set(), set()
    b = _bar_por_id(conn, bid)
    if b and b.get('cpf_cnpj'):
        docs.add(_doc_norm(b['cpf_cnpj']))
    for c in _todas(conn, 'SELECT efi_conta, titular_doc FROM pubshow_pay_contas WHERE business_id=?', (bid,)):
        docs.add(_doc_norm(c['titular_doc']))
        contas.add(_so_digitos(c['efi_conta']))
    docs.discard('')
    contas.discard('')
    ids = {bid}
    for r in _todas(conn, 'SELECT business_id, efi_conta, titular_doc FROM pubshow_pay_contas'):
        if _doc_norm(r['titular_doc']) in docs or _so_digitos(r['efi_conta']) in contas:
            ids.add(int(r['business_id']))
    if docs:
        try:
            for r in _todas(conn, "SELECT id, cpf_cnpj FROM pubshow_businesses WHERE COALESCE(cpf_cnpj,'')<>''"):
                if _doc_norm(r['cpf_cnpj']) in docs:
                    ids.add(int(r['id']))
        except sqlite3.OperationalError:
            pass   # testes do módulo sem a tabela dos bares
    return sorted(ids)


def _in(ids):
    return ','.join('?' * len(ids))


def divida_dono_cent(conn, business_id, conta=None):
    """Quanto o dono deve de estorno (centavos, >= 0), somando os bares dele (grupo_dono). conta=None = casa + gorjeta."""
    g = grupo_dono(conn, business_id)
    contas = (conta,) if conta else ('casa', 'gorjeta')
    return sum(max(0, -_soma(conn, f'SELECT SUM(valor_cent) FROM pubshow_ledger WHERE business_id IN ({_in(g)}) '
                                    'AND conta=?', (*g, c))) for c in contas)


def conta_do_bar(conn, business_id):
    return _uma(conn, 'SELECT * FROM pubshow_pay_contas WHERE business_id=? AND ativo=1', (int(business_id),))


def conta_aceita(conn, business_id):
    """CC1 X7 (16/10/26): a Efí já ACEITOU a conta atual do bar (existe config de split criada com ela: no passo 4
    com conferir=True ou num momento pago)? Conta aceita só troca pelo suporte (o painel recusa trocar em silêncio)."""
    c = conta_do_bar(conn, business_id)
    if not c:
        return False
    return bool(_uma(conn, 'SELECT id FROM pubshow_pay_splitcfg WHERE business_id=? AND efi_conta=? LIMIT 1',
                     (int(business_id), c['efi_conta'])))


def split_recusado(conn, business_id):
    """VCb-01 (16/10/26): a Efí recusou o split da conta do bar no último momento que chegou ao split?
    Olha a última cobrança com parte do bar (bar_cent > 0) que chegou ao split. Cobrança que caiu antes, na
    criação do QR ('gateway'), não conta. Salvar a conta de novo (salvar_conta_efi) re-rotula as falhas antigas
    ('split_antes_da_correcao'): o aviso some até a próxima falha. Devolve o dict da cobrança que falhou, ou None."""
    if not conta_do_bar(conn, business_id):
        return None
    r = _uma(conn, """SELECT id, pedido_id, status, erro, criado_em FROM pubshow_pay_cobrancas
                      WHERE business_id=? AND bar_cent > 0 AND status != 'criando'
                        AND (status != 'erro' OR erro LIKE 'split%')
                      ORDER BY id DESC LIMIT 1""", (int(business_id),))
    return r if r and r['status'] == 'erro' and (r['erro'] or '').startswith('split:') else None


# ══ "O app se paga": quanto de cada momento fica com o PubShow ════════════════════════════
def _bar_por_id(conn, business_id):
    """Linha do bar no pubshow_businesses (o mesmo pubshow.db), ou None (tabela não existe nos testes do módulo)."""
    try:
        return _uma(conn, 'SELECT * FROM pubshow_businesses WHERE id=?', (int(business_id),))
    except sqlite3.OperationalError:
        return None


def definir_plano(conn, business_id, plano_cent=None, garantia_inicio=None, obs='', agora=None):
    """Exceção por bar: plano_cent (centavos; 0 = isento; None = o preço do bar). Sobrescreve a exceção anterior.
    garantia_inicio: aceito por compatibilidade e SEM efeito (Lote D A2, 16/10/26: a garantia saiu).
    Lote D A1: bar fundador (preco_trava_ate no futuro) não sobe acima do preço travado antes da data
    ({'erro': 'fundador_preco_travado'}); baixar (isento, teste do bar demo) pode."""
    agora = _agora(agora)
    if plano_cent is not None and (isinstance(plano_cent, bool) or int(plano_cent) != plano_cent or plano_cent < 0):
        return {'erro': 'plano_invalido'}
    if garantia_inicio is not None:
        try:
            datetime.strptime(str(garantia_inicio), '%Y-%m-%d')
        except ValueError:
            return {'erro': 'data_invalida (use AAAA-MM-DD)'}
    b = _bar_por_id(conn, business_id)
    if b and plano_cent is not None and b.get('preco_trava_ate') and b.get('plano_cent') is not None:
        hoje = _agora(agora).replace(tzinfo=timezone.utc).astimezone(BRT).strftime('%Y-%m-%d')
        if str(b['preco_trava_ate'])[:10] >= hoje and int(plano_cent) > int(b['plano_cent']):
            return {'erro': 'fundador_preco_travado', 'travado_cent': int(b['plano_cent']),
                    'ate': str(b['preco_trava_ate'])[:10]}
    _exec_commit(conn, '''INSERT INTO pubshow_pay_planos (business_id, plano_cent, garantia_inicio, obs, atualizado_em)
                          VALUES (?,?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET plano_cent=excluded.plano_cent,
                          garantia_inicio=excluded.garantia_inicio, obs=excluded.obs, atualizado_em=excluded.atualizado_em''',
                 (int(business_id), None if plano_cent is None else int(plano_cent), garantia_inicio,
                  (obs or '')[:200], _fmt(agora)))
    return {'ok': True}


def _bar_com_excecoes(conn, bar):
    """dict(bar) + as exceções de pubshow_pay_planos (plano_cent / garantia_inicio), quando existem.
    Lote D A1 (16/10/26): veio só o id (resumo_admin)? Lê o preço guardado do bar no pubshow_businesses."""
    d = dict(bar)
    if 'plano_cent' not in d:
        _b = _bar_por_id(conn, d['id'])
        if _b and _b.get('plano_cent') is not None:
            d['plano_cent'] = _b['plano_cent']
    ex = _uma(conn, 'SELECT plano_cent, garantia_inicio FROM pubshow_pay_planos WHERE business_id=?', (int(d['id']),))
    if ex:
        if ex['plano_cent'] is not None:
            d['plano_cent'] = ex['plano_cent']
        if ex['garantia_inicio']:
            d['garantia_inicio'] = ex['garantia_inicio']
    return d


def plano_do_bar(conn, bar):
    """Mensalidade do bar em centavos, com a exceção de pubshow_pay_planos (0 = isento). CC2 (16/10/26): o painel
    usa no texto do passo 4 ("os primeiros R$ X de momentos do mês pagam a mensalidade")."""
    return plano_cent(_bar_com_excecoes(conn, bar))


def estado_bar(conn, business_id, mes, agora=None):
    """O que a parte_plataforma() precisa saber, lido do livro-caixa (+ cobranças abertas, que
    RESERVAM a sua parte: 2 cobranças ao mesmo tempo nunca cobram a mensalidade 2×).
    CC1 X2 (16/10/26): a 'expirada' há menos de RESERVA_POS_PRAZO_S também reserva (o PIX dela ainda pode entrar)."""
    _mes_ok(mes)
    bid = int(business_id)
    _res = f"(status IN {ST_RESERVA} OR (status='expirada' AND expira_em > ?))"
    _lim = _fmt(_agora(agora) - timedelta(seconds=RESERVA_POS_PRAZO_S))
    paga = _soma(conn, '''SELECT SUM(valor_cent) FROM pubshow_ledger WHERE business_id=? AND conta='plataforma'
                          AND tipo IN ('mensalidade','estorno_mensalidade') AND competencia=?''', (bid, mes))
    reservada = _soma(conn, f'''SELECT SUM(mens_cent) FROM pubshow_pay_cobrancas WHERE business_id=?
                               AND competencia=? AND {_res}''', (bid, mes, _lim))
    out = {'id': bid, 'mes': mes, 'mens_paga_cent': paga + reservada}
    # 17/10/26 (decisão 3a): "o bar deve" é do DONO (CPF/CNPJ + conta Efí): soma os bares dele
    g = grupo_dono(conn, bid)
    for conta in ('casa', 'gorjeta'):
        saldo_c = _soma(conn, f'SELECT SUM(valor_cent) FROM pubshow_ledger WHERE business_id IN ({_in(g)}) AND conta=?',
                        (*g, conta))
        res_rec = _soma(conn, f'''SELECT SUM(recupera_cent) FROM pubshow_pay_cobrancas WHERE business_id IN ({_in(g)})
                                 AND conta=? AND {_res}''', (*g, conta, _lim))
        out[f'divida_{conta}_cent'] = max(0, -saldo_c - res_rec)
    return out


def parte_plataforma(bar, mes, item_cent, conv_cent, tipo, plano=None, pct=None):
    """FUNÇÃO PURA. Divide 1 cobrança (item + taxa de serviço) entre PubShow e bar (split).
    bar: dict com 'mens_paga_cent' (mensalidade já abatida no mês, com as reservas) e
         'divida_casa_cent' / 'divida_gorjeta_cent' (o bar deve por estorno). estado_bar() monta isso.
    Regras:
      - taxa de serviço ("R$ 1 do app"): sempre do PubShow;
      - mensalidade: min(30% do item, o que falta do mês até o plano). GORJETA = 0, sempre.
        16/10/26 (Lote D A2): era min(item, falta) — os primeiros R$ 49,90 do mês iam inteiros pro PubShow.
        Agora o bar recebe 70% desde o 1º momento. 30% = PUBSHOW_MENSAL_PCT (pct= sobrescreve, nos testes);
        centavo quebrado arredonda pra BAIXO (fica com o bar);
      - compensação: o que o bar deve, tirado só da MESMA conta (casa paga dívida da casa,
        gorjeta só da gorjeta) e nunca além do que sobrou do item;
      - o resto (bar_cent) cai na conta Efí do bar pelo split, na hora.
    Mês que não fecha a mensalidade: o bar pagou só os 30% (nunca tira do bolso; não existe cobrança da diferença)."""
    _mes_ok(mes)
    keys = bar.keys() if hasattr(bar, 'keys') else []
    if 'mes' in keys and bar['mes'] and bar['mes'] != mes:
        raise ValueError(f'estado do bar é de {bar["mes"]}, não de {mes}')
    if isinstance(item_cent, bool) or isinstance(conv_cent, bool):
        raise TypeError('centavos não podem ser bool')
    item, conv = int(item_cent), int(conv_cent)
    if item != item_cent or conv != conv_cent:
        raise TypeError('centavos têm que ser inteiros')
    if item <= 0 or conv < 0:
        raise ValueError('item tem que ser > 0 e taxa >= 0')
    conta = 'gorjeta' if tipo in TIPOS_EQUIPE else 'casa'
    pl = plano_cent(bar) if plano is None else max(0, int(plano))
    pc = mensal_pct() if pct is None else min(100, max(0, int(pct)))
    mens = 0
    if conta == 'casa':
        ja = int(bar['mens_paga_cent']) if 'mens_paga_cent' in keys and bar['mens_paga_cent'] else 0
        mens = min(item * pc // 100, max(0, pl - ja))
    k = f'divida_{conta}_cent'
    div = int(bar[k]) if k in keys and bar[k] else 0
    rec = min(item - mens, max(0, div))
    plat = conv + mens + rec
    total = item + conv
    return {'conta': conta, 'item_cent': item, 'conv_cent': conv,
            'mensalidade_cent': mens, 'recupera_cent': rec, 'plataforma_cent': plat,
            'bar_cent': total - plat, 'valor_cobrado_cent': total}


# ══ Cobrança ══════════════════════════════════════════════════════════════════════════════
def _novo_txid(pedido_id):
    base = f'ps{int(pedido_id)}x'
    return (base + secrets.token_hex(16))[:32]


def _saida(row, novo):
    return {'ok': True, 'novo': novo, 'cobranca_id': row['id'], 'pedido_id': row['pedido_id'],
            'txid': row['txid'], 'copia_cola': row['copia_cola'] or '', 'valor_cent': row['valor_cent'],
            'item_cent': row['item_cent'], 'conv_cent': row['conv_cent'], 'expira_em': row['expira_em'],
            'conta': row['conta'],
            'parte': {'plataforma_cent': row['plataforma_cent'], 'mensalidade_cent': row['mens_cent'],
                      'recupera_cent': row['recupera_cent'], 'bar_cent': row['bar_cent']}}


def _split_cfg(conn, cli, bid, conta_efi, plat, bar_cent, agora, forcar=False, falha=None):
    """Config de split FIXA (minha parte = plat, bar = bar_cent). Mesma divisão = mesmo id = reaproveita.
    15/10/26 (V7-05): a chave inclui a CONTA Efí do PubShow (base + client_id): trocar homologação →
    produção ou de conta gera config nova. forcar=True apaga o cache e manda o PUT de novo (mesmo id =
    atualiza na Efí [L])."""
    conta_pub = f"{getattr(cli, 'base', '') or ''}|{getattr(cli, 'client_id', '') or ''}"
    chave = f"{conta_pub}|{bid}|{conta_efi['efi_conta']}|{conta_efi['titular_doc']}|{plat}|{bar_cent}"
    cfg_id = 'pss' + hashlib.sha256(chave.encode()).hexdigest()[:29]
    if forcar:
        _exec_commit(conn, 'DELETE FROM pubshow_pay_splitcfg WHERE id=?', (cfg_id,))
    elif _uma(conn, 'SELECT id FROM pubshow_pay_splitcfg WHERE id=?', (cfg_id,)):
        return cfg_id
    r = cli.split_config(cfg_id, plat, [{'conta': conta_efi['efi_conta'], 'doc': conta_efi['titular_doc'],
                                         'valor_cent': bar_cent}], descricao=f'PubShow bar {bid}')
    if r.get('erro'):
        log.warning('[PubShow pay] split_config %s falhou: %s', cfg_id, r.get('erro'))
        if falha is not None:          # VCb-01: quem chama quer saber o porquê (conferir a conta do bar)
            falha.update(r)
        return None
    _exec_commit(conn, '''INSERT OR IGNORE INTO pubshow_pay_splitcfg (id, business_id, efi_conta, plataforma_cent,
                         bar_cent, criado_em) VALUES (?,?,?,?,?,?)''',
                 (cfg_id, bid, conta_efi['efi_conta'], plat, bar_cent, _fmt(agora)))
    return cfg_id


def criar_cobranca_momento(conn, bar, pedido_id, item_cent, conv_cent=None, tipo='brinde', descricao='',
                           cli=None, agora=None, sem_mensalidade=False):
    """Cria (ou devolve, se já existe e está ativa) a cobrança Efí de 1 pedido pago.
    bar: linha do pubshow_businesses (precisa de 'id'; 'plano_cent' opcional).
    sem_mensalidade=True (VCa-01/02, 16/10/26): esta cobrança não tira mensalidade (bar que já paga no Asaas
    ou que cancelou e segue aberto só até o fim do mês pago). Taxa de serviço e compensação seguem iguais.
    Retorna {'ok', 'txid', 'copia_cola', 'valor_cent', 'expira_em', 'parte', ...} ou {'erro'}.
    Falhou? O pedido não tem QR: a tela mostra "Pagamento indisponível, tenta de novo" e um pedido NOVO
    gera outra cobrança (cada pedido tem no máximo 1 cobrança)."""
    agora = _agora(agora)
    cli = cli or cliente()
    bid = int(bar['id'])
    pid = int(pedido_id)
    if isinstance(item_cent, bool) or int(item_cent) != item_cent or int(item_cent) <= 0:
        return {'erro': 'valor_invalido'}
    item = int(item_cent)
    if item > TETO_ITEM_CENT:
        # Lote D A4 (16/10/26): 2ª trava do teto (o pubshow.py já corta o preço): nunca cobra acima de R$ 50
        return {'erro': 'acima_do_teto', 'teto_cent': TETO_ITEM_CENT}
    conv = conv_cent_para(tipo) if conv_cent is None else int(conv_cent)
    mes = competencia(agora)

    ex = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE pedido_id=?', (pid,))
    if ex:
        if ex['business_id'] != bid:
            return {'erro': 'pedido_de_outro_bar'}
        if ex['status'] == 'ativa' and ex['expira_em'] > _fmt(agora):
            return _saida(ex, novo=False)
        return {'erro': 'pedido_ja_tem_cobranca', 'status': ex['status']}

    # split-only (16/10/26): sem a conta Efí do bar não há cobrança — o dinheiro dele nunca cai no PubShow
    conta_efi = conta_do_bar(conn, bid)
    if not conta_efi:
        return {'erro': 'bar_sem_conta_efi'}

    # 1) RESERVA (transação curta, sem rede): divide e grava 'criando'
    txid = _novo_txid(pid)
    try:
        with _imediata(conn):
            est = estado_bar(conn, bid, mes, agora)
            base = _bar_com_excecoes(conn, bar)
            base.update(est)
            p = parte_plataforma(base, mes, item, conv, tipo, plano=0 if sem_mensalidade else None)
            conn.execute(
                '''INSERT INTO pubshow_pay_cobrancas (pedido_id, business_id, txid, tipo, conta,
                       competencia, item_cent, conv_cent, valor_cent, mens_cent, recupera_cent, plataforma_cent,
                       bar_cent, status, expira_em, criado_em, atualizado_em)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'criando',?,?,?)''',
                (pid, bid, txid, tipo, p['conta'], mes, item, conv, p['valor_cobrado_cent'],
                 p['mensalidade_cent'], p['recupera_cent'], p['plataforma_cent'], p['bar_cent'],
                 _fmt(agora + timedelta(seconds=expira_s())), _fmt(agora), _fmt(agora)))
    except sqlite3.IntegrityError:
        ex = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE pedido_id=?', (pid,))
        if ex and ex['status'] == 'ativa':
            return _saida(ex, novo=False)
        return {'erro': 'pedido_ja_tem_cobranca', 'status': ex['status'] if ex else None}
    row = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE txid=?', (txid,))

    def _falhou(motivo, detalhe=''):
        _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET status='erro', erro=?, atualizado_em=?
                              WHERE id=? AND status='criando' ''', (f'{motivo}: {detalhe}'[:300], _fmt(agora), row['id']))
        log.warning('[PubShow pay] cobrança do pedido %s falhou (%s): %s', pid, motivo, detalhe)
        return {'erro': motivo, 'detalhe': str(detalhe)[:200]}

    # 2) Efí: cobrança com o NOSSO txid (re-tentar com o mesmo txid nunca duplica)
    r = cli.criar_cobranca_txid(txid, row['valor_cent'], descricao, expira=expira_s(), timeout=6)
    if r.get('erro'):
        try:
            cli.cancelar_cobranca(txid)          # se o timeout escondeu uma cobrança criada, ela não fica viva
        except Exception:
            pass
        return _falhou('gateway', r.get('erro'))

    # 3) split: só quando o bar tem parte nesta cobrança
    cfg_id, split_ok = None, 0
    if row['bar_cent'] > 0:
        cfg_id = _split_cfg(conn, cli, bid, conta_efi, row['plataforma_cent'], row['bar_cent'], agora)
        v = cli.split_vincular(txid, cfg_id) if cfg_id else {'erro': 'sem config de split'}
        if v.get('erro') and cfg_id:
            # V7-05: a config do cache pode não existir (ou estar inativa) na Efí: recria 1 vez e re-tenta
            cfg_id = _split_cfg(conn, cli, bid, conta_efi, row['plataforma_cent'], row['bar_cent'], agora, forcar=True)
            v = cli.split_vincular(txid, cfg_id) if cfg_id else {'erro': 'sem config de split'}
        if v.get('erro'):
            # Sem split NÃO mostra o QR: o dinheiro do bar nunca cai na conta do PubShow.
            try:
                cli.cancelar_cobranca(txid)
            except Exception:
                pass
            return _falhou('split', v.get('erro'))
        split_ok = 1

    _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET status='ativa', copia_cola=?, split_config_id=?,
                          split_ok=?, atualizado_em=? WHERE id=? AND status='criando' ''',
                 (r.get('copia_cola', ''), cfg_id, split_ok, _fmt(agora), row['id']))
    row = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE id=?', (row['id'],))
    if row['status'] != 'ativa':
        # cancelaram o pedido enquanto a Efí respondia: o QR não sai e a cobrança não fica viva
        try:
            cli.cancelar_cobranca(txid)
        except Exception:
            pass
        return {'erro': 'cobranca_mudou_de_status', 'status': row['status']}
    return _saida(row, novo=True)


# ══ Confirmação (idempotente; 2 threads ao mesmo tempo = 1 lançamento) ════════════════════
def _lanca_pagamento(conn, row, agora):
    bid, pid, cid, comp = row['business_id'], row['pedido_id'], row['id'], row['competencia']
    conta = row['conta']
    _lanca(conn, bid, conta, 'credito', int(row['item_cent']), f'ped:{pid}', comp, agora, pid, cid)
    _lanca(conn, bid, 'plataforma', 'taxa_servico', int(row['conv_cent']), f'tx:{pid}', comp, agora, pid, cid)
    if row['mens_cent']:
        _lanca(conn, bid, 'casa', 'mensalidade', -int(row['mens_cent']), f'mc:{pid}', comp, agora, pid, cid)
        _lanca(conn, bid, 'plataforma', 'mensalidade', int(row['mens_cent']), f'mp:{pid}', comp, agora, pid, cid)
    if row['split_ok'] and row['bar_cent']:
        _lanca(conn, bid, conta, 'repasse_split', -int(row['bar_cent']), f'rsp:{pid}', comp, agora, pid, cid,
               obs='caiu na conta Efí do bar pelo split')


def _pago_no_prazo(horario, expira_em):
    h, e = _parse(horario), _parse(expira_em)
    if not h or not e:
        return False
    return h <= e + timedelta(seconds=GRACA_PRAZO_S)


def _confirmado_tarde(expira_em, horario_pix):
    """Lote D A4 + decisão 5 do Diogo (17/10/26): o cliente PAGOU mais de CONFIRMA_TARDE_S (30 min) depois do vencimento
    do QR? Conta do horário do PIX (o da Efí), não da nossa confirmação: se a consulta da Efí cai 50 min numa sexta, o
    PIX pago no prazo vai pra TV quando a confirmação chegar (nada de devolução em massa). Sem horário = não é tarde."""
    e, h = _parse(expira_em), _parse(horario_pix)
    return bool(e and h) and h > e + timedelta(seconds=CONFIRMA_TARDE_S)


def confirmar(conn, txid, cli=None, on_pago=None, agora=None, consulta=None, freio_s=0):
    """Consulta a cobrança na Efí e, se PAGA, numa transação BEGIN IMMEDIATE:
    cobrança 'paga' (UPDATE ... WHERE status IN abertos) e, SÓ com rowcount=1, os lançamentos no livro-caixa
    + on_pago(conn, cobranca) — callback do pubshow.py que muda o pedido (sem commit; devolve True se o
    pedido aceitou o pagamento). PIX atrasado, pedido cancelado ou on_pago=False → devolução automática.
    freio_s: a tela do cliente passa 5 → no máximo 1 consulta à Efí a cada 5 s por cobrança.
    Retorna {'ok', 'pago', 'novo', 'status', 'estorno'?}."""
    agora = _agora(agora)
    row = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE txid=?', (txid,))
    if not row:
        return {'ok': False, 'erro': 'cobranca_desconhecida'}
    if row['status'] not in ST_CONFIRMAVEL:
        return {'ok': True, 'novo': False, 'pago': row['status'] in ST_PAGAS, 'status': row['status']}
    if freio_s and consulta is None and row['ultima_consulta']:
        ult = _parse(row['ultima_consulta'])
        if ult and agora - ult < timedelta(seconds=freio_s):
            return {'ok': True, 'pago': False, 'novo': False, 'status': row['status'], 'freio': True}
    cli = cli or cliente()
    c = consulta if consulta is not None else cli.consultar(txid, timeout=6)
    try:
        _exec_commit(conn, 'UPDATE pubshow_pay_cobrancas SET ultima_consulta=? WHERE id=?', (_fmt(agora), row['id']))
    except sqlite3.OperationalError:
        pass                                            # banco ocupado: só o carimbo, sem drama
    if c.get('erro'):
        return {'ok': False, 'erro': 'consulta', 'detalhe': c.get('erro'), 'status': row['status']}
    if not c.get('pago'):
        return {'ok': True, 'pago': False, 'novo': False, 'status': row['status']}

    pago_cent = int(c.get('pago_cent') or 0)
    if pago_cent != int(row['valor_cent']):
        _exec_commit(conn, f'''UPDATE pubshow_pay_cobrancas SET status='divergente', erro=?, atualizado_em=?
                               WHERE id=? AND status IN {ST_CONFIRMAVEL}''',
                     (f'pago {pago_cent} x cobrado {row["valor_cent"]}', _fmt(agora), row['id']))
        log.error('[PubShow pay] valor divergente txid=%s pago=%s cobrado=%s', txid, pago_cent, row['valor_cent'])
        return {'ok': False, 'erro': 'valor_divergente', 'status': 'divergente'}

    # 16/10/26 (CC3 R8): PIX já devolvido FORA do sistema (à mão no app da Efí, ex.: durante um rollback pro asaas).
    # Antes o robô via a cobrança CONCLUIDA, liberava o pedido (momento de graça na TV) e lançava o pagamento como
    # se o dinheiro ainda estivesse lá. Agora: grava 'devolvida' com o pagamento E o estorno no livro-caixa (a parte
    # do bar que caiu pelo split vira "o bar deve", igual à devolução do robô) e NÃO chama o on_pago.
    dev_fora = pago_cent > 0 and int(c.get('devolvido_cent') or 0) >= pago_cent
    novo, precisa_estorno, motivo = False, False, None
    with _imediata(conn):
        atual = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE id=?', (row['id'],))
        if dev_fora:
            cur = conn.execute(
                f'''UPDATE pubshow_pay_cobrancas SET status='devolvida', e2eid=?, pago_cent=?, pago_em=?, pix_horario=?,
                       estornar=1, motivo_estorno='devolvido_fora_do_sistema', devolucao_status='DEVOLVIDO_FORA',
                       devolvido_em=?, atualizado_em=?
                    WHERE id=? AND status IN {ST_CONFIRMAVEL}''',
                (c.get('e2eid') or '', pago_cent, _fmt(agora), c.get('horario') or '', _fmt(agora), _fmt(agora),
                 row['id']))
            if cur.rowcount == 1:
                novo = 'devolvido_fora'
                _lanca_pagamento(conn, atual, agora)
                _lanca_estorno(conn, dict(atual, motivo_estorno='devolvido_fora_do_sistema'), agora)
        else:
            if atual['status'] == 'cancelada':
                precisa_estorno, motivo = True, 'pedido_cancelado'
            elif atual['status'] == 'expirada' and not _pago_no_prazo(c.get('horario'), atual['expira_em']):
                precisa_estorno, motivo = True, 'pix_atrasado'
            elif _confirmado_tarde(atual['expira_em'], c.get('horario')):
                # Lote D A4 + decisão 5 (17/10/26): PAGO mais de 30 min depois de vencer → devolve. Pago no prazo e
                # confirmado tarde (pane da Efí, robô parado) NÃO devolve: vai pra fila quando a confirmação chegar.
                precisa_estorno, motivo = True, 'confirmado_tarde'
            cur = conn.execute(
                f'''UPDATE pubshow_pay_cobrancas SET status='paga', e2eid=?, pago_cent=?, pago_em=?, pix_horario=?,
                       atrasado=?, estornar=?, motivo_estorno=?, atualizado_em=?
                    WHERE id=? AND status IN {ST_CONFIRMAVEL}''',
                (c.get('e2eid') or '', pago_cent, _fmt(agora), c.get('horario') or '', 1 if precisa_estorno else 0,
                 1 if precisa_estorno else 0, motivo, _fmt(agora), row['id']))
            if cur.rowcount == 1:
                novo = True
                _lanca_pagamento(conn, atual, agora)
                if not precisa_estorno and on_pago is not None:
                    if not on_pago(conn, dict(atual, status='paga')):
                        precisa_estorno, motivo = True, 'pedido_nao_aceitou'
                        conn.execute('''UPDATE pubshow_pay_cobrancas SET estornar=1, motivo_estorno=? WHERE id=?''',
                                     (motivo, row['id']))
    if novo == 'devolvido_fora':
        log.warning('[PubShow pay] PIX do pedido %s já foi devolvido fora do sistema (app da Efí): não vai pra fila',
                    row['pedido_id'])
        return {'ok': True, 'novo': True, 'pago': True, 'status': 'devolvida', 'devolvido_fora': True}
    if not novo:
        st = _uma(conn, 'SELECT status FROM pubshow_pay_cobrancas WHERE id=?', (row['id'],))['status']
        return {'ok': True, 'novo': False, 'pago': st in ST_PAGAS, 'status': st}
    out = {'ok': True, 'novo': True, 'pago': True, 'status': 'paga'}
    if precisa_estorno:
        out['estorno'] = estornar(conn, row['pedido_id'], motivo=motivo, cli=cli, agora=agora)
        out['status'] = _uma(conn, 'SELECT status FROM pubshow_pay_cobrancas WHERE id=?', (row['id'],))['status']
    return out


# ══ Estorno (idempotente) ═════════════════════════════════════════════════════════════════
def _id_devolucao(pid, tentativa_nova):
    return f'psd{int(pid)}' if tentativa_nova <= 1 else f'psd{int(pid)}t{int(tentativa_nova)}'


def _finaliza_devolucao(conn, row_id, status_dev, agora):
    """DEVOLVIDO na Efí → cobrança 'devolvida' + lançamentos de volta (só com rowcount=1)."""
    with _imediata(conn):
        cur = conn.execute('''UPDATE pubshow_pay_cobrancas SET status='devolvida', devolucao_status=?, devolvido_em=?,
                                  atualizado_em=? WHERE id=? AND status='devolvendo' ''',
                           (status_dev, _fmt(agora), _fmt(agora), row_id))
        if cur.rowcount != 1:
            return False
        r = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE id=?', (row_id,))
        _lanca_estorno(conn, r, agora)
    return True


def _lanca_estorno(conn, r, agora):
    """Lançamentos da devolução de 1 cobrança (dentro da transação de quem chama). CC3 R8 (16/10/26): também
    usado pelo confirmar() quando o PIX já foi devolvido fora do sistema."""
    row_id = r['id']
    # 15/10/26 (V7-06): o estorno do ITEM e da taxa feito depois do fim do mês da cobrança entra no mês ATUAL ("o bar
    # deve" a parte que caiu nele, compensada nos próximos momentos).
    # 17/10/26 (decisão 4 do Diogo, crítico CD2 c1): a MENSALIDADE que o momento tinha pago volta a faltar no mês DA
    # COBRANÇA (o mês passado fica "não fechou"), nunca no mês atual: antes os momentos de outubro pagavam a diferença
    # de setembro (R$ 13 num plano de R$ 10). Mês que não fecha, o bar não paga a diferença.
    bid, pid = r['business_id'], r['pedido_id']
    comp = max(r['competencia'], competencia(agora))
    comp_mens = r['competencia']
    _lanca(conn, bid, r['conta'], 'estorno', -int(r['item_cent']), f'est:{pid}', comp, agora, pid, row_id,
           obs=r['motivo_estorno'])
    _lanca(conn, bid, 'plataforma', 'estorno_taxa', -int(r['conv_cent']), f'estx:{pid}', comp, agora, pid, row_id)
    if r['mens_cent']:
        # o momento devolvido não paga mensalidade: ela volta a faltar (no mês DA COBRANÇA, decisão 4)
        _lanca(conn, bid, 'casa', 'estorno_mensalidade', int(r['mens_cent']), f'emc:{pid}', comp_mens, agora, pid, row_id)
        _lanca(conn, bid, 'plataforma', 'estorno_mensalidade', -int(r['mens_cent']), f'emp:{pid}', comp_mens, agora,
               pid, row_id)
    # No split, o 'repasse_split' NÃO volta: a Efí debita só a conta do PubShow [L]. O saldo do bar
    # fica negativo (ele deve) e a parte_plataforma() compensa nas próximas cobranças da mesma conta.


def _recusa_definitiva(r):
    """Erro HTTP 4xx de verdade = a Efí NÃO fez a devolução. 409 (id já usado) nunca é recusa:
    pode ter saído. Sem 'http' (rede/timeout) e 5xx = desconhecido."""
    try:
        h = int(r.get('http') or 0)
    except Exception:
        h = 0
    return 400 <= h < 500 and h != 409 and not r.get('ja_existia')


def _dev_nao_encontrada(r):
    """GET da devolução respondeu 'não existe'. A doc da Efí (gestao-de-pix) dá HTTP 400
    'devolucao_nao_encontrada' [L]; 404 também vale. Outro 400 (parâmetro errado) NÃO conta."""
    h = str(r.get('http') or '')
    return h == '404' or (h == '400' and 'devolucao_nao_encontrada' in str(r.get('erro') or ''))


def _dev_pela_cobranca(cli, row):
    """Plano B (V7-03): lê a devolução na própria cobrança (GET /v2/cob/:txid → pix[].devolucoes[] com
    id e status [L padrão BCB]). None = não deu pra ler ou a devolução não aparece lá."""
    try:
        c = cli.consultar(row['txid'], timeout=6)
    except Exception:
        return None
    if not isinstance(c, dict) or c.get('erro'):
        return None
    for p in c.get('pix') or []:
        for d in (p.get('devolucoes') or []):
            if str(d.get('id') or '') == str(row['devolucao_id'] or ''):
                return {'id': d.get('id'), 'status': d.get('status'), 'rtrId': d.get('rtrId', ''),
                        'valor': d.get('valor', ''), 'motivo': d.get('motivo', ''), 'pela_cobranca': True}
    return None


def _proxima_tentativa_dev(row):
    """Quando o robô pode tentar de novo uma devolução recusada (V7-04): espera crescente
    (DEV_ESPERA_MIN), contada da última recusa (atualizado_em)."""
    n = max(1, int(row['dev_tentativas'] or 0))
    ult = _parse(row['atualizado_em'])
    if not ult:
        return None
    return ult + timedelta(minutes=DEV_ESPERA_MIN[min(n, len(DEV_ESPERA_MIN)) - 1])


def devolucoes_travadas(conn, agora=None):
    """Pro saas-admin (V7-04): devoluções que precisam de gente olhando — esgotaram as tentativas,
    estão em espera depois de recusa da Efí (ex.: conta do PubShow sem saldo) ou estão 'devolvendo'
    há mais de 24 h do pagamento. Mais antigas primeiro."""
    agora = _agora(agora)
    return _todas(conn, '''SELECT id, pedido_id, business_id, txid, status, valor_cent, pago_em, devolucao_id,
                                  dev_tentativas, dev_checagens, erro, motivo_estorno, atualizado_em
                           FROM pubshow_pay_cobrancas
                           WHERE status='devolver_erro'
                              OR (status='devolvendo' AND (pago_em < ? OR dev_checagens >= ?))
                           ORDER BY pago_em, id''', (_fmt(agora - timedelta(hours=24)), DEV_TRAVADA_CHECAGENS))


def _avisa_se_esgotou(row, motivo):
    if int(row['dev_tentativas'] or 0) >= DEV_MAX_TENTATIVAS:
        log.error('[PubShow pay] devolução do pedido %s ESGOTOU %s tentativas (cliente sem o PIX de volta; '
                  'ver devolucoes_travadas no saas-admin): %s', row['pedido_id'], DEV_MAX_TENTATIVAS, motivo)


def _resultado_dev(conn, row, r, agora):
    """Trata a resposta do PUT da devolução (ou da re-tentativa com o mesmo id)."""
    if r.get('erro'):
        if _recusa_definitiva(r):
            # recusa definitiva (ex.: passou de 90 dias, valor errado): marca erro; o próximo pedido usa id novo
            _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET status='devolver_erro', erro=?, atualizado_em=?
                                  WHERE id=? AND status='devolvendo' ''', (str(r['erro'])[:300], _fmt(agora), row['id']))
            _avisa_se_esgotou(row, r['erro'])
            return {'ok': False, 'erro': 'devolucao_recusada', 'detalhe': r['erro'], 'status': 'devolver_erro'}
        # rede/timeout/5xx: pode ter saído. Fica 'devolvendo' e o reconciliador confere com o MESMO id.
        _exec_commit(conn, 'UPDATE pubshow_pay_cobrancas SET erro=?, atualizado_em=? WHERE id=?',
                     (str(r['erro'])[:300], _fmt(agora), row['id']))
        return {'ok': True, 'novo': True, 'status': 'devolvendo', 'pendente': True}
    st = (r.get('status') or '').upper()
    if st == 'DEVOLVIDO':
        _finaliza_devolucao(conn, row['id'], st, agora)
        return {'ok': True, 'novo': True, 'status': 'devolvida'}
    if st == 'NAO_REALIZADO':
        _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET status='devolver_erro', devolucao_status=?, erro=?,
                              atualizado_em=? WHERE id=? AND status='devolvendo' ''',
                     (st, (r.get('motivo') or 'NAO_REALIZADO')[:300], _fmt(agora), row['id']))
        _avisa_se_esgotou(row, r.get('motivo') or 'NAO_REALIZADO')
        return {'ok': False, 'erro': 'devolucao_nao_realizada', 'status': 'devolver_erro'}
    _exec_commit(conn, 'UPDATE pubshow_pay_cobrancas SET devolucao_status=?, atualizado_em=? WHERE id=?',
                 (st or 'EM_PROCESSAMENTO', _fmt(agora), row['id']))
    return {'ok': True, 'novo': True, 'status': 'devolvendo', 'pendente': True}


def estornar(conn, pedido_id, motivo='', cli=None, agora=None, continuar=False, business_id=None):
    """Devolve o PIX de um pedido (gerente recusou, PIX atrasado, pedido cancelado).
    - Não pago ainda → cancela a cobrança (se o PIX cair depois, confirmar() devolve sozinho).
    - Pago → PUT da devolução com id 'psd<pedido>' (Efí recusa id repetido = nunca devolve 2×).
    - 2º clique / 2ª thread → não faz nada de novo (novo=False).
    continuar=True (reconciliador): termina uma devolução que ficou 'devolvendo'/'devolver_erro'.
    business_id (painel): só devolve se o pedido for desse bar."""
    agora = _agora(agora)
    row = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE pedido_id=?', (int(pedido_id),))
    if not row:
        return {'ok': False, 'erro': 'sem_cobranca'}
    if business_id is not None and int(row['business_id']) != int(business_id):
        return {'ok': False, 'erro': 'pedido_de_outro_bar'}
    st = row['status']
    if st == 'devolvida':
        return {'ok': True, 'novo': False, 'status': 'devolvida'}
    if st in ('erro', 'divergente'):
        return {'ok': False, 'erro': st, 'status': st}
    cli = cli or cliente()

    if st in ('criando', 'ativa', 'expirada'):
        n = _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET status='cancelada', motivo_estorno=?, atualizado_em=?
                                  WHERE id=? AND status IN ('criando','ativa','expirada')''',
                         ((motivo or 'cancelado')[:120], _fmt(agora), row['id']))
        if n != 1:
            return estornar(conn, pedido_id, motivo, cli, agora, continuar, business_id)   # mudou no meio: relê
        c = cli.cancelar_cobranca(row['txid'])
        if c.get('erro'):
            # não deu pra cancelar: talvez já esteja paga → confirma (e o confirmar devolve, pois está 'cancelada')
            conf = confirmar(conn, row['txid'], cli=cli, agora=agora)
            return {'ok': True, 'novo': True, 'cancelada': True, 'devolvido': conf.get('status') == 'devolvida',
                    'status': conf.get('status', 'cancelada'), 'confirmar': conf}
        return {'ok': True, 'novo': True, 'cancelada': True, 'devolvido': False, 'status': 'cancelada'}
    if st == 'cancelada':
        return {'ok': True, 'novo': False, 'cancelada': True, 'status': 'cancelada'}

    if st == 'devolvendo':
        if not continuar:
            return {'ok': True, 'novo': False, 'status': 'devolvendo'}
        # CC1 X5 (16/10/26): depois de DEV_CHECA_RAPIDO conferências sem resposta, espera crescente (até 1×/dia)
        n_chk = int(row.get('dev_checagens') or 0)
        if n_chk >= DEV_CHECA_RAPIDO:
            ult = _parse(row['atualizado_em'])
            espera = DEV_ESPERA_MIN[min(n_chk - DEV_CHECA_RAPIDO, len(DEV_ESPERA_MIN) - 1)]
            if ult and agora < ult + timedelta(minutes=espera):
                return {'ok': True, 'novo': False, 'status': 'devolvendo', 'pendente': True, 'aguardando': True}
        r = cli.consultar_devolucao(row['e2eid'], row['devolucao_id'], timeout=6)
        if r.get('erro'):
            # 15/10/26 (V7-03): plano B = GET /v2/cob/:txid, que lista pix[].devolucoes[] com id e status
            # (a doc do split não mostra o GET da devolução de split).
            rb = _dev_pela_cobranca(cli, row)
            if rb is not None:
                r = rb
            elif _dev_nao_encontrada(r) and n_chk < DEV_REPUT_MAX:
                # V7-01: a Efí não conhece essa devolução (doc: HTTP 400 'devolucao_nao_encontrada'):
                # o PUT nunca chegou → pede de novo com o MESMO id (id repetido = 409, nunca 2×).
                # CC1 X5: no máximo DEV_REPUT_MAX vezes (se o GET nunca lê, re-mandar não adianta).
                r = cli.devolver_pix(row['e2eid'], _reais_cent(row['pago_cent']), row['devolucao_id'],
                                     split=bool(row['split_ok']), idempotente=True, timeout=6)
            else:
                _conta_checagem(conn, row, agora)
                return {'ok': True, 'novo': False, 'status': 'devolvendo', 'pendente': True}   # tenta na próxima
        res = _resultado_dev(conn, row, r, agora)
        if res.get('status') == 'devolvendo':
            _conta_checagem(conn, row, agora)
        return res

    if st == 'devolver_erro' and row['dev_tentativas'] >= DEV_MAX_TENTATIVAS:
        log.error('[PubShow pay] devolução do pedido %s esgotou %s tentativas: %s', row['pedido_id'],
                  DEV_MAX_TENTATIVAS, row['erro'])
        return {'ok': False, 'erro': 'devolucao_esgotou_tentativas', 'status': st}
    if st == 'devolver_erro':
        # V7-04: recusa da Efí (ex.: conta do PubShow sem saldo) → nova tentativa ESPAÇADA, não a cada 60 s
        prox = _proxima_tentativa_dev(row)
        if prox and agora < prox:
            return {'ok': True, 'novo': False, 'status': st, 'aguardando': True, 'proxima_em': _fmt(prox)}

    _aprov = str(row.get('dev_aprovacao') or '')
    if st == 'paga' and _aprov.startswith('negada'):
        return {'ok': False, 'erro': 'devolucao_negada', 'status': st}
    if st == 'paga' and _aprov == 'pendente':
        return {'ok': True, 'novo': False, 'status': st, 'aprovacao': 'pendente'}
    # 17/10/26 (decisão 3b): teto de devolução de momento JÁ PAGO por dono por dia — só na devolução que o BAR pediu
    # (recusa do gerente/dono: o caminho do "devolvo pro meu bolso"). Devolução automática do sistema (PIX atrasado,
    # pedido cancelado) não espera o suporte (crítico dinheiro M5). Aprovada no saas-admin passa direto.
    _teto_vale = (st == 'paga' and not _aprov.startswith('aprovada')
                  and str(motivo or row.get('motivo_estorno') or '').startswith('recusado'))

    if st in ('paga', 'devolver_erro'):
        # 'devolver_erro' só existe depois de uma recusa DEFINITIVA da Efí (4xx ou NAO_REALIZADO): a
        # devolução anterior não saiu, então a nova tentativa usa id novo (psd<pedido>t2, t3...).
        idd = _id_devolucao(row['pedido_id'], int(row['dev_tentativas']) + 1)
        _pendente = False
        with _imediata(conn):
            if _teto_vale:
                # crítico dinheiro B1: a soma do dia é lida DENTRO da transação (BEGIN IMMEDIATE serializa as recusas)
                teto = teto_devolucao_cent()
                if _devolvido_hoje_cent(conn, row['business_id'], agora) + int(row['pago_cent'] or row['valor_cent']) > teto:
                    _pendente = conn.execute('''UPDATE pubshow_pay_cobrancas SET estornar=1, dev_aprovacao='pendente',
                                                    motivo_estorno=COALESCE(motivo_estorno, ?), atualizado_em=?
                                                 WHERE id=? AND status='paga' AND dev_aprovacao IS NULL''',
                                              ((motivo or 'estorno')[:120], _fmt(agora), row['id'])).rowcount == 1
                    cur = None
            cur = None if _pendente else conn.execute('''UPDATE pubshow_pay_cobrancas SET status='devolvendo', devolucao_id=?,
                                      devolucao_status=NULL, dev_tentativas=dev_tentativas+1, dev_checagens=0, estornar=1,
                                      motivo_estorno=COALESCE(motivo_estorno, ?), atualizado_em=?,
                                      dev_pedida_em=COALESCE(dev_pedida_em, ?)
                                  WHERE id=? AND status IN ('paga','devolver_erro')''',
                               (idd, (motivo or 'estorno')[:120], _fmt(agora), _fmt(agora), row['id']))
            ganhou = cur is not None and cur.rowcount == 1
        if _pendente:
            log.warning('[PubShow pay] devolução do pedido %s passou do teto do dono (R$ %s/dia): espera o OK no '
                        'saas-admin', row['pedido_id'], _reais_cent(teto_devolucao_cent()))
            return {'ok': True, 'novo': True, 'status': 'paga', 'aprovacao': 'pendente'}
        if not ganhou:
            return {'ok': True, 'novo': False, 'status': _uma(conn, 'SELECT status FROM pubshow_pay_cobrancas WHERE id=?',
                                                              (row['id'],))['status']}
        row = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE id=?', (row['id'],))
        r = cli.devolver_pix(row['e2eid'], _reais_cent(row['pago_cent']), idd,
                             split=bool(row['split_ok']), idempotente=True, timeout=6)
        return _resultado_dev(conn, row, r, agora)
    return {'ok': False, 'erro': f'status_{st}'}


def _devolvido_hoje_cent(conn, business_id, agora):
    """Decisão 3b: quanto o PubShow já começou a devolver HOJE (Brasília) de momento já pago dos bares deste dono."""
    g = grupo_dono(conn, business_id)
    brt = _agora(agora).replace(tzinfo=timezone.utc).astimezone(BRT)
    ini = (brt.replace(hour=0, minute=0, second=0, microsecond=0)).astimezone(timezone.utc).replace(tzinfo=None)
    return _soma(conn, f'''SELECT SUM(COALESCE(pago_cent, valor_cent)) FROM pubshow_pay_cobrancas
                            WHERE business_id IN ({_in(g)}) AND dev_pedida_em >= ?
                              AND status IN ('devolvendo','devolvida','devolver_erro')''', (*g, _fmt(ini)))


def devolucoes_aguardando(conn):
    """Decisão 3b (17/10/26): devoluções de momento já pago que passaram do teto do dono no dia e esperam o OK no
    /saas-admin (aprovar_devolucao / negar_devolucao). Mais antigas primeiro."""
    return _todas(conn, '''SELECT id, pedido_id, business_id, txid, status, valor_cent, pago_cent, pago_em,
                                  motivo_estorno, atualizado_em
                           FROM pubshow_pay_cobrancas WHERE status='paga' AND dev_aprovacao='pendente'
                           ORDER BY atualizado_em, id''')


def aprovar_devolucao(conn, pedido_id, quem, cli=None, agora=None):
    """/saas-admin: o Diogo aprovou a devolução que passou do teto → devolve agora (mesmo caminho do estornar)."""
    agora = _agora(agora)
    if not (quem or '').strip():
        return {'erro': 'quem_obrigatorio'}
    n = _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET dev_aprovacao=?, atualizado_em=?
                              WHERE pedido_id=? AND status='paga' AND dev_aprovacao='pendente' ''',
                     (f'aprovada:{quem.strip()[:30]}', _fmt(agora), int(pedido_id)))
    if n != 1:
        return {'erro': 'nao_esta_aguardando'}
    log.warning('[PubShow pay] devolução do pedido %s (acima do teto) aprovada por %s', pedido_id, quem)
    return estornar(conn, pedido_id, cli=cli, agora=agora, continuar=True)


def negar_devolucao(conn, pedido_id, quem, obs='', agora=None):
    """/saas-admin: o Diogo NÃO aprova a devolução (ex.: o dono devolvendo pro próprio bolso). O PIX fica como pago e o
    momento continua fora da TV (o pedido já está recusado). Só registra; nunca mexe em dinheiro."""
    agora = _agora(agora)
    if not (quem or '').strip():
        return {'erro': 'quem_obrigatorio'}
    n = _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET dev_aprovacao=?, estornar=0, atualizado_em=?,
                                     motivo_estorno=COALESCE(motivo_estorno,'') || ?
                              WHERE pedido_id=? AND status='paga' AND dev_aprovacao='pendente' ''',
                     (f'negada:{quem.strip()[:30]}', _fmt(agora), f' | negada: {(obs or "").strip()[:80]}', int(pedido_id)))
    if n != 1:
        return {'erro': 'nao_esta_aguardando'}
    log.warning('[PubShow pay] devolução do pedido %s NEGADA por %s: %s', pedido_id, quem, obs)
    return {'ok': True}


def _conta_checagem(conn, row, agora):
    """CC1 X5: +1 conferência sem resposta da devolução 'devolvendo' (a espera crescente conta daqui)."""
    try:
        _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET dev_checagens=dev_checagens+1, atualizado_em=?
                              WHERE id=? AND status='devolvendo' ''', (_fmt(agora), row['id']))
    except sqlite3.OperationalError:
        pass


def devolucao_conferida(conn, pedido_id, quem, obs='', agora=None):
    """CC1 X5 (16/10/26), pelo shell: a devolução ficou 'devolvendo' (a Efí não respondeu por nenhum caminho) e você
    CONFERIU no app da Efí que ela SAIU. Fecha como 'devolvida' com os lançamentos de estorno de sempre (o "bar deve"
    da parte que caiu pelo split). Nunca devolve nada: só registra. Não vale pra outro status."""
    agora = _agora(agora)
    if not (quem or '').strip():
        return {'erro': 'quem_obrigatorio'}
    row = _uma(conn, 'SELECT * FROM pubshow_pay_cobrancas WHERE pedido_id=?', (int(pedido_id),))
    if not row:
        return {'erro': 'sem_cobranca'}
    if row['status'] not in ('devolvendo', 'devolver_erro'):
        return {'erro': f'status_{row["status"]}'}
    if row['status'] == 'devolver_erro':   # crítico dinheiro B5 (17/10/26): a Efí devolveu mas respondeu erro
        _exec_commit(conn, "UPDATE pubshow_pay_cobrancas SET status='devolvendo' WHERE id=? AND status='devolver_erro'",
                     (row['id'],))
    ok = _finaliza_devolucao(conn, row['id'], f'CONFERIDO:{quem.strip()[:30]}'[:40], agora)
    if ok:
        log.warning('[PubShow pay] devolução do pedido %s fechada à mão por %s: %s', pedido_id, quem, obs)
    return {'ok': bool(ok)}


def _reais_cent(cent):
    cent = int(cent or 0)
    return f'{cent // 100}.{cent % 100:02d}'


# ══ Reconciliador (a cada 60 s) ═══════════════════════════════════════════════════════════
def reconciliar(conn, lote=50, cli=None, on_pago=None, agora=None):
    """1 rodada: (1) confirma o que foi pago (1 chamada de LISTA dos PIX recebidos; se a lista falhar,
    consulta 1 a 1 até `lote` cobranças ativas); (2) PIX que caiu em cobrança expirada/cancelada = devolve;
    (3) limpa 'criando' travado; (4) expira as ativas vencidas; (5) termina devoluções pendentes."""
    agora = _agora(agora)
    cli = cli or cliente()
    st = {'confirmadas': 0, 'devolvidas': 0, 'expiradas': 0, 'consultas': 0, 'erros': 0, 'limpas': 0,
          'lista_ok': None}
    desde = agora - timedelta(hours=JANELA_ATRASO_H)
    cands = _todas(conn, '''SELECT * FROM pubshow_pay_cobrancas
                            WHERE status IN ('criando','ativa')
                               OR (status IN ('expirada','cancelada') AND criado_em >= ?)
                            ORDER BY criado_em LIMIT 1000''', (_fmt(desde),))
    pagos = None
    if cands:
        ini = max(min(_parse(r['criado_em']) for r in cands) - timedelta(minutes=5), desde)
        pagos, pagina = {}, 0
        while pagina < 20:
            lst = cli.listar_pix_recebidos(ini, agora + timedelta(minutes=1), pagina=pagina)
            st['consultas'] += 1
            if lst.get('erro'):
                pagos = None
                break
            for p in lst.get('pix') or []:
                if p.get('txid'):
                    pagos[p['txid']] = p
            pagina += 1
            if pagina >= int(lst.get('paginas') or 1):
                break
        st['lista_ok'] = pagos is not None

    if pagos is not None:
        alvo = [r for r in cands if r['txid'] in pagos]
    else:
        # sem lista: só as ATIVAS, as consultadas há mais tempo primeiro, no máximo `lote`
        alvo = sorted([r for r in cands if r['status'] == 'ativa'], key=lambda r: r['ultima_consulta'] or '')[:lote]
    # 16/10/26 (crítico C3): ativa VENCIDA que esta rodada não conferiu (fora da lista — que tem teto de
    # JANELA_ATRASO_H — ou além do `lote`) é consultada 1 a 1 ANTES de expirar. Robô parado 3 h: o PIX pago
    # 3 min depois de criado virava 'expirada' sem lançamento nem devolução (o dinheiro sumia das telas).
    _limite_exp = _fmt(agora - timedelta(seconds=60))
    _ja = {r['txid'] for r in alvo}
    _venc = [r for r in _todas(conn, "SELECT * FROM pubshow_pay_cobrancas WHERE status='ativa' AND expira_em < ? "
                                     "ORDER BY criado_em LIMIT ?", (_limite_exp, int(lote)))
             if r['txid'] not in _ja]
    _venc_tx = {r['txid'] for r in _venc}
    _nao_conferidas = []
    for r in alvo + _venc:
        try:
            res = confirmar(conn, r['txid'], cli=cli, on_pago=on_pago, agora=agora)
            if pagos is None or r['txid'] in _venc_tx:
                st['consultas'] += 1
            if not res.get('ok'):
                _nao_conferidas.append(r['txid'])
            if res.get('novo') and res.get('pago'):
                st['confirmadas'] += 1
                if res.get('estorno'):
                    st['devolvidas'] += 1
            elif not res.get('ok'):
                st['erros'] += 1
        except Exception as e:
            st['erros'] += 1
            _nao_conferidas.append(r['txid'])
            log.warning('[PubShow pay] reconciliar %s: %s', r['txid'], e)

    # 'criando' travado (o processo caiu no meio): o QR nunca foi mostrado → cancela e libera a reserva
    for r in _todas(conn, "SELECT * FROM pubshow_pay_cobrancas WHERE status='criando' AND criado_em < ?",
                    (_fmt(agora - timedelta(minutes=2)),)):
        try:
            cli.cancelar_cobranca(r['txid'])
        except Exception:
            pass
        st['limpas'] += _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET status='erro', erro='criando travado',
                                              atualizado_em=? WHERE id=? AND status='criando' ''', (_fmt(agora), r['id']))

    # expira (a cobrança Efí também expira sozinha; PIX depois disso = devolução automática)
    # (a que não deu pra conferir nesta rodada — Efí fora — e a vencida além do `lote` ficam pra próxima)
    # Vencida há mais de 48 h expira mesmo sem consulta (a Efí que não responde por ela não trava a reserva).
    _confer = (_venc_tx | {r['txid'] for r in alvo if r['status'] == 'ativa'}) - set(_nao_conferidas)
    _velha = _fmt(agora - timedelta(hours=48))
    _todas_venc = _todas(conn, "SELECT id, txid, expira_em FROM pubshow_pay_cobrancas WHERE status='ativa' "
                               "AND expira_em < ?", (_limite_exp,))
    _ids_exp = [r['id'] for r in _todas_venc if r['txid'] in _confer or r['expira_em'] < _velha]
    st['expiradas'] = 0
    for _id in _ids_exp:
        st['expiradas'] += _exec_commit(conn, '''UPDATE pubshow_pay_cobrancas SET status='expirada', atualizado_em=?
                                                 WHERE id=? AND status='ativa' AND expira_em < ?''',
                                        (_fmt(agora), _id, _limite_exp))

    # devoluções que ficaram no meio (EM_PROCESSAMENTO, timeout, queda) ou pedidas e não feitas
    for r in _todas(conn, f'''SELECT * FROM pubshow_pay_cobrancas
                              WHERE status='devolvendo'
                                 OR (status='paga' AND estornar=1 AND COALESCE(dev_aprovacao,'') NOT IN ('pendente')
                                     AND COALESCE(dev_aprovacao,'') NOT LIKE 'negada%')
                                 OR (status='devolver_erro' AND dev_tentativas < {DEV_MAX_TENTATIVAS})
                              ORDER BY atualizado_em LIMIT ?''', (int(lote),)):
        try:
            res = estornar(conn, r['pedido_id'], motivo=r['motivo_estorno'] or 'estorno', cli=cli, agora=agora,
                           continuar=True)
            if res.get('status') == 'devolvida' and res.get('novo'):
                st['devolvidas'] += 1
        except Exception as e:
            st['erros'] += 1
            log.warning('[PubShow pay] devolução pendente do pedido %s: %s', r['pedido_id'], e)
    return st


_thread = None


def iniciar_reconciliador(get_conn, on_pago=None, intervalo=60):
    """Sobe o robô (thread daemon, como os do app.py). Só com PUBSHOW_GATEWAY=efi.
    get_conn = pubshow_db.get_pubshow_db; on_pago = o mesmo callback do confirmar()."""
    global _thread
    if not ligado():
        return False
    if _thread is not None and _thread.is_alive():
        return True
    c0 = get_conn()
    try:
        init_tables(c0)
    finally:
        c0.close()

    def _loop():
        time.sleep(30)   # deixa o app subir
        log.info('[PubShow pay] reconciliador Efí ATIVO (a cada %ss)', intervalo)
        while True:
            conn = None
            try:
                conn = get_conn()
                r = reconciliar(conn, on_pago=on_pago)
                if r.get('confirmadas') or r.get('devolvidas') or r.get('erros'):
                    log.info('[PubShow pay] reconciliar: %s', r)
            except Exception as e:
                log.error('[PubShow pay] reconciliador: %s', e)
            finally:
                try:
                    conn and conn.close()
                except Exception:
                    pass
            time.sleep(intervalo)

    _thread = threading.Thread(target=_loop, daemon=True, name='pubshow-pay-reconciliador')
    _thread.start()
    return True


# ══ Saldo, extrato e fechamento (só relatório: no split nada fica parado com o PubShow) ═════
def saldo(conn, business_id, mes=None):
    """Saldo de cada conta = SUM do livro-caixa (até o fim da competência `mes`, se vier).
    casa/gorjeta: 0 = em dia (a parte do bar já caiu na conta Efí dele pelo split); < 0 = o bar deve
    (estorno: a devolução sai só da conta do PubShow) e é compensado nos próximos momentos da MESMA conta;
    > 0 só por ajuste() manual. plataforma = o que o PubShow ficou desse bar (taxa de serviço + mensalidade)."""
    bid = int(business_id)
    out = {}
    for conta in CONTAS:
        if mes:
            _mes_ok(mes)
            out[conta] = _soma(conn, '''SELECT SUM(valor_cent) FROM pubshow_ledger WHERE business_id=? AND conta=?
                                        AND competencia<=?''', (bid, conta, mes))
        else:
            out[conta] = _soma(conn, 'SELECT SUM(valor_cent) FROM pubshow_ledger WHERE business_id=? AND conta=?',
                               (bid, conta))
    return out


def extrato(conn, business_id, mes, conta=None):
    """Linhas do livro-caixa da competência (mais novas por último) + totais por conta e tipo."""
    _mes_ok(mes)
    args = [int(business_id), mes]
    sql = 'SELECT * FROM pubshow_ledger WHERE business_id=? AND competencia=?'
    if conta:
        sql += ' AND conta=?'
        args.append(conta)
    linhas = _todas(conn, sql + ' ORDER BY id', tuple(args))
    tot = {}
    for l in linhas:
        tot.setdefault(l['conta'], {}).setdefault(l['tipo'], 0)
        tot[l['conta']][l['tipo']] += l['valor_cent']
    return {'mes': mes, 'linhas': linhas, 'totais': tot}


def _mens_paga(conn, bid, mes):
    return _soma(conn, '''SELECT SUM(valor_cent) FROM pubshow_ledger WHERE business_id=? AND conta='plataforma'
                          AND tipo IN ('mensalidade','estorno_mensalidade') AND competencia=?''', (int(bid), mes))


def fechamento_mes(conn, bar, mes):
    """RELATÓRIO do mês do bar (tela "Meu dinheiro", saas-admin). Não grava nada e não move dinheiro:
    16/10/26 (split-only) o dinheiro do bar já caiu na conta Efí dele, na hora; o lote de repasse do
    bolsão saiu do código. Mostra:
    - momentos e gorjetas do mês (já sem os estornos) e a taxa de serviço;
    - mensalidade quitada (e em que dia, Brasília) ou o que falta (mensalidade_falta_cent). 16/10/26 (Lote D A2):
      o que falta NUNCA é cobrado (no mês atual, 30% dos próximos momentos pagam; mês passado, ficou assim).
      Saíram 'garantia', 'mensalidade_pendente_cent' e 'mensalidade_perdoada_cent' (garantia e C9 morreram);
    - "o bar deve" (estorno: a devolução saiu da conta do PubShow), que sai dos próximos momentos."""
    _mes_ok(mes)
    bar = _bar_com_excecoes(conn, bar)
    bid = int(bar['id'])
    pl = plano_cent(bar)

    def _s(conta, tipos):
        return _soma(conn, f'''SELECT SUM(valor_cent) FROM pubshow_ledger WHERE business_id=? AND conta=?
                               AND competencia=? AND tipo IN ({",".join("?" * len(tipos))})''',
                     (bid, conta, mes, *tipos))

    paga = _mens_paga(conn, bid, mes)
    falta = max(0, pl - paga)
    quitada_em = None
    if pl and paga >= pl:
        acc = 0
        for l in _todas(conn, '''SELECT valor_cent, criado_em FROM pubshow_ledger WHERE business_id=? AND conta='plataforma'
                                 AND tipo IN ('mensalidade','estorno_mensalidade') AND competencia=? ORDER BY id''',
                        (bid, mes)):
            acc += l['valor_cent']
            if acc >= pl:
                dt = _parse(l['criado_em'])
                quitada_em = dt.replace(tzinfo=timezone.utc).astimezone(BRT).strftime('%Y-%m-%d') if dt else None
                break
    out = {
        'business_id': bid, 'mes': mes, 'plano_cent': pl,
        'momentos_cent': _s('casa', ('credito', 'estorno')),
        'gorjetas_cent': _s('gorjeta', ('credito', 'estorno')),
        'taxa_servico_cent': _s('plataforma', ('taxa_servico', 'estorno_taxa')),
        'mensalidade_paga_cent': paga,
        'mensalidade_quitada': paga >= pl,
        'mensalidade_quitada_em': quitada_em,
        'mensalidade_falta_cent': falta,                           # nunca cobrada (Lote D A2)
        'mensal_pct': mensal_pct(),
        # >0 só num caso raro (PIX pago no limite do prazo depois de outra cobrança reservar a mesma parte):
        # é do bar — devolver com ajuste() / abater no mês seguinte
        'mensalidade_excesso_cent': max(0, paga - pl),
        'saldo': saldo(conn, bid, mes),
    }
    # 17/10/26 (decisão 3a): a dívida é do DONO (soma os bares dele): é ela que sai dos próximos momentos
    out['bar_deve_cent'] = {c: divida_dono_cent(conn, bid, c) for c in ('casa', 'gorjeta')}
    return out


def resumo_admin(conn, mes=None, agora=None):
    """Pro saas-admin (16/10/26): receita REAL do PubShow = soma da conta 'plataforma' do livro-caixa
    (taxa de serviço + mensalidade, já sem os estornos), no total e no mês; "bares me devem" (casa/gorjeta
    negativas: estorno no split ainda não compensado); e as devoluções travadas (devolucoes_travadas)."""
    agora = _agora(agora)
    mes = _mes_ok(mes) if mes else competencia(agora)
    n_lanc = _soma(conn, 'SELECT COUNT(*) FROM pubshow_ledger')
    receita_total = _soma(conn, "SELECT SUM(valor_cent) FROM pubshow_ledger WHERE conta='plataforma'")
    receita_mes = _soma(conn, "SELECT SUM(valor_cent) FROM pubshow_ledger WHERE conta='plataforma' AND competencia=?",
                        (mes,))
    devem, _vistos = [], set()
    # 17/10/26 (decisão 3a): 1 linha por DONO (os bares do mesmo CPF/CNPJ/conta Efí somados)
    for d in _todas(conn, '''SELECT business_id, SUM(valor_cent) AS s FROM pubshow_ledger
                             WHERE conta IN ('casa','gorjeta') GROUP BY business_id HAVING SUM(valor_cent) < 0'''):
        g = tuple(grupo_dono(conn, int(d['business_id'])))
        if g in _vistos:
            continue
        _vistos.add(g)
        casa, gorj = divida_dono_cent(conn, g[0], 'casa'), divida_dono_cent(conn, g[0], 'gorjeta')
        if casa or gorj:
            devem.append({'business_id': g[0], 'bares': list(g), 'casa_cent': casa, 'gorjeta_cent': gorj})
    travadas = devolucoes_travadas(conn, agora)
    # CC1 X2 (16/10/26): mensalidade tirada ACIMA do plano num mês (caso raro do PIX pago no limite do prazo).
    # O excesso é do bar e está na conta do PubShow: devolva por PIX ao bar e lance ajuste(conn, bar, 'plataforma',
    # -excesso, motivo, quem) pro livro-caixa bater com a conta Efí.
    excesso = []
    for d in _todas(conn, '''SELECT business_id, competencia, SUM(valor_cent) AS paga FROM pubshow_ledger
                             WHERE conta='plataforma' AND tipo IN ('mensalidade','estorno_mensalidade')
                             GROUP BY business_id, competencia'''):
        pl = plano_cent(_bar_com_excecoes(conn, {'id': int(d['business_id'])}))
        if int(d['paga'] or 0) > pl:
            excesso.append({'business_id': int(d['business_id']), 'mes': d['competencia'],
                            'excesso_cent': int(d['paga']) - pl})
    aguardando = devolucoes_aguardando(conn)   # decisão 3b: passou do teto do dono, espera o seu OK
    return {'mes': mes, 'tem_movimento': n_lanc > 0, 'receita_total_cent': receita_total,
            'aguardando_ok': aguardando, 'n_aguardando_ok': len(aguardando),
            'receita_mes_cent': receita_mes, 'bares_devem': devem,
            'bares_devem_cent': sum(x['casa_cent'] + x['gorjeta_cent'] for x in devem),
            'travadas': travadas, 'n_travadas': len(travadas),
            'mens_excesso': excesso, 'mens_excesso_cent': sum(x['excesso_cent'] for x in excesso)}


def ajuste(conn, business_id, conta, valor_cent, motivo, quem, agora=None):
    """Correção manual (ex.: acerto do Garage): lançamento NOVO, nunca edição. Exige motivo e quem."""
    agora = _agora(agora)
    if not (motivo or '').strip() or not (quem or '').strip():
        return {'erro': 'motivo_e_quem_obrigatorios'}
    ref = f'aj:{int(business_id)}:{secrets.token_hex(6)}'
    with _imediata(conn):
        _lanca(conn, business_id, conta, 'ajuste', int(valor_cent), ref, competencia(agora), agora,
               obs=f'{quem.strip()[:40]}: {motivo.strip()[:150]}')
    return {'ok': True, 'ref': ref}


# ══ Pausa de 60 dias sem momento pago (Lote D A3, 16/10/26 — SÓ modo Efí) ═════════════════════
# Decisão do Diogo: bar na Efí sem nenhum momento pago há 60 dias → aviso no painel e por WhatsApp/e-mail 7 dias
# antes; no dia 60 os momentos, a música grátis e a TV mostram "pausado, fale com o suporte". Reativar = botão no
# /pubshow/admin e no saas-admin. Nunca pausa bar no modo Asaas (o pubshow.py só chama isto com _efi_para(b)).
# O relógio conta do MAIS NOVO entre: a 1ª vez que o sistema viu o bar na Efí (desde), o último PIX pago (momento
# ou gorjeta: bar com gorjeta caindo está em uso), a conta Efí conectada e a última reativação.
def _garante_pausa(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pubshow_pay_pausa'").fetchone()             or 'visto_em' not in {r[1] for r in conn.execute('PRAGMA table_info(pubshow_pay_pausa)')}:
        init_tables(conn)


def pausa_isento(conn, bar, agora=None):
    """Lote D, críticos (17/10/26, CD2): bar que NUNCA pausa, mesmo no modo Efí sem momento pago:
    isento (definir_plano 0, ex.: o demo no meio de uma demonstração) ou com a assinatura do Asaas ativa (o Garage na
    migração, pagando R$ 49,90 enquanto não conecta a conta). Mesma regra que tira a mensalidade do split (VCa-02).
    O mês pago de quem CANCELOU (ativo_ate) continua podendo pausar: é a decisão do VDA-05 (fica pro Diogo)."""
    try:
        if plano_do_bar(conn, bar) <= 0:
            return True
    except Exception as ex:
        log.warning('[PubShow pay] pausa: plano do bar %s não lido: %s', bar['id'], ex)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pubshow_assinaturas'").fetchone():
        if conn.execute("SELECT 1 FROM pubshow_assinaturas WHERE business_id=? AND status='ativo' LIMIT 1",
                        (int(bar['id']),)).fetchone():
            return True
    return False


def pausa_do_bar(conn, bar, agora=None, robo=False):
    """Estado da pausa do bar: {'pausado', 'aviso' (faltam 7 dias ou menos), 'dias' (sem momento pago),
    'ref', 'pausa_em' (UTC), 'pausa_em_brt' ('DD/MM'), 'avisado' (o aviso deste ciclo já saiu), 'isento'}.
    1ª chamada pro bar grava o 'desde' (o relógio começa quando o sistema vê o bar na Efí).
    17/10/26 (Lote D, críticos CD2): bar isento (pausa_isento) nunca pausa e o relógio dele fica parado no agora (ao
    deixar de ser isento, ganha os 60 dias inteiros). robo=True (só o robô de avisos, a cada 10 min) marca visto_em;
    se o robô não via o bar fazia mais de PAUSA_FORA_DIAS (fora da lista da Efí, rollback), o relógio recomeça."""
    agora = _agora(agora)
    bid = int(bar['id'])
    _garante_pausa(conn)
    p = _uma(conn, 'SELECT * FROM pubshow_pay_pausa WHERE business_id=?', (bid,))
    if not p:
        _exec_commit(conn, 'INSERT OR IGNORE INTO pubshow_pay_pausa (business_id, desde, visto_em) VALUES (?,?,?)',
                     (bid, _fmt(agora), _fmt(agora) if robo else None))
        p = _uma(conn, 'SELECT * FROM pubshow_pay_pausa WHERE business_id=?', (bid,))
    isento = pausa_isento(conn, bar, agora)
    visto = _parse(p.get('visto_em'))
    fora = robo and visto is not None and agora - visto > timedelta(days=PAUSA_FORA_DIAS)
    if (isento or fora) and (_parse(p['desde']) or agora) < agora - timedelta(minutes=10):
        _exec_commit(conn, 'UPDATE pubshow_pay_pausa SET desde=?, aviso_em=NULL WHERE business_id=?', (_fmt(agora), bid))
        if fora:
            log.warning('[PubShow pay] pausa: bar %s voltou pra Efí depois de %s sem ser visto — relógio recomeça',
                        bid, agora - visto)
    if robo and (visto is None or agora - visto > timedelta(minutes=5) or fora):
        _exec_commit(conn, 'UPDATE pubshow_pay_pausa SET visto_em=? WHERE business_id=?', (_fmt(agora), bid))
    if isento or fora:
        p = _uma(conn, 'SELECT * FROM pubshow_pay_pausa WHERE business_id=?', (bid,))
    ult = _uma(conn, '''SELECT MAX(pago_em) AS u FROM pubshow_pay_cobrancas
                        WHERE business_id=? AND pago_em IS NOT NULL''', (bid,))
    conta = _uma(conn, 'SELECT criado_em FROM pubshow_pay_contas WHERE business_id=?', (bid,))
    refs = [_parse(p['desde']), _parse(p['reativado_em']), _parse(ult['u'] if ult else None),
            _parse(conta['criado_em'] if conta else None)]
    ref = max(r for r in refs if r)
    pausa_em = ref + timedelta(days=PAUSA_DIAS)
    pausado = agora >= pausa_em
    av = _parse(p['aviso_em'])
    if isento:
        pausado = False
    return {'pausado': pausado, 'isento': isento,
            'aviso': (not pausado) and (not isento) and agora >= pausa_em - timedelta(days=PAUSA_AVISO_DIAS),
            'dias': max(0, int((agora - ref).total_seconds() // 86400)),
            'ref': _fmt(ref), 'pausa_em': _fmt(pausa_em),
            'pausa_em_brt': pausa_em.replace(tzinfo=timezone.utc).astimezone(BRT).strftime('%d/%m'),
            'avisado': bool(av and av >= ref)}


def marcar_aviso_pausa(conn, business_id, ref, agora=None):
    """Reserva o aviso dos 7 dias DESTE ciclo (ref = pausa_do_bar()['ref']). True = quem chamou manda o aviso
    (1 vez por ciclo, mesmo com 2 workers ao mesmo tempo). Momento pago ou reativação = ciclo novo."""
    agora = _agora(agora)
    _garante_pausa(conn)
    n = _exec_commit(conn, '''UPDATE pubshow_pay_pausa SET aviso_em=? WHERE business_id=?
                              AND (aviso_em IS NULL OR aviso_em < ?)''', (_fmt(agora), int(business_id), ref))
    return n == 1


def reativar_pausa(conn, business_id, quem, agora=None):
    """Botão "Reativar" do /pubshow/admin e do saas-admin: o relógio dos 60 dias recomeça agora."""
    agora = _agora(agora)
    if not (quem or '').strip():
        return {'erro': 'quem_obrigatorio'}
    _garante_pausa(conn)
    _exec_commit(conn, '''INSERT INTO pubshow_pay_pausa (business_id, desde, aviso_em, reativado_em, reativado_por)
                          VALUES (?,?,NULL,?,?)
                          ON CONFLICT(business_id) DO UPDATE SET aviso_em=NULL, reativado_em=excluded.reativado_em,
                              reativado_por=excluded.reativado_por''',
                 (int(business_id), _fmt(agora), _fmt(agora), quem.strip()[:40]))
    log.warning('[PubShow pay] pausa do bar %s reativada por %s', business_id, quem)
    return {'ok': True}


# ══ Dia do pagamento (Lote D A5, 16/10/26): quanto caiu na conta Efí do bar num período ══════════
def caiu_no_periodo(conn, business_id, ini, fim):
    """O que CAIU de verdade na conta Efí do bar (repasses do split) entre ini e fim (UTC, fim exclusivo),
    por conta: {'casa': momentos, 'gorjeta': gorjetas}, em centavos."""
    return {c: -_soma(conn, '''SELECT SUM(valor_cent) FROM pubshow_ledger WHERE business_id=? AND conta=?
                               AND tipo='repasse_split' AND criado_em >= ? AND criado_em < ?''',
                      (int(business_id), c, _fmt(_agora(ini)), _fmt(_agora(fim))))
            for c in ('casa', 'gorjeta')}


def rendeu_no_periodo(conn, business_id, ini, fim):
    """VDA-03 (16/10/26): o que o bar GANHOU de verdade no período (o "dia do pagamento"), em centavos, por conta.
    = o que caiu pelo split − a parte do bar dos momentos devolvidos no período (essa parte vira "o bar deve")
      + o que os momentos do período cobriram de devolução antiga (recupera_cent: já tinha caído antes).
    Momento devolvido não conta como rendimento, igual ao modo Asaas (pedido recusado sai do STATUS_PAGOS).
    Nunca negativo (semana só com devolução = 0 = não manda nada)."""
    bid, a, z = int(business_id), _fmt(_agora(ini)), _fmt(_agora(fim))
    caiu = caiu_no_periodo(conn, bid, ini, fim)
    out = {}
    for c in ('casa', 'gorjeta'):
        # parte do bar devolvida: estorno do item (negativo) + a mensalidade que volta a faltar (positivo, só casa);
        # só de cobrança cujo split caiu de verdade na conta do bar
        devolvida = -_soma(conn, '''SELECT SUM(l.valor_cent) FROM pubshow_ledger l
                                    JOIN pubshow_pay_cobrancas cb ON cb.id = l.cobranca_id
                                    WHERE l.business_id=? AND l.conta=? AND l.tipo IN ('estorno','estorno_mensalidade')
                                      AND cb.split_ok=1 AND l.criado_em >= ? AND l.criado_em < ?''', (bid, c, a, z))
        cobriu = _soma(conn, f'''SELECT SUM(recupera_cent) FROM pubshow_pay_cobrancas
                                 WHERE business_id=? AND conta=? AND status IN {ST_PAGAS}
                                   AND pago_em >= ? AND pago_em < ?''', (bid, c, a, z))
        out[c] = max(0, caiu[c] - devolvida + cobriu)
    return out
