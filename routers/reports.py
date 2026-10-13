import os
import io
import csv
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.responses import FileResponse

from security import require_admin, get_current_user
from receipt_processor import ReceiptProcessor, FinancialReportPDFGenerator, FinancialReportExcelGenerator

router = APIRouter(tags=["Reports & Financial Summary"])

def get_sm():
    from app import sm
    return sm

def get_receipts_dir():
    from app import RECEIPTS_DIR
    return RECEIPTS_DIR

@router.get("/api/summary")
def get_summary():
    sm = get_sm()
    return sm.get_summary()

@router.get("/api/debtors")
def get_debtors(current_user: Optional[dict] = Depends(get_current_user)):
    sm = get_sm()
    all_debtors = sm.get_debtors()
    if current_user and isinstance(current_user, dict):
        role = str(current_user.get("role", "")).lower()
        if role in ["owner", "tenant"]:
            my_unit = str(current_user.get("unit_id") or current_user.get("unit") or "")
            return [d for d in all_debtors if str(d.get("unit")) == my_unit]
    return all_debtors

@router.get("/api/debtors/export/csv")
def export_debtors_csv(current_user: dict = Depends(require_admin)):
    sm = get_sm()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Unidad", "Propietario", "Meses Vencidos", "Detalles Deuda", "Deuda Total"])

    debtors = sm.get_debtors()
    for d in debtors:
        writer.writerow([
            d["unit"], d["owner"], d["unpaid_aliquots_count"],
            ", ".join(d["details"]), f"{d['total_debt']:.2f}"
        ])

    csv_data = output.getvalue()
    output.close()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_deudores.csv"}
    )

@router.get("/api/reports/financial-pdf")
def export_financial_pdf(current_user: dict = Depends(require_admin)):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    pdf_path = os.path.join(RECEIPTS_DIR, "reporte_financiero_edificio.pdf")

    summary = sm.get_summary()
    aliquots = sm.get_aliquots_with_details()
    expenses = sm.get_expenses_list()
    debtors = sm.get_debtors()

    ReceiptProcessor.generate_financial_report_pdf(pdf_path, summary, aliquots, expenses, debtors)

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename="Reporte_Financiero_Edificio.pdf"
    )

@router.get("/api/notification-logs")
def get_notification_logs(current_user: dict = Depends(require_admin)):
    sm = get_sm()
    return sm.data.get("notification_logs", [])

@router.get("/api/reports/monthly-closing")
def get_monthly_closing_report(
    month: Optional[str] = None,
    year: Optional[int] = None,
    current_user: dict = Depends(require_admin)
):
    sm = get_sm()
    try:
        return sm.get_monthly_closing_report_data(month=month, year=year)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating monthly closing report: {e}")


@router.get("/api/reports/preview")
def get_reports_preview(
    report_type: str = "payments",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    unit: Optional[str] = None,
    category: Optional[str] = None,
    min_debt: float = 0.0,
    status_filter: Optional[str] = None,
    record_type: Optional[str] = None,
    current_user: dict = Depends(require_admin)
):
    sm = get_sm()
    try:
        report_type_clean = str(report_type or "payments").strip().lower()
        if report_type_clean == "payments":
            return sm.get_payments_report_data(start_date=start_date, end_date=end_date, unit=unit, category=category)
        elif report_type_clean in ["debtors", "deudores"]:
            return sm.get_debtors_report_data(unit=unit, min_debt=min_debt, status_filter=status_filter)
        elif report_type_clean in ["unreconciled", "sin_conciliar"]:
            return sm.get_unreconciled_report_data(start_date=start_date, end_date=end_date, record_type=record_type)
        else:
            raise HTTPException(status_code=400, detail=f"Tipo de reporte '{report_type}' no soportado.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/reports/export/pdf")
def export_report_pdf(
    report_type: str = "payments",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    unit: Optional[str] = None,
    category: Optional[str] = None,
    min_debt: float = 0.0,
    status_filter: Optional[str] = None,
    record_type: Optional[str] = None,
    current_user: dict = Depends(require_admin)
):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    try:
        report_type_clean = str(report_type or "payments").strip().lower()
        condo_name = sm.config.get("condo_name", "Condominio El Mirador")
        condo_address = sm.config.get("condo_address", "")
        condo_ruc = sm.config.get("condo_ruc", "")
        currency = sm.config.get("currency", "$")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if report_type_clean == "payments":
            data = sm.get_payments_report_data(start_date=start_date, end_date=end_date, unit=unit, category=category)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_pagos_{timestamp}.pdf")
            FinancialReportPDFGenerator.generate_payments_report_pdf(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/pdf", filename=f"reporte_pagos_{timestamp}.pdf")

        elif report_type_clean in ["debtors", "deudores"]:
            data = sm.get_debtors_report_data(unit=unit, min_debt=min_debt, status_filter=status_filter)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_deudores_{timestamp}.pdf")
            FinancialReportPDFGenerator.generate_debtors_report_pdf(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/pdf", filename=f"reporte_deudores_{timestamp}.pdf")

        elif report_type_clean in ["unreconciled", "sin_conciliar"]:
            data = sm.get_unreconciled_report_data(start_date=start_date, end_date=end_date, record_type=record_type)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_sin_conciliar_{timestamp}.pdf")
            FinancialReportPDFGenerator.generate_unreconciled_report_pdf(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/pdf", filename=f"reporte_sin_conciliar_{timestamp}.pdf")

        else:
            raise HTTPException(status_code=400, detail=f"Tipo de reporte '{report_type}' no soportado.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/reports/export/excel")
def export_report_excel(
    report_type: str = "payments",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    unit: Optional[str] = None,
    category: Optional[str] = None,
    min_debt: float = 0.0,
    status_filter: Optional[str] = None,
    record_type: Optional[str] = None,
    current_user: dict = Depends(require_admin)
):
    sm = get_sm()
    RECEIPTS_DIR = get_receipts_dir()
    try:
        report_type_clean = str(report_type or "payments").strip().lower()
        condo_name = sm.config.get("condo_name", "Condominio El Mirador")
        condo_address = sm.config.get("condo_address", "")
        condo_ruc = sm.config.get("condo_ruc", "")
        currency = sm.config.get("currency", "$")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if report_type_clean == "payments":
            data = sm.get_payments_report_data(start_date=start_date, end_date=end_date, unit=unit, category=category)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_pagos_{timestamp}.xlsx")
            FinancialReportExcelGenerator.generate_payments_report_excel(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"reporte_pagos_{timestamp}.xlsx")

        elif report_type_clean in ["debtors", "deudores"]:
            data = sm.get_debtors_report_data(unit=unit, min_debt=min_debt, status_filter=status_filter)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_deudores_{timestamp}.xlsx")
            FinancialReportExcelGenerator.generate_debtors_report_excel(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"reporte_deudores_{timestamp}.xlsx")

        elif report_type_clean in ["unreconciled", "sin_conciliar"]:
            data = sm.get_unreconciled_report_data(start_date=start_date, end_date=end_date, record_type=record_type)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_sin_conciliar_{timestamp}.xlsx")
            FinancialReportExcelGenerator.generate_unreconciled_report_excel(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"reporte_sin_conciliar_{timestamp}.xlsx")

        else:
            raise HTTPException(status_code=400, detail=f"Tipo de reporte '{report_type}' no soportado.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
