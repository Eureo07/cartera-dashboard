# -*- coding: utf-8 -*-
import sys, os, subprocess
from zoneinfo import ZoneInfo
_PROJ_DIR = "C:/Users/franl/OneDrive/Escritorio/Inversión/OpenCode/2026"
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)
import pandas as pd
import yfinance as yf
import requests
import json, math, statistics
from datetime import datetime, date
from config_loader import CFG, logger, get_logger
from screener import calcular_soporte_resistencia

_YF_SESSION = requests.Session()
_YF_SESSION.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

BASE_DIR = CFG["base_dir"]
portfolio = CFG["portfolio"]
tesis = CFG["thesis"]
EXCEL_FILE = CFG["paths"]["excel"]
OUT_FILE = CFG["paths"]["dashboard"]
PRICE_HISTORY = CFG["paths"]["price_history"]

log = get_logger("generate_dashboard")

# ========== HELPERS ==========
def val_metric(val, min_v, max_v):
    if val is None or (isinstance(val, float) and (pd.isna(val) or math.isnan(val))):
        return None
    if min_v <= val <= max_v:
        return val
    return None

def metric_class(mtype, val):
    if val is None:
        return ""
    if mtype == "roe":
        return "green" if val >= 15 else ("yellow" if val >= 5 else "red")
    elif mtype == "roi":
        return "green" if val >= 10 else ("yellow" if val >= 5 else "red")
    elif mtype == "fcf":
        return "green" if val > 0 else "red"
    elif mtype == "eva":
        return "green" if val > 0 else "red"
    elif mtype == "margen":
        return "green" if val >= 15 else ("yellow" if val >= 8 else "red")
    elif mtype == "per":
        if val is None: return ""
        return "green" if 10 <= val <= 20 else ("yellow" if (5 <= val < 10) or (20 < val <= 30) else "red")
    elif mtype == "pb":
        if val is None: return ""
        return "green" if 1 <= val <= 3 else ("yellow" if (0.5 <= val < 1) or (3 < val <= 5) else "red")
    elif mtype == "ev_ebitda":
        if val is None: return ""
        return "green" if val < 10 else ("yellow" if val < 15 else "red")
    elif mtype == "pnl":
        return "green" if val >= 0 else "red"
    elif mtype == "dist_stop":
        return "green" if val > 15 else ("yellow" if val > 8 else "red")
    return ""

def per_val_class(val):
    if val is None: return ""
    return "green" if val < 15 else ("yellow" if val <= 25 else "red")

def cell(val_str, cls, cell_id=None):
    id_attr = f' id="{cell_id}"' if cell_id else ""
    if not cls:
        return f"<td{id_attr}>{val_str}</td>"
    return f'<td{id_attr} class="{cls}">{val_str}</td>'

def fmt(val, dec=2):
    if val is None: return "-"
    return f"{val:.{dec}f}"

# ========== GAUGE ==========
def generar_gauge_riesgo(entry, stop, target, current):
    if stop is None or stop <= 0:
        return '<div style="font-size:11px;color:#5a5f6b;margin-top:10px">Gauge no disponible (sin Stop Loss)</div>'
    fallback_max = max(current * 1.1, entry * 1.15)
    max_val = max(target, fallback_max) if target else fallback_max
    rng = max_val - stop
    entry_pct = min(max((entry - stop) / rng * 100, 0), 100)
    cur_pct = min(max((current - stop) / rng * 100, 0), 100)
    w, h = 280, 48
    bar_y = 20
    bar_h = 10
    entry_x = round(entry_pct / 100 * w)
    cur_x = round(cur_pct / 100 * w)
    is_ok = current >= entry
    marker_color = "#3ecf8e" if is_ok else "#e05050"
    cur_label = f"{current:.2f}"
    stop_label = f"{stop:.2f}"
    entry_label = f"{entry:.2f}"
    max_label = f"{max_val:.2f} \u20ac" if target else "N/D"
    return f'''<div style="margin-top:24px;padding:0 4px">
    <svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;overflow:visible">
      <rect x="0" y="{bar_y}" width="{w}" height="{bar_h}" rx="5" fill="#1e2235"/>
      <rect x="0" y="{bar_y}" width="{entry_x}" height="{bar_h}" rx="5" fill="#e05050" opacity="0.35"/>
      <rect x="{entry_x}" y="{bar_y}" width="{w-entry_x}" height="{bar_h}" rx="5" fill="#3ecf8e" opacity="0.35"/>
      <line x1="{entry_x}" y1="{bar_y}" x2="{entry_x}" y2="{bar_y+bar_h}" stroke="#9aa0b0" stroke-width="1" stroke-dasharray="2,2"/>
      <polygon points="{cur_x},{bar_y-2} {cur_x-5},{bar_y-9} {cur_x+5},{bar_y-9}" fill="{marker_color}"/>
      <text x="{cur_x}" y="{bar_y-12}" text-anchor="middle" fill="{marker_color}" font-size="9">{cur_label} \u20ac</text>
      <text x="0" y="{bar_y+bar_h+12}" fill="#e05050" font-size="8">{stop_label}</text>
      <text x="{entry_x}" y="{bar_y+bar_h+12}" text-anchor="middle" fill="#9aa0b0" font-size="8">{entry_label}</text>
      <text x="{w}" y="{bar_y+bar_h+12}" text-anchor="end" fill="#3ecf8e" font-size="8">{max_label}</text>
    </svg>
    <div style="display:flex;justify-content:space-between;font-size:9px;color:#9aa0b0;margin-top:4px;padding:0 2px">
      <span>Stop Loss</span>
      <span>P. Entrada</span>
      <span>{"Objetivo" if target else "Obj. N/D"}</span>
    </div>
    </div>'''

# ========== LOAD DATA ==========
DF_FILE = EXCEL_FILE
df = pd.read_excel(DF_FILE)
cols_list = list(df.columns)
idx_col_name, sec_col_name = cols_list[3], cols_list[4]

# Fetch historical prices from yfinance for all positions + benchmark
price_hist = {}
full_hist = {}
bench_hist = None
hist_path = CFG["paths"]["price_history"]
log.info("Downloading price histories...")
last_data_date = None
for p in portfolio:
    tk = p["ticker"]
    try:
        entry_dt = datetime.strptime(p["entry_date"], "%d/%m/%Y")
        start_str = entry_dt.strftime("%Y-%m-%d")
        stock = yf.Ticker(tk, session=_YF_SESSION)
        hist = stock.history(start=start_str, auto_adjust=False)
        if hist is not None and len(hist) > 2:
            close = hist["Close"].dropna()
            if len(close) < 2:
                log.warning(f"  {tk}: menos de 2 Close validos ({len(close)})")
                continue
            price_hist[tk] = close
            full_hist[tk] = hist
            p["current"] = float(close.iloc[-1])
            dt_candle = close.index[-1]
            if last_data_date is None or dt_candle > last_data_date:
                last_data_date = dt_candle
        else:
            log.warning(f"  {tk}: hist={None if hist is None else len(hist)} (insuficiente)")
    except Exception as e:
        log.error(f"  {tk}: error history ({e})")
    # Fallback for current price
    if "current" not in p or p["current"] is None:
        try:
            info = yf.Ticker(tk, session=_YF_SESSION).info or {}
            raw = info.get("regularMarketPrice") or info.get("previousClose") or info.get("currentPrice")
            if raw is not None:
                p["current"] = float(raw)
            else:
                p["current"] = 0
                p["data_error"] = True
                log.error(f"  {tk}: fallback SIN DATOS — todas las fuentes yfinance devolvieron None")
        except Exception as e:
            log.error(f"  {tk}: fallback EXCEPTION — {e}")
            p["current"] = 0
            p["data_error"] = True
# Support/resistance for each position
for p in portfolio:
    tk = p["ticker"]
    hist = full_hist.get(tk)
    if hist is not None and len(hist) >= 3:
        try:
            support, resistance, cprice, ok = calcular_soporte_resistencia(tk, hist_data=hist)
            if ok and support is not None:
                p["pos_support"] = round(support, 2)
                p["pos_resistance"] = round(resistance, 2) if resistance is not None else None
                p["dynamic_stop"] = round(support * 0.98, 2)
            else:
                p["pos_support"] = None
                p["pos_resistance"] = None
                p["dynamic_stop"] = None
        except Exception:
            p["pos_support"] = None
            p["pos_resistance"] = None
            p["dynamic_stop"] = None
    else:
        p["pos_support"] = None
        p["pos_resistance"] = None
        p["dynamic_stop"] = None
# Override support values with hardcoded correct values
support_overrides = {"ENR.DE": 133.85, "NVD.DE": 144.54, "RRU.DE": 12.53, "DANR.MI": 47.84}
for p in portfolio:
    if p["ticker"] in support_overrides:
        p["pos_support"] = support_overrides[p["ticker"]]
# Benchmark
try:
    bm = yf.Ticker("^STOXX50E", session=_YF_SESSION)
    bm_h = bm.history(period="6mo", auto_adjust=False)
    if bm_h is not None and len(bm_h) > 2:
        bench_hist = bm_h["Close"].dropna()
except:
    pass
# Save to CSV
try:
    records = []
    for tk, series in price_hist.items():
        for d, px in series.items():
            records.append({"fecha": d.strftime("%Y-%m-%d"), "ticker": tk, "precio": px})
    if records:
        rdf = pd.DataFrame(records)
        rdf.to_csv(hist_path, index=False)
        log.info(f"  Hist\u00f3rico guardado: {hist_path} ({len(rdf)} filas)")
except Exception as e:
    log.error(f"  Error al guardar hist\u00f3rico: {e}")

# ========== VALUATION ==========
def get_valuation(t):
    try:
        stock = yf.Ticker(t, session=_YF_SESSION)
        info = stock.info or {}
        ev = info.get("enterpriseValue")
        ebitda = info.get("ebitda")
        div_yield = info.get("dividendYield")
        if div_yield is not None and (div_yield < 0 or div_yield > 0.15):
            div_yield = None
        beta_v = info.get("beta")
        if beta_v is not None and (beta_v < 0 or beta_v > 5):
            beta_v = None
        return {
            "per": val_metric(info.get("trailingPE"), 0, 200),
            "fwd_per": info.get("forwardPE"),
            "pb": val_metric(info.get("priceToBook"), 0, 100),
            "ev_ebitda": ev / ebitda if (ev and ebitda and ebitda != 0) else None,
            "mcap": info.get("marketCap"),
            "eps": info.get("trailingEps"),
            "div_yield": div_yield,
            "ps": info.get("priceToSalesTrailing12Months"),
            "beta": beta_v,
        }
    except:
        return {"per": None, "fwd_per": None, "pb": None, "ev_ebitda": None, "mcap": None, "eps": None, "div_yield": None, "ps": None, "beta": None}

_rent_cache = {}
def get_1y_return(t):
    if t in _rent_cache:
        return _rent_cache[t]
    try:
        hist = yf.download(t, period="1y", progress=False, auto_adjust=False, session=_YF_SESSION)
        if hist is not None and not hist.empty:
            if isinstance(hist.columns, pd.MultiIndex):
                close = hist.xs(t, level=1, axis=1)["Close"]
            else:
                close = hist["Close"] if "Close" in hist.columns else hist.iloc[:, 0]
            close = close.dropna()
            if len(close) < 2:
                return None
            r = (float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0]) * 100
            _rent_cache[t] = r
            return r
    except:
        pass
    _rent_cache[t] = None
    return None

def rent_1a_class(val, sector_avg):
    if val is None or sector_avg is None:
        return ""
    if val > sector_avg + 10:
        return "green"
    elif val < sector_avg - 10:
        return "red"
    else:
        return "yellow"

# ========== FALLBACK FINANCIALS (for positions without DB entry) ==========
def get_fallback_financials(ticker):
    """Extract financial metrics from yfinance for tickers not in the DB."""
    try:
        stock = yf.Ticker(ticker, session=_YF_SESSION)
        info = stock.info or {}
        bs = stock.balance_sheet
        inc = stock.financials
        cf = stock.cashflow
    except:
        return None
    result = {}
    # Find the latest fiscal year with actual non-NaN data
    def find_last_good_year(frames):
        best = None
        for f in frames:
            if f is None or f.empty: continue
            for c in sorted(f.columns, reverse=True):
                for label in ["Net Income", "Stockholders Equity", "Total Revenue"]:
                    if label in f.index:
                        v = f.loc[label, c]
                        if pd.notna(v) and v != 0:
                            if best is None or c.year > best:
                                best = c.year
        return best
    latest_year = find_last_good_year([inc, bs, cf])
    # Map the latest real fiscal year to the closest DB column (2024, 2025, 2026)
    db_year_map = {}
    if latest_year is not None:
        for db_y in [2024, 2025, 2026]:
            if latest_year == db_y:
                db_year_map[db_y] = latest_year
    # If no exact match, put data in 2024 (latest available year)
    if latest_year is not None and not db_year_map:
        db_year_map[2024] = latest_year
    # Populate DB year columns
    for db_year in [2024, 2025, 2026]:
        y = db_year_map.get(db_year)
        if y is None:
            result[f"{db_year} ROE"] = None
            result[f"{db_year} ROI"] = None
            result[f"{db_year} FCN"] = None
            result[f"{db_year} EVA"] = None
            result[f"{db_year} M.EBITA"] = None
            result[f"{db_year} M.EBIT"] = None
            continue
        def col_for_year(df_ref):
            if df_ref is None or df_ref.empty: return None
            for c in sorted(df_ref.columns, reverse=True):
                if c.year == y:
                    return c
            return None
        bs_col = col_for_year(bs)
        inc_col = col_for_year(inc)
        cf_col = col_for_year(cf)
        roe, roi, fcf, eva, mebita, mebit = None, None, None, None, None, None
        if inc_col is not None and bs_col is not None:
            ni = inc.loc["Net Income", inc_col] if "Net Income" in inc.index else None
            eq = bs.loc["Stockholders Equity", bs_col] if "Stockholders Equity" in bs.index else None
            rev = inc.loc["Total Revenue", inc_col] if "Total Revenue" in inc.index else None
            ebit = inc.loc["EBIT", inc_col] if "EBIT" in inc.index else None
            if pd.notna(ni) and pd.notna(eq) and eq != 0:
                roe = float(ni / eq) * 100
            if pd.notna(ni):
                ta = bs.loc["Total Assets", bs_col] if "Total Assets" in bs.index else None
                if pd.notna(ta) and ta != 0:
                    roi = float(ni / ta) * 100
        if cf_col is not None:
            fcf_v = cf.loc["Free Cash Flow", cf_col] if "Free Cash Flow" in cf.index else None
            if pd.notna(fcf_v):
                fcf = float(fcf_v)
        if pd.notna(ebit) and pd.notna(rev) and rev != 0:
            mebit = float(ebit / rev) * 100
        ebitda = inc.loc["EBITDA", inc_col] if (inc_col is not None and "EBITDA" in inc.index) else None
        if pd.notna(ebitda) and pd.notna(rev) and rev != 0:
            mebita = float(ebitda / rev) * 100
        result[f"{db_year} ROE"] = roe
        result[f"{db_year} ROI"] = roi
        result[f"{db_year} FCN"] = fcf
        result[f"{db_year} EVA"] = eva
        result[f"{db_year} M.EBITA"] = mebita
        result[f"{db_year} M.EBIT"] = mebit
    # Sector from yfinance info
    result["Sector"] = info.get("sector", "Desconocido")
    return result

log.info("Fetching valuation data...")
valuation = {}
for p in portfolio:
    v = get_valuation(p["ticker"])
    valuation[p["ticker"]] = v
    log.info(f"  {p['ticker']}: PER={v['per']}, DivY={v['div_yield']}")

def get_sector_metrics(sec, df, sec_col):
    sdf = df[df[sec_col] == sec]
    return {"roe_mean": sdf["2026 ROE"].mean(), "roe_med": sdf["2026 ROE"].median(), "n": len(sdf)}

# ========== CALCULATIONS ==========
now = datetime.now()
for p in portfolio:
    p["cost"] = p["shares"] * p["entry"] + p.get("commission", 0)
    p["value"] = p["shares"] * p["current"]
    p["pnl"] = p["value"] - p["cost"]
    p["pnl_pct"] = (p["pnl"] / p["cost"]) * 100 if p["cost"] else 0
    p["dist_stop"] = ((p["current"] - p["stop"]) / p["current"]) * 100 if p["current"] else 0
    p["weight"] = 0  # calc after total
    # CAGR
    try:
        ed = datetime.strptime(p["entry_date"], "%d/%m/%Y")
        days = (now - ed).days
        p["days"] = days
        years = days / 365.25
        if days >= 30 and p["cost"] > 0:
            p["cagr"] = (p["value"] / p["cost"]) ** (1 / years) - 1
        elif days == 0 and p["cost"] > 0:
            p["cagr"] = 0.0  # Day of entry, no return yet
        else:
            p["cagr"] = None
    except:
        p["cagr"] = None
        p["days"] = 0

total_cost = sum(p["cost"] for p in portfolio)
total_value = sum(p["value"] for p in portfolio)
total_pnl = total_value - total_cost
total_pnl_pct = (total_pnl / total_cost) * 100 if total_cost else 0

# ========== HISTORICAL CLOSED POSITIONS ==========
closed_positions = [
    {"entry_date": "15/08/2025", "name": "FERROVIAL SE", "ticker": "FER.MC", "shares": 53, "entry": 46.86, "cost": 2490.58, "support": 43.30, "stop": 41.99, "sale_price": 54.69, "pnl_eur": 400.99, "pnl_pct": 16.15},
    {"entry_date": "18/08/2025", "name": "IBERDROLA", "ticker": "IBE.MC", "shares": 158, "entry": 16.40, "cost": 2597.41, "support": 15.10, "stop": 14.64, "sale_price": 18.86, "pnl_eur": 375.47, "pnl_pct": 14.49},
    {"entry_date": "18/08/2025", "name": "GRENERGY RENOVABLES", "ticker": "GRE.MC", "shares": 127, "entry": 5.00, "cost": 904.00, "support": 58.60, "stop": 56.84, "sale_price": 102.00, "pnl_eur": 316.00, "pnl_pct": 35.11},
    {"entry_date": "17/11/2025", "name": "HEIDELBERG MATERIALS", "ticker": "HEI.DE", "shares": 52, "entry": 12.00, "cost": 1064.90, "support": 184.80, "stop": 179.16, "sale_price": 179.20, "pnl_eur": -168.90, "pnl_pct": -15.93},
]
closed_total_pnl = sum(p["pnl_eur"] for p in closed_positions)
closed_total_cost = sum(p["cost"] for p in closed_positions)
historical_pnl = total_pnl + closed_total_pnl
historical_cost = total_cost + closed_total_cost
historical_return = (historical_pnl / historical_cost) * 100 if historical_cost else 0

# ========== FONDOS INDEXADOS + CUENTA (para KPIs Rendimiento) ==========
fondos_total = 0.0
total_aportado_fondos = 0.0
cuenta_saldo = 0.0
cuenta_intereses = 0.0
cuenta_tae = 3.0
try:
    f_path = os.path.join(CFG["base_dir"], "fondos_indexados.json")
    if os.path.exists(f_path):
        with open(f_path, "r", encoding="utf-8") as _f:
            _fd = json.load(_f)
        for _fdo in _fd.get("fondos", []):
            fondos_total += _fdo.get("valor_actual") or 0
            total_aportado_fondos += _fdo.get("aportado") or 0
except Exception:
    pass
rend_fondos_eur = fondos_total - total_aportado_fondos
rend_fondos_pct = (rend_fondos_eur / total_aportado_fondos * 100) if total_aportado_fondos else None
try:
    c_path = os.path.join(CFG["base_dir"], "cuenta_remunerada.json")
    if os.path.exists(c_path):
        with open(c_path, "r", encoding="utf-8") as _f:
            _cd = json.load(_f)
        _hist = _cd.get("historico_saldos", [])
        if _hist:
            _ult = max(_hist, key=lambda x: x["fecha"])
            cuenta_saldo = _ult["saldo"]
        cuenta_intereses = _cd.get("intereses_acumulados_periodo", 0) or 0
        cuenta_tae = _cd.get("tae_actual", 3.0)
except Exception:
    pass
rend_cuenta_eur = cuenta_intereses
rend_cuenta_pct = (rend_cuenta_eur / cuenta_saldo * 100) if cuenta_saldo else 0

# Daily variation (HOY) — cur - previousClose (real, no ajustado)
day_var_total = 0.0
n_day_var = 0
for p in portfolio:
    tk = p["ticker"]
    hist = full_hist.get(tk)
    if hist is not None and len(hist) >= 2:
        close_s = hist["Close"].dropna()
        cur_px = float(close_s.iloc[-1])
        prev_close = None
        try:
            import urllib.request, json, yfinance as yf
            info = yf.Ticker(tk).info or {}
            ip = info.get("regularMarketPreviousClose") or info.get("previousClose")
            if ip is not None and round(float(ip), 4) != round(cur_px, 4):
                prev_close = float(ip)
            else:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=1d&range=5d"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                resp = urllib.request.urlopen(req, timeout=10)
                chart = json.loads(resp.read())
                cp = chart.get("chart", {}).get("result", [{}])[0].get("meta", {}).get("chartPreviousClose")
                if cp is not None:
                    prev_close = float(cp)
        except:
            if len(close_s) >= 2:
                prev_close = float(close_s.iloc[-2])
        if prev_close is not None:
            dv = (cur_px - prev_close) * p["shares"]
            p["day_var"] = dv
            day_var_total += dv
            n_day_var += 1
        else:
            p["day_var"] = None
    else:
        p["day_var"] = None
day_var_pct = (day_var_total / total_value) * 100 if total_value and n_day_var > 0 else None

for p in portfolio:
    p["weight"] = (p["value"] / total_value) * 100 if total_value else 0
    p["target"] = p["entry"] * 1.175  # +17.5%

# ========== ALERTS ==========
alerts = []
for p in portfolio:
    v = valuation.get(p["ticker"], {})
    per = v.get("per")
    pb = v.get("pb")
    if per and per > 30:
        alerts.append(f"{p['name']}: PER alto ({per:.1f})")
    if per and per < 5:
        alerts.append(f"{p['name']}: PER muy bajo ({per:.1f}), posible distress")
    if pb and pb > 5:
        alerts.append(f"{p['name']}: P/B elevado ({pb:.2f})")
    if p["dist_stop"] < 8 and p["dist_stop"] > 0:
        alerts.append(f"{p['name']}: Stop loss cercano ({p['dist_stop']:.1f}%)")
    db_t = p["db_ticker"]
    if db_t:
        row = df[df["Ticker"] == db_t]
        if not row.empty:
            roe = row.iloc[0]["2026 ROE"]
            roe_v = val_metric(roe, -500, 500)
            if roe_v is not None and roe_v < 5:
                alerts.append(f"{p['name']}: ROE bajo ({roe_v:.1f}%)")
    # Target profit alert
    if p["pnl_pct"] > 15:
        alerts.append(f"{p['name']}: objetivo de beneficios alcanzado (+{p['pnl_pct']:.1f}%)")
    if p["current"] >= p["target"]:
        alerts.append(f"{p['name']}: Objetivo alcanzado \u2014 revisar soporte")
    # Concentration alerts
    if p["weight"] > 30:
        alerts.insert(0, f'<span class="alert-red">\u26a0 {p["name"]}: peso elevado ({p["weight"]:.1f}% de la cartera)</span>')
    elif p["weight"] > 25:
        alerts.insert(0, f'<span class="alert-yellow">\u00b7 {p["name"]}: peso alto ({p["weight"]:.1f}% de la cartera)</span>')

# ========== DIVERSIFICATION INFO ==========
defensive_sectors = {"Salud", "Utilities", "Consumo básico", "Telecomunicaciones", "Comunicaciones", "Alimentación", "Consumo", "Farma"}
def get_diversification_info():
    info_list = []
    for p in portfolio:
        ticker = p["ticker"]
        try:
            stock = yf.Ticker(ticker, session=_YF_SESSION)
            si = stock.info or {}
            sec = si.get("sector", "Desconocido")
            cur = si.get("currency", "EUR")
            ctry = si.get("country", "Desconocido")
        except:
            sec, cur, ctry = "Desconocido", "EUR", "Desconocido"
        info_list.append({"name": p["name"], "sector": sec, "currency": cur, "country": ctry, "weight": p["weight"]})
    return info_list

div_info = get_diversification_info()

# Indicator 1: Max concentration
max_w = max(p["weight"] for p in portfolio)
if max_w > 35:
    worst_name = [p['name'] for p in portfolio if abs(p['weight'] - max_w) < 0.01][0]
    conc_icon, conc_text = "\U0001f534", f"Concentraci\u00f3n cr\u00edtica ({worst_name} {max_w:.1f}%)"  # red circle
elif max_w > 25:
    worst = [p['name'] for p in portfolio if p['weight']==max_w]
    conc_icon, conc_text = "\U0001f7e1", f"Concentraci\u00f3n alta ({worst[0]} {max_w:.1f}%)"  # yellow circle
else:
    conc_icon, conc_text = "\U0001f7e2", f"Concentraci\u00f3n controlada (m\u00e1ximo {max_w:.1f}%)"  # green circle

# Indicator 2: Defensive sectors
defensive_count = sum(1 for d in div_info if d["sector"] in defensive_sectors)
if defensive_count > 0:
    def_icon, def_text = "\U0001f7e2", f"{defensive_count} posici\u00f3n(es) en sectores defensivos"
else:
    def_icon, def_text = "\U0001f534", "Ninguna posici\u00f3n en sectores defensivos"

# Indicator 3: Currency diversification
currencies = set(d["currency"] for d in div_info)
countries = set(d["country"] for d in div_info)
if len(currencies) >= 2:
    div_icon, div_text = "\U0001f7e2", f"Divisas: {'/'.join(currencies)} ({len(currencies)} distintas)"
elif len(countries) >= 2:
    div_icon, div_text = "\U0001f7e1", f"Misma divisa ({list(currencies)[0]}) pero empresas de distintos pa\u00edses"
else:
    div_icon, div_text = "\U0001f534", f"Misma divisa ({list(currencies)[0]}) y mismo pa\u00eds ({list(countries)[0]})"

# ========== SECTOR PEERS (DEDUPED) ==========
def normalized_score(series):
    mn, mx = series.min(), series.max()
    if mx == mn: return pd.Series(0.5, index=series.index)
    return (series - mn) / (mx - mn)

def peer_ticker_to_yf(t):
    m = {"RDEIF": "REE.MC", "HEI.DE": "HEIG.DE", "STLAM.MI": "STLAM.PA", "STMPA.PA": "STM.PA", "HEN3.DE": "HENKY.DE", "BRK-B": "BRK.B"}
    return m.get(t, t)

# ========== BENCHMARK (Euro Stoxx 50) ==========
log.info("Fetching benchmark data...")
entry_dates = [datetime.strptime(p["entry_date"], "%d/%m/%Y") for p in portfolio]
bench_start = min(entry_dates).strftime("%Y-%m-%d")
benchmark_return = None
try:
    stoxx = yf.download("^STOXX50E", start=bench_start, progress=False, auto_adjust=False, session=_YF_SESSION)
    if stoxx is not None and not stoxx.empty:
        if isinstance(stoxx.columns, pd.MultiIndex):
            stoxx_close = stoxx.xs("^STOXX50E", level=1, axis=1)["Close"]
        else:
            stoxx_close = stoxx["Close"] if "Close" in stoxx.columns else stoxx.iloc[:, 0]
        stoxx_close = stoxx_close.dropna()
        if len(stoxx_close) < 2:
            raise ValueError("Insufficient benchmark data")
        bench_start_px = float(stoxx_close.iloc[0])
        bench_end_px = float(stoxx_close.iloc[-1])
        benchmark_return = (bench_end_px / bench_start_px - 1) * 100
        log.info(f"  ^STOXX50E: {bench_start_px:.0f} -> {bench_end_px:.0f} ({benchmark_return:+.2f}%)")
except Exception as e:
    log.error(f"  Benchmark error: {e}")

# ========== CORRELATION MATRIX ==========
log.info("Computing correlation matrix...")
corr_tickers = [p["ticker"] for p in portfolio if p["ticker"]]
corr_data = {}
corr_html_rows = ""
try:
    for t in corr_tickers:
        hist = yf.download(t, period="1y", progress=False, auto_adjust=False, session=_YF_SESSION)
        if hist is not None and not hist.empty:
            if isinstance(hist.columns, pd.MultiIndex):
                c = hist.xs(t, level=1, axis=1)["Close"]
            else:
                c = hist["Close"] if "Close" in hist.columns else hist.iloc[:, 0]
            c = c.dropna()
            if len(c) > 2:
                corr_data[t] = c
    if len(corr_data) >= 2:
        corr_df = pd.DataFrame(corr_data).pct_change().dropna()
        if len(corr_df) > 5:
            corr_matrix = corr_df.corr()
            # Build HTML table
            corr_html_rows = "<tr><th></th>"
            for t2 in corr_tickers:
                short = t2.replace(".DE", "").replace(".MI", "")
                corr_html_rows += f"<th>{short}</th>"
            corr_html_rows += "</tr>"
            for t1 in corr_tickers:
                short1 = t1.replace(".DE", "").replace(".MI", "")
                corr_html_rows += f"<tr><td><strong>{short1}</strong></td>"
                for t2 in corr_tickers:
                    if t1 in corr_matrix.index and t2 in corr_matrix.columns:
                        c_val = corr_matrix.loc[t1, t2]
                        c_cls = "green" if abs(c_val) < 0.3 else ("yellow" if abs(c_val) < 0.7 else "red")
                        corr_html_rows += f'<td class="{c_cls}">{c_val:.2f}</td>'
                    else:
                        corr_html_rows += "<td>-</td>"
                corr_html_rows += "</tr>"
            print("  Correlation matrix OK")
            log.info("  Correlation matrix OK")
        else:
            corr_html_rows = "<tr><td colspan='5' style='color:#888;'>Datos insuficientes para correlaci\u00f3n</td></tr>"
    else:
        corr_html_rows = "<tr><td colspan='5' style='color:#888;'>No hay suficientes datos</td></tr>"
except Exception as e:
    print(f"  Correlation error: {e}")
    corr_html_rows = "<tr><td colspan='5' style='color:#888;'>Error: " + str(e) + "</td></tr>"

# ========== EVOLUTION CHART DATA ==========
evol_dates = []
evol_portfolio = []
evol_benchmark = []
try:
    # Normalize all indices to tz-naive
    ph = {}
    for tk, series in price_hist.items():
        s = series.copy()
        if s.index.tz is not None:
            s.index = s.index.tz_localize(None)
        ph[tk] = s
    bh = bench_hist.copy() if bench_hist is not None else None
    if bh is not None and bh.index.tz is not None:
        bh.index = bh.index.tz_localize(None)

    if len(ph) >= 2 and bh is not None and len(bh) > 2:
        all_dates = sorted({d.date() for s in ph.values() for d in s.index})
        for d in all_dates:
            d_ts = pd.Timestamp(d)
            total = sum(ph[tk].loc[d_ts] * p["shares"]
                        for p in portfolio
                        if p["ticker"] in ph and d_ts in ph[p["ticker"]].index)
            if total > 0:
                evol_dates.append(d.strftime("%Y-%m-%d"))
                evol_portfolio.append(total)
                evol_benchmark.append(bh.loc[d_ts] if d_ts in bh.index else None)
        log.info(f"Evolution chart: {len(evol_dates)} data points")
    else:
        log.info("Evolution chart: datos insuficientes")
except Exception as e:
    log.error(f"  Evolution chart error: {e}")

evol_dates_json = json.dumps(evol_dates)
evol_portfolio_json = json.dumps(evol_portfolio)
evol_benchmark_json = json.dumps(evol_benchmark)

# ========== SECTOR ALTERNATIVES DATA (collected from Top 5 loop) ==========
sector_alts_data = {}

# ========== BUILD HTML ==========
if last_data_date is not None:
    ref_date = last_data_date
    if hasattr(ref_date, 'to_pydatetime'):
        ref_date = ref_date.to_pydatetime()
    if ref_date.tzinfo is None:
        ref_date = ref_date.replace(tzinfo=ZoneInfo("Europe/Madrid"))
    now_str = ref_date.strftime("%d/%m/%Y")
else:
    ref_date = datetime.now(ZoneInfo("Europe/Madrid"))
    now_str = ref_date.strftime("%d/%m/%Y %H:%M")
total_cls = "green" if total_pnl >= 0 else "red"

html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="300">
<title>Dashboard Cartera</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',-apple-system,Arial,sans-serif}}
.dash{{background:#0f1117;color:#e8eaed;padding:20px 30px;min-height:100vh}}
.header{{background:linear-gradient(135deg,#1a1d2e,#2a2d3e);border-radius:16px;padding:28px 36px;margin-bottom:24px;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:24px;font-weight:700;color:#fff}}
.header .sub{{color:#9aa0b0;font-size:13px;margin-top:4px}}
.header .date-info{{text-align:right;color:#9aa0b0;font-size:12px}}
.kpi-row{{display:grid;grid-template-columns:repeat(auto-fill, minmax(130px,1fr));gap:14px;margin-bottom:24px}}
.kpi{{background:#1a1d2e;border-radius:12px;padding:18px 22px}}
.kpi .label{{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#9aa0b0;margin-bottom:8px}}
.kpi .value{{font-size:22px;font-weight:700;color:#fff}}
.kpi .value.neg{{color:#e05050}}
.kpi .value.pos{{color:#3ecf8e}}
.kpi .sub{{font-size:11px;color:#9aa0b0;margin-top:4px}}
.section-title{{font-size:13px;text-transform:uppercase;letter-spacing:1px;color:#9aa0b0;margin-bottom:14px;font-weight:600}}
.positions-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
.pos-card{{background:#1a1d2e;border-radius:12px;padding:20px 24px;border-left:4px solid #3ecf8e}}
.pos-card.neg{{border-left-color:#e05050}}
.pos-card .pos-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px}}
.pos-card .ticker{{font-size:18px;font-weight:700;color:#fff}}
.pos-card .name{{font-size:12px;color:#9aa0b0;margin-top:2px}}
.pos-card .price{{text-align:right}}
.pos-card .price .current{{font-size:20px;font-weight:700}}
.pos-card .price .pnl{{font-size:13px;margin-top:2px}}
.pos-card .price .pnl.neg{{color:#e05050}}
.pos-card .price .pnl.pos{{color:#3ecf8e}}
.signal-badge{{display:inline-block;padding:4px 12px;border-radius:6px;font-size:11px;font-weight:700;margin-bottom:12px}}
.signal-badge.compra{{background:#1a3d2e;color:#3ecf8e;border:1px solid #3ecf8e}}
.signal-badge.venta{{background:#3d1a1a;color:#e05050;border:1px solid #e05050}}
.signal-badge.hold{{background:#2d2d1a;color:#f0a500;border:1px solid #f0a500}}
.metrics-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.metric-row{{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:12px}}
.metric-row .ml{{color:#9aa0b0}}
.metric-row .mv{{color:#fff;font-weight:600}}
.metric-row .mv.neg{{color:#e05050}}
.metric-row .mv.pos{{color:#3ecf8e}}
.metric-row .mv.warn{{color:#f0a500}}
.charts-row{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
.chart-card{{background:#1a1d2e;border-radius:12px;padding:18px 20px}}
.chart-card .ctitle{{font-size:12px;color:#9aa0b0;margin-bottom:12px;text-transform:uppercase;letter-spacing:1px}}
.bottom-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:24px}}
.info-card{{background:#1a1d2e;border-radius:12px;padding:18px 20px}}
.info-card .ctitle{{font-size:12px;color:#9aa0b0;margin-bottom:12px;text-transform:uppercase;letter-spacing:1px}}
.alert-item{{padding:8px 10px;border-radius:6px;font-size:12px;margin-bottom:6px;display:flex;align-items:center;gap:8px}}
.alert-item.danger{{background:#3d1a1a;color:#e05050}}
.alert-item.warn{{background:#2d2a1a;color:#f0a500}}
.alert-item.info{{background:#1a1d3d;color:#5b8def}}
.tendencia-row{{display:flex;gap:16px;margin-top:8px}}
.tend-item{{background:#12151f;border-radius:8px;padding:10px 14px;flex:1}}
.tend-label{{font-size:11px;color:#9aa0b0;margin-bottom:6px}}
.tend-val{{font-size:13px;font-weight:700}}
.tend-val.alcista{{color:#3ecf8e}}
.tend-val.bajista{{color:#e05050}}
.corr-table{{width:100%;border-collapse:collapse;font-size:12px}}
.corr-table th{{color:#9aa0b0;padding:5px 8px;text-align:center;font-weight:500}}
.corr-table td{{padding:5px 8px;text-align:center;border-radius:4px;font-weight:600}}
.corr-low{{background:#1a3d2e;color:#3ecf8e}}
.corr-mid{{background:#2d2d1a;color:#f0a500}}
.corr-high{{background:#3d1a1a;color:#e05050}}
.corr-self{{background:#12151f;color:#9aa0b0}}
.legend-row{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:10px}}
.leg-item{{display:flex;align-items:center;gap:5px;font-size:11px;color:#9aa0b0}}
.leg-dot{{width:10px;height:10px;border-radius:2px}}
.tesis-item{{margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid rgba(255,255,255,0.05)}}
.tesis-item:last-child{{border-bottom:none}}
.tesis-ticker{{font-size:13px;font-weight:700;color:#fff}}
.tesis-target{{font-size:11px;color:#3ecf8e;margin-top:2px}}
.tesis-text{{font-size:11px;color:#9aa0b0;margin-top:4px;line-height:1.5}}
.peso-bar{{height:6px;border-radius:3px;background:#3ecf8e;margin-top:4px}}
.peso-bar.warn{{background:#f0a500}}
.peso-bar.danger{{background:#e05050}}
.alt-section{{margin-bottom:24px}}
.alt-table{{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:8px}}
.alt-table th{{background:#12151f;color:#9aa0b0;padding:8px 10px;text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid rgba(255,255,255,0.1)}}
.alt-table td{{padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.05);color:#e8eaed}}
.alt-table tr.excluded td{{color:#5a5f6b;font-style:italic}}
.alt-table tr.selected td{{background:rgba(62,207,142,0.08);font-weight:600}}
.alt-table tr:last-child td{{border-bottom:none}}
.alt-table .badge-tu{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;background:#1a3d2e;color:#3ecf8e;border:1px solid #3ecf8e}}
.alt-table .badge-alt{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;background:#1a2a3d;color:#5b8def;border:1px solid #5b8def}}
.alt-table .badge-excl{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;background:#3d1a1a;color:#e05050;border:1px solid #e05050}}
.score-bar-bg{{background:#12151f;border-radius:4px;height:8px;width:80px;display:inline-block;vertical-align:middle;overflow:hidden}}
.score-bar-fill{{height:8px;border-radius:4px;display:block}}
.alt-note{{font-size:10px;color:#9aa0b0;margin-bottom:20px;padding:8px 12px;background:#12151f;border-radius:8px;line-height:1.5}}
.footer{{text-align:center;padding:20px 0;font-size:11px;color:#5a5f6b;border-top:1px solid rgba(255,255,255,0.05);margin-top:30px}}
.footer span{{color:#9aa0b0}}
.hist-table{{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:8px}}
.hist-table th{{background:#12151f;color:#9aa0b0;padding:8px 10px;text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid rgba(255,255,255,0.1)}}
.hist-table td{{padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.05);color:#e8eaed}}
.hist-table tr:last-child td{{border-bottom:none}}
.hist-table tr.closed td{{background:rgba(255,255,255,0.02);color:#9aa0b0}}
.ew-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
.ew-card{{background:#1a1d2e;border-radius:12px;padding:18px 20px;border-left:4px solid #3ecf8e}}
.ew-card.ew-venta{{border-left-color:#e05050}}
.ew-card.ew-alerta{{border-left-color:#f0a500}}
.ew-card.ew-sin-dato{{border-left-color:#5a5f6b}}
.ew-hdr{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
.ew-ticker{{font-size:15px;font-weight:700;color:#fff}}
.ew-dias{{font-size:11px;color:#9aa0b0}}
.ew-badge{{display:inline-block;padding:3px 10px;border-radius:6px;font-size:11px;font-weight:700}}
.ew-badge-ok{{background:#1a3d2e;color:#3ecf8e;border:1px solid #3ecf8e}}
.ew-badge-alerta{{background:#3d3a1a;color:#f0a500;border:1px solid #f0a500}}
.ew-badge-venta{{background:#3d1a1a;color:#e05050;border:1px solid #e05050}}
.ew-badge-sin{{background:#1a1a2d;color:#5a5f6b;border:1px solid #5a5f6b}}
.ew-table{{width:100%;border-collapse:collapse;font-size:11px;margin:8px 0}}
.ew-table th{{color:#9aa0b0;padding:5px 8px;text-align:left;font-weight:500;font-size:10px;text-transform:uppercase;border-bottom:1px solid rgba(255,255,255,0.1)}}
.ew-table td{{padding:5px 8px;border-bottom:1px solid rgba(255,255,255,0.05);color:#e8eaed}}
.ew-table tr.ew-linea-venta td{{background:rgba(224,80,80,0.1);color:#e05050}}
.ew-table tr.ew-linea-alerta td{{background:rgba(240,165,0,0.08);color:#f0a500}}
.ew-table tr.ew-linea-ok td{{color:#e8eaed}}
.ew-table tr.ew-linea-sin td{{color:#5a5f6b}}
.ew-fuente{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:9px;font-weight:700}}
.ew-fuente-fmp{{background:#1a1a3d;color:#5b8def;border:1px solid #5b8def}}
.ew-fuente-yfinance{{background:#1a2d1a;color:#3ecf8e;border:1px solid #3ecf8e}}
.ew-fuente-xlsx{{background:#2d1a1a;color:#f0a500;border:1px solid #f0a500}}
.ew-fuente-sin{{background:#1a1a2d;color:#5a5f6b;border:1px solid #5a5f6b}}
.ew-cond{{font-size:11px;color:#e8eaed;padding:8px 10px;background:#12151f;border-radius:6px;margin-top:6px;border-left:2px solid #e05050}}
.ew-note{{font-size:10px;color:#5a5f6b;margin-top:10px}}
.ew-loading{{color:#9aa0b0;font-size:13px;padding:20px;text-align:center}}
.ew-error{{color:#e05050;font-size:12px;padding:20px;text-align:center}}
/* Fondos */
.fondos-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
.fondo-card{{background:#1a1d2e;border-radius:12px;padding:20px 24px;border-left:4px solid #3ecf8e}}
.fondo-card .fondo-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}}
.fondo-card .fondo-nombre{{font-size:15px;font-weight:600;color:#fff}}
.fondo-card .fondo-isin{{font-size:11px;color:#9aa0b0}}
.fondo-card .fondo-metric{{display:flex;justify-content:space-between;padding:4px 0;font-size:12px}}
.fondo-card .fondo-metric .ml{{color:#9aa0b0}}
.fondo-card .fondo-metric .mv{{color:#e8eaed;font-weight:600}}
/* Cuenta */
.cuenta-card{{background:#1a1d2e;border-radius:12px;padding:20px 24px;border-left:4px solid #3a7bd5;max-width:400px}}
.cuenta-card .cuenta-saldo{{font-size:28px;font-weight:700;color:#fff;margin-bottom:4px}}
.cuenta-card .cuenta-sub{{font-size:12px;color:#9aa0b0}}
.cuenta-card .cuenta-row{{display:flex;justify-content:space-between;padding:4px 0;font-size:12px;border-top:1px solid #232638;margin-top:6px;padding-top:6px}}
.cuenta-card .cuenta-row .ml{{color:#9aa0b0}}
.cuenta-card .cuenta-row .mv{{color:#e8eaed;font-weight:600}}
/* Radar comparativo */
.radar-toggle{{background:#2a2d3e;border:none;color:#9aa0b0;font-size:11px;padding:6px 14px;border-radius:6px;cursor:pointer;margin-top:10px}}
.radar-toggle:hover{{background:#3a3d4e;color:#e8eaed}}
.radar-table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:11px}}
.radar-table th{{color:#9aa0b0;padding:6px 8px;text-align:left;border-bottom:1px solid #232638;font-weight:600}}
.radar-table td{{padding:6px 8px;border-bottom:1px solid #232638;color:#b0b5c0}}
.radar-table .ter-high{{color:#e05050;font-weight:600}}
.radar-table .ter-low{{color:#3ecf8e}}
.radar-table .tr-own{{background:rgba(62,207,142,0.06)}}
.radar-warn{{color:#f0a500;font-size:11px;padding:6px 10px;background:#2d1a1a;border-radius:6px;margin-top:8px}}
</style>
</head>
<body>
<div class="dash">
  <div class="header">
    <div>
      <h1>Dashboard Cartera</h1>
      <div class="sub">{' · '.join(p['name'] for p in portfolio)}</div>
    </div>
    <div class="date-info">Actualizado: {now_str}<br><span style="color:#3ecf8e;font-size:13px;">{len(portfolio)} posiciones activas</span></div>
  </div>

  <div class="kpi-row" id="kpi-row">
    <div class="kpi" data-kpi="total-cost"><div class="label">Inversi\u00f3n Activa</div><div class="value" data-kpi-val="total-cost">{total_cost:,.0f} \u20ac</div></div>
    <div class="kpi" data-kpi="total-value"><div class="label">Valor Actual</div><div class="value {"neg" if total_pnl < 0 else "pos"}" data-kpi-val="total-value">{total_value:,.0f} \u20ac</div></div>
    <div class="kpi" data-kpi="total-pnl"><div class="label">Resultado Activo</div><div class="value {"neg" if total_pnl < 0 else "pos"}" data-kpi-val="total-pnl">{total_pnl:+,.2f} \u20ac</div></div>
    <div class="kpi" data-kpi="total-pnl-pct"><div class="label">Rentabilidad Activa</div><div class="value {"neg" if total_pnl_pct < 0 else "pos"}" data-kpi-val="total-pnl-pct">{total_pnl_pct:+.2f}%</div><div class="sub">Cartera</div></div>
    <div class="kpi" data-kpi="vs-benchmark" data-bench-start="{bench_start_px:.4f}" data-bench-start-date="{bench_start}"><div class="label">vs Euro Stoxx 50</div><div class="value {"neg" if benchmark_return is not None and (total_pnl_pct - benchmark_return) < 0 else "pos"}" data-kpi-val="vs-benchmark">{("" if benchmark_return is None else f"{(total_pnl_pct - benchmark_return):+.2f}%")}</div><div class="sub" data-kpi-sub="benchmark-ret">{f"\u00cdndice {benchmark_return:+.2f}%" if benchmark_return is not None else "N/D"}</div></div>
    {"<div class=\"kpi\" data-kpi=\"today\"><div class=\"label\">HOY</div><div class=\"value " + ("pos" if day_var_total >= 0 else "neg") + "\" data-kpi-val=\"day-pct\">" + (f"{day_var_pct:+.2f}%" if day_var_pct is not None else "\u2014") + "</div><div class=\"sub\" data-kpi-val=\"day-eur\">" + (f"{day_var_total:+,.2f} \u20ac" if day_var_total is not None else "\u2014") + "</div></div>" if day_var_pct is not None else ""}
    <div class="kpi" data-kpi="historical" data-closed-pnl="{closed_total_pnl:.2f}" data-closed-cost="{closed_total_cost:.2f}"><div class="label">Rent. Hist\u00f3rica Acciones</div><div class="value {"neg" if historical_pnl < 0 else "pos"}" data-kpi-val="historical-return">{historical_return:+.2f}%</div><div class="sub" data-kpi-val="historical-pnl">{historical_pnl:+,.2f} \u20ac / {historical_cost:,.0f} \u20ac invertidos</div></div>
    <div class="kpi" data-kpi="rend-fondos" data-rend-fondos-eur="{rend_fondos_eur:.2f}" data-total-aportado-fondos="{total_aportado_fondos:.2f}"><div class="label">Rendimiento Fondos</div><div class="value {"neg" if rend_fondos_pct is not None and rend_fondos_pct < 0 else "pos"}" data-kpi-val="rend-fondos-pct">{"{:+.2f}%".format(rend_fondos_pct) if rend_fondos_pct is not None else "\u2014"}</div><div class="sub">{"{:+,.2f} \u20ac / {:,.2f} \u20ac aportados".format(rend_fondos_eur, total_aportado_fondos) if rend_fondos_pct is not None else "\u2014"}</div></div>
    <div class="kpi" data-kpi="rend-cuenta" data-rend-cuenta-eur="{rend_cuenta_eur:.2f}" data-rend-cuenta-pct="{rend_cuenta_pct:.2f}" data-tae="{cuenta_tae}"><div class="label">Rendimiento Cuenta</div><div class="value {"neg" if rend_cuenta_pct is not None and rend_cuenta_pct < 0 else "pos"}" data-kpi-val="rend-cuenta-pct">{"{:+,.2f}%".format(rend_cuenta_pct) if cuenta_saldo else "\u2014"}</div><div class="sub">{"{:+,.2f} \u20ac / saldo {:,.2f} \u20ac".format(rend_cuenta_eur, cuenta_saldo) if cuenta_saldo else "\u2014"}, TAE {cuenta_tae:.0f}%</div></div>
    <div class="kpi" data-kpi="rend-total-risk" data-rend-fondos-eur="{rend_fondos_eur:.2f}" data-total-aportado-fondos="{total_aportado_fondos:.2f}" data-closed-pnl="{closed_total_pnl:.2f}" data-closed-cost="{closed_total_cost:.2f}"><div class="label">Rendimiento Total (con riesgo)</div><div class="value" data-kpi-val="rend-total-risk-pct">\u2014</div><div class="sub" data-kpi-val="rend-total-risk-sub">\u2014</div></div>
  </div>

  <div class="section-title">An\u00e1lisis por posici\u00f3n</div>
  <div class="positions-grid">
"""

# MA data for trends
ma20_data = {}
ma50_data = {}
for tk, series in price_hist.items():
    if len(series) >= 20:
        ma20_data[tk] = series.tail(20).mean()
    elif len(series) > 0:
        ma20_data[tk] = series.mean()
    if len(series) >= 50:
        ma50_data[tk] = series.tail(50).mean()
    elif len(series) > 0:
        ma50_data[tk] = series.mean()

pos_charts_js = []
for i, p in enumerate(portfolio):
    tk = p["ticker"]
    v = valuation.get(tk, {})
    per = v.get("per")
    pb_val = v.get("pb")
    fwd_per = v.get("fwd_per")
    beta_val = v.get("beta")
    db_t = p.get("db_ticker")
    sector_name = "Desconocido"
    roe_val = None
    fcf_val = None
    if db_t:
        row = df[df["Ticker"] == db_t]
        if not row.empty:
            r = row.iloc[0]
            sector_name = r.get(sec_col_name, "Desconocido")
            roe_val = val_metric(r.get("2026 ROE"), -500, 500)
            fcf_v = r.get("2026 FCN")
            fcf_val = fcf_v if pd.notna(fcf_v) else None
    # Signal badge
    days = p.get("days", 999)
    if days <= 14:
        signal_cls = "compra"
        signal_txt = "COMPRA (posici\u00f3n nueva)"
    elif p["pnl_pct"] > 5:
        signal_cls = "compra"
        signal_txt = "COMPRA (momentum positivo)"
    elif p["pnl_pct"] < -5:
        signal_cls = "venta"
        signal_txt = "VENTA (correcci\u00f3n)"
    else:
        signal_cls = "hold"
        signal_txt = "HOLD (seguimiento)"
    # Trends
    ma20 = ma20_data.get(tk)
    ma50 = ma50_data.get(tk)
    if ma20 and p["current"] >= ma20:
        st_trend, st_cls = "ALCISTA", "alcista"
    else:
        st_trend, st_cls = "BAJISTA", "bajista"
    if ma50 and p["current"] >= ma50:
        lt_trend, lt_cls = "ALCISTA", "alcista"
    else:
        lt_trend, lt_cls = "BAJISTA", "bajista"
    # Thesis
    t = tesis.get(tk, {})
    thesis_text = t.get("motivo", "")
    # P&L colors
    pnl_cls_card = "neg" if p["pnl"] < 0 else "pos"
    pnl_sign = "" if p["pnl"] < 0 else "+"
    pnl_pct_sign = "" if p["pnl_pct"] < 0 else "+"
    # Metric classes
    per_cls = metric_class("per", per)
    pb_cls = metric_class("pb", pb_val)
    roe_cls = metric_class("roe", roe_val)
    dist_cls = metric_class("dist_stop", p["dist_stop"])
    fcf_cls = "pos" if (fcf_val or 0) > 0 else "neg"
    weight_warn = "warn" if p["weight"] > 25 else ("danger" if p["weight"] > 30 else "")
    weight_cls = "warn" if p["weight"] > 25 else ""
    peso_bar_cls = "danger" if p["weight"] > 30 else ("warn" if p["weight"] > 25 else "")
    # Dynamic stop data
    dynamic_stop = p.get("dynamic_stop")
    pos_support = p.get("pos_support")
    if dynamic_stop is not None:
        dyn_stop_str = f"{dynamic_stop:.2f} \u20ac"
        stop_alert = dynamic_stop > p["stop"]
    else:
        dyn_stop_str = "N/D"
        stop_alert = False
    # Beta data
    if beta_val is not None:
        beta_str = f"{beta_val:.2f}"
        beta_cls = "pos" if beta_val < 1.0 else ("warn" if beta_val <= 1.5 else "neg")
        beta_desc = "Volatilidad relativa al mercado. &gt;1 amplifica movimientos."
    else:
        beta_str = "N/D"
        beta_cls = ""
        beta_desc = ""
    # Chart data for this position
    pos_hist = full_hist.get(tk)
    if pos_hist is not None and len(pos_hist) >= 5:
        pos_dates = json.dumps([d.strftime("%Y-%m-%d") for d in pos_hist.index])
        pos_closes = json.dumps([round(float(v), 2) for v in pos_hist["Close"].values])
        pos_has_chart = True
        pos_support_val = p.get("pos_support")
        pos_resistance_val = p.get("pos_resistance")
        pos_dyn_stop_val = p.get("dynamic_stop")
    else:
        pos_dates = "[]"
        pos_closes = "[]"
        pos_has_chart = False
        pos_support_val = None
        pos_resistance_val = None
        pos_dyn_stop_val = None

    desc = lambda t: f'<span style="display:block;font-size:10px;color:#9aa0b0;font-weight:400;line-height:1.3">{t}</span>'
    html += f"""    <div class="pos-card{" neg" if p["pnl"] < 0 else ""}" data-ticker="{tk}" data-entry="{p['entry']}" data-shares="{p['shares']}" data-stop="{p['stop']}">
      <div class="pos-header">
        <div><div class="ticker">{tk} — {p['name']}</div><div class="name">{sector_name} · Entrada {p['entry_date']}</div></div>
        <div class="price"><div class="current" id="price-{i}" style="color:{"#e05050" if p["pnl"] < 0 else "#3ecf8e"}"><span class="price-val">{p['current']:.2f}</span> \u20ac{" <span style=\"color:#f0a500;font-size:11px\" title=\"Dato no actualizado\">\u26a0</span>" if p.get("data_error") else ""}</div><div class="pnl {pnl_cls_card}" id="pnl-{i}"><span class="pnl-val">{pnl_sign}{p['pnl']:,.2f}</span> \u20ac (<span class="pnl-pct-val">{pnl_pct_sign}{p['pnl_pct']:.2f}</span>%)</div></div>
      </div>
      <div class="signal-badge {signal_cls}">{signal_txt}</div>
      <div class="metrics-grid">
        <div class="metric-row"><span class="ml">P. Entrada</span><span class="mv">{p['entry']:.2f} \u20ac</span></div>
        <div class="metric-row"><span class="ml">Stop Loss</span><span class="mv">{p['stop']:.2f} \u20ac</span></div>
        <div class="metric-row"><span class="ml">Distancia stop{desc("Ca\u00edda m\u00e1xima asumida antes de salir")}</span><span class="mv {dist_cls}" data-metric="dist-stop">{p['dist_stop']:.1f}%</span></div>
        <div class="metric-row"><span class="ml">P. Objetivo</span><span class="mv {"pos" if p["current"] >= p["target"] else ""}" data-metric="target">{p['target']:.2f} \u20ac</span></div>
        <div class="metric-row"><span class="ml">PER{desc("Veces que el precio recoge el beneficio anual")}</span><span class="mv {"warn" if (per or 99) > 30 else ("pos" if per and per <= 20 else "")}">{f"{per:.1f}x" if per else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">PER Fwd{desc("PER estimado con beneficios futuros")}</span><span class="mv">{f"{fwd_per:.1f}x" if fwd_per else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">P/B{desc("Precio respecto al valor contable. &lt;1 infravalorado")}</span><span class="mv {"warn" if (pb_val or 99) > 5 else ("pos" if pb_val and pb_val <= 3 else "")}">{f"{pb_val:.2f}" if pb_val else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">Beta{desc(beta_desc)}</span><span class="mv {beta_cls}">{beta_str}</span></div>
        <div class="metric-row"><span class="ml">ROE 2026{desc("Rentabilidad sobre fondos propios")}</span><span class="mv {"pos" if (roe_val or 0) >= 15 else ("warn" if (roe_val or 0) >= 5 else "neg")}">{f"{roe_val:.1f}%" if roe_val else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">FCF 2026{desc("Caja generada tras inversiones")}</span><span class="mv {fcf_cls}">{f"{fcf_val:,.0f}M \u20ac" if fcf_val else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">Peso cartera{desc("% del capital total invertido en esta posici\u00f3n")}</span><span class="mv {weight_cls}" data-metric="weight">{p['weight']:.1f}%{" \u26a0" if p["weight"] > 25 else ""}</span></div>
        <div class="metric-row"><span class="ml">Stop Din\u00e1mico{desc("Stop calculado sobre soporte t\u00e9cnico (-2%)")}</span><span class="mv neg">{dyn_stop_str}{" <span style=\"color:#f0a500;font-size:9px;margin-left:4px\">\u26a0 Revisar stop</span>" if stop_alert else ""}</span></div>
      </div>
      {generar_gauge_riesgo(p["entry"], p["stop"], p["target"], p["current"])}
      <div class="tendencia-row">
        <div class="tend-item"><div class="tend-label">Corto Plazo</div><div class="tend-val {st_cls}">{st_trend}</div></div>
        <div class="tend-item"><div class="tend-label">Largo Plazo</div><div class="tend-val {lt_cls}">{lt_trend}</div></div>
      </div>
      <div style="font-size:11px;color:#9aa0b0;margin-top:10px;line-height:1.5">{thesis_text}</div>
      {"<div style=\"font-size:11px;color:#9aa0b0;text-transform:uppercase;letter-spacing:0.5px;margin-top:14px;margin-bottom:6px\">Evoluci\u00f3n desde entrada \u00b7 soporte/resistencia</div><div style=\"position:relative;height:180px\"><canvas id=\"chartPos_" + str(i) + "\"></canvas></div>" if pos_has_chart else "<div style=\"font-size:11px;color:#5a5f6b;margin-top:14px\">Soporte en c\u00e1lculo</div>"}
    </div>
"""
    # Build chart JS for this position
    if pos_has_chart:
        n_dates = len(json.loads(pos_dates))
        cid = "chartPos_" + str(i)
        # Build annotation lines via afterDraw plugin
        js_lines = ""
        if pos_support_val is not None:
            sv = str(pos_support_val)
            js_lines += "ctx.save();ctx.strokeStyle='#e05050';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(c.chartArea.left,yScale.getPixelForValue("+sv+"));ctx.lineTo(c.chartArea.right,yScale.getPixelForValue("+sv+"));ctx.stroke();ctx.restore();"
        if pos_resistance_val is not None and pos_resistance_val != pos_support_val:
            rv = str(pos_resistance_val)
            js_lines += "ctx.save();ctx.strokeStyle='#3ecf8e';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(c.chartArea.left,yScale.getPixelForValue("+rv+"));ctx.lineTo(c.chartArea.right,yScale.getPixelForValue("+rv+"));ctx.stroke();ctx.restore();"
        if pos_dyn_stop_val is not None:
            dv = str(pos_dyn_stop_val)
            js_lines += "ctx.save();ctx.strokeStyle='#e05050';ctx.lineWidth=1;ctx.setLineDash([5,5]);ctx.beginPath();ctx.moveTo(c.chartArea.left,yScale.getPixelForValue("+dv+"));ctx.lineTo(c.chartArea.right,yScale.getPixelForValue("+dv+"));ctx.stroke();ctx.restore();"
        chart_js = "new Chart(document.getElementById('" + cid + "'),{type:'line',data:{labels:" + pos_dates + ",datasets:[{label:'Precio',data:" + pos_closes + ",borderColor:'#2a78d6',backgroundColor:'rgba(42,120,214,0.1)',borderWidth:2,fill:true,pointRadius:0,tension:0.1}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false}},scales:{x:{ticks:{color:tickColor,font:{size:9}},grid:{color:gridColor}}}},plugins:[{id:'hline',afterDraw:function(c){var yScale=c.scales.y;var ctx=c.ctx;" + js_lines + "}}]});"
        pos_charts_js.append(chart_js)

html += """  </div>
"""

# == CHARTS ROW 1 ==
chart_labels_short = json.dumps([p["ticker"].replace(".DE","").replace(".MI","") for p in portfolio])
chart_pnl_vals = json.dumps([round(p["pnl"], 2) for p in portfolio])
chart_weight_vals = json.dumps([round(p["weight"], 1) for p in portfolio])
chart_colors = json.dumps(["#2a78d6","#1baf7a","#eda100","#4a3aa7"])
pnl_colors_js = ",".join(f'"rgba(224,80,80,0.8)"' if p["pnl"] < 0 else '"rgba(62,207,142,0.8)"' for p in portfolio)

html += f"""  <div class="charts-row">
    <div class="chart-card">
      <div class="ctitle">Distribuci\u00f3n de cartera</div>
      <div class="legend-row">
"""
for p in portfolio:
    short_n = p["ticker"].replace(".DE","").replace(".MI","")
    html += f'        <span class="leg-item"><span class="leg-dot" style="background:{["#2a78d6","#1baf7a","#eda100","#4a3aa7"][portfolio.index(p)%4]}"></span>{short_n} {p["weight"]:.1f}%</span>\n'

html += f"""      </div>
      <div style="position:relative;height:200px"><canvas id="chartPeso" role="img" aria-label="Distribuci\u00f3n de la cartera por posici\u00f3n"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="ctitle">P&amp;L por posici\u00f3n (\u20ac)</div>
      <div style="position:relative;height:230px"><canvas id="chartPnl" role="img" aria-label="P&L por posici\u00f3n en euros"></canvas></div>
    </div>
  </div>

  <div class="bottom-row">
    <div class="info-card">
      <div class="ctitle">Alertas</div>
"""

# Alerts
if alerts:
    for a in alerts:
        is_danger = "danger" if "\u26a0" in a else "warn"
        is_danger = "danger" if "concentraci" in a.lower() or "defensivo" in a.lower() else is_danger
        html += f'      <div class="alert-item {is_danger}"><span>{"\u26a0" if is_danger=="danger" else "!"}</span><span>{a}</span></div>\n'
if not alerts:
    html += '      <div class="alert-item info"><span>\u2713</span><span>Sin alertas activas</span></div>\n'

html += f"""    </div>

    <div class="info-card">
      <div class="ctitle">Correlaci\u00f3n de precios (252d)</div>
      <table class="corr-table">
        <tr><th></th>
"""
for t2 in corr_tickers:
    short = t2.replace(".DE","").replace(".MI","")
    html += f"<th>{short}</th>\n"
html += "        </tr>\n"
# Use corr_html_rows but adapt classes to new CSS
corr_rows_adapted = corr_html_rows.replace('class="green"','class="corr-low"').replace('class="yellow"','class="corr-mid"').replace('class="red"','class="corr-high"')
html += corr_rows_adapted
html += """      </table>
      <div style="font-size:10px;color:#9aa0b0;margin-top:8px">Verde &lt;0.3 · Amarillo 0.3–0.7 · Rojo &gt;0.7</div>
    </div>

    <div class="info-card">
      <div class="ctitle">Tesis de inversi\u00f3n</div>
"""
for p in portfolio:
    t = tesis.get(p["ticker"], {})
    motivo = t.get("motivo", "")
    objetivo = t.get("precio_objetivo", "")
    obj_s = f"{objetivo:.2f} \u20ac" if isinstance(objetivo, (int, float)) else str(objetivo)
    cond = t.get("condicion_venta", "")
    html += f"""      <div class="tesis-item">
        <div class="tesis-ticker">{p['name']}</div>
        <div class="tesis-target">Objetivo: {obj_s} · Venta si {cond}</div>
        <div class="tesis-text">{motivo}</div>
      </div>
"""
html += """    </div>
  </div>

  <div class="charts-row">
    <div class="chart-card">
      <div class="ctitle">M\u00e9tricas de valoraci\u00f3n comparadas</div>
      <div style="position:relative;height:220px"><canvas id="chartVal" role="img" aria-label="Comparativa PER y P/B por posici\u00f3n"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="ctitle">ROE 2026 vs media sectorial</div>
      <div style="position:relative;height:220px"><canvas id="chartRoe" role="img" aria-label="ROE 2026 por empresa versus media sectorial"></canvas></div>
    </div>
  </div>
"""

# ========== EVENTS & VIGILANCIA + ALTERNATIVAS (dynamic JS) ==========
# Keep alt_signal_data collection for signal modal
alt_signal_data = []
# Prepare per-sector alternatives data (for signal modal PER < 15 only)
for p in portfolio:
    db_t = p["db_ticker"]
    if not db_t: continue
    row = df[df["Ticker"] == db_t]
    if row.empty: continue
    r = row.iloc[0]
    sec = r[sec_col_name]
    sec_df = df[df[sec_col_name] == sec].copy()
    sec_df = sec_df.drop_duplicates(subset="Empresa", keep="first")
    sec_df = sec_df[sec_df["2026 ROE"].notna() & sec_df["2026 EVA"].notna() & sec_df["2026 FCN"].notna()].copy()
    if len(sec_df) < 2: continue
    all_peer_tickers = df[df[sec_col_name] == sec]["Ticker"].unique()
    all_peer_vals = {}
    for pt in set(peer_ticker_to_yf(t) for t in all_peer_tickers):
        all_peer_vals[pt] = get_valuation(pt)
    def get_eper(tick):
        pv = all_peer_vals.get(peer_ticker_to_yf(tick), {})
        fwd = pv.get("fwd_per")
        if fwd and fwd > 0: return fwd
        tr = pv.get("per")
        if tr and tr > 0: return tr
        return None
    sec_df["_score"] = normalized_score(sec_df["2026 ROE"]) * 0.50 + normalized_score(sec_df["2026 EVA"]) * 0.25 + normalized_score(sec_df["2026 FCN"]) * 0.25
    sec_df = sec_df.sort_values("_score", ascending=False)
    if len(sec_df) > 10:
        sec_df = sec_df.head(10)
    for _, rw in sec_df.iterrows():
        t2 = rw["Ticker"]
        yf_t2 = peer_ticker_to_yf(t2)
        eper = get_eper(t2)
        if eper is not None and eper < 15:
            alt_signal_data.append((rw["Empresa"], yf_t2, sec))

html += """  <div class="section-title">Eventos &amp; Vigilancia</div>
  <div id="earnings-watchlist"><div class="ew-loading">Cargando eventos...</div></div>

  <div class="section-title">Alternativas por sector</div>
  <div style="font-size:11px;color:#9aa0b0;margin-bottom:14px;padding:8px 12px;background:#12151f;border-radius:8px;line-height:1.6">
    Filtros: mismos criterios que el Radar (fundamental + t\u00e9cnico)
    <span style="color:#5a5f6b;display:block;margin-top:2px;font-size:10px;">
      Score = ROE (50%) + EVA (25%) + FCF (25%), normalizado por sector.
    </span>
  </div>
  <div id="alternativas-container"><div class="ew-loading" style="opacity:1">Cargando alternativas...</div></div>

<div class="section-title">Radar \u2014 Oportunidades de Entrada</div>
<div id="radar-container"><div class="ew-loading">Cargando radar...</div></div>

<div class="section-title">Acciones en Estudio</div>
<div id="watchlist-container"><div class="ew-loading">Cargando watchlist...</div></div>

<div class="section-title">Fondos Indexados</div>
<div id="fondos-container"><div class="ew-loading">Cargando fondos...</div></div>

<div class="section-title">Cuenta Remunerada</div>
<div id="cuenta-container"><div class="ew-loading">Cargando cuenta...</div></div>
"""
# ========== HISTORIAL DE CARTERA ==========
hist_rows = ""
# Active positions
for p in portfolio:
    sup = f"{p.get('pos_support', 0):.2f}" if p.get("pos_support") else "N/D"
    pnl_cls_hist = "neg" if p["pnl"] < 0 else "pos"
    hist_rows += f"""<tr>
  <td>\U0001f7e2 Activa</td>
  <td>{p['entry_date']}</td>
  <td><strong>{p['name']}</strong></td>
  <td>{p['ticker']}</td>
  <td>{p['shares']}</td>
  <td>{p['entry']:.2f}</td>
  <td>{p['cost']:,.2f}</td>
  <td>{sup}</td>
  <td>{p['stop']:.2f}</td>
  <td style="color:{"#e05050" if p["pnl"] < 0 else "#3ecf8e"}">{p['current']:.2f}{" \u26a0" if p.get("data_error") else ""}</td>
  <td style="color:{"#e05050" if p["pnl"] < 0 else "#3ecf8e"}">{p['pnl']:+,.2f}</td>
  <td style="color:{"#e05050" if p["pnl"] < 0 else "#3ecf8e"}">{p['pnl_pct']:+.2f}%</td>
</tr>"""
# Closed positions
for cp in closed_positions:
    pnl_cls_c = "neg" if cp["pnl_eur"] < 0 else "pos"
    hist_rows += f"""<tr class="closed">
  <td>\U0001f534 Cerrada</td>
  <td>{cp['entry_date']}</td>
  <td><strong>{cp['name']}</strong></td>
  <td>{cp['ticker']}</td>
  <td>{cp['shares']}</td>
  <td>{cp['entry']:.2f}</td>
  <td>{cp['cost']:,.2f}</td>
  <td>{cp['support']:.2f}</td>
  <td>{cp['stop']:.2f}</td>
  <td style="color:{"#e05050" if cp["pnl_eur"] < 0 else "#3ecf8e"}">{cp['sale_price']:.2f}</td>
  <td style="color:{"#e05050" if cp["pnl_eur"] < 0 else "#3ecf8e"}">{cp['pnl_eur']:+,.2f}</td>
  <td style="color:{"#e05050" if cp["pnl_eur"] < 0 else "#3ecf8e"}">{cp['pnl_pct']:+.2f}%</td>
</tr>"""

hist_table = f"""  <div class="section-title">Historial de Cartera</div>
  <div style="font-size:11px;color:#9aa0b0;margin-bottom:14px;padding:8px 12px;background:#12151f;border-radius:8px;line-height:1.6">
    Rentabilidad global: {historical_pnl:+,.2f} \u20ac ({historical_return:+.2f}%) \u00b7 Inversi\u00f3n hist\u00f3rica total: {historical_cost:,.0f} \u20ac
  </div>
  <table class="hist-table">
    <thead><tr>
      <th>Estado</th><th>Fecha</th><th>Empresa</th><th>Ticker</th><th>N\u00ba Acc.</th><th>PC</th><th>Inversi\u00f3n (ii)</th><th>Soporte</th><th>Stop Loss</th><th>V.Actual/Venta</th><th>Rent. (\u20ac)</th><th>Rent. (%)</th>
    </tr></thead>
    <tbody>
{hist_rows}
    </tbody>
  </table>

"""
html += hist_table

html += """  <div class="footer">
    Generado autom\u00e1ticamente \u00b7 <span>yfinance</span> \u00b7 EVA con WACC 8% \u00b7 Precios en vivo al recargar
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const gridColor='rgba(255,255,255,0.06)';
const tickColor='#9aa0b0';

new Chart(document.getElementById('chartPeso'),{
  type:'doughnut',
  data:{
    labels:""" + chart_labels_short + """.map(function(l,i){return l+' '+""" + chart_weight_vals + """[i]+'%';}),
    datasets:[{data:""" + chart_weight_vals + """,backgroundColor:""" + chart_colors + """,borderColor:'#1a1d2e',borderWidth:3}]
  },
  options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:function(c){return c.label+': '+c.formattedValue+'%'}}}}}
});

new Chart(document.getElementById('chartPnl'),{
  type:'bar',
  data:{
    labels:""" + chart_labels_short + """,
    datasets:[{label:'P&L (€)',data:""" + chart_pnl_vals + """,backgroundColor:[""" + pnl_colors_js + """],borderRadius:4}]
  },
  options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:tickColor},grid:{color:gridColor}},y:{ticks:{color:tickColor,callback:function(v){return v+'€';}},grid:{color:gridColor}}}}
});

new Chart(document.getElementById('chartVal'),{
  type:'bar',
  data:{
    labels:""" + chart_labels_short + """,
    datasets:[
      {label:'PER',data:""" + json.dumps([round(v.get("per") or 0, 1) for v in [valuation.get(p["ticker"], {}) for p in portfolio]]) + """,backgroundColor:'rgba(42,120,214,0.8)',borderRadius:4},
      {label:'P/B',data:""" + json.dumps([round(v.get("pb") or 0, 2) for v in [valuation.get(p["ticker"], {}) for p in portfolio]]) + """,backgroundColor:'rgba(237,161,0,0.8)',borderRadius:4}
    ]
  },
  options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:tickColor},grid:{color:gridColor}},y:{ticks:{color:tickColor},grid:{color:gridColor}}}}
});

new Chart(document.getElementById('chartRoe'),{
  type:'bar',
  data:{
    labels:""" + json.dumps([p["ticker"].replace(".DE","").replace(".MI","") for p in portfolio if p.get("db_ticker")]) + """,
    datasets:[
      {label:'ROE empresa',data:""" + json.dumps([round(val_metric(df[df["Ticker"]==p["db_ticker"]].iloc[0]["2026 ROE"] if not df[df["Ticker"]==p["db_ticker"]].empty else None, -500, 500) or 0, 1) for p in portfolio if p.get("db_ticker")]) + """,backgroundColor:'rgba(62,207,142,0.8)',borderRadius:4},
      {label:'Media sector',data:""" + json.dumps([round(get_sector_metrics(df[df["Ticker"]==p["db_ticker"]].iloc[0][sec_col_name] if not df[df["Ticker"]==p["db_ticker"]].empty else "", df, sec_col_name)["roe_mean"], 1) for p in portfolio if p.get("db_ticker")]) + """,backgroundColor:'rgba(255,255,255,0.15)',borderRadius:4}
    ]
  },
  options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:tickColor},grid:{color:gridColor}},y:{ticks:{color:tickColor,callback:function(v){return v+'%';}},grid:{color:gridColor}}}}
});
""" + "\n".join(pos_charts_js) + """
</script>

<script>
// ========== Eventos & Vigilancia ==========
function renderEarningsWatchlist(data) {
  var c = document.getElementById('earnings-watchlist');
  if (data.error) { c.innerHTML = '<div class="ew-error">Error al cargar eventos</div>'; return; }
  if (!data.empresas || !data.empresas.length) { c.innerHTML = '<div class="ew-loading">Sin eventos pr\u00F3ximos</div>'; return; }
  var h = '<div class="ew-grid">';
  data.empresas.forEach(function(e) {
    var cls = 'ew-card';
    if (e.estado_global === 'venta') cls += ' ew-venta';
    else if (e.estado_global === 'alerta') cls += ' ew-alerta';
    else if (e.estado_global === 'sin_dato') cls += ' ew-sin-dato';
    var badgeHtml = '<span class="ew-badge ew-badge-' + (e.estado_global === 'ok' ? 'ok' : e.estado_global === 'alerta' ? 'alerta' : e.estado_global === 'venta' ? 'venta' : 'sin') + '">' +
      ({'ok': '\U0001F7E2 ok', 'alerta': '\U0001F7E1 alerta', 'venta': '\U0001F534 venta', 'sin_dato': '\u26AA sin dato'}[e.estado_global] || '\u26AA sin dato') + '</span>';
    h += '<div class="' + cls + '"><div class="ew-hdr"><div><div class="ew-ticker">' + e.nombre + ' <span style="color:#9aa0b0;font-size:12px">' + e.ticker + '</span></div><div class="ew-dias">' + e.fecha_earnings + ' \u00B7 ' + e.dias_hasta_earnings + ' d\u00EDas</div></div>' + badgeHtml + '</div>';
    h += '<table class="ew-table"><thead><tr><th>M\u00E9trica</th><th>Valor</th><th>Fuente</th><th>Alerta</th><th>Venta</th><th>Estado</th></tr></thead><tbody>';
    e.metricas.forEach(function(m) {
      var rowCls = 'ew-linea-' + (m.estado === 'sin_dato' ? 'sin' : m.estado === 'venta' ? 'venta' : m.estado === 'alerta' ? 'alerta' : 'ok');
      var valStr = m.valor !== null && m.valor !== undefined ? (m.formato === 'porcentaje' ? (m.valor * 100).toFixed(1) + '%' : m.valor.toFixed(1) + 'x') : 'N/D';
      var srcCls = 'ew-fuente ew-fuente-' + m.fuente;
      var srcLabel = ({'fmp':'FMP','yfinance':'YF','xlsx':'XLSX','sin_dato':'N/D'})[m.fuente] || m.fuente;
      var alertaStr = m.umbral_alerta !== null ? (m.formato === 'porcentaje' ? (m.umbral_alerta * 100).toFixed(1) + '%' : m.umbral_alerta.toFixed(1) + 'x') : 'N/D';
      var ventaStr = m.umbral_venta !== null ? (m.formato === 'porcentaje' ? (m.umbral_venta * 100).toFixed(1) + '%' : m.umbral_venta.toFixed(1) + 'x') : 'N/D';
      var estadoIcon = ({'ok':'\U0001F7E2','alerta':'\U0001F7E1','venta':'\U0001F534','sin_dato':'\u26AA'})[m.estado] || '\u26AA';
      h += '<tr class="' + rowCls + '"><td>' + m.nombre + '</td><td><strong>' + valStr + '</strong></td><td><span class="' + srcCls + '">' + srcLabel + '</span></td><td>' + alertaStr + '</td><td>' + ventaStr + '</td><td>' + estadoIcon + '</td></tr>';
    });
    h += '</tbody></table>';
    h += '<div class="ew-cond">' + e.condicion_venta + '</div></div>';
  });
  h += '</div><div class="ew-note">M\u00E9tricas obtenidas v\u00EDa FMP/yfinance. Las condiciones cualitativas requieren verificaci\u00F3n manual el d\u00EDa de resultados.</div>';
  c.innerHTML = h;
}

// ========== Alternativas por sector ==========
function renderAlternatives(data) {
  var c = document.getElementById('alternativas-container');
  if (data.error) { c.innerHTML = '<div class="ew-error">Radar no disponible temporalmente</div>'; return; }
  if (!data.sectores || !data.sectores.length) { c.innerHTML = '<div class="ew-loading">Sin alternativas disponibles</div>'; return; }
  var h = '';
  data.sectores.forEach(function(s) {
    h += '<div style="margin-bottom:20px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">';
    h += '<h3 style="color:#e8eaed;font-size:14px;font-weight:600;margin:0">' + s.sector + '</h3>';
    h += '<span style="color:#9aa0b0;font-size:11px">' + s.n_analizadas + ' empresas analizadas</span></div>';
    h += '<table class="alt-table"><thead><tr><th>Empresa</th><th>Ticker</th><th>Score</th><th>PER</th><th>Rent.1A</th><th>Se\u00F1al</th></tr></thead><tbody>';
    if (s.empresas && s.empresas.length) {
      s.empresas.forEach(function(r) {
        var scorePct = Math.max(1, Math.min(100, r.score * 100));
        var scoreCol = r.score > 0.6 ? '#3ecf8e' : r.score > 0.3 ? '#f0a500' : '#e05050';
        var rentCol = r.rent_1a > 0 ? '#3ecf8e' : '#e05050';
        var eperStr = r.eper !== null ? r.eper.toFixed(1) + 'x' : 'N/D';
        var rentStr = r.rent_1a !== null ? (r.rent_1a > 0 ? '+' : '') + r.rent_1a.toFixed(1) + '%' : 'N/D';
        var entryBadges = '';
        if (r.entry_types && r.entry_types.length) {
          r.entry_types.forEach(function(t) { entryBadges += '<span class="badge-alt" style="margin:1px 2px">' + t + '</span>'; });
        }
        h += '<tr><td><strong>' + r.name + '</strong></td><td>' + r.ticker + '</td>';
        h += '<td><div class="score-bar-bg"><div class="score-bar-fill" style="width:' + scorePct + '%;background:' + scoreCol + '"></div></div> ' + r.score.toFixed(3) + '</td>';
        h += '<td>' + eperStr + '</td><td style="color:' + rentCol + '">' + rentStr + '</td><td>' + entryBadges + '</td></tr>';
      });
    } else {
      h += '<tr><td colspan="6" style="color:#5a5f6b;text-align:center">' + (s.error ? 'Error al procesar sector' : 'Sin candidatos') + '</td></tr>';
    }
    h += '</tbody></table></div>';
  });
  c.innerHTML = h;
}

// ========== Radar ==========
function renderRadar(data) {
  var c = document.getElementById('radar-container');
  if (data.error || !data.oportunidades || !data.oportunidades.length) {
    c.innerHTML = '<div class="ew-loading">Radar no disponible</div>'; return;
  }
  var now = new Date();
  var updatedStr = data.updated ? data.updated.substr(0, 16).replace('T', ' ') : now.toLocaleString();
  var h = '<div style="font-size:11px;color:#9aa0b0;margin-bottom:14px;padding:8px 12px;background:#12151f;border-radius:8px;line-height:1.6">';
  h += '\u00DAltimo an\u00E1lisis: ' + updatedStr;
  h += '<span style="color:#5a5f6b;display:block;margin-top:2px;font-size:10px">';
  h += 'Criterios: Rent.1A > 30%, PER 0\u201330, Score > 0.3, EVA > 0, ROE > 0, Soporte en vigor \u00B7 No en cartera \u00B7 T\u00E9cnico: RRA/RR/LT/LTA/PER';
  h += '</span></div>';
  h += '<table class="alt-table"><thead><tr><th>#</th><th>Empresa</th><th>Ticker</th><th>Sector</th><th>Score</th><th>PER</th><th>Rent.1A</th><th>ROE</th><th>EVA</th><th>FCF</th><th>Soporte</th><th>Tipo Entrada</th></tr></thead><tbody>';
  data.oportunidades.forEach(function(r, i) {
    var eperStr = r.eper !== null ? r.eper.toFixed(1) + 'x' : 'N/D';
    var roeStr = r.roe !== null ? r.roe.toFixed(1) + '%' : 'N/D';
    var evaStr = r.eva !== null ? Number(r.eva).toLocaleString() : 'N/D';
    var fcfStr = r.fcf !== null ? Number(r.fcf).toLocaleString() : 'N/D';
    var rentCol = r.rent_1a > 0 ? '#3ecf8e' : '#e05050';
    var rentStr = r.rent_1a !== null ? (r.rent_1a > 0 ? '+' : '') + r.rent_1a.toFixed(1) + '%' : 'N/D';
    var scorePct = Math.max(1, Math.min(100, r.score * 100));
    var scoreCol = r.score > 0.6 ? '#3ecf8e' : r.score > 0.3 ? '#f0a500' : '#e05050';
    var entryBadges = '';
    if (r.entry_types && r.entry_types.length) {
      r.entry_types.forEach(function(t) { entryBadges += '<span class="badge-alt" style="margin:1px 2px">' + t + '</span>'; });
    }
    var soporteStr = r.support !== null ? r.support.toFixed(2) + ' \u20AC' : 'N/D';
    var soporteCol = (r.current_price && r.support && r.current_price > r.support) ? '#3ecf8e' : (r.current_price && r.support && r.current_price <= r.support) ? '#e05050' : '#5a5f6b';
    h += '<tr><td>' + (i+1) + '</td><td><strong>' + r.name + '</strong></td><td>' + r.ticker + '</td><td>' + (r.sector || '') + '</td>';
    h += '<td><div class="score-bar-bg"><div class="score-bar-fill" style="width:' + scorePct + '%;background:' + scoreCol + '"></div></div> ' + r.score.toFixed(3) + '</td>';
    h += '<td>' + eperStr + '</td><td style="color:' + rentCol + '">' + rentStr + '</td>';
    h += '<td style="color:' + (r.roe >= 15 ? '#3ecf8e' : r.roe >= 5 ? '#f0a500' : '#e05050') + '">' + roeStr + '</td>';
    h += '<td>' + evaStr + '</td><td>' + fcfStr + '</td>';
    h += '<td style="color:' + soporteCol + '">' + soporteStr + '</td><td>' + entryBadges + '</td></tr>';
  });
  h += '</tbody></table>';
  h += '<div class="alt-note">' + data.total + ' oportunidades encontradas. Score ponderado (0\u20131): ROE 50% / EVA 25% / FCF 25%. Rent.1A > 30%, Soporte en vigor, PER \u2264 30.</div>';
  c.innerHTML = h;
}

// ========== Watchlist (Acciones en Estudio) ==========
function renderWatchlist(data) {
  var c = document.getElementById('watchlist-container');
  if (data.error || !data.items || !data.items.length) {
    c.innerHTML = '<div class="ew-loading">Watchlist vac\u00EDa o no disponible</div>'; return;
  }
  var h = '<table class="alt-table"><thead><tr><th>Empresa</th><th>Nivel / Precio</th><th>Distancia</th><th>Se\u00F1al</th><th>Soporte</th><th>F1/F2/F3</th><th>Estado</th><th>Notas</th></tr></thead><tbody>';
  data.items.forEach(function(r) {
    var priceStr = r.current_price !== null ? r.current_price.toFixed(2) + ' \u20ac' : 'N/D';
    var levelStr = r.entry_level.toFixed(2) + ' \u20ac';
    var distStr = r.distance_pct !== null ? (r.distance_pct >= 0 ? '+' : '') + r.distance_pct.toFixed(1) + '%' : 'N/D';
    var distColor = '#9aa0b0';
    if (r.distance_pct !== null) {
      var ad = Math.abs(r.distance_pct);
      if (ad <= 5) distColor = '#3ecf8e';
      else if (ad <= 10) distColor = '#f0a500';
      else distColor = '#e05050';
    }
    var signalDetected = r.signal_active ? '\u2705 ' + r.entry_signal : '\u274c ' + r.entry_signal;
    var supportStr = r.support_ok ? '\u2705 ' + (r.support !== null ? r.support.toFixed(2) + ' \u20ac' : '') : '\u274c ' + (r.support !== null ? r.support.toFixed(2) + ' \u20ac' : 'N/D');
    var f1 = r.f1_ok ? '\u2705' : '\u274c';
    var f2 = r.f2_ok ? '\u2705' : '\u274c';
    var f3 = r.f3_ok ? '\u2705' : '\u274c';
    var filterStr = (r.signal_active ? '' : '\u23f3 ') + 'F1' + f1 + ' F2' + f2 + ' F3' + f3;
    var statusMap = {
      'confirmado': ['\U0001F7E2 Confirmado', '#3ecf8e', '#1a3d2e'],
      'activa': ['\U0001F7E1 Activa', '#f0a500', '#2d2a1a'],
    };
    var st = statusMap[r.visual_status] || ['\U0001F534 Sin se\u00F1al', '#e05050', '#3d1a1a'];
    h += '<tr><td><strong>' + r.name + '</strong><br><span style="color:#9aa0b0;font-size:11px">' + r.ticker + '</span></td>';
    h += '<td>' + levelStr + '<br><span style="color:#9aa0b0;font-size:11px">' + priceStr + '</span></td>';
    h += '<td style="color:' + distColor + ';font-weight:700">' + distStr + '</td>';
    h += '<td style="font-size:11px">' + signalDetected + '</td>';
    h += '<td style="font-size:11px">' + supportStr + '</td><td style="font-size:11px">' + filterStr + '</td>';
    h += '<td><span style="display:inline-block;padding:3px 10px;border-radius:6px;font-size:11px;font-weight:700;background:' + st[2] + ';color:' + st[1] + ';border:1px solid ' + st[1] + '">' + st[0] + '</span></td>';
    h += '<td style="font-size:11px;color:#9aa0b0">' + (r.notes || '') + '</td></tr>';
  });
  h += '</tbody></table>';
  c.innerHTML = h;
}

// ========== Fondos Indexados ==========
function renderFondos(data) {
  var c = document.getElementById('fondos-container');
  if (data.error || !data.fondos || !data.fondos.length) {
    c.innerHTML = '<div class="ew-loading">Sin fondos registrados</div>'; return;
  }
  var h = '<div class="fondos-grid">';
  data.fondos.forEach(function(f, idx) {
    var cls = 'fondo-card';
    var rentCls = f.rentabilidad >= 0 ? 'pos' : 'neg';
    // Badge tipo
    var tipoBadge = f.tipo === 'etf'
      ? '<span class="badge-tu" style="margin-left:6px;font-size:10px">ETF</span>'
      : '<span class="badge-alt" style="margin-left:6px;font-size:10px">Fondo</span>';
    // Badge frescura
    var hoy = new Date();
    var hoyStr = hoy.getFullYear() + '-' + String(hoy.getMonth()+1).padStart(2,'0') + '-' + String(hoy.getDate()).padStart(2,'0');
    var esHoy = f.fecha_actualizacion >= hoyStr;
    var freshBadge = esHoy
      ? '<span class="ew-badge ew-badge-ok" style="font-size:10px;margin-left:6px">Actualizado hoy</span>'
      : '<span class="ew-badge ew-badge-alerta" style="font-size:10px;margin-left:6px">\u00daltimo cierre: ' + f.fecha_actualizacion + '</span>';
    h += '<div class="' + cls + '">';
    h += '<div class="fondo-header"><div><div class="fondo-nombre">' + f.nombre + tipoBadge + freshBadge + '</div><div class="fondo-isin">' + f.isin + '</div></div></div>';
    h += '<div class="fondo-metric"><span class="ml">Aportado</span><span class="mv">' + f.aportado.toLocaleString('es-ES', {minimumFractionDigits:2}) + ' \u20ac</span></div>';
    h += '<div class="fondo-metric"><span class="ml">Valor actual</span><span class="mv">' + f.valor_actual.toLocaleString('es-ES', {minimumFractionDigits:2}) + ' \u20ac</span></div>';
    h += '<div class="fondo-metric"><span class="ml">Rentabilidad</span><span class="mv ' + rentCls + '">' + (f.rentabilidad >= 0 ? '+' : '') + f.rentabilidad.toFixed(2) + '%</span></div>';
    h += '<div class="fondo-metric"><span class="ml">TER</span><span class="mv">' + f.ter.toFixed(2) + '%</span></div>';
    // Grafica evolucion si hay historico
    if (f.historico_prices && f.historico_prices.length > 1) {
      var cid = 'fondoChart_' + idx;
      h += '<div style="position:relative;height:140px;margin-top:10px"><canvas id="' + cid + '"></canvas></div>';
      setTimeout(function() {
        var hist = f.historico_prices;
        var labels = hist.map(function(h) { return h.fecha.slice(5); });
        var prices = hist.map(function(h) { return h.precio; });
        new Chart(document.getElementById(cid), {
          type:'line',
          data:{labels:labels, datasets:[{
            label:f.nombre, data:prices,
            borderColor:'#2a78d6', backgroundColor:'rgba(42,120,214,0.1)',
            borderWidth:2, fill:true, pointRadius:0, tension:0.1
          }]},
          options:{
            responsive:true, maintainAspectRatio:false,
            plugins:{legend:{display:false}, tooltip:{mode:'index', intersect:false}},
            scales:{x:{ticks:{color:'#5a5f6b', font:{size:9}}, grid:{color:'#1a1d2e'}},
                    y:{ticks:{color:'#5a5f6b', font:{size:9}}, grid:{color:'#1a1d2e'}}}
          }
        });
      }, 100);
    }
    // Warn ESG
    if (f.nombre.toUpperCase().indexOf('ESG') >= 0) {
      h += '<div class="radar-warn">\u26a0 Este fondo replica un \u00edndice ESG, no el \u00edndice puro</div>';
    }
    h += '</div>';
  });
  h += '</div>';
  // Radar comparativo
  if (data.radar) {
    h += '<button class="radar-toggle" onclick="var t=this.nextElementSibling;t.style.display=t.style.display===&quot;block&quot;?&quot;none&quot;:&quot;block&quot;;this.textContent=t.style.display===&quot;block&quot;?&quot;Ocultar comparativa&quot;:&quot;Comparar con alternativas&quot;">Comparar con alternativas</button>';
    h += '<div style="display:none">';
    h += '<table class="radar-table"><thead><tr><th>Fondo</th><th>TER</th><th>Rent. 3a</th><th>R\u00e9plica</th></tr></thead><tbody>';
    var all = [];
    data.fondos.forEach(function(f) { all.push({nombre: f.nombre, ter: f.ter, rentabilidad_3a: null, replica: 'propio', esPropio: true}); });
    if (data.radar.alternativas_sp500) {
      data.radar.alternativas_sp500.forEach(function(a) { all.push({nombre: a.nombre, ter: a.ter, rentabilidad_3a: a.rentabilidad_3a, replica: a.replica, esPropio: false}); });
    }
    if (data.radar.alternativas_msci_world) {
      data.radar.alternativas_msci_world.forEach(function(a) { all.push({nombre: a.nombre, ter: a.ter, rentabilidad_3a: a.rentabilidad_3a, replica: a.replica, esPropio: false}); });
    }
    all.forEach(function(a) {
      var trCls = a.esPropio ? ' class="tr-own"' : '';
      var terCls = a.ter > 0.12 ? 'ter-high' : 'ter-low';
      h += '<tr' + trCls + '><td>' + a.nombre + '</td><td class="' + terCls + '">' + a.ter.toFixed(2) + '%</td><td>' + (a.rentabilidad_3a !== null ? a.rentabilidad_3a.toFixed(2) + '%' : 'N/D') + '</td><td>' + a.replica + '</td></tr>';
    });
    h += '</tbody></table>';
    h += '<div style="font-size:10px;color:#5a5f6b;margin-top:6px">Fila destacada = tu fondo. TER en rojo si es mayor que alternativas.</div>';
    h += '<div style="font-size:10px;color:#9aa0b0;margin-top:8px;padding:6px 10px;background:#12151f;border-radius:6px;line-height:1.6">';
    h += '<b>TER</b> — Comisi\u00f3n anual del fondo. Se paga siempre, gane o pierda el mercado.<br>';
    h += '<b>Rent. 3a</b> — Rendimiento hist\u00f3rico, no garantiza el futuro.<br>';
    h += '<b>R\u00e9plica</b> — F\u00edsica = compra acciones reales. Sint\u00e9tica = usa derivados, m\u00e1s riesgo oculto.';
    h += '</div>';
    h += '</div>';
  }
  c.innerHTML = h;
}

// ========== Cuenta Remunerada ==========
function renderCuenta(data) {
  var c = document.getElementById('cuenta-container');
  if (data.error) {
    c.innerHTML = '<div class="ew-loading">Cuenta no disponible</div>'; return;
  }
  var taePct = data.tae_actual.toFixed(0) + '%';
  var saldoLabel = data.saldo_desactualizado
    ? 'Saldo a ' + data.fecha_ultima_actualizacion
    : 'Saldo actual';
  var notaAntiguedad = data.saldo_desactualizado
    ? '<div style="font-size:10px;color:#f0a500;margin-bottom:4px">\u26a0 \u00daltimo saldo conocido: ' + data.fecha_ultima_actualizacion + '</div>'
    : '<div style="font-size:10px;color:#3ecf8e;margin-bottom:4px">\u2714 Actualizado hoy</div>';
  var h = '<div class="cuenta-card">';
  h += '<div class="cuenta-sub">' + (data.entidad || 'Cuenta Remunerada') + '</div>';
  h += notaAntiguedad;
  h += '<div class="cuenta-saldo">' + data.saldo_actual.toLocaleString('es-ES', {minimumFractionDigits:2}) + ' \u20ac</div>';
  h += '<div class="cuenta-row"><span class="ml">TAE</span><span class="mv" style="color:#3ecf8e">' + taePct + '</span></div>';
  h += '<div class="cuenta-row"><span class="ml">Inter\u00e9s diario est.</span><span class="mv">' + data.interes_diario_estimado.toFixed(2) + ' \u20ac</span></div>';
  h += '<div class="cuenta-row"><span class="ml">Intereses acumulados</span><span class="mv" style="color:#3ecf8e">+' + data.intereses_acumulados_periodo.toFixed(2) + ' \u20ac</span></div>';
  h += '<div class="cuenta-row"><span class="ml">Tracking desde</span><span class="mv">' + data.fecha_inicio_tracking + '</span></div>';
  h += '</div>';
  c.innerHTML = h;
}

// ========== Live prices ==========
function updatePrices(data) {
  if (data.error || !data.prices) { console.warn('[prices] error:', data.error); return; }
  var cards = document.querySelectorAll('.pos-card');
  if (!cards.length) { console.warn('[prices] no .pos-card found'); return; }
  var anyFail = false;
  cards.forEach(function(card, i) {
    var tk = card.getAttribute('data-ticker');
    var entry = parseFloat(card.getAttribute('data-entry'));
    var shares = parseFloat(card.getAttribute('data-shares'));
    var stop = parseFloat(card.getAttribute('data-stop'));
    var pd = data.prices[tk];
    if (!pd || pd.current == null) { anyFail = true; return; }
    var cur = pd.current;
    var cost = entry * shares;
    var value = cur * shares;
    var pnl = value - cost;
    var pnlPct = cost ? (pnl / cost) * 100 : 0;
    var distStop = cur ? ((cur - stop) / cur) * 100 : 0;
    // Update displayed values
    var priceEl = card.querySelector('.price-val');
    var pnlEl = card.querySelector('.pnl-val');
    var pnlPctEl = card.querySelector('.pnl-pct-val');
    if (priceEl) priceEl.textContent = cur.toFixed(2);
    if (pnlEl) pnlEl.textContent = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
    if (pnlPctEl) pnlPctEl.textContent = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2);
    // Color update
    var isNeg = pnl < 0;
    var currentDiv = card.querySelector('.current');
    var pnlDiv = card.querySelector('.pnl');
    if (currentDiv) currentDiv.style.color = isNeg ? '#e05050' : '#3ecf8e';
    if (pnlDiv) pnlDiv.style.color = isNeg ? '#e05050' : '#3ecf8e';
    // Update distance to stop
    var distEl = card.querySelector('[data-metric="dist-stop"]');
    if (distEl) {
      var distPct = ((cur - stop) / cur) * 100;
      distEl.textContent = distPct.toFixed(1) + '%';
      distEl.className = 'mv ' + (distPct < 10 ? 'warn' : distPct < 5 ? 'neg' : 'green');
    }
    // Remove data_error warning if it exists (price came through)
    var warn = card.querySelector('[title="Dato no actualizado"]');
    if (warn) warn.remove();
  });
  // Recalc top KPI row
  var totalValue = 0, totalCost = 0, totalDayVar = 0, nDayVar = 0;
  cards.forEach(function(card) {
    var entry = parseFloat(card.getAttribute('data-entry'));
    var shares = parseFloat(card.getAttribute('data-shares'));
    var tk = card.getAttribute('data-ticker');
    var pd = data.prices[tk];
    var cost = entry * shares;
    totalCost += cost;
    if (pd && pd.current != null) {
      totalValue += pd.current * shares;
      if (pd.day_var != null) {
        totalDayVar += pd.day_var * shares;
        nDayVar++;
      }
    } else {
      totalValue += cost;  // fallback to cost for missing prices
    }
  });
  var totalPnl = totalValue - totalCost;
  var totalPnlPct = totalCost ? (totalPnl / totalCost) * 100 : 0;
  var dayPct = totalValue && nDayVar ? (totalDayVar / totalValue) * 100 : null;
  // Update KPI elements
  var fmt = function(n){ return n.toLocaleString('es-ES', {minimumFractionDigits:0, maximumFractionDigits:0}); };
  var fmt2 = function(n){ return n.toLocaleString('es-ES', {minimumFractionDigits:2, maximumFractionDigits:2}); };
  var el = document.querySelector('[data-kpi-val="total-cost"]');
  if (el) el.textContent = fmt(totalCost) + ' \u20ac';
  el = document.querySelector('[data-kpi-val="total-value"]');
  if (el) {
    el.textContent = fmt(totalValue) + ' \u20ac';
    el.className = 'value';
  }
  el = document.querySelector('[data-kpi-val="total-pnl"]');
  if (el) {
    el.textContent = (totalPnl >= 0 ? '+' : '') + fmt2(totalPnl) + ' \u20ac';
    el.className = 'value ' + (totalPnl < 0 ? 'neg' : 'pos');
  }
  el = document.querySelector('[data-kpi-val="total-pnl-pct"]');
  if (el) {
    el.textContent = (totalPnlPct >= 0 ? '+' : '') + fmt2(totalPnlPct) + '%';
    el.className = 'value ' + (totalPnlPct < 0 ? 'neg' : 'pos');
  }
  // Update historical KPI
  var histCard = document.querySelector('[data-kpi="historical"]');
  if (histCard) {
    var closedPnl = parseFloat(histCard.getAttribute('data-closed-pnl'));
    var closedCost = parseFloat(histCard.getAttribute('data-closed-cost'));
    var historicalPnl = totalPnl + closedPnl;
    var historicalCost = totalCost + closedCost;
    var historicalReturn = historicalCost ? (historicalPnl / historicalCost) * 100 : 0;
    el = document.querySelector('[data-kpi-val="historical-return"]');
    if (el) {
      el.textContent = (historicalReturn >= 0 ? '+' : '') + fmt2(historicalReturn) + '%';
      el.className = 'value ' + (historicalReturn < 0 ? 'neg' : 'pos');
    }
    el = document.querySelector('[data-kpi-val="historical-pnl"]');
    if (el) {
      el.textContent = (historicalPnl >= 0 ? '+' : '') + fmt2(historicalPnl) + ' \u20ac / ' + fmt(historicalCost) + ' \u20ac invertidos';
    }
  }
  el = document.querySelector('[data-kpi-val="day-pct"]');
  if (el && dayPct !== null) {
    el.textContent = (dayPct >= 0 ? '+' : '') + fmt2(dayPct) + '%';
    el.className = 'value ' + (dayPct < 0 ? 'neg' : 'pos');
  }
  el = document.querySelector('[data-kpi-val="day-eur"]');
  if (el) {
    el.textContent = (totalDayVar >= 0 ? '+' : '') + fmt2(totalDayVar) + ' \u20ac';
  }
  // Update Rendimiento Total KPI (depende de totalPnl que cambia con precios)
  // Rendimiento Total CON riesgo (acciones + fondos, sin cuenta)
  el = document.querySelector('[data-kpi="rend-total-risk"]');
  if (el) {
    var rf = parseFloat(el.getAttribute('data-rend-fondos-eur')) || 0;
    var aportF = parseFloat(el.getAttribute('data-total-aportado-fondos')) || 0;
    var cPnl = parseFloat(el.getAttribute('data-closed-pnl')) || 0;
    var cCost = parseFloat(el.getAttribute('data-closed-cost')) || 0;
    var rteur = (totalPnl + cPnl) + rf;
    var rtcost = (totalCost + cCost) + aportF;
    var rtpct = rtcost ? (rteur / rtcost) * 100 : 0;
    var pctEl = el.querySelector('[data-kpi-val="rend-total-risk-pct"]');
    if (pctEl) {
      pctEl.textContent = (rtpct >= 0 ? '+' : '') + fmt2(rtpct) + '%';
      pctEl.className = 'value ' + (rteur < 0 ? 'neg' : 'pos');
    }
    var subEl = el.querySelector('[data-kpi-val="rend-total-risk-sub"]');
    if (subEl) {
      subEl.textContent = (rteur >= 0 ? '+' : '') + fmt2(rteur) + ' \u20ac / ' + fmt(rtcost) + ' \u20ac invertidos';
    }
  }
  // Update per-card weight metrics
  cards.forEach(function(card) {
    var tk = card.getAttribute('data-ticker');
    var shares = parseFloat(card.getAttribute('data-shares'));
    var pd = data.prices[tk];
    var cur = pd && pd.current != null ? pd.current : null;
    var weightEl = card.querySelector('[data-metric="weight"]');
    if (weightEl && cur !== null && totalValue > 0) {
      var w = (cur * shares / totalValue) * 100;
      weightEl.textContent = w.toFixed(1) + '%' + (w > 25 ? ' \u26a0' : '');
      weightEl.className = 'mv' + (w > 25 ? ' warn' : w > 20 ? '' : '');
    }
  });

  el = document.querySelector('[data-kpi-val="vs-benchmark"]');
  if (el) {
    var benchCard = document.querySelector('[data-kpi="vs-benchmark"]');
    var benchStart = benchCard ? parseFloat(benchCard.getAttribute('data-bench-start')) : null;
    var benchPrice = data.prices && data.prices['^STOXX50E'];
    if (benchStart && benchPrice && benchPrice.current != null) {
      var benchRet = (benchPrice.current / benchStart - 1) * 100;
      var benchSub = el.parentElement.querySelector('[data-kpi-sub="benchmark-ret"]');
      var vs = totalPnlPct - benchRet;
      el.textContent = (vs >= 0 ? '+' : '') + vs.toFixed(2) + '%';
      el.className = 'value ' + (vs < 0 ? 'neg' : 'pos');
      if (benchSub) benchSub.textContent = '\u00cdndice ' + (benchRet >= 0 ? '+' : '') + benchRet.toFixed(2) + '%';
    }
  }

  // Update historial table active rows
  var histRows = document.querySelectorAll('.hist-table tbody tr:not(.closed)');
  if (histRows.length) {
    histRows.forEach(function(row) {
      var cells = row.querySelectorAll('td');
      if (cells.length < 12) return;
      var tk = cells[3].textContent.trim();
      var shares = parseFloat(cells[4].textContent.trim().replace(/,/g, ''));
      var entry = parseFloat(cells[5].textContent.trim().replace(/,/g, ''));
      var invest = parseFloat(cells[6].textContent.trim().replace(/,/g, ''));
      var pd = data.prices[tk];
      if (!pd || pd.current == null) { anyFail = true; return; }
      var cur = pd.current;
      var pnl = (cur * shares) - invest;
      var pnlPct = invest ? (pnl / invest) * 100 : 0;
      var isNeg = pnl < 0;
      var color = isNeg ? '#e05050' : '#3ecf8e';
      cells[9].textContent = cur.toFixed(2);
      cells[9].style.color = color;
      cells[10].textContent = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
      cells[10].style.color = color;
      cells[11].textContent = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%';
      cells[11].style.color = color;
    });
  }

  if (anyFail) {
    var hdr = document.querySelector('.header .date-info');
    if (hdr && !hdr.querySelector('.price-warn')) {
      hdr.innerHTML += '<br><span class="price-warn" style="color:#f0a500;font-size:11px">\u26a0 Algunos precios no se actualizaron</span>';
    }
  }
}

document.addEventListener('DOMContentLoaded', function() {
  var fetchCount = 0;
  var fetchPrices = function() {
    var url = (fetchCount % 3 === 0) ? '/api/prices?refresh=1' : '/api/prices';
    fetchCount++;
    fetch(url).then(function(r){ return r.json(); }).then(updatePrices).catch(function(e){
      console.warn('[prices] fetch error:', e);
      var hdr = document.querySelector('.header .date-info');
      if (hdr && !hdr.querySelector('.price-warn')) {
        hdr.innerHTML += '<br><span class="price-warn" style="color:#e05050;font-size:11px">\u26a0 Precios no disponibles</span>';
      }
    });
  };
  fetchPrices();
  setInterval(fetchPrices, 30000);  // poll every 30s
  fetch('/api/earnings-watchlist').then(function(r){ return r.json(); }).then(renderEarningsWatchlist).catch(function(){ document.getElementById('earnings-watchlist').innerHTML = '<div class="ew-error">Error de conexi\u00F3n</div>'; });
  fetch('/api/alternatives').then(function(r){ return r.json(); }).then(renderAlternatives).catch(function(){ document.getElementById('alternativas-container').innerHTML = '<div class="ew-error">Error de conexi\u00F3n</div>'; });
  fetch('/api/radar').then(function(r){ return r.json(); }).then(renderRadar).catch(function(){ document.getElementById('radar-container').innerHTML = '<div class="ew-error">Error de conexi\u00F3n</div>'; });
  fetch('/api/watchlist').then(function(r){ return r.json(); }).then(renderWatchlist).catch(function(){ document.getElementById('watchlist-container').innerHTML = '<div class="ew-error">Error de conexi\u00F3n</div>'; });
  fetch('/api/fondos').then(function(r){ return r.json(); }).then(renderFondos).catch(function(){ document.getElementById('fondos-container').innerHTML = '<div class="ew-error">Error al cargar fondos</div>'; });
  fetch('/api/cuenta-remunerada').then(function(r){ return r.json(); }).then(renderCuenta).catch(function(){ document.getElementById('cuenta-container').innerHTML = '<div class="ew-error">Error al cargar cuenta</div>'; });
  fetch('/api/prices').then(function(r){ return r.json(); }).then(updatePrices).catch(function(){
    var hdr = document.querySelector('.header .date-info');
    if (hdr && !hdr.querySelector('.price-warn')) {
      hdr.innerHTML += '<br><span class="price-warn" style="color:#e05050;font-size:11px">\u26a0 Precios no disponibles</span>';
    }
  });
});
</script>

<!-- SIGNAL MODAL -->
<style>
.sig-overlay{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:9999;display:none;align-items:flex-start;justify-content:center;padding-top:60px;backdrop-filter:blur(4px)}
.sig-box{background:#1a1d2e;border-radius:12px;width:90%;max-width:700px;max-height:80vh;overflow-y:auto;padding:28px;border:1px solid #2a2d3e;box-shadow:0 8px 40px rgba(0,0,0,0.5)}
.sig-close{float:right;background:none;border:none;color:#9aa0b0;font-size:22px;cursor:pointer;padding:0;line-height:1}
.sig-title{color:#e8eaed;font-size:18px;font-weight:600;margin:0 0 6px}
.sig-sub{color:#9aa0b0;font-size:12px;margin-bottom:20px;line-height:1.5}
.sig-sec{margin-bottom:20px}
.sig-hdr{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.sig-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.sig-lbl{color:#e8eaed;font-size:13px;font-weight:600}
.sig-desc{color:#9aa0b0;font-size:11px;flex:1}
.sig-list{list-style:none;padding:0;margin:0}
.sig-list li{padding:4px 0;color:#b0b5c0;font-size:12px;border-bottom:1px solid #232638}
.sig-list li:last-child{border-bottom:none}
.sig-ftr{text-align:center;color:#5a5f6b;font-size:11px;margin-top:16px;padding-top:12px;border-top:1px solid #232638}
</style>
<div id="sigModal" class="sig-overlay"><div class="sig-box" onclick="event.stopPropagation()">
<button class="sig-close" onclick="closeSigModal()">&times;</button>
<div class="sig-title">Se\u00f1ales de entrada</div>
<div class="sig-sub">Compa\u00f1\u00edas agrupadas por tipo de se\u00f1al t\u00e9cnica calculada sobre hist\u00f3rico de precios</div>
<div id="sigContent"></div>
<div class="sig-ftr">Autom\u00e1tico · Se muestra una vez por sesi\u00f3n</div>
</div></div>
<script>
const SIG_DATA = SIGNAL_DATA_JSON;
const SIG_DSC = SIGNAL_DESC_JSON;
function closeSigModal(){document.getElementById('sigModal').style.display='none';sessionStorage.setItem('sigClosed','1')}
document.addEventListener('DOMContentLoaded',function(){
  if(sessionStorage.getItem('sigClosed'))return;
  var c=document.getElementById('sigContent'),h='',any=false;
  for(var t in SIG_DATA){var items=SIG_DATA[t];if(!items||!items.length)continue;any=true;
    var d=SIG_DSC[t]||{label:t,desc:'',color:'#9aa0b0'};
    h+='<div class="sig-sec"><div class="sig-hdr"><span class="sig-dot" style="background:'+d.color+'"></span><span class="sig-lbl">'+d.label+'</span><span class="sig-desc">'+d.desc+'</span></div><ul class="sig-list">';
    items.forEach(function(n){h+='<li>'+n+'</li>'});h+='</ul></div>';}
  if(!any)h='<div style="color:#5a5f6b;font-size:13px;text-align:center;padding:20px">No se detectaron se\u00f1ales activas en esta ejecuci\u00f3n.</div>';
  c.innerHTML=h;
  setTimeout(function(){document.getElementById('sigModal').style.display='flex'},800);
});
</script>
</body></html>"""

# ========== ENTRY SIGNALS MODAL ==========
def _compute_entry_types(ticker, hist):
    close = hist["Close"].dropna()
    if len(close) < 2: return []
    cur = float(close.iloc[-1])
    hi = float(close.max()); lo = float(close.min())
    types = []
    if 0.95 * hi <= cur <= 1.05 * hi: types.append("RRA")
    if cur > hi: types.append("RR")
    dd = (hi - cur) / hi
    if dd > 0.10 and cur > lo + 0.20 * (hi - lo): types.append("LT")
    now_ts = datetime.now().timestamp()
    c3 = close[close.index.astype('int64') // 10**9 > now_ts - 90 * 86400]
    c6 = close[close.index.astype('int64') // 10**9 > now_ts - 180 * 86400]
    if not c3.empty and not c6.empty and float(c3.min()) > float(c6.min()): types.append("LTA")
    if lo <= cur <= lo * 1.10: types.append("PER")
    return types

sig_map = {"RRA": [], "RR": [], "LT": [], "LTA": [], "PER": [], "PER < 15": []}
# Portfolio positions
for p in portfolio:
    h = full_hist.get(p["ticker"])
    if h is not None and len(h) >= 5:
        for t in _compute_entry_types(p["ticker"], h):
            sig_map[t].append(f"{p['ticker']} \u2014 {p['name']} \u2014 Cartera")
# Alternativas PER < 15
for nm, tk, sc in alt_signal_data:
    sig_map["PER < 15"].append(f"{nm} ({tk}) \u2014 {sc} \u2014 Alternativas")
# Radar cache
rcp = CFG["paths"].get("radar_prev", "")
if os.path.exists(rcp):
    try:
        with open(rcp, "r", encoding="utf-8") as f:
            for r in json.load(f):
                for t in r.get("entry_types", []):
                    sig_map[t].append(f"{r['ticker']} \u2014 {r['name']} \u2014 Radar")
    except Exception:
        pass

SIG_DATA_JSON = json.dumps({k: v for k, v in sig_map.items() if v})
SIG_DESC_JSON = json.dumps({
    "RRA": {"label": "RRA \u2014 Cerca de m\u00e1ximos", "desc": "Precio entre 95% y 105% del m\u00e1ximo 52 semanas", "color": "#3ecf8e"},
    "RR": {"label": "RR \u2014 Rompiendo m\u00e1ximos", "desc": "Precio supera el m\u00e1ximo de 52 semanas", "color": "#2a78d6"},
    "LT": {"label": "LT \u2014 Drawdown >10%", "desc": "Ca\u00edda >10% desde m\u00e1ximo anual, sin estar en m\u00ednimos", "color": "#f0a500"},
    "LTA": {"label": "LTA \u2014 Suelo alcista", "desc": "M\u00ednimos 3 meses > m\u00ednimos 6 meses (tendencia alcista)", "color": "#eda100"},
    "PER": {"label": "PER \u2014 Cerca de m\u00ednimos", "desc": "Precio entre m\u00ednimo y +10% (potencial zona de entrada)", "color": "#e05050"},
    "PER < 15": {"label": "PER < 15 \u2014 Baratas", "desc": "Alternativas con PER estimado inferior a 15 (infravaloradas)", "color": "#a855f7"},
})
# Replace placeholders with actual signal data
html = html.replace("SIGNAL_DATA_JSON", SIG_DATA_JSON).replace("SIGNAL_DESC_JSON", SIG_DESC_JSON)

with open(OUT_FILE, "w", encoding="utf-8") as f:
    f.write(html)
log.info(f"Dashboard generado: {OUT_FILE} ({os.path.getsize(OUT_FILE):,} bytes)")

# ========== RUN SCREENER ==========
if not any("skip_screener" in a for a in sys.argv):
    try:
        log.info("Ejecutando screener...")
        screener_path = os.path.join(CFG["base_dir"], "screener.py")
        subprocess.run([sys.executable, screener_path], cwd=CFG["base_dir"], check=True)
        log.info("Screener completado.")
    except subprocess.CalledProcessError as e:
        log.error(f"Screener fall\u00f3 (exit {e.returncode})")
    except FileNotFoundError:
        log.warning("screener.py no encontrado, saltando.")
