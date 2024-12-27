import copy
import json
import threading

import numpy as np
import pandas as pd


class DecksDB:
    def __init__(self, decks_path, deck_word_db_path):
        self.decks_path = decks_path
        self.deck_word_db_path = deck_word_db_path
        self.decks = pd.read_csv(self.decks_path)
        self.deck_word = pd.read_csv(self.deck_word_db_path)
        self.decks['id'] = self.decks['id'].astype(int)
        self.deck_word['deck_id'] = self.deck_word['deck_id'].astype(int)
        self.deck_word['word_id'] = self.deck_word['word_id'].astype(int)
        self._lock = threading.Lock()

    def get_decks_lang(self, owner: str, lang: str):
        # returns an array of dictionaries
        self._lock.acquire()
        owner_lang_data = self.decks.loc[self.decks['owner'].isin([owner, 'common']) & (self.decks['language'] == lang)]
        res = [dict(id=data['id'], owner=data['owner'], name=data['name'], language=data['name'], tags=data['tags']) for d, data in owner_lang_data.iterrows()]
        self._lock.release()
        return res

    def get_deck_words(self, deck_id: int):
        self._lock.acquire()
        res = self.deck_word.loc[self.deck_word['deck_id'] == deck_id, 'word_id'].to_list()
        self._lock.release()
        return res

    def is_deck_owner(self, owner: str, deck_id: int):
        self._lock.acquire()
        actual_owner = self.decks.loc[self.decks['id'] == deck_id, 'owner'].item()
        self._lock.release()
        return owner == actual_owner

    def create_deck(self, owner: str, deck_name: str, lang: str):
        self._lock.acquire()
        new_deck_id = len(self.decks)
        self.decks.loc[new_deck_id] = \
            dict(id=len(self.decks), owner=owner, name=deck_name, language=lang, tags=np.nan)
        self._lock.release()
        return new_deck_id

    def save_decks_db(self):
        self._lock.acquire()
        self.decks.to_csv(self.decks_path, index=False)
        self.deck_word.to_csv(self.deck_word_db_path, index=False)
        self._lock.release()

    def add_new_word(self, deck_id: int, word_id: int):
        self._lock.acquire()
        self.deck_word.loc[len(self.deck_word)] = \
            dict(deck_id=deck_id, word_id=word_id)
        self._lock.release()

    def get_deck_name(self, deck_id: int):
        self._lock.acquire()
        res = self.decks.loc[self.decks['id'] == deck_id, 'name'].item()
        self._lock.release()
        return res

    def release_lock(self):
        if self._lock.locked():
            self._lock.release()

