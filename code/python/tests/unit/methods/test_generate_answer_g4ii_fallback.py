"""Task 12 / G4(ii): generate_answer exception fallback uses msg_type (was
hardcoded 'nlws', invisible in unified mode because the frontend has no
'nlws' case). After the fix, unified mode sends the fallback as 'answer' so
the error text renders via onAnswer -> renderAnswerProgressive.

Also verifies non-unified mode still sends 'nlws' (behaviour unchanged there).
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


def _make_ga(generate_mode):
    """Build a GenerateAnswer without running the heavy NLWebHandler __init__;
    set only the attributes synthesizeAnswer's except-path reads."""
    from methods.generate_answer import GenerateAnswer
    ga = GenerateAnswer.__new__(GenerateAnswer)
    ga.generate_mode = generate_mode
    ga.final_ranked_answers = [("u", "{}", "n", "s")]  # non-empty -> proceeds to PromptRunner
    ev = MagicMock()
    ev.is_set.return_value = True
    ga.connection_alive_event = ev
    ga.message_sender = MagicMock()
    ga.message_sender.send_message = AsyncMock()
    return ga


@pytest.mark.asyncio
async def test_unified_exception_fallback_uses_answer():
    ga = _make_ga("unified")
    # Force an exception inside the try (PromptRunner.run_prompt raises) so we
    # reach the except-fallback (:931).
    with patch("methods.generate_answer.PromptRunner") as PR:
        PR.return_value.run_prompt = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await ga.synthesizeAnswer()
    sent = ga.message_sender.send_message.call_args[0][0]
    assert sent["message_type"] == "answer", (
        "unified exception fallback must use msg_type='answer' (G4(ii)), not 'nlws'")
    assert "抱歉，生成回答時發生錯誤" in sent["answer"]


@pytest.mark.asyncio
async def test_non_unified_exception_fallback_still_uses_nlws():
    ga = _make_ga("generate")  # non-unified
    with patch("methods.generate_answer.PromptRunner") as PR:
        PR.return_value.run_prompt = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await ga.synthesizeAnswer()
    sent = ga.message_sender.send_message.call_args[0][0]
    assert sent["message_type"] == "nlws", (
        "non-unified fallback semantics unchanged: msg_type='nlws'")
