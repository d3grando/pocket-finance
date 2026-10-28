"""Clarify buttons must differ: options «<whole phrase> на Карта» used to be cut to the same
60-char label."""
from app.services.ai.cards import option_labels, unclear_card

PHRASE = "с сейфа перевел 20.2552 USDT на покупку лицензии майнкрафт"


def test_long_shared_prefix_shows_distinct_tail_with_connector():
    opts = [f"{PHRASE} на {a}" for a in ("Карта", "Наличные", "Кошелёк", "Копилка")]
    assert option_labels(opts) == ["на Карта", "на Наличные", "на Кошелёк", "на Копилка"]


def test_tail_sharing_first_letters_cuts_at_word_boundary():
    opts = [f"{PHRASE} с Наличные", f"{PHRASE} с Налоги"]
    assert option_labels(opts) == ["с Наличные", "с Налоги"]


def test_short_or_different_options_stay_as_they_are():
    opts = ["кофе 85 000 ₫", "кофе 85 ₫"]
    assert option_labels(opts) == opts
    assert option_labels(["одна фраза"]) == ["одна фраза"]


def test_card_buttons_keep_full_text_to_send():
    opts = [f"{PHRASE} на {a}" for a in ("Карта", "Кошелёк")]
    card = unclear_card("ambiguous_target", "На какой счёт перевод?", opts, input_text=PHRASE)
    sent = [b for b in card.buttons if b.action and b.action.kind == "send_text"]
    assert [b.label for b in sent] == ["на Карта", "на Кошелёк"]
    assert [b.action.text for b in sent] == opts
