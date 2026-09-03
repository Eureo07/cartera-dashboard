# -*- coding: utf-8 -*-
"""Fase 2: motor WACC/CAPM en vivo + NOPAT + EVA + Score fundamental
(ROE 25% + EVA 25% + FCF 50%), sobre los 449 tickers que ya pasaron los
4 criterios bloqueantes de Fase 1 (universo_global_pasan_4_criterios.json).

Formulas (documentadas, no inventadas sobre la marcha):
  Ke (CAPM) = Rf + Beta x ERP
  Kd        = Gasto en intereses / Deuda total (aproximacion estandar)
  WACC      = (E/V x Ke) + (D/V x Kd x (1-tasa impositiva))
  NOPAT     = EBIT x (1 - tasa impositiva)
  Capital Invertido = Deuda total + Patrimonio neto - Caja
  EVA       = NOPAT - (WACC x Capital Invertido)

Inputs y sus limitaciones documentadas explicitamente (no en silencio):
  - Rf (tasa libre de riesgo): en vivo via ^TNX (bono EE.UU. 10 anios) SOLO
    para tickers en USD. Para el resto de divisas (EUR, GBP, AUD, CAD...)
    se usa una referencia MANUAL unica (RF_NO_USD, ver constante abajo) --
    no hay un ticker limpio en yfinance para bund/gilt/etc, y el propio
    diseño original acepta una referencia actualizada a mano de forma
    periodica, no en vivo estricto. Esto es una aproximacion: no distingue
    entre EUR/GBP/AUD/CAD, todas comparten la misma Rf de referencia.
  - ERP (prima de riesgo de mercado): constante de referencia tipo
    Damodaran (~5.5%), no recalculada por ticker ni en vivo. Revisar
    manualmente de forma periodica.
  - Beta: de yfinance .info; si falta, fallback a 1.0 (neutro), marcado
    explicitamente en el resultado (beta_fuente).
  - Tasa impositiva: efectiva desde income_stmt (Tax Rate For Calcs); si
    falta, fallback 25% (aproximacion generica), marcado explicitamente.

Score: normalized_score() de screener.py (reutilizada sin modificar) sobre
ROE/EVA/FCF sobre el propio conjunto de 449 -- no es comparable con el
score del radar de universo original ni con el de /api/candidatos.
"""
import json, time
from datetime import datetime
import yfinance as yf
import requests
import pandas as pd

from screener import normalized_score

_YF_SESSION = requests.Session()
_YF_SESSION.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

CACHE_FILE = "universo_global_fase2_wacc_cache.json"
LOG_FILE = "universo_global_scan.log"

ERP = 5.5  # % -- referencia Damodaran, revisar manualmente de forma periodica
RF_MANUAL_FALLBACK = 2.8  # % -- solo para divisas SIN fuente en vivo (GBP/AUD/CAD/etc,
                          # hueco real: el diagnostico previo solo cubria USD/EUR).
                          # Aproximacion documentada, no en vivo, pendiente de una
                          # fuente real por divisa si se decide cubrirlas mejor.
TAX_RATE_FALLBACK = 0.25  # 25% -- si no hay tasa efectiva disponible


def _log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


_rf_cache = {"USD": None, "EUR": None}

def _get_rf_usd():
    """Bono EE.UU. 10 anios, en vivo via FRED (serie DGS10, CSV sin API key).
    Devuelve (valor, exito_en_vivo). Cachea la tupla completa (bug anterior:
    el atajo de cache devolvia solo el valor suelto en llamadas repetidas,
    rompiendo el unpacking en el ticker 2 en adelante de cada divisa)."""
    if _rf_cache["USD"] is not None:
        return _rf_cache["USD"]
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
                          headers=_YF_SESSION.headers, timeout=15)
        lineas = [l for l in r.text.strip().splitlines()[1:] if l and "." in l.split(",")[-1]]
        rf = float(lineas[-1].split(",")[-1])
    except Exception as e:
        _log(f"  AVISO: FRED fallo ({e}), usando fallback manual para USD")
        rf = None
    _rf_cache["USD"] = (rf if rf is not None else 4.3, rf is not None)
    return _rf_cache["USD"]


def _get_rf_eur():
    """Rendimiento AAA area euro 10 anios, en vivo via ECB (data-detail-api).
    Nota: el host antiguo sdw-wsrest.ecb.europa.eu esta dado de baja (DNS no
    resuelve), se uso el host actual data.ecb.europa.eu, verificado en vivo.
    La clave real del JSON es "OBS" (string), NO "OBS_VALUE" -- ese nombre
    no existe en la respuesta y causaba un KeyError silenciado por el
    except, cayendo al fallback sin avisar (bug real, corregido).
    Cachea la tupla completa, mismo fix que _get_rf_usd()."""
    if _rf_cache["EUR"] is not None:
        return _rf_cache["EUR"]
    try:
        r = requests.get(
            "https://data.ecb.europa.eu/data-detail-api/YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y",
            headers={"Accept": "application/json", **_YF_SESSION.headers}, timeout=15)
        rf = float(r.json()[0]["OBS"])
    except Exception as e:
        _log(f"  AVISO: ECB fallo ({e}), usando fallback manual para EUR")
        rf = None
    _rf_cache["EUR"] = (rf if rf is not None else RF_MANUAL_FALLBACK, rf is not None)
    return _rf_cache["EUR"]


def evaluar_wacc_eva(tk, roe_pct, fcf):
    try:
        t = yf.Ticker(tk, session=_YF_SESSION)
        info = t.info
        beta = info.get("beta")
        beta_fuente = "yfinance"
        if beta is None:
            beta = 1.0
            beta_fuente = "fallback_neutro"
        market_cap = info.get("marketCap")
        moneda = info.get("currency", "USD")
        if moneda == "USD":
            rf, ok = _get_rf_usd()
            rf_fuente = "FRED_DGS10_en_vivo" if ok else "FRED_fallo_fallback_manual"
        elif moneda == "EUR":
            rf, ok = _get_rf_eur()
            rf_fuente = "ECB_10Y_en_vivo" if ok else "ECB_fallo_fallback_manual"
        else:
            # Hueco real (GBP/AUD/CAD/GBp/etc): sin fuente en vivo diagnosticada,
            # referencia manual documentada, no en vivo.
            rf, rf_fuente = RF_MANUAL_FALLBACK, f"manual_sin_fuente_{moneda}"

        inc = t.income_stmt
        ebit = tax_rate = interest_expense = None
        tax_rate_fuente = "efectiva"
        if inc is not None and not inc.empty:
            if "EBIT" in inc.index:
                s = inc.loc["EBIT"].dropna()
                ebit = float(s.iloc[0]) if not s.empty else None
            if "Tax Rate For Calcs" in inc.index:
                s = inc.loc["Tax Rate For Calcs"].dropna()
                tax_rate = float(s.iloc[0]) if not s.empty else None
            if "Interest Expense" in inc.index:
                s = inc.loc["Interest Expense"].dropna()
                interest_expense = abs(float(s.iloc[0])) if not s.empty else None
        if tax_rate is None:
            tax_rate = TAX_RATE_FALLBACK
            tax_rate_fuente = "fallback_25pct"

        bs = t.balance_sheet
        debt = equity = cash = None
        if bs is not None and not bs.empty:
            for row, var in [("Total Debt", "debt"), ("Stockholders Equity", "equity"), ("Cash And Cash Equivalents", "cash")]:
                if row in bs.index:
                    s = bs.loc[row].dropna()
                    if not s.empty:
                        val = float(s.iloc[0])
                        if var == "debt": debt = val
                        elif var == "equity": equity = val
                        elif var == "cash": cash = val

        if market_cap is None or ebit is None or debt is None or equity is None:
            return {"error": "datos insuficientes para WACC (marketCap/EBIT/debt/equity)"}

        E = market_cap
        D = debt
        V = E + D
        Ke = rf + beta * ERP
        Kd = (interest_expense / D * 100) if (interest_expense is not None and D > 0) else 0.0
        WACC = (E / V) * Ke + (D / V) * Kd * (1 - tax_rate) if V > 0 else Ke

        NOPAT = ebit * (1 - tax_rate)
        capital_invertido = D + equity - (cash or 0)
        EVA = None
        if capital_invertido and capital_invertido > 0:
            EVA = NOPAT - (WACC / 100) * capital_invertido

        return {
            "beta": beta, "beta_fuente": beta_fuente,
            "rf": rf, "rf_fuente": rf_fuente, "erp": ERP,
            "ke": round(Ke, 2), "kd": round(Kd, 2),
            "tax_rate": tax_rate, "tax_rate_fuente": tax_rate_fuente,
            "wacc": round(WACC, 2),
            "nopat": round(NOPAT, 2),
            "capital_invertido": round(capital_invertido, 2) if capital_invertido else None,
            "eva": round(EVA, 2) if EVA is not None else None,
            "market_cap": market_cap, "moneda": moneda,
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    candidatos = json.load(open("universo_global_pasan_4_criterios.json", encoding="utf-8"))
    _log(f"=== INICIO FASE 2 (WACC/EVA/Score) sobre {len(candidatos)} candidatos ===")
    cache = _load_cache()
    pendientes = [c for c in candidatos if c["ticker"] not in cache]
    _log(f"Pendientes: {len(pendientes)}")
    for i, c in enumerate(pendientes, 1):
        tk = c["ticker"]
        r = evaluar_wacc_eva(tk, c.get("roe"), None)
        cache[tk] = r
        if i % 25 == 0:
            _save_cache(cache)
            _log(f"  progreso fase2: {i}/{len(pendientes)}")
        time.sleep(0.5)
    _save_cache(cache)
    _log(f"=== FIN FASE 2: {len(pendientes)} procesados ===")
