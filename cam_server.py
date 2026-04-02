#!/usr/bin/env python3
import json, os, hashlib, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

# إعدادات المنفذ وقاعدة البيانات
PORT = int(os.environ.get("PORT", 8082))
DB_FILE = "cam_db.json"

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def default_db():
    return {
        "users": [{
            "id": 1, "fullname": "مدير النظام", "username": "admin",
            "password": hash_pw("1000"), "role": "admin", "active": True,
            "perms": {"view": True, "edit": True, "del": True, "files": True, "reports": True}
        }],
        "stations": [],
        "tours": [],
        "maintenance": [],
        "inventory": [],
        "coding": [],
        "notifications": [],
        "next_station_id": 1,
        "next_maint_id": 1,
        "next_inv_id": 1,
        "next_coding_id": 1,
    }

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return default_db()

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f: 
        json.dump(db, f, ensure_ascii=False, indent=2)

class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        db = load_db()

        # 1. تحديث جلب الإحصائيات للوحة التحكم الجديدة
        if p == "/api/stats_v2":
            sts = db.get("stations", [])
            maint = db.get("maintenance", [])
            
            gov_stations = [s for s in sts if s.get("type") == "حكومية"]
            priv_stations = [s for s in sts if s.get("type") == "أهلية"]
            
            # حساب صيانات كل نوع
            gov_maint_count = sum(1 for m in maint if any(s['id'] == m['station_id'] for s in gov_stations))
            priv_maint_count = sum(1 for m in maint if any(s['id'] == m['station_id'] for s in priv_stations))

            self.send_json({
                "gov": {"count": len(gov_stations), "maint": gov_maint_count},
                "priv": {"count": len(priv_stations), "maint": priv_maint_count},
                "total_maint": len(maint),
                "total_reports": len(db.get("inventory", [])) # كمثال للتقارير
            })

        # 2. جلب المحطات مع الفلترة (حكومي/أهلي)
        elif p == "/api/stations":
            params = parse_qs(urlparse(self.path).query)
            st_type = params.get("type", [None])[0]
            sts = db.get("stations", [])
            if st_type:
                sts = [s for s in sts if s.get("type") == st_type]
            self.send_json({"stations": sts})

        # 3. جلب الترميز مع دعم "المعامل"
        elif p == "/api/coding":
            params = parse_qs(urlparse(self.path).query)
            cat = params.get("category", [None])[0] # gov, priv, factory
            devices = db.get("coding", [])
            if cat:
                devices = [d for d in devices if d.get("category") == cat]
            self.send_json({"devices": devices})

    def do_POST(self):
        # هنا تضاف عمليات الحفظ مع دعم التصنيفات الجديدة
        pass

# تشغيل السيرفر
if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Server started on port {PORT}")
    server.serve_forever()
