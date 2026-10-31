import pytest


@pytest.fixture
def decks_db(make_decks_db):
    return make_decks_db(
        [{'id': 0, 'owner': 'common', 'name': 'base', 'language': 'english', 'tags': None}],
        [{'deck_id': 0, 'word_id': 1}],
    )


def test_get_custom_deck_id_missing_returns_none(decks_db):
    assert decks_db.get_custom_deck_id('42', 'english') is None
    assert not decks_db._lock.locked()


def test_add_and_get_custom_deck(decks_db):
    new_id = decks_db.add_custom_deck('42', 'english')

    assert decks_db.get_custom_deck_id('42', 'english') == new_id
    assert not decks_db._lock.locked()


def test_add_new_word_to_deck(decks_db):
    decks_db.add_new_word(0, 7)

    assert 7 in decks_db.get_deck_words(0)
    assert not decks_db._lock.locked()
