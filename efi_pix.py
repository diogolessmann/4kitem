"""
Adapter PIX da Efí (Gerencianet) para o 4kitem — receber (cobrança) e enviar (afiliado).
Tudo lido de ENV (sem segredo no código). Cert .p12 vem em base64 (EFI_CERT_B64) p/ Railway.
Validado em produção 27/jun/2026 (cobrança + dinheiro real caindo). NÃO importa nada do app.py.
"""
import os, base64, time, uuid, tempfile, warnings
import requests

EFI_BASE          = os.environ.get('EFI_BASE', 'https://pix.api.efipay.com.br')
EFI_CLIENT_ID     = os.environ.get('EFI_CLIENT_ID', '')
EFI_CLIENT_SECRET = os.environ.get('EFI_CLIENT_SECRET', '')
EFI_PIX_KEY       = os.environ.get('EFI_PIX_KEY', '')      # chave recebedora (EVP)
EFI_CERT_B64      = os.environ.get('EFI_CERT_B64', '')     # .p12 em base64

_pem_path = None
def _cert_pem():
    """Converte o .p12 (base64 do env) em PEM (cert+key) e cacheia o caminho."""
    global _pem_path
    if _pem_path and os.path.exists(_pem_path):
        return _pem_path
    if not EFI_CERT_B64:
        raise RuntimeError('EFI_CERT_B64 não configurado')
    from cryptography.hazmat.primitives.serialization import (
        pkcs12, Encoding, PrivateFormat, NoEncryption)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')   # cert Efí tem serial não-padrão (warning inofensivo)
        key, cert, _ = pkcs12.load_key_and_certificates(base64.b64decode(EFI_CERT_B64), None)
    pem = (cert.public_bytes(Encoding.PEM) +
           key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
    p = os.path.join(tempfile.gettempdir(), 'efi_cert.pem')
    with open(p, 'wb') as f:
        f.write(pem)
    _pem_path = p
    return p

_tok = {'v': '', 'exp': 0}
def _token():
    if _tok['v'] and time.time() < _tok['exp'] - 60:
        return _tok['v']
    auth = base64.b64encode(f'{EFI_CLIENT_ID}:{EFI_CLIENT_SECRET}'.encode()).decode()
    r = requests.post(f'{EFI_BASE}/oauth/token',
        headers={'Authorization': f'Basic {auth}', 'Content-Type': 'application/json'},
        json={'grant_type': 'client_credentials'}, cert=_cert_pem(), timeout=20)
    r.raise_for_status()
    j = r.json()
    _tok['v'] = j['access_token']
    _tok['exp'] = time.time() + int(j.get('expires_in', 3600))
    return _tok['v']

def _h():
    return {'Authorization': f'Bearer {_token()}', 'Content-Type': 'application/json'}

def configurado():
    return bool(EFI_CLIENT_ID and EFI_CLIENT_SECRET and EFI_PIX_KEY and EFI_CERT_B64)

def criar_cobranca(valor, descricao='', expira=3600):
    """Cria cobrança PIX imediata. Retorna {txid, copia_cola, location, status} ou {erro}.
    O pixCopiaECola já vem aqui — o QR é gerado no front (qrcodejs), igual o SlotZap."""
    body = {'calendario': {'expiracao': int(expira)},
            'valor': {'original': f'{float(valor):.2f}'},
            'chave': EFI_PIX_KEY,
            'solicitacaoPagador': (descricao or '')[:140]}
    try:
        r = requests.post(f'{EFI_BASE}/v2/cob', headers=_h(), cert=_cert_pem(), json=body, timeout=20)
        j = r.json()
        if r.status_code in (200, 201) and j.get('txid'):
            return {'txid': j['txid'], 'copia_cola': j.get('pixCopiaECola', ''),
                    'location': j.get('location', ''), 'status': j.get('status')}
        return {'erro': str(j)[:300]}
    except Exception as e:
        return {'erro': str(e)[:200]}

def consultar_cobranca(txid):
    """Status da cobrança. status == 'CONCLUIDA' = PAGA. Usado pelo reconciliador/baixa."""
    try:
        r = requests.get(f'{EFI_BASE}/v2/cob/{txid}', headers=_h(), cert=_cert_pem(), timeout=20)
        j = r.json()
        return {'status': j.get('status'), 'pago': j.get('status') == 'CONCLUIDA',
                'pix': j.get('pix', [])}
    except Exception as e:
        return {'erro': str(e)[:200]}

def consultar_e2e(txid):
    """Pega o endToEndId + valor do PIX recebido nessa cobrança (pra poder estornar)."""
    c = consultar_cobranca(txid)
    if c.get('erro'):
        return {'erro': c['erro']}
    pix = c.get('pix') or []
    if not pix:
        return {'erro': 'sem PIX recebido nessa cobrança (nada a estornar)'}
    return {'e2eid': pix[0].get('endToEndId', ''), 'valor': pix[0].get('valor', '')}

def devolver_pix(e2eid, valor, id_devolucao=None):
    """Estorna (devolve) um PIX recebido. id_devolucao = idempotência (alfanumérico ≤35:
    reusar o mesmo NÃO devolve 2×). PUT /v2/pix/:e2eId/devolucao/:id."""
    if not e2eid:
        return {'erro': 'e2eId vazio'}
    idd = ''.join(ch for ch in (id_devolucao or '') if ch.isalnum())[:35] or uuid.uuid4().hex[:35]
    body = {'valor': f'{float(valor):.2f}'}
    try:
        r = requests.put(f'{EFI_BASE}/v2/pix/{e2eid}/devolucao/{idd}',
                         headers=_h(), cert=_cert_pem(), json=body, timeout=20)
        j = r.json()
        if r.status_code in (200, 201) and (j.get('id') or j.get('status')):
            return {'id': j.get('id', idd), 'status': j.get('status'), 'rtrId': j.get('rtrId', '')}
        return {'erro': str(j)[:300]}
    except Exception as e:
        return {'erro': str(e)[:200]}

def enviar_pix(valor, chave_destino, info='', id_envio=None):
    """Envia PIX (paga afiliado). id_envio = idempotência (reusar o mesmo p/ não pagar 2×).
    Retorna {idEnvio, e2eId, status} ou {erro, idEnvio}."""
    idenvio = (id_envio or uuid.uuid4().hex)[:35]
    body = {'valor': f'{float(valor):.2f}',
            'pagador': {'chave': EFI_PIX_KEY, 'infoPagador': (info or '')[:140]},
            'favorecido': {'chave': chave_destino}}
    try:
        r = requests.put(f'{EFI_BASE}/v2/gn/pix/{idenvio}', headers=_h(), cert=_cert_pem(), json=body, timeout=20)
        j = r.json()
        if r.status_code in (200, 201) and (j.get('idEnvio') or j.get('e2eId')):
            return {'idEnvio': j.get('idEnvio', idenvio), 'e2eId': j.get('e2eId', ''),
                    'status': j.get('status')}
        return {'erro': str(j)[:300], 'idEnvio': idenvio}
    except Exception as e:
        return {'erro': str(e)[:200], 'idEnvio': idenvio}
