from __future__ import annotations

import re


_INTERNAL_MARKERS = (
    "SECTION CIBLE INTROUVABLE",
    "REPLACEMENT_TEXT",
    "AUCUN TEXTE DE REMPLACEMENT",
    "DOCUMENT_NOT_FOUND",
    "ARTICLE_NOT_FOUND",
    "AMBIGUOUS_REFERENCE",
    "AMBIGUOUS_VERSION",
    "SOURCE_IDENTITY_MISMATCH",
    "INVALID_REFERENCE_QUERY",
    "NO_RELIABLE_SOURCE",
)


def humanize_exact_lookup(status: str, language: str = "fr") -> str:
    if status == "FOUND":
        return {
            "fr": "Référence exacte trouvée. Le texte original est disponible dans le panneau Sources.",
            "ar": "تم العثور على المرجع الدقيق. النص الأصلي متاح في لوحة المصادر.",
            "en": "The exact reference was found. The original text is available in the Sources panel.",
        }[language]
    if status in {"AMBIGUOUS", "MULTIPLE_MATCHES"} or status.startswith("AMBIGUOUS_"):
        return {
            "fr": "Plusieurs textes peuvent correspondre à cette référence. Précisez le code ou l’intitulé du texte pour que je choisisse le bon.",
            "ar": "قد يطابق هذا المرجع عدة نصوص. يرجى تحديد المجلة أو عنوان النص.",
            "en": "Several texts may match this reference. Please specify the code or title.",
        }[language]
    return {
        "fr": "Je n’ai pas retrouvé cette référence dans les sources disponibles. Vous pouvez préciser le code, le numéro d’article ou l’intitulé du texte.",
        "ar": "لم أعثر على هذا المرجع في المصادر المتاحة. يمكن تحديد المجلة أو رقم الفصل أو عنوان النص.",
        "en": "I could not find this reference in the available sources. Please specify the code, article number, or title.",
    }[language]


def humanize_draft_failure(error: str | None, instruction: str, language: str = "fr") -> str:
    folded = (error or "").casefold()
    request = instruction.casefold()
    absent = "section cible introuvable" in folded or "target" in folded and "not found" in folded
    deletion = any(word in request for word in ("supprime", "retire", "enlève", "enleve", "delete", "remove"))
    if absent and deletion:
        return {
            "fr": "Cet argument n’apparaît pas actuellement dans le projet. Si vous visiez un autre passage, indiquez-moi simplement son idée principale.",
            "ar": "هذه الحجة غير موجودة حاليًا في المشروع. إذا كنت تقصد مقطعًا آخر، اذكر فكرته الرئيسية.",
            "en": "That argument does not currently appear in the draft. If you meant another passage, tell me its main idea.",
        }[language]
    return {
        "fr": "Je n’ai pas pu identifier avec assez de certitude le passage à modifier. Pouvez-vous préciser son idée principale ou me citer quelques mots du passage ?",
        "ar": "لم أتمكن من تحديد المقطع المطلوب تعديله بدقة كافية. يرجى ذكر فكرته الرئيسية أو بعض كلماته.",
        "en": "I could not identify the passage to edit with enough confidence. Please give its main idea or quote a few words.",
    }[language]


def sanitize_user_messages(messages: list[str], language: str = "fr") -> list[str]:
    result: list[str] = []
    for message in messages:
        if not message:
            continue
        upper = message.upper()
        if any(marker in upper for marker in _INTERNAL_MARKERS):
            replacement = {
                "fr": "Les sources disponibles ne permettent pas encore de confirmer ce point.",
                "ar": "لا تسمح المصادر المتاحة بعد بتأكيد هذه النقطة.",
                "en": "The available sources do not yet confirm this point.",
            }[language]
            if replacement not in result:
                result.append(replacement)
            continue
        clean = re.sub(r"\s+", " ", str(message)).strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def contains_internal_marker(value: str) -> bool:
    upper = value.upper()
    return any(marker in upper for marker in _INTERNAL_MARKERS)
