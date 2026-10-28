# -*- coding: utf-8 -*-
"""
pubshow_dinheiro.py — 💰 Dinheiro do PubShow (F1, 25/10/26): a página /saas-admin/pubshow e a leitura no Asaas/Efí.

Pedido do Diogo (24/10/26): "preciso ter certeza do que está acontecendo nos bares, lucros, pagamento reais".
Plano aprovado: PUBSHOW_15out/ADMIN_DINHEIRO_24out/PLANO.md (a F1 e a parte técnica T1–T6).

SÓ LÊ. Nada aqui grava no banco (a conexão abre com PRAGMA query_only=1: qualquer escrita explode), nada mexe em
pedido, nada devolve, nada saca. No Asaas/Efí só GET, e só pelas funções que o pubshow.py já usa (_asaas_saldo,
_asaas_req('GET', ...), a consulta da cobrança da Efí). Nenhum POST/PUT/DELETE que mude algo: o único POST é o
OAuth da Efí (efi_client._token, POST /oauth/token), que só pega o token de acesso quando ele venceu — não muda nada
na conta. Ele usa o timeout de 20 s do efi_client (não o TIMEOUT_EFI_S) e a mesma trava da ponte; como a leitura
roda numa thread, a página não espera por ele (o que passar do prazo vira "conferi X de Y").
Módulo próprio (e não um _ps_dinheiro no app.py): o app.py e o pubshow.py recebem hunks de outras sessões; aqui o
rebase é limpo. O app.py só tem 2 rotas finas que chamam pagina() e leitura_json().

Os conjuntos (pubshow_pedidos, por bar; os bares de teste ficam fora de todo total e de todo aviso):
  C   confirmado pelo Asaas/Efí = pubshow._repasse_where_pago(STATUS_PAGOS): a MESMA regra da tela 💸 Repasses
  S   sem confirmação do banco  = Asaas/Efí + pago_em + pix_conferido HISTORICO/ADMIN/ERRO…
  S−  o banco disse que não pagou (PENDING/OVERDUE/REFUNDED…): fora de tudo (o somar_pagos também tira)
  M   PIX na chave do bar (sem Asaas/Efí, via_gateway=0) COM pago_em: o dono confirmou
  M0  "o cliente disse que pagou" (sem Asaas/Efí, via_gateway=0, SEM pago_em, modo confiança): só contagem
  Xp  pedido da divisão automática da Efí (sem Asaas/Efí no pedido, via_gateway=1) com pago_em: o dinheiro dele é o X
  X   divisão automática da Efí: pubshow_pay_cobrancas 'paga', PELO pago_em (não pela competência), em CENTAVOS
Identidade (o teste confere): C + S + M + Xp do mês = somar_pagos(conn, bar, de=dia 1, conta='tudo').

Fuso: o banco grava UTC; Brasília = UTC−3 fixo (como o resto do código). "Esta noite" começa às 12:00 de Brasília
(antes do meio-dia, a noite ainda é a que começou ontem às 12:00): às 00:30 a tela não zera.
"""
import os
import re
import time
import logging
import threading
import concurrent.futures as _cf
from datetime import datetime, timedelta

import pubshow as _ps
from pubshow_db import get_pubshow_db, STATUS_PAGOS, bares_teste

log = logging.getLogger('pubshow')

# ══ Tarifas e regras de exibição: UM lugar só ════════════════════════════════════════════════════════════════
# Tarifa de TABELA (o contrato do Diogo não foi conferido): só pra ESTIMAR a margem na tela. Não mexe na conta do
# pedido (PUBSHOW_TAXA_ASAAS, pubshow.py:291, continua sendo a que grava a parte do bar).
TARIFA_ASAAS_PADRAO = '1.99'    # R$ por PIX recebido no Asaas (asaas.com/precos-e-taxas); env PUBSHOW_TARIFA_ASAAS_REAL
TARIFA_EFI_PCT_PADRAO = '1.19'  # % do PIX recebido na Efí (sejaefi.com.br/tarifas); env PUBSHOW_TARIFA_EFI_PCT
TESTE_PADRAO = 'CC4TN7PG'       # o bar demo "Lessmann"; env PUBSHOW_DINHEIRO_TESTE (somado ao pubshow_db.bares_teste())
# Pago no Asaas = o MESMO do _repasse_conferir (RECEIVED, CONFIRMED). RECEIVED_IN_CASH é baixa manual (sem dinheiro
# no Asaas, pubshow.py:8128): nunca fica verde nem acende PIX perdido.
PAGO_ASAAS = tuple(_ps._ASAAS_STATUS_PAGO)
BAIXA_MANUAL_ASAAS = 'RECEIVED_IN_CASH'
# Só estes erros do Asaas querem dizer "essa cobrança não existe" (conferido, não pago). Qualquer outro (chave trocada,
# sem permissão: "invalid_access_token"…) é "não deu pra conferir" — senão a tela diria "✓ nenhum foi pago" sem ter lido.
NAO_EXISTE_ASAAS = frozenset({'not_found', 'invalid_object', 'object_not_found'})
CACHE_S = 600                   # a leitura no Asaas/Efí vale 10 min (em memória, por processo)
DE_NOVO_MIN_S = 60              # "Tentar de novo" refaz no máximo 1 vez por minuto
PRAZO_TOTAL_S = 12.0            # a leitura inteira; o que não deu tempo vira "conferi X de Y"
TIMEOUT_EFI_S = 5               # por consulta na Efí (o _asaas_req do pubshow.py já usa 4 s)
TRABALHADORES = 4               # perguntas ao mesmo tempo
MAX_PERDIDOS = 40               # QR sem pagamento conferidos por vez (os mais novos)
DIAS_PERDIDOS = 7
DIAS_REPASSE = 7                # bar esperando repasse há mais que isso = aviso vermelho
HORAS_DEVOLUCAO = 24            # devolução parada há mais que isso = aviso vermelho
TV_LIGADA_MIN = 5               # o mesmo _TV_SEM_SINAL_MIN do painel do bar (pubshow.py:4247)
MAX_AVISOS_VISIVEIS = 3
TEMPLATE = 'pubshow/admin_dinheiro.html'

_BRT = timedelta(hours=-3)
_FMT = '%Y-%m-%d %H:%M:%S'
_RE_ID = re.compile(r'^[A-Za-z0-9_-]{3,64}$')      # id de cobrança/assinatura/cliente do Asaas (igual ao _repasse_conferir)
_RE_TXID = re.compile(r'^[A-Za-z0-9]{26,35}$')     # código do PIX da Efí (efi_client.TXID_RE)
_PIX_CONF_SOMA = "(pix_conferido IS NULL OR pix_conferido IN ('HISTORICO','ADMIN','RECEIVED','CONFIRMED','EFI:CONCLUIDA') " \
                 "OR pix_conferido LIKE 'ERRO%')"   # o mesmo filtro do somar_pagos (pubshow.py:392-396)
_DIAS_SEMANA = ('segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo')
_MESES = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro',
          'novembro', 'dezembro')
_MES_ABREV = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez')


# ══ Pequenos ajudantes ════════════════════════════════════════════════════════════════════════════════════════
def agora_utc():
    """O 'agora' (UTC sem fuso, como o banco). Os testes trocam esta função; ninguém mexe no relógio do sistema."""
    return _ps._utc_agora()


def _env_num(nome, padrao):
    try:
        v = float(str(os.environ.get(nome) or padrao).strip().replace(',', '.'))
        return v if v >= 0 else float(padrao)
    except (TypeError, ValueError):
        return float(padrao)


def tarifas():
    """As tarifas de tabela usadas na estimativa (lidas na hora: dá pra trocar a env sem mexer no código)."""
    return {'asaas': _env_num('PUBSHOW_TARIFA_ASAAS_REAL', TARIFA_ASAAS_PADRAO),
            'efi_pct': _env_num('PUBSHOW_TARIFA_EFI_PCT', TARIFA_EFI_PCT_PADRAO)}


def codigos_teste():
    """UM conjunto só dos bares de teste: pubshow_db.bares_teste() (env PUBSHOW_BARES_TESTE, padrão o demo) ∪
    env PUBSHOW_DINHEIRO_TESTE (padrão 'CC4TN7PG'). Assim o demo sai daqui mesmo se a env do fundador mudar."""
    extra = os.environ.get('PUBSHOW_DINHEIRO_TESTE')
    extra = TESTE_PADRAO if extra is None else extra
    return set(bares_teste()) | {c.strip().upper() for c in extra.split(',') if c.strip()}


def _conn():
    """Conexão SÓ de leitura: query_only faz qualquer INSERT/UPDATE/DELETE/CREATE explodir aqui dentro."""
    conn = get_pubshow_db()
    conn.execute('PRAGMA query_only=1')
    return conn


def _tem_tabela(conn, nome):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nome,)).fetchone())


def _cols(conn, tabela):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({tabela})').fetchall()}


def _x_vale(conn):
    """O filtro das cobranças da divisão automática que contam como dinheiro que FICOU: 'paga' e não indo de volta
    pro cliente (devolução pedida = estornar=1, esperando o OK = dev_aprovacao 'pendente', pedido recusado)."""
    cs = _cols(conn, 'pubshow_pay_cobrancas')
    w = ["c.status='paga'", "TRIM(COALESCE(c.pago_em,''))<>''", "COALESCE(p.status,'')<>'recusado'"]
    if 'estornar' in cs:
        w.append('COALESCE(c.estornar,0)=0')
    if 'dev_aprovacao' in cs:
        w.append("COALESCE(c.dev_aprovacao,'')<>'pendente'")
    return ' AND '.join(w), ('c.conv_cent' if 'conv_cent' in cs else 'c.plataforma_cent')


def _parse(s):
    s = str(s or '').strip()
    if not s:
        return None
    for fmt, n in ((_FMT, 19), ('%Y-%m-%d', 10)):
        try:
            return datetime.strptime(s[:n].replace('T', ' '), fmt)
        except ValueError:
            continue
    return None


def _txt(dt):
    return dt.strftime(_FMT)


def _c(v):
    """Reais (REAL do banco) → centavos inteiros."""
    try:
        return int(round(float(v or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _pct_cent(cent, pct):
    """pct % de 'cent' centavos, arredondado pra cima no meio centavo (sempre >= 0)."""
    return int(max(0, cent) * pct / 100 + 0.5)


def _r(cent):
    return round(cent / 100, 2)


def brl(v):
    """1234.5 → 'R$ 1.234,50' (o mesmo _brl da tela Repasses)."""
    return _ps._brl(v)


def _ddmm(s):
    """'2026-10-03' ou '2026-10-03 12:00:00' (data do Asaas / do banco, SEM converter fuso) → '03/10'."""
    dt = _parse(s)
    return dt.strftime('%d/%m') if dt else ''


def _quando(utc_txt, agora):
    """Hora UTC do banco → 'hoje 22:31' / 'ontem 23:10' / '12/09 21:00' (Brasília)."""
    dt = _parse(utc_txt)
    if not dt:
        return '—'
    b, ab = dt + _BRT, agora + _BRT
    if b.date() == ab.date():
        return 'hoje ' + b.strftime('%H:%M')
    if b.date() == ab.date() - timedelta(days=1):
        return 'ontem ' + b.strftime('%H:%M')
    return b.strftime('%d/%m %H:%M')


def _plural(n, um, varios):
    return um if n == 1 else varios


def _fone(tel):
    """Telefone só com dígitos, sem o 0 da operadora/DDD e sem o 55 do país (10 ou 11 dígitos), ou ''."""
    d = re.sub(r'\D', '', str(tel or '')).lstrip('0')
    if len(d) in (12, 13) and d.startswith('55'):
        d = d[2:]
    return d if len(d) in (10, 11) else ''


def _fone_chave(tel):
    """Pra achar 2 contas do mesmo telefone: DDD + os últimos 8 dígitos (o celular antigo sem o 9 bate com o novo)."""
    f = _fone(tel)
    return f[:2] + f[-8:] if f else ''


def limites(agora):
    """As fronteiras de tempo (UTC) da tela: começo da noite (12:00 de Brasília) e do mês (dia 1, 00:00 de Brasília)."""
    ab = agora + _BRT
    meio_dia = datetime(ab.year, ab.month, ab.day, 12)
    if ab < meio_dia:
        meio_dia -= timedelta(days=1)   # antes do meio-dia, a noite ainda é a que começou ontem às 12h
    mes_brt = datetime(ab.year, ab.month, 1)
    return {'agora_brt': ab, 'noite_brt': meio_dia, 'noite_ini': meio_dia - _BRT, 'mes_brt': mes_brt,
            'mes_ini': mes_brt - _BRT, 'mes_de': mes_brt.date(), 'noite_ontem': meio_dia.date() < ab.date()}


def _bar_zero():
    return {'c_n': 0, 'c_cob': 0, 'c_val': 0, 'c_marg': 0, 'noite_n': 0, 'noite_cob': 0,
            'x_n': 0, 'x_cob': 0, 'x_bar': 0, 'x_marg': 0,
            's_n': 0, 's_val': 0, 'm_n': 0, 'm_val': 0, 'xp_n': 0, 'xp_val': 0, 'm0_n': 0,
            's_aberto_n': 0, 's_aberto_val': 0, 'dev_mes_n': 0, 'ult_pix': None,
            'repasse_desde': None, 'rep_dev_n': 0, 'rep_dev_val': 0,
            'devido': 0.0, 'devido_asaas': 0.0, 'devido_efi': 0.0}


def _rota(conn, b):
    """Por onde sai o PIX deste bar AGORA — a mesma decisão do pubshow._rota_efi, sem o _pp_conn (que cria tabela):
    'split' (divisão automática), 'ponte' (Efí do PubShow), 'asaas', 'manual' (chave do bar) ou 'sem'."""
    try:
        if _ps._efi_lista(b) and _ps._efi_cli_ok():
            pay = _ps.pubshow_pay
            if (pay is not None and _tem_tabela(conn, 'pubshow_pay_contas') and _tem_tabela(conn, 'pubshow_pay_splitcfg')
                    and pay.conta_aceita(conn, b['id'])):
                return 'split'
            return 'ponte'
    except Exception as ex:
        log.warning('[PUBSHOW dinheiro] rota do bar %s não lida: %s', b.get('id'), ex)
    if os.environ.get('ASAAS_API_KEY'):
        return 'asaas'
    return 'manual' if (b.get('pix_key') or '').strip() else 'sem'


_ROTA_TXT = {'split': 'Efí · divisão automática', 'ponte': 'Efí', 'asaas': 'Asaas', 'manual': 'PIX na chave do bar',
             'sem': 'sem cobrança'}


# ══ A conta (só banco) ═══════════════════════════════════════════════════════════════════════════════════════
def calcular(conn, agora):
    """Todos os números que saem do BANCO (sem Asaas/Efí). Recebe conn e o 'agora' (UTC); não grava nada."""
    L = limites(agora)
    T = tarifas()
    teste_codes = codigos_teste()
    bares = [dict(r) for r in conn.execute('SELECT * FROM pubshow_businesses ORDER BY id').fetchall()]
    teste_ids = {b['id'] for b in bares if (b.get('code') or '').strip().upper() in teste_codes}
    por = {b['id']: _bar_zero() for b in bares}

    def _st(bid):
        return por.setdefault(bid, _bar_zero())

    A, E = _ps._TEM_ASAAS, _ps._TEM_EFI
    st_ph = ','.join('?' * len(STATUS_PAGOS))
    mes_ini, noite_ini = _txt(L['mes_ini']), _txt(L['noite_ini'])
    desde = _txt(min(L['mes_ini'], L['noite_ini']))
    tar_asaas = _c(T['asaas'])

    # C: confirmado pelo Asaas/Efí (a regra dos Repasses, com os 4 status de pago)
    w, wp = _ps._repasse_where_pago(STATUS_PAGOS)
    for r in conn.execute(
            f'''SELECT business_id, valor, valor_cobrado, {A} AS eh_asaas,
                       datetime(pago_em) >= datetime(?) AS no_mes, datetime(pago_em) >= datetime(?) AS na_noite
                FROM pubshow_pedidos WHERE {w} AND datetime(pago_em) >= datetime(?)''',
            [mes_ini, noite_ini] + wp + [desde]).fetchall():
        s = _st(r['business_id'])
        cob = _c(r['valor_cobrado'] if r['valor_cobrado'] is not None else r['valor'])
        val = _c(r['valor'])
        if r['no_mes']:
            tarifa = tar_asaas if r['eh_asaas'] else _pct_cent(cob, T['efi_pct'])
            s['c_n'] += 1; s['c_cob'] += cob; s['c_val'] += val; s['c_marg'] += cob - tarifa - val
        if r['na_noite']:
            s['noite_n'] += 1; s['noite_cob'] += cob
    for r in conn.execute(f'SELECT business_id, MAX(datetime(pago_em)) AS u FROM pubshow_pedidos WHERE {w} '
                          f'GROUP BY business_id', wp).fetchall():
        _st(r['business_id'])['ult_pix'] = r['u']

    # X: divisão automática da Efí (centavos), pelo pago_em. Só o que ficou (o que está voltando pro cliente sai).
    # Margem = só a taxa de serviço (conv_cent) − tarifa: o plataforma_cent também tem a parte da mensalidade
    # (mens_cent) e a recuperação de devolução antiga (recupera_cent), que não são margem dos PIX.
    tem_x = _tem_tabela(conn, 'pubshow_pay_cobrancas')
    com_cob = set()
    if tem_x:
        xw, xconv = _x_vale(conn)
        for r in conn.execute(
                f'''SELECT c.business_id, c.valor_cent, c.bar_cent, {xconv} AS conv,
                          datetime(c.pago_em) >= datetime(?) AS no_mes, datetime(c.pago_em) >= datetime(?) AS na_noite
                   FROM pubshow_pay_cobrancas c LEFT JOIN pubshow_pedidos p ON p.id = c.pedido_id
                   WHERE {xw} AND datetime(c.pago_em) >= datetime(?)''',
                (mes_ini, noite_ini, desde)).fetchall():
            s = _st(r['business_id'])
            vc = int(r['valor_cent'] or 0)
            if r['no_mes']:
                s['x_n'] += 1; s['x_cob'] += vc; s['x_bar'] += int(r['bar_cent'] or 0)
                s['x_marg'] += int(r['conv'] or 0) - _pct_cent(vc, T['efi_pct'])
            if r['na_noite']:
                s['noite_n'] += 1; s['noite_cob'] += vc
        com_cob = {r[0] for r in conn.execute('SELECT pedido_id FROM pubshow_pay_cobrancas').fetchall()}
        for r in conn.execute(f"SELECT c.business_id, MAX(datetime(c.pago_em)) AS u FROM pubshow_pay_cobrancas c "
                              f"LEFT JOIN pubshow_pedidos p ON p.id = c.pedido_id WHERE {xw} "
                              f"GROUP BY c.business_id").fetchall():
            s = _st(r['business_id'])
            if r['u'] and (not s['ult_pix'] or r['u'] > s['ult_pix']):
                s['ult_pix'] = r['u']

    # S: contado como pago sem confirmação do banco (histórico, "PIX caiu" do admin, conferência com erro)
    for r in conn.execute(
            f'''SELECT business_id, valor, repasse_manual_id, datetime(pago_em) >= datetime(?) AS no_mes
                FROM pubshow_pedidos
                WHERE ({A} OR {E}) AND pago_em IS NOT NULL AND status IN ({st_ph})
                  AND (pix_conferido IN ('HISTORICO','ADMIN') OR pix_conferido LIKE 'ERRO%')''',
            [mes_ini] + list(STATUS_PAGOS)).fetchall():
        s = _st(r['business_id'])
        if r['no_mes']:
            s['s_n'] += 1; s['s_val'] += _c(r['valor'])
        if r['repasse_manual_id'] is None:
            s['s_aberto_n'] += 1; s['s_aberto_val'] += _c(r['valor'])

    # M (chave do bar, o dono confirmou), Xp (pedido da divisão automática) e M0 ("o cliente disse que pagou")
    for r in conn.execute(
            f'''SELECT business_id, valor, COALESCE(via_gateway,0) AS vg, pago_em,
                       datetime(pago_em) >= datetime(?) AS pago_mes, datetime(created_at) >= datetime(?) AS criado_mes
                FROM pubshow_pedidos
                WHERE NOT ({A} OR {E}) AND status IN ({st_ph}) AND {_PIX_CONF_SOMA}''',
            [mes_ini, mes_ini] + list(STATUS_PAGOS)).fetchall():
        s = _st(r['business_id'])
        if r['pago_em'] is not None:
            if r['pago_mes']:
                if r['vg']:
                    s['xp_n'] += 1; s['xp_val'] += _c(r['valor'])
                else:
                    s['m_n'] += 1; s['m_val'] += _c(r['valor'])
        elif not r['vg'] and _c(r['valor']) > 0 and r['criado_mes']:
            s['m0_n'] += 1

    # O que se deve a cada bar: a MESMA conta da tela 💸 Repasses (_repasses_calcular)
    rep = _ps._repasses_calcular(conn, agora)
    for x in rep['bares']:
        s = _st(x['id'])
        s['devido'] = x['devido_total']
        s['devido_efi'] = round(float(x['devido']['efi']), 2)
        s['devido_asaas'] = round(float(x['devido']['asaas']), 2)
    w2, wp2 = _ps._repasse_where_pago()
    for r in conn.execute(
            f'''SELECT business_id, MIN(datetime(pago_em)) AS d FROM pubshow_pedidos
                WHERE {w2} AND repasse_manual_id IS NULL AND datetime(created_at) <= datetime(?)
                GROUP BY business_id''', wp2 + [_txt(agora)]).fetchall():
        _st(r['business_id'])['repasse_desde'] = r['d']

    # Repassado e depois devolvido: o bar ficou com a parte de um PIX que voltou pro cliente. Só até o PRÓXIMO
    # repasse do bar (o desconto à mão é nele): repasse feito depois da devolução = o aviso sai.
    for r in conn.execute(
            "SELECT p.business_id, COUNT(*) AS n, COALESCE(SUM(p.valor),0) AS v FROM pubshow_pedidos p "
            "WHERE TRIM(COALESCE(p.estornado_em,''))<>'' AND p.repasse_manual_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM pubshow_repasses_manuais r WHERE r.business_id = p.business_id "
            "                AND datetime(COALESCE(r.criado_em, r.ate_em)) > datetime(p.estornado_em)) "
            "GROUP BY p.business_id").fetchall():
        s = _st(r['business_id'])
        s['rep_dev_n'], s['rep_dev_val'] = int(r['n']), _c(r['v'])

    # Devoluções do mês (o Asaas avisou a devolução: estornado_em)
    for r in conn.execute(
            "SELECT business_id, COUNT(*) AS n FROM pubshow_pedidos WHERE TRIM(COALESCE(estornado_em,''))<>'' "
            "AND datetime(estornado_em) >= datetime(?) GROUP BY business_id", (mes_ini,)).fetchall():
        _st(r['business_id'])['dev_mes_n'] = int(r['n'])

    # PIX a devolver: pago e recusado, sem devolução registrada (o "↩️ PIX a devolver" do /pubshow/admin)
    auto = set()
    if tem_x:
        auto = {r[0] for r in conn.execute("SELECT pedido_id FROM pubshow_pay_cobrancas "
                                           "WHERE status IN ('devolvendo','devolvida')").fetchall()}
    a_devolver = []
    lim24 = agora - timedelta(hours=HORAS_DEVOLUCAO)
    for r in conn.execute(
            f'''SELECT id, business_id, valor, valor_cobrado, pago_em, {A} AS eh_asaas, {E} AS eh_efi
                FROM pubshow_pedidos WHERE status='recusado' AND pago_em IS NOT NULL AND estornado_em IS NULL
                ORDER BY id''').fetchall():
        if r['id'] in auto or r['business_id'] in teste_ids:
            continue
        pe = _parse(r['pago_em'])
        split = r['id'] in com_cob
        # a devolução da divisão automática sai da conta Efí do PubShow (a mesma da ponte): linha da Efí
        a_devolver.append({'id': r['id'], 'business_id': r['business_id'],
                           'cent': _c(r['valor_cobrado'] if r['valor_cobrado'] is not None else r['valor']),
                           'onde': 'asaas' if r['eh_asaas'] else ('efi' if (r['eh_efi'] or split) else 'outro'),
                           # ponte: devolvida no app da Efí, o sistema não fica sabendo (até a F4d) → a leitura confere
                           'ponte': bool(r['eh_efi'] and not r['eh_asaas'] and not split),
                           'parada': bool(pe and pe < lim24)})

    # Devoluções da divisão automática que travaram (só com a Efí ligada): pubshow_pay.devolucoes_travadas, a MESMA
    # lista que o resumo_admin mostra na tela antiga (só um SELECT)
    travadas = []
    if _ps._efi_ligado() and tem_x:
        try:
            travadas = [t for t in _ps.pubshow_pay.devolucoes_travadas(conn, agora) if t['business_id'] not in teste_ids]
        except Exception as ex:
            log.warning('[PUBSHOW dinheiro] devoluções da divisão automática não lidas: %s', ex)

    ass = {}
    for r in conn.execute('SELECT * FROM pubshow_assinaturas ORDER BY id').fetchall():
        ass[r['business_id']] = dict(r)

    return {'L': L, 'T': T, 'bares': bares, 'teste_ids': teste_ids, 'por': por, 'rep': rep,
            'a_devolver': a_devolver, 'travadas': travadas, 'ass': ass, 'tem_x': tem_x}


def identidade(conn, agora):
    """Pro teste: {bar: (C + S + M + Xp do mês, somar_pagos do mês)} em reais. Têm que ser iguais ao centavo."""
    calc = calcular(conn, agora)
    out = {}
    for bid, s in calc['por'].items():
        meu = _r(s['c_val'] + s['s_val'] + s['m_val'] + s['xp_val'])
        out[bid] = (meu, _ps.somar_pagos(conn, bid, de=calc['L']['mes_de'], conta='tudo')['total'])
    return out


# ══ A leitura no Asaas/Efí (só GET) ══════════════════════════════════════════════════════════════════════════
def _leitura_vazia(agora, motivo=''):
    return {'configurado': bool(os.environ.get('ASAAS_API_KEY')), 'motivo': motivo, 'completo': False,
            'lido_em': _txt(agora), 'previstas': 0, 'feitas': 0, 'saldo': None, 'saldo_ok': False,
            'perdidos': [], 'perdidos_alvo': 0, 'perdidos_conferidos': 0, 'perdidos_total': 0, 'perdidos_fora': 0,
            'assin': {}, 'clientes': {}, 'desconhecidas': [], 'dev_efi': {}}


def _plano_leitura(conn, agora):
    """O que perguntar: os QR sem pagamento dos últimos 7 dias, as assinaturas que o banco conhece e os clientes
    do Asaas dos bares de verdade (pra achar assinatura feita à mão no painel do Asaas)."""
    teste_codes = codigos_teste()
    teste_ids = {r['id'] for r in conn.execute('SELECT id, code FROM pubshow_businesses').fetchall()
                 if (r['code'] or '').strip().upper() in teste_codes}
    A, E = _ps._TEM_ASAAS, _ps._TEM_EFI
    orfaos = [dict(r) for r in conn.execute(
        f'''SELECT id, business_id, status, asaas_payment_id, efi_txid, valor, valor_cobrado, created_at
            FROM pubshow_pedidos
            WHERE ({A} OR {E}) AND status IN ('aguardando_pix','recusado')
              AND TRIM(COALESCE(pago_em,''))='' AND TRIM(COALESCE(estornado_em,''))=''
              AND datetime(created_at) >= datetime(?)
            ORDER BY id DESC''', (_txt(agora - timedelta(days=DIAS_PERDIDOS)),)).fetchall()
              if r['business_id'] not in teste_ids]
    conhecidas = {(r[0] or '').strip() for r in conn.execute(
        'SELECT asaas_subscription_id FROM pubshow_assinaturas').fetchall() if (r[0] or '').strip()}
    subs = [{'business_id': r['business_id'], 'sub': r['asaas_subscription_id'].strip()} for r in conn.execute(
        "SELECT business_id, asaas_subscription_id FROM pubshow_assinaturas "
        "WHERE TRIM(COALESCE(asaas_subscription_id,''))<>'' AND COALESCE(status,'')<>'cancelado' ORDER BY id").fetchall()
        if r['business_id'] not in teste_ids]
    clientes = [{'business_id': r['id'], 'cliente': r['asaas_customer_id'].strip()} for r in conn.execute(
        "SELECT id, asaas_customer_id FROM pubshow_businesses WHERE TRIM(COALESCE(asaas_customer_id,''))<>'' "
        "ORDER BY id").fetchall() if r['id'] not in teste_ids]
    # PIX da ponte Efí a devolver (pago, recusado, sem devolução registrada): a devolução é no app da Efí e o sistema
    # não fica sabendo (até a F4d). A leitura pergunta à Efí se já foi devolvido.
    split = set()
    if _tem_tabela(conn, 'pubshow_pay_cobrancas'):
        split = {r[0] for r in conn.execute('SELECT pedido_id FROM pubshow_pay_cobrancas').fetchall()}
    ponte_dev = [dict(r) for r in conn.execute(
        f'''SELECT id, business_id, efi_txid FROM pubshow_pedidos
            WHERE status='recusado' AND pago_em IS NOT NULL AND estornado_em IS NULL AND {E} AND NOT {A}
            ORDER BY id''').fetchall() if r['business_id'] not in teste_ids and r['id'] not in split]
    return orfaos, subs, clientes, conhecidas, ponte_dev


def _pagamento(p):
    return {'id': str(p.get('id') or ''), 'situacao': str(p.get('status') or '').strip().upper(), 'valor': p.get('value'),
            'vencimento': str(p.get('dueDate') or '')[:10],
            'pago_em': str(p.get('paymentDate') or p.get('clientPaymentDate') or p.get('confirmedDate') or '')[:10]}


def _executar(tarefa):
    """Uma pergunta ao Asaas/Efí. Só GET. Devolve {'ok': bool, ...}; nunca levanta exceção."""
    tipo, arg = tarefa
    try:
        if tipo == 'saldo':
            v = _ps._asaas_saldo()
            return {'ok': v is not None, 'saldo': v}
        if tipo == 'devefi':
            efi = (arg.get('efi_txid') or '').strip()
            if not _RE_TXID.match(efi):
                return {'ok': False}
            cli = _ps._efi_cli()
            if cli is None or not cli.configurado():
                return {'ok': False}
            c = cli.consultar(efi, timeout=TIMEOUT_EFI_S)
            if isinstance(c, dict) and not c.get('erro') and c.get('status'):
                devolvido = int(c.get('devolvido_cent') or 0) > 0 or any(
                    str(d.get('status') or '').upper() == 'DEVOLVIDO'
                    for p in (c.get('pix') or []) for d in (p.get('devolucoes') or []))
                return {'ok': True, 'devolvido': devolvido}
            return {'ok': False}
        if tipo == 'pix':
            efi = (arg.get('efi_txid') or '').strip()
            if efi:
                if not _RE_TXID.match(efi):
                    return {'ok': True, 'pago': False}
                cli = _ps._efi_cli()
                if cli is None or not cli.configurado():
                    return {'ok': False}
                c = cli.consultar(efi, timeout=TIMEOUT_EFI_S)
                if isinstance(c, dict) and not c.get('erro') and c.get('status'):
                    devolvido = int(c.get('devolvido_cent') or 0) > 0 or any(
                        str(d.get('status') or '').upper() == 'DEVOLVIDO'
                        for p in (c.get('pix') or []) for d in (p.get('devolucoes') or []))
                    return {'ok': True, 'pago': bool(c.get('pago')) and not devolvido, 'onde': 'Efí'}
                return {'ok': False}
            pay = (arg.get('asaas_payment_id') or '').strip()
            if not _RE_ID.match(pay):
                return {'ok': True, 'pago': False}
            r = _ps._asaas_req('GET', f'/payments/{pay}')
            st = r.get('status') if isinstance(r, dict) else None
            if isinstance(st, str) and st.strip():
                return {'ok': True, 'pago': st.strip().upper() in PAGO_ASAAS, 'onde': 'Asaas'}
            if isinstance(r, dict) and isinstance(r.get('errors'), list):
                codigos = {str(e.get('code') or '').lower() for e in r['errors'] if isinstance(e, dict)}
                if codigos and codigos <= NAO_EXISTE_ASAAS:
                    return {'ok': True, 'pago': False}   # o Asaas respondeu que não existe
            return {'ok': False}
        if tipo == 'assin':
            if not _RE_ID.match(arg or ''):
                return {'ok': False}
            r = _ps._asaas_req('GET', f'/subscriptions/{arg}/payments?limit=100')
            if isinstance(r, dict) and isinstance(r.get('data'), list):
                return {'ok': True, 'pagamentos': [_pagamento(p) for p in r['data'] if isinstance(p, dict)]}
            return {'ok': False}
        if tipo == 'cliente':
            cid = (arg or {}).get('cliente') or ''
            if not _RE_ID.match(cid):
                return {'ok': False}
            r = _ps._asaas_req('GET', f'/subscriptions?customer={cid}&limit=100')
            if isinstance(r, dict) and isinstance(r.get('data'), list):
                subs = [{'id': str(s.get('id') or ''), 'valor': s.get('value'), 'proximo': str(s.get('nextDueDate') or '')[:10],
                         'ref': str(s.get('externalReference') or ''), 'situacao': str(s.get('status') or '').upper()}
                        for s in r['data'] if isinstance(s, dict) and not s.get('deleted')]
                return {'ok': True, 'subs': subs}
            return {'ok': False}
    except Exception as ex:
        log.warning('[PUBSHOW dinheiro] pergunta %s falhou: %s', tipo, type(ex).__name__)
    return {'ok': False}


def _rodar(tarefas, limite):
    """Roda as perguntas em paralelo até o 'limite' (time.monotonic()). O que não respondeu a tempo fica de fora."""
    if not tarefas:
        return {}
    ex = _cf.ThreadPoolExecutor(max_workers=min(TRABALHADORES, len(tarefas)), thread_name_prefix='ps-dinheiro')
    try:
        futs = {ex.submit(_executar, t): i for i, t in enumerate(tarefas)}
        feitos, _ = _cf.wait(futs, timeout=max(0.0, limite - time.monotonic()))
        out = {}
        for f in feitos:
            try:
                out[futs[f]] = f.result()
            except Exception:
                out[futs[f]] = {'ok': False}
        return out
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def ler_asaas_efi(agora, prazo_s=None):
    """A leitura inteira (só GET): saldo do Asaas, PIX perdido dos últimos 7 dias, pagamentos das assinaturas e as
    assinaturas que o banco não conhece. Sem ASAAS_API_KEY não pergunta NADA. Nunca grava no banco."""
    if not os.environ.get('ASAAS_API_KEY'):
        return _leitura_vazia(agora, 'sem_chave')
    prazo = PRAZO_TOTAL_S if prazo_s is None else float(prazo_s)
    out = _leitura_vazia(agora)
    ini = time.monotonic()
    limite = ini + prazo
    conn = _conn()
    try:
        orfaos, subs, clientes, conhecidas, ponte_dev = _plano_leitura(conn, agora)
    finally:
        conn.close()
    alvo = orfaos[:MAX_PERDIDOS]
    out['perdidos_total'], out['perdidos_alvo'] = len(orfaos), len(alvo)
    out['perdidos_fora'] = len(orfaos) - len(alvo)
    t1 = ([('saldo', None)] + [('pix', o) for o in alvo] + [('assin', s['sub']) for s in subs]
          + [('cliente', c) for c in clientes] + [('devefi', x) for x in ponte_dev])
    r1 = _rodar(t1, limite)
    feitas = 0
    vistas = {}   # assinatura que o banco não conhece → 1 registro só, com as contas que dividem aquele cliente
    for i, (tipo, arg) in enumerate(t1):
        res = r1.get(i) or {'ok': False}
        feitas += 1 if res.get('ok') else 0
        if tipo == 'saldo':
            out['saldo'], out['saldo_ok'] = res.get('saldo'), bool(res.get('ok'))
        elif tipo == 'pix':
            if res.get('ok'):
                out['perdidos_conferidos'] += 1
                if res.get('pago'):
                    out['perdidos'].append({'id': arg['id'], 'business_id': arg['business_id'],
                                            'onde': res.get('onde') or 'Asaas', 'pedido': arg['status'],
                                            'cent': _c(arg['valor_cobrado'] if arg['valor_cobrado'] is not None
                                                       else arg['valor'])})
        elif tipo == 'assin':
            out['assin'][arg] = {'ok': bool(res.get('ok')), 'pagamentos': res.get('pagamentos') or []}
        elif tipo == 'cliente':
            out['clientes'][arg['business_id']] = bool(res.get('ok'))
            for s in res.get('subs') or []:
                if not s['id'] or s['id'] in conhecidas:
                    continue
                if s['ref'] and not s['ref'].startswith('pubshow_'):
                    continue   # de outro SaaS na mesma conta do Asaas
                if s['situacao'] not in ('', 'ACTIVE'):
                    continue
                v = vistas.setdefault(s['id'], {'sub': s['id'], 'valor': s['valor'], 'proximo': s['proximo'],
                                                'ref': s['ref'], 'contas': []})
                if arg['business_id'] not in v['contas']:
                    v['contas'].append(arg['business_id'])
        elif tipo == 'devefi':
            out['dev_efi'][arg['id']] = {'ok': bool(res.get('ok')), 'devolvido': bool(res.get('devolvido'))}
    out['desconhecidas'] = list(vistas.values())
    # 2ª rodada: os pagamentos das assinaturas que o banco não conhece (o cartão usa os pagamentos delas)
    novas = sorted({x['sub'] for x in out['desconhecidas']} - set(out['assin']))
    t2 = [('assin', s) for s in novas]
    r2 = _rodar(t2, limite)
    for i, (tipo, arg) in enumerate(t2):
        res = r2.get(i) or {'ok': False}
        feitas += 1 if res.get('ok') else 0
        out['assin'][arg] = {'ok': bool(res.get('ok')), 'pagamentos': res.get('pagamentos') or []}
    out['previstas'], out['feitas'] = len(t1) + len(t2), feitas
    out['completo'] = feitas == out['previstas'] and out['perdidos_fora'] == 0
    log.info('[PUBSHOW dinheiro] leitura no Asaas/Efí: %d de %d em %.1f s (%d PIX perdido)',
             feitas, out['previstas'], time.monotonic() - ini, len(out['perdidos']))
    return out


# Cache por processo + leitura em segundo plano: o 4kitem roda com 1 worker (Procfile); a leitura (até 12 s) nunca
# segura o request — a página pergunta de novo a cada ~1 s até a leitura ficar pronta.
_ESTADO = {'leitura': None, 'mono': None, 'rodando': False, 'inicio': None}
_TRAVA = threading.Lock()


def limpar_cache():
    with _TRAVA:
        _ESTADO.update(leitura=None, mono=None, rodando=False, inicio=None)


def _disparar(agora):
    """Chamada com _TRAVA na mão: liga a leitura numa thread e volta na hora."""
    _ESTADO['rodando'], _ESTADO['inicio'] = True, time.monotonic()

    def _run():
        try:
            nova = ler_asaas_efi(agora)
        except Exception as ex:
            log.error('[PUBSHOW dinheiro] leitura no Asaas/Efí quebrou: %s', type(ex).__name__)
            nova = _leitura_vazia(agora, 'erro')
        with _TRAVA:
            _ESTADO.update(leitura=nova, mono=time.monotonic(), rodando=False)

    threading.Thread(target=_run, daemon=True, name='pubshow-dinheiro-leitura').start()


def leitura_atual(agora, de_novo=False):
    """(leitura ou None, info). None = está lendo agora: pergunte de novo em instantes.
    Vale 10 min; de_novo=True refaz, no máximo 1 vez por minuto. Sem a chave do Asaas não pergunta nada."""
    if not os.environ.get('ASAAS_API_KEY'):
        return _leitura_vazia(agora, 'sem_chave'), {'cache': False, 'de_novo_cedo': False}
    with _TRAVA:
        if _ESTADO['rodando'] and time.monotonic() - (_ESTADO['inicio'] or 0) < PRAZO_TOTAL_S + 30:
            return None, {'rodando': True}
        lei, mono = _ESTADO['leitura'], _ESTADO['mono']
        if lei is not None and mono is not None:
            idade = time.monotonic() - mono
            if de_novo and idade < DE_NOVO_MIN_S:
                return lei, {'cache': True, 'de_novo_cedo': True}
            if not de_novo and idade < CACHE_S:
                return lei, {'cache': True, 'de_novo_cedo': False}
        _disparar(agora)
        return None, {'rodando': True}


# ══ Mensalidade de cada conta ════════════════════════════════════════════════════════════════════════════════
def _m(icone, cor, texto, estado, lido=False, **extra):
    d = {'icone': icone, 'cor': cor, 'texto': texto, 'estado': estado, 'lido': lido}
    d.update(extra)
    return d


def mensalidade(b, ass, rota, agora, pagamentos=None, desconhecida=None):
    """A frase da mensalidade de UMA conta. pagamentos = a lista lida no Asaas AGORA (das assinaturas desta conta:
    a do banco e as que o banco não conhece), ou None se não foi lida inteira. desconhecida = a 1ª assinatura achada
    no Asaas que o banco não conhece (ou None). 🟢 SÓ com pagamento lido agora; o resto sai do banco."""
    ab = agora + _BRT
    mes, hoje = ab.strftime('%Y-%m'), ab.strftime('%Y-%m-%d')
    st = (ass or {}).get('status') or ''
    tem_ass = st in ('pendente', 'ativo', 'inadimplente') or desconhecida is not None
    if b.get('suspenso'):
        return _m('⛔', 'cinza', 'suspenso', 'suspenso')
    if st == 'suspenso':   # a carência da mensalidade acabou (pubshow.py:9647-9651)
        return _m('⛔', 'cinza', 'suspenso por mensalidade não paga', 'suspenso')
    venc_asaas = ''
    lido = pagamentos is not None and tem_ass
    if lido:
        atras = sorted((p for p in pagamentos if p['situacao'] == 'OVERDUE'), key=lambda p: p['vencimento'])
        if atras:
            return _m('🔴', 'verm', f"atrasada desde {_ddmm(atras[0]['vencimento'])} (lido no Asaas agora)",
                      'atrasada', True, desde=_ddmm(atras[0]['vencimento']))
        pagos = sorted((p for p in pagamentos if p['situacao'] in PAGO_ASAAS and p['vencimento'][:7] == mes),
                       key=lambda p: p['vencimento'])
        if pagos:
            p = pagos[-1]
            return _m('🟢', 'verde', f"{_MESES[ab.month - 1]} paga em {_ddmm(p['pago_em'] or p['vencimento'])} · "
                                     f"{brl(p['valor'])} (lido no Asaas agora)", 'paga', True,
                      valor=round(float(p['valor'] or 0), 2),
                      pag_id=p.get('id') or f"{p['vencimento']}|{p['valor']}|{p['pago_em']}")
        baixa = sorted((p for p in pagamentos if p['situacao'] == BAIXA_MANUAL_ASAAS and p['vencimento'][:7] == mes),
                       key=lambda p: p['vencimento'])
        if baixa:   # marcada como recebida à mão no Asaas: não prova que o dinheiro entrou (pubshow.py:8128)
            p = baixa[-1]
            return _m('⚪', 'cinza', f"{_MESES[ab.month - 1]} marcada como paga à mão no Asaas em "
                                     f"{_ddmm(p['pago_em'] or p['vencimento'])} · {brl(p['valor'])} "
                                     f"(sem PIX no Asaas: confira)", 'baixa_manual', True)
        pend = sorted((p for p in pagamentos if p['situacao'] == 'PENDING'), key=lambda p: p['vencimento'])
        venc_asaas = _ddmm(pend[0]['vencimento']) if pend else ''
    if st == 'pendente':
        v = venc_asaas or _ddmm(ass.get('proximo_vencimento'))
        return _m('⚪', 'cinza', 'assinatura criada · ' + (f'1ª fatura vence {v} · ' if v else '') + 'ainda não paga'
                  + (' (lido no Asaas agora)' if lido else ''), 'criada', lido)
    if st == 'inadimplente' and not lido:
        d = _ddmm(ass.get('inadimplente_desde'))
        return _m('🔴', 'verm', 'atrasada' + (f' desde {d}' if d else ''), 'atrasada', desde=d)
    if st in ('ativo', 'inadimplente'):
        if lido:
            return _m('⚪', 'cinza', (f'fatura vence {venc_asaas} · ainda não paga' if venc_asaas
                                      else 'nenhuma fatura deste mês paga') + ' (lido no Asaas agora)', 'criada', True)
        v = _ddmm(ass.get('proximo_vencimento'))
        return _m('⚪', 'cinza', 'assinatura criada · ' + (f'vence {v} · ' if v else '') + 'pagamento não conferido',
                  'criada')
    if desconhecida is not None:
        v = venc_asaas or _ddmm(desconhecida.get('proximo'))
        return _m('⚪', 'cinza', 'assinatura feita à mão no Asaas · ' + (f'fatura vence {v} · ' if v else '')
                  + ('ainda não paga (lido no Asaas agora)' if lido else 'pagamento não conferido'), 'criada', lido)
    ate = (b.get('ativo_ate') or '')[:10]
    if ate and ate >= hoje:
        return _m('🟡', 'amar', f'cancelou, aberto até {_ddmm(ate)}', 'cancelou')
    te = _parse(b.get('trial_ends'))
    if te and te > agora:
        return _m('🟡', 'amar', f'em teste até {te.strftime("%d/%m")}', 'teste')
    if rota == 'split':
        return _m('💸', 'cinza', 'sai dos momentos (divisão automática)', 'split')
    if b.get('plano_ativo'):
        base = te or _parse(b.get('created_at')) or agora
        dias = max(0, (agora - base).days)
        return _m('🟠', 'amar', f'aberto sem cobrança há {dias} {_plural(dias, "dia", "dias")}'
                  + (f' (teste acabou {te.strftime("%d/%m")})' if te else ''), 'aberto', dias=dias,
                  teste_fim=(te.strftime('%d/%m') if te else ''))
    return _m('⚪', 'cinza', 'fechado', 'fechado')


# ══ A página inteira (banco + leitura) ═══════════════════════════════════════════════════════════════════════
def _aviso(nivel, titulo, detalhe, botao='', href='', de_novo=False, ordem=0):
    """botao='' = aviso sem botão (quando não há nada pra tocar que resolva)."""
    return {'nivel': nivel, 'titulo': titulo, 'detalhe': detalhe, 'botao': botao, 'href': href,
            'de_novo': de_novo, 'ordem': ordem, 'pri': False}


def _lista_ids(ids, n=4):
    s = ', '.join(f'#{i}' for i in ids[:n])
    return s + (f' e mais {len(ids) - n}' if len(ids) > n else '')


def donates_sistema(conn, agora, limite=50):
    """27/10/26 (D6): o Donate para o sistema, 100% do app. Só o admin vê (nunca o painel, o caixa nem o repasse)."""
    vazio = {'n': 0, 'total_txt': brl(0), 'linhas': []}
    try:
        w = "p.tipo='donate_sistema' AND p.status='sistema' AND p.pago_em IS NOT NULL AND p.estornado_em IS NULL"
        n, tot = conn.execute(f'SELECT COUNT(*), COALESCE(SUM(p.valor_cobrado),0) FROM pubshow_pedidos p WHERE {w}').fetchone()
        rows = conn.execute(
            f'''SELECT p.id, p.nome_cliente, p.mensagem, p.valor_cobrado, p.pago_em, b.nome AS bar
                FROM pubshow_pedidos p LEFT JOIN pubshow_businesses b ON b.id = p.business_id
                WHERE {w} ORDER BY datetime(p.pago_em) DESC, p.id DESC LIMIT ?''', (int(limite),)).fetchall()
    except Exception as ex:
        log.warning('[PUBSHOW] donates do sistema não lidos: %s', ex)
        return vazio
    return {'n': int(n or 0), 'total_txt': brl(float(tot or 0)),
            'linhas': [{'id': r['id'], 'quando': _quando(r['pago_em'], agora), 'bar': r['bar'] or '—',
                        'nome': r['nome_cliente'] or '—', 'recado': r['mensagem'] or '',
                        'valor_txt': brl(float(r['valor_cobrado'] or 0))} for r in rows]}


def montar(conn, agora, leitura=None):
    """Tudo que a página mostra. leitura=None: a leitura no Asaas/Efí ainda não chegou (a tela nunca fica verde)."""
    calc = calcular(conn, agora)
    L, T, por, teste_ids, rep = calc['L'], calc['T'], calc['por'], calc['teste_ids'], calc['rep']
    bares = {b['id']: b for b in calc['bares']}
    reais = [b for b in calc['bares'] if b['id'] not in teste_ids]
    nome = {b['id']: (b.get('nome') or f"Bar #{b['id']}") for b in calc['bares']}
    ab = L['agora_brt']
    lido = leitura is not None and leitura.get('configurado') and leitura.get('motivo') != 'sem_chave'

    # PIX perdido da leitura: confere de novo no banco AGORA (o "PIX caiu" pode ter sido dado depois da leitura)
    perdidos = []
    if leitura:
        ids = [p['id'] for p in leitura.get('perdidos') or []]
        ainda = set()
        if ids:
            ainda = {r[0] for r in conn.execute(
                f"SELECT id FROM pubshow_pedidos WHERE id IN ({','.join('?' * len(ids))}) "
                "AND status IN ('aguardando_pix','recusado') AND TRIM(COALESCE(pago_em,''))='' "
                "AND TRIM(COALESCE(estornado_em,''))=''", ids).fetchall()}
        perdidos = [p for p in leitura.get('perdidos') or [] if p['id'] in ainda and p['business_id'] not in teste_ids]

    # ── mensalidade de cada conta ─────────────────────────────────────────────────────────────────────────────
    # Assinatura que o banco não conhece: presa a UMA conta só. Com a referência 'pubshow_<id>_…' (a que o sistema
    # grava), é daquela conta. Sem referência: só se o cliente do Asaas for de 1 conta; cliente dividido por 2+ contas
    # (o _asaas_criar_ou_buscar_cliente reaproveita o cliente pelo CPF/CNPJ) = "de qual conta?", sem verde pra nenhuma.
    desconhecidas, sem_dono = {}, []
    for x in (leitura or {}).get('desconhecidas') or []:
        contas = [i for i in (x.get('contas') or []) if i not in teste_ids and i in bares]
        m_ref = re.match(r'^pubshow_(\d+)_', x.get('ref') or '')
        dono = int(m_ref.group(1)) if m_ref else None
        if dono is not None and dono in teste_ids:
            continue
        if dono is None or dono not in bares:
            dono = contas[0] if len(contas) == 1 else None
        if dono is not None:
            desconhecidas.setdefault(dono, []).append(x)
        elif contas:
            sem_dono.append(dict(x, contas=contas))
    rotas = {b['id']: _rota(conn, b) for b in calc['bares']}
    mens = {}
    for b in calc['bares']:
        a = calc['ass'].get(b['id'])
        subs = []
        if a and (a.get('asaas_subscription_id') or '').strip() and (a.get('status') or '') != 'cancelado':
            subs.append(a['asaas_subscription_id'].strip())
        subs += [x['sub'] for x in desconhecidas.get(b['id'], [])]
        pags = None
        if leitura and lido and subs and all((leitura['assin'].get(s) or {}).get('ok') for s in subs):
            pags = [p for s in subs for p in leitura['assin'][s]['pagamentos']]
        mens[b['id']] = mensalidade(b, a, rotas[b['id']], agora, pags,
                                    (desconhecidas.get(b['id']) or [None])[0])

    # ── grupos de telefone (só bares de verdade; cada conta segue com o seu cartão) ─────────────────────────────
    grupos = {}
    for b in reais:
        f = _fone_chave(b.get('telefone'))
        if f:
            grupos.setdefault(f, []).append(b['id'])
    mesmo_fone = {f: ids for f, ids in grupos.items() if len(ids) > 1}
    fone_de = {bid: f for f, ids in mesmo_fone.items() for bid in ids}

    def _tem_assinatura(bid):
        a = calc['ass'].get(bid)
        return bool(desconhecidas.get(bid)) or bool(a and (a.get('status') or '') in
                                                     ('pendente', 'ativo', 'inadimplente', 'suspenso'))

    # ── avisos ────────────────────────────────────────────────────────────────────────────────────────────────
    avisos = []
    # 🔴 PIX perdido (da leitura)
    for tipo in ('aguardando_pix', 'recusado'):
        porbar = {}
        for p in perdidos:
            if p['pedido'] == tipo:
                porbar.setdefault(p['business_id'], []).append(p)
        for bid, ps in sorted(porbar.items()):
            n, cent = len(ps), sum(p['cent'] for p in ps)
            ondes = sorted({p['onde'] for p in ps})
            no = ' e '.join({'Efí': 'na Efí'}.get(o, 'no ' + o) for o in ondes)
            pelo = ' e '.join({'Efí': 'pela Efí'}.get(o, 'pelo ' + o) for o in ondes)
            ids = [p['id'] for p in ps]
            if tipo == 'aguardando_pix':
                # a tela do bar só lista os 50 pedidos mais novos (pubshow.py admin_bar): avisa quem já saiu de lá
                fora = [i for i in ids if conn.execute(
                    'SELECT COUNT(*) FROM pubshow_pedidos WHERE business_id=? AND created_at > '
                    '(SELECT created_at FROM pubshow_pedidos WHERE id=?)', (bid, i)).fetchone()[0] >= 50]
                det = (f"{_plural(n, 'Pedido', 'Pedidos')} {_lista_ids(ids)}. No bar, toque em \"PIX caiu\" em cada um; "
                       f"depois, \"Conferir agora\" nos Repasses.")
                if fora:
                    det += (f" {_lista_ids(fora)} já {_plural(len(fora), 'saiu', 'saíram')} da lista do bar (ela mostra "
                            f"só os 50 pedidos mais novos): anote o número.")
                avisos.append(_aviso('r', f"{nome[bid]}: {n} PIX {_plural(n, 'pago', 'pagos')} {no} e o pedido não "
                                          f"foi liberado · {brl(_r(cent))}", det,
                                     'Abrir o bar', f'/pubshow/admin/bar/{bid}', ordem=10))
            else:
                # a tela do bar não tem o que fazer com pedido recusado: a devolução é no app do banco (sem botão)
                avisos.append(_aviso('r', f"{nome[bid]}: {n} PIX {_plural(n, 'pago', 'pagos')} em pedido recusado · "
                                          f"{brl(_r(cent))} a devolver",
                                     f"{_plural(n, 'Pedido', 'Pedidos')} {_lista_ids(ids)}. O cliente pagou e o pedido foi "
                                     f"recusado: devolva {pelo}, no app.", ordem=11))
    # 🔴 Devolução parada há mais de 24 h (PIX a devolver + devoluções travadas da divisão automática)
    # Ponte Efí: a devolução é no app da Efí e não grava nada aqui (até a F4d). A leitura pergunta à Efí: já devolvido
    # = linha cinza "falta registrar" (fora do "a devolver"); sem resposta da Efí = 🟠 "conferir", não 🔴.
    dev_lida = ((leitura or {}).get('dev_efi') or {}) if lido else {}
    a_dev, ja_devolvido = [], []
    for x in calc['a_devolver']:
        if x['ponte']:
            r = dev_lida.get(x['id']) or {}
            if r.get('ok') and r.get('devolvido'):
                ja_devolvido.append(x)
                continue
            x = dict(x, conferido=bool(r.get('ok')))
        a_dev.append(x)
    paradas = [{'id': x['id'], 'business_id': x['business_id'], 'cent': x['cent']}
               for x in a_dev if x['parada'] and (not x['ponte'] or x['conferido'])]
    conferir = [x for x in a_dev if x['parada'] and x['ponte'] and not x['conferido']]
    vistos = {x['id'] for x in paradas + conferir + ja_devolvido}
    travadas = []
    for t in calc['travadas']:
        if t['pedido_id'] not in vistos:
            travadas.append({'id': t['pedido_id'], 'business_id': t['business_id'], 'cent': int(t['valor_cent'] or 0)})
            vistos.add(t['pedido_id'])
    if paradas or travadas:
        todas = paradas + travadas
        n, cent = len(todas), sum(x['cent'] for x in todas)
        quem = ', '.join(f"{nome.get(x['business_id'], 'bar')} #{x['id']}" for x in todas[:3])
        det = f"{quem}{' e mais' if n > 3 else ''}. PIX pago em pedido recusado, ainda sem devolver ao cliente."
        if travadas:   # a 'devolvendo' travada não aparece no "PIX a devolver" (pubshow.py:8054): fica na tela antiga
            det += (f" {_plural(len(travadas), 'A da divisão automática travada está', 'As da divisão automática travadas estão')}"
                    f" em \"Devoluções travadas\", na tela antiga.")
        botao, href = (('Abrir PIX a devolver', '/pubshow/admin') if paradas else ('Abrir a tela antiga', '/saas-admin'))
        avisos.append(_aviso('r', f"{n} {_plural(n, 'devolução parada', 'devoluções paradas')} há mais de 24 h · "
                                  f"{brl(_r(cent))}", det, botao, href, ordem=20))
    if conferir:
        n, cent = len(conferir), sum(x['cent'] for x in conferir)
        avisos.append(_aviso('a', f"{n} {_plural(n, 'devolução', 'devoluções')} pela Efí a conferir · {brl(_r(cent))}",
                             f"{_plural(n, 'Pedido', 'Pedidos')} {_lista_ids([x['id'] for x in conferir])}, pago há mais "
                             f"de 24 h e recusado. A devolução pela Efí é feita no app e o sistema não fica sabendo: "
                             f"esta página pergunta à Efí quando consegue.", 'Abrir PIX a devolver', '/pubshow/admin',
                             ordem=52))
    # 🔴 Bar esperando repasse há mais de 7 dias
    for b in reais:
        s = por[b['id']]
        d = _parse(s['repasse_desde'])
        if d and s['devido'] > 0 and d < agora - timedelta(days=DIAS_REPASSE):
            dias = (agora - d).days
            avisos.append(_aviso('r', f"{nome[b['id']]} espera repasse há {dias} dias · {brl(s['devido'])}",
                                 f"PIX confirmado mais antigo sem \"Repassei\": {(d + _BRT).strftime('%d/%m')}. "
                                 f"O combinado é toda segunda.", 'Abrir Repasses', '/pubshow/admin/repasses', ordem=30))
    # 🔴 Repassado e depois devolvido
    for b in reais:
        s = por[b['id']]
        if s['rep_dev_n']:
            n = s['rep_dev_n']
            avisos.append(_aviso('r', f"{nome[b['id']]} recebeu repasse de {n} PIX que depois "
                                      f"{_plural(n, 'foi devolvido', 'foram devolvidos')}: o bar te deve "
                                      f"{brl(_r(s['rep_dev_val']))}",
                                 'Desconte à mão no próximo repasse.', 'Abrir Repasses', '/pubshow/admin/repasses',
                                 ordem=40))
    # 🟠 Não deu pra conferir no Asaas/Efí agora
    if leitura is not None and not leitura.get('completo'):
        if not leitura.get('configurado') or leitura.get('motivo') == 'sem_chave':
            # tentar de novo não resolve: sem botão
            avisos.append(_aviso('a', 'Não deu pra conferir no Asaas/Efí agora',
                                 'A chave do Asaas não está configurada neste servidor: nada foi perguntado.', ordem=50))
            det = None
        elif leitura.get('motivo') == 'erro':
            det = 'A conferência quebrou no meio. Tente de novo em 1 minuto.'
        else:
            det = f"Conferi {leitura['feitas']} de {leitura['previstas']}; o resto não respondeu a tempo."
            if leitura.get('perdidos_fora'):
                det += (f" {leitura['perdidos_fora']} QR mais antigos ficaram de fora (no máximo {MAX_PERDIDOS} "
                        f"por vez).")
        if det:
            avisos.append(_aviso('a', 'Não deu pra conferir no Asaas/Efí agora', det, 'Tentar de novo', de_novo=True,
                                 ordem=50))
    # 🟠 Assinatura no Asaas que o sistema não conhece
    for bid, xs in sorted(desconhecidas.items()):
        x = xs[0]
        avisos.append(_aviso('a', 'Existe assinatura no Asaas que o sistema não conhece: o pagamento dela não liga nem '
                                  'desliga o bar sozinho',
                             f"{nome.get(bid, 'bar')} · {brl(x['valor'])} por mês"
                             + (f" · próxima fatura {_ddmm(x['proximo'])}" if x.get('proximo') else '')
                             + (f" · e mais {len(xs) - 1}" if len(xs) > 1 else '') + '.',
                             'Gerenciar', f'/pubshow/admin/bar/{bid}', ordem=55))
    for x in sem_dono:
        avisos.append(_aviso('a', 'Existe assinatura no Asaas que o sistema não conhece: o pagamento dela não liga nem '
                                  'desliga o bar sozinho',
                             f"Feita à mão no cliente do Asaas que {' e '.join(nome[i] for i in x['contas'])} dividem: "
                             f"não dá pra saber de qual conta é · {brl(x['valor'])} por mês"
                             + (f" · próxima fatura {_ddmm(x['proximo'])}" if x.get('proximo') else '')
                             + '. Nenhuma conta fica verde por ela.',
                             'Ver as contas', f"#conta-{x['contas'][0]}", ordem=56))
    # 🟠 Mensalidade atrasada (o cartão fica vermelho: o sinal não pode ficar verde com ele)
    for b in reais:
        m = mens[b['id']]
        if m['estado'] == 'atrasada':
            avisos.append(_aviso('a', f"Mensalidade atrasada: {nome[b['id']]}",
                                 m['texto'][0].upper() + m['texto'][1:] + '.', 'Gerenciar',
                                 f"/pubshow/admin/bar/{b['id']}", ordem=58))
    # 🟠 A conta que vende não é a que paga (mesmo telefone)
    cobertos = set()
    for f, ids in sorted(mesmo_fone.items()):
        for bid in ids:
            s = por[bid]
            vende = s['c_n'] + s['x_n']
            # o plano (T4): a conta que vende SEM assinatura PAGA (assinatura criada e não paga também avisa)
            if not vende or mens[bid]['estado'] in ('paga', 'split'):
                continue
            pagantes = [o for o in ids if o != bid and _tem_assinatura(o)]
            if not pagantes:
                continue
            o = pagantes[0]
            vo = por[o]['c_n'] + por[o]['x_n']
            onde_o = ('A assinatura está na conta' if not _tem_assinatura(bid) else 'A outra assinatura está na conta')
            det = (f"{onde_o} {nome[o]}, " + ('que não vendeu nada no mês' if not vo else f"que vendeu {vo} PIX no mês")
                   + '. Pagar numa não cobre a outra.')
            if _tem_assinatura(bid):
                titulo = f"A conta que vende ({nome[bid]}) ainda não tem mensalidade paga"
                det = f"A dela: {mens[bid]['texto']}. " + det
            else:
                titulo = f"A conta que vende ({nome[bid]}) não tem mensalidade"
            avisos.append(_aviso('a', titulo, det, 'Ver as contas', f'#conta-{bid}', ordem=60))
            cobertos.add(bid)
            cobertos.add(f)
    # 🟠 Bar aberto sem cobrança
    for b in reais:
        m = mens[b['id']]
        if m['estado'] == 'aberto' and b['id'] not in cobertos:
            det = (f"O teste acabou em {m['teste_fim']} e não há assinatura." if m.get('teste_fim')
                   else 'Liberado à mão, sem assinatura.')
            avisos.append(_aviso('a', f"{nome[b['id']]} está aberto sem cobrança há {m['dias']} "
                                      f"{_plural(m['dias'], 'dia', 'dias')}", det, 'Gerenciar',
                                 f"/pubshow/admin/bar/{b['id']}", ordem=65))
    # 🟠 PIX contado como pago sem confirmação do banco (ainda sem repasse)
    s_n = sum(por[b['id']]['s_aberto_n'] for b in reais)
    s_val = sum(por[b['id']]['s_aberto_val'] for b in reais)
    if s_n:
        avisos.append(_aviso('a', f"{s_n} PIX {_plural(s_n, 'contado', 'contados')} como "
                                  f"{_plural(s_n, 'pago', 'pagos')} sem confirmação do banco · {brl(_r(s_val))}",
                             'Liberados à mão ou de antes da conferência. Confira antes de repassar.',
                             'Conferir agora', '/pubshow/admin/repasses', ordem=70))
    # 🟠 Contas com o mesmo telefone (a não ser que o aviso de "conta que vende" já fale delas)
    for f, ids in sorted(mesmo_fone.items()):
        if f in cobertos:
            continue
        avisos.append(_aviso('a', f"{len(ids)} contas com o mesmo telefone: " + ', '.join(nome[i] for i in ids),
                             'Aqui elas ficam separadas: pagar numa não cobre a outra.', 'Ver as contas',
                             f'#conta-{ids[0]}', ordem=75))

    avisos.sort(key=lambda a: (0 if a['nivel'] == 'r' else 1, a['ordem']))
    for a in avisos:   # SÓ 1 botão principal na página: o do aviso mais grave que tem botão
        if a['botao']:
            a['pri'] = True
            break
    nr = sum(1 for a in avisos if a['nivel'] == 'r')
    na = len(avisos) - nr
    mais = avisos[MAX_AVISOS_VISIVEIS:]
    mais_r = sum(1 for a in mais if a['nivel'] == 'r')
    mais_a = len(mais) - mais_r
    # o "+N" usa as mesmas palavras do sinal: "urgentes" (🔴) e "avisos" (🟠)
    mais_txt = ' e '.join(([f"{mais_r} {_plural(mais_r, 'urgente', 'urgentes')}"] if mais_r else [])
                          + ([f"{mais_a} {_plural(mais_a, 'aviso', 'avisos')}"] if mais_a else []))

    # ── sinal ─────────────────────────────────────────────────────────────────────────────────────────────────
    hora_lida = (_parse(leitura['lido_em']) + _BRT).strftime('%H:%M') if leitura and leitura.get('lido_em') else ''
    if nr:
        sinal = {'cor': 'r', 'icone': '🔴', 'linhas': [f"{nr} {_plural(nr, 'urgente', 'urgentes')}"]
                 + ([f"{na} {_plural(na, 'aviso', 'avisos')}"] if na else [])}
    elif na:
        sinal = {'cor': 'a', 'icone': '🟠', 'linhas': [f"{na} {_plural(na, 'aviso', 'avisos')}"]}
    elif leitura is None:
        sinal = {'cor': 'w', 'icone': '⏳', 'linhas': ['conferindo no Asaas/Efí…']}
    elif leitura.get('completo'):
        sinal = {'cor': 'g', 'icone': '🟢', 'linhas': ['tudo certo', f'conferido às {hora_lida}']}
    else:   # (não acontece: leitura incompleta sempre vira aviso) — nunca verde
        sinal = {'cor': 'a', 'icone': '🟠', 'linhas': ['não deu pra conferir agora']}

    # ── topo ──────────────────────────────────────────────────────────────────────────────────────────────────
    noite_cent = sum(por[b['id']]['noite_cob'] for b in reais)
    noite_n = sum(por[b['id']]['noite_n'] for b in reais)
    deve_teste = round(sum(por[i]['devido'] for i in teste_ids if i in por), 2)
    deve = round(float(rep['total_devido']) - deve_teste, 2)
    deve_asaas = round(float(rep['total_asaas']) - sum(por[i]['devido_asaas'] for i in teste_ids if i in por), 2)
    deve_efi = round(float(rep['total_efi']) - sum(por[i]['devido_efi'] for i in teste_ids if i in por), 2)

    # ── a devolver (dinheiro de cliente parado na conta do PubShow) ───────────────────────────────────────────
    dev_asaas = sum(x['cent'] for x in a_dev if x['onde'] == 'asaas')
    dev_efi = sum(x['cent'] for x in a_dev if x['onde'] == 'efi')
    for p in perdidos:
        if p['pedido'] == 'recusado':
            if p['onde'] == 'Efí':
                dev_efi += p['cent']
            else:
                dev_asaas += p['cent']

    # ── linhas cinza da leitura ───────────────────────────────────────────────────────────────────────────────
    do_pubshow = f"Do PubShow ali: {brl(deve_asaas)} devido aos bares + {brl(_r(dev_asaas))} a devolver."
    efi_txt = (f"Na Efí (conta da RifaJá): {brl(deve_efi)} devido aos bares"
               + (f" + {brl(_r(dev_efi))} a devolver" if dev_efi else '') + '.')
    if ja_devolvido:
        jn = len(ja_devolvido)
        efi_txt += (f" Já {_plural(jn, 'devolvido', 'devolvidos')} na Efí, falta registrar no sistema: {jn} PIX · "
                    f"{brl(_r(sum(x['cent'] for x in ja_devolvido)))} ({_lista_ids([x['id'] for x in ja_devolvido])}),"
                    f" fora do \"a devolver\".")
    if leitura is None:
        perdido_txt = {'icone': '⏳', 'texto': f'PIX perdido: conferindo no Asaas/Efí os QR dos últimos {DIAS_PERDIDOS} '
                                               f'dias sem pagamento…'}
        saldo_txt = 'Saldo do Asaas: conferindo… · é a conta de todos os SaaS. ' + do_pubshow
    elif not lido:
        perdido_txt = {'icone': '🟠', 'texto': 'PIX perdido: não conferido (a chave do Asaas não está configurada).'}
        saldo_txt = 'Saldo do Asaas: não lido · é a conta de todos os SaaS. ' + do_pubshow
    elif leitura.get('motivo') == 'erro':
        perdido_txt = {'icone': '🟠', 'texto': f'PIX perdido: não deu pra conferir agora (às {hora_lida}).'}
        saldo_txt = 'Saldo do Asaas: não deu pra ler agora · é a conta de todos os SaaS. ' + do_pubshow
    else:
        alvo, conf = leitura['perdidos_alvo'], leitura['perdidos_conferidos']
        if conf < alvo:
            perdido_txt = {'icone': '🟠', 'texto': f'PIX perdido: conferi {conf} de {alvo} QR dos últimos '
                                                   f'{DIAS_PERDIDOS} dias; o resto não respondeu a tempo.'}
        elif perdidos:
            perdido_txt = {'icone': '🔴', 'texto': f"PIX perdido: {len(perdidos)} de {alvo} QR dos últimos {DIAS_PERDIDOS} "
                                                   f"dias estavam pagos (avisos acima)."}
        elif alvo:
            perdido_txt = {'icone': '✓', 'texto': f"PIX perdido: perguntei ao Asaas/Efí sobre "
                                                  f"{_plural(alvo, 'o QR', f'os {alvo} QR')} dos últimos {DIAS_PERDIDOS} "
                                                  f"dias sem pagamento: {_plural(alvo, 'não foi pago', 'nenhum foi pago')}."}
        else:
            perdido_txt = {'icone': '✓', 'texto': f'PIX perdido: nenhum QR sem pagamento nos últimos {DIAS_PERDIDOS} dias.'}
        if leitura.get('perdidos_fora'):
            perdido_txt['texto'] += f" Mais {leitura['perdidos_fora']} QR mais antigos ficaram de fora."
        # sem QR pra perguntar, nada foi lido no Asaas/Efí: não diz "lido às"
        perdido_txt['texto'] += f' (lido às {hora_lida})' if alvo else ' (nada a perguntar)'
        saldo_txt = (f"Saldo do Asaas: {brl(leitura['saldo'])}" if leitura.get('saldo_ok')
                     else 'Saldo do Asaas: não deu pra ler agora') + ' · é a conta de todos os SaaS. ' + do_pubshow
    linhas = {'perdido': perdido_txt, 'saldo': saldo_txt, 'efi': efi_txt}
    linhas_falha = {'perdido': {'icone': '🟠', 'texto': 'PIX perdido: não deu pra conferir agora (a página não '
                                                        'conseguiu perguntar ao Asaas/Efí).'},
                    'saldo': 'Saldo do Asaas: não deu pra ler agora · é a conta de todos os SaaS. ' + do_pubshow,
                    'efi': efi_txt}

    # ── o mês ─────────────────────────────────────────────────────────────────────────────────────────────────
    def _soma(k):
        return sum(por[b['id']][k] for b in reais)
    cli_cent = _soma('c_cob') + _soma('x_cob')
    mens_pagas, _pags_vistos = [], set()
    for bid, m in mens.items():   # cada pagamento 1 vez só (o mesmo pagamento nunca conta em 2 contas)
        if bid not in teste_ids and m['estado'] == 'paga' and m.get('pag_id') not in _pags_vistos:
            _pags_vistos.add(m.get('pag_id'))
            mens_pagas.append(m)
    dev_n = _soma('dev_mes_n')
    mes = {'nome': _MESES[ab.month - 1].capitalize(),
           'n_pix': _soma('c_n') + _soma('x_n'), 'clientes': _r(cli_cent),
           'bares': _r(_soma('c_val') + _soma('x_bar')), 'margem': _r(_soma('c_marg') + _soma('x_marg')),
           's_n': _soma('s_n'), 's_val': _r(_soma('s_val')), 'm_n': _soma('m_n'), 'm_val': _r(_soma('m_val')),
           'm0_n': _soma('m0_n'), 'dev_n': dev_n, 'dev_custo': round(dev_n * T['asaas'], 2),
           'x_n': _soma('x_n'), 'mens_n': len(mens_pagas), 'mens_val': round(sum(m['valor'] for m in mens_pagas), 2),
           'mens_lidas': bool(lido and leitura),
           'mens_todas': bool(lido and leitura and leitura.get('motivo') != 'erro'
                              and all(v.get('ok') for v in (leitura.get('assin') or {}).values()))}

    # ── cartões ───────────────────────────────────────────────────────────────────────────────────────────────
    def _tv(b):
        v = _parse(b.get('tv_visto_em'))
        if not v:
            return {'texto': '—', 'cor': 'cinza'}
        if agora - v < timedelta(minutes=TV_LIGADA_MIN):
            if b.get('tv_som') == 0:
                return {'texto': '● ligada, sem som', 'cor': 'amar'}
            return {'texto': '● ligada agora', 'cor': 'verde'}
        return {'texto': 'última vez ' + _quando(b['tv_visto_em'], agora), 'cor': 'cinza'}

    def _cartao(b):
        s = por[b['id']]
        n = s['c_n'] + s['x_n']
        mes_txt = f"{n} PIX {_plural(n, 'confirmado', 'confirmados')} · {brl(_r(s['c_cob'] + s['x_cob']))}"
        if s['s_n']:
            mes_txt += f" · {s['s_n']} sem confirmação"
        if s['m_n']:
            mes_txt += f" · {s['m_n']} na chave do bar"
        f = _fone(b.get('telefone'))
        return {'id': b['id'], 'nome': nome[b['id']], 'code': b.get('code') or '', 'rota': _ROTA_TXT[rotas[b['id']]],
                'mesmo_fone': b['id'] in fone_de, 'mens': mens[b['id']], 'tv': _tv(b),
                'ult_pix': _quando(s['ult_pix'], agora) if s['ult_pix'] else '—', 'mes_txt': mes_txt,
                'devido': s['devido'], 'devido_txt': brl(s['devido']),
                'whatsapp': f'https://wa.me/55{f}' if f else '', 'ordem': (-(s['c_cob'] + s['x_cob']), -s['devido'],
                                                                          (b.get('nome') or '').lower())}
    contas = sorted((_cartao(b) for b in reais), key=lambda c: c['ordem'])
    teste = [_cartao(bares[i]) for i in sorted(teste_ids)]

    dia = f"{_DIAS_SEMANA[ab.weekday()]} {ab.strftime('%d/%m')} · {ab.strftime('%H:%M')}"
    noite_desde = 'desde 12h de ontem' if L['noite_ontem'] else 'desde 12h'
    return {
        'agora_txt': dia, 'agora_utc': _txt(agora),
        'teste_nomes': [nome[i] for i in sorted(teste_ids)],
        'noite': {'total': _r(noite_cent), 'txt': brl(_r(noite_cent)), 'n': noite_n, 'desde': noite_desde},
        'deve': {'total': deve, 'txt': brl(deve), 'teste': deve_teste, 'teste_txt': brl(deve_teste),
                 'asaas': deve_asaas, 'efi': deve_efi, 'repasses_txt': brl(rep['total_devido'])},
        'a_devolver': {'asaas': _r(dev_asaas), 'efi': _r(dev_efi)},
        'ja_devolvido_efi': [x['id'] for x in ja_devolvido],
        'sinal': sinal, 'avisos': avisos, 'avisos_vis': avisos[:MAX_AVISOS_VISIVEIS],
        'avisos_mais': mais, 'mais_txt': mais_txt, 'mais_r': mais_r, 'n_vermelhos': nr, 'n_laranjas': na,
        'linhas': linhas, 'linhas_falha': linhas_falha, 'lido': bool(leitura), 'completo': bool(leitura and leitura.get('completo')),
        'mes': mes, 'contas': contas, 'teste': teste, 'perdidos': perdidos,
        'tarifas': {'asaas': T['asaas'], 'asaas_txt': brl(T['asaas']),
                    'efi_txt': f"{T['efi_pct']:.2f}".replace('.', ',').rstrip('0').rstrip(',') + '%'},
        'brl': brl, 'dias_perdidos': DIAS_PERDIDOS, 'max_perdidos': MAX_PERDIDOS,
        'donates': donates_sistema(conn, agora),   # 27/10/26 (D6): a lista do Diogo
    }


# ══ As 2 rotas (o app.py só chama estas) ═════════════════════════════════════════════════════════════════════
_FRAG = re.compile(r'<!--F:([\w-]+)-->(.*?)<!--/F:\1-->', re.S)


def _sem_cache(resp):
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def pagina():
    """GET /saas-admin/pubshow: a página abre só com o banco (o sinal fica ⏳, nunca verde) e o JS busca a leitura."""
    from flask import make_response, render_template
    agora = agora_utc()
    conn = _conn()
    try:
        d = montar(conn, agora, None)
    finally:
        conn.close()
    return _sem_cache(make_response(render_template(TEMPLATE, d=d)))


def leitura_json(de_novo=False):
    """GET /saas-admin/pubshow/gateway: {'pronto': False} enquanto lê; pronto → os pedaços da página já com a
    leitura (o mesmo template, então a página e a atualização nunca discordam). Nunca leva chave nem cabeçalho."""
    from flask import jsonify, render_template
    agora = agora_utc()
    leitura, info = leitura_atual(agora, de_novo)
    if leitura is None:
        return _sem_cache(jsonify({'ok': True, 'pronto': False}))
    conn = _conn()
    try:
        d = montar(conn, agora, leitura)
    finally:
        conn.close()
    html = render_template(TEMPLATE, d=d)
    return _sem_cache(jsonify({
        'ok': True, 'pronto': True, 'sinal': d['sinal']['cor'], 'completo': bool(leitura.get('completo')),
        'configurado': bool(leitura.get('configurado')) and leitura.get('motivo') != 'sem_chave',
        'conferidas': leitura.get('feitas', 0), 'previstas': leitura.get('previstas', 0),
        'perdidos': [p['id'] for p in d['perdidos']], 'saldo': leitura.get('saldo'),
        'cache': bool(info.get('cache')), 'de_novo_cedo': bool(info.get('de_novo_cedo')),
        'html': {m.group(1): m.group(2) for m in _FRAG.finditer(html)},
    }))
