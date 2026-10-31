import logging
import copy
import json
import threading

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


class DecksDB:
    def __init__(self, decks_path, deck_word_db_path):
        self.decks_path = decks_path
        self.deck_word_db_path = deck_word_db_path
        self.decks = pd.read_csv(self.decks_path)
        self.decks['owner'] = self.decks['owner'].astype(str)
        self.deck_word = pd.read_csv(self.deck_word_db_path)
        self.decks['id'] = self.decks['id'].astype(int)
        self.deck_word['deck_id'] = self.deck_word['deck_id'].astype(int)
        self.deck_word['word_id'] = self.deck_word['word_id'].astype(int)
        self._lock = threading.Lock()
        logger.info('Decks loaded decks=%s memberships=%s', len(self.decks), len(self.deck_word))

    def get_decks_df(self):
        with self._lock:
            return self.decks.copy()

    def get_deck_word_df(self):
        with self._lock:
            return self.deck_word.copy()

    def get_decks_lang(self, owner: str, lang: str):
        # returns an array of dictionaries
        with self._lock:
            owner_lang_data = self.decks.loc[self.decks['owner'].isin([owner, str(owner), 'common']) & (self.decks['language'] == lang)]
            return [dict(id=data['id'], owner=data['owner'], name=data['name'], language=data['name'], tags=data['tags']) for d, data in owner_lang_data.iterrows()]

    def get_deck_words(self, deck_id: int):
        with self._lock:
            return self.deck_word.loc[self.deck_word['deck_id'] == deck_id, 'word_id'].to_list()

    def get_user_decks(self, chat_id: int, lang: str):
        with self._lock:
            return self.decks.loc[(self.decks['owner'] == str(chat_id)) & (self.decks['language'] == lang)]['id'].to_list()

    def get_custom_deck_id(self, chat_id, lang):
        with self._lock:
            if len(self.decks.loc[(self.decks['owner'] == str(chat_id)) & (self.decks['language'] == lang) & (self.decks['name'] == 'custom'), 'id']) == 0:
                return None
            return self.decks.loc[(self.decks['owner'] == str(chat_id)) & (self.decks['language'] == lang) & (self.decks['name'] == 'custom'), 'id'].item()

    def add_custom_deck(self, chat_id: str, lang: str):
        with self._lock:
            new_deck_id = len(self.decks)
            self.decks.loc[new_deck_id] = \
                dict(id=len(self.decks), owner=chat_id, name='custom', language=lang, tags=np.nan)
            logger.info('Custom deck created chat_id=%s deck_id=%s language=%s', chat_id, new_deck_id, lang)
            return new_deck_id

    def save_decks_db(self):
        with self._lock:
            self.decks.to_csv(self.decks_path, index=False)
            self.deck_word.to_csv(self.deck_word_db_path, index=False)
            logger.info('Decks saved decks=%s memberships=%s', len(self.decks), len(self.deck_word))

    def add_new_word(self, deck_id: int, word_id: int):
        with self._lock:
            if ((self.deck_word['deck_id'] == deck_id) &
                    (self.deck_word['word_id'] == word_id)).any():
                return
            self.deck_word.loc[len(self.deck_word)] = \
                dict(deck_id=deck_id, word_id=word_id)
            logger.info('Word added to deck deck_id=%s word_id=%s', deck_id, word_id)

