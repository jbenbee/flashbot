from types import SimpleNamespace

import pytest

import main
from decks_db import DecksDB
from words_db import WordsDB


@pytest.mark.parametrize('has_custom_deck', [False, True])
def test_add_word_persists_valid_custom_deck(
    monkeypatch, make_words_db, make_decks_db, has_custom_deck,
):
    words_db = make_words_db([
        {'id': 1, 'word': 'hello', 'lang': 'english', 'tags': None},
    ])
    deck_rows = [
        {'id': 0, 'owner': 'common', 'name': 'base', 'language': 'english', 'tags': None},
    ]
    if has_custom_deck:
        deck_rows.append(
            {'id': 1, 'owner': '42', 'name': 'custom', 'language': 'english', 'tags': None},
        )
    decks_db = make_decks_db(deck_rows, [{'deck_id': 0, 'word_id': 1}])
    monkeypatch.setattr(main, 'words_db', words_db, raising=False)
    monkeypatch.setattr(main, 'decks_db', decks_db, raising=False)
    monkeypatch.setattr(main, 'lang_map', {'test-token': 'english'}, raising=False)
    monkeypatch.setattr(main, 'templates', SimpleNamespace(
        get_template=lambda *args: 'Added {{ word }}',
    ), raising=False)
    context = SimpleNamespace(_application=SimpleNamespace(
        bot=SimpleNamespace(token='test-token'),
    ))

    message = main.execute_command_message(
        context, 42, 'english', 'add_word', 'world\ngoodbye',
    )

    # Reload both CSVs to check that the command survives a bot restart.
    reloaded_decks = DecksDB(decks_db.decks_path, decks_db.deck_word_db_path)
    reloaded_words = WordsDB(words_db.db_path)
    custom_deck_id = reloaded_decks.get_custom_deck_id('42', 'english')
    assert custom_deck_id is not None
    assert reloaded_decks.get_user_decks(42, 'english') == [custom_deck_id]
    if has_custom_deck:
        assert custom_deck_id == 1
    word_ids = reloaded_decks.get_deck_words(custom_deck_id)
    words = reloaded_words.get_words_df()
    assert set(words.loc[words['id'].isin(word_ids), 'word']) == {'world', 'goodbye'}
    assert reloaded_decks.get_deck_words(0) == [1]
    assert message == 'Added goodbye'


@pytest.mark.parametrize('messages', [
    ['hello\nhello\nHello'],
    ['hello', 'hello', 'Hello'],
])
def test_add_word_skips_duplicates_case_insensitively(
    monkeypatch, make_words_db, make_decks_db, messages,
):
    words_db = make_words_db([
        {'id': 1, 'word': 'hello', 'lang': 'english', 'tags': None},
    ])
    decks_db = make_decks_db([
        {'id': 0, 'owner': 'common', 'name': 'base', 'language': 'english', 'tags': None},
    ], [{'deck_id': 0, 'word_id': 1}])
    monkeypatch.setattr(main, 'words_db', words_db, raising=False)
    monkeypatch.setattr(main, 'decks_db', decks_db, raising=False)
    monkeypatch.setattr(main, 'lang_map', {'test-token': 'english'}, raising=False)
    monkeypatch.setattr(main, 'templates', SimpleNamespace(
        get_template=lambda *args: 'Added {{ word }}',
    ), raising=False)
    context = SimpleNamespace(_application=SimpleNamespace(
        bot=SimpleNamespace(token='test-token'),
    ))

    for message in messages:
        main.execute_command_message(context, 42, 'english', 'add_word', message)

    reloaded_words = WordsDB(words_db.db_path).get_words_df()
    reloaded_decks = DecksDB(decks_db.decks_path, decks_db.deck_word_db_path)
    custom_deck_id = reloaded_decks.get_custom_deck_id('42', 'english')
    assert reloaded_words['word'].to_list() == ['hello']
    assert reloaded_decks.get_deck_words(custom_deck_id) == [1]
    assert reloaded_decks.get_deck_words(0) == [1]
