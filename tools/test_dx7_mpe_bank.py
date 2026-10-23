#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Tests for tools/dx7_mpe_bank.py (the DX7-bank MPE-fit shortlister).

The fixture voices are built by planting bytes at the documented pack offsets
(see dx7_mpe_bank.py and the C source it mirrors: firmware/src/fm6_core.c
fm6_unpack), so the parser's expectations do not depend on the tool's own
writer. Runs with: python3 tools/test_dx7_mpe_bank.py
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dx7_mpe_bank as m  # noqa: E402


def mkvoice(name="VOICE", alg=0, fb=0, lfs=0, lpmd=0, lamd=0, ol=70, fixed=False,
            fixed_ops=0, l1=70, l2=70, att=30, dec=40, d2=None, rel=50, coarse=1, index=0):
    """A 128-byte packed voice record with the planted fields (rest zeros)."""
    b = bytearray(m.RECORD)
    padded = (name + " " * 10)[:10].encode("ascii")
    b[m.NAME:m.NAME + 10] = padded
    b[m.ALG] = alg
    b[m.FB] = fb
    b[m.LFS] = lfs
    b[m.LPMD] = lpmd
    b[m.LAMD] = lamd
    if d2 is None:
        d2 = dec
    for k in range(6):
        op = k * m.OP
        b[op + 0] = att                 # R1 (attack rate)
        b[op + 1] = dec                 # D1R
        b[op + 2] = d2                  # D2R
        b[op + 4] = l1                  # D1L
        b[op + 5] = l2                  # D2L (the decay-to-silence level)
        b[op + 3] = rel                 # RR
        b[op + 14] = ol                 # output level
        b[op + 15] = ((1 if (fixed and k == 0) or k < fixed_ops else 0)) | (coarse << 1)
        b[op + 16] = 0                  # fine
    return bytes(b)


def mkbank(voices):
    if len(voices) == 1:
        voices = list(voices) * 32
    assert len(voices) == 32 and all(len(v) == m.RECORD for v in voices)
    return b"".join(voices)


class ParserTest(unittest.TestCase):
    def test_bank_parse_returns_32_voices(self):
        self.assertEqual(len(m.parse_bank(mkbank([mkvoice(index=i) for i in range(32)]))), 32)
    def test_bank_rejects_bad_sizes(self):
        with self.assertRaises(ValueError):
            m.parse_bank(b"\x00" * 100)
        with self.assertRaises(ValueError):
            m.parse_bank(b"\x00" * (m.BANK + 1))

    def test_bank_accepts_4104_byte_sysex_framed_dumps(self):
        # most online DX7 banks are F0 43 00 09 20 00 + 4096 voices + checksum + F7
        raw = mkbank([mkvoice(name="FRAMED  ", index=i) for i in range(32)])
        framed = b"\xf0\x43\x00\x09\x20\x00" + raw + b"\x0e\xf7"
        bank = m.parse_bank(framed)
        self.assertEqual(len(bank), 32)
        self.assertEqual(bank.voices[0].name, "FRAMED")

    def test_voice_fields_parse_at_documented_offsets(self):
        v = m.parse_bank(mkbank([mkvoice(name="MYVOICE  ", alg=5, fb=3, lfs=4, lpmd=40,
                                         fixed=True, ol=70)])).voices[0]
        self.assertEqual(v.name, "MYVOICE")
        self.assertEqual(v.alg, 5)
        self.assertEqual(v.fb, 3)
        self.assertEqual(v.lfs, 4)
        self.assertEqual(v.lpmd, 40)
        self.assertEqual(v.ops[0]["fixed"], True)
        self.assertEqual(v.ops[0]["ol"], 70)
        self.assertEqual(v.ops[0]["coarse"], 1)
        self.assertEqual(v.ops[1]["fixed"], False)


class CarrierTest(unittest.TestCase):
    def test_carrier_masks_match_the_C_rule_val3_zero(self):
        # FM6_ALG rows 0, 1, 30, 31 whose &3 == 0 bits are the carriers
        self.assertEqual(m.carrier_mask(0), 0x28)   # {0xc1,0x11,0x11,0x14,0x01,0x14}
        self.assertEqual(m.carrier_mask(1), 0x28)
        self.assertEqual(m.carrier_mask(30), 0x3E)  # {0xc1,0x14,0x04,0x04,0x04,0x04}
        self.assertEqual(m.carrier_mask(31), 0x3F)  # all six carriers


class CategoryTest(unittest.TestCase):
    """Heuristics planted with unambiguous values -> the branch they hit."""

    def test_sustained_slow_attack_is_pad(self):
        v = m.parse_bank(mkbank([mkvoice(alg=31, l1=80, l2=80, att=20, dec=30)])).voices[0]
        self.assertEqual(m.category(v), "PAD")

    def test_sustained_fast_attack_is_ep(self):
        v = m.parse_bank(mkbank([mkvoice(alg=31, l1=80, l2=60, att=70, dec=30)])).voices[0]
        self.assertEqual(m.category(v), "EP")

    def test_percussive_fast_decay_is_bell(self):
        v = m.parse_bank(mkbank([mkvoice(alg=31, l1=10, l2=0, att=70, dec=90, d2=60, rel=90)])).voices[0]
        self.assertEqual(m.category(v), "BELL")

    def test_percussive_medium_decay_is_pluck(self):
        v = m.parse_bank(mkbank([mkvoice(alg=31, l1=10, l2=0, att=50, dec=50, d2=50, rel=60)])).voices[0]
        self.assertEqual(m.category(v), "PLUCK")

    def test_held_metallic_hit_is_bell(self):
        # an idiophone holds D1L=99 and decay-2 levels high, and uses fixed frequencies
        # (triangle, glass) - that must read as BELL, not EP
        v = m.parse_bank(mkbank([mkvoice(alg=31, l1=99, l2=90, att=99, dec=99, d2=35, fixed_ops=4)])).voices[0]
        self.assertEqual(m.category(v), "BELL")


class ScoreTest(unittest.TestCase):
    def test_vibrato_depths_rank_lower(self):
        a = m.parse_bank(mkbank([mkvoice(alg=31, lpmd=0)])).voices[0]
        b = m.parse_bank(mkbank([mkvoice(alg=31, lpmd=99)])).voices[0]
        self.assertGreater(m.score(a), m.score(b))

    def test_fixed_operators_rank_lower(self):
        a = m.parse_bank(mkbank([mkvoice(alg=31)])).voices[0]
        b = m.parse_bank(mkbank([mkvoice(alg=31, fixed=True)])).voices[0]
        self.assertGreater(m.score(a), m.score(b))

    def test_score_ties_break_by_mpe_health_not_name(self):
        # two voices tie at 80; the vibrato one (lpmd>0) holds the same score but
        # must come second: "BED" (clean) outranks "AAA" (vibrato) despite the name
        d = tempfile.mkdtemp(prefix="dx7tie")
        try:
            with open(os.path.join(d, "bed.syx"), "wb") as f:
                f.write(mkbank([mkvoice(name="BED      ", alg=31, lpmd=0)]))
            with open(os.path.join(d, "aaa.syx"), "wb") as f:
                f.write(mkbank([mkvoice(name="AAA      ", alg=31, lpmd=1)]))
            entries = m.scan(d)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].voice.lpmd, 0)   # the clean one, despite "bed" > "aaa"
        finally:
            shutil.rmtree(d)

    def test_mpe_like_category_outranks_other(self):
        pad = m.parse_bank(mkbank([mkvoice(alg=31, l1=80, l2=80, att=20)])).voices[0]
        other = m.parse_bank(mkbank([mkvoice(alg=0, ol=10, l1=0, l2=0, dec=99)])).voices[0]
        self.assertGreater(m.score(pad), m.score(other))


class ScanMakeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dx7t")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write_bank(self, name, voice):
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(mkbank([voice]))

    def test_scan_parses_a_tree_and_dedupes_by_record_hash(self):
        shared = mkvoice(name="SHARED  ", index=0)
        self.write_bank("a.syx", shared)
        self.write_bank("b.syx", shared)
        self.write_bank("c.syx", mkvoice(name="UNIQUE  ", index=1))
        voices = m.scan(self.dir)
        self.assertEqual(len(voices), 2)
        names = sorted(v.voice.name for v in voices)
        self.assertEqual(names, ["SHARED", "UNIQUE"])

    def test_scan_skips_unparseable_files_rather_than_aborting(self):
        self.write_bank("a.syx", mkvoice(name="GOOD    ", index=0))
        with open(os.path.join(self.dir, "odd.syx"), "wb") as f:
            f.write(b"\x00" * 100)          # a corrupt straggler
        voices = m.scan(self.dir)
        self.assertEqual(len(voices), 1)
        self.assertEqual(voices[0].voice.name, "GOOD")

    def test_make_assembles_top_n_into_a_4096_byte_bank(self):
        for i in range(12):
            self.write_bank(f"p{i}.syx", mkvoice(name=f"PAD{i:02d}   ", alg=31, l1=80, l2=80, att=20, ol=70, lpmd=0, index=i))
        for i in range(8):
            self.write_bank(f"e{i}.syx", mkvoice(name=f"EP{i:02d}    ", alg=31, l1=80, l2=60, att=70, ol=70, lpmd=60, index=i))
        out = os.path.join(self.dir, "out.syx")
        m.make(out, self.dir, top=8)
        with open(out, "rb") as fp_out:
            data = fp_out.read()
        self.assertEqual(len(data), m.BANK)
        voices = m.parse_bank(data).voices
        names = [v.name for v in voices]
        # 12 clean PADs (score 80) beat 8 vibrato-heavy EPs (score ~55): the top 8
        # are all distinct PADs, and every record is byte-identical to its source
        self.assertEqual(len(set(names)), 8)
        self.assertTrue(all(n.startswith("PAD") for n in names), names)
        src = {open(os.path.join(self.dir, "p0.syx"), "rb").read()}
        for j in range(1, 12):
            src.add(open(os.path.join(self.dir, f"p{j}.syx"), "rb").read())
        for v in voices:
            self.assertTrue(any(bytes(v.record) in bank for bank in src))

    def test_make_rejects_non4096_sources(self):
        with self.assertRaises(ValueError):
            m.make(os.path.join(self.dir, "o.syx"), self.dir, top=8,
                   extra=(b"\x00" * 100,))

    def test_make_fill_spreads_the_bank_across_categories(self):
        for i in range(8):
            self.write_bank(f"pad{i}.syx", mkvoice(name=f"PAD{i:02d}", alg=31, l1=80, l2=80, att=20, ol=70, index=i))
            self.write_bank(f"ep{i}.syx", mkvoice(name=f"EP{i:02d}", alg=31, l1=80, l2=60, att=70, ol=70, index=i))
            self.write_bank(f"bel{i}.syx", mkvoice(name=f"BELL{i:02d}", alg=31, l1=99, l2=90, att=99, d2=35, fixed_ops=4, ol=70, index=i))
            self.write_bank(f"plu{i}.syx", mkvoice(name=f"PLUCK{i:02d}", alg=31, l1=10, l2=0, att=50, d2=50, ol=70, index=i))
        out = os.path.join(self.dir, "fill.syx")
        m.make(out, self.dir, top=16, fill=True)
        cats = {m.category(v) for v in m.parse_bank(open(out, "rb").read()).voices}
        self.assertEqual(cats, {"PAD", "EP", "BELL", "PLUCK"})
        # a greedy top-16 of the same pool never reaches BELL/PLUCK (they score below
        # the PAD/EP ties), so only score-driven selection drops them
        m.make(out, self.dir, top=16, fill=False)
        greedy = {m.category(v) for v in m.parse_bank(open(out, "rb").read()).voices}
        self.assertNotIn("BELL", greedy)
        self.assertNotIn("PLUCK", greedy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
