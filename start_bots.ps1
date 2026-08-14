# Запуск всех ботов, каждый в своём окне PowerShell.
# Обычный режим x5 — все 5 монет; турбо x20 — только ETH и BTC (лучшие в бэктесте).
# Закрыть бота = закрыть его окно (позиции на бирже при этом остаются!).
#
# Файл сохранён в utf-8 С BOM: Windows PowerShell 5.1 без BOM читает его как
# ANSI, русские комментарии превращаются в «умные кавычки» и скрипт не
# парсится совсем (ParserError вместо запуска ботов).

# Папка проекта = папка этого файла. $PSScriptRoot надёжнее $MyInvocation:
# при запуске через «powershell -Command "& .\start_bots.ps1"» последний пуст,
# и боты стартовали бы не там, где лежит config.py.
$here = $PSScriptRoot
if (-not $here) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }

# Интерпретатор ищем сами, а не пишем голое "python": в PATH первым стоит
# системный Python 3.9 БЕЗ pybit, и каждое окно бота мгновенно падало с
# ModuleNotFoundError. Кандидат годится, только если в нём есть pybit —
# без торгового API бот всё равно нежилец.
function Test-Py([string]$exe) {
    if (-not $exe) { return $false }
    if (-not (Test-Path $exe)) { return $false }
    & $exe -c "import pybit" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

# Лаунчер и PATH приводим к ПУТИ exe: команду мы передаём в новое окно, а там
# PATH может оказаться другим — путь к файлу однозначен всегда.
function Resolve-Py([string[]]$cmd) {
    # хвост берём отдельно: у массива из одного элемента $cmd[1..0] в
    # PowerShell разворачивается задом наперёд и подсовывает лишний аргумент
    $rest = @()
    if ($cmd.Length -gt 1) { $rest = $cmd[1..($cmd.Length - 1)] }
    try {
        $out = & $cmd[0] ($rest + @("-c", "import sys; print(sys.executable)")) 2>$null
    } catch { return $null }
    if (-not $out) { return $null }
    return ($out | Select-Object -Last 1).ToString().Trim()
}

$py = $null
foreach ($c in @($env:BBBOT_PYTHON, "$here\venv\Scripts\python.exe",
                 "$here\.venv\Scripts\python.exe", "$here\env\Scripts\python.exe")) {
    if (-not $py -and (Test-Py $c)) { $py = $c }
}
if (-not $py) {
    $exe = Resolve-Py @("py", "-3")
    if (Test-Py $exe) { $py = $exe }
}
if (-not $py) {
    $exe = Resolve-Py @("python")
    if (Test-Py $exe) { $py = $exe }
}

if (-not $py) {
    Write-Host ""
    Write-Host "Не нашёл Python, в котором установлен pybit - запускать ботов нечем." -ForegroundColor Red
    Write-Host "Проверено: BBBOT_PYTHON, venv/.venv/env рядом с этим файлом, py -3, python из PATH."
    Write-Host ""
    Write-Host "Что сделать один раз в папке проекта:"
    Write-Host "    py -3 -m venv venv"
    Write-Host "    venv\Scripts\python.exe -m pip install -r requirements.txt"
    Write-Host "Если окружение лежит в другом месте, задать путь к нему:"
    Write-Host "    `$env:BBBOT_PYTHON = 'D:\путь\venv\Scripts\python.exe'"
    exit 1
}
Write-Host "Python: $py"

foreach ($coin in @("DOGE", "LTC", "BTC", "ETH", "SOL")) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "cd '$here'; & '$py' bot_rsi.py $coin"
}

# Турбо-боты (раскомментируй, когда обкатаешь обычные):
# Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$here'; & '$py' bot_rsi.py ETH turbo"
# Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$here'; & '$py' bot_rsi.py BTC turbo"
