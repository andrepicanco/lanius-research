# strategy-monitor

Acompanha as estratégias do Lanius contra um **baseline** aprovado e busca parâmetros melhores, com um
**motor de backtest em Python** que lê barras do seu MT5 aberto. Sem Strategy Tester, sem agendador, sem
permissão de administrador. Independente dos outros projetos: não importa código de `risk-mgmt`,
`mt5-backtest-py` nem `strategies/`.

## Estado

| Parte                                                                  | Situação                                                                             |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `monitor`: período novo de cada baseline vs baseline, com semáforo      | Pronto; usa o motor Python                                                           |
| Motor Python (IdxSwing91, BreakoutDonchian1)                            | Pronto; paridade medida contra o Tester nos dois baselines (seção "Motor")          |
| `discover`: busca de parâmetros com OOS, consistência e vizinhança      | Pronto; rodado de verdade no DAX40 (sem candidato: ver "Descoberta")                 |
| Resumo no Telegram                                                      | Testado pelo André                                                                   |
| Tela local (`interface.bat`)                                            | Pronta: monitor, baselines/paridade e descoberta com seleção de parâmetros           |

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy config\monitor.example.yaml config\monitor.yaml   # ajuste baseline_dir e mt5.terminal_path
copy config\discovery.example.yaml config\discovery.yaml
python -m pytest tests -q
```

`mt5.terminal_path` é o `terminal64.exe` do MT5 que você usa. **Ele precisa estar aberto e logado** quando
você roda `monitor`, `parity` ou `discover`: o motor se conecta a essa instância pela API Python
(`MetaTrader5`), **somente para ler** barras e a especificação do símbolo; nunca envia ordens. Se o MT5 estiver
fechado, o motor **recusa** em vez de tentar abri-lo: a API Python poderia lançar o terminal (não verifiquei se
lança), e um lançamento aciona o LiveUpdate, que pede administrador.

## Configuração (`config/monitor.yaml`, ignorado pelo git)

| Chave                                   | O que é                                                                                                          |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `baseline_dir`                          | Pasta dos baselines `.xlsx`. Também vale a variável `STRATEGY_MONITOR_BASELINE_DIR` ou `--baseline-dir` (esta vence). |
| `min_trades`                            | Abaixo disso o win rate nunca passa de `ATENCAO` (padrão 30).                                                    |
| `thresholds.confidence`                 | Nível do intervalo de Wilson (padrão 0,95).                                                                      |
| `thresholds.dd_fail_factor`             | `FALHA` se o DD corrente passar de fator × o do baseline (padrão 1,5).                                           |
| `thresholds.max_drawdown_pct`           | Teto absoluto de DD no **monitor** (`null` = sem teto). O `discover` tem o próprio teto, em `discovery.yaml`.    |
| `thresholds.trades_per_month_ratio`     | Faixa aceita de trades/mês vs baseline; fora dela, `ATENCAO` (padrão 0,5 a 2).                                   |
| `mt5.terminal_path`                     | `terminal64.exe` do MT5 que você usa (aberto e logado).                                                          |
| `mt5.cache_dir`                         | Cache de barras (padrão `data_cache/`). **Não apague sem necessidade**: o terminal só entrega ~100 mil barras por série, e o cache guarda histórico que ele talvez não devolva mais. |
| `outputs_dir`                           | Onde saem os resultados do `discover` (padrão `outputs/`).                                                       |

Credenciais do Telegram ficam em `.env`, nunca aqui (ver "Telegram").

## Tela (front-end)

```powershell
.\interface.bat      # abre http://localhost:8501 no navegador; feche a janela do terminal para encerrar
```

Streamlit local (sem administrador, só escuta em `localhost`). Faz o mesmo que a CLI, com cliques:

| Página         | O que tem                                                                                                                                                                                 |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Monitor**    | Escolhe estratégias e a data final, roda o motor e mostra, por estratégia: selo de estado (ícone + texto), tabela baseline × período novo, curva de **R acumulado** dos dois, verificações e trades. Exporta **relatório HTML** autocontido e envia o resumo ao Telegram (botão explícito). |
| **Baselines**  | Tabela dos baselines e o botão **Conferir motor (paridade)**: roda o motor no período do baseline e compara com o Tester (texto + curvas).                                                |
| **Descoberta** | Escolhe a estratégia, os ativos (qualquer símbolo do MT5), **quais parâmetros liberar** (e a grade de cada um: faixa, lista ou múltiplos do baseline), janelas, teto de DD e validação. Mostra o plano e o tempo estimado, roda, lista finalistas com veredito e baixa `report.md`, `results.csv` e os `.set` candidatos. |

O botão de Telegram lê o `.env` **no momento do clique** (a tela não passa pela largada da CLI, que é onde o arquivo
costumava ser lido: um bug corrigido em 2026-11-24 fazia a tela acusar "faltando TELEGRAM_*" com o `.env` preenchido).

Os valores iniciais da página Descoberta vêm de `config/discovery.yaml` (os "atuais"). O botão **Salvar como padrão**
grava a seleção de volta nesse arquivo (mantém as outras estratégias; os comentários do arquivo se perdem, o modelo
comentado fica em `discovery.example.yaml`). Cores: azul = baseline/Tester, laranja = período novo/motor; o estado
nunca depende só de cor. A tela não foi conferida visualmente por mim em navegador: foi exercitada por teste
automatizado do Streamlit com o MT5 real e o servidor subiu (HTTP 200).

## Uso no dia a dia

```powershell
cd C:\Users\15339\Documents\andre\lanius\strategy-monitor
.\monitor                     # todos os baselines (e envia o resumo ao Telegram)
.\monitor DAX40               # só o DAX40
.\monitor --until 2026-11-20  # até 19/11 (a data final é exclusiva)
```

`monitor.bat` usa o venv do projeto e **sempre** passa `--telegram`. Para rodar sem enviar, use o Python direto:
`.\.venv\Scripts\python.exe -m strategy_monitor monitor`.

### Conceitos

| Termo              | O que é                                                                                                                              |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| **Baseline**       | O backtest que você aprovou para uma estratégia × ativo: um `.xlsx` do Tester na pasta `baseline_dir`. É a régua.                    |
| **Janela julgada** | Só os trades com **entrada a partir da data final do cabeçalho do baseline**. O que o baseline já cobriu (inclusive forward) é "visto". |

Comparar o período do baseline consigo mesmo não diz nada; o que importa é se a estratégia se comporta igual
**fora** da amostra que a aprovou.

```
  baseline (a régua)                                   o que o monitor julga
 |-- 2026-07-01 ... 2026-10-05 -- (forward) -- 2026-11-08 |---- 2026-11-08 ........ hoje -->
     trades do arquivo      último trade   período do cabeçalho    └ fim do período do baseline
```

Se o último trade do arquivo fechar depois do cabeçalho (não deveria), vale o que for mais tarde. Toda saída
mostra o baseline, a data de corte e quantos trades foram julgados.

**Amostra pequena é o normal.** Depois do corte são poucas semanas de trades. Com menos de `min_trades` (30) o
win rate nunca passa de `ATENCAO`; leia o semáforo como acompanhamento, não como conclusão.

### Como ler o resultado

```
IdxSwing91 · DAX40 M12  [OK]                                         <- veredito geral = o pior das verificações
Baseline: 2026-07-01 → 2026-11-08 (último trade: 2026-10-05)         <- a régua
Julgando entradas a partir de 2026-11-08 (fim do período do baseline): 8 trades.
Janela corrente: 2026-11-10 → 2026-11-19 (n=8, líquido +55.98)       <- o que foi julgado
  [OK] Win rate vs breakeven: win 37.5% | BE 26.3% | folga +11.2 pp | IC95 13.7%–69.4% | baseline: win 40.9%, BE 34.5% [amostra pequena: n=8 < 30]
  [OK] Expectativa (R): +0.210 R/trade (n=8) | baseline +0.125 R
  [OK] Profit factor: 1.68 | baseline 1.31
  [OK] Drawdown (saldo fechado): 0.43% | baseline 2.51%
```

| Estado    | Significa                                                                                                                                                              |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `OK`      | Dentro do esperado.                                                                                                                                                    |
| `ATENCAO` | Fora do esperado, mas ainda compatível com sorte (win rate abaixo do breakeven com intervalo de confiança que ainda o contém), ou amostra pequena demais para concluir. |
| `FALHA`   | Fora do esperado de forma estatisticamente clara (o teto do intervalo do win rate abaixo do breakeven).                                                                |
| `NA`      | Não há dado para julgar (sem trades na janela, ou sem ganho e perda para calcular).                                                                                    |

A linha **Trades/mês** só aparece com janela de pelo menos 30 dias. O código de saída é `0` mesmo com
`ATENCAO`/`FALHA` (só erros mudam o código): se encadear em script, leia o texto.

### Todos os comandos

| Comando                                  | Para que serve                                                                              | Precisa do MT5 aberto?   |
| ---------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------ |
| `monitor [ALVO] [--until] [--telegram]`  | Período novo de cada baseline vs baseline.                                                  | Sim                      |
| `baselines`                              | Lista os baselines: período, win rate, breakeven, PF, E[R], DD e o corte que o monitor usa. | Não                      |
| `parity [--only ALVO]`                   | Confere o motor contra os trades do Tester em cada baseline.                                | Sim                      |
| `discover [--only] [--trials] [--dry-run] [--until] [--telegram] [--discovery-config]` | Busca parâmetros melhores; nunca aplica. | Sim (exceto `--dry-run`) |
| `telegram-test`                          | Envia uma mensagem de teste.                                                                | Não                      |

A tela (`interface.bat`) cobre `monitor`, `parity` e `discover`.

Opções gerais, **antes** do comando: `--baseline-dir PASTA`, `--config ARQUIVO`, `--env-file ARQUIVO`.
`ALVO` é um texto que casa com o nome do baseline, sem diferenciar maiúsculas (`DAX40`, `nas100`, `breakout`).

## Como configurar um baseline

Um baseline é **um arquivo `.xlsx` do Strategy Tester por estratégia × ativo × timeframe**, na pasta
`baseline_dir`. O nome do arquivo não importa: o monitor identifica o baseline pelo que está dentro (EA +
ativo + timeframe). Dois arquivos para a mesma combinação são erro. Para **adicionar** uma estratégia, basta
pôr o arquivo na pasta: não há nada a registrar em outro lugar, e `monitor` sem alvo passa a incluí-la
(se o motor a implementar: ver "Motor").

**Passo a passo**

1. No Strategy Tester do seu terminal, rode o backtest aprovado: modelagem "Cada tick", **depósito** real do
   teste, período que você aprovou e os **inputs exatamente iguais aos da conta**.
2. Exporte o relatório para `.xlsx` (botão direito no relatório do Tester; o nome exato do menu depende da
   versão do MT5, não confirmei). O arquivo precisa ter o cabeçalho com os **inputs** e as tabelas **Ordens**
   e **Negociações**.
3. Salve em `baseline_dir` e confira (não abre o MT5): `.\.venv\Scripts\python.exe -m strategy_monitor baselines`.
   Confira EA, ativo, timeframe, período, `n` e as métricas contra o que o Tester mostrou.
4. Com o MT5 aberto, confira o motor: `... -m strategy_monitor parity --only <ativo>`.

**O que o monitor lê do arquivo**

| Do `.xlsx`                           | Uso                                                                                     |
| ------------------------------------ | --------------------------------------------------------------------------------------- |
| EA, ativo, timeframe                 | Identificam o baseline e a estratégia do motor.                                         |
| Período `AAAA.MM.DD - AAAA.MM.DD`    | **A data final define o início da janela julgada.**                                     |
| Inputs                               | O motor roda com **exatamente** estes inputs.                                           |
| Depósito                             | Saldo inicial do motor (dimensionamento por risco %) e métricas de drawdown.            |
| Ordens (S/L inicial) e Negociações   | Trades, win rate, breakeven, PF, drawdown e **R** (o R precisa do S/L inicial da ordem). |

Só foi validado com terminal em **português**.

**Forward.** Se o seu backtest usa "Para frente", o relatório mostra o período todo no cabeçalho mas o arquivo
só traz os trades da parte de backtest (caso do DAX40). Tudo bem: o monitor trata o dia final do cabeçalho
como o fim do baseline. Exporte só depois de decidir até que data você já "viu" a estratégia, porque é essa
data que passa a ser o corte.

**Regra: o baseline tem de refletir os parâmetros que estão rodando na conta.** O monitor testa os inputs do
**arquivo**, não os do EA em execução, e **não tem como detectar** que você mudou um parâmetro na conta.

- Mudou qualquer input de uma EA em operação (inclusive pequenos ajustes de trailing/horário)? Refaça o
  backtest com os inputs novos, exporte um `.xlsx` novo e **substitua** o arquivo antigo.
- O corte passa a ser o fim do período do **novo** baseline: a contagem de trades novos recomeça do zero.
  Isso é esperado: do ponto de vista do monitor é uma estratégia nova.
- Candidatos do `discover` (`.set`) só valem depois de aplicados à conta **e** de virarem baseline. Até lá o
  monitor continua julgando a versão antiga.
- Ao subir uma mudança para a conta, confira os inputs do `.xlsx` (cabeçalho) contra os do EA anexado ao
  gráfico. Esta checagem é manual.

## Motor de backtest (`strategy_monitor/engine/`)

Reproduz o que o EA faz, barra a barra, sobre dados do próprio MT5. Cada backtest leva ~0,1 s com cache
(o Tester levava 20 s ou mais).

**Dados.** Barras e especificação do símbolo (tick, lote, stops, swap) vêm da API do MT5 e ficam em cache
local (`data_cache/`, ignorado pelo git), que cresce a cada uso. Fatos verificados na sua instância
(build 6230, 2026-11-24):

- o terminal entrega no máximo `maxbars` = 100 000 barras por série; M1 só alcança ~3 meses;
- o terminal **não guarda ticks históricos** (0 ticks para março): não há como usar ticks reais;
- por isso o motor usa a **menor sub-barra que divide o timeframe do EA e cobre a janela** (DAX40 M12 → M2/M4;
  NAS100 H1 → M4) para decidir o que veio primeiro dentro de cada barra.

**Pressupostos** (em `engine/core.py`): barras são de bid e o ask é bid + spread da barra; compra entra no ask e
sai no bid (venda, o inverso); SL e TP na **mesma** sub-barra → vale o SL; gap além do SL → sai na abertura;
posição aberta no fim dos dados fecha no último preço (como o Tester, motivo "other"); ATR do MT5 é média
**simples** do true range; swap por noite, com o triplo no dia configurado; comissão zero (os baselines têm 0).

**Estratégias implementadas:** IdxSwing91 e BreakoutDonchian1, transcritas dos `.mq5` (não do port Python de
`strategies/9-1_lanius-cap/IdxSwing91_Python`, que está defasado). Outra estratégia exige novo módulo em
`engine/`; o filtro de volume de ticks do Breakout **não é reproduzível** (os ticks do Tester são gerados) e fica
desligado por padrão no EA.

**Fidelidade medida (`parity`), em 2026-11-24:**

| Teste                                | Trades pareados  | E[R] Tester → motor | Outros                          |
| ------------------------------------ | ---------------- | ------------------- | ------------------------------- |
| DAX40, período do baseline           | 93/93 (motor 95) | +0,125 → +0,131     | SL inicial idêntico             |
| NAS100, período do baseline          | 161/161          | +0,091 → +0,089     | PF 1,21 → 1,20; DD 3,12 → 3,13% |
| DAX40, janela nova de 23/11          | 8/8              | +0,208 → +0,210     | preços a centavos               |
| NAS100, janela nova de 23/11         | 8/8              | −0,176 → −0,176     |                                 |

O líquido em dinheiro difere (ex.: DAX 372 vs 403): swap e arredondamento de lote. O motor serve para E[R],
win rate, PF e DD; não para prever o saldo em dinheiro ao centavo.

**Ativo sem baseline não tem paridade medida.** O motor roda em qualquer símbolo do MT5, mas a fidelidade
acima foi medida só onde há baseline; em outro ativo é uma referência, não uma garantia.

## Descoberta de parâmetros (`discover`)

```powershell
.\.venv\Scripts\python.exe -m strategy_monitor discover --dry-run          # plano e tempo estimado
.\.venv\Scripts\python.exe -m strategy_monitor discover --only DAX40       # roda (~0,3 s por backtest)
```

**Nada é aplicado.** A saída fica em `outputs/discovery/<data>/`: `report.md`, `results.csv` (todas as
tentativas, gravado linha a linha) e, **só para quem passou nos testes**, um `.set` em `candidates/`.

**Só varia o que você liberar.** `config/discovery.yaml` (copie de `discovery.example.yaml`) lista, por
estratégia, os parâmetros liberados e os valores possíveis (`min/max/step`, `values` ou `factors`, que são
multiplicadores do valor do baseline). **Esse arquivo é um exemplo que eu montei, não uma escolha sua**:
revise os parâmetros e o teto de drawdown. Recusados de propósito: `InpMagicNumber`, `InpTradeComment`, `InpLogLevel` (identificam o EA ou não afetam o
resultado) e `InpRiskPercent` (use o lote fixo para variar o tamanho). `max_drawdown_pct` é obrigatório.

**Lote fixo (`InpUseFixedLot` + `InpFixedLot`).** Ativos diferentes pedem tamanhos diferentes para caber no teto de
drawdown. Medido no DAX40 (jan–set, mesma estratégia): E[R] **−0,0351 em todos os tamanhos**, e o DD vai de 8,5%
(risco de 0,3%) a 7,5% (lote 0,5), 1,5% (0,1) e 0,76% (0,05). Ou seja, **o lote muda o DD e o dinheiro, não o E[R]**.
Consequências:

- Libere **os dois juntos**: `InpFixedLot` só vale com `InpUseFixedLot` ligado (a tela avisa se não for assim).
- Sob um teto de DD, reduzir o lote faz tentativas "caberem": no exemplo acima, 44 de 120 passaram o teto de 5%,
  contra 0 antes. Isso **não é edge**: é só o tamanho certo para o teto.
- Como o E[R] não depende do lote, o vizinho que só troca o lote **não conta** na mediana da vizinhança (repete o
  E[R] do finalista e inflaria a robustez). O mesmo vale para qualquer parâmetro que não mude o E[R].
- Os finalistas tendem a se parecer (a mesma estratégia com lotes diferentes disputa as vagas do `top_k`).

**Método (por estratégia × ativo):**

1. **Janelas.** OOS = últimos `oos_days` (60) até `--until`; IS = `is_days` (240) imediatamente antes. A busca
   **nunca vê o OOS**.
2. **Busca no IS:** a referência (inputs do baseline) mais `trials` (300) combinações sorteadas, sem repetição,
   com semente fixa. Objetivo: maximizar **E[R]**, com n ≥ `min_trades_is` e drawdown ≤ teto.
3. Os `top_k` (5) melhores passam por três testes que a busca nunca viu. **CANDIDATO** só quem passa nos três:
   - **OOS:** n mínimo, DD ≤ teto, E[R] > 0 e maior que o da referência no mesmo OOS;
   - **consistência:** o IS é dividido em `folds` (3) trechos; E[R] > 0 em pelo menos `min_positive_folds` (2);
   - **vizinhança:** cada parâmetro vai ao valor vizinho da grade, um por vez; a **mediana** do E[R] dos
     vizinhos no IS precisa ser > 0. Pico isolado é sorte. Parâmetro sem efeito (ex.: multiplicador do trailing
     com o trailing desligado) repete o resultado do finalista e é descartado dessa conta.

**O viés de seleção é o risco principal.** Com N tentativas, o melhor do IS é inflado mesmo sem edge algum (o
máximo esperado de N ruídos normais é ~√(2 ln N) desvios acima da média). O relatório compara o melhor com
esse piso. Com centenas de tentativas isso pesa, por isso os três testes. O número que vale é o do OOS, e ele
também sofre seleção (`top_k`).

**Resultado real (DAX40, 150 tentativas, 2026-11-24):** a referência dá E[R] −0,035 no IS (jan–set), 29 de 150
tentativas ficam com E[R] > 0 no IS, o melhor com +0,068, e **nenhuma respeita o teto de 5%** (o menor DD entre
as 150 é 5,4%). Ou seja: com esse teto não há candidato; com teto maior o relatório passaria a mostrar
finalistas. Isso confirma o achado anterior: o baseline do DAX40 foi aprovado numa janela favorável.

**Limites:** um único OOS (um só regime); o motor é uma aproximação do Tester; os testes de consistência e
vizinhança reduzem, não eliminam, o viés de seleção; um ativo novo usa os inputs de outro baseline como
referência (o stop em pontos muda de escala): o `--dry-run` avisa. Antes de levar um candidato à conta, rode-o
em forward (demo) e refaça o baseline.

## Glossário

| Termo                 | O que significa                                                                                                                                                                           |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **R**                 | Unidade de risco de **um trade**: a distância da entrada até o stop inicial, em dinheiro (distância × volume). Um trade que ganhou o dobro do que arriscava fez +2 R; um stop cheio fez −1 R. |
| **E[R]** (expectância) | A **média do resultado por trade, em R**. +0,125 R = em média, cada trade ganha 12,5% do que arrisca. É o objetivo do `discover` porque **não depende do tamanho do lote**: compara estratégias e ativos na mesma régua. Positivo = ganha em média; negativo = perde. |
| **IS** (*in-sample*)  | A janela **em que a busca testa as combinações** (`is_days`, 240 dias). Aqui qualquer parâmetro pode parecer bom por sorte, porque foi escolhido olhando esses dados.                      |
| **OOS** (*out-of-sample*) | A janela **que a busca nunca viu** (`oos_days`, 60 dias, a mais recente). Só os finalistas são avaliados nela. É a prova do "funcionou fora dos dados em que foi escolhido".          |
| **Trechos (folds)**   | O IS partido em `folds` pedaços iguais. Um edge de verdade costuma ser positivo em mais de um pedaço; um que vive num só é suspeito.                                                       |
| **Vizinhança**        | Mover um parâmetro por vez para o valor vizinho da grade. Se os vizinhos são ruins, o finalista é um "pico" isolado (sorte).                                                              |
| **Referência**        | Os inputs do baseline, sem alteração, rodados na mesma janela. Um candidato precisa superá-la no OOS.                                                                                     |
| **n**                 | Número de trades da janela. Poucos trades = resultado barulhento (veja o intervalo de confiança).                                                                                        |
| **DD**                | Drawdown máximo do saldo de trades fechados, em % do pico. É o que o teto `max_drawdown_pct` limita.                                                                                      |
| **Viés de seleção**   | Testar muitas combinações e ficar com a melhor infla o resultado mesmo sem edge algum. Por isso o número que vale é o do OOS, e por isso existem trechos e vizinhança.                    |

## Telegram

Mesmo bot e chat do `risk-mgmt`. As credenciais **nunca** vão em YAML versionado nem em flag. Ficam em
`strategy-monitor/.env` (ignorado pelo git; modelo em `.env.example`):

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...        # id NUMÉRICO do chat, não o nome do bot
```

- Formato `CHAVE=valor`, sem aspas (aspas, `export`, comentários `#`, BOM e quebra de linha do Windows são tolerados).
- **Variável já definida no ambiente do Windows vence o arquivo** (`setx TELEGRAM_BOT_TOKEN "..."` não precisa de admin).
- Erro de credencial lista só os **nomes** que faltam; se a rede falhar, o token é mascarado, porque faz parte da URL.
- A CLI lê o `.env` na largada; a tela, ao clicar em enviar. Variável do Windows vence o arquivo nos dois.
- Valide com `... -m strategy_monitor telegram-test` (envia uma mensagem de teste ao seu chat).

## Racional do win rate de breakeven

**Definição** (calculada dos trades do run, líquidos de swap e comissão): `BE = perda_média / (ganho_médio + perda_média)`.

Com win rate `p`, ganho médio `W` e perda média `L`, a expectativa por trade é `p·W − (1−p)·L`, que zera em
`p = L / (W + L)`. Acima disso a estratégia ganha dinheiro em média; abaixo, perde. Usa-se o payoff
**realizado** (e não `1/(1+RR)`) porque os EAs do Lanius usam trailing ATR e stop que se move: ganho e perda
realizados diferem dos configurados.

- "Win rate > BE" é o mesmo que "expectativa > 0 em dinheiro" (e PF > 1). A folga põe isso na escala do win rate.
- O BE **não é fixo**: muda a cada run, junto com o payoff. Com poucos trades ele é instável.

| Condição                                                               | Estado  |
| ---------------------------------------------------------------------- | ------- |
| win rate ≥ BE                                                          | OK      |
| win rate < BE, mas o limite superior do IC ≥ BE (indistinguível do BE) | ATENCAO |
| limite superior do IC < BE (significativamente abaixo)                 | FALHA   |

Compara-se com o **intervalo de Wilson** (95%), não só com o ponto. Nos dois baselines atuais o limite inferior
do IC95 do win rate fica **abaixo** do BE (NAS100: 34,3% < 37,0%; DAX40: 31,4% < 34,5%): mesmo no baseline o
excesso sobre o breakeven não é estatisticamente distinguível de zero com esse n.

## Outras verificações

- **Expectativa em R:** líquido ÷ risco inicial em dinheiro, com risco = |entrada − SL inicial| × volume × k. `k`
  (dinheiro por ponto por lote) é estimado dos próprios trades (mediana), sem depender de `tick_value`.
- **Profit factor**, **drawdown** (saldo de trades fechados) e **trades/mês** (mudança de comportamento).
- Drawdown: FALHA se passar do teto `max_drawdown_pct` (quando configurado) ou de `dd_fail_factor` × o do
  baseline. A janela corrente costuma ser mais curta, então a comparação favorece o corrente: leia como piso.

## Estrutura do código

| Caminho                                | Papel                                                                                          |
| -------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `strategy_monitor/cli.py`              | Comandos (`monitor`, `baselines`, `parity`, `discover`, `telegram-test`).                       |
| `strategy_monitor/services.py`         | Casos de uso compartilhados pela CLI e pela tela (rodar monitor, paridade, descoberta, Telegram). |
| `strategy_monitor/engine/`             | Motor: `mt5data` (barras e cache), `core` (posição, lote, swap), `idxswing91`, `breakout_donchian1`, `indicators`, `parity`, `run`. |
| `strategy_monitor/discovery*.py`       | Busca, validação e relatórios; `discovery_config.py` lê o `discovery.yaml`.                    |
| `strategy_monitor/window.py`, `compare.py`, `metrics.py`, `report.py` | Corte do período julgado, semáforo, métricas, texto do resumo.   |
| `strategy_monitor/tester_report.py`    | Leitor do `.xlsx` de baseline.                                                                 |
| `strategy_monitor/ui/`                 | Tela (`app.py`), lógica testável (`logic.py`) e relatório HTML (`html_export.py`).             |
| `tests/`                               | Suíte sem MT5 (`python -m pytest tests -q`). A fidelidade do motor **não** é testada aqui: é o comando `parity`. |

## Problemas comuns

| Mensagem / sintoma                                   | Causa e o que fazer                                                                                          |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `o MT5 não está aberto`                              | Abra o MT5 e faça login; confira `mt5.terminal_path` no `monitor.yaml`.                                      |
| `nenhuma sub-barra ... cobre ...`                    | O terminal não tem histórico até o início da janela. Reduza `is_days` ou espere o cache crescer.             |
| `historico insuficiente`                             | Símbolo sem barras no período; confira o nome do símbolo na sua corretora.                                   |
| `nenhum baseline casa com ...` / `dois baselines para ...` | O ALVO não casa com nenhum nome (veja `baselines`), ou há dois `.xlsx` da mesma combinação EA × ativo × timeframe. |
| `faltando: TELEGRAM_BOT_TOKEN ...`                   | Preencha o `.env` (ver "Telegram"). Se já preencheu e a mensagem persiste, é bug: avise.                     |
| `TELEGRAM_CHAT_ID não parece um id de chat`          | Use o id numérico do chat, não o nome do bot.                                                                |
| Janela julgada com `n=0` ou `[NA]`                   | Ainda não houve trades depois do corte, ou `--until` está antes dele.                                        |

## Limitações conhecidas

- Drawdown só com trades fechados (o relatório de deals não traz equity flutuante).
- Pareamento entrada/saída FIFO por ativo, exato para EA de uma posição por vez.
- O leitor de baseline foi validado em terminal em português, com os dois baselines atuais.
- O motor só implementa IdxSwing91 e BreakoutDonchian1 e depende do MT5 aberto (leitura de barras).
