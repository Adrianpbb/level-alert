"""
ICETEX Scraper - Beca Colombia Extranjeros 2026-2
Revisa si ya se publicaron los resultados y avisa por Telegram.
"""
import os
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
URL       = "https://web.icetex.gov.co/becas/beca-colombia-extranjeros"

TEXTO_BUSCADO = "Resultados Convocatoria 2026-2"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=30)
        print(f"Telegram: {r.status_code}")
    except Exception as e:
        print(f"Error Telegram: {e}")

def main():
    print("Revisando página ICETEX...")
    r = requests.get(URL, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    print(f"Status: {r.status_code}")

    if r.status_code != 200:
        print(f"Error al cargar la página: {r.status_code}")
        return

    soup = BeautifulSoup(r.text, "html.parser")
    texto_pagina = soup.get_text()

    if TEXTO_BUSCADO in texto_pagina:
        print("✅ ¡Resultados publicados!")

        # Buscar links de PDF cerca de esa sección
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            texto = a.get_text(strip=True)
            if "seleccionad" in texto.lower() and ".pdf" in href.lower():
                links.append(f"{texto}: {href}")

        msg = "🎓 ¡RESULTADOS BECA COLOMBIA 2026-2 PUBLICADOS!\n\n"
        msg += "\n".join(links) if links else f"Revisa la página: {URL}"

        send_telegram(msg)
    else:
        print("Sin resultados aún.")

if __name__ == "__main__":
    main()
