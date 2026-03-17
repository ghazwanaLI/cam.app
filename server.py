import os, json, time, random
from flask import Flask, request, session, jsonify, send_from_directory
from flask_session import Session

app = Flask(__name__, static_folder=".", static_url_path="")

# ── Config ──────────────────────────────────────────────
app.secret_key = os.environ.get("SECRET", "cam-salahadin-2026")
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "/tmp/flask_sessions"
app.config["PERMANENT_SESSION_LIFETIME"] = 7 * 24 * 3600
Session(app)

DB_PATH = os.path.join(os.path.dirname(__file__), "database.json")

# ── DB helpers ───────────────────────────────────────────
def read_db():
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def write_db(data):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def uid():
    return int(time.time() * 1000) + random.randint(0, 999)

# ── Auth helpers ─────────────────────────────────────────
def get_user():
    return session.get("user")

def require_auth():
    if not get_user():
        return jsonify({"error": "غير مصرح"}), 401
    return None

def require_admin():
    u = get_user()
    if not u or u.get("role") != "admin":
        return jsonify({"error": "للمشرف فقط"}), 403
    return None

# ═══════════════════════════════════════════
# STATIC FILES
# ═══════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/<path:path>")
def static_files(path):
    try:
        return send_from_directory(".", path)
    except:
        return send_from_directory(".", "index.html")

# ═══════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════
@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    phone = data.get("phone", "")
    password = data.get("password", "")
    db = read_db()
    user = next((o for o in db["officers"] if o["phone"] == phone and o["password"] == password), None)
    if not user:
        return jsonify({"error": "رقم الهاتف أو كلمة المرور غير صحيحة"}), 401
    safe = {k: v for k, v in user.items() if k != "password"}
    session["user"] = safe
    session.permanent = True
    return jsonify({"ok": True, "user": safe})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me")
def me():
    err = require_auth()
    if err: return err
    return jsonify(get_user())

# ═══════════════════════════════════════════
# STATIC DATA
# ═══════════════════════════════════════════
@app.route("/api/districts")
def get_districts():
    err = require_auth()
    if err: return err
    return jsonify(read_db()["districts"])

@app.route("/api/stations")
def get_stations():
    err = require_auth()
    if err: return err
    db = read_db()
    u = get_user()
    if u["role"] == "admin":
        return jsonify(db["stations"])
    return jsonify([s for s in db["stations"] if s["district"] == u["district"]])

# ═══════════════════════════════════════════
# OFFICERS
# ═══════════════════════════════════════════
@app.route("/api/officers", methods=["GET"])
def get_officers():
    err = require_auth() or require_admin()
    if err: return err
    return jsonify([{k: v for k, v in o.items() if k != "password"} for o in read_db()["officers"]])

@app.route("/api/officers", methods=["POST"])
def add_officer():
    err = require_auth() or require_admin()
    if err: return err
    data = request.json
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    password = data.get("password", "")
    district = data.get("district", "")
    if not all([name, phone, password, district]):
        return jsonify({"error": "جميع الحقول مطلوبة"}), 400
    db = read_db()
    if any(o["phone"] == phone for o in db["officers"]):
        return jsonify({"error": "رقم الهاتف مسجل مسبقاً"}), 400
    officer = {"id": uid(), "name": name, "phone": phone, "password": password, "district": district, "role": "officer"}
    db["officers"].append(officer)
    write_db(db)
    return jsonify({k: v for k, v in officer.items() if k != "password"})

@app.route("/api/officers/<int:oid>", methods=["DELETE"])
def del_officer(oid):
    err = require_auth() or require_admin()
    if err: return err
    db = read_db()
    db["officers"] = [o for o in db["officers"] if o["id"] != oid]
    write_db(db)
    return jsonify({"ok": True})

# ═══════════════════════════════════════════
# TOURS
# ═══════════════════════════════════════════
@app.route("/api/tours", methods=["GET"])
def get_tours():
    err = require_auth()
    if err: return err
    db = read_db()
    u = get_user()
    tours = db["tours"] if u["role"] == "admin" else [t for t in db["tours"] if t["officerId"] == u["id"]]
    tours.sort(key=lambda x: x.get("date", ""), reverse=True)
    return jsonify(tours)

@app.route("/api/tours", methods=["POST"])
def add_tour():
    err = require_auth()
    if err: return err
    u = get_user()
    data = request.json
    district = data.get("district", "")
    station = data.get("station", "")
    date = data.get("date", "")
    if not all([district, station, date]):
        return jsonify({"error": "القاطع والمحطة والتاريخ مطلوبة"}), 400
    if u["role"] == "officer" and u["district"] != district:
        return jsonify({"error": "لا يمكنك إضافة جولة لقاطع آخر"}), 403
    db = read_db()
    dist_name = next((d["name"] for d in db["districts"] if d["id"] == district), "")
    stat_name = next((s["name"] for s in db["stations"] if s["id"] == station), "")
    tour = {
        "id": uid(), "district": district, "station": station,
        "distName": dist_name, "statName": stat_name,
        "officerId": u["id"], "officerName": u["name"],
        "visitType": data.get("visitType", "كشف دوري"),
        "date": date, "time": data.get("time", ""),
        "notes": data.get("notes", ""),
        "images": data.get("images", []),
        "createdAt": str(int(time.time()))
    }
    db["tours"].append(tour)
    write_db(db)
    return jsonify(tour)

@app.route("/api/tours/<int:tid>", methods=["DELETE"])
def del_tour(tid):
    err = require_auth() or require_admin()
    if err: return err
    db = read_db()
    db["tours"] = [t for t in db["tours"] if t["id"] != tid]
    write_db(db)
    return jsonify({"ok": True})

# ═══════════════════════════════════════════
# MAINTENANCE
# ═══════════════════════════════════════════
@app.route("/api/maintenance", methods=["GET"])
def get_maintenance():
    err = require_auth()
    if err: return err
    db = read_db()
    u = get_user()
    maint = db["maintenance"] if u["role"] == "admin" else [m for m in db["maintenance"] if m["district"] == u["district"]]
    maint.sort(key=lambda x: x.get("date", ""), reverse=True)
    return jsonify(maint)

@app.route("/api/maintenance", methods=["POST"])
def add_maintenance():
    err = require_auth()
    if err: return err
    u = get_user()
    data = request.json
    district = data.get("district", "")
    station = data.get("station", "")
    date = data.get("date", "")
    if not all([district, station, date]):
        return jsonify({"error": "الحقول الأساسية مطلوبة"}), 400
    if u["role"] == "officer" and u["district"] != district:
        return jsonify({"error": "لا يمكنك إضافة صيانة لقاطع آخر"}), 403
    db = read_db()
    dist_name = next((d["name"] for d in db["districts"] if d["id"] == district), "")
    stat_name = next((s["name"] for s in db["stations"] if s["id"] == station), "")
    record = {
        "id": uid(), "district": district, "station": station,
        "distName": dist_name, "statName": stat_name,
        "deviceType": data.get("deviceType", "كاميرا مراقبة"),
        "qty": int(data.get("qty", 1)),
        "reason": data.get("reason", ""),
        "techName": data.get("techName", ""),
        "date": date, "status": data.get("status", "مكتمل"),
        "notes": data.get("notes", ""),
        "createdAt": str(int(time.time()))
    }
    db["maintenance"].append(record)
    write_db(db)
    return jsonify(record)

@app.route("/api/maintenance/<int:mid>", methods=["PATCH"])
def update_maintenance(mid):
    err = require_auth()
    if err: return err
    db = read_db()
    for i, m in enumerate(db["maintenance"]):
        if m["id"] == mid:
            db["maintenance"][i].update(request.json)
            write_db(db)
            return jsonify(db["maintenance"][i])
    return jsonify({"error": "السجل غير موجود"}), 404

@app.route("/api/maintenance/<int:mid>", methods=["DELETE"])
def del_maintenance(mid):
    err = require_auth() or require_admin()
    if err: return err
    db = read_db()
    db["maintenance"] = [m for m in db["maintenance"] if m["id"] != mid]
    write_db(db)
    return jsonify({"ok": True})

# ═══════════════════════════════════════════
# REPORTS
# ═══════════════════════════════════════════
@app.route("/api/reports/monthly")
def monthly_report():
    err = require_auth()
    if err: return err
    u = get_user()
    db = read_db()
    month = request.args.get("month", "")
    ALERT = 14

    tours = [t for t in db["tours"] if t.get("date", "").startswith(month) and (u["role"] == "admin" or t["officerId"] == u["id"])]
    maint = [m for m in db["maintenance"] if m.get("date", "").startswith(month) and (u["role"] == "admin" or m["district"] == u["district"])]

    device_totals = {}
    for m in maint:
        device_totals[m["deviceType"]] = device_totals.get(m["deviceType"], 0) + (m.get("qty") or 1)

    dist_stats = []
    for d in db["districts"]:
        dist_stats.append({
            "id": d["id"], "name": d["name"],
            "tours": len([t for t in tours if t["district"] == d["id"]]),
            "maint": len([m for m in maint if m["district"] == d["id"]]),
            "devices": sum(m.get("qty", 1) for m in maint if m["district"] == d["id"]),
        })

    import datetime
    visible = db["stations"] if u["role"] == "admin" else [s for s in db["stations"] if s["district"] == u["district"]]
    alert_stations = []
    for s in visible:
        last = sorted([t for t in db["tours"] if t["station"] == s["id"]], key=lambda x: x.get("date",""), reverse=True)
        if not last:
            alert_stations.append(s)
        else:
            try:
                last_date = datetime.datetime.strptime(last[0]["date"], "%Y-%m-%d")
                days = (datetime.datetime.now() - last_date).days
                if days >= ALERT:
                    alert_stations.append(s)
            except:
                alert_stations.append(s)

    return jsonify({
        "month": month,
        "tours": len(tours),
        "maint": len(maint),
        "pendingMaint": len([m for m in maint if m["status"] == "معلق"]),
        "deviceTotals": device_totals,
        "distStats": dist_stats,
        "alertStations": alert_stations,
    })

# ═══════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
