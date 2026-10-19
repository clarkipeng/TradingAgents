from pathlib import Path
import json, subprocess, datetime, os
from sightmesh.cdesktop import CdesktopClient
from sightmesh.succession import OwnershipStore
root=Path(__file__).resolve().parent
state=json.loads((root/'program.json').read_text())
def record(outcome):
    (root/'last-fallback.json').write_text(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'outcome':outcome}))
    print(outcome)
if state.get('state') in {'complete','blocked','cancelled'}:
    record('program_terminal')
    (Path('/Users/clarkpeng/Library/LaunchAgents')/(state['fallback_label']+'.plist')).unlink(missing_ok=True)
    subprocess.run(['launchctl','bootout',f"gui/{os.getuid()}/{state['fallback_label']}"],capture_output=True)
    raise SystemExit(0)
if state.get('continuation') == 'root_owned':
    record('root_owned_no_wake')
    raise SystemExit(0)
if state.get('continuation') == 'awaiting_integration':
    record('review_pending_no_wake')
    raise SystemExit(0)
try:
    client=CdesktopClient('http://127.0.0.1:3210')
    processes={sid:client.execution_processes(sid) for sid in [state['lead_session'],*state.get('children',[])]}
    active=any(p.get('status')=='running' and p.get('run_reason')!='devserver' for rows in processes.values() for p in rows)
    if active:
        record('work_running_no_wake');raise SystemExit(0)
    message=('Audit continuation guard: reconcile the child handoffs and coverage ledger. Resume missing owned work, integrate ready commits, or record a real external blocker. No unchanged status narration or repeated full suite. Update program.json children on owner changes; set terminal state and retire guard when done. Child terminal events are primary.')
    OwnershipStore().assert_deliverable(state['lead_session'])
    process_ids={str(p['id']) for p in processes[state['lead_session']]}
    if any(str(a.get('execution_process_id')) in process_ids for a in client.pending_approvals()):
        record('approval_pending_no_wake');raise SystemExit(0)
    slot=int(datetime.datetime.now(datetime.timezone.utc).timestamp()) // state['fallback_seconds']
    client.send(state['lead_session'],message,dedupe_key=f"{state['program']}:fallback:{slot}")
    outcome='wake_queued'

except Exception as exc:
    outcome='callback_timed_out' if isinstance(exc,subprocess.TimeoutExpired) else 'callback_failed'
record(outcome)
