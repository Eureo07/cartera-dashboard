# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV TZ=Europe/Madrid
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --upgrade --no-cache-dir "yfinance>=1.3"

# Copy all project files
COPY config.json config_loader.py generate_dashboard.py server.py screener.py start.sh dashboard.html ./
COPY fondos_indexados.json cuenta_remunerada.json fondos_comparativa.json cuenta_remunerada_myinvestor.json ./
COPY fin_data_final.xlsx tickers_universo.json price_history.csv ./
COPY ipc_ine.py ipc_cache.json expectancy.py position_sizing.py regimen_mercado.py alertas.py ./

RUN chmod +x start.sh

# Cache buster
ARG CACHEBUST=2
RUN head -1 generate_dashboard.py

EXPOSE 5000
CMD ["./start.sh"]
