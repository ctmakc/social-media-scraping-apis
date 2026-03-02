const express = require('express');
const fs = require('fs');
const path = require('path');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

const CONFIG_FILE = process.env.CONFIG_FILE || path.join(__dirname, '.config.json');

// Ensure config directory exists (needed for Docker volume mount)
fs.mkdirSync(path.dirname(CONFIG_FILE), { recursive: true });

function loadConfig() {
  try {
    if (fs.existsSync(CONFIG_FILE)) {
      return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
    }
  } catch (e) {
    console.error('Config load error:', e.message);
  }
  return { keys: {} };
}

function saveConfig(config) {
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
}

function maskKey(val) {
  if (!val || val.length < 8) return val ? '****' : '';
  return val.substring(0, 4) + '••••••••' + val.slice(-4);
}

// GET /api/config — masked keys for display
app.get('/api/config', (req, res) => {
  const config = loadConfig();
  const masked = {};
  for (const [k, v] of Object.entries(config.keys || {})) {
    masked[k] = maskKey(v);
  }
  const hasApify = !!(config.keys?.APIFY_TOKEN);
  res.json({ keys: masked, hasApify });
});

// POST /api/config — save keys (only updates non-empty values)
app.post('/api/config', (req, res) => {
  const config = loadConfig();
  if (!config.keys) config.keys = {};
  for (const [k, v] of Object.entries(req.body.keys || {})) {
    if (v && v.trim() !== '') {
      config.keys[k] = v.trim();
    }
  }
  saveConfig(config);
  res.json({ success: true });
});

// DELETE /api/config/:key — delete a key
app.delete('/api/config/:key', (req, res) => {
  const config = loadConfig();
  delete config.keys[req.params.key];
  saveConfig(config);
  res.json({ success: true });
});

// POST /api/validate — validate Apify token
app.post('/api/validate', async (req, res) => {
  const config = loadConfig();
  const token = config.keys?.APIFY_TOKEN;

  if (!token) {
    return res.json({ valid: false, message: 'Token not saved yet' });
  }

  try {
    const response = await fetch(`https://api.apify.com/v2/users/me?token=${token}`);
    const data = await response.json();

    if (response.ok && data.data?.id) {
      res.json({
        valid: true,
        username: data.data.username,
        email: data.data.email,
        plan: data.data.plan?.tier || 'free'
      });
    } else {
      res.json({ valid: false, message: data.error?.message || 'Invalid token' });
    }
  } catch (err) {
    res.json({ valid: false, message: `Network error: ${err.message}` });
  }
});

// POST /api/run — start actor run and poll until done
app.post('/api/run', async (req, res) => {
  const config = loadConfig();
  const token = config.keys?.APIFY_TOKEN;

  if (!token) {
    return res.status(400).json({ error: 'Apify token not configured. Go to the API Keys section.' });
  }

  const { actorId, input } = req.body;

  if (!actorId) {
    return res.status(400).json({ error: 'actorId is required' });
  }

  // Apify API uses ~ instead of / in actor IDs within URL paths
  const actorPath = actorId.replace('/', '~');
  const startTime = Date.now();

  try {
    // 1. Start the run
    const runRes = await fetch(
      `https://api.apify.com/v2/acts/${actorPath}/runs?token=${token}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input || {})
      }
    );

    if (!runRes.ok) {
      const err = await runRes.json().catch(() => ({}));
      return res.status(runRes.status).json({
        error: err.error?.message || `Apify error ${runRes.status}`
      });
    }

    const runData = await runRes.json();
    const runId = runData.data?.id;

    if (!runId) {
      return res.status(500).json({ error: 'No run ID returned from Apify' });
    }

    // 2. Poll for completion (max 120 seconds)
    const maxWait = 120_000;
    let status = 'RUNNING';

    while (Date.now() - startTime < maxWait) {
      await new Promise(r => setTimeout(r, 3000));

      const pollRes = await fetch(
        `https://api.apify.com/v2/acts/${actorPath}/runs/${runId}?token=${token}`
      );

      if (!pollRes.ok) break;

      const pollData = await pollRes.json();
      status = pollData.data?.status;

      if (!['RUNNING', 'READY', 'ABORTING'].includes(status)) break;
    }

    const duration = Math.round((Date.now() - startTime) / 1000);
    const consoleUrl = `https://console.apify.com/actors/${actorPath}/runs/${runId}`;

    if (status === 'SUCCEEDED') {
      const itemsRes = await fetch(
        `https://api.apify.com/v2/acts/${actorPath}/runs/${runId}/dataset/items?token=${token}&limit=50`
      );
      const items = await itemsRes.json();

      return res.json({
        success: true,
        status,
        runId,
        duration,
        count: Array.isArray(items) ? items.length : 0,
        data: items,
        consoleUrl
      });
    }

    if (['RUNNING', 'READY'].includes(status)) {
      return res.json({
        success: false,
        status: 'TIMEOUT',
        runId,
        duration,
        message: 'Run is still in progress (>120s). Check Apify Console for results.',
        consoleUrl
      });
    }

    return res.json({
      success: false,
      status: status || 'FAILED',
      runId,
      duration,
      message: `Run finished with status: ${status}`,
      consoleUrl
    });

  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`
╔══════════════════════════════════════════╗
║    Social Media API — Admin Panel        ║
║    http://localhost:${PORT}                ║
╚══════════════════════════════════════════╝
  `);
});
