import { Keypair } from '@solana/web3.js';
import bs58 from 'bs58';

/**
 * Generates a dedicated trading wallet. The point of using a fresh key is
 * that the bot's blast radius is capped at whatever you choose to send here —
 * a leaked or buggy bot cannot touch the wallet holding everything else.
 */
const kp = Keypair.generate();

console.log(`
New trading wallet generated.

  Public address (fund this one):
    ${kp.publicKey.toBase58()}

  Secret key (put this in .env as WALLET_PRIVATE_KEY):
    ${bs58.encode(kp.secretKey)}

Next:
  1. Paste the secret key into .env as WALLET_PRIVATE_KEY
  2. Send SOL to the public address from Trust Wallet — only what you
     accept losing entirely
  3. Run: npm run preflight

This secret key was printed once and not saved anywhere. Anyone who has it
owns everything in this wallet. Keep it out of screenshots, chat logs and
git — .env is already gitignored.
`);
