#!/usr/bin/env python3
"""
Capture 30s of live sensing_update frames from the WebSocket,
save to JSON, then print a summary interpretation.
"""

import asyncio
import json
import math
import sys
import time
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
    import websockets

WS_URL = "ws://localhost:3001/ws/sensing"
DURATION = 30
OUTPUT = Path("data/recordings")


def amplitude_entropy(values):
    """Variance of consecutive differences — measures CSI noise level."""
    if len(values) < 4:
        return 0.0
    diffs = [abs(values[i] - values[i-1]) for i in range(1, len(values))]
    mean = sum(diffs) / len(diffs)
    var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
    return round(math.sqrt(var), 4)


async def capture():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    out_path = OUTPUT / f"capture-{ts}.json"

    frames = []
    print(f"Connecting to {WS_URL} ...")

    async with websockets.connect(WS_URL) as ws:
        print(f"Connected. Capturing for {DURATION}s  →  {out_path}")
        start = time.time()
        while time.time() - start < DURATION:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(raw)
                if data.get("type") == "sensing_update" or data.get("msg_type") == "sensing_update":
                    frames.append({
                        "t": round(time.time() - start, 3),
                        "source": data.get("source"),
                        "nodes": data.get("nodes", []),
                        "classification": data.get("classification", {}),
                        "features": data.get("features", {}),
                    })
                    elapsed = time.time() - start
                    print(f"\r  {len(frames):4d} frames  {elapsed:.1f}s / {DURATION}s", end="", flush=True)
            except asyncio.TimeoutError:
                continue

    print(f"\nDone. {len(frames)} frames captured.")

    with open(out_path, "w") as f:
        json.dump(frames, f, indent=2)
    print(f"Saved → {out_path}")

    return frames, out_path


def interpret(frames, out_path):
    if not frames:
        print("No frames captured.")
        return

    print("\n" + "="*60)
    print("INTERPRETATION")
    print("="*60)

    # ── Per-node stats ──────────────────────────────────────────
    node_stats = {}
    for f in frames:
        for n in f.get("nodes", []):
            nid = n.get("node_id")
            amp = n.get("amplitude", [])
            if nid is None or not amp:
                continue
            if nid not in node_stats:
                node_stats[nid] = {"frames": 0, "entropies": [], "rssi_vals": []}
            node_stats[nid]["frames"] += 1
            node_stats[nid]["entropies"].append(amplitude_entropy(amp))
            rssi = n.get("rssi_dbm", 0)
            if rssi != 0:
                node_stats[nid]["rssi_vals"].append(rssi)

    print(f"\nNodes seen: {sorted(node_stats.keys())}")
    print(f"Total frames: {len(frames)}")
    duration = frames[-1]["t"] if frames else 0
    print(f"Duration: {duration:.1f}s")
    print(f"Avg frame rate: {len(frames)/max(duration,1):.1f} fps\n")

    for nid in sorted(node_stats.keys()):
        ns = node_stats[nid]
        entropies = ns["entropies"]
        mean_ent = sum(entropies) / len(entropies)
        # variance of entropy over time
        ent_var = sum((e - mean_ent)**2 for e in entropies) / len(entropies)
        ent_std = math.sqrt(ent_var)
        rssi_vals = ns["rssi_vals"]
        mean_rssi = sum(rssi_vals)/len(rssi_vals) if rssi_vals else 0

        is_real = mean_ent > 1.0 and ent_std > 0.3
        print(f"  Node {nid}:")
        print(f"    Frames:          {ns['frames']}")
        print(f"    Avg entropy:     {mean_ent:.3f}  (real > 1.0)")
        print(f"    Entropy std-dev: {ent_std:.3f}  (real > 0.3)")
        print(f"    Avg RSSI:        {mean_rssi:.1f} dBm" if rssi_vals else "    Avg RSSI:        n/a")
        print(f"    Assessment:      {'✅ REAL ESP32 data' if is_real else '⚠️  SIMULATED / no hardware'}")

    # ── Motion / classification timeline ───────────────────────
    print("\n  Motion timeline (sampled every 5s):")
    bucket = 5
    buckets = {}
    for f in frames:
        b = int(f["t"] // bucket) * bucket
        cl = f.get("classification", {})
        level = cl.get("motion_level", "?")
        buckets.setdefault(b, []).append(level)

    for b in sorted(buckets):
        counts = {}
        for v in buckets[b]:
            counts[v] = counts.get(v, 0) + 1
        dominant = max(counts, key=counts.get)
        bar = {"active": "🏃 MOVEMENT", "present_still": "🧍 PRESENT", "absent": "⬜ EMPTY"}.get(dominant, dominant)
        print(f"    t={b:3d}-{b+bucket}s  →  {bar}  ({counts})")

    # ── Feature snapshot ────────────────────────────────────────
    if frames:
        last_feat = frames[-1].get("features", {})
        print(f"\n  Final feature snapshot:")
        for k in ["variance", "motion_band_power", "breathing_band_power", "mean_rssi", "dominant_freq_hz"]:
            v = last_feat.get(k)
            if v is not None:
                print(f"    {k}: {v:.4f}")

    print(f"\nRaw data: {out_path}")


async def main():
    frames, out_path = await capture()
    interpret(frames, out_path)


if __name__ == "__main__":
    asyncio.run(main())
