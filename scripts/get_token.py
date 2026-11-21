#!/usr/bin/env python3
"""One-time, on your own PC: log in with the Google account that will OWN the files and print the authorized-user JSON
to store in Secret Manager as GOOGLE_OAUTH_TOKEN_JSON.   Needs credentials.json (OAuth client "Desktop app") next to app.py.
In the OAuth consent screen set the publishing status to "In production" - in "Testing" Google expires the refresh token after 7 days."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gauth
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(gauth.CRED, gauth.SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print(creds.to_json())
