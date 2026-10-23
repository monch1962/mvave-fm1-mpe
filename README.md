# Felucca — MPE fork

[![License: GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-blue.svg)](LICENSE)

A fork of [hugelton/Felucca](https://github.com/hugelton/Felucca) 1.6.1 that adds **MIDI Polyphonic Expression
(MPE)** to the M-VAVE FM-1: per-note **pitch bend, pressure and timbre** from an MPE controller such as a
LinnStrument. Felucca's engines, sequencer, effects and every other UI page are untouched — the full host test
suite passes on byte-identical golden renders — MPE is an additional MIDI IN routing on top.

The MPE work lives on the **`mpe`** branch (the repo's default branch).

## MPE mode

Select **`MENU > MIDI > MIDI IN`** → **`MPE`** (appended to `CH1-4 / SEL / CH5-8 / CH9-12 / CH13-16 / TRACK`;
value 6). On the same page the **`T1 CHANNEL` row becomes `BASE CH`** in MPE mode: it sets the lower zone's first
member channel, `CH1`…`CH16`, default `CH2`. Both settings are live (a reboot returns BASE CH to CH2).

MPE follows the spec's **single lower zone**: the master channel (1) stays outside, every member channel from
BASE CH upward plays one instrument on the MPE part's voice pool. MPE **takes over** — the MPE part owns the whole
voice budget (NVOICE, 8 by default) in this route.

Per note, per MIDI channel (each channel bends/pressures/timbres only its own voices):

| Dimension | MIDI | What happens |
| --- | --- | --- |
| Pitch | Pitch Bend | per-note bend, RPN 0 range **0..96 semitones**, applied without smoothing |
| Pressure | Channel Aftertouch | per-note brightness (the voice's filter cutoff) |
| Timbre | CC74 | per-note brightness via the engine's cutoff / MLVL; on FM6 an extra per-voice 1-pole low-pass |

Notes pass 1:1 to the MPE part — chord, arp and live recording are bypassed. **Reset All Controllers** (CC121)
and any route or BASE CH change that moves a channel out of the zone release its notes and recentre its
bend, pressure and timbre.

### Playing MPE

Put an MPE controller in a single-zone layout on consecutive channels (e.g. a LinnStrument covering channels
2..16), set `MIDI IN = MPE`, leave `BASE CH` at `CH2`, and play: hold a chord and bend one note — the others
stay put. Most MPE controllers send **RPN 0 = ±48** at configure time; it is honoured over the full 0..96 range.

## Browser emulator (MPE in the browser)

The firmware compiles to WebAssembly, and **Web MIDI input is wired into the real firmware MIDI path**
(`web_midi → midi_enqueue`), so MPE is fully exercisable with no FM-1 and no cable:

```sh
./build.sh                  # build/gen tables first (once)
web/emu/build.sh            # needs Emscripten (emcc); → build/emu/felucca.wasm
python3 -m http.server -d build/emu 8790   # then open http://localhost:8790 in Chrome/Edge
```

Click **Switch on**, allow MIDI access, choose the controller, and set `MIDI IN = MPE` on the virtual panel
(hold **HOME** to open the menu; **ALGORITHM** steps the tabs). Notes: the emulator runs **one core** (the FM-1's
second core is not emulated); USB audio out, the editor protocol, firmware updates and TRS MIDI are not emulated.

**Load an FM6/DX7 bank** with the **Load FM6 bank** button below the panel, or by
dragging the file onto the page (a 4096-byte bank, or the 4104-byte sysex-framed
dump most sites host): the firmware's own staged-bank flow commits it to flash
`0xE7000`, so `FM6 SLOT B1..B32` pick the voices at once — the same file the FM-1's
web editor later flashes. The loaded bank is kept with the emulator's saved flash
across reloads.

## The DX7-bank MPE shortlister

A host tool that reads a standard DX7 bank — the 4096-byte bank the web editor
sends (or the 4104-byte sysex-framed dump most sites host) — scores every
voice's fit under MPE (per-note pitch bend, pressure, CC74 timbre), and can
assemble a new bank from the top picks — so a large DX7 archive collapses to a
shortlist to audition:

```sh
python3 tools/dx7_mpe_bank.py BANK.syx [--top 24] [--json]   # score one bank
python3 tools/dx7_mpe_bank.py scan DIR [--top N] [--json]   # a whole tree, deduped
python3 tools/dx7_mpe_bank.py make OUT.syx DIR [--top 32] [--fill]   # a new bank from the top (--fill: one per category)
```

Signals: LFO pitch-mod depth (built-in vibrato fights per-note bend), fixed-vs-ratio
operators (ratio chords bend in tune), carrier envelope shape → PAD / E.PIANO /
PLUCK / BELL / BASS / OTHER, feedback, output levels. The scoring is conservative
guidance (weights calibrated by listening — see the project plan), and its unit
tests run with the host suite (`tools/test_dx7_mpe_bank.py` in `tests/run_tests.sh`).

## Voice budget

`NVOICE` (8 by default) is a build-time override: `NVOICE=12 ./build.sh` (or 16) raises the shared voice pool.
Rendering the higher voice counts across the FM-1's two cores is a separate work item (see the project plan).

## Building and installing

Building needs the JieLi pi32v2 toolchain and the AC79 SDK — see [BUILDING.md](BUILDING.md). On macOS the
Linux-only toolchain runs in a Docker container; the project's `BUILD-NOTES.md` documents the environment
quirks (`DOCKER_CONFIG`, a venv for `Pillow`/`fontTools`).

Install a built package over USB:

```sh
python3 tools/fm1_install.py build/felucca.fwsc
```

or through a web installer in Chrome/Edge. Note: **upstream Felucca's installer and editor serve that
project's own releases** — use them only to return to stock Felucca; for this fork, build and flash from
source (or a release here).

If an install fails and the FM-1 no longer starts, recovery needs
[FM-1-transporter](https://github.com/kurogedelic/FM-1-transporter).

## Upstream, credits and licence

Fork of **Felucca 1.6.1 by Leo Kuroshita (@kurogedelic), Hügelton Instruments**
([upstream](https://github.com/hugelton/Felucca) · [wiki](https://github.com/hugelton/Felucca/wiki/Home)).
Free software: [GPL-3.0-only](LICENSE); the Hügelton Sample Pack is CC0 1.0; the engines in `firmware/engines`
are MIT ([Felucca-Engines](https://github.com/hugelton/Felucca-Engines)); the bundled fonts and ported DSP keep
their own licences ([LICENSES/](LICENSES/), [LICENSING.md](LICENSING.md)). Thanks to everyone whose work is in
it: [CREDITS.md](CREDITS.md). The MPE additions are the fork authors' work, GPL-3.0 like the rest.

AI-assisted development: like upstream, this fork is developed with the assistance of AI coding agents; no
generative AI is used for music, icons or artwork.

M-VAVE and FM-1 are trademarks of their respective owners. This firmware is not affiliated with or endorsed by them.

Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments; MPE additions © 2026 the fork authors.