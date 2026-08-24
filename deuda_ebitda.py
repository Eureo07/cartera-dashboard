# -*- coding: utf-8 -*-
"""
Deuda Neta / EBITDA por ticker, solo para uso LOCAL.

No hay ninguna fuente de este dato ya integrada en el pipeline (no esta en
el CSV de Eurekers, y FMP free tier solo cubre tickers US). La unica fuente
disponible es yfinance .info (totalDebt, totalCash, ebitda), que esta
bloqueado en las IPs de datacenter de Render — por eso este modulo se
ejecuta EXCLUSIVAMENTE dentro de `python generate_dashboard.py` en local, y
persiste el resultado en deuda_ebitda_cache.json. server.py (proceso vivo
en Render) solo LEE ese cache, nunca vuelve a llamar a yfinance .info.

Mismo patron de cache que per_futuro.py (AV_CACHE_FILE / MANUAL_FILE).
"""
import os, sys, json
from datetime import datetime

_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)

import requests
import yfinance as yf

_YF_SESSION = requests.Session()
_YF_SESSION.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

CACHE_FILE = os.path.join(_PROJ_DIR, "deuda_ebitda_cache.json")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def calcular_deuda_neta_ebitda(ticker):
    """Deuda neta / EBITDA via yfinance .info. Solo funciona en local (en
    Render .info esta bloqueado). Devuelve float o None."""
    try:
        info = yf.Ticker(ticker, session=_YF_SESSION).info or {}
        total_debt = info.get("totalDebt")
        total_cash = info.get("totalCash")
        ebitda = info.get("ebitda")
        if total_debt is None or total_cash is None or ebitda in (None, 0):
            return None
        deuda_neta = float(total_debt) - float(total_cash)
        return round(deuda_neta / float(ebitda), 2)
    except Exception:
        return None


def actualizar_cache(tickers):
    """Recalcula deuda_neta/EBITDA para una lista de tickers y actualiza
    deuda_ebitda_cache.json (solo se llama desde generate_dashboard.py en
    local). Conserva las entradas de tickers no incluidos en `tickers`."""
    cache = _read_json(CACHE_FILE, {})
    now_iso = datetime.now().isoformat()
    for ticker in tickers:
        ratio = calcular_deuda_neta_ebitda(ticker)
        if ratio is not None:
            cache[ticker] = {"deuda_neta_ebitda": ratio, "fecha_actualizacion": now_iso}
    _write_json(CACHE_FILE, cache)
    return cache


def get_deuda_neta_ebitda_cacheada(ticker):
    """Lectura pura de cache, segura de llamar desde server.py en Render:
    nunca dispara una llamada de red. Devuelve dict
    {"deuda_neta_ebitda","fecha_actualizacion"} o None si no hay cache para
    ese ticker."""
    cache = _read_json(CACHE_FILE, {})
    return cache.get(ticker)


if __name__ == "__main__":
    for tk in ["NVD.DE", "DANR.MI", "ITX.MC", "SAF.PA", "ACS.MC"]:
        print(tk, "->", calcular_deuda_neta_ebitda(tk))
