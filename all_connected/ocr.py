'''
Author: Victoria Li
Date: 10/14/2025
Description: Optimized OCR-based time extraction for food delivery screenshots
'''

import easyocr
import cv2
import numpy as np
from PIL import Image
import re
import time
from datetime import datetime
from pathlib import Path

def initialize_ocr_reader():
    """Initialize EasyOCR reader"""
    return easyocr.Reader(['en'], gpu=False)

def ocr_process_image(image_path, image_stage):
    """
    Optimized time extraction for food delivery screenshots
    
    Args:
        image_path: Path to the image file
        image_stage: 'awaiting_placement_time' or 'awaiting_arrival_time'
    """
    try:
        reader = initialize_ocr_reader()
        img = Image.open(image_path)
        img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        
        # Initialize result - focus on times only
        result = {
            "restaurant_name": None,
            "restaurant_address": None, 
            "order_placement_time": None,
            "earliest_estimated_arrival_time": None,
            "latest_estimated_arrival_time": None,
            "order_completion_time": None
        }
        
        print("Performing OCR...")
        ocr_results = reader.readtext(img_cv, detail=1)
        full_text = ' '.join([result[1] for result in ocr_results])
        print(f"Raw OCR Response: {full_text}")
        
        # Extract all unique times with positions
        times_with_positions = extract_times_with_positions(ocr_results, full_text)
        
        if image_stage == "awaiting_placement_time":
            placement_data = extract_placement_times(times_with_positions, full_text, img_cv.shape)
            result.update(placement_data)
        elif image_stage == "awaiting_arrival_time":
            completion_time = extract_completion_time_optimized(times_with_positions, full_text)
            result["order_completion_time"] = completion_time
        
        return result
        
    except Exception as e:
        print(f"Error processing image: {e}")
        return create_empty_result()

def extract_times_with_positions(ocr_results, full_text):
    times = []
    
    time_patterns = [
        r'(\d{1,2}:\d{2}\s*[AP]M)',  # 8:45 PM
        r'(\d{1,2}\.\d{2}\s*[AP]M)', # 8.45 PM (with dots)
        r'(\d{1,2}:\d{2})',          # 8:45
        r'(\d{1,2}\s*[AP]M)',        # 8 PM
    ]
    
    all_time_strings = []
    for pattern in time_patterns:
        matches = re.findall(pattern, full_text, re.IGNORECASE)
        all_time_strings.extend(matches)
    
    print(f"All time strings found: {all_time_strings}")
    
    # For each OCR result, check if it contains times and record position
    for bbox, text, confidence in ocr_results:
        for time_str in all_time_strings:
            if time_str in text:
                # Calculate center position of bounding box
                center_x, center_y = get_bbox_center(bbox)
                times.append({
                    'time_str': time_str,
                    'center_x': center_x,
                    'center_y': center_y, 
                    'confidence': confidence,
                    'full_text': text
                })
                # Remove this time string to avoid duplicates
                all_time_strings.remove(time_str)
                break
    
    print(f"Unique times with positions: {[(t['time_str'], t['center_x'], t['center_y']) for t in times]}")
    return times

def extract_placement_times(times_with_positions, full_text, image_shape):
    """
    Extract times specifically for placement stage screenshot
    """
    result = {
        "order_placement_time": None,
        "earliest_estimated_arrival_time": None, 
        "latest_estimated_arrival_time": None
    }
    
    if not times_with_positions:
        print("No times found in image")
        return result
    
    height, width = image_shape[:2]
    print(f"Image dimensions: {width}x{height}")
    
    # Strategy 1: Find AM/PM context from delivery times
    am_pm_context = None
    for time_data in times_with_positions:
        time_str_upper = time_data['time_str'].upper()
        if 'PM' in time_str_upper:
            am_pm_context = "PM"
            break
        elif 'AM' in time_str_upper:
            am_pm_context = "AM"
            break
    
    print(f"Detected AM/PM context: {am_pm_context}")
    
    # Strategy 2: Find top-left corner time (likely placement time)
    top_left_times = []
    for time_data in times_with_positions:
        # Consider top-left quadrant (first 20% of screen)
        if time_data['center_x'] < width * 0.2 and time_data['center_y'] < height * 0.2:
            top_left_times.append(time_data)
            print(f"Found top-left time: {time_data['time_str']} at ({time_data['center_x']}, {time_data['center_y']})")
    
    if top_left_times:
        # Pick the most top-left time (highest and leftmost)
        top_left_times.sort(key=lambda x: (x['center_y'], x['center_x']))
        placement_time_data = top_left_times[0]
        
        # Use AM/PM context for times without explicit AM/PM
        result["order_placement_time"] = convert_to_unix(
            placement_time_data['time_str'], 
            am_pm_context=am_pm_context
        )
        print(f"Selected placement time from top-left: {placement_time_data['time_str']}")
    
    # Strategy 3: Look for delivery time patterns in text
    delivery_patterns = [
        r'Estimated arrival\s*(\d{1,2}[\.:]\d{2}\s*[AP]M)',
        r'Latest arrival by\s*(\d{1,2}[\.:]\d{2}\s*[AP]M)',
        r'Arrival\s*(\d{1,2}[\.:]\d{2}\s*[AP]M)',
        r'Deliver.*?(\d{1,2}[\.:]\d{2}\s*[AP]M)',
        r'Estimated\s*(\d{1,2}[\.:]\d{2}\s*[AP]M)',
        r'by\s*(\d{1,2}[\.:]\d{2}\s*[AP]M)',
    ]
    
    earliest_time = None
    latest_time = None
    
    for pattern in delivery_patterns:
        matches = re.findall(pattern, full_text, re.IGNORECASE)
        if matches:
            time_str = matches[0]
            print(f"Found delivery time with pattern '{pattern}': {time_str}")
            if 'latest' in pattern.lower() or 'by' in pattern.lower():
                latest_time = time_str
            else:
                earliest_time = time_str
    
    # Strategy 4: If no patterns found, use time range detection
    if not earliest_time or not latest_time:
        time_range = find_time_range(full_text)
        if time_range:
            earliest_time, latest_time = time_range
            print(f"Found time range: {earliest_time} - {latest_time}")
    
    # Strategy 5: Fallback - use remaining times by position
    remaining_times = [t for t in times_with_positions 
                      if result["order_placement_time"] is None or 
                      convert_to_unix(t['time_str'], am_pm_context=am_pm_context) != result["order_placement_time"]]
    
    # Sort remaining times by vertical position (top to bottom)
    remaining_times.sort(key=lambda x: x['center_y'])
    
    if earliest_time is None and len(remaining_times) >= 1:
        earliest_time = remaining_times[0]['time_str']
        print(f"Using earliest time from position: {earliest_time}")
    
    if latest_time is None and len(remaining_times) >= 2:
        latest_time = remaining_times[1]['time_str']
        print(f"Using latest time from position: {latest_time}")
    
    # Convert to timestamps
    if earliest_time:
        result["earliest_estimated_arrival_time"] = convert_to_unix(earliest_time)
    if latest_time:
        result["latest_estimated_arrival_time"] = convert_to_unix(latest_time)
    
    return result
def extract_completion_time_optimized(times_with_positions, full_text):
    """
    Extract completion/delivery time for arrival stage screenshot
    """
    if not times_with_positions:
        return None
    
    # Look for delivery completion keywords
    completion_keywords = [
        'delivered', 'arrived', 'completed', 'finished', 
        'is here', 'at your door', 'delivery complete'
    ]
    
    # Check if any time is near completion keywords
    for time_data in times_with_positions:
        text_lower = time_data['full_text'].lower()
        if any(keyword in text_lower for keyword in completion_keywords):
            return convert_to_unix(time_data['time_str'])
    
    # Fallback: use the most prominent time (highest confidence)
    times_with_positions.sort(key=lambda x: x['confidence'], reverse=True)
    if times_with_positions:
        return convert_to_unix(times_with_positions[0]['time_str'])
    
    return None

def find_time_range(text):
    """
    Find time range patterns like '8:45 PM - 9:20 PM'
    """
    range_patterns = [
        r'(\d{1,2}[\.:]\d{2}\s*[AP]M?)\s*[-–to]+\s*(\d{1,2}[\.:]\d{2}\s*[AP]M?)',
        r'(\d{1,2}\s*[AP]M?)\s*[-–to]+\s*(\d{1,2}\s*[AP]M?)',
        r'between\s*(\d{1,2}[\.:]\d{2}\s*[AP]M?)\s*and\s*(\d{1,2}[\.:]\d{2}\s*[AP]M?)',
    ]
    
    for pattern in range_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return (match.group(1), match.group(2))
    
    return None

def get_bbox_center(bbox):
    """Calculate center of bounding box"""
    points = np.array(bbox)
    center_x = np.mean(points[:, 0])
    center_y = np.mean(points[:, 1])
    return center_x, center_y

def create_empty_result():
    """Create empty result structure"""
    return {
        "restaurant_name": None,
        "restaurant_address": None,
        "order_placement_time": None, 
        "earliest_estimated_arrival_time": None,
        "latest_estimated_arrival_time": None,
        "order_completion_time": None
    }

def convert_to_unix(time_string, am_pm_context=None):
    """Convert time string to Unix timestamp"""
    try:
        # Normalize time string (replace dots with colons for parsing)
        original_time = time_string
        time_string = time_string.replace('.', ':')
        
        # Check if time has explicit AM/PM
        has_am_pm = 'AM' in original_time.upper() or 'PM' in original_time.upper()
        
        datetime_object = None

        # If time has no AM/PM but we have context, use it first
        if not has_am_pm and am_pm_context:
            for format_str in ["%I:%M %p", "%I:%M%p"]:
                try:
                    datetime_object = datetime.strptime(time_string + " " + am_pm_context, format_str)
                    break
                except ValueError:
                    pass
        
        # If still no datetime object, try other formats
        if datetime_object is None:
            formats_to_try = [
                "%I:%M %p",  # 8:45 PM
                "%I:%M%p",   # 8:45PM  
                "%H:%M",     # 20:45 (avoid this for ambiguous times)
                "%I %p",     # 8 PM
            ]
            
            for format_str in formats_to_try:
                try:
                    datetime_object = datetime.strptime(time_string, format_str)
                    # If we used 24-hour format for a time without AM/PM, be cautious
                    if format_str == "%H:%M" and not has_am_pm:
                        break
                except ValueError:
                    pass
        
        # Add current date
        now = datetime.now()
        if datetime_object.year == 1900:
            datetime_object = datetime_object.replace(year=now.year, month=now.month, day=now.day)

        timestamp = int(time.mktime(datetime_object.timetuple()))
        readable_time = datetime_object.strftime("%I:%M %p")
        return timestamp

    except Exception as e:
        print(f"Time conversion error for '{time_string}': {e}")
        return None
    

# Example usage with actual image testing
if __name__ == "__main__":
    # Test the OCR implementation
    ss_dir = Path(__file__).parent.parent / "ss"
    image_file = ss_dir / "dd-orderplacement.png"
    
    if image_file.exists():
        print(f"Processing image: {image_file}")
        result = ocr_process_image(image_file, "awaiting_placement_time")
        print("\n" + "="*50)
        print("OCR Extraction Results:")
        print("="*50)
        for key, value in result.items():
            if "time" in key and value is not None:
                # Convert timestamp back to readable time for verification
                readable_time = datetime.fromtimestamp(value).strftime("%I:%M %p") if value else None
                print(f"{key}: {value} ({readable_time})")
            else:
                print(f"{key}: {value}")
    else:
        print(f"Test image not found at {image_file}")
