"""The JSON API the injected bridge calls, end to end over a seeded local run and the offline
marketing agent (fixtures + deterministic Gemini fake). No network, no keys."""
import pytest
from fastapi.testclient import TestClient

from tests.dashboard_helpers import BUSINESS_ID, dashboard_app

TEMPLATE_FIELDS = {"callsData": ("id", "time", "duration", "route", "outcome", "confidence", "cost", "transcript"),
                   "callbacksData": ("id", "name", "number", "question", "time", "priority", "status"),
                   "trendsData": ("id", "niche", "platform", "score", "title", "why"),
                   "unansweredDefs": ("id", "text", "evidence", "lastAsked"),
                   "menuRows": ("name", "ingredients", "price"), "packetFields": ("label", "value")}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app, ctx, _ = dashboard_app(tmp_path_factory.mktemp("dash"))
    with TestClient(app) as c:
        c.ctx = ctx
        yield c


def test_page_and_static_assets_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "dashboard bridge" in page.text and "{{ kpiCalls }}" in page.text
    assert client.get("/support.js").status_code == 200
    assert client.get("/_ds/nocturne-3df42377-c4f5-43bf-8ccd-724d903ef744/styles.css").status_code == 200
    assert client.get("/assets/uncle-tony-mascot.svg").status_code == 200
    assert client.get("/healthz").json()["business_id"] == BUSINESS_ID


def test_bootstrap_matches_the_template_contract(client):
    d = client.get("/api/dashboard/bootstrap").json()
    lists = {"callsData": d["calls"], "callbacksData": d["callbacks"], "trendsData": d["trends"]["items"],
             "unansweredDefs": d["gaps"], "menuRows": d["setup"]["menuRows"], "packetFields": d["setup"]["packetFields"]}
    for name, fields in TEMPLATE_FIELDS.items():
        rows = lists[name]
        if name == "trendsData":
            continue  # no scan yet → empty; checked after the scan job below
        assert rows, name
        for field in fields:
            assert field in rows[0], f"{name}.{field}"
    assert d["business"] == {"id": BUSINESS_ID, "name": "Uncle Tony", "timezone": "Australia/Melbourne", "source": "local", "demo": True}
    k = d["overview"]["kpis"]
    assert k["kpiCalls"] == "10" and k["kpiBookings"] == "3" and k["kpiCovers"] == "12"
    assert k["kpiContainment"] == "60%" and k["kpiContainmentMeta"] == "6 of 10 calls, no callback requested"
    assert d["overview"]["chart"]["labels"][-1] == "Today" and sum(d["overview"]["chart"]["values"]) == len(d["calls"]) == 26
    assert {r["label"]: r["count"] for r in d["overview"]["routeMix"]["legend"]} == {"Booking": 3, "Question": 5, "Callback": 1, "Chitchat": 1}
    outcomes = [c["outcome"] for c in d["calls"] if c["startedAt"] >= d["period"]["start"]]
    assert outcomes.count("Booked") == 3 and outcomes.count("Couldn't answer") == 3 and outcomes.count("Callback logged") == 1
    assert d["analytics"]["dataSourceLabel"] == "Demo data" and d["analytics"]["freshness"]["staleAfterMinutes"] == 60
    assert d["setup"]["ownerName"] == "Tony Marino" and d["setup"]["menuCountLabel"] == "Menu · 5 items"
    assert d["setup"]["packetFields"][0] == {"label": "Hours", "value": "7:30am to 2:30pm Monday to Friday, and 8am to 3pm on Saturday. Closed Sunday."}
    assert d["usage"]["minutesIncluded"] == 1000 and d["usage"]["pct"] >= 0
    assert d["trends"]["empty"] is True and d["trends"]["greeting"]


def test_call_rows_carry_transcripts_metrics_and_summaries(client):
    calls = client.get("/api/dashboard/calls?period=today").json()["calls"]
    booked = next(c for c in calls if c["outcome"] == "Booked")
    assert booked["route"] == "Booking" and booked["transcript"][0]["speaker"] == "Caller" and booked["transcript"][1]["speaker"] == "Receptionist"
    assert booked["transcript"][0]["ts"] == "0:00" or ":" in booked["transcript"][0]["ts"]
    assert {m["label"] for m in booked["metrics"]} >= {"Handle time", "Answer latency", "Task grade", "Handoff", "Cost"}
    assert "Caller asked" in booked["summaryText"] and booked["time"].endswith(("am", "pm"))
    gap = next(c for c in calls if c["outcome"] == "Couldn't answer")
    assert gap["hasGap"] and gap["taskGrade"] == "failure"
    one = client.get(f"/api/dashboard/calls/{booked['id']}").json()
    assert one["id"] == booked["id"] and client.get("/api/dashboard/calls/nope").status_code == 404


def test_analytics_periods_and_tiles(client):
    week = client.get("/api/dashboard/analytics?period=week").json()
    assert week["period"]["key"] == "week" and len(week["voiceOpsBandTiles"]) == 8 and len(week["impactTiles"]) == 4
    labels = {t["label"]: t for t in week["voiceOpsBandTiles"]}
    assert labels["Conversation coherence"]["value"] == "No data" and labels["Conversation coherence"]["band"] == "No data"
    assert labels["Containment"]["value"] == week["impactTiles"][2]["headline"]  # same definition on both tabs
    assert labels["Planned vs forced"]["note"].endswith("unclassified of 26 calls")
    assert labels["Task success"]["note"].endswith("unknown excluded")
    assert week["handoffReasonBars"][0]["count"] >= 1 and week["oldestCallbackWait"] != "None waiting"
    assert client.get("/api/dashboard/analytics?period=custom").json()["period"]["key"] == "custom"


def test_callback_status_gap_reviews_and_settings_persist(client):
    d = client.get("/api/dashboard/bootstrap").json()
    cb = d["callbacks"][0]
    assert client.post(f"/api/dashboard/callbacks/{cb['id']}/status", json={"status": "done"}).json()["status"] == "done"
    assert client.post("/api/dashboard/callbacks/nope/status", json={"status": "done"}).status_code == 404
    assert client.post(f"/api/dashboard/callbacks/{cb['id']}/status", json={"status": "weird"}).status_code == 400
    gap = d["gaps"][0]
    r = client.post(f"/api/dashboard/gaps/{gap['id']}/review", json={"status": "approved", "answer": "Yes", "question": gap["text"]})
    assert r.status_code == 200
    assert client.post("/api/dashboard/settings", json={"notifPrefs": {"callbacks": False, "digest": True, "trends": True}}).status_code == 200
    assert client.post("/api/dashboard/settings", json={}).status_code == 400
    after = client.get("/api/dashboard/bootstrap").json()
    assert next(c for c in after["callbacks"] if c["id"] == cb["id"])["status"] == "done"
    assert next(g for g in after["gaps"] if g["id"] == gap["id"])["review"] == "approved"
    assert after["settings"]["notifPrefs"]["callbacks"] is False
    assert after["analytics"]["gaps"][0]["review"] is None  # unreviewed first


def test_marketing_flow_through_the_dashboard(client):
    assert client.get("/api/marketing/brief").status_code == 404  # empty state before the first scan
    job = client.post("/api/jobs/marketing-scan")  # local sink + no CRON_SECRET → allowed
    assert job.status_code == 200 and job.json()["scan_id"] and job.json()["credits_spent"] == 3
    trends = client.get("/api/dashboard/trends").json()
    assert not trends["empty"] and trends["items"] and trends["items"][0]["score"] == 99
    assert {t["platform"] for t in trends["items"]} <= {"TikTok", "Instagram", "YouTube Shorts", "Facebook", "Reddit"}
    assert any(t["niche"] for t in trends["items"]) and any(not t["niche"] for t in trends["items"])
    post_id = trends["items"][0]["id"]
    liked = client.post(f"/api/marketing/posts/{post_id}/like").json()
    assert len(liked["angles"]) == 3 and liked["breakdown"]
    saved = client.post(f"/api/dashboard/trends/{post_id}/save").json()
    assert saved["saved"] and saved["script"]["status"] == "saved"
    assert client.post("/api/dashboard/trends/nope/save").status_code == 404
    again = client.get("/api/dashboard/trends").json()
    assert post_id in again["likedIds"] and post_id in again["savedIds"] and again["savedCount"] == 1
    chat = client.post("/api/marketing/chat", json={"message": "what should I post tomorrow?", "thread_id": "ui"}).json()
    assert chat["scan_id"] == job.json()["scan_id"] and chat["reply"]
    assert client.get("/api/marketing/stats").json()["scrapecreators"]["remaining"] <= 97  # scan + like transcript credit
    boot = client.get("/api/dashboard/bootstrap").json()
    assert boot["overview"]["quickActions"][2]["label"] == "Top trend this week"


def test_overlord_answers_from_the_records_without_a_model(client):
    r = client.post("/api/overlord/ask", json={"question": "What's trending this week?"}).json()
    assert r["answer"].startswith("From scan ") and r["model"] is None and r["grounded_on"]["scan_id"]
    r = client.post("/api/overlord/ask", json={"question": "How many bookings did the AI take this week?"}).json()
    assert "bookings" in r["answer"] and "covers" in r["answer"]
    r = client.post("/api/overlord/ask", json={"question": "Who is waiting on a callback?"}).json()
    assert "callback" in r["answer"].lower()
    assert client.post("/api/overlord/ask", json={"question": ""}).status_code == 400


def test_jobs_require_the_cron_secret_when_configured(client):
    client.ctx.cron_secret = "s3cret"
    try:
        assert client.get("/api/jobs/marketing-expire").status_code == 401
        ok = client.get("/api/jobs/marketing-expire", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200 and ok.json()["job"] == "marketing-expire"
        assert client.get("/api/jobs/nope", headers={"Authorization": "Bearer s3cret"}).status_code == 404
        assert client.get("/api/jobs/calendar-retry", headers={"Authorization": "Bearer s3cret"}).json()["synced"] == 0
    finally:
        client.ctx.cron_secret = None
