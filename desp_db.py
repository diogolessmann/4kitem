"""
desp_db.py — Banco de dados do Despachante Lessmann (integrado ao 4kitem)
SQLite WAL · clientes · veiculos · ordens_servico · documentos
"""
import sqlite3, os
from datetime import datetime, date
from collections import OrderedDict

_base   = os.environ.get("DATA_DIR", os.path.dirname(__file__))
DB_PATH = os.path.join(_base, "desp.db")

# ── Serviços agrupados (estrutura hierárquica) ───────────────────────────────
SERVICOS_GRUPOS = OrderedDict([

    ("licenciamento", {
        "label": "Licenciamento",
        "icon": "📋",
        "items": OrderedDict([
            ("licenciamento",          "Licenciamento Anual / CRLV"),
            ("lic_debitos",            "Com Débitos ou Parcelado"),
            ("lic_outro_municipio",    "Outro Município"),
            ("lic_outro_estado",       "Outro Estado"),
            ("lic_emissao",            "Emissão"),
        ])
    }),

    ("transferencia", {
        "label": "Transferência",
        "icon": "🔄",
        "items": OrderedDict([
            ("transferencia",              "Transferência Padrão (SC)"),
            ("transferencia_debito",       "Transferência com Débitos"),
            ("transferencia_gravame",      "Transferência com Gravame / Alienação"),
            ("transferencia_leilao",       "Veículo de Leilão Público"),
            ("transferencia_outro_estado", "Transferência Outro Estado"),
            ("atpv_comunicado",            "Comunicado de Venda (ATPV-e)"),
        ])
    }),

    ("especial", {
        "label": "Especial",
        "icon": "⭐",
        "items": OrderedDict([
            ("inventario",             "Inventário / Herança"),
            ("recibo_inventario",      "Número de Recibo — Inventário"),
            ("baixa_administrativa",   "Baixa Administrativa"),
            ("baixa_circulacao",       "Baixa de Circulação (Sucata)"),
            ("segunda_via_crv",        "2ª Via do CRV"),
            ("comunicado_retroativo",  "Comunicado Retroativo — Limpa CNH"),
        ])
    }),

    ("boletos", {
        "label": "Boletos",
        "icon": "🧾",
        "items": OrderedDict([
            ("boleto_multa",           "Boleto de Multa"),
            ("boleto_licenciamento",   "Boleto de Licenciamento"),
            ("boleto_ipva",            "Boleto de IPVA"),
            ("boleto_divida_ativa",    "Boleto / Dívida Ativa"),
            ("boletos",                "Boletos (Outros)"),
        ])
    }),

    ("alteracao", {
        "label": "Alteração de Características",
        "icon": "🔧",
        "items": OrderedDict([
            ("alt_cor",            "Mudança de Cor"),
            ("alt_motor",          "Substituição de Motor"),
            ("alt_combustivel",    "Mudança de Combustível (GNV / Flex)"),
            ("alt_carroceria",     "Mudança de Carroceria"),
            ("alt_visual",         "Alteração Visual (kit, acessórios)"),
            ("alt_suspensao",      "Rebaixamento / Suspensão"),
            ("alt_iluminacao",     "Alteração de Iluminação"),
            ("alt_capacidade",     "Capacidade de Carga"),
            ("alt_passageiros",    "Nº de Passageiros"),
            ("alt_chassi_along",   "Alongamento de Chassi"),
            ("alt_categoria",      "Mudança de Categoria (Particular → Aluguel)"),
            ("mudanca_endereco",   "Mudança de Endereço / Domicílio"),
            ("alt_dados",          "Atualização de Dados (refinanciamento)"),
        ])
    }),

    ("registro", {
        "label": "Registro e Emplacamento",
        "icon": "🆕",
        "items": OrderedDict([
            ("primeiro_emplacamento", "Primeiro Emplacamento"),
            ("registro_inicial",      "Registro Inicial (Importado)"),
            ("placa_mercosul",        "Par de Placas Mercosul"),
            ("remarcacao_chassi",     "Remarcação de Chassi"),
            ("remarcacao_motor",      "Remarcação de Motor"),
            ("conversao_placa_piv",   "Conversão Placa PIV"),
            ("veiculo_colecao",       "Veículo de Coleção"),
            ("veiculo_artesanal",     "Veículo Artesanal"),
        ])
    }),

    ("consultas", {
        "label": "Consultas e Certidões",
        "icon": "🔍",
        "items": OrderedDict([
            ("certidao",           "Certidão Negativa DETRAN"),
            ("consulta_debitos",   "Consulta Débitos (Placa + CPF)"),
            ("historico_leilao",   "Histórico Leilão / Sinistro / Fraude"),
            ("consulta_gravame",   "Consulta RENAJUD / Gravames / Restrições"),
            ("historico_donos",    "Histórico de Proprietários"),
        ])
    }),

    ("cnh", {
        "label": "CNH e Condutor",
        "icon": "🪪",
        "items": OrderedDict([
            ("indicacao_condutor",  "Indicação de Condutor Infrator"),
            ("renovacao_cnh_ab",    "Renovação CNH — Categorias A/B"),
            ("renovacao_cnh_cde",   "Renovação CNH — Categorias C/D/E"),
            ("segunda_via_cnh",     "2ª Via CNH"),
            ("reciclagem_cnh",      "Reciclagem CNH (suspensão)"),
        ])
    }),

    ("antt", {
        "label": "ANTT / Transporte Profissional",
        "icon": "🚛",
        "items": OrderedDict([
            ("antt_registro",    "ANTT — Registro RNTRC"),
            ("antt_inclusao",    "ANTT — Inclusão de Veículo"),
            ("antt_renovacao",   "ANTT — Renovação"),
            ("aet_excesso",      "AET — Autorização Excesso de Tonelagem"),
            ("cap_mopp",         "Capacitação MOPP"),
            ("cap_motofrete",    "Capacitação Motofrete"),
            ("cap_escolar",      "Capacitação Transporte Escolar"),
            ("cap_passageiros",  "Capacitação Transporte de Passageiros"),
            ("cap_ambulancia",   "Capacitação Transporte de Ambulância"),
        ])
    }),

    ("documentos", {
        "label": "Documentos e Contratos",
        "icon": "📄",
        "items": OrderedDict([
            ("procuracao",            "Procuração Veicular"),
            ("pedido_etiquetas",      "Pedido de Etiquetas"),
            ("contrato_compra_venda", "Contrato de Compra e Venda"),
            ("contrato_aluguel",      "Contrato de Aluguel"),
            ("contrato_universal",    "Contrato Universal"),
            ("comodato",              "Contrato de Comodato"),
            ("distrato_comodato",     "Distrato de Comodato"),
            ("declaracao_residencia", "Declaração de Residência"),
            ("declaracao_odometro",   "Declaração de Odômetro"),
            ("assinatura_digital",    "Assinatura / Autenticação Digital"),
            ("autorizacao_viagem",    "Autorização de Viagem (Menor)"),
        ])
    }),

    ("outros", {
        "label": "Outros Serviços",
        "icon": "⚙️",
        "items": OrderedDict([
            ("pcd_ipva",          "PCD — Isenção IPVA"),
            ("pcd_0km",           "PCD — Veículo 0km"),
            ("vaga_especial",     "Vaga Especial PCD / Idoso / Gestante"),
            ("seguro_carta_verde","Seguro Carta Verde"),
            ("abertura_mei",      "Abertura de MEI"),
            ("protecao_veicular", "Proteção Veicular"),
            ("outros",            "Outros Serviços"),
        ])
    }),
])

# Flat dict — compatibilidade com templates existentes e banco de dados
SERVICOS = {
    k: v
    for grupo in SERVICOS_GRUPOS.values()
    for k, v in grupo["items"].items()
}

# ── Documentos necessários por tipo de serviço ──────────────────────────────
DOCS_POR_SERVICO = {
    # Licenciamento
    "licenciamento":          ["CRLV Anterior", "CNH / RG do Proprietário", "Comprovante de Endereço", "Boleto DETRAN Pago"],
    "lic_debitos":            ["CRLV Anterior", "CNH / RG do Proprietário", "Comprovante de Endereço", "Comprovante de Parcelamento / Quitação"],
    "lic_outro_municipio":    ["CRLV Anterior", "CNH / RG do Proprietário", "Comprovante Novo Endereço", "Boleto DETRAN Pago"],
    "lic_outro_estado":       ["CRV Original", "CNH / RG do Proprietário", "Comprovante de Endereço SC", "Laudo de Vistoria"],
    "lic_emissao":            ["CRLV Anterior", "CNH / RG do Proprietário", "Comprovante de Endereço"],

    # Transferência
    "transferencia":          ["CRV Original Assinado (AT)", "CNH Vendedor", "CNH / RG Comprador", "Comprovante Endereço Comprador", "Laudo de Vistoria (se exigido)"],
    "transferencia_debito":   ["CRV Original Assinado (AT)", "CNH Vendedor", "CNH / RG Comprador", "Comprovante Quitação / Parcelamento Débitos", "Comprovante Endereço Comprador"],
    "transferencia_gravame":  ["CRV Original Assinado (AT)", "CNH Vendedor", "CNH / RG Comprador", "Autorização da Financeira / Banco", "Comprovante Endereço Comprador"],
    "transferencia_leilao":   ["Nota Fiscal do Leilão", "Auto / Edital do Leilão", "CNH Arrematante", "Comprovante Endereço Arrematante", "Laudo de Vistoria"],
    "transferencia_outro_estado": ["CRV Original Assinado", "CNH Vendedor", "CNH / RG Comprador", "Laudo de Vistoria DETRAN-SC", "Comprovante Endereço SC"],
    "atpv_comunicado":        ["ATPV-e ou CRV Original", "CNH Vendedor", "Dados do Comprador (nome, CPF)"],

    # Especial
    "inventario":             ["Formal de Partilha ou Alvará Judicial", "Certidão de Óbito", "CRV Original", "CNH / RG Herdeiro", "CPF Herdeiro"],
    "recibo_inventario":      ["CRV Original", "Formal de Partilha", "CNH / RG Herdeiro"],
    "baixa_administrativa":   ["CRV Original", "CNH / RG Proprietário", "Requerimento DETRAN Assinado"],
    "baixa_circulacao":       ["CRV Original", "CNH / RG Proprietário", "Laudo de Vistoria / Sucata", "DUT (se aplicável)"],
    "segunda_via_crv":        ["Boletim de Ocorrência (BO)", "CNH / RG Proprietário", "CPF Proprietário", "Comprovante de Endereço", "Taxa DETRAN Paga"],
    "comunicado_retroativo":  ["CRLV ou CRV do Período", "CNH Vendedor", "Contrato de Compra e Venda", "Dados Completos do Comprador"],

    # Boletos
    "boleto_multa":           ["CNH / RG Proprietário", "CPF Proprietário", "Dados da Multa (AIT)"],
    "boleto_licenciamento":   ["Placa / RENAVAM", "CPF Proprietário"],
    "boleto_ipva":            ["Placa / RENAVAM", "CPF Proprietário"],
    "boleto_divida_ativa":    ["CPF / CNPJ Proprietário", "Placa / RENAVAM", "Número da Dívida (se houver)"],
    "boletos":                ["CPF / CNPJ Proprietário", "Placa / RENAVAM"],

    # Alterações
    "alt_cor":                ["CRV Original", "CNH Proprietário", "Nota Fiscal Tintura / Serviço", "Laudo Vistoria"],
    "alt_motor":              ["CRV Original", "CNH Proprietário", "Nota Fiscal Motor", "Laudo Vistoria"],
    "alt_combustivel":        ["CRV Original", "CNH Proprietário", "Nota Fiscal Conversão GNV/Flex", "Certificado INMETRO"],
    "alt_gravame_inclusao":   ["CRV Original", "Contrato Financiamento", "CNH Proprietário"],
    "alt_gravame_baixa":      ["CRV Original", "Carta Quitação do Banco", "CNH Proprietário"],
    "mudanca_endereco":       ["CNH / RG Proprietário", "Comprovante Novo Endereço"],

    # Registro
    "primeiro_emplacamento":  ["Nota Fiscal Veículo 0km", "CNH / RG Comprador", "Comprovante de Endereço", "Laudo Vistoria"],
    "registro_inicial":       ["NF Importação / DI", "CNH / RG Comprador", "Comprovante Endereço", "Laudo Vistoria"],
    "placa_mercosul":         ["CRV Original", "CNH / RG Proprietário", "Taxa DETRAN Paga"],

    # CNH
    "renovacao_cnh_ab":       ["CNH Atual", "Comprovante de Endereço", "Exame Médico / Psicológico", "Taxa DETRAN Paga"],
    "renovacao_cnh_cde":      ["CNH Atual", "Comprovante de Endereço", "Exame Médico / Psicológico", "Exame Toxicológico", "Taxa DETRAN Paga"],
    "segunda_via_cnh":        ["Boletim de Ocorrência (BO)", "Comprovante de Endereço", "Taxa DETRAN Paga"],
    "indicacao_condutor":     ["CNH Proprietário", "CPF Proprietário", "Dados do Condutor Infrator", "Auto de Infração (AIT)"],

    # Documentos
    "procuracao":             ["RG / CPF Outorgante", "Comprovante de Endereço", "Dados do Veículo (Placa, RENAVAM)"],
    "contrato_compra_venda":  ["RG / CPF Vendedor", "RG / CPF Comprador", "Dados Completos do Veículo"],
    "contrato_aluguel":       ["RG / CPF Locador", "RG / CPF Locatário", "Comprovante de Endereço"],
    "contrato_universal":     ["RG / CPF das Partes", "Comprovante de Endereço"],
    "autorizacao_viagem":     ["RG / CPF do Responsável", "RG / Certidão do Menor", "Dados do Destino / Período"],
}

# Padrão quando o serviço não tem mapeamento específico
DOCS_PADRAO = ["CRV / CRLV Original", "CNH / RG do Proprietário", "CPF do Proprietário", "Comprovante de Endereço"]

# Final de placa → mês de licenciamento (SC)
FINAIS_PLACA = {
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "6": 6, "7": 7, "8": 8, "9": 9, "0": 10,
}
MESES = ["", "Janeiro","Fevereiro","Março","Abril","Maio","Junho",
         "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]

STATUS_LABELS = {
    "aberta":    ("🟡", "Aguardando Doc."),
    "andamento": ("🔵", "Em Andamento"),
    "detran":    ("🏛️", "No DETRAN"),
    "concluida": ("🟢", "Concluída"),
    "cancelada": ("🔴", "Cancelada"),
}

# Colunas visíveis no Kanban (sem cancelada)
KANBAN_COLUNAS = [
    ("aberta",    "🟡", "Aguardando Doc.",  "rgba(234,179,8,.12)",   "var(--yellow)"),
    ("andamento", "🔵", "Em Andamento",     "rgba(59,130,246,.12)",  "var(--blue)"),
    ("detran",    "🏛️", "No DETRAN",        "rgba(168,85,247,.12)",  "var(--purple)"),
    ("concluida", "🟢", "Concluída",        "rgba(34,197,94,.12)",   "var(--green)"),
]

# ── Conexão ─────────────────────────────────────────────────────────────────
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# ── Init ─────────────────────────────────────────────────────────────────────
def init_desp_db():
    """Alias para compatibilidade com app.py"""
    return init_db()

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS clientes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo        TEXT    DEFAULT 'PF',
            nome        TEXT    NOT NULL,
            cpf         TEXT,
            cnpj        TEXT,
            rg          TEXT,
            nascimento  TEXT,
            nome_mae    TEXT,
            telefone    TEXT,
            email       TEXT,
            cep         TEXT,
            logradouro  TEXT,
            numero      TEXT,
            complemento TEXT,
            bairro      TEXT,
            cidade      TEXT,
            uf          TEXT    DEFAULT 'SC',
            criado_em   TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS veiculos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            placa           TEXT    UNIQUE NOT NULL,
            renavam         TEXT,
            chassi          TEXT,
            marca           TEXT,
            modelo          TEXT,
            ano_fab         INTEGER,
            ano_mod         INTEGER,
            cor             TEXT,
            especie         TEXT    DEFAULT 'Automóvel',
            tipo_veiculo    TEXT,
            categoria       TEXT    DEFAULT 'Particular',
            combustivel     TEXT,
            num_crv         TEXT,
            proprietario_id INTEGER REFERENCES clientes(id),
            criado_em       TEXT    DEFAULT CURRENT_TIMESTAMP,
            atualizado_em   TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ordens_servico (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            numero          TEXT    UNIQUE NOT NULL,
            cliente_id      INTEGER REFERENCES clientes(id),
            veiculo_id      INTEGER REFERENCES veiculos(id),
            servico         TEXT    NOT NULL,
            status          TEXT    DEFAULT 'aberta',
            honorarios      REAL    DEFAULT 0,
            custos          REAL    DEFAULT 0,
            total           REAL    DEFAULT 0,
            pago            REAL    DEFAULT 0,
            forma_pagamento TEXT,
            observacoes     TEXT,
            criado_em       TEXT    DEFAULT CURRENT_TIMESTAMP,
            atualizado_em   TEXT    DEFAULT CURRENT_TIMESTAMP,
            concluido_em    TEXT
        );

        CREATE TABLE IF NOT EXISTS documentos (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            os_id     INTEGER REFERENCES ordens_servico(id) ON DELETE CASCADE,
            tipo      TEXT    NOT NULL,
            titulo    TEXT,
            campos    TEXT,
            criado_em TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS debitos_veiculo (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            os_id       INTEGER REFERENCES ordens_servico(id) ON DELETE CASCADE,
            veiculo_id  INTEGER REFERENCES veiculos(id),
            tipo        TEXT    NOT NULL,
            descricao   TEXT,
            valor       REAL    DEFAULT 0,
            vencimento  TEXT,
            situacao    TEXT    DEFAULT 'em aberto',
            auto_infracao TEXT,
            criado_em   TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_desp_os_cliente  ON ordens_servico(cliente_id);
        CREATE INDEX IF NOT EXISTS idx_desp_os_veiculo  ON ordens_servico(veiculo_id);
        CREATE INDEX IF NOT EXISTS idx_desp_os_status   ON ordens_servico(status);
        CREATE INDEX IF NOT EXISTS idx_desp_os_criado   ON ordens_servico(criado_em DESC);
        CREATE INDEX IF NOT EXISTS idx_desp_veiculo_placa ON veiculos(placa);
        CREATE INDEX IF NOT EXISTS idx_desp_cliente_cpf   ON clientes(cpf);
        CREATE INDEX IF NOT EXISTS idx_debitos_os        ON debitos_veiculo(os_id);
        CREATE INDEX IF NOT EXISTS idx_debitos_veiculo   ON debitos_veiculo(veiculo_id);
    """)
    conn.commit()

    # ── Migrations suaves (colunas adicionadas depois do CREATE inicial) ────────
    _migrations = [
        "ALTER TABLE ordens_servico ADD COLUMN exercicio INTEGER",
        "ALTER TABLE ordens_servico ADD COLUMN situacao_pag TEXT DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_desp_os_exercicio ON ordens_servico(exercicio)",
        """CREATE TABLE IF NOT EXISTS debitos_veiculo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            os_id INTEGER REFERENCES ordens_servico(id) ON DELETE CASCADE,
            veiculo_id INTEGER REFERENCES veiculos(id),
            tipo TEXT NOT NULL,
            descricao TEXT,
            valor REAL DEFAULT 0,
            vencimento TEXT,
            situacao TEXT DEFAULT 'em aberto',
            auto_infracao TEXT,
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        "ALTER TABLE debitos_veiculo ADD COLUMN numero_detran TEXT",
        "ALTER TABLE debitos_veiculo ADD COLUMN valor_nominal REAL DEFAULT 0",
        "ALTER TABLE debitos_veiculo ADD COLUMN valor_multa REAL DEFAULT 0",
        "ALTER TABLE debitos_veiculo ADD COLUMN valor_juros REAL DEFAULT 0",
        # ── OS: campos extras ──
        "ALTER TABLE ordens_servico ADD COLUMN total_parcelas INTEGER DEFAULT 1",
        # ── Histórico / log de OS ──
        """CREATE TABLE IF NOT EXISTS os_historico (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            os_id      INTEGER NOT NULL REFERENCES ordens_servico(id) ON DELETE CASCADE,
            status     TEXT,
            nota       TEXT,
            usuario    TEXT    DEFAULT 'Diogo',
            criado_em  TEXT    DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_os_hist_os ON os_historico(os_id)",
        # ── Parcelas de pagamento ──
        """CREATE TABLE IF NOT EXISTS os_parcelas (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            os_id          INTEGER NOT NULL REFERENCES ordens_servico(id) ON DELETE CASCADE,
            numero         INTEGER NOT NULL,
            valor          REAL    NOT NULL,
            vencimento     TEXT,
            pago_em        TEXT,
            forma_pagamento TEXT,
            observacao     TEXT,
            criado_em      TEXT    DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_parcelas_os ON os_parcelas(os_id)",
        # ── Config geral (key/value) ──
        """CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT,
            atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        # ── Requerimento customizável ──
        "ALTER TABLE ordens_servico ADD COLUMN corpo_req TEXT",
        # ── Lotes de protocolo RENAVAM ──
        """CREATE TABLE IF NOT EXISTS protocolos_renavam (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            protocolo   TEXT    NOT NULL,
            letra       TEXT,
            lote        TEXT,
            emitido_em  TEXT,
            observacao  TEXT,
            usado       INTEGER DEFAULT 0,
            os_id       INTEGER REFERENCES ordens_servico(id),
            criado_em   TEXT    DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_proto_protocolo ON protocolos_renavam(protocolo)",
        "CREATE INDEX IF NOT EXISTS idx_proto_lote ON protocolos_renavam(lote)",
        # ── Portal do cliente — token público ──
        "ALTER TABLE ordens_servico ADD COLUMN token_publico TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_os_token ON ordens_servico(token_publico)",
        # ── Entrega de documentos ao cliente ──
        "ALTER TABLE ordens_servico ADD COLUMN entregue_em TEXT DEFAULT NULL",
        "ALTER TABLE ordens_servico ADD COLUMN entregue_por TEXT DEFAULT NULL",
        # ── Prazo legal da OS (ex: 30 dias para protocolar transferência) ──
        "ALTER TABLE ordens_servico ADD COLUMN prazo_legal TEXT DEFAULT NULL",
        # ── CNH do cliente ──
        "ALTER TABLE clientes ADD COLUMN cnh TEXT DEFAULT NULL",
        "ALTER TABLE clientes ADD COLUMN categoria_cnh TEXT DEFAULT NULL",
        "ALTER TABLE clientes ADD COLUMN vencimento_cnh TEXT DEFAULT NULL",
        # ── Performance: índice em atualizado_em ──
        "CREATE INDEX IF NOT EXISTS idx_desp_os_atualizado ON ordens_servico(atualizado_em DESC)",
        "CREATE INDEX IF NOT EXISTS idx_desp_os_status ON ordens_servico(status)",
    ]
    for sql in _migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # Já existe — ok

    conn.close()

# ── Número de O.S. ───────────────────────────────────────────────────────────
def _novo_numero_os(conn):
    ano = datetime.now().strftime("%y")
    row = conn.execute(
        "SELECT numero FROM ordens_servico WHERE numero LIKE ? ORDER BY id DESC LIMIT 1",
        (f"{ano}%",)
    ).fetchone()
    if row:
        seq = int(row["numero"][2:]) + 1
    else:
        seq = 1
    return f"{ano}{seq:04d}"

# ── CRUD Clientes ─────────────────────────────────────────────────────────────
def criar_cliente(dados: dict) -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO clientes (tipo,nome,cpf,cnpj,rg,nascimento,nome_mae,
            telefone,email,cep,logradouro,numero,complemento,bairro,cidade,uf)
        VALUES (:tipo,:nome,:cpf,:cnpj,:rg,:nascimento,:nome_mae,
            :telefone,:email,:cep,:logradouro,:numero,:complemento,:bairro,:cidade,:uf)
    """, dados)
    conn.commit()
    id_ = cur.lastrowid
    conn.close()
    return id_

def buscar_cliente_cpf(cpf: str) -> dict | None:
    cpf = cpf.replace(".","").replace("-","")
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM clientes WHERE replace(replace(cpf,'.',''),'-','') = ? LIMIT 1", (cpf,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def buscar_cliente_nome(nome: str) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM clientes WHERE nome LIKE ? LIMIT 10", (f"%{nome}%",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_cliente(id_: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM clientes WHERE id=?", (id_,)).fetchone()
    conn.close()
    return dict(row) if row else None

def atualizar_cliente(id_: int, dados: dict):
    conn = get_conn()
    campos = ", ".join(f"{k}=:{k}" for k in dados if k != "id")
    dados["id"] = id_
    conn.execute(f"UPDATE clientes SET {campos} WHERE id=:id", dados)
    conn.commit()
    conn.close()

# ── CRUD Veículos ─────────────────────────────────────────────────────────────
def criar_veiculo(dados: dict) -> int:
    conn = get_conn()
    dados["placa"] = dados.get("placa","").upper().replace("-","")
    cur = conn.execute("""
        INSERT OR IGNORE INTO veiculos
            (placa,renavam,chassi,marca,modelo,ano_fab,ano_mod,cor,
             especie,tipo_veiculo,categoria,combustivel,num_crv,proprietario_id)
        VALUES (:placa,:renavam,:chassi,:marca,:modelo,:ano_fab,:ano_mod,:cor,
                :especie,:tipo_veiculo,:categoria,:combustivel,:num_crv,:proprietario_id)
    """, dados)
    if cur.rowcount == 0:
        # Placa já existe — atualiza
        campos = [k for k in dados if k not in ("placa","id")]
        sets   = ", ".join(f"{c}=:{c}" for c in campos)
        conn.execute(f"UPDATE veiculos SET {sets}, atualizado_em=CURRENT_TIMESTAMP WHERE placa=:placa", dados)
    conn.commit()
    row = conn.execute("SELECT id FROM veiculos WHERE placa=?", (dados["placa"],)).fetchone()
    conn.close()
    return row["id"]

def buscar_veiculo_placa(placa: str) -> dict | None:
    placa = placa.upper().replace("-","").strip()
    conn  = get_conn()
    row   = conn.execute("""
        SELECT v.*, c.nome as prop_nome, c.cpf as prop_cpf,
               c.telefone as prop_tel, c.cidade as prop_cidade
        FROM veiculos v
        LEFT JOIN clientes c ON c.id = v.proprietario_id
        WHERE v.placa = ?
    """, (placa,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_veiculo(id_: int) -> dict | None:
    conn = get_conn()
    row  = conn.execute("SELECT * FROM veiculos WHERE id=?", (id_,)).fetchone()
    conn.close()
    return dict(row) if row else None

# ── CRUD Ordens de Serviço ────────────────────────────────────────────────────
def criar_os(dados: dict) -> int:
    conn = get_conn()
    dados["numero"] = _novo_numero_os(conn)
    dados.setdefault("status", "aberta")
    dados.setdefault("exercicio", datetime.now().year)
    dados.setdefault("situacao_pag", "")
    dados["total"] = float(dados.get("honorarios",0)) + float(dados.get("custos",0))
    cur = conn.execute("""
        INSERT INTO ordens_servico
            (numero,cliente_id,veiculo_id,servico,status,honorarios,
             custos,total,pago,forma_pagamento,observacoes,exercicio,situacao_pag)
        VALUES (:numero,:cliente_id,:veiculo_id,:servico,:status,:honorarios,
                :custos,:total,:pago,:forma_pagamento,:observacoes,:exercicio,:situacao_pag)
    """, dados)
    conn.commit()
    id_ = cur.lastrowid
    conn.close()
    return id_

def get_os(id_: int) -> dict | None:
    conn = get_conn()
    row  = conn.execute("""
        SELECT
          os.id, os.numero, os.cliente_id, os.veiculo_id, os.servico, os.status,
          os.honorarios, os.custos, os.total, os.pago, os.forma_pagamento,
          os.observacoes, os.corpo_req, os.criado_em, os.atualizado_em, os.concluido_em,
          os.exercicio, os.situacao_pag,
          c.nome       AS cliente_nome,
          c.cpf,       c.cnpj,      c.rg,     c.nascimento,  c.nome_mae,
          c.telefone,  c.email,     c.cep,    c.logradouro,
          c.numero     AS endereco_num,
          c.complemento, c.bairro,  c.cidade, c.uf,
          v.placa,     v.renavam,   v.chassi, v.marca,    v.modelo,
          v.ano_fab,   v.ano_mod,   v.cor,    v.especie,  v.categoria,
          v.combustivel, v.num_crv, v.tipo_veiculo
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE os.id = ?
    """, (id_,)).fetchone()
    conn.close()
    return dict(row) if row else None

def kanban_os() -> dict:
    """
    Retorna todas as OS ativas agrupadas por status para o Kanban.
    Exclui canceladas. Inclui contagem de checklist pendente e valor devido.
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT os.id, os.numero, os.servico, os.status,
               os.honorarios, os.custos,
               (os.honorarios + os.custos)           AS total,
               (os.honorarios + os.custos - os.pago) AS pendente,
               os.criado_em, os.atualizado_em, os.exercicio,
               c.nome AS cliente_nome, c.telefone,
               v.placa, v.marca, v.modelo
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos  v ON v.id  = os.veiculo_id
        WHERE os.status != 'cancelada'
        ORDER BY os.atualizado_em DESC
    """).fetchall()
    conn.close()

    hoje_str = date.today().isoformat()

    ordens = [dict(r) for r in rows]
    colunas = {col[0]: [] for col in KANBAN_COLUNAS}
    for o in ordens:
        # Dias desde última atualização
        ref = (o.get('atualizado_em') or o.get('criado_em') or hoje_str)[:10]
        try:
            delta = date.fromisoformat(hoje_str) - date.fromisoformat(ref)
            o['dias'] = delta.days
        except Exception:
            o['dias'] = 0
        col = o['status'] if o['status'] in colunas else 'aberta'
        colunas[col].append(o)
    return colunas


def listar_os(status=None, busca=None, limit=50, offset=0) -> list:
    conn   = get_conn()
    where  = []
    params = []
    if status:
        where.append("os.status = ?")
        params.append(status)
    if busca:
        where.append("(c.nome LIKE ? OR v.placa LIKE ? OR os.numero LIKE ?)")
        b = f"%{busca}%"
        params += [b, b, b]
    wclause = ("WHERE " + " AND ".join(where)) if where else ""
    params += [limit, offset]
    rows = conn.execute(f"""
        SELECT os.id, os.numero, os.servico, os.status, os.honorarios,
               os.custos, os.total, os.pago, os.criado_em, os.concluido_em,
               c.nome as cliente_nome, c.cpf, c.telefone,
               v.placa, v.marca, v.modelo
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        {wclause}
        ORDER BY os.id DESC
        LIMIT ? OFFSET ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def atualizar_os_status(id_: int, status: str, pago: float = None):
    conn = get_conn()
    if pago is not None:
        conn.execute(
            "UPDATE ordens_servico SET status=?, pago=?, atualizado_em=CURRENT_TIMESTAMP,"
            " concluido_em=CASE WHEN ?='concluida' THEN CURRENT_TIMESTAMP ELSE concluido_em END"
            " WHERE id=?", (status, pago, status, id_)
        )
    else:
        conn.execute(
            "UPDATE ordens_servico SET status=?, atualizado_em=CURRENT_TIMESTAMP,"
            " concluido_em=CASE WHEN ?='concluida' THEN CURRENT_TIMESTAMP ELSE concluido_em END"
            " WHERE id=?", (status, status, id_)
        )
    conn.commit()
    conn.close()

def atualizar_os(id_: int, dados: dict):
    dados["total"] = float(dados.get("honorarios",0)) + float(dados.get("custos",0))
    dados["id"]    = id_
    dados["atualizado_em"] = datetime.now().isoformat()
    dados.setdefault("exercicio", datetime.now().year)
    dados.setdefault("situacao_pag", "")
    dados.setdefault("corpo_req", "")
    conn = get_conn()
    conn.execute("""
        UPDATE ordens_servico
        SET servico=:servico, honorarios=:honorarios, custos=:custos,
            total=:total, pago=:pago, forma_pagamento=:forma_pagamento,
            observacoes=:observacoes, corpo_req=:corpo_req,
            atualizado_em=:atualizado_em,
            exercicio=:exercicio, situacao_pag=:situacao_pag
        WHERE id=:id
    """, dados)
    conn.commit()
    conn.close()

# ── Stats dashboard ───────────────────────────────────────────────────────────
def stats_dashboard() -> dict:
    conn = get_conn()
    mes  = datetime.now().strftime("%Y-%m")
    ano  = datetime.now().strftime("%Y")

    # ── Básico
    os_abertas   = conn.execute("SELECT COUNT(*) FROM ordens_servico WHERE status='aberta'").fetchone()[0]
    os_andamento = conn.execute("SELECT COUNT(*) FROM ordens_servico WHERE status='andamento'").fetchone()[0]
    os_mes       = conn.execute("SELECT COUNT(*) FROM ordens_servico WHERE strftime('%Y-%m',criado_em)=?", (mes,)).fetchone()[0]
    os_total     = conn.execute("SELECT COUNT(*) FROM ordens_servico").fetchone()[0]
    clientes     = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
    veiculos     = conn.execute("SELECT COUNT(*) FROM veiculos").fetchone()[0]

    # ── Financeiro
    a_receber    = conn.execute(
        "SELECT COALESCE(SUM(total-pago),0) FROM ordens_servico WHERE status NOT IN ('cancelada','concluida') AND (total-pago)>0.01"
    ).fetchone()[0]
    recebido_mes = conn.execute(
        "SELECT COALESCE(SUM(pago),0) FROM ordens_servico WHERE strftime('%Y-%m',atualizado_em)=? AND pago>0", (mes,)
    ).fetchone()[0]
    faturado_mes = conn.execute(
        "SELECT COALESCE(SUM(honorarios+custos),0) FROM ordens_servico WHERE strftime('%Y-%m',criado_em)=? AND status!='cancelada'", (mes,)
    ).fetchone()[0]
    faturado_ano = conn.execute(
        "SELECT COALESCE(SUM(honorarios+custos),0) FROM ordens_servico WHERE strftime('%Y',criado_em)=? AND status!='cancelada'", (ano,)
    ).fetchone()[0]

    # ── Faturamento por mês (últimos 6 meses)
    fat_mensal = []
    for i in range(5, -1, -1):
        d = date.today().replace(day=1)
        # subtrai i meses
        m = d.month - i
        y = d.year
        while m <= 0:
            m += 12; y -= 1
        mes_str = f"{y}-{m:02d}"
        row = conn.execute(
            "SELECT COALESCE(SUM(honorarios+custos),0), COUNT(*) FROM ordens_servico "
            "WHERE strftime('%Y-%m',criado_em)=? AND status!='cancelada'", (mes_str,)
        ).fetchone()
        fat_mensal.append({"mes": mes_str, "valor": round(float(row[0]), 2), "qtd": row[1]})

    # ── Faturamento por serviço (top 5 do ano)
    fat_servico = conn.execute("""
        SELECT servico, COUNT(*) as qtd, COALESCE(SUM(honorarios),0) as total
        FROM ordens_servico
        WHERE strftime('%Y',criado_em)=? AND status!='cancelada'
        GROUP BY servico ORDER BY total DESC LIMIT 5
    """, (ano,)).fetchall()

    # ── ALERTAS: OS paradas há mais de 20 dias sem atualização
    os_paradas = conn.execute("""
        SELECT os.id, os.numero, os.status, os.atualizado_em,
               c.nome AS cliente_nome, v.placa,
               CAST((julianday('now') - julianday(os.atualizado_em)) AS INTEGER) AS dias_parada
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE os.status IN ('aberta','andamento')
          AND (julianday('now') - julianday(os.atualizado_em)) > 20
        ORDER BY dias_parada DESC
        LIMIT 10
    """).fetchall()

    # ── ALERTAS: parcelas vencidas (não pagas, vencimento < hoje)
    parcelas_vencidas = conn.execute("""
        SELECT p.id, p.os_id, p.numero, p.valor, p.vencimento,
               os.numero AS os_numero,
               c.nome AS cliente_nome, c.telefone,
               CAST((julianday('now') - julianday(p.vencimento)) AS INTEGER) AS dias_atraso
        FROM os_parcelas p
        JOIN ordens_servico os ON os.id = p.os_id
        LEFT JOIN clientes c ON c.id = os.cliente_id
        WHERE p.pago_em IS NULL
          AND p.vencimento < date('now')
        ORDER BY dias_atraso DESC
        LIMIT 10
    """).fetchall()

    # ── OS abertas hoje
    os_hoje = conn.execute(
        "SELECT COUNT(*) FROM ordens_servico WHERE date(criado_em)=date('now')"
    ).fetchone()[0]

    # ── Pipeline: contagem por status (Kanban)
    pipeline = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) as n FROM ordens_servico WHERE status!='cancelada' GROUP BY status"
    ).fetchall():
        pipeline[row['status']] = row['n']

    # ── Top devedores (OS com maior saldo pendente)
    top_devedores = conn.execute("""
        SELECT os.id, os.numero,
               c.nome AS cliente_nome, c.telefone,
               v.placa,
               (os.honorarios + os.custos - os.pago) AS saldo
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos  v ON v.id  = os.veiculo_id
        WHERE os.status NOT IN ('cancelada', 'concluida')
          AND (os.honorarios + os.custos - os.pago) > 0.01
        ORDER BY saldo DESC
        LIMIT 6
    """).fetchall()

    conn.close()
    return {
        "os_abertas":        os_abertas,
        "os_andamento":      os_andamento,
        "os_mes":            os_mes,
        "os_total":          os_total,
        "os_hoje":           os_hoje,
        "clientes":          clientes,
        "veiculos":          veiculos,
        "a_receber":         round(float(a_receber), 2),
        "recebido_mes":      round(float(recebido_mes), 2),
        "faturado_mes":      round(float(faturado_mes), 2),
        "faturado_ano":      round(float(faturado_ano), 2),
        "fat_mensal":        fat_mensal,
        "fat_servico":       [dict(r) for r in fat_servico],
        "os_paradas":        [dict(r) for r in os_paradas],
        "parcelas_vencidas": [dict(r) for r in parcelas_vencidas],
        "pipeline":          pipeline,
        "top_devedores":     [dict(r) for r in top_devedores],
    }

# ── Finais de placa do mês ────────────────────────────────────────────────────
def os_do_mes_placa(mes: int) -> list:
    """O.S. abertas com veículos cujo final de placa corresponde ao mês."""
    finais = [k for k, v in FINAIS_PLACA.items() if v == mes]
    if not finais:
        return []
    conn  = get_conn()
    placas = []
    for f in finais:
        rows = conn.execute("""
            SELECT os.id, os.numero, os.status, v.placa, c.nome, c.telefone
            FROM veiculos v
            LEFT JOIN ordens_servico os ON os.veiculo_id = v.id AND os.status != 'cancelada'
            LEFT JOIN clientes c ON c.id = v.proprietario_id
            WHERE substr(replace(v.placa,'-',''), -1, 1) = ?
        """, (f,)).fetchall()
        placas += [dict(r) for r in rows]
    conn.close()
    return placas

# ── Documentos ────────────────────────────────────────────────────────────────
def salvar_documento(os_id: int, tipo: str, titulo: str, campos: dict) -> int:
    import json
    conn = get_conn()
    cur  = conn.execute(
        "INSERT INTO documentos (os_id,tipo,titulo,campos) VALUES (?,?,?,?)",
        (os_id, tipo, titulo, json.dumps(campos, ensure_ascii=False))
    )
    conn.commit()
    id_ = cur.lastrowid
    conn.close()
    return id_

def get_documentos_os(os_id: int) -> list:
    import json
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM documentos WHERE os_id=? ORDER BY id", (os_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["campos"] = json.loads(d["campos"] or "{}")
        except Exception:
            d["campos"] = {}
        result.append(d)
    return result


def listar_exercicios() -> list:
    """Anos de exercício distintos registrados nas O.S."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT exercicio FROM ordens_servico "
        "WHERE exercicio IS NOT NULL ORDER BY exercicio DESC"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def lista_final_placa(final: str, exercicio: int = None, situacao: str = None) -> list:
    """
    Retorna todas as O.S. de licenciamento cujo veículo termina com *final*,
    opcionalmente filtradas por exercício e situação de pagamento.
    """
    SERVICOS_LICEN = (
        'licenciamento','lic_debitos','lic_outro_municipio',
        'lic_outro_estado','boleto_divida_ativa','segunda_via_crv','pedido_etiquetas',
    )
    placeholders = ",".join("?" * len(SERVICOS_LICEN))
    where  = [f"substr(replace(v.placa,'-',''), -1, 1) = ?",
              f"os.servico IN ({placeholders})"]
    params = [final, *SERVICOS_LICEN]

    if exercicio:
        where.append("os.exercicio = ?")
        params.append(exercicio)
    if situacao == "pendente":
        where.append("os.status NOT IN ('concluida','cancelada')")
    elif situacao == "concluido":
        where.append("os.status = 'concluida'")

    conn = get_conn()
    rows = conn.execute(f"""
        SELECT
            c.nome        AS cliente,
            c.cpf,
            v.renavam,
            v.placa,
            os.exercicio,
            c.telefone,
            os.status,
            os.situacao_pag,
            os.id         AS os_id,
            os.numero
        FROM ordens_servico os
        LEFT JOIN veiculos  v ON v.id = os.veiculo_id
        LEFT JOIN clientes  c ON c.id = os.cliente_id
        WHERE {' AND '.join(where)}
        ORDER BY c.nome COLLATE NOCASE
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def atualizar_situacao_pag(os_id: int, situacao_pag: str):
    """Atualiza somente o campo situacao_pag de uma O.S."""
    conn = get_conn()
    conn.execute(
        "UPDATE ordens_servico SET situacao_pag=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
        (situacao_pag, os_id)
    )
    conn.commit()
    conn.close()


# ── CRUD Clientes (listagem e detalhe) ───────────────────────────────────────
def listar_clientes(busca: str = None, limit: int = 50, offset: int = 0) -> list:
    conn = get_conn()
    where, params = [], []
    if busca:
        where.append("(c.nome LIKE ? OR c.cpf LIKE ? OR c.telefone LIKE ? OR c.cidade LIKE ?)")
        b = f"%{busca}%"
        params += [b, b, b, b]
    wclause = ("WHERE " + " AND ".join(where)) if where else ""
    params += [limit, offset]
    rows = conn.execute(f"""
        SELECT c.id, c.tipo, c.nome, c.cpf, c.cnpj, c.telefone, c.cidade, c.uf, c.criado_em,
               COUNT(DISTINCT v.id)  AS total_veiculos,
               COUNT(DISTINCT os.id) AS total_os,
               MAX(os.criado_em)     AS ultima_os
        FROM clientes c
        LEFT JOIN veiculos       v  ON v.proprietario_id = c.id
        LEFT JOIN ordens_servico os ON os.cliente_id      = c.id
        {wclause}
        GROUP BY c.id
        ORDER BY c.nome COLLATE NOCASE
        LIMIT ? OFFSET ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def contar_clientes(busca: str = None) -> int:
    conn = get_conn()
    where, params = [], []
    if busca:
        where.append("(nome LIKE ? OR cpf LIKE ? OR telefone LIKE ? OR cidade LIKE ?)")
        b = f"%{busca}%"
        params += [b, b, b, b]
    wclause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM clientes {wclause}", params).fetchone()[0]
    conn.close()
    return total


def get_cliente_detalhe(id_: int) -> dict | None:
    """Retorna cliente + lista de veículos + histórico de OS."""
    conn = get_conn()
    row  = conn.execute("SELECT * FROM clientes WHERE id=?", (id_,)).fetchone()
    if not row:
        conn.close()
        return None
    cliente = dict(row)

    veiculos = conn.execute("""
        SELECT v.*, COUNT(os.id) AS total_os
        FROM veiculos v
        LEFT JOIN ordens_servico os ON os.veiculo_id = v.id
        WHERE v.proprietario_id = ?
        GROUP BY v.id
        ORDER BY v.placa
    """, (id_,)).fetchall()
    cliente["veiculos"] = [dict(v) for v in veiculos]

    historico = conn.execute("""
        SELECT os.id, os.numero, os.servico, os.status, os.honorarios,
               os.total, os.pago, os.criado_em, os.concluido_em,
               v.placa, v.marca, v.modelo
        FROM ordens_servico os
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE os.cliente_id = ?
        ORDER BY os.id DESC
        LIMIT 100
    """, (id_,)).fetchall()
    cliente["historico"] = [dict(h) for h in historico]

    conn.close()
    return cliente


def importar_clientes_bulk(registros: list) -> dict:
    """
    Importa lista de dicts com chaves: nome, cpf, telefone, placa, [email, cidade].
    Retorna {inseridos, duplicados, erros}.
    """
    conn    = get_conn()
    inseridos = duplicados = erros = 0
    for r in registros:
        nome    = (r.get("nome") or "").strip()
        cpf     = (r.get("cpf")  or "").strip()
        telefone= (r.get("telefone") or "").strip()
        placa   = (r.get("placa") or "").upper().replace("-","").strip()
        if not nome:
            erros += 1
            continue
        try:
            # Upsert cliente
            cur = conn.execute("""
                INSERT INTO clientes (nome, cpf, telefone, email, cidade, criado_em)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (nome, cpf, telefone, r.get("email",""), r.get("cidade","")))
            cliente_id = cur.lastrowid
            inseridos += 1
        except Exception:
            # Provavelmente duplicado — tenta buscar
            row = conn.execute(
                "SELECT id FROM clientes WHERE nome=? OR (cpf != '' AND cpf=?)",
                (nome, cpf)
            ).fetchone()
            if row:
                cliente_id = row[0]
                duplicados += 1
            else:
                erros += 1
                continue

        # Cadastra veículo se vier placa
        if placa:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO veiculos (placa, proprietario_id, criado_em)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (placa, cliente_id))
            except Exception:
                pass

    conn.commit()
    conn.close()
    return {"inseridos": inseridos, "duplicados": duplicados, "erros": erros}


# ── CRUD Débitos DETRAN ───────────────────────────────────────────────────────

def listar_debitos(os_id: int) -> list:
    """Retorna todos os débitos vinculados a uma O.S., ordenados por tipo."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM debitos_veiculo WHERE os_id = ? ORDER BY tipo, vencimento",
        (os_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _parse_valor(v) -> float:
    """Converte string de valor BR para float. Ex: 'R$ 1.420,85' → 1420.85"""
    try:
        s = str(v or "0").replace("R$","").replace(" ","").strip()
        # formato BR: ponto=milhar, vírgula=decimal
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def salvar_debitos_bulk(os_id: int, veiculo_id: int | None, debitos: list) -> dict:
    """
    Substitui todos os débitos de uma O.S. pela nova lista.
    debitos: lista de dicts com: tipo, descricao, numero_detran, valor_nominal,
             valor_multa, valor_juros, valor (=Valor Atual), vencimento, situacao, auto_infracao
    """
    conn = get_conn()
    conn.execute("DELETE FROM debitos_veiculo WHERE os_id = ?", (os_id,))
    inseridos = 0
    for d in debitos:
        tipo = (d.get("tipo") or "Outros").strip()
        if not tipo:
            continue
        valor_atual   = _parse_valor(d.get("valor") or d.get("valor_atual"))
        valor_nominal = _parse_valor(d.get("valor_nominal"))
        valor_multa   = _parse_valor(d.get("valor_multa") or d.get("multa"))
        valor_juros   = _parse_valor(d.get("valor_juros") or d.get("juros"))
        # Se não veio valor_atual mas veio nominal, usa nominal como total
        if valor_atual == 0 and valor_nominal > 0:
            valor_atual = valor_nominal + valor_multa + valor_juros
        conn.execute("""
            INSERT INTO debitos_veiculo
                (os_id, veiculo_id, tipo, descricao, numero_detran,
                 valor_nominal, valor_multa, valor_juros, valor,
                 vencimento, situacao, auto_infracao)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            os_id,
            veiculo_id,
            tipo,
            (d.get("descricao") or d.get("classe") or "").strip(),
            (d.get("numero_detran") or d.get("numero") or "").strip(),
            valor_nominal,
            valor_multa,
            valor_juros,
            valor_atual,
            (d.get("vencimento") or "").strip(),
            (d.get("situacao") or "em aberto").strip(),
            (d.get("auto_infracao") or "").strip(),
        ))
        inseridos += 1
    conn.commit()
    conn.close()
    return {"inseridos": inseridos}


def deletar_debito(debito_id: int):
    """Remove um débito pelo ID."""
    conn = get_conn()
    conn.execute("DELETE FROM debitos_veiculo WHERE id = ?", (debito_id,))
    conn.commit()
    conn.close()


def total_debitos(os_id: int) -> float:
    """Soma dos débitos em aberto de uma O.S."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(valor), 0) FROM debitos_veiculo WHERE os_id = ? AND situacao != 'pago'",
        (os_id,)
    ).fetchone()
    conn.close()
    return float(row[0])


# ── Histórico da O.S. ────────────────────────────────────────────────────────

def registrar_historico(os_id: int, status: str, nota: str = "", usuario: str = "Diogo"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO os_historico (os_id, status, nota, usuario) VALUES (?,?,?,?)",
        (os_id, status, nota, usuario)
    )
    conn.commit()
    conn.close()


def get_historico_os(os_id: int) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, status, nota, usuario, criado_em FROM os_historico WHERE os_id=? ORDER BY id ASC",
        (os_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Parcelas de pagamento ────────────────────────────────────────────────────

def criar_parcelas(os_id: int, total_parcelas: int, valor_total: float,
                   vencimento_1: str = None, forma: str = "") -> list:
    """Cria N parcelas iguais para uma O.S. Retorna lista das parcelas criadas."""
    conn = get_conn()
    # Remove parcelas antigas se existirem
    conn.execute("DELETE FROM os_parcelas WHERE os_id=?", (os_id,))
    valor_parc = round(valor_total / total_parcelas, 2)
    # Ajuste de centavos na última parcela
    resto = round(valor_total - valor_parc * total_parcelas, 2)
    criadas = []
    from datetime import date, timedelta
    try:
        base = date.fromisoformat(vencimento_1) if vencimento_1 else date.today()
    except Exception:
        base = date.today()
    for i in range(1, total_parcelas + 1):
        venc = (base + timedelta(days=30 * (i - 1))).isoformat()
        val  = valor_parc + (resto if i == total_parcelas else 0)
        cur = conn.execute("""
            INSERT INTO os_parcelas (os_id, numero, valor, vencimento, forma_pagamento)
            VALUES (?,?,?,?,?)
        """, (os_id, i, val, venc, forma))
        criadas.append({"id": cur.lastrowid, "numero": i, "valor": val,
                        "vencimento": venc, "pago_em": None})
    conn.execute("UPDATE ordens_servico SET total_parcelas=? WHERE id=?",
                 (total_parcelas, os_id))
    conn.commit()
    conn.close()
    return criadas


def get_parcelas(os_id: int) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, numero, valor, vencimento, pago_em, forma_pagamento, observacao "
        "FROM os_parcelas WHERE os_id=? ORDER BY numero ASC",
        (os_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def dar_baixa_parcela(parcela_id: int, forma: str, observacao: str = "") -> dict:
    """Marca uma parcela como paga. Atualiza campo 'pago' da OS."""
    conn = get_conn()
    row = conn.execute(
        "SELECT os_id, valor, pago_em FROM os_parcelas WHERE id=?", (parcela_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"erro": "Parcela não encontrada"}
    if row["pago_em"]:
        conn.close()
        return {"erro": "Parcela já está paga"}

    agora = datetime.now().isoformat()
    conn.execute(
        "UPDATE os_parcelas SET pago_em=?, forma_pagamento=?, observacao=? WHERE id=?",
        (agora, forma, observacao, parcela_id)
    )
    # Recalcula total pago na OS
    total_pago = conn.execute(
        "SELECT COALESCE(SUM(valor),0) FROM os_parcelas WHERE os_id=? AND pago_em IS NOT NULL",
        (row["os_id"],)
    ).fetchone()[0]
    conn.execute(
        "UPDATE ordens_servico SET pago=?, atualizado_em=? WHERE id=?",
        (round(total_pago, 2), agora, row["os_id"])
    )
    conn.commit()
    conn.close()
    return {"ok": True, "pago_em": agora, "total_pago": round(total_pago, 2)}


def estornar_parcela(parcela_id: int) -> dict:
    """Desfaz o pagamento de uma parcela."""
    conn = get_conn()
    row = conn.execute("SELECT os_id FROM os_parcelas WHERE id=?", (parcela_id,)).fetchone()
    if not row:
        conn.close()
        return {"erro": "Parcela não encontrada"}
    conn.execute(
        "UPDATE os_parcelas SET pago_em=NULL, forma_pagamento=NULL, observacao=NULL WHERE id=?",
        (parcela_id,)
    )
    total_pago = conn.execute(
        "SELECT COALESCE(SUM(valor),0) FROM os_parcelas WHERE os_id=? AND pago_em IS NOT NULL",
        (row["os_id"],)
    ).fetchone()[0]
    conn.execute("UPDATE ordens_servico SET pago=? WHERE id=?",
                 (round(total_pago, 2), row["os_id"]))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Busca global ────────────────────────────────────────────────────────────

def busca_global(q: str, limit: int = 10) -> dict:
    """Busca clientes, veículos e OS por qualquer termo."""
    conn = get_conn()
    b = f"%{q}%"
    clientes = conn.execute("""
        SELECT id, nome, cpf, telefone, cidade
        FROM clientes
        WHERE nome LIKE ? OR cpf LIKE ? OR telefone LIKE ?
        ORDER BY nome LIMIT ?
    """, (b, b, b, limit)).fetchall()

    veiculos = conn.execute("""
        SELECT v.id, v.placa, v.marca, v.modelo, v.ano_fab,
               c.nome AS proprietario, c.telefone
        FROM veiculos v
        LEFT JOIN clientes c ON c.id = v.proprietario_id
        WHERE v.placa LIKE ? OR v.renavam LIKE ? OR v.chassi LIKE ?
        ORDER BY v.placa LIMIT ?
    """, (b, b, b, limit)).fetchall()

    ordens = conn.execute("""
        SELECT os.id, os.numero, os.servico, os.status, os.pago, os.total,
               c.nome AS cliente_nome, c.telefone,
               v.placa
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE os.numero LIKE ? OR c.nome LIKE ? OR v.placa LIKE ? OR c.cpf LIKE ?
        ORDER BY os.id DESC LIMIT ?
    """, (b, b, b, b, limit)).fetchall()

    conn.close()
    return {
        "clientes": [dict(r) for r in clientes],
        "veiculos":  [dict(r) for r in veiculos],
        "ordens":    [dict(r) for r in ordens],
    }


# ── Relatório de retenção ────────────────────────────────────────────────────

def relatorio_retencao(ano: int, servico: str = None) -> list:
    """
    Retorna todos os clientes que fizeram serviço no ano informado,
    agrupados com dados para campanha de retenção no próximo ano.
    """
    conn = get_conn()
    where = ["strftime('%Y', os.criado_em) = ?"]
    params = [str(ano)]
    if servico:
        where.append("os.servico = ?")
        params.append(servico)
    rows = conn.execute(f"""
        SELECT
          os.id       AS os_id,
          os.numero,
          os.servico,
          os.status,
          os.criado_em,
          os.exercicio,
          os.honorarios,
          os.total,
          os.pago,
          c.id        AS cliente_id,
          c.nome,
          c.cpf,
          c.telefone,
          c.cidade,
          v.placa,
          v.marca,
          v.modelo,
          v.ano_fab,
          CASE
            WHEN v.placa GLOB '*[0-9]'   THEN SUBSTR(v.placa,-1,1)
            WHEN v.placa GLOB '*[A-Z][0-9][0-9][0-9]' THEN SUBSTR(v.placa,-2,1)
            ELSE ''
          END AS final_placa
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE {' AND '.join(where)}
          AND os.status != 'cancelada'
        ORDER BY os.criado_em DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Config geral (key/value) ──────────────────────────────────────────────────

def get_config(chave: str, default=None):
    conn = get_conn()
    row  = conn.execute("SELECT valor FROM config WHERE chave=?", (chave,)).fetchone()
    conn.close()
    return row["valor"] if row else default

def set_config(chave: str, valor: str):
    conn = get_conn()
    conn.execute("""
        INSERT INTO config (chave, valor, atualizado_em) VALUES (?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor, atualizado_em=CURRENT_TIMESTAMP
    """, (chave, valor))
    conn.commit()
    conn.close()


# ── Templates de mensagem WhatsApp ───────────────────────────────────────────

import json as _json_db

TEMPLATES_PADRAO: dict = {
    "os_aberta": {
        "nome": "OS Aberta",
        "texto": "Olá {nome}! 😊 Recebemos sua O.S. nº {numero} para *{servico}*. Em breve entraremos em contato com atualizações. Qualquer dúvida, é só chamar! — {despachante} 📲 {whatsapp}",
    },
    "os_concluida": {
        "nome": "OS Concluída",
        "texto": "Olá {nome}! ✅ Sua O.S. nº {numero} (*{servico}*) está *PRONTA*! Pode passar para retirar os documentos. — {despachante} 📲 {whatsapp}",
    },
    "cobranca": {
        "nome": "Cobrança",
        "texto": "Olá {nome}, passando para lembrar que há *R$ {pendente}* em aberto referente à O.S. {numero} ({servico}). PIX: {pix} — {despachante} 📲 {whatsapp}",
    },
    "licenciamento_vence": {
        "nome": "Licenciamento Vencendo",
        "texto": "Olá {nome}! 🚗 O licenciamento {exercicio} do seu veículo *{placa}* vence em *{mes}*. Podemos resolver tudo sem você precisar sair de casa! Entre em contato: {whatsapp} — {despachante}",
    },
    "licenciamento_vencido": {
        "nome": "Licenciamento Vencido",
        "texto": "Olá {nome}! ⚠️ O licenciamento {exercicio} do seu veículo *{placa}* está *ATRASADO* desde {mes}. Regularize antes de ter problemas com multa! Fale com a gente: {whatsapp} — {despachante}",
    },
    "aguardando_doc": {
        "nome": "Aguardando Documento",
        "texto": "Olá {nome}! 📋 Sua O.S. {numero} está aguardando um documento para prosseguir. Por favor, entre em contato o quanto antes: {whatsapp} — {despachante}",
    },
    "aniversario": {
        "nome": "Aniversário do Cliente",
        "texto": "Parabéns {nome}! 🎉🎂 Desejamos um ótimo aniversário! É um prazer contar com você como cliente. — {despachante}",
    },
}

def get_templates_wpp() -> dict:
    """Retorna dict {chave: {nome, texto}} — merge padrão + personalizados."""
    raw = get_config("templates_wpp")
    if raw:
        try:
            salvos = _json_db.loads(raw)
            merged = {}
            for k, v in TEMPLATES_PADRAO.items():
                merged[k] = {**v, **(salvos.get(k, {}))}
            return merged
        except Exception:
            pass
    return {k: dict(v) for k, v in TEMPLATES_PADRAO.items()}

def set_templates_wpp(templates: dict):
    """Salva templates editados pelo usuário."""
    set_config("templates_wpp", _json_db.dumps(templates, ensure_ascii=False))

def get_template_wpp(chave: str) -> str:
    """Retorna o texto de um template específico."""
    return get_templates_wpp().get(chave, {}).get("texto", "")


# ── Tabela de preços por serviço ─────────────────────────────────────────────

PRECOS_PADRAO: dict = {
    # Licenciamento
    "licenciamento":              120.0,
    "lic_debitos":                150.0,
    "lic_outro_municipio":        140.0,
    "lic_outro_estado":           220.0,
    "lic_emissao":                 80.0,
    # Transferência
    "transferencia":              280.0,
    "transferencia_debito":       350.0,
    "transferencia_gravame":      320.0,
    "transferencia_leilao":       420.0,
    "transferencia_outro_estado": 380.0,
    "atpv_comunicado":            120.0,
    # Especial
    "inventario":                 550.0,
    "recibo_inventario":          300.0,
    "baixa_administrativa":       250.0,
    "baixa_circulacao":           200.0,
    "segunda_via_crv":            180.0,
    "comunicado_retroativo":      200.0,
    # Boletos
    "boleto_multa":                60.0,
    "boleto_licenciamento":        40.0,
    "boleto_ipva":                 40.0,
    "boleto_divida_ativa":         60.0,
    "boletos":                     50.0,
    # Alterações
    "alt_cor":                    220.0,
    "alt_motor":                  280.0,
    "alt_carroceria":             200.0,
    "alt_categoria":              200.0,
    "alt_municipio":              180.0,
    "bloqueio":                   150.0,
    "desbloqueio":                150.0,
    "restricao":                  200.0,
    # CNH
    "cnh_primeira":               120.0,
    "cnh_renovacao":              100.0,
    "cnh_mudanca_categoria":      150.0,
    "cnh_segunda_via":            150.0,
    "cnh_acc":                    150.0,
    "cnh_cassada":                200.0,
    "pontuacao":                  100.0,
    # PCD
    "pcd_ipva":                   380.0,
    "pcd_0km":                    420.0,
    "vaga_especial":              200.0,
    # Outros
    "seguro_carta_verde":          80.0,
    "abertura_mei":               150.0,
    "protecao_veicular":           80.0,
    "outros":                     100.0,
}

def get_tabela_precos() -> dict:
    """Retorna dict {servico: preco} — merge padrão com personalizados."""
    raw = get_config("tabela_precos")
    if raw:
        try:
            salvos = _json_db.loads(raw)
            merged = {**PRECOS_PADRAO, **salvos}
            return merged
        except Exception:
            pass
    return dict(PRECOS_PADRAO)

def set_tabela_precos(precos: dict):
    """Salva tabela de preços (apenas os personalizados sobre o padrão)."""
    set_config("tabela_precos", _json_db.dumps(precos, ensure_ascii=False))

# ── Custo de Produção / Taxas por serviço ───────────────────────────────────
# Valores padrão de custo/taxa para cada serviço (o que o despachante paga)
CUSTOS_PADRAO: dict = {k: 0.0 for k in PRECOS_PADRAO}

def get_tabela_custos() -> dict:
    """Retorna dict {servico: custo} — custo de produção/taxa por serviço."""
    raw = get_config("tabela_custos")
    if raw:
        try:
            salvos = _json_db.loads(raw)
            merged = {**CUSTOS_PADRAO, **salvos}
            return merged
        except Exception:
            pass
    return dict(CUSTOS_PADRAO)

def set_tabela_custos(custos: dict):
    """Salva tabela de custos de produção."""
    set_config("tabela_custos", _json_db.dumps(custos, ensure_ascii=False))

def get_custo_servico(servico: str) -> float:
    """Retorna o custo de produção de um serviço específico."""
    return get_tabela_custos().get(servico, 0.0)

# ── Protocolos RENAVAM ───────────────────────────────────────────────────────

def listar_protocolos(lote: str = None, usado: bool = None, busca: str = None) -> list:
    conn   = get_conn()
    where, params = [], []
    if lote:
        where.append("lote = ?"); params.append(lote)
    if usado is not None:
        where.append("usado = ?"); params.append(1 if usado else 0)
    if busca:
        where.append("(protocolo LIKE ? OR lote LIKE ? OR observacao LIKE ?)")
        b = f"%{busca}%"; params += [b, b, b]
    wc = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(f"""
        SELECT p.*, os.numero AS os_numero
        FROM protocolos_renavam p
        LEFT JOIN ordens_servico os ON os.id = p.os_id
        {wc}
        ORDER BY p.id DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def criar_protocolo(dados: dict) -> int:
    conn = get_conn()
    cur  = conn.execute("""
        INSERT INTO protocolos_renavam (protocolo, letra, lote, emitido_em, observacao)
        VALUES (:protocolo, :letra, :lote, :emitido_em, :observacao)
    """, dados)
    conn.commit(); id_ = cur.lastrowid; conn.close()
    return id_

def criar_protocolos_lote(protocolos: list, letra: str, lote: str,
                           emitido_em: str, observacao: str) -> int:
    """Cria múltiplos protocolos de uma vez (importação em lote)."""
    conn = get_conn()
    inseridos = 0
    for p in protocolos:
        p = str(p).strip()
        if not p: continue
        try:
            conn.execute("""
                INSERT INTO protocolos_renavam (protocolo, letra, lote, emitido_em, observacao)
                VALUES (?, ?, ?, ?, ?)
            """, (p, letra, lote, emitido_em, observacao))
            inseridos += 1
        except Exception:
            pass
    conn.commit(); conn.close()
    return inseridos

def vincular_protocolo_os(protocolo_id: int, os_id: int):
    conn = get_conn()
    conn.execute("UPDATE protocolos_renavam SET usado=1, os_id=? WHERE id=?", (os_id, protocolo_id))
    conn.commit(); conn.close()

def deletar_protocolo(protocolo_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM protocolos_renavam WHERE id=?", (protocolo_id,))
    conn.commit(); conn.close()

def stats_protocolos() -> dict:
    conn   = get_conn()
    total  = conn.execute("SELECT COUNT(*) FROM protocolos_renavam").fetchone()[0]
    usados = conn.execute("SELECT COUNT(*) FROM protocolos_renavam WHERE usado=1").fetchone()[0]
    lotes  = conn.execute("SELECT DISTINCT lote FROM protocolos_renavam WHERE lote IS NOT NULL ORDER BY lote DESC").fetchall()
    conn.close()
    return {"total": total, "usados": usados, "disponiveis": total - usados,
            "lotes": [r[0] for r in lotes]}


def get_preco_servico(servico: str) -> float:
    """Retorna o preço padrão de um serviço específico."""
    tabela = get_tabela_precos()
    return tabela.get(servico, 0.0)


# ── Checklist de documentos por OS ───────────────────────────────────────────

def get_checklist_os(os_id: int, servico: str = '') -> list:
    """
    Retorna a lista de itens do checklist de uma OS.
    Se ainda não existe, inicializa com os docs padrão do serviço.
    Retorna: [{item, feito}, ...]
    """
    raw = get_config(f"chk_{os_id}")
    if raw:
        try:
            return _json_db.loads(raw)
        except Exception:
            pass
    # Inicializa com docs padrão do serviço
    docs = DOCS_POR_SERVICO.get(servico, DOCS_PADRAO) if servico else DOCS_PADRAO
    return [{"item": d, "feito": False} for d in docs]

def set_checklist_os(os_id: int, checklist: list):
    """Salva o checklist de uma OS."""
    set_config(f"chk_{os_id}", _json_db.dumps(checklist, ensure_ascii=False))

def toggle_checklist_item(os_id: int, idx: int, feito: bool, servico: str = '') -> list:
    """Marca/desmarca um item e salva. Retorna checklist atualizado."""
    chk = get_checklist_os(os_id, servico)
    if 0 <= idx < len(chk):
        chk[idx]['feito'] = feito
    set_checklist_os(os_id, chk)
    return chk

def add_checklist_item(os_id: int, item: str, servico: str = '') -> list:
    """Adiciona item custom ao checklist."""
    chk = get_checklist_os(os_id, servico)
    chk.append({"item": item, "feito": False})
    set_checklist_os(os_id, chk)
    return chk

def remove_checklist_item(os_id: int, idx: int, servico: str = '') -> list:
    """Remove item do checklist."""
    chk = get_checklist_os(os_id, servico)
    if 0 <= idx < len(chk):
        chk.pop(idx)
    set_checklist_os(os_id, chk)
    return chk


# ── Não Licenciados ──────────────────────────────────────────────────────────

_SERVICOS_LIC = ('licenciamento','lic_debitos','lic_outro_municipio',
                 'lic_outro_estado','lic_emissao')

def veiculos_nao_licenciados(exercicio: int = None, final_placa: str = None,
                              mostrar: str = 'sem_os') -> list:
    """
    Retorna veículos de clientes cujo licenciamento está em atraso ou
    vencendo no mês atual e sem OS de licenciamento concluída para o exercício.

    mostrar: 'sem_os'      → sem nenhuma OS de licenciamento este ano
             'em_andamento'→ com OS em aberto/andamento (já contatados)
             'todos'       → todos sem OS concluída
    """
    from datetime import date
    hoje  = date.today()
    ano   = exercicio or hoje.year
    phs   = ",".join("?" * len(_SERVICOS_LIC))

    conn  = get_conn()
    rows  = conn.execute(f"""
        SELECT
            v.id        AS veiculo_id,
            v.placa,
            v.renavam,
            v.marca,
            v.modelo,
            v.ano_mod,
            c.id        AS cliente_id,
            c.nome      AS cliente,
            c.telefone,
            c.cpf,
            (SELECT COUNT(*) FROM ordens_servico os
             WHERE os.veiculo_id = v.id AND os.exercicio = ?
               AND os.servico IN ({phs}) AND os.status = 'concluida'
            ) AS lic_concluida,
            (SELECT COUNT(*) FROM ordens_servico os
             WHERE os.veiculo_id = v.id AND os.exercicio = ?
               AND os.servico IN ({phs}) AND os.status NOT IN ('cancelada','concluida')
            ) AS lic_em_andamento,
            (SELECT os.id FROM ordens_servico os
             WHERE os.veiculo_id = v.id AND os.exercicio = ?
               AND os.servico IN ({phs}) AND os.status NOT IN ('cancelada')
             ORDER BY os.id DESC LIMIT 1
            ) AS os_id,
            (SELECT os.numero FROM ordens_servico os
             WHERE os.veiculo_id = v.id AND os.exercicio = ?
               AND os.servico IN ({phs}) AND os.status NOT IN ('cancelada')
             ORDER BY os.id DESC LIMIT 1
            ) AS os_numero,
            (SELECT os.status FROM ordens_servico os
             WHERE os.veiculo_id = v.id AND os.exercicio = ?
               AND os.servico IN ({phs}) AND os.status NOT IN ('cancelada')
             ORDER BY os.id DESC LIMIT 1
            ) AS os_status
        FROM veiculos v
        INNER JOIN clientes c ON c.id = v.proprietario_id
        WHERE v.placa IS NOT NULL AND v.placa != ''
        ORDER BY c.nome COLLATE NOCASE
    """, (ano, *_SERVICOS_LIC,
          ano, *_SERVICOS_LIC,
          ano, *_SERVICOS_LIC,
          ano, *_SERVICOS_LIC,
          ano, *_SERVICOS_LIC)).fetchall()
    conn.close()

    mes_atual = hoje.month
    result    = []
    for r in rows:
        row = dict(r)
        placa  = (row['placa'] or '').replace('-', '').strip()
        if not placa: continue
        final  = placa[-1]
        mes_v  = FINAIS_PLACA.get(final, 0)
        if mes_v == 0: continue                        # placa sem mapeamento
        if final_placa and final != final_placa: continue

        row['final']        = final
        row['mes_venc']     = mes_v
        row['mes_venc_nome'] = MESES[mes_v]
        row['atrasado']     = mes_v < mes_atual        # prazo já passou
        row['vence_agora']  = mes_v == mes_atual       # vence este mês

        if row['lic_concluida']:                       # já licenciado → pula
            continue
        if not (row['atrasado'] or row['vence_agora']): # prazo futuro → pula
            continue

        if mostrar == 'sem_os' and row['lic_em_andamento']:
            continue
        if mostrar == 'em_andamento' and not row['lic_em_andamento']:
            continue

        result.append(row)

    return result


def stats_nao_licenciados(exercicio: int = None) -> dict:
    """Counts rápidos para o header da página."""
    from datetime import date
    hoje = date.today()
    ano  = exercicio or hoje.year
    todos       = veiculos_nao_licenciados(exercicio=ano, mostrar='todos')
    sem_os      = [v for v in todos if not v['lic_em_andamento']]
    em_andamento= [v for v in todos if v['lic_em_andamento']]
    atrasados   = [v for v in todos if v['atrasado']]
    return {
        'total':        len(todos),
        'sem_os':       len(sem_os),
        'em_andamento': len(em_andamento),
        'atrasados':    len(atrasados),
    }


# ── Relatório de Produção ──────────────────────────────────────────────────────

def relatorio_producao(data_ini: str, data_fim: str,
                       servico: str = None, status: str = None) -> dict:
    """
    Retorna dict com lista de OS + totais para o período.
    data_ini / data_fim no formato YYYY-MM-DD.
    """
    conn   = get_conn()
    where  = ["date(os.criado_em) >= ?", "date(os.criado_em) <= ?"]
    params = [data_ini, data_fim]

    if servico:
        where.append("os.servico = ?")
        params.append(servico)
    if status:
        where.append("os.status = ?")
        params.append(status)

    rows = conn.execute(f"""
        SELECT os.id, os.numero, os.servico, os.status,
               os.honorarios, os.custos, os.pago,
               (os.honorarios + os.custos) AS total,
               (os.honorarios + os.custos - os.pago) AS pendente,
               os.forma_pagamento, os.exercicio, os.criado_em, os.atualizado_em,
               c.nome AS cliente_nome, c.cpf, c.telefone,
               v.placa
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos v ON v.id = os.veiculo_id
        WHERE {' AND '.join(where)}
        ORDER BY os.criado_em DESC
    """, params).fetchall()

    ordens = [dict(r) for r in rows]

    # Totais
    total_os       = len(ordens)
    faturado       = sum(o["total"]    for o in ordens if o["status"] != "cancelada")
    recebido       = sum(o["pago"]     for o in ordens if o["status"] != "cancelada")
    pendente       = sum(o["pendente"] for o in ordens if o["status"] != "cancelada" and o["pendente"] > 0)
    concluidas     = sum(1 for o in ordens if o["status"] == "concluida")
    canceladas     = sum(1 for o in ordens if o["status"] == "cancelada")

    # Por serviço
    por_servico: dict = {}
    for o in ordens:
        if o["status"] == "cancelada": continue
        svc = o["servico"]
        if svc not in por_servico:
            por_servico[svc] = {"qtd": 0, "honorarios": 0.0}
        por_servico[svc]["qtd"]        += 1
        por_servico[svc]["honorarios"] += o.get("honorarios", 0)

    # Por forma de pagamento
    por_forma: dict = {}
    for o in ordens:
        if o["status"] == "cancelada" or not o["pago"]: continue
        forma = o.get("forma_pagamento") or "Não informado"
        por_forma[forma] = por_forma.get(forma, 0) + o["pago"]

    conn.close()
    return {
        "ordens": ordens,
        "total_os": total_os,
        "faturado": round(faturado, 2),
        "recebido": round(recebido, 2),
        "pendente": round(pendente, 2),
        "concluidas": concluidas,
        "canceladas": canceladas,
        "por_servico": sorted(por_servico.items(), key=lambda x: x[1]["honorarios"], reverse=True),
        "por_forma":   sorted(por_forma.items(),   key=lambda x: x[1], reverse=True),
    }


# ── Relatório "Fez / Não Fez" ─────────────────────────────────────────────────

def relatorio_fez_nao_fez(servico: str, data_ini: str, data_fim: str) -> dict:
    """
    Para um serviço e período, retorna:
    - fizeram   → OS com status 'concluida'
    - pendentes → OS com status 'aberta' ou 'andamento'
    Útil para saber quem regularizou e quem ainda não fez.
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT os.id, os.numero, os.servico, os.status,
               os.honorarios, os.custos, os.pago,
               (os.honorarios + os.custos)           AS total,
               (os.honorarios + os.custos - os.pago) AS pendente,
               os.criado_em, os.concluido_em, os.exercicio,
               c.id   AS cliente_id,
               c.nome AS cliente_nome, c.telefone, c.cpf,
               v.placa, v.marca, v.modelo
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos  v ON v.id  = os.veiculo_id
        WHERE os.servico = ?
          AND date(os.criado_em) >= ?
          AND date(os.criado_em) <= ?
          AND os.status != 'cancelada'
        ORDER BY
            CASE os.status
                WHEN 'aberta'    THEN 1
                WHEN 'andamento' THEN 2
                WHEN 'concluida' THEN 3
                ELSE 4
            END,
            os.criado_em DESC
    """, (servico, data_ini, data_fim)).fetchall()
    conn.close()

    ordens    = [dict(r) for r in rows]
    fizeram   = [o for o in ordens if o['status'] == 'concluida']
    pendentes = [o for o in ordens if o['status'] != 'concluida']
    n_total   = len(ordens)

    return {
        'ordens':       ordens,
        'fizeram':      fizeram,
        'pendentes':    pendentes,
        'n_total':      n_total,
        'n_fizeram':    len(fizeram),
        'n_pendentes':  len(pendentes),
        'pct_fizeram':  round(len(fizeram) / n_total * 100) if n_total else 0,
        'financeiro': {
            'total_faturado': round(sum(o['total'] for o in ordens), 2),
            'total_recebido': round(sum(o['pago']  for o in ordens), 2),
            'total_pendente': round(sum(o['pendente'] for o in pendentes if o['pendente'] > 0), 2),
        },
    }


# ── Portal do cliente (acesso público por token) ──────────────────────────────

def gerar_token_os(os_id: int) -> str:
    """Gera (ou retorna existente) token público para uma OS."""
    import secrets as _secrets
    conn = get_conn()
    row = conn.execute(
        "SELECT token_publico FROM ordens_servico WHERE id=?", (os_id,)
    ).fetchone()
    if row and row['token_publico']:
        conn.close()
        return row['token_publico']
    token = _secrets.token_urlsafe(24)
    conn.execute(
        "UPDATE ordens_servico SET token_publico=? WHERE id=?", (token, os_id)
    )
    conn.commit()
    conn.close()
    return token


def get_os_por_token(token: str) -> dict | None:
    """Busca dados públicos de uma OS pelo token (sem login)."""
    conn = get_conn()
    row = conn.execute("""
        SELECT os.id, os.numero, os.servico, os.status, os.token_publico,
               os.honorarios, os.custos, os.pago,
               (os.honorarios + os.custos)           AS total,
               (os.honorarios + os.custos - os.pago) AS pendente,
               os.criado_em, os.atualizado_em, os.concluido_em,
               os.exercicio, os.situacao_pag,
               c.nome AS cliente_nome, c.telefone AS cliente_tel,
               v.placa, v.marca, v.modelo, v.ano_fab, v.ano_mod, v.cor
        FROM ordens_servico os
        LEFT JOIN clientes c ON c.id = os.cliente_id
        LEFT JOIN veiculos  v ON v.id  = os.veiculo_id
        WHERE os.token_publico = ?
    """, (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def revogar_token_os(os_id: int):
    """Revoga o token público (gera novo na próxima chamada)."""
    conn = get_conn()
    conn.execute(
        "UPDATE ordens_servico SET token_publico=NULL WHERE id=?", (os_id,)
    )
    conn.commit()
    conn.close()
