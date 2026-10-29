from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ingestion.email_parser import parse_gmail_message

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailClient:
    def __init__(self, credentials_file: Path, token_file: Path, user: str = "me", cursor_file: Path | None = None):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.user = user
        self.cursor_file = cursor_file

    def _service(self):
        credentials = Credentials.from_authorized_user_file(self.token_file, SCOPES) if self.token_file.exists() else None
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError:
                # Expired or revoked (Google expires Testing-mode tokens after 7 days): sign in again.
                credentials = None
        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
            credentials = flow.run_local_server(port=0)
        self.token_file.write_text(credentials.to_json(), encoding="utf-8")
        return build("gmail", "v1", credentials=credentials)

    def read_cursor(self) -> int | None:
        """Return the internalDate (ms) of the last processed message, if any."""
        if not self.cursor_file or not self.cursor_file.exists():
            return None
        value = float(self.cursor_file.read_text(encoding="utf-8").strip())
        # Older cursor files stored a seconds timestamp.
        return int(value * 1000) if value < 1e11 else int(value)

    def save_cursor(self, internal_date: int) -> None:
        if self.cursor_file:
            self.cursor_file.write_text(str(internal_date), encoding="utf-8")

    def fetch_billing_emails(self, since: datetime | None = None) -> list[dict]:
        """Return parsed billing-like messages newer than the cursor, oldest first.

        The cursor is not advanced here; the caller saves it once each message is processed.
        """
        cursor = self.read_cursor() if since is None else None
        if cursor is not None:
            since = datetime.fromtimestamp(cursor / 1000, timezone.utc)
        since = since or datetime.now(timezone.utc) - timedelta(days=90)
        query = f"after:{int(since.timestamp())} (receipt OR invoice OR subscription OR trial)"
        service = self._service()
        messages = []
        page_token = None
        while True:
            result = service.users().messages().list(userId=self.user, q=query, pageToken=page_token, maxResults=100).execute()
            for item in result.get("messages", []):
                raw = service.users().messages().get(userId=self.user, id=item["id"], format="raw").execute()
                parsed = parse_gmail_message(raw)
                # after: is second-granular, so drop anything at or before the cursor itself.
                if cursor is None or parsed["internal_date"] > cursor:
                    messages.append(parsed)
            page_token = result.get("nextPageToken")
            if not page_token:
                return sorted(messages, key=lambda message: message["internal_date"])
