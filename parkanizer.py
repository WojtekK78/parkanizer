from seleniumwire import webdriver  # Import from seleniumwire
from seleniumwire.utils import decode
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from datetime import datetime, timedelta
from parkanizer_notifiers import pushover_notify
from parkanizer_notifiers import gmail_notify
import requests
import configparser
import sys
import logging
import shelve
import time
import os
from notifiers.logging import NotificationHandler


def get_cookies():
    cookies = {}
    try:
        selenium_cookies = driver.get_cookies()
        for cookie in selenium_cookies:
            cookies[cookie["name"]] = cookie["value"]
    except Exception as error:
        logger.error("Error while gettitng cookies for authorization")
        logger.error(error)
        sys.exit(1)
        return

    return cookies


def get_req_header():
    try:
        for request in driver.requests:
            if request.url == "https://share.parkanizer.com/api/get-employee-context":
                Authorization = request.headers["Authorization"]

        header = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:93.0) Gecko/20100101 Firefox/93.0",
            "Accept": "application/json; charset=utf8",
            "Accept-Language": "en-US,en;q=0.5",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
            "Authorization": Authorization,
            "Origin": "https://share.parkanizer.com",
            "DNT": "1",
            "Connection": "keep-alive",
            "Referer": "https://share.parkanizer.com/marketplace",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
    except Exception as error:
        logger.error("Error while gettitng headers for Authorization from Selenium")
        logger.error(error)
        sys.exit(1)
        return

    return header


# Seconds to wait for Parkanizer to answer a single request
REQUEST_TIMEOUT = 30
# Number of tries for a request failing with network error or 5xx, waits RETRY_BACKOFF, 2x, 4x... seconds between tries
REQUEST_TRIES = 4
RETRY_BACKOFF = 2

driver = None

# Authorization headers and cookies taken from browser after login, refreshed by relogin()
auth = {"headers": None, "cookies": None}


class SessionExpired(Exception):
    pass


def api_post(url, data):
    # POST to Parkanizer API with timeout, retries on network errors/5xx and new login when authorization expired
    relogged = False
    attempt = 0
    while True:
        attempt += 1
        try:
            response = requests.post(
                url,
                headers=auth["headers"],
                cookies=auth["cookies"],
                data=data,
                timeout=REQUEST_TIMEOUT,
            )
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
                "Request to " + url + " failed (" + str(error) + "), retry " + str(attempt)
                + " of " + str(REQUEST_TRIES - 1) + " in " + str(wait) + "s"
            )
            time.sleep(wait)


def get_spots_status():
    dict_spots_avaliable = {}
    dict_spots_avaliable.clear()
    dict_spots_free = {}
    dict_spots_free.clear()


    #    cookies = get_cookies()
    data = '{"parkingSpotZoneId":"fa44ef73-af90-48fb-b2f7-da513a25239e"}'

    try:
        response = api_post("https://share.parkanizer.com/api/marketplace/get-spots", data)
        spots_avaliable = response.json()
    except Exception as error:
        logger.error("Error while gettitng spot status from web")
        logger.error(error)
        sys.exit(1)
        return

    # transform response into simple dictionary with date and info on space
    try:
        for i in spots_avaliable["weeks"]:
            for row in i["week"]:
                logger.debug(
                    (
                        "Date",
                        row["day"],
                        ", ReservedParkingSpot ",
                        row["reservedParkingSpotOrNull"],
                        " Free spots: ",
                        row["freeSpots"],
                    ),
                )
                ReservationDate = datetime.fromisoformat(row["day"]).date()
                if row["reservedParkingSpotOrNull"] == None:
                    ReservedSpot = "None"
                else:
                    ReservedSpot = row["reservedParkingSpotOrNull"]["name"]
                dict_spots_avaliable[ReservationDate] = ReservedSpot
                dict_spots_free[ReservationDate] = row["freeSpots"]
    except Exception as error:
        logger.error("Error while processing spots statuse receivd from web")
        logger.error(error)
        sys.exit(1)
        return
    return dict_spots_avaliable, dict_spots_free


def make_booking(daytotake):
    spot = ""
    #    cookies = get_cookies()
    data = (
        '{"dayToTake":"'
        + daytotake
        + '", "parkingSpotZoneId":"fa44ef73-af90-48fb-b2f7-da513a25239e"}'
    )
    try:
        response = api_post("https://share.parkanizer.com/api/employee-reservations/take-spot-from-marketplace", data)
        spot = response.json()
    except Exception as error:
        logger.error("Error while gettitng reponse on making booking")
        logger.error(error)
        sys.exit(1)
        return

    try:
        if spot["receivedParkingSpotOrNull"] == None:
            spot = None
            logger.info(("Problem, no free spaces for ", daytotake))
        else:
            spot = spot["receivedParkingSpotOrNull"]["name"]
            logger.debug(("Booked for ", daytotake, " spot ", spot))
    except Exception as error:
        logger.error("Error while processing response results on making booking")
        logger.error(error)
        sys.exit(1)
        return

    return spot


def release_spot(daystoshare):
    #    cookies = get_cookies()
    data = '{"daysToShare":["' + daystoshare + '"],"receivingEmployeeIdOrNull":null}'
    try:
        response = api_post("https://share.parkanizer.com/api/employee-reservations/resign", data)
    except Exception as error:
        logger.error("Error while relesing inconvinient spot")
        logger.error(error)
        sys.exit(1)
        return
    logger.debug(("Spot from date ", daystoshare, " released"))
    return response.status_code

def logout():
    #    cookies = get_cookies()
    data = "{}"
    response = api_post("https://share.parkanizer.com/api/auth0/logout", data)
    return response.status_code


def send_notifications(message, title, gmail=True, pushover=True):
    # gmail_notify_enabled / pushover_notify_enabled are master switches for each channel
    if pushover and pushover_notify_enabled:
        pushover_notify(
            message,
            title,
            pushover_token,
            pushover_user,
            pushover_device,
        )
    if gmail and gmail_notify_enabled:
        gmail_notify(
            message=message,
            title=title,
            password=gmail_password,
            username=gmail_user,
            to=gmail_to,
        )


def start_driver():
    global driver
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--disable-proxy-certificate-handler")
        options.add_argument("--disable-content-security-policy")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument('--allow-running-insecure-content')
        driver = webdriver.Chrome(options=options)
    except Exception as error:
        logger.error("Error while initializing Chrome webdriver")
        logger.error(error)
        sys.exit(1)


def quit_driver():
    global driver
    if driver is not None:
        try:
            driver.quit()
        except Exception as error:
            logger.debug("Error while closing Chrome: " + str(error))
        driver = None


def relogin():
    # Fresh browser, as the old one may still hold login session and skip login page
    quit_driver()
    start_driver()
    auth["headers"], auth["cookies"] = login()


def login():
    # Login into page and get proper authntication cookies and headers for later usage
    try:
        driver.delete_all_cookies()
        del driver.requests
        all_requests = driver.requests

        # logging in
        logger.info("Initating login to parkanizer")
        driver.get("https://share.parkanizer.com")
        wait = WebDriverWait(driver, 10)
        wait.until(EC.title_is("User details"))
        wait.until(EC.url_contains("https://login.parkanizer.com"))
        wait.until(
            EC.visibility_of_element_located((By.ID, "signInName"))
        )
        driver.find_element(By.ID, "signInName").send_keys(parkanizer_user)
        driver.find_element(By.ID, "continue").click()
        wait.until(
            EC.visibility_of_element_located((By.ID, "password"))
        )
        driver.find_element(By.ID, "password").send_keys(parkanizer_pass)
        driver.find_element(By.ID, "next").click()
        wait.until(EC.url_contains("https://share.parkanizer.com/welcome/employee"))
        logger.info("Succesfully logged in")
    except Exception as error:
        logger.error("Error while initializing selenium and logging into parkanizer")
        logger.error(error)
        sys.exit(1)
        return

    # logged in, now getting headers and cookies
    return get_req_header(), get_cookies()


def reservation_keys(date):
    # ISO date is the key used now. Older versions used "%A %B %d" (no year) which repeats
    # across years, it's still checked so reservations stored by older versions are honoured.
    return date.isoformat(), date.strftime("%A %B %d")


def was_reserved_before(date):
    try:
        shelve_db = "./shelve/reservations_" + parkanizer_user_id + ".db"
        with shelve.open(shelve_db) as reservationshelve:
            return any(key in reservationshelve for key in reservation_keys(date))
    except Exception as error:
        logger.error(
            "Error while checking if reservation was already made in past for user"
        )
        logger.error(error)
        sys.exit(1)


def store_reservation(date):
    try:
        shelve_db = "./shelve/reservations_" + parkanizer_user_id + ".db"
        with shelve.open(shelve_db) as reservationshelve:
            key, legacy_value = reservation_keys(date)
            # value kept in old format so older versions (which check values) still see it after rollback
            reservationshelve[key] = legacy_value
    except Exception as error:
        logger.error("Problem in writing reservation to storage")
        logger.error(error)
        sys.exit(1)


def booking_decision(date, reserved_spot, free_spots, alreadyreserved):
    # Returns None if booking process should start for date, otherwise reason why not
    holds_spot = reserved_spot != "None"
    if date.isoweekday() not in BookForWeekDay:
        return "day not configured for booking"
    if holds_spot and reserved_spot in Whitelist:
        return "Whitelisted spot " + reserved_spot + " already reserved for this date"
    if not holds_spot and alreadyreserved:
        return "spot was previously reserved but later released manually via app"
    if holds_spot and free_spots <= 2:
        return (
            "non Whitelisted spot " + reserved_spot + " already reserved, keeping it as only "
            + str(free_spots) + " free spots left"
        )
    return None


def report_booking(date, spot):
    if spot != None:  # Send success confirmation if we have managed to books spot
        confirmation = (
            "Succesfull booking completed for "
            + date.strftime("%A %B %d")
            + " spot = "
            + spot
            + " check at https://share.parkanizer.com/reservations-list"
        )
        title = "Parkanizer " + date.strftime("%a %m-%d") + " spot = " + spot
        logger.info(confirmation)

        # Writing succefull reservation data to shelve as it'll be used later to check if sombody cancelled and then not to re-do reservation
        store_reservation(date)
    else:  # Send failure information if we were unable to book spot
        confirmation = (
            "Problem with booking for "
            + date.strftime("%A %B %d")
            + " there was no spots avaliable to book !!!. Please check manually at https://share.parkanizer.com/reservations-list"
        )
        title = "Parkanizer Problem " + date.strftime("%a %m-%d") + " no spots booked"
        log_msg = confirmation + " for user " + parkanizer_user
        logger.warning(log_msg)
    send_notifications(
        message=confirmation,
        title=title,
        pushover=notify_booking_outcome_pushover,
        gmail=notify_booking_outcome_gmail,
    )
    logger.info("Notifications send")


def search_spots(dates):
    # Books spot for every date. If booked spot is not in our Whitelist release it and repeat booking untill we will get "Whitelisted".
    # If spot == None booking was unsuccesful as there was no free spaces so we need to abort.
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
            spots[date] = make_booking(daytotake=str(date))
        # Refresh number of free spots. If there is 2 or less we need to take it and stop searching
        not_used, free_status = get_spots_status()
        released = []
        for date, spot in spots.items():
            if spot == None or spot in Whitelist or free_status[date] <= 2:
                report_booking(date, spot)
                continue
            logger.info(
                "Searching for Whitelisted spot on: " + str(date)
                + " Iteration: " + str(iterations[date])
                + " Time spend searching: " + str(timedelta(seconds=round(time.monotonic() - start)))
                + " Free spaces: " + str(free_status[date])
                + " Got non Whitelisted spot: " + spot
            )
            release_spot(daystoshare=str(date))
            released.append(date)
        if released:
            # Wait to get different count of Free spot before moving to next booking try
            logger.info("Initiated wait for change in free spots avalaiable before next booking try")
            not_used, free_status = get_spots_status()
            for date in released:
                waiting[date] = free_status[date]

    book(dates)
    loop = 0
    while waiting:
        if maxSearchTime and time.monotonic() - start >= maxSearchTime:
            # Don't end up without any spot - take whatever is offered now
            logger.info(
                "Search time limit (maxSearchTime=" + str(maxSearchTime) + "s) reached, taking any avaliable spot for: "
                + ", ".join(str(d) for d in waiting)
            )
            for date in list(waiting):
                del waiting[date]
                report_booking(date, make_booking(daytotake=str(date)))
            break
        loop += 1
        time.sleep(pauseTime)
        not_used, free_status = get_spots_status()
        changed = [date for date in waiting if free_status[date] != waiting[date]]
        logger.debug(
            "Waiting for change in avaliable spots. Loop: " + str(loop)
            + ", watched (date: free when released -> now): "
            + ", ".join(str(d) + ": " + str(waiting[d]) + " -> " + str(free_status[d]) for d in waiting)
        )
        for date in changed:
            del waiting[date]
        if changed:
            book(changed)


def parkanizer():
    auth["headers"], auth["cookies"] = login()

    # Get status of what you have currently booked
    spots_status, free_status = get_spots_status()
    logger.info(("Spots status: " + str(spots_status)))
    logger.info(("Free space status: " + str(free_status)))

    # Send reminder if you have already booked place for Today
    try:
        today = datetime.now().date()
        if spots_status.get(today, "None") != "None":
            send_notifications(
                message="Remember you have spot "
                + spots_status[today]
                + " booked for today ("
                + today.strftime("%A")
                + "). Release if not needed via app or https://share.parkanizer.com/select-dates",
                title="Parkanizer today's ("
                + today.strftime("%a")
                + ") spot : "
                + spots_status[today],
                pushover=notify_reminder_pushover,
                gmail=notify_reminder_gmail,
            )
            logger.info(("Sent reminder to user on booked spot for today."))
    except Exception as error:
        logger.error("Error while sending info about already booked spot for today")
        logger.error(error)
        sys.exit(1)
        return

    # Decide for every date what to do
    to_book = []
    for date in spots_status:
        reserved_spot = spots_status[date]
        # Did this script already make reservation for that date in past? If so and there is no spot reserved now
        # then someone probably cancelled via app and there is no need to reserve for that day again
        alreadyreserved = was_reserved_before(date)
        reason = booking_decision(date, reserved_spot, free_status[date], alreadyreserved)
        if reason is not None:
            logger.info("No need to book for: " + str(date) + " - " + reason)
            continue
        # If reserved spot is not Whitelisted and there is more than 2 free spots open,
        # release reservation and search for new Whitelisted spot
        if reserved_spot != "None":
            release_spot(daystoshare=str(date))
            logger.info("Released non whitelisted spot: " + reserved_spot + " from: " + date.strftime("%A %B %d"))
        to_book.append(date)

    # Book all dates at once, then keep searching for Whitelisted spots for all of them in one loop
    if to_book:
        logger.info("Start booking process for: " + ", ".join(str(d) for d in to_book))
        search_spots(to_book)

    logger.info("Done")
    try:
        logout()
        logger.info("Logged out")
    except Exception as error:
        logger.warning("Logout failed: " + str(error))


def initialize_logger():
    # Logger initialization
    # Create a custom logger
    global logger
    logger = logging.getLogger(__name__)

    # Create handlers
    notification_defaults = {
        "subject": "Parkanizer ERROR",
        "to": gmail_to,
        "username": gmail_user,
        "password": gmail_password,
    }

    # initiate extra infor on users to be added to log
    logger_user = {"user": parkanizer_user_id}

    c_handler = logging.StreamHandler()
    n_handler = NotificationHandler("gmail", defaults=notification_defaults)
    logger.setLevel(logLevel)

    # Create formatters and add it to handlers

    c_format = logging.Formatter(
        "%(levelname)s - %(asctime)s - %(user)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    c_handler.setFormatter(c_format)
    n_handler.setFormatter(c_format)
    n_handler.setLevel(logging.WARNING)

    # Add handlers to the logger
    logger.addHandler(c_handler)
    # enable gmail notifications only if gmail allowed in config
    if gmail_notify_enabled:
        logger.addHandler(n_handler)

    # add extra info on user
    logger = logging.LoggerAdapter(logger, logger_user)


def read_config():
    # Parsing config file
    try:
        config = configparser.ConfigParser()
        config.read(str(sys.argv[1]))
        global parkanizer_user, parkanizer_user_id, parkanizer_pass, notify_reminder_gmail, notify_reminder_pushover, notify_booking_outcome_gmail, notify_booking_outcome_pushover, pushover_notify_enabled, pushover_token, pushover_user, pushover_device, gmail_notify_enabled, gmail_user, gmail_password, gmail_to, Whitelist, BookForWeekDay, pauseTime, maxSearchTime, shutdownOnSuccess, logLevel
        parkanizer_user = config["login"]["parkanizer_user"]
        parkanizer_user_id = parkanizer_user.partition("@")[0].replace(".", "")
        parkanizer_pass = config["login"]["parkanizer_pass"]
        notify_reminder_gmail = config["notifications"].getboolean(
            "notify_reminder_gmail"
        )
        notify_reminder_pushover = config["notifications"].getboolean(
            "notify_reminder_pushover"
        )
        notify_booking_outcome_gmail = config["notifications"].getboolean(
            "notify_booking_outcome_gmail"
        )
        notify_booking_outcome_pushover = config["notifications"].getboolean(
            "notify_booking_outcome_pushover"
        )
        pushover_notify_enabled = config["pushover"].getboolean(
            "pushover_notify_enabled"
        )
        pushover_token = config["pushover"]["pushover_token"]
        pushover_user = config["pushover"]["pushover_user"]
        pushover_device = config["pushover"]["pushover_device"]
        gmail_notify_enabled = config["gmail"].getboolean("gmail_notify_enabled")
        gmail_user = config["gmail"]["gmail_user"]
        gmail_password = config["gmail"]["gmail_password"]
        gmail_to = config["gmail"]["gmail_to"]
        Whitelist = config["booking"]["Whitelist"].split(",")
        BookForWeekDay = [
            int(numeric_string)
            for numeric_string in config["booking"]["BookForWeekDay"].split(",")
        ]
        pauseTime = int(config["booking"]["pauseTime"])
        # 0 = search without time limit
        maxSearchTime = config["booking"].getint("maxSearchTime", fallback=3600)
        logLevel = config["other"]["logLevel"]
        shutdownOnSuccess = config["other"].getboolean(
            "shutdownOnSuccess"
        )
    except Exception as error:
        print("Problems with initalization of config file")
        sys.exit(1)
        return


if __name__ == "__main__":
    try:
        config_file = str(sys.argv[1])
    except IndexError:
        config_file = ""
    if config_file.find(".ini") < 1:
        print('Please provide any ".ini" file as first parameter')
        sys.exit(1)

    read_config()
    initialize_logger()
    logger.info("Initialization")

    try:
        start_driver()
        parkanizer()
    except SystemExit:
        raise
    except Exception as error:
        logger.error("Unexpected error: " + repr(error))
        sys.exit(1)
    finally:
        # Always close Chrome, also when run ended with error
        quit_driver()

    #Shutdown if succesful
    if shutdownOnSuccess:
        os.system('sudo shutdown +15')