FROM python:3.10-slim

WORKDIR /app

# Configurar DNS y repositorios
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
        libpq-dev \
        netcat \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "spanishDailybot.py"]
