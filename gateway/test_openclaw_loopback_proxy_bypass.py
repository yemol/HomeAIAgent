#!/usr/bin/env python3
import companion_gateway as g

old = g.OPENCLAW_BASE_URL
try:
    g.OPENCLAW_BASE_URL = "http://127.0.0.1:18790"
    assert g._openclaw_http_trust_env() is False
    g.OPENCLAW_BASE_URL = "http://localhost:18790"
    assert g._openclaw_http_trust_env() is False
    g.OPENCLAW_BASE_URL = "https://example.com"
    assert g._openclaw_http_trust_env() is True
finally:
    g.OPENCLAW_BASE_URL = old
print("OpenClaw loopback proxy bypass regression: PASS")
