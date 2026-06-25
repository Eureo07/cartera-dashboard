# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV TZ=Europe/Madrid
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --upgrade --no-cache-dir "yfinance>=1.3"

# Copy all project files
COPY config.json config_loader.py generate_dashboard.py server.py screener.py start.sh dashboard.html ./
COPY fin_data_final.xlsx tickers_universo.json price_history.csv ./

RUN chmod +x start.sh

# Force no-cache on generate_dashboard.py
RUN head -1 generate_dashboard.py

EXPOSE 5000
CMD ["./start.sh"]
# rebuild-22
