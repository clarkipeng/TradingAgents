import json
from argparse import Namespace
from pathlib import Path
from sightmesh.cdesktop import CdesktopClient
from sightmesh.cli.spawn import _profile_selection, _primary_session_id, _workspace_id, _workspace_container
from sightmesh.leases import LeaseStore
from sightmesh.escalation import EscalationStore, detect_launcher, parse_signal_conditions
from sightmesh import routing
root=Path('/Users/clarkpeng/conductor/workspaces/TradingAgents/london/.context/audit-fleet')
receipt=root/'sol-execution-spawn.json'
if receipt.exists():
 print('existing_receipt_requires_reconciliation');raise SystemExit(2)
c=CdesktopClient('http://127.0.0.1:3210')
repo=Path('/Users/clarkpeng/Documents/Code/TradingAgents')
leases=LeaseStore();leases.assert_spawn_allowed(repo,use_worktree=True)
p=_profile_selection(Namespace(profile_name='codex-sol-high',executor=None,provider=None,model=None,reasoning=None),c)
r=c.spawn_workspace(name='ta-sol-execution-v2',repo_path=repo,target_branch='cdt/8224-ta-audit-lead',executor=p.executor,prompt=(root/'sol-execution.md').read_text(),use_worktree=True,permission_policy='BYPASS_PERMISSIONS',model=p.model,reasoning=p.reasoning,provider_id=p.provider_id)
wid=_workspace_id(r);sid=_primary_session_id(r);container=_workspace_container(r,c,wid)
receipt.write_text(json.dumps({'workspace_id':wid,'session_id':sid,'worktree':str(container/repo.name)}))
leases.acquire('cdesktop-spawn:ta-sol-execution-v2',repo,container/repo.name,ttl_seconds=3600,workspace_id=wid,session_id=sid)
c.set_parent(sid,'df867712-0bb8-43b5-87cd-92bb3f3127d5')
e=EscalationStore();e.record_launcher(session_id=sid,workspace_id=wid,identity=detect_launcher());e.set_signal_policy(sid,parse_signal_conditions('terminal'))
routing.enable(wid)
f=root/'program.json';state=json.loads(f.read_text());state['children']=list(dict.fromkeys([*state.get('children',[]),sid]));f.write_text(json.dumps(state,indent=2)+'\n')
print(receipt.read_text())
