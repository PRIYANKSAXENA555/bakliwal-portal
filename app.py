# ====================================================================
# BAKLIWAL TUTORIALS - COMPLETE STUDENT PORTAL
# WORKING VERSION FOR RENDER.COM
# ====================================================================

import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-this')

# Database path - Render uses /tmp for writable storage
DB_PATH = '/tmp/students.db'

# Google Sheets Configuration
SHEET_NAME = os.environ.get('SHEET_NAME', 'Master Sheet')
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

# Try to import Google Sheets
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    HAS_GSHEETS = True
except ImportError:
    HAS_GSHEETS = False
    print("⚠️ Google Sheets not installed")

print(f"✅ Starting Bakliwal Portal")
print(f"📊 Database path: {DB_PATH}")
print(f"📝 Sheet name: {SHEET_NAME}")

# ====================================================================
# DATABASE SETUP
# ====================================================================

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create all tables
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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL,
            subject TEXT,
            message TEXT NOT NULL,
            parent_id INTEGER DEFAULT NULL,
            is_read BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

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
            ('Chemistry', 'Chapter 1: Atoms', 'Exercise 1.1 - Atomic Structure'),
            ('Chemistry', 'Chapter 1: Atoms', 'Exercise 1.2 - Periodic Table'),
            ('Mathematics', 'Chapter 1: Algebra', 'Exercise 1.1 - Linear Equations'),
            ('Mathematics', 'Chapter 1: Algebra', 'Exercise 1.2 - Quadratic Equations')
        ]
        for subject, chapter, exercise_name in sample_exercises:
            cursor.execute('''
                INSERT OR IGNORE INTO exercises (subject, chapter, exercise_name)
                VALUES (?, ?, ?)
            ''', (subject, chapter, exercise_name))

    # Add sample students for testing
    cursor.execute('SELECT COUNT(*) as count FROM students WHERE is_teacher = 0')
    if cursor.fetchone()[0] == 0:
        sample_students = [
            ('KETKI KULKARNI', '23000010', 'Varsha', 'WOW', 'JS'),
            ('KARTIK KULKARNI', '23000009', 'Smita', 'WOW', 'JS'),
            ('JAYRAJ HUGAR', '23000008', 'Jayashri', 'WOW', 'JS'),
            ('OJAS HUMNABADKAR', '25800325', 'Pratibha', 'WOW', 'JS'),
            ('SATVIKA MORE', '23000017', 'Manisha', 'WOW', 'JS'),
        ]
        for name, roll, mother, batch, branch in sample_students:
            password_hash = generate_password_hash(mother.lower())
            cursor.execute('''
                INSERT INTO students (name, roll_no, mother_name, batch, branch, password_hash, is_teacher)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (name, roll, mother, batch, branch, password_hash))
            student_id = cursor.lastrowid
            exercises = cursor.execute('SELECT id FROM exercises').fetchall()
            for ex in exercises:
                cursor.execute('''
                    INSERT OR IGNORE INTO progress (student_id, exercise_id, status, discussed)
                    VALUES (?, ?, 'pending', 'no')
                ''', (student_id, ex[0]))

    conn.commit()
    conn.close()
    print("✅ Database initialized successfully!")

# ====================================================================
# HELPER FUNCTIONS
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
# GOOGLE SHEETS SYNC
# ====================================================================

def get_google_sheets_client():
    if not HAS_GSHEETS or not GOOGLE_CREDENTIALS_JSON:
        return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"⚠️ Google Sheets error: {e}")
        return None

def sync_students_from_google_sheets():
    client = get_google_sheets_client()
    if not client:
        return
    try:
        spreadsheet = client.open(SHEET_NAME)
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
            return
        try:
            mother_sheet = spreadsheet.worksheet('Mother Name')
            mother_data = mother_sheet.get_all_values()
            mother_dict = {}
            for row in mother_data[1:]:
                if len(row) >= 2:
                    mother_dict[row[0].strip()] = row[1].strip()
        except:
            mother_dict = {}
        conn = get_db_connection()
        cursor = conn.cursor()
        existing_rolls = set([row[0] for row in cursor.execute('SELECT roll_no FROM students WHERE is_teacher = 0').fetchall()])
        new_rolls = set()
        for row in data[1:]:
            if len(row) <= max(roll_idx, name_idx):
                continue
            roll_no = str(row[roll_idx]).strip()
            if not roll_no:
                continue
            new_rolls.add(roll_no)
            name = str(row[name_idx]).strip() if name_idx != -1 else 'Student'
            batch = str(row[batch_idx]).strip() if batch_idx != -1 else ''
            branch = str(row[branch_idx]).strip() if branch_idx != -1 else ''
            mother_name = mother_dict.get(name, mother_dict.get(roll_no, 'password'))
            password_hash = generate_password_hash(mother_name.lower())
            if roll_no in existing_rolls:
                cursor.execute('UPDATE students SET name=?, mother_name=?, batch=?, branch=?, password_hash=? WHERE roll_no=?',
                              (name, mother_name, batch, branch, password_hash, roll_no))
            else:
                cursor.execute('INSERT INTO students (name, roll_no, mother_name, batch, branch, password_hash, is_teacher) VALUES (?, ?, ?, ?, ?, ?, 0)',
                              (name, roll_no, mother_name, batch, branch, password_hash))
                student_id = cursor.lastrowid
                exercises = cursor.execute('SELECT id FROM exercises').fetchall()
                for ex in exercises:
                    cursor.execute('INSERT OR IGNORE INTO progress (student_id, exercise_id, status, discussed) VALUES (?, ?, "pending", "no")',
                                  (student_id, ex[0]))
        conn.commit()
        conn.close()
        print(f"✅ Synced students")
    except Exception as e:
        print(f"⚠️ Sync error: {e}")

# ====================================================================
# ROUTES
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
            flash('Invalid credentials', 'error')
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>Bakliwal Tutorials</title>
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
        input{width:100%;padding:12px 15px;border:2px solid #e0e0e0;border-radius:10px;font-size:16px;}
        input:focus{outline:none;border-color:#667eea;}
        button{width:100%;padding:14px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;border:none;border-radius:10px;font-size:16px;font-weight:600;cursor:pointer;}
        button:hover{transform:translateY(-2px);}
        .alert{padding:12px 15px;border-radius:10px;margin-bottom:20px;}
        .alert-success{background:#d4edda;color:#155724;}
        .alert-error{background:#f8d7da;color:#721c24;}
        .info-text{margin-top:20px;font-size:12px;color:#666;text-align:center;line-height:1.8;}
        .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;margin:2px;}
        .badge-physics{background:#4299e1;color:white;}
        .badge-chemistry{background:#ed8936;color:white;}
        .badge-maths{background:#9f7aea;color:white;}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="logo"><h1>🎓 Bakliwal Tutorials</h1><p>JEE 2027 - Student Portal</p></div>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="POST">
            <div class="form-group"><label>📝 Username</label><input type="text" name="username" placeholder="Name or Roll No" required></div>
            <div class="form-group"><label>🔑 Password</label><input type="password" name="password" placeholder="Mother's Name" required></div>
            <button type="submit">🔓 Login</button>
        </form>
        <div class="info-text">
            <strong>👨‍🎓 Students:</strong> Name or Roll No / Mother's Name<br>
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
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>Student Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        *{margin:0;padding:0;box-sizing:border-box;}
        body{font-family:Segoe UI,sans-serif;background:#f5f5f5;padding:20px;}
        .container{max-width:1200px;margin:0 auto;}
        .header{background:white;border-radius:15px;padding:20px 25px;margin-bottom:25px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;box-shadow:0 2px 10px rgba(0,0,0,0.08);}
        .header h1{font-size:22px;color:#333;}
        .btn-group{display:flex;gap:10px;flex-wrap:wrap;}
        .btn-message{background:#667eea;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;text-decoration:none;display:inline-block;}
        .btn-logout{background:#dc3545;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;}
        .badge{background:#e53e3e;color:white;padding:2px 8px;border-radius:10px;font-size:12px;margin-left:5px;}
        .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:20px;margin-bottom:30px;}
        .stat-card{background:white;border-radius:15px;padding:20px;text-align:center;box-shadow:0 2px 10px rgba(0,0,0,0.08);}
        .stat-card .value{font-size:28px;font-weight:bold;color:#667eea;}
        .stat-card .label{color:#666;font-size:13px;margin-top:5px;}
        .section{background:white;border-radius:15px;padding:25px;margin-bottom:25px;box-shadow:0 2px 10px rgba(0,0,0,0.08);overflow-x:auto;}
        .section-title{font-size:18px;color:#333;margin-bottom:20px;}
        .subject-title{padding:12px 15px;font-weight:bold;color:white;border-radius:8px;margin-bottom:10px;}
        .subject-physics{background:#4299e1;}
        .subject-chemistry{background:#ed8936;}
        .subject-mathematics{background:#9f7aea;}
        table{width:100%;border-collapse:collapse;font-size:13px;}
        th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #e0e0e0;}
        th{background:#f8f9fa;font-weight:600;}
        .status-done{background:#48bb78;color:white;padding:3px 8px;border-radius:4px;font-size:11px;}
        .status-pending{background:#f56565;color:white;padding:3px 8px;border-radius:4px;font-size:11px;}
        .btn-sm{padding:4px 8px;border:none;border-radius:4px;cursor:pointer;font-size:11px;margin:1px;}
        .btn-done{background:#48bb78;color:white;}
        .btn-pending{background:#f56565;color:white;}
        .rank-badge{padding:2px 10px;border-radius:20px;font-weight:600;font-size:12px;}
        .rank-good{background:#d4edda;color:#155724;}
        .rank-avg{background:#fff3cd;color:#856404;}
        .rank-low{background:#f8d7da;color:#721c24;}
        @media (max-width:768px){.header{flex-direction:column;align-items:flex-start;gap:15px;}}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="header">
            <div><h1>👋 Welcome, {{ session.user_name }}!</h1><p>Roll No: <strong>{{ session.roll_no }}</strong></p></div>
            <div class="btn-group">
                <a href="/student/messages" class="btn-message">💬 Messages <span class="badge">{{ unread_count }}</span></a>
                <button class="btn-logout" onclick="location.href='/logout'">🚪 Logout</button>
            </div>
        </div>
        <div class="stats-grid">
            <div class="stat-card"><div class="value">{{ done }}/{{ total }}</div><div class="label">Exercises Done</div></div>
            <div class="stat-card"><div class="value">{{ "%.0f"|format((done/total*100) if total>0 else 0) }}%</div><div class="label">Completion</div></div>
            <div class="stat-card"><div class="value">{{ stats.total_tests or 0 }}</div><div class="label">Tests Given</div></div>
            <div class="stat-card"><div class="value">{{ "%.1f"|format(stats.avg_percentage or 0) }}%</div><div class="label">Avg Score</div></div>
        </div>
        <div class="section">
            <div class="section-title">📝 Exercise Progress</div>
            {% for subject, exercises in subjects.items() %}
            <div class="subject-title subject-{{ subject.lower() }}">{{ subject }}</div>
            <table>
                <thead><tr><th>Chapter</th><th>Exercise</th><th>Status</th><th>Actions</th></tr></thead>
                <tbody>
                    {% for ex in exercises %}
                    <tr>
                        <td>{{ ex.chapter }}</td>
                        <td>{{ ex.exercise_name }}</td>
                        <td><span class="status-{{ ex.status }}">{{ ex.status.upper() }}</span></td>
                        <td>
                            <form method="POST" action="/student/update_progress" style="display:inline;">
                                <input type="hidden" name="exercise_id" value="{{ ex.exercise_id }}">
                                <button type="submit" name="status" value="done" class="btn-sm btn-done">✓ Done</button>
                                <button type="submit" name="status" value="pending" class="btn-sm btn-pending">○ Pending</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% endfor %}
        </div>
        <div class="section">
            <div class="section-title">📋 Test Results</div>
            {% if test_results|length > 0 %}
            <table>
                <thead><tr><th>Test</th><th>Rank</th><th>Physics</th><th>Chemistry</th><th>Maths</th><th>Total</th><th>%</th></tr></thead>
                <tbody>
                    {% for r in test_results %}
                    {% set rank_num = r.rank|int if r.rank != '-' and r.rank else 999 %}
                    {% set rank_class = 'rank-good' if rank_num <= 50 else ('rank-avg' if rank_num <= 100 else 'rank-low') %}
                    <tr>
                        <td>{{ r.test_name }}</td>
                        <td><span class="rank-badge {{ rank_class }}">#{{ r.rank }}</span></td>
                        <td>{{ "%.0f"|format(r.physics_marks) }}/{{ "%.0f"|format(r.physics_max) }}</td>
                        <td>{{ "%.0f"|format(r.chemistry_marks) }}/{{ "%.0f"|format(r.chemistry_max) }}</td>
                        <td>{{ "%.0f"|format(r.maths_marks) }}/{{ "%.0f"|format(r.maths_max) }}</td>
                        <td><strong>{{ "%.0f"|format(r.total_marks) }}</strong>/{{ "%.0f"|format(r.total_max) }}</td>
                        <td>{{ "%.1f"|format(r.percentage) }}%</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p style="padding:20px;color:#666;">No test results available yet.</p>
            {% endif %}
        </div>
    </div>
    </body>
    </html>
    ''', subjects=subjects, total=total, done=done, test_results=test_results, stats=stats, unread_count=unread_count)

@app.route('/student/update_progress', methods=['POST'])
@login_required
def student_update_progress():
    if session.get('is_teacher'):
        return redirect(url_for('teacher_dashboard'))
    student_id = session['user_id']
    exercise_id = request.form['exercise_id']
    status = request.form['status']
    conn = get_db_connection()
    conn.execute('UPDATE progress SET status = ? WHERE student_id = ? AND exercise_id = ?',
                (status, student_id, exercise_id))
    conn.commit()
    conn.close()
    flash('Status updated!', 'success')
    return redirect(url_for('student_dashboard'))

# ====================================================================
# TEACHER DASHBOARD
# ====================================================================

@app.route('/teacher/dashboard')
@teacher_required
def teacher_dashboard():
    subject = session.get('subject')
    user_id = session['user_id']
    unread_count = get_unread_count(user_id)
    conn = get_db_connection()
    exercises = conn.execute('SELECT id, chapter, exercise_name FROM exercises WHERE subject = ? ORDER BY chapter, id', (subject,)).fetchall()
    students = conn.execute('SELECT id, name, roll_no, batch, branch FROM students WHERE is_teacher = 0 ORDER BY name').fetchall()
    conn.close()
    exercises = [dict(e) for e in exercises]
    students = [dict(s) for s in students]
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>{{ subject }} - Teacher</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        *{margin:0;padding:0;box-sizing:border-box;}
        body{font-family:Segoe UI,sans-serif;background:#f5f5f5;padding:20px;}
        .container{max-width:1400px;margin:0 auto;}
        .header{background:white;border-radius:15px;padding:20px 25px;margin-bottom:25px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;box-shadow:0 2px 10px rgba(0,0,0,0.08);}
        .header h1{font-size:22px;color:#333;}
        .btn-group{display:flex;gap:10px;flex-wrap:wrap;}
        .btn-message{background:#667eea;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;text-decoration:none;display:inline-block;}
        .btn-sync{background:#48bb78;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;text-decoration:none;display:inline-block;}
        .btn-logout{background:#dc3545;color:white;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;}
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
        table{width:100%;border-collapse:collapse;font-size:13px;}
        th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #e0e0e0;}
        th{background:#f8f9fa;font-weight:600;}
        .status-done{background:#48bb78;color:white;padding:2px 8px;border-radius:4px;font-size:11px;}
        .status-pending{background:#f56565;color:white;padding:2px 8px;border-radius:4px;font-size:11px;}
        .btn-sm{padding:3px 6px;border:none;border-radius:4px;cursor:pointer;font-size:10px;}
        .btn-done{background:#48bb78;color:white;}
        .btn-pending{background:#f56565;color:white;}
        .alert{padding:12px 15px;border-radius:10px;margin-bottom:20px;}
        .alert-success{background:#d4edda;color:#155724;}
        @media (max-width:768px){.header{flex-direction:column;align-items:flex-start;gap:15px;}}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="header">
            <div><h1>📖 {{ subject }}</h1><p>Welcome, {{ session.user_name }}!</p></div>
            <div class="btn-group">
                <a href="/teacher/sync" class="btn-sync">🔄 Sync</a>
                <a href="/teacher/messages" class="btn-message">💬 Messages <span class="badge">{{ unread_count }}</span></a>
                <button class="btn-logout" onclick="location.href='/logout'">🚪 Logout</button>
            </div>
        </div>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}<div class="alert alert-success">{{ messages[0][1] }}</div>{% endif %}
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
            <div class="section-title">👨‍🎓 Student Progress</div>
            <table>
                <thead><tr><th>Student</th><th>Roll No</th><th>Exercise</th><th>Status</th></tr></thead>
                <tbody>
                    {% for s in students %}
                        {% for ex in exercises %}
                        {% set status = 'pending' %}
                        <tr>
                            <td>{{ s.name }}</td>
                            <td>{{ s.roll_no or '-' }}</td>
                            <td>{{ ex.exercise_name }}</td>
                            <td>
                                <form method="POST" action="/teacher/update_status" style="display:inline;">
                                    <input type="hidden" name="student_id" value="{{ s.id }}">
                                    <input type="hidden" name="exercise_id" value="{{ ex.id }}">
                                    <button type="submit" name="status" value="done" class="btn-sm btn-done">✓ Done</button>
                                    <button type="submit" name="status" value="pending" class="btn-sm btn-pending">○ Pending</button>
                                </form>
                            </td>
                        </tr>
                        {% endfor %}
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    </body>
    </html>
    ''', subject=subject, students=students, exercises=exercises, unread_count=unread_count)

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

@app.route('/teacher/sync')
@teacher_required
def sync_data():
    try:
        sync_students_from_google_sheets()
        flash('✅ Data synced from Google Sheets!', 'success')
    except Exception as e:
        flash(f'⚠️ Error syncing: {str(e)}', 'error')
    return redirect(url_for('teacher_dashboard'))

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
        .back-btn{background:#667eea;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;}
        .logout-btn{background:#dc3545;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;}
        .conversation{background:#f8f9fa;border-radius:10px;margin-bottom:15px;overflow:hidden;border:1px solid #e0e0e0;}
        .conversation-header{background:#667eea;color:white;padding:12px 20px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;}
        .conversation-header:hover{background:#5a67d8;}
        .conversation-messages{padding:20px;max-height:400px;overflow-y:auto;display:none;}
        .conversation-messages.active{display:block;}
        .message{border-left:4px solid #667eea;padding:12px;margin-bottom:10px;background:white;border-radius:4px;}
        .message.sent{border-left-color:#48bb78;}
        .message.received{border-left-color:#667eea;}
        .message-header{display:flex;justify-content:space-between;font-size:12px;color:#666;}
        .message-body{font-size:14px;color:#333;}
        .message-reply{background:#f8f9fa;padding:15px;border-radius:8px;margin-top:10px;}
        .message-reply textarea{width:100%;padding:10px;border:1px solid #ddd;border-radius:5px;height:60px;}
        .message-reply .btn-reply{background:#48bb78;color:white;padding:8px 20px;border:none;border-radius:4px;cursor:pointer;margin-top:10px;}
        .unread-badge{background:#e53e3e;color:white;padding:2px 8px;border-radius:10px;font-size:12px;}
        .send-form{background:#f8f9fa;padding:20px;border-radius:10px;margin-bottom:30px;}
        .form-group{margin-bottom:15px;}
        label{display:block;margin-bottom:5px;font-weight:500;}
        input[type=text],select,textarea{width:100%;padding:10px;border:1px solid #ddd;border-radius:5px;}
        textarea{height:80px;}
        .btn-send{background:#48bb78;color:white;padding:10px 20px;border:none;border-radius:5px;cursor:pointer;}
        .alert{padding:12px 15px;border-radius:10px;margin-bottom:20px;}
        .alert-success{background:#d4edda;color:#155724;}
        @media (max-width:768px){.header{flex-direction:column;align-items:flex-start;gap:15px;}}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="header"><div><h1>💬 Messages</h1></div><div><a href="/teacher/dashboard" class="back-btn">← Back</a><a href="/logout" class="logout-btn">Logout</a></div></div>
        {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}<div class="alert alert-success">{{ messages[0][1] }}</div>{% endif %}{% endwith %}
        <div class="send-form"><h3>📤 New Message</h3>
        <form method="POST" action="/teacher/send_message">
            <div class="form-group"><label>Student</label><select name="to_id" required><option value="">Select Student</option>{% for s in students %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select></div>
            <div class="form-group"><label>Subject</label><input type="text" name="subject" required></div>
            <div class="form-group"><label>Message</label><textarea name="message" required></textarea></div>
            <button type="submit" class="btn-send">📤 Send</button>
        </form></div>
        <h3>📥 Conversations</h3>
        {% if conversations|length == 0 %}<p style="color:#666;margin-top:15px;">No conversations yet.</p>{% else %}
            {% for other_id, conv in conversations.items() %}
            <div class="conversation">
                <div class="conversation-header" onclick="toggleConversation(this)"><span><strong>{{ conv.name }}</strong>{% if conv.unread_count > 0 %}<span class="unread-badge">{{ conv.unread_count }} new</span>{% endif %}</span><span>{{ conv.messages|length }} messages</span></div>
                <div class="conversation-messages">
                    {% for msg in conv.messages|reverse %}
                    <div class="message {% if msg.from_id == session.user_id %}sent{% else %}received{% endif %}">
                        <div class="message-header"><span><strong>{{ msg.from_name }}</strong> → {{ msg.to_name }}</span><span>{{ msg.created_at[:16] }}</span></div>
                        <div class="message-body"><strong>{{ msg.subject }}</strong><br>{{ msg.message }}</div>
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
    <script>function toggleConversation(header){const messages=header.nextElementSibling;messages.classList.toggle('active');}</script>
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
        .back-btn{background:#667eea;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;}
        .logout-btn{background:#dc3545;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;}
        .conversation{background:#f8f9fa;border-radius:10px;margin-bottom:15px;overflow:hidden;border:1px solid #e0e0e0;}
        .conversation-header{background:#667eea;color:white;padding:12px 20px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;}
        .conversation-header:hover{background:#5a67d8;}
        .conversation-messages{padding:20px;max-height:400px;overflow-y:auto;display:none;}
        .conversation-messages.active{display:block;}
        .message{border-left:4px solid #667eea;padding:12px;margin-bottom:10px;background:white;border-radius:4px;}
        .message.sent{border-left-color:#48bb78;}
        .message.received{border-left-color:#667eea;}
        .message-header{display:flex;justify-content:space-between;font-size:12px;color:#666;}
        .message-body{font-size:14px;color:#333;}
        .message-reply{background:#f8f9fa;padding:15px;border-radius:8px;margin-top:10px;}
        .message-reply textarea{width:100%;padding:10px;border:1px solid #ddd;border-radius:5px;height:60px;}
        .message-reply .btn-reply{background:#48bb78;color:white;padding:8px 20px;border:none;border-radius:4px;cursor:pointer;margin-top:10px;}
        .unread-badge{background:#e53e3e;color:white;padding:2px 8px;border-radius:10px;font-size:12px;}
        .send-form{background:#f8f9fa;padding:20px;border-radius:10px;margin-bottom:30px;}
        .form-group{margin-bottom:15px;}
        label{display:block;margin-bottom:5px;font-weight:500;}
        input[type=text],select,textarea{width:100%;padding:10px;border:1px solid #ddd;border-radius:5px;}
        textarea{height:80px;}
        .btn-send{background:#48bb78;color:white;padding:10px 20px;border:none;border-radius:5px;cursor:pointer;}
        .alert{padding:12px 15px;border-radius:10px;margin-bottom:20px;}
        .alert-success{background:#d4edda;color:#155724;}
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
        {% if conversations|length == 0 %}<p style="color:#666;margin-top:15px;">No conversations yet.</p>{% else %}
            {% for other_id, conv in conversations.items() %}
            <div class="conversation">
                <div class="conversation-header" onclick="toggleConversation(this)"><span><strong>{{ conv.name }}</strong>{% if conv.unread_count > 0 %}<span class="unread-badge">{{ conv.unread_count }} new</span>{% endif %}</span><span>{{ conv.messages|length }} messages</span></div>
                <div class="conversation-messages">
                    {% for msg in conv.messages|reverse %}
                    <div class="message {% if msg.from_id == session.user_id %}sent{% else %}received{% endif %}">
                        <div class="message-header"><span><strong>{{ msg.from_name }}</strong> → {{ msg.to_name }}</span><span>{{ msg.created_at[:16] }}</span></div>
                        <div class="message-body"><strong>{{ msg.subject }}</strong><br>{{ msg.message }}</div>
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
    <script>function toggleConversation(header){const messages=header.nextElementSibling;messages.classList.toggle('active');}</script>
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

# ====================================================================
# HEALTH CHECK
# ====================================================================

@app.route('/health')
def health_check():
    try:
        conn = get_db_connection()
        count = conn.execute('SELECT COUNT(*) FROM students').fetchone()[0]
        conn.close()
        return jsonify({'status': 'healthy', 'students': count})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

# ====================================================================
# RUN THE APP
# ====================================================================

if __name__ == '__main__':
    init_db()
    print("=" * 60)
    print("🎓 Bakliwal Tutorials Portal Running!")
    print("👤 Test Students: KETKI KULKARNI / Varsha")
    print("👨‍🏫 Teachers: physics_teacher / physics_teacher")
    print("=" * 60)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
