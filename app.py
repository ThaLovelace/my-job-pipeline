import streamlit as st
import requests
from difflib import get_close_matches

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
NOTION_TOKEN       = st.secrets["NOTION_TOKEN"]
JOB_PIPELINE_DB_ID = st.secrets["JOB_PIPELINE_DB_ID"]
COMPANIES_DB_ID    = st.secrets["COMPANIES_DB_ID"]

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
        return sanitize_select(fuzzy_match(raw, choices))

    payload = {
        "parent": {"database_id": COMPANIES_DB_ID},
        "properties": {
            "Company Name": {"title": [{"text": {"content": d["company_name"]}}]},
            "Company Size": {"select": {"name": sel(d.get("company_size", ""), opt["company"]["Company Size"])}},
            "Company Tier": {"select": {"name": sel(d.get("company_tier", ""), opt["company"]["Company Tier"])}},
            "Industry":     {"select": {"name": sel(d.get("industry", ""),     opt["company"]["Industry"])}},
            "Location":     {"rich_text": [{"text": {"content": d.get("location", "")}}]},
            "WFH Policy":   {"select": {"name": sel(d.get("wfh_policy", ""),  opt["company"]["WFH Policy"])}},
            "Notes":        {"rich_text": [{"text": {"content": d.get("notes", "")}}]}
        }
    }
    if d.get("website"):
        payload["properties"]["Website"] = {"url": d["website"]}
    res = requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=payload)
    result = res.json()
    if "id" not in result:
        return None, result.get("message", "unknown error")
    return result["id"], None

def create_job(d, company_page_id, opt):
    props = {
        "Job Title":       {"title": [{"text": {"content": d["job_title"]}}]},
        "Company":         {"relation": [{"id": company_page_id}]},
        "Role Tier":       {"select": {"name": fuzzy_match(d.get("role_tier", ""),    opt["job"]["Role Tier"])}},
        "Fit Level":       {"select": {"name": fuzzy_match(d.get("fit_level", ""),    opt["job"]["Fit Level"])}},
        "Apply Priority":  {"number": None},
        "Salary Min":      {"number": d.get("salary_min") or None},
        "Salary Max":      {"number": d.get("salary_max") or None},
        "Apply Status":    {"select": {"name": fuzzy_match(d.get("apply_status", ""), opt["job"]["Apply Status"])}},
        "Work Location":   {"rich_text": [{"text": {"content": d.get("work_location", "")}}]},
        "Key Tech Stack":  {"rich_text": [{"text": {"content": d.get("key_tech_stack", "")}}]},
        "Gaps to Address": {"rich_text": [{"text": {"content": d.get("gaps", "")}}]},
        "Notes":           {"rich_text": [{"text": {"content": d.get("notes", "")}}]}
    }
    if d.get("linkedin_url"):
        props["LinkedIn URL"] = {"url": d["linkedin_url"]}

    children = []
    if d.get("analysis"):
        children.append({
            "object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": "AI Analysis"}}]}
        })
        text = d["analysis"].strip()
        for chunk in [text[i:i+2000] for i in range(0, len(text), 2000)]:
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

# สร้างฟังก์ชัน Submit เพื่อให้เรียกใช้ได้ทั้งจากการวางโค้ดและการกรอกฟอร์ม
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

        # 1. search / create company
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

        # 2. create job
        job_data["company_name"] = company_name
        ok, err = create_job(job_data, company_id, opt)
        if not ok:
            st.error(f"❌ สร้าง job ไม่สำเร็จ: {err}")
            return
        log(f"✅ เพิ่ม job: {job_title} @ {company_name}")

        # 3. rerank
        log("\n📊 Reranking jobs...")
        rerank_all_jobs(opt, log)
        log("\n🎉 เสร็จแล้ว!")

    st.success("เพิ่มลง Notion สำเร็จแล้วค่ะ! ✨")
    with st.expander("ดู log"):
        st.code("\n".join(log_lines))


# ── Tabs Navigation ──────────────────────────────────────
tab1, tab2 = st.tabs(["📝 Paste Python Dict (Fast)", "✍️ Manual Form"])

# --- TAB 1: สำหรับวางโค้ด ---
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
            # รัน string ให้กลายเป็นตัวแปร Python
            exec(raw_code, {}, local_vars)
            
            j_data = local_vars.get("job_data")
            c_data = local_vars.get("company_data")

            if not isinstance(j_data, dict) or not isinstance(c_data, dict):
                st.error("❌ โค้ดไม่ถูกต้อง: ต้องมีตัวแปร `job_data` และ `company_data` ที่เป็นรูปแบบ Dictionary ค่ะ")
                st.stop()

            submit_to_notion(j_data, c_data)
            
        except Exception as e:
            st.error(f"❌ เกิดข้อผิดพลาดในการอ่านโค้ด: {e}")


# --- TAB 2: สำหรับกรอกฟอร์มแบบเดิม ---
with tab2:
    st.subheader("📋 Job Info")
    col1, col2 = st.columns(2)
    with col1:
        job_title = st.text_input("Job Title *", placeholder="Data Analyst")
        role_tier = st.selectbox("Role Tier", [""] + opt["job"]["Role Tier"])
        fit_level = st.selectbox("Fit Level", [""] + opt["job"]["Fit Level"])
        apply_status = st.selectbox("Apply Status", [""] + opt["job"]["Apply Status"])

    with col2:
        work_location = st.text_input("Work Location", placeholder="Bangkok, On-site")
        salary_min = st.number_input("Salary Min (฿)", min_value=0, step=1000, value=0)
        salary_max = st.number_input("Salary Max (฿)", min_value=0, step=1000, value=0)
        linkedin_url = st.text_input("LinkedIn URL", placeholder="https://linkedin.com/...")

    key_tech_stack = st.text_input("Key Tech Stack", placeholder="SQL, Python, Tableau")
    gaps = st.text_area("Gaps to Address", placeholder="สิ่งที่ขาด / ต้องเตรียม", height=80)
    job_notes = st.text_area("Job Notes", placeholder="หมายเหตุเพิ่มเติม", height=80)
    analysis = st.text_area("AI Analysis", placeholder="วาง AI analysis ได้เลยค่ะ", height=120)

    st.markdown("---")
    st.subheader("🏢 Company Info")

    col3, col4 = st.columns(2)
    with col3:
        company_name = st.text_input("Company Name *", placeholder="Shopee")
        company_size = st.text_input("Company Size", placeholder="10000+ employees")
        company_tier = st.selectbox("Company Tier", [""] + opt["company"]["Company Tier"])
        industry = st.selectbox("Industry", [""] + opt["company"]["Industry"])

    with col4:
        location = st.text_input("Location", placeholder="Bangkok, Thailand")
        wfh_policy = st.selectbox("WFH Policy", [""] + opt["company"]["WFH Policy"])
        website = st.text_input("Website", placeholder="https://careers.shopee.co.th")

    company_notes = st.text_area("Company Notes", placeholder="Glassdoor score, culture notes ฯลฯ", height=80)

    st.markdown("---")
    if st.button("🚀 Add to Notion + Rerank", key="btn_manual"):
        j_data = {
            "job_title": job_title,
            "role_tier": role_tier,
            "fit_level": fit_level,
            "apply_status": apply_status,
            "work_location": work_location,
            "salary_min": salary_min if salary_min > 0 else None,
            "salary_max": salary_max if salary_max > 0 else None,
            "linkedin_url": linkedin_url,
            "key_tech_stack": key_tech_stack,
            "gaps": gaps,
            "notes": job_notes,
            "analysis": analysis,
        }
        c_data = {
            "company_name": company_name,
            "company_size": company_size,
            "company_tier": company_tier,
            "industry": industry,
            "location": location,
            "wfh_policy": wfh_policy,
            "website": website,
            "notes": company_notes,
        }
        submit_to_notion(j_data, c_data)
