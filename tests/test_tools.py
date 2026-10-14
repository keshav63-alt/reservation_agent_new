import asyncio
from bot.tools import check_availability, book_reservation, cancel_reservation


class FakeParams:
    function_name = "fake"

    async def result_callback(self, result, **kwargs):
        print("->", result)


async def main():
    p = FakeParams()
    day = "2030-01-15"
    await check_availability(p, day, "20:00", "2")           # "2" as a string
    await book_reservation(p, "Asha", "98765 43210", day, "20:00", 2)
    await cancel_reservation(p, "000000", "9876543210")       # wrong ID, expect failure


asyncio.run(main())