import os
import sys
from receipt_processor import ReceiptProcessor
from sheets_manager import SheetsManager

def test_receipt_generation():
    sm = SheetsManager()
    output_dir = os.path.join(os.path.dirname(__file__), "static", "receipts")
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Test unit with debt (passed explicitly or with debt)
    pdf_305 = os.path.join(output_dir, "test_recibo_305.pdf")
    ReceiptProcessor.generate_receipt_pdf(
        dest_path=pdf_305,
        receipt_no="REC-TEST-305",
        resident_name="Jaramillo Jimenez Washington",
        unit="305",
        concept="Pago de Alícuota Ordinaria - Mes: Mayo / 2026",
        amount=25.0,
        late_fee=0.0,
        reference="REF-TEST-305",
        payment_date="2026-05-10",
        pending_debts_summary={
            "total_debt": 5.0,
            "details": ["Deuda Inicial: Atraso Agosto ($5.00)"]
        }
    )
    assert os.path.exists(pdf_305)
    size_305 = os.path.getsize(pdf_305)
    print(f"Receipt 305 generated: {pdf_305} (Size: {size_305} bytes)")
    
    # Extract text from 305 PDF
    text_305 = ReceiptProcessor.extract_text_from_pdf(pdf_305)
    print("--- TEXT IN RECEIPT 305 ---")
    print(text_305)
    print("---------------------------")
    assert "DEUDA PENDIENTE REGISTRADA" in text_305 or "TOTAL DEUDA PENDIENTE" in text_305
    assert "$5.00" in text_305
    
    # 2. Test unit with NO debt (101)
    pdf_101 = os.path.join(output_dir, "test_recibo_101.pdf")
    ReceiptProcessor.generate_receipt_pdf(
        dest_path=pdf_101,
        receipt_no="REC-TEST-101",
        resident_name="Propietario 101",
        unit="101",
        concept="Pago de Alícuota Ordinaria - Mes: Mayo / 2026",
        amount=25.0,
        late_fee=0.0,
        reference="REF-TEST-101",
        payment_date="2026-05-10",
        pending_debts_summary={"total_debt": 0.0, "details": []}
    )
    assert os.path.exists(pdf_101)
    size_101 = os.path.getsize(pdf_101)
    print(f"Receipt 101 generated: {pdf_101} (Size: {size_101} bytes)")
    
    # Extract text from 101 PDF
    text_101 = ReceiptProcessor.extract_text_from_pdf(pdf_101)
    print("--- TEXT IN RECEIPT 101 ---")
    print(text_101)
    print("---------------------------")
    assert "AL DÍA" in text_101
    
    print("\nALL RECEIPT PDF TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_receipt_generation()
