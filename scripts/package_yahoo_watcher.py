"""Build the downloadable unpacked Chrome extension from its checked-in source."""
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root=Path(__file__).resolve().parents[1]
source=root/'extensions/yahoo-draft-watcher'
target=root/'survivor/web/static/yahoo-draft-watcher.zip'
with ZipFile(target,'w',ZIP_DEFLATED) as archive:
    for path in sorted(source.iterdir()):
        if path.is_file():
            archive.write(path,path.name)
version=json.loads((source/'manifest.json').read_text(encoding='utf-8'))['version']
print(f'Packaged Yahoo watcher {version}')
