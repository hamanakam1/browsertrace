#!/usr/bin/env python3
"""Standalone backend for the panther-intent-capture extension.

Stores recordings under ~/.panther-intent-capture/ and runs local inference
via Ollama (http://127.0.0.1:11434/v1). Defaults to :7890.
"""

from __future__ import annotations

import os
import sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# allow running both as `python3 backend/server.py` and `python3 -m backend.server`
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.routes import handle_request  # noqa: E402

PORT = int(os.environ.get("EXTENSION_BACKEND_PORT", "7890"))
HOST = "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        handle_request(self)

    def do_POST(self):
        handle_request(self)

    def do_DELETE(self):
        handle_request(self)

    def log_message(self, fmt, *args):  # quiet default
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("""
 ______     ______     ______     __     ______     __         ______     __  __     ______     ______     ______  
/\  __ \   /\  == \   /\  __ \   /\ \   /\  ___\   /\ \       /\  == \   /\ \_\ \   /\  == \   /\  __ \   /\  __ \ 
\ \  __ \  \ \  __<   \ \  __ \  \ \ \  \ \___  \  \ \ \____  \ \  __<   \ \____ \  \ \  __<   \ \ \/\ \  \ \ \/\ \
 \ \_\ \_\  \ \_\ \_\  \ \_\ \_\  \ \_\  \/\_____\  \ \_______\ \ \_\ \_\  \/\_____\  \ \_\ \_\  \ \_____\  \ \_____\
  \/_/\/_/   \/_/\/_/   \/_/\/_/   \/_/   \/_____/   \/_______/  \/_/\/_/   \/_____/   \/_/\/_/   \/_____/   \/_____/

   panther-intent-capture backend  v1.0.0   listening on http://{HOST}:{PORT}
    local-first  •  consent-gated  •  secret-redacted  •  user-deletable
    """.format(HOST=HOST, PORT=PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
