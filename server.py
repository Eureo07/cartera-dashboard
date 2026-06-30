# -*- coding: utf-8 -*-
"""
Servidor del dashboard con autenticación HTTP Basic.
Usuario y contraseña desde variables de entorno:
  DASHBOARD_USER (defecto: "admin")
  DASHBOARD_PASSWORD (obligatorio en Railway, defecto: "cartera2026")
"""
import http.server
import socketserver
import urllib.request
import json
import os
import re
import base64
import subprocess
import threading
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import sys
_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)
from config_loader import CFG
import time as _ytime

# ========== FMP API ==========
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
_FMP_CACHE = {}  # {"ticker_field": {"value": ..., "updated": "iso"}}
_FMP_CACHE_TTL = 24 * 3600  # 24h

def _fmp_url(endpoint, ticker):
    return f"https://financialmodelingprep.com/api/v3/{endpoint}/{ticker}?limit=1&apikey={FMP_API_KEY}"

def _fetch_fmp(endpoint, ticker):
    """Try ticker with exchange suffix first, then without. Returns (value, source) or (None, None)."""
    if not FMP_API_KEY:
        return None, None
    variants = [ticker]
    parts = ticker.split(".")
    if len(parts) > 1:
        variants.append(parts[0])
    for v in variants:
        cache_key = f"{endpoint}:{v}"
        now = datetime.now()
        if cache_key in _FMP_CACHE:
            age = (now - datetime.fromisoformat(_FMP_CACHE[cache_key]["updated"])).total_seconds()
            if age < _FMP_CACHE_TTL:
                return _FMP_CACHE[cache_key]["value"], "fmp"
        try:
            url = _fmp_url(endpoint, v)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            if isinstance(data, list) and len(data) > 0:
                val = data[0]
                _FMP_CACHE[cache_key] = {"value": val, "updated": now.isoformat()}
                return val, "fmp"
        except Exception:
            continue
    return None, None

# ========== EARNINGS WATCHLIST ==========
_WATCHLIST_CACHE = {"data": None, "updated": None}
_WATCHLIST_TTL = 24 * 3600

def _get_metric_value(ticker, metrica, df_xlsx):
    """Fetch a metric value following FMP -> yfinance -> xlsx hierarchy.
    Returns (valor, fuente) where fuente is 'fmp', 'yfinance', 'xlsx', or None."""
    fmp_field = metrica.get("fmp_campo")
    yf_field = metrica.get("yf_campo")
    xlsx_field = metrica.get("xlsx_campo")
    # 1) FMP
    if fmp_field:
        val, src = _fetch_fmp("ratios", ticker)
        if val is not None and isinstance(val, dict) and fmp_field in val and val[fmp_field] is not None:
            return float(val[fmp_field]), "fmp"
        val2, _ = _fetch_fmp("key-metrics", ticker)
        if val2 is not None and isinstance(val2, dict) and fmp_field in val2 and val2[fmp_field] is not None:
            return float(val2[fmp_field]), "fmp"
    # 2) yfinance
    if yf_field:
        try:
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info
            if yf_field in info and info[yf_field] is not None:
                return float(info[yf_field]), "yfinance"
        except Exception:
            pass
    # 3) xlsx
    if xlsx_field and df_xlsx is not None:
        try:
            match = df_xlsx[df_xlsx["Ticker"].astype(str).str.strip() == ticker]
            if not match.empty and xlsx_field in match.columns:
                val = match.iloc[0][xlsx_field]
                if val is not None and not (isinstance(val, float) and pd.isna(val)):
                    return float(val), "xlsx"
        except Exception:
            pass
    return None, None

def _compute_metric_status(valor, umbral_alerta, umbral_venta, direccion):
    """Compute metric status based on direction thresholds."""
    if valor is None:
        return "sin_dato"
    if direccion == "menor_es_peor":
        if umbral_venta is not None and valor < umbral_venta:
            return "venta"
        if umbral_alerta is not None and valor < umbral_alerta:
            return "alerta"
        return "ok"
    # mayor_es_peor
    if umbral_venta is not None and valor > umbral_venta:
        return "venta"
    if umbral_alerta is not None and valor > umbral_alerta:
        return "alerta"
    return "ok"

def _compute_watchlist(df_xlsx=None):
    """Compute earnings watchlist data."""
    if df_xlsx is None:
        try:
            df_xlsx = pd.read_excel(os.path.join(DIR, CFG["paths"]["excel"]))
        except Exception:
            df_xlsx = None
    watchlist = CFG.get("earnings_watchlist", [])
    now = datetime.now()
    empresas = []
    for item in watchlist:
        ticker = item["ticker"]
        fecha_earnings = datetime.strptime(item["fecha_earnings"], "%Y-%m-%d")
        dias = (fecha_earnings - now).days
        metricas = []
        estados = []
        for m in item["metricas"]:
            valor, fuente = _get_metric_value(ticker, m, df_xlsx)
            estado = _compute_metric_status(valor, m.get("umbral_alerta"), m.get("umbral_venta"), m.get("direccion"))
            estados.append(estado)
            metricas.append({
                "nombre": m["nombre"],
                "valor": valor,
                "formato": m.get("formato", "ratio"),
                "fuente": fuente if fuente else "sin_dato",
                "umbral_alerta": m.get("umbral_alerta"),
                "umbral_venta": m.get("umbral_venta"),
                "estado": estado,
            })
        if "venta" in estados:
            estado_global = "venta"
        elif "alerta" in estados:
            estado_global = "alerta"
        elif all(e == "sin_dato" for e in estados):
            estado_global = "sin_dato"
        else:
            estado_global = "ok"
        empresas.append({
            "ticker": ticker,
            "nombre": item["nombre"],
            "fecha_earnings": item["fecha_earnings"],
            "dias_hasta_earnings": dias,
            "condicion_venta": item["condicion_venta_texto"],
            "estado_global": estado_global,
            "metricas": metricas,
        })
    return {"updated": now.isoformat(), "empresas": empresas}

# ========== ALTERNATIVES CACHE ==========
_ALT_CACHE = {"data": None, "updated": None}
_ALT_TTL = 24 * 3600

_RADAR_CACHE = {"data": None, "updated": None}
_RADAR_TTL = 24 * 3600

_PRICES_CACHE = {"data": None, "updated": None}
_PRICES_TTL = 300  # 5 min

PORT = int(os.environ.get("PORT", "5000"))
DIR = os.path.dirname(os.path.abspath(__file__))

AUTH_USER = os.environ.get("DASHBOARD_USER", "admin")
AUTH_PASS = os.environ.get("DASHBOARD_PASSWORD", "cartera2026")
REGENERATE_KEY = os.environ.get("REGENERATE_KEY", "")

def check_auth(headers):
    auth = ""
    for k, v in headers.items():
        if k.lower() == "authorization":
            auth = v
            break
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
            out = r.stdout.decode(errors="replace")[-2000:]
            err = r.stderr.decode(errors="replace")[-2000:]
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
                    out = r.stdout.decode(errors="replace")[-3000:]
                    err = r.stderr.decode(errors="replace")[-3000:]
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
        # API: Earnings watchlist (no auth — same origin from logged-in page)
        if self.path == "/api/earnings-watchlist":
            self._send_json_cache(_WATCHLIST_CACHE, _WATCHLIST_TTL, _compute_watchlist)
            return
        # API: Alternatives (no auth — same origin from logged-in page)
        if self.path == "/api/alternatives":
            self._send_json_cache(_ALT_CACHE, _ALT_TTL, self._compute_alternatives)
            return
        # API: Radar (no auth — same origin from logged-in page)
        if self.path == "/api/radar":
            self._send_json_cache(_RADAR_CACHE, _RADAR_TTL, self._compute_radar)
            return
        # API: Live prices for portfolio positions (no auth)
        if self.path == "/api/prices":
            self._send_json_cache(_PRICES_CACHE, _PRICES_TTL, self._compute_prices)
            return
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
        # Auth check for static files
        if not check_auth(self.headers):
            return send_401(self)
        return super().do_GET()

    def _send_json_cache(self, cache, ttl, compute_fn):
        now = datetime.now()
        if cache["data"] and cache["updated"]:
            age = (now - datetime.fromisoformat(cache["updated"])).total_seconds()
            if age < ttl:
                self._send_json(cache["data"])
                return
        try:
            result = compute_fn()
            cache["data"] = result
            cache["updated"] = now.isoformat()
            self._send_json(result)
        except Exception as e:
            if cache["data"]:
                self._send_json(cache["data"])
            else:
                self._send_json({"error": True, "msg": str(e)})

    def _send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _compute_alternatives(self):
        from screener import ejecutar_radar
        sec_col = None
        df = None
        try:
            df = pd.read_excel(os.path.join(DIR, CFG["paths"]["excel"]))
            sec_col = df.columns[4]
        except Exception:
            return {"error": True, "msg": "No se pudo leer excel"}
        # Map portfolio tickers to sectors
        ticker_sectors = {}
        for p in CFG["portfolio"]:
            candidates = [p["ticker"]]
            if p.get("db_ticker"):
                candidates.append(p["db_ticker"])
            for t in candidates:
                match = df[df["Ticker"].astype(str).str.strip() == t]
                if not match.empty:
                    ticker_sectors[p["ticker"]] = str(match.iloc[0][sec_col]).strip()
                    break
        unique_sectors = sorted(set(ticker_sectors.values()))
        results = []
        for sec in unique_sectors:
            try:
                empresas = ejecutar_radar(sector_filter=sec, max_resultados=5)
                n_total = len(df[df[sec_col].str.strip() == sec]) if sec_col else 0
                results.append({
                    "sector": sec,
                    "empresas": [
                        {
                            "ticker": r["ticker"],
                            "name": r["name"],
                            "score": r["score"],
                            "eper": r["eper"],
                            "roe": r["roe"],
                            "rent_1a": r["rent_1a"],
                            "entry_types": r.get("entry_types", []),
                            "support": r.get("support"),
                            "current_price": r.get("current_price"),
                        }
                        for r in empresas
                    ],
                    "n_analizadas": int(n_total),
                })
            except Exception as e:
                print(f"[alternatives] Error en sector {sec}: {e}")
                results.append({"sector": sec, "empresas": [], "n_analizadas": 0, "error": True})
        return {"sectores": results, "updated": datetime.now().isoformat()}

    def _compute_radar(self):
        from screener import ejecutar_radar
        try:
            empresas = ejecutar_radar(max_resultados=15)
            return {
                "oportunidades": [
                    {
                        "ticker": r["ticker"],
                        "name": r["name"],
                        "score": r["score"],
                        "eper": r["eper"],
                        "current_price": r.get("current_price"),
                        "rent_1a": r.get("rent_1a"),
                        "entry_types": r.get("entry_types", []),
                        "support": r.get("support"),
                        "resistance": r.get("resistance"),
                    }
                    for r in empresas
                ],
                "total": len(empresas),
                "updated": datetime.now().isoformat(),
            }
        except Exception as e:
            print(f"[radar] ERROR: {e}")
            return {"error": True, "msg": str(e), "oportunidades": [], "total": 0}

    def _compute_prices(self):
        try:
            import yfinance as yf
            import requests as _req
            _sess = _req.Session()
            _sess.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            data = {}
            for p in CFG.get("portfolio", []):
                tk = p["ticker"]
                try:
                    info = yf.Ticker(tk, session=_sess).info or {}
                    cur = info.get("regularMarketPrice") or info.get("previousClose") or info.get("currentPrice")
                    prev = info.get("previousClose")
                    if cur is not None:
                        cur = float(cur)
                    if prev is not None:
                        prev = float(prev)
                    day_var = (cur - prev) if (cur and prev) else 0
                    data[tk] = {"current": cur, "prev_close": prev, "day_var": round(day_var, 2)}
                except Exception:
                    data[tk] = {"current": None, "prev_close": None, "day_var": 0}
            return {"prices": data, "updated": datetime.now().isoformat()}
        except Exception as e:
            return {"error": True, "msg": str(e), "prices": {}}

    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {args[0] if len(args) > 0 else ''} {args[1] if len(args) > 1 else ''} {args[2] if len(args) > 2 else ''}")

print(f"Servidor iniciado en puerto {PORT}")
print(f"Sirviendo: {DIR}")
print(f"Dashboard: http://localhost:{PORT}/dashboard.html")
if "DASHBOARD_PASSWORD" in os.environ:
    print(f"Autenticación: usuario={AUTH_USER} (desde DASHBOARD_PASSWORD)")
else:
    print(f"! DASHBOARD_PASSWORD no definida, usando contraseña por defecto: {AUTH_PASS}")
socketserver.ThreadingTCPServer(("0.0.0.0", PORT), DashboardHandler).serve_forever()
