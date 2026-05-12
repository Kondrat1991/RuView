import asyncio, json, websockets

async def check():
    try:
        async with websockets.connect('ws://localhost:3001/ws/sensing', open_timeout=5) as ws:
            for _ in range(20):
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                d = json.loads(msg)
                if d.get('type') == 'sensing_update':
                    nodes = d.get('nodes', [])
                    nf = d.get('node_features') or []
                    src = d.get('source', '?')
                    ids = [n.get('node_id', n.get('id')) for n in nodes]
                    print(f"source={src}  nodes={len(nodes)}  node_ids={ids}  node_features={len(nf)}")
                    print(f"classification={d.get('classification', {}).get('motion_level')}  presence={d.get('classification', {}).get('presence')}")
                    return
        print("No sensing_update received")
    except Exception as e:
        print("error:", e)

asyncio.run(check())
