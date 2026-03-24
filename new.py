from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
import os

CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def get_credentials():
    if not os.path.exists(TOKEN_FILE):
        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_SECRET_FILE, scopes=SCOPES
        )
        creds = flow.run_local_server(port=0, open_browser=False)  # <-- headless mode
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
        print("token.json generated successfully")
    else:
        print("token.json already exists")

get_credentials()