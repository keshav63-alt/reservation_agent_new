# db/connection.py
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv(override=True)

url = os.getenv("SUPABASE_DATA_URL", "").strip().rstrip("/")
key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()

if not url or not key:
    raise RuntimeError("Missing SUPABASE_DATA_URL or SUPABASE_SERVICE_KEY in .env")

supabase: Client = create_client(url, key)


if __name__ == "__main__":
    print("URL being used:", url)   # safe to print; never print the key
    print(supabase.table("restaurants").select("*").limit(5).execute().data)