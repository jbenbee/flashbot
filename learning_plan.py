
import numpy as np
import pandas as pd

from reading_exercise import ReadingExercise
from words_exercise import WordsExerciseLearn, WordsExerciseTest


class LearningPlan:
    def __init__(self, words_progress_db=None, words_db=None, decks_db=None, reading_db=None, user_config=None):
        self.progress_db = words_progress_db
        self.words_db = words_db
        self.decks_db = decks_db
        self.reading_db = reading_db
        self.user_config = user_config

        # Words that are repeated at least this many times will be used for testing exercises,
        # others will be used for learning exercises.
        self.test_threshold = 2

    def get_next_reading_exercise(self, chat_id, lang, topics=None):
        reading_data = self.reading_db.get_reading_data()
        if topics is None:
            # choose topics specific to the user
            topics = self.user_config.get_user_data(chat_id)['reading_topics']
        if len(topics) == 0:
            return None
        user_reading_data = {topic: reading_data[topic] for topic in topics}
        exercise = ReadingExercise(user_reading_data, lang=lang)
        return exercise

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
        next_word_num_reps = next_exercise_data['num_reps'].item()

        user_level = self.user_config.get_user_data(chat_id)['level']
        if 'test' == mode:
            exercise = WordsExerciseTest(word=next_exercise_word, word_id=next_exercise_word_id, lang=lang, level=user_level)
        else:
            exercise = WordsExerciseLearn(word=next_exercise_word, word_id=next_exercise_word_id, lang=lang, num_reps=next_word_num_reps)

        return exercise

    def get_next_exercise_like(self, exercise, chat_id, lang, mode):
        if isinstance(exercise, WordsExerciseLearn) or isinstance(exercise, WordsExerciseTest):
            next_exercise = self.get_next_words_exercise(chat_id, lang, mode)
        else:
            raise ValueError(f'Non-word exercises are not supported yet')
        return next_exercise
