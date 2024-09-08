import math
import random
import re
from typing import List, Optional
import jinja2

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
    def __init__(self, word, meaning, word_id, lang, uilang, num_reps, interface, templates):
        super().__init__()
        self.word = word
        self.meaning = meaning
        self.word_id = word_id
        self.lang = lang
        self.uilang = uilang
        self.interface = interface
        self.templates = templates
        self.num_reps = num_reps + 1 if not math.isnan(num_reps) else 1

    def repeat(self):
        pass

    def get_next_message_to_user(self, query, assistant_response):

        environment = jinja2.Environment()
        message_template = self.templates[self.uilang]['learn_word_user_message']
        example_usage = '\n'.join([f'- {example}' for example in assistant_response.examples])
        template = environment.from_string(message_template)
        message = template.render(word=self.word, num_reps=self.num_reps, example_usage=example_usage, conjugations=assistant_response.conjugations)

        return message

    def get_next_assistant_query(self, user_response):

        environment = jinja2.Environment()
        message_template = self.templates[self.uilang]['learn_word_query']
        template = environment.from_string(message_template)
        word_phrase = "word" if len(self.word.split()) == 1 else "phrase"
        lang_tr = self.interface[self.lang][self.uilang]
        query = template.render(word_phrase=word_phrase, word=self.word, meaning=self.meaning, lang=lang_tr)

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
    def __init__(self, word, word_id, lang, uilang, level, interface, templates, add_metrics=False):
        super().__init__()
        self.word = word
        self.word_id = word_id
        self.lang = lang
        self.uilang = uilang
        self.level = level
        self.interface = interface
        self.templates = templates
        self.add_metrics = add_metrics
        self.n_examples = 1

        self.is_first_message_to_user = True
        self.assistant_responses = []
        self.user_messages = []
        self.next_query_idx = 0

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

            environment = jinja2.Environment()
            message_template = self.templates[self.uilang]['test_word_user_message_1']
            template = environment.from_string(message_template)
            mes = template.render(lang=lang_tr, test_sentence=test_sentence)

        else:
            environment = jinja2.Environment()
            message_template = self.templates[self.uilang]['test_word_user_message_2']
            template = environment.from_string(message_template)
            mes = template.render(score=assistant_response.translation_score,
                                  justification=assistant_response.score_justification,
                                  explanation=assistant_response.mistakes_explanation)

        return mes

    def get_next_answer_test_query(self, user_response):
        lang_tr = self.interface[self.lang][self.uilang]
        if self.is_first_message_to_user:
            # part_of_speech = random.choice(['verb', 'noun', 'name', 'adverb', 'pronoun'])

            environment = jinja2.Environment()
            message_template = self.templates[self.uilang]['test_word_query_1']
            template = environment.from_string(message_template)
            query = template.render(word=self.word, lang=lang_tr, level=self.level)

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

            environment = jinja2.Environment()
            message_template = self.templates[self.uilang]['test_word_query_2']
            template = environment.from_string(message_template)
            query = template.render(user_response=user_response, sentence=self.assistant_responses[0]['test'])

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
