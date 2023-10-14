
import numpy as np
import pandas as pd

from reading_exercise import ReadingExercise
from words_exercise import WordsExerciseLearn, WordsExerciseTest


class LearningPlan:
    def __init__(self, words_progress_db=None, words_db=None, reading_db=None, user_config=None):
        self.progress_db = words_progress_db
        self.words_db = words_db
        self.reading_db = reading_db
        self.user_config = user_config

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

        # Words that are repeated at least this many times will be used for testing exercises,
        # others will be used for learning exercises.
        test_threshold = 2

        words_df = self.words_db.get_words_df()

        # choose word groups specific to the user
        current_word_group = self.user_config.get_user_data(chat_id)['current_word_group']
        user_words_df = words_df.loc[words_df['group'] == current_word_group]

        if user_words_df.shape[0] == 0:
            return None

        progress_df = self.progress_db.get_progress_df()
        progress_words_df = pd.merge(progress_df, user_words_df, how='outer', left_on='word_id',
                                     right_on='id', sort=False)
        not_ignored_words = progress_words_df.loc[(progress_words_df['lang'] == lang.lower()) &
                                                  progress_words_df['to_ignore'].isin([False, np.nan])]

        test_words_mask = not_ignored_words['num_reps'] >= test_threshold
        test_words = not_ignored_words.loc[test_words_mask]

        test_weights = [max(1/v[1], 0.001) for v in test_words['num_reps'].items()]
        learn_words = not_ignored_words[~test_words_mask]
        learn_weights = [0.5 if np.isnan(v[1]).item() else 1 + v[1] for v in learn_words['num_reps'].items()]

        if 'test' == mode:
            words = test_words
            weights = test_weights
        else:
            words = learn_words
            weights = learn_weights

        if words.shape[0] == 0:
            # there are no words for the selected mode
            print('Warning: There are 0 selected words.')
            return None

        next_exercise_data = words.sample(n=1, weights=np.array(weights))
        next_exercise_word = next_exercise_data['word'].item()
        next_exercise_word_id = next_exercise_data['id'].item()

        if 'test' == mode:
            exercise = WordsExerciseTest(word=next_exercise_word, word_id=next_exercise_word_id, lang=lang)
        else:
            exercise = WordsExerciseLearn(word=next_exercise_word, word_id=next_exercise_word_id, lang=lang)

        return exercise

    def get_next_exercise_like(self, exercise, chat_id, lang, mode):
        if isinstance(exercise, WordsExerciseLearn) or isinstance(exercise, WordsExerciseTest):
            next_exercise = self.get_next_words_exercise(chat_id, lang, mode)
        else:
            raise ValueError(f'Non-word exercises are not supported yet')
        return next_exercise
