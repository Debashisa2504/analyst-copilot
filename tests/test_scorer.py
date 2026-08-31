from unittest.mock import patch

from eval.scorer import _is_bare_number, answers_match, extract_numbers, normalize_answer, page_matches, score_single


def test_basic_strip_and_lowercase():
    assert normalize_answer("**$1,577.00**") == "1577.00"


def test_remove_percent():
    assert normalize_answer("12.5%") == "12.5"


def test_parenthesized_negative():
    assert normalize_answer("$(1,034)") == "-1034"


def test_extract_numbers_multiple():
    assert extract_numbers("Revenue was $100 and cost was $(40)") == [100.0, -40.0]


def test_answers_match_numeric():
    assert answers_match("$1577.00", "1577")


def test_answers_match_wrong_number():
    assert not answers_match("$1577.00", "1500")


def test_answers_match_containment():
    assert answers_match("the answer is yes, it increased", "increased")


def test_page_matches_within_tolerance():
    assert page_matches(61, [59], page_tolerance=12)


def test_page_matches_out_of_tolerance():
    assert not page_matches(100, [59], page_tolerance=12)


def test_score_correct_and_located():
    assert score_single("1577", "1577", 60, [59]) == 1


def test_score_abstained():
    assert score_single("not found in this filing", "1577", None, [59], abstained=True) == 0


def test_score_correct_wrong_location():
    assert score_single("1577", "1577", 500, [59]) == 0


def test_score_wrong_answer():
    assert score_single("1500", "1577", 60, [59]) == -1


def test_is_bare_number_true_for_plain_numbers():
    assert _is_bare_number("$1577.00")
    assert _is_bare_number("1500")
    assert _is_bare_number("1500 million")


def test_is_bare_number_false_with_surrounding_text():
    assert not _is_bare_number("Yes, CVS Health paid dividends to common shareholders in Q2 of FY2022.")


def test_bare_number_mismatch_is_definitive_no_llm_call():
    # A wrong number with nothing else in either answer needs no semantic
    # second opinion -- and must not spend an LLM call finding that out.
    with patch("eval.scorer._llm_semantic_match") as mock_llm:
        mock_llm.side_effect = AssertionError("should not call the LLM judge for a bare-number mismatch")
        assert not answers_match("$1577.00", "1500")
        mock_llm.assert_not_called()


def test_numeric_mismatch_with_context_falls_through_to_semantic_judge():
    # An incidental digit ("Q2") must not poison the numeric tier into a
    # false "wrong" -- real surrounding text should get a semantic check.
    with patch("eval.scorer._llm_semantic_match", return_value=True) as mock_llm:
        result = answers_match(
            "Yes, CVS Health paid dividends to common shareholders in Q2 of FY2022.",
            "Yes, CVS paid a $0.55 dividend per share every quarter in FY2022",
        )
        assert result is True
        mock_llm.assert_called_once()
