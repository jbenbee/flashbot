# Flashbot
A telegram bot powered by ChatGPT, that helps you to learn a new language.

The bot supports flashcards-style exercises and can send you brief texts to train reading skills.

The bot will send you exercises based on the schedule that you set up in ```user_data/user_config.json```.
In addition, you can invoke a command to send you a new exercise whenever you want.


![Example Usage](example_usage.gif)

## How to use the code

1. Create a telegram bot. See, e.g. [https://www.pragnakalp.com/create-telegram-bot-using-python-tutorial-with-examples/](https://www.pragnakalp.com/create-telegram-bot-using-python-tutorial-with-examples/).
2. Add the following commands to the bot (BotFather->Edit Bot->Edit Commands):
   - /next_test - Next test word 
   - /next_new - Next new word 
   - /next_reading - Next reading exercise 
   - /add_word - Add a word 
   - /known_words - List of known words
   - /cur_word_group - Current word group
   - /sel_word_group - Select a word group
3. Specify the bot token in environmental variable ```BOT_TOKEN``` or in ```api_keys/bot_token.txt```
4. Specify OpenAI API key in environmental variable ```OPENAI_KEY``` or on the first line of ```api_keys/openai_api.txt```.
5. Specify OpenAI organization in environment variable ```OPENAI_ORG``` or on the second line of ```api_keys/openai_api.txt```.
6. Find chat_ids of all users who will use the bot.
7. Specify user config in ```user_data/user_config.json``` for all users.
8. Add words that the users are learning into ```resources/words_db.csv```.
9. Add topics for reading exercises into ```resources/reading_lists.json```.
10. ```pip install -r requirements.txt```
11. To run locally using ngrok:
    - [Install ```ngrok```](https://ngrok.com/)
    - ```python main.py --local```
12. To run on a server:
    - ```python main.py --webhook <webhook url>```

