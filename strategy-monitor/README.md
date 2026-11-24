# strategy-monitor

Compara o backtest corrente das estratégias do Lanius com um **baseline** aprovado e avisa
quando o win rate cai abaixo do ponto de equilíbrio. Executado **sob demanda** por comando
(sem agendador). Independente dos outros projetos: não importa código de `risk-mgmt` nem de
`mt5-backtest-py`.

## Estado

| Parte                                                                        | Situação                                                                                                                                                                    |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Leitura de baseline `.xlsx` (Ordens + Negociações)                       | Pronta, conferida contra os números do Tester                                                                                                                                |
| Métricas: win rate, breakeven, IC de Wilson, PF, expectativa em R, drawdown | Pronta                                                                                                                                                                        |
| Comparação com semáforo (OK / ATENCAO / FALHA)                            | Pronta                                                                                                                                                                        |
| Resumo no Telegram                                                           | Código pronto; falta corrigir `TELEGRAM_CHAT_ID` no `.env` (hoje tem o nome do bot, não o id numérico do chat) e rodar `telegram-test`                               |
| Rodar o Strategy Tester por linha de comando (`run`)                       | **Pronto e validado** num terminal portátil dedicado (ver abaixo)                                                                                                      |
| Descoberta de edges (`discover`)                                           | Pronta e com testes automáticos; no Tester real, IS e OOS-triagem rodaram no DAX40; a confirmação no modelo exato **ainda não foi exercitada** numa rodada completa |

**Dia a dia:** `monitor` (ou `monitor DAX40`). Roda o Tester só no período novo, compara e imprime. `compare` continua útil para julgar um relatório que você exportou à mão (`.xlsx` ou `.htm`).

```powershell
cd C:\Users\15339\Documents\andre\lanius\strategy-monitor
.\monitor                     # todos os baselines
.\monitor DAX40               # só o DAX40
.\monitor --telegram          # e manda o resumo ao Telegram
```

`monitor.bat` usa o venv do projeto: não precisa ativá-lo. Ele **abre o MT5 portátil de teste** (feche a janela dele antes).

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy config\monitor.example.yaml config\monitor.yaml   # ajuste baseline_dir
python -m pytest tests -q
```

## Uso

Rode **sempre dentro da pasta `strategy-monitor/`, com o venv ativo**:

```powershell
cd C:\Users\15339\Documents\andre\lanius\strategy-monitor
.\.venv\Scripts\Activate.ps1
python -m strategy_monitor <comando> [opções]
```

### Três conceitos (leia antes dos comandos)

| Termo                    | O que é                                                                                                                                                                                                                                       |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Baseline**       | O backtest que você aprovou para iniciar o algotrading de um ativo: um relatório `.xlsx` do Tester guardado na **pasta dos baselines**. É a régua. Hoje: `NAS100_H1.xlsx` (BreakoutDonchian1) e `DAX40_M12.xlsx` (IdxSwing91). |
| **Run corrente**   | Um relatório novo do **mesmo EA, ativo e timeframe** de um baseline, com os mesmos inputs, cobrindo um período mais recente. Vem do comando `run` ou de um relatório que você exportou do Tester.                                  |
| **Janela julgada** | O trecho do run corrente que o monitor avalia: por padrão, só os trades com **entrada a partir da data final do cabeçalho do baseline**.                                                                                              |

Por que só o que veio depois: o baseline foi aprovado olhando aquele período, então comparar esse
mesmo período consigo mesmo não diz nada. O que importa é se a estratégia se comporta igual **fora**
da amostra que a aprovou.

```
  baseline (a régua)                                   o que o monitor julga
 |-- 2026-07-01 ... 2026-10-05 -- (forward) -- 2026-11-08 |---- 2026-11-08 ........ hoje -->
     trades do arquivo      último trade   período do cabeçalho    └ fim do período do baseline
```

O **fim do período do baseline** é a data final que aparece no cabeçalho do relatório (DAX40:
08/11; NAS100: 07/11). Se o último trade do arquivo fechar depois dela (não deveria), vale o que for
mais tarde. Com "Para frente" ligado (o caso do DAX40, forward em 06/10) o cabeçalho mostra o
período todo, mas o arquivo só traz os trades do backtest; o forward conta como **já visto**, não
como evidência nova. (Decisão de 2026-11-23: antes o corte era o último trade, o que deixava 36 de
41 trades "novos" do DAX40 dentro do forward.)

**Toda saída diz o que foi julgado:** a linha `Baseline:` mostra o período e o último trade dele, e a
linha `Julgando entradas a partir de ...` mostra a data de corte, a regra e quantos trades do
relatório ficaram de fora. Se algum trade julgado for do período do baseline (por `--full` ou
`--since` antigo demais), a saída traz `[aviso]` e a verificação **Sobreposição com o baseline**
(`ATENCAO`): o veredito geral não pode sair `OK` nesse caso.

**Amostra pequena é o normal.** Depois do corte, são poucas semanas de trades. Com menos de
`min_trades` (30) o win rate nunca passa de `ATENCAO`; leia o semáforo como acompanhamento, não como
conclusão, até a amostra crescer.

## Como configurar um baseline

Um baseline é **um arquivo `.xlsx` do Strategy Tester por estratégia × ativo × timeframe**, na pasta
`baseline_dir` do `config/monitor.yaml`. O nome do arquivo não importa: o monitor identifica o
baseline pelo que está dentro (EA + ativo + timeframe). Dois arquivos para a mesma combinação são
erro. Para **adicionar** uma estratégia, basta pôr o arquivo na pasta: não há nada a registrar em
outro lugar, e `monitor` sem alvo passa a incluí-la.

**Passo a passo**

1. No Strategy Tester do seu terminal, rode o backtest aprovado para a estratégia:

   - modelagem **"Cada tick"** e latência **20 ms** (é o que o monitor reproduz; ver abaixo);
   - **depósito** real do teste (o monitor usa o do arquivo) e período que você aprovou;
   - os **inputs exatamente iguais aos da conta** (próximo item).
2. Exporte o relatório do Tester para `.xlsx` (clique com o botão direito no relatório do Tester e
   salve como relatório/Excel; o nome exato do menu depende da versão do MT5, não confirmei). O
   arquivo precisa ter o cabeçalho com os **inputs** e as tabelas **Ordens** e **Negociações**.
3. Salve em `baseline_dir` e confira com o comando offline (não abre o MT5):

   ```powershell
   .\.venv\Scripts\python.exe -m strategy_monitor baselines
   ```

   Confira: EA, ativo, timeframe, período, `n` de trades e as métricas contra o que o Tester mostrou.
4. Garanta que o terminal de teste portátil tem o EA: `<terminal_dir>\MQL5\Experts\<EA>\<EA>.ex5`
   (a pasta tem o nome do EA). Se for diferente, mapeie em `tester.experts` no `monitor.yaml`.

**O que o monitor lê do arquivo, e o que ele assume**

| Do `.xlsx`                         | Uso                                                                                            |
| ------------------------------------ | ---------------------------------------------------------------------------------------------- |
| EA, ativo, timeframe                 | Identificam o baseline e geram o `.ini` do Tester.                                           |
| Período `AAAA.MM.DD - AAAA.MM.DD` | **A data final define o início da janela julgada** (acima).                             |
| Inputs                               | O run roda com **exatamente** estes inputs.                                              |
| Depósito                            | Usado no `.ini` e nas métricas de drawdown.                                                 |
| Ordens (S/L inicial) e Negociações | Trades, win rate, breakeven, PF, drawdown e **R** (o R precisa do S/L inicial da ordem). |

Assumido pelo monitor, **não lido do arquivo** (vem de `config/monitor.yaml` e do código):
modelagem `tester.model` (0 = Cada tick), latência `tester.execution_mode` (20 ms), moeda USD e
alavancagem 1:100. Baseline feito com outra modelagem ou latência não será reproduzido fielmente.
Também só foi validado com terminal em **português**.

**Forward.** Se o seu backtest usa "Para frente", o relatório mostra o período todo no cabeçalho mas
o arquivo só traz os trades da parte de backtest (ver DAX40). Tudo bem: o monitor trata o dia final
do cabeçalho como o fim do baseline. Exporte o relatório só depois de decidir até que data você já
"viu" a estratégia, porque é essa data que passa a ser o corte.

**Regra: o baseline tem de refletir os parâmetros que estão rodando na conta.** O monitor testa os
inputs do **arquivo**, não os do EA em execução, e **não tem como detectar** que você mudou um
parâmetro na conta. O `compare` só avisa quando o relatório corrente difere do baseline, nunca quando
a conta difere dos dois. Por isso:

- Mudou qualquer input de uma EA em operação (inclusive pequenos ajustes de trailing/horário)?
  Refaça o backtest com os inputs novos, exporte um `.xlsx` novo e **substitua** o arquivo antigo
  (um por EA/ativo/timeframe, senão é erro).
- O corte passa a ser o fim do período do **novo** baseline: a contagem de trades novos recomeça do
  zero. Isso é esperado, porque é uma estratégia nova do ponto de vista do monitor.
- Candidatos do `discover` (`.set`) só valem depois de aplicados à conta **e** de virarem baseline.
  Até lá, o monitor continua julgando a versão antiga.
- Ao subir uma mudança para a conta, confira os inputs do `.xlsx` (cabeçalho do arquivo) contra os
  do EA anexado ao gráfico antes de dar o baseline por válido. Esta checagem é manual hoje.

### Resumo dos comandos

| Comando           | Para que serve                                                                                                                 | Abre o MT5?                          |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------ |
| `monitor`       | **Dia a dia.** Roda o Tester no período novo de cada baseline (ou do `ALVO`) e compara. É o `run` com os padrões. | **Sim**                        |
| `baselines`     | Lista os baselines com win rate, breakeven e demais métricas.                                                                 | Não                                 |
| `compare`       | Julga **um relatório que você já tem** contra o baseline dele.                                                        | Não                                 |
| `run`           | Roda o Strategy Tester (numa cópia portátil do MT5) para cada baseline e já compara.                                        | **Sim**                        |
| `discover`      | Busca parâmetros melhores (só os que você liberar) e propõe candidatos. Nada é aplicado.                                  | **Sim** (exceto `--dry-run`) |
| `telegram-test` | Envia uma mensagem de teste ao seu chat do Telegram.                                                                           | Não                                 |

`run` e `discover` só funcionam com o terminal portátil de teste configurado e **fechado** (ver a
seção `run`).

### Opções que valem para qualquer comando

Elas vão **antes** do nome do comando:

```powershell
python -m strategy_monitor --baseline-dir D:\outra\pasta baselines
```

| Opção                  | O que faz                                                                                                                                                                                                            |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--baseline-dir PASTA` | Pasta dos baselines, só nesta execução. Sem ela vale o `baseline_dir` do `config/monitor.yaml`, ou a variável `STRATEGY_MONITOR_BASELINE_DIR` (ordem de precedência: esta opção > variável > arquivo). |
| `--config ARQUIVO`     | Outro `monitor.yaml` (padrão: `config/monitor.yaml`).                                                                                                                                                           |
| `--env-file ARQUIVO`   | Outro `.env` com as credenciais do Telegram (padrão: `strategy-monitor/.env`).                                                                                                                                  |

Cada baseline é identificado pelo que está **dentro** do arquivo (EA + ativo + timeframe), não pelo
nome. Dois arquivos para o mesmo EA/ativo/timeframe na pasta é erro.

### `baselines`

```powershell
python -m strategy_monitor baselines
```

```
IdxSwing91 · DAX40 M12  [DAX40_M12.sxxlsx]
  período 2026-07-01 → 2026-11-08 | histórico 100% | ticks 15,400,993
  n=93 win 40.9% | BE 34.5% | folga +6.3 pp | IC 31.4–51.0%
  líquido +402.92 | PF 1.31 | E[R] +0.125 R | DD 2.51%
  [aviso] cabeçalho vai até 2026-11-08, mas o último trade fecha em 2026-10-05 (33 dias antes): ...
  o monitor julga só entradas a partir de 2026-11-08 (fim do período do baseline)
```

`n` = trades; `win` = win rate; `BE` = win rate de breakeven; `folga` = win − BE; `IC` = intervalo de
confiança de 95% do win rate; `PF` = profit factor; `E[R]` = expectância em R por trade; `DD` =
drawdown máximo. O `[aviso]` aparece quando o cabeçalho e o último trade não coincidem.

### `compare`: julgar um relatório que você já tem

```powershell
python -m strategy_monitor compare --current <ARQUIVO> [--since AAAA-MM-DD | --full] [--telegram]
```

| Opção                | O que faz                                                                                                                                                                                                                                            |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--current ARQUIVO`  | **Obrigatória.** O relatório que você quer julgar: **um arquivo** `.xlsx` ou `.htm` do Tester (não uma pasta). Precisa ser do mesmo EA, ativo e timeframe de algum baseline; se não for, o erro lista os baselines que existem. |
| `--since AAAA-MM-DD` | Julga só os trades com **entrada a partir desta data** (inclusive). Padrão: fim do período do baseline (data final do cabeçalho). Antes dessa data, a saída avisa sobreposição.                                                         |
| `--full`             | **Diagnóstico.** Julga **todos** os trades do relatório, inclusive os do período do baseline: a saída traz aviso de sobreposição e o veredito não sai `OK`. Não serve como evidência.                                         |
| `--telegram`         | Além de imprimir, envia o resumo ao Telegram.                                                                                                                                                                                                       |

`--since` e `--full` são alternativas e **não podem ser usadas juntas**. Sem nenhuma das duas, vale o
padrão (só o que veio depois do baseline).

**De onde vem o arquivo do `--current`:**

- `outputs\sm_<EA>_<ATIVO>_<TF>_<data>_<hora>.htm`: gerado pelo comando `run` (os 10 mais recentes de
  cada baseline ficam guardados);
- ou um relatório que você exporta do Tester, do mesmo jeito que fez com os baselines (`.xlsx`).

**Exemplo real.** O relatório abaixo (run de 17/11) cobre 05/09 a 17/11; o baseline do DAX40 tem
cabeçalho até 08/11 e último trade em 05/10. Os três modos julgam trechos diferentes do **mesmo
arquivo**:

```powershell
$F = "outputs\sm_IdxSwing91_DAX40_M12_20261117_113547.htm"

python -m strategy_monitor compare --current $F                      # padrão: só depois do baseline
python -m strategy_monitor compare --current $F --since 2026-10-20   # a partir de 20/10 (avisa sobreposição)
python -m strategy_monitor compare --current $F --full               # o arquivo inteiro (diagnóstico)
```

| Modo                   | Linha "Janela corrente"                               | Sobreposição com o baseline            |
| ---------------------- | ----------------------------------------------------- | ---------------------------------------- |
| padrão                | `2026-11-10 → 2026-11-13 (n=5, líquido +58.47)`   | nenhuma                                  |
| `--since 2026-10-20` | `2026-10-20 → 2026-11-13 (n=25, líquido +68.65)`  | aviso: 20 dos 25 trades são do baseline |
| `--full`             | `2026-09-07 → 2026-11-13 (n=70, líquido +252.68)` | aviso e `ATENCAO`: 65 dos 70           |

`Janela corrente` mostra da primeira entrada ao último fechamento **dos trades julgados**, e `n` é
quantos são. Use `--full` só para diagnóstico (por exemplo, conferir que um relatório reproduz o
próprio baseline); para julgar a estratégia use o padrão ou um `--since` a partir do fim do baseline.

Se o corte deixar a janela sem nenhum trade, o resultado sai `[NA]`, não `[OK]`:

```
IdxSwing91 · DAX40 M12  [NA]
Janela corrente: sem trades (n=0, líquido +0.00)
  [NA] Periodo julgado: nenhum trade com entrada na janela julgada (veja --since/--full e o periodo do relatorio)
```

Se os inputs do relatório diferirem dos do baseline, aparece um `[aviso] inputs diferentes do baseline: ...`: o run deixou de ser uma comparação direta.

### `run`: rodar o Strategy Tester e comparar

```powershell
python -m strategy_monitor run [--only TEXTO] [--until AAAA-MM-DD] [--since AAAA-MM-DD | --full] [--telegram]
```

Para cada baseline: gera o `.ini` do Tester a partir dele (EA, ativo, timeframe, inputs, depósito),
abre o MT5 portátil, roda o teste, lê o relatório, compara e imprime o resultado.

| Opção                | O que faz                                                                                                                                                                                                                                      |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--only TEXTO`       | Só os baselines cujo nome contenha o texto (sem diferenciar maiúsculas). O nome tem a forma `IdxSwing91 · DAX40 M12`, então `--only dax40`, `--only breakout` e `--only M12` funcionam. Sem a opção, roda todos, um de cada vez. |
| `--until AAAA-MM-DD` | Data final do teste, **exclusiva** (padrão: hoje, ou seja, até ontem). Exclusiva significa que o dia indicado **não** entra: `--until 2026-10-06` testa até 05/10.                                                           |
| `--since AAAA-MM-DD` | Começa a julgar nesta data. O teste começa `warmup_days` (30) dias **antes**, para aquecer os indicadores; só os trades a partir de `--since` contam. Padrão: fim do período do baseline (data final do cabeçalho).            |
| `--full`             | Roda e julga a **janela inteira do baseline**, desde o início dele. Lento, e pode estourar disco e memória (o NAS100 H1 de 10 meses não completa). Serve para conferir se o monitor reproduz o baseline.                              |
| `--telegram`         | Envia **uma mensagem** com o resumo de todos os baselines rodados.                                                                                                                                                                       |

`--since` e `--full` também são alternativas. Se `--until` cair antes do início do teste, o comando
avisa `nada a testar` e segue.

```powershell
python -m strategy_monitor run                                   # todos os baselines, até ontem
python -m strategy_monitor run --only DAX40                      # só o DAX40
python -m strategy_monitor run --only DAX40 --until 2026-11-10   # só o DAX40, até 09/11
python -m strategy_monitor run --only DAX40 --full --until 2026-10-06   # confere paridade (reproduz o baseline)
python -m strategy_monitor run --telegram                        # e manda o resumo ao Telegram
```

**Antes de rodar:** o terminal portátil de teste (`tester.terminal_dir` no `monitor.yaml`) precisa
existir, estar logado numa conta demo e estar **fechado**; com a janela dele aberta o MT5 ignora o
`.ini`. Veja as seções abaixo para o porquê, o espaço em disco e o LiveUpdate.

### `discover`: busca de parâmetros

```powershell
python -m strategy_monitor discover [--dry-run] [--only TEXTO] [--trials N] [--until AAAA-MM-DD] [--telegram]
```

| Opção                        | O que faz                                                                                                                                            |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--dry-run`                  | **Mostra o plano e o tempo estimado, sem abrir o MT5.** Comece sempre por aqui.                                                                |
| `--only TEXTO`               | Só os alvos cujo `EA ativo` contenha o texto (`--only DAX40`, `--only Breakout`).                                                             |
| `--trials N`                 | Quantas combinações sortear por alvo (sobrepõe `settings.trials` do `discovery.yaml`).                                                        |
| `--until AAAA-MM-DD`         | Fim **exclusivo** da janela de teste (padrão: hoje).                                                                                          |
| `--telegram`                 | Envia o resumo ao Telegram.                                                                                                                          |
| `--discovery-config ARQUIVO` | Outro arquivo de configuração (padrão: `config/discovery.yaml`). Esta, ao contrário das opções gerais, vai **depois** de `discover`. |

```
> python -m strategy_monitor discover --dry-run
Janelas: IS 2026-02-11 -> 2026-10-09 | OOS 2026-10-09 -> 2026-11-23. Triagem modelo 1, confirmacao modelo 0. Teto de DD 5%.
  IdxSwing91 · DAX40 M12: espaco 1320 combinacoes, 30 tentativas, 39 testes
  BreakoutDonchian1 · NAS100 H1: espaco 3465 combinacoes, 30 tentativas, 39 testes
Total: 78 testes (~32 min).
```

Quais parâmetros podem variar, o teto de drawdown e o método estão na seção `discover` mais abaixo.

### `telegram-test`

```powershell
python -m strategy_monitor telegram-test
```

Envia "teste de conexão OK" ao chat configurado. Veja a seção Telegram para as credenciais.

### Como ler o resultado de `compare` e `run`

```
IdxSwing91 · DAX40 M12  [OK]                                         <- veredito geral = o pior das verificações
Baseline: 2026-07-01 → 2026-11-08 (último trade: 2026-10-05)         <- a régua
Julgando entradas a partir de 2026-11-08 (fim do período do baseline): 5 trades; 65 de 70 do relatório ficaram de fora.
Janela corrente: 2026-11-10 → 2026-11-13 (n=5, líquido +58.47)       <- o que foi julgado
  [OK] Win rate vs breakeven: win 40.0% | BE 22.4% | folga +17.6 pp | IC95 11.8%–76.9% | baseline: win 40.9%, BE 34.5% [amostra pequena: n=5 < 30]
  [OK] Expectativa (R): +0.301 R/trade (n=5) | baseline +0.125 R
  [OK] Profit factor: 2.30 | baseline 1.31
  [OK] Drawdown (saldo fechado): 0.26% | baseline 2.51%
```

(Com n=5 esse `OK` é só ausência de alarme: o intervalo de confiança vai de 12% a 77%.)

| Estado      | Significa                                                                                                                                                                               |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `OK`      | Dentro do esperado.                                                                                                                                                                     |
| `ATENCAO` | Fora do esperado, mas ainda compatível com sorte (por exemplo, win rate abaixo do breakeven com intervalo de confiança que ainda o contém), ou amostra pequena demais para concluir. |
| `FALHA`   | Fora do esperado de forma estatisticamente clara (por exemplo, o teto do intervalo do win rate abaixo do breakeven).                                                                    |
| `NA`      | Não há dado para julgar (sem trades na janela, ou sem ganho e perda para calcular).                                                                                                   |

A principal verificação é a primeira: **win rate vs breakeven**, explicada na seção "Racional do win
rate de breakeven". Com menos de `min_trades` (30) trades ela nunca passa de `ATENCAO`.

### Receitas

| Quero...                                         | Comando                                                                                          |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| Ver como as estratégias estão desde o baseline | `.\monitor`                                                                                    |
| Rodar só uma estratégia                        | `.\monitor DAX40`                                                                              |
| Rodar só uma até uma data (exclusiva)          | `.\monitor DAX40 --until 2026-11-20`                                                           |
| Julgar um relatório que exportei do Tester      | `compare --current C:\caminho\relatorio.xlsx`                                                  |
| Olhar só as últimas semanas de um relatório   | `compare --current <arquivo> --since 2026-11-15`                                               |
| Olhar o relatório inteiro (só diagnóstico)    | `compare --current <arquivo> --full`                                                           |
| Receber o resultado no celular                   | acrescente `--telegram` a `monitor`, `compare` ou `run`                                  |
| Adicionar uma estratégia ao monitor             | exporte o `.xlsx` do Tester para a pasta dos baselines (seção "Como configurar um baseline") |
| Ver o que a descoberta faria, sem gastar tempo   | `discover --dry-run`                                                                           |
| Conferir se o monitor reproduz um baseline       | `run --only DAX40 --full --until 2026-10-06`                                                   |

### Códigos de saída

| Código | Quando                                                                                                               |
| ------- | -------------------------------------------------------------------------------------------------------------------- |
| `0`   | O comando terminou, **mesmo que o veredito seja `ATENCAO` ou `FALHA`**: o semáforo não altera o código. |
| `1`   | Erro de execução ou de configuração (baseline não encontrado, Telegram sem credencial, teste que falhou).       |
| `2`   | Opção inválida (data fora do formato, `--since` com `--full`, opção que não existe).                       |

Se for encadear em script, leia o texto da saída, não o código.

### Telegram

Mesmo bot e chat do `risk-mgmt`. As credenciais **nunca** vão em YAML versionado nem em flag. Ficam no
arquivo `strategy-monitor/.env` (ignorado pelo git; modelo em `.env.example`):

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

- Formato `CHAVE=valor`, sem aspas (aspas, `export`, comentários `#`, BOM e quebra de linha do Windows
  são tolerados).
- **Variável já definida no ambiente do Windows vence o arquivo** (útil se você preferir
  `setx TELEGRAM_BOT_TOKEN "..."`, que não precisa de admin).
- `--env-file <caminho>` aponta outro arquivo. Sem o arquivo padrão, a CLI segue só com o ambiente.
- Erro de credencial lista só os **nomes** que faltam, nunca valores. Se a rede falhar, o token é
  mascarado (`<TOKEN>`) na mensagem, porque ele faz parte da URL da API.

Para validar token e chat (envia **uma mensagem de teste** ao seu chat):

```powershell
python -m strategy_monitor telegram-test
```

## Racional do win rate de breakeven

**Definição** (calculada dos trades do run, em resultado líquido de swap e comissão):

```
BE = perda_média / (ganho_médio + perda_média)
```

**Derivação.** Com win rate `p`, ganho médio `W` e perda média `L` (ambos positivos), a
expectativa por trade é `p·W − (1−p)·L`. Ela zera em `p = L / (W + L)`. Acima disso a estratégia
ganha dinheiro em média; abaixo, perde.

**Por que pelo payoff realizado e não por `1/(1+RR)`.** A fórmula clássica `1/(1+RR)` supõe
alvo e stop fixos. Os EAs do Lanius usam trailing ATR, saída por tempo e stop que se move, então
o ganho e a perda *realizados* diferem dos configurados. Exemplos dos baselines atuais
(win rate / BE):

| Baseline                       | Win rate | BE    | Folga   |
| ------------------------------ | -------- | ----- | ------- |
| BreakoutDonchian1 · NAS100 H1 | 41,6%    | 37,0% | +4,6 pp |
| IdxSwing91 · DAX40 M12        | 40,9%    | 34,5% | +6,3 pp |

**Consequências a ter em mente:**

- "Win rate > BE" é o mesmo que "expectativa > 0 em dinheiro" (e que PF > 1). A folga só põe a
  informação na escala do win rate.
- O BE **não é fixo**: muda a cada run, junto com o payoff. Um win rate estável com BE subindo é
  sinal de que o trailing está cortando ganhos ou deixando perdas maiores.
- Trade de resultado exatamente zero conta no total mas não é ganho nem perda.

**Veredito (win rate × BE).** Como n é pequeno (dezenas de trades), compara-se o BE com o
**intervalo de Wilson** do win rate (95% por padrão), não só com o ponto:

| Condição                                                               | Estado  |
| ------------------------------------------------------------------------ | ------- |
| win rate ≥ BE                                                           | OK      |
| win rate < BE, mas o limite superior do IC ≥ BE (indistinguível do BE) | ATENCAO |
| limite superior do IC < BE (significativamente abaixo)                   | FALHA   |

Com menos de `min_trades` trades (padrão 30) o veredito de win rate **nunca passa de ATENCAO**.

> Nos dois baselines atuais o limite inferior do IC95 do win rate fica **abaixo** do BE
> (NAS100: 34,3% < 37,0%; DAX40: 31,4% < 34,5%). Ou seja, mesmo no baseline o excesso sobre o
> breakeven não é estatisticamente distinguível de zero com esse n. Isso não invalida o baseline,
> mas é o contexto certo para ler os semáforos.

## Outras verificações

- **Expectativa em R**: líquido ÷ risco inicial em dinheiro, onde risco = |entrada − SL inicial| ×
  volume × k. O SL inicial vem da ordem de entrada (tabela Ordens). `k` (dinheiro por ponto por
  lote) é estimado dos próprios trades (mediana de `lucro ÷ (Δpreço × volume)`), sem depender de
  `tick_value` do símbolo; nos baselines a dispersão de `k` é de 0,05% (NAS100) e 2,5% (DAX40,
  conversão EUR→USD).
- **Profit factor**, **drawdown** (sobre o saldo de trades fechados; bate com o "Rebaixamento
  máximo do saldo" do Tester) e **trades/mês** (mudança de comportamento).
- Drawdown: FALHA se passar do teto `max_drawdown_pct` (quando configurado) ou de
  `dd_fail_factor` × o do baseline. A janela corrente costuma ser mais curta que a do baseline,
  então a comparação de drawdown favorece o corrente — leia como piso, não como prova.

## Limitações conhecidas

- Drawdown só com trades fechados (o relatório de deals não traz equity flutuante).
- Pareamento entrada/saída FIFO por ativo, exato para EA de uma posição por vez; fechamento
  parcial gera aviso.
- O relatório `.xlsx` não informa o modelo de ticks usado; só "qualidade do histórico" e nº de
  ticks. Ver pendência no plano.
- O leitor foi validado em terminal em português com os dois baselines atuais.

## `run`: Tester por linha de comando

Executa `<terminal_dir>\terminal64.exe /portable /config:<ini>` num **terminal portátil dedicado**
(`tester.terminal_dir`), gerando o `.ini` a partir do baseline (EA, ativo, timeframe, inputs,
depósito; modelagem "Cada tick" e latência 20 ms, como nos baselines). O terminal fecha sozinho
ao terminar (`ShutdownTerminal=1`); o relatório `.htm` vai para `outputs/`.

**Por que um terminal separado (testado em 2026-11-17):**

- `/config` com o terminal ao vivo já aberto é **ignorado** (o processo novo sai em segundos, sem
  teste e sem relatório). O runner recusa rodar se o terminal de teste estiver aberto.
- Com `ShutdownTerminal=1` o terminal que roda o teste se fecha ao fim.
- A cópia portátil leva só o programa e os `.ex5` (sem gráficos nem EAs anexados), então nunca
  opera. Precisa de **um login manual** numa conta demo na primeira vez
  (Arquivo → Login na conta de negociação). Nunca aponte `terminal_dir` para o terminal que opera.

**Janela do run.** Por padrão roda só do **fim do período do baseline − `warmup_days`** (30) até
`--until` (exclusivo; padrão hoje), e julga só os trades com entrada a partir do fim do período do baseline.
`--full` roda a janela inteira do baseline; use só para conferir paridade.

- A data final (`ToDate`) é **exclusiva**: com `ToDate=2026.10.05` o Tester não gerou os trades
  de 05/10.
- Janelas longas custam disco e memória. NAS100 H1 de 10 meses = 194 M de ticks; o agente local
  gerou ~23 M e **o Tester encerrou o teste sozinho em 01/04** (log: `Test passed`, "2112 Mb of
  cached tick data"), devolvendo relatório sem trades. A causa provável é o teto de cache de ticks
  do agente (~2 GB); não foi verificada. Por isso o padrão é a janela curta.
- O runner **recusa** relatório com 0 barras ("rodou sem dados"). Ele não detecta teste truncado
  com dados parciais (como o de abril acima); se o `Janela corrente` terminar muito antes de
  `--until`, desconfie.

**Paridade validada.** IdxSwing91 · DAX40 M12, janela do baseline (`--full --until 2026-10-06`):
93 trades, +402,92, PF 1,31, drawdown 2,51%: **idêntico ao baseline**, duas vezes, com histórico
recém-baixado. O NAS100 de 10 meses não pôde ser conferido (limite acima).

**LiveUpdate.** O primeiro disparo após um tempo parado pode acionar o LiveUpdate do MT5, que fecha
o terminal sem rodar o teste e o reabre sem o `/config`. O runner tenta de novo uma vez, depois de
encerrar essa instância reaberta (só a do `terminal_dir`).

## Disco (restrição do André: pouco espaço no C:)

Medido no terminal portátil: programa + `.ex5` ≈ 180 MB; histórico M1 baixado ≈ 55–120 MB por
símbolo (cresce a cada símbolo novo); `Tester/` e logs ≈ 10 MB (regerados a cada run);
relatório `.htm` ≈ 0,2 MB. Política (`tester.*` no `monitor.yaml`):

| Chave               | Padrão | O que faz                                                                                                      |
| ------------------- | ------- | -------------------------------------------------------------------------------------------------------------- |
| `min_free_gb`     | 1.0     | Recusa rodar com menos que isso livre no disco                                                                 |
| `max_terminal_mb` | 500     | Depois do run, se o terminal passar disso, apaga o histórico baixado (volta a baixar no run seguinte, ~1 min) |
| `keep_reports`    | 10      | Relatórios mantidos por baseline em `outputs/`                                                              |
| `warmup_days`     | 30      | Janela curta do run (acima)                                                                                    |

Depois de cada run bem-sucedido, o runner apaga os PNG do relatório, `Tester/{cache,logs,bases}`
e os logs do terminal. Em **falha** os logs ficam, para diagnóstico. A limpeza só mexe dentro de
`terminal_dir` e `outputs/`; nunca em `Config/` (login) nem em `MQL5/` (EAs).

## `discover`: busca de parâmetros (item 2)

```powershell
python -m strategy_monitor discover --dry-run            # plano e tempo estimado, sem rodar nada
python -m strategy_monitor discover --only DAX40         # roda; ~25 s por teste
python -m strategy_monitor discover --telegram           # e envia o resumo
```

**Nada é aplicado.** A saída fica em `outputs/discovery/<data>/`: `report.md` (para ler),
`results.csv` (todas as tentativas, gravado linha a linha) e, **só para quem foi aprovado**, um
`.set` em `candidates/`. Quem decide é você.

**Só varia o que você liberar.** `config/discovery.yaml` (copie de `discovery.example.yaml`)
lista, por estratégia, os parâmetros liberados e os valores possíveis (`min/max/step`, `values` ou
`factors`, que são multiplicadores do valor do baseline e servem para parâmetros em pontos, cuja
escala muda de ativo para ativo). Recusados de propósito: `InpMagicNumber`, `InpTradeComment`,
`InpLogLevel` e o dimensionamento (`InpRiskPercent`, `InpFixedLot`, `InpUseFixedLot`), porque o
objetivo é E[R] e tamanho de posição não cria edge. Parâmetro liberado que não existe nos inputs
do baseline é erro. `max_drawdown_pct` é obrigatório.

**Método (por estratégia × ativo):**

1. **Janelas.** OOS = últimos `oos_days` (45) até `--until`; IS = `is_days` (240) imediatamente
   antes. A busca **nunca vê o OOS**.
2. **Triagem no IS** com o modelo rápido "1 min OHLC": a referência (inputs do baseline,
   inalterados) mais `trials` combinações sorteadas, sem repetição, com semente fixa
   (reprodutível).
3. **Objetivo:** maximizar **E[R]** (expectância em R), com restrições: n ≥ `min_trades_is` e
   drawdown ≤ `max_drawdown_pct`. Tentativa fora das restrições não entra no ranking.
4. Os `top_k` (3) melhores vão ao OOS junto com a referência, no modelo rápido e depois no
   **modelo exato** dos baselines ("Cada tick").
5. **CANDIDATO** só se, no OOS (exato quando existe): n ≥ `min_trades_oos`, drawdown ≤ teto,
   E[R] > 0 **e** E[R] maior que o da referência no mesmo OOS. Se a confirmação exata falhar, não
   há candidato.

**Por que o modelo rápido.** O "Cada tick" leva minutos e o NAS100 H1 nem cabe numa janela longa.
Medido contra os baselines (janela inteira): DAX40 M12 — 93 de 93 trades, E[R] +0,127 contra
+0,125; NAS100 H1 — 161 de 161 trades, E[R] +0,106 contra +0,091, drawdown 2,98% contra 3,12%. O
modelo rápido é **otimista** (~+0,015 R no NAS100), então serve para ordenar as tentativas, não
para medir o nível. Por isso os finalistas são reconfirmados no modelo exato.

**O viés de seleção é o risco principal.** Com N tentativas, o melhor do IS é inflado mesmo sem
edge algum (o máximo esperado de N ruídos normais é cerca de √(2 ln N) desvios acima da média).
O relatório mostra quantos desvios o melhor do IS está acima da média das tentativas e compara
com esse piso. **O número que vale é o do OOS**, e ele também sofre seleção (`top_k` finalistas).
Os testes automáticos incluem o caso "edge só no treino", que tem de sair REPROVADO.

**Limites (valem para qualquer candidato):**

- Um único OOS, ou seja, um só regime; sem teste de vizinhança (o candidato pode ser um pico
  isolado).
- OOS curto (45 d) dá poucos trades e intervalo de confiança largo; o teto de ~45–60 d vem do
  limite de ticks do modelo exato no NAS100 H1.
- Ativo sem baseline próprio usa o baseline de outro ativo (`template`) como referência, que
  **não foi validada** nesse ativo (o stop em pontos muda de escala). O `--dry-run` avisa.
- Antes de levar um candidato à conta, rode-o em forward (demo) por mais tempo.

**Tempo e disco.** ~39 testes por alvo com 30 tentativas (~16 min). Cada relatório é lido e
apagado; a limpeza e o teto de disco do runner valem aqui também. Ativo novo baixa o histórico do
símbolo (dezenas de MB).
