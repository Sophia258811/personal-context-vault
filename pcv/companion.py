"""Loopback receiver for explicitly enabled browser companion sessions."""
import argparse
import hmac
import json
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .observation import structured_fields


def create_server(inbox, token, port=8765, on_saved=None):
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # URLs and page text must not enter HTTP logs.

        def reply(self, status):
            self.send_response(status)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_POST(self):
            if self.path != '/events':
                return self.reply(404)
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                return self.reply(401)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 100000:
                    return self.reply(413)
                event = json.loads(self.rfile.read(length))
                if not isinstance(event, dict) or not isinstance(event.get('url'), str):
                    raise ValueError('event')
                url = urlsplit(event['url'])
                if url.scheme not in ('http', 'https') or not url.hostname:
                    raise ValueError('URL')
                text = event['text']
                if not isinstance(text, str) or len(text) > 20000:
                    raise ValueError('text')
                now = datetime.now(timezone.utc).isoformat()
                record = {
                    'id': secrets.token_hex(16), 'platform': 'browser',
                    'event_type': 'page_observed', 'actor_role': 'third_party',
                    'url': f'{url.scheme}://{url.netloc.split("@")[-1]}{url.path}',
                    'title': str(event.get('title', ''))[:300],
                    'text': text, 'created_at': now,
                    'interpretation': 'Exposure is not endorsement or verified reading.',
                }
                record.update(structured_fields(event, url.hostname))
                with lock:
                    target = inbox / (record['id'] + '.json')
                    temporary = target.with_suffix('.tmp')
                    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
                    temporary.replace(target)
                if on_saved:
                    on_saved(target)
            except (ValueError, KeyError, TypeError):
                return self.reply(400)
            except OSError:
                return self.reply(500)
            self.reply(204)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inbox', type=Path, default=Path('.pcv/browser-inbox'))
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    server = create_server(args.inbox, token, args.port)
    server.timeout = 1
    print(f'Local receiver: http://127.0.0.1:{args.port}/events', flush=True)
    print(f'Pairing token (this run only): {token}', flush=True)
    print(f'Inbox: {args.inbox.resolve()}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
