/*
Author: Victoria Li, based on work from Amy Fung & Cynthia Wang & Sofia Kobayashi & Helen Mao & Amelia Zhang
Date: 10/13/2025
Description: Updated to store all timestamps as Unix timestamps (integers)
*/

DROP DATABASE IF EXISTS `snackngo_db`;
CREATE DATABASE `snackngo_db`;

USE `snackngo_db`;

DROP TABLE IF EXISTS `users`;
DROP TABLE IF EXISTS `orders`;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(50) PRIMARY KEY,
    username VARCHAR(50),
    email VARCHAR(50),
    status ENUM('active', 'inactive') DEFAULT 'active'
)
ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS orders (
    -- Order info
    order_id INT AUTO_INCREMENT,
    user_id VARCHAR(50),
    start_submission_timestamp INT,
    app_used VARCHAR(20),
    status ENUM(
        'awaiting_app_selection',
        'awaiting_initial_screenshot',
        'verifying_initial_data',
        'awaiting_completion_screenshot',
        'verifying_completion_data',
        'collecting_missing_info', 
        'completed', 
        'rejected'
    ) DEFAULT 'awaiting_app_selection',

    -- Restaurant info
    restaurant_name VARCHAR(100),
    is_restaurant_name_verified BOOLEAN DEFAULT FALSE,
    restaurant_address VARCHAR(100), 
    is_restaurant_address_verified BOOLEAN DEFAULT FALSE, 

    -- Time in Unix timestamps
    order_placement_time INT,
    is_order_placement_time_verified BOOLEAN DEFAULT FALSE,
    earliest_estimated_arrival_time INT,
    is_earliest_estimated_arrival_time_verified BOOLEAN DEFAULT FALSE,
    latest_estimated_arrival_time INT,
    is_latest_estimated_arrival_time_verified BOOLEAN DEFAULT FALSE,
    order_completion_time INT,
    is_order_completion_time_verified BOOLEAN DEFAULT FALSE,

    -- Screenshot paths
    placement_screenshot_path VARCHAR(300),
    completion_screenshot_path VARCHAR(300),

    -- Auto-verification
    auto_verified_fields JSON,
    
    PRIMARY KEY (order_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL
)
ENGINE = InnoDB;