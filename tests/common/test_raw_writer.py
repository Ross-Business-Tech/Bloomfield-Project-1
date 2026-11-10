from src.common.raw_writer import content_hash


def test_hash_ignores_key_order():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_hash_changes_with_content():
    assert content_hash({"a": 1}) != content_hash({"a": 2})
    assert len(content_hash("text")) == 64
