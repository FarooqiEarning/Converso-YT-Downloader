import os
import yt_dlp
import sys
import re
from flask import Flask, request, send_from_directory, jsonify
from datetime import datetime
from urllib.parse import unquote, urlparse, parse_qs

app = Flask(__name__)
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ---------- PROGRESS HOOKS ----------
def ydl_progress(d):
    if d['status'] == 'downloading':
        print(f"[*] Downloading...")
    elif d['status'] == 'finished':
        print("[✓] Download completed.")


# ---------- FORMAT SELECTION ----------
def list_formats(url):
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get('formats', [])


def select_best_video(formats):
    vids = [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('acodec') == 'none']
    codec_order = ['av01', 'vp9', 'avc1']
    for codec in codec_order:
        cand = [f for f in vids if f.get('vcodec', '').lower().startswith(codec)]
        if cand:
            return max(cand, key=lambda f: (f.get('height', 0), f.get('tbr') or 0))
    return max(vids, key=lambda f: (f.get('height', 0), f.get('tbr') or 0), default=None)


def select_best_audio(formats):
    auds = [f for f in formats if f.get('acodec') not in (None, 'none') and f.get('abr') is not None]
    if not auds:
        return None
    return max(auds, key=lambda f: f.get('abr', 0))


# ---------- YOUTUBE ID EXTRACTION ----------
def extract_youtube_id(url):
    """Extract YouTube video ID from various URL formats including Shorts"""
    # YouTube video ID pattern (11 characters)
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com\/.*[?&]v=([a-zA-Z0-9_-]{11})',
        r'youtube\.com\/shorts\/([a-zA-Z0-9_-]{11})',  # YouTube Shorts
        r'youtube\.com\/live\/([a-zA-Z0-9_-]{11})',    # YouTube Live
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None


# ---------- DOWNLOAD ----------
def download_best(url):
    print(f"\n[+] Starting download: {url}")

    # Extract full metadata with comprehensive options to avoid 403 errors
    ydl_opts_info = {
        'quiet': True,
        'no_warnings': True,
        # User agent and headers
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        },
        # Retry options
        'extractor_retries': 5,
        'fragment_retries': 5,
        'retry_sleep': 'linear:1:5:1',
        # Network options
        'socket_timeout': 30,
        'http_chunk_size': 10485760,  # 10MB chunks
        # YouTube specific
        'extract_flat': False,
        'writesubtitles': False,
        'writeinfojson': False,
        # Cache and update
        'cachedir': False,  # Disable cache to avoid issues
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info_dict = ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"[!] Info extraction failed: {str(e)}")
        return None, None

    formats = info_dict.get('formats', [])
    v = select_best_video(formats)
    a = select_best_audio(formats)

    if not v or not a:
        print("[x] ERROR: No suitable video/audio found.")
        return None, None

    print(f"    Selected video: {v.get('height')}p | {v.get('vcodec')} | {v.get('tbr')} kbps")
    print(f"    Selected audio: {a.get('acodec')} | {a.get('abr')} kbps")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    final_file = f"{timestamp}.mp4"
    final_path = os.path.join(DOWNLOAD_DIR, final_file)

    ydl_opts = {
        'format': f"{v['format_id']}+{a['format_id']}",
        'merge_output_format': 'mp4',
        'outtmpl': final_path,
        'progress_hooks': [ydl_progress],
        # Same headers and options as info extraction
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        },
        'extractor_retries': 5,
        'fragment_retries': 5,
        'retry_sleep': 'linear:1:5:1',
        'socket_timeout': 30,
        'http_chunk_size': 10485760,
        'cachedir': False,  # Disable cache
        'quiet': False,  # Show yt-dlp's built-in progress
        'no_warnings': False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        print(f"[!] Download failed: {str(e)}")
        return None, None

    print(f"[✓] Done! File saved as: {final_file}\n")
    return final_file, info_dict


# ---------- ROUTES ----------
@app.route('/favicon.ico')
def favicon():
    return "", 204


@app.route('/files/<path:filename>')
def serve_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


@app.route('/<path:yt_url>', methods=['GET'])
def download_route(yt_url):
    yt_url = unquote(yt_url)
    
    # Reconstruct full URL including query string
    if request.query_string:
        yt_url += '?' + request.query_string.decode('utf-8')
    
    # Validate that it's a YouTube URL
    if not any(domain in yt_url.lower() for domain in ['youtube.com', 'youtu.be']):
        return jsonify({
            "error": "Invalid request",
            "usage": "Paste your YouTube video URL after the server address",
            "examples": [
                "http://127.0.0.1:5000/https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "http://127.0.0.1:5000/https://youtu.be/dQw4w9WgXcQ",
                "http://127.0.0.1:5000/https://www.youtube.com/shorts/VIDEO_ID",
                "http://127.0.0.1:5000/https://www.youtube.com/live/VIDEO_ID"
            ]
        }), 400
    
    # Extract video ID
    video_id = extract_youtube_id(yt_url)
    if not video_id:
        return jsonify({
            "error": "Could not extract YouTube video ID from URL",
            "url_received": yt_url
        }), 400
    
    # Reconstruct clean YouTube URL
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    
    try:
        filename, info = download_best(clean_url)
        if not filename:
            return jsonify({"error": "No suitable video/audio found"}), 400

        download_link = request.host_url + "files/" + filename

        data = {
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "channel": info.get("channel"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url"),
            "filesize_approx": info.get("filesize_approx"),
            "download_url": download_link,
            "video_id": video_id
        }

        return jsonify(data)
    
    except Exception as e:
        print(f"[x] Error during download: {str(e)}")
        return jsonify({
            "error": "Download failed",
            "details": str(e)
        }), 500


if __name__ == "__main__":
    app.run(debug=True)