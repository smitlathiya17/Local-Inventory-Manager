"""Management commands: python manage.py <command>"""
import sys


def cmd_seed_demo():
    """Seed a demo data folder with realistic sample data."""
    print("seed-demo: not yet implemented (milestone 9)")


def cmd_download_vendor():
    """Download vendored JS/font assets into app/static/vendor/."""
    import urllib.request
    from pathlib import Path

    vendor = Path(__file__).parent / 'app' / 'static' / 'vendor'
    vendor.mkdir(parents=True, exist_ok=True)

    assets = {
        'htmx.min.js': 'https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js',
        'JsBarcode.all.min.js': (
            'https://cdn.jsdelivr.net/npm/jsbarcode@3.11.6/dist/JsBarcode.all.min.js'
        ),
    }

    for filename, url in assets.items():
        dest = vendor / filename
        if dest.exists():
            print(f"  {filename}: already present, skipping")
            continue
        print(f"  Downloading {filename}…", end=' ', flush=True)
        urllib.request.urlretrieve(url, dest)
        print("done")

    print("Vendor assets ready.")


COMMANDS = {
    'seed-demo': cmd_seed_demo,
    'download-vendor': cmd_download_vendor,
}

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    fn = COMMANDS.get(cmd)
    if fn is None:
        print(f"Usage: python manage.py [{' | '.join(COMMANDS)}]")
        sys.exit(1)
    fn()
