import pytest


def test_add_new_word_assigns_next_id(make_words_db):
    db = make_words_db([{'id': 1, 'word': 'hello', 'lang': 'english', 'tags': None}])

    assert db.add_new_word('world', 'english') == 2
    assert not db._lock.locked()


def test_add_existing_word_returns_existing_id(make_words_db):
    db = make_words_db([{'id': 1, 'word': 'hello', 'lang': 'english', 'tags': None}])

    assert db.add_new_word('hello', 'english') == 1
    assert not db._lock.locked()


def test_same_word_in_different_lang_gets_new_id(make_words_db):
    db = make_words_db([{'id': 1, 'word': 'hello', 'lang': 'english', 'tags': None}])

    assert db.add_new_word('hello', 'german') == 2
    assert not db._lock.locked()

@pytest.mark.parametrize(('stored', 'submitted'), [
    ('hello', 'Hello'),
    ('Hello', 'HELLO'),
    ('Straße', 'STRASSE'),
])
def test_add_word_reuses_case_insensitive_match(make_words_db, stored, submitted):
    db = make_words_db([{'id': 1, 'word': stored, 'lang': 'german', 'tags': None}])

    assert db.add_new_word(submitted, 'german') == 1
    assert db.get_words_df()['word'].to_list() == [stored]
    assert db.add_new_word(submitted, 'english') == 2


def test_add_word_reuses_first_existing_case_variant(make_words_db):
    db = make_words_db([
        {'id': 1, 'word': 'Hello', 'lang': 'english', 'tags': None},
        {'id': 2, 'word': 'hello', 'lang': 'english', 'tags': None},
    ])

    assert db.add_new_word('HELLO', 'english') == 1
    assert len(db.get_words_df()) == 2
