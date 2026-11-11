#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["numpy"]
# ///
"""Build an FCPXML project from two cameras:

Camera A is the main storyline/narrative, clips in format "[cam] A 04 [content]"
Camera B is b-roll taken during same time, in format "[cam] B 08 [content]"

Everything will be placed by the file's creation time, with B clips fine-tuned
by matching their audio against the A clips they overlap.

This fixes clock drift and the 1-second resolution of MP4 timestamps.

Camera is read from the filename (`... A 04 ...` / `... B 12 ...`). Stretches
where neither camera was rolling are shortened to --max-gap.

usage: fcp_sync.py [options] CLIP_OR_DIR [...]
Then in Final Cut Pro: File > Import > XML.

When trying to find the initial sync via audio, use `--sync-pair` specifying an
A clip that has a B clip recorded somewhere in the same time:

    uv run fcp_sync.py --sync-pair \
        "Scratch/Cam A 09 tour.MP4" "Scratch/Cam B 17 thing.MP4" Scratch
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import quoteattr

CAM = re.compile(r"\b([AB]) \d+\b")
EXTS = {".mp4", ".mov", ".mxf", ".m4v"}
RATES = {Fraction(1001, 24000): "2398", Fraction(1, 24): "24", Fraction(1, 25): "25", Fraction(1001, 30000): "2997",
         Fraction(1, 30): "30", Fraction(1, 50): "50", Fraction(1001, 60000): "5994", Fraction(1, 60): "60"}
SIZES = {(3840, 2160): "3840x2160", (1920, 1080): "1080", (1280, 720): "720"}


def fmt_name(fd, w, h):
    """FCP's built-in format name; sequences need one."""
    if fd in RATES and (w, h) in SIZES:
        return f' name="FFVideoFormat{SIZES[w, h]}p{RATES[fd]}"'
    return ""


def ts(x):
    x = Fraction(x)
    return f"{x.numerator}s" if x.denominator == 1 else f"{x.numerator}/{x.denominator}s"


def probe(path):
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    a = [s for s in j["streams"] if s["codec_type"] == "audio"]
    num, den = map(int, v["r_frame_rate"].split("/"))
    fd = Fraction(den, num)
    created = j["format"].get("tags", {}).get("creation_time")
    if not created:
        return None
    frames = int(v.get("nb_frames") or round(Fraction(j["format"]["duration"]) / fd))
    return {
        "path": path, "fd": fd, "w": v["width"], "h": v["height"],
        "dur": frames * fd,
        "t": datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp(),
        "audio": a and (len(a), a[0].get("channels", 2), a[0].get("sample_rate", "48000")),
    }


SR = 8000


def audio(path, start=0.0, dur=None):
    import numpy as np
    t = ["-t", f"{dur:.3f}"] if dur else []
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", *t, "-i", str(path), "-map", "0:a:0",
                          "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def xcorr(a, b, min_lag=None, max_lag=None):
    """Where b starts within a (seconds) and match strength, via GCC-PHAT (robust to different mics)."""
    import numpy as np
    n = 1 << (len(a) + len(b)).bit_length()
    r = np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n))
    c = np.fft.irfft(r / (np.abs(r) + 1e-12), n)
    c = np.r_[c[n - len(b) + 1:], c[:len(a)]]  # lags -len(b)+1 .. len(a)-1
    lo = len(b) - 1 + (min_lag if min_lag is not None else -len(b) + 1)
    hi = len(b) - 1 + (max_lag if max_lag is not None else len(a) - 1)
    k = lo + int(c[lo:hi + 1].argmax())
    return (k - len(b) + 1) / SR, c[k] / c.std()


def find_offset(a_path, b_path):
    """Seconds to add to camera B times, from one overlapping pair."""
    lag, strength = xcorr(audio(a_path), audio(b_path))
    print(f"B clip starts {lag:.3f}s into A clip (match strength {strength:.0f})")
    return probe(Path(a_path).resolve())["t"] + lag - probe(Path(b_path).resolve())["t"]


def refine(b, a_clips, window=4.0, min_strength=10, seg=60):
    """Fine-tune a B clip's start against the A clip it overlaps most; returns (shift or None, message)."""
    best = max(a_clips, key=lambda a: min(b["t"] + float(b["dur"]), a["t"] + float(a["dur"])) - max(b["t"], a["t"]))
    ov_start = max(b["t"], best["t"])
    ov = min(b["t"] + float(b["dur"]), best["t"] + float(best["dur"])) - ov_start
    if ov < 5:
        return None, "no overlapping A clip"
    b_from = ov_start - b["t"]
    a_from = max(0.0, ov_start - best["t"] - window)
    bb = audio(b["path"], b_from, min(ov, seg))
    aa = audio(best["path"], a_from, len(bb) / SR + 2 * window)
    lag, strength = xcorr(aa, bb, 0, max(0, len(aa) - len(bb)))
    if strength < min_strength:
        return None, f"weak match ({strength:.0f})"
    shift = best["t"] + a_from + lag - b_from - b["t"]
    b["t"] += shift
    return shift, f"{shift:+.2f}s vs {best['path'].stem} (strength {strength:.0f})"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--name", default="Aligned Project", help="event/project name")
    ap.add_argument("-o", "--output", type=Path, help="output file (default: NAME.fcpxml)")
    ap.add_argument("--b-offset", type=float, default=0, help="seconds to add to camera B times (clock drift fix)")
    ap.add_argument("--sync-pair", nargs=2, metavar=("A_CLIP", "B_CLIP"),
                    help="overlapping A and B clips to compute --b-offset from by matching audio")
    ap.add_argument("--no-refine", action="store_true", help="skip per-clip audio sync of B clips")
    ap.add_argument("--max-gap", type=float, default=2, help="max seconds of nothing-rolling between clips (default 2)")
    a = ap.parse_args()
    if a.sync_pair:
        a.b_offset = find_offset(*a.sync_pair)
        print(f"using --b-offset {a.b_offset:.3f}")

    clips = {"A": [], "B": []}
    for f in sorted(f for p in a.inputs for f in (p.iterdir() if p.is_dir() else [p])
                    if f.suffix.lower() in EXTS and not f.name.startswith(".")):
        cam = CAM.search(f.stem)
        c = cam and probe(f.resolve())
        if not c:
            print(f"skipping (no camera letter or creation time): {f.name}", file=sys.stderr)
            continue
        c["t"] += a.b_offset if cam[1] == "B" else 0
        clips[cam[1]].append(c)
    if not clips["A"]:
        sys.exit("no camera A clips found")
    if not a.no_refine:
        bs, log = sorted(clips["B"], key=lambda c: c["t"]), {}
        for c in bs:
            c["shift"], log[c["path"]] = refine(c, clips["A"])
        good = [c for c in bs if c["shift"] is not None]
        for c in bs:
            if c["shift"] is None and good:
                # Nearby matched clips' shifts estimate the clock drift here.
                near = sorted(good, key=lambda g: abs(g["t"] - c["t"]))[:5]
                c["t"] += statistics.median(g["shift"] for g in near)
                shift, msg = refine(c, clips["A"], window=1.5, min_strength=7)
                log[c["path"]] += f", retried: {msg}" if shift is not None else ", used nearby drift"
        for c in bs:
            print(f"{c['path'].stem}: {log[c['path']]}")
    for c in clips["A"] + clips["B"]:
        c["end"] = c["t"] + float(c["dur"])
    clips["A"].sort(key=lambda c: c["t"])

    # Collapse stretches where nothing was recording.
    allc = sorted(clips["A"] + clips["B"], key=lambda c: c["t"])
    t0, end, dead = allc[0]["t"], allc[0]["t"], []
    for c in allc:
        if c["t"] > end:
            dead.append((end, c["t"]))
        end = max(end, c["end"])
    seq = clips["A"][0]
    sfd = seq["fd"]

    def frame(t):
        t -= t0 + sum(max(0, b - x - a.max_gap) for x, b in dead if b <= t)
        return round(Fraction(t) / sfd)

    def nframes(c):
        return int(c["dur"] / sfd)

    # Primary storyline: A clips with gaps between them.
    spine, cur = [], 0
    for c in clips["A"]:
        s = frame(c["t"])
        if s > cur:
            spine.append({"s": cur, "d": s - cur, "clip": None, "kids": []})
        s = max(s, cur)
        spine.append({"s": s, "d": nframes(c), "clip": c, "kids": []})
        cur = s + nframes(c)
    total = max([cur] + [frame(c["t"]) + nframes(c) for c in clips["B"]])
    if total > cur:
        spine.append({"s": cur, "d": total - cur, "clip": None, "kids": []})
    for c in clips["B"]:
        s = frame(c["t"])
        next(p for p in spine if p["s"] <= s < p["s"] + p["d"])["kids"].append((s, c))

    # Resources.
    fmts, res = {}, []
    for i, c in enumerate(allc, 1):
        key = (c["fd"], c["w"], c["h"])
        if key not in fmts:
            fmts[key] = f"r{len(fmts) + 1}"
            res.append(f'<format id="{fmts[key]}"{fmt_name(*key)} frameDuration="{ts(c["fd"])}" '
                       f'width="{c["w"]}" height="{c["h"]}"/>')
        c["id"], c["fmt"] = f"a{i}", fmts[key]
    for c in allc:
        au = (f' hasAudio="1" audioSources="{c["audio"][0]}" audioChannels="{c["audio"][1]}" '
              f'audioRate="{c["audio"][2]}"' if c["audio"] else "")
        res.append(f'<asset id="{c["id"]}" name={quoteattr(c["path"].stem)} start="0s" '
                   f'duration="{ts(c["dur"])}" hasVideo="1" videoSources="1" format="{c["fmt"]}"{au}>'
                   f'<media-rep kind="original-media" src={quoteattr(c["path"].as_uri())}/></asset>')

    # Timeline.
    out = []
    for p in spine:
        c = p["clip"]
        kids = "".join(
            f'<asset-clip ref="{k["id"]}" lane="1" offset="{ts((s - p["s"]) * sfd)}" '
            f'name={quoteattr(k["path"].stem)} duration="{ts(nframes(k) * sfd)}"/>'
            for s, k in p["kids"])
        attrs = f'offset="{ts(p["s"] * sfd)}" duration="{ts(p["d"] * sfd)}"'
        if c:
            out.append(f'<asset-clip ref="{c["id"]}" {attrs} name={quoteattr(c["path"].stem)}>{kids}</asset-clip>')
        else:
            out.append(f'<gap name="Gap" {attrs}>{kids}</gap>')

    name = quoteattr(a.name)
    dest = a.output or Path(f"{a.name}.fcpxml")
    dest.write_text("\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10">',
        "<resources>", *res, "</resources>",
        f'<event name={name}><project name={name}>'
        f'<sequence format="{seq["fmt"]}" duration="{ts(total * sfd)}" tcStart="0s" tcFormat="NDF" '
        f'audioLayout="stereo" audioRate="48k"><spine>',
        *out,
        "</spine></sequence></project></event>\n</fcpxml>\n"]))
    print(f"{len(clips['A'])} A clips, {len(clips['B'])} B clips -> {dest}")


if __name__ == "__main__":
    main()
