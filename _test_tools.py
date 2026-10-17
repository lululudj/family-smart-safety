import tower_server as t

print("mcp API:", t._API)

print("ping:", t.ping())
print("status:", t.tower_status())
hw = t.tower_hardware()
print("hostname:", hw["主机名"], "| arch:", hw["架构"])
print("capabilities:", len(t.list_capabilities()))
print("--- TOOLS OK ---")
