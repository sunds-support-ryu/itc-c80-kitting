"""Offline RSA/AES helpers matching config_import.py; no requests or file writes."""
import base64
import re


def create_challenge():
    from cryptography.hazmat.primitives.asymmetric import rsa
    private = rsa.generate_private_key(public_exponent=3, key_size=1024)
    modulus = private.public_key().public_numbers().n.to_bytes(128, 'big')
    public = base64.b64encode(base64.b64encode(modulus)).decode('ascii')
    return private, {'PublicKey': public}


def encrypt_import_password(private, response_xml, import_password):
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = re.search(r'<key>(.*?)</key>', response_xml, re.S)
    key_id = re.search(r'<keyID>(.*?)</keyID>', response_xml, re.S)
    if not key or not key_id:
        raise ValueError('Handshake key/keyID missing')
    encrypted = bytes.fromhex(base64.b64decode(key.group(1), validate=True).decode('ascii'))
    aes = private.decrypt(encrypted, rsa_padding.PKCS1v15())
    padder = padding.PKCS7(128).padder()
    padded = padder.update(import_password.encode('utf-8')) + padder.finalize()
    encryptor = Cipher(algorithms.AES(aes[:16]), modes.CBC(b'0123456789ABCDEF')).encryptor()
    password = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode('ascii')
    return {'keyId': key_id.group(1), 'encryptPwd': password}


if __name__ == '__main__':
    print('Offline helpers: create_challenge(), then encrypt_import_password(private, XML, password).')
    print('Keep the same private key in memory between these calls. No upload is performed.')
