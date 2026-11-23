"""Package only the built application and redistribution notices."""
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kandu import __version__


def package():
    folder=ROOT/'dist/Kandu'
    if not (folder/'Kandu.exe').is_file():
        raise RuntimeError('Build the Windows executable first')
    for name in ('LICENSE','THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT/name,folder/name)
    output=ROOT/f'dist/Kandu-v{__version__}-win64.zip'
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                archive.write(path,path.relative_to(folder.parent).as_posix())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Release archive failed integrity check')
    print(f'Packaged: {output} ({output.stat().st_size/1024**2:.1f} MiB)')
    return output


if __name__=='__main__':
    package()
