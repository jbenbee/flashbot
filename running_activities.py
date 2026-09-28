import os
import threading

import joblib
import numpy as np
import pandas as pd

from exercise import Exercise


class RunningActivities:
    def __init__(self, fpath):
        self.running_exercises_file = fpath
        if os.path.exists(self.running_exercises_file):
            self._running_activities = joblib.load(filename=self.running_exercises_file)
            os.remove(self.running_exercises_file)
        else:
            self._running_activities = dict()
        self._lock = threading.Lock()

    def add_activity(self, chat_id, activity):
        with self._lock:
            if chat_id not in self._running_activities:
                self._running_activities[chat_id] = []

            if isinstance(activity, Exercise):
                # remove all other activities
                self._running_activities[chat_id] = []
                self._running_activities[chat_id].append(activity)
            else:
                # add a command
                current_activity = self._running_activities[chat_id][-1] if len(self._running_activities[chat_id]) > 0 else None
                if not isinstance(current_activity, Exercise) and current_activity is not None:
                    # remove the command that is on top of the stack
                    self._running_activities[chat_id].pop()
                self._running_activities[chat_id].append(activity)

            assert len(self._running_activities[chat_id]) <= 2

    def pop_activity(self, chat_id):
        with self._lock:
            activity = None
            if len(self._running_activities[chat_id]) > 0:
                activity = self._running_activities[chat_id].pop()
            return activity

    def pop_all(self, chat_id):
        with self._lock:
            self._running_activities[chat_id] = []

    @property
    def chat_ids(self):
        with self._lock:
            return list(self._running_activities.keys())

    def current_activity(self, chat_id):
        with self._lock:
            if chat_id not in self._running_activities or len(self._running_activities[chat_id]) == 0:
                return None
            return self._running_activities[chat_id][-1]
    
    # def current_exercise(self, chat_id):
    #     self._lock.acquire()
    #     activity = self.current_activity(chat_id)
    #     if not isinstance(activity, Exercise):
    #         self.pop_activity(chat_id)
    #     activity = self.current_activity(chat_id)
    #     assert len(self._running_activities[chat_id]) <= 1
    #     self._lock.release()
    #     return activity

    def backup(self):
        with self._lock:
            joblib.dump(self._running_activities, filename=self.running_exercises_file)