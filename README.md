# 🚀 Universal Video Downloader & Uploader (Heroku Ready)

A powerful, high-speed universal media downloader and auto-uploader built with FastAPI, yt-dlp, Pyrofork (Telegram 2GB MTProto), Google Drive (gdown), and Mega.nz.

---

## ✨ Features Added & Supercharged

1. **⚡ Ultra-Fast Download Speed:**
   - Multi-threaded fragment downloading (24 to 32 concurrent threads).
   - 32MB buffer chunking with adaptive rate tuning.
   - `aria2c` multi-connection integration (16 parallel connections for direct files).
   - Akamai CDN & ClassPlus token auto-preservation and DRM decryption.

2. **🌐 Universal Link Support:**
   - **Google Drive:** Direct high-speed download with auto virus-scan bypass.
   - **Mega.nz:** Fully supported with auto-folder & file extraction.
   - **ClassPlus / DRM:** High-speed M3U8/MPD stream handling with token injection.
   - **Direct & Streaming Media:** MP4, MKV, M3U8, WEBM, MOV, FLV, TS, YouTube, and all standard video websites.

3. **📤 Telegram & YouTube Auto-Uploader:**
   - Direct Telegram 2GB upload via MTProto (Pyrofork).
   - Progress bar tracking in real-time.
   - YouTube OAuth direct video uploader.

4. **🔐 Device Key & Cloudflare Worker Authentication:**
   - Key generation, device lock, expiration handling, and feature permission locks.

---

## 🛠️ Heroku Pe Deploy Karne Ka Asaan Tarika (Step-by-Step)

Aap is app ko **2 minute** me Heroku par deploy kar sakte hain.

### Step 1: Files Ko GitHub Par Push Karein

Apne terminal ya command prompt me ye commands chalayein:

```bash
git add .
git commit -m "Heroku deployment ready with universal downloader"
git push origin main
```

---

### Step 2: Heroku Par App Banayein

1. [Heroku Dashboard](https://dashboard.heroku.com/) par login karein.
2. Upar right side me **New** -> **Create new app** par click karein.
3. App ka ek unique naam dein (jaise: `my-fast-downloader`) aur **Create app** par click karein.

---

### Step 3: Buildpacks Add Karein (Sabse Zaroori Step ⚠️)

Heroku ko `ffmpeg` aur `aria2` chalane ke liye 2 buildpacks chahiye:

1. App ke **Settings** tab me jayein.
2. Scroll karke **Buildpacks** section me **Add buildpack** par click karein:
   - **Pehla Buildpack (Apt Buildpack):**
     Paste karein: `https://github.com/heroku/heroku-buildpack-apt.git` -> Click **Save changes**.
   - **Doosra Buildpack (Python Buildpack):**
     Click **Add buildpack** -> Select **Python** (`heroku/python`) -> Click **Save changes**.

> ⚠️ **Dhyan dein:** Buildpacks ka sequence yahi hona chahiye:
> 1. `heroku-buildpack-apt` (Upar)
> 2. `heroku/python` (Neeche)

---

### Step 4: Config Vars (Environment Variables) Set Karein

1. **Settings** tab me **Reveal Config Vars** par click karein.
2. Naye variable add karein:
   - **KEY:** `CLOUDFLARE_AUTH_API`
   - **VALUE:** `https://yt-uplode.mypresonal65.workers.dev`
   - Click **Add**.
3. *(Optional)* Agar aapke paas ClassPlus token fix hai to(ye dalne ki jarurat ni h):
   - **KEY:** `CP_TOKEN`
   - **VALUE:** `Aapka_Classplus_Token`
   - Click **Add**.

---

### Step 5: Deploy Karein

1. App ke **Deploy** tab me jayein.
2. **Deployment method** me **GitHub** choose karein aur apna GitHub repo connect karein.
3. **Manual deploy** section me **Deploy Branch** (`main`) par click karein.
4. 1-2 minute me build complete ho jayega aur aapko **View App** ka button mil jayega! 🚀

---

## 💻 Local Machine Par Run Karne Ke Liye

Agar aap local computer par run karna chahte hain:

```bash
# Dependencies install karein
pip install -r requirements.txt

# Server start karein
uvicorn website.app:app --host 0.0.0.0 --port 8000 --reload
```

Browser me open karein: `http://localhost:8000`
