package store

import (
	"context"
	"database/sql"
)

type Stock struct{ db *sql.DB }

func New(db *sql.DB) *Stock { return &Stock{db: db} }

func (s *Stock) Level(ctx context.Context, sku string) (int, error) {
	var qty int
	err := s.db.QueryRowContext(ctx, "SELECT qty FROM stock WHERE sku = $1", sku).Scan(&qty)
	return qty, err
}

func (s *Stock) Reserve(ctx context.Context, sku string, qty int) (string, error) {
	if _, err := s.db.ExecContext(ctx, "UPDATE stock SET qty = qty - $1 WHERE sku = $2", qty, sku); err != nil {
		return "", err
	}
	var id string
	err := s.db.QueryRowContext(ctx, "INSERT INTO reservations (sku, qty) VALUES ($1, $2) RETURNING id", sku, qty).Scan(&id)
	return id, err
}
