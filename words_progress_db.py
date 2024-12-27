import os.path
import threading
from typing import Optional

import pandas as pd

from item import Item


class WordsProgressDB:
    def __init__(self, db_path):
        self.db_path = db_path
        if not os.path.exists(self.db_path):
            self.progress_df = pd.DataFrame(columns=['chat_id', 'word_id', 'num_reps', 'e_factor', 'last_interval', 'last_review_date', 'next_review_date', 'is_known', 'to_ignore'])
            self.save_progress()
        self.progress_df = pd.read_csv(self.db_path)
        self.progress_df['last_review_date'] = pd.to_datetime(self.progress_df['last_review_date'])
        self.progress_df['next_review_date'] = pd.to_datetime(self.progress_df['next_review_date']).dt.date
        self._lock = threading.Lock()

    def get_progress_df(self):
        self._lock.acquire()
        pdf_cpy = self.progress_df.copy()
        self._lock.release()
        return pdf_cpy

    def save_progress(self):
        self._lock.acquire()
        self.progress_df.to_csv(self.db_path, index=False)
        self._lock.release()

    def add_word_to_progress(self, chat_id, word_id):
        self._lock.acquire()
        self.progress_df.loc[len(self.progress_df)] = \
            {'chat_id': chat_id, 'word_id': word_id, 'num_reps': 0.0, 'is_known': False, 'to_ignore': False,
             'e_factor': 2.5, 'last_interval': 0, 'last_review_date': None, 'next_review_date': None}
        self._lock.release()

    def ignore_word(self, chat_id, word_id):
        self._lock.acquire()
        chat_word_progress = self.progress_df[(self.progress_df['chat_id'] == chat_id) &
                                              (self.progress_df['word_id'] == word_id)]
        if chat_word_progress.shape[0] == 0:
            self.add_word_to_progress(chat_id, word_id)

        mask = (self.progress_df['chat_id'] == chat_id) & (self.progress_df['word_id'] == word_id)
        if self.progress_df[mask].shape[0] != 1:
            self._lock.release()
            raise ValueError('Number of rows satisfying the condition must be exactly 1.')
        self.progress_df.loc[mask, 'to_ignore'] = True
        self._lock.release()

    def get_word_progress(self, chat_id, word_id) -> Optional[Item]:
        self._lock.acquire()
        chat_word_progress = self.progress_df[(self.progress_df['chat_id'] == chat_id) &
                                              (self.progress_df['word_id'] == word_id)]
        if chat_word_progress.shape[0] == 0:
            self._lock.release()
            return None

        mask = (self.progress_df['chat_id'] == chat_id) & (self.progress_df['word_id'] == word_id)
        row = self.progress_df[mask]
        if row.shape[0] != 1:
            self._lock.release()
            raise ValueError('Number of rows satisfying the condition must be exactly 1.')
        item = Item(e_factor=row['e_factor'].item(), num_reps=row['num_reps'].item(),
                    next_review_date=row['next_review_date'].item(), last_review_date=row['last_review_date'].item(),
                    last_interval=row['last_interval'].item(), word_id=word_id)
        self._lock.release()
        return item

    def set_word_progress(self, chat_id, word_id, item) -> None:
        self._lock.acquire()
        mask = (self.progress_df['chat_id'] == chat_id) & (self.progress_df['word_id'] == word_id)

        chat_word_progress = self.progress_df[mask]
        if chat_word_progress.shape[0] != 1:
            self._lock.release()
            raise ValueError(f'Number of rows satisfying the condition must be exactly 1, found {chat_word_progress.shape[0]}.')
        self.progress_df.loc[mask, 'num_reps'] = item.num_reps
        self.progress_df.loc[mask, 'e_factor'] = item.e_factor
        self.progress_df.loc[mask, 'next_review_date'] = item.next_review_date
        self.progress_df.loc[mask, 'last_review_date'] = item.last_review_date
        self.progress_df.loc[mask, 'last_interval'] = item.last_interval
        self._lock.release()

    def release_lock(self):
        if self._lock.locked():
            self._lock.release()