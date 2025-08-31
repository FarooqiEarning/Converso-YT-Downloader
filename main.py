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

    # Alternative strategies for cloud deployment
    strategies = [
        {
            'name': 'Minimal Web Client',
            'opts': {
                'quiet': True,
                'no_warnings': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'extractor_retries': 1,
                'fragment_retries': 1,
                'cachedir': False,
                'format': 'best[height<=720]',  # Try lower quality first
                'no_check_certificate': True,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web'],
                        'player_skip': ['configs', 'webpage', 'js'],
                    }
                }
            }
        },
        {
            'name': 'Generic Extractor',
            'opts': {
                'quiet': True,
                'no_warnings': True,
                'user_agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
                'extractor_retries': 1,
                'fragment_retries': 1,
                'cachedir': False,
                'format': 'best[height<=480]',  # Even lower quality
                'force_generic_extractor': True,
                'no_check_certificate': True,
            }
        },
        {
            'name': 'Direct Format',
            'opts': {
                'quiet': True,
                'no_warnings': True,
                'user_agent': 'yt-dlp/2025.08.28',
                'extractor_retries': 1,
                'fragment_retries': 1,
                'cachedir': False,
                'format': '18',  # Try specific format (360p mp4)
                'no_check_certificate': True,
            }
        }
    ]
    
    info_dict = None
    successful_strategy = None
    
    # Try each strategy until one works
    for strategy in strategies:
        print(f"[*] Trying {strategy['name']}...")
        try:
            with yt_dlp.YoutubeDL(strategy['opts']) as ydl:
                info_dict = ydl.extract_info(url, download=False)
                successful_strategy = strategy
                print(f"[✓] Success with {strategy['name']}!")
                break
        except Exception as e:
            print(f"[!] {strategy['name']} failed")
            continue
    
    if not info_dict:
        print("[x] All extraction strategies failed!")
        # Try one last desperate attempt with absolute minimal config
        print("[*] Trying last resort method...")
        try:
            minimal_opts = {
                'quiet': True,
                'format': 'worst',
                'no_warnings': True,
                'cachedir': False,
                'no_check_certificate': True,
                'user_agent': 'curl/7.68.0',
            }
            with yt_dlp.YoutubeDL(minimal_opts) as ydl:
                info_dict = ydl.extract_info(url, download=False)
                successful_strategy = {'name': 'Minimal', 'opts': minimal_opts}
                print("[✓] Success with minimal config!")
        except Exception as e:
            return None, None

    # For cloud deployment, use simple format selection
    formats = info_dict.get('formats', [])
    
    # Try to find a combined format first (easier for cloud)
    combined_formats = [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('acodec') not in (None, 'none')]
    if combined_formats:
        selected_format = max(combined_formats, key=lambda f: (f.get('height', 0), f.get('tbr') or 0))
        format_selector = selected_format['format_id']
        print(f"    Selected combined format: {selected_format.get('height')}p | {selected_format.get('vcodec')} + {selected_format.get('acodec')}")
    else:
        # Fallback to separate video+audio
        v = select_best_video(formats)
        a = select_best_audio(formats)
        
        if not v or not a:
            # Last resort: use any available format
            available_formats = [f for f in formats if f.get('url')]
            if available_formats:
                best_available = max(available_formats, key=lambda f: f.get('quality', 0))
                format_selector = best_available['format_id']
                print(f"    Using available format: {best_available.get('format_id')}")
            else:
                print("[x] ERROR: No suitable formats found.")
                return None, None
        else:
            format_selector = f"{v['format_id']}+{a['format_id']}"
            print(f"    Selected video: {v.get('height')}p | {v.get('vcodec')} | {v.get('tbr')} kbps")
            print(f"    Selected audio: {a.get('acodec')} | {a.get('abr')} kbps")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    final_file = f"{timestamp}.mp4"
    final_path = os.path.join(DOWNLOAD_DIR, final_file)

    # Use the successful strategy for download with minimal modifications
    download_opts = successful_strategy['opts'].copy()
    download_opts.update({
        'format': format_selector,
        'merge_output_format': 'mp4',
        'outtmpl': final_path,
        'progress_hooks': [ydl_progress],
        'quiet': False,
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