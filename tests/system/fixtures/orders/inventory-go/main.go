package main

import (
	"database/sql"
	"log"
	"net/http"
	"os"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/example/inventory/internal/store"
)

func main() {
	db, err := sql.Open("pgx", os.Getenv("DATABASE_URL"))
	if err != nil {
		log.Fatal(err)
	}
	h := &handlers{stock: store.New(db), events: newPublisher(os.Getenv("KAFKA_BROKERS"))}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/reservations", h.reserve)
	mux.HandleFunc("GET /api/stock/{sku}", h.stockLevel)
	log.Fatal(http.ListenAndServe(":8080", mux))
}
