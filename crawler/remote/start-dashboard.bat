@echo off
REM NLWeb Dashboard Server - 開機自動啟動
REM 放入 shell:startup 資料夾或用 Task Scheduler 觸發
cd /d C:\Users\User\nlweb\code\python
python -m indexing.dashboard_server
