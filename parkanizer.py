"""Parkanizer/Tidaro parking spot auto reservation.

Logs in with headless Chrome (Authorization header the web app uses is read from Chrome's network log),
then talks to Parkanizer API directly: books spots for configured week days, searches for
Whitelisted spots and sends notifications. Run as: python parkanizer.py config.ini
"""
from selenium import webdriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from parkanizer_notifiers import pushover_notify
from parkanizer_notifiers import gmail_notify
import json
import re
import requests
import configparser
import sys
import logging
import shelve
import time
import os
from notifiers.logging import NotificationHandler

API_URL = "https://share.parkanizer.com/api/"
# Request made by web app after login, it carries Authorization header we need
EMPLOYEE_CONTEXT_REQUEST = r"/api/get-employee-context"
# Whole day booking, as sent by web app (ISO 8601 durations from midnight)
FULL_DAY = {"fromBookingTime": "P0DT00H00M", "toBookingTime": "P1DT00H00M"}
# take-spot-from-marketplace statuses that are normal outcomes, any other one (i.e. ChallengeTokenMissing,
# ManualChallengeRequired when Tidaro turns on reCAPTCHA for booking) means script can't book
TAKE_SPOT_OK_STATUSES = (None, "Reserved", "NoSpotsFound")

# Seconds to wait for Parkanizer to answer a single request
REQUEST_TIMEOUT = 30
# Number of tries for a request failing with network error or 5xx, waits RETRY_BACKOFF, 2x, 4x... seconds between tries
REQUEST_TRIES = 4
RETRY_BACKOFF = 2


class ParkanizerError(Exception):
    """Error that ends the run, message says which step failed."""


class SessionExpired(Exception):
    pass


@contextmanager
def step(description):
    # Turns any error into ParkanizerError saying what we were doing
    try:
        yield
    except ParkanizerError:
        raise
    except Exception as error:
        raise ParkanizerError(description + ": " + repr(error)) from error


@dataclass
class Config:
    user: str
    password: str
    notify_reminder_gmail: bool
    notify_reminder_pushover: bool
    notify_booking_outcome_gmail: bool
    notify_booking_outcome_pushover: bool
    pushover_enabled: bool
    pushover_token: str
    pushover_user: str
    pushover_device: str
    gmail_enabled: bool
    gmail_user: str
    gmail_password: str
    gmail_to: str
    whitelist: list
    book_for_weekdays: list
    pause_time: int
    max_search_time: int
    # None = first zone offered by Parkanizer, resolved after login
    zone_id: str
    min_free_spots: int
    log_level: str
    chrome_arguments: list
    shutdown_on_success: bool

    @property
    def user_id(self):
        return self.user.partition("@")[0].replace(".", "")

    @property
    def reservations_file(self):
        return "./shelve/reservations_" + self.user_id + ".db"


def read_config(path):
    try:
        config = configparser.ConfigParser()
        if not config.read(path):
            raise FileNotFoundError(path)
        booking = config["booking"]
        other = config["other"]
        return Config(
            user=config["login"]["parkanizer_user"],
            password=config["login"]["parkanizer_pass"],
            notify_reminder_gmail=config["notifications"].getboolean("notify_reminder_gmail"),
            notify_reminder_pushover=config["notifications"].getboolean("notify_reminder_pushover"),
            notify_booking_outcome_gmail=config["notifications"].getboolean("notify_booking_outcome_gmail"),
            notify_booking_outcome_pushover=config["notifications"].getboolean("notify_booking_outcome_pushover"),
            pushover_enabled=config["pushover"].getboolean("pushover_notify_enabled"),
            pushover_token=config["pushover"]["pushover_token"],
            pushover_user=config["pushover"]["pushover_user"],
            pushover_device=config["pushover"]["pushover_device"],
            gmail_enabled=config["gmail"].getboolean("gmail_notify_enabled"),
            gmail_user=config["gmail"]["gmail_user"],
            gmail_password=config["gmail"]["gmail_password"],
            gmail_to=config["gmail"]["gmail_to"],
            whitelist=booking["Whitelist"].split(","),
            book_for_weekdays=[int(day) for day in booking["BookForWeekDay"].split(",")],
            pause_time=int(booking["pauseTime"]),
            # 0 = search without time limit
            max_search_time=booking.getint("maxSearchTime", fallback=3600),
            zone_id=booking.get("parkingSpotZoneId", fallback="").strip() or None,
            min_free_spots=booking.getint("minFreeSpots", fallback=2),
            log_level=other["logLevel"],
            # extra Chrome command line arguments, i.e. --no-sandbox when running as root/in Docker
            chrome_arguments=[
                argument.strip()
                for argument in other.get("chromeArguments", fallback="").split(",")
                if argument.strip()
            ],
            shutdown_on_success=other.getboolean("shutdownOnSuccess"),
        )
    except Exception as error:
        raise ParkanizerError("Problems with initalization of config file: " + repr(error)) from error


# Set by main() / tests
cfg = None
logger = logging.LoggerAdapter(logging.getLogger(__name__), {"user": "-"})
driver = None

# One HTTP session for all API calls - keeps connection open between requests.
# Holds Authorization header and cookies taken from browser after login, refreshed by relogin()
http = requests.Session()


def initialize_logger():
    global logger
    base_logger = logging.getLogger(__name__)
    base_logger.setLevel(cfg.log_level)
    # safe to call again - replaces handlers instead of duplicating every message
    for handler in list(base_logger.handlers):
        base_logger.removeHandler(handler)

    log_format = logging.Formatter(
        "%(levelname)s - %(asctime)s - %(user)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    base_logger.addHandler(console_handler)

    # warnings and errors are emailed, only if gmail allowed in config
    if cfg.gmail_enabled:
        email_handler = NotificationHandler(
            "gmail",
            defaults={
                "subject": "Parkanizer ERROR",
                "to": cfg.gmail_to,
                "username": cfg.gmail_user,
                "password": cfg.gmail_password,
            },
        )
        email_handler.setFormatter(log_format)
        email_handler.setLevel(logging.WARNING)
        base_logger.addHandler(email_handler)

    # add extra info on user
    logger = logging.LoggerAdapter(base_logger, {"user": cfg.user_id})


# ---------------------------------------------------------------------------------------------
# Browser and login
# ---------------------------------------------------------------------------------------------


def start_driver():
    global driver
    with step("Error while initializing Chrome webdriver"):
        options = webdriver.ChromeOptions()
        # don't wait for images/subresources, login waits for elements explicitly
        options.page_load_strategy = "eager"
        options.add_argument("--headless=new")
        options.add_argument("--blink-settings=imagesEnabled=false")
        # Chrome logs requests it sends (with headers), get_req_header() reads Authorization from there.
        # No proxy in between, so the web app's big scripts load at full speed.
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        options.add_experimental_option("perfLoggingPrefs", {"enableNetwork": True, "enablePage": False})
        for argument in cfg.chrome_arguments:
            options.add_argument(argument)
        driver = webdriver.Chrome(options=options)


def quit_driver():
    global driver
    if driver is not None:
        try:
            driver.quit()
        except Exception as error:
            logger.debug("Error while closing Chrome: %r", error)
        driver = None


def get_cookies():
    with step("Error while gettitng cookies for authorization"):
        return {cookie["name"]: cookie["value"] for cookie in driver.get_cookies()}


def read_authorization(log_entries):
    # Authorization header of the latest authorized get-employee-context request in Chrome performance log, or None
    authorization = None
    for entry in log_entries:
        message = json.loads(entry["message"])["message"]
        if message["method"] != "Network.requestWillBeSent":
            continue
        request = message["params"]["request"]
        if not re.search(EMPLOYEE_CONTEXT_REQUEST, request["url"]):
            continue
        for name, value in request.get("headers", {}).items():
            if name.lower() == "authorization" and value:
                authorization = value
    return authorization


def get_req_header():
    with step("Error while gettitng headers for Authorization from Selenium"):
        # wait for web app to make the request, then take Authorization from the latest one.
        # Chrome hands out every log entry once, so already read entries are not seen again
        deadline = time.monotonic() + 30
        while True:
            authorization = read_authorization(driver.get_log("performance"))
            if authorization:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("No authorized " + EMPLOYEE_CONTEXT_REQUEST + " request seen")
            time.sleep(0.2)

    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:93.0) Gecko/20100101 Firefox/93.0",
        "Accept": "application/json; charset=utf8",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
        "Authorization": authorization,
        "Origin": "https://share.parkanizer.com",
        "DNT": "1",
        "Connection": "keep-alive",
        "Referer": "https://share.parkanizer.com/marketplace",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def login():
    # Login into page and get proper authntication cookies and headers for later usage
    with step("Error while initializing selenium and logging into parkanizer"):
        driver.delete_all_cookies()
        # drop requests logged so far, only ones made after this login count
        driver.get_log("performance")

        logger.info("Initating login to parkanizer")
        driver.get("https://share.parkanizer.com")
        # web app loads ~10MB of scripts before redirecting to login page, give slow connections time
        wait = WebDriverWait(driver, 30)
        try:
            wait.until(EC.title_is("User details"))
            wait.until(EC.url_contains("https://login.parkanizer.com"))
            wait.until(EC.visibility_of_element_located((By.ID, "signInName")))
            driver.find_element(By.ID, "signInName").send_keys(cfg.user)
            driver.find_element(By.ID, "continue").click()
            wait.until(EC.visibility_of_element_located((By.ID, "password")))
            driver.find_element(By.ID, "password").send_keys(cfg.password)
            driver.find_element(By.ID, "next").click()
            wait.until(EC.url_contains("https://share.parkanizer.com/welcome/employee"))
        except TimeoutException:
            # say where login got stuck, i.e. changed login page or wrong password
            raise TimeoutError(
                "login page did not reach expected state, stuck at " + repr(driver.current_url)
                + " with title " + repr(driver.title)
            ) from None
        logger.info("Succesfully logged in")

    # logged in, now getting headers and cookies
    return get_req_header(), get_cookies()


def set_auth(headers, cookies):
    http.headers.clear()
    http.headers.update(headers)
    http.cookies.clear()
    http.cookies.update(cookies)


def relogin():
    # Fresh browser, as the old one may still hold login session and skip login page
    quit_driver()
    start_driver()
    set_auth(*login())


# ---------------------------------------------------------------------------------------------
# Parkanizer API
# ---------------------------------------------------------------------------------------------


def api_post(path, payload):
    # POST to Parkanizer API with timeout, retries on network errors/5xx and new login when authorization expired
    relogged = False
    attempt = 0
    while True:
        attempt += 1
        try:
            response = http.post(API_URL + path, json=payload, timeout=REQUEST_TIMEOUT)
            if response.status_code == 401:
                raise SessionExpired()
            if response.status_code >= 500:
                raise requests.HTTPError(
                    str(response.status_code) + " server error", response=response
                )
            response.raise_for_status()
            return response
        except SessionExpired:
            if relogged:
                raise requests.HTTPError("401 Unauthorized even after new login")
            logger.info("Authorization expired, logging in again")
            relogin()
            relogged = True
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as error:
            is_server_error = (
                isinstance(error, requests.HTTPError)
                and error.response is not None
                and error.response.status_code >= 500
            )
            if isinstance(error, requests.HTTPError) and not is_server_error:
                raise
            if attempt >= REQUEST_TRIES:
                raise
            wait = RETRY_BACKOFF * 2 ** (attempt - 1)
            logger.info(
                "Request to %s failed (%s), retry %d of %d in %ss",
                path, error, attempt, REQUEST_TRIES - 1, wait,
            )
            time.sleep(wait)


def get_zone_id():
    # First parking zone avaliable for user, the one web app shows by default
    with step("Error while getting parking zones from web"):
        zones = api_post("marketplace/get-parking-spot-zones", {}).json()["parkingSpotZones"]
    if not zones:
        raise ParkanizerError("No parking zones avaliable for user")
    logger.info(
        "Parking zones avaliable (set parkingSpotZoneId in config to use other than first): %s",
        ", ".join("%s = %s" % (zone["name"], zone["id"]) for zone in zones),
    )
    return zones[0]["id"]


def get_spots_status():
    # Returns ({date: reserved spot name or None}, {date: free spots count})
    with step("Error while gettitng spot status from web"):
        response = api_post(
            "marketplace/get-spots", {"parkingSpotZoneId": cfg.zone_id, "bookingTimeInterval": FULL_DAY}
        )
        spots = response.json()

    reserved = {}
    free = {}
    with step("Error while processing spots status received from web"):
        for week in spots["weeks"]:
            for row in week["week"]:
                logger.debug(
                    "Date %s, ReservedParkingSpot %s, Free spots: %s",
                    row["day"], row["reservedParkingSpotOrNull"], row["freeSpots"],
                )
                date = datetime.fromisoformat(row["day"]).date()
                spot = row["reservedParkingSpotOrNull"]
                reserved[date] = spot["name"] if spot else None
                free[date] = row["freeSpots"]
    return reserved, free


def make_booking(date):
    # Returns name of booked spot or None when there was no free spot
    with step("Error while booking spot for " + str(date)):
        response = api_post(
            "employee-reservations/take-spot-from-marketplace",
            {
                "dayToTake": str(date),
                "parkingSpotZoneId": cfg.zone_id,
                "parkingSpotIdOrNull": None,
                "bookingTimeInterval": FULL_DAY,
                "challengeTokenOrNull": None,
            },
        )
        result = response.json()
        spot = result.get("receivedParkingSpotOrNull")
    status = result.get("status")
    if spot is None and status not in TAKE_SPOT_OK_STATUSES:
        raise ParkanizerError("Parkanizer refused booking for " + str(date) + " with status " + repr(status))
    if spot is None:
        logger.info("Problem, no free spaces for %s", date)
        return None
    logger.debug("Booked for %s spot %s", date, spot["name"])
    return spot["name"]


def release_spot(date):
    with step("Error while relesing spot for " + str(date)):
        api_post(
            "employee-reservations/resign",
            {"daysToShare": [str(date)], "receivingEmployeeIdOrNull": None},
        )
    logger.debug("Spot from date %s released", date)


def logout():
    # web app logs out by opening this url, answer is redirect to login page logout
    response = http.get(API_URL + "auth0/logout", allow_redirects=False, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()


# ---------------------------------------------------------------------------------------------
# Reservations made by this script in the past
# ---------------------------------------------------------------------------------------------


class ReservationStore:
    """Dates for which script booked spot. If there is no spot for such date anymore,
    user released it via app and it must not be booked again."""

    def __init__(self, path):
        with step("Error while opening reservations storage " + path):
            self._db = shelve.open(path)

    @staticmethod
    def _keys(date):
        # ISO date is the key used now. Older versions used "%A %B %d" (no year) which repeats
        # across years, it's still checked so reservations stored by older versions are honoured.
        return date.isoformat(), date.strftime("%A %B %d")

    def was_reserved(self, date):
        with step("Error while checking if reservation was already made in past for user"):
            return any(key in self._db for key in self._keys(date))

    def add(self, date):
        with step("Problem in writing reservation to storage"):
            key, legacy_value = self._keys(date)
            # value kept in old format so older versions (which check values) still see it after rollback
            self._db[key] = legacy_value
            self._db.sync()

    def close(self):
        self._db.close()


# ---------------------------------------------------------------------------------------------
# Booking logic
# ---------------------------------------------------------------------------------------------


def send_notifications(message, title, gmail=True, pushover=True):
    # gmail_notify_enabled / pushover_notify_enabled are master switches for each channel
    if pushover and cfg.pushover_enabled:
        pushover_notify(
            message,
            title,
            cfg.pushover_token,
            cfg.pushover_user,
            cfg.pushover_device,
        )
    if gmail and cfg.gmail_enabled:
        gmail_notify(
            message=message,
            title=title,
            password=cfg.gmail_password,
            username=cfg.gmail_user,
            to=cfg.gmail_to,
        )


def booking_decision(date, reserved_spot, free_spots, already_reserved):
    # Returns None if booking process should start for date, otherwise reason why not
    if date.isoweekday() not in cfg.book_for_weekdays:
        return "day not configured for booking"
    if reserved_spot in cfg.whitelist:
        return "Whitelisted spot " + reserved_spot + " already reserved for this date"
    if reserved_spot is None and already_reserved:
        return "spot was previously reserved but later released manually via app"
    if reserved_spot is not None and free_spots <= cfg.min_free_spots:
        return (
            "non Whitelisted spot " + reserved_spot + " already reserved, keeping it as only "
            + str(free_spots) + " free spots left"
        )
    return None


def report_booking(date, spot, store):
    if spot is not None:  # Send success confirmation if we have managed to books spot
        confirmation = (
            "Succesfull booking completed for " + date.strftime("%A %B %d") + " spot = " + spot
            + " check at https://share.parkanizer.com/reservations-list"
        )
        title = "Parkanizer " + date.strftime("%a %m-%d") + " spot = " + spot
        logger.info(confirmation)
        # stored to know later that somebody cancelled it and not to re-do reservation
        store.add(date)
    else:  # Send failure information if we were unable to book spot
        confirmation = (
            "Problem with booking for " + date.strftime("%A %B %d")
            + " there was no spots avaliable to book !!!. Please check manually at https://share.parkanizer.com/reservations-list"
        )
        title = "Parkanizer Problem " + date.strftime("%a %m-%d") + " no spots booked"
        logger.warning("%s for user %s", confirmation, cfg.user)
    send_notifications(
        message=confirmation,
        title=title,
        pushover=cfg.notify_booking_outcome_pushover,
        gmail=cfg.notify_booking_outcome_gmail,
    )
    logger.info("Notifications send")


def search_spots(dates, store):
    # Books spot for every date. If booked spot is not in our Whitelist release it and repeat booking untill we will get "Whitelisted".
    # If spot is None booking was unsuccesful as there was no free spaces so we need to abort.
    # 20240226 After change of Tidaro API they no longer loop through avaliable spot. Instead they alway provide one spot number until
    # somebody will book it. Only then they make next one avaliable.
    # So after releasing non-whitelisted spot we wait (pauseTime) and refresh list of avaliable spots. If number of free spots changed
    # for that date - somebody booked our non-whitelisted spot - we try again to check if avaliable one is "Whitelisted".
    # All dates are watched in one loop with one status request per iteration, so waiting for one date doesn't block the others.
    waiting = {}  # date -> free spots count right after we released our spot
    iterations = {date: 0 for date in dates}
    start = time.monotonic()

    def book(dates_to_book):
        spots = {}
        for date in dates_to_book:
            iterations[date] += 1
            spots[date] = make_booking(date)
        # Refresh number of free spots. If there is min_free_spots or less we need to take it and stop searching
        _, free = get_spots_status()
        released = []
        for date, spot in spots.items():
            if spot is None or spot in cfg.whitelist or free[date] <= cfg.min_free_spots:
                report_booking(date, spot, store)
                continue
            logger.info(
                "Searching for Whitelisted spot on: %s Iteration: %d Time spend searching: %s Free spaces: %d Got non Whitelisted spot: %s",
                date, iterations[date], timedelta(seconds=round(time.monotonic() - start)), free[date], spot,
            )
            release_spot(date)
            released.append(date)
        if released:
            # Wait to get different count of Free spot before moving to next booking try
            logger.info("Initiated wait for change in free spots avalaiable before next booking try")
            _, free = get_spots_status()
            for date in released:
                waiting[date] = free[date]

    book(dates)
    loop = 0
    while waiting:
        if cfg.max_search_time and time.monotonic() - start >= cfg.max_search_time:
            # Don't end up without any spot - take whatever is offered now
            logger.info(
                "Search time limit (maxSearchTime=%ss) reached, taking any avaliable spot for: %s",
                cfg.max_search_time, ", ".join(str(date) for date in waiting),
            )
            for date in list(waiting):
                del waiting[date]
                report_booking(date, make_booking(date), store)
            break
        loop += 1
        time.sleep(cfg.pause_time)
        _, free = get_spots_status()
        changed = [date for date in waiting if free[date] != waiting[date]]
        logger.debug(
            "Waiting for change in avaliable spots. Loop: %d, watched (date: free when released -> now): %s",
            loop, ", ".join("%s: %s -> %s" % (date, waiting[date], free[date]) for date in waiting),
        )
        for date in changed:
            del waiting[date]
        if changed:
            book(changed)


def remind_about_today(reserved):
    today = datetime.now().date()
    spot = reserved.get(today)
    if spot is None:
        return
    with step("Error while sending info about already booked spot for today"):
        send_notifications(
            message="Remember you have spot " + spot + " booked for today (" + today.strftime("%A")
            + "). Release if not needed via app or https://share.parkanizer.com/select-dates",
            title="Parkanizer today's (" + today.strftime("%a") + ") spot : " + spot,
            pushover=cfg.notify_reminder_pushover,
            gmail=cfg.notify_reminder_gmail,
        )
    logger.info("Sent reminder to user on booked spot for today.")


def parkanizer():
    set_auth(*login())
    if cfg.zone_id is None:
        cfg.zone_id = get_zone_id()

    # Get status of what you have currently booked
    reserved, free = get_spots_status()
    logger.info("Spots status: %s", reserved)
    logger.info("Free space status: %s", free)

    remind_about_today(reserved)

    store = ReservationStore(cfg.reservations_file)
    try:
        # Decide for every date what to do
        to_book = []
        for date, reserved_spot in reserved.items():
            reason = booking_decision(date, reserved_spot, free[date], store.was_reserved(date))
            if reason is not None:
                logger.info("No need to book for: %s - %s", date, reason)
                continue
            # Non Whitelisted spot while more than min_free_spots are free: release it and search for Whitelisted one
            if reserved_spot is not None:
                release_spot(date)
                logger.info("Released non whitelisted spot: %s from: %s", reserved_spot, date.strftime("%A %B %d"))
            to_book.append(date)

        # Book all dates at once, then keep searching for Whitelisted spots for all of them in one loop
        if to_book:
            logger.info("Start booking process for: %s", ", ".join(str(date) for date in to_book))
            search_spots(to_book, store)
    finally:
        store.close()

    logger.info("Done")
    try:
        logout()
        logger.info("Logged out")
    except Exception as error:
        logger.warning("Logout failed: %r", error)


def main(argv):
    config_file = argv[1] if len(argv) > 1 else ""
    if config_file.find(".ini") < 1:
        print('Please provide any ".ini" file as first parameter')
        return 1

    global cfg
    try:
        cfg = read_config(config_file)
    except ParkanizerError as error:
        print(error)
        return 1
    initialize_logger()
    logger.info("Initialization")

    try:
        start_driver()
        parkanizer()
    except ParkanizerError as error:
        logger.error("%s", error)
        return 1
    except Exception as error:
        logger.error("Unexpected error: %r", error)
        return 1
    finally:
        # Always close Chrome, also when run ended with error
        quit_driver()

    # Shutdown if succesful
    if cfg.shutdown_on_success:
        os.system("sudo shutdown +15")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
