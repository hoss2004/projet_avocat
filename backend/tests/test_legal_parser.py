from app.parsers.legal_code_parser import parse_legal_code, canonical_article_number

def test_arabic_original_offsets_and_pages():
    pages = ["TEST LAW — SYNTHETIC\nالكتاب الأول\nالباب الأول\nالفصل الأول - نص اصطناعي للاختبار فقط.\nالفصل ٢ - بداية\n", "تكملة اصطناعية.\nالفصل ٣ مكرر - TEST ARTICLE\n"]
    result = parse_legal_code(pages)
    assert [a.article_number for a in result.articles] == ["1", "2", "3 مكرر"]
    second = result.articles[1]
    assert (second.page_start, second.page_end) == (1, 2)
    assert second.chapter == "الباب الأول"
    original = "\n\f\n".join(pages)
    for a in result.articles:
        assert original[a.start_offset:a.end_offset] == a.original_text

def test_presentation_and_multiline_numbers():
    result = parse_legal_code(["TEST LAW\nﺍﻟﻔﺼﻞ۱۰ : TEST ARTICLE\nالفصل\n١١ - نص تجريبي فقط\n"])
    assert [a.article_number for a in result.articles] == ["10", "11"]
    assert result.articles[0].original_text.startswith("ﺍﻟﻔﺼﻞ")

def test_french_suffix_and_references_inside_prose():
    result = parse_legal_code(["TEST LAW\nArticle premier. Texte artificiel. Selon article 403, rien de réel.\nArticle 2 bis - TEST ARTICLE\n"])
    assert [a.article_number for a in result.articles] == ["1", "2 bis"]

def test_duplicate_numbers_are_not_overwritten():
    result = parse_legal_code(["Article 1 - TEST ARTICLE\nArticle 1 - TEST ARTICLE annexe"])
    assert len(result.articles) == 2
    assert any("répétés" in warning for warning in result.warnings)

def test_structure_does_not_bleed_into_previous_article():
    result = parse_legal_code(["Article 1 - TEST ARTICLE\nChapitre II\nArticle 2 - TEST ARTICLE"])
    assert "Chapitre" not in result.articles[0].original_text
    assert result.articles[1].chapter == "Chapitre II"

def test_exact_reference_normalization():
    assert canonical_article_number("الفصل ٤٠٣") == "403"
    assert canonical_article_number("article 403") == "403"
    assert canonical_article_number("الفصل الأول") == "1"


def test_visual_rtl_headings_keep_source_order():
    source = "TEST LAW\nالفصل األول\nنص اصطناعي\n403 الفصل\nTEST ARTICLE\n)2000 مكرر (أضيف بالقانون عدد404 الفصل\nTEST ARTICLE"
    result = parse_legal_code([source])
    assert [a.article_number for a in result.articles] == ["1", "403", "404 مكرر"]
    assert "403 الفصل" in result.articles[1].original_text
    assert any("RTL" in w for w in result.warnings)

def test_inline_rtl_reference_is_not_an_article():
    result = parse_legal_code(["Article 1 - TEST ARTICLE\nوفقا لما جاء في 403 الفصل\nTEST ARTICLE"])
    assert len(result.articles) == 1
