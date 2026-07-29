# Запуск всех ботов, каждый в своём окне PowerShell.
# Обычный режим x5 — все 5 монет; турбо x20 — только ETH и BTC (лучшие в бэктесте).
# Закрыть бота = закрыть его окно (позиции на бирже при этом остаются!).

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

foreach ($coin in @("DOGE", "LTC", "BTC", "ETH", "SOL")) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "cd '$here'; python bot_rsi.py $coin"
}

# Турбо-боты (раскомментируй, когда обкатаешь обычные):
# Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$here'; python bot_rsi.py ETH turbo"
# Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$here'; python bot_rsi.py BTC turbo"
