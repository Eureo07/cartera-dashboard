# -*- coding: utf-8 -*-
"""
Screener automático de oportunidades de inversión.
Analiza un universo de empresas (IBEX 35, Euro Stoxx 50, DAX 40, CAC 40, FTSE 100, S&P 500, MIB 40)
y filtra por criterios: no en cartera, Rent.1A > 0, PER 0-30, Score > 0.5, EVA > 0.
Añade sección "Radar — Oportunidades de Entrada" al dashboard.html
"""
import sys, os
_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)
import pandas as pd
import yfinance as yf
import json, math, re
from datetime import datetime

from config_loader import CFG, get_logger



log = get_logger("screener")
anomaly_log = get_logger("anomalias_screener")

# ========== CONFIG ==========
BASE_DIR = CFG["base_dir"]
EXCEL_FILE = CFG["paths"]["excel"]
DASHBOARD_FILE = CFG["paths"]["dashboard"]
CACHE_FILE = CFG["paths"]["tickers_universe"]
MAX_RESULTS = CFG["screener"]["max_results"]
SCORE_THRESHOLD = CFG["screener"]["score_threshold"]

# ========== PORTFOLIO ==========
portfolio = CFG["portfolio"]
portfolio_tickers = set()
for p in portfolio:
    portfolio_tickers.add(p["ticker"])
    if p.get("db_ticker"):
        portfolio_tickers.add(p["db_ticker"])

def is_in_portfolio(ticker, name):
    if ticker in portfolio_tickers:
        return True
    for p in portfolio:
        if p["name"].strip().lower() == name.strip().lower():
            return True
    return False

# ========== TICKER HELPERS ==========
_yf_cache = {}
def get_valuation(t):
    if t in _yf_cache:
        return _yf_cache[t]
    try:
        stock = yf.Ticker(t)
        info = stock.info
        result = {
            "per": info.get("trailingPE"),
            "fwd_per": info.get("forwardPE"),
            "pb": info.get("priceToBook"),
            "mcap": info.get("marketCap"),
            "div_yield": info.get("dividendYield"),
        }
        _yf_cache[t] = result
        return result
    except:
        _yf_cache[t] = {}
        return {}

_hist_cache = {}
_rent_cache = {}
def get_1y_return_and_hist(t):
    """Returns (return_pct, hist_df). Caches both."""
    if t in _rent_cache and t in _hist_cache:
        return _rent_cache[t], _hist_cache.get(t)
    try:
        stock = yf.Ticker(t)
        hist = stock.history(period="1y", auto_adjust=False)
        if hist is None or hist.empty:
            _rent_cache[t] = None
            _hist_cache[t] = None
            return None, None
        close = hist["Close"].dropna()
        if len(close) < 2:
            _rent_cache[t] = None
            _hist_cache[t] = hist
            return None, hist
        ret = (float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0]) * 100
        _rent_cache[t] = ret
        _hist_cache[t] = hist
        return ret, hist
    except:
        _rent_cache[t] = None
        _hist_cache[t] = None
        return None, None

def get_1y_return(t):
    r, _ = get_1y_return_and_hist(t)
    return r

def get_effective_per(t):
    pv = get_valuation(t)
    fwd = pv.get("fwd_per")
    if fwd is not None and fwd > 0:
        return fwd
    tr = pv.get("per")
    if tr is not None and tr > 0:
        return tr
    return None

def val_metric(val, min_v, max_v):
    if val is None:
        return None
    if isinstance(val, float) and (pd.isna(val) or math.isnan(val)):
        return None
    if min_v <= val <= max_v:
        return val
    return None

# ========== DOWNLOAD UNIVERSE ==========
def download_index_tickers():
    tickers = []
    try:
        # IBEX 35
        tables = pd.read_html("https://en.wikipedia.org/wiki/IBEX_35")
        for t in tables:
            if "Ticker" in t.columns:
                tickers.extend(t["Ticker"].tolist())
            elif "Symbol" in t.columns:
                tickers.extend(t["Symbol"].tolist())
    except:
        pass
    try:
        # S&P 500
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        if len(tables) >= 1:
            sp500 = tables[0]
            if "Symbol" in sp500.columns:
                tickers.extend(sp500["Symbol"].tolist())
    except:
        pass
    try:
        # FTSE 100
        tables = pd.read_html("https://en.wikipedia.org/wiki/FTSE_100_Index")
        for t in tables:
            if "Ticker" in t.columns:
                tickers.extend(t["Ticker"].tolist())
            elif "Epic" in t.columns:
                tickers.extend(t["Epic"].tolist())
    except:
        pass
    # Clean and deduplicate
    cleaned = set()
    for t in tickers:
        if isinstance(t, str):
            t = t.strip().upper()
            if t and not re.search(r'[^A-Z0-9.\-]', t):
                cleaned.add(t)
    return sorted(cleaned)

def load_universe_from_excel():
    df = pd.read_excel(EXCEL_FILE)
    indices_of_interest = ["IBEX 35", "DAX 40", "CAC 40", "FTSE 100", "S&P 100"]
    idx_col = df.columns[3]
    mask = df[idx_col].isin(indices_of_interest)
    tickers = df.loc[mask, "Ticker"].dropna().unique().tolist()
    return [str(t).strip() for t in tickers]

def get_universe():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    log.info("Descargando universo de tickers...")
    tickers = load_universe_from_excel()
    downloaded = download_index_tickers()
    all_tickers = sorted(set(tickers + downloaded))
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_tickers, f, ensure_ascii=False)
    log.info(f"  {len(all_tickers)} tickers guardados en {CACHE_FILE}")
    return all_tickers

# ========== TECHNICAL FILTERS ==========
def get_entry_types(ticker):
    """Return list of entry type codes for a ticker (RRA, RR, LT, LTA, PER).
    Uses cached history from get_1y_return_and_hist."""
    _, hist = get_1y_return_and_hist(ticker)
    if hist is None or hist.empty:
        return []
    close = hist["Close"].dropna()
    if len(close) < 2:
        return []
    current = float(close.iloc[-1])
    high52 = float(close.max())
    low52 = float(close.min())
    types = []
    # RRA: current between 95% and 105% of 52W High
    if 0.95 * high52 <= current <= 1.05 * high52:
        types.append("RRA")
    # RR: current above 52W High
    if current > high52:
        types.append("RR")
    # LT: drawdown > 10% from high, but above low52 + 20% of range
    drawdown = (high52 - current) / high52
    if drawdown > 0.10 and current > low52 + 0.20 * (high52 - low52):
        types.append("LT")
    # LTA: 3-month lows > 6-month lows
    now = datetime.now()
    cutoff_3m = now.timestamp() - 90 * 86400
    cutoff_6m = now.timestamp() - 180 * 86400
    recent = close[close.index.astype('int64') // 10**9 > cutoff_3m]
    mid = close[close.index.astype('int64') // 10**9 > cutoff_6m]
    if not recent.empty and not mid.empty:
        if float(recent.min()) > float(mid.min()):
            types.append("LTA")
    # PER: current between low52 and low52 + 10%
    if low52 <= current <= low52 * 1.10:
        types.append("PER")
    return types

# ========== SCREENING ==========
def normalized_score(series):
    mn, mx = series.min(), series.max()
    if mx - mn == 0:
        return pd.Series([0.5] * len(series))
    return (series - mn) / (mx - mn)

def run_screener():
    log.info("Cargando datos financieros...")
    df = pd.read_excel(EXCEL_FILE)
    sec_col = df.columns[4]
    universe_tickers = get_universe()
    df_u = df[df["Ticker"].isin(universe_tickers)].copy()
    df_u = df_u.drop_duplicates(subset="Empresa", keep="first")
    log.info(f"  {len(df_u)} empresas en el universo con datos financieros")
    # Sequential fetch valuations and 1-year returns (no threading, avoids yfinance thread-safety issues)
    all_tickers = df_u["Ticker"].unique().tolist()
    all_tickers = [str(t).strip() for t in all_tickers]
    log.info(f"  Descargando datos de {len(all_tickers)} tickers (secuencial)...")
    val_cache = {}
    rent_cache = {}
    for t in all_tickers:
        val_cache[t] = get_valuation(t) or {}
        rent_cache[t] = get_1y_return(t)
    log.info(f"  Datos descargados. Evaluando criterios...")
    # Local versions using cache
    def cached_eper(t):
        pv = val_cache.get(t, {})
        fwd = pv.get("fwd_per")
        if fwd is not None and fwd > 0:
            return fwd
        tr = pv.get("per")
        if tr is not None and tr > 0:
            return tr
        return None
    def cached_rent(t):
        return rent_cache.get(t)
    results = []
    cnt_total = 0; cnt_port = 0; cnt_rent_none = 0; cnt_rent_neg = 0; cnt_per = 0; cnt_eva_fcf = 0; cnt_roe = 0; cnt_eva_neg = 0; cnt_tec = 0
    for _, row in df_u.iterrows():
        ticker = str(row["Ticker"]).strip()
        name = str(row["Empresa"]).strip()
        sector = str(row[sec_col]).strip()
        cnt_total += 1
        if is_in_portfolio(ticker, name):
            cnt_port += 1
            continue
        rent_1a = cached_rent(ticker)
        if rent_1a is None:
            cnt_rent_none += 1
            continue
        if rent_1a <= 0:
            cnt_rent_neg += 1
            continue
        if rent_1a > 200:
            anomaly_log.warning(f"Rent.1A > 200% excluido: {ticker} ({name}) — Rent.1A: {rent_1a:+.1f}%")
            continue
        eper = cached_eper(ticker)
        per_missing = False
        if eper is None:
            per_missing = True
        elif not (0 <= eper <= 30):
            cnt_per += 1
            continue
        roe = val_metric(row["2026 ROE"], -500, 500)
        eva = val_metric(row["2026 EVA"], -1e12, 1e12)
        fcf = val_metric(row["2026 FCN"], -1e12, 1e12)
        if eva is None or fcf is None:
            cnt_eva_fcf += 1
            continue
        roe_missing = False
        if roe is None:
            roe_missing = True
        elif roe <= 0:
            cnt_roe += 1
            continue
        if eva <= 0:
            cnt_eva_neg += 1
            continue
        entry_types = get_entry_types(ticker)
        if not entry_types:
            cnt_tec += 1
            continue
        results.append({
            "ticker": ticker, "name": name, "sector": sector,
            "roe": roe, "roe_missing": roe_missing, "eva": eva, "fcf": fcf,
            "eper": eper, "per_missing": per_missing, "rent_1a": rent_1a,
            "entry_types": entry_types, "_sec_key": sector,
        })
    log.info(f"  Filtros: total={cnt_total}, port={cnt_port}, rentNone={cnt_rent_none}, rentNeg={cnt_rent_neg}, PER={cnt_per}, evaFcf={cnt_eva_fcf}, roe={cnt_roe}, evaNeg={cnt_eva_neg}, tec={cnt_tec}, passed={len(results)}")
    if not results:
        log.info("  No se encontraron candidatos")
        return []
    rdf = pd.DataFrame(results)
    scores = []
    for sec in rdf["_sec_key"].unique():
        mask = rdf["_sec_key"] == sec
        sub = rdf[mask]
        roe_s = normalized_score(sub["roe"]) * 0.50
        eva_s = normalized_score(sub["eva"]) * 0.25
        fcf_s = normalized_score(sub["fcf"]) * 0.25
        for j in range(len(sub)):
            scores.append(roe_s.iloc[j] + eva_s.iloc[j] + fcf_s.iloc[j])
    rdf["score"] = scores
    rdf = rdf[rdf["score"] > SCORE_THRESHOLD].copy()
    rdf = rdf.sort_values("score", ascending=False)
    final = rdf.head(MAX_RESULTS)
    log.info(f"  {len(final)} oportunidades encontradas (Score > {SCORE_THRESHOLD})")
    return final.to_dict("records")

# ========== DASHBOARD INTEGRATION ==========
DEFENSIVE_SECTORS = {"Salud", "Utilities", "Consumo básico", "Telecomunicaciones", "Comunicaciones", "Alimentación", "Consumo", "Farma"}

def append_radar(results):
    if not os.path.exists(DASHBOARD_FILE):
        log.error(f"{DASHBOARD_FILE} no encontrado")
        return
    with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    # Remove previous radar section if exists
    radar_start = html.find("<!-- RADAR SCREENER -->")
    if radar_start >= 0:
        radar_end = html.find("<!-- END RADAR -->", radar_start)
        if radar_end >= 0:
            html = html[:radar_start] + html[radar_end + len("<!-- END RADAR -->"):]
    # Build new radar section
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    rows_html = ""
    for i, r in enumerate(results, 1):
        eper_str = f"{r['eper']:.1f}" if r['eper'] is not None else "N/D"
        if r['eper'] is None:
            eper_str += " (*)"
        roe_str = f"{r['roe']:.1f}%" if r['roe'] is not None else "N/D"
        if r.get("roe_missing"):
            roe_str += " (*)"
        roe_cls = "green" if r['roe'] and r['roe'] > 15 else ("yellow" if r['roe'] and r['roe'] > 5 else "red")
        eva_str = f"{r['eva']:,.0f}"
        fcf_str = f"{r['fcf']:,.0f}"
        rent_str = f"{r['rent_1a']:+.1f}%"
        rent_cls = "green" if r['rent_1a'] > 10 else ("yellow" if r['rent_1a'] > 0 else "red")
        entry_str = ", ".join(r.get("entry_types", []))
        rows_html += f"""<tr>
  <td>{i}</td>
  <td>{r['name']}</td>
  <td>{r['ticker']}</td>
  <td>{r['sector']}</td>
  <td>{r['score']:.3f}</td>
  <td>{eper_str}</td>
  <td class="{rent_cls}">{rent_str}</td>
  <td class="{roe_cls}">{roe_str}</td>
  <td>{eva_str}</td>
  <td>{fcf_str}</td>
  <td>{entry_str}</td>
</tr>"""
    radar_section = f"""<!-- RADAR SCREENER -->
<h2>Radar \u2014 Oportunidades de Entrada</h2>
<p class="small">\u00daltimo an\u00e1lisis: {now_str} · Criterios: Rent.1A &gt; 0, PER 0-30, Score &gt; 0.3, EVA &gt; 0, ROE &gt; 0 · No en cartera · Técnico: RRA/RR/LT/LTA/PER · (*) no disponible</p>
<table>
<thead><tr>
  <th>#</th><th>Empresa</th><th>Ticker</th><th>Sector</th><th>Score</th><th>PER</th><th>Rent.1A</th><th>ROE</th><th>EVA</th><th>FCF</th><th>Tipo Entrada</th>
</tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<!-- END RADAR -->"""
    # Insert before the closing </body> or at the end
    insert_pos = html.find("</body>")
    if insert_pos < 0:
        html += radar_section
    else:
        html = html[:insert_pos] + radar_section + "\n" + html[insert_pos:]

    # Add defensive sector suggestion if needed
    if "Ninguna posición en sectores defensivos" in html:
        def_candidates = [r for r in results if r["sector"] in DEFENSIVE_SECTORS]
        if def_candidates:
            def_candidates.sort(key=lambda x: x["score"], reverse=True)
            top2 = def_candidates[:2]
            cand_list = ", ".join(f'{r["name"]} ({r["ticker"]}, Score: {r["score"]:.3f})' for r in top2)
            suggestion = f'<div class="div-suggestion">💡 Sugerencia: añadir posición en sector defensivo. Top candidatas: {cand_list}</div>'
            last_item = html.rfind('<div class="div-item"', 0, html.find("Ninguna posición en sectores defensivos") + 80)
            if last_item > 0:
                end_div = html.find("</div>", last_item)
                if end_div > 0:
                    end_div = html.find("</div>", end_div + 6)
                    if end_div > 0:
                        html = html[:end_div] + "\n" + suggestion + html[end_div:]

    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Secci\u00f3n Radar a\u00f1adida a {DASHBOARD_FILE}")

# ========== MAIN ==========
if __name__ == "__main__":
    log.info("=" * 50)
    log.info("SCREENER — Radar de Oportunidades")
    log.info("=" * 50)
    results = run_screener()
    if results:
        log.info(f"\nTop {len(results)} oportunidades:")
        for i, r in enumerate(results, 1):
            eper_str = f"{r['eper']:.1f}" if r['eper'] is not None else "N/D"
            entry_str = ", ".join(r.get("entry_types", []))
            log.info(f"  {i}. {r['name']} ({r['ticker']}) — Score: {r['score']:.3f}, PER: {eper_str}, Rent.1A: {r['rent_1a']:+.1f}%, Entrada: {entry_str}")
        append_radar(results)
    else:
        log.info("No se encontraron oportunidades que cumplan los criterios.")
    log.info("Hecho.")
