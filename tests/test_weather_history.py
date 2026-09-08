from datetime import datetime
from zoneinfo import ZoneInfo

from oracle_app.weather_history import parse_historical_weather_query


def test_parse_historical_weather_query_for_yesterday() -> None:
    query = parse_historical_weather_query(
        "what was the weather yesterday",
        now=datetime(2026, 4, 4, 12, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    assert query is not None
    assert query.target_date.isoformat() == "2026-04-03"


def test_parse_historical_weather_query_for_explicit_date_field() -> None:
    query = parse_historical_weather_query(
        "what was the temperature on march 12 2025",
        now=datetime(2026, 4, 4, 12, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    assert query is not None
    assert query.target_date.isoformat() == "2025-03-12"
    assert query.field == "temperature"


def test_month_name_is_not_damaged_by_ordinal_cleanup() -> None:
    query = parse_historical_weather_query(
        "what was the weather August 31st 2026",
        now=datetime(2026, 9, 6, 12, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert query is not None
    assert query.target_date.isoformat() == "2026-08-31"
