import asyncio, json, time, websockets

WS = "ws://localhost:3001/ws/sensing"
DURATION = 2.0

def frame_fingerprint(d):
    """Unique key for a frame — changes each time real ESP32 data arrives."""
    nodes = d.get("nodes", [])
    # Use node amplitudes first subcarrier + last subcarrier as fingerprint
    parts = []
    for n in sorted(nodes, key=lambda x: x.get("node_id", 0)):
        amp = n.get("amplitude", [])
        if amp:
            parts.append(f"{n.get('node_id')}:{amp[0]:.3f}:{amp[-1]:.3f}:{len(amp)}")
    return "|".join(parts) if parts else None

async def collect():
    frames = []
    seen_fingerprints = set()
    t0 = time.time()
    async with websockets.connect(WS, open_timeout=5) as ws:
        while time.time() - t0 < DURATION:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                d = json.loads(msg)
                src = d.get("source", "")
                if d.get("type") != "sensing_update" or not src.startswith("esp32"):
                    continue
                fp = frame_fingerprint(d)
                if fp is None or fp in seen_fingerprints:
                    continue  # stale re-broadcast — skip
                seen_fingerprints.add(fp)
                frames.append(d)
            except asyncio.TimeoutError:
                break

    elapsed = time.time() - t0
    print(f"\n=== {elapsed:.1f}s  |  {len(frames)} frames  |  {len(frames)/max(elapsed,0.001):.1f} fps ===\n")

    nodes_seen = {}
    for f in frames:
        for n in f.get("nodes", []):
            nid = n.get("node_id", n.get("id"))
            if nid not in nodes_seen:
                nodes_seen[nid] = {"frames": 0, "amp_lens": [], "rssi": []}
            ns = nodes_seen[nid]
            ns["frames"] += 1
            amp = n.get("amplitude", [])
            if amp:
                ns["amp_lens"].append(len(amp))
                ns["rssi"].append(n.get("rssi_dbm", 0))

    print(f"Nodes seen: {sorted(nodes_seen.keys())}\n")
    for nid, ns in sorted(nodes_seen.items()):
        avg_sub = sum(ns["amp_lens"]) / len(ns["amp_lens"]) if ns["amp_lens"] else 0
        avg_rssi = sum(ns["rssi"]) / len(ns["rssi"]) if ns["rssi"] else 0
        print(f"  Node {nid}: {ns['frames']} frames  |  avg subcarriers={avg_sub:.0f}  |  avg RSSI={avg_rssi:.1f} dBm")

    if frames:
        last = frames[-1]
        cl = last.get("classification", {})
        vs = last.get("vital_signs") or {}
        print(f"\nLast frame classification:")
        print(f"  motion_level = {cl.get('motion_level')}  |  presence = {cl.get('presence')}  |  confidence = {cl.get('confidence', 0):.2f}")
        print(f"  breathing_rate = {vs.get('breathing_rate_bpm')}  |  breath_conf = {vs.get('breathing_confidence', 0):.2f}")

asyncio.run(collect())
