"""Manage strategy run-profiles — Phase 6 (config/persistence only).

NO execution, NO orders, NO secrets. Profiles live as YAML under `profiles/`.

  python -m scripts.manage_profiles --list
  python -m scripts.manage_profiles --show PROFILE_ID
  python -m scripts.manage_profiles --validate PROFILE_ID
  python -m scripts.manage_profiles --validate-all
  python -m scripts.manage_profiles --copy SRC_ID NEW_ID [--force]
  python -m scripts.manage_profiles --create-template NEW_ID [--force]

User-facing errors are printed cleanly (no traceback) and return a non-zero code.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main(argv: list[str] | None = None) -> int:
    from src.config.strategy_profiles import (
        default_profiles_dir,
        list_profiles,
        load_profile_file,
        resolve_profile_path,
        save_profile_dict,
        template_profile_dict,
        validate_profile_dict,
    )

    parser = argparse.ArgumentParser(
        description="Manage ZerσSigma strategy run-profiles (config only — no execution)",
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="list all profiles")
    g.add_argument("--show", metavar="PROFILE_ID", help="show one profile (sanitized)")
    g.add_argument("--validate", metavar="PROFILE_ID", help="validate one profile")
    g.add_argument("--validate-all", action="store_true", help="validate every profile")
    g.add_argument("--copy", nargs=2, metavar=("SRC_ID", "NEW_ID"),
                   help="copy SRC_ID to a new profile NEW_ID")
    g.add_argument("--create-template", metavar="NEW_ID", help="write a safe starter profile")
    parser.add_argument("--force", action="store_true",
                        help="allow overwriting an existing file (copy / create-template)")
    parser.add_argument("--profiles-dir", default=None,
                        help="override the profiles directory (default: <repo>/profiles)")
    args = parser.parse_args(argv)

    profiles_dir = Path(args.profiles_dir) if args.profiles_dir else default_profiles_dir()

    # ── --list ──
    if args.list:
        results = list_profiles(profiles_dir)
        if not results:
            print(f"(no profiles found in {profiles_dir})")
            return 0
        hdr = f"{'profile_id':<34} {'strategy_id':<18} {'dte':>3} {'quote':<10} {'selector':<22} {'enabled':<7} valid"
        print(hdr)
        print("-" * len(hdr))
        worst = 0
        for r in results:
            if r.ok and r.profile:
                p = r.profile
                print(f"{p.profile_id:<34} {p.strategy_id:<18} {p.target_dte:>3} "
                      f"{p.quote_provider:<10} {p.daily_selector:<22} {p.enabled!s:<7} ok")
            else:
                name = Path(r.path).stem if r.path else "?"
                print(f"{name:<34} {'-':<18} {'-':>3} {'-':<10} {'-':<22} {'-':<7} INVALID")
                worst = 1
        return worst

    # ── --show ──
    if args.show:
        r = load_profile_file(args.show, profiles_dir)
        if not r.ok:
            print(f"FAIL: {args.show}", file=sys.stderr)
            for e in r.errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        p = r.profile
        print(f"# {p.profile_id}  (path: {r.path})")
        print(f"# profile_hash: {p.profile_hash()}")
        import yaml as _yaml
        print(_yaml.safe_dump(p.to_dict(include_path=False), sort_keys=False).rstrip())
        return 0

    # ── --validate ──
    if args.validate:
        r = load_profile_file(args.validate, profiles_dir)
        if r.ok:
            print(f"PASS: {args.validate} (hash {r.profile.profile_hash()})")
            return 0
        print(f"FAIL: {args.validate}", file=sys.stderr)
        for e in r.errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # ── --validate-all ──
    if args.validate_all:
        results = list_profiles(profiles_dir)
        if not results:
            print(f"(no profiles found in {profiles_dir})")
            return 0
        failed = 0
        for r in results:
            name = (r.profile.profile_id if r.ok and r.profile
                    else (Path(r.path).stem if r.path else "?"))
            if r.ok:
                print(f"PASS: {name}")
            else:
                failed += 1
                print(f"FAIL: {name}")
                for e in r.errors:
                    print(f"  - {e}")
        print(f"\n{len(results) - failed}/{len(results)} profiles valid")
        return 1 if failed else 0

    # ── --copy ──
    if args.copy:
        src_id, new_id = args.copy
        r = load_profile_file(src_id, profiles_dir)
        if not r.ok:
            print(f"FAIL: cannot copy — source {src_id} is invalid:", file=sys.stderr)
            for e in r.errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        from datetime import datetime
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        d = r.profile.to_dict(include_path=False)
        d["profile_id"] = new_id
        d["profile_name"] = f"{r.profile.profile_name} (copy)"
        d["created_at"] = now
        d["updated_at"] = now
        errs = validate_profile_dict(d)
        if errs:
            print("FAIL: copied profile failed validation:", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
            return 1
        dest = resolve_profile_path(new_id, profiles_dir)
        ok, msg = save_profile_dict(d, dest, force=args.force)
        print(msg if ok else f"FAIL: {msg}", file=None if ok else sys.stderr)
        return 0 if ok else 1

    # ── --create-template ──
    if args.create_template:
        new_id = args.create_template
        d = template_profile_dict(new_id)
        dest = resolve_profile_path(new_id, profiles_dir)
        ok, msg = save_profile_dict(d, dest, force=args.force)
        print(msg if ok else f"FAIL: {msg}", file=None if ok else sys.stderr)
        return 0 if ok else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
