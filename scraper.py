"""
Level Price Scraper v3
- Telegram solo si min(p_ida) o min(p_vuelta) < $150
- Historial en historial.json (commiteado al repo)
- Retry automático en meses con 0 días
"""
import os
import json
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from twilio.rest import Client
from playwright.sync_api import sync_playwright

# ─── CONFIG ──────────────────────────────────────────────────────
BOT_TOKEN          = os.environ.get("BOT_TOKEN")
CHAT_ID            = os.environ.get("CHAT_ID")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM        = os.environ.get("TWILIO_FROM")
TWILIO_TO          = os.environ.get("TWILIO_TO")

ORIGEN       = "SCL"
DESTINO      = "BCN"
MONEDA       = "USD"
FECHA_INICIO = datetime(2026, 9, 1)
FECHA_FIN    = datetime(2027, 3, 30)
MIN_NOCHES   = 3
MAX_NOCHES   = 21
TOP_N        = 10
TASAS        = 41
UMBRAL_USD   = 150
HISTORIAL    = "historial.json"
MAX_RETRIES  = 3
DELAY_BASE   = 2.0

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ─── TELEGRAM ────────────────────────────────────────────────────
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=60)
        log(f"Telegram: {r.status_code}")
    except Exception as e:
        log(f"Error Telegram: {e}")

# ─── TWILIO ───────────────────────────────────────────────────────
def hacer_llamada(mensaje):
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        call = client.calls.create(
            to=TWILIO_TO,
            from_=TWILIO_FROM,
            twiml=f'<Response><Say language="es-MX">{mensaje}</Say></Response>'
        )
        log(f"Llamada Twilio: {call.sid}")
    except Exception as e:
        log(f"Error Twilio: {e}")

# ─── HISTORIAL JSON ───────────────────────────────────────────────
def cargar_historial():
    if Path(HISTORIAL).exists():
        try:
            return json.loads(Path(HISTORIAL).read_text())
        except:
            pass
    return {"min_ida": None, "min_vuelta": None, "updated_at": None}

def guardar_historial(data):
    Path(HISTORIAL).write_text(json.dumps(data, indent=2))

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
            log("⚠️ Usando Chromium")

        browser = p.chromium.launch(**launch_args)
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = ctx.new_page()

        log("Navegando a flylevel...")
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
        log(f"Título: {title}")
        if "access denied" in title.lower() or "blocked" in title.lower():
            log("❌ Bloqueado por firewall")
            send_telegram("❌ Level scraper bloqueado. Requiere intervención manual.")
            browser.close()
            return {}, {}

        def fetch_calendar(origin, destination, year, month, retry=0):
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
                        return {{ ok: true, data: d?.data?.dayPrices || [] }};
                    }} catch(e) {{
                        return {{ ok: false, data: [] }};
                    }}
                }}
            """)
            prices = {}
            for item in (result.get("data") or []):
                fecha = (item.get("date") or "")[:10]
                price = item.get("price")
                if fecha and price is not None:
                    prices[fecha] = float(price)

            if len(prices) == 0 and retry < MAX_RETRIES:
                wait = DELAY_BASE * (retry + 2)
                log(f"    0 días, reintentando en {wait:.0f}s... ({retry+1}/{MAX_RETRIES})")
                time.sleep(wait)
                return fetch_calendar(origin, destination, year, month, retry + 1)

            return prices

        meses = get_meses()

        log(f"Scrapeando {len(meses)} meses IDA ({ORIGEN}→{DESTINO})...")
        for year, month in meses:
            prices = fetch_calendar(ORIGEN, DESTINO, year, month)
            ida_prices.update({k: v + TASAS for k, v in prices.items()})
            log(f"  {year}/{month:02d}: {len(prices)} días")
            time.sleep(DELAY_BASE)

        log(f"Scrapeando {len(meses)} meses VUELTA ({DESTINO}→{ORIGEN})...")
        for year, month in meses:
            prices = fetch_calendar(DESTINO, ORIGEN, year, month)
            vuelta_prices.update({k: v + TASAS for k, v in prices.items()})
            log(f"  {year}/{month:02d}: {len(prices)} días")
            time.sleep(DELAY_BASE)

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

def verificar_y_alertar(ida_prices, vuelta_prices):
    if not ida_prices and not vuelta_prices:
        return

    min_ida    = min(ida_prices.values()) if ida_prices else None
    min_vuelta = min(vuelta_prices.values()) if vuelta_prices else None

    historial     = cargar_historial()
    prev_min_ida  = historial.get("min_ida")
    prev_min_vuelta = historial.get("min_vuelta")

    alerta_partes = []

    # IDA: baja de umbral y antes estaba sobre (o no había registro)
    if min_ida and min_ida < UMBRAL_USD:
        if prev_min_ida is None or prev_min_ida >= UMBRAL_USD:
            alerta_partes.append(f"ida ${round(min_ida)}")

    # VUELTA: ídem
    if min_vuelta and min_vuelta < UMBRAL_USD:
        if prev_min_vuelta is None or prev_min_vuelta >= UMBRAL_USD:
            alerta_partes.append(f"vuelta ${round(min_vuelta)}")

    if alerta_partes:
        texto = " y ".join(alerta_partes)
        log(f"🚨 UMBRAL ALCANZADO: {texto}")
        hacer_llamada(f"Alerta Level. Pasaje de {texto} dólares disponible. Revisa Telegram.")
        send_telegram(f"🚨 ALERTA PRECIO\nPasaje {texto} USD — bajo el umbral de ${UMBRAL_USD}!")
    else:
        log(f"Sin alerta. min_ida=${round(min_ida) if min_ida else 'N/A'} min_vuelta=${round(min_vuelta) if min_vuelta else 'N/A'}")

    # Guardar historial actualizado
    historial["min_ida"]     = round(min_ida) if min_ida else None
    historial["min_vuelta"]  = round(min_vuelta) if min_vuelta else None
    historial["updated_at"]  = datetime.now().isoformat()
    guardar_historial(historial)

# ─── MAIN ─────────────────────────────────────────────────────────
def main():
    log("=== Level Scraper v3 ===")
    ida, vuelta = scrape_precios()
    log(f"IDA: {len(ida)} días | VUELTA: {len(vuelta)} días")

    if not ida and not vuelta:
        return

    verificar_y_alertar(ida, vuelta)

    combos = find_combos(ida, vuelta)
    log(f"{len(combos)} combinaciones encontradas")

    if not combos:
        log("Sin combinaciones.")
        return

    best = combos[0]
    min_ida    = round(min(ida.values())) if ida else "N/A"
    min_vuelta = round(min(vuelta.values())) if vuelta else "N/A"

    lines = [
        "✈️ LEVEL SCL↔BCN",
        f"🛫 Min ida:    ${min_ida} USD",
        f"🛬 Min vuelta: ${min_vuelta} USD",
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

if __name__ == "__main__":
    main()
