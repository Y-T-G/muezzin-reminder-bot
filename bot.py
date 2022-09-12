import telebot
from telebot.async_telebot import AsyncTeleBot
import requests
import asyncio
from datetime import datetime, timedelta
from pypref import SinglePreferences as Preferences
import os
import logging
from aiohttp import web

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

pref = Preferences(filename="preferences.py")

token = os.getenv('TELEGRAM_BOT_API_KEY')
bot = AsyncTeleBot(token, parse_mode=None)

PRAYERS = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
API_ENDPOINT = "https://waktu-solat-api.herokuapp.com/api/v1"
request = requests.get(f"{API_ENDPOINT}/zones.json")
ZONES = request.json()["data"]["zon"]
timers = dict()

WEBHOOK_HOST = 'muezzin-reminder-bot.herokuapp.com'
WEBHOOK_PORT = int(os.getenv('PORT', 8443))
WEBHOOK_LISTEN = '0.0.0.0'
WEBHOOK_URL_BASE = "https://{}".format(WEBHOOK_HOST)
WEBHOOK_URL_PATH = "/{}/".format(token)

class Timer:
    #https://stackoverflow.com/a/45430833
    def __init__(self, timeout, callback=None, *args, **kwargs):
        self._timeout = timeout
        self._callback = callback
        self._task = asyncio.ensure_future(self._job(**kwargs))

    async def _job(self, **kwargs):
        await asyncio.sleep(self._timeout)
        await self._callback(**kwargs)

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


def time_to_mili(time):
    date = datetime.now().date()
    timestamp = datetime.combine(
        date, datetime.strptime(time, "%H:%M").time()
    ).timestamp()
    return timestamp


def format_time_12hours(time):
    return datetime.strptime(time, "%H:%M").strftime("%I:%M %p")


def get_next_prayer_time(prayer_times, settings):
    time_now = datetime.today().timestamp()
    next_prayer_num = 0
    for i, prayer in enumerate(prayer_times):
        if time_to_mili(prayer["time"]) > time_now:
            next_prayer_num = i
            settings.current_prayer_num = i - 1
            break
    print(PRAYERS[next_prayer_num])
    settings.update_preferences()
    next_prayer_time = time_to_mili(prayer_times[next_prayer_num]["time"])
    return next_prayer_time


async def list_zones(context):
    text = "Availabled zones:\n\n"

    for zone in ZONES:
        text += "`" + zone + "`, "

    text = text[:-2]  # remove trailing comma and space

    await bot.send_message(context.chat.id, text)


async def send_schedule(context):
    settings = BotSettings(context.chat.id)

    text = "*Muezzin Schedule:*\n\n"
    for prayer, muezzin in settings.schedule.items():
        text += f"*{prayer}*: @{muezzin}\n"

    text = text[:-1]  # remove extra \n

    await bot.send_message(settings.chatid, text, parse_mode="MarkdownV2")


async def send_prayer_times(context):
    settings = BotSettings(context.chat.id)

    prayer_times = requests.get(
        f"{API_ENDPOINT}/prayer_times.json?zon={settings.selected_zone}"
    ).json()["data"][0]["waktu_solat"]

    text = "*Prayer Times:*\n\n"
    for prayer in prayer_times:
        text += f"*{prayer['name'].title()}*: {format_time_12hours(prayer['time'])}\n"

    text = text[:-1]  # remove extra \n

    await bot.send_message(settings.chatid, text, parse_mode="MarkdownV2")


async def initialize(context):

    settings = BotSettings(context.chat.id)

    text = "‏اَلسَلامُ عَلَيْكُم وَرَحْمَةُ اَللهِ وَبَرَكاتُهُ‎.\nThe Muezzin Reminder Bot is online\."

    if not settings.alerts_enabled:
        text += ' To enable alerts, send "`/enable ZONE_NAME`"\.'

    await bot.reply_to(context, text, parse_mode="MarkdownV2")

    await list_zones(context)


async def enable_alerts(context):
    global timers
    settings = BotSettings(context.chat.id)

    if timers.get(settings.chatid) is not None:
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


async def create_alert(context, settings):
    prayer_name = PRAYERS[(settings.current_prayer_num + 1) % 5]
    print(settings.current_prayer_num)
    text = ""

    muezzin = settings.schedule.get(prayer_name)
    if muezzin is not None:
        text += f"@{muezzin} "

    text += f"{prayer_name} in *{settings.alert_time // 60} minutes*\ at *{format_time_12hours(settings.current_prayer_num['time'])}*."

    await bot.send_message(context.chat.id, text, parse_mode="MarkdownV2")

    settings.current_prayer_num += 1

    asyncio.sleep(settings.alert_time + 5)


async def set_alert(context, settings):
    global timers
    settings.prayer_times = requests.get(
        f"{API_ENDPOINT}/prayer_times.json?zon={settings.selected_zone}"
    ).json()["data"][0]["waktu_solat"]

    del settings.prayer_times[0]  # remove imsak
    del settings.prayer_times[1]  #  remove syuruk

    time_to_wait = (
        get_next_prayer_time(settings.prayer_times, settings) - datetime.now().timestamp()
    )

    print(settings.current_prayer_num)

    if time_to_wait < 0:
        now = datetime.now()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_duration = midnight.timestamp() - now.timestamp()
        asyncio.sleep(sleep_duration)
        await set_alert(context, settings)
    else:
        time_to_wait -= settings.alert_time
        timers[settings.chatid] = settings.timer = Timer(time_to_wait, create_alert, context=context, settings=settings)
        await asyncio.sleep(time_to_wait) # wait before exiting


async def set_muezzin(message):

    settings = BotSettings(message.chat.id)

    msg_txt = message.text.split(" ")
    if len(msg_txt) >= 3 and msg_txt[1] in PRAYERS:
        prayer = msg_txt[1]
        user = msg_txt[2]
        settings.schedule[prayer] = user
        text = f"@{user} assigned as muezzin for {prayer} prayer\."
    else:
        text = f"Bad format or incorrect prayer name\. Usage: `/set_muezzin PRAYER_NAME USERNAME`\."

    settings.update_preferences()

    await bot.reply_to(message, text, parse_mode="MarkdownV2")


async def help(message):
    text = "*Usage:*\n`/enable ZONE_NAME` - Enable alerts for the particular zone\.\n\
                      `/set_muezzin PRAYER_NAME USERNAME` - Assign a muezzin for a particular prayer\.\n\
                      `/start` - Start the bot\.\n\
                      `/list_zones` - View available zones\.\n\
                      `/show_schedule` - View current muezzin schedule\.\n\
                      `/show_prayer_times` - View prayer times for your zone\."

    await bot.reply_to(message, text, parse_mode="MarkdownV2")

# Process webhook calls
async def handle(request):
    if request.match_info.get('token') == bot.token:
        request_body_dict = await request.json()
        update = telebot.types.Update.de_json(request_body_dict)
        asyncio.ensure_future(bot.process_new_updates([update]))
        return web.Response()
    else:
        return web.Response(status=403)

# Remove webhook and closing session before exiting
async def shutdown(app):
    logger.info('Shutting down: removing webhook')
    await bot.remove_webhook()
    logger.info('Shutting down: closing session')
    await bot.close_session()


async def setup():
    bot.register_message_handler(initialize, commands=["start"])
    bot.register_message_handler(help, commands=["help"])
    bot.register_message_handler(enable_alerts, commands=["enable"])
    bot.register_message_handler(set_muezzin, commands=["set_muezzin"])
    bot.register_message_handler(send_schedule, commands=["show_schedule"])
    bot.register_message_handler(list_zones, commands=["list_zones"]) 
    bot.register_message_handler(send_prayer_times, commands=["show_prayer_times"]) 

    # Remove webhook, it fails sometimes the set if there is a previous webhook
    logger.info('Starting up: removing old webhook')
    await bot.remove_webhook()
    # Set webhook
    logger.info('Starting up: setting webhook')
    print(WEBHOOK_URL_BASE + WEBHOOK_URL_PATH)
    await bot.set_webhook(url=WEBHOOK_URL_BASE + WEBHOOK_URL_PATH)
    app = web.Application()
    app.router.add_post('/{token}/', handle)
    app.on_cleanup.append(shutdown)
    return app


if __name__ == '__main__':
    # Start aiohttp server
    web.run_app(
        setup(),
        host=WEBHOOK_LISTEN,
        port=WEBHOOK_PORT
    )