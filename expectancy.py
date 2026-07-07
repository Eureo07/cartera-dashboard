import json
import os
from datetime import datetime


def cargar_cartera_cerrada(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "cartera_cerrada.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def calcular_expectancy(trades):
    n = len(trades)
    winners = [t for t in trades if t["pnl_pct"] > 0]
    losers = [t for t in trades if t["pnl_pct"] <= 0]

    n_w = len(winners)
    n_l = len(losers)

    win_rate = round((n_w / n) * 100, 1) if n else 0.0
    loss_rate = round((n_l / n) * 100, 1) if n else 0.0

    avg_win = round(sum(t["pnl_pct"] for t in winners) / n_w, 2) if n_w else 0.0
    avg_loss = round(abs(sum(t["pnl_pct"] for t in losers) / n_l), 2) if n_l else 0.0

    expectancy = round(
        (win_rate / 100 * avg_win) - (loss_rate / 100 * avg_loss), 2
    )

    payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss else 0.0

    annualized_return = None
    dates = []
    for t in trades:
        if "fecha_cierre" in t and "entry_date" in t:
            try:
                fd = datetime.strptime(t["fecha_cierre"], "%Y-%m-%d")
                ed = datetime.strptime(t["entry_date"], "%d/%m/%Y")
                dates.extend([ed, fd])
            except (ValueError, KeyError):
                pass
    if len(dates) == 2 * n:
        first_entry = min(dt for i, dt in enumerate(dates) if i % 2 == 0)
        last_close = max(dt for i, dt in enumerate(dates) if i % 2 == 1)
        days = (last_close - first_entry).days
        if days > 0:
            total_cost = sum(t["cost"] for t in trades)
            total_pnl = sum(t["pnl_eur"] for t in trades)
            total_return = total_pnl / total_cost if total_cost else 0
            annualized_return = round(
                ((1 + total_return) ** (365 / days) - 1) * 100, 2
            )

    return {
        "total_trades": n,
        "winners": n_w,
        "losers": n_l,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "payoff_ratio": payoff_ratio,
        "annualized_return": annualized_return,
    }
