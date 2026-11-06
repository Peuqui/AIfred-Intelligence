"""Authentication mixin for AIfred state.

Handles login, registration, logout and cookie-based auto-login.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import reflex as rx


class AuthMixin(rx.State, mixin=True):
    """Mixin for all authentication-related state and logic."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        _session_initialized: bool
        available_sessions: list[dict[str, Any]]
        debug_messages: list[str]
        session_id: str

        def _clear_chat_internal(self, silent: bool = False): ...
        def _load_latest_session(self) -> bool: ...
        def add_debug(self, message: str) -> None: ...
        def new_session(self): ...
        def refresh_session_list(self): ...

    # ── State Variables ──────────────────────────────────────────────
    logged_in_user: str = ""  # Currently logged in username (empty = not logged in)
    login_dialog_open: bool = True  # Blocks UI until authenticated (closed by auto-login or manual login)
    login_mode: str = "login"  # "login" or "register"
    login_username: str = ""  # Input field for username
    login_password: str = ""  # Input field for password
    login_error: str = ""  # Error message to display

    # ── Setters ──────────────────────────────────────────────────────

    def set_login_username(self, value: str) -> None:
        """Set login username input (explicit setter for Reflex 0.9.0)."""
        self.login_username = value

    def set_login_password(self, value: str) -> None:
        """Set login password input (explicit setter for Reflex 0.9.0)."""
        self.login_password = value

    # ── Login Dialog ─────────────────────────────────────────────────

    def handle_login_key_down(self, key: str):
        """Handle key press in login dialog - Enter triggers submit."""
        if key == "Enter":
            if self.login_mode == "login":
                return type(self).do_login
            else:
                return type(self).do_register

    def handle_login_submit(self, form_data: dict):
        """Handle form submit - for browser password manager support."""
        if self.login_mode == "login":
            return type(self).do_login
        else:
            return type(self).do_register

    def open_login_dialog(self, mode: str = "login") -> None:
        """Open login dialog in specified mode."""
        self.login_mode = mode
        self.login_username = ""
        self.login_password = ""
        self.login_error = ""
        self.login_dialog_open = True

    def close_login_dialog(self) -> None:
        """Close login dialog and clear fields."""
        self.login_dialog_open = False
        self.login_username = ""
        self.login_password = ""
        self.login_error = ""

    # ── Login / Register / Logout ────────────────────────────────────

    def _post_auth_script(self):
        """Nach Login/Registrierung: Username- + Session-Cookie setzen und
        den Browser-Push-Bus-SSE-Stream starten (SSOT für beide Pfade)."""
        from ..lib.browser_storage import set_session_id_script, set_username_script
        combined_script = (
            set_username_script(self.logged_in_user)
            + "; "
            + set_session_id_script(self.session_id)
            + "; if(window.startBrowserStream) startBrowserStream('"
            + self.session_id
            + "');"
        )
        return rx.call_script(combined_script)

    def do_login(self):
        """Attempt to log in with entered credentials."""
        from ..lib.session_storage import account_exists, verify_account

        username = self.login_username.strip()
        password = self.login_password

        if not username or not password:
            self.login_error = "Bitte Username und Passwort eingeben"
            return

        if not account_exists(username):
            self.login_error = "Account nicht gefunden"
            return

        if not verify_account(username, password):
            self.login_error = "Falsches Passwort"
            return

        # Login successful
        self.logged_in_user = username.lower()
        self.close_login_dialog()
        self.refresh_session_list()

        # Load the most recently active session — see _load_latest_session().
        if self._load_latest_session():
            self.add_debug(f"✅ Logged in as: {self.logged_in_user}")
        else:
            self.add_debug(f"✅ Logged in as: {self.logged_in_user} (new)")

        return self._post_auth_script()

    def do_register(self):
        """Attempt to create new account."""
        from ..lib.session_storage import create_account, is_username_allowed

        username = self.login_username.strip()
        password = self.login_password

        if not username or not password:
            self.login_error = "Bitte Username und Passwort eingeben"
            return

        if not is_username_allowed(username):
            self.login_error = "Username nicht auf Whitelist"
            return

        if not create_account(username, password):
            self.login_error = "Account existiert bereits"
            return

        # Registration successful - auto login
        self.logged_in_user = username.lower()
        self.close_login_dialog()
        self.refresh_session_list()

        # New account always gets new session
        self.new_session()
        self.add_debug(f"✅ Account created: {self.logged_in_user}")

        return self._post_auth_script()

    def do_logout(self):
        """Log out current user."""
        from ..lib.browser_storage import clear_username_script
        from ._audio_player_mixin import discard_audio_runtime_state
        from ._tts_streaming_mixin import discard_tts_backend_state

        self.add_debug(f"👋 Logged out: {self.logged_in_user}")
        # Drop per-session runtime state (audio snapshot, TTS backend
        # tracking) for the session we're leaving — otherwise it
        # accumulates for the lifetime of the server process.
        prev_session = self.session_id
        discard_audio_runtime_state(prev_session)
        discard_tts_backend_state(prev_session)

        self.logged_in_user = ""
        self.available_sessions = []
        self.session_id = ""
        # silent=True: Session data is preserved on disk, we're just clearing UI state
        self._clear_chat_internal(silent=True)

        # Show login dialog again
        self.login_dialog_open = True

        # Clear cookie
        return rx.call_script(clear_username_script())

    # ── Cookie-based Auto-Login ──────────────────────────────────────

    def handle_username_loaded(self, cookie_value: str):
        """Callback nach Cookie-Read via rx.call_script().

        Der Cookie enthält ``username.<hmac>`` — die Signatur wird gegen das
        Server-Secret geprüft (lib/auth), bevor eingeloggt wird. Ein gefälschter
        oder unsignierter Cookie führt zum Login-Dialog statt zum Auto-Login.
        """
        # Guard: Nur einmal ausführen
        if self._session_initialized:
            print("⏭️ Session already initialized, skipping")
            return
        self._session_initialized = True

        from ..lib.auth import verify_signed_username
        from ..lib.session_storage import account_exists

        username = verify_signed_username(cookie_value) if cookie_value else None
        print(f"🔑 handle_username_loaded: verified username={username!r}")

        if username and account_exists(username):
            # Valid username in cookie - auto login
            self.logged_in_user = username.lower()
            self.login_dialog_open = False
            self.refresh_session_list()

            # Load the most recently active session — see _load_latest_session().
            if self._load_latest_session():
                self.add_debug(f"✅ Logged in as: {self.logged_in_user}")
            else:
                self.add_debug(f"✅ Logged in as: {self.logged_in_user} (new)")

            from ..lib.logging_utils import console_separator
            console_separator()  # File log
            self.debug_messages = [*self.debug_messages, "────────────────────"]

            # Set session cookie AND start Browser Push Bus SSE stream
            from ..lib.browser_storage import set_session_id_script
            combined_script = (
                set_session_id_script(self.session_id)
                + "\nif(window.startBrowserStream) startBrowserStream('"
                + self.session_id
                + "');"
            )
            return rx.call_script(combined_script)
        else:
            # No valid username - dialog stays open (default is True)
            self.add_debug("🔐 Login required")
