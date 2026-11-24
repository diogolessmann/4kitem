# salt_NOTAS (23/11/2026)

## Fontes abertas
- TPWD species pages: /huntwild/wild/species/{reddrum,strout,flounder,blackdrum,sheepshead,croaker}/ (os slugs red/sst/sfl/bdr/shd/atc dao 404 ou pagina errada)
- TPWD Outdoor Annual: drum-, seatrout-, flounder-, sheepshead-bag-length-limits (validas 1 Set 2026 a 31 Ago 2027) + Pocket Guide pwd_cd_v3400_100b.pdf (lido por pypdf; confirma tudo; croaker sem limite)
- TPWD brochura Red Drum (pwd_br_v3400_045): desova Ago-Nov; "second-most popular saltwater fish sought in Texas, falling just behind spotted seatrout"
- USFWS FWS/OBS-82/11.4 Lassuy 1983 seatrout (vliz.be PDF, texto lido por pypdf): temperaturas, desova >=70F, fev-out
- Resumos OSTI (so abstract): red drum 11.36 (tolerancia 2-37.5C), black drum 11.51 (12-33C; desova fev-mar), croaker 11.3 (5-35.5C; desova out-mar)

## Ressalvas
- Muitas quotes marcadas "(extracted summary)" vem do resumo do WebFetch (modelo pequeno), NAO sao copia literal confirmada. Reabrir as paginas TPWD antes de publicar. Literais confirmadas: brochura red drum, Lassuy, pocket guide.
- Flounder: temp/desova OSTI vieram so de snippet de busca (OSTI 7193777 nao aberto). ID OSTI do flounder incerto.
- Croaker: TPWD diz Ago-Out; USFWS diz Out-Mar. Conflito nao resolvido.
- Seatrout: desova fev-out e do Golfo, nao so Texas; a pagina TPWD nao da meses.
- Temperaturas "observed/tolerance" nao sao otimo; temp_preferred_f = null nesses casos (campos extras temp_tolerance_f / temp_observed_range_f).
- Lista das 6: so a brochura TPWD confirma seatrout #1 e red drum #2 em "sought". Nao achei pesquisa/creel TPWD ranqueando black drum, sheepshead, flounder, croaker (orcamento acabou).
- Flounder regs: Nov 1-Dec 14 fechado (bag 0), conforme pagina conferida em 23/11/2026.

## Nao achei
temp preferida (red drum, flounder, black drum, sheepshead, croaker); temp de desova (todos menos seatrout); tide incoming/outgoing para red drum, black drum, sheepshead, croaker; hora do dia para red drum, black drum, sheepshead, croaker; iscas por estacao (so 1 isca para black drum/sheepshead/croaker); profile USFWS do sheepshead (OSTI 429, UNT/DTIC 403).

## Rodada 2 (tapar buracos) - 23/11/2026
Verificacao igual a do fresh: 136 quotes true / 0 false no total (fresh+salt). Todas as quotes "(extracted summary)" foram substituidas por texto copiado da pagina real (TPWD baixada com Python), ou trocadas por outra fonte. Script: D:\bateria\hdp_gaps\apply.py.

### Preenchido [L]
- Tide: red drum = TPWD "Daily movement from the shallows to deeper waters is influenced by tides and water temperatures". Black drum, sheepshead, croaker = so a frase geral do TPWD (Learn to Fish 0639d) "Tidal movements influence the feeding activity of all species", dita na preference. Flounder = TPWD "best catches are made during an incoming tide and on dark nights", saida tambem serve. Seatrout: free-shrimping onde ha "strong tidal flow" + frase geral.
- Atividade: red drum 'mixed' (USFWS: juvenis comem "night and day"; adultos sem fonte); black drum 'mixed' (LSU Sea Grant: dia e noite, menos de manha cedo); flounder noturno (TPWD + USFWS: 89% da captura a noite); seatrout crepuscular (TPWD: manha cedo e fim de tarde no calor; sob luzes a noite). Sheepshead e croaker: sem fonte, pattern null.
- Desova: flounder 10-12 (USFWS: migra para o Golfo de outubro a dezembro "apparently to spawn"; a fonte antiga OSTI so por snippet foi removida); black drum fev-abr + jun-jul (TPWD); sheepshead fev-mar (TPWD); croaker ago-out (TPWD).
- Baits: seatrout, flounder, black drum (4 cada), sheepshead (2), croaker (1) com quotes reais.
- Sem preferencia de temperatura em fonte aceita para red drum, flounder, black drum, sheepshead, croaker (so faixas observadas/tolerancia, mantidas em campos separados e marcadas "nao e otimo"). Otimo de red drum encontrado e so de eclosao/larva (25 C, Holt 1981).

### Conflitos
- Croaker: TPWD ago-out x USFWS out-mar. Nao resolvido (os dois ficam no JSON).
- Flounder meses: derivados da migracao (out-dez); resumo USFWS diz desova out-fev.

### Ainda null
temp preferida (5 especies); temp de desova (todas menos seatrout); atividade de sheepshead e croaker; tide especifico por especie para black drum, sheepshead, croaker; iscas por estacao (sheepshead 2, croaker 1).
