package main

import (
	"context"
	"log"
	"os"
	"time"

	"github.com/segmentio/kafka-go"
)

func main() {
	reader := kafka.NewReader(kafka.ReaderConfig{
		Brokers: []string{os.Getenv("KAFKA_URL")},
		Topic:   "order.placed",
		GroupID: "notify",
	})
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	for {
		msg, err := reader.ReadMessage(ctx)
		if err != nil {
			log.Fatal(err)
		}
		log.Printf("notified: %s", msg.Value)
	}
}