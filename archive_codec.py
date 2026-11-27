#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
archive_codec.py — 流式分块 AES-256-GCM 编解码（内存恒定，可处理超大视频）
文件格式:
  MAGIC(8)="CHVAULT1" + VER(1)=1 + ORIG_SIZE(8,大端)
  重复块: LEN(4,大端) + NONCE(12) + CIPHERTEXT(LEN, 含16字节GCM tag)
  每块 AAD = block_index(8,大端) + 调用方aad  (防调换/防截断)
  注意：本库迁移照片时 aad = 内容指纹(md5字符串).encode()，解密要原样传入同名指纹。
解密时逐块校验 GCM tag，并核对总字节==ORIG_SIZE。
"""
import os
import struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"CHVAULT1"
VER = 1
BLOCK = 4 * 1024 * 1024
_HDR = struct.Struct(">8sBQ")      # magic, ver, orig_size
_LEN = struct.Struct(">I")


def encrypt_stream(key, fsrc, fdst, orig_size, aad=b""):
    """从文件对象 fsrc 读明文，流式写密文到 fdst。"""
    aes = AESGCM(key)
    fdst.write(_HDR.pack(MAGIC, VER, orig_size))
    idx = 0
    while True:
        chunk = fsrc.read(BLOCK)
        if not chunk:
            break
        nonce = os.urandom(12)
        ct = aes.encrypt(nonce, chunk, idx.to_bytes(8, "big") + aad)
        fdst.write(_LEN.pack(len(ct)))
        fdst.write(nonce)
        fdst.write(ct)
        idx += 1
    return idx


def decrypt_stream(key, fsrc, fdst, aad=b""):
    """从 fsrc 读密文，流式还原明文到 fdst；返回原始字节数。"""
    aes = AESGCM(key)
    head = fsrc.read(_HDR.size)
    magic, ver, size = _HDR.unpack(head)
    if magic != MAGIC or ver != VER:
        raise ValueError("不是CHVAULT格式或版本不符")
    idx = 0
    total = 0
    while True:
        lb = fsrc.read(_LEN.size)
        if not lb:
            break
        (ln,) = _LEN.unpack(lb)
        nonce = fsrc.read(12)
        ct = fsrc.read(ln)
        pt = aes.decrypt(nonce, ct, idx.to_bytes(8, "big") + aad)
        fdst.write(pt)
        total += len(pt)
        idx += 1
    if total != size:
        raise ValueError(f"长度不符: 解出{total} 记录{size}")
    return total
