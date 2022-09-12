from telebot.async_telebot import AsyncTeleBot
import requests
import asyncio
from datetime import datetime, timedelta
from pypref import SinglePreferences as Preferences
import os

pref = Preferences(filename="preferences.py")

bot = AsyncTeleBot(os.getenv("TELEGRAM_BOT_API_KEY"), parse_mode=None)

PRAYERS = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
API_ENDPOINT = "https://waktu-solat-api.herokuapp.com/api/v1"
request = requests.get(f"{API_ENDPOINT}/zones.json")
ZONES = request.json()["data"]["zon"]
timers = dict()

class Timer:
    #https://stackoverflow.com/a/45430833
    def __init__(self, timeout, callback):
        self._timeout = timeout
        self._callback = callback
        self._task = asyncio.ensure_future(self._job())

    async def _job(self):
        await asyncio.sleep(self._timeout)
        await self._callback()

    def cancel(self):
        self._task.cancel()


class BotSettings:
    def __init__(self, chatid) -> None:
        self.selected_zone = "gombak"  # default zone
        self.current_prayer_num = -1
        self.alert_time = 600
        self.schedule = {prayer: None for prayer in PRAYERS}
        self.chatid = chatid
        self.alerts_enabled = False
        self.timer = None
        self.fetch_preferences()

    def fetch_preferences(self):
        prefs = pref.get(self.chatid)
        if prefs is None:
            return
        else:
            prefs = pref.get(self.chatid)
            self.schedule = prefs.get("schedule", {})
            self.alert_time = prefs.get("alert_time", 600)
            self.selected_zone = prefs.get("selected_zone", "gombak")
            self.alerts_enabled = prefs.get("alerts_enabled", False)

    def update_preferences(self):
        prefs = {
            self.chatid: {
                "schedule": self.schedule,
                "alert_time": self.alert_time,
                "selected_zone": self.selected_zone,
                "alerts_enabled": self.alerts_enabled,
            }
        }
        pref.set_preferences(prefs)


def format_time(time):
    date = datetime.now().date()
    timestamp = datetime.combine(
        date, datetime.strptime(time, "%H:%M").time()
    ).timestamp()
    return timestamp


def get_next_prayer_time(prayer_times, settings):
    time_now = datetime.today().timestamp()
    for i, prayer in enumerate(prayer_times):
        if format_time(prayer["time"]) > time_now:
            next_prayer_num = i
            settings.current_prayer_num = i - 1
            break
    print(PRAYERS[next_prayer_num])
    settings.update_preferences()
    next_prayer_time = format_time(prayer_times[next_prayer_num]["time"])
    return next_prayer_time


@bot.message_handler(commands=["list_zones"])
async def list_zones(context):
    text = "Availabled zones:\n\n"

    for zone in ZONES:
        text += "`" + zone + "`, "

    text = text[:-2]  # remove trailing comma and space

    await bot.send_message(context.chat.id, text)


@bot.message_handler(commands=["show_schedule"])
async def send_schedule(context):
    settings = BotSettings(context.chat.id)

    text = "*Muezzin Schedule:*\n\n"
    for prayer, muezzin in settings.schedule.items():
        text += f"*{prayer}*: @{muezzin}\n"

    text = text[:-1]  # remove extra \n

    await bot.send_message(settings.chatid, text, parse_mode="MarkdownV2")


@bot.message_handler(commands=["start"])
async def intialize(context):

    settings = BotSettings(context.chat.id)

    text = "‏اَلسَلامُ عَلَيْكُم وَرَحْمَةُ اَللهِ وَبَرَكاتُهُ‎.\nThe Muezzin Reminder Bot is online\."

    if not settings.alerts_enabled:
        text += ' To enable alerts, send "`/enable ZONE_NAME`"\.'

    await bot.reply_to(context, text, parse_mode="MarkdownV2")

    await list_zones(context)


@bot.message_handler(commands=["enable"])
async def enable_alerts(context):

    settings = BotSettings(context.chat.id)

    if timers[settings.chatid] is not None:
        timers[settings.chatid].cancel()

    if not ZONES:
        text = "Bot not started\. Start the bot by sending `/start`\."
    else:
        selected = context.text.split(" ")[1]

        if selected in ZONES:
            settings.selected_zone = selected
            settings.alerts_enabled = True
            settings.update_preferences()
            text = f"Alerts enabled for {selected}\. Alert will be sent 10 minutes before the next azan\."
        else:
            text = "Zone not found\. Make sure the selected zone is valid\. View valid zones by sending `/list_zones`\."

        await bot.reply_to(context, text, parse_mode="MarkdownV2")

        if settings.alerts_enabled:
            while True:
                await set_alert(context, settings)


async def create_alert(context, settings, time_to_wait):
    timers[settings.chatid] = settings.timer = Timer(time_to_wait)

    prayer_name = PRAYERS[(settings.current_prayer_num + 1) % 5]
    print(settings.current_prayer_num)
    text = ""

    muezzin = settings.schedule.get(prayer_name)
    if muezzin is not None:
        text += f"@{muezzin} "

    text += f"{prayer_name} in *{settings.alert_time // 60} minutes*\."

    await bot.send_message(context.chat.id, text, parse_mode="MarkdownV2")

    settings.current_prayer_num += 1

    timers[settings.chatid] = settings.timer = Timer(settings.alert_time + 5)


async def set_alert(context, settings):
    prayer_times = requests.get(
        f"{API_ENDPOINT}/prayer_times.json?zon={settings.selected_zone}"
    ).json()["data"][0]["waktu_solat"]

    del prayer_times[0]  # remove imsak
    del prayer_times[1]  #  remove syuruk

    time_to_wait = (
        get_next_prayer_time(prayer_times, settings) - datetime.now().timestamp()
    )

    print(settings.current_prayer_num)

    if time_to_wait < 0:
        now = datetime.now()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_duration = midnight.timestamp() - now.timestamp()
        await asyncio.sleep(sleep_duration)
        await set_alert(context, settings)
    else:
        time_to_wait -= settings.alert_time
        await create_alert(context, settings, time_to_wait)


@bot.message_handler(commands=["set_muezzin"])
async def set_muezzin(message):

    settings = BotSettings(message.chat.id)

    msg_txt = message.text.split(" ")
    if len(msg_txt) >= 3:
        prayer = msg_txt[1]
        user = msg_txt[2]
        settings.schedule[prayer] = user
        text = f"@{user} assigned as muezzin for {prayer} prayer\."
    else:
        text = f"Bad format\. Usage: `/set_muezzin PRAYER_NAME USERNAME`\."

    settings.update_preferences()

    await bot.reply_to(message, text, parse_mode="MarkdownV2")

@bot.message_handler(commands=["help"])
async def help(message):
    text = "*Usage:*\n`/enable ZONE_NAME` - Enable alerts for the particular zone.\n\
                      `/set_muezzin PRAYER_NAME USERNAME` - Assign a muezzin for a particular prayer.\n\
                      `/start` - Start the bot.\n\
                      `/list_zones` - View available zones.\n\
                      `/show_schedule` - View current muezzin schedule."

    await bot.reply_to(message, text, parse_mode="MarkdownV2")

asyncio.run(bot.polling())
