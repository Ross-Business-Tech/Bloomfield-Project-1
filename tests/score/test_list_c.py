from src.score.list_c import PERSONAL, merged_count


def test_articles_about_the_same_sale_count_once():
    deals = {("src", "a1"): 1, ("src", "a2"): 1, ("fac", "f3"): 1}
    homes = {("src", "a1"): {"f1", "f2"}, ("src", "a2"): {"f2"}, ("fac", "f3"): {"f3"}}
    assert merged_count(deals, homes) == 2


def test_personal_trusts_are_not_used_as_a_parent():
    assert PERSONAL.search("SMITH FAMILY IRREVOCABLE TRUST")
    assert not PERSONAL.search("Acme Senior Living, LLC")
