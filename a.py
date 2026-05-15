from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, session, jsonify
import os
import random
import time
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import DictCursor
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "your-secret-key-change-this")
app.permanent_session_lifetime = timedelta(minutes=10)

DATABASE_URL = os.environ.get("DATABASE_URL")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")

def get_db_connection():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url, sslmode="require")
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id SERIAL PRIMARY KEY,
            name TEXT,
            username TEXT UNIQUE,
            email TEXT UNIQUE,
            password TEXT,
            role TEXT DEFAULT 'voter',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_verified BOOLEAN DEFAULT FALSE
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS elections(
            id SERIAL PRIMARY KEY,
            title TEXT,
            description TEXT,
            candidate_deadline DATE,
            vote_start DATE,
            vote_end DATE
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS candidates(
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            election_id INTEGER REFERENCES elections(id),
            manifesto TEXT,
            approved INTEGER DEFAULT 0
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votes(
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            election_id INTEGER REFERENCES elections(id),
            candidate_id INTEGER REFERENCES candidates(id),
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, election_id)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS verification_codes(
            id SERIAL PRIMARY KEY,
            email TEXT,
            code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    """)
    
    conn.commit()
    
    cur.execute("SELECT * FROM users WHERE username = %s", ("admin",))
    if not cur.fetchone():
        admin_password = generate_password_hash("admin123")
        cur.execute("""
            INSERT INTO users (name, username, email, password, role, is_verified)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, ("Administrator", "admin", "admin@example.com", admin_password, "admin", True))
        conn.commit()
    
    cur.close()
    conn.close()

init_db()

def send_verification_email(to_email, otp):
    try:
        message = Mail(
            from_email='noreply@evoting-system.com',
            to_emails=to_email,
            subject='Verify Your Email - E-Voting System',
            html_content=f'<strong>Your OTP is: {otp}</strong><br>Valid for 5 minutes'
        )
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        return response.status_code == 202
    except Exception as e:
        print(f"SendGrid error: {e}")
        return False

def save_verification_code(email, code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM verification_codes WHERE email = %s", (email,))
    expires_at = datetime.now() + timedelta(minutes=5)
    cur.execute("""
        INSERT INTO verification_codes (email, code, expires_at)
        VALUES (%s, %s, %s)
    """, (email, code, expires_at))
    conn.commit()
    cur.close()
    conn.close()

def verify_code(email, code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM verification_codes 
        WHERE email = %s AND code = %s AND expires_at > NOW()
        ORDER BY created_at DESC LIMIT 1
    """, (email, code))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result is not None

@app.route("/")
def home():
    return render_template("index.html")

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
            
            if len(password) < 6:
                return "Password must be at least 6 characters"
            
            conn = get_db_connection()
            cur = conn.cursor()
            
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cur.fetchone():
                cur.close()
                conn.close()
                return "Username already exists"
            
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                cur.close()
                conn.close()
                return "Email already exists"
            
            cur.close()
            conn.close()
            
            hashed_password = generate_password_hash(password)
            
            session["temp_user"] = {
                "name": name,
                "username": username,
                "email": email,
                "password": hashed_password
            }
            
            otp = str(random.randint(100000, 999999))
            save_verification_code(email, otp)
            
            if send_verification_email(email, otp):
                return redirect("/verify-email")
            else:
                return "Failed to send verification email. Please try again."
            
        except Exception as e:
            return f"Registration error: {str(e)}"
    
    return render_template("auth/register.html")

@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    if "temp_user" not in session:
        return redirect("/register")
    
    if request.method == "POST":
        otp = request.form.get("otp")
        email = session["temp_user"]["email"]
        
        if verify_code(email, otp):
            conn = get_db_connection()
            cur = conn.cursor()
            
            user = session["temp_user"]
            cur.execute("""
                INSERT INTO users (name, username, email, password, role, is_verified)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (user["name"], user["username"], user["email"], user["password"], "voter", True))
            
            user_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            conn.close()
            
            session["username"] = user["username"]
            session["role"] = "voter"
            session["user_id"] = user_id
            session.pop("temp_user", None)
            
            return redirect("/dashboard")
        else:
            return "Invalid or expired OTP. Please try again."
    
    return render_template("auth/verify_register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            username = request.form.get("username")
            password = request.form.get("password")
            
            if username == "admin" and password == "admin123":
                session["username"] = "admin"
                session["role"] = "admin"
                return redirect("/admin")
            
            conn = get_db_connection()
            cur = conn.cursor()
            
            cur.execute("SELECT id, username, password, role, is_verified FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
            cur.close()
            conn.close()
            
            if user and check_password_hash(user[2], password):
                if not user[4]:
                    return "Please verify your email before logging in."
                
                session["username"] = user[1]
                session["role"] = user[3]
                session["user_id"] = user[0]
                return redirect("/dashboard")
            
            return "Invalid username or password"
            
        except Exception as e:
            return f"Login error: {str(e)}"
    
    return render_template("auth/login.html")

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = %s", (session["username"],))
    user = cur.fetchone()
    cur.close()
    conn.close()
    
    return render_template("voter/dashboard.html", user=user)

@app.route("/profile")
def profile():
    if "username" not in session:
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = %s", (session["username"],))
    user = cur.fetchone()
    cur.close()
    conn.close()
    
    return render_template("voter/profile.html", user=user)

@app.route("/elections")
def elections():
    if "username" not in session:
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM elections")
    elections_list = cur.fetchall()
    
    cur.execute("""
        SELECT candidates.*, users.name
        FROM candidates
        JOIN users ON candidates.user_id = users.id
        WHERE approved = 1
    """)
    candidates_list = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template("voter/elections.html", elections=elections_list, candidates=candidates_list)

@app.route("/apply")
def apply():
    if "username" not in session:
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM elections")
    elections_list = cur.fetchall()
    cur.close()
    conn.close()
    
    return render_template("candidate/apply_candidate.html", elections=elections_list)

@app.route("/apply_candidate", methods=["POST"])
def apply_candidate():
    if "username" not in session:
        return redirect("/login")
    
    manifesto = request.form.get("manifesto")
    election_id = request.form.get("election_id")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT candidate_deadline FROM elections WHERE id = %s", (election_id,))
    election = cur.fetchone()
    
    if not election:
        cur.close()
        conn.close()
        return "Election not found"
    
    deadline = datetime.strptime(election[0], "%Y-%m-%d")
    
    if datetime.now() > deadline:
        cur.close()
        conn.close()
        return "Candidate application deadline has passed"
    
    cur.execute("SELECT id FROM users WHERE username = %s", (session["username"],))
    user = cur.fetchone()
    
    if not user:
        cur.close()
        conn.close()
        return "User not found"
    
    user_id = user[0]
    
    cur.execute("SELECT * FROM candidates WHERE user_id = %s AND election_id = %s", (user_id, election_id))
    
    if cur.fetchone():
        cur.close()
        conn.close()
        return "You already applied for this election"
    
    cur.execute("""
        INSERT INTO candidates (user_id, election_id, manifesto, approved)
        VALUES (%s, %s, %s, 0)
    """, (user_id, election_id, manifesto))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return "Application sent to admin for approval"

@app.route("/vote/<int:election_id>", methods=["GET", "POST"])
def vote(election_id):
    if "username" not in session:
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT vote_start, vote_end FROM elections WHERE id = %s", (election_id,))
    election = cur.fetchone()
    
    vote_start = datetime.strptime(election[0], "%Y-%m-%d")
    vote_end = datetime.strptime(election[1], "%Y-%m-%d")
    
    if datetime.now() < vote_start:
        cur.close()
        conn.close()
        return "Voting has not started yet"
    
    if datetime.now() > vote_end:
        cur.close()
        conn.close()
        return "Voting has ended"
    
    cur.execute("SELECT id FROM users WHERE username = %s", (session["username"],))
    user_id = cur.fetchone()[0]
    
    if request.method == "POST":
        candidate_id = request.form.get("candidate")
        
        try:
            cur.execute("""
                INSERT INTO votes (user_id, election_id, candidate_id, timestamp)
                VALUES (%s, %s, %s, %s)
            """, (user_id, election_id, candidate_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        except Exception:
            cur.close()
            conn.close()
            return "You already voted in this election"
        
        cur.close()
        conn.close()
        return "Vote submitted successfully"
    
    cur.execute("""
        SELECT candidates.*, users.name
        FROM candidates
        JOIN users ON candidates.user_id = users.id
        WHERE election_id = %s AND approved = 1
    """, (election_id,))
    
    candidates_list = cur.fetchall()
    cur.close()
    conn.close()
    
    return render_template("elections/vote.html", candidates=candidates_list)

@app.route("/admin")
def admin():
    if session.get("role") != "admin":
        return redirect("/login")
    
    return render_template("admin/admin_dashboard.html")

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
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO elections (title, description, candidate_deadline, vote_start, vote_end)
            VALUES (%s, %s, %s, %s, %s)
        """, (title, description, candidate_deadline, vote_start, vote_end))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return redirect("/admin")
    
    return render_template("admin/create_election.html")

@app.route("/manage_candidates")
def manage_candidates():
    if session.get("role") != "admin":
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT candidates.id, users.name, elections.title, candidates.approved
        FROM candidates
        JOIN users ON candidates.user_id = users.id
        JOIN elections ON candidates.election_id = elections.id
    """)
    
    candidates_list = cur.fetchall()
    cur.close()
    conn.close()
    
    return render_template("admin/manage_candidates.html", candidates=candidates_list)

@app.route("/approve_candidate/<int:candidate_id>")
def approve_candidate(candidate_id):
    if session.get("role") != "admin":
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE candidates SET approved = 1 WHERE id = %s", (candidate_id,))
    conn.commit()
    cur.close()
    conn.close()
    
    return redirect("/manage_candidates")

@app.route("/results")
def results():
    if session.get("role") != "admin":
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
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
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    elections_dict = {}
    for r in rows:
        election = r[0]
        if election not in elections_dict:
            elections_dict[election] = []
        elections_dict[election].append(r)
    
    return render_template("admin/results.html", elections=elections_dict)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/health")
def health():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return jsonify({"status": "healthy", "database": "Neon.tech PostgreSQL", "email_service": "SendGrid"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
