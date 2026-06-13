"""
Level Price Scraper - Standalone
Scrapea precios SCL→BCN y manda top 10 combos por Telegram.
"""
import os
import json
import time
import requests
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# ─── CONFIG ──────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN")
CHAT_ID      = os.environ.get("CHAT_ID")

ORIGEN       = "SCL"
DESTINO      = "BCN"
MONEDA       = "USD"
FECHA_INICIO = datetime(2026, 10, 1)
FECHA_FIN    = datetime(2027, 6, 30)
MIN_NOCHES   = 3
MAX_NOCHES   = 21
TOP_N        = 10
TASAS        = 41

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ─── TELEGRAM ────────────────────────────────────────────────────
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=60)
        log(f"Telegram: {r.status_code} {r.text[:200]}")
    except Exception as e:
        log(f"Error Telegram: {e}")

# ─── SCRAPER ─────────────────────────────────────────────────────
def get_meses():
    meses = []
    d = FECHA_INICIO.replace(day=1)
    while d <= FECHA_FIN:
        meses.append((d.year, d.month))
        d = (d + timedelta(days=32)).replace(day=1)
    return meses

def scrape_precios():
    ida_prices = {}
    vuelta_prices = {}

    chrome_paths = [
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome_exe = next((c for c in chrome_paths if os.path.exists(c)), None)

    with sync_playwright() as p:
        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        }
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe
            log(f"Usando Chrome: {chrome_exe}")
        else:
            log("⚠️ Chrome no encontrado, usando Chromium")

        browser = p.chromium.launch(**launch_args)
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = ctx.new_page()

        log("Navegando a flylevel para obtener cookies...")
        page.goto(
            f"https://www.flylevel.com/Flight/Select/"
            f"?triptype=RT&o1={ORIGEN}&d1={DESTINO}"
            f"&dd1=2026-11-01&dd2=2026-11-08"
            "&ADT=1&CHD=0&INL=0&r=true&mm=false"
            f"&forcedCurrency={MONEDA}&forcedCulture=es-ES&newecom=true",
            wait_until="domcontentloaded", timeout=30000
        )
        try:
            page.wait_for_selector("#onetrust-accept-btn-handler", timeout=8000)
            page.click("#onetrust-accept-btn-handler")
            time.sleep(1)
        except:
            pass
        time.sleep(5)

        title = page.title()
        url_actual = page.url
        log(f"Título: {title}")
        log(f"URL: {url_actual}")

        # Verificar si Akamai bloqueó
        if "access denied" in title.lower() or "403" in title or "blocked" in title.lower():
            log("❌ Bloqueado por Akamai/firewall")
            send_telegram("❌ Level scraper bloqueado por firewall. Requiere intervención manual.")
            browser.close()
            return {}, {}

        def fetch_calendar(origin, destination, year, month):
            url = (f"/nwe/flights/api/calendar/"
                   f"?origin={origin}&destination={destination}"
                   f"&year={year}&month={month:02d}"
                   f"&adults=1&currency={MONEDA}"
                   f"&culture=es-ES&forcedCulture=es-ES&triptype=RT")
            result = page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch('{url}');
                        const d = await r.json();
                        return {{ ok: true, data: d?.data?.dayPrices || [], status: r.status }};
                    }} catch(e) {{
                        return {{ ok: false, data: [], error: e.toString() }};
                    }}
                }}
            """)
            if not result.get("ok"):
                log(f"    Error fetch: {result.get('error')}")
                return {}
            prices = {}
            for item in (result.get("data") or []):
                fecha = (item.get("date") or "")[:10]
                price = item.get("price")
                if fecha and price is not None:
                    prices[fecha] = float(price)
            return prices

        meses = get_meses()

        log(f"Scrapeando {len(meses)} meses IDA ({ORIGEN}→{DESTINO})...")
        for year, month in meses:
            prices = fetch_calendar(ORIGEN, DESTINO, year, month)
            ida_prices.update({k: v + TASAS for k, v in prices.items()})
            log(f"  {year}/{month:02d}: {len(prices)} días")
            time.sleep(0.5)

        log(f"Scrapeando {len(meses)} meses VUELTA ({DESTINO}→{ORIGEN})...")
        for year, month in meses:
            prices = fetch_calendar(DESTINO, ORIGEN, year, month)
            vuelta_prices.update({k: v + TASAS for k, v in prices.items()})
            log(f"  {year}/{month:02d}: {len(prices)} días")
            time.sleep(0.5)

        browser.close()

    return ida_prices, vuelta_prices

def find_combos(ida, vuelta):
    combos = []
    for ida_str, p_ida in sorted(ida.items()):
        ida_dt = datetime.strptime(ida_str, "%Y-%m-%d")
        if not (FECHA_INICIO <= ida_dt <= FECHA_FIN):
            continue
        for n in range(MIN_NOCHES, MAX_NOCHES + 1):
            vta_str = (ida_dt + timedelta(days=n)).strftime("%Y-%m-%d")
            p_vta = vuelta.get(vta_str)
            if not p_vta:
                continue
            combos.append({
                "ida": ida_str, "vuelta": vta_str, "noches": n,
                "p_ida": round(p_ida), "p_vta": round(p_vta),
                "total": round(p_ida + p_vta),
            })
    combos.sort(key=lambda x: x["total"])
    return combos

def enviar_telegram(combos):
    if not combos:
        send_telegram("⚠️ Scraper corrió pero no encontró combinaciones.")
        return

    best = combos[0]
    lines = [
        "✈️ LEVEL PRECIOS SCL→BCN",
        "",
        "🏆 MEJOR COMBO:",
        f"  IDA:    {best['ida']}",
        f"  VUELTA: {best['vuelta']}",
        f"  Noches: {best['noches']}",
        f"  Total:  ${best['total']} USD",
        "",
        f"📊 TOP {min(TOP_N, len(combos))} COMBOS:",
    ]
    for i, c in enumerate(combos[:TOP_N], 1):
        lines.append(f"  {i:>2}. {c['ida']} → {c['vuelta']} ({c['noches']}n) = ${c['total']}")

    send_telegram("\n".join(lines))

# ─── MAIN ─────────────────────────────────────────────────────────
def main():
    log("=== Level Scraper ===")
    ida, vuelta = scrape_precios()
    log(f"IDA: {len(ida)} días | VUELTA: {len(vuelta)} días")

    if not ida and not vuelta:
        return

    combos = find_combos(ida, vuelta)
    log(f"{len(combos)} combinaciones encontradas")
    enviar_telegram(combos)

if __name__ == "__main__":
    main()
