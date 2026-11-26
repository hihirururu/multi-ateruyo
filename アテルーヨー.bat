@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 本物のOpusつきで遊ぶ。黒い窓（最小化）を閉じると終了
start "アテルーヨー（この窓を閉じると終了）" /min python server.py
