import os
import json
from sheets_manager import SheetsManager
from notifications import NotificationManager
from receipt_processor import ReceiptProcessor
from app import test_email_endpoint, EmailTestRequest

def run_test():
    sm = SheetsManager()
    pdf_path = os.path.join(os.path.dirname(__file__), "static", "receipts", "test_email_receipt.pdf")
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

    # 1. Test Receipt notification dispatch
    ReceiptProcessor.generate_receipt_pdf(
        dest_path=pdf_path,
        receipt_no="REC-TEST-EMAIL-01",
        resident_name="Jenny Portilla",
        unit="701",
        concept="Pago de Alícuota Ordinaria - Octubre 2026",
        amount=25.0,
        late_fee=0.0,
        reference="REF-TEST-EMAIL-123",
        payment_date="2026-10-02",
        sm=sm
    )

    print("Testing NotificationManager.send_receipt_notifications with real SMTP dispatch...")
    results = NotificationManager.send_receipt_notifications(
        sm=sm,
        unit_id="701",
        receipt_pdf_path=pdf_path,
        receipt_no="REC-TEST-EMAIL-01",
        resident_name="Jenny Portilla",
        concept="Pago de Alícuota Ordinaria - Octubre 2026",
        amount=25.0,
        payment_date="2026-10-02",
        reference="REF-TEST-EMAIL-123",
        background=False
    )

    print("\nNotification Results:")
    for r in results:
        print(f"Channel: {r['channel']}, Dest: {r['destination']}, Status: {r['status']}, Details: {r['details']}")

    email_results = [r for r in results if r["channel"] == "Email"]
    assert len(email_results) > 0, "No email tasks were generated"
    assert any(r["status"] == "Enviado" for r in email_results), "Email status should be 'Enviado'"

    # 2. Test API Endpoint function test_email_endpoint directly
    print("\nTesting test_email_endpoint function directly...")
    req = EmailTestRequest(recipient_email="sanpedro.torre6@gmail.com")
    resp = test_email_endpoint(req)
    print("API Response:", resp)
    assert resp.get("status") == "success"

    print("\n>>> ALL EMAIL NOTIFICATION & API TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_test()
