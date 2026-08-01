# Остановка всей системы: сайт, помощник сигналов, боты.
# Запуск: powershell -ExecutionPolicy Bypass -File stop_all.ps1

$targets = Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match "bot_rsi|advisor|webapp\\app\.py|bot\.py" }

if (-not $targets) {
    "Нечего останавливать — процессы не запущены."
    return
}

foreach ($t in $targets) {
    $cmd = $t.CommandLine
    $name = if ($cmd -match "webapp\\app\.py") { "сайт" }
            elseif ($cmd -match "advisor") { "помощник" }
            elseif ($cmd -match "bot_rsi\.py (\w+)") { "бот $($Matches[1])" }
            else { "bot.py (старый)" }
    try {
        Stop-Process -Id $t.ProcessId -Force -Confirm:$false -ErrorAction Stop
        "остановлен: {0} (pid {1})" -f $name, $t.ProcessId
    } catch {
        "не удалось остановить {0} (pid {1}): {2}" -f $name, $t.ProcessId, $_
    }
}

# осиротевшие окна cmd, которыми оборачивался запуск
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
    Where-Object { $_.CommandLine -match "bybit_bot" } |
    ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction Stop } catch {}
    }

Start-Sleep -Seconds 1
$left = (Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
         Where-Object { $_.CommandLine -match "bot_rsi|advisor|webapp" } |
         Measure-Object).Count
"осталось процессов: $left"
