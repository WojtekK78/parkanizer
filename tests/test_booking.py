import shelve
import subprocess
import sys
import os

import parkanizer
from conftest import MON, TUE, WED, THU, FRI

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def shelf_keys():
    with shelve.open("./shelve/reservations_johndoe.db") as db:
        return set(db.keys())


def take_after_release(date, times=1):
    """Other employee takes the offered spot of `date` a moment after we released it."""
    state = {"left": times}

    def hook(fake):
        if state["left"] and fake.polls_since_resign == 2:
            state["left"] -= 1
            fake.someone_takes(date)

    return hook


def test_books_whitelisted_spot_straight_away(app):
    fake = app.run({MON: {"pool": ["1.007", "2", "3", "4"]}})
    assert fake.days[MON]["reserved"] == "1.007"
    assert fake.count("resign") == 0
    assert shelf_keys() == {MON.isoformat()}
    assert ("gmail", "Parkanizer Mon 10-05 spot = 1.007") in app.notifications
    assert ("pushover", "Parkanizer Mon 10-05 spot = 1.007") in app.notifications


def test_day_not_configured_is_skipped(app):
    fake = app.run({FRI: {"pool": ["1.007", "2", "3"]}})
    assert fake.count("take") == 0
    assert fake.days[FRI]["reserved"] is None


def test_whitelisted_reservation_is_kept(app):
    fake = app.run({MON: {"pool": ["2", "3", "4"], "reserved": "9.999"}})
    assert fake.count("take") == 0
    assert fake.count("resign") == 0


def test_searches_until_whitelisted_spot(app):
    # "2" is offered until somebody else takes it, then "1.007" becomes available
    fake = app.run(
        {MON: {"pool": ["2", "1.007", "3", "4", "5"]}},
        on_poll=take_after_release(MON),
    )
    assert fake.days[MON]["reserved"] == "1.007"
    assert fake.count("take", MON) == 2
    assert fake.count("resign", MON) == 1


def test_stops_searching_when_only_two_spots_left(app):
    fake = app.run({MON: {"pool": ["2", "3", "4"]}})
    # after taking "2" only 2 are free, so it is kept
    assert fake.days[MON]["reserved"] == "2"
    assert fake.count("resign") == 0


def test_no_free_spots_sends_problem_notification(app):
    fake = app.run({MON: {"pool": []}})
    assert fake.days[MON]["reserved"] is None
    assert ("gmail", "Parkanizer Problem Mon 10-05 no spots booked") in app.notifications
    assert any("Problem with booking" in e for e in app.error_emails)
    assert shelf_keys() == set()


def test_empty_day_is_not_released_before_booking(app):
    fake = app.run({MON: {"pool": ["1.007", "2", "3"]}})
    assert fake.calls.index(("take", MON)) >= 0
    assert fake.count("resign") == 0


def test_manually_released_day_is_not_rebooked(app):
    with shelve.open("./shelve/reservations_johndoe.db") as db:
        db[MON.isoformat()] = MON.isoformat()
    fake = app.run({MON: {"pool": ["1.007", "2", "3"]}})
    assert fake.count("take") == 0


def test_stored_reservation_readable_by_older_versions(app):
    app.run({MON: {"pool": ["1.007", "2", "3"]}})
    with shelve.open("./shelve/reservations_johndoe.db") as db:
        # older versions check: date.strftime("%A %B %d") in list(values())
        assert MON.strftime("%A %B %d") in list(db.values())


def test_legacy_shelve_key_is_honoured(app):
    legacy = TUE.strftime("%A %B %d")
    with shelve.open("./shelve/reservations_johndoe.db") as db:
        db[legacy] = legacy
    fake = app.run({TUE: {"pool": ["1.007", "2", "3"]}})
    assert fake.count("take") == 0


def test_previous_non_whitelisted_booking_is_not_lost(app):
    # Script booked "2" earlier (stored in shelve). More spots freed up since then.
    # Old versions released "2" and then skipped booking as the day was "already reserved".
    with shelve.open("./shelve/reservations_johndoe.db") as db:
        db[WED.isoformat()] = WED.isoformat()
    fake = app.run(
        {WED: {"pool": ["3", "1.007", "4", "5"], "reserved": "2"}},
        on_poll=take_after_release(WED, times=2),
    )
    assert fake.days[WED]["reserved"] == "1.007"


def test_non_whitelisted_spot_kept_when_few_free(app):
    fake = app.run({THU: {"pool": ["3", "4"], "reserved": "2"}})
    assert fake.count("resign") == 0
    assert fake.count("take") == 0
    assert fake.days[THU]["reserved"] == "2"


def test_disabled_channels_are_not_used(app):
    app.configure(pushover_enabled=False, gmail_enabled=False)
    app.run({MON: {"pool": ["1.007", "2", "3"]}})
    assert app.notifications == []


def test_only_pushover_disabled(app):
    app.configure(pushover_enabled=False)
    app.run({MON: {"pool": ["1.007", "2", "3"]}})
    assert {n[0] for n in app.notifications} == {"gmail"}


def test_missing_today_in_status_does_not_crash(app):
    # none of the dates is today
    fake = app.run({MON: {"pool": ["1.007", "2", "3"]}})
    assert fake.count("logout") == 1


def test_booking_decision():
    parkanizer.BookForWeekDay = [1, 2, 3, 4]
    parkanizer.Whitelist = ["1.007"]
    d = parkanizer.booking_decision
    assert d(MON, "None", 10, False) is None
    assert d(MON, "2", 10, False) is None
    assert d(MON, "2", 10, True) is None
    assert d(MON, "None", 10, True).startswith("spot was previously reserved")
    assert d(MON, "1.007", 10, False).startswith("Whitelisted")
    assert d(MON, "2", 2, False).startswith("non Whitelisted")
    assert d(FRI, "None", 10, False) == "day not configured for booking"


def run_main(*args):
    return subprocess.run(
        [sys.executable, "parkanizer.py", *args], cwd=REPO, capture_output=True, text=True
    )


def test_main_without_config_exits_with_error():
    result = run_main()
    assert result.returncode == 1
    assert "Please provide" in result.stdout


def test_main_with_non_ini_file_exits_with_error():
    result = run_main("config.txt")
    assert result.returncode == 1
    assert "Please provide" in result.stdout
