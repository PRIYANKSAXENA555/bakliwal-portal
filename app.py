# ====================================================================
# BAKLIWAL TUTORIALS - COMPLETE STUDENT PORTAL
# FIXED: Database persistence and student loading
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

# Database path - use a persistent location
DB_PATH = '/tmp/students.db'

# Also try to use current directory as fallback
if not os.path.exists('/tmp'):
    DB_PATH = 'students.db'

SHEET_NAME = os.environ.get('SHEET_NAME', 'Master Sheet')
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

print("=" * 60)
print("🚀 BAKLIWAL TUTORIALS PORTAL")
print(f"📊 Database: {DB_PATH}")
print(f"📝 Sheet: {SHEET_NAME}")
print("=" * 60)

# Try Google Sheets
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    HAS_GSHEETS = True
    print("✅ Google Sheets loaded")
except ImportError:
    HAS_GSHEETS = False
    print("❌ Google Sheets not available")

# ====================================================================
# DATABASE
# ====================================================================

def get_db_connection():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"❌ DB error: {e}")
        return None

def init_db():
    print("🔧 Initializing database...")
    try:
        conn = get_db_connection()
        if not conn:
            return False
        cursor = conn.cursor()

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

        # Check if teachers exist
        cursor.execute('SELECT COUNT(*) as count FROM students WHERE is_teacher = 1')
        if cursor.fetchone()[0] == 0:
            print("📝 Adding teachers...")
            teachers = [
                ('Physics Teacher', 'physics_teacher', 'physics@school.com', 'Physics'),
                ('Chemistry Teacher', 'chemistry_teacher', 'chemistry@school.com', 'Chemistry'),
                ('Mathematics Teacher', 'maths_teacher', 'maths@school.com', 'Mathematics')
            ]
            for name, username, email, subject in teachers:
                password_hash = generate_password_hash(username)
                cursor.execute('''
                    INSERT INTO students (name, roll_no, mother_name, email, password_hash, is_teacher, subject)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                ''', (name, username, 'teacher', email, password_hash, subject))

        # Add exercises
        cursor.execute('SELECT COUNT(*) as count FROM exercises')
        if cursor.fetchone()[0] == 0:
            print("📝 Adding exercises...")
            exercises = [
                ('Physics', 'Chapter 1: Motion', 'Ex 1.1 - Speed'),
                ('Physics', 'Chapter 1: Motion', 'Ex 1.2 - Acceleration'),
                ('Physics', 'Chapter 2: Force', 'Ex 2.1 - Newton\'s Laws'),
                ('Chemistry', 'Chapter 1: Atoms', 'Ex 1.1 - Atomic Structure'),
                ('Chemistry', 'Chapter 1: Atoms', 'Ex 1.2 - Periodic Table'),
                ('Chemistry', 'Chapter 2: Reactions', 'Ex 2.1 - Chemical Equations'),
                ('Mathematics', 'Chapter 1: Algebra', 'Ex 1.1 - Linear Equations'),
                ('Mathematics', 'Chapter 1: Algebra', 'Ex 1.2 - Quadratic Equations'),
                ('Mathematics', 'Chapter 2: Calculus', 'Ex 2.1 - Derivatives'),
            ]
            for subject, chapter, name in exercises:
                cursor.execute('INSERT OR IGNORE INTO exercises (subject, chapter, exercise_name) VALUES (?, ?, ?)',
                              (subject, chapter, name))

        conn.commit()
        conn.close()
        print("✅ Database initialized!")
        
        # Verify database has students
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as count FROM students WHERE is_teacher = 0')
            count = cursor.fetchone()[0]
            conn.close()
            print(f"📊 Database has {count} students")
        return True
    except Exception as e:
        print(f"❌ DB init error: {e}")
        import traceback
        traceback.print_exc()
        return False

# ====================================================================
# GOOGLE SHEETS SYNC
# ====================================================================

def get_gs_client():
    if not HAS_GSHEETS or not GOOGLE_CREDENTIALS_JSON:
        return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"⚠️ GS error: {e}")
        return None

def sync_all():
    """Full sync from Google Sheets"""
    print("🔄 Starting sync...")
    client = get_gs_client()
    if not client:
        return 0, "No Google Sheets client"
    
    try:
        spreadsheet = client.open(SHEET_NAME)
        sheet20 = spreadsheet.worksheet('Sheet20')
        data = sheet20.get_all_values()
        
        if len(data) < 2:
            return 0, "Sheet20 is empty"
        
        # Get mother names
        mother_dict = {}
        try:
            mother_sheet = spreadsheet.worksheet('Mother Name')
            mother_data = mother_sheet.get_all_values()
            for row in mother_data[1:]:
                if len(row) >= 2 and row[0].strip():
                    mother_dict[row[0].strip().upper()] = row[1].strip()
            print(f"📋 Loaded {len(mother_dict)} mother names")
        except Exception as e:
            print(f"⚠️ Mother Name error: {e}")
        
        headers = data[0]
        name_idx = headers.index('NAME') if 'NAME' in headers else -1
        roll_idx = headers.index('ROLL NO') if 'ROLL NO' in headers else -1
        batch_idx = headers.index('BATCH') if 'BATCH' in headers else -1
        branch_idx = headers.index('BRANCH') if 'BRANCH' in headers else -1
        
        if roll_idx == -1 or name_idx == -1:
            return 0, "NAME or ROLL NO not found"
        
        conn = get_db_connection()
        if not conn:
            return 0, "DB connection failed"
        cursor = conn.cursor()
        
        # Clear non-teacher students
        cursor.execute('DELETE FROM students WHERE is_teacher = 0')
        cursor.execute('DELETE FROM progress')
        
        cursor.execute('SELECT id FROM exercises')
        exercise_ids = [row[0] for row in cursor.fetchall()]
        
        student_count = 0
        for row in data[1:]:
            if len(row) <= max(roll_idx, name_idx):
                continue
            roll_no = str(row[roll_idx]).strip()
            if not roll_no:
                continue
            
            name = str(row[name_idx]).strip() if name_idx != -1 else 'Student'
            batch = str(row[batch_idx]).strip() if batch_idx != -1 else ''
            branch = str(row[branch_idx]).strip() if branch_idx != -1 else ''
            mother_name = mother_dict.get(name.upper(), 'password')
            password_hash = generate_password_hash(mother_name.lower())
            
            cursor.execute('''
                INSERT INTO students (name, roll_no, mother_name, batch, branch, password_hash, is_teacher)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (name, roll_no, mother_name, batch, branch, password_hash))
            
            student_id = cursor.lastrowid
            student_count += 1
            for ex_id in exercise_ids:
                cursor.execute('''
                    INSERT OR IGNORE INTO progress (student_id, exercise_id, status, discussed)
                    VALUES (?, ?, 'pending', 'no')
                ''', (student_id, ex_id))
        
        conn.commit()
        conn.close()
        print(f"✅ Added {student_count} students")
        return student_count, None
        
    except Exception as e:
        print(f"❌ Sync error: {e}")
        import traceback
        traceback.print_exc()
        return 0, str(e)

# ====================================================================
# INITIALIZE ON STARTUP
# ====================================================================

print("🔧 Initializing...")
init_db()

# Try to sync if credentials are available
if GOOGLE_CREDENTIALS_JSON:
    print("📡 Syncing from Google Sheets...")
    count, error = sync_all()
    if count > 0:
        print(f"✅ Synced {count} students")
    else:
        print(f"⚠️ Sync failed: {error}")
else:
    print("⚠️ No Google Sheets credentials, using existing data")

# Verify database
conn = get_db_connection()
if conn:
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as count FROM students WHERE is_teacher = 0')
    student_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) as count FROM test_results')
    test_count = cursor.fetchone()[0]
    conn.close()
    print(f"📊 Database: {student_count} students, {test_count} test results")
print("=" * 60)

# ====================================================================
# HELPERS
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

def get_all_students():
    """Get all student names for dropdown - WITH DEBUGGING"""
    try:
        conn = get_db_connection()
        if not conn:
            print("❌ get_all_students: No DB connection")
            return []
        
        students = conn.execute('SELECT id, name, roll_no FROM students WHERE is_teacher = 0 ORDER BY name').fetchall()
        conn.close()
        
        print(f"📋 get_all_students: Found {len(students)} students")
        return [dict(s) for s in students]
    except Exception as e:
        print(f"❌ get_all_students error: {e}")
        import traceback
        traceback.print_exc()
        return []

def get_unread_count(user_id):
    try:
        conn = get_db_connection()
        if not conn:
            return 0
        count = conn.execute('SELECT COUNT(*) as count FROM messages WHERE to_id = ? AND is_read = 0', (user_id,)).fetchone()
        conn.close()
        return count[0] if count else 0
    except:
        return 0

def send_message(from_id, to_id, subject, message, parent_id=None):
    try:
        conn = get_db_connection()
        if not conn:
            return
        conn.execute('INSERT INTO messages (from_id, to_id, subject, message, parent_id) VALUES (?, ?, ?, ?, ?)',
                    (from_id, to_id, subject, message, parent_id))
        conn.commit()
        conn.close()
    except:
        pass

def mark_message_read(message_id):
    try:
        conn = get_db_connection()
        if not conn:
            return
        conn.execute('UPDATE messages SET is_read = 1 WHERE id = ?', (message_id,))
        conn.commit()
        conn.close()
    except:
        pass

def get_all_messages(user_id):
    try:
        conn = get_db_connection()
        if not conn:
            return []
        messages = conn.execute('''
            SELECT m.*, s.name as from_name, s2.name as to_name
            FROM messages m
            JOIN students s ON m.from_id = s.id
            JOIN students s2 ON m.to_id = s2.id
            WHERE m.from_id = ? OR m.to_id = ?
            ORDER BY m.created_at DESC
        ''', (user_id, user_id)).fetchall()
        conn.close()
        return [dict(m) for m in messages]
    except:
        return []

def get_students():
    try:
        conn = get_db_connection()
        if not conn:
            return []
        students = conn.execute('SELECT id, name, roll_no FROM students WHERE is_teacher = 0 ORDER BY name').fetchall()
        conn.close()
        return [dict(s) for s in students]
    except:
        return []

def get_progress_for_student(student_id):
    try:
        conn = get_db_connection()
        if not conn:
            return []
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
    except:
        return []

def get_test_results(student_id):
    try:
        conn = get_db_connection()
        if not conn:
            return []
        results = conn.execute('''
            SELECT * FROM test_results
            WHERE student_id = ?
            ORDER BY test_date DESC
        ''', (student_id,)).fetchall()
        conn.close()
        return [dict(r) for r in results]
    except:
        return []

def get_student_stats(student_id):
    try:
        conn = get_db_connection()
        if not conn:
            return {'total_tests': 0, 'avg_percentage': 0, 'best_rank': None, 'highest_score': 0}
        results = conn.execute('''
            SELECT COUNT(*) as total_tests, AVG(percentage) as avg_percentage,
                   MIN(CAST(rank AS INTEGER)) as best_rank, MAX(total_marks) as highest_score
            FROM test_results
            WHERE student_id = ? AND rank != '-'
        ''', (student_id,)).fetchone()
        conn.close()
        if results:
            return {
                'total_tests': results[0] or 0,
                'avg_percentage': results[1] or 0,
                'best_rank': results[2],
                'highest_score': results[3] or 0
            }
        return {'total_tests': 0, 'avg_percentage': 0, 'best_rank': None, 'highest_score': 0}
    except:
        return {'total_tests': 0, 'avg_percentage': 0, 'best_rank': None, 'highest_score': 0}

# ====================================================================
# ROUTES
# ====================================================================

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    students = get_all_students()
    
    if request.method == 'POST':
        student_id = request.form.get('student_id')
        password = request.form.get('password', '').strip()
        username = request.form.get('username', '').strip()
        
        try:
            conn = get_db_connection()
            if not conn:
                flash('Database error', 'error')
                return render_template_string(LOGIN_TEMPLATE, students=students)
            
            if student_id:
                user = conn.execute('SELECT * FROM students WHERE id = ?', (student_id,)).fetchone()
            elif username:
                user = conn.execute('SELECT * FROM students WHERE name = ? OR roll_no = ? OR email = ?', 
                                   (username, username, username)).fetchone()
            else:
                flash('Please select a student', 'error')
                return render_template_string(LOGIN_TEMPLATE, students=students)
            
            conn.close()
            
            if user:
                user_dict = dict(user)
                if check_password_hash(user_dict['password_hash'], password.lower()):
                    session['user_id'] = user_dict['id']
                    session['user_name'] = user_dict['name']
                    session['roll_no'] = user_dict['roll_no']
                    session['is_teacher'] = user_dict.get('is_teacher', 0)
                    session['subject'] = user_dict.get('subject') if user_dict.get('is_teacher') else None
                    flash(f'Welcome {user_dict["name"]}!', 'success')
                    
                    if user_dict.get('is_teacher'):
                        return redirect(url_for('teacher_dashboard'))
                    else:
                        return redirect(url_for('student_dashboard'))
                else:
                    flash('Invalid password. Use your Mother\'s Name.', 'error')
            else:
                flash('User not found.', 'error')
                
        except Exception as e:
            print(f"❌ Login error: {e}")
            flash('Login error. Please try again.', 'error')
    
    return render_template_string(LOGIN_TEMPLATE, students=students)

LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Bakliwal Tutorials</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        *{margin:0;padding:0;box-sizing:border-box;}
        body{font-family:Segoe UI,sans-serif;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;display:flex;justify-content:center;align-items:center;padding:20px;}
        .container{background:white;border-radius:20px;padding:40px;width:100%;max-width:500px;box-shadow:0 20px 60px rgba(0,0,0,0.3);}
        .logo{text-align:center;margin-bottom:30px;}
        .logo h1{color:#333;font-size:24px;}
        .logo p{color:#666;font-size:14px;}
        .form-group{margin-bottom:20px;}
        label{display:block;margin-bottom:8px;font-weight:500;color:#333;font-size:14px;}
        select{width:100%;padding:12px 15px;border:2px solid #e0e0e0;border-radius:10px;font-size:16px;background:white;}
        select:focus{outline:none;border-color:#667eea;}
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
        .divider{text-align:center;padding:15px 0;color:#999;font-size:13px;}
        .teacher-section{border-top:2px solid #e0e0e0;padding-top:20px;margin-top:10px;}
        .count-badge{font-size:12px;color:#999;margin-left:10px;}
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
        <div class="form-group">
            <label>👨‍🎓 Select Your Name <span class="count-badge">({{ students|length }} students)</span></label>
            <select name="student_id" required>
                <option value="">-- Select Student --</option>
                {% for s in students %}
                <option value="{{ s.id }}">{{ s.name }} ({{ s.roll_no }})</option>
                {% endfor %}
            </select>
        </div>
        <div class="form-group">
            <label>🔑 Password (Mother's Name)</label>
            <input type="password" name="password" placeholder="Enter mother's name" required>
        </div>
        <button type="submit">🔓 Login</button>
    </form>
    
    <div class="divider"><span>OR</span></div>
    
    <div class="teacher-section">
        <p style="font-size:13px;color:#666;text-align:center;margin-bottom:10px;">
            <strong>👨‍🏫 Teachers Login Here:</strong>
        </p>
        <form method="POST">
            <div class="form-group">
                <label>📝 Username</label>
                <input type="text" name="username" placeholder="physics_teacher">
            </div>
            <div class="form-group">
                <label>🔑 Password</label>
                <input type="password" name="password" placeholder="Same as username">
            </div>
            <button type="submit">🔓 Teacher Login</button>
        </form>
        <div style="margin-top:10px;font-size:12px;color:#666;text-align:center;">
            <span class="badge badge-physics">Physics</span> physics_teacher<br>
            <span class="badge badge-chemistry">Chemistry</span> chemistry_teacher<br>
            <span class="badge badge-maths">Mathematics</span> maths_teacher
        </div>
    </div>
</div>
</body>
</html>
'''

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'success')
    return redirect(url_for('login'))

# ====================================================================
# DEBUG ROUTES
# ====================================================================

@app.route('/debug/check_student/<int:student_id>')
def debug_check_student(student_id):
    try:
        conn = get_db_connection()
        student = conn.execute('SELECT id, name, roll_no, mother_name FROM students WHERE id = ?', (student_id,)).fetchone()
        conn.close()
        
        if student:
            return f"""
            <html>
            <head><title>Student Debug</title></head>
            <body style="font-family:Segoe UI,sans-serif;padding:30px;">
                <h2>🔍 Student Debug Info</h2>
                <hr>
                <p><strong>ID:</strong> {student['id']}</p>
                <p><strong>Name:</strong> {student['name']}</p>
                <p><strong>Roll No:</strong> {student['roll_no']}</p>
                <p><strong>Mother Name (stored):</strong> <code>'{student['mother_name']}'</code></p>
                <p><strong>Try logging in with:</strong> {student['mother_name']} (case insensitive)</p>
                <hr>
                <p><a href="/login">🔙 Go back to Login</a></p>
                <p><a href="/admin/reset_all_passwords">🔄 Reset ALL Student Passwords</a></p>
            </body>
            </html>
            """
        else:
            return "Student not found"
    except Exception as e:
        return f"Error: {e}"

@app.route('/debug/list_students')
def debug_list_students():
    try:
        conn = get_db_connection()
        students = conn.execute('SELECT id, name, roll_no, mother_name FROM students WHERE is_teacher = 0 ORDER BY name LIMIT 20').fetchall()
        conn.close()
        
        html = """
        <html>
        <head><title>Students List</title></head>
        <body style="font-family:Segoe UI,sans-serif;padding:30px;">
            <h2>📋 Students List (First 20)</h2>
            <hr>
            <table border="1" cellpadding="8" style="border-collapse:collapse;">
                <tr><th>ID</th><th>Name</th><th>Roll No</th><th>Mother Name</th></tr>
        """
        for s in students:
            html += f"<tr><td>{s['id']}</td><td>{s['name']}</td><td>{s['roll_no']}</td><td><code>{s['mother_name']}</code></td></tr>"
        
        html += """
            </table>
            <hr>
            <p><a href="/login">🔙 Go back to Login</a></p>
            <p><a href="/admin/reset_all_passwords">🔄 Reset ALL Student Passwords</a></p>
        </body>
        </html>
        """
        return html
    except Exception as e:
        return f"Error: {e}"

@app.route('/debug/db_status')
def debug_db_status():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM students WHERE is_teacher = 0')
        student_count = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) as count FROM students WHERE is_teacher = 1')
        teacher_count = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) as count FROM test_results')
        test_count = cursor.fetchone()[0]
        conn.close()
        return f"""
        <html>
        <head><title>Database Status</title></head>
        <body style="font-family:Segoe UI,sans-serif;padding:30px;">
            <h2>📊 Database Status</h2>
            <hr>
            <p><strong>Students:</strong> {student_count}</p>
            <p><strong>Teachers:</strong> {teacher_count}</p>
            <p><strong>Test Results:</strong> {test_count}</p>
            <hr>
            <p><a href="/login">🔙 Go back to Login</a></p>
            <p><a href="/debug/list_students">📋 View Students</a></p>
            <p><a href="/admin/reset_all_passwords">🔄 Reset Passwords</a></p>
        </body>
        </html>
        """
    except Exception as e:
        return f"Error: {e}"

# ====================================================================
# RESET PASSWORDS
# ====================================================================

@app.route('/admin/reset_all_passwords')
def reset_all_passwords():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT id, mother_name FROM students WHERE is_teacher = 0')
        students = cursor.fetchall()
        
        count = 0
        for s in students:
            clean_mother = s['mother_name'].strip().lower()
            password_hash = generate_password_hash(clean_mother)
            cursor.execute('UPDATE students SET password_hash = ? WHERE id = ?', 
                          (password_hash, s['id']))
            count += 1
        
        conn.commit()
        conn.close()
        
        return f"""
        <html>
        <head><title>Password Reset</title></head>
        <body style="font-family:Segoe UI,sans-serif;padding:30px;">
            <h2>✅ Password Reset Complete</h2>
            <hr>
            <p><strong>Success:</strong> Reset passwords for {count} students</p>
            <p><strong>Students can now login with their Mother's Name (case insensitive).</strong></p>
            <hr>
            <p><a href="/login">🔙 Go to Login</a></p>
            <p><a href="/debug/db_status">📊 Check Database</a></p>
        </body>
        </html>
        """
    except Exception as e:
        return f"❌ Error: {e}"

# ====================================================================
# TEACHER ROUTES
# ====================================================================

@app.route('/teacher/dashboard')
@teacher_required
def teacher_dashboard():
    subject = session.get('subject')
    user_id = session['user_id']
    unread_count = get_unread_count(user_id)
    
    try:
        conn = get_db_connection()
        if not conn:
            flash('Database error', 'error')
            return redirect(url_for('login'))
        exercises = conn.execute('SELECT id, chapter, exercise_name FROM exercises WHERE subject = ? ORDER BY chapter, id', (subject,)).fetchall()
        students = conn.execute('SELECT id, name, roll_no FROM students WHERE is_teacher = 0 ORDER BY name').fetchall()
        conn.close()
        exercises = [dict(e) for e in exercises]
        students = [dict(s) for s in students]
    except Exception as e:
        flash(f'Error: {e}', 'error')
        exercises = []
        students = []
    
    return render_template_string(TEACHER_DASHBOARD_TEMPLATE, 
        subject=subject, students=students, exercises=exercises, unread_count=unread_count)

TEACHER_DASHBOARD_TEMPLATE = '''
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
    .btn-add{background:#48bb78;color:white;padding:10px 25px;border:none;border-radius:8px;cursor:pointer;font-weight:600;}
    table{width:100%;border-collapse:collapse;font-size:13px;}
    th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #e0e0e0;}
    th{background:#f8f9fa;font-weight:600;}
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
            <a href="/teacher/sync" class="btn-sync">🔄 Sync Data</a>
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
'''

@app.route('/teacher/add_exercise', methods=['POST'])
@teacher_required
def add_exercise_route():
    chapter = request.form['chapter']
    exercise_name = request.form['exercise_name']
    subject = session.get('subject')
    
    try:
        conn = get_db_connection()
        if not conn:
            flash('Database error', 'error')
            return redirect(url_for('teacher_dashboard'))
        
        conn.execute('INSERT INTO exercises (subject, chapter, exercise_name, created_by) VALUES (?, ?, ?, ?)',
                    (subject, chapter, exercise_name, session['user_id']))
        exercise_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        students = conn.execute('SELECT id FROM students WHERE is_teacher = 0').fetchall()
        for student in students:
            conn.execute('INSERT OR IGNORE INTO progress (student_id, exercise_id, status, discussed) VALUES (?, ?, "pending", "no")',
                        (student[0], exercise_id))
        conn.commit()
        conn.close()
        flash('Exercise added!', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    
    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/update_status', methods=['POST'])
@teacher_required
def teacher_update_status():
    try:
        conn = get_db_connection()
        if conn:
            conn.execute('UPDATE progress SET status = ? WHERE student_id = ? AND exercise_id = ?',
                        (request.form['status'], request.form['student_id'], request.form['exercise_id']))
            conn.commit()
            conn.close()
            flash('Status updated!', 'success')
        else:
            flash('Database error', 'error')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    
    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/sync')
@teacher_required
def sync_data():
    try:
        count, error = sync_all()
        if count > 0:
            flash(f'✅ Synced {count} students from Google Sheets!', 'success')
        else:
            flash(f'⚠️ Sync failed: {error}', 'error')
    except Exception as e:
        flash(f'⚠️ Error: {str(e)}', 'error')
    return redirect(url_for('teacher_dashboard'))

# ====================================================================
# STUDENT ROUTES
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
    
    return render_template_string(STUDENT_DASHBOARD_TEMPLATE, 
        subjects=subjects, total=total, done=done, test_results=test_results, 
        stats=stats, unread_count=unread_count)

STUDENT_DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head><title>Student Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
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
    .chart-container{height:280px;margin-top:10px;}
    .progress-bar{background:#e0e0e0;border-radius:10px;overflow:hidden;height:6px;width:100px;}
    .progress-fill{background:linear-gradient(90deg,#667eea,#764ba2);height:100%;border-radius:10px;}
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
    
    {% if test_results|length > 0 %}
    <div class="section">
        <div class="section-title">📊 Performance Trend</div>
        <div class="chart-container"><canvas id="perfChart"></canvas></div>
    </div>
    {% endif %}
    
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
                    <td><span class="rank-badge {{ rank_class }}">#{{ r.rank if r.rank != '-' else '-' }}</span></td>
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

{% if test_results|length > 0 %}
<script>
const ctx = document.getElementById('perfChart').getContext('2d');
const tests = {{ test_results|map(attribute='test_name')|list|tojson }};
const percents = {{ test_results|map(attribute='percentage')|list|tojson }};
const totals = {{ test_results|map(attribute='total_marks')|list|tojson }};
const maxes = {{ test_results|map(attribute='total_max')|list|tojson }};

new Chart(ctx, {
    type: 'bar',
    data: {
        labels: tests.reverse(),
        datasets: [{
            label: 'Percentage (%)',
            data: percents.reverse(),
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
                        return `Score: ${totals.reverse()[idx]}/${maxes.reverse()[idx]} (${percents.reverse()[idx].toFixed(1)}%)`;
                    }
                }
            }
        },
        scales: { y: { beginAtZero: true, max: 100 } }
    }
});
</script>
{% endif %}
</body>
</html>
'''

@app.route('/student/update_progress', methods=['POST'])
@login_required
def student_update_progress():
    if session.get('is_teacher'):
        return redirect(url_for('teacher_dashboard'))
    
    student_id = session['user_id']
    exercise_id = request.form['exercise_id']
    status = request.form['status']
    
    try:
        conn = get_db_connection()
        if conn:
            conn.execute('UPDATE progress SET status = ? WHERE student_id = ? AND exercise_id = ?',
                        (status, student_id, exercise_id))
            conn.commit()
            conn.close()
            flash('Status updated!', 'success')
        else:
            flash('Database error', 'error')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    
    return redirect(url_for('student_dashboard'))

# ====================================================================
# MESSAGING ROUTES
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
    return render_template_string(MESSAGES_TEMPLATE, conversations=conversations, students=students)

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
    
    return render_template_string(MESSAGES_TEMPLATE, conversations=conversations, teachers=teachers, is_student=True)

MESSAGES_TEMPLATE = '''
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
    <div class="header">
        <div><h1>💬 Messages</h1></div>
        <div>
            <a href="{% if is_student %}/student/dashboard{% else %}/teacher/dashboard{% endif %}" class="back-btn">← Back</a>
            <a href="/logout" class="logout-btn">Logout</a>
        </div>
    </div>
    {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}<div class="alert alert-success">{{ messages[0][1] }}</div>{% endif %}{% endwith %}
    
    <div class="send-form">
        <h3>📤 New Message</h3>
        <form method="POST" action="{% if is_student %}/student/send_message{% else %}/teacher/send_message{% endif %}">
            <div class="form-group">
                <label>Recipient</label>
                <select name="to_id" required>
                    <option value="">Select Recipient</option>
                    {% if is_student %}
                        {% for t in teachers %}
                        <option value="{{ t.id }}">{{ t.name }} ({{ t.subject }})</option>
                        {% endfor %}
                    {% else %}
                        {% for s in students %}
                        <option value="{{ s.id }}">{{ s.name }}</option>
                        {% endfor %}
                    {% endif %}
                </select>
            </div>
            <div class="form-group"><label>Subject</label><input type="text" name="subject" required></div>
            <div class="form-group"><label>Message</label><textarea name="message" required></textarea></div>
            <button type="submit" class="btn-send">📤 Send</button>
        </form>
    </div>
    
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
                    <form method="POST" action="{% if is_student %}/student/mark_read{% else %}/teacher/mark_read{% endif %}" style="margin-top:5px;">
                        <input type="hidden" name="message_id" value="{{ msg.id }}">
                        <button type="submit" style="padding:3px 10px;background:#667eea;color:white;border:none;border-radius:4px;cursor:pointer;font-size:11px;">✓ Mark Read</button>
                    </form>{% endif %}
                </div>
                {% endfor %}
                <div class="message-reply">
                    <form method="POST" action="{% if is_student %}/student/send_reply{% else %}/teacher/send_reply{% endif %}">
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
'''

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
# RUN
# ====================================================================

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("🎓 Bakliwal Tutorials Portal Ready!")
    print("📱 Access at: http://localhost:5000")
    print("=" * 60)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
