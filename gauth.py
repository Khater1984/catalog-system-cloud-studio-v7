"""Google credentials for Drive / Sheets.

Order of preference (first one found wins):
  1. GOOGLE_OAUTH_TOKEN_JSON      - authorized-user JSON (with refresh token) of the app owner. Recommended; also works with a normal Gmail account.
  2. GOOGLE_SERVICE_ACCOUNT_JSON  - service-account key JSON (needs a Shared Drive or a folder shared with the account).
  3. GOOGLE_USE_ADC=1             - the Cloud Run service identity (Application Default Credentials).
  4. credentials.json next to app.py + data/token.json (interactive browser login; local use only).
"""
import os, json
import db

DRIVE_SCOPE = os.environ.get("GOOGLE_DRIVE_SCOPE", "https://www.googleapis.com/auth/drive.file")
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SCOPES = [DRIVE_SCOPE, SHEETS_SCOPE]
CRED = os.path.join(db.BASE, "credentials.json")
TOKEN = os.path.join(db.DATA, "token.json")


def configured():
    return bool(os.environ.get("GOOGLE_OAUTH_TOKEN_JSON") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
                or os.environ.get("GOOGLE_USE_ADC") == "1" or os.path.exists(TOKEN) or os.path.exists(CRED))


def credentials(scopes=None):
    scopes = scopes or SCOPES
    from google.auth.transport.requests import Request
    tok = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON")
    if tok:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_info(json.loads(tok), scopes)
        if not creds.valid:
            creds.refresh(Request())
        return creds
    sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa:
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_info(json.loads(sa), scopes=scopes)
    if os.environ.get("GOOGLE_USE_ADC") == "1":
        import google.auth
        return google.auth.default(scopes=scopes)[0]
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds = Credentials.from_authorized_user_file(TOKEN, scopes) if os.path.exists(TOKEN) else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CRED):
                raise RuntimeError("Google credentials are not configured (see README_CLOUD_AR.md)")
            creds = InstalledAppFlow.from_client_secrets_file(CRED, scopes).run_local_server(port=0)
        open(TOKEN, "w").write(creds.to_json())
    return creds
