from google_auth_oauthlib.flow import InstalledAppFlow
import os

CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def get_credentials():
    if not os.path.exists(TOKEN_FILE):
        print("Generating new token...")

        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_SECRET_FILE,
            scopes=SCOPES
        )

        creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

        print("token.json generated successfully")

    else:
        print("token.json already exists")

if __name__ == "__main__":
    get_credentials()