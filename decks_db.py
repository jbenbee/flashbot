import copy
import json

import numpy as np
import pandas as pd


class DecksDB:
    def __init__(self, decks_path, deck_word_db_path):
        self.decks_path = decks_path
        self.deck_word_db_path = deck_word_db_path
        self.decks = pd.read_csv(self.decks_path)
        self.deck_word = pd.read_csv(self.deck_word_db_path)

    def get_decks_lang(self, owner: str, lang: str):
        # returns an array of dictionaries
        owner_lang_data = self.decks.loc[self.decks['owner'].isin([owner, 'common']) & (self.decks['language'] == lang)]
        return [dict(id=data['id'], owner=data['owner'], name=data['name'], language=data['name'], tags=data['tags']) for d, data in owner_lang_data.iterrows()]

    def get_deck_words(self, deck_id: int):
        return self.deck_word.loc[self.deck_word['deck_id'] == deck_id, 'word_id'].to_list()

    def is_deck_owner(self, owner: str, deck_id: int):
        actual_owner = self.decks.loc[self.decks['id'] == deck_id, 'owner'].item()
        return owner == actual_owner

    def create_deck(self, owner: str, deck_name: str, lang: str):
        new_deck_id = len(self.decks)
        self.decks.loc[new_deck_id] = \
            dict(id=len(self.decks), owner=owner, name=deck_name, language=lang, tags=np.nan)
        return new_deck_id

    def save_decks_db(self):
        self.decks.to_csv(self.decks_path, index=False)
        self.deck_word.to_csv(self.deck_word_db_path, index=False)

    def add_new_word(self, deck_id: int, word_id: int):
        self.deck_word.loc[len(self.deck_word)] = \
            dict(deck_id=deck_id, word_id=word_id)

    def get_deck_name(self, deck_id: int):
        return self.decks.loc[self.decks['id'] == deck_id, 'name'].item()