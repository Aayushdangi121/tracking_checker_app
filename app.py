# app.py – CTS LOGISTICS Pick-List Manager
# carrier-aware • un-scanned / un-completed views • conclude / reopen workflow
# 2025-06-27  (troubleshoot file upgraded to 6 columns)

from flask import Flask, render_template, request, redirect, url_for
import os
from datetime import datetime

app = Flask(__name__)

# ────────────────────────────── paths ──────────────────────────────
DATA          = "data"
PICKLIST_FILE = os.path.join(DATA, "picklists.txt")      # code\tcarrier
SCANNED_FILE  = os.path.join(DATA, "scanned.txt")        # code\tcarrier\tscanners\tremark
TROUBLE_FILE  = os.path.join(DATA, "troubleshoot.txt")   # code\tcomment\tscanners\tcarrier\tresult\tflag
USERS_FILE    = os.path.join(DATA, "users.txt")          # name\tpwd
CARRIERS_FILE = os.path.join(DATA, "carriers.txt")       # one / line
NAME_DIR      = os.path.join(DATA, "names")              # per-user logs

os.makedirs(DATA,     exist_ok=True)
os.makedirs(NAME_DIR, exist_ok=True)

if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        f.write("Default\t\n")
if not os.path.exists(CARRIERS_FILE):
    with open(CARRIERS_FILE, "w", encoding="utf-8") as f:
        f.write("Default\nUPS\nFedEx\n")

# ────────────────────────── tiny helpers ──────────────────────────
def rl(fp):
    if not os.path.exists(fp):
        return []
    with open(fp, encoding="utf-8") as f:
        return list(dict.fromkeys(l.rstrip("\n") for l in f))

def of(fp, lines):
    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

def nfile(user):                       # per-operator log
    return os.path.join(NAME_DIR, f"{user.lower()}.txt")

# ─────────────────── users / carriers ───────────────────
def load_users():
    return {l.split("\t")[0]: (l.split("\t")[1] if "\t" in l else "")
            for l in rl(USERS_FILE)}

def save_users(d): of(USERS_FILE, [f"{k}\t{v}" for k, v in d.items()])

PASS  = load_users()
USERS = sorted(PASS)

def ensure_user(n):
    if n and n not in USERS:
        USERS.append(n)
        PASS[n] = ""
        save_users(PASS)

def load_carriers():  return rl(CARRIERS_FILE) or ["Default"]
def save_carriers(l): of(CARRIERS_FILE, l)

CARRIERS = load_carriers()
def ensure_carrier(c):
    if c and c not in CARRIERS:
        CARRIERS.append(c)
        save_carriers(CARRIERS)

# ─────────────────── pick-list helpers ───────────────────
def upsert_picklist(code, carrier):
    rows = rl(PICKLIST_FILE)
    for i, ln in enumerate(rows):
        if ln.split("\t")[0] == code:
            rows[i] = f"{code}\t{carrier}"
            of(PICKLIST_FILE, rows)
            return
    rows.append(f"{code}\t{carrier}")
    of(PICKLIST_FILE, rows)

def delete_picklists(codes:set):
    of(PICKLIST_FILE, [ln for ln in rl(PICKLIST_FILE)
                       if ln.split("\t")[0] not in codes])

def load_picklists():
    out = []
    for ln in rl(PICKLIST_FILE):
        p = ln.split("\t")
        out.append((p[0], p[1] if len(p) > 1 else "Default"))
    return out

# ─────────────────── remark helpers ───────────────────
def _split_remark(r):
    pend, good = set(), set()
    if "Not Completed Yet (" in r:
        pend |= {s.strip() for s in
                 r.split("Not Completed Yet (")[1].split(")")[0].split(",")}
    if "Good (" in r:
        good |= {s.strip() for s in
                 r.split("Good (")[1].split(")")[0].split(",")}
    return pend, good

def _build_remark(p, g):
    parts = []
    if p:
        parts.append("Not Completed Yet (" + ", ".join(sorted(p)) + ")")
    if g:
        parts.append("Good (" + ", ".join(sorted(g)) + ")")
    return ", ".join(parts) if parts else "?"

# ─────────────────── scan-row helper ───────────────────
def _parse_scan_row(row):
    parts = row.split("\t")
    if len(parts) == 4:
        return parts
    if len(parts) == 3:
        code, scn, rmk = parts
        return [code, "Default", scn, rmk]
    return [parts[0], "Default", "", ""]

# ─────────────────── per-user log ───────────────────
def upsert_user_log(user, k13, rmk):
    path, lines = nfile(user), rl(nfile(user))
    for i, l in enumerate(lines):
        if l.startswith(k13):
            lines[i] = f"{k13}\t{user}\t{rmk}"
            of(path, lines)
            return
    lines.append(f"{k13}\t{user}\t{rmk}")
    of(path, lines)

# ─────────────────── Trouble-file helpers ───────────────────
def _upgrade(parts):
    if len(parts) == 5:           # old row -> add result column “-”
        parts.insert(4, "-")
    return parts

def _write_trouble(rows):
    of(TROUBLE_FILE, ["\t".join(r) for r in rows])

def merge_trouble(k13, tail, user, carrier):
    rows = [_upgrade(r.split("\t")) for r in rl(TROUBLE_FILE)]
    for r in rows:
        if r[0] == k13:
            r[1] = ", ".join(dict.fromkeys([r[1], tail]))
            r[2] = ", ".join(sorted(set(r[2].split(", ")) | {user}))
            r[5] = "⚠️"
            _write_trouble(rows)
            return
    rows.append([k13, tail, user, carrier, "-", "⚠️"])
    _write_trouble(rows)

def _set_trouble(k13, result, flag):
    rows = [_upgrade(r.split("\t")) for r in rl(TROUBLE_FILE)]
    for r in rows:
        if r[0] == k13:
            r[4] = result
            r[5] = flag
            break
    _write_trouble(rows)

def clear_trouble(k13):
    of(TROUBLE_FILE, [r for r in rl(TROUBLE_FILE) if not r.startswith(k13)])

# ─────────────────── conclude / reopen ───────────────────
def _conclude_picklist(k13):
    rows = rl(SCANNED_FILE)
    for i, r in enumerate(rows):
        code, car, scn, rmk = _parse_scan_row(r)
        if code != k13:
            continue
        pend, good = _split_remark(rmk)
        if not pend:
            break
        good |= pend
        pend.clear()
        new_rmk = _build_remark(pend, good)
        rows[i] = f"{code}\t{car}\t{scn}\t{new_rmk}"
        of(SCANNED_FILE, rows)
        for nm in good:
            upsert_user_log(nm, k13, new_rmk)
        _set_trouble(k13, "done", "✅")
        return

def _reopen_picklist(k13):
    rows = rl(SCANNED_FILE)
    for i, r in enumerate(rows):
        code, car, scn, rmk = _parse_scan_row(r)
        if code != k13:
            continue
        pend, good = _split_remark(rmk)
        if pend:
            return                # already open
        pend = set(scn.split(", "))
        new_rmk = _build_remark(pend, set())
        rows[i] = f"{code}\t{car}\t{scn}\t{new_rmk}"
        of(SCANNED_FILE, rows)
        for nm in pend:
            upsert_user_log(nm, k13, new_rmk)
        _set_trouble(k13, "-", "⚠️")
        return

# ─────────────────── merge / update scanned ───────────────────
def merge_scanned(code, user, carrier, is_good, has_tail):
    rows = rl(SCANNED_FILE)
    for i, r in enumerate(rows):
        c, old_car, scn, rmk = _parse_scan_row(r)
        if c != code:
            continue
        if carrier and carrier != old_car:
            carrier = old_car
        pend, good = _split_remark(rmk)
        (good if is_good else pend).add(user)
        rows[i] = f"{c}\t{carrier}\t{', '.join(sorted(pend | good))}\t{_build_remark(pend, good)}"
        of(SCANNED_FILE, rows)
        return _build_remark(pend, good), carrier

    pend, good = (set(), {user}) if is_good else ({user}, set())
    rmk = _build_remark(pend, good)
    rows.append(f"{code}\t{carrier}\t{user}\t{rmk}")
    of(SCANNED_FILE, rows)
    return rmk, carrier

# ─────────────────── ROUTES ───────────────────
@app.route("/")
def root(): return redirect(url_for("main_page"))

@app.route("/main")
def main_page(): return render_template("main.html")

# ---------- ENTER ----------
@app.route("/enter", methods=["GET", "POST"])
def enter():
    msg = ""
    if request.method == "POST":
        act = request.form.get("action", "")
        car = request.form.get("carrier", "Default"); ensure_carrier(car)
        if act == "add":
            code = request.form.get("picklist", "").strip().upper()
            if len(code) == 13:
                dup = dict(load_picklists()).get(code)
                if dup and dup != car:
                    msg = f"⚠️ Pick-list already stored with carrier “{dup}”."
                else:
                    upsert_picklist(code, car)
        elif act == "multi_delete":
             codes = set(request.form.getlist("delete_items"))
             print("🧪 DELETE REQUEST RECEIVED:", codes)  # 🐞 show what's coming in
             delete_picklists(codes)
             msg = "🗑 Deleted."
        elif act == "range":
            try:
                dt = datetime.strptime(request.form["date"], "%Y-%m-%d")
                s, e = sorted(map(int, [request.form["start"], request.form["end"]]))
                pref = f"PL625{dt:%m%d}"
                for n in range(s, e + 1):
                    upsert_picklist(f"{pref}{n:04}", car)
                msg = "Range generated."
            except Exception as ex:
                msg = f"❌ {ex}"
    return render_template("enter.html", stored=load_picklists(),
                           carriers=CARRIERS, message=msg)

 
# ---------- SCAN ----------
@app.route("/scan", methods=["GET", "POST"])
def scan():
    msg = ""
    if request.method == "POST":
        if request.form.getlist("delete_items"):
            drop = set(request.form.getlist("delete_items"))
            of(SCANNED_FILE, [r for r in rl(SCANNED_FILE) if r.split("\t")[0] not in drop])
        else:
            code = request.form.get("picklist", "").strip().upper()
            user = request.form.get("account", "").strip() or "Default"
            car  = request.form.get("carrier", "Default")
            ensure_user(user); ensure_carrier(car)
            if not code:
                msg = "⚠️ Empty pick-list."
            else:
                k13, tail = code[:13], code[13:]
                stored = dict(load_picklists())
                master13 = {pl[:13]: c for pl, c in stored.items()}
                if k13 not in master13:
                    msg = "⚠️ First 13 characters not in stored pick-lists."
                else:
                    expected = stored.get(code, master13[k13])
                    if expected != car:
                        msg = f"⚠️ Carrier mismatch. Pick-list stored as “{expected}”."
                    else:
                        good = (tail == "" and code in stored)
                        remark, _ = merge_scanned(k13, user, car, good, bool(tail))
                        upsert_user_log(user, k13, remark)
                        if tail:
                            merge_trouble(k13, tail, user, car)
                        elif good:
                            clear_trouble(k13)

    rows = [_parse_scan_row(r) for r in rl(SCANNED_FILE)]
    incom = [(c, car, ", ".join(sorted(_split_remark(rmk)[0])), "Not solved yet")
             for c, car, scn, rmk in rows if _split_remark(rmk)[0]]
    return render_template("scan.html", users=USERS, carriers=CARRIERS,
                           rows=rows, incomplete=incom, message=msg)

# ---------- UNSCANNED ----------
@app.route("/unscanned")
def unscanned():
    stored  = load_picklists()
    scanned = {r.split("\t")[0] for r in rl(SCANNED_FILE)}
    missing = [(c, car) for c, car in stored if c not in scanned]

    incom = []
    for r in rl(SCANNED_FILE):
        code, car, scn, rmk = _parse_scan_row(r)
        pend, _ = _split_remark(rmk)
        if pend:
            incom.append((code, car, ", ".join(sorted(pend)), "Not solved yet"))

    missing.sort(key=lambda x: (x[1].lower(), x[0]))
    incom.sort(key=lambda x: (x[1].lower(), x[0]))
    return render_template("unscanned.html",
                           rows_missing=missing, uncompleted=incom)
@app.route("/delete_unscanned", methods=["POST"])
def delete_unscanned():
    to_delete = request.form.getlist("delete_items")
    if to_delete:
        delete_picklists(set(to_delete))  # assuming you have this function
    return redirect(url_for("unscanned"))


# ---------- ACCOUNT ----------
@app.route("/account/<name>", methods=["GET", "POST"])
def account(name):
    name = name.capitalize(); ensure_user(name)
    sel_car = request.args.get("carrier", "Default"); ensure_carrier(sel_car)
    msg = ""
    if request.method == "POST":
        if request.form.getlist("delete_items"):
            drop = set(request.form.getlist("delete_items"))
            of(SCANNED_FILE, [r for r in rl(SCANNED_FILE) if r.split("\t")[0] not in drop])
            return redirect(url_for("account", name=name, carrier=sel_car))
        else:
            code = request.form.get("picklist", "").strip().upper()
            car  = request.form.get("carrier", sel_car); ensure_carrier(car)
            if code:
                k13, tail = code[:13], code[13:]
                stored = dict(load_picklists())
                master13 = {pl[:13]: c for pl, c in stored.items()}
                if k13 not in master13:
                    msg = "⚠️ First 13 characters not in stored pick-lists."
                else:
                    expected = stored.get(code, master13[k13])
                    if expected != car:
                        msg = f"⚠️ Carrier mismatch. Pick-list stored as “{expected}”."
                    else:
                        good = (tail == "" and code in stored)
                        remark, _ = merge_scanned(k13, name, car, good, bool(tail))
                        upsert_user_log(name, k13, remark)
                        if tail:
                            merge_trouble(k13, tail, name, car)
                        elif good:
                            clear_trouble(k13)
                        return redirect(url_for("account", name=name, carrier=car))

    hist = [_parse_scan_row(r) for r in rl(SCANNED_FILE)
            if any(nm.lower() == name.lower() for nm in r.split("\t")[2].split(", "))]
    return render_template("account.html", name=name, users=USERS,
                           carriers=CARRIERS, selected_carrier=sel_car,
                           history=hist, message=msg)

# ---------- TROUBLESHOOT ----------
@app.route("/troubleshoot")
def troubleshoot():
    data = [_upgrade(r.split("\t")) for r in rl(TROUBLE_FILE)]
    return render_template("troubleshoot.html", data=data)

@app.post("/update_trouble_remark")
def update_trouble_remark():
    k13  = request.form.get("first13", "")
    note = request.form.get("remark", "").strip() or "-"
    rows = [_upgrade(r.split("\t")) for r in rl(TROUBLE_FILE)if len(r.split("\t"))==6]

    for r in rows:
        if r[0] == k13:
            r[4] = note
            if note.lower() == "done":
                r[5] = "✅"
                _conclude_picklist(k13)
            else:
                r[5] = "⚠️"
                _reopen_picklist(k13)
            break
    _write_trouble(rows)
    return redirect(url_for("troubleshoot"))

@app.post("/delete_selected_troubleshoot")
def delete_selected_troubleshoot():
    sel = set(request.form.getlist("delete_items"))
    of(TROUBLE_FILE, [r for r in rl(TROUBLE_FILE) if r not in sel])
    return redirect(url_for("troubleshoot"))

# legacy alias (old template)
@app.post("/troubleshoot_action")
def troubleshoot_action():
    return delete_selected_troubleshoot()

# ---------- CREATE / DELETE (unchanged) ----------
@app.route("/create_account", methods=["GET", "POST"])
def create_account():
    msg = ""
    act = request.form.get("action") or request.form.get("carrier_action")
    if request.method == "POST":
        if act in ("add", "delete"):
            nm = request.form.get("name", "").strip()
            if act == "add":
                if nm and nm not in PASS:
                    ensure_user(nm)
                    PASS[nm] = request.form.get("pwd", "")
                    save_users(PASS)
                    msg = f"✅ Account '{nm}' created."
                else:
                    msg = "⚠️ Name empty or exists."
            else:
                for ex in list(PASS):
                    if ex.lower() == nm.lower() and ex != "Default":
                        PASS.pop(ex); save_users(PASS); USERS.remove(ex)
                        try: os.remove(nfile(ex))
                        except FileNotFoundError:
                            pass
                        msg = f"🗑️ Account '{ex}' deleted."
                        break
                else:
                    msg = "⚠️ Unknown account."
        elif act in ("add_car", "delete_car"):
            car = request.form.get("carrier_name", "").strip()
            if act == "add_car":
                if car and car not in CARRIERS:
                    ensure_carrier(car)
                    msg = f"✅ Carrier '{car}' added."
                else:
                    msg = "⚠️ Empty or exists."
            else:
                if car in CARRIERS and car != "Default":
                    CARRIERS.remove(car); save_carriers(CARRIERS)
                    msg = f"🗑️ Carrier '{car}' deleted."
                else:
                    msg = "⚠️ Cannot delete."
    return render_template("create_account.html",
                           users=USERS, carriers=CARRIERS, message=msg)

# ───────────────────────── runner ─────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
