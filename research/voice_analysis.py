"""Voice mining over the TextTorrent conversation export. Analysis only.

Reads texttorrent_conversations_last30d.csv (repo root, gitignored) and
answers: what openers were sent, which ones actually drew interested
replies, how reps write by hand, how prospects write, and which historical
messages carry compliance problems. Then validates a set of new draft
openers/follow-ups against SMS constraints (ASCII, GSM-7, single segment).

DATA HANDLING: everything containing message content, names, or numbers is
written under research/output/voice/ (gitignored) — never the repo, never
stdout. stdout carries aggregate numbers only.

    uv run python research/voice_analysis.py [--csv PATH] [--sample 100]
    uv run python research/voice_analysis.py --drafts-only
"""

import argparse
import json
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from conversation_harness import sms_encoding

from classifier.buckets import bucket_intent
from guardrails import evaluate

OUT_DIR = Path(__file__).resolve().parent / "output" / "voice"
INTEREST_BUCKETS = {"Interested", "Amount_Given", "Call_Request"}
TEMPLATE_SKELETON_MIN = 10  # a skeleton seen this often is a blast, not hand-typed

OPT_OUT_RE = re.compile(r"(reply|text|txt)\s*\"?'?\s*stop|opt[ -]?out|stop\s*2\s*(quit|end)", re.I)
RATE_RE = re.compile(
    r"\b\d{1,2}(\.\d+)?\s?%|\binterest rate\b|\bapr\b|\bas low as\b|\brates?\s+(start|from)\b", re.I
)
APPROVAL_RE = re.compile(r"\b(pre[- ]?approv\w*|approv\w*|guarantee\w*)\b", re.I)
QUALIFY_RE = re.compile(r"\b(pre[- ]?qualifi\w*|qualifi(?:ed|es)|qualify)\b", re.I)
URGENCY_RE = re.compile(
    r"act now|today only|expires?\b|last chance|final notice|limited time|hurry"
    r"|don'?t miss|while (it|they) last|only \d+ (spots?|days?)|next 24 hours",
    re.I,
)
CONTRACTION_RE = re.compile(
    r"\b(i'?m|don'?t|can'?t|won'?t|it'?s|that'?s|what'?s|you'?re|we'?re|i'?ll|we'?ll"
    r"|i'?ve|didn'?t|isn'?t|wasn'?t|lets|let'?s|ive|im|dont|cant)\b",
    re.I,
)
GREETING_RE = re.compile(r"^(hi|hey|hello|good\s+(morning|afternoon|evening))\b", re.I)
SIGNOFF_RE = re.compile(r"(thanks|thank you|best|regards|talk soon|appreciate)\W*$", re.I)

# ---------------------------------------------------------------------------
# Drafts (part 6). Authored from the findings; the script only VALIDATES them.
# Constraints: ASCII only, GSM-7 basic charset only (no {}[]~^|\ either — the
# extension chars cost double), 1 segment including the opt-out, named human
# sender, exactly one concrete question, no rate/approval/urgency language.
# ---------------------------------------------------------------------------
DRAFTS: list[dict[str, str]] = [
    {
        "name": "opener-1 (direct amount question)",
        "kind": "opener",
        "body": (
            "Hi {first_name}, this is Maria with FundMate. Quick question - are you "
            "looking for working capital for {company} right now? Reply STOP to opt out."
        ),
        "grounded_in": "F2/F3: short, named, one question - feature-list pitches ranked bottom",
    },
    {
        "name": "opener-2 (amount-first)",
        "kind": "opener",
        "body": (
            "Hi {first_name}, Maria with FundMate here. If extra capital were on the "
            "table for {company}, how much would actually help? Reply STOP to opt out."
        ),
        "grounded_in": "F1: every top opener closes with a how-much ask; Amount_Given dominates",
    },
    {
        "name": "opener-3 (timing question)",
        "kind": "opener",
        "body": (
            "Hi {first_name}, this is Maria at FundMate. We fund businesses like "
            "{company}. Is growth capital on your radar this quarter? Reply STOP to opt out."
        ),
        "grounded_in": "F3/F5: named sender; plain register matching median-24-char replies",
    },
    {
        "name": "opener-4 (owner-check)",
        "kind": "opener",
        "body": (
            "Hi {first_name}, Maria with FundMate. Are you still the right person to "
            "talk to about financing for {company}? Reply STOP to opt out."
        ),
        "grounded_in": "F7: wrong number is the 4th most common reply (247x); qualify early",
    },
    {
        "name": "opener-5 (revenue-fit)",
        "kind": "opener",
        "body": (
            "Hi {first_name}, this is Maria from FundMate. Does {company} have monthly "
            "revenue over 20k? If so I can lay out options. Reply STOP to opt out."
        ),
        "grounded_in": "F1/F2: a concrete-number question pulls a number back, not a bare yes",
    },
    {
        "name": "follow-up-1 (after an amount)",
        "kind": "follow-up",
        "body": (
            "Got it, thanks. Two quick ones so I point you right: how long in "
            "business, and roughly what monthly revenue?"
        ),
        "grounded_in": "F4: rep voice is ~74 chars, 1-2 sentences, ends on the ask",
    },
    {
        "name": "follow-up-2 (after interest)",
        "kind": "follow-up",
        "body": (
            "Great. What would the capital go toward - equipment, payroll, or "
            "expansion? That changes which option fits."
        ),
        "grounded_in": "F4/F5: short casual register, concrete either/or, matches the thread",
    },
    {
        "name": "follow-up-3 (no reply nudge)",
        "kind": "follow-up",
        "body": (
            "Hi {first_name}, Maria again with FundMate. Still worth a look at "
            "funding for {company}, or should I close this out?"
        ),
        "grounded_in": "F4/F6: the observed 'no pressure' softener, minus the urgency history",
    },
]

MERGE_PREVIEW = {"{first_name}": "Christopher", "{company}": "Blackwater Logistics"}


def classify_reply(text: str) -> str:
    return bucket_intent(text, evaluate(text, [], "sms"))[0]


def mask_names(text: str, *names: str | None) -> str:
    for full in names:
        for token in (full or "").replace(",", " ").split():
            if len(token) >= 3:
                text = re.sub(rf"(?i)\b{re.escape(token)}\b", "<name>", text)
    return text


def skeleton(text: str) -> str:
    t = " ".join(text.split()).lower()
    t = re.sub(r"https?://\S+", "<url>", t)
    t = re.sub(r"\d+", "#", t)
    return t


def sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?]+", text) if s.strip()])


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def load(csv_path: str) -> list[dict[str, Any]]:
    frame = (
        pl.read_csv(csv_path, infer_schema_length=5000)
        .with_columns(pl.col("message").fill_null(""))
        .sort(["chat_id", "created_at", "message_id"])
    )
    return frame.to_dicts()


def analyze(rows: list[dict[str, Any]], sample_size: int) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chats: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        chats[row["chat_id"]].append(row)

    skeleton_totals: Counter[str] = Counter()
    for row in rows:
        if row["direction"] == "outbound":
            skeleton_totals[skeleton(mask_names(row["message"], row["contact_name"]))] += 1

    # -- 1+2: openers, clustered, with reply/interest linkage ----------------
    variants: dict[str, dict[str, Any]] = {}
    for _chat_id, messages in chats.items():
        if messages[0]["direction"] != "outbound":
            continue  # they texted first: not an outreach opener
        opener = messages[0]
        masked = mask_names(opener["message"], opener["contact_name"], opener["rep_name"])
        key = skeleton(masked)
        bucket = variants.setdefault(
            key, {"sends": 0, "replies": 0, "interest": 0, "example": masked, "reply_buckets": []}
        )
        bucket["sends"] += 1
        inbound_after = [m for m in messages[1:] if m["direction"] == "inbound"]
        if inbound_after:
            bucket["replies"] += 1
            reply_intent = classify_reply(inbound_after[0]["message"])
            bucket["reply_buckets"].append(reply_intent)
            if reply_intent in INTEREST_BUCKETS:
                bucket["interest"] += 1

    ranked = sorted(
        (v for v in variants.values() if v["sends"] >= 30),
        key=lambda v: v["interest"] / v["sends"],
        reverse=True,
    )
    with (OUT_DIR / "openers_ranked.txt").open("w", encoding="utf-8") as out:
        for v in ranked:
            out.write(
                f"sends={v['sends']} reply%={pct(v['replies'], v['sends'])} "
                f"interest%of_sends={pct(v['interest'], v['sends'])} "
                f"interest%of_replies={pct(v['interest'], v['replies'])}\n"
                f"  buckets={dict(Counter(v['reply_buckets']).most_common())}\n"
                f"  {v['example']!r}\n\n"
            )
    opener_chats = sum(v["sends"] for v in variants.values())
    print(f"[1] opener chats: {opener_chats}, distinct variants: {len(variants)}")
    print(f"    variants with >=30 sends: {len(ranked)} (ranked file written)")
    print(
        "[2] reply linkage: chat_id + created_at/message_id ordering; "
        f"overall reply rate {pct(sum(v['replies'] for v in variants.values()), opener_chats)}%"
    )

    # -- 3: hand-typed outbound voice ---------------------------------------
    hand_typed: list[str] = []
    for _chat_id, messages in chats.items():
        first_outbound_id = next(
            (m["message_id"] for m in messages if m["direction"] == "outbound"), None
        )
        for m in messages:
            if m["direction"] != "outbound" or m["message_id"] == first_outbound_id:
                continue
            if not m["message"].strip():
                continue
            if skeleton_totals[skeleton(mask_names(m["message"], m["contact_name"]))] < (
                TEMPLATE_SKELETON_MIN
            ):
                hand_typed.append(mask_names(m["message"], m["contact_name"], m["rep_name"]))
    sample = random.Random(42).sample(hand_typed, min(sample_size, len(hand_typed)))
    lengths = [len(t) for t in sample]
    words = [len(t.split()) for t in sample]
    stats = {
        "population": len(hand_typed),
        "sampled": len(sample),
        "chars_mean": round(statistics.mean(lengths), 1),
        "chars_median": statistics.median(lengths),
        "words_mean": round(statistics.mean(words), 1),
        "sentences_mean": round(statistics.mean(sentences(t) for t in sample), 2),
        "pct_with_question": pct(sum("?" in t for t in sample), len(sample)),
        "pct_greeting_start": pct(sum(bool(GREETING_RE.match(t)) for t in sample), len(sample)),
        "pct_contractions": pct(sum(bool(CONTRACTION_RE.search(t)) for t in sample), len(sample)),
        "pct_signoff": pct(sum(bool(SIGNOFF_RE.search(t.strip())) for t in sample), len(sample)),
        "pct_ends_question": pct(sum(t.strip().endswith("?") for t in sample), len(sample)),
        "pct_ends_period": pct(sum(t.strip().endswith(".") for t in sample), len(sample)),
        "pct_ends_exclaim": pct(sum(t.strip().endswith("!") for t in sample), len(sample)),
        "pct_no_terminal_punct": pct(
            sum(not t.strip().endswith((".", "!", "?")) for t in sample), len(sample)
        ),
    }
    (OUT_DIR / "human_outbound_sample.txt").write_text("\n---\n".join(sample), encoding="utf-8")
    print(f"[3] hand-typed outbound stats: {json.dumps(stats)}")

    # -- 4: inbound voice ----------------------------------------------------
    inbound = [r["message"] for r in rows if r["direction"] == "inbound" and r["message"].strip()]
    alpha = [t for t in inbound if any(c.isalpha() for c in t)]
    in_stats = {
        "count": len(inbound),
        "chars_mean": round(statistics.mean(len(t) for t in inbound), 1),
        "chars_median": statistics.median(len(t) for t in inbound),
        "words_mean": round(statistics.mean(len(t.split()) for t in inbound), 1),
        "pct_one_word": pct(sum(len(t.split()) == 1 for t in inbound), len(inbound)),
        "pct_all_lower": pct(sum(t == t.lower() for t in alpha), len(alpha)),
        "pct_all_caps": pct(sum(t == t.upper() for t in alpha), len(alpha)),
        "pct_terminal_punct": pct(
            sum(t.strip().endswith((".", "!", "?")) for t in inbound), len(inbound)
        ),
        "pct_contains_comma": pct(sum("," in t for t in inbound), len(inbound)),
        "pct_greeting": pct(sum(bool(GREETING_RE.match(t)) for t in inbound), len(inbound)),
    }
    top = Counter(" ".join(t.split()).lower() for t in inbound).most_common(25)
    (OUT_DIR / "inbound_top25.txt").write_text(
        "\n".join(f"{count}x {text!r}" for text, count in top), encoding="utf-8"
    )
    print(f"[4] inbound voice stats: {json.dumps(in_stats)}")

    # -- 5: compliance flags -------------------------------------------------
    flags: dict[str, list[dict[str, Any]]] = {"rate": [], "approval": [], "urgency": []}
    for row in rows:
        if row["direction"] != "outbound":
            continue
        for kind, regex in (("rate", RATE_RE), ("approval", APPROVAL_RE), ("urgency", URGENCY_RE)):
            if regex.search(row["message"]):
                flags[kind].append(
                    {
                        "chat_id": row["chat_id"],
                        "message_id": row["message_id"],
                        "account": row["account"],
                        "match": regex.search(row["message"]).group(0),  # type: ignore[union-attr]
                        "message": row["message"],
                    }
                )
    qualify_hits = sum(
        1 for r in rows if r["direction"] == "outbound" and QUALIFY_RE.search(r["message"])
    )
    opener_optout = sum(
        1
        for msgs in chats.values()
        if msgs[0]["direction"] == "outbound" and OPT_OUT_RE.search(msgs[0]["message"])
    )
    with (OUT_DIR / "compliance_flags.txt").open("w", encoding="utf-8") as out:
        for kind, hits in flags.items():
            out.write(f"== {kind}: {len(hits)} outbound messages ==\n")
            for hit in hits:
                out.write(json.dumps(hit, ensure_ascii=False) + "\n")
            out.write("\n")
    print(
        f"[5] compliance: rate_claims={len(flags['rate'])} "
        f"approval_language={len(flags['approval'])} urgency={len(flags['urgency'])} "
        f"qualify_language={qualify_hits} "
        f"openers_with_optout={opener_optout}/{opener_chats} "
        f"({pct(opener_optout, opener_chats)}%)"
    )
    print(f"    details in {OUT_DIR / 'compliance_flags.txt'}")


def validate_drafts() -> None:
    gsm_extension = set("^{}\\[~]|\f")
    print("[6] draft validation (merged with longest realistic sample values):")
    failures = 0
    for draft in DRAFTS:
        body = draft["body"]
        for field, value in MERGE_PREVIEW.items():
            body = body.replace(field, value)
        encoding, units, segments = sms_encoding(body)
        checks = {
            "ascii": body.isascii(),
            "gsm7": encoding == "GSM-7",
            "no_ext_chars": not (set(body) & gsm_extension),
            "one_segment": segments == 1,
            "one_question": body.count("?") == 1,
            "opt_out": bool(OPT_OUT_RE.search(body)) or draft["kind"] == "follow-up",
            "no_rate": not RATE_RE.search(body),
            "no_approval": not APPROVAL_RE.search(body) and not QUALIFY_RE.search(body),
            "no_urgency": not URGENCY_RE.search(body),
        }
        ok = all(checks.values())
        failures += 0 if ok else 1
        print(
            f"  {'PASS' if ok else 'FAIL'} {draft['name']}: chars={len(body)} "
            f"units={units} segments={segments}"
            + ("" if ok else f" failed={[k for k, v in checks.items() if not v]}")
        )
        print(f"        grounded in: {draft['grounded_in']}")
    if failures:
        sys.exit(f"{failures} draft(s) failed validation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", default="texttorrent_conversations_last30d.csv")
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--drafts-only", action="store_true")
    args = parser.parse_args()
    if not args.drafts_only:
        analyze(load(args.csv), args.sample)
    validate_drafts()


if __name__ == "__main__":
    main()
