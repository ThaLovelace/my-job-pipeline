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
JOB_LISTING_DB_ID   = st.secrets.get("JOB_LISTING_DB_ID", "")
OPENROUTER_API_KEY  = st.secrets.get("OPENROUTER_API_KEY", "")
SCRAPERAPI_KEY      = st.secrets.get("SCRAPERAPI_KEY", "")
GROQ_API_KEY        = st.secrets.get("GROQ_API_KEY", "")

RESUME_VERSIONS_DB_ID = st.secrets.get("RESUME_VERSIONS_DB_ID", "a3d45e99-8f7f-4304-a66d-760bb45e556e")
PROJECT_DETAILS_DB_ID = st.secrets.get("PROJECT_DETAILS_DB_ID", "a07cbb74-b720-43b8-98fc-726410d8fe00")
GEMINI_API_KEY         = st.secrets.get("GEMINI_API_KEY", "")

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

# apply_decision (จาก LLM) → status_key สำหรับ Job Listing DB
# ให้ตรงกับ Apply Status ใน Job Pipeline DB (To Apply / On Hold / Pass)
APPLY_DECISION_TO_LISTING_STATUS = {
    "APPLY":     "to_apply",
    "WATCHLIST": "on_hold",
    "PASS":      "pass",
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

def _company_from_url(url):
    """
    พยายาม extract ชื่อบริษัทจาก URL — คืน string หรือ None
    เช่น: careers.shopee.co.th → "Shopee"
          jobs.grab.com → "Grab"
          th.jobsdb.com/job/... → None (job board ไม่ใช่บริษัท)
    """
    JOB_BOARDS = {
        "jobsdb", "jobthai", "linkedin", "indeed", "jobstreet",
        "workday", "lever", "greenhouse", "bamboohr", "smartrecruiters",
        "facebook", "fastwork", "glints", "th.jobsdb",
    }
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # ถ้าเป็น job board → return None
        for board in JOB_BOARDS:
            if board in host:
                return None
        # ตัด www., careers., jobs., th., en. ออก
        parts = host.replace("www.", "").replace("careers.", "").replace("jobs.", "").split(".")
        # ใช้ส่วนแรกที่ไม่ใช่ country code
        SKIP = {"co", "com", "th", "net", "org", "io", "ai", "app", "in", "sg", "my"}
        for part in parts:
            if part and part not in SKIP and len(part) > 2:
                return part.capitalize()
    except Exception:
        pass
    return None


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
            "Platform":     get_select_options(JOB_PIPELINE_DB_ID, "Platform"),
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

# ── Notion page-body block builders ──────────────────────

def _text_chunks(text, size=1999):
    """แบ่ง text เป็น chunks ตาม Notion rich_text limit (2000 chars/block)"""
    text = (text or "").strip()
    return [text[i:i+size] for i in range(0, len(text), size)] or [""]

def _paragraph_blocks(text, size=1999):
    return [
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"text": {"content": chunk}}]}}
        for chunk in _text_chunks(text, size) if chunk.strip()
    ]

def _heading_block(text, level=2):
    htype = f"heading_{level}"
    return {"object": "block", "type": htype,
            "heading_2" if level == 2 else htype: {"rich_text": [{"text": {"content": text}}]}} \
        if level != 2 else \
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": text}}]}}

def _h2(text):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": text}}]}}

def _h3(text):
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"text": {"content": text}}]}}

def _bullet(text):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content": text[:1999]}}]}}

def _callout(text, emoji="💡"):
    blocks_out = []
    chunks = _text_chunks(text)
    blocks_out.append({
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": chunks[0]}}],
            "icon": {"type": "emoji", "emoji": emoji},
        }
    })
    return blocks_out

def _toggle(title, children_blocks):
    """toggle block พร้อม children (รองรับใน create page ได้สูงสุด ~2 ชั้น)"""
    return {
        "object": "block", "type": "toggle",
        "toggle": {
            "rich_text": [{"text": {"content": title[:1999]}}],
            "children": children_blocks,
        }
    }

def _divider():
    return {"object": "block", "type": "divider", "divider": {}}


# ── เรื่องวิเคราะห์ — แยก narrative_analysis เป็นย่อหน้าตาม [1]..[7] ─────
_NARRATIVE_SPLIT_RE = re.compile(r"(?=\[\d+\])")

def _narrative_blocks(narrative_text):
    if not narrative_text:
        return []
    sections = [s.strip() for s in _NARRATIVE_SPLIT_RE.split(narrative_text) if s.strip()]
    blocks_out = []
    if len(sections) > 1:
        for sec in sections:
            blocks_out.extend(_paragraph_blocks(sec))
    else:
        blocks_out.extend(_paragraph_blocks(narrative_text))
    return blocks_out


def build_analysis_blocks(a, max_blocks=95):
    """
    สร้าง Notion page body blocks จาก raw analysis dict (a) — จัด layout สวยงาม:
      📊 AI Analysis (narrative, แยกตาม [1]-[7])
      🎯 Interview Prep — toggle ต่อคำถาม + answer guide ข้างใน, ถามกลับ (bullets), salary script (callout)
      📋 Application Guide — how to apply, form questions, things to prepare (bullets)
      📄 Resume — version + เหตุผล (callout)

    max_blocks: Notion API จำกัด children ต่อ request ที่ ~100 blocks — เผื่อ headroom
    """
    blocks = []

    # ── AI Analysis ──────────────────────────────────────────
    narrative = a.get("narrative_analysis", "")
    if narrative:
        blocks.append(_h2("📊 AI Analysis"))
        blocks.extend(_narrative_blocks(narrative))
        blocks.append(_divider())

    # ── Interview Prep ───────────────────────────────────────
    ip = a.get("interview_prep") or {}
    if ip:
        blocks.append(_h2("🎯 Interview Prep"))

        behavioral = ip.get("behavioral_questions") or []
        if behavioral:
            blocks.append(_h3("Behavioral Questions"))
            for q in behavioral:
                question = (q.get("question") or "").strip()
                answer = (q.get("answer_guide") or "").strip()
                if question:
                    blocks.append(_toggle(f"❓ {question}", _paragraph_blocks(answer) or [_paragraph_blocks(" ")[0]]))

        technical = ip.get("technical_questions") or []
        if technical:
            blocks.append(_h3("Technical Questions"))
            for q in technical:
                question = (q.get("question") or "").strip()
                answer = (q.get("answer_guide") or "").strip()
                if question:
                    blocks.append(_toggle(f"❓ {question}", _paragraph_blocks(answer) or [_paragraph_blocks(" ")[0]]))

        questions_to_ask = ip.get("questions_to_ask") or []
        if questions_to_ask:
            blocks.append(_h3("คำถามถามกลับ Employer"))
            for q in questions_to_ask:
                if q and q.strip():
                    blocks.append(_bullet(q.strip()))

        salary_script = (ip.get("salary_negotiation_script") or "").strip()
        if salary_script:
            blocks.append(_h3("💰 Salary Negotiation Script"))
            blocks.extend(_callout(salary_script, emoji="💰"))
            # ถ้ายาวกว่า 1 chunk ให้เติม paragraph ต่อ
            chunks = _text_chunks(salary_script)
            if len(chunks) > 1:
                for chunk in chunks[1:]:
                    blocks.extend(_paragraph_blocks(chunk))

        blocks.append(_divider())

    # ── Application Guide ────────────────────────────────────
    ag = a.get("application_guide") or {}
    if ag:
        blocks.append(_h2("📋 Application Guide"))

        how_to_apply = (ag.get("how_to_apply") or "").strip()
        if how_to_apply:
            blocks.append(_h3("วิธี Apply"))
            blocks.extend(_paragraph_blocks(how_to_apply))

        form_qs = ag.get("form_questions_to_prepare") or []
        if form_qs:
            blocks.append(_h3("คำถามในฟอร์ม / Screening"))
            for q in form_qs:
                if q and q.strip():
                    blocks.append(_bullet(q.strip()))

        prep = ag.get("things_to_prepare") or []
        if prep:
            blocks.append(_h3("สิ่งที่ต้องเตรียม"))
            for item in prep:
                if item and item.strip():
                    blocks.append(_bullet(item.strip()))

        blocks.append(_divider())

    # ── Resume Version ────────────────────────────────────────
    resume_version = (a.get("resume_version") or "").strip()
    if resume_version:
        blocks.append(_h2("📄 Resume Version"))
        resume_text = f"ใช้: {resume_version}"
        reason = (a.get("resume_reason") or "").strip()
        if reason:
            resume_text += f"\nเหตุผล: {reason}"
        blocks.extend(_callout(resume_text, emoji="📄"))

    # ── Safety cap: Notion จำกัด children ต่อ request ──────────
    if len(blocks) > max_blocks:
        blocks = blocks[:max_blocks]
        blocks.append(_paragraph_blocks("…(เนื้อหาบางส่วนถูกตัด — ดูฉบับเต็มใน analysis text เดิม)")[0])

    return blocks


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
        "Apply Email":     {"rich_text": [{"text": {"content": d.get("apply_email", "")}}]},
        "Date Applied":    {"date": {"start": time.strftime("%Y-%m-%d")}},
    }

    # select fields — skip ถ้าว่าง
    for field, key in [
        ("Role Tier",    "role_tier"),
        ("Fit Level",    "fit_level"),
        ("Apply Status", "apply_status"),
        ("Platform",     "platform"),
    ]:
        v = sel(d.get(key, ""), opt["job"][field])
        if v:
            props[field] = {"select": {"name": v}}

    # ⚠️ WARNING: "๋Job URL" สะกดผิดโดยตั้งใจ — มีอักขระไทย ๋ (mai tri) นำหน้า
    # ห้ามแก้เป็น "Job URL" ตามปกติ เพราะ Notion column สะกดผิดจริงและต้อง map ตรงนี้เท่านั้น
    url_to_save = d.get("job_url") or d.get("linkedin_url")
    if url_to_save:
        props["๋Job URL"] = {"url": url_to_save}

    # ── Score fields (บันทึกเสมอ ถ้ามีค่า) ────────────────────
    if d.get("ai_depth_score") is not None:
        props["AI Depth Score"] = {"number": d["ai_depth_score"]}
    if d.get("ownership_score") is not None:
        props["Ownership Score"] = {"number": d["ownership_score"]}
    if d.get("skill_match_pct") is not None:
        props["Skill Match %"] = {"number": d["skill_match_pct"]}

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
    if "id" not in result:
        return False, result.get("message", "unknown error")

    page_id = result["id"]  # เก็บไว้คืนให้ caller ใช้โดยตรง (ไม่ต้อง query เดาจาก title)

    # ── Post-write verification: เช็คว่า Apply Status ไม่ว่าง ──────────────────
    # bug เดิม: sel() silent-skip ถ้า fuzzy_match คืน falsy → Apply Status ว่างเงียบๆ
    # ถ้าว่าง → PATCH เติม fallback "⏸️ On Hold" (ดีกว่าปล่อยว่าง)
    try:
        verify_res = requests.get(
            f"https://api.notion.com/v1/pages/{page_id}", headers=HEADERS
        )
        verify_props = verify_res.json().get("properties", {})
        apply_status_val = (
            (verify_props.get("Apply Status") or {})
            .get("select", {})
            .get("name", "")
        )
        if not apply_status_val.strip():
            # Apply Status ว่าง — เติม fallback
            requests.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=HEADERS,
                json={"properties": {"Apply Status": {"select": {"name": "⏸️ On Hold"}}}}
            )
    except Exception:
        pass  # verification ล้มเหลว ก็ยังถือว่า create สำเร็จ

    return True, page_id

# ── Job Listing DB helpers ───────────────────────────────

def _get_listing_status_options():
    """ดึง select options จาก Job Listing DB สำหรับ fuzzy match"""
    if not JOB_LISTING_DB_ID:
        return []
    try:
        res = requests.get("https://api.notion.com/v1/databases/" + JOB_LISTING_DB_ID, headers=HEADERS)
        props = res.json().get("properties", {})
        # หา property ที่เป็น select หรือ status สำหรับ status column
        for name in ("Status", "status", "สถานะ"):
            prop = props.get(name, {})
            opts = prop.get("select", {}).get("options", []) or prop.get("status", {}).get("options", [])
            if opts:
                return [o["name"] for o in opts]
    except Exception:
        pass
    return []

@st.cache_data(ttl=300, show_spinner=False)
def get_listing_property_map(_bust=0):
    """
    ดึงชื่อ property จริงจาก Job Listing DB และ map กับชื่อ canonical ที่โค้ดใช้
    คืน dict เช่น:
      { "status": "Status", "name": "Name", "url": "URL",
        "company": "Company", "jd": "JD", "notes": "Notes",
        "status_type": "select"  # หรือ "status" (Notion status type) }
    """
    if not JOB_LISTING_DB_ID:
        return {}
    try:
        res = requests.get("https://api.notion.com/v1/databases/" + JOB_LISTING_DB_ID, headers=HEADERS)
        props = res.json().get("properties", {})
    except Exception:
        return {}

    result = {}

    # helper: หาชื่อ property จริงจาก candidates (fuzzy)
    def find_prop(candidates, types=None):
        prop_names = list(props.keys())
        for c in candidates:
            # exact match ก่อน
            if c in props:
                p = props[c]
                if types is None or p.get("type") in types:
                    return c, p.get("type")
            # fuzzy match
            matched = fuzzy_match(c, prop_names, cutoff=0.7)
            if matched and matched in props:
                p = props[matched]
                if types is None or p.get("type") in types:
                    return matched, p.get("type")
        return None, None

    # Status (select หรือ status type)
    name, ptype = find_prop(["Status", "status", "สถานะ"], types=["select", "status"])
    if name:
        result["status"] = name
        result["status_type"] = ptype

    # title field (Name ของ row)
    name, _ = find_prop(["Name", "name", "ชื่อ", "Job", "Title"], types=["title"])
    if name:
        result["name"] = name

    # URL
    name, _ = find_prop(["URL", "url", "Link", "link", "ลิงก์"], types=["url"])
    if name:
        result["url"] = name

    # Company
    name, _ = find_prop(["Company", "company", "บริษัท"], types=["rich_text", "title"])
    if name:
        result["company"] = name

    # JD
    name, _ = find_prop(["JD", "jd", "Job Description", "Description", "รายละเอียด"], types=["rich_text"])
    if name:
        result["jd"] = name

    # Notes
    # ⚠️ WARNING: field จริงใน Notion ชื่อ "ืnotes" (มีสระ ื นำหน้าผิดปกติ)
    # fuzzy_match จะ handle ได้ ห้าม hardcode "notes" ธรรมดา — จะหาไม่เจอ
    name, _ = find_prop(["Notes", "notes", "Note", "note", "หมายเหตุ", "Error", "error"], types=["rich_text"])
    if name:
        result["notes"] = name

    return result

@st.cache_data(ttl=300, show_spinner=False)
def get_listing_status_map():
    """คืน dict mapping ชื่อ canonical → ชื่อจริงใน Notion (fuzzy)"""
    options = _get_listing_status_options()
    return {
        # สถานะปกติ
        "consider":    fuzzy_match("Consider",    options, cutoff=0.5) or "Consider",
        # สถานะ "เสร็จแล้ว" — แยกตามผลตัดสินของ Job Pipeline (To Apply / On Hold / Pass)
        # ใช้ชื่อเดียวกับ Apply Status ใน Job Pipeline DB เพื่อให้ดูสถานะตรงกันทั้ง 2 DB
        "to_apply":    fuzzy_match("To Apply",    options, cutoff=0.5) or "To Apply",
        "on_hold":     fuzzy_match("On Hold",     options, cutoff=0.5) or "On Hold",
        "pass":        fuzzy_match("Pass",        options, cutoff=0.5) or "❌ Pass ไม่เอา",
        # error states — ออกจาก queue แต่ยังไม่เสร็จ
        "fetch_error":  fuzzy_match("Fetch Error",   options, cutoff=0.5) or "Fetch Error",
        "llm_error":    fuzzy_match("LLM Error",     options, cutoff=0.5) or "LLM Error",
        "need_company": fuzzy_match("Need Company",  options, cutoff=0.5) or "Need Company",
    }

def upsert_job_listing(url, *, job_title="", company_name="", jd_raw="", status_key="consider", error_note=""):
    """
    สร้างหรืออัปเดต row ใน Job Listing DB
    ใช้ชื่อ property จริงจาก get_listing_property_map() — ไม่ hardcode ชื่อ field ใดๆ
    """
    if not JOB_LISTING_DB_ID:
        return None, "ไม่มี JOB_LISTING_DB_ID"

    pmap = get_listing_property_map()
    # fallback ถ้า detect ไม่ได้ — ใช้ชื่อที่รู้จากผู้ใช้โดยตรง
    if not pmap.get("status"):
        pmap = {
            "status": "status", "status_type": "select",
            "name": "Name", "url": "URL",
            "company": "Company", "jd": "JD",
        }

    status_map  = get_listing_status_map()
    status_name = status_map.get(status_key, status_key)

    # ── ค้นหา row เดิมด้วย URL ──────────────────────────────
    existing_id = None
    url_prop = pmap.get("url")
    if url_prop:
        try:
            res = requests.post(
                f"https://api.notion.com/v1/databases/{JOB_LISTING_DB_ID}/query",
                headers=HEADERS,
                json={"filter": {"property": url_prop, "url": {"equals": url}}}
            )
            rows = res.json().get("results", [])
            if rows:
                existing_id = rows[0]["id"]
        except Exception:
            pass

    # ── สร้าง properties payload ด้วยชื่อ property จริง ─────
    props = {}

    # Status — always written
    status_prop = pmap.get("status")
    status_type = pmap.get("status_type", "select")
    if status_prop:
        if status_type == "status":
            props[status_prop] = {"status": {"name": status_name}}
        else:
            props[status_prop] = {"select": {"name": status_name}}

    # Name / title — only write if caller provided a value (avoid blanking existing title)
    name_prop = pmap.get("name")
    if name_prop and job_title:
        props[name_prop] = {"title": [{"text": {"content": job_title[:200]}}]}

    # URL — only write if provided
    if url_prop and url:
        props[url_prop] = {"url": url}

    # Company — only write if provided
    company_prop = pmap.get("company")
    if company_prop and company_name:
        props[company_prop] = {"rich_text": [{"text": {"content": company_name[:200]}}]}

    # JD → เขียนทั้ง 2 ที่:
    #   1. property "JD" (rich_text, ตัด 2000 chars) — ใช้ตอน retry (skip fetch, read jd_text)
    #   2. page body blocks — ให้คนอ่านสบายตา ไม่ต้องตัด 2000 chars
    jd_prop = pmap.get("jd")
    if jd_prop and jd_raw:
        props[jd_prop] = {"rich_text": [{"text": {"content": jd_raw[:2000]}}]}

    jd_blocks = []
    if jd_raw:
        jd_blocks.append({
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"text": {"content": "📄 Job Description"}}]}
        })
        for chunk in [jd_raw[i:i+1999] for i in range(0, len(jd_raw), 1999)]:
            jd_blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": chunk}}]}
            })

    # Notes — เขียนเฉพาะ error states เพื่อบอกสาเหตุ
    notes_prop = pmap.get("notes")
    notes_parts = []
    if status_key not in ("consider", "to_apply", "on_hold", "pass"):
        notes_parts.append(f"[{status_name}]")
    if error_note:
        notes_parts.append(error_note[:460])
    if notes_prop and notes_parts:
        props[notes_prop] = {"rich_text": [{"text": {"content": " — ".join(notes_parts)[:500]}}]}

    if not props:
        return None, f"ไม่มี property ที่ map ได้เลย — pmap={pmap}"

    # ── ส่ง request ──────────────────────────────────────────
    try:
        if existing_id:
            res = requests.patch(
                f"https://api.notion.com/v1/pages/{existing_id}",
                headers=HEADERS,
                json={"properties": props}
            )
            result = res.json()
            if "id" not in result:
                notion_msg = result.get("message", "unknown error")
                return None, f"{notion_msg} | pmap={pmap}"
            page_id = result["id"]

            # append JD blocks (replace ไม่ได้ใน Notion → append ต่อท้าย)
            # ลบ blocks เก่าก่อนถ้ามีอยู่แล้ว เพื่อไม่ให้ซ้ำ
            if jd_blocks:
                _replace_page_blocks(page_id, jd_blocks)

        else:
            # สร้างใหม่ — ต้องมี title เสมอ
            if name_prop and name_prop not in props:
                props[name_prop] = {"title": [{"text": {"content": (job_title or url)[:200]}}]}
            payload = {"parent": {"database_id": JOB_LISTING_DB_ID}, "properties": props}
            if jd_blocks:
                payload["children"] = jd_blocks
            res = requests.post(
                "https://api.notion.com/v1/pages",
                headers=HEADERS,
                json=payload
            )
            result = res.json()
            if "id" not in result:
                notion_msg = result.get("message", "unknown error")
                return None, f"{notion_msg} | pmap={pmap}"
            page_id = result["id"]

        return page_id, None

    except Exception as e:
        return None, str(e)


def _get_page_body_text(page_id, max_chars=6000):
    """
    ดึงข้อความจาก page body (blocks) — ใช้เป็น fallback เมื่อ property "JD" ว่าง
    แต่ผู้ใช้แปะ JD ไว้ในหน้า page เอง (paragraph/heading blocks)
    """
    try:
        text_parts = []
        url = f"https://api.notion.com/v1/blocks/{page_id}/children"
        params = {"page_size": 100}
        while True:
            res = requests.get(url, headers=HEADERS, params=params)
            data = res.json()
            for b in data.get("results", []):
                btype = b.get("type", "")
                content = b.get(btype, {})
                rich = content.get("rich_text", [])
                if rich:
                    line = "".join(rt.get("plain_text", "") for rt in rich)
                    if line.strip():
                        text_parts.append(line)
            if not data.get("has_more"):
                break
            params["start_cursor"] = data["next_cursor"]
        return "\n".join(text_parts)[:max_chars]
    except Exception:
        return ""


def _replace_page_blocks(page_id, new_blocks):
    """ลบ blocks ทั้งหมดใน page แล้ว append ใหม่"""
    try:
        # ดึง blocks เก่า
        res = requests.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=HEADERS
        )
        old_blocks = res.json().get("results", [])
        # ลบทีละ block
        for b in old_blocks:
            requests.delete(
                f"https://api.notion.com/v1/blocks/{b['id']}",
                headers=HEADERS
            )
        # append blocks ใหม่
        requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=HEADERS,
            json={"children": new_blocks}
        )
    except Exception:
        pass  # ถ้า replace ไม่ได้ก็ไม่เป็นไร — properties ยังอัปเดตแล้ว


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
        props = job["properties"]
        fit   = props.get("Fit Level", {}).get("select") or {}
        fit_s = FIT_SCORE.get(fit.get("name", "").lower(), 0)

        ai_depth  = (props.get("AI Depth Score", {}).get("number") or 0)
        ownership = (props.get("Ownership Score", {}).get("number") or 0)
        skill_match = (props.get("Skill Match %", {}).get("number") or 0)

        # Company Tier: extract number from "Tier1/Tier2/Tier3" or "Level1/Level2/Level3"
        import re as _re
        role_tier_raw = (props.get("Role Tier", {}).get("select") or {}).get("name", "")
        tier_m = _re.search(r"[123]", role_tier_raw)
        company_tier_val = (4 - int(tier_m.group())) if tier_m else 0  # Tier1=3, Tier2=2, Tier3=1

        # Salary penalty: ถ้า salary_max < 80000 หัก 1 คะแนน
        salary_max = props.get("Salary Max", {}).get("number") or 0
        salary_penalty = 1 if (salary_max > 0 and salary_max < 80000) else 0

        # Formula: fit×3 + ai_depth×2 + ownership×2 + company_tier×1 + skill_match/20 - salary_penalty
        score = (fit_s * 3) + (ai_depth * 2) + (ownership * 2) + company_tier_val + (skill_match / 20) - salary_penalty
        return -score  # negative for ascending sort

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

# ── UI ───────────────────────────────────────────────────────
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
        ok, page_id_or_err = create_job(job_data, company_id, opt)
        if not ok:
            st.error(f"❌ สร้าง job ไม่สำเร็จ: {page_id_or_err}")
            return
        log(f"✅ เพิ่ม job: {job_title} @ {company_name}")

        log("\n📊 Reranking jobs...")
        rerank_all_jobs(opt, log)
        log("\n🎉 เสร็จแล้ว!")

    st.success("เพิ่มลง Notion สำเร็จแล้วค่ะ! ✨")
    with st.expander("ดู log"):
        st.code("\n".join(log_lines))


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

    # ── clean role_tier: เอาแค่ Tier1/Tier2/Tier3 ──────────────
    import re as _re
    raw_tier = a.get("role_tier", "")
    tier_reason = a.get("role_tier_reason", "")
    tier_match = _re.search(r"(Tier\s*[123])", raw_tier, _re.IGNORECASE)
    if tier_match:
        clean_tier = tier_match.group(1).replace(" ", "")
        if not tier_reason:
            leftover = _re.sub(r"Tier\s*[123]\s*[—\-–]?\s*", "", raw_tier).strip()
            if leftover:
                tier_reason = leftover
    else:
        clean_tier = raw_tier

    # merge tier_reason เข้า notes
    base_notes = a.get("notes", "")
    notes_parts_list = [p for p in [base_notes, tier_reason] if p]

    # ถ้า salary เป็นค่าประมาณ (ไม่ใช่ระบุใน JD) → ใส่หมายเหตุไว้
    if a.get("salary_is_estimated") and (a.get("salary_min") or a.get("salary_max")):
        est_reason = a.get("salary_estimate_reason", "")
        est_note = "💰 เงินเดือนเป็นค่าประมาณ (JD ไม่ได้ระบุ)"
        if est_reason:
            est_note += f": {est_reason}"
        notes_parts_list.append(est_note)

    merged_notes = " | ".join(notes_parts_list)[:500]

    # ปรับเหลือแค่ job_url หลักชิ้นเดียว
    job_data = {
        "job_title":       a.get("job_title", "Unknown"),
        "role_tier":       clean_tier,
        "fit_level":       a.get("fit_level", "medium"),
        "apply_status":    {
            "APPLY":     "To Apply",
            "WATCHLIST": "On Hold",
            "PASS":      "❌ Pass ไม่เอา",
        }.get(a.get("apply_decision", "").upper(), "No Apply Status"),
        "work_location":   a.get("work_location", ""),
        "salary_min":      a.get("salary_min") or None,
        "salary_max":      a.get("salary_max") or None,
        "job_url":         job_url,
        "apply_email":     a.get("apply_email", ""),
        "platform":        a.get("platform", ""),
        "key_tech_stack":  a.get("key_tech_stack", ""),
        "gaps":            a.get("gaps", ""),
        "notes":           merged_notes,
        "analysis":        "\n\n".join(parts),
        "ai_depth_score":  a.get("ai_depth_score") or None,
        "ownership_score": a.get("ownership_score") or None,
        "skill_match_pct": a.get("my_skill_match_pct") or None,
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


# ── Tabs ─────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📝 Paste Python Dict (Fast)", "✍️ Manual Form", "🤖 Batch Analyze", "⚙️ Admin"])

# --- TAB 1 ---
with tab1:
    st.markdown(
        "วางได้ 2 แบบค่ะ:\n"
        "- **ผลลัพธ์ AI analysis (JSON)** ทั้งก้อน — ระบบจะแปลงเป็น job_data/company_data ให้อัตโนมัติ\n"
        "- **Python dict** ที่มีตัวแปร `job_data` และ `company_data`"
    )
    raw_code = st.text_area(
        "Paste Code Here",
        height=450,
        placeholder='วาง JSON ผลลัพธ์ AI analysis ทั้งก้อน หรือ\n\njob_data = {\n  "job_title": "...", \n  ...\n}\n\ncompany_data = {\n  ...\n}',
        label_visibility="collapsed"
    )

    if st.button("🚀 Add to Notion + Rerank", key="btn_code"):
        if not raw_code.strip():
            st.error("กรุณาวางโค้ดก่อนค่ะ")
            st.stop()

        j_data, c_data = None, None
        text = raw_code.strip()

        # ── ลอง 1: แปะมาเป็น JSON (ผลลัพธ์ AI analysis ทั้งก้อน หรือ {job_data, company_data}) ──
        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", text)
            cleaned = re.sub(r"```\s*$", "", cleaned).strip()
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                if "job_data" in parsed and "company_data" in parsed:
                    # รูปแบบ {"job_data": {...}, "company_data": {...}}
                    j_data, c_data = parsed["job_data"], parsed["company_data"]
                elif "job_title" in parsed or "narrative_analysis" in parsed:
                    # ผลลัพธ์ AI analysis ดิบ — แปลงด้วย analysis_to_notion_dicts
                    job_url = parsed.get("apply_url") or parsed.get("job_url", "")
                    j_data, c_data = analysis_to_notion_dicts(parsed, job_url)
        except json.JSONDecodeError:
            pass

        # ── ลอง 2: ถ้ายังไม่ได้ → ลองรันเป็น Python dict (job_data = {...}; company_data = {...}) ──
        local_vars = {}
        if j_data is None or c_data is None:
            try:
                exec(text, {}, local_vars)
                j_data = local_vars.get("job_data")
                c_data = local_vars.get("company_data")
            except Exception as e:
                st.error(f"❌ เกิดข้อผิดพลาดในการอ่านโค้ด: {e}")
                st.stop()

        if not isinstance(j_data, dict) or not isinstance(c_data, dict):
            st.error("❌ โค้ดไม่ถูกต้อง: ต้องเป็น JSON ผลลัพธ์ AI analysis, JSON ที่มี job_data/company_data, "
                     "หรือ Python dict ที่มีตัวแปร `job_data` และ `company_data` ค่ะ")
            st.stop()

        # ── ดึง platform จาก google_sheet_data (ถ้ามี) แล้วใส่เข้า job_data ──
        # รองรับ output format เก่าที่ยังมี google_sheet_data อยู่
        gsd = local_vars.get("google_sheet_data")
        if isinstance(gsd, dict) and gsd.get("platform") and not j_data.get("platform"):
            j_data["platform"] = gsd["platform"]

        # ── new_resume_version_data (Path 3) → สร้างใน Notion Resume Versions DB อัตโนมัติ ──
        nrv = local_vars.get("new_resume_version_data")
        if isinstance(nrv, dict) and nrv.get("version_id"):
            with st.spinner(f"🆕 กำลังสร้าง Resume Version '{nrv.get('version_id')}' ใน Notion..."):
                try:
                    nrv_res = requests.post(
                        "https://api.notion.com/v1/pages",
                        headers=HEADERS,
                        json={
                            "parent": {"database_id": RESUME_VERSIONS_DB_ID},
                            "properties": {
                                "version_id":        {"title": [{"text": {"content": nrv.get("version_id", "")}}]},
                                "tone_label":        {"rich_text": [{"text": {"content": nrv.get("tone_label", "")[:2000]}}]},
                                "use_when":          {"rich_text": [{"text": {"content": nrv.get("use_when", "")[:2000]}}]},
                                "use_with":          {"rich_text": [{"text": {"content": nrv.get("use_with", "")[:2000]}}]},
                                "summary_paragraph": {"rich_text": [{"text": {"content": nrv.get("summary_paragraph", "")[:2000]}}]},
                                "headline":          {"rich_text": [{"text": {"content": nrv.get("headline", "")[:2000]}}]},
                                "skills_emphasis":   {"rich_text": [{"text": {"content": nrv.get("skills_emphasis", "")[:2000]}}]},
                                "projects_order":    {"rich_text": [{"text": {"content": str(nrv.get("projects_order", ""))[:2000]}}]},
                            }
                        }
                    )
                    if "id" in nrv_res.json():
                        st.success(f"✅ สร้าง Resume Version **{nrv.get('version_id')}** ใน Notion แล้วค่ะ — อย่าลืมสร้าง Canva แล้วใส่ link กลับมานะคะ")
                    else:
                        st.warning(f"⚠️ สร้าง Resume Version ไม่สำเร็จ: {nrv_res.json().get('message', 'unknown error')}")
                except Exception as e:
                    st.warning(f"⚠️ สร้าง Resume Version ไม่สำเร็จ: {e}")

        submit_to_notion(j_data, c_data)


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


@st.cache_data(ttl=300, show_spinner=False)
def load_resume_versions_summary():
    """
    Query Resume Versions DB สดจาก Notion — cache 5 นาที
    คืน string พร้อมต่อเข้า FIT_ANALYSIS_PROMPT แทน hardcoded CANDIDATE_PROFILE block เดิม
    ชื่อ version จริง: Thapanee_AIEngineer, Thapanee_AIEngineer_Research, ฯลฯ
    """
    try:
        res = requests.post(
            f"https://api.notion.com/v1/databases/{RESUME_VERSIONS_DB_ID}/query",
            headers=HEADERS,
            json={"page_size": 50}
        )
        results = res.json().get("results", [])
        if not results:
            return "(ไม่พบ Resume Versions ใน DB — ใช้ judgment เอง)"

        lines = []
        for page in results:
            p = page.get("properties", {})
            def txt(key):
                arr = (p.get(key) or {}).get("rich_text", [])
                return arr[0].get("plain_text", "").strip() if arr else ""
            def sel(key):
                return ((p.get(key) or {}).get("select") or {}).get("name", "").strip()
            def ttl(key):
                arr = (p.get(key) or {}).get("title", [])
                return arr[0].get("plain_text", "").strip() if arr else ""

            vid    = ttl("version_id") or txt("version_id")
            tone   = sel("tone_label") or txt("tone_label")
            when   = txt("use_when")
            with_  = txt("use_with")
            skills = txt("skills_emphasis")

            if not vid:
                continue

            block = f"VERSION {vid}"
            if tone:
                block += f" — {tone}"
            if with_:
                block += f"\n  ใช้กับ: {with_}"
            if when:
                block += f"\n  ใช้เมื่อ: {when}"
            if skills:
                block += f"\n  เน้น: {skills}"
            lines.append(block)

        return "\n\n".join(lines) if lines else "(Resume Versions DB ว่าง)"
    except Exception as e:
        return f"(โหลด Resume Versions ไม่สำเร็จ: {e})"


def add_jd_field_to_job_listings_db():
    """
    เพิ่ม field "JD" (rich_text) ใน Job Listings DB ถ้ายังไม่มี
    ขั้น 2 ของ Runbook — รันครั้งเดียว
    """
    if not JOB_LISTING_DB_ID:
        return False, "ไม่มี JOB_LISTING_DB_ID"
    try:
        # เช็คก่อนว่ามี field "JD" แล้วหรือยัง
        res = requests.get(
            f"https://api.notion.com/v1/databases/{JOB_LISTING_DB_ID}",
            headers=HEADERS
        )
        props = res.json().get("properties", {})
        existing_names = [k.lower() for k in props.keys()]
        if "jd" in existing_names:
            return True, "มี field JD อยู่แล้ว"

        # เพิ่ม field "JD" ผ่าน PATCH database
        patch_res = requests.patch(
            f"https://api.notion.com/v1/databases/{JOB_LISTING_DB_ID}",
            headers=HEADERS,
            json={"properties": {"JD": {"rich_text": {}}}}
        )
        if patch_res.status_code in (200, 204):
            return True, "เพิ่ม field JD สำเร็จ"
        return False, f"HTTP {patch_res.status_code}: {patch_res.text[:200]}"
    except Exception as e:
        return False, str(e)


def backfill_apply_status():
    """
    ขั้น 1 ของ Runbook — backfill Apply Status ที่ว่างใน Job Pipeline DB
    Logic: ถ้า Apply Status ว่าง → ดู Notes/analysis เพื่อ derive
    - ถ้ามี notes บ่งชี้ว่า applied / interviewed → "📤 Applied"
    - ไม่มีข้อมูล → "🔍 To Apply" (ค่า default ที่ปลอดภัยที่สุด)
    คืน (จำนวนที่แก้, list of errors)
    """
    if not JOB_PIPELINE_DB_ID:
        return 0, ["ไม่มี JOB_PIPELINE_DB_ID"]

    try:
        # Query ทุก page ที่ Apply Status ว่าง
        results = []
        payload = {
            "filter": {"property": "Apply Status", "select": {"is_empty": True}},
            "page_size": 100
        }
        while True:
            res = requests.post(
                f"https://api.notion.com/v1/databases/{JOB_PIPELINE_DB_ID}/query",
                headers=HEADERS, json=payload
            )
            data = res.json()
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            payload["start_cursor"] = data["next_cursor"]

        fixed = 0
        errors = []
        for page in results:
            page_id = page["id"]
            props   = page.get("properties", {})

            # กรอง instruction-prompt page ออก
            title_arr = (props.get("Job Title") or {}).get("title", [])
            title = title_arr[0].get("plain_text", "") if title_arr else ""
            if any(kw in title for kw in ["เตรียมใบสมัคร", "INSTRUCTION", "instruction"]):
                continue

            # Derive status จาก notes
            notes_arr = (props.get("Notes") or {}).get("rich_text", [])
            notes_txt = notes_arr[0].get("plain_text", "").lower() if notes_arr else ""
            applied_keywords = ["applied", "สมัครแล้ว", "interviewed", "interview", "สัมภาษณ์", "offer"]
            if any(kw in notes_txt for kw in applied_keywords):
                new_status = "📤 Applied"
            else:
                new_status = "🔍 To Apply"

            patch_res = requests.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=HEADERS,
                json={"properties": {"Apply Status": {"select": {"name": new_status}}}}
            )
            if patch_res.status_code in (200, 204):
                fixed += 1
            else:
                errors.append(f"{title[:40]}: HTTP {patch_res.status_code}")

        return fixed, errors
    except Exception as e:
        return 0, [str(e)]


# ── TAB 3: Batch Analyze ─────────────────────────────────
CANDIDATE_PROFILE = """
═══════════════════════════════════════════
WHO I AM — THAPANEE CHAIPRAPHA (ทับทิม)
═══════════════════════════════════════════
Fresh grad (May 2026), Thammasat University — Software Engineering (CS)
งานแรก — เปิดรับกว้าง แต่ prefer AI/Tech roles ที่ build ของจริง

── HARD SKILLS ──────────────────────────
Proficient: Python, JavaScript, Java, React.js, FastAPI, Node.js, SQL, MongoDB, Git, Figma,
  LLM API (Gemini, OpenAI-compatible), Prompt Engineering,
  Vision Transformers (ViT-B/16), Deep Learning, Grad-CAM (Explainable AI)
Familiar: Next.js, TypeScript, Tailwind CSS, Docker, Power BI, Excel VBA

── KEY PROJECTS ─────────────────────────
- dCDT (Senior Project, Solo) — Medical AI screening app
    96.14% accuracy | FastAPI + Next.js + ViT-B/16 + Grad-CAM heatmaps
    → คนทำคนเดียวตั้งแต่ research → model → backend → frontend → deploy
    → เน้น explainability เพราะ medical staff ต้องตรวจสอบ AI ได้
- MyGPT — Full-stack LLM web app (Production deployed)
    React + Node.js + Gemini API + JWT auth + credit system + Vercel
- Keeppook — Android finance tracker
    Java + Gemini API + caching layer; designed around real UX pain points
- Freelance UX/UI — 6 projects, 5-star rating, Fastwork

── SALARY & LOGISTICS ───────────────────
Target: 35K–45K THB | Hard floor: 30K
WFH preferred; Hybrid OK; On-site ยอมรับได้ถ้างานดีมากพอ

── LOCATION & WORK ARRANGEMENT ──────────────
ที่อยู่ตอนนี้: รังสิต (ปทุมธานี) — ย้ายได้ภายใน ~1 เดือน
เปิดรับงาน 3 รูปแบบ เรียงตามความยืดหยุ่น:

1. REMOTE / WFH เต็มเวลา → ดีที่สุด (อยู่ที่ไหนก็ได้)
2. กรุงเทพ/ปริมณฑล → โอเคถ้างานดีพอ (เงินดี + น่าสนใจ + โอกาสโต)
   แต่ต้องบวกค่าครองชีพกรุงเทพ (~5,000-8,000 บาท/เดือน) เข้าไปในการประเมิน
   เช่น งาน 35k กรุงเทพ จริงๆ เหลือเท่ากับ ~27-30k ถ้าเทียบกับเชียงใหม่
3. เชียงใหม่ → โอเค ถ้าเงินเดือน ≥ 28,000 บาท (ประหยัดค่าที่พักได้มาก)
   หรือ Hybrid/Remote บางส่วนก็นับ

Scoring guide สำหรับ location:
- งาน Remote/WFH เต็มเวลา: +1 คะแนน fit เสมอ
- งาน On-site กรุงเทพ salary < 35k: fit ลดลง 1 ระดับ (เพราะค่าครองชีพกิน margin)
- งาน On-site เชียงใหม่ salary ≥ 28k: นับเป็น acceptable เทียบเท่า ~35k กรุงเทพ
- งาน On-site เชียงใหม่ salary < 28k: PASS (ต่ำกว่า floor จริงแม้ค่าครองชีพถูก)

── ROLE TYPES ที่เปิดรับ ──────────────────────
Primary: AI Engineer, Software Engineer, Full-Stack Developer, ML Engineer
Secondary (เพิ่มใหม่):
  - UX Engineer / Design Engineer (ต้องทำ code ได้ ไม่ใช่แค่ Figma)
  - Product Designer ที่ต้องการ technical background
  - Frontend Developer ที่มี UX component
  เงื่อนไข: ต้องมี creative input จริง (ไม่ใช่แค่ implement spec ของคนอื่น)
  และต้องไม่ใช่ Pure QA หรือ Pure Graphic Design เท่านั้น

── WHAT I THRIVE IN (Green flags) ───────
✓ Real users, big problems — งานที่ impact คนจริงๆ ไม่ใช่ internal tool ที่ไม่มีคนใช้
✓ Design my own solution — ไม่ใช่แค่ implement spec ที่คนอื่นคิดมาให้
✓ High ownership — รับผิดชอบ feature / product ตั้งแต่ต้นจนจบ
✓ Fast learning culture — ทีมที่ ship เร็ว ไม่ติด process หนัก
✓ No bureaucracy — ไม่มี layer approval 5 ชั้น, ตัดสินใจได้จริง
✓ AI ที่เป็น core product — ไม่ใช่แค่ feature ประดับ
✓ ทีมเล็ก-กลาง — อยากเห็น impact ของงานตัวเองชัดๆ

── DEALBREAKERS (Pass ทันที) ────────────
✗ เงินต่ำกว่า 30K
✗ Pure QA / Testing role
✗ Implement ตาม spec เท่านั้น ไม่มี creative input
✗ AI washing — บริษัทบอกว่าทำ AI แต่จริงๆ แค่ใช้ ChatGPT
✗ บริษัทไม่มั่นคง / burn rate สูงผิดปกติ / ไม่มี revenue จริง
✗ On-site 5 วัน + งานไม่ได้พิเศษมากพอ

── RESUME VERSIONS & WHEN TO USE ────────
{resume_versions_block}
═══════════════════════════════════════════
"""

ANALYSIS_PROMPT = """
คุณคือ career advisor อาวุโสที่รู้จัก Thapanee (ทับทิม) ดีมาก
วิเคราะห์ JD ด้านล่างให้เธออย่างตรงไปตรงมา เหมือนเพื่อนที่ทำงาน HR มาบอก

══ CANDIDATE PROFILE ══
{profile}

══ JD ที่ต้องวิเคราะห์ ══
{jd_text}

══ วิธีคิดก่อน output ══
ก่อน output JSON ให้คิดผ่าน 4 ข้อนี้ในใจก่อน (ไม่ต้องเขียนออกมา):
1. บริษัทนี้ทำ AI จริงหรือ AI washing? — ดูจาก JD ว่า AI เป็น core หรือแค่ buzzword
2. งานนี้ให้ ownership จริงหรือเปล่า? — design solution เองได้ หรือแค่ implement spec?
3. culture fit กับทับทิมไหม? — fast learning, no bureaucracy, real impact?
4. resume version ไหนเหมาะ? — match tone ของบริษัทและสิ่งที่ JD เน้น

══ SCORING GUIDE ══
fit_level:
  high        = skill match ≥70% + culture fit + ไม่มี dealbreaker
  medium-high = skill match ≥60% + culture fit หรือ skill match ≥70% แต่ culture มีข้อกังวล
  medium      = skill match ≥50% หรือ culture fit แต่มีช่องว่างพอสมควร
  low-medium  = skill match <50% หรือมี dealbreaker 1 ข้อ
  low         = มี dealbreaker หลายข้อ หรือ mismatch ชัดเจน

ai_depth_score (1-5):
  5 = AI เป็น core product, ต้องทำ model / agent / production AI จริง
  4 = AI สำคัญมาก มี engineering depth
  3 = AI ใช้อยู่แต่ไม่ใช่ core
  2 = AI แค่ tool ประกอบ
  1 = แทบไม่มี AI / AI washing

ownership_score (1-5):
  5 = design + build + ship เอง, end-to-end ownership
  4 = มี ownership สูง มีอิสระในการตัดสินใจ
  3 = ปานกลาง มี spec แต่ยืดหยุ่นได้
  2 = ส่วนใหญ่ implement ตาม spec
  1 = pure execution, ไม่มี creative input

apply_decision logic:
  APPLY     = fit_level high/medium-high + ไม่มี dealbreaker + ai_depth≥3 + ownership≥3
  WATCHLIST = fit_level medium หรือ low-medium ที่ยังมีข้อดีชัดเจน หรือสงสัยระหว่าง PASS กับ WATCHLIST
              → ถ้าไม่แน่ใจ ให้เลือก WATCHLIST เสมอ (false PASS แย่กว่า false WATCHLIST)
              → fit_level low-medium → WATCHLIST ไม่ใช่ PASS (ยกเว้นมี dealbreaker ชัดเจนจริงๆ)
  PASS      = มี dealbreaker ชัดเจนมาก (เงินต่ำกว่า 30k ชัวร์ / Pure QA role ชัวร์ /
              On-site 5 วัน + งานไม่พิเศษเลย ชัวร์) หรือ fit_level = low เท่านั้น

══ OUTPUT FORMAT ══
ตอบกลับเป็น JSON เท่านั้น ห้ามมี markdown backticks หรือข้อความอื่นนอก JSON:

{{
  "job_title": "ชื่อตำแหน่งจาก JD",
  "company_name": "ชื่อบริษัท",
  "role_tier": "Tier1/Tier2/Tier3",
  "role_tier_reason": "เหตุผล 1 ประโยคว่าทำไมถึงเป็น Tier นี้",
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
  "gap_skills": ["skill ที่ขาดจริงๆ ไม่ใช่แค่ nice-to-have"],
  "resume_version": "ชื่อ version จาก CANDIDATE PROFILE ที่ match ที่สุด (เช่น Thapanee_AIEngineer ฯลฯ — ต้องเป็นชื่อที่มีจริงใน CANDIDATE PROFILE เท่านั้น)",
  "resume_reason": "เหตุผล 1-2 ประโยคว่าทำไม version นี้ถึง match tone ของบริษัทนี้",
  "apply_decision": "APPLY/WATCHLIST/PASS",
  "apply_url": "ลิงค์สมัครโดยตรงจาก JD ถ้าไม่มีให้ใส่ค่าว่าง",
  "apply_email": "อีเมลสำหรับส่งใบสมัคร ถ้ามีใน JD ถ้าไม่มีให้ใส่ค่าว่าง",
  "platform": "ชื่อแพลตฟอร์มที่โพสต์งานนี้ เช่น LinkedIn/JobThai/Indeed/Company Website/อื่นๆ ถ้าไม่ทราบให้ใส่ค่าว่าง",
  "company_size": "startup/sme/enterprise",
  "company_tier": "Level1/Level2/Level3",
  "company_tier_reason": "เหตุผลสั้นๆ",
  "industry": "อุตสาหกรรม",
  "location": "Bangkok, Thailand",
  "website": "",
  "gaps": "gap หลัก max 80 chars",
  "notes": "สิ่งที่ต้องรู้ก่อน apply max 100 chars",
  "narrative_analysis": "วิเคราะห์ละเอียดภาษาไทย ครอบคลุม: [1] บริษัทเป็นใคร ทำอะไร น่าเชื่อถือแค่ไหน [2] AI ที่บริษัทนี้ทำ — จริงหรือ washing? [3] งานนี้ให้ ownership และ creative input แค่ไหน [4] culture fit กับทับทิม — fast/no bureaucracy/real impact ไหม [5] เงินและสวัสดิการ [6] green flags และ red flags ที่เห็นใน JD [7] สรุป — ทำไมถึง APPLY/WATCHLIST/PASS พร้อมเหตุผลตรงๆ",
  "interview_prep": {{
    "behavioral_questions": [
      {{"question": "คำถาม behavioral ที่น่าจะถามสำหรับบริษัทนี้", "answer_guide": "แนวตอบที่ดึง project/experience ของทับทิมมาใช้"}}
    ],
    "technical_questions": [
      {{"question": "คำถาม technical ตาม stack ของ JD นี้", "answer_guide": "แนวตอบพร้อมตัวอย่างจาก project จริง"}}
    ],
    "questions_to_ask": ["คำถามถามกลับ employer ที่ช่วยประเมิน culture/ownership/AI depth จริงๆ"],
    "salary_negotiation_script": "script ต่อรองเงินภาษาไทย เหมาะกับ culture ของบริษัทนี้"
  }},
  "application_guide": {{
    "how_to_apply": "วิธี apply และ channel ที่ดีที่สุด",
    "form_questions_to_prepare": ["คำถามในฟอร์มหรือ screening ที่น่าจะเจอ"],
    "things_to_prepare": ["สิ่งที่ต้องเตรียมก่อน apply เช่น portfolio, cover letter focus, etc."]
  }}
}}
ถ้า JD ดึงไม่ได้หรือข้อมูลน้อยเกินไป: {{"error": "ไม่สามารถดึง JD ได้", "job_title": "Unknown", "company_name": "Unknown"}}
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



def _fetch_with_jina(url):
    """Layer 0: Jina AI Reader — ฟรี ไม่ต้อง API key"""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        resp = requests.get(jina_url, timeout=30, headers={"Accept": "text/plain"})
        resp.raise_for_status()
        text = resp.text.strip()
        if len(text) >= 100:
            return text, None
        return None, "Jina: content น้อยเกินไป"
    except Exception as e:
        return None, f"Jina error: {e}"

def _fetch_with_scraperapi(url):
    """Layer 1: ScraperAPI — bypass anti-bot ผ่าน proxy"""
    if not SCRAPERAPI_KEY:
        return None, "ScraperAPI: ไม่มี key"
    try:
        params = {
            "api_key": SCRAPERAPI_KEY,
            "url": url,
            "render": "true",
        }
        resp = requests.get("http://api.scraperapi.com", params=params, timeout=60)
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


def _clean_job_url(url: str) -> str:
    """ตัด tracking params ออกจาก URL เพื่อให้ ScraperAPI fetch ได้ง่ายขึ้น"""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    try:
        parsed = urlparse(url)
        # jobsdb: ตัด ?tracking=... ออก เหลือแค่ /job/<id>
        if "jobsdb.com" in (parsed.hostname or ""):
            clean = urlunparse(parsed._replace(query="", fragment=""))
            return clean
        # jobthai: ตัด query string ที่ไม่จำเป็นออก
        if "jobthai.com" in (parsed.hostname or ""):
            clean = urlunparse(parsed._replace(query="", fragment=""))
            return clean
    except Exception:
        pass
    return url


def fetch_jd(url):
    """
    Returns (content, error_message). error_message is None on success.
    ลำดับ: ScraperAPI → requests ธรรมดา
    """
    # sites ที่ login-wall จริงๆ — ไม่มีทาง fetch ได้เลย
    if "facebook.com" in url:
        return None, "⚠️ Facebook บล็อกการดึงข้อมูล\n👉 กรุณา copy JD มาวางในช่อง 'วาง JD' ด้านล่างค่ะ"
    if "linkedin.com" in url:
        return None, "⚠️ LinkedIn บล็อกการดึงข้อมูล\n👉 กรุณา copy JD มาวางในช่อง 'วาง JD' ด้านล่างค่ะ"

    # ตัด tracking params ออกก่อน fetch
    url = _clean_job_url(url)

    errors = []
    for name, fn in [("Jina", _fetch_with_jina), ("ScraperAPI", _fetch_with_scraperapi), ("requests", _fetch_with_requests)]:
        text, err = fn(url)
        if text:
            return text, None
        errors.append(f"{name}: {err}")

    # ถ้า fetch ไม่ได้ → แสดง message ชัดๆ พร้อมบอกให้วาง JD เอง
    raw_err = " | ".join(errors)
    if any(site in url for site in ["jobsdb.com", "jobthai.com"]):
        site_name = "JobsDB" if "jobsdb.com" in url else "JobThai"
        return None, (
            f"⚠️ {site_name} ป้องกันการดึงข้อมูลอัตโนมัติในครั้งนี้\n"
            f"👉 กรุณาเปิด {site_name} → copy ข้อความ JD ทั้งหมด → วางในช่อง 'วาง JD' ด้านล่างค่ะ\n"
            f"(error: {raw_err})"
        )
    return None, raw_err


def _trim_prompt(prompt, target_chars):
    """ย่อ prompt โดยตัด section ที่ใหญ่และ optional ก่อน — ไม่ตัดกลางๆ"""
    if len(prompt) <= target_chars:
        return prompt
    # 1. ย่อ COMPANY RESEARCH ก่อน (ใหญ่สุด + optional)
    m = re.search(r'(══ COMPANY RESEARCH.*?)(\n══)', prompt, re.DOTALL)
    if m and len(prompt) > target_chars:
        keep = max(200, int((target_chars - (len(prompt) - len(m.group(1)))))  )
        prompt = prompt[:m.start(1)] + m.group(1)[:keep] + "\n...(ตัดทอน)\n" + prompt[m.end(1):]
    # 2. ถ้ายังใหญ่อยู่ ย่อ JD DATA
    m2 = re.search(r'(══ JD DATA.*?)(\n══)', prompt, re.DOTALL)
    if m2 and len(prompt) > target_chars:
        keep2 = max(200, int((target_chars - (len(prompt) - len(m2.group(1))))))
        prompt = prompt[:m2.start(1)] + m2.group(1)[:keep2] + "\n...(ตัดทอน)\n" + prompt[m2.end(1):]
    # 3. สุดท้าย hard cut (กรณี edge case)
    return prompt[:target_chars] if len(prompt) > target_chars else prompt


def _call_llm_raw(prompt, retries=2):
    """Low-level LLM call — returns raw text string or raises RuntimeError
    ใช้ Groq API (ฟรี) — llama-3.3-70b-versatile

    Rate limit strategy (Groq Free tier):
    - 429 → รอตาม retry-after header จริงๆ (ไม่จำกัดรอบ) แล้วลองใหม่ใน model เดิม
    - ถ้า retry-after > 120 วินาที → switch model ทันที
    - 413 → ย่อ company_research / JD section แล้วลองใหม่ (ไม่ตัด prompt ตรงๆ)
    - 400 → likely context too long → switch model ทันที
    - 503/529 → backoff สั้น แล้วลองใหม่
    """
    if not GROQ_API_KEY:
        raise RuntimeError("ไม่มี GROQ_API_KEY ใน secrets")

    # (model, max_tokens_output, context_char_limit)
    # context_char_limit ≈ 80% ของ context window จริง แปลงคร่าวๆ 1 token ≈ 3 chars
    models = [
        ("llama-3.3-70b-versatile", 4096, 393216),  # 128k context
        ("llama-3.1-8b-instant",    4096, 393216),  # 128k context
        ("gemma2-9b-it",            2048,  24576),  # 8k context — ตั้ง max_tokens ต่ำกว่า
    ]
    last_err = ""
    current_prompt = prompt

    for model, max_tok, ctx_limit in models:
        max_429_waits = 5
        rate_limit_count = 0
        trim_count = 0

        # ถ้า prompt ใหญ่เกิน context ของ model นี้ → ย่อก่อนส่ง
        if len(current_prompt) > ctx_limit:
            current_prompt = _trim_prompt(current_prompt, ctx_limit)

        attempt = 0
        while attempt < retries + rate_limit_count:
            resp = None
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": current_prompt}],
                        "max_tokens": max_tok,
                        "temperature": 0.3,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]

            except requests.exceptions.HTTPError:
                status = resp.status_code if resp is not None else 0
                last_err = f"{model} HTTP {status}"

                if status == 429:
                    rate_limit_count += 1
                    retry_after = int(resp.headers.get("retry-after", 30))
                    if retry_after > 120 or rate_limit_count > max_429_waits:
                        last_err = f"{model} rate-limited (retry-after={retry_after}s) → switching model"
                        break
                    time.sleep(retry_after + 2)
                    attempt += 1
                    continue

                if status == 413:
                    if trim_count < 3:
                        trim_count += 1
                        current_prompt = _trim_prompt(current_prompt, int(len(current_prompt) * 0.75))
                        last_err = f"{model} 413 → trim (round {trim_count})"
                        attempt += 1
                        continue
                    last_err = f"{model} 413 after {trim_count} trims → switching model"
                    break

                if status == 400:
                    # Bad request — likely context too long หรือ parameter ไม่รองรับ → switch model
                    last_err = f"{model} 400 → switching model"
                    break

                if status in (503, 529):
                    if attempt < retries - 1:
                        time.sleep(10)
                        attempt += 1
                        continue
                    break

                # HTTP error อื่น → switch model
                break

            except requests.exceptions.Timeout:
                last_err = f"{model} timeout"
                break
            except Exception as ex:
                last_err = f"{model}: {ex}"
                break

            attempt += 1

    raise RuntimeError(f"Groq ล้มเหลวทุก model — {last_err}")


# Prompt สำหรับ Call 1 — extract facts จาก JD เท่านั้น ไม่ต้องรู้จัก candidate
JD_EXTRACT_PROMPT = """
Extract structured information from the job description below.
Reply ONLY with valid JSON. No markdown. No backticks. No extra text.

Rules:
- job_title: exact position name (e.g. "Data Engineer", "AI Developer"). NEVER leave empty.
- company_name: hiring company name. Search EVERYWHERE in the text in this order:
    1. First 3 lines / document title
    2. "About us / เกี่ยวกับเรา / เกี่ยวกับบริษัท" sections
    3. Copyright footer: "© 2024 CompanyName"
    4. Email domains: "hr@company.com" → extract "Company"
    5. Phrases: "join [X]", "at [X]", "we are [X]", "work at [X]", "apply at [X]", "posted by [X]"
    6. Thai patterns: "บริษัท X จำกัด", "บริษัท X (มหาชน)", "ร่วมงานกับ X", "สมัครงานที่ X"
    7. Any capitalized brand/organization name that appears 2+ times
    Use "Not specified" only if genuinely absent after checking all above.
- salary_min / salary_max: numbers in THB only, null if not stated.
- wfh_policy: one of "WFH Available" / "Hybrid" / "On-site" / "Unknown"

JD:
{jd_text}

JSON structure to return:
{{
  "job_title": "<exact position — required>",
  "company_name": "<company name or 'Not specified'>",
  "work_location": "<city/area or null>",
  "wfh_policy": "<WFH Available|Hybrid|On-site|Unknown>",
  "salary_min": <THB number or null>,
  "salary_max": <THB number or null>,
  "min_experience_years": <number or null>,
  "fresh_grad_welcome": <true|false|null>,
  "key_tech_stack": "<comma-separated tech>",
  "core_responsibilities": ["<up to 5 items>"],
  "must_have_skills": ["<required skills only>"],
  "nice_to_have_skills": ["<nice-to-have only>"],
  "company_size": "<e.g. 50-200 or null>",
  "industry": "<e.g. HR Tech, Fintech or null>",
  "apply_url": "<URL or null>",
  "apply_email": "<email address to send application to, or null>",
  "platform": "<platform this job is posted on, e.g. LinkedIn/JobThai/Indeed/Company Website, or null>",
  "website": "<URL or null>"
}}
"""

# Prompt สำหรับ Call 2 — วิเคราะห์ fit กับ candidate โดยใช้ extracted data
FIT_ANALYSIS_PROMPT = """
คุณคือ career advisor อาวุโสที่รู้จัก Thapanee (ทับทิม) ดีมาก
วิเคราะห์ job ด้านล่างให้เธออย่างตรงไปตรงมา เหมือนเพื่อนที่ทำงาน HR มาบอก

══ CANDIDATE PROFILE ══
{profile}

══ JOB DATA (extracted จาก JD แล้ว) ══
{job_data_json}

══ COMPANY RESEARCH (ดึงจาก web จริง — ใช้ประกอบการวิเคราะห์) ══
{company_research}

══ วิธีคิดก่อน output ══
คิดผ่าน 4 ข้อนี้ในใจก่อน (ไม่ต้องเขียนออกมา):
1. บริษัทนี้ทำ AI จริงหรือ AI washing? — ดูจากทั้ง JD และ company research
2. งานนี้ให้ ownership จริงหรือเปล่า?
3. culture fit กับทับทิมไหม? — ใช้ข้อมูลจาก company research ประกอบ (Glassdoor, news, funding)
4. resume version ไหนเหมาะ?

หมายเหตุ: ถ้า company research บอกว่า "ไม่พบข้อมูล" ให้วิเคราะห์จาก JD อย่างเดียวและระบุในวิเคราะห์ว่าข้อมูลบริษัทจำกัด

══ SCORING GUIDE ══
fit_level:
  high        = skill match ≥70% + culture fit + ไม่มี dealbreaker
  medium-high = skill match ≥60% + culture fit หรือ skill match ≥70% แต่ culture มีข้อกังวล
  medium      = skill match ≥50% หรือ culture fit แต่มีช่องว่างพอสมควร
  low-medium  = skill match <50% หรือมี dealbreaker 1 ข้อ
  low         = มี dealbreaker หลายข้อ หรือ mismatch ชัดเจน

ai_depth_score (1-5):
  5 = AI เป็น core product, ต้องทำ model/agent/production AI จริง
  4 = AI สำคัญมาก มี engineering depth
  3 = AI ใช้อยู่แต่ไม่ใช่ core
  2 = AI แค่ tool ประกอบ
  1 = แทบไม่มี AI / AI washing

ownership_score (1-5):
  5 = design + build + ship เอง, end-to-end ownership
  4 = มี ownership สูง มีอิสระในการตัดสินใจ
  3 = ปานกลาง มี spec แต่ยืดหยุ่นได้
  2 = ส่วนใหญ่ implement ตาม spec
  1 = pure execution, ไม่มี creative input

apply_decision:
  APPLY     = fit_level high/medium-high + ไม่มี dealbreaker + ai_depth≥3 + ownership≥3
  WATCHLIST = fit_level medium หรือ low-medium ที่ยังมีข้อดีชัดเจน หรือสงสัยระหว่าง PASS กับ WATCHLIST
              → ถ้าไม่แน่ใจ ให้เลือก WATCHLIST เสมอ (false PASS แย่กว่า false WATCHLIST)
              → fit_level low-medium → WATCHLIST ไม่ใช่ PASS (ยกเว้นมี dealbreaker ชัดเจนจริงๆ)
  PASS      = มี dealbreaker ชัดเจนมาก (เงินต่ำกว่า 30k ชัวร์, Pure QA role ชัวร์,
              On-site 5 วัน + งานไม่พิเศษเลย ชัวร์) หรือ fit_level = low เท่านั้น

══ SALARY ESTIMATION (สำคัญ) ══
ดูที่ "salary_min" / "salary_max" ใน JOB DATA ด้านบน
- ถ้ามีค่าระบุชัดเจนแล้ว (ไม่ใช่ null) → ใส่ "salary_min"/"salary_max" ใน output เป็นค่าเดิมนั้น (ห้ามเปลี่ยน)
- ถ้าเป็น null (JD ไม่ได้บอกเงินเดือน) → ประมาณเงินเดือนที่เหมาะสมเป็น "salary_min"/"salary_max" (หน่วย THB)
  โดยพิจารณาจาก:
  • company_size / company_tier / industry จาก JOB DATA และ company research
  • role_tier และ requirements ใน JD (entry-level, fresh grad welcome, years required)
  • ตลาดเงินเดือนจริงสำหรับ fresh grad / entry-level developer ในกรุงเทพฯ ปี 2025-2026
    (ทั่วไปอยู่ที่ประมาณ 25,000-45,000 บาท สำหรับ fresh grad SWE/AI roles ขึ้นกับขนาดบริษัท)
  • salary target ของทับทิม (35,000-45,000 บาท, floor 30,000) เป็น reference จุดหนึ่ง ไม่ใช่ค่าตายตัว
  ใส่ "salary_is_estimated": true และเขียนเหตุผลสั้นๆ ไว้ใน "salary_estimate_reason"
- ถ้าทั้งคู่มีค่าแล้ว (ไม่ null) → ใส่ "salary_is_estimated": false และ "salary_estimate_reason": ""

══ OUTPUT FORMAT ══
ตอบกลับเป็น JSON เท่านั้น ห้ามมี markdown backticks:

{{
  "role_tier": "Tier1/Tier2/Tier3",
  "role_tier_reason": "เหตุผล 1 ประโยคว่าทำไมถึงเป็น Tier นี้",
  "fit_level": "high/medium-high/medium/low-medium/low",
  "my_skill_match_pct": 75,
  "salary_min": 0,
  "salary_max": 0,
  "salary_is_estimated": false,
  "salary_estimate_reason": "",
  "ai_depth_score": 3,
  "ownership_score": 3,
  "gap_skills": ["skill ที่ขาดจริงๆ"],
  "resume_version": "ชื่อ version จาก CANDIDATE PROFILE ที่ match ที่สุด (เช่น Thapanee_AIEngineer ฯลฯ — ต้องเป็นชื่อที่มีจริงใน CANDIDATE PROFILE เท่านั้น)",
  "resume_reason": "เหตุผล 1-2 ประโยค",
  "apply_decision": "APPLY/WATCHLIST/PASS",
  "company_tier": "Level1/Level2/Level3",
  "company_tier_reason": "เหตุผลสั้นๆ",
  "location": "Bangkok, Thailand",
  "gaps": "gap หลัก max 80 chars",
  "notes": "สิ่งที่ต้องรู้ก่อน apply max 100 chars",
  "narrative_analysis": "วิเคราะห์ละเอียดภาษาไทย ครอบคลุม: [1] บริษัทเป็นใคร ทำอะไร น่าเชื่อถือแค่ไหน — อิงจาก research จริง ไม่ใช่แค่ JD [2] AI ที่บริษัทนี้ทำ — จริงหรือ washing? มีหลักฐานจาก web ไหม [3] งานนี้ให้ ownership และ creative input แค่ไหน [4] culture จริงๆ เป็นยังไง — Glassdoor บอกว่าไง ข่าวล่าสุดบอกอะไร [5] เงินและสวัสดิการ [6] green flags และ red flags ที่เห็นจากทั้ง JD และ research [7] สรุปพร้อมเหตุผลตรงๆ",
  "interview_prep": {{
    "behavioral_questions": [
      {{"question": "คำถาม behavioral ที่ตรงกับ culture บริษัทนี้จริงๆ", "answer_guide": "แนวตอบดึง project ของทับทิม"}}
    ],
    "technical_questions": [
      {{"question": "คำถาม technical ตาม stack ของ JD", "answer_guide": "แนวตอบพร้อมตัวอย่างจาก project จริง"}}
    ],
    "questions_to_ask": ["คำถามถามกลับ employer — อิงจาก red flags หรือสิ่งที่อยากรู้จาก research"],
    "salary_negotiation_script": "script ต่อรองเงินภาษาไทย เหมาะกับ culture ของบริษัทนี้"
  }},
  "application_guide": {{
    "how_to_apply": "วิธี apply และ channel ที่ดีที่สุด",
    "form_questions_to_prepare": ["คำถามใน screening ที่น่าจะเจอ"],
    "things_to_prepare": ["สิ่งที่ต้องเตรียมก่อน apply"]
  }}
}}
"""


def _scrape_text(url, max_chars=2000):
    """ดึง text จาก URL ด้วย ScraperAPI ก่อน แล้ว fallback requests"""
    text, _ = _fetch_with_scraperapi(url) if SCRAPERAPI_KEY else (None, "no key")
    if not text:
        text, _ = _fetch_with_requests(url)
    return (text or "")[:max_chars]


def research_company(company_name, website=""):
    """
    ดึงข้อมูลบริษัทจาก web จริงๆ — ใช้ ScraperAPI ที่มีอยู่แล้ว
    คืน string สรุปข้อมูลที่ดึงได้ หรือ "ไม่พบข้อมูล" ถ้าทำไม่ได้
    """
    if not company_name or company_name.lower() in ("unknown", "not specified", ""):
        return "ไม่ทราบชื่อบริษัท — ไม่สามารถ research ได้"

    sections = []

    # ── 1. Google search: บริษัท + glassdoor/crunchbase/linkedin ──
    search_queries = [
        f"{company_name} Thailand company review Glassdoor",
        f"{company_name} funding revenue crunchbase",
        f"{company_name} layoff news 2024 2025",
    ]
    for query in search_queries:
        try:
            encoded = requests.utils.quote(query, safe="")
            # ใช้ DuckDuckGo HTML (ไม่มี API key) เป็น free search
            ddg_url = f"https://html.duckduckgo.com/html/?q={encoded}"
            soup_text = _scrape_text(ddg_url, max_chars=1500)
            if soup_text and len(soup_text) > 100:
                # ตัดเอาแค่ snippet ที่ mention ชื่อบริษัท
                lines = [l.strip() for l in soup_text.splitlines()
                         if company_name.lower()[:6] in l.lower() and len(l.strip()) > 30]
                if lines:
                    sections.append(f"[Search: {query[:50]}]\n" + "\n".join(lines[:5]))
        except Exception:
            pass

    # ── 2. เว็บบริษัทโดยตรง (About / Careers page) ──
    targets = []
    if website:
        targets.append(website)
        # ลอง /about และ /careers ด้วย
        base = website.rstrip("/")
        targets += [f"{base}/about", f"{base}/about-us", f"{base}/careers"]

    for url in targets[:3]:
        try:
            text = _scrape_text(url, max_chars=1500)
            if text and len(text) > 150:
                sections.append(f"[เว็บบริษัท: {url[:60]}]\n{text[:1500]}")
                break  # ได้แล้วพอ
        except Exception:
            pass

    # ── 3. Glassdoor direct search ──
    try:
        gd_url = f"https://www.glassdoor.com/Search/results.htm?keyword={requests.utils.quote(company_name)}"
        gd_text = _scrape_text(gd_url, max_chars=1500)
        if gd_text and len(gd_text) > 100:
            lines = [l.strip() for l in gd_text.splitlines()
                     if any(k in l.lower() for k in ("rating", "review", "recommend", "culture", "salary", "คะแนน"))
                     and len(l.strip()) > 20]
            if lines:
                sections.append(f"[Glassdoor]\n" + "\n".join(lines[:8]))
    except Exception:
        pass

    if not sections:
        return f"ไม่พบข้อมูลบริษัท '{company_name}' จาก web — วิเคราะห์จาก JD เท่านั้น"

    return "\n\n".join(sections)[:4000]


def analyze_with_llm(jd_text, retries=2, known_company_name=""):
    # ── Call 1: Extract job facts จาก JD (~2000 tokens) ────
    extract_prompt = JD_EXTRACT_PROMPT.format(jd_text=jd_text[:6000])
    try:
        raw1 = _call_llm_raw(extract_prompt, retries=retries)
        raw1 = re.sub(r"^```json\s*", "", raw1.strip())
        raw1 = re.sub(r"```\s*$", "", raw1.strip())
        job_facts = json.loads(raw1)
    except json.JSONDecodeError:
        # ถ้า parse ไม่ได้ ลอง repair ก่อน แล้วค่อย fallback
        job_facts = _parse_llm_json(raw1) if 'raw1' in locals() else {}
        if not job_facts.get("company_name"):
            job_facts["raw_jd"] = jd_text[:2000]
    except RuntimeError as e:
        return {"error": str(e), "job_title": "Unknown", "company_name": "Unknown"}

    # ── ถ้าผู้ใช้กรอกชื่อบริษัทมาเอง (เช่น แปะ JD จาก Facebook + กรอก Company ใน Notion)
    #    ให้เชื่อชื่อนั้นเสมอ — แม่นยำกว่า LLM เดาจาก JD ที่อาจไม่มีชื่อบริษัทระบุชัด
    if known_company_name and known_company_name.strip():
        job_facts["company_name"] = known_company_name.strip()

    # ── Call 1.5: Research บริษัทจาก web ────────────────────
    # ลอง regex fallback ก่อน research เพื่อให้ได้ชื่อบริษัทที่ดีขึ้น
    company_name = job_facts.get("company_name", "")
    website      = job_facts.get("website", "")
    UNKNOWN_NAMES = {"", "not specified", "unknown", "none", "n/a"}
    if company_name.lower() in UNKNOWN_NAMES:
        # ลอง regex ใน JD ก่อน research (เพื่อไม่เสีย ScraperAPI credits)
        for pat in [
            r"บริษัท\s+([^\s][^\n]+?)(?:\s+จำกัด|\s+\(มหาชน\)|$)",
            r"(?:company|employer|posted by|hiring company)\s*[:\-]\s*(.+)",
            r"(?:join|at|work at|careers at)\s+([A-Z][A-Za-z0-9\s&\.]{2,40}?)(?:\n|,|\.|$)",
            r"©\s*(?:\d{4}\s+)?([A-Za-z][A-Za-z0-9\s&\.]{2,40}?)(?:\s|,|\.|All rights)",
        ]:
            m = re.search(pat, jd_text[:4000], re.IGNORECASE | re.MULTILINE)
            if m:
                candidate = m.group(1).strip()[:80]
                SKIP_WORDS = {"apply", "job", "position", "role", "work", "us", "the", "our"}
                if len(candidate) > 2 and candidate.lower() not in SKIP_WORDS:
                    company_name = candidate
                    job_facts["company_name"] = company_name
                    break
    # วิจัยบริษัทเฉพาะเมื่อมีชื่อชัดเจน — ไม่เปลือง ScraperAPI credits กับ "Not specified"
    if company_name.lower() not in UNKNOWN_NAMES:
        company_research = research_company(company_name, website)
    else:
        # ── ไม่รู้ชื่อบริษัทแม้ลอง regex แล้ว และไม่มี known_company_name ──
        # หยุดตรงนี้ ไม่เรียก Call 2 (fit analysis) เพื่อไม่เปลือง token
        # ให้ผู้ใช้กรอกชื่อบริษัทใน Notion (field Company) ก่อน แล้วเปลี่ยน status เป็น llm_error เพื่อ retry
        return {
            "error": "no_company",
            "job_title": job_facts.get("job_title", "Unknown"),
            "company_name": "Unknown",
        }

    # ── Call 2: วิเคราะห์ fit (~3500 tokens) ───────────────
    # ดึง Resume Versions สดจาก Notion (cache 5 นาที) แทน hardcoded block เดิม
    resume_versions_block = load_resume_versions_summary()
    profile_with_versions = CANDIDATE_PROFILE.format(resume_versions_block=resume_versions_block)

    fit_prompt = FIT_ANALYSIS_PROMPT.format(
        profile=profile_with_versions,
        job_data_json=json.dumps(job_facts, ensure_ascii=False, indent=2),
        company_research=company_research,
    )
    try:
        raw2 = _call_llm_raw(fit_prompt, retries=retries)
        raw2 = re.sub(r"^```json\s*", "", raw2.strip())
        raw2 = re.sub(r"```\s*$", "", raw2.strip())
        fit_data = _parse_llm_json(raw2)
    except RuntimeError as e:
        return {"error": str(e), "job_title": "Unknown", "company_name": "Unknown"}

    # ── Merge: fit_data เป็น base, job_facts ชนะสำหรับ key ที่มีค่าจริง ──
    result = {**fit_data, **job_facts}

    # สำหรับทุก key: ใช้ค่าแรกที่ไม่ว่างจาก job_facts → fit_data → default
    CORE_KEYS = [
        ("job_title",      "Unknown"),
        ("company_name",   "Not specified"),
        ("work_location",  ""),
        ("wfh_policy",     "Unknown"),
        ("key_tech_stack", ""),
        ("industry",       ""),
        ("website",        ""),
        ("apply_url",      ""),
        ("apply_email",    ""),
        ("platform",       ""),
        ("salary_min",     None),
        ("salary_max",     None),
    ]
    for key, default in CORE_KEYS:
        result[key] = job_facts.get(key) or fit_data.get(key) or default

    # ── Validate company_name — ถ้ายัง "Not specified" / ว่าง ให้ retry extract จาก JD โดยตรง
    cn = result.get("company_name", "")
    if not cn or cn.lower() in ("not specified", "unknown", ""):
        # พยายามดึงชื่อบริษัทจาก JD ด้วย regex เป็น last resort
        company_patterns = [
            # Thai patterns
            r"บริษัท\s+([^\s][^\n]+?)(?:\s+จำกัด|\s+\(มหาชน\)|$)",
            r"(?:สมัครงานที่|ร่วมงานกับ|ทำงานกับ)\s+([A-Za-zก-๛][^\n]{2,40})",
            # English patterns
            r"(?:company|employer|organization|posted by|hiring company)\s*[:\-]\s*(.+)",
            r"(?:about|join|at|work at|careers at)\s+([A-Z][A-Za-z0-9\s&\.]{2,40}?)(?:\n|,|\.|$)",
            r"©\s*(?:\d{4}\s+)?([A-Za-z][A-Za-z0-9\s&\.]{2,40}?)(?:\s|,|\.|All rights)",
            # Email domain
            r"[\w\.\+]+@([a-z0-9\-]+)\.[a-z]{2,}",
        ]
        for pat in company_patterns:
            m = re.search(pat, jd_text[:4000], re.IGNORECASE | re.MULTILINE)
            if m:
                candidate = m.group(1).strip()[:80]
                # กรณี email domain → capitalize
                if "@" in pat:
                    candidate = candidate.split(".")[0].capitalize()
                # กรอง false positives
                SKIP_WORDS = {"apply", "job", "position", "role", "work", "us", "the", "our"}
                if len(candidate) > 2 and candidate.lower() not in SKIP_WORDS:
                    result["company_name"] = candidate
                    break

    return result


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
        def extract_num(field, default=None):
            m = re.search(rf'"{field}"\s*:\s*(-?\d+(?:\.\d+)?|null)', raw)
            if not m or m.group(1) == "null":
                return default
            return float(m.group(1)) if "." in m.group(1) else int(m.group(1))
        def extract_bool(field, default=False):
            m = re.search(rf'"{field}"\s*:\s*(true|false)', raw)
            return (m.group(1) == "true") if m else default
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
            "apply_email":    extract("apply_email", ""),
            "platform":       extract("platform", ""),
            "salary_min":          extract_num("salary_min", None),
            "salary_max":          extract_num("salary_max", None),
            "salary_is_estimated": extract_bool("salary_is_estimated", False),
            "salary_estimate_reason": extract("salary_estimate_reason", ""),
            "error": "JSON truncated — partial data recovered"
        }


# ── Cache-loader functions (must be defined before tab3 calls them) ──────────

@st.cache_data(ttl=300, show_spinner=False)
def load_resume_versions_cache() -> list:
    """
    STEP 0 — ดึง Resume Versions ทั้งหมดจาก Notion สำหรับใช้ใน batch
    cache 5 นาที — ทำครั้งเดียวต่อ batch แทนที่จะดึงแยกต่อทุกงาน
    คืน list of dicts: version_id, tone_label, use_when, use_with,
      summary_paragraph, headline, skills_emphasis, projects_order, canva_link
    """
    try:
        res = requests.post(
            f"https://api.notion.com/v1/databases/{RESUME_VERSIONS_DB_ID}/query",
            headers=HEADERS,
            json={"page_size": 50}
        )
        results = res.json().get("results", [])
        cache = []
        for page in results:
            p = page.get("properties", {})
            def txt(key):
                arr = (p.get(key) or {}).get("rich_text", [])
                return arr[0].get("plain_text", "").strip() if arr else ""
            def sel(key):
                return ((p.get(key) or {}).get("select") or {}).get("name", "").strip()
            def ttl(key):
                arr = (p.get(key) or {}).get("title", [])
                return arr[0].get("plain_text", "").strip() if arr else ""
            def url_val(key):
                return (p.get(key) or {}).get("url", "") or ""
            vid = ttl("version_id") or txt("version_id")
            if not vid:
                continue
            cache.append({
                "version_id":        vid,
                "tone_label":        sel("tone_label") or txt("tone_label"),
                "use_when":          txt("use_when"),
                "use_with":          txt("use_with"),
                "summary_paragraph": txt("summary_paragraph"),
                "headline":          txt("headline"),
                "skills_emphasis":   txt("skills_emphasis"),
                "projects_order":    txt("projects_order"),
                "canva_link":        url_val("canva_link"),
            })
        return cache
    except Exception:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def load_project_details_cache() -> list:
    """
    STEP 0 — ดึง Project Details ทั้งหมดจาก Notion สำหรับใช้ใน batch
    cache 5 นาที — ทำครั้งเดียวต่อ batch
    คืน list of dicts: Project Name, Project Type, Tech Stack, etc.
    """
    try:
        res = requests.post(
            f"https://api.notion.com/v1/databases/{PROJECT_DETAILS_DB_ID}/query",
            headers=HEADERS,
            json={"page_size": 20}
        )
        results = res.json().get("results", [])
        cache = []
        for page in results:
            p = page.get("properties", {})
            def txt(key):
                arr = (p.get(key) or {}).get("rich_text", [])
                return arr[0].get("plain_text", "").strip() if arr else ""
            def ttl(key):
                arr = (p.get(key) or {}).get("title", [])
                return arr[0].get("plain_text", "").strip() if arr else ""
            name = ttl("Project Name") or txt("Project Name")
            if not name:
                continue
            cache.append({
                "Project Name":        name,
                "Project Type":        txt("Project Type"),
                "Tech Stack":          txt("Tech Stack"),
                "Problem/Why Built":   txt("Problem/Why Built"),
                "Key Actions":         txt("Key Actions"),
                "Key Metrics":         txt("Key Metrics"),
                "Relevance Tags":      txt("Relevance Tags"),
                "STAR Story":          txt("STAR Story"),
                "Can Answer Questions":txt("Can Answer Questions"),
                "Portfolio Link":      txt("Portfolio Link"),
            })
        return cache
    except Exception:
        return []


def _format_resume_versions_cache(cache: list) -> str:
    """แปลง resume_versions_cache (list of dicts) เป็น string สำหรับ prompt"""
    if not cache:
        return "(ไม่พบ Resume Versions ใน cache)"
    lines = []
    for v in cache:
        block = f"VERSION {v['version_id']}"
        if v.get("tone_label"): block += f" — {v['tone_label']}"
        if v.get("use_with"):   block += f"\n  ใช้กับ: {v['use_with']}"
        if v.get("use_when"):   block += f"\n  ใช้เมื่อ: {v['use_when']}"
        if v.get("skills_emphasis"): block += f"\n  เน้น: {v['skills_emphasis']}"
        lines.append(block)
    return "\n\n".join(lines)


def _format_project_details_cache(cache: list) -> str:
    """แปลง project_details_cache (list of dicts) เป็น string สำหรับ prompt"""
    if not cache:
        return "(ไม่พบ Project Details ใน cache)"
    blocks = []
    for proj in cache:
        block = f"PROJECT: {proj['Project Name']}"
        if proj.get("Project Type"):         block += f"\n  Type: {proj['Project Type']}"
        if proj.get("Tech Stack"):           block += f"\n  Stack: {proj['Tech Stack']}"
        if proj.get("Problem/Why Built"):    block += f"\n  Why: {proj['Problem/Why Built']}"
        if proj.get("Key Actions"):          block += f"\n  Actions: {proj['Key Actions']}"
        if proj.get("Key Metrics"):          block += f"\n  Metrics: {proj['Key Metrics']}"
        if proj.get("Relevance Tags"):       block += f"\n  Tags: {proj['Relevance Tags']}"
        if proj.get("STAR Story"):           block += f"\n  STAR: {proj['STAR Story']}"
        if proj.get("Can Answer Questions"): block += f"\n  Can Answer: {proj['Can Answer Questions']}"
        if proj.get("Portfolio Link"):       block += f"\n  Portfolio: {proj['Portfolio Link']}"
        blocks.append(block)
    return "\n\n".join(blocks)


with tab3:
    if not GROQ_API_KEY:
        st.warning("⚠️ ยังไม่มี `GROQ_API_KEY` ใน Streamlit Secrets ค่ะ")

    # ── Settings ─────────────────────────────────────────────
    with st.expander("⚙️ Settings", expanded=False):
        delay = st.slider("หน่วงเวลาระหว่าง request (วินาที)", 5, 30, 12,
                          help="แนะนำ 12+ วินาที เพื่อหลีกเลี่ยง Groq rate limit")
        batch_size = st.slider("จำนวนงานต่อ batch (0 = ทั้งหมด)", 0, 50, 0,
                               help="แนะนำ 15-20 งานต่อ batch เพื่อกัน session timeout | 0 = รันทั้งหมดที่มี")

    # ── Debug: แสดง property map จริงจาก Notion ─────────────
    if JOB_LISTING_DB_ID:
        with st.expander("🔍 Debug: Notion field map", expanded=False):
            if st.button("รีเฟรช field map", key="btn_bust_pmap"):
                get_listing_property_map.clear()
                get_listing_status_map.clear()
                load_options.clear()  # clear select options ด้วย (Company Size, Role Tier ฯลฯ)
            pmap_debug = get_listing_property_map()
            smap_debug = get_listing_status_map()
            st.json({"property_map": pmap_debug, "status_map": smap_debug})

    st.markdown("---")

    # ══════════════════════════════════════════════════════════
    # SECTION A — ดึงจาก Job Listing DB (main flow)
    # ══════════════════════════════════════════════════════════
    def fetch_pending_listings():
        """ดึง rows ใน Job Listing DB ที่ยังไม่มีสถานะ ใช้ชื่อ field จริงจาก pmap"""
        if not JOB_LISTING_DB_ID:
            return [], "ไม่มี JOB_LISTING_DB_ID"
        try:
            pmap = get_listing_property_map()
            status_field  = pmap.get("status", "Status")
            status_type   = pmap.get("status_type", "select")
            url_field     = pmap.get("url", "URL")
            name_field    = pmap.get("name", "Name")
            jd_field      = pmap.get("jd", "JD")
            company_field = pmap.get("company", "Company")

            status_map = get_listing_status_map()
            # reverse map: display name (lowered) → canonical key
            # e.g. "llm error" → "llm_error", "consider" → "consider"
            display_to_key = {v.lower(): k for k, v in status_map.items()}
            # fallback: canonical key itself (in case Notion stores the key)
            display_to_key.update({k: k for k in status_map})

            # ── Paginate ทุก row (ไม่มี limit 100) ─────────────
            rows = []
            payload = {"page_size": 100}
            while True:
                res = requests.post(
                    f"https://api.notion.com/v1/databases/{JOB_LISTING_DB_ID}/query",
                    headers=HEADERS, json=payload
                )
                data = res.json()
                rows.extend(data.get("results", []))
                if not data.get("has_more"):
                    break
                payload["start_cursor"] = data["next_cursor"]

            # สถานะที่ "เสร็จแล้ว" หรือ ต้องรอแก้ไขด้วยมือก่อน → ข้ามถาวร (auto-queue ไม่หยิบ)
            # to_apply / on_hold / pass: push เข้า Job Pipeline สำเร็จแล้ว ผลตัดสินคือ APPLY/WATCHLIST/PASS
            # need_company: รอผู้ใช้กรอกชื่อบริษัทใน Notion แล้วเปลี่ยนเป็น llm_error เพื่อ retry เอง
            DONE_STATUSES = {"to_apply", "on_hold", "pass", "fetch_error", "need_company"}
            # สถานะที่ "ว่าง" → ถือว่า pending ใหม่ (fetch + LLM ทุกขั้น)
            EMPTY_STATUS_VALUES = {"", "no status", "no apply status", "none", "-"}
            # สถานะที่ retry ได้ — skip fetch แต่ run LLM ใหม่
            # llm_error / consider → JD ดึงมาได้แล้ว แต่ LLM พัง หรือยังไม่ push สำเร็จ
            SKIP_FETCH_STATUSES = {"llm_error", "consider"}

            pending = []
            for row in rows:
                props = row.get("properties", {})

                # ── อ่าน status ด้วยชื่อ field จริง ──────────────
                sp = props.get(status_field, {})
                sel = sp.get("select") or sp.get("status") or {}
                status_val   = (sel.get("name") or "").strip()
                status_lower = status_val.lower().strip()

                # normalize display name (เช่น "LLM Error" → "llm error") → canonical key ("llm_error")
                canonical_key = display_to_key.get(status_lower, status_lower)

                # ข้าม row ที่เสร็จแล้ว หรือ URL ใช้ไม่ได้ถาวร
                if canonical_key in DONE_STATUSES:
                    continue

                if status_lower in EMPTY_STATUS_VALUES:
                    # งานใหม่ — fetch + LLM ทุกขั้น
                    skip_fetch = False
                elif canonical_key in SKIP_FETCH_STATUSES:
                    # มี JD แล้ว (llm_error) หรือยังไม่ push (consider) → skip fetch, run LLM ใหม่
                    skip_fetch = True
                else:
                    continue  # status ที่ไม่รู้จัก → ข้าม

                # ── URL ──────────────────────────────────────────
                up = props.get(url_field, {})
                url = (up.get("url") or "").strip()
                if not url:
                    continue

                # ── Name ─────────────────────────────────────────
                np = props.get(name_field, {})
                title_arr = np.get("title", [])
                name = title_arr[0].get("plain_text", "") if title_arr else ""

                # ── JD ───────────────────────────────────────────
                jp = props.get(jd_field, {})
                jd_arr  = jp.get("rich_text", [])
                jd_text = jd_arr[0].get("plain_text", "") if jd_arr else ""

                # ── Fallback: ถ้า property "JD" ว่าง แต่ผู้ใช้แปะ JD ไว้ใน page body เอง ──
                # (เกิดได้ทั้งกับ row ที่ upsert ก่อนแก้ field "JD", และ row ที่ผู้ใช้พิมพ์เอง)
                if not jd_text.strip() and canonical_key in SKIP_FETCH_STATUSES:
                    jd_text = _get_page_body_text(row["id"])

                # ── Company (ถ้าผู้ใช้กรอกไว้เอง — ใช้ override การเดาของ LLM) ──
                cp = props.get(company_field, {})
                company_arr = cp.get("rich_text", []) or cp.get("title", [])
                company_name_hint = company_arr[0].get("plain_text", "").strip() if company_arr else ""

                pending.append({
                    "notion_id": row["id"], "url": url,
                    "name": name or url[:60], "jd_text": jd_text,
                    "company_name_hint": company_name_hint,
                    "canonical_status": canonical_key,
                    "skip_fetch": skip_fetch,
                })
            return pending, None
        except Exception as e:
            return [], str(e)

    jobs_to_run = []
    source = None

    if JOB_LISTING_DB_ID:
        st.subheader("📋 Job Listing Queue")
        col_btn, col_info = st.columns([1, 3])
        with col_btn:
            do_refresh = st.button("🔄 โหลด Queue", key="btn_refresh_queue")

        if do_refresh or "listing_queue" not in st.session_state:
            with st.spinner("กำลังดึง Job Listing DB..."):
                q, qerr = fetch_pending_listings()
            if qerr:
                st.error(f"❌ {qerr}")
                st.session_state["listing_queue"] = []
            else:
                st.session_state["listing_queue"] = q

        queue = st.session_state.get("listing_queue", [])

        if queue:
            with col_info:
                st.info(f"พบ **{len(queue)}** งานรอวิเคราะห์")

            with st.expander(f"ดูรายการทั้งหมด ({len(queue)} งาน)", expanded=False):
                for i, q_job in enumerate(queue, 1):
                    status_badge = q_job.get("canonical_status", "new") or "new"
                    has_jd = " *(มี JD แล้ว)*" if q_job.get("jd_text") else ""
                    skip_badge = " `[skip fetch]`" if q_job.get("skip_fetch") else ""
                    st.caption(
                        f"{i}. {q_job['name'][:70]}  •  `{q_job['url'][:50]}`  •  "
                        f"`{status_badge}`{has_jd}{skip_badge}"
                    )

            if st.button(f"🚀 วิเคราะห์ {len(queue)} งาน → Push Notion อัตโนมัติ",
                         key="btn_run_queue", type="primary"):
                jobs_to_run = queue
                source = "listing_db"
        else:
            with col_info:
                st.success("✅ ไม่มีงานค้างใน Queue")
            st.caption("เพิ่ม URL ลงใน Job Listing DB ใน Notion แล้วกด 🔄 โหลด Queue")

        st.markdown("---")
    else:
        st.warning("⚠️ ยังไม่ได้ตั้งค่า `JOB_LISTING_DB_ID` — ใช้โหมดใส่เองด้านล่างแทนได้ค่ะ")

    # ══════════════════════════════════════════════════════════
    # SECTION B — ใส่เองด้วยมือ (backup / one-off)
    # ══════════════════════════════════════════════════════════
    with st.expander("➕ เพิ่มงานเองด้วยมือ (URL / JD โดยตรง)",
                     expanded=not bool(JOB_LISTING_DB_ID)):
        mode_manual = st.radio("วิธีใส่", ["🔗 วาง URL", "📝 วาง JD โดยตรง"],
                               horizontal=True, key="manual_mode")
        manual_jobs = []

        if mode_manual == "🔗 วาง URL":
            raw_urls = st.text_area("วาง URL (1 บรรทัด / URL)", height=120,
                                    placeholder="https://th.jobsdb.com/job/12345",
                                    key="manual_urls")
            if raw_urls.strip():
                seen_m = set()
                for line in raw_urls.strip().splitlines():
                    u = line.strip()
                    if not u:
                        continue
                    base = u.split("?")[0].rstrip("/")
                    if base not in seen_m:
                        seen_m.add(base)
                        manual_jobs.append({"url": u, "name": u[:60]})
                if manual_jobs:
                    st.caption(f"พบ {len(manual_jobs)} URL")
        else:
            m_url = st.text_input("URL ต้นทาง (ไม่บังคับ)",
                                  placeholder="https://www.facebook.com/share/p/...",
                                  key="manual_jd_url")
            m_jd  = st.text_area("วาง JD ที่นี่", height=250,
                                  placeholder="ชื่อตำแหน่ง: ...\nบริษัท: ...",
                                  key="manual_jd_text")
            if m_jd.strip():
                ref = m_url.strip() or f"manual://jd-{hash(m_jd[:100]) % 100000}"
                manual_jobs.append({"url": ref, "name": ref[:60], "jd_text": m_jd.strip()})
                st.caption("พบ 1 JD พร้อมวิเคราะห์")

        if manual_jobs:
            if st.button("🚀 วิเคราะห์ + Push Notion", key="btn_manual_run"):
                jobs_to_run = manual_jobs
                source = "manual"

    # ══════════════════════════════════════════════════════════
    # SECTION C — Pipeline ประมวลผล (ทำงานเมื่อกดปุ่มใดก็ตาม)
    # ══════════════════════════════════════════════════════════
    if jobs_to_run:
        # ── ตัด batch ตาม batch_size ────────────────────────
        total_in_queue = len(jobs_to_run)
        if batch_size > 0 and total_in_queue > batch_size:
            jobs_to_run = jobs_to_run[:batch_size]
            st.info(f"📦 Queue มี {total_in_queue} งาน — รัน batch นี้ {batch_size} งานก่อน "
                    f"(เหลือ {total_in_queue - batch_size} งาน กด 🚀 อีกครั้งเพื่อทำรอบต่อไป)")
        st.markdown("---")
        st.subheader("⚙️ กำลังประมวลผล...")

        stats    = {"ok": 0, "err": 0, "notion_ok": 0, "notion_err": 0, "drafted": 0}
        results  = []
        progress = st.progress(0, text="เริ่มต้น...")
        log_area = st.empty()
        logs     = []

        def add_log(msg):
            logs.append(msg)
            log_area.code("\n".join(logs[-50:]))

        # ── STEP 0: โหลด cache ก่อน loop — ทำครั้งเดียวต่อ batch ──────
        add_log("⚙️ STEP 0: โหลด Resume Versions...")
        _rv_cache = load_resume_versions_cache()
        add_log(f"  ✅ Resume Versions: {len(_rv_cache)} versions")

        add_log("⚙️ STEP 0: โหลด Project Details...")
        _pd_cache = load_project_details_cache()
        add_log(f"  ✅ Project Details: {len(_pd_cache)} projects")

        for i, job in enumerate(jobs_to_run):
            url  = job["url"]
            name = job.get("name") or url[:50]
            progress.progress(i / len(jobs_to_run), text=f"[{i+1}/{len(jobs_to_run)}] {name[:40]}...")
            add_log(f"\n[{i+1}/{len(jobs_to_run)}] {name[:55]}")

            if job.get("jd_text"):
                jd = job["jd_text"]
                add_log(f"  📋 ใช้ JD ที่มีอยู่แล้ว ({len(jd)} chars)")
            elif job.get("skip_fetch"):
                # status เป็น llm_error/consider แต่ field "JD" ว่าง (row เก่าก่อนแก้)
                # → fallback fetch ใหม่
                add_log(f"  ⚠️ ไม่มี JD ใน record (status retryable แต่ field JD ว่าง) — fetch ใหม่")
                jd, fetch_err = fetch_jd(url)
                if fetch_err or not jd:
                    add_log(f"  ❌ Fetch failed: {fetch_err}")
                    stats["err"] += 1
                    results.append({"url": url, "name": name, "status": "error", "error": fetch_err})
                    if JOB_LISTING_DB_ID:
                        _uid, _uerr = upsert_job_listing(url, status_key="fetch_error",
                                           error_note=f"Fetch failed (retry fallback): {fetch_err}")
                        if _uerr:
                            add_log(f"  ⚠️ Listing update failed: {_uerr}")
                        else:
                            add_log(f"  📋 Listing: fetch_error (ออกจาก queue แล้ว — retry ได้ใส่ JD เองด้านล่าง)")
                    if i < len(jobs_to_run) - 1:
                        time.sleep(delay)
                    continue
                add_log(f"  📄 {jd[:80].replace(chr(10),' ')}...")
            else:
                add_log(f"  🌐 Fetching JD...")
                jd, fetch_err = fetch_jd(url)
                if fetch_err:
                    add_log(f"  ❌ Fetch failed: {fetch_err}")
                    stats["err"] += 1
                    results.append({"url": url, "name": name, "status": "error", "error": fetch_err})
                    if JOB_LISTING_DB_ID:
                        # ตั้ง status = "fetch_error" เพื่อให้ออกจาก queue ถาวร
                        # (fetch_pending_listings กรอง "no status" เท่านั้น → งานนี้จะไม่วนกลับมา)
                        _uid, _uerr = upsert_job_listing(url, status_key="fetch_error",
                                           error_note=f"Fetch failed: {fetch_err}")
                        if _uerr:
                            add_log(f"  ⚠️ Listing update failed: {_uerr}")
                        else:
                            add_log(f"  📋 Listing: fetch_error (ออกจาก queue แล้ว — retry ได้ใส่ JD เองด้านล่าง)")
                    if i < len(jobs_to_run) - 1:
                        time.sleep(delay)
                    continue
                add_log(f"  📄 {jd[:80].replace(chr(10),' ')}...")

            add_log(f"  🤖 Analyzing + researching บริษัท...")
            company_hint = job.get("company_name_hint", "")
            if company_hint:
                add_log(f"  🏷️ ใช้ชื่อบริษัทที่กรอกไว้: {company_hint}")
            analysis = analyze_with_llm(jd, known_company_name=company_hint)
            cn_log = analysis.get("company_name", "?")
            has_research = cn_log not in ("?", "Unknown", "Not specified", "")
            add_log(f"  {'🔍' if has_research else '⚠️'} {cn_log}")

            if analysis.get("error") == "no_company":
                jt_nc = analysis.get("job_title", "Unknown")
                add_log(f"  ⚠️ ไม่พบชื่อบริษัทใน JD ({jt_nc}) — ข้าม LLM fit analysis")
                stats["err"] += 1
                results.append({"url": url, "name": name, "status": "error", "error": "ไม่พบชื่อบริษัทใน JD"})
                if JOB_LISTING_DB_ID:
                    # ตั้ง status = "need_company" — รอผู้ใช้กรอกชื่อบริษัทใน Notion ก่อน
                    # แล้วเปลี่ยน status เป็น "llm_error" เองเพื่อ retry
                    _uid, _uerr = upsert_job_listing(url, job_title=jt_nc,
                                       jd_raw=jd[:2000] if 'jd' in locals() else "",
                                       status_key="need_company",
                                       error_note="ไม่พบชื่อบริษัทใน JD — กรุณากรอก Company แล้วเปลี่ยน status เป็น LLM Error เพื่อ retry")
                    if _uerr:
                        add_log(f"  ⚠️ Listing update failed: {_uerr}")
                    else:
                        add_log(f"  📋 Listing: need_company (กรอก Company แล้วเปลี่ยนเป็น LLM Error เพื่อ retry)")
            elif "error" in analysis and analysis.get("job_title", "Unknown") == "Unknown" and analysis.get("company_name", "Unknown") == "Unknown":
                add_log(f"  ❌ LLM error: {analysis['error']}")
                stats["err"] += 1
                results.append({"url": url, "name": name, "status": "error", "error": analysis["error"]})
                if JOB_LISTING_DB_ID:
                    # ตั้ง status = "llm_error" เพื่อออกจาก queue ถาวร
                    _uid, _uerr = upsert_job_listing(url, status_key="llm_error",
                                       jd_raw=jd[:2000] if 'jd' in locals() else "",
                                       error_note=f"LLM error: {analysis.get('error','')}")
                    if _uerr:
                        add_log(f"  ⚠️ Listing update failed: {_uerr}")
                    else:
                        add_log(f"  📋 Listing: llm_error (ออกจาก queue แล้ว)")
            else:
                jt = analysis.get("job_title", "?")
                cn = analysis.get("company_name", "?")
                decision = analysis.get("apply_decision", "?")
                add_log(f"  ✅ {jt} @ {cn} | {analysis.get('fit_level','?')} | {decision}")
                stats["ok"] += 1
                result_entry = {"url": url, "name": name, "status": "ok", "analysis": analysis}

                # บันทึก Consider + ข้อมูลดิบ
                if JOB_LISTING_DB_ID:
                    upsert_job_listing(url, job_title=jt, company_name=cn,
                                       jd_raw=jd[:2000] if 'jd' in locals() else "",
                                       status_key="consider")
                    add_log(f"  📋 Listing: Consider")

                # Push Notion อัตโนมัติเสมอ
                add_log(f"  📤 Pushing to Notion...")
                try:
                    j_data, c_data = analysis_to_notion_dicts(analysis, url)
                    if not j_data.get("job_title", "").strip():
                        raise ValueError("no job title")
                    cname = c_data.get("company_name", "").strip()
                    # ถ้าชื่อบริษัทไม่ชัดเจน → ลอง extract จาก URL ก่อน
                    if not cname or cname.lower() in ("not specified", "unknown", ""):
                        url_company = _company_from_url(url)
                        if url_company:
                            cname = url_company
                            c_data["company_name"] = cname
                            add_log(f"  🔎 Company from URL: {cname}")
                        else:
                            # ยังไม่ได้ชื่อบริษัทเลย → ไม่ push, ตั้ง fetch_error
                            raise ValueError(f"ไม่ทราบชื่อบริษัท — ไม่ push Notion (แก้ใน Listing แล้วลอง retry)")
                    company_id, found = search_company(cname)
                    if not found or not company_id:
                        company_id, cerr = create_company(c_data, opt)
                        if cerr:
                            raise ValueError(f"create company: {cerr}")
                    ok_job, page_id_or_err = create_job(j_data, company_id, opt)
                    if not ok_job:
                        raise ValueError(f"create job: {page_id_or_err}")
                    add_log(f"  ✅ Notion OK")
                    stats["notion_ok"] += 1
                    job_page_id_for_rm = page_id_or_err  # page_id จาก create_job()

                    # อัปเดต Listing → ตามผลตัดสินของ AI (To Apply / On Hold / Pass)
                    # ให้ status ใน Job Listing DB ตรงกับ Apply Status ใน Job Pipeline DB
                    if JOB_LISTING_DB_ID:
                        decision_key = APPLY_DECISION_TO_LISTING_STATUS.get(
                            analysis.get("apply_decision", "").upper(), "on_hold"
                        )
                        upsert_job_listing(url, status_key=decision_key)
                        decision_label = {"to_apply": "To Apply", "on_hold": "On Hold", "pass": "Pass"}.get(decision_key, decision_key)
                        add_log(f"  📋 Listing: {decision_label} ✅")

                    # ── APPLY → resume matching pipeline ─────────────
                    if analysis.get("apply_decision", "").upper() == "APPLY":
                        add_log(f"  🎯 APPLY → เช็ค budget + เริ่ม resume matching...")
                        current_spend = get_monthly_spend_so_far()
                        if notify_budget_exceeded(current_spend, jt):
                            add_log(f"  🚨 Budget cap ถึงแล้ว ({current_spend:.2f}/{BUDGET_CAP_THB} ฿) — ข้าม Gemini")
                        else:
                            try:
                                rm_result = resume_matching_pipeline(
                                    job_page_id=job_page_id_for_rm,
                                    job_title=jt,
                                    company_name=cn,
                                    job_data=analysis,
                                    log_fn=add_log,
                                    resume_versions_cache=_rv_cache,
                                    project_details_cache=_pd_cache,
                                )
                                if rm_result.get("error") == "critical_gap":
                                    add_log(f"  🚨 CRITICAL GAP — รอทับทิมตัดสินใจ")
                                elif rm_result.get("success"):
                                    stats["drafted"] += 1
                                    add_log(f"  📝 Draft: {rm_result.get('chosen_version','?')}")
                                else:
                                    add_log(f"  ⚠️ Resume matching ไม่สำเร็จ: {rm_result.get('error')}")
                                # log token usage จะถูกเรียกใน resume_matching_pipeline แล้ว
                            except Exception as e:
                                add_log(f"  ⚠️ Resume matching error: {e}")

                except Exception as e:
                    add_log(f"  ❌ Notion error: {e}")
                    stats["notion_err"] += 1
                    # ถ้าเป็น error เรื่องชื่อบริษัท → ตั้ง fetch_error ไม่ใช่ค้าง consider
                    if JOB_LISTING_DB_ID and "ไม่ทราบชื่อบริษัท" in str(e):
                        upsert_job_listing(url, status_key="fetch_error",
                                           error_note=f"Company name unknown: {str(e)[:200]}")
                        add_log(f"  📋 Listing: fetch_error (ไม่ทราบชื่อบริษัท)")
                    else:
                        add_log(f"  📋 Listing: คง Consider ไว้ (Notion error — retry ได้)")

                results.append(result_entry)

            if i < len(jobs_to_run) - 1:
                time.sleep(delay)

        if stats["notion_ok"] > 0:
            add_log("\n📊 Reranking all jobs...")
            try:
                rerank_all_jobs(opt, add_log)
                add_log("✅ Rerank done!")
            except Exception as e:
                add_log(f"❌ Rerank error: {e}")

        # ── Clear queue ถ้ามาจาก Listing DB ─────────────────
        if source == "listing_db":
            st.session_state["listing_queue"] = []

        progress.progress(1.0, text="เสร็จแล้ว! ✨")

        # ── Summary ───────────────────────────────────────────
        st.markdown("---")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("วิเคราะห์ได้",  stats["ok"])
        c2.metric("Push Notion",   stats["notion_ok"])
        c3.metric("📝 Drafted",    stats["drafted"])
        c4.metric("Error",         stats["err"])
        c5.metric("Notion Error",  stats["notion_err"])

        all_a = [r["analysis"] for r in results if r.get("status") == "ok"]
        if all_a:
            st.markdown("**ผลการตัดสิน**")
            ca, cw, cp = st.columns(3)
            n_apply = sum(1 for a in all_a if a.get("apply_decision") == "APPLY")
            ca.metric("✅ APPLY",     f"{n_apply} งาน (drafted {stats['drafted']})")
            cw.metric("👀 WATCHLIST", sum(1 for a in all_a if a.get("apply_decision") == "WATCHLIST"))
            cp.metric("❌ PASS",      sum(1 for a in all_a if a.get("apply_decision") == "PASS"))
            all_gaps = [g for a in all_a for g in a.get("gap_skills", [])]
            gc = {}
            for g in all_gaps:
                g = g.strip()
                if g: gc[g] = gc.get(g, 0) + 1
            top = sorted(gc.items(), key=lambda x: -x[1])[:5]
            if top:
                st.caption(f"Top skill gaps: {', '.join(f'{g}({n})' for g,n in top)}")

        # ── Failed jobs — retry ───────────────────────────────
        failed_jobs = [r for r in results if r.get("status") == "error"]
        if failed_jobs:
            st.markdown("---")
            st.warning(f"⚠️ **{len(failed_jobs)} URL fetch ไม่ได้** — วาง JD เองได้ที่นี่ค่ะ")
            for fr in failed_jobs:
                with st.expander(f"🔗 {fr['url'][:70]}  •  {fr.get('error','')}"):
                    retry_jd = st.text_area("วาง JD ที่นี่",
                                            key=f"retry_{hash(fr['url']) % 999999}",
                                            height=200, placeholder="Copy JD มาวางได้เลยค่ะ...")
                    if st.button("วิเคราะห์ + Push", key=f"btn_retry_{hash(fr['url']) % 999999}"):
                        if retry_jd.strip():
                            with st.spinner("วิเคราะห์..."):
                                a2 = analyze_with_llm(retry_jd.strip())
                            if "error" not in a2 or a2.get("job_title") != "Unknown":
                                jt2 = a2.get("job_title", "?")
                                cn2d = a2.get("company_name", "?")
                                st.success(f"✅ {jt2} @ {cn2d}")
                                if JOB_LISTING_DB_ID:
                                    upsert_job_listing(fr["url"], job_title=jt2, company_name=cn2d,
                                                       jd_raw=retry_jd.strip()[:2000], status_key="consider")
                                try:
                                    j2, c2 = analysis_to_notion_dicts(a2, fr["url"])
                                    if not j2.get("job_title","").strip():
                                        raise ValueError("no job title")
                                    cn2 = c2.get("company_name","").strip()
                                    if not cn2 or cn2.lower() in ("not specified","unknown",""):
                                        cn2_from_url = _company_from_url(fr["url"])
                                        if cn2_from_url:
                                            cn2 = cn2_from_url
                                            c2["company_name"] = cn2
                                        else:
                                            raise ValueError("ไม่ทราบชื่อบริษัท — กรุณาระบุในช่อง JD หรือแก้ใน Listing")
                                    cid, f2 = search_company(cn2)
                                    if not f2 or not cid:
                                        cid, cerr2 = create_company(c2, opt)
                                        if cerr2: raise ValueError(cerr2)
                                    ok2, _pid2_or_err = create_job(j2, cid, opt)
                                    if ok2:
                                        st.success("📤 Push Notion สำเร็จค่ะ!")
                                        if JOB_LISTING_DB_ID:
                                            # อัปเดต Listing ตามผลตัดสินของ AI (To Apply / On Hold / Pass)
                                            decision_key2 = APPLY_DECISION_TO_LISTING_STATUS.get(
                                                a2.get("apply_decision", "").upper(), "on_hold"
                                            )
                                            upsert_job_listing(fr["url"], status_key=decision_key2)
                                        with st.spinner("กำลัง rerank jobs..."):
                                            try:
                                                rerank_all_jobs(opt, lambda *_: None)
                                                st.success("✅ Rerank เสร็จแล้ว")
                                            except Exception as rerank_ex:
                                                st.warning(f"⚠️ Rerank error: {rerank_ex}")
                                    else:
                                        st.error(f"Notion error: {jerr2}")
                                except Exception as ex:
                                    st.error(f"❌ {ex}")
                            else:
                                st.error(f"LLM error: {a2.get('error')}")
                        else:
                            st.warning("กรุณาวาง JD ก่อนค่ะ")

        st.download_button("⬇️ Download jobs_analyzed.json",
                           data=json.dumps(results, ensure_ascii=False, indent=2),
                           file_name="jobs_analyzed.json", mime="application/json")


# ══════════════════════════════════════════════════════════════
# RESUME MATCHING PIPELINE (Gemini API)
# ══════════════════════════════════════════════════════════════

SPEND_TRACKER_PAGE_TITLE = "💰 API Spend Tracker"
SPEND_TRACKER_DB_ID      = st.secrets.get("SPEND_TRACKER_DB_ID", "")  # optional Notion page ID

# Exchange rate THB/USD (ใช้ estimate — อัปเดตเองถ้า rate เปลี่ยนมาก)
USD_TO_THB = 35.0
BUDGET_CAP_THB = 50.0

# Gemini 2.5 Flash pricing (June 2026) — ใช้ free tier ของ Google AI Studio
# Free tier: 10 RPM / 250 RPD ต่อ project — เพียงพอสำหรับ batch 15-20 งาน/ครั้ง
# ถ้าวันหนึ่งเปลี่ยนไปใช้ paid tier ให้แก้ค่าด้านล่างเป็นเรทจริง (เช่น Flash $0.30/$2.50 ต่อล้าน token)
GEMINI_INPUT_PER_MILLION  = 0.0   # USD — free tier
GEMINI_OUTPUT_PER_MILLION = 0.0   # USD — free tier
GEMINI_CACHE_READ_PER_MILLION = 0.0  # USD — free tier


def _tokens_to_thb(input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
    cost_usd = (
        (input_tokens      / 1_000_000) * GEMINI_INPUT_PER_MILLION
        + (output_tokens   / 1_000_000) * GEMINI_OUTPUT_PER_MILLION
        + (cache_read_tokens / 1_000_000) * GEMINI_CACHE_READ_PER_MILLION
    )
    return cost_usd * USD_TO_THB


def _current_period() -> str:
    """คืน 'YYYY-MM' ของเดือนปัจจุบัน — ใช้เป็น key reset budget รายเดือน"""
    from datetime import datetime as _dt
    return _dt.now().strftime("%Y-%m")


def get_monthly_spend_so_far() -> float:
    """
    อ่านยอด spend เดือนปัจจุบันจาก Notion Spend Tracker page
    ถ้าขึ้นเดือนใหม่ → reset Spend THB = 0 + อัปเดต current_period ก่อนคืนค่า
    budget cap เป็นแบบ "ต่อเดือน" ไม่ใช่ "ตลอดกาล"
    คืน float (บาท) — คืน 0.0 ถ้าอ่านไม่ได้
    """
    if not SPEND_TRACKER_DB_ID:
        return 0.0
    try:
        res = requests.get(
            f"https://api.notion.com/v1/pages/{SPEND_TRACKER_DB_ID}",
            headers=HEADERS
        )
        props = res.json().get("properties", {})

        # อ่าน current_period ที่บันทึกไว้
        stored_period = ""
        for key in ("current_period", "Current Period", "period"):
            if key in props:
                rt = props[key].get("rich_text", [])
                stored_period = rt[0].get("plain_text", "").strip() if rt else ""
                break

        now_period = _current_period()

        # ขึ้นเดือนใหม่ → reset spend = 0 ก่อน
        if stored_period != now_period:
            requests.patch(
                f"https://api.notion.com/v1/pages/{SPEND_TRACKER_DB_ID}",
                headers=HEADERS,
                json={"properties": {
                    "Spend THB":      {"number": 0.0},
                    "Current Period": {"rich_text": [{"text": {"content": now_period}}]},
                    "Last Updated":   {"date": {"start": datetime.now().isoformat()}},
                }}
            )
            return 0.0

        # อ่านยอด spend เดือนนี้
        for key in ("spend_thb", "Spend THB", "spend", "Spend"):
            if key in props:
                return props[key].get("number") or 0.0
        return 0.0
    except Exception:
        return 0.0


def log_token_usage(job_title: str, input_tok: int, output_tok: int, cache_tok: int = 0):
    """
    บันทึก token usage ลง Notion Spend Tracker
    ถ้าไม่มี SPEND_TRACKER_DB_ID → เขียน log ใน Streamlit เท่านั้น
    """
    cost_thb = _tokens_to_thb(input_tok, output_tok, cache_tok)
    st.write(f"  💰 {job_title}: {input_tok}in/{output_tok}out/{cache_tok}cached → {cost_thb:.2f} ฿")

    if not SPEND_TRACKER_DB_ID:
        return

    try:
        current = get_monthly_spend_so_far()  # อ่าน + reset ถ้าขึ้นเดือนใหม่
        new_total = current + cost_thb
        now_period = _current_period()

        requests.patch(
            f"https://api.notion.com/v1/pages/{SPEND_TRACKER_DB_ID}",
            headers=HEADERS,
            json={"properties": {
                "Spend THB":      {"number": round(new_total, 4)},
                "Current Period": {"rich_text": [{"text": {"content": now_period}}]},
                "Last Updated":   {"date": {"start": datetime.now().isoformat()}},
            }}
        )
    except Exception:
        pass  # spend tracking ล้มเหลว ไม่หยุดงานหลัก


def notify_budget_exceeded(current_spend: float, job_title: str = ""):
    """
    1.8 — เมื่อชน budget cap 50 ฿:
    - หยุด Gemini API ทันที (caller ต้องเช็ค return value)
    - เขียน Notion แจ้งเตือน (ถ้ามี SPEND_TRACKER_DB_ID)
    - คืน True = ชนเพดาน, False = ยังโอเค
    """
    if current_spend < BUDGET_CAP_THB:
        return False

    msg = f"🚨 Budget cap {BUDGET_CAP_THB} ฿ ถึงแล้ว (ใช้ไป {current_spend:.2f} ฿) — หยุด Gemini API อัตโนมัติ"
    if job_title:
        msg += f" | งานค้าง: {job_title} → ตั้ง On Hold"
    st.error(msg)

    if SPEND_TRACKER_DB_ID:
        try:
            requests.patch(
                f"https://api.notion.com/v1/pages/{SPEND_TRACKER_DB_ID}",
                headers=HEADERS,
                json={"properties": {"Alert": {"rich_text": [{"text": {"content": msg[:2000]}}]}}}
            )
        except Exception:
            pass

    return True


# ── RESUME MATCHING SYSTEM PROMPT (ย้ายจาก instruction prompt เดิม) ──────────

RESUME_MATCHING_SYSTEM_PROMPT = """
คุณคือ senior career strategist ที่รู้จัก Thapanee (ทับทิม) ดีมาก และมีเป้าหมายเดียวคือทำให้เธอได้งานที่ใช่ที่สุด

## กฎเหล็กที่ห้ามละเมิด
- ห้าม generic — ทุก draft ต้องอ้างอิง JD นั้นโดยเฉพาะ
- ห้ามโกหก — ไม่ใส่ skill/experience ที่ไม่มีจริง
- ห้าม hallucinate รายละเอียดโปรเจกต์ — ใช้ Project Details ที่ให้มาเท่านั้น ถ้าขาดให้บอกตรงๆ
- ห้ามใส่ชื่อบริษัทใน Resume PDF Summary — ชื่อบริษัทอยู่ใน Email เท่านั้น
- เน้น metric จริง — 96.14% accuracy, production deployed, 6 projects 5-star, solo end-to-end
- Critical gap check: ถ้า JD มี hard requirement ที่ขาดจริง (X ปีขึ้นไป / cert เฉพาะ) → flag "⚠️ CRITICAL GAP" และหยุดรอคนตัดสินใจ อย่า proceed draft

## STEP 2A — เทียบ tone ตามรูบริก 3 มิติ
1. Skill overlap: skills_emphasis ทับซ้อนกับ Key Tech Stack ของ JD กี่ตัว
2. Industry conflict: summary_paragraph มีคำเฉพาะอุตสาหกรรมที่ขัดกับบริษัทเป้าหมายไหม (เช่น "guest experience" ใช้กับ hospitality เท่านั้น) ถ้าเจอแม้ 1 จุด → ตัด Path 1 ทันที
3. use_when match: ตรงกับ industry + ลักษณะองค์กร (startup/enterprise) ไหม

## STEP 2B — Path Decision
- Path 1 ✅: ผ่านทั้ง 3 มิติ → ใช้ version เดิม ไม่ต้องแก้
- Path 2 ⚠️: skill overlap ดี แต่ติด industry conflict หรือ use_when ไม่ตรง → ใช้เป็น base + draft summary ใหม่ (ตัดคำที่ขัด)
- Path 3 🆕: ไม่มี version match เลย → สร้าง Resume Version ใหม่ + แจ้งทับทิมให้สร้าง Canva

## OUTPUT FORMAT
ตอบกลับเป็น JSON เท่านั้น ไม่มี markdown:
{
  "path": "1/2/3",
  "chosen_version": "ชื่อ version จริงจาก Resume Versions ที่ให้มา",
  "path_reason": "เหตุผล 1-2 ประโยค",
  "critical_gap": null,
  "resume_summary": "...(ห้ามใส่ชื่อบริษัท)...",
  "skills_order": ["skill1", "skill2"],
  "projects_order": [{"name": "...", "reason": "เหตุผล 1 ประโยค"}],
  "email_subject": "...",
  "email_body": "...(ย่อหน้า 1: fact จริงเกี่ยวกับบริษัท | ย่อหน้า 2: metric จาก Project Details | ย่อหน้า 3: CTA)...",
  "email_signoff": "Best, Thapanee Chaiprapha (ทับทิม) | cha.thapanee.work@gmail.com | [Phone — กรุณาเติม] | [Portfolio — กรุณาเติม] | [LinkedIn — กรุณาเติม]",
  "top3_stories": [
    {"project": "...", "star_story": "...", "likely_question": "..."}
  ],
  "technical_qa": [
    {"question": "...", "answer_guide": "..."}
  ],
  "questions_to_ask": ["...", "...", "..."],
  "salary_script": "...(Target: 35,000–45,000 THB | Floor: 30,000)...",
  "checklist_before": ["..."],
  "checklist_how_to_apply": "Platform: ... | URL: ... | Email: ...",
  "red_flags": ["max 3 bullet"],
  "new_version_fields": null
}

ถ้า critical_gap พบ → ใส่ string อธิบาย และ set ทุก draft field เป็น null ยกเว้น path, chosen_version, path_reason, critical_gap
ถ้า path = 3 → ใส่ new_version_fields: {version_id, tone_label, use_when, use_with, summary_paragraph, headline, skills_emphasis, projects_order}
"""


def _call_gemini_api(system_prompt: str, user_message: str) -> dict:
    """
    Low-level call ไปยัง Gemini API (Gemini 2.5 Flash — free tier)
    คืน (response_dict, usage_dict) หรือ raise RuntimeError
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("ไม่มี GEMINI_API_KEY ใน secrets — กรุณาเพิ่มใน Streamlit secrets")

    model = "gemini-2.5-flash"
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": GEMINI_API_KEY},
        headers={"Content-Type": "application/json"},
        json={
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {"role": "user", "parts": [{"text": user_message}]}
            ],
            "generationConfig": {
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 1024},
            },
        },
        timeout=120,
    )

    if response.status_code != 200:
        raise RuntimeError(f"Gemini API HTTP {response.status_code}: {response.text[:300]}")

    data = response.json()
    usage_meta = data.get("usageMetadata", {})
    usage = {
        "input_tokens": usage_meta.get("promptTokenCount", 0),
        "output_tokens": usage_meta.get("candidatesTokenCount", 0),
        "cache_read_input_tokens": usage_meta.get("cachedContentTokenCount", 0),
    }

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini ไม่คืน candidates — raw: {json.dumps(data)[:300]}")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)

    # parse JSON
    text_clean = text.strip()
    if text_clean.startswith("```"):
        text_clean = re.sub(r"^```[a-z]*\n?", "", text_clean)
        text_clean = re.sub(r"\n?```$", "", text_clean)
    try:
        parsed = json.loads(text_clean)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini ตอบ JSON ไม่ถูกต้อง: {e} | raw: {text[:300]}")

    return parsed, usage


def _load_project_details() -> str:
    """
    Query Project Details DB สดจาก Notion
    คืน string สรุปโปรเจกต์ทั้งหมดพร้อมต่อเข้า prompt
    """
    try:
        res = requests.post(
            f"https://api.notion.com/v1/databases/{PROJECT_DETAILS_DB_ID}/query",
            headers=HEADERS,
            json={"page_size": 20}
        )
        results = res.json().get("results", [])
        if not results:
            return "(ไม่พบ Project Details ใน DB)"

        blocks = []
        for page in results:
            p = page.get("properties", {})

            def txt(key):
                arr = (p.get(key) or {}).get("rich_text", [])
                return arr[0].get("plain_text", "").strip() if arr else ""

            def ttl(key):
                arr = (p.get(key) or {}).get("title", [])
                return arr[0].get("plain_text", "").strip() if arr else ""

            name    = ttl("Project Name") or txt("Project Name")
            ptype   = txt("Project Type")
            stack   = txt("Tech Stack")
            problem = txt("Problem/Why Built")
            actions = txt("Key Actions")
            metrics = txt("Key Metrics")
            tags    = txt("Relevance Tags")
            star    = txt("STAR Story")
            qa      = txt("Can Answer Questions")
            link    = txt("Portfolio Link")

            if not name:
                continue

            block = f"PROJECT: {name}"
            if ptype:   block += f"\n  Type: {ptype}"
            if stack:   block += f"\n  Stack: {stack}"
            if problem: block += f"\n  Why: {problem}"
            if actions: block += f"\n  Actions: {actions}"
            if metrics: block += f"\n  Metrics: {metrics}"
            if tags:    block += f"\n  Tags: {tags}"
            if star:    block += f"\n  STAR: {star}"
            if qa:      block += f"\n  Can Answer: {qa}"
            if link:    block += f"\n  Portfolio: {link}"
            blocks.append(block)

        return "\n\n".join(blocks) if blocks else "(Project Details DB ว่าง)"
    except Exception as e:
        return f"(โหลด Project Details ไม่สำเร็จ: {e})"


def _create_application_draft_subpage(job_page_id: str, job_title: str, company_name: str, draft: dict):
    """
    สร้าง sub-page "📝 Application Draft — [Job Title] - [Company Name]" ใน Notion
    sub-page naming บังคับ format ตายตัว (แก้ bug 1.3.3)
    """
    page_title = f"📝 Application Draft — {job_title} - {company_name}"

    def heading(text, level=2):
        t = f"heading_{level}"
        return {"object": "block", "type": t, t: {"rich_text": [{"type": "text", "text": {"content": text}}]}}

    def para(text):
        # แบ่งถ้า text ยาวเกิน 2000 chars (Notion limit ต่อ block)
        chunks = [text[i:i+1999] for i in range(0, len(text), 1999)]
        return [{"object": "block", "type": "paragraph",
                 "paragraph": {"rich_text": [{"type": "text", "text": {"content": c}}]}}
                for c in chunks]

    def bullet(text):
        return {"object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text[:1999]}}]}}

    children = []

    # Path info
    path_label = {"1": "✅ Path 1 — ใช้ version เดิม", "2": "⚠️ Path 2 — ปรับ summary", "3": "🆕 Path 3 — สร้าง version ใหม่"}.get(
        str(draft.get("path", "")), f"Path {draft.get('path','?')}"
    )
    children.append(heading(f"{path_label} | {draft.get('chosen_version', '?')}", level=3))
    if draft.get("path_reason"):
        children += para(draft["path_reason"])

    # Critical gap warning
    if draft.get("critical_gap"):
        children.append(heading("🚨 CRITICAL GAP — รอการตัดสินใจจากทับทิม", level=2))
        children += para(str(draft["critical_gap"]))
    else:
        # Resume Summary
        children.append(heading("📄 Resume Summary"))
        children += para(draft.get("resume_summary") or "(ไม่มีข้อมูล)")

        if draft.get("skills_order"):
            children.append(heading("🛠️ Skills Order", level=3))
            for s in draft["skills_order"]:
                children.append(bullet(s))

        if draft.get("projects_order"):
            children.append(heading("📂 Projects Order", level=3))
            for proj in draft["projects_order"]:
                children.append(bullet(f"{proj.get('name','?')} — {proj.get('reason','')}"))

        # Email
        children.append(heading("✉️ Email Draft"))
        if draft.get("email_subject"):
            children += para(f"Subject: {draft['email_subject']}")
        children += para(draft.get("email_body") or "(ไม่มีข้อมูล)")
        if draft.get("email_signoff"):
            children += para(draft["email_signoff"])

        # Interview Prep
        children.append(heading("🎯 Interview Prep"))

        if draft.get("top3_stories"):
            children.append(heading("TOP 3 STORIES", level=3))
            for s in draft["top3_stories"]:
                children.append(bullet(f"[{s.get('project','?')}] Q: {s.get('likely_question','')}"))
                children += para(s.get("star_story", ""))

        if draft.get("technical_qa"):
            children.append(heading("Technical Q&A", level=3))
            for q in draft["technical_qa"]:
                children.append(bullet(f"Q: {q.get('question','')}"))
                children += para(f"A: {q.get('answer_guide','')}")

        if draft.get("questions_to_ask"):
            children.append(heading("คำถามถามกลับ", level=3))
            for q in draft["questions_to_ask"]:
                children.append(bullet(q))

        if draft.get("salary_script"):
            children.append(heading("💰 Salary Script", level=3))
            children += para(draft["salary_script"])

        # Checklist
        children.append(heading("✅ Checklist"))
        if draft.get("checklist_before"):
            children.append(heading("Before Applying", level=3))
            for item in draft["checklist_before"]:
                children.append(bullet(item))
        if draft.get("checklist_how_to_apply"):
            children.append(heading("How to Apply", level=3))
            children += para(draft["checklist_how_to_apply"])
        if draft.get("red_flags"):
            children.append(heading("🚩 Red Flags", level=3))
            for rf in draft["red_flags"]:
                children.append(bullet(rf))

    # สร้าง sub-page
    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=HEADERS,
        json={
            "parent": {"page_id": job_page_id},
            "properties": {
                "title": {"title": [{"text": {"content": page_title}}]}
            },
            "children": children[:100]  # Notion limit 100 blocks ต่อ request
        }
    )
    return res.status_code in (200, 201), res.json().get("id")


def _update_matched_resume_relation(job_page_id: str, resume_version_name: str) -> bool:
    """
    อัปเดต Matched Resume (relation) ใน Job Pipeline
    ต้องค้นหา page ID ของ Resume Version จาก Resume Versions DB ก่อน

    ⚠️ Known limitation (Bug 4 — ไม่เร่งด่วน):
    Query ด้วย page_size=50 ครั้งเดียว ไม่ paginate
    ตอนนี้มี ~10 versions — จะพังเงียบๆ ถ้ามี version เกิน 50 ในอนาคต
    TODO: เพิ่ม has_more/next_cursor loop ถ้า version เพิ่มมากขึ้น
    """
    try:
        # ค้นหา Resume Version page ที่ชื่อตรงกัน
        res = requests.post(
            f"https://api.notion.com/v1/databases/{RESUME_VERSIONS_DB_ID}/query",
            headers=HEADERS,
            json={"page_size": 50}
        )
        for page in res.json().get("results", []):
            p = page.get("properties", {})
            vid_arr = (p.get("version_id") or {}).get("title", [])
            vid = vid_arr[0].get("plain_text", "").strip() if vid_arr else ""
            if vid == resume_version_name:
                resume_page_id = page["id"]
                # อัปเดต Matched Resume relation
                patch = requests.patch(
                    f"https://api.notion.com/v1/pages/{job_page_id}",
                    headers=HEADERS,
                    json={"properties": {
                        "Matched Resume": {"relation": [{"id": resume_page_id}]}
                    }}
                )
                return patch.status_code in (200, 204)
        return False  # ไม่เจอ version นั้นใน DB
    except Exception:
        return False


def resume_matching_pipeline(
    job_page_id: str,
    job_title: str,
    company_name: str,
    job_data: dict,
    log_fn=None,
    resume_versions_cache: list | None = None,   # ถ้า None → ดึงใหม่เอง
    project_details_cache: list | None = None,   # ถ้า None → ดึงใหม่เอง
) -> dict:
    """
    1.7 — Resume Matching + Application Draft ด้วย Gemini API
    เรียกหลังจาก Job Pipeline ingestion สำเร็จแล้วเท่านั้น

    job_data ควรมี: key_tech_stack, fit_level, ai_depth_score, gaps, notes,
                    apply_email, salary_min, salary_max
    resume_versions_cache / project_details_cache: ส่ง pre-loaded cache จาก STEP 0 ได้
    ถ้าไม่ส่ง → ดึงเองจาก Notion (standalone call)
    คืน {"success": bool, "path": "1/2/3", "error": str|None}
    """
    def log(msg):
        if log_fn:
            log_fn(msg)

    # ── เช็ค budget cap ก่อนเสมอ ────────────────────────────
    current_spend = get_monthly_spend_so_far()
    if notify_budget_exceeded(current_spend, job_title):
        # ตั้ง Apply Status → On Hold
        requests.patch(
            f"https://api.notion.com/v1/pages/{job_page_id}",
            headers=HEADERS,
            json={"properties": {
                "Apply Status": {"select": {"name": "⏸️ On Hold"}},
                "Notes": {"rich_text": [{"text": {"content":
                    f"[AUTO] On Hold — Budget cap {BUDGET_CAP_THB} ฿ ถึงแล้ว (ใช้ไป {current_spend:.2f} ฿)"
                }}]}
            }}
        )
        return {"success": False, "path": None, "error": f"Budget cap exceeded ({current_spend:.2f} ฿)"}

    log(f"  💡 Budget check: {current_spend:.2f}/{BUDGET_CAP_THB} ฿ — OK")

    # ── โหลด context — ใช้ cache ถ้ามี ไม่งั้นดึงใหม่ ────────
    if resume_versions_cache is not None:
        resume_versions_text = _format_resume_versions_cache(resume_versions_cache)
    else:
        resume_versions_text = load_resume_versions_summary()

    if project_details_cache is not None:
        project_details_text = _format_project_details_cache(project_details_cache)
    else:
        project_details_text = _load_project_details()

    # ── สร้าง user message ────────────────────────────────────
    user_msg = f"""
## JOB ที่ต้องทำ
- Job Title: {job_title}
- Company: {company_name}
- Key Tech Stack: {job_data.get('key_tech_stack', 'ไม่ระบุ')}
- Fit Level: {job_data.get('fit_level', 'ไม่ระบุ')}
- AI Depth Score: {job_data.get('ai_depth_score', 'ไม่ระบุ')}
- Salary Min/Max: {job_data.get('salary_min', 0)}-{job_data.get('salary_max', 0)} THB
- Apply Email: {job_data.get('apply_email', 'ไม่ระบุ')}
- Gaps to Address: {job_data.get('gaps', 'ไม่มี')}
- Notes: {job_data.get('notes', '')}

## RESUME VERSIONS ที่มีอยู่จริงใน Notion
{resume_versions_text}

## PROJECT DETAILS (ใช้เป็น source of truth เท่านั้น — ห้าม hallucinate)
{project_details_text}

ทำ STEP 2A-2B และ STEP 3 ทั้งหมด ตอบกลับเป็น JSON ตาม output format ใน system prompt
"""

    # ── เรียก Gemini API ──────────────────────────────────────
    log(f"  🤖 เรียก Gemini 2.5 Flash...")
    try:
        draft, usage = _call_gemini_api(RESUME_MATCHING_SYSTEM_PROMPT, user_msg)
    except RuntimeError as e:
        log(f"  ❌ Gemini API error: {e}")
        return {"success": False, "path": None, "error": str(e)}

    # บันทึก token usage
    log_token_usage(
        job_title,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("cache_read_input_tokens", 0),
    )

    # ── Critical gap check ────────────────────────────────────
    if draft.get("critical_gap"):
        log(f"  🚨 CRITICAL GAP พบ — หยุดรอคนตัดสินใจ: {draft['critical_gap']}")
        # สร้าง sub-page แจ้งเตือนแต่ไม่ draft สมบูรณ์
        _create_application_draft_subpage(job_page_id, job_title, company_name, draft)
        # อัปเดต Notes ใน Job Pipeline
        requests.patch(
            f"https://api.notion.com/v1/pages/{job_page_id}",
            headers=HEADERS,
            json={"properties": {"Notes": {"rich_text": [{"text": {"content":
                f"[AUTO] ⚠️ CRITICAL GAP: {str(draft['critical_gap'])[:500]}"
            }}]}}}
        )
        return {"success": True, "path": draft.get("path"), "error": "critical_gap"}

    # ── Path 3: สร้าง Resume Version ใหม่ใน Notion ───────────
    if str(draft.get("path")) == "3" and draft.get("new_version_fields"):
        nv = draft["new_version_fields"]
        log(f"  🆕 Path 3 — สร้าง Resume Version ใหม่: {nv.get('version_id','?')}")
        try:
            requests.post(
                "https://api.notion.com/v1/pages",
                headers=HEADERS,
                json={
                    "parent": {"database_id": RESUME_VERSIONS_DB_ID},
                    "properties": {
                        "version_id":        {"title": [{"text": {"content": nv.get("version_id", "")}}]},
                        "tone_label":        {"rich_text": [{"text": {"content": nv.get("tone_label", "")[:2000]}}]},
                        "use_when":          {"rich_text": [{"text": {"content": nv.get("use_when", "")}}]},
                        "use_with":          {"rich_text": [{"text": {"content": nv.get("use_with", "")}}]},
                        "summary_paragraph": {"rich_text": [{"text": {"content": nv.get("summary_paragraph", "")}}]},
                        "headline":          {"rich_text": [{"text": {"content": nv.get("headline", "")}}]},
                        "skills_emphasis":   {"rich_text": [{"text": {"content": nv.get("skills_emphasis", "")}}]},
                        "projects_order":    {"rich_text": [{"text": {"content": str(nv.get("projects_order", ""))}}]},
                        "canva_link":        {"url": None},
                        "last_updated":      {"date": {"start": datetime.now().date().isoformat()}},
                    }
                }
            )
            log(f"  ✅ สร้าง Resume Version {nv.get('version_id')} สำเร็จ — กรุณาสร้าง Canva แล้วใส่ link กลับมา")
        except Exception as e:
            log(f"  ⚠️ สร้าง Resume Version ใหม่ไม่สำเร็จ: {e}")

    # ── เขียน Application Draft sub-page ─────────────────────
    log(f"  📝 สร้าง sub-page Application Draft...")
    ok_page, subpage_id = _create_application_draft_subpage(
        job_page_id, job_title, company_name, draft
    )
    if ok_page:
        log(f"  ✅ Sub-page สำเร็จ: {subpage_id}")
    else:
        log(f"  ⚠️ Sub-page สร้างไม่สำเร็จ")

    # ── อัปเดต Matched Resume relation ───────────────────────
    chosen = draft.get("chosen_version", "")
    if chosen:
        log(f"  🔗 อัปเดต Matched Resume → {chosen}")
        linked = _update_matched_resume_relation(job_page_id, chosen)
        if not linked:
            log(f"  ⚠️ ไม่พบ Resume Version '{chosen}' ใน DB — ข้าม relation")

    return {
        "success": True,
        "path": draft.get("path"),
        "chosen_version": chosen,
        "subpage_id": subpage_id,
        "error": None,
    }








# ══════════════════════════════════════════════════════════════
# ADMIN TAB — Backfill + JD Field + Budget
# ══════════════════════════════════════════════════════════════

with tab4:
    with st.expander("⚙️ Admin Tools (Developer)", expanded=False):
        st.subheader("ขั้น 1 — Backfill Apply Status ที่ว่าง")
        st.caption("รันครั้งเดียวก่อนเปิดระบบอัตโนมัติ — เติม Apply Status ให้ทุก row ที่ว่างใน Job Pipeline DB")
        if st.button("🔧 Run Backfill"):
            with st.spinner("กำลัง backfill..."):
                fixed, errs = backfill_apply_status()
            if errs:
                st.warning(f"แก้ได้ {fixed} รายการ | Errors: {errs[:5]}")
            else:
                st.success(f"✅ Backfill เสร็จ — แก้ {fixed} รายการ")

        st.divider()
        st.subheader("ขั้น 2 — เพิ่ม field 'JD' ใน Job Listings DB")
        st.caption("รันครั้งเดียว — ถ้ามีอยู่แล้วจะแจ้งว่า 'มีแล้ว'")
        if st.button("➕ Add JD Field"):
            with st.spinner("กำลังเพิ่ม field..."):
                ok, msg = add_jd_field_to_job_listings_db()
            if ok:
                st.success(f"✅ {msg}")
            else:
                st.error(f"❌ {msg}")

        st.divider()
        st.subheader("💰 Budget Tracker")
        spend = get_monthly_spend_so_far()
        pct   = min(spend / BUDGET_CAP_THB, 1.0)
        st.progress(pct, text=f"ใช้ไป {spend:.2f} / {BUDGET_CAP_THB:.0f} ฿ ({pct*100:.0f}%)")
        if spend >= BUDGET_CAP_THB:
            st.error("🚨 Budget cap ถึงแล้ว — Gemini API หยุดทำงานอัตโนมัติ")
        elif spend >= BUDGET_CAP_THB * 0.8:
            st.warning("⚠️ เกือบถึง budget cap แล้ว")

        st.divider()
        st.subheader("🧪 Test Resume Matching (Manual)")
        st.caption("ทดสอบ resume_matching_pipeline() กับ job page ID ที่มีอยู่แล้วใน Notion")
        test_page_id = st.text_input("Job Page ID (จาก Notion URL)", key="test_rm_page_id")
        test_title   = st.text_input("Job Title", key="test_rm_title")
        test_company = st.text_input("Company Name", key="test_rm_company")
        if st.button("▶️ Run Resume Matching") and test_page_id and test_title and test_company:
            test_logs = []
            test_log_area = st.empty()
            def _test_log(msg):
                test_logs.append(msg)
                test_log_area.code("\n".join(test_logs[-30:]))
            result = resume_matching_pipeline(
                job_page_id=test_page_id,
                job_title=test_title,
                company_name=test_company,
                job_data={},
                log_fn=_test_log,
            )
            if result.get("success"):
                st.success(f"✅ Path {result.get('path')} | Version: {result.get('chosen_version')}")
            else:
                st.error(f"❌ {result.get('error')}")
