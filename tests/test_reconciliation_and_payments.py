import pytest


def test_payment_reconciliation_and_abonos(test_sm):
    unit_id = "TEST-REC-UNIT"
    test_sm.add_or_update_unit(
        unit_id=unit_id,
        owner="Propietario Conciliacion",
        phone1="0987654321",
        email1="concilia@test.com",
        aliquot_base=70.0
    )

    # Clean existing aliquots for this unit
    test_sm.data["aliquots"] = [a for a in test_sm.data.get("aliquots", []) if a["unit"] != unit_id]

    # 1. Partial payment (Abono de $40 a una alícuota de $70)
    rec_res = test_sm.reconcile_payment(
        unit=unit_id,
        month="Junio",
        year=2026,
        amount=40.0,
        reference="REF-ABONO-001",
        payment_date="2026-06-05"
    )

    aliquot = rec_res["aliquot"]
    assert aliquot["status"] == "Pendiente"  # Pending remaining balance
    assert aliquot["paid_amount"] == 40.0
    assert aliquot["amount"] == 30.0  # Remaining balance to pay

    # 2. Complete remaining balance ($30)
    rec_res_final = test_sm.reconcile_payment(
        unit=unit_id,
        month="Junio",
        year=2026,
        amount=30.0,
        reference="REF-COMPLETO-002",
        payment_date="2026-06-08"
    )

    aliquot_final = rec_res_final["aliquot"]
    assert aliquot_final["status"] == "Pagado"
    assert aliquot_final["paid_amount"] == 70.0
    assert aliquot_final["amount"] == 70.0

    # 3. Undo payment
    success = test_sm.undo_payment(aliquot_final["id"], "aliquot")
    assert success is True

    # Find the undone aliquot
    aliquot_undone = next((a for a in test_sm.data["aliquots"] if a["id"] == aliquot_final["id"]), None)
    assert aliquot_undone is not None
    assert aliquot_undone["status"] == "Pendiente"
    assert aliquot_undone["paid_amount"] == 0.0
    assert aliquot_undone["amount"] == 70.0

    # Cleanup
    test_sm.delete_unit(unit_id)
