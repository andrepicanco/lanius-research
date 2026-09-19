# mt5-backtest-py

Estudos de dados de mercado via MetaTrader 5 + Python.

## Pré-requisito: terminal MetaTrader 5

A API Python oficial (`MetaTrader5`) só funciona com o terminal MT5 instalado e logado na mesma máquina — ela se conecta via IPC local, não existe modo somente-nuvem.

1. Instalar o terminal (site da corretora ou metatrader5.com).
   - O instalador padrão costuma pedir elevação UAC. Se você não tem admin nesta máquina e o instalador pedir UAC, procure se a corretora oferece uma **versão portátil** (pasta autocontida, sem instalação). Caso contrário, será necessário pedir para alguém com admin instalar uma vez.
2. Abrir uma conta demo (ou usar a real) e fazer login no terminal.
3. Deixar o terminal aberto (pode estar minimizado) enquanto rodar o notebook — `mt5.initialize()` se conecta à instância já logada, sem precisar de credenciais no código.

## Setup do ambiente Python

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Rodando o notebook

```
jupyter notebook
```

Abrir `notebooks/01_forex_explore.ipynb`.
