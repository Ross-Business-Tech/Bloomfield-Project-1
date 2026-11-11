from src.common.normalize import normalize_name
from src.score.buyers import BuyerResolver


def bare(ops=None, transfers=()):
    r = BuyerResolver.__new__(BuyerResolver)
    r.by_name, r.org_chain, r.fac_ops, r.transferred_to = {normalize_name("Acme OpCo"): ("o1", None)}, {}, ops or {}, set(transfers)
    r.org_facs, r.chains = {}, {}
    return r


def test_buyer_named_after_the_home_is_its_own_operator():
    r = bare()
    assert r.own_operator("f1", "ARCADIA CARE TOULON LLC", "ARCADIA CARE TOULON")
    assert not r.own_operator("f1", "Strawberry Fields REIT", "LIVING CENTER, THE")


def test_same_org_as_operator_only_without_a_recorded_transfer():
    assert bare({"f1": {"o1"}}).own_operator("f1", "Acme OpCo", "Sunny Acres")
    assert not bare({"f1": {"o1"}}, [("f1", "o1")]).own_operator("f1", "Acme OpCo", "Sunny Acres")


def test_single_home_opco_without_chain_is_not_a_buyer_but_with_chain_it_is():
    r = bare({"f1": {"o1"}}, [("f1", "o1")])
    r.org_facs = {"o1": {"f1"}}
    assert r.own_operator("f1", "Acme OpCo", "Sunny Acres")              # one home, no chain, set up at the CHOW
    r.by_name = {normalize_name("Acme OpCo"): ("o1", "c9")}
    r.chains = {"c9": {"chain_name": "ACME HEALTH"}}
    assert not r.own_operator("f1", "Acme OpCo", "Sunny Acres")          # CMS chain known: kept, chain shown as parent
