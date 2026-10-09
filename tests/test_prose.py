"""What the model wrote, versus what a surface should show.

The model is told to answer in plain English and mostly does, but on a summary
it reaches for markdown anyway. Those asterisks went straight into a QLabel,
so the Cockpit displayed a literal "**Personal facts:**" and the transcript
read as a wall of syntax.

Deleting the markdown is only half the fix. The structure the model reached
for is real — these pin that it is read rather than thrown away.
"""
import pytest

from core.prose import plain, sections


class TestMarkdownNeverReachesTheScreen:
    @pytest.mark.parametrize("written,shown", [
        ("**Personal facts:** Mohab", "Personal facts: Mohab"),
        ("***emphatic***", "emphatic"),
        ("__also bold__", "also bold"),
        ("*italic*", "italic"),
        ("`code`", "code"),
        ("## Heading", "Heading"),
        ("[label](https://example.com)", "label"),
    ])
    def test_the_syntax_goes_and_the_words_stay(self, written, shown):
        assert plain(written) == shown

    def test_a_whole_answer_comes_out_clean(self):
        answer = ("Here's the summary:\n\n**Academic:** two courses left\n\n"
                  "**Today:** nothing due")
        out = plain(answer)
        assert "**" not in out
        assert "Academic" in out and "two courses left" in out

    def test_bullets_become_a_bullet_rather_than_a_hyphen(self):
        assert plain("- first\n- second") == "• first\n• second"


class TestStructureIsReadNotDiscarded:
    def test_a_labelled_block_becomes_a_label_and_a_body(self):
        got = sections("**Academic:** two courses left")
        assert got == [("Academic", "two courses left")]

    def test_an_unlabelled_block_keeps_an_empty_label(self):
        got = sections("Just a sentence with no heading.")
        assert got == [("", "Just a sentence with no heading.")]

    def test_a_real_answer_splits_into_its_parts(self):
        answer = ("Here's what you've told me, Mo:\n\n"
                  "**Personal facts:** Mohab, 22, Cairo.\n\n"
                  "**Academic:** German 3 and German 4 left.\n\n"
                  "**Today:** checking on me.")
        got = sections(answer)
        assert len(got) == 4
        assert got[0][0] == ""                      # the lead-in has no label
        assert [g[0] for g in got[1:]] == ["Personal facts", "Academic", "Today"]
        assert all("**" not in body for _, body in got)

    def test_a_sentence_that_merely_contains_a_colon_is_not_a_label(self):
        # "I checked three things: mail, calendar and tasks" is prose, not a
        # heading — a label is short, and treating a whole clause as one would
        # put half a sentence in the kicker.
        got = sections("I looked at all of the following things today: mail, "
                       "calendar and tasks.")
        assert got[0][0] == ""

    @pytest.mark.parametrize("sentence", [
        "Tomorrow in Cairo: sunny, 31 degrees.",
        "Opened it: https://www.youtube.com/watch?v=abc",
        "Done: the reminder is set for 7.",
    ])
    def test_a_short_plain_lead_in_is_prose_not_a_heading(self, sentence):
        # Only a label the model marked as one is a heading. These came out as
        # a "TOMORROW IN CAIRO" kicker over "sunny, 31 degrees."
        assert sections(sentence) == [("", " ".join(plain(sentence).split()))]

    def test_a_run_of_plain_labels_is_structure(self):
        # Mo's meal plan on 09-14 was written this way, in plain spoken style.
        got = sections("Here's a day that hits it, Mo:\n\n"
                       "Breakfast: 4 eggs and toast.\n\n"
                       "Lunch: chicken and rice.\n\n"
                       "Dinner: fish and salad.")
        assert [g[0] for g in got] == ["", "Breakfast", "Lunch", "Dinner"]
        assert got[1][1] == "4 eggs and toast."

    def test_a_clock_time_is_never_a_label(self):
        got = sections("It's 2:30 PM in Cairo.\n\nWeather: sunny.\n\nTraffic: light.")
        assert [g[0] for g in got] == ["", "Weather", "Traffic"]
        assert got[0][1] == "It's 2:30 PM in Cairo."

    def test_a_bold_label_with_the_colon_outside_is_still_a_label(self):
        assert sections("**Weather**: sunny") == [("Weather", "sunny")]

    def test_bold_inside_a_sentence_is_not_a_label(self):
        got = sections("It's **really** hot today: 40 degrees.")
        assert got == [("", "It's really hot today: 40 degrees.")]

    def test_it_never_returns_none(self):
        for empty in ("", "   ", "\n\n"):
            assert sections(empty) == []
            assert plain(empty) == ""
