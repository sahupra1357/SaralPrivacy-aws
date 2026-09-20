import uuid

from freezegun import freeze_time

from app.core import security


def test_password_hash_roundtrip() -> None:
    h = security.hash_password("correct horse battery")
    assert h != "correct horse battery"
    assert security.verify_password("correct horse battery", h)
    assert not security.verify_password("wrong", h)


def test_password_length_rule_matches_set_password_route() -> None:
    assert not security.password_ok("x" * 11)
    assert security.password_ok("x" * 12)
    assert security.password_ok("x" * 128)
    assert not security.password_ok("x" * 129)


def test_pending_token_has_mfa_purpose_and_expires_in_ten_minutes() -> None:
    uid = uuid.uuid4()
    with freeze_time("2026-09-18 10:00:00"):
        tok = security.create_pending_token(uid)
        claims = security.decode_token(tok, "mfa")
        assert claims and claims["sub"] == str(uid)
        assert security.decode_token(tok, "access") is None  # purpose mismatch
    with freeze_time("2026-09-18 10:11:00"):
        assert security.decode_token(tok, "mfa") is None


def test_access_token_carries_role_jti_and_lasts_eight_hours() -> None:
    uid, sid = uuid.uuid4(), uuid.uuid4()
    with freeze_time("2026-09-18 10:00:00"):
        tok = security.create_access_token(uid, "blogger", sid, name="Asha")
        claims = security.decode_token(tok, "access")
        assert (
            claims
            and claims["role"] == "blogger"
            and claims["jti"] == str(sid)
            and claims["name"] == "Asha"
        )
    with freeze_time("2026-09-18 17:59:00"):
        assert security.decode_token(tok, "access") is not None
    with freeze_time("2026-09-18 18:01:00"):
        assert security.decode_token(tok, "access") is None


def test_tampered_token_is_rejected() -> None:
    tok = security.create_access_token(uuid.uuid4(), "admin", uuid.uuid4())
    head, body, sig = tok.split(".")
    assert security.decode_token(f"{head}.{body}.{sig[:-2]}xx", "access") is None


def test_totp_secret_encrypts_at_rest() -> None:
    enc = security.encrypt_secret("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in enc
    assert security.decrypt_secret(enc) == "JBSWY3DPEHPK3PXP"
    assert security.decrypt_secret("garbage") is None


def test_opaque_token_hash_is_stable_and_one_way() -> None:
    t = security.new_opaque_token()
    assert len(t) >= 32
    assert security.hash_opaque_token(t) == security.hash_opaque_token(t)
    assert security.hash_opaque_token(t) != t


def test_verify_accepts_existing_standard_bcrypt_hashes() -> None:
    # passlib (the previous implementation) produced plain $2b$ hashes by calling the
    # same bcrypt library, so any stored hash of that shape must keep verifying.
    import bcrypt

    stored = bcrypt.hashpw(b"correct horse battery", bcrypt.gensalt(rounds=12)).decode()
    assert stored.startswith("$2b$12$")
    assert security.verify_password("correct horse battery", stored)
    assert not security.verify_password("wrong horse battery", stored)


def test_verify_rejects_malformed_hash() -> None:
    assert not security.verify_password("anything", "not-a-bcrypt-hash")


def test_passwords_beyond_72_bytes_are_truncated_like_before() -> None:
    long_pw = "x" * 100
    h = security.hash_password(long_pw)
    assert security.verify_password("x" * 72, h)
    assert security.verify_password(long_pw, h)
