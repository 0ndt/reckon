"""Rebuild the adapter fixtures from real sessions on a maintainer's machine.

Usage: python3 build.py --codex <rollout.jsonl> --opencode-db <opencode.db> --opencode-session <ses_id>

Every string is replaced by "x"s (length kept, up to 200) except a short list of structural keys (type,
role, tool name, status, model, ids, timestamps, cwd), so the fixtures keep
the real timing and record shapes but none of the conversation, code or
output. Large records that only carry instructions are dropped.
"""
import argparse, json, os, shutil, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP = {"type", "role", "name", "status", "model", "model_provider", "providerID", "id", "call_id", "turn_id",
        "root_turn_id", "session_id", "thread_id", "response_id", "timestamp", "cli_version", "originator",
        "source", "phase", "outcome", "finish", "rawFinish", "agent", "variant", "cwd", "limit_id"}
DROP_CODEX = {"world_state"}
DROP_PAYLOAD_TYPES = {"additional_tools"}


def scrub(value, key=None):
    if isinstance(value, dict):
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, key) for v in value]
    if isinstance(value, str):
        if key in ("providerID", "model_provider"):
            return "example-gateway"
        return value if key in KEEP else "x" * min(len(value), 200)   # keep rough length only
    return value


def codex(src):
    out_dir = os.path.join(HERE, "codex", "sessions", "2026", "11", "26")
    shutil.rmtree(os.path.join(HERE, "codex"), ignore_errors=True)
    os.makedirs(out_dir)
    with open(src) as fh, open(os.path.join(out_dir, os.path.basename(src)), "w") as out:
        for line in fh:
            rec = json.loads(line)
            if rec.get("type") in DROP_CODEX or (rec.get("payload") or {}).get("type") in DROP_PAYLOAD_TYPES:
                continue
            p = rec.get("payload") or {}
            if p.get("cwd"):
                p["cwd"] = "/tmp/fixture-project"
            rec["payload"] = scrub(p)
            out.write(json.dumps(rec) + "\n")


def opencode(db, sid):
    dst = os.path.join(HERE, "opencode.db")
    if os.path.exists(dst):
        os.remove(dst)
    src = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    for (ddl,) in src.execute("select sql from sqlite_master where type='table' and name in ('session_v2','session_message')"):
        out.execute(ddl)
    cols = [r[1] for r in src.execute("pragma table_info(session_v2)")]
    row = dict(zip(cols, src.execute("select * from session_v2 where id=?", (sid,)).fetchone()))
    row.update(model=json.dumps(scrub(json.loads(row["model"]))) if row.get("model") else None,
               directory="/tmp/fixture-project", path=None, title="x", slug="x", summary_diffs=None,
               metadata=None, permission=None, revert=None, share_url=None)
    out.execute(f"insert into session_v2 ({','.join(row)}) values ({','.join('?' * len(row))})", list(row.values()))
    for r in src.execute("select id, session_id, type, seq, time_created, time_updated, data "
                         "from session_message where session_id=? order by seq", (sid,)):
        data = scrub(json.loads(r[6]))
        out.execute("insert into session_message values (?,?,?,?,?,?,?)", (*r[:6], json.dumps(data)))
    out.commit()
    out.execute("vacuum")
    out.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex")
    ap.add_argument("--opencode-db")
    ap.add_argument("--opencode-session")
    a = ap.parse_args()
    if a.codex:
        codex(a.codex)
    if a.opencode_db:
        opencode(a.opencode_db, a.opencode_session)
