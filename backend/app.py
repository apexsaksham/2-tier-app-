from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_cors import CORS            # <-- allow S3 frontend (or other origins) to call API
import sqlite3
import os

# Optional postgres driver. We import carefully so local (sqlite) use doesn't require psycopg2.
try:
    import psycopg2
except Exception:
    psycopg2 = None

# === Paths ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # backend/
FRONTEND_DIR = os.path.join(BASE_DIR, '..', 'frontend') # ../frontend
STATIC_DIR = os.path.join(FRONTEND_DIR, 'css')         # ../frontend/css

# Tell Flask where to find templates/static without moving files
# template_folder points to frontend/ so render_template("index.html") finds frontend/index.html
app = Flask(__name__, template_folder=FRONTEND_DIR, static_folder=STATIC_DIR)

# Allow cross-origin requests (used when frontend is hosted on S3)
CORS(app)

# === Database configuration ===
# For local use default to a file inside backend/
DB_FILE = os.path.join(BASE_DIR, 'votes.db')           # local sqlite file
DB_HOST = os.environ.get('DB_HOST')                    # if set -> use Postgres (RDS)
DB_NAME = os.environ.get('DB_NAME', 'postgres')
DB_USER = os.environ.get('DB_USER')
DB_PASS = os.environ.get('DB_PASS')
DB_PORT = int(os.environ.get('DB_PORT', 5432)) if os.environ.get('DB_PORT') else 5432

USE_POSTGRES = bool(DB_HOST)  # True when DB_HOST is provided

# --- SQLite initializer (local testing) ---
def init_sqlite():
    # create the local votes table if it doesn't exist
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate TEXT NOT NULL
            )
        ''')

# --- Postgres initializer (RDS) ---
def init_postgres():
    if psycopg2 is None:
        # do not crash silently — explicit error if user tries to use Postgres but psycopg2 isn't installed
        raise RuntimeError("psycopg2 is required for Postgres usage. Install psycopg2-binary.")
    conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS, port=DB_PORT)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS votes (
            id SERIAL PRIMARY KEY,
            candidate VARCHAR(255) NOT NULL
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

# Initialize the appropriate DB on start (safe for local)
if USE_POSTGRES:
    init_postgres()
else:
    init_sqlite()

# === DB helper functions used by both web UI and API ===
def insert_vote(candidate):
    """Insert a vote into the DB (Postgres or SQLite depending on env)."""
    if USE_POSTGRES:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS, port=DB_PORT)
        cur = conn.cursor()
        cur.execute('INSERT INTO votes (candidate) VALUES (%s)', (candidate,))
        conn.commit()
        cur.close()
        conn.close()
    else:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute('INSERT INTO votes (candidate) VALUES (?)', (candidate,))

def get_results():
    """Return aggregated results as a dict: {candidate: count, ...}"""
    if USE_POSTGRES:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS, port=DB_PORT)
        cur = conn.cursor()
        cur.execute('SELECT candidate, COUNT(*) FROM votes GROUP BY candidate')
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {row[0]: row[1] for row in rows}
    else:
        with sqlite3.connect(DB_FILE) as conn:
            cur = conn.cursor()
            cur.execute('SELECT candidate, COUNT(*) FROM votes GROUP BY candidate')
            rows = cur.fetchall()
            return {row[0]: row[1] for row in rows}

# ======================
# Routes (UI + API)
# ======================

# Local UI: serve the index.html from frontend/ so local testing continues to work exactly as before.
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # This preserves your original form POST behavior (server-rendered flow).
        provider = request.form.get('option')
        if provider:
            insert_vote(provider)                       # use same DB helper (keeps behavior identical)
            # It's helpful to show current results on form submit, so pass results too.
            results = get_results()
            return render_template("result.html", selected_option=provider, results=results)
    # GET - render the main page
    return render_template("index.html")

# Original /vote endpoint preserved (keeps compatibility if something posts to /vote)
@app.route('/vote', methods=['POST'])
def vote():
    selected_option = request.form.get('option')
    if selected_option:
        insert_vote(selected_option)
        results = get_results()
        # render the same result.html template (keeps your app's existing behavior intact)
        return render_template('result.html', selected_option=selected_option, results=results)
    return redirect('/')

# ---- New JSON API endpoints (required for 2-tier: S3 frontend -> API backend) ----
@app.route('/api/vote', methods=['POST'])
def api_vote():
    """
    Accepts JSON: {"option": "AWS"}
    Returns JSON: {"status": "ok"}
    This endpoint is used by the static frontend hosted on S3 (cross-origin requests allowed via CORS).
    """
    data = request.get_json(silent=True)
    if not data or 'option' not in data:
        return jsonify({'error': 'option required'}), 400
    insert_vote(data['option'])
    return jsonify({'status': 'ok'}), 200

@app.route('/api/results', methods=['GET'])
def api_results():
    """Return results JSON for the JS frontend to display."""
    return jsonify(get_results()), 200

# Health check for ALB / monitoring
@app.route('/health', methods=['GET'])
def health():
    return "OK", 200

# Run server (local dev). Keep host 0.0.0.0 so EC2/ALB can reach it when deployed.
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
