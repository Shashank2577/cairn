package main

import (
	"net/http"
	"testing"
)

func TestStock(t *testing.T) {
	resp, err := http.Get("http://localhost:8080/api/stock/abc")
	if err != nil {
		t.Skip("service not running")
	}
	defer resp.Body.Close()
}
