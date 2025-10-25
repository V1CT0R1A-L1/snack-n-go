"""
Author: Victoria Li
Date: 10/14/2025
Description: OCR pattern matching for different delivery apps and information types
"""

import re
from datetime import datetime

class OCRPatternMatcher:
    def __init__(self):
        self.app_identifiers = {
            'uber': ['uber', 'ubereats', 'uber eats'],
            'doordash': ['doordash', 'door dash'],
            'grubhub': ['grubhub', 'grub hub']
        }
        self.restaurant_patterns = [
            r'(?:^|\n)([A-Z][A-Za-z0-9\s&\.\']{2,30})(?:\n|$)',
            r'(?:restaurant|from|at|@)\s*([A-Z][A-Za-z0-9\s&\.\']{2,30})',
            r'(?:your order from|order from)\s*([A-Z][A-Za-z0-9\s&\.\']{2,30})'
        ]
       
        self.time_patterns = {
            'order_time': [
                r'(?:ordered|placed|ordered at|at)\s*(\d{1,2}:\d{2}\s*[AP]M)',
                r'(?:order time|ordered)\s*(\d{1,2}:\d{2})',
                r'(\d{1,2}:\d{2}\s*[AP]M)\s*(?:ordered|placed)'
            ],
            'delivery_estimate': [
                r'(?:arrive by|deliver by|est\. delivery|estimated)\s*(\d{1,2}:\d{2}\s*[AP]M)',
                r'(?:estimated|delivery)\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})',
                r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*(?:PM|AM)'
            ],
            'completion_time': [
                r'(?:delivered|completed|arrived at)\s*(\d{1,2}:\d{2}\s*[AP]M)',
                r'(?:delivered at)\s*(\d{1,2}:\d{2})'
            ]
        }
       
        self.address_patterns = [
            r'(\d+\s+[A-Za-z0-9\s,\.]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr))',
            r'(?:deliver to|address)\s*([A-Za-z0-9\s,\.\-]+)'
        ]

    def identify_app(self, text):
        text_lower = text.lower()
        for app, keywords in self.app_identifiers.items():
            if any(keyword in text_lower for keyword in keywords):
                return app
        return None

    def extract_restaurant_name(self, text):
        for pattern in self.restaurant_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                return max(matches, key=len).strip()
        return None

    def extract_times(self, text):
        times = {}
       
        order_time = self._extract_specific_time(text, 'order_time')
        if order_time:
            times['order_placement_time'] = [order_time]
       
        delivery_times = self._extract_delivery_estimate(text)
        if delivery_times:
            if len(delivery_times) >= 1:
                times['earliest_estimated_arrival_time'] = [delivery_times[0]]
            if len(delivery_times) >= 2:
                times['latest_estimated_arrival_time'] = [delivery_times[1]]
       
        completion_time = self._extract_specific_time(text, 'completion_time')
        if completion_time:
            times['order_completion_time'] = [completion_time]
           
        return times

    def _extract_specific_time(self, text, time_type):
        patterns = self.time_patterns.get(time_type, [])
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                time_str = matches[0] if isinstance(matches[0], str) else matches[0][0]
                return self._parse_time_to_unix(time_str)
        return None

    def _extract_delivery_estimate(self, text):
        delivery_times = []
       
        for pattern in self.time_patterns['delivery_estimate']:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                for match in matches:
                    if isinstance(match, tuple):
                        for time_str in match:
                            if time_str:
                                timestamp = self._parse_time_to_unix(time_str)
                                if timestamp:
                                    delivery_times.append(timestamp)
                    else:
                        timestamp = self._parse_time_to_unix(match)
                        if timestamp:
                            delivery_times.append(timestamp)
       
        return sorted(delivery_times)

    def _parse_time_to_unix(self, time_str):
        try:
            time_str = time_str.strip().upper()
           
            if 'PM' in time_str:
                time_part = time_str.replace('PM', '').strip()
                if ':' in time_part:
                    hours, minutes = map(int, time_part.split(':'))
                    if hours < 12:
                        hours += 12
                else:
                    hours = int(time_part)
                    minutes = 0
                    if hours < 12:
                        hours += 12
            elif 'AM' in time_str:
                time_part = time_str.replace('AM', '').strip()
                if ':' in time_part:
                    hours, minutes = map(int, time_part.split(':'))
                    if hours == 12:
                        hours = 0
                else:
                    hours = int(time_part)
                    minutes = 0
                    if hours == 12:
                        hours = 0
            else:
                if ':' in time_str:
                    hours, minutes = map(int, time_str.split(':'))
                else:
                    return None
           
            now = datetime.now()
            dt = datetime(now.year, now.month, now.day, hours, minutes)
            return int(dt.timestamp())
           
        except Exception as e:
            print(f"Time parsing error: {e}")
            return None

    def extract_address(self, text):
        for pattern in self.address_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                return matches[0].strip()
        return None

    def parse_ocr_text_structured(self, text):
        result = {}
       
        app = self.identify_app(text)
        if app:
            result['app_detected'] = app
       
        restaurant = self.extract_restaurant_name(text)
        if restaurant:
            result['restaurant_name'] = restaurant
       
        times = self.extract_times(text)
        result.update(times)
       
        address = self.extract_address(text)
        if address:
            result['restaurant_address'] = address
           
        return result

ocr_matcher = OCRPatternMatcher()