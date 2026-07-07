CAPITAL_SISTEMA = 15000
RIESGO_POR_OPERACION_PCT = 2


def calcular_tamano_posicion(precio_entrada, precio_stop_loss,
                             capital_sistema=CAPITAL_SISTEMA,
                             riesgo_pct=RIESGO_POR_OPERACION_PCT):
    if not precio_entrada or not precio_stop_loss or precio_entrada <= 0:
        return {
            "riesgo_eur": None,
            "distancia_stop_pct": None,
            "inversion_maxima_eur": None,
            "num_acciones_max": None,
        }

    riesgo_eur = capital_sistema * (riesgo_pct / 100)

    distancia_stop_pct = ((precio_entrada - precio_stop_loss) / precio_entrada) * 100

    if distancia_stop_pct <= 0:
        return {
            "riesgo_eur": round(riesgo_eur, 2),
            "distancia_stop_pct": round(distancia_stop_pct, 2),
            "inversion_maxima_eur": None,
            "num_acciones_max": None,
        }

    inversion_maxima_eur = riesgo_eur / (distancia_stop_pct / 100)
    num_acciones_max = int(inversion_maxima_eur // precio_entrada)

    return {
        "riesgo_eur": round(riesgo_eur, 2),
        "distancia_stop_pct": round(distancia_stop_pct, 2),
        "inversion_maxima_eur": round(inversion_maxima_eur, 2),
        "num_acciones_max": num_acciones_max,
    }
