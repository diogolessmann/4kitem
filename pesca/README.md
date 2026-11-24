# Hora de Pescar: protótipo local, versão 1 (Texas)

Site em inglês dos EUA. Diz, para 20 lugares de pesca do Texas, a melhor hora de hoje e dos próximos 7 dias, o peixe, a isca, o tempo e a maré. Exemplo de manchete: "Today, 4:20 PM · Largemouth bass · topwater lure · 7/10".

## Rodar
```
python coleta.py      # baixa TPWD (lagos), NWS (tempo + pressão) e NOAA (maré): ~2 min
python gera.py        # monta site/ e data/out/RESUMO_PT.txt: ~12 s
python confere.py     # imprime os números de 3 lugares para conferir à mão contra NWS e NOAA
python verifica_fichas.py   # confere se cada citação das fichas existe na fonte (125/125 em 23/11)
HDP_SITE_URL=https://dominio python atualiza.py   # o que o robô roda: coleta -> gera --deploy -> confere (sai 1 se vier ruim)
HDP_SITE_URL=https://dominio python atualiza.py --sem-coleta   # o mesmo, com a última coleta (teste)
```
Publicação: `.github/workflows/atualiza.yml` roda `atualiza.py` a cada 2 h e sobe `site/` no Cloudflare Pages. Fica desligado até a variável `HDP_LIGADO` valer `sim`. Passo a passo em `../PUBLICAR.md`. Dependências fixas em `requirements.txt`.
Abrir `site/index.html` no navegador (prévia local; o modo `--deploy` usa URLs limpas e precisa de servidor). Dependências: `requests`, `jinja2`, `skyfield` (MIT) e o arquivo `data/de421.bsp` da NASA/JPL (~17 MB, baixado pelo Skyfield; fora do git).

## Onde mora cada coisa
- `data/places.json`: os 20 lugares, com a fonte de cada coordenada (estação do USGS no lago, estação da NOAA no litoral).
- `fichas/fresh.json` e `fichas/salt.json`: as 15 fichas de espécie (dentro do repositório desde 23/11, porque o robô precisa delas). Cada número tem `source_url` e `quote`.
- `data/tpwd_lakes.json`: o que foi lido das páginas de lago do TPWD (espécies, nota, iscas citadas, link das regras).
- `hdp/`: o motor. `score.py` tem a nota e os pesos; `engine.py` monta o lugar; `astro.py` cuida de sol, lua e solunar.
- `templates/`: as páginas. O título é o termo buscado ("Best Time to Fish Lake Fork"). Desde 24/11, cada lugar tem 4 abas numa barra embaixo (🎣 Today, 📅 Week, 📍 Places, 🎒 What to bring), trocadas por JS sem sair da página. Sem JS, tudo aparece (o Google lê tudo). Nota = número grande + uma palavra (8-10 Great, 6-7 Good, 4-5 Fair, 0-3 Poor). "Near me" usa o GPS do aparelho só no navegador.

## Regras do motor (depois da revisão Opus de 23/11)
- Nota = 3 + 7 × (1 − e^(−positivos/4)) + negativos. Entardecer comum ≈ 6; com solunar ≈ 7; 9 só com quase tudo alinhado; 10 nunca.
- Positivos: luz da espécie, solunar, maré, pressão caindo (−2 hPa em 3 h), antes de frente fria. Negativos: vento em degraus (10/15/20/25 mph), chance de chuva (30/50/70%), trovoada, alerta do NWS (−4), depois da frente com céu limpo.
- Regra de espécie (hora de comer, maré, lua) só entra com fonte e em **campo estruturado** da ficha (`activity.pattern`, `activity.months`, `tide.incoming_bonus`, `moon_phase_bonus`). Nunca procurar palavra em texto livre.
- **Fechamento**: campo `closed` da ficha (linguado 1/nov a 14/dez, TPWD). Espécie fechada não vira manchete nem "melhor hora"; a tabela mostra 0.
- **Manchete** = melhor janela das próximas 24 h, só entre espécies com regra de luz ou de maré. O site traz 48 manchetes por hora e o navegador mostra a da hora de quem lê. Em produção, gerar de novo a cada hora.
- **Limites**: lago com regra própria diz isso com palavras nossas e manda para o link oficial do TPWD daquele lago (`fishregs2.php`), sem números. O texto do TPWD não é copiado: a política deles pede permissão escrita para uso comercial. A tabela estadual (números = fatos) aparece só no litoral e em lago que o TPWD diz ser 100% estadual (Travis, Amistad).
- Dia sem previsão do NWS fica sem nota. Lago sem dado do TPWD fica sem lista de peixe.
- Horário: faixas em UTC, mostradas no horário central (o horário de verão não cria nem some hora). Maré da NOAA pedida em GMT.
- Temperatura: **neutra na v1**. As faixas das fichas são de água (crescimento ótimo) e a v1 não mede a água.
- Pressão: só leitura com menos de 3 h e um campo só por estação. A frente fria é detectada pela virada do vento para o norte com queda de temperatura.

## Revisões
- `../REVISAO_OPUS.md`: 2 críticos, 5 altos, 10 médios e 12 baixos.
- `../REVISAO_OPUS_2_CONFERE.md`: conferência das correções. Achou 2 bugs altos novos nas bordas do fechamento do linguado; eles e mais 9 menores foram corrigidos e testados em 23/11.
- Para a v2: a nota do dia ainda é só a melhor hora (o tempo do resto do dia não pesa), e por isso os lagos dão 6 ou 7 quase todo dia numa semana calma.
- Antes de publicar (23/11): o texto das regras do TPWD saiu (política de copyright deles); o User-Agent vira `BestTimeToFish/1.0 (+URL do site)` quando `HDP_SITE_URL` existe. Fica com o Diogo: "Today" no `<title>`.

## Preço
`hdp/config.py` → `PRICE` (US$ 19,99/ano e US$ 2,99/mês, decisão do Diogo em 23/11). Nenhuma página escreve o preço fixo; a v1 ainda não mostra a parte paga.

## Fora da v1
Diário de pesca, aprendizado, temperatura da água, outros estados, app iOS, IA por usuário, pagamento (Paddle: a conta é do Diogo).
