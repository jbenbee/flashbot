import os.path
import threading

import pandas as pd


class WordsProgressDB:

    def __init__(self, db_path):
        self.db_path = db_path
        if not os.path.exists(self.db_path):
            self.progress_df = pd.DataFrame(columns=['chat_id', 'word_id', 'num_reps', 'is_known', 'to_ignore'])
            self.save_progress()
        self.progress_df = pd.read_csv(self.db_path)
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
            {'chat_id': chat_id, 'word_id': word_id, 'num_reps': 0.0, 'is_known': False, 'to_ignore': False}
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

    def add_known_word(self, chat_id, word_id):
        self._lock.acquire()
        chat_word_progress = self.progress_df[(self.progress_df['chat_id'] == chat_id) &
                                              (self.progress_df['word_id'] == word_id)]
        if chat_word_progress.shape[0] == 0:
            self.add_word_to_progress(chat_id, word_id)

        mask = (self.progress_df['chat_id'] == chat_id) & (self.progress_df['word_id'] == word_id)
        if self.progress_df[mask].shape[0] != 1:
            self._lock.release()
            raise ValueError('Number of rows satisfying the condition must be exactly 1.')
        self.progress_df.loc[mask, 'is_known'] = True
        self._lock.release()

    def increment_word_reps(self, chat_id, word_id):
        self._lock.acquire()
        chat_word_progress = self.progress_df[(self.progress_df['chat_id'] == chat_id) &
                                              (self.progress_df['word_id'] == word_id)]
        if chat_word_progress.shape[0] == 0:
            self.add_word_to_progress(chat_id, word_id)

        mask = (self.progress_df['chat_id'] == chat_id) & (self.progress_df['word_id'] == word_id)
        if self.progress_df[mask].shape[0] != 1:
            self._lock.release()
            raise ValueError('Number of rows satisfying the condition must be exactly 1.')

        self.progress_df.loc[mask, 'num_reps'] = self.progress_df.loc[mask, 'num_reps'] + 1
        self._lock.release()

    def release_lock(self):
        if self._lock.locked():
            self._lock.release()