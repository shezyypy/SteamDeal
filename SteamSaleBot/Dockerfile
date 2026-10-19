FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Приложение не должно работать от root. Каталоги с демо-кэшем остаются
# доступными для локального режима, но секреты по-прежнему приходят только из env.
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

CMD ["python", "main.py"]
