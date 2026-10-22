import os
import re
import uuid
import time
import asyncio
import logging
import shutil
import subprocess
from typing import List, Optional, Dict, Any
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote
from concurrent.futures import ThreadPoolExecutor

import glob
from xml.etree import ElementTree as ET

import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

import yt_dlp

try:
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device
    from pywidevine.pssh import PSSH
    WIDEVINE_ENABLED = True
except ImportError:
    WIDEVINE_ENABLED = False

from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("media_downloader")

# Base paths
import sys
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
DOWNLOADS_DIR = BASE_DIR / "downloads"
STATIC_DIR = BASE_DIR / "static"
if not (STATIC_DIR / "index.html").exists():
    if (BASE_DIR / "website" / "static" / "index.html").exists():
        STATIC_DIR = BASE_DIR / "website" / "static"
    elif (BASE_DIR.parent / "website" / "static" / "index.html").exists():
        STATIC_DIR = BASE_DIR.parent / "website" / "static"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# ClassPlus Access Token (Can be overridden via env CP_TOKEN or token_cp)
CP_TOKEN = os.environ.get(
    "CP_TOKEN", 
    os.environ.get("token_cp", "eyJjb3Vyc2VJZCI6IjQ1NjY4NyIsInR1dG9ySWQiOm51bGwsIm9yZ0lkIjo0ODA2MTksImNhdGVnb3J5SWQiOm51bGx9r")
)

# Optional YouTube cookies path
COOKIES_FILE = os.environ.get(
    "COOKIES_FILE", 
    str(BASE_DIR.parent / "Banana_bot--main" / "youtube_cookies.txt")
)

# Cloudflare Auth & Licensing Server
CLOUDFLARE_AUTH_API = os.environ.get("CLOUDFLARE_AUTH_API", "").rstrip("/")
if not CLOUDFLARE_AUTH_API and (BASE_DIR / "cloudflare_api.txt").exists():
    try:
        CLOUDFLARE_AUTH_API = (BASE_DIR / "cloudflare_api.txt").read_text().strip().rstrip("/")
    except Exception:
        pass
if not CLOUDFLARE_AUTH_API:
    CLOUDFLARE_AUTH_API = "https://yt-uplode.mypresonal65.workers.dev"

# License In-memory Cache & Helper
license_cache: Dict[str, Dict[str, Any]] = {}

def verify_license_permission(license_key: Optional[str], device_id: Optional[str], permission_name: str) -> bool:
    """License restriction removed: all features are permanently permitted and free."""
    return True

# In-memory Task Store and Queue
tasks_lock = asyncio.Lock()
tasks_db: Dict[str, Dict[str, Any]] = {}
download_queue: asyncio.Queue = asyncio.Queue()
MAX_CONCURRENT_WORKERS = 3
thread_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS)

async def auto_clean_downloads_loop():
    """
    Background worker running every 60 seconds.
    Automatically deletes files from DOWNLOADS_DIR:
    1) If uploaded to YouTube: deleted exactly 1 hour (3600s) after upload completes.
    2) If not uploaded: deleted 1 hour after download completes / file creation.
    Never deletes files while upload is actively in progress.
    Keeps the YouTube watch link & task entry intact in UI while reclaiming 100% disk space.
    """
    ONE_HOUR = 3600
    while True:
        try:
            await asyncio.sleep(60)  # Check every 60 seconds
            now = time.time()
            deleted_count = 0

            # Map filename and path to task
            file_to_task = {}
            for t in tasks_db.values():
                fn = t.get("resolved_filename")
                fp = t.get("file_path")
                if fn:
                    file_to_task[fn] = t
                if fp:
                    file_to_task[Path(fp).name] = t

            for item in DOWNLOADS_DIR.glob("*"):
                if item.name == ".gitkeep":
                    continue
                if item.is_file():
                    fname = item.name
                    task = file_to_task.get(fname)

                    # SAFETY CHECK: Never delete if currently uploading
                    if task and (
                        task.get("status") == "uploading" or 
                        task.get("youtube_status") == "uploading" or 
                        task.get("telegram_status") == "uploading"
                    ):
                        continue

                    should_delete = False
                    reason = ""

                    # Case 1: Uploaded to YouTube or Telegram -> Wait 1 hour after latest upload completion
                    upload_times = [t for t in [task.get("uploaded_at"), task.get("uploaded_to_tg_at")] if t] if task else []
                    if upload_times:
                        time_since_upload = now - max(upload_times)
                        if time_since_upload >= ONE_HOUR:
                            should_delete = True
                            reason = "1 hr after upload"
                    # Case 2: General download older than 1 hour
                    else:
                        age = now - item.stat().st_mtime
                        if age >= ONE_HOUR:
                            should_delete = True
                            reason = "older than 1 hr"

                    if should_delete:
                        try:
                            item.unlink(missing_ok=True)
                            deleted_count += 1
                            logger.info(f"🧹 Auto-cleaned video from disk ({reason}): {fname}")
                            if task:
                                task["file_path"] = None
                                task["download_url"] = None
                                task["filesize_str"] = f"Auto-cleaned ({reason})"
                        except Exception as e:
                            logger.warning(f"Failed to auto-delete {fname}: {e}")

            if deleted_count > 0:
                logger.info(f"🧹 Auto-cleanup finished: Reclaimed space from {deleted_count} video(s).")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in auto_clean_downloads_loop: {e}")



@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_tasks = [asyncio.create_task(queue_worker_loop(f"worker-{i+1}")) for i in range(MAX_CONCURRENT_WORKERS)]
    cleaner_task = asyncio.create_task(auto_clean_downloads_loop())
    logger.info(f"Universal Web Video Downloader with {MAX_CONCURRENT_WORKERS} concurrent workers & 1-hour auto-cleanup loop initialized.")
    yield
    for wt in worker_tasks:
        wt.cancel()
    cleaner_task.cancel()

app = FastAPI(
    title="Universal Web Video Downloader",
    description="Clean, queue-based video & media downloader API with ClassPlus and Akamai DRM support",
    version="1.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



def sanitize_filename(name: str) -> str:
    """Sanitize string to be safe for filenames across Windows and Linux."""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.strip()
    return name if name else "video"


def format_bytes(bytes_count: Optional[float]) -> str:
    """Format bytes into readable string."""
    if not bytes_count or bytes_count <= 0:
        return "N/A"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_count < 1024.0:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.1f} PB"


def format_seconds(seconds: Optional[float]) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# YouTube Uploader integration
try:
    from youtube_uploader import upload_video_to_youtube, get_youtube_service
    YOUTUBE_UPLOADER_AVAILABLE = True
except Exception as e:
    logger.warning(f"YouTube uploader module not available: {e}")
    YOUTUBE_UPLOADER_AVAILABLE = False


# Telegram Uploader integration (Pyrofork MTProto up to 2GB)
TG_IMPORT_ERROR = None
try:
    from telegram_uploader import (
        upload_video_to_telegram,
        validate_telegram_credentials,
        PYROGRAM_AVAILABLE as TELEGRAM_UPLOADER_AVAILABLE,
        PYROGRAM_IMPORT_ERROR
    )
    if not TELEGRAM_UPLOADER_AVAILABLE:
        TG_IMPORT_ERROR = PYROGRAM_IMPORT_ERROR or "Pyrofork/Pyrogram library missing"
except Exception as e:
    logger.warning(f"Telegram uploader module not available: {e}")
    TELEGRAM_UPLOADER_AVAILABLE = False
    TG_IMPORT_ERROR = str(e)



# --- Models ---
class SingleDownloadRequest(BaseModel):
    url: str
    custom_name: Optional[str] = None
    resolution: str = "720"  # 'best', '1080', '720', '480', '360', 'audio'
    upload_to_youtube: bool = False
    privacy_status: str = "unlisted"  # 'unlisted', 'private', 'public'
    user_id: Optional[str] = None
    upload_to_telegram: bool = False
    tg_bot_token: Optional[str] = None
    tg_api_id: Optional[str] = None
    tg_api_hash: Optional[str] = None
    tg_chat_id: Optional[str] = None
    keys: Optional[List[str]] = None
    custom_referer: Optional[str] = None
    custom_origin: Optional[str] = None
    token: Optional[str] = None
    threads: Optional[int] = 16
    license_key: Optional[str] = None
    device_id: Optional[str] = None


class BatchItemRequest(BaseModel):
    url: str
    custom_name: Optional[str] = None
    resolution: Optional[str] = "720"
    upload_to_youtube: bool = False
    privacy_status: Optional[str] = "unlisted"
    user_id: Optional[str] = None
    upload_to_telegram: bool = False
    tg_bot_token: Optional[str] = None
    tg_api_id: Optional[str] = None
    tg_api_hash: Optional[str] = None
    tg_chat_id: Optional[str] = None
    custom_referer: Optional[str] = None
    custom_origin: Optional[str] = None
    token: Optional[str] = None
    threads: Optional[int] = 16


class BatchDownloadRequest(BaseModel):
    items: Optional[List[BatchItemRequest]] = None
    raw_text: Optional[str] = None
    default_resolution: str = "720"
    upload_to_youtube: bool = False
    default_privacy: str = "unlisted"
    user_id: Optional[str] = None
    upload_to_telegram: bool = False
    tg_bot_token: Optional[str] = None
    tg_api_id: Optional[str] = None
    tg_api_hash: Optional[str] = None
    tg_chat_id: Optional[str] = None
    default_referer: Optional[str] = None
    default_origin: Optional[str] = None
    default_token: Optional[str] = None
    default_threads: Optional[int] = 16
    license_key: Optional[str] = None
    device_id: Optional[str] = None


class TelegramManualUploadRequest(BaseModel):
    bot_token: Optional[str] = None
    api_id: Optional[str] = None
    api_hash: Optional[str] = None
    chat_id: Optional[str] = None



# --- DRM & ClassPlus URL Resolver Helpers ---
def extract_keys_from_mpd(mpd_content: str) -> List[str]:
    """Extract decryption keys from MPD content."""
    keys = []
    try:
        kid_pattern = r'default_KID="([^"]+)"'
        kid_matches = re.findall(kid_pattern, mpd_content)
        for kid in kid_matches:
            clean_kid = kid.replace('-', '').lower()
            keys.append(f"{clean_kid}:00000000000000000000000000000000")
        return keys
    except Exception as e:
        logger.warning(f"Error extracting keys from MPD: {e}")
        return []


def get_mps_and_keys(api_url: str) -> tuple:
    """
    Fetch MPD and keys from ClassPlus API or direct manifest.
    Supports both L1 (key + userIds) and L2 (hdnts only) links.
    """
    try:
        if api_url.endswith('.mpd') or '/manifest' in api_url:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/dash+xml,application/xml,text/xml,*/*'
            }
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            mpd_content = response.text
            keys = extract_keys_from_mpd(mpd_content)
            return api_url, keys

        response = requests.get(api_url, timeout=30)
        response_json = response.json()
        mpd = response_json.get('MPD') or api_url
        keys = response_json.get('KEYS') or []

        if not keys and isinstance(mpd, str) and ('<MPD' in mpd or mpd.endswith('.mpd')):
            if '<MPD' in mpd:
                keys = extract_keys_from_mpd(mpd)
            else:
                _, keys = get_mps_and_keys(mpd)

        return mpd, keys
    except Exception as e:
        logger.warning(f"Error in get_mps_and_keys: {e}")
        return api_url, []


def parse_m3u8_variant(master_url: str, m3u8_text: str, target_res: Optional[str] = None) -> Optional[str]:
    """
    Parses multi-variant HLS master playlist (master.m3u8) to select the best stream
    matching the target resolution (or highest quality), preserving Akamai tokens (hdnts/hdntl/key).
    """
    try:
        lines = [line.strip() for line in m3u8_text.splitlines() if line.strip()]
        variants = []
        current_inf = None
        for line in lines:
            if line.startswith("#EXT-X-STREAM-INF:"):
                current_inf = line
            elif current_inf and not line.startswith("#"):
                m_res = re.search(r'RESOLUTION=\d+x(\d+)', current_inf)
                m_bw = re.search(r'BANDWIDTH=(\d+)', current_inf)
                height = int(m_res.group(1)) if m_res else 0
                bandwidth = int(m_bw.group(1)) if m_bw else 0

                full_uri = urljoin(master_url, line)
                parsed_master = urlparse(master_url)
                parsed_variant = urlparse(full_uri)

                # Ensure query string from master_url (hdnts, key, userIds) is retained
                if parsed_master.query and not parsed_variant.query:
                    full_uri = f"{full_uri}?{parsed_master.query}"
                elif parsed_master.query and parsed_variant.query and parsed_master.query not in parsed_variant.query:
                    full_uri = f"{full_uri}&{parsed_master.query}"

                variants.append({
                    "height": height,
                    "bandwidth": bandwidth,
                    "url": full_uri
                })
                current_inf = None

        if not variants:
            return None

        # Filter by requested resolution
        if target_res and str(target_res).lower() != "best":
            try:
                target_h = int(target_res)
                matching = [v for v in variants if v["height"] <= target_h and v["height"] > 0]
                if matching:
                    matching.sort(key=lambda x: (x["height"], x["bandwidth"]), reverse=True)
                    return matching[0]["url"]
            except (ValueError, TypeError):
                pass

        # Default to highest available
        variants.sort(key=lambda x: (x["height"], x["bandwidth"]), reverse=True)
        return variants[0]["url"]
    except Exception as e:
        logger.debug(f"Error parsing m3u8 variants: {e}")
        return None


def extract_youtube_video_id(url: str) -> Optional[str]:
    """
    Extract 11-char YouTube ID from standard YouTube URLs, SaveTube wrappers,
    videoplayback links, or shorts wrappers.
    """
    if not url:
        return None
    # 1. Standard YouTube URL patterns
    m1 = re.search(r'(?:v=|/v/|/embed/|/shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    if m1:
        return m1.group(1)
    # 2. Third-party wrapper /media/{id} (e.g. SaveTube)
    m2 = re.search(r'/media/([a-zA-Z0-9_-]{11})(?:/|\?|$)', url)
    if m2:
        return m2.group(1)
    # 3. Direct videoplayback stream tokens with &id=o-{id}
    m3 = re.search(r'[?&]id=o-([a-zA-Z0-9_-]{11})', url)
    if m3:
        return m3.group(1)
    return None


def resolve_media_url(
    url: str, 
    requested_resolution: Optional[str] = None,
    custom_referer: Optional[str] = None,
    custom_origin: Optional[str] = None,
    token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Resolves ClassPlus, Akamai (L1/L2), ClassX (Google Edge Cache), Testbook, CloudFront Live Classes, and DRM URLs before downloading.
    """
    base_url = url.strip()
    resolved_url = base_url
    keys_string = ""
    extra_headers: Dict[str, str] = {}
    media_type = "Standard"

    # 0. SaveTube / Third-party YouTube wrappers / Raw videoplayback stream tokens
    # When users copy links from SaveTube or third-party tools, the stream token is IP-locked to their phone (IPv6).
    # Requesting that token on Heroku results in "HTTP Error 403: Forbidden".
    # We automatically extract the underlying 11-char YouTube ID so yt-dlp gets a fresh direct stream from YouTube!
    yt_id = extract_youtube_video_id(resolved_url)
    if yt_id and any(k in resolved_url.lower() for k in ["savetube", "googlevideo.com", "videoplayback", "y2mate", "ytshorts", "savethevideo", "yt1s"]):
        resolved_url = f"https://www.youtube.com/watch?v={yt_id}"
        media_type = "YouTube (Extracted from Wrapper)"
        logger.info(f"✅ Auto-extracted YouTube Video ID '{yt_id}' from wrapper URL: {resolved_url}")

    # 0.1 Google Drive / Google UserContent support
    elif "drive.google.com" in resolved_url.lower() or "docs.google.com" in resolved_url.lower() or "googleusercontent.com" in resolved_url.lower():
        return {
            "url": resolved_url,
            "keys_string": "",
            "extra_headers": extra_headers,
            "media_type": "Google Drive / UserContent"
        }

    # 0.1 Mega.nz support
    if "mega.nz" in resolved_url.lower() or "mega.co.nz" in resolved_url.lower():
        return {
            "url": resolved_url,
            "keys_string": "",
            "extra_headers": extra_headers,
            "media_type": "Mega.nz"
        }

    # 1. VisionIAS support
    if "visionias" in resolved_url:
        try:
            headers = {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Referer': 'http://www.visionias.in/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            resp = requests.get(resolved_url, headers=headers, timeout=15)
            m = re.search(r'(https://.*?playlist\.m3u8.*?)["\']', resp.text)
            if m:
                resolved_url = m.group(1)
                media_type = "VisionIAS Stream"
        except Exception as e:
            logger.warning(f"VisionIAS resolve error: {e}")

    # 2. PW Live / Multiverse support
    elif "d1d34p8vz63oiq" in resolved_url or "sec1.pw.live" in resolved_url:
        resolved_url = f"https://download.asmultiverse.com?Vurl={resolved_url}"
        media_type = "PW Stream"

    # 3. Encrypted AppX support
    elif 'encrypted.m' in resolved_url:
        if '*' in resolved_url:
            resolved_url = resolved_url.split('*')[0]
        media_type = "AppX Encrypted"

    # 4. CloudFront / Live Classes / Timeshift HLS Stream (e.g. liveclasses.cloud-front.in)
    elif any(k in resolved_url.lower() for k in ["cloud-front.in", "liveclasses", "timeshift=1", "txcodectempname", "starttime_epoch"]):
        media_type = "CloudFront / Live Timeshift"
        try:
            parsed = urlparse(resolved_url)
            origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "https://liveclasses.cloud-front.in"
        except Exception:
            origin = "https://liveclasses.cloud-front.in"

        extra_headers['Referer'] = f"{origin}/"
        extra_headers['Origin'] = origin
        extra_headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        extra_headers['Accept'] = '*/*'
        extra_headers['Accept-Encoding'] = 'gzip, deflate, br'
        extra_headers['Sec-Fetch-Mode'] = 'cors'
        extra_headers['Sec-Fetch-Site'] = 'cross-site'
        extra_headers['Sec-Fetch-Dest'] = 'empty'

        # Adapt txCodecTempName to requested resolution if present
        if requested_resolution and "txCodecTempName=" in resolved_url:
            res_val = str(requested_resolution).strip().lower()
            if res_val in ["360", "480", "720", "1080"]:
                test_url = re.sub(r'txCodecTempName=[^&]+', f'txCodecTempName={res_val}p', resolved_url)
                try:
                    head_res = requests.head(test_url, headers=extra_headers, timeout=3, allow_redirects=True)
                    if head_res.status_code == 200:
                        resolved_url = test_url
                        logger.info(f"✅ Adapted stream resolution to {res_val}p")
                except Exception:
                    pass

    # ============================================================
    #  🔥 ClassX (Google Edge Cache Signed HLS Streams)
    # ============================================================
    elif any(k in resolved_url.lower() for k in ["classx.co.in", "transcoded-videos.classx"]):
        media_type = "ClassX Stream"
        
        # Check if token needs to be appended
        if token and token.strip() and "edge-cache-token=" not in resolved_url:
            tok = token.strip()
            if not tok.startswith("edge-cache-token="):
                tok = f"edge-cache-token={tok}"
            sep = "&" if "?" in resolved_url else "?"
            resolved_url = f"{resolved_url}{sep}{tok}"

        # Extract tenant name from path /videos/{tenant}-data/...
        m_tenant = re.search(r'/videos/([a-zA-Z0-9_\-]+?)(?:-data)?/', resolved_url)
        tenant_name = m_tenant.group(1) if m_tenant else "web"
        
        # Determine candidate referer & origin
        auto_referer = f"https://{tenant_name}.classx.co.in/"
        auto_origin = f"https://{tenant_name}.classx.co.in"
        
        final_referer = custom_referer.strip() if custom_referer and custom_referer.strip() else auto_referer
        final_origin = custom_origin.strip() if custom_origin and custom_origin.strip() else auto_origin

        extra_headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        extra_headers['Referer'] = final_referer
        extra_headers['Origin'] = final_origin
        extra_headers['Accept'] = '*/*'
        extra_headers['Accept-Encoding'] = 'gzip, deflate, br'
        extra_headers['Sec-Fetch-Mode'] = 'cors'
        extra_headers['Sec-Fetch-Site'] = 'cross-site'
        extra_headers['Sec-Fetch-Dest'] = 'empty'

        # Parse multi-variant if m3u8
        if ".m3u8" in resolved_url:
            logger.info("🎯 ClassX HLS stream detected, checking multi-variant resolution...")
            try:
                resp = requests.get(resolved_url, headers=extra_headers, timeout=6)
                if resp.status_code in [403, 404]:
                    # Fallback to transcoded-videos.classx.co.in if tenant subdomain was strictly restricted
                    fallback_headers = extra_headers.copy()
                    fallback_headers['Referer'] = "https://transcoded-videos.classx.co.in/"
                    fallback_headers['Origin'] = "https://transcoded-videos.classx.co.in"
                    fb_resp = requests.get(resolved_url, headers=fallback_headers, timeout=6)
                    if fb_resp.status_code == 200:
                        extra_headers.update(fallback_headers)
                        resp = fb_resp

                if resp.status_code == 200 and "#EXT-X-STREAM-INF" in resp.text:
                    variant_url = parse_m3u8_variant(resolved_url, resp.text, requested_resolution or "720")
                    if variant_url:
                        resolved_url = variant_url
                        logger.info(f"✅ Selected ClassX variant stream URL: {resolved_url[:80]}...")
            except Exception as e:
                logger.debug(f"ClassX m3u8 variant check skipped: {e}")

    # ============================================================
    #  🔥 FIXED: ClassPlus / Akamai Logic (L1 + L2 + Direct HLS)
    # ============================================================
    elif ('classplusapp' in resolved_url or "testbook.com" in resolved_url or 
          "classplusapp.com/drm" in resolved_url or "media-cdn.classplusapp.com/drm" in resolved_url or 
          "akamai-cdn.classplusapp.com" in resolved_url or "hdnts=" in resolved_url or "hdntl=" in resolved_url):
        
        media_type = "ClassPlus / Akamai"

        # Extract Akamai token (hdnts or hdntl)
        m_hdnts = re.search(r'[?&](hdnt[sl]=[^&]+)', resolved_url)
        hdnts_token = m_hdnts.group(1) if m_hdnts else ""

        # Check L1 / L2
        is_l1 = 'key=' in resolved_url and 'userIds=' in resolved_url
        is_l2 = bool(hdnts_token) and not is_l1

        if is_l1:
            logger.info("✅ L1 link detected (key + userIds) — preserving all parameters")
            media_type = "ClassPlus Akamai (L1)"
        elif is_l2:
            logger.info("✅ L2 Akamai HLS link detected (hdnts token)")
            media_type = "ClassPlus Akamai (L2)"
        else:
            logger.info("ℹ️ Standard / direct ClassPlus link")

        # Standard headers for ClassPlus & Akamai CDN EdgeAuth
        extra_headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        extra_headers['Referer'] = 'https://web.classplusapp.com/'
        extra_headers['Origin'] = 'https://web.classplusapp.com'
        extra_headers['Accept'] = '*/*'
        extra_headers['Accept-Encoding'] = 'gzip, deflate, br'
        extra_headers['Accept-Language'] = 'en-US,en;q=0.9'
        extra_headers['Sec-Fetch-Mode'] = 'cors'
        extra_headers['Sec-Fetch-Site'] = 'cross-site'
        extra_headers['Sec-Fetch-Dest'] = 'empty'
        extra_headers['x-cdn-tag'] = 'empty'
        if hdnts_token:
            extra_headers['Cookie'] = hdnts_token

        # Check if URL is ALREADY a direct Akamai/ClassPlus HLS stream (.m3u8)
        if ".m3u8" in resolved_url:
            logger.info("🎯 Direct Akamai / ClassPlus HLS stream detected, checking multi-variant resolution...")
            try:
                resp = requests.get(resolved_url, headers=extra_headers, timeout=5)
                if resp.status_code == 200 and "#EXT-X-STREAM-INF" in resp.text:
                    variant_url = parse_m3u8_variant(resolved_url, resp.text, requested_resolution or "720")
                    if variant_url:
                        resolved_url = variant_url
                        logger.info(f"✅ Selected variant stream URL: {resolved_url[:80]}...")
            except Exception as e:
                logger.debug(f"Direct m3u8 pre-resolve skipped: {e}")

            return {
                "url": resolved_url,
                "keys_string": "",
                "extra_headers": extra_headers,
                "media_type": media_type
            }

        # ---------- EXTRACT contentHashId / contentHashIdl ----------
        content_id = None
        hash_patterns = [
            r'contentHashId=([^&]+)',
            r'contentHashIdl=([^&]+)',
            r'[?&]contentHashId=([^&]+)',
            r'[?&]contentHashIdl=([^&]+)',
        ]
        for pattern in hash_patterns:
            match = re.search(pattern, resolved_url)
            if match:
                content_id = match.group(1).split('&')[0]
                break

        # Also try to get vidKey from URL path
        vidkey = None
        vid_patterns = [
            r'/lc/([^/]+)/',
            r'vidKey=([^&]+)',
        ]
        for pattern in vid_patterns:
            match = re.search(pattern, resolved_url)
            if match:
                vidkey = match.group(1)
                break

        logger.info(f"🔑 ClassPlus content_id: {content_id}")
        logger.info(f"🔑 ClassPlus vidkey: {vidkey}")

        # ---------- BUILD HEADERS ----------
        headers = {
            'host': 'api.classplusapp.com',
            'x-access-token': f'{CP_TOKEN}',    
            'accept-language': 'EN',
            'api-version': '18',
            'app-version': '1.4.73.2',
            'build-number': '35',
            'connection': 'Keep-Alive',
            'content-type': 'application/json',
            'device-details': 'Xiaomi_Redmi 7_SDK-32',
            'device-id': 'c28d3cb16bbdac01',
            'region': 'IN',
            'user-agent': 'Mobile-Android',
            'webengage-luid': '00000187-6fe4-5d41-a530-26186858be4c',
            'accept-encoding': 'gzip'
        }

        # ---------- USE content_id OR vidkey ----------
        api_content_id = content_id or vidkey

        if not api_content_id:
            # If no content_id found, try to extract from URL
            match = re.search(r'/([a-zA-Z0-9]+-[0-9]+[a-z]?)/', resolved_url)
            if match:
                api_content_id = match.group(1)

        if api_content_id:
            params = {
                'contentId': api_content_id,
                'offlineDownload': "false"
            }

            try:
                res = requests.get(
                    "https://api.classplusapp.com/cams/uploader/video/jw-signed-url",
                    params=params,
                    headers=headers,
                    timeout=15
                ).json()

                logger.info(f"📦 ClassPlus API Response: {res}")

                # Check if it's a DRM URL
                if ("testbook.com" in base_url or "classplusapp.com/drm" in base_url or 
                    "media-cdn.classplusapp.com/drm" in base_url or '/drm/' in base_url or
                    "akamai-cdn.classplusapp.com" in base_url):
                    if 'drmUrls' in res:
                        drm_urls = res.get('drmUrls') or {}
                        mpd_url = drm_urls.get('manifestUrl') or drm_urls.get('mpdUrl')
                        lic_url = drm_urls.get('licenseUrl') or drm_urls.get('licUrl')
                        if mpd_url:
                            resolved_url = mpd_url
                            keys = []
                            if lic_url:
                                pssh = extract_pssh(mpd_url)
                                if pssh:
                                    keys = extract_widevine_keys(pssh, lic_url)
                            keys_string = " ".join([f"--key {k}" for k in keys]) if keys else ""
                            media_type = "ClassPlus DRM (MPD)"
                        else:
                            resolved_url = res.get("url", base_url)
                            keys_string = ""
                    else:
                        resolved_url = res.get("url", base_url)
                        keys_string = ""
                else:
                    resolved_url = res.get("url", base_url)
                    keys_string = ""

                logger.info(f"✅ ClassPlus API Success - contentId: {api_content_id[:30]}...")

            except Exception as e:
                logger.warning(f"⚠️ ClassPlus API Error: {e}")
                # Keep original URL if API fails — L1 and L2 both preserved
                resolved_url = base_url
                keys_string = ""
        else:
            logger.info("⚠️ No contentId or vidkey found, using original URL")
            resolved_url = base_url
            keys_string = ""

    # Extra headers for ClassPlus & Akamai CDNs (mimics browser extension perfectly)
    if any(k in resolved_url.lower() for k in ["classplusapp", "akamai", "media-cdn", "testbook"]):
        extra_headers['Referer'] = 'https://web.classplusapp.com/'
        extra_headers['Origin'] = 'https://web.classplusapp.com'
        extra_headers['x-cdn-tag'] = 'empty'
        extra_headers['Sec-Fetch-Site'] = 'cross-site'
        extra_headers['Sec-Fetch-Mode'] = 'cors'
        extra_headers['Sec-Fetch-Dest'] = 'empty'

    # Apply explicit custom overrides if provided by user
    if custom_referer and custom_referer.strip():
        extra_headers['Referer'] = custom_referer.strip()
    if custom_origin and custom_origin.strip():
        extra_headers['Origin'] = custom_origin.strip()
    if token and token.strip() and "edge-cache-token=" not in resolved_url and "token=" not in resolved_url:
        tok = token.strip()
        sep = "&" if "?" in resolved_url else "?"
        resolved_url = f"{resolved_url}{sep}{tok}"

    return {
        "url": resolved_url,
        "keys_string": keys_string,
        "extra_headers": extra_headers,
        "media_type": media_type
    }


def download_google_drive(gdrive_url: str, dest_path: Path, task_dict: dict) -> Path:
    """Download Google Drive file using gdown or direct requests session with confirmation token."""
    m = re.search(r'(?:file/d/|id=|uc\?id=|file/d/e/)([-_\w]{25,})', gdrive_url)
    file_id = m.group(1) if m else gdrive_url

    # 1. Try gdown first
    try:
        import gdown
        task_dict["speed_str"] = "Downloading from Google Drive..."
        task_dict["progress"] = 10.0
        output_file = str(dest_path)
        url = f"https://drive.google.com/uc?id={file_id}"
        logger.info(f"Downloading Google Drive file via gdown: {file_id}")
        res = gdown.download(url=url, output=output_file, quiet=True, fuzzy=True)
        if res and Path(res).exists() and Path(res).stat().st_size > 0:
            p = Path(res)
            if not p.name.startswith(f"{task_dict['id']}_"):
                renamed = p.with_name(f"{task_dict['id']}_{p.name}")
                p.rename(renamed)
                p = renamed
            task_dict["progress"] = 99.0
            return p
    except Exception as e:
        logger.warning(f"gdown error: {e}, attempting direct session stream...")

    # 2. Fallback: direct session with confirm token
    session = requests.Session()
    url = "https://docs.google.com/uc?export=download"
    r = session.get(url, params={'id': file_id}, stream=True, timeout=25)

    token = None
    for k, v in r.cookies.items():
        if k.startswith('download_warning'):
            token = v
            break
    if not token:
        m_confirm = re.search(r'confirm=([0-9A-Za-z_]+)', r.text)
        if m_confirm:
            token = m_confirm.group(1)
    if token:
        r = session.get(url, params={'id': file_id, 'confirm': token}, stream=True, timeout=30)

    cd = r.headers.get("content-disposition", "")
    fname_match = re.search(r'filename="?([^";]+)"?', cd)
    if fname_match:
        real_name = sanitize_filename(fname_match.group(1))
        dest_path = dest_path.with_name(f"{task_dict['id']}_{real_name}")

    total_size = int(r.headers.get('content-length', 0))
    downloaded = 0
    start_t = time.time()

    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if task_dict.get("cancelled"):
                raise Exception("Download cancelled by user.")
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                elapsed = max(0.1, time.time() - start_t)
                speed = downloaded / elapsed
                task_dict["speed_str"] = f"{format_bytes(speed)}/s"
                if total_size > 0:
                    task_dict["progress"] = round((downloaded / total_size) * 100, 1)
                    task_dict["filesize_str"] = format_bytes(total_size)
                    remaining = max(0, total_size - downloaded)
                    task_dict["eta_str"] = format_seconds(remaining / max(1, speed))
                else:
                    task_dict["progress"] = 50.0
                    task_dict["filesize_str"] = format_bytes(downloaded)

    return dest_path


def download_mega(mega_url: str, dest_dir: Path, task_dict: dict, custom_name: Optional[str] = None) -> Path:
    """Download Mega.nz file using mega.py with renaming and progress tracking."""
    task_dict["speed_str"] = "Downloading from Mega.nz..."
    task_dict["progress"] = 10.0
    from mega import Mega
    mega = Mega()
    m = mega.login()
    logger.info(f"Downloading Mega file: {mega_url}")
    res_path = m.download_url(mega_url, dest_path=str(dest_dir))
    if res_path and Path(res_path).exists():
        p = Path(res_path)
        task_dict["progress"] = 99.0
        ext = p.suffix or ".mp4"
        if custom_name and custom_name.strip():
            safe_name = sanitize_filename(custom_name.strip())
            final_p = p.with_name(f"{task_dict['id']}_{safe_name}{ext}")
        else:
            final_p = p.with_name(f"{task_dict['id']}_{p.name}")
        if p != final_p:
            p.rename(final_p)
        return final_p
    raise Exception("Mega.nz download returned empty file")


def finalize_download_task(task_id: str, keys_string: str = ""):
    """Post-processes downloaded file, decrypts DRM if needed, handles auto-upload to YouTube & Telegram, and updates status."""
    task = tasks_db.get(task_id)
    if not task:
        return

    # Decrypt DRM if keys_string exists and mp4decrypt is available
    if keys_string and '--key' in keys_string:
        mp4decrypt_bin = shutil.which("mp4decrypt")
        if mp4decrypt_bin:
            task["status"] = "processing"
            task["speed_str"] = "Decrypting DRM..."
            task_files = list(DOWNLOADS_DIR.glob(f"{task_id}_*"))
            for tf in task_files:
                if tf.suffix in [".mp4", ".m4a"]:
                    dec_file = tf.with_name(f"dec_{tf.name}")
                    decrypt_cmd = f'"{mp4decrypt_bin}" {keys_string} "{tf}" "{dec_file}"'
                    logger.info(f"Decrypting with mp4decrypt: {decrypt_cmd}")
                    subprocess.run(decrypt_cmd, shell=True)
                    if dec_file.exists() and dec_file.stat().st_size > 0:
                        tf.unlink(missing_ok=True)
                        dec_file.rename(tf)
        else:
            logger.info(f"DRM keys found ({keys_string}) but mp4decrypt not installed in PATH.")

    # Find the final file on disk
    candidates = list(DOWNLOADS_DIR.glob(f"{task_id}_*"))
    if candidates:
        final_file = candidates[0]
        task["file_path"] = str(final_file)
        task["resolved_filename"] = final_file.name
        friendly_name = final_file.name.replace(f"{task_id}_", "")
        task["friendly_filename"] = friendly_name
        task["filesize_str"] = format_bytes(final_file.stat().st_size)
        task["download_url"] = f"/api/files/{task_id}"

        # Clean display title if currently a raw URL or empty
        if not task.get("custom_name") and (not task.get("display_title") or str(task.get("display_title")).startswith("http")):
            task["display_title"] = Path(friendly_name).stem

        # Auto-upload to YouTube if enabled
        if task.get("upload_to_youtube"):
            task["status"] = "uploading"
            task["speed_str"] = "Uploading to YouTube..."
            task["progress"] = 0.0
            logger.info(f"Task {task_id}: Starting auto-upload to YouTube...")

            def yt_progress(pct, spd=""):
                task["progress"] = pct
                speed_txt = f" ({spd})" if spd else ""
                task["speed_str"] = f"YouTube: {pct}%{speed_txt}"

            yt_title = task.get("display_title") or friendly_name
            if YOUTUBE_UPLOADER_AVAILABLE:
                yt_res = upload_video_to_youtube(
                    file_path=str(final_file),
                    title=yt_title,
                    description="",
                    privacy_status=task.get("privacy_status", "unlisted"),
                    progress_callback=yt_progress,
                    user_id=task.get("user_id")
                )
                if yt_res.get("success"):
                    task["youtube_url"] = yt_res.get("url")
                    task["youtube_status"] = "uploaded"
                    task["uploaded_at"] = time.time()
                    logger.info(f"Task {task_id}: YouTube upload succeeded -> {yt_res.get('url')}")
                else:
                    task["youtube_status"] = f"Upload failed: {yt_res.get('error')}"
                    logger.warning(f"Task {task_id}: YouTube upload failed -> {yt_res.get('error')}")
            else:
                task["youtube_status"] = "YouTube uploader module not configured."

        # Auto-upload to Telegram if enabled
        if task.get("upload_to_telegram") and not task.get("cancelled"):
            task["status"] = "uploading"
            task["speed_str"] = "Uploading to Telegram..."
            task["progress"] = 0.0
            task["telegram_status"] = "uploading"
            logger.info(f"Task {task_id}: Starting auto-upload to Telegram...")

            def tg_progress(pct, spd_str):
                task["progress"] = pct
                task["speed_str"] = f"TG: {pct}% ({spd_str})"

            clean_title = (task.get("custom_name") or "").strip()
            if not clean_title:
                disp = (task.get("display_title") or "").strip()
                if disp and not disp.startswith("http://") and not disp.startswith("https://"):
                    clean_title = disp
            if not clean_title:
                clean_title = friendly_name
            clean_title = re.sub(r'^[a-zA-Z0-9]{8}_', '', clean_title).strip()
            title_stem = Path(clean_title).stem if "." in clean_title else clean_title
            if not title_stem:
                title_stem = "Video"

            ext = final_file.suffix or ".mp4"
            video_filename = f"{title_stem}{ext}"
            caption_text = f"**{title_stem}**"

            bot_token = task.get("tg_bot_token") or os.environ.get("TG_BOT_TOKEN")
            api_id = task.get("tg_api_id") or os.environ.get("TG_API_ID")
            api_hash = task.get("tg_api_hash") or os.environ.get("TG_API_HASH")
            chat_id = task.get("tg_chat_id") or os.environ.get("TG_CHAT_ID")

            if TELEGRAM_UPLOADER_AVAILABLE and bot_token and api_id and api_hash and chat_id:
                try:
                    tg_res = asyncio.run(upload_video_to_telegram(
                        file_path=str(final_file),
                        caption=caption_text,
                        file_name=video_filename,
                        bot_token=bot_token,
                        api_id=api_id,
                        api_hash=api_hash,
                        chat_id=chat_id,
                        progress_callback=tg_progress
                    ))
                    if tg_res.get("success"):
                        task["telegram_url"] = tg_res.get("url")
                        task["telegram_message_id"] = tg_res.get("message_id")
                        task["telegram_status"] = "uploaded"
                        task["uploaded_to_tg_at"] = time.time()
                        logger.info(f"Task {task_id}: Telegram upload succeeded -> {tg_res.get('url') or tg_res.get('message_id')}")
                    else:
                        task["telegram_status"] = f"Upload failed: {tg_res.get('error')}"
                        logger.warning(f"Task {task_id}: Telegram upload failed -> {tg_res.get('error')}")
                except Exception as e:
                    task["telegram_status"] = f"Upload error: {str(e)}"
                    logger.error(f"Task {task_id}: Telegram upload exception: {e}")
            else:
                missing = []
                if not TELEGRAM_UPLOADER_AVAILABLE:
                    missing.append(f"Server library missing ({TG_IMPORT_ERROR or 'run: pip install pyrofork tgcrypto'})")
                if not bot_token: missing.append("Bot Token")
                if not api_id: missing.append("API ID")
                if not api_hash: missing.append("API Hash")
                if not chat_id: missing.append("Chat ID")
                task["telegram_status"] = f"Missing TG configs: {', '.join(missing)}"

        task["status"] = "completed"
        task["progress"] = 100.0
        task["completed_at"] = time.time()
        logger.info(f"Task {task_id} completed successfully: {final_file.name}")
    else:
        task["status"] = "failed"
        task["error_message"] = "Output file not found after download completed."


def direct_stream_fallback(url: str, task: dict, headers: dict):
    """Fallback chunked downloader for direct HTTP/HTTPS files when yt-dlp fails."""
    task_id = task["id"]
    custom_name = task.get("custom_name", "").strip()

    resp = requests.get(url, headers=headers, stream=True, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    total_size = int(resp.headers.get('content-length', 0))

    # Filename detection
    cd = resp.headers.get('content-disposition', '')
    fname = None
    if cd:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', cd)
        if m:
            fname = unquote(m.group(1))
    if not fname:
        path_name = Path(urlparse(url).path).name
        if path_name and '.' in path_name:
            fname = path_name
    if not fname:
        fname = "video.mp4"

    if custom_name:
        ext = Path(fname).suffix or ".mp4"
        fname = f"{sanitize_filename(custom_name)}{ext}"

    final_path = DOWNLOADS_DIR / f"{task_id}_{sanitize_filename(fname)}"
    task["resolved_filename"] = final_path.name
    if not task.get("display_title"):
        task["display_title"] = custom_name or fname

    downloaded = 0
    start_time = time.time()
    last_update = start_time

    with open(final_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
            if task.get("cancelled"):
                final_path.unlink(missing_ok=True)
                raise Exception("Download cancelled by user.")
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if now - last_update >= 0.5:
                    last_update = now
                    elapsed = now - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    if total_size > 0:
                        pct = round((downloaded / total_size) * 100, 1)
                        eta = (total_size - downloaded) / speed if speed > 0 else 0
                        task["progress"] = pct
                        task["eta_str"] = format_seconds(eta)
                        task["filesize_str"] = format_bytes(total_size)
                    else:
                        task["progress"] = 50.0
                        task["filesize_str"] = format_bytes(downloaded)
                    task["speed_str"] = f"{format_bytes(speed)}/s"

    task["progress"] = 99.0
    task["status"] = "processing"


# --- Worker Loop ---
def sync_download_worker(task_id: str):
    """Synchronous worker executed in ThreadPool for yt-dlp, Google Drive, Mega & ClassPlus/DRM support."""
    task = tasks_db.get(task_id)
    if not task:
        return

    original_url = task["url"]
    custom_name = task.get("custom_name") or ""
    resolution = task.get("resolution", "720")

    task["status"] = "downloading"
    task["started_at"] = time.time()

    is_gdrive = ("drive.google.com" in original_url.lower() or "docs.google.com" in original_url.lower())
    is_mega = ("mega.nz" in original_url.lower() or "mega.co.nz" in original_url.lower())

    if is_gdrive:
        try:
            safe_title = sanitize_filename(custom_name.strip() or f"GoogleDrive_{task_id}")
            dest_file = DOWNLOADS_DIR / f"{task_id}_{safe_title}.mp4"
            final_file = download_google_drive(original_url, dest_file, task)
            task["display_title"] = custom_name.strip() or final_file.stem.replace(f"{task_id}_", "")
            task["resolved_url"] = original_url
            task["media_type"] = "Google Drive"
            finalize_download_task(task_id)
        except Exception as e:
            logger.error(f"Google Drive download error for {task_id}: {e}")
            task["status"] = "failed"
            task["error_message"] = str(e)
        finally:
            task["finished_at"] = time.time()
        return

    if is_mega:
        try:
            final_file = download_mega(original_url, DOWNLOADS_DIR, task, custom_name)
            task["display_title"] = custom_name.strip() or final_file.stem.replace(f"{task_id}_", "")
            task["resolved_url"] = original_url
            task["media_type"] = "Mega.nz"
            finalize_download_task(task_id)
        except Exception as e:
            logger.error(f"Mega.nz download error for {task_id}: {e}")
            task["status"] = "failed"
            task["error_message"] = str(e)
        finally:
            task["finished_at"] = time.time()
        return

    # Resolve ClassPlus / Akamai / ClassX / DRM / CloudFront URL
    resolved_info = resolve_media_url(
        original_url, 
        requested_resolution=resolution,
        custom_referer=task.get("custom_referer"),
        custom_origin=task.get("custom_origin"),
        token=task.get("token")
    )
    target_url = resolved_info["url"]
    keys_string = resolved_info.get("keys_string", "")
    if task.get("keys"):
        provided_keys = [k.strip() for k in task["keys"] if k and k.strip()]
        if provided_keys:
            keys_string = " ".join([f"--key {k}" for k in provided_keys])
            task["media_type"] = "ClassPlus DRM (MPD)"
    extra_headers = resolved_info.get("extra_headers", {})
    task["media_type"] = resolved_info.get("media_type", "Standard")
    task["resolved_url"] = target_url
    logger.info(f"Task {task_id}: Resolved link [{task['media_type']}] -> {target_url[:75]}...")

    is_m3u8_stream = (
        ".m3u8" in target_url or 
        "timeshift=1" in target_url or 
        "cloud-front.in" in target_url or
        "liveclasses" in target_url
    )

    # yt-dlp format selection supporting all media containers (mp4, mkv, webm, mov, flv, ts, audio)
    if resolution == "audio":
        format_spec = "bestaudio/best"
        outtmpl = str(DOWNLOADS_DIR / f"{task_id}_%(title)s.%(ext)s")
        if custom_name.strip():
            safe_name = sanitize_filename(custom_name)
            outtmpl = str(DOWNLOADS_DIR / f"{task_id}_{safe_name}.%(ext)s")
        postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        if resolution == "best":
            format_spec = "best/bestvideo+bestaudio" if is_m3u8_stream else "bestvideo+bestaudio/best[ext=mp4]/best"
        else:
            try:
                res_num = int(resolution)
                if is_m3u8_stream:
                    format_spec = f"best[height<={res_num}]/bestvideo[height<={res_num}]+bestaudio/best"
                else:
                    format_spec = f"bestvideo[height<={res_num}]+bestaudio/best[height<={res_num}]/best[ext=mp4]/best"
            except ValueError:
                format_spec = "best/bestvideo+bestaudio" if is_m3u8_stream else "bestvideo+bestaudio/best[ext=mp4]/best"

        outtmpl = str(DOWNLOADS_DIR / f"{task_id}_%(title)s.%(ext)s")
        if custom_name.strip():
            safe_name = sanitize_filename(custom_name)
            outtmpl = str(DOWNLOADS_DIR / f"{task_id}_{safe_name}.%(ext)s")
        postprocessors = []

    def progress_hook(d):
        if task.get("cancelled", False):
            raise Exception("Download cancelled by user.")

        status = d.get("status")
        if status == "downloading":
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            frag_index = d.get("fragment_index")
            frag_count = d.get("fragment_count")

            # Accurate progress for both single files and fragmented streams
            if frag_count and frag_index:
                task["progress"] = round((frag_index / frag_count) * 100, 1)
                frag_str = f" • Frag {frag_index}/{frag_count}"
            elif total_bytes > 0:
                task["progress"] = round((downloaded / total_bytes) * 100, 1)
                frag_str = ""
            else:
                task["progress"] = 0.0
                frag_str = ""

            speed = d.get("speed")
            speed_val = f"{format_bytes(speed)}/s" if speed else "Downloading..."
            task["speed_str"] = f"{speed_val}{frag_str}"
            task["eta_str"] = format_seconds(d.get("eta"))
            task["filesize_str"] = format_bytes(total_bytes) if total_bytes > 0 else "Estimating..."

            filename = d.get("filename")
            if filename and not task.get("resolved_filename"):
                task["resolved_filename"] = Path(filename).name
        elif status == "finished":
            task["progress"] = 99.0
            task["status"] = "processing"
            task["speed_str"] = "Finalizing..."
            task["eta_str"] = "00:00"

    # Full browser headers to prevent CDN rate-limiting / throttle clamping
    browser_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
        'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
    }
    if extra_headers:
        browser_headers.update(extra_headers)

    user_threads = task.get("threads") or 24
    try:
        user_threads = max(4, min(32, int(user_threads)))
    except (ValueError, TypeError):
        user_threads = 24

    ydl_opts = {
        'format': format_spec,
        'outtmpl': outtmpl,
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True,
        'no_color': True,
        'retries': 30,
        'fragment_retries': 30,
        'noplaylist': True,
        'nocheckcertificate': True,
        'allow_unplayable_formats': True,
        'concurrent_fragment_downloads': user_threads,  # 🚀 24-32 concurrent threads
        'buffersize': 32 * 1024 * 1024,                # 32MB buffer
        'http_chunk_size': 10 * 1024 * 1024,           # 10MB chunk size
        'socket_timeout': 20,
        'http_headers': browser_headers,                # Full browser identity for every chunk
    }

    if postprocessors:
        ydl_opts['postprocessors'] = postprocessors

    # Aria2c ultra-fast multi-connection download for direct non-fragmented files
    aria2c_bin = shutil.which("aria2c")
    is_safe_for_aria2c = (
        aria2c_bin and
        not is_m3u8_stream and
        not any(h in target_url.lower() for h in [
            ".mpd", "youtube.com", "youtu.be",
            "googlevideo.com", "googleusercontent.com", "drive.google.com"
        ])
    )
    if is_safe_for_aria2c:
        ydl_opts['external_downloader'] = 'aria2c'
        ydl_opts['external_downloader_args'] = {
            'aria2c': ['-x', '8', '-s', '8', '-j', '8', '-k', '1M', '--file-allocation=none', '--check-certificate=false']
        }

    if "youtube.com" in target_url or "youtu.be" in target_url:
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_client': ['android', 'ios', 'web'],
                'player_skip': ['configs', 'webpage']
            }
        }
        possible_cookie_paths = [
            COOKIES_FILE,
            str(BASE_DIR / "youtube_cookies.txt"),
            str(BASE_DIR / "cookies.txt"),
            str(BASE_DIR.parent / "Banana_bot--main" / "youtube_cookies.txt"),
            str(BASE_DIR / "website" / "youtube_cookies.txt")
        ]
        for cp in possible_cookie_paths:
            if cp and Path(cp).exists():
                ydl_opts['cookiefile'] = cp
                logger.info(f"Using YouTube cookies from: {cp}")
                break

    if is_m3u8_stream:
        ydl_opts['hls_use_mpegts'] = True
        ydl_opts['live_from_start'] = True

    is_akamai_stream = (
        "akamai-cdn.classplusapp.com" in target_url or
        "hdnts=" in target_url or
        "hdntl=" in target_url or
        "classplusapp" in target_url
    )
    if is_akamai_stream:
        # 🚀 High-speed multi-threaded downloads for ClassPlus Akamai (24-32 threads)
        ydl_opts['concurrent_fragment_downloads'] = user_threads
        ydl_opts['buffersize'] = 32 * 1024 * 1024
        ydl_opts['http_chunk_size'] = 10 * 1024 * 1024
        # Propagate hdnts / hdntl token across variant playlists, chunks, and AES key requests
        tok_m = re.search(r'[?&](hdnt[sl]=[^&]+)', target_url)
        if tok_m:
            tok_str = tok_m.group(1)
            ydl_opts.setdefault('extractor_args', {})['generic'] = {
                'fragment_query': [tok_str],
                'variant_query': [tok_str],
                'key_query': [tok_str]
            }

    is_classx_stream = (
        "classx.co.in" in target_url or
        "transcoded-videos.classx" in target_url
    )
    if is_classx_stream:
        ydl_opts['concurrent_fragment_downloads'] = user_threads
        ydl_opts['buffersize'] = 32 * 1024 * 1024
        ydl_opts['http_chunk_size'] = 10 * 1024 * 1024
        tok_m = re.search(r'[?&](edge-cache-token=[^&]+)', target_url)
        if tok_m:
            tok_str = tok_m.group(1)
            ydl_opts.setdefault('extractor_args', {})['generic'] = {
                'fragment_query': [tok_str],
                'variant_query': [tok_str]
            }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info first
            try:
                info = ydl.extract_info(target_url, download=False)
                extracted_title = info.get('title', 'Video')
            except Exception as e:
                logger.warning(f"Metadata extraction fallback for {task_id}: {e}")
                extracted_title = custom_name.strip() or f"Video_{task_id}"

            # If title is generic (e.g. 'master', 'index', 'Video'), extract clean identifier from Akamai / ClassX path
            if (not extracted_title or extracted_title.lower() in ["master", "index", "playlist", "video"]):
                m_media = re.search(r'/media/(\d+)/[^/]+/([a-zA-Z0-9]+)/', target_url)
                m_classx = re.search(r'/videos/([a-zA-Z0-9_\-]+?)(?:-data)?/([0-9\-]+)/', target_url)
                if m_media:
                    extracted_title = f"ClassPlus_{m_media.group(1)}_{m_media.group(2)}"
                elif m_classx:
                    tenant_clean = m_classx.group(1).replace("onlinetutoria", "")
                    content_clean = m_classx.group(2).split("-")[0]
                    extracted_title = f"ClassX_{tenant_clean}_{content_clean}"
                else:
                    extracted_title = f"ClassPlus_{task_id}" if is_akamai_stream else (f"ClassX_{task_id}" if is_classx_stream else f"Video_{task_id}")

            task["display_title"] = custom_name.strip() if custom_name.strip() else extracted_title

            # Perform download with automatic fallbacks
            try:
                ydl.download([target_url])
            except Exception as dl_err:
                logger.warning(f"yt-dlp download attempt error for {task_id}: {dl_err}")
                recovered = False

                # Fallback 1: If aria2c was used and failed, retry with native yt-dlp downloader
                if ydl_opts.get('external_downloader'):
                    logger.info(f"Task {task_id}: Retrying with native yt-dlp downloader...")
                    ydl_opts_native = dict(ydl_opts)
                    ydl_opts_native.pop('external_downloader', None)
                    ydl_opts_native.pop('external_downloader_args', None)
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts_native) as ydl_native:
                            ydl_native.download([target_url])
                            recovered = True
                            logger.info(f"Task {task_id}: Native yt-dlp retry succeeded!")
                    except Exception as e_native:
                        logger.warning(f"Native yt-dlp retry failed: {e_native}")
                        dl_err = e_native

                if not recovered:
                    ffmpeg_bin = shutil.which("ffmpeg")
                    if is_m3u8_stream and ffmpeg_bin:
                        logger.info(f"Task {task_id}: Falling back to FFmpeg native HLS capture...")
                        task["speed_str"] = "Downloading with FFmpeg..."
                        fallback_prefix = "ClassPlus" if is_akamai_stream else ("ClassX" if is_classx_stream else "LiveClass")
                        fallback_title = sanitize_filename(task.get("display_title") or f"{fallback_prefix}_{task_id}")
                        fallback_file = DOWNLOADS_DIR / f"{task_id}_{fallback_title}.mp4"
                        
                        hdr_lines = "".join([f"{k}: {v}\r\n" for k, v in browser_headers.items()])
                        ffmpeg_cmd = [
                            ffmpeg_bin,
                            "-y",
                            "-headers", hdr_lines,
                            "-i", target_url,
                            "-c", "copy",
                            "-bsf:a", "aac_adtstoasc",
                            "-movflags", "+faststart",
                            str(fallback_file)
                        ]
                        logger.info(f"Running FFmpeg: {' '.join(ffmpeg_cmd[:6])}...")
                        proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=3600)
                        if proc.returncode == 0 and fallback_file.exists() and fallback_file.stat().st_size > 1000:
                            logger.info(f"Task {task_id}: FFmpeg download completed successfully -> {fallback_file}")
                            recovered = True
                        else:
                            logger.error(f"FFmpeg error: {proc.stderr[:300]}")
                            raise dl_err
                    elif target_url.startswith(("http://", "https://")):
                        # Direct HTTP streaming fallback via requests
                        logger.info(f"Task {task_id}: Falling back to direct HTTP streaming chunked download...")
                        direct_stream_fallback(target_url, task, browser_headers)
                        recovered = True
                    else:
                        raise dl_err

        # Post-process, decrypt and handle uploads
        finalize_download_task(task_id, keys_string)
    except Exception as e:
        logger.error(f"Error downloading task {task_id}: {str(e)}")
        task["status"] = "failed"
        task["error_message"] = str(e)
    finally:
        task["finished_at"] = time.time()


async def queue_worker_loop(worker_name: str = "worker-1"):
    """Background task to pull items from the download queue."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            task_id = await download_queue.get()
            task = tasks_db.get(task_id)
            if task and not task.get("cancelled", False):
                logger.info(f"[{worker_name}] Starting task: {task_id}")
                await loop.run_in_executor(thread_pool, sync_download_worker, task_id)
            download_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[{worker_name}] Queue worker exception: {e}")
            await asyncio.sleep(1)


# --- Helper Functions ---
def create_task_entry(
    url: str, 
    custom_name: Optional[str], 
    resolution: str,
    upload_to_youtube: bool = False,
    privacy_status: str = "unlisted",
    user_id: Optional[str] = None,
    upload_to_telegram: bool = False,
    tg_bot_token: Optional[str] = None,
    tg_api_id: Optional[str] = None,
    tg_api_hash: Optional[str] = None,
    tg_chat_id: Optional[str] = None,
    keys: Optional[List[str]] = None,
    custom_referer: Optional[str] = None,
    custom_origin: Optional[str] = None,
    token: Optional[str] = None,
    threads: Optional[int] = 16
) -> str:
    task_id = str(uuid.uuid4())[:8]
    is_cp = any(k in url.lower() for k in ["classplusapp", "testbook.com", "akamai", "media-cdn", "hdnts=", "hdntl="])
    is_classx = any(k in url.lower() for k in ["classx.co.in", "transcoded-videos.classx"])
    is_live_hls = any(k in url.lower() for k in ["cloud-front.in", "liveclasses", "timeshift=1", "txcodectempname", "starttime_epoch"])
    initial_media_type = "ClassPlus / Akamai" if is_cp else ("ClassX Stream" if is_classx else ("Live Classes / Timeshift" if is_live_hls else "Standard"))
    if keys:
        initial_media_type = "ClassPlus DRM (MPD)"
    
    # Validate and normalize threads (4 to 32)
    safe_threads = 16
    try:
        safe_threads = max(4, min(32, int(threads or 16)))
    except (ValueError, TypeError):
        safe_threads = 16

    tasks_db[task_id] = {
        "id": task_id,
        "url": url.strip(),
        "custom_name": custom_name.strip() if custom_name else "",
        "display_title": custom_name.strip() if custom_name else url.strip(),
        "resolution": str(resolution or "720"),
        "media_type": initial_media_type,
        "resolved_url": None,
        "keys": keys or [],
        "custom_referer": custom_referer,
        "custom_origin": custom_origin,
        "token": token,
        "threads": safe_threads,
        "upload_to_youtube": upload_to_youtube,
        "privacy_status": privacy_status,
        "user_id": user_id,
        "youtube_url": None,
        "youtube_status": None,
        "upload_to_telegram": upload_to_telegram,
        "tg_bot_token": tg_bot_token,
        "tg_api_id": tg_api_id,
        "tg_api_hash": tg_api_hash,
        "tg_chat_id": tg_chat_id,
        "telegram_url": None,
        "telegram_status": None,
        "telegram_message_id": None,
        "uploaded_to_tg_at": None,
        "status": "queued",
        "progress": 0.0,
        "speed_str": "--",
        "eta_str": "--",
        "filesize_str": "--",
        "file_path": None,
        "resolved_filename": None,
        "friendly_filename": None,
        "download_url": None,
        "error_message": None,
        "cancelled": False,
        "created_at": time.time(),
        "started_at": None,
        "completed_at": None,
        "uploaded_at": None,
    }
    return task_id



# --- API Routes ---
def background_yt_upload(task_id: str):
    task = tasks_db.get(task_id)
    if not task:
        return
    file_path = task.get("file_path")
    if not file_path or not os.path.exists(file_path):
        task["youtube_status"] = "Error: File missing from disk"
        return
    task["status"] = "uploading"
    task["speed_str"] = "Uploading to YouTube..."
    task["progress"] = 0.0

    def yt_progress(pct, spd=""):
        task["progress"] = pct
        speed_txt = f" ({spd})" if spd else ""
        task["speed_str"] = f"YouTube: {pct}%{speed_txt}"

    yt_title = task.get("display_title") or task.get("friendly_filename") or "Uploaded Video"
    if YOUTUBE_UPLOADER_AVAILABLE:
        yt_res = upload_video_to_youtube(
            file_path=file_path,
            title=yt_title,
            description="",
            privacy_status=task.get("privacy_status", "unlisted"),
            progress_callback=yt_progress,
            user_id=task.get("user_id")
        )
        if yt_res.get("success"):
            task["youtube_url"] = yt_res.get("url")
            task["youtube_status"] = "uploaded"
            task["uploaded_at"] = time.time()
            task["status"] = "completed"
            task["progress"] = 100.0
            logger.info(f"Task {task_id}: YouTube upload succeeded -> {yt_res.get('url')}")
        else:
            task["youtube_status"] = f"Upload failed: {yt_res.get('error')}"
            task["status"] = "completed"
            task["progress"] = 100.0
            logger.warning(f"Task {task_id}: YouTube upload failed -> {yt_res.get('error')}")


def background_tg_upload(task_id: str):
    task = tasks_db.get(task_id)
    if not task:
        return
    file_path = task.get("file_path")
    if not file_path or not os.path.exists(file_path):
        task["telegram_status"] = "Error: File missing from disk"
        return
    task["status"] = "uploading"
    task["speed_str"] = "Uploading to Telegram..."
    task["progress"] = 0.0
    task["telegram_status"] = "uploading"

    def tg_progress(pct, spd_str):
        task["progress"] = pct
        task["speed_str"] = f"TG: {pct}% ({spd_str})"

    # Determine clean video name and title (never a raw URL or task_id hash)
    clean_title = (task.get("custom_name") or "").strip()
    if not clean_title:
        disp = (task.get("display_title") or "").strip()
        if disp and not disp.startswith("http://") and not disp.startswith("https://"):
            clean_title = disp
    if not clean_title:
        clean_title = task.get("friendly_filename") or Path(file_path).stem
    clean_title = re.sub(r'^[a-zA-Z0-9]{8}_', '', clean_title).strip()
    title_stem = Path(clean_title).stem if "." in clean_title else clean_title
    if not title_stem:
        title_stem = "Video"

    ext = Path(file_path).suffix or ".mp4"
    video_filename = f"{title_stem}{ext}"
    caption_text = f"**{title_stem}**"

    bot_token = task.get("tg_bot_token") or os.environ.get("TG_BOT_TOKEN")
    api_id = task.get("tg_api_id") or os.environ.get("TG_API_ID")
    api_hash = task.get("tg_api_hash") or os.environ.get("TG_API_HASH")
    chat_id = task.get("tg_chat_id") or os.environ.get("TG_CHAT_ID")

    if TELEGRAM_UPLOADER_AVAILABLE and bot_token and api_id and api_hash and chat_id:
        try:
            tg_res = asyncio.run(upload_video_to_telegram(
                file_path=file_path,
                caption=caption_text,
                file_name=video_filename,
                bot_token=bot_token,
                api_id=api_id,
                api_hash=api_hash,
                chat_id=chat_id,
                progress_callback=tg_progress
            ))
            if tg_res.get("success"):
                task["telegram_url"] = tg_res.get("url")
                task["telegram_message_id"] = tg_res.get("message_id")
                task["telegram_status"] = "uploaded"
                task["uploaded_to_tg_at"] = time.time()
                task["status"] = "completed"
                task["progress"] = 100.0
                logger.info(f"Task {task_id}: Telegram manual upload succeeded -> {tg_res.get('url') or tg_res.get('message_id')}")
            else:
                task["telegram_status"] = f"Upload failed: {tg_res.get('error')}"
                task["status"] = "completed"
                task["progress"] = 100.0
                logger.warning(f"Task {task_id}: Telegram manual upload failed -> {tg_res.get('error')}")
        except Exception as e:
            task["telegram_status"] = f"Upload error: {str(e)}"
            task["status"] = "completed"
            task["progress"] = 100.0
            logger.error(f"Task {task_id}: Telegram upload exception -> {e}")
    else:
        missing = []
        if not TELEGRAM_UPLOADER_AVAILABLE:
            missing.append(f"Server library missing ({TG_IMPORT_ERROR or 'run: pip install pyrofork tgcrypto'})")
        if not bot_token: missing.append("Bot Token")
        if not api_id: missing.append("API ID")
        if not api_hash: missing.append("API Hash")
        if not chat_id: missing.append("Chat ID")
        task["telegram_status"] = f"Missing TG configs: {', '.join(missing)}"
        task["status"] = "completed"


@app.get("/api/youtube/status")
async def get_youtube_status(user_id: Optional[str] = Query(default=None)):
    # 1. Check Cloudflare if configured and user_id passed
    if user_id and CLOUDFLARE_AUTH_API:
        try:
            cf_res = requests.get(f"{CLOUDFLARE_AUTH_API}/api/user/token?user_id={user_id}", timeout=5)
            cdata = cf_res.json() if "application/json" in cf_res.headers.get("content-type", "") else {}
            if cf_res.status_code == 200:
                return {
                    "available": True,
                    "authenticated": cdata.get("authenticated", False),
                    "channel_name": cdata.get("channel_name", "Connected"),
                    "mode": "cloudflare"
                }
            elif cf_res.status_code == 404:
                return {
                    "available": True,
                    "authenticated": False,
                    "mode": "cloudflare"
                }
        except Exception as e:
            logger.warning(f"Error checking user status with Cloudflare API: {e}")

    # 2. Local fallback
    srv = get_youtube_service(user_id=user_id) if YOUTUBE_UPLOADER_AVAILABLE else None
    return {
        "available": YOUTUBE_UPLOADER_AVAILABLE,
        "authenticated": srv is not None,
        "mode": "local"
    }


@app.post("/api/user/save-secrets")
async def save_user_secrets(payload: dict):
    user_id = payload.get("user_id")
    client_secrets = payload.get("client_secrets")
    if not user_id or not client_secrets:
        raise HTTPException(status_code=400, detail="user_id and client_secrets are required")

    if not CLOUDFLARE_AUTH_API:
        # Save locally if no Cloudflare configured
        try:
            with open(BASE_DIR / "client_secrets.json", "w") as f:
                json.dump(client_secrets, f, indent=2)
            return {"success": True, "message": "Saved client_secrets.json locally on server."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    try:
        cf_res = requests.post(
            f"{CLOUDFLARE_AUTH_API}/api/user/save-secrets",
            json={"user_id": user_id, "client_secrets": client_secrets},
            timeout=10
        )
        return cf_res.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to Cloudflare API: {str(e)}")


@app.get("/api/user/login-url")
async def get_user_login_url(user_id: str):
    if not CLOUDFLARE_AUTH_API:
        return {"url": None, "error": "Cloudflare Auth API URL is not set"}
    return {"url": f"{CLOUDFLARE_AUTH_API}/api/auth/google/login?user_id={user_id}"}


# --- License Verification & Activation API Endpoints ---
@app.post("/api/license/activate")
async def activate_license_route(payload: dict):
    if not CLOUDFLARE_AUTH_API:
        raise HTTPException(status_code=500, detail="Cloudflare Auth API URL is not configured.")
    try:
        resp = requests.post(f"{CLOUDFLARE_AUTH_API}/api/license/activate", json=payload, timeout=8)
        data = resp.json()
        if (data.get("valid") or data.get("success")) and data.get("key"):
            license_cache[data["key"].strip().upper()] = {"data": data, "cached_at": time.time()}
        return JSONResponse(content=data, status_code=resp.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with license server: {e}")


@app.get("/api/license/verify")
@app.post("/api/license/verify")
async def verify_license_route(
    key: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None),
    payload: Optional[dict] = None
):
    return {"valid": True, "success": True, "licensed": True, "permissions": {
        "single": True, "batch": True, "cp_support": True,
        "yt_upload": True, "tg_upload": True, "disk_clean": True
    }}


@app.post("/api/license/request")
async def request_license_route(payload: dict):
    if not CLOUDFLARE_AUTH_API:
        raise HTTPException(status_code=500, detail="Cloudflare Auth API URL is not configured.")
    try:
        resp = requests.post(f"{CLOUDFLARE_AUTH_API}/api/license/request", json=payload, timeout=8)
        return resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"License server error: {e}")


@app.post("/api/youtube/upload/{task_id}")
async def manual_youtube_upload(task_id: str, background_tasks: BackgroundTasks):
    task = tasks_db.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    srv = get_youtube_service(user_id=task.get("user_id")) if YOUTUBE_UPLOADER_AVAILABLE else None
    if not srv:
        raise HTTPException(status_code=400, detail="YouTube API not authenticated. Please connect your YouTube channel first.")
    
    file_path = task.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Downloaded video file not found on disk")

    background_tasks.add_task(background_yt_upload, task_id)
    return {"success": True, "message": "YouTube upload started"}


@app.post("/api/telegram/test")
async def test_telegram_creds(payload: dict):
    bot_token = payload.get("bot_token", "")
    res = validate_telegram_credentials(bot_token)
    res["pyrogram_available"] = TELEGRAM_UPLOADER_AVAILABLE
    if not TELEGRAM_UPLOADER_AVAILABLE:
        res["warning"] = f"Bot token is valid, but Pyrofork MTProto library is not installed: {TG_IMPORT_ERROR or 'run: pip install pyrofork tgcrypto'}"
    return res


@app.post("/api/telegram/upload/{task_id}")
async def manual_telegram_upload(
    task_id: str, 
    payload: TelegramManualUploadRequest, 
    background_tasks: BackgroundTasks
):
    if not TELEGRAM_UPLOADER_AVAILABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Telegram uploader disabled: Pyrofork library is missing on server. Run: !pip install pyrofork tgcrypto in your notebook/server. ({TG_IMPORT_ERROR or ''})"
        )

    task = tasks_db.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    file_path = task.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Downloaded video file not found on disk")

    if payload.bot_token: task["tg_bot_token"] = payload.bot_token
    if payload.api_id: task["tg_api_id"] = payload.api_id
    if payload.api_hash: task["tg_api_hash"] = payload.api_hash
    if payload.chat_id: task["tg_chat_id"] = payload.chat_id

    background_tasks.add_task(background_tg_upload, task_id)
    return {"success": True, "message": "Telegram upload started in background"}


# =========================================================================
#  Classplus Stream Signer & Widevine DRM Extraction (ITSGOLU Engine)
# =========================================================================

DEFAULT_CP_HEADERS = {
    'host': 'api.classplusapp.com',
    'accept-language': 'en',
    'api-version': '56',
    'app-version': '1.12.1.1',
    'build-number': '56',
    'connection': 'Keep-Alive',
    'content-type': 'application/json',
    'device-details': 'motorola_Moto G4_SDK-32',
    'device-id': 'c28d3cb16bbdac01',
    'region': 'IN',
    'user-agent': 'Mobile-Android',
    'accept-encoding': 'gzip'
}


def extract_pssh(mpd_url: str) -> Optional[str]:
    try:
        resp = requests.get(mpd_url, timeout=12)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for elem in root.iter():
            if 'ContentProtection' in elem.tag:
                scheme = elem.get('schemeIdUri', '').lower()
                if 'edef8ba9-79d6-4ace-a3c8-27dcd51d21ed' in scheme:
                    for child in elem:
                        if 'pssh' in child.tag.lower() and child.text:
                            return child.text.strip()
    except Exception as err:
        logger.warning(f"Error parsing MPD for PSSH: {err}")
    return None


def extract_widevine_keys(pssh_b64: str, lic_url: str) -> List[str]:
    if not WIDEVINE_ENABLED:
        logger.warning("pywidevine not installed; cannot extract DRM keys.")
        return []

    wvd_files = (
        glob.glob(str(BASE_DIR / 'WVDs' / '*.wvd')) or 
        glob.glob(str(BASE_DIR / '*.wvd')) or
        glob.glob(str(BASE_DIR.parent / 'WVDs' / '*.wvd')) or
        glob.glob('WVDs/*.wvd') or
        glob.glob('./*.wvd')
    )
    if not wvd_files:
        logger.warning("No .wvd file found for Widevine decryption.")
        return []

    try:
        device = Device.load(wvd_files[0])
        cdm = Cdm.from_device(device)
        session_id = cdm.open()

        challenge = cdm.get_license_challenge(session_id, PSSH(pssh_b64))
        lic_resp = requests.post(
            lic_url,
            data=challenge,
            headers={
                'user-agent': 'okhttp/4.9.3',
                'content-type': 'application/octet-stream'
            },
            timeout=15
        )
        lic_resp.raise_for_status()

        cdm.parse_license(session_id, lic_resp.content)
        keys = [f"{k.kid.hex}:{k.key.hex()}" for k in cdm.get_keys(session_id) if k.type == 'CONTENT']
        cdm.close(session_id)
        return keys
    except Exception as err:
        logger.warning(f"Widevine key extraction failed: {err}")
        return []


@app.get("/itsgolu")
@app.get("/api/classplus/sign")
def itsgolu_signer(
    url: str = Query(..., description="Target Classplus stream URL / content ID"),
    token: Optional[str] = Query(None, description="x-access-token"),
    orgCode: Optional[str] = Query(None, description="Optional Org Code")
):
    headers = DEFAULT_CP_HEADERS.copy()
    if isinstance(token, str) and token.strip() and token != "/d":
        headers["x-access-token"] = token.strip()
    elif CP_TOKEN and isinstance(CP_TOKEN, str) and CP_TOKEN.strip():
        headers["x-access-token"] = CP_TOKEN.strip()
    if isinstance(orgCode, str) and orgCode.strip():
        headers["orgCode"] = orgCode.strip()

    clean_url = url.replace("https://cpvod.testbook.com/", "https://media-cdn.classplusapp.com/drm/")
    clean_url = clean_url.replace("https://tencdn.classplusapp.com/", "https://media-cdn.classplusapp.com/tencent/")

    sign_api = "https://api.classplusapp.com/cams/uploader/video/jw-signed-url"
    params = {"contentId": clean_url, "offlineDownload": "false"} if not clean_url.startswith("http") else {"url": clean_url}

    try:
        resp = requests.get(sign_api, params=params, headers=headers, timeout=20)
        data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Classplus API communication error: {e}")

    signed_url = data.get("url") or (data.get("data") or {}).get("url")
    drm_urls = data.get("drmUrls") or (data.get("data") or {}).get("drmUrls")

    if signed_url:
        return {
            "success": True,
            "url": signed_url
        }

    if drm_urls:
        mpd_url = drm_urls.get("manifestUrl") or drm_urls.get("mpdUrl")
        lic_url = drm_urls.get("licenseUrl") or drm_urls.get("licUrl")
        
        keys = []
        if mpd_url and lic_url:
            pssh = extract_pssh(mpd_url)
            if pssh:
                keys = extract_widevine_keys(pssh, lic_url)

        return {
            "success": True,
            "MPD": mpd_url,
            "KEYS": keys,
            "lic_url": lic_url
        }

    return JSONResponse(
        status_code=400,
        content={"success": False, "message": "Failed to sign URL", "raw_response": data}
    )


@app.get("/get_keys")
def get_keys_endpoint(
    mpd_url: str = Query(..., description="MPD manifest URL"),
    lic_url: str = Query(..., description="License acquisition URL")
):
    pssh = extract_pssh(mpd_url)
    if not pssh:
        raise HTTPException(status_code=400, detail="PSSH box missing in MPD manifest")

    keys = extract_widevine_keys(pssh, lic_url)
    if not keys:
        raise HTTPException(status_code=500, detail="Key acquisition failed from license server")

    return {
        "mpd": mpd_url,
        "keys": keys
    }


@app.get("/download")
def download_stream(
    url: str = Query(..., description="Target file URL to download"),
    filename: Optional[str] = Query("classplus_video.mp4", description="Output filename")
):
    """
    Proxy download endpoint streaming files with Content-Disposition attachment header.
    """
    try:
        req = requests.get(url, stream=True, timeout=30)
        req.raise_for_status()

        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
        content_type = req.headers.get("content-type", "application/octet-stream")
        content_length = req.headers.get("content-length")
        if content_length:
            headers["Content-Length"] = content_length

        def stream_chunks():
            for chunk in req.iter_content(chunk_size=1024 * 128):
                if chunk:
                    yield chunk

        return StreamingResponse(stream_chunks(), media_type=content_type, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download streaming error: {e}")


@app.get("/health")
@app.get("/api/health")
def check_health():
    wvd_found = bool(
        glob.glob(str(BASE_DIR / 'WVDs' / '*.wvd')) or 
        glob.glob(str(BASE_DIR / '*.wvd')) or
        glob.glob(str(BASE_DIR.parent / 'WVDs' / '*.wvd')) or
        glob.glob('WVDs/*.wvd') or
        glob.glob('./*.wvd')
    )
    return {
        "status": "online",
        "pywidevine_installed": WIDEVINE_ENABLED,
        "wvd_device_loaded": wvd_found
    }


@app.post("/api/download/single")
async def download_single(req: SingleDownloadRequest):
    url = req.url.strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Invalid URL provided. Please provide a full HTTP/HTTPS URL.")

    # License and granular feature verification
    if CLOUDFLARE_AUTH_API:
        if not verify_license_permission(req.license_key, req.device_id, "single"):
            raise HTTPException(status_code=403, detail="Single video download is locked. Please activate a valid license key.")
        
        is_cp_stream = any(k in url.lower() for k in ["classplusapp", "testbook.com", "akamai-cdn", "media-cdn", "hdnts=", "hdntl=", ".mpd"])
        if is_cp_stream and not verify_license_permission(req.license_key, req.device_id, "cp_support"):
            raise HTTPException(status_code=403, detail="ClassPlus / DRM stream processing is locked on your license plan.")

        if req.upload_to_youtube and not verify_license_permission(req.license_key, req.device_id, "yt_upload"):
            raise HTTPException(status_code=403, detail="YouTube Auto-Upload is locked on your license plan.")

        if req.upload_to_telegram and not verify_license_permission(req.license_key, req.device_id, "tg_upload"):
            raise HTTPException(status_code=403, detail="Telegram Auto-Upload is locked on your license plan.")

    task_id = create_task_entry(
        url=url, 
        custom_name=req.custom_name, 
        resolution=req.resolution,
        upload_to_youtube=req.upload_to_youtube,
        privacy_status=req.privacy_status,
        user_id=req.user_id,
        upload_to_telegram=req.upload_to_telegram,
        tg_bot_token=req.tg_bot_token,
        tg_api_id=req.tg_api_id,
        tg_api_hash=req.tg_api_hash,
        tg_chat_id=req.tg_chat_id,
        keys=req.keys,
        custom_referer=req.custom_referer,
        custom_origin=req.custom_origin,
        token=req.token,
        threads=req.threads or 16
    )
    await download_queue.put(task_id)
    return {"success": True, "task": tasks_db[task_id]}


@app.post("/api/download/batch")
async def download_batch(req: BatchDownloadRequest):
    if CLOUDFLARE_AUTH_API:
        if not verify_license_permission(req.license_key, req.device_id, "batch"):
            raise HTTPException(status_code=403, detail="Batch / Multi-Link download is locked. Please activate a valid license key.")

        if req.upload_to_youtube and not verify_license_permission(req.license_key, req.device_id, "yt_upload"):
            raise HTTPException(status_code=403, detail="YouTube Auto-Upload is locked on your license plan.")

        if req.upload_to_telegram and not verify_license_permission(req.license_key, req.device_id, "tg_upload"):
            raise HTTPException(status_code=403, detail="Telegram Auto-Upload is locked on your license plan.")

    added_tasks = []

    # Case 1: Structured items list
    if req.items:
        for item in req.items:
            u = item.url.strip()
            if u.startswith("http://") or u.startswith("https://"):
                res = item.resolution or req.default_resolution
                yt_up = item.upload_to_youtube or req.upload_to_youtube
                priv = item.privacy_status or req.default_privacy
                uid = item.user_id or req.user_id
                tg_up = item.upload_to_telegram or req.upload_to_telegram
                b_tok = item.tg_bot_token or req.tg_bot_token
                a_id = item.tg_api_id or req.tg_api_id
                a_hash = item.tg_api_hash or req.tg_api_hash
                c_id = item.tg_chat_id or req.tg_chat_id
                c_ref = item.custom_referer or req.default_referer
                c_orig = item.custom_origin or req.default_origin
                c_tok = item.token or req.default_token
                th = item.threads or req.default_threads or 16
                tid = create_task_entry(
                    url=u, 
                    custom_name=item.custom_name, 
                    resolution=res, 
                    upload_to_youtube=yt_up, 
                    privacy_status=priv, 
                    user_id=uid,
                    upload_to_telegram=tg_up,
                    tg_bot_token=b_tok,
                    tg_api_id=a_id,
                    tg_api_hash=a_hash,
                    tg_chat_id=c_id,
                    custom_referer=c_ref,
                    custom_origin=c_orig,
                    token=c_tok,
                    threads=th
                )
                await download_queue.put(tid)
                added_tasks.append(tasks_db[tid])

    # Case 2: Raw multiline text
    if req.raw_text:
        lines = [line.strip() for line in req.raw_text.splitlines() if line.strip()]
        for line in lines:
            extracted_url = None
            extracted_name = None

            if " | " in line:
                parts = line.split(" | ", 1)
                if parts[0].startswith("http"):
                    extracted_url, extracted_name = parts[0], parts[1]
                else:
                    extracted_name, extracted_url = parts[0], parts[1]
            elif " : " in line:
                parts = line.split(" : ", 1)
                if parts[0].startswith("http"):
                    extracted_url, extracted_name = parts[0], parts[1]
                else:
                    extracted_name, extracted_url = parts[0], parts[1]
            elif "://" in line:
                match = re.search(r'(https?://[^\s]+)', line)
                if match:
                    extracted_url = match.group(1)
                    name_candidate = line.replace(extracted_url, "").strip(" -:|")
                    if name_candidate:
                        extracted_name = name_candidate

            if extracted_url and (extracted_url.startswith("http://") or extracted_url.startswith("https://")):
                tid = create_task_entry(
                    url=extracted_url, 
                    custom_name=extracted_name, 
                    resolution=req.default_resolution,
                    upload_to_youtube=req.upload_to_youtube,
                    privacy_status=req.default_privacy,
                    user_id=req.user_id,
                    upload_to_telegram=req.upload_to_telegram,
                    tg_bot_token=req.tg_bot_token,
                    tg_api_id=req.tg_api_id,
                    tg_api_hash=req.tg_api_hash,
                    tg_chat_id=req.tg_chat_id,
                    custom_referer=req.default_referer,
                    custom_origin=req.default_origin,
                    token=req.default_token,
                    threads=req.default_threads or 16
                )
                await download_queue.put(tid)
                added_tasks.append(tasks_db[tid])

    if not added_tasks:
        raise HTTPException(status_code=400, detail="No valid URLs found in request.")

    return {"success": True, "count": len(added_tasks), "tasks": added_tasks}



@app.get("/api/queue")
async def get_queue(user_id: Optional[str] = Query(None)):
    if user_id:
        user_tasks = [t for t in tasks_db.values() if not t.get("user_id") or t.get("user_id") == user_id]
    else:
        user_tasks = list(tasks_db.values())
    # Return tasks sorted by creation time (most recent first)
    sorted_tasks = sorted(user_tasks, key=lambda t: t.get("created_at", 0), reverse=True)
    return {"tasks": sorted_tasks}


@app.get("/api/queue/{task_id}")
async def get_task_status(task_id: str):
    task = tasks_db.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.delete("/api/queue/{task_id}")
async def cancel_or_delete_task(task_id: str, user_id: Optional[str] = Query(None)):
    task = tasks_db.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if user_id and task.get("user_id") and task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to delete this task")

    task["cancelled"] = True
    # If file exists, clean up
    file_path = task.get("file_path")
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            logger.warning(f"Failed to delete file {file_path}: {e}")

    tasks_db.pop(task_id, None)
    return {"success": True, "message": f"Task {task_id} deleted."}


@app.post("/api/queue/clear")
async def clear_finished_tasks(user_id: Optional[str] = Query(None)):
    to_delete = [
        tid for tid, t in tasks_db.items()
        if t.get("status") in ("completed", "failed") and (not user_id or t.get("user_id") == user_id)
    ]
    for tid in to_delete:
        tasks_db.pop(tid, None)
    return {"success": True, "cleared_count": len(to_delete)}


@app.get("/api/files/{task_id}")
async def download_completed_file(task_id: str):
    task = tasks_db.get(task_id)
    if not task or task.get("status") != "completed":
        raise HTTPException(status_code=404, detail="File is not available or download not completed.")

    file_path = task.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Downloaded file not found on disk.")

    friendly_name = task.get("friendly_filename") or Path(file_path).name
    return FileResponse(
        path=file_path,
        filename=friendly_name,
        media_type="application/octet-stream"
    )


@app.post("/api/storage/clean")
async def clean_storage_manually(
    user_id: Optional[str] = Query(None),
    license_key: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None)
):
    """Manually deletes downloaded video files for the requesting user (or all if user_id is omitted)."""
    if CLOUDFLARE_AUTH_API and not verify_license_permission(license_key, device_id, "disk_clean"):
        raise HTTPException(status_code=403, detail="Disk storage cleanup is locked on your license plan. Contact Admin.")

    count = 0
    freed_bytes = 0
    target_tasks = [t for t in tasks_db.values() if not user_id or t.get("user_id") == user_id]

    for t in target_tasks:
        file_path = t.get("file_path")
        if file_path and os.path.exists(file_path):
            try:
                fsize = os.path.getsize(file_path)
                os.remove(file_path)
                freed_bytes += fsize
                count += 1
                t["file_path"] = None
                t["download_url"] = None
                t["filesize_str"] = "Cleaned"
            except Exception as e:
                logger.warning(f"Could not delete {file_path}: {e}")

    return {
        "success": True,
        "deleted_files": count,
        "freed_space": format_bytes(freed_bytes)
    }


@app.get("/api/stats")
async def get_system_stats(user_id: Optional[str] = Query(None)):
    target_tasks = [t for t in tasks_db.values() if not user_id or t.get("user_id") == user_id]
    total = len(target_tasks)
    active = sum(1 for t in target_tasks if t.get("status") in ("queued", "downloading", "processing", "uploading"))
    completed = sum(1 for t in target_tasks if t.get("status") == "completed")
    failed = sum(1 for t in target_tasks if t.get("status") == "failed")

    # Compute target user's files size
    total_size = 0
    for t in target_tasks:
        fp = t.get("file_path")
        if fp and os.path.exists(fp):
            try:
                total_size += os.path.getsize(fp)
            except Exception:
                pass

    user_queue_len = sum(1 for t in target_tasks if t.get("status") == "queued")

    return {
        "total_tasks": total,
        "active_tasks": active,
        "completed_tasks": completed,
        "failed_tasks": failed,
        "disk_usage": format_bytes(total_size),
        "queue_length": user_queue_len
    }


# Mount static assets
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
