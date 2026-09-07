# -*- coding: utf-8 -*-
"""
PER y CAPE por indice de referencia (por pais), via scraping de Siblis
Research (siblisresearch.com) -- unica fuente publica gratuita encontrada
con cobertura razonable (15 mercados). No hay fuente automatizada via
yfinance/FMP (verificado: yfinance .info.trailingPE/forwardPE devuelve
None para indices; no hay clave FMP en el proyecto). STAR Capital, la
fuente originalmente propuesta, ya no existe como sitio independiente
(absorbida por Bellevue Group AG, starcapital.de redirige a bellevue.ch).

Local-only, igual que deuda_ebitda.py: se ejecuta solo dentro de
generate_dashboard.py, nunca en Render. server.py solo lee el cache
(get_valoracion_pais), nunca dispara red.

Cache keyed por el nombre de "Nation" tal cual lo usa Siblis Research
(ej. "United States", "Spain", "Australia"), independiente del simbolo
Yahoo usado en indices_referencia (ese sirve para el retorno de indice en
vivo via yfinance, un proposito distinto).

Las tablas de Siblis son HTML estatico (plugin "supsystic-table"), cada
celda con atributos data-cell-id/data-original-value explicitos -- se
parsea por esas coordenadas en vez de por layout visual, mas robusto que
pandas.read_html frente a rowspan/colspan.

Cobertura confirmada (verificado en vivo):
- CAPE: Canada, United States, United Kingdom, Germany, Italy, France,
  Spain, India, Japan, China, Taiwan, Hong Kong, South Korea, Australia.
- PER (P/E TTM): igual que CAPE EXCEPTO Spain (no aparece en la tabla de
  PER de Siblis) -- asimetria real, no un bug. IBEX 35 (ITX.MC, ACS.MC)
  siempre tendra CAPE real pero PER del indice "N/D".
- Paises sin cobertura (ej. Paises Bajos, por THEON.AS) quedan siempre en
  None -- hueco documentado, nunca forzado.
"""
import os
import json
import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_PROJ_DIR, "indices_valoracion_cache.json")

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

CAPE_URL = "https://siblisresearch.com/data/cape-ratios-by-country/"
PE_URL = "https://siblisresearch.com/data/pe-ratios-by-country/"

_SKIP_NATIONS = {"Global Equity Markets"}


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


def _parse_cape_table(html):
    """Tabla CAPE: 1 fila por pais. Columna C = valor de la fecha mas
    reciente (las columnas de fecha estan ordenadas descendente, la mas
    reciente primero). Devuelve {nation: cape_float}."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for td_a in soup.select('td[data-cell-id^="A"]'):
        nation = (td_a.get("data-original-value") or "").strip()
        if not nation or nation in _SKIP_NATIONS:
            continue
        row = td_a["data-cell-id"][1:]
        td_c = soup.select_one(f'td[data-cell-id="C{row}"]')
        if td_c is None:
            continue
        val = td_c.get("data-original-value")
        try:
            out[nation] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def _parse_pe_table(html):
    """Tabla PER: bloque de 3 filas por pais (P/E TTM, EPS TTM, Forward
    P/E). Se localiza la fila 'P/E (TTM)' por su CONTENIDO (columna C),
    no por posicion fija dentro del bloque -- robusto a reordenamientos.
    El pais (columna A) solo aparece en la primera fila del bloque
    (celda con rowspan visual), por eso se recuerda el ultimo visto.
    Devuelve {nation: per_ttm_float}."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    current_nation = None
    for td_a in soup.select('td[data-cell-id^="A"]'):
        nation = (td_a.get("data-original-value") or "").strip()
        if nation and nation not in _SKIP_NATIONS:
            current_nation = nation
        if current_nation is None:
            continue
        row = td_a["data-cell-id"][1:]
        td_c = soup.select_one(f'td[data-cell-id="C{row}"]')
        if td_c is None:
            continue
        label = (td_c.get("data-original-value") or "").strip()
        if label == "P/E (TTM)":
            td_d = soup.select_one(f'td[data-cell-id="D{row}"]')
            if td_d is None:
                continue
            val = td_d.get("data-original-value")
            try:
                out[current_nation] = float(val)
            except (TypeError, ValueError):
                continue
    return out


def actualizar_cache():
    """Local-only. Descarga y parsea ambas tablas de Siblis Research,
    escribe indices_valoracion_cache.json. Si el parseo falla (estructura
    de la tabla cambio, red caida, tabla vacia, etc.), loguea warning y
    NO borra el cache existente -- mejor servir el ultimo dato bueno que
    un cache vacio o a medias."""
    existing = _read_json(CACHE_FILE, {})
    cape_data = {}
    pe_data = {}
    algo_ok = False

    try:
        resp = _SESSION.get(CAPE_URL, timeout=20)
        resp.raise_for_status()
        cape_data = _parse_cape_table(resp.text)
        if not cape_data:
            raise ValueError("tabla CAPE parseada pero vacia")
        algo_ok = True
    except Exception as e:
        log.warning(f"No se pudo obtener/parsear CAPE de Siblis Research: {e}")

    try:
        resp = _SESSION.get(PE_URL, timeout=20)
        resp.raise_for_status()
        pe_data = _parse_pe_table(resp.text)
        if not pe_data:
            raise ValueError("tabla PER parseada pero vacia")
        algo_ok = True
    except Exception as e:
        log.warning(f"No se pudo obtener/parsear PER de Siblis Research: {e}")

    if not algo_ok:
        log.warning(
            "Scraping de Siblis Research fallo por completo -- se conserva "
            "el cache existente sin cambios."
        )
        return existing

    naciones = set(cape_data) | set(pe_data) | set(existing.keys())
    fecha = datetime.now(timezone.utc).isoformat()
    nuevo = {}
    for nation in naciones:
        prev = existing.get(nation, {})
        cape_val = cape_data.get(nation, prev.get("cape_indice"))
        per_val = pe_data.get(nation, prev.get("per_indice"))
        nuevo[nation] = {
            "per_indice": per_val,
            "cape_indice": cape_val,
            "fecha_actualizacion": fecha,
            "fuente": "Siblis Research (siblisresearch.com)",
        }
    _write_json(CACHE_FILE, nuevo)
    log.info(f"Cache de valoracion de indices actualizado: {len(nuevo)} mercados.")
    return nuevo


def get_valoracion_pais(nation):
    """Lectura pura, segura en Render (nunca dispara red). Devuelve
    {"per_indice": float|None, "cape_indice": float|None,
     "fecha_actualizacion": str, "fuente": str}."""
    if not nation:
        return {"per_indice": None, "cape_indice": None, "fecha_actualizacion": "", "fuente": ""}
    data = _read_json(CACHE_FILE, {})
    e = data.get(nation) or {}
    return {
        "per_indice": e.get("per_indice"),
        "cape_indice": e.get("cape_indice"),
        "fecha_actualizacion": e.get("fecha_actualizacion") or "",
        "fuente": e.get("fuente") or "",
    }
