"""SMS gateways.

Two providers ship with the app:

* ``test``           records messages as "simulated" and sends nothing. Use it to try
                     the system before buying SMS credit.
* ``africastalking`` sends through Africa's Talking (https://africastalking.com),
                     the most widely used bulk SMS gateway in Kenya.

Another gateway can be added by writing a class with the same ``send`` method and
registering it in ``get_provider``.
"""
import logging
import uuid
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

# Africa's Talking per-recipient status codes.
AT_SUCCESS = {100: "Processed", 101: "Sent", 102: "Queued"}
AT_ERRORS = {
    401: "Risk hold on the account",
    402: "Sender ID not approved for this account",
    403: "Invalid phone number",
    404: "Unsupported number type",
    405: "Insufficient SMS balance",
    406: "Recipient has blocked promotional SMS (DND)",
    407: "Could not route the message",
    409: "Recipient has blocked promotional SMS (DND)",
    500: "Africa's Talking internal error",
    501: "Gateway error",
    502: "Rejected by the network",
}


@dataclass
class SendResult:
    status: str               # 'sent', 'failed' or 'simulated'
    provider_id: str = None
    cost: str = None
    error: str = None


class TestProvider:
    name = "test"
    label = "Test mode (nothing is sent)"
    live = False

    def send(self, phone, message):
        return SendResult("simulated", provider_id="TEST-" + uuid.uuid4().hex[:10], cost="KES 0")

    def balance(self):
        return None


class AfricasTalkingProvider:
    name = "africastalking"
    live = True

    def __init__(self, username, api_key, sender_id=None, environment="live"):
        self.username = (username or "").strip()
        self.api_key = (api_key or "").strip()
        self.sender_id = (sender_id or "").strip() or None
        sandbox = environment == "sandbox" or self.username == "sandbox"
        host = "api.sandbox.africastalking.com" if sandbox else "api.africastalking.com"
        self.base = f"https://{host}/version1"
        self.label = "Africa's Talking" + (" sandbox" if sandbox else "")
        self.live = not sandbox

    @property
    def headers(self):
        return {"apiKey": self.api_key, "Accept": "application/json"}

    def send(self, phone, message):
        if not self.username or not self.api_key:
            return SendResult("failed", error="Africa's Talking username or API key is missing in .env")
        data = {"username": self.username, "to": phone, "message": message}
        if self.sender_id:
            data["from"] = self.sender_id
        try:
            r = requests.post(f"{self.base}/messaging", data=data, headers=self.headers, timeout=30)
        except requests.RequestException as exc:
            return SendResult("failed", error=f"No connection to Africa's Talking: {exc.__class__.__name__}")
        if r.status_code >= 400:
            return SendResult("failed", error=f"Africa's Talking replied {r.status_code}: {r.text[:180]}")
        try:
            payload = r.json()["SMSMessageData"]
        except (ValueError, KeyError):
            return SendResult("failed", error=f"Unexpected reply: {r.text[:180]}")
        recipients = payload.get("Recipients") or []
        if not recipients:
            return SendResult("failed", error=payload.get("Message") or "Message was not accepted")
        rc = recipients[0]
        code = int(rc.get("statusCode") or 0)
        if code in AT_SUCCESS:
            return SendResult("sent", provider_id=rc.get("messageId"), cost=rc.get("cost"))
        return SendResult("failed", error=AT_ERRORS.get(code, rc.get("status") or f"Status {code}"))

    def balance(self):
        if not self.username or not self.api_key:
            return None
        try:
            r = requests.get(f"{self.base}/user", params={"username": self.username},
                             headers=self.headers, timeout=15)
            r.raise_for_status()
            return r.json()["UserData"]["balance"]
        except Exception as exc:  # noqa: BLE001 - balance is informational only
            log.info("Could not read SMS balance: %s", exc)
            return None


def get_provider(config):
    name = (config.get("SMS_PROVIDER") or "test").strip().lower()
    if name in ("africastalking", "africas_talking", "at"):
        return AfricasTalkingProvider(
            config.get("AT_USERNAME"),
            config.get("AT_API_KEY"),
            config.get("AT_SENDER_ID"),
            (config.get("AT_ENVIRONMENT") or "live").strip().lower(),
        )
    return TestProvider()
