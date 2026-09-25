# Changelog

Each entry is one squashed commit on main, so every version can be checked out or reverted on its own (see README "Versions and going back to previous version").

## 9. README: installation, update and deployment
- Steps for updating existing installation after Tidaro 2026 changes (requirements, removing old parkingSpotZoneId, stale chromedriver).
- Optional config options and reCAPTCHA limitation described.
- Deployment: fixed crontab example, DISPLAY no longer needed (headless), systemd unit with absolute python path, network-online dependency, daily timer example.

## 8. Follow current Tidaro API
- get-spots and take-spot-from-marketplace send bookingTimeInterval (whole day) like the web app; get-spots failed with 400 "invalidProperties: bookingTimeInterval" without it.
- parkingSpotZoneId not set -> first zone avaliable to user is used (from get-parking-spot-zones), all zones with ids are logged at INFO. The previously hardcoded default zone answers 403 for current accounts.
- Booking answered with unexpected status (i.e. ChallengeTokenMissing / ManualChallengeRequired if Tidaro turns on reCAPTCHA for booking) ends run with error naming the status, instead of being reported as "no free spots".
- Logout uses GET like the web app (POST answered 403 and logged a warning, emailed when gmail is on, on every run).

## 7. Login works again with current Tidaro web app
- Web app now loads ~11MB of scripts before redirecting to login page. Through selenium-wire's proxy they took ~25s and the app gave up (requirejs "Load timeout for modules: main"), so login never started.
- selenium-wire removed: Authorization header is read from Chrome's own network log (performance log), Chrome connects directly. blinker/pyOpenSSL pins and their install problems are gone with it.
- Login waits up to 30s per step (was 10s), the login page alone takes ~10s to appear on a slower connection.

## 6. Code structure cleanup
- Configuration kept in one Config object instead of ~20 global variables (config file format unchanged).
- Helper functions raise ParkanizerError describing the failed step instead of calling sys.exit; main() handles errors in one place and returns exit code. One error log line/email per failure (was two).
- Login timeout error says at which page/URL login got stuck.
- Reservation storage opened once per run (was opened for every date and every booking).
- Log messages use logging's %s formatting.
- initialize_logger can be called again without duplicating log lines.

## 5. Lighter and more reliable login
- pyOpenSSL<24.3 pinned: newer versions removed API used by selenium-wire for HTTPS interception, so login token capture failed on fresh installs.
- selenium-wire captures only Parkanizer API requests (driver.scopes) and keeps them in memory, other traffic passes through untouched.
- Authorization is taken as soon as the web app makes an authorized get-employee-context request (waits up to 30s) instead of scanning all requests afterwards; failed with unclear NameError before when the request was missing.
- Images are not loaded and pages load "eager" - faster login.
- New optional config option [other] chromeArguments - extra Chrome arguments separated by comma, i.e. --no-sandbox when running as root/in Docker.
- Integration tests with real Chrome against local page (skipped when Chrome is not available).

## 4. Lighter HTTP calls and configurable zone
- One requests.Session for all API calls (connection kept open between polls, auth set once).
- Request bodies built as JSON by requests instead of string concatenation.
- New optional config options in [booking]: parkingSpotZoneId (default: previously hardcoded zone) and minFreeSpots (default 2 - search for Whitelisted spot only while more spots than this are free).

## 3. Robustness
- All API requests have 30s timeout. Network errors and 5xx responses are retried 3 times (2s, 4s, 8s pauses) instead of stopping the run.
- When authorization expires (401, i.e. during long search) script logs in again in fresh browser and repeats the request.
- New optional config option [booking] maxSearchTime (seconds, default 3600, 0 = no limit). When reached, script takes any spot offered for dates still searching, so you don't end up without a spot after releasing one.
- Chrome is always closed, also when run ends with error (it was left running before).
- Failed logout no longer marks the run as failed.

## 2. All dates searched in one loop
- Previously dates were handled one after another and waiting for a better spot on one date (possibly hours) blocked booking of all later dates.
- Now all dates are booked first, then one loop watches all dates still searching, with one status request per pauseTime, and retries only dates whose free spots count changed.
- Each date is reported (notification) as soon as it's finished.
- "Time spend searching" in logs is real elapsed time now.

## 1. Correctness fixes
- A non Whitelisted spot booked by the script on an earlier run is no longer released without re-booking (it was lost when the date was in the reservation storage).
- No "release" call for dates where nothing is reserved.
- No second booking when holding a non Whitelisted spot and only 2 or less spots are free - the spot is kept.
- Free spots count is refreshed after booking. Before, with exactly 3 free spots, the script took one (leaving 2), still released it using outdated count and waited for a change indefinitely.
- Reservation storage key now includes year (ISO date). Old keys are still read; values are kept in old format so older versions still work after rollback.
- gmail_notify_enabled / pushover_notify_enabled are respected for booking notifications and reminders.
- Missing "today" in Parkanizer response no longer stops the script.
- Script exits with error when no ".ini" file is given.
- blinker<1.8 pinned - fresh installs of selenium-wire were failing on import.
- Removed no-op gmail defaults code in parkanizer_notifiers.py.
- Tests with fake Parkanizer API + GitHub Actions workflow.

## 0. Original
Code before the refactoring series: commit aa81f92.
