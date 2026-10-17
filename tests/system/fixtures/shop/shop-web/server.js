const express = require("express");
const { createOrder, getOrder } = require("./src/api");
const app = express();
app.use(express.json());
app.post("/checkout", async (req, res) => res.json(await createOrder(req.body)));
app.get("/orders/:id", async (req, res) => res.json(await getOrder(req.params.id)));
app.listen(3000);
