# -*- coding: utf-8 -*-
"""
Servidor del dashboard con autenticación HTTP Basic.
Usuario y contraseña desde variables de entorno:
  DASHBOARD_USER (defecto: "admin")
  DASHBOARD_PASSWORD (obligatorio en Railway, defecto: "cartera2026")
"""
import http.server
import urllib.request
import json
import os
import re
import base64
import subprocess
import threading

PORT = int(os.environ.get("PORT", "5000"))
DIR = os.path.dirname(os.path.abspath(__file__))

AUTH_USER = os.environ.get("DASHBOARD_USER", "admin")
AUTH_PASS = os.environ.get("DASHBOARD_PASSWORD", "cartera2026")
REGENERATE_KEY = os.environ.get("REGENERATE_KEY", "")

def check_auth(headers):
    auth = headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        user, pwd = decoded.split(":", 1)
        return user == AUTH_USER and pwd == AUTH_PASS
    except:
        return False

def send_401(handler):
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="Dashboard Cartera"')
    handler.send_header("Content-Type", "text/plain")
    handler.end_headers()
    handler.wfile.write(b"Autenticacion requerida")

LAST_REGENERATE = {"ok": False, "msg": "", "time": ""}
def regenerate():
    def task():
        global LAST_REGENERATE
        try:
            r = subprocess.run(
                ["python", "generate_dashboard.py"],
                cwd=DIR, capture_output=True, timeout=600)
            LAST_REGENERATE["ok"] = r.returncode == 0
            LAST_REGENERATE["time"] = os.popen("date 2>nul || date 2>/dev/null").read().strip()
            out = r.stdout.decode(errors="replace")[-5000:]
            err = r.stderr.decode(errors="replace")[-5000:]
            LAST_REGENERATE["msg"] = f"rc={r.returncode}\nSTDOUT:{out}\nSTDERR:{err}"
            if r.returncode != 0:
                print(f"[regenerate] returncode={r.returncode}")
                print(out[-500:])
                print(err[-500:])
        except Exception as e:
            LAST_REGENERATE["ok"] = False
            LAST_REGENERATE["msg"] = str(e)
            print(f"[regenerate] EXCEPTION: {e}")
    threading.Thread(target=task, daemon=True).start()

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def do_GET(self):
        # Healthcheck endpoint (no auth required)
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            return
        # Regenerate endpoint (cron trigger, no auth)
        if self.path.startswith("/api/regenerate"):
            q = self.path.split("?", 1)
            key = ""
            if len(q) > 1:
                params = q[1].split("&")
                for p in params:
                    if "=" in p:
                        k, v = p.split("=", 1)
                        if k == "key":
                            key = v
            if REGENERATE_KEY and key != REGENERATE_KEY:
                self.send_response(403)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return
            # debug=1 => synchronous with output, else async for cron
            q_parts = self.path.split("?", 1)
            has_debug = len(q_parts) > 1 and "debug=1" in q_parts[1]
            if has_debug:
                import time as _t
                t0 = _t.time()
                try:
                    r = subprocess.run(
                        ["python", "generate_dashboard.py"],
                        cwd=DIR, capture_output=True, timeout=600)
                    el = _t.time() - t0
                    out = r.stdout.decode(errors="replace")[-5000:]
                    err = r.stderr.decode(errors="replace")[-5000:]
                    msg = f"rc={r.returncode} elapsed={el:.1f}s\n=== STDOUT ===\n{out}\n=== STDERR ===\n{err}"
                except Exception as e:
                    el = _t.time() - t0
                    msg = f"EXCEPTION after {el:.1f}s: {e}"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(msg.encode())
            else:
                regenerate()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Regenerating dashboard...")
            return
        if self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(LAST_REGENERATE, indent=2).encode())
            return
        if not check_auth(self.headers):
            return send_401(self)
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

print(f"Servidor iniciado en puerto {PORT}")
print(f"Sirviendo: {DIR}")
print(f"Dashboard: http://localhost:{PORT}/dashboard.html")
if "DASHBOARD_PASSWORD" in os.environ:
    print(f"Autenticación: usuario={AUTH_USER} (desde DASHBOARD_PASSWORD)")
else:
    print(f"! DASHBOARD_PASSWORD no definida, usando contraseña por defecto: {AUTH_PASS}")
http.server.HTTPServer(("0.0.0.0", PORT), DashboardHandler).serve_forever()
