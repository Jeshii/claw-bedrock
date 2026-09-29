"""Tests for the Bedrock auth settings card: GET/PUT /api/settings/bedrock.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=<fernet-key> python3 -m pytest tests/ -q
"""

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


def _clean_import_modules():
    prefixes = ("db", "management_app", "encryption_utils", "settings_resolver")
    for k in [k for k in __import__("sys").modules if k.startswith(prefixes)]:
        del __import__("sys").modules[k]


@pytest.fixture
def test_env(monkeypatch, tmp_path):
    """Isolated CONFIG_DIR, clean modules, and a refresher that never calls AWS."""
    for name in (
        "AWS_REGION",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    _clean_import_modules()

    import management_app as mgmt
    import token_refresher as tr

    class FakeRefresher:
        """Stands in for the module-level singleton.

        The real one runs boto3 in __init__, which is not a unit test. Only the
        surface configure() and auth_status_payload() touch is needed.
        """

        def __init__(self):
            self._region = "ap-northeast-1"
            self._profile = "bedrock-openai20b"
            self._access_key_id = ""
            self._secret_access_key = ""
            self.configured = []

        def configure(
            self, region=None, profile=None, access_key_id=None, secret_access_key=None
        ):
            self.configured.append(
                {
                    "region": region,
                    "profile": profile,
                    "access_key_id": access_key_id,
                    "secret_access_key": secret_access_key,
                }
            )
            if region is not None:
                self._region = region
            if profile is not None:
                self._profile = profile
            self._access_key_id = access_key_id or ""
            self._secret_access_key = secret_access_key or ""
            return True

        def auth_status_payload(self):
            return {
                "needs_login": False,
                "auth_url": None,
                "awaiting_code": False,
                "auth_error": None,
                "profile": self._profile,
                "region": self._region,
                "static_keys": {
                    "configured": bool(self._access_key_id),
                    "source": "config" if self._access_key_id else None,
                },
                "token": {
                    "present": False,
                    "age_seconds": None,
                    "ttl_seconds": 2700,
                    "expires_in": None,
                    "stale": True,
                },
            }

    refresher = FakeRefresher()
    monkeypatch.setattr(tr, "token_refresher", refresher)
    monkeypatch.setattr(mgmt, "_reload_litellm_config", lambda: True)
    monkeypatch.setattr(mgmt, "merge_configs", lambda: None)

    client = TestClient(mgmt.app)
    yield client, refresher, mgmt.db
    _clean_import_modules()


class TestGet:
    def test_reports_resolved_values_with_provenance(self, test_env):
        client, _r, _db = test_env
        data = client.get("/api/settings/bedrock").json()
        assert data["region"]["source"] == "default"
        assert data["profile"]["source"] == "default"

    def test_never_returns_credential_material(self, test_env):
        client, _r, db = test_env
        db.set_secret_setting(db.BEDROCK_ACCESS_KEY_SETTING, "AKIACANARY")
        db.set_secret_setting(db.BEDROCK_SECRET_KEY_SETTING, "wJalrCANARY")
        blob = json.dumps(client.get("/api/settings/bedrock").json())
        assert "AKIACANARY" not in blob
        assert "wJalrCANARY" not in blob

    def test_static_keys_reported_as_configured_boolean(self, test_env):
        client, _r, db = test_env
        db.set_secret_setting(db.BEDROCK_ACCESS_KEY_SETTING, "AKIAEXAMPLE")
        db.set_secret_setting(db.BEDROCK_SECRET_KEY_SETTING, "wJalrEXAMPLE")
        keys = client.get("/api/settings/bedrock").json()["static_keys"]
        assert keys == {"configured": True, "state": "ok"}

    def test_nothing_stored_is_absent_not_incomplete(self, test_env):
        """Not configured is the normal case, and must not read as broken.

        Regression: an earlier version reported `incomplete` whenever the two
        states differed, which included the both-absent case, so a fresh install
        looked half-configured.
        """
        client, _r, db = test_env
        assert db.get_secret_setting(db.BEDROCK_ACCESS_KEY_SETTING) == (None, "absent")
        data = client.get("/api/settings/bedrock").json()
        assert data["static_keys"] == {"configured": False, "state": "absent"}
        assert data["warnings"] == []

    @pytest.mark.parametrize(
        ("present", "expected_state", "expected_warning"),
        [
            (["access"], "incomplete", "incomplete_static_key_pair"),
            (["secret"], "incomplete", "incomplete_static_key_pair"),
            (["access", "secret"], "ok", None),
        ],
    )
    def test_key_pair_state_matrix(
        self, test_env, present, expected_state, expected_warning
    ):
        client, _r, db = test_env
        if "access" in present:
            db.set_secret_setting(db.BEDROCK_ACCESS_KEY_SETTING, "AKIAEXAMPLE")
        if "secret" in present:
            db.set_secret_setting(db.BEDROCK_SECRET_KEY_SETTING, "wJalrEXAMPLE")

        data = client.get("/api/settings/bedrock").json()
        assert data["static_keys"]["state"] == expected_state
        if expected_warning:
            assert expected_warning in data["warnings"]
        else:
            assert "incomplete_static_key_pair" not in data["warnings"]


class TestHealthEndpoints:
    """Liveliness and health answer different questions, and the split matters.

    `/api/health/litellm` proxies LiteLLM's /health, which makes a real call to
    every model unless background_health_checks is set — it does not here. So it
    is coupled to provider auth, and with an empty model_list it passes
    vacuously, which is how it read as a green smoke test in CI while proving
    nothing about serving requests. /health/liveliness only reports whether the
    process is up.
    """

    def _record_gets(self, monkeypatch, mgmt, status_code=200, error=None):
        urls = []

        def fake_get(url, timeout=5):
            urls.append(url)
            if error:
                raise error

            class Resp:
                pass

            resp = Resp()
            resp.status_code = status_code
            return resp

        monkeypatch.setattr(mgmt.requests, "get", fake_get)
        return urls

    def test_liveliness_hits_the_liveliness_path(self, test_env, monkeypatch):
        client, _r, _db = test_env
        import management_app as mgmt

        urls = self._record_gets(monkeypatch, mgmt)

        data = client.get("/api/health/litellm/liveliness").json()
        assert data == {"status": "ok", "litellm_status": 200}
        assert urls == ["http://localhost:4000/health/liveliness"]

    def test_health_still_hits_the_active_check(self, test_env, monkeypatch):
        """Deliberately unchanged: it is what goes red when Bedrock auth fails."""
        client, _r, _db = test_env
        import management_app as mgmt

        urls = self._record_gets(monkeypatch, mgmt)

        data = client.get("/api/health/litellm").json()
        assert data == {"status": "ok", "litellm_status": 200}
        assert urls == ["http://localhost:4000/health"]

    def test_probe_failure_reports_error_not_ok(self, test_env, monkeypatch):
        """Never report ok unless it actually was — the original sin here."""
        client, _r, _db = test_env
        import management_app as mgmt

        self._record_gets(monkeypatch, mgmt, error=OSError("connection refused"))

        for path in ("/api/health/litellm", "/api/health/litellm/liveliness"):
            data = client.get(path).json()
            assert data["status"] == "error", path
            assert "litellm_status" not in data, path


class TestPut:
    def test_saves_region_to_config_store(self, test_env):
        client, _r, db = test_env
        resp = client.put("/api/settings/bedrock", json={"region": "eu-west-1"})
        assert resp.status_code == 200
        assert db.get_setting(db.BEDROCK_REGION_SETTING) == "eu-west-1"

    def test_saves_profile_to_config_store(self, test_env):
        client, _r, db = test_env
        client.put("/api/settings/bedrock", json={"profile": "my-profile"})
        assert db.get_setting(db.BEDROCK_PROFILE_SETTING) == "my-profile"

    def test_env_still_wins_after_save(self, test_env, monkeypatch):
        """The GUI yields to the environment, and says so in the response."""
        client, _r, _db = test_env
        monkeypatch.setenv("AWS_REGION", "ap-south-1")
        data = client.put("/api/settings/bedrock", json={"region": "eu-west-1"}).json()
        assert data["bedrock"]["region"]["value"] == "ap-south-1"
        assert data["bedrock"]["region"]["source"] == "env"
        assert "config" in data["bedrock"]["region"]["shadowed"]

    def test_lowercases_region_with_warning(self, test_env):
        """Soft correction: AWS regions are lowercase, and we say we did it."""
        client, _r, db = test_env
        data = client.put("/api/settings/bedrock", json={"region": "EU-West-1"}).json()
        assert db.get_setting(db.BEDROCK_REGION_SETTING) == "eu-west-1"
        assert any("lowercase" in w for w in data["warnings"])

    def test_rejects_unknown_fields(self, test_env):
        client, _r, _db = test_env
        resp = client.put("/api/settings/bedrock", json={"nonsense": 1})
        assert resp.status_code == 400
        assert "nonsense" in resp.json()["detail"]

    def test_configures_refresher_without_restart(self, test_env):
        client, refresher, _db = test_env
        client.put("/api/settings/bedrock", json={"region": "eu-west-1"})
        assert refresher.configured[-1]["region"] == "eu-west-1"

    def test_reports_token_refresh_failure_distinctly(self, test_env, monkeypatch):
        """A saved-but-unusable setting must not report as success."""
        client, refresher, _db = test_env
        monkeypatch.setattr(type(refresher), "configure", lambda self, **kw: False)
        data = client.put("/api/settings/bedrock", json={"region": "bad-1"}).json()
        assert data["saved"] is True
        assert data["token_refreshed"] is False

    def test_region_change_regenerates_config(self, test_env):
        """Region is baked into the config as aws_region_name, so it must merge.

        The fixture stubs merge_configs at import time, so this asserts the
        effect that survives the stub: the stored region is the one the config
        generator reads.
        """
        client, _r, db = test_env
        client.put("/api/settings/bedrock", json={"region": "eu-west-1"})
        assert db._resolved_bedrock_region() == "eu-west-1"


class TestStaticKeys:
    def test_keys_stored_encrypted(self, test_env):
        client, _r, db = test_env
        client.put(
            "/api/settings/bedrock",
            json={"access_key_id": "AKIAREAL", "secret_access_key": "wJalrREAL"},
        )
        stored = db.get_setting(db.BEDROCK_ACCESS_KEY_SETTING)
        assert stored != "AKIAREAL"
        assert "AKIAREAL" not in json.dumps(db.get_settings())

    def test_keys_round_trip_through_strict_decrypt(self, test_env):
        client, _r, db = test_env
        client.put(
            "/api/settings/bedrock",
            json={"access_key_id": "AKIAREAL", "secret_access_key": "wJalrREAL"},
        )
        value, state = db.get_secret_setting(db.BEDROCK_ACCESS_KEY_SETTING)
        assert (value, state) == ("AKIAREAL", "ok")

    def test_omitted_keys_are_retained(self, test_env):
        client, _r, db = test_env
        client.put(
            "/api/settings/bedrock",
            json={"access_key_id": "AKIAFIRST", "secret_access_key": "wJalrFIRST"},
        )
        client.put("/api/settings/bedrock", json={"region": "eu-west-1"})
        value, _state = db.get_secret_setting(db.BEDROCK_ACCESS_KEY_SETTING)
        assert value == "AKIAFIRST"

    def test_empty_keys_are_retained(self, test_env):
        """Blank means "unchanged", matching the provider api_key convention."""
        client, _r, db = test_env
        client.put(
            "/api/settings/bedrock",
            json={"access_key_id": "AKIAFIRST", "secret_access_key": "wJalrFIRST"},
        )
        client.put(
            "/api/settings/bedrock",
            json={"access_key_id": "", "secret_access_key": ""},
        )
        value, _state = db.get_secret_setting(db.BEDROCK_ACCESS_KEY_SETTING)
        assert value == "AKIAFIRST"

    def test_clear_removes_both_halves(self, test_env):
        client, _r, db = test_env
        client.put(
            "/api/settings/bedrock",
            json={"access_key_id": "AKIAFIRST", "secret_access_key": "wJalrFIRST"},
        )
        client.put("/api/settings/bedrock", json={"clear_static_keys": True})
        assert db.get_secret_setting(db.BEDROCK_ACCESS_KEY_SETTING) == (
            None,
            "absent",
        )
        assert db.get_secret_setting(db.BEDROCK_SECRET_KEY_SETTING) == (
            None,
            "absent",
        )

    def test_refresher_receives_stored_keys(self, test_env):
        client, refresher, _db = test_env
        client.put(
            "/api/settings/bedrock",
            json={"access_key_id": "AKIAREAL", "secret_access_key": "wJalrREAL"},
        )
        call = refresher.configured[-1]
        assert call["access_key_id"] == "AKIAREAL"
        assert call["secret_access_key"] == "wJalrREAL"

    def test_response_never_echoes_submitted_keys(self, test_env):
        client, _r, _db = test_env
        blob = client.put(
            "/api/settings/bedrock",
            json={"access_key_id": "AKIACANARY", "secret_access_key": "wJalrCANARY"},
        ).text
        assert "AKIACANARY" not in blob
        assert "wJalrCANARY" not in blob


class TestUndecryptableCredentials:
    """BUGS.md #8: a wrong ENCRYPTION_KEY must not read as a working credential."""

    def test_reported_rather_than_used(self, test_env):
        client, _r, db = test_env
        # Not a Fernet token, so decrypt_data_strict raises InvalidToken.
        db.set_setting(db.BEDROCK_ACCESS_KEY_SETTING, "plaintext-not-encrypted")
        data = client.get("/api/settings/bedrock").json()
        assert data["static_keys"]["configured"] is False
        assert "stored_credentials_undecryptable" in data["warnings"]

    def test_absent_is_distinct_from_undecryptable(self, test_env):
        _client, _r, db = test_env
        assert db.get_secret_setting("never_set") == (None, "absent")
        db.set_setting("bad", "plaintext-not-encrypted")
        assert db.get_secret_setting("bad") == (None, "undecryptable")


class TestSecretSettingStorage:
    def test_empty_value_stores_as_absent(self, test_env):
        _client, _r, db = test_env
        db.set_secret_setting("some_key", "")
        assert db.get_secret_setting("some_key") == (None, "absent")

    def test_clear_removes_the_row(self, test_env):
        _client, _r, db = test_env
        db.set_secret_setting("some_key", "value")
        db.clear_secret_setting("some_key")
        assert db.get_secret_setting("some_key") == (None, "absent")

    def test_backups_carry_ciphertext_not_plaintext(self, test_env):
        """export_backup must not turn an encrypted setting into a readable one."""
        client, _r, db = test_env
        client.put(
            "/api/settings/bedrock",
            json={"access_key_id": "AKIAREAL", "secret_access_key": "wJalrREAL"},
        )
        dump = json.dumps(db.export_backup())
        assert "wJalrREAL" not in dump
