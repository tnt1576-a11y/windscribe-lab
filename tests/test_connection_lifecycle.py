"""CLI transcripts follow official strings.cpp, including its leading star."""
import threading
from unittest.mock import patch, Mock
import pytest
from windscribe_lab.engine import Client, ClientError, Runner, ConnectionRejected, ClientTimeout
from windscribe_lab.models import (ClientStatus, Location, Route, RunConfig, Result,
                                  parse_status, route_matches, route_identity_matches)
from windscribe_lab.storage import Store

LOC = Location('test','USA','US','Dallas','Ranch')
CONNECTED = 'Login state: Logged in\nFirewall state: Off\nConnect state: Connected: Dallas - Ranch\nProtocol: IKEv2:500\nVPN IP: 203.0.113.5'
PENDING = CONNECTED.replace('Connect state:', '*Connect state:').replace('VPN IP:', 'Public IP:')
DISCONNECTED = 'Login state: Logged in\nFirewall state: Off\nConnect state: Disconnected'

def client():
    with patch('windscribe_lab.engine.resolve_cli',return_value='synthetic/windscribe-cli.exe'):
        return Client('synthetic', frontend=Mock(main_window_ready=lambda: True))

@pytest.mark.parametrize('protocol,port',[('IKEv2',500),('WireGuard',443),('UDP',53),('TCP',80),('Stealth',443),('WStunnel',443)])
def test_official_protocol_names(protocol,port):
    state=parse_status(CONNECTED.replace('IKEv2:500',f'{protocol}:{port}'))
    assert route_matches(state,Route(LOC,protocol.lower(),port))

def test_official_leading_star_is_connected_but_public_ip_is_not_vpn_evidence():
    state=parse_status(PENDING)
    assert state.state=='connected' and state.pending and state.protocol=='ikev2' and state.port==500
    assert state.vpn_ip=='' and state.public_ip=='203.0.113.5'
    assert route_identity_matches(state,Route(LOC,'ikev2',500))
    assert not route_matches(state,Route(LOC,'ikev2',500))

def test_logged_out_without_connect_field_is_a_recognized_official_status():
    with patch('windscribe_lab.engine.run_process',return_value=(0,'Login state: Logged out\nFirewall state: Off')):
        state=client().status()
    assert state.state=='disconnected' and not state.logged_in

def test_restart_waits_through_logged_out_and_logging_in():
    replies=[(0,'Login state: Logged out\nFirewall state: Off'),
             (0,'Login state: Logging in\nConnect state: Disconnected\nFirewall state: Off'),
             (0,DISCONNECTED)]
    c=client()
    with patch('windscribe_lab.engine.run_process',side_effect=replies),patch.object(c,'sleep'):
        assert c.wait_ready(require_login=True).logged_in

def test_pending_tunnel_is_measured_and_final_state_is_checked(tmp_path,qapp):
    c=client()
    with patch.object(c,'wait_ready',return_value=parse_status(DISCONNECTED)),patch('windscribe_lab.engine.run_process',side_effect=[(0,''),(0,PENDING)]):
        state=c.connect('Ranch','ikev2',500,30)
    worker=Runner(Store(tmp_path),'synthetic')
    with patch.object(c,'disconnect'),patch.object(c,'connect',return_value=state),patch.object(c,'sleep'),patch.object(c,'wait_ready',return_value=state),patch('windscribe_lab.engine.check_https',return_value=(2,False,'HTTPS works; egress unconfirmed.')) as probes,patch('windscribe_lab.engine.ping',return_value=(50,1,0)):
        result=worker.measure(c,Route(LOC,'ikev2',500),RunConfig(settle=0))
    assert result.outcome=='https_only' and result.working and not result.tunnel_verified
    assert probes.call_args.args[1]==''

def test_route_rejection_does_not_abort_the_batch_and_restoration_runs(tmp_path,qapp):
    from dataclasses import asdict
    original=parse_status(CONNECTED)
    class ScriptedClient:
        path='synthetic'
        connects=[]
        def __init__(self,*args): pass
        def wait_ready(self,**kwargs): return original
        def status(self,**kwargs): return original
        def ports(self): return {'ikev2':[500],'wireguard':[443]}
        def disconnect(self,**kwargs): pass
        def sleep(self,*args): pass
        def connect(self,location,protocol,port,timeout):
            self.connects.append((location,protocol,port))
            if protocol=='wireguard': raise ConnectionRejected('Route rejected')
            return parse_status(PENDING)
    store=Store(tmp_path)
    routes=[Route(LOC,'wireguard',443),Route(LOC,'ikev2',500)]
    run=store.create_run(RunConfig(settle=0),routes,'live')
    done=[]
    worker=Runner(store,run)
    worker.finished_run.connect(done.append)
    with patch('windscribe_lab.engine.Client',ScriptedClient),patch('windscribe_lab.engine.check_https',return_value=(2,False,'HTTPS works')),patch('windscribe_lab.engine.ping',return_value=(50,1,0)):
        worker.run()
    assert store.run(run)['state']=='complete'
    assert [r.outcome for _,_,r in store.tasks(run)]==['connect_error','https_only']
    assert ScriptedClient.connects[-1]==('Ranch','ikev2',500)
    assert not done[0]['error'] and not done[0]['cleanup_error']

def test_explicit_connect_error_is_a_route_result(tmp_path,qapp):
    c=client()
    error=parse_status(DISCONNECTED.replace('Disconnected','Error: Location does not exist or is disabled'))
    assert error.state=='disconnected' and error.connection_error
    with patch.object(c,'disconnect'),patch.object(c,'connect',return_value=error):
        result=Runner(Store(tmp_path),'synthetic').measure(c,Route(LOC,'ikev2',500),RunConfig())
    assert result.outcome=='connect_error'

def test_restoration_failure_keeps_the_settings_recovery_journal(tmp_path,qapp):
    from test_engine import managed_session, FakeClient, FakeSettingsController
    store,run,_=managed_session(tmp_path)
    FakeSettingsController.instances=[]
    FakeSettingsController.fail_begin=False
    worker=Runner(store,run)
    done=[]
    worker.finished_run.connect(done.append)
    with patch('windscribe_lab.engine.Client',FakeClient),patch('windscribe_lab.engine.SettingsController',FakeSettingsController),patch.object(Runner,'measure',return_value=Result('verified',https_ok=2,tunnel_verified=True)),patch.object(Runner,'restore',side_effect=ClientError('reconnect failed')):
        worker.run()
    assert FakeSettingsController.instances[0].restored==1
    assert not getattr(FakeSettingsController.instances[0],'cleared',False)
    assert done[0]['cleanup_error'] and store.run(run)['state']=='error'
    assert all(result for _,_,result in store.tasks(run))

def test_legacy_bad_ikev2_restore_target_is_repaired_before_resume(tmp_path,qapp):
    from test_engine import session, FakeClient
    store,run=session(tmp_path)
    bad={'state':'connected','location':'Dallas - Ranch','protocol':'','port':0,'firewall':'On'}
    store.set_original(run,bad)
    with patch('windscribe_lab.engine.Client',FakeClient),patch.object(Runner,'measure',return_value=Result('timeout')):
        Runner(store,run).run()
    assert store.run(run)['state']=='complete'
    assert store.run(run)['original']['protocol']=='wireguard'
    assert store.run(run)['original']['vpn_ip']==store.run(run)['original']['public_ip']==''

def test_valid_saved_restore_targets_cannot_be_overwritten(tmp_path):
    from dataclasses import asdict
    store=Store(tmp_path)
    run=store.create_run(RunConfig(),[Route(LOC,'ikev2',500)],'live')
    baseline=asdict(parse_status(CONNECTED))
    store.set_original(run,baseline)
    with pytest.raises(ValueError):
        store.repair_original(run,baseline,baseline)
