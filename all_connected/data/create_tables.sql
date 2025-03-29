DROP DATABASE IF EXISTS `snapngo_db`;
CREATE DATABASE `snapngo_db`;

USE `snapngo_db`;

DROP TABLE IF EXISTS `assignments`;
DROP TABLE IF EXISTS `users`;
DROP TABLE IF EXISTS `tasks`;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(50), -- randomly generated
    `name` VARCHAR(50),
    email VARCHAR(50),
    compensation DECIMAL(4,2) DEFAULT 0,
    -- reliability DECIMAL(4,2) DEFAULT 0.5,
    -- `status` ENUM('active', 'inactive') DEFAULT 'active', TODO: want to use?
    -- PRIMARY KEY (id)
)
ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS order (
    order_id INT(5) NOT NULL, -- generated incrememntally with each new order
    user_id VARCHAR(50),
    `restaurant location` VARCHAR(100), -- TODO: see if I can extract from image
    `app used` VARCHAR(500),
    task_creation_time DATETIME,
    submission_time DATETIME,
    `image_path` VARCHAR(255); -- Stores the file path or URL of the image
    order_placement_time DATETIME,
    earliest_estimated_order_arrival DATETIME,
    latest_estimated_order_arrival DATETIME,
    order_completion_time DATETIME,
    -- TODO: add verification criteria later
    PRIMARY KEY (id)
)
ENGINE = InnoDB;
