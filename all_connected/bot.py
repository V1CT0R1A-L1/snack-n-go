"""
Author: Victoria Li, based on work from Amy Fung & Cynthia Wang & Sofia Kobayashi & Helen Mao
Date: 03/29/2025
Description: The main Slack bot logic for the food delivery data collection project
"""

import os
from pathlib import Path
from dotenv import load_dotenv
import json
import requests
import pymysql
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from datetime import datetime
from helper_functions import *
from gemini import *
import messenger
import re
import certifi
from datetime import datetime
from auto_verification import process_screenshot_with_auto_verify



## Load environment variables ##
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

### CONSTANTS ###
DB_NAME = os.environ.get('DB_NAME')
BOT_ID=WebClient(token=os.environ.get('SLACK_BOT_TOKEN')).api_call("auth.test")['user_id']

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
        'prompt': "Please upload your *order submission screenshot* now. This should include the current time, restaurant name, and estimated delivery time.",
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
                result = None
            conn.commit()
            return result
    except Exception as e:
        print(f"Database error in db_operation: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return None
    finally:
        if conn:
            conn.close()

def update_order_by_id(order_id, updates):
    """Update order fields with column existence check using order_id"""
    if not updates:
        return False
        
    # Get existing columns
    conn = connectDB(DB_NAME)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SHOW COLUMNS FROM orders")
            existing_columns = {col['Field'] for col in cursor.fetchall()}
            
            valid_updates = {k: v for k, v in updates.items() if k in existing_columns} 
            
            if not valid_updates:
                return False
                
            set_clause = ", ".join([f"{k} = %s" for k in valid_updates])
            
            query = f"UPDATE orders SET {set_clause} WHERE order_id = %s" 
            
            params = tuple(valid_updates.values()) + (order_id,) 
            
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        print(f"Database error in update_order_by_id: {e}") 
        return False
    finally:
        if conn:
            conn.close()

def create_order_in_DB(user_id):
    """Create a new order record with Unix timestamps"""
    conn = None
    try:
        conn = connectDB(DB_NAME)
        with conn.cursor() as cursor:
            # Modified query for MySQL compatibility
            cursor.execute(
                """INSERT INTO orders 
                   (user_id, start_submission_timestamp, status) 
                   VALUES (%s, %s, 'awaiting_app_selection')""",
                (user_id, get_current_unix_time())
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

def get_next_unverified_field(order, skip_auto_verified=False):
    """Determine which field to verify next - only returns fields with actual values"""
    print(f"--- GET NEXT UNVERIFIED FIELD DEBUG ---")
    print(f"Order ID: {order.get('order_id')}")
    print(f"Skip auto-verified: {skip_auto_verified}")
   
    # Get auto-verified fields if skipping is enabled
    auto_verified_fields = set()
    if skip_auto_verified:
        auto_verified_json = order.get('auto_verified_fields')
        if auto_verified_json:
            try:
                auto_verified_fields = set(json.loads(auto_verified_json))
                print(f"Auto-verified fields to skip: {auto_verified_fields}")
            except json.JSONDecodeError:
                print("Failed to parse auto_verified_fields JSON")

    verification_order = [
        ('restaurant_name', 'is_restaurant_name_verified'),
        ('order_placement_time', 'is_order_placement_time_verified'),
        ('earliest_estimated_arrival_time', 'is_earliest_estimated_arrival_time_verified'),
        ('latest_estimated_arrival_time', 'is_latest_estimated_arrival_time_verified'),
        ('order_completion_time', 'is_order_completion_time_verified'),
        ('restaurant_address', 'is_restaurant_address_verified')
    ]
   
    for field, verification_flag in verification_order:
        if skip_auto_verified and field in auto_verified_fields:
            print(f"Skipping auto-verified field: {field}")
            continue
           
        field_value = order.get(field)
        is_verified = order.get(verification_flag, False)
       
        print(f"Checking field: {field}, value: {field_value}, verified: {is_verified}")
       
        if field_value is not None and not is_verified:
            print(f"Found next field: {field}, verification_flag: {verification_flag}")
            return field, verification_flag
   
    print("No unverified fields found")
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

    user_id = None
    try:
        channel_info = client.conversations_info(channel=channel_id)['channel']
        
        if channel_info.get('is_im'):
            if 'user' in file: 
                user_id = file['user'] 
            else:
                response = client.conversations_members(channel=channel_id)
                members = response['members']
                user_id = next((m for m in members if m != BOT_ID), None)
            
            if not user_id:
                raise Exception("Could not find user_id from DM channel members.")

        else:
            raise Exception("Channel is not a DM (IM) channel.") 

    except Exception as e:
        print(f"Error determining user_id from DM: {e}")
        client.chat_postMessage(channel=channel_id, text="Error: Could not determine the active user.")
        return

    order = get_last_active_order_by_user(user_id) 

    if not order:
        client.chat_postMessage(
            channel=channel_id,
            text="No active order found for you. Please start a new submission."
        )
        return
    
    order_id = order['order_id']
    
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
        print(f"[AUTO_VERIFICATION] Starting auto-verification for order {order_id}")
        result = process_screenshot_with_auto_verify(filepath, image_stage, order_id, db_operation)

        if update_order_by_id(order_id, result['updates']):
           
            if not result['needs_manual_verification']:
                # All fields auto-verified! Skip manual verification
                print(f"[AUTO_VERIFICATION] All fields auto-verified for order {order_id}")
                client.chat_postMessage(
                    channel=channel_id,
                    text="✅ All information automatically verified! Moving to next step.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"✅ *Automatic Verification Complete!*\n\nAll {result['auto_verified_count']} fields were automatically verified. Moving to the next step."
                            }
                        }
                    ]
                )
                # Move to next stage directly
                updated_order = get_last_active_order_by_user(user_id)
                handle_stage_completion(updated_order, channel_id, client)
               
            else:
                # Some fields need manual verification
                print(f"[AUTO_VERIFICATION] {result['auto_verified_count']}/{result['total_expected_fields']} fields auto-verified, need manual verification")
               
                if result['auto_verified_count'] > 0:
                    # Show which fields were auto-verified
                    auto_verified_fields = json.loads(result['updates'].get('auto_verified_fields', '[]'))
                    client.chat_postMessage(
                        channel=channel_id,
                        text=f"✅ {result['auto_verified_count']} fields auto-verified, {result['total_expected_fields'] - result['auto_verified_count']} need manual confirmation",
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"✅ *Partial Auto-Verification Complete!*\n\n{result['auto_verified_count']} out of {result['total_expected_fields']} fields were automatically verified. Please verify the remaining information."
                                }
                            }
                        ]
                    )
               
                # Start manual verification for remaining fields
                start_field_verification(order_id, channel_id, client)
            
    except Exception as e:
        error_msg = f"Error processing image: {str(e)}"
        print(error_msg)
        client.chat_postMessage(
            channel=channel_id,
            text=error_msg
        )

def start_field_verification(order_id, channel_id, client):
    # Get the current order information
    order = db_operation("SELECT * FROM orders WHERE order_id = %s", (order_id,), fetch_one=True)

    print(f"[FIELD VERIFICATION] Starting verification for order {order_id}")
    print(f"Order status: {order.get('status')}")
   

    if not order:
        client.chat_postMessage(
            channel=channel_id,
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
    
    # Check if there are auto-verified fields
    auto_verified_json = order.get('auto_verified_fields')
    auto_verified_fields = json.loads(auto_verified_json) if auto_verified_json else []
   
    if auto_verified_fields:
        print(f"[FIELD VERIFICATION] Auto-verified fields: {auto_verified_fields}")
   
    # Determine which field needs verification next (skip auto-verified ones)
    field, verification_flag = get_next_unverified_field(order, skip_auto_verified=True)
    print(f"Next field to verify: {field}, Verification flag: {verification_flag}")
   
    if not field:
        # All fields verified - move to next stage
        print(f"[FIELD VERIFICATION] All fields verified for order {order_id}")
        handle_stage_completion(order, channel_id, client)
        return
   
    # Get current value of the field
    field_value = order.get(field)
    print(f"Field value: {field_value}, Type: {type(field_value)}")

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
    ]
   
    # Send verification prompt
    client.chat_postMessage(
        channel=channel_id,
        text=f'Verification prompt for {field}',
        blocks=blocks
    )

def handle_stage_completion(order, channel_id, client):
    """
    Handles the completion of a verification stage and moves to the next stage.
    """
    current_stage = order['status']
    next_stage = ORDER_STAGES.get(current_stage, {}).get('next')
   
    print(f"[STAGE CHANGE] Channel {channel_id} moving from {current_stage} to {next_stage}", datetime.now())

    if not next_stage:
        # Show completion message with auto-verification stats
        auto_verified_json = order.get('auto_verified_fields')
        auto_verified_count = len(json.loads(auto_verified_json)) if auto_verified_json else 0
       
        completion_text = "🎉 *Thank you!* Your order submission is complete."
        if auto_verified_count > 0:
            completion_text += f"\n\n🤖 *Auto-verification Stats:* {auto_verified_count} fields were automatically verified!"
       
        completion_text += "\n\nFor future reference, you can review the instructions here:\n<https://docs.google.com/document/d/1JOXu2Qwi_I5X__FwH6g0dlyMh-QxqCFeLo3s5l5ImjI/edit?usp=sharing | order submission instructions document>"
       
        client.chat_postMessage(
            channel=channel_id,
            text="Thank you! Submission complete.",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": completion_text
                    }
                }
            ]
        )
        return
    
    # Update to next stage
    if update_order_by_id(order['order_id'], {'status': next_stage}): 
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
            order_id = order['order_id']
            check_for_missing_info(order_id, channel_id, client) 

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
        text=f"*Decision:* {decision_text}",
        blocks=new_blocks
    )

def get_last_active_order_by_user(user_id):
    """
    Get the most recent active order for a user.
    'Active' = any status other than 'completed' or 'rejected'.
    """
    return db_operation(
        """
        SELECT * FROM orders 
        WHERE user_id = %s 
        AND status NOT IN ('completed', 'rejected')
        ORDER BY order_id DESC 
        LIMIT 1
        """,
        (user_id,),
        fetch_one=True
    )

def check_for_missing_info(order_id, channel_id, client):
    """Check if any required fields are missing and prompt for them"""
    order = db_operation("SELECT * FROM orders WHERE order_id = %s", (order_id,), fetch_one=True)
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
        if update_order_by_id(order_id, {'status': 'completed'}):
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

### MESSAGE HANDLERS ###
@app.event("file_created")
def handle_file_created_events(body, logger):
    logger.info(body)

@app.event("message")
def handle_message(payload, say):
    """Handle text messages and messages with files"""

    channel_id = payload.get('channel')
    user_id = payload.get('user')
    text = payload.get('text', '').strip().lower()
    subtype = payload.get('subtype')

    if user_id == BOT_ID:
        return

    if subtype == 'channel_join':
        print(f"[CHANNEL JOIN] User {user_id} joined channel {channel_id}", datetime.now())
        return

    print(f"[USER MESSAGE] Message from {user_id}: {text}", datetime.now())
    if text in ["help", "?"]:
        print(f"[HELP REQUEST] User {user_id} requested help", datetime.now())
        say(text="Here's how I can help you!",
            blocks=MESSAGE_BLOCKS["main_channel_welcome_message"]['blocks'])
        return
    if 'files' in payload:
        if text == '': 
            print("[FILE UPLOAD] Detected file with empty message text. Skipping to avoid duplication.")
            return
        return
    else:
        say()

@app.action("process_input")
def handle_user_input(ack, body, say, logger, client):
    ack()
    user_id=body["user"]["id"]
    order = get_last_active_order_by_user(user_id)
    if not order:
        say(channel=body["container"]["channel_id"], text="⚠️ Error: No active order found to update.")
        return
    order_id = order['order_id']
    channel_id = body["container"]["channel_id"] 
    try:
        state_values = body["state"]["values"]
        value = None
        field = None
        try:

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

        # Process the update
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

            print(f"[USER INPUT] User {user_id} provided input for {field}: {value}", datetime.now())
            
            if "missing_" in block_id:
                updates[f"is_{field}_verified"] = True

            if update_order_by_id(order_id, updates): 
                if "missing_" in block_id:
                    check_for_missing_info(order_id, channel_id, client) 
                else:
                    start_field_verification(order_id, channel_id, client) 

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
    user_id = body["event"]["user_id"]

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
    order_id = create_order_in_DB(user_id)
    print(f"[ORDER STARTED] User {user_id} started new order submission at {datetime.now()}") 

    if order_id:
        try:
            response = client.conversations_open(users=[user_id])
            dm_channel_id = response["channel"]["id"]
            channel_to_post = dm_channel_id 
        except SlackApiError as e:
            print(f"Failed to open DM channel: {e.response['error']}")
            say("Failed to start order submission due to a Slack issue.") 
            return
        
        client.chat_postMessage(
            channel=channel_to_post,
            text=f"You've started an order submission! Send 'help' or '?' to end submission.",
        )
        
        client.chat_postMessage(
            channel=channel_to_post,
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
                        "value": f"uber__{order_id}"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "DoorDash"},
                        "action_id": "select_app_doordash",
                        "value": f"doordash__{order_id}"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Grubhub"},
                        "action_id": "select_app_grubhub",
                        "value": f"grubhub__{order_id}"
                    }
                ]
            }]
        )
    else:
        print("Failed to start a new order submission. ")
        say("Failed to start a new order submission. ")

@app.action(re.compile(r"select_app_.+"))
def handle_app_selection(ack, body, say):
    """Handle delivery app selection"""
    ack()

    channel_id = body["container"]["channel_id"] 
    ts = body["container"]["message_ts"]

    action_value = body["actions"][0]["value"]
    app_used, order_id_str = action_value.split("__")
    order_id = int(order_id_str)
    
    # Create a friendly name for display
    app_display_names = {
        "uber": "Uber Eats",
        "doordash": "DoorDash",
        "grubhub": "Grubhub"
    }
    app_display_name = app_display_names.get(app_used)
    
    # Update the original message to show selection
    try:
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
    except SlackApiError as e:
        print(f"Failed to update message: {e.response['error']}")
        # Post a new message if update fails
        say(channel=channel_id, text=f"I tried to update the message but failed. You selected {app_display_name}. Proceeding to the next step.")

    if update_order_by_id(order_id, {"app_used": app_used, "status": "awaiting_initial_screenshot"}):
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
        pass
    else:
        client.chat_postMessage(channel=channel_id, text=f"⚠️ Internal error: Failed to update Order #{order_id} in the database.")
    
@app.action("verify_field_yes")
def handle_verification_yes(ack, body, say):
    ack()
    channel_id = body["container"]["channel_id"]
    ts = body["container"]["message_ts"]
    field = body["actions"][0]["value"].split("|")[0]
    user_id = body["user"]["id"]

    order = get_last_active_order_by_user(user_id)
    if not order:
        client.chat_postMessage(channel=channel_id, text="⚠️ Error: No active order found to update.")
        return
    order_id = order['order_id']

    print(f"[VERIFICATION] User {user_id} confirmed field {field} in order {order_id}", datetime.now())
    
    # Update message to show decision
    update_message_after_action(
        client,
        channel_id,
        ts,
        body["message"]["blocks"],
        f"✅ Confirmed {field.replace('_', ' ')}"
    )

    field, verification_flag = body["actions"][0]["value"].split("|")
    
    if update_order_by_id(order_id, {verification_flag: True}):
        start_field_verification(order_id, channel_id, client) 
    else:
        client.chat_postMessage(channel=channel_id, text=f"⚠️ Internal error: Failed to update Order #{order_id} in the database.")

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
    channel_id = body["container"]["channel_id"] 

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
- Total orders: {total_orders}
- Completed orders: {completed_orders}
- Rejected orders: {rejected_orders}
- Pending orders: {pending_orders}

*Recent Order History:*
{orders_history}
            """
            
            client.chat_postMessage(channel=channel_id, text=message.strip())
        else:
            say("No account information found. ")
            
    except Exception as e:
        client.chat_postMessage(channel=channel_id, text="No account information found.")
        print(f"Error getting account status: {e}")

if __name__ == "__main__":
    # TODO? Figure out why team join doesnt work when app starts
    user_store = get_all_users_info()
    messenger.add_users(user_store)
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()