from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "mentor-portal-secret-2024")
CORS(app)

# ─── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ─── Email ─────────────────────────────────────────────────────────────────────
EMAIL_USER = os.environ.get("EMAIL")
EMAIL_PASS = os.environ.get("EMAIL_PASSWORD")


def send_email(to: str, subject: str, body: str):
    if not EMAIL_USER or not EMAIL_PASS:
        raise Exception("Email credentials not configured in .env")
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    # Port 587 STARTTLS works on Render/Railway (port 465 SSL is often blocked)
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, to, msg.as_string())
    except smtplib.SMTPException as e:
        raise Exception(f"SMTP error: {str(e)}")


# ─── Auth helper ──────────────────────────────────────────────────────────────
def get_token():
    auth = request.headers.get("Authorization", "")
    return auth.replace("Bearer ", "").strip()


def get_client_with_token(token: str) -> Client:
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    # Set the auth token so RLS policies apply correctly
    client.postgrest.auth(token)
    return client


# ─── Page Route ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ─── Auth API (kept for compatibility) ────────────────────────────────────────
@app.route("/api/auth/signup", methods=["POST"])
def signup():
    data = request.get_json() or {}
    email = data.get("email", "").strip()
    password = data.get("password", "")
    full_name = data.get("full_name", "").strip()

    if not all([email, password, full_name]):
        return jsonify({"error": "All fields are required"}), 400

    try:
        res = supabase.auth.sign_up({"email": email, "password": password})
        if res.user:
            user_id = res.user.id
            supabase.table("mentors").insert({
                "id": user_id,
                "email": email,
                "full_name": full_name
            }).execute()
            return jsonify({
                "user": {"id": user_id, "email": email},
                "access_token": res.session.access_token if res.session else None,
                "full_name": full_name
            })
        return jsonify({"error": "Signup failed"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/signin", methods=["POST"])
def signin():
    data = request.get_json() or {}
    email = data.get("email", "")
    password = data.get("password", "")

    try:
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if res.user and res.session:
            mentor = supabase.table("mentors").select("*").eq("id", res.user.id).maybe_single().execute()
            return jsonify({
                "user": {"id": res.user.id, "email": res.user.email},
                "access_token": res.session.access_token,
                "full_name": mentor.data["full_name"] if mentor.data else email
            })
        return jsonify({"error": "Invalid credentials"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 401


# ─── Students API ─────────────────────────────────────────────────────────────
@app.route("/api/students", methods=["GET"])
def get_students():
    token = get_token()
    try:
        client = get_client_with_token(token)
        res = client.table("students").select("*").order("created_at", desc=True).execute()
        return jsonify(res.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/students", methods=["POST"])
def add_student():
    token = get_token()
    data = request.get_json() or {}
    try:
        client = get_client_with_token(token)
        user = client.auth.get_user(token)
        data["mentor_id"] = user.user.id
        data["cgpa"] = float(data.get("cgpa") or 0)
        data["gpa"] = float(data.get("gpa") or 0)
        # Remove any extra keys
        allowed = {"mentor_id","student_name","register_number","phone_number","email",
                   "date_of_birth","blood_group","address","parent_name","parent_occupation",
                   "parent_phone","siblings_details","scholarship_details","hackathon_details",
                   "arrears_details","cgpa","gpa"}
        data = {k: v for k, v in data.items() if k in allowed}
        res = client.table("students").insert(data).execute()
        return jsonify(res.data[0] if res.data else {}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/students/<student_id>", methods=["GET"])
def get_student(student_id):
    token = get_token()
    try:
        client = get_client_with_token(token)
        res = client.table("students").select("*").eq("id", student_id).maybe_single().execute()
        if res.data:
            return jsonify(res.data)
        return jsonify({"error": "Student not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/students/<student_id>", methods=["PUT"])
def update_student(student_id):
    token = get_token()
    data = request.get_json() or {}
    try:
        client = get_client_with_token(token)
        data["cgpa"] = float(data.get("cgpa") or 0)
        data["gpa"] = float(data.get("gpa") or 0)
        # Strip protected fields
        for f in ["id", "mentor_id", "created_at", "updated_at"]:
            data.pop(f, None)
        res = client.table("students").update(data).eq("id", student_id).execute()
        return jsonify(res.data[0] if res.data else {})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/students/<student_id>", methods=["DELETE"])
def delete_student(student_id):
    token = get_token()
    try:
        client = get_client_with_token(token)
        client.table("students").delete().eq("id", student_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/stats", methods=["GET"])
def get_stats():
    token = get_token()
    try:
        client = get_client_with_token(token)
        res = client.table("students").select("*").execute()
        students = res.data or []
        return jsonify({
            "totalStudents": len(students),
            "studentsWithArrears": len([s for s in students if (s.get("arrears_details") or "").strip()]),
            "lowCGPAStudents": len([s for s in students if float(s.get("cgpa") or 0) < 7.0]),
            "scholarshipStudents": len([s for s in students if (s.get("scholarship_details") or "").strip()])
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── Announcement / Email ──────────────────────────────────────────────────────
@app.route("/api/announcement", methods=["POST"])
def send_announcement():
    try:
        data = request.get_json(force=True, silent=True) or {}
        message = (data.get("message") or "").strip()
        token = get_token()

        if not message:
            return jsonify({"error": "Message cannot be empty"}), 400

        if not EMAIL_USER or not EMAIL_PASS:
            return jsonify({"error": "Email credentials not set in .env (EMAIL and EMAIL_PASSWORD)"}), 500

        client = get_client_with_token(token)
        res = client.table("students").select("email, student_name").execute()
        students = res.data or []

        if not students:
            return jsonify({"error": "No students found. Add students first."}), 404

        sent, errors = 0, []
        for student in students:
            try:
                send_email(
                    to=student["email"],
                    subject="📢 New Announcement from Your Mentor",
                    body=f"Dear {student['student_name']},\n\n{message}\n\nRegards,\nYour Mentor"
                )
                sent += 1
            except Exception as email_err:
                errors.append(f"{student.get('email','?')}: {str(email_err)}")

        return jsonify({
            "success": True,
            "sent": sent,
            "total": len(students),
            "errors": errors,
            "message": f"Sent to {sent}/{len(students)} students" + (f" ({len(errors)} failed)" if errors else "")
        })

    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


# ─── Health check (useful for platforms like Render/Railway) ──────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "production") == "development"
    app.run(debug=debug, host="0.0.0.0", port=port)
