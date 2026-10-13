import os
import pytest
from ocr_helper import OCRHelper
from schemas import ReviewQueueApproveRequest, ReviewQueueRejectRequest
from scripts.backup_db import backup_database


def test_health_check_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["healthy", "degraded"]
    assert data["database"] == "connected"
    assert "version" in data
    assert "timestamp" in data


def test_ocr_confidence_scoring():
    # Test OCR helper with confidence output
    result = OCRHelper.extract_text_and_confidence("comprobante_jenny.png")
    assert "text" in result
    assert "confidence" in result
    assert isinstance(result["confidence"], float)
    assert 0.0 <= result["confidence"] <= 1.0
    assert "is_doubtful" in result
    assert "flags" in result


def test_monthly_closing_report_calculation(test_sm, client, admin_headers):
    # 1. Direct model calculation
    closing_data = test_sm.get_monthly_closing_report_data(month="Abril", year=2026)
    assert "period" in closing_data
    assert "initial_balance" in closing_data
    assert "total_income" in closing_data
    assert "total_expenses" in closing_data
    assert "closing_balance" in closing_data
    assert "reconciliation_difference" in closing_data

    # 2. Endpoint query
    response = client.get("/api/reports/monthly-closing?month=Abril&year=2026", headers=admin_headers)
    assert response.status_code == 200
    res_json = response.json()
    assert res_json["period"] == "Abril 2026"
    assert "closing_balance" in res_json


def test_reconciliation_review_queue(test_sm, client, admin_headers):
    unit_id = "TEST-QUEUE-101"
    test_sm.add_or_update_unit(
        unit_id=unit_id,
        owner="Usuario Cola Revision",
        phone1="0999111222",
        email1="cola@test.com",
        aliquot_base=70.0
    )

    # 1. Create aliquot in manual validation
    aliquot_id = f"{unit_id}-2026-Julio"
    aliquot = {
        "id": aliquot_id,
        "unit": unit_id,
        "month": "Julio",
        "year": 2026,
        "amount": 70.0,
        "late_fee": 0.0,
        "paid_amount": 0.0,
        "payment_date": "2026-07-05",
        "reference": "REF-DUDOSA-999",
        "status": "Validación Manual",
        "comprobante_url": "/static/uploads/comprobante_test.jpg",
        "ocr_confidence": 0.55
    }
    test_sm.data["aliquots"].append(aliquot)
    test_sm.sync()

    # 2. Query review queue
    q_res = client.get("/api/reconcile/review-queue", headers=admin_headers)
    assert q_res.status_code == 200
    queue_data = q_res.json()
    assert queue_data["status"] == "success"
    assert any(item["id"] == aliquot_id for item in queue_data["queue"])

    # 3. Test Reject
    rej_res = client.post(
        f"/api/reconcile/review-queue/{aliquot_id}/reject",
        json={"reason": "Comprobante ilegible"},
        headers=admin_headers
    )
    assert rej_res.status_code == 200
    assert rej_res.json()["aliquot"]["status"] == "Pendiente"

    # Cleanup
    test_sm.data["aliquots"] = [a for a in test_sm.data["aliquots"] if a["id"] != aliquot_id]
    test_sm.delete_unit(unit_id)


def test_database_backup_utility():
    import sqlite3
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_db = os.path.join(base_dir, "condominium.db")
    created_dummy = False
    if not os.path.exists(test_db):
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test_backup (id INTEGER PRIMARY KEY, note TEXT)")
        cursor.execute("INSERT INTO test_backup (note) VALUES ('test_entry')")
        conn.commit()
        conn.close()
        created_dummy = True

    try:
        backup_path = backup_database()
        assert backup_path is not None, "El respaldo de la base de datos no debió ser None"
        assert os.path.exists(backup_path), f"El archivo de respaldo {backup_path} no fue creado en disco"
        # Clean up test backup file
        if os.path.exists(backup_path):
            os.remove(backup_path)
    finally:
        if created_dummy and os.path.exists(test_db):
            try:
                os.remove(test_db)
            except Exception:
                pass
