from notifiers import get_notifier
from notifiers.exceptions import BadArguments

pushover = get_notifier("pushover")
gmail = get_notifier("gmail")
gmail.defaults
{
    "subject": "Parkanizer notification",
    "html": False,
}


def pushover_notify(message, title, token, user, device):
    try:
        r = pushover.notify(
            message=message, title=title, token=token, user=user, device=device
        )
    except BadArguments as e:
        print(f"Pushover failed\n{e}")
        return

    if r.status != "Success":
        print(f"Pushover notification failed:\n{r.errors}")


def gmail_notify(message, title, to, username, password):
    try:
        r = gmail.notify(
            to=to,
            message=message,
            subject=title,
            username=username,
            password=password,
        )
    except BadArguments as e:
        print(f"Gmail failed\n{e}")
        return

    if r.status != "Success":
        print(f"Gmail notification failed:\n{r.errors}")
