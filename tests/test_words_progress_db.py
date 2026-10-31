import threading

import pandas as pd
import pytest

from conftest import progress_row


def test_lock_released_after_exception(make_progress_db):
    """An exception inside a method must not leave the internal lock held."""
    db = make_progress_db([progress_row(1, 10)])

    # create a duplicate (chat_id, word_id) pair so ignore_word raises ValueError
    db.progress_df = pd.concat([db.progress_df, db.progress_df], ignore_index=True)

    with pytest.raises(ValueError):
        db.ignore_word(1, 10)

    assert not db._lock.locked()


def test_ignore_word_adds_missing_progress_row_without_deadlock(make_progress_db):
    """ignore_word on a word with no progress row must add it first.

    Regression test: this path used to self-deadlock because ignore_word called
    add_word_to_progress while already holding the non-reentrant lock.
    """
    db = make_progress_db([progress_row(5, 99)])

    completed = threading.Event()

    def run():
        db.ignore_word(5, 1234)
        completed.set()

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=5)

    assert completed.is_set(), "ignore_word deadlocked on a missing progress row"
    row = db.progress_df[db.progress_df['word_id'] == 1234]
    assert row.shape[0] == 1
    assert row['to_ignore'].iloc[0]
    assert not db._lock.locked()


def test_add_and_get_word_progress(make_progress_db):
    db = make_progress_db([progress_row(5, 99)])

    db.add_word_to_progress(5, 100)
    item = db.get_word_progress(5, 100)

    assert item is not None
    assert item.word_id == 100
    assert not db._lock.locked()


def test_get_word_progress_missing_returns_none(make_progress_db):
    db = make_progress_db([progress_row(5, 99)])

    assert db.get_word_progress(5, 12345) is None
    assert not db._lock.locked()
