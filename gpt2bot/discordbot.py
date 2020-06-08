# -*- coding: UTF-8 -*-
#  Copyright (c) polakowo
#  Licensed under the MIT license.
# !pip install python-telegram-bot --upgrade
from functools import wraps
import configparser
import argparse
import requests
from urllib.parse import urlencode
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from functools import partial
import random
import re
import discord

from model import download_model_folder, download_reverse_model_folder, load_model
from decoder import generate_response

from threading import Thread
import time 
from queue import Queue

from flask_ngrok import run_with_ngrok
from flask import Flask
app = Flask(__name__)
run_with_ngrok(app)   #starts ngrok when the app is run
partial_run = partial(app.run)


#room codes
gpt_chat = 719063737448923179
chat_42 = 719260820047134735

client = discord.Client()
config = ""
context = {}
model = ""
tokenizer = ""
mmi_model= ""
mmi_tokenizer=""

# https://github.com/python-telegram-bot/python-telegram-bot/wiki/Code-snippets

def requests_retry_session(
    retries=3,
    backoff_factor=0.3,
    status_forcelist=(500, 502, 504),
    session=None,
):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def translate_message_to_gif(message, config):
    # https://engineering.giphy.com/contextually-aware-search-giphy-gets-work-specific/
    params = {
        'api_key': config.get('chatbot', 'giphy_token'),
        's': message,
        'weirdness': config.getint('chatbot', 'giphy_weirdness')
    }
    url = "http://api.giphy.com/v1/gifs/translate?" + urlencode(params)
    response = requests_retry_session().get(url)
    return response.json()['data']['images']['fixed_height']['url']

def self_decorator(self, func):
    """Passes bot object to func command."""
    # TODO: Any other ways to pass variables to handlers?
    def command_func(update, context, *args, **kwargs):
        return func(self, update, context, *args, **kwargs)
    return command_func

def send_action(action):
    """Sends `action` while processing func command."""
    def decorator(func):
        @wraps(func)
        def command_func(self, update, context, *args, **kwargs):
            context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=action)
            return func(self, update, context, *args, **kwargs)
        return command_func
    return decorator

def gpt_normalize(txt):
    txt = re.sub(r"[^A-Za-z0-9()\[\]:,.!?'“”\"]", " ", txt) # remove illegal chars
    return ' '.join(txt.strip().split()) # remove unnecessary spaces

def main():
    global config
    global model
    global tokenizer
    global mmi_model
    global mmi_tokenizer

    # Script arguments can include path of the config
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('--config', type=str, default="chatbot.cfg")
    args = arg_parser.parse_args()

    # Read the config
    config = configparser.ConfigParser(allow_no_value=True)
    with open(args.config) as f:
        config.read_file(f)

    # Download and load main model
    target_folder_name = download_model_folder(config)
    model, tokenizer = load_model(target_folder_name, config)

    # Download and load reverse model
    use_mmi = config.getboolean('model', 'use_mmi')
    if use_mmi:
        mmi_target_folder_name = download_reverse_model_folder(config)
        mmi_model, mmi_tokenizer = load_model(mmi_target_folder_name, config)
    else:
        mmi_model = None
        mmi_tokenizer = None
    
    # Run Telegram bot
    #bot = TelegramBot(model, tokenizer, config, mmi_model=mmi_model, mmi_tokenizer=mmi_tokenizer)
    #bot.run_chat()
    #client.loop.create_task(my_background_task())
    t = Thread(target=partial_run)
    t.start()
    client.run(config.get('chatbot', 'discord_token'))


@app.route('/')
def hello_world():
    return 'Hello, World!'

@client.event
async def on_message(message): #when someone sends a message
    if message.channel.id == gpt_chat and message.author != client.user:
        #await message.channel.send("test on message") #send a good morning message
        await discord_message(message)

async def discord_message(message):
    global config
    global context
    # Parse parameters
    num_samples = config.getint('decoder', 'num_samples')
    max_turns_history = config.getint('decoder', 'max_turns_history')
    if 'turns' not in context:
        context['turns'] = []
    turns = context['turns']

    user_message = message.content
    if user_message.lower() == 'bye':
        # Restart chat
        context['turns'] = []
        await message.channel.send("Bye")
        return None
    return_gif = False
    if '@gif' in user_message:
        # Return gif
        return_gif = True
        user_message = user_message.replace('@gif', '').strip()
    if max_turns_history == 0:
        # If you still get different responses then set seed
        context['turns'] = []
    # A single turn is a group of user messages and bot responses right after
    turn = {
        'user_messages': [],
        'bot_messages': []
    }
    turns.append(turn)
    turn['user_messages'].append(user_message)
    #logger.info(f"{update.effective_message.chat_id} - User >>> {user_message}")
    # Merge turns into a single history (don't forget EOS token)
    history = ""
    from_index = max(len(turns)-max_turns_history-1, 0) if max_turns_history >= 0 else 0
    for turn in turns[from_index:]:
        # Each turn begings with user messages
        for messagex in turn['user_messages']:
            history += gpt_normalize(messagex) + tokenizer.eos_token
        for messagex in turn['bot_messages']:
            history += gpt_normalize(messagex) + tokenizer.eos_token

    # Generate bot messages
    bot_messages = generate_response(
        model, 
        tokenizer, 
        history, 
        config, 
        mmi_model, 
        mmi_tokenizer
    )
    if num_samples == 1:
        bot_message = bot_messages[0]
    else:
        # TODO: Select a message that is the most appropriate given the context
        # This way you can avoid loops
        bot_message = random.choice(bot_messages)
    turn['bot_messages'].append(bot_message)
    #logger.info(f"{update.effective_message.chat_id} - Bot >>> {bot_message}")
    if return_gif:
        # Return response as GIF
        gif_url = translate_message_to_gif(bot_message, config)
        await message.channel.send(gif_url)
    else:
        # Return response as text
        #update.message.reply_text(bot_message)
        await message.channel.send(bot_message)
if __name__ == '__main__':
    main()
