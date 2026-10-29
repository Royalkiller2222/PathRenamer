"""How the Docker image starts PathRenamer (also for "docker exec ... pathrenamer").

Started as root, it runs PathRenamer as the user PUID and group PGID, so the
files it renames belong to you rather than to root, and makes the settings
folder (/config) theirs. UMASK (default 022) sets the permissions of new
files and folders, e.g. 002 to let your group change them too.
"""
import os
import sys

CONFIG = os.environ.get("PATHRENAMER_CONFIG_DIR") or "/config"


def _own(path, uid, gid):
    try:
        if os.stat(path).st_uid != uid or os.stat(path).st_gid != gid:
            os.chown(path, uid, gid)
    except OSError:
        pass


def drop_privileges(env=os.environ):
    uid, gid = env.get("PUID", "").strip(), env.get("PGID", "").strip()
    if os.geteuid() != 0 or not uid.isdigit() or not gid.isdigit():
        return
    uid, gid = int(uid), int(gid)
    os.makedirs(CONFIG, exist_ok=True)
    _own(CONFIG, uid, gid)
    for root, dirs, files in os.walk(CONFIG):
        for name in dirs + files:
            _own(os.path.join(root, name), uid, gid)
    os.setgroups([gid])
    os.setgid(gid)
    os.setuid(uid)
    os.environ["HOME"] = CONFIG


def main():
    umask = os.environ.get("UMASK", "").strip()
    if umask:
        try:
            os.umask(int(umask, 8))
        except ValueError:
            print(f"UMASK {umask!r} isn't valid (for example 002 or 022); keeping the default.", flush=True)
    drop_privileges()
    cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli.py")
    os.execv(sys.executable, [sys.executable, cli, *sys.argv[1:]])


if __name__ == "__main__":
    main()
