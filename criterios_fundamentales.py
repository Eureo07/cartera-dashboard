# -*- coding: utf-8 -*-
"""
Fuente unica de verdad para los 5 criterios bloqueantes del escaneo de
universo (deuda neta/EBITDA, ROIC vs ROE, FCF/Beneficio Neto, declive de
ingresos, score fundamental) + el nuevo criterio de distorsion de capital.

Por que existe este fichero: cada uno de estos calculos estaba duplicado
en al menos dos sitios con formulas/umbrales que podian desincronizarse
sin que nadie lo notara -- exactamente lo que paso con el score (server.py
usaba ROE50%/EVA25%/FCF25% en $ crudos para el email/watchlist, mientras
escaneo_universo_fase3_tecnico_watchlist.py ya habia sido corregido a
ROE25%/EVA%mcap25%/FCF%mcap50% para el filtro de entrada, sin que el
primero se actualizara). A partir de ahora TODO el sistema
(/api/candidatos, el texto del email via el mismo endpoint, el escaneo de
universo, la watchlist) importa las funciones de aqui -- si algun dia se
cambia un peso o un umbral, se cambia en un unico sitio.

Fuentes de datos, mismo patron ya establecido en el proyecto (Render
bloquea/no fiable yfinance .info): para tickers curados a mano
(ITX.MC, GOOGL, etc.) los fundamentales vienen del CSV de Eurekers
(screener.obtener_fundamentales, sin red, seguro en Render). Los tickers
del escaneo automatico (L1G.AX, EDV.L, RRL.AX, APA, ITH.L, KIE.L...) NO
estan en ese CSV (es un export centrado en España) -- obtener_fundamentales()
devuelve None para todos ellos, lo que significaba que roe/eva/fcf/roic
eran siempre None en /api/candidatos para estos 6 tickers, y por tanto
ningun aviso (ROIC vs ROE, score) se disparaba nunca para ellos en vivo,
aunque si se hubiera calculado correctamente en el escaneo offline
original. Fix: cache local-only (fundamentales_watchlist_cache.json,
mismo patron que deuda_ebitda.py/gordon_growth.py) que recalcula estos
mismos datos via yfinance -- solo se ejecuta en generate_dashboard.py,
server.py lo lee en modo solo lectura.
"""
import os
import json
import logging
from datetime import datetime, timezone

import yfinance as yf
import pandas as pd
import requests

from screener import obtener_fundamentales as _obtener_fundamentales_eurekers, normalized_score
from escaneo_universo_fase2_wacc_score import evaluar_wacc_eva

log = logging.getLogger(__name__)

_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
FUNDAMENTALES_CACHE_FILE = os.path.join(_PROJ_DIR, "fundamentales_watchlist_cache.json")
SCORE_REFERENCIA_FILE = os.path.join(_PROJ_DIR, "score_referencia_cohorte.json")
PASAN_4_CRITERIOS_FILE = os.path.join(_PROJ_DIR, "universo_global_pasan_4_criterios.json")
SCORES_FINALES_FILE = os.path.join(_PROJ_DIR, "universo_global_scores_finales.json")
WACC_CACHE_FILE = os.path.join(_PROJ_DIR, "universo_global_fase2_wacc_cache.json")
FASE1_CACHE_FILE = os.path.join(_PROJ_DIR, "universo_global_fase1_cache.json")
FASE1C_CACHE_FILE = os.path.join(_PROJ_DIR, "universo_global_fase1c_cache.json")

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# ---------- Criterio 1: Deuda Neta/EBITDA ----------
# Especificacion original: <2x sano, hasta <3.5x SOLO si el sector es
# intensivo en capital. Un dato ausente EXCLUYE, nunca cuenta como
# aprobado. Mismo vocabulario GICS/yfinance que ya fluye por el sistema
# en info.get("sector").
SECTORES_INTENSIVOS_CAPITAL = {"Energy", "Basic Materials", "Utilities"}
DEUDA_UMBRAL_NORMAL = 2.0
DEUDA_UMBRAL_CAPITAL_INTENSIVO = 3.5

# ---------- Criterio 2: ROIC vs ROE ----------
ROIC_ROE_RATIO_MINIMO = 0.5

# ---------- Criterio 3: FCF / Beneficio Neto ----------
FCF_NI_RATIO_MINIMO = 0.5

# ---------- Criterio 5: Score fundamental ----------
# ROE 25% + EVA%mcap 25% + FCF%mcap 50% -- EVA/FCF como % de
# capitalizacion (no en dolares brutos) para evitar que una mega-cap
# domine el ranking solo por tamaño (regla ya establecida en el proyecto).
SCORE_PESO_ROE = 0.25
SCORE_PESO_EVA = 0.25
SCORE_PESO_FCF = 0.50
SCORE_PERCENTIL_UMBRAL = 0.90

# ---------- Criterio nuevo: distorsion de base de capital ----------
# ROE o ROIC > 60% -- umbral confirmado contra la distribucion real de los
# 449 tickers que pasaron los criterios 1-4 (universo_global_pasan_4_criterios.json):
# p97 ROE=63.4%, p95-p97 ROIC=63-84%. 60% cae justo en esa cola (31/449 =
# ~6.9% del universo la supera), y los nombres que la superan son casos
# reales conocidos de ratio inflado por recompras/base de capital reducida
# (AAPL 148.75% ROE, RMV.L 282% ROE, L1G.AX 102.73% ROE/85.31% ROIC) --
# no excluye negocios genuinamente buenos a un nivel normal.
DISTORSION_CAPITAL_UMBRAL_PCT = 60.0


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
    except Exception as e:
        log.warning(f"No se pudo escribir {path}: {e}")


# ============================================================
# Criterios 1-4: evaluacion pura (mismos umbrales para todo el sistema)
# ============================================================

def evaluar_criterio1_deuda(deuda_neta_ebitda, sector):
    """(ok: bool, umbral_aplicado: float). Dato ausente -> ok=False."""
    if deuda_neta_ebitda is None:
        return False, None
    umbral = DEUDA_UMBRAL_CAPITAL_INTENSIVO if sector in SECTORES_INTENSIVOS_CAPITAL else DEUDA_UMBRAL_NORMAL
    return deuda_neta_ebitda <= umbral, umbral


def evaluar_criterio2_roic_roe(roic, roe):
    """(ok: bool|None, ratio: float|None). None si faltan datos."""
    if roic is None or roe is None or roe <= 0:
        return None, None
    ratio = roic / roe
    return ratio >= ROIC_ROE_RATIO_MINIMO, round(ratio, 4)


def evaluar_criterio3_fcf_ni(fcf, net_income):
    """(ok: bool|None, ratio: float|None). None si faltan datos."""
    if fcf is None or net_income in (None, 0):
        return None, None
    ratio = fcf / net_income
    return ratio >= FCF_NI_RATIO_MINIMO, round(ratio, 4)


def evaluar_criterio4_declive(revenue_hist):
    """revenue_hist: lista de ingresos, mas reciente primero (mismo orden
    que income_stmt.columns de yfinance). declive sostenido = el ultimo
    periodo es menor que los DOS anteriores. Devuelve bool|None."""
    if not revenue_hist or len(revenue_hist) < 3:
        return None
    return revenue_hist[0] < revenue_hist[1] and revenue_hist[0] < revenue_hist[2]


def evaluar_distorsion_capital(roe, roic):
    """Criterio nuevo (no bloqueante, warning): ROE o ROIC > 60% sugiere
    ratio inflado por una base de capital reducida (recompras masivas o
    perdidas historicas graves), no calidad real de negocio -- mismo
    patron detectado en Rolls-Royce y ahora en L1G.AX. Devuelve
    (distorsionado: bool, motivo: str|None)."""
    valores_altos = []
    if roe is not None and roe > DISTORSION_CAPITAL_UMBRAL_PCT:
        valores_altos.append(f"ROE={roe:.2f}%")
    if roic is not None and roic > DISTORSION_CAPITAL_UMBRAL_PCT:
        valores_altos.append(f"ROIC={roic:.2f}%")
    if not valores_altos:
        return False, None
    motivo = (
        f"Posible distorsión de base de capital ({', '.join(valores_altos)} > "
        f"{DISTORSION_CAPITAL_UMBRAL_PCT:.0f}%) — verificar histórico de "
        f"pérdidas/patrimonio neto antes de confiar en este ratio"
    )
    return True, motivo


# ============================================================
# Criterio 5: score fundamental normalizado (una sola formula)
# ============================================================

def construir_referencia_cohorte(cohorte_tickers=None, fund_cache=None, criterios_cache=None, wacc_cache=None):
    """Calcula (o reutiliza) los min/max de roe/eva_pct_mc/fcf_pct_mc y el
    percentil 90 sobre la cohorte de referencia, y los persiste en
    SCORE_REFERENCIA_FILE para que cualquier caller (server.py incluido)
    pueda normalizar el score de UN ticker sin tener que recalcular toda
    la cohorte en cada request.

    Prioridad de fuente (igual que antes en
    escaneo_universo_fase3_tecnico_watchlist.calcular_scores_y_umbral,
    migrado aqui): universo_global_scores_finales.json si existe (cohorte
    real ya usada, con exclusiones -ej. aseguradoras- aplicadas en su
    momento fuera de codigo committeado -- no se reproducen a ciegas aqui
    para no introducir una inconsistencia nueva); si no existe, recalcula
    desde fund_cache/criterios_cache/wacc_cache sobre cohorte_tickers."""
    if os.path.exists(SCORES_FINALES_FILE):
        try:
            data = _read_json(SCORES_FINALES_FILE, [])
            roes = [d["roe"] for d in data if d.get("roe") is not None]
            evas = [d["eva_pct_mc"] for d in data if d.get("eva_pct_mc") is not None]
            fcfs = [d["fcf_pct_mc"] for d in data if d.get("fcf_pct_mc") is not None]
            scores = [d["score"] for d in data if d.get("score") is not None]
            if roes and evas and fcfs and scores:
                referencia = {
                    "roe_min": min(roes), "roe_max": max(roes),
                    "eva_pct_mc_min": min(evas), "eva_pct_mc_max": max(evas),
                    "fcf_pct_mc_min": min(fcfs), "fcf_pct_mc_max": max(fcfs),
                    "percentil90": float(pd.Series(scores).quantile(SCORE_PERCENTIL_UMBRAL)),
                    "n_cohorte": len(scores),
                    "fuente": os.path.basename(SCORES_FINALES_FILE),
                    "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
                }
                _write_json(SCORE_REFERENCIA_FILE, referencia)
                return referencia
        except Exception as e:
            log.warning(f"No se pudo construir referencia desde {SCORES_FINALES_FILE} ({e}), recalculando desde cero")

    if cohorte_tickers is None:
        cohorte_tickers = [d["ticker"] if isinstance(d, dict) else d for d in _read_json(PASAN_4_CRITERIOS_FILE, [])]
    fund_cache = fund_cache if fund_cache is not None else _read_json(FASE1_CACHE_FILE, {})
    criterios_cache = criterios_cache if criterios_cache is not None else _read_json(FASE1C_CACHE_FILE, {})
    wacc_cache = wacc_cache if wacc_cache is not None else _read_json(WACC_CACHE_FILE, {})
    rows = []
    for tk in cohorte_tickers:
        roe = fund_cache.get(tk, {}).get("roe")
        fcf = criterios_cache.get(tk, {}).get("fcf")
        w = wacc_cache.get(tk, {})
        eva = w.get("eva")
        mcap = w.get("market_cap")
        if roe is not None and eva is not None and fcf is not None and mcap:
            rows.append({"roe": roe, "eva_pct_mc": eva / mcap * 100, "fcf_pct_mc": fcf / mcap * 100})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    n_roe = normalized_score(df["roe"])
    n_eva = normalized_score(df["eva_pct_mc"])
    n_fcf = normalized_score(df["fcf_pct_mc"])
    scores = n_roe * SCORE_PESO_ROE + n_eva * SCORE_PESO_EVA + n_fcf * SCORE_PESO_FCF
    referencia = {
        "roe_min": float(df["roe"].min()), "roe_max": float(df["roe"].max()),
        "eva_pct_mc_min": float(df["eva_pct_mc"].min()), "eva_pct_mc_max": float(df["eva_pct_mc"].max()),
        "fcf_pct_mc_min": float(df["fcf_pct_mc"].min()), "fcf_pct_mc_max": float(df["fcf_pct_mc"].max()),
        "percentil90": float(scores.quantile(SCORE_PERCENTIL_UMBRAL)),
        "n_cohorte": len(rows),
        "fuente": "recalculado (universo_global_scores_finales.json no disponible)",
        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(SCORE_REFERENCIA_FILE, referencia)
    return referencia


def _normalizar(valor, minimo, maximo):
    if maximo == minimo:
        return 0.5
    return (valor - minimo) / (maximo - minimo)


def calcular_score_fundamental(roe, eva, fcf, market_cap, referencia=None):
    """Score = ROE*0.25 + EVA%mcap*0.25 + FCF%mcap*0.50, normalizado
    min-max contra la cohorte de referencia (nunca contra el propio
    ticker aislado -- normalizar un solo valor no tiene sentido relativo).
    Devuelve (score: float|None, umbral: float|None, supera_umbral: bool|None).
    None en cualquiera de los tres si falta un dato o no hay referencia."""
    if referencia is None:
        referencia = _read_json(SCORE_REFERENCIA_FILE, None) or construir_referencia_cohorte()
    if referencia is None or roe is None or eva is None or fcf is None or not market_cap:
        return None, (referencia or {}).get("percentil90"), None
    eva_pct_mc = eva / market_cap * 100
    fcf_pct_mc = fcf / market_cap * 100
    n_roe = _normalizar(roe, referencia["roe_min"], referencia["roe_max"])
    n_eva = _normalizar(eva_pct_mc, referencia["eva_pct_mc_min"], referencia["eva_pct_mc_max"])
    n_fcf = _normalizar(fcf_pct_mc, referencia["fcf_pct_mc_min"], referencia["fcf_pct_mc_max"])
    score = n_roe * SCORE_PESO_ROE + n_eva * SCORE_PESO_EVA + n_fcf * SCORE_PESO_FCF
    umbral = referencia["percentil90"]
    return round(score, 4), round(umbral, 4), score >= umbral


# ============================================================
# Fuente unificada de fundamentales (Eurekers -> cache local yfinance)
# ============================================================

def _calcular_fundamentales_yfinance(tk):
    """Mismo metodo que escaneo_universo_global.py (roe/fcf/deuda) +
    escaneo_universo_fase1c_criterios.py (roic/fcf_sobre_ni/declive) +
    evaluar_wacc_eva (eva/market_cap), consolidado en una sola llamada.
    Local-only: solo se invoca desde generate_dashboard.py."""
    try:
        t = yf.Ticker(tk, session=_SESSION)
        info = t.info
        roe = round(info.get("returnOnEquity") * 100, 2) if info.get("returnOnEquity") is not None else None
        fcf = info.get("freeCashflow")
        net_income = info.get("netIncomeToCommon")
        debt_info = info.get("totalDebt")
        cash_info = info.get("totalCash")
        ebitda = info.get("ebitda")
        deuda_neta_ebitda = round((debt_info - cash_info) / ebitda, 2) if (debt_info is not None and cash_info is not None and ebitda) else None
        sector = info.get("sector")

        inc = t.income_stmt
        ebit = tax_rate = None
        revenue_hist = None
        if inc is not None and not inc.empty:
            if "EBIT" in inc.index:
                s = inc.loc["EBIT"].dropna()
                ebit = float(s.iloc[0]) if not s.empty else None
            if "Tax Rate For Calcs" in inc.index:
                s = inc.loc["Tax Rate For Calcs"].dropna()
                tax_rate = float(s.iloc[0]) if not s.empty else None
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

        wacc = evaluar_wacc_eva(tk, roe, fcf)
        eva = wacc.get("eva") if "error" not in wacc else None
        market_cap = wacc.get("market_cap") if "error" not in wacc else info.get("marketCap")

        return {
            "name": info.get("shortName") or tk, "sector": sector,
            "roe": roe, "fcf": fcf, "net_income": net_income, "roic": roic,
            "eva": eva, "market_cap": market_cap,
            "deuda_neta_ebitda": deuda_neta_ebitda, "revenue_hist": revenue_hist,
            "fuente": "yfinance_cache_local",
            "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "fecha_actualizacion": datetime.now(timezone.utc).isoformat()}


def actualizar_cache_fundamentales(tickers):
    """Local-only (llamar solo desde generate_dashboard.py). Recalcula via
    yfinance UNICAMENTE los tickers que no tengan ya datos utilizables en
    el CSV de Eurekers (obtener_fundamentales) -- ese sigue siendo la
    fuente primaria para los tickers curados a mano, esto solo tapa el
    hueco real de los tickers del escaneo automatico que el CSV nunca
    cubrio (verificado: los 6 candidatos de la ultima tanda devuelven
    None). Mergea en el cache existente, no lo pisa entero."""
    cache = _read_json(FUNDAMENTALES_CACHE_FILE, {})
    for tk in tickers:
        eurekers = _obtener_fundamentales_eurekers(tk)
        if eurekers is not None:
            continue  # Eurekers cubre este ticker, no hace falta cache propio
        cache[tk] = _calcular_fundamentales_yfinance(tk)
    _write_json(FUNDAMENTALES_CACHE_FILE, cache)
    return cache


def obtener_fundamentales_unificado(tk):
    """Punto de entrada UNICO para fundamentales, usalo en vez de llamar a
    screener.obtener_fundamentales() directamente. Devuelve
    {"name","sector","roe","eva","fcf","roic","net_income","market_cap",
    "revenue_hist","fuente"} o dict con roe/eva/fcf/roic=None si no hay
    dato en ningun sitio (Eurekers ni cache local) -- nunca lanza excepcion.
    Segura de llamar desde server.py (Render): la rama yfinance ya la
    calculo generate_dashboard.py en local, aqui solo se lee el cache."""
    eurekers = _obtener_fundamentales_eurekers(tk)
    if eurekers is not None:
        return {
            "name": eurekers.get("name", tk), "sector": eurekers.get("sector"),
            "roe": eurekers.get("roe"), "eva": eurekers.get("eva"), "fcf": eurekers.get("fcf"),
            "roic": eurekers.get("roi"), "net_income": None, "market_cap": None,
            "revenue_hist": None, "fuente": "eurekers",
        }
    cache = _read_json(FUNDAMENTALES_CACHE_FILE, {})
    entry = cache.get(tk)
    if entry is None or "error" in entry:
        return {"name": tk, "sector": None, "roe": None, "eva": None, "fcf": None,
                "roic": None, "net_income": None, "market_cap": None, "revenue_hist": None,
                "fuente": "sin_dato"}
    return {**entry, "fuente": entry.get("fuente", "yfinance_cache_local")}
