import os
import sys
import json
import time
import random
import smtplib
import csv
import datetime
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

# ----------------- CONFIGURATION -----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "recipients.csv")
HTML_TEMPLATE_PATH = os.path.join(BASE_DIR, "email_template.html")
STATE_PATH = os.path.join(BASE_DIR, "state.json")

CONFIG = {
    "smtp_server": os.environ.get("SMTP_SERVER", "smtp.gmail.com"),
    "smtp_port": int(os.environ.get("SMTP_PORT", 587)),
    "sender_email": os.environ.get("SENDER_EMAIL", "support@contactlejurist.com"),
    "app_password": os.environ.get("APP_PASSWORD", "uzwxpwlgtomlgeud"),
    "sender_name": os.environ.get("SENDER_NAME", "منظومة lejurist القضائية"),
    "daily_limit": int(os.environ.get("DAILY_LIMIT", 100)),
    "min_delay_minutes": float(os.environ.get("MIN_DELAY_MINUTES", 12.0)),
    "max_delay_minutes": float(os.environ.get("MAX_DELAY_MINUTES", 16.0))
}

PORT = int(os.environ.get("PORT", 8080))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

# ----------------- IN-MEMORY STATE -----------------
runtime = {
    "status": "RUNNING",
    "next_send_time": None,
    "next_recipient": None,
    "last_sent": None,
    "logs": []
}

def log_event(msg):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{now}] {msg}"
    print(entry, flush=True)
    runtime["logs"].insert(0, entry)
    if len(runtime["logs"]) > 50:
        runtime["logs"].pop()

def load_state():
    today_str = datetime.date.today().isoformat()
    default_state = {
        "date": today_str,
        "sent_today": 0,
        "total_sent_all_time": 0,
        "sent_emails": {},
        "failed_emails": {}
    }
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
                if state.get("date") != today_str:
                    state["date"] = today_str
                    state["sent_today"] = 0
                return state
        except Exception:
            pass
    save_state(default_state)
    return default_state

def save_state(state):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving state: {e}", flush=True)

def load_recipients():
    recipients = []
    if not os.path.exists(CSV_PATH):
        return recipients
    with open(CSV_PATH, "r", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            em = (row.get("email") or row.get("البريد الإلكتروني") or "").strip().lower()
            nm = (row.get("name") or row.get("الاسم الكامل") or "").strip()
            city = (row.get("city") or row.get("المدينة") or "").strip()
            spec = (row.get("specialty") or row.get("التخصص") or "").strip()
            if em and "@" in em and "cnej" not in em:
                recipients.append({
                    "email": em,
                    "name": nm or "السيد الخبير المحترم",
                    "city": city or "المغرب",
                    "specialty": spec or "خبير قضائي"
                })
    return recipients

def send_single_email(to_email, to_name, to_city, to_spec):
    if not os.path.exists(HTML_TEMPLATE_PATH):
        raise FileNotFoundError(f"Template not found at {HTML_TEMPLATE_PATH}")

    with open(HTML_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html_template = f.read()

    # Personalize greeting
    greeting_target = "الأستاذ(ة) الفاضل(ة) والخبير القضائي المحترم،"
    greeting_replacement = f"الأستاذ(ة) الخبير(ة) <strong>{to_name}</strong> المحترم(ة) ({to_city})،"
    if greeting_target in html_template:
        html_template = html_template.replace(greeting_target, greeting_replacement)

    subject = f"دعوة رسمية للأستاذ {to_name} • اكتشف منظومة lejurist لأتمتة تقارير الخبرة القضائية"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((CONFIG["sender_name"], CONFIG["sender_email"]))
    msg["To"] = formataddr((to_name, to_email))
    msg["Reply-To"] = CONFIG["sender_email"]

    plain_text = f"""السلام عليكم ورحمة الله وبركاته،
الأستاذ الخبير {to_name} المحترم ({to_city})،

يسرنا دعوتكم لاكتشاف منصة lejurist (https://lejurist.com)، المنظومة المغربية الأولى بالذكاء الاصطناعي لإدارة ملفات الخبرة القضائية وصياغة التقارير الفنية.

الرابط الرسمي:
https://lejurist.com

مع أطيب التحيات،
منصة lejurist • المغرب
"""
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_template, "html", "utf-8"))

    server = smtplib.SMTP(CONFIG["smtp_server"], CONFIG["smtp_port"], timeout=25)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(CONFIG["sender_email"], CONFIG["app_password"])
    server.sendmail(CONFIG["sender_email"], [to_email], msg.as_string())
    server.quit()
    return True

# ----------------- BACKGROUND WORKER -----------------
def mailer_loop():
    log_event("🚀 بدأ تشغيل محرك إرسال الإيميلات السحابي...")
    while True:
        try:
            state = load_state()
            recipients = load_recipients()
            pending = [r for r in recipients if r["email"] not in state.get("sent_emails", {})]

            # Day boundary check
            today_str = datetime.date.today().isoformat()
            if state.get("date") != today_str:
                state["date"] = today_str
                state["sent_today"] = 0
                save_state(state)

            if state["sent_today"] >= CONFIG["daily_limit"]:
                runtime["status"] = f"COMPLETED_TODAY ({state['sent_today']}/{CONFIG['daily_limit']})"
                runtime["next_recipient"] = None
                log_event(f"✅ تم استيفاء حصة اليوم ({CONFIG['daily_limit']} إيميل). انتظار الغد صباحاً...")
                time.sleep(300)
                continue

            if not pending:
                runtime["status"] = "ALL_COMPLETED"
                runtime["next_recipient"] = None
                log_event("🎉 اكتمل إرسال جميع الإيميلات في القائمة بالكامل!")
                time.sleep(600)
                continue

            target = pending[0]
            runtime["next_recipient"] = target
            runtime["status"] = "SENDING"

            log_event(f"📤 جاري الإرسال إلى: {target['name']} ({target['email']}) - {target['city']}...")
            try:
                send_single_email(target["email"], target["name"], target["city"], target["specialty"])
                state["sent_today"] += 1
                state["total_sent_all_time"] += 1
                state["sent_emails"][target["email"]] = {
                    "name": target["name"],
                    "city": target["city"],
                    "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "SENT"
                }
                save_state(state)
                runtime["last_sent"] = target
                log_event(f"✅ تم الإرسال بنجاح! [اليوم: {state['sent_today']}/{CONFIG['daily_limit']} | الإجمالي: {state['total_sent_all_time']}]")
            except Exception as e:
                log_event(f"❌ خطأ أثناء الإرسال إلى {target['email']}: {e}")
                state["failed_emails"][target["email"]] = str(e)
                save_state(state)

            if state["sent_today"] >= CONFIG["daily_limit"]:
                runtime["status"] = f"COMPLETED_TODAY ({state['sent_today']}/{CONFIG['daily_limit']})"
                runtime["next_recipient"] = None
                log_event(f"🎉 تم إكمال الحصة اليومية بالكامل ({CONFIG['daily_limit']} إيميل). في أمان لحماية الدومين.")
                time.sleep(300)
                continue

            # Random interval: 12 to 16 minutes
            delay_minutes = random.uniform(CONFIG["min_delay_minutes"], CONFIG["max_delay_minutes"])
            delay_seconds = int(delay_minutes * 60)
            runtime["next_send_time"] = time.time() + delay_seconds
            log_event(f"⏳ استراحة طبيعية لحماية البريد: {delay_minutes:.1f} دقيقة قبل الإيميل القادم...")

            # Wait with frequent checks
            target_time = time.time() + delay_seconds
            while time.time() < target_time:
                time.sleep(5)

        except Exception as e:
            log_event(f"⚠️ خطأ غير متوقع في محرك الإرسال: {e}")
            time.sleep(60)

# ----------------- SELF KEEP-ALIVE -----------------
def keep_alive_loop():
    time.sleep(30)
    while True:
        try:
            url = RENDER_EXTERNAL_URL or f"http://127.0.0.1:{PORT}/healthz"
            req = urllib.request.Request(url, headers={"User-Agent": "LeJurist-Self-Pinger/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                pass
        except Exception:
            pass
        time.sleep(480) # every 8 mins

# ----------------- WEB DASHBOARD -----------------
class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/healthz", "/ping"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        state = load_state()
        recipients = load_recipients()
        total_recipients = len(recipients)
        total_sent = state.get("total_sent_all_time", 0)
        sent_today = state.get("sent_today", 0)
        daily_limit = CONFIG["daily_limit"]
        remaining_today = max(0, daily_limit - sent_today)

        next_sec = int(max(0, (runtime["next_send_time"] or 0) - time.time()))
        next_min = next_sec // 60
        next_rem_sec = next_sec % 60

        next_user = runtime.get("next_recipient") or {}
        next_info = f"{next_user.get('name', '—')} ({next_user.get('city', '—')}) - {next_user.get('email', '—')}" if next_user else "لا يوجد (اكتملت الحصة)"

        logs_html = "".join([f"<div style='font-family: monospace; font-size: 12px; margin-bottom: 6px; padding: 6px 10px; background: rgba(15,23,42,0.6); border-radius: 6px; border-right: 3px solid #38bdf8;'>{entry}</div>" for entry in runtime["logs"][:25]])

        html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>لوحة متابعة البوت السحابي • lejurist</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 25px;
      background: #090d16; color: #f1f5f9;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      direction: rtl; text-align: right;
    }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    .header {{
      background: linear-gradient(135deg, #0f172a, #1e3a8a);
      padding: 30px; border-radius: 16px; border: 1px solid rgba(56,189,248,0.2);
      margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px;
    }}
    .brand {{ font-size: 26px; font-weight: 900; }}
    .brand span {{ color: #38bdf8; }}
    .status-badge {{
      background: #10b981; color: #ffffff; padding: 6px 16px; border-radius: 999px;
      font-size: 13px; font-weight: 800; display: inline-flex; align-items: center; gap: 6px;
    }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 25px; }}
    .card {{
      background: #0f172a; border: 1px solid #1e293b; border-radius: 14px; padding: 20px;
    }}
    .card-title {{ font-size: 12px; color: #94a3b8; margin-bottom: 8px; font-weight: 700; }}
    .card-value {{ font-size: 28px; font-weight: 900; color: #38bdf8; }}
    .card-sub {{ font-size: 11px; color: #64748b; margin-top: 5px; }}
    .section {{
      background: #0f172a; border: 1px solid #1e293b; border-radius: 14px; padding: 22px; margin-bottom: 25px;
    }}
    .section-title {{ font-size: 15px; font-weight: 800; margin-bottom: 15px; color: #e2e8f0; display: flex; align-items: center; gap: 8px; }}
    .logs-box {{
      max-height: 350px; overflow-y: auto; background: #020617; border-radius: 10px; padding: 15px; border: 1px solid #1e293b;
    }}
    .next-box {{
      background: #1e293b; border-radius: 10px; padding: 16px; border-right: 4px solid #38bdf8;
    }}
  </style>
  <meta http-equiv="refresh" content="15">
</head>
<body>
  <div class="container">
    <div class="header">
      <div>
        <div class="brand">le<span>jurist</span> • البوت السحابي الذكي</div>
        <div style="font-size: 13px; color: #94a3b8; margin-top: 4px;">إرسال رسائل التعريف للسادة الخبراء القضائيين • 24/24</div>
      </div>
      <div>
        <span class="status-badge">🟢 البوت نشط في السحابة</span>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="card-title">📨 تم الإرسال اليوم</div>
        <div class="card-value">{sent_today} <span style="font-size:16px; color:#64748b;">/ {daily_limit}</span></div>
        <div class="card-sub">المتبقي لليوم: {remaining_today} إيميل</div>
      </div>
      <div class="card">
        <div class="card-title">📈 إجمالي المرسل الشامل</div>
        <div class="card-value">{total_sent} <span style="font-size:16px; color:#64748b;">/ {total_recipients}</span></div>
        <div class="card-sub">من أصل جميع الخبراء</div>
      </div>
      <div class="card">
        <div class="card-title">⏳ موعد الإيميل القادم</div>
        <div class="card-value" style="color: #f59e0b;">{next_min:02d}:{next_rem_sec:02d}</div>
        <div class="card-sub">فاصل أمان بين 12 و 16 دقيقة</div>
      </div>
      <div class="card">
        <div class="card-title">⚙️ حساب الإرسال</div>
        <div class="card-value" style="font-size: 15px; color: #e2e8f0; word-break: break-all;">{CONFIG['sender_email']}</div>
        <div class="card-sub">Google SMTP • TLS</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">👤 الخبير القادم في قائمة الإرسال:</div>
      <div class="next-box">
        <div style="font-size: 15px; font-weight: 700; color: #f8fafc;">{next_info}</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">📜 سجل العمليات الحي (Live Logs) - يتحدث تلقائياً:</div>
      <div class="logs-box">
        {logs_html if logs_html else "<div style='color: #64748b;'>جاري بدء العمليات...</div>"}
      </div>
    </div>
  </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

def start_server():
    server = HTTPServer(("0.0.0.0", PORT), RequestHandler)
    log_event(f"🌐 خادم الويب بدأ العمل على البورت {PORT}")
    server.serve_forever()

if __name__ == "__main__":
    t_mailer = threading.Thread(target=mailer_loop, daemon=True)
    t_mailer.start()

    t_alive = threading.Thread(target=keep_alive_loop, daemon=True)
    t_alive.start()

    start_server()
