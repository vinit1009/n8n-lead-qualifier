"""Writes the three n8n workflow files in workflows/ (run after editing a prompt or a code step).

    python3 tools/build_workflows.py

Keeping the workflows in a generator makes the code steps and the prompt readable in review; the JSON
files it writes are what you import into n8n.
"""
import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG = {"postgres": {"id": "leadsdbcred00001", "name": "Leads DB"}}
AI = {"httpHeaderAuth": {"id": "openroutercred01", "name": "OpenRouter"}}
IDS = {"intake": "leadintake000001", "approve": "approvelink00001", "errors": "errorlog00000001"}


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "n8n-lead-qualifier/" + name))


def node(name, type_, version, pos, params, **extra):
    n = {"parameters": params, "id": uid(name), "name": name, "type": type_, "typeVersion": version, "position": list(pos)}
    n.update(extra)
    return n


def code(name, pos, js, each=True):
    params = {"jsCode": js.strip()}
    if each:
        params["mode"] = "runOnceForEachItem"
    return node(name, "n8n-nodes-base.code", 2, pos, params)


def sql(name, pos, query, values=None, always=False):
    params = {"operation": "executeQuery", "query": query.strip(), "options": {}}
    if values:
        params["options"]["queryReplacement"] = values
    extra = {"credentials": PG}
    if always:
        extra["alwaysOutputData"] = True
    return node(name, "n8n-nodes-base.postgres", 2.8, pos, params, **extra)


def route(name, pos, n, expr):
    return node(name, "n8n-nodes-base.switch", 3.5, pos, {"mode": "expression", "numberOutputs": n, "output": expr})


def reply(name, pos, body, status=200, html=False):
    if html:
        params = {"respondWith": "text", "responseBody": body, "options": {"responseCode": status, "responseHeaders": {"entries": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}]}}}
    else:
        params = {"respondWith": "json", "responseBody": body, "options": {"responseCode": status}}
    return node(name, "n8n-nodes-base.respondToWebhook", 1.5, pos, params)


def wire(*pairs):
    """pairs of (from, to) or (from, to, output_index) -> n8n connections."""
    out = {}
    for p in pairs:
        src, dst, idx = (p + (0,))[:3]
        main = out.setdefault(src, {"main": []})["main"]
        while len(main) <= idx:
            main.append([])
        main[idx].append({"node": dst, "type": "main", "index": 0})
    return out


PROMPT_JS = r"""
// Build the request to Claude (through OpenRouter's OpenAI-compatible API) with a forced tool call,
// so the answer always comes back as structured fields.
const s = $('Settings').item.json;
const lead = $('Validate and normalise').item.json;  // $json here is the duplicate check's row
const system = [
  `You qualify inbound leads for ${s.business}.`,
  `Ideal customer: ${s.ideal_customer}.`,
  'Not a fit: students, job seekers, people selling to us, and requests with no business problem.',
  'Spam: link drops, SEO or crypto offers, gibberish.',
  'Score the fit from 0 to 100 using only what the lead wrote; never invent facts about them.',
  'Then draft a short first reply (under 120 words) that answers their message specifically, asks one useful question,',
  `makes no promise about price or dates, and is signed "${s.signature}". Use no placeholders such as [Name].`,
  'Call the assess_lead tool with your answer.',
].join('\n');
const user = `Lead\nName: ${lead.name}\nCompany: ${lead.company || '(not given)'}\nEmail: ${lead.email}\nMessage: ${lead.message}`;
return { json: { payload: {
  model: s.model, temperature: 0, max_tokens: 800,
  messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
  tools: [TOOL],
  tool_choice: { type: 'function', function: { name: 'assess_lead' } },
} } };
"""

TOOL = {
    "type": "function",
    "function": {
        "name": "assess_lead",
        "description": "Record the qualification of one inbound lead and a draft first reply.",
        "parameters": {
            "type": "object",
            "properties": {
                "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "segment": {"type": "string", "enum": ["ideal_customer", "maybe", "not_a_fit", "spam"]},
                "reasons": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
                "email_subject": {"type": "string"},
                "email_body": {"type": "string"},
            },
            "required": ["fit_score", "segment", "reasons", "email_subject", "email_body"],
        },
    },
}

VALIDATE_JS = r"""
// Clean the form fields and refuse anything the model should never see.
const b = $json.body || {};
const clean = (v, n) => String(v ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
const lead = {
  email: clean(b.email, 254).toLowerCase(),
  name: clean(b.name, 120),
  company: clean(b.company, 120),
  message: clean(b.message, 2000),
  source: clean(b.source, 60) || 'webhook',
};
const problems = [];
if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(lead.email)) problems.push('email is missing or invalid');
if (!lead.name) problems.push('name is required');
if (lead.message.length < 10) problems.push('message is too short to qualify');
return { json: { ...lead, valid: problems.length === 0, problems } };
"""

CHECK_JS = r"""
// Never trust the model's output as-is: parse it, check every field, and decide the route in code.
const s = $('Settings').item.json;
const lead = $('Validate and normalise').item.json;
const problems = [];
let a = {};
try {
  const call = $json.choices[0].message.tool_calls[0];
  a = JSON.parse(call.function.arguments);
} catch (e) {
  problems.push('the model did not return a tool call');
}
const score = Number.isInteger(a.fit_score) && a.fit_score >= 0 && a.fit_score <= 100 ? a.fit_score : null;
if (score === null) problems.push('fit_score must be a whole number from 0 to 100');
const segments = ['ideal_customer', 'maybe', 'not_a_fit', 'spam'];
if (!segments.includes(a.segment)) problems.push('segment is not one of ' + segments.join(', '));
const reasons = Array.isArray(a.reasons) ? a.reasons.map(r => String(r).slice(0, 200)).slice(0, 3) : [];
if (!reasons.length) problems.push('no reasons given');
const subject = String(a.email_subject || '').trim();
const body = String(a.email_body || '').trim();
if (subject.length < 3 || subject.length > 90) problems.push('subject must be 3 to 90 characters');
if (body.length < 40 || body.length > 1500) problems.push('reply must be 40 to 1,500 characters');
if (/\[[^\]]{1,30}\]|\{\{|\}\}/.test(subject + ' ' + body)) problems.push('the draft contains a placeholder');

let route = 'review';
if (!problems.length) {
  if (a.segment === 'spam') route = 'archive';
  else if (score >= s.hot_threshold) route = 'hot';
  else if (score >= s.nurture_threshold) route = 'nurture';
  else route = 'archive';
}
return { json: { ...lead, fit_score: score, segment: a.segment ?? null, reasons, subject, body, route, problems, model: $json.model || s.model } };
"""

UNAVAILABLE_JS = r"""
// The model call failed after its retries. Keep the lead anyway and send it to a person.
const lead = $('Validate and normalise').item.json;
const why = String(($json.error && ($json.error.message || $json.error.description)) || 'no response').slice(0, 300);
return { json: { ...lead, fit_score: null, segment: null, reasons: [], subject: '', body: '', route: 'review',
  problems: ['the model could not be reached: ' + why], model: $('Settings').item.json.model } };
"""

ERROR_JS = r"""
const e = $json.execution || {};
return { json: {
  workflow: ($json.workflow || {}).name || '',
  node: e.lastNodeExecuted || '',
  message: String((e.error || {}).message || 'unknown error').slice(0, 2000),
  execution_id: String(e.id || ''),
} };
"""


def intake():
    nodes = [
        node("New lead", "n8n-nodes-base.webhook", 2, (0, 300), {"httpMethod": "POST", "path": "lead", "responseMode": "responseNode", "options": {}}, webhookId=uid("webhook-lead")),
        node("Settings", "n8n-nodes-base.set", 3.4, (220, 300), {"assignments": {"assignments": [
            {"id": uid("s1"), "name": "business", "value": "Northwind Automation, a small studio that builds AI and workflow automations for businesses", "type": "string"},
            {"id": uid("s2"), "name": "ideal_customer", "value": "a company with 10 to 500 people that describes a concrete business process it wants to automate (support, sales operations, finance operations, document handling) and has a budget or timeline", "type": "string"},
            {"id": uid("s3"), "name": "signature", "value": "The Northwind team", "type": "string"},
            {"id": uid("s4"), "name": "hot_threshold", "value": 70, "type": "number"},
            {"id": uid("s5"), "name": "nurture_threshold", "value": 40, "type": "number"},
            {"id": uid("s6"), "name": "model", "value": "anthropic/claude-haiku-5.5", "type": "string"},
        ]}, "includeOtherFields": True, "options": {}}),
        code("Validate and normalise", (440, 300), VALIDATE_JS),
        route("Valid?", (660, 300), 2, "={{ $json.valid ? 0 : 1 }}"),
        reply("Reject: bad input", (880, 480), "={{ { status: 'rejected', problems: $json.problems } }}", 400),
        sql("Seen in the last 30 days?", (880, 220), "SELECT count(*)::int AS n FROM leads WHERE lower(email) = $1 AND received_at > now() - interval '30 days'", "={{ [$json.email] }}"),
        route("Duplicate?", (1100, 220), 2, "={{ $json.n > 0 ? 1 : 0 }}"),
        reply("Reply: duplicate", (1320, 400), "={{ { status: 'duplicate', message: 'We already have this lead from the last 30 days.' } }}"),
        code("Write the prompt", (1320, 140), PROMPT_JS.replace("[TOOL]", json.dumps([TOOL]))),
        node("Claude: qualify and draft", "n8n-nodes-base.httpRequest", 4.2, (1540, 140), {
            "method": "POST", "url": "https://openrouter.ai/api/v1/chat/completions",
            "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
            "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.payload) }}",
            "options": {"timeout": 30000},
        }, credentials=AI, retryOnFail=True, maxTries=3, waitBetweenTries=2000, onError="continueErrorOutput"),
        code("Model unavailable: keep the lead", (1760, 340), UNAVAILABLE_JS),
        code("Check the model's answer", (1760, 140), CHECK_JS),
        sql("Save lead", (1980, 140), """
INSERT INTO leads (email, name, company, message, source, fit_score, segment, reasons, route, problems, model)
VALUES ($1, $2, NULLIF($3, ''), $4, $5, $6, $7, $8::jsonb, $9, $10::jsonb, $11)
RETURNING id, route""", "={{ [$json.email, $json.name, $json.company, $json.message, $json.source, $json.fit_score, $json.segment, JSON.stringify($json.reasons), $json.route, JSON.stringify($json.problems), $json.model] }}"),
        route("Route", (2200, 140), 4, "={{ ({ hot: 0, nurture: 1, archive: 2, review: 3 })[$json.route] ?? 3 }}"),
        sql("Queue the draft for approval", (2420, -40), "INSERT INTO outbox (lead_id, subject, body) VALUES ($1, $2, $3) RETURNING lead_id, token",
            "={{ [$json.id, $('Check the model\\'s answer').item.json.subject, $('Check the model\\'s answer').item.json.body] }}"),
        reply("Reply: awaiting approval", (2640, -40), "={{ { status: 'pending_approval', lead_id: $json.lead_id, fit_score: $('Check the model\\'s answer').item.json.fit_score, approve_url: 'http://' + $('New lead').item.json.headers.host + '/webhook/approve?id=' + $json.lead_id + '&token=' + $json.token } }}"),
        sql("Schedule follow-ups", (2420, 120), "WITH ins AS (INSERT INTO follow_ups (lead_id, step, due_at) VALUES ($1, 1, now() + interval '2 days'), ($1, 2, now() + interval '7 days') RETURNING id) SELECT count(*)::int AS follow_ups FROM ins",
            "={{ [$json.id] }}"),
        reply("Reply: nurture", (2640, 120), "={{ { status: 'nurture', lead_id: $('Save lead').item.json.id, fit_score: $('Check the model\\'s answer').item.json.fit_score, follow_ups: $json.follow_ups } }}"),
        reply("Reply: archived", (2420, 280), "={{ { status: 'archived', lead_id: $json.id, fit_score: $('Check the model\\'s answer').item.json.fit_score, segment: $('Check the model\\'s answer').item.json.segment } }}"),
        reply("Reply: needs review", (2420, 440), "={{ { status: 'needs_review', lead_id: $json.id, problems: $('Check the model\\'s answer').item.json.problems } }}"),
    ]
    conns = wire(("New lead", "Settings"), ("Settings", "Validate and normalise"), ("Validate and normalise", "Valid?"),
                 ("Valid?", "Seen in the last 30 days?", 0), ("Valid?", "Reject: bad input", 1),
                 ("Seen in the last 30 days?", "Duplicate?"), ("Duplicate?", "Write the prompt", 0), ("Write the prompt", "Claude: qualify and draft"), ("Duplicate?", "Reply: duplicate", 1),
                 ("Claude: qualify and draft", "Check the model's answer", 0), ("Claude: qualify and draft", "Model unavailable: keep the lead", 1), ("Model unavailable: keep the lead", "Save lead"), ("Check the model's answer", "Save lead"), ("Save lead", "Route"),
                 ("Route", "Queue the draft for approval", 0), ("Route", "Schedule follow-ups", 1), ("Route", "Reply: archived", 2), ("Route", "Reply: needs review", 3),
                 ("Queue the draft for approval", "Reply: awaiting approval"), ("Schedule follow-ups", "Reply: nurture"))
    return {"id": IDS["intake"], "name": "Lead intake: qualify with Claude, route, queue drafts for approval", "nodes": nodes, "connections": conns,
            "settings": {"executionOrder": "v1", "errorWorkflow": IDS["errors"]}, "active": False, "pinData": {}}


def approve():
    nodes = [
        node("Approval link", "n8n-nodes-base.webhook", 2, (0, 300), {"httpMethod": "GET", "path": "approve", "responseMode": "responseNode", "options": {}}, webhookId=uid("webhook-approve")),
        sql("Mark approved", (220, 300), "UPDATE outbox SET status = 'sent', approved_at = now() WHERE lead_id = $1 AND token = $2 AND status = 'pending_approval' RETURNING lead_id, subject",
            "={{ [Number($json.query.id) || 0, String($json.query.token || '')] }}", always=True),
        route("Approved?", (440, 300), 2, "={{ $json.lead_id ? 0 : 1 }}"),
        code("Send the email", (660, 200), "// Replace this step with a Gmail, Outlook or SMTP node. The draft only goes out after a person clicks the link.\nreturn { json: { ...$json, sent: true } };"),
        reply("Reply: sent", (880, 200), "=<p>Approved. Reply to lead {{ $json.lead_id }} (\"{{ $json.subject }}\") was sent.</p>", 200, html=True),
        reply("Reply: invalid link", (660, 400), "<p>This approval link was already used or is not valid.</p>", 404, html=True),
    ]
    conns = wire(("Approval link", "Mark approved"), ("Mark approved", "Approved?"), ("Approved?", "Send the email", 0), ("Approved?", "Reply: invalid link", 1),
                 ("Send the email", "Reply: sent"))
    return {"id": IDS["approve"], "name": "Lead intake: approve a draft from its link", "nodes": nodes, "connections": conns,
            "settings": {"executionOrder": "v1", "errorWorkflow": IDS["errors"]}, "active": False, "pinData": {}}


def errors():
    nodes = [
        node("When a workflow fails", "n8n-nodes-base.errorTrigger", 1, (0, 300), {}),
        code("Describe the failure", (220, 300), ERROR_JS),
        sql("Log it", (440, 300), "INSERT INTO errors (workflow, node, message, execution_id) VALUES ($1, $2, $3, $4) RETURNING id",
            "={{ [$json.workflow, $json.node, $json.message, $json.execution_id] }}"),
    ]
    conns = wire(("When a workflow fails", "Describe the failure"), ("Describe the failure", "Log it"))
    return {"id": IDS["errors"], "name": "Lead intake: log failures", "nodes": nodes, "connections": conns,
            "settings": {"executionOrder": "v1"}, "active": False, "pinData": {}}


if __name__ == "__main__":
    out = ROOT / "workflows"
    out.mkdir(exist_ok=True)
    for name, wf in (("lead-intake", intake()), ("approve-link", approve()), ("log-failures", errors())):
        (out / f"{name}.json").write_text(json.dumps(wf, indent=2) + "\n")
        print(f"workflows/{name}.json: {len(wf['nodes'])} nodes")
