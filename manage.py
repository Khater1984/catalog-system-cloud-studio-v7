#!/usr/bin/env python3
"""Administration from the command line (works against whatever DATABASE_URL points to).

  python manage.py init-db
  python manage.py create-user ali --name "Ali" [--owner] [--password ...]
  python manage.py set-password ali
  python manage.py backup backup.zip
  python manage.py restore backup.zip --yes
  python manage.py check [--fix]
"""
import os, sys, argparse, getpass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    p = sub.add_parser("create-user"); p.add_argument("username"); p.add_argument("--name", default=""); p.add_argument("--owner", action="store_true"); p.add_argument("--password")
    p = sub.add_parser("set-password"); p.add_argument("username"); p.add_argument("--password")
    p = sub.add_parser("backup"); p.add_argument("file")
    p = sub.add_parser("restore"); p.add_argument("file"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("check"); p.add_argument("--fix", action="store_true")
    a = ap.parse_args()
    db.init(); db.set_actor("manage.py")
    c = db.conn()
    try:
        if a.cmd == "init-db":
            print("schema version", db.get_version(c))
        elif a.cmd == "create-user":
            import auth
            pw = a.password or getpass.getpass("password: ")
            uid = auth.create_user(c, a.username, pw, a.name or a.username, a.owner); c.commit(); print("created user id", uid)
        elif a.cmd == "set-password":
            from werkzeug.security import generate_password_hash
            pw = a.password or getpass.getpass("new password: ")
            if len(pw) < 8: raise SystemExit("password too short (8+)")
            n = c.execute("UPDATE users SET password_hash=?, failed_count=0, locked_until='' WHERE username=?", (generate_password_hash(pw), a.username.lower())).rowcount
            c.commit(); print("updated" if n else "no such user")
        elif a.cmd == "backup":
            import dataio
            open(a.file, "wb").write(dataio.dump(c)); print("backup written:", a.file)
        elif a.cmd == "restore":
            import dataio
            if not a.yes: raise SystemExit("restore REPLACES the whole database content. Re-run with --yes to confirm.")
            print("restored rows per table:", dataio.restore(c, open(a.file, "rb").read()))
        elif a.cmd == "check":
            import integrity
            issues = integrity.run(c)
            for i in issues: print(i["kind"], i["count"], i["label"], i["sample"])
            if a.fix:
                print("aligned", integrity.fix_family(c), "models to their product's family"); c.commit()
            print("no issues" if not issues else f"{len(issues)} kinds of issues")
    finally:
        c.close()


if __name__ == "__main__":
    main()
