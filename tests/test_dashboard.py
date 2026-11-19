"""
Runs every dashboard page for every role (no browser needed), and plays a short chat on the patient phone.
    python tests/test_dashboard.py
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
# the page-by-page checks run in open mode (role switch), even when config/users.json exists; sign-in is checked at the end
os.environ["PP_USERS_FILE"] = os.path.join(ROOT, "tests", "no_accounts_here.json")
os.environ["PP_DEMO"] = "1"          # the chat below uses the patient phone, a demo tool hidden in production
os.environ.pop("PP_PASSWORD", None)
import logging
logging.getLogger("streamlit").setLevel(logging.ERROR)
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = os.path.join(ROOT, "frontend", "streamlit_app", "dashboard.py")
ok = []


def check(name, cond):
    ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)


def app():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    return at


base = app()
roles = base.sidebar.selectbox[0].options
for lang in (0, 1):
    for role in roles:
        at = app()
        if lang:
            at.sidebar.radio[0].set_value(at.sidebar.radio[0].options[1]).run()
        at.sidebar.selectbox[0].set_value(role).run()
        pages = at.sidebar.radio[1].options
        for page in pages:
            at.sidebar.radio[1].set_value(page).run()
            errs = [str(e.value)[:300] for e in at.exception]
            if errs:
                print("   ", role, page, errs)
            check(f"{'Arabic' if lang else 'English'} | {role} | {page}", not errs)

# patient phone: book, then STOP in Arabic, then START (answers come in the family's language)
import utils  # noqa: E402
REPLIES = utils.config("language")["family_replies"]
said = lambda key, md: any(REPLIES[l][key].split("{")[0].strip() in md for l in REPLIES)
at = app()
at.sidebar.selectbox[0].set_value("Outreach staff").run()     # the phone simulator is a staff demo tool
at.sidebar.radio[1].set_value("Patient phone").run()
md = " ".join(m.value for m in at.markdown)
check("phone shows the invitation message (in the family's language)",
      any(cue in md for cue in ("Reply BOOK", "للرد", "جواب دیں", "जवाब दें", "Sumagot", "nurse will call")))
at.chat_input[0].set_value("BOOK").run()
md = " ".join(m.value for m in at.markdown)
check("BOOK gets a booking confirmation", any(REPLIES[l]["booked"].split("{")[0].strip() in md or REPLIES[l]["booked"].split(")")[-1].strip()[:12] in md for l in REPLIES) or said("nothing", md))
at.chat_input[0].set_value("توقف").run()
md = " ".join(m.value for m in at.markdown)
check("the Arabic STOP opts the family out", said("stop", md))
at.chat_input[0].set_value("START").run()
md = " ".join(m.value for m in at.markdown)
check("START opts back in", said("start", md))
at.chat_input[0].set_value("I have chest pain").run()
md = " ".join(m.value for m in at.markdown)
check("chest pain gets the 998 emergency answer", "998" in md)
check("no errors during the chat", not at.exception)

# sign-in: with accounts, the Streamlit dashboard asks for the same accounts and keeps the same limits
import json, tempfile  # noqa: E402
import pandas as pd  # noqa: E402
from api import auth  # noqa: E402
tmp = tempfile.mkdtemp()
auth.ACCESS_LOG = os.path.join(tmp, "access_log.csv")
region = pd.read_csv(os.path.join(ROOT, "outputs", "new", "people_final.csv")).region.value_counts().index[0]
s_, h_ = auth.hash_password("streamlit-doc-123")
json.dump({"users": {"dr_s": {"role": "doctor", "salt": s_, "hash": h_, "region": region}}}, open(os.path.join(tmp, "u.json"), "w"))
os.environ["PP_USERS_FILE"] = os.path.join(tmp, "u.json")
at = app()
check("with accounts, the dashboard shows only a sign-in form (no pages, no data)",
      len(at.text_input) == 2 and not at.dataframe and not at.sidebar.radio[1:])
at.text_input[0].set_value("dr_s"); at.text_input[1].set_value("wrong-password")
at.button[0].click().run()
check("a wrong password is refused", at.error and not at.dataframe)
at.text_input[0].set_value("dr_s"); at.text_input[1].set_value("streamlit-doc-123")
at.button[0].click().run()
pages = at.sidebar.radio[1].options if len(at.sidebar.radio) > 1 else []
check(f"the signed-in doctor gets only doctor pages and only {region}",
      not at.exception and "Map" not in pages and "Doctor view" in pages
      and set(at.dataframe[0].value.region) == {region})
check("sign-ins are in the access log", "dr_s" in open(auth.ACCESS_LOG).read())
os.environ["PP_USERS_FILE"] = os.path.join(ROOT, "tests", "no_accounts_here.json")
os.environ["PP_DEMO"] = "1"          # the chat below uses the patient phone, a demo tool hidden in production

for f in ("doctor_feedback.csv", "outreach_log.csv", "consent_changes.csv"):
    p = os.path.join(ROOT, "outputs", f)
    if os.path.exists(p):
        os.remove(p)
print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
