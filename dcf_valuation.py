# -*- coding: utf-8 -*-
"""
DCF (descuento de flujos de caja) + MOS (margen de seguridad). Informativo,
NUNCA bloqueante -- no forma parte de los 5 criterios ya definidos, es una
columna mas junto a PER/PEG.

Metodologia (documentada aqui porque no hay ninguna especificacion previa
en el repo -- confirmado por busqueda exhaustiva: no existia ningun motor
DCF en este dashboard antes de este fichero; la nota que mencionaba
"dcf.ts/dcf-service.ts" se referia a la convencion de nombres de OTRO
proyecto, Next.js/TS, no a este):

  Valor intrinseco = sum_{t=1..N} FCF_t / (1+WACC)^t  +  VT / (1+WACC)^N
  VT (valor terminal, perpetuidad de Gordon) = FCF_N*(1+g_terminal) / (WACC-g_terminal)
  Valor intrinseco por accion = Valor intrinseco / acciones en circulacion
  MOS = (Valor intrinseco por accion - precio actual) / Valor intrinseco por accion

Fuente de las proyecciones de FCF (no hay consenso de analistas integrado
en el proyecto, se documenta la alternativa usada):
  FCF_0 = "Free Cash Flow" del ultimo año fiscal COMPLETO en
  yfinance .cash_flow (serie anual real, NO el freeCashflow TTM de .info
  -- mezclar TTM con una serie fiscal-anual para el CAGR seria
  inconsistente, mismo cuidado que ya se tuvo con dividendos en
  gordon_growth.py).
  g_proyeccion = CAGR de FCF anual de los ultimos hasta 5 años fiscales
  completos disponibles (minimo 2 para poder calcular un CAGR), capado a
  DCF_G_CAP_PCT.
  FCF_t = FCF_0 * (1+g_proyeccion)^t para t=1..N_ANOS_PROYECCION.
  g_terminal: constante conservadora FIJA (no en vivo), coherente con el
  ERP=5.5% Damodaran ya usado en evaluar_wacc_eva() -- una tasa terminal
  no deberia superar el crecimiento nominal a muy largo plazo de la
  economia.
  WACC: evaluar_wacc_eva() ya existente (escaneo_universo_fase2_wacc_score.py),
  reutilizado sin recalcular aparte.

Local-only, igual que gordon_growth.py/indices_valoracion.py: yfinance
.cash_flow/.info y evaluar_wacc_eva() estan bloqueados/poco fiables en
Render -- este modulo se ejecuta EXCLUSIVAMENTE dentro de
generate_dashboard.py y persiste en dcf_valuation_cache.json. server.py
solo LEE ese cache.
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
CACHE_FILE = os.path.join(_PROJ_DIR, "dcf_valuation_cache.json")

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

N_ANOS_PROYECCION = 5          # mismo horizonte explicito que Gordon (gordon_growth.py)
DCF_G_CAP_PCT = 15.0           # tope de crecimiento explicito de FCF (5 años) -- mas
                               # holgado que el 8% de Gordon porque el DCF cubre TODO
                               # tipo de empresa (incluidas las de crecimiento), no solo
                               # pagadoras de dividendo maduras; 15% es un tope habitual
                               # en la practica para el periodo explicito, no perpetuo.
DCF_G_TERMINAL_PCT = 2.5       # tasa terminal fija, conservadora (proxy PIB nominal /
                               # inflacion a muy largo plazo) -- coherente con el
                               # ERP=5.5% Damodaran ya usado como constante del proyecto.
DCF_MIN_SPREAD_PCT = 1.0       # floor de (WACC - g_terminal) en puntos porcentuales --
                               # mismo motivo que el floor de Gordon: por debajo de eso
                               # el valor terminal se dispara sin control.
DCF_MIN_ANOS_FCF = 2           # minimo de años fiscales completos con FCF disponible
                               # para poder calcular un CAGR (no forzar un g inventado).


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


def _fcf_anual_historico(ticker):
    """Serie de FCF por año fiscal (mas reciente primero), desde
    yfinance .cash_flow (fila 'Free Cash Flow'). Devuelve (lista_floats,
    None) o (None, motivo_str) si no hay suficientes datos."""
    try:
        t = yf.Ticker(ticker, session=_SESSION)
        cf = t.cash_flow
    except Exception as e:
        return None, f"error obteniendo cash_flow ({e})"
    if cf is None or cf.empty or "Free Cash Flow" not in cf.index:
        return None, "sin fila 'Free Cash Flow' en el estado de flujos de caja"
    serie = cf.loc["Free Cash Flow"].dropna()
    if len(serie) < DCF_MIN_ANOS_FCF:
        return None, f"solo {len(serie)} años de FCF disponibles, mínimo {DCF_MIN_ANOS_FCF}"
    return [float(x) for x in serie.tolist()], None


def calcular_dcf(ticker):
    """Orquesta FCF historico -> g proyeccion (CAGR, capado) -> flujos
    proyectados -> WACC (evaluar_wacc_eva) -> valor terminal -> valor
    intrinseco -> MOS. Siempre devuelve 'estado' explicito, nunca solo un
    numero. estado en {"ok","inestable","sin_datos","error_wacc","error_precio"}."""
    out = {
        "fcf_anual_historico": None, "n_anos_fcf": 0, "g_proyeccion_pct": None,
        "g_capped": False, "wacc_pct": None, "g_terminal_pct": DCF_G_TERMINAL_PCT,
        "spread_pct": None, "valor_intrinseco_total": None, "shares_outstanding": None,
        "valor_intrinseco_por_accion": None, "precio_actual": None, "mos_pct": None,
        "estado": "sin_datos", "motivo_estado": None,
        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
        "fuente": "yfinance.cash_flow (FCF anual real) + evaluar_wacc_eva (CAPM)",
    }
    fcf_hist, err = _fcf_anual_historico(ticker)
    if err:
        out["motivo_estado"] = err
        return out
    out["fcf_anual_historico"] = fcf_hist
    out["n_anos_fcf"] = len(fcf_hist)

    fcf_0 = fcf_hist[0]
    fcf_mas_antiguo = fcf_hist[min(N_ANOS_PROYECCION, len(fcf_hist) - 1)]
    n_periodos = min(N_ANOS_PROYECCION, len(fcf_hist) - 1)
    if fcf_0 <= 0 or fcf_mas_antiguo <= 0 or n_periodos <= 0:
        out["motivo_estado"] = "FCF inicial o de referencia no positivo, no se puede proyectar un CAGR fiable"
        return out
    g = ((fcf_0 / fcf_mas_antiguo) ** (1 / n_periodos) - 1) * 100
    g_final = min(g, DCF_G_CAP_PCT)
    out["g_proyeccion_pct"] = round(g_final, 2)
    out["g_capped"] = g > DCF_G_CAP_PCT

    try:
        wacc = evaluar_wacc_eva(ticker, None, None)
    except Exception as e:
        wacc = {"error": str(e)}
    if "error" in wacc:
        out["estado"] = "error_wacc"
        out["motivo_estado"] = f"No se pudo calcular WACC: {wacc['error']}"
        return out
    w = wacc["wacc"]
    out["wacc_pct"] = w

    spread = w - DCF_G_TERMINAL_PCT
    out["spread_pct"] = round(spread, 2)
    if spread < DCF_MIN_SPREAD_PCT:
        out["estado"] = "inestable"
        out["motivo_estado"] = f"Spread WACC-g_terminal ({spread:.2f}pp) por debajo del floor ({DCF_MIN_SPREAD_PCT}pp)"
        return out

    wacc_frac = w / 100
    g_frac = g_final / 100
    valor_presente_flujos = 0.0
    fcf_t = fcf_0
    for t in range(1, N_ANOS_PROYECCION + 1):
        fcf_t = fcf_t * (1 + g_frac)
        valor_presente_flujos += fcf_t / ((1 + wacc_frac) ** t)
    valor_terminal = fcf_t * (1 + DCF_G_TERMINAL_PCT / 100) / (wacc_frac - DCF_G_TERMINAL_PCT / 100)
    valor_terminal_presente = valor_terminal / ((1 + wacc_frac) ** N_ANOS_PROYECCION)
    valor_intrinseco_total = valor_presente_flujos + valor_terminal_presente
    out["valor_intrinseco_total"] = round(valor_intrinseco_total, 2)

    try:
        info = yf.Ticker(ticker, session=_SESSION).info
        shares = info.get("sharesOutstanding")
        precio_actual = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        moneda_precio = info.get("currency")
        moneda_financiera = info.get("financialCurrency")
    except Exception as e:
        out["estado"] = "error_precio"
        out["motivo_estado"] = f"No se pudo obtener precio/acciones en circulación: {e}"
        return out
    if not shares or not precio_actual:
        out["estado"] = "error_precio"
        out["motivo_estado"] = "Precio actual o acciones en circulación no disponibles"
        return out
    # El valor intrinseco sale en la moneda de los estados financieros
    # (financialCurrency), pero el precio cotiza en "currency" -- para
    # muchos cross-listings (ej. NVD.DE cotiza en EUR, reporta en USD;
    # ITH.L/EDV.L cotizan en GBp, reportan en USD) son distintas, y
    # comparar sin convertir da un MOS absurdo (bug real detectado en
    # pruebas: NVD.DE daba -277%, ITH.L -2944%). Solo se resuelve el caso
    # determinista GBp->GBP (division por 100, sin FX); el resto de
    # descoincidencias de moneda quedan documentadas como no aplicable en
    # vez de forzar una conversion FX en vivo no verificada.
    if moneda_precio == "GBp" and moneda_financiera == "GBP":
        precio_actual = precio_actual / 100
    elif moneda_precio != moneda_financiera:
        out["estado"] = "no_aplicable"
        out["motivo_estado"] = f"Moneda de cotización ({moneda_precio}) distinta de la moneda de los estados financieros ({moneda_financiera}) -- requeriría conversión FX no implementada, MOS no fiable sin ella"
        return out
    out["shares_outstanding"] = shares
    out["precio_actual"] = round(float(precio_actual), 4)

    valor_por_accion = valor_intrinseco_total / shares
    out["valor_intrinseco_por_accion"] = round(valor_por_accion, 4)
    out["mos_pct"] = round((valor_por_accion - precio_actual) / valor_por_accion * 100, 2)
    out["estado"] = "ok"
    return out


def actualizar_cache(tickers):
    """Local-only. Recalcula DCF/MOS para una lista de tickers, mergea en
    dcf_valuation_cache.json (conserva entradas de tickers no incluidos
    en esta llamada). Un fallo individual por ticker no aborta el resto."""
    cache = _read_json(CACHE_FILE, {})
    for tk in tickers:
        try:
            cache[tk] = calcular_dcf(tk)
        except Exception as e:
            log.warning(f"DCF {tk}: fallo inesperado - {e}")
            cache[tk] = {
                "estado": "sin_datos", "motivo_estado": f"fallo inesperado: {e}",
                "valor_intrinseco_por_accion": None, "mos_pct": None,
                "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
            }
    _write_json(CACHE_FILE, cache)
    return cache


def get_dcf_cacheado(ticker):
    """Lectura pura, segura en Render: nunca dispara yfinance."""
    cache = _read_json(CACHE_FILE, {})
    return cache.get(ticker)
