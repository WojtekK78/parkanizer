# parkanizer

Parkanizer auto reservation and notification solution

You can setup your week days that you want your bookings and preferred spots list separated by commas (i.e. wider spots).

PLEASE REMEBER ABOUT RELEASING UNSUED PARKING SPOTS - Remember that after you have released spot via Parkanizer app/webpage it'll not be booked again automatically by script for the same particular calendar date. You need to book it manually if you will need that spot in the end for this calendar date.

There is lots of speling mistakes, let them be :)

- install dependencies sudo python -m pip install -r requirements.txt
- install chromium web driver and make sure it's accesible in PATH source -> https://chromedriver.chromium.org/downloads
- Setup confg in any .ini file i.e. "config.ini" based on provided template "config.ini.template" file
- Run as: "python parkanizer.py config.ini"
- if running headless on linux you can use following guides to setup Chromium wbedriver & to allow for it to work in Crontab (Display:0)
	- https://tecadmin.net/setup-selenium-chromedriver-on-ubuntu/
	- https://newbedev.com/run-selenium-with-crontab-python
Two final technical details to run headless in crontab-python
	1) I've had to run it as user's crontab not root (not: sudo crontab -e)
	2) I've changed chown of chromium directory to my username i.e. sudo chown myuser:mysuer /usr/bin/chromedriver

Sample crontab runnig at 00:05 daily
		
5 0 * * * DISPLAY=:0 cd /home/myuser/python/parkanizer/ && python3 parkanizer.py config.ini >> parkanizer.log 2>&1
