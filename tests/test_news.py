from oracle_app.replies import build_reply_text
from oracle_app.schemas import DispatchPlan


def test_news_reply_uses_requested_source_label_when_no_headlines_are_available() -> None:
    dispatch = DispatchPlan(
        target="news",
        hook="news.execute",
        payload={"text": "reuters headlines"},
        status="executed",
        result={
            "action": "headlines",
            "source": "reuters",
            "source_label": "Reuters",
            "headlines": [],
            "error": "news_source_unavailable",
        },
    )
    assert build_reply_text(dispatch) == "I couldn't find any current headlines from Reuters."
