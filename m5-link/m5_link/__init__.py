"""m5-link: the desktop side of the M5Stack CoreS3 body.

The body (m5-firmware) dials in over WebSocket. m5-link turns what it hears
into text (ears), asks the head (Claude, with this repository's memory) for a
reply, turns the reply into speech (mouth) and sends it back. It also serves a
small web page for watching and typing, and the `body` MCP server through
which heartbeat sessions can speak, change the face and look.
"""

__version__ = "0.1.0"
