# Attach Cipher Research Copilot to Codex

This project includes a project-scoped Codex MCP configuration at `../.codex/config.toml`.

Open `C:\Aarav\cipher-system` as a trusted project in Codex, then restart Codex (or add the same server from Settings > MCP servers):

- Name: `cipher_research`
- Transport: `STDIO`
- Command: `python`
- Arguments: `run.py`
- Working directory: `C:\Aarav\cipher-system\mcp-server`

After restart, type `/mcp` to confirm `cipher_research` is connected. The server has no external packages and uses only its local SQLite database and Markdown exports.

The MCP supplies the repeatable workflow and research memory. Browser and Computer/Chrome tools remain features of the AI host, which should follow `BROWSER_HANDOFF.md` when navigating Cipher.
