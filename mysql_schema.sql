-- MySQL schema for user and report storage
-- Passwords: hash in the application layer (bcrypt/argon2) and store the hash only.

CREATE DATABASE IF NOT EXISTS user_service
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;
USE user_service;

-- Users
CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'user primary key',
  name VARCHAR(64) NOT NULL,
  age TINYINT UNSIGNED CHECK (age <= 150),
  sex TINYINT(1) NOT NULL CHECK (sex IN (0, 1)),
  password_hash CHAR(60) NOT NULL COMMENT 'bcrypt/argon2 hash; never store plaintext',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_users_name (name)
) ENGINE=InnoDB;

-- User reports
CREATE TABLE IF NOT EXISTS user_reports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'record primary key',
  user_id BIGINT UNSIGNED NOT NULL COMMENT 'references users.id',
  rid CHAR(36) NOT NULL COMMENT 'report ID (UUID recommended)',
  report_path VARCHAR(255) NOT NULL COMMENT 'path or URL to the PDF',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_reports_rid (rid),
  KEY idx_user_reports_user_id (user_id),
  CONSTRAINT fk_user_reports_user FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;

-- Masked view for safer reads (hides password hash)
CREATE OR REPLACE VIEW users_safe AS
SELECT id, name, age, sex, '***' AS password_masked, created_at, updated_at
FROM users;

-- Example inserts (remove if not needed)
-- INSERT INTO users (name, age, sex, password_hash)
-- VALUES ('alice', 28, 1, '$2b$12$examplehashexamplehashexamplehashabcd');
--
-- INSERT INTO user_reports (user_id, rid, report_path)
-- VALUES (1, '550e8400-e29b-41d4-a716-446655440000', '/reports/550e8400-e29b-41d4-a716-446655440000.pdf');
