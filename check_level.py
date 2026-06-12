import os
import base64
import pickle
from twilio.rest import Client
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ─── CONFIGURACIÓN ───────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM        = os.environ.get("TWILIO_FROM")
TWILIO_TO          = os.environ.get("TWILIO_TO")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# ─── PALABRAS CLAVE DE PRECIO (filtro secundario opcional) ────────
KEYWORDS_PRECIO = [
    "US$ 9", "US$9", "USD 9", "USD9",
    "€9", "9€", "9 euros", "EUR 9",
    "desde 9", "desde US$",
    "vuelos desde",
]

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

# ─── OBTENER ID DE ETIQUETA ───────────────────────────────────────
def get_label_id(service, nombre_etiqueta="LEVEL"):
    resultado = service.users().labels().list(userId="me").execute()
    for label in resultado.get("labels", []):
        if label["name"].upper() == nombre_etiqueta.upper():
            return label["id"]
    return None

# ─── BUSCAR CORREOS NO LEÍDOS CON ETIQUETA LEVEL ─────────────────
def buscar_correos_level(service):
    label_id = get_label_id(service, "LEVEL")

    if not label_id:
        print("⚠️  Etiqueta LEVEL no encontrada en Gmail. Usando búsqueda por remitente como fallback.")
        query = "from:flylevel.com is:unread"
    else:
        print(f"✅ Etiqueta LEVEL encontrada: {label_id}")
        query = "is:unread"

    print(f"Query: {query}")

    kwargs = {
        "userId": "me",
        "q": query,
        "maxResults": 10
    }
    if label_id:
        kwargs["labelIds"] = [label_id, "UNREAD"]
        kwargs.pop("q")  # con labelIds no necesitamos q para el unread

    result = service.users().messages().list(**kwargs).execute()
    return result.get("messages", [])

# ─── MARCAR COMO LEÍDO ───────────────────────────────────────────
def marcar_leido(service, msg_id):
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"removeLabelIds": ["UNREAD"]}
    ).execute()

# ─── LLAMADA DIRECTA VIA TWILIO ───────────────────────────────────
def hacer_llamada(asunto=""):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    call = client.calls.create(
        to=TWILIO_TO,
        from_=TWILIO_FROM,
        twiml='<Response><Say language="es-MX">Tienes un correo nuevo de Level. Revisa tu Gmail.</Say></Response>'
    )
    print(f"Llamada iniciada: {call.sid}")
    return call.sid

# ─── MAIN ─────────────────────────────────────────────────────────
def main():
    print("Revisando Gmail...")
    service = get_gmail_service()
    mensajes = buscar_correos_level(service)

    if not mensajes:
        print("Sin correos nuevos de Level.")
        return

    print(f"{len(mensajes)} correo(s) de Level encontrado(s). Llamando...")

    # Obtener asunto del primer correo
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
    hacer_llamada(asunto)

    # Marcar todos como leídos para no volver a alertar
    for m in mensajes:
        marcar_leido(service, m["id"])

if __name__ == "__main__":
    main()
