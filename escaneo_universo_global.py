# -*- coding: utf-8 -*-
"""
Escaneo de universo global (Fase 1) - ejecucion real, no diagnostico.

Ejecucion aparte de generate_dashboard.py (no integrado en el pipeline
todavia -- eso viene despues de ver resultados reales). Construye el
universo aproximado de ~9.800 tickers acordado en el plan (Europa con
indices secundarios, Norteamerica, Latam, Asia desarrollada, China/Asia
emergente recortada a indices de referencia blue-chip, Oriente Medio,
Africa viable) a partir de tablas de Wikipedia (mismo patron que
download_index_tickers() en screener.py), y aplica el filtro barato de
Fase 1 (ROE>0, FCF>0, Deuda Neta/EBITDA<=4 o no disponible) via
yfinance .info.

Cachea resultados incrementalmente en universo_global_fase1_cache.json
(ticker -> {roe, fcf, deuda_neta_ebitda, pasa_fase1, nombre_indice,
fecha}) para poder interrumpir/reanudar sin perder progreso.

Uso: python escaneo_universo_global.py
"""
import os, sys, json, time
from datetime import datetime

_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)

import pandas as pd
import requests
import yfinance as yf

_YF_SESSION = requests.Session()
_YF_SESSION.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

CACHE_FILE = os.path.join(_PROJ_DIR, "universo_global_fase1_cache.json")
LOG_FILE = os.path.join(_PROJ_DIR, "universo_global_scan.log")

# Tickers ya cubiertos por el sistema actual (watchlist + cartera) -- se
# excluyen del escaneo, el objetivo es encontrar candidatos NUEVOS.
YA_CONOCIDOS = {
    "GOOGL", "ACS.MC", "SAF.PA", "THEON.AS", "XTN", "XPO", "XSMO", "XNTK",
    "XNAS.DE", "XSD", "XMMO", "ITX.MC", "RRU.DE", "NVD.DE", "DANR.MI",
}

def _log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _clean_ticker(raw, suffix):
    if not isinstance(raw, str):
        return None
    t = raw.strip().upper().replace(" ", "")
    if not t or t in ("NAN", "N/A"):
        return None
    # Algunas tablas de wikipedia ya traen sufijo tipo BME:XXX o similar
    t = t.split(":")[-1]
    if suffix and not t.endswith(suffix):
        t = t + suffix
    return t


def _scrape(url, col_candidates, suffix, table_idx=None):
    """Descarga tickers de una tabla de wikipedia. Devuelve lista (puede
    estar vacia si no se encuentra columna reconocible).
    pd.read_html(url) directo da 403 en Wikipedia (sin User-Agent de
    navegador) -- se descarga el HTML primero con _YF_SESSION (que ya
    lleva User-Agent) y se parsea desde el string, igual que deberia
    arreglarse tambien download_index_tickers() en screener.py, que usa
    el mismo patron roto y probablemente esta fallando en produccion."""
    out = []
    try:
        resp = _YF_SESSION.get(url, timeout=15)
        resp.raise_for_status()
        import io
        tables = pd.read_html(io.StringIO(resp.text))
        candidatos = [tables[table_idx]] if table_idx is not None else tables
        for t in candidatos:
            for col in col_candidates:
                if col in t.columns:
                    for raw in t[col].tolist():
                        tk = _clean_ticker(raw, suffix)
                        if tk:
                            out.append(tk)
                    break
    except Exception as e:
        _log(f"  ERROR scraping {url}: {e}")
    return out


# ========== FUENTES POR INDICE (mejor esfuerzo, Wikipedia) ==========
# (nombre_indice, url, columnas_candidatas, sufijo_yfinance, indice_tabla)
FUENTES = [
    ("IBEX 35", "https://en.wikipedia.org/wiki/IBEX_35", ["Ticker", "Symbol"], ".MC", None),
    ("CAC 40", "https://en.wikipedia.org/wiki/CAC_40", ["Ticker"], ".PA", None),
    ("DAX 40", "https://en.wikipedia.org/wiki/DAX", ["Ticker"], ".DE", None),
    ("FTSE MIB", "https://en.wikipedia.org/wiki/FTSE_MIB", ["Ticker"], ".MI", None),
    ("AEX", "https://en.wikipedia.org/wiki/AEX_index", ["Ticker"], ".AS", None),
    ("BEL 20", "https://en.wikipedia.org/wiki/BEL_20", ["Ticker"], ".BR", None),
    ("SMI", "https://en.wikipedia.org/wiki/Swiss_Market_Index", ["Ticker"], ".SW", None),
    ("FTSE 100", "https://en.wikipedia.org/wiki/FTSE_100_Index", ["Ticker", "EPIC"], ".L", None),
    ("FTSE 250", "https://en.wikipedia.org/wiki/FTSE_250_Index", ["Ticker", "EPIC"], ".L", None),
    ("OMXS30", "https://en.wikipedia.org/wiki/OMX_Stockholm_30", ["Ticker"], ".ST", None),
    ("WIG20", "https://en.wikipedia.org/wiki/WIG20", ["Ticker"], ".WA", None),
    ("ATX", "https://en.wikipedia.org/wiki/ATX_(index)", ["Ticker"], ".VI", None),
    ("ATHEX Composite", "https://en.wikipedia.org/wiki/Athens_Stock_Exchange", ["Ticker"], ".AT", None),
    ("S&P 500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ["Symbol"], "", 0),
    ("S&P MidCap 400", "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", ["Symbol", "Ticker symbol"], "", None),
    ("Nasdaq 100", "https://en.wikipedia.org/wiki/Nasdaq-100", ["Ticker", "Symbol"], "", None),
    ("Dow Jones 30", "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", ["Symbol"], "", None),
    ("TSX 60", "https://en.wikipedia.org/wiki/S%26P/TSX_60", ["Ticker", "Symbol"], ".TO", None),
    ("IBOVESPA", "https://en.wikipedia.org/wiki/List_of_companies_listed_on_the_S%C3%A3o_Paulo_Stock_Exchange", ["Ticker"], ".SA", None),
    ("IPSA", "https://en.wikipedia.org/wiki/S%26P_IPSA", ["Ticker"], ".SN", None),
    ("Nikkei 225", "https://en.wikipedia.org/wiki/Nikkei_225", ["Code"], ".T", None),
    ("ASX 200", "https://en.wikipedia.org/wiki/S%26P/ASX_200", ["Code", "ASX code"], ".AX", None),
    ("STI", "https://en.wikipedia.org/wiki/Straits_Times_Index", ["Ticker"], ".SI", None),
    ("KOSPI 200", "https://en.wikipedia.org/wiki/KOSPI200", ["Code", "Ticker"], ".KS", None),
    ("TASI (Arabia Saudi)", "https://en.wikipedia.org/wiki/Tadawul_All_Share_Index", ["Ticker", "Code"], ".SR", None),
    ("TA-35", "https://en.wikipedia.org/wiki/TA-35_Index", ["Ticker"], ".TA", None),
    ("BIST 100", "https://en.wikipedia.org/wiki/BIST_100", ["Ticker", "Code"], ".IS", None),
    ("JSE Top 40", "https://en.wikipedia.org/wiki/FTSE/JSE_Africa_Index_Series", ["Ticker", "Code"], ".JO", None),
]


def construir_universo():
    universo = {}  # ticker -> nombre_indice (primera fuente que lo aporta)
    for nombre, url, cols, suffix, tbl in FUENTES:
        antes = len(universo)
        tickers = _scrape(url, cols, suffix, tbl)
        nuevos = 0
        for tk in tickers:
            if tk not in universo:
                universo[tk] = nombre
                nuevos += 1
        _log(f"{nombre}: {len(tickers)} tickers extraidos, {nuevos} nuevos (total acumulado {len(universo)})")
        time.sleep(0.3)
    return universo


def _get_yf_fundamentales(ticker):
    try:
        info = yf.Ticker(ticker, session=_YF_SESSION).info
        roe = info.get("returnOnEquity")
        fcf = info.get("freeCashflow")
        debt = info.get("totalDebt")
        cash = info.get("totalCash")
        ebitda = info.get("ebitda")
        deuda_neta_ebitda = None
        if debt is not None and cash is not None and ebitda not in (None, 0):
            deuda_neta_ebitda = round((debt - cash) / ebitda, 2)
        return {
            "roe": round(roe * 100, 2) if roe is not None else None,
            "fcf": fcf,
            "deuda_neta_ebitda": deuda_neta_ebitda,
            "nombre_empresa": info.get("shortName"),
            "sector": info.get("sector"),
        }
    except Exception as e:
        return {"error": str(e)}


def _load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def ejecutar_fase1():
    _log("=== INICIO ESCANEO UNIVERSO GLOBAL - FASE 1 ===")
    universo = construir_universo()
    universo = {tk: idx for tk, idx in universo.items() if tk not in YA_CONOCIDOS}
    _log(f"Universo total construido (excluyendo ya conocidos): {len(universo)} tickers")

    cache = _load_cache()
    pendientes = [tk for tk in universo if tk not in cache or cache[tk].get("error")]
    _log(f"Pendientes de evaluar (no en cache o con error previo): {len(pendientes)}")

    procesados = 0
    pasados = 0
    for tk in pendientes:
        datos = _get_yf_fundamentales(tk)
        datos["indice_origen"] = universo[tk]
        datos["fecha"] = datetime.now().isoformat()
        if "error" not in datos:
            roe, fcf, deuda = datos.get("roe"), datos.get("fcf"), datos.get("deuda_neta_ebitda")
            pasa = (roe is not None and roe > 0) and (fcf is not None and fcf > 0) and \
                   (deuda is None or deuda <= 4)
            datos["pasa_fase1"] = pasa
            if pasa:
                pasados += 1
        cache[tk] = datos
        procesados += 1
        if procesados % 25 == 0:
            _save_cache(cache)
            _log(f"  progreso: {procesados}/{len(pendientes)} procesados, {pasados} pasan Fase 1 hasta ahora")
        time.sleep(0.5)
    _save_cache(cache)
    _log(f"=== FIN FASE 1: {procesados} procesados, {pasados} pasan el filtro ===")
    return cache


if __name__ == "__main__":
    ejecutar_fase1()
