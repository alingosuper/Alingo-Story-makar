"""Authentication: user model, password hashing, login/register routes."""

import datetime
from dataclasses import dataclass

import bcrypt
from fasthtml.common import Redirect


@dataclass
class User:
    username: str = ""
    password_hash: str = ""
    created_at: str = ""
    id: int = None  # None → autoincrement


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _auth_page(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Alingo Video Story</title>
<style>
:root {{
    --bg: #111111; --bg2: #1a1a1a; --fg: #e0e0e0; --fg2: #888888;
    --accent: #d4a843; --border: #333333; --card-bg: #242424; --radius: 6px;
    --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    --danger: #e74c3c;
}}
*, *::before, *::after {{ box-sizing: border-box; }}
body {{ margin:0; font-family:var(--font-ui); background:var(--bg); color:var(--fg); }}
.auth-page {{ min-height:100vh; display:flex; align-items:center; justify-content:center; }}
.auth-card {{ max-width:400px; width:100%; padding:2rem; background:var(--card-bg); border:1px solid var(--border); border-radius:var(--radius); }}
.auth-card h2 {{ text-align:center; margin:0 0 1.5rem; }}
.auth-card h2 .accent {{ color:var(--accent); }}
.auth-card form {{ display:flex; flex-direction:column; gap:1rem; }}
.auth-card input {{ padding:0.6rem; background:var(--bg2); border:1px solid var(--border); border-radius:var(--radius); color:var(--fg); font-size:0.95rem; }}
.auth-card input:focus {{ outline:none; border-color:var(--accent); }}
.auth-card .btn {{ width:100%; padding:0.6rem; background:var(--accent); color:#000; border:none; border-radius:var(--radius); cursor:pointer; font-weight:600; font-size:0.95rem; }}
.auth-card .btn:hover {{ background:#b8922e; }}
.auth-error {{ color:var(--danger); font-size:0.85rem; text-align:center; margin:0; }}
.auth-link {{ text-align:center; font-size:0.85rem; color:var(--fg2); margin-top:1rem; }}
.auth-link a {{ color:var(--accent); text-decoration:none; }}
.auth-link a:hover {{ text-decoration:underline; }}
</style>
</head><body>
<div class="auth-page"><div class="auth-card">
{body_html}
</div></div>
</body></html>"""


def login_page(error: str = "") -> str:
    error_html = f'<p class="auth-error">{error}</p>' if error else ""
    return _auth_page("Login", f"""
<h2>STORY<span class="accent">2</span>VIDEO</h2>
{error_html}
<form method="post" action="/login">
    <input type="text" name="username" placeholder="Username" required autocomplete="username">
    <input type="password" name="password" placeholder="Password" required autocomplete="current-password">
    <button type="submit" class="btn">Log in</button>
</form>
<p class="auth-link">No account? <a href="/register">Register</a></p>
""")


def register_page(error: str = "") -> str:
    error_html = f'<p class="auth-error">{error}</p>' if error else ""
    return _auth_page("Register", f"""
<h2>STORY<span class="accent">2</span>VIDEO</h2>
{error_html}
<form method="post" action="/register">
    <input type="text" name="username" placeholder="Username" required autocomplete="username">
    <input type="password" name="password" placeholder="Password" required autocomplete="new-password">
    <input type="password" name="confirm" placeholder="Confirm password" required autocomplete="new-password">
    <button type="submit" class="btn">Register</button>
</form>
<p class="auth-link">Already have an account? <a href="/login">Log in</a></p>
""")


def register_auth_routes(rt, users):
    """Register login/register/logout routes on the app router."""

    @rt("/login", methods="get")
    def login_get(session):
        if session.get("user_id"):
            return Redirect("/")
        return login_page()

    @rt("/login", methods="post")
    def login_post(session, username: str, password: str):
        username = username.strip()
        if not username or not password:
            return login_page("Username and password required.")
        results = users(where=f"username='{username}'", limit=1)
        if not results or not verify_password(password, results[0].password_hash):
            return login_page("Invalid username or password.")
        session["user_id"] = results[0].id
        return Redirect("/")

    @rt("/register", methods="get")
    def register_get(session):
        if session.get("user_id"):
            return Redirect("/")
        return register_page()

    @rt("/register", methods="post")
    def register_post(session, username: str, password: str, confirm: str):
        username = username.strip()
        if not username or not password:
            return register_page("All fields are required.")
        if password != confirm:
            return register_page("Passwords do not match.")
        existing = users(where=f"username='{username}'", limit=1)
        if existing:
            return register_page("Username already taken.")
        pw_hash = hash_password(password)
        user = users.insert(User(
            username=username,
            password_hash=pw_hash,
            created_at=datetime.datetime.now().isoformat(),
        ))
        session["user_id"] = user.id
        return Redirect("/")

    @rt("/logout", methods="get")
    def logout_get(session):
        session.clear()
        return Redirect("/login")
