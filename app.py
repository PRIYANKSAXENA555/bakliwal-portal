# ====================================================================
# BAKLIWAL TUTORIALS - COMPLETE STUDENT PORTAL
# Optimized for free hosting (Render.com, Railway, Koyeb)
# ====================================================================

import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# ====================================================================
# CONFIGURATION
# ====================================================================

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')

# Database - Use environment variable for persistent storage
if os.environ.get('RENDER'):
    # On Render, use /tmp for writable storage (temporary)
    BASE_DIR = '/tmp'
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, 'students.db')

# Google Sheets Configuration
# Use environment variable for credentials
GOOGLE_SHEETS_CREDENTIALS = os.environ.get('GOOGLE_CREDENTIALS_JSON')
SHEET_NAME = os.environ.get('SHEET_NAME', 'Master Sheet')

# ====================================================================
# DATABASE SETUP
# ====================================================================

def get_db_connection():
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Students table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            roll_no TEXT UNIQUE,
            mother_name TEXT NOT NULL,
            batch TEXT,
            branch TEXT,
            password_hash TEXT NOT NULL,
            is_teacher BOOLEAN DEFAULT 0,
            subject TEXT,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Exercises table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            chapter TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(subject, chapter, exercise_name)
        )
    ''')

    # Progress table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            exercise_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            discussed TEXT DEFAULT 'no',
            teacher_comment TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, exercise_id)
        )
    ''')

    # Messages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL,
            subject TEXT,
            message TEXT NOT NULL,
            parent_id INTEGER DEFAULT NULL,
            is_read BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parent_id) REFERENCES messages(id)
        )
    ''')

    # Test Results table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            test_name TEXT NOT NULL,
            test_type TEXT,
            rank TEXT,
            physics_marks REAL,
            physics_max REAL,
            chemistry_marks REAL,
            chemistry_max REAL,
            maths_marks REAL,
            maths_max REAL,
            total_marks REAL,
            total_max REAL,
            percentage REAL,
            test_date TIMESTAMP,
            UNIQUE(student_id, test_name)
        )
    ''')

    # Add default teachers
    teachers = [
        ('Physics Teacher', 'physics_teacher', 'physics@school.com', 'Physics'),
        ('Chemistry Teacher', 'chemistry_teacher', 'chemistry@school.com', 'Chemistry'),
        ('Mathematics Teacher', 'maths_teacher', 'maths@school.com', 'Mathematics')
    ]

    for name, username, email, subject in teachers:
        cursor.execute('SELECT id FROM students WHERE email = ?', (email,))
        if not cursor.fetchone():
            password_hash = generate_password_hash(username)
            cursor.execute('''
                INSERT INTO students (name, roll_no, mother_name, email, password_hash, is_teacher, subject)
                VALUES (?, ?, ?, ?, ?, 1, ?)
            ''', (name, username, 'teacher', email, password_hash, subject))

    # Add sample exercises
    cursor.execute('SELECT COUNT(*) as count FROM exercises')
    if cursor.fetchone()[0] == 0:
        sample_exercises = [
            ('Physics', 'Chapter 1: Motion', 'Exercise 1.1 - Speed'),
            ('Physics', 'Chapter 1: Motion', 'Exercise 1.2 - Acceleration'),
            ('Physics', 'Chapter 2: Force', 'Exercise 2.1 - Newton\'s Laws'),
            ('Chemistry', 'Chapter 1: Atoms', 'Exercise 1.1 - Atomic Structure'),
            ('Chemistry', 'Chapter 1: Atoms', 'Exercise 1.2 - Periodic Table'),
            ('Chemistry', 'Chapter 2: Reactions', 'Exercise 2.1 - Chemical Equations'),
            ('Mathematics', 'Chapter 1: Algebra', 'Exercise 1.1 - Linear Equations'),
            ('Mathematics', 'Chapter 1: Algebra', 'Exercise 1.2 - Quadratic Equations'),
            ('Mathematics', 'Chapter 2: Calculus', 'Exercise 2.1 - Derivatives')
        ]
        for subject, chapter, exercise_name in sample_exercises:
            cursor.execute('''
                INSERT OR IGNORE INTO exercises (subject, chapter, exercise_name)
                VALUES (?, ?, ?)
            ''', (subject, chapter, exercise_name))

    conn.commit()
    conn.close()
    print("✅ Database initialized!")

# ====================================================================
# GOOGLE SHEETS INTEGRATION
# ====================================================================

def get_google_sheets_client():
    """Initialize Google Sheets client from environment variable"""
    try:
        if GOOGLE_SHEETS_CREDENTIALS:
            # Parse credentials from environment variable
            creds_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS)
            scope = ['https://spreadsheets.google.com/feeds', 
                     'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            return client
        else:
            # Try to load from file (local development)
            try:
                with open('credentials.json', 'r') as f:
                    creds_dict = json.load(f)
                scope = ['https://spreadsheets.google.com/feeds', 
                         'https://www.googleapis.com/auth/drive']
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
                client = gspread.authorize(creds)
                return client
            except:
                print("⚠️ No credentials found")
                return None
    except Exception as e:
        print(f"⚠️ Google Sheets error: {e}")
        return None

def sync_students_from_google_sheets():
    """Sync students from Google Sheets"""
    client = get_google_sheets_client()
    if not client:
        print("⚠️ Google Sheets client not available")
        return

    try:
        spreadsheet = client.open(SHEET_NAME)
        
        # Get student data from Sheet20
        sheet20 = spreadsheet.worksheet('Sheet20')
        data = sheet20.get_all_values()
        if len(data) < 2:
            return

        headers = data[0]
        name_idx = headers.index('NAME') if 'NAME' in headers else -1
        roll_idx = headers.index('ROLL NO') if 'ROLL NO' in headers else -1
        batch_idx = headers.index('BATCH') if 'BATCH' in headers else -1
        branch_idx = headers.index('BRANCH') if 'BRANCH' in headers else -1

        if roll_idx == -1:
            print("⚠️ ROLL NO column not found")
            return

        # Get mother names
        try:
            mother_sheet = spreadsheet.worksheet('Mother Name')
            mother_data = mother_sheet.get_all_values()
            mother_dict = {}
            for row in mother_data[1:]:
                if len(row) >= 2:
                    mother_dict[row[0].strip()] = row[1].strip()
        except:
            mother_dict = {}
            print("⚠️ Mother Name sheet not found")

        conn = get_db_connection()
        cursor = conn.cursor()

        existing_rolls = set([row[0] for row in cursor.execute('SELECT roll_no FROM students WHERE is_teacher = 0').fetchall()])
        new_rolls = set()

        for row in data[1:]:
            if len(row) <= max(roll_idx, name_idx):
                continue

            roll_no = str(row[roll_idx]).strip() if roll_idx != -1 else ''
            if not roll_no or roll_no == '':
                continue

            new_rolls.add(roll_no)
            name = str(row[name_idx]).strip() if name_idx != -1 else 'Student'
            batch = str(row[batch_idx]).strip() if batch_idx != -1 else ''
            branch = str(row[branch_idx]).strip() if branch_idx != -1 else ''
            mother_name = mother_dict.get(name, mother_dict.get(roll_no, 'password'))
            password_hash = generate_password_hash(mother_name.lower())

            if roll_no in existing_rolls:
                cursor.execute('''
                    UPDATE students 
                    SET name=?, mother_name=?, batch=?, branch=?, password_hash=?
                    WHERE roll_no=?
                ''', (name, mother_name, batch, branch, password_hash, roll_no))
            else:
                cursor.execute('''
                    INSERT INTO students (name, roll_no, mother_name, batch, branch, password_hash, is_teacher)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                ''', (name, roll_no, mother_name, batch, branch, password_hash))
                
                # Add exercises for new student
                student_id = cursor.lastrowid
                exercises = cursor.execute('SELECT id FROM exercises').fetchall()
                for exercise in exercises:
                    cursor.execute('''
                        INSERT OR IGNORE INTO progress (student_id, exercise_id, status, discussed)
                        VALUES (?, ?, 'pending', 'no')
                    ''', (student_id, exercise[0]))

        # Delete removed students
        for roll in existing_rolls - new_rolls:
            cursor.execute('DELETE FROM test_results WHERE student_id IN (SELECT id FROM students WHERE roll_no = ?)', (roll,))
            cursor.execute('DELETE FROM progress WHERE student_id IN (SELECT id FROM students WHERE roll_no = ?)', (roll,))
            cursor.execute('DELETE FROM students WHERE roll_no = ?', (roll,))

        conn.commit()
        conn.close()
        print(f"✅ Synced {len(new_rolls)} students")

    except Exception as e:
        print(f"⚠️ Error syncing students: {e}")

def sync_test_results_from_google_sheets():
    """Sync test results from Google Sheets"""
    client = get_google_sheets_client()
    if not client:
        return

    try:
        spreadsheet = client.open(SHEET_NAME)
        all_sheets = spreadsheet.worksheets()

        conn = get_db_connection()
        cursor = conn.cursor()

        test_count = 0
        
        for sheet in all_sheets:
            sheet_name = sheet.title
            if sheet_name in ['Sheet20', 'Mother Name', 'Sheet1'] or sheet_name.startswith('Sheet'):
                continue

            data = sheet.get_all_values()
            if len(data) < 10:
                continue

            header_row = -1
            for i in range(min(20, len(data))):
                if data[i] and data[i][0] == 'TOTAL RANK':
                    header_row = i
                    break

            if header_row == -1:
                continue

            headers = data[header_row]
            rank_idx = headers.index('TOTAL RANK') if 'TOTAL RANK' in headers else -1
            roll_idx = headers.index('ROLL NO.') if 'ROLL NO.' in headers else -1
            phy_idx = headers.index('PHY') if 'PHY' in headers else -1
            chem_idx = headers.index('CHEM') if 'CHEM' in headers else -1
            maths_idx = headers.index('MATHS') if 'MATHS' in headers else -1
            total_idx = headers.index('TOTAL') if 'TOTAL' in headers else -1

            if roll_idx == -1:
                continue

            is_brtest = sheet_name.upper().startswith('BRTEST')
            max_phy = 50 if is_brtest else 100
            max_chem = 50 if is_brtest else 100
            max_maths = 100
            max_total = max_phy + max_chem + max_maths

            if len(data) > header_row + 2 and data[header_row + 2][1] == 'MAX':
                if len(data[header_row + 2]) > 3:
                    max_phy = float(data[header_row + 2][3]) if data[header_row + 2][3] else max_phy
                if len(data[header_row + 2]) > 5:
                    max_chem = float(data[header_row + 2][5]) if data[header_row + 2][5] else max_chem
                if len(data[header_row + 2]) > 7:
                    max_maths = float(data[header_row + 2][7]) if data[header_row + 2][7] else max_maths
                if len(data[header_row + 2]) > 9:
                    max_total = float(data[header_row + 2][9]) if data[header_row + 2][9] else max_total

            test_name = sheet_name.replace('BATCH ', '').replace('BTEST', 'Test')
            test_name = test_name.replace('GRAND TEST', 'Grand Test').replace('BRTEST', 'CET Test')

            for row in data[header_row + 1:]:
                if len(row) <= roll_idx:
                    continue

                roll_no = str(row[roll_idx]).strip()
                if not roll_no or roll_no == '':
                    continue

                cursor.execute('SELECT id FROM students WHERE roll_no = ?', (roll_no,))
                student = cursor.fetchone()
                if not student:
                    continue

                student_id = student[0]

                try:
                    phy_marks = float(row[phy_idx]) if phy_idx != -1 and row[phy_idx] else 0
                except:
                    phy_marks = 0
                try:
                    chem_marks = float(row[chem_idx]) if chem_idx != -1 and row[chem_idx] else 0
                except:
                    chem_marks = 0
                try:
                    maths_marks = float(row[maths_idx]) if maths_idx != -1 and row[maths_idx] else 0
                except:
                    maths_marks = 0
                try:
                    total_marks = float(row[total_idx]) if total_idx != -1 and row[total_idx] else 0
                except:
                    total_marks = phy_marks + chem_marks + maths_marks

                rank = str(row[rank_idx]) if rank_idx != -1 and row[rank_idx] else '-'
                percentage = (total_marks / max_total * 100) if max_total > 0 else 0

                cursor.execute('''
                    INSERT OR REPLACE INTO test_results 
                    (student_id, test_name, test_type, rank, 
                     physics_marks, physics_max, chemistry_marks, chemistry_max, 
                     maths_marks, maths_max, total_marks, total_max, percentage, test_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (student_id, test_name, 'CET' if is_brtest else 'Mains',
                      rank, phy_marks, max_phy, chem_marks, max_chem,
                      maths_marks, max_maths, total_marks, max_total, percentage))

                test_count += 1

        conn.commit()
        conn.close()
        print(f"✅ Synced {test_count} test results")

    except Exception as e:
        print(f"⚠️ Error syncing test results: {e}")

# ====================================================================
# AUTHENTICATION HELPERS
# ====================================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def teacher_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_teacher', False):
            flash('Teacher access required', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_unread_count(user_id):
    conn = get_db_connection()
    count = conn.execute('SELECT COUNT(*) as count FROM messages WHERE to_id = ? AND is_read = 0', (user_id,)).fetchone()
    conn.close()
    return count[0] if count else 0

def send_message(from_id, to_id, subject, message, parent_id=None):
    conn = get_db_connection()
    conn.execute('INSERT INTO messages (from_id, to_id, subject, message, parent_id) VALUES (?, ?, ?, ?, ?)',
                (from_id, to_id, subject, message, parent_id))
    conn.commit()
    conn.close()

def mark_message_read(message_id):
    conn = get_db_connection()
    conn.execute('UPDATE messages SET is_read = 1 WHERE id = ?', (message_id,))
    conn.commit()
    conn.close()

def get_all_messages(user_id):
    conn = get_db_connection()
    messages = conn.execute('''
        SELECT m.id, m.from_id, m.to_id, m.subject, m.message, m.parent_id, m.is_read, m.created_at,
               s.name as from_name, s2.name as to_name
        FROM messages m
        JOIN students s ON m.from_id = s.id
        JOIN students s2 ON m.to_id = s2.id
        WHERE m.from_id = ? OR m.to_id = ?
        ORDER BY m.created_at DESC
    ''', (user_id, user_id)).fetchall()
    conn.close()
    return [dict(m) for m in messages]

def get_students():
    conn = get_db_connection()
    students = conn.execute('SELECT id, name, roll_no, batch, branch FROM students WHERE is_teacher = 0 ORDER BY name').fetchall()
    conn.close()
    return [dict(s) for s in students]

def get_progress_for_student(student_id):
    conn = get_db_connection()
    progress = conn.execute('''
        SELECT e.id as exercise_id, e.subject, e.chapter, e.exercise_name,
               COALESCE(p.status, 'pending') as status,
               COALESCE(p.discussed, 'no') as discussed,
               p.teacher_comment
        FROM exercises e
        LEFT JOIN progress p ON e.id = p.exercise_id AND p.student_id = ?
        ORDER BY e.subject, e.chapter, e.id
    ''', (student_id,)).fetchall()
    conn.close()
    return [dict(p) for p in progress]

def get_test_results(student_id):
    conn = get_db_connection()
    results = conn.execute('''
        SELECT test_name, test_type, rank, physics_marks, physics_max, chemistry_marks, chemistry_max,
               maths_marks, maths_max, total_marks, total_max, percentage
        FROM test_results
        WHERE student_id = ?
        ORDER BY test_date DESC
    ''', (student_id,)).fetchall()
    conn.close()
    return [dict(r) for r in results]

def get_student_stats(student_id):
    conn = get_db_connection()
    results = conn.execute('''
        SELECT COUNT(*) as total_tests, AVG(percentage) as avg_percentage,
               MIN(CAST(rank AS INTEGER)) as best_rank, MAX(total_marks) as highest_score
        FROM test_results
        WHERE student_id = ? AND rank != '-'
    ''', (student_id,)).fetchone()
    conn.close()
    return dict(results) if results else {'total_tests': 0, 'avg_percentage': 0, 'best_rank': None, 'highest_score': 0}

# ====================================================================
# ROUTES - AUTHENTICATION
# ====================================================================

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        conn = get_db_connection()
        user = conn.execute(
            'SELECT * FROM students WHERE name = ? OR roll_no = ? OR email = ? OR LOWER(name) = LOWER(?)',
            (username, username, username, username)
        ).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password_hash'], password.lower()):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['roll_no'] = user['roll_no']
            session['is_teacher'] = user.get('is_teacher', 0)
            session['subject'] = user.get('subject') if user.get('is_teacher') else None
            flash(f'Welcome {user["name"]}!', 'success')
            
            if user.get('is_teacher'):
                return redirect(url_for('teacher_dashboard'))
            else:
                return redirect(url_for('student_dashboard'))
        else:
            flash('Invalid credentials. Use your Name/Roll Number and Mother\'s Name.', 'error')

    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bakliwal Tutorials - Student Portal</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            *{margin:0;padding:0;box-sizing:border-box;}
            body{font-family:Segoe UI,sans-serif;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;display:flex;justify-content:center;align-items:center;padding:20px;}
            .container{background:white;border-radius:20px;padding:40px;width:100%;max-width:450px;box-shadow:0 20px 60px rgba(0,0,0,0.3);}
            .logo{text-align:center;margin-bottom:30px;}
            .logo h1{color:#333;font-size:24px;}
            .logo p{color:#666;font-size:14px;}
            .form-group{margin-bottom:20px;}
            label{display:block;margin-bottom:8px;font-weight:500;color:#333;font-size:14px;}
            input{width:100%;padding:12px 15px;border:2px solid #e0e0e0;border-radius:10px;font-size:16px;transition:all 0.3s;}
            input:focus{outline:none;border-color:#667eea;box-shadow:0 0 0 3px rgba(102,126,234,0.1);}
            button{width:100%;padding:14px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;border:none;border-radius:10px;font-size:16px;font-weight:600;cursor:pointer;transition:transform 0.2s;}
            button:hover{transform:translateY(-2px);}
            .alert{padding:12px 15px;border-radius:10px;margin-bottom:20px;}
            .alert-success{background:#d4edda;color:#155724;border:1px solid #c3e6cb;}
            .alert-error{background:#f8d7da;color:#721c24;border:1px solid #f5c6cb;}
            .info-text{margin-top:20px;font-size:12px;color:#666;text-align:center;line-height:1.8;}
            .info-text strong{color:#333;}
            .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;margin:2px;}
            .badge-physics{background:#4299e1;color:white;}
            .badge-chemistry{background:#ed8936;color:white;}
            .badge-maths{background:#9f7aea;color:white;}
        </style>
    </head>
    <body>
    <div class="container">
        <div class="logo">
            <h1>🎓 Bakliwal Tutorials</h1>
            <p>JEE 2027 - Student Portal</p>
        </div>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="POST">
            <div class="form-group">
                <label>📝 Username</label>
                <input type="text" name="username" placeholder="Your Name, Roll No, or Email" required>
            </div>
            <div class="form-group">
                <label>🔑 Password</label>
                <input type="password" name="password" placeholder="Mother's Name (for students)" required>
            </div>
            <button type="submit">🔓 Login</button>
        </form>
        <div class="info-text">
            <strong>👨‍🎓 Students:</strong> Use your Name or Roll No<br>
            Password is your <strong>Mother's Name</strong><br><br>
            <strong>👨‍🏫 Teachers:</strong><br>
            <span class="badge badge-physics">Physics</span> physics_teacher / physics_teacher<br>
            <span class="badge badge-chemistry">Chemistry</span> chemistry_teacher / chemistry_teacher<br>
            <span class="badge badge-maths">Mathematics</span> maths_teacher / maths_teacher
        </div>
    </div>
    </body>
    </html>
    ''')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'success')
    return redirect(url_for('login'))

# ====================================================================
# STUDENT DASHBOARD
# ====================================================================

@app.route('/student/dashboard')
@login_required
def student_dashboard():
    if session.get('is_teacher'):
        return redirect(url_for('teacher_dashboard'))

    student_id = session['user_id']
    progress = get_progress_for_student(student_id)
    test_results = get_test_results(student_id)
    stats = get_student_stats(student_id)
    unread_count = get_unread_count(student_id)

    subjects = {}
    for item in progress:
        if item['subject'] not in subjects:
            subjects[item['subject']] = []
        subjects[item['subject']].append(item)

    total = len(progress)
    done = sum(1 for p in progress if p['status'] == 'done')
    discussed = sum(1 for p in progress if p['discussed'] == 'yes')

    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Student Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            *{margin:0;padding:0;box-sizing:border-box;}
            body{font-family:Segoe UI,sans-serif;background:#f5f5f5;padding:20px;}
            .container{max-width:1400px;margin:0 auto;}
            .header{background:white;border-radius:15px;padding:20px 25px;margin-bottom:25px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;box-shadow:0 2px 10px rgba(0,0,0,0.08);}
            .header h1{font-size:22px;color:#333;}
            .header p{color:#666;font-size:14px;}
            .header .roll{background:#667eea;color:white;padding:4px 12px;border-radius:20px;font-size:12px;margin-left:10px;}
            .btn-group{display:flex;gap:10px;flex-wrap:wrap;}
            .btn-message{background:#667eea;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;text-decoration:none;display:inline-block;}
            .btn-message:hover{background:#5a67d8;}
            .btn-logout{background:#dc3545;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;}
            .btn-logout:hover{background:#c82333;}
            .badge{background:#e53e3e;color:white;padding:2px 8px;border-radius:10px;font-size:12px;margin-left:5px;}
            .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:20px;margin-bottom:30px;}
            .stat-card{background:white;border-radius:15px;padding:20px;text-align:center;box-shadow:0 2px 10px rgba(0,0,0,0.08);}
            .stat-card .value{font-size:28px;font-weight:bold;color:#667eea;}
            .stat-card .label{color:#666;font-size:13px;margin-top:5px;}
            .section{background:white;border-radius:15px;padding:25px;margin-bottom:25px;box-shadow:0 2px 10px rgba(0,0,0,0.08);overflow-x:auto;}
            .section-title{font-size:18px;color:#333;margin-bottom:20px;}
            .section-title .icon{margin-right:10px;}
            .subject-title{padding:12px 15px;font-weight:bold;color:white;border-radius:8px;margin-bottom:10px;}
            .subject-physics{background:#4299e1;}
            .subject-chemistry{background:#ed8936;}
            .subject-mathematics{background:#9f7aea;}
            table{width:100%;border-collapse:collapse;font-size:13px;}
            th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #e0e0e0;}
            th{background:#f8f9fa;font-weight:600;color:#333;}
            .status-done{background:#48bb78;color:white;padding:3px 8px;border-radius:4px;font-size:11px;}
            .status-pending{background:#f56565;color:white;padding:3px 8px;border-radius:4px;font-size:11px;}
            .discussed-yes{background:#48bb78;color:white;padding:3px 8px;border-radius:4px;font-size:11px;}
            .discussed-no{background:#f56565;color:white;padding:3px 8px;border-radius:4px;font-size:11px;}
            .btn-sm{padding:4px 8px;border:none;border-radius:4px;cursor:pointer;font-size:11px;margin:1px;}
            .btn-done{background:#48bb78;color:white;}
            .btn-pending{background:#f56565;color:white;}
            .btn-discuss-yes{background:#48bb78;color:white;}
            .btn-discuss-no{background:#f56565;color:white;}
            .rank-badge{display:inline-block;padding:2px 10px;border-radius:20px;font-weight:600;font-size:12px;}
            .rank-top{background:#d4edda;color:#155724;}
            .rank-good{background:#d1ecf1;color:#0c5460;}
            .rank-avg{background:#fff3cd;color:#856404;}
            .rank-low{background:#f8d7da;color:#721c24;}
            .progress-bar{background:#e0e0e0;border-radius:10px;overflow:hidden;height:6px;width:100px;}
            .progress-fill{background:linear-gradient(90deg,#667eea,#764ba2);height:100%;border-radius:10px;}
            .test-type{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;}
            .type-mains{background:#667eea;color:white;}
            .type-cet{background:#48bb78;color:white;}
            .chart-container{height:280px;}
            .no-data{text-align:center;padding:30px;color:#666;}
            @media (max-width:768px){
                .header{flex-direction:column;align-items:flex-start;gap:15px;}
                .stats-grid{grid-template-columns:1fr 1fr;}
                th,td{padding:6px 8px;font-size:11px;}
                .section{padding:15px;}
            }
        </style>
    </head>
    <body>
    <div class="container">
        <div class="header">
            <div>
                <h1>👋 Welcome, {{ session.user_name }}!</h1>
                <p>Roll No: <strong>{{ session.roll_no }}</strong>
                   <span class="roll">{{ student.batch or 'N/A' }}</span>
                   <span style="margin-left:10px;color:#666;">{{ student.branch or '' }}</span>
                </p>
            </div>
            <div class="btn-group">
                <a href="/student/messages" class="btn-message">💬 Messages <span class="badge">{{ unread_count }}</span></a>
                <button class="btn-logout" onclick="location.href='/logout'">🚪 Logout</button>
            </div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card"><div class="value">{{ done }}/{{ total }}</div><div class="label">Exercises Done</div></div>
            <div class="stat-card"><div class="value">{{ "%.0f"|format((done/total*100) if total>0 else 0) }}%</div><div class="label">Completion</div></div>
            <div class="stat-card"><div class="value">{{ stats.total_tests or 0 }}</div><div class="label">Tests Given</div></div>
            <div class="stat-card"><div class="value">{{ "%.1f"|format(stats.avg_percentage or 0) }}%</div><div class="label">Avg Test Score</div></div>
        </div>
        
        {% if test_results|length > 0 %}
        <div class="section">
            <div class="section-title"><span class="icon">📊</span> Test Performance</div>
            <div class="chart-container">
                <canvas id="performanceChart"></canvas>
            </div>
        </div>
        {% endif %}
        
        <div class="section">
            <div class="section-title"><span class="icon">📝</span> Exercise Progress</div>
            {% for subject, exercises in subjects.items() %}
            <div class="subject-title subject-{{ subject.lower() }}">{{ subject }}</div>
            <table>
                <thead><tr><th>Chapter</th><th>Exercise</th><th>Status</th><th>Discussed</th><th>Teacher Comment</th><th>Actions</th></tr></thead>
                <tbody>
                    {% for ex in exercises %}
                    <tr>
                        <td>{{ ex.chapter }}</td>
                        <td>{{ ex.exercise_name }}</td>
                        <td><span class="status-{{ ex.status }}">{{ ex.status.upper() }}</span></td>
                        <td><span class="discussed-{{ ex.discussed }}">{{ ex.discussed.upper() }}</span></td>
                        <td>{{ ex.teacher_comment or 'No comment' }}</td>
                        <td>
                            <form method="POST" action="/student/update_progress" style="display:inline;">
                                <input type="hidden" name="exercise_id" value="{{ ex.exercise_id }}">
                                <input type="hidden" name="action" value="status">
                                <button type="submit" name="status" value="done" class="btn-sm btn-done">✓ Done</button>
                                <button type="submit" name="status" value="pending" class="btn-sm btn-pending">○ Pending</button>
                            </form>
                            <form method="POST" action="/student/update_progress" style="display:inline;">
                                <input type="hidden" name="exercise_id" value="{{ ex.exercise_id }}">
                                <input type="hidden" name="action" value="discussed">
                                <button type="submit" name="discussed" value="yes" class="btn-sm btn-discuss-yes">📚 Yes</button>
                                <button type="submit" name="discussed" value="no" class="btn-sm btn-discuss-no">📚 No</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% endfor %}
        </div>
        
        <div class="section">
            <div class="section-title"><span class="icon">📋</span> Test Results</div>
            {% if test_results|length > 0 %}
            <table>
                <thead><tr><th>Test Name</th><th>Type</th><th>Rank</th><th>Physics</th><th>Chemistry</th><th>Maths</th><th>Total</th><th>Percentage</th></tr></thead>
                <tbody>
                    {% for r in test_results %}
                    {% set rank_num = r.rank|int if r.rank != '-' and r.rank else 999 %}
                    {% if rank_num <= 50 %}
                        {% set rank_class = 'rank-top' %}
                    {% elif rank_num <= 100 %}
                        {% set rank_class = 'rank-good' %}
                    {% elif rank_num <= 200 %}
                        {% set rank_class = 'rank-avg' %}
                    {% else %}
                        {% set rank_class = 'rank-low' %}
                    {% endif %}
                    <tr>
                        <td><strong>{{ r.test_name }}</strong></td>
                        <td><span class="test-type {% if r.test_type == 'CET' %}type-cet{% else %}type-mains{% endif %}">{{ r.test_type or 'Mains' }}</span></td>
                        <td><span class="rank-badge {{ rank_class }}">{% if r.rank != '-' %}#{{ r.rank }}{% else %}-{% endif %}</span></td>
                        <td>{{ "%.0f"|format(r.physics_marks) }}/{{ "%.0f"|format(r.physics_max) }}</td>
                        <td>{{ "%.0f"|format(r.chemistry_marks) }}/{{ "%.0f"|format(r.chemistry_max) }}</td>
                        <td>{{ "%.0f"|format(r.maths_marks) }}/{{ "%.0f"|format(r.maths_max) }}</td>
                        <td><strong>{{ "%.0f"|format(r.total_marks) }}</strong>/{{ "%.0f"|format(r.total_max) }}</td>
                        <td>
                            {{ "%.1f"|format(r.percentage) }}%
                            <div class="progress-bar"><div class="progress-fill" style="width:{{ r.percentage }}%"></div></div>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="no-data">📭 No test results available yet.</div>
            {% endif %}
        </div>
    </div>
    
    <script>
        {% if test_results and test_results|length > 0 %}
        const ctx = document.getElementById('performanceChart').getContext('2d');
        const labels = {{ test_results|map(attribute='test_name')|list|tojson }};
        const percentages = {{ test_results|map(attribute='percentage')|list|tojson }};
        const totalMarks = {{ test_results|map(attribute='total_marks')|list|tojson }};
        const totalMax = {{ test_results|map(attribute='total_max')|list|tojson }};
        const reversedLabels = labels.reverse();
        const reversedPercentages = percentages.reverse();
        const reversedTotal = totalMarks.reverse();
        const reversedMax = totalMax.reverse();
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: reversedLabels,
                datasets: [{
                    label: 'Percentage (%)',
                    data: reversedPercentages,
                    backgroundColor: ['#667eea', '#764ba2', '#f093fb', '#4facfe', '#43e97b', '#fa709a'],
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const idx = context.dataIndex;
                                return `Score: ${reversedTotal[idx]}/${reversedMax[idx]} (${reversedPercentages[idx].toFixed(1)}%)`;
                            }
                        }
                    }
                },
                scales: {
                    y: { beginAtZero: true, max: 100, title: { display: true, text: 'Percentage (%)' } }
                }
            }
        });
        {% endif %}
    </script>
    </body>
    </html>
    ''', student=session, test_results=test_results, stats=stats, 
        subjects=subjects, total=total, done=done, discussed=discussed, unread_count=unread_count)

@app.route('/student/update_progress', methods=['POST'])
@login_required
def student_update_progress():
    if session.get('is_teacher'):
        return redirect(url_for('teacher_dashboard'))
    
    student_id = session['user_id']
    exercise_id = request.form['exercise_id']
    action = request.form['action']
    
    conn = get_db_connection()
    if action == 'status':
        conn.execute('UPDATE progress SET status = ? WHERE student_id = ? AND exercise_id = ?',
                    (request.form['status'], student_id, exercise_id))
        flash('Status updated!', 'success')
    elif action == 'discussed':
        conn.execute('UPDATE progress SET discussed = ? WHERE student_id = ? AND exercise_id = ?',
                    (request.form['discussed'], student_id, exercise_id))
        flash('Discussion status updated!', 'success')
    conn.commit()
    conn.close()
    return redirect(url_for('student_dashboard'))

# ====================================================================
# TEACHER DASHBOARD (Simplified for Free Hosting)
# ====================================================================

@app.route('/teacher/dashboard')
@teacher_required
def teacher_dashboard():
    subject = session.get('subject')
    user_id = session['user_id']
    unread_count = get_unread_count(user_id)
    
    filter_student = request.args.get('filter_student', '')
    filter_chapter = request.args.get('filter_chapter', '')
    
    conn = get_db_connection()
    exercises_raw = conn.execute('SELECT id, chapter, exercise_name FROM exercises WHERE subject = ? ORDER BY chapter, id', (subject,)).fetchall()
    exercises = [dict(e) for e in exercises_raw]
    students_raw = conn.execute('SELECT id, name, roll_no, batch, branch FROM students WHERE is_teacher = 0 ORDER BY name').fetchall()
    students = [dict(s) for s in students_raw]
    chapters = list(set([e['chapter'] for e in exercises]))
    chapters.sort()
    
    filtered_students = students
    filtered_exercises = exercises
    if filter_student:
        filtered_students = [s for s in students if str(s['id']) == filter_student]
    if filter_chapter:
        filtered_exercises = [e for e in exercises if e['chapter'] == filter_chapter]
    
    progress_dict = {}
    if filtered_students and filtered_exercises:
        student_ids = [s['id'] for s in filtered_students]
        exercise_ids = [e['id'] for e in filtered_exercises]
        placeholders = ','.join(['?'] * len(student_ids))
        exercise_placeholders = ','.join(['?'] * len(exercise_ids))
        all_progress = conn.execute(f'''
            SELECT p.student_id, p.exercise_id, p.status, p.discussed,
                   p.teacher_comment, s.name as student_name
            FROM progress p
            JOIN students s ON p.student_id = s.id
            WHERE p.student_id IN ({placeholders}) 
            AND p.exercise_id IN ({exercise_placeholders})
        ''', student_ids + exercise_ids).fetchall()
        for p in all_progress:
            p = dict(p)
            sid = p['student_id']
            eid = p['exercise_id']
            if sid not in progress_dict:
                progress_dict[sid] = {}
            progress_dict[sid][eid] = p
    
    conn.close()
    
    pending_counts = {}
    for ex in exercises:
        pending_count = 0
        for student in students:
            prog = progress_dict.get(student['id'], {}).get(ex['id'])
            if not prog or prog.get('status') == 'pending':
                pending_count += 1
        pending_counts[ex['id']] = pending_count
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>{{ subject }} - Teacher Dashboard</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            *{margin:0;padding:0;box-sizing:border-box;}
            body{font-family:Segoe UI,sans-serif;background:#f5f5f5;padding:20px;}
            .container{max-width:1400px;margin:0 auto;}
            .header{background:white;border-radius:15px;padding:20px 25px;margin-bottom:25px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;box-shadow:0 2px 10px rgba(0,0,0,0.08);}
            .header h1{font-size:22px;color:#333;}
            .btn-group{display:flex;gap:10px;flex-wrap:wrap;}
            .btn-sync{background:#48bb78;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;text-decoration:none;display:inline-block;}
            .btn-sync:hover{background:#38a169;}
            .btn-message{background:#667eea;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;text-decoration:none;display:inline-block;}
            .btn-message:hover{background:#5a67d8;}
            .btn-logout{background:#dc3545;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;}
            .btn-logout:hover{background:#c82333;}
            .badge{background:#e53e3e;color:white;padding:2px 8px;border-radius:10px;font-size:12px;margin-left:5px;}
            .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:20px;margin-bottom:25px;}
            .stat-card{background:white;border-radius:15px;padding:20px;text-align:center;box-shadow:0 2px 10px rgba(0,0,0,0.08);}
            .stat-card .value{font-size:28px;font-weight:bold;color:#667eea;}
            .stat-card .label{color:#666;font-size:13px;margin-top:5px;}
            .section{background:white;border-radius:15px;padding:25px;margin-bottom:25px;box-shadow:0 2px 10px rgba(0,0,0,0.08);overflow-x:auto;}
            .section-title{font-size:18px;color:#333;margin-bottom:20px;}
            .add-form{background:#f8f9fa;padding:20px;border-radius:10px;margin-bottom:20px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;}
            .add-form input{padding:10px 15px;border:2px solid #e0e0e0;border-radius:8px;flex:1;min-width:200px;}
            .add-form input:focus{outline:none;border-color:#667eea;}
            .btn-add{background:#48bb78;color:white;padding:10px 25px;border:none;border-radius:8px;cursor:pointer;font-weight:600;}
            .btn-add:hover{background:#38a169;}
            .btn-delete{background:#dc3545;color:white;padding:4px 10px;border:none;border-radius:4px;cursor:pointer;font-size:11px;}
            .btn-delete:hover{background:#c82333;}
            .filter-form{background:#f8f9fa;padding:15px 20px;border-radius:10px;margin-bottom:20px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;}
            .filter-form select{padding:10px 15px;border:2px solid #e0e0e0;border-radius:8px;min-width:150px;flex:1;}
            .filter-form select:focus{outline:none;border-color:#667eea;}
            .btn-filter{background:#667eea;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;}
            .btn-filter:hover{background:#5a67d8;}
            .btn-clear{background:#dc3545;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;text-decoration:none;display:inline-block;}
            .btn-clear:hover{background:#c82333;}
            table{width:100%;border-collapse:collapse;font-size:13px;}
            th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #e0e0e0;}
            th{background:#f8f9fa;font-weight:600;color:#333;}
            .status-done{background:#48bb78;color:white;padding:2px 8px;border-radius:4px;font-size:11px;}
            .status-pending{background:#f56565;color:white;padding:2px 8px;border-radius:4px;font-size:11px;}
            .discussed-yes{background:#48bb78;color:white;padding:2px 8px;border-radius:4px;font-size:11px;}
            .discussed-no{background:#f56565;color:white;padding:2px 8px;border-radius:4px;font-size:11px;}
            .btn-sm{padding:3px 6px;border:none;border-radius:4px;cursor:pointer;font-size:10px;margin:1px;}
            .btn-done{background:#48bb78;color:white;}
            .btn-pending{background:#f56565;color:white;}
            .btn-yes{background:#48bb78;color:white;}
            .btn-no{background:#f56565;color:white;}
            .btn-comment{background:#667eea;color:white;padding:3px 8px;border:none;border-radius:4px;cursor:pointer;font-size:10px;}
            .comment-box{width:100px;padding:4px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px;}
            .pending-count{background:#e53e3e;color:white;padding:2px 8px;border-radius:10px;font-size:11px;}
            .btn-report{background:#ed8936;color:white;padding:3px 10px;border:none;border-radius:4px;cursor:pointer;font-size:11px;text-decoration:none;display:inline-block;}
            .alert{padding:12px 15px;border-radius:10px;margin-bottom:20px;}
            .alert-success{background:#d4edda;color:#155724;border:1px solid #c3e6cb;}
            .alert-error{background:#f8d7da;color:#721c24;border:1px solid #f5c6cb;}
            @media (max-width:768px){
                .header{flex-direction:column;align-items:flex-start;gap:15px;}
                .add-form{flex-direction:column;}
                .add-form input{width:100%;}
                .filter-form{flex-direction:column;}
                .filter-form select{width:100%;}
                th,td{padding:5px 6px;font-size:10px;}
                .comment-box{width:60px;}
            }
        </style>
    </head>
    <body>
    <div class="container">
        <div class="header">
            <div><h1>📖 {{ subject }}</h1><p>Welcome, {{ session.user_name }}!</p></div>
            <div class="btn-group">
                <a href="/teacher/sync" class="btn-sync">🔄 Sync Data</a>
                <a href="/teacher/messages" class="btn-message">💬 Messages <span class="badge">{{ unread_count }}</span></a>
                <button class="btn-logout" onclick="location.href='/logout'">🚪 Logout</button>
            </div>
        </div>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <div class="stats-grid">
            <div class="stat-card"><div class="value">{{ students|length }}</div><div class="label">Students</div></div>
            <div class="stat-card"><div class="value">{{ exercises|length }}</div><div class="label">Exercises</div></div>
        </div>
        <div class="section">
            <div class="section-title">➕ Add Exercise</div>
            <div class="add-form">
                <form method="POST" action="/teacher/add_exercise" style="display:flex;flex-wrap:wrap;gap:10px;width:100%;">
                    <input type="text" name="chapter" placeholder="Chapter Name" required>
                    <input type="text" name="exercise_name" placeholder="Exercise Name" required>
                    <button type="submit" class="btn-add">➕ Add</button>
                </form>
            </div>
        </div>
        <div class="section">
            <div class="section-title">📚 Exercises</div>
            <table>
                <thead><tr><th>Chapter</th><th>Exercise</th><th>Pending Students</th><th>Action</th></tr></thead>
                <tbody>
                    {% for ex in exercises %}
                    <tr>
                        <td>{{ ex.chapter }}</td>
                        <td>{{ ex.exercise_name }}</td>
                        <td>
                            <span class="pending-count">{{ pending_counts.get(ex.id, 0) }}</span>
                            <a href="/teacher/pending_report?exercise_id={{ ex.id }}" class="btn-report">📊 View</a>
                        </td>
                        <td>
                            <form method="POST" action="/teacher/delete_exercise" style="display:inline;">
                                <input type="hidden" name="exercise_id" value="{{ ex.id }}">
                                <button type="submit" class="btn-delete" onclick="return confirm('Delete this exercise?')">🗑️</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        <div class="section">
            <div class="section-title">👨‍🎓 Student Progress</div>
            <div class="filter-form">
                <form method="GET" action="/teacher/dashboard" style="display:flex;flex-wrap:wrap;gap:10px;width:100%;">
                    <select name="filter_student">
                        <option value="">All Students</option>
                        {% for s in students %}
                        <option value="{{ s.id }}" {% if filter_student == s.id|string %}selected{% endif %}>{{ s.name }}</option>
                        {% endfor %}
                    </select>
                    <select name="filter_chapter">
                        <option value="">All Chapters</option>
                        {% for ch in chapters %}
                        <option value="{{ ch }}" {% if filter_chapter == ch %}selected{% endif %}>{{ ch }}</option>
                        {% endfor %}
                    </select>
                    <button type="submit" class="btn-filter">🔍 Apply</button>
                    <a href="/teacher/dashboard" class="btn-clear">✖ Clear</a>
                </form>
            </div>
            <table>
                <thead><tr><th>Student</th><th>Roll No</th><th>Chapter</th><th>Exercise</th><th>Status</th><th>Discussed</th><th>Comment</th><th>Actions</th></tr></thead>
                <tbody>
                    {% if filtered_students and filtered_exercises %}
                        {% for s in filtered_students %}
                            {% for ex in filtered_exercises %}
                            {% set prog = progress_dict.get(s.id, {}).get(ex.id) %}
                            <tr>
                                <td>{{ s.name }}</td>
                                <td>{{ s.roll_no or '-' }}</td>
                                <td>{{ ex.chapter }}</td>
                                <td>{{ ex.exercise_name }}</td>
                                <td>{% if prog and prog.status == 'done' %}<span class="status-done">DONE</span>{% else %}<span class="status-pending">PENDING</span>{% endif %}</td>
                                <td>{% if prog and prog.discussed == 'yes' %}<span class="discussed-yes">YES</span>{% else %}<span class="discussed-no">NO</span>{% endif %}</td>
                                <td>
                                    <form method="POST" action="/teacher/add_comment" style="display:flex;gap:4px;align-items:center;">
                                        <input type="hidden" name="student_id" value="{{ s.id }}">
                                        <input type="hidden" name="exercise_id" value="{{ ex.id }}">
                                        <input type="text" name="comment" class="comment-box" value="{{ prog.teacher_comment if prog and prog.teacher_comment else '' }}">
                                        <button type="submit" class="btn-sm btn-comment">💾</button>
                                    </form>
                                </td>
                                <td>
                                    <form method="POST" action="/teacher/update_status" style="display:inline;">
                                        <input type="hidden" name="student_id" value="{{ s.id }}">
                                        <input type="hidden" name="exercise_id" value="{{ ex.id }}">
                                        <button type="submit" name="status" value="done" class="btn-sm btn-done">✓ Done</button>
                                        <button type="submit" name="status" value="pending" class="btn-sm btn-pending">○ Pending</button>
                                    </form>
                                    <form method="POST" action="/teacher/update_discussed" style="display:inline;">
                                        <input type="hidden" name="student_id" value="{{ s.id }}">
                                        <input type="hidden" name="exercise_id" value="{{ ex.id }}">
                                        <button type="submit" name="discussed" value="yes" class="btn-sm btn-yes">✓ Yes</button>
                                        <button type="submit" name="discussed" value="no" class="btn-sm btn-no">✗ No</button>
                                    </form>
                                </td>
                            </tr>
                            {% endfor %}
                        {% endfor %}
                    {% else %}
                        <tr><td colspan="8" style="text-align:center;padding:20px;color:#666;">No records found with the selected filters.</td></tr>
                    {% endif %}
                </tbody>
            </table>
        </div>
    </div>
    </body>
    </html>
    ''', subject=subject, students=students, exercises=exercises,
        filtered_students=filtered_students, filtered_exercises=filtered_exercises,
        progress_dict=progress_dict, unread_count=unread_count,
        filter_student=filter_student, filter_chapter=filter_chapter,
        chapters=chapters, pending_counts=pending_counts)

# ====================================================================
# TEACHER ACTIONS
# ====================================================================

@app.route('/teacher/add_exercise', methods=['POST'])
@teacher_required
def add_exercise_route():
    chapter = request.form['chapter']
    exercise_name = request.form['exercise_name']
    subject = session.get('subject')
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO exercises (subject, chapter, exercise_name, created_by) VALUES (?, ?, ?, ?)',
                    (subject, chapter, exercise_name, session['user_id']))
        exercise_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        students = conn.execute('SELECT id FROM students WHERE is_teacher = 0').fetchall()
        for student in students:
            conn.execute('INSERT OR IGNORE INTO progress (student_id, exercise_id, status, discussed) VALUES (?, ?, "pending", "no")',
                        (student[0], exercise_id))
        conn.commit()
        flash('Exercise added!', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    finally:
        conn.close()
    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/delete_exercise', methods=['POST'])
@teacher_required
def delete_exercise_route():
    exercise_id = request.form['exercise_id']
    subject = session.get('subject')
    conn = get_db_connection()
    try:
        exercise = conn.execute('SELECT subject FROM exercises WHERE id = ?', (exercise_id,)).fetchone()
        if exercise and exercise[0] == subject:
            conn.execute('DELETE FROM progress WHERE exercise_id = ?', (exercise_id,))
            conn.execute('DELETE FROM exercises WHERE id = ?', (exercise_id,))
            conn.commit()
            flash('Exercise deleted!', 'success')
        else:
            flash('Error deleting exercise', 'error')
    except:
        flash('Error deleting exercise', 'error')
    finally:
        conn.close()
    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/add_comment', methods=['POST'])
@teacher_required
def add_comment_route():
    conn = get_db_connection()
    conn.execute('UPDATE progress SET teacher_comment = ? WHERE student_id = ? AND exercise_id = ?',
                (request.form['comment'], request.form['student_id'], request.form['exercise_id']))
    conn.commit()
    conn.close()
    flash('Comment saved!', 'success')
    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/update_status', methods=['POST'])
@teacher_required
def teacher_update_status():
    conn = get_db_connection()
    conn.execute('UPDATE progress SET status = ? WHERE student_id = ? AND exercise_id = ?',
                (request.form['status'], request.form['student_id'], request.form['exercise_id']))
    conn.commit()
    conn.close()
    flash('Status updated!', 'success')
    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/update_discussed', methods=['POST'])
@teacher_required
def teacher_update_discussed():
    conn = get_db_connection()
    conn.execute('UPDATE progress SET discussed = ? WHERE student_id = ? AND exercise_id = ?',
                (request.form['discussed'], request.form['student_id'], request.form['exercise_id']))
    conn.commit()
    conn.close()
    flash('Discussed status updated!', 'success')
    return redirect(url_for('teacher_dashboard'))

# ====================================================================
# PENDING REPORT
# ====================================================================

@app.route('/teacher/pending_report')
@teacher_required
def pending_report():
    exercise_id = request.args.get('exercise_id')
    if not exercise_id:
        flash('No exercise selected', 'error')
        return redirect(url_for('teacher_dashboard'))
    subject = session.get('subject')
    conn = get_db_connection()
    exercise = conn.execute('SELECT id, chapter, exercise_name FROM exercises WHERE id = ? AND subject = ?', (exercise_id, subject)).fetchone()
    if not exercise:
        conn.close()
        flash('Exercise not found', 'error')
        return redirect(url_for('teacher_dashboard'))
    pending_students = conn.execute('''
        SELECT s.id, s.name, s.roll_no, s.batch, p.status, p.discussed, p.teacher_comment
        FROM students s
        JOIN progress p ON s.id = p.student_id
        WHERE p.exercise_id = ? AND p.status = 'pending' AND s.is_teacher = 0
        ORDER BY s.name
    ''', (exercise_id,)).fetchall()
    completed_students = conn.execute('''
        SELECT s.id, s.name, s.roll_no, s.batch, p.status, p.discussed, p.teacher_comment
        FROM students s
        JOIN progress p ON s.id = p.student_id
        WHERE p.exercise_id = ? AND p.status = 'done' AND s.is_teacher = 0
        ORDER BY s.name
    ''', (exercise_id,)).fetchall()
    conn.close()
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>Pending Report</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box;}
        body{font-family:Segoe UI,sans-serif;background:#f5f5f5;padding:20px;}
        .container{max-width:1000px;margin:0 auto;background:white;border-radius:15px;padding:25px;box-shadow:0 2px 10px rgba(0,0,0,0.08);}
        .header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;margin-bottom:25px;padding-bottom:20px;border-bottom:2px solid #e0e0e0;}
        .back-btn{background:#667eea;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;display:inline-block;}
        .logout-btn{background:#dc3545;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;display:inline-block;}
        .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:20px;margin-bottom:25px;}
        .stat-card{background:linear-gradient(135deg,#667eea,#764ba2);color:white;padding:20px;border-radius:10px;text-align:center;}
        .stat-number{font-size:32px;font-weight:bold;}
        .stat-label{font-size:14px;opacity:0.9;}
        table{width:100%;border-collapse:collapse;font-size:13px;margin-top:15px;}
        th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #e0e0e0;}
        th{background:#f8f9fa;font-weight:600;color:#333;}
        .status-pending{background:#f56565;color:white;padding:2px 8px;border-radius:4px;font-size:11px;}
        .status-done{background:#48bb78;color:white;padding:2px 8px;border-radius:4px;font-size:11px;}
        .alert-success{background:#d4edda;color:#155724;padding:15px;border-radius:10px;margin-bottom:20px;}
        .alert-info{background:#d1ecf1;color:#0c5460;padding:15px;border-radius:10px;margin-bottom:20px;}
        h3{margin-top:25px;margin-bottom:10px;color:#333;}
        @media (max-width:768px){.header{flex-direction:column;align-items:flex-start;gap:15px;}}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="header">
            <div><h1>📊 Pending Students Report</h1><p><strong>{{ exercise.chapter }}</strong> - {{ exercise.exercise_name }}</p></div>
            <div><a href="/teacher/dashboard" class="back-btn">← Back</a><a href="/logout" class="logout-btn">Logout</a></div>
        </div>
        <div class="stats-grid">
            <div class="stat-card"><div class="stat-number">{{ pending_students|length }}</div><div class="stat-label">Pending Students</div></div>
            <div class="stat-card"><div class="stat-number">{{ completed_students|length }}</div><div class="stat-label">Completed Students</div></div>
            <div class="stat-card"><div class="stat-number">{{ pending_students|length + completed_students|length }}</div><div class="stat-label">Total Students</div></div>
        </div>
        {% if pending_students|length == 0 %}
            <div class="alert-success">🎉 All students have completed this exercise!</div>
        {% else %}
            <div class="alert-info">📋 {{ pending_students|length }} student(s) haven't completed this exercise yet.</div>
            <h3>⚠️ Pending Students</h3>
            <table><thead><tr><th>Name</th><th>Roll No</th><th>Batch</th><th>Status</th><th>Discussed</th><th>Comment</th></tr></thead>
            <tbody>
                {% for s in pending_students %}
                <tr><td>{{ s.name }}</td><td>{{ s.roll_no or '-' }}</td><td>{{ s.batch or '-' }}</td><td><span class="status-pending">PENDING</span></td><td>{{ s.discussed.upper() if s.discussed else 'NO' }}</td><td>{{ s.teacher_comment or '-' }}</td></tr>
                {% endfor %}
            </tbody></table>
            <h3>✅ Completed Students</h3>
            <table><thead><tr><th>Name</th><th>Roll No</th><th>Batch</th><th>Status</th><th>Discussed</th><th>Comment</th></tr></thead>
            <tbody>
                {% for s in completed_students %}
                <tr><td>{{ s.name }}</td><td>{{ s.roll_no or '-' }}</td><td>{{ s.batch or '-' }}</td><td><span class="status-done">DONE</span></td><td>{{ s.discussed.upper() if s.discussed else 'NO' }}</td><td>{{ s.teacher_comment or '-' }}</td></tr>
                {% endfor %}
            </tbody></table>
        {% endif %}
    </div>
    </body>
    </html>
    ''', exercise=exercise, pending_students=pending_students, completed_students=completed_students)

# ====================================================================
# MESSAGING (Simplified)
# ====================================================================

@app.route('/teacher/messages')
@teacher_required
def teacher_messages():
    user_id = session['user_id']
    messages = get_all_messages(user_id)
    students = get_students()
    conversations = {}
    for msg in messages:
        other_id = msg['to_id'] if msg['from_id'] == user_id else msg['from_id']
        if other_id not in conversations:
            other_name = msg['to_name'] if msg['from_id'] == user_id else msg['from_name']
            conversations[other_id] = {'name': other_name, 'messages': [], 'unread_count': 0}
        conversations[other_id]['messages'].append(msg)
        if msg['to_id'] == user_id and msg['is_read'] == 0:
            conversations[other_id]['unread_count'] += 1
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>Messages</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box;}
        body{font-family:Segoe UI,sans-serif;background:#f5f5f5;padding:20px;}
        .container{max-width:1200px;margin:0 auto;background:white;border-radius:15px;padding:25px;}
        .header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;margin-bottom:25px;padding-bottom:20px;border-bottom:2px solid #e0e0e0;}
        .back-btn{background:#667eea;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;display:inline-block;}
        .logout-btn{background:#dc3545;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;display:inline-block;}
        .conversation{background:#f8f9fa;border-radius:10px;margin-bottom:15px;overflow:hidden;border:1px solid #e0e0e0;}
        .conversation-header{background:#667eea;color:white;padding:12px 20px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;}
        .conversation-header:hover{background:#5a67d8;}
        .conversation-messages{padding:20px;max-height:400px;overflow-y:auto;display:none;}
        .conversation-messages.active{display:block;}
        .message{border-left:4px solid #667eea;padding:12px;margin-bottom:10px;background:white;border-radius:4px;}
        .message.sent{border-left-color:#48bb78;}
        .message.received{border-left-color:#667eea;}
        .message-header{display:flex;justify-content:space-between;font-size:12px;color:#666;margin-bottom:5px;flex-wrap:wrap;}
        .message-body{font-size:14px;color:#333;}
        .message-reply{background:#f8f9fa;padding:15px;border-radius:8px;margin-top:10px;}
        .message-reply textarea{width:100%;padding:10px;border:1px solid #ddd;border-radius:5px;height:60px;font-family:inherit;}
        .message-reply .btn-reply{background:#48bb78;color:white;padding:8px 20px;border:none;border-radius:4px;cursor:pointer;margin-top:10px;}
        .unread-badge{background:#e53e3e;color:white;padding:2px 8px;border-radius:10px;font-size:12px;}
        .send-form{background:#f8f9fa;padding:20px;border-radius:10px;margin-bottom:30px;}
        .form-group{margin-bottom:15px;}
        label{display:block;margin-bottom:5px;font-weight:500;font-size:14px;}
        input[type=text],select,textarea{width:100%;padding:10px 12px;border:2px solid #e0e0e0;border-radius:8px;font-family:inherit;font-size:14px;}
        input[type=text]:focus,select:focus,textarea:focus{outline:none;border-color:#667eea;}
        textarea{height:80px;resize:vertical;}
        .btn-send{background:#48bb78;color:white;padding:10px 25px;border:none;border-radius:8px;cursor:pointer;font-weight:600;}
        .btn-send:hover{background:#38a169;}
        .btn-read{background:#667eea;color:white;padding:3px 10px;border:none;border-radius:4px;cursor:pointer;font-size:11px;}
        .btn-read:hover{background:#5a67d8;}
        .alert{padding:12px 15px;border-radius:10px;margin-bottom:20px;}
        .alert-success{background:#d4edda;color:#155724;border:1px solid #c3e6cb;}
        @media (max-width:768px){.header{flex-direction:column;align-items:flex-start;gap:15px;}}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="header"><div><h1>💬 Messages</h1></div><div><a href="/teacher/dashboard" class="back-btn">← Back</a><a href="/logout" class="logout-btn">Logout</a></div></div>
        {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}<div class="alert alert-success">{{ messages[0][1] }}</div>{% endif %}{% endwith %}
        <div class="send-form"><h3>📤 New Message</h3>
        <form method="POST" action="/teacher/send_message">
            <div class="form-group"><label>Student</label><select name="to_id" required><option value="">Select Student</option>{% for s in students %}<option value="{{ s.id }}">{{ s.name }} ({{ s.roll_no }})</option>{% endfor %}</select></div>
            <div class="form-group"><label>Subject</label><input type="text" name="subject" required></div>
            <div class="form-group"><label>Message</label><textarea name="message" required></textarea></div>
            <button type="submit" class="btn-send">📤 Send</button>
        </form></div>
        <h3>📥 Conversations</h3>
        {% if conversations|length == 0 %}<p style="color:#666;margin-top:15px;">No conversations yet.</p>
        {% else %}
            {% for other_id, conv in conversations.items() %}
            <div class="conversation">
                <div class="conversation-header" onclick="toggleConversation(this)">
                    <span><strong>{{ conv.name }}</strong>{% if conv.unread_count > 0 %}<span class="unread-badge">{{ conv.unread_count }} new</span>{% endif %}</span>
                    <span>{{ conv.messages|length }} messages</span>
                </div>
                <div class="conversation-messages">
                    {% for msg in conv.messages|reverse %}
                    <div class="message {% if msg.from_id == session.user_id %}sent{% else %}received{% endif %}">
                        <div class="message-header"><span><strong>{{ msg.from_name }}</strong> → {{ msg.to_name }}</span><span>{{ msg.created_at[:16] }}</span></div>
                        <div class="message-body"><strong>{{ msg.subject }}</strong><br>{{ msg.message }}</div>
                        {% if msg.to_id == session.user_id and msg.is_read == 0 %}
                        <form method="POST" action="/teacher/mark_read" style="margin-top:5px;"><input type="hidden" name="message_id" value="{{ msg.id }}"><button type="submit" class="btn-read">✓ Mark Read</button></form>{% endif %}
                    </div>
                    {% endfor %}
                    <div class="message-reply">
                        <form method="POST" action="/teacher/send_reply">
                            <input type="hidden" name="to_id" value="{{ other_id }}">
                            <input type="hidden" name="subject" value="Re: {{ conv.messages[0].subject if conv.messages else 'Reply' }}">
                            <textarea name="message" placeholder="Type your reply..." required></textarea>
                            <button type="submit" class="btn-reply">💬 Reply</button>
                        </form>
                    </div>
                </div>
            </div>
            {% endfor %}
        {% endif %}
    </div>
    <script>function toggleConversation(header){const messages=header.nextElementSibling;messages.classList.toggle('active');const span=header.querySelector('span:last-child');if(messages.classList.contains('active')){span.textContent='▼ hide';}else{span.textContent='▼ show';}}</script>
    </body>
    </html>
    ''', conversations=conversations, students=students)

@app.route('/teacher/send_message', methods=['POST'])
@teacher_required
def teacher_send_message():
    send_message(session['user_id'], request.form['to_id'], request.form['subject'], request.form['message'])
    flash('Message sent!', 'success')
    return redirect(url_for('teacher_messages'))

@app.route('/teacher/send_reply', methods=['POST'])
@teacher_required
def teacher_send_reply():
    send_message(session['user_id'], request.form['to_id'], request.form['subject'], request.form['message'])
    flash('Reply sent!', 'success')
    return redirect(url_for('teacher_messages'))

@app.route('/teacher/mark_read', methods=['POST'])
@teacher_required
def teacher_mark_read():
    mark_message_read(request.form['message_id'])
    flash('Marked as read', 'success')
    return redirect(url_for('teacher_messages'))

@app.route('/student/messages')
@login_required
def student_messages():
    if session.get('is_teacher'):
        return redirect(url_for('teacher_messages'))
    user_id = session['user_id']
    messages = get_all_messages(user_id)
    conn = get_db_connection()
    teachers = conn.execute('SELECT id, name, subject FROM students WHERE is_teacher = 1 ORDER BY name').fetchall()
    conn.close()
    teachers = [dict(t) for t in teachers]
    conversations = {}
    for msg in messages:
        other_id = msg['to_id'] if msg['from_id'] == user_id else msg['from_id']
        if other_id not in conversations:
            other_name = msg['to_name'] if msg['from_id'] == user_id else msg['from_name']
            conversations[other_id] = {'name': other_name, 'messages': [], 'unread_count': 0}
        conversations[other_id]['messages'].append(msg)
        if msg['to_id'] == user_id and msg['is_read'] == 0:
            conversations[other_id]['unread_count'] += 1
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>Messages</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box;}
        body{font-family:Segoe UI,sans-serif;background:#f5f5f5;padding:20px;}
        .container{max-width:1200px;margin:0 auto;background:white;border-radius:15px;padding:25px;}
        .header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;margin-bottom:25px;padding-bottom:20px;border-bottom:2px solid #e0e0e0;}
        .back-btn{background:#667eea;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;display:inline-block;}
        .logout-btn{background:#dc3545;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;display:inline-block;}
        .conversation{background:#f8f9fa;border-radius:10px;margin-bottom:15px;overflow:hidden;border:1px solid #e0e0e0;}
        .conversation-header{background:#667eea;color:white;padding:12px 20px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;}
        .conversation-header:hover{background:#5a67d8;}
        .conversation-messages{padding:20px;max-height:400px;overflow-y:auto;display:none;}
        .conversation-messages.active{display:block;}
        .message{border-left:4px solid #667eea;padding:12px;margin-bottom:10px;background:white;border-radius:4px;}
        .message.sent{border-left-color:#48bb78;}
        .message.received{border-left-color:#667eea;}
        .message-header{display:flex;justify-content:space-between;font-size:12px;color:#666;margin-bottom:5px;flex-wrap:wrap;}
        .message-body{font-size:14px;color:#333;}
        .message-reply{background:#f8f9fa;padding:15px;border-radius:8px;margin-top:10px;}
        .message-reply textarea{width:100%;padding:10px;border:1px solid #ddd;border-radius:5px;height:60px;font-family:inherit;}
        .message-reply .btn-reply{background:#48bb78;color:white;padding:8px 20px;border:none;border-radius:4px;cursor:pointer;margin-top:10px;}
        .unread-badge{background:#e53e3e;color:white;padding:2px 8px;border-radius:10px;font-size:12px;}
        .send-form{background:#f8f9fa;padding:20px;border-radius:10px;margin-bottom:30px;}
        .form-group{margin-bottom:15px;}
        label{display:block;margin-bottom:5px;font-weight:500;font-size:14px;}
        input[type=text],select,textarea{width:100%;padding:10px 12px;border:2px solid #e0e0e0;border-radius:8px;font-family:inherit;font-size:14px;}
        input[type=text]:focus,select:focus,textarea:focus{outline:none;border-color:#667eea;}
        textarea{height:80px;resize:vertical;}
        .btn-send{background:#48bb78;color:white;padding:10px 25px;border:none;border-radius:8px;cursor:pointer;font-weight:600;}
        .btn-send:hover{background:#38a169;}
        .btn-read{background:#667eea;color:white;padding:3px 10px;border:none;border-radius:4px;cursor:pointer;font-size:11px;}
        .btn-read:hover{background:#5a67d8;}
        .alert{padding:12px 15px;border-radius:10px;margin-bottom:20px;}
        .alert-success{background:#d4edda;color:#155724;border:1px solid #c3e6cb;}
        @media (max-width:768px){.header{flex-direction:column;align-items:flex-start;gap:15px;}}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="header"><div><h1>💬 Messages</h1></div><div><a href="/student/dashboard" class="back-btn">← Back</a><a href="/logout" class="logout-btn">Logout</a></div></div>
        {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}<div class="alert alert-success">{{ messages[0][1] }}</div>{% endif %}{% endwith %}
        <div class="send-form"><h3>📤 New Message</h3>
        <form method="POST" action="/student/send_message">
            <div class="form-group"><label>Teacher</label><select name="to_id" required><option value="">Select Teacher</option>{% for t in teachers %}<option value="{{ t.id }}">{{ t.name }} ({{ t.subject }})</option>{% endfor %}</select></div>
            <div class="form-group"><label>Subject</label><input type="text" name="subject" required></div>
            <div class="form-group"><label>Message</label><textarea name="message" required></textarea></div>
            <button type="submit" class="btn-send">📤 Send</button>
        </form></div>
        <h3>📥 Conversations</h3>
        {% if conversations|length == 0 %}<p style="color:#666;margin-top:15px;">No conversations yet.</p>
        {% else %}
            {% for other_id, conv in conversations.items() %}
            <div class="conversation">
                <div class="conversation-header" onclick="toggleConversation(this)">
                    <span><strong>{{ conv.name }}</strong>{% if conv.unread_count > 0 %}<span class="unread-badge">{{ conv.unread_count }} new</span>{% endif %}</span>
                    <span>{{ conv.messages|length }} messages</span>
                </div>
                <div class="conversation-messages">
                    {% for msg in conv.messages|reverse %}
                    <div class="message {% if msg.from_id == session.user_id %}sent{% else %}received{% endif %}">
                        <div class="message-header"><span><strong>{{ msg.from_name }}</strong> → {{ msg.to_name }}</span><span>{{ msg.created_at[:16] }}</span></div>
                        <div class="message-body"><strong>{{ msg.subject }}</strong><br>{{ msg.message }}</div>
                        {% if msg.to_id == session.user_id and msg.is_read == 0 %}
                        <form method="POST" action="/student/mark_read" style="margin-top:5px;"><input type="hidden" name="message_id" value="{{ msg.id }}"><button type="submit" class="btn-read">✓ Mark Read</button></form>{% endif %}
                    </div>
                    {% endfor %}
                    <div class="message-reply">
                        <form method="POST" action="/student/send_reply">
                            <input type="hidden" name="to_id" value="{{ other_id }}">
                            <input type="hidden" name="subject" value="Re: {{ conv.messages[0].subject if conv.messages else 'Reply' }}">
                            <textarea name="message" placeholder="Type your reply..." required></textarea>
                            <button type="submit" class="btn-reply">💬 Reply</button>
                        </form>
                    </div>
                </div>
            </div>
            {% endfor %}
        {% endif %}
    </div>
    <script>function toggleConversation(header){const messages=header.nextElementSibling;messages.classList.toggle('active');const span=header.querySelector('span:last-child');if(messages.classList.contains('active')){span.textContent='▼ hide';}else{span.textContent='▼ show';}}</script>
    </body>
    </html>
    ''', conversations=conversations, teachers=teachers)

@app.route('/student/send_message', methods=['POST'])
@login_required
def student_send_message():
    if session.get('is_teacher'):
        return redirect(url_for('teacher_dashboard'))
    send_message(session['user_id'], request.form['to_id'], request.form['subject'], request.form['message'])
    flash('Message sent!', 'success')
    return redirect(url_for('student_messages'))

@app.route('/student/send_reply', methods=['POST'])
@login_required
def student_send_reply():
    if session.get('is_teacher'):
        return redirect(url_for('teacher_dashboard'))
    send_message(session['user_id'], request.form['to_id'], request.form['subject'], request.form['message'])
    flash('Reply sent!', 'success')
    return redirect(url_for('student_messages'))

@app.route('/student/mark_read', methods=['POST'])
@login_required
def student_mark_read():
    mark_message_read(request.form['message_id'])
    flash('Marked as read', 'success')
    return redirect(url_for('student_messages'))

# ====================================================================
# SYNC ROUTE
# ====================================================================

@app.route('/teacher/sync')
@teacher_required
def sync_data():
    try:
        sync_students_from_google_sheets()
        sync_test_results_from_google_sheets()
        flash('✅ Data synced from Google Sheets successfully!', 'success')
    except Exception as e:
        flash(f'⚠️ Error syncing: {str(e)}', 'error')
    return redirect(url_for('teacher_dashboard'))

# ====================================================================
# HEALTH CHECK FOR FREE HOSTING
# ====================================================================

@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'database': 'connected',
        'students': len(get_students()),
        'timestamp': datetime.now().isoformat()
    })

# ====================================================================
# RUN THE APP
# ====================================================================

if __name__ == '__main__':
    init_db()
    print("=" * 70)
    print("🎓 BAKLIWAL TUTORIALS - Complete Unified Student Portal")
    print("📱 Combines: Exercise Tracker + Marks Dashboard + Messaging")
    print("👥 Handles 450+ students with Google Sheets sync")
    print("=" * 70)
    
    # Initial sync (if credentials available)
    try:
        sync_students_from_google_sheets()
        sync_test_results_from_google_sheets()
    except Exception as e:
        print(f"⚠️ Initial sync warning: {e}")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
