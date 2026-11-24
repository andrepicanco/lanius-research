@echo off
rem Atalho do dia a dia:  monitor            (todos os baselines)
rem                       monitor DAX40      (so o DAX40)
rem                       monitor --telegram (e envia o resumo ao Telegram)
rem Usa o venv do projeto; nao precisa ativa-lo nem entrar na pasta.
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -m strategy_monitor monitor --telegram %*
