import os
import yt_dlp
import sys
import re
import requests
import json
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


# ---------- YOUTUBE ID EXTRACTION ----------
def extract_youtube_id(url):
    """Extract YouTube video ID from various URL formats including Shorts"""
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


# ---------- ALTERNATIVE METHOD: USE YOUTUBE API ----------
def get_video_info_api(video_id):
    """Try to get video info using YouTube's internal API"""
    try:
        # YouTube's internal API endpoint (used by the website)
        api_url = f"https://www.youtube.com/youtubei/v1/player"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Content-Type': 'application/json',
            'Origin': 'https://www.youtube.com',
            'Referer': f'https://www.youtube.com/watch?v={video_id}',
        }
        
        data = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20231128.07.00",
                    "platform": "DESKTOP"
                }
            },
            "videoId": video_id
        }
        
        response = requests.post(api_url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"[!] API method failed: {str(e)}")
    
    return None


# ---------- FORMAT SELECTION ----------
def select_best_video(formats):
    vids = [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('acodec') == 'none']
    if not vids:
        # Fallback: any format with video
        vids = [f for f in formats if f.get('vcodec') not in (None, 'none')]
    
    codec_order = ['av01', 'vp9', 'avc1']
    for codec in codec_order:
        cand = [f for f in vids if f.get('vcodec', '').lower().startswith(codec)]
        if cand:
            return max(cand, key=lambda f: (f.get('height', 0), f.get('tbr') or 0))
    return max(vids, key=lambda f: (f.get('height', 0), f.get('tbr') or 0), default=None)


def select_best_audio(formats):
    auds = [f for f in formats if f.get('acodec') not in (None, 'none') and f.get('abr') is not None]
    if not auds:
        # Fallback: any format with audio
        auds = [f for f in formats if f.get('acodec') not in (None, 'none')]
    if not auds:
        return None
    return max(auds, key=lambda f: f.get('abr', 0))


# ---------- DOWNLOAD WITH AGGRESSIVE BYPASS ----------
def download_best(url):
    print(f"\n[+] Starting download: {url}")
    
    # Strategy 1: Try yt-dlp with minimal, aggressive bypass options
    print("[*] Trying yt-dlp with bypass options...")
    minimal_opts = {
        'quiet': True,
        'no_warnings': True,
        'extractor_retries': 1,
        'fragment_retries': 1,
        'socket_timeout': 30,
        'cachedir': False,
        'no_check_certificate': True,
        'prefer_insecure': True,
        'user_agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        'headers': {
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate',
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        },
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
            info_dict = ydl.extract_info(url, download=False)
            print("[✓] yt-dlp bypass successful!")
            
            formats = info_dict.get('formats', [])
            
            # Look for combined formats first (easier)
            combined_formats = [f for f in formats if 
                              f.get('vcodec') not in (None, 'none') and 
                              f.get('acodec') not in (None, 'none')]
            
            if combined_formats:
                # Use best combined format
                best_format = max(combined_formats, key=lambda f: (f.get('height', 0), f.get('tbr') or 0))
                format_selector = best_format['format_id']
                print(f"    Using combined format: {best_format.get('height')}p | {best_format.get('vcodec')} + {best_format.get('acodec')}")
            else:
                # Separate video+audio
                v = select_best_video(formats)
                a = select_best_audio(formats)
                
                if not v:
                    # Just use best available format
                    best_available = max(formats, key=lambda f: f.get('quality', 0)) if formats else None
                    if best_available:
                        format_selector = best_available['format_id']
                        print(f"    Using available format: {best_available.get('format_id')}")
                    else:
                        print("[x] No formats available")
                        return None, None
                elif not a:
                    # Video only
                    format_selector = v['format_id']
                    print(f"    Using video-only format: {v.get('height')}p | {v.get('vcodec')}")
                else:
                    # Video + Audio
                    format_selector = f"{v['format_id']}+{a['format_id']}"
                    print(f"    Selected video: {v.get('height')}p | {v.get('vcodec')}")
                    print(f"    Selected audio: {a.get('acodec')} | {a.get('abr')} kbps")

            # Download
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            final_file = f"{timestamp}.mp4"
            final_path = os.path.join(DOWNLOAD_DIR, final_file)

            download_opts = minimal_opts.copy()
            download_opts.update({
                'format': format_selector,
                'merge_output_format': 'mp4',
                'outtmpl': final_path,
                'progress_hooks': [ydl_progress],
                'quiet': False,
                'no_warnings': False,
            })

            with yt_dlp.YoutubeDL(download_opts) as ydl:
                ydl.download([url])

            print(f"[✓] Done! File saved as: {final_file}\n")
            return final_file, info_dict
            
    except Exception as e:
        print(f"[!] yt-dlp failed: {str(e)[:100]}...")

    # Strategy 2: If yt-dlp completely fails, return error with helpful message
    print("[x] All methods failed!")
    print("[!] YouTube has blocked this server's IP address.")
    print("[!] Possible solutions:")
    print("    1. Use a VPN or proxy service")
    print("    2. Deploy on a different cloud provider") 
    print("    3. Add cookies from your browser")
    print("    4. Use a residential proxy service")
    
    return None, None


# ---------- ROUTES ----------
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
        "usage": "Add YouTube URL after domain",
        "examples": [
            "https://ytd.stylefort.store/https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://ytd.stylefort.store/https://youtu.be/dQw4w9WgXcQ",
            "https://ytd.stylefort.store/https://www.youtube.com/shorts/VIDEO_ID"
        ]
    })


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
                "https://ytd.stylefort.store/https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://ytd.stylefort.store/https://youtu.be/dQw4w9WgXcQ",
                "https://ytd.stylefort.store/https://www.youtube.com/shorts/VIDEO_ID",
                "https://ytd.stylefort.store/https://www.youtube.com/live/VIDEO_ID"
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
            return jsonify({
                "error": "Download failed",
                "reason": "YouTube blocked this server's IP address",
                "solutions": [
                    "Try using a VPN or proxy",
                    "Export cookies from your browser and add to server",
                    "Use a different cloud provider",
                    "Try again later"
                ]
            }), 400

        download_link = "https://ytd.stylefort.store/files/" + filename

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
            "video_id": video_id,
            "status": "success"
        }

        return jsonify(data)
    
    except Exception as e:
        print(f"[x] Error during download: {str(e)}")
        return jsonify({
            "error": "Download failed",
            "details": str(e),
            "solutions": [
                "YouTube has blocked this IP address",
                "Try using cookies or VPN",
                "Consider alternative video sources"
            ]
        }), 500


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))  # Railway gives a dynamic port
    app.run(host="0.0.0.0", port=port, debug=False)