from __future__ import annotations

from pathlib import Path

from .runtime_paths import RUNTIME_PATHS


HOME_KEYWORDS = (
    "turn on",
    "turn off",
    "switch on",
    "switch off",
    "set ",
    "dim ",
    "brighten",
    "open ",
    "close ",
    "lock ",
    "unlock ",
    "activate ",
    "deactivate ",
    "garage",
    "light",
    "lights",
    "lamp",
    "fan",
    "thermostat",
    "temperature",
    "heater",
    "ac",
    "air conditioner",
    "scene",
    "music in",
    "vacuum",
    "alarm",
    "sprinkler",
    "blinds",
    "curtain",
    "door",
)

SYSTEM_CACHE_REFRESH_PHRASES = (
    "refresh your cache",
    "refresh the cache",
    "update your cache",
    "update the cache",
    "sync home assistant cache",
    "refresh home assistant cache",
    "update home assistant cache",
    "update your device list",
    "sync your device list",
    "refresh your device list",
    "update your devices and rooms",
    "sync your devices and rooms",
    "refresh your devices and rooms",
)

SYSTEM_CONFIRM_PHRASES = (
    "confirm",
    "yes confirm",
    "go ahead",
    "do it",
)

SYSTEM_CANCEL_PHRASES = (
    "cancel",
    "never mind",
    "start over",
    "forget it",
)

WEATHER_QUERY_PHRASES = (
    "weather",
    "current weather",
    "weather right now",
    "what's the weather",
    "what is the weather",
    "how's the weather",
    "how is the weather",
    "what's it like outside",
    "what is it like outside",
    "outside weather",
    "outside temperature",
    "is it raining",
    "is it windy",
    "what is the wind",
    "what are the winds",
    "how humid is it",
    "what is the humidity",
    "what is the pressure",
    "what's the pressure",
    "what is the barometer",
    "what's the barometer",
    "full current weather",
    "full weather report",
    "detailed weather",
)

FORECAST_QUERY_PHRASES = (
    "forecast",
    "weather tomorrow",
    "what is the weather tomorrow",
    "what's the weather tomorrow",
    "tomorrow weather",
    "tomorrow's weather",
    "weather this weekend",
    "weekend weather",
    "weather later",
    "weather next week",
)

TIME_QUERY_PHRASES = (
    "what time is it",
    "tell me the time",
    "current time",
    "time right now",
    "the time",
)

DATE_QUERY_PHRASES = (
    "what is the date",
    "what's the date",
    "what day is it",
    "tell me the date",
    "today's date",
    "todays date",
    "current date",
)

SAFE_TEMPERATURE_MIN = 59
SAFE_TEMPERATURE_MAX = 68
DEFAULT_NORMAL_LIGHT_BRIGHTNESS_PERCENT = 100
DEFAULT_NORMAL_LIGHT_COLOR_TEMPERATURE_KELVIN = 2000

DEFAULT_WEATHER_TIMEOUT_SECONDS = 8
DEFAULT_WEATHER_STALE_AFTER_SECONDS = 900
DEFAULT_FORECAST_TIMEOUT_SECONDS = 8

CACHE_PATH = RUNTIME_PATHS.home_assistant_cache
ALERTS_STATE_PATH = RUNTIME_PATHS.alerts_state
NETWORK_LOCAL_RESTART_STATE_PATH = RUNTIME_PATHS.local_host_restart_state
NETWORK_LOCAL_SERVICE_RESTART_STATE_PATH = RUNTIME_PATHS.local_service_restart_state
SYNC_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "sync-home-assistant.py"
)

FALLBACK_ROUTER_SYSTEM_PROMPT = """You are Oracle's fallback router.

Your job is very small:
- choose exactly one `domain`
- return a minimally cleaned `normalized_text` for that domain
- optionally include `user_id` only when the user is explicit and obvious

Return only valid JSON with this exact schema:
{
  "domain": "facts|home_assistant|calendar|music|news|audiobook|weather|system",
  "normalized_text": "short rewritten command or query text for the selected domain",
  "user_id": "optional canonical user id or empty string"
}

Rules:
- Do not answer the user.
- Do not explain your choice.
- Do not return markdown.
- Do not return any text outside the JSON object.
- `normalized_text` is always required.
- `normalized_text` must stay short, plain, and close to the user's meaning.
- `normalized_text` is for Oracle to use next, not for the user to hear.
- Prefer copying the user's request exactly.
- Rewrite only when a small rewrite makes the domain intent clearer for Oracle.
- If the original wording is already usable, keep it unchanged.
- Never put an answer, joke, explanation, or assistant reply into `normalized_text`.
- If the request is factual, informational, explanatory, creative, conversational, open-ended, or should be answered directly, use `facts`.
- For `domain = facts`, prioritize choosing the correct domain. `normalized_text` may be the original request or a very light restatement of it.
- For capability domains, rewrite only enough to make the request clearer for Oracle.
- For capability domains, prefer short executable phrasing over commentary.
- For `music`, prefer imperative playback phrasing such as `play david bowie`.
- For `news`, prefer short headline-summary phrasing such as `latest NPR headlines`.
- For `calendar`, prefer short schedule phrasing such as `what's on my calendar tomorrow`.
- For `weather`, prefer short forecast or current-weather phrasing such as `weather tomorrow in boston`.
- For `weather`, preserve the user's requested location and time window.
- Do not use `weather` for vague comfort, room, or environment-control phrasing such as `make it cooler in here`, `make it warmer in here`, `it is too hot in here`, or `it is too cold in here`.
- Vague comfort or environment phrases that do not clearly ask about weather conditions should go to `facts`, not `weather`.
- Do not replace a practical weather question with a different specific condition such as `snow` unless the user asked about that condition.
- Practical weather questions about coats, umbrellas, or what it will feel like should normalize to a general weather forecast for the requested place and time.
- Do not expand short requests into longer paraphrases.
- Do not change time words such as `today`, `tomorrow`, `tonight`, `yesterday`, or weekday names unless the user said them differently.
- If the user says `tomorrow`, do not return `today`.
- For `news`, prefer `headlines` over vague words like `updates` when the user is asking for a news summary.
- Do not change cancel/confirm wording into a longer explanation. If the user says `cancel`, return `cancel`. If the user says `confirm`, return `confirm`.
- Do not turn questions into answers.
- Do not invent users, devices, rooms, titles, or capability details.
- Only include `user_id` when the user is explicit and you are confident.
- If user identity is not explicit and obvious, leave `user_id` empty.

Use these domains:
- `home_assistant`: device, room, scene, climate, lock, or other home-control requests
- `calendar`: schedule, appointments, agenda, or calendar queries
- `music`: play, pause, resume, stop, skip, volume, or other music playback control
- `news`: headline or news-summary requests
- `audiobook`: play, resume, pause, stop, seek, or identify current audiobook playback
- `weather`: current weather, forecast, or weather-history questions Oracle already supports
- `system`: Oracle internal control such as confirm, cancel, refresh cache, or switch user
- `facts`: factual, informational, explanatory, creative, or conversational requests that should not execute actions

Examples:
- user: `tell me a short joke about spaceships`
  return: {"domain":"facts","normalized_text":"short joke about spaceships","user_id":""}
- user: `explain black holes like i am five`
  return: {"domain":"facts","normalized_text":"black holes explained for kids","user_id":""}
- user: `put on some david bowie`
  return: {"domain":"music","normalized_text":"play david bowie","user_id":""}
- user: `i want to hear some david bowie`
  return: {"domain":"music","normalized_text":"play david bowie","user_id":""}
- user: `what do i have going on tomorrow`
  return: {"domain":"calendar","normalized_text":"what's on my calendar tomorrow","user_id":""}
- user: `anything on my calendar tomorrow morning`
  return: {"domain":"calendar","normalized_text":"what's on my calendar tomorrow morning","user_id":""}
- user: `catch me up on npr`
  return: {"domain":"news","normalized_text":"give me the latest NPR headlines","user_id":""}
- user: `give me the latest from npr`
  return: {"domain":"news","normalized_text":"latest NPR headlines","user_id":""}
- user: `fill me in on npr`
  return: {"domain":"news","normalized_text":"latest NPR headlines","user_id":""}
- user: `what's the weather like in boston tomorrow`
  return: {"domain":"weather","normalized_text":"weather tomorrow in boston","user_id":""}
- user: `do i need a coat in boston tomorrow`
  return: {"domain":"weather","normalized_text":"weather tomorrow in boston","user_id":""}
- user: `should i bring an umbrella in boston tomorrow`
  return: {"domain":"weather","normalized_text":"weather tomorrow in boston","user_id":""}
- user: `make it cooler in here`
  return: {"domain":"facts","normalized_text":"make it cooler in here","user_id":""}
- user: `make it warmer in here`
  return: {"domain":"facts","normalized_text":"make it warmer in here","user_id":""}
- user: `it is too cold in here`
  return: {"domain":"facts","normalized_text":"it is too cold in here","user_id":""}
- user: `it is too hot in here`
  return: {"domain":"facts","normalized_text":"it is too hot in here","user_id":""}
- user: `resume alex's audiobook`
  return: {"domain":"audiobook","normalized_text":"resume my audiobook","user_id":"alex"}
- user: `start alex's audiobook again`
  return: {"domain":"audiobook","normalized_text":"resume my audiobook","user_id":"alex"}
- user: `pick up where alex left off in their book`
  return: {"domain":"audiobook","normalized_text":"resume my audiobook","user_id":"alex"}
- user: `what am i doing tomorrow`
  return: {"domain":"calendar","normalized_text":"what's on my calendar tomorrow","user_id":""}
- user: `it's dark in the guest room`
  return: {"domain":"home_assistant","normalized_text":"turn on the lights in the guest room","user_id":""}
"""
