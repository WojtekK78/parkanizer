# parkanizer/tidaro

Parkanizer/tidaro auto reservation and notification solution. Currently company is named tidaro previously was known as parkanizer.

You can setup your week days that you want your bookings and preferred spots list separated by commas (i.e. wider spots).

PLEASE REMEBER ABOUT RELEASING UNSUED PARKING SPOTS - Remember that after you have released spot via Parkanizer app/webpage it'll not be booked again automatically by script for the same particular calendar date. You need to book it manually if you will need that spot in the end for this calendar date.

There is lots of speling mistakes, let them be :)

## Installation

- Python 3.9+ and Chrome or Chromium are needed. Chrome runs headless (no screen / DISPLAY needed).
- install dependencies: `python3 -m pip install -r requirements.txt` (requests, notifiers, selenium 4.6+)
- chromedriver: Selenium 4.6+ downloads chromedriver matching your Chrome automatically on first run (cached in ~/.cache/selenium of the user running the script, so that user needs writable home and internet access). If that's not possible install chromedriver matching your Chrome version yourself and make sure it's accesible in PATH, source -> <https://googlechromelabs.github.io/chrome-for-testing/>. An old chromedriver left in PATH that doesn't match Chrome version breaks start ("SessionNotCreatedException") - remove or update it.
- running as root or in Docker: set chromeArguments = --no-sandbox in [other] section of config
- Setup confg in any .ini file i.e. "config.ini" based on provided template "config.ini.template" file
- Run as: `python3 parkanizer.py config.ini`
- First run: set `logLevel = INFO` to see login, parking zones avaliable to you and decisions for every date, switch back to WARNING later.

### Updating existing installation (from version before 2026-09)

Tidaro changed login page and API in 2026, older versions can't log in (stuck on 'https://share.parkanizer.com/' with title 'Tidaro') or fail with 400/403 errors. After `git pull`:

1. `python3 -m pip install -r requirements.txt`
2. optional cleanup, not used anymore: `python3 -m pip uninstall selenium-wire blinker` (pyOpenSSL only if nothing else on your system needs it)
3. config: remove `parkingSpotZoneId = fa44ef73-af90-48fb-b2f7-da513a25239e` if you have it - that zone answers 403 now. Without the option first zone avaliable to you is used; to use other one copy its id from the log line "Parking zones avaliable ..." (logLevel = INFO).
4. remove chromedriver you installed manually if it's older than your Chrome (see above), or keep it updated.

Nothing else in config changes, stored reservations in ./shelve are kept.

### Config options

See config.ini.template for all options with comments. Optional ones (default used when missing):

- [booking] maxSearchTime - seconds to search for Whitelisted spot, then any spot is taken (default 3600, 0 = no limit)
- [booking] parkingSpotZoneId - parking zone (level) to book in (default: first zone avaliable to you, all zones with ids are logged at INFO)
- [booking] minFreeSpots - search for Whitelisted spot only while more than this spots are free (default 2)
- [other] chromeArguments - extra Chrome arguments separated by comma, i.e. --no-sandbox

### Known limitation

Tidaro web app can protect booking with reCAPTCHA (per company setting, currently off). If it gets turned on script can't book anymore and every run ends with error "Parkanizer refused booking ... with status 'ChallengeTokenMissing'" (or 'ManualChallengeRequired') - book manually then.

## How booking decisions are made

For every date returned by Parkanizer that is on one of your BookForWeekDay days:

- you already hold a Whitelisted spot -> nothing is done
- you hold nothing, but the script booked this date in the past -> nothing is done (you released it manually)
- you hold a non Whitelisted spot and minFreeSpots (default 2) or less spots are free -> spot is kept
- you hold a non Whitelisted spot and more than minFreeSpots spots are free -> spot is released and search for Whitelisted spot starts
- you hold nothing -> spot is booked and, if it's not Whitelisted, search for Whitelisted spot starts (while more than minFreeSpots spots are free)

Search for Whitelisted spot: after releasing non Whitelisted spot script waits until free spots count for that date changes (somebody took the spot that is offered now), then books again. All dates are watched together in one loop, status is refreshed every pauseTime seconds.

Search stops after maxSearchTime seconds (config, default 1 hour, 0 = no limit), then any spot offered is taken so you don't end up without a spot.
Network errors are retried and expired login is renewed automatically, so long searches survive both.

Notifications: gmail_notify_enabled and pushover_notify_enabled are master switches. If a channel is disabled nothing is sent through it, whatever notify_* options say.

## Running tests

Tests use a fake Parkanizer API, no credentials are needed. Browser tests (tests/test_browser.py) start real Chrome against a local page and are skipped when Chrome can't be started.

    python -m pip install -r requirements.txt -r requirements-dev.txt
    python -m pytest

They also run on GitHub Actions for every pull request.

## Versions and going back to previous version

Every change is merged to main as one single (squashed) commit, listed in CHANGELOG.md.
See the list of versions (newest first):

    git fetch origin
    git log --oneline origin/main

Run a previous version (detached checkout, nothing is lost):

    git checkout <commit>        # e.g. git checkout aa81f92 = code before the refactoring series

Go back to latest:

    git checkout main && git pull

Undo one specific change on main while keeping the later ones: `git revert <commit>`.
Optionally you can name versions with tags, i.e. `git tag v1.0.0-original aa81f92 && git push origin --tags`.

Stored reservations in ./shelve stay compatible both ways: newer versions read what older ones wrote and the other way round.

## Scheduling / deployment

Run it from the directory with the code (reservations are stored in ./shelve relative to it). Use user account, not root (root needs --no-sandbox). Chrome is headless, no DISPLAY / X server needed.

Option 1 - crontab of your user (`crontab -e`, not sudo), i.e. daily at 00:05. With maxSearchTime run can last up to that long:

    5 0 * * * cd /home/myuser/python/parkanizer && /usr/bin/python3 parkanizer.py config.ini >> parkanizer.log 2>&1

Option 2 - systemd service, runs once at boot and restarts on error (use together with shutdownOnSuccess = True on machine dedicated for it, or with a timer).

/etc/systemd/system/parkanizer.service

    [Unit]
    Description=Parkanizer
    Wants=network-online.target
    After=network-online.target

    [Service]
    Type=simple
    WorkingDirectory=/DIRECTORY/TO/YOUR/PARKANIZER
    ExecStart=/usr/bin/python3 parkanizer.py YOUR_CONFIG.ini
    User=YOURUSER
    Group=YOURGROUP
    Restart=on-failure
    RestartSec=120

    [Install]
    WantedBy=multi-user.target

Enable and check it:

    sudo systemctl daemon-reload
    sudo systemctl enable --now parkanizer.service
    journalctl -u parkanizer.service -f

To run it every day instead of once per boot add /etc/systemd/system/parkanizer.timer and enable the timer (`sudo systemctl enable --now parkanizer.timer`) instead of the service:

    [Unit]
    Description=Run Parkanizer daily

    [Timer]
    OnCalendar=*-*-* 00:05
    Persistent=true

    [Install]
    WantedBy=timers.target

Exit code is 0 on success, 1 on error (error is logged and emailed when gmail is enabled).
