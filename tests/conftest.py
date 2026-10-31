import sys
from pathlib import Path

import pandas as pd
import pytest

# make the project root importable regardless of how pytest is invoked
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from decks_db import DecksDB
from running_activities import RunningActivities
from words_db import WordsDB
from words_progress_db import WordsProgressDB

PROGRESS_COLUMNS = ['chat_id', 'word_id', 'num_reps', 'to_ignore',
                    'e_factor', 'last_interval', 'last_review_date', 'next_review_date']

WORDS_COLUMNS = ['id', 'word', 'lang', 'tags', 'meaning']

DECKS_COLUMNS = ['id', 'owner', 'name', 'language', 'tags']

DECK_WORD_COLUMNS = ['deck_id', 'word_id']


def progress_row(chat_id, word_id, to_ignore=False):
    return {'chat_id': chat_id, 'word_id': word_id, 'num_reps': 0.0, 'to_ignore': to_ignore,
            'e_factor': 2.5, 'last_interval': 0, 'last_review_date': None, 'next_review_date': None}


@pytest.fixture
def make_progress_db(tmp_path):
    """Factory: build a WordsProgressDB backed by a temp CSV with the given rows."""
    def _make(rows):
        path = tmp_path / 'words_progress_db.csv'
        pd.DataFrame(rows, columns=PROGRESS_COLUMNS).to_csv(path, index=False)
        return WordsProgressDB(path)
    return _make


@pytest.fixture
def make_words_db(tmp_path):
    """Factory: build a WordsDB backed by a temp CSV with the given rows."""
    def _make(rows):
        path = tmp_path / 'words_db.csv'
        pd.DataFrame(rows, columns=WORDS_COLUMNS).to_csv(path, index=False)
        return WordsDB(path)
    return _make


@pytest.fixture
def make_decks_db(tmp_path):
    """Factory: build a DecksDB backed by temp CSVs with the given rows."""
    def _make(deck_rows, deck_word_rows):
        decks_path = tmp_path / 'decks_db.csv'
        deck_word_path = tmp_path / 'deck_word.csv'
        pd.DataFrame(deck_rows, columns=DECKS_COLUMNS).to_csv(decks_path, index=False)
        pd.DataFrame(deck_word_rows, columns=DECK_WORD_COLUMNS).to_csv(deck_word_path, index=False)
        return DecksDB(decks_path, deck_word_path)
    return _make


@pytest.fixture
def running_activities(tmp_path):
    return RunningActivities(tmp_path / 'running_exercise.jb')
