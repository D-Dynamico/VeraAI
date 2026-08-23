"""The five endpoints the judge harness drives, plus teardown."""

import time
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vera.conversation import ConversationStore, respond
from vera.store import VALID_SCOPES, ContextStore

app = FastAPI()
store = ContextStore()
conversations = ConversationStore()
started_at = time.time()

METADATA = {
    "team_name": "D-Dynamico",
    "team_members": ["D-Dynamico"],
    "model": "gemini-2.0-flash",
    "approach": "Deterministic fact extraction with an LLM wordsmithing layer and a template fallback",
    "contact_email": "contact@example.com",
    "version": "0.1.0",
    "submitted_at": "2026-08-23T00:00:00Z",
}


class ContextPush(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = []


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


@app.get("/v1/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - started_at),
        "contexts_loaded": store.counts_by_scope(),
    }


@app.get("/v1/metadata")
def metadata() -> dict[str, Any]:
    return METADATA


@app.post("/v1/context")
def push_context(request: ContextPush) -> JSONResponse:
    if request.scope not in VALID_SCOPES:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "invalid_scope", "details": f"unknown scope {request.scope!r}"},
        )

    result = store.put(request.scope, request.context_id, request.version, request.payload)
    if not result.accepted:
        return JSONResponse(
            status_code=409,
            content={"accepted": False, "reason": "stale_version", "current_version": result.current_version},
        )

    return JSONResponse(
        content={
            "accepted": True,
            "ack_id": f"ack_{request.context_id}_v{request.version}",
            "stored_at": request.delivered_at,
        }
    )


@app.post("/v1/tick")
def tick(request: TickRequest) -> dict[str, Any]:
    return {"actions": []}


@app.post("/v1/reply")
def reply(request: ReplyRequest) -> dict[str, Any]:
    state = conversations.conversation(request.conversation_id, request.merchant_id, request.customer_id)
    memory = conversations.partner(request.merchant_id, request.customer_id)
    if state.ended or memory.opted_out:
        return {"action": "end", "rationale": "Conversation already closed; no further sends on this thread."}
    return respond(state, memory, request.message)


@app.post("/v1/teardown")
def teardown() -> dict[str, Any]:
    store.clear()
    conversations.clear()
    return {"status": "ok"}
