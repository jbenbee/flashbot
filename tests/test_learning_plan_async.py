import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from learning_plan import LearningPlan
from words_exercise import FlashcardExercise, WordsExerciseLearn
from conftest import progress_row


def test_next_exercise_still_selects_due_word(make_words_db, make_decks_db, make_progress_db):
    words_db = make_words_db([
        {'id': 1, 'word': 'hello', 'lang': 'english', 'tags': None},
        {'id': 2, 'word': 'world', 'lang': 'english', 'tags': None},
    ])
    decks_db = make_decks_db(
        [{'id': 0, 'owner': '42', 'name': 'custom', 'language': 'english', 'tags': None},
         {'id': 1, 'owner': 'common', 'name': 'base', 'language': 'english', 'tags': None}],
        [{'deck_id': 0, 'word_id': 1}, {'deck_id': 0, 'word_id': 2}],
    )
    due_word = progress_row(42, 1)
    due_word['next_review_date'] = (datetime.now() - timedelta(days=1)).date().isoformat()
    progress_db = make_progress_db([due_word])
    user_config = SimpleNamespace(
        get_user_data=lambda chat_id: {'n_flashcards': 5, 'level': 'A1', 'ui_language': 'english'},
        get_user_ui_lang=lambda chat_id: 'english',
    )
    plan = LearningPlan({}, None, progress_db, words_db, decks_db, user_config)

    exercise = asyncio.run(plan.get_next_words_exercise(42, 'english', mode='test'))

    assert isinstance(exercise, FlashcardExercise)
    assert exercise.word_id == 1


@pytest.fixture
def shared_word_plan(make_words_db, make_decks_db, make_progress_db):
    def make(rows):
        words_db = make_words_db([
            {'id': 1, 'word': 'hello', 'lang': 'english', 'tags': None, 'meaning': 'a greeting'},
        ])
        decks_db = make_decks_db(
            [{'id': 0, 'owner': '42', 'name': 'custom', 'language': 'english', 'tags': None},
             {'id': 1, 'owner': '99', 'name': 'custom', 'language': 'english', 'tags': None}],
            [{'deck_id': 0, 'word_id': 1}, {'deck_id': 1, 'word_id': 1}],
        )
        user_config = SimpleNamespace(
            get_user_data=lambda chat_id: {'n_flashcards': 5, 'level': 'A1', 'ui_language': 'english'},
            get_user_ui_lang=lambda chat_id: 'english',
        )
        return LearningPlan({}, None, make_progress_db(rows), words_db, decks_db, user_config)
    return make


@pytest.mark.parametrize('other_user_ignored', [False, True])
def test_learning_shared_word_ignores_other_users_progress(shared_word_plan, other_user_ignored):
    other_progress = progress_row(99, 1, to_ignore=other_user_ignored)
    other_progress.update(
        num_reps=7.0,
        last_review_date=datetime.now().date().isoformat(),
        next_review_date=(datetime.now() + timedelta(days=10)).date().isoformat(),
    )
    plan = shared_word_plan([other_progress])

    exercise = asyncio.run(plan.get_next_words_exercise(42, 'english', mode='learn'))

    assert isinstance(exercise, WordsExerciseLearn)
    assert exercise.word_id == 1
    assert exercise.num_reps == 1
    assert exercise.meaning == 'a greeting'
    assert plan.progress_db.get_word_progress(42, 1) is None
    assert plan.progress_db.get_word_progress(99, 1).num_reps == 7.0


def test_learning_shared_word_respects_current_users_ignore(shared_word_plan):
    rows = [
        progress_row(42, 1, to_ignore=True),
        progress_row(99, 1, to_ignore=False),
    ]
    for row in rows:
        row['next_review_date'] = (datetime.now() + timedelta(days=1)).date().isoformat()
    plan = shared_word_plan(rows)

    assert plan._select_next_words_exercise(42, 'english', mode='learn') is None
