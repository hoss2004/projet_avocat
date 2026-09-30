import unicodedata

def detect_language(text: str) -> str:
    """Script-based detection; Latin text is undetermined unless FR/EN markers exist."""
    normalized = unicodedata.normalize("NFKC", text)
    letters = [c for c in normalized if c.isalpha()]
    if not letters:
        return "und"
    arabic = sum("ARABIC" in unicodedata.name(c, "") for c in letters)
    if arabic / len(letters) > 0.5:
        return "ar"
    words = set(normalized.lower().split())
    fr = len(words & {"le", "la", "les", "des", "du", "une", "et", "est", "droit"})
    en = len(words & {"the", "and", "of", "is", "shall", "this", "law"})
    return "fr" if fr > en else "en" if en > fr else "und"
