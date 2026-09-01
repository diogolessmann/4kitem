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
  método validado: emoção abre, razão fecha, CTA no zap.
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
        "label": "⚖️ DL Defesas",
        "tipo": "defesa",
        "telefone": "(47) 99716-2967",
        "endereco": "R. Mal. Castelo Branco, 2838, Sala 02 — Centro, Schroeder/SC",
        "hashtags": "#defesademulta #cnhsuspensa #recursodemulta #multa #Schroeder #DespachanteLessmann",
        "disclaimer": "Análise gratuita · atendimento digital em todo o Brasil",
    },
    "despachante": {
        "label": "🏛️ Despachante Lessmann",
        "tipo": "servicos",
        "telefone": "(47) 99716-2967",
        "endereco": "R. Mal. Castelo Branco, 2838, Sala 02 — Centro, Schroeder/SC",
        "hashtags": "#despachante #Schroeder #detransc #transferencia #licenciamento #DespachanteLessmann",
        "disclaimer": "Credencial DETRAN/SC nº 2095",
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
def _fallback_venda(cfg, titulo, contexto, preco):
    tipo = cfg.get("tipo")
    if tipo == "defesa":
        linhas = [f"⚖️ {titulo or 'Recebeu multa ou notificação?'} — DL Defesas!", ""]
        if contexto:
            linhas += [contexto, ""]
        linhas += ["✅ Análise GRATUITA da tua notificação",
                   "✅ Recurso com efeito suspensivo: você continua dirigindo",
                   "✅ Casos reais já arquivados · sigilo total", "",
                   f"📲 Manda a FOTO da notificação: WhatsApp {cfg['telefone']}", "",
                   cfg["hashtags"]]
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
        fatos = ("FATOS: DL Defesas do Despachante Lessmann (credencial DETRAN/SC 2095, "
                 "Schroeder); defesa de multa e CNH em todas as instâncias; recurso tem "
                 "efeito suspensivo — a pessoa CONTINUA dirigindo; análise GRATUITA pelo "
                 "WhatsApp; sigilo; atendimento digital para todo o Brasil. PROIBIDO "
                 "prometer resultado.")
    elif cfg["tipo"] == "servicos":
        fatos = ("FATOS: despachante credenciado DETRAN/SC nº 2095, Schroeder; 0km com "
                 "documento em até 2 horas; transferência em até 1 dia útil; IPVA em 3x "
                 "direto ou débitos em até 24x no cartão; tudo pelo WhatsApp. Para "
                 "documentos, citar só Schroeder.")
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
    meta_set(marca, arquivo, legenda_venda=venda)
    return venda


# ------------------------------------------------------------------ publicação (só IG Despachante)
def _tokens():
    tok = os.environ.get("DESP_PAGE_TOKEN", "")
    ig = os.environ.get("DESP_IG_USER_ID", "")
    return tok, ig


def tokens_ok():
    tok, ig = _tokens()
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


def _publicar_job(marca, arquivo, legenda):
    log(f"⏳ publicando {marca}/{arquivo} no IG do Despachante…")
    try:
        tok, ig = _tokens()
        if not (tok and ig):
            raise RuntimeError("Tokens ausentes: colar DESP_PAGE_TOKEN e "
                               "DESP_IG_USER_ID no Railway do 4kitem.")
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
                     "id": r.get("id")})
        meta_set(marca, arquivo, publicados=pubs)
        log(f"✅ publicado: {marca}/{arquivo} (id {r.get('id')})")
    except Exception as e:
        log(f"❌ falhou {marca}/{arquivo}: {e}")


def publicar(marca, arquivo, legenda):
    threading.Thread(target=_publicar_job, args=(marca, arquivo, legenda),
                     daemon=True).start()
    return True
