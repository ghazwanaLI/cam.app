const express = require("express");
const fs      = require("fs");
const path    = require("path");
const session = require("express-session");

const app  = express();
const PORT = process.env.PORT || 3000;
const DB   = path.join(__dirname, "database.json");

// ── Middleware ──────────────────────────────────────────
app.use(express.json({ limit: "10mb" }));
app.use(express.static(path.join(__dirname, "public")));
app.use(session({
  secret: process.env.SECRET || "cam-salahadin-secret-2026",
  resave: false,
  saveUninitialized: false,
  cookie: { maxAge: 7 * 24 * 60 * 60 * 1000 }
}));

// ── DB helpers ──────────────────────────────────────────
const readDB  = () => JSON.parse(fs.readFileSync(DB, "utf8"));
const writeDB = d  => fs.writeFileSync(DB, JSON.stringify(d, null, 2));
const uid     = () => Date.now() + Math.floor(Math.random() * 999);

// ── Auth middleware ─────────────────────────────────────
const auth      = (req, res, next) => req.session.user ? next() : res.status(401).json({ error: "غير مصرح" });
const adminOnly = (req, res, next) => req.session.user?.role === "admin" ? next() : res.status(403).json({ error: "للمشرف فقط" });

// ═══════════════════════════════════════════
// AUTH
// ═══════════════════════════════════════════
app.post("/api/login", (req, res) => {
  const { phone, password } = req.body;
  const db   = readDB();
  const user = db.officers.find(o => o.phone === phone && o.password === password);
  if (!user) return res.status(401).json({ error: "رقم الهاتف أو كلمة المرور غير صحيحة" });
  const { password: _, ...safe } = user;
  req.session.user = safe;
  res.json({ ok: true, user: safe });
});

app.post("/api/logout", (req, res) => {
  req.session.destroy();
  res.json({ ok: true });
});

app.get("/api/me", auth, (req, res) => res.json(req.session.user));

// ═══════════════════════════════════════════
// STATIC DATA
// ═══════════════════════════════════════════
app.get("/api/districts", auth, (req, res) => res.json(readDB().districts));

app.get("/api/stations", auth, (req, res) => {
  const db   = readDB();
  const user = req.session.user;
  res.json(user.role === "admin" ? db.stations : db.stations.filter(s => s.district === user.district));
});

// ═══════════════════════════════════════════
// OFFICERS
// ═══════════════════════════════════════════
app.get("/api/officers", auth, adminOnly, (req, res) => {
  res.json(readDB().officers.map(({ password, ...o }) => o));
});

app.post("/api/officers", auth, adminOnly, (req, res) => {
  const { name, phone, password, district } = req.body;
  if (!name || !phone || !password || !district)
    return res.status(400).json({ error: "جميع الحقول مطلوبة" });
  const db = readDB();
  if (db.officers.find(o => o.phone === phone))
    return res.status(400).json({ error: "رقم الهاتف مسجل مسبقاً" });
  const o = { id: uid(), name, phone, password, district, role: "officer" };
  db.officers.push(o);
  writeDB(db);
  const { password: _, ...safe } = o;
  res.json(safe);
});

app.delete("/api/officers/:id", auth, adminOnly, (req, res) => {
  const db = readDB();
  db.officers = db.officers.filter(o => String(o.id) !== req.params.id);
  writeDB(db);
  res.json({ ok: true });
});

// ═══════════════════════════════════════════
// TOURS
// ═══════════════════════════════════════════
app.get("/api/tours", auth, (req, res) => {
  const db   = readDB();
  const user = req.session.user;
  const list = user.role === "admin"
    ? db.tours
    : db.tours.filter(t => t.officerId === user.id);
  res.json(list.sort((a, b) => new Date(b.date) - new Date(a.date)));
});

app.post("/api/tours", auth, (req, res) => {
  const user = req.session.user;
  const db   = readDB();
  const { district, station, visitType, date, time, notes, images } = req.body;
  if (!district || !station || !date)
    return res.status(400).json({ error: "القاطع والمحطة والتاريخ مطلوبة" });
  if (user.role === "officer" && user.district !== district)
    return res.status(403).json({ error: "لا يمكنك إضافة جولة لقاطع آخر" });
  const distName = db.districts.find(d => d.id === district)?.name || "";
  const statName = db.stations.find(s => s.id === station)?.name  || "";
  const tour = { id: uid(), district, station, distName, statName, officerId: user.id, officerName: user.name, visitType: visitType || "كشف دوري", date, time: time || "", notes: notes || "", images: images || [], createdAt: new Date().toISOString() };
  db.tours.push(tour);
  writeDB(db);
  res.json(tour);
});

app.delete("/api/tours/:id", auth, adminOnly, (req, res) => {
  const db = readDB();
  db.tours = db.tours.filter(t => String(t.id) !== req.params.id);
  writeDB(db);
  res.json({ ok: true });
});

// ═══════════════════════════════════════════
// MAINTENANCE
// ═══════════════════════════════════════════
app.get("/api/maintenance", auth, (req, res) => {
  const db   = readDB();
  const user = req.session.user;
  const list = user.role === "admin"
    ? db.maintenance
    : db.maintenance.filter(m => m.district === user.district);
  res.json(list.sort((a, b) => new Date(b.date) - new Date(a.date)));
});

app.post("/api/maintenance", auth, (req, res) => {
  const user = req.session.user;
  const db   = readDB();
  const { district, station, deviceType, qty, reason, techName, date, status, notes } = req.body;
  if (!district || !station || !date)
    return res.status(400).json({ error: "الحقول الأساسية مطلوبة" });
  if (user.role === "officer" && user.district !== district)
    return res.status(403).json({ error: "لا يمكنك إضافة صيانة لقاطع آخر" });
  const distName = db.districts.find(d => d.id === district)?.name || "";
  const statName = db.stations.find(s => s.id === station)?.name  || "";
  const rec = { id: uid(), district, station, distName, statName, deviceType: deviceType || "كاميرا مراقبة", qty: parseInt(qty) || 1, reason: reason || "", techName: techName || "", date, status: status || "مكتمل", notes: notes || "", createdAt: new Date().toISOString() };
  db.maintenance.push(rec);
  writeDB(db);
  res.json(rec);
});

app.patch("/api/maintenance/:id", auth, (req, res) => {
  const db  = readDB();
  const idx = db.maintenance.findIndex(m => String(m.id) === req.params.id);
  if (idx === -1) return res.status(404).json({ error: "السجل غير موجود" });
  db.maintenance[idx] = { ...db.maintenance[idx], ...req.body };
  writeDB(db);
  res.json(db.maintenance[idx]);
});

app.delete("/api/maintenance/:id", auth, adminOnly, (req, res) => {
  const db = readDB();
  db.maintenance = db.maintenance.filter(m => String(m.id) !== req.params.id);
  writeDB(db);
  res.json({ ok: true });
});

// ═══════════════════════════════════════════
// REPORTS
// ═══════════════════════════════════════════
app.get("/api/reports/monthly", auth, (req, res) => {
  const db    = readDB();
  const user  = req.session.user;
  const month = req.query.month || new Date().toISOString().slice(0, 7);
  const ALERT = 14;

  const tours = db.tours.filter(t =>
    t.date?.startsWith(month) &&
    (user.role === "admin" || t.officerId === user.id)
  );
  const maint = db.maintenance.filter(m =>
    m.date?.startsWith(month) &&
    (user.role === "admin" || m.district === user.district)
  );

  const deviceTotals = {};
  maint.forEach(m => { deviceTotals[m.deviceType] = (deviceTotals[m.deviceType] || 0) + (m.qty || 1); });

  const distStats = db.districts.map(d => ({
    id: d.id, name: d.name,
    tours:   tours.filter(t => t.district === d.id).length,
    maint:   maint.filter(m => m.district === d.id).length,
    devices: maint.filter(m => m.district === d.id).reduce((a, m) => a + (m.qty || 1), 0),
  }));

  const visible = user.role === "admin" ? db.stations : db.stations.filter(s => s.district === user.district);
  const alertStations = visible.filter(s => {
    const last = db.tours.filter(t => t.station === s.id).sort((a, b) => new Date(b.date) - new Date(a.date))[0];
    return !last || Math.floor((Date.now() - new Date(last.date)) / 86400000) >= ALERT;
  });

  res.json({
    month,
    tours:        tours.length,
    maint:        maint.length,
    pendingMaint: maint.filter(m => m.status === "معلق").length,
    deviceTotals,
    distStats,
    alertStations,
  });
});

// ═══════════════════════════════════════════
// CATCH-ALL
// ═══════════════════════════════════════════
app.get("*", (req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.listen(PORT, () => {
  console.log(`✅ Server running on port ${PORT}`);
});
