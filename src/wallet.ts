import { Keypair } from '@solana/web3.js';
import bs58 from 'bs58';
import * as bip39 from 'bip39';
import { derivePath } from 'ed25519-hd-key';
import { config } from './config.js';

/**
 * Trust Wallet exports a BIP39 mnemonic; its Solana accounts sit at
 * m/44'/501'/0'/0'. We support that path so an existing wallet *can* be
 * used, but see the README: a dedicated wallet is the correct setup.
 */
function fromMnemonic(mnemonic: string, path: string): Keypair {
  const normalized = mnemonic.trim().replace(/\s+/g, ' ').toLowerCase();
  if (!bip39.validateMnemonic(normalized)) {
    throw new Error('WALLET_MNEMONIC is not a valid BIP39 mnemonic');
  }
  const seed = bip39.mnemonicToSeedSync(normalized);
  const derived = derivePath(path, seed.toString('hex')).key;
  return Keypair.fromSeed(derived.subarray(0, 32));
}

function fromBase58(secret: string): Keypair {
  let bytes: Uint8Array;
  try {
    bytes = bs58.decode(secret.trim());
  } catch {
    throw new Error('WALLET_PRIVATE_KEY is not valid base58');
  }
  if (bytes.length !== 64) {
    throw new Error(
      `WALLET_PRIVATE_KEY decoded to ${bytes.length} bytes, expected 64 (a full Solana secret key). ` +
        'Run `npm run new-wallet` to generate one in the right format.',
    );
  }
  try {
    // Length alone is not enough: this also verifies the embedded public key
    // matches the private half, which catches truncated or spliced keys.
    return Keypair.fromSecretKey(bytes);
  } catch {
    throw new Error(
      'WALLET_PRIVATE_KEY is 64 bytes but not a valid Solana keypair. ' +
        'Check it was copied whole, or run `npm run new-wallet` to generate a fresh one.',
    );
  }
}

export function loadWallet(): Keypair {
  if (config.WALLET_PRIVATE_KEY) return fromBase58(config.WALLET_PRIVATE_KEY);
  if (config.WALLET_MNEMONIC) {
    return fromMnemonic(config.WALLET_MNEMONIC, config.WALLET_DERIVATION_PATH);
  }
  throw new Error('no wallet credentials configured');
}
