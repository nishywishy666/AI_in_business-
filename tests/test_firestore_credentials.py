import base64
import json

from marketing_radar.db.firestore_backend import _load_credentials


class _FakeServiceAccount:
    class Credentials:
        @staticmethod
        def from_service_account_file(path):
            return ("file", path)

        @staticmethod
        def from_service_account_info(info):
            return ("info", info)


INFO = {"type": "service_account", "project_id": "demo"}


def test_path_is_read_as_a_file(tmp_path):
    path = tmp_path / "sa.json"
    path.write_text(json.dumps(INFO))
    assert _load_credentials(_FakeServiceAccount, str(path)) == ("file", str(path))


def test_raw_json_is_accepted():
    assert _load_credentials(_FakeServiceAccount, json.dumps(INFO)) == ("info", INFO)


def test_base64_json_is_accepted():
    encoded = base64.b64encode(json.dumps(INFO).encode()).decode()
    assert _load_credentials(_FakeServiceAccount, encoded) == ("info", INFO)
