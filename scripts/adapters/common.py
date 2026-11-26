"""Shared measurement for every adapter.

An adapter turns one agent session into a Trace (a flat list of timed
events). `compute` then measures it the same way for every agent, so a
"round", a "turn" and "agent_min" mean the same thing whichever tool did the
work. Definitions are in the claude_code module docstring and in
references/calibration.md.
"""

from datetime import datetime

WAIT_THRESHOLD_S = 120
IDLE_THRESHOLD_S = 600


class Trace:
    """One session, normalised.

    events   [(ts, "human" | "agent")]: every main-chain event with a time
    prompts  [(ts, text)]: what the person typed, one per human event
    rounds   [ts]: one per model response that called at least one tool
    waits    [(start, end)]: long shell/monitor tool calls
    models   [(ts, model id)]: one per model response
    usage    [(ts, tokens, usd)]: per model response, when the log has it
    """

    def __init__(self, session_id, source):
        self.session_id = session_id
        self.source = source
        self.events, self.prompts, self.rounds = [], [], []
        self.waits, self.models, self.usage = [], [], []
        self.title = self.cwd = self.branch = self.provider = None
        self.prs = []
        self.cost = None       # session totals when the log only has those
        self.extra = {}


def window(humans, round_ts, since, turns):
    """Pick the part of a session that belongs to one request.

    Returns (t_from, t_to, starts_at_prompt, first_is_request)."""
    inf = float("inf")
    before = [k for k, h in enumerate(humans) if h <= since]
    if not before:
        # The estimate was made before this session began: its first turn.
        k, t_from, at_prompt, first = 0, humans[0], True, True
    else:
        k = before[-1]
        end = humans[k + 1] if k + 1 < len(humans) else inf
        worked = any(since < t < end for t in round_ts)
        if worked or k + 1 >= len(humans):
            t_from, at_prompt, first = since, False, False
        else:
            # Nothing but the estimate itself in that turn: the work is the
            # next turn, and its prompt (the go-ahead) is a human turn.
            k += 1
            t_from, at_prompt, first = humans[k], True, False
    last = k + max(0, turns)
    t_to = humans[last + 1] if last + 1 < len(humans) else inf
    return t_from, t_to, at_prompt, first


def compute(trace, since=None, turns=0):
    """Measure a Trace, or one request in it when `since` is given."""
    humans = sorted(ts for ts, who in trace.events if who == "human")
    if not humans:
        return None
    events = sorted(trace.events)
    if since is None:
        scope = "session"
        t_from, t_to, at_prompt, first_is_request = events[0][0], float("inf"), True, True
    else:
        scope = "request"
        t_from, t_to, at_prompt, first_is_request = window(humans, trace.rounds, since, turns)
    inside = [(ts, who) for ts, who in events if t_from <= ts < t_to and (at_prompt or ts > t_from)]
    if not inside:
        return None

    agent_s = wait_s = idle_s = gap_s = 0.0
    in_turn = not at_prompt
    prev = None if at_prompt else t_from
    for ts, who in inside:
        if who == "human":
            if in_turn and prev is not None:
                gap_s += max(0.0, ts - prev)
            in_turn, prev = True, ts
            continue
        if not in_turn or prev is None:
            continue
        step = max(0.0, ts - prev)
        covered = sum(max(0.0, min(ts, b) - max(prev, a)) for a, b in trace.waits)
        covered = min(step, covered)
        wait_s += covered
        rest = step - covered
        if rest > IDLE_THRESHOLD_S:
            idle_s += rest
        else:
            agent_s += rest
        prev = ts

    def within(ts):
        return t_from <= ts < t_to and (at_prompt or ts > t_from)

    prompts_inside = sum(1 for h in humans if t_from <= h < t_to)
    models = {}
    for ts, name in trace.models:
        if name and t_from <= ts < t_to:
            models[name] = models.get(name, 0) + 1
    if not models:
        for _ts, name in trace.models:
            if name:
                models[name] = models.get(name, 0) + 1
    first_prompt = next((text for _ts, text in sorted(trace.prompts) if text and not text.startswith("/")), "")
    title = trace.title or (first_prompt.splitlines()[0][:80] if first_prompt else trace.session_id)
    result = {
        "session_id": trace.session_id,
        "source": trace.source,
        "scope": scope,
        "title": title,
        "start": datetime.fromtimestamp(t_from).isoformat(timespec="minutes"),
        "cwd": trace.cwd,
        "branch": trace.branch,
        "model": max(models, key=models.get) if models else None,
        "provider": trace.provider,
        "prs": trace.prs,
        "rounds": sum(1 for t in trace.rounds if within(t)),
        "agent_min": round(agent_s / 60, 1),
        "wait_min": round(wait_s / 60, 1),
        "idle_min": round(idle_s / 60, 1),
        "human_turns": max(0, prompts_inside - (1 if first_is_request else 0)),
        "human_gap_min": round(gap_s / 60, 1),
        "wall_min": round((inside[-1][0] - t_from) / 60, 1),
        "prompts": len(humans),
        "other_prompts": len(humans) - prompts_inside,
    }
    used = [(tok, usd) for ts, tok, usd in trace.usage if within(ts)]
    if used:
        result["tokens"] = sum(tok or 0 for tok, _ in used)
        if any(usd is not None for _, usd in used):
            result["cost_usd"] = round(sum(usd or 0 for _, usd in used), 4)
    if trace.cost:
        for k, v in trace.cost.items():
            result.setdefault(k, v)
    result.update(trace.extra)
    result["first_prompt"] = first_prompt
    return result


def requests(trace):
    """Each human prompt with its text, tool rounds and tokens until the next
    prompt; used by replay.py."""
    prompts = sorted(trace.prompts)
    out = []
    for k, (ts, text) in enumerate(prompts):
        end = prompts[k + 1][0] if k + 1 < len(prompts) else float("inf")
        out.append({"ts": ts, "text": text or "", "cwd": trace.cwd,
                    "rounds": sum(1 for t in trace.rounds if ts < t < end),
                    "work_tokens": sum(tok or 0 for t, tok, _ in trace.usage if ts < t < end),
                    "last_event": max([t for t, _w in trace.events if ts <= t < end] or [ts]),
                    "last": k + 1 == len(prompts),
                    "context": prompts[k - 1][1] if k else ""})
    return out
