"""Operator HTTP authorization tests with no command or game delivery."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from admin.app import create_app
from admin.settings import Settings


class OperatorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.operator = Mock()
        self.operator.call.return_value = {'ready': True}
        self.settings = Settings(Path(self.temp.name), origin='http://localhost:8092', demo=True)
        self.app = create_app(self.settings, operator=self.operator, background=False)
        self.client = TestClient(self.app, base_url=self.settings.origin)
        self.addCleanup(self.client.close)
        self.headers = {'Origin': self.settings.origin}

    def login(self):
        response = self.client.post('/admin/api/auth/demo', json={}, headers=self.headers)
        self.headers['X-Oak-CSRF'] = response.json()['csrf']
        return response.json()['user']['id']

    def test_no_operator_access_without_owner(self):
        self.assertEqual(self.client.get('/admin/api/operator/discover').status_code, 401)
        uid = self.login()
        for role in ('observer', 'moderator', 'administrator'):
            with self.app.state.store.transaction() as db:
                db.execute('UPDATE users SET role=? WHERE id=?', (role, uid))
            self.assertEqual(self.client.get('/admin/api/operator/discover').status_code, 403)
            response = self.client.post('/admin/api/operator/call', json={'method': 'files.read', 'data': {'path': '/private'}}, headers=self.headers)
            self.assertEqual(response.status_code, 403)
        self.operator.call.assert_not_called()

    def test_owner_execution_preserves_source_and_server_actor(self):
        uid = self.login()
        payload = {'kind': 'command', 'source': 'function oak:test', 'actor': 'root'}
        response = self.client.post('/admin/api/operator/jobs', json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        self.operator.call.assert_called_once_with('jobs.submit', payload, actor='portal:' + uid)

    def test_origin_and_csrf_are_required_for_general_calls(self):
        self.login()
        for headers in ({'Origin': self.settings.origin}, {**self.headers, 'Origin': 'https://invalid.example'}):
            response = self.client.post('/admin/api/operator/call', json={'method': 'jobs.submit', 'data': {}}, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.operator.call.assert_not_called()

    def test_large_scripts_do_not_expand_other_admin_endpoints(self):
        self.login()
        payload = {'source': '#' * 100000, 'kind': 'python'}
        self.assertEqual(self.client.post('/admin/api/operator/jobs', json=payload, headers=self.headers).status_code, 202)
        self.assertEqual(self.client.post('/admin/api/jobs', json=payload, headers=self.headers).status_code, 413)
        self.assertEqual(self.client.post('/admin/api/operator/jobs', json={'source': '#' * (2 * 1024 * 1024)}, headers=self.headers).status_code, 413)

    def test_read_cursor_and_notebook_revision_forwarded(self):
        uid = self.login()
        self.client.get('/admin/api/operator/jobs/abc?offset=120')
        self.operator.call.assert_called_with('jobs.get', {'id': 'abc', 'offset': 120}, actor='portal:' + uid)
        self.client.patch('/admin/api/operator/notebooks/abc', json={'title': 'Work', 'content': 'Result', 'revision': 3}, headers=self.headers)
        self.operator.call.assert_called_with('notebooks.save', {'id': 'abc', 'title': 'Work', 'content': 'Result', 'revision': 3}, actor='portal:' + uid)

    def test_demo_operator_closes_without_background_worker(self):
        with TestClient(self.app, base_url=self.settings.origin):
            self.operator.close.assert_not_called()
        self.operator.start.assert_not_called()
        self.operator.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
