import os
import shutil
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends

from schemas import (
    BankTransactionCreate,
    BankTransactionUpdate,
    ReconcileManual,
    ReconcilePair,
    UndoPaymentRequest,
    ReviewQueueApproveRequest,
    ReviewQueueRejectRequest
)
from receipt_processor import ReceiptProcessor, BankStatementParser
from notifications import NotificationManager
from security import require_admin

router = APIRouter(tags=["Bank & Reconciliation"])

def get_sm():
    from app import sm
    return sm

def get_receipts_dir():
    from app import RECEIPTS_DIR
    return RECEIPTS_DIR

def get_uploads_dir():
    from app import UPLOADS_DIR
    return UPLOADS_DIR

@router.get("/api/bank-statement")
def get_bank_statement(current_user: dict = Depends(require_admin)):
    sm = get_sm()
    return sm.get_bank_statement_list()

@router.post("/api/bank-statement-add")
def create_bank_transaction(tx: BankTransactionCreate):
    sm = get_sm()
    return sm.add_bank_transaction(tx.date, tx.reference, tx.amount, tx.detail)

@router.put("/api/bank-statement/{old_reference}")
def update_bank_transaction_endpoint(old_reference: str, req: BankTransactionUpdate):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    try:
        old_ref_clean = str(old_reference).strip()
        new_ref = str(req.reference).strip()

        # If this transaction was previously matched/reconciled, undo its previous effect
        matched_aliquots = [a for a in sm.data.get("aliquots", []) if a.get("reference") and old_ref_clean.lower() in str(a.get("reference", "")).lower()]
        matched_charges = [c for c in sm.data.get("additional_charges", []) if c.get("reference") and old_ref_clean.lower() in str(c.get("reference", "")).lower()]

        for a in matched_aliquots:
            sm.undo_payment(a["id"], "aliquot")
            a["reference"] = new_ref

        for c in matched_charges:
            sm.undo_payment(c["id"], "charge")
            c["reference"] = new_ref

        updated_tx = sm.update_bank_transaction(
            old_reference=old_reference,
            new_reference=req.reference,
            date=req.date,
            amount=req.amount,
            detail=req.detail
        )
        if not updated_tx:
            raise HTTPException(status_code=404, detail="Transacción bancaria no encontrada.")

        new_ref = str(req.reference).strip()
        # Check if there is an unreconciled aliquot with this exact reference
        aliquot_match = None
        for a in sm.data.get("aliquots", []):
            if a.get("status") != "Pagado" and a.get("reference") and str(a["reference"]).strip().lower() == new_ref.lower():
                aliquot_match = a
                break

        if aliquot_match:
            rec_date = updated_tx.get("date") or aliquot_match.get("payment_date") or datetime.now().strftime("%Y-%m-%d")
            rec_amt = float(updated_tx.get("amount") or aliquot_match.get("amount") or 70.0)
            rec_res = sm.reconcile_payment(
                unit=aliquot_match["unit"],
                month=aliquot_match["month"],
                year=int(aliquot_match["year"]),
                amount=rec_amt,
                reference=new_ref,
                payment_date=rec_date,
                late_fee=aliquot_match.get("late_fee"),
                reset_existing=True
            )
            # Generate receipt PDF
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot_match['id']}.pdf")
            receipt_no = f"REC-AL-{aliquot_match['id']}"
            recipient_name = sm.get_receipt_recipient_name(aliquot_match["unit"])
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot_match['month']} / {aliquot_match['year']}"
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=str(aliquot_match["unit"]),
                concept=concept,
                amount=rec_res["aliquot_paid"],
                late_fee=rec_res["late_fee_paid"],
                reference=new_ref,
                payment_date=rec_date,
                condo_name=sm.config.get("condo_name", "Condominio El Mirador"),
                abono_deuda=rec_res.get("abono_deuda", 0.0),
                pago_extra=rec_res.get("pago_extra", 0.0),
                saldo_deuda=rec_res.get("saldo_deuda", 0.0),
                advance_aliquots=rec_res.get("advance_aliquots")
            )
            return {
                "status": "success",
                "reconciled": True,
                "message": f"Transacción actualizada y conciliada automáticamente con la alícuota del Depto {aliquot_match['unit']} ({aliquot_match['month']})!",
                "tx": updated_tx,
                "aliquot": aliquot_match,
                "receipt_url": f"/api/receipts/aliquot/{aliquot_match['id']}/pdf"
            }

        # Also check additional charges
        charge_match = None
        for c in sm.data.get("additional_charges", []):
            if c.get("status") != "Pagado" and c.get("reference") and str(c["reference"]).strip().lower() == new_ref.lower():
                charge_match = c
                break

        if charge_match:
            rec_date = updated_tx.get("date") or charge_match.get("payment_date") or datetime.now().strftime("%Y-%m-%d")
            rec_amt = float(updated_tx.get("amount") or charge_match.get("amount") or 0.0)
            sm.reconcile_additional_charge(charge_match["id"], new_ref, rec_date, rec_amt)
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{charge_match['id']}.pdf")
            receipt_no = f"REC-AC-{charge_match['id']}"
            recipient_name = sm.get_receipt_recipient_name(charge_match["unit"])
            concept = f"{charge_match.get('type', 'Cargo')}: {charge_match.get('description', '')}"
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=str(charge_match["unit"]),
                concept=concept,
                amount=rec_amt,
                late_fee=0.0,
                reference=new_ref,
                payment_date=rec_date,
                condo_name=sm.config.get("condo_name", "Condominio El Mirador")
            )
            return {
                "status": "success",
                "reconciled": True,
                "message": f"Transacción actualizada y conciliada automáticamente con el cargo del Depto {charge_match['unit']}!",
                "tx": updated_tx,
                "charge": charge_match,
                "receipt_url": f"/api/receipts/charge/{charge_match['id']}/pdf"
            }

        return {
            "status": "success",
            "reconciled": False,
            "message": "Transacción bancaria actualizada exitosamente.",
            "tx": updated_tx
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/bank-statement/{reference}")
def delete_bank_transaction(reference: str):
    sm = get_sm()
    success = sm.delete_bank_transaction(reference)
    if not success:
        raise HTTPException(status_code=404, detail="Transacción bancaria no encontrada.")
    return {"status": "success", "message": "Transacción eliminada correctamente."}

@router.post("/api/upload-bank-statement")
async def upload_bank_statement(file: UploadFile = File(...)):
    sm = get_sm()
    UPLOADS_DIR = get_uploads_dir()
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in [".pdf", ".png", ".jpg", ".jpeg", ".csv", ".xlsx"]:
        raise HTTPException(status_code=400, detail="Formato de archivo no soportado.")

    temp_path = os.path.join(UPLOADS_DIR, f"statement_{datetime.now().timestamp()}{file_ext}")
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        transactions = BankStatementParser.parse_statement_file(temp_path, file_ext)

        # Add transactions bulk filtering duplicates
        added, duplicates = sm.add_bank_transactions_bulk(transactions)

        return {
            "status": "success",
            "message": f"Se procesó el archivo '{file.filename}' correctamente.",
            "added": added,
            "duplicates": duplicates,
            "total_extracted": len(transactions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al procesar el archivo: {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@router.post("/api/reconcile-manual")
def reconcile_manual(req: ReconcileManual):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    if req.type == "aliquot":
        # Check if the reference matches bank statement to mark it
        for b in sm.data["bank_statement"]:
            if b["reference"] == req.reference:
                b["reconciled"] = True
                break

        # Parse unit, year, month from aliquot id (format: unit-year-month)
        parts = req.id.split("-")
        unit_id = parts[0]
        year = int(parts[1])
        month = parts[2]

        # Look up current aliquot record to default amount
        aliquot_rec = None
        for a in sm.data["aliquots"]:
            if a["id"] == req.id:
                aliquot_rec = a
                break

        amt_to_use = req.amount
        reset_existing = False
        if aliquot_rec:
            if aliquot_rec.get("paid_amount", 0.0) > 0 or aliquot_rec.get("reference"):
                reset_existing = True
            if amt_to_use is None:
                base_due = float(aliquot_rec.get("amount", 0.0)) + float(aliquot_rec.get("paid_amount", 0.0))
                amt_to_use = base_due + float(req.late_fee or 0.0)
        elif amt_to_use is None:
            amt_to_use = 70.0 + float(req.late_fee or 0.0)

        rec_result = sm.reconcile_payment(
            unit=unit_id,
            month=month,
            year=year,
            amount=amt_to_use,
            reference=req.reference,
            payment_date=req.payment_date,
            late_fee=req.late_fee,
            recipient_override=req.receipt_recipient,
            reset_existing=reset_existing
        )

        if rec_result:
            aliquot = rec_result["aliquot"]
            # Generate receipt PDF
            receipt_no = f"REC-AL-{aliquot['id']}"
            resident_name = sm.get_receipt_recipient_name(aliquot["unit"], req.receipt_recipient)
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"

            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=resident_name,
                unit=aliquot["unit"],
                concept=concept,
                amount=rec_result["aliquot_paid"],
                late_fee=rec_result["late_fee_paid"],
                reference=req.reference,
                payment_date=req.payment_date,
                abono_deuda=rec_result["abono_deuda"],
                pago_extra=rec_result["pago_extra"],
                saldo_deuda=rec_result["saldo_deuda"],
                advance_aliquots=rec_result.get("advance_aliquots")
            )

            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=aliquot["unit"],
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=resident_name,
                    concept=concept,
                    amount=rec_result["aliquot_paid"] + rec_result["late_fee_paid"] + rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=req.payment_date,
                    reference=req.reference
                )
            except Exception as e:
                print(f"Error sending manual notification: {e}")

            return {"status": "success", "aliquot": aliquot, "receipt_url": f"/static/receipts/recibo_{aliquot['id']}.pdf"}
        raise HTTPException(status_code=404, detail="Aliquot record not found or could not be reconciled.")

    elif req.type == "charge":
        # Look up current charge record to default amount
        charge_rec = None
        for c in sm.data["additional_charges"]:
            if c["id"] == req.id:
                charge_rec = c
                break

        amt_to_use = req.amount
        if amt_to_use is None:
            amt_to_use = charge_rec["amount"] if charge_rec else 0.0

        rec_result = sm.reconcile_additional_charge(req.id, req.reference, req.payment_date, amount=amt_to_use, recipient_override=req.receipt_recipient)
        if rec_result:
            charge = rec_result["charge"]
            # Generate receipt PDF
            receipt_no = f"REC-AC-{charge['id']}"
            resident_name = sm.get_receipt_recipient_name(charge["unit"], req.receipt_recipient)
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{charge['id']}.pdf")
            concept = f"{charge['type']}: {charge['description']}"

            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=resident_name,
                unit=charge["unit"],
                concept=concept,
                amount=0.0,
                late_fee=0.0,
                reference=req.reference,
                payment_date=req.payment_date,
                abono_deuda=rec_result["abono_deuda"],
                pago_extra=rec_result["pago_extra"],
                saldo_deuda=rec_result["saldo_deuda"]
            )

            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=charge["unit"],
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=resident_name,
                    concept=concept,
                    amount=rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=req.payment_date,
                    reference=req.reference
                )
            except Exception as e:
                print(f"Error sending manual notification: {e}")

            return {"status": "success", "charge": charge, "receipt_url": f"/static/receipts/recibo_charge_{charge['id']}.pdf"}
        raise HTTPException(status_code=404, detail="Additional charge record not found or could not be reconciled.")

@router.post("/api/reconcile-pair")
def reconcile_pair(req: ReconcilePair):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    # 1. Find bank transaction to get date and amount
    bank_tx = None
    for b in sm.data["bank_statement"]:
        if b["reference"] == req.bank_reference:
            bank_tx = b
            break

    if not bank_tx:
        raise HTTPException(status_code=404, detail="La transacción bancaria no existe.")

    bank_amount = float(bank_tx["amount"])

    if req.payment_type == "aliquot":
        # Find aliquot
        aliquot = None
        for a in sm.data["aliquots"]:
            if a["id"] == req.payment_id:
                aliquot = a
                break

        if not aliquot:
            raise HTTPException(status_code=404, detail="El registro de alícuota no existe.")

        reconcile_res = sm.reconcile_payment(
            unit=aliquot["unit"],
            month=aliquot["month"],
            year=aliquot["year"],
            amount=bank_amount,
            reference=req.bank_reference,
            payment_date=bank_tx["date"],
            recipient_override=req.receipt_recipient
        )

        # Mark bank transaction as reconciled
        bank_tx["reconciled"] = True
        sm.sync()

        # Generate receipt PDF
        receipt_no = f"REC-AL-{aliquot['id']}"
        resident_name = sm.get_receipt_recipient_name(aliquot["unit"], req.receipt_recipient)
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
        concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"

        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=resident_name,
            unit=aliquot["unit"],
            concept=concept,
            amount=bank_amount,
            late_fee=aliquot.get("late_fee", 0.0),
            reference=req.bank_reference,
            payment_date=bank_tx["date"]
        )

        try:
            NotificationManager.send_receipt_notifications(
                sm=sm,
                unit_id=aliquot["unit"],
                receipt_pdf_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=resident_name,
                concept=concept,
                amount=bank_amount,
                payment_date=bank_tx["date"],
                reference=req.bank_reference
            )
        except Exception as e:
            print(f"Error sending reconcile-pair aliquot notification: {e}")

        return {"status": "success", "receipt_url": f"/static/receipts/recibo_{aliquot['id']}.pdf"}

    elif req.payment_type == "charge":
        # Find charge
        charge = None
        for c in sm.data["additional_charges"]:
            if c["id"] == req.payment_id:
                charge = c
                break

        if not charge:
            raise HTTPException(status_code=404, detail="El cargo extraordinario no existe.")

        reconcile_res = sm.reconcile_additional_charge(
            charge_id=charge["id"],
            reference=req.bank_reference,
            payment_date=bank_tx["date"],
            amount=bank_amount,
            recipient_override=req.receipt_recipient
        )

        # Mark bank transaction reconciled
        bank_tx["reconciled"] = True
        sm.sync()

        actual_paid = reconcile_res.get("abono_deuda", bank_amount) if reconcile_res else bank_amount

        # Generate receipt PDF
        receipt_no = f"REC-AC-{charge['id']}"
        resident_name = sm.get_receipt_recipient_name(charge["unit"], req.receipt_recipient)
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{charge['id']}.pdf")
        concept = f"{charge['type']}: {charge['description']}"

        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=resident_name,
            unit=charge["unit"],
            concept=concept,
            amount=actual_paid,
            late_fee=0.0,
            reference=req.bank_reference,
            payment_date=bank_tx["date"]
        )

        try:
            NotificationManager.send_receipt_notifications(
                sm=sm,
                unit_id=charge["unit"],
                receipt_pdf_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=resident_name,
                concept=concept,
                amount=actual_paid,
                payment_date=bank_tx["date"],
                reference=req.bank_reference
            )
        except Exception as e:
            print(f"Error sending reconcile-pair charge notification: {e}")

        return {"status": "success", "receipt_url": f"/static/receipts/recibo_charge_{charge['id']}.pdf"}

    raise HTTPException(status_code=400, detail="Tipo de pago inválido.")

@router.post("/api/reconcile-mass")
def reconcile_mass():
    sm = get_sm()
    try:
        aliquots_count, charges_count = sm.reconcile_mass()
        return {
            "status": "success",
            "aliquots_reconciled": aliquots_count,
            "charges_reconciled": charges_count,
            "total_reconciled": aliquots_count + charges_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error durante la conciliación masiva: {e}")

@router.post("/api/undo-payment")
def undo_payment_route(req: UndoPaymentRequest):
    sm = get_sm()
    success = sm.undo_payment(req.id, req.type)
    if success:
        return {"status": "success", "message": "Pago eliminado/deshecho correctamente."}
    raise HTTPException(status_code=400, detail="No se pudo eliminar/deshacer el pago.")

@router.get("/api/reconcile/review-queue")
def get_reconciliation_review_queue():
    """
    Returns receipts and vouchers flagged for manual validation or with doubtful OCR confidence.
    """
    sm = get_sm()
    queue = []
    
    # 1. Aliquots in manual validation
    for a in sm.data.get("aliquots", []):
        if a.get("status") == "Validación Manual":
            queue.append({
                "id": a["id"],
                "type": "aliquot",
                "unit": a["unit"],
                "period": f"{a.get('month', '')} {a.get('year', '')}",
                "amount": a.get("amount", 0.0),
                "reference": a.get("reference", ""),
                "payment_date": a.get("payment_date", ""),
                "comprobante_url": a.get("comprobante_url", ""),
                "confidence": a.get("ocr_confidence", 0.60),
                "reason": "Referencia bancaria pendiente de confirmación en el extracto",
                "status": a.get("status")
            })
            
    # 2. Additional charges in manual validation
    for c in sm.data.get("additional_charges", []):
        if c.get("status") == "Validación Manual":
            queue.append({
                "id": c["id"],
                "type": "charge",
                "unit": c["unit"],
                "period": c.get("type", "Cargo Extraordinario"),
                "description": c.get("description", ""),
                "amount": c.get("amount", 0.0),
                "reference": c.get("reference", ""),
                "payment_date": c.get("payment_date", ""),
                "comprobante_url": c.get("comprobante_url", ""),
                "confidence": c.get("ocr_confidence", 0.60),
                "reason": "Referencia de cargo pendiente de confirmación en el extracto",
                "status": c.get("status")
            })
            
    return {
        "status": "success",
        "count": len(queue),
        "queue": queue
    }

@router.post("/api/reconcile/review-queue/{item_id}/approve")
def approve_review_queue_item(item_id: str, req: ReviewQueueApproveRequest):
    """
    Approves a doubtful / manual voucher from the review queue and triggers conciliation.
    """
    sm = get_sm()
    # Check if aliquot
    aliquot = next((a for a in sm.data.get("aliquots", []) if a["id"] == item_id), None)
    if aliquot:
        manual_req = ReconcileManual(
            id=item_id,
            type="aliquot",
            reference=req.bank_reference,
            payment_date=req.payment_date or datetime.now().strftime("%Y-%m-%d"),
            amount=req.amount,
            receipt_recipient=req.receipt_recipient or "inquilino"
        )
        return reconcile_manual(manual_req)

    # Check if charge
    charge = next((c for c in sm.data.get("additional_charges", []) if c["id"] == item_id), None)
    if charge:
        manual_req = ReconcileManual(
            id=item_id,
            type="charge",
            reference=req.bank_reference,
            payment_date=req.payment_date or datetime.now().strftime("%Y-%m-%d"),
            amount=req.amount,
            receipt_recipient=req.receipt_recipient or "inquilino"
        )
        return reconcile_manual(manual_req)

    raise HTTPException(status_code=404, detail="Registro no encontrado en la cola de revisión.")

@router.post("/api/reconcile/review-queue/{item_id}/reject")
def reject_review_queue_item(item_id: str, req: ReviewQueueRejectRequest):
    """
    Rejects a voucher from the review queue and resets its status to 'Pendiente'.
    """
    sm = get_sm()
    # Check if aliquot
    aliquot = next((a for a in sm.data.get("aliquots", []) if a["id"] == item_id), None)
    if aliquot:
        aliquot["status"] = "Pendiente"
        aliquot["reference"] = ""
        aliquot["comprobante_url"] = ""
        sm.sync()
        return {
            "status": "success",
            "message": f"Comprobante de la alícuota {item_id} rechazado. Motivo: {req.reason}",
            "aliquot": aliquot
        }

    charge = next((c for c in sm.data.get("additional_charges", []) if c["id"] == item_id), None)
    if charge:
        charge["status"] = "Pendiente"
        charge["reference"] = ""
        charge["comprobante_url"] = ""
        sm.sync()
        return {
            "status": "success",
            "message": f"Comprobante del cargo {item_id} rechazado. Motivo: {req.reason}",
            "charge": charge
        }

    raise HTTPException(status_code=404, detail="Registro no encontrado en la cola de revisión.")

