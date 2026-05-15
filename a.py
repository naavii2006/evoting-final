from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, session
import sqlite3
import random
import smtplib
import time
import os
from datetime import datetime, timedelta

app = Flask(__name__)

# Security Configuration
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "prod_secret_12345")
app.permanent_session_lifetime = timedelta(minutes=10)

# ========================================================
# DATABASE SYSTEM (Hardened for Production)
# ========================================================
DATABASE_URL = os.environ.get("DATABASE_URL")
IS_PRODUCTION = DATABASE_URL is not None

def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    """Helper function to handle DB connections and close them automatically."""
    try:
        if IS_PRODUCTION:
            import psycopg2
            import psycopg2.extras
            # Fix for Render/Heroku postgres prefix
            url = DATABASE_URL.replace("postgres://", "postgresql://", 1) if DATABASE_URL.startswith("postgres://") else DATABASE_URL
            # Added strict connect_timeout to prevent worker hangs
            conn = psycopg2.connect(url, sslmode="require", connect_timeout=10)
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            if params:
                query = query.replace("?", "%s")
        else:
            conn = sqlite3.connect("evoting.db")
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

        cur.execute(query, params or ())
        
        result = None
        if fetch_one:
            result = cur.fetchone()
        elif fetch_all:
            result = cur.fetchall()
        
        if commit:
            conn.commit()
        
        return result
    except Exception as e:
        if commit:
            conn.rollback()
        raise e
    finally:
        if 'conn' in locals():
            conn.close()

# ========================================================
# AUTO-INITIALIZATION
# ========================================================
@app.before_request
def initialize_database():
    """Runs only once on the first request to prevent boot-up timeouts."""
    if not getattr(app, '_db_setup_done', False):
        id_t = "SERIAL PRIMARY KEY" if IS_PRODUCTION else "INTEGER PRIMARY KEY AUTOINCREMENT"
        tables = [
            f"CREATE TABLE IF NOT EXISTS users(id {id_t}, name TEXT, username TEXT UNIQUE, email TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'voter', created_at TEXT)",
            f"CREATE TABLE IF NOT EXISTS elections(id {id_t}, title TEXT, description TEXT, candidate_deadline TEXT, vote_start TEXT, vote_end TEXT)",
            f"CREATE TABLE IF NOT EXISTS candidates(id {id_t}, user_id INTEGER, election_id INTEGER, manifesto TEXT, approved INTEGER DEFAULT 0)",
            f"CREATE TABLE IF NOT EXISTS votes(id {id_t}, user_id INTEGER, election_id INTEGER, candidate_id INTEGER, timestamp TEXT, UNIQUE(user_id, election_id))"
        ]
        try:
            for query in tables:
                execute_query(query, commit=True)
            app._db_setup_done = True
        except Exception as e:
            print(f"Database setup failed: {e}")

# ========================================================
# PUBLIC ROUTES
# ========================================================

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm = request.form.get("confirm_password")

        if password != confirm:
            return "Error: Passwords do not match"

        try:
            # Check if user exists
            exists = execute_query("SELECT id FROM users WHERE username = ? OR email = ?", (username, email), fetch_one=True)
            if exists:
                return "Error: Username or Email already registered"
            
            # Store in session for OTP stage
            session["temp_user"] = {
                "name": name, "username": username, "email": email,
                "password": generate_password_hash(password)
            }
            
            otp = str(random.randint(1000, 9999))
            session["reg_otp"] = otp
            session["otp_time"] = time.time()

            # SMTP with 5-second timeout to prevent "Worker Timeout"
            try:
                email_user = os.environ.get("EMAIL_USER")
                email_pass = os.environ.get("EMAIL_PASS")
                with smtplib.SMTP("smtp.gmail.com", 587, timeout=5) as server:
                    server.starttls()
                    server.login(email_user, email_pass)
                    server.sendmail(email_user, email, f"Subject: OTP Verification\n\nYour OTP is {otp}")
            except Exception as mail_err:
                return f"Registration halted. Could not send email. Error: {str(mail_err)}"

            return redirect("/verify_register")
        except Exception as db_err:
            return f"Database error during registration: {str(db_err)}"

    return render_template("auth/register.html")

@app.route("/verify_register", methods=["GET", "POST"])
def verify_register():
    if "reg_otp" not in session:
        return redirect("/register")

    if request.method == "POST":
        user_otp = request.form.get("otp")
        if user_otp == session["reg_otp"]:
            u = session["temp_user"]
            execute_query("""INSERT INTO users (name, username, email, password, role, created_at) 
                          VALUES (?, ?, ?, ?, ?, ?)""",
                          (u['name'], u['username'], u['email'], u['password'], 'voter', datetime.now().strftime("%Y-%m-%d")), 
                          commit=True)
            session.clear()
            return redirect("/login")
        return "Incorrect OTP. Please try again."

    return render_template("auth/verify_register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        un = request.form.get("username")
        pw = request.form.get("password")

        if un == "admin" and pw == "admin123":
            session.update({"username": "admin", "role": "admin"})
            return redirect("/admin")

        user = execute_query("SELECT * FROM users WHERE username = ?", (un,), fetch_one=True)
        if user and check_password_hash(user["password"], pw):
            session.update({"username": un, "role": user["role"]})
            return redirect("/dashboard")
        return "Invalid Credentials"

    return render_template("auth/login.html")

# ========================================================
# VOTER ROUTES
# ========================================================

@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect("/login")
    user = execute_query("SELECT * FROM users WHERE username = ?", (session["username"],), fetch_one=True)
    return render_template("voter/dashboard.html", user=user)

@app.route("/elections")
def elections():
    if "username" not in session: return redirect("/login")
    e_list = execute_query("SELECT * FROM elections", fetch_all=True)
    c_list = execute_query("""SELECT c.*, u.name FROM candidates c 
                           JOIN users u ON c.user_id = u.id WHERE c.approved = 1""", fetch_all=True)
    return render_template("voter/elections.html", elections=e_list, candidates=c_list)

@app.route("/vote/<int:election_id>", methods=["GET", "POST"])
def vote(election_id):
    if "username" not in session: return redirect("/login")
    user = execute_query("SELECT id FROM users WHERE username = ?", (session["username"],), fetch_one=True)
    
    if request.method == "POST":
        try:
            cand_id = request.form.get("candidate")
            execute_query("INSERT INTO votes (user_id, election_id, candidate_id, timestamp) VALUES (?, ?, ?, ?)",
                          (user['id'], election_id, cand_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")), commit=True)
            return "Success! Your vote has been recorded."
        except:
            return "Error: You have already voted in this election."

    cands = execute_query("""SELECT c.*, u.name FROM candidates c 
                          JOIN users u ON c.user_id = u.id 
                          WHERE election_id = ? AND approved = 1""", (election_id,), fetch_all=True)
    return render_template("elections/vote.html", candidates=cands, election_id=election_id)

# ========================================================
# ADMIN ROUTES
# ========================================================

@app.route("/admin")
def admin():
    if session.get("role") != "admin": return redirect("/login")
    return render_template("admin/admin_dashboard.html")

@app.route("/results")
def results():
    if session.get("role") != "admin": return redirect("/login")
    rows = execute_query("""
        SELECT e.title as election_title, u.name as candidate_name, COUNT(v.id) as vote_count
        FROM candidates c
        JOIN users u ON c.user_id = u.id
        JOIN elections e ON c.election_id = e.id
        LEFT JOIN votes v ON v.candidate_id = c.id
        GROUP BY c.id, e.title, u.name, e.id
    """, fetch_all=True)
    
    e_dict = {}
    for r in rows:
        if r['election_title'] not in e_dict: e_dict[r['election_title']] = []
        e_dict[r['election_title']].append(r)
    return render_template("admin/results.html", elections=e_dict)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
