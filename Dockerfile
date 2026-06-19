# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Install Python deps (prebuilt wheels, no gcc needed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# tzdata for zoneinfo timezone support
RUN pip install --no-cache-dir tzdata

# Copy project files
COPY config.json config_loader.py generate_dashboard.py server.py screener.py start.sh ./
COPY fin_data_final.xlsx tickers_universo.json price_history.csv ./

# Pre-generate a baseline dashboard (best-effort, may skip yfinance)
RUN python generate_dashboard.py skip_screener || true

RUN chmod +x start.sh

EXPOSE 5000
CMD ["./start.sh"]
