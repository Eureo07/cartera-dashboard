# -*- coding: utf-8 -*-
"""Fase 1b: filtro tecnico (RR/RRA/LT/LTA) sobre los candidatos que ya
pasaron el filtro fundamental estricto (ROE>10%, FCF>0, deuda<=3.5x con
dato real). Reutiliza get_entry_types() de screener.py sin modificarla
(funcion protegida). Cachea incrementalmente."""
import json, time
from datetime import datetime
from screener import get_entry_types

CACHE_FILE = "universo_global_fase1b_tecnico_cache.json"
LOG_FILE = "universo_global_scan.log"


def _log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    candidatos = json.load(open("_candidatos_fundamentales_temp.json", encoding="utf-8"))
    _log(f"=== INICIO FASE 1b (tecnico) sobre {len(candidatos)} candidatos fundamentales ===")
    cache = _load_cache()
    pendientes = [tk for tk in candidatos if tk not in cache]
    _log(f"Pendientes: {len(pendientes)}")
    con_senal = 0
    for i, tk in enumerate(pendientes, 1):
        try:
            tipos = get_entry_types(tk)
        except Exception as e:
            tipos = []
        cache[tk] = tipos
        if any(t in ("RR", "RRA", "LT", "LTA") for t in tipos):
            con_senal += 1
        if i % 25 == 0:
            _save_cache(cache)
            _log(f"  progreso fase1b: {i}/{len(pendientes)}, {con_senal} con senal tecnica activa hasta ahora")
        time.sleep(0.4)
    _save_cache(cache)
    _log(f"=== FIN FASE 1b: {len(pendientes)} procesados, {con_senal} con senal tecnica activa ===")
