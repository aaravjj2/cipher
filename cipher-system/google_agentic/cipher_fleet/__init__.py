"""Google ADK entry package for Cipher's read-only research fleet.

The package stays importable without the optional ADK dependency so Cipher's
core safety tests can exercise the transport and policy boundary in the minimal
runtime. ADK loads ``cipher_fleet.agent`` directly when the isolated fleet
environment is used.
"""

__all__: list[str] = []
