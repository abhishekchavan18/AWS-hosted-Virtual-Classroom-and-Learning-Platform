# pyrefly: ignore [missing-import]
from flask import Flask, render_template, request, redirect, url_for, flash, session
import boto3
import pymysql
from pymysql import MySQLError
import sqlite3
import os
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta, datetime

# Load configuration from .env file
load_dotenv()

# Repository maintained by samruddhi2026.
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "classroom_secret_key_12345")
app.permanent_session_lifetime = timedelta(minutes=15)  # Session timeout

# AWS S3 Config
S3_BUCKET = os.getenv("S3_BUCKET", 'aws-project-virtualclassroom')
S3_REGION = os.getenv("S3_REGION", 'eu-north-1')

# Database Config
DB_HOST = os.getenv("DB_HOST", '__YOUR_HOST_NAME__')
DB_USER = os.getenv("DB_USER", 'admin')
DB_PASSWORD = os.getenv("DB_PASSWORD", 'Ram393')
DB_NAME = os.getenv("DB_NAME", 'my-db')


# SQLite Row & Connection Wrapper to mimic pymysql.cursors.DictCursor
class SQLiteCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, params=None):
        # Convert %s placeholder syntax to ? for SQLite
        query = query.replace('%s', '?')
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
        except sqlite3.IntegrityError as e:
            # Map SQLite unique constraint violation to PyMySQL duplicate entry (1062)
            if "UNIQUE constraint failed" in str(e) or "PRIMARY KEY constraint failed" in str(e):
                raise MySQLError(1062, f"Duplicate entry: {str(e)}")
            raise MySQLError(0, str(e))
        except sqlite3.Error as e:
            raise MySQLError(0, str(e))

    def fetchone(self):
        row = self.cursor.fetchone()
        if row:
            return dict(row)
        return None

    def close(self):
        self.cursor.close()


class SQLiteConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def cursor(self):
        return SQLiteCursorWrapper(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def init_sqlite_db():
    conn = sqlite3.connect('classroom.db')
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# Dynamic Database Provider
class DynamicDB:
    def __init__(self):
        self.use_sqlite = False
        if DB_HOST == '__YOUR_HOST_NAME__' or not DB_HOST:
            self.use_sqlite = True
            init_sqlite_db()

    def connect(self):
        if self.use_sqlite:
            return SQLiteConnectionWrapper(sqlite3.connect('classroom.db'))
        try:
            return pymysql.connect(
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=3
            )
        except Exception as e:
            print(f"Database connection failed: {e}. Falling back to SQLite.")
            self.use_sqlite = True
            init_sqlite_db()
            return SQLiteConnectionWrapper(sqlite3.connect('classroom.db'))


db_helper = DynamicDB()


def get_db_connection():
    return db_helper.connect()


# Local storage fallback for S3 operations
class LocalStorageClient:
    def __init__(self, upload_dir='static/uploads'):
        self.upload_dir = upload_dir
        os.makedirs(self.upload_dir, exist_ok=True)

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None, Callback=None, Config=None):
        filepath = os.path.join(self.upload_dir, Key)
        Fileobj.save(filepath)

    def list_objects_v2(self, Bucket, **kwargs):
        files = []
        if os.path.exists(self.upload_dir):
            for name in os.listdir(self.upload_dir):
                path = os.path.join(self.upload_dir, name)
                if os.path.isfile(path):
                    files.append({
                        'Key': name,
                        'Size': os.path.getsize(path),
                        'LastModified': datetime.fromtimestamp(os.path.getmtime(path))
                    })
        return {'Contents': files}


# Dynamic S3 client provider
class DynamicS3Client:
    def __init__(self, s3_bucket, region_name):
        self.s3_bucket = s3_bucket
        self.region_name = region_name
        self.local = LocalStorageClient()
        self._s3 = None
        self.use_local = False

    @property
    def client(self):
        if self.use_local:
            return self.local
        if self._s3 is None:
            try:
                self._s3 = boto3.client('s3', region_name=self.region_name)
            except Exception as e:
                print(f"Failed to create boto3 client: {e}")
                self.use_local = True
                return self.local
        return self._s3

    def upload_fileobj(self, Fileobj, Bucket, Key, **kwargs):
        if self.use_local:
            return self.local.upload_fileobj(Fileobj, Bucket, Key, **kwargs)
        try:
            return self.client.upload_fileobj(Fileobj, Bucket, Key, **kwargs)
        except Exception as e:
            print(f"S3 upload failed: {e}. Falling back to local storage.")
            Fileobj.seek(0)
            self.use_local = True
            return self.local.upload_fileobj(Fileobj, Bucket, Key, **kwargs)

    def list_objects_v2(self, Bucket, **kwargs):
        if self.use_local:
            return self.local.list_objects_v2(Bucket, **kwargs)
        try:
            return self.client.list_objects_v2(Bucket=Bucket, **kwargs)
        except Exception as e:
            print(f"S3 list_objects_v2 failed: {e}. Falling back to local storage.")
            self.use_local = True
            return self.local.list_objects_v2(Bucket, **kwargs)


s3 = DynamicS3Client(S3_BUCKET, S3_REGION)

MOCK_COURSES = [
    {
        'title': 'Cloud Computing & AWS',
        'description': 'Learn core AWS services including EC2, S3, RDS, and IAM with hands-on labs.',
        'badge': 'Cloud',
        'image': 'https://images.unsplash.com/photo-1451187580459-43490279c0fa?q=80&w=600&auto=format&fit=crop',
        'link': 'cloud-aws'
    },
    {
        'title': 'Full-Stack Web Development',
        'description': 'Master modern web development using HTML, CSS, JavaScript, and Flask.',
        'badge': 'Web Dev',
        'image': 'https://images.unsplash.com/photo-1547082299-de196ea013d6?q=80&w=600&auto=format&fit=crop',
        'link': 'full-stack'
    },
    {
        'title': 'Introduction to Data Science',
        'description': 'Analyze data, build predictions, and visualize results using Python.',
        'badge': 'Data Science',
        'image': 'https://images.unsplash.com/photo-1551288049-bebda4e38f71?q=80&w=600&auto=format&fit=crop',
        'link': 'data-science'
    }
]


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['email']
        password = request.form['password']
        hashed_password = generate_password_hash(password)

        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_password))
            conn.commit()
            flash('Registration successful!', 'success')
            return redirect(url_for('login'))
        except MySQLError as e:
            if e.args[0] == 1062:  # Duplicate entry error
                flash('Username already exists!', 'danger')
            else:
                flash(f"Error: {str(e)}", 'danger')
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
            user = cursor.fetchone()
            if user and check_password_hash(user['password'], password):
                session['username'] = username
                flash('Login successful!', 'success')
                return redirect(url_for('content'))
            else:
                flash('Invalid credentials!', 'danger')
        except MySQLError as e:
            flash(f"Database error: {str(e)}", 'danger')
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    return render_template('login.html')


@app.route('/content', methods=['GET', 'POST'])
def content():
    if 'username' not in session:
        flash('Please log in to access content!', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        file = request.files['file']
        if file:
            try:
                # Seek to end to find size, then seek back
                file.seek(0, 2)
                size = file.tell()
                file.seek(0)
                
                if size > 5 * 1024 * 1024:  # 5 MB size limit
                    flash("File size exceeds limit!", 'danger')
                elif file.filename.split('.')[-1].lower() not in ['pdf', 'jpg', 'jpeg', 'png']:
                    flash("Invalid file type! Only PDF, JPEG, and PNG are allowed.", 'danger')
                else:
                    s3.upload_fileobj(file, S3_BUCKET, file.filename)
                    flash(f"{file.filename} uploaded successfully!", 'success')
            except Exception as e:
                flash(f"Error uploading file: {str(e)}", 'danger')

    try:
        raw_files = s3.list_objects_v2(Bucket=S3_BUCKET).get('Contents', [])
        files = []
        for f in raw_files:
            dt = f.get('LastModified')
            formatted_date = ""
            if dt:
                if isinstance(dt, datetime):
                    formatted_date = dt.strftime('%Y-%m-%d %H:%M')
                else:
                    formatted_date = str(dt)
            files.append({
                'Key': f.get('Key'),
                'Size': f.get('Size', 0),
                'LastModified': formatted_date
            })
    except Exception as e:
        files = []
        flash(f"Error fetching files: {str(e)}", 'danger')

    return render_template('content.html', files=files, courses=MOCK_COURSES)


@app.route('/enroll/<course_name>')
def enroll(course_name):
    if 'username' not in session:
        flash('Please log in to enroll in courses!', 'warning')
        return redirect(url_for('login'))
    flash(f"Successfully enrolled in course: {course_name}!", 'success')
    return redirect(url_for('content'))


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out!', 'info')
    return redirect(url_for('home'))


if __name__ == '__main__':
    app.run(debug=True)
