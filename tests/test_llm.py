from __future__ import annotations

import pytest

from hermes2.llm import LLMError, _configured_reasoning_effort, _extract_chat_content


def test_extract_chat_content_rejects_reasoning_only_response() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning_content": "private reasoning with no visible answer",
                },
                "finish_reason": "length",
            }
        ]
    }

    with pytest.raises(LLMError, match="reasoning content but no visible response"):
        _extract_chat_content(payload, "local-model")


def test_lmstudio_defaults_reasoning_effort_to_none() -> None:
    assert _configured_reasoning_effort("lmstudio", {}) == "none"
    assert _configured_reasoning_effort("lmstudio", {"reasoning_effort": "low"}) == "low"
    assert _configured_reasoning_effort("openai", {}) is None
