from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from groq import Groq
import sqlite3
import datetime
import json
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fdamitra-secret-key-2026")

API_KEY = os.environ.get("GROQ_API_KEY", "")
client = Groq(api_key=API_KEY)

# Admin credentials — change these
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "fdamitra_admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Maharashtra@2026")

SYSTEM_PROMPT = """You are FDAMitra AI — an intelligent food safety complaint analyzer for Maharashtra FDA.

When given a complaint, respond ONLY with a JSON object like this:
{
  "score": 85,
  "category": "Expired Products",
  "severity": "Critical",
  "tags": ["Expired Product", "Health Risk", "Immediate Action"],
  "summary": "High priority complaint about expired dairy products",
  "recommended_action": "Inspect within 24 hours",
  "estimated_risk": "High public health risk"
}

Rules:
- score: 0-100 (higher = more urgent)
- Critical complaints (score 80+): expired products, adulteration, illegal drugs, minor safety
- High (60-79): hygiene violations, mislabeling, counterfeit products  
- Medium (40-59): minor violations, paperwork issues
- tags: max 3 tags, short and specific
- Always respond ONLY with valid JSON, no extra text
"""

def setup_db():
    conn = sqlite3.connect("fdamitra.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            district TEXT,
            location TEXT,
            complaint_type TEXT,
            description TEXT,
            score INTEGER,
            severity TEXT,
            category TEXT,
            tags TEXT,
            summary TEXT,
            recommended_action TEXT,
            status TEXT DEFAULT 'Pending',
            complainant_name TEXT
        )
    """)
    # Add column if upgrading from older schema
    try:
        c.execute("ALTER TABLE complaints ADD COLUMN complainant_name TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

# Run DB setup at import time so it works under gunicorn/production too,
# not only when this file is executed directly.
setup_db()

def save_complaint(data, analysis):
    conn = sqlite3.connect("fdamitra.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO complaints 
        (timestamp, district, location, complaint_type, description, score, severity, category, tags, summary, recommended_action, complainant_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.datetime.now().isoformat(),
        data.get('district', ''),
        data.get('location', ''),
        data.get('type', ''),
        data.get('desc', ''),
        analysis.get('score', 0),
        analysis.get('severity', ''),
        analysis.get('category', ''),
        json.dumps(analysis.get('tags', [])),
        analysis.get('summary', ''),
        analysis.get('recommended_action', ''),
        data.get('name', '')
    ))
    conn.commit()
    conn.close()

def get_all_complaints():
    conn = sqlite3.connect("fdamitra.db")
    c = conn.cursor()
    c.execute("SELECT * FROM complaints ORDER BY score DESC, timestamp DESC LIMIT 50")
    rows = c.fetchall()
    conn.close()
    return rows

def get_stats():
    conn = sqlite3.connect("fdamitra.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM complaints WHERE severity='Critical'")
    critical = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM complaints WHERE status='Pending'")
    pending = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM complaints WHERE status='Resolved'")
    resolved = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM complaints")
    total = c.fetchone()[0]
    conn.close()
    return {"critical": critical, "pending": pending, "resolved": resolved, "total": total}

# All 36 Maharashtra districts
ALL_DISTRICTS = [
    "Mumbai City","Mumbai Suburban","Thane","Palghar","Raigad","Ratnagiri","Sindhudurg",
    "Pune","Satara","Sangli","Solapur","Kolhapur",
    "Nashik","Dhule","Nandurbar","Jalgaon","Ahmednagar",
    "Aurangabad","Jalna","Beed","Latur","Osmanabad","Nanded","Parbhani","Hingoli",
    "Amravati","Akola","Washim","Buldhana","Yavatmal",
    "Nagpur","Wardha","Chandrapur","Gadchiroli","Gondia","Bhandara"
]

def get_district_data():
    conn = sqlite3.connect("fdamitra.db")
    c = conn.cursor()
    result = {}
    for d in ALL_DISTRICTS:
        c.execute("SELECT COUNT(*) FROM complaints WHERE district=?", (d,))
        count = c.fetchone()[0]
        if count >= 10: level = "critical"
        elif count >= 5: level = "high"
        elif count >= 2: level = "medium"
        elif count == 1: level = "low"
        else: level = "none"
        result[d] = {"count": count, "level": level}
    conn.close()
    return result

def get_complaints_by_district(district):
    conn = sqlite3.connect("fdamitra.db")
    c = conn.cursor()
    c.execute("SELECT * FROM complaints WHERE district=? ORDER BY score DESC, timestamp DESC", (district,))
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "timestamp": r[1], "district": r[2],
            "location": r[3], "type": r[4], "description": r[5],
            "score": r[6], "severity": r[7], "category": r[8],
            "tags": json.loads(r[9]) if r[9] else [],
            "summary": r[10], "action": r[11], "status": r[12],
            "complainant_name": r[13] if len(r) > 13 else None
        })
    return result

def get_complaint_by_id(complaint_id):
    conn = sqlite3.connect("fdamitra.db")
    c = conn.cursor()
    c.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {
        "id": r[0], "timestamp": r[1], "district": r[2],
        "location": r[3], "type": r[4], "description": r[5],
        "score": r[6], "severity": r[7], "category": r[8],
        "tags": json.loads(r[9]) if r[9] else [],
        "summary": r[10], "action": r[11], "status": r[12],
        "complainant_name": r[13] if len(r) > 13 else None
    }

# ─── AUTH DECORATOR ───
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ─── PUBLIC ROUTES ───
@app.route("/")
def home():
    return render_template("landing.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.json
        username = data.get("username", "")
        password = data.get("password", "")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            session["username"] = username
            return jsonify({"success": True})
        return jsonify({"success": False, "message": "Invalid credentials"})
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("index.html")

# ─── PUBLIC API (anyone can submit a complaint) ───
@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json
    complaint_type = data.get('type', '')
    desc = data.get('desc', '')
    district = data.get('district', '')
    location = data.get('location', '')

    prompt = f"""Analyze this Maharashtra FDA complaint:
Type: {complaint_type}
District: {district}
Location: {location}
Description: {desc}

Provide priority score and analysis."""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.3,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        analysis = json.loads(raw)
        save_complaint(data, analysis)
        return jsonify(analysis)

    except Exception as e:
        scores = {
            'Expired Products': 85, 'Illegal Tobacco / Gutkha Sale': 90,
            'Food Adulteration': 88, 'Dirty Kitchen / Unhygienic': 70,
            'Fake / Counterfeit Product': 82, 'Drug Quality Issue': 87,
            'Wrong Labeling / Misbranding': 60, 'Other': 55
        }
        score = scores.get(complaint_type, 65)
        severity = "Critical" if score >= 80 else "High" if score >= 60 else "Medium"
        analysis = {
            "score": score, "severity": severity,
            "category": complaint_type,
            "tags": [complaint_type, district or "Maharashtra", severity + " Priority"],
            "summary": f"{complaint_type} reported in {district or 'Maharashtra'}",
            "recommended_action": "Inspect within 24 hours" if score >= 80 else "Schedule inspection",
            "estimated_risk": "High" if score >= 80 else "Medium"
        }
        save_complaint(data, analysis)
        return jsonify(analysis)

# ─── ADMIN-ONLY API ───
@app.route("/complaints", methods=["GET"])
@login_required
def complaints():
    rows = get_all_complaints()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "timestamp": r[1], "district": r[2],
            "location": r[3], "type": r[4], "description": r[5],
            "score": r[6], "severity": r[7], "category": r[8],
            "tags": json.loads(r[9]) if r[9] else [],
            "summary": r[10], "action": r[11], "status": r[12],
            "complainant_name": r[13] if len(r) > 13 else None
        })
    return jsonify(result)

@app.route("/complaint/<int:complaint_id>", methods=["GET"])
@login_required
def complaint_detail(complaint_id):
    c = get_complaint_by_id(complaint_id)
    if not c:
        return jsonify({"error": "Not found"}), 404
    return jsonify(c)

@app.route("/districts", methods=["GET"])
@login_required
def districts():
    return jsonify(get_district_data())

@app.route("/district/<district_name>/complaints", methods=["GET"])
@login_required
def district_complaints(district_name):
    return jsonify(get_complaints_by_district(district_name))

@app.route("/stats", methods=["GET"])
def stats():
    return jsonify(get_stats())

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  FDAMitra Backend Running!")
    print("  http://localhost:5000")
    print("="*50 + "\n")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)