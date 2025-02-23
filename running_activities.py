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
        self._lock.acquire()
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
        self._lock.release()

    def pop_activity(self, chat_id):
        self._lock.acquire()
        if len(self._running_activities[chat_id]) > 0:
            activity = self._running_activities[chat_id].pop()
        self._lock.release()
        return activity

    def pop_all(self, chat_id):
        self._lock.acquire()
        self._running_activities[chat_id] = []
        self._lock.release()

    @property
    def chat_ids(self):
        self._lock.acquire()
        all_chat_ids = self._running_activities.keys()
        self._lock.release()
        return all_chat_ids

    def current_activity(self, chat_id):
        self._lock.acquire()
        if chat_id not in self._running_activities or len(self._running_activities[chat_id]) == 0:
            self._lock.release()
            return None
        exercise = self._running_activities[chat_id][-1]
        self._lock.release()
        return exercise
    
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
        self._lock.acquire()    
        joblib.dump(self._running_activities, filename=self.running_exercises_file)
        self._lock.release()

    def release_lock(self):
        if self._lock.locked():
            self._lock.release()