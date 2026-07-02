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

_WL_CACHE = {"data": None, "updated": None}
_WL_TTL = 300  # 5 min

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
        if self.path.startswith("/api/earnings-watchlist"):
            self._send_json_cache(_WATCHLIST_CACHE, _WATCHLIST_TTL, _compute_watchlist)
            return
        # API: Alternatives (no auth — same origin from logged-in page)
        if self.path.startswith("/api/alternatives"):
            self._send_json_cache(_ALT_CACHE, _ALT_TTL, self._compute_alternatives)
            return
        # API: Radar (no auth — same origin from logged-in page)
        if self.path.startswith("/api/radar"):
            self._send_json_cache(_RADAR_CACHE, _RADAR_TTL, self._compute_radar)
            return
        # API: Live prices for portfolio positions (no auth)
        if self.path.startswith("/api/prices"):
            # allow /api/prices?refresh=1 to bypass cache
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p) if qs else {}
            if "refresh" in params:
                _PRICES_CACHE["data"] = None
                _PRICES_CACHE["updated"] = None
            self._send_json_cache(_PRICES_CACHE, _PRICES_TTL, self._compute_prices)
            return
        # API: Watchlist (study list, no auth)
        if self.path.startswith("/api/watchlist"):
            self._send_json_cache(_WL_CACHE, _WL_TTL, self._compute_watchlist_study)
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

    def _compute_watchlist_study(self):
        """Compute watchlist study data. Only 3 states: sin_senal/activa/confirmado."""
        from screener import get_entry_types, calcular_soporte_resistencia
        import yfinance as yf
        try:
            wl_path = os.path.join(DIR, "watchlist.json")
            if not os.path.exists(wl_path):
                return {"error": True, "msg": "watchlist.json no encontrado", "items": []}
            with open(wl_path, "r", encoding="utf-8") as f:
                watchlist = json.load(f)
            results = []
            for item in watchlist:
                tk = item["ticker"]
                entry_level = item["entry_level"]
                entry_signal = item["entry_signal"]
                # 1) Current price via chart API
                cur_price = None
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=1d&range=5d"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                    resp = urllib.request.urlopen(req, timeout=10)
                    chart = json.loads(resp.read())
                    meta = chart.get("chart", {}).get("result", [{}])[0].get("meta", {})
                    cur_price = meta.get("regularMarketPrice")
                    if cur_price is not None:
                        cur_price = float(cur_price)
                except Exception:
                    pass
                # 2) Distance to entry level
                dist_pct = ((cur_price - entry_level) / entry_level) * 100 if cur_price else None
                # 3) Entry types from screener
                detected_types = get_entry_types(tk)
                signal_active = entry_signal in detected_types
                # 4) Support
                support_val = None
                support_ok = False
                try:
                    sv, _, _, sok = calcular_soporte_resistencia(tk)
                    support_val = sv
                    support_ok = sok
                except Exception:
                    pass
                # 5) Weekly granular F1/F2/F3 check (only if signal active)
                f1_ok = f2_ok = f3_ok = False
                if signal_active:
                    try:
                        weekly = yf.Ticker(tk).history(period="2y", interval="1wk")
                        if weekly is not None and not weekly.empty and len(weekly) >= 22:
                            c = weekly['Close'].values; h = weekly['High'].values; v = weekly['Volume'].values
                            ld = weekly.index[-1].to_pydatetime() if hasattr(weekly.index[-1], 'to_pydatetime') else weekly.index[-1]
                            hrs = (datetime.now(timezone.utc) - ld).total_seconds() / 3600
                            ref = -2 if hrs < 48 else -1
                            if abs(ref) + 22 <= len(weekly):
                                ws = max(0, ref - 52); pm = float(max(h[ws:ref]))
                                f1_ok = c[ref] > pm
                                vs = max(0, ref - 20); vm = float(sum(v[vs:ref]) / (ref - vs))
                                f2_ok = v[ref] > vm
                                cp = float(c[-1]); lb = pm * 0.95
                                f3_ok = lb <= cp <= pm if entry_signal in ('RR','RRA') else True
                    except Exception:
                        pass
                # 6) Visual status (only 3 states)
                if signal_active and f1_ok and f2_ok and f3_ok and support_ok:
                    visual_status = "confirmado"
                elif signal_active and f2_ok and f3_ok and not f1_ok:
                    visual_status = "activa"
                else:
                    visual_status = "sin_senal"
                results.append({
                    "ticker": tk,
                    "name": item.get("name", tk),
                    "entry_level": entry_level,
                    "entry_signal": entry_signal,
                    "current_price": cur_price,
                    "distance_pct": round(dist_pct, 2) if dist_pct is not None else None,
                    "detected_types": detected_types,
                    "signal_active": signal_active,
                    "support": support_val,
                    "support_ok": support_ok,
                    "f1_ok": f1_ok,
                    "f2_ok": f2_ok,
                    "f3_ok": f3_ok,
                    "visual_status": visual_status,
                    "notes": item.get("notes", ""),
                })
            # 7) Alert on status change
            self._wl_check_alerts(results)
            return {"items": results, "updated": datetime.now().isoformat()}
        except Exception as e:
            print(f"[watchlist] ERROR: {e}")
            return {"error": True, "msg": str(e), "items": []}

    def _wl_send_alert(self, item, new_status):
        subject = f"[Watchlist] {item['ticker']} — {item['name']}: {new_status}"
        emoji = {"activa": "\U0001F7E1", "confirmado": "\U0001F7E2"}.get(new_status, "\U0001F534")
        body = f"""<h3>{emoji} {item['name']} ({item['ticker']})</h3>
<p><b>Estado:</b> {new_status}</p>
<p><b>Precio:</b> {item.get('current_price', 'N/D')} \u20ac</p>
<p><b>Nivel entrada:</b> {item['entry_level']} \u20ac</p>
<p><b>Se\u00f1al esperada:</b> {item['entry_signal']}</p>
<p><b>Se\u00f1ales detectadas:</b> {', '.join(item.get('detected_types', [])) or 'ninguna'}</p>
<p><b>Soporte:</b> {item.get('support', 'N/D')} \u20ac</p>
<p><b>Notas:</b> {item.get('notes', '')}</p>
<hr><p style="color:#9aa0b0">Dashboard Cartera</p>"""
        try:
            from alertas import send_email
            send_email(subject, body)
        except Exception as e:
            print(f"[watchlist] Alert error: {e}")

    def _wl_check_alerts(self, results):
        status_file = os.path.join(DIR, "watchlist_status_cache.json")
        prev = {}
        if os.path.exists(status_file):
            try:
                with open(status_file, "r", encoding="utf-8") as f:
                    prev = json.load(f)
            except Exception:
                pass
        new_prev = {}
        for r in results:
            tk = r["ticker"]
            new_s = r["visual_status"]
            old_s = prev.get(tk, "sin_senal")
            new_prev[tk] = new_s
            if new_s in ("activa", "confirmado") and old_s != new_s:
                print(f"[watchlist] ALERT: {tk} cambió a {new_s}")
                self._wl_send_alert(r, new_s)
        try:
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump(new_prev, f, indent=2)
        except Exception:
            pass

    def _compute_prices(self):
        try:
            import yfinance as yf
            import requests as _req, concurrent.futures
            _sess = _req.Session()
            _sess.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

            def fetch_ticker_price(tk):
                try:
                    info = yf.Ticker(tk, session=_sess).info or {}
                    cur = info.get("regularMarketPrice") or info.get("previousClose") or info.get("currentPrice")
                    if cur is not None:
                        cur = float(cur)
                    prev_close = None
                    try:
                        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=1d&range=5d"
                        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                        resp = urllib.request.urlopen(req, timeout=10)
                        chart = json.loads(resp.read())
                        meta = chart.get("chart", {}).get("result", [{}])[0].get("meta", {})
                        cp = meta.get("chartPreviousClose")
                        if cp is not None:
                            prev_close = float(cp)
                        rmp = meta.get("regularMarketPrice")
                        if rmp is not None:
                            cur = float(rmp)
                    except Exception:
                        pass
                    if prev_close is None:
                        prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
                        if prev_close is not None:
                            prev_close = float(prev_close)
                    day_var = (cur - prev_close) if (cur and prev_close) else 0
                    return (tk, {"current": cur, "prev_close": prev_close, "day_var": round(day_var, 2)})
                except Exception:
                    return (tk, None)

            data = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as exec:
                fut = {exec.submit(fetch_ticker_price, p["ticker"]): p["ticker"] for p in CFG.get("portfolio", [])}
                for f in concurrent.futures.as_completed(fut, timeout=30):
                    tk, result = f.result()
                    if result:
                        data[tk] = result
            # Benchmark ^STOXX50E
            try:
                bench_info = yf.Ticker("^STOXX50E", session=_sess).info or {}
                bcur = bench_info.get("regularMarketPrice") or bench_info.get("previousClose") or bench_info.get("currentPrice")
                bprev = bench_info.get("regularMarketPreviousClose") or bench_info.get("previousClose")
                try:
                    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ESTOXX50E?interval=1d&range=5d"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                    resp = urllib.request.urlopen(req, timeout=10)
                    chart = json.loads(resp.read())
                    meta = chart.get("chart", {}).get("result", [{}])[0].get("meta", {})
                    cp = meta.get("chartPreviousClose")
                    if cp is not None:
                        bprev = float(cp)
                    rmp = meta.get("regularMarketPrice")
                    if rmp is not None:
                        bcur = float(rmp)
                except Exception:
                    pass
                if bprev is not None:
                    bprev = float(bprev)
                if bcur is not None:
                    bcur = float(bcur)
                data["^STOXX50E"] = {"current": bcur, "prev_close": bprev}
            except Exception:
                data["^STOXX50E"] = {"current": None, "prev_close": None}
            return {"prices": data, "updated": datetime.now().isoformat()}
        except Exception as e:
            return {"error": True, "msg": str(e), "prices": {}}

    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {args[0] if len(args) > 0 else ''} {args[1] if len(args) > 1 else ''} {args[2] if len(args) > 2 else ''}")

import socket, os, sys

def find_free_port(start):
    for p in range(start, start + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start

BASE_PORT = PORT
PORT = find_free_port(BASE_PORT)
if PORT != BASE_PORT:
    print(f"Puerto {BASE_PORT} ocupado, usando {PORT}")

print(f"PID: {os.getpid()}")
print(f"Servidor iniciado en puerto {PORT}")
print(f"Sirviendo: {DIR}")
print(f"Dashboard: http://localhost:{PORT}/dashboard.html")
if "DASHBOARD_PASSWORD" in os.environ:
    print(f"Autenticación: usuario={AUTH_USER} (desde DASHBOARD_PASSWORD)")
else:
    print(f"! DASHBOARD_PASSWORD no definida, usando contraseña por defecto: {AUTH_PASS}")
socketserver.ThreadingTCPServer(("0.0.0.0", PORT), DashboardHandler).serve_forever()
