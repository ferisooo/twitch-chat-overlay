import json
from urllib.error import HTTPError

from overlay.credstore import CredStore
from overlay.sources import twitch_api, twitch_auth
from overlay.sources.twitch_auth import TwitchAccount


def test_credstore_file_fallback_roundtrip(tmp_path):
    store = CredStore(fallback_dir=tmp_path)
    assert store.load() == {}
    store.save({"access_token": "abc"})
    assert CredStore(fallback_dir=tmp_path).load() == {"access_token": "abc"}
    store.clear()
    assert store.load() == {}


def test_imports_old_token_and_validates(tmp_path, monkeypatch):
    legacy = tmp_path / "secrets.json"
    legacy.write_text(json.dumps({"twitch_oauth": "oauth:" + "a" * 30}), encoding="utf-8")
    store = CredStore(fallback_dir=tmp_path / "new")
    events = []
    account = TwitchAccount(events.append, store, legacy)
    assert account.logged_in
    assert account.login == ""

    monkeypatch.setattr(
        twitch_api, "validate",
        lambda token: {"login": "Bob", "user_id": "7", "client_id": "cid", "scopes": ["chat:read", "chat:edit"]},
    )
    assert account.fresh_token() == "a" * 30
    assert account.login == "bob"
    assert account.client_id == "cid"
    assert store.load()["login"] == "bob"
    assert legacy.read_text(encoding="utf-8").count("oauth:") == 1  # old file left alone


def test_dead_token_logs_out(tmp_path, monkeypatch):
    store = CredStore(fallback_dir=tmp_path)
    store.save({"access_token": "x" * 30, "kind": "paste"})
    events = []
    account = TwitchAccount(events.append, store)

    def dead(token):
        raise HTTPError("u", 401, "nope", None, None)

    monkeypatch.setattr(twitch_api, "validate", dead)
    assert account.fresh_token(force=True) == ""
    assert not account.logged_in
    assert events == [{"src": "account", "kind": "expired"}]
    assert store.load() == {}


def test_device_token_is_renewed(tmp_path, monkeypatch):
    store = CredStore(fallback_dir=tmp_path)
    store.save({"access_token": "old" * 10, "refresh_token": "r1", "app_client_id": "app", "kind": "device"})
    account = TwitchAccount(lambda e: None, store)
    calls = []

    def validate(token):
        calls.append(token)
        if token == "old" * 10:
            raise HTTPError("u", 401, "expired", None, None)
        return {"login": "bob", "user_id": "1", "client_id": "app", "scopes": []}

    monkeypatch.setattr(twitch_api, "validate", validate)
    monkeypatch.setattr(twitch_api, "refresh", lambda cid, rt: {"access_token": "new" * 10, "refresh_token": "r2"})
    assert account.fresh_token(force=True) == "new" * 10
    assert store.load()["refresh_token"] == "r2"
    assert twitch_auth.TwitchAccount  # module import sanity
