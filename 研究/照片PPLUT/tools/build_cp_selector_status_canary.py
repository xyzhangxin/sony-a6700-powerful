"""Compile a constant result in the query's formal selector/status fields."""
from pathlib import Path
import build_cp_entry_echo_canary as template

_source=Path(template.__file__).read_text(encoding='utf-8').rsplit("if __name__=='__main__':",1)[0]
_source=_source.replace('cp_entry_echo_canary','cp_selector_status_canary').replace('CP-entry-echo-build','CP-selector-status-build')
for _old,_new in (
    ('MARKER,TAG=0x31455043,0x314b4341','SELECTOR,STATUS=0x43,0x5a'),
    ("b['echo_marker']!=hex(MARKER)","b['private_selector']!=SELECTOR"),
    ("b['echo_tag']!=hex(TAG)","b['private_status']!=STATUS"),
    ('echo_marker=hex(MARKER),echo_tag=hex(TAG)','private_selector=SELECTOR,private_status=STATUS')):
    if _source.count(_old)!=1:raise ValueError('Fixed builder boundary changed')
    _source=_source.replace(_old,_new)
exec(compile(_source,str(Path(__file__))+':same_pinned_compiler_and_geometry','exec'),globals())

if __name__=='__main__':main()
