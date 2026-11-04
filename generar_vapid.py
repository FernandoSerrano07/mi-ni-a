"""Genera las llaves VAPID para Web Push. Ejecuta: python generar_vapid.py
Copia las dos líneas que imprime en tu archivo .env (o en las variables del servidor)."""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
priv = key.private_numbers().private_value.to_bytes(32, "big")
pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

print(f"VAPID_PUBLIC_KEY={b64url(pub)}")
print(f"VAPID_PRIVATE_KEY={b64url(priv)}")
