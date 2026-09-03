# -*- coding: utf-8 -*-
"""Fase 3: senal tecnica real (RR/RRA/LT/LTA/PER) sobre los candidatos que
pasen Fase 1 (+ Fase 2 cuando exista) del escaneo de universo global.

Reutiliza sin modificar las funciones protegidas de screener.py:
get_entry_types(), calcular_soporte_resistencia() -- y la ya construida
calcular_trendline_lta() (no protegida, pero misma logica que usa ITX/RRU.DE).

Reglas:
- Con senal tecnica activa (RR/RRA/LT/LTA/PER en get_entry_types) Y soporte
  valido (calcular_soporte_resistencia) -> se anade automaticamente a
  watchlist.json, con entry_signal, support, stop (=support si no hay otro
  dato), y "origen": "escaneo_automatico" + fecha, para trazabilidad. Si la
  senal es LT/LTA, el entry_level que se guarda es solo informativo (mismo
  patron que ITX/RRU.DE) -- el sistema ya resuelve el nivel operativo en
  vivo via calcular_trendline_lta() en server.py (_resolver_nivel_senal).
- Sin senal tecnica activa -> se guarda en
  candidatos_fundamentales_sin_senal.json (no entra en watchlist.json),
  para revision futura o a la espera de que el propio sistema detecte la
  senal mas adelante.
- No anade duplicados: si el ticker ya esta en watchlist.json (cualquier
  entry_signal), no se vuelve a anadir.

Uso: python escaneo_universo_fase3_tecnico_watchlist.py <candidatos.json>
donde candidatos.json es una lista de tickers que ya pasaron Fase 1(+2).
"""
import os, sys, json, time
from datetime import datetime

_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)

from screener import get_entry_types, calcular_soporte_resistencia, calcular_trendline_lta

WATCHLIST_FILE = os.path.join(_PROJ_DIR, "watchlist.json")
SIN_SENAL_FILE = os.path.join(_PROJ_DIR, "candidatos_fundamentales_sin_senal.json")
LOG_FILE = os.path.join(_PROJ_DIR, "universo_global_scan.log")


def _log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def evaluar_senal(tk):
    """Devuelve (entry_signal, entry_level, support, valido) o
    (None, None, None, False) si no hay senal tecnica activa ahora mismo.

    entry_level != support siempre: para RR/RRA el nivel de entrada es la
    RESISTENCIA (el maximo que se rompe, igual que ITX.MC/ACS.MC en la
    watchlist real), no el soporte -- usar el soporte como entry_level en
    una ruptura daria distancia-a-stop 0% (bug real, corregido). Para
    LT/LTA/PER el entry_level informativo si es el soporte/nivel de
    retroceso (mismo patron ya usado para RRU.DE/ITX)."""
    tipos = get_entry_types(tk)
    activos = [t for t in tipos if t in ("RR", "RRA", "LT", "LTA", "PER")]
    if not activos:
        return None, None, None, False
    support, resistance, current_price, support_ok = calcular_soporte_resistencia(tk)
    if not support_ok:
        return None, None, None, False
    # Prioridad: LT/LTA primero (nivel dinamico via trendline), luego RR/RRA, luego PER
    for prioridad in ("LTA", "LT", "RR", "RRA", "PER"):
        if prioridad in activos:
            if prioridad in ("RR", "RRA"):
                if resistance is None:
                    continue  # sin resistencia valida, no se puede fijar entry_level correcto
                return prioridad, resistance, support, True
            return prioridad, support, support, True
    return None, None, None, False


def procesar(candidatos, fund_cache, criterios_cache):
    watchlist = _load_json(WATCHLIST_FILE, [])
    ya_en_watchlist = {str(i.get("ticker")) for i in watchlist}
    sin_senal = _load_json(SIN_SENAL_FILE, [])
    ya_sin_senal = {c["ticker"] for c in sin_senal}

    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    anadidos = []
    for i, tk in enumerate(candidatos, 1):
        if tk in ya_en_watchlist:
            _log(f"  {tk}: ya esta en watchlist.json, se omite")
            continue
        try:
            entry_signal, entry_level, support, valido = evaluar_senal(tk)
        except Exception as e:
            _log(f"  {tk}: error evaluando senal - {e}")
            continue
        f = fund_cache.get(tk, {})
        c = criterios_cache.get(tk, {})
        if valido:
            nueva_entrada = {
                "ticker": tk,
                "name": f.get("nombre_empresa") or tk,
                "entry_level": round(entry_level, 4) if entry_level else None,
                "entry_signal": entry_signal,
                "support": round(support, 4) if support else None,
                "stop": round(support, 4) if support else None,
                "theme": f.get("sector") or "",
                "notes": f"Candidato del escaneo global (indice origen: {f.get('indice_origen', 'N/D')}). ROE={f.get('roe')}%, ROIC={c.get('roic')}%, FCF/BeneficioNeto={c.get('fcf_sobre_ni')}, PEG={c.get('peg')}.",
                "origen": "escaneo_automatico",
                "fecha_deteccion": fecha_hoy,
                "requiere_cierre_semanal_manual": entry_signal in ("LT", "LTA"),
            }
            watchlist.append(nueva_entrada)
            ya_en_watchlist.add(tk)
            anadidos.append(tk)
            _log(f"  {tk}: SENAL {entry_signal} detectada, soporte={support:.2f} -> anadido a watchlist.json")
        else:
            if tk not in ya_sin_senal:
                sin_senal.append({
                    "ticker": tk, "nombre": f.get("nombre_empresa"), "sector": f.get("sector"),
                    "indice_origen": f.get("indice_origen"), "roe": f.get("roe"),
                    "roic": c.get("roic"), "fcf_sobre_ni": c.get("fcf_sobre_ni"),
                    "peg": c.get("peg"), "fecha_evaluacion": fecha_hoy,
                })
                ya_sin_senal.add(tk)
        if i % 25 == 0:
            _save_json(WATCHLIST_FILE, watchlist)
            _save_json(SIN_SENAL_FILE, sin_senal)
            _log(f"  progreso fase3: {i}/{len(candidatos)}, {len(anadidos)} anadidos a watchlist hasta ahora")
        time.sleep(0.5)

    _save_json(WATCHLIST_FILE, watchlist)
    _save_json(SIN_SENAL_FILE, sin_senal)
    _log(f"=== FIN FASE 3: {len(candidatos)} evaluados, {len(anadidos)} anadidos a watchlist.json, {len(sin_senal)} en seguimiento sin senal ===")
    return anadidos


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python escaneo_universo_fase3_tecnico_watchlist.py <lista_candidatos.json>")
        sys.exit(1)
    candidatos = json.load(open(sys.argv[1], encoding="utf-8"))
    fund_cache = _load_json("universo_global_fase1_cache.json", {})
    criterios_cache = _load_json("universo_global_fase1c_cache.json", {})
    _log(f"=== INICIO FASE 3 sobre {len(candidatos)} candidatos ===")
    procesar(candidatos, fund_cache, criterios_cache)
