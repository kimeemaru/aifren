"""Extract pinned Microsoft redistributable DLLs into disposable runtime staging.

Build-time only: expand.exe on Windows, cabextract on Linux. Never execute the
installer, modify the host, or copy system DLLs. Retain the exact vendor license.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

URL = 'https://download.visualstudio.microsoft.com/download/pr/bd1c8d9d-ba95-4eee-bc6e-df1fcc876373/CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe'
SHA256 = 'cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b'
FILES = ('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
         'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll',
         'vcruntime140.dll', 'vcruntime140_1.dll', 'vcomp140.dll')


def expand(cab, output):
    output.mkdir()
    command = (['expand.exe', '-F:*', str(cab), str(output)] if sys.platform == 'win32'
               else ['cabextract', '-q', '-d', str(output), str(cab)])
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)


def prepare(installer, runtime, materials):
    content = installer.read_bytes()
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise ValueError('MSVC redistributable differs from reviewed input')
    for path in (runtime, materials):
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError('Runtime staging must not follow links')
    with tempfile.TemporaryDirectory(prefix='aifren-msvc-') as temp:
        root = Path(temp)
        # Both CAB offsets and installer identity belong to this pinned build.
        for name, offset in (('ux',479744),('payloads',686152)):
            assert content[offset:offset+4] == b'MSCF'
            size=struct.unpack_from('<I', content, offset+8)[0]
            cab=root/(name+'.cab');cab.write_bytes(content[offset:offset+size]);expand(cab,root/name)
        expand(root/'payloads/a12',root/'minimum')
        runtime.mkdir(parents=True,exist_ok=True);materials.mkdir(parents=True,exist_ok=True)
        records=[]
        for name in FILES:
            source=root/'minimum'/(name+'_amd64');target=runtime/name
            if target.is_symlink():raise ValueError('Runtime target must not be a link')
            shutil.copyfile(source,target)
            records.append({'file':name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
        shutil.copyfile(root/'ux/u4',materials/'LICENSE.rtf')
        (materials/'provenance.json').write_text(json.dumps({'version':'14.44.35211',
            'url':URL,'sha256':SHA256,'files':records,'installation':'private application-local DLLs; installer never executed'},indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('installer','runtime','materials'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();prepare(a.installer,a.runtime,a.materials)
