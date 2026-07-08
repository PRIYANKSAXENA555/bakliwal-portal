# ====================================================================
# BAKLIWAL TUTORIALS - COMPLETE STUDENT PORTAL
# WITH FULL GOOGLE SHEETS SYNC
# ====================================================================

import os
import json
import sqlite3
import tempfile
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

# ====================================================================
# CONFIGURATION
# ====================================================================

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-this')

DB_PATH = os.path.join(tempfile.gettempdir(), 'students.db')

SHEET_NAME = os.environ.get('SHEET_NAME', 'Master Sheet')
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

print(f"✅ Starting Bakliwal Portal")
print(f"📊 Database path: {DB_PATH}")
print(f"📝 Sheet name: {SHEET_NAME}")

# Try to import Google Sheets
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    HAS_GSHEETS = True
    print("✅ Google Sheets loaded")
except ImportError:
    HAS_GSHEETS = False
    print("⚠️ Google Sheets not available")

# ====================================================================
# DATABASE SETUP
# ====================================================================

def get_db_connection():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return None

def init_db():
    print("🔧 Initializing database...")
    try:
        conn = get_db_connection()
        if not conn:
            return False
            
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
        cursor.execute('SELECT COUNT(*) as count FROM students WHERE is_teacher = 1')
        if cursor.fetchone()[0] == 0:
            print("📝 Adding default teachers...")
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

        # Add sample exercises
        cursor.execute('SELECT COUNT(*) as count FROM exercises')
        if cursor.fetchone()[0] == 0:
            print("📝 Adding sample exercises...")
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

        conn.commit()
        conn.close()
        print("✅ Database initialized successfully!")
        return True
    except Exception as e:
        print(f"❌ Database init error: {e}")
        import traceback
        traceback.print_exc()
        return False

# ====================================================================
# GOOGLE SHEETS SYNC - COMPLETE
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

def sync_all_from_google_sheets():
    """Complete sync: Students + Mother Names + Test Results"""
    print("🔄 Starting full Google Sheets sync...")
    
    client = get_google_sheets_client()
    if not client:
        print("⚠️ Google Sheets client not available")
        return False

    try:
        spreadsheet = client.open(SHEET_NAME)
        
        # ============================================================
        # 1. SYNC MOTHER NAMES
        # ============================================================
        print("📋 Reading Mother Names...")
        mother_sheet = spreadsheet.worksheet('Mother Name')
        mother_data = mother_sheet.get_all_values()
        mother_dict = {}
        
        # Skip header row
        for row in mother_data[1:]:
            if len(row) >= 2:
                student_name = row[0].strip().upper()
                mother_name = row[1].strip()
                if student_name and mother_name:
                    mother_dict[student_name] = mother_name
        
        print(f"   ✅ Found {len(mother_dict)} mother names")

        # ============================================================
        # 2. SYNC STUDENTS FROM SHEET20
        # ============================================================
        print("📋 Reading Sheet20...")
        sheet20 = spreadsheet.worksheet('Sheet20')
        data = sheet20.get_all_values()
        
        if len(data) < 2:
            print("⚠️ No data in Sheet20")
            return False

        headers = data[0]
        name_idx = headers.index('NAME') if 'NAME' in headers else -1
        roll_idx = headers.index('ROLL NO') if 'ROLL NO' in headers else -1
        batch_idx = headers.index('BATCH') if 'BATCH' in headers else -1
        branch_idx = headers.index('BRANCH') if 'BRANCH' in headers else -1

        if roll_idx == -1:
            print("⚠️ ROLL NO column not found")
            return False

        conn = get_db_connection()
        if not conn:
            return False
        cursor = conn.cursor()

        # Clear existing students (keep teachers)
        cursor.execute('DELETE FROM students WHERE is_teacher = 0')
        
        # Also clear progress for students
        cursor.execute('DELETE FROM progress')
        
        student_count = 0
        exercise_count = 0

        # Get all exercises for assigning to students
        cursor.execute('SELECT id FROM exercises')
        exercises = cursor.fetchall()
        exercise_ids = [ex[0] for ex in exercises]

        for row in data[1:]:
            if len(row) <= max(roll_idx, name_idx):
                continue

            roll_no = str(row[roll_idx]).strip()
            if not roll_no:
                continue

            name = str(row[name_idx]).strip() if name_idx != -1 else 'Student'
            batch = str(row[batch_idx]).strip() if batch_idx != -1 else ''
            branch = str(row[branch_idx]).strip() if branch_idx != -1 else ''
            
            # Get mother name from dictionary (case-insensitive)
            mother_name = mother_dict.get(name.upper(), 'password')
            
            # Create password hash (lowercase for case-insensitive login)
            password_hash = generate_password_hash(mother_name.lower())

            cursor.execute('''
                INSERT INTO students (name, roll_no, mother_name, batch, branch, password_hash, is_teacher)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (name, roll_no, mother_name, batch, branch, password_hash))
            
            student_id = cursor.lastrowid
            student_count += 1
            
            # Assign exercises to this student
            for ex_id in exercise_ids:
                cursor.execute('''
                    INSERT OR IGNORE INTO progress (student_id, exercise_id, status, discussed)
                    VALUES (?, ?, 'pending', 'no')
                ''', (student_id, ex_id))
                exercise_count += 1

        conn.commit()
        conn.close()
        print(f"   ✅ Synced {student_count} students with {exercise_count} exercise assignments")

        # ============================================================
        # 3. SYNC TEST RESULTS
        # ============================================================
        print("📋 Syncing test results...")
        all_sheets = spreadsheet.worksheets()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        test_count = 0

        for sheet in all_sheets:
            sheet_name = sheet.title
            
            # Skip non-test sheets
            if sheet_name in ['Sheet20', 'Mother Name', 'Sheet1'] or sheet_name.startswith('Sheet'):
                continue

            print(f"   📄 Processing: {sheet_name}")
            data = sheet.get_all_values()
            if len(data) < 10:
                continue

            # Find header row
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

            # Determine test type
            is_brtest = sheet_name.upper().startswith('BRTEST')
            max_phy = 50 if is_brtest else 100
            max_chem = 50 if is_brtest else 100
            max_maths = 100
            max_total = max_phy + max_chem + max_maths

            # Get max marks from sheet
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
                if not roll_no:
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
        print(f"   ✅ Synced {test_count} test results")

        # ============================================================
        # 4. VERIFY
        # ============================================================
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM students WHERE is_teacher = 0')
        student_count = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) as count FROM test_results')
        test_count = cursor.fetchone()[0]
        conn.close()
        
        print(f"✅ Sync complete! {student_count} students, {test_count} test results")
        return True

    except Exception as e:
        print(f"❌ Sync error: {e}")
        import traceback
        traceback.print_exc()
        return False

# ====================================================================
# INITIALIZE DATABASE AND SYNC ON STARTUP
# ====================================================================

print("=" * 60)
print("🚀 Initializing application...")
init_db()
print("=" * 60)

# Try to sync from Google Sheets on startup
print("🔄 Attempting Google Sheets sync...")
sync_all_from_google_sheets()
print("=" * 60)

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

def get_all_student_names():
    """Get all student names for dropdown"""
    try:
        conn = get_db_connection()
        if not conn:
            return []
        students = conn.execute('SELECT id, name, roll_no FROM students WHERE is_teacher = 0 ORDER BY name').fetchall()
        conn.close()
        print(f"📋 Loaded {len(students)} students for dropdown")
        return [dict(s) for s in students]
    except Exception as e:
        print(f"❌ Error loading students: {e}")
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
    except Exception as e:
        print(f"⚠️ Send message error: {e}")

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
    except:
        return []

def get_students():
    try:
        conn = get_db_connection()
        if not conn:
            return []
        students = conn.execute('SELECT id, name, roll_no, batch, branch FROM students WHERE is_teacher = 0 ORDER BY name').fetchall()
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
            SELECT test_name, test_type, rank, physics_marks, physics_max, chemistry_marks, chemistry_max,
                   maths_marks, maths_max, total_marks, total_max, percentage
            FROM test_results
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
        return dict(results) if results else {'total_tests': 0, 'avg_percentage': 0, 'best_rank': None, 'highest_score': 0}
    except:
        return {'total_tests': 0, 'avg_percentage': 0, 'best_rank': None, 'highest_score': 0}

# ====================================================================
# ROUTES - AUTHENTICATION WITH DROPDOWN
# ====================================================================

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Get all student names for dropdown
    student_list = get_all_student_names()
    print(f"📋 Login page loaded with {len(student_list)} students")
    
    if request.method == 'POST':
        # Get student name from dropdown
        student_id = request.form.get('student_id')
        password = request.form.get('password', '').strip()
        
        # Also support username input for teachers
        username = request.form.get('username', '').strip()
        
        try:
            conn = get_db_connection()
            if not conn:
                flash('Database error. Please try again.', 'error')
                return render_template_string(LOGIN_TEMPLATE, student_list=student_list)
            
            # If student_id is selected from dropdown
            if student_id:
                user = conn.execute(
                    'SELECT * FROM students WHERE id = ?',
                    (student_id,)
                ).fetchone()
            # If username is entered (for teachers)
            elif username:
                user = conn.execute(
                    'SELECT * FROM students WHERE name = ? OR roll_no = ? OR email = ? OR LOWER(name) = LOWER(?)',
                    (username, username, username, username)
                ).fetchone()
            else:
                flash('Please select a student or enter your name', 'error')
                return render_template_string(LOGIN_TEMPLATE, student_list=student_list)
            
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
                flash('Invalid password. Please check your Mother\'s Name.', 'error')
                
        except Exception as e:
            print(f"❌ Login error: {e}")
            flash('Login error. Please try again.', 'error')
    
    return render_template_string(LOGIN_TEMPLATE, student_list=student_list)

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
        .divider span{background:white;padding:0 15px;}
        .teacher-section{border-top:2px solid #e0e0e0;padding-top:20px;margin-top:10px;}
        .student-count{font-size:12px;color:#999;margin-left:10px;}
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
            <label>👨‍🎓 Select Your Name <span class="student-count">({{ student_list|length }} students)</span></label>
            <select name="student_id" required>
                <option value="">-- Select Student --</option>
                {% for student in student_list %}
                <option value="{{ student.id }}">{{ student.name }} ({{ student.roll_no }})</option>
                {% endfor %}
            </select>
        </div>
        <div class="form-group">
            <label>🔑 Password (Mother's Name)</label>
            <input type="password" name="password" placeholder="Enter your mother's name (any case)" required>
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
                <input type="text" name="username" placeholder="physics_teacher / chemistry_teacher / maths_teacher">
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
# TEACHER DASHBOARD
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
        students = conn.execute('SELECT id, name, roll_no, batch, branch FROM students WHERE is_teacher = 0 ORDER BY name').fetchall()
        conn.close()
        
        exercises = [dict(e) for e in exercises]
        students = [dict(s) for s in students]
    except Exception as e:
        flash(f'Error: {e}', 'error')
        exercises = []
        students = []
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>{{ subject }} - Teacher Dashboard</title>
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
    ''', subject=subject, students=students, exercises=exercises, unread_count=unread_count)

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
        sync_all_from_google_sheets()
        flash('✅ Data synced from Google Sheets!', 'success')
    except Exception as e:
        flash(f'⚠️ Error syncing: {str(e)}', 'error')
    return redirect(url_for('teacher_dashboard'))

# ====================================================================
# MESSAGING ROUTES (Same as before)
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
    print("=" * 60)
    print("🎓 Bakliwal Tutorials Portal Running!")
    print("👤 Students: Select from dropdown, password = Mother's Name")
    print("👨‍🏫 Teachers: physics_teacher / physics_teacher")
    print("=" * 60)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
