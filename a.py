from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, session, jsonify
import os
import random
import time
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import DictCursor

# Use Brevo for email (free, 300 emails/day, works on Render)
try:
    from brevo_python import Configuration, ApiClient, TransactionalEmailsApi, SendSmtpEmail
    BREVO_AVAILABLE = True
except ImportError:
    BREVO_AVAILABLE = False
    print("⚠️ Brevo not installed. Run: pip install brevo-python")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "your-secret-key")
app.permanent_session_lifetime = timedelta(minutes=10)

DATABASE_URL = os.environ.get("DATABASE_URL")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")

def get_db_connection():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url, sslmode="require")
    return conn

def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    """Execute query with automatic placeholder conversion for PostgreSQL"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Convert ? to %s for PostgreSQL
    if params:
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    try:
        cur.execute("ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT FALSE")
        conn.commit()
    except:
        conn.rollback()
    
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
    
    # Create or update admin user
    cur.execute("SELECT id FROM users WHERE username = %s", ("admin",))
    if not cur.fetchone():
        admin_password = generate_password_hash("admin123")
        cur.execute("""
            INSERT INTO users (name, username, email, password, role, is_verified)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, ("Administrator", "admin", "admin@example.com", admin_password, "admin", True))
        conn.commit()
        print("✅ Admin user created with username: admin, password: admin123")
    else:
        # Update existing admin to ensure correct password
        admin_password = generate_password_hash("admin123")
        cur.execute("""
            UPDATE users SET password = %s, role = 'admin', is_verified = TRUE 
            WHERE username = 'admin'
        """, (admin_password,))
        conn.commit()
        print("✅ Admin password reset to: admin123")
    
    cur.close()
    conn.close()

init_db()

def send_verification_email(to_email, otp):
    """Send OTP email using Brevo API (works on Render free tier)"""
    if not BREVO_AVAILABLE:
        print("❌ Brevo not installed")
        return False
    
    if not BREVO_API_KEY:
        print("❌ BREVO_API_KEY not set in environment variables")
        return False
    
    try:
        configuration = Configuration()
        configuration.api_key['api-key'] = BREVO_API_KEY
        
        api_instance = TransactionalEmailsApi(ApiClient(configuration))
        
        from_email = os.environ.get("FROM_EMAIL", "noreply@send.navi.on.com")
        
        send_smtp_email = SendSmtpEmail(
            to=[{"email": to_email}],
            sender={"name": "E-Voting System", "email": from_email},
            subject="Verify Your Email - E-Voting System",
            html_content=f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #333;">Email Verification</h2>
                    <p>Thank you for registering with E-Voting System.</p>
                    <p>Your OTP code is:</p>
                    <h1 style="font-size: 36px; color: #667eea; letter-spacing: 5px; text-align: center;">{otp}</h1>
                    <p>This code is valid for <strong>5 minutes</strong>.</p>
                    <p>If you didn't request this, please ignore this email.</p>
                    <hr style="margin: 20px 0;">
                    <p style="font-size: 12px; color: #999;">E-Voting System - Secure Online Voting Platform</p>
                </div>
            """
        )
        
        api_instance.send_transac_email(send_smtp_email)
        print(f"✅ Email sent to {to_email}")
        return True
        
    except Exception as e:
        print(f"❌ Brevo error: {str(e)}")
        return False

def save_verification_code(email, code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM verification_codes WHERE email = %s", (email,))
    expires_at = datetime.now() + timedelta(minutes=5)
    cur.execute("INSERT INTO verification_codes (email, code, expires_at) VALUES (%s, %s, %s)", (email, code, expires_at))
    conn.commit()
    cur.close()
    conn.close()

def verify_code(email, code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM verification_codes WHERE email = %s AND code = %s AND expires_at > NOW()", (email, code))
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
        name = request.form.get("name")
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm = request.form.get("confirm_password")
        
        if password != confirm:
            return "Passwords do not match"
        
        if len(password) < 6:
            return "Password must be at least 6 characters"
        
        # Check if user exists
        result, conn = execute_query(
            "SELECT id FROM users WHERE username = ? OR email = ?",
            (username, email),
            fetch_one=True
        )
        
        if result:
            conn.close()
            return "Username or Email already exists"
        conn.close()
        
        # Generate OTP
        otp = str(random.randint(100000, 999999))
        
        # Store temp user in session
        session["temp_user"] = {
            "name": name,
            "username": username,
            "email": email,
            "password": generate_password_hash(password)
        }
        
        # Save verification code
        save_verification_code(email, otp)
        
        # Try to send email via Brevo
        email_sent = send_verification_email(email, otp)
        
        if email_sent:
            return redirect("/verify-email")
        else:
            # Fallback: Print OTP to logs and show it to user
            print(f"===== OTP for {email} is: {otp} =====")
            return f"""
            <html>
            <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                <h2>Email could not be sent</h2>
                <p>Your OTP is: <strong style="font-size: 24px;">{otp}</strong></p>
                <p>Please use this OTP to verify your email.</p>
                <a href="/verify-email" style="display: inline-block; padding: 10px 20px; background: #667eea; color: white; text-decoration: none; border-radius: 5px;">Click here to verify</a>
            </body>
            </html>
            """
    
    return render_template("auth/register.html")

@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    if "temp_user" not in session:
        return redirect("/register")
    
    if request.method == "POST":
        otp = request.form.get("otp")
        email = session["temp_user"]["email"]
        
        if verify_code(email, otp):
            user = session["temp_user"]
            
            # Direct database insertion
            conn = get_db_connection()
            cur = conn.cursor()
            
            try:
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
            except Exception as e:
                cur.close()
                conn.close()
                return f"Error creating user: {str(e)}"
        else:
            return "Invalid or expired OTP. Please try again."
    
    return render_template("auth/verify_register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        # Admin login - hardcoded as backup
        if username == "admin" and password == "admin123":
            session["username"] = "admin"
            session["role"] = "admin"
            session["user_id"] = 1
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
    
    return render_template("auth/login.html")

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, username, email, role FROM users WHERE username = %s", (session["username"],))
    user_data = cur.fetchone()
    cur.close()
    conn.close()
    
    if user_data:
        # Convert tuple to dictionary for template
        user = {
            "name": user_data[0],
            "username": user_data[1],
            "email": user_data[2],
            "role": user_data[3]
        }
        return render_template("voter/dashboard.html", user=user)
    else:
        return redirect("/login")

@app.route("/profile")
def profile():
    if "username" not in session:
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, username, email, role, created_at FROM users WHERE username = %s", (session["username"],))
    user_data = cur.fetchone()
    cur.close()
    conn.close()
    
    if user_data:
        user = {
            "name": user_data[0],
            "username": user_data[1],
            "email": user_data[2],
            "role": user_data[3],
            "created_at": user_data[4]
        }
        return render_template("voter/profile.html", user=user)
    else:
        return redirect("/login")

@app.route("/elections")
def elections():
    if "username" not in session:
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT id, title, description, candidate_deadline, vote_start, vote_end FROM elections")
    elections_rows = cur.fetchall()
    
    cur.execute("""
        SELECT candidates.id, candidates.user_id, candidates.election_id, candidates.manifesto, candidates.approved, users.name
        FROM candidates
        JOIN users ON candidates.user_id = users.id
        WHERE approved = 1
    """)
    candidates_rows = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # Convert to dictionaries for templates
    elections_list = []
    for e in elections_rows:
        elections_list.append({
            "id": e[0],
            "title": e[1],
            "description": e[2],
            "candidate_deadline": e[3],
            "vote_start": e[4],
            "vote_end": e[5]
        })
    
    candidates_list = []
    for c in candidates_rows:
        candidates_list.append({
            "id": c[0],
            "user_id": c[1],
            "election_id": c[2],
            "manifesto": c[3],
            "approved": c[4],
            "name": c[5]
        })
    
    return render_template("voter/elections.html", elections=elections_list, candidates=candidates_list)

@app.route("/apply")
def apply():
    if "username" not in session:
        return redirect("/login")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM elections")
    elections_list = cur.fetchall()
    cur.close()
    conn.close()
    
    # Convert to list of dicts for template
    elections = []
    for e in elections_list:
        elections.append({
            "id": e[0],
            "title": e[1]
        })
    
    return render_template("candidate/apply_candidate.html", elections=elections)

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
    
    deadline = datetime.strptime(str(election[0]), "%Y-%m-%d")
    
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
    
    if not election:
        cur.close()
        conn.close()
        return "Election not found"
    
    vote_start = datetime.strptime(str(election[0]), "%Y-%m-%d")
    vote_end = datetime.strptime(str(election[1]), "%Y-%m-%d")
    
    if datetime.now() < vote_start:
        cur.close()
        conn.close()
        return "Voting has not started yet"
    
    if datetime.now() > vote_end:
        cur.close()
        conn.close()
        return "Voting has ended"
    
    cur.execute("SELECT id FROM users WHERE username = %s", (session["username"],))
    user_result = cur.fetchone()
    
    if not user_result:
        cur.close()
        conn.close()
        return "User not found"
    
    user_id = user_result[0]
    
    if request.method == "POST":
        candidate_id = request.form.get("candidate")
        
        try:
            cur.execute("""
                INSERT INTO votes (user_id, election_id, candidate_id, timestamp)
                VALUES (%s, %s, %s, %s)
            """, (user_id, election_id, candidate_id, datetime.now()))
            conn.commit()
        except Exception as e:
            cur.close()
            conn.close()
            return "You already voted in this election"
        
        cur.close()
        conn.close()
        return "Vote submitted successfully"
    
    cur.execute("""
        SELECT candidates.id, users.name, candidates.manifesto
        FROM candidates
        JOIN users ON candidates.user_id = users.id
        WHERE election_id = %s AND approved = 1
    """, (election_id,))
    
    candidates_list = cur.fetchall()
    cur.close()
    conn.close()
    
    # Convert to list of dicts for template
    candidates = []
    for c in candidates_list:
        candidates.append({
            "id": c[0],
            "name": c[1],
            "manifesto": c[2]
        })
    
    return render_template("elections/vote.html", candidates=candidates, election_id=election_id)

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
        WHERE candidates.approved = 0
    """)
    
    candidates_rows = cur.fetchall()
    cur.close()
    conn.close()
    
    candidates_list = []
    for c in candidates_rows:
        candidates_list.append({
            "id": c[0],
            "name": c[1],
            "title": c[2],
            "approved": c[3]
        })
    
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
    
    try:
        # First, check if there are any elections
        cur.execute("SELECT COUNT(*) FROM elections")
        election_count = cur.fetchone()[0]
        
        if election_count == 0:
            cur.close()
            conn.close()
            return render_template("admin/results.html", elections={}, message="No elections found")
        
        # Get results with proper error handling
        cur.execute("""
            SELECT 
                elections.id,
                elections.title AS election_title,
                users.name AS candidate_name,
                COUNT(votes.id) AS vote_count
            FROM elections
            LEFT JOIN candidates ON candidates.election_id = elections.id AND candidates.approved = 1
            LEFT JOIN users ON candidates.user_id = users.id
            LEFT JOIN votes ON votes.candidate_id = candidates.id
            GROUP BY elections.id, elections.title, users.name, candidates.id
            ORDER BY elections.id, vote_count DESC
        """)
        
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        # Organize results by election
        elections_dict = {}
        for row in rows:
            election_title = row[1]
            candidate_name = row[2]
            vote_count = row[3] if row[3] is not None else 0
            
            if election_title not in elections_dict:
                elections_dict[election_title] = []
            
            if candidate_name:  # Only add if there's a candidate
                elections_dict[election_title].append({
                    "candidate_name": candidate_name,
                    "vote_count": vote_count
                })
        
        return render_template("admin/results.html", elections=elections_dict)
        
    except Exception as e:
        cur.close()
        conn.close()
        return f"Error loading results: {str(e)}", 500

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
        return jsonify({"status": "healthy", "database": "Neon.tech PostgreSQL"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
