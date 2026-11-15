from tingmu.language import learning_text, normalize_cues
from tingmu.models import Cue, LanguagePair, Token


def test_cantonese_script_is_traditional_without_inventing_particles():
    assert learning_text("佢已经食咗饭喇。", LanguagePair()) == "佢已經食咗飯喇。"
    assert learning_text("喎啫㗎嘅咗啦", LanguagePair()) == "喎啫㗎嘅咗啦"
    assert learning_text("I have eaten.", LanguagePair(source="en", target="en")) == "I have eaten."


def test_conversion_preserves_raw_timing_and_simplified_explanation():
    cue = Cue("1", 1, 2, "食饭", original_text="食饭", translation="吃饭", tokens=[Token("食饭", "sik6 faan6")])
    out = normalize_cues([cue], LanguagePair())[0]
    assert out.text == "食飯"
    assert out.original_text == "食饭"
    assert out.translation == "吃饭"
    assert out.tokens[0].text == "食飯"
    assert (out.id, out.start, out.end) == (cue.id, cue.start, cue.end)


def test_cantonese_copula_spelling_does_not_become_binding_verb():
    assert learning_text("系咪仲系好想报仇先？梗系想啦，但系唔一定嘅。", LanguagePair()) == "係咪仲係好想報仇先？梗係想啦，但係唔一定嘅。"
    assert learning_text("系统系列关系，系好鞋带。", LanguagePair()) == "系統系列關係，繫好鞋帶。"
    assert learning_text("但系统未启动", LanguagePair()).startswith("但系統")
