import pytest
from datetime import datetime


def test_late_fee_calculation_rules(test_sm):
    month = "Abril"
    year = 2026
    late_day = int(test_sm.config.get("late_fee_day", 15))
    fee_amount = float(test_sm.config.get("late_fee_amount", 10.0))

    # 1. Paid on or before late_fee_day: No fine (0.0)
    assert test_sm.check_late_fee(f"2026-04-{late_day:02d}", month, year) == 0.0
    assert test_sm.check_late_fee("2026-04-01", month, year) == 0.0

    # 2. Paid after late_fee_day in same month: Fine applied
    next_day = min(late_day + 1, 28)
    assert test_sm.check_late_fee(f"2026-04-{next_day:02d}", month, year) == fee_amount
    assert test_sm.check_late_fee("2026-04-30", month, year) == fee_amount

    # 3. Paid in subsequent month: Fine applied
    assert test_sm.check_late_fee("2026-05-02", month, year) == fee_amount


def test_individual_and_bulk_aliquot_emission(test_sm):
    unit_id = "TEST-PYTEST-UNIT"
    test_sm.add_or_update_unit(
        unit_id=unit_id,
        owner="Test Owner Pytest",
        phone1="0999999999",
        email1="test@pytest.com",
        aliquot_base=75.0
    )

    # 1. Emit individual aliquot
    aliquot = test_sm.emit_aliquot_individual(unit=unit_id, month="Mayo", year=2026, amount=75.0)
    assert aliquot is not None
    assert aliquot["unit"] == unit_id
    assert aliquot["month"] == "Mayo"
    assert aliquot["year"] == 2026
    assert aliquot["amount"] == 75.0
    assert aliquot["status"] == "Pendiente"

    # 2. Incremental emission should not duplicate
    count_first = test_sm.emit_aliquots_bulk(month="Mayo", year=2026)
    count_second = test_sm.emit_aliquots_bulk(month="Mayo", year=2026)
    assert count_second == 0, "Second bulk emission should not duplicate existing records"

    # Cleanup
    test_sm.delete_unit(unit_id)
