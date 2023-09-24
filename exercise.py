import uuid


class Exercise:
    ''' Knows how to generate a sequence of messages to the assistant and the user '''

    def __init__(self):
        self.uid = str(uuid.uuid1())

    def repeat(self):
        # restart the exercise
        pass

    def get_next_message_to_user(self, query, assistant_response):
        pass

    def get_next_assistant_query(self, user_response) -> (str,int,bool):
        pass