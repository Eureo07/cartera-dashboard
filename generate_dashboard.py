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

# ========== LOAD DATA ==========
DF_FILE = EXCEL_FILE
df = pd.read_excel(DF_FILE)
cols_list = list(df.columns)
idx_col_name, sec_col_name = cols_list[3], cols_list[4]

# Fetch historical prices from yfinance for all positions + benchmark
price_hist = {}
bench_hist = None
hist_path = CFG["paths"]["price_history"]
log.info("Downloading price histories...")
for p in portfolio:
    tk = p["ticker"]
    try:
        stock = yf.Ticker(tk, session=_YF_SESSION)
        hist = stock.history(period="6mo", auto_adjust=False)
        if hist is not None and len(hist) > 2:
            close = hist["Close"].dropna()
            if len(close) < 2:
                log.warning(f"  {tk}: menos de 2 Close validos ({len(close)})")
                continue
            price_hist[tk] = close
            p["current"] = float(close.iloc[-1])
        else:
            log.warning(f"  {tk}: hist={None if hist is None else len(hist)} (insuficiente)")
    except Exception as e:
        log.error(f"  {tk}: error history ({e})")
    # Fallback for current price
    if "current" not in p or p["current"] is None:
        try:
            info = yf.Ticker(tk, session=_YF_SESSION).info or {}
            p["current"] = info.get("regularMarketPrice") or info.get("previousClose") or info.get("currentPrice") or 0
            log.info(f"  {tk}: fallback price={p['current']}")
        except Exception as e:
            log.error(f"  {tk}: fallback error ({e})")
            p["current"] = 0
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
        return {
            "per": val_metric(info.get("trailingPE"), 0, 200),
            "fwd_per": info.get("forwardPE"),
            "pb": val_metric(info.get("priceToBook"), 0, 100),
            "ev_ebitda": ev / ebitda if (ev and ebitda and ebitda != 0) else None,
            "mcap": info.get("marketCap"),
            "eps": info.get("trailingEps"),
            "div_yield": div_yield,
            "ps": info.get("priceToSalesTrailing12Months"),
        }
    except:
        return {"per": None, "fwd_per": None, "pb": None, "ev_ebitda": None, "mcap": None, "eps": None, "div_yield": None, "ps": None}

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
now_str = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%d/%m/%Y %H:%M")
total_cls = "green" if total_pnl >= 0 else "red"

html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard Cartera</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',-apple-system,Arial,sans-serif}}
.dash{{background:#0f1117;color:#e8eaed;padding:20px 30px;min-height:100vh}}
.header{{background:linear-gradient(135deg,#1a1d2e,#2a2d3e);border-radius:16px;padding:28px 36px;margin-bottom:24px;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:24px;font-weight:700;color:#fff}}
.header .sub{{color:#9aa0b0;font-size:13px;margin-top:4px}}
.header .date-info{{text-align:right;color:#9aa0b0;font-size:12px}}
.kpi-row{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:24px}}
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

  <div class="kpi-row">
    <div class="kpi"><div class="label">Inversi\u00f3n Total</div><div class="value">{total_cost:,.0f} \u20ac</div></div>
    <div class="kpi"><div class="label">Valor Actual</div><div class="value">{total_value:,.0f} \u20ac</div></div>
    <div class="kpi"><div class="label">Resultado</div><div class="value {"neg" if total_pnl < 0 else "pos"}">{total_pnl:+,.2f} \u20ac</div></div>
    <div class="kpi"><div class="label">Rentabilidad</div><div class="value {"neg" if total_pnl_pct < 0 else "pos"}">{total_pnl_pct:+.2f}%</div><div class="sub">Cartera</div></div>
    <div class="kpi"><div class="label">vs Euro Stoxx 50</div><div class="value {"neg" if benchmark_return is not None and (total_pnl_pct - benchmark_return) < 0 else "pos"}">{("" if benchmark_return is None else f"{(total_pnl_pct - benchmark_return):+.2f}%")}</div><div class="sub">{f"\u00cdndice {benchmark_return:+.2f}%" if benchmark_return is not None else "N/D"}</div></div>
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

for i, p in enumerate(portfolio):
    tk = p["ticker"]
    v = valuation.get(tk, {})
    per = v.get("per")
    pb_val = v.get("pb")
    fwd_per = v.get("fwd_per")
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

    html += f"""    <div class="pos-card{" neg" if p["pnl"] < 0 else ""}">
      <div class="pos-header">
        <div><div class="ticker">{tk} — {p['name']}</div><div class="name">{sector_name} · Entrada {p['entry_date']}</div></div>
        <div class="price"><div class="current" style="color:{"#e05050" if p["pnl"] < 0 else "#3ecf8e"}">{p['current']:.2f} \u20ac</div><div class="pnl {pnl_cls_card}">{pnl_sign}{p['pnl']:,.2f} \u20ac ({pnl_pct_sign}{p['pnl_pct']:.2f}%)</div></div>
      </div>
      <div class="signal-badge {signal_cls}">{signal_txt}</div>
      <div class="metrics-grid">
        <div class="metric-row"><span class="ml">P. Entrada</span><span class="mv">{p['entry']:.2f} \u20ac</span></div>
        <div class="metric-row"><span class="ml">Stop Loss</span><span class="mv">{p['stop']:.2f} \u20ac</span></div>
        <div class="metric-row"><span class="ml">Distancia stop</span><span class="mv {dist_cls}">{p['dist_stop']:.1f}%</span></div>
        <div class="metric-row"><span class="ml">P. Objetivo</span><span class="mv {"pos" if p["current"] >= p["target"] else ""}">{p['target']:.2f} \u20ac</span></div>
        <div class="metric-row"><span class="ml">PER</span><span class="mv {"warn" if (per or 99) > 30 else ("pos" if per and per <= 20 else "")}">{f"{per:.1f}x" if per else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">PER Fwd</span><span class="mv">{f"{fwd_per:.1f}x" if fwd_per else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">P/B</span><span class="mv {"warn" if (pb_val or 99) > 5 else ("pos" if pb_val and pb_val <= 3 else "")}">{f"{pb_val:.2f}" if pb_val else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">ROE 2026</span><span class="mv {"pos" if (roe_val or 0) >= 15 else ("warn" if (roe_val or 0) >= 5 else "neg")}">{f"{roe_val:.1f}%" if roe_val else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">FCF 2026</span><span class="mv {fcf_cls}">{f"{fcf_val:,.0f}M \u20ac" if fcf_val else "N/D"}</span></div>
        <div class="metric-row"><span class="ml">Peso cartera</span><span class="mv {weight_cls}">{p['weight']:.1f}%{" \u26a0" if p["weight"] > 25 else ""}</span></div>
      </div>
      <div class="tendencia-row">
        <div class="tend-item"><div class="tend-label">Corto Plazo</div><div class="tend-val {st_cls}">{st_trend}</div></div>
        <div class="tend-item"><div class="tend-label">Largo Plazo</div><div class="tend-val {lt_cls}">{lt_trend}</div></div>
      </div>
      <div style="font-size:11px;color:#9aa0b0;margin-top:10px;line-height:1.5">{thesis_text}</div>
    </div>
  </div>

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

# ========== ALTERNATIVAS POR SECTOR ==========
html += """  <div class="section-title">Alternativas por sector</div>
  <div style="font-size:11px;color:#9aa0b0;margin-bottom:14px;padding:8px 12px;background:#12151f;border-radius:8px;line-height:1.6">
    Filtros activos: PER ≤ 30 <span style="color:#5a5f6b;">|</span> Rentabilidad 1A ≥ +25%
    <span style="color:#5a5f6b;display:block;margin-top:2px;font-size:10px;">
      Score = ROE (50%) + EVA (25%) + FCF (25%), normalizado por sector. WACC: 8%.
    </span>
  </div>
"""

# Prepare per-sector alternatives data
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
    # Get valuations for all peers
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
    def get_pb_val(tick):
        pv = all_peer_vals.get(peer_ticker_to_yf(tick), {})
        return val_metric(pv.get("pb"), 0, 100)
    # Compute scores
    sec_df["_score"] = normalized_score(sec_df["2026 ROE"]) * 0.50 + normalized_score(sec_df["2026 EVA"]) * 0.25 + normalized_score(sec_df["2026 FCN"]) * 0.25
    sec_df = sec_df.sort_values("_score", ascending=False)
    # Build table data with pass/fail
    table_rows = []
    pass_count = 0
    for _, rw in sec_df.iterrows():
        t2 = rw["Ticker"]
        yf_t2 = peer_ticker_to_yf(t2)
        eper = get_eper(t2)
        pb2 = get_pb_val(t2)
        rent_1a = get_1y_return(yf_t2)
        is_ours = t2 == db_t or rw["Empresa"].strip().lower() == p["name"].strip().lower()
        # Check filters
        passes = True
        reasons = []
        if eper is not None and (eper > 30 or eper <= 0):
            passes = False
            reasons.append(f"PER {eper:.0f} >30")
        if rent_1a is not None and rent_1a < 25:
            passes = False
            reasons.append(f"Rent.1A {rent_1a:+.0f}% <25%")
        if eper is None and rent_1a is None:
            passes = False
            reasons.append("Sin datos")
        if is_ours:
            passes = True
            reasons = []
        if passes:
            pass_count += 1
        row_data = {
            "name": rw["Empresa"],
            "ticker": t2,
            "score": round(rw["_score"], 3),
            "roe": val_metric(rw["2026 ROE"], -500, 500) or 0,
            "per_fwd": round(eper, 1) if eper else None,
            "pb": round(pb2, 2) if pb2 else None,
            "rent_1a": round(rent_1a, 1) if rent_1a else None,
            "is_ours": is_ours,
            "passes": passes,
            "reasons": "; ".join(reasons)
        }
        table_rows.append(row_data)
    n_total = len(table_rows)
    # Render the sector table
    sec_short = sec[:30]
    html += f"""  <div style="margin-bottom:20px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <h3 style="color:#e8eaed;font-size:14px;font-weight:600;margin:0">{sec}</h3>
      <span style="color:#9aa0b0;font-size:11px">{n_total} empresas analizadas</span>
    </div>
    <table class="alt-table">
      <thead><tr>
        <th>Empresa</th><th>Ticker</th><th>Score</th><th>ROE 2026</th><th>PER Fwd</th><th>P/B</th><th>Rent.1A</th><th></th>
      </tr></thead>
      <tbody>
"""
    for rd in table_rows:
        tr_class = ""
        if rd["is_ours"]:
            tr_class = ' class="selected"'
        elif not rd["passes"]:
            tr_class = ' class="excluded"'
        badge_html = ""
        if rd["is_ours"]:
            badge_html = '<span class="badge-tu">TU POS.</span>'
        elif rd["passes"]:
            badge_html = '<span class="badge-alt">ALT</span>'
        else:
            badge_html = f'<span class="badge-excl">EXCL.</span>'
        score_pct = max(1, min(100, rd["score"] * 100))
        score_color = "#3ecf8e" if rd["score"] > 0.6 else ("#f0a500" if rd["score"] > 0.3 else "#e05050")
        html += f"""        <tr{tr_class}>
          <td><strong>{rd["name"]}</strong></td>
          <td>{rd["ticker"]}</td>
          <td><div class="score-bar-bg"><div class="score-bar-fill" style="width:{score_pct}%;background:{score_color}"></div></div> {rd["score"]:.3f}</td>
          <td style="color:{"#3ecf8e" if (rd["roe"] or 0) >= 15 else ("#f0a500" if (rd["roe"] or 0) >= 5 else "#e05050")}">{f"{rd['roe']:.1f}%" if rd["roe"] else "N/D"}</td>
          <td>{f"{rd['per_fwd']:.1f}x" if rd["per_fwd"] else "N/D"}</td>
          <td style="color:{"#3ecf8e" if (rd["pb"] or 99) <= 3 else ("#f0a500" if (rd["pb"] or 99) <= 5 else "#e05050")}">{f"{rd['pb']:.2f}" if rd["pb"] else "N/D"}</td>
          <td style="color:{"#3ecf8e" if (rd["rent_1a"] or 0) >= 25 else "#e05050"}">{f"{rd['rent_1a']:+.1f}%" if rd["rent_1a"] else "N/D"}</td>
          <td>{badge_html}{f' <span style="font-size:9px;color:#5a5f6b">({rd["reasons"]})</span>' if not rd["passes"] and not rd["is_ours"] else ""}</td>
        </tr>
"""
    html += f"""      </tbody>
    </table>
    <div class="alt-note">{pass_count} de {n_total} empresas pasan el filtro (PER ≤ 30 y Rent.1A ≥ +25%). Score: ROE 50% / EVA 25% / FCF 25%.</div>
  </div>
"""

# == FOOTER ==
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
</script>
</body></html>"""

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
