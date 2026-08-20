# Сайт исследования ботов Bybit: Flask за gunicorn.
#
# Собирается из корня репозитория: webapp/app.py при импорте делает chdir
# в корень проекта и импортирует оттуда config, evolution*, ext_data, patterns —
# без этих файлов сайт не поднимется, поэтому копируем проект целиком.

FROM python:3.12-slim

# Слой зависимостей отдельно: пока requirements.txt не менялся, пересборка
# после правки кода не тянет заново pandas и numpy — а это минуты на одном ядре.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir gunicorn

COPY . .

# Кэши свечей и аналитика лежат рядом с кодом и дописываются на ходу,
# поэтому каталог должен быть доступен на запись пользователю приложения.
RUN useradd -u 1001 -m site && chown -R 1001:1001 /app
USER 1001

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Один воркер и потоки — почему именно так, объяснено в webapp/wsgi.py.
# Таймаут 120 с: часть страниц считает проверку сигналов на лету, и на
# медленной машине это занимает десятки секунд.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", \
     "--timeout", "120", "--access-logfile", "-", "webapp.wsgi:app"]
