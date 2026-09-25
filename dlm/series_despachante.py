# -*- coding: utf-8 -*-
"""📚 SÉRIES DO DESPACHANTE — banco de conteúdo por JORNADA (15/set/2026).

Pedido do dono (14/set): "subir de 1-2 pra 3-4 posts/dia, criar temas (VOU TROCAR DE CARRO →
consultar, leilão, débitos, ATPV, vistoria, vir no despachante), LICENCIAMENTO, DEFESA DE MULTA...
fiel ao DETRAN/SC, levar as pessoas pro site ('veja mais no site'). O que NÃO pode: ensinar a
pagar débito / licenciar sozinho."

Regras fixas (ver memória feedback_dl_forma_branca_cnh — episódio ADEVI 14/set):
- CNH: o despachante NÃO renova, NÃO emite, NÃO dá curso. ORIENTA no gov.br/DETRAN e REPRESENTA
  curso homologado (reciclagem/MOPP). Nunca "renovamos", "fazemos sua CNH", "nosso curso".
- Transferência, licenciamento, débitos, defesa, indicação de condutor: atribuição do despachante
  credenciado (DETRAN/SC 2095) → aqui pode "a gente faz por você".
- Post mostra o PROBLEMA e a CONSEQUÊNCIA; o SITE dá o próximo passo; o próximo passo é mandar a
  placa no zap. Nunca tutorial de pagamento/emissão.
- Sem brasão do DETRAN, sem "garantimos", sem depoimento inventado, sem dado de terceiro.

Cada item: cat · titulo · bullets (3, curtos: o card quebra em 2 linhas por bullet) · fonte
(URL oficial que sustenta o fato — o checador usa) · opcional: formato ('dica' | 'mito' |
'consequencia'), link (sobrescreve o da série).

Cada série: nome (badge na capa), link (página do site), pagina (título humano da página),
cta_seal / cta_big (slide final), passos (lista de itens — a rotação anda um passo por dia).
"""

SITE = "https://dldespachante.com.br"
ZAP = "(47) 99716-2967"

# ─────────────────────────────────────────────────────────────────────────────
SERIES = {
    # ═══════════════ 1. VOU TROCAR DE CARRO (a jornada pedida pelo dono) ═══════════════
    "trocar_carro": {
        "nome": "VOU TROCAR DE CARRO",
        "link": "/consultar-debitos-veiculo-sc",
        "pagina": "Consultar débitos do veículo pela placa",
        "cta_seal": "MANDA A PLACA",
        "cta_big": ["A GENTE CONSULTA", "PRA VOCÊ"],
        "passos": [
            {"cat": "PASSO 1 · ANTES DE PAGAR",
             "titulo": "Vai comprar carro usado? Consulta a placa ANTES do PIX",
             "bullets": ["Débito, multa e restrição vêm junto com o carro — não com o dono.",
                         "Leilão e sinistro não aparecem no anúncio, aparecem no histórico.",
                         "Manda a placa no zap: a gente levanta tudo antes de você fechar."],
             "fonte": "https://www.detran.sc.gov.br/"},
            {"cat": "PASSO 2 · LEILÃO",
             "titulo": "Carro de leilão: o preço bom que vira dor de cabeça",
             "bullets": ["Leilão e sinistro não aparecem no DETRAN nem no anúncio: só no histórico especializado.",
                         "Seguradora pode recusar ou cobrar mais caro, e a revenda perde valor.",
                         "Antes de comprar, a gente puxa o histórico completo pra você."],
             "fonte": SITE + "/consultar-debitos-veiculo-sc"},
            {"cat": "PASSO 3 · DÉBITOS",
             "titulo": "Comprou com débito? Agora o débito é seu",
             "bullets": ["IPVA, licenciamento e multa atrasados travam a transferência.",
                         "Sem transferir, o carro continua no nome do antigo dono.",
                         "A gente levanta o valor exato antes de você negociar."],
             "fonte": "https://www.sef.sc.gov.br/"},
            {"cat": "PASSO 4 · ATPV-e",
             "titulo": "Fechou negócio? Você tem 30 dias pra transferir",
             "bullets": ["O CTB (art. 123) dá 30 dias pra transferir após a compra.",
                         "Passou do prazo: infração média, 4 pontos e remoção do veículo (art. 233).",
                         "ATPV-e, vistoria e transferência: a gente faz por você."],
             "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
            {"cat": "VENDEU? · COMUNICAÇÃO DE VENDA",
             "titulo": "Vendeu o carro e chegou multa no seu nome? O carro ainda é 'seu' pro DETRAN",
             "bullets": ["Sem a transferência, o antigo dono segue respondendo por multas e pontos.",
                         "A comunicação de venda encerra sua responsabilidade dali em diante.",
                         "A gente faz a comunicação por você: manda placa e data da venda."],
             "fonte": "https://www.detran.sc.gov.br/"},
            {"cat": "PASSO 5 · VISTORIA",
             "titulo": "Vistoria reprovou? Não é o fim do negócio",
             "bullets": ["Chassi, motor e itens obrigatórios precisam bater com o cadastro.",
                         "Reprovou: a gente te diz o que ajustar e reagenda.",
                         "Você não precisa descobrir isso sozinho no balcão."],
             "fonte": "https://www.detran.sc.gov.br/"},
            {"cat": "PASSO 6 · FECHAMENTO",
             "titulo": "Transferência à vista no PIX: condição especial no escritório",
             "bullets": ["Consulta, ATPV-e, vistoria e transferência num lugar só.",
                         "Honorário combinado antes; taxa oficial em guia no seu nome.",
                         "Mal. Castelo Branco, 2838, Schroeder — ou tudo pelo zap."],
             "fonte": SITE + "/transferencia-de-veiculo-assinatura"},
        ],
    },

    # ═══════════════ 2. LICENCIAMENTO ═══════════════
    "licenciamento": {
        "nome": "LICENCIAMENTO",
        "link": "/licenciamento-atrasado",
        "pagina": "Licenciamento atrasado: pode ser apreendido?",
        "cta_seal": "MANDA A PLACA",
        "cta_big": ["A GENTE REGULARIZA", "PRA VOCÊ"],
        "passos": [
            {"cat": "O QUE É",
             "titulo": "Licenciamento não é IPVA. E os dois vencem.",
             "bullets": ["IPVA é imposto do Estado. Licenciamento é a 'liberação' anual do DETRAN.",
                         "Pagou o IPVA e esqueceu o licenciamento? O carro continua irregular.",
                         "Manda a placa: a gente confere os dois em minutos."],
             "fonte": "https://www.detran.sc.gov.br/"},
            {"cat": "CONSEQUÊNCIA", "formato": "consequencia",
             "titulo": "Rodar sem licenciar: gravíssima, 7 pontos e o carro no pátio",
             "bullets": ["CTB art. 230, V: infração gravíssima com remoção do veículo.",
                         "Guincho, diária de pátio e a taxa que estava atrasada — tudo junto.",
                         "Antes da blitz te achar, a gente regulariza."],
             "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
            {"cat": "DICA",
             "titulo": "CRLV-e: o documento agora é digital, mas precisa estar pago",
             "bullets": ["O CRLV-e só aparece no app depois do licenciamento quitado.",
                         "'Não abre no app' quase sempre é débito pendente.",
                         "A gente descobre o que está travando e resolve."],
             "fonte": "https://www.gov.br/pt-br/servicos/obter-crlv-digital"},
            {"cat": "DICA",
             "titulo": "Pagou o licenciamento e o app continua mostrando o ano passado?",
             "bullets": ["A baixa do pagamento leva um tempo pra chegar no sistema do DETRAN.",
                         "Passou de dias? Pode ter taxa ou multa que ficou faltando no conjunto.",
                         "Manda a placa: a gente vê o que ainda trava o CRLV-e."],
             "fonte": "https://www.detran.sc.gov.br/crlv-e"},
            {"cat": "MITO OU VERDADE", "formato": "mito",
             "titulo": "\"Se eu não fui parado, tá tudo certo.\" MITO.",
             "bullets": ["O débito cresce com juros e vai pra dívida ativa mesmo sem blitz.",
                         "Na hora de vender, a transferência trava.",
                         "Regularizar hoje é mais barato que amanhã."],
             "fonte": "https://www.sef.sc.gov.br/"},
        ],
    },

    # ═══════════════ 3. IPVA SC ═══════════════
    "ipva": {
        "nome": "IPVA EM SC",
        "link": "/pagar-ipva-online",
        "pagina": "Pagar IPVA online em SC: dá pra confiar no app?",
        "cta_seal": "MANDA A PLACA",
        "cta_big": ["A GENTE CONFERE", "PRA VOCÊ"],
        "passos": [
            {"cat": "DICA",
             "titulo": "IPVA atrasado não some. Ele cresce.",
             "bullets": ["Juros e multa por dia de atraso, depois dívida ativa e protesto.",
                         "Nome negativado por IPVA acontece — e trava crédito.",
                         "Manda a placa: a gente te diz quanto está e como sair."],
             "fonte": "https://www.sef.sc.gov.br/"},
            {"cat": "GOLPE",
             "titulo": "Boleto de IPVA no WhatsApp? Desconfia.",
             "bullets": ["A guia oficial do IPVA em SC é a DARE, emitida no site da SEF.",
                         "Site 'parecido' e boleto por mensagem são golpe clássico.",
                         "Na dúvida, manda pra gente conferir antes de pagar."],
             "fonte": "https://www.sef.sc.gov.br/"},
            {"cat": "MITO OU VERDADE", "formato": "mito",
             "titulo": "\"Paguei o IPVA, posso rodar.\" Só se licenciou também.",
             "bullets": ["IPVA quitado não libera o licenciamento sozinho.",
                         "Falta a taxa do DETRAN e, se houver, multas em aberto.",
                         "A gente fecha o conjunto: IPVA + licenciamento + multas."],
             "fonte": "https://www.detran.sc.gov.br/"},
        ],
    },

    # ═══════════════ 4. MULTA E DEFESA ═══════════════
    "multa": {
        "nome": "MULTA E DEFESA",
        "link": "/defesas",
        "pagina": "Defesa de multa e CNH suspensa em SC",
        "cta_seal": "ANÁLISE GRÁTIS",
        "cta_big": ["MANDA A FOTO", "DA MULTA"],
        "passos": [
            {"cat": "DICA",
             "titulo": "Chegou multa? Você tem prazo — e ele corre",
             "bullets": ["Primeiro chega a notificação da autuação, depois a da penalidade.",
                         "Cada uma tem prazo pra defesa e pra indicar o condutor.",
                         "Manda a foto: a gente lê o prazo e te diz se vale recorrer."],
             "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
            {"cat": "DICA",
             "titulo": "Não indicou o condutor? Os pontos caem no dono do carro",
             "bullets": ["Quem dirigia não é você? Tem que indicar no prazo da notificação.",
                         "Perdeu o prazo: pontos na CNH do proprietário.",
                         "A gente monta a indicação certinha, sem erro de formulário."],
             "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
            {"cat": "MITO OU VERDADE", "formato": "mito",
             "titulo": "\"Recorrer é perda de tempo.\" MITO.",
             "bullets": ["Erro no auto, radar sem aferição e notificação fora do prazo anulam multa.",
                         "Enquanto o recurso corre, a penalidade fica suspensa.",
                         "Análise da multa é grátis. Se não tiver chance, a gente fala."],
             "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
            {"cat": "CONSEQUÊNCIA", "formato": "consequencia",
             "titulo": "20, 30 ou 40 pontos? O limite depende do seu histórico",
             "bullets": ["CTB art. 261: o teto muda conforme as gravíssimas em 12 meses.",
                         "Passou do limite: processo de suspensão da CNH.",
                         "A gente acompanha sua pontuação e age antes da suspensão."],
             "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
            {"cat": "DICA",
             "titulo": "Desconto de 40% na multa: quando vale e quando não vale",
             "bullets": ["Pelo SNE, quem não recorre pode pagar com desconto.",
                         "Mas pagar reconhece a infração — os pontos vêm junto.",
                         "Multa leve? Talvez pague. Gravíssima? Antes, manda pra análise."],
             "fonte": "https://www.gov.br/pt-br/servicos/aderir-ao-sistema-de-notificacao-eletronica-sne"},
        ],
    },

    # ═══════════════ 5. CNH (forma branca: orientação + curso que representamos) ═══════════════
    "cnh": {
        "nome": "SUA CNH",
        "link": "/cnh",
        "pagina": "Ajuda para renovar a CNH, MOPP e reciclagem",
        "cta_seal": "A GENTE TE ORIENTA",
        "cta_big": ["QUEM FAZ É VOCÊ.", "A GENTE ACOMPANHA."],
        "passos": [
            {"cat": "DICA",
             "titulo": "CNH vencendo? Dá pra renovar pelo gov.br — e a gente te orienta",
             "bullets": ["A renovação é feita por você, no gov.br / DETRAN, com exames.",
                         "A gente te mostra cada etapa e confere se está tudo certo.",
                         "Sem fila, sem chute, sem ficar sozinho no sistema."],
             "fonte": "https://www.gov.br/pt-br/servicos/renovar-cnh"},
            {"cat": "CONSEQUÊNCIA", "formato": "consequencia",
             "titulo": "CNH vencida há mais de 30 dias: gravíssima e o carro fica",
             "bullets": ["Dirigir com CNH vencida há mais de 30 dias é infração gravíssima.",
                         "Multa, 7 pontos e o carro fica retido até chegar um motorista habilitado.",
                         "Fala com a gente antes de vencer, não depois."],
             "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
            {"cat": "MOTORISTA PROFISSIONAL", "formato": "consequencia",
             "titulo": "Categoria C, D ou E: toxicológico vencido gera multa sem ninguém te parar",
             "bullets": ["O exame periódico é obrigatório mesmo sem renovar a CNH (CTB art. 148-A).",
                         "Passou 30 dias do vencimento: multa gravíssima pelo sistema, sem blitz (art. 165-D).",
                         "A gente te indica o laboratório credenciado e confere o seu prazo."],
             "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
            {"cat": "CURSO PARCEIRO",
             "titulo": "CNH suspensa: o curso de reciclagem que representamos",
             "bullets": ["Curso homologado pela SENATRAN, parceiro do escritório.",
                         "A gente faz sua matrícula e acompanha até o certificado.",
                         "Depois, a prova no DETRAN — e a gente te orienta nela também."],
             "fonte": "https://www.gov.br/transportes/pt-br/assuntos/transito"},
            {"cat": "MOTORISTA PROFISSIONAL",
             "titulo": "MOPP, escolar, mototáxi: curso homologado que representamos",
             "bullets": ["Cursos especializados exigidos pra trabalhar com a CNH.",
                         "Turmas do parceiro homologado — a gente te encaminha e matricula.",
                         "Toxicológico pra C, D e E: a gente te indica onde fazer."],
             "fonte": "https://www.gov.br/transportes/pt-br/assuntos/transito"},
        ],
    },

    # ═══════════════ 6. SCOOTER ELÉTRICA (dica de lei; a oferta com foto segue no DL Mobilidade) ═══════════════
    "scooter": {
        "nome": "SCOOTER ELÉTRICA",
        "link": "/scooter-eletrica-precisa-de-placa",
        "pagina": "Scooter elétrica precisa de placa? CONTRAN 996",
        "cta_seal": "TEST-RIDE GRÁTIS",
        "cta_big": ["VEM ANDAR", "NA LOJA"],
        "passos": [
            {"cat": "DICA DE LEI",
             "titulo": "Sem CNH. Sem placa. Sem IPVA. É sério.",
             "bullets": ["Autopropelido (CONTRAN 996): até 1000 W e 32 km/h.",
                         "Não exige habilitação, emplacamento nem licenciamento.",
                         "Vem conhecer na loja — Mal. Castelo Branco, 2838, Schroeder."],
             "fonte": "https://www.gov.br/transportes/pt-br/assuntos/transito/conteudo-contran/resolucoes/Resolucao9962023.pdf"},
            {"cat": "DICA DE LEI",
             "titulo": "Onde a scooter elétrica PODE andar?",
             "bullets": ["Ciclovia e ciclofaixa: pode, até 20 km/h.",
                         "Rua urbana com limite de até 40 km/h: pode.",
                         "Calçada e rodovia: NÃO pode. Anda certo, anda tranquilo."],
             "fonte": "https://www.gov.br/transportes/pt-br/assuntos/transito/conteudo-contran/resolucoes/Resolucao9962023.pdf"},
            {"cat": "MITO OU VERDADE", "formato": "mito",
             "titulo": "\"Scooter elétrica a blitz apreende.\" Depende da scooter.",
             "bullets": ["Dentro da 996 (1000 W, 32 km/h, equipamentos): circula em paz.",
                         "Acima disso vira ciclomotor: precisa CNH e placa.",
                         "Modelo do Paraguai sem nota: apreensão e descaminho."],
             "fonte": "https://www.gov.br/transportes/pt-br/assuntos/transito/conteudo-contran/resolucoes/Resolucao9962023.pdf"},
            {"cat": "NOVIDADE 2026", "formato": "consequencia",
             "titulo": "Sua scooter passa de 32 km/h? Desde 1º/1/2026 é ciclomotor: precisa de placa",
             "bullets": ["Acima de 1000 W ou 32 km/h não é autopropelido: é ciclomotor.",
                         "Ciclomotor exige CNH A ou ACC, placa e registro — o prazo de adaptação acabou em 31/12/2025.",
                         "Não sabe em qual caixa a sua cai? Manda modelo e foto no zap."],
             "fonte": "https://www.gov.br/transportes/pt-br/assuntos/transito/conteudo-contran/resolucoes/Resolucao9962023.pdf"},
            {"cat": "DICA DE LEI",
             "titulo": "Os equipamentos obrigatórios da scooter elétrica",
             "bullets": ["Campainha, farol dianteiro e lanterna traseira.",
                         "Faixas refletivas, retrovisor e velocímetro.",
                         "As nossas já vêm completas de fábrica — é ligar e andar."],
             "fonte": "https://www.gov.br/transportes/pt-br/assuntos/transito/conteudo-contran/resolucoes/Resolucao9962023.pdf"},
            {"cat": "CURIOSIDADE",
             "titulo": "Quanto custa 'abastecer' uma scooter elétrica?",
             "bullets": ["Recarga em tomada comum de casa, em 4 a 6 horas.",
                         "Custo por km muitas vezes menor que gasolina.",
                         "Sem óleo, sem vela, sem filtro — manutenção mínima."],
             "fonte": SITE + "/mobilidade"},
            {"cat": "DICA DE LEI",
             "titulo": "O perigo da scooter 'baratinha' do Paraguai",
             "bullets": ["Na blitz, nota paraguaia = veículo apreendido + descaminho.",
                         "Sem garantia, sem peça, sem assistência — fica na mão.",
                         "Aqui: nota fiscal brasileira e 2 anos de garantia. Durma em paz."],
             "fonte": SITE + "/mobilidade"},
        ],
    },

    # ═══════════════ 7. SITE + MARCA + MOTOR (DL Publicidade) ═══════════════
    "negocio": {
        "nome": "SEU NEGÓCIO NA INTERNET",
        "link": "/publicidade",
        "pagina": "Marketing digital com IA para empresas · DL Publicidade",
        "cta_seal": "FALA COM A DL",
        "cta_big": ["SITE, MARCA", "E POSTS NO AUTOMÁTICO"],
        "passos": [
            {"cat": "PERGUNTA",
             "titulo": "Seu Instagram parou em 2024? O cliente percebe.",
             "bullets": ["Perfil sem post há meses passa a impressão de loja fechada.",
                         "Este post que você está lendo foi feito por um motor automático.",
                         "A gente monta o seu: marca, site e posts todo dia."],
             "fonte": SITE + "/publicidade"},
            {"cat": "DICA",
             "titulo": "Site de uma página resolve 90% do comércio local",
             "bullets": ["Quem te acha no Google quer: o que você faz, onde e o zap.",
                         "Uma página bem feita faz isso melhor que dez.",
                         "A gente escreve, publica e coloca no Google."],
             "fonte": SITE + "/publicidade"},
            {"cat": "PROVA",
             "titulo": "1,2 milhão de visualizações por mês com um motor de posts",
             "bullets": ["É o número da Rádio SC News, feita pela mesma equipe.",
                         "O mesmo motor pode postar pelo seu negócio, todo dia.",
                         "Sem agência cara, sem depender de ninguém lembrar de postar."],
             "fonte": SITE + "/publicidade"},
        ],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# 15/set (noite) — "UM POST = UMA PÁGINA". Só 3 linhas de dinheiro rodam no automático:
#   documentalista (transferência, 0 km, dívida ativa, baixa, PCD, placa, licenciamento),
#   defesa (multa R$39, suspensão, cassação) e scooter. CNH-renovação, proteção veicular e
#   assessoria saíram do site e saem do motor. Cada item aponta pra UMA URL curta digitável
#   (dldespachante.com.br/multa …) que vai GRANDE no último slide — Instagram não linka legenda.
LINHAS = {"documentalista": ["trocar_carro", "licenciamento", "ipva"],
          "defesa": ["multa"],
          "scooter": ["scooter"]}
FORA_DO_MOTOR = ("cnh", "negocio")          # ficam no banco, não rodam

# URL curta por página (espelha o _redirects do site; utm vai no redirect)
CURTA = {"/defesa-de-multa": "/multa", "/suspensao-cnh": "/suspensao", "/cassacao-cnh": "/cassacao",
         "/defesas": "/defesas", "/transferencia-de-veiculo-assinatura": "/transferencia",
         "/emplacamento-0km": "/0km", "/divida-ativa-veiculo": "/divida", "/baixa-de-veiculo": "/baixa",
         "/isencao-pcd": "/pcd", "/consultar-debitos-veiculo-sc": "/placa", "/pagar-ipva-online": "/ipva",
         "/mobilidade": "/scooter", "/licenciamento-atrasado": "/licenciamento",
         "/scooter-eletrica-precisa-de-placa": "/scooter-eletrica-precisa-de-placa"}

# Travas que saíram do MiroFish (15/set): tom de utilidade, nunca terrorismo; fonte + número em todo
# post; defesa com o rodapé "despachante credenciado, não escritório de advocacia; quem julga é o órgão".
RODAPE_DEFESA = "Despachante credenciado DETRAN/SC 2095 · não é escritório de advocacia · quem julga é o órgão"

# Itens das 7 páginas novas (cada um = 1 página):
SERIES["trocar_carro"]["passos"] += [
    {"cat": "0 KM", "link": "/emplacamento-0km",
     "titulo": "Comprou 0 km? Documento e placa no mesmo dia",
     "bullets": ["Registro no DETRAN/SC, IPVA proporcional, CRLV-e e placa Mercosul.",
                 "Em Schroeder, sai em cerca de 2 horas depois da nota fiscal.",
                 "Manda a nota no zap e a gente cuida do resto."],
     "fonte": "https://www.detran.sc.gov.br/"},
    {"cat": "DÍVIDA ATIVA", "link": "/divida-ativa-veiculo",
     "titulo": "IPVA foi pra dívida ativa? Ainda dá pra licenciar",
     "bullets": ["Levantamos ano a ano o que está na PGE/SC e no cartório.",
                 "Dá pra parcelar e liberar o CRLV-e sem quitar tudo de uma vez.",
                 "Manda a placa: a gente te diz o total real antes de qualquer pagamento."],
     "fonte": "https://www.sef.sc.gov.br/"},
    {"cat": "BAIXA", "link": "/baixa-de-veiculo",
     "titulo": "Carro que não roda mais continua gerando IPVA",
     "bullets": ["Sucata, perda total, furto ou parado no quintal: a baixa encerra o registro.",
                 "Débito não impede a baixa (CTB art. 126).",
                 "A gente faz a baixa por você e o IPVA para de nascer."],
     "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
    {"cat": "ISENÇÃO PCD", "link": "/isencao-pcd",
     "titulo": "Isenção PCD: o pedido é ANTES de comprar o carro",
     "bullets": ["IPI, IOF, ICMS e IPVA: quem tem direito e os tetos de 2026.",
                 "Laudo, SISEN e TTD 596 — a ordem certa evita perder a isenção.",
                 "A gente monta o processo do começo ao fim."],
     "fonte": "https://www.sef.sc.gov.br/"},
]
SERIES["multa"]["passos"] += [
    {"cat": "R$ 39 · 24 H", "link": "/defesa-de-multa",
     "titulo": "Recebeu multa? Defesa pronta em 24 horas por R$ 39",
     "bullets": ["Radar, celular, cinto, estacionamento: defesa administrativa em PDF.",
                 "Você recebe pronta com o passo a passo pra protocolar. Todo o Brasil.",
                 "Quem julga é o órgão; a gente faz a defesa tecnicamente certa."],
     "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
    {"cat": "SUSPENSÃO", "link": "/suspensao-cnh",
     "titulo": "Recebeu a notificação de suspensão da CNH? Você ainda dirige",
     "bullets": ["Enquanto a defesa e o recurso correm, a CNH continua válida.",
                 "Por pontos (20/30/40 em 12 meses) ou por infração direta.",
                 "Defesa + recurso: valor combinado antes, metade no início."],
     "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
    {"cat": "CASSAÇÃO", "link": "/cassacao-cnh",
     "titulo": "CNH cassada ou a JARI negou? Ainda existe o CETRAN",
     "bullets": ["Dirigiu suspenso, reincidência ou condenação: cabe recurso em 2ª instância.",
                 "Acompanhamos até a decisão final do órgão.",
                 "Manda a notificação: análise grátis do que ainda cabe."],
     "fonte": "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"},
]
# Um post = UMA página: os passos antigos ganham a página específica (antes apontavam pra série).
_LINK_POR_TITULO = {
    "Fechou negócio? Você tem 30 dias pra transferir": "/transferencia-de-veiculo-assinatura",
    "Vendeu o carro e chegou multa no seu nome? O carro ainda é 'seu' pro DETRAN": "/transferencia-de-veiculo-assinatura",
    "Vistoria reprovou? Não é o fim do negócio": "/transferencia-de-veiculo-assinatura",
    "Transferência à vista no PIX: condição especial no escritório": "/transferencia-de-veiculo-assinatura",
    "Comprou com débito? Agora o débito é seu": "/divida-ativa-veiculo",
    "Chegou multa? Você tem prazo — e ele corre": "/defesa-de-multa",
    "Não indicou o condutor? Os pontos caem no dono do carro": "/defesa-de-multa",
    "\"Recorrer é perda de tempo.\" MITO.": "/defesa-de-multa",
    "20, 30 ou 40 pontos? O limite depende do seu histórico": "/suspensao-cnh",
    "Desconto de 40% na multa: quando vale e quando não vale": "/defesa-de-multa",
    "IPVA atrasado não some. Ele cresce.": "/divida-ativa-veiculo",
}
for _s in SERIES.values():
    for _it in _s["passos"]:
        if _it["titulo"] in _LINK_POR_TITULO:
            _it["link"] = _LINK_POR_TITULO[_it["titulo"]]

# Tom (MiroFish 15/set): consequência vira utilidade, não ameaça.
for _s in SERIES.values():
    for _it in _s["passos"]:
        _it["titulo"] = _it["titulo"].replace("gravíssima, 7 pontos e o carro no pátio", "o que muita gente não sabe")
        _it["bullets"] = [b.replace("Antes da blitz te achar, a gente regulariza.", "A gente regulariza antes que vire dor de cabeça.") for b in _it["bullets"]]
# Passo 1 da jornada: responder "isso eu faço de graça no DETRAN" (objeção nº 1 da simulação)
SERIES["trocar_carro"]["passos"][0]["bullets"] = [
    "A consulta grátis do DETRAN mostra débito e restrição. Não mostra leilão, sinistro nem multa em autuação.",
    "É aí que o negócio bom vira prejuízo depois do PIX.",
    "Manda a placa: a gente puxa o histórico completo antes de você fechar."]


PAGINA = {"/defesa-de-multa": "Defesa de multa pronta em 24 h por R$ 39",
          "/suspensao-cnh": "CNH suspensa: defesa e recurso, você segue dirigindo",
          "/cassacao-cnh": "CNH cassada ou JARI negou: recurso ao CETRAN",
          "/transferencia-de-veiculo-assinatura": "Transferência de veículo: ATPV-e, vistoria, comunicação de venda",
          "/emplacamento-0km": "Emplacamento de 0 km em SC em 2 horas",
          "/divida-ativa-veiculo": "IPVA em dívida ativa: regularizar e licenciar",
          "/baixa-de-veiculo": "Baixa de veículo: sucata, perda total, parado",
          "/isencao-pcd": "Isenção PCD: IPI, ICMS e IPVA no carro",
          "/consultar-debitos-veiculo-sc": "Consultar débitos do veículo pela placa",
          "/pagar-ipva-online": "IPVA em SC: guia, atraso e golpe do boleto",
          "/licenciamento-atrasado": "Licenciamento atrasado: pode ser apreendido?",
          "/mobilidade": "Scooter elétrica: modelos, parcelas e test-ride"}
for _s in SERIES.values():
    for _it in _s["passos"]:
        if _it.get("link") in PAGINA:
            _it["pagina"] = PAGINA[_it["link"]]


def _linha_de(serie):
    for k, v in LINHAS.items():
        if serie in v:
            return k
    return None


def curta(item):
    """URL curta digitável (sem utm — o redirect põe). Se não houver curta, a longa."""
    return "dldespachante.com.br" + CURTA.get(item.get("link", ""), item.get("link", ""))


def _ultimo_post_por_link():
    """{link: ts do último post} lido do marcas_posts.jsonl (só feed, não story)."""
    import json, os
    p = os.path.join(os.environ.get("DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "marcas_posts.jsonl")
    out = {}
    if not os.path.exists(p):
        return out
    for ln in open(p, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("brand") == "despachante" and r.get("link") and r.get("slot") != "noite":
            out[r["link"]] = max(out.get(r["link"], ""), r.get("ts", ""))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# ORDEM DA JORNADA (slot da manhã anda um passo por dia; ao acabar, volta ao começo)
ORDEM_MANHA = ["trocar_carro", "multa", "licenciamento", "multa", "ipva", "trocar_carro", "multa"]


def _flat(series_keys, formatos=None):
    out = []
    for k in series_keys:
        s = SERIES[k]
        for i, it in enumerate(s["passos"], 1):
            if formatos and it.get("formato", "dica") not in formatos:
                continue
            d = dict(it)
            d.update({"serie": k, "serie_nome": s["nome"], "passo": i,
                      "total": len(s["passos"]), "link": it.get("link", s["link"]),
                      "pagina": it.get("pagina", s["pagina"]), "cta_seal": s["cta_seal"], "cta_big": s["cta_big"]})
            out.append(d)
    return out


def escolher(slot, dia_do_ano, weekday=None):
    """Item do dia por slot. Determinístico (dia do ano) — sem estado, sobrevive a redeploy.
    manha  → jornada (passos em ordem, série por série)
    meio   → 'mito ou verdade' / 'consequência' (gancho de alcance; vira Plantão quando houver notícia)
    tarde  → scooter (seg/qua/sex) · negócio (dom) · multa (outros)
    noite  → o mesmo da manhã, só como story 'veja mais no site'
    """
    if slot in ("manha", "noite"):
        # intercala as duas linhas de dinheiro: doc, defesa, doc, defesa… (nunca 5 dias da mesma)
        import itertools as _it
        docs = _flat(LINHAS["documentalista"]); defs = _flat(LINHAS["defesa"])
        banco = []
        for k_ in range(len(docs)):
            banco += [docs[k_], defs[k_ % len(defs)]]   # dia sim, dia não, sempre
        i = dia_do_ano % len(banco)
        # 15/set: a PÁGINA que está há mais tempo sem post vence (máximo espaçamento entre
        # repetições da mesma URL); empate = ordem da fila a partir de hoje. Sem log = fila pura.
        ultimo = _ultimo_post_por_link()
        if not ultimo:
            return banco[i]
        fila = [banco[(i + k) % len(banco)] for k in range(len(banco))]
        return min(fila, key=lambda it: (ultimo.get(it.get("link"), ""), fila.index(it)))
    if slot == "meio":
        banco = _flat(list(SERIES), formatos=("mito", "consequencia"))
        return banco[(dia_do_ano * 7) % len(banco)]
    if slot == "tarde":
        wd = weekday if weekday is not None else 0
        if wd in (0, 2, 4):
            banco = _flat(["scooter"])
        elif wd == 6:
            banco = _flat(["multa"])          # negocio saiu do motor (15/set)
        else:
            banco = _flat(["multa", "trocar_carro"])
        return banco[(dia_do_ano * 3) % len(banco)]
    raise ValueError(f"slot desconhecido: {slot}")


def url(item):
    return SITE + item["link"]


# Travas de legenda (vão pro prompt da IA e pro fallback). Uma linha por regra.
TRAVAS = (
    "NUNCA ensine a pagar débito, emitir guia ou fazer licenciamento sozinho: o post mostra o "
    "problema e a consequência; o próximo passo é mandar a placa no WhatsApp ou ver a página do site. "
    "Despachante NÃO renova CNH, NÃO emite habilitação e NÃO dá curso: ele ORIENTA o cidadão no "
    "gov.br/DETRAN e REPRESENTA cursos homologados. Nunca escreva 'renovamos', 'fazemos sua CNH', "
    "'nosso curso', 'garantimos', 'sem pôr o pé no DETRAN'. Nunca se passe pelo DETRAN. "
    "Não invente número, prazo, artigo de lei ou valor além dos que estão nos PONTOS."
)
