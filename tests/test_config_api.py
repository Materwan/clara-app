"""Settings on disk, and the HTTP client against the fake server."""

from __future__ import annotations

import socket

import pytest

from clara_app.api import ApiError, ClaraApi, new_conversation
from clara_app.config import Config, load, save, url_hint


class TestUrlHint:
    @pytest.mark.parametrize(
        "url", ["http://127.0.0.1:8765", "http://localhost:8765", "https://box.tail1234.ts.net", "https://nas:8765", ""]
    )
    def test_nothing_to_say(self, url):
        assert url_hint(url) == ""

    def test_a_tailscale_address_needs_https(self):
        assert "https://" in url_hint("http://box.tail1234.ts.net")

    def test_plain_http_to_another_machine_is_flagged(self):
        assert "unencrypted" in url_hint("http://192.168.1.20:8765")


class TestConfig:
    def test_what_is_saved_comes_back(self, tmp_path):
        path = tmp_path / "clara-app" / "config.json"
        save(Config("http://nas:8765", "tok", "erwan", "Erwan"), path)
        assert load(path, env={}) == Config("http://nas:8765", "tok", "erwan", "Erwan")

    def test_a_missing_or_broken_file_gives_defaults(self, tmp_path):
        assert load(tmp_path / "none.json", env={}).url == "http://127.0.0.1:8765"
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert not load(broken, env={}).token

    def test_the_environment_fills_what_the_file_leaves_empty(self, tmp_path):
        config = load(tmp_path / "none.json", env={"CLARA_URL": "http://box:1", "CLARA_TOKEN": " secret "})
        assert (config.url, config.token) == ("http://box:1", "secret")

    def test_the_file_wins_over_the_environment(self, tmp_path):
        path = tmp_path / "config.json"
        save(Config("http://file:1", "from-file", "me"), path)
        config = load(path, env={"CLARA_URL": "http://env:2", "CLARA_TOKEN": "from-env"})
        assert (config.url, config.token) == ("http://file:1", "from-file")

    def test_the_user_defaults_to_the_windows_account(self, tmp_path):
        assert load(tmp_path / "none.json", env={}).user_id

    def test_ready_needs_a_server_a_token_and_a_user(self):
        assert Config("http://x", "t", "u").ready
        assert not Config("http://x", "", "u").ready
        assert not Config("", "t", "u").ready
        assert not Config("http://x", "t", " ").ready

    def test_each_new_conversation_has_its_own_id(self):
        first, second = new_conversation("erwan"), new_conversation("erwan")
        assert first.startswith("app:erwan:") and first != second


class TestApi:
    def test_health_checks_the_server_and_the_token(self, config):
        assert ClaraApi(config).health()["model"] == "fake-model"

    def test_a_wrong_token_is_explained(self, config):
        config.token = "nope"
        with pytest.raises(ApiError, match="Missing or invalid token.*401"):
            ClaraApi(config).health()

    def test_an_unreachable_server(self, config):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config.url = f"http://127.0.0.1:{port}"
        with pytest.raises(ApiError, match="Cannot reach the Clara server"):
            ClaraApi(config).health()
        with pytest.raises(ApiError, match="Cannot reach"):
            ClaraApi(config).chat("hi", "app:tester:1")

    def test_chat_sends_who_speaks_and_streams_the_answer(self, config, server):
        _, state = server
        events = list(ClaraApi(config).chat("hello", "app:tester:1"))
        assert [e["type"] for e in events] == ["turn", "token", "token", "done"]
        [body] = state.chat_bodies
        assert (body["surface"], body["user_id"], body["user_name"]) == ("app", "tester", "Tess")
        assert (body["message"], body["conversation"]) == ("hello", "app:tester:1")
        assert "desktop app" in body["instructions"]

    def test_chat_refused_says_why(self, config):
        config.token = "nope"
        with pytest.raises(ApiError, match="401"):
            ClaraApi(config).chat("hi", "app:tester:1")

    def test_the_conversations_of_this_user(self, config, server):
        _, state = server
        state.add_conversation("app:tester:a", ("Tea timer", "Done."), updated_at="2026-10-01T10:00:00+00:00")
        state.add_conversation("app:tester:b", ("C pointers", "Addresses."), updated_at="2026-10-02T10:00:00+00:00")
        api = ClaraApi(config)
        assert [c["id"] for c in api.conversations()] == ["app:tester:b", "app:tester:a"]
        assert [c["id"] for c in api.conversations("  tea ")] == ["app:tester:a"]
        assert state.list_requests == [
            {"surface": "app", "user_id": "tester"}, {"surface": "app", "user_id": "tester", "q": "tea"}
        ]
        shown = api.messages("app:tester:a")
        assert [(m["role"], m["content"]) for m in shown["messages"]] == [("user", "Tea timer"), ("assistant", "Done.")]
        assert api.title("app:tester:a") == "Clara's title"
        api.update_conversation("app:tester:a", pinned=True)
        api.update_conversation("app:tester:a", title="Tea")
        assert state.patches == [
            ("app:tester:a", {"surface": "app", "user_id": "tester", "title": None, "pinned": True}),
            ("app:tester:a", {"surface": "app", "user_id": "tester", "title": "Tea", "pinned": None}),
        ]
        api.delete_conversation("app:tester:a")
        assert state.deleted == ["/v1/conversations/app:tester:a?surface=app&user_id=tester"]
        with pytest.raises(ApiError, match="No such conversation.*404"):
            api.messages("app:tester:a")

    def test_an_id_with_odd_characters_is_quoted_in_the_path(self, config, server):
        _, state = server
        state.add_conversation("app:Erwan M/x:1", ("hi", "hello"))
        assert ClaraApi(config).messages("app:Erwan M/x:1")["id"] == "app:Erwan M/x:1"

    def test_the_reminder_stream_gives_the_events(self, config, server):
        _, state = server
        state.reminders = [{"type": "reminder", "id": 1, "text": "Dentist"}]
        state.hold_reminders = False  # end the stream after its events so that the list is finite
        events = list(ClaraApi(config).reminders())
        assert [e["text"] for e in events if e["type"] == "reminder"] == ["Dentist"]
        assert [e["state"] for e in events if e["type"] == "server"] == ["running"]

    def test_the_stream_is_this_users(self, config, server):
        _, state = server
        state.hold_reminders = False
        list(ClaraApi(config).reminders())
        assert state.stream_paths == ["/v1/notifications/stream?surface=app&user_id=tester"]

    def test_notify_sends_a_notification(self, config, server):
        _, state = server
        assert ClaraApi(config).notify("Done", "Build", ["cli"])["targets"] == ["cli"]
        assert state.notifications == [
            {"surface": "app", "user_id": "tester", "text": "Done", "title": "Build", "targets": ["cli"]}
        ]

    def test_closing_a_stream_ends_it_quietly(self, config, server):
        _, state = server
        state.hold.set()
        stream = ClaraApi(config).chat("hi", "app:tester:1")
        iterator = iter(stream)
        assert next(iterator)["type"] == "turn"
        stream.close()
        rest = list(iterator)  # ends without an error: we closed it ourselves
        assert len(rest) <= 1 and all(e["type"] == "token" for e in rest)  # at most what was already read
