import copy
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import yaml
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families
from exporter import Exporter, Client, stamp


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.fail = False
        cls.long_queue = False

        class API(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlsplit(self.path)
                q = parse_qs(parsed.query)
                cls.requests.append((parsed.path, q, dict(self.headers)))
                if cls.fail:
                    self.send_response(503)
                    self.end_headers()
                    return
                path = parsed.path
                if path == '/app/rest/agents':
                    data = {'agent': [{'id': 1, 'connected': True, 'authorized': True, 'enabled': True},
                                      {'id': 2, 'connected': True, 'authorized': True, 'enabled': False}]}
                elif path == '/app/rest/builds':
                    data = {'build': [{'status': 'FAILURE', 'startDate': '20261005T100000+0000',
                                       'finishDate': '20261005T100100+0000'}]} if 'state:finished' in q['locator'][0] else {'build': []}
                elif path == '/app/rest/buildQueue':
                    data = {'build': [{'queuedDate': '20261005T100000+0000'}]}
                elif path == '/computer/api/json':
                    data = {'computer': [{'displayName': 'linux-01', 'offline': False, 'numExecutors': 2},
                                         {'displayName': 'Built-In Node', 'offline': False, 'numExecutors': 2}]}
                elif path.startswith('/job/'):
                    data = {'lastCompletedBuild': {'result': 'SUCCESS', 'timestamp': 1791194400000, 'duration': 20000}}
                    if 'builds[' in q['tree'][0]:
                        data = {'builds': [{'number': 2, 'building': True, 'timestamp': 1791194400000}]}
                elif path == '/queue/api/json':
                    data = {'items': [{'inQueueSince': 1791194400000, 'task': {'url': 'https://public.example/job/platform/job/frontend/job/main/'}},
                                      {'inQueueSince': 1791194400000, 'task': {'url': 'https://public.example/job/unmonitored/'}}]}
                elif path == '/api/Spaces-1/tasks':
                    assert q['name'] == ['Deploy'] and q['project'] == ['Projects-1'] and q['environment'] == ['Environments-1']
                    if q['active'] == ['false']:
                        data = {'Items': [{'State': 'Failed', 'StartTime': '2026-10-05T10:00:00Z', 'CompletedTime': '2026-10-05T10:02:00Z'}]}
                    else:
                        skip = int(q['skip'][0])
                        if cls.long_queue:
                            data = {'TotalResults': 101, 'Items': [{'State': 'Queued', 'QueueTime': '2026-10-05T10:00:00Z'}] * (100 if skip == 0 else 1)}
                        else:
                            data = {'TotalResults': 1, 'Items': [{'State': 'Executing', 'StartTime': '2026-10-05T10:00:00Z'}]}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), API)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        type(self).fail = type(self).long_queue = False
        self.config = yaml.safe_load((Path(__file__).parents[1] / 'config.example.yml').read_text())
        for s in self.config['sources']:
            s['url'] = 'http://127.0.0.1:%d/' % self.server.server_port
        self.config['sources'][0]['expected_agents'] = ['1', '2', 'missing']
        self.env = patch.dict(os.environ, TEAMCITY_TOKEN='tc-secret', JENKINS_USER='reader',
                              JENKINS_TOKEN='jenkins-secret', OCTOPUS_API_KEY='octopus-secret')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.exporter = Exporter(self.config)

    def metrics(self):
        registry = CollectorRegistry()
        registry.register(self.exporter)
        text = generate_latest(registry).decode()
        self.assertNotIn('secret', text)
        return [s for f in text_string_to_metric_families(text) for s in f.samples]

    def value(self, name, component, **labels):
        samples = [s.value for s in self.metrics() if s.name == name and s.labels.get('component') == component
                   and all(s.labels.get(k) == v for k, v in labels.items())]
        self.assertEqual(len(samples), 1)
        return samples[0]

    def test_end_to_end_api_auth_scopes_and_statuses(self):
        for s in self.config['sources']:
            self.exporter.poll_source(s)
            self.assertEqual(self.value('ci_collection_complete', s['component']), 1)
        self.assertEqual(self.value('ci_last_operation_status', 'teamcity'), 2)
        self.assertEqual(self.value('ci_last_operation_status', 'jenkins'), 0)
        self.assertEqual(self.value('ci_last_operation_duration_seconds', 'octopus'), 120)
        self.assertEqual(self.value('ci_agents_online', 'teamcity'), 1)
        self.assertEqual(self.value('ci_agents_online', 'jenkins'), 1)
        self.assertEqual(self.value('ci_queue_length', 'jenkins'), 1)
        self.assertEqual(self.value('ci_resource_online', 'teamcity', resource_id='missing'), 0)
        self.assertFalse(any(s.name == 'ci_agents_online' and s.labels['component'] == 'octopus' for s in self.metrics()))
        self.assertTrue(any(h.get('Authorization') == 'Bearer tc-secret' for _, _, h in self.requests))
        self.assertTrue(any(h.get('X-Octopus-Apikey') == 'octopus-secret' for _, _, h in self.requests))

    def test_outage_does_not_fabricate_success_or_advance_timestamp(self):
        source = self.config['sources'][2]
        self.exporter.poll_source(source)
        ts = self.value('ci_collection_last_success_timestamp_seconds', 'octopus')
        type(self).fail = True
        self.exporter.poll_source(source)
        self.assertEqual(self.value('ci_api_up', 'octopus'), 0)
        self.assertEqual(self.value('ci_collection_complete', 'octopus'), 0)
        self.assertEqual(self.value('ci_collection_last_success_timestamp_seconds', 'octopus'), ts)
        self.assertEqual(self.value('ci_last_operation_status', 'octopus'), 2)

    def test_pagination_and_incomplete_snapshot(self):
        source = self.config['sources'][2]
        type(self).long_queue = True
        self.exporter.poll_source(source)
        self.assertEqual(self.value('ci_queue_length', 'octopus'), 101)
        source['max_pages'] = 1
        self.exporter.poll_source(source)
        self.assertEqual(self.value('ci_api_up', 'octopus'), 1)
        self.assertEqual(self.value('ci_collection_complete', 'octopus'), 0)

    def test_cross_origin_and_unsafe_url_rejected(self):
        client = Client(self.config['sources'][0])
        with self.assertRaises(ValueError):
            client.get('https://untrusted.example/page')
        source = dict(self.config['sources'][0], url='https://user:secret@example.com/')
        with self.assertRaises(ValueError):
            Client(source)

    def test_duplicate_scopes_rejected(self):
        p = copy.deepcopy(self.config['sources'][0]['pipelines'][0])
        p['pipeline'] = 'other-label'
        self.config['sources'][0]['pipelines'].append(p)
        with self.assertRaises(ValueError):
            Exporter(self.config)

    def test_timestamp_formats_and_missing_timezone(self):
        self.assertEqual(stamp('20261005T100000+0000'), stamp('2026-10-05T10:00:00Z'))
        with self.assertRaises(ValueError):
            stamp('2026-10-05T10:00:00')

    def test_startup_has_unknown_data_instead_of_success(self):
        samples = self.metrics()
        self.assertFalse(any(s.name in ('ci_api_up', 'ci_last_operation_status') for s in samples))
        self.assertEqual(self.value('ci_collection_complete', 'octopus'), 0)
        self.assertEqual(self.value('ci_collection_last_success_timestamp_seconds', 'octopus'), 0)


if __name__ == '__main__':
    unittest.main()
