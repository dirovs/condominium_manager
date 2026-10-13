import time
import pytest
from fastapi import HTTPException
from security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
    RateLimiter,
    check_unit_permission,
    verify_meta_signature,
    verify_twilio_signature
)


def test_jwt_create_and_decode():
    payload = {"sub": "user@test.com", "role": "admin", "unit": "101"}
    token = create_access_token(payload)
    decoded = decode_access_token(token)

    assert decoded["sub"] == "user@test.com"
    assert decoded["role"] == "admin"
    assert decoded["unit"] == "101"
    assert "exp" in decoded


def test_jwt_tampering():
    token = create_access_token({"sub": "admin@condo.com", "role": "admin"})
    parts = token.split(".")
    tampered_token = f"{parts[0]}.{parts[1]}tampered.{parts[2]}"

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(tampered_token)
    assert exc_info.value.status_code == 401


def test_bcrypt_hashing_and_verification():
    raw_pwd = "MySecretPassword2026!"
    hashed = get_password_hash(raw_pwd)

    assert hashed.startswith(("$2b$", "$2a$", "$2y$", "sha256$"))
    is_valid, _ = verify_password(raw_pwd, hashed)
    assert is_valid is True
    is_invalid, _ = verify_password("WrongPassword", hashed)
    assert is_invalid is False


def test_legacy_sha256_migration():
    import hashlib
    raw_pwd = "LegacyPassword123"
    legacy_sha256 = hashlib.sha256(raw_pwd.encode("utf-8")).hexdigest()

    # Legacy hash should still verify and flag for migration
    is_valid, needs_rehash = verify_password(raw_pwd, legacy_sha256)
    assert is_valid is True
    assert needs_rehash is True

    is_invalid, _ = verify_password("WrongPass", legacy_sha256)
    assert is_invalid is False


def test_rate_limiter():
    limiter = RateLimiter(max_requests=3, window_seconds=60, lockout_seconds=600)
    key = "test_ip_192_168_1_50"

    # 3 allowed attempts
    assert limiter.check(key) is True
    assert limiter.check(key) is True
    assert limiter.check(key) is True

    # 4th attempt should raise 429
    with pytest.raises(HTTPException) as exc:
        limiter.check(key)
    assert exc.value.status_code == 429

    # Reset allows immediate access again
    limiter.reset(key)
    assert limiter.check(key) is True


def test_unit_permission_isolation():
    # Admin has access to all units
    admin_user = {"role": "admin", "unit": None}
    check_unit_permission(admin_user, "701")  # Should not raise

    # Owner has access only to their own unit
    owner_user = {"role": "owner", "unit": "701"}
    check_unit_permission(owner_user, "701")  # Should not raise

    # Accessing different unit should raise 403
    with pytest.raises(HTTPException) as exc_info:
        check_unit_permission(owner_user, "102")
    assert exc_info.value.status_code == 403


def test_webhook_signatures():
    import hmac
    import hashlib

    # Meta signature
    secret = "my_meta_secret_key"
    payload = b'{"object": "whatsapp_business_account"}'
    expected_sig = "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    assert verify_meta_signature(payload, expected_sig, secret) is True
    assert verify_meta_signature(payload, "sha256=invalid_hash", secret) is False

    # Twilio signature with fake signature should be False
    twilio_token = "my_twilio_auth_token"
    url = "https://mycondo.com/api/whatsapp/webhook"
    params = {"Body": "Hola", "From": "whatsapp:+593987654321"}
    sig = verify_twilio_signature(twilio_token, "fake_sig", url, params)
    assert sig is False
