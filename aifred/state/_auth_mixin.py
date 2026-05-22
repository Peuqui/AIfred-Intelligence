"""Authentication mixin for AIfred state.

Handles login, registration, logout and cookie-based auto-login.
"""

from __future__ import annotations

import reflex as rx


class AuthMixin(rx.State, mixin=True):
    """Mixin for all authentication-related state and logic."""

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

    def handle_login_key_down(self, key: str):  # type: ignore[return]
        """Handle key press in login dialog - Enter triggers submit."""
        if key == "Enter":
            if self.login_mode == "login":
                return type(self).do_login
            else:
                return type(self).do_register

    def handle_login_submit(self, form_data: dict):  # type: ignore[return]
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

    def do_login(self):  # type: ignore[return]
        """Attempt to log in with entered credentials."""
        from ..lib.session_storage import verify_account, account_exists, list_sessions
        from ..lib.browser_storage import set_username_script, set_session_id_script

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
        self.refresh_session_list()  # type: ignore[attr-defined]

        # Load most recent session or create new one
        sessions = list_sessions(owner=self.logged_in_user)
        if sessions:
            self._load_session_by_id(sessions[0]["session_id"])  # type: ignore[attr-defined]
            self.add_debug(f"✅ Logged in as: {self.logged_in_user}")  # type: ignore[attr-defined]
        else:
            self.new_session()  # type: ignore[attr-defined]
            self.add_debug(f"✅ Logged in as: {self.logged_in_user} (new)")  # type: ignore[attr-defined]

        # Save username AND session cookies + start Browser Push Bus SSE stream
        combined_script = (
            set_username_script(self.logged_in_user)
            + "; "
            + set_session_id_script(self.session_id)  # type: ignore[attr-defined]
            + "; if(window.startBrowserStream) startBrowserStream('"
            + self.session_id  # type: ignore[attr-defined]
            + "');"
        )
        return rx.call_script(combined_script)

    def do_register(self):  # type: ignore[return]
        """Attempt to create new account."""
        from ..lib.session_storage import create_account, is_username_allowed
        from ..lib.browser_storage import set_username_script, set_session_id_script

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
        self.refresh_session_list()  # type: ignore[attr-defined]

        # New account always gets new session
        self.new_session()  # type: ignore[attr-defined]
        self.add_debug(f"✅ Account created: {self.logged_in_user}")  # type: ignore[attr-defined]

        # Save username AND session cookies + start Browser Push Bus SSE stream
        combined_script = (
            set_username_script(self.logged_in_user)
            + "; "
            + set_session_id_script(self.session_id)  # type: ignore[attr-defined]
            + "; if(window.startBrowserStream) startBrowserStream('"
            + self.session_id  # type: ignore[attr-defined]
            + "');"
        )
        return rx.call_script(combined_script)

    def do_logout(self):  # type: ignore[return]
        """Log out current user."""
        from ..lib.browser_storage import clear_username_script
        from ._audio_player_mixin import discard_audio_runtime_state
        from ._tts_streaming_mixin import discard_dashscope_runtime, discard_tts_backend_state

        self.add_debug(f"👋 Logged out: {self.logged_in_user}")  # type: ignore[attr-defined]
        # Drop per-session runtime state (audio snapshot, DashScope socket,
        # TTS backend tracking) for the session we're leaving — otherwise
        # it accumulates for the lifetime of the server process.
        prev_session = self.session_id  # type: ignore[attr-defined]
        discard_audio_runtime_state(prev_session)
        discard_dashscope_runtime(prev_session)
        discard_tts_backend_state(prev_session)

        self.logged_in_user = ""
        self.available_sessions = []  # type: ignore[attr-defined]
        self.session_id = ""  # type: ignore[attr-defined]
        # silent=True: Session data is preserved on disk, we're just clearing UI state
        self._clear_chat_internal(silent=True)  # type: ignore[attr-defined]

        # Show login dialog again
        self.login_dialog_open = True

        # Clear cookie
        return rx.call_script(clear_username_script())

    # ── Cookie-based Auto-Login ──────────────────────────────────────

    def handle_username_loaded(self, username: str):  # type: ignore[return]
        """Callback nach Cookie-Read via rx.call_script().

        Wird aufgerufen wenn das JavaScript den Username aus dem Cookie gelesen hat.
        Prüft ob Account existiert und loggt ein, sonst Login-Dialog öffnen.
        """
        print(f"🔑 handle_username_loaded called: username='{username}'")

        # Guard: Nur einmal ausführen
        if self._session_initialized:  # type: ignore[attr-defined, has-type]
            print("⏭️ Session already initialized, skipping")
            return
        self._session_initialized = True  # type: ignore[attr-defined]

        from ..lib.session_storage import account_exists, list_sessions

        if username and account_exists(username):
            # Valid username in cookie - auto login
            self.logged_in_user = username.lower()
            self.login_dialog_open = False
            self.refresh_session_list()  # type: ignore[attr-defined]

            # Load most recent session if available
            sessions = list_sessions(owner=self.logged_in_user)
            if sessions:
                most_recent = sessions[0]
                self._load_session_by_id(most_recent["session_id"])  # type: ignore[attr-defined]
                self.add_debug(f"✅ Logged in as: {self.logged_in_user}")  # type: ignore[attr-defined]
            else:
                self.new_session()  # type: ignore[attr-defined]
                self.add_debug(f"✅ Logged in as: {self.logged_in_user} (new)")  # type: ignore[attr-defined]

            from ..lib.logging_utils import console_separator
            console_separator()  # File log
            self.debug_messages = [*self.debug_messages, "────────────────────"]  # type: ignore[attr-defined]

            # Set session cookie AND start Browser Push Bus SSE stream
            from ..lib.browser_storage import set_session_id_script
            combined_script = (
                set_session_id_script(self.session_id)  # type: ignore[attr-defined]
                + "\nif(window.startBrowserStream) startBrowserStream('"
                + self.session_id  # type: ignore[attr-defined]
                + "');"
            )
            return rx.call_script(combined_script)
        else:
            # No valid username - dialog stays open (default is True)
            self.add_debug("🔐 Login required")  # type: ignore[attr-defined]
