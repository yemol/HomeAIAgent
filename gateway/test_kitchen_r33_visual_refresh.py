from pathlib import Path
p=Path(__file__).with_name("companion_gateway.py").read_text()
assert "A3.0b FIX1 R49 · 小K COOKING COCKPIT" in p
assert "/* R35 · Approved mockup UI" in p
assert "--accent:#2e7654" in p
assert ".modern-header .brand{font-size:58px!important" in p
assert ".home-hero h2{font-size:58px!important" in p
assert ".home-channel-card{min-height:154px!important" in p
assert ".food-title{font-size:46px!important" in p
assert ".cook-step-text{font-size:34px!important" in p
assert ".global-bottom-nav{max-width:760px!important" in p
start=p.index("html = r\'\'\'")
end=p.index("</html>\'\'\'",start)+len("</html>\'\'\'")
html=p[start:end]
assert "http://images." not in html and "https://images." not in html
print("KitchenTerminal R35 approved mockup UI regression: PASS")
