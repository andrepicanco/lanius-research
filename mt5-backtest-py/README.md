# mt5-backtest-py

Estudos de mercado via MetaTrader 5 + Python, para o programa Lanius.

## O que tem aqui

| Caminho | O que é |
|---|---|
| `mt5_data.py` | Conexão com o terminal e download de rates. |
| `meanrev/` | Pacote de pesquisa da estratégia mean reversion: sinais, motor de backtest, custos, métricas e validação. |
| `tests/` | Testes do pacote (`pytest`). |
| `notebooks/01_forex_mean_rev.ipynb` | Notebook exploratório original. **Mantido como referência, não alterar.** |
| `notebooks/02_mean_rev_research.ipynb` | Pesquisa com custos, saídas alternativas e validação out-of-sample. |
| `notebooks/import/` | Cache de rates em CSV, um arquivo por `<símbolo>_<timeframe>`. |

## Pré-requisito: terminal MetaTrader 5

A API Python oficial (`MetaTrader5`) só funciona com o terminal instalado e logado na
mesma máquina — ela se conecta via IPC local, não existe modo somente-nuvem.

1. Instalar o terminal (site da corretora ou metatrader5.com).
   - O instalador padrão costuma pedir elevação UAC. Sem admin, procure se a corretora
     oferece uma **versão portátil**.
2. Abrir uma conta demo (ou usar a real) e fazer login.
3. Deixar o terminal aberto (pode estar minimizado) enquanto roda o notebook —
   `mt5.initialize()` se conecta à instância já logada, sem credenciais no código.

Com os CSVs de `notebooks/import/` já baixados, o notebook 02 roda **sem o terminal**:
passe `allow_download=False` para `data.load_rates`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Use o venv **deste** projeto. O `risk-mgmt/` tem o seu próprio; misturar os dois cria a
dependência entre projetos que o `CLAUDE.md` da raiz proíbe.

## Testes

```powershell
python -m pytest tests/ -q
```

O mais importante é `tests/test_parity_nb01.py`: ele transcreve literalmente as células
do notebook 01 e exige que o motor chegue ao mesmo número. Sem isso, não há como saber
se uma refatoração mudou o resultado por engano.

## Pacote `meanrev/`

| Módulo | Responsabilidade |
|---|---|
| `data.py` | Carga e cache de rates, com verificação de cobertura e dos limites de histórico da API. |
| `indicators.py` | SMA, RSI de Wilder, ATR, contagem de streak, meia-vida OU, Hurst, variance ratio. |
| `signals.py` | Geração dos eventos de entrada, com a mesma semântica do `MeanRev1.mq5`. |
| `costs.py` | Spread (da própria barra), slippage, comissão e swap. |
| `exits.py` | Regras de saída combináveis, marcando quais o EA ainda não sabe executar. |
| `engine.py` | Backtest orientado a eventos: uma posição por vez, entrada na barra seguinte, SL/TP contra o range. |
| `metrics.py` | Conversão para dinheiro, drawdown, t-stat, MAE/MFE. |
| `validation.py` | Superfície de parâmetros, walk-forward, teste de permutação, orçamento de busca. |
| `export.py` | Ledger em CSV no formato que o `risk-mgmt` lê. |

### Uso mínimo

```python
import datetime as dt
from meanrev import data, export, metrics
from meanrev.costs import CostModel
from meanrev.engine import run_backtest
from meanrev.exits import EA_DEFAULT
from meanrev.signals import SignalParams

df = data.load_rates("AUDCHF", "M30", dt.datetime(2022, 1, 1), dt.datetime(2026, 9, 18))
result = run_backtest(df, "AUDCHF", SignalParams(), EA_DEFAULT, CostModel())
print(result.summary())
```

## Diferenças em relação ao notebook 01

O notebook 01 mede retorno bruto de preço close-a-close. O pacote corrige cinco coisas
que, juntas, mudam o sinal do resultado:

1. **Entrada na barra seguinte** ao sinal, que é quando o EA consegue operar — o 01
   assume execução no fechamento do candle que gerou o sinal.
2. **Uma posição por vez**, como o EA. As janelas `next_N` do 01 se sobrepõem.
3. **Spread cobrado** a partir da coluna `spread` dos próprios rates.
4. **SL/TP simulados** contra o range da barra. Quando uma barra contém os dois, o motor
   assume stop primeiro e **conta quantas vezes** precisou assumir isso.
5. **Treino e teste separados**, com o número de combinações avaliadas registrado.

Em AUDCHF M30 com os parâmetros que o 01 registrou (`streak=1`, `exit=7`), a cascata vai
de **+67,05%** pelo método do 01 para **−17,99%** com execução realista e spread.

O teste de permutação (200 séries embaralhadas, mesmo calendário e mesmo spread) separa
as duas leituras possíveis desse número:

| | real | nula (média ± desvio) | z | p |
|---|---|---|---|---|
| bruto | **+32,4%** | +1,2% ± 15,2 | +2,04 | 0,030 |
| líquido | −18,0% | −29,2% ± 15,4 | +0,73 | 0,244 |

A estrutura de reversão **parece** existir — o bruto fica fora da nula — mas ela é menor
que o custo de operá-la, e o líquido é indistinguível de ruído.

Cuidado com o `p = 0,030` do bruto: **ele não está corrigido para seleção**. Os
parâmetros testados (`streak=1, exit=7`) saíram de uma busca in-sample, e o notebook
avalia 216 combinações. Um p de 0,03 no combo escolhido dessa forma é sugestivo, não
conclusivo. O que ele justifica é continuar investigando o lado do custo (filtro de
horário, timeframe maior, alvo mais largo que amortize o spread) — não declarar edge.

## Integração com o `risk-mgmt`

`export.write_trades_csv()` grava `<símbolo>_trades.csv` no formato que o
`risk_mgmt/logsource/idxswing91_csv.py` já lê. Nenhum dos dois projetos importa o outro:
o contrato é o arquivo.

```powershell
# no risk-mgmt, com o venv DELE
python scripts/run_risk_report.py --mode local --strategy MeanRev1=reports/meanrev --dry-run
```

A conversão para dinheiro precisa do `value_per_point` do símbolo
(`tick_value / tick_size`), que vem do MT5 ou do `risk-mgmt/config/symbols.yaml`. Um
valor zero é rejeitado de propósito: o MT5 devolve zero quando não consegue converter
para a moeda da conta, e multiplicar esse zero zera todo o P/L sem gerar erro nenhum.

## Limitações conhecidas

- **Ordem intrabar** não é resolvida. Barras que contêm stop e alvo assumem stop. O
  notebook reporta a fração afetada.
- **Comissão e swap** são parâmetros, não medições. Enquanto não forem preenchidos com
  os valores reais da corretora, todo número líquido é otimista.
- **Margem não é modelada** — isso é trabalho do `risk-mgmt`.
