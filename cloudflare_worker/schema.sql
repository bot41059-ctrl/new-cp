-- =========================================================================
--  Cloudflare D1 Database Schema for OmniStream Authentication & Licensing
-- =========================================================================

-- 1. Existing Users Table (YouTube OAuth Tokens & Secrets)
CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY,
  client_id TEXT,
  client_secret TEXT,
  access_token TEXT,
  refresh_token TEXT,
  token_expiry INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Licenses / Access Keys Table (Feature Locks & Time Expiry)
CREATE TABLE IF NOT EXISTS licenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  license_key TEXT UNIQUE NOT NULL,       -- e.g. "OMNI-ABCD-1234-EF56"
  user_name TEXT,                         -- Assigned username
  device_id TEXT,                         -- Unique hardware/browser device fingerprint
  max_devices INTEGER DEFAULT 1,          -- Number of simultaneous devices allowed
  status TEXT DEFAULT 'active',           -- 'active', 'revoked', 'expired'
  duration_type TEXT DEFAULT '24h',       -- '1h', '2h', '10h', '1d', '7d', '30d', 'lifetime', etc.
  duration_seconds INTEGER DEFAULT 86400, -- Duration in seconds (-1 for lifetime)
  activated_at INTEGER DEFAULT 0,         -- Unix timestamp ms when activated on device
  expires_at INTEGER DEFAULT 0,           -- Unix timestamp ms when license expires
  created_at INTEGER NOT NULL,            -- Unix timestamp ms when generated
  created_by TEXT DEFAULT 'admin',        -- 'admin'
  
  -- Granular Feature Locks (1 = Allowed, 0 = Locked)
  allow_single INTEGER DEFAULT 1,         -- Single Video Download
  allow_batch INTEGER DEFAULT 1,          -- Batch / Multi-Link Download
  allow_cp_support INTEGER DEFAULT 1,     -- ClassPlus, Akamai & DRM Decryption
  allow_yt_upload INTEGER DEFAULT 1,      -- YouTube Auto-Upload
  allow_tg_upload INTEGER DEFAULT 1,      -- Telegram Auto-Upload
  allow_disk_clean INTEGER DEFAULT 0,     -- Storage / Disk Space Clean
  
  notes TEXT                              -- Custom remarks / contact info
);

CREATE INDEX IF NOT EXISTS idx_licenses_key ON licenses(license_key);
CREATE INDEX IF NOT EXISTS idx_licenses_device ON licenses(device_id);

-- 3. Key Requests Table (Requests submitted by users from website)
CREATE TABLE IF NOT EXISTS key_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_name TEXT NOT NULL,
  device_id TEXT NOT NULL,
  requested_features TEXT,                -- comma separated features requested
  duration_requested TEXT DEFAULT '24h',
  status TEXT DEFAULT 'pending',          -- 'pending', 'approved', 'rejected'
  assigned_key TEXT,
  created_at INTEGER NOT NULL,
  ip_address TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_status ON key_requests(status);

-- 4. Device Activation Logs (Audit Trail)
CREATE TABLE IF NOT EXISTS activation_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  license_key TEXT NOT NULL,
  device_id TEXT NOT NULL,
  user_name TEXT,
  action TEXT,                            -- 'activate', 'verify', 'rejected'
  ip TEXT,
  user_agent TEXT,
  timestamp INTEGER NOT NULL
);
