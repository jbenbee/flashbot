import threading

import numpy as np
import pandas as pd


class WordsDB:
    def __init__(self, db_path):
        self.db_path = db_path
        self.words_df = pd.read_csv(self.db_path)
        self.words_df['id'] = self.words_df['id'].astype(int)
        if not self.words_df['id'].is_unique:
            raise ValueError('"id" field in the words database is not unique.')
        self._lock = threading.Lock()

    def save_words_db(self):
        with self._lock:
            self.words_df.to_csv(self.db_path, index=False)

    def get_words_df(self):
        with self._lock:
            return self.words_df.copy()

    def get_word_data(self, word, lang):
        with self._lock:
            return self.words_df.loc[(self.words_df['word'] == word) & (self.words_df['lang'] == lang)].to_dict()

    def add_new_word(self, word, lang):
        with self._lock:
            word_data = self.words_df.loc[
                (self.words_df['lang'] == lang) &
                (self.words_df['word'].str.casefold() == word.casefold())
            ]
            if word_data.shape[0] == 0:
                word_id = int(self.words_df['id'].max() + 1)
                self.words_df.loc[len(self.words_df)] = {'id': word_id, 'word': word,
                                                         'lang': lang, 'tags': np.nan}
            else:
                # Reuse the first entry even if older data has case variants.
                word_id = int(word_data['id'].iloc[0])
            return word_id
