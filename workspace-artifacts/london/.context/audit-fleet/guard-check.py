import json,runpy,sys,tempfile,types
from pathlib import Path
from unittest.mock import patch
source=Path('.context/audit-fleet/wake_if_stalled.py').read_text()
for name,processes,approval,wanted in [('running',[{'id':'p','status':'running','run_reason':'codingagent'}],False,'work_running_no_wake'),('historical',[{'id':'p','status':'completed','run_reason':'codingagent'}],False,'wake_queued'),('devserver',[{'id':'p','status':'running','run_reason':'devserver'}],False,'wake_queued'),('approval',[{'id':'p','status':'completed','run_reason':'codingagent'}],True,'approval_pending_no_wake'),('review',[],False,'review_pending_no_wake'),('root',[],False,'root_owned_no_wake')]:
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);script=root/'guard.py';script.write_text(source)
        (root/'program.json').write_text(json.dumps({'state':'running','lead_session':'fixture','children':[],'fallback_seconds':900,'program':'fixture','continuation':'awaiting_integration' if name=='review' else ('root_owned' if name=='root' else 'active')}))
        module=types.ModuleType('sightmesh.cdesktop');sent=[]
        class Client:
            def __init__(self,*args):pass
            def execution_processes(self,*args,**kwargs):return processes
            def pending_approvals(self):return [{'execution_process_id':'p'}] if approval else []
            def send(self,*args,**kwargs):sent.append(kwargs)
        module.CdesktopClient=Client
        succession=types.ModuleType('sightmesh.succession')
        class Ownership:
            def assert_deliverable(self,*args):pass
        succession.OwnershipStore=Ownership
        with patch.dict(sys.modules,{'sightmesh.cdesktop':module,'sightmesh.succession':succession}):
            try:runpy.run_path(str(script))
            except SystemExit:pass
        assert json.loads((root/'last-fallback.json').read_text())['outcome']==wanted
        assert len(sent)==(1 if wanted=='wake_queued' else 0)
print('guard_fixtures_passed: 6')
