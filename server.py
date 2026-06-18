# -*- coding: utf-8 -*-
"""
Servidor local del dashboard.
Sirve dashboard.html en http://localhost:5000
API /api/price/<ticker> devuelve precio actual desde Yahoo Finance.
Ejecutar: python server.py
Luego abrir: http://localhost:5000
"""
import http.server
import urllib.request
import json
import os
import re

PORT = int(os.environ.get("PORT", "5000"))
DIR = os.path.dirname(os.path.abspath(__file__))

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def do_GET(self):
        # API endpoint for live price
        m = re.match(r"/api/price/([A-Za-z0-9.=-]+)", self.path)
        if m:
            ticker = m.group(1)
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                resp = urllib.request.urlopen(req, timeout=10)
                data = json.loads(resp.read())
                result = data.get("chart", {}).get("result", [{}])[0] if data.get("chart", {}).get("result") else {}
                meta = result.get("meta", {})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ticker": ticker,
                    "price": meta.get("regularMarketPrice"),
                    "previousClose": meta.get("previousClose"),
                    "high52": meta.get("fiftyTwoWeekHigh"),
                    "low52": meta.get("fiftyTwoWeekLow"),
                    "currency": meta.get("currency"),
                }).encode())
            except Exception as e:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"ticker": ticker, "price": None, "error": str(e)}).encode())
            return
        # Serve static files
        return super().do_GET()

    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {args[0]} {args[1]} {args[2]}")

print(f"Servidor iniciado en http://localhost:{PORT}")
print(f"Sirviendo: {DIR}")
print(f"Abre http://localhost:{PORT}/dashboard.html en tu navegador")
print("Presiona Ctrl+C para detener")
http.server.HTTPServer(("", PORT), DashboardHandler).serve_forever()
