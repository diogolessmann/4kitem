# -*- coding: utf-8 -*-
"""
insights.py — Loop de Insights (Rádio SC News).
A peça que vira a FÁBRICA em VOLANTE: puxa o resultado REAL de cada post no Instagram
(alcance, salvamentos, compartilhamentos, interações) via Graph API — mesmo token, custo zero —
e guarda em post_insights. Cruzado no /admin/saude, mostra o que o Vale realmente compartilha.

Pré-requisito: o post tem que ter `news.ig_media_id` salvo (distribuidor.mark_media no post).

Uso: python insights.py            # puxa os posts dos últimos 3 dias
     insights.atualizar_recentes() # chamado pelo scheduler 1x/dia
"""
import os
import sqlite3
from datetime import datetime

import requests

from dlm import distribuidor as dist

DB_PATH = dist.DB_PATH
GRAPH = "https://graph.facebook.com/v21.0"

# métrica de mídia (post). saved+shares são o sinal de ouro de 2026.
POST_METRICS = "reach,saved,shares,likes,comments,total_interactions"
# ESCADA de fallback: se a Graph reclamar de algum metric (varia por tipo de post / versão),
# vai afinando até pegar pelo menos reach+saved. Degrada com elegância em vez de voltar 0.
# 'views' (Graph nova) / 'plays' (antiga) no topo: sem eles o Placar não separa Reels de carrossel.
_METRIC_LADDER = (
    "reach,saved,shares,likes,comments,total_interactions,views",
    "reach,saved,shares,likes,comments,total_interactions,plays",
    "reach,saved,shares,likes,comments,total_interactions",
    "reach,saved,shares,total_interactions",
    "reach,saved,total_interactions",
    "reach,saved",
    "reach",
)

_LAST_ERR = ""   # último erro da Graph (lido pelo diagnostico() — mostra POR QUE deu 0)


def _token():
    try:
        from dlm import distribuidor as dist
        return dist.META_PAGE_TOKEN, dist.META_IG_USER_ID
    except Exception:
        return os.environ.get("META_PAGE_TOKEN", ""), os.environ.get("META_IG_USER_ID", "")


def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS post_insights (
        news_id INTEGER PRIMARY KEY,
        reach INTEGER, saved INTEGER, shares INTEGER,
        likes INTEGER, comments INTEGER, interactions INTEGER, plays INTEGER,
        coletado_em TEXT
    )""")
    conn.commit()


def _fetch(media_id, metrics, token):
    """Devolve {metric: valor} ou None se a Graph recusar (ex: metric não vale p/ esse tipo).
    Guarda o erro detalhado da Meta em _LAST_ERR pro diagnóstico (permissão, metric inválido...)."""
    global _LAST_ERR
    try:
        r = requests.get(f"{GRAPH}/{media_id}/insights",
                         params={"metric": metrics, "access_token": token}, timeout=20)
        if not r.ok:
            _LAST_ERR = f"HTTP {r.status_code}: {r.text[:300]}"
            return None
        out = {}
        for m in r.json().get("data", []):
            vals = m.get("values") or [{}]
            out[m.get("name")] = vals[0].get("value")
        return out
    except Exception as e:
        _LAST_ERR = f"EXC: {e}"
        return None


def coletar_post(media_id, token=None):
    """Métricas de UM post. Desce a escada de metrics até pegar pelo menos reach+saved."""
    token = token or _token()[0]
    if not (media_id and token):
        return {}
    for metrics in _METRIC_LADDER:
        res = _fetch(media_id, metrics, token)
        if res:                       # pegou alguma métrica de verdade
            return res
    return {}


def _media_product_type(media_id, token):
    """'REELS' / 'FEED' / 'STORY'... do post. 'views' (Graph nova) existe pra QUALQUER mídia,
    então só gravamos plays quando o post é Reels de verdade — é o que separa formato no Placar."""
    try:
        r = requests.get(f"{GRAPH}/{media_id}",
                         params={"fields": "media_product_type", "access_token": token}, timeout=20)
        if r.ok:
            return (r.json().get("media_product_type") or "").upper()
    except Exception:
        pass
    return ""


def coletar_conta(token=None, ig_user_id=None):
    """Métricas da CONTA do dia: seguidores, alcance, visitas ao perfil."""
    tk, ig = _token()
    token = token or tk
    ig_user_id = ig_user_id or ig
    if not (token and ig_user_id):
        return {}
    global _LAST_ERR
    out = {}
    # cada grupo em chamada PRÓPRIA: 1 metric inválido derruba a chamada inteira na Graph
    # (profile_views nas versões novas exige metric_type=total_value) -> isolado, o resto sobrevive.
    for params in (
        {"metric": "follower_count,reach", "period": "day"},
        {"metric": "profile_views", "period": "day", "metric_type": "total_value"},
    ):
        try:
            r = requests.get(f"{GRAPH}/{ig_user_id}/insights",
                             params={**params, "access_token": token}, timeout=20)
            if not r.ok:
                _LAST_ERR = f"HTTP {r.status_code}: {r.text[:300]}"
                continue
            for m in r.json().get("data", []):
                vals = m.get("values") or [{}]
                v = vals[-1].get("value")                       # formato period=day
                if v is None:                                   # formato metric_type=total_value
                    v = (m.get("total_value") or {}).get("value")
                out[m.get("name")] = v
        except Exception as e:
            _LAST_ERR = f"EXC: {e}"
    return out


def _conta_basica(token, ig_user_id):
    """Campos BÁSICOS da conta (followers_count/follows_count/media_count). Só precisa de
    instagram_basic — funciona MESMO sem a permissão de insights (que é a que costuma travar).
    É o número que mais importa pra conversão: SEGUIDORES por dia."""
    try:
        r = requests.get(f"{GRAPH}/{ig_user_id}",
                         params={"fields": "followers_count,follows_count,media_count",
                                 "access_token": token}, timeout=20)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {}


def _ensure_conta_dia(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS conta_dia (
        dia TEXT PRIMARY KEY,
        followers INTEGER, follows INTEGER, media_count INTEGER,
        reach INTEGER, profile_views INTEGER,
        coletado_em TEXT
    )""")
    conn.commit()


def snapshot_conta(dia=None):
    """Grava 1 linha por DIA em conta_dia: seguidores (básico, sempre pega) + alcance/visitas
    (insights, best-effort — vem vazio se faltar a permissão). É a SÉRIE TEMPORAL que faltava:
    sem ela, todo kaizen de conversão (view->seguidor) é achismo. Idempotente por dia (UPSERT)."""
    token, ig = _token()
    if not (token and ig):
        return {}
    dia = dia or datetime.now().strftime("%Y-%m-%d")
    basica = _conta_basica(token, ig)      # followers_count etc (basic — funciona sem insights)
    ins = coletar_conta(token, ig)         # reach, profile_views (precisa insights; pode vir {})
    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_conta_dia(conn)
        conn.execute(
            """INSERT INTO conta_dia (dia, followers, follows, media_count, reach, profile_views, coletado_em)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(dia) DO UPDATE SET
                 followers=COALESCE(excluded.followers, conta_dia.followers),
                 follows=COALESCE(excluded.follows, conta_dia.follows),
                 media_count=COALESCE(excluded.media_count, conta_dia.media_count),
                 reach=COALESCE(excluded.reach, conta_dia.reach),
                 profile_views=COALESCE(excluded.profile_views, conta_dia.profile_views),
                 coletado_em=excluded.coletado_em""",
            (dia, basica.get("followers_count"), basica.get("follows_count"), basica.get("media_count"),
             ins.get("reach"), ins.get("profile_views"),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()
    return {"dia": dia, "followers": basica.get("followers_count"),
            "reach": ins.get("reach"), "profile_views": ins.get("profile_views")}


def serie_conta(dias=30):
    """Últimos N dias de conta_dia (p/ ver a curva de seguidores/alcance no /admin)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_conta_dia(conn)
        rows = conn.execute("SELECT * FROM conta_dia ORDER BY dia DESC LIMIT ?", (dias,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def diagnostico():
    """POR QUE o Placar está em 0? Distingue as 2 causas possíveis:
      - ig_media_id não salvo  -> 'marcados_3d':0  (a publicação não guardou o ID)
      - coleta falhando        -> 'marcados_3d'>0 mas 'amostra_metricas' vazio + 'amostra_erro'
                                   mostra o erro da Meta (ex: falta permissão instagram_manage_insights)
    """
    global _LAST_ERR
    out = {"db": DB_PATH}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        def _c(sql):
            return conn.execute(sql).fetchone()[0]
        out["postados_30d"] = _c("SELECT COUNT(*) FROM news WHERE social_posted_at>datetime('now','-30 days')")
        out["marcados_30d"] = _c("SELECT COUNT(*) FROM news WHERE ig_media_id IS NOT NULL AND ig_media_id!='' AND social_posted_at>datetime('now','-30 days')")
        out["marcados_3d"] = _c("SELECT COUNT(*) FROM news WHERE ig_media_id IS NOT NULL AND ig_media_id!='' AND social_posted_at>datetime('now','-3 days')")
        row = conn.execute(
            "SELECT id, ig_media_id FROM news WHERE ig_media_id IS NOT NULL AND ig_media_id!='' "
            "ORDER BY social_posted_at DESC LIMIT 1").fetchone()
    except Exception as e:
        conn.close()
        return {"erro_db": str(e), "db": DB_PATH}
    conn.close()

    token, ig_user = _token()
    out["tem_token"] = bool(token)
    out["tem_ig_user_id"] = bool(ig_user)
    if row:
        _LAST_ERR = ""
        out["amostra_id"] = row["id"]
        out["amostra_media_id"] = row["ig_media_id"]
        m = coletar_post(row["ig_media_id"], token)
        out["amostra_metricas"] = m
        out["amostra_erro"] = "" if m else _LAST_ERR
        out["veredito"] = ("✅ coleta OK — pode ligar quando juntar volume" if m
                           else "🚩 COLETA FALHANDO — veja amostra_erro (provável falta de permissão instagram_manage_insights no token)")
    else:
        out["amostra_media_id"] = None
        out["veredito"] = ("🚩 NENHUM post tem ig_media_id salvo — a publicação não está guardando o ID "
                           "(autopost não passa por publish_real/mark_media?)")
    _LAST_ERR = ""
    out["conta"] = coletar_conta(token, ig_user)
    out["conta_erro"] = "" if out["conta"] else _LAST_ERR
    return out


def atualizar_recentes(dias=3):
    """Puxa e grava os insights dos posts com ig_media_id dos últimos N dias (UPSERT).
    Métrica amadurece com o tempo -> re-puxar os recentes todo dia mantém atualizado."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    try:
        rows = conn.execute(
            "SELECT id, ig_media_id FROM news "
            "WHERE ig_media_id IS NOT NULL AND ig_media_id!='' "
            "AND social_posted_at > datetime('now', ?)",
            (f"-{dias} days",),
        ).fetchall()
    except Exception:
        conn.close()
        return 0

    tk = _token()[0]
    n = 0
    for r in rows:
        m = coletar_post(r["ig_media_id"], tk)
        if not m:
            continue
        # plays: 'views' (nova) > 'plays' (antiga) > legado. Só grava se o post for REELS —
        # 'views' de carrossel marcaria formato errado no Placar.
        plays = m.get("views") or m.get("plays") or m.get("ig_reels_video_view_total")
        if plays and _media_product_type(r["ig_media_id"], tk) != "REELS":
            plays = None
        conn.execute(
            """INSERT INTO post_insights
               (news_id, reach, saved, shares, likes, comments, interactions, plays, coletado_em)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(news_id) DO UPDATE SET
                 reach=excluded.reach, saved=excluded.saved, shares=excluded.shares,
                 likes=excluded.likes, comments=excluded.comments,
                 interactions=excluded.interactions, plays=excluded.plays,
                 coletado_em=excluded.coletado_em""",
            (r["id"], m.get("reach"), m.get("saved"), m.get("shares"),
             m.get("likes"), m.get("comments"), m.get("total_interactions"),
             plays,
             datetime.now().isoformat(timespec="seconds")),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


if __name__ == "__main__":
    print(f"Insights atualizados: {atualizar_recentes()} posts.")
    print("Conta:", coletar_conta())


# ═══════════════════════ MARCAS (despachante) — placar por SÉRIE (15/set/2026) ═══════════════════════
# O motor do despachante grava cada post em DATA_DIR/marcas_posts.jsonl (marcas._log_post).
# Aqui a gente puxa reach/saved/shares/likes/comments de cada ig_media_id com os tokens DESP_*
# e guarda em marca_insights → /admin mostra qual série o Vale realmente salva e compartilha.
import json as _json

def _marca_tokens(brand_key):
    from dlm import marcas
    t = marcas.BRANDS[brand_key]
    return marcas._brand_tokens(t)[:2]


def _ensure_marca(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS marca_insights (
        ig_media_id TEXT PRIMARY KEY, brand TEXT, slot TEXT, serie TEXT, passo INTEGER,
        titulo TEXT, link TEXT, postado_em TEXT,
        reach INTEGER, saved INTEGER, shares INTEGER, likes INTEGER, comments INTEGER,
        coletado_em TEXT)""")
    conn.commit()


def coletar_marca(brand_key="despachante", dias=7):
    token, ig_user_id = _marca_tokens(brand_key)
    if not token:
        return 0
    log = os.path.join(dist.DATA_DIR, "marcas_posts.jsonl")
    if not os.path.exists(log):
        return 0
    corte = (datetime.now() - __import__("datetime").timedelta(days=dias)).isoformat()
    n = 0
    conn = sqlite3.connect(DB_PATH); _ensure_marca(conn)
    for line in open(log, encoding="utf-8"):
        try:
            r = _json.loads(line)
        except Exception:
            continue
        if r.get("brand") != brand_key or not r.get("ig_media_id") or r.get("ts", "") < corte:
            continue
        m = _fetch(r["ig_media_id"], "reach,saved,shares,likes,comments", token) or {}
        if not m:
            continue
        conn.execute("""INSERT OR REPLACE INTO marca_insights VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (r["ig_media_id"], brand_key, r.get("slot"), r.get("serie"), r.get("passo"),
                      r.get("titulo"), r.get("link"), r.get("ts"),
                      m.get("reach"), m.get("saved"), m.get("shares"), m.get("likes"), m.get("comments"),
                      datetime.now().isoformat(timespec="minutes")))
        n += 1
    conn.commit(); conn.close()
    return n


def resumo_marca(brand_key="despachante", dias=30):
    """[(serie, posts, reach_medio, saved_medio, shares_medio)] ordenado por alcance médio."""
    conn = sqlite3.connect(DB_PATH); _ensure_marca(conn)
    corte = (datetime.now() - __import__("datetime").timedelta(days=dias)).isoformat()
    rows = conn.execute("""SELECT COALESCE(serie,'(sem série)'), COUNT(*), AVG(reach), AVG(saved), AVG(shares)
                           FROM marca_insights WHERE brand=? AND postado_em>=?
                           GROUP BY serie ORDER BY AVG(reach) DESC""", (brand_key, corte)).fetchall()
    conn.close()
    return rows
