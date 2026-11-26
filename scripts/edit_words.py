"""edit_words.py -- the editorial pass between ASR and the burn.

The skill's `--words` JSON is literally what appears on screen, and ASR mangles names and terms
far more often than it mangles ordinary speech. This is the one place where transcript text is
allowed to change, and it is deliberately narrow: it can merge, rewrite or respell tokens, but it
cannot invent, reorder or drop content.

Read `references/transcript-edit.md` before using it -- in particular the rule that a word is only
changed when there is evidence for the change, and that anything unresolved is left verbatim and
flagged in the handoff.

    python scripts/edit_words.py words.json words_final.json --spec spec.json

`spec.json` (all keys optional):

    {
      "merge":  [[["ZK", "Sync"], "ZKsync"]],           # N tokens -> 1 token
      "phrase": [[["a", "era"], ["an", "era"]]],        # N tokens -> M tokens, times split
      "spell":  {"argentic": "Agentic"}                 # 1 token -> 1 token, punctuation kept
    }

Operations run in that order: merge first (so fragments are already glued), then phrase, then
spell. Matching is case-insensitive on the bare word, so punctuation in the JSON does not have to
be reproduced in the spec.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

END = ",.;:!?)]}\"'"


def split_punct(tok: str):
    """('Sync.', '.') -> ('Sync', '.'); ('course,', ',') -> ('course', ',')"""
    core = tok.rstrip(END)
    return core, tok[len(core):]


def merge_seq(tokens: list[dict], seq: list[str], joined: str) -> list[dict]:
    """N tokens -> one. Keeps the first token's start and the last token's end.

    Use for things ASR splits but that read as a single unit on screen. The karaoke highlight
    advances one token at a time, so "7" + "%" would light the number and the sign separately,
    and "ZK" + "Sync." would flicker across a word boundary that does not exist.
    """
    out, i, hits = [], 0, 0
    want = [s.lower() for s in seq]
    while i < len(tokens):
        window = [split_punct(t["text"])[0].lower() for t in tokens[i:i + len(want)]]
        if len(want) and len(window) == len(want) and window == want:
            head, tail = tokens[i], tokens[i + len(want) - 1]
            out.append(dict(start=head["start"], end=tail["end"],
                            text=joined + split_punct(tail["text"])[1],
                            probability=min(t.get("probability", 1.0)
                                           for t in tokens[i:i + len(want)])))
            i += len(want)
            hits += 1
        else:
            out.append(dict(tokens[i]))
            i += 1
    return out if hits else tokens


def replace_seq(tokens: list[dict], seq: list[str], to: list[str]) -> list[dict]:
    """N tokens -> M tokens, splitting the original time span by character length.

    Needed because the highlight advances word by word, so a correction has to produce real timed
    words: gluing "an era" into one token would light both words at once. Character-weighted
    splitting keeps a long replacement word on screen longer than a short one.
    """
    out, i, hits = [], 0, 0
    want = [s.lower() for s in seq]
    while i < len(tokens):
        window = [split_punct(t["text"])[0].lower() for t in tokens[i:i + len(want)]]
        if len(want) and len(want) <= len(tokens) and len(window) == len(want) and window == want:
            head, tail = tokens[i], tokens[i + len(want) - 1]
            span = tail["end"] - head["start"]
            weights = [max(1, len(split_punct(w)[0])) for w in to]
            total = sum(weights)
            t0 = head["start"]
            for k, (word, wgt) in enumerate(zip(to, weights)):
                t1 = tail["end"] if k == len(to) - 1 else t0 + span * wgt / total
                out.append(dict(start=round(t0, 3), end=round(t1, 3), text=word))
                t0 = t1
            i += len(want)
            hits += 1
        else:
            out.append(dict(tokens[i]))
            i += 1
    return out if hits else tokens


def main(src: Path, dst: Path, spec_path: Path | None) -> None:
    tr = json.loads(src.read_text(encoding="utf-8"))
    spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path else {}
    merges = [(list(seq), joined) for seq, joined in spec.get("merge", [])]
    phrases = [(list(seq), list(to)) for seq, to in spec.get("phrase", [])]
    spell = {k.lower(): v for k, v in spec.get("spell", {}).items()}
    if not (merges or phrases or spell):
        print("[edit] empty spec: nothing to do")

    log = []
    for cue in tr["cues"]:
        words = [dict(w) for w in cue.get("words", [])]
        for seq, joined in merges:
            before = [w["text"] for w in words]
            words = merge_seq(words, seq, joined)
            if before != [w["text"] for w in words]:
                log.append(f"cue {cue['id']:>2}  merge {'+'.join(seq)} -> {joined}")
        for seq, to in phrases:
            before = [w["text"] for w in words]
            words = replace_seq(words, seq, to)
            if before != [w["text"] for w in words]:
                log.append(f"cue {cue['id']:>2}  phrase {' '.join(seq)} -> {' '.join(to)}")
        for w in words:
            core, punct = split_punct(w["text"])
            fix = spell.get(core.lower())
            if fix and core != fix:
                # keep an author-supplied acronym as written, else match the source's casing
                if core[:1].isupper() and not fix.isupper():
                    fix = fix[:1].upper() + fix[1:]
                log.append(f"cue {cue['id']:>2}  spell {w['text']} -> {fix + punct}")
                w["text"] = fix + punct
        cue["words"] = words
        # rebuild cue text, keeping hyphen-led fragments glued to the word before them
        txt = ""
        for w in words:
            txt += (w["text"] if not txt or w["text"].startswith("-") else " " + w["text"])
        cue["text"] = txt

    tr.setdefault("meta", {})["editorial_pass"] = spec
    dst.write_text(json.dumps(tr, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(log) if log else "(no edits)")
    print(f"[edit] {sum(len(c['words']) for c in tr['cues'])} words -> {dst}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="ASR word-timed JSON")
    ap.add_argument("dst", help="corrected word-timed JSON to write")
    ap.add_argument("--spec", help="JSON with merge / phrase / spell operations")
    a = ap.parse_args()
    main(Path(a.src), Path(a.dst), Path(a.spec) if a.spec else None)
