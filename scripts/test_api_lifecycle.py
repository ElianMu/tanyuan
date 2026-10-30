"""Stage 6 ASGI/WebSocket tests with fake Agent only; no paid services."""
from __future__ import annotations
import asyncio,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from api import server
from api.monitor import manager,monitor,MonitorCallbackHandler,ConnectionManager
from agent import main_agent
from utils import path_utils


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        for name in ['OUTPUT_DIR','UPLOAD_DIR']:
            patcher=patch.object(path_utils,name,Path(self.tmp.name)/name);patcher.start();self.addCleanup(patcher.stop)
        server._sessions.clear();server._tasks.clear();manager._history.clear();manager._sequence.clear()
        self.client=TestClient(server.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def create(self): return self.client.post('/api/session').json()['thread_id']

    def wait(self,thread):
        for _ in range(100):
            data=self.client.get('/api/task/'+thread).json()
            if data['status'] not in ('created','running'): return data
            time.sleep(.01)
        self.fail('Task did not terminate')

    def test_upload_before_start_and_late_websocket_replay(self):
        thread=self.create()
        response=self.client.post('/api/upload',data={'thread_id':thread},files={'files':('input.txt',b'uploaded evidence')})
        self.assertEqual(response.status_code,200)
        async def fake(query,tid):
            self.assertEqual((path_utils.get_upload_dir(tid)/'input.txt').read_text(),'uploaded evidence')
            return 'answer'
        with patch.object(main_agent,'run_deep_agent',side_effect=fake):
            self.assertEqual(self.client.post('/api/task',json={'query':'question','thread_id':thread}).status_code,200)
            self.assertEqual(self.wait(thread)['status'],'completed')
        events=[]
        with self.client.websocket_connect('/ws/'+thread+'?after=0') as ws:
            while True:
                event=ws.receive_json();events.append(event)
                if event['event']=='task_result': break
        self.assertEqual([e['seq'] for e in events],sorted(set(e['seq'] for e in events)))
        self.assertTrue(all(e['thread_id']==thread for e in events))
        self.assertEqual(events[-1]['data']['result'],'answer')

    def test_duplicate_running_upload_and_cancellation(self):
        async def fake(query,thread): await asyncio.sleep(30)
        thread=self.create()
        with patch.object(main_agent,'run_deep_agent',side_effect=fake):
            self.client.post('/api/task',json={'query':'question','thread_id':thread})
            self.assertEqual(self.client.post('/api/task',json={'query':'again','thread_id':thread}).status_code,409)
            self.assertEqual(self.client.post('/api/upload',data={'thread_id':thread},files={'files':('x.txt',b'x')}).status_code,409)
            self.assertEqual(self.client.post('/api/task/'+thread+'/cancel').json()['status'],'cancelled')
            self.assertEqual(self.client.get('/api/task/'+thread).json()['status'],'cancelled')

    def test_independent_concurrent_sessions(self):
        first,second=self.create(),self.create()
        async def fake(query,thread):
            await asyncio.sleep(.05)
            return thread
        with patch.object(main_agent,'run_deep_agent',side_effect=fake):
            for thread in [first,second]: self.client.post('/api/task',json={'query':'question','thread_id':thread})
            for thread in [first,second]: self.assertEqual(self.wait(thread)['result'],thread)
        self.assertTrue(all(e['thread_id']==first for e in manager._history[first]))
        self.assertTrue(all(e['thread_id']==second for e in manager._history[second]))

    def test_files_are_scoped_to_session(self):
        first,second=self.create(),self.create()
        own=path_utils.get_session_dir(first)/'own.txt';own.write_text('own')
        other=path_utils.get_session_dir(second)/'other.txt';other.write_text('private')
        self.assertEqual(self.client.get('/api/download',params={'thread_id':first,'path':str(own)}).text,'own')
        for route in ['/api/download','/api/files']:
            self.assertEqual(self.client.get(route,params={'thread_id':first,'path':str(other)}).status_code,403)
        self.assertEqual(self.client.get('/api/files',params={'path':str(own.parent)}).status_code,422)
        (own.parent/'link.txt').symlink_to(other)
        self.assertEqual(self.client.get('/api/download',params={'thread_id':first,'path':'link.txt'}).status_code,403)

    def test_invalid_query_upload_limits_and_no_overwrite(self):
        self.assertEqual(self.client.post('/api/task',json={'query':'  '}).status_code,422)
        thread=self.create()
        data={'thread_id':thread}
        self.assertEqual(self.client.post('/api/upload',data=data,files={'files':('x.exe',b'x')}).status_code,415)
        duplicate=[('files',('a.txt',b'a')),('files',('a.txt',b'b'))]
        self.assertEqual(self.client.post('/api/upload',data=data,files=duplicate).status_code,422)
        self.assertFalse(path_utils.get_upload_dir(thread).exists())
        self.client.post('/api/upload',data=data,files={'files':('a.txt',b'original')})
        self.assertEqual(self.client.post('/api/upload',data=data,files={'files':('a.txt',b'changed')}).status_code,409)
        self.assertEqual((path_utils.get_upload_dir(thread)/'a.txt').read_bytes(),b'original')
        self.assertEqual(self.client.post('/api/upload',data=data,files={'files':('huge.txt',b'x'*(10*1024*1024+1))}).status_code,413)
        self.assertFalse((path_utils.get_upload_dir(thread)/'huge.txt').exists())

    def test_failure_messages_do_not_leak_exception(self):
        thread=self.create()
        with patch.object(main_agent,'run_deep_agent',side_effect=RuntimeError('private-password')):
            self.client.post('/api/task',json={'query':'question','thread_id':thread})
            self.assertEqual(self.wait(thread)['status'],'failed')
        self.assertNotIn('private-password',str(list(manager._history[thread])))

    def test_callback_tool_hierarchy(self):
        async def run():
            callback=MonitorCallbackHandler('hierarchy')
            await callback.on_tool_start({'name':'task'},'',run_id='parent',inputs={'subagent_type':'数据库查询助手'})
            await callback.on_chain_start({}, {},run_id='chain',parent_run_id='parent')
            await callback.on_tool_start({'name':'execute_sql_query'},'',run_id='child',parent_run_id='chain',inputs={'sql':'SELECT 1'})
            await callback.on_tool_end('value\n1',run_id='child')
        asyncio.run(run())
        events=list(manager._history['hierarchy'])
        self.assertEqual(events[1]['data']['parent_id'],'parent')
        self.assertEqual(events[2]['event'],'tool_end')
        self.assertEqual(events[2]['data']['status'],'success')

    def test_tool_end_marks_string_failures_as_error(self):
        async def run():
            callback=MonitorCallbackHandler('failure-status')
            await callback.on_tool_start({'name':'execute_sql_query'},'',run_id='q',inputs={'sql':'SELECT 1'})
            await callback.on_tool_end('连接数据库失败：数据库操作失败，请检查服务、只读权限和超时配置',run_id='q')
        asyncio.run(run())
        self.assertEqual(list(manager._history['failure-status'])[-1]['data']['status'],'error')

    def test_worker_callback_sends_on_websocket_owner_loop(self):
        async def run():
            owner=asyncio.get_running_loop();local=ConnectionManager();received=[]
            class Socket:
                async def accept(self): pass
                async def send_text(self,text):
                    self_loop=asyncio.get_running_loop()
                    if self_loop is not owner: raise AssertionError('cross-loop socket send')
                    received.append(text)
            ws=Socket();await local.connect('thread',ws)
            await asyncio.wait_for(asyncio.to_thread(lambda:asyncio.run(local.send_event('thread','tool_end','done'))),2)
            self.assertEqual(len(received),1)
            self.assertIn('tool_end',received[0])
        asyncio.run(run())

    def test_port_probe_still_refuses_an_existing_listener(self):
        import socket
        from scripts.check_stage0 import assert_local_port_available
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(('127.0.0.1',0));listener.listen(1)
            port=listener.getsockname()[1]
            with self.assertRaises(OSError): assert_local_port_available(port)
        assert_local_port_available(port)


if __name__=='__main__': unittest.main(verbosity=2)
