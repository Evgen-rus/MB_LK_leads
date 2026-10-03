import re


PHONE_FROM_SEVEN = re.compile(r"(?<!\d)\+?7(?:[\s()\-]*\d){10}(?!\d)")


def normalize_phone(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    first_from_seven = PHONE_FROM_SEVEN.search(text)
    digits = re.sub(r"\D+", "", first_from_seven.group()) if first_from_seven else re.sub(r"\D+", "", text)
    if len(digits) == 11 and digits.startswith("8"):
        return "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return digits
    if len(digits) == 10:
        return "7" + digits
    return ""
