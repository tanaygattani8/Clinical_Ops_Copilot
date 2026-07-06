import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from agents.orchestrator import root_agent
from agents._audit import audit_log, read_audit_log
from rag.brief_history import retrieve_latest, retrieve_by_date
from google.adk.runners import InMemoryRunner
from google.genai import types

app = FastAPI(title="Clinic Operations Copilot")

DISCLAIMER = (
    "This report is for operational decision support only. "
    "It does not constitute medical diagnosis, treatment recommendation, or clinical advice."
)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Clinic Operations Copilot</title>
<style>
body { margin: 0; font-family: Arial, Helvetica, sans-serif; background: #f5f5f5; color: #333; }
.container { display: flex; height: calc(100vh - 40px); }
.left { flex: 1; display: flex; flex-direction: column; border-right: 1px solid #ddd; background: #fff; }
.right { flex: 1; overflow-y: auto; padding: 16px; background: #fafafa; }
.chat-header { padding: 12px 16px; background: #2c5282; color: #fff; font-size: 18px; font-weight: bold; }
.chat-messages { flex: 1; overflow-y: auto; padding: 16px; }
.chat-input-area { display: flex; padding: 12px; border-top: 1px solid #ddd; }
.chat-input-area input { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; }
.chat-input-area button { margin-left: 8px; padding: 10px 20px; background: #2c5282; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
.chat-input-area button:hover { background: #2b6cb0; }
.msg { margin-bottom: 12px; padding: 8px 12px; border-radius: 6px; max-width: 80%; }
.msg-user { background: #ebf4ff; margin-left: auto; text-align: right; }
.msg-bot { background: #e2e8f0; }
.brief-card { background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 12px; margin-bottom: 12px; }
.brief-card h3 { margin: 0 0 8px 0; }
footer { height: 40px; display: flex; align-items: center; justify-content: center; background: #2c5282; color: #fff; font-size: 12px; }
</style>
</head>
<body>
<div class="container">
  <div class="left">
    <div class="chat-header">Clinic Operations Copilot</div>
    <div class="chat-messages" id="chatMessages"></div>
    <div class="chat-input-area">
      <input type="text" id="chatInput" placeholder="Ask about clinic operations..." />
      <button id="sendBtn" onclick="sendMessage()">Send</button>
    </div>
  </div>
  <div class="right">
    <h2>Recent Briefs</h2>
    <div id="briefsList"></div>
  </div>
</div>
<footer>""" + DISCLAIMER + """</footer>
<script>
var sessionId = 'session_' + Date.now();

async function sendMessage() {
  var input = document.getElementById('chatInput');
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  appendMessage(msg, 'user');
  document.getElementById('sendBtn').disabled = true;
  try {
    var res = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg, session_id: sessionId})
    });
    var data = await res.json();
    appendMessage(data.response || 'No response.', 'bot');
  } catch(e) {
    appendMessage('Error: ' + e.message, 'bot');
  }
  document.getElementById('sendBtn').disabled = false;
}

function appendMessage(text, sender) {
  var div = document.createElement('div');
  div.className = 'msg msg-' + sender;
  div.textContent = text;
  var container = document.getElementById('chatMessages');
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

document.getElementById('chatInput').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') sendMessage();
});

async function loadBriefs() {
  try {
    var res = await fetch('/api/briefs?n=5');
    var data = await res.json();
    var container = document.getElementById('briefsList');
    container.innerHTML = '';
    if (data.length === 0) {
      container.innerHTML = '<p>No briefs generated yet.</p>';
      return;
    }
    data.forEach(function(b) {
      var card = document.createElement('div');
      card.className = 'brief-card';
      card.innerHTML = '<h3>' + b.date + '</h3><pre>' +
        (b.brief_markdown || '').substring(0, 300) + '...</pre>';
      card.style.cursor = 'pointer';
      card.onclick = function() { viewBrief(b.date); };
      container.appendChild(card);
    });
  } catch(e) {}
}

async function viewBrief(date) {
  try {
    var res = await fetch('/api/briefs/' + date);
    var data = await res.json();
    if (data && data.brief_markdown) {
      var container = document.getElementById('briefsList');
      container.innerHTML = '<button onclick="loadBriefs()">Back</button>' +
        '<h3>Brief: ' + data.date + '</h3><pre>' + data.brief_markdown + '</pre>';
    }
  } catch(e) {}
}

loadBriefs();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=HTML_PAGE)


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", "default")

    audit_log("web", "chat_request", {"message_snippet": message[:120], "session_id": session_id})

    runner = InMemoryRunner(agent=root_agent)
    user_msg = types.Content(role="user", parts=[types.Part(text=message)])

    response_text = ""
    async for event in runner.run_async(
        user_id="web_user",
        session_id=session_id,
        new_message=user_msg
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    response_text += part.text

    return JSONResponse({"response": response_text, "session_id": session_id})


@app.get("/api/briefs")
async def list_briefs(n: int = 5):
    briefs = retrieve_latest(n)
    return JSONResponse(briefs)


@app.get("/api/briefs/{date}")
async def get_brief(date: str):
    brief = retrieve_by_date(date)
    if brief is None:
        return JSONResponse({"error": "Brief not found"}, status_code=404)
    return JSONResponse(brief)


@app.post("/api/trigger-brief")
async def trigger_brief():
    from monitoring.loop import run_daily_brief
    result = await run_daily_brief(force=True)
    return JSONResponse(result)


@app.get("/api/audit-log")
async def get_audit_log(n: int = 50):
    entries = read_audit_log(n)
    return JSONResponse(entries)


@app.get("/health")
async def health():
    return JSONResponse({"status": "healthy"})
