#!/usr/bin/env python3
"""
Agent Communication Hub - Minimal server for multi-agent coordination
"""
import json
import os
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

CHANNELS_FILE = "channels.json"

def load_channels():
    """Load channels from disk"""
    if not os.path.exists(CHANNELS_FILE):
        return {}
    with open(CHANNELS_FILE, 'r') as f:
        return json.load(f)

def save_channels(channels):
    """Save channels to disk"""
    with open(CHANNELS_FILE, 'w') as f:
        json.dump(channels, f, indent=2)

def validate_name(name, name_type="name"):
    """Validate channel/agent name: only lowercase letters, numbers, underscores"""
    import re
    if not re.match(r'^[a-z0-9_]+$', name):
        return False, f"Invalid {name_type}: only lowercase letters, numbers, and underscores allowed"
    return True, None

# @@@ API Endpoints - Core message passing

@app.route('/api/send', methods=['POST'])
def send_message():
    """Send a message to a channel"""
    data = request.json
    channel = data.get('channel')
    agent = data.get('agent')
    text = data.get('text')

    if not all([channel, agent, text]):
        return jsonify({"error": "Missing channel, agent, or text"}), 400

    # Validate names
    valid, error = validate_name(channel, "channel name")
    if not valid:
        return jsonify({"error": error}), 400

    valid, error = validate_name(agent, "agent name")
    if not valid:
        return jsonify({"error": error}), 400

    channels = load_channels()

    # Create channel if doesn't exist
    if channel not in channels:
        channels[channel] = {"messages": [], "last_read": {}}

    channel_data = channels[channel]

    # @@@ Enforce "read before send" - agent must be caught up
    last_read_index = channel_data.get("last_read", {}).get(agent, -1)
    total_messages = len(channel_data["messages"])

    if last_read_index < total_messages - 1:
        unread_count = total_messages - last_read_index - 1
        return jsonify({
            "error": "You have unread messages. Please check messages first.",
            "unread_count": unread_count,
            "hint": f"GET /api/messages?channel={channel}&agent={agent}"
        }), 403

    # Add message
    message = {
        "time": datetime.now().isoformat(),
        "agent": agent,
        "text": text
    }
    channel_data["messages"].append(message)

    # @@@ Automatically mark this message as read for the sender
    # Prevents agent from re-reading their own message
    if "last_read" not in channel_data:
        channel_data["last_read"] = {}
    channel_data["last_read"][agent] = len(channel_data["messages"]) - 1

    save_channels(channels)

    return jsonify({"success": True, "message_index": len(channel_data["messages"]) - 1})

@app.route('/api/messages', methods=['GET'])
def get_messages():
    """Get messages from a channel with two modes: new (default) or history"""
    channel = request.args.get('channel')
    agent = request.args.get('agent')
    mode = request.args.get('mode', 'new')  # 'new' or 'history'
    limit = request.args.get('limit', '20')  # Default 20, minimum 20

    if not channel:
        return jsonify({"error": "Missing channel parameter"}), 400

    if not agent:
        return jsonify({"error": "Missing agent parameter"}), 400

    if mode not in ['new', 'history']:
        return jsonify({"error": "Invalid mode. Must be 'new' or 'history'"}), 400

    # Parse and validate limit
    try:
        limit = max(20, int(limit))  # Minimum 20
    except ValueError:
        return jsonify({"error": "Invalid limit parameter. Must be an integer."}), 400

    # Validate names
    valid, error = validate_name(channel, "channel name")
    if not valid:
        return jsonify({"error": error}), 400

    valid, error = validate_name(agent, "agent name")
    if not valid:
        return jsonify({"error": error}), 400

    channels = load_channels()

    if channel not in channels:
        # Create empty channel
        channels[channel] = {"messages": [], "last_read": {}}
        save_channels(channels)
        return jsonify({"messages": [], "total": 0, "new_messages": 0, "mode": mode})

    channel_data = channels[channel]

    if mode == 'history':
        # @@@ History mode: return at most `limit` most recent messages, don't update last_read
        all_messages = channel_data["messages"]
        limited_messages = all_messages[-limit:] if len(all_messages) > limit else all_messages

        return jsonify({
            "messages": limited_messages,
            "total": len(channel_data["messages"]),
            "returned": len(limited_messages),
            "mode": "history"
        })

    # @@@ New mode (default): Get only NEW messages since agent's last_read
    last_read_index = channel_data.get("last_read", {}).get(agent, -1)
    all_new_messages = channel_data["messages"][last_read_index + 1:]

    # @@@ If more than limit unread messages, skip oldest ones to prevent context overflow
    if len(all_new_messages) > limit:
        # Move last_read forward to skip oldest messages
        skip_count = len(all_new_messages) - limit
        new_last_read = last_read_index + skip_count
        new_messages = all_new_messages[skip_count:]
    else:
        new_last_read = len(channel_data["messages"]) - 1
        new_messages = all_new_messages

    # @@@ Update last_read to latest message index (or to skip point if too many)
    if len(channel_data["messages"]) > 0:
        if "last_read" not in channel_data:
            channel_data["last_read"] = {}
        channel_data["last_read"][agent] = new_last_read
        save_channels(channels)

    return jsonify({
        "messages": new_messages,
        "total": len(channel_data["messages"]),
        "new_messages": len(new_messages),
        "skipped": len(all_new_messages) - len(new_messages) if len(all_new_messages) > limit else 0,
        "mode": "new"
    })

@app.route('/api/channels', methods=['GET'])
def list_channels():
    """List all channels"""
    channels = load_channels()

    channel_list = []
    for name, data in channels.items():
        channel_list.append({
            "name": name,
            "message_count": len(data.get("messages", []))
        })

    return jsonify({"channels": channel_list})

# @@@ Human-readable endpoints

@app.route('/channel/<channel_name>')
def channel_info(channel_name):
    """Human-readable channel info (for curl)"""
    channels = load_channels()

    if channel_name not in channels:
        info = f"""# Channel: {channel_name}

This channel doesn't exist yet. Send the first message to create it!

## Send a Message
curl -X POST http://localhost:5000/api/send \\
  -H "Content-Type: application/json" \\
  -d '{{"channel": "{channel_name}", "agent": "YourName", "text": "Hello team!"}}'

## Read Messages (required before sending!)
curl "http://localhost:5000/api/messages?channel={channel_name}&agent=YourName"

## Web View
http://localhost:5000/web/{channel_name}
"""
        return info, 200, {'Content-Type': 'text/plain; charset=utf-8'}

    messages = channels[channel_name]["messages"]
    recent = messages[-10:]  # Last 10 messages

    info = f"""# Channel: {channel_name}

Total messages: {len(messages)}

## Recent Messages (last {len(recent)})
"""

    for msg in recent:
        time_str = datetime.fromisoformat(msg['time']).strftime('%Y-%m-%d %H:%M:%S')
        info += f"\n[{time_str}] {msg['agent']}: {msg['text']}\n"

    info += f"""

## Send a Message
curl -X POST http://localhost:5000/api/send \\
  -H "Content-Type: application/json" \\
  -d '{{"channel": "{channel_name}", "agent": "YourName", "text": "Your message"}}'

## Read Messages (required before sending!)
curl "http://localhost:5000/api/messages?channel={channel_name}&agent=YourName"

## Web View
http://localhost:5000/web/{channel_name}
"""

    return info, 200, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/web/<channel_name>')
def web_view(channel_name):
    """Web UI for viewing channel"""
    channels = load_channels()

    if channel_name not in channels:
        channels[channel_name] = {"messages": []}

    messages = channels[channel_name]["messages"]

    # Get unique agents for color mapping
    agents = list(set(msg['agent'] for msg in messages))
    colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#34495e']
    agent_colors = {agent: colors[i % len(colors)] for i, agent in enumerate(agents)}

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ channel_name }} - Agent Hub</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            color: #333;
        }
        .header {
            background: white;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .header h1 {
            font-size: 24px;
            color: #2c3e50;
        }
        .header .stats {
            margin-top: 10px;
            font-size: 14px;
            color: #7f8c8d;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .message {
            background: white;
            padding: 15px 20px;
            margin-bottom: 10px;
            border-radius: 8px;
            border-left: 4px solid;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            animation: slideIn 0.3s ease;
        }
        @keyframes slideIn {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .message-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
        }
        .agent-name {
            font-weight: bold;
            font-size: 14px;
        }
        .timestamp {
            font-size: 12px;
            color: #95a5a6;
        }
        .message-text {
            line-height: 1.6;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .empty {
            text-align: center;
            padding: 60px 20px;
            color: #95a5a6;
        }
        .send-form {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: white;
            padding: 20px;
            box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
        }
        .send-form .form-container {
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            gap: 10px;
        }
        .send-form input, .send-form textarea {
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-family: inherit;
        }
        .send-form input[name="agent"] {
            width: 150px;
        }
        .send-form textarea {
            flex: 1;
            resize: vertical;
            min-height: 40px;
            max-height: 200px;
        }
        .send-form button {
            padding: 10px 30px;
            background: #3498db;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
        }
        .send-form button:hover {
            background: #2980b9;
        }
        .messages-container {
            margin-bottom: 140px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>{{ channel_name }}</h1>
        <div class="stats">
            <span id="message-count">{{ message_count }} messages</span>
            <span style="margin-left: 20px;">Auto-refresh every 2s</span>
        </div>
    </div>

    <div class="container">
        <div class="messages-container" id="messages">
            {% if messages %}
                {% for msg in messages %}
                <div class="message" style="border-left-color: {{ agent_colors[msg.agent] }}">
                    <div class="message-header">
                        <span class="agent-name" style="color: {{ agent_colors[msg.agent] }}">{{ msg.agent }}</span>
                        <span class="timestamp">{{ format_time(msg.time) }}</span>
                    </div>
                    <div class="message-text">{{ msg.text }}</div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty">No messages yet. Be the first to send one!</div>
            {% endif %}
        </div>
    </div>

    <div class="send-form">
        <div class="form-container">
            <input type="text" name="agent" placeholder="Your agent name" id="agent-input" />
            <textarea name="text" placeholder="Type your message..." id="text-input"></textarea>
            <button onclick="sendMessage()">Send</button>
        </div>
    </div>

    <script>
        const channelName = "{{ channel_name }}";
        let lastMessageCount = {{ message_count }};

        // Load saved agent name
        const savedAgent = localStorage.getItem('agent_name');
        if (savedAgent) {
            document.getElementById('agent-input').value = savedAgent;
        }

        async function sendMessage() {
            const agent = document.getElementById('agent-input').value.trim();
            const text = document.getElementById('text-input').value.trim();

            if (!agent || !text) {
                alert('Please enter both agent name and message');
                return;
            }

            // Save agent name
            localStorage.setItem('agent_name', agent);

            // @@@ Check messages first (enforces "read before send")
            await refreshMessages();

            try {
                const response = await fetch('/api/send', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ channel: channelName, agent, text })
                });

                const result = await response.json();

                if (response.ok) {
                    document.getElementById('text-input').value = '';
                    refreshMessages();
                } else {
                    alert(result.error + '\nUnread: ' + result.unread_count);
                }
            } catch (e) {
                console.error('Send failed:', e);
            }
        }

        async function refreshMessages() {
            const agent = document.getElementById('agent-input').value.trim();
            if (!agent) return;

            try {
                const response = await fetch(`/api/messages?channel=${channelName}&agent=${agent}`);
                const data = await response.json();

                if (data.total !== lastMessageCount) {
                    location.reload();
                }
            } catch (e) {
                console.error('Refresh failed:', e);
            }
        }

        // Auto-refresh every 2 seconds
        setInterval(refreshMessages, 2000);

        // Send on Ctrl+Enter
        document.getElementById('text-input').addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') {
                sendMessage();
            }
        });
    </script>
</body>
</html>
    """

    def format_time(iso_time):
        dt = datetime.fromisoformat(iso_time)
        return dt.strftime('%Y-%m-%d %H:%M:%S')

    return render_template_string(html,
        channel_name=channel_name,
        messages=messages,
        message_count=len(messages),
        agent_colors=agent_colors,
        format_time=format_time
    )

@app.route('/')
def index():
    """Agent documentation - comprehensive guide for AI agents"""
    doc = """# AgentTalk - Multi-Agent Coordination Hub

## What This Is

A minimal communication server for AI agents working together on projects. Agents send and read messages through simple HTTP endpoints. The system enforces "check before send" to ensure all agents stay synchronized.

## Core Rules

1. **Check before send** - You MUST read messages before you can send. This is enforced by the server.
2. **Identify yourself** - Always provide your agent name when reading or sending.
3. **Stay in your channel** - Each project has a channel. Only communicate in your assigned channel.
4. **Lowercase names only** - Channel and agent names must use only lowercase letters, numbers, and underscores (a-z, 0-9, _).

## Why These Rules?

- **Check before send**: Prevents agents from talking without listening. Ensures coordination and avoids duplicate work.
- **Agent identification**: The server tracks what each agent has read to enforce the check-before-send rule.
- **Lowercase naming**: Prevents typos and case-sensitivity issues (Project-Alpha vs project-alpha).

## API Usage

### Read Messages (Required First!)

Two modes available: `new` (default) and `history`

**Mode: new (default) - Get only new messages**
```bash
curl "http://SERVER/api/messages?channel=CHANNEL_NAME&agent=AGENT_NAME"
# or explicitly:
curl "http://SERVER/api/messages?channel=CHANNEL_NAME&agent=AGENT_NAME&mode=new"
# with custom limit:
curl "http://SERVER/api/messages?channel=CHANNEL_NAME&agent=AGENT_NAME&limit=50"
```

**What happens:**
- Returns only NEW messages since your last read
- Automatically updates your `last_read` position
- Your own sent messages are NOT included (already marked as read when you sent them)
- Next call will only return messages added after this call
- **Limit**: At most `limit` messages (default: 20, minimum: 20)
  - If more than `limit` unread messages exist, oldest ones are automatically skipped (marked as read)
  - Prevents context overflow in long conversations
  - Response includes `skipped` count if any messages were skipped

**Mode: history - Get full history**
```bash
curl "http://SERVER/api/messages?channel=CHANNEL_NAME&agent=AGENT_NAME&mode=history"
# with custom limit:
curl "http://SERVER/api/messages?channel=CHANNEL_NAME&agent=AGENT_NAME&mode=history&limit=50"
```

**What happens:**
- Returns up to `limit` most recent messages (default: 20)
- Includes your own messages
- Does NOT update your `last_read` position
- Use for: catching up on context, debugging, reviewing full conversation

**Example (new mode):**
```bash
curl "http://localhost:5000/api/messages?channel=my_project&agent=worker_1"
```

**Response:**
```json
{
  "messages": [
    {"time": "2025-10-23T10:13:40", "agent": "supervisor", "text": "Start working on task A"},
    {"time": "2025-10-23T10:15:20", "agent": "worker_2", "text": "I'll handle task B"}
  ],
  "total": 2,
  "new_messages": 2,
  "skipped": 0,
  "mode": "new"
}
```

**Note:** If there were 50 unread messages and you used default limit (20), you'd get:
```json
{
  "messages": [...20 most recent...],
  "total": 50,
  "new_messages": 20,
  "skipped": 30,
  "mode": "new"
}
```

### Send Message

```bash
curl -X POST http://SERVER/api/send \\
  -H "Content-Type: application/json" \\
  -d '{"channel":"CHANNEL_NAME","agent":"AGENT_NAME","text":"your message"}'
```

**Example:**
```bash
curl -X POST http://localhost:5000/api/send \\
  -H "Content-Type: application/json" \\
  -d '{"channel":"my_project","agent":"worker_1","text":"Task A completed"}'
```

**Success response:**
```json
{"success": true, "message_index": 3}
```

**Error if you didn't check first:**
```json
{
  "error": "You have unread messages. Please check messages first.",
  "unread_count": 2,
  "hint": "GET /api/messages?channel=my_project&agent=worker_1"
}
```

**Important:** When you send a message, it's automatically marked as read for you. You won't see your own message in the next `mode=new` read.

### File-Based Approach (Recommended for Complex Messages)

Inline JSON in curl can be error-prone with special characters. The most reliable method is using a file:

```bash
# Create message file
cat > /tmp/msg.json <<'EOF'
{
  "channel": "my_project",
  "agent": "worker_1",
  "text": "Here's my detailed response with 'quotes', newlines,\nand special characters!"
}
EOF

# Send it
curl -X POST http://localhost:5000/api/send \\
  -H "Content-Type: application/json" \\
  -d @/tmp/msg.json
```

**Why this approach?**
- Handles ALL special characters without escaping
- No quote/backslash escaping needed
- Works with multi-line messages
- Easy to debug (inspect /tmp/msg.json)
- Most portable across shells

**For simple messages**, inline JSON is fine:
```bash
curl -X POST http://localhost:5000/api/send \\
  -H "Content-Type: application/json" \\
  -d '{"channel":"test","agent":"bot","text":"Simple message"}'
```

**For programmatic use**, use Python/jq/etc to generate JSON properly.

## Typical Workflow

```bash
# 1. Check messages (always do this first!)
curl "http://localhost:5000/api/messages?channel=my_project&agent=worker_1"

# 2. Read and process what teammates said
# (your logic here)

# 3. Send your response
curl -X POST http://localhost:5000/api/send \\
  -H "Content-Type: application/json" \\
  -d '{"channel":"my_project","agent":"worker_1","text":"I will handle the backend"}'

# 4. Repeat: check -> think -> send
```

## Polling Pattern

For continuous coordination:

```bash
while true; do
  RESPONSE=$(curl -s "http://localhost:5000/api/messages?channel=my_project&agent=worker_1")
  NEW_COUNT=$(echo "$RESPONSE" | jq '.new_messages')

  if [ "$NEW_COUNT" -gt 0 ]; then
    echo "New messages from team!"
    echo "$RESPONSE" | jq '.messages'
    # Process and respond...
  fi

  sleep 5
done
```

## Web UI

View channel in browser: `http://SERVER/web/CHANNEL_NAME`

Features:
- See all messages with color-coded agents
- Send messages through form
- Auto-refreshes every 2 seconds

## Naming Conventions

**Valid names:**
- `my_project`
- `worker_1`
- `backend_team`
- `test123`

**Invalid names:**
- `My-Project` (uppercase, hyphen)
- `worker.1` (dot not allowed)
- `team@alpha` (special characters)

## Error Handling

**403 Forbidden**: You have unread messages. Check messages first.
**400 Bad Request**: Invalid channel/agent name or missing parameters.

## Example: Starting as a New Agent

```bash
# You're told: "You are worker_mars on channel project_apollo"

# Step 1: Check messages to see what's happening
curl "http://localhost:5000/api/messages?channel=project_apollo&agent=worker_mars"

# Step 2: Introduce yourself
curl -X POST http://localhost:5000/api/send \\
  -H "Content-Type: application/json" \\
  -d '{"channel":"project_apollo","agent":"worker_mars","text":"Hello team! Worker Mars here, ready to help."}'

# Step 3: Keep checking and responding
```

## Quick Reference

| Endpoint | Purpose |
|----------|---------|
| `GET /` | This documentation |
| `GET /api/messages?channel=X&agent=Y` | Read new messages (mode=new, limit=20) |
| `GET /api/messages?channel=X&agent=Y&mode=history` | Get recent history (limit=20, doesn't update position) |
| `GET /api/messages?channel=X&agent=Y&limit=50` | Read with custom limit (min: 20) |
| `POST /api/send` | Send message (auto-marks as read, requires caught up) |
| `GET /channel/X` | Channel info and recent messages |
| `GET /web/X` | Web UI for channel |

---

**Remember**: Always check before you send. The system will reject your message if you haven't read the latest updates from your team.
"""
    return doc, 200, {'Content-Type': 'text/plain; charset=utf-8'}

if __name__ == '__main__':
    print("🚀 Agent Communication Hub starting...")
    print("📡 Server running at: http://localhost:5000")
    print("💬 View channels at: http://localhost:5000")
    print("\n📝 Quick test:")
    print('curl -X POST http://localhost:5000/api/send -H "Content-Type: application/json" -d \'{"channel": "test", "agent": "TestAgent", "text": "Hello world!"}\'')
    print()
    app.run(host='0.0.0.0', port=5000, debug=True)
