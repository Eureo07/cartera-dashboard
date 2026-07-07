import yfinance as yf


def calcular_regimen(ticker_indice):
    try:
        hist = yf.Ticker(ticker_indice).history(period="1y")
        if hist.empty or len(hist) < 210:
            return {"precio_actual": None, "sma50": None, "sma200": None,
                    "sma200_pendiente_alcista": False, "regimen": "sin_datos"}

        precio_actual = float(hist["Close"].iloc[-1])
        sma50 = float(hist["Close"].rolling(50).mean().iloc[-1])
        sma200 = float(hist["Close"].rolling(200).mean().iloc[-1])
        sma200_hoy = sma200
        sma200_hace20 = float(hist["Close"].rolling(200).mean().iloc[-21]) if len(hist) >= 220 else sma200
        sma200_pendiente_alcista = sma200_hoy > sma200_hace20

        if precio_actual > sma50 > sma200 and sma200_pendiente_alcista:
            regimen = "favorable"
        else:
            regimen = "desfavorable"

        return {
            "precio_actual": round(precio_actual, 2),
            "sma50": round(sma50, 2),
            "sma200": round(sma200, 2),
            "sma200_pendiente_alcista": sma200_pendiente_alcista,
            "regimen": regimen,
        }
    except Exception:
        return {"precio_actual": None, "sma50": None, "sma200": None,
                "sma200_pendiente_alcista": False, "regimen": "sin_datos"}


def obtener_regimen_combinado():
    dax = calcular_regimen("^GDAXI")
    stoxx = calcular_regimen("^STOXX")

    dax_reg = dax.get("regimen", "sin_datos")
    stoxx_reg = stoxx.get("regimen", "sin_datos")

    if dax_reg == "favorable" and stoxx_reg == "favorable":
        regimen_general = "favorable"
    elif dax_reg == "sin_datos" or stoxx_reg == "sin_datos":
        if dax_reg == "favorable" or stoxx_reg == "favorable":
            regimen_general = "favorable"
        else:
            regimen_general = "sin_datos"
    elif dax_reg != stoxx_reg:
        regimen_general = "mixto"
    else:
        regimen_general = "desfavorable"

    return {
        "dax": dax,
        "stoxx": stoxx,
        "regimen_general": regimen_general,
    }
