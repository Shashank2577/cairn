"""One functional test per catalog rule, positive and negative (FR-007 to FR-010, FR-034; T013a-d, T014 to
T016, T018). A negative case is the prototype's lesson: an import, a dependency or a look-alike call
without the library's use site creates nothing."""
from __future__ import annotations

import pytest

from .util import build, write

DOCKER = {"Dockerfile": 'FROM base\nEXPOSE 8080\nCMD ["run"]\n'}


def model(tmp_path, files):
    root = write(tmp_path / "svc", {**DOCKER, **files})
    return build(root, "svc")


def routes(m) -> set[str]:
    return {e.name for e in m.elements.values() if e.kind == "route"}


def calls(m) -> set[tuple]:
    return {(e.meta["method"], e.meta["path"], e.meta.get("base")) for e in m.elements.values()
            if e.kind == "http-call"}


def links(m) -> set[tuple]:
    names = {e.id: e.name for e in m.elements.values()}
    return {(names[r.to_id], r.what) for r in m.relationships.values() if r.level == "container"}


PY = {"pyproject.toml": '[project]\nname = "svc"\ndependencies = ["fastapi", "flask", "django", "requests"]\n'}
JS = {"package.json": '{"name": "svc", "dependencies": {"express": "4", "fastify": "4", "next": "14"}}'}
GO = {"go.mod": "module example.com/svc\n\ngo 1.22\n\nrequire github.com/gin-gonic/gin v1.9.0\n"}
POM = {"pom.xml": "<project><groupId>g</groupId><artifactId>svc</artifactId><dependencies><dependency>"
                  "<groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId>"
                  "</dependency></dependencies></project>"}

ROUTES = [
    ("fastapi", {**PY, "app/main.py": (
        "from fastapi import FastAPI, APIRouter\nfrom .items import router\napp = FastAPI()\n"
        "@app.get('/health')\ndef h():\n    pass\napp.include_router(router, prefix='/api')\n"),
        "app/__init__.py": "", "app/items.py": (
        "from fastapi import APIRouter\nrouter = APIRouter(prefix='/v1')\n"
        "@router.post('/items/{item_id}')\ndef create(item_id: int):\n    pass\n"
        "@router.api_route('/items', methods=['PUT', 'PATCH'])\ndef bulk():\n    pass\n")},
     {"GET /health", "POST /api/v1/items/{}", "PUT /api/v1/items", "PATCH /api/v1/items"}),
    ("flask", {**PY, "web.py": (
        "from flask import Flask, Blueprint\napp = Flask(__name__)\nbp = Blueprint('o', __name__, url_prefix='/orders')\n"
        "@app.route('/', methods=['POST', 'GET'])\ndef index():\n    pass\n"
        "@bp.route('/<int:oid>')\ndef one(oid):\n    pass\n@bp.delete('/<int:oid>')\ndef rm(oid):\n    pass\n"
        "app.register_blueprint(bp, url_prefix='/api')\n")},
     {"POST /", "GET /", "GET /api/orders/{}", "DELETE /api/orders/{}"}),
    ("django", {**PY, "site/urls.py": (
        "from django.urls import include, path\nurlpatterns = [path('api/', include('shop.urls')), "
        "path('admin/', admin_view)]\n"),
        "shop/urls.py": "from django.urls import path, re_path\nfrom . import views\n"
                        "urlpatterns = [path('orders/<int:pk>/', views.detail), re_path(r'^stats/(?P<y>\\d+)/$', views.s)]\n"},
     {"ANY /admin", "ANY /api/orders/{}", "ANY /api/stats/{}"}),
    ("express", {**JS, "server.js": (
        "const express = require('express');\nconst orders = require('./routes/orders');\nconst app = express();\n"
        "app.get('/health', (req, res) => res.send('ok'));\napp.use('/api', orders);\napp.get('port');\n"),
        "routes/orders.js": "const { Router } = require('express');\nconst router = Router();\n"
                            "router.post('/orders/:id', h);\nrouter.all('/ping', h);\nmodule.exports = router;\n"},
     {"GET /health", "POST /api/orders/{}", "ANY /api/ping"}),
    ("fastify", {**JS, "index.mjs": (
        "import Fastify from 'fastify'\nconst fastify = Fastify({ logger: true })\n"
        "fastify.get('/items/:id', async () => ({}))\nfastify.route({ method: 'POST', url: '/items', handler: h })\n")},
     {"GET /items/{}", "POST /items"}),
    ("nextjs", {**JS, "app/api/orders/[id]/route.ts": "export async function GET(req: Request) {}\n"
                                                      "export const DELETE = async () => {}\n",
                "app/(shop)/cart/route.ts": "export async function POST() {}\n",
                "pages/api/hello.ts": "export default function handler(req, res) {}\n"},
     {"GET /api/orders/{}", "DELETE /api/orders/{}", "POST /cart", "ANY /api/hello"}),
    ("spring", {**POM, "src/main/java/a/OrdersController.java": (
        "package a;\nimport org.springframework.web.bind.annotation.*;\n@RestController\n@RequestMapping(\"/api/orders\")\n"
        "public class OrdersController {\n  @GetMapping(\"/{id}\")\n  public String one() { return \"\"; }\n"
        "  @RequestMapping(value = \"/bulk\", method = RequestMethod.PUT)\n  public void bulk() {}\n"
        "  @PostMapping\n  public void create() {}\n}\n"),
        "src/main/java/a/Client.java": (
        "package a;\nimport org.springframework.cloud.openfeign.FeignClient;\n"
        "import org.springframework.web.bind.annotation.GetMapping;\n@FeignClient(name = \"stock\")\n"
        "interface Client {\n  @GetMapping(\"/api/stock\")\n  String stock();\n}\n")},
     {"GET /api/orders/{}", "PUT /api/orders/bulk", "POST /api/orders"}),
    ("net-http", {**GO, "main.go": (
        "package main\nimport \"net/http\"\nfunc main() {\n  mux := http.NewServeMux()\n"
        "  mux.HandleFunc(\"DELETE /orders/{id}\", h)\n  http.HandleFunc(\"/legacy\", h)\n}\n")},
     {"DELETE /orders/{}", "ANY /legacy"}),
    ("gin", {**GO, "main.go": (
        "package main\nimport \"github.com/gin-gonic/gin\"\nfunc main() {\n  r := gin.Default()\n"
        "  api := r.Group(\"/api\")\n  v1 := api.Group(\"/v1\")\n  v1.GET(\"/items/:id\", h)\n  r.POST(\"/login\", h)\n}\n")},
     {"GET /api/v1/items/{}", "POST /login"}),
]


@pytest.mark.parametrize("name,files,want", ROUTES, ids=[r[0] for r in ROUTES])
def test_inbound_routes_per_framework(tmp_path, name, files, want):
    """FR-007, FR-034: each first-build framework's routes, with mount, group and class prefixes."""
    assert routes(model(tmp_path, files)) == want


ROUTE_NEGATIVES = [
    ("decorator-of-another-library", {**PY, "a.py": "from cachetools import cache\n@cache.get('/x')\ndef f():\n    pass\n"}),
    ("express-without-express", {"server.js": "const app = makeThing();\napp.get('/x', h);\n"}),
    ("flask-imported-not-used", {**PY, "a.py": "import flask\n\ndef f():\n    return 1\n"}),
    ("spring-annotation-without-dependency", {"src/main/java/a/A.java": (
        "package a;\nclass A {\n  @GetMapping(\"/x\")\n  void f() {}\n}\n")}),
    ("go-handle-on-other-type", {**GO, "main.go": "package main\nfunc main() {\n  router.HandleFunc(\"/x\", h)\n}\n"}),
]


@pytest.mark.parametrize("name,files", ROUTE_NEGATIVES, ids=[r[0] for r in ROUTE_NEGATIVES])
def test_route_look_alikes_create_nothing(tmp_path, name, files):
    """FR-007, FR-010, FR-034."""
    assert routes(model(tmp_path, files)) == set()


CALLS = [
    ("fetch", {"api.js": "const BASE = process.env.API_URL || 'http://localhost';\n"
                         "async function f(id) { return fetch(`${BASE}/api/orders/${id}`, { method: 'DELETE' }); }\n"
                         "async function g() { return fetch('/local/path'); }\n"},
     {("DELETE", "/api/orders/{}", "API_URL"), ("GET", "/local/path", None)}),
    ("axios", {"api.js": "import axios from 'axios';\nconst api = axios.create({ baseURL: process.env.ORDERS_URL });\n"
                         "api.post('/orders', {});\naxios.get(process.env.STOCK_URL + '/stock/' + sku);\n"
                         "axios({ method: 'put', url: '/orders/1' });\n"},
     {("POST", "/orders", "ORDERS_URL"), ("GET", "/stock/{}", "STOCK_URL"), ("PUT", "/orders/1", None)}),
    ("requests", {"c.py": "import os, requests\nBASE = os.environ['API']\n"
                          "def f(i):\n    return requests.post(f'{BASE}/api/orders/{i}', json={})\n"
                          "requests.request('PATCH', BASE + '/api/x')\n"},
     {("POST", "/api/orders/{}", "API"), ("PATCH", "/api/x", "API")}),
    ("httpx", {"c.py": "import os\nimport httpx\nclient = httpx.Client(base_url=os.getenv('PAY_URL'))\n"
                       "client.get('/payments/1')\n"},
     {("GET", "/payments/1", "PAY_URL")}),
    ("resttemplate", {"src/main/java/a/C.java": (
        "package a;\nimport org.springframework.web.client.RestTemplate;\nimport org.springframework.beans.factory.annotation.Value;\n"
        "class C {\n  private RestTemplate rest;\n  @Value(\"${pay.url}\") private String base;\n"
        "  void f() {\n    rest.exchange(base + \"/api/pay\", HttpMethod.PUT, null, String.class);\n"
        "    rest.getForObject(\"http://stock:8080/api/stock\", String.class);\n  }\n}\n")},
     {("PUT", "/api/pay", "pay.url"), ("GET", "/api/stock", None)}),
    ("webclient", {"src/main/java/a/C.java": (
        "package a;\nimport org.springframework.web.reactive.function.client.WebClient;\n"
        "class C {\n  private WebClient web;\n  void f() { web.post().uri(\"/api/orders\").retrieve(); }\n}\n")},
     {("POST", "/api/orders", None)}),
    ("feign", {"src/main/java/a/S.java": (
        "package a;\nimport org.springframework.cloud.openfeign.FeignClient;\n"
        "@FeignClient(name = \"stock\", url = \"${stock.url}\")\ninterface S {\n"
        "  @PostMapping(\"/api/reserve\")\n  String reserve();\n}\n")},
     {("POST", "/api/reserve", "stock.url")}),
    ("go-http", {"go.mod": "module x\n", "main.go": (
        "package main\nimport (\n  \"net/http\"\n  \"os\"\n)\nfunc main() {\n  base := os.Getenv(\"INV_URL\")\n"
        "  http.Get(base + \"/api/stock\")\n  req, _ := http.NewRequest(\"DELETE\", base+\"/api/hold/\"+id, nil)\n"
        "  _ = req\n}\n")},
     {("GET", "/api/stock", "INV_URL"), ("DELETE", "/api/hold/{}", "INV_URL")}),
    ("dotnet-httpclient", {"Api.cs": (
        "using System.Net.Http;\nclass A { HttpClient http; async void F() { await http.PostAsync(\"/api/votes\", c); } }\n")},
     {("POST", "/api/votes", None)}),
    ("shell", {"seed.sh": "#!/bin/sh\ncurl -sS -X PUT http://api:8000/api/reset\nwget --post-data x http://api/api/load\n"},
     {("PUT", "/api/reset", None), ("POST", "/api/load", None)}),
]


@pytest.mark.parametrize("name,files,want", CALLS, ids=[c[0] for c in CALLS])
def test_outbound_http_calls_per_client(tmp_path, name, files, want):
    """FR-008: method, literal or template path, and the configuration NAME of the base address."""
    assert calls(model(tmp_path, files)) == want


CALL_NEGATIVES = [
    ("local-fetch-function", {"a.js": "function fetch(x) { return x; }\nfetch('/api/x');\n"}),
    ("requests-imported-only", {"a.py": "import requests\n\nVALUE = 1\n"}),
    ("get-on-a-dict", {"a.py": "import requests\nd = {}\nd.get('/api/x')\n"}),
]


@pytest.mark.parametrize("name,files", CALL_NEGATIVES, ids=[c[0] for c in CALL_NEGATIVES])
def test_outbound_look_alikes_create_nothing(tmp_path, name, files):
    """FR-008, FR-010."""
    assert calls(model(tmp_path, files)) == set()


MESSAGING = [
    ("redis-pubsub", {"a.py": "import redis\nr = redis.Redis.from_url('redis://cache:6379')\n"
                              "r.publish('order.created', b'x')\np = r.pubsub()\np.subscribe('order.paid')\n"},
     {("Redis", "Publishes order.created"), ("Redis", "Subscribes to order.paid")}),
    ("redis-list-queue", {"a.py": "from redis import Redis\ndef q():\n    return g.redis\n"
                                  "def f():\n    redis = q()\n    redis.rpush('votes', 1)\n    redis.blpop(['jobs'])\n"},
     {("Redis", "Queues votes"), ("Redis", "Consumes jobs")}),
    ("redis-wrapper-two-hops", {"events.py": "import redis\nr = redis.Redis()\n"
                                             "def publish(topic, body):\n    r.publish(topic, body)\n",
                                "service.py": "from .events import publish\n"
                                              "def emit(name, body):\n    publish(name, body)\n"
                                              "def done():\n    emit('order.done', b'')\n"},
     {("Redis", "Publishes order.done")}),
    ("node-redis", {"a.js": "const { createClient } = require('redis');\nconst c = createClient({ url: process.env.REDIS_URL });\n"
                            "c.publish('chat', 'hi');\nc.rPush('jobs', 'x');\n"},
     {("Redis", "Publishes chat"), ("Redis", "Queues jobs")}),
    ("go-redis", {"go.mod": "module x\n", "main.go": (
        "package main\nimport \"github.com/redis/go-redis/v9\"\nfunc main() {\n"
        "  rdb := redis.NewClient(&redis.Options{Addr: \"cache:6379\"})\n  rdb.Publish(ctx, \"alerts\", \"x\")\n}\n")},
     {("Redis", "Publishes alerts")}),
    ("stackexchange-redis", {"W.cs": "using StackExchange.Redis;\nclass W { void F() {\n"
                                     "  var m = ConnectionMultiplexer.Connect(\"redis\");\n"
                                     "  var db = m.GetDatabase();\n  db.ListRightPush(\"votes\", \"x\");\n} }\n"},
     {("Redis", "Queues votes")}),
    ("kafka-python", {"a.py": "from kafka import KafkaProducer, KafkaConsumer\np = KafkaProducer(bootstrap_servers='k:9092')\n"
                              "p.send('orders', b'x')\nc = KafkaConsumer('payments', bootstrap_servers='k:9092')\n"},
     {("Kafka", "Publishes orders"), ("Kafka", "Subscribes to payments")}),
    ("confluent-kafka", {"a.py": "from confluent_kafka import Producer, Consumer\np = Producer({})\np.produce('o', b'x')\n"
                                 "c = Consumer({})\nc.subscribe(['a', 'b'])\n"},
     {("Kafka", "Publishes o"), ("Kafka", "Subscribes to a, b")}),
    ("kafkajs", {"a.js": "const { Kafka } = require('kafkajs');\nconst kafka = new Kafka({ brokers: ['k:9092'] });\n"
                         "const producer = kafka.producer();\nawait producer.send({ topic: 'orders', messages: [] });\n"
                         "const consumer = kafka.consumer({ groupId: 'g' });\nawait consumer.subscribe({ topics: ['paid'] });\n"},
     {("Kafka", "Publishes orders"), ("Kafka", "Subscribes to paid")}),
    ("spring-kafka", {"src/main/java/a/K.java": (
        "package a;\nimport org.springframework.kafka.core.KafkaTemplate;\nimport org.springframework.kafka.annotation.KafkaListener;\n"
        "class K {\n  private KafkaTemplate<String, String> kafka;\n  void f() { kafka.send(\"orders\", \"x\"); }\n"
        "  @KafkaListener(topics = {\"paid\", \"refunded\"})\n  void on(String m) {}\n}\n")},
     {("Kafka", "Publishes orders"), ("Kafka", "Subscribes to paid, refunded")}),
    ("kafka-go-and-sarama", {"go.mod": "module x\n", "main.go": (
        "package main\nimport (\n  \"github.com/segmentio/kafka-go\"\n  \"github.com/IBM/sarama\"\n)\n"
        "func main() {\n  w := &kafka.Writer{Topic: \"orders\"}\n  r := kafka.NewReader(kafka.ReaderConfig{Topic: \"paid\"})\n"
        "  m := &sarama.ProducerMessage{Topic: \"audit\"}\n  _ = kafka.Message{Value: nil}\n}\n")},
     {("Kafka", "Publishes audit, orders"), ("Kafka", "Subscribes to paid")}),
    ("pika", {"a.py": "import pika\nconn = pika.BlockingConnection(pika.ConnectionParameters('mq'))\nch = conn.channel()\n"
                      "ch.basic_publish(exchange='', routing_key='tasks', body=b'x')\n"
                      "ch.basic_consume(queue='results', on_message_callback=cb)\n"},
     {("RabbitMQ", "Publishes tasks"), ("RabbitMQ", "Subscribes to results")}),
    ("amqplib", {"a.js": "const amqp = require('amqplib');\nconst conn = await amqp.connect(process.env.AMQP_URL);\n"
                         "const ch = await conn.createChannel();\nch.sendToQueue('tasks', Buffer.from('x'));\n"
                         "ch.consume('results', m => {});\n"},
     {("RabbitMQ", "Publishes tasks"), ("RabbitMQ", "Subscribes to results")}),
    ("spring-amqp", {"src/main/java/a/R.java": (
        "package a;\nimport org.springframework.amqp.rabbit.core.RabbitTemplate;\n"
        "import org.springframework.amqp.rabbit.annotation.RabbitListener;\n"
        "class R {\n  private RabbitTemplate rabbit;\n  void f() { rabbit.convertAndSend(\"tasks\", \"x\"); }\n"
        "  @RabbitListener(queues = \"results\")\n  void on(String m) {}\n}\n")},
     {("RabbitMQ", "Publishes tasks"), ("RabbitMQ", "Subscribes to results")}),
    ("go-amqp", {"go.mod": "module x\n", "main.go": (
        "package main\nimport amqp \"github.com/rabbitmq/amqp091-go\"\nfunc main() {\n"
        "  conn, _ := amqp.Dial(\"amqp://mq\")\n  ch, _ := conn.Channel()\n"
        "  ch.PublishWithContext(ctx, \"\", \"tasks\", false, false, msg)\n  ch.Consume(\"results\", \"\", true, false, false, false, nil)\n}\n")},
     {("RabbitMQ", "Publishes tasks"), ("RabbitMQ", "Subscribes to results")}),
    ("sqs-boto3", {"a.py": "import os, boto3\nsqs = boto3.client('sqs')\n"
                           "sqs.send_message(QueueUrl=os.environ['ORDERS_QUEUE_URL'], MessageBody='x')\n"},
     {("Amazon SQS", None)}),
    ("sqs-js", {"a.js": "import { SQSClient, SendMessageCommand } from '@aws-sdk/client-sqs';\n"
                        "await client.send(new SendMessageCommand({ QueueUrl: 'https://sqs.eu/1/orders', MessageBody: 'x' }));\n"},
     {("Amazon SQS", "Publishes orders")}),
    ("nats", {"a.py": "import nats\nnc = await nats.connect('nats://n:4222')\nawait nc.publish('greet', b'hi')\n"},
     {("NATS", "Publishes greet")}),
]


@pytest.mark.parametrize("name,files,want", MESSAGING, ids=[c[0] for c in MESSAGING])
def test_messaging_per_client(tmp_path, name, files, want):
    """FR-009: publish/consume per client, list queues, wrappers resolved through two hops (T015)."""
    assert links(model(tmp_path, files)) == want


MSG_NEGATIVES = [
    ("redis-imported-only", {"a.py": "import redis\n\ndef f():\n    return 1\n"}),
    ("publish-on-unrelated-object", {"a.py": "def f(bus):\n    bus.publish('x', 1)\n"}),
    ("kafka-dependency-only", {"pyproject.toml": "[project]\nname='x'\ndependencies=['kafka-python']\n",
                               "a.py": "x = 1\n"}),
    ("kafka-template-import-only", {"src/main/java/a/A.java": (
        "package a;\nimport org.springframework.kafka.core.KafkaTemplate;\nclass A { void f() {} }\n")}),
]


@pytest.mark.parametrize("name,files", MSG_NEGATIVES, ids=[c[0] for c in MSG_NEGATIVES])
def test_messaging_look_alikes_create_nothing(tmp_path, name, files):
    """FR-010: an import or a declared dependency alone never creates an element or a relationship."""
    m = model(tmp_path, files)
    assert links(m) == set()
    assert not [e for e in m.elements.values() if e.type in ("channel", "data-store", "external-system")]


STORES = [
    ("psycopg", {"db.py": "import os, psycopg\ndef f():\n    with psycopg.connect(os.environ['DATABASE_URL']) as c:\n"
                          "        c.execute('SELECT id FROM orders JOIN lines ON 1=1')\n"},
     {("PostgreSQL", "Reads lines, orders")}),
    ("asyncpg", {"db.py": "import asyncpg\nasync def f():\n    p = await asyncpg.create_pool(dsn='postgres://db/x')\n"
                          "    await p.execute('INSERT INTO audit VALUES (1)')\n"},
     {("PostgreSQL", "Writes audit")}),
    ("sqlalchemy", {"db.py": "from sqlalchemy import create_engine\ne = create_engine('mysql+pymysql://u@db/x')\n"},
     {("MySQL", None)}),
    ("pymongo", {"db.py": "from pymongo import MongoClient\nc = MongoClient('mongodb://mongo:27017')\n"},
     {("MongoDB", None)}),
    ("django-databases", {"settings.py": "import os\nDATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql',"
                                         " 'HOST': os.environ.get('DB_HOST')}}\n"},
     {("PostgreSQL", None)}),
    ("pg", {"db.js": "const { Pool } = require('pg');\nconst pool = new Pool({ connectionString: process.env.PG_URL });\n"
                     "pool.query('UPDATE carts SET n = 1');\n"},
     {("PostgreSQL", "Writes carts")}),
    ("mysql2", {"db.js": "const mysql = require('mysql2/promise');\nconst c = await mysql.createPool({ host: 'db' });\n"},
     {("MySQL", None)}),
    ("mongoose", {"db.js": "const mongoose = require('mongoose');\nmongoose.connect(process.env.MONGO_URL);\n"},
     {("MongoDB", None)}),
    ("prisma", {"db.ts": "import { PrismaClient } from '@prisma/client';\nconst prisma = new PrismaClient();\n",
                "prisma/schema.prisma": 'datasource db {\n  provider = "postgresql"\n  url = env("DATABASE_URL")\n}\n'},
     {("PostgreSQL", None)}),
    ("jdbc-template", {"src/main/java/a/R.java": (
        "package a;\nimport org.springframework.jdbc.core.JdbcTemplate;\nclass R {\n  private JdbcTemplate jdbc;\n"
        "  void f() { jdbc.update(\"DELETE FROM sessions\"); }\n}\n"),
        "src/main/resources/application.properties": "spring.datasource.url=jdbc:postgresql://db:5432/app\n"},
     {("PostgreSQL", "Writes sessions")}),
    ("spring-data-jpa", {"src/main/java/a/Repo.java": (
        "package a;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
        "interface OrderRepo extends JpaRepository<Order, Long> {}\n"),
        "src/main/resources/application.yml": "spring:\n  datasource:\n    url: jdbc:mysql://db:3306/app\n"},
     {("MySQL", None)}),
    ("go-sql-driver", {"go.mod": "module x\n", "main.go": (
        "package main\nimport \"database/sql\"\nfunc main() {\n  db, _ := sql.Open(\"mysql\", dsn)\n"
        "  db.Query(\"SELECT * FROM users\")\n}\n")},
     {("MySQL", "Reads users")}),
    ("pgx", {"go.mod": "module x\n", "main.go": (
        "package main\nimport \"github.com/jackc/pgx/v5/pgxpool\"\nfunc main() {\n  p, _ := pgxpool.New(ctx, url)\n}\n")},
     {("PostgreSQL", None)}),
    ("npgsql", {"Db.cs": "using Npgsql;\nclass D { void F() { var c = new NpgsqlConnection(\"Host=db;Password=x\");\n"
                         "  cmd.CommandText = \"INSERT INTO votes (id) VALUES (1)\"; } }\n"},
     {("PostgreSQL", "Writes votes")}),
]


@pytest.mark.parametrize("name,files,want", STORES, ids=[c[0] for c in STORES])
def test_store_uses_per_driver(tmp_path, name, files, want):
    """FR-010, T016: driver connect and query sites; SQL verbs and tables when literal."""
    assert links(model(tmp_path, files)) == want


STORE_NEGATIVES = [
    ("psycopg-imported-only", {"db.py": "import psycopg\n\nVALUE = 1\n"}),
    ("dependency-only", {"pyproject.toml": "[project]\nname='x'\ndependencies=['psycopg', 'pymongo']\n"}),
    ("sql-without-driver", {"q.py": "QUERY = 'SELECT * FROM orders'\n"}),
    ("jpa-import-only", {"src/main/java/a/A.java": (
        "package a;\nimport org.springframework.data.jpa.repository.JpaRepository;\nclass A {}\n")}),
]


@pytest.mark.parametrize("name,files", STORE_NEGATIVES, ids=[c[0] for c in STORE_NEGATIVES])
def test_store_look_alikes_create_nothing(tmp_path, name, files):
    """FR-010."""
    m = model(tmp_path, files)
    assert links(m) == set()
    assert not [e for e in m.elements.values() if e.type == "data-store"]


SERVICES = [
    ("sendgrid-py", {"m.py": "from sendgrid import SendGridAPIClient\nSendGridAPIClient(k).send(msg)\n"}, "SendGrid"),
    ("sendgrid-js", {"m.js": "const sgMail = require('@sendgrid/mail');\nsgMail.send(msg);\n"}, "SendGrid"),
    ("stripe-py", {"p.py": "import stripe\nstripe.PaymentIntent.create(amount=1)\n"}, "Stripe"),
    ("stripe-js", {"p.js": "const stripe = require('stripe')(process.env.STRIPE_KEY);\n"}, "Stripe"),
    ("twilio", {"s.py": "from twilio.rest import Client\nClient(sid, tok).messages.create(to='x')\n"}, "Twilio"),
    ("s3", {"f.py": "import boto3\ns3 = boto3.client('s3')\n"}, "Amazon S3"),
    ("ses", {"f.py": "import boto3\nses = boto3.client('ses')\n"}, "Amazon SES"),
    ("openai", {"a.py": "from openai import OpenAI\nOpenAI()\n"}, "OpenAI API"),
    ("anthropic", {"a.py": "import anthropic\nanthropic.Anthropic()\n"}, "Anthropic API"),
    ("slack", {"a.py": "from slack_sdk import WebClient\nWebClient(token=t).chat_postMessage(channel='x')\n"}, "Slack"),
]


@pytest.mark.parametrize("name,files,want", SERVICES, ids=[c[0] for c in SERVICES])
def test_outside_services_per_sdk(tmp_path, name, files, want):
    """FR-010: an SDK use site creates the outside service with the catalog's verb."""
    m = model(tmp_path, files)
    assert want in {to for to, _ in links(m)}


SERVICE_NEGATIVES = [
    ("sendgrid-imported-only", {"m.py": "from sendgrid import SendGridAPIClient\n\nX = 1\n"}),
    ("boto3-other-service", {"f.py": "import boto3\ndb = boto3.client('dynamodb')\n"}),
    ("stripe-dependency-only", {"package.json": '{"name": "x", "dependencies": {"stripe": "1"}}'}),
]


@pytest.mark.parametrize("name,files", SERVICE_NEGATIVES, ids=[c[0] for c in SERVICE_NEGATIVES])
def test_outside_service_look_alikes_create_nothing(tmp_path, name, files):
    """FR-010."""
    m = model(tmp_path, files)
    assert not [e for e in m.elements.values() if e.type in ("external-system", "data-store")]
