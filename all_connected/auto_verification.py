"""
Author: Victoria Li
Date: 10/11/2025
Description: Automatic verification module combining Gemini and OCR for reduced user workload
"""

import os
import re
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import pytesseract
from PIL import Image
import cv2
import numpy as np
from gemini import gemini_process_image
from ocr_patterns import ocr_matcher

class AutoVerification:
    def __init__(self, confidence_threshold = 0.8, time_tolerance_minutes = 5):
        self.confidence_threshold = confidence_threshold
        self.time_tolerance_seconds = time_tolerance_minutes * 60
       
        self.validation_rules = {
            'restaurant_name': {
                'min_length': 2,
                'max_length': 100,
                'allowed_chars': r'[a-zA-Z0-9\s\-\'\&]'
            },
            'order_placement_time': {
                'min_timestamp': 1609459200,  # 2021-01-01
                'max_timestamp': 1893456000   # 2030-01-01
            },
            'order_completion_time': {
                'min_timestamp': 1609459200,
                'max_timestamp': 1893456000
            }
        }

    def extract_with_ocr(self, image_path: str) -> Dict:
        """
        Extract information using traditional OCR (Tesseract)
        """
        try:
            image = self.preprocess_image(image_path)
            ocr_text = pytesseract.image_to_string(image)
            print(f"[OCR RAW TEXT]\n{ocr_text}\n[END OCR TEXT]")
            extracted_data = ocr_matcher.parse_ocr_text_structured(ocr_text)
           
            return {
                'success': True,
                'data': extracted_data,
                'raw_text': ocr_text,
                'app_detected': extracted_data.get('app_detected')
            }
           
        except Exception as e:
            print(f"OCR extraction error: {e}")
            return {
                'success': False,
                'error': str(e),
                'data': {}
            }

    def preprocess_image(self, image_path):
        img = cv2.imread(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        denoised = cv2.medianBlur(gray, 5)
        _, thresh = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
       
        return Image.fromarray(thresh)

    def parse_ocr_text(self, text):
        """
        Parse OCR text to extract structured information
        """
        extracted = {}
       
        # Restaurant name patterns
        restaurant_patterns = [
            r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*(?:Restaurant|Cafe|Grill|Bar|Diner))'
        ]
       
        for pattern in restaurant_patterns:
            matches = re.findall(pattern, text)
            if matches:
                extracted['restaurant_name'] = matches[0].strip()
                break
       
        # Time patterns
        time_patterns = [
            r'(\d{1,2}:\d{2}\s*[AP]M)',
            r'(\d{1,2}:\d{2})',
            r'(\d{1,2}\s*[AP]M)'
        ]
       
        times_found = []
        for pattern in time_patterns:
            times_found.extend(re.findall(pattern, text, re.IGNORECASE))
       
        # First time is order time, subsequent times are delivery estimates
        if len(times_found) >= 1:
            extracted['order_placement_time'] = self.parse_time_to_unix(times_found[0])
       
        if len(times_found) >= 2:
            extracted['earliest_estimated_arrival_time'] = self.parse_time_to_unix(times_found[1])
       
        if len(times_found) >= 3:
            extracted['latest_estimated_arrival_time'] = self.parse_time_to_unix(times_found[2])
       
        return extracted

    def parse_time_to_unix(self, time_str: str) -> Optional[int]:
        """
        Convert time string to Unix timestamp
        """
        try:
            # Clean the time string
            time_str = time_str.strip().upper()
           
            # Handle AM/PM
            if 'PM' in time_str and ':' in time_str:
                time_part = time_str.replace('PM', '').strip()
                hours, minutes = map(int, time_part.split(':'))
                if hours < 12:
                    hours += 12
            elif 'AM' in time_str and ':' in time_str:
                time_part = time_str.replace('AM', '').strip()
                hours, minutes = map(int, time_part.split(':'))
                if hours == 12:
                    hours = 0
            elif ':' in time_str:
                hours, minutes = map(int, time_str.split(':'))
            else:
                return None
           
            # Use current date but keep time
            now = datetime.now()
            dt = datetime(now.year, now.month, now.day, hours, minutes)
            return int(dt.timestamp())
           
        except Exception:
            return None

    def compare_extractions(self, gemini_data: Dict, ocr_data: Dict) -> Dict:
        """
        Compare Gemini and OCR extractions and calculate confidence scores
        """
        comparison_results = {}
        fields_to_compare = [
            'restaurant_name', 'order_placement_time', 'earliest_estimated_arrival_time',
            'latest_estimated_arrival_time', 'order_completion_time', 'restaurant_address'
        ]
       
        for field in fields_to_compare:
            gemini_value = gemini_data.get(field)
            ocr_value = ocr_data.get(field)
           
            confidence = self.calculate_confidence(field, gemini_value, ocr_value)
           
            comparison_results[field] = {
                'gemini_value': gemini_value,
                'ocr_value': ocr_value,
                'confidence': confidence,
                'consistent': confidence >= self.confidence_threshold,
                'final_value': self.determine_final_value(field, gemini_value, ocr_value, confidence)
            }
       
        return comparison_results

    def calculate_confidence(self, field: str, gemini_value, ocr_value) -> float:
        """
        Calculate confidence score for field agreement
        """
        # If both are None, low confidence
        if gemini_value is None and ocr_value is None:
            return 0.0
       
        # If one is None, use the other but with reduced confidence
        if gemini_value is None or ocr_value is None:
            return 0.5
       
        # Exact match for strings
        if isinstance(gemini_value, str) and isinstance(ocr_value, str):
            if gemini_value.lower() == ocr_value.lower():
                return 1.0
            else:
                # Calculate string similarity
                return self.string_similarity(gemini_value, ocr_value)
       
        # Numeric comparison (for timestamps)
        if isinstance(gemini_value, (int, float)) and isinstance(ocr_value, (int, float)):
            time_diff = abs(gemini_value - ocr_value)
            if time_diff <= self.time_tolerance_seconds:
                return 1.0
            else:
                # Reduce confidence based on time difference
                return max(0.0, 1.0 - (time_diff / (self.time_tolerance_seconds * 2)))
       
        return 0.3  # Default low confidence for type mismatches

    def string_similarity(self, str1: str, str2: str) -> float:
        """
        Calculate simple string similarity (0.0 to 1.0)
        """
        str1 = str1.lower().strip()
        str2 = str2.lower().strip()
       
        if str1 == str2:
            return 1.0
       
        # Token-based similarity
        tokens1 = set(str1.split())
        tokens2 = set(str2.split())
       
        if not tokens1 or not tokens2:
            return 0.0
       
        intersection = tokens1.intersection(tokens2)
        union = tokens1.union(tokens2)
       
        return len(intersection) / len(union)

    def determine_final_value(self, field: str, gemini_value, ocr_value, confidence: float):
        """
        Determine which value to use based on confidence and field type
        """
        if confidence >= self.confidence_threshold:
            # Prefer Gemini for complex fields, OCR for simple ones
            if field in ['restaurant_name', 'restaurant_address']:
                return gemini_value if gemini_value else ocr_value
            else:
                return gemini_value if gemini_value else ocr_value
        else:
            # Low confidence - prefer Gemini but might need manual verification
            return gemini_value

    def validate_field(self, field: str, value) -> bool:
        """
        Validate if a field value meets basic requirements
        """
        if value is None:
            return False
       
        rules = self.validation_rules.get(field, {})
       
        if field.endswith('_name') or field == 'restaurant_address':
            if not isinstance(value, str):
                return False
            if len(value) < rules.get('min_length', 1):
                return False
            if len(value) > rules.get('max_length', 200):
                return False
       
        elif field.endswith('_time'):
            if not isinstance(value, (int, float)):
                return False
            if value < rules.get('min_timestamp', 0):
                return False
            if value > rules.get('max_timestamp', 2000000000):
                return False
       
        return True

    def auto_verify_screenshot(self, image_path, image_stage):
        """
        Main function to automatically verify a screenshot
        Returns verification results and auto-verified fields
        """
        print(f"[AUTO_VERIFICATION] Processing {image_stage} screenshot: {image_path}")
       
        try:
            # Step 1: Extract with Gemini
            print(f"[AUTO_VERIFICATION] Step 1: Gemini extraction...")
            gemini_data = gemini_process_image(image_path, image_stage)
            print(f"[AUTO_VERIFICATION] Gemini extraction completed: {gemini_data}")
        
            print(f"[AUTO_VERIFICATION] Step 2: OCR extraction...")
            ocr_result = self.extract_with_ocr(image_path)
            print(f"[AUTO_VERIFICATION] OCR extraction completed. Success: {ocr_result['success']}")

            comparison = {}
            auto_verified = []
            final_values = {}

            # Step 3: Compare results
            if ocr_result['success']:
                print(f"[AUTO_VERIFICATION] Step 3: Comparing Gemini and OCR results...")
                comparison = self.compare_extractions(
                    gemini_data if gemini_data else {},
                    ocr_result.get('data', {})
                )
                print(f"[AUTO_VERIFICATION] Comparison completed: {comparison}")
            
                print(f"[AUTO_VERIFICATION] Step 4: Determining auto-verified fields...")
                for field, result in comparison.items():
                    print(f"[AUTO_VERIFICATION] Checking field {field}: consistent={result['consistent']}, final_value={result['final_value']}")
                    if (result['consistent'] and
                        result['final_value'] is not None and
                        self.validate_field(field, result['final_value'])):
                        auto_verified.append(field)
                        final_values[field] = result['final_value']
                        print(f"[AUTO_VERIFICATION] Field {field} AUTO-VERIFIED")
                    else:
                        print(f"[AUTO_VERIFICATION] Field {field} needs manual verification")
                    
                print(f"[AUTO_VERIFICATION] Auto-verified fields: {auto_verified}")
            else:
                print(f"[AUTO_VERIFICATION] OCR failed, using Gemini data only (no auto-verification)")
                final_values = {}
        
            result = {
                'success': True,
                'auto_verified_fields': auto_verified,
                'final_values': final_values,
                'comparison_details': comparison,
                'gemini_data': gemini_data,
                'ocr_data': ocr_result.get('data', {}) if ocr_result['success'] else {},
                'ocr_app_detected': ocr_result.get('app_detected'),
                'ocr_success': ocr_result['success']
            }
            print(f"[AUTO_VERIFICATION] Final result: {result}")
            return result
        
        except Exception as e:
            error_msg = f"Auto-verification error: {str(e)}"
            print(f"[AUTO_VERIFICATION] {error_msg}")
            import traceback
            print(f"[AUTO_VERIFICATION] Traceback: {traceback.format_exc()}")
            return {
                'success': False,
                'error': error_msg,
                'auto_verified_fields': [],
                'final_values': {},
                'gemini_data': {},
                'ocr_data': {},
                'ocr_success': False
            }

auto_verifier = AutoVerification()

def process_screenshot_with_auto_verify(image_path, image_stage, order_id, db_operation_func):
    print(f"[AUTO_VERIFICATION] Starting for {image_stage} screenshot: {image_path}")
    verification_result = auto_verifier.auto_verify_screenshot(image_path, image_stage)
   
    if verification_result['success']:
        if image_stage == 'placement':
            next_status = 'verifying_initial_data'
            screenshot_field = 'placement_screenshot_path'
            expected_fields = ['restaurant_name', 'order_placement_time',
                              'earliest_estimated_arrival_time', 'latest_estimated_arrival_time']
        else:  # completion
            next_status = 'verifying_completion_data'
            screenshot_field = 'completion_screenshot_path'
            expected_fields = ['order_completion_time']
        
        updates = {
            'status': next_status,
            'auto_verified_fields': json.dumps(verification_result['auto_verified_fields']),
            screenshot_field: image_path
        }
        print(f"[AUTO_VERIFICATION] Setting status to: {next_status}")
        print(f"[AUTO_VERIFICATION] Setting screenshot field: {screenshot_field} = {image_path}")
       
       
        gemini_data = verification_result.get('gemini_data', {})
        ocr_data = verification_result.get('ocr_data', {})
       
        updates.update({
            'gemini_restaurant_name': gemini_data.get('restaurant_name'),
            'gemini_order_placement_time': gemini_data.get('order_placement_time'),
            'gemini_earliest_estimated_arrival_time': gemini_data.get('earliest_estimated_arrival_time'),
            'gemini_latest_estimated_arrival_time': gemini_data.get('latest_estimated_arrival_time'),
            'gemini_order_completion_time': gemini_data.get('order_completion_time'),
            'gemini_restaurant_address': gemini_data.get('restaurant_address'),
           
            'ocr_restaurant_name': ocr_data.get('restaurant_name'),
            'ocr_order_placement_time': ocr_data.get('order_placement_time'),
            'ocr_earliest_estimated_arrival_time': ocr_data.get('earliest_estimated_arrival_time'),
            'ocr_latest_estimated_arrival_time': ocr_data.get('latest_estimated_arrival_time'),
            'ocr_order_completion_time': ocr_data.get('order_completion_time'),
            'ocr_restaurant_address': ocr_data.get('restaurant_address')
        })
       
        auto_verified = verification_result['auto_verified_fields']
        final_values = verification_result['final_values']
       
        for field in auto_verified:
            if field in expected_fields or field == 'restaurant_address':
                updates[field] = final_values[field]
                updates[f'is_{field}_verified'] = True
                print(f"[AUTO_VERIFICATION] Auto-verified field: {field} = {final_values[field]}")
       
        for field in expected_fields:
            if field not in auto_verified and field not in updates:
                gemini_value = gemini_data.get(field)
                if gemini_value is not None:
                    updates[field] = gemini_value
                    print(f"[AUTO_VERIFICATION] Set Gemini value for {field}: {gemini_value}")

        if 'restaurant_address' not in auto_verified and 'restaurant_address' not in updates:
            address_value = gemini_data.get('restaurant_address')
            if address_value is not None:
                updates['restaurant_address'] = address_value
                print(f"[AUTO_VERIFICATION] Set Gemini value for restaurant_address: {address_value}")
       
        auto_verified_in_stage = [f for f in auto_verified if f in expected_fields]
        needs_manual_verification = len(auto_verified_in_stage) < len(expected_fields)
       
        print(f"[AUTO_VERIFICATION] Stage: {image_stage}, Expected: {expected_fields}")
        print(f"[AUTO_VERIFICATION] Auto-verified in stage: {auto_verified_in_stage}")
        print(f"[AUTO_VERIFICATION] Needs manual: {needs_manual_verification}")
       
        return {
            'updates': updates,
            'needs_manual_verification': needs_manual_verification,
            'auto_verified_count': len(auto_verified_in_stage),
            'total_expected_fields': len(expected_fields)
        }
   
   
    else:
        print(f"[AUTO_VERIFICATION] Auto-verification failed, falling back to manual processing")
       
        try:
            gemini_data = gemini_process_image(image_path, image_stage)
        except Exception as e:
            gemini_data = {}
            print(f"[AUTO_VERIFICATION] Gemini fallback also failed: {e}")
       
        if image_stage == 'placement':
            next_status = 'verifying_initial_data'
            screenshot_field = 'placement_screenshot_path'
            relevant_fields = ['restaurant_name', 'order_placement_time',
                              'earliest_estimated_arrival_time', 'latest_estimated_arrival_time',
                              'restaurant_address']
            total_expected_fields = 4
        else:  # completion
            next_status = 'verifying_completion_data'
            screenshot_field = 'completion_screenshot_path'
            relevant_fields = ['order_completion_time']
            otal_expected_fields = 1
       
        updates = {
            'status': next_status,
            'auto_verified_fields': json.dumps([]),
            screenshot_field: image_path
        }

        print(f"[AUTO_VERIFICATION] Fallback - Setting status to: {next_status}")
        print(f"[AUTO_VERIFICATION] Fallback - Setting screenshot field: {screenshot_field}")

        for field in relevant_fields:
            gemini_value = gemini_data.get(field)
            if gemini_value is not None:
                updates[field] = gemini_value
                print(f"[AUTO_VERIFICATION] Set fallback Gemini value for {field}: {gemini_value}")
        
        updates.update({
            'gemini_restaurant_name': gemini_data.get('restaurant_name'),
            'gemini_order_placement_time': gemini_data.get('order_placement_time'),
            'gemini_earliest_estimated_arrival_time': gemini_data.get('earliest_estimated_arrival_time'),
            'gemini_latest_estimated_arrival_time': gemini_data.get('latest_estimated_arrival_time'),
            'gemini_order_completion_time': gemini_data.get('order_completion_time'),
            'gemini_restaurant_address': gemini_data.get('restaurant_address')
        })
       
        return {
            'updates': updates,
            'needs_manual_verification': True,
            'auto_verified_count': 0,
            'total_expected_fields': total_expected_fields
        }