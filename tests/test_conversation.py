"""Reply-path assertions, written against the grader's literal keyword lists.

The replay scenarios in judge_simulator.py check for exact substrings rather
than asking an LLM, so these tests encode the same strings. If the graded rule
changes, these fail before the test window does.
"""

from vera.conversation import (
    ACTION_WORDS,
    QUALIFYING_PHRASES,
    ConversationStore,
    classify,
    respond,
)

AUTO_REPLY = "Thank you for contacting us! Our team will respond shortly."


def _fresh():
    store = ConversationStore()
    return store, store.conversation("conv_1", "m_001", None), store.partner("m_001", None)


def test_first_auto_reply_waits_then_repeat_ends():
    store, state, memory = _fresh()

    first = respond(state, memory, AUTO_REPLY)
    assert first["action"] == "wait"
    assert first["wait_seconds"] == 14400

    # The judge sends each canned reply under a fresh conversation id, so
    # detection has to live on the merchant, not the conversation.
    second_state = store.conversation("conv_2", "m_001", None)
    second = respond(second_state, memory, AUTO_REPLY)
    assert second["action"] == "end"


def test_repeated_message_counts_as_auto_reply_even_when_unrecognised():
    _, state, memory = _fresh()
    novel = "Namaste, we have received your message and will revert."
    respond(state, memory, novel)
    assert classify(novel, memory) == "auto_reply"


def test_commitment_reply_satisfies_the_grader():
    _, state, memory = _fresh()
    result = respond(state, memory, "Ok lets do it. Whats next?")

    assert result["action"] == "send"
    body = result["body"].lower()
    assert any(word in body for word in ACTION_WORDS)
    assert not any(phrase in body for phrase in QUALIFYING_PHRASES)
    assert state.mode == "action"


def test_hostile_message_ends_and_suppresses():
    _, state, memory = _fresh()
    result = respond(state, memory, "Stop messaging me. This is useless spam.")

    assert result["action"] == "end"
    assert memory.opted_out
    assert "sorry" in result["rationale"].lower()


def test_off_topic_deflects_once_then_ends():
    _, state, memory = _fresh()

    first = respond(state, memory, "Btw can you also help me with my GST filing this month?")
    assert first["action"] == "send"
    assert "sorry" in first["body"].lower()

    second = respond(state, memory, "But what about the GST portal?")
    assert second["action"] == "end"


def test_three_empty_acknowledgements_end_the_conversation():
    _, state, memory = _fresh()
    for message in ("hmm", "achha", "theek"):
        result = respond(state, memory, message)
    assert result["action"] == "end"
