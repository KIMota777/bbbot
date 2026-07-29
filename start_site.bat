@echo off
rem Запуск сайта ботов: http://127.0.0.1:8000
rem Окно можно свернуть, но не закрывать (закроешь — сайт остановится).
cd /d d:\prog\bybit_bot
title Сайт ботов Bybit - не закрывать
python webapp\app.py
pause
