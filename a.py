from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, session
import random
import smtplib
import time
import os
from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras

app = Flask(__name__)

# Production Secret Key
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "secret123")
app.permanent_session_lifetime = timedelta(minutes=10)

# ========================================================
# 1. DATABASE CONNECTION CONFIGURATION
# ========================================================
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    if DATABASE_URL:
        # Production: Cloud PostgreSQL (Using psycopg2)
        
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
            
        conn = psycopg2.connect(url, sslmode="require")
        # DictCursor allows accessing columns by name: user["username"]
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        return conn, cursor
    else:
        # Development: Local Laptop SQLite
        import sqlite3
        conn = sqlite3.connect("evoting.db")
        conn.row_factory = sqlite3.Row
        return conn, conn.cursor()

def get_p():
    """Returns %s for PostgreSQL (Cloud) or ? for SQLite (Local)"""
    return "%s" if DATABASE_URL else "?"

# ========================================================
# 2. DATABASE INITIALIZATION (Runs on Startup)
# ========================================================
conn, cursor = get_db()
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
)""")

cursor.execute(f"""
CREATE TABLE IF NOT EXISTS elections(
    id {id_type},
    title TEXT,
    description TEXT,
    candidate_deadline TEXT,
    vote_start TEXT,
    vote_end TEXT
)""")

cursor.execute(f"""
CREATE TABLE IF NOT EXISTS candidates(
    id {id_type},
    user_id INTEGER,
    election_id INTEGER,
    manifesto TEXT,
    approved INTEGER DEFAULT 0
)""")

cursor.execute(f"""
CREATE TABLE IF NOT EXISTS votes(
    id {id_type},
    user_id INTEGER,
    election_id INTEGER,
    candidate_id INTEGER,
    timestamp TEXT,
    UNIQUE(user_id, election_id)
)""")

conn.commit()
conn.close()

# ========================================================
# 3. PUBLIC ROUTES (Home, Register, Login)
# ========================================================

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    p = get_p()
    if request.method == "POST":
        name = request.form.get("name")
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm = request.form.get("confirm_password")

        if password != confirm:
            return "Passwords do not match"

        conn, cursor = get_db()
        cursor.execute(f"SELECT * FROM users WHERE username={p}", (username,))
        if cursor.fetchone():
            conn.close()
            return "Username already exists"

        cursor.execute(f"SELECT * FROM users WHERE email={p}", (email,))
        if cursor.fetchone():
            conn.close()
            return "Email already exists"
        
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

@app.route("/verify_register", methods=["GET", "POST"])
def verify_register():
    p = get_p()
    if "reg_otp" not in session:
        return redirect("/register")

    if time.time() - session["otp_time"] > 300:
        session.clear()
        return "OTP expired. Please register again."

    if request.method == "POST":
        if request.form.get("otp") != session["reg_otp"]:
            return "Incorrect OTP"

        user = session["temp_user"]
        conn, cursor = get_db()
        cursor.execute(f"""
            INSERT INTO users (name, username, email, password, role, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p})
        """, (user["name"], user["username"], user["email"], user["password"], "voter", datetime.now().strftime("%Y-%m-%d")))

        conn.commit()
        conn.close()
        session.clear()
        return redirect("/login")

    return render_template("auth/verify_register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    p = get_p()
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # Hardcoded Admin Login for quick access
        if username == "admin" and password == "admin123":
            session["username"] = "admin"
            session["role"] = "admin"
            return redirect("/admin")

        conn, cursor = get_db()
        cursor.execute(f"SELECT * FROM users WHERE username={p}", (username,))
        user = cursor.fetchone()
        conn.close()

        if not user or not check_password_hash(user["password"], password):
            return "Invalid login credentials"

        session["username"] = username
        session["role"] = user["role"]
        return redirect("/dashboard")

    return render_template("auth/login.html")

# ========================================================
# 4. VOTER ROUTES (Dashboard, Profile, Voting)
# ========================================================

@app.route("/dashboard")
def dashboard():
    p = get_p()
    if "username" not in session: return redirect("/login")
    conn, cursor = get_db()
    cursor.execute(f"SELECT * FROM users WHERE username={p}", (session["username"],))
    user = cursor.fetchone()
    conn.close()
    return render_template("voter/dashboard.html", user=user)

@app.route("/profile")
def profile():
    p = get_p()
    if "username" not in session: return redirect("/login")
    conn, cursor = get_db()
    cursor.execute(f"SELECT * FROM users WHERE username={p}", (session["username"],))
    user = cursor.fetchone()
    conn.close()
    return render_template("voter/profile.html", user=user)

@app.route("/elections")
def elections():
    if "username" not in session: return redirect("/login")
    conn, cursor = get_db()
    cursor.execute("SELECT * FROM elections")
    elections_list = cursor.fetchall()
    
    # Get approved candidates
    cursor.execute("""
        SELECT candidates.*, users.name 
        FROM candidates 
        JOIN users ON candidates.user_id = users.id 
        WHERE approved = 1
    """)
    candidates_list = cursor.fetchall()
    conn.close()
    return render_template("voter/elections.html", elections=elections_list, candidates=candidates_list)

@app.route("/apply")
def apply():
    if "username" not in session: return redirect("/login")
    conn, cursor = get_db()
    cursor.execute("SELECT * FROM elections")
    elections_list = cursor.fetchall()
    conn.close()
    return render_template("candidate/apply_candidate.html", elections=elections_list)

@app.route("/apply_candidate", methods=["POST"])
def apply_candidate():
    p = get_p()
    if "username" not in session: return redirect("/login")
    manifesto = request.form.get("manifesto")
    election_id = request.form.get("election_id")

    conn, cursor = get_db()
    cursor.execute(f"SELECT candidate_deadline FROM elections WHERE id={p}", (election_id,))
    election = cursor.fetchone()
    
    if not election:
        conn.close()
        return "Election not found"

    deadline = datetime.strptime(election["candidate_deadline"], "%Y-%m-%d")
    if datetime.now() > deadline:
        conn.close()
        return "Candidate application deadline has passed"

    cursor.execute(f"SELECT id FROM users WHERE username={p}", (session["username"],))
    user_id = cursor.fetchone()["id"]

    cursor.execute(f"SELECT * FROM candidates WHERE user_id={p} AND election_id={p}", (user_id, election_id))
    if cursor.fetchone():
        conn.close()
        return "You have already applied for this election"

    cursor.execute(f"INSERT INTO candidates (user_id, election_id, manifesto, approved) VALUES ({p}, {p}, {p}, 0)", 
                   (user_id, election_id, manifesto))
    conn.commit()
    conn.close()
    return "Application sent to admin for approval"

@app.route("/vote/<int:election_id>", methods=["GET", "POST"])
def vote(election_id):
    p = get_p()
    if "username" not in session: return redirect("/login")
    conn, cursor = get_db()
    
    # Get User ID
    cursor.execute(f"SELECT id FROM users WHERE username={p}", (session["username"],))
    user_id = cursor.fetchone()["id"]

    if request.method == "POST":
        candidate_id = request.form.get("candidate")
        try:
            cursor.execute(f"""
                INSERT INTO votes (user_id, election_id, candidate_id, timestamp) 
                VALUES ({p}, {p}, {p}, {p})
            """, (user_id, election_id, candidate_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        except:
            conn.close()
            return "You have already voted in this election"
        conn.close()
        return "Vote submitted successfully"

    # GET: Show candidates for this election
    cursor.execute(f"""
        SELECT candidates.*, users.name 
        FROM candidates 
        JOIN users ON candidates.user_id = users.id 
        WHERE election_id={p} AND approved=1
    """, (election_id,))
    candidates_list = cursor.fetchall()
    conn.close()
    return render_template("elections/vote.html", candidates=candidates_list, election_id=election_id)

# ========================================================
# 5. ADMIN ROUTES (Manage Elections, Candidates, Results)
# ========================================================

@app.route("/admin")
def admin():
    if session.get("role") != "admin": return redirect("/login")
    return render_template("admin/admin_dashboard.html")

@app.route("/create_election", methods=["GET", "POST"])
def create_election():
    p = get_p()
    if session.get("role") != "admin": return redirect("/login")
    if request.method == "POST":
        title = request.form.get("title")
        description = request.form.get("description")
        candidate_deadline = request.form.get("candidate_deadline")
        vote_start = request.form.get("vote_start")
        vote_end = request.form.get("vote_end")

        conn, cursor = get_db()
        cursor.execute(f"""
            INSERT INTO elections (title, description, candidate_deadline, vote_start, vote_end) 
            VALUES ({p}, {p}, {p}, {p}, {p})
        """, (title, description, candidate_deadline, vote_start, vote_end))
        conn.commit()
        conn.close()
        return redirect("/admin")
    return render_template("admin/create_election.html")

@app.route("/manage_candidates")
def manage_candidates():
    if session.get("role") != "admin": return redirect("/login")
    conn, cursor = get_db()
    cursor.execute("""
        SELECT candidates.id, users.name, elections.title, candidates.approved 
        FROM candidates 
        JOIN users ON candidates.user_id=users.id 
        JOIN elections ON candidates.election_id=elections.id
    """)
    candidates_list = cursor.fetchall()
    conn.close()
    return render_template("admin/manage_candidates.html", candidates=candidates_list)

@app.route("/approve_candidate/<int:candidate_id>")
def approve_candidate(candidate_id):
    p = get_p()
    if session.get("role") != "admin": return redirect("/login")
    conn, cursor = get_db()
    cursor.execute(f"UPDATE candidates SET approved=1 WHERE id={p}", (candidate_id,))
    conn.commit()
    conn.close()
    return redirect("/manage_candidates")

@app.route("/results")
def results():
    if session.get("role") != "admin": return redirect("/login")
    conn, cursor = get_db()
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
    
    # Organize data by election title
    elections_dict = {}
    for r in rows:
        title = r["election_title"]
        if title not in elections_dict:
            elections_dict[title] = []
        elections_dict[title].append(r)
    return render_template("admin/results.html", elections=elections_dict)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ========================================================
# 6. SERVER START
# ========================================================
if __name__ == "__main__":
    # Render assigns a port dynamically; fallback to 5000 for local testing
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
