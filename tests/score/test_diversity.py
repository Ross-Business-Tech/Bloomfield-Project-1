from src.score.diversity import diversity_order


def rows(buyers):
    return [{"lead_id": str(i), "buyer": b, "score_breakdown": {}} for i, b in enumerate(buyers)]


def test_third_lead_of_a_buyer_moves_just_below_the_top():
    rs = rows(["X", "X", "X", "Y", "NOT FOUND", "NOT FOUND", "NOT FOUND", "Z"])
    order, capped = diversity_order(rs, top_n=5, per_buyer=2)
    assert [r["lead_id"] for r in order] == ["0", "1", "3", "4", "5", "2", "6", "7"]
    assert capped == {"2"}


def test_no_buyer_is_never_capped_and_parent_groups_shells():
    rs = rows(["A OpCo", "B OpCo", "C OpCo"])
    for r in rs:
        r["score_breakdown"] = {"buyer_parent": "Acme Senior Living"}
    order, capped = diversity_order(rs, top_n=3, per_buyer=2)
    assert capped == {"2"} and order[-1]["lead_id"] == "2"
    assert diversity_order(rows(["NOT FOUND"] * 4), top_n=3, per_buyer=1)[1] == set()


def test_ties_break_on_latest_event_then_lead_id():
    from src.score.diversity import display_sort
    rs = [{"lead_id": "b", "score": 75, "score_breakdown": {"latest_signal_date": "2026-01-01"}},
          {"lead_id": "a", "score": 75, "score_breakdown": {"latest_signal_date": "2026-01-01"}},
          {"lead_id": "c", "score": 75, "score_breakdown": {"latest_signal_date": "2026-10-29"}},
          {"lead_id": "d", "score": 80, "score_breakdown": {}}]
    assert [r["lead_id"] for r in display_sort(rs)] == ["d", "c", "a", "b"]
