import os
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from schemas import ChargeCreate, ChargeUpdate
from security import require_authenticated
from receipt_processor import ReceiptProcessor

router = APIRouter(tags=["Additional Charges"])

def get_sm():
    from app import sm
    return sm

def get_receipts_dir():
    from app import RECEIPTS_DIR
    return RECEIPTS_DIR

@router.get("/api/additional-charges")
def get_additional_charges():
    sm = get_sm()
    return sm.get_additional_charges_with_details()

@router.post("/api/additional-charges")
def create_additional_charge(charge: ChargeCreate):
    sm = get_sm()
    return sm.add_additional_charge(charge.unit, charge.type, charge.description, charge.amount, charge.issue_date)

@router.put("/api/additional-charges/{id}")
def update_additional_charge(id: str, req: ChargeUpdate):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    try:
        updated = sm.update_additional_charge(id, req.amount, req.description, req.reference)
        if not updated:
            raise HTTPException(status_code=404, detail="Cargo adicional no encontrado.")

        new_ref = str(req.reference or "").strip()
        if new_ref:
            bank_match = None
            for b in sm.data.get("bank_statement", []):
                if b.get("reference") and str(b["reference"]).strip().lower() == new_ref.lower() and not b.get("reconciled", False):
                    bank_match = b
                    break

            if bank_match and req.auto_reconcile:
                pay_date = bank_match.get("date") or req.payment_date or datetime.now().strftime("%Y-%m-%d")
                pay_amt = float(bank_match.get("amount") or req.amount or 0.0)
                sm.reconcile_additional_charge(id, new_ref, pay_date, pay_amt)

                dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{id}.pdf")
                receipt_no = f"REC-AC-{id}"
                recipient_name = sm.get_receipt_recipient_name(updated["unit"])
                concept = f"{updated.get('type', 'Cargo')}: {updated.get('description', '')}"
                ReceiptProcessor.generate_receipt_pdf(
                    dest_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    unit=str(updated["unit"]),
                    concept=concept,
                    amount=pay_amt,
                    late_fee=0.0,
                    reference=new_ref,
                    payment_date=pay_date,
                    condo_name=sm.config.get("condo_name", "Condominio El Mirador")
                )
                return {
                    "status": "success",
                    "reconciled": True,
                    "message": f"Cargo adicional conciliado automáticamente con la transacción bancaria #{new_ref}.",
                    "charge": updated,
                    "receipt_url": f"/api/receipts/charge/{id}/pdf"
                }

        return {
            "status": "success",
            "reconciled": False,
            "message": "Cargo adicional actualizado correctamente.",
            "charge": updated
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/additional-charges/{id}")
def delete_additional_charge(id: str):
    sm = get_sm()
    success = sm.delete_additional_charge(id)
    if not success:
        raise HTTPException(status_code=404, detail="Cargo adicional no encontrado.")
    return {"status": "success"}

@router.get("/api/charges/{id}/receipt-pdf")
def get_charge_receipt_pdf_alias(id: str, current_user: dict = Depends(require_authenticated)):
    from routers.aliquots import get_receipt_pdf_by_type
    return get_receipt_pdf_by_type("charge", id, current_user=current_user)
