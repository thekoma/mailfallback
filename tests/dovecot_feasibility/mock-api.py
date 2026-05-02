"""Minimal mock of the MFB internal Dovecot userdb API.

Returns a fixed JSON response so we can validate that:
  1. Dovecot Lua userdb can make HTTP requests
  2. Lua can parse JSON
  3. Dynamic namespace extra-fields are accepted by Dovecot

Response format uses Dovecot 2.4 field names:
  - mail_driver (not "maildir:/..." location string)
  - mail_path (absolute path to maildir root)
  - mailbox_list_layout (e.g. "fs")
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

USERDB_RESPONSE = {
    "uid": 1000,
    "gid": 1000,
    "home": "/data/mailboxes/.dovecot-home/testuser",
    "namespaces": [
        {
            "name": "acc_1",
            "prefix": "",
            "mail_driver": "maildir",
            "mail_path": "/data/mailboxes/test-uuid-1",
            "mailbox_list_layout": "fs",
            "inbox": True,
        },
        {
            "name": "acc_2",
            "prefix": "Second Account/",
            "mail_driver": "maildir",
            "mail_path": "/data/mailboxes/test-uuid-2",
            "mailbox_list_layout": "fs",
            "inbox": False,
        },
    ],
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Expect: /api/internal/dovecot/userdb/{username}
        parts = self.path.rstrip("/").split("/")
        if (
            len(parts) >= 6
            and parts[1] == "api"
            and parts[2] == "internal"
            and parts[3] == "dovecot"
            and parts[4] == "userdb"
        ):
            username = parts[5]
            if username == "testuser":
                body = json.dumps(USERDB_RESPONSE).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        # Unknown user or bad path
        body = json.dumps({"error": "user not found"}).encode()
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[mock-api] {fmt % args}", flush=True)  # noqa: T201


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    print("[mock-api] Listening on :8080", flush=True)  # noqa: T201
    server.serve_forever()
