FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data && useradd -m appuser && chown -R appuser:appuser /app /data
USER appuser

ENV DB_PATH=/data/hexiron_games.db
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

CMD ["python", "main.py"]
