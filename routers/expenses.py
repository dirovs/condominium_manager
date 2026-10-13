import io
import csv
from fastapi import APIRouter, HTTPException, Response
from schemas import ExpenseCreate, ExpenseUpdate, OtherIncomeCreate, OtherIncomeUpdate

router = APIRouter(tags=["Expenses & Other Incomes"])

def get_sm():
    from app import sm
    return sm

@router.get("/api/expenses")
def get_expenses():
    sm = get_sm()
    return sm.get_expenses_list()

@router.post("/api/expenses")
def create_expense(expense: ExpenseCreate):
    sm = get_sm()
    return sm.add_expense(expense.date, expense.category, expense.description, expense.amount)

@router.put("/api/expenses/{id}")
def update_expense(id: str, exp: ExpenseUpdate):
    sm = get_sm()
    updated = sm.update_expense(id, exp.date, exp.category, exp.description, exp.amount)
    if not updated:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")
    return updated

@router.delete("/api/expenses/{id}")
def delete_expense(id: str):
    sm = get_sm()
    success = sm.delete_expense(id)
    if not success:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")
    return {"status": "success"}

@router.get("/api/expenses/export/csv")
def export_expenses_csv():
    sm = get_sm()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Fecha", "Categoría", "Descripción", "Monto"])
    for e in sm.data["expenses"]:
        writer.writerow([e["id"], e["date"], e["category"], e["description"], f"{e['amount']:.2f}"])

    csv_data = output.getvalue()
    output.close()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_gastos.csv"}
    )

@router.get("/api/other-incomes")
def get_other_incomes():
    sm = get_sm()
    return sm.data.get("other_incomes", [])

@router.post("/api/other-incomes")
def create_other_income(inc: OtherIncomeCreate):
    sm = get_sm()
    return sm.add_other_income(inc.date, inc.concept, inc.amount, inc.reference)

@router.put("/api/other-incomes/{id}")
def update_other_income(id: int, inc: OtherIncomeUpdate):
    sm = get_sm()
    updated = sm.update_other_income(id, inc.date, inc.concept, inc.amount, inc.reference)
    if not updated:
        raise HTTPException(status_code=404, detail="Ingreso no encontrado")
    return updated

@router.delete("/api/other-incomes/{id}")
def delete_other_income(id: int):
    sm = get_sm()
    success = sm.delete_other_income(id)
    if not success:
        raise HTTPException(status_code=404, detail="Ingreso no encontrado")
    return {"status": "success"}
