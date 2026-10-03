import re
from urllib.parse import urlparse


NO_DATA = "нет данных"


def normalize_source(value: object) -> str:
    if value is None:
        return NO_DATA
    text = str(value).strip()
    if not text or text == "66":
        return NO_DATA
    return text


def extract_lkid_from_source(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if "_" not in text:
        return ""
    tail = text.rsplit("_", 1)[-1]
    match = re.fullmatch(r"\d{4,}", tail)
    return tail if match else ""


def extract_domain(source: object) -> str:
    text = normalize_source(source)
    if text == NO_DATA:
        return NO_DATA
    first = text.split("_", 1)[0].strip()
    parsed = urlparse(first if "://" in first else f"//{first}")
    host = parsed.netloc or parsed.path
    host = host.lower().replace("www.", "").strip("/")
    return host or text


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\n\r\t]+', "_", value).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "project"
