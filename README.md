# Milestone 6.1 — Twitch Live Intelligence (public metadata and clip signals)

Clip Studio now has a **Twitch metadata monitor** and an independent **Public Clip Signals** queue under **Platforms**. Add another broadcaster to your watchlist, click **Monitor metadata**, then **Scan monitored streams**. The server queries Twitch's official `Get Streams` endpoint and, while the broadcaster is live, requests already-existing Twitch clips created during the current broadcast from `Get Clips`. It does **not** invoke `Create Clip`, publish to any platform, download media, record the livestream, or require your local computer. Public clips are scored by their observed views and views-per-hour; this is **community engagement ranking, not AI analysis of raw stream footage**. Existing suggestions are deduplicated and can be saved/dismissed privately. You can preview a signal using the official Twitch embedded clip player or watch the stream itself using the official Twitch player. Ranking cannot find moments that nobody has clipped yet.

## Optional 10-minute cloud-hosted scheduled scan

This milestone also adds `.github/workflows/twitch-monitor.yml`, a GitHub Actions scheduler with a 10-minute cron and manual Run workflow action. **The scheduler is inactive until both matching secrets are configured.** Generate a unique random secret of at least 32 characters (a 64-character hexadecimal secret is a good choice). Set **Render → Clip Studio → Environment → `MONITOR_SCAN_KEY`** to that value, and set **GitHub repository → Settings → Secrets and variables → Actions → New repository secret → `CLIP_STUDIO_MONITOR_KEY`** to exactly the same value. Redeploy Render if needed. The scheduled action sends a POST to `/api/twitch/monitor/scheduled`, which rejects missing/wrong secrets and uses existing encrypted Twitch OAuth tokens. No passwords or OAuth tokens should be committed. Scheduled checks are best-effort: GitHub Actions schedules can be delayed, and Render Free can sleep or lose local SQLite records between deployments/restarts. Reliable monitoring needs paid persistent infrastructure.

## Scope / publishing guardrails

- A live public stream and permission to make Twitch-native clips do **not** grant an API to copy the whole video or permission to republish that broadcaster's footage on TikTok/YouTube.
- The monitor only offers signals from other users' already-existing public clips; it does not record, transcribe or analyze audio/video and cannot automatically find never-clipped moments.
- Native Twitch clip creation remains **disabled** to prevent unreviewed public publication. Monitoring suggestions never publish.
- Full livestream AI clipping needs a lawful media feed with broadcaster/editor authorization where required, persistent storage, video-worker processing, an AI scoring pipeline, and separate explicit per-destination publishing integrations. Those are **not yet completed**.
- There is no change to existing uploaded-video transcription, aspect variants, subtitles, manual approval, and MP4 export.

Official references: https://dev.twitch.tv/docs/api/reference/streams and https://dev.twitch.tv/docs/api/reference/clips

---

# Review-first Twitch highlight workflow — safety migration

**Important behavior change:** The old `POST /api/twitch/clips` endpoint has been disabled because it created a Twitch-native clip immediately and did **not** create a private Clip Studio draft. The streamer watchlist now offers **Prepare private highlights** instead of **Create Twitch clip**. This launches the existing upload-and-analysis flow for creator-authorized recording footage. It does **not** currently capture someone else's live stream, monitor all stream audio continuously, or download a Twitch VOD without rights. Previous published Twitch clips are unchanged.

For an authorized source recording, choose an AI highlight target of 20, 35, 60, 90, 120 or 180 seconds. The transcript/audio-scoring analysis queues suggested segments in Review. Use the **16:9 copy** and **9:16 copy** actions to create two independent *private review* versions of one highlight. If the original footage had its facecam crop selected on 9:16 upload, the portrait copy preserves that facecam region; 16:9 uses the full widescreen composition. Open either version in the existing editor to adjust cut, captions and layout. **Approve** sends the edited version to the MP4 exporter only; no Twitch, YouTube or TikTok post is initiated.

Future milestones required for the stated end-to-end goal:
- **Hosted live ingest + AI highlight scoring:** a reliable persistent recorder/processor with lawful source footage access (ideally authorized creator/editor relationship), durable object storage, background worker and job scheduler. Public Twitch clip eligibility is not sufficient authorization to download or republish the source media.
- **YouTube Shorts direct publishing:** expand YouTube's current read-only OAuth authorization to the `youtube.upload` scope, use `videos.insert` with explicit user confirmation and track upload states.
- **TikTok posting:** connect TikTok account and implement its reviewed `video.upload` / `video.publish` Content Posting API workflow, creator selection and explicit approval; posting to arbitrary accounts is not possible without their authorization.
- **Twitch publication:** Twitch-native clip publishing is a different workflow with separate editor/broadcaster permissions and Twitch's platform-controlled clip system; it does not allow arbitrary 180-second 16:9 MP4 uploads via the current clip endpoint.

Twitch documentation: https://dev.twitch.tv/docs/api/reference/clips ; YouTube upload documentation: https://developers.google.com/youtube/v3/guides/uploading_a_video ; TikTok direct posting: https://developers.tiktok.com/doc/content-posting-api-get-started

---

# Twitch clip preview and cross-platform sharing

The Platforms → **Twitch Clips I've Created** panel now resolves each requested clip through Twitch's official `Get Clips` endpoint. Twitch clip creation is asynchronous: until a clip appears in Twitch's API, its card displays **Processing**. Click **Refresh clips**, then **Preview & share** to play the actual video through Twitch's official `clips.twitch.tv/embed` player. The player automatically includes the current website hostname in the required `parent` parameter.

To publish a creator-permitted clip on YouTube Shorts or TikTok, open **Open sharing options on Twitch** from the preview, then choose the destination in Twitch's own Share menu. For other broadcasters, these destination choices only appear if that broadcaster permits cross-platform clip sharing. The user must approve the final post through Twitch; Clip Studio cannot infer permissions or silently publish. For more information: https://help.twitch.tv/articles/en_US/Knowledge/how-to-use-clips

This is a functioning **preview + handoff to Twitch-native publishing**, not direct API publishing from Clip Studio. Separate YouTube videos.insert and TikTok Content Posting API integration would require additional authorization, review, and legally accessible source media. The YouTube connection currently remains read-only. No downloadable Twitch video URL is fabricated, no platform restrictions are bypassed, and no Twitch stream data is copied onto Render.

---

# Twitch-first Streamer Discovery and Native Clips

Clip Studio now supports researching **other Twitch broadcasters** in the Platforms page. Search for a username, add a channel to your watchlist, refresh current live status, inspect existing public Twitch clips, and create a Twitch-native clip of a **currently live** watched broadcaster when the broadcaster's clip settings permit it.

## One-time permission change

The new Twitch Create Clip API requires the `clips:edit` user OAuth scope. On **Platforms → Twitch**, click **Reconnect Twitch** and approve the new permission. Your existing Twitch Client ID/Secret and OAuth Encryption Key in Render remain the same. A previous Twitch connection without this scope can browse streamers but cannot create native Twitch clips.

Clip creation is an explicit user action; it is not an automatic service. Twitch decides whether a broadcaster permits clipping and can reject follower-only, subscriber-only, banned-user or disabled-clip requests. Twitch's `edit_url` is a time-limited Twitch clip editor link. Creating a native Twitch clip **does not** copy the video into Clip Studio and does not authorize arbitrary downloading or cross-platform redistribution. Custom Clip Studio subtitles, face-cam layouts, and MP4 export still require footage the user may lawfully process.

The watchlist and created-clip history use the existing temporary SQLite database on Render Free; they are not durable across service storage resets. No continuous monitoring, downloading, scraping or auto-publishing is introduced. YouTube integration and editing workflow remain intact.

---

# Milestone 5.2 — Connected-channel Recording Inbox

The Platforms page now has an **on-demand Recording Inbox** for connected YouTube and Twitch accounts. Use **Scan connected channels** to discover and deduplicate uploads and live-status records. Existing matched recordings keep their status when scanned again. The inbox is metadata only: the official APIs do **not** provide downloadable source recordings.

To clip a discovered recording, download **your own original file** from YouTube Studio or Twitch Video Producer, then select **Attach recording & analyze** in Clip Studio. This opens the existing Video Library upload form with the recording's platform identity attached. Choose aspect ratio, facecam crop, highlight count and length as before, then upload your original MP4. The usual transcript-based analysis runs without separate manual steps. The discovered entry links to the resulting video.

Safety and limits: Source files must be user-provided; Clip Studio does not automatically fetch videos from arbitrary URLs, bypass YouTube/Twitch permissions, or record livestreams. Scanning only runs on demand and does not monitor 24/7. Render Free still has ephemeral local files/database; recording inbox state can disappear on restart/redeployment. Continuous livestream monitoring, scheduled discovery, durable cloud asset storage and worker-based ingest need separate hosted services and platform-compliant capture paths. No automatic publishing is enabled.

---
# Milestone 5 — YouTube & Twitch platform connections (read-only)

The new **Platforms** sidebar page enables read-only YouTube and Twitch OAuth connections, channel-specific VOD discovery and current live status. Video metadata comes from the official YouTube Data/Live APIs and Twitch Helix. Clip Studio does **not** download YouTube/Twitch audiovisual streams, bypass platform protections, auto-publish videos, or persistently record livestreams in this milestone. Use the platform's creator tools to download a recording you own and upload that MP4 via Clip Studio's existing Videos page.

## Required Render Environment variables

- `PUBLIC_BASE_URL=https://clip-studio-t7r6.onrender.com` (use the URL of your own deployment, no trailing slash)
- `OAUTH_ENCRYPTION_KEY` — create a persistent Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it only in Render. **Keep the key unchanged** when redeploying, or connected tokens become unreadable.
- `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` — Google Cloud project, enable YouTube Data API v3, create an OAuth Web application and authorize `https://clip-studio-t7r6.onrender.com/api/platforms/youtube/callback`. Grant YouTube read-only scope and configure OAuth consent screen appropriately.
- `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` — register an application at Twitch Developer Console and add `https://clip-studio-t7r6.onrender.com/api/platforms/twitch/callback` as an OAuth redirect URI.

Do not paste client secrets or access tokens into GitHub. App uses Basic authentication, OAuth CSRF nonces with 10-minute expiry, and encrypts provider tokens in SQLite via Fernet.

## Known limitations

- Render free service uses ephemeral SQLite. Account connections and oauth states disappear when its filesystem resets; they must be reconnected. Use persistent database and key management for production.
- Accounts are authorized individually; no multi-user identity/permission roles in this demo.
- YouTube's official APIs supply metadata, **not source-video files**. Twitch's Helix Get Videos supplies VOD metadata; obtain your own video via Twitch Video Producer.
- Livestream status is an on-demand read of official API data, not persistent monitoring. Continuous capture, chat-spike analysis, durable video jobs, automatic imports and authorized publishing need later infrastructure and explicit integration work.
- Google verification/YouTube API quotas and Twitch developer setup may apply.

---
# Subtitle Style Studio

Clip editor keeps its familiar two-column video/editor layout but uses wider columns, improved padding, and roomier subtitle rows. Under Review / Edit, the right pane includes three subtabs:

- **Subtitle Text**: select None, Bold Uppercase, Minimal, or Custom; edit individual transcript text/timing, add/delete subtitles.
- **Style Designer**: see a frame from the current clip behind the sample caption; choose built-in or uploaded font, font size, text/stroke colours, stroke thickness, opacity and vertical bottom margin. Save a named preset or load a previous one.
- **Font Library**: upload a user-owned TTF/OTF up to 3 MB (validated by fontTools). The new font appears in Style Designer and FFmpeg burns it into the exported clip through libass's fontsdir option.

Preset styles and selected custom fonts are stored with the clip; if you export, libass uses the corresponding font and colour style. Font family names in uploads currently support ASCII letters/numbers/spaces/underscores/hyphens. Presets are not automatically applied to all clips; choose the saved preset in each clip that needs it.

**Render Free limitation:** Font files and preset SQLite records live on temporary filesystem and are not durable across redeploys or server restarts. Missing uploaded fonts block exports rather than silently substituting a different face. Move these assets to persistent object storage before using this workflow in production.

---
# Milestone 4 — Intelligent Review & Batch Workflow

Clip Studio keeps the working upload → highlight → review → export flow.

- **Explainable highlight ranking:** transcription-aware hooks, reactions, questions, dialogue and audio signals produce a **0–99 internal selection score**, not a prediction of virality. Speech-derived titles and explanations appear on new clips.
- **Optional semantic AI:** set Render environment variable `HIGHLIGHT_LLM_MODEL=openai/gpt-oss-20b` (or another currently supported Groq chat model) to use your existing `TRANSCRIPTION_API_KEY` for an additional transcript-based ranking call. This uses extra API requests, subject to the provider's pricing and rate limits. Leave it unset for heuristic-only mode. Groq failures fall back automatically.
- **Thumbnails:** FFmpeg generates midpoint stills lazily for each clip when source footage is available.
- **Review Queue:** rank/time sorting, multi-select, subtitle presets and batch queue for export.
- **Editor:** larger two-column workspace and an audio waveform that you can click to seek; existing layout/subtitle timing/editing controls are preserved.
- **Sequential exports:** one MP4 rendered at a time. The queue is in-process and **not durable** across Render restarts.
- **No automatic publishing:** approved MP4 files remain available in Exports until temporary storage is reset.
- **Existing uploads:** reanalyze to get new rankings and descriptions. Reanalyze replaces unapproved candidate clips; export/save important clips first.

Render Free sleeps and uses temporary storage. Do not rely on it for 24/7 recording, permanent files, or durable video jobs.

---
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
