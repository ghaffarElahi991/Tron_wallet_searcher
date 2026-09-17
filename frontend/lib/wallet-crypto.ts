import { secp256k1 } from "@noble/curves/secp256k1.js";
import { sha256 } from "@noble/hashes/sha2.js";
import { keccak_256 } from "@noble/hashes/sha3.js";

const BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function hexToBytes(value: string): Uint8Array {
  if (!/^[0-9a-fA-F]+$/.test(value) || value.length % 2 !== 0) {
    throw new Error("Invalid hexadecimal key material.");
  }
  return Uint8Array.from(value.match(/.{2}/g) ?? [], (byte) => Number.parseInt(byte, 16));
}

function bytesToBigInt(bytes: Uint8Array): bigint {
  return BigInt(`0x${bytesToHex(bytes)}`);
}

function base58Encode(bytes: Uint8Array): string {
  let value = bytesToBigInt(bytes);
  let encoded = "";
  while (value > 0n) {
    const remainder = Number(value % 58n);
    encoded = BASE58_ALPHABET[remainder] + encoded;
    value /= 58n;
  }
  for (const byte of bytes) {
    if (byte !== 0) break;
    encoded = `1${encoded}`;
  }
  return encoded;
}

function tronAddressFromPrivateKey(privateKey: Uint8Array): string {
  const uncompressedPublicKey = secp256k1.getPublicKey(privateKey, false);
  const digest = keccak_256(uncompressedPublicKey.slice(1));
  const payload = new Uint8Array(21);
  payload[0] = 0x41;
  payload.set(digest.slice(-20), 1);
  const firstHash = sha256(payload);
  const checksum = sha256(firstHash).slice(0, 4);
  const checked = new Uint8Array(payload.length + checksum.length);
  checked.set(payload);
  checked.set(checksum, payload.length);
  return base58Encode(checked);
}

export function verifyServerWallet(
  privateKeyHex: string,
  expectedAddress: string,
): { address: string; privateKey: string } {
  if (!/^[0-9a-fA-F]{64}$/.test(privateKeyHex)) {
    throw new Error("The server returned invalid private key material.");
  }
  const privateKey = hexToBytes(privateKeyHex);
  const derivedAddress = tronAddressFromPrivateKey(privateKey);
  if (derivedAddress !== expectedAddress) {
    throw new Error("Local wallet verification failed. The private key was hidden.");
  }
  return { address: derivedAddress, privateKey: privateKeyHex.toLowerCase() };
}
