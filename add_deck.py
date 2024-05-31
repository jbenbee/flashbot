import pandas as pd
from words_db import WordsDB
from decks_db import DecksDB


words_db = WordsDB('words_db.csv')
decks_db = DecksDB('decks_db.csv', 'deck_word.csv')
with open('words.txt', encoding='utf-8') as fp:
    word_list = [w.strip() for w in fp.read().split('\n')]

lang = 'uzbek'
chat_id = 'sdffewrwrwerw'
deck_name = 'Продукты'
deck_id = decks_db.create_deck(str(chat_id), deck_name, lang)
for word in word_list:
    if len(word) == 0: continue
    word_id = words_db.add_new_word(word, lang)
    decks_db.add_new_word(deck_id, word_id)
words_db.save_words_db()
decks_db.save_decks_db()


