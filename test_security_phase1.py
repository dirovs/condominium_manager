import os
os.environ["TESTING"] = "True"
import time
import hashlib
import base64
import hmac
from datetime import datetime, timedelta
from fastapi import HTTPException, Request

from app import app, sm, login, LoginRequest
from security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
    check_unit_permission,
    RateLimiter,
    verify_meta_signature,
    verify_twilio_signature
)
from sqlmodel import Session, select
from models import User

def make_request(headers=None, client_host="127.0.0.1"):
    h = []
    if headers:
        for k, v in headers.items():
            h.append((k.lower().encode('latin-1'), v.encode('latin-1')))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": h,
        "client": (client_host, 12345),
    }
    return Request(scope)

def test_jwt_generation_and_validation():
    print("Testing JWT Token Creation, Verification, and Tamper Resistance...")
    payload = {
        "sub": "1712345678",
        "email": "user@condo.com",
        "role": "owner",
        "name": "Carlos Gomez",
        "unit_id": "301"
    }
    token = create_access_token(payload)
    assert token and isinstance(token, str)

    decoded = decode_access_token(token)
    assert decoded["sub"] == "1712345678"
    assert decoded["role"] == "owner"
    assert decoded["unit_id"] == "301"

    # Tampered token should fail
    tampered = token[:-4] + "abcd"
    try:
        decode_access_token(tampered)
        assert False, "Tampered token should raise HTTPException 401"
    except HTTPException as e:
        assert e.status_code == 401

    # Expired token should fail
    expired_token = create_access_token(payload, expires_delta=timedelta(seconds=-10))
    try:
        decode_access_token(expired_token)
        assert False, "Expired token should raise HTTPException 401"
    except HTTPException as e:
        assert e.status_code == 401
        assert "expirado" in e.detail.lower()

    print("[OK] JWT generation and validation test PASSED!")

def test_password_hashing_and_legacy_migration():
    print("Testing Bcrypt Hashing and Transparent Migration from Legacy SHA-256...")
    plain_pwd = "MyStrongPassword2026!"

    # 1. New hash is salted and secure
    hashed = get_password_hash(plain_pwd)
    assert hashed != plain_pwd
    assert not hashed.startswith("sha256$") or "$" in hashed

    valid, needs_rehash = verify_password(plain_pwd, hashed)
    assert valid is True
    assert needs_rehash is False

    # Incorrect password fails
    invalid, _ = verify_password("WrongPassword", hashed)
    assert invalid is False

    # 2. Legacy SHA-256 hash detection and auto-migration
    legacy_sha256 = hashlib.sha256(plain_pwd.encode('utf-8')).hexdigest()
    valid_legacy, needs_rehash_legacy = verify_password(plain_pwd, legacy_sha256)
    assert valid_legacy is True
    assert needs_rehash_legacy is True

    print("[OK] Password hashing and legacy migration test PASSED!")

def test_login_and_backdoor_elimination():
    print("Testing Login Endpoint with JWT Response and Backdoor Elimination...")

    # Ensure superadmin exists in DB with known password
    with Session(sm.engine) as session:
        sa = session.exec(select(User).where(User.email == "superadmin@condo.com")).first()
        if not sa:
            sa = User(
                cedula="9999999999",
                email="superadmin@condo.com",
                name="Super Administrador",
                role="superadmin",
                password_hash=get_password_hash("SuperSecretPass2026!"),
                is_active=True
            )
            session.add(sa)
        else:
            sa.password_hash = get_password_hash("SuperSecretPass2026!")
            session.add(sa)
        session.commit()

    # 1. Login with correct password returns JWT token
    req_good = LoginRequest(
        cedula="9999999999",
        email="superadmin@condo.com",
        role="superadmin",
        password="SuperSecretPass2026!"
    )
    resp = login(req_good, make_request())
    assert resp["status"] == "success"
    assert "access_token" in resp
    assert resp["token_type"] == "bearer"
    assert resp["user"]["role"] == "superadmin"

    # 2. Verify old backdoor password (admin123) is REJECTED
    req_bad = LoginRequest(
        cedula="9999999999",
        email="superadmin@condo.com",
        role="superadmin",
        password="admin123"
    )
    try:
        login(req_bad, make_request())
        assert False, "Backdoor password admin123 should be rejected!"
    except HTTPException as e:
        assert e.status_code == 400
        assert "incorrect" in e.detail.lower()

    print("[OK] Login and backdoor elimination test PASSED!")

def test_rate_limiter():
    print("Testing Rate Limiter Brute-Force Protection...")
    limiter = RateLimiter(max_requests=3, window_seconds=60, lockout_seconds=300)
    key = "test-client-ip"

    # 3 allowed requests
    assert limiter.check(key) is True
    assert limiter.check(key) is True
    assert limiter.check(key) is True

    # 4th request exceeds limit -> 429
    try:
        limiter.check(key)
        assert False, "Expected 429 Too Many Requests"
    except HTTPException as e:
        assert e.status_code == 429
        assert "excedido" in e.detail.lower() or "bloqueado" in e.detail.lower()

    # Reset allows again
    limiter.reset(key)
    assert limiter.check(key) is True
    print("[OK] Rate Limiter test PASSED!")

def test_unit_isolation_permissions():
    print("Testing Condómino Unit Access Isolation...")
    tenant_user = {
        "sub": "1720001111",
        "role": "tenant",
        "unit_id": "101"
    }

    # Accessing own unit 101 -> Allowed
    assert check_unit_permission(tenant_user, "101") is True
    assert check_unit_permission(tenant_user, "Depto 101") is True

    # Accessing different unit 202 -> 403 Forbidden
    try:
        check_unit_permission(tenant_user, "202")
        assert False, "Accessing other unit should raise 403"
    except HTTPException as e:
        assert e.status_code == 403
        assert "Acceso denegado" in e.detail

    # Superadmin can access any unit
    admin_user = {
        "sub": "9999999999",
        "role": "superadmin",
        "unit_id": None
    }
    assert check_unit_permission(admin_user, "202") is True
    assert check_unit_permission(admin_user, "305") is True

    print("[OK] Unit Isolation Permissions test PASSED!")

def test_webhook_signatures():
    print("Testing Twilio and Meta WhatsApp Webhook Signatures...")

    # Meta signature
    secret = "my_meta_secret_key"
    body = b'{"entry":[{"changes":[{"value":{"messages":[{"from":"593999999999","text":{"body":"Hola"}}]}}]}]}'
    expected_meta_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_meta_signature(body, expected_meta_sig, secret) is True
    assert verify_meta_signature(body, "sha256=invalidhash", secret) is False

    # Twilio signature
    twilio_token = "1234567890abcdef"
    url = "https://mycondo.onrender.com/api/whatsapp/webhook"
    params = {"Body": "Pago alicuota", "From": "whatsapp:+593999999999"}

    data = url + "".join([f"{k}{params[k]}" for k in sorted(params.keys())])
    valid_tw_sig = base64.b64encode(hmac.new(twilio_token.encode(), data.encode(), hashlib.sha1).digest()).decode()

    assert verify_twilio_signature(twilio_token, valid_tw_sig, url, params) is True
    assert verify_twilio_signature(twilio_token, "invalid_sig", url, params) is False

    print("[OK] Webhook Signatures test PASSED!")

def test_static_protection_and_data_isolation():
    print("Testing Static File Protection and Sensitive Data Isolation by Role...")
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # 1. Resident tokens
    resident_token = create_access_token({"sub": "resident1", "role": "owner", "unit": "101"})
    res_headers = {"Authorization": f"Bearer {resident_token}"}

    admin_token = create_access_token({"sub": "admin1", "role": "admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Resident blocked from accessing global reports in /static/receipts/
    res = client.get("/static/receipts/reporte_deudores_test.xlsx", headers=res_headers)
    assert res.status_code == 403, f"Expected 403, got {res.status_code}"

    # 3. Resident blocked from accessing another unit's uploads in /static/uploads/
    res = client.get("/static/uploads/202/2026/10/comprobante_123.jpg", headers=res_headers)
    assert res.status_code == 403, f"Expected 403, got {res.status_code}"

    # 4. Resident cannot view condo bank statements (admin only)
    res = client.get("/api/bank-statement", headers=res_headers)
    assert res.status_code == 403, f"Expected 403, got {res.status_code}"

    # 5. Resident cannot export debtors CSV (admin only)
    res = client.get("/api/debtors/export/csv", headers=res_headers)
    assert res.status_code == 403, f"Expected 403, got {res.status_code}"

    # 6. Admin CAN access bank statement and export debtors
    res_admin_bank = client.get("/api/bank-statement", headers=admin_headers)
    assert res_admin_bank.status_code == 200

    # 7. Resident GET /api/debtors returns ONLY their own unit
    res_debtors = client.get("/api/debtors", headers=res_headers)
    assert res_debtors.status_code == 200
    debtors_data = res_debtors.json()
    assert all(str(d.get("unit")) == "101" for d in debtors_data)

    # 8. Resident GET /api/units returns ONLY their own unit
    res_units = client.get("/api/units", headers=res_headers)
    assert res_units.status_code == 200
    units_data = res_units.json()
    assert all(str(u.get("id")) == "101" for u in units_data)

    print("[OK] Static File Protection & Data Isolation test PASSED!")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("STARTING SECURITY PHASE 1 TEST SUITE")
    print("="*50)
    test_jwt_generation_and_validation()
    test_password_hashing_and_legacy_migration()
    test_login_and_backdoor_elimination()
    test_rate_limiter()
    test_unit_isolation_permissions()
    test_webhook_signatures()
    test_static_protection_and_data_isolation()
    print("\n" + "="*50)
    print("ALL PHASE 1 SECURITY TESTS PASSED SUCCESSFULLY!")
    print("="*50 + "\n")
