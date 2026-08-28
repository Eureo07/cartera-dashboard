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
from per_futuro import get_per_futuro
import time as _ytime

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False
from ipc_ine import inflacion_acumulada

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

# ========== ALPHA VANTAGE ==========
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")

def _fetch_alpha_vantage_nvda_price():
    """Consulta NVDA (NASDAQ) via Alpha Vantage y convierte USD a EUR.
    Retorna (current_eur, prev_close_eur) o None si falla."""
    if not ALPHA_VANTAGE_API_KEY:
        return None
    try:
        import urllib.request, json
        # 1) GLOBAL_QUOTE para NVDA
        url1 = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=NVDA&apikey={ALPHA_VANTAGE_API_KEY}"
        req1 = urllib.request.Request(url1, headers={"User-Agent": "Mozilla/5.0"})
        resp1 = urllib.request.urlopen(req1, timeout=10)
        data1 = json.loads(resp1.read())
        gq = data1.get("Global Quote", {})
        if not gq:
            return None
        nvda_price = float(gq.get("05. price", 0))
        nvda_prev = float(gq.get("08. previous close", 0))
        if nvda_price == 0 or nvda_prev == 0:
            return None
        # 2) Tipo de cambio USD→EUR
        url2 = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=EUR&apikey={ALPHA_VANTAGE_API_KEY}"
        req2 = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
        resp2 = urllib.request.urlopen(req2, timeout=10)
        data2 = json.loads(resp2.read())
        fx = data2.get("Realtime Currency Exchange Rate", {})
        fx_rate_str = fx.get("5. Exchange Rate")
        if not fx_rate_str:
            return None
        fx_rate = float(fx_rate_str)
        if fx_rate <= 0:
            return None
        cur_eur = round(nvda_price * fx_rate, 2)
        prev_eur = round(nvda_prev * fx_rate, 2)
        print(f"[AV] NVDA={nvda_price} USD, fx={fx_rate}, cur={cur_eur} EUR, prev={prev_eur} EUR")
        return (cur_eur, prev_eur)
    except Exception as e:
        print(f"[AV] Error: {e}")
        return None

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
_RADAR_FORCE_REFRESH = {"flag": False}

_PRICES_CACHE = {"data": None, "updated": None}
_PRICES_TTL = 30  # 30s (coincide con polling JS)

LIVE_PRICES_FILE = os.path.join(_PROJ_DIR, "live_prices_cache.json")

def _load_live_prices():
    try:
        with open(LIVE_PRICES_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _seed_live_prices_from_csv()

def _seed_live_prices_from_csv():
    """Seed cache from price_history.csv on first deploy."""
    csv_path = os.path.join(_PROJ_DIR, "price_history.csv")
    if not os.path.isfile(csv_path):
        return {}
    try:
        df = pd.read_csv(csv_path)
        if "fecha" not in df.columns or "ticker" not in df.columns or "precio" not in df.columns:
            return {}
        last = df.groupby("ticker").last().reset_index()
        data = {}
        for _, row in last.iterrows():
            tk = row["ticker"]
            px = float(row["precio"])
            data[tk] = {"current": px, "prev_close": px}
            print(f"[prices] {tk}: semilla desde price_history.csv ({px})")
        # Try to seed ^STOXX50E via chart API (quick one-shot)
        try:
            _sto_url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ESTOXX50E?interval=1d&range=1d"
            _sto_req = urllib.request.Request(_sto_url, headers={"User-Agent": "Mozilla/5.0"})
            _sto_resp = urllib.request.urlopen(_sto_req, timeout=10)
            _sto_chart = json.loads(_sto_resp.read())
            _sto_meta = _sto_chart.get("chart", {}).get("result", [{}])[0].get("meta", {})
            _sto_cur = _sto_meta.get("regularMarketPrice") or _sto_meta.get("chartPreviousClose")
            _sto_prev = _sto_meta.get("chartPreviousClose") or _sto_cur
            if _sto_cur is not None:
                data["^STOXX50E"] = {"current": float(_sto_cur), "prev_close": float(_sto_prev) if _sto_prev else None}
                print(f"[prices] ^STOXX50E: semilla desde chart API ({_sto_cur})")
        except Exception:
            print("[prices] ^STOXX50E: no se pudo obtener semilla via chart API")
        _save_live_prices(data)
        return data
    except Exception:
        return {}

def _save_live_prices(data):
    try:
        with open(LIVE_PRICES_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

_WL_CACHE = {"data": None, "updated": None}
_WL_TTL = 300  # 5 min

# Cache de calcular_trendline_lta()/calcular_media_movil_entrada() por ticker,
# compartida entre _compute_alertas, _compute_candidatos y _compute_watchlist_study.
# TTL largo porque ninguna de las dos cambia intradia -- evita descargar
# historico de yfinance (~2s/ticker) en cada request a /api/alertas (que no
# tiene cache propia). Caches separadas: un ticker puede tener entradas LT y
# MA a la vez (ej. RRU.DE), cada una con su propio calculo.
_TRENDLINE_LTA_CACHE = {}
_TRENDLINE_LTA_TTL = 6 * 3600  # 6h
_MEDIA_MOVIL_CACHE = {}
_MEDIA_MOVIL_TTL = 6 * 3600  # 6h

def _get_trendline_lta_cached(ticker):
    from screener import calcular_trendline_lta
    now = datetime.now()
    entry = _TRENDLINE_LTA_CACHE.get(ticker)
    if entry and (now - entry["updated"]).total_seconds() < _TRENDLINE_LTA_TTL:
        return entry["data"]
    data = calcular_trendline_lta(ticker)
    _TRENDLINE_LTA_CACHE[ticker] = {"data": data, "updated": now}
    return data

def _get_media_movil_cached(ticker):
    from screener import calcular_media_movil_entrada
    now = datetime.now()
    entry = _MEDIA_MOVIL_CACHE.get(ticker)
    if entry and (now - entry["updated"]).total_seconds() < _MEDIA_MOVIL_TTL:
        return entry["data"]
    data = calcular_media_movil_entrada(ticker)
    _MEDIA_MOVIL_CACHE[ticker] = {"data": data, "updated": now}
    return data

_ATR14_CACHE = {}
_ATR14_TTL = 6 * 3600  # 6h

def _get_atr14_cached(ticker):
    from screener import calcular_atr14
    now = datetime.now()
    entry = _ATR14_CACHE.get(ticker)
    if entry and (now - entry["updated"]).total_seconds() < _ATR14_TTL:
        return entry["data"]
    data = calcular_atr14(ticker)
    _ATR14_CACHE[ticker] = {"data": data, "updated": now}
    return data

def _fetch_precio_actual(ticker):
    """Precio actual via Yahoo chart API, mismo patron que el resto de
    endpoints de este fichero. Devuelve float o None."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        resp = urllib.request.urlopen(req, timeout=10)
        chart = json.loads(resp.read())
        meta = chart.get("chart", {}).get("result", [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        return float(price) if price is not None else None
    except Exception:
        return None

def _resolver_nivel_senal(item):
    """Para entradas LT/LTA/MA, resuelve el nivel de entrada operativo contra
    el calculo en vivo correspondiente (trendline o EMA20 semanal, ambos
    cacheados 6h), no el entry_level estatico de watchlist.json. entry_level
    se conserva solo como metadato informativo (cuando se detecto la senal
    por primera vez).
    Devuelve (nivel, fuente) donde fuente es 'trendline' | 'media_movil' |
    'manual_fallback' | 'fijo'. Para RR/RRA/otros, nivel = entry_level tal
    cual (fuente 'fijo')."""
    entry_level = item.get("entry_level")
    tipo = item.get("entry_signal") or ""
    tk = str(item.get("ticker") or "").strip()
    if tipo == "MA":
        try:
            ma = _get_media_movil_cached(tk)
        except Exception:
            ma = None
        if ma and ma.get("valido") and ma.get("valor_actual") is not None:
            return ma["valor_actual"], "media_movil"
        return entry_level, "manual_fallback"
    if tipo not in ("LT", "LTA"):
        return entry_level, "fijo"
    try:
        trend = _get_trendline_lta_cached(tk)
    except Exception:
        trend = None
    if trend and trend.get("valido") and trend.get("valor_actual") is not None:
        return trend["valor_actual"], "trendline"
    return entry_level, "manual_fallback"

# ========== ALERTAS STATE (dedup para n8n) ==========
# Estado separado de watchlist.json: solo alertado/fecha_ultima_alerta por ticker.
# Si un ticker de watchlist.json no tiene entrada aqui, se trata como alertado=false.
ALERTAS_STATE_FILE = os.path.join(_PROJ_DIR, "alertas_state.json")

def _load_alertas_state():
    try:
        with open(ALERTAS_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            return state if isinstance(state, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_alertas_state(state):
    try:
        with open(ALERTAS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def parsear_nav_espanol(nav_str):
    """Convierte valor liquidativo en formato espanol a float.
    '3.763,430000 EUR' -> 3763.43"""
    if not nav_str or not isinstance(nav_str, str):
        return None
    limpio = nav_str.strip()
    limpio = re.sub(r'\s*(EUR|€|USD|GBP)\s*$', '', limpio, flags=re.IGNORECASE).strip()
    if not limpio:
        return None
    if ',' in limpio:
        limpio = limpio.replace('.', '').replace(',', '.')
    elif '.' in limpio and len(limpio.split('.')) > 2:
        limpio = limpio.replace('.', '')
    try:
        return round(float(limpio), 4)
    except ValueError:
        return None

def extraer_nav_quefondos(isin):
    """Scrapea Quefondos por ISIN y devuelve (precio_unitario, fecha_str) o (None, None)."""
    url = f'https://www.quefondos.com/es/fondos/ficha/index.html?isin={isin}'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            soup = BeautifulSoup(resp.read(), 'html.parser')
    except Exception as e:
        print(f"[quefondos] Error HTTP/parseo para ISIN {isin}: {e}")
        return None, None
    # Buscar span.floatleft con "Valor liquidativo" -> siguiente span.floatright
    for fl in soup.find_all('span', class_='floatleft'):
        if 'Valor liquidativo' in fl.get_text():
            fr = fl.find_next_sibling('span', class_='floatright')
            if fr:
                precio = parsear_nav_espanol(fr.get_text(strip=True))
                if precio is not None:
                    from datetime import date
                    return precio, date.today().isoformat()
    print(f"[quefondos] No se encontro valor liquidativo para ISIN {isin}")
    return None, None


# ========== FONDOS INDEXADOS + CUENTA REMUNERADA ==========
_FONDOS_CACHE = {"data": None, "updated": None}
_FONDOS_TTL = 300  # 5 min (datos cambian solo cuando editas JSON)

_CUENTA_CACHE = {"data": None, "updated": None}
_CUENTA_TTL = 300  # 5 min

_CUENTA_MI_CACHE = {"data": None, "updated": None}
_CUENTA_MI_TTL = 300  # 5 min

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
_REGENERATE_LAST = 0.0
_REGENERATE_COOLDOWN = 300  # segundos entre regeneraciones
def regenerate():
    global _REGENERATE_LAST
    now = _ytime.time()
    if now - _REGENERATE_LAST < _REGENERATE_COOLDOWN:
        print(f"[regenerate] skipping — cooldown ({_REGENERATE_COOLDOWN}s)")
        return
    _REGENERATE_LAST = now
    def task():
        global LAST_REGENERATE
        try:
            r = subprocess.run(
                ["python", "generate_dashboard.py", "--skip-screener"],
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
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p) if qs else {}
            if "refresh" in params:
                _RADAR_CACHE["data"] = None
                _RADAR_CACHE["updated"] = None
                _RADAR_FORCE_REFRESH["flag"] = True
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
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p) if qs else {}
            if "refresh" in params:
                _WL_CACHE["data"] = None
                _WL_CACHE["updated"] = None
            self._send_json_cache(_WL_CACHE, _WL_TTL, self._compute_watchlist_study)
            return
        # API: Alertas para n8n (lee watchlist.json + alertas_state.json en caliente, no cache)
        if self.path.split("?")[0] == "/api/alertas":
            self._send_json(self._compute_alertas())
            return
        # API: Candidatos que cumplen el modelo, para n8n (watchlist.json + alertas_state.json
        # en caliente, no cache — mismo patron que /api/alertas)
        if self.path.split("?")[0] == "/api/candidatos":
            self._send_json(self._compute_candidatos())
            return
        # API: Fondos indexados (JSON local, sin fuentes externas)
        if self.path.startswith("/api/fondos"):
            self._send_json_cache(_FONDOS_CACHE, _FONDOS_TTL, self._compute_fondos)
            return
        # API: Cuenta MyInvestor (JSON local, debe ir ANTES que /api/cuenta-remunerada)
        if self.path.startswith("/api/cuenta-remunerada-myinvestor"):
            self._send_json_cache(_CUENTA_MI_CACHE, _CUENTA_MI_TTL, self._compute_cuenta_remunerada_myinvestor)
            return
        # API: Cuenta remunerada Trade Republic (JSON local, sin fuentes externas)
        if self.path.startswith("/api/cuenta-remunerada"):
            self._send_json_cache(_CUENTA_CACHE, _CUENTA_TTL, self._compute_cuenta_remunerada)
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
        # Redirect / → dashboard.html
        if self.path == "/" or self.path == "":
            self.send_response(302)
            self.send_header("Location", "/dashboard.html")
            self.end_headers()
            return
        # Auth check for static files
        if not check_auth(self.headers):
            return send_401(self)
        return super().do_GET()

    def do_POST(self):
        # Marcar ticker como alertado (dedup n8n). Body JSON: {"ticker": "XTN"}
        # Mismo handler y mismo alertas_state.json (dedup por ticker) para
        # /api/alertas y /api/candidatos: si un ticker ya se marco desde
        # cualquiera de los dos flujos, el otro tambien lo respeta.
        if self.path.startswith("/api/alertas/marcar") or self.path.startswith("/api/candidatos/marcar"):
            self._handle_alertas_marcar()
            return
        self.send_error(405, "Method Not Allowed")

    def _send_json_status(self, status, data):
        body = json.dumps(data, ensure_ascii=False)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _handle_alertas_marcar(self):
        """Marca un ticker como alertado (confirmacion) o vigilando (aviso de
        proximidad). Acepta JSON body {"ticker": "XTN"} o {"ticker": "XTN",
        "tipo": "vigilancia"}, o query params ?ticker=XTN&tipo=vigilancia.
        Sin "tipo" (o tipo distinto de "vigilancia") marca confirmacion,
        igual que antes. Idempotente. Los dos flags (alertado/vigilando) son
        independientes -- marcar uno no borra el otro."""
        ticker = None
        tipo = None
        error = None
        body_raw = b""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > 0:
                body_raw = self.rfile.read(length)
        except Exception:
            error = "Error leyendo body"
        if not error and body_raw:
            try:
                payload = json.loads(body_raw.decode("utf-8"))
                if isinstance(payload, dict):
                    ticker = str(payload.get("ticker") or "").strip()
                    tipo = payload.get("tipo")
                else:
                    error = "Body JSON debe ser un objeto"
            except (json.JSONDecodeError, ValueError):
                error = "Body JSON invalido"
        if not error and (not ticker or tipo is None):
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            import urllib.parse
            params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            if not ticker:
                ticker = urllib.parse.unquote(params.get("ticker", "")).strip()
            if tipo is None and "tipo" in params:
                tipo = urllib.parse.unquote(params["tipo"])
        if not error and not ticker:
            error = "Falta el campo 'ticker'"
        if error:
            self._send_json_status(400, {"ok": False, "error": error})
            return
        wl_path = os.path.join(DIR, "watchlist.json")
        valid = False
        try:
            if os.path.exists(wl_path):
                with open(wl_path, "r", encoding="utf-8") as f:
                    valid = any(str(i.get("ticker")) == ticker for i in json.load(f))
        except Exception:
            valid = False
        if not valid:
            self._send_json_status(404, {"ok": False, "error": f"Ticker '{ticker}' no está en watchlist.json"})
            return
        state = _load_alertas_state()
        now_iso = datetime.now().isoformat()
        entry = state.get(ticker, {}) if isinstance(state, dict) else {}
        if tipo == "vigilancia":
            entry["vigilando"] = True
            entry["fecha_ultima_vigilancia"] = now_iso
            state[ticker] = entry
            _save_alertas_state(state)
            print(f"[alertas] marcar {ticker} -> vigilando (fecha {now_iso})")
            self._send_json({"ok": True, "ticker": ticker, "vigilando": True, "fecha_ultima_vigilancia": now_iso})
        else:
            entry["alertado"] = True
            entry["fecha_ultima_alerta"] = now_iso
            state[ticker] = entry
            _save_alertas_state(state)
            print(f"[alertas] marcar {ticker} -> alertado (fecha {now_iso})")
            self._send_json({"ok": True, "ticker": ticker, "alertado": True, "fecha_ultima_alerta": now_iso})

    def _compute_alertas(self):
        """Estado de señales para n8n: entrada activa = toda entrada de watchlist.json.
        Lee ambos JSON en caliente por request (server.py es proceso vivo e independiente
        del build estático de generate_dashboard.py). Para LT/LTA, precio_trigger
        viene de la trendline recalculada en vivo (cacheada 6h, ver _resolver_nivel_senal),
        no de entry_level -- eso solo se expone como entry_level_detectado, informativo.
        requiere_cierre_semanal = true para entradas RR/RRA (se confirman en cierre
        semanal del viernes según la metodología), o para cualquier entrada con
        "requiere_cierre_semanal_manual": true en watchlist.json (p.ej. una LTA
        que también exige confirmación de cierre semanal)."""
        try:
            wl_path = os.path.join(DIR, "watchlist.json")
            if not os.path.exists(wl_path):
                return {"error": True, "msg": "watchlist.json no encontrado", "items": []}
            with open(wl_path, "r", encoding="utf-8") as f:
                watchlist = json.load(f)
            state = _load_alertas_state()
            state_dirty = False
            items = []
            for item in watchlist:
                tk = str(item.get("ticker") or "").strip()
                if not tk:
                    continue
                tipo = item.get("entry_signal") or ""
                nivel, nivel_fuente = _resolver_nivel_senal(item)
                st = state.get(tk, {}) if isinstance(state, dict) else {}
                alertado = bool(st.get("alertado", False))
                vigilando = bool(st.get("vigilando", False))

                # Aviso de proximidad (solo LT/LTA/MA, nivel dinamico): banda
                # de 1xATR14 alrededor del nivel resuelto en vivo. "confirmada"
                # una vez ya disparo la alerta real; "vigilar" si esta dentro
                # de la banda sin confirmar todavia; None si esta lejos.
                estado_lta = None
                atr_val = None
                if tipo in ("LT", "LTA", "MA"):
                    if alertado:
                        estado_lta = "confirmada"
                    elif nivel is not None:
                        atr_info = _get_atr14_cached(tk)
                        atr_val = atr_info.get("atr")
                        cur_price = _fetch_precio_actual(tk)
                        if cur_price is not None and atr_info.get("valido"):
                            if abs(cur_price - nivel) <= atr_val:
                                estado_lta = "vigilar"
                            elif vigilando:
                                # salio de la banda sin confirmar: resetea el
                                # flag para poder volver a avisar si se acerca de nuevo
                                vigilando = False
                                state.setdefault(tk, {})
                                state[tk]["vigilando"] = False
                                state_dirty = True

                items.append({
                    "ticker": tk,
                    "tag": item.get("theme") or "",
                    "tipo_entrada": tipo,
                    "precio_trigger": nivel,
                    "entry_level_detectado": item.get("entry_level"),
                    "nivel_fuente": nivel_fuente,
                    "precio_soporte": item.get("support"),
                    "requiere_cierre_semanal": tipo in ("RR", "RRA") or bool(item.get("requiere_cierre_semanal_manual")),
                    "alertado": alertado,
                    "fecha_ultima_alerta": st.get("fecha_ultima_alerta"),
                    "estado_lta": estado_lta,
                    "atr14": atr_val,
                    "vigilando": vigilando,
                    "fecha_ultima_vigilancia": st.get("fecha_ultima_vigilancia"),
                })
            if state_dirty:
                _save_alertas_state(state)
            return {"items": items, "updated": datetime.now().isoformat()}
        except Exception as e:
            print(f"[alertas] ERROR: {e}")
            return {"error": True, "msg": str(e), "items": []}

    def _send_json_cache(self, cache, ttl, compute_fn):
        ERROR_TTL = min(ttl, 900)  # 15 min max for errors (respects short TTLs like prices=120s)
        now = datetime.now()
        if cache["data"] and cache["updated"]:
            effective_ttl = cache.get("_error_ttl", ttl)
            age = (now - datetime.fromisoformat(cache["updated"])).total_seconds()
            if age < effective_ttl:
                self._send_json(cache["data"])
                return
        try:
            result = compute_fn()
            cache["data"] = result
            cache["updated"] = now.isoformat()
            if "_error_ttl" in cache:
                del cache["_error_ttl"]
            if isinstance(result, dict) and result.get("error"):
                cache["_error_ttl"] = ERROR_TTL
            elif isinstance(result, dict) and result.get("degraded"):
                cache["_error_ttl"] = ERROR_TTL
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
        def _clean(obj):
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_clean(v) for v in obj]
            if isinstance(obj, float) and (obj != obj or obj == float('inf') or obj == float('-inf')):
                return None
            return obj
        self.wfile.write(json.dumps(_clean(data), default=str).encode())

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
        extra = CFG.get("screener", {}).get("extra_sectors", [])
        unique_sectors = sorted(set(list(unique_sectors) + extra))
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
            force = _RADAR_FORCE_REFRESH.get("flag", False)
            empresas = ejecutar_radar(max_resultados=15, force_refresh=force)
            if force:
                _RADAR_FORCE_REFRESH["flag"] = False
            result = {
                "oportunidades": [
                    {
                        "ticker": r["ticker"],
                        "name": r["name"],
                        "score": r["score"],
                        "eper": r["eper"],
                        "peg": None if (r.get("peg") is None or (isinstance(r.get("peg"), float) and r.get("peg") != r.get("peg"))) else r.get("peg"),
                        "current_price": r.get("current_price"),
                        "rent_1a": r.get("rent_1a"),
                        "entry_types": r.get("entry_types", []),
                        "support": r.get("support"),
                        "resistance": r.get("resistance"),
                        "fwd_per": self._radar_per_futuro(r["ticker"], "fwd_per"),
                        "fuente_per": self._radar_per_futuro(r["ticker"], "fuente_per"),
                        "fuente_peg": self._radar_per_futuro(r["ticker"], "fuente_peg"),
                        "rev_growth": self._radar_per_futuro(r["ticker"], "rev_growth"),
                        "fuente_rev": self._radar_per_futuro(r["ticker"], "fuente_rev"),
                    }
                    for r in empresas
                ],
                "total": len(empresas),
                "updated": datetime.now().isoformat(),
            }
            # Si el cálculo vino de datos con PER/PEG null (yfinance .info falló),
            # trata como degradado para no cachear 24h basura.
            vals = [o.get("eper") for o in result["oportunidades"]]
            if result["oportunidades"] and all(v is None for v in vals):
                result["degraded"] = True
            return result
        except Exception as e:
            print(f"[radar] ERROR: {e}")
            return {"error": True, "msg": str(e), "oportunidades": [], "total": 0}

    def _wl_peg(self, ticker):
        """PEG ratio for a watchlist ticker (None si no hay dato)."""
        try:
            from screener import get_valuation
            return get_valuation(ticker).get("peg")
        except Exception:
            return None

    def _radar_per_futuro(self, ticker, key):
        """Campo de get_per_futuro para el radar, con cache en memoria."""
        if not hasattr(self, "_radar_pfu_cache"):
            self._radar_pfu_cache = {}
        if ticker not in self._radar_pfu_cache:
            self._radar_pfu_cache[ticker] = get_per_futuro(ticker)
        return self._radar_pfu_cache[ticker].get(key)

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
                entry_signal = item["entry_signal"]
                entry_level, nivel_fuente = _resolver_nivel_senal(item)
                entry_level_detectado = item.get("entry_level")
                proximity_entry = item.get("proximity_entry", False)
                proximity_pct = item.get("proximity_pct", 5)
                # 1) Current price via chart API
                cur_price = None
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=1d&range=1d"
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
                # 3) Support: manual from JSON if provided, else computed
                support_val = item.get("support")
                support_ok = False
                if support_val is not None:
                    try:
                        support_val = float(support_val)
                        sv, _, _, sok = calcular_soporte_resistencia(tk)
                        support_ok = sok
                    except Exception:
                        pass
                else:
                    try:
                        sv, _, _, sok = calcular_soporte_resistencia(tk)
                        support_val = sv
                        support_ok = sok
                    except Exception:
                        pass
                # 4) Proximity mode vs standard mode
                if proximity_entry:
                    signal_active = False
                    dist_to_support = ((cur_price - support_val) / support_val) * 100 if cur_price and support_val else None
                    proximo = dist_to_support is not None and dist_to_support <= proximity_pct
                    weekly_ok = False
                    if proximo:
                        try:
                            weekly = yf.Ticker(tk).history(period="2y", interval="1wk")
                            if weekly is not None and not weekly.empty and len(weekly) >= 22:
                                c = weekly['Close'].values; h = weekly['High'].values; v = weekly['Volume'].values
                                ld = weekly.index[-1].to_pydatetime() if hasattr(weekly.index[-1], 'to_pydatetime') else weekly.index[-1]
                                hrs = (datetime.now(timezone.utc) - ld).total_seconds() / 3600
                                ref = -2 if hrs < 48 else -1
                                if abs(ref) + 22 <= len(weekly):
                                    vs = max(0, ref - 20); vm = float(sum(v[vs:ref]) / (ref - vs))
                                    weekly_ok = v[ref] > vm
                        except Exception:
                            pass
                    f1_ok = f2_ok = f3_ok = weekly_ok
                    if proximo and support_ok and weekly_ok:
                        visual_status = "confirmado"
                    elif proximo:
                        visual_status = "activa"
                    else:
                        visual_status = "sin_senal"
                else:
                    # 4a) Entry types from screener
                    detected_types = get_entry_types(tk)
                    signal_active = entry_signal in detected_types
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
                    "entry_level_detectado": entry_level_detectado,
                    "nivel_fuente": nivel_fuente,
                    "entry_signal": entry_signal,
                    "current_price": cur_price,
                    "distance_pct": round(dist_pct, 2) if dist_pct is not None else None,
                    "detected_types": detected_types if not proximity_entry else [],
                    "signal_active": signal_active,
                    "proximity_entry": proximity_entry,
                    "proximity_pct": proximity_pct if proximity_entry else None,
                    "support": support_val,
                    "support_ok": support_ok,
                    "stop": item.get("stop", support_val),
                    "f1_ok": f1_ok,
                    "f2_ok": f2_ok,
                    "f3_ok": f3_ok,
                    "visual_status": visual_status,
                    "theme": item.get("theme", ""),
                    "peg": self._wl_peg(tk),
                    "fwd_per": self._radar_per_futuro(tk, "fwd_per"),
                    "fuente_per": self._radar_per_futuro(tk, "fuente_per"),
                    "fuente_peg": self._radar_per_futuro(tk, "fuente_peg"),
                    "rev_growth": self._radar_per_futuro(tk, "rev_growth"),
                    "fuente_rev": self._radar_per_futuro(tk, "fuente_rev"),
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

    def _compute_candidatos(self):
        """Listado de tickers de watchlist.json que cumplen (o estan cerca
        de cumplir) el modelo de inversion, para que n8n lo consuma igual
        que /api/alertas. Deliberadamente NO reutiliza _compute_watchlist_study():
        esa funcion tiene el efecto lateral de mandar email cuando cambia el
        estado visual (_wl_check_alerts) y no queremos disparar eso desde
        aqui, asi que recalcula precio/soporte/distancia con las mismas
        funciones de base (get_entry_types, calcular_soporte_resistencia).

        Deuda neta/EBITDA solo se lee de deuda_ebitda_cache.json (nunca se
        recalcula aqui: yfinance .info esta bloqueado en Render, el cache
        se rellena en local via generate_dashboard.py)."""
        from screener import get_entry_types, calcular_soporte_resistencia, obtener_fundamentales, get_valuation, normalized_score
        from position_sizing import calcular_tamano_posicion
        from deuda_ebitda import get_deuda_neta_ebitda_cacheada
        try:
            wl_path = os.path.join(DIR, "watchlist.json")
            if not os.path.exists(wl_path):
                return {"error": True, "msg": "watchlist.json no encontrado", "items": []}
            with open(wl_path, "r", encoding="utf-8") as f:
                watchlist = json.load(f)
            state = _load_alertas_state()

            SECTOR_TO_THEME = CFG.get("temas_exposicion", {}).get("sector_to_theme", {})
            THEMES_CFG = CFG.get("temas_exposicion", {}).get("themes", {})
            portfolio_theme_counts = {}
            for p in CFG.get("portfolio", []):
                tema = p.get("tema_exposicion", "N/D")
                portfolio_theme_counts[tema] = portfolio_theme_counts.get(tema, 0) + 1

            items = []
            for item in watchlist:
                tk = str(item.get("ticker") or "").strip()
                if not tk:
                    continue
                entry_signal = item.get("entry_signal") or ""
                entry_level, nivel_fuente = _resolver_nivel_senal(item)
                entry_level_detectado = item.get("entry_level")

                cur_price = None
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=1d&range=1d"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                    resp = urllib.request.urlopen(req, timeout=10)
                    chart = json.loads(resp.read())
                    meta = chart.get("chart", {}).get("result", [{}])[0].get("meta", {})
                    cur_price = meta.get("regularMarketPrice")
                    if cur_price is not None:
                        cur_price = float(cur_price)
                except Exception:
                    pass
                distancia_pct = ((cur_price - entry_level) / entry_level) * 100 if (cur_price and entry_level) else None

                support_val = item.get("support")
                if support_val is not None:
                    try:
                        support_val = float(support_val)
                    except Exception:
                        support_val = None
                if support_val is None:
                    try:
                        sv, _, _, sok = calcular_soporte_resistencia(tk)
                        support_val = sv if sok else None
                    except Exception:
                        support_val = None

                # "stop" es el nivel operativo de riesgo (puede ser distinto del
                # soporte técnico, p.ej. soporte menos margen). Si watchlist.json
                # no lo especifica, se usa el soporte como stop (comportamiento
                # previo, sin cambios para entradas existentes).
                stop_val = item.get("stop")
                if stop_val is not None:
                    try:
                        stop_val = float(stop_val)
                    except Exception:
                        stop_val = None
                if stop_val is None:
                    stop_val = support_val

                try:
                    detected_types = get_entry_types(tk)
                except Exception:
                    detected_types = []
                signal_active = entry_signal in detected_types

                fund = obtener_fundamentales(tk) or {}
                roe, eva, fcf, roic = fund.get("roe"), fund.get("eva"), fund.get("fcf"), fund.get("roi")

                try:
                    valuation = get_valuation(tk)
                    per_ttm = valuation.get("per")
                    pb = valuation.get("pb")
                except Exception:
                    per_ttm = None
                    pb = None
                pfu = get_per_futuro(tk)
                peg = pfu.get("peg")

                deuda_cache = get_deuda_neta_ebitda_cacheada(tk)
                deuda_ratio = deuda_cache.get("deuda_neta_ebitda") if deuda_cache else None

                if entry_level and stop_val:
                    tamano = calcular_tamano_posicion(entry_level, stop_val, capital_sistema=10000)
                else:
                    tamano = {"riesgo_eur": None, "distancia_stop_pct": None, "inversion_maxima_eur": None, "num_acciones_max": None}

                warnings = []
                if deuda_ratio is None:
                    warnings.append("Deuda neta/EBITDA no disponible (solo se calcula en la generación local del dashboard)")
                elif deuda_ratio > 3.5:
                    warnings.append(f"Deuda neta/EBITDA en zona de precaución ({deuda_ratio}x)")
                elif deuda_ratio > 2:
                    warnings.append(f"Deuda neta/EBITDA a vigilar ({deuda_ratio}x)")
                if roic is not None and roe is not None and roe > 0 and (roic / roe) < 0.5:
                    warnings.append(f"ROIC ({roic}%) muy por debajo de ROE ({roe}%) — posible apalancamiento")
                if peg is not None and peg > 2:
                    warnings.append(f"PEG caro ({peg}x)")
                if pb is not None and pb > 5:
                    warnings.append(f"P/B elevado ({pb:.2f}x)")
                tema_candidato = SECTOR_TO_THEME.get(fund.get("sector", ""))
                if tema_candidato:
                    count = portfolio_theme_counts.get(tema_candidato, 0)
                    thresh = THEMES_CFG.get(tema_candidato, {}).get("umbral_concentracion", 2)
                    if count >= thresh:
                        warnings.append(f'Concentración temática: ya hay {count} posiciones en "{tema_candidato}"')
                if item.get("notes"):
                    warnings.append(item["notes"])

                st = state.get(tk, {}) if isinstance(state, dict) else {}
                items.append({
                    "ticker": tk,
                    "name": item.get("name", tk),
                    "tipo_senal": entry_signal,
                    "entry_level": entry_level,
                    "entry_level_detectado": entry_level_detectado,
                    "nivel_fuente": nivel_fuente,
                    "stop": stop_val,
                    "support": support_val,
                    "distancia_pct": round(distancia_pct, 2) if distancia_pct is not None else None,
                    "signal_active": signal_active,
                    "detected_types": detected_types,
                    "roe": roe, "eva": eva, "fcf": fcf, "roic": roic,
                    "_score_inputs_ok": roe is not None and eva is not None and fcf is not None,
                    "per_ttm": per_ttm, "per_futuro": pfu.get("fwd_per"), "peg": peg, "pb": pb,
                    "deuda_neta_ebitda": deuda_ratio,
                    "tamano_sugerido": tamano,
                    "warnings": warnings,
                    "alertado": bool(st.get("alertado", False)),
                    "fecha_ultima_alerta": st.get("fecha_ultima_alerta"),
                })

            # Score Eurekers (ROE 50% / EVA 25% / FCF 25%) relativo dentro de la
            # watchlist, reutilizando normalized_score() de screener.py — no es
            # comparable con el score del radar de universo completo.
            validos = [it for it in items if it["_score_inputs_ok"]]
            if validos:
                n_roe = normalized_score(pd.Series([it["roe"] for it in validos]))
                n_eva = normalized_score(pd.Series([it["eva"] for it in validos]))
                n_fcf = normalized_score(pd.Series([it["fcf"] for it in validos]))
                for it, r, e, fcv in zip(validos, n_roe, n_eva, n_fcf):
                    it["score_watchlist"] = round(float(r * 0.5 + e * 0.25 + fcv * 0.25), 3)
            for it in items:
                it.setdefault("score_watchlist", None)
                del it["_score_inputs_ok"]

            items.sort(key=lambda it: (it["distancia_pct"] is None, abs(it["distancia_pct"]) if it["distancia_pct"] is not None else 0))
            return {"items": items, "updated": datetime.now().isoformat()}
        except Exception as e:
            print(f"[candidatos] ERROR: {e}")
            return {"error": True, "msg": str(e), "items": []}

    def _compute_fondos(self):
        """Lee fondos_indexados.json, actualiza precios via scraping/yfinance, calcula valor_actual."""
        from datetime import date
        hoy = date.today().isoformat()
        result = {"fondos": [], "radar": None}
        write_back = False
        try:
            path = os.path.join(DIR, "fondos_indexados.json")
            if not os.path.exists(path):
                return {"error": True, "msg": "JSON no encontrado"}
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for fdo in data.get("fondos", []):
                nombre = fdo.get("nombre", "")
                isin = fdo.get("isin", "")
                tipo = fdo.get("tipo", "")
                aportado = fdo.get("aportado", 0) or 0
                participaciones = fdo.get("participaciones", 1) or 1
                ter = fdo.get("ter", 0) or 0
                fecha_act = fdo.get("fecha_actualizacion", "")
                historico = fdo.get("historico_prices", [])
                precio_unitario = None
                nueva_fecha = fecha_act
                # Intentar obtener precio si no esta actualizado hoy
                if fecha_act < hoy:
                    if tipo == "etf":
                        ticker = fdo.get("ticker", "")
                        if ticker:
                            precio_unitario = None
                            try:
                                _sess = getattr(self, '_fondo_session', None)
                                if _sess is None:
                                    import requests as _req
                                    _sess = _req.Session()
                                    _sess.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                                    self._fondo_session = _sess
                                info = yf.Ticker(ticker, session=_sess).info or {}
                                cur = info.get("regularMarketPrice") or info.get("previousClose") or info.get("currentPrice")
                                if cur is not None:
                                    precio_unitario = float(cur)
                                    if precio_unitario and precio_unitario > 0:
                                        print(f"[fondos] {nombre} ({ticker}): yfinance -> {precio_unitario}")
                            except Exception as e:
                                print(f"[fondos] yfinance .info fallo para {ticker}: {e}")
                            # Fallback: history(period='5d') cuando .info no da precio valido
                            # (mismo patron que _yf_hist_fallback para precios de cartera)
                            if precio_unitario is None:
                                try:
                                    _h = yf.Ticker(ticker, session=_sess).history(period="5d", auto_adjust=False)
                                    if _h is not None and not _h.empty:
                                        _cl = [ll for ll in _h["Close"].tolist() if ll is not None]
                                        if _cl and _cl[-1] is not None and _cl[-1] > 0:
                                            precio_unitario = float(_cl[-1])
                                            print(f"[fondos] {nombre} ({ticker}): history fallback -> {precio_unitario}")
                                except Exception as e:
                                    print(f"[fondos] yfinance history fallback fallo para {ticker}: {e}")
                            if precio_unitario is not None and not (precio_unitario > 0):
                                precio_unitario = None
                    elif tipo == "fondo_no_cotizado" and _HAS_BS4:
                        precio_unitario, _ = extraer_nav_quefondos(isin)
                        if precio_unitario is not None:
                            print(f"[fondos] {nombre} ({isin}): Quefondos -> {precio_unitario}")
                        else:
                            print(f"[fondos] Quefondos fallo para {nombre} ({isin}), usando fallback cache")
                    # Fallback a ultimo precio cacheado en historico_prices
                    if precio_unitario is None:
                        if historico:
                            ultimo = max(historico, key=lambda x: x["fecha"])
                            precio_unitario = ultimo["precio"]
                            nueva_fecha = ultimo["fecha"]
                            print(f"[fondos] Fallback cache para {nombre}: precio={precio_unitario} ({nueva_fecha})")
                        else:
                            print(f"[fondos] Sin cache para {nombre}, usando valor_actual del JSON")
                            precio_unitario = round(fdo.get("valor_actual", 0) / participaciones, 4) if participaciones else 0
                    else:
                        # Precio obtenido con exito hoy -> persistir en JSON
                        nueva_fecha = hoy
                        historico.append({"fecha": hoy, "precio": precio_unitario})
                        fdo["historico_prices"] = historico
                        write_back = True
                else:
                    # Ya actualizado hoy, usar valor_actual del JSON
                    precio_unitario = round(fdo.get("valor_actual", 0) / participaciones, 4) if participaciones else 0
                valor_actual = round(precio_unitario * participaciones, 2) if precio_unitario else 0
                rent = ((valor_actual - aportado) / aportado * 100) if aportado else 0
                aportado_fecha = fdo.get("aportado_fecha", "")
                rent_real = None
                if aportado_fecha and aportado > 0:
                    inf = inflacion_acumulada(aportado_fecha, hoy)
                    if inf is not None:
                        rent_real = round(rent - inf * 100, 2)
                result["fondos"].append({
                    "nombre": nombre,
                    "isin": isin,
                    "tipo": tipo,
                    "aportado": aportado,
                    "aportado_fecha": aportado_fecha,
                    "valor_actual": valor_actual,
                    "fecha_actualizacion": nueva_fecha,
                    "ter": ter,
                    "rentabilidad": round(rent, 2),
                    "rentabilidad_real": rent_real,
                    "historico_prices": historico[-60:],
                })
            result["total_fondos"] = round(sum(f["valor_actual"] for f in result["fondos"]), 2)
            # Persistir cambios si se obtuvieron nuevos precios
            if write_back:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    print(f"[fondos] JSON actualizado con nuevos precios ({hoy})")
                except Exception as e:
                    print(f"[fondos] Error al escribir JSON: {e}")
            # Radar comparativo (opcional)
            radar_path = os.path.join(DIR, "fondos_comparativa.json")
            if os.path.exists(radar_path):
                with open(radar_path, "r", encoding="utf-8") as f:
                    result["radar"] = json.load(f)
        except Exception as e:
            print(f"[fondos] Error general: {e}")
            return {"error": True, "msg": str(e)}
        return result

    def _compute_cuenta_remunerada(self):
        """Lee cuenta_remunerada.json, último saldo conocido con fallback, interés diario estimado."""
        from datetime import date
        try:
            path = os.path.join(DIR, "cuenta_remunerada.json")
            if not os.path.exists(path):
                return {"error": True, "msg": "JSON no encontrado"}
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Último saldo conocido con fallback
            hoy = date.today()
            ultimo = max(data["historico_saldos"], key=lambda x: x["fecha"])
            ultima_fecha = date.fromisoformat(ultimo["fecha"])
            saldo_actual = ultimo["saldo"]
            saldo_desactualizado = ultima_fecha < hoy
            # Interés diario estimado (solo informativo, no alimenta KPI)
            interes_diario = round(saldo_actual * (data["tae_actual"] / 100) / 365, 2)
            # Interés acumulado del mes actual
            primer_dia_mes = hoy.replace(day=1)
            fecha_inicio = date.fromisoformat(data["fecha_inicio_tracking"]) if data.get("fecha_inicio_tracking") else hoy
            inicio_mes_efectivo = max(primer_dia_mes, fecha_inicio)
            dias_mes = (hoy - inicio_mes_efectivo).days + 1
            interes_mes_actual = round(interes_diario * dias_mes, 2)
            fecha_str = ultima_fecha.strftime("%d/%m/%Y")
            result = {
                "entidad": data.get("entidad", ""),
                "saldo_actual": saldo_actual,
                "saldo_desactualizado": saldo_desactualizado,
                "fecha_ultima_actualizacion": fecha_str,
                "fecha_ultima_actualizacion_iso": ultimo["fecha"],
                "tae_actual": data["tae_actual"],
                "interes_diario_estimado": interes_diario,
                "interes_mes_actual": interes_mes_actual,
                "intereses_acumulados_periodo": data.get("intereses_acumulados_periodo", 0),
                "fecha_inicio_tracking": data.get("fecha_inicio_tracking", ""),
                "historico_saldos": data.get("historico_saldos", []),
            }
            return result
        except Exception as e:
            return {"error": True, "msg": str(e)}

    def _compute_cuenta_remunerada_myinvestor(self):
        """Lee cuenta_remunerada_myinvestor.json, mismo patrón que Trade Republic."""
        from datetime import date
        try:
            path = os.path.join(DIR, "cuenta_remunerada_myinvestor.json")
            if not os.path.exists(path):
                return {"error": True, "msg": "JSON MyInvestor no encontrado"}
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            hoy = date.today()
            ultimo = max(data["historico_saldos"], key=lambda x: x["fecha"])
            ultima_fecha = date.fromisoformat(ultimo["fecha"])
            saldo_actual = ultimo["saldo"]
            saldo_desactualizado = ultima_fecha < hoy
            interes_diario = round(saldo_actual * (data["tae_actual"] / 100) / 365, 2)
            primer_dia_mes = hoy.replace(day=1)
            fecha_inicio = date.fromisoformat(data["fecha_inicio_tracking"]) if data.get("fecha_inicio_tracking") else hoy
            inicio_mes_efectivo = max(primer_dia_mes, fecha_inicio)
            dias_mes = (hoy - inicio_mes_efectivo).days + 1
            interes_mes_actual = round(interes_diario * dias_mes, 2)
            fecha_str = ultima_fecha.strftime("%d/%m/%Y")
            result = {
                "entidad": data.get("entidad", ""),
                "saldo_actual": saldo_actual,
                "saldo_desactualizado": saldo_desactualizado,
                "fecha_ultima_actualizacion": fecha_str,
                "fecha_ultima_actualizacion_iso": ultimo["fecha"],
                "tae_actual": data["tae_actual"],
                "interes_diario_estimado": interes_diario,
                "interes_mes_actual": interes_mes_actual,
                "intereses_acumulados_periodo": data.get("intereses_acumulados_periodo", 0),
                "fecha_inicio_tracking": data.get("fecha_inicio_tracking", ""),
                "historico_saldos": data.get("historico_saldos", []),
            }
            return result
        except Exception as e:
            return {"error": True, "msg": str(e)}

    def _compute_prices(self):
        try:
            import yfinance as yf
            import requests as _req, concurrent.futures
            _sess = _req.Session()
            _sess.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

            def _chart_cur(tk):
                """Fetch current price from Yahoo chart API (more real-time than .info). Returns float or None."""
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=1d&range=1d"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                    resp = urllib.request.urlopen(req, timeout=10)
                    chart = json.loads(resp.read())
                    meta = chart.get("chart", {}).get("result", [{}])[0].get("meta", {})
                    rmp = meta.get("regularMarketPrice")
                    return float(rmp) if rmp is not None else None
                except Exception:
                    return None

            def _chart_quote_and_prev(tk):
                """(current, prev_close) desde chart API range=5d.
                prev_close = ultima vela diaria COMPLETA anterior a la actual.
                Yahoo a veces devuelve velas con close=None (dias con gap, ej. feriados
                parciales o datos perdidos) — se filtran para no usar un cierre viejo
                como 'ayer' (daba day_var inflado). Devuelve None si no hay datos."""
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=1d&range=5d"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                    resp = urllib.request.urlopen(req, timeout=10)
                    chart = json.loads(resp.read())
                    res = chart.get("chart", {}).get("result", [{}])
                    if not res:
                        return None
                    meta = res[0].get("meta", {})
                    closes = res[0].get("indicators", {}).get("quote", [{}])[0].get("close", []) or []
                    valid = [c for c in closes if c is not None]
                    cur = meta.get("regularMarketPrice")
                    if cur is None and valid:
                        cur = valid[-1]
                    prev_close = None
                    if len(valid) >= 2:
                        prev_close = valid[-2]
                    if cur is None:
                        return None
                    return float(cur), (float(prev_close) if prev_close is not None else None)
                except Exception:
                    return None

            def _get_hist_prev_close(tk, sess):
                """Cierre del ultimo dia completo via intradia (fallback cuando Yahoo pierde dias)."""
                try:
                    _h = yf.Ticker(tk, session=sess).history(interval="1h", period="5d")
                    if len(_h) < 2:
                        return None
                    _today_d = datetime.now().date()
                    _i = len(_h) - 1
                    while _i >= 0 and _h.index[_i].date() == _today_d:
                        _i -= 1
                    return float(_h.iloc[_i]["Close"]) if _i >= 0 else None
                except Exception:
                    return None

            def _yf_hist_fallback(tk):
                """(cur, prev_close) via yfinance.history(period='5d').
                Fallback cuando el chart API crudo devuelve 404 (bloqueo anti-bot de Yahoo).
                yfinance usa cookie+crumb, por lo que es mas fiable desde Render.
                prev_close = ultima vela diaria COMPLETA anterior a la actual (close[-2])."""
                try:
                    _h = yf.Ticker(tk, session=_sess).history(period="5d", auto_adjust=False)
                    if _h is None or _h.empty:
                        return None
                    _cl = [ll for ll in _h["Close"].tolist() if ll is not None]
                    if not _cl:
                        return None
                    _cur = float(_cl[-1])
                    _prev = float(_cl[-2]) if len(_cl) >= 2 else _cur
                    return _cur, _prev
                except Exception:
                    return None

            def fetch_ticker_price(tk):
                # NVD.DE: AV en 10:00 CET y 17:30 CET, luego NASDAQ+FX (coincide con Degiro)
                if tk == "NVD.DE":
                    try:
                        import pytz
                        _now_dt = datetime.now(pytz.timezone("Europe/Madrid"))
                        now_hour = _now_dt.hour
                        now_min = _now_dt.minute
                    except Exception:
                        now_hour = 0
                        now_min = 0
                    # Ventanas AV: 10:00 CET (apertura Xetra+1h) y 17:30 CET (cierre Xetra)
                    _av_window = (now_hour == 10) or (now_hour == 17 and now_min >= 30)
                    if _av_window:
                        av_result = _fetch_alpha_vantage_nvda_price()
                        if av_result:
                            av_cur, av_prev = av_result
                            av_dv = round(av_cur - av_prev, 2)
                            print(f"[prices] {tk}: Alpha Vantage -> cur={av_cur} prev={av_prev} day_var={av_dv}")
                            return (tk, {"current": av_cur, "prev_close": av_prev, "day_var": av_dv})
                        print(f"[prices] {tk}: Alpha Vantage falló, fallback a NASDAQ+FX")
                    # Current price from chart API (real-time) -> .info fallback
                    _cur = _chart_cur("NVD.DE")
                    if _cur is None:
                        _nd_info = yf.Ticker("NVD.DE", session=_sess).info or {}
                        _cur = _nd_info.get("regularMarketPrice") or _nd_info.get("previousClose") or _nd_info.get("currentPrice")
                    # Prev close from NVDA NASDAQ hist + USDEUR
                    try:
                        _nvda_hist = yf.Ticker("NVDA", session=_sess).history(period="5d")
                        if _cur is not None and len(_nvda_hist) >= 2:
                            _last_date = _nvda_hist.index[-1].date()
                            _today = __import__('datetime').datetime.now().date()
                            if _last_date == _today:
                                _nvda_prev_usd = float(_nvda_hist["Close"].iloc[-2])
                            else:
                                _nvda_prev_usd = float(_nvda_hist["Close"].iloc[-1])
                            _fx_info = yf.Ticker("USDEUR=X", session=_sess).info or {}
                            _fr = _fx_info.get("regularMarketPrice") or _fx_info.get("previousClose")
                            if _fr:
                                _cur = float(_cur); _pe = round(_nvda_prev_usd * float(_fr), 2); _dv = round(_cur - _pe, 2)
                                print(f"[prices] {tk}: NASDAQ+FX -> cur={_cur} prev(NVDA*USDEUR)={_pe} dv={_dv}")
                                return (tk, {"current": _cur, "prev_close": _pe, "day_var": _dv})
                    except Exception as e:
                        print(f"[prices] {tk}: NASDAQ+FX error: {e}")
                    print(f"[prices] {tk}: NASDAQ+FX falló, fallback a yfinance Xetra")
                # RRU.DE: RR.L + GBPEUR (Degiro usa precio actual de RR.L, no hist)
                if tk == "RRU.DE":
                    # Current price from chart API (real-time) -> .info fallback
                    _cur = _chart_cur("RRU.DE")
                    if _cur is None:
                        _rru_info = yf.Ticker("RRU.DE", session=_sess).info or {}
                        _cur = _rru_info.get("regularMarketPrice") or _rru_info.get("previousClose") or _rru_info.get("currentPrice")
                    # Prev close from RR.L historical close * GBPEUR
                    try:
                        _rrl_prev_gbp = _get_hist_prev_close("RR.L", _sess)
                        _gfx_info = yf.Ticker("GBPEUR=X", session=_sess).info or {}
                        _gfr = _gfx_info.get("regularMarketPrice") or _gfx_info.get("previousClose")
                        if _cur and _rrl_prev_gbp and _gfr:
                            _cur = float(_cur); _pe = round(float(_rrl_prev_gbp) / 100 * float(_gfr), 3); _dv = round(_cur - _pe, 2)
                            print(f"[prices] {tk}: RR.L+FX -> cur={_cur} prev={_pe} (RR.L hist={_rrl_prev_gbp} * GBPEUR={_gfr}) dv={_dv}")
                            return (tk, {"current": _cur, "prev_close": _pe, "day_var": _dv})
                        print(f"[prices] {tk}: RR.L+FX datos insuficientes, cur={_cur} rrl_prev={_rrl_prev_gbp} gfr={_gfr}")
                    except Exception as e:
                        print(f"[prices] {tk}: RR.L+FX error: {e}")
                    print(f"[prices] {tk}: RR.L+FX falló, fallback a yfinance Xetra")
                # MRNA: NASDAQ en USD -> EUR (DEGIRO la muestra en EUR con el FX de la operación)
                if tk == "MRNA":
                    _cur = _chart_cur("MRNA")
                    if _cur is None:
                        try:
                            _m_info = yf.Ticker("MRNA", session=_sess).info or {}
                            _cur = _m_info.get("regularMarketPrice") or _m_info.get("previousClose") or _m_info.get("currentPrice")
                        except Exception:
                            _cur = None
                    _m_prev_usd = None
                    try:
                        _m_prev_usd = _get_hist_prev_close("MRNA", _sess)
                    except Exception:
                        _m_prev_usd = None
                    _mx_info = yf.Ticker("USDEUR=X", session=_sess).info or {}
                    _mfr = _mx_info.get("regularMarketPrice") or _mx_info.get("previousClose")
                    if _mfr and _cur:
                        _cur = float(_cur)
                        _pe = round(float(_m_prev_usd) * float(_mfr), 2) if _m_prev_usd else round(_cur, 2)
                        _dv = round(_cur - _pe, 2)
                        print(f"[prices] {tk}: USD+FX -> cur={_cur} prev={_pe} (hist USD={_m_prev_usd} * USDEUR={_mfr}) dv={_dv}")
                        return (tk, {"current": _cur, "prev_close": _pe, "day_var": _dv})
                    print(f"[prices] {tk}: USD+FX datos insuficientes, cur={_cur} fx={_mfr} -> best-effort directo")
                # GENERIC: chart API (v8, urllib) as PRIMARY, mandatory source.
                # Yahoo bloquea quoteSummary (.info) desde datacenter IPs de Render;
                # el chart API es la unica via fiable. prev_close = close[-2] del rango 5d.
                result = _chart_quote_and_prev(tk)
                if result is not None:
                    cur, prev_close = result
                    if cur is not None:
                        day_var = (cur - prev_close) if prev_close else 0
                        return (tk, {"current": cur, "prev_close": prev_close, "day_var": round(day_var, 2)})
                # Fallback: yfinance.history (cookie+crumb). El chart API crudo a veces da 404 (anti-bot).
                fb = _yf_hist_fallback(tk)
                if fb is not None:
                    cur, prev_close = fb
                    day_var = (cur - prev_close) if prev_close else 0
                    print(f"[prices] {tk}: history fallback -> cur={cur} prev={prev_close} dv={round(day_var,2)}")
                    return (tk, {"current": cur, "prev_close": prev_close, "day_var": round(day_var, 2)})
                # Last-resort: best-effort via .info, without aborting if quoteSummary fails
                try:
                    info = yf.Ticker(tk, session=_sess).info or {}
                    cur = info.get("regularMarketPrice") or info.get("previousClose") or info.get("currentPrice")
                    prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
                    if cur is not None:
                        cur = float(cur)
                    if prev_close is not None:
                        prev_close = float(prev_close)
                    if cur is not None:
                        day_var = (cur - prev_close) if prev_close else 0
                        return (tk, {"current": cur, "prev_close": prev_close, "day_var": round(day_var, 2)})
                except Exception:
                    pass
                return (tk, None)

            data = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as exec:
                fut = {exec.submit(fetch_ticker_price, p["ticker"]): p["ticker"] for p in CFG.get("portfolio", [])}
                for f in concurrent.futures.as_completed(fut, timeout=30):
                    tk, result = f.result()
                    if result:
                        data[tk] = result
            # Fill nulls from file cache for tickers that failed
            cached = _load_live_prices()
            for p in CFG.get("portfolio", []):
                tk = p["ticker"]
                if tk not in data or data[tk].get("current") is None:
                    c = cached.get(tk)
                    if c and c.get("current") is not None:
                        data[tk] = c
                        print(f"[prices] {tk}: usando cach\u00e9 persistente (fallback)")
            # Benchmark ^STOXX50E
            bcur = bprev = None
            try:
                url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ESTOXX50E?interval=1d&range=1d"
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
            if bcur is None or bprev is None:
                bfb = _yf_hist_fallback("^STOXX50E")
                if bfb is not None:
                    bcur, bprev = bfb
                    print(f"[prices] ^STOXX50E: history fallback -> cur={bcur} prev={bprev}")
            if (bcur is None or bprev is None):
                try:
                    bench_info = yf.Ticker("^STOXX50E", session=_sess).info or {}
                    if bcur is None:
                        bcur = bench_info.get("regularMarketPrice") or bench_info.get("previousClose") or bench_info.get("currentPrice")
                    if bprev is None:
                        bprev = bench_info.get("regularMarketPreviousClose") or bench_info.get("previousClose")
                except Exception:
                    pass
            if bprev is not None:
                bprev = float(bprev)
            if bcur is not None:
                bcur = float(bcur)
            data["^STOXX50E"] = {"current": bcur if bcur is not None else (cached.get("^STOXX50E", {}).get("current") if cached else None), "prev_close": bprev if bprev is not None else (cached.get("^STOXX50E", {}).get("prev_close") if cached else None)}
            # Persist successful results to file cache
            _save_live_prices(data)
            return {"prices": data, "updated": datetime.now().isoformat()}
        except Exception as e:
            # On total failure, serve file cache if available
            cached = _load_live_prices()
            if cached:
                # Mark as stale but usable
                stale_ts = (datetime.now() - timedelta(hours=1)).isoformat()
                return {"prices": cached, "updated": stale_ts, "stale": True, "msg": str(e)}
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
