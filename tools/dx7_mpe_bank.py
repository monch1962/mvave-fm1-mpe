#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""DX7 32-voice bank -> MPE-fit report and shortlist for the M-VAVE FM-1.

Reads the standard 4096-byte bank the FM-1/Felucca web editor sends (32 x
128-byte packed voices, the same layout firmware/src/fm6_core.c fm6_unpack
reads), scores each voice's fit under MPE (per-note pitch bend, pressure,
CC74 timbre), and can assemble a new bank from the top picks.

Usage:
    python3 tools/dx7_mpe_bank.py BANK.syx [--top 24] [--json]
    python3 tools/dx7_mpe_bank.py scan DIR [--top N] [--json]
    python3 tools/dx7_mpe_bank.py make OUT.syx DIR|BANK [--top 32]

The scoring is conservative guidance, calibrated by ear (see the project's
PLAN-mpe-banks.md): the report narrows thousands of voices to a shortlist;
listening is the judge.
"""
import argparse
import hashlib
import json
import os
import sys

RECORD = 128                 # bytes per packed voice
BANK = RECORD * 32           # the 4096-byte 32-voice bank
OP = 17                      # bytes per operator inside a record

# voice-level offsets inside the packed record (fm6_core.c fm6_unpack)
ALG = 110                    # algorithm 0..31 (bits 0..4)
FB = 111                     # feedback 0..7
LFS = 112                    # LFO speed
LFD = 113                    # LFO delay
LPMD = 114                   # LFO pitch-mod depth (the vibrato)
LAMD = 115                   # LFO amp-mod depth
LKS_LFW_LPMS = 116           # sync (bit0), wave (bits1..3), PMS (bits4..6)
TRANSPOSE = 117
NAME = 118                   # 10 bytes

# per-operator offsets inside the record's OP-byte block
OP_ATT = 0                   # R1, attack rate
OP_D1R = 1                   # decay-1 rate
OP_D2R = 2
OP_RR = 3                    # release rate
OP_L1 = 4                    # D1L, the sustain level
OP_L2 = 5
OP_OL = 14                   # output level
OP_MODE_FC = 15              # bit0 fixed frequency, bits1..5 coarse ratio
OP_FF = 16                   # fine frequency

# FM6_ALG from firmware/src/fm6_core.c: per algorithm the six operators'
# routing nibbles; an operator is a CARRIER when (value & 3) == 0.
FM6_ALG = [
    [0xc1, 0x11, 0x11, 0x14, 0x01, 0x14], [0x01, 0x11, 0x11, 0x14, 0xc1, 0x14],
    [0xc1, 0x11, 0x14, 0x01, 0x11, 0x14], [0xc1, 0x11, 0x94, 0x01, 0x11, 0x14],
    [0xc1, 0x14, 0x01, 0x14, 0x01, 0x14], [0xc1, 0x94, 0x01, 0x14, 0x01, 0x14],
    [0xc1, 0x11, 0x05, 0x14, 0x01, 0x14], [0x01, 0x11, 0xc5, 0x14, 0x01, 0x14],
    [0x01, 0x11, 0x05, 0x14, 0xc1, 0x14], [0x01, 0x05, 0x14, 0xc1, 0x11, 0x14],
    [0xc1, 0x05, 0x14, 0x01, 0x11, 0x14], [0x01, 0x05, 0x05, 0x14, 0xc1, 0x14],
    [0xc1, 0x05, 0x05, 0x14, 0x01, 0x14], [0xc1, 0x05, 0x11, 0x14, 0x01, 0x14],
    [0x01, 0x05, 0x11, 0x14, 0xc1, 0x14], [0xc1, 0x11, 0x02, 0x25, 0x05, 0x14],
    [0x01, 0x11, 0x02, 0x25, 0xc5, 0x14], [0x01, 0x11, 0x11, 0xc5, 0x05, 0x14],
    [0xc1, 0x14, 0x14, 0x01, 0x11, 0x14], [0x01, 0x05, 0x14, 0xc1, 0x14, 0x14],
    [0x01, 0x14, 0x14, 0xc1, 0x14, 0x14], [0xc1, 0x14, 0x14, 0x14, 0x01, 0x14],
    [0xc1, 0x14, 0x14, 0x01, 0x14, 0x04], [0xc1, 0x14, 0x14, 0x14, 0x04, 0x04],
    [0xc1, 0x14, 0x14, 0x04, 0x04, 0x04], [0xc1, 0x05, 0x14, 0x01, 0x14, 0x04],
    [0x01, 0x05, 0x14, 0xc1, 0x14, 0x04], [0x04, 0xc1, 0x11, 0x14, 0x01, 0x14],
    [0xc1, 0x14, 0x01, 0x14, 0x04, 0x04], [0x04, 0xc1, 0x11, 0x14, 0x04, 0x04],
    [0xc1, 0x14, 0x04, 0x04, 0x04, 0x04], [0xc4, 0x04, 0x04, 0x04, 0x04, 0x04],
]


def carrier_mask(alg):
    """Bit k of the mask = operator k is a carrier (fm6_core.c fm6_carriers)."""
    m = 0
    for k, val in enumerate(FM6_ALG[alg & 31]):
        if (val & 3) == 0:
            m |= 1 << k
    return m


class Voice:
    def __init__(self, record, index=0, source=""):
        if len(record) != RECORD:
            raise ValueError(f"a voice record is {RECORD} bytes, got {len(record)}")
        self.record = record
        self.index = index
        self.source = source
        self.name = record[NAME:NAME + 10].decode("latin-1").rstrip(" \x00")
        self.alg = record[ALG] & 31
        self.fb = record[FB] & 7
        self.lfs = record[LFS]
        self.lfd = record[LFD]
        self.lpmd = record[LPMD]
        self.lamd = record[LAMD]
        self.lfw = (record[LKS_LFW_LPMS] >> 1) & 7
        self.lpms = (record[LKS_LFW_LPMS] >> 4) & 7
        self.ops = []
        for k in range(6):
            o = k * OP
            self.ops.append({
                "att": record[o + OP_ATT], "dec1": record[o + OP_D1R],
                "dec2": record[o + OP_D2R], "rel": record[o + OP_RR],
                "l1": record[o + OP_L1], "l2": record[o + OP_L2],
                "ol": record[o + OP_OL],
                "fixed": bool(record[o + OP_MODE_FC] & 1),
                "coarse": (record[o + OP_MODE_FC] >> 1) & 31,
                "fine": record[o + OP_FF],
            })

    def digest(self):
        return hashlib.sha256(self.record).hexdigest()


class Bank:
    """A parsed 4096-byte bank."""

    def __init__(self, data, source=""):
        if len(data) in (BANK, RECORD):
            pass
        elif len(data) == BANK + 8 and data[0] == 0xF0 and data[-1] == 0xF7:
            data = data[6:-2]          # sysex-framed dump: F0 43 00 09 20 00 + voices + checksum + F7
        else:
            raise ValueError(f"a bank is {BANK} bytes (or {RECORD}, or the {BANK + 8}-byte framed dump): got {len(data)}")
        n = len(data) // RECORD
        self.voices = [Voice(data[i * RECORD:(i + 1) * RECORD], i, source)
                       for i in range(n)]

    def __len__(self):
        return len(self.voices)


def parse_bank(data, source=""):
    return Bank(data, source)


def category(v):
    """A voice's primary use, from its carriers' envelopes, levels and fixed ratio.

    In DX7 voices the decay-to-silence is D2L, not D1L (idiophones hold D1L=99 and
    decay via D2R -> D2L=0), so "sustains" reads the carriers' mean D2L.
    """
    car = carrier_mask(v.alg)
    ops = [v.ops[k] for k in range(6) if car >> k & 1]
    if not ops:
        return "OTHER"
    ol = sum(o["ol"] for o in ops) / len(ops)
    if ol < 30:                                # quiet/dark: don't guess
        return "OTHER"
    sust = sum(o["l2"] for o in ops) / len(ops)   # holds at/after decay-2
    att = sum(o["att"] for o in ops) / len(ops)
    d2 = sum(o["dec2"] for o in ops) / len(ops)
    rel = sum(o["rel"] for o in ops) / len(ops)
    if sust >= 40:                             # holds: pad / epiano / metallic bell
        if att >= 45:
            met = sum(o["fixed"] for o in ops)
            return "BELL" if met >= 2 else "EP"   # held metallic hits ring (triangle, glass)
        return "PAD"
    if att >= 60 and d2 >= 45:                 # sharp decaying hit ringing out
        return "BELL"
    return "PLUCK"


# category -> baseline bonus (calibration in PLAN-mpe-banks.md step 4)
CATEGORY_BONUS = {"PAD": 25, "EP": 25, "PLUCK": 20, "BELL": 20, "BASS": 10}


def score(v):
    """MPE-fit 0..100. Monotonic in the signals; the shapes are tuned so a corpus
    scan discriminates instead of collapsing to a few tied tiers:
    - vibrato is quadratic (a little is harmless, a lot very costly),
    - the level headroom is graduated, not a +/- switch."""
    c = category(v)
    car = carrier_mask(v.alg)
    ops = [v.ops[k] for k in range(6) if car >> k & 1]
    ol = sum(o["ol"] for o in ops) / (len(ops) or 1)
    s = 50 + CATEGORY_BONUS.get(c, 0)
    s -= min(40, (v.lpmd * v.lpmd) // 250)      # vibrato: 0->0, 50->10, 75->22, 99->39
    s -= 8 * sum(o["fixed"] for o in v.ops)     # ratio ops bend in tune
    s += 8 if ol <= 70 and ol > 15 else -6 if ol >= 85 else 0   # headroom: sweet 16..70, hot >=85, ignored 71..84
    if ol < 15:
        s -= 8                                  # too quiet to take timbre well
    return max(0, min(100, s))


def inspect(v):
    """The report row: category, signals and score."""
    car = carrier_mask(v.alg)
    ops = [v.ops[k] for k in range(6) if car >> k & 1]
    return {
        "index": v.index, "name": v.name, "source": v.source,
        "category": category(v), "score": score(v),
        "lfo_pmd": v.lpmd, "fixed_ops": sum(o["fixed"] for o in v.ops),
        "feedback": v.fb, "carrier_ol": round(sum(o["ol"] for o in ops) / (len(ops) or 1), 1),
    }


def _syx_files(root):
    if os.path.isfile(root):
        yield root
        return
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            if f.lower().endswith(".syx"):
                yield os.path.join(dirpath, f)


def health(v):
    """Within a score tie: the MPE-friendliest first — least LFO pitch-mod (vibrato),
    fewest fixed-freq operators (ratio chords bend in tune), most level headroom."""
    car = carrier_mask(v.alg)
    ops = [v.ops[k] for k in range(6) if car >> k & 1]
    ol = sum(o["ol"] for o in ops) / (len(ops) or 1)
    return (v.lpmd, sum(o["fixed"] for o in v.ops), -ol)


def _load_sources(sources, extra=()):
    """All distinct voices from a file/dir and any extra byte bags; a file that is
    not a parseable bank is skipped (the corpus has odd stragglers), not fatal."""
    seen = {}
    skipped = 0
    for bag in extra:
        for v in parse_bank(bag, "<extra>").voices:
            seen.setdefault(v.digest(), v)
    for path in _syx_files(sources):
        with open(path, "rb") as fp:
            bank = fp.read()
        try:
            parsed = parse_bank(bank, path)
        except ValueError:
            skipped += 1
            continue
        for v in parsed.voices:
            seen.setdefault(v.digest(), v)
    if skipped and os.environ.get("DX7BANK_VERBOSE"):
        print(f"scan: skipped {skipped} unparseable files", file=sys.stderr)
    return sorted(seen.values(), key=lambda v: (-score(v), health(v), v.name, v.digest()))


class Entry:
    """A scanned voice and where it came from."""

    def __init__(self, voice, source=""):
        self.voice = voice
        self.source = source


def scan(root, top=None):
    """Every distinct voice in a tree, MPE-sorted, deduped by record hash."""
    ranked = _load_sources(root)
    ranked = ranked[:top] if top else ranked
    return [Entry(v, v.source) for v in ranked]


def _fill_categories(ranked, top):
    """`make --fill`: round-robin the ranked voices one per category at a time, so a
    bank spreads across the MPE-relevant classes instead of a greedy tie grab-bag
    (OTHER — FM setup patches, quiet oddities — is never picked while any of
    PAD/EP/PLUCK/BELL/BASS has a voice)."""
    by = {}
    order = ("PAD", "EP", "PLUCK", "BELL", "BASS")
    for v in ranked:
        by.setdefault(category(v), []).append(v)
    idx = {c: 0 for c in order}
    out = []
    while len(out) < top:
        added = False
        for c in order:
            pool = by.get(c)
            if pool and idx[c] < len(pool):
                v = pool[idx[c]]
                idx[c] += 1
                out.append(v)
                added = True
                if len(out) >= top:
                    break
        if not added:
            break
    return out


def _write_report(path, ranked, picked, sources, top, fill, n_unique):
    """A human-readable shortlist next to the bank: its slots and the corpus-wide
    per-category leaders, so the selection travels with the bank."""
    with open(path, "w") as fp:
        fp.write("MPE-fit DX7 bank shortlist\n")
        fp.write(f"source: {sources} ({n_unique} unique voices)\n")
        fp.write(f"command: python3 tools/dx7_mpe_bank.py make {path[:-4]} {sources} --top {top}"
                 + (" --fill" if fill else "") + "\n\n")
        fp.write(f"Bank slots B1..B{len(picked)} (the file pads to 32):\n")
        for k, v in enumerate(picked, 1):
            fp.write(f"  B{k:>2}  {v.name[:12]:12} {category(v):6} {score(v):>3}  {v.source}\n")
        fp.write("\nCorpus leaders per category (top 8 each):\n")
        for cat in ("PAD", "EP", "PLUCK", "BELL", "BASS", "OTHER"):
            sel = [v for v in ranked if category(v) == cat][:8]
            if not sel:
                continue
            fp.write(f"  -- {cat} --\n")
            for v in sel:
                fp.write(f"    {score(v):>3}  {v.name[:14]:14} {v.source}\n")


def make(out, sources, top=32, fill=False, extra=(), report=None):
    """Assemble a new 4096-byte bank: the top-scoring distinct voices, or with fill a
    category-balanced set (every category's best, alternated). `report` also writes a
    shortlist next to the bank (default: OUT.txt; pass a path to place it elsewhere)."""
    ranked = _load_sources(sources, extra)
    if not ranked:
        raise ValueError("no usable voices in the sources")
    picked = _fill_categories(ranked, top) if fill else ranked[:max(1, min(top, len(ranked)))]
    picked = picked or ranked[:1]
    records = (picked * ((32 + len(picked) - 1) // len(picked)))[:32]
    with open(out, "wb") as fp:
        fp.write(b"".join(v.record for v in records))
    if report is not None:
        _write_report(out + ".txt" if report is True else report, ranked, picked,
                      sources, top, fill, len(ranked))


def _rows(voices):
    return [inspect(v) for v in voices]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p_bank = sub.add_parser("bank", help="score one bank")
    p_bank.add_argument("bank")
    p_bank.add_argument("--top", type=int, default=24)
    for p in (p_bank,):
        p.add_argument("--json", action="store_true")
    p_scan = sub.add_parser("scan", help="score every bank in a tree")
    p_scan.add_argument("dir")
    p_scan.add_argument("--top", type=int, default=0)
    p_scan.add_argument("--json", action="store_true")
    p_mk = sub.add_parser("make", help="assemble a bank from the top voices")
    p_mk.add_argument("out")
    p_mk.add_argument("source")
    p_mk.add_argument("--top", type=int, default=32)
    p_mk.add_argument("--fill", action="store_true", help="alternate one per category (PAD/EP/PLUCK/BELL/BASS), not a greedy top")
    p_mk.add_argument("--report", nargs="?", const=True, metavar="TXT",
                      help="also write a shortlist next to the bank (default OUT.txt, or give a path)")
    args = ap.parse_args(argv)
    if args.cmd == "make":
        make(args.out, args.source, top=args.top, fill=getattr(args, "fill", False),
             report=getattr(args, "report", None))
        print(f"wrote {args.out}"
              + (" and its shortlist" if getattr(args, "report", None) is not None else ""))
        return 0
    voices = scan(args.dir, top=args.top or None) if args.cmd == "scan" \
        else scan(args.bank, top=args.top)
    rows = _rows(e.voice for e in voices)
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=1))
        return 0
    print(f"{'idx':>5} {'name':10} {'cat':6} {'score':5}  lfo-pmd fixed fb ol")
    for r in rows:
        print(f"{r['index']:>5} {r['name'][:10]:10} {r['category']:6} "
              f"{r['score']:5}  {r['lfo_pmd']:>7} {r['fixed_ops']:>5} "
              f"{r['feedback']:2} {r['carrier_ol']:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
