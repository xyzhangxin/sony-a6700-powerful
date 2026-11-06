"""Smoke-check the built app after moving it, with no camera or authorization access.

The physical worker is limited to its environment check. No inspect/unlock/close
command is sent to it. Simulation uses the normal fixed JSON workflow.
"""
import argparse
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
import tempfile
from threading import Thread
import time


def check_worker(resources, physical):
    config=json.loads((resources/'runtime.json').read_text())
    python=resources/config['python']
    command=[str(python),'-I','-S','-B','-u',str(resources/'worker.py')]+(['--physical'] if physical else [])
    # Inject unusable external paths deliberately: isolated mode must ignore them.
    environment=dict(os.environ,PATH='/usr/bin:/bin:/usr/sbin:/sbin',
                     PYTHONHOME='/nonexistent/a6700',PYTHONPATH='/nonexistent/a6700')
    events=Queue()
    with tempfile.TemporaryDirectory(prefix='a6700-app-check-') as folder:
        worker=subprocess.Popen(command,cwd=folder,env=environment,stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def receive():
            for line in worker.stdout:
                events.put(json.loads(line))
        Thread(target=receive,daemon=True).start()
        def send(command,**extra):
            worker.stdin.write(json.dumps(dict(command=command,**extra))+'\n');worker.stdin.flush()
        def state_until(predicate,timeout=30):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                try:event=events.get(timeout=max(0.01,deadline-time.monotonic()))
                except Empty:break
                if event['type']=='error':raise AssertionError(event)
                if event['type']=='state' and predicate(event['state']):return event['state']
            raise AssertionError('Timed out waiting for bundled worker')
        try:
            state_until(lambda _:True)
            send('environment' if physical else 'reset',**({} if physical else dict(scenario='original')))
            state=state_until(lambda s:s['checked'])
            assert state['environment']['ready'],state['environment']
            assert all(isinstance(check['detail'],str) for check in state['environment']['checks']), 'Native UI requires text check details'
            assert not state['device_access'] and not state['write_attempted']
            if not physical:
                send('acknowledge',value=True);send('prepare')
                send('confirm_preparation',value=True)
                state_until(lambda s:s['ready'])
                send('inspect');state_until(lambda s:not s['busy'] and s['compatible'])
                send('select',mode='none');state=state_until(lambda s:not s['busy'])
                assert all(not row['selected'] for row in state['features']),state
                send('select',mode='missing');state=state_until(lambda s:not s['busy'])
                assert all(row['selected'] == row.get('available', True) for row in state['features']),state
                send('backup');state=state_until(lambda s:not s['busy'] and s['result'] is not None)
                assert state['result']['status']=='backup-completed',state['result']
                backup_id=state['result']['backup_id']
                assert backup_id.endswith('.a6700backup') and state['backups'],state
                send('home');state_until(lambda s:s['phase']=='home')
                send('unlock');state=state_until(lambda s:not s['busy'] and s['result'] is not None)
                assert state['simulation'] and not state['device_access'] and not state['write_attempted']
                assert state['result']['status']=='completed',state['result']
                assert state['result']['aftercare']['simulation']
                assert len(state['result']['aftercare']['features'])==6
                assert not state['compatible'] and not state['ready']
                send('restart_preparation');state_until(lambda s:s['phase']=='preparation')
                send('confirm_preparation',value=True);state_until(lambda s:s['ready'])
                send('inspect');state_until(lambda s:not s['busy'] and s['phase']=='home')
                send('select',mode='all');state_until(lambda s:all(row['selected'] for row in s['features'] if row.get('available',True)))
                send('restore',backup_id=backup_id)
                state=state_until(lambda s:not s['busy'] and s['result'] is not None)
                assert state['result']['status']=='restored',state['result']
                assert state['result']['success'] and state['backups']
                send('restart_preparation')
                state=state_until(lambda s:s['phase']=='preparation')
                assert not state['ready'] and not state['compatible'] and not state['features']
            return dict(mode='physical-environment-only' if physical else 'simulation-backup-restore',
                        ready=state['environment']['ready'],device_access=state['device_access'],
                        write_attempted=state['write_attempted'])
        finally:
            worker.stdin.close()
            try:worker.wait(timeout=8)
            except subprocess.TimeoutExpired:worker.terminate();worker.wait(timeout=5)
            errors=worker.stderr.read()
            worker.stdout.close();worker.stderr.close()
            if worker.returncode:raise AssertionError('Bundled worker failed: '+errors[-1500:])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app',type=Path)
    args=parser.parse_args()
    subprocess.run(['codesign','--verify','--deep','--strict',str(args.app)],check=True)
    print(json.dumps([check_worker(args.app.resolve()/'Contents/Resources',physical)
                      for physical in (False,True)],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
