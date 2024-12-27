from typing import Optional
import uuid


class Exercise:

    def __init__(self):
        self.uid = str(uuid.uuid1())

    async def get_next_user_message(self, user_response: Optional[str]) -> tuple[str, int]:
        pass