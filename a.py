from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, session
import sqlite3
import random
import smtplib
import time
import os  # <-- Crucial for reading environment variables on the cloud
from datetime import datetime, timedelta

app = Flask(__name__)

# Production Secret Key: Uses a fallback secret locally, but picks up a secure cloud one if available
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "secret123")
app.permanent_session_lifetime = timedelta(minutes=10)

# ========================================================
# 1. PRODUCTION-READY DATABASE CONNECTION
# ========================================================
# Looks for a cloud PostgreSQL URL. If not found, defaults to local SQLite.
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    if DATABASE_URL:
        # Production: Cloud PostgreSQL (Using psycopg2)
        import psycopg2
        import psycopg2.extras
        
        # Adjusting Render/Supabase connection string quirks if necessary
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
            
        conn = psycopg2.connect(url, sslmode="require")
        # Makes PostgreSQL results accessible by column names like sqlite3.Row
        conn.cursor_factory = psycopg2.extras.DictCursor
        return conn
    else:
        # Development: Local Laptop SQLite
        conn = sqlite3.connect("evoting.db")
        conn.row_factory = sqlite3.Row
        return conn


# ========================================================
# 2. DATABASE INITIALIZATION (SQL Dialect Agnostic)
# ========================================================
# The table definitions below are modified to work perfectly on BOTH SQLite and PostgreSQL.
conn = get_db()
cursor = conn.cursor()

# PostgreSQL requires SERIAL for autoincrementing IDs
id_type = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"

cursor.execute(f"""
CREATE TABLE IF NOT EXISTS users(
    id {id_type},
    name TEXT,
    username TEXT UNIQUE,
    email TEXT UNIQUE,
    password TEXT,
    role TEXT DEFAULT 'voter',
    created_at TEXT
)
""")

cursor.execute(f"""
CREATE TABLE IF NOT EXISTS elections(
    id {id_type},
    title TEXT,
    description TEXT,
    candidate_deadline TEXT,
    vote_start TEXT,
    vote_end TEXT
)
""")

cursor.execute(f"""
CREATE TABLE IF NOT EXISTS candidates(
    id {id_type},
    user_id INTEGER,
    election_id INTEGER,
    manifesto TEXT,
    approved INTEGER DEFAULT 0
)
""")

cursor.execute(f"""
CREATE TABLE IF NOT EXISTS votes(
    id {id_type},
    user_id INTEGER,
    election_id INTEGER,
    candidate_id INTEGER,
    timestamp TEXT,
    UNIQUE(user_id, election_id)
)
""")

conn.commit()
conn.close()


# =========================
# HOME
# =========================

@app.route("/")
def home():
    return render_template("index.html")


# ========================================================
# 3. REGISTER (Secured Email Setup)
# ========================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm = request.form.get("confirm_password")

        if password != confirm:
            return "Passwords do not match"

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE username=?", (username,))
        if cursor.fetchone():
            conn.close()
            return "Username exists"

        cursor.execute("SELECT * FROM users WHERE email=?", (email,))
        if cursor.fetchone():
            conn.close()
            return "Email exists"
        
        conn.close()

        session["temp_user"] = {
            "name": name,
            "username": username,
            "email": email,
            "password": generate_password_hash(password)
        }

        otp = str(random.randint(1000, 9999))
        session["reg_otp"] = otp
        session["otp_time"] = time.time()

        # Securely fetch credentials from cloud variables, fallback to your defaults locally
        email_user = os.environ.get("EMAIL_USER", "lightphoton3108@gmail.com")
        email_pass = os.environ.get("EMAIL_PASS", "drto uobo fiyc slhd")

        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(email_user, email_pass)

            message = f"Subject: Email Verification OTP\n\nYour OTP is {otp}\n\nValid for 5 minutes"

            server.sendmail(email_user, email, message)
            server.quit()
        except Exception as e:
            return f"Failed to send verification email: {str(e)}"

        return redirect("/verify_register")

    return render_template("auth/register.html")


# =========================
# VERIFY REGISTER OTP
# =========================

@app.route("/verify_register", methods=["GET", "POST"])
def verify_register():
    if "reg_otp" not in session:
        return redirect("/register")

    if time.time() - session["otp_time"] > 300:
        session.clear()
        return "OTP expired"

    if request.method == "POST":
        if request.form.get("otp") != session["reg_otp"]:
            return "Wrong OTP"

        user = session["temp_user"]

        conn = get_db()
        cursor = conn.cursor()

        # Note: Added explicit column definitions to ensure safety across database types
        cursor.execute("""
            INSERT INTO users (name, username, email, password, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user["name"],
            user["username"],
            user["email"],
            user["password"],
            "voter",
            datetime.now().strftime("%Y-%m-%d")
        ))

        conn.commit()
        conn.close()

        session.clear()
        return redirect("/login")

    return render_template("auth/verify_register.html")


# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # ADMIN LOGIN
        if username == "admin" and password == "admin123":
            session["username"] = "admin"
            session["role"] = "admin"
            return redirect("/admin")

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cursor.fetchone()
        conn.close()

        if not user:
            return "Invalid login"

        if check_password_hash(user["password"], password):
            session["username"] = username
            session["role"] = "voter"
            return redirect("/dashboard")

        return "Invalid password"

    return render_template("auth/login.html")


# =========================
# DASHBOARD
# =========================

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username=?", (session["username"],))
    user = cursor.fetchone()
    conn.close()

    return render_template("voter/dashboard.html", user=user)


# =========================
# PROFILE
# =========================

@app.route("/profile")
def profile():
    if "username" not in session:
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username=?", (session["username"],))
    user = cursor.fetchone()
    conn.close()

    return render_template("voter/profile.html", user=user)


# =========================
# VIEW ELECTIONS
# =========================

@app.route("/elections")
def elections():
    if "username" not in session:
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM elections")
    elections_list = cursor.fetchall()

    cursor.execute("""
        SELECT candidates.*, users.name
        FROM candidates
        JOIN users ON candidates.user_id = users.id
        WHERE approved = 1
    """)
    candidates_list = cursor.fetchall()

    conn.close()

    return render_template(
        "voter/elections.html",
        elections=elections_list,
        candidates=candidates_list
    )


# =========================
# APPLY PAGE
# =========================

@app.route("/apply")
def apply():
    if "username" not in session:
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM elections")
    elections_list = cursor.fetchall()
    conn.close()

    return render_template("candidate/apply_candidate.html", elections=elections_list)


# =========================
# APPLY CANDIDATE
# =========================

@app.route("/apply_candidate", methods=["POST"])
def apply_candidate():
    if "username" not in session:
        return redirect("/login")

    manifesto = request.form.get("manifesto")
    election_id = request.form.get("election_id")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT candidate_deadline FROM elections WHERE id=?", (election_id,))
    election = cursor.fetchone()

    if not election:
        conn.close()
        return "Election not found"

    deadline = datetime.strptime(election["candidate_deadline"], "%Y-%m-%d")

    if datetime.now() > deadline:
        conn.close()
        return "Candidate application deadline has passed"

    cursor.execute("SELECT id FROM users WHERE username=?", (session["username"],))
    user = cursor.fetchone()

    if not user:
        conn.close()
        return "User not found"

    user_id = user["id"]  # Accessing by column key instead of index for consistency

    cursor.execute("SELECT * FROM candidates WHERE user_id=? AND election_id=?", (user_id, election_id))

    if cursor.fetchone():
        conn.close()
        return "You already applied for this election"

    cursor.execute("""
        INSERT INTO candidates (user_id, election_id, manifesto, approved)
        VALUES (?, ?, ?, 0)
    """, (user_id, election_id, manifesto))

    conn.commit()
    conn.close()

    return "Application sent to admin for approval"


# =========================
# VOTE
# =========================

@app.route("/vote/<int:election_id>", methods=["GET", "POST"])
def vote(election_id):
    if "username" not in session:
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT vote_start, vote_end FROM elections WHERE id=?", (election_id,))
    election = cursor.fetchone()

    vote_start = datetime.strptime(election["vote_start"], "%Y-%m-%d")
    vote_end = datetime.strptime(election["vote_end"], "%Y-%m-%d")

    if datetime.now() < vote_start:
        conn.close()
        return "Voting has not started yet"

    if datetime.now() > vote_end:
        conn.close()
        return "Voting has ended"

    cursor.execute("SELECT id FROM users WHERE username=?", (session["username"],))
    user_id = cursor.fetchone()["id"]

    if request.method == "POST":
        candidate_id = request.form.get("candidate")

        try:
            cursor.execute("""
                INSERT INTO votes (user_id, election_id, candidate_id, timestamp)
                VALUES (?, ?, ?, ?)
            """, (user_id, election_id, candidate_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        except Exception:
            conn.close()
            return "You already voted in this election"

        conn.close()
        return "Vote submitted successfully"

    cursor.execute("""
        SELECT candidates.*, users.name
        FROM candidates
        JOIN users ON candidates.user_id = users.id
        WHERE election_id=? AND approved=1
    """, (election_id,))

    candidates_list = cursor.fetchall()
    conn.close()

    return render_template("elections/vote.html", candidates=candidates_list)


# =========================
# ADMIN PANEL
# =========================

@app.route("/admin")
def admin():
    if session.get("role") != "admin":
        return redirect("/login")

    return render_template("admin/admin_dashboard.html")


# =========================
# CREATE ELECTION
# =========================

@app.route("/create_election", methods=["GET", "POST"])
def create_election():
    if session.get("role") != "admin":
        return redirect("/login")

    if request.method == "POST":
        title = request.form.get("title")
        description = request.form.get("description")
        candidate_deadline = request.form.get("candidate_deadline")
        vote_start = request.form.get("vote_start")
        vote_end = request.form.get("vote_end")

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO elections (title, description, candidate_deadline, vote_start, vote_end)
            VALUES (?, ?, ?, ?, ?)
        """, (title, description, candidate_deadline, vote_start, vote_end))

        conn.commit()
        conn.close()

        return redirect("/admin")

    return render_template("admin/create_election.html")


# =========================
# MANAGE CANDIDATES
# =========================

@app.route("/manage_candidates")
def manage_candidates():
    if session.get("role") != "admin":
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT candidates.id, users.name, elections.title, candidates.approved
        FROM candidates
        JOIN users ON candidates.user_id=users.id
        JOIN elections ON candidates.election_id=elections.id
    """)

    candidates_list = cursor.fetchall()
    conn.close()

    return render_template("admin/manage_candidates.html", candidates=candidates_list)


# =========================
# APPROVE CANDIDATE
# =========================

@app.route("/approve_candidate/<int:candidate_id>")
def approve_candidate(candidate_id):
    if session.get("role") != "admin":
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE candidates SET approved=1 WHERE id=?", (candidate_id,))
    conn.commit()
    conn.close()

    return redirect("/manage_candidates")


# =========================
# RESULTS
# =========================

@app.route("/results")
def results():
    if session.get("role") != "admin":
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            elections.title AS election_title,
            users.name AS candidate_name,
            candidates.manifesto,
            COUNT(votes.id) AS vote_count
        FROM candidates
        JOIN users ON candidates.user_id = users.id
        JOIN elections ON candidates.election_id = elections.id
        LEFT JOIN votes ON votes.candidate_id = candidates.id
        GROUP BY candidates.id, elections.title, users.name, candidates.manifesto, elections.id
        ORDER BY elections.id
    """)

    rows = cursor.fetchall()
    conn.close()

    elections_dict = {}
    for r in rows:
        election = r["election_title"]
        if election not in elections_dict:
            elections_dict[election] = []
        elections_dict[election].append(r)

    return render_template("admin/results.html", elections=elections_dict)


# =========================
# LOGOUT
# =========================

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ========================================================
# 4. DYNAMIC PORT BINDING FOR CLOUD HOSTS
# ========================================================
if __name__ == "__main__":
    # Cloud environments dynamically pass assigned routing ports through OS environments
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)