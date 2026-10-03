"""Signing in with a password: the settings dialog trades it for a token, and only the token is kept."""

from __future__ import annotations

import socket

import pytest
from conftest import PASSWORD, USER_TOKEN, wait_until
from PySide6.QtWidgets import QDialog

from clara_app.api import SIGNED_OUT, ApiError, ClaraApi, login
from clara_app.config import Config
from clara_app.settings_dialog import SettingsDialog


def dialog_for(url: str, user: str = "Tester", password: str = PASSWORD, token: str = "") -> SettingsDialog:
    dialog = SettingsDialog(Config(url=url, token=token, user_id=user))
    dialog.password.setText(password)
    return dialog


class TestLogin:
    def test_the_server_gives_a_token_and_the_name_it_knows(self, server):
        url, state = server
        answer = login(url, "Tester", PASSWORD)
        assert answer["token"] == USER_TOKEN and answer["user"]["name"] == "tester"
        assert state.logins == [{"username": "Tester", "password": PASSWORD, "surface": "app", "device": state.logins[0]["device"]}]

    def test_a_wrong_password_says_so(self, server):
        url, _ = server
        with pytest.raises(ApiError, match="Wrong user name or password"):
            login(url, "tester", "nope")

    def test_an_unreachable_server_is_explained(self):
        with socket.socket() as sock:  # a port nobody listens on
            sock.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        with pytest.raises(ApiError, match="Cannot reach"):
            login(url, "tester", PASSWORD, timeout=5)


class TestSignedOut:
    def test_a_refused_sign_in_token_asks_for_the_password_again(self, server):
        url, state = server
        state.signed_out = True
        with pytest.raises(ApiError) as error:
            ClaraApi(Config(url=url, token=USER_TOKEN, user_id="tester")).conversations()
        assert str(error.value) == SIGNED_OUT

    def test_a_refused_client_token_keeps_the_server_s_own_words(self, server):
        url, _ = server
        with pytest.raises(ApiError, match="Missing or invalid token"):
            ClaraApi(Config(url=url, token="wrong", user_id="tester")).conversations()


class TestDialog:
    def test_saving_with_a_password_signs_in_and_keeps_only_the_token(self, qt, server):
        url, state = server
        dialog = dialog_for(url)
        assert dialog.save_button.isEnabled()  # a password is enough: the token comes from signing in
        dialog.accept()
        wait_until(lambda: dialog.result() == QDialog.DialogCode.Accepted)
        config = dialog.config()
        assert (config.token, config.user_id) == (USER_TOKEN, "tester")  # as the server spells it
        assert dialog.password.text() == "" and len(state.logins) == 1

    def test_a_wrong_password_stays_in_the_dialog_and_says_why(self, qt, server):
        url, _ = server
        dialog = dialog_for(url, password="not the password")
        dialog.accept()
        wait_until(lambda: "Wrong user name or password" in dialog.verdict.text())
        assert dialog.result() != QDialog.DialogCode.Accepted and not dialog.config().token
        assert dialog.save_button.isEnabled()  # the user can fix it

    def test_testing_the_connection_signs_in_first(self, qt, server):
        url, _ = server
        dialog = dialog_for(url)
        dialog.test.click()
        wait_until(lambda: dialog.verdict.text().startswith("Connected"))
        assert dialog.config().token == USER_TOKEN and dialog.password.text() == ""

    def test_without_a_token_or_a_password_it_cannot_be_saved(self, qt, server):
        url, _ = server
        dialog = dialog_for(url, password="")
        assert not dialog.save_button.isEnabled()
        dialog.password.setText("x")
        assert dialog.save_button.isEnabled()

    def test_a_saved_token_is_enough_to_save_again(self, qt, server):
        url, state = server
        dialog = dialog_for(url, password="", token=USER_TOKEN)
        dialog.accept()
        wait_until(lambda: dialog.result() == QDialog.DialogCode.Accepted)
        assert state.logins == []
