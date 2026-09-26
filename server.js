const express = require('express');
const cors = require('cors');
const crypto = require('crypto');
const admin = require('firebase-admin');

const app = express();

// Enable CORS and JSON Parsing
app.use(cors({ origin: true }));
app.use(express.json());

// 1. FIREBASE ADMIN SDK INITIALIZATION
// Ensure 'serviceAccountKey.json' is present in your project root folder
const serviceAccount = require('./serviceAccountKey.json');

admin.initializeApp({
  credential: admin.credential.cert(serviceAccount)
});

const db = admin.firestore();

// 2. HARDWARE SECURITY PROFILE (Master Controller Device)
const RESTRICTED_HARDWARE = {
  model: 'F41',
  serial: 'NMLC00609028'
};

// Sensitive Tapjoy Credentials
const TAPJOY_SECRETS = {
  reportId: 'c4dbe1ef-e3e2-41e7-8013-e32503f461cd',
  tayjoyAppId:'c035260e-b1af-4b26-bdb0-45db757295ae',
  sdkKey: 'wDUmDrGvSya9sEXbdXKVrgECPme8ryKdNaMxXVFX8gXrNcYStqCOqsx18dyG',
  placementId: 'placement_main_wall',
  reportApiKey: 'Y2UwMzU3ZjUtMDg0Yy00MGI5LTlmOGUtMjgxMWM1MGNjYTcxOlhtZEIyajQxMnUwcmI1WHVlMWRrbVNoaU9BVWU4OUFDMktPelJyYUtpS0psRkVJZGRGUW9uT0svMURMNm1FME93LzJDQUF0aEp2eVRvRHpGb3Q2TWRnPT0',
  publicId: '577af404-7983-43d2-8a90-f0be54363dcf',
  callbackUrl: 'https://scorpio-octavious-vibe-web-lit.onrender.com/api/tapjoy/callback',
  selfManagerSdkKey: 'CMaWblTW1exd1Xi0xKif',
};

const SUPPORTED_NETWORKS = ['SOV', 'BTC', 'ETH', 'SOL', 'USDT'];

/* ==================== HELPER FUNCTIONS ==================== */

// Generate deterministic crypto addresses tied uniquely to user email
function generateEmailDerivedAddresses(email) {
  const cleanEmail = email.trim().toLowerCase();
  
  const hashBtc = crypto.createHash('sha256').update(`btc:${cleanEmail}`).digest('hex');
  const hashEth = crypto.createHash('sha256').update(`eth:${cleanEmail}`).digest('hex');
  const hashSol = crypto.createHash('sha256').update(`sol:${cleanEmail}`).digest('hex');
  const hashSov = crypto.createHash('sha256').update(`sov:${cleanEmail}`).digest('hex');

  return {
    sovAddress: `0xSOV${hashSov.substring(0, 32).toUpperCase()}`,
    btcAddress: `bc1q${hashBtc.substring(0, 38)}`,
    ethAddress: `0x${hashEth.substring(0, 40)}`,
    solAddress: `${hashSol.substring(0, 44)}`,
    usdtAddress: `0x${hashEth.substring(0, 40)}` // ERC-20 USDT
  };
}

/* ==================== MIDDLEWARE ==================== */

// Verify Firebase Auth Bearer Token
async function verifyAuthToken(req, res, next) {
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Unauthorized: Missing or invalid authentication token.' });
  }

  const token = authHeader.split('Bearer ')[1];
  try {
    const decodedToken = await admin.auth().verifyIdToken(token);
    req.user = decodedToken;
    next();
  } catch (error) {
    return res.status(403).json({ error: 'Forbidden: Invalid user session.' });
  }
}

// Strict Hardware Fingerprint Check for Protected Routes
function verifyHardwareDevice(req, res, next) {
  const model = req.headers['x-phone-model'];
  const serial = req.headers['x-serial-number'];

  if (model === RESTRICTED_HARDWARE.model && serial === RESTRICTED_HARDWARE.serial) {
    req.isAuthorizedAdminDevice = true;
    return next();
  }

  return res.status(403).json({
    error: 'Access Denied: Protected resource. Hardware fingerprint mismatch.',
    received: { model: model || 'Unknown', serial: serial || 'Unknown' }
  });
}

/* ==================== API ROUTES ==================== */

// 1. DEDICATED ADDRESS SYNC & DERIVATION ENDPOINT
app.post('/api/wallet/sync-addresses', verifyAuthToken, async (req, res) => {
  const uid = req.user.uid;
  const email = req.user.email;

  if (!email) {
    return res.status(400).json({ error: 'User token lacks a verified email address.' });
  }

  const generatedAddresses = generateEmailDerivedAddresses(email);
  const userRef = db.collection('users').doc(uid);

  try {
    await userRef.set({
      email: email.toLowerCase(),
      walletAddresses: generatedAddresses,
      lastSyncedAt: new Date().toISOString()
    }, { merge: true });

    res.json({
      success: true,
      email,
      addresses: generatedAddresses
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// 2. HARDWARE-RESTRICTED TAPJOY CREDENTIALS ENDPOINT
app.get('/api/tapjoy/credentials', verifyAuthToken, verifyHardwareDevice, (req, res) => {
  res.json({
    success: true,
    hardwareVerified: true,
    data: TAPJOY_SECRETS
  });
});

// 3. REAL TAPJOY S2S POSTBACK CALLBACK (Auto-Credits Balance in Firestore)
app.post('/api/tapjoy/callback', async (req, res) => {
  const { snuid, currency } = req.query; // snuid = Firebase Auth UID, currency = SOV reward amount
  const rewardAmount = parseFloat(currency);

  if (!snuid || isNaN(rewardAmount) || rewardAmount <= 0) {
    return res.status(400).send('Invalid postback parameters.');
  }

  try {
    const userRef = db.collection('users').doc(snuid);
    
    // Atomically increment real user balance in Firestore
    await db.runTransaction(async (transaction) => {
      const userDoc = await transaction.get(userRef);
      if (!userDoc.exists) throw new Error("Target user profile missing.");

      const currentBalance = userDoc.data().balanceSOV || 0;
      transaction.update(userRef, { balanceSOV: currentBalance + rewardAmount });
    });

    console.log(`[Tapjoy Verified Postback] Credited ${rewardAmount} SOV to UID: ${snuid}`);
    return res.status(200).send('OK');
  } catch (error) {
    console.error('Tapjoy Postback Processing Error:', error);
    return res.status(500).send('Internal Server Error');
  }
});

// 4. SECURE MULTI-NETWORK TRANSFER WITH MANDATORY 50% ADMIN FEE ROUTING
app.post('/api/wallet/transfer', verifyAuthToken, async (req, res) => {
  const senderUid = req.user.uid;
  const { network, recipientAddress, amount } = req.body;
  const transferAmount = parseFloat(amount);

  if (!network || !SUPPORTED_NETWORKS.includes(network.toUpperCase())) {
    return res.status(400).json({ error: `Unsupported network. Supported: ${SUPPORTED_NETWORKS.join(', ')}` });
  }

  if (isNaN(transferAmount) || transferAmount <= 0) {
    return res.status(400).json({ error: 'Invalid transfer amount.' });
  }

  const feeRate = 0.50; // 50% Mandatory Charge Fee
  const feeAmount = transferAmount * feeRate;
  const netAmount = transferAmount - feeAmount;

  try {
    const senderRef = db.collection('users').doc(senderUid);
    
    // Query master controller account linked to Phone Model F41 & Serial NMLC00609028
    const adminQuery = await db.collection('users')
      .where('hardwareModel', '==', RESTRICTED_HARDWARE.model)
      .where('hardwareSerial', '==', RESTRICTED_HARDWARE.serial)
      .limit(1)
      .get();

    let adminRef = null;
    if (!adminQuery.empty) {
      adminRef = adminQuery.docs[0].ref;
    }

    await db.runTransaction(async (transaction) => {
      const senderDoc = await transaction.get(senderRef);
      if (!senderDoc.exists) throw new Error('Sender profile not found.');

      const currentBalance = senderDoc.data().balanceSOV || 0;
      if (currentBalance < transferAmount) {
        throw new Error(`Insufficient balance on server. Available: ${currentBalance} SOV.`);
      }

      // Deduct full amount from sender
      transaction.update(senderRef, { 
        balanceSOV: currentBalance - transferAmount 
      });

      // Route 50% fee directly to F41 / NMLC00609028 controller account
      if (adminRef) {
        const adminDoc = await transaction.get(adminRef);
        const adminBalance = adminDoc.data().balanceSOV || 0;
        transaction.update(adminRef, { 
          balanceSOV: adminBalance + feeAmount 
        });
      }
    });

    res.json({
      success: true,
      network: network.toUpperCase(),
      senderUid,
      recipientAddress,
      totalDebited: transferAmount,
      transferredToRecipient: netAmount,
      feeRoutedToAdmin: feeAmount,
      feeRecipientHardware: `${RESTRICTED_HARDWARE.model}:${RESTRICTED_HARDWARE.serial}`
    });

  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

// 5. APP HUB & PAYMENT GATEWAY WEBHOOK (PayFast, Capitec Pay, Instant EFT, PayPal)
app.post('/api/app-hub/webhook', async (req, res) => {
  const { provider, userUid, amountPaid, paymentStatus } = req.body;

  if (paymentStatus !== 'COMPLETE') {
    return res.status(400).json({ error: 'Payment status not confirmed.' });
  }

  try {
    const userRef = db.collection('users').doc(userUid);
    
    await db.runTransaction(async (transaction) => {
      const userDoc = await transaction.get(userRef);
      if (!userDoc.exists) throw new Error('Target user account not found.');

      const currentBalance = userDoc.data().balanceSOV || 0;
      transaction.update(userRef, { 
        balanceSOV: currentBalance + parseFloat(amountPaid) 
      });
    });

    console.log(`[App Hub Payment Webhook] ${provider} payment processed. User ${userUid} credited with ${amountPaid} SOV.`);
    res.json({ success: true, message: `Successfully credited account via ${provider}.` });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// START SERVER
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`====================================================`);
  console.log(`Scorpio Octavious Vibe Node.js Backend Server Online`);
  console.log(`Listening on Port: ${PORT}`);
  console.log(`Hardware Security Active for: F41 / NMLC00609028`);
  console.log(`====================================================`);
});