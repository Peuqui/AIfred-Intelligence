"""
Browser Storage Integration for AIfred

Handles cookie reading/writing for Session-ID via rx.call_script().

Reflex has no native cookie support, so JavaScript snippets are
generated and executed via rx.call_script().

Usage:
    # In State.on_load():
    yield rx.call_script(
        get_session_id_script(),
        callback=AIState.handle_session_id_loaded
    )

    # In callback when session_id == "NEW":
    return rx.call_script(set_session_id_script(new_id))
"""


# Cookie configuration
COOKIE_NAME = "aifred_session_id"
COOKIE_MAX_AGE_DAYS = 365


def get_session_id_script(delay_ms: int = 0) -> str:
    """
    Generate JavaScript to read Session-ID from cookie.

    The script returns the Session-ID or "NEW" if no cookie exists.
    The return value is passed to the callback.

    Args:
        delay_ms: Optional delay in milliseconds before reading cookie.
                  Use for retry mechanism to handle race conditions.

    Returns:
        JavaScript code as string
    """
    read_cookie_js = f"""
    const name = "{COOKIE_NAME}=";
    const cookies = document.cookie.split(';');
    for (let c of cookies) {{
        c = c.trim();
        if (c.indexOf(name) === 0) {{
            return c.substring(name.length);
        }}
    }}
    return "NEW";
    """

    if delay_ms > 0:
        # Delayed version: Returns a Promise that resolves after delay
        return f"""
(new Promise((resolve) => {{
    setTimeout(() => {{
        const result = (function() {{
            {read_cookie_js}
        }})();
        resolve(result);
    }}, {delay_ms});
}}))
"""
    else:
        # Immediate version
        return f"""
(function() {{
    {read_cookie_js}
}})()
"""


def set_session_id_script(session_id: str) -> str:
    """
    Generate JavaScript to set Session-ID as cookie.

    Cookie attributes:
    - path=/: Applies to entire domain
    - max-age: 1 year in seconds
    - SameSite=Lax: Prevents CSRF, but allows normal navigation

    Args:
        session_id: Session-ID to store (32 hex chars)

    Returns:
        JavaScript code as string
    """
    # Validation: Only allow alphanumeric characters
    safe_id = "".join(c for c in session_id if c.isalnum())[:32]
    if not safe_id:
        raise ValueError("Invalid session_id for cookie")

    max_age_seconds = COOKIE_MAX_AGE_DAYS * 24 * 60 * 60

    return f'document.cookie = "{COOKIE_NAME}={safe_id}; path=/; max-age={max_age_seconds}; SameSite=Lax";'


def clear_session_id_script() -> str:
    """
    Generate JavaScript to delete Session-ID cookie.

    Sets max-age=0 to immediately invalidate cookie.

    Returns:
        JavaScript code as string
    """
    return f'document.cookie = "{COOKIE_NAME}=; path=/; max-age=0";'


# ============================================================
# Username Cookie (for Login persistence)
# ============================================================

USERNAME_COOKIE_NAME = "aifred_username"


def get_username_script() -> str:
    """
    Generate JavaScript to read username from cookie.

    Returns username or empty string if no cookie exists.

    Returns:
        JavaScript code as string
    """
    return f"""
(function() {{
    const name = "{USERNAME_COOKIE_NAME}=";
    const cookies = document.cookie.split(';');
    for (let c of cookies) {{
        c = c.trim();
        if (c.indexOf(name) === 0) {{
            return c.substring(name.length);
        }}
    }}
    return "";
}})()
"""


def set_username_script(username: str) -> str:
    """
    Generate JavaScript to set the (HMAC-signed) username cookie.

    The cookie stores ``username.<signature>`` so it cannot be forged
    client-side to impersonate another account. Signing/verification lives in
    lib/auth (single source of truth).

    Args:
        username: Username to store

    Returns:
        JavaScript code as string
    """
    from .auth import sign_username

    # Sanitize username: only allow alphanumeric and underscore
    safe_username = "".join(c for c in username if c.isalnum() or c == "_")[:50]
    if not safe_username:
        raise ValueError("Invalid username for cookie")

    signed = sign_username(safe_username)
    max_age_seconds = COOKIE_MAX_AGE_DAYS * 24 * 60 * 60

    return f'document.cookie = "{USERNAME_COOKIE_NAME}={signed}; path=/; max-age={max_age_seconds}; SameSite=Lax";'


def clear_username_script() -> str:
    """
    Generate JavaScript to delete username cookie.

    Returns:
        JavaScript code as string
    """
    return f'document.cookie = "{USERNAME_COOKIE_NAME}=; path=/; max-age=0";'
