📄 Streaming System (RTMP + HLS + YouTube Integration)
📌 Overview

This project allows you to:

Stream a camera feed (RTSP / HTTP) to YouTube Live (RTMP)
Record live streams
Generate HLS output (m3u8 + segments)
Re-stream HLS to YouTube
Upload recorded videos to YouTube (VOD)
Replace or mix audio during live streaming
⚙️ Features
🎥 Live streaming to YouTube (RTMP)
💾 Recording live streams (MP4/MKV)
📦 HLS generation for playback and distribution
🔁 HLS → RTMP bridge streaming
🎧 Audio replacement / mixing
📤 Upload recorded videos to YouTube
🧰 Requirements
Ubuntu / Linux (recommended)
Python 3.8+
FFmpeg installed
Internet connection
YouTube Live account + Stream Key
YouTube Data API credentials (for uploads)
📦 Installation
1. Clone the project
git clone <your-repo-url>
cd <project-folder>
2. Create virtual environment
python3 -m venv venv
source venv/bin/activate
3. Install dependencies
pip install -r requirements.txt
4. Install FFmpeg
sudo apt update
sudo apt install ffmpeg

Verify:

ffmpeg -version
⚙️ Configuration

Create or update your config file (example variables):

IP_CAMERA_URL = "rtsp://admin:%26%26Shared1122%26%26@10.1.5.72:554/H264/ch1/sub/av_stream"
YOUTUBE_RTMP_URL = "rtmp://a.rtmp.youtube.com/live2"
YOUTUBE_STREAM_KEY = "j11z-egxh-zu9k-f5pj-0b4y"
OUTPUT_DIR=/path/to/output

For YouTube uploads:

Enable YouTube Data API
Download OAuth credentials JSON
Place it in project directory
▶️ Running the Application
uvicorn main:app --host 0.0.0.0 --port 8000

Open in browser:

http://localhost:8000