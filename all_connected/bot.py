"""
Author: Victoria Lee, based on work from Amy Fung & Cynthia Wang & Sofia Kobayashi & Helen Mao
Date: 03/29/2025
Description: The main Slack bot logic for the food delivery data collection project
"""

import os
from pathlib import Path
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

import messenger

import json
import requests
import copy
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_bolt.adapter.flask import SlackRequestHandler
from datetime import date, datetime
import random


### ### CONSTANTS ### ###
# DB_NAME = os.environ['DB_NAME']
DB_NAME = os.environ.get('DB_NAME')

EMOJI_DICT = {0: '🪴', 
                1: '🌺', 
                2: '🍀', 
                3: '✨',
                4: '🐨', 
                5: '🐶',
                6: '🐱',
                7: '🦔',
                8: '🐱',
                9: '🪴', 
}


## ### LOAD IN MESSAGE BLOCKS ### ###
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
BLOCK_MESSAGES_DIR = os.path.join(PROJECT_ROOT, 'all_connected', 'block_messages')

with open(os.path.join(BLOCK_MESSAGES_DIR, 'sample_task.json'), 'r') as infile:  # TODO: renew sample task
    sample_task = json.load(infile)

with open(os.path.join(BLOCK_MESSAGES_DIR, 'headers.json'), 'r') as infile:
    block_headers = json.load(infile)

with open(os.path.join(BLOCK_MESSAGES_DIR, 'task_channel_welcome_message.json'), 'r') as infile:
    task_channel_welcome_message = json.load(infile)  # TODO: modify welcome message

with open(os.path.join(BLOCK_MESSAGES_DIR, 'task_channel_created_confirmation.json'), 'r') as infile:
    task_channel_created_confirmation = json.load(infile)

with open(os.path.join(BLOCK_MESSAGES_DIR, 'main_channel_welcome_message.json'), 'r') as infile:
    main_channel_welcome_message = json.load(infile)  # TODO: modify welcome message

### ### INITIALIZE BOLT APP ### ###
# Initialize app, socket mode handler, & client 
app = App(
    token=os.environ.get('SLACK_BOT_TOKEN'),
    signing_secret=os.environ.get('TASK_BOT_SIGNING_SECRET')
)
client = WebClient(token=os.environ.get('SLACK_BOT_TOKEN'))
if os.environ.get('SLACK_APP_TOKEN'):
    handler = SocketModeHandler(app, os.environ.get('SLACK_APP_TOKEN'))

# Get the bot id
BOT_ID = client.api_call("auth.test")['user_id']

# TEMP: In-memory stage tracking
# TODO: switch to database storage later
order_stages = {} # order_stages[channel_id]["stage"/"extracted_data"/]
STAGES = {
    "RESTAURANT_NAME": 1,
    "ORDER_PLACEMENT_TIME": 2,
    "ESTIMATED_ARRIVAL_TIME": 3,
    "ORDER_FINISH_TIME": 4,
    "MISSING_INFO": 5,
    "SURVEY": 6,
    "COMPLETE": 7
}

### ### HELPER FUNCTIONS ### ####
def send_messages(channel_id, block=None, text=None):
    messenger.send_message(channel_id, block, text)

def send_welcome_message(users_list) -> None:
    '''
    Takes   A list containing all user ids or a dictionary with user ids as its keys. 
            currently using users_store returned by get_all_users_info()
    Sends welcoming message to all users
    '''
    active_users = messenger.get_active_users_list()
    for user_id in users_list:
        if BOT_ID != user_id and user_id in active_users:      
            try:
                print(f'IN Welcome: {user_id}', datetime.now())
                client.chat_postMessage(channel=f"@{user_id}", blocks = main_channel_welcome_message['blocks'], text="Welcome to Snack N Go!")
                print("Welcome!")
            except SlackApiError as e:
                assert e.response["ok"] is False and e.response["error"], f"Got an error: {e.response['error']}"
    # active_users = messenger.get_active_users_list()
    # for user_id in users_list:
    #     if BOT_ID != user_id and user_id in active_users:      
    #         try:
    #             print(f'IN Welcome: {user_id}', datetime.now())
    #             client.chat_postMessage(channel=f"@{user_id}", blocks = onboarding['blocks'], text="Welcome to Snap N Go!")
    #             print("Welcome!")
    #         except SlackApiError as e:
    #             assert e.response["ok"] is False and e.response["error"], f"Got an error: {e.response['error']}"

def create_task_channel(user_id, task_id):
    """
    Helper function for handle_begin_task() in bot.py
    Creates a new private channel for a task (an order submission) and invites the user asked for submitting a new order.
    """
    try:
        # create a new private channel
        channel_name = f"order-upload-{task_id}" # TODO: change to a better name
        response = client.conversations_create(
            name=channel_name, 
            is_private=True
        )
        channel_id = response["channel"]["id"]

        # invite the user to the channel
        client.conversations_invite(channel=channel_id, users=[user_id])

        order_stages[channel_id] = {
            "stage": STAGES["RESTAURANT_NAME"],
            "extracted_data": {}
        }

        return channel_id
    except SlackApiError as e:
        print(f"Error creating channel: {e.response['error']}")
        return None

def get_all_users_info() -> dict:
    '''
    Helper function to get all users info from slack
    Takes a users array we get from slack which is a SlackResponse object type
    Returns a dict type containing same info with user id as key
    '''
    # Get users list (requires the users:read scope)
    result = client.users_list()

    # Get all user info in result
    users_array = result["members"]
    users_store = {}

    # Turn the SlackResponse object type into dict type
    for user in users_array:
        if user['deleted'] == False:
            # Key user info on their unique user ID
            user_id = user["id"]
            # Store the entire user object (you may not need all of the info)
            users_store[user_id] = user
    
    return users_store

def create_order_task(user_id):
    """
    Create a new order upload task in the database.
    Return a task_id corresponding to the task_id in the database. 
    # TODO: Implementation when database ready
    """
    task_id = int(datetime.now().timestamp()) # WARNING: This is temproary!! # TODO: revise when database ready
    return task_id

def process_image(channel_id, file, say):

    # TODO
    # Check what stage is it at
    # Prompt AI to extract

    # TODO: replace with AI
    # Data for test now
    # e.g. initial screenshot + restaurant name
    extracted_data = {
        "restaurant_name": "Hey Tea",
        "order_placement_time": 1741722179,
        "estimated_early_arrival_time": None,
        "estimated_late_arrival_time": None,
        "order_finish_time": None
    }
    
    for key, value in extracted_data.items():
        if value and key not in order_stages[channel_id]["extracted_data"]:
            verify_extracted_data(channel_id, say, key, value)
            order_stages[channel_id]["extracted_data"][key] = value
    
    # TODO: continue on asking for the next data

def verify_extracted_data(channel_id, say, data_type, data_value):
    # Ask the user to verify the extracted data
    say({
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"The data extracted is: \n\n"
                            f"*{data_type}*: {data_value}\n\n"
                            f"Is this correct?"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Yes"
                        },
                        "action_id": f"verify_yes",
                        "value": data_type
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "No"
                        },
                        "action_id": f"verify_no",
                        "value": data_type
                    }
                ]
            }
        ]
    })

def handle_manual_input(channel_id, text, say):
    if channel_id not in order_stages:
        return

    # Check which field is currently missing
    missing_data = order_stages[channel_id]["missing_data"]
    for field in missing_data:
        if missing_data[field]:
            # Update the extracted data with the user's input
            order_stages[channel_id]["extracted_data"][field] = text
            missing_data[field] = False
            say(f"Thank you! The *{field.replace('_', ' ').title()}* has been updated.")
            verify_extracted_data(channel_id, say)
            return

### ### MESSAGE HANDLERS ### ###
@app.message()
def handle_message(payload, say):
    """
    Handles text messages and messages with both text and files.
    """
    channel_id = payload.get('channel')
    user_id = payload.get('user')
    text = payload.get('text')

    print("- Message sent", user_id, text, datetime.now())
    print("Payload:", payload)  # Log the entire payload for debugging

    # Handle certain responses
    if BOT_ID != user_id:
        if 'files' in payload:
            # User attaches a file (with or without text)
            print("text+file", datetime.now())
            print(payload)
            if len(payload['files']) > 1: 
                say("more than one file")
                return

            # User attaches a file that is not an image
            file = payload['files'][0]
            if "image" not in file['mimetype']: 
                say("file type wrong")
                return
            
            # Handle user's image
            process_image(channel_id, file, say)
        else:
            """
            User sends a text without any image
            """
            # User needs help
            if text.strip() == "?" or text.strip().lower() == 'help':
                say("im too lazy to help")
            # User want account summary
            elif text.strip().lower() == "account":
                say("im too lazy to give any account info")
            elif text.strip().lower() == "report":
                say("don't report")
            else:
                print("here")
                say(sample_task)

        return 

### ### INTERACTION HANDLERS ### ###
@app.event("file_shared")
def handle_file_shared_events(body, logger, say):
    """
    Handles file uploads without text.
    """
    logger.info("File shared event received!")
    logger.info(body)  # Log the entire payload for debugging

    file_id = body["event"]["file_id"]
    channel_id = body["event"]["channel_id"]

    # Get file details using the Slack API
    try:
        file_info = client.files_info(file=file_id)["file"]
        if "image" in file_info["mimetype"]:
            # Handle the image
            process_image(channel_id, file_info, say)
        else:
            say("The file you uploaded is not an image.")
    except SlackApiError as e:
        logger.error(f"Error fetching file info: {e.response['error']}")
        say("Sorry, I couldn't process the file. Please try again.")

@app.event("team_join")
def handle_team_join(body, logger, say):
    logger.info("Team join event received!")
    logger.info(body)  # Log the entire payload for debugging
    user_store = get_all_users_info()
    messenger.add_users(user_store)
    user_id = body["event"]["user"]["id"]
    send_welcome_message([user_id])

@app.action("bugs_form")
def handle_some_action(ack, body, logger):
    ack()
    logger.info(body)

@app.action("start_order_submission")
def handle_start_order_submission(ack, body, say):
    """
    Handles the 'Let me submit my order' button in the main channel Welcome message. 
    Create a new order upload task in the database and a dedicated Slack channel for this order.
    TODO: send everything to the database
    TODO: tell amelia that we need a new db column "channel_id" associated with a task
    """
    # Acknowledge the command
    ack()
    # get this user's info
    user_id = str(body["user"]["id"])
    # create a new task in the database 
    task_id = create_order_task(user_id)
    # create a new channel for this order 
    channel_id = create_task_channel(user_id, task_id)
    # sends a welcome message in the task channel
    client.chat_postMessage(channel=channel_id, blocks=task_channel_welcome_message["blocks"])
    # sends a confirmation message in the main channel
    confirmation_message = task_channel_created_confirmation.copy()
    confirmation_message["blocks"][0]["text"]["text"] = confirmation_message["blocks"][0]["text"]["text"].replace("PLACEHOLDER_CHANNEL_NAME", f"order-upload-{task_id}") # TODO: change the channel name when channel name in create_task_channel() changed
    say(confirmation_message)

@app.action("check_account_status")
def handle_check_account_status(ack, body, say):
    """
    Handles the "Check Account Status" button in the main channel Welcome Message.
    Gives user back with their current info such as total compensation, order submission
    history/status, etc. # TODO: revise
    # TODO: implementation
    """
    # Acknowledge the button click
    ack()
    # get the user's ID
    user_id = body["user"]["id"]
    
    say(f"yay nothing ready yet")

@app.action("verify_yes")
def handle_verify_yes(ack, body, say):
    ack()
    channel_id = body["container"]["channel_id"]
    data_type = body["actions"][0]["value"]  # Get the data type from the button value

    # Mark the data as verified
    if channel_id in order_stages:
        order_stages[channel_id]["extracted_data"][data_type] = body["actions"][0]["value"]

    # Move to the next stage
    current_stage = order_stages[channel_id]["stage"]
    next_stage = current_stage + 1 if current_stage < len(STAGES) else STAGES["COMPLETE"]
    order_stages[channel_id]["stage"] = next_stage

    say("Thank you! The data has been verified. Moving to the next stage.")

@app.action("verify_no")
def handle_verify_no(ack, body, say):
    ack()
    channel_id = body["container"]["channel_id"]
    data_type = body["actions"][0]["value"]

    # Ask the user to manually input the correct data
    say({
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Please manually input the *{data_type.replace('_', ' ').title()}*."
                }
            },
            {
                "type": "input",
                "block_id": f"manual_input_{data_type}",
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"manual_input_action"
                },
                "label": {
                    "type": "plain_text",
                    "text": f"Enter the correct {data_type.replace('_', ' ').title()}:"
                }
            }
        ]
    })

@app.action("manual_input_action")
def handle_manual_input_action(ack, body, say):
    ack()
    channel_id = body["container"]["channel_id"]
    data_type = body["actions"][0]["block_id"].replace("manual_input_", "")
    user_input = body["actions"][0]["value"]

    if data_type in ["order_placement_time", "estimated_early_arrival_time", "estimated_late_arrival_time", "order_finish_time"]:
        try:
            input_time = datetime.strptime(user_input, "%Y-%m-%d %H:%M").timestamp()
            order_stages[channel_id]["extracted_data"][data_type] = input_time
        except ValueError:
            say("Invalid time format. Please use `YYYY-MM-DD HH:MM`.")
            return
    else:
        order_stages[channel_id]["extracted_data"][data_type] = user_input

    say(f"Thank you! The *{data_type.replace('_', ' ').title()}* has been updated.")

    # Move to the next stage
    current_stage = order_stages[channel_id]["stage"]
    next_stage = current_stage + 1 if current_stage < len(STAGES) else STAGES["COMPLETE"]
    order_stages[channel_id]["stage"] = next_stage

if __name__ == "__main__":
    # TODO? Figure out why team join doesnt work when app starts
    user_store = get_all_users_info()
    messenger.add_users(user_store)
    send_welcome_message(user_store.keys())
    # Start bolt socket handler
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()