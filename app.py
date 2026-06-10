import streamlit as st
import requests
import re
import json
import time
from difflib import get_close_matches
from bs4 import BeautifulSoup

# ── page config ──────────────────────────────────────────
st.set_page_config(
    page_title="Job Pipeline",
    page_icon="🎯",
    layout="centered"
)

st.markdown("""
<style>
  [data-testid="stAppViewContainer"] { background: #0f0f11; }
  [data-testid="stMain"] { background: #0f0f11; }
  section[data-testid="stSidebar"] { background: #18181c; }
  h1, h2, h3, label, .stMarkdown p { color: #f0f0f0 !important; }
  .stTextInput input, .stTextArea textarea, .stNumberInput input {
      background: #1e1e24 !important;
      color: #f0f0f0 !important;
      border: 1px solid #333 !important;
      border-radius: 8px !important;
  }
  .stSelectbox div[data-baseweb="select"] > div {
      background: #1e1e24 !important;
      color: #f0f0f0 !important;
      border: 1px solid #333 !important;
      border-radius: 8px !important;
  }
  .stButton > button {
      background: #6c63ff !important;
      color: white !important;
      border: none !important;
      border-radius: 8px !important;
      padding: 0.6rem 2rem !important;
      font-weight: 600 !important;
      width: 100%;
  }
  .stButton > button:hover { background: #8a83ff !important; }
  .stSuccess, .stError, .stInfo, .stWarning {
      border-radius: 8px !important;
  }
  div[data-testid="stExpander"] {
      background: #1e1e24;
      border: 1px solid #333;
      border-radius: 10px;
  }
  hr { border-color: #333; }
</style>
""", unsafe_allow_html=True)

# ── config ───────────────────────────────────────────────
NOTION_TOKEN        = st.secrets["NOTION_TOKEN"]
JOB_PIPELINE_DB_ID  = st.secrets["JOB_PIPELINE_DB_ID"]
COMPANIES_DB_ID     = st.secrets["COMPANIES_DB_ID"]
GEMINI_API_KEY      = st.secrets.get("GEMINI_API_KEY", "")
OPENROUTER_API_KEY  = st.secrets.get("OPENROUTER_API_KEY", "")
SCRAPERAPI_KEY      = st.secrets.get("SCRAPERAPI_KEY", "")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

PRIORITY_EMOJI = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
}

FIT_SCORE = {
    "high": 5, "medium-high": 4, "medium high": 4,
    "medium": 3, "low-medium": 2, "low medium": 2, "low": 1
}

# ── helpers ──────────────────────────────────────────────

def sanitize_select(value):
    if value:
        return value.replace(",", "")
    return value

def fuzzy_match(value, choices, cutoff=0.6):
    if not value or not choices:
        return value
    def clean(s):
        return "".join(c for c in s if c.isalnum() or c.isspace()).strip().lower()
    cleaned_value = clean(value)
    cleaned_choices = {clean(c): c for c in choices}
    matches = get_close_matches(cleaned_value, cleaned_choices.keys(), n=1, cutoff=cutoff)
    if matches:
        return cleaned_choices[matches[0]]
    return value

def get_select_options(db_id, property_name):
    res = requests.get("https://api.notion.com/v1/databases/" + db_id, headers=HEADERS)
    props = res.json().get("properties", {})
    prop = props.get(property_name, {})
    options = prop.get("select", {}).get("options", [])
    return [o["name"] for o in options]

@st.cache_data(ttl=300, show_spinner=False)
def load_options():
    return {
        "company": {
            "Company Size": get_select_options(COMPANIES_DB_ID, "Company Size"),
            "Company Tier": get_select_options(COMPANIES_DB_ID, "Company Tier"),
            "Industry":     get_select_options(COMPANIES_DB_ID, "Industry"),
            "WFH Policy":   get_select_options(COMPANIES_DB_ID, "WFH Policy"),
        },
        "job": {
            "Role Tier":    get_select_options(JOB_PIPELINE_DB_ID, "Role Tier"),
            "Fit Level":    get_select_options(JOB_PIPELINE_DB_ID, "Fit Level"),
            "Apply Status": get_select_options(JOB_PIPELINE_DB_ID, "Apply Status"),
        }
    }

def search_company(name):
    res = requests.post(
        "https://api.notion.com/v1/databases/" + COMPANIES_DB_ID + "/query",
        headers=HEADERS,
        json={"filter": {"property": "Company Name", "title": {"equals": name}}}
    )
    results = res.json().get("results", [])
    if results:
        return results[0]["id"], True

    res = requests.post(
        "https://api.notion.com/v1/databases/" + COMPANIES_DB_ID + "/query",
        headers=HEADERS, json={}
    )
    all_companies = res.json().get("results", [])
    name_map = {}
    for p in all_companies:
        title = p["properties"].get("Company Name", {}).get("title", [])
        if title:
            name_map[title[0]["plain_text"]] = p["id"]

    matched_name = fuzzy_match(name, list(name_map.keys()), cutoff=0.7)
    if matched_name in name_map:
        return name_map[matched_name], True
    return None, False

def create_company(d, opt):
    def sel(raw, choices):
        v = sanitize_select(fuzzy_match(raw, choices))
        return v if v else None

    props = {
        "Company Name": {"title": [{"text": {"content": d["company_name"]}}]},
        "Location":     {"rich_text": [{"text": {"content": d.get("location", "")}}]},
        "Notes":        {"rich_text": [{"text": {"content": d.get("notes", "")}}]},
    }

    # ใส่ select เฉพาะเมื่อมีค่า — Notion error ถ้าส่ง name: ""
    for field, key in [
        ("Company Size", "company_size"),
        ("Company Tier", "company_tier"),
        ("Industry",     "industry"),
        ("WFH Policy",   "wfh_policy"),
    ]:
        v = sel(d.get(key, ""), opt["company"][field])
        if v:
            props[field] = {"select": {"name": v}}

    payload = {"parent": {"database_id": COMPANIES_DB_ID}, "properties": props}
    if d.get("website"):
        payload["properties"]["Website"] = {"url": d["website"]}

    res = requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=payload)
    result = res.json()
    if "id" not in result:
        return None, result.get("message", "unknown error")
    return result["id"], None

def create_job(d, company_page_id, opt):
    def sel(raw, choices):
        v = sanitize_select(fuzzy_match(raw, choices))
        return v if v else None

    props = {
        "Job Title":       {"title": [{"text": {"content": d["job_title"]}}]},
        "Company":         {"relation": [{"id": company_page_id}]},
        "Apply Priority":  {"number": None},
        "Salary Min":      {"number": d.get("salary_min") or None},
        "Salary Max":      {"number": d.get("salary_max") or None},
        "Work Location":   {"rich_text": [{"text": {"content": d.get("work_location", "")}}]},
        "Key Tech Stack":  {"rich_text": [{"text": {"content": d.get("key_tech_stack", "")}}]},
        "Gaps to Address": {"rich_text": [{"text": {"content": d.get("gaps", "")}}]},
        "Notes":           {"rich_text": [{"text": {"content": d.get("notes", "")}}]},
        "Date Applied":    {"date": {"start": time.strftime("%Y-%m-%d")}},
    }

    # select fields — skip ถ้าว่าง
    for field, key in [
        ("Role Tier",    "role_tier"),
        ("Fit Level",    "fit_level"),
        ("Apply Status", "apply_status"),
    ]:
        v = sel(d.get(key, ""), opt["job"][field])
        if v:
            props[field] = {"select": {"name": v}}

    # ── แก้ไขจุดนี้: ยุบรวมเหลือแค่คอลัมน์ "๋Job URL" ตามโครงสร้าง Notion ของคุณหนู ──
    url_to_save = d.get("job_url") or d.get("linkedin_url")
    if url_to_save:
        props["๋Job URL"] = {"url": url_to_save}

    children = []
    if d.get("analysis"):
        children.append({
            "object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": "AI Analysis"}}]}
        })
        text = d["analysis"].strip()
        for chunk in [text[i:i+1999] for i in range(0, len(text), 1999)]:
            children.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": chunk}}]}
            })

    res = requests.post(
        "https://api.notion.com/v1/pages", headers=HEADERS,
        json={"parent": {"database_id": JOB_PIPELINE_DB_ID}, "properties": props, "children": children}
    )
    result = res.json()
    if "id" in result:
        return True, None
    return False, result.get("message", "unknown error")

def query_all_jobs(filter_payload):
    results = []
    payload = dict(filter_payload)
    while True:
        res = requests.post(
            "https://api.notion.com/v1/databases/" + JOB_PIPELINE_DB_ID + "/query",
            headers=HEADERS, json=payload
        )
        data = res.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data["next_cursor"]
    return results

def rerank_all_jobs(opt, log_fn):
    pending_status = fuzzy_match("To Apply", opt["job"]["Apply Status"])

    non_pending = query_all_jobs({
        "filter": {"property": "Apply Status", "select": {"does_not_equal": pending_status}}
    })
    for job in non_pending:
        requests.patch(
            "https://api.notion.com/v1/pages/" + job["id"],
            headers=HEADERS,
            json={"properties": {"Apply Priority": {"number": None}}}
        )

    jobs = query_all_jobs({
        "filter": {"property": "Apply Status", "select": {"equals": pending_status}}
    })
    if not jobs:
        log_fn("ℹ️ ไม่มี pending jobs")
        return

    def job_score(job):
        fit = job["properties"].get("Fit Level", {}).get("select") or {}
        return -FIT_SCORE.get(fit.get("name", "").lower(), 0)

    jobs_sorted = sorted(jobs, key=job_score)
    for rank, job in enumerate(jobs_sorted, start=1):
        page_id = job["id"]
        title = job["properties"].get("Job Title", {}).get("title", [{}])
        job_name = title[0].get("plain_text", "?") if title else "?"
        emoji = PRIORITY_EMOJI.get(rank, str(rank))
        requests.patch(
            "https://api.notion.com/v1/pages/" + page_id,
            headers=HEADERS,
            json={"properties": {"Apply Priority": {"number": rank}}}
        )
        log_fn(f"{emoji} {rank}. {job_name}")

# ── UI ───────────────────────────────────────────────────

st.title("🎯 Job Pipeline")
st.markdown("---")

try:
    opt = load_options()
except Exception as e:
    st.error(f"❌ โหลด Notion options ไม่ได้: {e}")
    st.stop()

def submit_to_notion(job_data, company_data):
    if not job_data.get("job_title", "").strip():
        st.error("กรุณาตรวจสอบว่ามี Job Title ค่ะ")
        return
    if not company_data.get("company_name", "").strip():
        st.error("กรุณาตรวจสอบว่ามี Company Name ค่ะ")
        return

    log_lines = []
    def log(msg):
        log_lines.append(msg)

    with st.spinner("กำลัง sync กับ Notion..."):
        company_name = company_data["company_name"]
        job_title = job_data["job_title"]

        log(f"🔍 ค้นหา: {company_name}")
        company_id, found = search_company(company_name)

        if found and company_id:
            log("✅ เจอบริษัทใน Notion แล้ว")
        else:
            log("➕ สร้างบริษัทใหม่...")
            company_id, err = create_company(company_data, opt)
            if err:
                st.error(f"❌ สร้างบริษัทไม่สำเร็จ: {err}")
                return
            log(f"✅ สร้างบริษัท {company_name} สำเร็จ")

        job_data["company_name"] = company_name
        ok, err = create_job(job_data, company_id, opt)
        if not ok:
            st.error(f"❌ สร้าง job ไม่สำเร็จ: {err}")
            return
        log(f"✅ เพิ่ม job: {job_title} @ {company_name}")

        log("\n📊 Reranking jobs...")
        rerank_all_jobs(opt, log)
        log("\n🎉 เสร็จแล้ว!")

    st.success("เพิ่มลง Notion สำเร็จแล้วค่ะ! ✨")
    with st.expander("ดู log"):
        st.code("\n".join(log_lines))


# ── Tabs ─────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📝 Paste Python Dict (Fast)", "✍️ Manual Form", "🤖 Batch Analyze"])

# --- TAB 1 ---
with tab1:
    st.markdown("วางโค้ด `job_data` และ `company_data` ลงในช่องด้านล่างแล้วกด Submit ได้เลยค่ะ")
    raw_code = st.text_area(
        "Paste Code Here",
        height=450,
        placeholder="job_data = {\n  'job_title': '...', \n  ...\n}\n\ncompany_data = {\n  ...\n}",
        label_visibility="collapsed"
    )

    if st.button("🚀 Add to Notion + Rerank", key="btn_code"):
        if not raw_code.strip():
            st.error("กรุณาวางโค้ดก่อนค่ะ")
            st.stop()
        local_vars = {}
        try:
            exec(raw_code, {}, local_vars)
            j_data = local_vars.get("job_data")
            c_data = local_vars.get("company_data")
            if not isinstance(j_data, dict) or not isinstance(c_data, dict):
                st.error("❌ โค้ดไม่ถูกต้อง: ต้องมีตัวแปร `job_data` และ `company_data` ที่เป็นรูปแบบ Dictionary ค่ะ")
                st.stop()
            submit_to_notion(j_data, c_data)
        except Exception as e:
            st.error(f"❌ เกิดข้อผิดพลาดในการอ่านโค้ด: {e}")


# --- TAB 2 ---
# --- TAB 2 ---
with tab2:
    st.subheader("📋 Job Info")
    col1, col2 = st.columns(2)
    with col1:
        job_title    = st.text_input("Job Title *", placeholder="Data Analyst")
        role_tier    = st.selectbox("Role Tier", [""] + opt["job"]["Role Tier"])
        fit_level    = st.selectbox("Fit Level", [""] + opt["job"]["Fit Level"])
        apply_status = st.selectbox("Apply Status", [""] + opt["job"]["Apply Status"])
    with col2:
        work_location = st.text_input("Work Location", placeholder="Bangkok, On-site")
        salary_min    = st.number_input("Salary Min (฿)", min_value=0, step=1000, value=0)
        salary_max    = st.number_input("Salary Max (฿)", min_value=0, step=1000, value=0)

    # ปรับเหลือแค่ช่อง Job URL ช่องเดียวแมปกับ Notion
    job_url       = st.text_input("Job URL", placeholder="https://th.jobsdb.com/job/...")
    key_tech_stack = st.text_input("Key Tech Stack", placeholder="SQL, Python, Tableau")
    gaps          = st.text_area("Gaps to Address", placeholder="สิ่งที่ขาด / ต้องเตรียม", height=80)
    job_notes     = st.text_area("Job Notes", placeholder="หมายเหตุเพิ่มเติม", height=80)
    analysis      = st.text_area("AI Analysis", placeholder="วาง AI analysis ได้เลยค่ะ", height=120)

    st.markdown("---")
    st.subheader("🏢 Company Info")
    col3, col4 = st.columns(2)
    with col3:
        company_name = st.text_input("Company Name *", placeholder="Shopee")
        company_size = st.text_input("Company Size", placeholder="10000+ employees")
        company_tier = st.selectbox("Company Tier", [""] + opt["company"]["Company Tier"])
        industry     = st.selectbox("Industry", [""] + opt["company"]["Industry"])
    with col4:
        location   = st.text_input("Location", placeholder="Bangkok, Thailand")
        wfh_policy = st.selectbox("WFH Policy", [""] + opt["company"]["WFH Policy"])
        website    = st.text_input("Website", placeholder="https://careers.shopee.co.th")

    company_notes = st.text_area("Company Notes", placeholder="Glassdoor score, culture notes ฯลฯ", height=80)

    st.markdown("---")
    if st.button("🚀 Add to Notion + Rerank", key="btn_manual"):
        j_data = {
            "job_title":      job_title,
            "role_tier":      role_tier,
            "fit_level":      fit_level,
            "apply_status":   apply_status,
            "work_location":  work_location,
            "salary_min":     salary_min if salary_min > 0 else None,
            "salary_max":     salary_max if salary_max > 0 else None,
            "job_url":        job_url,
            "key_tech_stack": key_tech_stack,
            "gaps":           gaps,
            "notes":          job_notes,
            "analysis":       analysis,
        }
        c_data = {
            "company_name": company_name,
            "company_size": company_size,
            "company_tier": company_tier,
            "industry":     industry,
            "location":     location,
            "wfh_policy":   wfh_policy,
            "website":      website,
            "notes":        company_notes,
        }
        submit_to_notion(j_data, c_data)


# ── TAB 3: Batch Analyze ─────────────────────────────────
CANDIDATE_PROFILE = """
WHO I AM — THAPANEE CHAIPRAPHA
Fresh grad (May 2026), Thammasat University — Software Engineering (CS)
งานแรก — เปิดรับกว้าง แต่ prefer AI/Tech roles

Hard Skills
Proficient: Python, JavaScript, Java, React.js, FastAPI, Node.js, SQL, MongoDB, Git, Figma,
LLM API (Gemini, OpenAI-compatible), Prompt Engineering, Vision Transformers (ViT-B/16),
Deep Learning, Grad-CAM (Explainable AI)
Familiar: Next.js, TypeScript, Tailwind CSS, Docker, Power BI, Excel VBA

Key Projects
- dCDT — Solo medical AI screening: 96.14% accuracy | FastAPI + Next.js + ViT-B/16 + Grad-CAM
- MyGPT — Full-stack LLM web app | React + Node.js + Gemini API + JWT + Vercel
- Keeppook — Android finance tracker | Java + Gemini API + caching layer
- Freelance UX/UI: 6 projects, 5-star, Fastwork

Salary: 35K–45K THB (floor 30K) | WFH/Hybrid preferred
งานที่ใช่: build AI ที่คนใช้จริง, ownership สูง, ทีมเล็ก, ไม่ bureaucratic
Dealbreakers: เงิน <30K, Pure QA, implement ตาม spec อย่างเดียว, บริษัทไม่มั่นคง
"""

ANALYSIS_PROMPT = """
คุณคือ career advisor วิเคราะห์ JD นี้สำหรับผู้สมัคร:
{profile}

JD:
{jd_text}

ตอบกลับเป็น JSON เท่านั้น ห้ามมี markdown backticks หรือข้อความอื่นนอกจาก JSON:

{{
  "job_title": "ชื่อตำแหน่ง",
  "company_name": "ชื่อบริษัท",
  "role_tier": "Tier1/2/3 - เหตุผลสั้นๆ",
  "fit_level": "high/medium-high/medium/low-medium/low",
  "work_location": "เมือง/ย่าน",
  "wfh_policy": "WFH Available/Hybrid/On-site/Unknown",
  "key_tech_stack": "max 6 items คั่นด้วยคอมมา",
  "salary_min": 0,
  "salary_max": 0,
  "min_experience_years": 0,
  "fresh_grad_welcome": true,
  "ai_depth_score": 3,
  "ownership_score": 3,
  "my_skill_match_pct": 75,
  "gap_skills": ["skill ที่ขาด"],
  "resume_version": "VERSION A/B/RHENUS/THINKNET/ACCENTURE",
  "resume_reason": "เหตุผลสั้นๆ",
  "apply_decision": "APPLY/WATCHLIST/PASS",
  "apply_url": "ลิงค์สมัครงานโดยตรงจาก JD ถ้าไม่มีให้ใส่ค่าว่าง",
  "company_size": "startup/sme/enterprise",
  "company_tier": "Level1/2/3 - เหตุผล",
  "industry": "อุตสาหกรรม",
  "location": "Bangkok, Thailand",
  "website": "",
  "gaps": "gap หลักสั้นๆ max 80 chars",
  "notes": "note สำคัญ max 100 chars",
  "narrative_analysis": "วิเคราะห์ละเอียดภาษาไทย: บริษัทเป็นยังไง / เงิน-สวัสดิการ / AI จริงหรือ AI washing / เติบโตได้ไหม / red flags / green flags / สรุป APPLY-WATCHLIST-PASS",
  "interview_prep": {{
    "behavioral_questions": [{{"question": "?", "answer_guide": "?"}}],
    "technical_questions":  [{{"question": "?", "answer_guide": "?"}}],
    "questions_to_ask": ["คำถามถามกลับ employer"],
    "salary_negotiation_script": "script ต่อรองเงินภาษาไทย"
  }},
  "application_guide": {{
    "how_to_apply": "วิธี apply",
    "form_questions_to_prepare": ["คำถามในฟอร์มที่น่าจะเจอ"],
    "things_to_prepare": ["สิ่งที่ต้องเตรียม"]
  }}
}}
ถ้า JD ดึงไม่ได้ให้ตอบ: {{"error": "ไม่สามารถดึง JD ได้", "job_title": "Unknown", "company_name": "Unknown"}}
"""


def _extract_text_from_html(html, url):
    """แยก text จาก HTML โดย detect site-specific selectors ก่อน"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "iframe"]):
        tag.decompose()
    if "jobsdb.com" in url:
        section = soup.find("div", {"data-automation": "jobAdDetails"})
        if section:
            return section.get_text(separator="\n", strip=True)[:6000]
    if "jobthai.com" in url:
        section = soup.find("div", class_=re.compile("job-detail|detail-content", re.I))
        if section:
            return section.get_text(separator="\n", strip=True)[:6000]
    main = soup.find("main") or soup.find("article") or soup.body
    if main:
        text = main.get_text(separator="\n", strip=True)
        return re.sub(r"\n{3,}", "\n\n", text)[:6000]
    return ""


def _fetch_with_scraperapi(url):
    """Layer 1: ScraperAPI — bypass anti-bot ผ่าน proxy"""
    if not SCRAPERAPI_KEY:
        return None, "ScraperAPI: ไม่มี key"
    try:
        proxied = (
            f"http://api.scraperapi.com"
            f"?api_key={SCRAPERAPI_KEY}"
            f"&url={requests.utils.quote(url, safe='')}"
            f"&render=true"
        )
        resp = requests.get(proxied, timeout=60)
        resp.raise_for_status()
        text = _extract_text_from_html(resp.text, url)
        if len(text.strip()) >= 100:
            return text, None
        return None, "ScraperAPI: content น้อยเกินไป"
    except Exception as e:
        return None, f"ScraperAPI error: {e}"


def _fetch_with_requests(url):
    """Layer 2: requests ธรรมดา — fallback สุดท้าย"""
    hdrs = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.google.com/",
    }
    try:
        resp = requests.get(url, headers=hdrs, timeout=15)
        resp.raise_for_status()
        text = _extract_text_from_html(resp.text, url)
        if len(text.strip()) >= 100:
            return text, None
        return None, "requests: content น้อยเกินไป (อาจเป็น JS-rendered page)"
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status == 403:
            return None, "403 Forbidden"
        if status == 404:
            return None, "404 Not Found — URL อาจหมดอายุแล้ว"
        return None, f"HTTP {status}: {e}"
    except Exception as e:
        return None, f"requests error: {e}"


def fetch_jd(url):
    """
    Returns (content, error_message). error_message is None on success.
    ลำดับ: ScraperAPI → requests ธรรมดา
    """
    if "facebook.com" in url:
        return None, "Facebook URL — กรุณา copy JD มาวางเองค่ะ"
    if "linkedin.com" in url:
        return None, "LinkedIn URL — กรุณา copy JD มาวางเองค่ะ"

    errors = []
    for name, fn in [("ScraperAPI", _fetch_with_scraperapi), ("requests", _fetch_with_requests)]:
        text, err = fn(url)
        if text:
            return text, None
        errors.append(f"{name}: {err}")

    return None, " | ".join(errors)


def analyze_with_llm(jd_text, retries=2):
    prompt = ANALYSIS_PROMPT.format(profile=CANDIDATE_PROFILE, jd_text=jd_text[:5000])

    # ── ลอง OpenRouter ก่อน ──────────────────────────────
    if OPENROUTER_API_KEY:
        # เรียง model จากเร็ว → ช้า, ถ้าตัวแรก fail/timeout ลองตัวถัดไป
        models = [
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
        ]
        for model in models:
            for attempt in range(retries):
                resp = None
                try:
                    resp = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 4096,
                            "temperature": 0.3,
                        },
                        timeout=45  # fail fast แล้วลอง model ถัดไป
                    )
                    resp.raise_for_status()
                    raw = resp.json()["choices"][0]["message"]["content"]
                    raw = re.sub(r"^```json\s*", "", raw.strip())
                    raw = re.sub(r"```\s*$", "", raw.strip())
                    return _parse_llm_json(raw)
                except requests.exceptions.HTTPError as e:
                    status = resp.status_code if resp is not None else 0
                    if status == 429 and attempt < retries - 1:
                        time.sleep(15)
                        continue
                    if status in (503, 529) and attempt < retries - 1:
                        time.sleep(5)
                        continue
                    break  # HTTP error อื่น → ลอง model ถัดไป
                except requests.exceptions.Timeout:
                    break  # timeout → ลอง model ถัดไปเลย
                except Exception:
                    break
        # OpenRouter ล้มเหลวทุก model → fall through ไป Gemini

    # ── fallback: Gemini direct ───────────────────────────
    if not GEMINI_API_KEY:
        return {"error": "ไม่มี OPENROUTER_API_KEY หรือ GEMINI_API_KEY ใน secrets"}

    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-2.0-flash-lite:generateContent?key=" + GEMINI_API_KEY)

    for attempt in range(retries):
        resp = None
        try:
            resp = requests.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192}
            }, timeout=90)
            resp.raise_for_status()
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            raw = re.sub(r"^```json\s*", "", raw.strip())
            raw = re.sub(r"```\s*$", "", raw.strip())
            return _parse_llm_json(raw)

        except requests.exceptions.HTTPError as e:
            error_body = {}
            try:
                error_body = resp.json()
            except Exception:
                pass
            error_msg    = error_body.get("error", {}).get("message", str(e))
            error_status = error_body.get("error", {}).get("status", "")

            if resp is not None and resp.status_code == 429:
                if error_status == "RESOURCE_EXHAUSTED" or "quota" in error_msg.lower():
                    retry_match = re.search(r"retry in ([\d.]+)s", error_msg)
                    if retry_match and attempt < retries - 1:
                        time.sleep(float(retry_match.group(1)) + 5)
                        continue
                    return {"error": "Gemini quota หมดแล้ว — รอถึงพรุ่งนี้หรือเพิ่ม billing"}
                if attempt < retries - 1:
                    time.sleep((attempt + 1) * 30)
                    continue
            elif resp is not None and resp.status_code == 503 and attempt < retries - 1:
                time.sleep((attempt + 1) * 10)
                continue
            return {"error": f"{str(e)} | {error_msg}"}

        except Exception as e:
            return {"error": str(e)}

    return {"error": "LLM retry หมดแล้ว"}


def _parse_llm_json(raw):
    """Parse JSON จาก LLM — พยายาม repair ถ้าถูกตัด"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        last_brace = raw.rfind("}")
        if last_brace != -1:
            try:
                return json.loads(raw[:last_brace + 1])
            except json.JSONDecodeError:
                pass
        def extract(field, default=""):
            m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', raw)
            return m.group(1) if m else default
        return {
            "job_title":      extract("job_title", "Unknown"),
            "company_name":   extract("company_name", "Unknown"),
            "fit_level":      extract("fit_level", "medium"),
            "apply_decision": extract("apply_decision", "WATCHLIST"),
            "role_tier":      extract("role_tier", ""),
            "work_location":  extract("work_location", ""),
            "key_tech_stack": extract("key_tech_stack", ""),
            "gaps":           extract("gaps", ""),
            "notes":          extract("notes", ""),
            "wfh_policy":     extract("wfh_policy", "Unknown"),
            "company_size":   extract("company_size", ""),
            "company_tier":   extract("company_tier", ""),
            "industry":       extract("industry", ""),
            "apply_url":      extract("apply_url", ""),
            "error": "JSON truncated — partial data recovered"
        }


def analysis_to_notion_dicts(a, job_url):
    ip = a.get("interview_prep", {})
    ag = a.get("application_guide", {})
    parts = [a.get("narrative_analysis", "")]
    if ip:
        parts.append("\n--- INTERVIEW PREP ---")
        for q in ip.get("behavioral_questions", []):
            parts.append(f"[Behavioral] {q.get('question','')}\n  → {q.get('answer_guide','')}")
        for q in ip.get("technical_questions", []):
            parts.append(f"[Technical] {q.get('question','')}\n  → {q.get('answer_guide','')}")
        if ip.get("questions_to_ask"):
            parts.append("ถามกลับ:\n" + "\n".join(f"  • {q}" for q in ip["questions_to_ask"]))
        if ip.get("salary_negotiation_script"):
            parts.append(f"\n💰 Salary Script:\n{ip['salary_negotiation_script']}")
    if ag:
        parts.append("\n--- APPLICATION GUIDE ---")
        if ag.get("how_to_apply"):
            parts.append(f"How to apply: {ag['how_to_apply']}")
        if ag.get("things_to_prepare"):
            parts.append("เตรียม:\n" + "\n".join(f"  • {x}" for x in ag["things_to_prepare"]))
    if a.get("resume_version"):
        parts.append(f"\n--- RESUME ---\nใช้: {a['resume_version']}\nเหตุผล: {a.get('resume_reason','')}")

    # ปรับเหลือแค่ job_url หลักชิ้นเดียว
    job_data = {
        "job_title":      a.get("job_title", "Unknown"),
        "role_tier":      a.get("role_tier", ""),
        "fit_level":      a.get("fit_level", "medium"),
        "apply_status":   "To Apply",
        "work_location":  a.get("work_location", ""),
        "salary_min":     a.get("salary_min") or None,
        "salary_max":     a.get("salary_max") or None,
        "job_url":        job_url,
        "key_tech_stack": a.get("key_tech_stack", ""),
        "gaps":           a.get("gaps", ""),
        "notes":          a.get("notes", ""),
        "analysis":       "\n\n".join(parts),
    }
    company_data = {
        "company_name": a.get("company_name", "Unknown"),
        "company_size": a.get("company_size", ""),
        "company_tier": a.get("company_tier", ""),
        "industry":     a.get("industry", ""),
        "location":     a.get("location", ""),
        "wfh_policy":   a.get("wfh_policy", "Unknown"),
        "website":      a.get("website", ""),
        "notes":        "",
    }
    return job_data, company_data


with tab3:
    st.markdown("วิเคราะห์ job จาก URL เดี่ยว หรืออัปโหลด CSV รายการ URL → LLM → Notion ค่ะ")

    if not OPENROUTER_API_KEY and not GEMINI_API_KEY:
        st.warning("⚠️ ยังไม่มี `OPENROUTER_API_KEY` หรือ `GEMINI_API_KEY` ใน Streamlit Secrets ค่ะ")

    delay       = st.slider("หน่วงเวลาระหว่าง request (วินาที)", 5, 30, 12,
                            help="แนะนำ 12+ วินาที เพื่อหลีกเลี่ยง rate limit")
    push_notion = st.checkbox("Push เข้า Notion อัตโนมัติ", value=True)

    mode = st.radio("วิธีใส่ job", ["🔗 วาง URL เดี่ยว", "📂 อัปโหลด CSV"], horizontal=True)

    jobs = []

    if mode == "🔗 วาง URL เดี่ยว":
        raw_urls = st.text_area(
            "วาง URL (ได้หลายบรรทัด — 1 URL ต่อบรรทัด)",
            height=150,
            placeholder="https://th.jobsdb.com/job/12345\nhttps://www.jobthai.com/en/job/67890"
        )
        if raw_urls.strip():
            seen = set()
            for line in raw_urls.strip().splitlines():
                url = line.strip()
                if not url:
                    continue
                base = url.split("?")[0].rstrip("/")
                if base not in seen:
                    seen.add(base)
                    jobs.append({"url": url, "name": url[:60]})
            if jobs:
                st.info(f"พบ **{len(jobs)}** URL")

    else:
        uploaded = st.file_uploader("อัปโหลด Job_Listings.csv", type="csv")
        if uploaded:
            import csv, io
            content = uploaded.read().decode("utf-8-sig")
            reader  = csv.DictReader(io.StringIO(content))
            seen, dupes = set(), 0
            for row in reader:
                url = (row.get("URL") or "").strip()
                if not url:
                    continue
                base = url.split("?")[0].rstrip("/")
                if base in seen:
                    dupes += 1
                    continue
                seen.add(base)
                jobs.append({"url": url, "name": (row.get("Name") or "").strip()})
            if jobs:
                st.info(f"พบ **{len(jobs)}** unique jobs ({dupes} duplicates ถูกตัดออก)")

    if st.button("🚀 Start Batch Analyze", key="btn_batch"):
        if not jobs:
            st.warning("⚠️ กรุณาใส่ URL หรืออัปโหลด CSV ก่อนนะคะ")
            st.stop()

        stats    = {"ok": 0, "err": 0, "notion_ok": 0, "notion_err": 0}
        results  = []
        progress = st.progress(0, text="เริ่มต้น...")
        log_area = st.empty()
        logs     = []

        def add_log(msg):
            logs.append(msg)
            log_area.code("\n".join(logs[-30:]))

        for i, job in enumerate(jobs):
            url  = job["url"]
            name = job["name"] or url[:50]
            progress.progress(i / len(jobs), text=f"[{i+1}/{len(jobs)}] {name[:40]}...")

            add_log(f"\n[{i+1}/{len(jobs)}] {name[:55]}")
            add_log(f"  🌐 Fetching JD...")
            jd, fetch_err = fetch_jd(url)
            if fetch_err:
                add_log(f"  ❌ Fetch failed: {fetch_err}")
                add_log(f"  ⏭️ ข้ามไปก่อนเลยค่ะ — ไม่ส่งเข้า LLM")
                stats["err"] += 1
                results.append({"url": url, "name": name, "status": "error", "error": fetch_err})
                if i < len(jobs) - 1:
                    time.sleep(delay)
                continue
            add_log(f"  📄 {jd[:80].replace(chr(10),' ')}...")

            add_log(f"  🤖 Analyzing with LLM...")
            analysis = analyze_with_llm(jd)

            if "error" in analysis and analysis.get("job_title", "Unknown") == "Unknown" and analysis.get("company_name", "Unknown") == "Unknown":
                add_log(f"  ❌ {analysis['error']}")
                stats["err"] += 1
                results.append({"url": url, "name": name, "status": "error", "error": analysis["error"]})
            else:
                add_log(f"  ✅ {analysis.get('job_title','?')} @ {analysis.get('company_name','?')} "
                        f"| {analysis.get('fit_level','?')} | {analysis.get('apply_decision','?')}")
                stats["ok"] += 1
                result_entry = {"url": url, "name": name, "status": "ok", "analysis": analysis}

                if push_notion:
                    add_log(f"  📤 Pushing to Notion...")
                    try:
                        j_data, c_data = analysis_to_notion_dicts(analysis, url)
                        if not j_data.get("job_title", "").strip():
                            raise ValueError("no job title")
                        if not c_data.get("company_name", "").strip():
                            raise ValueError("no company name")
                        company_id, found = search_company(c_data["company_name"])
                        if not found or not company_id:
                            company_id, err = create_company(c_data, opt)
                            if err:
                                raise ValueError(f"create company: {err}")
                        ok_job, err_job = create_job(j_data, company_id, opt)
                        if not ok_job:
                            raise ValueError(f"create job: {err_job}")
                        add_log(f"  ✅ Notion OK")
                        stats["notion_ok"] += 1
                    except Exception as e:
                        add_log(f"  ❌ Notion error: {e}")
                        stats["notion_err"] += 1

                results.append(result_entry)

            if i < len(jobs) - 1:
                time.sleep(delay)

        if push_notion and stats["notion_ok"] > 0:
            add_log("\n📊 Reranking all jobs...")
            try:
                rerank_all_jobs(opt, add_log)
                add_log("✅ Rerank done!")
            except Exception as e:
                add_log(f"❌ Rerank error: {e}")

        progress.progress(1.0, text="เสร็จแล้ว! ✨")
        st.success(f"เสร็จแล้ว! ✅ {stats['ok']} analyzed | 📤 {stats['notion_ok']} pushed | ❌ {stats['err']} errors")

        all_a = [r["analysis"] for r in results if r.get("status") == "ok"]
        if all_a:
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("APPLY",     sum(1 for a in all_a if a.get("apply_decision") == "APPLY"))
            col_b.metric("WATCHLIST", sum(1 for a in all_a if a.get("apply_decision") == "WATCHLIST"))
            col_c.metric("PASS",      sum(1 for a in all_a if a.get("apply_decision") == "PASS"))
            all_gaps = [g for a in all_a for g in a.get("gap_skills", [])]
            gc = {}
            for g in all_gaps:
                g = g.strip()
                if g: gc[g] = gc.get(g, 0) + 1
            top = sorted(gc.items(), key=lambda x: -x[1])[:5]
            if top:
                st.caption(f"Top skill gaps: {', '.join(f'{g}({n})' for g,n in top)}")

        st.download_button(
            "⬇️ Download jobs_analyzed.json",
            data=json.dumps(results, ensure_ascii=False, indent=2),
            file_name="jobs_analyzed.json",
            mime="application/json"
        )
