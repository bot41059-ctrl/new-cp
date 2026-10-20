/**
 * =========================================================================
 *  OmniStream Cloudflare Worker: Auth, Google OAuth, & Advanced Licensing API
 * =========================================================================
 * 
 * Features:
 *  1. Google OAuth & YouTube Secrets Storage (Preserved 100%)
 *  2. Hardware/Browser Device-ID Bound Licensing & Key Activation
 *  3. Granular Feature Locks (Single, Batch, ClassPlus/DRM, YT, TG, Disk Clean)
 *  4. Flexible Expiration (1h to 10h, 1d to 365d, or Lifetime)
 *  5. Embedded High-Tech Admin Dashboard at /admin
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const origin = request.headers.get("Origin") || "*";

    // CORS Headers for API calls from OmniStream website
    const corsHeaders = {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS, DELETE, PUT",
      "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Admin-Password, X-Device-Id, X-License-Key",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      // =============================================================
      //  🔥 ADMIN WEB DASHBOARD UI (Embedded at /admin)
      // =============================================================
      if (path === "/" || path === "/admin" || path === "/admin/") {
        return new Response(renderAdminHTML(), {
          headers: { "Content-Type": "text/html; charset=utf-8" }
        });
      }

      // =============================================================
      //  🔐 ADMIN API ENDPOINTS (Protected with ADMIN_PASSWORD)
      // =============================================================
      if (path.startsWith("/api/admin/")) {
        const adminPass = env.ADMIN_PASSWORD || "admin123";

        // Admin Login / Verification
        if (path === "/api/admin/login" && request.method === "POST") {
          const body = await request.json();
          if (body.password === adminPass) {
            return jsonResponse({ success: true, message: "Admin authenticated" }, 200, corsHeaders);
          }
          return jsonResponse({ success: false, error: "Invalid Admin Password" }, 401, corsHeaders);
        }

        // Verify admin header on all subsequent admin endpoints
        const authHeader = request.headers.get("X-Admin-Password") || request.headers.get("Authorization");
        const cleanHeader = (authHeader || "").replace(/^Bearer\s+/i, "");
        if (cleanHeader !== adminPass) {
          return jsonResponse({ error: "Unauthorized admin access" }, 401, corsHeaders);
        }

        // --- List All License Keys ---
        if (path === "/api/admin/keys" && request.method === "GET") {
          const { results } = await env.DB.prepare(`
            SELECT * FROM licenses ORDER BY created_at DESC
          `).all();

          const now = Date.now();
          const enriched = (results || []).map(k => {
            let isExpired = false;
            let remainingText = "Lifetime";
            if (k.duration_seconds !== -1 && k.expires_at > 0) {
              if (now >= k.expires_at) {
                isExpired = true;
                remainingText = "Expired";
              } else {
                const diffSec = Math.floor((k.expires_at - now) / 1000);
                remainingText = formatSecondsToHuman(diffSec);
              }
            } else if (k.activated_at === 0) {
              remainingText = `Not activated yet (${k.duration_type})`;
            }

            return {
              ...k,
              is_expired: isExpired,
              remaining_time: remainingText,
              allow_single: Boolean(k.allow_single),
              allow_batch: Boolean(k.allow_batch),
              allow_cp_support: Boolean(k.allow_cp_support),
              allow_yt_upload: Boolean(k.allow_yt_upload),
              allow_tg_upload: Boolean(k.allow_tg_upload),
              allow_disk_clean: Boolean(k.allow_disk_clean)
            };
          });

          return jsonResponse({ success: true, keys: enriched }, 200, corsHeaders);
        }

        // --- Create New License Key ---
        if (path === "/api/admin/keys/create" && request.method === "POST") {
          const body = await request.json();
          const {
            user_name,
            device_id,
            duration_type,       // e.g. '1h', '2h', '10h', '1d', '7d', '30d', 'lifetime'
            custom_hours,
            allow_single = 1,
            allow_batch = 1,
            allow_cp_support = 1,
            allow_yt_upload = 1,
            allow_tg_upload = 1,
            allow_disk_clean = 0,
            notes = ""
          } = body;

          // Compute duration in seconds
          const durationSeconds = parseDurationToSeconds(duration_type, custom_hours);
          const key = generateLicenseKey();
          const now = Date.now();

          await env.DB.prepare(`
            INSERT INTO licenses (
              license_key, user_name, device_id, status, duration_type,
              duration_seconds, activated_at, expires_at, created_at,
              allow_single, allow_batch, allow_cp_support, allow_yt_upload,
              allow_tg_upload, allow_disk_clean, notes
            ) VALUES (?, ?, ?, 'active', ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)
          `).bind(
            key,
            user_name || "Guest User",
            device_id ? device_id.trim() : null,
            duration_type || "24h",
            durationSeconds,
            now,
            allow_single ? 1 : 0,
            allow_batch ? 1 : 0,
            allow_cp_support ? 1 : 0,
            allow_yt_upload ? 1 : 0,
            allow_tg_upload ? 1 : 0,
            allow_disk_clean ? 1 : 0,
            notes || ""
          ).run();

          return jsonResponse({
            success: true,
            message: "License key generated successfully",
            key: key,
            user_name: user_name || "Guest User",
            duration_type: duration_type || "24h"
          }, 200, corsHeaders);
        }

        // --- Revoke / Unrevoke License Key ---
        if (path === "/api/admin/keys/revoke" && request.method === "POST") {
          const { key, status } = await request.json();
          const newStatus = status === 'active' ? 'active' : 'revoked';
          await env.DB.prepare("UPDATE licenses SET status = ? WHERE license_key = ?")
            .bind(newStatus, key).run();
          return jsonResponse({ success: true, message: `Key status set to ${newStatus}` }, 200, corsHeaders);
        }

        // --- Delete License Key ---
        if (path === "/api/admin/keys/delete" && request.method === "POST") {
          const { key } = await request.json();
          await env.DB.prepare("DELETE FROM licenses WHERE license_key = ?").bind(key).run();
          return jsonResponse({ success: true, message: "Key deleted" }, 200, corsHeaders);
        }

        // --- Extend License Expiry ---
        if (path === "/api/admin/keys/extend" && request.method === "POST") {
          const { key, add_hours } = await request.json();
          const hours = parseInt(add_hours || 24, 10);
          const addMs = hours * 3600 * 1000;
          
          const license = await env.DB.prepare("SELECT * FROM licenses WHERE license_key = ?").bind(key).first();
          if (!license) {
            return jsonResponse({ error: "License not found" }, 404, corsHeaders);
          }

          const baseTime = (license.expires_at && license.expires_at > Date.now()) ? license.expires_at : Date.now();
          const newExpiry = baseTime + addMs;

          await env.DB.prepare("UPDATE licenses SET expires_at = ?, status = 'active' WHERE license_key = ?")
            .bind(newExpiry, key).run();

          return jsonResponse({ success: true, new_expiry: newExpiry, message: `Extended by ${hours} hours` }, 200, corsHeaders);
        }

        // --- List Key Requests ---
        if (path === "/api/admin/requests" && request.method === "GET") {
          const { results } = await env.DB.prepare(`
            SELECT * FROM key_requests ORDER BY created_at DESC LIMIT 100
          `).all();
          return jsonResponse({ success: true, requests: results || [] }, 200, corsHeaders);
        }

        // --- Approve Key Request ---
        if (path === "/api/admin/requests/approve" && request.method === "POST") {
          const { request_id, duration_type = "24h" } = await request.json();
          const reqItem = await env.DB.prepare("SELECT * FROM key_requests WHERE id = ?").bind(request_id).first();
          if (!reqItem) {
            return jsonResponse({ error: "Request not found" }, 404, corsHeaders);
          }

          const key = generateLicenseKey();
          const durationSeconds = parseDurationToSeconds(duration_type);
          const now = Date.now();

          // Create license bound to requesting device
          await env.DB.prepare(`
            INSERT INTO licenses (
              license_key, user_name, device_id, status, duration_type,
              duration_seconds, activated_at, expires_at, created_at,
              allow_single, allow_batch, allow_cp_support, allow_yt_upload,
              allow_tg_upload, allow_disk_clean
            ) VALUES (?, ?, ?, 'active', ?, ?, 0, 0, ?, 1, 1, 1, 1, 1, 0)
          `).bind(key, reqItem.user_name, reqItem.device_id, duration_type, durationSeconds, now).run();

          // Mark request approved
          await env.DB.prepare("UPDATE key_requests SET status = 'approved', assigned_key = ? WHERE id = ?")
            .bind(key, request_id).run();

          return jsonResponse({ success: true, key: key, message: "Request approved and key generated" }, 200, corsHeaders);
        }
      }

      // =============================================================
      //  🔑 USER DEVICE & LICENSE VERIFICATION ENDPOINTS
      // =============================================================

      // 1. Activate License on Device
      if (path === "/api/license/activate" && request.method === "POST") {
        const body = await request.json();
        const { key, device_id, user_name } = body;

        if (!key || !device_id) {
          return jsonResponse({ error: "License key and device_id are required" }, 400, corsHeaders);
        }

        const cleanKey = key.trim().toUpperCase();
        const cleanDevice = device_id.trim();

        const lic = await env.DB.prepare("SELECT * FROM licenses WHERE license_key = ?").bind(cleanKey).first();
        if (!lic) {
          return jsonResponse({ valid: false, error: "Invalid License Key. Please check the code." }, 404, corsHeaders);
        }

        if (lic.status === 'revoked') {
          return jsonResponse({ valid: false, error: "This license key has been revoked by admin." }, 403, corsHeaders);
        }

        const now = Date.now();

        // Check if key is expired
        if (lic.duration_seconds !== -1 && lic.expires_at > 0 && now >= lic.expires_at) {
          await env.DB.prepare("UPDATE licenses SET status = 'expired' WHERE id = ?").bind(lic.id).run();
          return jsonResponse({ valid: false, error: "This license key has expired." }, 403, corsHeaders);
        }

        // Device Binding Logic
        if (lic.device_id && lic.device_id !== cleanDevice) {
          return jsonResponse({
            valid: false,
            error: "License key is already locked to another device! Contact admin to reset."
          }, 403, corsHeaders);
        }

        // First Time Activation: Bind device and calculate expiration timestamp
        let expiresAt = lic.expires_at;
        let activatedAt = lic.activated_at;
        let assignedName = lic.user_name || user_name || "OmniUser";

        if (activatedAt === 0) {
          activatedAt = now;
          if (lic.duration_seconds === -1) {
            expiresAt = -1; // Lifetime
          } else {
            expiresAt = now + (lic.duration_seconds * 1000);
          }

          await env.DB.prepare(`
            UPDATE licenses SET 
              device_id = ?, 
              user_name = ?,
              activated_at = ?, 
              expires_at = ?,
              status = 'active'
            WHERE id = ?
          `).bind(cleanDevice, assignedName, activatedAt, expiresAt, lic.id).run();
        }

        // Log Activation
        try {
          await env.DB.prepare(`
            INSERT INTO activation_logs (license_key, device_id, user_name, action, ip, user_agent, timestamp)
            VALUES (?, ?, ?, 'activate', ?, ?, ?)
          `).bind(cleanKey, cleanDevice, assignedName, request.headers.get("CF-Connecting-IP") || "", request.headers.get("User-Agent") || "", now).run();
        } catch (_) {}

        return jsonResponse({
          success: true,
          valid: true,
          licensed: true,
          key: lic.license_key,
          user_name: assignedName,
          device_id: cleanDevice,
          duration_type: lic.duration_type,
          activated_at: activatedAt,
          expires_at: expiresAt,
          permissions: {
            single: lic.allow_single === 1,
            batch: lic.allow_batch === 1,
            cp_support: lic.allow_cp_support === 1,
            yt_upload: lic.allow_yt_upload === 1,
            tg_upload: lic.allow_tg_upload === 1,
            disk_clean: lic.allow_disk_clean === 1
          }
        }, 200, corsHeaders);
      }

      // 2. Verify Active License (Called on page refresh & by Python backend)
      if ((path === "/api/license/verify") && (request.method === "GET" || request.method === "POST")) {
        let key = url.searchParams.get("key");
        let device_id = url.searchParams.get("device_id");

        if (request.method === "POST") {
          try {
            const body = await request.json();
            key = key || body.key;
            device_id = device_id || body.device_id;
          } catch (_) {}
        }

        if (!key) {
          return jsonResponse({ valid: false, error: "License key required" }, 400, corsHeaders);
        }

        const cleanKey = key.trim().toUpperCase();
        const lic = await env.DB.prepare("SELECT * FROM licenses WHERE license_key = ?").bind(cleanKey).first();
        if (!lic) {
          return jsonResponse({ valid: false, error: "License not found" }, 404, corsHeaders);
        }

        if (lic.status === 'revoked') {
          return jsonResponse({ valid: false, error: "License key revoked" }, 403, corsHeaders);
        }

        const now = Date.now();
        if (lic.duration_seconds !== -1 && lic.expires_at > 0 && now >= lic.expires_at) {
          return jsonResponse({ valid: false, error: "License has expired" }, 403, corsHeaders);
        }

        if (device_id && lic.device_id && lic.device_id !== device_id.trim()) {
          return jsonResponse({ valid: false, error: "Device ID mismatch" }, 403, corsHeaders);
        }

        return jsonResponse({
          success: true,
          valid: true,
          licensed: true,
          key: lic.license_key,
          user_name: lic.user_name,
          device_id: lic.device_id,
          expires_at: lic.expires_at,
          duration_type: lic.duration_type,
          permissions: {
            single: lic.allow_single === 1,
            batch: lic.allow_batch === 1,
            cp_support: lic.allow_cp_support === 1,
            yt_upload: lic.allow_yt_upload === 1,
            tg_upload: lic.allow_tg_upload === 1,
            disk_clean: lic.allow_disk_clean === 1
          }
        }, 200, corsHeaders);
      }

      // 3. User Request Key from Website
      if (path === "/api/license/request" && request.method === "POST") {
        const body = await request.json();
        const { user_name, device_id, requested_features, duration } = body;

        if (!user_name || !device_id) {
          return jsonResponse({ error: "Name and Device ID are required" }, 400, corsHeaders);
        }

        const now = Date.now();
        await env.DB.prepare(`
          INSERT INTO key_requests (user_name, device_id, requested_features, duration_requested, status, created_at, ip_address)
          VALUES (?, ?, ?, ?, 'pending', ?, ?)
        `).bind(
          user_name.trim(),
          device_id.trim(),
          requested_features || "All Features",
          duration || "24h",
          now,
          request.headers.get("CF-Connecting-IP") || ""
        ).run();

        return jsonResponse({
          success: true,
          message: "Key request received. Contact Admin with your Device ID to activate quickly!"
        }, 200, corsHeaders);
      }

      // =============================================================
      //  1. SAVE USER's client_secrets.json (ORIGINAL ROUTE PRESERVED)
      // =============================================================
      if (path === "/api/user/save-secrets" && request.method === "POST") {
        const body = await request.json();
        const { user_id, client_secrets } = body;

        if (!user_id || !client_secrets) {
          return jsonResponse({ error: "user_id and client_secrets are required" }, 400, corsHeaders);
        }

        const config = client_secrets.web || client_secrets.installed || client_secrets;
        const clientId = config.client_id;
        const clientSecret = config.client_secret;

        if (!clientId || !clientSecret) {
          return jsonResponse({ error: "Invalid client_secrets.json format" }, 400, corsHeaders);
        }

        await env.DB.prepare(`
          INSERT INTO users (user_id, client_id, client_secret, updated_at)
          VALUES (?, ?, ?, CURRENT_TIMESTAMP)
          ON CONFLICT(user_id) DO UPDATE SET
            client_id = excluded.client_id,
            client_secret = excluded.client_secret,
            updated_at = CURRENT_TIMESTAMP
        `).bind(user_id, clientId, clientSecret).run();

        return jsonResponse({ success: true, message: "Credentials saved. Now proceed to Google Login." }, 200, corsHeaders);
      }

      // =============================================================
      //  2. REDIRECT USER TO GOOGLE LOGIN (ORIGINAL ROUTE PRESERVED)
      // =============================================================
      if (path === "/api/auth/google/login" && request.method === "GET") {
        const userId = url.searchParams.get("user_id");
        if (!userId) {
          return jsonResponse({ error: "user_id param missing" }, 400, corsHeaders);
        }

        const user = await env.DB.prepare("SELECT client_id FROM users WHERE user_id = ?").bind(userId).first();
        if (!user || !user.client_id) {
          return jsonResponse({ error: "Please upload client_secrets.json first" }, 404, corsHeaders);
        }

        const redirectUri = `${url.origin}/api/auth/google/callback`;
        const googleAuthUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
        googleAuthUrl.searchParams.set("client_id", user.client_id);
        googleAuthUrl.searchParams.set("redirect_uri", redirectUri);
        googleAuthUrl.searchParams.set("response_type", "code");
        googleAuthUrl.searchParams.set("scope", "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly");
        googleAuthUrl.searchParams.set("access_type", "offline");
        googleAuthUrl.searchParams.set("prompt", "consent");
        googleAuthUrl.searchParams.set("state", userId);

        return Response.redirect(googleAuthUrl.toString(), 302);
      }

      // =============================================================
      //  3. GOOGLE OAUTH CALLBACK (ORIGINAL ROUTE PRESERVED)
      // =============================================================
      if (path === "/api/auth/google/callback" && request.method === "GET") {
        const code = url.searchParams.get("code");
        const userId = url.searchParams.get("state");
        const redirectUri = `${url.origin}/api/auth/google/callback`;

        if (!code || !userId) {
          return new Response("Missing authorization code or user_id state.", { status: 400 });
        }

        const user = await env.DB.prepare("SELECT client_id, client_secret FROM users WHERE user_id = ?").bind(userId).first();
        if (!user) {
          return new Response("User credentials not found in database.", { status: 404 });
        }

        const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            code: code,
            client_id: user.client_id,
            client_secret: user.client_secret,
            redirect_uri: redirectUri,
            grant_type: "authorization_code"
          })
        });

        const tokenData = await tokenRes.json();
        if (!tokenRes.ok || tokenData.error) {
          return jsonResponse({ error: "Failed to exchange token", details: tokenData }, 400, corsHeaders);
        }

        const accessToken = tokenData.access_token;
        const refreshToken = tokenData.refresh_token;
        const expiry = Date.now() + (tokenData.expires_in * 1000);

        if (refreshToken) {
          await env.DB.prepare(`
            UPDATE users SET access_token = ?, refresh_token = ?, token_expiry = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
          `).bind(accessToken, refreshToken, expiry, userId).run();
        } else {
          await env.DB.prepare(`
            UPDATE users SET access_token = ?, token_expiry = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
          `).bind(accessToken, expiry, userId).run();
        }

        const frontEnd = env.FRONTEND_URL || "http://localhost:8000";
        return Response.redirect(`${frontEnd}?auth_status=success&user_id=${userId}`, 302);
      }

      // =============================================================
      //  4. GET VALID ACCESS TOKEN (ORIGINAL ROUTE PRESERVED)
      // =============================================================
      if (path === "/api/user/token" && request.method === "GET") {
        const userId = url.searchParams.get("user_id");
        if (!userId) {
          return jsonResponse({ error: "user_id required" }, 400, corsHeaders);
        }

        const user = await env.DB.prepare("SELECT * FROM users WHERE user_id = ?").bind(userId).first();
        if (!user || !user.refresh_token) {
          return jsonResponse({ authenticated: false, error: "User not authenticated with YouTube" }, 404, corsHeaders);
        }

        if (user.access_token && user.token_expiry && user.token_expiry > Date.now() + 300000) {
          return jsonResponse({
            authenticated: true,
            access_token: user.access_token,
            client_id: user.client_id,
            client_secret: user.client_secret,
            refresh_token: user.refresh_token
          }, 200, corsHeaders);
        }

        const refreshRes = await fetch("https://oauth2.googleapis.com/token", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            client_id: user.client_id,
            client_secret: user.client_secret,
            refresh_token: user.refresh_token,
            grant_type: "refresh_token"
          })
        });

        const refreshData = await refreshRes.json();
        if (!refreshRes.ok || refreshData.error) {
          return jsonResponse({ authenticated: false, error: "Failed to refresh token", details: refreshData }, 400, corsHeaders);
        }

        const newAccessToken = refreshData.access_token;
        const newExpiry = Date.now() + (refreshData.expires_in * 1000);

        await env.DB.prepare("UPDATE users SET access_token = ?, token_expiry = ? WHERE user_id = ?")
          .bind(newAccessToken, newExpiry, userId).run();

        return jsonResponse({
          authenticated: true,
          access_token: newAccessToken,
          client_id: user.client_id,
          client_secret: user.client_secret,
          refresh_token: user.refresh_token
        }, 200, corsHeaders);
      }

      return new Response("Not Found", { status: 404 });
    } catch (err) {
      return jsonResponse({ error: err.message }, 500, corsHeaders);
    }
  }
};

// --- Helper: Response Builder ---
function jsonResponse(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers }
  });
}

// --- Helper: Generate Key (e.g. OMNI-8F3A-7B2C-9E41) ---
function generateLicenseKey() {
  const seg = () => Math.random().toString(36).substring(2, 6).toUpperCase();
  return `OMNI-${seg()}-${seg()}-${seg()}`;
}

// --- Helper: Parse Duration ---
function parseDurationToSeconds(type, customHours) {
  if (type === 'lifetime') return -1;
  if (customHours && Number(customHours) > 0) {
    return Math.floor(Number(customHours) * 3600);
  }
  const map = {
    '1h': 3600,
    '2h': 7200,
    '3h': 10800,
    '5h': 18000,
    '10h': 36000,
    '24h': 86400,
    '1d': 86400,
    '2d': 172800,
    '3d': 259200,
    '7d': 604800,
    '15d': 1296000,
    '30d': 2592000,
    '90d': 7776000,
    '365d': 31536000
  };
  return map[type] || 86400; // default 24h
}

// --- Helper: Format Seconds to Human ---
function formatSecondsToHuman(seconds) {
  if (seconds < 0) return "Expired";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h remaining`;
  if (h > 0) return `${h}h ${m}m remaining`;
  return `${m}m remaining`;
}

// --- Render Admin Dashboard Web Page ---
function renderAdminHTML() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OmniStream — Admin License Control Hub</title>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(22, 29, 47, 0.7);
      --border: rgba(255, 255, 255, 0.08);
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --accent-emerald: #10b981;
      --accent-rose: #f43f5e;
      --accent-amber: #f59e0b;
      --accent-purple: #8b5cf6;
      --text: #f8fafc;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: radial-gradient(circle at 50% 0%, #1e1b4b 0%, #090d16 60%);
      color: var(--text);
      font-family: 'Plus Jakarta Sans', sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    .header {
      padding: 20px 32px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      background: rgba(9, 13, 22, 0.8);
      backdrop-filter: blur(12px);
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .logo {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 20px;
      font-weight: 800;
      background: linear-gradient(135deg, #60a5fa, #a855f7);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .container {
      max-width: 1280px;
      margin: 32px auto;
      padding: 0 20px;
      width: 100%;
      flex: 1;
    }
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      padding: 20px;
      border-radius: 16px;
      backdrop-filter: blur(8px);
    }
    .stat-title { font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
    .stat-val { font-size: 32px; font-weight: 800; margin-top: 6px; }

    .action-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
      margin-bottom: 24px;
    }
    .btn {
      padding: 10px 20px;
      border-radius: 10px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      border: none;
      transition: all 0.2s;
    }
    .btn-primary { background: var(--primary); color: #fff; }
    .btn-primary:hover { background: var(--primary-hover); transform: translateY(-1px); }
    .btn-danger { background: rgba(244, 63, 94, 0.15); color: #fda4af; border: 1px solid rgba(244, 63, 94, 0.3); }
    .btn-danger:hover { background: var(--accent-rose); color: #fff; }
    .btn-success { background: rgba(16, 185, 129, 0.15); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.3); }
    .btn-success:hover { background: var(--accent-emerald); color: #fff; }
    .btn-copy { background: rgba(255, 255, 255, 0.08); color: #e2e8f0; border: 1px solid var(--border); font-size: 12px; padding: 6px 12px; }
    .btn-copy:hover { background: rgba(255, 255, 255, 0.15); }

    .table-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 16px;
      overflow: hidden;
      backdrop-filter: blur(8px);
    }
    table { width: 100%; border-collapse: collapse; text-align: left; }
    th {
      background: rgba(15, 23, 42, 0.6);
      padding: 14px 18px;
      font-size: 12px;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      border-bottom: 1px solid var(--border);
    }
    td {
      padding: 16px 18px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      font-size: 14px;
      vertical-align: middle;
    }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }
    .key-badge {
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.3);
      padding: 4px 8px;
      border-radius: 6px;
      color: #93c5fd;
    }
    .device-badge {
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: var(--text-muted);
    }
    .tag {
      padding: 4px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .tag-active { background: rgba(16, 185, 129, 0.15); color: #34d399; }
    .tag-expired { background: rgba(244, 63, 94, 0.15); color: #fb7185; }
    .tag-revoked { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }

    .perm-pills { display: flex; flex-wrap: wrap; gap: 4px; }
    .perm-pill {
      font-size: 10px;
      padding: 2px 6px;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.06);
      color: #cbd5e1;
    }
    .perm-pill.on { background: rgba(59, 130, 246, 0.2); color: #93c5fd; }

    /* Modal */
    .modal-overlay {
      position: fixed; inset: 0; background: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(8px); display: flex; align-items: center; justify-content: center;
      z-index: 100;
    }
    .modal {
      background: #111827; border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 20px; width: 90%; max-width: 580px; padding: 28px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    }
    .modal-title { font-size: 20px; font-weight: 700; margin-bottom: 20px; }
    .form-group { margin-bottom: 16px; }
    .form-group label { display: block; font-size: 13px; font-weight: 600; color: var(--text-muted); margin-bottom: 6px; }
    .form-input, .form-select {
      width: 100%; padding: 10px 14px; background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border); border-radius: 10px; color: #fff;
      font-size: 14px; outline: none;
    }
    .form-input:focus, .form-select:focus { border-color: var(--primary); }
    .perm-checkboxes {
      display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
      background: rgba(0, 0, 0, 0.25); padding: 14px; border-radius: 12px;
      border: 1px solid var(--border);
    }
    .chk-label { display: flex; align-items: center; gap: 8px; font-size: 13px; cursor: pointer; }
    .chk-label input { accent-color: var(--primary); width: 16px; height: 16px; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 24px; }
    .hidden { display: none !important; }
  </style>
</head>
<body>

  <!-- Login Modal if unauthenticated -->
  <div id="loginModal" class="modal-overlay">
    <div class="modal" style="max-width: 400px; text-align: center;">
      <h2 class="modal-title">🔐 Admin Master Access</h2>
      <p style="color: var(--text-muted); font-size: 14px; margin-bottom: 20px;">Enter Cloudflare Worker Admin Password</p>
      <input type="password" id="adminPassInput" class="form-input" placeholder="Password (default: admin123)" style="margin-bottom: 16px;">
      <button class="btn btn-primary" style="width: 100%; justify-content: center;" onclick="adminLogin()">Unlock Dashboard</button>
      <p id="loginError" style="color: var(--accent-rose); font-size: 13px; margin-top: 12px;" class="hidden"></p>
    </div>
  </div>

  <header class="header">
    <div class="logo">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
        <polyline points="2 17 12 22 22 17"></polyline>
        <polyline points="2 12 12 17 22 12"></polyline>
      </svg>
      <span>OmniStream License Hub</span>
    </div>
    <div style="display: flex; gap: 12px; align-items: center;">
      <button class="btn btn-primary" onclick="openCreateModal()">+ Generate New Key</button>
      <button class="btn btn-copy" onclick="logoutAdmin()">Logout</button>
    </div>
  </header>

  <div class="container">
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-title">Total Generated</div>
        <div class="stat-val" id="statTotal">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Active Licenses</div>
        <div class="stat-val" style="color: var(--accent-emerald);" id="statActive">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Expired / Revoked</div>
        <div class="stat-val" style="color: var(--accent-rose);" id="statExpired">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Bound Devices</div>
        <div class="stat-val" style="color: var(--accent-purple);" id="statDevices">0</div>
      </div>
    </div>

    <div class="action-bar">
      <div style="display: flex; gap: 10px; align-items: center;">
        <input type="text" id="searchInput" class="form-input" placeholder="Search by Key, User or Device ID..." style="width: 320px;" oninput="filterKeys()">
        <select id="statusFilter" class="form-select" style="width: 140px;" onchange="filterKeys()">
          <option value="all">All Status</option>
          <option value="active">Active Only</option>
          <option value="expired">Expired</option>
          <option value="revoked">Revoked</option>
        </select>
      </div>
      <button class="btn btn-copy" onclick="loadKeys()">↻ Refresh Data</button>
    </div>

    <div class="table-card">
      <table>
        <thead>
          <tr>
            <th>User & Status</th>
            <th>License Key</th>
            <th>Device Binding</th>
            <th>Remaining Validity</th>
            <th>Feature Permissions</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="keysTableBody">
          <tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 40px;">Loading licenses...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Create Key Modal -->
  <div id="createModal" class="modal-overlay hidden">
    <div class="modal">
      <h2 class="modal-title">⚡ Generate New Access Key</h2>
      <form id="createKeyForm" onsubmit="handleCreateKey(event)">
        <div class="form-group">
          <label>Assign to User Name</label>
          <input type="text" id="newUserName" class="form-input" placeholder="e.g. John Doe / VIP Student" required>
        </div>
        <div class="form-group">
          <label>Lock to Specific Device ID (Optional)</label>
          <input type="text" id="newDeviceId" class="form-input" placeholder="Leave empty to auto-bind on first activation (Recommended)">
        </div>
        <div class="form-group">
          <label>Duration & Expiry Window</label>
          <select id="newDurationType" class="form-select" onchange="toggleCustomDuration()">
            <option value="1h">1 Hour</option>
            <option value="2h">2 Hours</option>
            <option value="5h">5 Hours</option>
            <option value="10h">10 Hours</option>
            <option value="24h" selected>1 Day (24 Hours)</option>
            <option value="3d">3 Days</option>
            <option value="7d">7 Days (1 Week)</option>
            <option value="15d">15 Days</option>
            <option value="30d">30 Days (1 Month)</option>
            <option value="90d">90 Days</option>
            <option value="365d">365 Days (1 Year)</option>
            <option value="lifetime">Lifetime Unlimited Access</option>
            <option value="custom">Custom Hours</option>
          </select>
        </div>
        <div class="form-group hidden" id="customHoursGroup">
          <label>Custom Expiry in Hours</label>
          <input type="number" id="newCustomHours" class="form-input" placeholder="e.g. 48 for 2 days">
        </div>
        <div class="form-group">
          <label>Granular Feature Permissions (Lock / Unlock)</label>
          <div class="perm-checkboxes">
            <label class="chk-label"><input type="checkbox" id="chkSingle" checked> <span>Single Download</span></label>
            <label class="chk-label"><input type="checkbox" id="chkBatch" checked> <span>Batch / Multi-Link</span></label>
            <label class="chk-label"><input type="checkbox" id="chkCp" checked> <span>ClassPlus & DRM</span></label>
            <label class="chk-label"><input type="checkbox" id="chkYt" checked> <span>YouTube Upload</span></label>
            <label class="chk-label"><input type="checkbox" id="chkTg" checked> <span>Telegram Upload</span></label>
            <label class="chk-label"><input type="checkbox" id="chkDisk"> <span>Disk Clean (Optional)</span></label>
          </div>
        </div>
        <div class="modal-actions">
          <button type="button" class="btn btn-copy" onclick="closeCreateModal()">Cancel</button>
          <button type="submit" class="btn btn-primary" id="btnSubmitKey">Generate Key</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    let allKeys = [];

    function getAdminPass() {
      return localStorage.getItem('omni_admin_pass') || '';
    }

    async function adminLogin() {
      const pass = document.getElementById('adminPassInput').value.trim();
      const err = document.getElementById('loginError');
      if (!pass) return;

      try {
        const res = await fetch('/api/admin/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: pass })
        });
        const data = await res.json();
        if (data.success) {
          localStorage.setItem('omni_admin_pass', pass);
          document.getElementById('loginModal').classList.add('hidden');
          loadKeys();
        } else {
          err.textContent = data.error || 'Authentication failed';
          err.classList.remove('hidden');
        }
      } catch (e) {
        err.textContent = 'Server connection error';
        err.classList.remove('hidden');
      }
    }

    function logoutAdmin() {
      localStorage.removeItem('omni_admin_pass');
      location.reload();
    }

    async function loadKeys() {
      const pass = getAdminPass();
      if (!pass) {
        document.getElementById('loginModal').classList.remove('hidden');
        return;
      }
      try {
        const res = await fetch('/api/admin/keys', {
          headers: { 'X-Admin-Password': pass }
        });
        if (res.status === 401) {
          localStorage.removeItem('omni_admin_pass');
          document.getElementById('loginModal').classList.remove('hidden');
          return;
        }
        const data = await res.json();
        if (data.success) {
          allKeys = data.keys || [];
          renderStats();
          filterKeys();
        }
      } catch (err) {
        console.error(err);
      }
    }

    function renderStats() {
      document.getElementById('statTotal').textContent = allKeys.length;
      document.getElementById('statActive').textContent = allKeys.filter(k => k.status === 'active' && !k.is_expired).length;
      document.getElementById('statExpired').textContent = allKeys.filter(k => k.status === 'revoked' || k.is_expired).length;
      document.getElementById('statDevices').textContent = allKeys.filter(k => k.device_id).length;
    }

    function filterKeys() {
      const query = (document.getElementById('searchInput').value || '').toLowerCase();
      const status = document.getElementById('statusFilter').value;

      const filtered = allKeys.filter(k => {
        const matchesQuery = (k.license_key || '').toLowerCase().includes(query) ||
                             (k.user_name || '').toLowerCase().includes(query) ||
                             (k.device_id || '').toLowerCase().includes(query);
        let matchesStatus = true;
        if (status === 'active') matchesStatus = (k.status === 'active' && !k.is_expired);
        if (status === 'expired') matchesStatus = k.is_expired;
        if (status === 'revoked') matchesStatus = (k.status === 'revoked');
        return matchesQuery && matchesStatus;
      });

      renderTable(filtered);
    }

    function renderTable(keys) {
      const tbody = document.getElementById('keysTableBody');
      if (keys.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 40px;">No matching licenses found.</td></tr>';
        return;
      }

      tbody.innerHTML = keys.map(k => {
        let tagClass = 'tag-active';
        let statusText = 'ACTIVE';
        if (k.status === 'revoked') { tagClass = 'tag-revoked'; statusText = 'REVOKED'; }
        else if (k.is_expired) { tagClass = 'tag-expired'; statusText = 'EXPIRED'; }

        const deviceHtml = k.device_id 
          ? \`<span class="device-badge">🔒 \${k.device_id}</span>\`
          : \`<span style="color: var(--accent-amber); font-size: 12px;">⏳ Unbound (Binds on 1st use)</span>\`;

        return \`
          <tr>
            <td>
              <div style="font-weight: 700; margin-bottom: 4px;">\${k.user_name || 'Anonymous'}</div>
              <span class="tag \${tagClass}">\${statusText}</span>
            </td>
            <td>
              <span class="key-badge">\${k.license_key}</span>
              <button class="btn btn-copy" style="margin-left: 8px;" onclick="copyText('\${k.license_key}')">Copy</button>
            </td>
            <td>\${deviceHtml}</td>
            <td>
              <div style="font-weight: 600;">\${k.remaining_time}</div>
              <div style="font-size: 11px; color: var(--text-muted);">Window: \${k.duration_type}</div>
            </td>
            <td>
              <div class="perm-pills">
                <span class="perm-pill \${k.allow_single ? 'on' : ''}">Single</span>
                <span class="perm-pill \${k.allow_batch ? 'on' : ''}">Batch</span>
                <span class="perm-pill \${k.allow_cp_support ? 'on' : ''}">Classplus</span>
                <span class="perm-pill \${k.allow_yt_upload ? 'on' : ''}">YT</span>
                <span class="perm-pill \${k.allow_tg_upload ? 'on' : ''}">TG</span>
                <span class="perm-pill \${k.allow_disk_clean ? 'on' : ''}">Clean</span>
              </div>
            </td>
            <td>
              <div style="display: flex; gap: 6px;">
                <button class="btn btn-copy" title="Extend 24h" onclick="extendKey('\${k.license_key}', 24)">+24h</button>
                \${k.status === 'active' 
                  ? \`<button class="btn btn-danger" onclick="toggleRevoke('\${k.license_key}', 'revoked')">Revoke</button>\` 
                  : \`<button class="btn btn-success" onclick="toggleRevoke('\${k.license_key}', 'active')">Unrevoke</button>\`}
                <button class="btn btn-copy" style="color: var(--accent-rose);" onclick="deleteKey('\${k.license_key}')">✕</button>
              </div>
            </td>
          </tr>
        \`;
      }).join('');
    }

    function openCreateModal() { document.getElementById('createModal').classList.remove('hidden'); }
    function closeCreateModal() { document.getElementById('createModal').classList.add('hidden'); }
    function toggleCustomDuration() {
      const type = document.getElementById('newDurationType').value;
      document.getElementById('customHoursGroup').classList.toggle('hidden', type !== 'custom');
    }

    async function handleCreateKey(e) {
      e.preventDefault();
      const pass = getAdminPass();
      const payload = {
        user_name: document.getElementById('newUserName').value.trim(),
        device_id: document.getElementById('newDeviceId').value.trim() || null,
        duration_type: document.getElementById('newDurationType').value,
        custom_hours: document.getElementById('newCustomHours').value || null,
        allow_single: document.getElementById('chkSingle').checked ? 1 : 0,
        allow_batch: document.getElementById('chkBatch').checked ? 1 : 0,
        allow_cp_support: document.getElementById('chkCp').checked ? 1 : 0,
        allow_yt_upload: document.getElementById('chkYt').checked ? 1 : 0,
        allow_tg_upload: document.getElementById('chkTg').checked ? 1 : 0,
        allow_disk_clean: document.getElementById('chkDisk').checked ? 1 : 0,
      };

      try {
        const res = await fetch('/api/admin/keys/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Admin-Password': pass },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
          alert('✓ Key Generated: ' + data.key);
          closeCreateModal();
          document.getElementById('createKeyForm').reset();
          loadKeys();
        } else {
          alert('Error: ' + (data.error || 'Failed to create key'));
        }
      } catch (err) {
        alert('Network error generating key');
      }
    }

    async function toggleRevoke(key, newStatus) {
      const pass = getAdminPass();
      if (!confirm(\`Are you sure you want to set key to \${newStatus}?\`)) return;
      await fetch('/api/admin/keys/revoke', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Password': pass },
        body: JSON.stringify({ key, status: newStatus })
      });
      loadKeys();
    }

    async function deleteKey(key) {
      const pass = getAdminPass();
      if (!confirm(\`Permanently delete key \${key}?\`)) return;
      await fetch('/api/admin/keys/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Password': pass },
        body: JSON.stringify({ key })
      });
      loadKeys();
    }

    async function extendKey(key, hours) {
      const pass = getAdminPass();
      await fetch('/api/admin/keys/extend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Password': pass },
        body: JSON.stringify({ key, add_hours: hours })
      });
      loadKeys();
    }

    function copyText(txt) {
      navigator.clipboard.writeText(txt);
      alert('Copied: ' + txt);
    }

    // Auto load on start
    document.addEventListener('DOMContentLoaded', () => {
      if (getAdminPass()) {
        document.getElementById('loginModal').classList.add('hidden');
        loadKeys();
      }
    });
  </script>
</body>
</html>`;
}
