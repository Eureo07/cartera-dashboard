# -*- coding: utf-8 -*-
"""Fase 3: senal tecnica real (RR/RRA/LT/LTA/PER) sobre los candidatos que
pasen Fase 1 (+ Fase 2 cuando exista) del escaneo de universo global.

Reutiliza sin modificar las funciones protegidas de screener.py:
get_entry_types(), calcular_soporte_resistencia() -- y la ya construida
calcular_trendline_lta() (no protegida, pero misma logica que usa ITX/RRU.DE).

Reglas:
- Criterio 5 (score fundamental, BLOQUEANTE, se aplica ANTES de la senal
  tecnica -- ver calcular_scores_y_umbral()/CRITERIO_5_SCORE): un
  candidato por debajo del umbral se descarta directamente, sin gastar
  tiempo evaluando senal tecnica. Hasta esta version no existia ningun
  script que aplicara esto como filtro real (hueco real: estaba en la
  especificacion pero nunca se codigo) -- auditoria confirmo que, con los
  datos ya calculados en universo_global_scores_finales.json, esto no
  cambia el resultado para los candidatos ya en watchlist.json (todos
  superan el umbral con margen), pero a partir de ahora se aplica siempre.
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

import pandas as pd

_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)

from screener import get_entry_types, calcular_soporte_resistencia, calcular_trendline_lta, normalized_score

WATCHLIST_FILE = os.path.join(_PROJ_DIR, "watchlist.json")
SIN_SENAL_FILE = os.path.join(_PROJ_DIR, "candidatos_fundamentales_sin_senal.json")
SCORE_INSUFICIENTE_FILE = os.path.join(_PROJ_DIR, "candidatos_score_insuficiente.json")
WACC_CACHE_FILE = os.path.join(_PROJ_DIR, "universo_global_fase2_wacc_cache.json")
PASAN_4_CRITERIOS_FILE = os.path.join(_PROJ_DIR, "universo_global_pasan_4_criterios.json")
SCORES_FINALES_FILE = os.path.join(_PROJ_DIR, "universo_global_scores_finales.json")
LOG_FILE = os.path.join(_PROJ_DIR, "universo_global_scan.log")

# Criterio 5 (bloqueante): Score = ROE 25% + EVA%mcap 25% + FCF%mcap 50%,
# normalizado min-max (normalized_score(), sin modificar) sobre la cohorte
# de candidatos que ya pasaron los criterios 1-4
# (universo_global_pasan_4_criterios.json). EVA y FCF se normalizan como %
# de capitalizacion (no en dolares brutos) -- misma regla anti-dominio de
# mega-caps ya establecida en el resto del proyecto (evita que una mega-cap
# dentro de la cohorte domine el ranking solo por tamano). Umbral = percentil
# 90 de esa distribucion, SIEMPRE recalculado sobre los datos actuales de la
# cohorte -- nunca hardcodear un numero fijo, el universo cambia entre
# escaneos y el percentil se mueve con el.
CRITERIO_5_PERCENTIL = 0.90


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


def calcular_scores_y_umbral(cohorte_tickers, fund_cache, criterios_cache, wacc_cache):
    """Criterio 5 (bloqueante): construye la cohorte de referencia (todos
    los tickers de cohorte_tickers con roe/eva/fcf/market_cap disponibles),
    calcula el score de cada uno y el umbral = percentil 90 de esa
    distribucion. Devuelve (scores: {ticker: float}, umbral: float|None --
    None si no hay suficientes datos para calcular nada, en cuyo caso el
    criterio 5 NO puede aplicarse y debe tratarse como bloqueante por
    ausencia de datos, no como un pase automatico).

    Si universo_global_scores_finales.json existe, se usa DIRECTAMENTE como
    fuente de verdad (auditoria confirmo: recalcular desde cero con
    fund_cache/criterios_cache/wacc_cache da un resultado distinto -- ese
    fichero se genero sobre una cohorte de 401 tickers, no los 432 con datos
    completos en pasan_4_criterios, probablemente tras excluir aseguradoras/
    anomalias de datos ya tratadas en su momento fuera de codigo committeado
    -- reproducir esa exclusion a ciegas aqui arriesgaria introducir una
    inconsistencia nueva). Solo si ese fichero no existe se recalcula desde
    cero (fallback para universos futuros sin ese artefacto ya calculado),
    dejando constancia explicita en el log de que ese fallback no aplica
    las mismas exclusiones."""
    if os.path.exists(SCORES_FINALES_FILE):
        try:
            data = _load_json(SCORES_FINALES_FILE, [])
            scores = {d["ticker"]: d["score"] for d in data if d.get("score") is not None}
            if scores:
                umbral = float(pd.Series(list(scores.values())).quantile(CRITERIO_5_PERCENTIL))
                return scores, umbral
        except Exception as e:
            _log(f"  AVISO: no se pudo leer {SCORES_FINALES_FILE} ({e}), recalculando desde cero")
    rows = []
    for tk in cohorte_tickers:
        roe = fund_cache.get(tk, {}).get("roe")
        fcf = criterios_cache.get(tk, {}).get("fcf")
        w = wacc_cache.get(tk, {})
        eva = w.get("eva")
        mcap = w.get("market_cap")
        if roe is not None and eva is not None and fcf is not None and mcap:
            rows.append({
                "ticker": tk, "roe": roe,
                "eva_pct_mc": eva / mcap * 100,
                "fcf_pct_mc": fcf / mcap * 100,
            })
    if not rows:
        return {}, None
    df = pd.DataFrame(rows)
    n_roe = normalized_score(df["roe"])
    n_eva = normalized_score(df["eva_pct_mc"])
    n_fcf = normalized_score(df["fcf_pct_mc"])
    df["score"] = n_roe * 0.25 + n_eva * 0.25 + n_fcf * 0.50
    umbral = float(df["score"].quantile(CRITERIO_5_PERCENTIL))
    scores = dict(zip(df["ticker"], df["score"]))
    return scores, umbral


def procesar(candidatos, fund_cache, criterios_cache, wacc_cache):
    watchlist = _load_json(WATCHLIST_FILE, [])
    ya_en_watchlist = {str(i.get("ticker")) for i in watchlist}
    sin_senal = _load_json(SIN_SENAL_FILE, [])
    ya_sin_senal = {c["ticker"] for c in sin_senal}
    score_insuficiente = _load_json(SCORE_INSUFICIENTE_FILE, [])
    ya_score_insuficiente = {c["ticker"] for c in score_insuficiente}

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
    scores, umbral_score = calcular_scores_y_umbral(cohorte_tickers, fund_cache, criterios_cache, wacc_cache)
    if umbral_score is None:
        _log("  AVISO: no hay datos suficientes (roe/eva/fcf/market_cap) para calcular el criterio 5 en ningun ticker de la cohorte -- todos los candidatos de esta tanda se descartan por falta de datos, un dato ausente no puede contar como aprobado")
    else:
        _log(f"  Criterio 5 (score): cohorte={len(scores)} tickers con datos completos, umbral (percentil {int(CRITERIO_5_PERCENTIL*100)})={umbral_score:.4f}")

    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    anadidos = []
    for i, tk in enumerate(candidatos, 1):
        if tk in ya_en_watchlist:
            _log(f"  {tk}: ya esta en watchlist.json, se omite")
            continue
        score_tk = scores.get(tk)
        if umbral_score is None or score_tk is None or score_tk < umbral_score:
            motivo = "sin datos suficientes para el score" if (umbral_score is None or score_tk is None) else f"score {score_tk:.4f} < umbral {umbral_score:.4f}"
            _log(f"  {tk}: NO supera el criterio 5 ({motivo}) -- descartado antes de evaluar señal técnica")
            if tk not in ya_score_insuficiente:
                f = fund_cache.get(tk, {})
                score_insuficiente.append({
                    "ticker": tk, "nombre": f.get("nombre_empresa"), "sector": f.get("sector"),
                    "indice_origen": f.get("indice_origen"), "score": score_tk, "umbral_aplicado": umbral_score,
                    "motivo": motivo, "fecha_evaluacion": fecha_hoy,
                })
                ya_score_insuficiente.add(tk)
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
            _save_json(SCORE_INSUFICIENTE_FILE, score_insuficiente)
            _log(f"  progreso fase3: {i}/{len(candidatos)}, {len(anadidos)} anadidos a watchlist hasta ahora")
        time.sleep(0.5)

    _save_json(WATCHLIST_FILE, watchlist)
    _save_json(SIN_SENAL_FILE, sin_senal)
    _save_json(SCORE_INSUFICIENTE_FILE, score_insuficiente)
    _log(f"=== FIN FASE 3: {len(candidatos)} evaluados, {len(anadidos)} anadidos a watchlist.json, {len(sin_senal)} en seguimiento sin senal, {len(score_insuficiente)} descartados por score insuficiente ===")
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
