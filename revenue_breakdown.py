from sheets_manager import SheetsManager

sm = SheetsManager()

print("="*70)
print("LINE BY LINE BREAKDOWN OF SYSTEM TOTAL REVENUE ($1250.00)")
print("="*70)

rev_aliquots = 0.0
rev_charges = 0.0
rev_other = 0.0

print("\n--- ALIQUOTS IN REVENUE ---")
for a in sm.data["aliquots"]:
    if a.get("status") == "Exonerado" or str(a.get("reference", "")).startswith("EXONERADO"):
        continue
    paid = float(a.get("paid_amount", 0.0) or 0.0)
    st = a.get("status")
    fee = float(a.get("late_fee", 0.0) or 0.0)
    amt = float(a.get("amount", 0.0) or 0.0)
    
    val = 0.0
    if paid > 0.0:
        val = paid + (fee if st == "Pagado" else 0.0)
        rev_aliquots += val
        print(f"Aliquot {a['id']:<24} (Depto {a['unit']:<4}): ${val:6.2f} (from paid_amount={paid:.2f} + fee={fee:.2f}) [Status: {st}, Ref: {a.get('reference')}]")
    elif st == "Pagado":
        val = amt + fee
        rev_aliquots += val
        print(f"Aliquot {a['id']:<24} (Depto {a['unit']:<4}): ${val:6.2f} (from amt={amt:.2f} + fee={fee:.2f}) [Status: {st}, Ref: {a.get('reference')}]")

print(f"SUBTOTAL ALIQUOTS: ${rev_aliquots:.2f}")

print("\n--- CHARGES IN REVENUE ---")
for c in sm.data["additional_charges"]:
    if c.get("status") == "Exonerado" or str(c.get("reference", "")).startswith("EXONERADO"):
        continue
    paid = float(c.get("paid_amount", 0.0) or 0.0)
    st = c.get("status")
    amt = float(c.get("amount", 0.0) or 0.0)
    
    val = 0.0
    if paid > 0.0:
        val = paid
        rev_charges += val
        print(f"Charge {c['id']:<8} (Depto {c['unit']:<4} - {c['type']:<15}): ${val:6.2f} (from paid_amount={paid:.2f}) [Status: {st}, Ref: {c.get('reference')}]")
    elif st == "Pagado":
        val = amt
        rev_charges += val
        print(f"Charge {c['id']:<8} (Depto {c['unit']:<4} - {c['type']:<15}): ${val:6.2f} (from amt={amt:.2f}) [Status: {st}, Ref: {c.get('reference')}]")

print(f"SUBTOTAL CHARGES: ${rev_charges:.2f}")

print("\n--- SUMMARY COMPARISON ---")
total_system = rev_aliquots + rev_charges + rev_other
print(f"Total Aliquots:  ${rev_aliquots:.2f}")
print(f"Total Charges:   ${rev_charges:.2f}")
print(f"Total Other:     ${rev_other:.2f}")
print(f"TOTAL SYSTEM:    ${total_system:.2f}")
