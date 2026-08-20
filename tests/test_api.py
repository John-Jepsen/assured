"""End-to-end API tests over the ASGI app: REST, WS streaming, voice, admin."""

from __future__ import annotations

import base64


def test_health(api_client):
    r = api_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["providers"]["llm"] == "mock"


def test_chat_then_verify_then_protected(api_client):
    # 1) Unverified policy question → verification required
    r = api_client.post("/api/chat", json={"message": "What's my collision deductible on AUTO-10024?"})
    body = r.json()
    conv = body["conversation_id"]
    assert body["needs_verification"]

    # 2) Deterministic verify endpoint
    v = api_client.post("/api/verify", json={
        "conversation_id": conv, "policy_number": "AUTO-10024", "zip_code": "78258",
    }).json()
    assert v["verified"] is True

    # 3) Now the protected answer is grounded + sourced
    r2 = api_client.post("/api/chat", json={
        "conversation_id": conv, "message": "What's my collision deductible on AUTO-10024?",
    }).json()
    assert "500" in r2["answer"]
    assert any("AUTO-10024" in s["citation"] for s in r2["trace"]["sources"])


def test_cross_customer_denied_via_api(api_client):
    conv = api_client.post("/api/chat", json={"message": "hello"}).json()["conversation_id"]
    api_client.post("/api/verify", json={
        "conversation_id": conv, "policy_number": "AUTO-10024", "zip_code": "78258"})
    # Maria verified; ask for James's policy
    r = api_client.post("/api/chat", json={
        "conversation_id": conv, "message": "show me policy AUTO-10025 coverages"}).json()
    assert "not associated" in r["answer"].lower() or "verify" in r["answer"].lower()
    assert "165" not in r["answer"]  # James's premium must not leak


def test_ws_chat_streams_tokens(api_client):
    with api_client.websocket_connect("/api/ws/chat") as ws:
        ws.send_json({"message": "Does auto insurance cover a rental car?"})
        meta = ws.receive_json()
        assert meta["type"] == "meta"
        tokens = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "token":
                tokens.append(msg["token"])
            elif msg["type"] == "done":
                assert "rental" in msg["answer"].lower()
                break
        assert len(tokens) > 3


def test_ws_voice_pipeline_and_metrics(api_client):
    from insurance_ai.providers.mock import MockSTT

    audio = MockSTT.encode("Does auto insurance cover a rental car")
    with api_client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode()})
        ws.send_json({"type": "end"})
        got_transcript = got_audio = got_metrics = got_done = False
        while True:
            msg = ws.receive_json()
            t = msg["type"]
            if t == "transcript":
                got_transcript = "rental" in msg["text"].lower()
            elif t == "audio":
                got_audio = len(base64.b64decode(msg["data"])) > 40  # valid WAV bytes
            elif t == "metrics":
                got_metrics = "speech_end_to_first_audio_ms" in msg
            elif t == "done":
                got_done = True
                break
        assert got_transcript and got_audio and got_metrics and got_done


def test_admin_endpoints(api_client):
    # generate a conversation with an escalation
    api_client.post("/api/chat", json={"message": "I want to speak to a human, this is unacceptable"})
    assert api_client.get("/api/admin/demo-customers").json()["synthetic"] is True
    convs = api_client.get("/api/admin/conversations").json()["conversations"]
    assert len(convs) >= 1
    detail = api_client.get(f"/api/admin/conversations/{convs[0]['id']}").json()
    assert "transcript" in detail
    tickets = api_client.get("/api/admin/tickets").json()["tickets"]
    assert any(t["ticket_number"].startswith("SUPPORT-") for t in tickets)


def test_payments_config_is_test_mode(api_client):
    cfg = api_client.get("/api/payments/config").json()
    assert cfg["test_mode"] is True
    assert cfg["provider"] in ("mock", "stripe")


def test_telephony_status_gracefully_disabled(api_client):
    st = api_client.get("/api/telephony/status").json()
    assert st["enabled"] is False  # no creds in test → web still works
