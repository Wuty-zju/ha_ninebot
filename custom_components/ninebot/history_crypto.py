"""MIT protocol primitives from kxn/ninebot-recon, pinned to a46124d.

Copyright (c) 2026 kxn. See licenses/ninebot-recon-MIT.txt and SOURCES.json.
Only history wire encoding/decoding is used. No transport, login, token storage
or CLI is vendored. CBC/RSA-1024/MD5 are upstream protocol requirements, not
local archive encryption. cryptography is provided by supported HA Core.
"""

import base64
import hashlib
import json
import secrets
import struct
import time
from typing import Any

# ---------------------------------------------------------------------------
# 常量（从二进制 .rodata 提取）
# ---------------------------------------------------------------------------

# GenKReq 字符集：94 个可打印 ASCII（0x21-0x7e）
CHARSET_KREQ = "".join(chr(value) for value in range(0x21, 0x7F))
# GenKeyData 第 4 分量字符集：69 字符
CHARSET_KEYDATA4 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789;-_.,+/"
# randHex 字符集：小写 hex
CHARSET_HEX = "0123456789abcdef"

# 内置 RSA-1024 公钥 —— 2026-08-07 确认二进制中有两把公钥：
#   N1 (d3de6d1c..., DER SPKI @VA 0xc2e020) = crypto.init.0 解析并缓存(0xc87c00)、
#   EncryptRequest 实际使用的密钥（GDB 抓 EncryptPKCS1v15 实参确认）
#   N2 (ba99a158..., PEM 字符串 @VA 0xc2fae5) = 死数据，无任何代码引用（旧版遗留）
# 此前版本误用 N2 的 PEM；真实服务器只认 N1。
RSA_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDT3m0c/8y9c13PzaFbATEg+Zwd
kpPcCy0V21VKBBSx16ckVtLERAQ7EH8d6DqgEbyzayAwlQd1gDhUmx27hDWafXr9
/evUZkkBegcsNnKrIlh93lPKccjk+LDXS1TnDIIFiTlSbNnaYwehI/9pUbKCI3h7
yE0pum6hJh/9QtGPlwIDAQAB
-----END PUBLIC KEY-----"""

# wire 层固定参数（抓包确认）
WIRE_P = "101"
WIRE_T = "0"
# business 域（api-jhcx / cbu-gateway / ebike）wrapper 统一 platform=2、timeStamp=秒
# （GDB 抓 BuildWrapper 输入确认：登录/vehicles/travel 全为 platform 2 + Unix 秒）
PLATFORM = 2


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _rol32(v: int, n: int) -> int:
    n %= 32
    return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF


def _rol8acc(s: bytes) -> int:
    """v = 0; for b in s: v = rol32(v,8) ^ b  （DeriveKey 输入的累加）"""
    v = 0
    for b in s:
        v = _rol32(v, 8) ^ b
    return v & 0xFFFFFFFF


def _rand_int(bound: int) -> int:
    """拒绝采样随机整数 [0, bound) —— 与二进制 rand.IntN 行为一致"""
    return secrets.randbelow(bound)


# ---------------------------------------------------------------------------
# 密钥材料生成
# ---------------------------------------------------------------------------


def rand_hex(n: int) -> str:
    return "".join(CHARSET_HEX[_rand_int(16)] for _ in range(n))


def rand_charset(n: int, charset: str) -> str:
    return "".join(charset[_rand_int(len(charset))] for _ in range(n))


def gen_kreq() -> bytes:
    """AES-128 密钥材料：16 字节，94 字符集"""
    return rand_charset(16, CHARSET_KREQ).encode()


def gen_key_data() -> tuple[str, str, str, str]:
    """4 个密钥分量（响应解密用）: (randHex(11), randHex(11), randHex(10), randCharset(8))"""
    return (rand_hex(11), rand_hex(11), rand_hex(10), rand_charset(8, CHARSET_KEYDATA4))


def gen_nonce() -> str:
    """GenNonce @0x6ca9e0：16 随机字节 -> 32 hex，首字节小写、其余大写。

    抓包样例: 7dB0201E322B7B54BF9117E4E11EFC0E / f39292B0A02157954B86A733248503C6
    """
    raw = bytes(_rand_int(256) for _ in range(16))
    h = raw.hex()
    return h[0:2].lower() + h[2:].upper()


# ---------------------------------------------------------------------------
# DeriveKey —— 公式经 unicorn 执行二进制机器码验证（11 组测试 ALL PASS）
# ---------------------------------------------------------------------------


def derive_key(a: bytes, b: bytes, c: bytes, d: bytes) -> bytes:
    """DeriveKey(A,B,C,D) -> 16 字节 AES 密钥。

    反汇编推导（crypto.DeriveKey @0x6c99e0）:
      A..D 各自做 ROL8 累加得 4 个 32 位分量;
      x = A^C, y = B^D;  fx = rol(x,8)^x^rol(x,24);  gy = rol(y,8)^y^rol(y,24);
      a'=A^gy, b'=B^fx, c'=C^gy, d'=D^fx;
      out3 = (c'&b')^a'
      tmp  = ~(c'^d'^((c'|d')^b'))
      out2 = out3^tmp
      out1 = (tmp|out3)^((c'|d')^b')
      out0 = (~((c'|d')^b')&out2)^d'
      key = out0|out1|out2|out3 小端序 16 字节
    """
    A = _rol8acc(a)
    B = _rol8acc(b)
    C = _rol8acc(c)
    D = _rol8acc(d)
    x = A ^ C
    y = B ^ D
    fx = _rol32(x, 8) ^ x ^ _rol32(x, 24)
    gy = _rol32(y, 8) ^ y ^ _rol32(y, 24)
    a1 = A ^ gy
    b1 = B ^ fx
    c1 = C ^ gy
    d1 = D ^ fx
    out3 = (c1 & b1) ^ a1
    tmp = (~(c1 ^ d1 ^ ((c1 | d1) ^ b1))) & 0xFFFFFFFF
    out2 = out3 ^ tmp
    out1 = ((tmp | out3) ^ ((c1 | d1) ^ b1)) & 0xFFFFFFFF
    out0 = ((~((c1 | d1) ^ b1) & out2) ^ d1) & 0xFFFFFFFF
    return struct.pack("<IIII", out0, out1, out2, out3)


# ---------------------------------------------------------------------------
# AndroidB64 —— 行为经 unicorn 执行二进制机器码验证（9 组测试 ALL PASS）
# ---------------------------------------------------------------------------


def android_b64(data: bytes) -> str:
    """模拟 ninecli 的 AndroidB64：标准 base64，每 76 字符插 \\n，非空时末尾 \\n"""
    b = base64.b64encode(data)
    if not b:
        return ""
    lines = [b[i : i + 76] for i in range(0, len(b), 76)]
    return b"\n".join(lines).decode() + "\n"


# ---------------------------------------------------------------------------
# JSON 转义（jsonEscapeString @0x6cc7a0 行为：标准转义 + 首尾引号）
# ---------------------------------------------------------------------------


def _json_escape(s: str) -> str:
    """模拟二进制 jsonEscapeString：返回带引号的转义字符串"""
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


# ---------------------------------------------------------------------------
# 明文 wrapper（BuildWrapper @0x6cc4c0，字段拼接顺序与二进制完全一致）
# ---------------------------------------------------------------------------


def build_wrapper(
    data: bytes,
    kd1: str,
    kd2: str,
    kd3: str,
    kd4: str,
    platform: int = PLATFORM,
    timestamp: int | None = None,
    timestamp_seconds: bool = True,
) -> str:
    """business 域 wrapper：platform=2、timeStamp=Unix 秒（GDB 抓包确认）。

    timestamp_seconds=False 时使用毫秒（通用层语义，保留兼容）。
    """
    if timestamp is None:
        ts = int(time.time()) if timestamp_seconds else int(time.time() * 1000)
    else:
        ts = timestamp
    parts = [
        "{\n",
        '\t"data" : ',
        _json_escape(android_b64(data)),
        ",\n",
        '\t"keyDataFour" : "',
        kd4,
        '",\n',
        '\t"keyDataOne" : "',
        kd1,
        '",\n',
        '\t"keyDataThree" : "',
        kd3,
        '",\n',
        '\t"keyDataTwo" : "',
        kd2,
        '",\n',
        '\t"platform" : ',
        str(platform),
        ",\n",
        '\t"timeStamp" : ',
        str(ts),
        "\n}\n",
    ]
    return "".join(parts)


# ---------------------------------------------------------------------------
# 内层 JSON（BuildInner @0x6cece0）：Go map + encoding/json（键排序、紧凑、
# HTMLEscape）+ checkcode 追加。2026-08-07 GDB 抓 446B 输入 100% 复现。
# ---------------------------------------------------------------------------


def _go_json_dumps(d: dict) -> str:
    """精确模拟二进制 EncodeOrderedJSON @0x6cbf00 + writeJSONValue @0x6cc0a0。

    **按字段声明顺序**输出（非 map 排序）、紧凑无空格、字符串只转义
    \\n \\t \\r \" \\ 五个字符，<>& / U+2028 / U+2029 / 控制字符 / 非 ASCII
    全部原样 UTF-8 —— 2026-08-07 反汇编逐句确认：writeJSONValue 字符串分支
    (0x6cc362-0x6cc485) 无 HTML escape，key 直接 append 不转义。
    此前版本按 Go encoding/json 默认行为多做 <>& 与 U+2028/2029 转义，已修正。

    GDB 抓包证实内层 JSON 是 Go struct 序列化（字段顺序固定）：
    business login / vehicles / travel-list / travel-info 各有固定字段序，
    并非 map 字母序。调用方须按抓包顺序构造 dict。
    """

    def _val(v: Any) -> str:
        if isinstance(v, str):
            return _json_escape(v)  # 5 转义 + 首尾引号（与 writeJSONValue 一致）
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int,)):
            return str(v)  # 0x48df60 十进制
        if isinstance(v, float):
            return repr(v)  # 理论差异：二进制用 strconv 'f'，业务无 float
        if v is None:
            return "null"
        if isinstance(v, dict):
            return "{" + ",".join(_json_escape(str(k)) + ":" + _val(x) for k, x in v.items()) + "}"
        if isinstance(v, (list, tuple)):
            return "[" + ",".join(_val(x) for x in v) + "]"
        return _json_escape(str(v))

    return "{" + ",".join(_json_escape(str(k)) + ":" + _val(x) for k, x in d.items()) + "}"


def build_inner(params: dict) -> str:
    """内层业务 JSON：先求无 checkcode 的 MD5（大写 hex）作为 checkcode，
    再输出含 checkcode 的完整紧凑 JSON（字段顺序保持 params 传入顺序）。

    params 须按抓包字段顺序构造（见 api.py 的 COMMON_PARAMS_ORDER 等）。
    """
    params = dict(params)
    inner_no_cc = _go_json_dumps(params)
    checkcode = hashlib.md5(inner_no_cc.encode()).hexdigest().upper()
    params["checkcode"] = checkcode
    return _go_json_dumps(params)


# ---------------------------------------------------------------------------
# 加密 / 解密（AES-128-CBC, IV=16 个零字节, PKCS7）
# ---------------------------------------------------------------------------


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes, block: int = 16) -> bytes:
    if not data:
        raise ValueError("empty data")
    pad = data[-1]
    if pad < 1 or pad > block or data[-pad:] != bytes([pad]) * pad:
        raise ValueError("bad PKCS7 padding")
    return data[:-pad]


def _aes_cbc_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """AES-128-CBC, IV = 16×0x00, PKCS7 —— 与二进制 AESEncrypt @0x6ccac0 一致"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = b"\x00" * 16
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(_pkcs7_pad(plaintext)) + enc.finalize()


def _aes_cbc_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = b"\x00" * 16
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = dec.update(ciphertext) + dec.finalize()
    return _pkcs7_unpad(padded)


def _rsa_encrypt(pub_pem: str, data: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pub = serialization.load_pem_public_key(pub_pem.encode())
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

    if not isinstance(pub, RSAPublicKey):
        raise ValueError("Not an RSA key")
    return pub.encrypt(data, padding.PKCS1v15())


# ---------------------------------------------------------------------------
# 请求封装（EncryptRequest @0x6ca000）
# ---------------------------------------------------------------------------


def encrypt_request(
    data: bytes,
    aes_key: bytes,
    kd1: str,
    kd2: str,
    kd3: str,
    kd4: str,
    platform: int = PLATFORM,
    timestamp: int | None = None,
    timestamp_seconds: bool = True,
) -> dict:
    """生成 wire 请求体 {"d","h","k","p","t"}"""
    wrapper = build_wrapper(
        data, kd1, kd2, kd3, kd4, platform, timestamp, timestamp_seconds
    ).encode()
    d_raw = _aes_cbc_encrypt(aes_key, wrapper)
    h = hashlib.md5(wrapper).hexdigest()
    k_raw = _rsa_encrypt(RSA_PUBLIC_KEY_PEM, aes_key)
    return {
        "d": base64.b64encode(d_raw).decode(),
        "h": h,
        "k": base64.b64encode(k_raw).decode(),
        "p": WIRE_P,
        "t": WIRE_T,
    }


# ---------------------------------------------------------------------------
# 响应解密（DecryptResponse @0x6ca300）
# ---------------------------------------------------------------------------


def decrypt_response(r_b64: str, kd1: str, kd2: str, kd3: str, kd4: str) -> bytes:
    """解 {"r": b64} -> 解密明文 bytes。key = DeriveKey(kd1..kd4)

    **2026-08-07 反汇编 + GDB 实测确认：解密明文是 wrapper JSON**
    （{"data": AndroidB64(业务JSON), "platform": ..., "timeStamp": ...}），
    业务 JSON 在 wrapper.data 里，需再 AndroidB64 解一层（见 unwrap_response）。
    二进制 DecryptResponse @0x6ca300：AES 解密 → unescapeJSONString 取
    'data' → AndroidB64 decode；错误串 "missing 'data' field in wrapper"
    证实响应也是 wrapper 封装。
    """
    r = base64.b64decode(r_b64)
    key = derive_key(kd1.encode(), kd2.encode(), kd3.encode(), kd4.encode())
    return _aes_cbc_decrypt(key, r)


def unwrap_response(plain: bytes) -> bytes:
    """解析响应 wrapper：取 data 字段（AndroidB64）解出业务 JSON bytes。"""
    w = json.loads(plain.decode("utf-8"))
    if not isinstance(w, dict) or not isinstance(w.get("data"), str):
        raise ValueError("missing 'data' field in wrapper")
    return base64.b64decode(w["data"].strip())


# ---------------------------------------------------------------------------
# 高层便捷函数
# ---------------------------------------------------------------------------


def build_request(
    data: bytes,
    platform: int = PLATFORM,
    timestamp: int | None = None,
    timestamp_seconds: bool = True,
) -> tuple[dict, dict]:
    """生成完整请求 + 保留密钥材料（用于解密响应）。

    data: 业务 JSON bytes（如 {"wnumber":"...","month":"202607"}）
    返回 (wire_body, secrets)  secrets 含 aes_key / kd1..4
    """
    aes_key = gen_kreq()
    kd1, kd2, kd3, kd4 = gen_key_data()
    body = encrypt_request(
        data, aes_key, kd1, kd2, kd3, kd4, platform, timestamp, timestamp_seconds
    )
    return body, {"aes_key": aes_key, "kd1": kd1, "kd2": kd2, "kd3": kd3, "kd4": kd4}


def decrypt_wire_response(resp_body: dict, secrets: dict) -> bytes:
    """解密并解包响应。resp_body: {"r": "b64..."}

    流程（与二进制 DecryptResponse @0x6ca300 一致）：
    b64(r) → AES-CBC 解密(DeriveKey(kd)) → wrapper JSON → data 字段
    AndroidB64 解码 → 业务 JSON bytes
    """
    plain = decrypt_response(
        resp_body["r"], secrets["kd1"], secrets["kd2"], secrets["kd3"], secrets["kd4"]
    )
    return unwrap_response(plain)
