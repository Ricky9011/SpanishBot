FROM python:3.10-bullseye

WORKDIR /app

# Configurar repositorios correctamente
RUN sed -i 's/main/main contrib non-free/' /etc/apt/sources.list && \
    echo "deb http://security.debian.org/debian-security/ bullseye-security main" >> /etc/apt/sources.list && \
    echo "deb http://deb.debian.org/debian bullseye-updates main contrib non-free" >> /etc/apt/sources.list && \
    apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        libpq-dev \
        netcat \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "spanishDailybot.py"]
