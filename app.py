"""
C-Messager (Pro)
================
Messenger-style chat app.

  * Registration (name, address, birthday, phone, email) + unique ID No (CM1001...)
  * Search by ID or name to chat with any user
  * Profile photo, bio, online / last seen, seen tick, unread count
  * Send image, delete message
  * Group chat (group admin add / remove / leave)
  * Admin panel: view all users, search, ban/unban, delete, password reset, Excel download
  * All user information is automatically saved to users.xlsx (Excel)

Config via environment variable (or .env file) - see README.
"""
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, send_file, session, url_for)
from markupsafe import escape
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from PIL import Image, ImageOps
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

# ------------------------------------------------------------------ config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_dotenv():
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


load_dotenv()

DATA_DIR = os.environ.get("CM_DATA_DIR") or os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
AVATAR_DIR = os.path.join(UPLOAD_DIR, "avatars")
MEDIA_DIR = os.path.join(UPLOAD_DIR, "media")
for _d in (AVATAR_DIR, MEDIA_DIR):
    os.makedirs(_d, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "c_messager.db")
XLSX_PATH = os.path.join(DATA_DIR, "users.xlsx")

LOCAL_OFFSET = float(os.environ.get("CM_TZ_OFFSET_HOURS") or 6)  # Bangladesh = +6
ALLOW_NAME_SEARCH = (os.environ.get("CM_ALLOW_NAME_SEARCH") or "1") == "1"
MAX_GROUP_MEMBERS = 200
ONLINE_WINDOW = 35  # second
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

Image.MAX_IMAGE_PIXELS = 40_000_000


def load_secret():
    env = os.environ.get("CM_SECRET_KEY")
    if env:
        return env
    path = os.path.join(DATA_DIR, "secret.key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


ADMIN_PASSWORD = os.environ.get("CM_ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = "CM2010$"
    print("=" * 60)
    print("CM_ADMIN_PASSWORD is not set. Temporary admin password for this session:")
    print("   ", ADMIN_PASSWORD)
    print("To make it permanent, add CM_ADMIN_PASSWORD to your .env file.")
    print("=" * 60)

app = Flask(__name__)
app.secret_key = load_secret()
app.config.update(
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(os.environ.get("CM_HTTPS") == "1"),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
if os.environ.get("CM_BEHIND_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

excel_lock = threading.Lock()


# ---------------------------------------------------------------- database
@app.route('/google2a93afe8c228e5ef.html')
def google_verification():
    return app.send_static_file('google2a93afe8c228e5ef.html')
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_column(conn, table, column, ddl):
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    old = os.path.join(BASE_DIR, "c_messager.db")  # if v1 database exists, bring it over
    if os.path.exists(old) and not os.path.exists(DB_PATH):
        shutil.copy(old, DB_PATH)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_code     TEXT UNIQUE,
                name          TEXT NOT NULL,
                address       TEXT NOT NULL,
                birthday      TEXT NOT NULL,
                phone         TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sender     TEXT NOT NULL,
                receiver   TEXT NOT NULL,
                body       TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS groups (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                group_code TEXT UNIQUE,
                name       TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_members (
                group_code TEXT NOT NULL,
                user_code  TEXT NOT NULL,
                role       TEXT NOT NULL DEFAULT 'member',
                joined_at  TEXT NOT NULL,
                last_read  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (group_code, user_code)
            );
            """
        )
        ensure_column(conn, "users", "email", "TEXT DEFAULT ''")
        ensure_column(conn, "users", "bio", "TEXT DEFAULT ''")
        ensure_column(conn, "users", "avatar", "TEXT DEFAULT ''")
        ensure_column(conn, "users", "last_seen", "TEXT DEFAULT ''")
        ensure_column(conn, "users", "is_banned", "INTEGER DEFAULT 0")
        ensure_column(conn, "messages", "image", "TEXT DEFAULT ''")
        ensure_column(conn, "messages", "deleted", "INTEGER DEFAULT 0")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "seen" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN seen INTEGER DEFAULT 0")
            conn.execute("UPDATE messages SET seen=1")  # mark older messages as "seen"
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_pair ON messages(sender, receiver)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_recv ON messages(receiver, id)")


# ----------------------------------------------------------------- helpers
def now_iso():
    return datetime.now(timezone.utc).strftime(TS_FMT)


def parse_iso(s):
    try:
        return datetime.strptime(s, TS_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def is_online(last_seen):
    d = parse_iso(last_seen)
    return bool(d and (datetime.now(timezone.utc) - d).total_seconds() < ONLINE_WINDOW)


def local_str(s):
    d = parse_iso(s)
    if not d:
        return s or ""
    return (d + timedelta(hours=LOCAL_OFFSET)).strftime("%Y-%m-%d %H:%M")


def err(message, status=400):
    return jsonify(error=message), status


def remove_file(folder, name):
    if name and re.fullmatch(r"[0-9a-f]{32}\.jpg", name):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def save_image(file_storage, folder, max_side, square=False):
    """Re-encodes image (strips EXIF/malware) and saves as JPEG. Returns None on failure."""
    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
        img.load()
    except Exception:
        return None
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    if square:
        img = ImageOps.fit(img, (max_side, max_side))
    else:
        img.thumbnail((max_side, max_side))
    name = uuid.uuid4().hex + ".jpg"
    img.save(os.path.join(folder, name), "JPEG", quality=85, optimize=True)
    return name


PALETTE = ["#1f7a6d", "#c2571a", "#6b4fbb", "#b3372f", "#2563a8", "#8a6d00", "#0f766e", "#9d2b6b"]


def initials_svg(label, key):
    words = [w for w in re.split(r"\s+", (label or "").strip()) if w]
    ini = "".join(w[0] for w in words[:2]).upper() or "?"
    color = PALETTE[sum(ord(c) for c in key) % len(PALETTE)]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" fill="{color}"/>'
        '<text x="32" y="33" text-anchor="middle" dominant-baseline="central" '
        'font-family="Segoe UI, Arial, sans-serif" font-size="28" font-weight="600" '
        f'fill="#ffffff">{escape(ini)}</text></svg>'
    )
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=86400"})


# simple in-memory rate limit (to prevent brute-force)
_attempts = {}
_attempts_lock = threading.Lock()


def too_many(key, limit=8, window=300):
    now = time.time()
    with _attempts_lock:
        recent = [t for t in _attempts.get(key, []) if now - t < window]
        _attempts[key] = recent
        return len(recent) >= limit


def record(key):
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())


def client_ip():
    return request.remote_addr or "?"


# ----------------------------------------------------------------- excel
def export_to_excel():
    """Fetches all users from database and creates/updates users.xlsx."""
    with excel_lock:
        with db() as conn:
            rows = conn.execute(
                "SELECT user_code, name, address, birthday, phone, email, is_banned, created_at "
                "FROM users ORDER BY id"
            ).fetchall()
        wb = Workbook()
        ws = wb.active
        ws.title = "Users"
        ws.append(["ID No", "Name", "Address", "Birthday", "Phone", "Email", "Status", "Registered At"])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F7A6D")
            c.alignment = Alignment(horizontal="center")
        for r in rows:
            ws.append([r["user_code"], r["name"], r["address"], r["birthday"], r["phone"],
                       r["email"] or "", "Banned" if r["is_banned"] else "Active",
                       local_str(r["created_at"])])
        for col, width in zip("ABCDEFGH", (11, 26, 40, 13, 16, 28, 10, 18)):
            ws.column_dimensions[col].width = width
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        wb.save(XLSX_PATH)


# ------------------------------------------------------------- auth / csrf
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


def current_user():
    if "user" in g:
        return g.user
    g.user = None
    code = session.get("code")
    if code:
        with db() as conn:
            u = conn.execute("SELECT * FROM users WHERE user_code=?", (code,)).fetchone()
        if u and not u["is_banned"]:
            g.user = u
        else:
            session.pop("code", None)
    return g.user


def require_user():
    u = current_user()
    if not u:
        abort(401, "Please log in.")
    return u


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*a, **kw)
    return wrapper


@app.before_request
def protect_and_touch():
    if request.method == "POST":
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf") or ""
        if not secrets.compare_digest(token, session.get("csrf", "")):
            abort(400, "Security token mismatch. Please refresh the page and try again.")
    if request.endpoint and request.endpoint != "static":
        u = current_user()
        if u and time.time() - session.get("ls", 0) > 10:
            with db() as conn:
                conn.execute("UPDATE users SET last_seen=? WHERE user_code=?",
                             (now_iso(), u["user_code"]))
            session["ls"] = int(time.time())


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(HTTPException)
def http_error(e):
    if request.path.startswith("/api/"):
        return jsonify(error=e.description or e.name), e.code
    return e


# ----------------------------------------------------- profile validation
def parse_profile(form):
    f = {k: form.get(k, "").strip() for k in ("name", "address", "birthday", "phone", "email")}
    if not (f["name"] and f["address"] and f["birthday"] and f["phone"]):
        return f, "Please fill in all required fields."
    if len(f["name"]) < 2 or len(f["name"]) > 80:
        return f, "Name must be between 2 and 80 characters."
    if len(f["address"]) > 250:
        return f, "Address is too long (max 250 characters)."
    try:
        bday = datetime.strptime(f["birthday"], "%Y-%m-%d").date()
    except ValueError:
        bday = None
    if bday is None or bday >= date.today() or bday.year < 1900:
        return f, "Please enter a valid date of birth."
    phone = re.sub(r"[\s\-]", "", f["phone"])
    if not re.fullmatch(r"\+?[0-9]{6,15}", phone):
        return f, "Please enter a valid phone number (e.g., 017XXXXXXXX)."
    f["phone"] = phone
    if f["email"] and (len(f["email"]) > 120 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", f["email"])):
        return f, "Please enter a valid email address or leave it blank."
    return f, None


# ------------------------------------------------------------------ pages
@app.route("/")
def index():
    return redirect(url_for("chat") if current_user() else url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("chat"))
    error, f = None, {}
    if request.method == "POST":
        f, error = parse_profile(request.form)
        password = request.form.get("password", "")
        rl_key = "reg:" + client_ip()
        if not error and not request.form.get("agree"):
            error = "You must agree to the privacy policy."
        if not error and len(password) < 6:
            error = "Password must be at least 6 characters long."
        if not error and password != request.form.get("confirm", ""):
            error = "Passwords do not match."
        if not error and too_many(rl_key, 10, 3600):
            error = "Too many accounts created. Please try again later."
        code = None
        if not error:
            with db() as conn:
                if conn.execute("SELECT 1 FROM users WHERE phone=?", (f["phone"],)).fetchone():
                    error = "An account with this phone number already exists."
                else:
                    cur = conn.execute(
                        "INSERT INTO users (name, address, birthday, phone, email, password_hash, "
                        "created_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                        (f["name"], f["address"], f["birthday"], f["phone"], f["email"],
                         generate_password_hash(password), now_iso(), now_iso()),
                    )
                    code = f"CM{1000 + cur.lastrowid}"
                    conn.execute("UPDATE users SET user_code=? WHERE id=?", (code, cur.lastrowid))
        if code:
            record(rl_key)
            export_to_excel()  # update Excel instantly
            session.clear()
            session["code"] = code
            session.permanent = True
            return render_template("welcome.html", name=f["name"], code=code)
    return render_template("register.html", error=error, f=f)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("chat"))
    error, code = None, ""
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        # 8 failed attempts per ID blocks for 5 mins; 30 failed attempts per IP blocks for 5 mins
        ip_key, id_key = "login:ip:" + client_ip(), "login:id:" + code
        if too_many(ip_key, 30) or too_many(id_key, 8):
            error = "Too many failed attempts. Please try again in 5 minutes."
        else:
            with db() as conn:
                u = conn.execute("SELECT * FROM users WHERE user_code=?", (code,)).fetchone()
            if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
                if u["is_banned"]:
                    error = "This account has been suspended. Please contact an administrator."
                else:
                    session.clear()
                    session["code"] = u["user_code"]
                    session.permanent = True
                    return redirect(url_for("chat"))
            else:
                record(ip_key)
                record(id_key)
                error = "Invalid ID No or password."
    return render_template("login.html", error=error, code=code)


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("code", None)
    return redirect(url_for("login"))


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/chat")
@login_required
def chat():
    return render_template("chat.html", me=current_user())


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    me = current_user()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "info":
            f, error = parse_profile(request.form)
            bio = request.form.get("bio", "").strip()[:200]
            if not error:
                with db() as conn:
                    dup = conn.execute("SELECT 1 FROM users WHERE phone=? AND user_code!=?",
                                       (f["phone"], me["user_code"])).fetchone()
                if dup:
                    error = "This phone number is already used by another account."
            if error:
                flash(error, "err")
                return redirect(url_for("profile"))
            avatar_name = me["avatar"] or ""
            if request.form.get("remove_avatar"):
                remove_file(AVATAR_DIR, avatar_name)
                avatar_name = ""
            up = request.files.get("avatar")
            if up and up.filename:
                new = save_image(up, AVATAR_DIR, 256, square=True)
                if not new:
                    flash("Invalid image file. Please use png / jpg / webp.", "err")
                    return redirect(url_for("profile"))
                remove_file(AVATAR_DIR, avatar_name)
                avatar_name = new
            with db() as conn:
                conn.execute(
                    "UPDATE users SET name=?, address=?, birthday=?, phone=?, email=?, bio=?, avatar=? "
                    "WHERE user_code=?",
                    (f["name"], f["address"], f["birthday"], f["phone"], f["email"], bio,
                     avatar_name, me["user_code"]),
                )
            export_to_excel()
            flash("Profile updated successfully.", "ok")
        elif action == "password":
            cur_pw = request.form.get("current", "")
            new_pw = request.form.get("new", "")
            if not check_password_hash(me["password_hash"], cur_pw):
                flash("Current password is incorrect.", "err")
            elif len(new_pw) < 6:
                flash("New password must be at least 6 characters long.", "err")
            elif new_pw != request.form.get("confirm", ""):
                flash("New passwords do not match.", "err")
            else:
                with db() as conn:
                    conn.execute("UPDATE users SET password_hash=? WHERE user_code=?",
                                 (generate_password_hash(new_pw), me["user_code"]))
                flash("Password changed successfully.", "ok")
        return redirect(url_for("profile"))
    return render_template("profile.html", me=me)


@app.route("/avatar/<code>")
def avatar(code):
    if not (current_user() or session.get("admin")):
        abort(401)
    code = code.upper()
    av = ""
    with db() as conn:
        if code.startswith("G"):
            row = conn.execute("SELECT name FROM groups WHERE group_code=?", (code,)).fetchone()
        else:
            row = conn.execute("SELECT name, avatar FROM users WHERE user_code=?", (code,)).fetchone()
            av = (row["avatar"] or "") if row else ""
    if not row:
        abort(404)
    if av:
        path = os.path.join(AVATAR_DIR, av)
        if os.path.exists(path):
            return send_file(path, mimetype="image/jpeg", max_age=86400)
    return initials_svg(row["name"], code)


@app.route("/media/<name>")
def media(name):
    if not current_user():
        abort(401)
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", name):
        abort(404)
    path = os.path.join(MEDIA_DIR, name)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=86400)


# -------------------------------------------------------------- chat API
def group_role(conn, gcode, user_code):
    r = conn.execute("SELECT role FROM group_members WHERE group_code=? AND user_code=?",
                     (gcode, user_code)).fetchone()
    return r["role"] if r else None


def names_for(conn, codes):
    codes = list(set(codes))
    if not codes:
        return {}
    q = ",".join("?" * len(codes))
    return {r["user_code"]: r["name"] for r in
            conn.execute(f"SELECT user_code, name FROM users WHERE user_code IN ({q})", codes)}


def msg_json(r, me, names=None):
    deleted = bool(r["deleted"])
    return {
        "id": r["id"],
        "mine": r["sender"] == me,
        "sender": r["sender"],
        "sender_name": (names or {}).get(r["sender"], r["sender"]),
        "body": "" if deleted else r["body"],
        "image": "" if deleted or not r["image"] else "/media/" + r["image"],
        "deleted": deleted,
        "time": r["created_at"],
        "seen": bool(r["seen"]),
    }


def preview(r):
    if r["deleted"]:
        return "Message was deleted"
    if r["image"] and not r["body"]:
        return "\U0001F4F7 Photo"
    return r["body"][:60]


def fix_groups(conn):
    """Deletes empty groups and promotes an old member if admin is gone. Returns deleted image names."""
    files = []
    empty = [r["group_code"] for r in conn.execute(
        "SELECT group_code FROM groups WHERE group_code NOT IN (SELECT group_code FROM group_members)")]
    for gc in empty:
        files += [r["image"] for r in conn.execute(
            "SELECT image FROM messages WHERE receiver=? AND image!=''", (gc,))]
        conn.execute("DELETE FROM messages WHERE receiver=?", (gc,))
        conn.execute("DELETE FROM groups WHERE group_code=?", (gc,))
    no_admin = [r["group_code"] for r in conn.execute(
        "SELECT group_code FROM groups WHERE group_code NOT IN "
        "(SELECT group_code FROM group_members WHERE role='admin')")]
    for gc in no_admin:
        conn.execute(
            "UPDATE group_members SET role='admin' WHERE rowid=("
            "SELECT rowid FROM group_members WHERE group_code=? ORDER BY joined_at LIMIT 1)", (gc,))
    return files


@app.route("/api/search")
def api_search():
    me = require_user()["user_code"]
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    with db() as conn:
        if ALLOW_NAME_SEARCH:
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = conn.execute(
                "SELECT user_code, name, avatar, last_seen FROM users WHERE is_banned=0 AND user_code!=? "
                "AND (user_code LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\') ORDER BY name LIMIT 10",
                (me, f"%{esc}%", f"%{esc}%")).fetchall()
        else:
            rows = conn.execute(
                "SELECT user_code, name, avatar, last_seen FROM users "
                "WHERE is_banned=0 AND user_code!=? AND user_code=?", (me, q.upper())).fetchall()
    return jsonify([{"code": r["user_code"], "name": r["name"], "av": r["avatar"] or "",
                     "online": is_online(r["last_seen"])} for r in rows])


@app.route("/api/conversations")
def api_conversations():
    me = require_user()["user_code"]
    items = []
    with db() as conn:
        last = conn.execute(
            """
            SELECT * FROM messages WHERE id IN (
                SELECT MAX(id) FROM messages
                WHERE receiver NOT LIKE 'G%' AND (sender=:me OR receiver=:me)
                GROUP BY CASE WHEN sender=:me THEN receiver ELSE sender END)
            """, {"me": me}).fetchall()
        others = {(r["receiver"] if r["sender"] == me else r["sender"]) for r in last}
        users = {}
        if others:
            q = ",".join("?" * len(others))
            users = {u["user_code"]: u for u in conn.execute(
                f"SELECT user_code, name, avatar, last_seen FROM users WHERE user_code IN ({q})",
                list(others))}
        unread = {r["sender"]: r["c"] for r in conn.execute(
            "SELECT sender, COUNT(*) AS c FROM messages WHERE receiver=? AND seen=0 AND deleted=0 "
            "GROUP BY sender", (me,))}
        for r in last:
            other = r["receiver"] if r["sender"] == me else r["sender"]
            u = users.get(other)
            if not u:
                continue
            items.append({
                "type": "user", "code": other, "name": u["name"], "av": u["avatar"] or "",
                "online": is_online(u["last_seen"]),
                "last": ("You: " if r["sender"] == me else "") + preview(r),
                "time": r["created_at"], "unread": unread.get(other, 0), "sort": r["id"],
            })
        for gr in conn.execute(
                "SELECT g.group_code, g.name, gm.last_read FROM groups g "
                "JOIN group_members gm ON gm.group_code=g.group_code WHERE gm.user_code=?",
                (me,)).fetchall():
            m = conn.execute(
                "SELECT m.*, u.name AS sname FROM messages m LEFT JOIN users u ON u.user_code=m.sender "
                "WHERE m.receiver=? ORDER BY m.id DESC LIMIT 1", (gr["group_code"],)).fetchone()
            cnt = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE receiver=? AND id>? AND sender!=? AND deleted=0",
                (gr["group_code"], gr["last_read"], me)).fetchone()[0]
            if m:
                who = "You" if m["sender"] == me else (m["sname"] or m["sender"])
                last_text, when, sort = f"{who}: {preview(m)}", m["created_at"], m["id"]
            else:
                last_text, when, sort = "New group", "", 10 ** 12
            items.append({"type": "group", "code": gr["group_code"], "name": gr["name"], "av": "",
                          "online": False, "last": last_text, "time": when,
                          "unread": cnt, "sort": sort})
    items.sort(key=lambda x: x["sort"], reverse=True)
    return jsonify(items)


@app.route("/api/messages/<code>")
def api_messages(code):
    me = require_user()["user_code"]
    code = code.upper()
    with db() as conn:
        if code.startswith("G"):
            if not group_role(conn, code, me):
                abort(403, "You are not a member of this group.")
            grp = conn.execute("SELECT * FROM groups WHERE group_code=?", (code,)).fetchone()
            members = conn.execute(
                "SELECT u.user_code, u.name, u.avatar, u.last_seen, gm.role FROM group_members gm "
                "JOIN users u ON u.user_code=gm.user_code WHERE gm.group_code=? "
                "ORDER BY gm.role='admin' DESC, u.name", (code,)).fetchall()
            rows = conn.execute("SELECT * FROM messages WHERE receiver=? ORDER BY id DESC LIMIT 200",
                                (code,)).fetchall()[::-1]
            if rows:
                conn.execute("UPDATE group_members SET last_read=? WHERE group_code=? AND user_code=?",
                             (rows[-1]["id"], code, me))
            names = {m["user_code"]: m["name"] for m in members}
            names.update(names_for(conn, [r["sender"] for r in rows if r["sender"] not in names]))
            info = {
                "type": "group", "code": code, "name": grp["name"], "av": "",
                "count": len(members), "my_role": group_role(conn, code, me),
                "members": [{"code": m["user_code"], "name": m["name"], "av": m["avatar"] or "",
                             "role": m["role"], "online": is_online(m["last_seen"])} for m in members],
            }
        else:
            other = conn.execute("SELECT * FROM users WHERE user_code=?", (code,)).fetchone()
            if not other:
                abort(404, "ID No not found.")
            rows = conn.execute(
                "SELECT * FROM messages WHERE receiver NOT LIKE 'G%' AND "
                "((sender=? AND receiver=?) OR (sender=? AND receiver=?)) ORDER BY id DESC LIMIT 200",
                (me, code, code, me)).fetchall()[::-1]
            conn.execute("UPDATE messages SET seen=1 WHERE sender=? AND receiver=? AND seen=0", (code, me))
            names = {me: "You", code: other["name"]}
            info = {
                "type": "user", "code": code, "name": other["name"], "bio": other["bio"] or "",
                "av": other["avatar"] or "", "online": is_online(other["last_seen"]),
                "last_seen": other["last_seen"] or "",
            }
    return jsonify(info=info, messages=[msg_json(r, me, names) for r in rows])


@app.route("/api/send", methods=["POST"])
def api_send():
    me = require_user()["user_code"]
    to = request.form.get("to", "").strip().upper()
    body = request.form.get("body", "").strip()[:2000]
    file = request.files.get("image")
    has_file = bool(file and file.filename)
    if not body and not has_file:
        return err("Please write something or attach an image.")
    with db() as conn:
        if to.startswith("G"):
            if not group_role(conn, to, me):
                return err("You are not a member of this group.", 403)
        else:
            if to == me:
                return err("You cannot send a message to yourself.")
            u = conn.execute("SELECT is_banned FROM users WHERE user_code=?", (to,)).fetchone()
            if not u or u["is_banned"]:
                return err("Cannot send message to this user.", 404)
        image = ""
        if has_file:
            image = save_image(file, MEDIA_DIR, 1600)
            if not image:
                return err("Invalid image file (please use png / jpg / webp / gif).")
        conn.execute(
            "INSERT INTO messages (sender, receiver, body, image, created_at, seen, deleted) "
            "VALUES (?,?,?,?,?,0,0)", (me, to, body, image, now_iso()))
    return jsonify(ok=True)


@app.route("/api/message/<int:mid>/delete", methods=["POST"])
def api_delete_message(mid):
    me = require_user()["user_code"]
    with db() as conn:
        r = conn.execute("SELECT image FROM messages WHERE id=? AND sender=?", (mid, me)).fetchone()
        if not r:
            return err("Message not found.", 404)
        conn.execute("UPDATE messages SET deleted=1, body='', image='' WHERE id=?", (mid,))
    remove_file(MEDIA_DIR, r["image"])
    return jsonify(ok=True)


# ------------------------------------------------------------ group API
@app.route("/api/groups", methods=["POST"])
def api_group_create():
    me = require_user()["user_code"]
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()[:60]
    codes = {str(c).strip().upper() for c in (data.get("members") or []) if str(c).strip()}
    codes.discard(me)
    if not name:
        return err("Please enter a group name.")
    if len(codes) + 1 > MAX_GROUP_MEMBERS:
        return err(f"Maximum {MAX_GROUP_MEMBERS} members allowed per group.")
    with db() as conn:
        valid = set()
        if codes:
            q = ",".join("?" * len(codes))
            valid = {r["user_code"] for r in conn.execute(
                f"SELECT user_code FROM users WHERE is_banned=0 AND user_code IN ({q})", list(codes))}
        missing = codes - valid
        if missing:
            return err("The following IDs were not found: " + ", ".join(sorted(missing)))
        cur = conn.execute("INSERT INTO groups (name, created_by, created_at) VALUES (?,?,?)",
                           (name, me, now_iso()))
        gcode = f"G{1000 + cur.lastrowid}"
        conn.execute("UPDATE groups SET group_code=? WHERE id=?", (gcode, cur.lastrowid))
        conn.execute("INSERT INTO group_members (group_code, user_code, role, joined_at) VALUES (?,?,?,?)",
                     (gcode, me, "admin", now_iso()))
        for c in valid:
            conn.execute("INSERT INTO group_members (group_code, user_code, role, joined_at) VALUES (?,?,?,?)",
                         (gcode, c, "member", now_iso()))
    return jsonify(ok=True, code=gcode, name=name)


@app.route("/api/groups/<code>/add", methods=["POST"])
def api_group_add(code):
    me = require_user()["user_code"]
    code = code.upper()
    new = str((request.get_json(silent=True) or {}).get("code", "")).strip().upper()
    with db() as conn:
        if group_role(conn, code, me) != "admin":
            return err("Only group admins can add members.", 403)
        if not conn.execute("SELECT 1 FROM users WHERE user_code=? AND is_banned=0", (new,)).fetchone():
            return err("ID No not found.", 404)
        if group_role(conn, code, new):
            return err("User is already in the group.")
        count = conn.execute("SELECT COUNT(*) FROM group_members WHERE group_code=?", (code,)).fetchone()[0]
        if count >= MAX_GROUP_MEMBERS:
            return err(f"Maximum {MAX_GROUP_MEMBERS} members allowed per group.")
        last_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM messages WHERE receiver=?", (code,)).fetchone()[0]
        conn.execute("INSERT INTO group_members (group_code, user_code, role, joined_at, last_read) "
                     "VALUES (?,?,?,?,?)", (code, new, "member", now_iso(), last_id))
    return jsonify(ok=True)


@app.route("/api/groups/<code>/remove", methods=["POST"])
def api_group_remove(code):
    me = require_user()["user_code"]
    code = code.upper()
    target = str((request.get_json(silent=True) or {}).get("code", "")).strip().upper()
    with db() as conn:
        if group_role(conn, code, me) != "admin":
            return err("Only group admins can remove members.", 403)
        if target == me:
            return err("To remove yourself, click 'Leave Group'.")
        conn.execute("DELETE FROM group_members WHERE group_code=? AND user_code=?", (code, target))
    return jsonify(ok=True)


@app.route("/api/groups/<code>/leave", methods=["POST"])
def api_group_leave(code):
    me = require_user()["user_code"]
    code = code.upper()
    with db() as conn:
        if not group_role(conn, code, me):
            return err("You are not a member of this group.", 403)
        conn.execute("DELETE FROM group_members WHERE group_code=? AND user_code=?", (code, me))
        files = fix_groups(conn)
    for f in files:
        remove_file(MEDIA_DIR, f)
    return jsonify(ok=True)


# ------------------------------------------------------------- admin panel
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        key = "admin:" + client_ip()
        if too_many(key, 5, 300):
            error = "Too many failed attempts. Please try again in 5 minutes."
        elif secrets.compare_digest(request.form.get("password", "").encode(), ADMIN_PASSWORD.encode()):
            session["admin"] = True
            return redirect(url_for("admin"))
        else:
            record(key)
            error = "Incorrect password."
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin():
    q = request.args.get("q", "").strip()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ONLINE_WINDOW)).strftime(TS_FMT)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db() as conn:
        one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
        stats = {
            "users": one("SELECT COUNT(*) FROM users"),
            "online": one("SELECT COUNT(*) FROM users WHERE last_seen>=?", cutoff),
            "today": one("SELECT COUNT(*) FROM users WHERE created_at>=?", today),
            "banned": one("SELECT COUNT(*) FROM users WHERE is_banned=1"),
            "messages": one("SELECT COUNT(*) FROM messages"),
            "groups": one("SELECT COUNT(*) FROM groups"),
        }
        if q:
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{esc}%"
            rows = conn.execute(
                "SELECT * FROM users WHERE name LIKE ? ESCAPE '\\' OR user_code LIKE ? ESCAPE '\\' "
                "OR phone LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 500",
                (like, like, like, like)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM users ORDER BY id DESC LIMIT 500").fetchall()
        groups = conn.execute(
            "SELECT g.group_code, g.name, g.created_by, g.created_at, "
            "(SELECT COUNT(*) FROM group_members WHERE group_code=g.group_code) AS members "
            "FROM groups g ORDER BY g.id DESC LIMIT 200").fetchall()
    users = [dict(u, online=is_online(u["last_seen"]), joined=local_str(u["created_at"]),
                  seen_text=local_str(u["last_seen"])) for u in rows]
    groups = [dict(gr, created=local_str(gr["created_at"])) for gr in groups]
    return render_template("admin.html", stats=stats, users=users, groups=groups, q=q)


@app.route("/admin/export.xlsx")
@admin_required
def admin_export():
    export_to_excel()
    return send_file(XLSX_PATH, as_attachment=True, download_name="c_messager_users.xlsx")


@app.route("/admin/users/<code>/ban", methods=["POST"])
@admin_required
def admin_ban(code):
    with db() as conn:
        conn.execute("UPDATE users SET is_banned = 1 - COALESCE(is_banned,0) WHERE user_code=?",
                     (code.upper(),))
    export_to_excel()
    flash(f"Status changed for {code.upper()}.", "ok")
    return redirect(url_for("admin", q=request.form.get("q", "")))


@app.route("/admin/users/<code>/reset", methods=["POST"])
@admin_required
def admin_reset(code):
    temp = secrets.token_urlsafe(6)
    with db() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE user_code=?",
                     (generate_password_hash(temp), code.upper()))
    flash(f"New temporary password for {code.upper()}: {temp}  (inform the user immediately, they can change it from their profile)", "ok")
    return redirect(url_for("admin", q=request.form.get("q", "")))


@app.route("/admin/users/<code>/delete", methods=["POST"])
@admin_required
def admin_delete(code):
    code = code.upper()
    with db() as conn:
        u = conn.execute("SELECT avatar FROM users WHERE user_code=?", (code,)).fetchone()
        if u:
            files = [r["image"] for r in conn.execute(
                "SELECT image FROM messages WHERE (sender=? OR receiver=?) AND image!=''", (code, code))]
            conn.execute("DELETE FROM messages WHERE sender=? OR receiver=?", (code, code))
            conn.execute("DELETE FROM group_members WHERE user_code=?", (code,))
            conn.execute("DELETE FROM users WHERE user_code=?", (code,))
            files += fix_groups(conn)
            for f in files:
                remove_file(MEDIA_DIR, f)
            remove_file(AVATAR_DIR, u["avatar"])
    export_to_excel()
    flash(f"User {code} has been deleted.", "ok")
    return redirect(url_for("admin", q=request.form.get("q", "")))


# --------------------------------------------------------------- startup
init_db()
export_to_excel()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT") or 5000), debug=False, threaded=True)
