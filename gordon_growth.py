# -*- coding: utf-8 -*-
"""
Modelo de Gordon (Dividend Discount Model): P = D1 / (k - g).
Solo aplicable a pagadores de dividendo maduros y estables -- ver
evaluar_elegibilidad_gordon(). No es un filtro universal: NVIDIA ni la
mayoria de la watchlist de crecimiento no reparten dividendo predecible,
y deben salir "no_elegible", nunca un numero calculado sin sentido.

Local-only, igual que deuda_ebitda.py: se ejecuta solo dentro de
generate_dashboard.py, nunca en Render (yfinance .dividends/.info/
income_stmt/balance_sheet -- esto ultimo via evaluar_wacc_eva -- estan
bloqueados o poco fiables ahi). server.py solo lee gordon_growth_cache.json
via get_gordon_cacheado(), nunca dispara red.

k = Ke (CAPM) via evaluar_wacc_eva() de escaneo_universo_fase2_wacc_score.py
    (devuelto ya en PUNTOS PORCENTUALES, ej. 7.9 = 7.9%, no 0.079).
g = CAGR del dividendo anual total sobre los ultimos 5 años NATURALES
    COMPLETOS (excluye el año en curso: un año en progreso subestima el
    total real y falsea tanto la elegibilidad como el CAGR -- verificado
    con ITX.MC: 2026 parcial 0.875€ vs 1.68€ del año completo anterior,
    pareceria un recorte del 48% sin serlo).
D1 = dividendo del ultimo año completo x (1 + g).

Elegibilidad:
  1. quoteType == "EQUITY" (excluye ETF/fondo -- barato, antes de tocar
     dividendos o WACC).
  2. >=5 años naturales completos y CONSECUTIVOS (sin huecos) de pago.
     Un año faltante dentro del rango cuenta como recorte del 100%.
  3. Ningun recorte interanual > GORDON_MAX_YOY_CUT_PCT.
Payout-ratio-volatilidad queda fuera de esta primera version (reconstruir
EPS historico de forma fiable no esta verificado como viable) -- mejora
futura documentada, no un check bloqueante.

Salvaguarda numerica: si k - g < GORDON_MIN_SPREAD_PCT, se devuelve
estado "inestable" en vez de un precio teorico sin control (por debajo de
ese margen, un error pequeño en k o g mueve el precio teorico >25%).
"""
import os
import json
import logging
from datetime import datetime, timezone

import yfinance as yf
import requests

from escaneo_universo_fase2_wacc_score import evaluar_wacc_eva

log = logging.getLogger(__name__)

_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_PROJ_DIR, "gordon_growth_cache.json")

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

GORDON_G_CAP_PCT = 8.0        # tope de g -- crecimiento sostenido de dividendo
                               # >8% anual durante decadas es raro en pagadores
                               # maduros; coherente con el ERP=5.5% (Damodaran)
                               # ya usado como constante conservadora del proyecto
GORDON_MIN_SPREAD_PCT = 2.0   # floor de (k - g) en puntos porcentuales
GORDON_MAX_YOY_CUT_PCT = 20.0 # recorte interanual max tolerado antes de
                               # descalificar (un descenso puntual moderado
                               # puede ser ruido de timing/FX, uno mayor no)
GORDON_MIN_YEARS = 5           # años naturales completos y consecutivos
GORDON_MIN_DIV_YIELD_PCT = 0.5 # yield minimo (%) para considerar el dividendo
                               # economicamente relevante -- detectado en pruebas:
                               # NVD.DE pasa 5 años sin recortes de un dividendo
                               # simbolico (yield ~0.43%, valor Gordon resultante
                               # 0.49€ vs precio real ~195€), exactamente el caso
                               # que el prompt original pedia excluir explicitamente.
                               # Umbral calibrado contra dividendYield real de
                               # info (ya viene en % en la version de yfinance de
                               # este proyecto, no como fraccion): NVD.DE 0.43%
                               # (excluido), DANR.MI 0.84%, SAF.PA 1.0%, ITX.MC
                               # 1.13%, ACS.MC 2.28% (todos incluidos).


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


def _dividendos_anuales_completos(ticker):
    """yf.Ticker(ticker).dividends agrupado por año natural, EXCLUYENDO
    el año en curso. Devuelve (dict ordenado {año:int -> total:float},
    None) o (None, motivo_str) si no hay historial usable."""
    try:
        t = yf.Ticker(ticker, session=_SESSION)
        divs = t.dividends
    except Exception as e:
        return None, f"error obteniendo dividendos ({e})"
    if divs is None or len(divs) == 0:
        return None, "sin historial de dividendos"
    anio_actual = datetime.now().year
    por_anio = {}
    for fecha, val in divs.items():
        anio = fecha.year
        if anio >= anio_actual:
            continue
        por_anio[anio] = por_anio.get(anio, 0.0) + float(val)
    if not por_anio:
        return None, "sin años naturales completos con dividendo"
    return dict(sorted(por_anio.items())), None


def evaluar_elegibilidad_gordon(ticker):
    """Devuelve {"elegible": bool, "motivo_no_elegible": str|None,
    "quote_type": str|None, "n_years_completos": int,
    "dividendos_anuales": {año:float}, "recorte_max_yoy_pct": float|None,
    "hueco_detectado": bool}."""
    out = {
        "elegible": False, "motivo_no_elegible": None, "quote_type": None,
        "n_years_completos": 0, "dividendos_anuales": {},
        "recorte_max_yoy_pct": None, "hueco_detectado": False,
    }
    try:
        info = yf.Ticker(ticker, session=_SESSION).info
        quote_type = info.get("quoteType")
        div_yield = info.get("dividendYield")
    except Exception as e:
        out["motivo_no_elegible"] = f"error obteniendo quoteType ({e})"
        return out
    out["quote_type"] = quote_type
    if quote_type != "EQUITY":
        out["motivo_no_elegible"] = (
            f"{quote_type or 'tipo desconocido'}, no es una acción individual "
            "-- el modelo de dividendo por empresa no aplica"
        )
        return out
    if div_yield is not None and div_yield < GORDON_MIN_DIV_YIELD_PCT:
        out["motivo_no_elegible"] = (
            f"Dividendo simbólico (yield {div_yield:.2f}%, mínimo "
            f"{GORDON_MIN_DIV_YIELD_PCT}%) -- no es económicamente relevante "
            "para una valoración por descuento de dividendos"
        )
        return out

    por_anio, err = _dividendos_anuales_completos(ticker)
    if err:
        out["motivo_no_elegible"] = f"Historial de dividendos insuficiente ({err})"
        return out
    out["dividendos_anuales"] = por_anio

    anios = sorted(por_anio.keys())
    if len(anios) < GORDON_MIN_YEARS:
        out["n_years_completos"] = len(anios)
        out["motivo_no_elegible"] = (
            f"Historial de dividendos insuficiente ({len(anios)} años "
            f"completos, mínimo {GORDON_MIN_YEARS})"
        )
        return out

    ultimos = anios[-GORDON_MIN_YEARS:]
    rango_completo = list(range(ultimos[0], ultimos[-1] + 1))
    hueco = rango_completo != ultimos
    out["hueco_detectado"] = hueco
    out["n_years_completos"] = len(ultimos)
    if hueco:
        out["motivo_no_elegible"] = (
            f"Hueco detectado en el historial de dividendos entre {ultimos[0]} "
            f"y {ultimos[-1]} (año(s) sin pago = recorte del 100%)"
        )
        return out

    valores = [por_anio[a] for a in ultimos]
    recorte_max = 0.0
    for i in range(1, len(valores)):
        prev, cur = valores[i - 1], valores[i]
        if prev > 0 and cur < prev:
            recorte_max = max(recorte_max, (prev - cur) / prev * 100)
    out["recorte_max_yoy_pct"] = round(recorte_max, 1)
    if recorte_max > GORDON_MAX_YOY_CUT_PCT:
        out["motivo_no_elegible"] = (
            f"Recorte interanual de dividendo del {recorte_max:.1f}% detectado "
            f"(máximo tolerado {GORDON_MAX_YOY_CUT_PCT}%)"
        )
        return out

    out["elegible"] = True
    return out


def calcular_gordon(ticker):
    """Orquesta elegibilidad -> g (CAGR, capado) -> D1 -> k
    (evaluar_wacc_eva) -> comprobación de spread -> valor. Siempre
    devuelve 'estado' explícito, nunca solo un número."""
    elig = evaluar_elegibilidad_gordon(ticker)
    out = {
        "elegible": elig["elegible"],
        "motivo_no_elegible": elig["motivo_no_elegible"],
        "quote_type": elig["quote_type"],
        "n_years_completos": elig["n_years_completos"],
        "dividendos_anuales": elig["dividendos_anuales"],
        "recorte_max_yoy_pct": elig["recorte_max_yoy_pct"],
        "hueco_detectado": elig["hueco_detectado"],
        "g_pct": None, "g_capped": False, "d1": None,
        "k_pct": None, "k_fuente": None, "spread_pct": None,
        "estado": "no_elegible", "valor_gordon": None, "motivo_estado": None,
        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
        "fuente": "yfinance.dividends + evaluar_wacc_eva (CAPM)",
    }
    if not elig["elegible"]:
        return out

    anios = sorted(elig["dividendos_anuales"].keys())
    d_inicio = elig["dividendos_anuales"][anios[0]]
    d_fin = elig["dividendos_anuales"][anios[-1]]
    n_periodos = len(anios) - 1
    if d_inicio <= 0 or n_periodos <= 0:
        out["estado"] = "error_datos"
        out["motivo_estado"] = "Dividendo inicial no positivo o periodo insuficiente para CAGR"
        return out

    g = ((d_fin / d_inicio) ** (1 / n_periodos) - 1) * 100
    g_final = min(g, GORDON_G_CAP_PCT)
    out["g_pct"] = round(g_final, 2)
    out["g_capped"] = g > GORDON_G_CAP_PCT
    out["d1"] = round(d_fin * (1 + g_final / 100), 4)

    try:
        wacc = evaluar_wacc_eva(ticker, None, None)
    except Exception as e:
        wacc = {"error": str(e)}
    if "error" in wacc:
        out["estado"] = "error_ke"
        out["motivo_estado"] = f"No se pudo calcular Ke (CAPM): {wacc['error']}"
        return out
    k = wacc["ke"]
    out["k_pct"] = k
    out["k_fuente"] = "evaluar_wacc_eva (CAPM)"

    spread = k - g_final
    out["spread_pct"] = round(spread, 2)
    if spread < GORDON_MIN_SPREAD_PCT:
        out["estado"] = "inestable"
        out["motivo_estado"] = (
            f"Spread k-g ({spread:.2f}pp) por debajo del floor ({GORDON_MIN_SPREAD_PCT}pp)"
        )
        return out

    out["valor_gordon"] = round(out["d1"] / (spread / 100), 2)
    out["estado"] = "ok"
    return out


def actualizar_cache(tickers):
    """Local-only. Recalcula Gordon para una lista de tickers, mergea en
    gordon_growth_cache.json (conserva entradas de tickers no incluidos
    en esta llamada). Un fallo individual por ticker no aborta el resto."""
    cache = _read_json(CACHE_FILE, {})
    for tk in tickers:
        try:
            cache[tk] = calcular_gordon(tk)
        except Exception as e:
            log.warning(f"Gordon Growth {tk}: fallo inesperado - {e}")
            cache[tk] = {
                "elegible": False, "estado": "error_datos",
                "motivo_estado": f"fallo inesperado: {e}", "valor_gordon": None,
                "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
            }
    _write_json(CACHE_FILE, cache)
    return cache


def get_gordon_cacheado(ticker):
    """Lectura pura, segura en Render: nunca dispara yfinance."""
    cache = _read_json(CACHE_FILE, {})
    return cache.get(ticker)
