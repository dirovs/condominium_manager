import os
import csv
import io
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Depends, UploadFile, File, Response
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from schemas import AliquotUpdate, AliquotEmitRequest, ReceiptResend
from models import Aliquot
from security import require_authenticated, check_unit_permission, decode_access_token
from receipt_processor import ReceiptProcessor
from notifications import NotificationManager
from ocr_helper import OCRHelper

router = APIRouter(tags=["Aliquots & Receipts"])

def get_sm():
    from app import sm
    return sm

def get_receipts_dir():
    from app import RECEIPTS_DIR
    return RECEIPTS_DIR

def get_save_structured_receipt():
    from app import save_structured_receipt
    return save_structured_receipt

def get_is_valid_name_for_mismatch_check():
    from app import is_valid_name_for_mismatch_check
    return is_valid_name_for_mismatch_check

@router.get("/api/aliquots")
def get_aliquots():
    sm = get_sm()
    return sm.get_aliquots_with_details()

@router.post("/api/aliquots/emit")
def emit_aliquots(req: AliquotEmitRequest):
    sm = get_sm()
    try:
        if req.unit_id and str(req.unit_id).strip().lower() not in ["", "all", "todos"]:
            aliquot = sm.emit_aliquot_individual(
                unit=req.unit_id,
                month=req.month,
                year=req.year,
                amount=req.amount
            )
            return {
                "status": "success",
                "emitted": 1,
                "message": f"Alícuota para el Depto {req.unit_id} ({req.month} {req.year}) generada exitosamente.",
                "aliquot": aliquot
            }
        else:
            count = sm.emit_aliquots_bulk(req.month, req.year)
            if count == 0:
                message = f"Todas las alícuotas para {req.month} {req.year} ya estaban generadas. No se generaron nuevos registros."
            else:
                message = f"Se emitieron {count} alícuotas faltantes para {req.month} {req.year} exitosamente."
            return {"status": "success", "emitted": count, "message": message}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/api/aliquots/{id}")
def update_aliquot(id: str, req: AliquotUpdate):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    try:
        aliquot = None
        for a in sm.data.get("aliquots", []):
            if str(a.get("id")) == str(id):
                aliquot = a
                break

        if not aliquot:
            raise HTTPException(status_code=404, detail="Alícuota no encontrada.")

        is_paid = (aliquot.get("status") == "Pagado")

        # 1. Update amount if provided
        if req.amount is not None:
            if float(req.amount) < 0:
                raise ValueError("El monto de la alícuota no puede ser negativo.")
            aliquot["amount"] = float(req.amount)
            if aliquot.get("status") != "Pagado":
                aliquot["paid_amount"] = 0.0
            with Session(sm.engine) as session:
                db_a = session.exec(select(Aliquot).where(Aliquot.id == id)).first()
                if db_a:
                    db_a.amount = float(req.amount)
                    session.add(db_a)
                    session.commit()
            sm.sync()

        # 2. Update late fee if provided
        if req.late_fee is not None:
            aliquot["late_fee"] = float(req.late_fee)
            with Session(sm.engine) as session:
                db_a = session.exec(select(Aliquot).where(Aliquot.id == id)).first()
                if db_a:
                    db_a.late_fee = float(req.late_fee)
                    session.add(db_a)
                    session.commit()
            sm.sync()

        # 3. Update reference if provided
        if req.reference is not None:
            new_ref = str(req.reference).strip()
            aliquot["reference"] = new_ref
            if req.payment_date:
                aliquot["payment_date"] = str(req.payment_date).strip()

            bank_match = None
            if new_ref:
                for b in sm.data.get("bank_statement", []):
                    if b.get("reference") and str(b["reference"]).strip().lower() == new_ref.lower() and not b.get("reconciled"):
                        bank_match = b
                        break

            if bank_match and req.auto_reconcile:
                pay_date = bank_match.get("date") or req.payment_date or aliquot.get("payment_date") or datetime.now().strftime("%Y-%m-%d")
                pay_amt = float(bank_match.get("amount") or aliquot.get("amount") or 70.0)

                rec_res = sm.reconcile_payment(
                    unit=aliquot["unit"],
                    month=aliquot["month"],
                    year=int(aliquot["year"]),
                    amount=pay_amt,
                    reference=new_ref,
                    payment_date=pay_date,
                    late_fee=req.late_fee,
                    reset_existing=True
                )
                aliquot = rec_res["aliquot"]
                sm.sync()

                dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
                receipt_no = f"REC-AL-{aliquot['id']}"
                recipient_name = sm.get_receipt_recipient_name(aliquot["unit"])
                concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"

                ReceiptProcessor.generate_receipt_pdf(
                    dest_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    unit=str(aliquot["unit"]),
                    concept=concept,
                    amount=rec_res["aliquot_paid"],
                    late_fee=rec_res["late_fee_paid"],
                    reference=new_ref,
                    payment_date=pay_date,
                    condo_name=sm.config.get("condo_name", "Condominio El Mirador"),
                    abono_deuda=rec_res.get("abono_deuda", 0.0),
                    pago_extra=rec_res.get("pago_extra", 0.0),
                    saldo_deuda=rec_res.get("saldo_deuda", 0.0),
                    advance_aliquots=rec_res.get("advance_aliquots"),
                    sm=sm
                )

                return {
                    "status": "success",
                    "reconciled": True,
                    "message": f"Referencia guardada y conciliada automáticamente con la transacción bancaria #{new_ref} por {sm.config.get('currency', '$')}{pay_amt:.2f}.",
                    "aliquot": aliquot,
                    "receipt_url": f"/api/receipts/aliquot/{aliquot['id']}/pdf"
                }
            else:
                if new_ref and not is_paid:
                    aliquot["status"] = "Validación Manual"
                with Session(sm.engine) as session:
                    db_a = session.exec(select(Aliquot).where(Aliquot.id == id)).first()
                    if db_a:
                        db_a.reference = new_ref
                        db_a.status = aliquot["status"]
                        if req.payment_date:
                            db_a.payment_date = req.payment_date
                        if req.amount is not None:
                            db_a.amount = float(req.amount)
                        if req.late_fee is not None:
                            db_a.late_fee = float(req.late_fee)
                        session.add(db_a)
                        session.commit()
                sm.sync()

                msg = f"Referencia '{new_ref}' guardada. No se encontró ninguna transacción en el estado de cuenta con esta referencia exacta." if new_ref else "Alícuota actualizada correctamente."
                return {
                    "status": "success",
                    "reconciled": False,
                    "message": msg,
                    "aliquot": aliquot
                }

        return {
            "status": "success",
            "reconciled": False,
            "message": "Alícuota actualizada correctamente.",
            "aliquot": aliquot
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/aliquots/{id}")
def delete_aliquot(id: str, request: Request):
    sm = get_sm()
    role = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = decode_access_token(auth_header.split(" ", 1)[1].strip())
            role = str(payload.get("role", "")).strip().lower()
        except Exception:
            pass
    if not role:
        role = request.headers.get("X-User-Role", "").strip().lower()

    if role != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="Permiso denegado. Solo el Superadministrador tiene permisos para eliminar alícuotas."
        )

    success = sm.delete_aliquot(id)
    if not success:
        raise HTTPException(status_code=404, detail="La alícuota especificada no existe o ya fue eliminada.")

    return {"status": "success", "message": "Alícuota eliminada exitosamente."}

@router.post("/api/aliquots/{id}/upload-receipt")
async def upload_receipt_for_aliquot(
    id: str,
    file: UploadFile = File(...)
):
    sm = get_sm()
    save_structured_receipt = get_save_structured_receipt()
    is_valid_name_for_mismatch_check = get_is_valid_name_for_mismatch_check()
    RECEIPTS_DIR = get_receipts_dir()

    aliquot = None
    for a in sm.data["aliquots"]:
        if a["id"] == id:
            aliquot = a
            break
    if not aliquot:
        raise HTTPException(status_code=404, detail="Alícuota no encontrada.")

    unit_id = aliquot["unit"]
    month = aliquot["month"]
    year = aliquot["year"]

    unit_exists = any(u["id"] == unit_id for u in sm.data["units"])
    if not unit_exists:
        raise HTTPException(status_code=404, detail="El departamento de la alícuota no existe.")

    owner_name = next(u["owner"] for u in sm.data["units"] if u["id"] == unit_id)
    recipient_name = sm.get_receipt_recipient_name(unit_id)

    file_ext = os.path.splitext(file.filename)[1].lower()
    file_bytes = await file.read()

    relative_url, temp_file_path = save_structured_receipt(file_bytes, file.filename, unit_id, year, month)

    file_text = ""
    try:
        if file_ext == ".pdf":
            file_text = ReceiptProcessor.extract_text_from_pdf(temp_file_path)
        elif file_ext in [".png", ".jpg", ".jpeg"]:
            file_text = OCRHelper.extract_text(temp_file_path)
    except Exception as e:
        print(f"Error extracting text: {e}")

    chosen_unit = next((u for u in sm.data["units"] if u["id"] == unit_id), None)
    chosen_names = []
    if chosen_unit.get("owner"):
        chosen_names.append(chosen_unit["owner"])
    if chosen_unit.get("tenant"):
        chosen_names.append(chosen_unit["tenant"])

    combined_text = file_text.lower()
    chosen_mentioned = False
    for name in chosen_names:
        if not is_valid_name_for_mismatch_check(name):
            continue
        parts = name.split()
        if len(parts) >= 2:
            if f"{parts[0]} {parts[1]}".lower() in combined_text:
                chosen_mentioned = True
                break
        elif len(parts) == 1 and len(parts[0]) >= 4:
            if parts[0].lower() in combined_text:
                chosen_mentioned = True
                break

    detected_other_unit = None
    detected_other_name = None
    if not chosen_mentioned:
        for u in sm.data["units"]:
            if u["id"] == unit_id:
                continue
            other_names = []
            if u.get("owner"):
                other_names.append((u["owner"], "propietario"))
            if u.get("tenant"):
                other_names.append((u["tenant"], "inquilino"))

            for oname, orole in other_names:
                if not is_valid_name_for_mismatch_check(oname):
                    continue
                oparts = oname.split()
                match = False
                if len(oparts) >= 2:
                    if f"{oparts[0]} {oparts[1]}".lower() in combined_text:
                        match = True
                elif len(oparts) == 1 and len(oparts[0]) >= 4:
                    if oparts[0].lower() in combined_text:
                        match = True

                if match:
                    detected_other_unit = u["id"]
                    detected_other_name = f"{oname} ({orole} del Depto {u['id']})"
                    break
            if detected_other_unit:
                break

    extracted_amount, extracted_reference, extracted_date = ReceiptProcessor.parse_bank_receipt_text(file_text)
    if not extracted_amount:
        extracted_amount = float(aliquot.get("amount") or next(u["aliquot_base"] for u in sm.data["units"] if u["id"] == unit_id))
    if not extracted_reference:
        extracted_reference = f"REF-{int(datetime.now().timestamp())}"
    if not extracted_date:
        extracted_date = datetime.now().strftime("%Y-%m-%d")

    payment_date = extracted_date
    late_fee = sm.check_late_fee(payment_date, month, year)

    bank_match = None
    for b in sm.data.get("bank_statement", []):
        if b.get("reference") and str(b["reference"]).strip().lower() == str(extracted_reference).strip().lower():
            if not b.get("reconciled", False):
                bank_match = b
                break

    mismatch_warning = None
    if detected_other_unit and not chosen_mentioned:
        mismatch_warning = f"¡Atención! El comprobante cargado para el Depto {unit_id} contiene el nombre de '{detected_other_name}'. Por favor verifique si el comprobante pertenece realmente a este departamento."

    if bank_match:
        payment_date = bank_match["date"]
        rec_result = sm.reconcile_payment(
            unit=unit_id,
            month=month,
            year=year,
            amount=extracted_amount,
            reference=extracted_reference,
            payment_date=payment_date,
            late_fee=late_fee,
            reset_existing=True
        )
        aliquot = rec_result["aliquot"]
        aliquot["comprobante_url"] = relative_url
        sm.sync()

        receipt_no = f"REC-AL-{aliquot['id']}"
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
        concept = f"Pago de Alícuota Ordinaria - Mes: {month} / {year}"

        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=recipient_name,
            unit=str(unit_id),
            concept=concept,
            amount=rec_result["aliquot_paid"],
            late_fee=rec_result["late_fee_paid"],
            reference=extracted_reference,
            payment_date=payment_date,
            condo_name=sm.config.get("condo_name", "Condominio El Mirador"),
            abono_deuda=rec_result.get("abono_deuda", 0.0),
            pago_extra=rec_result.get("pago_extra", 0.0),
            saldo_deuda=rec_result.get("saldo_deuda", 0.0),
            advance_aliquots=rec_result.get("advance_aliquots"),
            sm=sm
        )

        try:
            NotificationManager.send_receipt_notifications(
                sm=sm,
                unit_id=unit_id,
                receipt_pdf_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                concept=concept,
                amount=rec_result["aliquot_paid"] + rec_result["late_fee_paid"] + rec_result.get("abono_deuda", 0.0) + rec_result.get("pago_extra", 0.0),
                payment_date=payment_date,
                reference=extracted_reference
            )
        except Exception as e:
            print(f"Error sending notifications: {e}")

        return {
            "status": "success",
            "message": f"¡Comprobante procesado y conciliado con el banco para el Depto {unit_id} ({month}/{year})!",
            "details": {
                "tipo": "Alícuota Ordinaria",
                "mes": f"{month} / {year}",
                "unidad": unit_id,
                "propietario": owner_name,
                "monto_alicuota": rec_result["aliquot_paid"],
                "multa": rec_result["late_fee_paid"],
                "referencia": extracted_reference,
                "fecha_pago": payment_date,
                "conciliacion": "Automática (Banco)"
            },
            "receipt_url": f"/api/receipts/aliquot/{aliquot['id']}/pdf",
            "mismatch_warning": mismatch_warning
        }
    else:
        aliquot["status"] = "Validación Manual"
        aliquot["reference"] = extracted_reference
        aliquot["payment_date"] = payment_date
        aliquot["late_fee"] = late_fee
        aliquot["comprobante_url"] = relative_url
        sm.sync()

        return {
            "status": "manual_validation",
            "message": f"Comprobante recibido por {sm.config.get('currency', '$')}{extracted_amount:.2f}, pero la referencia bancaria '{extracted_reference}' no se encontró en el Estado de Cuenta. Marcado para Validación Manual.",
            "details": {
                "tipo": "Alícuota Ordinaria",
                "mes": f"{month} / {year}",
                "unidad": unit_id,
                "propietario": owner_name,
                "monto": extracted_amount,
                "multa": late_fee,
                "referencia": extracted_reference,
                "fecha_pago": payment_date,
                "conciliacion": "Pendiente de Verificación Manual"
            },
            "comprobante_url": relative_url,
            "mismatch_warning": mismatch_warning
        }

@router.get("/api/aliquots/export/csv")
def export_aliquots_csv():
    sm = get_sm()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Unidad", "Mes", "Año", "Monto", "Multa", "Fecha Pago", "Referencia", "Estado", "Abono Deuda", "Saldo Deuda"])
    for a in sm.data["aliquots"]:
        writer.writerow([
            a.get("id"),
            a.get("unit"),
            a.get("month"),
            a.get("year"),
            a.get("amount"),
            a.get("late_fee"),
            a.get("payment_date"),
            a.get("reference"),
            a.get("status"),
            a.get("abono_deuda", 0.0),
            a.get("saldo_deuda", 0.0)
        ])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alicuotas.csv"}
    )

@router.get("/api/receipts/{type}/{id}/pdf")
def get_receipt_pdf_by_type(
    type: str,
    id: str,
    current_user: dict = Depends(require_authenticated)
):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    type_clean = str(type or "aliquot").strip().lower()

    if type_clean in ["aliquots", "aliquot", "alicuota"]:
        aliquot = None
        for a in sm.data.get("aliquots", []):
            if str(a.get("id")) == str(id):
                aliquot = a
                break
        if not aliquot:
            raise HTTPException(status_code=404, detail="Alícuota no encontrada.")

        check_unit_permission(current_user, str(aliquot.get("unit")))
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")

        receipt_no = f"REC-AL-{aliquot['id']}"
        resident_name = aliquot.get("paid_by") or sm.get_receipt_recipient_name(aliquot.get("unit"))
        concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot.get('month', '')} / {aliquot.get('year', '')}"
        paid_amount = float(aliquot.get("paid_amount") or aliquot.get("amount") or 70.0)
        late_fee = float(aliquot.get("late_fee") or 0.0)
        ref = aliquot.get("reference") or "Comprobante de Pago"
        pdate = aliquot.get("payment_date") or datetime.now().strftime("%Y-%m-%d")

        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=resident_name,
            unit=str(aliquot.get("unit")),
            concept=concept,
            amount=paid_amount,
            late_fee=late_fee,
            reference=ref,
            payment_date=pdate,
            condo_name=sm.config.get("condo_name"),
            condo_address=sm.config.get("condo_address"),
            condo_ruc=sm.config.get("condo_ruc"),
            abono_deuda=float(aliquot.get("abono_deuda", 0.0) or 0.0),
            pago_extra=float(aliquot.get("pago_extra", 0.0) or 0.0),
            saldo_deuda=float(aliquot.get("saldo_deuda", 0.0) or 0.0),
            advance_aliquots=aliquot.get("advance_aliquots"),
            sm=sm
        )
        return FileResponse(dest_pdf, media_type="application/pdf", filename=f"recibo_{aliquot['id']}.pdf")

    elif type_clean in ["charge", "cargo", "additional_charge", "charges"]:
        charge = None
        for c in sm.data.get("additional_charges", []):
            if str(c.get("id")) == str(id):
                charge = c
                break
        if not charge:
            raise HTTPException(status_code=404, detail="Cargo no encontrado.")

        check_unit_permission(current_user, str(charge.get("unit")))
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{charge['id']}.pdf")

        receipt_no = f"REC-AC-{charge['id']}"
        resident_name = charge.get("paid_by") or charge.get("owner_name") or sm.get_receipt_recipient_name(charge.get("unit"))
        concept = f"{charge.get('type', 'Cargo')}: {charge.get('description', '') or charge.get('concept', '')}"
        paid_amount = float(charge.get("paid_amount") or charge.get("amount") or 0.0)
        ref = charge.get("reference") or "Comprobante de Pago"
        pdate = charge.get("payment_date") or datetime.now().strftime("%Y-%m-%d")

        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=resident_name,
            unit=str(charge.get("unit")),
            concept=concept,
            amount=paid_amount,
            late_fee=0.0,
            reference=ref,
            payment_date=pdate,
            condo_name=sm.config.get("condo_name"),
            condo_address=sm.config.get("condo_address"),
            condo_ruc=sm.config.get("condo_ruc"),
            sm=sm
        )
        return FileResponse(dest_pdf, media_type="application/pdf", filename=f"recibo_charge_{charge['id']}.pdf")

    else:
        raise HTTPException(status_code=400, detail="Tipo de recibo no válido.")

@router.get("/api/aliquots/{id}/receipt-pdf")
def get_aliquot_receipt_pdf_alias(id: str, current_user: dict = Depends(require_authenticated)):
    return get_receipt_pdf_by_type("aliquot", id, current_user=current_user)

@router.post("/api/receipts/resend")
def resend_receipt(req: ReceiptResend):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()

    if req.type == "aliquot":
        aliquot = next((a for a in sm.data["aliquots"] if a["id"] == req.id), None)
        if not aliquot:
            raise HTTPException(status_code=404, detail="Alícuota no encontrada.")
        if aliquot["status"] != "Pagado":
            raise HTTPException(status_code=400, detail="Solo se pueden reenviar recibos de pagos ya conciliados/pagados.")

        unit_id = aliquot["unit"]
        resident_name = sm.get_receipt_recipient_name(unit_id)
        concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
        amount = aliquot["amount"] + aliquot["late_fee"]
        payment_date = aliquot["payment_date"]
        reference = aliquot["reference"]
        receipt_no = f"REC-AL-{aliquot['id']}"
        pdf_filename = f"recibo_{aliquot['id']}.pdf"

    elif req.type == "charge":
        charge = next((c for c in sm.data["additional_charges"] if c["id"] == req.id), None)
        if not charge:
            raise HTTPException(status_code=404, detail="Cargo extraordinario no encontrado.")
        if charge["status"] != "Pagado":
            raise HTTPException(status_code=400, detail="Solo se pueden reenviar recibos de pagos ya conciliados/pagados.")

        unit_id = charge["unit"]
        resident_name = sm.get_receipt_recipient_name(unit_id)
        concept = f"{charge['type']}: {charge['description']}"
        amount = charge["amount"]
        payment_date = charge["payment_date"]
        reference = charge["reference"]
        receipt_no = f"REC-AC-{charge['id']}"
        pdf_filename = f"recibo_charge_{charge['id']}.pdf"

    else:
        raise HTTPException(status_code=400, detail="Tipo de recibo no soportado.")

    pdf_path = os.path.join(RECEIPTS_DIR, pdf_filename)

    late_fee_val = aliquot.get("late_fee", 0.0) if req.type == "aliquot" else 0.0
    base_amount = (aliquot.get("paid_amount") or aliquot.get("amount") or 0.0) if req.type == "aliquot" else (charge.get("paid_amount") or charge.get("amount") or 0.0)
    abono_val = float(aliquot.get("abono_deuda", 0.0) or 0.0) if req.type == "aliquot" else 0.0
    pago_extra_val = float(aliquot.get("pago_extra", 0.0) or 0.0) if req.type == "aliquot" else 0.0
    saldo_deuda_val = float(aliquot.get("saldo_deuda", 0.0) or 0.0) if req.type == "aliquot" else 0.0
    advance_aliquots_val = aliquot.get("advance_aliquots") if req.type == "aliquot" else None

    ReceiptProcessor.generate_receipt_pdf(
        dest_path=pdf_path,
        receipt_no=receipt_no,
        resident_name=resident_name,
        unit=str(unit_id),
        concept=concept,
        amount=base_amount,
        late_fee=late_fee_val,
        reference=reference,
        payment_date=payment_date,
        condo_name=sm.config.get("condo_name"),
        condo_address=sm.config.get("condo_address"),
        condo_ruc=sm.config.get("condo_ruc"),
        abono_deuda=abono_val,
        pago_extra=pago_extra_val,
        saldo_deuda=saldo_deuda_val,
        advance_aliquots=advance_aliquots_val,
        sm=sm
    )

    results = NotificationManager.send_receipt_notifications(
        sm=sm,
        unit_id=unit_id,
        receipt_pdf_path=pdf_path,
        receipt_no=receipt_no,
        resident_name=resident_name,
        concept=concept,
        amount=amount,
        payment_date=payment_date,
        reference=reference
    )

    if not results:
        raise HTTPException(status_code=500, detail="No se pudo despachar el reenvío. Verifique que existan contactos registrados.")

    return {"status": "success", "message": f"Recibo reenviado exitosamente a {len(results)} destinos.", "sent_logs": results}
