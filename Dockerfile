FROM python:3.11-slim

# Install Chromium dependencies manually (avoids playwright install-deps font issues on Debian Trixie)
RUN apt-get update && apt-get install -y \
    wget curl gnupg \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libpangocairo-1.0-0 \
    libasound2t64 \
    libgtk-3-0 libx11-xcb1 libxshmfence1 \
    fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY opportunity_scanner.py .

RUN mkdir -p /app/data

CMD ["python", "opportunity_scanner.py"]
