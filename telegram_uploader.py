"""
Telegram Uploader Module using Pyrofork (MTProto)
Supports uploading large videos (up to 2GB) to Telegram channels or chats
using Bot Token, API ID, and API Hash.
"""

import os
import re
import math
import time
import json
import logging
import asyncio
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Union

import requests

logger = logging.getLogger("telegram_uploader")

PYROGRAM_IMPORT_ERROR = None
try:
    from pyrogram import Client, raw
    PYROGRAM_AVAILABLE = True
except Exception as e:
    try:
        from pyrofork import Client, raw  # fallback check
        PYROGRAM_AVAILABLE = True
    except Exception as e2:
        Client = None
        raw = None
        PYROGRAM_AVAILABLE = False
        PYROGRAM_IMPORT_ERROR = str(e2)
        logger.warning(f"Pyrofork/Pyrogram not installed: {e2}")


def format_bytes(bytes_count: Optional[float]) -> str:
    """Format bytes into human-readable string."""
    if not bytes_count or bytes_count <= 0:
        return "N/A"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_count < 1024.0:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.1f} PB"


def validate_telegram_credentials(bot_token: str) -> Dict[str, Any]:
    """
    Quickly verifies a Telegram Bot Token via HTTP Bot API getMe.
    Returns bot info (id, first_name, username) or error message.
    """
    bot_token = bot_token.strip() if bot_token else ""
    if not bot_token or ":" not in bot_token:
        return {"valid": False, "error": "Invalid Bot Token format (e.g. 123456:ABC-DEF...)"}

    try:
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("ok"):
            result = data.get("result", {})
            return {
                "valid": True,
                "bot_id": result.get("id"),
                "first_name": result.get("first_name"),
                "username": result.get("username")
            }
        else:
            return {"valid": False, "error": data.get("description", "Unauthorized Bot Token")}
    except Exception as e:
        return {"valid": False, "error": f"Network error checking bot token: {str(e)}"}


def get_video_metadata(file_path: str) -> Dict[str, Any]:
    """
    Extracts duration, width, and height using ffprobe, with ffmpeg -i fallback.
    Normalizes dimensions so Telegram always displays a full-width widescreen player card
    even for lower-resolution (360p/480p) videos.
    """
    meta = {"duration": 0, "width": 1280, "height": 720}
    # 1. Try ffprobe first
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration:stream=width,height,duration",
            "-of", "json", file_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            format_info = data.get("format", {})
            if "duration" in format_info and format_info["duration"]:
                try:
                    meta["duration"] = int(float(format_info["duration"]))
                except (ValueError, TypeError):
                    pass
            for stream in data.get("streams", []):
                raw_w = stream.get("width")
                raw_h = stream.get("height")
                if raw_w and raw_h:
                    try:
                        w = int(raw_w)
                        h = int(raw_h)
                        if w > 0 and h > 0:
                            aspect = w / h
                            if aspect >= 1.2:
                                meta["width"] = 1280
                                calc_h = int(round(1280 / aspect))
                                meta["height"] = calc_h + (1 if calc_h % 2 != 0 else 0)
                            else:
                                meta["height"] = 1280
                                calc_w = int(round(1280 * aspect))
                                meta["width"] = calc_w + (1 if calc_w % 2 != 0 else 0)
                    except (ValueError, TypeError):
                        pass
                if not meta["duration"] and stream.get("duration"):
                    try:
                        meta["duration"] = int(float(stream["duration"]))
                    except (ValueError, TypeError):
                        pass
                break
    except Exception as e:
        logger.debug(f"ffprobe metadata extraction skipped: {e}")

    # 2. Fallback to ffmpeg -i if duration is 0 (essential for Heroku where ffprobe might not be symlinked)
    if not meta.get("duration"):
        try:
            ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
            ff_res = subprocess.run([ffmpeg_bin, "-i", file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            err_output = ff_res.stderr or ""
            dur_m = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', err_output)
            if dur_m:
                h, m, s = dur_m.groups()
                meta["duration"] = int(int(h) * 3600 + int(m) * 60 + float(s))
            res_m = re.search(r'Video:.*?(\d{3,4})x(\d{3,4})', err_output)
            if res_m and meta["width"] == 1280 and meta["height"] == 720:
                try:
                    w = int(res_m.group(1))
                    h = int(res_m.group(2))
                    if w > 0 and h > 0:
                        aspect = w / h
                        if aspect >= 1.2:
                            meta["width"] = 1280
                            calc_h = int(round(1280 / aspect))
                            meta["height"] = calc_h + (1 if calc_h % 2 != 0 else 0)
                        else:
                            meta["height"] = 1280
                            calc_w = int(round(1280 * aspect))
                            meta["width"] = calc_w + (1 if calc_w % 2 != 0 else 0)
                except (ValueError, TypeError):
                    pass
        except Exception as e:
            logger.debug(f"ffmpeg metadata fallback skipped: {e}")

    return meta


def generate_video_thumbnail(file_path: str, thumb_path: str, duration: int = 0) -> bool:
    """
    Generates a crisp JPEG thumbnail screenshot (max 320px, 16:9 preserved) using ffmpeg.
    Picks a meaningful frame (avoiding pitch black screens at 00:00:00 - 00:00:02).
    """
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    candidate_times = []
    if duration > 60:
        candidate_times.extend(["00:00:15", "00:00:30", "00:00:05"])
    elif duration > 10:
        candidate_times.extend(["00:00:05", "00:00:02"])
    else:
        candidate_times.extend(["00:00:02", "00:00:00"])

    for ts in candidate_times:
        try:
            cmd = [
                ffmpeg_bin, "-y", "-ss", ts, "-i", file_path,
                "-vframes", "1", "-vf", "scale=320:-2", "-q:v", "2", thumb_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            if res.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 1000:
                return True
        except Exception as e:
            logger.debug(f"ffmpeg thumbnail generation at {ts} failed: {e}")

    # Fallback to start
    try:
        cmd_fb = [
            ffmpeg_bin, "-y", "-ss", "00:00:01", "-i", file_path,
            "-vframes", "1", "-vf", "scale=320:-2", "-q:v", "2", thumb_path
        ]
        res_fb = subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        return res_fb.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0
    except Exception as e:
        logger.debug(f"ffmpeg thumbnail fallback skipped: {e}")
        return False


def ensure_faststart(file_path: str) -> str:
    """
    Ensures MP4 has moov atom at beginning (+faststart) so Telegram Desktop & Mobile
    can stream it instantly and show the play button instead of download arrow.
    Uses -c copy (takes only ~1-2 seconds, zero quality loss).
    """
    try:
        p = Path(file_path)
        if p.suffix.lower() not in [".mp4", ".m4v"]:
            return file_path
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        fast_path = p.with_name(f"fast_{p.name}")
        cmd = [
            ffmpeg_bin, "-y", "-i", str(p),
            "-c", "copy", "-movflags", "+faststart",
            str(fast_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if res.returncode == 0 and fast_path.exists() and fast_path.stat().st_size > 1000:
            fast_path.replace(p)
            logger.info(f"Faststart applied successfully to {p.name}")
    except Exception as e:
        logger.debug(f"Faststart optimization skipped: {e}")
    return file_path


def parse_chat_id(chat_id_raw: Union[str, int]) -> Union[int, str]:
    """Parses chat ID to proper int or username string for Pyrogram."""
    if isinstance(chat_id_raw, int):
        return chat_id_raw
    s = str(chat_id_raw).strip()
    # Check numeric (e.g. -1001234567890 or 12345678)
    if s.startswith("-100") or s.lstrip("-").isdigit():
        return int(s)
    # Check username
    if not s.startswith("@") and not s.isdigit():
        s = f"@{s}"
    return s


def setup_fast_save_file(client: Any, max_workers: int = 16):
    """
    Hooks client.save_file to upload parts using 16 parallel MTProto connections.
    Caches uploaded input file to make caption formatting retries instant.
    """
    original_save_file = getattr(client, "save_file", None)
    cache: Dict[str, Any] = {}

    async def fast_save_file_impl(
        path: Union[str, Path],
        file_id: int = None,
        file_part: int = 0,
        progress: Callable = None,
        progress_args: tuple = (),
        **kwargs
    ):
        file_path_str = str(path)
        if file_path_str in cache:
            return cache[file_path_str]

        if raw is None or not os.path.exists(file_path_str):
            if original_save_file:
                return await original_save_file(path, file_id=file_id, file_part=file_part, progress=progress, progress_args=progress_args, **kwargs)
            return path

        try:
            file_p = Path(path)
            file_size = file_p.stat().st_size
            chunk_size = 512 * 1024  # 512 KB standard chunk size
            total_parts = math.ceil(file_size / chunk_size)
            is_big = file_size > 10 * 1024 * 1024

            if file_id is None:
                rnd_fn = getattr(client, "rnd_id", None)
                if callable(rnd_fn):
                    file_id = rnd_fn()
                else:
                    file_id = int.from_bytes(os.urandom(8), byteorder='little', signed=True)

            uploaded_bytes = 0
            uploaded_lock = asyncio.Lock()
            num_workers = min(max_workers, total_parts) if total_parts > 0 else 1
            # Bounded queue: keeps at most 8 chunks (4 MB total) in memory at any time!
            chunk_queue = asyncio.Queue(maxsize=min(8, num_workers * 2))

            clean_name = file_p.name
            clean_name = re.sub(r'^[a-zA-Z0-9]{8}_', '', clean_name)

            async def upload_worker():
                nonlocal uploaded_bytes
                while True:
                    item = await chunk_queue.get()
                    if item is None:
                        chunk_queue.task_done()
                        break
                    part_index, chunk = item
                    try:
                        for attempt in range(5):
                            try:
                                if is_big:
                                    await client.invoke(
                                        raw.functions.upload.SaveBigFilePart(
                                            file_id=file_id,
                                            file_part=part_index,
                                            file_total_parts=total_parts,
                                            bytes=chunk
                                        )
                                    )
                                else:
                                    await client.invoke(
                                        raw.functions.upload.SaveFilePart(
                                            file_id=file_id,
                                            file_part=part_index,
                                            bytes=chunk
                                        )
                                    )
                                break
                            except Exception as e:
                                if attempt == 4:
                                    raise e
                                await asyncio.sleep(0.3 * (attempt + 1))

                        async with uploaded_lock:
                            uploaded_bytes += len(chunk)
                            if progress:
                                try:
                                    if asyncio.iscoroutinefunction(progress):
                                        await progress(uploaded_bytes, file_size, *progress_args)
                                    else:
                                        progress(uploaded_bytes, file_size, *progress_args)
                                except Exception:
                                    pass
                    finally:
                        chunk_queue.task_done()

            # Launch worker tasks
            worker_tasks = [asyncio.create_task(upload_worker()) for _ in range(num_workers)]

            # Stream chunks from disk one by one (blocks if queue is full, keeping RAM < 10MB)
            with open(file_p, "rb") as f:
                p_idx = 0
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    await chunk_queue.put((p_idx, chunk))
                    p_idx += 1

            # Send stop signal to workers and wait for completion
            for _ in range(num_workers):
                await chunk_queue.put(None)

            await asyncio.gather(*worker_tasks)

            if is_big:
                res = raw.types.InputFileBig(
                    id=file_id,
                    parts=total_parts,
                    name=clean_name
                )
            else:
                res = raw.types.InputFile(
                    id=file_id,
                    parts=total_parts,
                    name=clean_name,
                    md5_checksum=""
                )

            cache[file_path_str] = res
            return res
        except Exception as err:
            logger.warning(f"Fast parallel save_file error ({err}), falling back to standard save_file...")
            if original_save_file:
                return await original_save_file(path, file_id=file_id, file_part=file_part, progress=progress, progress_args=progress_args, **kwargs)
            raise err

    client.save_file = fast_save_file_impl


async def upload_video_to_telegram(
    file_path: str,
    caption: str,
    bot_token: str,
    api_id: Union[int, str],
    api_hash: str,
    chat_id: Union[str, int],
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Dict[str, Any]:
    """
    Uploads a video to Telegram via Pyrofork MTProto client (supports up to 2GB).
    Runs with in_memory=True (no persistent session files on disk).
    """
    if not PYROGRAM_AVAILABLE:
        return {
            "success": False,
            "error": "Pyrofork is not installed. Please run: pip install pyrofork tgcrypto"
        }

    file_p = Path(file_path)
    if not file_p.exists() or file_p.stat().st_size == 0:
        return {"success": False, "error": f"File does not exist on disk: {file_path}"}

    try:
        api_id_int = int(str(api_id).strip())
    except ValueError:
        return {"success": False, "error": f"Invalid Telegram API ID '{api_id}'. It must be an integer."}

    api_hash_clean = str(api_hash).strip()
    bot_token_clean = str(bot_token).strip()
    target_chat = parse_chat_id(chat_id)

    # Clean display file name for Telegram (removes internal task_id prefix)
    clean_filename = file_p.name
    clean_filename = re.sub(r'^[a-zA-Z0-9]{8}_', '', clean_filename)
    if not clean_filename.endswith(file_p.suffix):
        clean_filename = f"{clean_filename}{file_p.suffix}"

    # 0. Ensure MP4 faststart (+faststart) so Telegram desktop & mobile show play button and stream inline
    ensure_faststart(str(file_p))

    # 1. Extract metadata and thumbnail
    meta = get_video_metadata(str(file_p))
    thumb_path = str(file_p.with_suffix(".thumb.jpg"))
    has_thumb = generate_video_thumbnail(str(file_p), thumb_path, duration=meta.get("duration", 0))

    # 2. Progress wrapper
    start_time = time.time()
    last_update = [0.0]

    async def pyrogram_progress(current, total):
        now = time.time()
        # Update progress at most every 0.6 seconds
        if now - last_update[0] >= 0.6 or current == total:
            last_update[0] = now
            pct = round((current / total) * 100, 1) if total > 0 else 0.0
            elapsed = now - start_time
            speed = current / elapsed if elapsed > 0 else 0
            speed_str = f"{format_bytes(speed)}/s"
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(pct, speed_str)
                else:
                    progress_callback(pct, speed_str)

    # 3. Create high-speed temporary in-memory client (32 workers for multi-connection speed)
    session_name = f"omni_uploader_{int(time.time() * 1000) % 100000}"
    client_params = {
        "name": session_name,
        "api_id": api_id_int,
        "api_hash": api_hash_clean,
        "bot_token": bot_token_clean,
        "in_memory": True,
        "workers": 32,
        "ipv6": False,
    }
    try:
        client = Client(**client_params, max_concurrent_transmissions=16)
    except TypeError:
        try:
            client = Client(**client_params)
        except TypeError:
            client_params.pop("ipv6", None)
            client = Client(**client_params)

    setattr(client, "max_concurrent_transmissions", 16)

    try:
        logger.info(f"Connecting to Telegram MTProto for chat: {target_chat}...")
        await client.start()

        logger.info(f"Uploading video {clean_filename} ({format_bytes(file_p.stat().st_size)}) to Telegram...")
        clean_caption = caption[:1024] if caption else clean_filename

        # Hook 16-worker parallel chunk uploader into client.save_file
        setup_fast_save_file(client, max_workers=16)

        try:
            sent_msg = await client.send_video(
                chat_id=target_chat,
                video=str(file_p),
                caption=clean_caption,
                file_name=clean_filename,
                duration=meta.get("duration", 0),
                width=meta.get("width", 1280),
                height=meta.get("height", 720),
                thumb=thumb_path if has_thumb and os.path.exists(thumb_path) else None,
                supports_streaming=True,
                progress=pyrogram_progress
            )
        except Exception as send_err:
            err_str = str(send_err)
            # If formatting parse failed, retry with plain text caption
            if any(k in err_str for k in ["MESSAGE_EMPTY", "ENTITY", "PARSE", "TAG_NOT_FOUND"]):
                logger.warning(f"send_video formatting failed ({send_err}), retrying with plain text caption...")
                plain_caption = re.sub(r'[*_`\[\]()]', '', clean_caption)
                sent_msg = await client.send_video(
                    chat_id=target_chat,
                    video=str(file_p),
                    caption=plain_caption,
                    file_name=clean_filename,
                    duration=meta.get("duration", 0),
                    width=meta.get("width", 1280),
                    height=meta.get("height", 720),
                    thumb=thumb_path if has_thumb and os.path.exists(thumb_path) else None,
                    supports_streaming=True,
                    progress=pyrogram_progress
                )
            # Auto-retry for channels if user entered raw ID without -100
            elif isinstance(target_chat, int) and target_chat > 0 and any(k in err_str for k in ["PEER_ID_INVALID", "CHAT_ID_INVALID", "CHANNEL_INVALID"]):
                alt_chat = int(f"-100{target_chat}")
                logger.info(f"Target chat {target_chat} failed ({send_err}). Retrying with channel ID format {alt_chat}...")
                sent_msg = await client.send_video(
                    chat_id=alt_chat,
                    video=str(file_p),
                    caption=clean_caption,
                    file_name=clean_filename,
                    duration=meta.get("duration", 0),
                    width=meta.get("width", 1280),
                    height=meta.get("height", 720),
                    thumb=thumb_path if has_thumb and os.path.exists(thumb_path) else None,
                    supports_streaming=True,
                    progress=pyrogram_progress
                )
            else:
                raise send_err

        # 4. Generate view link if possible
        tg_link = None
        if sent_msg:
            if sent_msg.chat and sent_msg.chat.username:
                tg_link = f"https://t.me/{sent_msg.chat.username}/{sent_msg.id}"
            elif sent_msg.chat and str(sent_msg.chat.id).startswith("-100"):
                clean_cid = str(sent_msg.chat.id).replace("-100", "")
                tg_link = f"https://t.me/c/{clean_cid}/{sent_msg.id}"

        logger.info(f"Telegram upload succeeded! Message ID: {sent_msg.id}")
        return {
            "success": True,
            "message_id": sent_msg.id,
            "url": tg_link,
            "chat_id": str(target_chat)
        }

    except Exception as e:
        logger.error(f"Telegram upload failed: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        try:
            if client.is_connected:
                await client.stop()
        except Exception:
            pass
        # Clean up temporary thumbnail
        if has_thumb and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception:
                pass
