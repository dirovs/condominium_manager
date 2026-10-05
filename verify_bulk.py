import os
import json
from sheets_manager import SheetsManager

def test_mass_reconciliation():
    sm = SheetsManager()
    
    # Let's add an aliquot in "Validación Manual"
    aliquot_id = "701-2026-Mayo"
    
    # Clean-up existing if any
    sm.data["aliquots"] = [a for a in sm.data["aliquots"] if a["id"] != aliquot_id]
    
    # Add new validation manual aliquot
    sm.data["aliquots"].append({
        "id": aliquot_id,
        "unit": "701",
        "month": "Mayo",
        "year": 2026,
        "amount": 70.0,
        "late_fee": 0.0,
        "payment_date": "2026-06-10",
        "reference": "DEP-99882",  # Not in bank statement yet
        "status": "Validación Manual"
    })
    
    # Add matching bank transaction
    sm.data["bank_statement"] = [b for b in sm.data["bank_statement"] if b["reference"] != "DEP-99882"]
    sm.data["bank_statement"].append({
        "date": "2026-06-10",
        "reference": "DEP-99882",
        "amount": 70.0,
        "detail": "TRANSF Jenny Portilla 701",
        "reconciled": False
    })
    
    sm.sync()
    
    print("Running reconcile_mass...")
    aliquots_reconciled, charges_reconciled = sm.reconcile_mass()
    
    print(f"Reconciled: Aliquots={aliquots_reconciled}, Charges={charges_reconciled}")
    
    # Assertions
    refetched = next(a for a in sm.data["aliquots"] if a["id"] == aliquot_id)
    assert refetched["status"] == "Pagado", f"Expected Pagado, got {refetched['status']}"
    assert refetched["reference"] == "DEP-99882", f"Expected DEP-99882, got {refetched['reference']}"
    
    # The bank statement should be marked reconciled
    bank_tx = next(b for b in sm.data["bank_statement"] if b["reference"] == "DEP-99882")
    assert bank_tx["reconciled"] == True, "Expected bank transaction to be reconciled"
    
    print("[OK] Mass reconciliation verification script PASSED!")

if __name__ == "__main__":
    test_mass_reconciliation()
