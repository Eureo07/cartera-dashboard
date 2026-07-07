import json
from datetime import datetime


def cargar_cartera_cerrada(path="cartera_cerrada.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


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
    }
