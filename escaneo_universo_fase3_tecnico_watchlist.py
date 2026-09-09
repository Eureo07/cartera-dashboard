# -*- coding: utf-8 -*-
"""Fase 3: senal tecnica real (RR/RRA/LT/LTA/PER) sobre los candidatos que
pasen Fase 1 (+ Fase 2 cuando exista) del escaneo de universo global.

Reutiliza sin modificar las funciones protegidas de screener.py:
get_entry_types(), calcular_soporte_resistencia() -- y la ya construida
calcular_trendline_lta() (no protegida, pero misma logica que usa ITX/RRU.DE).

Reglas:
- Criterios 1-5 y el aviso de distorsion de capital viven en
  criterios_fundamentales.py (fuente unica compartida con server.py) --
  este script NUNCA calcula sus propias formulas/umbrales, solo importa
  y aplica. Motivo: el score llego a tener dos formulas distintas (esta
  fase corregida a ROE25%/EVA%mc25%/FCF%mc50%, pero server.py y por tanto
  el email de n8n seguian mostrando ROE50%/EVA25%/FCF25% sin que nadie lo
  notara hasta que llego un email con el numero equivocado). No se repite.
- Criterio 5 (score fundamental, BLOQUEANTE) se aplica ANTES de la senal
  tecnica: un candidato por debajo del percentil 90 de la cohorte de
  referencia se descarta directamente, sin gastar tiempo evaluando senal.
- Techo de distorsion de capital (ROE o ROIC > 60%, ver
  criterios_fundamentales.DISTORSION_CAPITAL_UMBRAL_PCT): NO bloqueante,
  solo aviso -- se registra en candidatos_distorsion_capital.json y se
  anade visiblemente a las "notes" de la entrada en watchlist.json si el
  candidato entra igualmente. La decision de excluir un ticker concreto
  por esto es manual (mismo criterio ya aplicado a KGF.L/APTV con EVA
  negativo: se avisa, no se descarta en automatico).
- Con senal tecnica activa (RR/RRA/LT/LTA/PER en get_entry_types) Y soporte
  valido (calcular_soporte_resistencia) -> se anade automaticamente a
  watchlist.json, con entry_signal, support, stop (=support si no hay otro
  dato), y "origen": "escaneo_automatico" + fecha, para trazabilidad. Si la
  senal es LT/LTA, el entry_level que se guarda es solo informativo (mismo
  patron que ITX/RRU.DE) -- el sistema ya resuelve el nivel operativo en
  vivo via calcular_trendline_lta() en server.py (_resolver_nivel_senal).
- Sin senal tecnica activa (pero con score suficiente) -> se guarda en
  candidatos_fundamentales_sin_senal.json (no entra en watchlist.json),
  para revision futura o a la espera de que el propio sistema detecte la
  senal mas adelante.
- Score insuficiente (criterio 5) -> se guarda en
  candidatos_score_insuficiente.json, NUNCA en watchlist.json ni en
  sin_senal (motivo de descarte distinto: aqui puede haber senal tecnica
  activa, pero el fundamental no da para incluirlo).
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
from criterios_fundamentales import (
    construir_referencia_cohorte, calcular_score_fundamental, evaluar_distorsion_capital,
)

WATCHLIST_FILE = os.path.join(_PROJ_DIR, "watchlist.json")
SIN_SENAL_FILE = os.path.join(_PROJ_DIR, "candidatos_fundamentales_sin_senal.json")
SCORE_INSUFICIENTE_FILE = os.path.join(_PROJ_DIR, "candidatos_score_insuficiente.json")
DISTORSION_CAPITAL_FILE = os.path.join(_PROJ_DIR, "candidatos_distorsion_capital.json")
WACC_CACHE_FILE = os.path.join(_PROJ_DIR, "universo_global_fase2_wacc_cache.json")
PASAN_4_CRITERIOS_FILE = os.path.join(_PROJ_DIR, "universo_global_pasan_4_criterios.json")
LOG_FILE = os.path.join(_PROJ_DIR, "universo_global_scan.log")

# Criterio 5 (bloqueante) y el nuevo aviso de distorsion de capital viven en
# criterios_fundamentales.py -- fuente unica compartida con server.py, para
# que un cambio de peso/umbral no pueda quedar desincronizado entre sitios
# (bug real ya detectado una vez con el score: el email de n8n mostraba una
# formula vieja porque server.py nunca se actualizo cuando se corrigio aqui).


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


def procesar(candidatos, fund_cache, criterios_cache, wacc_cache):
    watchlist = _load_json(WATCHLIST_FILE, [])
    ya_en_watchlist = {str(i.get("ticker")) for i in watchlist}
    sin_senal = _load_json(SIN_SENAL_FILE, [])
    ya_sin_senal = {c["ticker"] for c in sin_senal}
    score_insuficiente = _load_json(SCORE_INSUFICIENTE_FILE, [])
    ya_score_insuficiente = {c["ticker"] for c in score_insuficiente}
    distorsion_capital = _load_json(DISTORSION_CAPITAL_FILE, [])
    ya_distorsion = {c["ticker"] for c in distorsion_capital}

    # Cohorte de referencia para el criterio 5: todos los que pasaron 1-4
    # (mismo fichero que ya usa esta fase), no solo los candidatos de esta
    # tanda -- normalizar min-max sobre un puñado de tickers sueltos no
    # tendria sentido relativo. Si ese fichero no existe (ejecucion aislada,
    # ej. pruebas), se cae a la propia lista de candidatos como cohorte.
    cohorte_tickers = _load_json(PASAN_4_CRITERIOS_FILE, None)
    if cohorte_tickers is not None:
        cohorte_tickers = [c["ticker"] if isinstance(c, dict) else c for c in cohorte_tickers]
    else:
        cohorte_tickers = list(candidatos)
        _log(f"  AVISO: {PASAN_4_CRITERIOS_FILE} no encontrado, usando solo los {len(candidatos)} candidatos de esta tanda como cohorte de referencia para el score (menos representativo)")
    referencia = construir_referencia_cohorte(cohorte_tickers, fund_cache, criterios_cache, wacc_cache)
    if referencia is None:
        _log("  AVISO: no hay datos suficientes (roe/eva/fcf/market_cap) para calcular el criterio 5 en ningun ticker de la cohorte -- todos los candidatos de esta tanda se descartan por falta de datos, un dato ausente no puede contar como aprobado")
    else:
        _log(f"  Criterio 5 (score): cohorte={referencia['n_cohorte']} tickers con datos completos, umbral (percentil 90)={referencia['percentil90']:.4f}")

    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    anadidos = []
    for i, tk in enumerate(candidatos, 1):
        if tk in ya_en_watchlist:
            _log(f"  {tk}: ya esta en watchlist.json, se omite")
            continue
        f = fund_cache.get(tk, {})
        c = criterios_cache.get(tk, {})
        w = wacc_cache.get(tk, {})
        score_tk, umbral_score, score_ok = calcular_score_fundamental(f.get("roe"), w.get("eva"), c.get("fcf"), w.get("market_cap"), referencia=referencia)
        if not score_ok:
            motivo = "sin datos suficientes para el score" if score_tk is None else f"score {score_tk:.4f} < umbral {umbral_score:.4f}"
            _log(f"  {tk}: NO supera el criterio 5 ({motivo}) -- descartado antes de evaluar señal técnica")
            if tk not in ya_score_insuficiente:
                score_insuficiente.append({
                    "ticker": tk, "nombre": f.get("nombre_empresa"), "sector": f.get("sector"),
                    "indice_origen": f.get("indice_origen"), "score": score_tk, "umbral_aplicado": umbral_score,
                    "motivo": motivo, "fecha_evaluacion": fecha_hoy,
                })
                ya_score_insuficiente.add(tk)
            continue
        # Techo de distorsion de capital (no bloqueante -- solo warning,
        # visible en watchlist.json/notes, /api/candidatos y el email; la
        # decision de excluir un ticker concreto por esto queda a revision
        # manual, mismo criterio ya aplicado a KGF.L/APTV con EVA negativo).
        distorsionado, motivo_distorsion = evaluar_distorsion_capital(f.get("roe"), c.get("roic"))
        if distorsionado:
            _log(f"  {tk}: AVISO distorsion de capital -- {motivo_distorsion}")
            if tk not in ya_distorsion:
                distorsion_capital.append({
                    "ticker": tk, "nombre": f.get("nombre_empresa"), "roe": f.get("roe"), "roic": c.get("roic"),
                    "motivo": motivo_distorsion, "fecha_evaluacion": fecha_hoy,
                })
                ya_distorsion.add(tk)
        try:
            entry_signal, entry_level, support, valido = evaluar_senal(tk)
        except Exception as e:
            _log(f"  {tk}: error evaluando senal - {e}")
            continue
        if valido:
            notas = f"Candidato del escaneo global (indice origen: {f.get('indice_origen', 'N/D')}). ROE={f.get('roe')}%, ROIC={c.get('roic')}%, FCF/BeneficioNeto={c.get('fcf_sobre_ni')}, PEG={c.get('peg')}. Score={score_tk:.4f} (umbral {umbral_score:.4f})."
            if distorsionado:
                notas += f" ⚠ {motivo_distorsion}"
            nueva_entrada = {
                "ticker": tk,
                "name": f.get("nombre_empresa") or tk,
                "entry_level": round(entry_level, 4) if entry_level else None,
                "entry_signal": entry_signal,
                "support": round(support, 4) if support else None,
                "stop": round(support, 4) if support else None,
                "theme": f.get("sector") or "",
                "notes": notas,
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
            _save_json(SCORE_INSUFICIENTE_FILE, score_insuficiente)
            _save_json(DISTORSION_CAPITAL_FILE, distorsion_capital)
            _log(f"  progreso fase3: {i}/{len(candidatos)}, {len(anadidos)} anadidos a watchlist hasta ahora")
        time.sleep(0.5)

    _save_json(WATCHLIST_FILE, watchlist)
    _save_json(SIN_SENAL_FILE, sin_senal)
    _save_json(SCORE_INSUFICIENTE_FILE, score_insuficiente)
    _save_json(DISTORSION_CAPITAL_FILE, distorsion_capital)
    _log(f"=== FIN FASE 3: {len(candidatos)} evaluados, {len(anadidos)} anadidos a watchlist.json, {len(sin_senal)} en seguimiento sin senal, {len(score_insuficiente)} descartados por score insuficiente, {len(distorsion_capital)} con aviso de distorsion de capital ===")
    return anadidos


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python escaneo_universo_fase3_tecnico_watchlist.py <lista_candidatos.json>")
        sys.exit(1)
    candidatos = json.load(open(sys.argv[1], encoding="utf-8"))
    fund_cache = _load_json("universo_global_fase1_cache.json", {})
    criterios_cache = _load_json("universo_global_fase1c_cache.json", {})
    wacc_cache = _load_json(WACC_CACHE_FILE, {})
    _log(f"=== INICIO FASE 3 sobre {len(candidatos)} candidatos ===")
    procesar(candidatos, fund_cache, criterios_cache, wacc_cache)
