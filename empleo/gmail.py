"""Gmail OAuth, envío único y conciliación positiva de mensajes."""
import base64
import hashlib
import os
from email.message import EmailMessage
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from urllib.parse import urlencode
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .transport import request_json

SCOPES = {"https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.readonly"}


def recipients_key(recipients):
    addresses = sorted({address.lower().strip() for _, address in getaddresses(recipients)})
    if len(addresses) != 2 or any("@" not in a or "\n" in a or "\r" in a for a in addresses):
        raise ValueError("Se requieren los dos destinatarios autorizados")
    return ",".join(addresses)


def body_hash(body):
    return hashlib.sha256(body.replace("\r\n", "\n").rstrip().encode()).hexdigest()


class Gmail:
    def __init__(self, access_token):
        self.headers = {"Authorization": "Bearer " + access_token}
        self.base = "https://gmail.googleapis.com/gmail/v1/users/me/"
        self.address = None

    @classmethod
    def from_environment(cls):
        names = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise RuntimeError("Faltan variables: " + ", ".join(missing))
        response = request_json("https://oauth2.googleapis.com/token", method="POST", form={
            "client_id": os.environ[names[0]], "client_secret": os.environ[names[1]],
            "refresh_token": os.environ[names[2]], "grant_type": "refresh_token"})
        scopes = set(response.get("scope", "").split())
        if not (SCOPES <= scopes or scopes & {"https://mail.google.com/",
                                             "https://www.googleapis.com/auth/gmail.modify"}):
            raise RuntimeError("OAuth debe confirmar permisos gmail.send y gmail.readonly")
        gmail = cls(response["access_token"])
        gmail.profile()  # Verificación de lectura sin enviar correo de prueba.
        return gmail

    def profile(self):
        data = request_json(self.base + "profile", headers=self.headers)
        self.address = data["emailAddress"]
        return data

    def send(self, subject, body, recipients):
        message = EmailMessage()
        if self.address is None:
            self.profile()
        message["From"] = self.address
        message["To"] = recipients_key(recipients)
        message["Subject"] = subject
        message.set_content(body)
        response = request_json(self.base + "messages/send", method="POST", headers=self.headers,
                                data={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()})
        if not response.get("id"):
            raise RuntimeError("Gmail no confirmó el identificador del envío")
        return response["id"]

    def reconcile(self, delivery):
        day = datetime.fromisoformat(delivery["local_date"]).replace(tzinfo=ZoneInfo("Europe/Madrid"))
        start, end = int(day.timestamp()), int((day + timedelta(days=1)).timestamp())
        token, matches = None, []
        while True:
            query = {"q": f"in:sent after:{start - 1} before:{end}", "maxResults": 100}
            if token:
                query["pageToken"] = token
            listing = request_json(self.base + "messages?" + urlencode(query), headers=self.headers)
            for item in listing.get("messages", []):
                data = request_json(self.base + "messages/" + item["id"] + "?format=raw",
                                    headers=self.headers)
                if not start * 1000 <= int(data.get("internalDate", 0)) < end * 1000:
                    continue
                msg = BytesParser(policy=policy.default).parsebytes(
                    base64.urlsafe_b64decode(data["raw"] + "=="))
                try:
                    same_to = recipients_key(msg.get_all("To", [])) == delivery["recipient"]
                except ValueError:
                    same_to = False
                content = msg.get_body(preferencelist=("plain",))
                if (same_to and str(msg["Subject"]) == delivery["subject"] and content
                        and body_hash(content.get_content()) == delivery["body_sha256"]):
                    matches.append(data["id"])
            token = listing.get("nextPageToken")
            if not token:
                break
        # Ninguna coincidencia NO habilita reintento; varias requieren revisión.
        return matches[0] if len(matches) == 1 else None
