from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseDownload
from google_auth_httplib2 import AuthorizedHttp
from io import BytesIO
import httplib2
import os

# -----------------------------
# CONFIG
# -----------------------------
SCOPES = ["https://www.googleapis.com/auth/drive"]
SERVICE_ACCOUNT_FILE = "google_drive.json"


# -----------------------------
# CREATE DRIVE CLIENT
# -----------------------------
def get_drive_client():
    creds = None

    # ✅ Streamlit Cloud (only if secrets exist)
    try:
        import streamlit as st
        if "google_service_account" in st.secrets:
            creds = service_account.Credentials.from_service_account_info(
                st.secrets["google_service_account"],
                scopes=SCOPES
            )
    except Exception:
        pass  # Not running in Streamlit

    # ✅ Local fallback (YOUR ORIGINAL FLOW)
    if creds is None:
        if not os.path.exists(SERVICE_ACCOUNT_FILE):
            raise RuntimeError(
                "google_drive.json not found and Streamlit secrets not configured"
            )

        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=SCOPES
        )

    http = httplib2.Http(timeout=60)
    authed_http = AuthorizedHttp(creds, http=http)

    return build(
        "drive",
        "v3",
        http=authed_http,
        cache_discovery=False
    )


# -----------------------------
# LIST FILES
# -----------------------------
def list_files(folder_id, mime_contains=None):
    drive = get_drive_client()

    q = f"'{folder_id}' in parents and trashed = false"
    if mime_contains:
        q += f" and mimeType contains '{mime_contains}'"

    res = drive.files().list(
        q=q,
        fields="files(id, name)"
    ).execute()

    return res.get("files", [])


# -----------------------------
# DOWNLOAD FILE
# -----------------------------
def download_file(file_id: str) -> bytes:
    drive = get_drive_client()

    request = drive.files().get_media(fileId=file_id)
    fh = BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    fh.seek(0)
    return fh.read()
