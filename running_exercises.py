import os
import threading

import joblib
import numpy as np
import pandas as pd


class RunningExercises:
    def __init__(self, fpath):
        self.running_exercises_file = fpath
        if os.path.exists(self.running_exercises_file):
            self._running_exercises = joblib.load(filename=self.running_exercises_file)
            os.remove(self.running_exercises_file)
        else:
            self._running_exercises = dict()
        self._lock = threading.Lock()

    def add_exercise(self, chat_id, exercise):
        self._lock.acquire()
        self._running_exercises[chat_id] = exercise
        self._lock.release()

    def pop_exercise(self, chat_id):
        self._lock.acquire()
        exercise = self._running_exercises.pop(chat_id)
        self._lock.release()
        return exercise

    @property
    def chat_ids(self):
        self._lock.acquire()
        all_chat_ids = self._running_exercises.keys()
        self._lock.release()
        return all_chat_ids

    def current_exercise(self, chat_id):
        self._lock.acquire()
        exercise = self._running_exercises[chat_id]
        self._lock.release()
        return exercise

    def backup(self):
        self._lock.acquire()
        joblib.dump(self._running_exercises, filename=self.running_exercises_file)
        self._lock.release()

    def release_lock(self):
        if not self._lock.locked():
            self._lock.release()