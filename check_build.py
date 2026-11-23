"""Verify a fresh Windows artifact and its actual PE version resource."""
from pathlib import Path
import time
import pefile
from kandu import __version__


def check(exe=Path('dist/Kandu/Kandu.exe')):
    if not exe.is_file():
        raise RuntimeError('Executable missing')
    if time.time() - exe.stat().st_mtime > 300:
        raise RuntimeError('Executable was not updated by this build')
    expected=tuple(map(int,__version__.split('.'))) + (0,)
    with pefile.PE(str(exe),fast_load=True) as pe:
        pe.parse_data_directories([pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        info=pe.VS_FIXEDFILEINFO[0]
        actual=(info.FileVersionMS>>16,info.FileVersionMS&65535,info.FileVersionLS>>16,info.FileVersionLS&65535)
        if actual != expected:
            raise RuntimeError(f'PE version mismatch: {actual} != {expected}')
    print(f'OK: {exe} v{__version__}, PE version verified')


if __name__=='__main__':
    check()
