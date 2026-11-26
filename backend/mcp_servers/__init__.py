"""The MCP servers that hold the provider credentials (docs/PHASE6.md).

They are separate processes from the agent: the agent reaches them with a short-lived signed token
and never holds a provider's credentials. Nothing here imports the agent (``app``).
"""
