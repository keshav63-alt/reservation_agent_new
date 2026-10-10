# db/test_reservations.py
from datetime import datetime, timedelta


import pytest

from connection import supabase
from reservations import (
    book_reservation,
    cancel_reservation,
    check_availability,
    find_reservations_by_phone,
    get_reservation,
    log_call,
    reschedule_reservation,
    STEP_MIN,
)


RID = "test_r"
DAY = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
PHONE = "+910000000001"



print("STEP_MIN =", STEP_MIN)
print(check_availability("r1", DAY, "21:30", 2))
print(check_availability("r1", DAY, "20:30", 2))
print(check_availability("r1", DAY, "20:00", 2))

# ---------- fixtures ----------
def _wipe():
    supabase.table("calls").delete().eq("restaurant_id", RID).execute()
    supabase.table("reservations").delete().eq("restaurant_id", RID).execute()
    supabase.table("tables").delete().eq("restaurant_id", RID).execute()
    supabase.table("restaurants").delete().eq("restaurant_id", RID).execute()


@pytest.fixture(scope="module", autouse=True)
def test_restaurant():
    """Create an isolated restaurant: hours 12:00-23:00, 90 min slots,
    one 2-seater (A) and one 4-seater (B)."""
    _wipe()
    supabase.table("restaurants").insert({
        "restaurant_id": RID, "name": "Test Bistro", "phone": "+910000000000",
        "address": "Test", "opening_time": "12:00", "closing_time": "23:00",
        "slot_duration": 90,
    }).execute()
    supabase.table("tables").insert([
        {"table_id": "test_ta", "restaurant_id": RID, "table_name": "A", "capacity": 2},
        {"table_id": "test_tb", "restaurant_id": RID, "table_name": "B", "capacity": 4},
    ]).execute()
    yield
    _wipe()


@pytest.fixture(autouse=True)
def clean_reservations():
    """Every test starts with no reservations."""
    supabase.table("reservations").delete().eq("restaurant_id", RID).execute()
    yield


def book(time="20:00", party=2, phone=PHONE, day=DAY):
    return book_reservation(RID, "Tester", phone, day, time, party)


# ---------- check_availability ----------
def test_available_when_empty():
    r = check_availability(RID, DAY, "20:00", 2)
    assert r["ok"] and r["available"]


def test_unknown_restaurant():
    r = check_availability("nope", DAY, "20:00", 2)
    assert not r["ok"]


def test_rejects_time_not_on_half_hour():
    r = check_availability(RID, DAY, "20:10", 2)
    assert not r["ok"]


def test_rejects_before_opening():
    assert not check_availability(RID, DAY, "11:00", 2)["ok"]


def test_rejects_too_late_for_full_slot():
    # 22:00 + 90 min = 23:30, after closing
    assert not check_availability(RID, DAY, "22:00", 2)["ok"]


def test_accepts_last_valid_slot():
    r = check_availability(RID, DAY, "21:30", 2)
    assert r["available"], r



def test_rejects_past_date():
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert not check_availability(RID, yesterday, "20:00", 2)["ok"]


def test_rejects_bad_format():
    assert not check_availability(RID, "not-a-date", "20:00", 2)["ok"]


def test_party_larger_than_biggest_table():
    r = check_availability(RID, DAY, "20:00", 10)
    assert r["ok"] and not r["available"]


# ---------- book_reservation ----------
def test_book_success():
    r = book()
    assert r["ok"]
    assert len(r["reservation_id"]) == 6
    assert get_reservation(r["reservation_id"])["status"] == "confirmed"


def test_party_of_2_gets_smallest_table():
    assert book(party=2)["table"] == "A"


def test_party_of_4_gets_big_table():
    assert book(party=4)["table"] == "B"


def test_second_party_gets_next_table():
    assert book(party=2)["table"] == "A"
    assert book(party=2)["table"] == "B"


def test_full_slot_rejected_with_alternatives():
    book(party=2)
    book(party=2)
    r = book(party=2)
    assert not r["ok"]
    assert r["alternatives"]            # suggests other times


def test_overlapping_slot_is_blocked():
    book(party=2)
    book(party=2)
    # 21:00 overlaps the 20:00-21:30 bookings on both tables
    assert not book("21:00", 2)["ok"]


def test_back_to_back_slot_is_allowed():
    book(party=2)
    book(party=2)
    r = book("21:30", 2)
    assert r["ok"], r


def test_availability_reflects_booking():
    book(party=2)
    book(party=2)
    assert not check_availability(RID, DAY, "20:00", 2)["available"]


# ---------- cancel_reservation ----------
def test_cancel_frees_the_slot():
    a = book(party=2)
    book(party=2)
    assert not check_availability(RID, DAY, "20:00", 2)["available"]
    assert cancel_reservation(a["reservation_id"], "changed plans")["ok"]
    assert check_availability(RID, DAY, "20:00", 2)["available"]


def test_cancel_stores_status_and_reason():
    a = book()
    cancel_reservation(a["reservation_id"], "sick")
    row = get_reservation(a["reservation_id"])
    assert row["status"] == "cancelled"
    assert row["cancellation_reason"] == "sick"


def test_cancel_twice_fails():
    a = book()
    cancel_reservation(a["reservation_id"])
    assert not cancel_reservation(a["reservation_id"])["ok"]


def test_cancel_unknown_id():
    assert not cancel_reservation("000000")["ok"]


def test_can_rebook_after_cancel():
    a = book(party=4)
    cancel_reservation(a["reservation_id"])
    assert book(party=4)["ok"]


# ---------- reschedule_reservation ----------
def test_reschedule_to_free_slot():
    a = book("20:00")
    r = reschedule_reservation(a["reservation_id"], DAY, "19:00")
    assert r["ok"]
    row = get_reservation(a["reservation_id"])
    assert row["reservation_time"].startswith("19:00")


def test_reschedule_overlapping_own_slot():
    # 20:00 -> 20:30 overlaps its own old slot; must not block itself
    a = book("20:00")
    r = reschedule_reservation(a["reservation_id"], DAY, "20:30")
    assert r["ok"], r


def test_reschedule_to_full_slot_fails():
    book("20:00", 2)
    book("20:00", 2)
    c = book("18:00", 2)
    r = reschedule_reservation(c["reservation_id"], DAY, "20:00")
    assert not r["ok"]
    # original booking is unchanged
    assert get_reservation(c["reservation_id"])["reservation_time"].startswith("18:00")


def test_reschedule_change_party_size():
    a = book("20:00", 2)
    r = reschedule_reservation(a["reservation_id"], DAY, "20:00", new_party_size=4)
    assert r["ok"] and r["table"] == "B"


def test_reschedule_cancelled_reservation_fails():
    a = book()
    cancel_reservation(a["reservation_id"])
    assert not reschedule_reservation(a["reservation_id"], DAY, "19:00")["ok"]


def test_reschedule_unknown_id():
    assert not reschedule_reservation("000000", DAY, "19:00")["ok"]


# ---------- lookup / logging ----------
def test_find_by_phone_returns_only_confirmed():
    a = book("18:00", phone=PHONE)
    b = book("20:00", phone=PHONE)
    cancel_reservation(b["reservation_id"])
    ids = [r["reservation_id"] for r in find_reservations_by_phone(PHONE)]
    assert a["reservation_id"] in ids
    assert b["reservation_id"] not in ids


def test_log_call_inserts_row():
    now = datetime.now().isoformat()
    log_call("+910000000001", now, now, "completed", RID, summary="test call")
    rows = (supabase.table("calls").select("*")
            .eq("restaurant_id", RID).execute().data)
    assert len(rows) == 1 and rows[0]["summary"] == "test call"


# ---------- database safety net ----------
def test_unique_index_blocks_double_confirmed_slot():
    row = {
        "restaurant_id": RID, "table_id": "test_ta", "customer_name": "X",
        "customer_phone": PHONE, "reservation_date": DAY,
        "reservation_time": "20:00", "party_size": 2,
        "slot_key": f"test_ta_{DAY}_20:00",
    }
    supabase.table("reservations").insert({**row, "reservation_id": "900001"}).execute()
    with pytest.raises(Exception):
        supabase.table("reservations").insert({**row, "reservation_id": "900002"}).execute()

# ---------- input validation ----------
def _row_count():
    return len(supabase.table("reservations").select("reservation_id")
               .eq("restaurant_id", RID).execute().data)


@pytest.mark.parametrize("bad", [0, -1, -5])
def test_rejects_zero_or_negative_party_size(bad):
    assert not check_availability(RID, DAY, "20:00", bad)["ok"]
    r = book_reservation(RID, "Tester", PHONE, DAY, "20:00", bad)
    assert not r["ok"], r
    assert _row_count() == 0


@pytest.mark.parametrize("name", ["", "   "])
def test_rejects_empty_customer_name(name):
    r = book_reservation(RID, name, PHONE, DAY, "20:00", 2)
    assert not r["ok"], r
    assert _row_count() == 0


@pytest.mark.parametrize("phone", ["", "   "])
def test_rejects_empty_phone(phone):
    r = book_reservation(RID, "Tester", phone, DAY, "20:00", 2)
    assert not r["ok"], r
    assert _row_count() == 0


@pytest.mark.parametrize("bad_time", ["25:00", "20:60", "8pm", "abc", "20", ""])
def test_rejects_malformed_time(bad_time):
    assert not check_availability(RID, DAY, bad_time, 2)["ok"]
    r = book_reservation(RID, "Tester", PHONE, DAY, bad_time, 2)
    assert not r["ok"], r
    assert _row_count() == 0


@pytest.mark.parametrize("bad_date", ["2026-02-30", "2026-13-01", "2026-00-10", "31-12-2026"])
def test_rejects_invalid_calendar_date(bad_date):
    assert not check_availability(RID, bad_date, "20:00", 2)["ok"]
    r = book_reservation(RID, "Tester", PHONE, bad_date, "20:00", 2)
    assert not r["ok"], r
    assert _row_count() == 0


@pytest.mark.parametrize("bad", [0, -1])
def test_rejects_invalid_reschedule_party_size(bad):
    a = book("20:00", 2)
    r = reschedule_reservation(a["reservation_id"], DAY, "21:30", new_party_size=bad)
    assert not r["ok"], r
    # original booking is unchanged
    row = get_reservation(a["reservation_id"])
    assert row["status"] == "confirmed"
    assert row["party_size"] == 2
    assert row["reservation_time"].startswith("20:00")