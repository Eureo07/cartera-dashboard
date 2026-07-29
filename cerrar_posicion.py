# -*- coding: utf-8 -*-
"""
Cierra una posición activa del portfolio:
1. Pregunta ticker, precio de cierre, fecha y motivo
2. Calcula P&L
3. Mueve la posición a cartera_cerrada.json (incluyendo precio_soporte_entrada = stop)
4. Elimina la posición de config.json
"""
import json
import sys
from datetime import datetime

MOTIVOS = [
    "stop ejecutado",
    "reversión señal técnica",
    "toma de beneficios manual",
    "corte de pérdidas manual",
    "cambio de tesis",
    "otro",
]

CONFIG = "config.json"
CERRADAS = "cartera_cerrada.json"


def cargar_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    cfg = cargar_json(CONFIG)
    portfolio = cfg.get("portfolio", [])
    if not portfolio:
        print("No hay posiciones activas en el portfolio.")
        return

    print("\nPosiciones activas:")
    for i, p in enumerate(portfolio, 1):
        print(f"  {i}. {p['ticker']} — {p['name']} ({p['shares']} acc, entry {p['entry']}, stop {p['stop']})")

    sel = input("\nTicker a cerrar (ej. ENR.DE): ").strip()
    match = [p for p in portfolio if p["ticker"] == sel]
    if not match:
        print(f"Ticker '{sel}' no encontrado en el portfolio.")
        return
    p = match[0]

    try:
        precio_cierre = float(input("Precio de cierre (€/acción): ").strip().replace(",", "."))
    except ValueError:
        print("Precio inválido.")
        return

    fecha_cierre = input("Fecha de cierre (YYYY-MM-DD, Enter = hoy): ").strip()
    if not fecha_cierre:
        fecha_cierre = datetime.now().strftime("%Y-%m-%d")

    print("\nMotivos de cierre disponibles:")
    for i, m in enumerate(MOTIVOS, 1):
        print(f"  {i}. {m}")
    try:
        idx = int(input("Selecciona motivo (número): ").strip())
        motivo = MOTIVOS[idx - 1] if 1 <= idx <= len(MOTIVOS) else "otro"
    except (ValueError, IndexError):
        motivo = "otro"

    shares = p["shares"]
    entry = p["entry"]
    commission = p.get("commission", 0)
    stop = p.get("stop")

    coste = entry * shares + commission
    valor_venta = precio_cierre * shares
    pnl_eur = round(valor_venta - coste, 2)
    pnl_pct = round((pnl_eur / coste) * 100, 2) if coste else 0

    cerrada = {
        "operacion": p["name"],
        "entrada": entry,
        "acciones": shares,
        "coste": round(coste, 2),
        "venta": precio_cierre,
        "pnl_eur": pnl_eur,
        "pnl_pct": pnl_pct,
        "precio_soporte_entrada": stop,
        "motivo_cierre": motivo,
        "fecha_entrada": p["entry_date"],
        "fecha_cierre": fecha_cierre,
        "regimen_entrada": None,
    }

    # Confirm
    print(f"\n--- Resumen ---")
    print(f"Operación:  {cerrada['operacion']} ({sel})")
    print(f"Entrada:    {entry} × {shares} = {coste:.2f}€")
    print(f"Venta:      {precio_cierre} × {shares} = {valor_venta:.2f}€")
    print(f"P&L:        {pnl_eur:+,.2f}€ ({pnl_pct:+.2f}%)")
    print(f"Soporte:    {stop}€" if stop else "Soporte:    (no disponible)")
    print(f"Motivo:     {motivo}")
    print(f"Fecha cierre: {fecha_cierre}")

    conf = input("\n¿Confirmar cierre? (s/N): ").strip().lower()
    if conf != "s":
        print("Cancelado.")
        return

    # Persist
    cerradas = cargar_json(CERRADAS)
    cerradas.append(cerrada)
    guardar_json(CERRADAS, cerradas)

    # Remove from portfolio
    cfg["portfolio"] = [x for x in portfolio if x["ticker"] != sel]
    guardar_json(CONFIG, cfg)

    print(f"\n✓ {p['name']} cerrada y movida a {CERRADAS}")
    print(f"  R múltiple: {'N/A (sin soporte)' if stop is None else f'{(precio_cierre - entry) / (entry - stop):+.2f}x'}")


if __name__ == "__main__":
    main()
