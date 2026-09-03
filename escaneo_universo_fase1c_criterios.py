# -*- coding: utf-8 -*-
"""Fase 1c: criterios 2 (ROIC vs ROE), 3 (FCF/Beneficio Neto), 4 (declive de
ingresos) + PEG informativo, sobre los tickers que ya pasaron el criterio 1
(deuda neta/EBITDA, ver _paso_criterio1_temp.json). Criterio 5 (score con
EVA) NO se aplica aqui -- no existe motor de WACC/EVA en el repo, queda
pendiente de decision del usuario.

ROIC = EBIT * (1 - tax_rate) / (Total Debt + Stockholders Equity - Cash),
usando el ultimo periodo disponible de income_stmt/balance_sheet -- no es
un WACC/EVA completo, es el minimo necesario para comparar ROIC vs ROE
segun el criterio 2 tal como esta definido (deteccion de apalancamiento
inflando ROE), sin construir el motor completo.

Declive de ingresos: se considera "declive sostenido" si el ingreso del
ultimo periodo es menor que el de hace 2 Y el de hace 3 periodos
disponibles (monotonía descendente en la ventana reciente) -- definicion
explicita, documentada aqui porque el criterio original no la fija.
"""
import json, time
from datetime import datetime
import yfinance as yf
import requests
from per_futuro import _get_yf_info  # reutiliza cache en memoria de .info si ya se llamo

_YF_SESSION = requests.Session()
_YF_SESSION.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

CACHE_FILE = "universo_global_fase1c_cache.json"
LOG_FILE = "universo_global_scan.log"


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


def _calcular_peg(info):
    from screener import calcular_peg_desde_info
    return calcular_peg_desde_info(info)


def evaluar_ticker(tk, roe_pct):
    try:
        t = yf.Ticker(tk, session=_YF_SESSION)
        info = t.info
        net_income = info.get("netIncomeToCommon")
        fcf = info.get("freeCashflow")
        peg = _calcular_peg(info)

        inc = t.income_stmt
        ebit = None
        tax_rate = None
        revenue_hist = None
        if inc is not None and not inc.empty:
            if "EBIT" in inc.index:
                eb = inc.loc["EBIT"].dropna()
                ebit = float(eb.iloc[0]) if not eb.empty else None
            if "Tax Rate For Calcs" in inc.index:
                tr = inc.loc["Tax Rate For Calcs"].dropna()
                tax_rate = float(tr.iloc[0]) if not tr.empty else None
            if "Total Revenue" in inc.index:
                revenue_hist = [float(x) for x in inc.loc["Total Revenue"].dropna().tolist()]

        bs = t.balance_sheet
        debt = equity = cash = None
        if bs is not None and not bs.empty:
            for row, var in [("Total Debt", "debt"), ("Stockholders Equity", "equity"), ("Cash And Cash Equivalents", "cash")]:
                if row in bs.index:
                    s = bs.loc[row].dropna()
                    if not s.empty:
                        if var == "debt": debt = float(s.iloc[0])
                        elif var == "equity": equity = float(s.iloc[0])
                        elif var == "cash": cash = float(s.iloc[0])

        roic = None
        if ebit is not None and tax_rate is not None and debt is not None and equity is not None:
            invested_capital = debt + equity - (cash or 0)
            if invested_capital and invested_capital > 0:
                roic = round((ebit * (1 - tax_rate)) / invested_capital * 100, 2)

        declive_ingresos = None
        if revenue_hist and len(revenue_hist) >= 3:
            declive_ingresos = revenue_hist[0] < revenue_hist[1] and revenue_hist[0] < revenue_hist[2]

        fcf_sobre_ni = None
        if fcf is not None and net_income not in (None, 0):
            fcf_sobre_ni = round(fcf / net_income, 2)

        # Criterio 2: ROIC << ROE (marcado si ROIC < 50% del ROE, mismo umbral que ya usa /api/candidatos)
        c2_ok = None
        if roic is not None and roe_pct is not None and roe_pct > 0:
            c2_ok = (roic / roe_pct) >= 0.5
        # Criterio 3: FCF/Beneficio Neto >= 0.5
        c3_ok = fcf_sobre_ni is not None and fcf_sobre_ni >= 0.5
        # Criterio 4: sin declive sostenido
        c4_ok = declive_ingresos is not None and not declive_ingresos

        return {
            "net_income": net_income, "fcf": fcf, "peg": peg,
            "roic": roic, "fcf_sobre_ni": fcf_sobre_ni,
            "declive_ingresos": declive_ingresos,
            "c2_roic_vs_roe_ok": c2_ok, "c3_fcf_ni_ok": c3_ok, "c4_sin_declive_ok": c4_ok,
            "datos_suficientes": roic is not None and fcf_sobre_ni is not None and declive_ingresos is not None,
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    candidatos = json.load(open("_paso_criterio1_temp.json", encoding="utf-8"))
    fund = json.load(open("universo_global_fase1_cache.json", encoding="utf-8"))
    _log(f"=== INICIO FASE 1c (ROIC/FCF-NI/declive/PEG) sobre {len(candidatos)} tickers ===")
    cache = _load_cache()
    pendientes = [tk for tk in candidatos if tk not in cache]
    _log(f"Pendientes: {len(pendientes)}")
    for i, tk in enumerate(pendientes, 1):
        roe_pct = fund.get(tk, {}).get("roe")
        cache[tk] = evaluar_ticker(tk, roe_pct)
        if i % 25 == 0:
            _save_cache(cache)
            _log(f"  progreso fase1c: {i}/{len(pendientes)}")
        time.sleep(0.4)
    _save_cache(cache)
    _log(f"=== FIN FASE 1c: {len(pendientes)} procesados ===")
