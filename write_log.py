import sys
import os
import traceback

log_path = "C:/Users/HP/.gemini/antigravity/scratch/condominium_manager/test_log.txt"

def log(msg):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")
    print(msg)

try:
    if os.path.exists(log_path):
        os.remove(log_path)
except Exception as e:
    pass

log("Script started.")
try:
    log("Importing SheetsManager...")
    from sheets_manager import SheetsManager
    log("Importing save_structured_receipt...")
    from app import save_structured_receipt
    log("Imports successful.")
    
    sm = SheetsManager()
    log("SheetsManager initialized.")
    
    # 1. Test Bulk Aliquots Emission
    log("Testing bulk aliquots emission...")
    # Clear any existing Julio 2026 aliquots for test reproducibility
    sm.data["aliquots"] = [a for a in sm.data["aliquots"] if not (a["month"] == "Julio" and a["year"] == 2026)]
    sm.sync()
    
    # Emit for Julio 2026
    count = sm.emit_aliquots_bulk("Julio", 2026)
    log(f"Emitted {count} aliquots.")
    
    # Assertions
    units_count = len(sm.data["units"])
    julio_aliquots = [a for a in sm.data["aliquots"] if a["month"] == "Julio" and a["year"] == 2026]
    log(f"Julio aliquots count in data: {len(julio_aliquots)}")
    assert len(julio_aliquots) == units_count, f"Expected {units_count} aliquots, got {len(julio_aliquots)}"
    for a in julio_aliquots:
        assert a["status"] == "Pendiente", f"Expected Pendiente status, got {a['status']}"
        assert a["comprobante_url"] == "", f"Expected empty comprobante_url, got '{a['comprobante_url']}'"
    log("[OK] Aliquots emission bulk validation passed.")
    
    # 2. Test save_structured_receipt
    log("Testing save_structured_receipt helper...")
    test_content = b"Comprobante de Pago Depto 101 de Jenny Portilla por $70.00 Ref DEP-12345"
    unit_id = "101"
    year = 2026
    month = "Julio"
    
    relative_url, absolute_path = save_structured_receipt(test_content, "comprobante_test.png", unit_id, year, month)
    log(f"File saved to relative URL: {relative_url}")
    log(f"Absolute path: {absolute_path}")
    
    # Assert file exists and contains correct bytes
    assert os.path.exists(absolute_path), "Structured receipt file does not exist on disk!"
    with open(absolute_path, "rb") as f:
        read_bytes = f.read()
    assert read_bytes == test_content, "File content mismatch!"
    
    # Assert correct directory structure
    expected_subdir = os.path.join("static", "uploads", unit_id, str(year), month)
    assert expected_subdir in absolute_path, f"Path does not match structured format. Got: {absolute_path}"
    log("[OK] save_structured_receipt file saving validation passed.")
    
    # Cleanup file
    if os.path.exists(absolute_path):
        os.remove(absolute_path)
    log("[SUCCESS] All structured uploads workflow tests passed successfully!")

except Exception as e:
    log("Exception occurred!")
    log(str(e))
    tb = traceback.format_exc()
    log(tb)
