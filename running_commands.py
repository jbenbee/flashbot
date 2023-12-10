import os
import threading

import joblib
import numpy as np
import pandas as pd


class RunningCommands:
    def __init__(self):
        self._running_commands = dict()
        self._lock = threading.Lock()

    def add_command(self, chat_id, exercise):
        self._lock.acquire()
        self._running_commands[chat_id] = exercise
        self._lock.release()

    def pop_command(self, chat_id):
        self._lock.acquire()
        command = self._running_commands.pop(chat_id)
        self._lock.release()
        return command

    @property
    def chat_ids(self):
        self._lock.acquire()
        all_chat_ids = self._running_commands.keys()
        self._lock.release()
        return all_chat_ids

    def current_command(self, chat_id):
        self._lock.acquire()
        command = self._running_commands[chat_id]
        self._lock.release()
        return command

    def release_lock(self):
        if not self._lock.locked():
            self._lock.release()