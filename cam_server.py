#!/usr/bin/env python3
import json, os, hashlib, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# استشعار المنفذ تلقائياً من المنصة
PORT = int(os.environ.get("PORT", 8080))
DB_FILE = "cam_db.json"

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {"stations": [], "maintenance": [], "users": [], "inventory": [], "coding": []}

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

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"🚀 Server running on port {PORT}")
    server.serve_forever()
