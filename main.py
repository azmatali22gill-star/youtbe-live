import subprocess
import threading
from fastapi import FastAPI, HTTPException
from stream_config import *
import os
import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from typing import List, Optional
from fastapi.staticfiles import StaticFiles
import signal
import time

def generate_session_id():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

app = FastAPI()
ffmpeg_process = None
record_process = None
hls_process = None
status = "idle"
hls_bridge_process = None
hls_bridge_status = "idle"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECORD_PATH = os.getenv("RECORD_PATH", "./recordings")
HLS_PATH = os.path.join(BASE_DIR, "hls")
os.makedirs(HLS_PATH, exist_ok=True)
os.makedirs(RECORD_PATH, exist_ok=True)

CLIENT_SECRET_FILE = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

def build_hls_command(session_id, segment_time=4, playlist_size=0):
    """Single-bitrate HLS"""
    session_folder = os.path.join(HLS_PATH, session_id)
    os.makedirs(session_folder, exist_ok=True)

    return [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-fflags", "+genpts",
        "-i", IP_CAMERA_URL,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-f", "hls",
        "-hls_time", str(segment_time),
        "-hls_list_size", str(playlist_size),
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", os.path.join(session_folder, "segment_%03d.ts"),
        os.path.join(session_folder, "playlist.m3u8")
    ]

def build_hls_command_multibitrate(session_id, segment_time=4, playlist_size=0):

    session_folder = os.path.join(HLS_PATH, session_id)
    os.makedirs(session_folder, exist_ok=True)

    return [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-fflags", "+genpts",
        "-i", IP_CAMERA_URL,

        "-filter_complex",
        "[0:v]split=3[v1][v2][v3];"
        "[v1]scale=w=1920:h=1080[v1out];"
        "[v2]scale=w=1280:h=720[v2out];"
        "[v3]scale=w=854:h=480[v3out]",

        "-map", "[v1out]",
        "-c:v:0", "libx264",
        "-b:v:0", "5000k",
        "-maxrate:v:0", "5350k",
        "-bufsize:v:0", "7500k",
        "-preset", "veryfast",

        "-map", "[v2out]",
        "-c:v:1", "libx264",
        "-b:v:1", "3000k",
        "-maxrate:v:1", "3210k",
        "-bufsize:v:1", "4500k",
        "-preset", "veryfast",

        "-map", "[v3out]",
        "-c:v:2", "libx264",
        "-b:v:2", "1000k",
        "-maxrate:v:2", "1070k",
        "-bufsize:v:2", "1500k",
        "-preset", "veryfast",

        "-f", "hls",

        "-hls_time", str(segment_time),
        "-hls_list_size", str(playlist_size),
        "-hls_flags", "independent_segments",

        "-master_pl_name", "master.m3u8",

        "-var_stream_map", "v:0 v:1 v:2",

        "-hls_segment_filename",
        os.path.join(session_folder, "v%v_segment_%03d.ts"),

        os.path.join(session_folder, "v%v.m3u8")
    ]

def build_hls_to_rtmp_command(hls_playlist_url):
    return [
        "ffmpeg",
        "-re",
        "-i", hls_playlist_url,

        "-c:v","libx264",
        "-preset","veryfast",
        "-b:v","2500k",

        "-c:a","aac",
        "-b:a","128k",

        "-f","flv",
        f"{YOUTUBE_RTMP_URL}/{YOUTUBE_STREAM_KEY}"
    ]

def monitor_hls_bridge(session_id, retry_time=3, max_retries=3):
    """
    Monitors the HLS-to-RTMP bridge:
    - waits for missing segments
    - detects end-of-stream
    - updates global status
    """
    global hls_bridge_status, hls_bridge_process

    playlist_path = os.path.join(HLS_PATH, session_id, "master.m3u8")
    if not os.path.exists(playlist_path):
        playlist_path = os.path.join(HLS_PATH, session_id, "playlist.m3u8")
        if not os.path.exists(playlist_path):
            hls_bridge_status = "error"
            print("Playlist not found!")
            return

    retries = {}
    hls_bridge_status = "connecting"

    while hls_bridge_process and hls_bridge_process.poll() is None:
        with open(playlist_path, 'r') as f:
            content = f.read()
        if "#EXT-X-ENDLIST" in content:
            print("HLS stream ended")
            hls_bridge_status = "ended"
            hls_bridge_process.terminate()
            break

        lines = content.splitlines()
        segments = [line.strip() for line in lines if line.strip().endswith(".ts")]

        for segment in segments:
            segment_path = os.path.join(HLS_PATH, session_id, segment)
            if not os.path.exists(segment_path):
                retries[segment] = retries.get(segment, 0) + 1
                if retries[segment] > max_retries:
                    print(f"[WARN] Skipping missing segment: {segment}")
                else:
                    print(f"[INFO] Segment missing: {segment}, retrying in {retry_time}s")
                    hls_bridge_status = "waiting_segment"
                    time.sleep(retry_time)


        if hls_bridge_process.stderr:
            for line in hls_bridge_process.stderr:
                line = line.strip()
                if "403 Forbidden" in line or "Invalid" in line.lower():
                    hls_bridge_status = "error"
                if "Connection refused" in line or "Broken pipe" in line:
                    hls_bridge_status = "error"

        if hls_bridge_status not in ["error", "waiting_segment", "ended"]:
            hls_bridge_status = "live"

        time.sleep(1)

    if hls_bridge_process and hls_bridge_process.poll() is not None and hls_bridge_status != "error":
        hls_bridge_status = "idle"

def build_ffmpeg_command():
    return [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-i", IP_CAMERA_URL,
        "-stream_loop", "-1",
        "-i", BACKGROUND_MUSIC,
        "-filter_complex", "[1:a]volume=0.5[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-r", FPS,
        "-c:v", VIDEO_CODEC,
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-b:v", BITRATE,
        "-pix_fmt", "yuv420p",
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-f", "flv",
        f"{YOUTUBE_RTMP_URL}/{YOUTUBE_STREAM_KEY}"
    ]

def monitor_ffmpeg():
    global status, ffmpeg_process
    for line in ffmpeg_process.stderr:
        print(line.strip())
        if "Press [q] to stop" in line or "frame=" in line:
            status = "live"
        if "403 Forbidden" in line or "Invalid" in line or "authentication" in line.lower():
            status = "error"
        if "Connection refused" in line or "Broken pipe" in line:
            status = "error"
    if ffmpeg_process.poll() is not None and status != "error":
        status = "idle"

@app.post("/start")
def start_stream():
    global ffmpeg_process, status
    if ffmpeg_process and ffmpeg_process.poll() is None:
        raise HTTPException(status_code=400, detail="Streaming already running")
    cmd = build_ffmpeg_command()
    try:
        ffmpeg_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        status = "connecting"
        threading.Thread(target=monitor_ffmpeg, daemon=True).start()
        return {"message": "Streaming started"}
    except Exception as e:
        status = "error"
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stop")
def stop_stream():
    global ffmpeg_process, status
    if not ffmpeg_process or ffmpeg_process.poll() is not None:
        raise HTTPException(status_code=400, detail="Streaming not running")
    ffmpeg_process.terminate()
    ffmpeg_process.wait()
    status = "idle"
    return {"message": "Streaming stopped"}

@app.get("/status")
def get_status():
    global ffmpeg_process, status
    if ffmpeg_process and ffmpeg_process.poll() is not None and status != "error":
        status = "idle"
    return {"status": status}

@app.post("/start_hls_auto")
def start_hls_auto(segment_time: int = 4, playlist_size: int = 0, adaptive: bool = False):
    global hls_process
    if hls_process and hls_process.poll() is None:
        raise HTTPException(status_code=400, detail="HLS already running")

    session_id = generate_session_id()

    if adaptive:
        cmd = build_hls_command_multibitrate(session_id, segment_time, playlist_size)
    else:
        cmd = build_hls_command(session_id, segment_time, playlist_size)

    hls_process = subprocess.Popen(cmd)
    return {
        "message": "HLS generation started",
        "session_id": session_id,
        "playlist_url": f"/hls/{session_id}/master.m3u8" if adaptive else f"/hls/{session_id}/playlist.m3u8"
    }

@app.post("/stop_hls")
def stop_hls():
    global hls_process
    if not hls_process or hls_process.poll() is not None:
        raise HTTPException(status_code=400, detail="HLS not running")

    hls_process.send_signal(signal.SIGINT)
    hls_process.wait()

    hls_process = None
    return {"message": "HLS generation stopped"}

@app.post("/start_recording")
def start_recording(session_id: str = "default"):
    global record_process
    if record_process and record_process.poll() is None:
        raise HTTPException(status_code=400, detail="Recording already running")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"recording_{timestamp}_{session_id}.mp4"
    file_path = os.path.join(RECORD_PATH, filename)

    cmd = [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-i", IP_CAMERA_URL,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-f", "mp4",
        file_path
    ]

    record_process = subprocess.Popen(
    cmd,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    text=True
    )
    record_process.file_path = file_path
    threading.Thread(target=monitor_recording, daemon=True).start()
    return {"message": "Recording started", "file": filename}

def monitor_recording():
    global record_process
    for line in record_process.stderr:
        print(line.strip())
        if "No space left on device" in line:
            print("Disk full error!")

@app.post("/stop_recording")
def stop_recording():
    global record_process

    if not record_process or record_process.poll() is not None:
        raise HTTPException(status_code=400, detail="Recording not running")

    try:
        # try graceful stop
        record_process.terminate()

        try:
            record_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("Force killing ffmpeg...")
            record_process.kill()

    finally:
        file_path = getattr(record_process, "file_path", None)
        record_process = None

    return {"message": "Recording stopped", "file": file_path}

@app.get("/record_status")
def record_status():
    global record_process
    if record_process and record_process.poll() is None:
        return {"status": "recording"}
    return {"status": "stopped"}

def get_credentials():
    if not os.path.exists(TOKEN_FILE):
        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_SECRET_FILE,
            scopes=["https://www.googleapis.com/auth/youtube.upload"]
        )
        auth_url, _ = flow.authorization_url(prompt='consent')
        print("Open this URL in your browser:", auth_url)
        code = input("Enter the authorization code here: ")
        creds = flow.fetch_token(code=code)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    else:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    return creds

def upload_video(file_path, title, description="Recorded Stream", privacy="private", tags=None):

    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    media = MediaFileUpload(
        file_path,
        chunksize=1024*1024,
        resumable=True
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": privacy
            }
        },
        media_body=media
    )

    response = None
    retry = 0
    max_retries = 3

    while response is None:
        try:

            status, response = request.next_chunk()

            if status:
                progress = int(status.progress() * 100)
                print(f"Upload progress: {progress}%")

        except Exception as e:

            retry += 1
            print(f"Upload error: {str(e)}")

            if retry > max_retries:
                raise Exception("Upload failed after retries")

            print(f"Retrying upload... Attempt {retry}/{max_retries}")

    print("Upload completed successfully")

    return response["id"]

@app.post("/upload_recording")
def upload_recording(
    file_name: str,
    title: str = "Camera Recording Upload",
    description: str = "Recorded Stream",
    privacy: str = "private",
    tags: Optional[List[str]] = None
):
    file_path = os.path.join(RECORD_PATH, file_name)

    if record_process and record_process.poll() is None:
        raise HTTPException(status_code=400, detail="Recording in progress. Stop first.")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    if tags is None:
        tags = ["camera", "stream"]
    if privacy not in ["public", "private", "unlisted"]:
        raise HTTPException(status_code=400, detail="Invalid privacy status.")
    
    video_id = upload_video(file_path, title, description, privacy, tags)
    return {"message": "Upload successful", "video_url": f"https://youtube.com/watch?v={video_id}"}

app.mount("/hls", StaticFiles(directory=HLS_PATH), name="hls")




@app.post("/start_hls_bridge")
def start_hls_bridge(session_id: str):
    """
    Start pushing an existing HLS playlist to YouTube Live.
    """
    global hls_bridge_process, hls_bridge_status

    if hls_bridge_process and hls_bridge_process.poll() is None:
        raise HTTPException(status_code=400, detail="HLS bridge already running")

    playlist_path = os.path.join(HLS_PATH, session_id, "master.m3u8")
    if not os.path.exists(playlist_path):
        playlist_path = os.path.join(HLS_PATH, session_id, "playlist.m3u8")
        if not os.path.exists(playlist_path):
            raise HTTPException(status_code=404, detail="HLS playlist not found")

    cmd = build_hls_to_rtmp_command(playlist_path)
    hls_bridge_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    hls_bridge_status = "connecting"
    threading.Thread(target=monitor_hls_bridge, daemon=True).start()
    return {"message": "HLS bridge started", "playlist": playlist_path}


@app.post("/stop_hls_bridge")
def stop_hls_bridge():
    """
    Stop the HLS-to-RTMP bridge.
    """
    global hls_bridge_process, hls_bridge_status
    if not hls_bridge_process or hls_bridge_process.poll() is not None:
        raise HTTPException(status_code=400, detail="HLS bridge not running")
    hls_bridge_process.terminate()
    hls_bridge_process.wait()
    hls_bridge_status = "idle"
    return {"message": "HLS bridge stopped"}


@app.get("/hls_bridge_status")
def get_hls_bridge_status():
    global hls_bridge_status, hls_bridge_process
    if hls_bridge_process and hls_bridge_process.poll() is not None and hls_bridge_status != "error":
        hls_bridge_status = "idle"
    return {"status": hls_bridge_status}


@app.post("/set_audio")
def set_audio(audio_source: str, mode: str = "mix", volume: float = 0.5):
    """
    Set external audio for the live stream.
    mode: "mix" or "replace"
    volume: 0.0 - 1.0 (for mix mode)
    """
    global AUDIO_SOURCE, AUDIO_MODE, AUDIO_VOLUME
    if mode not in ["mix", "replace"]:
        raise HTTPException(status_code=400, detail="Invalid mode")
    AUDIO_SOURCE = audio_source
    AUDIO_MODE = mode
    AUDIO_VOLUME = max(0.0, min(volume, 1.0))
    return {"message": "Audio source updated", "mode": AUDIO_MODE, "volume": AUDIO_VOLUME}
