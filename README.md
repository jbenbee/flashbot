# Flashbot
A telegram bot powered by ChatGPT, that helps you to learn a new language.

The bot supports flashcards-style exercises and can send you brief texts to train reading skills.

The bot will send you exercises based on the schedule that you set up in ```user_data/user_config.json```.
In addition, you can invoke a command to send you a new exercise whenever you want.


![Example Usage](example_usage.gif)

## How to use the code

1. Create a telegram bot. See, e.g. [https://www.pragnakalp.com/create-telegram-bot-using-python-tutorial-with-examples/](https://www.pragnakalp.com/create-telegram-bot-using-python-tutorial-with-examples/).
2. Add the following commands to the bot (BotFather->Edit Bot->Edit Commands):
   - next_test - Next test word 
   - next_new - Next new word 
   - next_reading - Next reading exercise 
   - add_word - Add a word 
   - known_words - List of known words
2. Specify the bot token in environmental variable ```BOT_TOKEN``` or in ```api_keys/bot_token.txt```
3. Specify OpenAI API key in environmental variable ```OPENAI_KEY``` or on the first line of ```api_keys/openai_api.txt```.
4. Specify OpenAI organization in environment variable ```OPENAI_ORG``` or on the second line of ```api_keys/openai_api.txt```.
5. Find chat_ids of all users who will use the bot.
6. Specify user config in ```user_data/user_config.json``` for all users.
7. Add words that the users are learning into ```resources/words_db.csv```.
8. Add topics for reading exercises into ```resources/reading_lists.json```.
9. [Install ```ngrok```](https://ngrok.com/)
10. ```pip install -r requirements.txt```
11. ```python main.py```

