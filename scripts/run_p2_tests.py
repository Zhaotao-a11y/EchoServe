"""Run P2 tests with full traceback"""
import sys
import traceback
sys.path.insert(0, '.')

# Load test_p2 module
import importlib.util
spec = importlib.util.spec_from_file_location("test_p2", "scripts/test_p2.py")
test_p2 = importlib.util.module_from_spec(spec)

# Don't run main
import io
old_stdout = sys.stdout
sys.stdout = io.StringIO()

try:
    spec.loader.exec_module(test_p2)
finally:
    sys.stdout = old_stdout

# Now run tests one by one with full traceback
tests = [
    ("T01", test_p2.test_whatsapp_import),
    ("T02", test_p2.test_whatsapp_signature),
    ("T03", test_p2.test_whatsapp_message_processing),
    ("T04", test_p2.test_whatsapp_rate_limit),
    ("T05", test_p2.test_enterprise_auth_import),
    ("T06", test_p2.test_ldap_fallback),
    ("T07", test_p2.test_oauth_authorize_url),
    ("T08", test_p2.test_dpo_preference_store),
    ("T09", test_p2.test_dpo_dataset_build),
    ("T10", test_p2.test_dpo_trainer),
    ("T11", test_p2.test_windows_installer_build),
    ("T12", test_p2.test_compliance_check),
    ("T13", test_p2.test_full_finetune_script),
    ("T14", test_p2.test_api_endpoints_registered),
]

for name, func in tests:
    try:
        result = func()
        if result is True or (isinstance(result, dict) and result.get("status") == "success"):
            print(f"  ✅ {name} PASS")
        else:
            detail = result.get("detail", "") if isinstance(result, dict) else str(result)
            print(f"  ⚠️ {name} PARTIAL: {detail}")
    except Exception as e:
        print(f"  ❌ {name} FAIL: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
