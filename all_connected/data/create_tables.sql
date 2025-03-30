/*
Author: Amelia Zhang, based on work from Amy Fung & Cynthia Wang & Sofia Kobayashi & Helen Mao
Date: 03/28/2025
Description: 
*/

DROP DATABASE IF EXISTS `snapngo_db`;
CREATE DATABASE `snapngo_db`;

USE `snapngo_db`;

DROP TABLE IF EXISTS `users`;
DROP TABLE IF EXISTS `orders`;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(50), -- randomly generated
    username VARCHAR(50),
    email VARCHAR(50),
    total_compensation DECIMAL(4,2) DEFAULT 0, 
    status ENUM('active', 'inactive') DEFAULT 'active', 
    PRIMARY KEY (id)
)
ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS orders (
    -- Order info
    order_id INT AUTO_INCREMENT,
    user_id VARCHAR(50),
    channel_id VARCHAR(50), -- current timestamp one order is associated with one channel
    compensation DECIMAL(5,2) DEFAULT 0.00, 
    app_used VARCHAR(20),
    channel_creation_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    channel_completion_time DATETIME,
    status ENUM(
        'awaiting_placement_time',
        'awaiting_arrival_time',
        'awaiting_missing_info',
        'completed',
        'rejected'
    ) DEFAULT 'awaiting_placement_time',

    -- Restaurant info
    restaurant_name VARCHAR(100), -- TODO: see if I can extract from image
    is_restaurant_name_verified BOOLEAN DEFAULT FALSE,
    restaurant_address VARCHAR(100), 
    is_restaurant_address_verified BOOLEAN DEFAULT FALSE, 

    -- Time
    order_placement_time DATETIME,
    is_order_placement_time_verified BOOLEAN DEFAULT FALSE,
    earliest_estimated_arrival_time DATETIME,
    latest_estimated_arrival_time DATETIME,
    is_estimated_arrival_time_verified BOOLEAN DEFAULT FALSE,
    order_completion_time DATETIME,
    is_order_completion_time_verified BOOLEAN DEFAULT FALSE,

    -- Screenshot paths
    restaurant_screenshot_path VARCHAR(300),
    placement_screenshot_path VARCHAR(300),
    arrival_screenshot_path VARCHAR(300),
    completion_screenshot_path VARCHAR(300),

    -- TODO: add survey response later

    PRIMARY KEY (order_id),
    UNIQUE KEY (channel_id), 
    FOREIGN KEY (user_id) REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL
)
ENGINE = InnoDB;
