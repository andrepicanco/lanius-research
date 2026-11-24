@echo off
rem Abre a tela do strategy-monitor no navegador (local, sem admin). Feche esta janela para encerrar.
rem O MT5 que voce usa precisa estar aberto e logado para rodar monitor / paridade / descoberta.
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run strategy_monitor\ui\app.py
