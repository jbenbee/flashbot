import re
import threading

import numpy as np
import pandas as pd

import spacy


class WordsDB:
    def __init__(self, db_path):
        self.db_path = db_path
        self.words_df = pd.read_csv(self.db_path)
        self.words_df['id'] = self.words_df['id'].astype(int)
        if not self.words_df['id'].is_unique:
            raise ValueError('"id" field in the words database is not unique.')
        self.supported_languages = ['italian', 'russian', 'spanish']
        self.alphabets = {
            'italian': 'ABCDEFGHIJKLMNOPQRSTUVWXYZÉÍÓÙabcdefghijklmnopqrstuvwxyzéíóù',
            'spanish': 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÜÑabcdefghijklmnopqrstuvwxyzáéíóúüñ',
            'russian': 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя'
        }
        self.spacy_lib = {
            'spanish': 'es_dep_news_trf',
            'italian': 'it_core_news_lg',
            'russian': 'ru_core_news_sm', #'ru_core_news_lg'  TODO
        }
        self._lock = threading.Lock()

    def save_words_db(self):
        self._lock.acquire()
        self.words_df.to_csv(self.db_path, index=False)
        self._lock.release()

    def get_words_df(self):
        self._lock.acquire()
        wdf_cpy = self.words_df.copy()
        self._lock.release()
        return wdf_cpy

    def get_word_data(self, word, lang):
        self._lock.acquire()
        res = self.words_df.loc[(self.words_df['word'] == word) & (self.words_df['lang'] == lang)].to_dict()
        self._lock.release()
        return res

    def add_new_word(self, word, lang):
        self._lock.acquire()
        word_data = self.words_df.loc[(self.words_df['lang'] == lang) & (self.words_df['word'] == word)]
        if word_data.shape[0] == 0:
            word_id = int(self.words_df['id'].max() + 1)
            self.words_df.loc[len(self.words_df)] = {'id': word_id, 'word': word,
                                                     'lang': lang, 'tags': np.nan}
        elif word_data.shape[0] == 1:
            word_id = int(word_data['id'].item())
        else:
            self._lock.release()
            raise ValueError(f'The same word "{word}" appears >1 time in the database: {word_data}')
        self._lock.release()
        return word_id

    def release_lock(self):
        if self._lock.locked():
            self._lock.release()

    def add_words(self, lang, corpus):
        nlp = spacy.load(self.spacy_lib[lang])
        doc = nlp(corpus)
        tokens = set()
        for token in doc:
            alphabet = self.alphabets[lang]
            if re.match(fr'^[{alphabet}]+$', str(token)) is None:
                continue
            if token.pos_ not in ['VERB', 'NOUN', 'ADJ', 'ADV', 'SCONJ']:
                continue
            tokens.add(token.lemma_)
        print(tokens)
