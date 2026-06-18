# -*- coding: utf-8 -*-
"""
Alertas automáticas por email para la cartera.
Usa Gmail SMTP con contraseña de aplicación.
Configurar variable de entorno GMAIL_PASSWORD antes de usar.
"""
import smtplib, sys, os, json, yfinance as yf
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date
_PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJ_DIR not in sys.path:
    sys.path.insert(0, _PROJ_DIR)
from config_loader import CFG, get_logger

log = get_logger("alertas")

# ========== CONFIG ==========
BASE_DIR = CFG["base_dir"]
GMAIL_USER = CFG["email"]["gmail_user"]
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "")
SMTP_SERVER = CFG["email"]["smtp_server"]
SMTP_PORT = CFG["email"]["smtp_port"]
SCREENER_CACHE = CFG["paths"].get("radar_prev", os.path.join(BASE_DIR, "radar_prev.json"))

portfolio = CFG["portfolio"]

def get_current_prices():
    prices = {}
    for p in portfolio:
        try:
            stock = yf.Ticker(p["ticker"])
            info = stock.info
            prices[p["ticker"]] = info.get("regularMarketPrice") or info.get("previousClose")
        except:
            prices[p["ticker"]] = None
    return prices

def get_pnl(p, price):
    if price is None:
        return None, None
    cost = p["entry"] * p["shares"] + p["commission"]
    value = price * p["shares"] - p["commission"]
    pnl = value - cost
    pnl_pct = (pnl / cost) * 100
    return pnl, pnl_pct

def send_email(subject, html_body):
    if not GMAIL_PASSWORD:
        print("  ERROR: Variable de entorno GMAIL_PASSWORD no configurada")
        print("  Configúrala con: $env:GMAIL_PASSWORD = 'tu_contraseña_de_aplicación'")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = GMAIL_USER
        msg["To"] = GMAIL_USER
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"  Email enviado: {subject}")
        return True
    except Exception as e:
        print(f"  Error al enviar email: {e}")
        return False

def style_pnl(val):
    if val is None:
        return ""
    cls = "green" if val >= 0 else "red"
    return f' style="color:{'#166534' if val>=0 else '#991b1b'};background:{'#dcfce7' if val>=0 else '#fce4e4'};padding:2px 8px;border-radius:4px;font-weight:600;"'

def build_portfolio_table(prices):
    rows = ""
    for p in portfolio:
        price = prices.get(p["ticker"])
        pnl, pnl_pct = get_pnl(p, price)
        target = p["entry"] * 1.175
        dist_stop = ((price - p["stop"]) / p["stop"] * 100) if price and p["stop"] else None
        pnl_str = f"{pnl:+,.2f}" if pnl is not None else "N/D"
        pnl_pct_str = f"{pnl_pct:+.2f}%" if pnl_pct is not None else "N/D"
        price_str = f"{price:.4f}" if price else "N/D"
        dist_str = f"{dist_stop:.1f}%" if dist_stop is not None else "N/D"
        pnl_style = style_pnl(pnl)
        rows += f"""<tr>
  <td>{p['name']}</td>
  <td>{price_str}</td>
  <td{pnl_style}>{pnl_str}</td>
  <td{pnl_style}>{pnl_pct_str}</td>
  <td>{f"{target:.2f}"}</td>
  <td>{dist_str}</td>
</tr>"""
    return rows

# ========== ALERT CHECKERS ==========
def check_immediate_alerts():
    alerts = []
    prices = get_current_prices()
    for p in portfolio:
        price = prices.get(p["ticker"])
        if price is None:
            continue
        target = p["entry"] * 1.175
        cost = p["entry"] * p["shares"] + p["commission"]
        pnl_pct = ((price * p["shares"] - p["commission"] - cost) / cost) * 100
        # Stop loss
        if p["stop"] and price <= p["stop"]:
            subject = f"\U0001f6a8 STOP LOSS: {p['name']} ha tocado el stop"
            body = f"""
            <h2>\U0001f6a8 Stop Loss Alcanzado</h2>
            <p><strong>{p['name']}</strong> ({p['ticker']}) ha tocado el stop loss.</p>
            <p>Precio actual: {price:.4f} | Stop: {p['stop']:.2f}</p>
            <p>Revisar posici\u00f3n y decidir si mantener o vender.</p>
            """
            alerts.append((subject, body))
        # Target profit
        if pnl_pct >= 17.5:
            subject = f"\u2705 OBJETIVO: {p['name']} ha alcanzado +17.5%"
            body = f"""
            <h2>\u2705 Objetivo de Beneficios Alcanzado</h2>
            <p><strong>{p['name']}</strong> ({p['ticker']}) ha alcanzado +{pnl_pct:.1f}%.</p>
            <p>Rentabilidad objetivo (+17.5%) superada.</p>
            <p>Considerar tomar beneficios o revisar soporte.</p>
            """
            alerts.append((subject, body))
    return alerts

def check_daily_drop():
    alerts = []
    prices = get_current_prices()
    for p in portfolio:
        price = prices.get(p["ticker"])
        if price is None:
            continue
        try:
            stock = yf.Ticker(p["ticker"])
            hist = stock.history(period="5d")
            if len(hist) >= 2:
                prev_close = hist["Close"].iloc[-2]
                daily_change = ((price - prev_close) / prev_close) * 100
                if daily_change <= -5:
                    subject = f"\U0001f4c9 CA\u00cdDA: {p['name']} cae {daily_change:.1f}% en un d\u00eda"
                    body = f"""
                    <h2>\U0001f4c9 Ca\u00edda Diaria Significativa</h2>
                    <p><strong>{p['name']}</strong> ({p['ticker']}) ha ca\u00eddo {daily_change:.1f}% hoy.</p>
                    <p>Precio actual: {price:.4f} | Cierre anterior: {prev_close:.4f}</p>
                    <p>Revisar si hay noticias o cambios fundamentales.</p>
                    """
                    alerts.append((subject, body))
        except:
            pass
    return alerts

def build_weekly_summary():
    prices = get_current_prices()
    now_str = datetime.now().strftime("%d/%m/%Y")
    table = build_portfolio_table(prices)
    total_cost = sum(p["entry"] * p["shares"] + p["commission"] for p in portfolio)
    total_value = sum((prices.get(p["ticker"]) or 0) * p["shares"] - p["commission"] for p in portfolio)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost) * 100
    # Benchmark
    try:
        bm = yf.Ticker("^STOXX50E")
        hist = bm.history(period="1y")
        if len(hist) >= 2:
            bm_ret = ((hist["Close"].iloc[-1] - hist["Close"].iloc[0]) / hist["Close"].iloc[0]) * 100
        else:
            bm_ret = None
    except:
        bm_ret = None
    vs_bm = f"{total_pnl_pct - bm_ret:+.2f}%" if bm_ret else "N/D"
    bm_ret_str = f"{bm_ret:+.2f}%" if bm_ret else "N/D"
    total_cls = "#166534" if total_pnl >= 0 else "#991b1b"
    body = f"""
    <h2>\U0001f4ca Resumen Semanal — {now_str}</h2>
    <h3>Estado de la Cartera</h3>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-family:arial;width:100%;">
    <tr style="background:#f8f9fa;"><th>Empresa</th><th>Precio</th><th>P&amp;L</th><th>Rent.</th><th>Objetivo</th><th>Dist.Stop</th></tr>
    {table}
    <tr style="font-weight:700;background:#f0f0f0;"><td colspan="6">Total: {total_pnl:+,.2f} \u20ac ({total_pnl_pct:+.2f}%) | vs Euro Stoxx: {vs_bm} (\u00edndice: {bm_ret_str})</td></tr>
    </table>
    """
    subject = f"\U0001f4ca Radar semanal — {now_str}"
    return [(subject, body)]

def check_screener_new_opportunities():
    alerts = []
    radar_file = os.path.join(BASE_DIR, "radar_prev.json")
    # Load previous results if exists
    prev_tickers = set()
    if os.path.exists(radar_file):
        with open(radar_file, "r") as f:
            prev = json.load(f)
            prev_tickers = set(prev.get("tickers", []))
    # Get new results from screener
    try:
        import screener
        results = screener.run_screener()
        new_tickers = set(r["ticker"] for r in results)
        # Detect new tickers
        new_opportunities = new_tickers - prev_tickers
        for r in results:
            if r["ticker"] in new_opportunities:
                subject = f"\U0001f50d Nueva oportunidad: {r['name']} ({r['ticker']})"
                eper_str = f"{r['eper']:.1f}" if r['eper'] else "N/D"
                body = f"""
                <h2>\U0001f50d Nueva Oportunidad Detectada</h2>
                <p><strong>{r['name']}</strong> ({r['ticker']}) — {r['sector']}</p>
                <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-family:arial;">
                <tr><td>Score</td><td>{r['score']:.3f}</td></tr>
                <tr><td>PER</td><td>{eper_str}</td></tr>
                <tr><td>Rent.1A</td><td>{r['rent_1a']:+.1f}%</td></tr>
                <tr><td>ROE</td><td>{r['roe']:.1f}%</td></tr>
                <tr><td>EVA</td><td>{r['eva']:,.0f}</td></tr>
                <tr><td>FCF</td><td>{r['fcf']:,.0f}</td></tr>
                </table>
                """
                alerts.append((subject, body))
        # Save current results for next comparison
        with open(radar_file, "w") as f:
            json.dump({"tickers": list(new_tickers), "date": datetime.now().isoformat()}, f)
    except Exception as e:
        print(f"  Error al ejecutar screener: {e}")
    return alerts

# ========== MAIN ==========
if __name__ == "__main__":
    import sys
    print("=" * 50)
    print("ALERTAS — Cartera")
    print("=" * 50)
    if not GMAIL_PASSWORD:
        print("ADVERTENCIA: GMAIL_PASSWORD no configurada.")
        print("  Crea una contraseña de aplicación en:\n  https://myaccount.google.com/apppasswords")
        print("  Luego: $env:GMAIL_PASSWORD = 'tu_contraseña'")
        sys.exit(1)
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    sent = 0
    if mode == "daily":
        print("\n--- Alertas inmediatas ---")
        for subj, body in check_immediate_alerts():
            if send_email(subj, body):
                sent += 1
        for subj, body in check_daily_drop():
            if send_email(subj, body):
                sent += 1
    elif mode == "weekly":
        print("\n--- Resumen semanal ---")
        for subj, body in build_weekly_summary():
            if send_email(subj, body):
                sent += 1
        # Also include screener opportunities in weekly
        print("\n--- Oportunidades del screener ---")
        for subj, body in check_screener_new_opportunities():
            if send_email(subj, body):
                sent += 1
    elif mode == "screener":
        print("\n--- Nuevas oportunidades screener ---")
        for subj, body in check_screener_new_opportunities():
            if send_email(subj, body):
                sent += 1
    else:
        print(f"Modo desconocido: {mode}")
        print("Usa: python alertas.py [daily|weekly|screener]")
    print(f"\n{'-'*50}")
    print(f"Alertas enviadas: {sent}")
    print("Hecho.")
