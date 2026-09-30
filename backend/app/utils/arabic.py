import re
import unicodedata

DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

def normalize_for_search(text: str) -> str:
    """Derived search representation only; never overwrite the source text."""
    text = unicodedata.normalize("NFKC", text).translate(DIGITS).casefold()
    text = "".join(c for c in text if unicodedata.category(c) not in {"Mn", "Cf"} and c != "ـ")
    text = text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي"}))
    return re.sub(r"\s+", " ", text).strip()
