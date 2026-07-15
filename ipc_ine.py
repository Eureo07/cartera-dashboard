import json, os, requests
from datetime import datetime, timedelta

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ipc_cache.json")
IPC_TABLE_ID = "50902"
API_URL = f"https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/{IPC_TABLE_ID}"
# Real IPC values from INE API (Base 2021). 2026 values are estimates.
_FALLBACK = {
    "2023-01": 109.668, "2023-02": 110.703, "2023-03": 111.111,
    "2023-04": 111.773, "2023-05": 111.719, "2023-06": 112.354,
    "2023-07": 112.544, "2023-08": 113.149, "2023-09": 113.348,
    "2023-10": 113.676, "2023-11": 113.280, "2023-12": 113.308,
    "2024-01": 113.404, "2024-02": 113.807, "2024-03": 114.674,
    "2024-04": 115.472, "2024-05": 115.776, "2024-06": 116.212,
    "2024-07": 115.660, "2024-08": 115.707, "2024-09": 115.009,
    "2024-10": 115.726, "2024-11": 116.010, "2024-12": 116.534,
    "2025-01": 116.733, "2025-02": 117.191, "2025-03": 117.260,
    "2025-04": 117.997, "2025-05": 118.077, "2025-06": 118.867,
    "2025-07": 118.777, "2025-08": 118.824, "2025-09": 118.485,
    "2025-10": 119.301, "2025-11": 119.532, "2025-12": 119.942,
    "2026-01": 120.200, "2026-02": 120.500, "2026-03": 120.800,
    "2026-04": 121.100, "2026-05": 121.400, "2026-06": 121.700,
    "2026-07": 122.000,
}
_ESTIMATED_INTERANUAL = 0.029

def _cargar_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _guardar_cache(data):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except:
        pass

def _fetch_api():
    try:
        r = requests.get(API_URL, timeout=15, headers={"Accept": "application/json"})
        r.raise_for_status()
        raw = r.json()
        ipc = {}
        for tabla in raw if isinstance(raw, list) else [raw]:
            if tabla.get("COD") not in ("IPC251852",):
                continue
            for d in tabla.get("Data", []):
                valor = d.get("Valor")
                if valor is None:
                    continue
                anyo = d.get("Anyo")
                mes = d.get("FK_Periodo")
                if anyo and mes is not None:
                    key = f"{int(anyo):04d}-{int(mes):02d}"
                    try:
                        ipc[key] = float(valor)
                    except:
                        continue
        if ipc:
            return ipc
    except:
        pass
    return None

def obtener_ipc_mensual(force_refresh=False):
    cache = _cargar_cache()
    if not force_refresh and cache.get("ipc_mensual"):
        return cache["ipc_mensual"]
    data = _fetch_api()
    if data:
        merged = dict(data)
        ultimo_api = max(data.keys())
        for k, v in _FALLBACK.items():
            if k not in merged:
                merged[k] = v
        cache["ipc_mensual"] = merged
        cache["ultimo_mes_api"] = ultimo_api
        cache["ultima_actualizacion"] = datetime.now().isoformat()
        _guardar_cache(cache)
        return merged
    if cache.get("ipc_mensual"):
        return cache["ipc_mensual"]
    fb = dict(_FALLBACK)
    cache["ipc_mensual"] = fb
    cache["ultima_actualizacion"] = datetime.now().isoformat()
    _guardar_cache(cache)
    return fb

def _valor_ipc(fecha, ipc_data):
    if isinstance(fecha, datetime):
        fecha = fecha.strftime("%Y-%m")
    elif len(fecha) >= 10:
        fecha = fecha[:7]
    exacto = ipc_data.get(fecha)
    if exacto is not None:
        return exacto
    claves = sorted(ipc_data.keys())
    if not claves:
        return None
    if fecha < claves[0]:
        return ipc_data[claves[0]]
    if fecha > claves[-1]:
        return ipc_data[claves[-1]]
    for i in range(len(claves) - 1):
        if claves[i] <= fecha <= claves[i + 1]:
            if claves[i] == fecha:
                return ipc_data[claves[i]]
            if claves[i + 1] == fecha:
                return ipc_data[claves[i + 1]]
            return ipc_data[claves[i]]
    return None

def inflacion_acumulada(desde, hasta=None):
    ipc_data = obtener_ipc_mensual()
    if hasta is None:
        hasta = datetime.now()
    if isinstance(desde, str):
        if len(desde) == 7:
            desde = datetime.strptime(desde, "%Y-%m")
        else:
            desde = datetime.strptime(desde[:10], "%Y-%m-%d")
    if isinstance(hasta, str):
        if len(hasta) == 7:
            hasta = datetime.strptime(hasta, "%Y-%m")
        else:
            hasta = datetime.strptime(hasta[:10], "%Y-%m-%d")
    vi = _valor_ipc(desde, ipc_data)
    vf = _valor_ipc(hasta, ipc_data)
    if vi is not None and vf is not None and vi > 0:
        return (vf - vi) / vi
    return None

def inflacion_interanual():
    ipc_data = obtener_ipc_mensual()
    meses = sorted(ipc_data.keys())
    if len(meses) < 13:
        return _ESTIMATED_INTERANUAL
    ult = meses[-1]
    ant = f"{int(ult[:4]) - 1}-{ult[5:]}"
    if ult in ipc_data and ant in ipc_data and ipc_data[ant] > 0:
        return (ipc_data[ult] - ipc_data[ant]) / ipc_data[ant]
    return _ESTIMATED_INTERANUAL

_MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

def _fmt_mes_es(key):
    m = int(key[5:7])
    return f"{_MESES_ES[m-1]} {key[:4]}"

def inflacion_interanual_rolling():
    """IPC interanual rolling 12m sobre el último mes con dato definitivo del INE.
    Returns: (tasa_float, mes_desde_str, mes_hasta_str) o (None, None, None)."""
    cache = _cargar_cache()
    ipc_data = cache.get("ipc_mensual")
    if not ipc_data:
        ipc_data = obtener_ipc_mensual()
    meses = sorted(ipc_data.keys())
    if len(meses) < 13:
        return None, None, None
    ultimo_api = cache.get("ultimo_mes_api")
    ahora = datetime.now()
    mes_actual = f"{ahora.year}-{ahora.month:02d}"
    if ultimo_api and ultimo_api in ipc_data:
        ult = ultimo_api
    else:
        ult = meses[-1]
        if ult == mes_actual and len(meses) >= 2:
            ult = meses[-2]
    ant = f"{int(ult[:4]) - 1}-{ult[5:]}"
    if ult in ipc_data and ant in ipc_data and ipc_data[ant] > 0:
        tasa = (ipc_data[ult] - ipc_data[ant]) / ipc_data[ant]
        return tasa, ant, ult
    return None, None, None
