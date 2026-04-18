#!/usr/bin/env python3
"""نظام إدارة كاميرات المراقبة"""
import json, os, hashlib, uuid, base64, io, urllib.request, threading, copy
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta

PORT = int(os.environ.get("PORT", 8082))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_DB = bool(DATABASE_URL)
DB_FILE = "cam_db.json"

# ── Supabase (fallback رفع من السيرفر) ──
_SB_URL    = "https://vcgfmmdpjpiktkyorili.supabase.co"
_SB_KEY    = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZjZ2ZtbWRwanBpa3RreW9yaWxpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUyMzk1NzksImV4cCI6MjA5MDgxNTU3OX0.b0vlvL0uHmpQVACq2lWujQdJ54M_P60kWuhGP2_8S8o"
_SB_BUCKET = "Cam-files"


# ── Web Push (VAPID) ──
VAPID_PUBLIC  = "BDYjEFbxOkILMKSDeuXlQKe57smrqZp-dv8T_5FVTL3z0E0pQBrhCRcFml6TnPyQTDkW-h10TGTTAEOquMAGc-8"
VAPID_PRIVATE = "gQE828S4hWsEJ1on07e-sjLJec-1LvafA3CjybkIyfk"
VAPID_CLAIMS  = {"sub": "mailto:admin@opdc-cam.iq"}

def send_push_notification(subscription, title, body_text, tag="circ"):
    try:
        from pywebpush import webpush
        import json as _j
        ep = str(subscription.get('endpoint',''))[:80]
        print(f"[PUSH] Sending to: {ep}")
        webpush(
            subscription_info=subscription,
            data=_j.dumps({"title":title,"body":body_text,"tag":tag}),
            vapid_private_key=VAPID_PRIVATE,
            vapid_claims=VAPID_CLAIMS
        )
        print(f"[PUSH] OK: {title}")
        return True
    except Exception as e:
        print(f"[PUSH] FAILED {type(e).__name__}: {e}")
        return False

def send_push_to_district(district, title, body_text, tag="circ"):
    try:
        db = load_db()
        subs = db.get("push_subscriptions", {})
        users = db.get("users", [])
        print(f"[PUSH] {len(subs)} subscriptions total, district={district}")
        sent = 0
        for uid_str, sub_data in list(subs.items()):
            uid = int(uid_str)
            user = next((u2 for u2 in users if u2["id"]==uid), None)
            if not user:
                print(f"[PUSH] User {uid} not found"); continue
            u_dists = get_user_dists(user)
            if district != "الكل" and u_dists and district not in u_dists:
                print(f"[PUSH] Skip user {uid}: dists={u_dists} vs {district}"); continue
            sub = sub_data.get("subscription", sub_data)
            result = send_push_notification(sub, title, body_text, tag)
            if result: sent += 1
        print(f"[PUSH] Sent {sent}/{len(subs)} notifications")
    except Exception as e:
        print(f"[PUSH] send_push_to_district error: {e}")


def get_user_dists(u):
    """استخراج قواطع المستخدم — يدعم pipe-separated 'بيجي|العلم' والمصفوفة"""
    if u.get("role") == "admin":
        return []
    dists = u.get("districts")
    if dists and isinstance(dists, list) and len(dists):
        return [d.strip() for d in dists if d.strip()]
    raw = u.get("district", "")
    if not raw:
        return []
    return [d.strip() for d in raw.split("|") if d.strip()]

def _sb_upload(name, b64data, mime):
    try:
        raw = base64.b64decode(b64data)
        ext = "pdf" if mime=="application/pdf" else "jpg"
        path = f"{uuid.uuid4().hex[:12]}.{ext}"
        req = urllib.request.Request(
            f"{_SB_URL}/storage/v1/object/{_SB_BUCKET}/{path}",
            data=raw, method="POST"
        )
        req.add_header("Authorization", f"Bearer {_SB_KEY}")
        req.add_header("Content-Type", mime or "image/jpeg")
        req.add_header("x-upsert", "true")
        with urllib.request.urlopen(req, timeout=30) as r: r.read()
        return f"{_SB_URL}/storage/v1/object/public/{_SB_BUCKET}/{path}"
    except Exception as e:
        print(f"[SB] {e}"); return None

# ── Connection Pool ──
_pg_pool=[];_pg_lock=threading.Lock();_PG_MAX=5

def get_conn():
    import pg8000, urllib.parse as _up
    with _pg_lock:
        while _pg_pool:
            c=_pg_pool.pop()
            try: c.run("SELECT 1"); return c
            except: pass
    r=_up.urlparse(DATABASE_URL)
    return pg8000.connect(host=r.hostname,port=r.port or 5432,
        database=r.path.lstrip("/"),user=r.username,password=r.password,ssl_context=True)

def _rel(c):
    with _pg_lock:
        if len(_pg_pool)<_PG_MAX: _pg_pool.append(c)
        else:
            try: c.close()
            except: pass

# ── DB Cache ──
_db=None; _dbl=threading.RLock()



def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ── PostgreSQL ──
def get_conn():
    import pg8000, urllib.parse
    r = urllib.parse.urlparse(DATABASE_URL)
    return pg8000.connect(host=r.hostname,port=r.port or 5432,
        database=r.path.lstrip("/"),user=r.username,password=r.password,ssl_context=True)

def init_pg():
    c=get_conn();cur=c.cursor()
    for sql in [
        "CREATE TABLE IF NOT EXISTS cam_store (key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS cam_files (key TEXT PRIMARY KEY, name TEXT, data TEXT, mime TEXT)",
        "CREATE TABLE IF NOT EXISTS cam_logs (id SERIAL PRIMARY KEY, user_name TEXT, user_fullname TEXT, action TEXT, details TEXT, ip TEXT, created_at TIMESTAMP DEFAULT NOW())",
    ]: cur.execute(sql)
    c.commit()
    cur.execute("SELECT value FROM cam_store WHERE key='data'")
    row = cur.fetchone()
    if not row:
        # قاعدة البيانات فارغة — أدخل البيانات الافتراضية
        cur.execute("INSERT INTO cam_store VALUES ('data',%s)",[json.dumps(default_db(),ensure_ascii=False)])
        c.commit()
        print(f"[INIT] Created new DB with {len(STATIONS)} stations")
    else:
        # قاعدة البيانات موجودة — تحديث المحطات دائماً من STATIONS
        try:
            db = json.loads(row[0])
            db_stations = {s['id']:s for s in db.get('stations',[])}
            updated = False
            for st in STATIONS:
                if st['id'] not in db_stations:
                    db.setdefault('stations',[]).append(st)
                    updated = True
                    print(f"[INIT] Added station: {st['name']}")
                else:
                    # حدّث الاسم والقاطع لو تغيّرا
                    old = db_stations[st['id']]
                    if old.get('name') != st['name'] or old.get('district') != st['district']:
                        for i,s in enumerate(db['stations']):
                            if s['id'] == st['id']:
                                db['stations'][i].update({'name':st['name'],'district':st['district'],'type':st['type']})
                                updated = True
            if len(db.get('stations',[])) == 0:
                db['stations'] = STATIONS[:]
                updated = True
                print(f"[INIT] Restored {len(STATIONS)} stations")
            if updated:
                cur.execute("UPDATE cam_store SET value=%s WHERE key='data'",[json.dumps(db,ensure_ascii=False)])
                c.commit()
                print(f"[INIT] DB updated, total stations: {len(db['stations'])}")
        except Exception as e:
            print(f"[INIT] Sync error: {e}")
    cur.close(); _rel(c)

def pg_load():
    c=get_conn()
    try:
        cur=c.cursor(); cur.execute("SELECT value FROM cam_store WHERE key='data'")
        row=cur.fetchone(); cur.close(); return json.loads(row[0])
    finally: _rel(c)

def pg_save(db):
    c=get_conn()
    try:
        cur=c.cursor()
        cur.execute("UPDATE cam_store SET value=%s WHERE key='data'",[json.dumps(db,ensure_ascii=False)])
        c.commit(); cur.close()
    finally: _rel(c)

def pg_save_file(key,name,data,mime):
    c=get_conn()
    try:
        cur=c.cursor()
        cur.execute("INSERT INTO cam_files(key,name,data,mime) VALUES(%s,%s,%s,%s) ON CONFLICT(key) DO UPDATE SET name=%s,data=%s,mime=%s",[key,name,data,mime,name,data,mime])
        c.commit(); cur.close()
    finally: _rel(c)

def _sb_fetch_b64(url):
    """جلب ملف من Supabase URL وتحويله لـ base64"""
    try:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {_SB_KEY}")
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        return base64.b64encode(raw).decode(), mime
    except Exception as e:
        print(f"[SB_FETCH] {e}"); return None, None

def pg_load_file(key):
    c=get_conn()
    try:
        cur=c.cursor(); cur.execute("SELECT name,data,mime FROM cam_files WHERE key=%s",[key])
        row=cur.fetchone(); cur.close()
        if not row: return None
        name, data, mime = row[0], row[1], row[2]
        # الملفات الجديدة: base64 مباشر
        if data and not data.startswith("http"):
            return {"name": name, "data": data, "mime": mime}
        # الملفات القديمة: URL من Supabase — نجلبه ونحوّله base64
        if data and data.startswith("http"):
            b64, fetched_mime = _sb_fetch_b64(data)
            if b64:
                return {"name": name, "data": b64, "mime": fetched_mime or mime}
            return {"name": name, "data": None, "mime": mime, "url": data}
        return None
    finally: _rel(c)

def pg_del_file(key):
    c=get_conn()
    try:
        cur=c.cursor(); cur.execute("DELETE FROM cam_files WHERE key=%s",[key])
        c.commit(); cur.close()
    finally: _rel(c)

def pg_add_log(user,action,details,ip=""):
    try:
        c=get_conn()
        try:
            cur=c.cursor()
            cur.execute("INSERT INTO cam_logs(user_name,user_fullname,action,details,ip) VALUES(%s,%s,%s,%s,%s)",
                [user.get("username",""),user.get("fullname",""),action,details,ip])
            c.commit(); cur.close()
        finally: _rel(c)
    except: pass

def pg_get_logs(limit=100):
    c=get_conn()
    try:
        cur=c.cursor()
        cur.execute("SELECT id,user_name,user_fullname,action,details,ip,created_at FROM cam_logs ORDER BY created_at DESC LIMIT %s",[limit])
        rows=cur.fetchall(); cur.close()
        return [{"id":r[0],"username":r[1],"fullname":r[2],"action":r[3],"details":r[4],"ip":r[5],"time":str(r[6])} for r in rows]
    finally: _rel(c)

def load_db():
    global _db
    with _dbl:
        if _db is None:
            _db = pg_load() if USE_DB else (json.load(open(DB_FILE,encoding="utf-8")) if os.path.exists(DB_FILE) else default_db())
        return copy.deepcopy(_db)

def save_db(db):
    global _db
    with _dbl: _db=db
    if USE_DB:
        snap=copy.deepcopy(db)
        threading.Thread(target=_pgw,args=(snap,),daemon=True).start()
    else:
        with open(DB_FILE,"w",encoding="utf-8") as f: json.dump(db,f,ensure_ascii=False,indent=2)

def _pgw(db):
    try: pg_save(db)
    except Exception as e: print(f"[DB] {e}")

def save_file(key,name,data,mime):
    # حفظ base64 مباشرة في قاعدة البيانات — بدون Supabase
    if USE_DB: pg_save_file(key,name,data,mime); return
    db=load_db(); db["files"][key]={"name":name,"data":data,"mime":mime}; save_db(db)

def load_file(key):
    if USE_DB: return pg_load_file(key)
    return load_db()["files"].get(key)

def del_file(key):
    if USE_DB: pg_del_file(key); return
    db=load_db(); db["files"].pop(key,None); save_db(db)

def add_log(user,action,details,ip=""):
    if USE_DB: pg_add_log(user,action,details,ip)

def get_logs(limit=100):
    if USE_DB: return pg_get_logs(limit)
    return []

DISTRICTS = ["الشرقاط / الايمن","الشرقاط الايسر","بيجي","تكريت","سامراء","العلم","الدور","بلد","الدجيل"]

STATIONS = [
    {"id":1,"name":"محطة اشور","district":"الشرقاط","type":"حكومية","cam_working":28,"cam_broken":2,"station_status":"تعمل"},
    {"id":2,"name":"محطة مكحول","district":"بيجي","type":"حكومية","cam_working":30,"cam_broken":2,"station_status":"تعمل"},
    {"id":3,"name":"محطة الشهيد بدر","district":"بيجي","type":"حكومية","cam_working":32,"cam_broken":8,"station_status":"تعمل"},
    {"id":4,"name":"محطة الحجاج","district":"بيجي","type":"حكومية","cam_working":38,"cam_broken":4,"station_status":"تعمل"},
    {"id":5,"name":"محطة فتح الفتوح","district":"تكريت","type":"حكومية","cam_working":22,"cam_broken":8,"station_status":"تعمل"},
    {"id":6,"name":"محطة تكريت القديمة","district":"تكريت","type":"حكومية","cam_working":32,"cam_broken":10,"station_status":"تعمل"},
    {"id":7,"name":"محطة تكريت الجديدة","district":"تكريت","type":"حكومية","cam_working":22,"cam_broken":4,"station_status":"تعمل"},
    {"id":9,"name":"محطة سامراء الجديدة","district":"سامراء","type":"حكومية","cam_working":32,"cam_broken":0,"station_status":"تعمل"},
    {"id":10,"name":"مركز توزيع سامراء","district":"سامراء","type":"حكومية","cam_working":16,"cam_broken":0,"station_status":"تعمل"},
    {"id":11,"name":"محطة الشهيدة أمية","district":"العلم","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":12,"name":"محطة الدور","district":"الدور","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":13,"name":"محطة الانوار","district":"الدور","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":14,"name":"محطة بلد","district":"بلد","type":"حكومية","cam_working":24,"cam_broken":0,"station_status":"تعمل"},
    {"id":15,"name":"محطة اريحا","district":"بلد","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":16,"name":"محطة الدجيل","district":"الدجيل","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":17,"name":"محطة الطوز القديمة","district":"امرلي الطوز","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":18,"name":"محطة الطوز الجديدة","district":"امرلي الطوز","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":19,"name":"مركز توزيع الطوز","district":"امرلي الطوز","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":21,"name":"شعبة توزيع سامراء","district":"سامراء","type":"حكومية","cam_working":16,"cam_broken":0,"station_status":"تعمل"},
    {"id":22,"name":"مقر شعبة توزيع الشرقاط","district":"الشرقاط","type":"حكومية","cam_working":8,"cam_broken":8,"station_status":"تعمل"},
    {"id":23,"name":"مقر الفرع/مصفى بيجي","district":"بيجي","type":"حكومية","cam_working":50,"cam_broken":10,"station_status":"تعمل"},
    {"id":24,"name":"مستودع الشمال","district":"بيجي","type":"حكومية","cam_working":4,"cam_broken":0,"station_status":"تعمل"},
    {"id":25,"name":"مصفى الصينية","district":"بيجي","type":"حكومية","cam_working":5,"cam_broken":0,"station_status":"تعمل"},
    {"id":26,"name":"مستودع الجديد","district":"بيجي","type":"حكومية","cam_working":5,"cam_broken":0,"station_status":"تعمل"},
    {"id":27,"name":"مستودع الارسال","district":"بيجي","type":"حكومية","cam_working":4,"cam_broken":0,"station_status":"تعمل"},
    {"id":28,"name":"مقر شعبة توزيع الدجيل","district":"الدجيل","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":29,"name":"مقر الفرع -تكريت","district":"تكريت","type":"حكومية","cam_working":58,"cam_broken":10,"station_status":"تعمل"},
    {"id":30,"name":"مقر قسم التفتيش","district":"سامراء","type":"حكومية","cam_working":0,"cam_broken":16,"station_status":"تعمل"},
    {"id":31,"name":"محطة سامراء القديمة","district":"سامراء","type":"حكومية","cam_working":25,"cam_broken":5,"station_status":"تعمل"},
    {"id":32,"name":"محطة البوطعمة الاهلية","district":"بيجي","type":"أهلية","cam_working":16,"cam_broken":0,"station_status":"تعمل"},
    {"id":34,"name":"محطة الصخرة الاهلية","district":"بيجي","type":"أهلية","cam_working":24,"cam_broken":0,"station_status":"تعمل"},
    {"id":35,"name":"محطة بادية الحجاج","district":"بيجي","type":"أهلية","cam_working":16,"cam_broken":0,"station_status":"تعمل"},
    {"id":36,"name":"محطة هيبة العمران","district":"بيجي","type":"أهلية","cam_working":16,"cam_broken":0,"station_status":"تعمل"},
    {"id":37,"name":"العباسي الاهلية","district":"سامراء","type":"أهلية","cam_working":16,"cam_broken":1,"station_status":"تعمل"},
    {"id":38,"name":"ارض الدجيل الاهلية","district":"الدجيل","type":"أهلية","cam_working":16,"cam_broken":0,"station_status":"تعمل"},
    {"id":40,"name":"الحمرة الاهلية","district":"تكريت","type":"أهلية","cam_working":16,"cam_broken":0,"station_status":"تعمل"},
    {"id":41,"name":"درة بيجي","district":"بيجي","type":"أهلية","cam_working":20,"cam_broken":0,"station_status":"تعمل"},
    {"id":42,"name":"محطة الصخرة الاهلية","district":"بيجي","type":"أهلية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":43,"name":"محطة اشور","district":"الشرقاط","type":"حكومية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
    {"id":44,"name":"البندري الاهلية","district":"بيجي","type":"أهلية","cam_working":16,"cam_broken":0,"station_status":"تعمل"},
    {"id":45,"name":"الملك الاهلية","district":"بيجي","type":"أهلية","cam_working":16,"cam_broken":0,"station_status":"تعمل"},
    {"id":46,"name":"امواج البحر","district":"بيجي","type":"أهلية","cam_working":24,"cam_broken":0,"station_status":"تعمل"},
    {"id":47,"name":"الشراع الاهلبة","district":"بيجي","type":"أهلية","cam_working":15,"cam_broken":1,"station_status":"تعمل"},
    {"id":48,"name":"مقر الفرع / تكريت","district":"تكريت","type":"حكومية","cam_working":60,"cam_broken":3,"station_status":"تعمل"},
    {"id":49,"name":"مقر الهيأة الغربية / تكريت","district":"تكريت","type":"حكومية","cam_working":10,"cam_broken":0,"station_status":"تعمل"},
    {"id":50,"name":"بناية مقر شعبة التفتيش /تكريت","district":"تكريت","type":"حكومية","cam_working":10,"cam_broken":0,"station_status":"تعمل"},
    {"id":51,"name":"عين البنية","district":"بيجي","type":"أهلية","cam_working":18,"cam_broken":0,"station_status":"تعمل"},
    {"id":52,"name":"القلعة الاهلية","district":"الشرقاط / الايمن","type":"أهلية","cam_working":0,"cam_broken":0,"station_status":"تعمل"},
]

# Dynamic districts list stored in db
def default_db():
    return {
        "users":[{
            "id":1,"fullname":"مدير النظام","username":"admin",
            "password":hash_pw("1000"),"role":"admin","active":True,"district":"",
            "perms":{"view":True,"edit":True,"del":True,"files":True,"reports":True}
        }],
        "delegates":[],
        "stations": STATIONS,
        "tours":[],
        "maintenance":[],
        "cameras":[],
        "files":{},
        "next_user_id":2,
        "next_tour_id":1,
        "next_maintenance_id":1,
        "next_camera_id":1,
        "next_station_id":53,
        "next_delegate_id":1,
        "inventory":[],
        "next_inventory_id":1,
        "circulars":[],
        "circular_reads":[],"push_subscriptions":{},
        "next_circular_id":1,
        
        "custom_districts":[],
        "notifications":[],
        "inv_buildings":[],"next_inv_building_id":1,"handover":[],"next_handover_id":1,
        "inv_private":[],"next_inv_private_id":1,
        "next_notif_id":1,
    }


RESTORE_DB_JSON = '{"users": [{"id": 1, "fullname": "مدير النظام", "username": "admin", "password": "40510175845988f13f6162ed8526f0b09f73384467fa855e1e79b44a56562a58", "role": "admin", "active": true, "district": "", "perms": {"view": true, "edit": true, "del": true, "files": true, "reports": true}}], "delegates": [{"id": 1, "district": "الشرقاط", "name": "مثنى مسلط احمد", "phone": ""}, {"id": 2, "district": "بيجي", "name": "مثنى فياض علي", "phone": ""}, {"id": 3, "district": "تكريت", "name": "شاكر محمود حسين", "phone": ""}, {"id": 4, "district": "سامراء", "name": "محمد زكي اسماعيل", "phone": ""}, {"id": 5, "district": "العلم", "name": "احمد حامد هايس", "phone": ""}, {"id": 6, "district": "بلد", "name": "عبدالرزاق حسن", "phone": ""}, {"id": 7, "district": "الدجيل", "name": "ايهاب خميس ردام", "phone": ""}, {"id": 8, "district": "الدجيل", "name": "اسعد سعيد حاتم", "phone": ""}, {"id": 9, "district": "امرلي الطوز", "name": "سيف الدين", "phone": ""}], "stations": [{"id": 1, "name": "محطة اشور", "district": "الشرقاط", "type": "حكومية", "cam_working": 28, "cam_broken": 2}, {"id": 2, "name": "محطة مكحول", "district": "بيجي", "type": "حكومية", "cam_working": 30, "cam_broken": 2}, {"id": 3, "name": "محطة الشهيد بدر", "district": "بيجي", "type": "حكومية", "cam_working": 32, "cam_broken": 8}, {"id": 4, "name": "محطة الحجاج", "district": "بيجي", "type": "حكومية", "cam_working": 38, "cam_broken": 4}, {"id": 5, "name": "محطة فتح الفتوح", "district": "تكريت", "type": "حكومية", "cam_working": 22, "cam_broken": 8}, {"id": 6, "name": "محطة تكريت القديمة", "district": "تكريت", "type": "حكومية", "cam_working": 32, "cam_broken": 10}, {"id": 7, "name": "محطة تكريت الجديدة", "district": "تكريت", "type": "حكومية", "cam_working": 22, "cam_broken": 4}, {"id": 9, "name": "محطة سامراء الجديدة", "district": "سامراء", "type": "حكومية", "cam_working": 32, "cam_broken": 0}, {"id": 10, "name": "مركز توزيع سامراء", "district": "سامراء", "type": "حكومية", "cam_working": 16, "cam_broken": 0}, {"id": 11, "name": "محطة الشهيدة أمية", "district": "العلم", "type": "حكومية"}, {"id": 12, "name": "محطة الدور", "district": "الدور", "type": "حكومية"}, {"id": 13, "name": "محطة الانوار", "district": "الدور", "type": "حكومية"}, {"id": 14, "name": "محطة بلد", "district": "بلد", "type": "حكومية", "cam_working": 24, "cam_broken": 0}, {"id": 15, "name": "محطة اريحا", "district": "بلد", "type": "حكومية"}, {"id": 16, "name": "محطة الدجيل", "district": "الدجيل", "type": "حكومية"}, {"id": 17, "name": "محطة الطوز القديمة", "district": "امرلي الطوز", "type": "حكومية"}, {"id": 18, "name": "محطة الطوز الجديدة", "district": "امرلي الطوز", "type": "حكومية"}, {"id": 19, "name": "مركز توزيع الطوز", "district": "امرلي الطوز", "type": "حكومية"}, {"id": 21, "name": "شعبة توزيع سامراء", "district": "سامراء", "type": "حكومية", "cam_working": 16, "cam_broken": 0}, {"id": 22, "name": "مقر شعبة توزيع الشرقاط", "district": "الشرقاط", "type": "حكومية", "cam_working": 8, "cam_broken": 8}, {"id": 23, "name": "مقر الفرع/مصفى بيجي", "district": "بيجي", "type": "حكومية", "cam_working": 50, "cam_broken": 10}, {"id": 24, "name": "مستودع الشمال", "district": "بيجي", "type": "حكومية", "cam_working": 4, "cam_broken": 0}, {"id": 25, "name": "مصفى الصينية", "district": "بيجي", "type": "حكومية", "cam_working": 5, "cam_broken": 0}, {"id": 26, "name": "مستودع الجديد", "district": "بيجي", "type": "حكومية", "cam_working": 5, "cam_broken": 0}, {"id": 27, "name": "مستودع الارسال", "district": "بيجي", "type": "حكومية", "cam_working": 4, "cam_broken": 0}, {"id": 28, "name": "مقر شعبة توزيع الدجيل", "district": "الدجيل", "type": "حكومية"}, {"id": 29, "name": "مقر الفرع -تكريت", "district": "تكريت", "type": "حكومية", "cam_working": 58, "cam_broken": 10}, {"id": 30, "name": "مقر قسم التفتيش", "district": "سامراء", "type": "حكومية", "cam_working": 0, "cam_broken": 16, "main_cam_count": 16, "main_cam_type": "", "main_hdd_count": 4, "main_hdd_size": "8تيرا", "main_record_days": "أكثر من 90 يوم", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 31, "name": "محطة سامراء القديمة", "district": "سامراء", "type": "حكومية", "cam_working": 25, "cam_broken": 5}, {"id": 32, "name": "محطة البوطعمة الاهلية", "district": "بيجي", "type": "أهلية", "cam_working": 16, "cam_broken": 0, "main_cam_count": 8, "main_cam_type": "هيكفجن", "main_hdd_count": 2, "main_hdd_size": "8", "main_record_days": "90 يوم", "sanda_cam_count": 8, "sanda_cam_type": "هيكفجن", "sanda_hdd_count": 2, "sanda_hdd_size": "8 تيرا", "sanda_record_days": "90 يوم", "sanda_notes": ""}, {"id": 34, "name": "محطة الصخرة الاهلية", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 24, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 35, "name": "محطة بادية الحجاج", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 16, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 36, "name": "محطة هيبة العمران", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 16, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 37, "name": "العباسي الاهلية", "district": "سامراء", "districts": [], "type": "أهلية", "cam_working": 16, "cam_broken": 1, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 38, "name": "ارض الدجيل الاهلية", "district": "الدجيل", "districts": [], "type": "أهلية", "cam_working": 16, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 40, "name": "الحمرة الاهلية", "district": "تكريت", "districts": [], "type": "أهلية", "cam_working": 16, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 41, "name": "درة بيجي", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 20, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 42, "name": "محطة الصخرة الاهلية", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 0, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 43, "name": "محطة اشور", "district": "الشرقاط", "districts": [], "type": "حكومية", "cam_working": 0, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 44, "name": "البندري الاهلية", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 16, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 45, "name": "الملك الاهلية", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 16, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 46, "name": "امواج البحر", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 24, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 47, "name": "الشراع الاهلبة", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 15, "cam_broken": 1, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 48, "name": "مقر الفرع / تكريت", "district": "تكريت", "districts": [], "type": "حكومية", "cam_working": 60, "cam_broken": 3, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 49, "name": "مقر الهيأة الغربية / تكريت", "district": "تكريت", "districts": [], "type": "حكومية", "cam_working": 10, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 50, "name": "بناية مقر شعبة التفتيش /تكريت", "district": "تكريت", "districts": [], "type": "حكومية", "cam_working": 10, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 51, "name": "عين البنية", "district": "بيجي", "districts": [], "type": "أهلية", "cam_working": 18, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}, {"id": 52, "name": "القلعة الاهلية", "district": "الشرقاط / الايمن", "districts": [], "type": "أهلية", "cam_working": 0, "cam_broken": 0, "main_cam_count": 0, "main_cam_type": "", "main_hdd_count": 0, "main_hdd_size": "", "main_record_days": "", "sanda_cam_count": 0, "sanda_cam_type": "", "sanda_hdd_count": 0, "sanda_hdd_size": "", "sanda_record_days": "", "sanda_notes": ""}], "tours": [{"id": 28, "date": "2026-04-09", "district": "الشرقاط / الايمن", "station_id": 52, "station_name": "القلعة الاهلية", "visit_type": "متابعة", "notes": "للفحص", "technician": "مسؤول وحدة الكاميرات", "created_by": "مسؤول وحدة الكاميرات", "created_at": "2026-04-09 13:06"}], "maintenance": [{"id": 84, "date": "2026-04-08", "district": "بلد", "station_id": 14, "station_name": "محطة بلد", "device_type": "كاميرا(2)", "qty": 2, "reason": "قطع كيبل رئيسي/ استبدال كاميرات / اعادة توجيه كاميرات / تشغيل كاميرات", "technician": "غزون علي - عبدالرزاق محمد حسن - علي دحام", "notes": "", "created_by": "علي دحام محمود البشر", "created_at": "2026-04-08 18:09"}, {"id": 85, "date": "2026-04-09", "district": "العلم", "station_id": 11, "station_name": "محطة الشهيدة أمية", "device_type": "كاميرا(1)", "qty": 1, "reason": "قطع كيبل رئيسي/ استبدال كاميرات / اعادة توجيه كاميرات / تشغيل كاميرات", "technician": "غزوان علي مطر - علي دحام محمود", "notes": "", "created_by": "مسؤول وحدة الكاميرات", "created_at": "2026-04-09 12:11"}], "cameras": [], "files": {}, "next_user_id": 2, "next_tour_id": 29, "next_maintenance_id": 86, "next_camera_id": 1, "next_station_id": 53, "next_delegate_id": 10, "inventory": [], "next_inventory_id": 1, "circulars": [], "circular_reads": [], "push_subscriptions": {}, "next_circular_id": 1, "custom_districts": [], "notifications": [], "inv_buildings": [], "next_inv_building_id": 1, "handover": [], "next_handover_id": 1, "inv_private": [], "next_inv_private_id": 1, "next_notif_id": 1}'

def force_restore_if_empty():
    """يستعيد البيانات لو المحطات فارغة"""
    if not USE_DB: return
    try:
        db = pg_load()
        if len(db.get('stations',[])) < 5:
            print("[RESTORE] Stations empty, restoring from backup...")
            restore = json.loads(RESTORE_DB_JSON)
            # احتفظ بالمستخدمين والتعاميم الموجودة
            restore['users'] = db.get('users', restore['users'])
            restore['circulars'] = db.get('circulars', [])
            restore['circular_reads'] = db.get('circular_reads', [])
            restore['push_subscriptions'] = db.get('push_subscriptions', {})
            c=get_conn(); cur=c.cursor()
            cur.execute("UPDATE cam_store SET value=%s WHERE key='data'",[json.dumps(restore,ensure_ascii=False)])
            c.commit(); cur.close(); _rel(c)
            print(f"[RESTORE] Done! Stations: {len(restore['stations'])}")
    except Exception as e:
        print(f"[RESTORE] Error: {e}")

sessions = {}

def add_log_safe(user,action,details,ip=""):
    try: add_log(user,action,details,ip)
    except: pass

# ألوان لكل مستخدم
USER_COLORS = ["#3b82f6","#16a34a","#d97706","#7c3aed","#0891b2","#dc2626","#059669","#d946ef","#f97316","#0ea5e9"]

def get_user_color(username):
    h = sum(ord(c) for c in (username or ""))
    return USER_COLORS[h % len(USER_COLORS)]

def add_notification(db, user, action, details, urgent=False):
    """إضافة إشعار للمدير مع لون المستخدم وحذف بعد 24 ساعة"""
    try:
        if user.get("role") == "admin": return
        # حذف الإشعارات القديمة (أكثر من 24 ساعة)
        cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        db["notifications"] = [n for n in db.get("notifications",[]) if n.get("time","") >= cutoff]
        nid = db.get("next_notif_id", 1)
        db["next_notif_id"] = nid + 1
        notif = {
            "id": nid,
            "user": user.get("fullname", user.get("username", "—")),
            "username": user.get("username", ""),
            "action": action,
            "details": details,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "read": False,
            "urgent": urgent,
            "color": get_user_color(user.get("username","")),
        }
        if "notifications" not in db: db["notifications"] = []
        db["notifications"].insert(0, notif)
        db["notifications"] = db["notifications"][:200]
    except: pass



class Handler(BaseHTTPRequestHandler):
    timeout = 120  # 2 دقيقة للملفات الكبيرة
    def log_message(self,f,*a): pass
    def send_json(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",len(body))
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Cache-Control","no-cache")
        self.end_headers(); self.wfile.write(body)
    def send_html(self,content):
        body=content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",len(body))
        self.send_header("Cache-Control","no-cache,no-store,must-revalidate")
        self.end_headers(); self.wfile.write(body)
    def read_body(self):
        l=int(self.headers.get("Content-Length",0))
        if l > 20*1024*1024:  # 20MB max
            self.send_json({"error":"حجم الملف كبير جداً — الحد 20MB"},413); return {}
        if l==0: return {}
        data = b""
        remaining = l
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk: break
            data += chunk
            remaining -= len(chunk)
        try: return json.loads(data)
        except: return {}
    def get_token(self): return self.headers.get("Authorization","").replace("Bearer ","").strip()
    def get_user(self):
        uid=sessions.get(self.get_token())
        if not uid: return None
        return next((u for u in load_db()["users"] if u["id"]==uid),None)
    def require_auth(self):
        u=self.get_user()
        if not u: self.send_json({"error":"غير مصرح"},401)
        return u
    def can(self,user,perm):
        if user["role"]=="admin": return True
        return bool(user.get("perms",{}).get(perm))
    def ip(self): return self.headers.get("X-Forwarded-For",self.client_address[0])

    def do_OPTIONS(self):
        self.send_response(200)
        for h,v in [("Access-Control-Allow-Origin","*"),("Access-Control-Allow-Methods","GET,POST,PUT,DELETE,OPTIONS"),("Access-Control-Allow-Headers","Content-Type,Authorization")]: self.send_header(h,v)
        self.end_headers()

    def do_GET(self):
        p=urlparse(self.path).path.rstrip("/")
        # ── الصفحة الرئيسية + ملفات static (بدون authentication) ──
        if p in ("","/"): 
            f=os.path.join(os.path.dirname(os.path.abspath(__file__)),"cam_index.html")
            with open(f,"r",encoding="utf-8") as fh: self.send_html(fh.read()); return
        if p=="/favicon.ico":
            self.send_response(204); self.end_headers(); return
        if p.endswith(".html") or p.endswith(".js") or p.endswith(".css"):
            self.send_response(404); self.end_headers(); return
        u=self.require_auth()
        if not u: return
        db=load_db()
        if p=="/api/me": self.send_json({"ok":True,"user":{k:v for k,v in u.items() if k!="password"}})
        elif p=="/api/notifications":
            db=load_db()
            notifs=db.get("notifications",[])
            unread=sum(1 for n in notifs if not n.get("read"))
            self.send_json({"notifications":notifs,"unread":unread})

        elif p=="/api/stations":
            sts=db["stations"]
            if u["role"]!="admin":
                u_dists=get_user_dists(u)
                if u_dists: sts=[s for s in sts if s.get("district") in u_dists]
            self.send_json({"ok":True,"stations":sts})
        elif p=="/api/districts":
            db=load_db()
            all_d=DISTRICTS+[d for d in db.get("custom_districts",[]) if d not in DISTRICTS]
            self.send_json({"ok":True,"districts":all_d})
        elif p=="/api/districts_admin_placeholder":  # removed duplicate
            name=body.get("name","").strip()
            if not name: self.send_json({"error":"اسم القاطع مطلوب"},400); return
            db=load_db()
            if "custom_districts" not in db: db["custom_districts"]=[]
            if name in DISTRICTS or name in db["custom_districts"]:
                self.send_json({"error":"القاطع موجود مسبقاً"},400); return
            db["custom_districts"].append(name); save_db(db)
            self.send_json({"ok":True})

        elif "/api/circulars/" in p and p.endswith("/reads"):
            cid=int(p.split("/")[3])
            reads=[r for r in db.get("circular_reads",[]) if r.get("circ_id")==cid]
            self.send_json({"ok":True,"reads":reads})

        elif p=="/api/push/key":
            self.send_json({"ok":True,"publicKey":VAPID_PUBLIC})

        elif p=="/api/push/subscribe":
            self.send_json({"ok":True,"publicKey":VAPID_PUBLIC})



        elif p=="/api/delegates": self.send_json({"ok":True,"delegates":db.get("delegates",[])})
        elif p=="/api/tours":
            tours=db.get("tours",[])
            if u["role"]!="admin":
                u_dists=get_user_dists(u)
                if u_dists: tours=[t for t in tours if t.get("district") in u_dists]
            self.send_json({"ok":True,"tours":tours})
        elif p=="/api/maintenance":
            maint=db.get("maintenance",[])
            if u["role"]!="admin":
                u_dists=get_user_dists(u)
                if u_dists: maint=[m for m in maint if m.get("district") in u_dists]
            self.send_json({"ok":True,"maintenance":maint})
        elif p=="/api/cameras":
            cams=db.get("cameras",[])
            if u["role"]!="admin":
                u_dists=get_user_dists(u)
                if u_dists: cams=[c for c in cams if c.get("district") in u_dists]
            self.send_json({"ok":True,"cameras":cams})
        elif p=="/api/users":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            self.send_json({"ok":True,"users":[{k:v for k,v in x.items() if k!="password"} for x in db["users"]]})
        elif p=="/api/logs":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            qs=parse_qs(urlparse(self.path).query)
            self.send_json({"ok":True,"logs":get_logs(int(qs.get("limit",["100"])[0]))})
        elif "/api/circulars/" in p and p.endswith("/reads"):
            cid=int(p.split("/")[3])
            reads=[r for r in db.get("circular_reads",[]) if r.get("circ_id")==cid]
            self.send_json({"ok":True,"reads":reads})

        elif p=="/api/push/key":
            self.send_json({"ok":True,"publicKey":VAPID_PUBLIC})

        elif p=="/api/push/subscribe":
            self.send_json({"ok":True,"publicKey":VAPID_PUBLIC})

        elif p=="/api/circulars":
            circs=db.get("circulars",[])
            # Filter by district for non-admin — يدعم القواطع المتعددة "بيجي|العلم"
            if u["role"]!="admin":
                raw_dist = u.get("district","")
                u_dists = u.get("districts") or (raw_dist.split("|") if "|" in raw_dist else ([raw_dist] if raw_dist else []))
                u_dists = [d.strip() for d in u_dists if d.strip()]
                if u_dists:
                    circs=[c2 for c2 in circs if c2.get("district")=="الكل" or c2.get("district") in u_dists]
            self.send_json({"ok":True,"circulars":list(reversed(circs))})

        elif p=="/api/coding":
            devices=db.get("coding",[])
            if u["role"]!="admin" and u.get("district"):
                devices=[d for d in devices if not d.get("district") or d.get("district")==u["district"]]
            self.send_json({"ok":True,"devices":devices})

        elif p=="/api/scan_logs":
            code_q=urlparse(self.path).query.replace("code=","")
            logs=db.get("scan_logs",[])
            if code_q: logs=[l for l in logs if l.get("code")==code_q]
            self.send_json({"ok":True,"logs":list(reversed(logs[-100:]))})

        elif p=="/api/handover":
            items=db.get("handover",[])
            if u["role"]!="admin" and u.get("district"):
                u_dists=get_user_dists(u)
                if u_dists: items=[x for x in items if x.get("district") in u_dists]
            self.send_json({"ok":True,"items":items})
        elif p=="/api/inv_buildings":
            items=db.get("inv_buildings",[])
            if u["role"]!="admin" and u.get("district"):
                u_dists=get_user_dists(u)
                if u_dists: items=[x for x in items if x.get("district") in u_dists]
            self.send_json({"ok":True,"items":items})
        elif p=="/api/inv_private":
            items=db.get("inv_private",[])
            if u["role"]!="admin" and u.get("district"):
                u_dists=get_user_dists(u)
                if u_dists: items=[x for x in items if x.get("district") in u_dists]
            self.send_json({"ok":True,"items":items})

        elif p=="/api/inv_buildings":
            if "inv_buildings" not in db: db["inv_buildings"]=[]
            if "next_inv_building_id" not in db: db["next_inv_building_id"]=1
            iid=db["next_inv_building_id"]; db["next_inv_building_id"]+=1
            item={**body,"id":iid,"created_by":u.get("fullname",""),"created_at":datetime.now().strftime("%Y-%m-%d %H:%M")}
            db["inv_buildings"].append(item); save_db(db)
            self.send_json({"ok":True,"item":item})
        elif p=="/api/inv_private":
            if "inv_private" not in db: db["inv_private"]=[]
            if "next_inv_private_id" not in db: db["next_inv_private_id"]=1
            iid=db["next_inv_private_id"]; db["next_inv_private_id"]+=1
            item={**body,"id":iid,"created_by":u.get("fullname",""),"created_at":datetime.now().strftime("%Y-%m-%d %H:%M")}
            db["inv_private"].append(item); save_db(db)
            self.send_json({"ok":True,"item":item})
        elif p=="/api/inventory":
            inv=db.get("inventory",[])
            if u["role"]!="admin":
                u_dists=get_user_dists(u)
                if u_dists: inv=[x for x in inv if x.get("district") in u_dists]
            self.send_json({"ok":True,"inventory":inv})

        elif p=="/api/stats":
            tours=db.get("tours",[])
            maintenance=db.get("maintenance",[])
            cameras=db.get("cameras",[])
            stations=db.get("stations",[])
            if u["role"]!="admin" and u.get("district"):
                tours=[t for t in tours if t.get("district")==u["district"]]
                maintenance=[m for m in maintenance if m.get("district")==u["district"]]
                cameras=[c for c in cameras if c.get("district")==u["district"]]
                stations=[s for s in stations if s.get("district")==u["district"]]
            # Unvisited stations (no tour in last 30 days)
            now=datetime.now()
            visited_30=set(t["station_id"] for t in tours if t.get("date") and (now-datetime.strptime(t["date"],"%Y-%m-%d")).days<=30)
            unvisited=[s for s in stations if s["id"] not in visited_30]
            # Camera counts from stations (more accurate)
            st_cams_working=sum(int(s.get("cam_working",0)) for s in stations)
            st_cams_broken=sum(int(s.get("cam_broken",0)) for s in stations)
            # Fallback to cameras table if stations have no data
            cams_working=st_cams_working if st_cams_working>0 else len([c for c in cameras if c.get("status")=="working"])
            cams_broken=st_cams_broken if st_cams_broken>0 else len([c for c in cameras if c.get("status")=="broken"])
            # Per district breakdown
            district_cams={}
            for s in stations:
                d=s.get("district","")
                if d not in district_cams: district_cams[d]={"working":0,"broken":0}
                district_cams[d]["working"]+=int(s.get("cam_working",0))
                district_cams[d]["broken"]+=int(s.get("cam_broken",0))
            self.send_json({
                "ok":True,
                "total_stations":len(stations),
                "total_tours":len(tours),
                "total_maintenance":len(maintenance),
                "cameras_working":cams_working,
                "cameras_broken":cams_broken,
                "cameras_total":cams_working+cams_broken,
                "district_cams":district_cams,
                "unvisited_count":len(unvisited),
                "unvisited":unvisited[:5],
            })
        elif p.startswith("/api/files/"):
            key="/".join(p.split("/")[3:])
            result = load_file(key)
            print(f"[FILE LOAD] key={key} found={result is not None} data_len={len((result or {}).get('data','') or '')}")
            self.send_json({"ok":True,"file":result})

        elif p=="/sw.js":
            sw_code = (
                "self.addEventListener('push',function(e){"
                "var d={};try{d=e.data.json();}catch(x){}"
                "e.waitUntil(self.registration.showNotification(d.title||'\u062a\u0639\u0645\u064a\u0645',{"
                "body:d.body||'',tag:d.tag||'circ',dir:'rtl',lang:'ar',data:d,"
                "requireInteraction:true,vibrate:[200,100,200]}));});"
                "self.addEventListener('notificationclick',function(e){"
                "e.notification.close();"
                "var tag=e.notification.tag||'';"
                "var url='/?circ_notif=1&tag='+encodeURIComponent(tag);"
                "e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(function(cs){"
                "for(var i=0;i<cs.length;i++){"
                "if(cs[i].url&&'focus' in cs[i]){"
                "cs[i].postMessage({type:'circ_notif',tag:tag});"
                "return cs[i].focus();}}"
                "return clients.openWindow(url);}));});"
            ).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type','application/javascript; charset=utf-8')
            self.send_header('Service-Worker-Allowed','/')
            self.send_header('Cache-Control','no-cache')
            self.end_headers()
            self.wfile.write(sw_code.strip())
            return


        elif p=="/api/admin/restore" and u and u.get("role")=="admin":
            try:
                restore = json.loads(RESTORE_DB_JSON)
                db2=pg_load()
                restore['users'] = db2.get('users', restore['users'])
                restore['circulars'] = db2.get('circulars', [])
                restore['circular_reads'] = db2.get('circular_reads', [])
                restore['push_subscriptions'] = db2.get('push_subscriptions', {})
                c2=get_conn(); cur2=c2.cursor()
                cur2.execute("UPDATE cam_store SET value=%s WHERE key='data'",[json.dumps(restore,ensure_ascii=False)])
                c2.commit(); cur2.close(); _rel(c2)
                self.send_json({"ok":True,"stations":len(restore['stations']),"msg":"تم استعادة البيانات"})
            except Exception as e:
                self.send_json({"error":str(e)},500)

        else: self.send_json({"error":"غير موجود"},404)

    def do_POST(self):
        p=urlparse(self.path).path.rstrip("/")
        if p=="/api/login":
            body=self.read_body(); db=load_db()
            user=next((u for u in db["users"] if u["username"]==body.get("username") and u["password"]==hash_pw(body.get("password","")) and u.get("active",True)),None)
            if not user: self.send_json({"error":"اسم المستخدم أو كلمة المرور غير صحيحة"},401); return
            token=str(uuid.uuid4()); sessions[token]=user["id"]
            add_log_safe(user,"تسجيل دخول",f"دخل: {user['fullname']}",self.ip())
            self.send_json({"ok":True,"token":token,"user":{k:v for k,v in user.items() if k!="password"}}); return
        if p=="/api/logout":
            u2=self.get_user()
            if u2: add_log_safe(u2,"تسجيل خروج",f"خرج: {u2['fullname']}",self.ip())
            sessions.pop(self.get_token(),None); self.send_json({"ok":True}); return
        u=self.require_auth()
        if not u: return
        body=self.read_body(); db=load_db(); now=datetime.now().strftime("%Y-%m-%d %H:%M")

        if p=="/api/tours":
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            tid=db["next_tour_id"]; db["next_tour_id"]+=1
            station=next((s for s in db["stations"] if s["id"]==body.get("station_id")),{})
            tour={
                "id":tid,"date":body.get("date",""),"district":body.get("district",u.get("district","")),"station_id":body.get("station_id"),
                "station_name":station.get("name",""),"visit_type":body.get("visit_type",""),"notes":body.get("notes",""),
                "technician":body.get("technician",u["fullname"]),"created_by":u["fullname"],"created_at":now,
            }
            db["tours"].append(tour); save_db(db)
            add_log_safe(u,"إضافة جولة",f"جولة: {station.get('name','')} - {tour['date']}",self.ip())
            add_notification(db, u, "إضافة جولة ميدانية", f"محطة: {station.get('name','')} | التاريخ: {tour['date']} | الفني: {tour['technician']}", urgent=True)
            save_db(db)
            self.send_json({"ok":True,"tour":tour})

        elif p=="/api/maintenance":
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            mid=db["next_maintenance_id"]; db["next_maintenance_id"]+=1
            station=next((s for s in db["stations"] if s["id"]==body.get("station_id")),{})
            maint={
                "id":mid,"date":body.get("date",""),"district":body.get("district",u.get("district","")),"station_id":body.get("station_id"),
                "station_name":station.get("name",""),"device_type":body.get("device_type",""),"qty":body.get("qty",1),
                "reason":body.get("reason",""),"technician":body.get("technician",""),"notes":body.get("notes",""),
                "created_by":u["fullname"],"created_at":now,
            }
            db["maintenance"].append(maint); save_db(db)
            add_log_safe(u,"إضافة صيانة",f"صيانة: {station.get('name','')} - {maint['device_type']}",self.ip())
            add_notification(db, u, "إضافة صيانة ميدانية", f"محطة: {station.get('name','')} | الجهاز: {maint['device_type']} | السبب: {maint['reason']}", urgent=True)
            save_db(db)
            self.send_json({"ok":True,"maintenance":maint})

        elif p=="/api/cameras":
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            cid=db["next_camera_id"]; db["next_camera_id"]+=1
            station=next((s for s in db["stations"] if s["id"]==body.get("station_id")),{})
            cam={
                "id":cid,"cam_no":body.get("cam_no",""),"station_id":body.get("station_id"),
                "station_name":station.get("name",""),"district":body.get("district",station.get("district","")),
                "location_detail":body.get("location_detail",""),"cam_type":body.get("cam_type",""),
                "manufacturer":body.get("manufacturer",""),"status":body.get("status","working"),
                "last_maintenance":body.get("last_maintenance",""),"notes":body.get("notes",""),
                "created_by":u["fullname"],"updated_at":now,
            }
            db["cameras"].append(cam); save_db(db)
            add_log_safe(u,"إضافة كاميرا",f"كاميرا: {cam['cam_no']} - {station.get('name','')}",self.ip())
            self.send_json({"ok":True,"camera":cam})

        elif p=="/api/notifications":
            db=load_db()
            notifs=db.get("notifications",[])
            unread=sum(1 for n in notifs if not n.get("read"))
            self.send_json({"notifications":notifs,"unread":unread})

        elif p=="/api/stations":
            sid=db["next_station_id"]; db["next_station_id"]+=1
            st={"id":sid,"name":body.get("name",""),"district":body.get("district",""),"districts":body.get("districts",[]),"type":body.get("type","حكومية"),"cam_working":body.get("cam_working",0),"cam_broken":body.get("cam_broken",0),"main_cam_count":body.get("main_cam_count",0),"main_cam_type":body.get("main_cam_type",""),"main_hdd_count":body.get("main_hdd_count",0),"main_hdd_size":body.get("main_hdd_size",""),"main_record_days":body.get("main_record_days",""),"sanda_cam_count":body.get("sanda_cam_count",0),"sanda_cam_type":body.get("sanda_cam_type",""),"sanda_hdd_count":body.get("sanda_hdd_count",0),"sanda_hdd_size":body.get("sanda_hdd_size",""),"sanda_record_days":body.get("sanda_record_days",""),"sanda_notes":body.get("sanda_notes","")}
            db["stations"].append(st); save_db(db)
            add_notification(db, u, "إضافة محطة جديدة", f"المحطة: {st['name']} | القاطع: {st['district']}", urgent=True)
            save_db(db)
            self.send_json({"ok":True,"station":st})

        elif p=="/api/districts_admin_placeholder":  # removed duplicate
            name=body.get("name","").strip()
            if not name: self.send_json({"error":"اسم القاطع مطلوب"},400); return
            db=load_db()
            if "custom_districts" not in db: db["custom_districts"]=[]
            if name in DISTRICTS or name in db["custom_districts"]:
                self.send_json({"error":"القاطع موجود مسبقاً"},400); return
            db["custom_districts"].append(name); save_db(db)
            self.send_json({"ok":True})

        elif p=="/api/notifications/read":
            db=load_db()
            nid=body.get("id")
            for n in db.get("notifications",[]):
                if nid is None or n["id"]==nid: n["read"]=True
            save_db(db); self.send_json({"ok":True})

        elif "/api/circulars/" in p and p.endswith("/read"):
            cid=int(p.split("/")[3])
            if "circular_reads" not in db: db["circular_reads"]=[]
            # Remove old read by same user for same circ
            db["circular_reads"]=[r for r in db["circular_reads"] if not(r.get("circ_id")==cid and r.get("user_id")==u["id"])]
            db["circular_reads"].append({
                "circ_id":cid,"user_id":u["id"],
                "username":body.get("username",u["username"]),
                "fullname":body.get("fullname",u["fullname"]),
                "district":body.get("district",u.get("district","")),
                "read_at":body.get("read_at",datetime.now().strftime("%Y/%m/%d %H:%M"))
            })
            save_db(db); self.send_json({"ok":True})





        elif p=="/api/coding":
            if "coding" not in db: db["coding"]=[]
            if "next_coding_id" not in db: db["next_coding_id"]=1
            cid=db["next_coding_id"]; db["next_coding_id"]+=1
            dev={
                "id":cid,"code":body.get("code",""),"device_type":body.get("device_type","موقع"),
                "model":body.get("model",""),"district":body.get("district",""),
                "station_id":body.get("station_id"),"station_name":body.get("station_name",""),
                "location":body.get("location",""),"install_date":body.get("install_date",""),
                "nvrs":body.get("nvrs",[]),"hdds":body.get("hdds",[]),"switches":body.get("switches",[]),
                "cam_total":body.get("cam_total",0),"cam_working":body.get("cam_working",0),"cam_broken":body.get("cam_broken",0),
                "status":body.get("status","يعمل"),"notes":body.get("notes",""),
                "added_by":u["fullname"],"created_at":datetime.now().strftime("%Y-%m-%d")
            }
            db["coding"].append(dev); save_db(db)
            self.send_json({"ok":True,"device":dev})

        elif p=="/api/scan":
            if "scan_logs" not in db: db["scan_logs"]=[]
            code=body.get("code","")
            device=next((d for d in db.get("coding",[]) if d.get("code")==code),None)
            db["scan_logs"].append({
                "code":code,"scanned_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "scanned_by":body.get("scanned_by",""),"fullname":body.get("fullname",""),
                "ip":self.client_address[0],
                "station":device.get("station_name","") if device else "",
                "location":device.get("location","") if device else "",
                "found":device is not None
            })
            save_db(db); self.send_json({"ok":True,"found":device is not None})

        elif p=="/api/circulars":
            if u["role"]!="admin": self.send_json({"error":"المدير فقط يستطيع نشر التعاميم"},403); return
            if "circulars" not in db: db["circulars"]=[]
            if "next_circular_id" not in db: db["next_circular_id"]=1
            cid=db["next_circular_id"]; db["next_circular_id"]+=1
            circ={
                "id":cid,"title":body.get("title",""),"type":body.get("type","تعميم"),
                "district":body.get("district","الكل"),"body":body.get("body",""),
                "date":body.get("date",datetime.now().strftime("%Y-%m-%d")),
                "added_by":u["fullname"],"created_at":datetime.now().strftime("%Y-%m-%d %H:%M"),
                "has_file1":body.get("has_file1",False),"has_file2":body.get("has_file2",False),
                "has_file3":body.get("has_file3",False),
            }
            db["circulars"].append(circ); save_db(db)
            # إرسال push notification للمكلفين
            threading.Thread(target=send_push_to_district,
                args=(circ["district"], "📢 تعميم جديد: "+circ["title"],
                      circ.get("body","")[:80] or circ["type"], "circ_"+str(cid)),
                daemon=True).start()
            self.send_json({"ok":True,"circular":circ})

        elif p=="/api/push/subscribe":
            if not u: self.send_json({"error":"غير مصرح"},401); return
            sub = body.get("subscription") or body
            if "push_subscriptions" not in db: db["push_subscriptions"]={}
            db["push_subscriptions"][str(u["id"])] = {"subscription":sub,"username":u.get("username",""),"district":u.get("district","")}
            save_db(db)
            self.send_json({"ok":True})

        elif p=="/api/push/unsubscribe":
            if not u: self.send_json({"error":"غير مصرح"},401); return
            db.get("push_subscriptions",{}).pop(str(u["id"]),None)
            save_db(db); self.send_json({"ok":True})

        elif p=="/api/push/test":
            subs = db.get("push_subscriptions",{})
            print(f"[PUSH TEST] All subs: {list(subs.keys())}")
            sub_data = subs.get(str(u["id"]))
            if not sub_data:
                self.send_json({"error":"لا يوجد اشتراك للمستخدم "+str(u['id']),"all_subs":list(subs.keys())}); return
            sub = sub_data.get("subscription", sub_data)
            print(f"[PUSH TEST] Sub keys: {list(sub.keys()) if isinstance(sub,dict) else type(sub)}")
            result = send_push_notification(sub, "🔔 اختبار الإشعارات", "إذا وصلك هذا الإشعار فالنظام يعمل ✅", "test")
            self.send_json({"ok":result, "uid":u["id"], "sub_endpoint":str(sub.get("endpoint",""))[:50] if isinstance(sub,dict) else "?"})

        elif p=="/api/inventory":
            if not self.can(u,"edit") and not u.get("perms",{}).get("inventory"): self.send_json({"error":"لا صلاحية"},403); return
            db=load_db()
            if "inventory" not in db: db["inventory"]=[]
            if "next_inventory_id" not in db: db["next_inventory_id"]=1
            iid=db["next_inventory_id"]; db["next_inventory_id"]+=1
            now=datetime.now().strftime("%Y-%m-%d %H:%M")
            inv_item={
                "id":iid,"station_id":body.get("station_id"),"station_name":body.get("station_name",""),
                "district":body.get("district",""),"status":body.get("status","مكتمل"),
                "dvr_count":body.get("dvr_count",0),"dvr_spec":body.get("dvr_spec",""),"dvr_model":body.get("dvr_model",""),
                "hdd_count":body.get("hdd_count",0),"hdd_size":body.get("hdd_size",""),
                "storage_days":body.get("storage_days",""),
                "cam_count":body.get("cam_count",0),"cam_spec":body.get("cam_spec",""),"cam_res":body.get("cam_res",""),
                "cam_indoor":body.get("cam_indoor",0),"cam_outdoor":body.get("cam_outdoor",0),
                "cam_rows":body.get("cam_rows",""),
                "dvr_rows":body.get("dvr_rows",""),
                "power_source":body.get("power_source",""),
                "phone":body.get("phone",""),"coords":body.get("coords",""),
                "poe_count":body.get("poe_count",0),"poe_spec":body.get("poe_spec",""),
                "ups_count":body.get("ups_count",0),"ups_spec":body.get("ups_spec",""),
                "notes":body.get("notes",""),"created_by":u["fullname"],"updated_at":now,
            }
            db["inventory"].append(inv_item); save_db(db)
            add_log_safe(u,"إضافة جرد",f"جرد: {inv_item['station_name']}",self.ip())
            self.send_json({"ok":True,"inventory":inv_item})

        elif p=="/api/delegates":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            did=db["next_delegate_id"]; db["next_delegate_id"]+=1
            d={"id":did,"district":body.get("district",""),"name":body.get("name",""),"phone":body.get("phone","")}
            db["delegates"].append(d); save_db(db)
            self.send_json({"ok":True,"delegate":d})

        elif p=="/api/users":
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            if any(x["username"]==body.get("username") for x in db["users"]):
                self.send_json({"error":"اسم المستخدم مستخدم"},400); return
            uid=db["next_user_id"]; db["next_user_id"]+=1
            role=body.get("role","viewer")
            nu={"id":uid,"fullname":body.get("fullname",""),"username":body.get("username",""),
                "password":hash_pw(body.get("password","")),"role":role,"active":True,
                "district":body.get("district",""),
                "districts":body.get("districts",[]),
                "perms":{"view":True,"edit":True,"del":True,"files":True,"reports":True} if role=="admin"
                    else body.get("perms",{"view":True,"edit":False,"del":False,"files":False,"reports":False})}
            db["users"].append(nu); save_db(db)
            self.send_json({"ok":True,"user":{k:v for k,v in nu.items() if k!="password"}})

        elif p.startswith("/api/files/"):
            if not self.can(u,"files"): self.send_json({"error":"لا صلاحية"},403); return
            key="/".join(p.split("/")[3:])
            try:
                data=body.get("data","")
                print(f"[FILE SAVE] key={key} data_len={len(data)} mime={body.get('mime','')}")
                save_file(key,body.get("name",""),data,body.get("mime",""))
                print(f"[FILE SAVE] OK key={key}")
                self.send_json({"ok":True})
            except Exception as e:
                print(f"[FILE SAVE] ERROR key={key} err={e}")
                self.send_json({"error":str(e)},500)
        elif p=="/api/handover":
            if "handover" not in db: db["handover"]=[]
            if "next_handover_id" not in db: db["next_handover_id"]=1
            hid=db["next_handover_id"]; db["next_handover_id"]+=1
            item={**body,"id":hid,"created_by":u.get("fullname",""),"created_at":datetime.now().strftime("%Y-%m-%d %H:%M")}
            db["handover"].append(item); save_db(db)
            op="تسليم مواد" if body.get("op_type")=="deliver" else "استلام مواد"
            add_log_safe(u,op,f"{body.get('person','')} — {body.get('district','')}",self.ip())
            self.send_json({"ok":True,"item":item})
        else: self.send_json({"error":"غير موجود"},404)

    def do_PUT(self):
        p=urlparse(self.path).path.rstrip("/")
        u=self.require_auth()
        if not u: return
        body=self.read_body(); db=load_db()

        if p.startswith("/api/tours/"):
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            tid=int(p.split("/")[-1]); idx=next((i for i,t in enumerate(db["tours"]) if t["id"]==tid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["date","district","station_id","visit_type","notes","technician"]:
                if f in body: db["tours"][idx][f]=body[f]
            if "station_id" in body:
                st=next((s for s in db["stations"] if s["id"]==body["station_id"]),{})
                db["tours"][idx]["station_name"]=st.get("name","")
            add_notification(db, u, "تعديل جولة ميدانية", f"جولة المحطة: {db['tours'][idx].get('station_name','—')} | التاريخ: {db['tours'][idx].get('date','')}"); save_db(db); self.send_json({"ok":True,"tour":db["tours"][idx]})

        elif p.startswith("/api/maintenance/"):
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            mid=int(p.split("/")[-1]); idx=next((i for i,m in enumerate(db["maintenance"]) if m["id"]==mid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["date","district","station_id","device_type","qty","reason","technician","notes"]:
                if f in body: db["maintenance"][idx][f]=body[f]
            if "station_id" in body:
                st=next((s for s in db["stations"] if s["id"]==body["station_id"]),{})
                db["maintenance"][idx]["station_name"]=st.get("name","")
            add_notification(db, u, "تعديل صيانة ميدانية", f"صيانة المحطة: {db['maintenance'][idx].get('station_name','—')}"); save_db(db); self.send_json({"ok":True})

        elif p.startswith("/api/cameras/"):
            if not self.can(u,"edit"): self.send_json({"error":"لا صلاحية"},403); return
            cid=int(p.split("/")[-1]); idx=next((i for i,c in enumerate(db["cameras"]) if c["id"]==cid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["cam_no","station_id","location_detail","cam_type","manufacturer","status","last_maintenance","notes"]:
                if f in body: db["cameras"][idx][f]=body[f]
            db["cameras"][idx]["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M")
            if "station_id" in body:
                st=next((s for s in db["stations"] if s["id"]==body["station_id"]),{})
                db["cameras"][idx]["station_name"]=st.get("name","")
                db["cameras"][idx]["district"]=st.get("district","")
            save_db(db); self.send_json({"ok":True})

        elif p.startswith("/api/inventory/"):
            if not self.can(u,"edit") and not u.get("perms",{}).get("inventory"): self.send_json({"error":"لا صلاحية"},403); return
            iid=int(p.split("/")[-1]); idx=next((i for i,x in enumerate(db.get("inventory",[])) if x["id"]==iid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            fields=["station_id","station_name","district","status",
                    "dvr_count","dvr_spec","dvr_model","hdd_count","hdd_size","storage_days",
                    "cam_count","cam_spec","cam_res","cam_indoor","cam_outdoor","cam_rows","dvr_rows",
                    "power_source","phone","coords",
                    "poe_count","poe_spec","ups_count","ups_spec","notes"]
            for f in fields:
                if f in body: db["inventory"][idx][f]=body[f]
            db["inventory"][idx]["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M")
            save_db(db); self.send_json({"ok":True})

        elif p.startswith("/api/handover/"):
            hid=int(p.split("/")[-1])
            idx=next((i for i,x in enumerate(db.get("handover",[])) if x["id"]==hid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            if u["role"]!="admin":
                allowed={"status","signed_at","signed_by","signed_url"}
                body={k:v for k,v in body.items() if k in allowed}
            db["handover"][idx]={**db["handover"][idx],**body,"updated_at":datetime.now().strftime("%Y-%m-%d %H:%M")}
            save_db(db); self.send_json({"ok":True})
        elif p.startswith("/api/inv_buildings/"):
            iid=int(p.split("/")[-1]); idx=next((i for i,x in enumerate(db.get("inv_buildings",[])) if x["id"]==iid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            db["inv_buildings"][idx]={**db["inv_buildings"][idx],**body,"updated_at":datetime.now().strftime("%Y-%m-%d %H:%M")}
            save_db(db); self.send_json({"ok":True})
        elif p.startswith("/api/inv_private/"):
            iid=int(p.split("/")[-1]); idx=next((i for i,x in enumerate(db.get("inv_private",[])) if x["id"]==iid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            db["inv_private"][idx]={**db["inv_private"][idx],**body,"updated_at":datetime.now().strftime("%Y-%m-%d %H:%M")}
            save_db(db); self.send_json({"ok":True})
        elif p.startswith("/api/stations/"):
            sid=int(p.split("/")[-1]); idx=next((i for i,s in enumerate(db["stations"]) if s["id"]==sid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["name","district","type","cam_working","cam_broken","main_cam_count","main_cam_type","main_hdd_count","main_hdd_size","main_record_days","sanda_cam_count","sanda_cam_type","sanda_hdd_count","sanda_hdd_size","sanda_record_days","sanda_notes"]:
                if f in body: db["stations"][idx][f]=body[f]
            save_db(db); self.send_json({"ok":True})

        elif p.startswith("/api/users/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            uid=int(p.split("/")[-1]); idx=next((i for i,x in enumerate(db["users"]) if x["id"]==uid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            if "password" in body and body["password"]:
                if "old_password" in body:
                    if db["users"][idx]["password"]!=hash_pw(body["old_password"]):
                        self.send_json({"error":"كلمة المرور الحالية غير صحيحة"},400); return
                db["users"][idx]["password"]=hash_pw(body["password"])
            for f in ["fullname","username","role","active","perms","district","districts"]:
                if f in body: db["users"][idx][f]=body[f]
            save_db(db); self.send_json({"ok":True})


        elif p.startswith("/api/coding/"):
            cid=int(p.split("/")[-1]); idx=next((i for i,d in enumerate(db.get("coding",[])) if d["id"]==cid),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["code","device_type","model","district","station_id","station_name","location","install_date","nvrs","hdds","switches","cam_total","cam_working","cam_broken","status","notes"]:
                if f in body: db["coding"][idx][f]=body[f]
            save_db(db); self.send_json({"ok":True})

        elif p.startswith("/api/delegates/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            did=int(p.split("/")[-1]); idx=next((i for i,d in enumerate(db["delegates"]) if d["id"]==did),None)
            if idx is None: self.send_json({"error":"غير موجود"},404); return
            for f in ["district","name","phone"]:
                if f in body: db["delegates"][idx][f]=body[f]
            save_db(db); self.send_json({"ok":True})
        else: self.send_json({"error":"غير موجود"},404)

    def do_DELETE(self):
        p=urlparse(self.path).path.rstrip("/")
        u=self.require_auth()
        if not u: return
        db=load_db()

        if p.startswith("/api/tours/"):
            if not self.can(u,"del"): self.send_json({"error":"لا صلاحية"},403); return
            tid=int(p.split("/")[-1]); db["tours"]=[t for t in db["tours"] if t["id"]!=tid]; save_db(db)
            del_file(f"tour_{tid}"); self.send_json({"ok":True})

        elif p.startswith("/api/maintenance/"):
            if not self.can(u,"del"): self.send_json({"error":"لا صلاحية"},403); return
            mid=int(p.split("/")[-1]); db["maintenance"]=[m for m in db["maintenance"] if m["id"]!=mid]; save_db(db)
            self.send_json({"ok":True})

        elif p.startswith("/api/cameras/"):
            if not self.can(u,"del"): self.send_json({"error":"لا صلاحية"},403); return
            cid=int(p.split("/")[-1]); db["cameras"]=[c for c in db["cameras"] if c["id"]!=cid]; save_db(db)
            self.send_json({"ok":True})

        elif p.startswith("/api/users/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            uid=int(p.split("/")[-1])
            if uid==u["id"]: self.send_json({"error":"لا يمكن حذف حسابك"},400); return
            db["users"]=[x for x in db["users"] if x["id"]!=uid]; save_db(db)
            self.send_json({"ok":True})

        elif p.startswith("/api/coding/"):
            cid=int(p.split("/")[-1])
            db["coding"]=[d for d in db.get("coding",[]) if d["id"]!=cid]
            save_db(db); self.send_json({"ok":True})

        elif p.startswith("/api/circulars/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            cid=int(p.split("/")[-1])
            db["circulars"]=[c2 for c2 in db.get("circulars",[]) if c2["id"]!=cid]
            save_db(db); self.send_json({"ok":True})


        elif p.startswith("/api/inventory/"):
            if not self.can(u,"del"): self.send_json({"error":"لا صلاحية"},403); return
            iid=int(p.split("/")[-1])
            db["inventory"]=[x for x in db.get("inventory",[]) if x["id"]!=iid]; save_db(db)
            self.send_json({"ok":True})

        elif p.startswith("/api/handover/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            hid=int(p.split("/")[-1])
            db["handover"]=[x for x in db.get("handover",[]) if x["id"]!=hid]
            save_db(db); self.send_json({"ok":True})
        elif p.startswith("/api/inv_buildings/"):
            iid=int(p.split("/")[-1])
            db["inv_buildings"]=[x for x in db.get("inv_buildings",[]) if x["id"]!=iid]; save_db(db)
            self.send_json({"ok":True})
        elif p.startswith("/api/inv_private/"):
            iid=int(p.split("/")[-1])
            db["inv_private"]=[x for x in db.get("inv_private",[]) if x["id"]!=iid]; save_db(db)
            self.send_json({"ok":True})
        elif p.startswith("/api/stations/"):
            if u["role"]!="admin": self.send_json({"error":"غير مصرح"},403); return
            sid=int(p.split("/")[-1])
            db["stations"]=[s for s in db["stations"] if s["id"]!=sid]; save_db(db)
            self.send_json({"ok":True})

        elif p.startswith("/api/files/"):
            key="/".join(p.split("/")[3:]); del_file(key); self.send_json({"ok":True})
        else: self.send_json({"error":"غير موجود"},404)

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

if __name__=="__main__":
    if USE_DB:
        print("⏳ تهيئة قاعدة البيانات...")
        init_pg()
        force_restore_if_empty()
    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n  📹  نظام إدارة كاميرات المراقبة")
    print(f"  ✅  السيرفر يعمل على المنفذ {PORT}")
    print(f"  🌐  http://localhost:{PORT}\n")
    try: server.serve_forever()
    except KeyboardInterrupt: server.shutdown()
