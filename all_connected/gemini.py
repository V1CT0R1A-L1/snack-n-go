'''
Author: Amelia Zhang
Date: 03/28/2025
Description: 
'''

import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv
from datetime import datetime
import re
import time
from pathlib import Path
import os

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

# configure the Gemini API
GOOGLE_API_KEY = DB_NAME = os.environ.get('GOOGLE_API_KEY') # Get API key from environment variable
if not GOOGLE_API_KEY:
    raise ValueError("No GOOGLE_API_KEY found in environment variables.  Please set it.")

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# extract timestamp from an image using the Gemini API
def test_image_extraction(image_path):

    try:
        # Load the image
        img = Image.open(image_path)

        # Load the model
        model = genai.GenerativeModel("gemini-1.5-flash")

        # Generate content
        response = model.generate_content([img, "extract the time screenshot was made, usually top left of screen if iphone aka current time and extract any other times if provided in image eg order placement or completion time."])
        # response = model.generate_content([img, "extract metadata of the screenshot, specifically the time of when the ss was made."])


        return response.text

    except Exception as e:
        print(f"Error processing image: {e}")
        return None

# Example usage:
# if __name__ == "__main__":
#     image_file = "ss/uber-orderplacement.PNG"  # Replace with the path to your image file
#     info = test_image_extraction(image_file)

#     if info:
#         print(f"Time extracted :\n{info}")
#     else:
#         print("Failed to generate image description.")

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ SNACK'N'GO FUNCTION ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

def gemini_process_image(image_path, image_stage):
    """
    Processes food delivery screenshot based on stage and returns data in consistent format.
    
    Args:
        image_path: Path to the image file
        image_stage: Either 'awaiting_placement_time' or 'awaiting_arrival_time'
        
    Returns:
        Dictionary with extracted data matching database schema:
        {
            "restaurant_name": str or None,
            "restaurant_address": str or None,
            "order_placement_time": unix timestamp or None,
            "earliest_estimated_arrival_time": unix timestamp or None,
            "latest_estimated_arrival_time": unix timestamp or None,
            "order_completion_time": unix timestamp or None
        }
    """
    try:
        img = Image.open(image_path)
        
        # Initialize result structure matching database schema
        result = {
            "restaurant_name": None,
            "restaurant_address": None,
            "order_placement_time": None,
            "earliest_estimated_arrival_time": None,
            "latest_estimated_arrival_time": None,
            "order_completion_time": None
        }
        
        # Get restaurant info
        restaurant_info = extract_restaurant_info(img)
        result.update(restaurant_info)
        
        # Process based on stage
        if image_stage == "awaiting_placement_time":
            time_data = extract_initial_times(img)
            result.update(time_data)
        elif image_stage == "awaiting_arrival_time":
            completion_time = extract_completion_time(img)
            result["order_completion_time"] = completion_time
        
        return result
        
    except Exception as e:
        print(f"Error processing {image_stage} image: {e}")
        return {
            "restaurant_name": None,
            "restaurant_address": None,
            "order_placement_time": None,
            "earliest_estimated_arrival_time": None,
            "latest_estimated_arrival_time": None,
            "order_completion_time": None
        }

def extract_restaurant_info(img):
    """Extract restaurant name and address from image"""
    response = model.generate_content([
        img,
        "Extract: 1. Restaurant name (if shown) "
        "2. Restaurant address (if shown). "
        "Return as: 'Name: x, Address: y' or just what's available."
        "Do not include ', Address: y' if address is not shown. "
    ])
    
    info = {"restaurant_name": None, "restaurant_address": None}
    location_text = response.text
    
    if "name:" in location_text.lower():
        info["restaurant_name"] = location_text.split("Name:")[1].split(",")[0].strip()
    if "address:" in location_text.lower():
        info["restaurant_address"] = location_text.split("Address:")[1].strip()
    elif "address" not in location_text.lower() and "," in location_text:
        info["restaurant_address"] = location_text.strip()
        
    return info

def extract_initial_times(img):
    """Extract order placement and estimated arrival times"""
    response = model.generate_content([
        img,
        "Extract the following times separately and adjust their AM/PM logically if needed. Follow these principles:\n"
        "1. **Relative Consistency:** If two times appear in the same context (e.g., order time and delivery time), ensure their relationship makes sense (e.g., delivery cannot be before ordering).\n"
        "2. **24-Hour Clues:** If any time is in 24-hour format (e.g., '20:45'), assume other times nearby should align (e.g., '8:17' becomes '20:17').\n"
        "3. **AM/PM Priority:** If AM/PM labels exist (e.g., '8:17 PM'), trust them. If missing, infer based on activity (e.g., '9:00' with 'Evening Delivery' text → PM).\n"
        "Return in this exact format:\n"
        "Order placement time: [time with AM/PM]\n"
        "Delivery window: [earliest time with AM/PM] - [latest time with AM/PM]"
    ])
    
    print("Raw Gemini response:", response.text)
    
    time_data = {
        "order_placement_time": None,
        "earliest_estimated_arrival_time": None,
        "latest_estimated_arrival_time": None
    }
    
    placement_match = re.search(r"Order placement time:\s*([^\n]+)", response.text)
    window_match = re.search(r"Delivery window:\s*([^\n-]+)\s*-\s*([^\n]+)", response.text)
    
    if placement_match:
        placement_time = placement_match.group(1).strip()
        time_data["order_placement_time"] = convert_to_unix(placement_time)
    
    if window_match:
        earliest_time = window_match.group(1).strip()
        latest_time = window_match.group(2).strip()
        time_data["earliest_estimated_arrival_time"] = convert_to_unix(earliest_time)
        time_data["latest_estimated_arrival_time"] = convert_to_unix(latest_time)
    
    return time_data

def extract_completion_time(img):
    """Extract order completion time"""
    response = model.generate_content([
        img,
        "Extract when the order was delivered/completed. "
        "Return just the time in its original format."
    ])
    
    timestamps = process_gemini_response(response.text)
    if timestamps:
        return timestamps.get("time_1")
    return None

def convert_to_unix(time_string, am_pm_context=None):
    try:
        # Attempt to parse the time string with different formats
        # Add more formats as needed based on Gemini's output
        formats_to_try = [
            "%I:%M %p",  # e.g., "10:30 AM"
            "%I:%M%p",   # e.g., "10:30AM"
            "%H:%M",      # e.g., "10:30"  (24-hour format)
            # "%H:%M:%S",  # e.g., "10:30:00"
            # "%I:%M:%S %p", # e.g. "10:30:00 AM"
            # "%m/%d/%Y %I:%M %p", # e.g. "03/15/2024 02:30 PM"
            # "%m/%d/%y %I:%M %p", # e.g. "03/15/24 02:30 PM"
            # "%m-%d-%Y %I:%M %p", # e.g. "03-15-2024 02:30 PM"
            # "%m-%d-%y %I:%M %p", # e.g. "03-15-24 02:30 PM"
            # "%Y-%m-%d %H:%M:%S", # e.g. "2024-03-15 14:30:00"
            # "%Y/%m/%d %H:%M:%S", # e.g. "2024/03/15 14:30:00"
            # "%d %b %Y %H:%M:%S", # e.g. "15 Mar 2024 14:30:00"
            # "%d %B %Y %H:%M:%S", # e.g. "15 March 2024 14:30:00"
            "%d %b %Y %I:%M %p", # e.g. "15 Mar 2024 02:30 PM"
            "%d %B %Y %I:%M %p", # e.g. "15 March 2024 02:30 PM"
            "%B %d, %Y at %I:%M %p", # e.g. "March 15, 2024 at 02:30 PM"
            "%b %d, %Y at %I:%M %p", # e.g. "Mar 15, 2024 at 02:30 PM"

            #Add more date and time formats here as needed...
        ]

        datetime_object = None #Initialize variable

        for format_str in formats_to_try:
            try:
                datetime_object = datetime.strptime(time_string, format_str)
                break # Exit the loop if parsing succeeds
            except ValueError:
                pass  # Ignore and try the next format

        if datetime_object is None and am_pm_context:
            if re.search(r"^\d{1,2}:\d{2}(?::\d{2})?$", time_string): #if time string is 24 hour format.
                for format_str in ["%I:%M %p", "%I:%M%p"]:
                    try:
                        datetime_object = datetime.strptime(time_string + " " + am_pm_context, format_str)
                        break
                    except ValueError:
                        pass
        if datetime_object is None:
            print(f"Failed to parse time string: {time_string}")
            return None

        # If the parsed datetime object doesn't contain the year, month, or day, then we assume it is todays date.
        now = datetime.now()
        if datetime_object.year == 1900: # default year when only time is extracted.
            datetime_object = datetime_object.replace(year=now.year)
        if datetime_object.month == 1: # month defaults to 1 when only time is extracted.
             datetime_object = datetime_object.replace(month=now.month)
        if datetime_object.day == 1: # day defaults to 1 when only time is extracted.
            datetime_object = datetime_object.replace(day=now.day)


        timestamp = int(time.mktime(datetime_object.timetuple()))
        return timestamp

    except ValueError as e:
        print(f"Error converting time string '{time_string}' to Unix timestamp: {e}")
        return None

# take gemini info and convert extracted time to unix timestamp
def process_gemini_response(response_text):
    print("gemini raw response: ", response_text)
    time_data = {}

    # Simple regex to find time patterns (adjust as needed based on Gemini's response format)
    time_patterns = [
        r"(\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)",  # Matches 1:00 PM, 01:00 PM, 1:00:00 PM, 01:00:00 PM
        r"(\d{1,2}:\d{2}(?::\d{2})?)(?!\s*[AP]M)", #only 24 hour times if no AM/PM
        r"(\d{2}[/-]\d{2}[/-]\d{2,4}\s*\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)",
        r"(\d{4}[/-]\d{2}[/-]\d{2}\s*\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)",
        r"(\d{2}\s*[A-Za-z]{3,9}\s*\d{4}\s*\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)",
        r"([A-Za-z]{3,9}\s*\d{1,2},\s*\d{4}\s*at\s*\d{1,2}:\d{2}\s*[AP]M)",
        # Add more patterns as needed

        # note: I removed the 24 hour patterns cause they were causing issues by printing out duplicate times
    ]

    extracted_times = []
    for pattern in time_patterns:
        extracted_times.extend(re.findall(pattern, response_text, re.IGNORECASE)) #Added ignore case as sometimes it is lowercase

    # for debugging
    print (f"Extracted times: {extracted_times}")

    # Deduplicate, prioritizing AM/PM times
    unique_times = []
    seen_times = set()
    am_pm_counts = {"AM": 0, "PM": 0}

    for time_str in extracted_times:
        normalized_time = re.sub(r"\s*[AP]M", "", time_str.strip().lower()).strip()
        if normalized_time not in seen_times:
            seen_times.add(normalized_time)
            unique_times.append(time_str)
            am_pm_match = re.search(r"([AP]M)", time_str, re.IGNORECASE)
            if am_pm_match:
                am_pm_counts[am_pm_match.group(1).upper()] += 1

    print(f"Unique times: {unique_times}")

    # dominant am_pm used for ss time without AM/PM -> we're assuming it's the same as the most common AM/PM time
    dominant_am_pm = None
    if am_pm_counts["AM"] > am_pm_counts["PM"]:
        dominant_am_pm = "AM"
    elif am_pm_counts["PM"] > am_pm_counts["AM"]:
        dominant_am_pm = "PM"

    if not unique_times:
        print("No times found in Gemini response.")
        return {}

    for i, time_str in enumerate(unique_times):
        if re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)(?!\s*[AP]M)", time_str): #if it is a time without am/pm
            time_data[f"time_{i+1}"] = convert_to_unix(time_str.strip(), dominant_am_pm)
        else:
            time_data[f"time_{i+1}"] = convert_to_unix(time_str)

    # Remove any entries where the conversion failed:
    time_data = {k: v for k, v in time_data.items() if v is not None}

    return time_data


# Example usage:
if __name__ == "__main__":
    image_file = "ss/uber-orderplacement.PNG"  # uber order placement
    image_file2 = "ss/uber-ordercomplete.jpeg"  # uber order completion
    image_file3 = "ss/dd-ordercomplete.jpeg"  # doordash order completion

    # info = process_image(image_file, "order-placement", "uber")
    # info = process_image(image_file2, "order-completion", "uber")
    info = gemini_process_image(image_file3, "final")

    if info:
        print(f"Gemini extracted :\n{info}")

        timestamps = process_gemini_response(info)

        if timestamps:
            print("\nExtracted Timestamps:")
            for label, timestamp in timestamps.items():
                print(f"{label}: {timestamp}")
        else:
            print("No valid timestamps could be extracted.")
    else:
        print("Failed to generate image description.")