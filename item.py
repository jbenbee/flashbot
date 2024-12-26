from dataclasses import dataclass
from datetime import datetime


@dataclass
class Item:
    e_factor: float = 2.5
    num_reps: int = 0
    next_review_date: datetime = None
    last_review_date: datetime = None
    last_interval: int = 0  # Added to store the previous interval
    word_id: int = None