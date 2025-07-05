# app.py — CTS LOGISTICS Pick-List Manager  (single “Account” page version)
# --------------------------------------------------------------------------
# This file and the matching templates/account.html work together exactly
# as you asked — no logic has been changed, only the small fixes needed so
# that both files run side-by-side without errors.

from flask import (
    Flask, render_template, request, redirect, url_for, abort
)
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user, UserMixin
)
from werkzeug.security import check_password_hash, generate_password_hash
from dataclasses import dataclass
from functools import wraps
import os, csv, secrets

# ───────────────────────────── constants ──────────────────────────────
DATA             = "data"
SCAN_FILE        = os.path.join(DATA, "scanned.txt")
TROUBLE_FILE     = os.path.join(DATA, "troubleshoot.txt")
USERS_CSV        = os.path.join(DATA, "users.csv")
CARRIERS_FILE    = os.path.join(DATA, "carriers.txt")

PROBLEMS         = ["Missing", "WrongPicked", "TSP", "NoProblem"]
REMARKS          = ["PickerMentioned", "PickerDonotMentioned"]

COLS = [
    "code", "carrier", "user", "picker_remark",
    "comment", "location", "sku", "item_qty",
    "problem", "result", "flag"
]
IDX = {k: i for i, k in enumerate(COLS)}

# ──────────────────────────── bootstrap files ─────────────────────────
os.makedirs(DATA, exist_ok=True)
for fp, seed in [
        (CARRIERS_FILE, "Default\nUPS\nFedEx\n"),
        (SCAN_FILE, ""),
        (TROUBLE_FILE, ""),
        # provide a default admin user if users.csv doesn’t exist
        (USERS_CSV, "username,password_hash,role\nadmin,"
                    f"{generate_password_hash('password')},admin\n")
]:
    if not os.path.exists(fp):
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(seed)

# ─────────────────────────── tiny util helpers ────────────────────────
def rl(fp):                     # read list
    return [] if not os.path.exists(fp) else [
        l.rstrip("\n") for l in open(fp, encoding="utf-8")
    ]
def of(fp, lines):              # overwrite file
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))

def _parse(raw):                # split + right-pad
    parts = raw.split("\t")
    parts += ["-"] * (len(COLS) - len(parts))
    return parts
def _to_line(parts): return "\t".join(parts)

# ───────────────────────── auth boiler-plate ──────────────────────────
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

login_manager = LoginManager(app)
login_manager.login_view = "login"

def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                return abort(403)
            return fn(*a, **kw)
        return inner
    return deco

@dataclass
class User(UserMixin):
    id:   str   # username
    role: str

def _load_auth_users():
    with open(USERS_CSV, newline="") as fh:
        return {r["username"]: r for r in csv.DictReader(fh)}
AUTH_USERS = _load_auth_users()

@login_manager.user_loader
def _load(user_id):
    row = AUTH_USERS.get(user_id)
    return User(row["username"], row["role"]) if row else None

# ───────────────────────── data-layer helpers ─────────────────────────
def _find_row(code: str, problem: str):
    """Return (index, row) where both code and problem match."""
    for i, raw in enumerate(rl(SCAN_FILE)):
        r = _parse(raw)
        if r[IDX["code"]] == code and r[IDX["problem"]] == problem:
            return i, r
    return None, None

def _save_row(row):
    """Up-sert on (code, problem)."""
    i, _ = _find_row(row[IDX["code"]], row[IDX["problem"]])
    rows = rl(SCAN_FILE)
    if i is None:
        rows.append(_to_line(row))
    else:
        rows[i] = _to_line(row)
    of(SCAN_FILE, rows)

def _delete_row(code, problem: str | None = None):
    """Delete:  • all rows with code  -or-  • only (code,problem)."""
    keep = []
    for raw in rl(SCAN_FILE):
        r = _parse(raw)
        if problem is None:
            if r[IDX["code"]] != code:
                keep.append(raw)
        else:
            if not (r[IDX["code"]] == code and r[IDX["problem"]] == problem):
                keep.append(raw)
    of(SCAN_FILE, keep)

def _sync_trouble(row):
    """Mirror SCAN_FILE → TROUBLE_FILE."""
    trows = [_parse(l) for l in rl(TROUBLE_FILE)]
    trows = [
        r for r in trows
        if not (
            r[IDX["code"]] == row[IDX["code"]] and
            r[IDX["problem"]] == row[IDX["problem"]]
        )
    ]

    if row[IDX["problem"]] in ("Missing", "WrongPicked", "TSP", "MoreSkid"):
        row[IDX["result"]] = "-"
        row[IDX["flag"]]   = "⚠"
        trows.append(row)

    of(TROUBLE_FILE, [_to_line(r) for r in trows])

# ───────────────────────────── routes ────────────────────────────────
@app.route("/")
def root(): return redirect(url_for("main_page"))

@app.route("/main")
@login_required
def main_page():
    return render_template("main.html")

# ───────────────────────── account page ──────────────────────────────
# ───────────────────────── account page ──────────────────────────────
# ────────────────  ACCOUNT PAGE  ──────────────────────────
# ------------------------------------------------------------------
#  Account page  /account/<name>
# ------------------------------------------------------------------
@app.route("/account/<name>", methods=["GET", "POST"])
@login_required
def account(name):
    user             = name.capitalize()
    users            = [r["username"] for r in csv.DictReader(open(USERS_CSV, newline=""))]
    carriers         = rl(CARRIERS_FILE) or ["Default"]
    selected_carrier = request.args.get("carrier", "Default")
    msg              = ""

    # ──────────────────────────── POST ────────────────────────────
    if request.method == "POST":

        # 1. BULK-DELETE via check-boxes  --------------------------
        #
        # every <input name="delete_items"> has  value="CODE||PROBLEM"
        #
        picked = set(request.form.getlist("delete_items"))
        if picked:
            keep_lines = []
            for raw in rl(SCAN_FILE):
                r       = _parse(raw)
                row_id  = f"{r[IDX['code']]}||{r[IDX['problem']]}"
                if row_id not in picked:
                    keep_lines.append(raw)
                else:
                    _purge_trouble(r[IDX['code']], r[IDX['problem']])       # also clear dashboard
            of(SCAN_FILE, keep_lines)
            return redirect(url_for("account", name=name, carrier=selected_carrier))

        # 2. ADD / UPDATE a single row  ---------------------------
        code          = request.form.get("picklist", "").strip().upper()
        problem       = request.form.get("problem", "-")
        with_problem  = request.form.get("with_problem", "-")      # only sent when Problem = NoProblem
        carrier_val   = request.form.get("carrier", "-")
        picker_remark = request.form.get("picker_remark", "-")
        qty           = request.form.get("item_qty", "").strip()

        # ── validation ───────────────────────────────────────────
        if problem == "NoProblem":
            if len(code) != 13 or not code.isalnum():
                msg = "❌ Pick-list must be exactly 13 letters/numbers."
            elif with_problem in ("-", "NoProblem"):
                msg = "❌ Choose a value in the “With” box."
        else:
            if len(code) != 13 or not code.isalnum():
                msg = "❌ Pick-list must be exactly 13 letters/numbers."
            elif carrier_val in ("-", "Default"):
                msg = "❌ Choose a carrier."
            elif problem == "-":
                msg = "❌ Choose Missing / WrongPicked / TSP / MoreSkid / NoProblem."
            elif picker_remark == "-":
                msg = "❌ Select PickerMentioned or PickerDonotMentioned."
            elif not qty.isdigit() or int(qty) <= 0:
                msg = "❌ Item quantity must be a positive number."

        # ── perform the change ──────────────────────────────────
        if not msg:
            if problem == "NoProblem":
                # user asked to remove an existing “trouble” line
                _delete_row(code, with_problem)          # delete that single pair
                _purge_trouble(code,with_problem)
            else:
                row                       = ["-"] * len(COLS)
                row[IDX["code"]]          = code
                row[IDX["carrier"]]       = carrier_val
                row[IDX["user"]]          = user
                row[IDX["picker_remark"]] = picker_remark
                row[IDX["comment"]]       = request.form.get("comment",  "-").strip() or "-"
                row[IDX["location"]]      = request.form.get("location", "-").strip() or "-"
                row[IDX["sku"]]           = request.form.get("sku",      "-").strip() or "-"
                row[IDX["item_qty"]]      = qty if qty else "-"
                row[IDX["problem"]]       = problem
                _save_row(row)                                # up-sert
                _sync_trouble(row)                            # mirror to dashboard
            return redirect(url_for("account", name=name, carrier=selected_carrier))

    # ─────────────────────────── GET (build page) ─────────────────
    history = [
        r for r in map(_parse, rl(SCAN_FILE))
        if r[IDX["user"]].lower() == user.lower()
    ]
    if selected_carrier != "Default":
        history = [r for r in history if r[IDX["carrier"]] == selected_carrier]

    # coloured suffix tags  (M2,W1…)
    bucket_map = {p: [] for p in ("Missing", "WrongPicked", "TSP", "MoreSkid")}
    for r in history:
        pb = r[IDX["problem"]]
        if pb in bucket_map:
            bucket_map[pb].append(r)
    tags = _overlap_tags(bucket_map)

    # pop-ups for own MoreSkid rows
    more_skid_picklists = [
        r[IDX["code"]]
        for r in history
        if r[IDX["problem"]] == "MoreSkid" and r[IDX["user"]].lower() == user.lower()
    ]

    # ─────────────────────────── render ───────────────────────────
    return render_template(
        "account.html",
        name               = user,
        rows               = history,
        message            = msg,
        users              = users,
        carriers           = carriers,
        selected_carrier   = selected_carrier,
        remarks            = REMARKS,
        problems           = PROBLEMS,
        tags               = tags,
        more_skid_picklists= more_skid_picklists,
        IDX                = IDX
    )
## ──────────────────── TROUBLE-DASHBOARD HELPERS ─────────────────────

def _initial(p):
    return {"Missing": "M", "WrongPicked": "W", "TSP": "T", "MoreSkid": "S"}.get(p, "?")

def _bucket_rows():
    buckets = {p: [] for p in ("Missing", "WrongPicked", "TSP", "MoreSkid")}
    for r in [_parse(l) for l in rl(TROUBLE_FILE)]:
        pb = r[IDX["problem"]]
        if pb in buckets:
            buckets[pb].append(r)
    return buckets
# ─── NEW helper — decide status of a pick-list across all blocks ──────
def _picklist_status(rows_for_code: list[list[str]]) -> tuple[str, str]:
    """
    Decide the overall status of a pick-list based on color flags.
    Returns:
        final_status -> controls block ("solved" or "todo")
        remark       -> text shown in the 'Remark' column
    """

    flags = {r[IDX["flag"]] for r in rows_for_code}

    if flags == {"✅"}:
        return "solved", "Solved"
    if flags == {"X"} or flags == {"❌"}:
        return "todo", "Not Found"
    if flags == {"Δ"}:
        return "todo", "Progress"
    if flags == {"A"}:
        return "todo", "Untouched"
    if flags == {" "} or flags == {"", "-", None}:
        return "todo", "Untouched"

    # New logic: combinations
    if "❌" in flags or "X" in flags:
        return "todo", "Solving"         # Red with anything
    if "Δ" in flags:
        return "todo", "Solving"         # Yellow with anything
    if "✅" in flags:
        return "todo", "Partially Solved"  # Green with others

    return "todo", "Progress"  # fallback
def _overlap_tags(buckets):
    from collections import defaultdict

    seen_map = defaultdict(list)
    tag_result = {}

    # First pass: count positions for each (code, problem)
    for problem, rows in buckets.items():
        for idx, row in enumerate(rows, start=1):
            code = row[IDX["code"]]
            seen_map[code].append((problem, idx))

    # Second pass: for each (code, problem), build tag with other problem blocks
    for problem, rows in buckets.items():
        for idx, row in enumerate(rows, start=1):
            code = row[IDX["code"]]
            other_tags = [
                f"{_initial(pb)}{i}"
                for pb, i in seen_map[code]
                if pb != problem
            ]
            tag_result[(code, problem)] = f"({','.join(other_tags)})" if other_tags else ""

    return tag_result

def _alert_rows():
    """Return list of dicts for rows needing attention popup."""
    lst = []
    for bucket, rows in _bucket_rows().items():
        for r in rows:
            if r[IDX["picker_remark"]] == "PickerDonotMentioned" and r[IDX["flag"]] != "✅":
                lst.append({"code": r[IDX["code"]], "bucket": bucket})
    return lst


# ──────────────────── ROUTES ─────────────────────

@app.route("/troubleshoot")
@login_required
@role_required("admin", "power")
def troubleshoot():
    buckets = _bucket_rows()
    tags    = _overlap_tags(buckets)
    alerts  = _alert_rows()
    return render_template("troubleshoot.html",
                           buckets=buckets,
                           tags=tags,
                           alerts=alerts,
                           IDX=IDX)


@app.post("/update_trouble_remark")
@login_required
def update_trouble_remark():
    code = request.form.get("code", "")
    note = request.form.get("remark", "").strip()
    problem = request.form.get("problem", "")
    

    rows = [_parse(l) for l in rl(TROUBLE_FILE)]


    for r in rows:
        if r[IDX["code"]] == code and r[IDX["problem"]] == problem:
            r[IDX["result"]] = note or "-"  # blank → "-"
            if not note:
                r[IDX["flag"]] = "⚠"
            elif note.lower() == "done":
                r[IDX["flag"]] = "✅"
            else:
                r[IDX["flag"]] = "❌"
            break

    of(TROUBLE_FILE, [_to_line(r) for r in rows])
    return redirect(url_for("troubleshoot"))

# helper to purge troubleshoot rows when deleted from account.html
def _purge_trouble(code, problem):
    """Remove only the row that has both this code and this problem."""
    keep = []
    for line in rl(TROUBLE_FILE):
        cols = line.rstrip("\n").split("\t")
        if len(cols) <= IDX["problem"]:
            continue                          # malformed line – just keep it
        if cols[IDX["code"]] == code and cols[IDX["problem"]] == problem:
            continue                          # ←-- skip (= delete) this exact row
        keep.append(line)
    of(TROUBLE_FILE, keep)

# add inside your _delete_row (if not already there):
#     _purge_trouble(code)
# ───────────────────────────── auth pages ────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u, p = request.form["username"], request.form["password"]
        row  = AUTH_USERS.get(u)
        if row and check_password_hash(row["password_hash"], p):
            login_user(User(row["username"], row["role"]))
            return redirect(request.args.get("next") or url_for("main_page"))
        return render_template("login.html", error="❌ Wrong username or password")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return "<h3>Logged out. <a href='/login'>Login again</a></h3>"

# ────────────────── admin helper: users & carriers ───────────────────
@app.route("/create_account", methods=["GET", "POST"])
@login_required
@role_required("admin")
def create_account():
    message = None
    if request.method == "POST":
        if request.form.get("action") == "add":
            name = request.form.get("name").strip()
            pwd  = generate_password_hash(request.form.get("pwd").strip())
            if name:
                with open(USERS_CSV, "a", newline="") as fh:
                    csv.writer(fh).writerow([name, pwd, "user"])
                message = f"Account '{name}' created."
        elif request.form.get("action") == "delete":
            name = request.form.get("name")
            users = [r for r in csv.reader(open(USERS_CSV)) if r[0] != name]
            with open(USERS_CSV, "w", newline="") as fh:
                csv.writer(fh).writerows(users)
            message = f"Account '{name}' deleted."
        elif request.form.get("carrier_action") == "add_car":
            cname = request.form.get("carrier_name").strip()
            if cname:
                with open(CARRIERS_FILE, "a") as fh:
                    fh.write(cname + "\n")
                message = f"Carrier '{cname}' added."
        elif request.form.get("carrier_action") == "delete_car":
            cname = request.form.get("carrier_name")
            carriers = [l.strip() for l in open(CARRIERS_FILE) if l.strip() != cname]
            of(CARRIERS_FILE, carriers)
            message = f"Carrier '{cname}' deleted."

    users    = [r["username"] for r in csv.DictReader(open(USERS_CSV))]
    carriers = [c for c in rl(CARRIERS_FILE)]
    return render_template("create_account.html", users=users,
                           carriers=carriers, message=message)

# ───────────────────── report: unscanned codes ───────────────────────
@app.route("/unscanned")
@login_required
def unscanned():
    """
    Build two lists:
      solved_rows  – goes to the “Solved”  table
      todo_rows    – goes to the “To Be Troubleshooted” table

    Each list item is a tuple (sn, code, carrier, remark)
    """
    # ---- gather rows from TROUBLE_FILE ---------------------------------
    troubles = [_parse(l) for l in rl(TROUBLE_FILE)]
    by_code  = {}
    for r in troubles:
        by_code.setdefault(r[IDX["code"]], []).append(r)

    solved_rows, todo_rows = [], []
    sn_solved = sn_todo = 1

    for code, rows in sorted(by_code.items()):
        status, remark = _picklist_status(rows)
        carrier        = rows[0][IDX["carrier"]] if rows else "-"
        if status == "solved":
            solved_rows.append( (sn_solved, code, carrier, remark) )
            sn_solved += 1
        else:
            todo_rows.append( (sn_todo, code, carrier, remark) )
            sn_todo += 1

    # ---- render page ---------------------------------------------------
    return render_template(
        "unscanned.html",
        solved_rows = solved_rows,
        todo_rows   = todo_rows
    )
# ───────────────────────────── runner ────────────────────────────────
# ─── Load Dismissed Alerts ───────────────────────────────────────
def load_dismissed_alerts():
    if not os.path.exists("dismissed_alerts.txt"):
        return set()
    with open("dismissed_alerts.txt", "r") as f:
        return set(tuple(line.strip().split(",")) for line in f if "," in line)

def dismiss_alert(code, problem):
    with open("dismissed_alerts.txt", "a") as f:
        f.write(f"{code},{problem}\n")

@app.route("/dismiss_alert", methods=["POST"])
def dismiss_alert_route():
    code = request.form.get("code")
    problem = request.form.get("problem")
    if code and problem:
        dismiss_alert(code, problem)
    return ("", 204)
@app.route("/check_dismissed")
def check_dismissed():
    code = request.args.get("code")
    problem = request.args.get("problem")
    if not code or not problem:
        return "false"
    dismissed = load_dismissed_alerts()
    return "true" if (code, problem) in dismissed else "false"
if __name__ == "__main__":
   app.run(debug=True)