# db/check_r1.py
from connection import supabase
print(supabase.table("restaurants").select("restaurant_id,name").execute().data)