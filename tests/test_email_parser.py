import base64
from email.message import EmailMessage

from ingestion.email_parser import extract_plain_text, parse_gmail_message


def multipart(plain=None, html=None, attachment=False):
    message = EmailMessage()
    message["Subject"] = "Receipt"
    if plain is not None:
        message.set_content(plain)
    if html is not None:
        if plain is None:
            message.set_content(html, subtype="html")
        else:
            message.add_alternative(html, subtype="html")
    if attachment:
        message.add_attachment(b"Invoice text inside attachment", maintype="text", subtype="plain", filename="invoice.txt")
    return message


def test_prefers_plain_text_over_html():
    assert extract_plain_text(multipart(plain="Plain body", html="<p>HTML body</p>")) == "Plain body"


def test_falls_back_to_stripped_html():
    html = "<html><style>p{color:red}</style><body><p>Total:&nbsp;<b>$9.99</b></p><script>x()</script></body></html>"
    assert extract_plain_text(multipart(html=html)) == "Total: $9.99"


def test_skips_attachments():
    assert extract_plain_text(multipart(plain="Body", attachment=True)) == "Body"


def test_parse_gmail_raw_resource():
    raw = base64.urlsafe_b64encode(multipart(plain="Your receipt").as_bytes()).decode()
    parsed = parse_gmail_message({"id": "abc", "internalDate": "1767225600000", "raw": raw})
    assert parsed == {"message_id": "abc", "internal_date": 1767225600000, "received_date": "2026-01-01T00:00:00+00:00",
                      "subject": "Receipt", "sender": "", "body": "Your receipt"}
