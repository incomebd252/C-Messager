FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    CM_DATA_DIR=/app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# database, upload kora chhobi, users.xlsx ei folder e thake - volume mount korben
VOLUME /app/data
EXPOSE 5000

CMD ["sh", "-c", "gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT:-5000} app:app"]
