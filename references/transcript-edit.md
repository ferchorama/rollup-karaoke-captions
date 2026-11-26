# Transcript editing — names, terms, and what may be changed

The `--words` JSON is literally what appears on screen. ASR mangles names and domain terms far
more often than ordinary speech, and a wrong brand or person's name is the most visible defect a
captions deliverable can carry — worse than a missed comma, worse than a dull stretch of text.
This file is the workflow for the pass between transcription and burn.

## The three rules

1. **Verify, don't guess.** A word changes only when there is evidence for the change. Plausibility
   is not evidence.
2. **Everything else stays verbatim** — fillers (`um`, `uh`), stutters, doubled words, spoken
   grammar. Captions transcribe; they do not rewrite the speaker.
3. **Report the pass.** Every change goes in the handoff, and every line you could not resolve
   goes in the handoff *with a timestamp* so a human can check it in seconds.

## Verification ladder — cheapest authority first

| # | Source | Why it ranks here | Worked example |
|---|---|---|---|
| 1 | **The footage itself** — lower thirds, step-and-repeat backdrop, slide decks, name cards | The client's own graphic. Highest authority, and free. | "Red Juan" → **Redwan Meslem**: the on-stage slide read `MODERATOR · REDWAN MESLEM, Enterprise Ethereum Alliance` in the same source folder. |
| 2 | **The same speaker elsewhere in the set** | The same name is usually misheard the same way, but a different take may land it correctly. | "ccz" in one clip vs "CZ" in a sibling clip of the same shoot. |
| 3 | **Public record** — press coverage, filings, exchange notices | Settles companies, tickers, executives, and announcements that the clip is literally about. | "bmb standard company … largest digital treasury for binance coin" → **BNB Standard Corporation (NASDAQ: BNC)**, CEO **Alex Odagiu**, confirmed by Nasdaq bell-ringing coverage. |
| 4 | **Brand spelling** — the canonical form of the company, asset or protocol | ASR has no notion of brand casing or spacing. | ZKsync (not "ZK Sync"), The Rollup (not "The Roll"), DTCC (not "DCCC"), stablecoins, agentic, articulate. |
| 5 | **Best guess** | — | **Never.** Leave the ASR text and flag it. |

When 1–4 disagree, the footage wins: it is what the audience is looking at.

## Recurring ASR failures in this footage

Worth checking every time, because these exact errors have recurred across separate clips:

| ASR | Actual | Note |
|---|---|---|
| `DCCC` / `DDCC` | **DTCC** | Hit two different speakers in the same shoot. |
| `The Roll` | **The Rollup** | Truncation at a sentence end where the ASR puts a period. |
| `ZK Sync` | **ZKsync** | Also splits into two tokens — needs a merge, not a spell. |
| `Argentic` | **Agentic** | Correctly transcribed later in the same clip. |
| `intricate in his words` | **articulate in his words** | Context-only fix. |
| `routes` (market) | **routs** | Homophone of the more common word; the sentence only parses one way. |
| `jordy viscer` | **Jordi Visser** | Name split across two tokens with no capitalisation. |
| `Red Juan` | **Redwan Meslem** | Settled from an on-screen credit. |

## The tool

```
python scripts/edit_words.py words.json words_final.json --spec spec.json
```

Three operations, applied in this order. Matching is case-insensitive on the bare word, so the
spec does not have to reproduce punctuation.

| Op | Shape | Use for | Example |
|---|---|---|---|
| `merge` | N tokens → 1 token | Tokens the karaoke highlight must not separate | `"7"+"%"` → `7%`, `"ZK"+"Sync"` → `ZKsync`, `"stable"+"coins"` → `stablecoins` |
| `phrase` | N tokens → M tokens, time span split by character length | Corrections that change the word count | `"a"+"era"` → `an era`, `"jordy"+"viscer"` → `Jordi Visser` |
| `spell` | 1 token → 1 token, punctuation preserved | One-for-one fixes | `argentic` → `Agentic` |

Why the split matters: the highlight advances **one token at a time**. Gluing "an era" into a
single token would light both words at once, which the reference style never does. Why the merge
matters just as much: `7` and `%` as two beats looks like a bug.

```json
{
  "merge":  [[["ZK", "Sync"], "ZKsync"]],
  "phrase": [[["a", "era"], ["an", "era"]]],
  "spell":  {"argentic": "Agentic"}
}
```

Keep the raw `words.json`. Write the pass to a different file (`words_final.json`) so the decision
is auditable and re-runnable, and so the burn's input can always be diffed against the ASR.

## Reviewing the pass

After running it, read the corrected text back — `words.txt`/`words.srt` written by
`introkit transcribe` are the fastest way, or print `cue["text"]`. Check that:

- no merge produced a token so wide it forces a page to shrink (watch the long ones);
- no phrase rewrite collapsed a multi-word span into a moment that no longer aligns with speech;
- the log line count matches what you set out to change, and nothing else moved.

## What goes in the handoff

- **Verified changes**, with the evidence that settled each one (footage / sibling clip / press).
- **Unresolved lines**, as `time — what is on screen now — what it is probably meant to be`.
- **An explicit "nothing else changed"**, so the reviewer knows the verbatim spans are intentional
  and not an oversight.
