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
Grade (informar fica em ~2/3 do feed): Despachante 13h e 19h, DL Mobilidade 11h e 19h15 (dlmotor.agenda).
Ordem: uma linha diferente a cada post (a usada há mais tempo primeiro); dentro dela, a menos publicada.
Arte: o mesmo carrossel das séries (capa com foto REAL, conteúdo, fecho) e legenda com a FONTE citada.
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


def _sorteio(chave, pool):
    return pool[int(hashlib.md5(chave.encode("utf-8")).hexdigest(), 16) % len(pool)] if pool else None


def foto(marca, item):
    """Foto REAL do fundo da capa. Pauta de um modelo só sai com foto daquele modelo; sem ela, card liso."""
    from dlm import marcas
    if marca == "dl_mobilidade":
        from dlm import dl_material
        try:
            central = dl_material.fotos_central()
        except Exception:
            central = []
        modelo = (item.get("foto_modelo") or "").strip()
        if modelo:
            alvo = dl_material.modelo_de(modelo)
            chave = re.sub(r"[^a-z0-9]", "", modelo.lower())
            pool = [p for p in central
                    if (alvo and dl_material.modelo_de(os.path.basename(p)) == alvo)
                    or (chave and chave in re.sub(r"[^a-z0-9]", "", os.path.basename(p).lower()))]
        else:
            pool = sorted(glob.glob(os.path.join(marcas.DL_PHOTOS_DIR, "*.jpg"))) + central
            # arte, logo e story já têm texto: debaixo da capa da chuva viram texto sobre texto
            pool = [p for p in pool if dl_material.foto_ok(os.path.basename(p))
                    and not re.search(r"arte|promo|logo|story", os.path.basename(p), re.I)]
        return _sorteio(item["id"], pool)
    d = os.path.join(marcas.SERIES_IMG_DIR, POOL_DESP.get(item.get("linha"), "trocar_carro"))
    return _sorteio(item["id"], sorted(glob.glob(os.path.join(d, "*.jp*g")) + glob.glob(os.path.join(d, "*.webp"))))


def montar(marca, item, outdir):
    """Slides + legenda. Levanta vacina.Bloqueado se a pauta violar a régua (o turno pula para a próxima)."""
    from dlm import marcas, vacina
    t = marcas.BRANDS[marca]
    dl = marca == "dl_mobilidade"
    info = item.get("tipo") != "vender"
    it = dict(item)
    it["foto_path"] = foto(marca, item)
    it["rotulo_conteudo"] = ROTULO.get(item.get("formato"), "COMO FUNCIONA")
    it["cta_seal"] = "SALVA PRA LEMBRAR" if info else ("VEM DAR UMA VOLTA" if dl else "MANDA A PLACA")
    it["cta_big"] = (["DÚVIDA?", "CHAMA A LOJA"] if dl else ["DÚVIDA?", "PERGUNTA PRA GENTE"]) if info \
        else (["TEST-RIDE", "SEM COMPROMISSO"] if dl else ["A GENTE FAZ", "POR VOCÊ"])
    vacina.exigir(f"chuva {item['id']}", {k: it.get(k) for k in ("titulo", "bullets", "cta_seal", "cta_big")},
                  extra={"brand": marca, "titulo": it.get("titulo")})
    os.makedirs(outdir, exist_ok=True)
    paths = [marcas.slide_capa(t, it, outdir, 1), marcas.slide_conteudo(t, it, outdir, 2),
             marcas.slide_cta(t, outdir, 3, item=it)]
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
            _grava(est)
        except Exception as ex:
            print(f"[chuva] postou {item['id']}, mas o estado não gravou: {ex}")
        return f"POSTADO chuva {item['id']} ({item['linha']})"
    return "5 pautas barradas seguidas (ver log): pulado"
