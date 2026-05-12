import asyncio, json, websockets

async def check():
    async with websockets.connect('ws://localhost:3001/ws/sensing', open_timeout=5) as ws:
        for _ in range(3):
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            d = json.loads(msg)
            print("source:", d.get("source"), "| nodes:", [n.get("node_id") for n in d.get("nodes",[])])

asyncio.run(check())
