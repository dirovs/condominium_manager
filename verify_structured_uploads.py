import os
import json
import time
import shutil
from sheets_manager import SheetsManager
from app import save_structured_receipt

def test_verify_workflow():
    print("Initializing test...")
    sm = SheetsManager()
    
    # 1. Test Bulk Aliquots Emission
    print("Testing bulk aliquots emission...")
    # Clear any existing Julio 2026 aliquots for test reproducibility
    sm.data["aliquots"] = [a for a in sm.data["aliquots"] if not (a["month"] == "Julio" and a["year"] == 2026)]
    sm.sync()
    
    # Emit for Julio 2026
    count = sm.emit_aliquots_bulk("Julio", 2026)
    print(f"Emitted {count} aliquots.")
    
    # Assertions
    units_count = len(sm.data["units"])
    julio_aliquots = [a for a in sm.data["aliquots"] if a["month"] == "Julio" and a["year"] == 2026]
    assert len(julio_aliquots) == units_count, f"Expected {units_count} aliquots, got {len(julio_aliquots)}"
    for a in julio_aliquots:
        assert a["status"] == "Pendiente", f"Expected Pendiente status, got {a['status']}"
        assert a["comprobante_url"] == "", f"Expected empty comprobante_url, got '{a['comprobante_url']}'"
    print("[OK] Aliquots emission bulk validation passed.")
    
    # 2. Test save_structured_receipt
    print("Testing save_structured_receipt helper...")
    test_content = b"Comprobante de Pago Depto 101 de Jenny Portilla por $70.00 Ref DEP-12345"
    unit_id = "101"
    year = 2026
    month = "Julio"
    
    relative_url, absolute_path = save_structured_receipt(test_content, "comprobante_test.png", unit_id, year, month)
    print(f"File saved to relative URL: {relative_url}")
    print(f"Absolute path: {absolute_path}")
    
    # Assert file exists and contains correct bytes
    assert os.path.exists(absolute_path), "Structured receipt file does not exist on disk!"
    with open(absolute_path, "rb") as f:
        read_bytes = f.read()
    assert read_bytes == test_content, "File content mismatch!"
    
    # Assert correct directory structure
    expected_subdir = os.path.join("static", "uploads", unit_id, str(year), month)
    assert expected_subdir in absolute_path, f"Path does not match structured format. Got: {absolute_path}"
    print("[OK] save_structured_receipt file saving validation passed.")
    
    # Cleanup file
    if os.path.exists(absolute_path):
        os.remove(absolute_path)
        
    print("\n[SUCCESS] All structured uploads workflow tests passed successfully!")

if __name__ == "__main__":
    test_verify_workflow()
