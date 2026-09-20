"""PII redaction (spec §7). Ported from frontend/lib/chat/redact.test.ts."""

from app.services.chat.redact import redact, redact_text


def test_redacts_an_email_address() -> None:
    result = redact("write to priya.sharma@example.co.in please")
    assert result.text == "write to [email] please"
    assert result.redactions == 1


def test_redacts_an_aadhaar_number_before_treating_it_as_a_phone() -> None:
    result = redact("my aadhaar is 1234 5678 9012")
    assert result.text == "my aadhaar is [aadhaar]"
    assert result.redactions == 1


def test_redacts_a_pan() -> None:
    assert redact("PAN ABCDE1234F on file").text == "PAN [pan] on file"


def test_redacts_a_phone_number() -> None:
    assert redact("call +91 98765 43210 today").text == "call [phone] today"


def test_counts_every_redaction_in_one_message() -> None:
    result = redact("a@b.com and 9876543210 and ABCDE1234F")
    assert result.redactions == 3
    assert "a@b.com" not in result.text


def test_leaves_clean_text_untouched() -> None:
    clean = "Does DPDPA apply to a 5 person startup?"
    result = redact(clean)
    assert result.text == clean
    assert result.redactions == 0


def test_redact_text_returns_only_the_text() -> None:
    assert redact_text("mail me at x@y.io") == "mail me at [email]"
