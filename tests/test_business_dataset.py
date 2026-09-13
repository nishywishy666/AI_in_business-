"""The Uncle Tony dataset (data/business/uncle_tony) answers through the production lookups."""
import datetime as dt

from services.common.config import local_config
from services.voice.tools import answer_question, load_business_context

from tests.dashboard_helpers import BUSINESS_ID, DATASET, reader, windows


def test_dataset_loads_as_confirmed_business_context():
    r = reader()
    ctx = load_business_context(r, BUSINESS_ID, windows=windows())
    assert ctx.business_name == "Uncle Tony"
    assert {i.name for i in ctx.items} == {"The Uncle Tony", "The Wise Guy", "The Luca Brasi", "The Frank Fungini", "The OG"}
    assert {"hours", "address", "phone", "seating", "socials", "business_name"} <= ctx.fact_keys
    assert all(i.price_cents for i in ctx.items)


def test_lookups_answer_from_the_dataset():
    r = reader()
    ctx = load_business_context(r, BUSINESS_ID)
    price = answer_question("How much is the Uncle Tony?", ctx, r)
    assert price.source == "menu" and price.payload["price"] == "$19.00"
    hours = answer_question("What time do you open on Saturdays?", ctx, r)
    assert hours.source == "fact" and "8am to 3pm" in hours.payload["value"]
    seats = answer_question("How many people can you seat?", ctx, r)
    assert seats.source == "fact" and "28" in seats.payload["value"]
    gluten = answer_question("Do you have anything gluten-free?", ctx, r)
    assert gluten.source == "not_found" and gluten.reason == "allergen_unknown"
    delivery = answer_question("Do you do delivery?", ctx, r)
    assert delivery.source == "not_found" and delivery.reason == "no_data"


def test_capacity_windows_come_from_the_mockup_hours():
    slots = reader().list_docs(f"businesses/{BUSINESS_ID}/capacitySlots")
    ids = {slot_id for slot_id, _ in slots}
    assert "2026-09-10T1230" in ids and "2026-09-12T0800" in ids  # Thursday lunch, Saturday open
    assert "2026-09-13T1200" not in ids  # Sunday closed
    assert all(doc["seatsTotal"] == 40 for _, doc in slots)


def test_local_config_boots_with_zero_keys(monkeypatch):
    config = local_config({})
    assert config.session_sink == "local" and config.business_id == "uncle_tony" and config.ws_token_secret
    assert local_config({"BUSINESS_ID": "biz_x"}).business_id == "biz_x"
    assert DATASET.exists() and dt.date.today()  # dataset present on disk
