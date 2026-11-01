import logging

import pytest

from app.memory.extract import Proposal
from app.memory.filter import filter_proposals, sensitivity


def add(content, kind="fact"):
    return Proposal("add", content, kind)


def delete(content):
    return Proposal("delete", content)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("The owner's card number is 4111 1111 1111 1111.", "card"),
        ("The owner's card is 4111-1111-1111-1111.", "card"),
        ("Card 4111111111111111 expires in May.", "card"),
        ("The owner's Amex is 3782 822463 10005.", "card"),
        ("On 3 March at 10:00 the owner paid with 5500005555555559.", "card"),
        ("The owner's number is 1234 5678 9012 3456.", "long_number"),
        ("The account number is 12345678901234.", "long_number"),
        ("The reference is 123456789012345678901234.", "long_number"),
        ("The owner's IBAN is PT50 0002 0123 1234 5678 9015 4.", "iban"),
        ("The owner banks at DE89370400440532013000.", "iban"),
        ("The owner's account is GB29 NWBK 6016 1331 9268 19.", "iban"),
        ("The wifi password is sunflower.", "secret_word"),
        ("The owner's PIN is 4821.", "secret_word"),
        ("Their passcode is swordfish.", "secret_word"),
        ("The owner's CVV is on the back.", "secret_word"),
        ("The API key is in the shed.", "secret_word"),
        ("The recovery code is in the drawer.", "secret_word"),
        ("The owner keeps a secret about the surprise.", "secret_word"),
        ("The token is sk-abc123def456ghi789jkl012.", "key"),
        ("The hash is a3f5c9d1e7b24680a3f5c9d1e7b24680.", "key"),
        ("The owner's passport number is X1234567.", "identifier"),
        ("The owner's NIF is 123456789.", "identifier"),
        ("The social security number is 123-45-6789.", "identifier"),
        ("The driver's licence is AB1234567.", "identifier"),
    ],
)
def test_a_secret_is_named_by_the_rule_that_caught_it(text, rule):
    assert sensitivity(text) == rule


@pytest.mark.parametrize(
    "text",
    [
        "The owner lives in Porto.",
        "Dentist appointment on 3 March 2027 at 10:00.",
        "The owner takes the 8:15 train to work.",
        "The owner's phone number is +351 912 345 678.",
        "The owner was born on 1990-05-09.",
        "The owner pays 1,250 euros in rent.",
        "Flight TP1234 on 12 December.",
        "The owner's postcode is 4000-322.",
        "The owner is 35 years old.",
        "The owner's sister Inês is allergic to nuts.",
        "The owner uses 1Password.",
        "The owner is vegetarian.",
        "The owner's favourite number is 7.",
        "Order 20261028 was delivered on Friday.",
        "The owner's passport expires in 2029.",
        "The owner's flat is number 4021B.",
        "The owner runs 10 km in 52 minutes.",
        "The owner works at Acme Logistics in Lisbon.",
        "The owner's license plate is AB-12-CD.",
    ],
)
def test_an_ordinary_memory_passes(text):
    assert sensitivity(text) is None


def test_the_filter_leans_towards_dropping():
    # Known false positives, kept on purpose: a lost harmless fact is cheaper than a stored secret
    assert sensitivity("The owner likes pin-up art.") == "secret_word"
    assert sensitivity("The owner is bad at remembering passwords.") == "secret_word"


def test_a_card_rule_needs_the_whole_run_not_part_of_a_longer_one():
    assert sensitivity("Digits 123456789012 are not a card.") is None  # twelve
    assert sensitivity("Digits 1234567890123 are a long number.") == "long_number"  # thirteen


def test_a_valid_card_is_called_a_card_and_an_invalid_one_a_long_number():
    assert sensitivity("4111111111111111") == "card"
    assert sensitivity("4111111111111112") == "long_number"


def test_an_identifier_word_alone_is_not_enough_and_neither_is_a_number_alone():
    assert sensitivity("The owner renewed their passport.") is None
    assert sensitivity("The owner's code is 1234567.") is None


def test_a_secret_beside_a_harmless_fact_drops_that_whole_proposal():
    assert sensitivity("The owner flies to Rome and the PIN is 4821.") == "secret_word"


# filter_proposals -----------------------------------------------------------------------------


def test_adds_that_look_sensitive_are_dropped_and_the_rest_kept_in_order():
    first, last = add("The owner lives in Porto."), add("The owner has a cat.")
    secret = add("The owner's card is 4111 1111 1111 1111.")

    result = filter_proposals([first, secret, last])

    assert result.kept == (first, last)
    assert result.dropped == ("card",)


def test_a_delete_is_never_dropped_even_when_it_names_a_secret():
    forget = delete("The owner's card number 4111 1111 1111 1111.")

    assert filter_proposals([forget]).kept == (forget,)


def test_each_dropped_proposal_is_reported_by_its_rule():
    result = filter_proposals([add("PIN is 1234"), add("passport X1234567"), add("fine")])

    assert result.dropped == ("secret_word", "identifier")
    assert len(result.kept) == 1


def test_the_result_and_the_log_never_hold_the_text_that_was_dropped(caplog):
    secret = add("The owner's card is 4111 1111 1111 1111 and the PIN is 4821.")

    with caplog.at_level(logging.INFO, logger="app.memory.filter"):
        result = filter_proposals([secret])

    assert "4111" not in repr(result) and "4821" not in repr(result)
    assert "4111" not in caplog.text and "4821" not in caplog.text
    assert "dropped 1 proposal(s): card" in caplog.text


def test_nothing_dropped_logs_nothing(caplog):
    with caplog.at_level(logging.INFO, logger="app.memory.filter"):
        filter_proposals([add("The owner has a cat.")])

    assert caplog.text == ""


def test_no_proposals_is_nothing_filtered():
    assert filter_proposals([]).kept == () and filter_proposals([]).dropped == ()
