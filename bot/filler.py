# bot/filler.py
"""Short phrases spoken while a tool runs, so the caller never hears dead air."""

FILLERS = {
    "check_availability": [
        "Let me check that for you.",
        "One moment, let me see what's open.",
        "Sure, checking availability now.",
    ],
    "book_reservation": [
        "Booking that for you now.",
        "Give me just a second to confirm that.",
    ],
    "cancel_reservation": [
        "Okay, let me take care of that.",
        "One moment while I cancel that.",
    ],
    "reschedule_reservation": [
        "Let me move that for you.",
        "One moment, updating your booking.",
    ],
    "find_my_reservations": [
        "Let me pull up your bookings.",
        "One moment, looking that up.",
    ],
}

DEFAULT_FILLERS = ["One moment."]
