"""Small helpers shared across the app: time zone, phone numbers, SMS length."""
import re
from datetime import date, datetime, time, timedelta, timezone

# Kenya does not observe daylight saving, so a fixed UTC+3 offset is exact.
EAT = timezone(timedelta(hours=3), "EAT")

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SERVICES = {
    "sunday": "Sunday service",
    "thursday": "Thursday Dominion service",
    "other": "Other / special event",
}

BORN_AGAIN = {"yes": "Yes", "no": "No", "unsure": "Not sure"}

SMS_STATUS = {
    "active": "In follow-up",
    "paused": "Paused",
    "opted_out": "Opted out",
    "completed": "Follow-up complete",
}


def now_eat() -> datetime:
    return datetime.now(EAT)


def today_eat() -> date:
    return now_eat().date()


def parse_date(value, default=None):
    if not value:
        return default
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return default


def parse_hhmm(value, default="10:00") -> time:
    try:
        h, m = str(value or default).strip().split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, TypeError):
        h, m = default.split(":")
        return time(int(h), int(m))


def week_start(d: date) -> date:
    """Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


_PHONE_STRIP = re.compile(r"[\s\-().]")


def normalize_phone(raw):
    """Return a phone number in +2547XXXXXXXX form, or None if it can't be read.

    Accepts the ways Kenyan numbers are usually written on cards:
    0712 345 678, 712345678, 254712345678, +254 712 345 678.
    Other international numbers are accepted when written with a leading +.
    """
    if raw is None:
        return None
    s = _PHONE_STRIP.sub("", str(raw).strip())
    if not s:
        return None
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+"):
        digits = s[1:]
        if not digits.isdigit():
            return None
        if digits.startswith("254"):
            return "+" + digits if len(digits) == 12 and digits[3] in "17" else None
        return "+" + digits if 9 <= len(digits) <= 15 else None
    if not s.isdigit():
        return None
    if len(s) == 10 and s[0] == "0" and s[1] in "17":
        return "+254" + s[1:]
    if len(s) == 9 and s[0] in "17":
        return "+254" + s
    if len(s) == 12 and s.startswith("254") and s[3] in "17":
        return "+" + s
    return None


def display_phone(phone):
    """+254712345678 -> 0712 345 678 for Kenyan numbers."""
    if not phone:
        return ""
    if phone.startswith("+254") and len(phone) == 13:
        local = "0" + phone[4:]
        return f"{local[:4]} {local[4:7]} {local[7:]}"
    return phone


GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXT = set("^{}\\[~]|€")


def sms_parts(text: str):
    """Return (characters, segments) the way phone networks count them."""
    text = text or ""
    if all(c in GSM7 or c in GSM7_EXT for c in text):
        length = sum(2 if c in GSM7_EXT else 1 for c in text)
        single, multi = 160, 153
    else:
        length = len(text.encode("utf-16-le")) // 2  # emoji take two units each
        single, multi = 70, 67
    if length <= single:
        return length, 1 if length else 0
    return length, -(-length // multi)


def first_word_title(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "friend"
    first = name.split()[0]
    return first[:1].upper() + first[1:].lower() if first.isupper() or first.islower() else first


def tidy_name(value, max_len=120):
    """'GRACE ACHIENG' or 'grace achieng' -> 'Grace Achieng'. Mixed case is left as typed."""
    v = " ".join(str(value or "").split())[:max_len]
    if not v:
        return None
    if v.isupper() or v.islower():
        v = " ".join(w[:1].upper() + w[1:].lower() for w in v.split())
    return v


def clean_text(value, max_len=2000):
    if value is None:
        return None
    v = str(value).strip()
    return v[:max_len] if v else None


def pct(part, whole):
    return round(100 * part / whole) if whole else 0
