import numpy as np
import pandas as pd


class WordsDB:
    def __init__(self, db_path):
        self.db_path = db_path
        self.words_df = pd.read_csv(self.db_path)
        self.words_df['id'] = self.words_df['id'].astype(int)
        if not self.words_df['id'].is_unique:
            raise ValueError('"id" field in the words database is not unique.')

    def save_words_db(self):
        self.words_df.to_csv(self.db_path, index=False)

    def get_words_df(self):
        return self.words_df.copy()

    def get_word_data(self, word, lang):
        return self.words_df.loc[(self.words_df['word'] == word) & (self.words_df['lang'] == lang)].to_dict()

    def add_new_word(self, word, word_group, lang, user):
        if word not in self.words_df.loc[self.words_df['lang'] == lang, 'word'].tolist():
            word_id = self.words_df['id'].max() + 1
            self.words_df.loc[len(self.words_df)] = {'id': word_id, 'word': word, 'group': word_group,
                                                     'lang': lang, 'tags': np.nan}
