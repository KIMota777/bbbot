#!/bin/sh
# Запуск: ./wsa.sh <команда>. Питон — из локального окружения проекта.
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/.venv/bin/python" -m wsa "$@"
