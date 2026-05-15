from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, session
import sqlite3
import random
import smtplib
import time
import os
from datetime import datetime, timedelta

app = Flask(__name__)

# Production Secret Key
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "secret123")
app.permanent_session_lifetime = timedelta(minutes=10)

# ========================================================
# DATABASE CONFIGURATION
# ========================================================
DATABASE_URL = os.environ.get("DATABASE_URL")
IS_PRODUCTION = DATABASE_URL is not None

def get_db():
    """Get database connection"""
    if IS_PRODUCTION:
        # PostgreSQL for production
        import psycopg2
        import psycopg2.extras
        
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        
        conn = psycopg2.connect(url, sslmode="require")
        conn.cursor_factory = psycopg2.extras.DictCursor
        return conn
    else:
        # SQLite for development
        conn = sqlite3.connect("evoting.db")
        conn.row_factory = sqlite3.Row
        return conn

def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    """Execute query with automatic placeholder conversion"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Convert ? to %s for PostgreSQL
    if IS_PRODUCTION and params:
        query = query.replace("?", "%s")
    
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        result = None
        if fetch_one:
            result = cursor.fetchone()
        elif fetch_all:
            result = cursor.fetchall()
        
        if commit:
            conn.commit()
        
        return result, conn
    except Exception as e:
        conn.rollback()
        conn.close()
        raise e

# ========================================================
# DATABASE INITIALIZATION
# ========================================================
def init_db():
    """Initialize database tables"""
    conn = get_db()
    cursor = conn.cursor()
    
    if IS_PRODUCTION:
        # PostgreSQL table creation
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id SERIAL PRIMARY KEY,
                name TEXT,
                username TEXT UNIQUE,
                email TEXT UNIQUE,
                password TEXT,
                role TEXT DEFAULT 'voter',
                created_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS elections(
                id SERIAL PRIMARY KEY,
                title TEXT,
                description TEXT,
                candidate_deadline TEXT,
                vote_start TEXT,
                vote_end TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candidates(
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                election_id INTEGER,
                manifesto TEXT,
                approved INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS votes(
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                election_id INTEGER,
                candidate_id INTEGER,
                timestamp TEXT,
                UNIQUE(user_id, election_id)
            )
        """)
    else:
        # SQLite table creation
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                username TEXT UNIQUE,
                email TEXT UNIQUE,
                password TEXT,
                role TEXT DEFAULT 'voter',
                created_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS elections(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT,
                candidate_deadline TEXT,
                vote_start TEXT,
                vote_end TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candidates(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                election_id INTEGER,
                manifesto TEXT,
                approved INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS votes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                election_id INTEGER,
                candidate_id INTEGER,
                timestamp TEXT,
                UNIQUE(user_id, election_id)
            )
        """)
    
    conn.commit()
    conn.close()

# Initialize database when app starts
init_db()

# ========================================================
# ROUTES
# ========================================================

@app.route("/")
def home():
    try:
        return render_template("index.html")
    except:
        return "E-Voting System is running! (Template missing)"

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            name = request.form.get("name")
            username = request.form.get("username")
            email = request.form.get("email")
            password = request.form.get("password")
            confirm = request.form.get("confirm_password")

            if password != confirm:
                return "Passwords do not match"

            # Check if username exists
            user_result, conn1 = execute_query(
                "SELECT * FROM users WHERE username = ?", 
                (username,), 
                fetch_one=True
            )
            if user_result:
                conn1.close()
                return "Username exists"
            conn1.close()

            # Check if email exists
            email_result, conn2 = execute_query(
                "SELECT * FROM users WHERE email = ?", 
                (email,), 
                fetch_one=True
            )
            if email_result:
                conn2.close()
                return "Email exists"
            conn2.close()

            # Store in session for OTP verification
            session["temp_user"] = {
                "name": name,
                "username": username,
                "email": email,
                "password": generate_password_hash(password)
            }

            # Generate and send OTP
            otp = str(random.randint(1000, 9999))
            session["reg_otp"] = otp
            session["otp_time"] = time.time()

            # Send email
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
        except Exception as e:
            return f"Error in registration: {str(e)}"

    return render_template("auth/register.html")

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

        try:
            _, conn = execute_query("""
                INSERT INTO users (name, username, email, password, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user["name"],
                user["username"],
                user["email"],
                user["password"],
                "voter",
                datetime.now().strftime("%Y-%m-%d")
            ), commit=True)
            conn.close()
        except Exception as e:
            return f"Error saving user: {str(e)}"

        session.clear()
        return redirect("/login")

    return render_template("auth/verify_register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            username = request.form.get("username")
            password = request.form.get("password")

            # Admin login
            if username == "admin" and password == "admin123":
                session["username"] = "admin"
                session["role"] = "admin"
                return redirect("/admin")

            user_result, conn = execute_query(
                "SELECT * FROM users WHERE username = ?", 
                (username,), 
                fetch_one=True
            )
            
            if not user_result:
                conn.close()
                return "Invalid login"

            if check_password_hash(user_result["password"], password):
                session["username"] = username
                session["role"] = user_result["role"]
                conn.close()
                return redirect("/dashboard")

            conn.close()
            return "Invalid password"
        except Exception as e:
            return f"Login error: {str(e)}"

    return render_template("auth/login.html")

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect("/login")
    
    try:
        user_result, conn = execute_query(
            "SELECT * FROM users WHERE username = ?", 
            (session["username"],), 
            fetch_one=True
        )
        conn.close()
        return render_template("voter/dashboard.html", user=user_result)
    except:
        return f"Welcome {session['username']}! (Dashboard template missing)"

@app.route("/profile")
def profile():
    if "username" not in session:
        return redirect("/login")
    
    try:
        user_result, conn = execute_query(
            "SELECT * FROM users WHERE username = ?", 
            (session["username"],), 
            fetch_one=True
        )
        conn.close()
        return render_template("voter/profile.html", user=user_result)
    except:
        return f"Profile for {session['username']} (Template missing)"

@app.route("/elections")
def elections():
    if "username" not in session:
        return redirect("/login")
    
    try:
        elections_result, conn1 = execute_query("SELECT * FROM elections", fetch_all=True)
        
        candidates_result, conn2 = execute_query("""
            SELECT candidates.*, users.name
            FROM candidates
            JOIN users ON candidates.user_id = users.id
            WHERE approved = 1
        """, fetch_all=True)
        
        conn1.close()
        conn2.close()
        
        return render_template("voter/elections.html", elections=elections_result, candidates=candidates_result)
    except Exception as e:
        return f"Elections page error: {str(e)}"

@app.route("/apply")
def apply():
    if "username" not in session:
        return redirect("/login")
    
    try:
        elections_result, conn = execute_query("SELECT * FROM elections", fetch_all=True)
        conn.close()
        return render_template("candidate/apply_candidate.html", elections=elections_result)
    except:
        return "Apply for candidacy (Template missing)"

@app.route("/apply_candidate", methods=["POST"])
def apply_candidate():
    if "username" not in session:
        return redirect("/login")
    
    try:
        manifesto = request.form.get("manifesto")
        election_id = request.form.get("election_id")

        # Check deadline
        election_result, conn1 = execute_query(
            "SELECT candidate_deadline FROM elections WHERE id = ?", 
            (election_id,), 
            fetch_one=True
        )

        if not election_result:
            conn1.close()
            return "Election not found"

        deadline = datetime.strptime(election_result["candidate_deadline"], "%Y-%m-%d")

        if datetime.now() > deadline:
            conn1.close()
            return "Candidate application deadline has passed"
        conn1.close()

        # Get user id
        user_result, conn2 = execute_query(
            "SELECT id FROM users WHERE username = ?", 
            (session["username"],), 
            fetch_one=True
        )

        if not user_result:
            conn2.close()
            return "User not found"

        user_id = user_result["id"]
        conn2.close()

        # Check if already applied
        existing_result, conn3 = execute_query(
            "SELECT * FROM candidates WHERE user_id = ? AND election_id = ?", 
            (user_id, election_id), 
            fetch_one=True
        )

        if existing_result:
            conn3.close()
            return "You already applied for this election"
        conn3.close()

        # Insert application
        _, conn4 = execute_query("""
            INSERT INTO candidates (user_id, election_id, manifesto, approved)
            VALUES (?, ?, ?, 0)
        """, (user_id, election_id, manifesto), commit=True)
        conn4.close()

        return "Application sent to admin for approval"
    except Exception as e:
        return f"Error applying as candidate: {str(e)}"

@app.route("/vote/<int:election_id>", methods=["GET", "POST"])
def vote(election_id):
    if "username" not in session:
        return redirect("/login")
    
    try:
        # Get election dates
        election_result, conn1 = execute_query(
            "SELECT vote_start, vote_end FROM elections WHERE id = ?", 
            (election_id,), 
            fetch_one=True
        )

        vote_start = datetime.strptime(election_result["vote_start"], "%Y-%m-%d")
        vote_end = datetime.strptime(election_result["vote_end"], "%Y-%m-%d")

        if datetime.now() < vote_start:
            conn1.close()
            return "Voting has not started yet"

        if datetime.now() > vote_end:
            conn1.close()
            return "Voting has ended"
        conn1.close()

        # Get user id
        user_result, conn2 = execute_query(
            "SELECT id FROM users WHERE username = ?", 
            (session["username"],), 
            fetch_one=True
        )
        user_id = user_result["id"]
        conn2.close()

        if request.method == "POST":
            candidate_id = request.form.get("candidate")

            try:
                _, conn3 = execute_query("""
                    INSERT INTO votes (user_id, election_id, candidate_id, timestamp)
                    VALUES (?, ?, ?, ?)
                """, (user_id, election_id, candidate_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")), commit=True)
                conn3.close()
            except Exception:
                return "You already voted in this election"

            return "Vote submitted successfully"

        # Get candidates for this election
        candidates_result, conn4 = execute_query("""
            SELECT candidates.*, users.name
            FROM candidates
            JOIN users ON candidates.user_id = users.id
            WHERE election_id = ? AND approved = 1
        """, (election_id,), fetch_all=True)
        conn4.close()

        return render_template("elections/vote.html", candidates=candidates_result)
    except Exception as e:
        return f"Voting error: {str(e)}"

@app.route("/admin")
def admin():
    if session.get("role") != "admin":
        return redirect("/login")
    
    try:
        return render_template("admin/admin_dashboard.html")
    except:
        return "Admin Dashboard (Template missing)"

@app.route("/create_election", methods=["GET", "POST"])
def create_election():
    if session.get("role") != "admin":
        return redirect("/login")
    
    if request.method == "POST":
        try:
            title = request.form.get("title")
            description = request.form.get("description")
            candidate_deadline = request.form.get("candidate_deadline")
            vote_start = request.form.get("vote_start")
            vote_end = request.form.get("vote_end")

            _, conn = execute_query("""
                INSERT INTO elections (title, description, candidate_deadline, vote_start, vote_end)
                VALUES (?, ?, ?, ?, ?)
            """, (title, description, candidate_deadline, vote_start, vote_end), commit=True)
            conn.close()

            return redirect("/admin")
        except Exception as e:
            return f"Error creating election: {str(e)}"
    
    try:
        return render_template("admin/create_election.html")
    except:
        return "Create Election Form (Template missing)"

@app.route("/manage_candidates")
def manage_candidates():
    if session.get("role") != "admin":
        return redirect("/login")
    
    try:
        candidates_result, conn = execute_query("""
            SELECT candidates.id, users.name, elections.title, candidates.approved
            FROM candidates
            JOIN users ON candidates.user_id = users.id
            JOIN elections ON candidates.election_id = elections.id
        """, fetch_all=True)
        conn.close()
        
        return render_template("admin/manage_candidates.html", candidates=candidates_result)
    except Exception as e:
        return f"Manage candidates error: {str(e)}"

@app.route("/approve_candidate/<int:candidate_id>")
def approve_candidate(candidate_id):
    if session.get("role") != "admin":
        return redirect("/login")
    
    try:
        _, conn = execute_query(
            "UPDATE candidates SET approved = 1 WHERE id = ?", 
            (candidate_id,), 
            commit=True
        )
        conn.close()
        
        return redirect("/manage_candidates")
    except Exception as e:
        return f"Error approving candidate: {str(e)}"

@app.route("/results")
def results():
    if session.get("role") != "admin":
        return redirect("/login")
    
    try:
        rows_result, conn = execute_query("""
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
        """, fetch_all=True)
        conn.close()

        elections_dict = {}
        for r in rows_result:
            election = r["election_title"]
            if election not in elections_dict:
                elections_dict[election] = []
            elections_dict[election].append(r)

        return render_template("admin/results.html", elections=elections_dict)
    except Exception as e:
        return f"Results error: {str(e)}"

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/health")
def health():
    """Health check endpoint for Render"""
    return {"status": "healthy", "database": "postgresql" if IS_PRODUCTION else "sqlite"}

# ========================================================
# RUN THE APP
# ========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
