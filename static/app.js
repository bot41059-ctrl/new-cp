/**
 * OmniStream — Interactive Frontend Logic & Queue Management
 */

// Application State
let currentTab = 'single';
let currentFilter = 'all';
let allTasks = [];
let pollInterval = null;

// User ID Management
function getUserId() {
  let uid = localStorage.getItem('omni_user_id');
  if (!uid) {
    uid = 'user_' + Math.random().toString(36).substring(2, 10);
    localStorage.setItem('omni_user_id', uid);
  }
  return uid;
}

// Device ID & License Management
let currentPermissions = {
  single: true,
  batch: true,
  cp_support: true,
  yt_upload: true,
  tg_upload: true,
  disk_clean: true
};
let isLicenseEnforced = false;
let activeLicenseData = null;

function getDeviceId() {
  let devId = localStorage.getItem('omni_device_id');
  if (!devId) {
    const rand1 = Math.random().toString(36).substring(2, 6).toUpperCase();
    const rand2 = Math.random().toString(36).substring(2, 6).toUpperCase();
    const rand3 = Math.floor(1000 + Math.random() * 9000);
    devId = `DEV-${rand1}-${rand2}-${rand3}`;
    localStorage.setItem('omni_device_id', devId);
  }
  return devId;
}

function getActiveLicense() {
  try {
    const raw = localStorage.getItem('omni_license_data');
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function saveActiveLicense(data) {
  if (!data) {
    localStorage.removeItem('omni_license_data');
    localStorage.removeItem('omni_license_key');
  } else {
    localStorage.setItem('omni_license_data', JSON.stringify(data));
    if (data.license_key) {
      localStorage.setItem('omni_license_key', data.license_key);
    }
  }
}

// Initialize on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('auth_status') === 'success') {
    showToast('YouTube channel connected successfully!', 'success');
    window.history.replaceState({}, document.title, window.location.pathname);
  }

  const singleUrlInput = document.getElementById('singleUrl');
  if (singleUrlInput) {
    singleUrlInput.addEventListener('input', (e) => {
      detectStreamType(e.target.value.trim());
    });
  }

  fetchQueue();
  fetchStats();
  checkYouTubeStatus();
  initServerTgConfig();
  startPolling();
});

// --- YouTube Status & Options ---
async function checkYouTubeStatus() {
  try {
    const uid = getUserId();
    const res = await fetch(`/api/youtube/status?user_id=${encodeURIComponent(uid)}`);
    if (!res.ok) return;
    const data = await res.json();
    const singleBadge = document.getElementById('ytAuthBadgeSingle');
    const batchBadge = document.getElementById('ytAuthBadgeBatch');
    const headerBtn = document.getElementById('btnOpenYtModal');
    const headerText = document.getElementById('headerYtStatusText');

    if (data.authenticated) {
      if (singleBadge) { singleBadge.textContent = '● Authenticated'; singleBadge.className = 'yt-status-tag active'; }
      if (batchBadge) { batchBadge.textContent = '● Authenticated'; batchBadge.className = 'yt-status-tag active'; }
      if (headerBtn) headerBtn.classList.add('connected');
      if (headerText) headerText.textContent = data.channel_name || 'YouTube Connected';
      updateModalStatus(true, data.channel_name);
    } else {
      if (singleBadge) { singleBadge.textContent = '⚠ Auth Needed'; singleBadge.className = 'yt-status-tag warning'; }
      if (batchBadge) { batchBadge.textContent = '⚠ Auth Needed'; batchBadge.className = 'yt-status-tag warning'; }
      if (headerBtn) headerBtn.classList.remove('connected');
      if (headerText) headerText.textContent = 'Link YouTube';
      updateModalStatus(false);
    }
  } catch (e) {
    console.log('YouTube status check error:', e);
  }
}

function updateModalStatus(connected, channelName) {
  const dot = document.getElementById('modalYtDot');
  const msg = document.getElementById('modalYtStatusMsg');
  const btn = document.getElementById('btnGoogleConnect');
  if (connected) {
    if (dot) { dot.className = 'status-indicator-dot active'; }
    if (msg) { msg.textContent = `Connected: ${channelName || 'Your YouTube Channel'}`; }
    if (btn) { btn.textContent = 'Channel Already Linked (Click to Re-link)'; btn.disabled = false; }
  } else {
    if (dot) { dot.className = 'status-indicator-dot'; }
    if (msg) { msg.textContent = 'Not connected. Please upload client_secrets.json first.'; }
  }
}

function openYtModal() {
  const overlay = document.getElementById('ytModalOverlay');
  const uidVal = document.getElementById('modalUserId');
  if (uidVal) uidVal.textContent = getUserId();
  if (overlay) overlay.classList.remove('hidden');
}

function closeYtModal(e) {
  const overlay = document.getElementById('ytModalOverlay');
  if (overlay) overlay.classList.add('hidden');
}

async function handleSecretsFile(file) {
  if (!file) return;
  const statusDiv = document.getElementById('fileUploadStatus');
  const btnConnect = document.getElementById('btnGoogleConnect');
  statusDiv.textContent = 'Reading file...';
  statusDiv.style.color = 'var(--text-muted)';

  try {
    const text = await file.text();
    const json = JSON.parse(text);
    const uid = getUserId();

    statusDiv.textContent = 'Saving credentials to Cloudflare...';
    const res = await fetch('/api/user/save-secrets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: uid, client_secrets: json })
    });

    const data = await res.json();
    if (res.ok && data.success) {
      statusDiv.textContent = '✓ client_secrets.json saved! Now click Step 2.';
      statusDiv.style.color = 'var(--accent-emerald)';
      if (btnConnect) btnConnect.disabled = false;
      showToast('Credentials saved successfully!', 'success');
    } else {
      statusDiv.textContent = `✕ Error: ${data.detail || data.error || 'Failed to save'}`;
      statusDiv.style.color = 'var(--accent-rose)';
    }
  } catch (err) {
    statusDiv.textContent = '✕ Invalid JSON file. Please upload a valid client_secrets.json';
    statusDiv.style.color = 'var(--accent-rose)';
  }
}

async function startGoogleAuth() {
  const uid = getUserId();
  try {
    const res = await fetch(`/api/user/login-url?user_id=${encodeURIComponent(uid)}`);
    const data = await res.json();
    if (data.url) {
      window.location.href = data.url;
    } else {
      showToast(data.error || 'Authentication URL not available.', 'error');
    }
  } catch (err) {
    showToast('Failed to start Google sign-in.', 'error');
  }
}

function toggleYtPrivacy(mode) {
  const checkbox = document.getElementById(`${mode}YtUpload`);
  const options = document.getElementById(`${mode}YtOptions`);
  if (checkbox && options) {
    options.classList.toggle('hidden', !checkbox.checked);
  }
}

// --- Telegram Status & Settings Management ---
let serverTgConfig = {
  api_id_configured: true,
  api_hash_configured: true,
  bot_token_configured: true,
  default_chat_id: '',
  pyrogram_available: true
};

async function initServerTgConfig() {
  try {
    const res = await fetch('/api/telegram/config');
    if (res.ok) {
      serverTgConfig = await res.json();
      if (serverTgConfig.default_api_id && !localStorage.getItem('omni_tg_api_id')) {
        localStorage.setItem('omni_tg_api_id', serverTgConfig.default_api_id);
      }
      if (serverTgConfig.default_api_hash && !localStorage.getItem('omni_tg_api_hash')) {
        localStorage.setItem('omni_tg_api_hash', serverTgConfig.default_api_hash);
      }
      if (serverTgConfig.default_bot_token && !localStorage.getItem('omni_tg_bot_token')) {
        localStorage.setItem('omni_tg_bot_token', serverTgConfig.default_bot_token);
      }
      if (serverTgConfig.default_chat_id && !localStorage.getItem('omni_tg_default_chat')) {
        localStorage.setItem('omni_tg_default_chat', serverTgConfig.default_chat_id);
      }
    }
  } catch (e) {
    console.warn('Could not fetch server TG config:', e);
  }
  checkTelegramStatus();
}

function getTelegramConfig() {
  const localChat = localStorage.getItem('omni_tg_default_chat') || '';
  const localBotToken = localStorage.getItem('omni_tg_bot_token') || (serverTgConfig.default_bot_token || '');
  const localApiId = localStorage.getItem('omni_tg_api_id') || (serverTgConfig.default_api_id || '');
  const localApiHash = localStorage.getItem('omni_tg_api_hash') || (serverTgConfig.default_api_hash || '');
  const botUsername = localStorage.getItem('omni_tg_bot_username') || '';

  return {
    bot_token: localBotToken,
    api_id: localApiId,
    api_hash: localApiHash,
    default_chat: localChat || serverTgConfig.default_chat_id || '',
    bot_username: botUsername
  };
}

function checkTelegramStatus() {
  const cfg = getTelegramConfig();
  const headerBtn = document.getElementById('btnOpenTgModal');
  const headerText = document.getElementById('headerTgStatusText');
  const singleBadge = document.getElementById('tgConfigBadgeSingle');
  const batchBadge = document.getElementById('tgConfigBadgeBatch');
  const modalDot = document.getElementById('modalTgDot');
  const modalMsg = document.getElementById('modalTgStatusMsg');

  const hasChat = Boolean(cfg.default_chat);

  if (hasChat) {
    const displayChat = cfg.default_chat;
    if (headerBtn) headerBtn.classList.add('connected');
    if (headerText) headerText.textContent = `TG: ${displayChat}`;
    if (singleBadge) { singleBadge.textContent = '● ' + displayChat; singleBadge.className = 'tg-status-tag active'; }
    if (batchBadge) { batchBadge.textContent = '● ' + displayChat; batchBadge.className = 'tg-status-tag active'; }
    if (modalDot) modalDot.className = 'status-indicator-dot active';
    if (modalMsg) modalMsg.textContent = `Connected to Channel: ${displayChat}`;
  } else if (serverTgConfig.bot_token_configured || cfg.bot_token) {
    if (headerBtn) headerBtn.classList.remove('connected');
    if (headerText) headerText.textContent = 'Enter Channel';
    if (singleBadge) { singleBadge.textContent = 'Enter Channel ID'; singleBadge.className = 'tg-status-tag'; }
    if (batchBadge) { batchBadge.textContent = 'Enter Channel ID'; batchBadge.className = 'tg-status-tag'; }
    if (modalDot) modalDot.className = 'status-indicator-dot';
    if (modalMsg) modalMsg.textContent = 'Bot Ready — Enter Channel ID to connect';
  } else {
    if (headerBtn) headerBtn.classList.remove('connected');
    if (headerText) headerText.textContent = 'Link Telegram';
    if (singleBadge) { singleBadge.textContent = 'Config Needed'; singleBadge.className = 'tg-status-tag'; }
    if (batchBadge) { batchBadge.textContent = 'Config Needed'; batchBadge.className = 'tg-status-tag'; }
    if (modalDot) modalDot.className = 'status-indicator-dot';
    if (modalMsg) modalMsg.textContent = 'Enter Channel / Bot Token';
  }
}

function openTgModal() {
  const overlay = document.getElementById('tgModalOverlay');
  const cfg = getTelegramConfig();
  const tokenInp = document.getElementById('tgModalBotToken');
  const apiIdInp = document.getElementById('tgModalApiId');
  const apiHashInp = document.getElementById('tgModalApiHash');
  const chatInp = document.getElementById('tgModalDefaultChat');

  if (tokenInp) tokenInp.value = cfg.bot_token;
  if (apiIdInp) apiIdInp.value = cfg.api_id;
  if (apiHashInp) apiHashInp.value = cfg.api_hash;
  if (chatInp) chatInp.value = cfg.default_chat;

  checkTelegramStatus();
  if (overlay) overlay.classList.remove('hidden');
  if (chatInp) setTimeout(() => chatInp.focus(), 150);
}

function closeTgModal(e) {
  const overlay = document.getElementById('tgModalOverlay');
  if (overlay) overlay.classList.add('hidden');
}

function toggleTgOptions(mode) {
  const checkbox = document.getElementById(`${mode}TgUpload`);
  const options = document.getElementById(`${mode}TgOptions`);
  const chatInput = document.getElementById(`${mode}TgChatId`);
  if (checkbox && options) {
    options.classList.toggle('hidden', !checkbox.checked);
    if (checkbox.checked && chatInput && !chatInput.value) {
      const cfg = getTelegramConfig();
      if (cfg.default_chat) chatInput.value = cfg.default_chat;
    }
  }
}

async function quickConnectTgChannel(channelId) {
  if (!channelId || !channelId.trim()) {
    showToast('Please enter a Channel or Chat ID (e.g. -100xxxxxxxxxx or @ChannelName)', 'error');
    return;
  }
  const cleanCid = channelId.trim();
  const cfg = getTelegramConfig();
  const testPayload = {
    chat_id: cleanCid,
    bot_token: cfg.bot_token || undefined
  };

  showToast('Connecting to Telegram Channel...', 'info');
  try {
    const res = await fetch('/api/telegram/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(testPayload)
    });
    const data = await res.json();
    if (data.valid !== false && (data.chat_connected !== false || !data.chat_error)) {
      const finalCid = data.formatted_chat_id || cleanCid;
      localStorage.setItem('omni_tg_default_chat', finalCid);
      if (data.username) localStorage.setItem('omni_tg_bot_username', data.username);

      const sInp = document.getElementById('singleTgChatId');
      const bInp = document.getElementById('batchTgChatId');
      if (sInp) sInp.value = finalCid;
      if (bInp) bInp.value = finalCid;

      checkTelegramStatus();
      const title = data.chat_title ? ` (${data.chat_title})` : '';
      showToast(`Telegram Channel Connected: ${finalCid}${title}!`, 'success');
      closeTgModal();
    } else {
      const errMsg = data.chat_error || data.error || 'Could not verify channel access';
      showToast(`Telegram: ${errMsg}`, 'error');
    }
  } catch (err) {
    localStorage.setItem('omni_tg_default_chat', cleanCid);
    const sInp = document.getElementById('singleTgChatId');
    const bInp = document.getElementById('batchTgChatId');
    if (sInp) sInp.value = cleanCid;
    if (bInp) bInp.value = cleanCid;
    checkTelegramStatus();
    showToast(`Channel ID saved: ${cleanCid}`, 'success');
    closeTgModal();
  }
}

async function saveTelegramSettings(e) {
  if (e) e.preventDefault();
  const defaultChat = (document.getElementById('tgModalDefaultChat')?.value || '').trim();
  const customBotToken = (document.getElementById('tgModalBotToken')?.value || '').trim();
  const customApiId = (document.getElementById('tgModalApiId')?.value || '').trim();
  const customApiHash = (document.getElementById('tgModalApiHash')?.value || '').trim();
  const btn = document.getElementById('btnSaveTg');

  if (customBotToken) localStorage.setItem('omni_tg_bot_token', customBotToken);
  if (customApiId) localStorage.setItem('omni_tg_api_id', customApiId);
  if (customApiHash) localStorage.setItem('omni_tg_api_hash', customApiHash);

  if (defaultChat) {
    if (btn) { btn.disabled = true; btn.textContent = 'Connecting...'; }
    await quickConnectTgChannel(defaultChat);
    if (btn) { btn.disabled = false; btn.textContent = 'Connect'; }
  } else if (customBotToken) {
    if (btn) { btn.disabled = true; btn.textContent = 'Testing Token...'; }
    try {
      const res = await fetch('/api/telegram/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bot_token: customBotToken })
      });
      const data = await res.json();
      if (data.valid) {
        if (data.username) localStorage.setItem('omni_tg_bot_username', data.username);
        showToast(`Bot Verified: @${data.username || 'Ready'}! Now enter your Channel ID.`, 'success');
        checkTelegramStatus();
      } else {
        showToast(`Telegram Error: ${data.error || 'Invalid Bot Token'}`, 'error');
      }
    } catch (err) {
      showToast('Network error verifying Telegram bot credentials', 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Connect'; }
    }
  } else {
    showToast('Please enter your Channel / Chat ID (e.g. -100xxxxxxxxxx or @ChannelName)', 'error');
  }
}


// --- License System Removed (All Features Free & Unlocked) ---
var isUserLicensed = true;
function updateFeatureLocks() {}
function checkLicenseStatus() {}
function openLicenseModal() {}
function closeLicenseModal() {}
function openKeyRequestModal() {}
function closeKeyRequestModal() {}
function handleLicenseActivation(e) { if (e) e.preventDefault(); }
function handleKeyRequestSubmit(e) { if (e) e.preventDefault(); }
function copyDeviceId() {}

// --- Tab Switching ---
function switchTab(tab) {
  currentTab = tab;
  const singleBtn = document.getElementById('tabSingleBtn');
  const batchBtn = document.getElementById('tabBatchBtn');
  const cpBtn = document.getElementById('tabCpSignerBtn');
  const singleForm = document.getElementById('singleForm');
  const batchForm = document.getElementById('batchForm');
  const cpForm = document.getElementById('cpSignerForm');
  const cpResult = document.getElementById('cpResultCard');

  singleBtn.classList.remove('active');
  batchBtn.classList.remove('active');
  if (cpBtn) cpBtn.classList.remove('active');

  singleForm.classList.add('hidden');
  batchForm.classList.add('hidden');
  if (cpForm) cpForm.classList.add('hidden');
  if (cpResult) cpResult.classList.add('hidden');

  if (tab === 'single') {
    singleBtn.classList.add('active');
    singleForm.classList.remove('hidden');
  } else if (tab === 'batch') {
    batchBtn.classList.add('active');
    batchForm.classList.remove('hidden');
  } else if (tab === 'cpsigner') {
    if (cpBtn) cpBtn.classList.add('active');
    if (cpForm) cpForm.classList.remove('hidden');
    if (currentCpExtractedData && cpResult) {
      cpResult.classList.remove('hidden');
    }
  }
}

// --- Classplus / DRM Extractor Logic ---
let currentCpExtractedData = null;

function fillCpSampleDemo() {
  document.getElementById('cpUrlInput').value = 'https://media-cdn.classplusapp.com/drm/b04d1316-sample-video/master.mpd';
  document.getElementById('cpOrgCodeInput').value = 'CPAPP';
  showToast('Sample Classplus URL & Org loaded!', 'info');
}

function clearInputDirect(inputId) {
  const input = document.getElementById(inputId);
  if (input) {
    input.value = '';
    input.focus();
  }
}

function togglePasswordVisibilityDirect(inputId, iconId) {
  const input = document.getElementById(inputId);
  const icon = document.getElementById(iconId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (icon) {
      icon.innerHTML = `
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
        <line x1="1" y1="1" x2="23" y2="23"></line>
      `;
    }
  } else {
    input.type = 'password';
    if (icon) {
      icon.innerHTML = `
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
        <circle cx="12" cy="12" r="3"></circle>
      `;
    }
  }
}

async function handleCpSignerSubmit(e) {
  e.preventDefault();

  const urlInput = document.getElementById('cpUrlInput');
  const tokenInput = document.getElementById('cpTokenInput');
  const orgCodeInput = document.getElementById('cpOrgCodeInput');
  const btn = document.getElementById('btnCpSignerSubmit');
  const btnText = document.getElementById('btnCpSubmitText');
  const spinner = document.getElementById('cpSpinner');
  const resultCard = document.getElementById('cpResultCard');

  const url = urlInput.value.trim();
  const token = tokenInput.value.trim();
  const orgCode = orgCodeInput.value.trim();

  if (!url) {
    showToast('Please enter a target Stream URL or Content ID', 'error');
    urlInput.focus();
    return;
  }

  btn.disabled = true;
  btnText.textContent = 'Extracting link...';
  spinner.classList.remove('hidden');
  resultCard.classList.add('hidden');

  try {
    const params = new URLSearchParams();
    params.append('url', url);
    if (token) params.append('token', token);
    if (orgCode) params.append('orgCode', orgCode);

    const res = await fetch(`/itsgolu?${params.toString()}`);
    const data = await res.json();

    if (!res.ok || !data.success) {
      throw new Error(data.message || data.detail || 'Signer API returned an error.');
    }

    currentCpExtractedData = data;
    renderCpResult(data);
    showToast('Link & keys extracted successfully!', 'success');
  } catch (err) {
    showToast(err.message || 'Error communicating with Classplus API', 'error');
  } finally {
    btn.disabled = false;
    btnText.textContent = 'Generate & Extract Link';
    spinner.classList.add('hidden');
  }
}

function renderCpResult(data) {
  const card = document.getElementById('cpResultCard');
  const badge = document.getElementById('cpResultBadge');
  const directSec = document.getElementById('cpDirectSection');
  const drmSec = document.getElementById('cpDrmSection');

  if (data.url) {
    // Direct URL
    badge.className = 'type-badge badge-direct';
    badge.textContent = 'Direct Signed URL';
    document.getElementById('cpDirectUrlBox').textContent = data.url;
    directSec.classList.remove('hidden');
    drmSec.classList.add('hidden');
  } else if (data.MPD || data.KEYS) {
    // DRM URL
    badge.className = 'type-badge badge-drm';
    badge.textContent = 'Widevine DRM Stream';

    const mpd = data.MPD || '';
    document.getElementById('cpMpdUrlBox').textContent = mpd;

    const keys = data.KEYS || [];
    const countBadge = document.getElementById('cpKeysCount');
    const badgeWrap = document.getElementById('cpKeysBadgeWrap');
    badgeWrap.innerHTML = '';

    if (keys.length > 0) {
      countBadge.textContent = `${keys.length} Key(s) Extracted`;
      keys.forEach(k => {
        const span = document.createElement('span');
        span.className = 'key-tag';
        span.textContent = k;
        badgeWrap.appendChild(span);
      });
      document.getElementById('cpKeysRawBox').textContent = keys.join('\n');
    } else {
      countBadge.textContent = 'No content keys';
      document.getElementById('cpKeysRawBox').textContent = 'No keys returned. WVD may not be loaded or stream requires active session.';
    }

    // Auto-generate N_m3u8DL-RE Command
    let cmd = `N_m3u8DL-RE "${mpd}"`;
    keys.forEach(k => {
      cmd += ` --key "${k}"`;
    });
    cmd += ` --save-name "Classplus_Video" -M format=mp4`;
    document.getElementById('cpCmdBox').textContent = cmd;

    // License URL
    const licWrapper = document.getElementById('cpLicUrlWrapper');
    const licBox = document.getElementById('cpLicUrlBox');
    if (licWrapper && licBox) {
      if (data.lic_url) {
        licBox.textContent = data.lic_url;
        licWrapper.classList.remove('hidden');
      } else {
        licWrapper.classList.add('hidden');
      }
    }

    directSec.classList.add('hidden');
    drmSec.classList.remove('hidden');
  }

  card.classList.remove('hidden');
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function copyTextById(elementId) {
  const elem = document.getElementById(elementId);
  if (!elem) return;
  const text = elem.textContent.trim();
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    showToast('Copied to clipboard!', 'success');
  }).catch(() => {
    showToast('Failed to copy. Please copy manually.', 'error');
  });
}

function downloadDirectCp() {
  if (!currentCpExtractedData || !currentCpExtractedData.url) {
    showToast('No direct stream link available to download', 'error');
    return;
  }
  const rawUrl = currentCpExtractedData.url;
  showToast('Starting direct file download...', 'info');
  const downloadUrl = `/download?url=${encodeURIComponent(rawUrl)}&filename=video.mp4`;
  const a = document.createElement('a');
  a.href = downloadUrl;
  a.setAttribute('download', 'video.mp4');
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function addCpToOmniQueue() {
  if (!currentCpExtractedData || !currentCpExtractedData.url) {
    showToast('No URL to queue', 'error');
    return;
  }
  const customName = prompt('Enter custom video title for queue:', 'Classplus_Lecture') || 'Classplus_Lecture';
  const uid = getUserId();

  try {
    showToast('Adding to download queue...', 'info');
    const res = await fetch('/api/download/single', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: currentCpExtractedData.url,
        custom_name: customName,
        resolution: '720',
        user_id: uid,
        license_key: localStorage.getItem('omni_license_key') || null,
        device_id: getDeviceId()
      })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast('✓ Video successfully enqueued in OmniStream!', 'success');
      fetchQueue();
      fetchStats();
    } else {
      showToast(data.detail || 'Failed to add to queue', 'error');
    }
  } catch (err) {
    showToast('Network error enqueuing video', 'error');
  }
}

async function addCpDrmToOmniQueue() {
  if (!currentCpExtractedData || !currentCpExtractedData.MPD) {
    showToast('No DRM MPD to queue', 'error');
    return;
  }
  const customName = prompt('Enter custom video title for queue:', 'Classplus_DRM_Video') || 'Classplus_DRM_Video';
  const uid = getUserId();

  try {
    showToast('Adding DRM stream to download queue...', 'info');
    const res = await fetch('/api/download/single', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: currentCpExtractedData.MPD,
        custom_name: customName,
        resolution: '720',
        user_id: uid,
        keys: currentCpExtractedData.KEYS || [],
        license_key: localStorage.getItem('omni_license_key') || null,
        device_id: getDeviceId()
      })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast('✓ DRM Stream successfully enqueued!', 'success');
      fetchQueue();
      fetchStats();
    } else {
      showToast(data.detail || 'Failed to add to queue', 'error');
    }
  } catch (err) {
    showToast('Network error enqueuing video', 'error');
  }
}

function resetCpForm() {
  document.getElementById('cpSignerForm').reset();
  document.getElementById('cpResultCard').classList.add('hidden');
  currentCpExtractedData = null;
  showToast('Extractor form reset', 'info');
}

// --- Stream Auto-Detection ---
function detectStreamType(rawUrl) {
  const badge = document.getElementById('singleStreamBadge');
  const advBadge = document.getElementById('singleAdvancedBadge');
  const refererInput = document.getElementById('singleReferer');
  const originInput = document.getElementById('singleOrigin');
  if (!badge) return;

  const url = (rawUrl || '').trim();
  if (!url) {
    badge.classList.add('hidden');
    if (advBadge) { advBadge.textContent = 'Auto-Detect'; advBadge.className = 'advanced-tag'; }
    return;
  }

  if (url.includes('classx.co.in') || url.includes('transcoded-videos.classx')) {
    const mTenant = url.match(/\/videos\/([a-zA-Z0-9_\-]+?)(?:-data)?\//);
    const tenantName = mTenant ? mTenant[1] : 'impetusgurukul';
    const autoRef = `https://${tenantName}.classx.co.in/`;
    const autoOrig = `https://${tenantName}.classx.co.in`;
    
    if (refererInput && !refererInput.value) refererInput.placeholder = autoRef;
    if (originInput && !originInput.value) originInput.placeholder = autoOrig;

    const hasEdgeToken = url.includes('edge-cache-token=');
    const tokenHint = hasEdgeToken ? '✓ Edge Cache Token Present' : 'Token: Optional/Auto';

    badge.className = 'stream-detect-badge classx-badge';
    badge.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polygon points="5 3 19 12 5 21 5 3"></polygon>
      </svg>
      <span><strong>ClassX Stream Detected:</strong> Origin: <code>${autoOrig}</code> | Referer: <code>${autoRef}</code> (${tokenHint})</span>
    `;
    badge.classList.remove('hidden');
    if (advBadge) { advBadge.textContent = 'ClassX Auto-Configured'; advBadge.className = 'advanced-tag active'; }
  } else if (url.includes('classplusapp') || url.includes('testbook.com') || url.includes('hdnts=') || url.includes('hdntl=')) {
    badge.className = 'stream-detect-badge classplus-badge';
    badge.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
        <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
      </svg>
      <span><strong>ClassPlus / Akamai Stream:</strong> Auto DRM Decryption & EdgeAuth headers active</span>
    `;
    badge.classList.remove('hidden');
    if (advBadge) { advBadge.textContent = 'ClassPlus Configured'; advBadge.className = 'advanced-tag active'; }
  } else if (url.includes('cloud-front.in') || url.includes('liveclasses') || url.includes('timeshift=1')) {
    badge.className = 'stream-detect-badge live-badge';
    badge.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"></circle>
        <circle cx="12" cy="12" r="3" fill="currentColor"></circle>
      </svg>
      <span><strong>CloudFront Live Timeshift:</strong> Anti-drift & Origin headers active</span>
    `;
    badge.classList.remove('hidden');
    if (advBadge) { advBadge.textContent = 'LiveClass Configured'; advBadge.className = 'advanced-tag active'; }
  } else {
    badge.classList.add('hidden');
    if (advBadge) { advBadge.textContent = 'Auto-Detect'; advBadge.className = 'advanced-tag'; }
  }
}

// --- Clipboard Helper ---
async function pasteClipboard(targetInputId) {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      const input = document.getElementById(targetInputId);
      input.value = text.trim();
      input.focus();
      showToast('URL pasted from clipboard', 'info');
      if (targetInputId === 'singleUrl') detectStreamType(text.trim());
      if (targetInputId === 'batchText') updateBatchCount();
    }
  } catch (err) {
    showToast('Clipboard access denied. Please paste manually.', 'error');
  }
}

// --- Batch URL Count Parser ---
function updateBatchCount() {
  const text = document.getElementById('batchText').value;
  const lines = text.split('\n').filter(l => l.trim().length > 0 && l.includes('http'));
  const counter = document.getElementById('batchLinkCount');
  counter.textContent = `${lines.length} link${lines.length === 1 ? '' : 's'} detected`;
}

// --- Submit Single Download ---
async function handleSingleSubmit(e) {
  e.preventDefault();

  const urlInput = document.getElementById('singleUrl');
  const nameInput = document.getElementById('singleName');
  const resInput = document.querySelector('input[name="singleRes"]:checked');
  const ytUpload = document.getElementById('singleYtUpload')?.checked || false;
  const ytPrivacy = document.getElementById('singleYtPrivacy')?.value || 'unlisted';
  const tgUpload = document.getElementById('singleTgUpload')?.checked || false;
  const tgChatId = document.getElementById('singleTgChatId')?.value.trim() || '';
  const customReferer = document.getElementById('singleReferer')?.value.trim() || null;
  const customOrigin = document.getElementById('singleOrigin')?.value.trim() || null;
  const customToken = document.getElementById('singleToken')?.value.trim() || null;
  const threadsVal = parseInt(document.getElementById('singleThreads')?.value || '16', 10);
  const tgConfig = getTelegramConfig();

  const effectiveChat = tgChatId || tgConfig.default_chat;
  if (tgUpload && !effectiveChat) {
    showToast('Please enter your Telegram Channel / Chat ID (e.g. -100xxxxxxxxxx or @ChannelName)', 'error');
    const targetInp = document.getElementById('singleTgChatId');
    if (targetInp) targetInp.focus();
    return;
  }

  const url = urlInput.value.trim();
  const customName = nameInput.value.trim();
  const resolution = resInput ? resInput.value : '720';

  if (!url) {
    showToast('Please enter a valid video URL', 'error');
    return;
  }

  const submitBtn = document.getElementById('btnSingleSubmit');
  if (submitBtn) submitBtn.disabled = true;

  // 🚀 OPTIMISTIC LIVE CARD: Show in queue instantly (0ms) so user never wonders if it started
  const tempId = 'temp_' + Date.now();
  const optimisticTask = {
    id: tempId,
    url: url,
    custom_name: customName,
    display_title: customName || url,
    resolution: resolution,
    media_type: detectStreamMediaType(url),
    upload_to_youtube: ytUpload,
    privacy_status: ytPrivacy,
    upload_to_telegram: tgUpload,
    tg_chat_id: tgChatId || tgConfig.default_chat || null,
    status: 'queued',
    progress: 0,
    speed_str: 'Starting download engine...',
    eta_str: 'Initializing...',
    filesize_str: 'Connecting...',
    created_at: Date.now() / 1000
  };

  allTasks.unshift(optimisticTask);
  renderQueue();

  // Smoothly scroll down towards queue so user sees the live card
  const qSec = document.getElementById('queueList');
  if (qSec) {
    qSec.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  try {
    const res = await fetch('/api/download/single', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: url,
        custom_name: customName || null,
        resolution: resolution,
        upload_to_youtube: ytUpload,
        privacy_status: ytPrivacy,
        user_id: getUserId(),
        upload_to_telegram: tgUpload,
        tg_bot_token: tgConfig.bot_token || null,
        tg_api_id: tgConfig.api_id || null,
        tg_api_hash: tgConfig.api_hash || null,
        tg_chat_id: tgChatId || tgConfig.default_chat || null,
        custom_referer: customReferer,
        custom_origin: customOrigin,
        token: customToken,
        threads: isNaN(threadsVal) ? 16 : threadsVal,
        license_key: localStorage.getItem('omni_license_key') || null,
        device_id: getDeviceId()
      })
    });

    let data = {};
    try {
      data = await res.json();
    } catch (e) {
      data = {};
    }

    if (res.ok && data.success && data.task) {
      showToast(ytUpload ? '✓ Video queued + YouTube upload enabled!' : '✓ Video added to download queue!', 'success');
      urlInput.value = '';
      nameInput.value = '';
      const refInp = document.getElementById('singleReferer');
      const origInp = document.getElementById('singleOrigin');
      const tokInp = document.getElementById('singleToken');
      if (refInp) refInp.value = '';
      if (origInp) origInp.value = '';
      if (tokInp) tokInp.value = '';
      detectStreamType('');

      // Replace optimistic card with real server task
      const optIdx = allTasks.findIndex(t => t.id === tempId);
      if (optIdx !== -1) {
        allTasks[optIdx] = data.task;
      } else {
        allTasks.unshift(data.task);
      }
      renderQueue();
      triggerImmediatePoll();
    } else {
      // Revert optimistic task
      allTasks = allTasks.filter(t => t.id !== tempId);
      renderQueue();
      showToast(data.detail || data.error || 'Failed to add video to queue.', 'error');
    }
  } catch (err) {
    allTasks = allTasks.filter(t => t.id !== tempId);
    renderQueue();
    showToast('Network error while adding video. Please try again.', 'error');
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

// --- Submit Batch Download ---
async function handleBatchSubmit(e) {
  e.preventDefault();

  const textArea = document.getElementById('batchText');
  const resInput = document.querySelector('input[name="batchRes"]:checked');
  const ytUpload = document.getElementById('batchYtUpload')?.checked || false;
  const ytPrivacy = document.getElementById('batchYtPrivacy')?.value || 'unlisted';
  const tgUpload = document.getElementById('batchTgUpload')?.checked || false;
  const tgChatId = document.getElementById('batchTgChatId')?.value.trim() || '';
  const defaultReferer = document.getElementById('batchReferer')?.value.trim() || null;
  const defaultOrigin = document.getElementById('batchOrigin')?.value.trim() || null;
  const defaultToken = document.getElementById('batchToken')?.value.trim() || null;
  const batchThreadsVal = parseInt(document.getElementById('batchThreads')?.value || '16', 10);
  const tgConfig = getTelegramConfig();

  const effectiveBatchChat = tgChatId || tgConfig.default_chat;
  if (tgUpload && !effectiveBatchChat) {
    showToast('Please enter your Telegram Channel / Chat ID (e.g. -100xxxxxxxxxx or @ChannelName)', 'error');
    const targetInp = document.getElementById('batchTgChatId');
    if (targetInp) targetInp.focus();
    return;
  }

  const rawText = textArea.value.trim();
  const defaultRes = resInput ? resInput.value : '720';

  if (!rawText) {
    showToast('Please paste at least one video link.', 'error');
    return;
  }

  const submitBtn = document.getElementById('btnBatchSubmit');
  if (submitBtn) submitBtn.disabled = true;

  // Optimistic feedback
  const tempBatchId = 'batch_' + Date.now();
  const optimisticBatchTask = {
    id: tempBatchId,
    url: 'Batch Download',
    custom_name: 'Adding batch videos...',
    display_title: 'Adding batch videos...',
    resolution: defaultRes,
    media_type: 'Batch Process',
    status: 'queued',
    progress: 0,
    speed_str: 'Processing links...',
    eta_str: 'Queuing...',
    filesize_str: '--',
    created_at: Date.now() / 1000
  };
  allTasks.unshift(optimisticBatchTask);
  renderQueue();

  try {
    const res = await fetch('/api/download/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        raw_text: rawText,
        default_resolution: defaultRes,
        upload_to_youtube: ytUpload,
        default_privacy: ytPrivacy,
        user_id: getUserId(),
        upload_to_telegram: tgUpload,
        tg_bot_token: tgConfig.bot_token || null,
        tg_api_id: tgConfig.api_id || null,
        tg_api_hash: tgConfig.api_hash || null,
        tg_chat_id: tgChatId || tgConfig.default_chat || null,
        default_referer: defaultReferer,
        default_origin: defaultOrigin,
        default_token: defaultToken,
        default_threads: isNaN(batchThreadsVal) ? 16 : batchThreadsVal,
        license_key: localStorage.getItem('omni_license_key') || null,
        device_id: getDeviceId()
      })
    });

    let data = {};
    try {
      data = await res.json();
    } catch (e) {
      data = {};
    }

    allTasks = allTasks.filter(t => t.id !== tempBatchId);

    if (res.ok && data.success) {
      showToast(`✓ Enqueued ${data.count} videos successfully!`, 'success');
      textArea.value = '';
      const refInp = document.getElementById('batchReferer');
      const origInp = document.getElementById('batchOrigin');
      const tokInp = document.getElementById('batchToken');
      if (refInp) refInp.value = '';
      if (origInp) origInp.value = '';
      if (tokInp) tokInp.value = '';
      updateBatchCount();
      if (data.tasks && Array.isArray(data.tasks)) {
        // Prepend new batch tasks
        const existingIds = new Set(allTasks.map(t => t.id));
        data.tasks.forEach(bt => {
          if (!existingIds.has(bt.id)) allTasks.unshift(bt);
        });
      }
      renderQueue();
      triggerImmediatePoll();
    } else {
      renderQueue();
      showToast(data.detail || data.error || 'Failed to process batch.', 'error');
    }
  } catch (err) {
    allTasks = allTasks.filter(t => t.id !== tempBatchId);
    renderQueue();
    showToast('Network error while enqueueing batch.', 'error');
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

// Media type helper for optimistic UI
function detectStreamMediaType(url) {
  const u = (url || '').toLowerCase();
  if (u.includes('drive.google.com') || u.includes('docs.google.com')) return 'Google Drive';
  if (u.includes('mega.nz') || u.includes('mega.co.nz')) return 'Mega.nz';
  if (u.includes('classplus') || u.includes('testbook.com') || u.includes('akamai') || u.includes('media-cdn')) return 'ClassPlus / Akamai';
  if (u.includes('classx.co.in') || u.includes('transcoded-videos.classx')) return 'ClassX Stream';
  if (u.includes('cloud-front.in') || u.includes('liveclasses') || u.includes('timeshift=1')) return 'Live Classes / Timeshift';
  if (u.includes('.m3u8')) return 'HLS Stream';
  if (u.includes('.mpd')) return 'DASH Stream';
  return 'Standard';
}

// --- Adaptive Real-Time Polling Engine ---
// Automatically syncs every 750ms during active downloads for a pure 60fps-like LIVE page experience
let pollTimeoutId = null;
let isPollingActive = false;

function scheduleNextPoll(delayMs) {
  if (pollTimeoutId) clearTimeout(pollTimeoutId);
  pollTimeoutId = setTimeout(runPollCycle, delayMs);
}

async function runPollCycle() {
  if (isPollingActive) {
    scheduleNextPoll(1000);
    return;
  }
  isPollingActive = true;
  try {
    await fetchQueue();
    await fetchStats();
  } catch (err) {
    console.warn('Poll cycle warning:', err);
  } finally {
    isPollingActive = false;
    // Check if any download, processing, or upload task is active
    const hasActiveTasks = allTasks.some(t => ['queued', 'downloading', 'processing', 'uploading'].includes(t.status));
    // High-speed 750ms polling when tasks are running, 2600ms when idle
    const nextInterval = hasActiveTasks ? 750 : 2600;
    scheduleNextPoll(nextInterval);
  }
}

function startPolling() {
  scheduleNextPoll(400);
}

function triggerImmediatePoll() {
  scheduleNextPoll(60);
}

// Automatically resume & fast-refresh whenever user switches back to this tab
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    triggerImmediatePoll();
  }
});

// Manual Refresh for Queue & Progress
async function manualRefreshQueue() {
  const btn = document.getElementById('btnQueueRefresh');
  if (btn) btn.classList.add('spinning');
  try {
    await fetchQueue();
    await fetchStats();
    showToast('Download queue updated in real-time!', 'info');
  } catch (err) {
    showToast('Failed to refresh queue', 'error');
  } finally {
    setTimeout(() => {
      if (btn) btn.classList.remove('spinning');
    }, 600);
  }
}

async function fetchQueue() {
  try {
    const uid = getUserId();
    const res = await fetch(`/api/queue?user_id=${encodeURIComponent(uid)}`);
    if (!res.ok) return;
    const data = await res.json();
    const serverTasks = data.tasks || [];

    // Retain any pending optimistic tasks (temp_...) until resolved
    const pendingOptimistic = allTasks.filter(t => String(t.id).startsWith('temp_') || String(t.id).startsWith('batch_'));
    const serverIds = new Set(serverTasks.map(t => String(t.id)));
    const filteredPending = pendingOptimistic.filter(ot => !serverIds.has(String(ot.id)));

    allTasks = [...filteredPending, ...serverTasks];
    renderQueue();
  } catch (err) {
    console.error('Failed to fetch queue:', err);
  }
}

async function fetchStats() {
  try {
    const uid = getUserId();
    const res = await fetch(`/api/stats?user_id=${encodeURIComponent(uid)}`);
    if (!res.ok) return;
    const stats = await res.json();
    const elActive = document.getElementById('statActive');
    const elQueue = document.getElementById('statQueue');
    const elCompleted = document.getElementById('statCompleted');
    const elDisk = document.getElementById('statDisk');
    const elBadge = document.getElementById('queueTotalBadge');

    if (elActive) elActive.textContent = stats.active_tasks ?? 0;
    if (elQueue) elQueue.textContent = stats.queue_length ?? 0;
    if (elCompleted) elCompleted.textContent = stats.completed_tasks ?? 0;
    if (elDisk) elDisk.textContent = stats.disk_usage ?? '0 MB';
    if (elBadge) elBadge.textContent = stats.total_tasks ?? 0;
  } catch (err) {
    console.error('Failed to fetch stats:', err);
  }
}

// --- Queue Filter ---
function setFilter(filter) {
  currentFilter = filter;
  document.querySelectorAll('.filter-pill').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toLowerCase().includes(filter));
  });
  renderQueue();
}

// --- Delete Single Task ---
async function deleteTask(taskId) {
  try {
    const card = document.getElementById(`card-${taskId}`);
    if (card) {
      card.style.opacity = '0.35';
      card.style.pointerEvents = 'none';
    }
    const uid = getUserId();
    const res = await fetch(`/api/queue/${taskId}?user_id=${encodeURIComponent(uid)}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Task removed from queue', 'info');
      allTasks = allTasks.filter(t => String(t.id) !== String(taskId));
      renderQueue();
      triggerImmediatePoll();
    }
  } catch (err) {
    showToast('Failed to delete task', 'error');
  }
}

// --- Clear Finished Queue ---
async function clearFinishedQueue() {
  try {
    const uid = getUserId();
    const res = await fetch(`/api/queue/clear?user_id=${encodeURIComponent(uid)}`, { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      showToast(`Cleared ${data.cleared_count || 0} finished tasks`, 'info');
      allTasks = allTasks.filter(t => !['completed', 'failed'].includes(t.status));
      renderQueue();
      triggerImmediatePoll();
    }
  } catch (err) {
    showToast('Failed to clear queue', 'error');
  }
}

// Helper: Status badge markup
function getStatusBadgeData(task) {
  const status = task.status || 'queued';
  const isDownloading = status === 'downloading';
  const isUploading = status === 'uploading';
  const isCompleted = status === 'completed';
  const isFailed = status === 'failed';
  const isProcessing = status === 'processing';

  let badgeClass = 'badge-queued';
  let badgeHtml = 'Queued';

  if (isDownloading) {
    badgeClass = 'badge-downloading';
    badgeHtml = '<span class="pill-dot blue pulse"></span> Downloading';
  } else if (isUploading) {
    badgeClass = 'badge-downloading';
    if (task.telegram_status === 'uploading') {
      badgeHtml = '<span class="pill-dot blue pulse"></span> Uploading to Telegram...';
    } else {
      badgeHtml = '<span class="pill-dot amber pulse"></span> Uploading to YouTube...';
    }
  } else if (isProcessing) {
    badgeClass = 'badge-processing';
    badgeHtml = '<span class="pill-dot purple pulse"></span> Merging / Encoding';
  } else if (isCompleted) {
    badgeClass = 'badge-completed';
    badgeHtml = '✓ Completed';
  } else if (isFailed) {
    badgeClass = 'badge-failed';
    badgeHtml = '✕ Failed';
  }

  return { badgeClass, badgeHtml };
}

// Helper: Build card action buttons
function buildTaskActionsHtml(task) {
  const isCompleted = task.status === 'completed';
  const taskId = escapeHtml(String(task.id));
  let html = '';

  if (task.youtube_url) {
    html += `
      <a href="${escapeHtml(task.youtube_url)}" target="_blank" rel="noopener noreferrer" class="btn-yt-watch" title="Watch on YouTube">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
        </svg>
        Watch on YouTube
      </a>
    `;
  } else if (isCompleted) {
    html += `
      <button class="btn-yt-watch" onclick="uploadTaskToYouTube('${taskId}')" title="Upload to YouTube">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
        </svg>
        Upload to YT
      </button>
    `;
  }

  if (task.telegram_url) {
    html += `
      <a href="${escapeHtml(task.telegram_url)}" target="_blank" rel="noopener noreferrer" class="btn-tg-watch" title="Watch on Telegram">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 0 0-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.75-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z"/>
        </svg>
        Watch on TG
      </a>
    `;
  } else if (isCompleted) {
    html += `
      <button class="btn-tg-watch" onclick="uploadTaskToTelegram('${taskId}')" title="Upload to Telegram">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 0 0-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.75-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z"/>
        </svg>
        Upload to TG
      </button>
    `;
  }

  if (isCompleted && task.download_url) {
    html += `
      <a href="${task.download_url}" class="btn-download-file" download="${escapeHtml(task.friendly_filename || 'video.mp4')}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
        Download File
      </a>
    `;
  }

  html += `
    <button class="btn-task-del" onclick="deleteTask('${taskId}')" title="Delete from list">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
    </button>
  `;

  return html;
}

// Helper: Build card inner HTML
function buildTaskCardInnerHtml(task) {
  const status = task.status || 'queued';
  const isDownloading = status === 'downloading';
  const isUploading = status === 'uploading';
  const isCompleted = status === 'completed';
  const isFailed = status === 'failed';

  const { badgeClass, badgeHtml } = getStatusBadgeData(task);

  const progressPercent = Math.max(0, Math.min(100, Math.round((task.progress || 0) * 10) / 10));
  const speed = task.speed_str || '--';
  const eta = task.eta_str || '--';
  const filesize = task.filesize_str || '--';
  const resText = String(task.resolution || '720').toUpperCase();
  const displayTitle = task.display_title || task.custom_name || task.url || 'Video';

  let etaHtml = '';
  if (isDownloading) {
    etaHtml = `<span>ETA: ${escapeHtml(eta)}</span>`;
  } else if (isFailed) {
    etaHtml = `<span style="color: var(--accent-rose);">${escapeHtml(task.error_message || 'Download Error')}</span>`;
  } else if (task.youtube_status && !task.youtube_url) {
    etaHtml = `<span style="color: #fbbf24;">${escapeHtml(task.youtube_status)}</span>`;
  } else if (task.telegram_status && !task.telegram_url) {
    etaHtml = `<span style="color: #38bdf8;">${escapeHtml(task.telegram_status)}</span>`;
  }

  const speedSpan = (isDownloading || isUploading) ? `<span>• ${escapeHtml(speed)}</span>` : '';

  return `
    <div class="task-top">
      <div class="task-info">
        <h4 class="task-title" title="${escapeHtml(displayTitle)}">
          ${escapeHtml(displayTitle)}
        </h4>
        <div class="task-meta">
          <span class="badge-res">${escapeHtml(resText)}</span>
          ${task.media_type && task.media_type !== 'Standard' ? `<span class="badge-drm">⚡ ${escapeHtml(task.media_type)}</span>` : ''}
          ${task.upload_to_youtube ? `<span class="badge-drm" style="color:#ff6666; border-color:rgba(255,0,0,0.35); background:rgba(255,0,0,0.12);">🔴 YT (${escapeHtml(task.privacy_status || 'unlisted')})</span>` : ''}
          ${task.upload_to_telegram ? `<span class="badge-drm" style="color:#38bdf8; border-color:rgba(0,136,204,0.35); background:rgba(0,136,204,0.12);">✈ TG (${escapeHtml(task.tg_chat_id || 'Chat')})</span>` : ''}
          <span class="task-meta-size">Size: ${escapeHtml(filesize)}</span>
        </div>
      </div>
      <div class="status-badge ${badgeClass}">
        ${badgeHtml}
      </div>
    </div>

    <div class="task-progress-section">
      <div class="progress-track">
        <div class="progress-fill ${isCompleted ? 'done' : ''}" style="width: ${progressPercent}%;"></div>
      </div>
      <div class="task-stats">
        <div class="stat-item stat-percent-wrap">
          <span>${progressPercent}%</span>${speedSpan}
        </div>
        <div class="stat-item stat-eta-wrap">
          ${etaHtml}
        </div>
      </div>
    </div>

    <div class="task-bottom">
      <span class="task-url" title="${escapeHtml(task.url || '')}">${escapeHtml(task.url || '')}</span>
      <div class="task-actions">
        ${buildTaskActionsHtml(task)}
      </div>
    </div>
  `;
}

// Create new card DOM element
function createCardElement(task) {
  const card = document.createElement('div');
  const taskId = String(task.id);
  card.id = `card-${taskId}`;
  card.dataset.taskId = taskId;
  card.className = `task-card status-${escapeHtml(task.status || 'queued')}`;
  card.innerHTML = buildTaskCardInnerHtml(task);
  return card;
}

// In-place card updater (ZERO FLICKER, ZERO PAGE REBUILDS)
function updateCardElement(card, task) {
  const status = task.status || 'queued';
  const isDownloading = status === 'downloading';
  const isUploading = status === 'uploading';
  const isCompleted = status === 'completed';
  const isFailed = status === 'failed';

  // Update card class
  const expectedClass = `task-card status-${status}`;
  if (card.className !== expectedClass) {
    card.className = expectedClass;
  }

  // Update status badge
  const { badgeClass, badgeHtml } = getStatusBadgeData(task);
  const badgeEl = card.querySelector('.status-badge');
  if (badgeEl) {
    badgeEl.className = `status-badge ${badgeClass}`;
    if (badgeEl.innerHTML !== badgeHtml) {
      badgeEl.innerHTML = badgeHtml;
    }
  }

  // Smooth live progress bar update
  const progressPercent = Math.max(0, Math.min(100, Math.round((task.progress || 0) * 10) / 10));
  const fill = card.querySelector('.progress-fill');
  if (fill) {
    fill.style.width = `${progressPercent}%`;
    fill.classList.toggle('done', isCompleted);
  }

  // Stats: % and Speed
  const speed = task.speed_str || '--';
  const speedSpan = (isDownloading || isUploading) ? `<span>• ${escapeHtml(speed)}</span>` : '';
  const statPercent = card.querySelector('.stat-percent-wrap');
  if (statPercent) {
    statPercent.innerHTML = `<span>${progressPercent}%</span>${speedSpan}`;
  }

  // Stats: ETA / Error / YouTube / Telegram
  const eta = task.eta_str || '--';
  const statEta = card.querySelector('.stat-eta-wrap');
  if (statEta) {
    let etaHtml = '';
    if (isDownloading) {
      etaHtml = `<span>ETA: ${escapeHtml(eta)}</span>`;
    } else if (isFailed) {
      etaHtml = `<span style="color: var(--accent-rose);">${escapeHtml(task.error_message || 'Download Error')}</span>`;
    } else if (task.youtube_status && !task.youtube_url) {
      etaHtml = `<span style="color: #fbbf24;">${escapeHtml(task.youtube_status)}</span>`;
    } else if (task.telegram_status && !task.telegram_url) {
      etaHtml = `<span style="color: #38bdf8;">${escapeHtml(task.telegram_status)}</span>`;
    }
    statEta.innerHTML = etaHtml;
  }

  // Filesize
  const sizeEl = card.querySelector('.task-meta-size');
  if (sizeEl) {
    sizeEl.textContent = `Size: ${task.filesize_str || '--'}`;
  }

  // Action buttons
  const actionsContainer = card.querySelector('.task-actions');
  if (actionsContainer && (isCompleted || isFailed || task.youtube_url || task.telegram_url)) {
    actionsContainer.innerHTML = buildTaskActionsHtml(task);
  }
}

// --- True Real-Time Incremental Queue Renderer ---
function renderQueue() {
  const container = document.getElementById('queueList');
  const emptyState = document.getElementById('emptyState');
  if (!container) return;

  // Filter tasks
  let filtered = allTasks;
  if (currentFilter === 'active') {
    filtered = allTasks.filter(t => ['queued', 'downloading', 'processing', 'uploading'].includes(t.status));
  } else if (currentFilter === 'completed') {
    filtered = allTasks.filter(t => t.status === 'completed');
  } else if (currentFilter === 'failed') {
    filtered = allTasks.filter(t => t.status === 'failed');
  }

  if (filtered.length === 0) {
    container.querySelectorAll('.task-card').forEach(c => c.remove());
    if (emptyState) {
      if (!container.contains(emptyState)) {
        container.appendChild(emptyState);
      }
      emptyState.style.display = 'flex';
    }
    return;
  }

  if (emptyState && container.contains(emptyState)) {
    emptyState.style.display = 'none';
  }

  const activeTaskIds = new Set(filtered.map(t => String(t.id)));

  // Remove cards that are no longer in this filtered list
  container.querySelectorAll('.task-card').forEach(card => {
    const cardId = card.dataset.taskId;
    if (cardId && !activeTaskIds.has(cardId)) {
      card.remove();
    }
  });

  // Incrementally insert or update cards in-place
  filtered.forEach((task, index) => {
    const taskId = String(task.id);
    let card = document.getElementById(`card-${taskId}`);

    if (!card) {
      // Create new card and insert at the exact index
      card = createCardElement(task);
      const existingCards = container.querySelectorAll('.task-card');
      if (index < existingCards.length) {
        container.insertBefore(card, existingCards[index]);
      } else {
        container.appendChild(card);
      }
    } else {
      // In-place live update: zero flicker, glides smoothly!
      updateCardElement(card, task);
    }
  });
}

// --- Toast Alerts ---
function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  let icon = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="12" y1="16" x2="12" y2="12"></line>
      <line x1="12" y1="8" x2="12.01" y2="8"></line>
    </svg>
  `;
  if (type === 'success') {
    icon = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent-emerald)" stroke-width="2">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
        <polyline points="22 4 12 14.01 9 11.01"></polyline>
      </svg>
    `;
  } else if (type === 'error') {
    icon = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent-rose)" stroke-width="2">
        <circle cx="12" cy="12" r="10"></circle>
        <line x1="15" y1="9" x2="9" y2="15"></line>
        <line x1="9" y1="9" x2="15" y2="15"></line>
      </svg>
    `;
  }

  toast.innerHTML = `${icon} <span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(30px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3200);
}

// Utility: Escape HTML
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

async function uploadTaskToYouTube(taskId) {
  try {
    showToast('Starting YouTube upload...', 'info');
    const res = await fetch(`/api/youtube/upload/${taskId}`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast('YouTube upload started in background!', 'success');
      fetchQueue();
    } else {
      showToast(data.detail || 'Upload failed to start.', 'error');
    }
  } catch (e) {
    showToast('Failed to connect to server.', 'error');
  }
}

async function uploadTaskToTelegram(taskId) {
  const cfg = getTelegramConfig();
  if (!cfg.bot_token || !cfg.api_id || !cfg.api_hash) {
    showToast('Please configure Telegram credentials first!', 'error');
    openTgModal();
    return;
  }

  const targetChat = prompt('Enter Telegram Channel/Chat ID (@username or -100...):', cfg.default_chat || '');
  if (!targetChat) return;

  try {
    showToast('Starting Telegram upload...', 'info');
    const res = await fetch(`/api/telegram/upload/${taskId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bot_token: cfg.bot_token,
        api_id: cfg.api_id,
        api_hash: cfg.api_hash,
        chat_id: targetChat.trim()
      })
    });
    const data = await res.json();
    if (res.ok) {
      showToast('Telegram upload started in background!', 'success');
      fetchQueue();
    } else {
      showToast(data.detail || 'Upload failed to start.', 'error');
    }
  } catch (e) {
    showToast('Failed to connect to server.', 'error');
  }
}

async function cleanDiskStorage() {
  if (!confirm("Are you sure you want to clean your disk storage? Your downloaded video files will be permanently deleted to free space.")) {
    return;
  }
  try {
    showToast('Cleaning your downloaded files...', 'info');
    const uid = getUserId();
    const lk = localStorage.getItem('omni_license_key') || '';
    const did = getDeviceId();
    const res = await fetch(`/api/storage/clean?user_id=${encodeURIComponent(uid)}&license_key=${encodeURIComponent(lk)}&device_id=${encodeURIComponent(did)}`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`Cleaned! Freed ${data.freed_space} (${data.deleted_files} files deleted)`, 'success');
      await fetchStats();
      await fetchQueue();
    } else {
      showToast(data.detail || 'Failed to clean disk storage.', 'error');
    }
  } catch (err) {
    showToast('Network error while cleaning storage.', 'error');
  }
}
