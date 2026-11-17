## Upload-time aspect ratio and face-cam crop (new)

On **Videos → Upload a recording**, select the output aspect ratio (9:16, 1:1, or 16:9). For 9:16 choose whether the source contains an embedded face-cam. If yes, the uploaded footage appears in-browser; pause/seek to a clear frame and **drag a rectangle around the face-cam** before clicking **Upload & analyze**. The normalized crop and output aspect ratio are saved on the video and applied to **every highlight from that recording**, including newly generated highlights after reanalysis. Automatic face detection and per-highlight crop controls have been removed. Portrait face-cam layout places the cropped camera in the upper 40% and gameplay below it. Subtitle text/timings are still editable individually in Review Queue. 1:1 and 16:9 exports use the standard fit-with-blurred-background layout and do not use face-cam extraction.

Crop selection needs a browser-playable video preview (typically MP4). For unusual formats supported by the backend but not the browser, convert to MP4 to choose a crop. Preview and final FFmpeg framing can differ slightly.

# Clip Studio — Free Render Test Build

This is a **free-tier demo**, not a production or 24/7 livestream deployment.

## What changed from Build 1

- `render.yaml`: `plan: free` instead of `starter`; removed `disk` completely.
- Temporary writable media and SQLite location: `/tmp/clip-studio`.
- Limited uploads to `0.1 GB` (approximately 102 MiB) for short demo videos.
- `ENABLE_LIVE_MONITOR=false`: prevents starting unstable live recordings on Render Free.
- Existing upload → automatic audio-activity highlights → review/edit → approve → MP4 export remains available for small tests, subject to the service's processing capacity.

## Deploy to Render

1. In GitHub, replace your **repository-root** `render.yaml`, `Dockerfile`, `app/main.py`, and `app/static/index.html` with the files in this package (or upload this entire project's contents to repository root). Also replace `.env.example` and `README.md` for consistency.
2. Commit directly to `main`.
3. Render → **New** → **Blueprint** → select your `clip-studio` repo and `main` branch.
4. When asked, provide `APP_PASSWORD` as a unique strong password. Username is `admin`.
5. Deploy. Visit your `.onrender.com` URL; upload a short MP4 (recommended: less than 60 seconds and 50 MB).
6. Review the suggested highlight and approve/export one clip.

**If an older paid Blueprint/service has already been created:** check Render service settings for the selected plan and disk; editing the repo alone does not necessarily delete previously provisioned paid resources. A fresh Blueprint may be easier for the free test.

## Limitations

Render Free web services can spin down after 15 minutes without incoming traffic. Waking can take time. Local storage is **ephemeral**: uploads, exports and the SQLite database may vanish on restarts, redeploys or spin-down. A 100 MB upload can still fail if HTTP limits, free CPU/RAM, storage or request timing are exceeded. This app processes tasks in process threads and is not a durable job queue. Use small source videos only. This demo does not download YouTube videos, connect directly to Twitch/YouTube accounts, add captions or publish clips. Do not use for irreplaceable content.

When ready to move beyond testing, use paid compute plus object storage and a database, and a background worker for continuous live monitoring.

---

# Clip Studio — Build 1

A small hosted, password-protected clipping MVP. Upload your own video or configure an **authorized HTTPS HLS** livestream, suggest audio-activity highlights, review/trim candidates, and **manually approve** vertical MP4 exports. **It never posts automatically.**

## Included and actually working

- Upload MP4/MOV/MKV/WEBM/TS/M4V videos (default max 2 GB)
- Background analysis with FFmpeg audio RMS scoring and spaced highlight candidates (not semantic AI)
- Review queue: preview originals, edit start/end/title, discard, approve
- Portrait export (540×960, blurred fill) to downloadable MP4 with audio
- Live feed capture from **authorized HTTPS HLS `.m3u8`** URL: 120-second chunks analyzed during broadcast; start/stop controls
- SQLite data and media on a mounted Render persistent disk; password-protected dashboard

## Not implemented yet

- Twitch/YouTube OAuth, channel URL ingestion, Twitch chat spike detection, livestream auto-discovery
- YouTube video downloading (not included due to API policies)
- Whisper captions, semantic transcript understanding, smart subject reframing
- Social-account publishing, team accounts, distributed background workers, automatic monitor restart

## Deploy entirely online with Render

1. Create a private GitHub repository and upload these files **at repository root**.
2. In Render, choose **New > Blueprint**, connect the repository, and deploy `render.yaml`.
3. When prompted, set **APP_PASSWORD** to a strong unique secret. `APP_USERNAME` defaults to `admin`.
4. Render creates a paid `starter` Docker web service with a **20 GB persistent disk**. Check current Render pricing before approving deployment.
5. Open the generated `.onrender.com` URL, sign in and test with a short MP4.
6. Leave one instance running. Use the Live Monitor only for a direct HTTPS HLS feed you own or have permission to record. Twitch and YouTube *page URLs* are **not** HLS feed URLs.

Persistent disk is essential: without it, uploads, exports and SQLite may vanish on redeploy. Videos consume substantial space. A hosted CPU without GPU may take minutes to analyze/export large recordings. Never expose the app with a weak password.

## Local development (optional, not needed for hosting)

```bash
export APP_PASSWORD='your-unique-long-password'
export DATA_DIR='./storage'
pip install -r requirements.txt
# requires ffmpeg and ffprobe installed
uvicorn app.main:app --reload
```

Open http://localhost:8000 . If `APP_PASSWORD` is unset, API endpoints reject access.

## Architecture and limitations

FastAPI + a single-page static dashboard + SQLite + FFmpeg. Simple in-process background threads keep deployment small, but are not durable jobs: a server restart interrupts uploads, monitor capture, ongoing analysis and exports. For paid customer usage, move processing to dedicated queue/workers and storage to object storage + PostgreSQL. Live feed URLs can expire. HTTPS HLS feeds must be authorized. This build should be kept behind operator-only authentication and must not be exposed as a multi-tenant public service without additional network egress hardening and authentication work.

**Platform policy:** YouTube API rules prohibit downloading/storing audiovisual content without YouTube's prior written approval. Twitch offers official API clips and downloads subject to authorization; direct platform integrations must use approved scopes and APIs.

**Original source guide:** user-provided `gta6-clip-watcher-setup-guide.pdf` described Streamlink, chat spike counts, FFmpeg cutting and Whisper captions. This project adopts the overall monitoring-and-clipping concept but does not claim to implement its Twitch anonymous chat reader or Whisper captions yet.
