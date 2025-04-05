"""
Author: Victoria Lee, based on work from Amy Fung & Cynthia Wang & Sofia Kobayashi & Helen Mao
Date: 03/29/2025
Description: The main Slack bot logic for the food delivery data collection project
"""

import os
from pathlib import Path
from dotenv import load_dotenv
import json
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from datetime import datetime
from helper_functions import *
from gemini import *
import messenger
import re

## Load environment variables ##
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

### CONSTANTS ###
DB_NAME = os.environ.get('DB_NAME')
BOT_ID = WebClient(token=os.environ.get('SLACK_BOT_TOKEN')).api_call("auth.test")['user_id']

## Path configurations ##
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
BLOCK_MESSAGES_DIR = os.path.join(PROJECT_ROOT, 'all_connected', 'block_messages')
IMAGE_STORAGE_DIR = os.path.join(PROJECT_ROOT, '..', 'order_screenshots')
os.makedirs(IMAGE_STORAGE_DIR, exist_ok=True)

## Load message blocks ##
def load_message_block(filename):
    with open(os.path.join(BLOCK_MESSAGES_DIR, filename), 'r') as infile:
        return json.load(infile)

MESSAGE_BLOCKS = {
    'headers': load_message_block('headers.json'),
    'channel_welcome': load_message_block('channel_welcome_message.json'),
    'channel_created': load_message_block('channel_created_confirmation.json'),
    'main_channel_welcome_message': load_message_block('main_channel_welcome_message.json')
}

# Order stages configuration
ORDER_STAGES = {
    'awaiting_app_selection': {
        'next': 'awaiting_initial_screenshot',
        'prompt': "Which delivery app did you use?",
        'actions': ['app_selection']
    },
    'awaiting_initial_screenshot': {
        'next': 'verifying_initial_data',
        'prompt': None,
        'actions': ['file_upload']
    },
    'verifying_initial_data': {
        'next': 'awaiting_completion_screenshot',
        'prompt': None,  # Dynamic based on verification flow
        'actions': ['verify_field']
    },
    'awaiting_completion_screenshot': {
        'next': 'verifying_completion_data',
        'prompt': "Thanks for verifying all this information! Now we will move on to submitting the second screenshot which will be an *order completion* screenshot. This is usually taken right after you receive your order from the driver and includes information about the *order completion time* aka when the order was delivered. Please give snack\'n\'go a few seconds to process your image before we proceed to the next step 🙂",
        'actions': ['file_upload']
    },
    'verifying_completion_data': {
        'next': 'collecting_missing_info',
        'prompt': None,
        'actions': ['verify_field']
    },
    'collecting_missing_info': {
        'next': 'completed',
        'prompt': "Let's check if we're missing anything...",
        'actions': ['verify_field']
    }
}

# Initialize Slack app
app = App(
    token=os.environ.get('SLACK_BOT_TOKEN'),
    signing_secret=os.environ.get('TASK_BOT_SIGNING_SECRET')
)
client = WebClient(token=os.environ.get('SLACK_BOT_TOKEN'))

### HELPER FUNCTIONS ###
# Add these helper functions
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

def get_current_unix_time():
    return int(time.time())

def format_unix_time(timestamp, format_str="%Y-%m-%d %H:%M"):
    """Convert Unix timestamp to human-readable string"""
    if timestamp is None:
        return "[Not Provided]"
    return datetime.fromtimestamp(timestamp).strftime(format_str)

def parse_human_time_to_unix(time_str):
    """Convert user-input time to Unix timestamp"""
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        return int(dt.timestamp())
    except ValueError:
        try:
            dt = datetime.strptime(time_str, "%H:%M")  # Assume today's date
            dt = dt.replace(year=datetime.now().year, 
                           month=datetime.now().month,
                           day=datetime.now().day)
            return int(dt.timestamp())
        except:
            return None

def db_operation(query, params=None, fetch_one=False, fetch_all=False):
    """Generic database operation handler"""
    conn = None
    try:
        conn = connectDB(DB_NAME)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(query, params or ())
            if fetch_one:
                result = cursor.fetchone()
            elif fetch_all:
                result = cursor.fetchall()
            else:
                result = None  # For operations that don't return results (INSERT/UPDATE)
            conn.commit()
            return result
    except Exception as e:
        print(f"Database error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_order_info(channel_id):
    """Get order information by channel ID"""
    return db_operation(
        "SELECT * FROM orders WHERE channel_id = %s",
        (channel_id,),
        fetch_one=True
    )

def get_order_channel(body):
    """Helper to get the order channel from any interaction"""
    channel_id = body["container"]["channel_id"]
    order = get_order_info(channel_id)
    return order["channel_id"] if order else channel_id

def update_order(channel_id, updates):
    """Update order fields with column existence check"""
    if not updates:
        return False
        
    # Get existing columns
    conn = connectDB(DB_NAME)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SHOW COLUMNS FROM orders")
            existing_columns = {col[0] for col in cursor.fetchall()}
            
            # Filter updates to only include existing columns
            valid_updates = {k: v for k, v in updates.items() if k in existing_columns}
            
            if not valid_updates:
                return False
                
            set_clause = ", ".join([f"{k} = %s" for k in valid_updates])
            query = f"UPDATE orders SET {set_clause} WHERE channel_id = %s"
            params = list(valid_updates.values()) + [channel_id]
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        print(f"Database error in update_order: {e}")
        return False
    finally:
        if conn:
            conn.close()

def create_order(user_id, channel_id):
    """Create a new order record with Unix timestamps"""
    conn = None
    try:
        conn = connectDB(DB_NAME)
        with conn.cursor() as cursor:
            # Modified query for MySQL compatibility
            cursor.execute(
                """INSERT INTO orders 
                   (user_id, channel_id, status, channel_creation_time) 
                   VALUES (%s, %s, 'awaiting_app_selection', %s)""",
                (user_id, channel_id, get_current_unix_time())
            )
            order_id = cursor.lastrowid  # Get the auto-incremented ID
            conn.commit()
            return order_id
    except Exception as e:
        print(f"Database error in create_order: {e}")
        return None
    finally:
        if conn:
            conn.close()

def create_channel(user_id):
    """Create a new private channel for an order"""
    try:
        # Create channel
        channel_name = f"order-{get_current_unix_time()}"
        response = client.conversations_create(name=channel_name, is_private=True)
        channel_id = response["channel"]["id"]
        
        # Create order record
        order_id = create_order(user_id, channel_id)
        if not order_id:
            raise Exception("Failed to create order record")
            
        # Invite user
        client.conversations_invite(channel=channel_id, users=[user_id])
        return order_id, channel_id
        
    except SlackApiError as e:
        print(f"Error creating channel: {e.response['error']}")
        return None, None

def get_next_unverified_field(order):
    """Determine which field to verify next - only returns fields with actual values"""
    verification_order = [
        ('restaurant_name', 'is_restaurant_name_verified'),
        ('order_placement_time', 'is_order_placement_time_verified'),
        ('earliest_estimated_arrival_time', 'is_earliest_estimated_arrival_time_verified'),
        ('latest_estimated_arrival_time', 'is_latest_estimated_arrival_time_verified'),
        ('order_completion_time', 'is_order_completion_time_verified'),
        ('restaurant_address', 'is_restaurant_address_verified')
    ]
    
    for field, verification_flag in verification_order:
        # Only return if field has a value AND isn't verified yet
        if order.get(field) is not None and not order.get(verification_flag, False):
            return field, verification_flag
    return None, None

def format_field_for_display(field_name, value):
    """Convert field values to human-readable format"""
    if field_name.endswith('_time') and value:
        if isinstance(value, (int, float)):  # Handle Unix timestamp
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
        return value.strftime("%Y-%m-%d %H:%M") if hasattr(value, 'strftime') else str(value)
    return str(value) if value else "[Not Provided]"

def send_input_prompt(channel_id, field, is_missing=False, client=None):
    """Generic function to ask for user input with better guidance"""
    prompt = (f"We couldn't determine the {field.replace('_', ' ')}. Please provide it:" 
              if is_missing 
              else f"Please enter the correct {field.replace('_', ' ')}")
    
    hint_text = ""
    if field.endswith('_time'):
        prompt += " (format: YYYY-MM-DD HH:MM or HH:MM)"
        hint_text = "Examples: 2025-03-29 14:30 or 14:30 (for today)"
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": prompt
            }
        }
    ]
    
    if hint_text:
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"💡 *Tip:* {hint_text}"
                }
            ]
        })
    
    blocks.extend([
        {
            "type": "input",
            "block_id": f"{'missing' if is_missing else 'correct'}_{field}",
            "element": {
                "type": "plain_text_input",
                "action_id": "text_input",
                "placeholder": {
                    "type": "plain_text",
                    "text": "Type your answer here"
                }
            },
            "label": {
                "type": "plain_text",
                "text": "Enter your answer:"
            }
        },
        {
            "type": "actions",
            "elements": [
                create_button("Submit", "process_input", field)
            ]
        }
    ])
    
    if client:
        client.chat_postMessage(
            channel=channel_id,
            text='Input prompt', 
            blocks=blocks
        )
    return blocks

def process_image(channel_id, file):
    """Process uploaded image based on order stage"""
    print(f"[IMAGE PROCESSING] Processing image in channel {channel_id}, File: {file['name']}", datetime.now())
    allowed_mimetypes = ["image/png", "image/jpeg", "image/jpg"]
    max_size_mb = 5
    
    if file["mimetype"] not in allowed_mimetypes:
        client.chat_postMessage(
            channel=channel_id,
            text="Only PNG/JPEG images under 5MB are allowed."
        )
        return
        
    if file["size"] > max_size_mb * 1024 * 1024:
        client.chat_postMessage(
            channel=channel_id,
            text=f"Image too large. Max size: {max_size_mb}MB."
        )
        return

    order = get_order_info(channel_id)
    if not order:
        client.chat_postMessage(
            channel=channel_id,
            text="Order not found"
        )
        return
    
    try:
        # Get file info
        file_info = client.files_info(file=file['id'])['file']
        
        # Download the file
        response = requests.get(
            file_info['url_private_download'],
            headers={'Authorization': f'Bearer {os.environ.get("SLACK_BOT_TOKEN")}'}
        )
        
        if response.status_code != 200:
            raise Exception("Failed to download file from Slack")
        
        # Create filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_ext = file['name'].split('.')[-1] if '.' in file['name'] else 'jpg'
        
        # Determine screenshot stage
        if order['status'] == 'awaiting_initial_screenshot':
            stage = 'placement'
            image_stage = "awaiting_placement_time"
        elif order['status'] == 'awaiting_completion_screenshot':
            stage = 'completion'
            image_stage = "awaiting_arrival_time"
        else:
            stage = 'other'
            image_stage = "awaiting_placement_time"
            
        filename = f"order_{order['order_id']}_{stage}_{timestamp}.{file_ext}"
        filepath = os.path.join(IMAGE_STORAGE_DIR, filename)
        
        # Save the file
        with open(filepath, 'wb') as f:
            f.write(response.content)
        
        # Process the image
        extracted = gemini_process_image(filepath, image_stage)
        print(extracted)
        
        updates = {
            'status': 'verifying_initial_data' if stage == 'placement' else 'verifying_completion_data'
        }
        
        if stage == 'placement':
            updates.update({
                'placement_screenshot_path': filepath,
                'restaurant_name': extracted.get('restaurant_name'),
                'order_placement_time': extracted.get('order_placement_time'),
                'earliest_estimated_arrival_time': extracted.get('earliest_estimated_arrival_time'),
                'latest_estimated_arrival_time': extracted.get('latest_estimated_arrival_time')
            })
        else:
            updates.update({
                'completion_screenshot_path': filepath,
                'order_completion_time': extracted.get('order_completion_time')
            })
        
        if update_order(channel_id, updates):
            start_field_verification(channel_id, client)
        else:
            raise Exception("Failed to update order in database")
            
    except Exception as e:
        error_msg = f"Error processing image: {str(e)}"
        print(error_msg)
        client.chat_postMessage(
            channel=channel_id,
            text=error_msg
        )

def start_field_verification(channel_id, client):
    # Get the current order information
    order = get_order_info(channel_id)
    if not order:
        client.chat_postMessage(
            channel=order['channel_id'],
            text="No active order", 
            blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "No active order in this channel"
                        )
                    }
                }]
        )
        return
    
    # Determine which field needs verification next
    field, verification_flag = get_next_unverified_field(order)
    
    if not field:
        # All fields verified - move to next stage
        handle_stage_completion(order, client)
        return
    
    # Get current value of the field
    field_value = order.get(field)
    
    # Send verification prompt
    client.chat_postMessage(
        channel=channel_id,
        text='send verification prompt', 
        blocks={{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{field.replace('_', ' ').title()}*: "
                            f"{format_field_for_display(field, field_value)}\n"
                            "Is this correct?"
                        )
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ Yes"},
                            "action_id": "verify_field_yes",
                            "value": f"{field}|{verification_flag}",
                            "style": "primary"
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✏️ No"},
                            "action_id": "verify_field_no",
                            "value": field
                        }
                    ]
                }
            }
    )

def handle_stage_completion(order, client):
    """
    Handles the completion of a verification stage and moves to the next stage.
    """
    channel_id = order['channel_id']
    current_stage = order['status']
    next_stage = ORDER_STAGES.get(current_stage, {}).get('next')
    
    print(f"[STAGE CHANGE] Channel {channel_id} moving from {current_stage} to {next_stage}", datetime.now())

    if not next_stage:
        client.chat_postMessage(
            channel=channel_id,
            text="Thank you! Submission complete. ",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🎉 *Thank you!* Your order submission is complete.\n\nFor future reference, you can review the instructions here:\n<https://docs.google.com/document/d/1JOXu2Qwi_I5X__FwH6g0dlyMh-QxqCFeLo3s5l5ImjI/edit?usp=sharing | order submission instructions document>"
                    }
                }
            ]
        )
        return
    
    # Update to next stage
    if update_order(order['channel_id'], {'status': next_stage}):
        # Show progress indicator with the new stage
        next_prompt = ORDER_STAGES.get(next_stage, {}).get('prompt')
        if next_prompt:
            client.chat_postMessage(
                channel=channel_id, 
                text='Next step', 
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{next_prompt}"
                    }
                }]
            )
        
        # Special handling for certain stage transitions
        if next_stage == 'collecting_missing_info':
            check_for_missing_info(order['channel_id'], client)

def get_button_style(action_id, is_disabled=False):
    """Helper to get button style based on action_id"""
    if is_disabled:
        return None
    if action_id == "verify_field_yes":
        return "primary"  # Slack's primary is green
    elif action_id == "verify_field_no":
        return "danger"   # Slack's danger is red
    elif action_id == "process_input":
        return "primary"  # Blue (same as yes for now)
    return None

def create_button(text, action_id, value, style=None):
    """Create a properly formatted Slack button"""
    button = {
        "type": "button",
        "text": {"type": "plain_text", "text": text},
        "action_id": action_id,
        "value": value
    }
    if style in ["primary", "danger"]:  # Only allowed styles
        button["style"] = style
    return button

def update_message_after_action(client, channel_id, ts, original_blocks, decision_text):
    """Update message to show decision and remove buttons"""
    # Create new blocks without action blocks
    new_blocks = [block for block in original_blocks if block.get("type") != "actions"]
    
    # Add decision text
    new_blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Decision:* {decision_text}"
        }
    })
    
    client.chat_update(
        channel=channel_id,
        ts=ts,
        blocks=new_blocks
    )

### MESSAGE HANDLERS ###
@app.event("file_created")
def handle_file_created_events(body, logger):
    logger.info(body)

@app.event("message")
def handle_message(payload, say):
    """Handle text messages and messages with files"""
    print(json.dumps(payload, indent=2))

    channel_id = payload.get('channel')
    user_id = payload.get('user')
    text = payload.get('text', '').strip().lower()

    if user_id == BOT_ID:
        return

    print(f"[USER MESSAGE] Message from {user_id}: {text}", datetime.now())
    if text in ["help", "?"]:
        print(f"[HELP REQUEST] User {user_id} requested help", datetime.now())
        say(text="Here's how I can help you!",
            blocks=MESSAGE_BLOCKS["main_channel_welcome_message"]['blocks'])
        return
    if 'files' in payload:
        print(f"[FILE UPLOAD] User {user_id} uploaded {len(payload['files'])} files",  datetime.now())
        if len(payload['files']) > 1:
            say("Please upload only one file at a time.")
            return
        file = payload['files'][0]
        if "image" not in file['mimetype']:
            say(text="Please upload an image file. ", 
                blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "⚠️ *Please upload an image file*\nWe need a screenshot to process your order. Only JPG, JPEG, or PNG files are accepted."
                }
            }])
            return
        process_image(channel_id, file)
    else:
        say()

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
                client.chat_postMessage(channel=f"@{user_id}", blocks = MESSAGE_BLOCKS["main_channel_welcome_message"]['blocks'], text="Welcome to Snack N Go!")
                print("Welcome!")
            except SlackApiError as e:
                assert e.response["ok"] is False and e.response["error"], f"Got an error: {e.response['error']}"

@app.action("process_input")
def handle_user_input(ack, body, say, logger, client):
    try:
        ack()
        channel_id = body["container"]["channel_id"]

        user_id = body["user"]["id"]

        
        print("\n=== FULL PAYLOAD ===")
        print(json.dumps(body, indent=2, default=str))
        
        field = block_id.replace("correct_", "").replace("missing_", "")
        value = block_content["text_input"]["value"]
        print(f"[USER INPUT] User {user_id} provided input for {field}: {value}", datetime.now())

        try:
            channel_id = body["container"]["channel_id"]
            state_values = body["state"]["values"]
            
            for block_id, block_content in state_values.items():
                if "text_input" in block_content:
                    value = block_content["text_input"]["value"]
                    field = block_id.replace("correct_", "").replace("missing_", "")
                    break
            else:
                raise ValueError("No text input found in state.values")
                
        except Exception as e:
            print(f"Extraction error: {e}")
            say("⚠️ We couldn't process your input. Please try again.")
            return

        try:
            updates = {}
            if field.endswith('_time'):
                timestamp = parse_human_time_to_unix(value)
                if not timestamp:
                    say("⚠️ Invalid time format. Please use HH:MM (24-hour format)")
                    return
                updates[field] = timestamp
            else:
                updates[field] = value

            # Handle verification flags for missing fields
            if "missing_" in block_id:
                updates[f"is_{field}_verified"] = True

            if update_order(channel_id, updates):
                if "missing_" in block_id:
                    check_for_missing_info(channel_id, client)
                else:
                    start_field_verification(channel_id, client)
                    
        except Exception as e:
            print(f"Update error: {e}")
            say("⚠️ Failed to update your information. Please try again.")

    except Exception as e:
        print(f"Critical error: {e}")

@app.event("file_shared")
def handle_file_shared_events(body, logger):
    """Handle file uploads without text"""
    logger.info("File shared event received")
    file_id = body["event"]["file_id"]
    channel_id = body["event"]["channel_id"]
    user_id = body["event"]["user"]["id"]

    print(f"[FILE SHARED] User {user_id} shared file in channel {channel_id}", datetime.now())
    
    try:
        file_info = client.files_info(file=file_id)["file"]
        if "image" in file_info["mimetype"]:
            process_image(channel_id, file_info)
        else:
            client.chat_postMessage(
                channel=channel_id,
                text="Please upload an image file (JPG, JPEG, or PNG).",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⚠️ *Please upload an image file*\nWe need a screenshot to process your order. Only JPG, JPEG, or PNG files are accepted."
                    }
                }]
            )
    except SlackApiError as e:
        logger.error(f"Error fetching file info: {e.response['error']}")
        client.chat_postMessage(
            channel=channel_id,
            text="Sorry, I couldn't process your file.",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "⚠️ *Unable to process your file*\nPlease try again with a clear screenshot of your order. If the problem persists, try uploading a smaller file size (under 5MB)."
                }
            }]
        )

@app.event("team_join")
def handle_team_join(body, logger, say):
    logger.info("Team join event received!")
    logger.info(body)  # Log the entire payload for debugging
    user_store = get_all_users_info()
    messenger.add_users(user_store)
    user_id = body["event"]["user"]["id"]
    print(f"[NEW USER] User {user_id} joined the workspace", datetime.now())
    send_welcome_message([user_id])

@app.action("start_order_submission")
def handle_start_order_submission(ack, body, say):
    """Start new order submission flow"""
    ack()
    user_id = body["user"]["id"]
    order_id, channel_id = create_channel(user_id)
    print(f"[ORDER STARTED] User {user_id} started new order submission at {datetime.now()}") 

    if order_id and channel_id:
        client.chat_postMessage(
            channel=channel_id,
            text = 'Which of the following delivery apps do you use?', 
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn", 
                    "text": f"*Order #{order_id} Started*\nWhich of the following delivery apps do you use?"
                }
            }, {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Uber Eats"},
                        "action_id": "select_app_uber",
                        "value": "uber"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "DoorDash"},
                        "action_id": "select_app_doordash",
                        "value": "doordash" 
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Grubhub"},
                        "action_id": "select_app_grubhub",
                        "value": "grubhub"
                    }
                ]
            }]
        )
        say(f"Created private channel for your order: <#{channel_id}>")
    else:
        print("Failed to create order channel")
        say("Failed to create order channel.")

@app.action("select_app_uber")
def handle_app_selection(ack, body, say):
    """Handle delivery app selection"""
    ack()
    channel_id = get_order_channel(body)
    app_used = "uber"
    ts = body["container"]["message_ts"]  # Get the timestamp of the original message
    
    # Create a friendly name for display
    app_display_names = {
        "uber": "Uber Eats",
        "doordash": "DoorDash",
        "grubhub": "Grubhub"
    }
    app_display_name = app_display_names.get(app_used, app_used.capitalize())
    
    # Update the original message to show selection
    client.chat_update(
        channel=channel_id,
        ts=ts,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn", 
                    "text": f"*Order App Selected*\nGreat! You picked *{app_display_name}*. Now, we will move on to submitting your screenshots!"
                }
            }, 
            {
                "type": "section", 
                "text": {
                    "type": "mrkdwn", 
                    "text": f"The first screenshot you need to upload is the *order submission* screenshot. This is usually taken right after you make an order through {app_display_name} and includes information about the *current time*⏱︎ , *restaurant name*🍽️, and *estimated delivery time/window*🪟. Please give snack\'n\'go a few seconds to process your image before we proceed to the next step 🙂"
                }
            }
        ],
        text=f"You selected {app_display_name}"
    )
    
    # Update the database and proceed to the next step
    if update_order(channel_id, {"app_used": app_used, "status": "awaiting_initial_screenshot"}):
        # Send a new message for the next step
        client.chat_postMessage(
            channel=channel_id,
            text=ORDER_STAGES['awaiting_initial_screenshot']['prompt'],
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ORDER_STAGES['awaiting_initial_screenshot']['prompt']
                    }
                }
            ]
        )

@app.action("select_app_doordash")
def handle_app_selection(ack, body, say):
    """Handle delivery app selection"""
    ack()
    channel_id = get_order_channel(body)
    app_used = "doordash"
    ts = body["container"]["message_ts"]  # Get the timestamp of the original message
    
    # Create a friendly name for display
    app_display_names = {
        "uber": "Uber Eats",
        "doordash": "DoorDash",
        "grubhub": "Grubhub"
    }
    app_display_name = app_display_names.get(app_used, app_used.capitalize())
    
    # Update the original message to show selection
    client.chat_update(
        channel=channel_id,
        ts=ts,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn", 
                    "text": f"*Order App Selected*\nYou selected: *{app_display_name}*"
                }
            }
        ],
        text=f"You selected {app_display_name}"
    )
    
    # Update the database and proceed to the next step
    if update_order(channel_id, {"app_used": app_used, "status": "awaiting_initial_screenshot"}):
        # Send a new message for the next step
        client.chat_postMessage(
            channel=channel_id,
            text=ORDER_STAGES['awaiting_initial_screenshot']['prompt'],
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ORDER_STAGES['awaiting_initial_screenshot']['prompt']
                    }
                }
            ]
        )

@app.action("select_app_grubhub")
def handle_app_selection(ack, body, say):
    """Handle delivery app selection"""
    ack()
    channel_id = get_order_channel(body)
    app_used = "grubhub"
    ts = body["container"]["message_ts"]  # Get the timestamp of the original message
    
    # Create a friendly name for display
    app_display_names = {
        "uber": "Uber Eats",
        "doordash": "DoorDash",
        "grubhub": "Grubhub"
    }
    app_display_name = app_display_names.get(app_used, app_used.capitalize())
    
    # Update the original message to show selection
    client.chat_update(
        channel=channel_id,
        ts=ts,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn", 
                    "text": f"*Order App Selected*\nYou selected: *{app_display_name}*"
                }
            }
        ],
        text=f"You selected {app_display_name}"
    )
    
    # Update the database and proceed to the next step
    if update_order(channel_id, {"app_used": app_used, "status": "awaiting_initial_screenshot"}):
        # Send a new message for the next step
        client.chat_postMessage(
            channel=channel_id,
            text=ORDER_STAGES['awaiting_initial_screenshot']['prompt'],
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ORDER_STAGES['awaiting_initial_screenshot']['prompt']
                    }
                }
            ]
        )

@app.action("verify_field_yes")
def handle_verification_yes(ack, body, say):
    ack()
    channel_id = body["container"]["channel_id"]
    ts = body["container"]["message_ts"]
    field = body["actions"][0]["value"].split("|")[0]
    user_id = body["user"]["id"]

    print(f"[VERIFICATION] User {user_id} confirmed field {field} in channel {channel_id}", datetime.now())
    
    # Update message to show decision
    update_message_after_action(
        client,
        channel_id,
        ts,
        body["message"]["blocks"],
        f"✅ Confirmed {field.replace('_', ' ')}"
    )

    field, verification_flag = body["actions"][0]["value"].split("|")
    
    if update_order(channel_id, {verification_flag: True}):
        start_field_verification(channel_id, client)

@app.action("verify_field_no")
def handle_verification_no(ack, body, client):
    """Handle when user indicates a field is incorrect"""
    ack()
    channel_id = body["container"]["channel_id"]
    ts = body["container"]["message_ts"]
    
    # Update message to disable buttons
    update_message_after_action(client, channel_id, ts, body["message"]["blocks"], "Information Incorrect")
    
    field = body["actions"][0]["value"]
    send_input_prompt(channel_id, field, is_missing=False, client=client)

@app.action("check_account_status")
def handle_check_account_status(ack, body, say):
    """Show user their account status and history"""
    ack()
    user_id = body["user"]["id"]
    
    try:
        # Get user data from database
        user_data = db_operation(
            "SELECT * FROM users WHERE id = %s",
            (user_id,),
            fetch_one=True
        )
        
        if user_data:
            # Get order statistics
            total_orders = db_operation(
                "SELECT COUNT(*) FROM orders WHERE user_id = %s",
                (user_id,),
                fetch_one=True
            )['COUNT(*)']
            
            completed_orders = db_operation(
                "SELECT COUNT(*) FROM orders WHERE user_id = %s AND status = 'completed'",
                (user_id,),
                fetch_one=True
            )['COUNT(*)']
            
            rejected_orders = db_operation(
                "SELECT COUNT(*) FROM orders WHERE user_id = %s AND status = 'rejected'",
                (user_id,),
                fetch_one=True
            )['COUNT(*)']
            
            pending_orders = db_operation(
                """SELECT COUNT(*) FROM orders WHERE user_id = %s 
                   AND status NOT IN ('completed', 'rejected')""",
                (user_id,),
                fetch_one=True
            )['COUNT(*)']
            
            # Get the most recent orders for history
            recent_orders = db_operation(
                """SELECT order_id, restaurant_name, status, channel_creation_time 
                FROM orders WHERE user_id = %s 
                ORDER BY channel_creation_time DESC LIMIT 5""",
                (user_id,),
                fetch_one=False
            )
            
            # Format recent orders for display
            orders_history = "\n".join(
                [f"- Order #{o['order_id']}: {o['restaurant_name']} ({o['status']})" 
                 for o in recent_orders]
            ) if recent_orders else "No recent orders"

            compensation_type = user_data['compensation_category']
            if compensation_type == 'staged_raffle':
                explanation_link = "<https://docs.google.com/document/d/1sip1ct22LFrP4dXjwdH0j_A7hBjtvsFUCwKPhRTvS8w/edit?usp=sharing | What does this mean?>"
            elif compensation_type == 'submission_count':
                explanation_link = "<https://docs.google.com/document/d/1Cri52reeZ2jFT0YkGvPEu04LvAQYYFd8dNCzD2tvNnc/edit?usp=sharing | What does this mean?>"
            else:
                explanation_link = ""
            
            # Format the message
            message = f"""
                *Your Account Status:*
- Username: {user_data['username']}
- Account Status: {user_data['status'].capitalize()}
- Compensation Type: {compensation_type.replace('_', ' ').title()} {explanation_link}

*Order Statistics:*
- Total orders submitted: {total_orders}
- Completed orders: {completed_orders}
- Rejected orders: {rejected_orders}
- Pending orders: {pending_orders}

*Recent Order History:*
{orders_history}
            """
            
            say(message.strip())
        else:
            say("No account information found. ")
            
    except Exception as e:
        say("Sorry, I couldn't retrieve your account information. ")
        print(f"Error getting account status: {e}")

def start_field_verification(channel_id, client):
    """
    Starts or continues the verification process for order fields.
    Checks which fields need verification and prompts the user accordingly.
    
    Args:
        channel_id: The Slack channel ID associated with the order
        client: The Slack WebClient instance
    """
    # Get the current order information
    order = get_order_info(channel_id)
    if not order:
        client.chat_postMessage(
            channel=channel_id,
            text="No active order", 
            blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "⚠️ *No active order found in this channel*\n"
                            "To start a new order submission, please go to the main channel and click 'Submit New Order'."
                        )
                    }
                }]
        )
        return
    
    # Determine which field needs verification next
    field, verification_flag = get_next_unverified_field(order)
    
    if not field:
        # All fields verified - move to next stage
        handle_stage_completion(order, client)
        return
    
    # Get current value of the field
    field_value = order.get(field)
    
    # Create the blocks payload correctly
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{field.replace('_', ' ').title()}*: "
                    f"{format_field_for_display(field, field_value)}\n"
                    "Is this correct?"
                )
            }
        },
        {
            "type": "actions",
            "elements": [
                create_button("✅ Yes", "verify_field_yes", f"{field}|{verification_flag}"),
                create_button("✏️ No", "verify_field_no", field)
            ]
        }
    ]
    
    client.chat_postMessage(
        channel=channel_id,
        text='Field verification prompt',
        blocks=blocks
    )

def check_for_missing_info(channel_id, client):
    """Check if any required fields are missing and prompt for them"""
    order = get_order_info(channel_id)
    if not order:
        return client.chat_postMessage(
            channel=channel_id, 
            text = "Order not found"
        )
    
    required_fields = [
        ('restaurant_name', 'is_restaurant_name_verified'),
        ('order_placement_time', 'is_order_placement_time_verified'),
        ('earliest_estimated_arrival_time', 'is_earliest_estimated_arrival_time_verified'),
        ('latest_estimated_arrival_time', 'is_latest_estimated_arrival_time_verified'),
        ('order_completion_time', 'is_order_completion_time_verified')
    ]
    
    missing_fields = [
        field for field, flag in required_fields 
        if not order.get(field) and not order.get(flag)
    ]
    
    if missing_fields:
        client.chat_postMessage(
            channel=channel_id, 
            text = "We're missing some information:"
        )
        for field in missing_fields:
            send_input_prompt(channel_id, field, is_missing=True, client=client)
    else:
        # No missing info, complete the order
        if update_order(channel_id, {'status': 'completed'}):
            client.chat_postMessage(
                channel=channel_id, 
                text = "Thank you! Your order submission is complete.",
                blocks = [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "Thank you for submitting your screenshots and verifying the times on those screenshots! Your order submission is now complete. You\'ve finished everything required on your end, and we\'ll take it from here."
                            }
                        }, 
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "If you encounter a bug, typo, or other error at any point in the order submission process or other issues, feel free to fill out this <https://docs.google.com/forms/d/e/1FAIpQLSe7U05qgO7AUrkEcH4brPSnPAsvjgfcE3kEhOrg1b8ZoNPWdA/viewform?usp=sharing | form>!"
                            }
                        }
                ]
            )

if __name__ == "__main__":
    # TODO? Figure out why team join doesnt work when app starts
    user_store = get_all_users_info()
    messenger.add_users(user_store)
    send_welcome_message(user_store.keys())
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()