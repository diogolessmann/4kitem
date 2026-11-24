# fresh_NOTAS (9 espécies de água doce) - 23/11/2026

## Fontes abertas (todas lidas nesta pesquisa)
- TPWD Outdoor Annual, limites estaduais (valem 1/9/2026 a 31/8/2027): tpwd.texas.gov/regulations/outdoor-annual/fishing/freshwater-fishing/bag-length-limits
- Páginas de espécie TPWD: lmb (só spawn/habitat), wtb (white bass), str, wcp, crappie (black), ccf, bgl.
- Folhetos TPWD (PDF, lidos via pdftotext do arquivo baixado): pwd_br_t3200_0236 (catfish), 0022 (white bass), 0023m (largemouth), pwd_lf_k0700_0692 (peixes comuns).
- TPWD: Texoma (lakes/texoma), gulfstripedbass, managepond, fw_production, news release 20200915a (crappie).
- USFWS HSI: Largemouth Bass FWS/OBS-82/10.16 (cópia em riptideweb.com, não no site oficial) e White Bass Biol. Rep. 82(10.89) (govinfo LPS101986).

## Como justifiquei a lista
- TPWD 0023m: "The largemouth bass is the most popular freshwater sport fish in Texas."
- TPWD 0236: catfish ficam em segundo, logo atrás do largemouth ("catfish rank second, just behind largemouth bass"); channel é o mais pescado.
- Não achei pesquisa estadual de pescador de água doce com % por espécie. Resumos de busca (NÃO abertos, tratar como [H]) citam creel de lagos: Tawakoni 2021/22 (temperate bass e crappie 23% do esforço, catfish 22%, largemouth 19%); Lake Fork (largemouth 73-81%, crappie 2º); Corpus Christi (catfish 37%). Mostra que a ordem varia por lago.
- Bluegill/sunfish, hybrid e flathead: sem fonte de ranking. Entram pela lista do briefing.

## Não achei
- HSI/USFWS de bluegill, channel catfish, crappie, striped bass: o NWRC deu 403, outros hosts só retornaram a espécie errada. Por isso temp_preferred_f é null para crappie, catfish, hybrid, flathead e blue.
- Mês de desova no Texas: só largemouth (fev-mai) e bluegill (pico mai-jun) têm mês em fonte. Os outros dizem "primavera" ou temperatura; deixei null.
- Hybrid striped bass: nenhuma página TPWD de espécie; isca, temperatura e atividade null.
- Horário de atividade (madrugada/noite): só largemouth (HSI: pico de manhã cedo e fim de tarde) e flathead (noite). White bass: pesca à noite com luz.
- Páginas TPWD bcf e fcf deram "plantas ameaçadas" (URL errada); hsb também. Por isso tpwd_profile_url null para blue, flathead, hybrid.

## Dúvidas / cuidados
- Conversão C para F: largemouth 24-30 C = 75-86 F; spawn 16-22 C = 61-72 F; white bass ótimo 28-29,5 C = 82-85 F (é "probable optimum" de um estudo citado no HSI, não regra TPWD). Quote traz o valor original.
- Folheto de catfish de 2015 diz mínimo de 12 in para channel/blue; Outdoor Annual atual diz sem mínimo e 25 no total, só 10 com 20 in ou mais. Usei o Outdoor Annual.
- Crappie: temp_spawn_f junta branco (65-70 F; até 56 F) e preto (60 F) de duas páginas; source_url aponta só para wcp.
- Bluegill temp_preferred_f min 40 vem de "feeds down into the 40s" (limite inferior de atividade, não ótimo).
- Taxonomia: TPWD 0023m separa M. s. salmoides (nativa) e M. s. floridanus (Flórida); a ficha usa M. salmoides só. Striped bass: cruza "male white bass x female striped" no hatchery; a ficha não depende disso.
- Exceções por lago existem (Texoma, Toledo Bend, Rayburn, Livingston, Caddo etc.); a página estadual não as lista.
- Iscas por estação de striper/crappie vêm muito da página do Lake Texoma (um lago). Generalizar com cautela.
- Dos ~46 usos, desperdicei 5 em 403/URL errada.

## Rodada 2 (tapar buracos) - 23/11/2026
Verificacao: cada quote (e quote_2) foi baixada com Python (requests/pypdf), normalizada e testada como substring. Script em D:\bateria\hdp_gaps\ (apply.py, h.py). Campo "verified": true em tudo; 136 quotes true / 0 false (fresh+salt). Cinco passaram so com tolerancia a espacos/hifen de OCR/quebra de linha ("verified_mode": "ocr-spacing-tolerant").

### Preenchido [L]
- white_bass: temp 82-85F AGORA com fonte certa. **Erro da rodada 1:** a URL govinfo LPS101986 e o HSI do WARMOUTH, nao do white bass. O texto certo e FWS/OBS-82/10.89 (LPS101735): "probable optimum temperature ... 28.0 and 29.5 C" = achado de campo de Gammon (1973) perto de efluente de usina, nao otimo de crescimento. Desova: TPWD "In February and March they begin their spawning migration", termina em maio -> meses 2-5. Atividade: 'mixed' mantido (pesca noturna com luz + cardume ao amanhecer).
- hybrid: sem pagina TPWD de especie (tpwd_profile_url null). Temp 77-81F = crescimento maximo em viveiro (SRAC 303; fonte de aquicultura). Atividade crepuscular (Texas A&M, "low light, such as dawn and dusk"). Isca: 5 itens de paginas de lago do TPWD (Walter Long, Conroe, Jim Chapman). Reproducao: CONFLITO (TPWD diz que so o white bass se reproduz / "do not reproduce"; Texas A&M diz "they are fertile"). Tratar como peixe de estocagem, sem janela de desova.
- crappie: desova meados de marco a meados de maio, comeca a 60F e pico 68-72F (folheto TPWD pwd_br_t3200_178); atividade 'mixed' (verao: pesca noturna sob luz muito produtiva); iscas reescritas por estacao. Temp preferida continua null.
- channel catfish: otimo 80-85F (Texas A&M, viveiro; SRAC 180 diz ~85F); desova 75-85F (SRAC 180; TPWD ~75F); meses 2-8 e a faixa de populacoes selvagens do SRAC (nao e so Texas), pico provavel mai-jul [E]; atividade noturna (TAMU: "dusk or night"; SRAC: logo apos por do sol e antes do nascer; USFWS HSI: "primarily at night").
- blue catfish: URL TPWD valida /blc/. Desova 75F so como frase generica de catfish; TPWD diz so "similar to channel catfish". Temp preferida, meses e atividade seguem null.
- flathead: URL TPWD /catfish/. Desova fim de maio a agosto, 75-80F (TPWD); atividade noturna (TPWD: "At night, they move into shallow areas to feed"); temp preferida 76-89F = telemetria de verao em lago do Oeste do Texas (Weller & Winter, Proc. SEAFWA 53; evita >32 C).
- bluegill: temp preferida 86F (30 C, maior crescimento de juvenis em laboratorio, Lemke 1977, resumo OSTI); o "40s" virou campo separado temp_feeding_floor_f; atividade crepuscular (Texas A&M); desova ~70F e pico mai-jun (TPWD) confirmados.
- striped bass: so temp max 75F segue (TPWD). Corroboracao: SEAFWA Smith (Lake Buchanan) "<25 C". Padrao de atividade null (a fonte so diz "feed irregularly").
- largemouth: quotes de temperatura corrigidas para o texto real do HSI (24-30 C; desova 16-22 C).

### Ainda null
temp preferida: crappie, blue catfish. Meses de desova: striped bass, blue catfish, hybrid (peixe de estocagem). Atividade: striped bass, blue catfish. Iscas de outono/inverno em varias.

### Conflitos / ressalvas
- Hybrid fertil x nao reproduz (acima). Channel catfish: SRAC 180 (85F) x TAMU (80-85F): faixa usada 80-85.
- O site do MN DNR (crappie ao amanhecer/anoitecer) deu 403 no download; nao entrou.
- Fontes do USFWS HSI no archive.org tem OCR com pontos faltando (nao achei texto de bluegill/channel/crappie HSI com temperatura otima).
