#!/usr/bin/env python3
"""
Analyze a capture-*.json file for human movement signatures.
Uses CSI amplitude variance, inter-frame delta, and frequency analysis.
"""

import json
import math
import sys
from pathlib import Path
from collections import defaultdict

# ── Load ─────────────────────────────────────────────────────────────────────

captures = sorted(Path("data/recordings").glob("capture-*.json"))
if not captures:
    print("No capture files found in data/recordings/")
    sys.exit(1)

path = captures[-1]
print(f"Analyzing: {path}\n")

with open(path) as f:
    frames = json.load(f)

print(f"Total frames: {len(frames)}")
print(f"Duration:     {frames[-1]['t']:.1f}s")
print(f"Frame rate:   {len(frames)/frames[-1]['t']:.1f} fps")

# ── Per-node frame series ─────────────────────────────────────────────────────

node_series = defaultdict(list)  # node_id -> [{t, amplitude, features, classification}]

for f in frames:
    cl = f.get("classification", {})
    feat = f.get("features", {})
    for n in f.get("nodes", []):
        nid = n.get("node_id")
        amp = n.get("amplitude", [])
        if nid is None or not amp:
            continue
        node_series[nid].append({
            "t": f["t"],
            "amp": amp,
            "motion_level": cl.get("motion_level", "?"),
            "presence": cl.get("presence", False),
            "variance": feat.get("variance", 0),
            "motion_band": feat.get("motion_band_power", 0),
            "breath_band": feat.get("breathing_band_power", 0),
            "dominant_hz": feat.get("dominant_freq_hz", 0),
        })

nodes = sorted(node_series.keys())
print(f"Nodes:        {nodes}\n")


# ── Helper functions ──────────────────────────────────────────────────────────

def mean(vals):
    return sum(vals) / len(vals) if vals else 0

def stddev(vals):
    if len(vals) < 2: return 0
    m = mean(vals)
    return math.sqrt(sum((v - m)**2 for v in vals) / len(vals))

def interframe_delta(series):
    """Mean absolute change in mean-amplitude between consecutive frames."""
    deltas = []
    for i in range(1, len(series)):
        a = mean(series[i-1]["amp"])
        b = mean(series[i]["amp"])
        deltas.append(abs(b - a))
    return deltas

def subcarrier_variance_series(series):
    """Per-frame: variance across all 56 subcarrier amplitudes."""
    return [stddev(s["amp"]) for s in series]

def sliding_window_motion(series, window=20):
    """
    For each position, compute variance of mean-amplitudes over a window.
    High value = someone moving, low = static.
    """
    means = [mean(s["amp"]) for s in series]
    result = []
    for i in range(len(means)):
        start = max(0, i - window)
        window_vals = means[start:i+1]
        result.append((series[i]["t"], stddev(window_vals)))
    return result


# ── Per-node analysis ─────────────────────────────────────────────────────────

print("=" * 60)
print("PER-NODE ANALYSIS")
print("=" * 60)

for nid in nodes:
    s = node_series[nid]
    print(f"\n  ── Node {nid} ({len(s)} frames) ──────────────────────────")

    # Subcarrier variance
    sub_vars = subcarrier_variance_series(s)
    print(f"  Subcarrier variance:  avg={mean(sub_vars):.2f}  std={stddev(sub_vars):.2f}  "
          f"min={min(sub_vars):.2f}  max={max(sub_vars):.2f}")

    # Inter-frame delta
    deltas = interframe_delta(s)
    print(f"  Inter-frame delta:    avg={mean(deltas):.3f}  std={stddev(deltas):.3f}  "
          f"max={max(deltas):.3f}")

    # Motion band power
    mb = [f["motion_band"] for f in s]
    print(f"  Motion band power:    avg={mean(mb):.2f}  std={stddev(mb):.2f}")

    # Breathing band power
    bb = [f["breath_band"] for f in s]
    print(f"  Breathing band power: avg={mean(bb):.2f}  std={stddev(bb):.2f}")

    # Dominant frequency
    hz = [f["dominant_hz"] for f in s if f["dominant_hz"] > 0]
    if hz:
        dom = mean(hz)
        print(f"  Dominant frequency:   {dom:.2f} Hz  →  ", end="")
        if 0.1 <= dom <= 0.5:
            print(f"breathing range (~{dom*60:.0f} breaths/min)")
        elif 0.8 <= dom <= 2.5:
            print(f"heartbeat range (~{dom*60:.0f} bpm)")
        elif dom > 2.5:
            print(f"motion / walking range")
        else:
            print("very low / noise")

    # Movement events: frames where inter-frame delta > mean + 2*std
    if deltas:
        threshold = mean(deltas) + 2 * stddev(deltas)
        events = [(s[i+1]["t"], d) for i, d in enumerate(deltas) if d > threshold]
        print(f"  Movement spikes:      {len(events)} events  (threshold: delta > {threshold:.3f})")
        for t, d in events[:10]:
            print(f"    t={t:.2f}s  delta={d:.3f}")
        if len(events) > 10:
            print(f"    ... and {len(events)-10} more")


# ── Cross-node correlation ────────────────────────────────────────────────────

print("\n" + "=" * 60)
print("CROSS-NODE ANALYSIS")
print("=" * 60)

# Resample to 1s buckets, average subcarrier variance per bucket per node
bucket_size = 1.0
node_buckets = {}
for nid in nodes:
    buckets = defaultdict(list)
    for s in node_series[nid]:
        b = int(s["t"] // bucket_size)
        buckets[b].append(stddev(s["amp"]))
    node_buckets[nid] = {b: mean(v) for b, v in buckets.items()}

all_buckets = sorted(set(b for nb in node_buckets.values() for b in nb))

print(f"\n  Subcarrier variance timeline (1s buckets, by node):\n")
header = f"  {'t':>4s}"
for nid in nodes:
    header += f"  Node{nid:>2d}"
header += "   Motion"
print(header)
print("  " + "-" * (len(header) - 2))

for b in all_buckets:
    t_start = b * bucket_size
    row = f"  {t_start:4.0f}s"
    vals = []
    for nid in nodes:
        v = node_buckets[nid].get(b, 0)
        vals.append(v)
        bar = "▓" * int(v / 2)
        row += f"  {v:5.1f}"
    # Cross-node agreement: are multiple nodes showing high variance simultaneously?
    high_nodes = [nodes[i] for i, v in enumerate(vals) if v > 3.0]
    if len(high_nodes) >= 2:
        row += f"   ← MOVEMENT ({len(high_nodes)} nodes agree)"
    elif len(high_nodes) == 1:
        row += f"   ← signal (Node {high_nodes[0]})"
    else:
        row += "   · static"
    print(row)


# ── Summary verdict ───────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print("VERDICT")
print("=" * 60)

all_deltas = []
for nid in nodes:
    all_deltas += interframe_delta(node_series[nid])

all_sub_vars = []
for nid in nodes:
    all_sub_vars += subcarrier_variance_series(node_series[nid])

movement_buckets = sum(
    1 for b in all_buckets
    if sum(1 for nid in nodes if node_buckets[nid].get(b, 0) > 3.0) >= 2
)

print(f"""
  Avg subcarrier variance:  {mean(all_sub_vars):.2f}
  Avg inter-frame delta:    {mean(all_deltas):.3f}
  Movement buckets (≥2 nodes agree, variance>3): {movement_buckets}/{len(all_buckets)}s

  Interpretation:
""")

avg_var = mean(all_sub_vars)
avg_delta = mean(all_deltas)
move_frac = movement_buckets / max(len(all_buckets), 1)

if avg_var > 4.0 and move_frac > 0.5:
    print("  🏃 ACTIVE MOVEMENT — significant motion detected across multiple nodes")
elif avg_var > 2.5 or move_frac > 0.2:
    print("  🧍 PERSON PRESENT, MOSTLY STILL — minor movements (breathing, fidgeting)")
elif avg_var > 1.5:
    print("  💤 VERY STILL — possible person, minimal movement (sleeping/seated)")
else:
    print("  ⬜ LIKELY EMPTY — very low CSI variance, no movement signature")

breath_hz_all = [f["dominant_hz"] for nid in nodes
                 for f in node_series[nid] if 0.1 < f["dominant_hz"] < 0.7]
if breath_hz_all:
    bpm = mean(breath_hz_all) * 60
    print(f"\n  Breathing estimate:   ~{bpm:.0f} breaths/min  ({mean(breath_hz_all):.2f} Hz)")
    if 12 <= bpm <= 20:
        print("  (Normal resting breathing range ✅)")
    elif bpm > 20:
        print("  (Elevated — active or walking)")
    else:
        print("  (Very slow — sitting very still or noise)")
