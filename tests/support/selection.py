"""Validated selection fixtures for command-level tests."""
from codex_wechat.backend import PreparedSelection, PreparedSelectionKind


def prepared_selection(
    backend,
    thread_id,
    *,
    result=None,
    app=None,
    created=False,
    adopt=False,
    desktop=False,
    thread=None,
):
    expected = {
        "expected_connection": backend.connection_id,
        "expected_epoch": backend.selection_epoch,
        "thread_id": thread_id,
        "desktop": desktop,
    }
    if adopt:
        return PreparedSelection(
            **expected,
            kind=PreparedSelectionKind.ADOPTED,
            thread=thread or {},
        )
    if app is not None:
        return PreparedSelection(
            **expected,
            kind=PreparedSelectionKind.FOLLOWER,
            result=result or {"thread": {"id": thread_id}},
            thread=thread,
            app=app,
        )
    return PreparedSelection(
        **expected,
        kind=PreparedSelectionKind.NATIVE,
        result=result or {"thread": {"id": thread_id}},
        created=created,
    )
