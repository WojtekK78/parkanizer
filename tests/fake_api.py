"""In-memory fake of the Parkanizer/Tidaro API used by the tests.

Behaves like the post 2024-02-26 API: the marketplace always hands out the first
free spot of a day's pool until somebody takes it, and a resigned spot goes back
to the front of the pool (so it is offered again next time).
"""
import json
import re
from datetime import datetime

import requests
import responses

BASE = "https://share.parkanizer.com/api"


class FakeParkanizer:
    def __init__(self, days):
        # days: {date: {"pool": [spot names], "reserved": name or None}}
        self.days = {
            d: {"pool": list(v.get("pool", [])), "reserved": v.get("reserved")}
            for d, v in days.items()
        }
        self.calls = []
        self.polls = 0
        self.polls_since_resign = None
        # hook(fake) called on every get-spots, lets tests simulate other employees
        self.on_poll = None
        # list of status codes (or "conn" for network error) returned, one per request, before normal handling
        self.failures = []
        self.timeouts = []

    # -- simulated other employees -------------------------------------------------
    def someone_takes(self, date):
        day = self.days[date]
        if day["pool"]:
            return day["pool"].pop(0)

    def someone_releases(self, date, spot):
        self.days[date]["pool"].append(spot)

    # -- handlers --------------------------------------------------------------------
    def _fail(self, request=None):
        if request is not None:
            self.timeouts.append(request.req_kwargs.get("timeout"))
        if self.failures:
            code = self.failures.pop(0)
            if code == "conn":
                raise requests.ConnectionError("simulated network error")
            return (code, {}, json.dumps({"error": code}))

    def _get_spots(self, request):
        self.calls.append(("get-spots", None))
        failed = self._fail(request)
        if failed:
            return failed
        self.polls += 1
        if self.polls_since_resign is not None:
            self.polls_since_resign += 1
        if self.polls > 200:
            raise RuntimeError("runaway polling loop")
        if self.on_poll:
            self.on_poll(self)
        week = []
        for d in sorted(self.days):
            day = self.days[d]
            week.append(
                {
                    "day": datetime(d.year, d.month, d.day).isoformat(),
                    "reservedParkingSpotOrNull": (
                        {"name": day["reserved"]} if day["reserved"] else None
                    ),
                    "freeSpots": len(day["pool"]),
                }
            )
        return (200, {}, json.dumps({"weeks": [{"week": week}]}))

    def _take(self, request):
        body = json.loads(request.body)
        date = datetime.fromisoformat(body["dayToTake"]).date()
        self.calls.append(("take", date))
        failed = self._fail(request)
        if failed:
            return failed
        day = self.days[date]
        if day["reserved"]:
            spot = day["reserved"]
        elif day["pool"]:
            spot = day["pool"].pop(0)
            day["reserved"] = spot
        else:
            spot = None
        return (
            200,
            {},
            json.dumps({"receivedParkingSpotOrNull": {"name": spot} if spot else None}),
        )

    def _resign(self, request):
        body = json.loads(request.body)
        date = datetime.fromisoformat(body["daysToShare"][0]).date()
        self.calls.append(("resign", date))
        failed = self._fail(request)
        if failed:
            return failed
        day = self.days[date]
        self.polls_since_resign = 0
        if day["reserved"]:
            day["pool"].insert(0, day["reserved"])
            day["reserved"] = None
        return (200, {}, "{}")

    def _logout(self, request):
        self.calls.append(("logout", None))
        return (200, {}, "{}")

    def register(self, rsps):
        rsps.add_callback(responses.POST, BASE + "/marketplace/get-spots", self._get_spots)
        rsps.add_callback(
            responses.POST,
            BASE + "/employee-reservations/take-spot-from-marketplace",
            self._take,
        )
        rsps.add_callback(responses.POST, BASE + "/employee-reservations/resign", self._resign)
        rsps.add_callback(responses.POST, BASE + "/auth0/logout", self._logout)

    def count(self, name, date=None):
        return sum(1 for c in self.calls if c[0] == name and (date is None or c[1] == date))
