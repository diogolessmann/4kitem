# -*- coding: utf-8 -*-
"""🗂️ CENTRAL DL — a midiateca das marcas Lessmann DENTRO do 4kitem (migrada da Rádio, 01/set/2026).

Motivo: a Rádio SC News foi vendida ao Gabriel; as ferramentas das marcas do grupo
(Despachante · DL Defesas · DL Mobilidade) mudam de casa. Publicação SÓ no IG do
Despachante — a opção "postar na Rádio" deixou de existir por decisão do dono.

Arquitetura (espelho enxuto da midiateca original):
- Arquivos: repo em static/dlcentral/<marca>/ (fotos leves, versionadas) e uploads
  no VOLUME (DATA_DIR/dlcentral/<marca>) — servidos por /dlmedia/<marca>/<arquivo>.
- Upload de FOTO é comprimido na entrada (alvo ~100-200 KB, máx 1350px).
- Metadados em JSON no volume (título/contexto/preço/legenda/publicados).
- Legenda de VENDA por IA (Gemini REST direto) com fallback construído — o mesmo
  método validado: emoção abre, razão fecha, CTA no WhatsApp (nunca "zap" — regra do dono, 27/set).
- Publicação: foto (container→publish) e reel (container REELS→poll→publish),
  tokens por env: DESP_PAGE_TOKEN + DESP_IG_USER_ID (copiar do Railway da Rádio).
"""
import io
import json
import os
import threading
import time
import urllib.request
import urllib.parse
from datetime import datetime

GRAPH = "https://graph.facebook.com/v21.0"
PUBLIC_BASE = os.environ.get("DL_PUBLIC_BASE", "https://www.4kitem.com.br").rstrip("/")

MARCAS = {
    "dlmob": {
        "label": "🛵 Scooters · DL Mobilidade",
        "tipo": "scooter",
        "telefone": "(47) 99776-6831",
        "endereco": "R. Mal. Castelo Branco, 2838 — Centro, Schroeder/SC",
        "hashtags": "#scootereletrica #Schroeder #JaraguaDoSul #ValeDoItapocu #DLMobilidade",
        "disclaimer": "*sujeito a análise de crédito",
    },
    "defesas": {
        "label": "DL Defesas",   # 06/10: sem a balança (símbolo de advocacia; a marca não é escritório de advocacia)
        "tipo": "defesa",
        "telefone": "(47) 99716-2967",
        "endereco": "R. Mal. Castelo Branco, 2838, Sala 02 — Centro, Schroeder/SC",
        "hashtags": "#defesademulta #recursodemulta #cnhsuspensa #jaraguadosul #DespachanteLessmann",   # 5 (limite do Instagram; A3)
        # 06/10: versão curta; o rodapé COMPLETO (razão social, CNPJ, credencial da pessoa, CRDD) é montado por _rodape_defesas() e colado por gerar_legenda
        "disclaimer": "DL Defesas, um produto do Despachante Lessmann · Defesa administrativa: não somos escritório de advocacia",
    },
    "despachante": {
        "label": "🏛️ Despachante Lessmann",
        "tipo": "servicos",
        "telefone": "(47) 99716-2967",
        "endereco": "R. Mal. Castelo Branco, 2838, Sala 02 — Centro, Schroeder/SC",
        "hashtags": "#despachante #Schroeder #detransc #transferencia #licenciamento #DespachanteLessmann",
        "disclaimer": "Credencial DETRAN/SC nº 2095",
    },
    # 28/set/26 — aba MOTOR = propaganda da VITRINE (site da loja + motor que posta). A Rádio foi
    # vendida (24/set): nada aqui fala dela. Público = lojista/empresário. WhatsApp = o da LOJA.
    "motor": {
        "label": "🏪 MOTOR · Vitrine (site + motor)",
        "tipo": "motor",
        "telefone": "(47) 99776-6831",
        "endereco": "Schroeder/SC · atende todo o Vale do Itapocu",
        "hashtags": "#comerciolocal #catalogodigital #sitedaloja #Schroeder #JaraguaDoSul #ValeDoItapocu #4kitem",
        "disclaimer": "Schroeder/SC · imagens ilustrativas",
    },
}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA = os.environ.get("DATA_DIR", _BASE_DIR)
_UP_BASE = os.path.join(_DATA, "dlcentral")
_META_PATH = os.path.join(_DATA, "dlcentral_meta.json")
_LOG_PATH = os.path.join(_DATA, "dlcentral_log.json")
_REPO_BASE = os.path.join(_BASE_DIR, "static", "dlcentral")

_EXT_FOTO = (".jpg", ".jpeg", ".png", ".webp")
_EXT_VIDEO = (".mp4",)


# ------------------------------------------------------------------ meta (JSON no volume)
def _meta_all():
    try:
        with open(_META_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _meta_save(d):
    try:
        os.makedirs(os.path.dirname(_META_PATH) or ".", exist_ok=True)
        with open(_META_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[dlcentral] meta nao salvou: {e}")


def meta_get(marca, arquivo):
    return _meta_all().get(f"{marca}/{arquivo}", {})


def meta_set(marca, arquivo, **campos):
    d = _meta_all()
    k = f"{marca}/{arquivo}"
    d.setdefault(k, {}).update({c: v for c, v in campos.items() if v is not None})
    _meta_save(d)
    return d[k]


def log(msg):
    try:
        try:
            with open(_LOG_PATH, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
        hist.insert(0, {"quando": datetime.now().strftime("%d/%m %H:%M"), "msg": msg})
        os.makedirs(os.path.dirname(_LOG_PATH) or ".", exist_ok=True)
        with open(_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(hist[:30], f, ensure_ascii=False)
    except Exception:
        pass


def log_recente(n=10):
    try:
        with open(_LOG_PATH, encoding="utf-8") as f:
            return json.load(f)[:n]
    except Exception:
        return []


# ------------------------------------------------------------------ prateleira
def upload_dir(marca):
    p = os.path.join(_UP_BASE, marca)
    os.makedirs(p, exist_ok=True)
    return p


def repo_dir(marca):
    return os.path.join(_REPO_BASE, marca)


def _pastas(marca):
    return ((repo_dir(marca), "repo"), (upload_dir(marca), "upload"))


def listar(marca):
    itens = []
    for pasta, origem in _pastas(marca):
        try:
            for f in os.listdir(pasta):
                low = f.lower()
                if low.endswith(_EXT_FOTO):
                    tipo = "foto"
                elif low.endswith(_EXT_VIDEO):
                    tipo = "video"
                else:
                    continue
                caminho = os.path.join(pasta, f)
                itens.append({"arquivo": f, "tipo": tipo, "origem": origem,
                              "url": f"/dlmedia/{marca}/{f}",
                              "mtime": os.path.getmtime(caminho),
                              "meta": meta_get(marca, f)})
        except Exception:
            pass
    itens = [i for i in itens if not i["meta"].get("excluido")]
    itens.sort(key=lambda x: -x["mtime"])
    return itens


def acha(marca, arquivo):
    """(caminho_local, url_publica_absoluta, tipo) — upload tem prioridade sobre repo."""
    for pasta, _origem in reversed(_pastas(marca)):
        p = os.path.join(pasta, arquivo)
        if os.path.exists(p):
            tipo = "video" if arquivo.lower().endswith(_EXT_VIDEO) else "foto"
            return p, f"{PUBLIC_BASE}/dlmedia/{marca}/{arquivo}", tipo
    raise FileNotFoundError(arquivo)


def excluir(marca, arquivo):
    up = os.path.join(upload_dir(marca), arquivo)
    if os.path.exists(up):
        os.remove(up)
    meta_set(marca, arquivo, excluido=True)
    log(f"🗑️ excluído: {marca}/{arquivo}")
    return True


def salvar_upload(marca, filename, blob):
    """Salva upload. FOTO passa pelo compressor (alvo ≤ ~200 KB, máx 1350 px).
    Vídeo mp4 salva direto. Devolve o nome final."""
    nome = "".join(c for c in filename if c.isalnum() or c in "._- ")[:80].strip() or "arquivo"
    low = nome.lower()
    destino = upload_dir(marca)
    if low.endswith(_EXT_VIDEO):
        caminho = os.path.join(destino, nome)
        with open(caminho, "wb") as f:
            f.write(blob)
        log(f"⬆️ vídeo: {marca}/{nome} ({len(blob)//1024} KB)")
        return nome
    # foto → compressor
    from PIL import Image
    im = Image.open(io.BytesIO(blob))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((1350, 1350), Image.LANCZOS)
    base = nome.rsplit(".", 1)[0] or "foto"
    nome = base + ".jpg"
    caminho = os.path.join(destino, nome)
    q = 85
    while q >= 45:
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= 200 * 1024 or q == 45:
            with open(caminho, "wb") as f:
                f.write(buf.getvalue())
            break
        q -= 10
    log(f"⬆️ foto: {marca}/{nome} ({os.path.getsize(caminho)//1024} KB, q{q})")
    return nome


# ------------------------------------------------------------------ legenda de VENDA
# DL Defesas (06/10/26): a credencial DETRAN/SC nº 2095 é de Diogo Kauê Lessmann (Despachante Lessmann), não da marca DL Defesas. Lei 14.282/2021, art. 6º, IX: a publicidade
# leva a razão social e a inscrição no CRDD. O número vem da variável DEFESAS_CRDD do Railway (ex.: "CRDD/SC nº 1234"); sem ela, NADA publica na aba DL Defesas.
_DEFESAS_RAZAO = "Despachante Lessmann Schroeder Ltda"


def _crdd_defesas():
    return os.environ.get("DEFESAS_CRDD", "").strip()


def _rodape_defesas():
    return ("DL Defesas é um produto do %s · CNPJ 28.858.795/0001-92\n"
            "Despachante Lessmann - Diogo Kauê Lessmann · %s · Credencial DETRAN/SC nº 2095\n"
            "Defesa administrativa. Não somos escritório de advocacia. Quem decide é o órgão de trânsito."
            % (_DEFESAS_RAZAO, _crdd_defesas() or "CRDD/SC nº [PREENCHER]"))


def _sem_hashtags(txt):
    return "\n".join(l for l in txt.split("\n") if not (l.strip() and all(t.startswith("#") for t in l.split()))).rstrip()


def legenda_defesas_ok(legenda):
    """True só se a legenda traz a razão social e o CRDD (variável DEFESAS_CRDD). Conferida antes de publicar na aba DL Defesas."""
    crdd = _crdd_defesas()
    return bool(crdd) and crdd in (legenda or "") and _DEFESAS_RAZAO in (legenda or "")


def _fallback_venda(cfg, titulo, contexto, preco):
    tipo = cfg.get("tipo")
    if tipo == "defesa":
        # 06/10: sem balança, sem "continua dirigindo" solto (CTB, art. 285, § 1º: só no prazo e, em regra), sem "casos reais arquivados" (sem prova real e autorizada),
        # "conferência" gratuita (não "análise jurídica"). A frase de recurso só sai em post sobre recurso (A3). O rodapé e as hashtags são colados por gerar_legenda.
        linhas = [f"{titulo or 'Recebeu uma notificação de multa?'} — DL Defesas, um produto do Despachante Lessmann.", ""]
        if contexto:
            linhas += [contexto, ""]
        linhas += ["Conferência GRATUITA do prazo e dos dados da notificação."]
        if "recurso" in f"{titulo} {contexto}".lower():
            linhas += ["Recurso no prazo: em regra, suspende a penalidade enquanto é julgado (CTB, art. 285). Fora do prazo, não."]
        linhas += ["Conversa reservada, com sigilo profissional.", "",
                   f"Envie a FOTO da notificação pelo WhatsApp {cfg['telefone']}"]
        return "\n".join(linhas)
    if tipo == "servicos":
        linhas = [f"🏛️ {titulo or 'Documentação veicular'} — Despachante Lessmann, Schroeder!", ""]
        if contexto:
            linhas += [contexto, ""]
        linhas += ["✅ Veículo 0km: documento em até 2 horas",
                   "✅ Transferência pronta em até 1 dia útil",
                   "✅ Tudo pelo WhatsApp, sem fila de DETRAN"]
        if preco:
            linhas.insert(2, f"💰 {preco}")
        linhas += ["", f"📍 {cfg['endereco']}", f"📲 WhatsApp {cfg['telefone']}", "",
                   cfg["disclaimer"], "", cfg["hashtags"]]
        return "\n".join(linhas)
    if tipo == "motor":
        linhas = [f"🏪 {titulo or 'Tua loja com um site que chama no teu WhatsApp'}", ""]
        if contexto:
            linhas += [contexto, ""]
        linhas += ["✅ Um site com os teus produtos e preços, feito pro Google te achar",
                   "✅ Cada botão abre o TEU WhatsApp com a mensagem pronta",
                   "✅ A gente faz e posta no teu Instagram por ti",
                   "✅ Toda sexta, um número: quantas pessoas clicaram pra te chamar"]
        if preco:
            linhas.insert(2, f"💰 {preco}")
        linhas += ["", f"📲 WhatsApp {cfg['telefone']}", "", cfg["disclaimer"], "", cfg["hashtags"]]
        return "\n".join(linhas)
    linhas = [f"🛵 {titulo or 'Scooter elétrica'} na DL Mobilidade — Schroeder!", ""]
    if contexto:
        linhas += [contexto, ""]
    linhas += ["✅ Sem CNH e sem emplacamento (CONTRAN 996)",
               "✅ Zero gasolina — recarrega na tomada de casa",
               "💳 Até 48x ViaCredi · parcelas a partir de R$ 200*"]
    if preco:
        linhas.insert(2, f"💰 {preco}")
    linhas += ["", "🏁 TEST-RIDE GRÁTIS: vem dar uma volta antes de decidir!",
               f"📍 {cfg['endereco']}", f"📲 WhatsApp {cfg['telefone']}", "",
               cfg["disclaimer"], "", cfg["hashtags"]]
    return "\n".join(linhas)


def _gemini(prompt):
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    modelo = os.environ.get("DLCENTRAL_MODEL", "gemini-2.5-flash")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}"
           f":generateContent?key={key}")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        txt = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # meta-fala da IA não vai pro ar (lição de 12/ago da Rádio)
        baixa = txt.lower()
        if any(m in baixa for m in ("como modelo", "não posso", "houve um equívoco",
                                    "desculpe", "atenção:")):
            return None
        return txt or None
    except Exception as e:
        print(f"[dlcentral] gemini indisponível: {e}")
        return None


def gerar_legenda(marca, arquivo):
    cfg = MARCAS[marca]
    m = meta_get(marca, arquivo)
    titulo = m.get("titulo") or arquivo.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()
    contexto = m.get("contexto") or ""
    preco = m.get("preco") or ""
    if cfg["tipo"] == "defesa":
        fatos = ("FATOS: DL Defesas, um produto do Despachante Lessmann, em Schroeder/SC; defesa ADMINISTRATIVA de multa e CNH; NÃO somos escritório de advocacia "
                 "e não somos advogados; recurso apresentado no prazo, em regra, suspende a penalidade enquanto é julgado (CTB, art. 285), com exceções (por exemplo, "
                 "álcool); recurso fora do prazo não suspende; pontos: dizer 'dentro de um período de 12 meses', nunca 'últimos 12 meses' nem a data em que um ponto "
                 "'sai'; curso preventivo de reciclagem em SC: só CNH C, D ou E com EAR, de 30 a 39 pontos e prontuário de SC; desconto de 40%: só com adesão prévia ao "
                 "SNE e renúncia à defesa e ao recurso; conferência GRATUITA do prazo e dos dados da notificação pelo WhatsApp; conversa reservada, com sigilo "
                 "profissional; atendimento pelo WhatsApp. PROIBIDO: prometer ou sugerir resultado, 'garantido', '100%', 'cancelamos', 'revertemos', 'análise gratuita' "
                 "(dizer 'conferência gratuita'), 'continua dirigindo' sem 'em regra', 'casos reais arquivados' ou qualquer prova que não esteja nos FATOS, preço de "
                 "suspensão ou de cassação, 'acompanhamos o processo', 'não pedimos senha', prazo de entrega, 'Lei Seca' como chamariz, soar como advogado (advogado, "
                 "OAB, jurídico, defesa técnica, assessoria, consultoria, parecer), a palavra 'zap', balança ou martelo, medo como isca, emojis. "
                 "NÃO escreva rodapé, razão social, credencial, CRDD, endereço nem hashtags: o sistema acrescenta. "
                 "Nunca diga que a DL Defesas é 'despachante credenciado' sozinha: a credencial é do Despachante Lessmann.")
    elif cfg["tipo"] == "servicos":
        fatos = ("FATOS: despachante credenciado DETRAN/SC nº 2095, Schroeder; 0km com "
                 "documento em até 2 horas; transferência em até 1 dia útil; IPVA em 3x "
                 "direto ou débitos em até 24x no cartão; tudo pelo WhatsApp. Para "
                 "documentos, citar só Schroeder.")
    elif cfg["tipo"] == "motor":
        fatos = ("FATOS: VITRINE (4kitem, Schroeder/SC) = site da loja no domínio dela, com catálogo de "
                 "foto e preço; cada botão abre o WhatsApp DA LOJA com a mensagem pronta ('Oi, vim do site. "
                 "Quero: cód. … Tem na loja?'); o lojista muda preço e marca 'acabou' pelo celular; a gente "
                 "faz e posta no Instagram dele; toda sexta ele recebe quantas pessoas clicaram no botão "
                 "(CLIQUES, não vendas); quem atende é a loja — sem robô, sem carrinho. Público: dono de loja "
                 "do Vale do Itapocu. PROIBIDO: prometer vendas ou alcance, 'post todo dia', a palavra 'zap' "
                 "(sempre WhatsApp), falar da Rádio, citar preço se não vier no PREÇO.")
        _legado_radio = ("FATOS antigos (Rádio vendida, NÃO usar): Rádio SC News, portal e Instagram hiperlocal do Norte de SC (Schroeder, "
                 "Jaraguá do Sul, Guaramirim, Corupá), 1,2 milhão de views/mês, 456 mil contas "
                 "alcançadas em 30 dias. VENDE 3 COISAS: (1) Plano Vitrine R$97/mês — o lojista "
                 "manda a foto e uma frase pelo WhatsApp, a Rádio escreve o post, ele aprova, sai "
                 "1 post + 1 story por semana com cupom e relatório toda sexta (cupons usados, "
                 "cliques no WhatsApp); (2) MOTOR DE CONTEÚDO alugado — publica no Instagram e no site "
                 "do cliente todo dia, com aprovação dele, 'não substitui quem escreve, "
                 "multiplica'; (3) SITE DE 1 PÁGINA no domínio do cliente, que o Google e as IAs "
                 "acham, com WhatsApp pré-preenchido. Sempre: nota fiscal, sem contrato, cancela "
                 "quando quiser, 'sem cupom usado em 30 dias, cancela'. Público: dono de loja, "
                 "pizzaria, salão, oficina, escritório. Fale a dor dele: postar no status pra 50 "
                 "pessoas, sempre as mesmas. PROIBIDO: prometer alcance ou vendas, 'post "
                 "automático' (dizer 'a gente posta por você'), citar preço se não vier no PREÇO.")
    else:
        fatos = ("FATOS: scooters elétricas NXT; sem CNH e sem emplacamento (CONTRAN 996); "
                 "zero gasolina; até 48x ViaCredi; parcelas a partir de R$ 200 (com "
                 "asterisco de análise de crédito); test-ride grátis. PROIBIDO 'boleto'.")
    prompt = (
        "Você escreve legendas de Instagram que VENDEM. Escreva UMA legenda pronta (sem "
        "opções, sem comentários) sobre a mídia abaixo. Método: EMOÇÃO ABRE (a cena da "
        "vida melhor), RAZÃO FECHA (números e condições reais). 6-10 linhas curtas com "
        "emojis com gosto. TERMINE com endereço + WhatsApp + hashtags. PROIBIDO: preço "
        "inventado, promessa falsa, meta-comentário. Sua resposta vai DIRETO pro ar.\n\n"
        f"PRODUTO/CENA: {titulo}\nCONTEXTO DO DONO: {contexto or '(nenhum)'}\n"
        f"PREÇO: {preco or '(não citar valor)'}\n"
        f"CASA: {cfg['label']}, {cfg['endereco']} — WhatsApp {cfg['telefone']}\n"
        f"{fatos}\nHASHTAGS: {cfg['hashtags']}")
    venda = _gemini(prompt) or _fallback_venda(cfg, titulo, contexto, preco)
    if cfg["tipo"] == "defesa":
        # 06/10 (A3): rodapé (razão social, CNPJ, credencial, CRDD) e as 5 hashtags vão no fim de TODA legenda (IA ou fallback), colados pelo código, nunca pedidos ao modelo
        venda = _sem_hashtags(venda) + "\n\n" + _rodape_defesas() + "\n\n" + cfg["hashtags"]
    meta_set(marca, arquivo, legenda_venda=venda)
    return venda


# ------------------------------------------------------------------ publicação (só IG Despachante)
# Destinos da publicação. 24/set/26: a Rádio SC News foi VENDIDA ao Gabriel (paga à vista) —
# o destino "radio" saiu de vez. O 4kitem publica só nos perfis do dono.
DESTINOS = {
    "despachante": {"label": "IG do Despachante", "env": ("DESP_PAGE_TOKEN", "DESP_IG_USER_ID")},
    # 19/set/26: 3º perfil — Roda Norte (@rodanorte.sc, carro/moto/scooter do Norte de SC)
    "mobilidade": {"label": "IG Roda Norte", "env": ("MOB_PAGE_TOKEN", "MOB_IG_USER_ID")},
    # 29/set/26: a loja de scooters tem IG próprio — a aba "Scooters" publica lá, não no Despachante
    "dl_mobilidade": {"label": "IG da DL Mobilidade", "env": ("DLMOB_PAGE_TOKEN", "DLMOB_IG_USER_ID")},
    # 06/10/26: a aba "DL Defesas" publica no IG PRÓPRIO (@dldefesas.multas), nunca no do Despachante. Sem os 2 tokens, a tela avisa e não publica.
    # Railway (4 variáveis): DEFESAS_PAGE_TOKEN, DEFESAS_IG_USER_ID, DEFESAS_PAGE_ID e DEFESAS_CRDD (ex.: "CRDD/SC nº 1234").
    "defesas": {"label": "IG da DL Defesas", "env": ("DEFESAS_PAGE_TOKEN", "DEFESAS_IG_USER_ID")},
}


def _tokens(destino="despachante"):
    if destino not in DESTINOS:
        return "", ""
    e_tok, e_ig = DESTINOS[destino]["env"]
    tok = os.environ.get(e_tok, "")
    ig = os.environ.get(e_ig, "")
    return tok, ig


def tokens_ok(destino="despachante"):
    tok, ig = _tokens(destino)
    return bool(tok and ig)


def _graph_post(url, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise RuntimeError(out["error"].get("message", str(out["error"])))
    return out


def _graph_get(url, params):
    with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=60) as r:
        return json.loads(r.read())


def _publicar_job(marca, arquivo, legenda, destino="despachante"):
    # 24/set: destino desconhecido NAO cai mais no despachante em silencio — antes, uma chamada
    # antiga com destino="radio" publicaria conteudo da Radio no Instagram do despachante.
    if destino not in DESTINOS:
        log(f"⛔ destino '{destino}' não existe mais — nada publicado ({marca}/{arquivo})")
        return
    alvo = DESTINOS[destino]
    if destino == "defesas" and not legenda_defesas_ok(legenda):
        log(f"⛔ {marca}/{arquivo}: a legenda precisa da razão social e do CRDD (variável DEFESAS_CRDD no Railway; Lei 14.282/2021, art. 6º, IX) — nada publicado")
        return
    log(f"⏳ publicando {marca}/{arquivo} no {alvo['label']}…")
    try:
        tok, ig = _tokens(destino)
        if not (tok and ig):
            raise RuntimeError("Tokens ausentes: colar %s e %s no Railway do 4kitem."
                               % alvo["env"])
        _caminho, url, tipo = acha(marca, arquivo)
        if tipo == "foto":
            cont = _graph_post(f"{GRAPH}/{ig}/media",
                               {"image_url": url, "caption": legenda,
                                "access_token": tok})["id"]
            time.sleep(4)
        else:
            cont = _graph_post(f"{GRAPH}/{ig}/media",
                               {"media_type": "REELS", "video_url": url,
                                "caption": legenda, "access_token": tok})["id"]
            # reel processa assíncrono: espera FINISHED (até ~6 min)
            for _ in range(36):
                time.sleep(10)
                st = _graph_get(f"{GRAPH}/{cont}",
                                {"fields": "status_code", "access_token": tok})
                sc = st.get("status_code")
                if sc == "FINISHED":
                    break
                if sc == "ERROR":
                    raise RuntimeError("Instagram recusou o vídeo (status ERROR).")
        r = _graph_post(f"{GRAPH}/{ig}/media_publish",
                        {"creation_id": cont, "access_token": tok})
        pubs = meta_get(marca, arquivo).get("publicados", [])
        pubs.append({"quando": datetime.now().strftime("%d/%m %H:%M"),
                     "id": r.get("id"), "destino": destino})
        meta_set(marca, arquivo, publicados=pubs)
        log(f"✅ publicado no {alvo['label']}: {marca}/{arquivo} (id {r.get('id')})")
    except Exception as e:
        log(f"❌ falhou {marca}/{arquivo} ({alvo['label']}): {e}")


def publicar(marca, arquivo, legenda, destino="despachante"):
    threading.Thread(target=_publicar_job, args=(marca, arquivo, legenda, destino),
                     daemon=True).start()
    return True
