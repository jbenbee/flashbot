import math
import re

import evaluate

from exercise import Exercise


class WordsExerciseLearn(Exercise):
    def __init__(self, word, word_id, lang, num_reps):
        super().__init__()
        self.word = word
        self.word_id = word_id
        self.lang = lang
        self.num_reps = num_reps + 1 if not math.isnan(num_reps) else 1

    def repeat(self):
        pass

    def get_next_message_to_user(self, query, assistant_response):
        mes = f'Learning word "{self.word}" (# of repetitions: {int(self.num_reps)}):\n\n{assistant_response}'
        return mes

    def get_next_assistant_query(self, user_response) -> (str,int,bool):
        query = f"User: 3 simple examples in italian of how to use the following word or frase: attendere.\n"\
                f"Assistant:\n"\
                f"1. Potresti attendere un momento prima di parlare? Can you wait a moment before talking?\n"\
                f"2. Devo attendere il treno delle 16.45. I have to wait for the 4.45 p.m. train.\n"\
                f"3. Attendiamo il tuo arrivo con ansia! We are eagerly waiting for your arrival!\n"\
                f"User: 3 simple examples in {self.lang} of how to use the following word or frase: {self.word}.\n"\
                f"Assistant:\n"
        is_last = True
        return query, is_last


class WordsExerciseTest(Exercise):
    def __init__(self, word, word_id, lang, level, add_metrics=False):
        super().__init__()
        self.word = word
        self.word_id = word_id
        self.lang = lang
        self.level = level
        self.add_metrics = add_metrics

        self.is_first_message_to_user = True
        self.assistant_responses = []
        self.user_messages = []
        self.next_query_idx = 0
        with open('resources/words_test_analysis_prompt.txt') as fp:
            self.analysis_pre_prompt = fp.read()

        with open('resources/words_test_exercise_prompt.txt') as fp:
            self.exercise_pre_prompt = fp.read()

    def repeat(self):
        self.is_first_message_to_user = True
        self.next_query_idx = 0

    def correct_answer(self):
        return self.assistant_responses[0]["answer"]

    def get_next_message_to_user(self, query, assistant_response):
        # parse the response
        if self.is_first_message_to_user:

            sentences = assistant_response.split('\n')
            sentences = [sen for sen in sentences if len(sen.strip()) > 0]
            if len(sentences) != 2:
                raise ValueError('Assistant responded in a wrong pattern.')
            test_sentence = sentences[1]
            test_sentence = re.sub(r'(?:Sentence)?\s?2[:.]?', '', test_sentence,
                   flags=re.IGNORECASE)
            test_sentence = test_sentence.strip()

            answer_sentence = sentences[0]
            answer_sentence = re.sub(r'(?:Sentence)?\s?1[:.]?', '', answer_sentence,
                   flags=re.IGNORECASE)
            answer_sentence = answer_sentence.strip()

            self.assistant_responses.append(dict(test=test_sentence, answer=answer_sentence))

            self.is_first_message_to_user = False
            mes = f'Translate into {self.lang}:\n' \
                  f'\n{test_sentence}\n'
        else:
            metrics_str = ''
            if self.add_metrics:
                predictions = [self.user_messages[-1]]
                references = [self.correct_answer()]

                try:
                    bleu = evaluate.load("evaluate/metrics/bleu")
                    bleu_score = bleu.compute(predictions=predictions, references=references)['bleu']
                    meteor = evaluate.load("evaluate/metrics/meteor")
                    meteor_score = meteor.compute(predictions=predictions, references=references)['meteor']
                    # TODO: fix below when they fix "evaluate"
                    # rouge = evaluate.load("evaluate/metrics/rouge")
                    # rouge_scores = rouge.compute(predictions=predictions, references=references)
                    # bert = evaluate.load("evaluate/metrics/bertscore")
                    # bert_scores = bert.compute(predictions=predictions, references=references)
                    metrics_str = f'Metrics (user vs reference):\n' \
                                  f'BLEU: {bleu_score}\n' \
                                  f'METEOR: {meteor_score}\n' \
                                  f'\n'
                        # f'BERT: F1: {bert_scores[-1]}, precision: {bert_scores[0]}, recall: {bert_scores[1]}\n\n'
                    # f'ROUGE: {rouge_scores["rouge1"]}, {rouge_scores["rouge2"]}, {rouge_scores["rougeL"]}, {rouge_scores["rougeLsum"]}\n' \
                except Exception as e:
                    print('Something is wrong with "evaluate" package, skipping the metrics.')
                    metrics_str = ''
            mes = f'Reference translation: {self.correct_answer()}\n\n' \
                  f'{metrics_str}' \
                  f'Corrections:\n{assistant_response}'
        return mes

    def get_next_answer_test_query(self, user_response):
        if self.is_first_message_to_user:
            query = self.exercise_pre_prompt + '\n\n' + \
                    f'User: Example of using the {self.lang} word or phrase "{self.word}" in everyday life at {self.level} level of proficiency:\n' \
                    f'Assistant:\n'

        else:
            self.user_messages.append(user_response)
            query = self.analysis_pre_prompt + '\n\n' + \
                    f'Translation: {self.assistant_responses[0]["test"]} -> {user_response}\n' \
                    f'Suggested word: {self.word}\n' \
                    f'Correction: '
        return query

    def get_next_assistant_query(self, user_response) -> (str,int,bool):
        query = self.get_next_answer_test_query(user_response)
        is_last = True if self.next_query_idx == 0 else False
        self.next_query_idx += 1

        return query, is_last
