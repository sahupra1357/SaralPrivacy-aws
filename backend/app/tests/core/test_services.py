"""Service contracts: these run against the autouse mocks, proving modules can rely on
the recorded-call shape, plus pure helpers that need no network."""

from app.services import email, storage


def test_email_render_uses_templates_dir() -> None:
    html = email.render("base.html", title="Hi", body_html="<p>Body</p>")
    assert "Hi" in html and "<p>Body</p>" in html


def test_email_send_is_recorded_by_fixture(mock_email) -> None:
    sent = email.send("a@example.com", "Subject", "<b>x</b>")
    assert sent.message_id.startswith("test-")
    assert mock_email.last()["to"] == ["a@example.com"]


def test_storage_public_url_and_recording(mock_storage) -> None:
    url = storage.put("infographics/x.png", b"png", "image/png")
    assert url.endswith("/infographics/x.png")
    assert storage.exists("infographics/x.png")
    assert mock_storage.last()["content_type"] == "image/png"


def test_health_check(client) -> None:
    assert client.get("/api/v1/utils/health-check/").json() == {"ok": True}


def test_ensure_bucket_is_a_noop_on_real_s3(monkeypatch) -> None:
    from app.services import storage as st

    monkeypatch.setattr(st.settings, "S3_ENDPOINT", "")
    monkeypatch.setattr(
        st, "_client", lambda: (_ for _ in ()).throw(AssertionError("no client on AWS"))
    )
    assert st.ensure_bucket() is False


def test_ensure_bucket_creates_bucket_and_public_prefix_policy(monkeypatch) -> None:
    import json

    from botocore.exceptions import ClientError

    from app.services import storage as st

    calls: list[tuple[str, dict]] = []

    class FakeS3:
        def head_bucket(self, **kw):
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

        def create_bucket(self, **kw):
            calls.append(("create", kw))

        def put_bucket_policy(self, **kw):
            calls.append(("policy", kw))

    monkeypatch.setattr(st.settings, "S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setattr(st.settings, "S3_BUCKET", "sp")
    monkeypatch.setattr(st, "_client", lambda: FakeS3())
    assert st.ensure_bucket() is True
    assert calls[0] == ("create", {"Bucket": "sp"})
    policy = json.loads(calls[1][1]["Policy"])
    assert policy["Statement"][0]["Resource"] == ["arn:aws:s3:::sp/infographics/*"]
