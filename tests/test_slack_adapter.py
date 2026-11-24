import hashlib
import hmac
import time

from app.modules.integrations.adapters.slack.adapter import SlackMessagingAdapter
from app.modules.integrations.adapters.slack.block_kit import (
    build_leave_balance_card,
    build_leave_draft_card,
)


def compute_test_slack_signature(secret: str, timestamp: int, body: bytes) -> str:
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    computed = hmac.new(
        secret.encode("utf-8"), sig_basestring, hashlib.sha256
    ).hexdigest()
    return f"v0={computed}"


def test_slack_signature_valid():
    secret = "my_signing_secret_123"
    adapter = SlackMessagingAdapter(bot_token="xoxb-test", signing_secret=secret)

    now = int(time.time())
    body = b'{"type":"event_callback","event":{"type":"message","text":"hello"}}'
    sig = compute_test_slack_signature(secret, now, body)

    headers = {
        "x-slack-request-timestamp": str(now),
        "x-slack-signature": sig,
    }

    assert adapter.verify_signature(headers, body) is True


def test_slack_signature_tampered_body():
    secret = "my_signing_secret_123"
    adapter = SlackMessagingAdapter(bot_token="xoxb-test", signing_secret=secret)

    now = int(time.time())
    body = b'{"type":"event_callback","event":{"type":"message","text":"hello"}}'
    sig = compute_test_slack_signature(secret, now, body)

    tampered_body = (
        b'{"type":"event_callback","event":{"type":"message","text":"tampered"}}'
    )
    headers = {
        "x-slack-request-timestamp": str(now),
        "x-slack-signature": sig,
    }

    assert adapter.verify_signature(headers, tampered_body) is False


def test_slack_signature_replay_attack():
    secret = "my_signing_secret_123"
    adapter = SlackMessagingAdapter(bot_token="xoxb-test", signing_secret=secret)

    # 10 minutes old timestamp (exceeds 300s window)
    old_time = int(time.time()) - 600
    body = b'{"type":"test"}'
    sig = compute_test_slack_signature(secret, old_time, body)

    headers = {
        "x-slack-request-timestamp": str(old_time),
        "x-slack-signature": sig,
    }

    assert adapter.verify_signature(headers, body) is False


def test_parse_inbound_dm_event():
    adapter = SlackMessagingAdapter(bot_token="xoxb-test", signing_secret="secret")
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "channel_type": "im",
            "user": "U12345",
            "text": "What is my leave balance?",
            "channel": "D98765",
            "ts": "1700000000.000100",
        },
    }

    event = adapter.parse_inbound_event(payload)
    assert event is not None
    assert event.platform == "slack"
    assert event.event_type == "message"
    assert event.channel_type == "im"
    assert event.user_id == "U12345"
    assert event.text == "What is my leave balance?"
    assert event.channel_id == "D98765"


def test_parse_inbound_app_mention_event():
    adapter = SlackMessagingAdapter(bot_token="xoxb-test", signing_secret="secret")
    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "user": "U12345",
            "text": "<@U_BOT> apply casual leave tomorrow",
            "channel": "C98765",
            "ts": "1700000000.000200",
        },
    }

    event = adapter.parse_inbound_event(payload)
    assert event is not None
    assert event.event_type == "app_mention"
    assert event.user_id == "U12345"
    # Strips bot mention from text for AI processing
    assert "apply casual leave tomorrow" in event.clean_text


def test_build_leave_draft_block_kit():
    draft_data = {
        "leave_type": "Casual Leave",
        "start_date": "2026-12-01",
        "end_date": "2026-12-02",
        "total_days": 2.0,
        "reason": "Family vacation",
    }
    blocks = build_leave_draft_card(draft_data)
    assert len(blocks) >= 3
    # Check header
    assert blocks[0]["type"] == "header"
    # Check actions block contains confirm and cancel buttons
    actions = [b for b in blocks if b["type"] == "actions"][0]
    action_ids = [el["action_id"] for el in actions["elements"]]
    assert "confirm_leave_submission" in action_ids
    assert "cancel_leave_submission" in action_ids


def test_build_leave_balance_card():
    balances = [
        {"leave_type": "Casual Leave", "available_days": 8.0, "total_allocated": 12.0},
        {"leave_type": "Sick Leave", "available_days": 5.0, "total_allocated": 7.0},
    ]
    blocks = build_leave_balance_card(user_name="John Doe", balances=balances)
    assert len(blocks) >= 2
    assert blocks[0]["type"] == "header"
