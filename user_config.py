import copy
import itertools
import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo


class UserConfig:
    def __init__(self, path):

        self.data_path = path
        with open(self.data_path, 'r', encoding='utf-8') as fp:
            self._user_data_orig = json.loads(fp.read())

        self._user_data = {int(k): copy.deepcopy(v) for k, v in self._user_data_orig.items()}

        for exercise, day in itertools.product(['words'], ['weekday', 'weekend']):
            for chat_id, chat_config in self._user_data.items():
                if exercise not in chat_config['schedule']:
                    self._user_data[chat_id]['schedule'][exercise] = {}

                user_tz = ZoneInfo(self._user_data[chat_id]["timezone"])
                         
                self._user_data[chat_id]['schedule'][exercise][day] = \
                    {datetime.strptime(x, '%H:%M').time().replace(tzinfo=user_tz): data for x, data in chat_config['schedule'][exercise][day].items()} \
                    if day in self._user_data[chat_id]['schedule'][exercise].keys() else {}
        self._lock = threading.Lock()

    def get_all_user_data(self):
        with self._lock:
            return copy.deepcopy(self._user_data)

    def get_all_chat_ids(self):
        with self._lock:
            return list(self._user_data.keys())

    def get_user_data(self, chat_id):
        with self._lock:
            return copy.deepcopy(self._user_data[chat_id])

    def get_user_ui_lang(self, chat_id):
        return self._user_data[chat_id]['ui_language'] if 'ui_language' in self._user_data[chat_id].keys() else 'english'

    def set_user_field(self, chat_id, key, value):
        with self._lock:
            self._user_data[chat_id][key] = value
            # keep the original JSON-serializable data in sync and persist it
            self._user_data_orig[str(chat_id)][key] = value
            with open(self.data_path, 'w', encoding='utf-8') as fp:
                json.dump(self._user_data_orig, fp, ensure_ascii=False, indent=4)