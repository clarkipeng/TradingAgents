"""Task-local supported client launch: avoid a fleet-wide lease refresh."""
import json,sys
from pathlib import Path
from sightmesh.cdesktop import CdesktopClient
from sightmesh.cli.spawn import _profile_selection, _primary_session_id, _workspace_id, _workspace_container
from sightmesh.leases import LeaseStore
from sightmesh.escalation import EscalationStore, detect_launcher, parse_signal_conditions
from sightmesh import routing
from argparse import Namespace

kind=sys.argv[1]
assert kind in {'p4','ops'}
name=f'ta-luna-{kind}-resume'
root=Path('/Users/clarkpeng/conductor/workspaces/TradingAgents/london/.context/audit-fleet')
repo=Path('/Users/clarkpeng/Documents/Code/TradingAgents')
c=CdesktopClient('http://127.0.0.1:3210')
existing=[w for w in c.workspaces() if w.get('name')==name]
if existing:
    print(json.dumps({'outcome':'existing_workspace_requires_reconcile','workspace_ids':[w['id'] for w in existing]}));sys.exit(2)
leases=LeaseStore();leases.assert_spawn_allowed(repo,use_worktree=True)
profile=_profile_selection(Namespace(profile_name='codex-luna',executor=None,provider=None,model=None,reasoning=None),c)
r=c.spawn_workspace(name=name,repo_path=repo,target_branch='audit-resume-base-543a370',executor=profile.executor,prompt=(root/f'luna-{kind}-resume.md').read_text(),use_worktree=True,permission_policy='BYPASS_PERMISSIONS',model=profile.model,reasoning=profile.reasoning,provider_id=profile.provider_id)
wid=_workspace_id(r);sid=_primary_session_id(r)
p=root/f'{kind}-targeted-spawn.json';p.write_text(json.dumps(r));p.chmod(0o600)
container=_workspace_container(r,c,wid)
leases.acquire(f'cdesktop-spawn:{name}',repo,container/repo.name,ttl_seconds=3600,workspace_id=wid,session_id=sid)
c.set_parent(sid,'df867712-0bb8-43b5-87cd-92bb3f3127d5')
store=EscalationStore();store.record_launcher(session_id=sid,workspace_id=wid,identity=detect_launcher());store.set_signal_policy(sid,parse_signal_conditions('terminal'))
routing.enable(wid)
p=root/'program.json';program=json.loads(p.read_text());program['children']=list(dict.fromkeys([*program.get('children',[]),sid]));p.write_text(json.dumps(program,indent=2)+'\n')
print(json.dumps({'outcome':'started','workspace_id':wid,'session_id':sid,'worktree':str(container/repo.name)}))
