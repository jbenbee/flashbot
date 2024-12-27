import math
import os
import random
import re
from typing import List, Optional
import jinja2
from utils import get_assistant_response

from pydantic import BaseModel, ValidationError

from exercise import Exercise


class ExampleSentenceSchema(BaseModel):
    class Config:
        extra = 'forbid'

    example_sentence: str
    sentence_translation: str


class WordTestSchema(BaseModel):
    example_list: list[ExampleSentenceSchema]

    class Config:
        extra = 'forbid'


class WordExamplesSchema(BaseModel):
    example_list: list[ExampleSentenceSchema]
    conjugations: Optional[str]

    class Config:
        extra = 'forbid'


class ResponseCorrectionSchema(BaseModel):
    translation_score: int
    score_justification: str
    mistakes_explanation: Optional[str]
    corrected_translation: str

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
        self.model_base = os.getenv('MODEL_BASE')
        self.model_substitute = os.getenv('MODEL_SUBSTITUTE')

    async def get_next_user_message(self, user_response: Optional[str]) -> tuple[str, int]:
        message_template = self.templates[self.uilang]['learn_word_query']
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        word_phrase = "word" if len(self.word.split()) == 1 else "phrase"
        lang_tr = self.interface[self.lang][self.uilang]
        query = template.render(word_phrase=word_phrase, word=self.word, meaning=self.meaning, lang=lang_tr)

        schema = WordExamplesSchema.model_json_schema()

        response_format = {
            "type": "json_schema",
            "json_schema": {"strict": True,
                            "name": "word_example",
                            "schema": schema
                            }
        }

        assistant_response = await get_assistant_response(self.interface, query, uilang=self.uilang, model_base=self.model_base,
                                                          model_substitute=self.model_substitute, response_format=response_format, validation_cls=WordExamplesSchema)
        
        message_template = self.templates[self.uilang]['learn_word_user_message']
        examples = [(entry.example_sentence, entry.sentence_translation) for entry in assistant_response.example_list]
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        message = template.render(word=self.word, num_reps=self.num_reps, examples=examples, conjugations=assistant_response.conjugations)
        return message, None


class WordsExerciseTest(Exercise):
    def __init__(self, word, word_id, lang, uilang, level, interface, templates):
        super().__init__()
        self.word = word
        self.word_id = word_id
        self.lang = lang
        self.uilang = uilang
        self.level = level
        self.interface = interface
        self.templates = templates
        self.n_examples = 1
        self.hint_clicked = False
        self.correct_answer_clicked = False

        self.assistant_responses = []
        self.user_messages = []
        self.next_query_idx = 0
        self.model_base = os.getenv('MODEL_BASE')
        self.model_substitute = os.getenv('MODEL_SUBSTITUTE')

    def correct_answer(self):
        return self.assistant_responses[0]['answer']

    async def get_next_user_message(self, user_response: Optional[str]):
        lang_tr = self.interface[self.lang][self.uilang]
        if user_response is None:
            # first message to the user

            message_template = self.templates[self.uilang]['test_word_query_1']
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
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

            assistant_response = await get_assistant_response(self.interface, query, model_base=self.model_base,
                                                        model_substitute=self.model_substitute, uilang=self.uilang,
                                                        response_format=response_format, validation_cls=validation_cls)
            
            examples = assistant_response.example_list

            # choose randomly an example
            ridx = random.randint(0, len(examples) - 1)
            sentence = examples[ridx]

            answer_sentence = sentence.example_sentence
            test_sentence = sentence.sentence_translation

            self.assistant_responses.append(dict(test=test_sentence, answer=answer_sentence))

            message_template = self.templates[self.uilang]['test_word_user_message_1']
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            message = template.render(lang=lang_tr, test_sentence=test_sentence)
            quality = None
            
        else:
            self.user_messages.append(user_response)

            message_template = self.templates[self.uilang]['test_word_query_2']

            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            query = template.render(lang=self.interface[self.lang][self.uilang], user_response=user_response, sentence=self.assistant_responses[0]['test'])

            validation_cls = ResponseCorrectionSchema
            schema = validation_cls.model_json_schema()

            response_format = {
                "type": "json_schema",
                "json_schema": {"strict": True,
                                "name": "word_example",
                                "schema": schema
                                }
            }

            assistant_response = await get_assistant_response(self.interface, query, model_base=self.model_base,
                                                        model_substitute=self.model_substitute, uilang=self.uilang,
                                                        response_format=response_format, validation_cls=validation_cls)
            message_template = self.templates[self.uilang]['test_word_user_message_2']
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            message = template.render(score=assistant_response.translation_score,
                                  justification=assistant_response.score_justification,
                                  explanation=assistant_response.mistakes_explanation,
                                  corrected_translation=assistant_response.corrected_translation)
            quality = assistant_response.translation_score

        return message, quality
