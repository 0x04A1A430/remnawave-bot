import os
import shutil
import sys
from pathlib import Path

APP_UID = 1000
APP_GID = 1000
APP_HOME = '/home/app'

os.environ['HOME'] = APP_HOME
os.environ['USER'] = 'app'

dirs = [
    '/app/logs/current', '/app/logs/archive',
    '/app/data', '/app/uploads/images', '/app/uploads/videos',
    '/app/uploads/thumbnails', '/app/locales',
]

for d in dirs:
    Path(d).mkdir(parents=True, exist_ok=True)

runtime_dirs = ['/app/logs', '/app/data', '/app/uploads', '/app/locales']
for dir_path in runtime_dirs:
    p = Path(dir_path)
    if not p.exists():
        continue
    try:
        shutil.chown(p, user='app', group='app')
        for root, dirs, files in os.walk(dir_path):
            for name in dirs:
                try:
                    os.chown(os.path.join(root, name), APP_UID, APP_GID, follow_symlinks=False)
                except PermissionError:
                    pass
            for name in files:
                try:
                    os.chown(os.path.join(root, name), APP_UID, APP_GID, follow_symlinks=False)
                except PermissionError:
                    pass
    except PermissionError:
        try:
            os.chmod(p, 0o777)
            for root, dirs, files in os.walk(dir_path):
                for name in dirs:
                    try:
                        os.chmod(os.path.join(root, name), 0o777)
                    except PermissionError:
                        pass
                for name in files:
                    try:
                        os.chmod(os.path.join(root, name), 0o777)
                    except PermissionError:
                        pass
        except PermissionError:
            pass

os.setgid(APP_GID)
os.setuid(APP_UID)

os.execvp(sys.argv[1], sys.argv[1:])
