"""
vitrine_fabricantes.py — puxa o catálogo dos FABRICANTES que a loja revende (Vitrine, 17/set/2026)

Nordecor (nordecor.com.br) publica o catálogo inteiro numa API pública do WordPress:
  /wp-json/wp/v2/produto  → 894 produtos com taxonomias (tipo, potência, K, lúmens, IRC, IP, soquete)
  e ACF (descrição curta, código por cor, fotos por cor, tabela técnica).
O importador roda em thread, baixa a 1ª foto de cada item, comprime (WebP ≤ 120 KB), e grava em
vit_produtos com marca "Nordecor", código = código da cor (ex. 80080), SEM preço (a loja põe o
preço depois ou o cliente pede orçamento). Reimportar só atualiza specs/foto de quem já existe.
Regras: é catálogo de revenda (o lojista é revendedor autorizado); fotos são do fabricante;
nunca inventar preço; respeitar o servidor (1 req/0,3 s).
"""
import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from vitrine_db import get_vit_db

UA = {'User-Agent': 'Mozilla/5.0 (Vitrine 4kitem; revenda) AppleWebKit/537.36'}
NORDECOR = 'https://nordecor.com.br/wp-json/wp/v2'
# tipo_produto da Nordecor → tipo da Vitrine
MAPA_TIPO = {'Spot': 'spot', 'Lâmpada Técnica': 'lampada', 'Lâmpada Decorativa': 'lampada', 'Fita LED': 'fita',
             'Perfil LED': 'perfil', 'Painel LED': 'plafon', 'Luminária': 'plafon', 'Pendente': 'pendente', 'Arandela': 'arandela',
             'Trilho/Sistema': 'trilho', 'Espeto': 'jardim', 'Balizador': 'jardim', 'Embutido de Solo': 'jardim',
             'Downlight': 'spot', 'Fonte/Driver': 'fonte', 'Módulo': 'outro', 'Acessório': 'outro'}
PROGRESSO = {}   # slug → dict(total, feitos, novos, atualizados, erro, fim)


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url):
    return json.loads(_get(url))


def _limpa(t):
    t = re.sub(r'<[^>]+>', '', t or '')
    t = t.replace('&#8211;', '–').replace('&amp;', '&').replace('&#038;', '&').replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', t).strip()


def _comprime(blob, base, max_px=900, max_kb=120):
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(blob))
    im = ImageOps.exif_transpose(im)
    if im.mode in ('RGBA', 'LA', 'P'):           # PNG com fundo transparente → fundo branco
        fundo = Image.new('RGB', im.size, (255, 255, 255))
        fundo.paste(im.convert('RGBA'), mask=im.convert('RGBA').split()[-1])
        im = fundo
    im = im.convert('RGB')
    im.thumbnail((max_px, max_px))
    q = 82
    while True:
        out = io.BytesIO()
        im.save(out, 'WEBP', quality=q, method=6)
        if out.tell() <= max_kb * 1024 or q <= 45:
            break
        q -= 8
    return base + '.webp', out.getvalue()


def _termos(p):
    """Taxonomias embutidas → dict nome_taxonomia → [nomes]."""
    out = {}
    for grupo in (p.get('_embedded', {}) or {}).get('wp:term', []):
        for t in grupo:
            out.setdefault(t['taxonomy'], []).append(_limpa(t['name']))
    return out


def _k_de(termos, titulo):
    ks = set()
    for t in termos.get('temperatura_cor', []) + [titulo]:
        for m in re.findall(r'(\d[\.\d]{3,5})\s*K', t.replace(' ', '')):
            ks.add(m.replace('.', ''))
    return ' '.join(sorted(k for k in ks if k.isdigit() and 2000 <= int(k) <= 7000))


def importar_nordecor(slug, media_dir, categoria_id=None, limite=None):
    """Roda em thread. categoria_id = id de categoria_produtos (None = tudo)."""
    prog = PROGRESSO.setdefault(slug, {})
    prog.update(total=0, feitos=0, novos=0, atualizados=0, erro='', fim=False, inicio=time.time())
    try:
        conn = get_vit_db()
        loja = conn.execute('SELECT id FROM vit_lojas WHERE slug=?', (slug,)).fetchone()
        lid = loja['id']
        pagina, feitos = 1, 0
        while True:
            q = {'per_page': 50, 'page': pagina, '_embed': 1}
            if categoria_id:
                q['categoria_produtos'] = categoria_id
            url = f"{NORDECOR}/produto?{urllib.parse.urlencode(q)}"
            try:
                itens = _json(url)
            except Exception as e:
                if pagina == 1:
                    raise
                break
            if not isinstance(itens, list) or not itens:
                break
            if pagina == 1:
                try:
                    req = urllib.request.Request(url, headers=UA, method='HEAD')
                    with urllib.request.urlopen(req, timeout=30) as r:
                        prog['total'] = int(r.headers.get('X-WP-Total', 0))
                except Exception:
                    prog['total'] = 0
            for p in itens:
                if limite and feitos >= limite:
                    break
                try:
                    _grava(conn, lid, slug, media_dir, p, prog)
                except Exception as e:
                    print(f'[vitrine] nordecor item {p.get("id")}: {e}')
                feitos += 1
                prog['feitos'] = feitos
                time.sleep(0.3)
            conn.commit()
            if limite and feitos >= limite:
                break
            pagina += 1
        conn.commit()
        conn.close()
    except Exception as e:
        prog['erro'] = str(e)[:200]
    prog['fim'] = True
    prog['segundos'] = int(time.time() - prog['inicio'])


def _grava(conn, lid, slug, media_dir, p, prog):
    titulo = _limpa(p['title']['rendered'])[:140]
    acf = p.get('acf') or {}
    termos = _termos(p)
    cores = acf.get('cor') or []
    cor0 = cores[0] if cores and isinstance(cores[0], dict) else {}
    codigo = str(cor0.get('codigo_da_cor') or p['slug'])[:30]
    tipo = 'outro'
    for t in termos.get('tipo_produto', []):
        tipo = MAPA_TIPO.get(t, 'outro')
    specs = []
    for tax, rot in (('potencia', ''), ('lumens', ''), ('irc', ''), ('facho_angulo', 'facho '), ('grau_protecao', ''),
                     ('tensao_alimentacao', ''), ('soquete_compatibilidade', ''), ('tipo_instalacao', '')):
        v = termos.get(tax)
        if v:
            specs.append(rot + v[0])
    specs = ' · '.join(dict.fromkeys(specs))[:200]
    k = _k_de(termos, titulo)
    desc = _limpa(acf.get('descricao_curta') or '')[:1500]
    cat = 'ilum'
    ex = conn.execute('SELECT id, foto FROM vit_produtos WHERE loja_id=? AND codigo=? AND marca=?', (lid, codigo, 'Nordecor')).fetchone()
    foto = ex['foto'] if ex else ''
    if not foto:
        ids = cor0.get('fotos_pela_cor') or []
        if not ids:
            ids = [d.get('imagem_display') for d in (acf.get('imagem_dados') or []) if isinstance(d, dict)]
        for mid in ids[:1]:
            try:
                m = _json(f'{NORDECOR}/media/{mid}?_fields=source_url,media_details')
                src = m.get('source_url')
                sizes = (m.get('media_details') or {}).get('sizes') or {}
                src = (sizes.get('medium_large') or sizes.get('galeria-produto-lista') or {}).get('source_url') or src
                if src:
                    nome, blob = _comprime(_get(src, 60), f"n-{codigo}-{os.urandom(2).hex()}")
                    with open(os.path.join(media_dir, nome), 'wb') as fh:
                        fh.write(blob)
                    foto = nome
            except Exception as e:
                print(f'[vitrine] foto {codigo}: {e}')
    if ex:
        conn.execute('UPDATE vit_produtos SET titulo=?, tipo=?, k=?, specs=?, descricao=CASE WHEN descricao="" THEN ? ELSE descricao END, foto=? WHERE id=?',
                     (titulo, tipo, k, specs, desc, foto, ex['id']))
        prog['atualizados'] = prog.get('atualizados', 0) + 1
    else:
        conn.execute('''INSERT INTO vit_produtos (loja_id, codigo, titulo, marca, categoria, tipo, k, specs, descricao, foto, ativo, ordem)
                        VALUES (?,?,?,?,?,?,?,?,?,?,1,500)''', (lid, codigo, titulo, 'Nordecor', cat, tipo, k, specs, desc, foto))
        prog['novos'] = prog.get('novos', 0) + 1


def iniciar_nordecor(slug, media_dir, categoria_id=None, limite=None):
    if PROGRESSO.get(slug) and not PROGRESSO[slug].get('fim', True):
        return False
    threading.Thread(target=importar_nordecor, args=(slug, media_dir, categoria_id, limite), daemon=True).start()
    return True


CATEGORIAS_NORDECOR = [(173, 'Spot'), (172, 'Spot para Trilho'), (359, 'Linha de Spots LOYO'), (184, 'Downlight POWERUS'), (362, 'Downlight POWERLUX'),
                       (177, 'Fita LED'), (185, 'Perfil para Fita LED'), (187, 'Fonte de Alimentação'), (175, 'Plafon'), (289, 'Painel LED'),
                       (178, 'Pendente / Lustre'), (171, 'Arandela'), (174, 'Jardim / Externa'), (176, 'Balizador'), (188, 'Lâmpada Técnica'),
                       (169, 'Lâmpada Decorativa'), (248, 'Magnetic Track KAY'), (262, 'Cinta Eletrificada SITY'), (279, 'Marcenaria'), (277, 'Módulos TAP')]
