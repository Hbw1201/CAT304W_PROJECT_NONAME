# MAQ Chat Service

Chat service for the MAQ-SCREEN system, running on port 3000.

## Initial Setup

**First-time setup (Windows):**

```powershell
# Navigate to node directory
cd node

# Install dependencies
npm install

# Return to project root
cd ..
```

**First-time setup (Linux/Mac):**

```bash
# Navigate to node directory
cd node

# Install dependencies
npm install

# Return to project root
cd ..
```

## Running the Service

### Automatic Start (Recommended)

The chat service is automatically started by `backend/main.py` when you run:

```bash
python backend/main.py
```

### Manual Start (Development/Testing)

**Prerequisites:**
1. Ensure `.env` file exists in `node/` directory (see Configuration Setup above)
2. Install dependencies: `npm install`

**Start the service:**
```bash
cd node
node chatbot.js
```

The service will:
- Start on port 3000 (or `NODE_CHAT_PORT` if set)
- Provide health check endpoint at `GET /health`
- Handle chat requests at `POST /api/chat`
- Display startup logs including API key configuration status

## Manual Testing

**Check health (Windows PowerShell):**

```powershell
Invoke-RestMethod http://127.0.0.1:3000/health
```

**Check health (Linux/Mac):**

```bash
curl http://127.0.0.1:3000/health
```

**Test chat endpoint (Windows PowerShell):**

```powershell
# Test with a message
$body = @{ message = "hi" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3000/api/chat -ContentType "application/json" -Body $body

# Expected response:
# {
#   "ok": true,
#   "reply": "<DashScope generated response>",
#   "answer": "<same as reply>"
# }
```

**Test chat endpoint (Linux/Mac):**

```bash
curl -X POST http://127.0.0.1:3000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hi"}'

# Expected response:
# {
#   "ok": true,
#   "reply": "<DashScope generated response>",
#   "answer": "<same as reply>"
# }
```

## Environment Variables

### Configuration Setup

**Development Setup:**

1. Copy the example environment file:
   ```bash
   cd node
   cp .env.example .env
   ```

2. Edit `.env` and fill in your DashScope API Key:
   ```
   DASHSCOPE_API_KEY=sk-your-actual-api-key-here
   DASHSCOPE_MODEL=qwen-plus
   ```

3. Get your API Key from: https://dashscope.console.aliyun.com/

### Available Variables

- `DASHSCOPE_API_KEY` (Required): Your Alibaba Cloud DashScope API Key
  - Get it from: https://dashscope.console.aliyun.com/
  - Format: `sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

- `DASHSCOPE_MODEL` (Optional, default: `qwen-plus`): Model name to use
  - Available options: `qwen-plus`, `qwen-turbo`, `qwen-max`
  - `qwen-plus`: Recommended, balanced performance
  - `qwen-turbo`: Faster, lower cost
  - `qwen-max`: Most capable, higher cost

- `PORT` or `NODE_CHAT_PORT` (Optional, default: 3000): Service port number

## Logs

Service logs are written to `backend/logs/chatbot-node.log`.

If the service fails to start, check:
1. The log file: `backend/logs/chatbot-node.log`
2. Node.js is installed: `node --version`
3. Dependencies are installed: `npm list` in the `node/` directory

