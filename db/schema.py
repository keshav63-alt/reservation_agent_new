from connection import supabase

res = supabase.table("restaurants").select("*").limit(5).execute()
print(res.data)