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
