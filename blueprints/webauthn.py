"""
blueprints/webauthn.py — WebAuthn (Face ID / 指紋) 生物辨識登入
"""
import base64 as _b64
import secrets
import struct as _struct

from flask import Blueprint, request, jsonify, session

from config import WEBAUTHN_RP_ID, WEBAUTHN_RP_NAME, WEBAUTHN_ORIGIN
from db import get_db

bp = Blueprint('webauthn', __name__)

# ── DB init ────────────────────────────────────────────────────────

def init_webauthn_db():
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS webauthn_credentials (
                    id            SERIAL PRIMARY KEY,
                    user_key      TEXT NOT NULL,
                    credential_id TEXT NOT NULL UNIQUE,
                    public_key    BYTEA NOT NULL,
                    sign_count    BIGINT DEFAULT 0,
                    device_name   TEXT DEFAULT '',
                    created_at    TIMESTAMPTZ DEFAULT NOW()
                )
            """)
    except Exception as e:
        print(f'[webauthn_init] {e}')


# ── Helpers ────────────────────────────────────────────────────────

def _b64url_encode(data: bytes) -> str:
    return _b64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(s: str) -> bytes:
    s = s.replace(' ', '+').replace('-', '+').replace('_', '/')
    padding = 4 - len(s) % 4
    if padding != 4:
        s += '=' * padding
    return _b64.b64decode(s)


def _verify_cose_signature(cose_key_bytes: bytes, message: bytes, signature: bytes):
    try:
        import cbor2
        cose = cbor2.loads(cose_key_bytes)
    except ImportError:
        cose = _minimal_cbor_decode(cose_key_bytes)

    from cryptography.hazmat.primitives.asymmetric import ec, padding
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend

    kty = cose.get(1) or cose.get(b'\x01')
    alg = cose.get(3) or cose.get(b'\x03')

    if alg == -7 or kty == 2:
        x = cose.get(-2) or cose.get(b'\x21') or b''
        y = cose.get(-3) or cose.get(b'\x22') or b''
        pub_numbers = ec.EllipticCurvePublicNumbers(
            x=int.from_bytes(x, 'big'), y=int.from_bytes(y, 'big'), curve=ec.SECP256R1()
        )
        pub_key = pub_numbers.public_key(default_backend())
        pub_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    elif alg == -257 or kty == 3:
        from cryptography.hazmat.primitives.asymmetric import rsa
        n       = cose.get(-1) or cose.get(b'\x20') or b''
        e_bytes = cose.get(-2) or cose.get(b'\x21') or b''
        pub_numbers = rsa.RSAPublicNumbers(
            e=int.from_bytes(e_bytes, 'big'), n=int.from_bytes(n, 'big')
        )
        pub_key = pub_numbers.public_key(default_backend())
        pub_key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    else:
        raise ValueError(f'Unsupported alg: {alg}')


def _minimal_cbor_decode(data: bytes) -> dict:
    import io
    return _cbor_read(io.BytesIO(data))


def _cbor_read(buf):
    import io
    b0   = ord(buf.read(1))
    major = b0 >> 5
    info  = b0 & 0x1f
    if info <= 23:   val = info
    elif info == 24: val = ord(buf.read(1))
    elif info == 25: val = _struct.unpack('>H', buf.read(2))[0]
    elif info == 26: val = _struct.unpack('>I', buf.read(4))[0]
    elif info == 27: val = _struct.unpack('>Q', buf.read(8))[0]
    else:            val = 0
    if major == 0: return val
    if major == 1: return -1 - val
    if major == 2: return buf.read(val)
    if major == 3: return buf.read(val).decode('utf-8', errors='replace')
    if major == 4: return [_cbor_read(buf) for _ in range(val)]
    if major == 5: return {_cbor_read(buf): _cbor_read(buf) for _ in range(val)}
    if major == 6: _cbor_read(buf); return None
    if major == 7:
        if info == 20: return False
        if info == 21: return True
        if info == 22: return None
    return None


def _resolve_rp(req):
    from urllib.parse import urlparse
    req_origin = req.headers.get('Origin') or req.url_root.rstrip('/')
    import os
    rp_id  = os.environ.get('WEBAUTHN_RP_ID')  or (urlparse(req_origin).hostname or WEBAUTHN_RP_ID)
    origin = os.environ.get('WEBAUTHN_ORIGIN') or req_origin
    return rp_id, origin


# ── Registration Begin ─────────────────────────────────────────────

@bp.route('/api/webauthn/register/begin', methods=['POST'])
def webauthn_register_begin():
    user_key = user_name = user_display = None
    if session.get('logged_in'):
        user_key     = f"admin_{session['admin_id']}"
        user_name    = session.get('admin_username', '')
        user_display = session.get('admin_display_name', user_name)
    elif session.get('punch_staff_id'):
        sid          = session['punch_staff_id']
        user_key     = f"staff_{sid}"
        user_name    = session.get('punch_staff_name', str(sid))
        user_display = user_name
    else:
        return jsonify({'error': '請先登入'}), 401

    rp_id, origin = _resolve_rp(request)
    challenge = secrets.token_bytes(32)
    session['webauthn_reg_challenge'] = _b64url_encode(challenge)
    session['webauthn_reg_user_key']  = user_key
    session['webauthn_reg_rp_id']     = rp_id
    session['webauthn_reg_origin']    = origin

    return jsonify({
        'rp': {'id': rp_id, 'name': WEBAUTHN_RP_NAME},
        'user': {
            'id': _b64url_encode(user_key.encode('utf-8')),
            'name': user_name, 'displayName': user_display,
        },
        'challenge': _b64url_encode(challenge),
        'pubKeyCredParams': [
            {'type': 'public-key', 'alg': -7},
            {'type': 'public-key', 'alg': -257},
        ],
        'timeout': 60000,
        'authenticatorSelection': {
            'authenticatorAttachment': 'platform',
            'userVerification': 'required',
            'residentKey': 'preferred',
        },
        'attestation': 'none',
    })


# ── Registration Complete ──────────────────────────────────────────

@bp.route('/api/webauthn/register/complete', methods=['POST'])
def webauthn_register_complete():
    import json as _j, hashlib as _hs
    challenge_b64    = session.get('webauthn_reg_challenge')
    user_key         = session.get('webauthn_reg_user_key')
    webauthn_rp_id   = session.get('webauthn_reg_rp_id', WEBAUTHN_RP_ID)
    webauthn_origin  = session.get('webauthn_reg_origin', WEBAUTHN_ORIGIN)
    if not challenge_b64 or not user_key:
        return jsonify({'error': '找不到挑戰，請重新開始'}), 400

    b = request.get_json(force=True) or {}
    try:
        credential_id = b['id']
        resp          = b['response']
        client_data   = _b64url_decode(resp['clientDataJSON'])
        attestation   = _b64url_decode(resp['attestationObject'])
        client_json   = _j.loads(client_data)

        assert client_json['type'] == 'webauthn.create', 'wrong type'
        assert client_json['challenge'].rstrip('=') == challenge_b64.rstrip('='), 'challenge mismatch'
        assert client_json['origin'] == webauthn_origin, f"origin mismatch: {client_json['origin']}"

        try:
            import cbor2
            att_obj = cbor2.loads(attestation)
        except ImportError:
            att_obj = _minimal_cbor_decode(attestation)

        auth_data      = att_obj.get(b'authData') or att_obj.get('authData', b'')
        rp_id_hash     = auth_data[:32]
        expected_hash  = _hs.sha256(webauthn_rp_id.encode()).digest()
        assert rp_id_hash == expected_hash, 'rpIdHash mismatch'
        flags = auth_data[32]
        assert flags & 0x01, 'User Presence not set'
        assert flags & 0x04, 'User Verification not set'

        cred_data      = auth_data[37:]
        cred_id_len    = _struct.unpack('>H', cred_data[16:18])[0]
        cred_id_bytes  = cred_data[18:18 + cred_id_len]
        cose_key_bytes = cred_data[18 + cred_id_len:]

        assert _b64url_encode(cred_id_bytes).rstrip('=') == credential_id.rstrip('='), 'credentialId mismatch'

        device_name = b.get('device_name', '我的裝置')
        with get_db() as conn:
            conn.execute("""
                INSERT INTO webauthn_credentials
                  (user_key, credential_id, public_key, sign_count, device_name)
                VALUES (%s, %s, %s, 0, %s)
                ON CONFLICT (credential_id) DO UPDATE
                  SET sign_count=0, device_name=%s
            """, (user_key, credential_id, cose_key_bytes, device_name, device_name))

        for k in ('webauthn_reg_challenge', 'webauthn_reg_user_key',
                  'webauthn_reg_rp_id', 'webauthn_reg_origin'):
            session.pop(k, None)
        return jsonify({'ok': True})
    except Exception as ex:
        return jsonify({'error': f'綁定失敗：{ex}'}), 400


# ── Authentication Begin ───────────────────────────────────────────

@bp.route('/api/webauthn/auth/begin', methods=['POST'])
def webauthn_auth_begin():
    b        = request.get_json(force=True) or {}
    username = (b.get('username') or '').strip()
    allow_credentials = []

    if username:
        with get_db() as conn:
            admin = conn.execute(
                "SELECT id FROM admin_accounts WHERE username=%s AND active=TRUE", (username,)
            ).fetchone()
            if admin:
                user_key = f"admin_{admin['id']}"
            else:
                staff = conn.execute(
                    "SELECT id FROM punch_staff WHERE username=%s AND active=TRUE", (username,)
                ).fetchone()
                user_key = f"staff_{staff['id']}" if staff else None

        if user_key:
            with get_db() as conn:
                creds = conn.execute(
                    "SELECT credential_id FROM webauthn_credentials WHERE user_key=%s", (user_key,)
                ).fetchall()
            allow_credentials = [{'type': 'public-key', 'id': r['credential_id']} for r in creds]

    rp_id, origin = _resolve_rp(request)
    challenge = secrets.token_bytes(32)
    session['webauthn_auth_challenge'] = _b64url_encode(challenge)
    session['webauthn_auth_rp_id']     = rp_id
    session['webauthn_auth_origin']    = origin

    return jsonify({
        'challenge': _b64url_encode(challenge),
        'timeout': 60000, 'rpId': rp_id,
        'allowCredentials': allow_credentials,
        'userVerification': 'required',
    })


# ── Authentication Complete ────────────────────────────────────────

@bp.route('/api/webauthn/auth/complete', methods=['POST'])
def webauthn_auth_complete():
    import json as _j, hashlib as _hs
    challenge_b64   = session.get('webauthn_auth_challenge')
    webauthn_rp_id  = session.get('webauthn_auth_rp_id',  WEBAUTHN_RP_ID)
    webauthn_origin = session.get('webauthn_auth_origin', WEBAUTHN_ORIGIN)
    if not challenge_b64:
        return jsonify({'error': '找不到挑戰，請重新開始'}), 400

    b = request.get_json(force=True) or {}
    try:
        credential_id = b['id']
        resp          = b['response']
        client_data   = _b64url_decode(resp['clientDataJSON'])
        auth_data     = _b64url_decode(resp['authenticatorData'])
        signature     = _b64url_decode(resp['signature'])
        client_json   = _j.loads(client_data)

        assert client_json['type'] == 'webauthn.get', 'wrong type'
        assert client_json['challenge'].rstrip('=') == challenge_b64.rstrip('='), 'challenge mismatch'
        assert client_json['origin'] == webauthn_origin, f"origin mismatch: {client_json['origin']}"

        rp_id_hash = auth_data[:32]
        assert rp_id_hash == _hs.sha256(webauthn_rp_id.encode()).digest(), 'rpIdHash mismatch'
        flags = auth_data[32]
        assert flags & 0x01, 'User Presence not set'
        assert flags & 0x04, 'User Verification not set'

        with get_db() as conn:
            cred = conn.execute(
                "SELECT * FROM webauthn_credentials WHERE credential_id=%s", (credential_id,)
            ).fetchone()
        if not cred:
            return jsonify({'error': '找不到已綁定的裝置，請先綁定'}), 401

        client_data_hash = _hs.sha256(client_data).digest()
        _verify_cose_signature(cred['public_key'], auth_data + client_data_hash, signature)

        new_sign_count = _struct.unpack('>I', auth_data[33:37])[0]
        with get_db() as conn:
            conn.execute(
                "UPDATE webauthn_credentials SET sign_count=%s WHERE id=%s",
                (new_sign_count, cred['id'])
            )

        for k in ('webauthn_auth_challenge', 'webauthn_auth_rp_id', 'webauthn_auth_origin'):
            session.pop(k, None)

        user_key = cred['user_key']
        if user_key.startswith('admin_'):
            admin_id = int(user_key[6:])
            with get_db() as conn:
                admin = conn.execute(
                    "SELECT * FROM admin_accounts WHERE id=%s AND active=TRUE", (admin_id,)
                ).fetchone()
            if not admin:
                return jsonify({'error': '帳號不存在或已停用'}), 401
            import json as _json2
            perms = admin['permissions']
            if isinstance(perms, str):
                try: perms = _json2.loads(perms)
                except: perms = []
            session['logged_in']          = True
            session['admin_id']           = admin['id']
            session['admin_username']     = admin['username']
            session['admin_display_name'] = admin['display_name'] or admin['username']
            session['admin_permissions']  = perms
            session['admin_is_super']     = bool(admin['is_super'])
            return jsonify({'ok': True, 'redirect': '/admin', 'role': 'admin'})

        elif user_key.startswith('staff_'):
            staff_id = int(user_key[6:])
            with get_db() as conn:
                staff = conn.execute(
                    "SELECT id, name, role FROM punch_staff WHERE id=%s AND active=TRUE", (staff_id,)
                ).fetchone()
            if not staff:
                return jsonify({'error': '帳號不存在或已停用'}), 401
            session['punch_staff_id']   = staff['id']
            session['punch_staff_name'] = staff['name']
            return jsonify({'ok': True, 'role': 'staff', 'user': dict(staff)})

        return jsonify({'error': '未知帳號類型'}), 400
    except Exception as ex:
        return jsonify({'error': f'驗證失敗：{ex}'}), 400


# ── Credentials List & Delete ──────────────────────────────────────

@bp.route('/api/webauthn/credentials', methods=['GET'])
def webauthn_list_credentials():
    if session.get('logged_in'):
        user_key = f"admin_{session['admin_id']}"
    elif session.get('punch_staff_id'):
        user_key = f"staff_{session['punch_staff_id']}"
    else:
        return jsonify({'error': '請先登入'}), 401
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, device_name, created_at FROM webauthn_credentials WHERE user_key=%s ORDER BY created_at DESC",
            (user_key,)
        ).fetchall()
    return jsonify([{'id': r['id'], 'device_name': r['device_name'],
                     'created_at': str(r['created_at'])} for r in rows])


@bp.route('/api/webauthn/credentials/<int:cid>', methods=['DELETE'])
def webauthn_delete_credential(cid):
    if session.get('logged_in'):
        user_key = f"admin_{session['admin_id']}"
    elif session.get('punch_staff_id'):
        user_key = f"staff_{session['punch_staff_id']}"
    else:
        return jsonify({'error': '請先登入'}), 401
    with get_db() as conn:
        conn.execute(
            "DELETE FROM webauthn_credentials WHERE id=%s AND user_key=%s", (cid, user_key)
        )
    return jsonify({'ok': True})
