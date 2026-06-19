# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV TZ=Europe/Madrid

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.json config_loader.py generate_dashboard.py server.py screener.py start.sh ./
COPY fin_data_final.xlsx tickers_universo.json price_history.csv ./

RUN chmod +x start.sh

EXPOSE 5000
CMD ["./start.sh"]
