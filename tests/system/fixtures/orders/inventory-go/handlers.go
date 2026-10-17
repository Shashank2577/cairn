package main

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/example/inventory/internal/store"
	"github.com/segmentio/kafka-go"
)

type handlers struct {
	stock  *store.Stock
	events *kafka.Writer
}

func newPublisher(brokers string) *kafka.Writer {
	return &kafka.Writer{Addr: kafka.TCP(brokers), Topic: "stock.reserved"}
}

func (h *handlers) reserve(w http.ResponseWriter, r *http.Request) {
	var req struct {
		SKU string `json:"sku"`
		Qty int    `json:"qty"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	id, err := h.stock.Reserve(r.Context(), req.SKU, req.Qty)
	if err != nil {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	_ = h.events.WriteMessages(context.Background(), kafka.Message{Value: []byte(id)})
	_ = json.NewEncoder(w).Encode(map[string]string{"id": id})
}

func (h *handlers) stockLevel(w http.ResponseWriter, r *http.Request) {
	qty, _ := h.stock.Level(r.Context(), r.PathValue("sku"))
	_ = json.NewEncoder(w).Encode(map[string]int{"qty": qty})
}
