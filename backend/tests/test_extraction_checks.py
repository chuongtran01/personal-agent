from datetime import UTC, datetime

from extraction_cases import EVAL_TZ, ExtractionCase, load_all
from extraction_checks import Add, Delete, Supersede, check_case

CITY = {"key": "city", "kind": "fact", "content": "The owner lives in Lisbon.", "status": "active"}
JOB = {"key": "job", "kind": "fact", "content": "The owner works at Acme.", "status": "active"}
OLD = {"key": "old", "kind": "fact", "content": "The owner lived in Faro.", "status": "superseded"}


def write(include, **rest):
    return {
        "kind": None,
        "include": include,
        "replaces": None,
        "starts": None,
        "ends": None,
        **rest,
    }


def make(**change) -> ExtractionCase:
    row = {
        "id": "x",
        "category": "add",
        "existing": [],
        "context": [],
        "owner": ["hello"],
        "reply": None,
        "writes": [],
        "deletes": [],
        "no_changes": False,
        "must_not_store": [],
        "note": None,
        **change,
    }
    return ExtractionCase.model_validate(row)


def at(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=EVAL_TZ)


def test_the_committed_cases_are_all_failed_by_no_changes_unless_they_ask_for_none():
    from extraction_checks import check_case as check

    for cases in load_all().values():
        for case in cases:
            verdict = check(case, [])
            assert verdict.passed == case.no_changes, case.id
            assert not verdict.stored and not verdict.wrong_changes


def test_an_expected_write_passes():
    case = make(writes=[write([["penicillin"]], kind="fact")])

    assert check_case(case, [Add("fact", "The owner is allergic to Penicillin.")]).passed


def test_a_missing_write_fails_and_says_what():
    verdict = check_case(make(writes=[write([["Luna", "dog"], ["beagle"]])]), [])

    assert verdict.failures == ("missing_write:Luna|dog + beagle",)


def test_every_group_needs_one_of_its_words():
    case = make(writes=[write([["Luna"], ["beagle"]])])

    assert not check_case(case, [Add("fact", "The owner has a dog called Luna.")]).passed
    assert check_case(case, [Add("fact", "Luna is a beagle.")]).passed


def test_the_wrong_kind_fails():
    case = make(writes=[write([["x"]], kind="event")])

    assert not check_case(case, [Add("fact", "x")]).passed


def test_a_kind_outside_the_four_fails_even_when_the_case_does_not_ask():
    verdict = check_case(make(writes=[write([["x"]])]), [Add("mood", "x")])

    assert "unknown_kind:mood" in verdict.failures


def test_an_extra_write_fails():
    case = make(writes=[write([["Miso"]])])

    verdict = check_case(
        case, [Add("fact", "Miso is a cat."), Add("fact", "The owner likes cats.")]
    )

    assert verdict.failures == ("unexpected_write:The owner likes cats.",)


def test_one_change_cannot_satisfy_two_expected_writes():
    case = make(writes=[write([["Miso"]]), write([["cat"]])])

    verdict = check_case(case, [Add("fact", "Miso is a cat.")])

    assert verdict.failures == ("missing_write:cat",)


def test_no_changes_fails_on_any_write_or_delete():
    case = make(no_changes=True, existing=[CITY])

    assert check_case(case, []).passed
    assert not check_case(case, [Add("fact", "anything")]).passed
    assert check_case(case, [Delete("city")]).wrong_changes == ("delete:city",)


def test_a_replacement_must_name_the_memory_and_be_a_supersede():
    case = make(existing=[CITY, JOB], writes=[write([["Porto"]], replaces="city")])

    assert check_case(case, [Supersede("city", "fact", "The owner lives in Porto.")]).passed
    assert not check_case(case, [Add("fact", "The owner lives in Porto.")]).passed
    wrong = check_case(case, [Supersede("job", "fact", "The owner lives in Porto.")])
    assert wrong.wrong_changes == ("replace:job",)
    assert "missing_write:replace city: Porto" in wrong.failures


def test_a_new_memory_is_not_a_replacement_when_none_was_asked():
    case = make(existing=[CITY], writes=[write([["Porto"]])])

    verdict = check_case(case, [Supersede("city", "fact", "The owner lives in Porto.")])

    assert verdict.wrong_changes == ("replace:city",)
    assert not verdict.passed


def test_a_replaced_memory_replaced_twice_fails():
    case = make(existing=[CITY], writes=[write([["Porto"]], replaces="city")])
    twice = [Supersede("city", "fact", "Porto."), Supersede("city", "fact", "Porto again.")]

    assert check_case(case, twice).failures == ("unexpected_write:Porto again.",)


def test_a_delete_must_hit_exactly_the_memories_meant():
    case = make(existing=[CITY, JOB], deletes=["city"])

    assert check_case(case, [Delete("city")]).passed
    assert check_case(case, []).failures == ("not_deleted:city",)
    both = check_case(case, [Delete("city"), Delete("job")])
    assert both.wrong_changes == ("delete:job",)
    assert not both.passed


def test_a_change_to_a_memory_that_is_not_active_or_not_shown_is_wrong():
    case = make(existing=[OLD, CITY], no_changes=True)

    assert check_case(case, [Delete("old")]).wrong_changes == ("delete:old (not an active memory)",)
    assert check_case(case, [Delete("ghost")]).wrong_changes == (
        "delete:ghost (not an active memory)",
    )
    assert (
        check_case(case, [Supersede("old", "fact", "x")]).wrong_changes[0].startswith("replace:old")
    )


def test_a_forbidden_word_in_a_new_memory_is_a_stored_failure():
    case = make(no_changes=True, must_not_store=["4111", "PIN"])

    verdict = check_case(case, [Add("fact", "The owner's card is 4111 and the pin is 4821.")])

    assert verdict.stored == ("4111", "PIN")
    assert not verdict.passed


def test_a_forbidden_word_is_matched_after_flattening():
    case = make(no_changes=True, must_not_store=["Inês"])

    assert check_case(case, [Add("fact", "INÊS is a sister.")]).stored == ("Inês",)


def test_a_card_like_number_is_caught_without_the_case_asking():
    for text in (
        "Card 4111 1111 1111 1111.",
        "Card 4111-1111-1111-1111",
        "number 4111111111111111",
    ):
        verdict = check_case(make(writes=[write([["card"]])]), [Add("fact", text)])
        assert verdict.stored == ("card-like number",), text


def test_ordinary_numbers_are_not_card_like():
    case = make(writes=[write([["x"]])])

    for text in ("x 8:15 train, platform 12.", "x phone 912 345 678.", "x born 1990-05-09."):
        assert check_case(case, [Add("fact", text)]).passed, text


def test_a_delete_does_not_count_toward_stored_text():
    case = make(existing=[CITY], deletes=["city"], must_not_store=["Lisbon"])

    assert check_case(case, [Delete("city")]).passed


def test_an_event_start_is_read_in_the_owners_time_zone():
    case = make(writes=[write([["dentist"]], starts="2027-03-03")])

    assert check_case(case, [Add("event", "dentist", at(2027, 3, 3, 9))]).passed
    assert not check_case(case, [Add("event", "dentist", at(2027, 3, 2, 23))]).passed
    assert not check_case(case, [Add("event", "dentist")]).passed
    assert not check_case(case, [Add("event", "dentist", at(2027, 3, 4))]).passed


def test_a_start_late_in_utc_is_the_next_day_in_lisbon_summer_time():
    case = make(writes=[write([["dinner"]], starts="2027-07-02")])
    late_utc = datetime(2027, 7, 1, 23, 30, tzinfo=UTC)  # 00:30 on the 2nd in Lisbon

    assert check_case(case, [Add("event", "dinner", late_utc)]).passed
    assert not check_case(
        make(writes=[write([["dinner"]], starts="2027-07-01")]), [Add("event", "dinner", late_utc)]
    ).passed


def test_an_event_end_may_be_the_last_day_or_the_start_of_the_next():
    case = make(writes=[write([["Berlin"]], starts="2026-12-12", ends="2026-12-15")])
    start = at(2026, 12, 12)

    assert check_case(case, [Add("event", "Berlin", start, at(2026, 12, 15, 23))]).passed
    assert check_case(case, [Add("event", "Berlin", start, at(2026, 12, 16))]).passed
    assert not check_case(case, [Add("event", "Berlin", start, at(2026, 12, 17))]).passed
    assert not check_case(case, [Add("event", "Berlin", start)]).passed


def test_a_run_with_several_failures_lists_each():
    case = make(
        existing=[CITY], deletes=["city"], writes=[write([["Rome"]])], must_not_store=["X1"]
    )

    verdict = check_case(case, [Add("fact", "X1 trip")])

    assert verdict.failures == (
        "not_deleted:city",
        "missing_write:Rome",
        "unexpected_write:X1 trip",
        "stored:X1",
    )
