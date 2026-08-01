# Запуск всей системы БЕЗ окон терминала.
#
# Два требования сразу: процессы переживают закрытие консоли (поэтому WMI
# Win32_Process.Create, а не дочерний процесс) и не показывают окон
# (поэтому pythonw.exe — он консоль не создаёт вовсе; обёртка «cmd /c»
# не годится: cmd открывает окно даже при ShowWindow=0).
# У pythonw нет stdout/stderr, а скрипты пишут в консоль и на этом падают,
# поэтому запуск идёт через run_hidden.py — он перенаправляет вывод в
# logs\<имя>.log и уже затем стартует целевой скрипт.
#
# Запуск:    powershell -ExecutionPolicy Bypass -File start_all.ps1
# Остановка: powershell -ExecutionPolicy Bypass -File stop_all.ps1

$DIR = "d:\prog\bybit_bot"
Set-Location $DIR

$PYW = Join-Path (Split-Path (Get-Command python).Source) "pythonw.exe"
if (-not (Test-Path $PYW)) {
    "pythonw.exe не найден — запускаю обычным python, окна будут видны"
    $PYW = (Get-Command python).Source
}
$proc = [wmiclass]"Win32_Process"

function Start-Bg([string]$title, [string]$target) {
    $r = $proc.Create("`"$PYW`" run_hidden.py $target", $DIR, $null)
    if ($r.ReturnValue -eq 0) { "{0,-22} запущен (pid {1})" -f $title, $r.ProcessId }
    else { "{0,-22} ОШИБКА (код {1})" -f $title, $r.ReturnValue }
}

Start-Bg "сайт"              "webapp\app.py"
Start-Bg "помощник сигналов" "advisor.py"
foreach ($coin in @("DOGE", "LTC", "BTC", "ETH", "SOL")) {
    Start-Bg "бот $coin" "bot_rsi.py $coin"
}

Start-Sleep -Seconds 8
"`nПроверка:"
try {
    $r = Invoke-WebRequest "http://127.0.0.1:8000/" -UseBasicParsing -TimeoutSec 25
    "  сайт отвечает: $($r.StatusCode) -> http://127.0.0.1:8000"
} catch {
    "  сайт НЕ отвечает — смотри logs\app.log"
}
$n = (Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
      Where-Object { $_.CommandLine -match "run_hidden" } | Measure-Object).Count
"  живых процессов: $n из 7"
$w = (Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
      Where-Object { $_.CommandLine -match "bybit_bot" } | Measure-Object).Count
"  окон терминала: $w (должно быть 0)"
"`nЛоги системы: logs\app.log, logs\advisor.log, logs\bot_rsi_<МОНЕТА>.log"
"Логи стратегий: bot_<МОНЕТА>USDT_final.log, advisor.log"
