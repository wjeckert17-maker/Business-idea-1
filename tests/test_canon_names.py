from canon.people.names import parse_name, compatibility


def p(s):
    return parse_name(s)


def test_layouts():
    a = p("Thayer, Jane")
    assert (a.family, a.given, a.layout) == ("Thayer", "Jane", "last_first")
    b = p("Dr. Jane M. Thayer, Ph.D.")
    assert (b.family, b.given, b.middle, b.honorifics, b.suffixes) == ("Thayer", "Jane", ["M."], ["dr"], ["phd"])
    c = p("THAYER, J")
    assert c.initials_only and c.given_initial == "j" and c.family_key == "thayer"
    d = p("Ludwig van Beethoven")
    assert d.family == "van Beethoven" and d.family_key == "beethoven"
    e = p("Smith")
    assert e.layout == "single" and e.given is None


def test_compatibility_levels():
    assert compatibility(p("Thayer, Jane"), p("Jane Thayer"))[0] == "exact"
    assert compatibility(p("Thayer, Jane"), p("THAYER, J"))[0] == "strong"
    assert compatibility(p("William Smith"), p("Bill Smith"))[0] == "strong"
    assert compatibility(p("Smith"), p("Bill Smith"))[0] == "weak"
    assert compatibility(p("John Smith"), p("Jane Smith"))[0] == "conflict"
    assert compatibility(p("J. Smith"), p("Jane Smith"))[0] == "strong"
    assert compatibility(p("J. Smith"), p("Karen Smith"))[0] == "conflict"
    assert compatibility(p("Jane A. Smith"), p("Jane B. Smith"))[0] == "conflict"
    assert compatibility(p("Jane Smith"), p("Jane Jones"))[0] == "conflict"
    assert compatibility(p("Jane Smith-Jones"), p("Jane Jones"))[0] in ("strong", "weak")
