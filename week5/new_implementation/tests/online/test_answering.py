"""Answer coverage checks accept equivalent, unambiguous date notation."""

from week5.new_implementation.online.answering import (
    _answer_mentions_date,
    _claims_sample,
)


def test_coverage_bound_in_natural_english_date():
    answer = (
        "Available attendance records run from August 3, 2026, to September 6, 2026."
    )

    assert _answer_mentions_date(answer, "2026-08-03")
    assert _answer_mentions_date(answer, "2026-09-06")
    assert not _answer_mentions_date(answer, "2026-08-04")


def test_coverage_bound_in_iso_and_day_first_dates():
    answer = "Available dates: 2026-08-03 through 6 September 2026."

    assert _answer_mentions_date(answer, "2026-08-03")
    assert _answer_mentions_date(answer, "2026-09-06")


def test_negative_sample_wording_is_not_a_sample_claim():
    assert not _claims_sample("No bounded sample was returned.")
    assert not _claims_sample("This is not a sample of the database.")
    assert _claims_sample("The answer is a sample of the database.")
