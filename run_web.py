"""Run the LAN web edition without changing the desktop data directory."""
import argparse
import os
from pathlib import Path
import socket


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8766)
    base = Path(os.environ.get('LOCALAPPDATA', Path.home()))
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('KANDU_WEB_DATA_DIR') or base / 'KanduWeb'))
    parser.add_argument('--import-desktop', type=Path, help='One-time import; original book files stay in place')
    parser.add_argument('--allowed-host', action='append', default=[], help='Additional hostname, e.g. read.138272.xyz')
    parser.add_argument('--secure-cookie', action='store_true', help='Enable for HTTPS behind a trusted tunnel')
    args = parser.parse_args()
    desktop = Path(os.environ.get('KANDU_DATA_DIR') or base / 'KanduDesktop').resolve()
    if args.data_dir.resolve() == desktop:
        parser.error('Use a separate Web data directory, not KanduDesktop')
    if args.import_desktop:
        from webapp.bootstrap import import_desktop
        print('Imported publications:', import_desktop(args.import_desktop, args.data_dir), flush=True)
    os.environ['KANDU_DATA_DIR'] = str(args.data_dir.resolve())
    from webapp.server import create_app
    from waitress import serve
    app = create_app(allowed_hosts=args.allowed_host, secure_cookie=args.secure_cookie)
    print(f'Kandu Web: http://127.0.0.1:{args.port}', flush=True)
    for address in sorted({r[4][0] for r in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}):
        print(f'LAN address: http://{address}:{args.port}', flush=True)
    print('Access code file:', args.data_dir.resolve() / 'access-code.txt', flush=True)
    serve(app, host=args.host, port=args.port, threads=12, max_request_body_size=160 * 1024**2,
          channel_timeout=120, ident='Kandu Web')


if __name__ == '__main__':
    main()
