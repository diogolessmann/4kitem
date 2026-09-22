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
import unicodedata
import urllib.parse
import urllib.request

from vitrine_db import get_vit_db

UA = {'User-Agent': 'Mozilla/5.0 (Vitrine 4kitem; revenda) AppleWebKit/537.36'}
NORDECOR = 'https://nordecor.com.br/wp-json/wp/v2'
# tipo_produto da Nordecor → tipo da Vitrine
MAPA_TIPO = {'Spot': 'spot', 'Lâmpada Técnica': 'lampada', 'Lâmpada Decorativa': 'lampada', 'Fita LED': 'fita',
             'Perfil LED': 'perfil', 'Painel LED': 'plafon', 'Luminária': 'plafon', 'Pendente': 'pendente', 'Arandela': 'arandela',
             'Trilho/Sistema': 'trilho', 'Espeto': 'jardim', 'Balizador': 'jardim', 'Embutido de Solo': 'jardim',
             'Downlight': 'spot', 'Fonte/Driver': 'fonte', 'Módulo': 'outro', 'Acessório': 'acessorio'}
PROGRESSO = {}   # slug → dict(total, feitos, novos, atualizados, erro, fim)


def _get(url, timeout=60, headers=None):
    req = urllib.request.Request(url, headers=headers or UA)
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
    prog.update(fab='Nordecor', total=0, feitos=0, novos=0, atualizados=0, erro='', fim=False, inicio=time.time())
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
                conn.commit()   # transação curta por item: não segura o lock do banco enquanto o site atende
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
    tipo = _tipo_por_nome(titulo)            # 1º o nome (a Nordecor marca vários tipos por item e o último vencia: trilho virava spot)
    if not tipo:
        for t in termos.get('tipo_produto', []):
            tipo = MAPA_TIPO.get(t) or tipo
    tipo = tipo or 'outro'
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


# ─────────────────────────────────────────────── Lumanti (21/set/2026)
# lumanti.com.br é WordPress SEM API de produto (só posts/pages). O catálogo está nas páginas de linha
# (/blog/linha/<linha>/, 12 itens por página): nome, códigos por cor, foto. Lemos só essas páginas, devagar
# (1 pedido a cada 2 s), uma vez, a pedido do lojista que revende a marca. A foto cheia é a mesma URL sem o
# sufixo -180x180. Ficha completa fica no site deles.
import html as _html

LUMANTI = 'https://lumanti.com.br'
UA_NAV = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36',
          'Accept-Language': 'pt-BR,pt;q=0.9', 'Accept': 'text/html,image/webp,*/*;q=0.8'}
LINHAS_LUMANTI = [('lampadas-luminarias-painel-led', 'Lâmpadas, luminárias e painel LED', 'outro'),
                  ('luminarias-spots-trilhos-iluminados', 'Luminárias, spots e trilhos', 'outro'),
                  ('abajur-pendentes-arandela-lustre', 'Abajur, pendentes, arandela e lustre', 'outro'),
                  ('mangueiras-led-fita-led-neon', 'Mangueira, fita LED e neon', 'decor'),
                  ('refletores-led-quadra-parque-area-rural', 'Refletores LED', 'refletor'),
                  ('projetores-refletores-led-quadras-parques', 'Projetores e refletores', 'refletor'),
                  ('acessorios-iluminacao-led', 'Acessórios', 'acessorio')]
_TIPO_KW = [  # ordem = prioridade: o específico antes do genérico; o sistema (trilho, perfil, fita) é dono dos seus acessórios
    ('emergência', 'emergencia'), ('emergencia', 'emergencia'), ('placa de saída', 'emergencia'), ('placa de saida', 'emergencia'),
    ('fotocélula', 'sensor'), ('fotocelula', 'sensor'), ('sensor', 'sensor'), ('relé', 'sensor'), ('rele ', 'sensor'),
    ('interruptor', 'interruptor'), ('tomada', 'tomada'), ('disjuntor', 'disjuntor'), ('quadro de distribuição', 'quadro'), ('quadro de distribuicao', 'quadro'),
    ('fonte', 'fonte'), ('driver', 'fonte'), ('transformador', 'fonte'), ('reator', 'fonte'), ('amplificador', 'fonte'), ('controlador', 'fonte'),
    ('trilho', 'trilho'), ('magnetic', 'trilho'), ('track', 'trilho'), ('cinta eletrificada', 'trilho'),
    ('perfil', 'perfil'),
    ('mangueira', 'decor'), ('neon', 'decor'), ('varal', 'decor'), ('festão', 'decor'), ('festao', 'decor'), ('cordão', 'decor'), ('cordao', 'decor'),
    ('cascata', 'decor'), ('pisca', 'decor'), ('natal', 'decor'), ('fio de fada', 'decor'),
    ('fita', 'fita'), ('barra led', 'fita'),
    ('espeto', 'jardim'), ('balizador', 'jardim'), ('jardim', 'jardim'), ('de solo', 'jardim'), ('de piso', 'jardim'),
    ('braço', 'publica'), ('braco', 'publica'), ('pública', 'publica'), ('publica', 'publica'), ('telegestão', 'publica'), ('telegestao', 'publica'),
    ('poste', 'publica'), ('pétala', 'publica'), ('petala', 'publica'), ('ornamental', 'publica'), ('viária', 'publica'), ('viaria', 'publica'),
    ('refletor', 'refletor'), ('projetor', 'refletor'), ('high bay', 'refletor'), ('highbay', 'refletor'), ('ufo', 'refletor'), ('holofote', 'refletor'), ('industrial', 'refletor'),
    ('abajur', 'mesa'), ('de mesa', 'mesa'), ('de chão', 'mesa'), ('de chao', 'mesa'), ('coluna', 'mesa'),
    ('pendente', 'pendente'), ('lustre', 'pendente'),
    ('arandela', 'arandela'),
    ('spot', 'spot'), ('downlight', 'spot'),
    ('lâmpada', 'lampada'), ('lampada', 'lampada'), ('bulbo', 'lampada'), ('filamento', 'lampada'), ('dicroica', 'lampada'), ('dicróica', 'lampada'),
    ('par20', 'lampada'), ('par30', 'lampada'), ('par38', 'lampada'), ('par 20', 'lampada'), ('par 30', 'lampada'), ('tubular', 'lampada'),
    ('vela', 'lampada'), ('bolinha', 'lampada'), ('globo', 'lampada'), ('halógena', 'lampada'), ('halogena', 'lampada'), ('fluorescente', 'lampada'),
    ('acessório', 'acessorio'), ('acessorio', 'acessorio'), ('suporte', 'acessorio'), ('conector', 'acessorio'), ('adaptador', 'acessorio'),
    ('soquete', 'acessorio'), ('emenda', 'acessorio'), ('presilha', 'acessorio'), ('garra', 'acessorio'), ('canopla', 'acessorio'), ('haste', 'acessorio'),
    ('plug', 'acessorio'), ('junção', 'acessorio'), ('juncao', 'acessorio'), ('terminal', 'acessorio'), ('fixador', 'acessorio'), ('base para', 'acessorio'), ('base p/', 'acessorio'),
    ('tampa', 'acessorio'), ('moldura', 'acessorio'), ('contrapeso', 'acessorio'), ('rabicho', 'acessorio'),
    ('módulo', 'spot'), ('modulo', 'spot'),
    ('cabo', 'cabo'),
    ('painel', 'plafon'), ('plafon', 'plafon'), ('luminária', 'plafon'), ('luminaria', 'plafon')]
_RE_ITEM = re.compile(r'<div class="item"><a href="(https://lumanti\.com\.br/blog/produto/[^"]+)">.*?<b>(.*?)</b>\s*<p>(.*?)</p>.*?<img[^>]*src="([^"]+)"', re.S)
_RE_COD = re.compile(r'^((?:[A-Z0-9][A-Z0-9\-\./]{3,}\s*\|\s*)*[A-Z0-9][A-Z0-9\-\./]{3,})\s*(.*)$', re.S)


def _tipo_por_nome(titulo, padrao=None):
    """Família pelo nome do produto (o que o cliente digita). None/padrao quando nenhuma palavra bate."""
    t = unicodedata.normalize('NFKC', titulo).lower()   # 'Perﬁl' com ligadura vira 'Perfil'
    for kw, tipo in _TIPO_KW:
        if kw in t:
            return tipo
    return padrao


def importar_lumanti(slug, media_dir, linha=None, limite=None):
    """Roda em thread. linha = slug da linha (None = todas). Reimportar só atualiza quem já existe."""
    prog = PROGRESSO.setdefault(slug, {})
    prog.update(fab='Lumanti', total=0, feitos=0, novos=0, atualizados=0, erro='', fim=False, inicio=time.time())
    try:
        conn = get_vit_db()
        lid = conn.execute('SELECT id FROM vit_lojas WHERE slug=?', (slug,)).fetchone()['id']
        feitos = 0
        for ls, ln, tipo_padrao in LINHAS_LUMANTI:
            if linha and ls != linha:
                continue
            pagina = 1
            while True:
                url = f'{LUMANTI}/blog/linha/{ls}/' + (f'page/{pagina}/' if pagina > 1 else '')
                try:
                    pag = _get(url, 60, UA_NAV).decode('utf-8', 'ignore')
                except Exception as e:
                    if pagina == 1:
                        print(f'[vitrine] lumanti {ls}: {e}')
                    break
                itens = _RE_ITEM.findall(pag)
                if not itens:
                    break
                for link, titulo, p, img in itens:
                    if limite and feitos >= limite:
                        break
                    try:
                        _grava_lumanti(conn, lid, media_dir, link, titulo, p, img, tipo_padrao, prog)
                    except Exception as e:
                        print(f'[vitrine] lumanti item {link}: {e}')
                    conn.commit()   # idem: 1 item = 1 transação
                    feitos += 1
                    prog['feitos'] = feitos
                    time.sleep(2)
                conn.commit()
                if (limite and feitos >= limite) or f'/page/{pagina + 1}/' not in pag:
                    break
                pagina += 1
                time.sleep(2)
            if limite and feitos >= limite:
                break
        conn.commit()
        conn.close()
    except Exception as e:
        prog['erro'] = str(e)[:200]
    prog['total'] = prog['feitos']
    prog['fim'] = True
    prog['segundos'] = int(time.time() - prog['inicio'])


def _grava_lumanti(conn, lid, media_dir, link, titulo, p, img, tipo_padrao, prog):
    titulo = _html.unescape(_limpa(titulo))[:140]
    p = _html.unescape(_limpa(p)).replace('…', '').strip()
    m = _RE_COD.match(p)
    codigos, desc = (m.group(1), m.group(2)) if m else ('', p)
    codigo = (codigos.split('|')[0].strip() if codigos else 'L-' + link.rstrip('/').rsplit('/', 1)[-1])[:30]
    specs = ' · '.join(c.strip() for c in codigos.split('|')[1:] if c.strip())[:200]   # outras versões/cores do mesmo item
    tipo = _tipo_por_nome(titulo, tipo_padrao) or 'outro'
    k = _k_de({}, titulo)
    ex = conn.execute('SELECT id, foto FROM vit_produtos WHERE loja_id=? AND codigo=? AND marca=?', (lid, codigo, 'Lumanti')).fetchone()
    foto = ex['foto'] if ex else ''
    if not foto and img:
        cheia = re.sub(r'-\d+x\d+(\.\w+)$', r'\1', img)
        for src in dict.fromkeys((cheia, img)):
            try:
                nome, blob = _comprime(_get(src, 60, UA_NAV), f"l-{re.sub(r'[^a-z0-9]+', '', codigo.lower())[:20]}-{os.urandom(2).hex()}", max_px=800, max_kb=100)
                with open(os.path.join(media_dir, nome), 'wb') as fh:
                    fh.write(blob)
                foto = nome
                break
            except Exception as e:
                print(f'[vitrine] foto lumanti {codigo}: {e}')
        time.sleep(1)
    if ex:
        conn.execute('UPDATE vit_produtos SET titulo=?, tipo=?, k=?, specs=CASE WHEN specs="" THEN ? ELSE specs END, '
                     'descricao=CASE WHEN descricao="" THEN ? ELSE descricao END, foto=? WHERE id=?',
                     (titulo, tipo, k, specs, desc[:1500], foto, ex['id']))
        prog['atualizados'] = prog.get('atualizados', 0) + 1
    else:
        conn.execute('''INSERT INTO vit_produtos (loja_id, codigo, titulo, marca, categoria, tipo, k, specs, descricao, foto, ativo, ordem)
                        VALUES (?,?,?,?,?,?,?,?,?,?,1,500)''', (lid, codigo, titulo, 'Lumanti', 'ilum', tipo, k, specs, desc[:1500], foto))
        prog['novos'] = prog.get('novos', 0) + 1


def iniciar_lumanti(slug, media_dir, linha=None, limite=None):
    if PROGRESSO.get(slug) and not PROGRESSO[slug].get('fim', True):
        return False
    threading.Thread(target=importar_lumanti, args=(slug, media_dir, linha, limite), daemon=True).start()
    return True
