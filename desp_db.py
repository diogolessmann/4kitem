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
            ("boleto_divida_ativa",    "Boleto / Dívida Ativa"),
            ("segunda_via_crv",        "2ª Via do CRV"),
            ("pedido_etiquetas",       "Pedido de Etiquetas"),
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
            ("transferencia_inventario",   "Inventário / Herança"),
            ("atpv_comunicado",            "Comunicado de Venda (ATPV-e)"),
            ("comunicado_retroativo",      "Comunicado Retroativo — Limpa CNH"),
            ("baixa_circulacao",           "Baixa de Circulação (Sucata)"),
            ("baixa_administrativa",       "Baixa Administrativa"),
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
            ("contrato_compra_venda", "Contrato de Compra e Venda"),
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

# Final de placa → mês de licenciamento (SC)
FINAIS_PLACA = {
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "6": 6, "7": 7, "8": 8, "9": 9, "0": 10,
}
MESES = ["", "Janeiro","Fevereiro","Março","Abril","Maio","Junho",
         "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]

STATUS_LABELS = {
    "aberta":    ("🟡", "Aberta"),
    "andamento": ("🔵", "Em Andamento"),
    "concluida": ("🟢", "Concluída"),
    "cancelada": ("🔴", "Cancelada"),
}

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

        CREATE INDEX IF NOT EXISTS idx_desp_os_cliente  ON ordens_servico(cliente_id);
        CREATE INDEX IF NOT EXISTS idx_desp_os_veiculo  ON ordens_servico(veiculo_id);
        CREATE INDEX IF NOT EXISTS idx_desp_os_status   ON ordens_servico(status);
        CREATE INDEX IF NOT EXISTS idx_desp_os_criado   ON ordens_servico(criado_em DESC);
        CREATE INDEX IF NOT EXISTS idx_desp_veiculo_placa ON veiculos(placa);
        CREATE INDEX IF NOT EXISTS idx_desp_cliente_cpf   ON clientes(cpf);
    """)
    conn.commit()

    # ── Migrations suaves (colunas adicionadas depois do CREATE inicial) ────────
    _migrations = [
        "ALTER TABLE ordens_servico ADD COLUMN exercicio INTEGER",
        "ALTER TABLE ordens_servico ADD COLUMN situacao_pag TEXT DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_desp_os_exercicio ON ordens_servico(exercicio)",
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
          os.observacoes, os.criado_em, os.atualizado_em, os.concluido_em,
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
    conn = get_conn()
    conn.execute("""
        UPDATE ordens_servico
        SET servico=:servico, honorarios=:honorarios, custos=:custos,
            total=:total, pago=:pago, forma_pagamento=:forma_pagamento,
            observacoes=:observacoes, atualizado_em=:atualizado_em,
            exercicio=:exercicio, situacao_pag=:situacao_pag
        WHERE id=:id
    """, dados)
    conn.commit()
    conn.close()

# ── Stats dashboard ───────────────────────────────────────────────────────────
def stats_dashboard() -> dict:
    conn = get_conn()
    mes  = datetime.now().strftime("%Y-%m")
    r = {
        "os_abertas":     conn.execute("SELECT COUNT(*) FROM ordens_servico WHERE status='aberta'").fetchone()[0],
        "os_andamento":   conn.execute("SELECT COUNT(*) FROM ordens_servico WHERE status='andamento'").fetchone()[0],
        "os_mes":         conn.execute("SELECT COUNT(*) FROM ordens_servico WHERE strftime('%Y-%m',criado_em)=?", (mes,)).fetchone()[0],
        "os_total":       conn.execute("SELECT COUNT(*) FROM ordens_servico").fetchone()[0],
        "a_receber":      conn.execute("SELECT COALESCE(SUM(total-pago),0) FROM ordens_servico WHERE status IN ('aberta','andamento') AND total>pago").fetchone()[0],
        "recebido_mes":   conn.execute("SELECT COALESCE(SUM(pago),0) FROM ordens_servico WHERE strftime('%Y-%m',atualizado_em)=? AND pago>0", (mes,)).fetchone()[0],
        "clientes":       conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0],
        "veiculos":       conn.execute("SELECT COUNT(*) FROM veiculos").fetchone()[0],
    }
    conn.close()
    return r

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
