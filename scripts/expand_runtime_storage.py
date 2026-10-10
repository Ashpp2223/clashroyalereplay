"""Expand an offline SDK device, preserving its complete current disk contents.

The qcow2 overlay must be flattened first. Changing config.ini alone can discard
the overlay during the emulator's automatic raw-image resize. Original image,
overlay, encryption keys and configuration are retained for rollback.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):result.update(block)
    return result.hexdigest()


def expand(home:Path,size_gib:int,reuse_copy=False):
    config=json.loads((home/'runtime.json').read_text(encoding='utf-8-sig'))
    if config.get('backend')!='android-sdk':raise ValueError('Only the dedicated SDK device can be expanded')
    avd_home=Path(config['avdHome']).resolve()
    avd=(avd_home/(config['avdName']+'.avd')).resolve()
    if avd.parent!=avd_home:raise ValueError('Device directory is outside the configured AVD home')
    sdk=Path(config['sdkRoot'])
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    def run(exe,*args,allowed=(0,)):
        result=subprocess.run([str(exe),*map(str,args)],cwd=avd,capture_output=True,
            encoding='utf-8',errors='replace',timeout=300,creationflags=flags)
        if result.returncode not in allowed:raise RuntimeError(result.stdout+'\n'+result.stderr)
        return result.stdout
    devices=run(sdk/'platform-tools/adb.exe','devices')
    if any(line.split() and line.split()[0]==config['serial'] for line in devices.splitlines()[1:]):
        raise ValueError('Stop the selected emulator before resizing its disk')
    image_tool=sdk/'emulator/qemu-img.exe'
    source=avd/'userdata-qemu.img';overlay=avd/'userdata-qemu.img.qcow2'
    chain=json.loads(run(image_tool,'info','--output=json','--backing-chain',overlay.name))
    if len(chain)!=2 or chain[0]['format']!='qcow2' or chain[1]['format']!='raw' or chain[0].get('backing-filename')!=source.name:
        raise ValueError('Unsupported disk backing chain; original files were preserved')
    old_size=chain[0]['virtual-size'];new_size=size_gib*1024**3
    if new_size<=old_size:raise ValueError('New size must be larger than the existing virtual disk')
    if shutil.disk_usage(avd).free<new_size+2*1024**3:raise ValueError('Insufficient host disk space for a verified expansion copy')
    candidate=avd/'userdata-expanded.img'
    if candidate.exists() and not reuse_copy:raise ValueError('An expansion copy already exists; inspect it before retrying')
    original_digest=digest(overlay)
    # The SDK's older Windows qemu-img raw creator truncates files above 2 GiB.
    # Python creates the full 64-bit length; -n preserves that precreated file.
    if not candidate.exists():
        with candidate.open('xb') as stream:stream.truncate(old_size)
        run(image_tool,'convert','-n','-f','qcow2','-O','raw',overlay.name,candidate.name)
    run(image_tool,'compare','-f','qcow2','-F','raw',overlay.name,candidate.name)
    with candidate.open('rb') as stream:
        stream.seek(1080)
        plain_ext4=stream.read(2)==b'\x53\xef'
    if plain_ext4:
        run(sdk/'emulator/bin64/e2fsck.exe','-p','-f',candidate.name,allowed=(0,1))
        run(sdk/'emulator/bin64/resize2fs.exe','-f',candidate.name,str(size_gib)+'G')
        run(sdk/'emulator/bin64/e2fsck.exe','-f','-n',candidate.name)
    else:
        # Metadata-encrypted userdata is resized through Android's decrypted
        # mapping after boot. Never feed encrypted bytes to a host ext4 tool.
        with candidate.open('r+b') as stream:stream.truncate(new_size)
    if candidate.stat().st_size!=new_size or digest(overlay)!=original_digest:
        raise ValueError('Expansion size or original image changed; active disk was not replaced')
    backup=avd/('storage-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    if backup.parent.resolve()!=avd or backup.exists():raise ValueError('Invalid backup destination')
    backup.mkdir()
    for name in ('config.ini','encryptionkey.img','encryptionkey.img.qcow2'):
        file=avd/name
        if file.exists():shutil.copy2(file,backup/name)
    source.rename(backup/source.name)
    overlay.rename(backup/overlay.name)
    candidate.rename(source)
    settings=(avd/'config.ini').read_text(encoding='utf-8')
    lines=[line for line in settings.splitlines() if not line.startswith('disk.dataPartition.size=')]
    lines.append('disk.dataPartition.size='+str(size_gib)+'G')
    (avd/'config.ini').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    receipt={'schema':'cr-device-expansion.v1','previousBytes':old_size,'expandedBytes':new_size,
             'originalOverlaySha256':original_digest,'guestBytesComparedBeforeResize':True,
             'ext4CheckedAfterResize':plain_ext4,'filesystemResizePending':not plain_ext4,'backup':str(backup)}
    (backup/'expansion.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--size-gib',type=int,choices=range(16,65),default=20)
    parser.add_argument('--reuse-copy',action='store_true',help='Recheck and reuse a previously flattened expansion copy')
    args=parser.parse_args()
    print(json.dumps(expand(args.home,args.size_gib,args.reuse_copy)))
