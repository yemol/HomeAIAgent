from pathlib import Path
src = Path(__file__).with_name('companion_gateway.py').read_text(encoding='utf-8')
assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in src
assert '/* R35 · Approved mockup UI' in src
for token in [
    '.modern-header .brand{font-size:58px!important',
    '.home-hero h2{font-size:58px!important',
    '.home-channel-card{min-height:154px!important',
    '.today-row{grid-template-columns:52px',
    '.food-title{font-size:46px!important',
    '.prep-check span{font-size:18px!important',
    '.cook-step-text{font-size:34px!important',
    '.global-bottom-nav{max-width:760px!important',
    '.idle-timer-time{font-size:68px!important',
]:
    assert token in src, token
assert '/* R33 · 小K VISUAL REFRESH' not in src
print('PASS: R35 approved mockup UI CSS is present and overrides legacy styling')
