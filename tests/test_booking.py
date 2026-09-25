import shelve
import subprocess
import sys
import os

from unittest import mock

import pytest
import responses

import parkanizer
from fake_api import FakeParkanizer
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


def test_booking_decision(app):
    app.monkeypatch.setattr(parkanizer.cfg, "whitelist", ["1.007"])
    d = parkanizer.booking_decision
    assert d(MON, None, 10, False) is None
    assert d(MON, "2", 10, False) is None
    assert d(MON, "2", 10, True) is None
    assert d(MON, None, 10, True).startswith("spot was previously reserved")
    assert d(MON, "1.007", 10, False).startswith("Whitelisted")
    assert d(MON, "2", 2, False).startswith("non Whitelisted")
    assert d(FRI, None, 10, False) == "day not configured for booking"


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


def test_all_dates_searched_in_one_loop(app):
    # Monday's non whitelisted spot is taken by somebody only after a long time,
    # Tuesday's quickly. Tuesday must not wait for Monday.
    def hook(fake):
        if fake.polls_since_resign == 2:
            fake.someone_takes(TUE)
        if fake.polls_since_resign == 10:
            fake.someone_takes(MON)

    fake = app.run(
        {
            MON: {"pool": ["2", "1.007", "3", "4"]},
            TUE: {"pool": ["5", "9.999", "6", "7"]},
        },
        on_poll=hook,
    )
    assert fake.days[MON]["reserved"] == "1.007"
    assert fake.days[TUE]["reserved"] == "9.999"
    booked = [title for channel, title in app.notifications if channel == "gmail"]
    assert booked == ["Parkanizer Tue 10-06 spot = 9.999", "Parkanizer Mon 10-05 spot = 1.007"]
    # one status request per waiting loop for both dates (no separate polling per date)
    assert fake.polls < 16


def test_each_date_reported_once(app):
    fake = app.run(
        {
            MON: {"pool": ["1.007", "2", "3"]},
            TUE: {"pool": []},
            WED: {"pool": ["2", "3", "4"]},
        }
    )
    titles = sorted(t for c, t in app.notifications if c == "gmail")
    assert titles == [
        "Parkanizer Mon 10-05 spot = 1.007",
        "Parkanizer Problem Tue 10-06 no spots booked",
        "Parkanizer Wed 10-07 spot = 2",
    ]


def test_requests_have_timeout(app):
    fake = app.run({MON: {"pool": ["1.007", "2", "3"]}})
    assert fake.timeouts and all(t == parkanizer.REQUEST_TIMEOUT for t in fake.timeouts)


def test_server_errors_and_network_errors_are_retried(app):
    fake = app.run({MON: {"pool": ["1.007", "2", "3"]}}, failures=[503, "conn", 502])
    assert fake.days[MON]["reserved"] == "1.007"


def test_persistent_errors_stop_the_run(app):
    with pytest.raises(parkanizer.ParkanizerError, match="spot status"):
        app.run({MON: {"pool": ["1.007", "2", "3"]}}, failures=["conn"] * 10)


def test_client_error_is_not_retried(app):
    with pytest.raises(parkanizer.ParkanizerError):
        app.run({MON: {"pool": ["1.007", "2", "3"]}}, failures=[400, 400])
    # second failure not consumed -> the request was not repeated
    assert app.fake.failures == [400]


def test_expired_authorization_logs_in_again(app):
    fake = app.run({MON: {"pool": ["1.007", "2", "3"]}}, failures=[401])
    assert app.logins == 2
    assert fake.days[MON]["reserved"] == "1.007"


def test_search_time_limit_takes_offered_spot(app):
    app.configure(extra_booking="maxSearchTime = 1")
    clock = {"t": 0}
    app.monkeypatch.setattr(parkanizer.time, "monotonic", lambda: clock["t"])
    app.monkeypatch.setattr(parkanizer.time, "sleep", lambda s: clock.update(t=clock["t"] + 1))
    # nobody ever takes offered "2", so the free count never changes
    fake = app.run({MON: {"pool": ["2", "1.007", "3", "4"]}})
    assert fake.days[MON]["reserved"] == "2"
    assert ("gmail", "Parkanizer Mon 10-05 spot = 2") in app.notifications


def test_max_search_time_default(app):
    assert parkanizer.cfg.max_search_time == 3600


FULL_DAY = {"fromBookingTime": "P0DT00H00M", "toBookingTime": "P1DT00H00M"}


def test_zone_id_default_is_first_zone_and_json_payload(app):
    fake = app.run({MON: {"pool": ["1.007", "2", "3"]}})
    assert fake.count("zones") == 1
    assert {"parkingSpotZoneId": "zone-2", "bookingTimeInterval": FULL_DAY} in fake.bodies
    assert {
        "dayToTake": "2026-10-05",
        "parkingSpotZoneId": "zone-2",
        "parkingSpotIdOrNull": None,
        "bookingTimeInterval": FULL_DAY,
        "challengeTokenOrNull": None,
    } in fake.bodies


def test_zone_id_from_config(app):
    app.configure(extra_booking="parkingSpotZoneId = my-zone")
    fake = app.run({MON: {"pool": ["1.007", "2", "3"]}})
    assert fake.count("zones") == 0
    assert {"parkingSpotZoneId": "my-zone", "bookingTimeInterval": FULL_DAY} in fake.bodies


def test_booking_refused_status_ends_run_with_error(app):
    # i.e. Tidaro turned on reCAPTCHA for booking - must not be reported as "no free spots"
    fake = FakeParkanizer({MON: {"pool": ["1.007", "2", "3"]}})
    fake.take_status = "ChallengeTokenMissing"
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        fake.register(rsps)
        with pytest.raises(parkanizer.ParkanizerError, match="ChallengeTokenMissing"):
            parkanizer.parkanizer()
    assert app.notifications == []


def test_release_payload(app):
    fake = app.run({MON: {"pool": ["3", "4", "5"], "reserved": "2"}}, on_poll=take_after_release(MON, 3))
    assert {"daysToShare": ["2026-10-05"], "receivingEmployeeIdOrNull": None} in fake.bodies


def test_min_free_spots_from_config(app):
    app.configure(extra_booking="minFreeSpots = 5")
    # 5 free after taking "2" -> keep it, no search
    fake = app.run({MON: {"pool": ["2", "1.007", "3", "4", "5", "6"]}})
    assert fake.days[MON]["reserved"] == "2"
    assert fake.count("resign") == 0


def test_new_authorization_used_after_relogin(app):
    fake = app.run({MON: {"pool": ["1.007", "2", "3"]}}, failures=[401])
    assert fake.auth_headers[0] == "Bearer 1"
    assert set(fake.auth_headers[1:]) == {"Bearer 2"}
    assert parkanizer.http.cookies.get("c") == "1"


def test_reservation_storage_opened_once_per_run(app):
    opened = []
    real_open = shelve.open
    app.monkeypatch.setattr(
        parkanizer.shelve, "open", lambda *a, **k: opened.append(a) or real_open(*a, **k)
    )
    app.run(
        {
            MON: {"pool": ["1.007", "2", "3"]},
            TUE: {"pool": ["9.999", "2", "3"]},
            WED: {"pool": ["1.007", "2", "3"]},
        }
    )
    assert len(opened) == 1
    assert shelf_keys() == {MON.isoformat(), TUE.isoformat(), WED.isoformat()}


def test_config_error_is_reported(tmp_path):
    bad = tmp_path / "bad.ini"
    bad.write_text("[login]\n")
    with pytest.raises(parkanizer.ParkanizerError, match="config file"):
        parkanizer.read_config(str(bad))


def test_main_returns_error_and_closes_chrome_when_login_fails(app):
    browser = mock.MagicMock()
    app.monkeypatch.setattr(parkanizer, "start_driver", lambda: setattr(parkanizer, "driver", browser))

    def failing_login():
        with parkanizer.step("Error while initializing selenium and logging into parkanizer"):
            raise TimeoutError("page did not load")

    app.monkeypatch.setattr(parkanizer, "login", failing_login)
    assert parkanizer.main(["parkanizer.py", str(app.config_file)]) == 1
    browser.quit.assert_called_once()
    assert parkanizer.driver is None
    # one error email with the step and the cause (was two separate emails before)
    assert len(app.error_emails) == 1
    assert "logging into parkanizer: TimeoutError('page did not load')" in app.error_emails[0]


def test_main_success(app):
    browser = mock.MagicMock()
    app.monkeypatch.setattr(parkanizer, "start_driver", lambda: setattr(parkanizer, "driver", browser))
    fake = FakeParkanizer({MON: {"pool": ["1.007", "2", "3"]}})
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        fake.register(rsps)
        assert parkanizer.main(["parkanizer.py", str(app.config_file)]) == 0
    assert fake.days[MON]["reserved"] == "1.007"
    browser.quit.assert_called_once()
