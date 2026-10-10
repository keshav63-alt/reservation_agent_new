# check_key.py
import os, base64, json
from dotenv import load_dotenv

load_dotenv(override=True)
key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()

if key.startswith("sb_secret_"):
    print("OK: new-style secret key (bypasses RLS)")
elif key.startswith("sb_publishable_"):
    print("WRONG: this is a publishable key (subject to RLS)")
elif key.count(".") == 2:
    payload = key.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    print("JWT role:", json.loads(base64.urlsafe_b64decode(payload)).get("role"))
else:
    print("Unrecognised key format")