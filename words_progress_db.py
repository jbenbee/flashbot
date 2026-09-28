import os.path
import threading
from typing import Optional

import pandas as pd

from item import Item


class WordsProgressDB:
    def __init__(self, db_path):
        self.db_path = db_path
        if not os.path.exists(self.db_path):
            self.progress_df = pd.DataFrame(columns=['chat_id', 'word_id', 'num_reps', 'e_factor', 'last_interval', 'last_review_date', 'next_review_date', 'to_ignore'])
        self.progress_df = pd.read_csv(self.db_path)
        self.progress_df['last_review_date'] = pd.to_datetime(self.progress_df['last_review_date'])
        self.progress_df['next_review_date'] = pd.to_datetime(self.progress_df['next_review_date']).dt.date
        self.progress_df['word_id'] = self.progress_df['word_id'].astype(int)
        self.progress_df['chat_id'] = self.progress_df['chat_id'].astype(int)
        self._lock = threading.Lock()

    def get_progress_df(self):
        with self._lock:
            return self.progress_df.copy()

    def save_progress(self):
        with self._lock:
            self.progress_df.to_csv(self.db_path, index=False)

    def _add_word_to_progress(self, chat_id, word_id):
        # caller must hold self._lock
        new_row = pd.DataFrame([{'chat_id': chat_id, 'word_id': word_id, 'num_reps': 0.0, 'to_ignore': False,
             'e_factor': 2.5, 'last_interval': 0, 'last_review_date': None, 'next_review_date': None}])[self.progress_df.columns].astype(self.progress_df.dtypes)
        self.progress_df = pd.concat([self.progress_df, new_row], ignore_index=True)

    def add_word_to_progress(self, chat_id, word_id):
        with self._lock:
            self._add_word_to_progress(chat_id, word_id)

    def ignore_word(self, chat_id, word_id):
        with self._lock:
            chat_word_progress = self.progress_df[(self.progress_df['chat_id'] == chat_id) &
                                                  (self.progress_df['word_id'] == word_id)]
            if chat_word_progress.shape[0] == 0:
                self._add_word_to_progress(chat_id, word_id)

            mask = (self.progress_df['chat_id'] == chat_id) & (self.progress_df['word_id'] == word_id)
            if self.progress_df[mask].shape[0] != 1:
                raise ValueError('Number of rows satisfying the condition must be exactly 1.')
            self.progress_df.loc[mask, 'to_ignore'] = True

    def get_word_progress(self, chat_id, word_id) -> Optional[Item]:
        with self._lock:
            chat_word_progress = self.progress_df[(self.progress_df['chat_id'] == chat_id) &
                                                  (self.progress_df['word_id'] == word_id)]
            if chat_word_progress.shape[0] == 0:
                return None

            mask = (self.progress_df['chat_id'] == chat_id) & (self.progress_df['word_id'] == word_id)
            row = self.progress_df[mask]
            if row.shape[0] != 1:
                raise ValueError('Number of rows satisfying the condition must be exactly 1.')
            return Item(e_factor=row['e_factor'].item(), num_reps=row['num_reps'].item(),
                        next_review_date=row['next_review_date'].item(), last_review_date=row['last_review_date'].item(),
                        last_interval=row['last_interval'].item(), word_id=word_id)

    def set_word_progress(self, chat_id, word_id, item) -> None:
        with self._lock:
            mask = (self.progress_df['chat_id'] == chat_id) & (self.progress_df['word_id'] == word_id)

            chat_word_progress = self.progress_df[mask]
            if chat_word_progress.shape[0] != 1:
                raise ValueError(f'Number of rows satisfying the condition must be exactly 1, found {chat_word_progress.shape[0]}.')
            self.progress_df.loc[mask, 'num_reps'] = item.num_reps
            self.progress_df.loc[mask, 'e_factor'] = item.e_factor
            self.progress_df.loc[mask, 'next_review_date'] = item.next_review_date
            self.progress_df.loc[mask, 'last_review_date'] = item.last_review_date
            self.progress_df.loc[mask, 'last_interval'] = item.last_interval

    def remove_progress(self, chat_id, word_ids):
        with self._lock:
            mask = (self.progress_df['chat_id'] == chat_id) & (self.progress_df['word_id'].isin(word_ids))
            self.progress_df = self.progress_df[~mask]