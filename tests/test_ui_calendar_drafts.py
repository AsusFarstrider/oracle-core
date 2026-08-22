from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from oracle_app import ui_calendar_drafts


def setup_function() -> None:
    ui_calendar_drafts.clear_all_ui_calendar_drafts()


def test_draft_is_scoped_by_client_and_returned_as_copy() -> None:
    ui_calendar_drafts.store_ui_calendar_draft(
        "client-a",
        "draft-1",
        {"title": "Dinner", "nested": {"confirmed": False}},
    )

    assert ui_calendar_drafts.load_ui_calendar_draft("client-b", "draft-1") is None
    loaded = ui_calendar_drafts.load_ui_calendar_draft("client-a", "draft-1")
    assert loaded is not None
    loaded["nested"]["confirmed"] = True

    assert ui_calendar_drafts.load_ui_calendar_draft("client-a", "draft-1") == {
        "title": "Dinner",
        "nested": {"confirmed": False},
    }


def test_clear_for_client_does_not_clear_another_client() -> None:
    ui_calendar_drafts.store_ui_calendar_draft("client-a", "draft-a", {"title": "A"})
    ui_calendar_drafts.store_ui_calendar_draft("client-b", "draft-b", {"title": "B"})

    ui_calendar_drafts.clear_ui_calendar_drafts_for_client("client-a")

    assert ui_calendar_drafts.load_ui_calendar_draft("client-a", "draft-a") is None
    assert ui_calendar_drafts.load_ui_calendar_draft("client-b", "draft-b") == {"title": "B"}


def test_concurrent_draft_mutations_remain_isolated() -> None:
    def store(index: int) -> None:
        ui_calendar_drafts.store_ui_calendar_draft(
            f"client-{index % 4}",
            f"draft-{index}",
            {"index": index},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(store, range(100)))

    for index in range(100):
        assert ui_calendar_drafts.load_ui_calendar_draft(
            f"client-{index % 4}",
            f"draft-{index}",
        ) == {"index": index}
