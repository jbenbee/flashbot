import math
import os
import random
import re
from typing import List, Optional
import jinja2
from utils import get_assistant_response

from pydantic import BaseModel, Field, ValidationError

from exercise import Exercise


class ExampleTestSentenceSchema(BaseModel):
    class Config:
        extra = 'forbid'

    example_sentence: str
    sentence_translation: str
    difficulty: int


class ExampleSentenceSchema(BaseModel):
    class Config:
        extra = 'forbid'

    example_sentence: str
    sentence_translation: str


class WordTestSchema(BaseModel):
    example_list: list[ExampleTestSentenceSchema]

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


class FlashCardExampleSchema(BaseModel):
    example: str = Field(..., description="Example sentence")
    translation_of_example: str = Field(..., description="Translate the example sentence")
    translation_of_word: str= Field(..., description="Translate the word itself")


    class Config:
        extra = 'forbid'


class FlashcardCorrectionSchema(BaseModel):
    translation_score: int
    score_justification: str

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
        self.is_responded = True

    async def get_next_user_message(self, user_response: Optional[str]) -> tuple[str, int]:
        message_template = self.templates.get_template(self.uilang, self.lang, 'learn_word_query')
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
        
        message_template = self.templates.get_template(self.uilang, self.lang, 'learn_word_user_message')
        examples = [(entry.example_sentence, entry.sentence_translation) for entry in assistant_response.example_list]
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        message = template.render(word=self.word, examples=examples, conjugations=assistant_response.conjugations)
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
        self.is_responded = False
        self.difficulty = 3

        self.assistant_responses = []
        self.user_messages = []
        self.next_query_idx = 0
        self.model_base = os.getenv('MODEL_BASE')
        self.model_substitute = os.getenv('MODEL_SUBSTITUTE')

    def correct_answer(self):
        return self.assistant_responses[0][self.difficulty - 1]['answer']
    
    def test_sentence(self):
        return self.assistant_responses[0][self.difficulty - 1]['test']

    async def get_next_user_message(self, user_response: Optional[str]):
        lang_tr = self.interface[self.lang][self.uilang]
        if user_response is None:
            # first message to the user

            message_template = self.templates.get_template(self.uilang, self.lang, 'test_word_query_1')
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
            
            examples = sorted(assistant_response.example_list, key=lambda x: x.difficulty)
            examples = [dict(test=item.sentence_translation, answer=item.example_sentence) for item in examples]

            self.difficulty = 3

            self.assistant_responses.append(examples)

            message_template = self.templates.get_template(self.uilang, self.lang, 'test_word_user_message_1')
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            message = template.render(lang=lang_tr, test_sentence=self.test_sentence())
            quality = None
            
        else:
            self.user_messages.append(user_response)

            message_template = self.templates.get_template(self.uilang, self.lang, 'test_word_query_2')

            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            query = template.render(lang=self.interface[self.lang][self.uilang],
                                    user_response=user_response, sentence=self.test_sentence(),
                                    word=self.word)

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
            message_template = self.templates.get_template(self.uilang, self.lang, 'test_word_user_message_2')
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            message = template.render(score=assistant_response.translation_score,
                                  justification=assistant_response.score_justification,
                                  explanation=assistant_response.mistakes_explanation,
                                  corrected_translation=assistant_response.corrected_translation,
                                  original_translation=self.correct_answer())
            quality = assistant_response.translation_score

        return message, quality
    

    def change_difficulty(self, easier: bool) -> None:
        lang_tr = self.interface[self.lang][self.uilang]

        if easier:
            self.difficulty = max(1, self.difficulty - 1)
        else:
            self.difficulty = min(5, self.difficulty + 1)

        message_template = self.templates.get_template(self.uilang, self.lang, 'test_word_user_message_1')
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        message = template.render(lang=lang_tr, test_sentence=self.test_sentence())
        return message


class FlashcardExercise(Exercise):
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
        self.is_responded = False

        self.assistant_responses = []
        self.user_messages = []
        self.next_query_idx = 0
        self.model_base = os.getenv('MODEL_BASE')
        self.model_substitute = os.getenv('MODEL_SUBSTITUTE')

    def correct_answer(self):
        return f'{self.word}\n\n{self.interface["Example"][self.uilang]}: {self.assistant_responses[0]["example"]}'

    async def get_next_user_message(self, user_response: Optional[str]):
        lang_tr = self.interface[self.lang][self.uilang]
        if user_response is None:
            # first message to the user
            message_template = self.templates.get_template(self.uilang, self.lang, 'flashcard_query_1')
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            query = template.render(word=self.word, level=self.level, lang=lang_tr, lang_ui=self.uilang)

            validation_cls = FlashCardExampleSchema
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
            
            self.assistant_responses.append(dict(example=assistant_response.example, translation_example=assistant_response.translation_of_example,
                                                 translation_word=assistant_response.translation_of_word))

            message_template = self.templates.get_template(self.uilang, self.lang, 'flashcard_user_message_1')
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            message = template.render(lang=lang_tr, lang_ui=self.uilang, word=assistant_response.translation_of_word, example=assistant_response.translation_of_example)
            quality = None
            
        else:
            self.user_messages.append(user_response)

            message_template = self.templates.get_template(self.uilang, self.lang, 'flashcard_query_2')

            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            query = template.render(lang=self.interface[self.lang][self.uilang], user_response=user_response,
                                    word_translation=self.assistant_responses[-1]['translation_word'], correct_answer=self.word)

            validation_cls = FlashcardCorrectionSchema
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
            message_template = self.templates.get_template(self.uilang, self.lang, 'flashcard_user_message_2')
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            correct_answer = self.word if assistant_response.translation_score < 5 else None
            message = template.render(score=assistant_response.translation_score,
                                justification=assistant_response.score_justification,
                                correct_answer=correct_answer)
            quality = assistant_response.translation_score

        return message, quality
