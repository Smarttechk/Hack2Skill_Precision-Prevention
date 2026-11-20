"""
The Care Assistant (api/assistant.py): optional, read-only, and safe. A scripted stand-in model plays the service, so these checks need
no API key and make no network calls.
    python tests/test_assistant.py
"""
import ast
import json
import os
import sys
import tempfile
import warnings
from types import SimpleNamespace

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ["PP_USERS_FILE"] = os.path.join(ROOT, "tests", "no_accounts_here.json")        # open mode, as in test_api.py
os.environ.pop("PP_PASSWORD", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
os.environ["PP_OUTREACH_DIR"] = tempfile.mkdtemp()
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import utils  # noqa: E402
from api import auth as _a  # noqa: E402
_a.SESSIONS_FILE = os.path.join(tempfile.mkdtemp(), "sessions.json")
_a.ACCESS_LOG = os.path.join(os.path.dirname(_a.SESSIONS_FILE), "access_log.csv")
_a.LIMITS_FILE = os.path.join(os.path.dirname(_a.SESSIONS_FILE), "auth_limits.json")
from api import assistant as A  # noqa: E402
import api.app as appmod  # noqa: E402
from agents import outreach as agent_outreach  # noqa: E402

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


# ------------------------------------------------------------------ a scripted stand-in for the service
def text(t):
    return SimpleNamespace(type="text", text=t)


def use(name, args, i):
    return SimpleNamespace(type="tool_use", id=f"toolu_{i}", name=name, input=args)


def resp(reason, *blocks):
    return SimpleNamespace(stop_reason=reason, content=list(blocks), model="claude-opus-5-5")


class Fake:
    def __init__(self, script, beta_ok=True):
        self.script, self.calls, self.beta_ok = list(script), [], beta_ok
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._beta))
        self.messages = SimpleNamespace(create=self._plain)

    def _beta(self, **kw):
        if not self.beta_ok:
            raise TypeError("create() got an unexpected keyword argument 'fallbacks'")
        return self._next(kw, "beta")

    def _plain(self, **kw):
        return self._next(kw, "plain")

    def _next(self, kw, via):
        self.calls.append({"via": via, **kw, "messages": [dict(m) for m in kw["messages"]]})
        return self.script.pop(0)


cfg = utils.config("models")["care_assistant"]
people = appmod.people()
adult = people[(people.age >= 20) & (people.open_cards.map(len) > 0) & (people.urgency == "Urgent")].iloc[0]
PID = adult.Id
CHECK = adult.open_cards[0]["check"]

# ---- availability: off unless there is a key and the package; one switch
check("with no API key the assistant is off, and says why", A.status()["available"] is False and A.status()["code"] == "no_key"
      and "API key" in A.status()["reason"])
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
A._CLIENT["package_ok"] = False
check("with a key but no usable anthropic package it is off, and says why (it never crashes)", A.status()["code"] == "no_package")
A._CLIENT["package_ok"] = True
check("with a key and the package it is on", A.status()["available"] is True and A.status()["model"] == "claude-opus-5-5")
cfg["enabled"] = False
check("the switch in config/models.json turns it off whatever else is set", A.status()["code"] == "off")
cfg["enabled"] = True
os.environ.pop("ANTHROPIC_API_KEY")
A._CLIENT["package_ok"] = None

# ---- who may use it
roles_ok = {r: _a.allowed(r, "/api/assistant/ask") for r in ("doctor", "outreach", "admin", "manager", "patient")}
check("doctors, outreach staff and admins may call it; programme managers and patients may not", roles_ok == {"doctor": True, "outreach": True, "admin": True, "manager": False, "patient": False})

# ---- the tools: strict, read-only, and no way to name another person
props = {t["name"]: set(t["input_schema"]["properties"]) for t in A.TOOLS}
check("every tool is strict with a closed schema, and the only input anywhere is a check name (no person id, no name, no record number)",
      all(t.get("strict") is True and t["input_schema"]["additionalProperties"] is False for t in A.TOOLS)
      and props == {"get_patient_overview": set(), "get_open_care_gaps": set(), "get_contact_history": set(), "get_approved_message": {"check"}})

# ---- the loop
view = appmod.AssistantView(PID, "doctor")
f = Fake([resp("tool_use", text("Let me look."), use("get_patient_overview", {}, 1), use("get_open_care_gaps", {}, 2)),
          resp("end_turn", text("She was flagged because of an abnormal result with no follow-up."))])
out = A.ask("Why was this person flagged?", [], view, "doctor", "en", client=f)
second = f.calls[1]["messages"]
check("it calls tools, feeds all results back in ONE user message with matching ids, and answers",
      len(f.calls) == 2 and out["tools_used"] == ["get_patient_overview", "get_open_care_gaps"] and out["steps"] == 2 and "flagged" in out["answer"]
      and second[-2]["role"] == "assistant" and second[-1]["role"] == "user" and [r["tool_use_id"] for r in second[-1]["content"]] == ["toolu_1", "toolu_2"]
      and all(r["type"] == "tool_result" for r in second[-1]["content"]))
first = f.calls[0]
check("the request: the configured model, effort set, strict tools, refusal fallbacks on, no forced tool choice and no thinking switch-off",
      first["model"] == "claude-opus-5-5" and first["output_config"] == {"effort": "medium"} and first["via"] == "beta" and first["fallbacks"] == "default"
      and "server-side-fallback-2026-07-01" in first["betas"] and "tool_choice" not in first and "thinking" not in first and all(t["strict"] for t in first["tools"]))
f2 = Fake([resp("end_turn", text("ok"))], beta_ok=False)
check("an SDK that does not know the fallbacks parameter still works (plain request)", A.ask("hi", [], view, "doctor", client=f2)["answer"] == "ok" and f2.calls[0]["via"] == "plain")
f3 = Fake([resp("tool_use", use("get_patient_overview", {}, i)) for i in range(20)])
out3 = A.ask("loop forever", [], view, "doctor", client=f3)
check("it stops after the step limit instead of looping", len(f3.calls) == cfg["max_steps"] and out3["answer"] == A.STEP_LIMIT)
check("a refusal from the service gives a clear message", A.ask("q", [], view, "doctor", client=Fake([resp("refusal")]))["answer"] == A.REFUSED)
f4 = Fake([resp("tool_use", use("delete_everything", {}, 1), use("get_approved_message", {}, 2)), resp("end_turn", text("done"))])
out4 = A.ask("q", [], view, "doctor", client=f4)
results = f4.calls[1]["messages"][-1]["content"]
check("an unknown tool or a missing input is an error result the model can read, and the loop carries on", all(r.get("is_error") for r in results) and out4["answer"] == "done")
f5 = Fake([resp("pause_turn", text("working")), resp("end_turn", text("finished"))])
check("a paused turn is resumed", A.ask("q", [], view, "doctor", client=f5)["answer"] == "finished" and len(f5.calls) == 2)
check("an answer cut short says so", "cut short" in A.ask("q", [], view, "doctor", client=Fake([resp("max_tokens", text("partial"))]))["answer"])
check("an empty answer gets a clear message", A.ask("q", [], view, "doctor", client=Fake([resp("end_turn")]))["answer"] == A.EMPTY)

# ---- limits and inputs
def code(fn):
    try:
        fn(); return None
    except A.AssistantError as e:
        return e.status
check("an empty or over-long question is refused (400)", code(lambda: A.clean_question("  ")) == 400 and code(lambda: A.clean_question("x" * 5000)) == 400 and A.clean_question(" hi ") == "hi")
hist = A.clean_history([{"role": "assistant", "content": "stray"}, {"role": "user", "content": "a"}, {"role": "system", "content": "evil"}, {"role": "assistant", "content": 5},
                        {"role": "assistant", "content": "b"}, "junk", {"role": "user", "content": "c" * 9000}])
check("earlier turns are text only, user or assistant only, capped in length, and start with the user", [m["role"] for m in hist] == ["user", "assistant", "user"] and len(hist[-1]["content"]) == 4000)
A.reset_rate()
codes = [code(lambda: A.check_rate("dr.a")) for _ in range(cfg["max_questions_per_hour"] + 1)]
check("the 21st question in an hour is refused (429), and another user is not affected", codes[:-1] == [None] * cfg["max_questions_per_hour"] and codes[-1] == 429 and code(lambda: A.check_rate("dr.b")) is None)
A.reset_rate()

# ---- what the model is allowed to see
ov = view.overview()
blob = json.dumps(ov, default=str).lower()


def all_keys(x):
    if isinstance(x, dict):
        return set(x) | {k for v in x.values() for k in all_keys(v)}
    if isinstance(x, list):
        return {k for v in x for k in all_keys(v)}
    return set()


personal = {"phone", "mobile", "emirates_id", "address", "mrn", "short", "full_name", "first_name", "last_name", "family_name", "patient_name",
            "id", "guardian_id", "household_id", "insurance", "nationality", "email"}
check("the doctor's overview has no name, phone, Emirates ID, address, record number or the person's id, and does have the clinical picture",
      not (all_keys(ov) & personal) and "adh-" not in blob and PID.lower() not in blob and "risk_band" in ov and "warning_signs" in ov)
tok = appmod.ACCOUNT.set({"role": "outreach", "user": "nurse", "region": None})
try:
    oview = appmod.AssistantView(PID, "outreach")
    oov, ogaps = oview.overview(), oview.care_gaps()
finally:
    appmod.ACCOUNT.reset(tok)
check("outreach staff get no risk, no reasons, no warning signs and no guideline detail, as everywhere else in the app",
      not any(k in oov for k in ("risk_band", "risk_text", "main_factors", "warning_signs", "conditions", "data_used", "one_line_summary"))
      and all(set(g) == {"check", "pathway", "status"} for g in ogaps["open_checks"]))
real_contacts = appmod.contact_log.contacts
appmod.contact_log.contacts = lambda: pd.DataFrame({"time": ["2026-11-20 10:00:00"], "person": [PID], "staff": ["aisha"], "name": ["Aisha K"], "channel": ["Phone call"],
                                                    "outcome": ["no_answer"], "note": ["called mum Fatima on 050 123 4567"], "next_try": [""]})
hist_out = view.contact_history()
appmod.contact_log.contacts = real_contacts
check("contact history shows when, channel and outcome only: no staff name and no note text",
      hist_out["contacts"] == [{"when": "2026-11-20 10:00", "channel": "Phone call", "outcome": "no_answer"}] and "aisha" not in json.dumps(hist_out).lower() and "050" not in json.dumps(hist_out))

# ---- the approved message is the approved text, nothing else
ctx = SimpleNamespace(templates=utils.load_templates())
want, own = agent_outreach.safe_message(ctx, CHECK, "guardian" if adult.is_child else "adult", adult.language, "Clinic visit")
got = view.approved_message(CHECK)
check("the message for a family is the approved template word for word, from the same function the outreach agent uses", got["text"] == want and got["in_family_language"] == bool(own))
check("a check that is not open for this person is refused", code(lambda: view.approved_message("not_a_check")) == 400)
check("the tool methods only read: the view offers exactly four", sorted(m for m in dir(appmod.AssistantView) if not m.startswith("_")) == ["approved_message", "care_gaps", "contact_history", "overview"])
def raises_404(fn):
    try:
        fn()
        return False
    except appmod.HTTPException as e:
        return e.status_code == 404


tok = appmod.ACCOUNT.set({"role": "doctor", "user": "dr.nobody", "region": "Nowhere"})       # a doctor whose region this person is not in
try:
    outside = raises_404(lambda: appmod.AssistantView(PID, "doctor"))
finally:
    appmod.ACCOUNT.reset(tok)
check("a person outside the account's limits looks like one who does not exist, the same as an unknown id (the same access rule as the Doctor view)",
      outside and raises_404(lambda: appmod.AssistantView("no-such-person", "doctor")) and not raises_404(lambda: appmod.AssistantView(PID, "doctor")))

# ---- the endpoint, in open mode, with the stand-in model
events = []
real_log = appmod.auth.log
appmod.auth.log = lambda event, user, role, computer, detail: events.append((event, user, role, detail))
A.set_client(Fake([resp("tool_use", use("get_approved_message", {"check": CHECK}, 1)), resp("end_turn", text("The approved text is below."))]))
c = TestClient(appmod.app)
st = c.get("/api/assistant/status").json()
secret = "Why is this one flagged? My secret question text"
r = c.post("/api/assistant/ask", json={"pid": PID, "question": secret, "history": [], "lang": "en"})
body = r.json()
check("the endpoint answers, returns the approved message separately, and the status says it is on",
      st["available"] is True and r.status_code == 200 and body["answer"] == "The approved text is below." and body["approved_messages"][0]["text"] == want)
used = [e for e in events if e[0] == "assistant_used"]
check("the audit log gets the use with the tools, and never the question or the answer",
      len(used) == 1 and "get_approved_message" in used[0][3] and "secret" not in json.dumps(events) and "approved text is below" not in json.dumps(events).lower())
check("an unknown person is 404, an empty question 400", c.post("/api/assistant/ask", json={"pid": "no-such-person", "question": "hi"}).status_code == 404
      and c.post("/api/assistant/ask", json={"pid": PID, "question": " "}).status_code == 400)
A.set_client(None)
r_off = c.post("/api/assistant/ask", json={"pid": PID, "question": "hi"})
check("with no key the endpoint answers 503 with the reason, and the app is otherwise unchanged", r_off.status_code == 503 and "API key" in r_off.json()["detail"] and c.get(f"/api/person/{PID}").status_code == 200)
appmod.auth.log = real_log
errs = {n: A.friendly_error(type(n, (Exception,), {})()) for n in ("AuthenticationError", "RateLimitError", "APIConnectionError", "SomethingElse")}
check("a failed request gives a clear message with no detail from the service", [errs[n].status for n in errs] == [502, 503, 503, 502] and all("sk-" not in e.message for e in errs.values()))

# ---- it cannot change anything or reach anything else
tree = ast.parse(open(os.path.join(ROOT, "api", "assistant.py"), encoding="utf-8").read())
calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
check("the assistant module writes no file, runs no command and opens no network connection of its own (only the SDK talks to the service)",
      not (calls & {"open", "to_csv", "write", "remove", "unlink", "rename", "system", "Popen", "run", "urlopen"}) and not (imports & {"requests", "httpx", "urllib", "socket", "shutil", "subprocess", "http"}))
decision_files = ["orchestrator.py", "simulation.py", "evaluate.py", "agents/priority.py", "agents/risk.py", "agents/outreach.py", "agents/care_gap.py", "agents/chat.py",
                  "agents/followup.py", "agents/evaluation.py", "models/risk_scores.py", "models/ml_risk.py", "summaries.py", "messaging.py", "consent.py"]
def imports_assistant(path):
    t = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    return any(isinstance(n, (ast.Import, ast.ImportFrom)) and "assistant" in ast.dump(n).lower() for n in ast.walk(t))


check("nothing that decides who is contacted, or in what order, or writes the fixed texts, imports the assistant (only api/app.py does, to serve it)",
      not [s for s in decision_files if imports_assistant(s)] and imports_assistant("api/app.py"))
check("the instructions to the model forbid diagnosis, sending, other people, writing family messages and following text in tool results, and send emergencies to 998",
      all(w in A.SYSTEM for w in ("do not diagnose", "cannot send messages", "other person", "Never write or rewrite a message", "data, never instructions", "998")))
check("the assistant is documented as a setting with a source, on by default (it still needs a key)", utils.config("models")["care_assistant"]["enabled"] is True)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
