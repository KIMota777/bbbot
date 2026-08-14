@echo off
rem NOTE: chcp goes FIRST and everything above it is plain ASCII on purpose:
rem cmd re-reads the file after a codepage switch and garbles russian text
rem that was parsed under the previous codepage.
chcp 65001 >nul
title Сайт ботов Bybit - не закрывать

rem Запуск сайта ботов: http://127.0.0.1:8000
rem Окно можно свернуть, но не закрывать (закроешь — сайт остановится).
rem Папка проекта берётся от самого файла (%~dp0): раньше тут был жёсткий
rem d:\prog\bybit_bot — такой папки нет, и запуск падал у всех, кроме автора.
cd /d "%~dp0"

rem Интерпретатор ищем сами, а не берём голый "python": в PATH у владельца
rem первым стоит системный Python 3.9 БЕЗ flask, и ярлык падал с
rem ModuleNotFoundError, так и не подняв сайт. Порядок поиска: заданный вручную
rem BBBOT_PYTHON, окружение рядом с этим файлом, лаунчер py -3, python из PATH.
rem Каждый кандидат проверяется импортом flask: "интерпретатор нашёлся" само по
rem себе ничего не значит, нужен тот, в котором стоят зависимости сайта.
set "PY="
for %%P in ("%BBBOT_PYTHON%" "%~dp0venv\Scripts\python.exe" "%~dp0.venv\Scripts\python.exe" "%~dp0env\Scripts\python.exe") do (
    if not defined PY if exist "%%~P" (
        "%%~P" -c "import flask" >nul 2>&1 && set "PY=%%~P"
    )
)
if not defined PY (
    py -3 -c "import flask" >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    python -c "import flask" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo Не нашёл Python, в котором установлен flask - сайт запускать нечем.
    echo Проверено: BBBOT_PYTHON, venv/.venv/env рядом с этим файлом,
    echo лаунчер py -3, python из PATH.
    echo.
    echo Что сделать один раз в папке проекта:
    echo     py -3 -m venv venv
    echo     venv\Scripts\python.exe -m pip install -r requirements.txt
    echo и запустить этот файл снова.
    echo Если окружение лежит в другом месте:
    echo     set BBBOT_PYTHON=D:\путь\к\venv\Scripts\python.exe
    echo.
    pause
    exit /b 1
)

rem порт по умолчанию 8000, но app.py слушает SITE_PORT — печатаем то, что
rem действительно откроется, а не заученное число
if not defined SITE_PORT set "SITE_PORT=8000"
echo Python: %PY%
echo Сайт откроется по адресу http://127.0.0.1:%SITE_PORT%
%PY% webapp\app.py
rem Окно не должно закрыться молча, если сайт упал.
if errorlevel 1 echo.& echo Сайт завершился с ошибкой. Зависимости ставятся так: %PY% -m pip install -r requirements.txt
pause
