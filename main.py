import os
import yt_dlp
import sys
import re
from flask import Flask, request, send_from_directory, jsonify
from datetime import datetime
from urllib.parse import unquote

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


# ---------- DOWNLOAD WITH MULTIPLE BYPASS STRATEGIES ----------
def download_best(url):
    print(f"\n[+] Starting download: {url}")

    # Multiple strategies to bypass YouTube's bot detection on cloud platforms
    strategies = [
        {
            'name': 'Android TV Client',
            'opts': {
                'quiet': True,
                'no_warnings': True,
                'user_agent': 'com.google.android.youtube.tv/2.12.08 (Linux; U; Android 9; SM-T720)',
                'extractor_retries': 3,
                'fragment_retries': 3,
                'cachedir': False,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android_tv'],
                        'player_skip': ['configs', 'webpage'],
                    }
                }
            }
        },
        {
            'name': 'iOS Client',
            'opts': {
                'quiet': True,
                'no_warnings': True,
                'user_agent': 'com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X)',
                'extractor_retries': 3,
                'fragment_retries': 3,
                'cachedir': False,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['ios'],
                        'player_skip': ['configs', 'webpage'],
                    }
                }
            }
        },
        {
            'name': 'Android Creator',
            'opts': {
                'quiet': True,
                'no_warnings': True,
                'user_agent': 'com.google.android.apps.youtube.creator/22.43.101 (Linux; U; Android 11; SM-G998B)',
                'extractor_retries': 3,
                'fragment_retries': 3,
                'cachedir': False,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android_creator'],
                        'player_skip': ['configs'],
                    }
                }
            }
        },
        {
            'name': 'Web with bypass',
            'opts': {
                'quiet': True,
                'no_warnings': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'referer': 'https://www.youtube.com/',
                'headers': {
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-origin',
                },
                'extractor_retries': 2,
                'fragment_retries': 2,
                'cachedir': False,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web'],
                        'player_skip': ['configs', 'webpage'],
                        'skip': ['hls'],
                    }
                }
            }
        }
    ]
    
    info_dict = None
    successful_strategy = None
    
    # Try each strategy until one works
    for strategy in strategies:
        print(f"[*] Trying {strategy['name']} client...")
        try:
            with yt_dlp.YoutubeDL(strategy['opts']) as ydl:
                info_dict = ydl.extract_info(url, download=False)
                successful_strategy = strategy
                print(f"[✓] Success with {strategy['name']} client!")
                break
        except Exception as e:
            print(f"[!] {strategy['name']} failed: {str(e)[:100]}...")
            continue
    
    if not info_dict:
        print("[x] All extraction strategies failed!")
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

    # Use the successful strategy for download
    download_opts = successful_strategy['opts'].copy()
    download_opts.update({
        'format': f"{v['format_id']}+{a['format_id']}",
        'merge_output_format': 'mp4',
        'outtmpl': final_path,
        'progress_hooks': [ydl_progress],
        'quiet': False,  # Show yt-dlp's built-in progress
        'no_warnings': False,
    })

    try:
        with yt_dlp.YoutubeDL(download_opts) as ydl:
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
            return jsonify({"error": "No suitable video/audio found"}), 400

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
    import os
    port = int(os.environ.get("PORT", 5000))  # Railway gives a dynamic port
    app.run(host="0.0.0.0", port=port, debug=False)