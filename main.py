"""
CRM Backend v3 — Production Hardened
———————————————————————————————————————————————————————
TWO SEPARATE DATA PATHS:

  LEADS_PATH        — your existing telecaller leads (Mumbai, Delhi etc.)
                      All old data lives here. Nothing changes here.

  RESEARCHER_LEADS_PATH — NEW separate location for scraper-saved
                          no-phone leads (Pune salon/spa etc.)
                          Scraper writes here.
                          Researcher dashboard reads from here.
                          Admin counts come from here.
                          Completely isolated from old data.

So admin dashboard stats show 0 on fresh start because
RESEARCHER_LEADS_PATH is empty until scraper runs.
Old telecaller data is untouched in LEADS_PATH.
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.api_core.exceptions import ResourceExhausted
from datetime import datetime, date, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import os, time, threading, re, secrets

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

@app.errorhandler(ResourceExhausted)
def handle_quota(e):
    return jsonify({"error": "Firestore quota exceeded. Wait and retry.", "code": 429}), 429

# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
FIREBASE_JSON = os.environ.get(
    "FIREBASE_CREDENTIALS_PATH",
    os.path.join(BASE_DIR, "firebase.json")
)
PROJECT_ID    = "telecallercrm-45ec7"

# ── OLD path (Mumbai/Delhi telecaller leads — DO NOT TOUCH)
LEADS_PATH    = f"artifacts/{PROJECT_ID}/public/data/leads"

# ── NEW separate path (Pune scraper no-phone leads for researchers)
# Scraper saves here. Researcher reads from here. Admin stats from here.
# This is completely empty on fresh start → dashboard shows 0.
RESEARCHER_LEADS_PATH = f"artifacts/{PROJECT_ID}/public/data/researcher_leads"

USERS_PATH    = f"artifacts/{PROJECT_ID}/public/data/users"
LOGS_PATH     = f"artifacts/{PROJECT_ID}/public/data/audit_logs"
SESSIONS_PATH = f"artifacts/{PROJECT_ID}/public/data/sessions"
CHATS_PATH    = f"artifacts/{PROJECT_ID}/public/data/chats"
PAGE_SIZE     = 50
AUTH_TTL      = 86400  # 24 hours

# ═══════════════════════════════════════════════════════════════
# AREA GROUPS
# ═══════════════════════════════════════════════════════════════
AREA_GROUPS = {
    "Maharashtra": [
        "Mumbai","Andheri East","Andheri West","Bandra West","Bandra East",
        "Borivali West","Borivali East","Kandivali West","Kandivali East",
        "Malad West","Malad East","Goregaon West","Goregaon East",
        "Juhu","Vile Parle West","Vile Parle East","Santacruz West","Santacruz East",
        "Khar West","Powai","Chandivali","Ghatkopar West","Ghatkopar East",
        "Mulund West","Mulund East","Bhandup West","Chembur","Kurla West",
        "Sion","Matunga","Dadar West","Dadar East","Prabhadevi","Worli",
        "Lower Parel","Mahalakshmi","Byculla","Mazgaon","Colaba","Cuffe Parade",
        "Nariman Point","Fort","Churchgate","Marine Lines","Girgaon",
        "Malabar Hill","Breach Candy","Tardeo","Parel","Lalbaug","Wadala",
        "Sewri","Dharavi","Govandi","Mankhurd","Vikhroli","Kanjurmarg",
        "Dahisar West","Dahisar East",
        "Thane","Navi Mumbai","Pune","Nagpur","Nashik","Aurangabad",
        # Pune localities for researcher leads filter
        "Shivajinagar","Deccan","Kothrud","Karve Nagar","Erandwane",
        "Paud Road","Law College Road","FC Road","JM Road",
        "Hadapsar","Magarpatta","Kharadi","Wagholi","Viman Nagar",
        "Kalyani Nagar","Koregaon Park","Nagar Road","Lohegaon",
        "Baner","Balewadi","Aundh","Wakad","Pimple Saudagar",
        "Pimple Nilakh","Pimple Gurav","Sus Road","Hinjewadi",
        "Sinhagad Road","Dhayari","Narhe","Ambegaon","Katraj",
        "Kondhwa","NIBM Road","Undri","Pisoli","Wanowrie",
        "Pimpri","Chinchwad","Akurdi","Nigdi","Bhosari",
        "Dapodi","Kasarwadi","Sangvi","Vishrantwadi",
        "Sadashiv Peth","Narayan Peth","Camp","MG Road Pune",
    ],
    "Rajasthan": ["Jaipur","Jodhpur","Udaipur","Kota","Ajmer","Chitrakoot","Bikaner"],
    "Delhi NCR": [
        "New Delhi","Delhi","Gurgaon","Noida","Faridabad","Ghaziabad",
        "Connaught Place","Lajpat Nagar","Greater Kailash 1","Greater Kailash 2",
        "Hauz Khas","Safdarjung","Defence Colony","Vasant Kunj","Vasant Vihar",
        "Saket","Malviya Nagar","Mehrauli","Chhatarpur","Green Park",
        "Panchsheel Park","Andrews Ganj","Kalkaji","Govindpuri","Okhla",
        "Tughlakabad","Badarpur","Alaknanda","Chirag Delhi","Munirka",
        "Civil Lines","Model Town","Pitampura",
        "Rohini Sector 7","Rohini Sector 9","Rohini Sector 11",
        "Rohini Sector 13","Rohini Sector 15","Rohini Sector 17",
        "Shalimar Bagh","Ashok Vihar","Wazirpur","Saraswati Vihar","Punjabi Bagh",
        "Paschim Vihar","Rajouri Garden","Janakpuri","Vikaspuri","Uttam Nagar",
        "Laxmi Nagar","Preet Vihar","Mayur Vihar Phase 1","Mayur Vihar Phase 2",
        "Mayur Vihar Phase 3","Patparganj","IP Extension","Shahdara",
        "Vivek Vihar","Dilshad Garden","Krishna Nagar","Gandhi Nagar",
        "Dwarka Sector 6","Dwarka Sector 10","Dwarka Sector 12","Dwarka Sector 13",
        "Dwarka Sector 14","Dwarka Sector 21","Dwarka Mor","Bindapur",
        "Karol Bagh","Patel Nagar","Rajendra Nagar","Naraina","Tilak Nagar",
        "Kirti Nagar","Moti Nagar","Khan Market","Lodhi Colony","Jor Bagh",
        "Sundar Nagar","Nizamuddin",
    ],
    "Gujarat": ["Ahmedabad","Surat","Vadodara","Rajkot"],
    "Karnataka": ["Bangalore","Mysore","Hubli"],
    "Tamil Nadu": ["Chennai","Coimbatore","Madurai"],
    "Telangana": ["Hyderabad","Secunderabad","Warangal"],
}

# ═══════════════════════════════════════════════════════════════
# FIREBASE INIT
# ═══════════════════════════════════════════════════════════════
try:
    cred = credentials.Certificate(FIREBASE_JSON)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase connected.")
except Exception as e:
    print(f"❌ Firebase init error: {e}")
    db = None

# ═══════════════════════════════════════════════════════════════
# AUTH SESSION STORE
# ═══════════════════════════════════════════════════════════════
_auth_store = {}
_auth_lock  = threading.Lock()

def _create_token(username, role):
    token = secrets.token_hex(32)
    with _auth_lock:
        _auth_store[token] = {"username": username, "role": role, "ts": time.time()}
    return token

def _validate_token(token):
    with _auth_lock:
        s = _auth_store.get(token)
        if s and (time.time() - s["ts"]) < AUTH_TTL:
            return s
        _auth_store.pop(token, None)
    return None

def _destroy_token(token):
    with _auth_lock:
        _auth_store.pop(token, None)

# ═══════════════════════════════════════════════════════════════
# AUTH MIDDLEWARE
# ═══════════════════════════════════════════════════════════════
_PUBLIC = {"/api/login", "/api/health"}

@app.before_request
def _auth_check():
    path = request.path
    if not path.startswith("/api/") or path in _PUBLIC:
        return
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token:
        return jsonify({"error": "Unauthorized", "code": 401}), 401
    sess = _validate_token(token)
    if not sess:
        return jsonify({"error": "Session expired", "code": 401}), 401
    request.username  = sess["username"]
    request.user_role = sess["role"]

def _require_role(*roles):
    if not hasattr(request, "user_role") or request.user_role not in roles:
        return jsonify({"error": "Forbidden", "code": 403}), 403
    return None

# ═══════════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════════
_cache      = {}
_cache_lock = threading.Lock()

def cache_get(key):
    with _cache_lock:
        e = _cache.get(key)
        if e and time.time() < e["exp"]:
            return e["val"]
    return None

def cache_set(key, value, ttl=120):
    with _cache_lock:
        _cache[key] = {"val": value, "exp": time.time() + ttl}

def cache_bust(prefix):
    with _cache_lock:
        for k in list(_cache.keys()):
            if k.startswith(prefix):
                del _cache[k]

def _quota_err():
    return jsonify({"error": "Firestore quota exceeded. Wait a minute.", "code": 429}), 429

# ═══════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════
PHONE_RE    = re.compile(r"^\+?[\d\s\-]{7,15}$")
DATE_RE     = re.compile(r"^\d{4}-\d{2}-\d{2}$")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,50}$")

VALID_DISPOSITIONS = {"interested","not_interested","not_picked","busy","callback","completed"}
VALID_PIPELINE     = {"Interested","Processing","Converted","Closed"}
VALID_ROLES        = {"telecaller","researcher","admin","super_admin"}

def _ok_phone(p):    return bool(p and PHONE_RE.match(str(p).strip()))
def _ok_date(d):     return bool(d and DATE_RE.match(str(d)))
def _ok_str(v,mn=1,mx=200): return isinstance(v,str) and mn<=len(v.strip())<=mx
def _err(msg,code=400): return jsonify({"error":msg,"code":code}),code

def _safe_url(url):
    if not url: return "javascript:void(0)"
    s = str(url).strip()
    if re.match(r"^(javascript|data|vbscript)\s*:",s,re.I): return "javascript:void(0)"
    return s

def _to_ist(utc_dt):
    if not utc_dt or not hasattr(utc_dt,"strftime"): return None
    return utc_dt + timedelta(hours=5,minutes=30)

def _gmb(d):
    return d.get("link") or d.get("gmb_link") or d.get("gmbLink") or d.get("gmb") or ""

def _enrich(doc_id, data):
    d = {"id": doc_id, **data}
    d["gmb_link"] = _gmb(data)
    return d

# ═══════════════════════════════════════════════════════════════
# PAGE ROUTES
# ═══════════════════════════════════════════════════════════════
@app.route("/")
def login_page():      return send_from_directory(BASE_DIR, "index.html")
@app.route("/telecaller")
def telecaller_page(): return send_from_directory(BASE_DIR, "telecaller.html")
@app.route("/researcher")
def researcher_page(): return send_from_directory(BASE_DIR, "researcher.html")
@app.route("/admin")
def admin_page():      return send_from_directory(BASE_DIR, "admin.html")
@app.route("/superadmin")
def superadmin_page(): return send_from_directory(BASE_DIR, "admin.html")

# ═══════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/login", methods=["POST"])
def login():
    data     = request.json or {}
    username = str(data.get("username","")).strip()
    password = str(data.get("password",""))
    if not username or not password:
        return _err("Username and password required")
    # Try exact match first, then casing variants for case-insensitive login
    ck = f"user:{username.lower()}"
    ud = cache_get(ck)
    refs = None
    if ud is None:
        refs = db.collection(USERS_PATH).where(
            filter=FieldFilter("username","==",username)
        ).limit(1).get()
        if not refs:
            for variant in [username.lower(), username.capitalize(), username.title()]:
                refs = db.collection(USERS_PATH).where(
                    filter=FieldFilter("username","==",variant)
                ).limit(1).get()
                if refs: break
        if not refs: return _err("User not found",404)
        ud = refs[0].to_dict()
        cache_set(ck,ud,ttl=60)
    stored  = ud.get("password","")
    auth_ok = check_password_hash(stored,password) if stored.startswith(("pbkdf2:","scrypt:")) else False
    if not auth_ok and stored == password:
        auth_ok = True
        try:
            if refs: refs[0].reference.update({"password":generate_password_hash(password)})
            cache_bust(f"user:{username.lower()}")
        except Exception: pass
    if not auth_ok: return _err("Invalid credentials",401)
    # Use canonical username from Firestore so it matches assigned_to values set by admin
    canonical_username = ud.get("username", username)
    token = _create_token(canonical_username, ud.get("role"))
    db.collection(LOGS_PATH).add({
        "action":"login","done_by":canonical_username,
        "timestamp":firestore.SERVER_TIMESTAMP,"date":str(date.today())
    })
    return jsonify({"status":"success","username":canonical_username,"role":ud.get("role"),"token":token})


@app.route("/api/logout", methods=["POST"])
def logout():
    token = request.headers.get("Authorization","").replace("Bearer ","").strip()
    _destroy_token(token)
    uname = str((request.json or {}).get("username","")).strip()
    if uname:
        db.collection(LOGS_PATH).add({
            "action":"logout","done_by":uname,
            "timestamp":firestore.SERVER_TIMESTAMP,"date":str(date.today())
        })
    return jsonify({"status":"success"})

# ═══════════════════════════════════════════════════════════════
# STATS — reads from RESEARCHER_LEADS_PATH (new separate location)
# Shows 0 on fresh start. Grows only as scraper adds new data.
# ═══════════════════════════════════════════════════════════════
@app.route("/api/admin/counts-only", methods=["GET"])
def get_counts_only():
    r = _require_role("admin","super_admin")
    if r: return r
    cached = cache_get("counts_only")
    if cached: return jsonify(cached)
    try:
        col   = db.collection(RESEARCHER_LEADS_PATH)
        total = col.count().get()[0][0].value
        # no_phone = all leads here since scraper only saves NO phone leads
        noph  = col.where(filter=FieldFilter("phone","==","NO")).count().get()[0][0].value
        # raw_pool = unassigned
        raw   = col.where(
            filter=FieldFilter("assigned_to","==",None)
        ).count().get()[0][0].value
        # completed = researcher marked done
        done  = col.where(
            filter=FieldFilter("status","==","research_done")
        ).count().get()[0][0].value
        result = {"total":total,"raw_pool":raw,"no_phone":noph,"completed":done}
        cache_set("counts_only",result,ttl=90)
        return jsonify(result)
    except ResourceExhausted:
        return _quota_err()
    except Exception as e:
        return _err(str(e),500)


@app.route("/api/stats", methods=["GET"])
def get_global_stats():
    # Also reads from new path so telecaller dashboard is consistent
    cached = cache_get("global_stats")
    if cached: return jsonify(cached)
    try:
        col   = db.collection(RESEARCHER_LEADS_PATH)
        total = col.count().get()[0][0].value
        done  = col.where(
            filter=FieldFilter("status","==","research_done")
        ).count().get()[0][0].value
        noph  = col.where(
            filter=FieldFilter("phone","==","NO")
        ).count().get()[0][0].value
        raw   = col.where(
            filter=FieldFilter("assigned_to","==",None)
        ).count().get()[0][0].value
        result = {"total":total,"new":raw,"no_phone":noph,"called":done}
        cache_set("global_stats",result,ttl=60)
        return jsonify(result)
    except ResourceExhausted:
        return _quota_err()

# ═══════════════════════════════════════════════════════════════
# LEAD POOL — reads from RESEARCHER_LEADS_PATH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/admin/all-leads", methods=["GET"])
def get_all_leads():
    r = _require_role("admin","super_admin")
    if r: return r
    area          = request.args.get("area","").strip()
    state         = request.args.get("state","").strip()
    cat           = request.args.get("category","").strip()
    ph            = request.args.get("has_phone","all")
    page          = max(1,int(request.args.get("page",1)))
    all_st        = request.args.get("all_statuses","0") == "1"
    unassigned_only = request.args.get("unassigned_only","1") == "1"  # default: show only unassigned

    ck = f"leads:{area}:{state}:{cat}:{ph}:{page}:{all_st}:{unassigned_only}"
    cached = cache_get(ck)
    if cached: return jsonify(cached)

    areas_list   = None
    use_in_query = False
    if state and state in AREA_GROUPS:
        areas_list   = AREA_GROUPS[state]
        use_in_query = True
    elif area:
        areas_list = [area]

    try:
        def _fetch(query):
            offset    = (page-1)*PAGE_SIZE
            docs      = list(query.limit(PAGE_SIZE+1+offset).get())
            page_docs = docs[offset:]
            has_more  = len(page_docs) > PAGE_SIZE
            return page_docs[:PAGE_SIZE], has_more

        col = db.collection(RESEARCHER_LEADS_PATH)

        # Apply unassigned filter first if requested
        if unassigned_only:
            col = col.where(filter=FieldFilter("assigned_to","==",None))

        if ph == "no" or not ph or ph == "all":
            # All leads here are no-phone by design
            if use_in_query and areas_list:
                raw_leads, has_more = [], False
                for i in range(0,len(areas_list),30):
                    batch = areas_list[i:i+30]
                    q = col.where(filter=FieldFilter("area","in",batch))
                    if cat: q = q.where(filter=FieldFilter("keyword","==",cat))
                    bl, hm = _fetch(q)
                    raw_leads.extend(bl)
                    if hm: has_more = True
            else:
                q = col
                if areas_list:
                    q = q.where(filter=FieldFilter("area","==",areas_list[0]))
                if cat:
                    q = q.where(filter=FieldFilter("keyword","==",cat))
                raw_leads, has_more = _fetch(q)
        else:
            raw_leads, has_more = [], False

        # ── FIX: serialize before caching ──
        leads_serialized = [_enrich(d.id, d.to_dict()) for d in raw_leads]

        result = {"leads": leads_serialized, "page": page, "has_more": has_more, "page_size": PAGE_SIZE}
        cache_set(ck, result, ttl=120)
        return jsonify(result)
    except ResourceExhausted:
        return _quota_err()


@app.route("/api/admin/lead-filters", methods=["GET"])
def get_lead_filters():
    r = _require_role("admin","super_admin")
    if r: return r
    cached = cache_get("lead_filters")
    if cached: return jsonify(cached)
    try:
        # Read filters from new researcher leads path
        docs = db.collection(RESEARCHER_LEADS_PATH).select(["area","keyword"]).get()
        areas, cats = set(), set()
        for d in docs:
            dat = d.to_dict()
            if dat.get("area"):   areas.add(dat["area"])
            if dat.get("keyword"): cats.add(dat["keyword"])
        result = {"areas":sorted(areas),"categories":sorted(cats)}
        cache_set("lead_filters",result,ttl=1800)
        return jsonify(result)
    except ResourceExhausted:
        return _quota_err()

# ═══════════════════════════════════════════════════════════════
# NEXT LEAD — from RESEARCHER_LEADS_PATH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/next-lead", methods=["GET"])
def get_next_lead():
    caller      = request.args.get("caller","").strip()
    specific_id = request.args.get("id","").strip()
    if not caller: return _err("Missing caller")
    try:
        if specific_id:
            ref = db.document(f"{RESEARCHER_LEADS_PATH}/{specific_id}")
            doc = ref.get()
            if doc.exists:
                d = doc.to_dict()
                if d.get("assigned_to") == caller:
                    if d.get("status") != "calling":
                        ref.update({"status":"calling"})
                    return jsonify(_enrich(doc.id,d))

        in_prog = (db.collection(RESEARCHER_LEADS_PATH)
                     .where(filter=FieldFilter("assigned_to","==",caller))
                     .where(filter=FieldFilter("status","==","calling")).limit(1).get())
        if in_prog:
            return jsonify(_enrich(in_prog[0].id,in_prog[0].to_dict()))

        pre_refs = list(
            db.collection(RESEARCHER_LEADS_PATH)
            .where(filter=FieldFilter("assigned_to","==",caller))
            .where(filter=FieldFilter("status","==","new")).limit(1).get()
        )
        if pre_refs:
            ref = pre_refs[0].reference
            @firestore.transactional
            def _claim(transaction,ref,caller):
                snap = ref.get(transaction=transaction)
                if not snap.exists: return None
                data = snap.to_dict()
                if data.get("assigned_to") != caller or data.get("status") != "new":
                    return None
                transaction.update(ref,{"status":"calling"})
                return _enrich(snap.id,data)
            result = _claim(db.transaction(),ref,caller)
            if result: return jsonify(result)

        return jsonify({"error":"Queue Empty"}),404
    except Exception as e:
        return _err(str(e),500)

# ═══════════════════════════════════════════════════════════════
# SUBMIT CALL — updates RESEARCHER_LEADS_PATH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/submit-call", methods=["POST"])
def submit_call():
    try:
        d        = request.json or {}
        lead_id  = str(d.get("id","")).strip()
        caller   = str(d.get("caller","")).strip()
        stat     = str(d.get("status","")).strip()
        remark   = str(d.get("remarks","")).strip()
        duration = int(d.get("duration",0))
        idem     = str(d.get("idempotency_key","")).strip()

        if not lead_id: return _err("Missing lead id")
        if not caller:  return _err("Missing caller")
        if stat not in VALID_DISPOSITIONS: return _err(f"Invalid status '{stat}'")
        if stat in ("interested","callback") and not remark:
            return _err("Remarks required for this outcome")
        if stat == "callback" and not str(d.get("callback_time","")).strip():
            return _err("callback_time required")
        if duration < 0 or duration > 86400: duration = 0

        if idem:
            existing = list(db.collection(LOGS_PATH)
                .where(filter=FieldFilter("idempotency_key","==",idem)).limit(1).get())
            if existing:
                return jsonify({"status":"success","duplicate":True})

        lref = db.document(f"{RESEARCHER_LEADS_PATH}/{lead_id}")
        ldat = lref.get().to_dict()
        if not ldat: return _err("Lead not found",404)

        gmb_link = _gmb(ldat)
        upd = {"disposition":stat,"remarks":remark,"updated_at":firestore.SERVER_TIMESTAMP}

        if stat == "not_interested": upd["status"] = "completed"
        elif stat == "not_picked":
            attempts = ldat.get("not_picked_count",0)+1
            upd["not_picked_count"] = attempts
            upd["status"] = "completed" if attempts >= 2 else "new"
            if attempts < 2: upd["assigned_to"] = None
        elif stat == "callback":
            upd["status"]        = "callback"
            upd["callback_time"] = d.get("callback_time")
            upd["assigned_to"]   = caller
        else:
            upd["status"] = "completed"

        if stat == "interested": upd["pipeline_status"] = "Interested"
        lref.update(upd)

        log_entry = {
            "action":"call_submission","lead_id":lead_id,
            "lead_name":ldat.get("name"),"lead_phone":ldat.get("phone"),
            "gmb_link":gmb_link,"done_by":caller,
            "disposition":stat,"remark":remark,"duration":duration,
            "timestamp":firestore.SERVER_TIMESTAMP,"date":str(date.today())
        }
        if idem: log_entry["idempotency_key"] = idem
        db.collection(LOGS_PATH).add(log_entry)

        cache_bust("global_stats")
        cache_bust("counts_only")
        cache_bust("leads:")
        cache_bust("staff_summary")
        cache_bust("staff_pending")
        return jsonify({"status":"success"})
    except Exception as e:
        return _err(str(e),500)

# ═══════════════════════════════════════════════════════════════
# RESEARCHER — reads/writes RESEARCHER_LEADS_PATH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/researcher/my-leads", methods=["GET"])
def get_researcher_leads():
    r = _require_role("researcher","admin","super_admin")
    if r: return r
    researcher = request.args.get("researcher","").strip()
    if not researcher: return _err("Missing researcher")
    docs = (db.collection(RESEARCHER_LEADS_PATH)
              .where(filter=FieldFilter("assigned_to","==",researcher))
              .where(filter=FieldFilter("status","in",["new","calling"]))
              .limit(200).get())
    return jsonify([_enrich(d.id,d.to_dict()) for d in docs])


@app.route("/api/researcher/completed-leads", methods=["GET"])
def get_researcher_completed():
    r = _require_role("researcher","admin","super_admin")
    if r: return r
    researcher = request.args.get("researcher","").strip()
    if not researcher: return _err("Missing researcher")
    docs = (db.collection(RESEARCHER_LEADS_PATH)
              .where(filter=FieldFilter("research_completed_by","==",researcher))
              .where(filter=FieldFilter("status","==","research_done"))
              .limit(200).get())
    return jsonify([_enrich(d.id,d.to_dict()) for d in docs])


@app.route("/api/updater/update-lead", methods=["POST"])
def update_missing_phone():
    r = _require_role("researcher","admin","super_admin")
    if r: return r
    data  = request.json or {}
    lid   = str(data.get("id","")).strip()
    phone = str(data.get("phone","")).strip()
    uname = str(data.get("username","")).strip()
    if not lid:   return _err("Missing id")
    if not uname: return _err("Missing username")
    if phone != "UNRESOLVABLE" and not _ok_phone(phone):
        return _err("Invalid phone number")

    if phone == "UNRESOLVABLE":
        payload = {
            "phone": "UNRESOLVABLE",
            "research_at": firestore.SERVER_TIMESTAMP,
            "status": "research_done",
            "assigned_to": None,
            "research_completed_by": uname,
        }
    else:
        payload = {
            "phone":phone,"is_researched":True,
            "research_completed_by":uname,
            "research_at":firestore.SERVER_TIMESTAMP,
            "status":"research_done","assigned_to":None
        }
    db.document(f"{RESEARCHER_LEADS_PATH}/{lid}").update(payload)
    db.collection(LOGS_PATH).add({
        "action":"phone_update","lead_id":lid,"done_by":uname,
        "details":f"phone={phone}",
        "timestamp":firestore.SERVER_TIMESTAMP,"date":str(date.today())
    })
    cache_bust("global_stats")
    cache_bust("counts_only")
    cache_bust("leads:")
    return jsonify({"status":"updated"})


@app.route("/api/researcher/batch-assign", methods=["POST"])
def researcher_batch_assign():
    r = _require_role("researcher","admin","super_admin")
    if r: return r
    data       = request.json or {}
    lead_ids   = data.get("lead_ids",[])
    target     = str(data.get("target_user","")).strip()
    researcher = str(data.get("researcher","")).strip()
    if not lead_ids:  return _err("No leads selected")
    if not target:    return _err("Missing target_user")
    if not researcher: return _err("Missing researcher")
    if len(lead_ids) > 200: return _err("Max 200 per batch")

    batch = db.batch()
    for lid in lead_ids:
        ref = db.document(f"{RESEARCHER_LEADS_PATH}/{lid}")
        batch.update(ref,{
            "assigned_to":target,"status":"new",
            "batch_assigned_by":researcher,
            "batch_assigned_at":firestore.SERVER_TIMESTAMP
        })
    batch.commit()
    db.collection(LOGS_PATH).add({
        "action":"batch_assign","done_by":researcher,
        "details":f"assigned {len(lead_ids)} leads to {target}",
        "timestamp":firestore.SERVER_TIMESTAMP,"date":str(date.today())
    })
    cache_bust("leads:")
    cache_bust("global_stats")
    cache_bust("counts_only")
    return jsonify({"status":"success","assigned":len(lead_ids)})

@app.route("/api/admin/user-assigned-leads", methods=["GET"])
def get_user_assigned_leads():
    r = _require_role("admin","super_admin")
    if r: return r
    username = request.args.get("user","").strip()
    if not username: return _err("Missing user")
    ck = f"user_assigned:{username}"
    cached = cache_get(ck)
    if cached: return jsonify(cached)
    try:
        docs = (db.collection(RESEARCHER_LEADS_PATH)
                  .where(filter=FieldFilter("assigned_to","==",username))
                  .where(filter=FieldFilter("status","in",["new","calling","callback"]))
                  .limit(200).get())
        result = [_enrich(d.id, d.to_dict()) for d in docs]
        cache_set(ck, result, ttl=30)
        return jsonify(result)
    except ResourceExhausted:
        return _quota_err()
    except Exception as e:
        return _err(str(e), 500)

# ═══════════════════════════════════════════════════════════════
# CALLER ASSIGNED LEADS — from RESEARCHER_LEADS_PATH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/caller-assigned-leads", methods=["GET"])
def get_caller_assigned_leads():
    caller = request.args.get("caller","").strip()
    if not caller: return _err("Missing caller")
    docs = (db.collection(RESEARCHER_LEADS_PATH)
              .where(filter=FieldFilter("assigned_to","==",caller))
              .limit(200).get())
    leads = []
    for d in docs:
        l = _enrich(d.id,d.to_dict())
        for ts_field in ("updated_at","claimed_at"):
            v = l.get(ts_field)
            if v and hasattr(v,"strftime"):
                ist = _to_ist(v)
                l[ts_field] = ist.strftime("%d %b %Y, %I:%M %p") if ist else None
        leads.append(l)
    status_order = {"calling":0,"callback":1,"new":2,"completed":3}
    leads.sort(key=lambda x: status_order.get(x.get("status",""),9))
    return jsonify(leads)


@app.route("/api/caller-callbacks", methods=["GET"])
def get_callbacks():
    caller = request.args.get("caller","").strip()
    if not caller: return _err("Missing caller")
    docs = (db.collection(RESEARCHER_LEADS_PATH)
              .where(filter=FieldFilter("assigned_to","==",caller))
              .where(filter=FieldFilter("status","==","callback")).get())
    return jsonify([{"id":d.id,**d.to_dict()} for d in docs])

# ═══════════════════════════════════════════════════════════════
# STAFF STATS (unchanged — still reads from LOGS_PATH)
# ═══════════════════════════════════════════════════════════════
@app.route("/api/admin/staff-full-stats", methods=["GET"])
def get_staff_full_stats():
    r = _require_role("admin","super_admin")
    if r: return r
    username = request.args.get("user","").strip()
    tdate    = request.args.get("date",str(date.today()))
    if not username:      return _err("Missing user")
    if not _ok_date(tdate): return _err("Invalid date format (YYYY-MM-DD)")
    ck = f"staff_stats:{username}:{tdate}"
    cached = cache_get(ck)
    if cached: return jsonify(cached)
    try:
        now = datetime.now()
        ninety_days_ago = (datetime.utcnow()-timedelta(days=90)).strftime("%Y-%m-%d")
        try:
            logs = (db.collection(LOGS_PATH)
                      .where(filter=FieldFilter("done_by","==",username))
                      .where(filter=FieldFilter("date",">=",ninety_days_ago)).get())
        except Exception:
            logs = (db.collection(LOGS_PATH)
                      .where(filter=FieldFilter("done_by","==",username)).get())
        raw_logs = []
        for l in logs:
            try:
                d = l.to_dict()
                if d.get("timestamp"): raw_logs.append(d)
            except Exception: pass
        try:
            ldata = sorted(raw_logs,key=lambda x: x.get("timestamp",datetime.min))
        except Exception:
            ldata = raw_logs

        timeline, amap = [], {}
        stats = {
            "m_calls":0,"m_int":0,"life_calls":0,"life_int":0,
            "life_completed":0,"daily_activity":[],"today_research":0
        }
        login_t = logout_t = None

        for d in ldata:
            ts_utc = d.get("timestamp")
            ts_ist = _to_ist(ts_utc)
            action = d.get("action")
            ldate  = d.get("date","Unknown")
            is_int = d.get("disposition") == "interested"

            if ldate == tdate:
                tstr = ts_ist.strftime("%I:%M %p") if ts_ist else "N/A"
                if action == "login" and not login_t:    login_t  = tstr
                elif action == "logout":                  logout_t = tstr
                elif action == "call_submission":
                    timeline.append({
                        "time":tstr,"name":d.get("lead_name","N/A"),
                        "phone":d.get("lead_phone","--"),
                        "link":d.get("gmb_link","#"),
                        "status":d.get("disposition","Completed"),
                        "remark":d.get("remark","--"),"duration":d.get("duration",0)
                    })
                elif action == "phone_update":
                    stats["today_research"] += 1

            if action == "call_submission":
                stats["life_calls"]     += 1
                stats["life_completed"] += 1
                if is_int: stats["life_int"] += 1
                if ts_utc and hasattr(ts_utc,"month") and ts_utc.year==now.year and ts_utc.month==now.month:
                    stats["m_calls"] += 1
                    if is_int: stats["m_int"] += 1
                    if ldate != "Unknown": amap[ldate] = amap.get(ldate,0)+1

        stats["daily_activity"] = [
            {"date":k,"calls":v} for k,v in sorted(amap.items()) if k != "Unknown"
        ]

        try:
            sess_doc = db.collection(SESSIONS_PATH).document(username).get()
            if sess_doc.exists:
                sd = sess_doc.to_dict()
                stats["session_seconds"] = sd.get("session_seconds",0)
                stats["session_active"]  = sd.get("active",False)
            else:
                stats["session_seconds"] = 0
                stats["session_active"]  = False
        except Exception:
            stats["session_seconds"] = 0
            stats["session_active"]  = False

        result = {"login":login_t,"logout":logout_t,"timeline":timeline,"stats":stats}
        ttl = 60 if tdate == str(date.today()) else 600
        cache_set(ck,result,ttl=ttl)
        return jsonify(result)
    except Exception as e:
        return _err(str(e),500)


@app.route("/api/admin/staff-pending-counts", methods=["GET"])
def get_staff_pending_counts():
    r = _require_role("admin","super_admin")
    if r: return r
    cached = cache_get("staff_pending")
    if cached: return jsonify(cached)
    try:
        users_list = cache_get("users_list")
        if not users_list:
            docs = db.collection(USERS_PATH).get()
            users_list = [{"id":d.id,**d.to_dict()} for d in docs]
            cache_set("users_list",users_list,ttl=60)

        counts,cb_counts,new_counts,res_counts = {},{},{},{}
        use_counts = True

        for u in users_list:
            uname = u.get("username")
            if not uname or u.get("role") == "super_admin": continue
            try:
                c_col   = db.collection(RESEARCHER_LEADS_PATH)
                calling = c_col.where(filter=FieldFilter("assigned_to","==",uname)).where(filter=FieldFilter("status","==","calling")).count().get()[0][0].value
                cb      = c_col.where(filter=FieldFilter("assigned_to","==",uname)).where(filter=FieldFilter("status","==","callback")).count().get()[0][0].value
                nw      = c_col.where(filter=FieldFilter("assigned_to","==",uname)).where(filter=FieldFilter("status","==","new")).count().get()[0][0].value
                noph    = c_col.where(filter=FieldFilter("assigned_to","==",uname)).where(filter=FieldFilter("status","in",["new","calling"])).count().get()[0][0].value
                total_active = calling+cb+nw
                if total_active > 0: counts[uname]    = total_active
                if cb > 0:           cb_counts[uname] = cb
                if nw > 0:           new_counts[uname] = nw
                if noph > 0:         res_counts[uname] = noph
            except Exception:
                use_counts = False
                break

        if not use_counts:
            docs = db.collection(RESEARCHER_LEADS_PATH).where(filter=FieldFilter("assigned_to","!=",None)).get()
            for d in docs:
                dat      = d.to_dict()
                status   = dat.get("status","")
                assignee = dat.get("assigned_to")
                if not assignee: continue
                # Count as researcher pending if status is new/calling (regardless of phone value)
                if status in ("new","calling") and dat.get("phone") in ("NO", None):
                    res_counts[assignee] = res_counts.get(assignee,0)+1
                if status in ("calling","callback","new"):
                    counts[assignee] = counts.get(assignee,0)+1
                if status == "callback":  cb_counts[assignee]  = cb_counts.get(assignee,0)+1
                if status == "new":       new_counts[assignee] = new_counts.get(assignee,0)+1
            for name,cnt in res_counts.items():
                if name not in counts: counts[name] = cnt

        result = {"pending":counts,"callbacks":cb_counts,"new_leads":new_counts,"researcher_pending":res_counts}
        cache_set("staff_pending",result,ttl=120)
        return jsonify(result)
    except ResourceExhausted:
        return _quota_err()
    except Exception as e:
        return _err(str(e),500)


@app.route("/api/admin/staff-summary", methods=["GET"])
def get_staff_summary():
    r = _require_role("admin","super_admin")
    if r: return r
    cached = cache_get("staff_summary")
    if cached: return jsonify(cached)
    today   = str(date.today())
    users   = db.collection(USERS_PATH).get()
    summary = {}
    for u in users:
        ud    = u.to_dict()
        uname = ud.get("username","Unknown")
        if ud.get("role") == "super_admin": continue
        cur = (db.collection(RESEARCHER_LEADS_PATH)
                 .where(filter=FieldFilter("assigned_to","==",uname))
                 .where(filter=FieldFilter("status","==","calling")).limit(1).get())
        summary[uname] = {
            "role":ud.get("role"),
            "live_status":cur[0].to_dict().get("name","Idle") if cur else "Idle",
            "today_calls":0,"today_research":0
        }
    for log in db.collection(LOGS_PATH).where(filter=FieldFilter("date","==",today)).get():
        d = log.to_dict()
        u = d.get("done_by")
        if u in summary:
            if d.get("action") == "call_submission": summary[u]["today_calls"]    += 1
            elif d.get("action") == "phone_update":  summary[u]["today_research"] += 1
    try:
        for sd in db.collection(SESSIONS_PATH).get():
            uname = sd.id
            if uname in summary:
                sdat = sd.to_dict()
                summary[uname]["session_seconds"] = sdat.get("session_seconds",0)
                summary[uname]["session_active"]  = sdat.get("active",False)
    except Exception: pass
    cache_set("staff_summary",summary,ttl=60)
    return jsonify(summary)


@app.route("/api/admin/transfer-stats", methods=["GET"])
def get_transfer_stats():
    r = _require_role("admin","super_admin")
    if r: return r
    cached = cache_get("transfer_stats")
    if cached: return jsonify(cached)
    logs = (db.collection(LOGS_PATH)
              .where(filter=FieldFilter("action","==","call_submission"))
              .where(filter=FieldFilter("disposition","==","interested")).get())
    stats = {}
    for log in logs:
        d      = log.to_dict()
        caller = d.get("done_by","Unknown")
        if caller not in stats: stats[caller] = {"total":0,"leads":[]}
        stats[caller]["total"] += 1
        stats[caller]["leads"].append({
            "name":d.get("lead_name","–"),"phone":d.get("lead_phone","–"),
            "date":d.get("date","–"),"remark":d.get("remark","–")
        })
    cache_set("transfer_stats",stats,ttl=300)
    return jsonify(stats)

# ═══════════════════════════════════════════════════════════════
# USER MANAGEMENT (unchanged)
# ═══════════════════════════════════════════════════════════════
@app.route("/api/admin/users", methods=["GET"])
def get_admin_users():
    r = _require_role("admin","super_admin")
    if r: return r
    cached = cache_get("users_list")
    if cached: return jsonify(cached)
    docs = db.collection(USERS_PATH).get()
    safe = []
    for d in docs:
        row = {"id":d.id,**d.to_dict()}
        row.pop("password",None)
        safe.append(row)
    cache_set("users_list",safe,ttl=60)
    return jsonify(safe)


@app.route("/api/admin/create-user", methods=["POST"])
def create_user():
    r = _require_role("admin","super_admin")
    if r: return r
    data  = request.json or {}
    uname = str(data.get("username","")).strip()
    pw    = str(data.get("password","")).strip()
    role  = str(data.get("role","telecaller")).strip()
    if not USERNAME_RE.match(uname): return _err("Username must be 3-50 chars, letters/numbers/underscore only")
    if not _ok_str(pw,6,100):        return _err("Password must be at least 6 characters")
    if role not in VALID_ROLES:      return _err(f"Invalid role")
    if db.collection(USERS_PATH).where(filter=FieldFilter("username","==",uname)).limit(1).get():
        return _err("Username already exists",409)
    db.collection(USERS_PATH).add({
        "username":uname,"password":generate_password_hash(pw),
        "role":role,"created_at":firestore.SERVER_TIMESTAMP
    })
    cache_bust("users_list")
    cache_bust(f"user:{uname}")
    return jsonify({"status":"created"})


@app.route("/api/admin/delete-user/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    r = _require_role("admin","super_admin")
    if r: return r
    if not user_id: return _err("Missing user id")
    try:
        db.collection(USERS_PATH).document(user_id).delete()
        cache_bust("users_list")
        cache_bust("user:")
        return jsonify({"status":"deleted"})
    except Exception as e:
        return _err(str(e),500)

# ═══════════════════════════════════════════════════════════════
# PIPELINE — from RESEARCHER_LEADS_PATH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/admin/interested-leads", methods=["GET"])
def get_interested_leads():
    cached = cache_get("interested_leads")
    if cached: return jsonify(cached)
    docs   = db.collection(RESEARCHER_LEADS_PATH).where(filter=FieldFilter("disposition","==","interested")).get()
    result = [{"id":d.id,**d.to_dict()} for d in docs]
    cache_set("interested_leads",result,ttl=120)
    return jsonify(result)


@app.route("/api/admin/update-pipeline", methods=["POST"])
def update_pipeline():
    r = _require_role("admin","super_admin")
    if r: return r
    data   = request.json or {}
    lid    = str(data.get("id","")).strip()
    ps     = str(data.get("pipeline_status") or "").strip()
    ar     = str(data.get("admin_remarks") or "").strip()
    remove = bool(data.get("remove_from_pipeline",False))
    if not lid: return _err("Missing id")
    if ps and ps not in VALID_PIPELINE: return _err(f"Invalid pipeline_status")
    upd = {"pipeline_updated_at":firestore.SERVER_TIMESTAMP}
    if ps:     upd["pipeline_status"] = ps
    if ar:     upd["admin_remarks"]   = ar
    if remove:
        upd["disposition"]     = "not_interested"
        upd["pipeline_status"] = "Closed"
        upd["status"]          = "completed"
    db.document(f"{RESEARCHER_LEADS_PATH}/{lid}").update(upd)
    cache_bust("interested_leads")
    cache_bust("global_stats")
    cache_bust("counts_only")
    cache_bust("leads:")
    return jsonify({"status":"success"})


@app.route("/api/admin/bulk-assign", methods=["POST"])
def bulk_assign():
    r = _require_role("admin","super_admin")
    if r: return r
    data     = request.json or {}
    lead_ids = data.get("lead_ids",[])
    target   = str(data.get("target_user","")).strip()
    if not lead_ids:       return _err("No leads selected")
    if len(lead_ids) > 500: return _err("Max 500 per bulk op")
    for i in range(0,len(lead_ids),499):
        chunk = lead_ids[i:i+499]
        batch = db.batch()
        for lid in chunk:
            ref = db.document(f"{RESEARCHER_LEADS_PATH}/{lid}")
            if target == "POOL":
                batch.update(ref,{"assigned_to":None,"status":"new"})
            else:
                batch.update(ref,{"assigned_to":target,"status":"new"})
        batch.commit()
    cache_bust("leads:")
    cache_bust("global_stats")
    cache_bust("counts_only")
    cache_bust("staff_pending")
    return jsonify({"status":"success","updated":len(lead_ids)})


@app.route("/api/admin/bulk-delete", methods=["POST"])
def bulk_delete():
    r = _require_role("admin","super_admin")
    if r: return r
    lead_ids = (request.json or {}).get("lead_ids",[])
    if not lead_ids:       return _err("No leads selected")
    if len(lead_ids) > 500: return _err("Max 500 per bulk op")
    for i in range(0,len(lead_ids),499):
        chunk = lead_ids[i:i+499]
        batch = db.batch()
        for lid in chunk:
            batch.delete(db.document(f"{RESEARCHER_LEADS_PATH}/{lid}"))
        batch.commit()
    cache_bust("leads:")
    cache_bust("global_stats")
    cache_bust("counts_only")
    return jsonify({"status":"success","deleted":len(lead_ids)})

# ═══════════════════════════════════════════════════════════════
# NOT PICKED — from RESEARCHER_LEADS_PATH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/not-picked-leads", methods=["GET"])
def get_caller_not_picked():
    docs = (db.collection(RESEARCHER_LEADS_PATH)
              .where(filter=FieldFilter("disposition","==","not_picked"))
              .where(filter=FieldFilter("status","==","new")).limit(200).get())
    result = []
    for d in docs:
        l = _enrich(d.id,d.to_dict())
        v = l.get("updated_at")
        if v and hasattr(v,"strftime"):
            ist = _to_ist(v)
            l["updated_at"] = ist.strftime("%d %b %Y, %I:%M %p") if ist else str(v)
        result.append(l)
    result.sort(key=lambda x: x.get("not_picked_count",0),reverse=True)
    return jsonify(result)


@app.route("/api/admin/not-picked-leads", methods=["GET"])
def get_not_picked_leads():
    r = _require_role("admin","super_admin")
    if r: return r
    cached = cache_get("not_picked_leads")
    if cached: return jsonify(cached)
    try:
        docs = (db.collection(RESEARCHER_LEADS_PATH)
                  .where(filter=FieldFilter("disposition","==","not_picked"))
                  .where(filter=FieldFilter("status","==","new")).limit(300).get())
        result = [_enrich(d.id,d.to_dict()) for d in docs]
        result.sort(key=lambda x: x.get("not_picked_count",0),reverse=True)
        cache_set("not_picked_leads",result,ttl=60)
        return jsonify(result)
    except Exception as e:
        return _err(str(e),500)


@app.route("/api/admin/reassign-lead", methods=["POST"])
def reassign_lead():
    r = _require_role("admin","super_admin")
    if r: return r
    data   = request.json or {}
    lid    = str(data.get("id","")).strip()
    target = str(data.get("target","")).strip()
    if not lid:    return _err("Missing lead id")
    if not target: return _err("Missing target user")
    try:
        db.document(f"{RESEARCHER_LEADS_PATH}/{lid}").update({
            "assigned_to":target,"status":"new",
            "reassigned_at":firestore.SERVER_TIMESTAMP,
        })
        db.collection(LOGS_PATH).add({
            "action":"reassign","lead_id":lid,"done_by":"admin",
            "details":f"reassigned to {target}",
            "timestamp":firestore.SERVER_TIMESTAMP,"date":str(date.today())
        })
        cache_bust("not_picked_leads")
        cache_bust("leads:")
        cache_bust("staff_pending")
        return jsonify({"status":"ok"})
    except Exception as e:
        return _err(str(e),500)

# ═══════════════════════════════════════════════════════════════
# SCRAPER STATUS (unchanged)
# ═══════════════════════════════════════════════════════════════
@app.route("/api/admin/scraper-status", methods=["GET"])
def get_scraper_status():
    cached = cache_get("scraper_status")
    if cached: return jsonify(cached)
    try:
        doc    = db.collection("meta").document("scraper_status").get()
        result = doc.to_dict().get("scripts",[]) if doc.exists else []
        cache_set("scraper_status",result,ttl=30)
        return jsonify(result)
    except Exception:
        return jsonify([])


@app.route("/api/admin/scraper-status", methods=["POST"])
def update_scraper_status():
    r = _require_role("admin","super_admin")
    if r: return r
    data = request.json or {}
    for k in ["vm","city","category","status"]:
        if not data.get(k): return _err(f"Missing required field: {k}")
    entry = {
        "vm":str(data.get("vm",""))[:50],"city":str(data.get("city",""))[:100],
        "category":str(data.get("category",""))[:100],"status":str(data.get("status","running"))[:20],
        "area_index":int(data.get("area_index",0)),"keyword_index":int(data.get("keyword_index",0)),
        "leads_found":int(data.get("leads_found",0)),"script":str(data.get("script",""))[:50],
        "updated":datetime.utcnow().isoformat()
    }
    try:
        meta_ref = db.collection("meta").document("scraper_status")
        meta_doc = meta_ref.get()
        scripts  = meta_doc.to_dict().get("scripts",[]) if meta_doc.exists else []
        idx = next((i for i,s in enumerate(scripts)
                    if s.get("vm")==entry["vm"] and s.get("city")==entry["city"]
                    and s.get("category")==entry["category"]),None)
        if idx is not None: scripts[idx] = entry
        else:               scripts.append(entry)
        meta_ref.set({"scripts":scripts})
        cache_bust("scraper_status")
        return jsonify({"status":"updated"})
    except ResourceExhausted:
        return _quota_err()

# ═══════════════════════════════════════════════════════════════
# MY CALLS / EDIT (unchanged)
# ═══════════════════════════════════════════════════════════════
@app.route("/api/my-calls", methods=["GET"])
def get_my_calls():
    user = request.args.get("user","").strip()
    if not user: return _err("Missing user")
    ck = f"my_calls:{user}"
    cached = cache_get(ck)
    if cached: return jsonify(cached)
    try:
        logs = (db.collection(LOGS_PATH)
                  .where(filter=FieldFilter("done_by","==",user))
                  .where(filter=FieldFilter("action","==","call_submission")).get())
        result = []
        for l in logs:
            d      = l.to_dict()
            ts     = d.get("timestamp")
            ts_ist = _to_ist(ts) if ts else None
            result.append({
                "id":l.id,"lead_name":d.get("lead_name","–"),
                "lead_phone":d.get("lead_phone","–"),
                "status":d.get("disposition","–"),
                "remarks":d.get("remark","–"),"duration":d.get("duration",0),
                "date":d.get("date","–"),
                "timestamp_str":ts_ist.strftime("%d %b %Y, %I:%M %p") if ts_ist else "–",
                "gmb_link":d.get("gmb_link",""),
            })
        result.sort(key=lambda x: x.get("date",""),reverse=True)
        cache_set(ck,result,ttl=60)
        return jsonify(result)
    except Exception as e:
        return _err(str(e),500)


@app.route("/api/update-call/<log_id>", methods=["POST"])
def update_call(log_id):
    data    = request.json or {}
    status  = str(data.get("status","")).strip()
    remarks = str(data.get("remarks","")).strip()
    user    = str(data.get("user","")).strip()
    if not log_id or not user: return _err("Missing log_id or user")
    try:
        upd = {"updated_at":firestore.SERVER_TIMESTAMP}
        if status:  upd["disposition"] = status
        if remarks: upd["remark"]      = remarks
        db.collection(LOGS_PATH).document(log_id).update(upd)
        cache_bust(f"my_calls:{user}")
        return jsonify({"status":"updated"})
    except Exception as e:
        return _err(str(e),500)

# ═══════════════════════════════════════════════════════════════
# SESSION TRACKING (unchanged)
# ═══════════════════════════════════════════════════════════════
@app.route("/api/session/start", methods=["POST"])
def session_start():
    data = request.json or {}
    user = str(data.get("username","")).strip()
    if not user: return _err("Missing username")
    db.collection(SESSIONS_PATH).document(user).set({
        "username":user,"login_at":firestore.SERVER_TIMESTAMP,
        "login_at_iso":datetime.utcnow().isoformat(),
        "last_heartbeat":firestore.SERVER_TIMESTAMP,
        "session_seconds":0,"active":True
    })
    return jsonify({"status":"started"})


@app.route("/api/session/heartbeat", methods=["POST"])
def session_heartbeat():
    data    = request.json or {}
    user    = str(data.get("username","")).strip()
    elapsed = int(data.get("elapsed_seconds",0))
    if not user: return _err("Missing username")
    db.collection(SESSIONS_PATH).document(user).set(
        {"last_heartbeat":firestore.SERVER_TIMESTAMP,"session_seconds":elapsed,"active":True},
        merge=True
    )
    return jsonify({"status":"ok"})


@app.route("/api/session/end", methods=["POST"])
def session_end():
    data    = request.json or {}
    user    = str(data.get("username","")).strip()
    elapsed = int(data.get("elapsed_seconds",0))
    if not user: return _err("Missing username")
    db.collection(SESSIONS_PATH).document(user).set(
        {"last_heartbeat":firestore.SERVER_TIMESTAMP,"session_seconds":elapsed,
         "active":False,"logout_at":firestore.SERVER_TIMESTAMP},
        merge=True
    )
    return jsonify({"status":"ended"})


@app.route("/api/admin/sessions", methods=["GET"])
def get_all_sessions():
    r = _require_role("admin","super_admin")
    if r: return r
    cached = cache_get("all_sessions")
    if cached: return jsonify(cached)
    try:
        docs   = db.collection(SESSIONS_PATH).get()
        result = {}
        for d in docs:
            dat = d.to_dict()
            result[d.id] = {
                "active":dat.get("active",False),
                "session_seconds":dat.get("session_seconds",0),
                "login_at_iso":dat.get("login_at_iso",""),
            }
        cache_set("all_sessions",result,ttl=15)
        return jsonify(result)
    except Exception as e:
        return _err(str(e),500)

# ═══════════════════════════════════════════════════════════════
# CHAT (unchanged)
# ═══════════════════════════════════════════════════════════════
@app.route("/api/chat/<lead_id>/messages", methods=["GET"])
def get_chat_messages(lead_id):
    if not lead_id: return _err("Missing lead_id")
    ck = f"chat:{lead_id}"
    cached = cache_get(ck)
    if cached: return jsonify(cached)
    try:
        msgs = (db.collection(CHATS_PATH).document(lead_id)
                  .collection("messages").order_by("timestamp").limit(200).get())
        result = []
        for m in msgs:
            d      = m.to_dict()
            ts     = d.get("timestamp")
            ts_ist = _to_ist(ts) if ts else None
            result.append({
                "id":m.id,"sender":d.get("sender",""),
                "role":d.get("role",""),"text":d.get("text",""),
                "time":ts_ist.strftime("%d %b, %I:%M %p") if ts_ist else "–"
            })
        cache_set(ck,result,ttl=10)
        return jsonify(result)
    except Exception as e:
        return _err(str(e),500)


@app.route("/api/chat/<lead_id>/send", methods=["POST"])
def send_chat_message(lead_id):
    if not lead_id: return _err("Missing lead_id")
    data   = request.json or {}
    sender = str(data.get("sender","")).strip()
    role   = str(data.get("role","")).strip()
    text   = str(data.get("text","")).strip()
    if not sender: return _err("Missing sender")
    if not text:   return _err("Missing text")
    if not role:   return _err("Missing role")
    if len(text) > 1000: return _err("Message too long")
    db.collection(CHATS_PATH).document(lead_id).collection("messages").add({
        "sender":sender,"role":role,"text":text,"timestamp":firestore.SERVER_TIMESTAMP
    })
    db.collection(CHATS_PATH).document(lead_id).set({
        "last_message":text[:80],"last_sender":sender,
        "updated_at":firestore.SERVER_TIMESTAMP,"lead_id":lead_id
    },merge=True)
    cache_bust(f"chat:{lead_id}")
    return jsonify({"status":"sent"})


@app.route("/api/chat/unread-counts", methods=["GET"])
def get_unread_counts():
    user = request.args.get("user","").strip()
    if not user: return _err("Missing user")
    ck = f"chat_unread:{user}"
    cached = cache_get(ck)
    if cached: return jsonify(cached)
    try:
        docs   = db.collection(CHATS_PATH).limit(100).get()
        result = {}
        for d in docs:
            dat = d.to_dict()
            result[d.id] = {"last_message":dat.get("last_message",""),"last_sender":dat.get("last_sender","")}
        cache_set(ck,result,ttl=15)
        return jsonify(result)
    except Exception as e:
        return _err(str(e),500)

# ═══════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status":"ok","time":str(datetime.now()),
        "cache_entries":len(_cache),"auth_sessions":len(_auth_store),
        "researcher_leads_path": RESEARCHER_LEADS_PATH,
        "old_leads_path": LEADS_PATH,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, reloader_type="stat")