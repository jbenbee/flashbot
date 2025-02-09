import math
from datetime import datetime, timedelta
from dataclasses import dataclass
import os
from typing import Dict, List, Optional
import jinja2
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from exercise import Exercise
from item import Item
from utils import get_assistant_response
from words_exercise import FlashcardExercise, WordsExerciseLearn, WordsExerciseTest


class LearningPlan:
    def __init__(self, interface, templates, words_progress_db=None, words_db=None, decks_db=None, user_config=None):
        self.progress_db = words_progress_db
        self.words_db = words_db
        self.decks_db = decks_db
        self.user_config = user_config
        self.interface = interface
        self.templates = templates
    
    def calculate_interval(self, item: Item) -> int:
        """Returns number of days until the next review."""
        if item.last_interval in [0, 1]:
            return 1
        elif item.last_interval == 2:
            return 6
        else:
            return math.ceil(item.last_interval * item.e_factor)
    
    def calculate_e_factor(self, item: Item, quality: int) -> float:
        new_ef = item.e_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        return max(1.3, new_ef)
    
    async def get_next_words_exercise(self, chat_id: str, lang: str, mode: Optional[str]=None) -> Optional[Exercise]:
        
        if not self.has_enough_words(chat_id, lang):
            await self.add_words(chat_id, lang)

        now = datetime.now().date()

        words_df = self.words_db.get_words_df()
        deck_words_df = self.decks_db.get_deck_word_df()

        deck_words_df = pd.merge(words_df, deck_words_df, how='inner', left_on='id', right_on='word_id', sort=False)
        user_decks = self.decks_db.get_user_decks(chat_id, lang)
        deck_words_df = deck_words_df[deck_words_df['deck_id'].isin(user_decks)]

        if deck_words_df.shape[0] == 0:
            return None

        progress_df = self.progress_db.get_progress_df()
        last_review_date = pd.to_datetime(progress_df['last_review_date']).dt.date
        n_done_today = progress_df.loc[(progress_df['chat_id'] == chat_id) & (last_review_date == now)].shape[0]
        n_tests_done_today = progress_df.loc[(progress_df['chat_id'] == chat_id) & (last_review_date == now) & (progress_df['num_reps'] > 0)].shape[0]
        user_data = self.user_config.get_user_data(chat_id)
        n_flashcards = user_data.get('n_flashcards', 5)
        if mode is None:
            if n_done_today == 0:
                mode  = 'learn'
            elif n_tests_done_today % n_flashcards == 0:
                mode = 'test_translation'
            else:
                mode = 'test_flashcard'

        if mode == 'test':
            mode = 'test_translation' if (n_tests_done_today > 0) and (n_tests_done_today % n_flashcards == 0) else 'test_flashcard'

        if mode in ['test_flashcard', 'test_translation']:
            progress_df = progress_df[progress_df['to_ignore'].isin([False, np.nan])]
            words_progress = pd.merge(progress_df, deck_words_df, how='left', left_on='word_id', right_on='word_id', sort=False)
            user_words_progress = words_progress.loc[(words_progress['lang'] == lang.lower()) & (words_progress['chat_id'] == chat_id)]

            if user_words_progress.shape[0] == 0:
                return None

            user_words_progress = user_words_progress.sort_values(by='next_review_date')
            to_review_mask = ((user_words_progress['next_review_date'] <= now) | user_words_progress['next_review_date'].isna())
            if to_review_mask.sum() > 0:
                to_review_words = user_words_progress[to_review_mask]
                row_item = to_review_words.iloc[0]
            else:
                row_item = user_words_progress.iloc[0]
        else:

            user_words_progress = pd.merge(progress_df, deck_words_df, how='right', left_on='word_id', right_on='id', sort=False)
            user_words_progress = user_words_progress[user_words_progress['to_ignore'].isin([False, np.nan])]

            if user_words_progress.shape[0] == 0:
                return None

            unseen_words = user_words_progress[user_words_progress['next_review_date'].isna()]

            if unseen_words.shape[0] > 0:
                row_item = unseen_words.sample(n=1).iloc[0]
            else:
                user_words_progress = user_words_progress.sort_values(by=['next_review_date', 'num_reps'])
                row_item = user_words_progress.sample(n=1).iloc[0]

        user_level = self.user_config.get_user_data(chat_id)['level']
        uilang = self.user_config.get_user_ui_lang(chat_id)
                
        if 'test_flashcard' == mode:
            exercise = FlashcardExercise(word=row_item['word'], word_id=row_item['id'].item(), lang=lang, uilang=uilang, level=user_level,
                                         interface=self.interface, templates=self.templates)
        elif 'test_translation' == mode:
            exercise = WordsExerciseTest(word=row_item['word'], word_id=row_item['id'].item(), lang=lang, uilang=uilang, level=user_level,
                                         interface=self.interface, templates=self.templates)
        else:
            exercise = WordsExerciseLearn(word=row_item['word'], meaning=row_item['meaning'], word_id=row_item['id'].item(), lang=lang, uilang=uilang,
                                          num_reps=row_item['num_reps'].item(), interface=self.interface,
                                          templates=self.templates)

        return exercise

    def get_due_today(self, chat_id: str, lang: str) -> List[str]:

        now = datetime.now().date()

        words_df = self.words_db.get_words_df()
        deck_words_df = self.decks_db.get_deck_word_df()

        deck_words_df = pd.merge(words_df, deck_words_df, how='inner', left_on='id', right_on='word_id', sort=False)
        user_decks = self.decks_db.get_user_decks(chat_id, lang)
        deck_words_df = deck_words_df[deck_words_df['deck_id'].isin(user_decks)]

        if deck_words_df.shape[0] == 0:
            return None

        progress_df = self.progress_db.get_progress_df()

        progress_df = progress_df[progress_df['to_ignore'].isin([False, np.nan])]
        words_progress = pd.merge(progress_df, deck_words_df, how='left', left_on='word_id', right_on='word_id', sort=False)
        user_words_progress = words_progress.loc[(words_progress['lang'] == lang.lower()) & (words_progress['chat_id'] == chat_id)]

        if user_words_progress.shape[0] == 0:
            return None
    
        user_words_progress = user_words_progress.sort_values(by='next_review_date')
        to_review_mask = ((user_words_progress['next_review_date'] <= now) | user_words_progress['next_review_date'].isna())
        if to_review_mask.sum() > 0:
            to_review_words = user_words_progress[to_review_mask]['word'].to_list()
        else:
            to_review_words = []

        return to_review_words


    def process_hint(self, chat_id: int, exercise: Exercise) -> None:
        item = self.progress_db.get_word_progress(chat_id, exercise.word_id)
        item.num_reps = 1
        item.last_interval = max(math.floor(item.last_interval / 2), 0)
        item.last_review_date = datetime.now()
        item.next_review_date = (datetime.now() + timedelta(days=1)).date()
        self.progress_db.set_word_progress(chat_id, exercise.word_id, item)

    def process_correct_answer(self, chat_id: int, exercise: Exercise) -> None:
        item = self.progress_db.get_word_progress(chat_id, exercise.word_id)
        item.num_reps = 1
        item.last_interval = 0
        item.last_review_date = datetime.now()
        item.next_review_date = (datetime.now() + timedelta(days=1)).date()
        self.progress_db.set_word_progress(chat_id, exercise.word_id, item)

    def process_response(self, chat_id: int, exercise: Exercise, quality: Optional[int]) -> None:

        item = self.progress_db.get_word_progress(chat_id, exercise.word_id)

        if quality is None:
            # a word was learned
            if item is None:
                self.progress_db.add_word_to_progress(chat_id, exercise.word_id)
            item = self.progress_db.get_word_progress(chat_id, exercise.word_id)
            now = datetime.now()
            item.last_review_date = now
            item.next_review_date = (now + timedelta(days=1)).date()
        elif quality is not None:
            # a word got tested

            if exercise.correct_answer_clicked or exercise.hint_clicked:
                # last interval and next review date are already updated
                return

            if not 0 <= quality <= 5:
                raise ValueError("Quality must be between 0 and 5")

            item.e_factor = self.calculate_e_factor(item, quality)

            if quality < 3:
                item.num_reps = 1
                item.last_interval = 0
            else:
                item.num_reps += 1
            
            new_interval = self.calculate_interval(item)
            item.last_interval = new_interval
            now = datetime.now()
            item.last_review_date = now
            item.next_review_date = (now + timedelta(days=new_interval)).date()

        self.progress_db.set_word_progress(chat_id, exercise.word_id, item)

    def set_word_easy(self, chat_id: int, word_id: int) -> None:
        item = self.progress_db.get_word_progress(chat_id, word_id)
        if item is None:
            raise ValueError(f'Word {word_id} is not found.')
        item.last_interval = 30
        new_interval = self.calculate_interval(item)
        item.next_review_date = (datetime.now() + timedelta(days=new_interval)).date()
        self.progress_db.set_word_progress(chat_id, word_id, item)

    def has_enough_words(self, chat_id, lang):
        progress_df = self.progress_db.get_progress_df()

        words_df = self.words_db.get_words_df()
        words_lang = words_df[words_df['lang'] == lang]

        all_seen_words = progress_df.loc[(progress_df['chat_id'] == chat_id)]
        lang_seen_words = pd.merge(all_seen_words, words_lang, how='inner', left_on='word_id', right_on='id', sort=False)

        user_decks = self.decks_db.get_user_decks(chat_id, lang)
        user_words = []
        for deck in user_decks:
            deck_words = self.decks_db.get_deck_words(deck)
            user_words += deck_words
        return lang_seen_words.shape[0] < len(user_words)

    async def add_words(self, chat_id, lang):

        user_data = self.user_config.get_user_data(chat_id)
        uilang = user_data['ui_language']

        words_df = self.words_db.get_words_df()

        if words_df.shape[0] == 0:
            return None

        progress_df = self.progress_db.get_progress_df()
        progress_words_df = pd.merge(progress_df, words_df, how='outer', left_on='word_id',
                                     right_on='id', sort=False)
        not_ignored_words = progress_words_df.loc[(progress_words_df['lang'] == lang.lower()) & (progress_words_df['chat_id'] == chat_id) &
                                                  progress_words_df['to_ignore'].isin([False, np.nan])]

        user_words_str = ', '.join(not_ignored_words['word'].to_list())

        message_template = self.templates.get_template(uilang, lang, 'gen_words')
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        query = template.render(lang=lang, user_words_str=user_words_str)

        model_base = os.getenv('MODEL_BASE')
        model_substitute = os.getenv('MODEL_SUBSTITUTE')

        class NewWordsSchema(BaseModel):
            class Config:
                extra = 'forbid'

            deck_theme: str
            deck_words: list[str]

        validation_cls = NewWordsSchema
        schema = validation_cls.model_json_schema()

        response_format = {
            "type": "json_schema",
            "json_schema": {"strict": True,
                            "name": "new_words",
                            "schema": schema
                            }
        }

        for i in range(3):
            assistant_response = await get_assistant_response(self.interface, query, model_base=model_base,
                                                    model_substitute=model_substitute, uilang=user_data['ui_language'],
                                                    response_format=response_format, validation_cls=validation_cls)
            words = assistant_response.deck_words

            new_words = [word for word in words if words_df[(words_df['word'] == word) & (words_df['lang'] == lang)].shape[0] == 0]
            if len(new_words) >= 15:
                break

        custom_deck_id = self.decks_db.get_custom_deck_id(str(chat_id), lang)

        for word in new_words:
            word_id = self.words_db.add_new_word(word, lang)
            self.decks_db.add_new_word(custom_deck_id, word_id)
        self.words_db.save_words_db()
        self.decks_db.save_decks_db()
