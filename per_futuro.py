# -*- coding: utf-8 -*-
"""
PER futuro (forward PE) + PEG + rev_growth con jerarquia de fuentes:
  1. Alpha Vantage OVERVIEW (solo tickers con par US: NVD.DE->NVDA, GOOGL)
  2. yfinance .info local (DANR.MI forwardPE, revenueGrowth local)
  3. Cache manual per_futuro_manual.json (solo edicion manual, nunca sobrescribe)

AV respeta las ventanas horarias aprobadas (10:00 y 17:30 CET) y los rate
limits: solo se llama a OVERVIEW cuando no hay cache fresca y se esta en
ventana; el resultado se persiste en per_futuro_av_cache.json (TTL 24h).

ALPHA_VANTAGE_API_KEY se lee de os.environ (nunca hardcodeada), mismo
patron que el resto del proyecto.
"""
import os, sys, json, urllib.request
from datetime import datetime

_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)

import requests
import yfinance as yf

_YF_SESSION = requests.Session()
_YF_SESSION.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

# Ticker de la cartera/watchlist -> simbolo US cubierto por Alpha Vantage OVERVIEW
AV_TICKER_MAP = {
    "NVD.DE": "NVDA",
    "GOOGL": "GOOGL",
}

MANUAL_FILE = os.path.join(_PROJ_DIR, "per_futuro_manual.json")
AV_CACHE_FILE = os.path.join(_PROJ_DIR, "per_futuro_av_cache.json")
AV_CACHE_TTL = 24 * 3600  # 24h

_YF_INFO_CACHE = {}  # in-memory, evita repetir .info en un mismo proceso


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


def _in_av_window():
    """Ventanas aprobadas para Alpha Vantage: 10:00 y 17:30 CET (Europe/Madrid)."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Madrid"))
        return now.hour == 10 or (now.hour == 17 and now.minute >= 30)
    except Exception:
        return False


def _load_av_cache():
    return _read_json(AV_CACHE_FILE, {})


def _save_av_cache(data):
    _write_json(AV_CACHE_FILE, data)


def _fetch_av_overview(symbol):
    """Consulta OVERVIEW de Alpha Vantage. Devuelve dict crudo o None.
    Solo se usa dentro de la ventana aprobada (controlado por get_per_futuro)."""
    key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not key:
        return None
    try:
        url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={symbol}&apikey={key}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        if not isinstance(data, dict) or "Symbol" not in data:
            # 'Information' = rate limit / plan; no cachear
            return None
        return data
    except Exception:
        return None


def _get_av_data(ticker):
    """(fwd_per, peg) via Alpha Vantage OVERVIEW, respetando ventana + cache 24h.
    Fuera de ventana reusa cache; si no hay cache fresca devuelve (None, None)."""
    symbol = AV_TICKER_MAP.get(ticker)
    if not symbol:
        return None, None
    cache = _load_av_cache()
    entry = cache.get(symbol)
    now = datetime.now()
    if entry and "data" in entry:
        try:
            updated = datetime.fromisoformat(entry["updated"])
            age = (now - updated).total_seconds()
            if age < AV_CACHE_TTL:
                return entry["data"].get("ForwardPE"), entry["data"].get("PEGRatio")
        except Exception:
            pass
    # Cache no fresca: solo refetch dentro de la ventana aprobada
    if not _in_av_window():
        return None, None
    data = _fetch_av_overview(symbol)
    if not data:
        return None, None
    try:
        fwd = float(data["ForwardPE"]) if data.get("ForwardPE") not in (None, "", "None") else None
        peg = float(data["PEGRatio"]) if data.get("PEGRatio") not in (None, "", "None") else None
    except (TypeError, ValueError):
        fwd = peg = None
    cache[symbol] = {"updated": now.isoformat(), "data": {"ForwardPE": fwd, "PEGRatio": peg}}
    _save_av_cache(cache)
    return fwd, peg


def _get_yf_info(ticker):
    """info de yfinance (local). Cache en memoria por proceso."""
    if ticker in _YF_INFO_CACHE:
        return _YF_INFO_CACHE[ticker]
    try:
        info = yf.Ticker(ticker, session=_YF_SESSION).info or {}
        _YF_INFO_CACHE[ticker] = info
        return info
    except Exception:
        _YF_INFO_CACHE[ticker] = None
        return None


def _load_manual():
    return _read_json(MANUAL_FILE, {})


def get_per_futuro(ticker):
    """PER futuro + PEG + rev_growth para un ticker.

    Jerarquia por campo:
      fwd_per:   AV OVERVIEW -> yfinance .info forwardPE -> manual per_futuro
      peg:       AV OVERVIEW PEGRatio -> yfinance (calcular_peg) -> manual peg
      rev_growth:yfinance .info revenueGrowth (*100 -> %) -> manual rev_growth

    Devuelve dict:
      {"fwd_per", "peg", "rev_growth",          # floats (o None)
       "fuente_per", "fuente_peg", "fuente_rev",# 'av' | 'yfinance' | 'manual' | None
       "fecha_manual"}                           # 'YYYY-MM-DD' o None
    """
    res = {
        "fwd_per": None, "peg": None, "rev_growth": None,
        "fuente_per": None, "fuente_peg": None, "fuente_rev": None,
        "fecha_manual": None,
    }
    # 1) AV OVERVIEW (mapping a ticker US)
    av_fwd, av_peg = _get_av_data(ticker)
    if av_fwd is not None and av_fwd > 0:
        res["fwd_per"] = round(av_fwd, 2)
        res["fuente_per"] = "av"
    if av_peg is not None and av_peg > 0:
        res["peg"] = round(av_peg, 2)
        res["fuente_peg"] = "av"
    # 2) yfinance .info local (solo rellena huecos)
    info = _get_yf_info(ticker)
    if info:
        if res["fwd_per"] is None:
            fwd = info.get("forwardPE")
            if fwd is not None:
                try:
                    fwd = float(fwd)
                    if fwd > 0:
                        res["fwd_per"] = round(fwd, 2)
                        res["fuente_per"] = "yfinance"
                except (TypeError, ValueError):
                    pass
        if res["peg"] is None:
            from screener import calcular_peg_desde_info
            peg = calcular_peg_desde_info(info)
            if peg is not None and peg > 0:
                res["peg"] = round(peg, 2)
                res["fuente_peg"] = "yfinance"
        if res["rev_growth"] is None:
            rg = info.get("revenueGrowth")
            if rg is not None:
                try:
                    rg = float(rg)
                    if rg != rg:  # NaN
                        rg = None
                except (TypeError, ValueError):
                    rg = None
                if rg is not None:
                    res["rev_growth"] = round(rg * 100, 2)  # decimal -> %
                    res["fuente_rev"] = "yfinance"
    # 3) Cache manual (solo edicion manual)
    manual = _load_manual().get(ticker)
    if manual and isinstance(manual, dict):
        fecha = manual.get("fecha_actualizacion") or ""
        if res["fwd_per"] is None and manual.get("per_futuro") is not None:
            try:
                res["fwd_per"] = float(manual["per_futuro"])
                res["fuente_per"] = "manual"
                res["fecha_manual"] = fecha
            except (TypeError, ValueError):
                pass
        if res["peg"] is None and manual.get("peg") is not None:
            try:
                res["peg"] = float(manual["peg"])
                res["fuente_peg"] = "manual"
                res["fecha_manual"] = res["fecha_manual"] or fecha
            except (TypeError, ValueError):
                pass
        if res["rev_growth"] is None and manual.get("rev_growth") is not None:
            try:
                res["rev_growth"] = float(manual["rev_growth"])
                res["fuente_rev"] = "manual"
                res["fecha_manual"] = res["fecha_manual"] or fecha
            except (TypeError, ValueError):
                pass
    return res


if __name__ == "__main__":
    for tk in ["NVD.DE", "RRU.DE", "DANR.MI", "GOOGL", "ACS.MC", "ITX.MC", "SAF.PA", "THEON.AS"]:
        d = get_per_futuro(tk)
        print(tk, "->", d)
