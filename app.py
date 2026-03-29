import sqlite3, hashlib, os, secrets, hmac
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, g)

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

DB_PATH = os.path.join(os.path.dirname(__file__), "voting.db")
ADMIN_SETUP_KEY = os.getenv("ADMIN_SETUP_KEY", "CHANGE_ME_OWNER_KEY")


def get_db():
    """Return a thread-local DB connection with row factory."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    """Create tables, indexes, and view from schema.sql."""
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
        db.executescript(f.read())
    ensure_schema_updates(db)
    db.commit()
    db.close()


def ensure_schema_updates(db):
    """Apply minimal in-place schema updates for old databases."""
    admin_cols = {
        row[1]
        for row in db.execute("PRAGMA table_info(Admins)").fetchall()
    }
    if "role" not in admin_cols:
        db.execute("ALTER TABLE Admins ADD COLUMN role TEXT DEFAULT 'admin'")
    owner_count = db.execute(
        "SELECT COUNT(*) FROM Admins WHERE role='owner'"
    ).fetchone()[0]
    if owner_count == 0:
        db.execute(
            "UPDATE Admins SET role='owner' "
            "WHERE admin_id=(SELECT admin_id FROM Admins ORDER BY admin_id LIMIT 1)"
        )

    db.execute(
        "DELETE FROM Admins WHERE name=? AND email=? AND password=?",
        ("Admin", "admin@vote.com", hash_password("admin123"))
    )

def hash_password(plain: str) -> str:
    """SHA-256 with a salt prefix stored in the hash string."""
    salt = "vms_salt_2024"
    return hashlib.sha256((salt + plain).encode()).hexdigest()


def check_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "admin_id" not in session:
            flash("Admin access required.", "danger")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


def owner_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "admin_id" not in session:
            flash("Admin access required.", "danger")
            return redirect(url_for("admin_login"))
        if session.get("admin_role") != "owner":
            flash("Only owner can perform this action.", "danger")
            return redirect(url_for("admin_panel"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name  = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pwd   = request.form.get("password", "")

        if not name or not email or not pwd:
            flash("All fields are required.", "danger")
            return render_template("register.html")
        if len(pwd) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")

        db = get_db()
        existing = db.execute("SELECT user_id FROM Users WHERE email=?", (email,)).fetchone()
        if existing:
            flash("Email already registered.", "danger")
            return render_template("register.html")

        db.execute(
            "INSERT INTO Users(name,email,password) VALUES(?,?,?)",
            (name, email, hash_password(pwd))
        )
        db.commit()
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pwd   = request.form.get("password", "")
        db    = get_db()
        user  = db.execute("SELECT * FROM Users WHERE email=?", (email,)).fetchone()

        if user and check_password(pwd, user["password"]):
            session["user_id"]   = user["user_id"]
            session["user_name"] = user["name"]
            flash(f"Welcome, {user['name']}!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elections = db.execute(
        "SELECT * FROM Elections WHERE status='active' ORDER BY end_time"
    ).fetchall()

    voted = {
        row["election_id"]
        for row in db.execute(
            "SELECT election_id FROM Votes WHERE user_id=?",
            (session["user_id"],)
        ).fetchall()
    }
    return render_template("dashboard.html", elections=elections, voted=voted)


@app.route("/election/<int:election_id>")
@login_required
def view_election(election_id):
    db = get_db()
    election = db.execute(
        "SELECT * FROM Elections WHERE election_id=?", (election_id,)
    ).fetchone()

    if not election:
        flash("Election not found.", "danger")
        return redirect(url_for("dashboard"))

    if election["status"] != "active":
        flash("This election is not currently active.", "warning")
        return redirect(url_for("dashboard"))

    already_voted = db.execute(
        "SELECT vote_id FROM Votes WHERE user_id=? AND election_id=?",
        (session["user_id"], election_id)
    ).fetchone()

    candidates = db.execute(
        "SELECT * FROM Candidates WHERE election_id=?", (election_id,)
    ).fetchall()

    return render_template("vote.html", election=election,
                           candidates=candidates, already_voted=already_voted)


@app.route("/vote", methods=["POST"])
@login_required
def cast_vote():
    election_id  = request.form.get("election_id",  type=int)
    candidate_id = request.form.get("candidate_id", type=int)

    if not election_id or not candidate_id:
        flash("Invalid vote submission.", "danger")
        return redirect(url_for("dashboard"))

    db = get_db()

    try:
        db.execute("BEGIN")

        election = db.execute(
            "SELECT status FROM Elections WHERE election_id=?", (election_id,)
        ).fetchone()
        if not election or election["status"] != "active":
            db.execute("ROLLBACK")
            flash("Election is not active.", "danger")
            return redirect(url_for("dashboard"))

        candidate = db.execute(
            "SELECT candidate_id FROM Candidates WHERE candidate_id=? AND election_id=?",
            (candidate_id, election_id)
        ).fetchone()
        if not candidate:
            db.execute("ROLLBACK")
            flash("Invalid candidate for this election.", "danger")
            return redirect(url_for("view_election", election_id=election_id))

        db.execute(
            "INSERT INTO Votes(user_id,candidate_id,election_id) VALUES(?,?,?)",
            (session["user_id"], candidate_id, election_id)
        )
        db.execute("COMMIT")
        flash("Your vote has been cast successfully!", "success")

    except sqlite3.IntegrityError:
        db.execute("ROLLBACK")
        flash("You have already voted in this election.", "danger")

    except Exception as e:
        db.execute("ROLLBACK")
        flash(f"An error occurred: {str(e)}", "danger")

    return redirect(url_for("dashboard"))


@app.route("/results")
@login_required
def results():
    db = get_db()
    rows = db.execute("SELECT * FROM ElectionResults").fetchall()

    elections = {}
    for row in rows:
        eid = row["election_id"]
        if eid not in elections:
            elections[eid] = {
                "title":      row["election_title"],
                "status":     row["status"],
                "candidates": []
            }
        elections[eid]["candidates"].append({
            "name":        row["candidate_name"],
            "total_votes": row["total_votes"]
        })
    return render_template("results.html", elections=elections)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    db = get_db()
    admin_count = db.execute("SELECT COUNT(*) AS cnt FROM Admins").fetchone()["cnt"]
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pwd   = request.form.get("password", "")
        admin = db.execute("SELECT * FROM Admins WHERE email=?", (email,)).fetchone()

        if admin and check_password(pwd, admin["password"]):
            session["admin_id"]   = admin["admin_id"]
            session["admin_name"] = admin["name"]
            session["admin_role"] = admin["role"] if admin["role"] else "admin"
            return redirect(url_for("admin_panel"))

        flash("Invalid admin credentials.", "danger")
    return render_template("admin_login.html", has_admin=admin_count > 0)


@app.route("/admin/setup", methods=["GET", "POST"])
def admin_setup():
    db = get_db()
    admin_count = db.execute("SELECT COUNT(*) AS cnt FROM Admins").fetchone()["cnt"]
    if admin_count > 0:
        flash("Admin already exists. Please use admin login.", "warning")
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pwd = request.form.get("password", "")
        admin_key = request.form.get("admin_key", "")

        if not email or not pwd or not admin_key:
            flash("All fields are required.", "danger")
            return render_template("admin_setup.html")
        if ADMIN_SETUP_KEY == "CHANGE_ME_OWNER_KEY":
            flash("Set ADMIN_SETUP_KEY first before creating owner.", "danger")
            return render_template("admin_setup.html")
        if not hmac.compare_digest(admin_key, ADMIN_SETUP_KEY):
            flash("Invalid owner setup key.", "danger")
            return render_template("admin_setup.html")

        user = db.execute("SELECT * FROM Users WHERE email=?", (email,)).fetchone()
        if not user or not check_password(pwd, user["password"]):
            flash("Use registered voter email/password for owner setup.", "danger")
            return render_template("admin_setup.html")
        existing_admin = db.execute("SELECT admin_id FROM Admins WHERE email=?", (email,)).fetchone()
        if existing_admin:
            flash("This registered user is already an admin.", "warning")
            return redirect(url_for("admin_login"))

        db.execute(
            "INSERT INTO Admins(name,email,password,role) VALUES(?,?,?,?)",
            (user["name"], user["email"], user["password"], "owner")
        )
        db.commit()
        flash("Owner account created from registered user. Please log in.", "success")
        return redirect(url_for("admin_login"))

    return render_template("admin_setup.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    session.pop("admin_name", None)
    session.pop("admin_role", None)
    flash("Admin logged out.", "info")
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_panel():
    db = get_db()
    elections  = db.execute("SELECT * FROM Elections ORDER BY election_id DESC").fetchall()
    candidates = db.execute(
        "SELECT c.*, e.title AS etitle FROM Candidates c "
        "JOIN Elections e ON e.election_id=c.election_id ORDER BY c.election_id"
    ).fetchall()
    vote_counts = db.execute(
        "SELECT election_id, COUNT(*) as cnt FROM Votes GROUP BY election_id"
    ).fetchall()
    vc_map = {r["election_id"]: r["cnt"] for r in vote_counts}
    admins = db.execute(
        "SELECT admin_id, name, email, role FROM Admins ORDER BY "
        "CASE WHEN role='owner' THEN 0 ELSE 1 END, admin_id"
    ).fetchall()
    return render_template("admin.html", elections=elections,
                           candidates=candidates, vc_map=vc_map,
                           admins=admins, is_owner=session.get("admin_role") == "owner")


@app.route("/admin/promote", methods=["POST"])
@owner_required
def promote_admin():
    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("Registered user email is required.", "danger")
        return redirect(url_for("admin_panel"))
    db = get_db()
    user = db.execute("SELECT * FROM Users WHERE email=?", (email,)).fetchone()
    if not user:
        flash("No registered voter found with this email.", "danger")
        return redirect(url_for("admin_panel"))
    existing_admin = db.execute("SELECT admin_id FROM Admins WHERE email=?", (email,)).fetchone()
    if existing_admin:
        flash("This user is already an admin.", "warning")
        return redirect(url_for("admin_panel"))
    db.execute(
        "INSERT INTO Admins(name,email,password,role) VALUES(?,?,?,?)",
        (user["name"], user["email"], user["password"], "admin")
    )
    db.commit()
    flash("Registered user promoted to admin.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/transfer_owner", methods=["POST"])
@owner_required
def transfer_owner():
    new_owner_id = request.form.get("new_owner_id", type=int)
    if not new_owner_id:
        flash("Please select a target admin.", "danger")
        return redirect(url_for("admin_panel"))
    db = get_db()
    target = db.execute(
        "SELECT admin_id, name, role FROM Admins WHERE admin_id=?",
        (new_owner_id,)
    ).fetchone()
    if not target:
        flash("Selected admin does not exist.", "danger")
        return redirect(url_for("admin_panel"))
    current_admin_id = session.get("admin_id")
    if target["admin_id"] == current_admin_id:
        flash("You are already the owner.", "info")
        return redirect(url_for("admin_panel"))
    db.execute("BEGIN")
    db.execute("UPDATE Admins SET role='admin' WHERE admin_id=?", (current_admin_id,))
    db.execute("UPDATE Admins SET role='owner' WHERE admin_id=?", (target["admin_id"],))
    db.execute("COMMIT")
    session["admin_role"] = "admin"
    flash(f"Ownership transferred to {target['name']}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/create_election", methods=["POST"])
@admin_required
def create_election():
    title = request.form.get("title", "").strip()
    start = request.form.get("start_time", "")
    end   = request.form.get("end_time",   "")
    if not title or not start or not end:
        flash("All fields required.", "danger")
        return redirect(url_for("admin_panel"))
    if end <= start:
        flash("End time must be after start time.", "danger")
        return redirect(url_for("admin_panel"))
    db = get_db()
    db.execute(
        "INSERT INTO Elections(title,start_time,end_time,status) VALUES(?,?,?,'upcoming')",
        (title, start, end)
    )
    db.commit()
    flash(f"Election '{title}' created.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/add_candidate", methods=["POST"])
@admin_required
def add_candidate():
    name        = request.form.get("name", "").strip()
    election_id = request.form.get("election_id", type=int)
    if not name or not election_id:
        flash("All fields required.", "danger")
        return redirect(url_for("admin_panel"))
    db = get_db()
    db.execute(
        "INSERT INTO Candidates(name,election_id) VALUES(?,?)",
        (name, election_id)
    )
    db.commit()
    flash(f"Candidate '{name}' added.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/set_status/<int:election_id>/<status>")
@admin_required
def set_election_status(election_id, status):
    if status not in ("upcoming", "active", "ended"):
        flash("Invalid status.", "danger")
        return redirect(url_for("admin_panel"))
    db = get_db()
    db.execute(
        "UPDATE Elections SET status=? WHERE election_id=?",
        (status, election_id)
    )
    db.commit()
    flash("Election status updated.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/results")
@admin_required
def admin_results():
    db = get_db()
    rows = db.execute("SELECT * FROM ElectionResults").fetchall()
    elections = {}
    for row in rows:
        eid = row["election_id"]
        if eid not in elections:
            elections[eid] = {
                "title":      row["election_title"],
                "status":     row["status"],
                "candidates": []
            }
        elections[eid]["candidates"].append({
            "name":        row["candidate_name"],
            "total_votes": row["total_votes"]
        })
    return render_template("admin_results.html", elections=elections)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
