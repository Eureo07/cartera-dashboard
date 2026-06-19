# -*- coding: utf-8 -*-
import sys, os, subprocess
from zoneinfo import ZoneInfo
_PROJ_DIR = "C:/Users/franl/OneDrive/Escritorio/Inversión/OpenCode/2026"
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)
import pandas as pd
import yfinance as yf
import json, math, statistics
from datetime import datetime, date
from config_loader import CFG, logger, get_logger

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
bench_start_dt = min(datetime.strptime(p["entry_date"], "%d/%m/%Y") for p in portfolio)
log.info("Downloading price histories...")
for p in portfolio:
    tk = p["ticker"]
    try:
        stock = yf.Ticker(tk)
        hist = stock.history(start=bench_start_dt, end=datetime.now(), auto_adjust=False)
        if len(hist) > 2:
            close = hist["Close"].dropna()
            if len(close) < 2:
                continue
            price_hist[tk] = close
            p["current"] = float(close.iloc[-1])
    except:
        pass
    # Fallback for current price
    if "current" not in p or p["current"] is None:
        try:
            info = yf.Ticker(tk).info or {}
            p["current"] = info.get("regularMarketPrice") or info.get("previousClose") or info.get("currentPrice") or 0
        except:
            p["current"] = 0
# Benchmark
try:
    bm = yf.Ticker("^STOXX50E")
    bm_h = bm.history(start=bench_start_dt, end=datetime.now(), auto_adjust=False)
    if len(bm_h) > 2:
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
        stock = yf.Ticker(t)
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
        hist = yf.download(t, period="1y", progress=False, auto_adjust=False)
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
        stock = yf.Ticker(ticker)
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
            stock = yf.Ticker(ticker)
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
    stoxx = yf.download("^STOXX50E", start=bench_start, progress=False, auto_adjust=False)
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
        hist = yf.download(t, period="1y", progress=False, auto_adjust=False)
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
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #333; padding: 20px; }}
h1 {{ font-size: 24px; margin-bottom: 5px; }}
h2 {{ font-size: 18px; margin: 25px 0 10px; color: #444; }}
h3 {{ font-size: 14px; margin: 15px 0 8px; color: #555; }}
.header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }}
.date {{ color: #888; font-size: 13px; }}
.summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }}
.card {{ background: white; border-radius: 10px; padding: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.card .label {{ font-size: 11px; text-transform: uppercase; color: #888; }}
.card .value {{ font-size: 20px; font-weight: 700; margin-top: 5px; }}
.charts-row {{ display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }}
.chart-box {{ background: white; border-radius: 10px; padding: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); flex: 1; min-width: 250px; }}
.chart-box canvas {{ max-height: 250px; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 15px; }}
th {{ background: #f8f9fa; text-align: left; padding: 10px 12px; font-size: 11px; text-transform: uppercase; color: #666; border-bottom: 2px solid #eee; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 13px; }}
tr:last-child td {{ border-bottom: none; }}
.green {{ color: #166534; background: #dcfce7; }}
.yellow {{ color: #854d0e; background: #fef9c3; }}
.orange {{ color: #9a3412; background: #ffedd5; }}
.red {{ color: #991b1b; background: #fce4e4; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.bar-bg {{ background: #eee; border-radius: 6px; height: 10px; width: 100px; display: inline-block; vertical-align: middle; }}
.bar-fill {{ height: 10px; border-radius: 6px; display: block; }}
.db-wrap {{ width: 240px; }}
.db-track {{ height: 14px; background: #e5e7eb; border-radius: 7px; position: relative; overflow: hidden; }}
.db-left {{ height: 100%; background: #ef4444; position: absolute; left: 0; top: 0; border-radius: 7px 0 0 7px; }}
.db-right {{ height: 100%; background: #22c55e; position: absolute; top: 0; border-radius: 0 7px 7px 0; }}
.db-right.blink {{ animation: db-blink 0.8s infinite; }}
@keyframes db-blink {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.2; }} }}
.db-center {{ position: absolute; top: 0; width: 2px; height: 100%; background: #1f2937; z-index: 2; }}
.db-current {{ position: absolute; top: -4px; font-size: 10px; color: #2563eb; transform: translateX(-50%); z-index: 3; line-height: 1; }}
.db-labels {{ display: flex; justify-content: space-between; margin-top: 2px; font-size: 10px; }}
.db-l-red {{ color: #dc2626; }}
.db-l-dark {{ color: #1f2937; font-weight: 600; }}
.db-l-green {{ color: #16a34a; }}
.small {{ font-size: 11px; color: #888; }}
.alert-box {{ background: #fff; border-left: 4px solid #ef4444; padding: 12px 15px; margin-bottom: 15px; border-radius: 0 8px 8px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.alert-box .a-title {{ font-weight: 700; font-size: 13px; }}
.alert-box .a-item {{ font-size: 12px; color: #666; margin-top: 4px; }}
.alert-red {{ color: #dc2626; font-weight: 600; }}
.alert-yellow {{ color: #ca8a04; font-weight: 600; }}
.divers-box {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.divers-box h2 {{ margin: 0 0 10px; }}
.div-row {{ display: flex; flex-wrap: wrap; gap: 12px; }}
.div-item {{ font-size: 13px; padding: 6px 12px; background: #f8f9fa; border-radius: 6px; flex: 1; min-width: 200px; }}
.div-icon {{ font-size: 18px; margin-right: 6px; }}
.div-suggestion {{ font-size: 12px; color: #2563eb; margin-top: 8px; padding: 6px 10px; background: #eff6ff; border-radius: 6px; }}
.btn-alt {{ border: none; background: #e5e7eb; color: #374151; padding: 2px 10px; border-radius: 4px; font-size: 11px; cursor: pointer; margin-right: 4px; }}
.btn-alt.active {{ background: #3b82f6; color: white; }}
.btn-alt:hover {{ background: #d1d5db; }}
.best-alt-btns {{ margin-top: 6px; }}
.best-alt-value {{ font-size: 13px; margin-top: 5px; }}
.card-wide {{ grid-column: span 2; }}
.sect-row {{ padding: 4px 0; border-bottom: 1px solid #eee; }}
.sect-row:last-child {{ border-bottom: none; }}
.sect-name {{ font-weight: 700; color: #555; font-size: 11px; text-transform: uppercase; margin-right: 4px; }}
.sect-meta {{ color: #888; font-size: 11px; }}
.sect-ours {{ color: #16a34a; font-size: 12px; }}
.sect-none {{ color: #999; font-style: italic; }}
.sect-warn {{ color: #ca8a04; font-size: 11px; }}


@media (max-width: 600px) {{ table {{ font-size: 12px; }} td, th {{ padding: 6px 8px; }} }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Dashboard Cartera</h1>
    <div class="date">Actualizado: {now_str}</div>
  </div>
</div>

<div class="summary-cards">
  <div class="card"><div class="label">Inversi\u00f3n Total</div><div class="value" id="total-cost">{total_cost:,.2f} \u20ac</div></div>
  <div class="card"><div class="label">Valor Actual</div><div class="value" id="total-value">{total_value:,.2f} \u20ac</div></div>
  <div class="card"><div class="label">Resultado</div><div class="value {total_cls}" id="total-pnl">{total_pnl:+,.2f} \u20ac</div></div>
  <div class="card"><div class="label">Rentabilidad</div><div class="value {total_cls}" id="total-pnlpct">{total_pnl_pct:+.2f}%</div></div>
"""

# vs Benchmark card
if benchmark_return is not None:
    vs_bm = total_pnl_pct - benchmark_return
    bm_cls = "green" if vs_bm > 0 else "red"
    html += f'  <div class="card"><div class="label">vs Euro Stoxx 50</div><div class="value {bm_cls}">{vs_bm:+.2f}%</div><div class="small">Cartera {total_pnl_pct:+.2f}% vs \u00cdndice {benchmark_return:+.2f}%</div></div>\n'

html += "  {BEST_ALT_CARD}\n"
html += "</div>\n"

# Alerts
if alerts:
    html += """<div class="alert-box"><div class="a-title">Alertas</div>\n"""
    for a in alerts:
        html += f'<div class="a-item">{a}</div>\n'
    html += "</div>\n"

# == DIVERSIFICATION SECTION ==
html += f"""<div class="divers-box">
  <h2>Diversificaci\u00f3n</h2>
  <div class="div-row">
    <div class="div-item"><span class="div-icon">{conc_icon}</span> <span>{conc_text}</span></div>
    <div class="div-item"><span class="div-icon">{def_icon}</span> <span>{def_text}</span></div>
    <div class="div-item"><span class="div-icon">{div_icon}</span> <span>{div_text}</span></div>
  </div>
</div>
"""

# == PIE CHARTS ==
pie_labels = json.dumps([p["name"] for p in portfolio])
pie_weights = json.dumps([round(p["weight"], 1) for p in portfolio])
pie_colors = json.dumps(["#22c55e","#3b82f6","#eab308","#ef4444","#a855f7","#ec4899"])

# Sector aggregation
sector_map = {}
for p in portfolio:
    sec_name = "Desconocido"
    db_t = p["db_ticker"]
    if db_t:
        row = df[df["Ticker"] == db_t]
        if not row.empty:
            sec_name = row.iloc[0][sec_col_name]
    else:
        fb = get_fallback_financials(p["ticker"])
        if fb:
            sec_name = fb.get("Sector", "Desconocido")
    sector_map[sec_name] = sector_map.get(sec_name, 0) + p["weight"]
sec_labels = json.dumps(list(sector_map.keys()))
sec_weights = json.dumps([round(v, 1) for v in sector_map.values()])

html += f"""
<div class="charts-row">
  <div class="chart-box">
    <h3>Peso por Posici\u00f3n</h3>
    <canvas id="piePos"></canvas>
  </div>
  <div class="chart-box">
    <h3>Peso por Sector</h3>
    <canvas id="pieSec"></canvas>
  </div>
  <div class="chart-box">
    <h3>Evoluci\u00f3n Cartera vs Benchmark</h3>
    <canvas id="chartEvol"></canvas>
  </div>
</div>

<script>
new Chart(document.getElementById('piePos'), {{
    type: 'pie',
    data: {{ labels: {pie_labels}, datasets: [{{ data: {pie_weights}, backgroundColor: {pie_colors} }}] }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }} }} }} }} }}
}});
new Chart(document.getElementById('pieSec'), {{
    type: 'pie',
    data: {{ labels: {sec_labels}, datasets: [{{ data: {sec_weights}, backgroundColor: {pie_colors} }}] }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }} }} }} }} }}
}});
var evolCtx = document.getElementById('chartEvol');
if (evolCtx) {{
  var evolDates = {evol_dates_json};
  var evolPort = {evol_portfolio_json};
  var evolBm = {evol_benchmark_json};
  if (evolDates.length > 0) {{
    var normPort = evolPort.map(function(v) {{ return (v / evolPort[0] - 1) * 100; }});
    var firstBm = evolBm.filter(function(v) {{ return v !== null; }})[0] || 1;
    var normBm = evolBm.map(function(v) {{ return v !== null ? (v / firstBm - 1) * 100 : null; }});
    new Chart(evolCtx, {{
      type: 'line',
      data: {{
        labels: evolDates,
        datasets: [
          {{ label: 'Cartera', data: normPort, borderColor: '#3b82f6', backgroundColor: 'transparent', tension: 0.2, pointRadius: 0 }},
          {{ label: 'Euro Stoxx 50', data: normBm, borderColor: '#9ca3af', backgroundColor: 'transparent', tension: 0.2, pointRadius: 0, borderDash: [4,4] }}
        ]
      }},
      options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }} }} }} }}, scales: {{ y: {{ ticks: {{ callback: function(v) {{ return v.toFixed(1) + '%'; }} }} }} }} }}
    }});
  }}
}}</script>
"""

# == POSICIONES ==
html += """<h2>Posiciones</h2>
<table>
<thead><tr>
  <th>#</th><th>Empresa</th><th>Ticker</th><th>Acc.</th><th>Fecha</th><th>P.Entrada</th><th>P.Actual</th><th>Inversi\u00f3n</th><th>Val.Actual</th><th>P&amp;L</th><th>Rent.</th><th>CAGR</th><th>Peso</th><th>Stop</th><th>Dist.</th><th>Obj.</th><th>50d</th>
</tr></thead>
<tbody>
"""
# MA50 data for each position (from price_hist)
ma50_data = {}
for tk, series in price_hist.items():
    if len(series) >= 50:
        ma50_data[tk] = series.tail(50).mean()
    elif len(series) > 0:
        ma50_data[tk] = series.mean()

for i, p in enumerate(portfolio, 1):
    pnl_cls = metric_class("pnl", p["pnl"])
    dist_cls = metric_class("dist_stop", p["dist_stop"])
    cagr_s = f"{p['cagr']*100:.1f}%" if p.get("cagr") is not None else "-"
    if p.get("cagr") is not None and p.get("days", 999) < 30:
        cagr_s += " (*per\u00edodo < 1 mes)"
    elif p.get("cagr") is None and p.get("days", 999) < 30:
        cagr_s = "- (*per\u00edodo < 1 mes)"
    cagr_cls = ""
    bar_color = "green" if p["dist_stop"] > 15 else ("yellow" if p["dist_stop"] > 8 else "red")
    bar_w = min(p["dist_stop"], 100)
    ret = p["pnl_pct"]
    if ret >= 17.5:
        target_cls = "orange"
    elif ret >= 12:
        target_cls = "green"
    else:
        target_cls = ""
    # MA50
    ma50 = ma50_data.get(p["ticker"])
    ma50_s = f"{ma50:.4f}" if ma50 else "N/D"
    ma50_cls = ""
    if ma50:
        ma50_cls = "green" if p["current"] >= ma50 else "red"
    html += f"""<tr>
  <td>{i}</td>
  <td><strong>{p['name']}</strong></td>
  <td>{p['ticker']}</td>
  <td>{p['shares']}</td>
  <td>{p['entry_date']}</td>
  <td>{p['entry']:.2f}</td>
  <td id="price-{i}">{p['current']:.4f}</td>
  <td>{p['cost']:,.2f}</td>
  <td id="val-{i}">{p['value']:,.2f}</td>
  {cell(f"{p['pnl']:+,.2f}", pnl_cls, f"pnl-{i}")}
  {cell(f"{p['pnl_pct']:+.2f}%", pnl_cls, f"pnlpct-{i}")}
  {cell(cagr_s, cagr_cls)}
  <td>{p['weight']:.1f}%</td>
  <td>{p['stop']:.2f}</td>
  <td id="dist-{i}"><span class="badge {dist_cls}">{p['dist_stop']:.1f}%</span><br><span class="bar-bg"><span class="bar-fill {bar_color}" style="width:{bar_w}%"></span></span></td>
  {cell(f"{p['target']:.2f}", target_cls)}
  {cell(ma50_s, ma50_cls)}
</tr>
"""
html += "</tbody></table>"

# == BENCHMARK ==
if benchmark_return is not None:
    bm_vs = total_pnl_pct - benchmark_return
    bm_cls2 = "green" if bm_vs > 0 else "red"
    html += f"""<h2>Benchmark</h2>
<table>
<thead><tr><th>Indicador</th><th>Valor</th></tr></thead>
<tbody>
<tr><td><strong>Euro Stoxx 50 ({bench_start} - hoy)</strong></td><td>{benchmark_return:+.2f}%</td></tr>
<tr><td><strong>Cartera (mismo periodo)</strong></td><td class="{total_cls}">{total_pnl_pct:+.2f}%</td></tr>
<tr><td><strong>Diferencia</strong></td><td class="{bm_cls2}">{bm_vs:+.2f}%</td></tr>
</tbody></table>
"""

# == RISK ==
html += """<h2>Riesgo</h2>
<table>
<thead><tr>
  <th>Empresa</th><th>P.Actual</th><th>Stop Loss</th><th>Barra Doble</th><th>P\u00e9rdida M\u00e1x</th><th>52w High</th><th>52w Low</th>
</tr></thead>
<tbody>
"""
for i, p in enumerate(portfolio, 1):
    high52 = low52 = None
    if p["ticker"]:
        try:
            sinfo = yf.Ticker(p["ticker"]).info or {}
            high52 = sinfo.get("fiftyTwoWeekHigh")
            low52 = sinfo.get("fiftyTwoWeekLow")
        except:
            pass
    # Build double bar
    S, E, T, C = p["stop"], p["entry"], p["target"], p["current"]
    total_range = T - S
    if total_range > 0:
        entry_pct = max(1, min(99, (E - S) / total_range * 100))
        current_pct = max(0, min(100, (C - S) / total_range * 100))
    else:
        entry_pct = 50
        current_pct = 50
    exceeded = C >= T
    blink_cls = " blink" if exceeded else ""
    double_bar = f"""<div class="db-wrap" id="dist-{i}">
  <div class="db-track">
    <div class="db-left" style="width:{entry_pct}%"></div>
    <div class="db-right{blink_cls}" style="width:{100-entry_pct}%"></div>
    <div class="db-center" style="left:{entry_pct}%"></div>
    <div class="db-current" style="left:{current_pct}%">&#x25BC;</div>
  </div>
  <div class="db-labels">
    <span class="db-l-red">{S:.2f}</span>
    <span class="db-l-dark">{E:.2f}</span>
    <span class="db-l-green">{T:.2f}</span>
  </div>
</div>"""
    html += f"""<tr>
  <td><strong>{p['name']}</strong></td>
  <td>{p['current']:.4f}</td>
  <td>{p['stop']:.2f}</td>
  <td>{double_bar}</td>
  <td class="red">{p['shares']*(p['current']-p['stop']):+,.0f} \u20ac</td>
  <td>{f"{high52:.2f}" if high52 else "-"}</td>
  <td>{f"{low52:.2f}" if low52 else "-"}</td>
</tr>"""
html += "</tbody></table>"

# == VALUATION ==
html += """<h2>Valoraci\u00f3n</h2>
<table>
<thead><tr>
  <th>Empresa</th><th>PER</th><th>PER Fwd</th><th>P/B</th><th>EV/EBITDA</th><th>Cap.Mercado</th><th>BPA</th><th>P/S</th><th>Div.Yield</th>
</tr></thead>
<tbody>
"""
for p in portfolio:
    v = valuation.get(p["ticker"], {})
    per_v = val_metric(v.get("per"), 0, 200)
    pb_v = val_metric(v.get("pb"), 0, 100)
    div_v = v.get("div_yield")
    html += f"""<tr>
  <td><strong>{p['name']}</strong></td>
  {cell(f"{per_v:.1f}" if per_v is not None else "N/D", metric_class("per", per_v))}
  <td>{f"{v['fwd_per']:.1f}" if v.get('fwd_per') else "N/D"}</td>
  {cell(f"{pb_v:.2f}" if pb_v is not None else "N/D", metric_class("pb", pb_v))}
  {cell(f"{v['ev_ebitda']:.1f}" if v.get('ev_ebitda') else "N/D", metric_class("ev_ebitda", v.get('ev_ebitda')))}
  <td>{f"{v['mcap']:,.0f}" if v.get('mcap') else "N/D"}</td>
  <td>{f"{v['eps']:.2f}" if v.get('eps') else "N/D"}</td>
  <td>{f"{v['ps']:.2f}" if v.get('ps') else "N/D"}</td>
  <td>{f"{div_v*100:.2f}%" if div_v is not None else "N/D"}</td>
</tr>"""
html += "</tbody></table>"

# == METRICAS FINANCIERAS ==
html += """<h2>M\u00e9tricas Financieras</h2>
<table>
<thead><tr>
  <th>Empresa</th><th>A\u00f1o</th><th>ROE</th><th>ROI</th><th>FCF</th><th>EVA</th><th>M.EBITA</th><th>M.EBIT</th>
</tr></thead>
<tbody>
"""
for p in portfolio:
    db_t = p["db_ticker"]
    if db_t:
        row = df[df["Ticker"] == db_t]
        if not row.empty:
            r = row.iloc[0]
            for y in ["2024", "2025", "2026"]:
                roe_v = val_metric(r[f"{y} ROE"], -500, 500)
                roi_v = val_metric(r[f"{y} ROI"], -500, 500)
                fcf_v = r[f"{y} FCN"]
                eva_v = r[f"{y} EVA"]
                mebita_v = val_metric(r[f"{y} M.EBITA"], -500, 500)
                mebit_v = val_metric(r[f"{y} M.EBIT"], -500, 500)
                html += f"""<tr>
  <td><strong>{p['name']}</strong></td>
  <td>{y}</td>
  {cell(f"{roe_v:.1f}%" if roe_v is not None else "-", metric_class("roe", roe_v))}
  {cell(f"{roi_v:.1f}%" if roi_v is not None else "-", metric_class("roi", roi_v))}
  {cell(f"{fcf_v:,.0f}" if pd.notna(fcf_v) else "-", metric_class("fcf", fcf_v))}
  {cell(f"{eva_v:,.0f}" if pd.notna(eva_v) else "-", metric_class("eva", eva_v))}
  {cell(f"{mebita_v:.1f}%" if mebita_v is not None else "-", metric_class("margen", mebita_v))}
  {cell(f"{mebit_v:.1f}%" if mebit_v is not None else "-", metric_class("margen", mebit_v))}
</tr>"""
    else:
        # Fallback: try yfinance
        fb = get_fallback_financials(p["ticker"])
        if fb:
            for y in ["2024", "2025", "2026"]:
                roe_v = val_metric(fb.get(f"{y} ROE"), -500, 500)
                roi_v = val_metric(fb.get(f"{y} ROI"), -500, 500)
                fcf_v = fb.get(f"{y} FCN")
                eva_v = fb.get(f"{y} EVA")
                mebita_v = val_metric(fb.get(f"{y} M.EBITA"), -500, 500)
                mebit_v = val_metric(fb.get(f"{y} M.EBIT"), -500, 500)
                html += f"""<tr>
  <td><strong>{p['name']}</strong></td>
  <td>{y}</td>
  {cell(f"{roe_v:.1f}%" if roe_v is not None else "N/D", metric_class("roe", roe_v))}
  {cell(f"{roi_v:.1f}%" if roi_v is not None else "N/D", metric_class("roi", roi_v))}
  {cell(f"{fcf_v:,.0f}" if fcf_v is not None else "N/D", metric_class("fcf", fcf_v))}
  {cell(f"{eva_v:,.0f}" if eva_v is not None else "N/D", metric_class("eva", eva_v))}
  {cell(f"{mebita_v:.1f}%" if mebita_v is not None else "N/D", metric_class("margen", mebita_v))}
  {cell(f"{mebit_v:.1f}%" if mebit_v is not None else "N/D", metric_class("margen", mebit_v))}
</tr>"""
            html += """<tr><td colspan="8" style="color:#888;font-size:11px;">* Datos parciales extra\u00eddos de yfinance</td></tr>"""
        else:
            html += f"""<tr><td><strong>{p['name']}</strong></td><td colspan="7" style="color:#888;">No disponible en la base de datos</td></tr>"""
html += "</tbody></table>"

# == SECTOR COMPARISON ==
html += """<h2>Comparativa Sectorial</h2>
<table>
<thead><tr>
  <th>Empresa</th><th>Sector</th><th>ROE 2026</th><th>Media Sector</th><th>vs Media</th><th>EVA 2026</th><th>Posici\u00f3n</th>
</tr></thead>
<tbody>
"""
for p in portfolio:
    db_t = p["db_ticker"]
    if db_t:
        row = df[df["Ticker"] == db_t]
        if not row.empty:
            r = row.iloc[0]
            sec = r[sec_col_name]
            sm = get_sector_metrics(sec, df, sec_col_name)
            roe_val = val_metric(r["2026 ROE"], -500, 500) or 0
            vs_media = roe_val - sm["roe_mean"]
            vs_cls = "green" if vs_media > 0 else "red"
            sec_df = df[df[sec_col_name] == sec].copy()
            sec_df["_rank"] = sec_df["2026 ROE"].rank(ascending=False)
            rank = int(sec_df[sec_df["Ticker"] == db_t]["_rank"].values[0]) if db_t in sec_df["Ticker"].values else "-"
            eva_v = r["2026 EVA"]
            html += f"""<tr>
  <td><strong>{p['name']}</strong></td>
  <td>{sec}</td>
  {cell(f"{roe_val:.1f}%", metric_class("roe", roe_val))}
  <td>{sm['roe_mean']:.1f}%</td>
  {cell(f"{vs_media:+.1f}%", vs_cls)}
  {cell(f"{eva_v:,.0f}" if pd.notna(eva_v) else "-", metric_class("eva", eva_v))}
  <td>{rank}/{sm['n']}</td>
</tr>"""
    else:
        fb = get_fallback_financials(p["ticker"])
        if fb:
            sec = fb.get("Sector", "Desconocido")
            # Use latest available ROE
            roe_val = None
            for y in ["2026", "2025", "2024"]:
                roe_val = val_metric(fb.get(f"{y} ROE"), -500, 500)
                if roe_val is not None:
                    break
            html += f"""<tr>
  <td><strong>{p['name']}</strong></td>
  <td>{sec}</td>
  {cell(f"{roe_val:.1f}%" if roe_val else "N/D", metric_class("roe", roe_val))}
  <td colspan="4" style="color:#888;">Sin comparativa sectorial (datos parciales)</td>
</tr>"""
html += "</tbody></table>"

# == TOP 5 PEERS (DEDUPED + PER FILTER) ==
html += """<h2>Top 5 Alternativas por Sector</h2>
<p class="small">Ranking: ROE (50%) + EVA (25%) + FCF (25%). Normalizado por sector. Filtro PER &lt;=30. (*) PER no disponible.</p>
"""
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
    # Get valuations for all peers in this sector
    all_peer_tickers = df[df[sec_col_name] == sec]["Ticker"].unique()
    all_peer_vals = {}
    for pt in set(peer_ticker_to_yf(t) for t in all_peer_tickers):
        all_peer_vals[pt] = get_valuation(pt)
    # Apply PER filter: exclude PER > 30 or PER < 0 (use fwd if available)
    def get_effective_per(tick):
        pv = all_peer_vals.get(peer_ticker_to_yf(tick), {})
        fwd = pv.get("fwd_per")
        if fwd is not None and fwd > 0:
            return fwd
        tr = pv.get("per")
        if tr is not None and tr > 0:
            return tr
        return None
    def get_trailing_per(tick):
        pv = all_peer_vals.get(peer_ticker_to_yf(tick), {})
        return pv.get("per")
    def get_fwd_per(tick):
        pv = all_peer_vals.get(peer_ticker_to_yf(tick), {})
        return pv.get("fwd_per")
    mask = []
    per_missing = set()
    our_row_saved = None
    for _, rw in sec_df.iterrows():
        is_ours = rw["Ticker"] == db_t or rw["Empresa"].strip().lower() == p["name"].strip().lower()
        if is_ours:
            our_row_saved = rw
            mask.append(True)
            continue
        eper = get_effective_per(rw["Ticker"])
        if eper is None:
            mask.append(True)
            per_missing.add(rw["Ticker"])
        elif 0 <= eper <= 30:
            mask.append(True)
        else:
            mask.append(False)
    sec_df = sec_df[mask].copy()
    # Ensure the portfolio company is always in the set
    if our_row_saved is not None and our_row_saved["Ticker"] not in sec_df["Ticker"].values:
        sec_df = pd.concat([sec_df, our_row_saved.to_frame().T], ignore_index=True)
        our_exempted = True
    else:
        our_exempted = False
    if len(sec_df) < 1: continue
    sec_df["_score"] = normalized_score(sec_df["2026 ROE"]) * 0.50 + normalized_score(sec_df["2026 EVA"]) * 0.25 + normalized_score(sec_df["2026 FCN"]) * 0.25
    sec_df = sec_df.sort_values("_score", ascending=False)
    top5 = sec_df.head(5)
    # n_sector counts non-portfolio peers
    n_sector = len(sec_df) - (1 if our_row_saved is not None else 0)
    # Collect peer data for sector alternatives
    peer_list = []
    for _, rw in sec_df.iterrows():
        t2 = rw["Ticker"]
        yf_t2 = peer_ticker_to_yf(t2)
        eper2 = get_effective_per(t2)
        rent_1a2 = get_1y_return(yf_t2)
        is_ours2 = t2 == db_t or rw["Empresa"].strip().lower() == p["name"].strip().lower()
        peer_list.append({
            "name": rw["Empresa"],
            "ticker": t2,
            "score": round(rw["_score"], 3),
            "per": round(eper2, 1) if eper2 is not None else None,
            "rent_1a": round(rent_1a2, 1) if rent_1a2 is not None else None,
            "is_ours": is_ours2
        })
    if peer_list:
        sector_alts_data[sec] = {"ours": p["name"], "peers": peer_list}
    # Compute 1y returns for peers in the filtered sector
    sec_tickers = [peer_ticker_to_yf(t) for t in sec_df["Ticker"].tolist()]
    sec_avg_1y = None
    sec_returns = []
    for st in set(sec_tickers):
        r = get_1y_return(st)
        if r is not None:
            sec_returns.append(r)
    if sec_returns:
        sec_avg_1y = statistics.median(sec_returns)

    html += f"""<h3>{p['name']} \u00b7 {sec} ({n_sector} empresas)</h3>
<table>
<thead><tr>
  <th>#</th><th>Empresa</th><th>Ticker</th><th>Score</th><th>ROE 2026</th><th>EVA 2026</th><th>FCF 2026</th><th>PER</th><th>PER Fwd</th><th>P/B</th><th>Rent.1A</th>
</tr></thead>
<tbody>
"""
    for i2, (_, pr) in enumerate(top5.iterrows(), 1):
        t = pr["Ticker"]
        is_ours = t == db_t or pr["Empresa"].strip().lower() == p["name"].strip().lower()
        name_disp = f"{pr['Empresa']}" + (" (tu posici\u00f3n)" if is_ours else "")
        if t in per_missing:
            name_disp += " (*)"
        if is_ours and our_exempted:
            name_disp += " (PER no filtrado)"
        tr_style = ' style="background:#f0fdf4;font-weight:600;"' if is_ours else ""
        yf_t = peer_ticker_to_yf(t)
        pv = all_peer_vals.get(yf_t, {})
        roe_v2 = val_metric(pr["2026 ROE"], -500, 500) or 0
        eva_v2, fcf_v2 = pr["2026 EVA"], pr["2026 FCN"]
        per_v2 = get_trailing_per(t)
        per_fwd_v2 = get_fwd_per(t)
        pb_v2 = val_metric(pv.get("pb"), 0, 100)
        rent_1a = get_1y_return(yf_t)
        html += f"""<tr{tr_style}>
  <td>{i2}</td>
  <td>{name_disp}</td>
  <td>{t}</td>
  <td>{pr['_score']:.3f}</td>
  {cell(f"{roe_v2:.1f}%", metric_class("roe", roe_v2))}
  {cell(f"{eva_v2:,.0f}", metric_class("eva", eva_v2))}
  {cell(f"{fcf_v2:,.0f}", metric_class("fcf", fcf_v2))}
  {cell(f"{per_v2:.1f}" if per_v2 is not None else "N/D", per_val_class(per_v2))}
  {cell(f"{per_fwd_v2:.1f}" if per_fwd_v2 is not None else "N/D", per_val_class(per_fwd_v2))}
  {cell(f"{pb_v2:.2f}" if pb_v2 is not None else "N/D", metric_class("pb", pb_v2))}
  {cell(f"{rent_1a:+.1f}%" if rent_1a is not None else "N/D", rent_1a_class(rent_1a, sec_avg_1y))}
</tr>"""
    # Sector average row
    sec_avg_str = f"{sec_avg_1y:+.1f}%" if sec_avg_1y is not None else "N/D"
    html += f"""<tr style="background:#f0f0f0;font-weight:700;">
  <td colspan="3">Mediana del sector ({len(sec_returns)} empresas)</td>
  <td colspan="8">{sec_avg_str}</td>
</tr>"""
    html += "</tbody></table>"

# Handle portfolio companies without db_ticker (add their sector with just the portfolio company)
for p in portfolio:
    if p.get("db_ticker"): continue
    fb = get_fallback_financials(p["ticker"])
    if fb:
        sec = fb.get("Sector", "Desconocido")
        if sec not in sector_alts_data:
            sector_alts_data[sec] = {"ours": p["name"], "peers": []}

# Build best alternative card per sector
def select_best_alt(peers, mode):
    """Return (best_peer_dict, warning_str, is_portfolio_company)."""
    non_ours = [q for q in peers if not q.get("is_ours")]
    ours_list = [q for q in peers if q.get("is_ours")]
    ours = ours_list[0] if ours_list else None

    if mode == "score":
        cand = [q for q in non_ours if q["per"] is not None and q["per"] <= 30 and q["rent_1a"] is not None and q["rent_1a"] > 0 and q.get("score", 0) > 0.4]
        if not cand:
            cand = [q for q in non_ours if q["per"] is not None and q["per"] <= 30 and q.get("score", 0) > 0.4]
            if cand:
                best = max(cand, key=lambda x: x["score"])
                return (best, "(sin momentum positivo en el sector)", False)
            if ours:
                return (ours, "no_momentum_ours", True)
            return (None, None, False)
        best = max(cand, key=lambda x: x["score"])
        return (best, None, False)

    elif mode == "per":
        cand = [q for q in non_ours if q["score"] > 0.4 and q["rent_1a"] is not None and q["rent_1a"] > 0 and q["per"] is not None]
        if not cand:
            cand = [q for q in non_ours if q["score"] > 0.4 and q["per"] is not None]
            if cand:
                best = min(cand, key=lambda x: x["per"])
                return (best, "(sin momentum positivo en el sector)", False)
            if ours:
                return (ours, "no_momentum_ours", True)
            return (None, None, False)
        best = min(cand, key=lambda x: x["per"])
        return (best, None, False)

    elif mode == "mom":
        cand = [q for q in non_ours if q["score"] > 0.4 and q["per"] is not None and q["per"] <= 30 and q["rent_1a"] is not None]
        if not cand:
            cand = [q for q in non_ours if q["score"] > 0.4 and q["per"] is not None]
            if cand:
                best = max(cand, key=lambda x: x["rent_1a"])
                return (best, "(sin momentum positivo en el sector)", False)
            if ours:
                return (ours, "no_momentum_ours", True)
            return (None, None, False)
        best = max(cand, key=lambda x: x["rent_1a"])
        return (best, None, False)

    return (None, None, False)

def fmt_alt_sector(sec_name, sec_data, mode):
    """Return HTML row for one sector given a mode."""
    best, warn, is_ours = select_best_alt(sec_data["peers"], mode)
    if best is None:
        return f'<div class="sect-row"><span class="sect-name">{sec_name}</span> \u2192 <span class="sect-none">Sin alternativas</span></div>'
    if is_ours:
        if warn == "no_momentum_ours":
            return f'<div class="sect-row"><span class="sect-name">{sec_name}</span> \u2192 <span class="sect-warn">Sector sin momentum positivo</span><br><span class="sect-ours">Mejor por score: {best["name"]} (ya en cartera)</span></div>'
        return f'<div class="sect-row"><span class="sect-name">{sec_name}</span> \u2192 <span class="sect-ours">Ya tienes la mejor opci\u00f3n ({best["ticker"]})</span></div>'
    rent_str = f"{best['rent_1a']:+.1f}%" if best["rent_1a"] is not None else "N/D"
    per_str = f"{best['per']:.1f}" if best["per"] is not None else "N/D"
    warn_str = f' <span class="sect-warn">{warn}</span>' if warn else ""
    return f'<div class="sect-row"><span class="sect-name">{sec_name}</span> \u2192 <strong>{best["name"]}</strong> ({best["ticker"]})<br><span class="sect-meta">Score: {best["score"]:.3f} \u00b7 PER: {per_str} \u00b7 Rent.1A: {rent_str}</span>{warn_str}</div>'

def build_sector_alts_html(mode="score"):
    rows = "".join(fmt_alt_sector(s, d, mode) for s, d in sector_alts_data.items())
    return rows

sector_alts_json = json.dumps(sector_alts_data, ensure_ascii=False, indent=None)

best_alt_card = f"""<div class="card card-wide" id="best-alt-card">
  <div class="label">Mejores Alternativas por Sector</div>
  <div class="best-alt-value" id="best-alt-value">
    {build_sector_alts_html("score")}
  </div>
  <div class="best-alt-btns" id="best-alt-btns">
    <button class="btn-alt active" data-mode="score">Score</button>
    <button class="btn-alt" data-mode="per">PER</button>
    <button class="btn-alt" data-mode="mom">Momentum</button>
  </div>
</div>
<script>
(function() {{
  const DATA = {sector_alts_json};
  function selectBestAlt(peers, mode) {{
    var nonOurs = peers.filter(function(q) {{ return !q.is_ours; }});
    var oursList = peers.filter(function(q) {{ return q.is_ours; }});
    var ours = oursList.length > 0 ? oursList[0] : null;
    var cand, best;
    if (mode === 'score') {{
      cand = nonOurs.filter(function(q) {{ return q.per !== null && q.per <= 30 && q.rent_1a !== null && q.rent_1a > 0 && q.score > 0.4; }});
      if (cand.length === 0) {{
        cand = nonOurs.filter(function(q) {{ return q.per !== null && q.per <= 30 && q.score > 0.4; }});
        if (cand.length > 0) {{
          best = cand.reduce(function(a,b) {{ return a.score > b.score ? a : b; }});
          return {{peer: best, warn: "(sin momentum positivo en el sector)", isOurs: false}};
        }}
        if (ours) return {{peer: ours, warn: "no_momentum_ours", isOurs: true}};
        return {{peer: null, warn: null, isOurs: false}};
      }}
      best = cand.reduce(function(a,b) {{ return a.score > b.score ? a : b; }});
      return {{peer: best, warn: null, isOurs: false}};
    }}
    if (mode === 'per') {{
      cand = nonOurs.filter(function(q) {{ return q.score > 0.4 && q.rent_1a !== null && q.rent_1a > 0 && q.per !== null; }});
      if (cand.length === 0) {{
        cand = nonOurs.filter(function(q) {{ return q.score > 0.4 && q.per !== null; }});
        if (cand.length > 0) {{
          best = cand.reduce(function(a,b) {{ return a.per < b.per ? a : b; }});
          return {{peer: best, warn: "(sin momentum positivo en el sector)", isOurs: false}};
        }}
        if (ours) return {{peer: ours, warn: "no_momentum_ours", isOurs: true}};
        return {{peer: null, warn: null, isOurs: false}};
      }}
      best = cand.reduce(function(a,b) {{ return a.per < b.per ? a : b; }});
      return {{peer: best, warn: null, isOurs: false}};
    }}
    if (mode === 'mom') {{
      cand = nonOurs.filter(function(q) {{ return q.score > 0.4 && q.per !== null && q.per <= 30 && q.rent_1a !== null; }});
      if (cand.length === 0) {{
        cand = nonOurs.filter(function(q) {{ return q.score > 0.4 && q.per !== null; }});
        if (cand.length > 0) {{
          best = cand.reduce(function(a,b) {{ return a.rent_1a > b.rent_1a ? a : b; }});
          return {{peer: best, warn: "(sin momentum positivo en el sector)", isOurs: false}};
        }}
        if (ours) return {{peer: ours, warn: "no_momentum_ours", isOurs: true}};
        return {{peer: null, warn: null, isOurs: false}};
      }}
      best = cand.reduce(function(a,b) {{ return a.rent_1a > b.rent_1a ? a : b; }});
      return {{peer: best, warn: null, isOurs: false}};
    }}
  }}
  function fmtSector(name, data, mode) {{
    var r = selectBestAlt(data.peers, mode);
    if (!r.peer) return '<div class="sect-row"><span class="sect-name">' + name + '</span> \u2192 <span class="sect-none">Sin alternativas</span></div>';
    if (r.isOurs) {{
      if (r.warn === 'no_momentum_ours') {{
        return '<div class="sect-row"><span class="sect-name">' + name + '</span> \u2192 <span class="sect-warn">Sector sin momentum positivo</span><br><span class="sect-ours">Mejor por score: ' + r.peer.name + ' (ya en cartera)</span></div>';
      }}
      return '<div class="sect-row"><span class="sect-name">' + name + '</span> \u2192 <span class="sect-ours">Ya tienes la mejor opci\u00f3n (' + r.peer.ticker + ')</span></div>';
    }}
    var rentStr = r.peer.rent_1a !== null ? (r.peer.rent_1a >= 0 ? '+' : '') + r.peer.rent_1a.toFixed(1) + '%' : 'N/D';
    var perStr = r.peer.per !== null ? r.peer.per.toFixed(1) : 'N/D';
    var warnStr = r.warn ? ' <span class="sect-warn">' + r.warn + '</span>' : '';
    return '<div class="sect-row"><span class="sect-name">' + name + '</span> \u2192 <strong>' + r.peer.name + '</strong> (' + r.peer.ticker + ')<br><span class="sect-meta">Score: ' + r.peer.score.toFixed(3) + ' \u00b7 PER: ' + perStr + ' \u00b7 Rent.1A: ' + rentStr + '</span>' + warnStr + '</div>';
  }}
  function updateSectorAlts(mode) {{
    var html = '';
    for (var sec in DATA) {{
      if (DATA.hasOwnProperty(sec)) {{
        html += fmtSector(sec, DATA[sec], mode);
      }}
    }}
    document.getElementById('best-alt-value').innerHTML = html;
    var btns = document.querySelectorAll('#best-alt-btns .btn-alt');
    btns.forEach(function(b) {{
      b.classList.remove('active');
      if (b.dataset.mode === mode) b.classList.add('active');
    }});
  }}
  var btns = document.querySelectorAll('#best-alt-btns .btn-alt');
  btns.forEach(function(b) {{
    b.addEventListener('click', function() {{
      updateSectorAlts(this.dataset.mode);
    }});
  }});
}})();
</script>"""
html = html.replace("{BEST_ALT_CARD}", best_alt_card)

# == CORRELATION ==
html += f"""<h2>Correlaci\u00f3n de Precios (252d)</h2>
<table>
<tbody>
{corr_html_rows}
</tbody>
</table>
<p class="small"><span class="green">Verde</span> = baja correlaci\u00f3n (<0.3) · <span class="yellow">Amarillo</span> = media (0.3-0.7) · <span class="red">Rojo</span> = alta (>0.7)</p>
"""

# == INVESTMENT THESIS ==
html += """<h2>Tesis de Inversi\u00f3n</h2>
<table>
<thead><tr>
  <th>Empresa</th><th>Fecha Entrada</th><th>Motivo de Compra</th><th>Precio Objetivo</th><th>Condici\u00f3n de Venta</th>
</tr></thead>
<tbody>
"""
for p in portfolio:
    t = tesis.get(p["ticker"], {})
    fecha = t.get("fecha_entrada", p["entry_date"])
    motivo = t.get("motivo", "—")
    objetivo = t.get("precio_objetivo", "—")
    obj_s = f"{objetivo:.2f}" if isinstance(objetivo, (int, float)) else str(objetivo)
    cond = t.get("condicion_venta", "—")
    html += f"""<tr>
  <td><strong>{p['name']}</strong></td>
  <td>{fecha}</td>
  <td>{motivo}</td>
  <td>{obj_s} \u20ac</td>
  <td>{cond}</td>
</tr>"""
html += "</tbody></table>\n"

# == JS LIVE UPDATE ==
js_data = json.dumps([{"ticker": p["ticker"], "shares": p["shares"], "entry": p["entry"], "commission": p["commission"], "stop": p["stop"], "name": p["name"], "cost": p["cost"]} for p in portfolio])

html += f"""
<script>
const PORTFOLIO = {js_data};
const SERVER = '';
function liveUpdate() {{
    let totalVal = 0, totalCost = 0;
    let pending = PORTFOLIO.length;
    PORTFOLIO.forEach((pos, idx) => {{
        if (!pos.ticker) {{ pending--; return; }}
        const row = idx + 1;
        fetch(SERVER + '/api/price/' + encodeURIComponent(pos.ticker))
            .then(r => r.json())
            .then(data => {{
                const price = data.price;
                if (!price) return;
                const cost = pos.cost;
                const currVal = pos.shares * price;
                const pnl = currVal - cost;
                const pnlPct = (pnl / cost) * 100;
                totalVal += currVal;
                totalCost += cost;
                document.getElementById('price-' + row).textContent = price.toFixed(4);
                document.getElementById('val-' + row).textContent = currVal.toFixed(2) + ' \\u20ac';
                const el = document.getElementById('pnl-' + row);
                if (el) {{ el.textContent = (pnl >= 0 ? '+' : '') + pnl.toFixed(2) + ' \\u20ac'; el.className = pnl >= 0 ? 'green' : 'red'; }}
                const el2 = document.getElementById('pnlpct-' + row);
                if (el2) {{ el2.textContent = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%'; el2.className = pnlPct >= 0 ? 'green' : 'red'; }}
                const el3 = document.getElementById('dist-' + row);
                if (el3) {{ 
                    const E = pos.entry, S = pos.stop, T = E * 1.175, C = price;
                    const rng = T - S;
                    if (rng > 0) {{
                        const ePct = Math.max(1, Math.min(99, (E-S)/rng*100));
                        const cPct = Math.max(0, Math.min(100, (C-S)/rng*100));
                        el3.querySelector('.db-left').style.width = ePct + '%';
                        el3.querySelector('.db-right').style.width = (100-ePct) + '%';
                        el3.querySelector('.db-center').style.left = ePct + '%';
                        el3.querySelector('.db-current').style.left = cPct + '%';
                        el3.querySelector('.db-l-red').textContent = S.toFixed(2);
                        el3.querySelector('.db-l-green').textContent = T.toFixed(2);
                        if (C >= T) {{
                            el3.querySelector('.db-right').classList.add('blink');
                        }} else {{
                            el3.querySelector('.db-right').classList.remove('blink');
                        }}
                    }}
                }}
            }})
            .catch(() => {{}})
            .finally(() => {{
                pending--;
                if (pending === 0 && totalCost > 0) {{
                    const totalPnl = totalVal - totalCost;
                    const totalPnlPct = (totalPnl / totalCost) * 100;
                    document.getElementById('total-value').textContent = totalVal.toFixed(2) + ' \\u20ac';
                    document.getElementById('total-pnl').textContent = (totalPnl >= 0 ? '+' : '') + totalPnl.toFixed(2) + ' \\u20ac';
                    document.getElementById('total-pnl').className = totalPnl >= 0 ? 'value green' : 'value red';
                    document.getElementById('total-pnlpct').textContent = (totalPnlPct >= 0 ? '+' : '') + totalPnlPct.toFixed(2) + '%';
                    document.getElementById('total-pnlpct').className = totalPnlPct >= 0 ? 'value green' : 'value red';
                }}
            }});
    }});
    if (pending === 0 && totalCost > 0) {{
        const totalPnl = totalVal - totalCost;
        const totalPnlPct = (totalPnl / totalCost) * 100;
        document.getElementById('total-value').textContent = totalVal.toFixed(2) + ' \\u20ac';
        document.getElementById('total-pnl').textContent = (totalPnl >= 0 ? '+' : '') + totalPnl.toFixed(2) + ' \\u20ac';
        document.getElementById('total-pnl').className = totalPnl >= 0 ? 'value green' : 'value red';
        document.getElementById('total-pnlpct').textContent = (totalPnlPct >= 0 ? '+' : '') + totalPnlPct.toFixed(2) + '%';
        document.getElementById('total-pnlpct').className = totalPnlPct >= 0 ? 'value green' : 'value red';
    }}
}}
window.addEventListener('DOMContentLoaded', liveUpdate);
</script>

<p class="small" style="margin-top:30px;text-align:center;color:#aaa;">
  Generado autom\u00e1ticamente \u00b7 yfinance \u00b7 EVA con WACC 8%<br>
  <span class="green">Verde = bueno</span> \u00b7
  <span class="yellow">Amarillo = precauci\u00f3n</span> \u00b7
  <span class="red">Rojo = atenci\u00f3n</span><br>
  Precios en vivo al recargar (requiere servidor local: python server.py)
</p>
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
