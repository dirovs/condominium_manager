import os
import json
import requests
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Response, Form, File, UploadFile

from receipt_processor import ReceiptProcessor
from ocr_helper import OCRHelper
from notifications import NotificationManager
from security import verify_twilio_signature, verify_meta_signature

router = APIRouter(tags=["WhatsApp & Webhooks"])

def get_sm():
    from app import sm
    return sm

def get_receipts_dir():
    from app import RECEIPTS_DIR
    return RECEIPTS_DIR

def get_static_dir():
    from app import STATIC_DIR
    return STATIC_DIR

def get_save_structured_receipt():
    from app import save_structured_receipt
    return save_structured_receipt

def get_is_valid_name_for_mismatch_check():
    from app import is_valid_name_for_mismatch_check
    return is_valid_name_for_mismatch_check

def send_meta_reply_text(to_number: str, message: str):
    """Helper to send a text reply using Meta API."""
    sm = get_sm()
    cfg = sm.config
    meta_token = cfg.get("meta_wa_token")
    phone_number_id = cfg.get("meta_wa_phone_number_id")
    if not meta_token or not phone_number_id:
        return
    try:
        headers = {
            "Authorization": f"Bearer {meta_token}",
            "Content-Type": "application/json"
        }
        dest_phone = "".join(filter(str.isdigit, to_number))
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": dest_phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message
            }
        }
        requests.post(f"https://graph.facebook.com/v19.0/{phone_number_id}/messages", headers=headers, json=payload, timeout=5.0)
    except Exception as e:
        print(f"Error sending Meta text reply: {e}")

def send_meta_reply_document(to_number: str, doc_url: str, filename: str, caption: str):
    """Helper to send a document reply using Meta API."""
    sm = get_sm()
    cfg = sm.config
    meta_token = cfg.get("meta_wa_token")
    phone_number_id = cfg.get("meta_wa_phone_number_id")
    if not meta_token or not phone_number_id:
        return
    try:
        headers = {
            "Authorization": f"Bearer {meta_token}",
            "Content-Type": "application/json"
        }
        dest_phone = "".join(filter(str.isdigit, to_number))
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": dest_phone,
            "type": "document",
            "document": {
                "link": doc_url,
                "filename": filename,
                "caption": caption
            }
        }
        requests.post(f"https://graph.facebook.com/v19.0/{phone_number_id}/messages", headers=headers, json=payload, timeout=5.0)
    except Exception as e:
        print(f"Error sending Meta document reply: {e}")

@router.post("/api/upload-receipt")
async def upload_receipt(
    message: str = Form(""),
    file: Optional[UploadFile] = File(None),
    unit: Optional[str] = Form(None)
):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    STATIC_DIR = get_static_dir()
    save_structured_receipt = get_save_structured_receipt()
    is_valid_name_for_mismatch_check = get_is_valid_name_for_mismatch_check()

    # 1. Parse WhatsApp Message
    parsed_msg = ReceiptProcessor.parse_whatsapp_message(message)

    if unit:
        parsed_msg["unit"] = unit

    if not parsed_msg["unit"]:
        return {
            "status": "error",
            "message": "No se pudo identificar el Departamento o Unidad en el mensaje de WhatsApp. Por favor, asegúrate de incluir el número de departamento (ej. 'Departamento: 701')."
        }

    if not parsed_msg["month"]:
        months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        parsed_msg["month"] = months[datetime.now().month - 1]

    unit_id = parsed_msg["unit"]
    unit_exists = any(u["id"] == unit_id for u in sm.data["units"])
    if not unit_exists:
        return {
            "status": "error",
            "message": f"El departamento '{unit_id}' especificado en el mensaje no existe en la base de datos de condóminos."
        }

    owner_name = next(u["owner"] for u in sm.data["units"] if u["id"] == unit_id)
    recipient_name = sm.get_receipt_recipient_name(unit_id)

    # 2. Extract Text from Uploaded File (if present)
    file_text = ""
    relative_url = ""
    if file:
        file_ext = os.path.splitext(file.filename)[1].lower()
        file_bytes = await file.read()

        year = parsed_msg.get("year") or datetime.now().year
        month = parsed_msg.get("month")

        relative_url, dest_path = save_structured_receipt(file_bytes, file.filename, unit_id, year, month)

        try:
            if file_ext == ".pdf":
                file_text = ReceiptProcessor.extract_text_from_pdf(dest_path)
            elif file_ext in [".png", ".jpg", ".jpeg"]:
                file_text = OCRHelper.extract_text(dest_path)
            else:
                file_text = ""
        except Exception as e:
            print(f"Error extracting text: {e}")

    if not file_text:
        file_text = message

    # 2b. Mismatch check: chosen unit vs parsed data (Propietario / Inquilino)
    if unit:
        temp_parsed = ReceiptProcessor.parse_whatsapp_message(message)
        if temp_parsed["unit"] and temp_parsed["unit"] != unit:
            if relative_url and os.path.exists(os.path.join(STATIC_DIR, relative_url.lstrip('/'))):
                try:
                    os.remove(os.path.join(STATIC_DIR, relative_url.lstrip('/')))
                except Exception as e:
                    print(f"Error removing file after mismatch: {e}")
            return {
                "status": "error",
                "message": f"Discrepancia: El mensaje de WhatsApp indica Departamento {temp_parsed['unit']}, pero seleccionaste el Depto {unit}."
            }

        chosen_unit = next((u for u in sm.data["units"] if u["id"] == unit), None)
        chosen_names = []
        if chosen_unit.get("owner"):
            chosen_names.append(chosen_unit["owner"])
        if chosen_unit.get("tenant"):
            chosen_names.append(chosen_unit["tenant"])

        combined_text = (message + " " + file_text).lower()

        chosen_mentioned = False
        for name in chosen_names:
            if not is_valid_name_for_mismatch_check(name):
                continue
            parts = name.split()
            if len(parts) >= 2:
                first = parts[0].lower()
                last = parts[-1].lower()
                if first in combined_text and last in combined_text:
                    chosen_mentioned = True
                    break
            elif len(parts) == 1:
                first = parts[0].lower()
                if first in combined_text:
                    chosen_mentioned = True
                    break

        mismatch_found = False
        other_entity = None
        if not chosen_mentioned:
            for u in sm.data["units"]:
                if u["id"] == unit:
                    continue
                u_names = []
                if u.get("owner"):
                    u_names.append((u["owner"], "propietario"))
                if u.get("tenant"):
                    u_names.append((u["tenant"], "inquilino"))

                for name, role in u_names:
                    if not is_valid_name_for_mismatch_check(name):
                        continue
                    parts = name.split()
                    mentioned = False
                    if len(parts) >= 2:
                        first = parts[0].lower()
                        last = parts[-1].lower()
                        if first in combined_text and last in combined_text:
                            mentioned = True
                    elif len(parts) == 1:
                        first = parts[0].lower()
                        if first in combined_text:
                            mentioned = True

                    if mentioned:
                        mismatch_found = True
                        other_entity = (u["id"], name, role)
                        break
                if mismatch_found:
                    break

        if mismatch_found:
            if relative_url and os.path.exists(os.path.join(STATIC_DIR, relative_url.lstrip('/'))):
                try:
                    os.remove(os.path.join(STATIC_DIR, relative_url.lstrip('/')))
                except Exception as e:
                    print(f"Error removing file after mismatch: {e}")
            unit_id, name, role = other_entity
            return {
                "status": "error",
                "message": f"Discrepancia: El comprobante o mensaje hace referencia a {name} ({role} del Depto {unit_id}), pero seleccionaste el Depto {unit}."
            }

    # 3. Parse receipt details (Amount, Reference, Date)
    extracted_amount, extracted_reference, extracted_date = ReceiptProcessor.parse_bank_receipt_text(file_text)

    if not extracted_amount:
        extracted_amount = float(sm.config.get("default_aliquot_base", 70.0))
    if not extracted_reference:
        extracted_reference = parsed_msg["cedula"].split("-")[0] if parsed_msg["cedula"] else f"SIM-{int(datetime.now().timestamp())}"

    # 4. Conciliation against Bank Statement
    bank_match = None
    for b in sm.data.get("bank_statement", []):
        if b.get("reference") and extracted_reference and str(b["reference"]).strip().lower() == str(extracted_reference).strip().lower():
            if abs(b["amount"] - extracted_amount) < 0.01 and not b.get("reconciled", False):
                bank_match = b
                break

    is_additional_charge = False
    matching_charge = None

    for c in sm.data["additional_charges"]:
        if c["unit"] == unit_id and c["status"] == "Pendiente":
            if c["type"].lower() in message.lower() or any(keyword in message.lower() for keyword in c["description"].lower().split()):
                is_additional_charge = True
                matching_charge = c
                break

    payment_date = bank_match["date"] if bank_match else (extracted_date or datetime.now().strftime("%Y-%m-%d"))

    if is_additional_charge and matching_charge:
        if bank_match:
            sm.reconcile_additional_charge(matching_charge["id"], extracted_reference, payment_date)
            matching_charge["comprobante_url"] = relative_url
            sm.sync()

            receipt_no = f"REC-AC-{matching_charge['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{matching_charge['id']}.pdf")
            concept = f"{matching_charge['type']}: {matching_charge['description']}"

            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=unit_id,
                concept=concept,
                amount=matching_charge["amount"],
                late_fee=0.0,
                reference=extracted_reference,
                payment_date=payment_date
            )

            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=unit_id,
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    concept=concept,
                    amount=matching_charge["amount"],
                    payment_date=payment_date,
                    reference=extracted_reference
                )
            except Exception as e:
                print(f"Error sending upload-receipt charge notification: {e}")

            return {
                "status": "success",
                "message": f"Comprobante conciliado exitosamente con la transacción bancaria #{extracted_reference} por {sm.config['currency']}{extracted_amount:.2f}.",
                "details": {
                    "tipo": "Cargo Extraordinario / Multa",
                    "descripcion": matching_charge["description"],
                    "unidad": unit_id,
                    "propietario": owner_name,
                    "monto": matching_charge["amount"],
                    "referencia": extracted_reference,
                    "fecha_pago": payment_date,
                    "conciliacion": "Automática"
                },
                "receipt_url": f"/static/receipts/recibo_charge_{matching_charge['id']}.pdf",
                "comprobante_url": relative_url
            }
        else:
            matching_charge["status"] = "Validación Manual"
            matching_charge["reference"] = extracted_reference
            matching_charge["payment_date"] = payment_date
            matching_charge["comprobante_url"] = relative_url
            sm.sync()

            return {
                "status": "manual_validation",
                "message": f"Comprobante recibido por {sm.config['currency']}{extracted_amount:.2f}, pero la referencia '{extracted_reference}' no coincide con el Estado de Cuenta. Registrado para Validación Manual.",
                "details": {
                    "tipo": "Cargo Extraordinario / Multa",
                    "descripcion": matching_charge["description"],
                    "unidad": unit_id,
                    "propietario": owner_name,
                    "monto": matching_charge["amount"],
                    "referencia": extracted_reference,
                    "fecha_pago": payment_date,
                    "conciliacion": "Pendiente de Verificación Manual"
                },
                "comprobante_url": relative_url
            }

    else:
        if bank_match:
            rec_result = sm.reconcile_payment(
                unit=unit_id,
                month=parsed_msg["month"],
                year=parsed_msg["year"],
                amount=extracted_amount,
                reference=extracted_reference,
                payment_date=payment_date
            )
            aliquot = rec_result["aliquot"]
            aliquot["comprobante_url"] = relative_url
            sm.sync()

            receipt_no = f"REC-AL-{aliquot['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"

            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=unit_id,
                concept=concept,
                amount=rec_result["aliquot_paid"],
                late_fee=rec_result["late_fee_paid"],
                reference=extracted_reference,
                payment_date=payment_date,
                abono_deuda=rec_result["abono_deuda"],
                pago_extra=rec_result["pago_extra"],
                saldo_deuda=rec_result["saldo_deuda"],
                advance_aliquots=rec_result.get("advance_aliquots")
            )

            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=unit_id,
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    concept=concept,
                    amount=rec_result["aliquot_paid"] + rec_result["late_fee_paid"] + rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=payment_date,
                    reference=extracted_reference
                )
            except Exception as e:
                print(f"Error sending upload-receipt aliquot notification: {e}")

            late_fee_msg = f" (incluye multa de {sm.config['currency']}{rec_result['late_fee_paid']:.2f} por pago después del día {sm.config['late_fee_day']})" if rec_result["late_fee_paid"] > 0 else ""

            return {
                "status": "success",
                "message": f"Comprobante conciliado exitosamente con la transacción bancaria #{extracted_reference} por {sm.config['currency']}{extracted_amount:.2f}{late_fee_msg}.",
                "details": {
                    "tipo": "Alícuota Ordinaria",
                    "mes": f"{aliquot['month']} / {aliquot['year']}",
                    "unidad": unit_id,
                    "propietario": owner_name,
                    "monto": rec_result["aliquot_paid"],
                    "multa": rec_result["late_fee_paid"],
                    "referencia": extracted_reference,
                    "fecha_pago": payment_date,
                    "conciliacion": "Automática"
                },
                "receipt_url": f"/static/receipts/recibo_{aliquot['id']}.pdf",
                "comprobante_url": relative_url
            }
        else:
            aliquot_id = f"{unit_id}-{parsed_msg['year']}-{parsed_msg['month']}"

            aliquot = None
            for a in sm.data["aliquots"]:
                if a["unit"] == unit_id and a["month"].lower() == parsed_msg["month"].lower() and int(a["year"]) == int(parsed_msg["year"]):
                    aliquot = a
                    break

            late_fee = sm.check_late_fee(payment_date, parsed_msg["month"], parsed_msg["year"])

            if aliquot:
                aliquot["status"] = "Validación Manual"
                aliquot["reference"] = extracted_reference
                aliquot["payment_date"] = payment_date
                aliquot["late_fee"] = late_fee
                aliquot["comprobante_url"] = relative_url
            else:
                aliquot = {
                    "id": aliquot_id,
                    "unit": unit_id,
                    "month": parsed_msg["month"],
                    "year": parsed_msg["year"],
                    "amount": extracted_amount,
                    "late_fee": late_fee,
                    "payment_date": payment_date,
                    "reference": extracted_reference,
                    "status": "Validación Manual",
                    "comprobante_url": relative_url
                }
                sm.data["aliquots"].append(aliquot)

            sm.sync()

            return {
                "status": "manual_validation",
                "message": f"Comprobante recibido por {sm.config['currency']}{extracted_amount:.2f}, pero la referencia bancaria '{extracted_reference}' no se encontró en el Estado de Cuenta. Marcado para Validación Manual.",
                "details": {
                    "tipo": "Alícuota Ordinaria",
                    "mes": f"{parsed_msg['month']} / {parsed_msg['year']}",
                    "unidad": unit_id,
                    "propietario": owner_name,
                    "monto": extracted_amount,
                    "multa": late_fee,
                    "referencia": extracted_reference,
                    "fecha_pago": payment_date,
                    "conciliacion": "Pendiente de Verificación Manual"
                },
                "comprobante_url": relative_url
            }

@router.post("/api/whatsapp/webhook")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    NumMedia: int = Form(0),
    MediaUrl0: Optional[str] = Form(None),
    MediaContentType0: Optional[str] = Form(None)
):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    save_structured_receipt = get_save_structured_receipt()

    # Verify Twilio webhook signature if twilio_token is configured
    twilio_token = sm.config.get("twilio_token")
    if twilio_token:
        sig = request.headers.get("X-Twilio-Signature")
        url = str(request.url)
        form_params = {
            "Body": Body,
            "From": From,
            "NumMedia": str(NumMedia),
        }
        if MediaUrl0:
            form_params["MediaUrl0"] = MediaUrl0
        if MediaContentType0:
            form_params["MediaContentType0"] = MediaContentType0
        if sig and not verify_twilio_signature(twilio_token, sig, url, form_params):
            raise HTTPException(status_code=403, detail="Firma de webhook de Twilio no válida.")

    base_url = str(request.base_url)
    parsed_msg = ReceiptProcessor.parse_whatsapp_message(Body)

    if not parsed_msg["unit"]:
        response_text = (
            "❌ No pudimos identificar tu número de departamento o unidad en el mensaje.\n\n"
            "Por favor, vuelve a enviar tu comprobante con el siguiente formato:\n"
            "Nombre: [Tu Nombre]\n"
            "Cédula: [Tu Cédula]\n"
            "Departamento: [Número de Depto]\n"
            "Mes: [Mes de pago]"
        )
        return Response(
            content=f"<Response><Message><Body>{response_text}</Body></Message></Response>",
            media_type="application/xml"
        )

    unit_id = parsed_msg["unit"]
    unit_exists = any(u["id"] == unit_id for u in sm.data["units"])
    if not unit_exists:
        response_text = f"❌ El departamento '{unit_id}' no está registrado en el condominio. Por favor verifica los datos."
        return Response(
            content=f"<Response><Message><Body>{response_text}</Body></Message></Response>",
            media_type="application/xml"
        )

    owner_name = next(u["owner"] for u in sm.data["units"] if u["id"] == unit_id)
    recipient_name = sm.get_receipt_recipient_name(unit_id)

    file_text = ""
    extracted_amount = None
    extracted_reference = None
    relative_url = ""

    if NumMedia > 0 and MediaUrl0:
        ext = ".jpg"
        if MediaContentType0 == "application/pdf":
            ext = ".pdf"
        elif MediaContentType0 == "image/png":
            ext = ".png"

        try:
            r = requests.get(MediaUrl0, timeout=10.0)
            if r.status_code == 200:
                year = parsed_msg.get("year") or datetime.now().year
                month = parsed_msg.get("month")
                if not month:
                    months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
                    month = months[datetime.now().month - 1]
                    parsed_msg["month"] = month

                relative_url, dest_path = save_structured_receipt(r.content, f"whatsapp_receipt{ext}", unit_id, year, month)

                if ext == ".pdf":
                    file_text = ReceiptProcessor.extract_text_from_pdf(dest_path)
                else:
                    file_text = OCRHelper.extract_text(dest_path)
        except Exception as e:
            print(f"Error downloading or parsing Twilio media: {e}")

    if not file_text:
        file_text = Body

    extracted_amount, extracted_reference, extracted_date = ReceiptProcessor.parse_bank_receipt_text(file_text)

    if not extracted_amount:
        extracted_amount = next(u["aliquot_base"] for u in sm.data["units"] if u["id"] == unit_id)
    if not extracted_reference:
        extracted_reference = parsed_msg["cedula"].split("-")[0] if parsed_msg["cedula"] else f"REF-{int(datetime.now().timestamp())}"

    is_additional_charge = False
    matching_charge = None

    for c in sm.data["additional_charges"]:
        if c["unit"] == unit_id and c["status"] == "Pendiente":
            if c["type"].lower() in Body.lower() or any(keyword in Body.lower() for keyword in c["description"].lower().split()):
                is_additional_charge = True
                matching_charge = c
                break

    bank_match = None
    for b in sm.data.get("bank_statement", []):
        if b.get("reference") and extracted_reference and str(b["reference"]).strip().lower() == str(extracted_reference).strip().lower():
            if abs(b["amount"] - extracted_amount) < 0.01 and not b.get("reconciled", False):
                bank_match = b
                break

    payment_date = bank_match["date"] if bank_match else (extracted_date or datetime.now().strftime("%Y-%m-%d"))

    if is_additional_charge and matching_charge:
        if bank_match:
            sm.reconcile_additional_charge(matching_charge["id"], extracted_reference, payment_date)
            matching_charge["comprobante_url"] = relative_url
            sm.sync()

            receipt_no = f"REC-AC-{matching_charge['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{matching_charge['id']}.pdf")
            concept = f"{matching_charge['type']}: {matching_charge['description']}"

            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=unit_id,
                concept=concept,
                amount=matching_charge["amount"],
                late_fee=0.0,
                reference=extracted_reference,
                payment_date=payment_date
            )

            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=unit_id,
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    concept=concept,
                    amount=matching_charge["amount"],
                    payment_date=payment_date,
                    reference=extracted_reference
                )
            except Exception as e:
                print(f"Error sending webhook charge notification: {e}")

            public_pdf_url = f"{base_url}static/receipts/recibo_charge_{matching_charge['id']}.pdf"
            response_xml = (
                "<Response><Message>"
                f"<Body>✅ ¡Gracias! Tu pago de {matching_charge['type']} por {sm.config['currency']}{matching_charge['amount']:.2f} ha sido conciliado en el banco Produbanco. Adjuntamos tu recibo oficial.</Body>"
                f"<Media>{public_pdf_url}</Media>"
                "</Message></Response>"
            )
        else:
            matching_charge["status"] = "Validación Manual"
            matching_charge["reference"] = extracted_reference
            matching_charge["payment_date"] = payment_date
            matching_charge["comprobante_url"] = relative_url
            sm.sync()

            response_xml = (
                "<Response><Message>"
                f"<Body>⚠️ Recibimos tu comprobante de {matching_charge['type']} por {sm.config['currency']}{extracted_amount:.2f}, pero la referencia {extracted_reference} está pendiente de confirmación bancaria. El pago ha quedado registrado en estado 'Validación Manual'.</Body>"
                "</Message></Response>"
            )
    else:
        if not parsed_msg["month"]:
            months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
            parsed_msg["month"] = months[datetime.now().month - 1]

        if bank_match:
            rec_result = sm.reconcile_payment(
                unit=unit_id,
                month=parsed_msg["month"],
                year=parsed_msg["year"],
                amount=extracted_amount,
                reference=extracted_reference,
                payment_date=payment_date
            )
            aliquot = rec_result["aliquot"]
            aliquot["comprobante_url"] = relative_url
            sm.sync()

            receipt_no = f"REC-AL-{aliquot['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"

            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=unit_id,
                concept=concept,
                amount=rec_result["aliquot_paid"],
                late_fee=rec_result["late_fee_paid"],
                reference=extracted_reference,
                payment_date=payment_date,
                abono_deuda=rec_result["abono_deuda"],
                pago_extra=rec_result["pago_extra"],
                saldo_deuda=rec_result["saldo_deuda"],
                advance_aliquots=rec_result.get("advance_aliquots")
            )

            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=unit_id,
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    concept=concept,
                    amount=rec_result["aliquot_paid"] + rec_result["late_fee_paid"] + rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=payment_date,
                    reference=extracted_reference
                )
            except Exception as e:
                print(f"Error sending webhook aliquot notification: {e}")

            late_fee_msg = f" (incluye recargo de {sm.config['currency']}{rec_result['late_fee_paid']:.2f} por pago atrasado)" if rec_result["late_fee_paid"] > 0 else ""
            public_pdf_url = f"{base_url}static/receipts/recibo_{aliquot['id']}.pdf"

            response_xml = (
                "<Response><Message>"
                f"<Body>✅ ¡Muchas gracias! Tu pago de la alícuota de {aliquot['month']} ha sido registrado y conciliado exitosamente{late_fee_msg}. Adjuntamos tu recibo digital oficial.</Body>"
                f"<Media>{public_pdf_url}</Media>"
                "</Message></Response>"
            )
        else:
            aliquot_id = f"{unit_id}-{parsed_msg['year']}-{parsed_msg['month']}"
            late_fee = sm.check_late_fee(payment_date, parsed_msg["month"], parsed_msg["year"])

            aliquot = None
            for a in sm.data["aliquots"]:
                if a["unit"] == unit_id and a["month"].lower() == parsed_msg["month"].lower() and int(a["year"]) == int(parsed_msg["year"]):
                    aliquot = a
                    break

            if aliquot:
                aliquot["status"] = "Validación Manual"
                aliquot["reference"] = extracted_reference
                aliquot["payment_date"] = payment_date
                aliquot["late_fee"] = late_fee
                aliquot["comprobante_url"] = relative_url
            else:
                aliquot = {
                    "id": aliquot_id,
                    "unit": unit_id,
                    "month": parsed_msg["month"],
                    "year": parsed_msg["year"],
                    "amount": extracted_amount,
                    "late_fee": late_fee,
                    "payment_date": payment_date,
                    "reference": extracted_reference,
                    "status": "Validación Manual",
                    "comprobante_url": relative_url
                }
                sm.data["aliquots"].append(aliquot)
            sm.sync()

            response_xml = (
                "<Response><Message>"
                f"<Body>⚠️ Comprobante recibido. La referencia {extracted_reference} por {sm.config['currency']}{extracted_amount:.2f} no coincide con el Estado de Cuenta. El pago de la alícuota de {parsed_msg['month']} queda en estado 'Validación Manual' hasta confirmación de la administración.</Body>"
                "</Message></Response>"
            )

    return Response(content=response_xml, media_type="application/xml")

@router.get("/api/whatsapp/meta-webhook")
def verify_meta_webhook(request: Request):
    sm = get_sm()
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    expected_token = sm.config.get("meta_wa_verify_token")

    if mode == "subscribe" and token == expected_token:
        print("[Meta Webhook] Verification successful!")
        return Response(content=challenge, media_type="text/plain")

    print("[Meta Webhook] Verification failed.")
    raise HTTPException(status_code=403, detail="Verification token mismatch.")

@router.post("/api/whatsapp/meta-webhook")
async def process_meta_webhook(request: Request):
    sm = get_sm()
    STATIC_DIR = get_static_dir()
    RECEIPTS_DIR = get_receipts_dir()
    save_structured_receipt = get_save_structured_receipt()

    body_bytes = await request.body()

    meta_secret = sm.config.get("meta_wa_app_secret") or sm.config.get("meta_wa_token")
    if meta_secret:
        hub_signature = request.headers.get("X-Hub-Signature-256")
        if hub_signature and not verify_meta_signature(body_bytes, hub_signature, meta_secret):
            raise HTTPException(status_code=403, detail="Firma de webhook de Meta no válida.")

    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception as e:
        print(f"[Meta Webhook] JSON parse error: {e}")
        return {"status": "ignored", "reason": "invalid_json"}

    print("[Meta Webhook] Event received successfully.")

    entry = data.get("entry", [])
    if not entry:
        return {"status": "ignored", "reason": "empty_entry"}

    changes = entry[0].get("changes", [])
    if not changes:
        return {"status": "ignored", "reason": "empty_changes"}

    val = changes[0].get("value", {})
    messages = val.get("messages", [])
    if not messages:
        return {"status": "ignored", "reason": "no_messages"}

    msg = messages[0]
    from_number = msg.get("from")
    msg_type = msg.get("type")

    msg_text = ""
    if msg_type == "text":
        msg_text = msg.get("text", {}).get("body", "")
    elif msg_type in ["image", "document"]:
        msg_text = msg.get(msg_type, {}).get("caption", "")

    phone_masked = f"***{from_number[-4:]}" if from_number and len(from_number) >= 4 else "unknown"
    print(f"[Meta Webhook] Message received from {phone_masked} (Type: {msg_type})")

    unit_id = None
    owner_name = None

    def clean_phone(p):
        return "".join(filter(str.isdigit, str(p)))

    clean_from = clean_phone(from_number)
    for u in sm.data["units"]:
        u_p1 = clean_phone(u.get("phone1", ""))
        u_p2 = clean_phone(u.get("phone2", ""))
        if clean_from and ((u_p1 and (clean_from[-9:] == u_p1[-9:] or u_p1[-9:] == clean_from[-9:])) or
                           (u_p2 and (clean_from[-9:] == u_p2[-9:] or u_p2[-9:] == clean_from[-9:]))):
            unit_id = u["id"]
            owner_name = u["owner"]
            break

    parsed_msg = ReceiptProcessor.parse_whatsapp_message(msg_text)
    if not unit_id:
        if parsed_msg["unit"]:
            unit_id = parsed_msg["unit"]
            unit_exists = any(u["id"] == unit_id for u in sm.data["units"])
            if unit_exists:
                owner_name = next(u["owner"] for u in sm.data["units"] if u["id"] == unit_id)
            else:
                unit_id = None

    if not unit_id:
        response_text = (
            "❌ No pudimos identificar tu número de departamento o unidad en el mensaje o tu teléfono no está registrado.\n\n"
            "Por favor, vuelve a enviar tu mensaje asegurándote de incluir el formato:\n"
            "Departamento: [Número de Depto]"
        )
        send_meta_reply_text(from_number, response_text)
        return {"status": "error", "reason": "unit_not_identified"}

    if not parsed_msg["month"]:
        months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        parsed_msg["month"] = months[datetime.now().month - 1]
    year = parsed_msg.get("year") or datetime.now().year
    month = parsed_msg.get("month")
    recipient_name = sm.get_receipt_recipient_name(unit_id)

    file_bytes = None
    file_name = None
    file_ext = ""
    relative_url = ""

    if msg_type in ["image", "document"]:
        media_info = msg.get(msg_type, {})
        media_id = media_info.get("id")
        mime_type = media_info.get("mime_type", "")

        if msg_type == "document":
            file_name = media_info.get("filename", "comprobante.pdf")
            file_ext = os.path.splitext(file_name)[1].lower()
        else:
            file_name = "comprobante.jpg"
            file_ext = ".pdf" if mime_type == "application/pdf" else (".png" if mime_type == "image/png" else ".jpg")

        print(f"[Meta Webhook] Downloading media {media_id} ({mime_type})...")

        meta_token = sm.config.get("meta_wa_token")
        if meta_token and media_id:
            try:
                headers = {"Authorization": f"Bearer {meta_token}"}
                r_info = requests.get(f"https://graph.facebook.com/v19.0/{media_id}", headers=headers, timeout=10.0)
                if r_info.status_code == 200:
                    media_url = r_info.json().get("url")
                    if media_url:
                        r_file = requests.get(media_url, headers=headers, timeout=10.0)
                        if r_file.status_code == 200:
                            file_bytes = r_file.content
                            relative_url, dest_path = save_structured_receipt(file_bytes, file_name, unit_id, year, month)
            except Exception as e:
                print(f"[Meta Webhook] Error downloading Meta media: {e}")

    file_text = ""
    if file_bytes and relative_url:
        dest_path = os.path.join(STATIC_DIR, relative_url.lstrip('/'))
        try:
            if file_ext == ".pdf":
                file_text = ReceiptProcessor.extract_text_from_pdf(dest_path)
            else:
                file_text = OCRHelper.extract_text(dest_path)
        except Exception as e:
            print(f"[Meta Webhook] Error extracting text: {e}")

    if not file_text:
        file_text = msg_text

    extracted_amount, extracted_reference, extracted_date = ReceiptProcessor.parse_bank_receipt_text(file_text)
    if not extracted_amount:
        extracted_amount = next(u["aliquot_base"] for u in sm.data["units"] if u["id"] == unit_id)
    if not extracted_reference:
        extracted_reference = parsed_msg["cedula"].split("-")[0] if parsed_msg["cedula"] else f"REF-{int(datetime.now().timestamp())}"

    is_additional_charge = False
    matching_charge = None

    for c in sm.data["additional_charges"]:
        if c["unit"] == unit_id and c["status"] == "Pendiente":
            if c["type"].lower() in msg_text.lower() or any(keyword in msg_text.lower() for keyword in c["description"].lower().split()):
                is_additional_charge = True
                matching_charge = c
                break

    bank_match = None
    for b in sm.data.get("bank_statement", []):
        if b.get("reference") and extracted_reference and str(b["reference"]).strip().lower() == str(extracted_reference).strip().lower():
            if abs(b["amount"] - extracted_amount) < 0.01 and not b.get("reconciled", False):
                bank_match = b
                break

    payment_date = bank_match["date"] if bank_match else (extracted_date or datetime.now().strftime("%Y-%m-%d"))

    base_url = str(request.base_url)

    if is_additional_charge and matching_charge:
        if bank_match:
            sm.reconcile_additional_charge(matching_charge["id"], extracted_reference, payment_date)
            matching_charge["comprobante_url"] = relative_url
            sm.sync()

            receipt_no = f"REC-AC-{matching_charge['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{matching_charge['id']}.pdf")
            concept = f"{matching_charge['type']}: {matching_charge['description']}"
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf, receipt_no=receipt_no, resident_name=recipient_name,
                unit=unit_id, concept=concept, amount=matching_charge["amount"],
                late_fee=0.0, reference=extracted_reference, payment_date=payment_date
            )

            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm, unit_id=unit_id, receipt_pdf_path=dest_pdf, receipt_no=receipt_no,
                    resident_name=recipient_name, concept=concept, amount=matching_charge["amount"],
                    payment_date=payment_date, reference=extracted_reference
                )
            except Exception as e:
                print(f"Error logging notifications: {e}")

            reply_body = f"✅ ¡Gracias! Tu pago de {matching_charge['type']} por {sm.config['currency']}{matching_charge['amount']:.2f} ha sido conciliado en el banco Produbanco. Adjuntamos tu recibo oficial."
            send_meta_reply_text(from_number, reply_body)

            public_pdf_url = f"{base_url}static/receipts/recibo_charge_{matching_charge['id']}.pdf"
            if "127.0.0.1" not in public_pdf_url:
                send_meta_reply_document(from_number, public_pdf_url, f"recibo_charge_{matching_charge['id']}.pdf", f"Recibo Oficial {receipt_no}")
        else:
            matching_charge["status"] = "Validación Manual"
            matching_charge["reference"] = extracted_reference
            matching_charge["payment_date"] = payment_date
            matching_charge["comprobante_url"] = relative_url
            sm.sync()

            reply_body = f"⚠️ Recibimos tu comprobante de {matching_charge['type']} por {sm.config['currency']}{extracted_amount:.2f}, pero la referencia {extracted_reference} está pendiente de confirmación bancaria. El pago ha quedado registrado en estado 'Validación Manual'."
            send_meta_reply_text(from_number, reply_body)
    else:
        if bank_match:
            rec_result = sm.reconcile_payment(
                unit=unit_id, month=parsed_msg["month"], year=parsed_msg["year"],
                amount=extracted_amount, reference=extracted_reference, payment_date=payment_date
            )
            aliquot = rec_result["aliquot"]
            aliquot["comprobante_url"] = relative_url
            sm.sync()

            receipt_no = f"REC-AL-{aliquot['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf, receipt_no=receipt_no, resident_name=recipient_name,
                unit=unit_id, concept=concept, amount=rec_result["aliquot_paid"],
                late_fee=rec_result["late_fee_paid"], reference=extracted_reference, payment_date=payment_date,
                abono_deuda=rec_result["abono_deuda"], pago_extra=rec_result["pago_extra"], saldo_deuda=rec_result["saldo_deuda"],
                advance_aliquots=rec_result.get("advance_aliquots")
            )

            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm, unit_id=unit_id, receipt_pdf_path=dest_pdf, receipt_no=receipt_no,
                    resident_name=recipient_name, concept=concept, amount=rec_result["aliquot_paid"] + rec_result["late_fee_paid"] + rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=payment_date, reference=extracted_reference
                )
            except Exception as e:
                print(f"Error logging notifications: {e}")

            late_fee_msg = f" (incluye recargo de {sm.config['currency']}{rec_result['late_fee_paid']:.2f} por pago atrasado)" if rec_result["late_fee_paid"] > 0 else ""
            reply_body = f"✅ ¡Muchas gracias! Tu pago de la alícuota de {aliquot['month']} ha sido registrado y conciliado exitosamente{late_fee_msg}. Adjuntamos tu recibo digital oficial."
            send_meta_reply_text(from_number, reply_body)

            public_pdf_url = f"{base_url}static/receipts/recibo_{aliquot['id']}.pdf"
            if "127.0.0.1" not in public_pdf_url:
                send_meta_reply_document(from_number, public_pdf_url, f"recibo_{aliquot['id']}.pdf", f"Recibo Oficial {receipt_no}")
        else:
            aliquot_id = f"{unit_id}-{parsed_msg['year']}-{parsed_msg['month']}"
            late_fee = sm.check_late_fee(payment_date, parsed_msg["month"], parsed_msg["year"])

            aliquot = None
            for a in sm.data["aliquots"]:
                if a["unit"] == unit_id and a["month"].lower() == parsed_msg["month"].lower() and int(a["year"]) == int(parsed_msg["year"]):
                    aliquot = a
                    break

            if aliquot:
                aliquot["status"] = "Validación Manual"
                aliquot["reference"] = extracted_reference
                aliquot["payment_date"] = payment_date
                aliquot["late_fee"] = late_fee
                aliquot["comprobante_url"] = relative_url
            else:
                aliquot = {
                    "id": aliquot_id, "unit": unit_id, "month": parsed_msg["month"], "year": parsed_msg["year"],
                    "amount": extracted_amount, "late_fee": late_fee, "payment_date": payment_date,
                    "reference": extracted_reference, "status": "Validación Manual", "comprobante_url": relative_url
                }
                sm.data["aliquots"].append(aliquot)
            sm.sync()

            reply_body = f"⚠️ Comprobante recibido. La referencia {extracted_reference} por {sm.config['currency']}{extracted_amount:.2f} no coincide con el Estado de Cuenta. El pago de la alícuota de {parsed_msg['month']} queda en estado 'Validación Manual' hasta confirmación de la administración."
            send_meta_reply_text(from_number, reply_body)

    return {"status": "success"}
