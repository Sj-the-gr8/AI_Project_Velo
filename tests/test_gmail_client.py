import base64
from email.message import EmailMessage

from ingestion.gmail_client import SCOPES, GmailClient


def raw_message(message_id, internal_date, body):
    message = EmailMessage()
    message["Subject"] = "Receipt"
    message.set_content(body)
    return {"id": message_id, "internalDate": str(internal_date), "raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}


class FakeGmailService:
    """Mimics service.users().messages().list/get with two result pages."""

    def __init__(self, pages):
        self.pages = pages
        self.queries = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, pageToken, maxResults):
        self.queries.append(q)
        index = int(pageToken or 0)
        page = {"messages": [{"id": m["id"]} for m in self.pages[index]]}
        if index + 1 < len(self.pages):
            page["nextPageToken"] = str(index + 1)
        self._result = page
        return self

    def get(self, userId, id, format):
        self._result = next(m for page in self.pages for m in page if m["id"] == id)
        return self

    def execute(self):
        return self._result


def client_with(tmp_path, monkeypatch, service):
    client = GmailClient(tmp_path / "secret.json", tmp_path / "token.json", cursor_file=tmp_path / "cursor")
    monkeypatch.setattr(client, "_service", lambda: service)
    return client


def test_scope_is_readonly():
    assert SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_paginates_sorts_oldest_first_and_leaves_cursor_to_caller(tmp_path, monkeypatch):
    service = FakeGmailService([[raw_message("b", 2000, "second")], [raw_message("a", 1000, "first")]])
    client = client_with(tmp_path, monkeypatch, service)
    messages = client.fetch_billing_emails()
    assert [(m["message_id"], m["body"]) for m in messages] == [("a", "first"), ("b", "second")]
    assert not (tmp_path / "cursor").exists()


def test_cursor_skips_already_processed_messages(tmp_path, monkeypatch):
    # Realistic Gmail internalDate values (ms since epoch).
    service = FakeGmailService([[raw_message("a", 1790000000000, "old"), raw_message("b", 1790000005000, "new")]])
    client = client_with(tmp_path, monkeypatch, service)
    client.save_cursor(1790000000000)
    assert [m["message_id"] for m in client.fetch_billing_emails()] == ["b"]
    assert service.queries[0].startswith("after:1790000000 ")


def test_reads_legacy_seconds_cursor(tmp_path, monkeypatch):
    client = client_with(tmp_path, monkeypatch, FakeGmailService([[]]))
    (tmp_path / "cursor").write_text("1790244261.297622", encoding="utf-8")
    assert client.read_cursor() == 1790244261297


def test_expired_refresh_token_falls_back_to_sign_in(tmp_path, monkeypatch):
    import ingestion.gmail_client as gmail_client
    from google.auth.exceptions import RefreshError

    class Expired:
        expired, refresh_token, valid = True, "old", False

        def refresh(self, request):
            raise RefreshError("invalid_grant: Token has been expired or revoked.")

    class Fresh:
        valid = True

        def to_json(self):
            return '{"token": "new"}'

    class Flow:
        def run_local_server(self, port):
            return Fresh()

    (tmp_path / "token.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gmail_client.Credentials, "from_authorized_user_file", lambda *args: Expired())
    monkeypatch.setattr(gmail_client.InstalledAppFlow, "from_client_secrets_file", lambda *args: Flow())
    monkeypatch.setattr(gmail_client, "build", lambda *args, **kwargs: "service")
    client = GmailClient(tmp_path / "secret.json", tmp_path / "token.json")
    assert client._service() == "service"
    assert (tmp_path / "token.json").read_text(encoding="utf-8") == '{"token": "new"}'
