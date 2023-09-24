import copy
import random

from exercise import Exercise


class ReadingExercise(Exercise):

    def __init__(self, exercise_data, lang):
        super().__init__()
        self.exercise_data = copy.deepcopy(exercise_data)
        self.topic = None
        self.lang = lang

    def repeat(self):
        # restart the exercise
        pass

    def get_next_message_to_user(self, query, assistant_response):
        mes_start = ''
        if self.topic is not None:
            mes_start = f'{self.topic}\n\n'
        mes = f'{mes_start}{assistant_response.strip()}'
        return mes

    def get_next_assistant_query(self, user_response) -> (str,int,bool):
        reading_ids = [k for k in self.exercise_data.keys()]
        ridx = random.randint(0, len(reading_ids) - 1)
        reading_id = reading_ids[ridx]
        next_exercise_data = self.exercise_data[reading_id]

        query = next_exercise_data["query"]
        tidx = random.randint(0, len(next_exercise_data["topics"]) - 1)
        query = query.replace('<TOPIC>', next_exercise_data["topics"][tidx])
        query = query.replace('<LANG>', self.lang)
        self.topic = next_exercise_data["topics"][tidx]
        is_last = True
        return query, is_last
