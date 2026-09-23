from canon.text import norm_number, norm_subject, norm_title, sequence_marker, institution_name_key, parse_units, hash_bucket


def test_number_normalization():
    assert norm_number("018") == "18"
    assert norm_number("2301.0") == "2301"
    assert norm_number("101L") == "101L"
    assert norm_number(" 46A ") == "46A"
    assert norm_subject("c s") == "CS"


def test_title_normalization_expands_and_keeps_sequence():
    assert norm_title("Intro to Comp Sci I") == "introduction comp science 1"
    assert norm_title("Elem Stats & Probability") == "elementary statistics probability"
    assert norm_title("Calculus II") == "calculus 2"
    assert sequence_marker("General Chemistry I") == "1"
    assert sequence_marker("General Chemistry II") == "2"
    assert sequence_marker("Physics 4B") == "2"
    assert sequence_marker("General Chemistry", "1B") == "2" and sequence_marker("Calculus B") == "2"
    assert sequence_marker("Abnormal Psychology") is None


def test_institution_key_ignores_inactive_suffix():
    assert institution_name_key("SOUTHERN TECHNICAL COLLEGE (INACTIVE AS OF 6/20/15)") == institution_name_key("Southern Technical College")


def test_units_and_hash():
    assert parse_units("1-3") == (1.0, 3.0)
    assert parse_units("3.0") == (3.0, 3.0)
    assert parse_units(None) == (None, None)
    b = [hash_bucket(f"k{i}", "v1") for i in range(2000)]
    assert 0 <= min(b) and max(b) < 100 and 150 < sum(1 for x in b if x < 20) < 550
