import os

js_path = "static/app.js"
keywords = ["emitAliquots", "uploadReceiptForAliquot", "triggerReceiptUpload", "handleAliquotFileChange", "renderAliquotsTable"]

with open(js_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    for kw in keywords:
        if kw in line:
            print(f"{i+1}: {line.strip()}")
            break
