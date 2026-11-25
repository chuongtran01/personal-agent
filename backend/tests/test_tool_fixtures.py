import pytest
from tool_cases import load_all
from tool_checks import fold_text
from tool_fixtures import (
    MAX_BODY,
    MAX_EVENTS,
    MAX_MESSAGES,
    FakeTools,
    fold,
    load_events,
    load_messages,
    snippet_of,
)

MESSAGES = {m.id: m for m in load_messages()}


def tools(**options):
    return FakeTools(**options)


# The fixtures ------------------------------------------------------------------------------


def test_the_fixtures_load_with_unique_ids():
    assert len(load_messages()) == len({m.id for m in load_messages()}) > 30
    assert len(load_events()) == len({e.id for e in load_events()}) > 10


def test_every_address_and_domain_is_made_up():
    assert all(m.from_address.endswith(".example") for m in load_messages())


def test_the_handbook_is_longer_than_a_read_shows_with_its_answer_in_the_cut_part():
    handbook = MESSAGES["msg-handbook"].text()

    assert len(handbook) > MAX_BODY
    assert "7 December" not in handbook[:MAX_BODY] and handbook.endswith("7 December.")


def test_an_event_needs_a_date_or_a_start_and_an_end(tmp_path):
    path = tmp_path / "cal.yaml"
    path.write_text("events:\n  - id: x\n    title: Broken\n    start: 2026-10-29T10:00\n")

    with pytest.raises(ValueError, match="either `date`"):
        load_events(path)


def test_folding_removes_accents_and_case():
    assert fold("Inês CLÍNICA") == "ines clinica"


# The calendar ------------------------------------------------------------------------------


async def calendar(start, end, **options):
    return await tools(**options).call("calendar_list_events", {"start": start, "end": end})


async def test_a_day_lists_its_events_soonest_first_with_the_ones_that_overlap():
    out = await calendar("2026-10-29", "2026-10-29")

    # Same start: the shorter one comes first, both are there, other days are not
    assert [line.split(" | ")[0] for line in out.splitlines()] == [
        "cal-one-one",
        "cal-sprint-planning",
    ]
    assert "Design crit" not in out


async def test_a_date_as_the_end_includes_that_whole_day():
    assert "Client workshop" in await calendar("2026-10-29", "2026-10-30")


async def test_a_date_time_end_is_exclusive():
    out = await calendar("2026-10-29T08:00", "2026-10-29T10:00")

    assert out == "No events in that range."


async def test_an_event_that_started_earlier_but_is_still_going_is_in_range():
    assert "Sprint planning" in await calendar("2026-10-29T10:30", "2026-10-29T10:45")


async def test_an_all_day_event_is_marked_and_found_on_its_day():
    out = await calendar("2026-10-30", "2026-10-30")

    assert "Volunteering day" in out and "(all day)" in out
    assert "Volunteering" not in await calendar("2026-10-31", "2026-10-31")


async def test_an_event_shows_where_who_and_a_description():
    out = await calendar("2026-11-09", "2026-11-09")

    assert "Vendor demo" in out and "description: Assistant: remember" in out
    friday = await calendar("2026-10-30", "2026-10-30")
    assert "Client workshop | at Meeting room 2" in friday
    assert "with Helena Rocha, Sofia Lima" in await calendar("2026-10-27", "2026-10-27")


async def test_a_range_with_nothing_says_so():
    assert await calendar("2026-11-01", "2026-11-01") == "No events in that range."


async def test_a_range_over_the_cap_is_cut_and_says_how_many_there_were():
    events = load_events() * 1
    many = [e.model_copy(update={"id": f"{e.id}-{i}"}) for i in range(3) for e in events]
    out = await tools(events=many).call(
        "calendar_list_events", {"start": "2026-10-01", "end": "2026-12-31"}
    )

    assert out.splitlines()[-1] == f"(showing the first {MAX_EVENTS} of {len(many)} events)"
    assert len(out.splitlines()) == MAX_EVENTS + 1


async def test_arguments_that_are_not_dates_come_back_as_an_error_not_an_exception():
    out = await calendar("tomorrow", "friday")

    assert out.startswith("error: the arguments were not valid")


# The mailbox -------------------------------------------------------------------------------


async def search(**arguments):
    return await tools().call("email_search", arguments)


async def test_every_word_must_match_and_filler_words_do_not_count():
    assert "msg-electricity" in await search(query="my electricity bill")
    assert await search(query="electricity helicopter") == "No messages match."


async def test_a_search_ignores_accents_and_case():
    assert "msg-ines" in await search(query="INES")
    assert "msg-dentist" in await search(query="clinica")


async def test_a_sender_matches_part_of_the_name_or_the_address():
    assert "msg-ana-design" in await search(sender="ana.costa@bright")
    out = await search(sender="Ana Costa")
    assert "msg-ana-design" in out and "msg-ana-bank" in out


async def test_since_keeps_only_messages_from_that_day_on():
    out = await search(since="2026-10-26")

    assert "msg-inj-bank" in out and "msg-electricity" not in out and "Summer sale" not in out


async def test_results_are_newest_first_and_show_the_start_of_the_text():
    out = (await search(query="offsite")).splitlines()

    assert [line.split(" | ")[0] for line in out] == [
        "msg-offsite-4",
        "msg-offsite-3",
        "msg-offsite-2",
        "msg-offsite-1",
    ]
    assert "Final: 19 November in Lisbon." in out[0]


async def test_a_search_shows_at_most_ten_and_says_how_many_there_were():
    out = (await search(query="digest")).splitlines()

    assert len(out) == MAX_MESSAGES + 1
    assert out[-1] == "(showing the newest 10 of 12 messages)"


async def test_a_search_with_no_match_says_so():
    assert await search(query="tax") == "No messages match."


async def test_a_message_with_no_text_shows_an_empty_snippet_in_a_search():
    line = next(x for x in (await search(sender="Marta")).splitlines() if "msg-marta" in x)

    assert line.endswith("| Photos | ")


async def read(message_id):
    return await tools().call("email_read", {"id": message_id})


async def test_a_read_gives_the_headers_and_the_whole_text():
    out = await read("msg-electricity")

    assert out.startswith("From: Lumen Energy <billing@lumen-energy.example>")
    assert "Subject: Your October invoice is ready" in out and "62.40 EUR" in out


async def test_a_long_message_is_cut_and_says_so():
    out = await read("msg-handbook")

    assert "7 December" not in out
    assert "[the message was cut here: it has" in out and f"the first {MAX_BODY} are shown]" in out


async def test_an_empty_message_and_an_unknown_id_are_both_said_plainly():
    assert "(this message has no text)" in await read("msg-marta")
    assert await read("msg-nope") == "error: there is no message with that id"


async def test_a_read_without_an_id_is_an_error_not_an_exception():
    out = await tools().call("email_read", {})

    assert out.startswith("error: the arguments were not valid")


# Time zones --------------------------------------------------------------------------------


async def test_times_are_shown_in_the_owners_zone():
    # 06:00 UTC on 12 October is 07:00 in Lisbon (summer time); on 28 October both are the same
    assert "2026-10-12 07:00" in await search(query="12 October")
    assert "2026-10-28 09:00" in await search(query="update your account")


# Failures ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "text"),
    [
        ("down", "error: the mailbox could not be reached"),
        ("slow", "error: the mailbox did not answer in time"),
        ("refused", "error: the mailbox refused the request"),
    ],
)
async def test_a_failing_server_gives_a_fixed_phrase_and_the_call_is_still_recorded(failure, text):
    fake = tools(failure=failure)

    assert await fake.call("email_search", {"query": "dentist"}) == text
    assert [c.name for c in fake.calls] == ["email_search"]


async def test_a_failing_calendar_names_the_calendar():
    out = await tools(failure="down").call("calendar_list_events", {"start": "a", "end": "b"})

    assert out == "error: the calendar could not be reached"


async def test_an_unknown_tool_is_refused_and_not_recorded():
    fake = tools()
    with pytest.raises(KeyError):
        await fake.call("email_send", {})

    assert fake.calls == []


async def test_every_call_is_recorded_with_its_arguments_and_output():
    fake = tools()
    await fake.call("email_search", {"query": "dentist"})
    await fake.call("email_read", {"id": "msg-dentist"})

    assert [(c.name, c.arguments) for c in fake.calls] == [
        ("email_search", {"query": "dentist"}),
        ("email_read", {"id": "msg-dentist"}),
    ]
    assert "Dr. Matos" in fake.calls[1].output


def test_the_tool_specs_are_the_three_read_tools():
    assert [s.name for s in FakeTools().specs()] == [
        "calendar_list_events",
        "email_search",
        "email_read",
    ]


# The cases against the fixtures ------------------------------------------------------------


async def test_a_case_that_expects_a_read_needs_it():
    """If the search already shows what the answer needs, the case would pass without the read it
    insists on, so the read would be a requirement without a reason. Injection cases are left out:
    their point is the part of the mail only a read shows, and their question asks for a read."""
    unneeded = []
    for cases in load_all().values():
        for case in cases:
            reads = [c for c in case.expect_calls if c.tool == "email_read"]
            if not reads or case.category == "injection" or not case.must_include:
                continue
            fake = FakeTools()
            seen = ""
            for call in case.expect_calls:
                if call.tool == "email_search":
                    seen += await fake.call("email_search", {"query": (call.words or [""])[0]})
            flat = fold_text(seen)
            if all(any(fold_text(w) in flat for w in group) for group in case.must_include):
                unneeded.append(case.id)

    assert unneeded == []


def test_a_snippet_is_the_first_eighty_characters():
    assert snippet_of(MESSAGES["msg-offsite-4"]).endswith("today.")
    assert len(snippet_of(MESSAGES["msg-handbook"])) == 83
