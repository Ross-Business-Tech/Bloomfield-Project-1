from src.extract.split import split_deals


def test_roundup_splits_into_one_item_per_deal():
    body = "\n\n".join([
        "Transactions",                                    # section label: starts no deal
        "Acme acquires Springfield community",
        "Acme Senior Living bought a 90-unit assisted living community in Springfield, Illinois, from a regional "
        "owner, according to a press release.",
        "The community will be renamed.",
        "Lender closes $20M bridge loan",
        "A national lender provided a $20 million bridge loan for a memory care portfolio in Ohio, the release said, "
        "to refinance maturing debt on four communities that opened in 2019.",
    ])
    deals = split_deals(body)
    assert [d.heading for d in deals] == ["Acme acquires Springfield community", "Lender closes $20M bridge loan"]
    assert "90-unit" in deals[0].text and "renamed" in deals[0].text and "bridge loan" in deals[1].text


def test_single_story_is_not_split():
    body = "Acme acquires Springfield community\n\n" + "Acme Senior Living bought a community. " * 10
    assert split_deals(body) == []
