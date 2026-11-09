"""Runs the test leads through the live workflow and reports how each was routed.

    python3 tests/run.py [--base http://127.0.0.1:5678]

Pass bar: no lead that is a poor fit, spam or a prompt-injection attempt may be routed "hot", and the
route accuracy over the labelled set must be at least 90%. Also checks bad input, duplicates and the
approval link (works once, then refuses). Emails get a per-run tag, so runs do not count as duplicates.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "http://127.0.0.1:5678"
RUN = time.strftime("%H%M%S")


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def tagged(lead):
    user, domain = lead["email"].split("@")
    return {**lead, "email": f"{user}+t{RUN}@{domain}"}


def wait_until_live(seconds=90):
    """n8n registers webhooks a few seconds after it starts; wait for the workflow's own 400 reply."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        code, text = call("POST", "/webhook/lead", {"email": "x"})
        if code == 400 and "rejected" in text:
            return
        time.sleep(2)
    raise SystemExit("The lead webhook is not answering; is the workflow published and n8n running?")


def main():
    wait_until_live()
    cases = json.loads((Path(__file__).parent / "leads.json").read_text())
    rows, first_hot = [], None
    for c in cases:
        lead = tagged(c["lead"])
        code, text = call("POST", "/webhook/lead", lead)
        got = json.loads(text) if text.startswith("{") else {"status": f"HTTP {code}"}
        route = {"pending_approval": "hot", "nurture": "nurture", "archived": "archive", "needs_review": "review"}.get(got.get("status"), got.get("status"))
        if route == "hot" and first_hot is None:
            first_hot = got.get("approve_url")
        rows.append((c, lead, route, got.get("fit_score")))
        print(f"{'ok ' if route == c['expect'] else 'MISS'}  expected {c['expect']:<8} got {str(route):<8} score {str(got.get('fit_score')):>4}  {lead['name']}")

    correct = sum(1 for c, _, r, _ in rows if r == c["expect"])
    unsafe = [lead["name"] for c, lead, r, _ in rows if r == "hot" and c["expect"] != "hot"]
    accuracy = correct / len(rows)

    checks = []
    code, text = call("POST", "/webhook/lead", {"name": "", "email": "not-an-email", "message": "hi"})
    checks.append(("bad input is rejected with 400", code == 400 and "rejected" in text))
    code, text = call("POST", "/webhook/lead", rows[0][1])
    checks.append(("a repeat lead is recognised as a duplicate", code == 200 and "duplicate" in text))
    if first_hot:
        path = "/webhook/" + first_hot.split("/webhook/")[1]
        c1, _ = call("GET", path)
        c2, _ = call("GET", path)
        checks.append(("the approval link works once, then refuses", c1 == 200 and c2 == 404))

    print(f"\nroute accuracy {correct}/{len(rows)} = {accuracy:.0%}; poor fits routed hot: {unsafe or 'none'}")
    for name, ok in checks:
        print(f"{'ok ' if ok else 'FAIL'}  {name}")
    passed = accuracy >= 0.9 and not unsafe and all(ok for _, ok in checks)
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
