import threading

import pytest

from conftest import progress_row

N_THREADS = 8
N_ITERATIONS = 50


@pytest.fixture
def shared_objects(make_words_db, make_decks_db, make_progress_db, running_activities):
    return (
        make_words_db([{'id': 1, 'word': 'hello', 'lang': 'english', 'tags': None}]),
        make_decks_db(
            [{'id': 0, 'owner': 'common', 'name': 'base', 'language': 'english', 'tags': None}],
            [{'deck_id': 0, 'word_id': 1}],
        ),
        make_progress_db([progress_row(5, 99)]),
        running_activities,
    )


def hammer(words_db, decks_db, progress_db, activities):
    for i in range(N_ITERATIONS):
        words_db.add_new_word(f'w{i}', 'english')
        decks_db.get_deck_words(0)
        progress_db.get_progress_df()
        activities.current_activity(42)


def test_shared_objects_survive_concurrent_access(shared_objects):
    """Concurrent readers/writers must neither deadlock nor leak locks."""
    threads = [threading.Thread(target=hammer, args=shared_objects) for _ in range(N_THREADS)]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert all(not t.is_alive() for t in threads), "deadlock under concurrent access"
    for obj in shared_objects:
        assert not obj._lock.locked()
