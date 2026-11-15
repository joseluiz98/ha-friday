from friday_agent_core.ota import manifest as m, order as o

def man(req=1, minp=1):
    return m.parse({"schema": 1, "protocol": {"satellite": 1, "server": 2, "update": 1},
                    "min_protocol": {"satellite": minp}, "requires": {"satellite": {"server": req}},
                    "heavy": [], "components": {c: [c] for c in m.COMPONENT_ORDER}})

def test_server_plan_fixed_order_without_satellite():
    assert o.server_plan(["satellite", "server-agent", "brain-skills", "container"]) == \
        ["brain-skills", "container", "server-agent"]

def test_satellite_waits_for_server_protocol():
    assert not o.satellite_ready(man(req=2), server_protocol_installed=1)
    assert o.satellite_ready(man(req=2), server_protocol_installed=2)

def test_server_accepts_window():
    assert o.accepts(man(minp=1), 1) and not o.accepts(man(minp=2), 1)
