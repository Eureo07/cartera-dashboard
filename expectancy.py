import json
from datetime import datetime


def cargar_cartera_cerrada(path="cartera_cerrada.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _calc_r_multiple(o):
    soporte = o.get("precio_soporte_entrada")
    if soporte is None or soporte <= 0:
        return None
    riesgo = o["entrada"] - soporte
    if riesgo <= 0:
        return None
    return round(abs(o["venta"] - o["entrada"]) / riesgo, 2)


def calcular_expectancy(operaciones):
    n = len(operaciones)
    ganadoras = [o for o in operaciones if o["pnl_pct"] > 0]
    perdedoras = [o for o in operaciones if o["pnl_pct"] <= 0]

    n_ganadoras = len(ganadoras)
    n_perdedoras = len(perdedoras)

    pct_acierto = round((n_ganadoras / n) * 100, 1) if n else 0.0
    pct_fallo = round((n_perdedoras / n) * 100, 1) if n else 0.0

    ganancia_media_pct = round(sum(o["pnl_pct"] for o in ganadoras) / n_ganadoras, 2) if n_ganadoras else 0.0
    perdida_media_pct = round(abs(sum(o["pnl_pct"] for o in perdedoras) / n_perdedoras), 2) if n_perdedoras else 0.0

    expectancy = round(
        (pct_acierto / 100 * ganancia_media_pct) - (pct_fallo / 100 * perdida_media_pct), 2
    )

    payoff_ratio = round(ganancia_media_pct / perdida_media_pct, 2) if perdida_media_pct else None

    # R-multiple metrics (only for positions with precio_soporte_entrada)
    ops_con_r = [o for o in operaciones if _calc_r_multiple(o) is not None]
    r_multiple_ganadoras = [
        _calc_r_multiple(o) for o in ops_con_r if o["pnl_pct"] > 0
    ]
    r_multiple_perdedoras = [
        _calc_r_multiple(o) for o in ops_con_r if o["pnl_pct"] <= 0
    ]

    r_medio_ganadoras = round(sum(r_multiple_ganadoras) / len(r_multiple_ganadoras), 2) if r_multiple_ganadoras else None
    r_medio_perdedoras = round(sum(r_multiple_perdedoras) / len(r_multiple_perdedoras), 2) if r_multiple_perdedoras else None
    r_payoff = round(r_medio_ganadoras / r_medio_perdedoras, 2) if (r_medio_ganadoras and r_medio_perdedoras) else None

    # Motivo desglose
    motivos = {}
    for o in operaciones:
        m = o.get("motivo_cierre")
        if m:
            motivos[m] = motivos.get(m, 0) + 1

    rentabilidad_anualizada = None
    dias_totales = None
    total_retorno = None
    if n > 0 and all(o.get("fecha_entrada") and o.get("fecha_cierre") for o in operaciones):
        try:
            fechas_entrada = [datetime.strptime(o["fecha_entrada"], "%Y-%m-%d") for o in operaciones]
            fechas_cierre = [datetime.strptime(o["fecha_cierre"], "%Y-%m-%d") for o in operaciones]
            dias_totales = (max(fechas_cierre) - min(fechas_entrada)).days
            if dias_totales > 0:
                total_coste = sum(o["coste"] for o in operaciones)
                total_pnl = sum(o["pnl_eur"] for o in operaciones)
                total_retorno = total_pnl / total_coste if total_coste else 0
                años = dias_totales / 365.25
                rentabilidad_anualizada = round((1 + total_retorno) ** (1 / años) - 1, 4)
        except (ValueError, KeyError):
            pass

    # R-multiple listas para la tabla
    r_list = [_calc_r_multiple(o) for o in operaciones]

    return {
        "pct_acierto": pct_acierto,
        "pct_fallo": pct_fallo,
        "ganancia_media_pct": ganancia_media_pct,
        "perdida_media_pct": perdida_media_pct,
        "expectancy": expectancy,
        "payoff_ratio": payoff_ratio,
        "rentabilidad_anualizada": rentabilidad_anualizada,
        "n_total": n,
        "n_ganadoras": n_ganadoras,
        "n_perdedoras": n_perdedoras,
        "lista_ganancias_pct": [round(o["pnl_pct"], 2) for o in ganadoras],
        "lista_perdidas_pct": [round(abs(o["pnl_pct"]), 2) for o in perdedoras],
        "dias_totales": dias_totales,
        "total_retorno": round(total_retorno, 4) if total_retorno is not None else None,
        # R-multiple
        "r_medio_ganadoras": r_medio_ganadoras,
        "r_medio_perdedoras": r_medio_perdedoras,
        "r_payoff": r_payoff,
        "n_con_r": len(ops_con_r),
        "lista_r": r_list,
        # Motivo desglose
        "motivo_desglose": motivos,
    }
