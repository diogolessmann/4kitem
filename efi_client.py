"""
efi_client.py — cliente Efí (Pix) da conta SEPARADA do PubShow. Usado SÓ pelo pubshow_pay.py.

16/10/26 (Lote C): a RifaJá/Arena/CAMPonline continuam no efi_pix.py de sempre (idêntico ao de cd052dd,
conta EFI_*). Aqui fica o EfiClient(prefixo_env) da S7: 1 instância por CONTA Efí, com token, certificado
e chave próprios. O PubShow usa EfiClient('PUBSHOW_EFI_') (envs PUBSHOW_EFI_BASE, PUBSHOW_EFI_CLIENT_ID,
PUBSHOW_EFI_CLIENT_SECRET, PUBSHOW_EFI_PIX_KEY, PUBSHOW_EFI_CERT_B64). O PEM nunca é o 'efi_cert.pem'
da RifaJá: o nome leva o prefixo e o hash do próprio certificado (V7-07).
Endpoints conferidos em dev.efipay.com.br em 15/10/26 [L]. O que a doc não confirmou está marcado [H].
Toda função devolve {'erro', 'http'?} em vez de levantar exceção (sem 'http' = rede/timeout).
NÃO importa nada do app.py nem do efi_pix.py.
"""
import os, re, base64, hashlib, time, uuid, tempfile, warnings, threading
from decimal import Decimal, ROUND_HALF_UP
import requests

_BASE_PRODUCAO = 'https://pix.api.efipay.com.br'          # homologação: https://pix-h.api.efipay.com.br
# txid de cobrança imediata: 26 a 35 letras/dígitos (padrão Pix do BCB) [L padrão BCB; H na Efí]
TXID_RE = re.compile(r'^[a-zA-Z0-9]{26,35}$')


def _reais(cent):
    """Centavos (int) -> '12.34' (string que a API Pix espera). Sem float no caminho."""
    cent = int(cent)
    sinal = '-' if cent < 0 else ''
    cent = abs(cent)
    return f'{sinal}{cent // 100}.{cent % 100:02d}'


def _centavos(valor):
    """'12.34' / 12.34 / Decimal -> 1234 (int). None/'' -> 0."""
    if valor is None or valor == '':
        return 0
    return int((Decimal(str(valor)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


class EfiClient:
    """Uma conta Efí. prefixo_env='PUBSHOW_EFI_' lê PUBSHOW_EFI_BASE, PUBSHOW_EFI_CLIENT_ID,
    PUBSHOW_EFI_CLIENT_SECRET, PUBSHOW_EFI_PIX_KEY e PUBSHOW_EFI_CERT_B64.
    Os parâmetros explícitos (base, client_id, ...) têm prioridade sobre o env (testes)."""

    def __init__(self, prefixo_env='PUBSHOW_EFI_', base=None, client_id=None, client_secret=None,
                 pix_key=None, cert_b64=None):
        env = os.environ.get
        p = prefixo_env or 'PUBSHOW_EFI_'
        self.prefixo = p
        self._cfg = {
            'base':          base if base is not None else env(p + 'BASE', _BASE_PRODUCAO),
            'client_id':     client_id if client_id is not None else env(p + 'CLIENT_ID', ''),
            'client_secret': client_secret if client_secret is not None else env(p + 'CLIENT_SECRET', ''),
            'pix_key':       pix_key if pix_key is not None else env(p + 'PIX_KEY', ''),
            'cert_b64':      cert_b64 if cert_b64 is not None else env(p + 'CERT_B64', ''),
        }
        self._tok = {'v': '', 'exp': 0}
        self._pem_path = None
        self._lock = threading.Lock()

    @property
    def base(self):
        return self._cfg['base']

    @property
    def client_id(self):
        return self._cfg['client_id']

    @property
    def client_secret(self):
        return self._cfg['client_secret']

    @property
    def pix_key(self):
        return self._cfg['pix_key']

    @property
    def cert_b64(self):
        return self._cfg['cert_b64']

    def _pem_nome(self):
        """15/10/26 (V7-07): nunca 'efi_cert.pem' (é o da RifaJá, no efi_pix.py). O nome leva o prefixo e o
        hash do próprio certificado: 2 contas nunca trocam o PEM uma da outra."""
        slug = re.sub(r'[^a-z0-9]+', '_', self.prefixo.lower()).strip('_') or 'conta'
        h = hashlib.sha256((self.cert_b64 or '').encode()).hexdigest()[:12]
        return f'efi_cert_{slug}_{h}.pem'

    # ── certificado + token (cache POR INSTÂNCIA) ──
    def _cert_pem(self):
        """Converte o .p12 (base64 do env) em PEM (cert+key) e cacheia o caminho."""
        if self._pem_path and os.path.exists(self._pem_path):
            return self._pem_path
        if not self.cert_b64:
            raise RuntimeError(f'{self.prefixo}CERT_B64 não configurado')
        from cryptography.hazmat.primitives.serialization import (
            pkcs12, Encoding, PrivateFormat, NoEncryption)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')   # cert Efí tem serial não-padrão (warning inofensivo)
            key, cert, _ = pkcs12.load_key_and_certificates(base64.b64decode(self.cert_b64), None)
        pem = (cert.public_bytes(Encoding.PEM) +
               key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
        p = os.path.join(tempfile.gettempdir(), self._pem_nome())
        with open(p, 'wb') as f:
            f.write(pem)
        try:
            os.chmod(p, 0o600)               # chave privada: só o dono do processo lê
        except Exception:
            pass
        self._pem_path = p
        return p

    def _token(self):
        with self._lock:
            if self._tok['v'] and time.time() < self._tok['exp'] - 60:
                return self._tok['v']
            auth = base64.b64encode(f'{self.client_id}:{self.client_secret}'.encode()).decode()
            r = requests.post(f'{self.base}/oauth/token',
                headers={'Authorization': f'Basic {auth}', 'Content-Type': 'application/json'},
                json={'grant_type': 'client_credentials'}, cert=self._cert_pem(), timeout=20)
            r.raise_for_status()
            j = r.json()
            self._tok['v'] = j['access_token']
            self._tok['exp'] = time.time() + int(j.get('expires_in', 3600))
            return self._tok['v']

    def _h(self):
        return {'Authorization': f'Bearer {self._token()}', 'Content-Type': 'application/json'}

    def configurado(self):
        return bool(self.client_id and self.client_secret and self.pix_key and self.cert_b64)

    # Timeout curto (6 s): o 4kitem roda em 1 worker.
    def _req(self, metodo, caminho, json=None, params=None, timeout=6):
        """(status_http, json|None). Exceção de rede sobe pra quem chamou tratar."""
        r = requests.request(metodo, f'{self.base}{caminho}', headers=self._h(), cert=self._cert_pem(),
                             json=json, params=params, timeout=timeout)
        try:
            j = r.json() if (r.content or b'').strip() else None
        except ValueError:
            j = None
        return r.status_code, j

    @staticmethod
    def _erro(st, j):
        return {'erro': str(j)[:300] if j is not None else f'HTTP {st}', 'http': st}

    # ══ Cobrança ══
    def criar_cobranca_txid(self, txid, valor_cent, descricao='', expira=900, timeout=6):
        """Cobrança imediata com txid NOSSO: PUT /v2/cob/:txid [L] (201). Mesmo txid de novo =
        409 'txid_duplicado' [L] → aqui vira idempotente: lê a cobrança que já existe e devolve
        ela (novo=False), desde que o valor seja o mesmo. Erro sem 'http' = rede/timeout:
        re-tentar com o MESMO txid nunca duplica."""
        if not TXID_RE.match(txid or ''):
            return {'erro': 'txid_invalido (26-35 letras/dígitos)'}
        body = {'calendario': {'expiracao': int(expira)},
                'valor': {'original': _reais(valor_cent)},
                'chave': self.pix_key,
                'solicitacaoPagador': (descricao or '')[:140]}
        try:
            st, j = self._req('PUT', f'/v2/cob/{txid}', json=body, timeout=timeout)
            j = j or {}
            if st in (200, 201) and j.get('txid'):
                return {'txid': j['txid'], 'copia_cola': j.get('pixCopiaECola', ''),
                        'location': j.get('location', ''), 'status': j.get('status'),
                        'revisao': j.get('revisao'), 'novo': True}
            if st == 409 or 'txid_duplicado' in str(j):
                c = self.consultar(txid, timeout=timeout)
                if c.get('erro'):
                    return c
                if c.get('valor_cent') != int(valor_cent):
                    return {'erro': 'txid_duplicado_com_valor_diferente', 'http': 409}
                return {'txid': txid, 'copia_cola': c.get('copia_cola', ''),
                        'location': c.get('location', ''), 'status': c.get('status'), 'novo': False}
            return self._erro(st, j)
        except Exception as e:
            return {'erro': str(e)[:200]}

    def consultar(self, txid, timeout=6):
        """GET /v2/cob/:txid [L], já mastigado: status, pago (CONCLUIDA), valor_cent (original),
        pago_cent (soma dos pix[].valor), e2eid/horario do 1º PIX, devolvido_cent, copia_cola [H: o
        pixCopiaECola no GET segue o padrão BCB]."""
        try:
            st, j = self._req('GET', f'/v2/cob/{txid}', timeout=timeout)
            if st != 200 or not isinstance(j, dict):
                return self._erro(st, j)
            pix = j.get('pix') or []
            devolvido = 0
            for p in pix:
                for d in (p.get('devolucoes') or []):
                    if d.get('status') == 'DEVOLVIDO':
                        devolvido += _centavos(d.get('valor'))
            return {'status': j.get('status'), 'pago': j.get('status') == 'CONCLUIDA',
                    'valor_cent': _centavos((j.get('valor') or {}).get('original')),
                    'pago_cent': sum(_centavos(p.get('valor')) for p in pix),
                    'e2eid': (pix[0].get('endToEndId', '') if pix else ''),
                    'horario': (pix[0].get('horario', '') if pix else ''),
                    'devolvido_cent': devolvido, 'pix': pix,
                    'copia_cola': j.get('pixCopiaECola', ''), 'location': j.get('location', '')}
        except Exception as e:
            return {'erro': str(e)[:200]}

    def cancelar_cobranca(self, txid, timeout=6):
        """PATCH /v2/cob/:txid com status REMOVIDA_PELO_USUARIO_RECEBEDOR [L exemplo da doc].
        Só cobrança ATIVA (senão 409 'status_cobranca_invalido' [L])."""
        try:
            st, j = self._req('PATCH', f'/v2/cob/{txid}',
                              json={'status': 'REMOVIDA_PELO_USUARIO_RECEBEDOR'}, timeout=timeout)
            if st in (200, 201):
                return {'ok': True, 'status': (j or {}).get('status')}
            return self._erro(st, j)
        except Exception as e:
            return {'erro': str(e)[:200]}

    @staticmethod
    def _rfc3339(dt):
        if isinstance(dt, str):
            return dt
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')   # dt em UTC (naive = UTC)

    def listar_pix_recebidos(self, inicio, fim, pagina=0, itens=100, timeout=10):
        """PIX recebidos no intervalo: GET /v2/pix?inicio=&fim= [L]. Cada item traz txid,
        endToEndId, valor, horario [L]. Paginação 'paginacao.paginaAtual/itensPorPagina' = padrão BCB [H]."""
        params = {'inicio': self._rfc3339(inicio), 'fim': self._rfc3339(fim),
                  'paginacao.paginaAtual': int(pagina), 'paginacao.itensPorPagina': int(itens)}
        try:
            st, j = self._req('GET', '/v2/pix', params=params, timeout=timeout)
            if st != 200 or not isinstance(j, dict):
                return self._erro(st, j)
            pag = (j.get('parametros') or {}).get('paginacao') or {}
            return {'pix': j.get('pix') or [], 'pagina': pag.get('paginaAtual', pagina),
                    'paginas': pag.get('quantidadeDePaginas', 1)}
        except Exception as e:
            return {'erro': str(e)[:200]}

    def listar_cobrancas(self, inicio, fim, status=None, pagina=0, itens=100, timeout=10):
        """GET /v2/cob?inicio=&fim= [L]. Filtro 'status' e paginação = padrão BCB [H na Efí]."""
        params = {'inicio': self._rfc3339(inicio), 'fim': self._rfc3339(fim),
                  'paginacao.paginaAtual': int(pagina), 'paginacao.itensPorPagina': int(itens)}
        if status:
            params['status'] = status
        try:
            st, j = self._req('GET', '/v2/cob', params=params, timeout=timeout)
            if st != 200 or not isinstance(j, dict):
                return self._erro(st, j)
            pag = (j.get('parametros') or {}).get('paginacao') or {}
            return {'cobs': j.get('cobs') or [], 'pagina': pag.get('paginaAtual', pagina),
                    'paginas': pag.get('quantidadeDePaginas', 1)}
        except Exception as e:
            return {'erro': str(e)[:200]}

    # ══ Devolução ══
    def consultar_devolucao(self, e2eid, id_devolucao, timeout=6):
        """GET /v2/pix/:e2eId/devolucao/:id [L]. status EM_PROCESSAMENTO|DEVOLVIDO|NAO_REALIZADO [L]."""
        try:
            st, j = self._req('GET', f'/v2/pix/{e2eid}/devolucao/{id_devolucao}', timeout=timeout)
            if st != 200 or not isinstance(j, dict):
                return self._erro(st, j)
            return {'id': j.get('id', id_devolucao), 'status': j.get('status'),
                    'rtrId': j.get('rtrId', ''), 'valor': j.get('valor', ''), 'motivo': j.get('motivo', '')}
        except Exception as e:
            return {'erro': str(e)[:200]}

    def devolver_pix(self, e2eid, valor, id_devolucao=None, split=False, idempotente=False, timeout=20):
        """Estorna (devolve) um PIX recebido. id_devolucao = idempotência (alfanumérico ≤35).
        PUT /v2/pix/:e2eId/devolucao/:id [L]. Reusar o id dá 409 'devolucao_id_duplicado' [L] (não devolve 2×).
        - split=True: cobrança COM split usa PUT /v2/gn/split/pix/:e2eid/devolucao/:id [L doc do split];
          o débito cai só na conta integradora (o PubShow) [L].
        - idempotente=True: o 409 do mesmo id vira SUCESSO, lendo a devolução que já existe
          (GET /v2/pix/:e2eId/devolucao/:id [L]; na devolução de split esse GET é [H]).
          Erro com resposta HTTP traz 'http' (sem 'http' = rede/timeout: pode ter saído, re-tente com o MESMO id)."""
        if not e2eid:
            return {'erro': 'e2eId vazio'}
        idd = ''.join(ch for ch in (id_devolucao or '') if ch.isalnum())[:35] or uuid.uuid4().hex[:35]
        body = {'valor': f'{float(valor):.2f}'}
        caminho = (f'/v2/gn/split/pix/{e2eid}/devolucao/{idd}' if split
                   else f'/v2/pix/{e2eid}/devolucao/{idd}')
        try:
            r = requests.put(f'{self.base}{caminho}',
                             headers=self._h(), cert=self._cert_pem(), json=body, timeout=timeout)
            j = r.json()
            if r.status_code in (200, 201) and (j.get('id') or j.get('status')):
                return {'id': j.get('id', idd), 'status': j.get('status'), 'rtrId': j.get('rtrId', '')}
            if idempotente and (r.status_code == 409 or 'devolucao_id_duplicado' in str(j)):
                c = self.consultar_devolucao(e2eid, idd, timeout=timeout)
                if not c.get('erro'):
                    c['ja_existia'] = True
                    return c
                # o id JÁ foi usado, mas não deu pra ler: NUNCA tratar como "não devolveu"
                return {'erro': str(j)[:300], 'http': r.status_code, 'ja_existia': True}
            if idempotente:
                return {'erro': str(j)[:300], 'http': r.status_code}
            return {'erro': str(j)[:300]}
        except Exception as e:
            return {'erro': str(e)[:200]}

    # ── Split de pagamento Pix (só entre contas Efí; até 20 favorecidos; nunca pra própria conta) [L] ──
    def split_config(self, config_id, minha_parte_cent, repasses, descricao='', timeout=6):
        """Cria/atualiza uma config de split com id NOSSO: PUT /v2/gn/split/config/:id [L]
        (id que já existe = atualiza [L]). Tudo em valor FIXO: a soma (minha parte + repasses)
        tem que bater com o valor da cobrança na hora do vínculo [L]. lancamento.imediato=True é
        o único modo hoje [L]; divisaoTarifa='assumir_total' = o PubShow paga a tarifa [L].
        repasses = [{'conta': '<nº da conta Efí>', 'doc': '<cpf ou cnpj, sem pontuação>', 'valor_cent': int}].
        favorecido: a doc mostra 'cpf' + 'conta' [L]; 'cnpj' pra conta PJ é [H]. Formato do id [H].
        16/10/26 (CC1): o CNPJ novo pode ter LETRAS (IN RFB 2.229/2024): mantém as letras (maiúsculas) e escolhe
        'cnpj' pelos 14 caracteres. Antes o \\D apagava as letras e ia como 'cpf' com 12 dígitos."""
        favorecidos = []
        for rp in (repasses or []):
            doc = re.sub(r'[^0-9A-Z]', '', str(rp.get('doc', '')).upper())
            fav = {('cnpj' if len(doc) == 14 else 'cpf'): doc, 'conta': str(rp.get('conta', '')).strip()}
            favorecidos.append({'tipo': 'fixo', 'valor': _reais(rp['valor_cent']), 'favorecido': fav})
        body = {'descricao': (descricao or 'PubShow')[:80],
                'lancamento': {'imediato': True},
                'split': {'divisaoTarifa': 'assumir_total',
                          'minhaParte': {'tipo': 'fixo', 'valor': _reais(minha_parte_cent)},
                          'repasses': favorecidos}}
        try:
            st, j = self._req('PUT', f'/v2/gn/split/config/{config_id}', json=body, timeout=timeout)
            if st in (200, 201):
                j = j or {}
                return {'id': j.get('id', config_id), 'status': j.get('status'), 'revisao': j.get('revisao')}
            return self._erro(st, j)
        except Exception as e:
            return {'erro': str(e)[:200]}

    def split_vincular(self, txid, config_id, timeout=6):
        """PUT /v2/gn/split/cob/:txid/vinculo/:splitConfigId [L] → 204. Cobrança e config ATIVAS [L]."""
        try:
            st, j = self._req('PUT', f'/v2/gn/split/cob/{txid}/vinculo/{config_id}', timeout=timeout)
            if st in (200, 201, 204):
                return {'ok': True}
            return self._erro(st, j)
        except Exception as e:
            return {'erro': str(e)[:200]}

    def split_desvincular(self, txid, timeout=6):
        """DELETE /v2/gn/split/cob/:txid/vinculo [L] → 200 (cobrança paga = 400 [L])."""
        try:
            st, j = self._req('DELETE', f'/v2/gn/split/cob/{txid}/vinculo', timeout=timeout)
            if st in (200, 204):
                return {'ok': True}
            return self._erro(st, j)
        except Exception as e:
            return {'erro': str(e)[:200]}

    def split_consultar_cob(self, txid, timeout=6):
        """GET /v2/gn/split/cob/:txid [L] (cobrança + split vinculado)."""
        try:
            st, j = self._req('GET', f'/v2/gn/split/cob/{txid}', timeout=timeout)
            if st != 200:
                return self._erro(st, j)
            return j or {}
        except Exception as e:
            return {'erro': str(e)[:200]}
