"""Regression: parse_intent must return an error action with a user-facing
message when LM Studio times out or errors, NOT a bare unknown that falls
through to 'I didn't understand that.'"""
import sys
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm


def test_timeout_returns_error_with_message():
    with patch.object(llm.requests, "post", side_effect=requests.ReadTimeout("read timeout=120")):
        actions = llm.parse_intent("join milky me voice chat", [], [], [], None)
    assert len(actions) == 1
    a = actions[0]
    assert a["action"] == "error", f"expected error, got {a}"
    assert a.get("message"), "error action must carry a user-facing message"
    assert "LM Studio" in a["message"]


def test_connection_error_returns_error_with_message():
    with patch.object(llm.requests, "post", side_effect=requests.ConnectionError("refused")):
        actions = llm.parse_intent("join voice", [], [], [], None)
    assert actions[0]["action"] == "error"
    assert actions[0].get("message")


if __name__ == "__main__":
    test_timeout_returns_error_with_message()
    test_connection_error_returns_error_with_message()
    print("OK")
