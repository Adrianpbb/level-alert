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

# ─── AUTENTICACIÓN GMAIL ──────────────────────────────────────────
def get_gmail_service():
    creds = None

    # En GitHub Actions: reconstruir token.pickle desde secret GMAIL_TOKEN2 (base64)
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

# ─── BUSCAR CORREOS DE LEVEL ──────────────────────────────────────
def buscar_correos_level(service):
    query = " OR ".join([
        "from:no-reply@communications.flylevel.com",
        "from:flylevel.com subject:(Vuelos desde)",
        "from:flylevel.com (\"US$ 9\" OR \"9 euros\")",
    ]) + " is:unread"

    print(f"Query: {query}")

    result = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=5
    ).execute()

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
