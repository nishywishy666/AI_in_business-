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
    # plan 0012: "Do you do delivery?" is answered from a fact now, so one fewer gap than the mockup's day
    assert k["kpiContainment"] == "70%" and k["kpiContainmentMeta"] == "7 of 10 calls, no callback requested"
    assert d["overview"]["chart"]["labels"][-1] == "Today" and sum(d["overview"]["chart"]["values"]) == len(d["calls"]) == 26
    # the 7D/30D toggle reads both series out of one bootstrap; 30d covers at least what 7d does
    assert d["overview"]["chart30"]["days"] == 30 and len(d["overview"]["chart30"]["values"]) == 30
    assert sum(d["overview"]["chart30"]["values"]) >= sum(d["overview"]["chart"]["values"])
    assert {r["label"]: r["count"] for r in d["overview"]["routeMix"]["legend"]} == {"Booking": 3, "Question": 5, "Callback": 1, "Chitchat": 1}
    outcomes = [c["outcome"] for c in d["calls"] if c["startedAt"] >= d["period"]["start"]]
    assert outcomes.count("Booked") == 3 and outcomes.count("Couldn't answer") == 2 and outcomes.count("Callback logged") == 1
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


def test_the_bootstrap_does_not_re_read_the_marketing_tree_every_poll(tmp_path):
    """The dashboard polls, so anything the bootstrap reads live is read all day. A brief's 8 posts
    plus the usage snapshot came to ~30 reads a poll — ~43,000 a day against a 50,000 free tier."""
    app, ctx, _ = dashboard_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/api/jobs/marketing-scan").status_code == 200
        hub = ctx.marketing
        hub.forget()
        first = c.get("/api/dashboard/bootstrap").json()["trends"]
        calls = {"n": 0}
        build = hub._trends

        def counted(**kwargs):
            calls["n"] += 1
            return build(**kwargs)

        hub._trends = counted
        for _ in range(5):
            c.get("/api/dashboard/bootstrap")
        assert calls["n"] == 0, "five polls, no rebuild"
        assert c.get("/api/dashboard/bootstrap").json()["trends"]["scanId"] == first["scanId"]

        # the things that change it say so
        hub.forget("trends")
        c.get("/api/dashboard/bootstrap")
        assert calls["n"] == 1


def test_bootstrap_says_which_free_model_is_answering(client):
    """The line above both chats. Offline it still names the tier and never raises — a chat that
    works while this is unavailable must not be blocked by it."""
    ai = client.get("/api/dashboard/bootstrap").json()["ai"]
    assert ai["provider"] == "Gemini free tier" and ai["paused"] in (True, False)
    assert set(ai) >= {"model", "modelId", "usedToday", "capToday", "credits", "resetsIn", "offline"}
    assert client.get("/api/dashboard/ai").json()["provider"] == ai["provider"]


def test_bootstrap_carries_notifications_that_name_a_screen(client):
    """The header bell is built from the same payload the page already has: every row is something
    waiting on Tony and says which screen it lives on, so clicking one can navigate."""
    notes = client.get("/api/dashboard/bootstrap").json()["notifications"]
    assert notes and all({"id", "tone", "screen", "title", "sub"} <= set(n) for n in notes)
    assert all(n["screen"] in {"overview", "calls", "marketing", "callbacks", "analytics"} for n in notes)
    assert {n["id"] for n in notes} & {"callbacks", "gaps", "clear"}


def test_save_sticks_even_when_every_free_model_is_paused(tmp_path):
    """A save is a bookmark, not an AI call. With the free Gemini ladder spent, the script cannot be
    written — but the save itself must still stick, or the UI rolls it back and the trend the owner
    just saved looks deleted."""
    from marketing_radar.agent import FakeGeminiTransport, RateLimited

    app, ctx, _ = dashboard_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/api/jobs/marketing-scan").status_code == 200
        post_id = c.get("/api/dashboard/trends").json()["items"][0]["id"]
        deps = ctx.marketing.deps()
        deps.gemini.transport = FakeGeminiTransport(
            scripts={model: [RateLimited("429")] for rung in deps.settings.ladder for model in rung.ids})
        body = c.post(f"/api/dashboard/trends/{post_id}/save").json()
        assert body["saved"] is True and body["script"] is None and body["note"]
        after = c.get("/api/dashboard/trends").json()
        assert post_id in after["savedIds"] and after["savedCount"] >= 1


def test_refresh_trends_rereads_the_brief_now(tmp_path):
    """The "Refresh now" button: never 500s on an empty state, and picks up a brief that landed after
    the once-a-day local cache was written — the case where the UI would otherwise show nothing.
    Own app: the module client has already run a scan by the time this test runs."""
    app, _, _ = dashboard_app(tmp_path)
    with TestClient(app) as c:
        empty = c.post("/api/dashboard/trends/refresh")
        assert empty.status_code == 200 and empty.json()["empty"] is True and empty.json()["note"]
        assert c.post("/api/jobs/marketing-scan").status_code == 200
        fresh = c.post("/api/dashboard/trends/refresh").json()
        assert not fresh["empty"] and fresh["items"] and fresh["scanId"]


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
