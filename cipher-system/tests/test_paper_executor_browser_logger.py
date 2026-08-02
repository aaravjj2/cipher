import json
import subprocess
from pathlib import Path


def test_browser_logger_local_success_and_remote_fallback():
    script = Path("scripts/accessobsidian_browser_logger.js").read_text(encoding="utf-8")
    harness = f"""
global.window = {{ CIPHER_REMOTE_SCANNER_INGEST_URL: 'https://remote.example/ingest' }};
global.TextEncoder = TextEncoder;
const nodeCrypto = require('crypto');
// Node 12 has no WebCrypto implementation; emulate only the browser API used
// by the logger so this contract test remains runnable on the system Node.
global.crypto = nodeCrypto.webcrypto || {{
  subtle: {{
    digest: async (_algorithm, bytes) => {{
      const hash = nodeCrypto.createHash('sha256').update(Buffer.from(bytes)).digest();
      return hash.buffer.slice(hash.byteOffset, hash.byteOffset + hash.byteLength);
    }}
  }}
}};
global.AbortController = global.AbortController || class AbortController {{
  constructor() {{ this.signal = {{}}; }}
  abort() {{}}
}};
const calls = [];
global.fetch = async (url, opts) => {{
  calls.push({{ url, body: opts.body }});
  return {{ ok: url.startsWith('http://127.0.0.1') }};
}};
{script}
(async () => {{
  const first = await window.CipherAccessObsidianLogger.deliverScannerSnapshot({{ cards: [{{ ticker: 'AAPL' }}] }});
  global.fetch = async (url, opts) => {{
    calls.push({{ url, body: opts.body }});
    return {{ ok: url.startsWith('https://remote.example') }};
  }};
  const second = await window.CipherAccessObsidianLogger.deliverScannerSnapshot({{ cards: [{{ ticker: 'AAPL' }}] }});
  console.log(JSON.stringify({{ first, second, calls }}));
}})();
"""
    result = subprocess.run(["node", "-e", harness], check=True, text=True, capture_output=True)
    payload = json.loads(result.stdout)
    assert payload["first"]["delivered"] == "local"
    assert payload["second"]["delivered"] == "remote_fallback"
    assert payload["calls"][0]["url"] == "http://127.0.0.1:8787/api/scanner-ingest"
    assert payload["calls"][-1]["url"] == "https://remote.example/ingest"
