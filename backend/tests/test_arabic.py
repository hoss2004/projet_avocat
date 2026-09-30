from app.utils.arabic import normalize_for_search
from app.services.language.detection import detect_language

def test_normalization_preserves_input():
    original = "إِجْرَاءَات ـ الفصل ٤٠٣"
    assert normalize_for_search(original) == "اجراءات الفصل 403"
    assert original == "إِجْرَاءَات ـ الفصل ٤٠٣"

def test_presentation_forms():
    assert normalize_for_search("ﺍﻟﻔﺼﻞ ۱۰") == "الفصل 10"

def test_detection():
    assert detect_language("الفصل الأول من مجلة الاختبار") == "ar"
    assert detect_language("Le droit et les documents du dossier") == "fr"
    assert detect_language("The law and the documents") == "en"
    assert detect_language("123") == "und"
