import math
import random
import re

import evaluate

from exercise import Exercise


class WordsExerciseLearn(Exercise):
    def __init__(self, word, word_id, lang, uilang, num_reps, interface):
        super().__init__()
        self.word = word
        self.word_id = word_id
        self.lang = lang
        self.uilang = uilang
        self.interface = interface
        self.num_reps = num_reps + 1 if not math.isnan(num_reps) else 1

        with open(f'resources/words_example_prompt_{uilang}.txt', 'r', encoding='utf-8') as fp:
            self.example_pre_prompt = fp.read()

    def repeat(self):
        pass

    def get_next_message_to_user(self, query, assistant_response):
        mes = f'{self.interface["Learning word"][self.uilang]} "{self.word}" (# {self.interface["of repetitions"][self.uilang]}: {int(self.num_reps)}):\n\n{assistant_response}'
        return mes

    def get_next_assistant_query(self, user_response) -> (str,int,bool):
        lang_tr = self.interface[self.lang][self.uilang]
        query = self.example_pre_prompt + '\n\n' + \
                f'{self.interface["User: Examples in"][self.uilang]} "{lang_tr}" {self.interface["of how to use the following word or frase"][self.uilang]}: {self.word}. ' \
                f'{self.interface["If the word is a verb, add its conjugations in present tense for all subjects"][self.uilang]}.\n' \
                f'{self.interface["Assistant"][self.uilang]}:\n'
        is_last = True
        return query, is_last


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

        try:
            self.bleu = evaluate.load("bleu")
            self.meteor = evaluate.load("meteor")
            self.rouge = evaluate.load("rouge")
        except Exception as e:
            print(f'Could not load metrics: {e}')
            self.bleu = None
            self.meteor = None
            self.rouge = None

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

            examples = assistant_response.split('\n')
            examples = [sen for sen in examples if len(sen.strip()) > 0]

            if len(examples) != self.n_examples or 'Ã¨' in assistant_response:
                raise ValueError('Assistant responded in a wrong pattern.')

            # choose randomly one of the {self.n_examples} examples
            ridx = random.randint(0, self.n_examples - 1)
            sentences = examples[ridx]

            res = re.search(r'([0-9]+\.\s)?([^\.?!]+[\.?!])\s([^\.?!]+[\.?!])', sentences)
            groups = res.groups()
            if len(groups) % 2 == 1:
                # skip example number
                groups = groups[1:]
            test_idx = len(groups)//2
            test_sentence = ' '.join(groups[test_idx:])
            answer_sentence = ' '.join(groups[:test_idx])

            self.assistant_responses.append(dict(test=test_sentence, answer=answer_sentence))

            lang_tr = self.interface[self.lang][self.uilang]
            self.is_first_message_to_user = False
            mes = f'{self.interface["Translate into"][self.uilang]} {lang_tr}:\n' \
                  f'\n{test_sentence}\n'
        else:
            metrics_str = ''
            if self.add_metrics:
                predictions = [self.user_messages[-1]]
                references = [self.correct_answer()]

                metrics_str = ''
                if all([metric is not None for metric in [self.bleu, self.meteor, self.rouge]]):
                    try:
                        bleu_score = self.bleu.compute(predictions=predictions, references=references)['bleu']
                        meteor_score = self.meteor.compute(predictions=predictions, references=references)['meteor']
                        rouge_scores = self.rouge.compute(predictions=predictions, references=references)
                        metrics_str = f'Metrics (user vs reference):\n' \
                                      f'BLEU: {bleu_score:.3f}\n' \
                                      f'METEOR: {meteor_score:.3f}\n' \
                                      f'ROUGE: {rouge_scores["rouge1"]:.3f}, {rouge_scores["rouge2"]:.3f}, {rouge_scores["rougeL"]:.3f}, {rouge_scores["rougeLsum"]:.3f}\n'
                    except Exception as e:
                        print(f'Something is wrong with "evaluate" package: {e}')
            mes = f'{self.interface["Reference translation"][self.uilang]}: {self.correct_answer()}\n\n' \
                  f'{metrics_str}' \
                  f'{self.interface["Corrections"][self.uilang]}:\n{assistant_response}'
        return mes

    def get_next_answer_test_query(self, user_response):
        lang_tr = self.interface[self.lang][self.uilang]
        if self.is_first_message_to_user:
            query = self.exercise_pre_prompt + '\n\n' + \
                    f'{self.interface["User: Show me an example of using the word or phrase"][self.uilang]} "{self.word}" ' \
                    f'{self.interface["in language"][self.uilang]} "{lang_tr}" {self.interface["at level of proficiency"][self.uilang]} "{self.level}":\n' \
                    f'{self.interface["Assistant"][self.uilang]}:\n'

        else:
            self.user_messages.append(user_response)
            query = self.analysis_pre_prompt + '\n\n' + \
                    f'{self.interface["Translation"][self.uilang]}: {self.assistant_responses[0]["test"]} -> {user_response}\n' \
                    f'{self.interface["Suggested word"][self.uilang]}: {self.word}\n' \
                    f'{self.interface["Correction"][self.uilang]}: '
        return query

    def get_next_assistant_query(self, user_response) -> (str,int,bool):
        query = self.get_next_answer_test_query(user_response)
        is_last = True if self.next_query_idx == 0 else False
        self.next_query_idx += 1

        return query, is_last
