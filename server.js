const express = require('express');
const session = require('express-session');
const Database = require('better-sqlite3');
const bcrypt = require('bcrypt');
const path = require('path');

const app = express();
const db = new Database('database.db');

// Initialize Database Tables
db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    pin_hash TEXT NOT NULL,
    profile_pic TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    sov_address TEXT UNIQUE,
    sov_balance REAL DEFAULT 0.0,
    paypal_balance REAL DEFAULT 0.0
  );

  CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER,
    receiver_address TEXT,
    amount REAL,
    fee REAL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
  );
`);

// Target Admin Device Rules
const ADMIN_MODEL = 'F41';
const ADMIN_SERIAL = 'NMLC00609028';

// Secure Device Credentials Storage (Backend Only)
const TAPJOY_CONFIG = {
  appId: '3bdeab76-d8dd-417d-bd54-371fdcc543ae',
  publicId: '577af404-7983-43d2-8a90-f0be54363dcf',
  sdkKey: 'SECRET_TAPJOY_SDK_KEY',
  placementId: 'SECRET_PLACEMENT_ID',
  reportApiKey: 'SECRET_REPORT_API_KEY',
  selfManagerSdkKey: 'SECRET_SELF_MANAGER_SDK_KEY',
  callbackUrl: 'https://scorpio-octavious-vibe-api.onrender.com/api/tapjoy/callback'
};

app.use(express.json({ limit: '10mb' }));
app.use(express.static(path.join(__dirname, 'public')));
app.use(session({
  secret: 'scorpio_octavious_vibe_secret_key_99',
  resave: false,
  saveUninitialized: false,
  cookie: { secure: false, maxAge: 24 * 60 * 60 * 1000 }
}));

// Middleware: Authenticate User Session
function requireAuth(req, res, next) {
  if (!req.session.userId) {
    return res.status(401).json({ error: 'Unauthorized. Please verify PIN first.' });
  }
  next();
}

// -------------------------------------------------------------
// PAGE 1 API: PIN Management & Authentication
// -------------------------------------------------------------

// Register PIN
app.post('/api/pin/register', async (req, res) => {
  const { pin } = req.body;
  if (!pin || pin.length < 4) return res.status(400).json({ error: 'PIN must be at least 4 digits.' });

  try {
    const pinHash = await bcrypt.hash(pin, 10);
    const sovAddress = 'SOV_' + Math.random().toString(36).substring(2, 10).toUpperCase() + Date.now().toString(36).toUpperCase();
    const tempUsername = 'Sov_' + Math.floor(1000 + Math.random() * 9000);

    const stmt = db.prepare('INSERT INTO users (username, pin_hash, sov_address) VALUES (?, ?, ?)');
    const info = stmt.run(tempUsername, pinHash, sovAddress);

    req.session.userId = info.lastInsertRowid;
    res.json({ success: true, message: 'PIN set successfully.', userId: info.lastInsertRowid });
  } catch (err) {
    res.status(500).json({ error: 'Failed to create user.' });
  }
});

// Login / Verify PIN
app.post('/api/pin/verify', async (req, res) => {
  const { pin } = req.body;
  if (!pin) return res.status(400).json({ error: 'PIN required.' });

  // If already logged in
  if (req.session.userId) {
    const user = db.prepare('SELECT id, username, sov_address, sov_balance, paypal_balance, profile_pic, bio FROM users WHERE id = ?').get(req.session.userId);
    if (user) {
      const match = await bcrypt.compare(pin, db.prepare('SELECT pin_hash FROM users WHERE id = ?').get(user.id).pin_hash);
      if (match) return res.json({ success: true, user });
    }
  }

  // Find user by testing PIN hashes
  const users = db.prepare('SELECT * FROM users').all();
  let foundUser = null;

  for (const u of users) {
    const isMatch = await bcrypt.compare(pin, u.pin_hash);
    if (isMatch) {
      foundUser = u;
      break;
    }
  }

  if (foundUser) {
    req.session.userId = foundUser.id;
    delete foundUser.pin_hash;
    res.json({ success: true, user: foundUser });
  } else {
    res.status(401).json({ error: 'Invalid PIN.' });
  }
});

// Check Existing Session
app.get('/api/auth/session', (req, res) => {
  if (req.session.userId) {
    const user = db.prepare('SELECT id, username, sov_address, sov_balance, paypal_balance, profile_pic, bio FROM users WHERE id = ?').get(req.session.userId);
    if (user) return res.json({ authenticated: true, user });
  }
  res.json({ authenticated: false });
});

// Update PIN
app.post('/api/pin/update', requireAuth, async (req, res) => {
  const { oldPin, newPin } = req.body;
  const user = db.prepare('SELECT pin_hash FROM users WHERE id = ?').get(req.session.userId);

  const match = await bcrypt.compare(oldPin, user.pin_hash);
  if (!match) return res.status(400).json({ error: 'Incorrect existing PIN.' });

  const newHash = await bcrypt.hash(newPin, 10);
  db.prepare('UPDATE users SET pin_hash = ? WHERE id = ?').run(newHash, req.session.userId);

  res.json({ success: true, message: 'PIN updated successfully.' });
});

// -------------------------------------------------------------
// PAGE 2 API: Tapjoy Config (Protected by Phone Hardware Identifiers)
// -------------------------------------------------------------
app.post('/api/admin/tapjoy-config', requireAuth, (req, res) => {
  const { phoneModel, serialNumber } = req.body;

  // Strict Device Security Verification
  if (phoneModel !== ADMIN_MODEL || serialNumber !== ADMIN_SERIAL) {
    return res.status(403).json({ 
      error: 'Access Denied: Hardware restriction. Device model and serial number mismatch.' 
    });
  }

  // Return sensitive details ONLY to designated device
  res.json({ success: true, config: TAPJOY_CONFIG });
});

// Tapjoy Backend Postback Callback (Verified Server-Side)
app.get('/api/tapjoy/callback', (req, res) => {
  const { snuid, currency } = req.query; // snuid = userId, currency = reward amount
  const userId = parseInt(snuid);
  const rewardAmount = parseFloat(currency);

  if (userId && rewardAmount > 0) {
    db.prepare('UPDATE users SET sov_balance = sov_balance + ? WHERE id = ?').run(rewardAmount, userId);
    return res.status(200).send('OK');
  }
  res.status(400).send('Invalid verification');
});

// -------------------------------------------------------------
// PAGE 3 API: Profile, Wallet & Payments
// -------------------------------------------------------------

// Profile Update
app.post('/api/profile/update', requireAuth, (req, res) => {
  const { username_sov, bio, profile_pic } = req.body;
  const userId = req.session.userId;

  let formattedUsername = username_sov;
  if (formattedUsername && !formattedUsername.startsWith('Sov_')) {
    formattedUsername = 'Sov_' + formattedUsername;
  }

  try {
    if (formattedUsername) {
      db.prepare('UPDATE users SET username = ? WHERE id = ?').run(formattedUsername, userId);
    }
    if (bio !== undefined) {
      db.prepare('UPDATE users SET bio = ? WHERE id = ?').run(bio, userId);
    }
    if (profile_pic !== undefined) {
      db.prepare('UPDATE users SET profile_pic = ? WHERE id = ?').run(profile_pic, userId);
    }

    const updatedUser = db.prepare('SELECT id, username, sov_address, sov_balance, paypal_balance, profile_pic, bio FROM users WHERE id = ?').get(userId);
    res.json({ success: true, user: updatedUser });
  } catch (err) {
    res.status(400).json({ error: 'Username already taken or invalid update.' });
  }
});

// SOV Crypto Send with 50% Platform Fee
app.post('/api/wallet/send-sov', requireAuth, (req, res) => {
  const { recipientAddress, amount } = req.body;
  const senderId = req.session.userId;
  const sendAmount = parseFloat(amount);

  if (isNaN(sendAmount) || sendAmount <= 0) {
    return res.status(400).json({ error: 'Invalid amount.' });
  }

  const sender = db.prepare('SELECT sov_balance FROM users WHERE id = ?').get(senderId);
  if (sender.sov_balance < sendAmount) {
    return res.status(400).json({ error: 'Insufficient SOV balance.' });
  }

  const recipient = db.prepare('SELECT id FROM users WHERE sov_address = ?').get(recipientAddress);
  if (!recipient) {
    return res.status(404).json({ error: 'Recipient SOV address not found.' });
  }

  // Calculation: 50% Fee Calculation
  const fee = sendAmount * 0.50;
  const netAmount = sendAmount - fee;

  // Execute Database Transaction
  const executeTransaction = db.transaction(() => {
    // Deduct total amount from sender
    db.prepare('UPDATE users SET sov_balance = sov_balance - ? WHERE id = ?').run(sendAmount, senderId);
    
    // Add net amount to recipient
    db.prepare('UPDATE users SET sov_balance = sov_balance + ? WHERE id = ?').run(netAmount, recipient.id);

    // Record Transaction Log
    db.prepare('INSERT INTO transactions (sender_id, receiver_address, amount, fee) VALUES (?, ?, ?, ?)').run(senderId, recipientAddress, sendAmount, fee);
  });

  executeTransaction();

  const newBalance = db.prepare('SELECT sov_balance FROM users WHERE id = ?').get(senderId).sov_balance;
  res.json({ 
    success: true, 
    message: `Transferred ${netAmount} SOV. ${fee} SOV fee applied (50%).`,
    balance: newBalance 
  });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));