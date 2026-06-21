from flask import Flask, render_template, request, jsonify
from groq import Groq
import sqlite3
import datetime
import json

app = Flask(__name__)
import os
API_KEY = os.environ.get("GROQ_API_KEY", "")
client = Groq(api_key=API_KEY)

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
            status TEXT DEFAULT 'Pending'
        )
    """)
    conn.commit()
    conn.close()

def save_complaint(data, analysis):
    conn = sqlite3.connect("fdamitra.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO complaints 
        (timestamp, district, location, complaint_type, description, score, severity, category, tags, summary, recommended_action)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        analysis.get('recommended_action', '')
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
    conn.close()
    return {"critical": critical, "pending": pending, "resolved": resolved}

@app.route("/")
def home():
    return render_template("index.html")

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
        # Clean JSON
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        analysis = json.loads(raw)
        save_complaint(data, analysis)
        return jsonify(analysis)

    except Exception as e:
        # Smart fallback
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

@app.route("/complaints", methods=["GET"])
def complaints():
    rows = get_all_complaints()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "timestamp": r[1], "district": r[2],
            "location": r[3], "type": r[4], "description": r[5],
            "score": r[6], "severity": r[7], "category": r[8],
            "tags": json.loads(r[9]) if r[9] else [],
            "summary": r[10], "action": r[11], "status": r[12]
        })
    return jsonify(result)

@app.route("/stats", methods=["GET"])
def stats():
    return jsonify(get_stats())

if __name__ == "__main__":
    setup_db()
    print("\n" + "="*50)
    print("  FDAMitra Backend Running!")
    print("  http://localhost:5000")
    print("="*50 + "\n")
    import os
port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)