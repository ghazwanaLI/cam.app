#!/usr/bin/env python3
import json, os, hashlib, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

PORT = int(os.environ.get("PORT", 8080))
DB_FILE = "cam_db.json"

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# القائمة الكاملة للمحطات الحكومية المستخرجة من ملفك
DEFAULT_STATIONS = [
    {"id":1,"name":"محطة اشور","district":"الشرقاط","type":"حكومية"},
    {"id":2,"name":"محطة مكحول","district":"بيجي","type":"حكومية"},
    {"id":3,"name":"محطة الشهيد بدر","district":"بيجي","type":"حكومية"},
    {"id":4,"name":"محطة الحجاج","district":"بيجي","type":"حكومية"},
    {"id":5,"name":"محطة فتح الفتوح","district":"تكريت","type":"حكومية"},
    {"id":6,"name":"محطة تكريت القديمة","district":"تكريت","type":"حكومية"},
    {"id":7,"name":"محطة تكريت الجديدة","district":"تكريت","type":"حكومية"},
    {"id":8,"name":"محطة سامراء القديمة","district":"سامراء","type":"حكومية"},
    {"id":9,"name":"محطة سامراء الجديدة","district":"سامراء","type":"حكومية"},
    {"id":10,"name":"مركز توزيع سامراء","district":"سامراء","type":"حكومية"},
    {"id":11,"name":"محطة الشهيدة أمية","district":"العلم","type":"حكومية"},
    {"id":12,"name":"محطة الدور","district":"الدور","type":"حكومية"},
    {"id":13,"name":"محطة الانوار","district":"الدور","type":"حكومية"},
    {"id":14,"name":"محطة بلد","district":"بلد","type":"حكومية"},
    {"id":15,"name":"محطة اريحا","district":"بلد","type":"حكومية"},
    {"id":16,"name":"محطة الدجيل","district":"الدجيل","type":"حكومية"}
]

def default_db():
    return {
        "users": [{"id":1,"fullname":"مدير النظام","username":"admin","password":hash_pw("1000"),"role":"admin","active":True}],
        "stations": DEFAULT_STATIONS,
        "maintenance": [], "inventory": [], "coding": [], "next_station_id": 17
    }

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: return default_db()
    return default_db()

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False, indent=2)

class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        db = load_db()
        if p == "/":
            try:
                with open("cam_index.html", "r", encoding="utf-8") as f:
                    self.send_response(200); self.send_header("Content-Type", "text/html")
                    self.end_headers(); self.wfile.write(f.read().encode("utf-8"))
            except: self.send_error(404)
        elif p == "/api/stats_v2":
            sts = db.get("stations", []); maint = db.get("maintenance", [])
            gov = [s for s in sts if s.get("type") == "حكومية"]
            priv = [s for s in sts if s.get("type") == "أهلية"]
            g_m = sum(1 for m in maint if any(s['id'] == m.get('station_id') for s in gov))
            p_m = sum(1 for m in maint if any(s['id'] == m.get('station_id') for s in priv))
            self.send_json({"gov":{"count":len(gov),"maint":g_m}, "priv":{"count":len(priv),"maint":p_m}, "total_maint":len(maint), "total_reports":len(db.get("inventory",[]))})
        elif p == "/api/stations":
            self.send_json({"stations": db.get("stations", [])})

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"🚀 Server started on {PORT}")
    server.serve_forever()
