import os
import base64
import pickle
import json
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from twilio.rest import Client
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

# ─── CONFIGURACIÓN ───────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM        = os.environ.get("TWILIO_FROM")
TWILIO_TO          = os.environ.get("TWILIO_TO")
BOT_TOKEN          = os.environ.get("BOT_TOKEN")
CHAT_ID            = os.environ.get("CHAT_ID")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# ─── SCRAPER CONFIG ───────────────────────────────────────────────
ORIGEN       = "SCL"
DESTINO      = "BCN"
MONEDA       = "USD"
FECHA_INICIO = datetime(2026, 10, 1)
FECHA_FIN    = datetime(2027, 6, 30)
MIN_NOCHES   = 3
MAX_NOCHES   = 21
TOP_N        = 10

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ─── TELEGRAM ────────────────────────────────────────────────────
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=60)
        print(f"Telegram: {r.status_code}")
    except Exception as e:
        print(f"Error Telegram: {e}")

# ─── AUTENTICACIÓN GMAIL ──────────────────────────────────────────
def get_gmail_service():
    creds = None
    token_b64 = os.environ.get("GMAIL_TOKEN2")
    if token_b64:
        token_bytes = base64.b64decode(token_b64)
        with open("token.pickle", "wb") as f:
            f.write(token_bytes)
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.pickle", "wb") as f:
            pickle.dump(creds, f)
    return build("gmail", "v1", credentials=creds)

# ─── BUSCAR CORREOS CON ETIQUETA LEVEL ───────────────────────────
def get_label_id(service, nombre="LEVEL"):
    resultado = service.users().labels().list(userId="me").execute()
    for label in resultado.get("labels", []):
        if label["name"].upper() == nombre.upper():
            return label["id"]
    return None

def buscar_correos_level(service):
    label_id = get_label_id(service, "LEVEL")
    if not label_id:
        print("⚠️ Etiqueta LEVEL no encontrada, usando fallback por remitente.")
        result = service.users().messages().list(
            userId="me", q="from:flylevel.com is:unread", maxResults=10
        ).execute()
    else:
        print(f"✅ Etiqueta LEVEL encontrada: {label_id}")
        result = service.users().messages().list(
            userId="me", labelIds=[label_id, "UNREAD"], maxResults=10
        ).execute()
    return result.get("messages", [])

def marcar_leido(service, msg_id):
    service.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()

# ─── LLAMADA TWILIO ───────────────────────────────────────────────
def hacer_llamada(asunto=""):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    call = client.calls.create(
        to=TWILIO_TO,
        from_=TWILIO_FROM,
        twiml='<Response><Say language="es-MX">Tienes un correo nuevo de Level. Revisa tu Gmail.</Say></Response>'
    )
    print(f"Llamada iniciada: {call.sid}")
    return call.sid

# ─── SCRAPER LEVEL ────────────────────────────────────────────────
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

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ]
        )
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = ctx.new_page()

        log("Obteniendo cookies de flylevel...")
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
        time.sleep(3)
        log("✓ Cookies activas")

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
                        return d?.data?.dayPrices || [];
                    }} catch(e) {{
                        return [];
                    }}
                }}
            """)
            prices = {}
            for item in (result or []):
                fecha = (item.get("date") or "")[:10]
                price = item.get("price")
                if fecha and price is not None:
                    prices[fecha] = float(price)
            return prices

        tasas = 41
        meses = get_meses()

        log(f"Scrapeando {len(meses)} meses IDA ({ORIGEN}→{DESTINO})...")
        for year, month in meses:
            prices = fetch_calendar(ORIGEN, DESTINO, year, month)
            ida_prices.update({k: v + tasas for k, v in prices.items()})
            time.sleep(0.3)

        log(f"Scrapeando {len(meses)} meses VUELTA ({DESTINO}→{ORIGEN})...")
        for year, month in meses:
            prices = fetch_calendar(DESTINO, ORIGEN, year, month)
            vuelta_prices.update({k: v + tasas for k, v in prices.items()})
            time.sleep(0.3)

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

def enviar_resultados_telegram(combos, asunto):
    if not combos:
        send_telegram("⚠️ Level scraper corrió pero no encontró combinaciones.")
        return

    best = combos[0]
    lines = [
        f"✈️ LEVEL ALERT",
        f"📧 {asunto}",
        f"",
        f"🏆 MEJOR COMBO:",
        f"  IDA:    {best['ida']}",
        f"  VUELTA: {best['vuelta']}",
        f"  Noches: {best['noches']}",
        f"  Total:  ${best['total']} USD",
        f"",
        f"📊 TOP {min(TOP_N, len(combos))} COMBOS:",
    ]
    for i, c in enumerate(combos[:TOP_N], 1):
        lines.append(f"  {i:>2}. {c['ida']} → {c['vuelta']} ({c['noches']}n) = ${c['total']}")

    send_telegram("\n".join(lines))

# ─── MAIN ─────────────────────────────────────────────────────────
def main():
    print("Revisando Gmail...")
    service = get_gmail_service()
    mensajes = buscar_correos_level(service)

    if not mensajes:
        print("Sin correos nuevos de Level.")
        return

    print(f"{len(mensajes)} correo(s) de Level encontrado(s).")

    # Obtener asunto
    msg = service.users().messages().get(
        userId="me", id=mensajes[0]["id"], format="metadata",
        metadataHeaders=["Subject"]
    ).execute()
    asunto = ""
    for header in msg["payload"]["headers"]:
        if header["name"] == "Subject":
            asunto = header["value"]
            break
    print(f"Asunto: {asunto}")

    # 1. LLAMAR PRIMERO
    hacer_llamada(asunto)

    # 2. MARCAR COMO LEÍDOS
    for m in mensajes:
        marcar_leido(service, m["id"])

    # 3. SCRAPEAR PRECIOS
    log("Iniciando scraper de precios...")
    try:
        ida, vuelta = scrape_precios()
        log(f"IDA: {len(ida)} días | VUELTA: {len(vuelta)} días")
        combos = find_combos(ida, vuelta)
        log(f"{len(combos)} combinaciones encontradas")

        # 4. ENVIAR RESULTADOS POR TELEGRAM
        enviar_resultados_telegram(combos, asunto)

    except Exception as e:
        log(f"Error en scraper: {e}")
        send_telegram(f"⚠️ Level scraper falló: {e}")

if __name__ == "__main__":
    main()
