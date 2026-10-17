const BASE = process.env.API_BASE_URL || "http://localhost:8000";
async function createOrder(order) {
  const r = await fetch(`${BASE}/api/orders`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(order) });
  return r.json();
}
async function getOrder(id) {
  const r = await fetch(`${BASE}/api/orders/${id}`);
  return r.json();
}
module.exports = { createOrder, getOrder };
