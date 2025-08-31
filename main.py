import os
import yt_dlp
import re
import requests
from flask import Flask, request, send_from_directory, jsonify
from datetime import datetime
from urllib.parse import unquote

app = Flask(__name__)
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ---------- PROGRESS HOOKS ----------
def ydl_progress(d):
    if d['status'] == 'downloading':
        print("[*] Downloading...")
    elif d['status'] == 'finished':
        print("[✓] Download completed.")

# ---------- YOUTUBE ID EXTRACTION ----------
def extract_youtube_id(url):
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{11})',
        r'youtube\.com/.*[?&]v=([A-Za-z0-9_-]{11})',
        r'youtube\.com/shorts/([A-Za-z0-9_-]{11})',
        r'youtube\.com/live/([A-Za-z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

# ---------- FORMAT SELECTION ----------
def select_best_video(formats):
    vids = [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('acodec') == 'none']
    if not vids:
        vids = [f for f in formats if f.get('vcodec') not in (None, 'none')]
    for codec in ('av01', 'vp9', 'avc1'):
        cand = [f for f in vids if f.get('vcodec', '').lower().startswith(codec)]
        if cand:
            return max(cand, key=lambda f: (f.get('height', 0), f.get('tbr') or 0))
    return max(vids, key=lambda f: (f.get('height', 0), f.get('tbr') or 0), default=None)

def select_best_audio(formats):
    auds = [f for f in formats if f.get('acodec') not in (None, 'none') and f.get('abr') is not None]
    if not auds:
        auds = [f for f in formats if f.get('acodec') not in (None, 'none')]
    if not auds:
        return None
    return max(auds, key=lambda f: f.get('abr', 0))

# ---------- DOWNLOAD WITH AGGRESSIVE BYPASS ----------
def download_best(url):
    print(f"\n[+] Starting download: {url}")
    minimal_opts = {
        'quiet': True,
        'no_warnings': True,
        'extractor_retries': 1,
        'fragment_retries': 1,
        'socket_timeout': 30,
        'cachedir': False,
        'no_check_certificate': True,
        'prefer_insecure': True,
        'extractor_args': {
            'youtube': {
                'skip': ['hls', 'dash', 'webpage'],
                'player_skip': ['js', 'configs', 'webpage'],
                'player_client': ['web'],
            }
        }
    }

    try:
        with yt_dlp.YoutubeDL(minimal_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            print("[✓] yt-dlp extract successful.")
            formats = info.get('formats', [])

            combined = [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('acodec') not in (None, 'none')]
            if combined:
                best = max(combined, key=lambda f: (f.get('height', 0), f.get('tbr') or 0))
                fmt = best['format_id']
                print(f"Using combined format: {best.get('height')}p")
            else:
                v = select_best_video(formats)
                a = select_best_audio(formats)
                if not v:
                    fallback = max(formats, key=lambda f: f.get('quality', 0)) if formats else None
                    if not fallback:
                        print("[x] No formats available.")
                        return None, None
                    fmt = fallback['format_id']
                elif not a:
                    fmt = v['format_id']
                else:
                    fmt = f"{v['format_id']}+{a['format_id']}"

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"{timestamp}.mp4"
            path = os.path.join(DOWNLOAD_DIR, filename)

            opts = minimal_opts.copy()
            opts.update({
                'format': fmt,
                'merge_output_format': 'mp4',
                'outtmpl': path,
                'progress_hooks': [ydl_progress],
                'quiet': False,
                'no_warnings': False
            })

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            print(f"[✓] Download complete: {filename}\n")
            return filename, info

    except Exception as e:
        print(f"[x] yt-dlp failed: {e}")
        return None, None

# ---------- Flask Routes ----------
@app.route('/favicon.ico')
def favicon():
    return "", 204

@app.route('/files/<path:filename>')
def serve_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

@app.route('/')
def index():
    return jsonify({
        "service": "YouTube Downloader API",
        "status": "running",
        "usage": "Add YouTube URL after domain"
    })

@app.route('/<path:yt_url>', methods=['GET'])
def download_route(yt_url):
    yt_url = unquote(yt_url)
    if request.query_string:
        yt_url += '?' + request.query_string.decode('utf-8')

    if not any(domain in yt_url.lower() for domain in ['youtube.com', 'youtu.be']):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    vid = extract_youtube_id(yt_url)
    if not vid:
        return jsonify({"error": "Could not extract video ID"}), 400

    clean = f"https://www.youtube.com/watch?v={vid}"

    filename, info = download_best(clean)
    if not filename:
        return jsonify({"error": "Download failed"}), 500

    return jsonify({
        "title": info.get("title"),
        "download_url": f"/files/{filename}",
        "video_id": vid,
        "status": "success"
    })

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)