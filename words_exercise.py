import math
import random
import re
from typing import List, Optional

from pydantic import BaseModel, Extra, ValidationError

from exercise import Exercise


class WordExampleSchema(BaseModel):
    examples: List[str]
    conjugations: Optional[str]

    class Config:
        extra = 'forbid'


class ExampleSentenceSchema(BaseModel):
    class Config:
        extra = 'forbid'

    example_sentence: str
    sentence_translation: str


class WordTestSchema(BaseModel):
    examples: List[ExampleSentenceSchema]

    class Config:
        extra = 'forbid'


class ResponseCorrectionSchema(BaseModel):
    translation_score: int
    score_justification: str
    mistakes_explanation: Optional[str]

    class Config:
        extra = 'forbid'


class WordsExerciseLearn(Exercise):
    def __init__(self, word, meaning, word_id, lang, uilang, num_reps, interface):
        super().__init__()
        self.word = word
        self.meaning = meaning
        self.word_id = word_id
        self.lang = lang
        self.uilang = uilang
        self.interface = interface
        self.num_reps = num_reps + 1 if not math.isnan(num_reps) else 1

    def repeat(self):
        pass

    def get_next_message_to_user(self, query, assistant_response):
        # message = assistant_response.replace('**', '')

        message = f'{self.interface["Learning word"][self.uilang]} "{self.word}" (# {self.interface["of repetitions"][self.uilang]}: {int(self.num_reps)}):\n\n'
        message += self.interface["Example usage"][self.uilang] + ':\n' + '\n'.join([f'- {example}' for example in assistant_response.examples])
        if assistant_response.conjugations is not None:
            message += f'\n\n{self.interface["Conjugations in present tense"][self.uilang]}:\n' + assistant_response.conjugations

        return message

    def get_next_assistant_query(self, user_response) -> (str,int,bool):
        lang_tr = self.interface[self.lang][self.uilang]
        meaning = f' {self.interface["to mean"][self.uilang]} "{self.meaning}"' if isinstance(self.meaning, str) else ''
        word_phrase = "word" if len(self.word.split()) == 1 else "phrase"
        qp1 = f"Show me 3 examples of how to use the {word_phrase}"
        query = f'{self.interface[qp1][self.uilang]} "{self.word}"{meaning} {self.interface["in"][self.uilang]} {lang_tr}. ' \
                f'{self.interface["If the word is a verb, show its conjugations in present tense for all subjects"][self.uilang]}.'
        is_last = True

        schema = WordExampleSchema.model_json_schema()

        response_format = {
            "type": "json_schema",
            "json_schema": {"strict": True,
                            "name": "word_example",
                            "schema": schema
                            }
        }

        return query, response_format, WordExampleSchema, is_last


class WordsExerciseTest(Exercise):
    def __init__(self, word, word_id, lang, uilang, level, interface, add_metrics=False):
        super().__init__()
        self.word = word
        self.word_id = word_id
        self.lang = lang
        self.uilang = uilang
        self.level = level
        self.interface = interface
        self.add_metrics = add_metrics
        self.n_examples = 1

        self.is_first_message_to_user = True
        self.assistant_responses = []
        self.user_messages = []
        self.next_query_idx = 0
        with open(f'resources/words_test_analysis_prompt_{uilang}.txt', 'r', encoding='utf-8') as fp:
            self.analysis_pre_prompt = fp.read()

        with open(f'resources/words_test_exercise_prompt_{uilang}.txt', 'r', encoding='utf-8') as fp:
            self.exercise_pre_prompt = fp.read()

    def selected_test(self):
        return self.assistant_responses[0]['test']

    def repeat(self):
        self.is_first_message_to_user = True
        self.next_query_idx = 0

    def correct_answer(self):
        return self.assistant_responses[0]['answer']

    def get_next_message_to_user(self, query, assistant_response):

        # parse the response
        if self.is_first_message_to_user:

            examples = assistant_response.examples

            # choose randomly an example
            ridx = random.randint(0, len(examples) - 1)
            sentence = examples[ridx]

            answer_sentence = sentence.example_sentence
            test_sentence = sentence.sentence_translation

            self.assistant_responses.append(dict(test=test_sentence, answer=answer_sentence))

            lang_tr = self.interface[self.lang][self.uilang]
            self.is_first_message_to_user = False
            mes = f'{self.interface["Translate into"][self.uilang]} {lang_tr}:\n' \
                  f'\n{test_sentence}\n'
        else:
            mes = f'{self.interface["Translation score"][self.uilang]}: {assistant_response.translation_score}\n\n' \
                  f'{self.interface["Score justification"][self.uilang]}: {assistant_response.score_justification}'
            if assistant_response.mistakes_explanation is not None:
                mes += f'\n\n{self.interface["Explanation of mistakes"][self.uilang]}: {assistant_response.mistakes_explanation}'

        return mes

    def get_next_answer_test_query(self, user_response):
        lang_tr = self.interface[self.lang][self.uilang]
        if self.is_first_message_to_user:
            # part_of_speech = random.choice(['verb', 'noun', 'name', 'adverb', 'pronoun'])

            query = f'{self.interface["Show me 3 examples of using the word or phrase"][self.uilang]} "{self.word}" ' \
                    f'{self.interface["in language"][self.uilang]} "{lang_tr}" {self.interface["at level of proficiency"][self.uilang]} "{self.level}". ' \
                    f'{self.interface[f"Start examples with a verb, noun, name, adverb or pronoun"][self.uilang]}.'

            validation_cls = WordTestSchema
            schema = validation_cls.model_json_schema()

            response_format = {
                "type": "json_schema",
                "json_schema": {"strict": True,
                                "name": "word_example",
                                "schema": schema
                                }
            }

        else:
            self.user_messages.append(user_response)
            # TODO: add russian?
            query = "Rate the following translation on a scale from 1 to 5 according to how grammatically correct and natural it is. " \
                    "1 means the translation has several major mistakes, 5 means that the translation is correct and it could have been written by a native speaker." \
                    "Justify your score in one sentence and if the translation has mistakes, explain them.\n" \
                    f"Sentence: {self.assistant_responses[0]['test']}\n" \
                    f"Translation: {user_response}"

            validation_cls = ResponseCorrectionSchema
            schema = validation_cls.model_json_schema()

            response_format = {
                "type": "json_schema",
                "json_schema": {"strict": True,
                                "name": "word_example",
                                "schema": schema
                                }
            }

        return query, response_format, validation_cls

    def get_next_assistant_query(self, user_response):
        query, response_format, validation_cls = self.get_next_answer_test_query(user_response)
        is_last = True if self.next_query_idx == 0 else False
        self.next_query_idx += 1

        return query, response_format, validation_cls, is_last
