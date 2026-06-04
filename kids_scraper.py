"""
kids_scraper.py — SalaTV: busca automática de vídeos via YouTube RSS
Não precisa de API key. Usa feeds públicos do YouTube.

Fluxo:
  1. Para cada canal no DB sem channel_id → resolve handle → channel_id
  2. Busca RSS: youtube.com/feeds/videos.xml?channel_id=UC...
  3. Insere vídeos novos no DB (INSERT OR IGNORE)
"""
import re
import time
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

from kids_db import get_conn, update_channel_id, DB_PATH

log = logging.getLogger('kids_scraper')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
}

RSS_NS = {
    'atom':  'http://www.w3.org/2005/Atom',
    'yt':    'http://www.youtube.com/xml/schemas/2015',
    'media': 'http://search.yahoo.com/mrss/',
}

# ── Resolve handle → channel_id ────────────────────────────────────────────
def resolve_channel_id(handle: str) -> str | None:
    """
    Dado '@galinhapintadinha' devolve 'UCxxxxx'.
    Faz scraping da página do canal — sem API key.
    """
    if not _HAS_REQUESTS:
        log.warning("requests não instalado — instala com: pip install requests")
        return None

    clean = handle.lstrip('@')
    url   = f"https://www.youtube.com/@{clean}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        text = r.text

        # Tentativa 1: "channelId":"UC..."
        m = re.search(r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"', text)
        if m:
            return m.group(1)

        # Tentativa 2: /channel/UC... na URL canônica
        m2 = re.search(r'/channel/(UC[A-Za-z0-9_-]{20,})', text)
        if m2:
            return m2.group(1)

        # Tentativa 3: externalId
        m3 = re.search(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"', text)
        if m3:
            return m3.group(1)

        log.warning(f"  Não encontrou channel_id para {handle}")
    except Exception as e:
        log.error(f"  Erro ao resolver {handle}: {e}")

    return None


# ── Busca RSS de um canal ──────────────────────────────────────────────────
def fetch_channel_videos(yt_id: str, db_id: int, age_min: int, age_max: int, gender: str) -> int:
    """
    Busca últimos 15 vídeos via RSS e insere no banco.
    Retorna número de vídeos novos inseridos.
    """
    if not _HAS_REQUESTS:
        return 0

    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            log.warning(f"  RSS status {r.status_code} para {yt_id}")
            return 0

        root = ET.fromstring(r.content)

        conn   = get_conn()
        added  = 0

        for entry in root.findall('atom:entry', RSS_NS):
            vid_el   = entry.find('yt:videoId', RSS_NS)
            title_el = entry.find('atom:title', RSS_NS)
            pub_el   = entry.find('atom:published', RSS_NS)
            thumb_el = entry.find('.//media:thumbnail', RSS_NS)

            if vid_el is None:
                continue

            yt_vid   = vid_el.text.strip()
            title    = (title_el.text or '').strip() if title_el is not None else ''
            pub      = (pub_el.text or '').strip()   if pub_el   is not None else ''
            thumb    = (
                thumb_el.get('url')
                if thumb_el is not None
                else f"https://i.ytimg.com/vi/{yt_vid}/hqdefault.jpg"
            )

            try:
                cur = conn.execute("""
                    INSERT OR IGNORE INTO videos
                        (channel_ref, youtube_id, title, thumbnail, published_at,
                         age_min, age_max, gender)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (db_id, yt_vid, title, thumb, pub, age_min, age_max, gender))
                if cur.rowcount > 0:
                    added += 1
            except Exception as e:
                log.debug(f"  Insert ignorado ({yt_vid}): {e}")

        conn.commit()
        conn.close()
        return added

    except ET.ParseError as e:
        log.error(f"  XML inválido para {yt_id}: {e}")
    except Exception as e:
        log.error(f"  Erro RSS para {yt_id}: {e}")
    return 0


# ── Scrape completo ────────────────────────────────────────────────────────
def scrape_all(force_resolve: bool = False) -> dict:
    """
    1. Resolve channel_id para canais que ainda não têm
    2. Busca RSS e insere vídeos novos
    Retorna dict com estatísticas
    """
    if not _HAS_REQUESTS:
        log.error("requests não instalado. Execute: pip install requests")
        return {'error': 'requests not installed'}

    log.info("═══ SalaTV Scraper iniciado ═══")

    conn     = get_conn()
    channels = [dict(r) for r in conn.execute("SELECT * FROM channels WHERE active = 1").fetchall()]
    conn.close()

    resolved = 0
    skipped  = 0
    new_vids = 0
    errors   = 0

    for ch in channels:
        yt_cid = ch['channel_id']

        # ── Passo 1: Resolve channel_id se necessário ─────────────
        if not yt_cid or force_resolve:
            log.info(f"→ Resolvendo {ch['handle']} ...")
            yt_cid = resolve_channel_id(ch['handle'])
            if yt_cid:
                update_channel_id(ch['id'], yt_cid)
                resolved += 1
                log.info(f"   ✓ {yt_cid}")
            else:
                skipped += 1
                log.warning(f"   ✗ Não resolvido — pulando")
                continue
            time.sleep(1.5)  # cortesia para o YouTube

        # ── Passo 2: Busca vídeos via RSS ─────────────────────────
        n = fetch_channel_videos(yt_cid, ch['id'], ch['age_min'], ch['age_max'], ch['gender'])
        new_vids += n
        if n:
            log.info(f"   📹 {ch['name']}: +{n} vídeo(s) novo(s)")
        time.sleep(0.8)

    result = {
        'channels_total':    len(channels),
        'channels_resolved': resolved,
        'channels_skipped':  skipped,
        'new_videos':        new_vids,
        'timestamp':         datetime.now(timezone.utc).isoformat(),
    }
    log.info(f"═══ Scrape concluído: {new_vids} novos vídeos ═══")
    return result


# ── Execução direta ────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
    )
    force = '--force' in sys.argv
    result = scrape_all(force_resolve=force)
    print("\nResultado:", result)
