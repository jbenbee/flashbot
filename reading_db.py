import copy
import json


class ReadingDB:
    def __init__(self, reading_path):
        self.reading_path = reading_path
        with open(self.reading_path, 'r', encoding='utf8') as fp:
            data_txt = fp.read()
        self.reading_data = json.loads(data_txt)

    def get_reading_data(self):
        return copy.deepcopy(self.reading_data)
