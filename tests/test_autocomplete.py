"""Community autosuggest ranking and short-query guard (no database access)."""
import data_access


def test_fusion_query_prefers_community_prefix(monkeypatch):
    rows = [
        {"id": 8, "name": "Fusion", "builder": "Lennar Homes", "city": "El Cajon"},
        {"id": 9, "name": "Other", "builder": "Fusion Properties", "city": "Carson"},
        {"id": 10, "name": "Fusion West", "builder": "Builder", "city": "San Diego"},
    ]
    monkeypatch.setattr(data_access, "community_suggestion_index", lambda: rows)
    found = data_access.suggest_communities("Fus", limit=5)
    assert found[0]["name"] == "Fusion"
    assert found[0]["builder"] == "Lennar Homes"
    assert len(found) == 3


def test_short_queries_do_not_hit_database(monkeypatch):
    def fail():
        raise AssertionError("No queries should execute before 2 characters")
    monkeypatch.setattr(data_access, "community_suggestion_index", fail)
    assert data_access.suggest_communities("F") == []
