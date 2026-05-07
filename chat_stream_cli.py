import json, requests
URL = "http://localhost:8000/chat/stream"
tid = None
print("Seagate chat CLI. Type 'exit' to quit.")
while True:
    msg = input("You: ").strip()
    if msg.lower() in {"exit", "quit"}: break
    if not msg: continue
    body = {"input": {"messages": [{"role": "user", "content": msg}], "channel": "web"}, "thread_id": tid}
    with requests.post(URL, json=body, stream=True, timeout=300) as r:
        r.raise_for_status()
        print("Bot: ", end="", flush=True); final = ""
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "): continue
            ev = json.loads(line[6:]); tid = ev.get("thread_id", tid); t = ev.get("type")
            if t == "token":
                s = ev.get("content", ""); final += s; print(s, end="", flush=True)
            elif t == "end_of_response":
                if not final: print(ev.get("content", ""), end="", flush=True)
            elif t in {"node_start", "node_end"}:
                print(f"\n[{t}:{ev.get('node')}]", end="", flush=True)
            elif ev.get("error"):
                print(f"\n[error] {ev['error']}", end="", flush=True)
        print()
