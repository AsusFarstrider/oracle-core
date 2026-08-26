from __future__ import annotations
from network_test_support import *

class NetworkStatusObservationTests(NetworkTestCase):

    def test_librenms_alert_normalization_keeps_service_match_fields(self) -> None:
        alert = LibreNmsBridge()._normalize_alert({'alert': 'Service up/down', 'hostname': 'oracle-brain.local', 'service': 'plex', 'service_id': 77, 'device_id': 205, 'severity': 'critical'})
        self.assertEqual(alert['service_name'], 'plex')
        self.assertEqual(alert['service_id'], '77')
        self.assertEqual(alert['device_id'], '205')

    def test_librenms_device_extraction_normalizes_device_payload(self) -> None:
        devices = LibreNmsBridge()._extract_devices({'devices': [{'device_id': 3, 'hostname': '192.0.2.153', 'sysName': 'mesh_node-xe75', 'display': 'Primary Mesh Node', 'ip': '192.0.2.153', 'status': 1}]})
        self.assertEqual(len(devices), 1)
        normalized = LibreNmsBridge()._normalize_device(devices[0])
        self.assertEqual(normalized['device_id'], '3')
        self.assertEqual(normalized['display'], 'Primary Mesh Node')
        self.assertEqual(normalized['status'], '1')

    @patch.object(LibreNmsBridge, '_fetch_interface_detail')
    def test_librenms_interface_detail_enrichment_uses_port_id(self, mock_fetch_detail) -> None:
        mock_fetch_detail.return_value = {'payload': {'port': [{'port_id': 399, 'device_id': 2, 'ifName': 'eth1', 'ifOperStatus': 'up', 'ifAdminStatus': 'up'}]}, 'http_status': 200, 'error': None}
        interfaces = LibreNmsBridge()._with_interface_details([{'port_id': 399, 'ifName': 'eth1'}], base_url='http://librenms.local', api_token='secret-token', timeout_seconds=5, max_detail_fetches=1)
        normalized = LibreNmsBridge()._normalize_interface(interfaces[0])
        self.assertEqual(normalized['port_id'], '399')
        self.assertEqual(normalized['device_id'], '2')
        self.assertEqual(normalized['if_name'], 'eth1')
        self.assertEqual(normalized['if_oper_status'], 'up')
        self.assertEqual(normalized['if_admin_status'], 'up')
        self.assertNotIn('secret-token', str(interfaces))

    def test_librenms_interface_extraction_normalizes_port_payload(self) -> None:
        interfaces = LibreNmsBridge()._extract_interfaces({'ports': [{'port_id': 10, 'device_id': 2, 'ifIndex': 4, 'ifName': 'wan', 'ifDescr': 'eth1', 'ifAlias': 'Internet uplink', 'ifOperStatus': 'up', 'ifAdminStatus': 'up'}]})
        self.assertEqual(len(interfaces), 1)
        normalized = LibreNmsBridge()._normalize_interface(interfaces[0])
        self.assertEqual(normalized['port_id'], '10')
        self.assertEqual(normalized['device_id'], '2')
        self.assertEqual(normalized['if_index'], '4')
        self.assertEqual(normalized['if_name'], 'wan')
        self.assertEqual(normalized['if_descr'], 'eth1')
        self.assertEqual(normalized['if_alias'], 'Internet uplink')
        self.assertEqual(normalized['if_oper_status'], 'up')
        self.assertEqual(normalized['if_admin_status'], 'up')

    def test_librenms_service_extraction_flattens_nested_service_payload(self) -> None:
        services = LibreNmsBridge()._extract_services({'services': [[{'service_id': 62, 'device_id': 1, 'service_ip': '192.0.2.205', 'service_name': 'plex', 'service_desc': 'Plex', 'service_status': 0, 'service_message': 'TCP OK'}]]})
        self.assertEqual(len(services), 1)
        normalized = LibreNmsBridge()._normalize_service(services[0])
        self.assertEqual(normalized['service_name'], 'plex')
        self.assertEqual(normalized['service_status'], '0')

    def test_network_admin_payload_attaches_safe_control_action_metadata(self) -> None:
        payload = build_network_admin_payload({'status': 'healthy', 'severity': 'none', 'freshness': 'fresh', 'generated_at': '2026-05-24T08:00:00-04:00', 'summary': 'No problems are known.', 'hosts': [{'id': 'oracle_host', 'display_name': 'Oracle Server', 'evidence_ids': []}], 'services': [{'id': 'plex', 'display_name': 'Plex', 'host_id': 'oracle_host', 'evidence_ids': []}], 'service_groups': [], 'dependencies': [], 'monitors': [], 'evidence': []}, control_policy=_enabled_plex_restart_policy())
        service = payload['hosts'][0]['services'][0]
        self.assertEqual(service['control_actions'][0]['action_id'], 'restart_service')
        self.assertTrue(service['control_actions'][0]['enabled'])
        self.assertTrue(service['control_actions'][0]['requires_confirmation'])
        self.assertNotIn('execution', service['control_actions'][0])
        self.assertNotIn('unit', service['control_actions'][0])

    def test_network_admin_payload_has_no_stage3_executable_fields(self) -> None:
        payload = build_network_admin_payload({'status': 'healthy', 'severity': 'none', 'freshness': 'fresh', 'generated_at': '2026-05-24T08:00:00-04:00', 'summary': 'No problems are known.', 'hosts': [{'id': 'oracle_host', 'display_name': 'Oracle Server', 'evidence_ids': []}], 'services': [{'id': 'plex', 'display_name': 'Plex', 'host_id': 'oracle_host', 'evidence_ids': []}], 'service_groups': [], 'dependencies': [], 'monitors': [], 'evidence': [{'id': 'librenms.monitor.plex', 'provider': 'librenms', 'detail': 'TCP OK', 'provider_reference': {'service_id': '62', 'service_name': 'plex'}}], 'provider_observations': {'librenms_services': [{'service_id': '62', 'device_id': '1', 'service_name': 'plex', 'status': 'healthy', 'matched_monitor_ids': []}]}})
        forbidden_key_fragments = ('action', 'command', 'execute', 'restart', 'reboot', 'self_heal', 'remediate', 'url', 'token', 'credential', 'secret', 'password')
        for path, value in _walk_payload(payload):
            if isinstance(value, dict):
                continue
            key = path.rsplit('.', 1)[-1].split('[', 1)[0].lower()
            self.assertFalse(any((fragment in key for fragment in forbidden_key_fragments)), f'{path} must not expose executable or secret-bearing fields')

    def test_network_admin_payload_reports_inventory_coverage(self) -> None:
        payload = build_network_admin_payload({'status': 'healthy', 'severity': 'none', 'freshness': 'fresh', 'generated_at': '2026-05-24T08:00:00-04:00', 'summary': 'No problems are known.', 'hosts': [{'id': 'oracle_host', 'display_name': 'Oracle Server', 'status': 'healthy', 'severity': 'none', 'freshness': 'fresh', 'summary': 'No problems are known.', 'evidence_ids': ['librenms.monitor.oracle_host_librenms']}, {'id': 'nas', 'display_name': 'NAS', 'status': 'unknown', 'severity': 'unknown', 'freshness': 'unknown', 'summary': 'Status is unknown.', 'evidence_ids': []}], 'services': [{'id': 'plex', 'display_name': 'Plex', 'host_id': 'oracle_host', 'status': 'healthy', 'severity': 'none', 'freshness': 'fresh', 'summary': 'No problems are known.', 'evidence_ids': ['librenms.monitor.plex_librenms_service']}, {'id': 'nextcloud', 'display_name': 'Nextcloud', 'host_id': 'oracle_host', 'status': 'unknown', 'severity': 'unknown', 'freshness': 'unknown', 'summary': 'Status is unknown.', 'evidence_ids': []}], 'service_groups': [], 'dependencies': [], 'monitors': [{'id': 'oracle_host_librenms', 'display_name': 'Oracle Host LibreNMS', 'provider': 'librenms', 'status': 'healthy', 'severity': 'none', 'freshness': 'fresh', 'summary': 'No problems are known.', 'target_type': 'host', 'target_id': 'oracle_host', 'evidence_ids': ['librenms.monitor.oracle_host_librenms']}, {'id': 'plex_librenms_service', 'display_name': 'Plex LibreNMS Service', 'provider': 'librenms', 'status': 'healthy', 'severity': 'none', 'freshness': 'fresh', 'summary': 'No problems are known.', 'target_type': 'service', 'target_id': 'plex', 'evidence_ids': ['librenms.monitor.plex_librenms_service']}, {'id': 'nas_librenms', 'display_name': 'NAS LibreNMS', 'provider': 'librenms', 'status': 'unknown', 'severity': 'unknown', 'freshness': 'unknown', 'summary': 'Status is unknown.', 'target_type': 'host', 'target_id': 'nas', 'evidence_ids': []}], 'evidence': [{'id': 'librenms.monitor.oracle_host_librenms'}, {'id': 'librenms.monitor.plex_librenms_service'}]})
        self.assertEqual(payload['coverage']['hosts']['total'], 2)
        self.assertEqual(payload['coverage']['hosts']['monitored'], 1)
        self.assertEqual(payload['coverage']['hosts']['configured_no_evidence'], 1)
        self.assertEqual(payload['coverage']['services']['monitored'], 1)
        self.assertEqual(payload['coverage']['services']['unmonitored'], 1)
        self.assertEqual(payload['coverage']['monitors']['without_evidence'], 1)
        host = next((item for item in payload['hosts'] if item['id'] == 'nas'))
        self.assertEqual(host['monitor_count'], 1)
        self.assertEqual(host['monitoring_state'], 'configured_no_evidence')
        service = payload['hosts'][0]['services'][1]
        self.assertEqual(service['id'], 'nextcloud')
        self.assertEqual(service['monitoring_state'], 'unmonitored')

    def test_plex_sessions_status_counts_active_streams(self) -> None:
        status = PlexMusicBridge().extract_active_sessions_status('\n            <MediaContainer size="1">\n              <Video title="Movie Night">\n                <Player title="Living Room TV" />\n              </Video>\n            </MediaContainer>\n            ')
        self.assertTrue(status['available'])
        self.assertEqual(status['active_stream_count'], 1)
        self.assertEqual(status['sessions'][0]['title'], 'Movie Night')
        self.assertEqual(status['sessions'][0]['player'], 'Living Room TV')

    def test_provider_diagnostics_do_not_create_oracle_services(self) -> None:
        payload = build_network_admin_payload({'status': 'healthy', 'severity': 'none', 'freshness': 'fresh', 'generated_at': '2026-05-24T08:00:00-04:00', 'summary': 'No problems are known.', 'hosts': [{'id': 'oracle_host', 'display_name': 'Oracle Server', 'evidence_ids': []}], 'services': [{'id': 'plex', 'display_name': 'Plex', 'host_id': 'oracle_host', 'evidence_ids': []}], 'service_groups': [], 'dependencies': [], 'monitors': [], 'evidence': [], 'provider_observations': {'librenms_services': [{'service_id': '99', 'device_id': '1', 'service_name': 'provider-only', 'service_desc': 'Provider Only', 'status': 'healthy', 'matched_monitor_ids': []}]}})
        host_services = [service['id'] for host in payload['hosts'] for service in host.get('services') or []]
        diagnostics = payload['provider_diagnostics']['librenms_services']
        self.assertEqual(host_services, ['plex'])
        self.assertEqual(diagnostics['unmatched'], 1)
        self.assertEqual(diagnostics['items'][0]['service_name'], 'provider-only')
        self.assertNotIn('provider-only', host_services)

    @patch('oracle_app.network.get_network_summary', return_value={'status': 'healthy', 'internet': {'status': 'healthy', 'detail': 'Direct network checks succeeded.'}, 'monitoring': {'status': 'unknown', 'detail': 'LibreNMS not configured.'}, 'problems': [], 'actions_available': [], 'generated_at': '2026-04-23T20:00:00-04:00'})
    def test_ui_network_health_snapshot_returns_summary_block(self, _mock_summary) -> None:
        payload = build_ui_network_health_snapshot()
        self.assertEqual(payload['status'], 'healthy')
        self.assertEqual(payload['label'], 'Network')
        self.assertEqual(payload['summary'], 'The network looks healthy.')

    @patch('oracle_app.handlers.network.build_network_response', return_value=('The internet appears to be down.', {'status': 'down', 'internet': {'status': 'down'}, 'monitoring': {'status': 'unknown'}, 'problems': ['HTTP reachability failed.'], 'actions_available': [], 'generated_at': '2026-04-23T20:00:00-04:00'}))
    def test_voice_query_routes_to_network_and_returns_short_reply(self, _mock_response) -> None:
        route = choose_route('is the internet down?', registry=_NEUTRAL_ROUTE_REGISTRY, household_settings=_NEUTRAL_RUNTIME.household)
        dispatch = build_dispatch_plan(CommandRequest(text='is the internet down?'), route)
        result = execute_dispatch(dispatch, registry=build_dispatch_registry())
        reply = build_reply_text(result)
        self.assertEqual(route.target, 'network')
        self.assertEqual(result.status, 'executed')
        self.assertEqual(reply, 'The internet appears to be down.')

