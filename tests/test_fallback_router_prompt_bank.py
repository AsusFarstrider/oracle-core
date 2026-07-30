from __future__ import annotations

from oracle_app.constants import FALLBACK_ROUTER_SYSTEM_PROMPT


def test_fallback_router_prompt_keeps_current_regression_bank_examples() -> None:
    prompt = FALLBACK_ROUTER_SYSTEM_PROMPT

    expected_examples = (
        "tell me a short joke about spaceships",
        "explain black holes like i am five",
        "put on some david bowie",
        "i want to hear some david bowie",
        "what do i have going on tomorrow",
        "anything on my calendar tomorrow morning",
        "catch me up on npr",
        "give me the latest from npr",
        "fill me in on npr",
        "what's the weather like in boston tomorrow",
        "do i need a coat in boston tomorrow",
        "should i bring an umbrella in boston tomorrow",
        "make it cooler in here",
        "make it warmer in here",
        "it is too cold in here",
        "it is too hot in here",
        "resume alex's audiobook",
        "start alex's audiobook again",
        "pick up where alex left off in their book",
        "what am i doing tomorrow",
        "it's dark in the guest room",
    )

    for example in expected_examples:
        assert example in prompt


def test_fallback_router_prompt_keeps_facts_as_classification_not_reply_generation() -> None:
    prompt = FALLBACK_ROUTER_SYSTEM_PROMPT

    assert "Do not answer the user." in prompt
    assert "Never put an answer, joke, explanation, or assistant reply into `normalized_text`." in prompt
    assert "If the request is factual, informational, explanatory, creative, conversational, open-ended, or should be answered directly, use `facts`." in prompt
    assert "For `domain = facts`, prioritize choosing the correct domain." in prompt


def test_fallback_router_prompt_keeps_bounded_normalization_guardrails() -> None:
    prompt = FALLBACK_ROUTER_SYSTEM_PROMPT

    assert "Rewrite only when a small rewrite makes the domain intent clearer for Oracle." in prompt
    assert "For capability domains, rewrite only enough to make the request clearer for Oracle." in prompt
    assert "For capability domains, prefer short executable phrasing over commentary." in prompt
    assert "For `music`, prefer imperative playback phrasing such as `play david bowie`." in prompt
    assert "For `news`, prefer short headline-summary phrasing such as `latest NPR headlines`." in prompt
    assert "For `calendar`, prefer short schedule phrasing such as `what's on my calendar tomorrow`." in prompt
    assert "For `weather`, prefer short forecast or current-weather phrasing such as `weather tomorrow in boston`." in prompt
    assert "For `weather`, preserve the user's requested location and time window." in prompt
    assert "Do not use `weather` for vague comfort, room, or environment-control phrasing" in prompt
    assert "Vague comfort or environment phrases that do not clearly ask about weather conditions should go to `facts`, not `weather`." in prompt
    assert "Do not replace a practical weather question with a different specific condition such as `snow` unless the user asked about that condition." in prompt
    assert "Practical weather questions about coats, umbrellas, or what it will feel like should normalize to a general weather forecast for the requested place and time." in prompt
    assert "Do not expand short requests into longer paraphrases." in prompt
    assert "Do not change time words such as `today`, `tomorrow`, `tonight`, `yesterday`, or weekday names unless the user said them differently." in prompt
    assert "If the user says `tomorrow`, do not return `today`." in prompt
    assert "For `news`, prefer `headlines` over vague words like `updates` when the user is asking for a news summary." in prompt
