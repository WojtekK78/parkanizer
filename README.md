# parkanizer/tidaro

Parkanizer/tidaro auto reservation and notification solution. Currently company is named tidaro previously was known as parkanizer.

You can setup your week days that you want your bookings and preferred spots list separated by commas (i.e. wider spots).

PLEASE REMEBER ABOUT RELEASING UNSUED PARKING SPOTS - Remember that after you have released spot via Parkanizer app/webpage it'll not be booked again automatically by script for the same particular calendar date. You need to book it manually if you will need that spot in the end for this calendar date.

There is lots of speling mistakes, let them be :)

- install dependencies sudo python -m pip install -r requirements.txt (note: selenium-wire needs blinker<1.8, it's pinned in requirements.txt)
- install chromium web driver and make sure it's accesible in PATH source -> <https://chromedriver.chromium.org/downloads>
- Setup confg in any .ini file i.e. "config.ini" based on provided template "config.ini.template" file
- Run as: "python parkanizer.py config.ini"
- if running headless on linux you can use following guides to setup Chromium wbedriver & to allow for it to work in Crontab (Display:0)
 	- <https://tecadmin.net/setup-selenium-chromedriver-on-ubuntu/>
 	- <https://newbedev.com/run-selenium-with-crontab-python>
Two final technical details to run headless in crontab-python
 1) I've had to run it as user's crontab not root (not: sudo crontab -e)
 2) I've changed chown of chromium directory to my username i.e. sudo chown myuser:mysuer /usr/bin/chromedriver

## How booking decisions are made

For every date returned by Parkanizer that is on one of your BookForWeekDay days:

- you already hold a Whitelisted spot -> nothing is done
- you hold nothing, but the script booked this date in the past -> nothing is done (you released it manually)
- you hold a non Whitelisted spot and 2 or less spots are free -> spot is kept
- you hold a non Whitelisted spot and more than 2 spots are free -> spot is released and search for Whitelisted spot starts
- you hold nothing -> spot is booked and, if it's not Whitelisted, search for Whitelisted spot starts (while more than 2 spots are free)

Search for Whitelisted spot: after releasing non Whitelisted spot script waits until free spots count for that date changes (somebody took the spot that is offered now), then books again. All dates are watched together in one loop, status is refreshed every pauseTime seconds.

Search stops after maxSearchTime seconds (config, default 1 hour, 0 = no limit), then any spot offered is taken so you don't end up without a spot.
Network errors are retried and expired login is renewed automatically, so long searches survive both.

Notifications: gmail_notify_enabled and pushover_notify_enabled are master switches. If a channel is disabled nothing is sent through it, whatever notify_* options say.

## Running tests

Tests use a fake Parkanizer API, no credentials or browser are needed.

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

## Scheduling

Sample crontab runnig at 00:05 daily
  
5 0 ** * DISPLAY=:0 cd /home/myuser/python/parkanizer/ && python3 parkanizer.py config.ini >> parkanizer.log 2>&1

Or newest alternative, after updating to Tidaro API's to situation when they don't reiterate through all open spots and you need to wait.
Setup systemd service i.e. as following

/etc/systemd/system/parkanizer.service

[Unit]
Description=Parkanizer
After=network.target
After=systemd-user-sessions.service
After=network-online.target

[Service]
WorkingDirectory=/DIRECTORY/TO/YOUR/PARKANIZER
ExecStart=python3 parkanizer.py YOUR_CONFIG.ini
Environment="DISPLAY=:0"
User=YOURUSER
Group=YOURGROUP
Restart=on-failure
TimeoutSec=30
RestartSec=120

[Install]
WantedBy=multi-user.target
