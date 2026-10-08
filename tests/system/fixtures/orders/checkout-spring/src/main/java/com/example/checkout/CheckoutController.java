package com.example.checkout;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestTemplate;

@RestController
@RequestMapping("/api/checkout")
public class CheckoutController {
    private final RestTemplate restTemplate = new RestTemplate();

    @Value("${inventory.url}")
    private String inventoryUrl;

    @PostMapping
    public Reservation checkout(@RequestBody Cart cart) {
        return restTemplate.postForObject(inventoryUrl + "/api/reservations", cart, Reservation.class);
    }

    @GetMapping("/{sku}/available")
    public Stock available(@PathVariable String sku) {
        return restTemplate.getForObject(inventoryUrl + "/api/stock/" + sku, Stock.class);
    }

    record Cart(String sku, int qty) {}
    record Reservation(String id) {}
    record Stock(String sku, int qty) {}
}
