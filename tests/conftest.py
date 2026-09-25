import logging
import os
import sys
from datetime import date
from unittest import mock

import pytest
import responses
from notifiers.logging import NotificationHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parkanizer  # noqa: E402
from fake_api import FakeParkanizer  # noqa: E402

MON = date(2026, 10, 5)
TUE = date(2026, 10, 6)
WED = date(2026, 10, 7)
THU = date(2026, 10, 8)
FRI = date(2026, 10, 9)

CONFIG = """
[login]
parkanizer_user = john.doe@example.com
parkanizer_pass = secret

[notifications]
notify_reminder_gmail = True
notify_reminder_pushover = True
notify_booking_outcome_gmail = True
notify_booking_outcome_pushover = True

[pushover]
pushover_notify_enabled = {pushover_enabled}
pushover_token = t
pushover_user = u
pushover_device = d

[gmail]
gmail_notify_enabled = {gmail_enabled}
gmail_user = g@example.com
gmail_password = p
gmail_to = to@example.com

[booking]
Whitelist = 1.007,9.999
BookForWeekDay = 1,2,3,4
pauseTime = 0
{extra_booking}

[other]
logLevel = DEBUG
shutdownOnSuccess = False
"""


def write_config(path, pushover_enabled=True, gmail_enabled=True, extra_booking=""):
    path.write_text(
        CONFIG.format(
            pushover_enabled=pushover_enabled,
            gmail_enabled=gmail_enabled,
            extra_booking=extra_booking,
        )
    )
    return path


class App:
    """Configured parkanizer module wired to a fake API."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.notifications = []
        self.logins = 0
        self.error_emails = []
        # logger warnings/errors are emailed via NotificationHandler, capture instead of sending
        monkeypatch.setattr(
            NotificationHandler, "emit", lambda h, record: self.error_emails.append(h.format(record))
        )
        monkeypatch.chdir(tmp_path)
        (tmp_path / "shelve").mkdir()

    def configure(self, **kwargs):
        self.config_file = write_config(self.tmp_path / "config.ini", **kwargs)
        self.monkeypatch.setattr(parkanizer, "cfg", parkanizer.read_config(str(self.config_file)))
        logging.getLogger(parkanizer.__name__).handlers.clear()
        self.monkeypatch.setattr(parkanizer, "logger", parkanizer.logger)
        parkanizer.initialize_logger()
        self.monkeypatch.setattr(parkanizer, "driver", mock.MagicMock(), raising=False)
        def fake_login():
            self.logins += 1
            return {"Authorization": "Bearer " + str(self.logins)}, {"c": "1"}

        self.monkeypatch.setattr(parkanizer, "login", fake_login)
        self.monkeypatch.setattr(
            parkanizer, "start_driver", lambda: setattr(parkanizer, "driver", mock.MagicMock())
        )
        self.monkeypatch.setattr(parkanizer, "RETRY_BACKOFF", 0)
        self.monkeypatch.setattr(
            parkanizer,
            "pushover_notify",
            lambda *a, **k: self.notifications.append(("pushover", a[1])),
        )
        self.monkeypatch.setattr(
            parkanizer,
            "gmail_notify",
            lambda **k: self.notifications.append(("gmail", k["title"])),
        )
        return self

    def run(self, days, on_poll=None, failures=None):
        fake = self.fake = FakeParkanizer(days)
        fake.on_poll = on_poll
        fake.failures = list(failures or [])
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            fake.register(rsps)
            parkanizer.parkanizer()
        return fake


@pytest.fixture
def app(tmp_path, monkeypatch):
    return App(tmp_path, monkeypatch).configure()
