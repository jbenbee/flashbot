import math
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from exercise import Exercise
from item import Item
from words_exercise import WordsExerciseLearn, WordsExerciseTest


class _LearningPlan:
    def __init__(self, interface, templates, words_progress_db=None, words_db=None, decks_db=None, user_config=None):
        self.progress_db = words_progress_db
        self.words_db = words_db
        self.decks_db = decks_db
        self.user_config = user_config
        self.interface = interface
        self.templates = templates

        # Words that are repeated at least this many times will be used for testing exercises,
        # others will be used for learning exercises.
        self.test_threshold = 2

    def get_next_words_exercise(self, chat_id, lang, mode):

        words_df = self.words_db.get_words_df()

        # choose words from the deck
        current_deck_id = self.user_config.get_user_data(chat_id)['current_deck_id']
        deck_words_df = words_df.loc[words_df['id'].isin(self.decks_db.get_deck_words(current_deck_id))]

        if deck_words_df.shape[0] == 0:
            return None

        progress_df = self.progress_db.get_progress_df()
        progress_words_df = pd.merge(progress_df, deck_words_df, how='outer', left_on='word_id',
                                     right_on='id', sort=False)
        not_ignored_words = progress_words_df.loc[(progress_words_df['lang'] == lang.lower()) &
                                                  progress_words_df['to_ignore'].isin([False, np.nan])]

        test_words_mask = not_ignored_words['num_reps'] >= self.test_threshold
        test_words = not_ignored_words.loc[test_words_mask]

        test_weights = [max(1/v[1], 0.001) for v in test_words['num_reps'].items()]

        not_ignored_learn_words = not_ignored_words[~test_words_mask]
        seen_words = not_ignored_learn_words.loc[~not_ignored_learn_words['num_reps'].isna()]

        if seen_words.shape[0] > 3:
            learn_words = seen_words
        else:
            learn_words = not_ignored_learn_words

        learn_weights = [1] * len(learn_words)

        if 'test' == mode:
            words = test_words
            weights = test_weights
        elif 'learn' == mode:
            words = learn_words
            weights = learn_weights
        elif ('repeat_test' == mode) or ('repeat_learn' == mode):
            # choose not ignored words that have been seen least often
            min_num_reps = not_ignored_words['num_reps'].min()
            words = not_ignored_words.loc[not_ignored_words['num_reps'] == min_num_reps]
            weights = [1] * len(words)
        else:
            return None

        if words.shape[0] == 0:
            # there are no words for the selected mode
            print('Warning: There are 0 selected words.')
            return None

        next_exercise_data = words.sample(n=1, weights=np.array(weights))
        next_exercise_word = next_exercise_data['word'].item()
        next_exercise_word_id = next_exercise_data['id'].item()
        next_exercise_word_meaning = next_exercise_data['meaning'].item()
        next_word_num_reps = next_exercise_data['num_reps'].item()

        user_data = self.user_config.get_user_data(chat_id)
        show_test_metrics = False if 'show_test_metrics' not in user_data.keys() else user_data['show_test_metrics']
        user_level = self.user_config.get_user_data(chat_id)['level']
        uilang = self.user_config.get_user_ui_lang(chat_id)
        if 'test' == mode:
            exercise = WordsExerciseTest(word=next_exercise_word, word_id=next_exercise_word_id, lang=lang, uilang=uilang, level=user_level, interface=self.interface, templates=self.templates,
                                         add_metrics=show_test_metrics)
        else:
            exercise = WordsExerciseLearn(word=next_exercise_word, meaning=next_exercise_word_meaning,
                                          word_id=next_exercise_word_id, lang=lang, uilang=uilang, num_reps=next_word_num_reps, interface=self.interface,
                                          templates=self.templates)

        return exercise

    # def get_next_exercise_like(self, exercise, chat_id, lang, mode):
    #     if isinstance(exercise, WordsExerciseLearn) or isinstance(exercise, WordsExerciseTest):
    #         next_exercise = self.get_next_words_exercise(chat_id, lang, mode)
    #     else:
    #         raise ValueError(f'Non-word exercises are not supported yet')
    #     return next_exercise


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
    
    def get_next_words_exercise(self, chat_id: str, lang: str, mode: Optional[str]=None) -> Optional[Exercise]:
        """Get all items that need to be reviewed"""
        now = datetime.now().date()

        words_df = self.words_db.get_words_df()

        current_deck_id = self.user_config.get_user_data(chat_id)['current_deck_id']
        deck_words_df = words_df.loc[words_df['id'].isin(self.decks_db.get_deck_words(current_deck_id))]

        if deck_words_df.shape[0] == 0:
            return None

        progress_df = self.progress_db.get_progress_df()
        progress_words_df = pd.merge(progress_df, deck_words_df, how='outer', left_on='word_id',
                                     right_on='id', sort=False)
        not_ignored_words = progress_words_df.loc[(progress_words_df['lang'] == lang.lower()) &
                                                  progress_words_df['to_ignore'].isin([False, np.nan])]

        if not_ignored_words.shape[0] == 0:
            return None

        row_item = None
        if 'test' == mode or mode is None:
            not_ignored_seen_words = not_ignored_words[~not_ignored_words['num_reps'].isna()]
            not_ignored_seen_words = not_ignored_seen_words.sort_values(by='next_review_date')
            to_review_mask = not_ignored_seen_words['next_review_date'].isna() | (not_ignored_seen_words['next_review_date'] <= now)
            if to_review_mask.sum() > 0:
                to_review_words = not_ignored_seen_words[to_review_mask]
                row_item = to_review_words.iloc[0]
            else:
                row_item = not_ignored_seen_words.iloc[0]
        elif 'learn' == mode:
            not_seen_words = not_ignored_words[not_ignored_words['num_reps'].isna()]
            if not_seen_words.shape[0] > 0:
                row_item = not_seen_words.iloc[0]
            else:
                not_ignored_words = not_ignored_words.sort_values(by='num_reps')
                row_item = not_ignored_words.iloc[0]
        elif ('repeat_test' == mode) or ('repeat_learn' == mode):
            # choose not ignored words that have been seen least often
            min_num_reps = not_ignored_words['num_reps'].min()
            row_item = not_ignored_words.loc[not_ignored_words['num_reps'] == min_num_reps].iloc[0]

        if row_item is None:
            # there are no words for the selected mode
            print('There are no words for the selected mode.')
            return None

        show_test_metrics = False
        user_level = self.user_config.get_user_data(chat_id)['level']
        uilang = self.user_config.get_user_ui_lang(chat_id)
        if 'test' == mode:
            exercise = WordsExerciseTest(word=row_item['word'], word_id=row_item['id'].item(), lang=lang, uilang=uilang, level=user_level,
                                         interface=self.interface, templates=self.templates)
        else:
            exercise = WordsExerciseLearn(word=row_item['word'], meaning=row_item['meaning'], word_id=row_item['id'].item(), lang=lang, uilang=uilang,
                                          num_reps=row_item['num_reps'].item(), interface=self.interface,
                                          templates=self.templates)

        return exercise

    def process_response(self, chat_id: int, exercise: Exercise, quality: Optional[int]) -> None:
        
        item = self.progress_db.get_word_progress(chat_id, exercise.word_id)

        if quality is None and item is None:
            # a word was learned (quality is None) for the first time (item is None)
            self.progress_db.add_word_to_progress(chat_id, exercise.word_id)
            item = self.progress_db.get_word_progress(chat_id, exercise.word_id)
            now = datetime.now()
            item.next_review_date = (now + timedelta(days=1)).date()
        elif quality is not None:
            # a word got tested
            if not 0 <= quality <= 5:
                raise ValueError("Quality must be between 0 and 5")

            if exercise.hint_clicked:
                quality = int(quality * 0.85)
            elif exercise.correct_answer_clicked:
                quality = int(quality * 0.6)

            item.e_factor = self.calculate_e_factor(item, quality)

            if quality < 3:
                item.num_reps = 0
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