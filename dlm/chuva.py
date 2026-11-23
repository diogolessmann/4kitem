# -*- coding: utf-8 -*-
"""🌧️ CHUVA DE INFORMAÇÃO (13/11/2026): as linhas de pauta que INFORMAM, não só vendem.

O dono, 13/11: "quero vários materiais: especificação, consumo, uso, economia, novidade, dia a dia, regras,
leis, macetes, dicas. Uma verdadeira chuva de informação, deixar de apenas vender para informar também."
No Despachante: licenciamento, calendário, transferência, assinatura digital, leis, regras, blitz, dívida
ativa, restrições, etiquetas, vistoria ECV, "tudo relacionado ao despachante".

Banco: dlm/chuva/<marca>.json. Cada pauta traz fonte, url, trecho literal, conferido_em, validade e status
(montado e criticado em 13/11: PROJETOS/MOTOR_AFIADO_13nov/CHUVA). Sai sozinho só o que está:
  - status "pronta" (o crítico aprovou);
  - validade "permanente" ou "ano AAAA" (só até 31/12 daquele ano); "conferir no dia" (valor, taxa ou prazo
    que muda) NUNCA sai sozinho;
  - dentro do prazo do campo "ate" (AAAA-MM-DD), quando a pauta fala de uma data.
Grade (18/11, "menos é mais": 1 post de feed por dia em cada perfil; informar fica em ~2/3): a chuva sai nos
5 dias sem venda, Despachante às 19h e DL Mobilidade às 19h15 (dlmotor.agenda e DIAS_VENDA_*).
Ordem: uma linha diferente a cada post (a usada há mais tempo primeiro); dentro dela, a menos publicada.
Arte: carrossel de 3 slides (capa com foto REAL, conteúdo, fecho) e legenda com a FONTE citada. Na DL a foto
ocupa a capa inteira (marcas.slide_capa_foto); a capa não repete as últimas FOTOS_LEMBRADAS fotos da marca.
Desliga com CHUVA_ON=0 no Railway.
"""
import glob
import hashlib
import json
import os
import re
from datetime import datetime

_AQUI = os.path.dirname(os.path.abspath(__file__))
BANCO_DIR = os.path.join(_AQUI, "chuva")
_DATA = os.environ.get("DATA_DIR", os.path.dirname(_AQUI))
MARCAS = ("despachante", "dl_mobilidade")

# linha do Despachante → pool de fotos REAIS que as séries já usam (assets/series/<pool>)
POOL_DESP = {"licenciamento": "licenciamento", "calendario_licenciamento": "licenciamento",
             "ipva": "ipva", "divida_ativa": "ipva", "debitos_2via": "trocar_carro",
             "placa_mercosul": "trocar_carro", "transferencia": "trocar_carro",
             "assinatura_digital": "trocar_carro", "vistoria_ecv": "trocar_carro",
             "etiquetas_gravacao": "trocar_carro", "restricoes": "trocar_carro",
             "restricao_administrativa": "trocar_carro", "outros_servicos": "trocar_carro",
             "leis": "multa", "regras": "multa", "blitz": "multa", "cnh_digital": "cnh",
             "multas_recursos": "multa"}
ROTULO = {"dica": "A DICA", "mito": "A VERDADE", "consequencia": "O QUE ACONTECE", "passo": "COMO FUNCIONA",
          "numero": "OS NÚMEROS", "checklist": "CONFERE AÍ"}


def ligado():
    return os.environ.get("CHUVA_ON", "1") == "1"


def _estado_path():
    return os.path.join(os.environ.get("DATA_DIR", _DATA), "chuva_estado.json")


def _estado():
    try:
        with open(_estado_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _grava(est):
    p = _estado_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def banco(marca):
    try:
        with open(os.path.join(BANCO_DIR, f"{marca}.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[chuva] banco {marca} ilegível: {e}")
        return []


def vale(item, hoje=None):
    """A pauta pode sair SOZINHA neste dia?"""
    hoje = hoje or datetime.now().date()
    if item.get("status") != "pronta":
        return False
    val = (item.get("validade") or "").lower()
    if "conferir" in val:
        return False
    m = re.search(r"ano (\d{4})", val)
    if m and hoje.year > int(m.group(1)):
        return False
    if item.get("ate"):
        try:
            if hoje > datetime.strptime(item["ate"], "%Y-%m-%d").date():
                return False
        except ValueError:
            return False
    return True


def escolher(marca, hoje=None, est=None):
    est = _estado() if est is None else est
    e = est.get(marca, {})
    pubs = e.get("publicados", {})
    usada = e.get("linha_usada", {})
    barr = set(e.get("barradas", []))
    lista = banco(marca)
    ordem = {i["id"]: n for n, i in enumerate(lista)}
    ok = [i for i in lista if vale(i, hoje) and i["id"] not in barr]
    if not ok:
        return None
    menor = min(len(pubs.get(i["id"], [])) for i in ok)
    cand = [i for i in ok if len(pubs.get(i["id"], [])) == menor]
    return min(cand, key=lambda i: (usada.get(i["linha"], ""), ordem[i["id"]]))


# 18/11 (pente fino do Instagram, 5 dias no ar): a mesma foto saiu 2 vezes em 3 dias na DL e a cena do
# "caderno com carimbo" 4 vezes em 15 posts no Despachante. A capa agora lembra as últimas fotos (pelo
# CONTEÚDO, não pelo nome: a mesma foto mora na Central e no repositório com nomes diferentes).
FOTOS_LEMBRADAS = 12
ULTIMA_FOTO = {}          # marca → impressão digital da capa que o montar() acabou de fazer (o postar() grava)
# arte, logo e story já têm texto; foto de entrega é para o post de entrega, não para capa de aviso ou de regra
_SO_VENDA = re.compile(r"arte|promo|logo|story|entt?rega", re.I)
# cenas do Despachante: sem foto nova na pasta da linha, vale a de uma pasta irmã (nunca a de scooter)
_FAMILIA = (("trocar_carro", "licenciamento", "ipva", "negocio"), ("multa", "cnh"))
# Foto limpa do produto para a pauta que tem o modelo no título. São fotos do repositório, que não levam o
# nome do modelo; conferidas no olho em 18/11 contra o mostruário: a Akasha é a das faixas de LED, a Kay é a
# vermelha com baú e o X13 é o azul com a bandeira. Sem isto, "Conheça a Akasha" saía com a capa preta.
FOTOS_MODELO = {"akasha": ("dl_34.jpg", "dl_35.jpg", "dl_36.jpg"),
                "kay": ("zap_2026-09-13_211412_10.jpg", "zap_2026-09-13_211412_11.jpg",
                        "zap_2026-09-13_211412_12.jpg", "zap_2026-09-13_211412_13.jpg"),
                "x13": ("zap_2026-09-13_211741.jpg",)}


def _ordem(chave, pool):
    """A mesma pauta dá sempre a mesma ordem; cada pauta dá uma ordem diferente."""
    return sorted(pool, key=lambda p: hashlib.md5(f"{chave}|{os.path.basename(p)}".encode("utf-8")).hexdigest())


def _nova(chave, pool, recentes):
    """(caminho, digital) da primeira foto da ordem que não repete uma capa recente; (None, None) se todas repetem."""
    from dlm import dl_material
    for p in _ordem(chave, pool):
        d = dl_material.digital(p)
        if d is not None and not any(dl_material.gemeas(d, r) for r in recentes):
            return p, d
    return None, None


def _pasta(nome):
    from dlm import marcas
    d = os.path.join(marcas.SERIES_IMG_DIR, nome)
    return sorted(glob.glob(os.path.join(d, "*.jp*g")) + glob.glob(os.path.join(d, "*.webp")))


def _pools(marca, item):
    """Listas de fotos em ordem de preferência. A pauta que tem o modelo no TÍTULO só sai com foto daquele modelo."""
    from dlm import marcas
    if marca != "dl_mobilidade":
        propria = POOL_DESP.get(item.get("linha"), "trocar_carro")
        irmas = next((f for f in _FAMILIA if propria in f), ())
        # 23/11: a imagem que MOSTRA o assunto da pauta (marcas.fotos_do_tema). Tendo imagem do assunto, a capa
        # nunca cai na pasta antiga (fachada, caderno): se todas já saíram, repete a que saiu há mais tempo.
        tema = marcas.fotos_do_tema(item)
        if tema:
            return [tema]
        return [_pasta(propria), [p for n in irmas if n != propria for p in _pasta(n)]]
    from dlm import dl_material
    try:
        central = dl_material.fotos_central()
    except Exception:
        central = []
    geral = [p for p in sorted(glob.glob(os.path.join(marcas.DL_PHOTOS_DIR, "*.jpg"))) + central
             if dl_material.foto_ok(os.path.basename(p)) and not _SO_VENDA.search(os.path.basename(p))
             and not dl_material.retirada(p, dl_material._FORA_CAPA)]
    modelo = (item.get("foto_modelo") or "").strip()
    if not modelo:
        return [geral]
    alvo = dl_material.modelo_de(modelo)
    chave = re.sub(r"[^a-z0-9]", "", modelo.lower())
    do_modelo = [p for p in central
                 if (alvo and dl_material.modelo_de(os.path.basename(p)) == alvo)
                 or (chave and chave in re.sub(r"[^a-z0-9]", "", os.path.basename(p).lower()))]
    # o modelo só no exemplo (ex.: a conta de energia usa a Akasha): sem foto dele, vale a foto geral da loja.
    # 13/11 esta pauta saiu com a capa preta, sem foto nenhuma.
    limpas = [os.path.join(marcas.DL_PHOTOS_DIR, n) for k, ns in FOTOS_MODELO.items() if k in chave for n in ns]
    limpas = [p for p in limpas if os.path.exists(p)]
    no_titulo = chave and chave in re.sub(r"[^a-z0-9]", "", (item.get("titulo") or "").lower())
    # pauta de ciclomotor (X8, XE-30) nunca leva a foto geral: debaixo de "exige CNH e placa" ela mostraria
    # uma scooter que NÃO exige. Sem foto do próprio modelo, card liso.
    if no_titulo or dl_material.CICLOMOTOR.search(modelo):
        return [limpas, do_modelo]
    return [limpas, do_modelo, geral]


def foto(marca, item, recentes=None):
    """Foto REAL do fundo da capa (None = card liso). Não repete as últimas FOTOS_LEMBRADAS capas da marca."""
    from dlm import dl_material
    if recentes is None:
        recentes = _estado().get(marca, {}).get("fotos_recentes", [])
    recentes = [int(r, 16) for r in recentes if isinstance(r, str)]
    pools = _pools(marca, item)
    for pool in pools:
        p, d = _nova(item["id"], pool, recentes)
        if p:
            ULTIMA_FOTO[marca] = d
            return p
    # todas repetem (banco de fotos pequeno): sai a que apareceu há mais tempo, que ainda é melhor que card liso
    def _vez(p):
        d = dl_material.digital(p)
        return max([k for k, r in enumerate(recentes) if d is not None and dl_material.gemeas(d, r)], default=-1)

    for pool in pools:
        if pool:
            p = min(_ordem(item["id"], pool), key=_vez)
            ULTIMA_FOTO[marca] = dl_material.digital(p)
            return p
    ULTIMA_FOTO[marca] = None
    return None


def montar(marca, item, outdir):
    """Slides + legenda. Levanta vacina.Bloqueado se a pauta violar a régua (o turno pula para a próxima)."""
    from dlm import marcas, vacina
    t = marcas.BRANDS[marca]
    dl = marca == "dl_mobilidade"
    info = item.get("tipo") != "vender"
    it = dict(item)
    it["foto_path"] = foto(marca, item)
    it["rotulo_conteudo"] = ROTULO.get(item.get("formato"), "COMO FUNCIONA")
    if item.get("formato") == "numero" and not re.search(r"\d", " ".join(item.get("bullets", []))):
        it["rotulo_conteudo"] = "COMO FUNCIONA"      # 18/11: "OS NÚMEROS" saiu em pauta sem número nenhum
    it["cta_seal"] = "SALVA PRA LEMBRAR" if info else ("VEM DAR UMA VOLTA" if dl else "MANDA A PLACA")
    it["cta_big"] = (["DÚVIDA?", "CHAMA A LOJA"] if dl else ["DÚVIDA?", "PERGUNTA PRA GENTE"]) if info \
        else (["TEST-RIDE", "SEM COMPROMISSO"] if dl else ["A GENTE FAZ", "POR VOCÊ"])
    vacina.exigir(f"chuva {item['id']}", {k: it.get(k) for k in ("titulo", "bullets", "cta_seal", "cta_big")},
                  extra={"brand": marca, "titulo": it.get("titulo")})
    os.makedirs(outdir, exist_ok=True)
    # 18/11: na DL a foto ocupa a capa inteira, como no card de oferta. Das 131 fotos da loja, 102 são em pé,
    # e a capa antiga só mostrava uma faixa deitada delas: cortava o topo da scooter e a cabeça de gente.
    capa = marcas.slide_capa_foto if (dl and it["foto_path"]) else marcas.slide_capa
    try:
        p1 = capa(t, it, outdir, 1)
    except Exception as ex:                  # foto que não abre não derruba o post: sai a capa clássica
        print(f"[chuva] capa de foto inteira falhou ({ex}); vai a capa clássica")
        p1 = marcas.slide_capa(t, it, outdir, 1)
    paths = [p1, marcas.slide_conteudo(t, it, outdir, 2), marcas.slide_cta(t, outdir, 3, item=it)]
    base = {k: v for k, v in it.items() if k not in ("serie", "link")}
    cap = marcas.build_caption(t, base)
    cap = cap.replace("\n\n🌐", f"\n\n{it.get('fecho', '')}\n📚 Fonte: {it['fonte']}\n\n🌐", 1)
    if dl:                                   # pauta de ciclomotor não leva #semcnh (crítica 13/11)
        from dlm import dl_material
        texto = " ".join([it.get("titulo", "")] + list(it.get("bullets", [])))
        if dl_material.CICLOMOTOR.search(texto) or re.search(r"ciclomotor", texto, re.I):
            cap = re.sub(r" ?#semcnh\b", "", cap)
    v = vacina.checar(cap)
    if v:
        raise vacina.Bloqueado(f"legenda da chuva {item['id']}: {v[0][0]}")
    if dl and marcas._dl_viola(cap):
        raise vacina.Bloqueado(f"legenda da chuva {item['id']}: {marcas._dl_viola(cap)[0]}")
    if t.get("rodape_legal") and "CRDD" not in cap:
        raise vacina.Bloqueado(f"legenda da chuva {item['id']} sem o rodapé legal (Lei 14.282, art. 6º, IX)")
    with open(os.path.join(outdir, "legenda.txt"), "w", encoding="utf-8") as f:
        f.write(cap)
    return paths, cap


def postar(marca, ao_vivo=False, hoje=None):
    """Um post da chuva. Devolve o texto do resultado do turno (o dlmotor guarda no estado)."""
    from dlm import marcas, vacina
    t = marcas.BRANDS[marca]
    token, ig_id, _ = marcas._brand_tokens(t)
    if not (token and ig_id):
        return f"sem tokens de {t['nome']}: pulado"
    for _tentativa in range(5):              # pauta barrada pela vacina não trava o turno: vai a próxima
        est = _estado()
        item = escolher(marca, hoje, est)
        if not item:
            return "banco da chuva vazio (pulado)"
        day = datetime.now().strftime("%Y-%m-%d_%H%M")
        outdir = os.path.join(marcas.OUT_BASE, f"{day}_chuva_{marca}")
        try:
            paths, cap = montar(marca, item, outdir)
        except vacina.Bloqueado as b:
            est.setdefault(marca, {}).setdefault("barradas", []).append(item["id"])
            _grava(est)
            print(f"[chuva] {item['id']} barrada: {b}")
            continue
        if not ao_vivo:
            return f"PREVIEW chuva {item['id']}: {paths[0]}"
        r = marcas.publish_brand(t, f"chuva_{marca}_{day}", paths, cap)
        # daqui pra baixo o post JÁ está no ar: nada pode virar "quebrou" (o motor repostaria)
        try:
            marcas._log_post(marca, "chuva", dict(item, serie="chuva_" + item["linha"], passo=item["id"]), r)
            est = _estado()                  # relê: outro turno pode ter gravado no meio
            e = est.setdefault(marca, {})
            e.setdefault("publicados", {}).setdefault(item["id"], []).append(
                datetime.now().isoformat(timespec="minutes"))
            e.setdefault("linha_usada", {})[item["linha"]] = datetime.now().isoformat(timespec="seconds")
            if ULTIMA_FOTO.get(marca) is not None:
                e["fotos_recentes"] = (e.get("fotos_recentes", []) + ["%016x" % ULTIMA_FOTO[marca]])[-FOTOS_LEMBRADAS:]
            _grava(est)
        except Exception as ex:
            print(f"[chuva] postou {item['id']}, mas o estado não gravou: {ex}")
        return f"POSTADO chuva {item['id']} ({item['linha']})"
    return "5 pautas barradas seguidas (ver log): pulado"
