"""Local development, account recovery and production service entry points."""
import argparse
import json
import os
from pathlib import Path
import sys

from .settings import Settings
from .store import Store


def main():
    parser = argparse.ArgumentParser(description='Oak private administration')
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8092)
    demo = sub.add_parser('demo')
    demo.add_argument('--port', type=int, default=8092)
    invite = sub.add_parser('invite')
    invite.add_argument('--name', required=True)
    invite.add_argument('--role', choices=['owner', 'administrator', 'moderator', 'observer'], default='owner')
    sub.add_parser('agent')
    sub.add_parser('recover-saving')
    sub.add_parser('recover-restore')
    sub.add_parser('guard-start')
    sub.add_parser('doctor')
    args = parser.parse_args()
    if args.command == 'demo':
        os.environ['OAK_ADMIN_DEMO'] = '1'
        os.environ['OAK_ADMIN_ORIGIN'] = f'http://localhost:{args.port}'
        os.environ.setdefault('OAK_ADMIN_STATE', str(Path.home() / '.oak-control-demo'))
    if args.command in ('serve', 'demo'):
        import uvicorn
        from .app import create_app
        # One worker owns the persistent queue. Bind only on host loopback.
        uvicorn.run(create_app(), host='127.0.0.1', port=args.port, proxy_headers=False, access_log=False, limit_concurrency=64)
    elif args.command == 'invite':
        settings = Settings.from_env()
        from .domain import clean_text
        token = Store(settings.state / 'control.sqlite3').invite(clean_text(args.name, 60, 1), args.role)
        print('One-use enrollment link. Expires in 15 minutes. Keep it private:')
        print(settings.origin + '/admin/#enroll=' + token)
    elif args.command == 'doctor':
        from .agent import AgentClient
        agent = AgentClient(Settings.from_env().socket)
        snapshot = agent.call('snapshot')
        backups = agent.call('backups')
        configuration = agent.call('configuration')
        if not snapshot.get('fresh'):
            raise RuntimeError('Host status is stale. Check the existing status collector.')
        print(json.dumps({'agent': 'connected', 'fresh': snapshot['fresh'], 'online': snapshot['online'], 'version': snapshot['version'], 'backups': len(backups), 'settings': len(configuration['fields'])}))
    elif args.command == 'agent':
        from .agent import main as agent_main
        agent_main()
    elif args.command == 'recover-saving':
        from .runtime import Runtime
        Runtime().recover_saving()
    elif args.command in ('recover-restore', 'guard-start'):
        from .runtime import Runtime
        runtime = Runtime()
        if args.command == 'guard-start':
            runtime.guard_start()
        else:
            with runtime.lock():
                runtime.recover_restore()


if __name__ == '__main__':
    main()
