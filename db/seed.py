# db/seed.py
from connection import supabase

RESTAURANT = {
    "restaurant_id": "r1",
    "name": "Demo Bistro",
    "phone": "+911234567890",
    "address": "Hyderabad",
    "opening_time": "12:00",
    "closing_time": "23:00",
    "slot_duration": 90,
}

TABLES = [
    {"table_id": "t1", "restaurant_id": "r1", "table_name": "T1", "capacity": 2},
    {"table_id": "t2", "restaurant_id": "r1", "table_name": "T2", "capacity": 2},
    {"table_id": "t3", "restaurant_id": "r1", "table_name": "T3", "capacity": 4},
    {"table_id": "t4", "restaurant_id": "r1", "table_name": "T4", "capacity": 6},
]


def seed():
    supabase.table("restaurants").upsert(RESTAURANT, on_conflict="restaurant_id").execute()
    supabase.table("tables").upsert(TABLES, on_conflict="table_id").execute()

    restaurants = supabase.table("restaurants").select("*").eq("restaurant_id", "r1").execute().data
    tables = supabase.table("tables").select("*").eq("restaurant_id", "r1").order("table_id").execute().data

    print("Restaurant:", restaurants)
    print(f"Tables ({len(tables)}):")
    for t in tables:
        print("  ", t["table_id"], t["table_name"], "capacity", t["capacity"])


if __name__ == "__main__":
    seed()