# Flashbot
A telegram bot powered by ChatGPT, that helps you to learn words of a new language.

The bot supports flashcards-style exercises.

The bot will send you exercises based on the schedule that you set up in ```user_data/user_config.json```.
In addition, you can invoke a command to send you a new exercise whenever you want.

## How to use the code

1. Create a telegram bot. See, e.g. [https://www.pragnakalp.com/create-telegram-bot-using-python-tutorial-with-examples/](https://www.pragnakalp.com/create-telegram-bot-using-python-tutorial-with-examples/).
2. Add the following commands to the bot (BotFather->Edit Bot->Edit Commands):
   - next_test - Next test word 
   - next_new - Next new word
   - add_word - Add a word 
3. Set up environment variables:
    - Specify the bot token in environmental variable ```BOT_TOKEN``` or in ```api_keys/bot_token.txt```
    - Specify OpenAI API key in environmental variable ```OPENAI_KEY``` or on the first line of ```api_keys/openai_api.txt```
    - Path to folder containing user files ```CH_USER_DATA_ROOT```
5. Find chat_ids of all users who will use the bot.
6. Specify user config in ```user_data/user_config.json``` for all users.
7. ```pip install -r requirements.txt```

```python main.py --webhook <webhook url>```

