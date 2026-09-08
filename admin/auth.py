"""Passkey authentication with one-use, SSH-issued enrollment invitations."""
import base64
import json
import secrets
import time
import uuid

from webauthn import generate_authentication_options, generate_registration_options, verify_authentication_response, verify_registration_response
from webauthn.helpers import options_to_json
from webauthn.helpers.structs import AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement

from .store import digest


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def unb64(raw):
    return base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))


class Auth:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings

    def register_options(self, token=None, user=None):
        invite = None
        if user is None:
            invite = self.store.one('SELECT i.*,u.name FROM invites i JOIN users u ON u.id=i.user_id WHERE i.token=? AND i.expires>? AND u.disabled=0', (digest(token or ''), time.time()))
            if not invite:
                raise PermissionError('Enrollment link expired or already used.')
            uid, name = invite['user_id'], invite['name']
        else:
            if time.time() - user['verified'] > 600:
                raise PermissionError('Sign in again before adding a passkey.')
            uid, name = user['user_id'], user['name']
        excluded = [PublicKeyCredentialDescriptor(id=unb64(row['id'])) for row in self.store.rows('SELECT id FROM credentials WHERE user_id=?', (uid,))]
        options = generate_registration_options(rp_id=self.settings.rp_id, rp_name='Oak', user_id=uuid.UUID(uid).bytes, user_name=name,
                                                authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
                                                exclude_credentials=excluded, timeout=120000)
        challenge = self.store.challenge('register', {'user_id': uid, 'challenge': b64(options.challenge), 'invite': invite['token'] if invite else None})
        return {'challenge_id': challenge, 'options': json.loads(options_to_json(options))}

    def register_verify(self, challenge_id, credential, name='Minha chave de acesso'):
        saved = self.store.consume_challenge(challenge_id, 'register')
        result = verify_registration_response(credential=credential, expected_challenge=unb64(saved['challenge']), expected_rp_id=self.settings.rp_id,
                                              expected_origin=self.settings.origin, require_user_verification=True)
        with self.store.transaction() as db:
            user = db.execute('SELECT * FROM users WHERE id=? AND disabled=0', (saved['user_id'],)).fetchone()
            if not user:
                raise PermissionError('Account is unavailable.')
            if saved['invite']:
                removed = db.execute('DELETE FROM invites WHERE token=? AND expires>?', (saved['invite'], time.time())).rowcount
                if not removed:
                    raise PermissionError('Enrollment link already used.')
            db.execute('INSERT INTO credentials VALUES(?,?,?,?,?,?)', (b64(result.credential_id), saved['user_id'], b64(result.credential_public_key), result.sign_count, name, time.time()))
            self.store.event('access', 'Passkey registered', saved['user_id'], db=db)
        return saved['user_id']

    def login_options(self):
        options = generate_authentication_options(rp_id=self.settings.rp_id, user_verification=UserVerificationRequirement.REQUIRED, timeout=120000)
        cid = self.store.challenge('login', {'challenge': b64(options.challenge)})
        return {'challenge_id': cid, 'options': json.loads(options_to_json(options))}

    def login_verify(self, challenge_id, credential):
        saved = self.store.consume_challenge(challenge_id, 'login')
        if not isinstance(credential, dict):
            raise ValueError('Invalid credential.')
        stored = self.store.one('SELECT c.* FROM credentials c JOIN users u ON u.id=c.user_id WHERE c.id=? AND u.disabled=0', (credential.get('id'),))
        if not stored:
            raise PermissionError('Passkey was not recognized.')
        handle = credential.get('response', {}).get('userHandle')
        if handle and unb64(handle) != uuid.UUID(stored['user_id']).bytes:
            raise PermissionError('Passkey user does not match.')
        result = verify_authentication_response(credential=credential, expected_challenge=unb64(saved['challenge']), expected_rp_id=self.settings.rp_id,
                                                expected_origin=self.settings.origin, credential_public_key=unb64(stored['public_key']),
                                                credential_current_sign_count=stored['counter'], require_user_verification=True)
        with self.store.transaction() as db:
            updated = db.execute('UPDATE credentials SET counter=? WHERE id=? AND counter=?', (result.new_sign_count, stored['id'], stored['counter'])).rowcount
            if not updated:
                raise PermissionError('Concurrent passkey use; sign in again.')
            self.store.event('access', 'Signed in with a passkey', stored['user_id'], db=db)
        return stored['user_id']
