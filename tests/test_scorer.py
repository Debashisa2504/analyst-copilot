from eval.scorer import answers_match, extract_numbers, normalize_answer, page_matches, score_single


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
