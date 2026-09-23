from oracle_app.lists_notes_intents import parse_lists_notes_intent


def test_list_and_note_intents_are_finite_and_provider_neutral() -> None:
    assert parse_lists_notes_intent("add milk to my groceries list").arguments == {"title": "milk", "list_id": "groceries"}
    assert parse_lists_notes_intent("delete everything from the groceries list").operation == "delete_all"
    assert parse_lists_notes_intent("append more to the reference note").operation == "append"
    assert parse_lists_notes_intent("share my note") .operation == "unsupported"


def test_intents_never_expose_nextcloud_vtodo_or_rest_vocabulary() -> None:
    for text in ("add milk to groceries list", "read reference note"):
        intent = parse_lists_notes_intent(text)
        assert intent is not None
        assert "nextcloud" not in repr(intent).casefold()
        assert "vtodo" not in repr(intent).casefold()
