const express = require('express');
const { Router } = require('express');

const app = express();
const billing = Router();

billing.post('/invoices', async (req, res) => {
  await fetch(`${process.env.BILLING_URL}/invoices`, { method: 'POST', body: JSON.stringify(req.body) });
  res.sendStatus(202);
});

app.get('/health', (req, res) => res.send('ok'));
app.use('/checkout', billing);

app.listen(80);
