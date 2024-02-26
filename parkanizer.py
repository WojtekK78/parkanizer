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
from notifiers.logging import NotificationHandler


def get_cookies():
    cookies = {}
    try:
        selenium_cookies = driver.get_cookies()
        for cookie in selenium_cookies:
            cookies[cookie["name"]] = cookie["value"]
    except Exception:
        logger.error("Error while gettitng cookies for authorization")
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
    except Exception:
        logger.error("Error while gettitng headers for Authorization from Selenium")
        return

    return header


def get_spots_status(headers, cookies):
    dict_spots_avaliable = {}
    dict_spots_avaliable.clear()
    dict_spots_free = {}
    dict_spots_free.clear()


    #    cookies = get_cookies()
    data = '{"parkingSpotZoneId":"fa44ef73-af90-48fb-b2f7-da513a25239e"}'

    try:
        response = requests.post(
            "https://share.parkanizer.com/api/marketplace/get-spots",
            headers=headers,
            cookies=cookies,
            data=data,
        )
        spots_avaliable = response.json()
    except Exception:
        logger.error("Error while gettitng spot status from web")
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
    except Exception:
        logger.error("Error while processing spots statuse receivd from web")
        return
    return dict_spots_avaliable, dict_spots_free


def make_booking(headers, cookies, daytotake):
    spot = ""
    #    cookies = get_cookies()
    data = (
        '{"dayToTake":"'
        + daytotake
        + '", "parkingSpotZoneId":"fa44ef73-af90-48fb-b2f7-da513a25239e"}'
    )
    try:
        response = requests.post(
            "https://share.parkanizer.com/api/employee-reservations/take-spot-from-marketplace",
            headers=headers,
            cookies=cookies,
            data=data,
        )
        spot = response.json()
    except Exception:
        logger.error("Error while gettitng reponse on making booking")
        return

    try:
        if spot["receivedParkingSpotOrNull"] == None:
            spot = None
            logger.info(("Problem, no free spaces for ", daytotake))
        else:
            spot = spot["receivedParkingSpotOrNull"]["name"]
            logger.debug(("Booked for ", daytotake, " spot ", spot))
    except Exception:
        logger.error("Error while processing response results on making booking")
        return

    return spot


def release_spot(headers, cookies, daystoshare):
    #    cookies = get_cookies()
    data = '{"daysToShare":["' + daystoshare + '"],"receivingEmployeeIdOrNull":null}'
    try:
        response = requests.post(
            "https://share.parkanizer.com/api/employee-reservations/resign",
            headers=headers,
            cookies=cookies,
            data=data,
        )
    except Exception:
        logger.error("Error while relesing inconvinient spot")
        return
    logger.debug(("Spot from date ", daystoshare, " released"))
    return response.status_code


def logout(headers, cookies):
    #    cookies = get_cookies()
    data = "{}"
    response = requests.post(
        "https://share.parkanizer.com/api/auth0/logout",
        headers=headers,
        cookies=cookies,
        data=data,
    )
    return response.status_code


def send_notifications(message, title, gmail=True, pushover=True):
    if pushover:
        pushover_notify(
            message,
            title,
            pushover_token,
            pushover_user,
            pushover_device,
        )
    if gmail:
        gmail_notify(
            message=message,
            title=title,
            password=gmail_password,
            username=gmail_user,
            to=gmail_to,
        )


def parkanizer():

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
    except Exception:
        logger.error("Error while initializing selenium and logging into parkanizer")
        return

    # logged in, now getting headers and cookies
    headers = get_req_header()
    cookies = get_cookies()

    # Get status of what you have currently booked
    spots_status, free_status = get_spots_status(headers=headers, cookies=cookies)
    logger.info(("Spots status: " + str(spots_status)))
    logger.info(("Free space status: " + str(free_status)))

    # Send reminder if you have already booked place for Today
    try:
        today = datetime.now().date()
        if spots_status[today] != "None":
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
    except Exception:
        logger.error("Error while sending info about already booked spot for today")
        return

    # Start booking process
    for date in spots_status:
        # Setting status of alreadyreserved if we already have made reservation in past. If so then someone probably cancelled and there is no need to reserve for that day again
        try:
            shelve_db = "./shelve/reservations_" + parkanizer_user_id + ".db"
            reservationcheck = date.strftime("%A %B %d")
            reservationshelve = shelve.open(shelve_db)
            alreadyreserved = reservationcheck in list(reservationshelve.values())
            reservationshelve.close()
        except Exception:
            logger.error(
                "Error while checking if reservation was already made in past for user"
            )
            return
        # Check if for days enabled in config we have already spot reserved, if there is not then start booking porcess
        if (
            date.isoweekday() in BookForWeekDay
            and spots_status[date] == "None"
            and not alreadyreserved
        ):
            # We need to book spot
            logger.info("Start booking process")
            spot = make_booking(headers=headers, cookies=cookies, daytotake=str(date))
            # If our booked spot is not in our Whitelist release it and repeat booking untill we will get "Whitelisted"
            # or untill you have "checked" all spots and are getting same spots again (looped through all spaces and none is in our Whitelist)
            # Also if we have spot == None it means that booking was unsuccesful as there was no free spaces so we need to abort
            # 20240226 After change of Tidaro API they no longer loop through avaliable spot. Instead they alway provide one spot number until 
            # somebody will book it. Only then they make next one avaliable.
            # So if there is no good spot avaliablewe will wait 15 seconds refresh list of avaliable spots. If it has changed - somebody booked our
            # non-whitelisted spot we will try again to check if avaliable one ie "Whitelisted"
            i = 1 # loop counter
            while spot not in Whitelist and spot != None and free_status[date] > 2 :
                i += 1
                time.sleep(pauseTime) # Wait 10 seconds maybe someone booked non-Whitelisted and there is new space to book
                logger.info("Searching for Whitelisted spot on: " + str (date) + " Iteration: " + str(i) + " Time spend searching: " + str (timedelta(seconds=(i*pauseTime))) + " Free spaces: " + str(free_status[date]))
                release_spot(headers=headers, cookies=cookies, daystoshare=str(date))
                spot = make_booking(
                    headers=headers, cookies=cookies, daytotake=str(date)
                )
                #Refresh number of free spots for that day. If there is 2 we need to take it and stop searching
                not_used, free_status = get_spots_status(headers=headers, cookies=cookies)
            if (
                spot != None
            ):  # Send success confirmation if we have managed to books spot
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
                try:
                    shelve_db = "./shelve/reservations_" + parkanizer_user_id + ".db"
                    reservationcheck = date.strftime("%A %B %d")
                    reservationshelve = shelve.open(shelve_db)
                    reservationshelve[reservationcheck] = reservationcheck
                    reservationshelve.close()
                except Exception:
                    logger.error("Problem in writing reservation to storage")
                    return
            else:  # Send failure information if we were unable to book spot
                confirmation = (
                    "Problem with booking for "
                    + date.strftime("%A %B %d")
                    + " there was no spots avaliable to book !!!. Please check manually at https://share.parkanizer.com/reservations-list"
                )
                title = (
                    "Parkanizer Problem "
                    + date.strftime("%a %m-%d")
                    + " no spots booked"
                )
                log_msg = confirmation + " for user " + parkanizer_user
                logger.warning(log_msg)
            send_notifications(
                message=confirmation,
                title=title,
                pushover=notify_booking_outcome_pushover,
                gmail=notify_booking_outcome_gmail,
            )
            logger.info("Notifications send")
        else:
            reason = ""
            if not date.isoweekday() in BookForWeekDay:
                reason = " - day not configured for booking"
            if alreadyreserved:
                reason = " - spot was previously reserved but later released manually via app"
            if not spots_status[date] == "None":
                reason = " - spot already reserved for this date"
            confirmation = "No need to book for: " + str(date) + reason
            logger.info(confirmation)

    logger.info("Done")
    logout(headers=headers, cookies=cookies)
    driver.quit()
    logger.info("Logged out")


def initialize_logger():
    # Logger initialization
    # Create a custom logger
    global logger
    logger = logging.getLogger(__name__)

    # Create handlers
    notification_defaults = {
        "subject": "Parkanizer ERROR",
        "to": "cdpkxj2h@anonaddy.me",
        "username": gmail_user,
        "password": gmail_password,
    }

    # initiate extra infor on users to be added to log
    logger_user = {"user": parkanizer_user_id}

    c_handler = logging.StreamHandler()
    n_handler = NotificationHandler("gmail", defaults=notification_defaults)
    logger.setLevel(logging.INFO)

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
        global parkanizer_user, parkanizer_user_id, parkanizer_pass, notify_reminder_gmail, notify_reminder_pushover, notify_booking_outcome_gmail, notify_booking_outcome_pushover, pushover_notify_enabled, pushover_token, pushover_user, pushover_device, gmail_notify_enabled, gmail_user, gmail_password, gmail_to, Whitelist, BookForWeekDay, pauseTime
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
    except Exception:
        print("Problems with initalization of config file")
        return


if __name__ == "__main__":
    try:
        if str(sys.argv[1]).find(".ini") < 1:
            print('Please provide any ".ini" file as first parameter')
    except Exception:
        print('Please provide any ".ini" file as first parameter')
        sys.exit()

    read_config()
    initialize_logger()
    logger.info("Initialization")

    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--disable-proxy-certificate-handler")
        options.add_argument("--disable-content-security-policy")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument('--allow-running-insecure-content')
        driver = webdriver.Chrome(options=options)
    except Exception:
        logger.error("Error while initializing Chrome webdriver")

    parkanizer()
