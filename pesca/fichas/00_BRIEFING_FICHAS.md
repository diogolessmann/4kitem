# Briefing: fichas de espécie do Hora de Pescar (Texas), 23/11/2026

## Pedido
Montar 15 fichas de espécie para um guia de pesca do Texas (site em inglês dos EUA). O motor dá nota de 0 a 10 por hora com regras de cada espécie (luz, temperatura, pressão, lua/solunar, maré no litoral) e diz que isca levar por estação.

## Regra de ouro
**A IA não inventa regra.** Cada número (temperatura, mês de desova, limite, tamanho) e cada afirmação de comportamento (come de madrugada, de noite, na maré mexendo) leva:
- `source_url` lido nesta pesquisa (página aberta por você, não lembrada);
- `quote`: trecho curto copiado da fonte (até 25 palavras) que sustenta o número;
- se não achou em fonte confiável, o campo fica `null` e entra na lista "não achei". Nunca preencher de memória.

Fontes aceitas, em ordem: TPWD (tpwd.texas.gov) > outra agência estadual ou federal dos EUA (USFWS, USGS, NOAA, FWC, LDWF etc.) > estudo revisado ou relatório técnico (ex.: USFWS "Habitat Suitability Index Models" FWS/OBS-82/10.x; "Species profiles: life histories and environmental requirements of coastal fishes" do USFWS/USACE; Coutant 1977 "Compilation of temperature preference data") > extensão universitária (Texas A&M AgriLife, SRAC). Nada de blog de pesca, loja ou fórum para número.

## As 15 espécies (confirmar com fonte que são as mais procuradas no Texas)
Doce (9): largemouth bass; white bass; striped bass; hybrid striped bass; crappie (white + black numa ficha só); channel catfish; blue catfish; flathead catfish; bluegill (sunfish).
Salgada (6): red drum; spotted seatrout; southern flounder; black drum; sheepshead; Atlantic croaker.
Se uma fonte do TPWD (pesquisa de pescador, creel survey, relatório de pesca) mostrar outra ordem, anotar e citar.

## Formato (JSON, uma lista de objetos; texto para o site em inglês dos EUA)
```json
{
  "id": "largemouth_bass",
  "common_name": "Largemouth Bass",
  "scientific_name": "...",
  "water": "fresh",
  "tpwd_profile_url": "https://tpwd.texas.gov/...",
  "habitat_short": {"text": "...", "source_url": "...", "quote": "..."},
  "temp_preferred_f": {"min": 0, "max": 0, "kind": "optimal growth | preferred | feeding", "source_url": "...", "source_title": "...", "quote": "..."},
  "temp_spawn_f": {"min": 0, "max": 0, "source_url": "...", "quote": "..."},
  "spawn_months_tx": {"months": [2, 3, 4], "source_url": "...", "quote": "..."},
  "activity": {"pattern": "crepuscular | nocturnal | diurnal | mixed", "detail": "...", "source_url": "...", "quote": "..."},
  "tide": {"preference": "...", "source_url": "...", "quote": "..."},
  "baits": [{"season": "spring | summer | fall | winter | all", "bait": "...", "technique": "...", "source_url": "...", "quote": "..."}],
  "regs_statewide": {"daily_bag": "...", "min_length_in": "...", "notes": "...", "source_url": "...", "checked": "2026-11-23"},
  "not_found": ["campos que ficaram sem fonte"]
}
```
- Temperaturas em °F (se a fonte dá °C, converter e dizer no `quote` o valor original).
- `tide` só nas salgadas (doce: `null`).
- `baits`: 3 a 6 itens por espécie, cobrindo as estações quando a fonte permitir. Fonte preferida: páginas do TPWD de "how to fish" / Angler Education / Texas Freshwater/Saltwater Fishing Guide.
- `regs_statewide`: só o limite ESTADUAL padrão do TPWD Outdoor Annual (tpwd.texas.gov/regulations/outdoor-annual/fishing/...). Anotar em `notes` se há exceções por lago/área. Nunca inventar.

## Restrições
- Só WebSearch e WebFetch. **Não usar navegador.**
- Orçamento: no máximo ~45 buscas+fetches por agente. Priorizar WebFetch de fonte primária.
- Gravar o JSON e uma nota curta (`*_NOTAS.md`: fontes usadas, o que não achou, dúvidas) nesta pasta.
- Devolver só um resumo de até 15 linhas, com [L] lido / [E] estimativa / [H] hipótese, e a lista do que NÃO achou.
