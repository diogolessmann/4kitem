"""
desp_rag.py — IA do Despachante Lessmann
RAG com base de conhecimento embutida (CTB, DETRAN-SC, SC normativas) +
suporte a documentos do usuário (PDF, DOCX).
"""
import os
import subprocess
import logging

log = logging.getLogger("desp_rag")

# ── Config ─────────────────────────────────────────────────────────────────────
DATA_DIR   = os.environ.get("DATA_DIR", os.path.dirname(__file__))
CHROMA_DIR = os.path.join(DATA_DIR, "desp_chroma")
PDFS_DIR   = os.path.join(DATA_DIR, "desp_pdfs")
DOCS_DIR   = os.path.join(DATA_DIR, "desp_docs")
GROQ_KEY   = os.environ.get("GROQ_API_KEY", "")
MODEL_CHAT = "llama-3.3-70b-versatile"
MODEL_EMB  = "paraphrase-multilingual-MiniLM-L12-v2"

_embedder   = None
_chroma     = None
_collection = None
_groq       = None


# ══════════════════════════════════════════════════════════════════════════════
# BASE DE CONHECIMENTO INTERNA — CTB, DETRAN-SC, Despachante Documentalista SC
# ══════════════════════════════════════════════════════════════════════════════

CONHECIMENTO_BASE = [

  ("ctb_principais", """
CÓDIGO DE TRÂNSITO BRASILEIRO (CTB) — ARTIGOS ESSENCIAIS PARA DESPACHANTE

Art. 1º — O trânsito de qualquer natureza nas vias terrestres do território nacional,
abertas à circulação, rege-se por este Código.

Art. 122 — Os veículos de propulsão humana e os de tração animal, nas vias urbanas,
serão identificados conforme determinação do CONTRAN.

Art. 123 — Será obrigatória a expedição de novo Certificado de Registro de Veículo — CRV quando:
I - efetuada a transferência de propriedade;
II - o proprietário mudar o Município de domicílio ou residência;
III - houver mudança nas características do veículo;
IV - a natureza ou a categoria do veículo for alterada;
V - houver transferência para outro Estado.

Art. 124 — O Certificado de Registro de Veículo — CRV conterá as características do
veículo e a identificação do proprietário, com os seguintes dados: espécie, tipo,
carroceria, cor, modelo, ano de fabricação, cilindrada ou potência, número do motor,
número do chassi, capacidade máxima de tração, peso bruto total, peso bruto total
combinado, distância entre eixos, dimensões externas, combustível, número de registro
do veículo no órgão executivo de trânsito — RENAVAM.

Art. 128 — Nos casos de transferência de propriedade, o prazo para o proprietário
adquirente tomar as providências necessárias à transferência é de trinta dias.

Art. 129 — O proprietário que transferir a posse do veículo, tal como nos casos de
venda, dação em pagamento ou doação, fica obrigado a comunicar o fato ao órgão
executivo de trânsito do Estado, dentro de trinta dias, sob pena de ser
co-responsabilizado pelas penalidades e crimes cometidos com o veículo.
Essa comunicação é a ATPV-e (Autorização para Transferência de Propriedade de Veículo
eletrônica), obrigatória desde 2019 no Brasil.

Art. 130 — Todo veículo automotor, elétrico, articulado, reboque ou semireboque, para
transitar na via pública, estará sujeito a registro no órgão executivo de trânsito do
Estado, no Município de domicílio ou residência de seu proprietário.

Art. 130-A — Os veículos automotores, elétricos, articulados, reboques e semireboques,
registrados no Brasil, estão obrigados a portar o Certificado de Licenciamento Anual —
CRLV, com prazo de validade de doze meses, a ser expedido pelo órgão executivo de
trânsito do Estado. (O CRLV-e digital tem validade equivalente.)

Art. 131 — O Certificado de Licenciamento Anual será obtido anualmente mediante o
pagamento do IPVA, Seguro Obrigatório (DPVAT — extinto em 31/12/2019 pela MP 904),
taxas e multas, e após a vistoria do veículo quando exigida.

Art. 232 — Conduzir veículo sem os documentos de porte obrigatório (CNH, CRLV):
Infração gravíssima | Multa | Medida administrativa: retenção do veículo.

Art. 160 — O órgão executivo de trânsito exigirá a vistoria do veículo:
I - por ocasião do primeiro licenciamento;
II - nos casos de transferência de propriedade;
III - nos casos de alteração de características;
IV - nos casos de autorização para circular em situação especial.

Art. 261 — A pontuação no prontuário do condutor e as penalidades previstas neste Código
serão aplicadas de forma cumulativa.

INFRAÇÕES RELEVANTES PARA CLIENTES:
- Conduzir sem CNH: Art. 162, I — gravíssima, 7 pontos + apreensão do veículo
- CRLV vencido: Art. 230, V — grave, 5 pontos + retenção
- Velocidade acima 50%: gravíssima, 7 pontos + cassação se reincidente
- Falta de habilitação para categoria: Art. 162, III — gravíssima, 7 pontos
"""),

  ("detransc_procedimentos", """
DETRAN-SC — DEPARTAMENTO ESTADUAL DE TRÂNSITO DE SANTA CATARINA

SITE OFICIAL: detran.sc.gov.br
SISTEMA PARA DESPACHANTES: sistema.detrannet.sc.gov.br (DETRANET/DETRANNET)

ESTRUTURA:
- DETRAN-SC é órgão estadual, sediado em Florianópolis/SC
- CIRETRANs: Coordenadorias Regionais de Trânsito
- CITRANs: Coordenadorias Intermunicipais de Trânsito
- CITRAN Guaramirim: atende Schroeder, Guaramirim, Massaranduba, Corupá, Jaraguá do Sul e região

CREDENCIAL DO DESPACHANTE:
- Despachante Documentalista credenciado pelo DETRAN-SC é regulamentado por legislação estadual
- Credencial: número único por despachante, renovação periódica
- Despachante Lessmann: Credencial nº 2095, Schroeder/SC
- Serviços autorizados: representação perante DETRAN-SC em todos os processos de veículos e CNH

DETRANET (sistema.detrannet.sc.gov.br):
- Acesso exclusivo para despachantes credenciados e servidores DETRAN-SC
- Permite: consulta de situação de veículo, débitos, protocolamento de processos
- Debitos aparecem como: IPVA, DPVAT (histórico), Seguro Obrigatório, multas de trânsito, taxas
- Guias de pagamento emitidas pelo próprio sistema
- Acompanhamento de processos protocolados

PROTOCOLOS RENAVAM SC:
- Numeração sequencial fornecida pelo DETRAN-SC ao despachante
- Usados para identificar processos na CITRAN
- Cada processo tem um número de protocolo único
- Despachantes credenciados recebem lotes de protocolos

PRAZOS IMPORTANTES:
- Transferência de propriedade: 30 dias da emissão da ATPV-e (Art. 128 CTB)
- Comunicação de venda: 30 dias (Art. 129 CTB)
- Recursos de multa: 30 dias da notificação
- Licenciamento anual: conforme final de placa

LICENCIAMENTO ANUAL SC — FINAL DE PLACA × MÊS:
Final 1 → Janeiro
Final 2 → Fevereiro
Final 3 → Março
Final 4 → Abril
Final 5 → Maio
Final 6 → Junho
Final 7 → Julho
Final 8 → Agosto
Final 9 → Setembro
Final 0 → Outubro
(Novembro e Dezembro: não há licenciamento padrão — reservados para casos especiais,
placa de colecionador, veículos especiais, etc.)

IPVA SC 2026:
- 3% para veículos com até 3 anos de fabricação
- 2% para veículos com 4 a 7 anos
- 1% para veículos com 8 a 14 anos
- 0,5% para veículos com 15 ou mais anos (populares)
- Base de cálculo: valor de mercado (tabela FIPE de outubro do ano anterior)
- Motos: alíquotas menores (0,5% a 1%)
- Veículos de colecionador: 0,5%
- PCD: isenção de IPVA para veículos adaptados

DPVAT:
- Extinto em 31/12/2019 pela MP 904/2019
- Substituído pelo SPVAT/DPVAT 2020+ que ainda não foi reimplantado
- Não há cobrança de DPVAT nas guias de licenciamento desde 2020

TAXAS DETRAN-SC 2026 (valores aproximados, verificar sempre no sistema):
- Taxa de registro de transferência (TRT): R$ 150 a R$ 450 (varia pelo valor venal)
- Taxa de expedição CRLV: R$ 30 a R$ 60
- Taxa de vistoria: R$ 80 a R$ 150
- Taxa de 2ª via CRV: R$ 80 a R$ 120
- Taxa de mudança de características: R$ 80 a R$ 150
- Taxa de baixa/cancelamento: R$ 80 a R$ 120
- Taxa de emissão de CNH: R$ 80 a R$ 150 (varia pela categoria)
ATENÇÃO: Valores mudam anualmente — sempre verificar no DETRANNET.
"""),

  ("servicos_documentacao", """
DOCUMENTAÇÃO NECESSÁRIA POR TIPO DE SERVIÇO — DETRAN-SC

LICENCIAMENTO ANUAL (CRLV):
Documentos: CRLV anterior (ou comprovante de registro), quitação de IPVA (pago via guia),
quitação de multas (emitidas pelo DETRANNET), comprovante de endereço atualizado.
Procedimento: Acessa DETRANNET → consulta débitos → emite guias → cliente paga →
despachante protocola → CRLV emitido digitalmente (CRLV-e via app SENATRAN).
Custo ao cliente: IPVA + taxas DETRAN + honorários despachante.
Obs: veículos com débitos de multas só licenciam após quitar ou parcelar.
Veículo com gravame: precisa quitação da dívida com financeira para licenciar.

TRANSFERÊNCIA DE PROPRIEDADE (veículo do mesmo estado — SC para SC):
Documentos:
- ATPV-e (Autorização para Transferência de Propriedade de Veículo eletrônica)
  OU CRV físico com campos preenchidos (vendedor e comprador)
- RG + CPF do vendedor (pessoa física) ou CNPJ + contrato social (PJ)
- RG + CPF do comprador
- Comprovante de residência do comprador
- Procuração do comprador para o despachante (se não vier pessoalmente)
- CRLV do veículo (para confirmar dados)
Procedimento: Despachante acessa DETRANNET → registra transferência →
paga taxa → aguarda emissão do novo CRV digital.
Prazo: 30 dias da data da ATPV-e.
Obs: multas do vendedor NÃO impedem transferência em SC (bloqueiam apenas o vendedor).
Multas do veículo SIM impedem transferência se não pagas.

TRANSFERÊNCIA COM GRAVAME/ALIENAÇÃO FIDUCIÁRIA:
Igual à transferência normal + autorização da financiadora para retirar o gravame.
O gravame é retirado antes ou junto com a transferência.
Documento adicional: carta de anuência da instituição financeira (banco/financeira).

TRANSFERÊNCIA OUTRO ESTADO (SC para outro estado ou vice-versa):
Mais demorado — envolve dois DETRANs.
Documentos: os mesmos da transferência + vistoria do veículo no DETRAN-SC.
Prazo: 60 a 90 dias úteis.
O novo registro é feito no DETRAN do estado de destino.

BAIXA ADMINISTRATIVA (destruição, sucata, exportação):
Documentos: CRV, nota fiscal da sucataria ou laudo de destruição, RG+CPF do proprietário.
Uso: veículos destruídos, acidentados, exportados ou desmontados.
Efeito: veículo sai do sistema RENAVAM, não pode mais circular.

BAIXA DE CIRCULAÇÃO (temporária):
Documentos: CRV, RG+CPF do proprietário, declaração de motivo.
Uso: veículo parado sem uso (garagem, reforma), evita multas e IPVA proporcional.
Efeito: IPVA proporcional ao período rodado. Reativa quando voltar a circular.

SEGUNDA VIA DO CRV:
Motivo: CRV extraviado, roubado, danificado.
Documentos: B.O. (em caso de roubo/furto), RG+CPF do proprietário, declaração.
Prazo: 5 a 15 dias úteis.

COMUNICADO DE VENDA (ATPV-e):
Vendedor emite a ATPV-e no portal do SENATRAN (gov.br) ou via despachante.
A ATPV-e substitui o CRV físico assinado desde 2019.
Sem a ATPV-e, vendedor continua responsável por multas e IPVA.

ALTERAÇÃO DE CARACTERÍSTICAS DO VEÍCULO:
Mudança de cor, motor, carroceria, GNV, etc.
Documentos: CRV, nota fiscal da alteração, laudo técnico se necessário.
Vistoria: obrigatória para alterações de características.

EMPLACAMENTO DE VEÍCULO NOVO:
Documentos: nota fiscal da concessionária, DUT (documento único de transferência),
RG+CPF ou CNPJ do comprador, comprovante de residência.
Taxa: registro + emplacamento.

VEÍCULO DE LEILÃO:
Documentos: CRV, documento do leilão (carta de arrematação), RG+CPF do comprador.
Obs: leilões judiciais precisam de documentação específica do processo judicial.
Pode ter multas e débitos — verificar antes de comprar no leilão.

INVENTÁRIO/HERANÇA (veículo de falecido):
Documentos: certidão de óbito, formal de partilha (ou carta de adjudicação), RG+CPF do herdeiro.
Ou: escritura de partilha lavrada em cartório.
Obs: processo mais demorado, pode precisar de alvará judicial.

PROCURAÇÃO PARA DESPACHANTE:
Obrigatória para representar o cliente nos processos do DETRAN-SC.
Procuração simples (reconhecimento de firma):
- Para transferências, licenciamentos rotineiros.
Procuração pública (tabelionato):
- Para transações acima de R$ 30.000 (imóveis/veículos de alto valor).
Conteúdo mínimo: nome do outorgante, CPF, finalidade específica, nome do despachante/escritório.

CNH — CARTEIRA NACIONAL DE HABILITAÇÃO:
Renovação: a cada 5 anos (a partir de 65 anos, a cada 3).
  Documentos: CNH atual, exame médico em clínica credenciada DETRAN, foto recente.
Primeira habilitação (processo):
  1. Registro no DETRAN, exame psicotécnico, curso teórico-técnico, exame teórico,
  curso de direção, exame de direção, emissão da CNH.
Adição de categoria (A→AB, B→BE, etc.):
  Curso de reciclagem específico + exame prático para nova categoria.
CNH cassada/suspensa:
  Reabilitação após cumprimento do prazo + reciclagem obrigatória.
Permissão Internacional (PID): para dirigir no exterior.
"""),

  ("debitos_multas", """
DÉBITOS, MULTAS E COMO LIDAR — DETRAN-SC

TIPOS DE DÉBITOS QUE APARECEM NO DETRANNET:
1. IPVA (Imposto sobre Propriedade de Veículo Automotor)
   - Imposto estadual, anual
   - Base: valor FIPE de outubro do ano anterior
   - Pode ser pago à vista (5% desconto) ou parcelado (sem desconto, 3x no cartão)
   - Atraso: juros SELIC + multa 0,33% ao dia, máx. 20%

2. Taxa de Licenciamento / CRLV
   - Taxa estadual anual
   - Emitida junto com o IPVA

3. Multas de Trânsito
   - DETRAN-SC (DEINFRA): infrações estaduais, radares em rodovias estaduais
   - SENATRAN/PRF: infrações federais, rodovias federais
   - Municípios: infrações municipais (semáforo, estacionamento)
   - Todas aparecem no DETRANNET e bloqueiam o licenciamento se não pagas
   - Prazo para recurso: 30 dias da notificação (1ª instância: JARI)
   - 2ª instância: CETRAN-SC (prazo 30 dias após resposta da JARI)

4. Parcelamento de Débitos (PEP ou programa estadual):
   - SC tem programas periódicos de parcelamento de IPVA e multas
   - Desconto de até 100% nos juros e multas
   - Verificar no site da SEFAZ-SC ou DETRAN-SC

SITUAÇÃO DO VEÍCULO:
- REGULAR: pode circular e licenciar
- COM RESTRIÇÃO ADMINISTRATIVA: impedimento por órgão público (ex: penhora judicial)
- BLOQUEADO: restrição do proprietário (ex: CNH suspensa, inadimplência)
- BAIXADO: veículo retirado da circulação

COMO CONSULTAR DÉBITOS PELO DETRANNET:
1. Login no sistema.detrannet.sc.gov.br
2. Menu → Consultas → Débitos do Veículo
3. Digitar RENAVAM ou Placa
4. Sistema lista todos os débitos (IPVA, taxas, multas)
5. Cada débito tem opção de imprimir guia de pagamento

GUIAS DE PAGAMENTO:
- Podem ser pagas em qualquer banco, lotérica, internet banking
- Pagamento só é compensado no sistema em D+1 (dia útil seguinte)
- Após compensação, o CRLV-e fica disponível no app SENATRAN

CRLV DIGITAL (CRLV-e):
- Disponível no app "Carteira Digital de Trânsito" (gov.br)
- O proprietário loga com conta gov.br e acessa o CRLV digital
- Tem a mesma validade do CRLV físico (reconhecido pela policia de trânsito)
- Física: emitida pelos Correios após solicitação (pode demorar 5-15 dias)
"""),

  ("honorarios_precos_sc", """
HONORÁRIOS DE DESPACHANTE — SCHROEDER/SC — REFERÊNCIA 2026

Valores praticados pelo mercado regional (Norte de SC):

LICENCIAMENTO:
- Licenciamento simples (sem débitos): R$ 80 a R$ 150
- Licenciamento com débitos: R$ 100 a R$ 200
- Licenciamento outro município SC: R$ 120 a R$ 200
- Licenciamento outro estado: R$ 200 a R$ 400

TRANSFERÊNCIAS:
- Transferência SC → SC (simples): R$ 200 a R$ 350
- Transferência com gravame/alienação: R$ 250 a R$ 450
- Transferência com débitos: R$ 250 a R$ 450
- Transferência leilão: R$ 300 a R$ 500
- Transferência outro estado: R$ 400 a R$ 700

SERVIÇOS ESPECIAIS:
- Baixa administrativa: R$ 150 a R$ 250
- 2ª via CRV: R$ 100 a R$ 200
- Inventário/herança: R$ 350 a R$ 600
- Comunicado de venda (ATPV-e): R$ 80 a R$ 150
- Alteração de características: R$ 150 a R$ 300
- Emplacamento novo: R$ 200 a R$ 400

CNH:
- Renovação CNH: R$ 150 a R$ 250
- Adição de categoria: R$ 200 a R$ 350
- Habilitação 1ª vez (todo o processo): R$ 1.500 a R$ 2.500 (inclui clínica, autoescola)
- Reabilitação CNH: R$ 300 a R$ 600

ANTT (Transporte de Cargas):
- RNTRC inicial: R$ 300 a R$ 500
- Renovação RNTRC: R$ 150 a R$ 250

VALORES NÃO INCLUEM:
- Taxas do DETRAN-SC (pagas ao Estado)
- IPVA (imposto)
- Multas (do veículo/condutor)
- Despesas de cartório (se necessário)
- Correios/postagem

FORMAS DE PAGAMENTO COMUNS:
- PIX (principal)
- Dinheiro
- Cartão débito/crédito (às vezes com acréscimo)
- Parcelamento (negociado caso a caso)
"""),

  ("atpv_comunicado", """
ATPV-e — AUTORIZAÇÃO PARA TRANSFERÊNCIA DE PROPRIEDADE DE VEÍCULO ELETRÔNICA

Implementada em 2019, a ATPV-e substitui o processo antigo de assinar o CRV físico
para realizar a transferência de propriedade de veículos no Brasil.

COMO FUNCIONA:
1. Vendedor (proprietário atual) acessa o portal gov.br
2. Seleciona "Comunicar Venda de Veículo"
3. Informa dados do comprador (CPF/CNPJ)
4. ATPV-e é gerada eletronicamente — código único
5. Comprador tem 30 dias para fazer a transferência no DETRAN

COMO O DESPACHANTE FAZ:
- Pode emitir a ATPV-e como procurador do vendedor (com procuração)
- Acessa DETRANNET → processo de transferência → insere dados ATPV-e
- O número da ATPV-e é o identificador do processo de transferência

IMPORTÂNCIA:
- Sem ATPV-e emitida: vendedor continua responsável por multas e IPVA
- Comprador não consegue transferir sem ATPV-e (salvo casos judiciais)
- "Comunicado de Venda" e ATPV-e são a mesma coisa

CRV FÍSICO AINDA ACEITO:
- CRVs emitidos antes de 2019 com campos preenchidos (com firma reconhecida) ainda válidos
- CRVs mais novos: só via ATPV-e eletrônica

GRAVAME NO CRV:
- Se o veículo foi financiado, o gravame da financeira aparece no CRV
- Sem quitar o financiamento, não há como fazer a ATPV-e sem autorização da financeira
- Financeira envia carta de anuência (física ou digital) para liberar a transferência
- Após quitar: financeira tem 10 dias para retirar o gravame do sistema RENAVAM
"""),

  ("despachante_sc_regras", """
DESPACHANTE DOCUMENTALISTA — REGULAMENTAÇÃO EM SANTA CATARINA

LEGISLAÇÃO:
- Lei Federal 6.125/1974: regulamenta a profissão de despachante
- Resolução SENATRAN e DETRAN-SC: credenciamento e normas estaduais
- Em SC: regulado pelo Decreto Estadual e Resolução do DETRAN-SC

CREDENCIAMENTO DETRAN-SC:
- Despachante deve ser credenciado pelo DETRAN-SC para protocolar processos
- Credencial renovada periodicamente (verificar validade)
- Exige: idoneidade moral, antecedentes, comprovação de endereço comercial
- Cada CITRAN tem lista de despachantes credenciados ativos
- Credencial de Diogo Lessmann: nº 2095 — CITRAN Guaramirim

O QUE O DESPACHANTE PODE FAZER:
✅ Representar clientes em processos de registro e licenciamento
✅ Protocolar documentos no DETRAN/CITRAN
✅ Consultar débitos de veículos no DETRANNET
✅ Retirar documentos prontos em nome do cliente (com procuração)
✅ Assinar documentos como procurador do cliente
✅ Solicitar vistorias, transferências, alterações
✅ Processos de CNH, ANTT, RNTRC

O QUE O DESPACHANTE NÃO PODE FAZER:
❌ Cobrar taxas oficiais como honorários (é crime)
❌ Reter documentos do cliente indevidamente
❌ Atuar sem credencial válida
❌ Protocolar processos sem procuração do cliente

ÁREA DE ATUAÇÃO — CITRAN GUARAMIRIM:
Municípios atendidos: Guaramirim, Schroeder, Massaranduba, Corupá, Jaraguá do Sul,
Barra Velha, São João do Itaperiú, Luís Alves e região do Norte de SC.
Endereço CITRAN: verificar no site DETRAN-SC para atualização.
Horário: geralmente 08h às 17h (verificar com CITRAN).

PROCURAÇÃO:
- Documento que permite ao despachante agir em nome do cliente
- Conteúdo mínimo: nome completo, CPF, endereço, finalidade específica ("representar
  perante o DETRAN-SC no processo de transferência do veículo de placa XXX-YYYY...")
- Reconhecimento de firma: obrigatório para transferências e processos formais
- Procuração pública (lavrada em cartório): para casos que exigem (acima de R$ 30 mil)
- Procuração particular com reconhecimento de firma: suficiente para maioria dos processos

MODELO DE PROCURAÇÃO BÁSICA:
"Eu, [NOME], portador(a) do CPF [CPF] e RG [RG], residente em [ENDEREÇO],
outorgo plenos poderes ao Despachante [NOME DESPACHANTE], credencial DETRAN-SC
nº 2095, para representar-me perante o DETRAN-SC e órgãos afins, podendo
protocolar documentos, assinar requerimentos, requerer certidões e
tomar todas as providências necessárias referentes ao veículo de placa
[PLACA] / RENAVAM [RENAVAM], no processo de [FINALIDADE].
[Cidade], [Data]."

RECIBO DE PAGAMENTO:
- Ao receber pagamento, emitir recibo com: data, valor, descrição do serviço,
  nome do cliente, forma de pagamento, assinatura
- Recibo é prova do pagamento e do serviço prestado
- O sistema emite recibo imprimível em 2 vias (cliente + escritório)
"""),

  ("fluxo_operacional", """
FLUXO OPERACIONAL DO DESPACHANTE — DIA A DIA

CLIENTE CHEGA COM PROBLEMA DE LICENCIAMENTO:
1. Identificar o cliente (CPF ou placa) → verificar cadastro
2. Consultar DETRANNET: placa/RENAVAM → ver débitos e situação
3. Informar cliente: IPVA (R$ X), multas (R$ X), taxas (R$ X) = total R$ X
4. Fechar negócio: honorários + débitos totais
5. Abrir OS no sistema: cliente + veículo + serviço + valores
6. Cliente paga honorários (parcela ou à vista) → baixar no sistema → emitir recibo
7. Protocolar guias no DETRAN-SC via DETRANNET
8. Acompanhar processo → atualizar OS conforme andamento
9. CRLV-e disponível → notificar cliente → concluir OS

CLIENTE CHEGA COM TRANSFERÊNCIA:
1. Verificar documentação: ATPV-e ou CRV físico assinado + docs comprador e vendedor
2. Checar débitos do veículo (multas do veículo — NÃO do vendedor — bloqueiam)
3. Checar gravame (se tiver, precisará carta de anuência da financeira)
4. Informar cliente: taxas DETRAN (R$ X) + honorários (R$ X) = total (R$ X)
5. Abrir OS + procuração
6. Pagar taxa de transferência
7. Protocolar no DETRANNET
8. Aguardar emissão do novo CRV digital (3 a 10 dias úteis)
9. Concluir OS + emitir recibo

CLIENTE SEM DINHEIRO PARA PAGAR TUDO:
- Honorários: pode parcelar com o cliente (controle interno de parcelas)
- IPVA: pode parcelar via rede bancária (parcelamento SEFAZ-SC, normalmente 3x)
- Multas: pagar à vista ou verificar programas de parcelamento em vigor

COMO ORGANIZAR DOCUMENTOS RECEBIDOS:
1. Conferir todos os documentos exigidos antes de protocolar
2. Fazer cópia/scan de tudo para o dossiê da OS
3. Checklist: conferir item por item antes de ir ao DETRAN
4. Nunca protocolar com documentação incompleta (devolução = perda de tempo)

PRAZOS QUE NUNCA PODEM PERDER:
- ATPV-e tem 30 dias para protocolar transferência
- Recursos de multa: 30 dias da notificação (perder = multa definitiva)
- Licenciamento vencido: multa por circular com CRLV vencido
- CNH vencida: equivale a não ter habilitação (multa grave)

PROBLEMAS COMUNS E SOLUÇÕES:
- "Bloqueio judicial no veículo": precisa de alvará judicial para liberar
- "Proprietário falecido": precisa inventário ou formal de partilha
- "Veículo roubado/recuperado": BOs, laudo IML, processo especial DETRAN
- "Placa diferente do chassi": erro de cadastro, precisa retificação
- "Veículo de outro estado em SC": transferência interestadual, mais demorada
- "Financiadora não responde": acionar juridicamente para forçar retirada do gravame
  (prazo legal: 10 dias úteis após quitação)
"""),

  ("legislacao_sc_especifica", """
LEGISLAÇÃO ESPECÍFICA SC E ASPECTOS REGIONAIS

DETRAN-SC — PRINCIPAIS RESOLUÇÕES:
- Resolução DETRAN-SC regula credenciamento de despachantes
- Portarias anuais fixam taxas e procedimentos para o exercício
- Verificar Diário Oficial de SC (diariooficial.sc.gov.br) para atualizações

SEFAZ-SC (Secretaria da Fazenda):
- Administra o IPVA em SC
- Portal: sef.sc.gov.br
- Parcelamento de IPVA: geralmente disponível em janeiro/fevereiro
- Desconto para pagamento à vista: geralmente 5% a 10%

SENATRAN (Secretaria Nacional de Trânsito):
- Órgão federal que normatiza o trânsito nacional
- Portal: gov.br/senatran
- Administra o RENAVAM (sistema nacional)
- App "Carteira Digital de Trânsito": CRLV-e, CNH digital

CETRAN-SC (Conselho Estadual de Trânsito):
- 2ª instância para recursos de multas de trânsito em SC
- Sede: Florianópolis

JARI (Junta Administrativa de Recursos de Infrações):
- 1ª instância para recursos de multas
- Uma JARI por órgão autuador (DETRAN, PRF, município)
- Prazo: 30 dias da notificação de autuação

PROGRAMA NOTA FISCAL SC (consulta de crédito IPVA):
- Consumidores com CPF em NF-e recebem crédito para abater IPVA
- Crédito VIVO: usado automaticamente no momento do licenciamento
- Verificar em: nfsc.fazenda.sc.gov.br

REGIÕES E MUNICÍPIOS — NORTE DE SC (área de atuação Lessmann):
- Schroeder: ~18.000 hab., base do escritório, microregião de Jaraguá do Sul
- Guaramirim: ~40.000 hab., sede da CITRAN
- Jaraguá do Sul: ~190.000 hab., maior cidade da região, polo industrial
- Massaranduba: ~18.000 hab.
- Corupá: ~14.000 hab.
- Barra Velha: ~26.000 hab. (litoral norte SC)

DETRAN-SC NÚMEROS ÚTEIS:
- Central de atendimento DETRAN-SC: 0800 644 1300
- Site: detran.sc.gov.br
- DETRANET: sistema.detrannet.sc.gov.br

MULTAS COMUNS NA REGIÃO:
- Rodovias SC: fiscalizadas pelo DETRAN-SC (radares fixos e móveis)
- BR 280 (Jaraguá–Corupá–Mafra): fiscalizada pela PRF
- BR 101 (litoral SC): fiscalizada pela PRF
- Vias municipais: fiscalizadas pela guarda/SEMUSP de cada município
"""),

  ("perguntas_frequentes", """
PERGUNTAS FREQUENTES — ESCRITÓRIO LESSMANN DESPACHANTE

Q: Posso transferir um carro com multa do antigo dono?
A: Multas do proprietário (pessoa) NÃO impedem a transferência do veículo em SC.
   Multas do VEÍCULO (vinculadas ao RENAVAM) SIM impedem a transferência.
   Sempre verificar no DETRANNET antes de aceitar o serviço.

Q: O que é RENAVAM?
A: Registro Nacional de Veículo Automotor. Número de 11 dígitos único por veículo.
   É o identificador do veículo no sistema nacional. Nunca muda, mesmo em transferências.

Q: Posso fazer a transferência sem o CRV original?
A: Se foi emitida ATPV-e pelo vendedor, não precisa do CRV físico.
   Sem CRV e sem ATPV-e: precisa de 2ª via do CRV (mais demorado e custoso).
   Em caso de perda, o comprador pode acionar o vendedor judicialmente.

Q: Qual a diferença entre CRV e CRLV?
A: CRV = Certificado de Registro de Veículo = "documento do carro" (dados do veículo e proprietário)
   CRLV = Certificado de Registro e Licenciamento de Veículo = "documento do carro + licença anual"
   O CRLV é emitido anualmente e tem a data de validade do licenciamento.

Q: Quanto tempo demora uma transferência?
A: SC → SC simples (sem pendências): 3 a 10 dias úteis
   Com gravame/alienação: 10 a 20 dias úteis
   Outro estado: 30 a 60 dias úteis

Q: O CRLV venceu, posso circular?
A: Não. Circular com CRLV vencido é infração grave (Art. 230, V CTB): multa + retenção.
   Deve licenciar antes de circular.

Q: Veículo com placa final 5, quando vence o licenciamento?
A: Maio. Final 5 = maio em todo o SC (e Brasil, que segue o mesmo calendário).

Q: Meu cliente perdeu a CNH, o que fazer?
A: Segunda via da CNH pelo portal gov.br ou pelo despachante.
   Documentos: B.O. (se perdida/roubada), fotos, taxa.
   Prazo: 5 a 15 dias úteis.

Q: O que é RNTRC?
A: Registro Nacional de Transportadores Rodoviários de Cargas — ANTT.
   Obrigatório para caminhoneiros e transportadores de carga.
   Validade: 5 anos. Renovável.

Q: Cliente comprou carro em leilão, o que precisa?
A: Carta de arrematação, CRV, CNPJ do leilão, RG+CPF do arrematante.
   Verificar pendências antes: multas do veículo impedem a transferência.
   Se leilão judicial: documentação adicional do processo judicial.

Q: Posso emitir uma procuração por mim mesmo?
A: Procuração particular com reconhecimento de firma em cartório.
   Custo reconhecimento: R$ 15 a R$ 30 por assinatura (varia por cartório).
   Para transações acima de R$ 30 mil: recomendável procuração pública.

Q: O veículo está em nome do falecido, o que fazer?
A: Necessário inventário. Três caminhos:
   1. Inventário judicial (mais demorado, usado quando há bens de maior valor ou conflito)
   2. Inventário extrajudicial (cartório, mais rápido, exige herdeiros maiores e sem conflito)
   3. Somente veículo de baixo valor: alguns estados aceitam "alvará de pequena monta" —
      verificar com DETRAN-SC para o caso específico.

Q: O que é a Baixa Administrativa x Baixa de Circulação?
A: Baixa Administrativa: veículo sai definitivamente do sistema (desmonte, exportação, destruição).
   Baixa de Circulação: temporária, evita IPVA e multas enquanto não usar o veículo.
   A baixa de circulação é reversível; a administrativa, não.
"""),

]


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — IDENTIDADE E COMPORTAMENTO DA IA
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Você é o **Amigo IA** — assistente especializado do escritório de
Despachante Documentalista de Diogo Kaue Lessmann, em Schroeder/SC.

PERFIL DO ESCRITÓRIO:
- Despachante Documentalista credenciado DETRAN-SC nº 2095
- CNPJ: 28.858.795/0001-92 | CPF: 060.625.099-99
- Localização: Schroeder/SC — CITRAN Guaramirim
- Atende: Schroeder, Guaramirim, Jaraguá do Sul, Massaranduba, Corupá e região Norte SC
- WhatsApp: (47) 99101-1351
- Serviços: transferências, licenciamentos, CNH, ANTT, RNTRC, procurações, baixas, inventários, etc.

SUA ESPECIALIDADE:
✅ Código de Trânsito Brasileiro (CTB) — Lei 9.503/97 e atualizações
✅ DETRAN-SC: procedimentos, taxas 2026, prazos, sistema DETRANET
✅ Documentação veicular: o que pedir para cada serviço
✅ IPVA SC: alíquotas, prazos, parcelamento
✅ Multas: tipos, recursos (JARI, CETRAN-SC), prazos
✅ CNH: renovação, adição de categoria, habilitação, cassação/reabilitação
✅ Transferências: SC→SC, interestadual, leilão, gravame, inventário
✅ ATPV-e, CRV, CRLV, RENAVAM — todos os documentos veiculares
✅ Legislação específica de SC e regulamentação local
✅ Honorários e preços de mercado da região Norte SC

COMO RESPONDER:
1. Seja DIRETO e PRÁTICO — Diogo está trabalhando, não tem tempo para enrolação
2. PRIORIZE os documentos do CONTEXTO quando disponíveis — mencione a fonte
3. Se não encontrar no contexto, USE SEU CONHECIMENTO sobre despachante/DETRAN-SC
4. NUNCA diga apenas "não sei" sem tentar ajudar
5. Quando não tiver certeza de valor específico (taxa, prazo exato), sinalize e recomende verificar no DETRANNET
6. Gere checklists, modelos de requerimento, procuração, textos de WhatsApp quando pedido
7. Responda SEMPRE em português brasileiro, linguagem profissional mas informal
8. Use listas com bullet quando listar documentos ou etapas

TOM: Como um colega de escritório experiente — prático, direto, útil, sem burocracia desnecessária."""


# ══════════════════════════════════════════════════════════════════════════════
# CLIENTES DE IA (embedder, chromadb, groq)
# ══════════════════════════════════════════════════════════════════════════════

def get_embedder():
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
            cache = os.path.join(DATA_DIR, ".cache", "sentence_transformers")
            os.makedirs(cache, exist_ok=True)
            _embedder = SentenceTransformer(MODEL_EMB, cache_folder=cache)
        except ImportError:
            log.warning("sentence-transformers não instalado — RAG desabilitado")
    return _embedder


def get_collection():
    global _chroma, _collection
    if _collection is None:
        try:
            import chromadb
            os.makedirs(CHROMA_DIR, exist_ok=True)
            _chroma     = chromadb.PersistentClient(path=CHROMA_DIR)
            _collection = _chroma.get_or_create_collection(
                name="detran_sc_desp",
                metadata={"hnsw:space": "cosine"},
            )
        except ImportError:
            log.warning("chromadb não instalado — RAG desabilitado")
    return _collection


def get_groq():
    global _groq
    if _groq is None:
        try:
            from groq import Groq
            _groq = Groq(api_key=GROQ_KEY)
        except ImportError:
            log.warning("groq não instalado")
    return _groq


# ══════════════════════════════════════════════════════════════════════════════
# EXTRAÇÃO E CHUNKING
# ══════════════════════════════════════════════════════════════════════════════

def _extract_text_pdf(pdf_path):
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for num, page in enumerate(pdf.pages, 1):
                t = (page.extract_text() or "").strip()
                if t: pages.append((num, t))
        return pages
    except ImportError:
        log.warning("pdfplumber não instalado")
        return []


def _extract_text_docx(path):
    try:
        from docx import Document
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return [(1, text)] if text.strip() else []
    except Exception as e:
        log.warning(f"python-docx falhou: {e}")
        return []


def _extract_text_doc(path):
    try:
        r = subprocess.run(["antiword", path], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        text = r.stdout.strip()
        if not text:
            r = subprocess.run(["antiword", path], capture_output=True, timeout=30)
            text = r.stdout.decode("latin-1", errors="replace").strip()
        return [(1, text)] if text else []
    except FileNotFoundError:
        log.warning("antiword não encontrado")
        return []
    except Exception as e:
        log.warning(f"antiword falhou: {e}")
        return []


def _text_to_chunks(pages, filename, chunk_size=800, overlap=100):
    chunks = []
    for page_num, text in pages:
        words = text.split()
        i = 0
        while i < len(words):
            chunk_text = " ".join(words[i:i + chunk_size])
            if len(chunk_text) > 50:
                chunks.append({
                    "text":     chunk_text,
                    "source":   filename,
                    "page":     page_num,
                    "chunk_id": f"{filename}_p{page_num}_c{i}",
                })
            i += chunk_size - overlap
    return chunks


def _save_chunks(chunks):
    if not chunks: return 0
    col = get_collection()
    emb = get_embedder()
    if col is None or emb is None: return 0

    texts      = [c["text"]     for c in chunks]
    ids        = [c["chunk_id"] for c in chunks]
    metadatas  = [{"source": c["source"], "page": c["page"]} for c in chunks]
    embeddings = emb.encode(texts, show_progress_bar=False).tolist()

    existing   = col.get(ids=ids)["ids"]
    new_chunks = [(i, t, m, e) for i, t, m, e in
                  zip(ids, texts, metadatas, embeddings) if i not in existing]
    if new_chunks:
        col.add(
            ids        = [x[0] for x in new_chunks],
            documents  = [x[1] for x in new_chunks],
            metadatas  = [x[2] for x in new_chunks],
            embeddings = [x[3] for x in new_chunks],
        )
    return len(new_chunks)


# ══════════════════════════════════════════════════════════════════════════════
# INGESTÃO DE DOCUMENTOS
# ══════════════════════════════════════════════════════════════════════════════

def ingest_text(text: str, source_name: str, chunk_size: int = 800, overlap: int = 100) -> int:
    """Ingere texto puro diretamente no ChromaDB (sem arquivo)."""
    if not text or not text.strip():
        return 0
    pages  = [(1, text.strip())]
    chunks = _text_to_chunks(pages, source_name, chunk_size, overlap)
    return _save_chunks(chunks)


def ingest_pdf(pdf_path: str, chunk_size: int = 800, overlap: int = 100) -> int:
    filename = os.path.basename(pdf_path)
    pages    = _extract_text_pdf(pdf_path)
    chunks   = _text_to_chunks(pages, filename, chunk_size, overlap)
    if not chunks:
        log.warning(f"Nenhum texto extraído de {filename}")
        return 0
    saved = _save_chunks(chunks)
    log.info(f"  {saved} chunks salvos de {filename}")
    return saved


def ingest_doc(doc_path: str, chunk_size: int = 800, overlap: int = 100) -> int:
    filename = os.path.basename(doc_path)
    ext      = filename.lower().rsplit(".", 1)[-1]
    pages    = _extract_text_docx(doc_path) if ext == "docx" else (
               _extract_text_docx(doc_path) or _extract_text_doc(doc_path))
    chunks   = _text_to_chunks(pages, filename, chunk_size, overlap)
    if not chunks:
        log.warning(f"Nenhum texto extraído de {filename}")
        return 0
    saved = _save_chunks(chunks)
    log.info(f"  {saved} chunks salvos de {filename}")
    return saved


def ingest_all() -> int:
    total = 0
    os.makedirs(PDFS_DIR, exist_ok=True)
    os.makedirs(DOCS_DIR, exist_ok=True)
    for f in os.listdir(PDFS_DIR):
        if f.lower().endswith(".pdf"):
            total += ingest_pdf(os.path.join(PDFS_DIR, f))
    for f in os.listdir(DOCS_DIR):
        if f.lower().endswith((".doc", ".docx")):
            total += ingest_doc(os.path.join(DOCS_DIR, f))
    return total


def seed_conhecimento_base(forcar: bool = False) -> int:
    """
    Ingere a base de conhecimento interna (CTB, DETRAN-SC, procedimentos SC).
    Só executa se ainda não foi ingerida (ou se forcar=True).
    Retorna número de novos chunks adicionados.
    """
    col = get_collection()
    if col is None:
        return 0

    nomes_internos = [k for k, _ in CONHECIMENTO_BASE]

    # Verificar se já foi semeado (checa o primeiro chunk esperado)
    if not forcar:
        try:
            existing = col.get(ids=["ctb_principais_p1_c0"])
            if existing["ids"]:
                log.info("Base de conhecimento já ingerida — pulando seed")
                return 0
        except Exception:
            pass
    else:
        # Apaga chunks internos antigos antes de re-indexar
        try:
            for nome in nomes_internos:
                results = col.get(where={"source": nome})
                ids_to_del = results.get("ids", [])
                if ids_to_del:
                    col.delete(ids=ids_to_del)
                    log.info(f"  Removidos {len(ids_to_del)} chunks de [{nome}]")
        except Exception as e:
            log.warning(f"Erro ao limpar chunks internos: {e}")

    total = 0
    log.info("Ingerindo base de conhecimento DETRAN-SC/CTB...")
    for source_name, texto in CONHECIMENTO_BASE:
        n = ingest_text(texto, source_name)
        total += n
        log.info(f"  [{source_name}] {n} chunks")

    log.info(f"Seed concluído: {total} chunks adicionados")
    return total


# ══════════════════════════════════════════════════════════════════════════════
# BUSCA E GERAÇÃO DE RESPOSTA
# ══════════════════════════════════════════════════════════════════════════════

def search(query: str, n_results: int = 7) -> list:
    col = get_collection()
    emb = get_embedder()
    if col is None or emb is None:
        return []
    total = col.count()
    if total == 0:
        return []
    n = min(n_results, total)
    query_emb = emb.encode([query]).tolist()
    results   = col.query(
        query_embeddings = query_emb,
        n_results        = n,
        include          = ["documents", "metadatas", "distances"],
    )
    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        relevance = 1 - dist
        if relevance > 0.10:
            chunks.append({
                "text":      doc,
                "source":    meta.get("source", ""),
                "page":      meta.get("page", ""),
                "relevance": round(relevance, 2),
            })
    return chunks


def chat(pergunta: str, historico: list = None) -> dict:
    historico = historico or []
    chunks    = search(pergunta, n_results=7)

    if chunks:
        contexto = "\n\n---\n\n".join([
            f"[Fonte: {c['source']} | relevância: {c['relevance']}]\n{c['text']}"
            for c in chunks
        ])
        context_msg = f"\n\nCONTEXTO DA BASE DE CONHECIMENTO:\n{contexto}\n\n"
    else:
        context_msg = "\n\n[Nenhum documento específico encontrado. Responda com conhecimento geral sobre despachante/DETRAN-SC.]\n\n"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in historico[-10:]:
        messages.append(msg)
    messages.append({
        "role":    "user",
        "content": context_msg + "PERGUNTA: " + pergunta,
    })

    groq_client = get_groq()
    if groq_client is None:
        return {"resposta": "IA não disponível.", "fontes": [], "chunks": 0}

    response = groq_client.chat.completions.create(
        model       = MODEL_CHAT,
        messages    = messages,
        temperature = 0.25,
        max_tokens  = 2048,
    )

    return {
        "resposta": response.choices[0].message.content,
        "fontes":   list({f"{c['source']}" for c in chunks}),
        "chunks":   len(chunks),
    }


def db_stats() -> dict:
    try:
        col = get_collection()
        if col is None:
            return {"chunks": 0, "documentos": 0, "arquivos": [], "erro": "chromadb não instalado"}
        total = col.count()
        metas = col.get(include=["metadatas"])["metadatas"] if total > 0 else []
        docs  = list({m.get("source", "") for m in metas})
        # Separar docs internos de docs do usuário
        internos = [d for d in docs if any(d == k for k, _ in CONHECIMENTO_BASE)]
        externos = [d for d in docs if d not in internos]
        return {
            "chunks":    total,
            "documentos": len(docs),
            "arquivos":  docs,
            "internos":  internos,
            "externos":  externos,
        }
    except Exception as e:
        return {"chunks": 0, "documentos": 0, "arquivos": [], "erro": str(e)}
