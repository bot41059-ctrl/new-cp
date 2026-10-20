# 🚀 OmniStream Cloudflare Worker & D1 Setup Guide

This folder contains the complete, production-ready Cloudflare Worker and D1 Database Schema for **OmniStream's Device ID Licensing, Feature Locking, and Google YouTube OAuth**.

---

## 📁 Files in this Directory

- `worker.js`: Cloudflare Worker code (includes Google OAuth, License Activation, Device Binding, Feature Control, and the `/admin` Dashboard).
- `schema.sql`: SQL database schema for Cloudflare D1.

---

## 🛠️ Step 1: Execute SQL in Cloudflare D1

1. Log into your [Cloudflare Dashboard](https://dash.cloudflare.com/).
2. Go to **Workers & Pages** ➔ **D1 SQL Database**.
3. Select your database (e.g. `omnistream-db` or your existing D1 database).
4. Click on the **Console** tab.
5. Copy and paste the contents of `schema.sql` and click **Execute**.

*(This creates the `users`, `licenses`, `key_requests`, and `activation_logs` tables without affecting existing data).*

---

## ⚡ Step 2: Deploy `worker.js` to Cloudflare Worker

1. In Cloudflare Dashboard, go to your existing Worker (e.g., `yt-uplode`).
2. Click **Quick Edit** (or deploy via Wrangler).
3. Replace the entire worker code with the contents of `worker.js`.
4. Click **Save and Deploy**.

### Environment Variables / Bindings:
Make sure your Worker settings have:
- **D1 Database Binding**: Variable name `DB` mapped to your D1 database.
- **Environment Variable (Optional)**:
  - `ADMIN_PASSWORD`: Your secret admin password (defaults to `admin123` if not set).
  - `FRONTEND_URL`: URL of your OmniStream website.

---

## 👑 Accessing the Admin Dashboard

Visit:
`https://yt-uplode.mypresonal65.workers.dev/admin`

Enter your admin password (e.g. `admin123`) to open the control panel where you can:
- Generate keys with duration: 1 hour, 2 hours, 10 hours, 1 day, 7 days, 30 days, or Lifetime.
- Toggle permissions for: Single Video, Batch Download, Classplus / DRM Support, YouTube Upload, Telegram Upload, and Storage Clean.
- View bound device IDs, remaining validity countdown, and revoke/delete keys with 1 click.
