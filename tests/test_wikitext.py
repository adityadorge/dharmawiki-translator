from pathlib import Path
from dwtranslate.wikitext import Masker, unmask, translate_wikitext, is_translatable

SAMPLE = (Path(__file__).parent / "sample_page.wikitext").read_text(encoding="utf-8")


def test_roundtrip_identity():
    m = Masker().mask(SAMPLE)
    assert unmask(m.text, m.slots) == SAMPLE


def test_identity_translation_keeps_page():
    out, _ = translate_wikitext(SAMPLE, lambda s: s)
    assert out == SAMPLE


def fake_hi(s: str) -> str:
    # pretend-translate: uppercase visible text, keep tags
    import re
    return re.sub(r"(<[^>]+>)|([^<]+)", lambda m: m.group(1) or m.group(2).upper(), s)


def test_protected_content_untouched():
    out, stats = translate_wikitext(SAMPLE, fake_hi)
    for keep in ["{{NeedCitation}}", "__NOTOC__", "<references />", "[[Category:Ayurveda]]",
                 "[[File:Ayu.png|thumb|Diagram of Ayu]]", "<!-- editor note -->",
                 "<ref>Charaka Samhita, Sutrasthana 1.41 <nowiki>https://example.org/x</nowiki></ref>",
                 "शरीरेन्द्रियसत्त्वात्मसंयोगो", "śarīrendriyasattvātmasaṃyogo", '{| class="wikitable"']:
        assert keep in out, keep
    assert "[[Charaka Samhita (चरक संहिता)|CHARAKA SAMHITA]]" in out
    assert "[[Ayurveda|AYURVEDA]]" in out
    assert "[https://example.org/ayu EXTERNAL PAGE]" in out
    assert "=== TYPES ===" in out
    assert "* HITAYU - A BENEFICIAL LIFE" in out
    assert stats.translated > 5


def test_fallback_when_model_drops_tags():
    import re
    def bad(s):  # drops all tags on full-segment calls
        return re.sub(r"<[^>]+>", "", s).upper()
    out, stats = translate_wikitext(SAMPLE, bad)
    assert "[[Category:Ayurveda]]" in out and "<ref>" in out
    assert stats.fallback > 0


def test_link_map_points_to_translated_page():
    out, _ = translate_wikitext(SAMPLE, lambda s: s,
                                link_map_fn=lambda ts: {"Ayurveda": "Ayurveda/hi"})
    assert "[[Ayurveda/hi|Ayurveda]]" in out


def test_verse_detection():
    assert not is_translatable("शरीरेन्द्रियसत्त्वात्मसंयोगो धारि जीवितम्")
    assert not is_translatable("śarīrendriyasattvātmasaṃyogo dhāri jīvitam। nityagaścānubandhaśca")
    assert is_translatable("The union of body, senses, mind and soul is called Ayu.")
