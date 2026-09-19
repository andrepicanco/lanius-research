# MeanRev1 — Referência de Parâmetros de Entrada

Este documento explica cada parâmetro editável (`input`) do EA `MeanRev1.mq5`, agrupado na mesma ordem em que aparecem no código. Use como referência ao configurar o EA no gráfico ou ao montar um `.set` para o Strategy Tester.

## Timeframe

| Parâmetro | Tipo | Padrão |
|---|---|---|
| `InpTimeframe` | `ENUM_TIMEFRAMES` | `PERIOD_CURRENT` |

Define em qual timeframe o EA calcula SMAs, RSI e detecta barras novas — **não precisa ser o mesmo timeframe do gráfico visível**. Com `PERIOD_CURRENT`, o EA usa o período do próprio gráfico onde está anexado.

## Estratégia / Sinal (SMA curta/longa + streak de RSI)

| Parâmetro | Tipo | Padrão | O que faz |
|---|---|---|---|
| `InpSMAShortPeriod` | `int` | `21` | Período da SMA curta, usada junto com a SMA longa para definir a tendência de longo prazo. |
| `InpSMALongPeriod` | `int` | `88` | Período da SMA longa. Tendência de alta quando `SMA curta > SMA longa`; de baixa caso contrário. |
| `InpRSIPeriod` | `int` | `2` | Período do RSI (curto, tipo "Connors"), usado para identificar zonas de sobrecompra/sobrevenda. O RSI nativo do MQL5 já usa suavização de Wilder, igual à fórmula validada no notebook de pesquisa. |
| `InpRSIOverboughtLevel` | `double` | `80.0` | Nível acima do qual o RSI é considerado em zona de sobrecompra. |
| `InpRSIOversoldLevel` | `double` | `20.0` | Nível abaixo do qual o RSI é considerado em zona de sobrevenda. |
| `InpStreakLength` | `int` | `2` | Quantos candles fechados consecutivos o RSI precisa permanecer na zona (sobrecompra ou sobrevenda) para gerar o gatilho 1. |

**Lógica dos dois gatilhos:** a cada barra fechada, o EA conta quantos candles seguidos o RSI está acima de `InpRSIOverboughtLevel` (ou abaixo de `InpRSIOversoldLevel`) — o sinal dispara **uma única vez**, exatamente no candle em que essa sequência atinge `InpStreakLength` (não "pelo menos", e sim "exatamente"; se a sequência continuar além disso, não há novo disparo, evitando reentradas repetidas no mesmo movimento). Esse é o **gatilho 1**. O **gatilho 2** é o alinhamento com a tendência: uma sequência de sobrevenda só vira sinal de **compra** se a SMA curta estiver acima da SMA longa (tendência de alta); uma sequência de sobrecompra só vira sinal de **venda** se a SMA curta estiver abaixo da SMA longa (tendência de baixa). Combinações contra a tendência (sobrecompra em alta, sobrevenda em baixa) nunca geram sinal.

## Saída por tempo

| Parâmetro | Tipo | Padrão | O que faz |
|---|---|---|---|
| `InpExitBars` | `int` | `6` | Quantos candles fechados depois da entrada a posição é encerrada a mercado, caso SL/TP não tenham disparado antes. A barra de entrada não conta — a posição fecha na barra em que exatamente `InpExitBars` candles adicionais já se fecharam. |

## Stop Loss / Take Profit

| Parâmetro | Tipo | Padrão | O que faz |
|---|---|---|---|
| `InpSLATRPeriod` | `int` | `14` | Período do ATR usado para calcular a distância do Stop Loss inicial. |
| `InpSLATRMultiplier` | `double` | `2.0` | Multiplicador do ATR: `SL = preço de entrada ∓ (ATR × este valor)`. |
| `InpTP_RMultiple` | `double` | `2.0` | Múltiplo da distância do Stop Loss usado para calcular o Take Profit (ex: `2.0` = TP a 2x a distância do SL, um "2R"). |

**Ponto de atenção:** diferente da estratégia 9.1 (que calcula o SL a partir do extremo da barra de rompimento), a mean_rev_1 entra a mercado sem uma barra de referência de breakout — por isso o SL é baseado em ATR, e não em pontos fixos nem no extremo de candle. **Essa combinação de SL/TP (ATR-based / R-multiple) é uma adição operacional, não validada no notebook de pesquisa** — o notebook só testou a saída por tempo (`InpExitBars`), medindo retorno bruto sem stop. Recomenda-se rodar o Strategy Tester observando com que frequência o SL ou o TP disparam antes do exit por tempo, antes de confiar nos valores padrão em conta real/demo.

## Trailing Stop (ATR, opcional)

| Parâmetro | Tipo | Padrão | O que faz |
|---|---|---|---|
| `InpUseTrailing` | `bool` | `false` | Liga/desliga o trailing stop. Desligado por padrão — avalie primeiro o comportamento do sistema-base (SL ATR fixo + TP em R + saída por tempo). |
| `InpTrailATRPeriod` | `int` | `14` | Período do ATR usado para calcular a distância do trailing. Independente de `InpSLATRPeriod` (o SL inicial e o trailing podem usar períodos de ATR diferentes). |
| `InpTrailATRMultiplier` | `double` | `2.0` | Multiplicador do ATR: o SL passa a seguir o preço a uma distância de `ATR × este valor`. |

O trailing só é reavaliado a cada barra nova fechada (não a cada tick) e **só aperta o stop, nunca afrouxa** — se o novo nível calculado for pior que o SL atual, ele é ignorado.

## Dimensionamento de posição (lote)

| Parâmetro | Tipo | Padrão | O que faz |
|---|---|---|---|
| `InpUseFixedLot` | `bool` | `false` | Se `true`, ignora o cálculo por risco e usa sempre `InpFixedLot`. |
| `InpFixedLot` | `double` | `0.10` | Lote fixo usado quando `InpUseFixedLot = true`. |
| `InpRiskPercent` | `double` | `1.0` | % do saldo da conta arriscado por operação (modo recomendado, usado quando `InpUseFixedLot = false`). |

No modo por risco-%, o lote é calculado a partir da distância até o SL (ATR-based) e do valor por tick do símbolo (`SYMBOL_TRADE_TICK_VALUE`/`SYMBOL_TRADE_TICK_SIZE`) — funciona igual para qualquer instrumento, sem precisar de tabela de conversão manual. O resultado é sempre arredondado para o step de volume permitido pela corretora.

## Gestão de ordens

| Parâmetro | Tipo | Padrão | O que faz |
|---|---|---|---|
| `InpMagicNumber` | `long` | `552601` | Identificador único das ordens/posições deste EA — usado para reconhecer "isto é meu" e não mexer em posições manuais ou de outros EAs no mesmo símbolo. **Valor placeholder**: escolha um número definitivo, distinto do `910091` usado pela estratégia 9.1, antes de rodar as duas em paralelo na mesma conta. |
| `InpTradeComment` | `string` | `"MeanRev1"` | Texto anexado a cada ordem enviada (aparece no histórico/terminal). |
| `InpSlippagePoints` | `int` | `10` | Desvio máximo de preço tolerado (em pontos) ao executar ordens de abertura e fechamento. |

## Filtros opcionais (desligados por padrão)

| Parâmetro | Tipo | Padrão | O que faz |
|---|---|---|---|
| `InpMaxSpreadPoints` | `int` | `0` | Se maior que 0, bloqueia novas entradas quando o spread atual (em pontos) ultrapassar esse valor. `0` = filtro desligado. |
| `InpUseTradingHoursFilter` | `bool` | `false` | Liga/desliga um filtro simples de horário para novas entradas. |
| `InpStartHour` | `int` | `0` | Hora de início da janela permitida (horário do servidor/corretora), usada só se o filtro acima estiver ligado. |
| `InpEndHour` | `int` | `23` | Hora de fim da janela permitida. Se `InpStartHour > InpEndHour`, a janela é interpretada como "atravessando a meia-noite". |

## Diagnóstico

| Parâmetro | Tipo | Padrão | O que faz |
|---|---|---|---|
| `InpLogLevel` | `ENUM_LOG_LEVEL` | `LOG_INFO` | Nível mínimo de mensagens que aparecem na aba "Experts"/Journal do MT5: `LOG_DEBUG` (inclui o detalhe de cada avaliação de sinal — tendência, streaks, RSI), `LOG_INFO` (eventos normais: posição aberta, saída por tempo), `LOG_WARN` (situações incomuns, ex: SL/TP abaixo da distância mínima da corretora), `LOG_ERROR` (falhas de execução). Use `LOG_DEBUG` ao validar o EA pela primeira vez.

## Símbolo e instrumento

Não existe um parâmetro de símbolo — o EA sempre opera no `_Symbol` do gráfico onde está anexado (uma instância por gráfico/instrumento). Universo-alvo, majoritariamente Forex: EURUSD, GBPUSD, USDJPY, AUDNZD, AUDJPY, AUDCHF, USDBRL — ocasionalmente também metais, índices internacionais, cripto e outras commodities (ex: XAUUSD, usado nos testes exploratórios do notebook). Para rodar em vários instrumentos, anexe uma instância separada em cada gráfico.
