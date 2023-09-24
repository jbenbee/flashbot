import copy
import itertools
import json
from datetime import datetime


class UserConfig:
    def __init__(self, path):

        with open(path, 'r') as fp:
            self.user_data = json.loads(fp.read())

        self.user_data = {int(k): v for k, v in self.user_data.items()}

        for exercise, day in itertools.product(['words', 'reading'], ['weekday', 'weekend']):
            for chat_id, chat_config in self.user_data.items():
                if exercise not in chat_config['schedule']:
                    self.user_data[chat_id]['schedule'][exercise] = {}
                self.user_data[chat_id]['schedule'][exercise][day] = \
                    {datetime.strptime(x, '%H:%M').time(): data for x, data in chat_config['schedule'][exercise][day].items()} \
                    if day in self.user_data[chat_id]['schedule'][exercise].keys() else {}

    def get_all_user_data(self):
        return copy.deepcopy(self.user_data)

    def get_all_chat_ids(self):
        return list(self.user_data.keys())

    def get_user_data(self, chat_id):
        return self.user_data[chat_id].copy()
