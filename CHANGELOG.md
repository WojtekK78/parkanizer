# Changelog

Each entry is one squashed commit on main, so every version can be checked out or reverted on its own (see README "Versions and going back to previous version").

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
