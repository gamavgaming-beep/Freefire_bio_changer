from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import binascii
import base64
import json
import jwt
import time
from datetime import datetime, timedelta, timezone
import my_pb2
import output_pb2
import proto_long_bio_pb2
from urllib.parse import urlparse, parse_qs

app = Flask(__name__)
CORS(app)

JWT_LIFETIME_HOURS = 7
jwt_token_cache = {}

XOR_KEY = b"1e5898ccb8dfdd921f9bdea848768b64a201"

PLATFORM_MAP = {
    3: "Facebook",
    4: "Guest",
    5: "VK",
    8: "Google",
    10: "AppleId",
    11: "X (Twitter)"
}

def decode_nickname(encoded: str) -> str:
    try:
        raw = base64.b64decode(encoded)
        dec = bytearray()
        for i, b in enumerate(raw):
            dec.append(b ^ XOR_KEY[i % len(XOR_KEY)])
        return dec.decode('utf-8', errors='replace')
    except Exception:
        return encoded

def decode_jwt(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1] + '=' * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        if 'nickname' in payload and isinstance(payload['nickname'], str):
            payload['nickname'] = decode_nickname(payload['nickname'])
        return payload
    except Exception:
        return {}

def get_player_info_from_external_api(uid: str) -> dict:
    try:
        url = f"http://203.57.85.58:2035/player-info?uid={uid}&key=@yashapis"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            basic = data.get("basicInfo", {})
            social = data.get("socialInfo", {})
            return {
                "nickname": basic.get("nickname", "Unknown"),
                "accountId": basic.get("accountId", uid),
                "region": basic.get("region", "Unknown"),
                "level": basic.get("level", "N/A"),
                "likes": basic.get("liked", 0),
                "signature": social.get("signature", "")
            }
    except Exception:
        pass
    return {
        "nickname": "Unknown",
        "accountId": uid,
        "region": "Unknown",
        "level": "N/A",
        "likes": 0,
        "signature": ""
    }

def encrypt_message(plaintext, key_bytes, iv_bytes):
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
    padded_message = pad(plaintext, AES.block_size)
    return cipher.encrypt(padded_message)

def encrypt_bio_data(plaintext):
    key = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded_message = pad(plaintext, AES.block_size)
    encrypted_message = cipher.encrypt(padded_message)
    return binascii.hexlify(encrypted_message).decode('utf-8')

def get_oauth_token(uid, password):
    oauth_url = "https://100067.connect.garena.com/oauth/guest/token/grant"
    payload = {
        'uid': uid,
        'password': password,
        'response_type': "token",
        'client_type': "2",
        'client_secret': "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        'client_id': "100067"
    }
    headers = {
        'User-Agent': "GarenaMSDK/4.0.19P9(SM-M526B ;Android 13;pt;BR;)",
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip"
    }
    try:
        oauth_response = requests.post(oauth_url, data=payload, headers=headers, timeout=10)
        if oauth_response.status_code == 200:
            oauth_data = oauth_response.json()
            if 'access_token' in oauth_data and 'open_id' in oauth_data:
                return oauth_data
    except requests.RequestException:
        pass
    return None

def get_token_inspect_data(access_token):
    try:
        resp = requests.get(
            f"https://100067.connect.garena.com/oauth/token/inspect?token={access_token}",
            timeout=15,
            verify=False
        )
        data = resp.json()
        if 'open_id' in data and 'platform' in data and 'uid' in data:
            return data
    except Exception:
        pass
    return None

def extract_params_from_url(url: str):
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        result = {}
        if 'access_token' in params:
            result['access_token'] = params['access_token'][0]
        if 'region' in params:
            result['region'] = params['region'][0]
        if 'account_id' in params:
            result['game_uid'] = params['account_id'][0]
        if 'nickname' in params:
            result['nickname'] = params['nickname'][0]
        return result
    except Exception:
        return {}

def eat_to_access_token(eat_token: str):
    try:
        callback_url = f"https://api-otrss.garena.com/support/callback/?access_token={eat_token}"
        response = requests.get(callback_url, allow_redirects=True, timeout=30, verify=False)
        if 'help.garena.com' in response.url:
            params = extract_params_from_url(response.url)
            if 'access_token' in params:
                token_data = get_token_inspect_data(params['access_token'])
                if token_data:
                    return {
                        'success': True,
                        'access_token': params['access_token'],
                        'region': params.get('region'),
                        'game_uid': params.get('game_uid'),
                        'nickname': params.get('nickname'),
                        'platform_type': token_data.get('platform', 4),
                        'open_id': token_data.get('open_id'),
                        'uid': token_data.get('uid')
                    }
        return {'success': False, 'error': 'INVALID_EAT_TOKEN'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def major_login(access_token, open_id, platform_type=4):
    key_bytes = b'Yg&tc%DEuh6%Zc^8'[:16]
    iv_bytes = b'6oyZDr22E3ychjM%'[:16]
    max_retries = 5
    for attempt in range(max_retries):
        try:
            game_data = my_pb2.GameData()
            game_data.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            game_data.game_name = "free fire"
            game_data.game_version = 1
            game_data.version_code = "1.123.1"
            game_data.os_info = "Android OS 9 / API-28"
            game_data.device_type = "Handheld"
            game_data.network_provider = "Verizon Wireless"
            game_data.connection_type = "WIFI"
            game_data.screen_width = 1280
            game_data.screen_height = 960
            game_data.dpi = "240"
            game_data.cpu_info = "ARMv7 VFPv3 NEON VMH | 2400 | 4"
            game_data.total_ram = 5951
            game_data.gpu_name = "Adreno (TM) 640"
            game_data.gpu_version = "OpenGL ES 3.0"
            game_data.user_id = "Google|74b585a9-0268-4ad3-8f36-ef41d2e53610"
            game_data.ip_address = "172.190.111.97"
            game_data.language = "en"
            game_data.open_id = open_id
            game_data.access_token = access_token
            game_data.platform_type = platform_type
            game_data.field_99 = str(platform_type)
            game_data.field_100 = str(platform_type)

            serialized_data = game_data.SerializeToString()
            encrypted_data = encrypt_message(serialized_data, key_bytes, iv_bytes)
            hex_encrypted_data = binascii.hexlify(encrypted_data).decode('utf-8')

            url = "https://loginbp.ggpolarbear.com/MajorLogin"
            headers = {
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip",
                "Content-Type": "application/octet-stream",
                "Expect": "100-continue",
                "X-Unity-Version": "2018.4.11f1",
                "X-GA": "v1 1",
                "ReleaseVersion": "OB53"
            }
            edata = bytes.fromhex(hex_encrypted_data)
            response = requests.post(url, data=edata, headers=headers, timeout=10)
            if response.status_code == 200:
                example_msg = output_pb2.Garena_420()
                example_msg.ParseFromString(response.content)
                if example_msg.token:
                    return example_msg.token
        except Exception:
            pass
        time.sleep(2)
    return None

def get_region_endpoint(jwt_token):
    try:
        decoded = decode_jwt(jwt_token)
        region = decoded.get("lock_region") or decoded.get("noti_region", "").upper()
        if region == "IND":
            return "https://client.ind.freefiremobile.com/UpdateSocialBasicInfo"
        elif region in ["BR", "US", "NA", "SAC"]:
            return "https://client.us.freefiremobile.com/UpdateSocialBasicInfo"
        else:
            return "https://clientbp.ggpolarbear.com/UpdateSocialBasicInfo"
    except Exception:
        return "https://clientbp.ggpolarbear.com/UpdateSocialBasicInfo"

def update_bio_with_token(bio_text, jwt_token):
    try:
        data_msg = proto_long_bio_pb2.Data()
        data_msg.field_2 = 17
        data_msg.field_8 = bio_text
        data_msg.field_9 = 1
        data_msg.field_5.SetInParent()
        data_msg.field_6.SetInParent()
        data_msg.field_11.SetInParent()
        data_msg.field_12.SetInParent()

        encrypted_data_hex = encrypt_bio_data(data_msg.SerializeToString())
        data_bytes_send = binascii.unhexlify(encrypted_data_hex)

        primary_endpoint = get_region_endpoint(jwt_token)
        endpoints = [
            primary_endpoint,
            "https://clientbp.ggpolarbear.com/UpdateSocialBasicInfo",
            "https://client.ind.freefiremobile.com/UpdateSocialBasicInfo",
            "https://client.us.freefiremobile.com/UpdateSocialBasicInfo"
        ]

        headers = {
            "Expect": "100-continue",
            "Authorization": f"Bearer {jwt_token}",
            "X-Unity-Version": "2018.4.11f1",
            "X-GA": "v1 1",
            "ReleaseVersion": "OB53",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; SM-A305F Build/RP1A.200720.012)",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip"
        }

        decoded = decode_jwt(jwt_token)
        region = decoded.get("lock_region") or decoded.get("noti_region", "Unknown")

        for url in endpoints:
            try:
                headers["Host"] = url.split("//")[1].split("/")[0]
                response = requests.post(url, headers=headers, data=data_bytes_send, timeout=10)
                if response.status_code == 200:
                    return {"success": True, "message": "Bio updated successfully", "region": region}
            except Exception:
                continue
        return {"success": False, "message": "Failed to update bio"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def get_jwt_token(uid, password):
    cache_key = f"{uid}_{password}"
    token_data = jwt_token_cache.get(cache_key)
    if token_data and token_data['expiry'] > datetime.utcnow():
        return token_data['token']
    oauth_data = get_oauth_token(uid, password)
    if not oauth_data:
        return None
    token = major_login(oauth_data['access_token'], oauth_data['open_id'])
    if token:
        jwt_token_cache[cache_key] = {'token': token, 'expiry': datetime.utcnow() + timedelta(hours=JWT_LIFETIME_HOURS)}
        return token
    return None

def get_jwt_from_access_token(access_token):
    cache_key = f"access_{access_token}"
    token_data = jwt_token_cache.get(cache_key)
    if token_data and token_data['expiry'] > datetime.utcnow():
        return token_data['token']
    insp = get_token_inspect_data(access_token)
    if not insp:
        return None
    token = major_login(access_token, insp['open_id'], insp['platform'])
    if token:
        jwt_token_cache[cache_key] = {'token': token, 'expiry': datetime.utcnow() + timedelta(hours=JWT_LIFETIME_HOURS)}
        return token
    return None

# ------------------- LOGIN ENDPOINT -------------------
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Invalid request"}), 400

    uid = data.get('uid')
    password = data.get('password')
    access_token = data.get('access_token')
    jwt_token = data.get('jwt_token')
    eat_token = data.get('eat_token')

    final_jwt = None

    if uid and password:
        final_jwt = get_jwt_token(uid, password)
        if not final_jwt:
            return jsonify({"success": False, "error": "Invalid UID/password"}), 400
    elif access_token:
        final_jwt = get_jwt_from_access_token(access_token)
        if not final_jwt:
            return jsonify({"success": False, "error": "Invalid access token"}), 400
    elif jwt_token:
        decoded = decode_jwt(jwt_token)
        if not decoded.get('account_id'):
            return jsonify({"success": False, "error": "Invalid JWT token"}), 400
        final_jwt = jwt_token
    elif eat_token:
        eat_result = eat_to_access_token(eat_token)
        if not eat_result.get('success'):
            return jsonify({"success": False, "error": "Invalid EAT token"}), 400
        final_jwt = get_jwt_from_access_token(eat_result['access_token'])
        if not final_jwt:
            return jsonify({"success": False, "error": "Failed to generate JWT from EAT token"}), 400
    else:
        return jsonify({"success": False, "error": "Provide uid+password, access_token, jwt_token, or eat_token"}), 400

    decoded = decode_jwt(final_jwt)
    account_id = decoded.get("account_id")
    if not account_id:
        return jsonify({"success": False, "error": "JWT missing account_id"}), 400

    player_info = get_player_info_from_external_api(str(account_id))
    if player_info["nickname"] == "Unknown":
        player_info["nickname"] = decoded.get("nickname", "Unknown")

    return jsonify({
        "success": True,
        "jwt": final_jwt,
        "nickname": player_info["nickname"],
        "accountId": str(player_info["accountId"]),
        "level": player_info["level"],
        "liked": player_info["likes"],
        "signature": player_info["signature"],
        "region": player_info["region"]
    })

# ------------------- UPDATE BIO ENDPOINT -------------------
@app.route('/update_bio', methods=['POST'])
def update_bio():
    try:
        data = request.get_json(silent=True) or {}
        bio = data.get('signature') or data.get('bio')
        jwt_token = data.get('jwt_token') or data.get('token')

        if not bio:
            return jsonify({"success": False, "error": "Bio is required"}), 400
        if len(bio) > 250:
            return jsonify({"success": False, "error": "Bio must be 250 characters or less"}), 400
        if not jwt_token:
            return jsonify({"success": False, "error": "JWT token required"}), 400

        result = update_bio_with_token(bio, jwt_token)
        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500

# ------------------- SERVE WEB PAGE & LIBRARY -------------------
@app.route('/')
def serve_index():
    return send_file('index.html')

@app.route('/lib.json')
def serve_lib():
    return jsonify({
        "templates": [
            {"code": "[B][C][FFFFFF]Sɪʀғ Aᴀᴡᴀᴢ Pᴇ Cʜᴀʜᴜɴ Tᴏ [00E5FF]Nɪsʜᴀɴᴇ[FFFFFF] Lᴀɢ Jᴀᴀʏᴇɪɴ, Mᴀɪɴ Aɢᴀʀ Aᴘɴɪ [00FF4C]Jᴀᴡᴀɴɪ Kᴇ[FFFFFF] Sᴜɴᴀ Dᴜ [FF4DFF]Kɪssᴇ[FFFFFF], Yᴇ Jᴏ Lᴀᴜɴᴅᴇ Hᴀɪɴ, Mᴇʀᴇ [FF2B2B]Pᴀᴏɴ[FFFFFF] Dᴀʙᴀɴᴇ Lᴀɢ Jᴀᴀʏᴇɪɴ !!!"},
            {"code": "[b][c][40e0d0]✧･ﾟ: *✧ [b][c][ffffff]   Matching name, matching heart [b][c][40e0d0]✧*:･ﾟ✧"},
            {"code": "[B][C]╱◥██████◣ [ffd319]│∩│▤│▤│▤ ▓▓▓▓▓▓▓▓▓ FF LOVE"}
        ]
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=False)