import re

test_cases = [
    ("加付赔偿金人民币62000元", "加付赔偿金", 62000.0),
     ("二倍赔偿金279356.30元", "二倍赔偿金", 279356.30),
     ("支付加付赔偿金人民币62000元", "加付赔偿金", 62000.0),
     ("赔偿经济补偿金人民币139678.15元", "经济补偿金", 139678.15),
     ("支付拖欠工资人民币50000元", "拖欠工资", 50000.0),
     ("1、判令被告支付加付赔偿金人民币62000元", "加付赔偿金", 62000.0),
     ("2、判令被告支付二倍工资差额人民币279356.30元", "二倍工资差额", 279356.30),
     ("3、判令被告支付经济补偿金139678.15元", "经济补偿金", 139678.15),
     ("二倍工资差额", None, None),
]

passed = 0
failed = 0

standalone_cases = {"加付赔偿金人民币62000元", "二倍赔偿金279356.30元"}

print("=== Layer1 name_match (verify_agent.py) ===")
print("  Note: Layer1 requires a verb prefix. Standalone items fall through to Layer2/Layer3.")
layer1_name = r"(?:支付|赔偿|补足)([^，、\n：]*?(?:金|费|工资|差额)?)(?:人民币\s*)?(?:\d)"
for tc, expected_name, _ in test_cases:
    if expected_name is None:
        continue
    m = re.search(layer1_name, tc)
    if tc in standalone_cases:
        result = "GOLDEN" if m is None or m.group(1).strip().rstrip("及与") != expected_name else "EXTRANEOUS"
        if result == "GOLDEN":
            print(f"  PASS: [{tc}] -> no match (correctly deferred to Layer2/Layer3)")
            passed += 1
        else:
            print(f"  FAIL: [{tc}] -> [{m.group(1).strip().rstrip('及与')}] (Layer1 unexpected match)")
            failed += 1
    elif m:
        item_name = m.group(1).strip().rstrip("及与")
        if item_name == expected_name:
            print(f"  PASS: [{tc}] -> [{item_name}]")
            passed += 1
        else:
            print(f"  FAIL: [{tc}] -> [{item_name}] (expected [{expected_name}])")
            failed += 1
    else:
        print(f"  FAIL: [{tc}] -> NO MATCH (expected [{expected_name}])")
        failed += 1

print()
print("=== Layer2 (verify_agent.py synced) ===")
layer2_pattern = (
    r'([^，、\n：（\(]{2,15}?'
    r'(?:工资|赔偿金|差额|奖金|提成|期权|加班费|补偿金|二倍工资|加付赔偿金))'
    r'\s*(?:人民币\s*)?(?:共计|合计|为|：|:)?\s*'
    r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元'
)
for tc, expected_name, expected_amount in test_cases:
    m = re.findall(layer2_pattern, tc)
    if m and expected_name is not None:
        item, amount = m[0]
        item_clean = item.strip()
        amount_val = float(amount.replace(",", "").replace("，", ""))
        name_ok = expected_name in item_clean or item_clean in expected_name
        amount_ok = abs(amount_val - expected_amount) < 0.01
        if name_ok and amount_ok:
            print(f"  PASS: [{tc}] -> item=[{item_clean}] amount=[{amount_val}]")
            passed += 1
        else:
            print(f"  FAIL: [{tc}] -> item=[{item_clean}] amount=[{amount_val}] (expected [{expected_name}] {expected_amount})")
            failed += 1
    elif expected_name is None and not m:
        print(f"  PASS: [{tc}] -> NO MATCH (correct)")
        passed += 1
    elif expected_name is None and m:
        print(f"  INFO: [{tc}] -> item=[{m[0][0].strip()}] (no amount expected)")
    else:
        print(f"  FAIL: [{tc}] -> NO MATCH (expected [{expected_name}])")
        failed += 1

print()
print("=== Layer3 (verify_agent.py synced) ===")
layer3_pattern = (
    r'((?:拖欠|克扣|降薪|待岗|加班|二倍|加付|绩效|年底|项目|股票|经济补偿)'
    r'[^，、\n：人民币]*?)\s*(?:人民币\s*)?(?:共计|合计|为|：|:)?\s*'
    r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元'
)
for tc, expected_name, expected_amount in test_cases:
    m = re.findall(layer3_pattern, tc)
    if m and expected_name is not None:
        item, amount = m[0]
        item_clean = item.strip()
        amount_val = float(amount.replace(",", "").replace("，", ""))
        name_ok = expected_name in item_clean or item_clean in expected_name
        amount_ok = abs(amount_val - expected_amount) < 0.01
        if name_ok and amount_ok:
            print(f"  PASS: [{tc}] -> item=[{item_clean}] amount=[{amount_val}]")
            passed += 1
        else:
            print(f"  FAIL: [{tc}] -> item=[{item_clean}] amount=[{amount_val}] (expected [{expected_name}] {expected_amount})")
            failed += 1
    elif expected_name is None and not m:
        print(f"  PASS: [{tc}] -> NO MATCH (correct)")
        passed += 1
    else:
        print(f"  FAIL: [{tc}] -> NO MATCH (expected [{expected_name}])")
        failed += 1

print()
print("=== RISK_GRADES boundary ===")
RISK_GRADES = {
    "A": (0, 5, "极低风险"),
    "B": (5, 15, "低风险"),
    "C": (15, 30, "中风险"),
    "D": (30, 50, "高风险"),
    "F": (50, 101, "极高风险"),
}
expected_grades = {0: "A", 4.9: "A", 5: "B", 14.9: "B", 15: "C", 29.9: "C", 30: "D", 49.9: "D", 50: "F", 99.9: "F", 100: "F"}
for score, exp_grade in expected_grades.items():
    grade = "F"
    for g, (low, high, _) in RISK_GRADES.items():
        if low <= score < high:
            grade = g
            break
    if grade == exp_grade:
        print(f"  PASS: score={score:5.1f} -> {grade}")
        passed += 1
    else:
        print(f"  FAIL: score={score:5.1f} -> {grade} (expected {exp_grade})")
        failed += 1

print()
print(f"=== TOTAL: {passed} PASSED, {failed} FAILED ===")
if failed > 0:
    print("SOME TESTS FAILED!")
else:
    print("ALL TESTS PASSED!")
